from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine
from audio_engine.events import EngineEvent


class V6047PartialLongTrackAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.analysis_source = (cls.root / "native_engine" / "src" / "audio_analysis.c").read_text(encoding="utf-8")
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = cls.root / "bin" / "ffmpeg"

    def test_long_track_sampling_budget_is_bounded_to_three_minutes(self) -> None:
        self.assertIn("#define WB_ANALYSIS_LONG_TRACK_HEAD_SAMPLE_MS 60000LL", self.analysis_source)
        self.assertIn("#define WB_ANALYSIS_LONG_TRACK_TAIL_SAMPLE_MS 120000LL", self.analysis_source)
        self.assertIn("tail_start_ms = track.source_end_ms - tail_duration_ms;", self.analysis_source)
        self.assertIn("decode_metrics_range(", self.analysis_source)
        self.assertIn('"native_pcm_long_track_partial"', self.analysis_source)

    def test_long_track_partial_analysis_keeps_safe_fallback(self) -> None:
        branch = self.analysis_source.index("else if (known_long_track(&track))")
        normal_branch = self.analysis_source.index("} else {", branch)
        section = self.analysis_source[branch:normal_branch]
        self.assertGreaterEqual(section.count("decode_metrics_range("), 2)
        self.assertIn("apply_long_track_partial_analysis(", section)
        self.assertIn("apply_long_track_fallback(&result);", section)

    def test_real_sparse_long_track_recovers_head_and_tail_boundaries(self) -> None:
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self.skipTest("staged native daemon is unavailable")
        if not self.ffmpeg.is_file() or not os.access(self.ffmpeg, os.X_OK):
            self.skipTest("staged bundled ffmpeg is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "long-boundaries.wav"
            sample_rate = 44100
            duration_seconds = 601
            frame_bytes = 4
            data_size = sample_rate * frame_bytes * duration_seconds
            header = (
                b"RIFF"
                + struct.pack("<I", 36 + data_size)
                + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, sample_rate, sample_rate * frame_bytes, frame_bytes, 16)
                + b"data"
                + struct.pack("<I", data_size)
            )
            one_second_tone = struct.pack("<hh", 6000, 6000) * sample_rate
            with source.open("wb") as handle:
                handle.write(header)
                handle.seek(44 + data_size - 1)
                handle.write(b"\0")
                for second in list(range(2, 60)) + list(range(481, 598)):
                    handle.seek(44 + second * sample_rate * frame_bytes)
                    handle.write(one_second_tone)

            socket_path = work / "engine.sock"
            environment = dict(os.environ)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PROBE"] = "1"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "0"
            environment["WEB_BROADCASTER_FFMPEG"] = str(self.ffmpeg)
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 6.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists(), "native daemon socket was not created")
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=12.0,
                    reconnect_delay_sec=0.05,
                )
                while time.monotonic() < deadline:
                    try:
                        native.ping()
                        break
                    except Exception:
                        time.sleep(0.02)
                native.start(station_key="analysis-test")

                ready = threading.Event()
                observed: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event in {"native_audio_analysis_ready", "native_audio_analysis_failed"}:
                        observed.append(event)
                        ready.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    uri = (
                        'annotate:queue_id="8047",track_id="8147",station_key="analysis-test",'
                        'wb_ab_slot_token="long-track-6047",wb_native_analyze="1",wb_manual_timing="0",'
                        'wb_orig_total="601.000",wb_audio_start="0",wb_audio_end="601.000",wb_play_start="0",'
                        'wb_crossfade_trigger="598.000",wb_effective_end="601.000",cue_in="0",cue_out="598.000",'
                        'fade_in="0",fade_out="5.000",wb_analysis_window_ms="10",wb_analysis_sustain_ms="30",'
                        'wb_analysis_artifact_max_ms="300",wb_analysis_artifact_silence_ms="250",'
                        'wb_gap_start_dbfs="-20",wb_gap_end_dbfs="-24",wb_crossfade_trigger_relative_db="-7",'
                        'wb_crossfade_fallback="3.000",wb_crossfade_min="0.1",wb_crossfade_max="6",'
                        f'wb_no_crossfade_max_duration="65.000":{source}'
                    )
                    started = time.monotonic()
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    self.assertTrue(ready.wait(4.0), "long-track partial analysis did not become ready")
                    self.assertLess(time.monotonic() - started, 4.0)
                    self.assertEqual(len(observed), 1)
                    event = observed[0]
                    self.assertEqual(event.event, "native_audio_analysis_ready")
                    self.assertEqual(event.payload.get("analysis_source"), "native_pcm_long_track_partial")
                    self.assertEqual(event.payload.get("audio_start_ms"), 2000)
                    self.assertEqual(event.payload.get("play_start_ms"), 2000)
                    self.assertEqual(event.payload.get("effective_end_ms"), 598000)
                    self.assertEqual(event.payload.get("source_end_ms"), 601000)
                    self.assertEqual(event.payload.get("trailing_silence_ms"), 3000)
                    transition = int(event.payload.get("transition_at_ms") or 0)
                    self.assertGreaterEqual(transition, 592000)
                    self.assertLessEqual(transition, 597900)

                    prebuffer_deadline = time.monotonic() + 2.0
                    state = {}
                    while time.monotonic() < prebuffer_deadline:
                        state = native.get_state(station_key="analysis-test")
                        if state.get("native_audio_deck_a_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(state.get("native_audio_deck_a_prebuffer_ready"), state)
                    self.assertGreater(int(state.get("native_audio_deck_a_ring_buffer_bytes") or 0), 0)
                    self.assertEqual(state.get("native_analysis_source"), "native_pcm_long_track_partial")
                finally:
                    unsubscribe()
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


if __name__ == "__main__":
    unittest.main()
