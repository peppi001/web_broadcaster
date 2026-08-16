from __future__ import annotations

import math
import os
import re
import struct
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path

from audio_engine import NativeEngine
from audio_engine.protocol import JsonlProtocolLogger, ProtocolSessionContext


class NativeDaemonAuthoritativeTests(unittest.TestCase):
    @staticmethod
    def _write_tone(path: Path, frequency: float) -> None:
        sample_rate = 44100
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            for index in range(sample_rate * 3):
                value = int(7000 * math.sin(2.0 * math.pi * frequency * index / sample_rate))
                handle.writeframesraw(struct.pack("<hh", value, value))

    def test_native_commands_own_load_select_and_transition(self) -> None:
        root = Path(__file__).resolve().parents[1]
        binary = root / "native_engine" / "bin" / "web_broadcaster_engine"
        header = (root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        match = re.search(r'#define WB_NATIVE_DAEMON_VERSION "([^"]+)"', header)
        self.assertIsNotNone(match)
        expected_native_version = match.group(1)
        self.assertTrue(binary.is_file())
        self.assertTrue(os.access(binary, os.X_OK))

        with tempfile.TemporaryDirectory() as temporary:
            temp = Path(temporary)
            track_a = temp / "a.wav"
            track_b = temp / "b.wav"
            self._write_tone(track_a, 440.0)
            self._write_tone(track_b, 880.0)
            socket_path = temp / "engine.sock"
            process = subprocess.Popen(
                [str(binary), str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 5.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists())
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=5.0,
                    protocol_logger=JsonlProtocolLogger(
                        None,
                        engine_name="native",
                        session_context=ProtocolSessionContext(
                            session_id="native-authoritative-test",
                            app_version="5102",
                            native_daemon_version="not_connected",
                        ),
                    ),
                )
                last_ready_error: Exception | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"native daemon exited during startup with code {process.returncode}")
                    try:
                        native.ping()
                        last_ready_error = None
                        break
                    except Exception as exc:  # startup readiness only
                        last_ready_error = exc
                        time.sleep(0.02)
                if last_ready_error is not None:
                    self.fail(f"native daemon did not become protocol-ready: {last_ready_error}")
                station = "db-Test.db"
                native.start(station_key=station)

                def uri(path: Path, queue_id: int) -> str:
                    return (
                        f'annotate:queue_id="{queue_id}",track_id="{queue_id}",'
                        f'station_key="{station}",wb_ab_slot_token="slot-{queue_id}",'
                        f'wb_play_start="0",wb_crossfade_trigger="2.5",'
                        f'wb_effective_end="3",fade_out="0.5":{path}'
                    )

                self.assertTrue(native.load_deck("A", uri(track_a, 1), station_key=station))
                self.assertTrue(native.load_deck("B", uri(track_b, 2), station_key=station))
                selected = native.select_deck("A", station_key=station)
                self.assertTrue(selected["accepted"])
                time.sleep(0.4)
                first = native.get_state(station_key=station)
                self.assertEqual(first["active_deck"], "A")
                self.assertEqual(first["queue_id"], 1)
                self.assertGreater(first["position_ms"], 0)
                self.assertFalse(first["control_only"])

                transition = native.transition_to("B", 0.5, station_key=station)
                self.assertTrue(transition["accepted"])
                time.sleep(0.6)
                finished = native.select_deck("B", station_key=station)
                self.assertTrue(finished["accepted"])
                time.sleep(0.2)
                second = native.get_state(station_key=station)
                self.assertEqual(second["active_deck"], "B")
                self.assertEqual(second["queue_id"], 2)
                self.assertFalse(second["transitioning"])
                self.assertEqual(second["app_version"], "5102")
                self.assertEqual(second["native_daemon_version"], expected_native_version)
                native.stop(station_key=station)
            finally:
                if native is not None:
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
