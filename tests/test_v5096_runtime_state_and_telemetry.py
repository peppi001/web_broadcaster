from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V5096RuntimeStateAndTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def test_removed_write_only_ab_state_fields_do_not_return(self) -> None:
        self.assertNotIn('"phase":', self.source)
        self.assertNotIn('"last_seed_at"', self.source)
        self.assertNotIn('_AB_PLAYER_STATE["starting"]', self.source)

    def test_preload_reuse_trace_publishes_protocol_event(self) -> None:
        node = self.functions["_preload_reuse_trace"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        published: list[tuple] = []
        namespace = {
            "os": __import__("os"),
            "get_active_station_key": lambda: "db-Test.db",
            "_publish_audio_engine_event": lambda *args, **kwargs: published.append((args, kwargs)),
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        namespace["_preload_reuse_trace"](
            "ab_hard_select_reused_native_preload",
            station_key="db-Test.db",
            deck="B",
            queue_id=31,
            slot_token="31-ready-5",
            reason="manual_next_db_head_direct_handoff",
            manual_next_request_id="mn-1",
        )
        self.assertEqual(len(published), 1)
        args, kwargs = published[0]
        self.assertEqual(args[0], "ab_hard_select_reused_native_preload")
        self.assertEqual(kwargs["station_key"], "db-Test.db")
        self.assertEqual(kwargs["queue_id"], 31)
        self.assertEqual(kwargs["deck"], "B")
        self.assertEqual(kwargs["payload"]["slot_token"], "31-ready-5")
        self.assertEqual(kwargs["payload"]["manual_next_request_id"], "mn-1")

    def test_both_reuse_paths_use_persistent_trace(self) -> None:
        for name in ("_ab_replan_after_queue_mutation", "_ab_start_cueout_transition_now"):
            text = ast.get_source_segment(self.source, self.functions[name]) or ""
            self.assertIn("_preload_reuse_trace", text)


if __name__ == "__main__":
    unittest.main()
