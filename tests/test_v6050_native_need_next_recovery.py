from __future__ import annotations

import ast
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


class V6050NativeNeedNextRecoveryTests(unittest.TestCase):
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

    def test_need_next_recovers_from_plan_that_ends_on_the_active_track(self) -> None:
        state = {
            "enabled": True,
            "generation": 12,
            "lines": ["q17179"],
            "durations": [226.0],
            "fadeouts": [3.0],
            "player_index": {"b": 0, "a": 0},
            "next_index": 0,
        }
        queue_build_calls = []
        push_calls = []

        @contextmanager
        def station_runtime_context(_station_key):
            yield

        def line_info(line: str) -> dict:
            return {
                "q17179": {"queue_id": 17179, "file": "/music/THE_CATCH_-_25_years.mp3"},
                "q17180": {"queue_id": 17180, "file": "/music/next_id.mp3"},
                "q17181": {"queue_id": 17181, "file": "/music/after_id.mp3"},
            }.get(line, {})

        def find_index(lines, *, path="", queue_id=0, track_id=0):
            del track_id
            for index, line in enumerate(lines):
                info = line_info(line)
                if queue_id and int(info.get("queue_id") or 0) == int(queue_id):
                    return index
                if path and info.get("file") == path:
                    return index
            return -1

        def build_plan(station_key, **kwargs):
            queue_build_calls.append((station_key, dict(kwargs)))
            return ["q17180", "q17181"]

        class Logger:
            def exception(self, *_args, **_kwargs):
                raise AssertionError("need-next recovery must not raise")

        namespace = {
            "station_runtime_context": station_runtime_context,
            "normalize_media_path": lambda value: str(value or ""),
            "_native_station_state": lambda _station_key: {
                "active_deck": "B",
                "queue_id": 17179,
                "slot_token": "active-17179",
                "deck_a_queue_id": 17178,
                "deck_a_slot_token": "stale-17178",
                "deck_a_consumed": True,
                "deck_a_terminal": True,
                "native_audio_deck_a_queue_id": 17178,
                "native_audio_deck_a_slot_token": "stale-17178",
                "native_audio_deck_a_status": "eof",
            },
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": state,
            "_ab_find_line_index_by_identity": find_index,
            "_build_station_queue_plan": build_plan,
            "_ab_same_queue_identity": lambda left, right: int(line_info(left).get("queue_id") or 0) == int(line_info(right).get("queue_id") or 0),
            "_ab_line_duration_and_fade": lambda _line: (60.0, 3.0),
            "_ab_line_info": line_info,
            "_ab_signal_monitor_wake": lambda *_args, **_kwargs: None,
            "wake_autodj_worker": lambda: None,
            "_ab_record_player_loaded_identity": lambda *_args, **_kwargs: True,
            "_ab_push": lambda player, line, **kwargs: push_calls.append((player, line, dict(kwargs))) or True,
            "logger": Logger(),
        }
        
        for name in (
            "_ab_native_deck_runtime_phase",
            "_ab_native_deck_has_live_candidate",
            "_native_load_requested_next_track",
        ):
            self._exec_function(name, namespace)

        event = SimpleNamespace(
            station_key="MIXFM",
            deck="B",
            queue_id=17179,
            track_id=188,
            slot_token="active-17179",
            path="/music/THE_CATCH_-_25_years.mp3",
            payload={"active_deck": "B", "target_deck": "A"},
        )
        namespace["_native_load_requested_next_track"](event)

        self.assertEqual(len(queue_build_calls), 1)
        self.assertEqual(queue_build_calls[0][0], "MIXFM")
        self.assertEqual(queue_build_calls[0][1]["skip_queue_id"], 17179)
        self.assertEqual(queue_build_calls[0][1]["skip_track_id"], 0)
        self.assertEqual(queue_build_calls[0][1]["skip_path"], "")
        self.assertEqual(push_calls, [("a", "q17180", {"attempts": 4, "retry_delay": 0.05, "clear_slot": True, "reject_if_active_deck": True, "reject_if_playback_started": True})])
        self.assertEqual(state["lines"], ["q17179", "q17180", "q17181"])
        self.assertEqual(state["player_index"]["a"], 1)
        self.assertEqual(state["next_index"], 1)

    def test_need_next_no_longer_silently_returns_when_cached_plan_has_no_tail(self) -> None:
        helper = self._function_source("_native_load_requested_next_track")
        self.assertIn("_build_station_queue_plan(", helper)
        self.assertIn('reason="native_need_next_queue_empty"', helper)
        self.assertIn("logger.exception(", helper)
        self.assertNotIn('if next_index >= len(lines):\n                return', helper)


if __name__ == "__main__":
    unittest.main()
