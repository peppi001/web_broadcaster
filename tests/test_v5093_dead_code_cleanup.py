from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V5093DeadCodeCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.top_level_functions = [
            node.name for node in cls.tree.body if isinstance(node, ast.FunctionDef)
        ]

    def test_only_one_search_route_implementation_remains(self) -> None:
        self.assertEqual(self.top_level_functions.count("search_tracks"), 1)
        search_node = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "search_tracks"
        )
        decorators = [ast.unparse(item) for item in search_node.decorator_list]
        self.assertTrue(any("/api/search_tracks" in item for item in decorators))

    def test_removed_dead_helpers_do_not_return(self) -> None:
        removed = {
            "_pid_exists",
            "_runtime_get_pending_history_song",
            "_runtime_record_pending_history",
            "_runtime_clear_pending_history",
            "_infer_autodj_rotation_index_from_queue",
            "_script_time_matches",
            "_sync_station_queue_before_skip",
            "_dequeue_station_queue_id_to_history",
            "_perform_station_script_break_next",
            "get_station_overlap_seconds",
            "_schedule_seek_eof_completion_after_handoff",
            "_commit_seek_eof_completion_snapshot",
            "_kill_pgid",
            "_sched_debug",
            "_mark_station_queue_ids_clean_transition",
            "_resolve_insert_to_track_ids",
            "_enqueue_track_ids",
            "_scheduler_update_after_run",
            "_normalize_stream_url",
            "init_fts",
            "_seek_owner_guard_state_locked",
        }
        self.assertTrue(removed.isdisjoint(self.top_level_functions))

    def test_deleted_imports_are_absent(self) -> None:
        imported = set()
        for node in self.tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.asname or alias.name for alias in node.names)
        self.assertTrue({"stat", "subprocess", "glob"}.isdisjoint(imported))


if __name__ == "__main__":
    unittest.main()
