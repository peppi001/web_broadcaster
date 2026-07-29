from __future__ import annotations

import ast
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path


class NativeSettingsSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        namespace = {"datetime": datetime}
        selected = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_create_canonical_settings_table"
        ]
        exec(compile(ast.Module(body=selected, type_ignores=[]), "settings_subset", "exec"), namespace)
        cls.create_schema = staticmethod(namespace["_create_canonical_settings_table"])

    def test_empty_database_creates_canonical_settings_schema(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        self.create_schema(cur)
        columns = {row[1] for row in cur.execute("PRAGMA table_info(settings)")}
        expected = {
            "id", "radio_name", "music_library_path", "created_at", "updated_at",
            "ssproc_appimage", "dsp_enabled",
            "cue_in_threshold", "cue_out_threshold", "cross_threshold", "overlap_seconds",
            "cue_start_offset_seconds", "gap_killer_start_dbfs", "gap_killer_end_dbfs",
            "crossfade_trigger_relative_db", "crossfade_fallback_seconds",
            "crossfade_min_seconds", "crossfade_max_seconds",
            "crossfade_fade_out_seconds", "no_crossfade_max_duration_sec",
        }
        self.assertTrue(expected.issubset(columns))
        self.assertNotIn("SoundSolution_path", columns)
        self.assertNotIn("crossfade_ms", columns)
        conn.close()

    def test_empty_database_schema_creation_is_idempotent(self) -> None:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        self.create_schema(cur)
        self.create_schema(cur)
        self.assertEqual(cur.execute("SELECT COUNT(*) FROM settings").fetchone()[0], 0)
        conn.close()

    def test_forms_use_only_current_audio_engine_fields(self) -> None:
        combined = "\n".join(
            (self.root / name).read_text(encoding="utf-8")
            for name in ("html/no_stations.html", "html/dashboard.html", "html/broadcaster.html")
        )
        self.assertNotIn('name="telnet_port"', combined)
        self.assertNotIn('name="http_api_port"', combined)
        self.assertIn('name="soundsolution_path"', combined)
        self.assertNotIn('name="crossfade_ms"', combined)
        self.assertNotIn("Crossfade Offset (in milliseconds)", combined)


if __name__ == "__main__":
    unittest.main()
