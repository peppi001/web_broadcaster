from __future__ import annotations

import ast
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


class _StateEngine:
    def __init__(self, states: list[dict]) -> None:
        self._states = list(states)
        self.calls = 0

    def get_state(self, *, station_key: str = "") -> dict:
        del station_key
        self.calls += 1
        if len(self._states) > 1:
            return dict(self._states.pop(0))
        return dict(self._states[0])


class NativeStartStopGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app_path = Path(__file__).resolve().parents[1] / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def _load_function(self, name: str, namespace: dict):
        node = self.functions[name]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace[name]

    def test_prebuffer_gate_waits_for_matching_nonempty_pcm_ring(self) -> None:
        engine = _StateEngine([
            {
                "running": True,
                "native_audio_deck_a_queue_id": 41,
                "native_audio_deck_a_prebuffer_ready": False,
                "native_audio_deck_a_ring_buffer_bytes": 0,
                "native_audio_deck_a_status": "prebuffering",
            },
            {
                "running": True,
                "native_audio_deck_a_queue_id": 99,
                "native_audio_deck_a_prebuffer_ready": True,
                "native_audio_deck_a_ring_buffer_bytes": 180000,
                "native_audio_deck_a_status": "ready",
            },
            {
                "running": True,
                "native_audio_deck_a_queue_id": 41,
                "native_audio_deck_a_prebuffer_ready": True,
                "native_audio_deck_a_ring_buffer_bytes": 180000,
                "native_audio_deck_a_status": "ready",
            },
        ])
        namespace = {
            "time": time,
            "_ab_line_info": lambda line: {"queue_id": 41, "line": line},
            "get_audio_engine": lambda: engine,
        }
        helper = self._load_function("_ab_wait_for_native_deck_prebuffer", namespace)

        ready, state, reason = helper(
            "a",
            "track-line",
            station_key="db-test.db",
            timeout_sec=0.25,
            poll_interval_sec=0.01,
        )

        self.assertTrue(ready)
        self.assertEqual(reason, "ready")
        self.assertEqual(state["native_audio_deck_a_queue_id"], 41)
        self.assertGreaterEqual(engine.calls, 3)

    def test_prebuffer_gate_aborts_when_engine_stops(self) -> None:
        engine = _StateEngine([{"running": False}])
        namespace = {
            "time": time,
            "_ab_line_info": lambda line: {"queue_id": 7, "line": line},
            "get_audio_engine": lambda: engine,
        }
        helper = self._load_function("_ab_wait_for_native_deck_prebuffer", namespace)
        ready, _state, reason = helper("a", "track-line", station_key="db-test.db")
        self.assertFalse(ready)
        self.assertEqual(reason, "engine_stopped")

    def test_bootstrap_refuses_to_load_when_station_is_stopped(self) -> None:
        import threading

        forbidden_calls: list[str] = []
        namespace = {
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": {"enabled": False, "stopping": False},
            "get_active_station_key": lambda: "db-test.db",
            "_native_station_state": lambda station_key: {"station_key": station_key, "running": False},
            "_build_station_queue_plan": lambda station_key: forbidden_calls.append("plan") or ["track"],
            "_ab_push": lambda *a, **k: forbidden_calls.append("load") or True,
        }
        bootstrap = self._load_function("_ab_bootstrap_from_queue_plan", namespace)

        self.assertFalse(bootstrap(["track"], station_key="db-test.db"))
        self.assertEqual(forbidden_calls, [])

    def test_offair_replan_returns_before_playlist_or_deck_bootstrap(self) -> None:
        forbidden_calls: list[str] = []
        namespace = {
            "_ab_manual_next_guard_remaining": lambda: 0.0,
            "_ab_schedule_deferred_replan": lambda *a, **k: forbidden_calls.append("deferred"),
            "_ab_begin_replan_serial": lambda reason: 12,
            "get_active_station_key": lambda: "db-test.db",
            "_native_station_state": lambda station_key: {"station_key": station_key, "running": False},
            "_build_station_queue_plan": lambda station_key: forbidden_calls.append("plan") or [],
            "_ab_bootstrap_from_queue_plan": lambda *a, **k: forbidden_calls.append("bootstrap") or True,
        }
        replan = self._load_function("_ab_replan_after_queue_mutation", namespace)

        self.assertTrue(replan("autodj_refill"))
        self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
