from __future__ import annotations

import ast
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


class V6055ShortTrackTerminalRecoveryTests(unittest.TestCase):
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
        cls.engine_source = (cls.root / "native_engine" / "src" / "engine.c").read_text(encoding="utf-8")

    def _exec_functions(self, names: list[str], namespace: dict) -> None:
        module = ast.Module(body=[self.functions[name] for name in names], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)

    @staticmethod
    def _line_info(line: str) -> dict:
        return {
            "sailor": {"queue_id": 3906, "track_id": 92, "file": "/music/SAILOR.mp3"},
            "short_id": {"queue_id": 3913, "track_id": 1, "file": "/music/short_id.mp3"},
            "next_song": {"queue_id": 3914, "track_id": 2, "file": "/music/next_song.mp3"},
            "after": {"queue_id": 3915, "track_id": 3, "file": "/music/after.mp3"},
        }.get(line, {})

    def test_native_state_exports_terminal_and_consumed_per_deck(self) -> None:
        self.assertIn('\\"deck_a_consumed\\":%s,\\"deck_a_terminal\\":%s', self.engine_source)
        self.assertIn('\\"deck_b_consumed\\":%s,\\"deck_b_terminal\\":%s', self.engine_source)
        self.assertIn('deck_a.consumed ? "true" : "false"', self.engine_source)
        self.assertIn('deck_b.terminal ? "true" : "false"', self.engine_source)

    def test_terminal_same_identity_is_not_a_reusable_preload(self) -> None:
        namespace = {
            "_ab_line_info": self._line_info,
            "_ab_loaded_identity_key": lambda line: f"key:{line}",
        }
        self._exec_functions(["_ab_native_deck_matches_line"], namespace)
        state = {
            "deck_b_queue_id": 3913,
            "deck_b_slot_token": "id-token",
            "deck_b_consumed": True,
            "deck_b_terminal": True,
            "native_audio_deck_b_status": "eof",
            "native_deck_b_analysis_ready": True,
            "native_audio_deck_b_prebuffer_ready": False,
            "native_audio_deck_b_ring_buffer_bytes": 0,
        }
        match = namespace["_ab_native_deck_matches_line"](state, "b", "short_id")
        ready = namespace["_ab_native_deck_matches_line"](state, "b", "short_id", require_ready=True)
        self.assertFalse(match[0])
        self.assertFalse(ready[0])

    def test_need_next_skips_terminal_short_id_and_loads_real_next_row(self) -> None:
        player_state = {
            "enabled": True,
            "generation": 28,
            "lines": ["sailor", "short_id", "next_song", "after"],
            "durations": [159.7, 1.1, 180.0, 190.0],
            "fadeouts": [5.0, 0.0, 3.0, 3.0],
            "player_index": {"a": 0, "b": 1},
            "next_index": 1,
        }
        push_calls = []
        build_calls = []

        @contextmanager
        def station_runtime_context(_station_key):
            yield

        def find_index(lines, *, path="", queue_id=0, track_id=0):
            del track_id
            for index, line in enumerate(lines):
                info = self._line_info(line)
                if queue_id and int(info.get("queue_id") or 0) == int(queue_id):
                    return index
                if path and info.get("file") == path:
                    return index
            return -1

        def build_plan(station_key, **kwargs):
            build_calls.append((station_key, kwargs))
            return ["short_id", "next_song", "after"]

        namespace = {
            "station_runtime_context": station_runtime_context,
            "normalize_media_path": lambda value: str(value or ""),
            "_native_station_state": lambda _station_key: {
                "running": True,
                "active_deck": "A",
                "queue_id": 3906,
                "slot_token": "sailor-token",
                "deck_b_queue_id": 3913,
                "deck_b_slot_token": "id-token",
                "deck_b_consumed": True,
                "deck_b_terminal": True,
                "native_audio_deck_b_status": "eof",
                "native_deck_b_analysis_ready": True,
                "native_deck_b_analysis_failed": False,
                "native_audio_deck_b_prebuffer_ready": False,
                "native_audio_deck_b_ring_buffer_bytes": 0,
            },
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": player_state,
            "_ab_find_line_index_by_identity": find_index,
            "_build_station_queue_plan": build_plan,
            "_ab_same_queue_identity": lambda left, right: int(self._line_info(left).get("queue_id") or 0) == int(self._line_info(right).get("queue_id") or 0),
            "_ab_line_duration_and_fade": lambda line: (float(self._line_info(line).get("queue_id") or 0), 3.0),
            "_ab_line_info": self._line_info,
            "_ab_loaded_identity_key": lambda line: f"key:{line}",
            "_ab_signal_monitor_wake": lambda *_args, **_kwargs: None,
            "wake_autodj_worker": lambda: None,
            "_ab_record_player_loaded_identity": lambda *_args, **_kwargs: True,
            "_ab_push": lambda player, line, **kwargs: push_calls.append((player, line, kwargs)) or True,
            "logger": SimpleNamespace(exception=lambda *_args, **_kwargs: None),
        }
        self._exec_functions(["_ab_native_deck_runtime_phase", "_ab_native_deck_matches_line", "_ab_native_deck_has_live_candidate", "_native_load_requested_next_track"], namespace)
        event = SimpleNamespace(
            station_key="db-Tel_Star.db",
            deck="A",
            queue_id=3906,
            track_id=92,
            slot_token="sailor-token",
            path="/music/SAILOR.mp3",
            payload={"active_deck": "A", "target_deck": "B"},
        )
        namespace["_native_load_requested_next_track"](event)

        self.assertEqual(len(build_calls), 1)
        self.assertEqual(push_calls[0][0], "b")
        self.assertEqual(push_calls[0][1], "next_song")
        self.assertEqual(player_state["lines"], ["sailor", "next_song", "after"])
        self.assertEqual(player_state["next_index"], 1)
        self.assertEqual(player_state["player_index"]["b"], 1)

    def test_late_short_track_started_commits_without_rewinding_now_playing(self) -> None:
        committed = []
        now_playing = []
        rebuilt = []
        manual = []
        queue_presence = {3913: True}

        @contextmanager
        def station_runtime_context(_station_key):
            yield

        def commit(station_key, line, **_kwargs):
            committed.append((station_key, line))
            queue_presence[3913] = False
            return True

        namespace = {
            "station_runtime_context": station_runtime_context,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": {"enabled": True},
            "_native_resolve_track_started_line": lambda _event: "short_id",
            "_native_queue_contains_queue_id": lambda _station, qid: bool(queue_presence.get(int(qid), False)),
            "_ab_commit_started_track": commit,
            "_manual_next_mark_lifecycle": lambda *args, **kwargs: manual.append((args, kwargs)),
            "_publish_ui_queue_history_changed": lambda *_args, **_kwargs: None,
            "autodj_fill_queue_once": lambda **_kwargs: False,
            "_native_station_state": lambda _station: {
                "running": True,
                "active_deck": "A",
                "queue_id": 3906,
                "slot_token": "sailor-token",
            },
            "start_autodj_thread": lambda *_args, **_kwargs: None,
            "invalidate_audio_engine_status_cache": lambda: None,
            "wake_autodj_worker": lambda: None,
            "_ab_apply_now_playing_line": lambda *args, **kwargs: now_playing.append((args, kwargs)),
            "_native_rebuild_plan_after_track_started": lambda *args, **kwargs: rebuilt.append((args, kwargs)) or (False, "b"),
            "_ab_line_info": self._line_info,
            "_publish_ui_event": lambda *_args, **_kwargs: None,
        }
        self._exec_functions(["_process_native_track_started_event"], namespace)
        event = SimpleNamespace(
            station_key="db-Tel_Star.db",
            deck="B",
            queue_id=3913,
            track_id=1,
            slot_token="id-token",
            path="/music/short_id.mp3",
            payload={"source": "native_hard_handoff_boundary"},
        )
        result = namespace["_process_native_track_started_event"](event)
        self.assertTrue(result)
        self.assertEqual(committed, [("db-Tel_Star.db", "short_id")])
        self.assertEqual(now_playing, [])
        self.assertEqual(rebuilt, [])
        self.assertTrue(manual)

    def test_manual_hard_select_requires_ready_native_preload(self) -> None:
        function = ast.get_source_segment(self.source, self.functions["_ab_start_cueout_transition_now"]) or ""
        self.assertIn("require_ready=True", function)


if __name__ == "__main__":
    unittest.main()
