from __future__ import annotations

import ast
import sqlite3
import unittest
from pathlib import Path


class V6006CrossfadeOffsetCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.templates = "\n".join(
            (cls.root / name).read_text(encoding="utf-8")
            for name in (
                "html/no_stations.html",
                "html/dashboard.html",
                "html/broadcaster.html",
            )
        )

    def test_obsolete_crossfade_offset_is_absent_from_runtime_source_and_forms(self) -> None:
        self.assertNotIn("crossfade_ms", self.app)
        self.assertNotIn("crossfade_ms", self.templates)
        self.assertNotIn("Crossfade Offset (in milliseconds)", self.templates)

    def test_empty_database_schema_does_not_create_retired_column(self) -> None:
        tree = ast.parse(self.app)
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_create_canonical_settings_table"
        )
        namespace: dict[str, object] = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "settings_subset", "exec"), namespace)
        conn = sqlite3.connect(":memory:")
        try:
            namespace["_create_canonical_settings_table"](conn.cursor())
            columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
        finally:
            conn.close()
        self.assertNotIn("crossfade_ms", columns)

    def test_real_native_transition_settings_remain_intact(self) -> None:
        for name in (
            "crossfade_trigger_relative_db",
            "crossfade_fallback_seconds",
            "crossfade_min_seconds",
            "crossfade_max_seconds",
            "crossfade_fade_out_seconds",
            "no_crossfade_max_duration_sec",
        ):
            self.assertIn(name, self.app)


if __name__ == "__main__":
    unittest.main()
