from __future__ import annotations

import ast
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


class V6056NeedNextCandidateStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node for node in cls.tree.body if isinstance(node, ast.FunctionDef)
        }

    def _exec_functions(self, names: list[str], namespace: dict) -> None:
        module = ast.Module(body=[self.functions[name] for name in names], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)

    @staticmethod
    def _line_info(line: str) -> dict:
        return {
            "short_id": {"queue_id": 5464, "track_id": 921, "file": "/music/id3.mp3"},
            "candidate_a": {"queue_id": 5463, "track_id": 172, "file": "/music/a.mp3"},
            "candidate_b": {"queue_id": 5465, "track_id": 731, "file": "/music/b.mp3"},
        }.get(line, {})

    def test_live_candidate_blocks_need_next_supersede_even_when_plan_changed(self) -> None:
        player_state = {
            "enabled": True,
            "generation": 1,
            "lines": ["short_id", "candidate_b"],
            "durations": [5.0, 275.0],
            "fadeouts": [0.0, 5.0],
            "player_index": {"a": 0, "b": 1},
            "next_index": 1,
        }
        push_calls = []

        @contextmanager
        def station_runtime_context(_station_key):
            yield

        state = {
            "running": True,
            "active_deck": "A",
            "queue_id": 5464,
            "slot_token": "id-token",
            # Deck B already owns a different, still-live analysis candidate.
            "deck_b_queue_id": 5463,
            "deck_b_slot_token": "candidate-a-token",
            "deck_b_consumed": False,
            "deck_b_terminal": False,
            "native_deck_b_analysis_ready": False,
            "native_deck_b_analysis_failed": False,
            "native_audio_deck_b_status": "prebuffering",
            "native_audio_deck_b_prebuffer_ready": False,
            "native_audio_deck_b_ring_buffer_bytes": 0,
        }

        def find_index(lines, *, path="", queue_id=0, track_id=0):
            del track_id
            for index, line in enumerate(lines):
                info = self._line_info(line)
                if queue_id and int(info.get("queue_id") or 0) == int(queue_id):
                    return index
                if path and info.get("file") == path:
                    return index
            return -1

        namespace = {
            "station_runtime_context": station_runtime_context,
            "normalize_media_path": lambda value: str(value or ""),
            "_native_station_state": lambda _station_key: dict(state),
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": player_state,
            "_ab_find_line_index_by_identity": find_index,
            "_build_station_queue_plan": lambda *_args, **_kwargs: ["candidate_b"],
            "_ab_same_queue_identity": lambda left, right: self._line_info(left).get("queue_id") == self._line_info(right).get("queue_id"),
            "_ab_line_duration_and_fade": lambda _line: (10.0, 3.0),
            "_ab_line_info": self._line_info,
            "_ab_loaded_identity_key": lambda line: f"key:{line}",
            "_ab_signal_monitor_wake": lambda *_args, **_kwargs: None,
            "wake_autodj_worker": lambda: None,
            "_ab_record_player_loaded_identity": lambda *_args, **_kwargs: True,
            "_ab_push": lambda *args, **kwargs: push_calls.append((args, kwargs)) or True,
            "logger": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
        }
        self._exec_functions(
            ["_ab_native_deck_runtime_phase", "_ab_native_deck_matches_line", "_ab_native_deck_has_live_candidate", "_native_load_requested_next_track"],
            namespace,
        )
        event = SimpleNamespace(
            station_key="db-Max_FM.db",
            deck="A",
            queue_id=5464,
            track_id=921,
            slot_token="id-token",
            path="/music/id3.mp3",
            payload={"active_deck": "A", "target_deck": "B"},
        )
        namespace["_native_load_requested_next_track"](event)
        self.assertEqual(push_calls, [])

    def test_stale_need_next_cannot_load_after_active_deck_changes(self) -> None:
        player_state = {
            "enabled": True,
            "generation": 1,
            "lines": ["short_id", "candidate_b"],
            "durations": [5.0, 275.0],
            "fadeouts": [0.0, 5.0],
            "player_index": {"a": 0},
            "next_index": 1,
        }
        states = [
            {
                "running": True,
                "active_deck": "A",
                "queue_id": 5464,
                "slot_token": "id-token",
                "deck_b_queue_id": 0,
                "deck_b_slot_token": "",
            },
            # The hard handoff happened while Python was preparing the queue plan.
            {
                "running": True,
                "active_deck": "B",
                "queue_id": 5472,
                "slot_token": "time-token",
                "deck_a_queue_id": 5464,
                "deck_a_slot_token": "id-token",
            },
        ]
        calls = {"state": 0}
        push_calls = []

        @contextmanager
        def station_runtime_context(_station_key):
            yield

        def native_state(_station_key):
            index = min(calls["state"], len(states) - 1)
            calls["state"] += 1
            return dict(states[index])

        def find_index(lines, *, path="", queue_id=0, track_id=0):
            del track_id
            for index, line in enumerate(lines):
                info = self._line_info(line)
                if queue_id and int(info.get("queue_id") or 0) == int(queue_id):
                    return index
                if path and info.get("file") == path:
                    return index
            return -1

        namespace = {
            "station_runtime_context": station_runtime_context,
            "normalize_media_path": lambda value: str(value or ""),
            "_native_station_state": native_state,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": player_state,
            "_ab_find_line_index_by_identity": find_index,
            "_build_station_queue_plan": lambda *_args, **_kwargs: ["candidate_b"],
            "_ab_same_queue_identity": lambda left, right: self._line_info(left).get("queue_id") == self._line_info(right).get("queue_id"),
            "_ab_line_duration_and_fade": lambda _line: (10.0, 3.0),
            "_ab_line_info": self._line_info,
            "_ab_loaded_identity_key": lambda line: f"key:{line}",
            "_ab_signal_monitor_wake": lambda *_args, **_kwargs: None,
            "wake_autodj_worker": lambda: None,
            "_ab_record_player_loaded_identity": lambda *_args, **_kwargs: True,
            "_ab_push": lambda *args, **kwargs: push_calls.append((args, kwargs)) or True,
            "logger": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
        }
        self._exec_functions(
            ["_ab_native_deck_runtime_phase", "_ab_native_deck_matches_line", "_ab_native_deck_has_live_candidate", "_native_load_requested_next_track"],
            namespace,
        )
        event = SimpleNamespace(
            station_key="db-Max_FM.db",
            deck="A",
            queue_id=5464,
            track_id=921,
            slot_token="id-token",
            path="/music/id3.mp3",
            payload={"active_deck": "A", "target_deck": "B"},
        )
        namespace["_native_load_requested_next_track"](event)
        self.assertGreaterEqual(calls["state"], 2)
        self.assertEqual(push_calls, [])


if __name__ == "__main__":
    unittest.main()
