from __future__ import annotations

import ast
import contextlib
import unittest
from pathlib import Path

from player import (
    ManualNextDependencies,
    ManualNextOrchestrator,
    PlayerHandoffDependencies,
    PlayerHandoffService,
)


class V6074SchedulerStreamInterruptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.player_source = (cls.root / "player" / "orchestration.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def _run_station_next_source_case(self, stream_source: bool) -> str:
        node = self.functions["_perform_station_next_action"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        observed: list[str] = []
        namespace = {
            "_resolve_station_id_to_db": lambda station: station,
            "os": __import__("os"),
            "_manual_next_read_reserved_plan": lambda station: (["target-line"], 44, 4),
            "_ab_line_info": lambda line: {"stream_source": bool(stream_source)},
            "_perform_player_manual_next_action": lambda station, action, source: (
                observed.append(source) or {"success": True}
            ),
        }
        exec(compile(module, str(self.root / "app.py"), "exec"), namespace)
        self.assertTrue(namespace["_perform_station_next_action"]("db-Test.db"))
        self.assertEqual(len(observed), 1)
        return observed[0]

    def test_scheduler_immediate_uses_scheduler_interrupt_source_for_current_targets(self) -> None:
        self.assertEqual(self._run_station_next_source_case(True), "scheduler_stream")
        self.assertEqual(self._run_station_next_source_case(False), "scheduler_stream")

    def test_scheduler_stream_handoff_uses_fade_without_changing_manual_next_mode(self) -> None:
        transitions: list[dict] = []
        state = {
            "enabled": True,
            "active": "a",
            "lines": ["live-line", "target-line"],
            "player_index": {"a": 0, "b": 1},
            "generation": 10,
        }

        def mutate(callback):
            return callback(state)

        deps = PlayerHandoffDependencies(
            get_active_station_key=lambda: "db-Test.db",
            build_queue_plan=lambda station: ["target-line"],
            line_info=lambda line: {
                "queue_id": 20 if line == "target-line" else 10,
                "track_id": 2 if line == "target-line" else 1,
            },
            read_player_state=lambda: dict(state),
            mutate_player_state=mutate,
            native_station_state=lambda station: {
                "active_deck": "A",
                "native_audio_probe_position_ms": 1000,
                "native_audio_probe_effective_end_ms": 12000,
            },
            reconcile_stale_transition=lambda station, native: False,
            trace_manual_next=lambda *args, **kwargs: None,
            resolve_native_live_player=lambda active, timeout: (
                "a",
                {"a_uri": "live-line"},
                {},
            ),
            same_queue_identity=lambda left, right: left == right,
            start_transition=lambda station, **kwargs: transitions.append(dict(kwargs)) or True,
            wake_autodj_worker=lambda: None,
            script_interrupt_fade_seconds=lambda station: 5.0,
        )
        service = PlayerHandoffService(deps)

        scheduler_result = service.direct_handoff(
            "db-Test.db",
            reserved_queue_lines=["target-line"],
            reservation_id="scheduler-1",
            scheduler_stream_interrupt=True,
        )
        self.assertTrue(scheduler_result and scheduler_result["success"])
        scheduler_transition = transitions[-1]
        self.assertFalse(scheduler_transition["manual_next_fast"])
        self.assertFalse(scheduler_transition["script_interrupt"])
        self.assertTrue(scheduler_transition["scheduler_stream_interrupt"])
        self.assertEqual(scheduler_transition["fade"], 5.0)
        self.assertEqual(
            scheduler_result["mode"],
            "scheduler_stream_interrupt_db_head_direct_handoff",
        )

        manual_result = service.direct_handoff(
            "db-Test.db",
            reserved_queue_lines=["target-line"],
            reservation_id="manual-1",
        )
        self.assertTrue(manual_result and manual_result["success"])
        manual_transition = transitions[-1]
        self.assertTrue(manual_transition["manual_next_fast"])
        self.assertFalse(manual_transition["script_interrupt"])
        self.assertFalse(manual_transition["scheduler_stream_interrupt"])
        self.assertEqual(manual_transition["fade"], 0.0)

    def test_scheduler_stream_source_sets_only_scheduler_stream_interrupt_flag(self) -> None:
        calls: list[dict] = []

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        service = ManualNextOrchestrator(
            ManualNextDependencies(
                resolve_station_key=lambda station: station,
                get_active_station_key=lambda: "db-Test.db",
                trace=lambda *args, **kwargs: None,
                station_runtime_context=runtime_context,
                read_reserved_plan=lambda station: (["target-line"], 55, 5),
                native_station_state=lambda station: {"running": True, "queue_id": 10},
                native_queue_contains_queue_id=lambda station, qid: qid == 55,
                perform_direct_handoff=lambda station, **kwargs: (
                    calls.append(dict(kwargs)) or {"success": True}
                ),
                signal_monitor_wake=lambda station, reason: None,
                wake_autodj_worker=lambda: None,
            )
        )
        service._wait_for_lifecycle = lambda station, qid: (True, "track_started_committed")
        result = service._execute_one(
            "db-Test.db",
            {"request_id": "scheduler-stream-1", "action": "next", "source": "scheduler_stream"},
        )
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].get("scheduler_stream_interrupt"))
        self.assertNotIn("script_interrupt", calls[0])

    def test_stream_target_is_ready_before_fade_and_existing_live_load_is_reused(self) -> None:
        transition = self._function_source("_ab_start_cueout_transition_now")
        self.assertIn("if scheduler_stream_interrupt and not hard_select:", transition)
        self.assertIn("require_ready=False", transition)
        self.assertIn("_ab_native_deck_has_live_candidate", transition)
        self.assertIn("timeout_sec=15.0", transition)
        self.assertIn("ab_scheduler_stream_interrupt_reused_live_preload", transition)
        self.assertIn("elif scheduler_stream_interrupt:", transition)
        self.assertIn("_ab_script_interrupt_to(", transition)

    def test_stable_manual_next_seek_and_script_guards_remain_present(self) -> None:
        transition = self._function_source("_ab_start_cueout_transition_now")
        self.assertIn(
            'if bool(st0.get("hard_handoff_armed")) and not bool(manual_next_fast):',
            transition,
        )
        self.assertIn(
            'if bool(st0.get("seek_pending")) and not bool(manual_next_fast):',
            transition,
        )
        self.assertIn("reject_if_playback_started=bool(manual_next_fast)", transition)
        self.assertIn("if script_interrupt and not hard_select:", transition)
        self.assertIn("ab_script_interrupt_reused_native_preload", transition)


if __name__ == "__main__":
    unittest.main()
