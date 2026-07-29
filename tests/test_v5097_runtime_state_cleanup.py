from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V5097RuntimeStateCleanupTests(unittest.TestCase):
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

    def test_additional_write_only_ab_fields_do_not_return(self) -> None:
        for field in (
            "seek_pending_station_key",
            "seek_pending_target_position",
            "seek_pending_requested_at",
            "hard_handoff_armed_at",
        ):
            self.assertNotIn(field, self.source)

    def test_seek_identity_guards_remain_authoritative(self) -> None:
        node = self.functions["_ab_seek_event_identity_matches"]
        text = ast.get_source_segment(self.source, node) or ""
        self.assertIn("seek_pending_active", text)
        self.assertIn("seek_pending_queue_id", text)
        self.assertIn("seek_pending_slot_token", text)

    def test_seek_and_handoff_deadlines_remain(self) -> None:
        self.assertIn('"seek_pending_deadline"', self.source)
        self.assertIn('"hard_handoff_deadline"', self.source)
        self.assertIn('"hard_handoff_station_key"', self.source)

    def test_native_seek_pending_no_longer_parses_unused_target_mirror(self) -> None:
        node = self.functions["_ab_mark_native_seek_pending"]
        text = ast.get_source_segment(self.source, node) or ""
        self.assertNotIn("seek_target_position_ms", text)
        self.assertIn("event_wall_time + 10.0", text)


if __name__ == "__main__":
    unittest.main()
