from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audio_engine.factory import create_audio_engine


class V6016LoggingGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.factory_source = (cls.root / "audio_engine" / "factory.py").read_text(encoding="utf-8")

    def test_default_disables_existing_disk_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protocol_path = Path(tmp) / "audio_engine_protocol.jsonl"
            engine = create_audio_engine(
                environ={
                    "WEB_BROADCASTER_NATIVE_AUTOSTART": "0",
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(Path(tmp) / "engine.sock"),
                },
                protocol_log_path=str(protocol_path),
                app_version="6019",
                log_callback=lambda _message: None,
            )
            try:
                self.assertFalse(engine._protocol_logger.enabled)
                self.assertEqual(engine.protocol_log_path, "")
                self.assertEqual(engine.daemon_log_path, "")
                engine.publish_event("logging_gate_default")
                self.assertFalse(protocol_path.exists())
            finally:
                engine.close()

    def test_debug_one_restores_only_existing_log_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protocol_path = Path(tmp) / "audio_engine_protocol.jsonl"
            daemon_path = Path(tmp) / "native_engine.log"
            engine = create_audio_engine(
                environ={
                    "DEBUG": "1",
                    "WEB_BROADCASTER_NATIVE_AUTOSTART": "0",
                    "WEB_BROADCASTER_ENGINE_SOCKET": str(Path(tmp) / "engine.sock"),
                    "WEB_BROADCASTER_NATIVE_DAEMON_LOG": str(daemon_path),
                },
                protocol_log_path=str(protocol_path),
                app_version="6019",
                log_callback=lambda _message: None,
            )
            try:
                self.assertTrue(engine._protocol_logger.enabled)
                self.assertFalse(engine._protocol_logger.verbose)
                self.assertEqual(engine.protocol_log_path, str(protocol_path))
                self.assertEqual(engine.daemon_log_path, str(daemon_path.resolve()))
                engine.publish_event("logging_gate_enabled")
                self.assertTrue(protocol_path.is_file())
                self.assertGreater(protocol_path.stat().st_size, 0)
            finally:
                engine.close()

    def test_debug_does_not_enable_other_debug_modes(self) -> None:
        self.assertIn("debug=False", self.app_source)
        self.assertNotIn("debug=_RUNTIME_LOGGING_ENABLED", self.app_source)
        self.assertIn(
            '"WEB_BROADCASTER_ENGINE_PROTOCOL_VERBOSE",\n        False,',
            self.factory_source,
        )
        self.assertIn(
            "_LOG_LEVEL = logging.WARNING if _RUNTIME_LOGGING_ENABLED else logging.ERROR",
            self.app_source,
        )
        self.assertNotIn("_LOG_LEVEL = logging.DEBUG", self.app_source)
        self.assertIn(
            "logging.disable(logging.NOTSET)",
            self.app_source,
        )
        self.assertIn("_WERKZEUG_LOG_LEVEL = logging.ERROR", self.app_source)
        self.assertNotIn("logging.CRITICAL + 1", self.app_source)

    def test_switch_controls_only_preexisting_log_sinks(self) -> None:
        self.assertIn('runtime_logging_enabled = _env_bool(environment, "DEBUG", False)', self.factory_source)
        self.assertIn("if runtime_logging_enabled\n        else None", self.factory_source)
        self.assertIn(') if runtime_logging_enabled else ""', self.factory_source)
        self.assertNotIn('WEB_BROADCASTER_ENGINE_PROTOCOL_VERBOSE",\n        runtime_logging_enabled', self.factory_source)


if __name__ == "__main__":
    unittest.main()
