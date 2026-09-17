from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V6056NeedNextDeckStateMachineTests(unittest.TestCase):
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
        cls.engine_source = (
            cls.root / "native_engine" / "src" / "engine.c"
        ).read_text(encoding="utf-8")
        cls.protocol_source = (
            cls.root / "audio_engine" / "protocol.py"
        ).read_text(encoding="utf-8")

    def _exec_functions(self, names: list[str]) -> dict:
        namespace: dict = {}
        module = ast.Module(body=[self.functions[name] for name in names], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace

    def test_stale_previous_probe_eof_does_not_kill_new_loading_candidate(self) -> None:
        namespace = self._exec_functions([
            "_ab_native_deck_runtime_phase",
            "_ab_native_deck_has_live_candidate",
        ])
        state = {
            "active_deck": "A",
            "deck_b_queue_id": 5463,
            "deck_b_slot_token": "new-5463",
            "deck_b_consumed": False,
            "deck_b_terminal": False,
            "deck_b_playback_started": False,
            "native_deck_b_analysis_ready": False,
            "native_deck_b_analysis_failed": False,
            # The audio worker can still expose the previous short ID for a few
            # milliseconds after the new descriptor is confirmed.
            "native_audio_deck_b_queue_id": 5464,
            "native_audio_deck_b_slot_token": "old-5464",
            "native_audio_deck_b_status": "eof",
            "native_audio_deck_b_prebuffer_ready": False,
            "native_audio_deck_b_ring_buffer_bytes": 0,
        }
        phase, queue_id, slot_token = namespace["_ab_native_deck_runtime_phase"](state, "b")
        self.assertEqual((phase, queue_id, slot_token), ("loading", 5463, "new-5463"))
        self.assertTrue(namespace["_ab_native_deck_has_live_candidate"](state, "b"))

    def test_identity_matched_terminal_probe_is_still_terminal(self) -> None:
        namespace = self._exec_functions(["_ab_native_deck_runtime_phase"])
        state = {
            "deck_b_queue_id": 5464,
            "deck_b_slot_token": "id-5464",
            "native_audio_deck_b_queue_id": 5464,
            "native_audio_deck_b_slot_token": "id-5464",
            "native_audio_deck_b_status": "eof",
            "native_deck_b_analysis_ready": True,
        }
        phase, _, _ = namespace["_ab_native_deck_runtime_phase"](state, "b")
        self.assertEqual(phase, "terminal")

    def test_need_next_checks_live_target_before_destructive_reload(self) -> None:
        function = ast.get_source_segment(
            self.app_source, self.functions["_native_load_requested_next_track"]
        ) or ""
        self.assertGreaterEqual(function.count("_ab_native_deck_has_live_candidate("), 2)
        self.assertIn("final_state = _native_station_state(station_key)", function)
        self.assertIn("_ab_native_deck_runtime_phase(", function)

    def test_native_state_exports_explicit_deck_lifecycle(self) -> None:
        self.assertIn('\\"deck_a_playback_started\\":%s', self.engine_source)
        self.assertIn('\\"deck_a_lifecycle_phase\\":\\"%s\\"', self.engine_source)
        self.assertIn('\\"deck_b_playback_started\\":%s', self.engine_source)
        self.assertIn('\\"deck_b_lifecycle_phase\\":\\"%s\\"', self.engine_source)
        self.assertIn('static const char *deck_lifecycle_phase(', self.engine_source)
        self.assertIn('"deck_a_lifecycle_phase"', self.protocol_source)
        self.assertIn('"deck_b_lifecycle_phase"', self.protocol_source)

    def test_native_need_next_guard_rejects_playing_active_deck(self) -> None:
        self.assertIn('wb_json_get_bool(options_json, "reject_if_active_deck"', self.engine_source)
        self.assertIn("reject_if_active_deck", self.engine_source)
        self.assertIn("confirmed->playback_started", self.engine_source)
        self.assertIn("state->active_deck == deck", self.engine_source)
        self.assertIn('active_deck_in_use', self.engine_source)
        function = ast.get_source_segment(
            self.app_source, self.functions["_native_load_requested_next_track"]
        ) or ""
        self.assertIn("reject_if_active_deck=True", function)


if __name__ == "__main__":
    unittest.main()
