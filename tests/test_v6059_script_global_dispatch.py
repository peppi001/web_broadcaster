from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V6059ScriptGlobalDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_exact_time_dispatcher_never_enters_thirty_second_idle_sleep(self) -> None:
        loop_source = self._function_source("_script_engine_loop")
        self.assertIn("_SCRIPT_ENGINE_ACTIVE_POLL_SECONDS", loop_source)
        self.assertIn("_SCRIPT_ENGINE_WAKE_EVENT", loop_source)
        self.assertNotIn("_SCRIPT_ENGINE_IDLE_POLL_SECONDS", loop_source)

    def test_station_start_backend_activates_auto_scripts_and_wakes_dispatchers(self) -> None:
        start_source = self._function_source("station_start")
        self.assertIn("_auto_start_station_automation_for_on_air", start_source)
        self.assertIn("_SCRIPT_ENGINE_WAKE_EVENT.set()", start_source)
        self.assertIn("_SCHEDULER_WAKE_EVENT.set()", start_source)

    def test_browser_endpoint_and_station_start_share_same_backend_autostart_helper(self) -> None:
        endpoint_source = self._function_source("api_studio_scripts_auto_start_on_air")
        helper_source = self._function_source("_auto_start_station_automation_for_on_air")
        self.assertIn("_auto_start_station_automation_for_on_air", endpoint_source)
        self.assertIn("auto_start", helper_source)
        self.assertIn("UPDATE station_scripts SET status", helper_source)
        self.assertIn("UPDATE scheduler_rules SET is_enabled = 1", helper_source)

    def test_v6059_contract_is_preserved_in_current_release(self) -> None:
        import re

        app_match = re.search(r'^APP_VERSION = "([0-9]+)"$', self.source, re.MULTILINE)
        native_header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        native_match = re.search(
            r'^#define WB_NATIVE_DAEMON_VERSION "([0-9]+)"$',
            native_header,
            re.MULTILINE,
        )
        self.assertIsNotNone(app_match)
        self.assertIsNotNone(native_match)
        self.assertGreaterEqual(int(app_match.group(1)), 6059)
        self.assertEqual(app_match.group(1), native_match.group(1))


if __name__ == "__main__":
    unittest.main()
