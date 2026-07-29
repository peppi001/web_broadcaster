from __future__ import annotations

import ast
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


class V5095PreloadDedupTests(unittest.TestCase):
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

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def _exec_function(self, name: str, namespace: dict) -> None:
        module = ast.Module(body=[self.functions[name]], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)

    def test_confirmed_load_records_identity_for_current_generation(self) -> None:
        state = {
            "generation": 7,
            "player_loaded_keys": {},
            "player_generation": {},
        }
        namespace = {
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": state,
            "_ab_loaded_identity_key": lambda _uri: "31:401:/music/next.mp3",
            "_parse_annotate_meta": lambda _uri: {"wb_ab_generation": "7"},
        }
        self._exec_function("_ab_record_player_loaded_identity", namespace)
        self.assertTrue(namespace["_ab_record_player_loaded_identity"]("B", "engine-uri"))
        self.assertEqual(state["player_loaded_keys"], {"b": "31:401:/music/next.mp3"})
        self.assertEqual(state["player_generation"], {"b": 7})

    def test_late_success_from_old_generation_is_not_recorded(self) -> None:
        state = {
            "generation": 8,
            "player_loaded_keys": {"b": "32:402:/music/current-next.mp3"},
            "player_generation": {"b": 8},
        }
        namespace = {
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": state,
            "_ab_loaded_identity_key": lambda _uri: "31:401:/music/stale.mp3",
            "_parse_annotate_meta": lambda _uri: {"wb_ab_generation": "7"},
        }
        self._exec_function("_ab_record_player_loaded_identity", namespace)
        self.assertFalse(namespace["_ab_record_player_loaded_identity"]("B", "stale-uri"))
        self.assertEqual(state["player_loaded_keys"], {"b": "32:402:/music/current-next.mp3"})
        self.assertEqual(state["player_generation"], {"b": 8})

    def test_native_deck_match_requires_exact_queue_and_confirmed_token(self) -> None:
        namespace = {
            "_ab_line_info": lambda _line: {"queue_id": 31},
            "_ab_loaded_identity_key": lambda _line: "31:401:/music/next.mp3",
        }
        self._exec_function("_ab_native_deck_matches_line", namespace)
        match = namespace["_ab_native_deck_matches_line"](
            {"deck_b_queue_id": 31, "deck_b_slot_token": "31-token"},
            "b",
            "next-line",
        )
        self.assertEqual(match, (True, "31:401:/music/next.mp3", 31, "31-token"))
        no_token = namespace["_ab_native_deck_matches_line"](
            {"deck_b_queue_id": 31, "deck_b_slot_token": ""},
            "b",
            "next-line",
        )
        self.assertFalse(no_token[0])
        wrong_queue = namespace["_ab_native_deck_matches_line"](
            {"deck_b_queue_id": 32, "deck_b_slot_token": "32-token"},
            "b",
            "next-line",
        )
        self.assertFalse(wrong_queue[0])

    def test_ab_push_records_only_successful_native_loads(self) -> None:
        recorded: list[tuple[str, str]] = []
        outcomes = iter((True, False))

        class Engine:
            def load_deck(self, *_args, **_kwargs):
                return next(outcomes)

        namespace = {
            "_ab_prepare_engine_load_uri": lambda player, uri: f"prepared:{player}:{uri}",
            "get_audio_engine": lambda: Engine(),
            "_ab_record_player_loaded_identity": lambda player, uri: recorded.append((player, uri)),
        }
        self._exec_function("_ab_push", namespace)
        self.assertTrue(namespace["_ab_push"]("a", "line-1"))
        self.assertFalse(namespace["_ab_push"]("b", "line-2"))
        self.assertEqual(recorded, [("a", "prepared:a:line-1")])

    def test_replan_and_manual_next_reuse_confirmed_native_preload(self) -> None:
        replan = self._function_source("_ab_replan_after_queue_mutation")
        transition = self._function_source("_ab_start_cueout_transition_now")
        need_next = self._function_source("_native_load_requested_next_track")
        rebuild = self._function_source("_native_rebuild_plan_after_track_started")

        self.assertIn("_ab_native_deck_matches_line", replan)
        self.assertIn("ab_queue_mutation_replan_reused_native_preload", replan)
        self.assertIn('clear_resp = "native_inactive_preload_reused"', replan)
        self.assertIn("loaded_key_now == expected_key_for_target", transition)
        self.assertIn("and native_target_matches", transition)
        self.assertIn("ab_hard_select_reused_native_preload", transition)
        self.assertIn("_ab_record_player_loaded_identity", need_next)
        self.assertIn("loaded_keys[inactive] = next_key", rebuild)

    def test_queue_replan_does_not_reload_unchanged_native_preload(self) -> None:
        module = ast.Module(
            body=[
                self.functions["_ab_native_deck_matches_line"],
                self.functions["_ab_replan_after_queue_mutation"],
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        state = {
            "enabled": True,
            "active": "a",
            "station_key": "db-Test.db",
            "lines": ["current-line", "next-line"],
            "durations": [120.0, 180.0],
            "fadeouts": [5.0, 5.0],
            "current_index": 0,
            "next_index": 1,
            "player_index": {"a": 0, "b": 1},
            "player_loaded_keys": {"b": "31:401:/music/next.mp3"},
            "player_generation": {"b": 5},
            "generation": 5,
            "started_at": time.time() - 30.0,
        }
        native_state = {
            "running": True,
            "active_deck": "A",
            "deck_b_queue_id": 31,
            "deck_b_slot_token": "31-401-ready-5",
        }
        pushes: list[tuple] = []
        traces: list[tuple[str, dict]] = []

        def line_info(line: str) -> dict:
            if line == "current-line":
                return {"queue_id": 30, "track_id": 400, "file": "/music/current.mp3"}
            return {"queue_id": 31, "track_id": 401, "file": "/music/next.mp3"}

        namespace = {
            "time": time,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": state,
            "_ab_manual_next_guard_remaining": lambda: 0.0,
            "_ab_schedule_deferred_replan": lambda *_args, **_kwargs: True,
            "_ab_begin_replan_serial": lambda _reason: 1,
            "get_active_station_key": lambda: "db-Test.db",
            "_native_station_state": lambda _station: dict(native_state),
            "_build_station_queue_plan": lambda _station: ["next-line"],
            "_ab_same_queue_identity": lambda left, right: left == right,
            "_ab_line_duration_and_fade": lambda _line: (180.0, 5.0),
            "_ab_abort_stale_replan_if_needed": lambda *_args, **_kwargs: False,
            "_ab_bootstrap_from_queue_plan": lambda *_args, **_kwargs: True,
            "_ab_resolve_native_live_player": lambda *_args, **_kwargs: (
                "a", {"a_uri": "current-line", "b_uri": "next-line"}, "native"
            ),
            "_ab_line_info": line_info,
            "_ab_line_path": lambda line: line_info(line)["file"],
            "normalize_media_path": lambda path: str(path),
            "_ab_loaded_identity_key": lambda line: (
                "30:400:/music/current.mp3" if line == "current-line"
                else "31:401:/music/next.mp3"
            ),
            "_ab_push": lambda *args, **kwargs: pushes.append((args, kwargs)) or True,
            "_ab_schedule_inactive_preload_after_start": lambda *_args, **_kwargs: True,
            "_preload_reuse_trace": lambda event, **payload: traces.append((event, payload)),
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        self.assertTrue(namespace["_ab_replan_after_queue_mutation"]("autodj_refill"))
        self.assertEqual(pushes, [])
        self.assertEqual(state["generation"], 6)
        self.assertEqual(state["player_index"], {"a": 0, "b": 1})
        self.assertEqual(state["player_generation"]["b"], 6)
        self.assertEqual(state["player_loaded_keys"]["b"], "31:401:/music/next.mp3")
        self.assertTrue(any(event == "ab_queue_mutation_replan_reused_native_preload" for event, _ in traces))

    def test_manual_next_selects_confirmed_preload_without_reloading(self) -> None:
        state = {
            "enabled": True,
            "active": "a",
            "lines": ["current-line", "next-line"],
            "player_index": {"a": 0, "b": 1},
            "player_loaded_keys": {"b": "31:401:/music/next.mp3"},
            "player_generation": {"b": 5},
            "generation": 6,
            "transitioning": False,
            "transition_starting": False,
        }
        pushes: list[tuple] = []
        selects: list[str] = []
        traces: list[tuple[str, dict]] = []

        def line_info(line: str) -> dict:
            if line == "current-line":
                return {"queue_id": 30, "track_id": 400, "file": "/music/current.mp3"}
            return {"queue_id": 31, "track_id": 401, "file": "/music/next.mp3"}

        namespace = {
            "time": time,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": state,
            "get_active_station_key": lambda: "db-Test.db",
            "_ab_line_info": line_info,
            "_ab_line_path": lambda line: line_info(line)["file"],
            "normalize_media_path": lambda path: str(path),
            "_ab_native_deck_matches_line": lambda *_args, **_kwargs: (
                True, "31:401:/music/next.mp3", 31, "31-ready-5"
            ),
            "_native_station_state": lambda _station: {
                "running": True,
                "deck_b_queue_id": 31,
                "deck_b_slot_token": "31-ready-5",
            },
            "_ab_push": lambda *args, **kwargs: pushes.append((args, kwargs)) or True,
            "_ab_select": lambda player: selects.append(player),
            "_ab_transition_to": lambda *_args, **_kwargs: None,
            "_manual_next_trace": lambda *_args, **_kwargs: None,
            "_preload_reuse_trace": lambda event, **payload: traces.append((event, payload)),
            "_ab_line_duration_and_fade": lambda _line: (180.0, 5.0),
            "_build_station_queue_plan": lambda _station: [],
            "_ab_schedule_inactive_preload_after_start": lambda *_args, **_kwargs: True,
        }
        self._exec_function("_ab_start_cueout_transition_now", namespace)
        ok = namespace["_ab_start_cueout_transition_now"](
            "db-Test.db",
            active="a",
            target="b",
            current_index=0,
            target_index=1,
            fade=0.0,
            generation=6,
            reason="manual_next_db_head_direct_handoff",
            manual_next_fast=True,
            manual_next_request_id="mn-test",
        )
        self.assertTrue(ok)
        self.assertEqual(pushes, [])
        self.assertEqual(selects, ["b"])
        self.assertTrue(any(event == "ab_hard_select_reused_native_preload" for event, _ in traces))


if __name__ == "__main__":
    unittest.main()
