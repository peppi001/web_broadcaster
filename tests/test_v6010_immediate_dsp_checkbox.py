from __future__ import annotations

import ast
import copy
import unittest
from datetime import datetime
from types import SimpleNamespace
from pathlib import Path


class V6010ImmediateDspCheckboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }
        cls.js = (cls.root / "html/static/broadcaster.js").read_text(encoding="utf-8")
        cls.template = (cls.root / "html/broadcaster.html").read_text(encoding="utf-8")
        cls.header = (cls.root / "native_engine/include/engine.h").read_text(encoding="utf-8")
        cls.output_source = (cls.root / "native_engine/src/icecast_output.c").read_text(encoding="utf-8")

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def _load_route(self, namespace: dict[str, object]):
        node = copy.deepcopy(self.functions["api_studio_settings_dsp"])
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.root / "app.py"), "exec"), namespace)
        return namespace["api_studio_settings_dsp"]

    def test_checkbox_has_stable_identity_and_change_listener(self) -> None:
        self.assertIn('id="studio-settings-dsp-enabled"', self.template)
        self.assertIn("studioSettingsDspEnabled: document.getElementById('studio-settings-dsp-enabled')", self.js)
        self.assertIn("studioSettingsDspEnabled.addEventListener('change'", self.js)
        self.assertIn("applyStudioDspSettingImmediately()", self.js)

    def test_immediate_request_is_dsp_only_and_rolls_back_ui_on_failure(self) -> None:
        self.assertIn("fetch('/api/studio/settings/dsp'", self.js)
        self.assertIn("JSON.stringify({ enabled: requestedEnabled })", self.js)
        self.assertIn("checkbox.dataset.persistedEnabled", self.js)
        self.assertIn("checkbox.checked = persistedEnabled", self.js)
        self.assertIn("checkbox.disabled = true", self.js)
        self.assertIn("els.studioSettingsSave.disabled = true", self.js)
        self.assertIn("els.studioSettingsSave.disabled = saveWasDisabled", self.js)

    def test_dsp_only_route_persists_then_applies_live_with_database_rollback(self) -> None:
        route = self._source("api_studio_settings_dsp")
        self.assertIn('"enabled" not in payload', route)
        self.assertIn("UPDATE settings SET dsp_enabled", route)
        self.assertIn("_apply_live_dsp_setting(station_key)", route)
        self.assertIn("current_dsp_enabled", route)
        self.assertIn('"dsp_live_reconfigured"', route)
        self.assertNotIn("radio_name", route)


    def test_route_applies_immediately_and_publishes_live_change(self) -> None:
        updates: list[int] = []
        publishes: list[tuple[object, ...]] = []

        class Cursor:
            def execute(self, sql, params=()):
                if sql.lstrip().startswith("UPDATE settings"):
                    updates.append(int(params[0]))
                return self

            def fetchone(self):
                return {"id": 7, "dsp_enabled": 0}

        class Connection:
            def __init__(self):
                self.cursor_object = Cursor()

            def cursor(self):
                return self.cursor_object

            def commit(self):
                return None

            def close(self):
                return None

        route = self._load_route({
            "request": SimpleNamespace(get_json=lambda silent=True: {"enabled": True}),
            "jsonify": lambda value: value,
            "get_active_station_db_path": lambda: "/tmp/db-Test.db",
            "get_active_station_key": lambda: "db-Test.db",
            "get_db": Connection,
            "is_station_on_air": lambda station: station == "db-Test.db",
            "_parse_checkbox_value": lambda value: 1 if value else 0,
            "_apply_live_dsp_setting": lambda station: {
                "station_running": True,
                "live_applied": True,
                "reconfigured_stream_id": 4,
            },
            "_publish_ui_encoders_changed": lambda *args: publishes.append(args),
            "datetime": datetime,
        })

        response = route()
        self.assertTrue(response["ok"])
        self.assertTrue(response["dsp_enabled"])
        self.assertEqual(updates, [1])
        self.assertEqual(publishes, [("db-Test.db", "dsp_live_reconfigured", 4)])

    def test_route_rolls_database_and_live_state_back_on_failure(self) -> None:
        updates: list[int] = []
        apply_calls: list[str] = []

        class Cursor:
            def execute(self, sql, params=()):
                if sql.lstrip().startswith("UPDATE settings"):
                    updates.append(int(params[0]))
                return self

            def fetchone(self):
                return {"id": 8, "dsp_enabled": 0}

        class Connection:
            def cursor(self):
                return Cursor()

            def commit(self):
                return None

            def close(self):
                return None

        def apply(station):
            apply_calls.append(station)
            if len(apply_calls) == 1:
                raise RuntimeError("DSP startup failed")
            return {"station_running": True, "live_applied": True}

        route = self._load_route({
            "request": SimpleNamespace(get_json=lambda silent=True: {"enabled": True}),
            "jsonify": lambda value: value,
            "get_active_station_db_path": lambda: "/tmp/db-Test.db",
            "get_active_station_key": lambda: "db-Test.db",
            "get_db": Connection,
            "is_station_on_air": lambda _station: True,
            "_parse_checkbox_value": lambda value: 1 if value else 0,
            "_apply_live_dsp_setting": apply,
            "_publish_ui_encoders_changed": lambda *args: None,
            "datetime": datetime,
        })

        body, status = route()
        self.assertEqual(status, 500)
        self.assertFalse(body["ok"])
        self.assertFalse(body["dsp_enabled"])
        self.assertEqual(updates, [1, 0])
        self.assertEqual(apply_calls, ["db-Test.db", "db-Test.db"])

    def test_first_dsp_pcm_activates_in_process_route(self) -> None:
        self.assertIn("ssnative_process_s16_interleaved", self.output_source)
        self.assertIn("output->dsp_writer_last_progress_monotonic_ms = ready_ms", self.output_source)
        self.assertIn("output->dsp_route_active = true", self.output_source)
        self.assertNotIn("normal running-state input-stall watchdog", self.output_source)

    def test_versions_are_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6024"', self.app_source)
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6024"', self.header)
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6010 - 2026-07-22", history)


if __name__ == "__main__":
    unittest.main()
