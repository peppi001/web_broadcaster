from __future__ import annotations

import ast
import unittest
from pathlib import Path


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += float(seconds)


class V6061ScriptTransitionRaceTests(unittest.TestCase):
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
        cls.native_python_source = (
            cls.root / "audio_engine" / "native_engine.py"
        ).read_text(encoding="utf-8")

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def test_script_next_does_not_preload_via_queue_mutation_replan(self) -> None:
        source = self._source("_run_station_script_once")
        self.assertIn("if ok and not do_next:", source)
        self.assertIn('_ab_replan_after_queue_mutation(reason="script_move_to_front")', source)
        next_branch = source[source.index("if ok and do_next:"):]
        self.assertIn("_ab_wait_for_native_transition_idle", next_branch)
        self.assertIn("_perform_player_manual_next_action", next_branch)

    def test_transition_idle_wait_ignores_terminal_playback_started_latch(self) -> None:
        fake_time = _FakeTime()
        states = iter([
            {
                "running": True,
                "active_deck": "A",
                "transitioning": True,
                "deck_b_playback_started": True,
                "deck_b_lifecycle_phase": "playing",
            },
            {
                "running": True,
                "active_deck": "A",
                "transitioning": False,
                "deck_b_playback_started": True,
                "deck_b_lifecycle_phase": "terminal",
            },
        ])
        last = {
            "running": True,
            "active_deck": "A",
            "transitioning": False,
            "deck_b_playback_started": True,
            "deck_b_lifecycle_phase": "terminal",
        }

        def native_state(_station: str):
            nonlocal last
            try:
                last = next(states)
            except StopIteration:
                pass
            return dict(last)

        namespace = {
            "time": fake_time,
            "_native_station_state": native_state,
        }
        module = ast.Module(
            body=[self.functions["_ab_wait_for_native_transition_idle"]],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        ok, state = namespace["_ab_wait_for_native_transition_idle"](
            "db-Max_FM.db", timeout_sec=1.0, poll_interval_sec=0.02
        )
        self.assertTrue(ok)
        self.assertFalse(state["transitioning"])
        self.assertEqual(state["deck_b_lifecycle_phase"], "terminal")

    def test_queue_mutation_preload_is_transition_and_playback_guarded(self) -> None:
        source = self._source("_ab_replan_after_queue_mutation")
        self.assertIn('if bool(native_state.get("transitioning")):', source)
        self.assertIn("_ab_schedule_deferred_replan", source)
        self.assertIn("reject_if_active_deck=True", source)
        self.assertIn("reject_if_playback_started=True", source)

    def test_delayed_and_need_next_loads_use_playback_guard(self) -> None:
        delayed = self._source("_ab_schedule_inactive_preload_after_start")
        need_next = self._source("_native_load_requested_next_track")
        hard_select = self._source("_ab_start_cueout_transition_now")
        self.assertIn("reject_if_playback_started=True", delayed)
        self.assertIn("reject_if_playback_started=True", need_next)
        self.assertIn("reject_if_playback_started=bool(manual_next_fast)", hard_select)
        self.assertIn("_ab_wait_for_native_deck_prebuffer(", hard_select)
        self.assertIn("timeout_sec=4.0", hard_select)

    def test_native_load_option_rejects_audible_nonterminal_deck(self) -> None:
        self.assertIn(
            'wb_json_get_bool(options_json, "reject_if_playback_started"',
            self.engine_source,
        )
        self.assertIn("reject_if_playback_started", self.engine_source)
        self.assertIn("confirmed->playback_started", self.engine_source)
        self.assertIn("!confirmed->terminal", self.engine_source)
        self.assertIn("!confirmed->consumed", self.engine_source)
        self.assertIn("deck_playback_in_use", self.engine_source)
        self.assertIn('"reject_if_playback_started": bool(reject_if_playback_started)', self.native_python_source)


if __name__ == "__main__":
    unittest.main()
