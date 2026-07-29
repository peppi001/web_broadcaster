from __future__ import annotations

import ast
import contextlib
import os
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace

from player import ManualNextDependencies, ManualNextOrchestrator


class ManualNextSerializationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.lifecycle_source = (cls.root / 'audio_engine' / 'lifecycle.py').read_text(encoding='utf-8')
        cls.player_source = (cls.root / 'player' / 'orchestration.py').read_text(encoding='utf-8')
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _module_for(self, *names: str) -> ast.Module:
        module = ast.Module(body=[self.functions[name] for name in names], type_ignores=[])
        ast.fix_missing_locations(module)
        return module

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_enqueue_returns_immediately_and_worker_serializes_requests(self) -> None:
        first_started = threading.Event()
        second_started = threading.Event()
        release_first = threading.Event()
        release_second = threading.Event()
        calls: list[str] = []
        active = 0
        max_active = 0
        call_lock = threading.Lock()

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        deps = ManualNextDependencies(
            resolve_station_key=lambda station: station or "db-AirFM.db",
            get_active_station_key=lambda: "db-AirFM.db",
            trace=lambda *args, **kwargs: None,
            station_runtime_context=runtime_context,
            read_reserved_plan=lambda station: (["line"], 1, 1),
            native_station_state=lambda station: {"running": True, "queue_id": 0},
            native_queue_contains_queue_id=lambda station, qid: True,
            perform_direct_handoff=lambda *args, **kwargs: {"success": True},
            signal_monitor_wake=lambda station, reason: None,
            wake_autodj_worker=lambda: None,
        )
        service = ManualNextOrchestrator(deps)

        def execute_one(_station: str, request: dict) -> dict:
            nonlocal active, max_active
            with call_lock:
                active += 1
                max_active = max(max_active, active)
                calls.append(str(request.get("request_id") or ""))
                call_no = len(calls)
            if call_no == 1:
                first_started.set()
                self.assertTrue(release_first.wait(2.0))
            else:
                second_started.set()
                self.assertTrue(release_second.wait(2.0))
            with call_lock:
                active -= 1
            return {
                "success": True,
                "request_id": str(request.get("request_id") or ""),
                "target_queue_id": 100 + call_no,
                "lifecycle_reason": "track_started_committed",
            }

        service._execute_one = execute_one
        first = service.enqueue("db-AirFM.db", action="next")
        self.assertTrue(first["accepted"])
        self.assertTrue(first_started.wait(1.0))
        second = service.enqueue("db-AirFM.db", action="next")
        self.assertTrue(second["accepted"])
        self.assertEqual(second["queued_position"], 2)
        time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        release_first.set()
        self.assertTrue(second_started.wait(1.0))
        release_second.set()
        deadline = time.monotonic() + 2.0
        while service.public_status("db-AirFM.db")["in_progress"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(service.public_status("db-AirFM.db")["in_progress"])
        self.assertEqual(max_active, 1)
        self.assertNotEqual(first["request_id"], second["request_id"])

    def test_execute_waits_until_db_head_differs_from_current_native_queue_id(self) -> None:
        plan_calls = 0
        direct_calls: list[list[str]] = []
        native_calls = 0

        def read_plan(_station: str):
            nonlocal plan_calls
            plan_calls += 1
            if plan_calls == 1:
                return ["active-head-line"], 700, 70
            return ["next-head-line"], 701, 71

        def native_state(_station: str):
            nonlocal native_calls
            native_calls += 1
            return {"queue_id": 700 if native_calls == 1 else 999, "running": True}

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        def direct(_station: str, *, reserved_queue_lines=None, reservation_id=""):
            direct_calls.append(list(reserved_queue_lines or []))
            return {"success": True, "target_player": "b", "reservation_id": reservation_id}

        service = ManualNextOrchestrator(
            ManualNextDependencies(
                resolve_station_key=lambda station: station,
                get_active_station_key=lambda: "db-AirFM.db",
                trace=lambda *args, **kwargs: None,
                station_runtime_context=runtime_context,
                read_reserved_plan=read_plan,
                native_station_state=native_state,
                native_queue_contains_queue_id=lambda station, qid: qid == 701,
                perform_direct_handoff=direct,
                signal_monitor_wake=lambda station, reason: None,
                wake_autodj_worker=lambda: None,
            )
        )
        service._wait_for_lifecycle = lambda station, qid: (True, "track_started_committed")
        result = service._execute_one(
            "db-AirFM.db", {"request_id": "mn-test", "action": "next"}
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["target_queue_id"], 701)
        self.assertGreaterEqual(plan_calls, 2)
        self.assertEqual(direct_calls, [["next-head-line"]])

    def test_automatic_need_next_cannot_overwrite_manual_reservation(self) -> None:
        guarded = self._function_source("_native_load_requested_next_track_guarded")
        self.assertIn("_manual_next_deck_plan_lock", guarded)
        self.assertIn("_manual_next_is_inflight", guarded)
        self.assertIn("_native_load_requested_next_track(event)", guarded)

    def test_track_started_is_deduplicated_by_queue_id_not_only_slot_token(self) -> None:
        lifecycle_source = self.lifecycle_source
        self.assertIn("_track_queue_id_pending", lifecycle_source)
        self.assertIn("_track_queue_id_done", lifecycle_source)
        self.assertIn("self._track_queue_id_done[queue_identity]", lifecycle_source)

    def test_manual_next_trace_publishes_request_identity_to_protocol_event(self) -> None:
        module = self._module_for("_manual_next_trace")
        published: list[dict] = []
        def publish(event: str, **kwargs):
            published.append({"event": event, **kwargs})

        namespace = {
            "os": os,
            "get_active_station_key": lambda: "db-AirFM.db",
            "_publish_audio_engine_event": publish,
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        namespace["_manual_next_trace"](
            "manual_next_target_reserved",
            "db-AirFM.db",
            "mn-9-123",
            target_queue_id=701,
            target_track_id=71,
            target_deck="B",
        )
        self.assertEqual(len(published), 1)
        record = published[0]
        self.assertEqual(record["event"], "manual_next_target_reserved")
        self.assertEqual(record["station_key"], "db-AirFM.db")
        self.assertEqual(record["queue_id"], 701)
        self.assertEqual(record["track_id"], 71)
        self.assertEqual(record["deck"], "B")
        self.assertEqual(record["payload"]["manual_next_request_id"], "mn-9-123")

    def test_manual_next_protocol_trace_covers_http_to_lifecycle_result(self) -> None:
        combined = self.source + self.player_source
        for event_name in (
            "manual_next_http_received",
            "manual_next_serialized_accepted",
            "manual_next_worker_started",
            "manual_next_target_reserved",
            "manual_next_select_dispatched",
            "manual_next_track_started",
            "manual_next_serialized_result",
        ):
            self.assertIn(event_name, combined)
        for reason in (
            "station_not_running",
            "queue_empty",
            "manual_next_queue_full",
            "active_head_timeout",
            "reserved_queue_head_missing",
            "handoff_failed",
            "track_started_commit_timeout",
            "worker_exception",
        ):
            self.assertIn(reason, combined)

    def test_native_transition_finished_clears_latched_python_transition_state(self) -> None:
        module = self._module_for(
            "_ab_clear_transition_runtime_fields_locked",
            "_ab_reconcile_native_transition_finished",
        )
        state = {
            "enabled": True,
            "active": "b",
            "lines": ["queue-15657"],
            "player_index": {"b": 0},
            "current_index": 0,
            "transitioning": True,
            "transition_starting": False,
            "transition_started_at": 123.0,
            "transition_duration": 5.0,
            "transition_target": "b",
            "transition_from": "a",
            "transition_from_line": "queue-15656",
            "transition_to_line": "queue-15657",
            "pending_cueout_transition": True,
            "pending_cueout_deadline": 456.0,
            "pending_cueout_token": 9,
        }
        @contextlib.contextmanager
        def station_runtime_context(_station: str):
            yield

        namespace = {
            "_AB_PLAYER_STATE": state,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "station_runtime_context": station_runtime_context,
            "_ab_line_info": lambda line: {"queue_id": 15657, "slot_token": "slot-57"} if line else {},
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        event = SimpleNamespace(
            station_key="db-AirFM.db",
            event="transition_finished",
            deck="B",
            queue_id=15657,
            slot_token="slot-57",
            payload={"from_deck": "A", "source": "native_timing_transition"},
        )
        self.assertTrue(namespace["_ab_reconcile_native_transition_finished"](event))
        self.assertFalse(state["transitioning"])
        self.assertFalse(state["transition_starting"])
        self.assertEqual(state["transition_started_at"], 0.0)
        self.assertEqual(state["transition_duration"], 0.0)
        self.assertEqual(state["transition_target"], "")
        self.assertEqual(state["transition_from"], "")
        self.assertEqual(state["transition_from_line"], "")
        self.assertEqual(state["transition_to_line"], "")
        self.assertFalse(state["pending_cueout_transition"])
        self.assertEqual(state["active"], "b")

    def test_manual_next_reconciles_stale_python_transition_from_native_idle_state(self) -> None:
        module = self._module_for(
            "_ab_clear_transition_runtime_fields_locked",
            "_ab_reconcile_stale_transition_before_manual_next",
        )
        state = {
            "active": "b",
            "current_index": 0,
            "player_index": {"b": 0},
            "transitioning": True,
            "transition_starting": False,
            "transition_started_at": 100.0,
            "transition_duration": 5.0,
            "transition_target": "b",
            "transition_from": "a",
            "transition_from_line": "old",
            "transition_to_line": "new",
            "pending_cueout_transition": False,
            "pending_cueout_deadline": 0.0,
            "pending_cueout_token": 0,
        }

        @contextlib.contextmanager
        def station_runtime_context(_station: str):
            yield

        namespace = {
            "_AB_PLAYER_STATE": state,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "station_runtime_context": station_runtime_context,
            "get_active_station_key": lambda: "db-AirFM.db",
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        reconciled = namespace["_ab_reconcile_stale_transition_before_manual_next"](
            "db-AirFM.db",
            {
                "running": True,
                "transitioning": False,
                "active_deck": "B",
                "queue_id": 15657,
                "slot_token": "slot-57",
            },
        )
        self.assertTrue(reconciled)
        self.assertFalse(state["transitioning"])
        self.assertFalse(state["transition_starting"])
        self.assertEqual(state["active"], "b")

    def test_manual_next_never_clears_a_real_native_transition(self) -> None:
        module = self._module_for(
            "_ab_clear_transition_runtime_fields_locked",
            "_ab_reconcile_stale_transition_before_manual_next",
        )
        state = {"active": "b", "transitioning": True, "transition_starting": False}

        @contextlib.contextmanager
        def station_runtime_context(_station: str):
            yield

        namespace = {
            "_AB_PLAYER_STATE": state,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "station_runtime_context": station_runtime_context,
            "get_active_station_key": lambda: "db-AirFM.db",
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        reconciled = namespace["_ab_reconcile_stale_transition_before_manual_next"](
            "db-AirFM.db",
            {"running": True, "transitioning": True, "active_deck": "B"},
        )
        self.assertFalse(reconciled)
        self.assertTrue(state["transitioning"])

    def test_transition_finished_callback_and_manual_next_safety_net_are_wired(self) -> None:
        lifecycle_source = self.lifecycle_source
        factory = self._function_source("_get_native_lifecycle_coordinator")
        handoff_factory = self._function_source("_get_player_handoff_service")
        self.assertIn('event_name == "transition_finished"', lifecycle_source)
        self.assertIn("_reconcile_transition_finished(event)", lifecycle_source)
        self.assertIn("reconcile_transition_finished=_ab_reconcile_native_transition_finished", factory)
        self.assertIn("reconcile_stale_transition", self.player_source)
        self.assertIn("manual_next_stale_transition_reconciled", self.player_source)
        self.assertIn("_ab_reconcile_stale_transition_before_manual_next", handoff_factory)

    def test_studio_next_button_tracks_backend_pending_state(self) -> None:
        js = (self.root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
        self.assertIn("setStudioManualNextPending", js)
        self.assertIn("manualNext.in_progress", js)
        self.assertIn("nextButton.disabled = studioManualNextPending", js)
        self.assertIn("manual_next_serialized_accepted", self.player_source)

if __name__ == "__main__":
    unittest.main()
