from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from audio_engine import NativeEngine
from audio_engine.events import EngineEvent


class NativePcmAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = cls.root / "bin" / "ffmpeg"
        if not cls.binary.exists() or not os.access(cls.binary, os.X_OK):
            raise unittest.SkipTest("native daemon binary is unavailable")
        if not cls.ffmpeg.exists() or not os.access(cls.ffmpeg, os.X_OK):
            raise unittest.SkipTest("bundled ffmpeg is unavailable")

    @contextmanager
    def _running_native(self, *, realtime: bool = False) -> Iterator[tuple[NativeEngine, Path]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "engine.sock"
            environment = dict(os.environ)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PROBE"] = "1"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "1" if realtime else "0"
            environment["WEB_BROADCASTER_FFMPEG"] = str(self.ffmpeg)
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 4.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists(), "native daemon socket was not created")
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=12.0,
                    reconnect_delay_sec=0.05,
                )
                native.start(station_key="analysis-test")
                yield native, root
            finally:
                if native is not None:
                    try:
                        native.stop(station_key="analysis-test")
                    except Exception:
                        pass
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=4.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=4.0)
                if process.stdout is not None:
                    process.stdout.close()

    @staticmethod
    def _write_corona_shape(path: Path) -> None:
        sample_rate = 44100
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            chunks: list[bytes] = []
            for index in range(sample_rate):
                value = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
                chunks.append(struct.pack("<hh", value, value))
            chunks.extend([struct.pack("<hh", 0, 0)] * sample_rate)
            for index in range(sample_rate // 50):  # isolated 20 ms EOF artifact
                value = int(10000 * math.sin(2.0 * math.pi * 1800.0 * index / sample_rate))
                chunks.append(struct.pack("<hh", value, value))
            output.writeframes(b"".join(chunks))

    @staticmethod
    def _write_tone(path: Path, duration_seconds: float) -> None:
        sample_rate = 44100
        total_frames = int(round(sample_rate * duration_seconds))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            block = bytearray()
            for index in range(total_frames):
                value = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / sample_rate))
                block.extend(struct.pack("<hh", value, value))
                if len(block) >= 65536:
                    output.writeframesraw(block)
                    block.clear()
            if block:
                output.writeframesraw(block)

    @staticmethod
    def _analysis_uri(
        path: Path,
        *,
        queue_id: int,
        token: str,
        duration_seconds: float = 2.020,
        no_crossfade_max_seconds: float = 65.0,
        crossfade_fallback_seconds: float = 3.0,
        fade_out_seconds: float = 5.0,
    ) -> str:
        return (
            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
            f'station_key="analysis-test",wb_ab_slot_token="{token}",'
            f'wb_native_analyze="1",wb_manual_timing="0",wb_orig_total="{duration_seconds:.3f}",'
            f'wb_audio_start="0",wb_audio_end="{duration_seconds:.3f}",wb_play_start="0",'
            f'wb_crossfade_trigger="0",wb_effective_end="{duration_seconds:.3f}",cue_in="0",cue_out="0",'
            f'fade_in="0",fade_out="{fade_out_seconds:.3f}",wb_analysis_window_ms="10",'
            'wb_analysis_sustain_ms="30",wb_analysis_artifact_max_ms="300",'
            'wb_analysis_artifact_silence_ms="250",wb_gap_start_dbfs="-20",'
            'wb_gap_end_dbfs="-24",wb_crossfade_trigger_relative_db="-7",'
            f'wb_crossfade_fallback="{crossfade_fallback_seconds:.3f}",wb_crossfade_min="0.1",wb_crossfade_max="6",'
            f'wb_no_crossfade_max_duration="{no_crossfade_max_seconds:.3f}":{path}'
        )

    def test_runtime_analysis_ignores_isolated_noise_after_long_trailing_silence(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "corona-shape.wav"
            self._write_corona_shape(source)
            ready = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event in {"native_audio_analysis_ready", "native_audio_analysis_failed"}:
                    ready.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("A", self._analysis_uri(source, queue_id=7101, token="analysis-a"), clear_slot=True))
                self.assertTrue(ready.wait(8.0), "native analysis did not finish")
                result = next(event for event in observed if event.event == "native_audio_analysis_ready")
                self.assertFalse(result.payload["analysis_failed"])
                self.assertEqual(result.payload["analysis_source"], "native_pcm_runtime")
                self.assertAlmostEqual(result.payload["effective_end_ms"], 1000, delta=30)
                self.assertAlmostEqual(result.payload["source_end_ms"], 2020, delta=30)
                self.assertGreaterEqual(result.payload["ignored_trailing_artifact_ms"], 10)
                self.assertGreaterEqual(result.payload["trailing_silence_ms"], 950)
                self.assertTrue(result.payload["short_no_crossfade"])

                state = native.get_state(station_key="analysis-test")
                self.assertTrue(state["native_pcm_analysis"])
                self.assertTrue(state["native_analysis_ready"])
                self.assertEqual(state["native_analysis_source"], "native_pcm_runtime")
                self.assertAlmostEqual(state["native_analysis_effective_end_ms"], 1000, delta=30)
                self.assertGreaterEqual(state["native_analysis_ignored_artifact_ms"], 10)
            finally:
                unsubscribe()

    def test_native_timing_requests_next_track_without_python_cue_clock(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "short.wav"
            self._write_corona_shape(source)
            requested = threading.Event()
            payloads: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_need_next_track":
                    payloads.append(dict(event.payload or {}))
                    requested.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = self._analysis_uri(source, queue_id=7201, token="timing-a")
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                self.assertTrue(native.select_deck("A"))
                self.assertTrue(requested.wait(5.0), "native timing worker did not request the next track")
                self.assertEqual(payloads[-1]["source"], "native_timing_worker")
                self.assertEqual(payloads[-1]["active_deck"], "A")
                self.assertEqual(payloads[-1]["target_deck"], "B")
                self.assertLessEqual(payloads[-1]["remaining_ms"], 15000)
                state = native.get_state(station_key="analysis-test")
                self.assertTrue(state["native_timing_owner"])
                self.assertGreaterEqual(state["native_next_track_request_count"], 1)
            finally:
                unsubscribe()

    def test_native_timing_starts_normal_transition_from_native_analysis(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            outgoing = root / "outgoing.wav"
            incoming = root / "incoming.wav"
            self._write_tone(outgoing, 4.0)
            self._write_tone(incoming, 4.0)
            started = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if (
                    event.event == "track_started"
                    and event.queue_id == 7302
                    and event.payload.get("source") == "native_timing_transition"
                ):
                    started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                outgoing_uri = self._analysis_uri(
                    outgoing, queue_id=7301, token="transition-a",
                    duration_seconds=4.0, no_crossfade_max_seconds=1.0,
                    crossfade_fallback_seconds=1.0, fade_out_seconds=1.0,
                )
                incoming_uri = self._analysis_uri(
                    incoming, queue_id=7302, token="transition-b",
                    duration_seconds=4.0, no_crossfade_max_seconds=1.0,
                    crossfade_fallback_seconds=1.0, fade_out_seconds=1.0,
                )
                self.assertTrue(native.load_deck("A", outgoing_uri, clear_slot=True))
                self.assertTrue(native.load_deck("B", incoming_uri, clear_slot=True))
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    state = native.get_state(station_key="analysis-test")
                    if (
                        state.get("native_audio_deck_a_prebuffer_ready")
                        and state.get("native_audio_deck_b_prebuffer_ready")
                    ):
                        break
                    time.sleep(0.01)
                state = native.get_state(station_key="analysis-test")
                self.assertTrue(state.get("native_audio_deck_a_prebuffer_ready"), state)
                self.assertTrue(state.get("native_audio_deck_b_prebuffer_ready"), state)
                self.assertTrue(native.select_deck("A"))
                self.assertTrue(started.wait(7.0), "native analysis did not start the normal transition")
                transition = next(
                    event for event in observed
                    if event.event == "track_started" and event.queue_id == 7302
                )
                self.assertEqual(transition.payload["source"], "native_timing_transition")
                state = native.get_state(station_key="analysis-test")
                self.assertGreaterEqual(state["native_transition_start_count"], 1)
            finally:
                unsubscribe()

    def test_native_need_next_retries_until_target_is_ready(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "retry.wav"
            self._write_tone(source, 4.0)
            requests: list[dict] = []
            repeated = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_need_next_track" and event.queue_id == 7401:
                    requests.append(dict(event.payload or {}))
                    if len(requests) >= 3:
                        repeated.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = self._analysis_uri(source, queue_id=7401, token="retry-a", duration_seconds=4.0)
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                self.assertTrue(native.select_deck("A"))
                self.assertTrue(repeated.wait(3.0), f"need-next was not retried: {requests}")
                attempts = [int(item.get("request_attempt") or 0) for item in requests]
                self.assertEqual(attempts, sorted(attempts))
                self.assertGreater(len(set(attempts)), 1)
            finally:
                unsubscribe()



if __name__ == "__main__":
    unittest.main()
