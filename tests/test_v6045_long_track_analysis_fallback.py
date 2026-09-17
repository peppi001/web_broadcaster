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


class V6045LongTrackAnalysisFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.analysis_source = (cls.root / "native_engine" / "src" / "audio_analysis.c").read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = cls.root / "bin" / "ffmpeg"

    def test_long_track_threshold_matches_existing_runtime_grace(self) -> None:
        self.assertIn("#define WB_ANALYSIS_LONG_TRACK_FALLBACK_MS 600000LL", self.analysis_source)
        self.assertIn("active_duration >= 600.0", self.app_source)

    def test_long_tracks_bypass_full_file_pcm_analysis(self) -> None:
        branch = self.analysis_source.index("else if (known_long_track(&track))")
        normal_decode = self.analysis_source.index("decode_result = decode_metrics(", branch)
        partial_decode = self.analysis_source.index("decode_result = decode_metrics_range(", branch)
        self.assertLess(partial_decode, normal_decode)
        self.assertIn("WB_ANALYSIS_LONG_TRACK_HEAD_SAMPLE_MS", self.analysis_source[branch:normal_decode])
        self.assertIn("WB_ANALYSIS_LONG_TRACK_TAIL_SAMPLE_MS", self.analysis_source[branch:normal_decode])

    def test_long_track_fallback_remains_available_if_partial_analysis_fails(self) -> None:
        for marker in (
            'copy_text(track->analysis_source, sizeof(track->analysis_source), "native_long_track_fallback")',
            "track->analysis_ready = true;",
            "track->analysis_failed = false;",
            "transition_ms = track->effective_end_ms - fallback_ms;",
            "track->cue_out_ms = transition_ms;",
            "else if (decode_result < 0)",
            "apply_long_track_fallback(&result);",
        ):
            self.assertIn(marker, self.analysis_source)

    def test_normal_tracks_still_use_native_pcm_analysis(self) -> None:
        self.assertIn("decode_metrics(", self.analysis_source)
        self.assertIn('copy_text(track->analysis_source, sizeof(track->analysis_source), "native_pcm_runtime")', self.analysis_source)

    def test_staged_native_runtime_prebuffers_sparse_601_second_track_with_partial_analysis(self) -> None:
        if not self.binary.is_file() or not os.access(self.binary, os.X_OK):
            self.skipTest("staged native daemon is unavailable")
        if not self.ffmpeg.is_file() or not os.access(self.ffmpeg, os.X_OK):
            self.skipTest("staged bundled ffmpeg is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "long-sparse.wav"
            sample_rate = 44100
            duration_seconds = 601
            data_size = sample_rate * 2 * 2 * duration_seconds
            header = (
                b"RIFF"
                + struct.pack("<I", 36 + data_size)
                + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, 1, 2, sample_rate, sample_rate * 4, 4, 16)
                + b"data"
                + struct.pack("<I", data_size)
            )
            with source.open("wb") as handle:
                handle.write(header)
                handle.seek(44 + data_size - 1)
                handle.write(b"\0")

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
                        'annotate:queue_id="8045",track_id="8145",station_key="analysis-test",'
                        'wb_ab_slot_token="long-track-6045",wb_native_analyze="1",wb_manual_timing="0",'
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
                    self.assertTrue(ready.wait(2.0), "long-track partial analysis did not become ready")
                    self.assertLess(time.monotonic() - started, 2.0)
                    self.assertEqual(len(observed), 1)
                    event = observed[0]
                    self.assertEqual(event.event, "native_audio_analysis_ready")
                    self.assertEqual(event.payload.get("analysis_source"), "native_pcm_long_track_partial")
                    self.assertEqual(event.payload.get("effective_end_ms"), 601000)
                    self.assertEqual(event.payload.get("transition_at_ms"), 598000)

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
