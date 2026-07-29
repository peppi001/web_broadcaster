from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from audio_engine.native_engine import NativeEngine


class _FakeProcess:
    pid = 43210

    def poll(self):
        return None


class _FakeSocket:
    def close(self):
        return None


class V6016ErrorVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

    def test_normal_python_policy_keeps_errors_and_suppresses_warnings(self) -> None:
        self.assertIn(
            "_LOG_LEVEL = logging.WARNING if _RUNTIME_LOGGING_ENABLED else logging.ERROR",
            self.app_source,
        )
        self.assertIn("_WERKZEUG_LOG_LEVEL = logging.ERROR", self.app_source)
        self.assertIn("logging.disable(logging.NOTSET)", self.app_source)
        self.assertNotIn("logging.CRITICAL + 1", self.app_source)

    def _assert_daemon_streams(self, *, daemon_log_path: str, debug_mode: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = root / "native_engine" / "bin" / "web_broadcaster_engine"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"#!/bin/sh\n")
            binary.chmod(0o755)
            socket_path = root / "engine.sock"
            engine = NativeEngine(
                socket_path=str(socket_path),
                daemon_binary_path=str(binary),
                daemon_log_path=daemon_log_path,
                daemon_start_timeout_sec=0.2,
            )
            engine._probe_socket = lambda: False  # type: ignore[method-assign]
            engine._open_socket = lambda: _FakeSocket()  # type: ignore[method-assign]
            try:
                with mock.patch("audio_engine.native_engine.subprocess.Popen", return_value=_FakeProcess()) as popen:
                    engine._ensure_managed_daemon()
                kwargs = popen.call_args.kwargs
                if debug_mode:
                    self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
                    self.assertIsNot(kwargs["stdout"], subprocess.DEVNULL)
                    self.assertTrue(Path(daemon_log_path).is_file())
                else:
                    self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
                    self.assertIsNone(kwargs["stderr"])
            finally:
                engine._daemon_process = None
                engine.close()

    def test_normal_daemon_hides_stdout_but_inherits_error_console(self) -> None:
        self._assert_daemon_streams(daemon_log_path="", debug_mode=False)

    def test_debug_daemon_preserves_existing_combined_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self._assert_daemon_streams(
                daemon_log_path=str(Path(temporary) / "native_engine.log"),
                debug_mode=True,
            )


if __name__ == "__main__":
    unittest.main()
