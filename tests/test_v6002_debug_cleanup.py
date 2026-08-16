from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V6002DebugCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.browser = (cls.root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
        cls.template = (cls.root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        cls.css = (cls.root / "html" / "static" / "broadcaster.css").read_text(encoding="utf-8")
        cls.runtime = (cls.root / "audio_engine" / "runtime.py").read_text(encoding="utf-8")
        cls.factory = (cls.root / "audio_engine" / "factory.py").read_text(encoding="utf-8")
        cls.module_sources = "\n".join(
            (cls.root / relative).read_text(encoding="utf-8")
            for relative in (
                "audio_engine/lifecycle.py",
                "autodj/service.py",
                "player/orchestration.py",
                "station/service.py",
                "storage/playback_repository.py",
            )
        )

    def test_legacy_debug_paths_and_files_are_absent(self) -> None:
        active_source = "\n".join((self.app, self.browser, self.template, self.css, self.module_sources))
        for removed in (
            "WEB_BROADCASTER_DEBUG",
            "DEBUG_ENABLED",
            "DEBUG_SEARCH",
            "web_broadcaster.log",
            "startstop-",
            "/api/debug/client-event",
            "WB_DEBUG_ENABLED",
            "debugTrace(",
        ):
            self.assertNotIn(removed, active_source)

    def test_python_uses_one_production_logging_and_sqlite_model(self) -> None:
        self.assertIn("_LOG_LEVEL = logging.WARNING if _RUNTIME_LOGGING_ENABLED", self.app)
        self.assertNotIn("request_handler=_WebBroadcasterRequestHandler", self.app)
        self.assertNotIn("app.run(", self.app)
        self.assertIn("from cheroot.wsgi import Server as CherootServer", self.app)
        self.assertIn("server = CherootServer((host, port), app, numthreads=16)", self.app)
        self.assertNotIn("_PERSISTENT_SQLITE_REUSE_ENABLED", self.app)
        self.assertIn("_PERSISTENT_SQLITE_CONNECTIONS", self.app)
        self.assertIn("audio_engine_protocol.jsonl", self.app)
        self.assertIn("native_engine.log", self.factory)

    def test_removed_debug_helpers_and_callbacks_do_not_return(self) -> None:
        tree = ast.parse(self.app)
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        for removed in (
            "dbg",
            "_dbg_ctx",
            "_debug_log",
            "_debug_client_trace",
            "_history_trace",
            "_script_debug_log",
            "_sched_log",
            "append_audio_engine_action_log",
            "api_debug_client_event",
        ):
            self.assertNotIn(removed, functions)
        for callback_name in (
            "debug_trace",
            "debug_log",
            "script_debug_log",
            "action_log",
        ):
            self.assertNotIn(callback_name, self.module_sources)


if __name__ == "__main__":
    unittest.main()
