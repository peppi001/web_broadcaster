from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from storage import PlaybackRepository


class FreshDatabaseBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function(self, name: str) -> ast.FunctionDef:
        return next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )

    def test_empty_global_database_uses_only_current_schema(self) -> None:
        function = self._function("_init_global_db_uncached")
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "global.db"

            def get_global_db() -> sqlite3.Connection:
                return sqlite3.connect(db_path)

            namespace = {"get_global_db": get_global_db}
            exec(
                compile(ast.Module(body=[function], type_ignores=[]), "global_schema_subset", "exec"),
                namespace,
            )
            namespace["_init_global_db_uncached"]()

            conn = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            conn.close()

            self.assertTrue(
                {"users", "stations", "audio_engine_state", "scheduler_rules"}.issubset(tables)
            )
            self.assertNotIn("process_pids", tables)

    def test_empty_station_settings_table_is_canonical(self) -> None:
        function = self._function("_create_canonical_settings_table")
        namespace: dict[str, object] = {}
        exec(
            compile(ast.Module(body=[function], type_ignores=[]), "station_schema_subset", "exec"),
            namespace,
        )
        conn = sqlite3.connect(":memory:")
        cursor = conn.cursor()
        namespace["_create_canonical_settings_table"](cursor)
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(settings)")}
        conn.close()

        self.assertTrue(
            {
                "radio_name",
                "music_library_path",
                "ssproc_appimage",
                "dsp_enabled",
                "cue_in_threshold",
                "cue_out_threshold",
                "crossfade_max_seconds",
            }.issubset(columns)
        )
        self.assertNotIn("SoundSolution_path", columns)

    def test_runtime_playback_state_has_no_retired_pending_history_columns(self) -> None:
        function = self._function("_ensure_runtime_playback_state_schema")
        namespace: dict[str, object] = {"sqlite3": sqlite3, "PlaybackRepository": PlaybackRepository}
        exec(
            compile(ast.Module(body=[function], type_ignores=[]), "runtime_schema_subset", "exec"),
            namespace,
        )
        conn = sqlite3.connect(":memory:")
        namespace["_ensure_runtime_playback_state_schema"](conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runtime_playback_state)")}
        conn.close()

        self.assertTrue(
            {
                "current_track_path",
                "current_queue_id",
                "current_track_id",
                "last_commit_key",
                "last_commit_played_at",
            }.issubset(columns)
        )
        self.assertTrue(
            {
                "pending_track_path",
                "pending_title",
                "pending_artist",
                "pending_album",
                "pending_queue_id",
                "pending_track_id",
                "pending_seen_at",
            }.isdisjoint(columns)
        )


if __name__ == "__main__":
    unittest.main()
