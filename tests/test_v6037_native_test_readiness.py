from __future__ import annotations

import unittest
from pathlib import Path


class V6037NativeTestReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]

    def test_command_harnesses_require_protocol_readiness(self) -> None:
        for relative in (
            "tests/test_native_audio_probe.py",
            "tests/test_native_pcm_analysis.py",
            "tests/test_native_diagnostics.py",
            "tests/test_v6003_corrupt_audio_recovery.py",
            "tests/test_native_daemon_sync.py",
            "tests/test_native_icecast_output.py",
            "tests/test_native_load_lifecycle.py",
        ):
            text = (self.root / relative).read_text(encoding="utf-8")
            self.assertIn("native.ping()", text, relative)

    def test_high_volume_harnesses_do_not_leave_unread_daemon_pipes(self) -> None:
        for relative in (
            "tests/test_native_audio_probe.py",
            "tests/test_native_pcm_analysis.py",
            "tests/test_native_diagnostics.py",
            "tests/test_v6003_corrupt_audio_recovery.py",
            "tests/test_native_icecast_output.py",
        ):
            text = (self.root / relative).read_text(encoding="utf-8")
            self.assertNotIn("stdout=subprocess.PIPE", text, relative)

    def test_raw_protocol_harnesses_wait_for_accepting_socket(self) -> None:
        for relative in (
            "tests/test_bundled_ffmpeg_runtime.py",
            "tests/test_v6000_embedded_libav.py",
        ):
            text = (self.root / relative).read_text(encoding="utf-8")
            self.assertIn("def _connect_ready_socket", text, relative)
            self.assertIn("client.connect(str(socket_path))", text, relative)
            self.assertIn("native daemon did not accept connections", text, relative)


if __name__ == "__main__":
    unittest.main()
