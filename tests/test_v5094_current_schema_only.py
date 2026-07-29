from __future__ import annotations

import ast
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path

from storage import PlaybackRepository


class V5094CurrentSchemaOnlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function(self, name: str) -> ast.FunctionDef:
        matches = [
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        self.assertEqual(len(matches), 1, name)
        return matches[0]

    def test_retired_route_aliases_and_single_station_import_are_absent(self) -> None:
        retired = {
            '"/dashboard_classic"',
            '"/stations/new"',
            '"/studio/dashboard"',
            '"/studio"',
            '"/player"',
            '"/settings"',
            '"/library"',
            '"/autodj"',
            "LEGACY_STATION_DATABASE",
            "radio_automation.db",
            "_infer_station_name_from_db",
        }
        for token in retired:
            self.assertNotIn(token, self.source)

    def test_no_database_upgrade_ddl_remains(self) -> None:
        self.assertNotIn("ALTER TABLE", self.source)
        self.assertNotIn("categories__legacy_fix", self.source)
        self.assertNotIn("autodj_rotation_nofade", self.source)
        self.assertNotIn("no_repeat_minutes", self.source)
        self.assertNotIn("SoundSolution_path", self.source)

    def test_station_name_helper_has_one_definition(self) -> None:
        definitions = [
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "get_station_name_safe"
        ]
        self.assertEqual(len(definitions), 1)

    def test_canonical_autodj_tables_have_only_current_columns(self) -> None:
        namespace = {"sqlite3": sqlite3}
        nodes = [
            self._function("ensure_autodj_rotation_table"),
            self._function("ensure_autodj_settings_table"),
        ]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "autodj_schema_subset", "exec"), namespace)
        conn = sqlite3.connect(":memory:")
        namespace["ensure_autodj_rotation_table"](conn)
        namespace["ensure_autodj_settings_table"](conn)
        rotation = {row[1] for row in conn.execute("PRAGMA table_info(autodj_rotation)")}
        settings = {row[1] for row in conn.execute("PRAGMA table_info(autodj_settings)")}
        conn.close()
        self.assertEqual(
            rotation,
            {"id", "position", "category_id", "norules", "created_at"},
        )
        self.assertEqual(
            settings,
            {
                "id", "no_repeat_artist_minutes", "no_repeat_title_minutes",
                "no_repeat_track_minutes", "keep_queue", "editor_text",
                "created_at", "rotation_next_index", "rotation_signature",
            },
        )

    def test_full_init_db_creates_only_current_station_schema(self) -> None:
        create_settings = self._function("_create_canonical_settings_table")
        create_runtime = self._function("_ensure_runtime_playback_state_schema")
        init_db = self._function("init_db")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "station.db"

            def get_db():
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                return conn

            namespace = {
                "os": __import__("os"),
                "sqlite3": sqlite3,
                "datetime": datetime,
                "get_active_station_db_path": lambda: str(db_path),
                "get_db": get_db,
                "_initialized_station_dbs": set(),
                "_db_init_lock": threading.RLock(),
                "NoActiveStationError": RuntimeError,
                "PlaybackRepository": PlaybackRepository,
            }
            exec(
                compile(
                    ast.Module(body=[create_settings, create_runtime, init_db], type_ignores=[]),
                    "full_station_schema_subset",
                    "exec",
                ),
                namespace,
            )
            namespace["init_db"](force=True)

            conn = sqlite3.connect(db_path)
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({
                "settings", "icecast_streams", "categories", "tracks",
                "category_tracks", "autodj_rotation", "autodj_settings",
                "queue_items", "play_history", "runtime_playback_state",
                "encoder_runtime_state", "scheduler_rules",
            }.issubset(tables))
            queue_columns = {row[1] for row in conn.execute("PRAGMA table_info(queue_items)")}
            settings_columns = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
            rotation_columns = {row[1] for row in conn.execute("PRAGMA table_info(autodj_rotation)")}
            autodj_columns = {row[1] for row in conn.execute("PRAGMA table_info(autodj_settings)")}
            seeded = conn.execute(
                "SELECT no_repeat_artist_minutes, no_repeat_title_minutes, "
                "no_repeat_track_minutes, keep_queue FROM autodj_settings"
            ).fetchone()
            conn.close()

            self.assertNotIn("autodj_rotation_nofade", queue_columns)
            self.assertNotIn("SoundSolution_path", settings_columns)
            self.assertNotIn("nofade", rotation_columns)
            self.assertNotIn("no_repeat_minutes", autodj_columns)
            self.assertEqual(tuple(seeded), (60, 60, 60, 3))


if __name__ == "__main__":
    unittest.main()
