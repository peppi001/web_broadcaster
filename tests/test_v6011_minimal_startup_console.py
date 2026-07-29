from __future__ import annotations

import unittest
from pathlib import Path


class V6011MinimalStartupConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")

    def test_normal_audio_engine_selection_details_do_not_print(self) -> None:
        self.assertIn("log_callback=lambda _message: None", self.source)
        self.assertNotIn("log_callback=lambda message: print(message, flush=True)", self.source)

    def test_internal_runtime_success_line_is_removed_but_failure_remains(self) -> None:
        self.assertNotIn("Internal audio runtime is ready", self.source)
        self.assertIn("Web Broadcaster cannot start its internal audio runtime", self.source)
        self.assertIn("raise SystemExit(2) from exc", self.source)

    def test_flask_and_werkzeug_startup_banners_are_suppressed(self) -> None:
        self.assertIn("_flask_cli.show_server_banner = lambda *args, **kwargs: None", self.source)
        self.assertIn('logging.getLogger("werkzeug").setLevel(_WERKZEUG_LOG_LEVEL)', self.source)

    def test_only_two_user_facing_startup_messages_remain(self) -> None:
        first = 'print(f"Web Broadcaster is starting on port {APP_HTTP_PORT}.", flush=True)'
        blank = "print(flush=True)"
        second = 'print(f"Open http://localhost:{APP_HTTP_PORT} in your browser.", flush=True)'
        self.assertEqual(self.source.count(first), 1)
        self.assertEqual(self.source.count(second), 1)
        self.assertLess(self.source.index(first), self.source.index(blank, self.source.index(first)))
        self.assertLess(self.source.index(blank, self.source.index(first)), self.source.index(second))

    def test_versions_are_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6024"', self.source)
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6024"', self.header)
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6011 - 2026-07-22", history)


if __name__ == "__main__":
    unittest.main()
