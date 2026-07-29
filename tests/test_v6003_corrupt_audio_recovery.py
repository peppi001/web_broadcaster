from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine
from audio_engine.events import EngineEvent


class V6003CorruptAudioRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = shutil.which("ffmpeg")
        if cls.ffmpeg is None:
            raise unittest.SkipTest("ffmpeg is required to build the damaged MP3 fixture")

    @staticmethod
    def _damage_middle_frames(source: Path, destination: Path) -> None:
        data = bytearray(source.read_bytes())
        start = len(data) // 2
        end = min(len(data), start + 700)
        data[start:end] = b"\x00" * (end - start)
        destination.write_bytes(data)

    def test_damaged_mp3_frame_is_skipped_and_playback_reaches_eof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            good = root / "good.mp3"
            damaged = root / "damaged.mp3"
            socket_path = root / "engine.sock"
            subprocess.run(
                [
                    self.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=880:duration=6",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    "-y",
                    str(good),
                ],
                check=True,
            )
            self._damage_middle_frames(good, damaged)

            environment = dict(os.environ)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PROBE"] = "1"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "0"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_RING_MS"] = "4000"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PREBUFFER_MS"] = "250"
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
                    request_timeout_sec=3.0,
                    reconnect_delay_sec=0.05,
                )
                terminal = threading.Event()
                analysis_ready = threading.Event()
                observed: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    observed.append(event)
                    if event.event == "native_audio_analysis_ready":
                        analysis_ready.set()
                    if event.event in {
                        "native_audio_probe_eof",
                        "native_audio_probe_early_eof",
                        "native_audio_probe_error",
                    }:
                        terminal.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.start(station_key="corrupt-recovery-test")
                    uri = (
                        'annotate:queue_id="60031",track_id="60131",'
                        'station_key="corrupt-recovery-test",'
                        'wb_ab_slot_token="corrupt-frame-token",'
                        'wb_play_start="0",wb_crossfade_trigger="6",'
                        'wb_effective_end="6",wb_source_end="6",wb_orig_total="6",'
                        'wb_native_analyze="1",artist="Recovery",title="Damaged MP3":'
                        f"{damaged}"
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(
                        native.get_state().get("native_audio_deck_a_prebuffer_ready"),
                        native.get_state(),
                    )
                    self.assertTrue(native.select_deck("A").get("accepted"))
                    self.assertTrue(terminal.wait(8.0), "damaged MP3 did not reach a terminal state")
                    self.assertTrue(analysis_ready.wait(2.0), "damaged MP3 analysis did not recover")

                    errors = [event for event in observed if event.event == "native_audio_probe_error"]
                    eof_events = [event for event in observed if event.event == "native_audio_probe_eof"]
                    analysis_failures = [
                        event for event in observed if event.event == "native_audio_analysis_failed"
                    ]
                    self.assertEqual(errors, [])
                    self.assertEqual(analysis_failures, [])
                    self.assertEqual(len(eof_events), 1, [event.event for event in observed])
                    self.assertGreaterEqual(
                        int(eof_events[0].payload.get("corrupt_input_skipped_count") or 0),
                        1,
                    )
                    state = native.get_state()
                    self.assertEqual(state.get("native_audio_probe_status"), "eof", state)
                    self.assertEqual(state.get("native_audio_probe_error"), "", state)
                finally:
                    unsubscribe()
                    native.stop()
            finally:
                if native is not None:
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
