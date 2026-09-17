from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V6060ControlStartStationKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_api_control_start_passes_resolved_station_key_to_service(self) -> None:
        source = self._function_source("api_control")
        self.assertIn(
            "start_payload, start_status = _get_station_service().start(station_key)",
            source,
        )
        self.assertNotIn(
            "start_payload, start_status = _get_station_service().start()",
            source,
        )

    def test_station_route_keeps_backend_auto_script_activation(self) -> None:
        source = self._function_source("station_start")
        self.assertIn("_get_station_service().start()", source)
        self.assertIn("_auto_start_station_automation_for_on_air(station_key)", source)
        self.assertIn("_SCRIPT_ENGINE_WAKE_EVENT.set()", source)
        self.assertIn("_SCHEDULER_WAKE_EVENT.set()", source)


if __name__ == "__main__":
    unittest.main()
