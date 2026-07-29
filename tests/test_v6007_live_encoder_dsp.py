from __future__ import annotations

import ast
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


class V6007LiveEncoderDspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.app_source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }
        cls.js = (cls.root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
        cls.template = (cls.root / "html" / "broadcaster.html").read_text(encoding="utf-8")

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def _load_function(self, name: str, namespace: dict[str, object]):
        module = ast.Module(body=[self.functions[name]], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace.setdefault("Any", Any)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace[name]

    def test_encoder_restart_warning_modal_is_completely_removed(self) -> None:
        combined = self.js + "\n" + self.template
        for retired in (
            "studio-encoder-saved-backdrop",
            "studioEncoderSavedBackdrop",
            "openStudioEncoderSavedModal",
            "You must stop and start this station",
            "Encoder saved",
        ):
            self.assertNotIn(retired, combined)

    def test_autostart_helper_starts_only_when_station_is_on_air(self) -> None:
        calls: list[tuple[int, str]] = []
        timestamps: list[tuple[int, str]] = []
        namespace: dict[str, object] = {
            "datetime": datetime,
            "get_active_station_key": lambda: "db-Test.db",
            "is_station_on_air": lambda station: station == "db-Test.db",
            "_encoder_action_native": lambda stream_id, action: calls.append((stream_id, action)),
            "set_encoder_started_at": lambda stream_id, value: timestamps.append((stream_id, value)),
        }
        helper = self._load_function("_start_encoder_if_autostart_on_air", namespace)

        result = helper(17, autostart=True, station_key="db-Test.db")
        self.assertTrue(result["station_running"])
        self.assertTrue(result["started_immediately"])
        self.assertEqual(calls, [(17, "start")])
        self.assertEqual(timestamps[0][0], 17)

        calls.clear()
        result = helper(18, autostart=False, station_key="db-Test.db")
        self.assertFalse(result["started_immediately"])
        self.assertEqual(calls, [])

        namespace["is_station_on_air"] = lambda _station: False
        helper = self._load_function("_start_encoder_if_autostart_on_air", namespace)
        result = helper(19, autostart=True, station_key="db-Test.db")
        self.assertFalse(result["station_running"])
        self.assertFalse(result["started_immediately"])
        self.assertEqual(calls, [])

    def test_create_and_configure_routes_apply_on_air_autostart(self) -> None:
        create = self._source("api_encoder_create")
        configure = self._source("api_encoder_configure")
        self.assertIn("_start_encoder_if_autostart_on_air", create)
        self.assertIn("autostart=bool(autostart)", create)
        self.assertIn("station_running = bool(station_key and is_station_on_air(station_key))", configure)
        self.assertIn("was_running or (autostart and station_running)", configure)
        self.assertIn('"started_immediately"', configure)

    def test_live_dsp_helper_reconfigures_shared_pipeline_once(self) -> None:
        calls: list[tuple[int, str]] = []
        namespace: dict[str, object] = {
            "_native_encoder_runtime_snapshot": lambda station: (
                station == "db-Test.db",
                {
                    "stream_9": {"enabled": True},
                    "stream_2": {"enabled": True},
                    "stream_4": {"enabled": False},
                    "legacy": {"enabled": True},
                },
            ),
            "_encoder_action_native": lambda stream_id, action: calls.append((stream_id, action)),
        }
        helper = self._load_function("_apply_live_dsp_setting", namespace)
        result = helper("db-Test.db")
        self.assertTrue(result["station_running"])
        self.assertTrue(result["live_applied"])
        self.assertEqual(result["reconfigured_stream_id"], 2)
        self.assertEqual(calls, [(2, "start")])

    def test_live_dsp_change_is_the_only_on_air_settings_exception(self) -> None:
        settings = self._source("api_studio_settings")
        self.assertIn("only the Enable DSP checkbox can be changed", settings)
        self.assertIn("station_on_air and dsp_changed", settings)
        self.assertIn("_apply_live_dsp_setting(station_key)", settings)
        self.assertIn('"dsp_live_reconfigured"', settings)
        self.assertNotIn("General settings can only be changed while the station is OFF AIR.", settings)

    def test_versions_are_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6024"', self.app_source)
        engine_header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6024"', engine_header)
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6009 - 2026-07-22", history)


if __name__ == "__main__":
    unittest.main()
