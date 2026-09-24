from __future__ import annotations

import ast
import contextlib
import unittest
from pathlib import Path

from player import ManualNextDependencies, ManualNextOrchestrator


class V6075SchedulerImmediateFadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def test_scheduler_immediate_uses_fade_source_for_local_and_url_targets(self) -> None:
        node = self.functions["_perform_station_next_action"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        observed: list[str] = []
        namespace = {
            "_resolve_station_id_to_db": lambda station: station,
            "os": __import__("os"),
            "_perform_player_manual_next_action": lambda station, action, source: (
                observed.append(source) or {"success": True}
            ),
        }
        exec(compile(module, str(self.root / "app.py"), "exec"), namespace)
        self.assertTrue(namespace["_perform_station_next_action"]("db-Test.db"))
        self.assertEqual(observed, ["scheduler_stream"])

    def test_scheduler_immediate_multi_file_insert_moves_all_items_then_fades_once(self) -> None:
        node = self.functions["_apply_scheduler_rule_queue_action_for_station"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        calls: list[tuple[str, object]] = []

        class RuntimeContext:
            def __enter__(self):
                return None
            def __exit__(self, exc_type, exc, tb):
                return False

        namespace = {
            "_enqueue_track_ids_return_queue_ids_for_station": lambda station, tids, priority: (
                calls.append(("enqueue", (list(tids), priority))) or [101, 102, 103]
            ),
            "_mark_queue_items_origin_for_station": lambda station, qids, origin: (
                calls.append(("origin", (list(qids), origin))) or True
            ),
            "_get_playback_repository": lambda: type("Repo", (), {"remove_queue_items": lambda *a, **k: None})(),
            "_move_station_queue_ids_to_front": lambda station, qids: (
                calls.append(("move", list(qids))) or True
            ),
            "wake_autodj_worker": lambda: calls.append(("wake", None)),
            "station_runtime_context": lambda station: RuntimeContext(),
            "_ab_schedule_async_replan": lambda reason: calls.append(("replan", reason)),
            "_perform_station_next_action": lambda station: (
                calls.append(("fade_next", station)) or True
            ),
            "time": type("Time", (), {"sleep": staticmethod(lambda seconds: calls.append(("sleep", seconds)))})(),
            "_enqueue_track_ids_for_station": lambda station, tids, priority: True,
        }
        exec(compile(module, str(self.root / "app.py"), "exec"), namespace)
        ok = namespace["_apply_scheduler_rule_queue_action_for_station"](
            "db-Test.db", [11, 12, 13], "immediate"
        )
        self.assertTrue(ok)
        self.assertIn(("move", [101, 102, 103]), calls)
        self.assertEqual(sum(1 for name, _value in calls if name == "fade_next"), 1)
        self.assertLess(
            next(i for i, item in enumerate(calls) if item[0] == "move"),
            next(i for i, item in enumerate(calls) if item[0] == "fade_next"),
        )

    def test_scheduler_immediate_target_is_not_restricted_to_stream_source(self) -> None:
        transition = self._function_source("_ab_start_cueout_transition_now")
        self.assertIn("if scheduler_stream_interrupt and not hard_select:", transition)
        self.assertNotIn('if not bool(stream_target_info.get("stream_source")):', transition)
        self.assertIn("timeout_sec=15.0", transition)
        self.assertIn("elif scheduler_stream_interrupt:", transition)
        self.assertIn("_ab_script_interrupt_to(", transition)

    def test_scheduler_source_does_not_change_manual_next_or_script_flags(self) -> None:
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

        scheduler_result = service._execute_one(
            "db-Test.db",
            {"request_id": "scheduler-immediate-1", "action": "next", "source": "scheduler_stream"},
        )
        self.assertTrue(scheduler_result["success"])
        self.assertTrue(calls[-1].get("scheduler_stream_interrupt"))
        self.assertNotIn("script_interrupt", calls[-1])

        manual_result = service._execute_one(
            "db-Test.db",
            {"request_id": "manual-1", "action": "next", "source": "ui"},
        )
        self.assertTrue(manual_result["success"])
        self.assertNotIn("scheduler_stream_interrupt", calls[-1])
        self.assertNotIn("script_interrupt", calls[-1])

        script_result = service._execute_one(
            "db-Test.db",
            {"request_id": "script-1", "action": "next", "source": "script"},
        )
        self.assertTrue(script_result["success"])
        self.assertTrue(calls[-1].get("script_interrupt"))
        self.assertNotIn("scheduler_stream_interrupt", calls[-1])


if __name__ == "__main__":
    unittest.main()
