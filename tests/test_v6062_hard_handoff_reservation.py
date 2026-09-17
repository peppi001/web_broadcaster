from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V6062HardHandoffReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.app_source = cls.app_path.read_text(encoding="utf-8")
        cls.app_tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.app_tree.body
            if isinstance(node, ast.FunctionDef)
        }
        cls.engine_source = (cls.root / "native_engine" / "src" / "engine.c").read_text(encoding="utf-8")
        cls.icecast_source = (cls.root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")
        cls.player_source = (cls.root / "player" / "orchestration.py").read_text(encoding="utf-8")

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def test_script_waits_for_armed_hard_handoff(self) -> None:
        source = self._source("_ab_wait_for_native_transition_idle")
        self.assertIn('hard_handoff_armed = bool(last_state.get("hard_handoff_armed"))', source)
        self.assertIn("and not hard_handoff_armed", source)

    def test_native_get_state_exports_pending_hard_handoff_identity(self) -> None:
        for field in (
            "hard_handoff_armed",
            "hard_handoff_from_deck",
            "hard_handoff_to_deck",
            "hard_handoff_to_queue_id",
            "hard_handoff_to_slot_token",
        ):
            self.assertIn(field, self.engine_source)

    def test_load_rejects_different_identity_on_pending_handoff_target(self) -> None:
        self.assertIn("hard_handoff_target_conflict", self.engine_source)
        self.assertIn("state->icecast_output.hard_handoff_pending", self.engine_source)
        self.assertIn("state->icecast_output.hard_handoff_to_deck == deck", self.engine_source)
        self.assertIn("hard_handoff_target_reserved", self.engine_source)

    def test_handoff_schedule_revalidates_control_identity_under_engine_lock(self) -> None:
        schedule_start = self.icecast_source.index("int wb_icecast_output_schedule_hard_handoff")
        schedule_end = self.icecast_source.index("bool wb_icecast_output_has_pending_hard_handoff", schedule_start)
        schedule = self.icecast_source[schedule_start:schedule_end]
        self.assertIn("pthread_mutex_lock(&state->lock)", schedule)
        self.assertIn("from_live->queue_id == from_track->queue_id", schedule)
        self.assertIn("to_live->queue_id == to_track->queue_id", schedule)
        self.assertIn("hard handoff control identity changed", schedule)
        self.assertIn("pthread_mutex_lock(&output->lock)", schedule)

    def test_manual_next_has_ready_active_head_recovery_path(self) -> None:
        self.assertIn("manual_next_active_head_not_audible_recovery", self.player_source)
        self.assertIn('active_phase == "playing"', self.player_source)
        self.assertIn('probe_status in {"decoding", "playing"}', self.player_source)
        self.assertIn("if has_audibility_evidence and not target_is_audible", self.player_source)


if __name__ == "__main__":
    unittest.main()
