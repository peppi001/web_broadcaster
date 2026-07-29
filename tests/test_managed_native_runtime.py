from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from audio_engine import create_audio_engine


class ManagedNativeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        if not cls.binary.is_file():
            raise unittest.SkipTest("bundled native daemon binary is unavailable")

    @staticmethod
    def _wait_for(predicate, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.025)
        return bool(predicate())

    def test_factory_starts_and_stops_bundled_daemon_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "managed.sock"
            daemon_log = root / "managed.log"
            engine = create_audio_engine(
                environ={
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(socket_path),
                    "WEB_BROADCASTER_NATIVE_BINARY": str(self.binary),
                    "DEBUG": "1",
                    "WEB_BROADCASTER_NATIVE_DAEMON_LOG": str(daemon_log),
                    "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
                },
                app_version="5102",
            )
            try:
                reply = engine.ensure_ready()
                self.assertTrue(reply.get("ok"), reply)
                self.assertEqual(
                    str((reply.get("result") or {}).get("native_daemon_version") or ""),
                    "6024",
                )
                state = engine.diagnostic_state()
                daemon_pid = int(state.get("daemon_pid") or 0)
                self.assertGreater(daemon_pid, 0, state)
                self.assertTrue(socket_path.exists())
                self.assertTrue(state.get("managed_daemon"), state)
                self.assertEqual(Path(state["daemon_binary_path"]), self.binary.resolve())
                self.assertTrue(Path(f"/proc/{daemon_pid}").exists())
            finally:
                engine.close()
            self.assertTrue(
                self._wait_for(lambda: not Path(f"/proc/{daemon_pid}").exists()),
                f"managed daemon {daemon_pid} survived client shutdown",
            )
            self.assertTrue(self._wait_for(lambda: not socket_path.exists()))
            self.assertIn("native multi-station daemon v6024", daemon_log.read_text(errors="replace"))


    def test_native_pause_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "pause.sock"
            engine = create_audio_engine(
                environ={
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(socket_path),
                    "WEB_BROADCASTER_NATIVE_BINARY": str(self.binary),
                    "DEBUG": "1",
                    "WEB_BROADCASTER_NATIVE_DAEMON_LOG": str(root / "pause.log"),
                    "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
                },
                app_version="5102",
            )
            try:
                engine.ensure_ready()
                station = "pause-test.db"
                other_station = "pause-other.db"
                engine.create_station(station)
                engine.create_station(other_station)
                engine.start(station_key=station)
                engine.start(station_key=other_station)
                paused = engine.set_paused(True, station_key=station)
                self.assertTrue(paused.get("paused"), paused)
                self.assertTrue(engine.get_state(station_key=station).get("paused"))
                self.assertFalse(engine.get_state(station_key=other_station).get("paused"))
                time.sleep(0.05)
                resumed = engine.set_paused(False, station_key=station)
                self.assertFalse(resumed.get("paused"), resumed)
                self.assertGreaterEqual(int(resumed.get("pause_duration_ms") or 0), 25)
                self.assertFalse(engine.get_state(station_key=station).get("paused"))
                engine.stop(station_key=station)
                engine.stop(station_key=other_station)
            finally:
                engine.close()

    def test_managed_daemon_restarts_after_unexpected_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "restart.sock"
            daemon_log = root / "restart.log"
            engine = create_audio_engine(
                environ={
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(socket_path),
                    "WEB_BROADCASTER_NATIVE_BINARY": str(self.binary),
                    "DEBUG": "1",
                    "WEB_BROADCASTER_NATIVE_DAEMON_LOG": str(daemon_log),
                    "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
                    "WEB_BROADCASTER_NATIVE_RECONNECT_DELAY": "0.05",
                },
                app_version="5102",
            )
            try:
                engine.ensure_ready()
                first_pid = int(engine.diagnostic_state().get("daemon_pid") or 0)
                self.assertGreater(first_pid, 0)
                os.kill(first_pid, signal.SIGKILL)
                self.assertTrue(self._wait_for(lambda: not engine.connected))
                time.sleep(0.08)
                reply = engine.ensure_ready()
                second_pid = int(engine.diagnostic_state().get("daemon_pid") or 0)
                self.assertTrue(reply.get("ok"), reply)
                self.assertGreater(second_pid, 0)
                self.assertNotEqual(second_pid, first_pid)
            finally:
                engine.close()

    def test_managed_daemon_exits_when_app_process_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "parent-death.sock"
            daemon_log = root / "parent-death.log"
            script = root / "launch.py"
            script.write_text(
                """
import os
import time
from audio_engine import create_audio_engine
engine = create_audio_engine(
    environ={
        "WEB_BROADCASTER_ENGINE_SOCKET": os.environ["TEST_SOCKET"],
        "WEB_BROADCASTER_NATIVE_BINARY": os.environ["TEST_BINARY"],
        "DEBUG": "1",
        "WEB_BROADCASTER_NATIVE_DAEMON_LOG": os.environ["TEST_LOG"],
        "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
    },
    app_version="5102",
    log_callback=lambda message: None,
)
engine.ensure_ready()
print(engine.diagnostic_state()["daemon_pid"], flush=True)
time.sleep(60)
""".strip()
                + "\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": str(self.root),
                    "TEST_SOCKET": str(socket_path),
                    "TEST_BINARY": str(self.binary),
                    "TEST_LOG": str(daemon_log),
                }
            )
            parent = subprocess.Popen(
                [sys.executable, str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            try:
                line = parent.stdout.readline().strip() if parent.stdout is not None else ""
                daemon_pid = int(line)
                self.assertTrue(Path(f"/proc/{daemon_pid}").exists())
                os.kill(parent.pid, signal.SIGKILL)
                parent.wait(timeout=3.0)
                self.assertTrue(
                    self._wait_for(lambda: not Path(f"/proc/{daemon_pid}").exists()),
                    f"managed daemon {daemon_pid} survived parent SIGKILL",
                )
                if socket_path.exists():
                    # A process killed while a client-disconnect thread is
                    # unwinding may leave only the filesystem socket node.
                    # It must have no listener, and the next managed startup
                    # must remove it transparently before binding.
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                        with self.assertRaises(OSError):
                            probe.connect(str(socket_path))
                    replacement = create_audio_engine(
                        environ={
                            "WEB_BROADCASTER_ENGINE_SOCKET": str(socket_path),
                            "WEB_BROADCASTER_NATIVE_BINARY": str(self.binary),
                            "DEBUG": "1",
                    "WEB_BROADCASTER_NATIVE_DAEMON_LOG": str(daemon_log),
                            "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
                        },
                        app_version="5102",
                        log_callback=lambda message: None,
                    )
                    try:
                        reply = replacement.ensure_ready()
                        self.assertTrue(reply.get("ok"), reply)
                    finally:
                        replacement.close()
                self.assertTrue(self._wait_for(lambda: not socket_path.exists()))
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=3.0)

    def test_autostart_can_be_disabled_for_external_daemon_setups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "external.sock"
            engine = create_audio_engine(
                environ={
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(socket_path),
                    "WEB_BROADCASTER_NATIVE_AUTOSTART": "0",
                    "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG": "disabled",
                },
                app_version="5102",
            )
            try:
                self.assertFalse(engine.diagnostic_state().get("managed_daemon"))
                with self.assertRaisesRegex(Exception, "cannot connect"):
                    engine.ensure_ready()
                self.assertFalse(socket_path.exists())
            finally:
                engine.close()


if __name__ == "__main__":
    unittest.main()
