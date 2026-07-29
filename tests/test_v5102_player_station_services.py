from __future__ import annotations

import contextlib
import threading
import time
import unittest
from pathlib import Path

from player import (
    ManualNextDependencies,
    ManualNextOrchestrator,
    PlayerHandoffDependencies,
    PlayerHandoffService,
)
from station import StationService, StationServiceDependencies


class V5102PlayerStationServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.player_source = (cls.root / "player" / "orchestration.py").read_text(encoding="utf-8")
        cls.station_source = (cls.root / "station" / "service.py").read_text(encoding="utf-8")

    def test_app_uses_thin_player_and_station_facades(self) -> None:
        self.assertIn("ManualNextOrchestrator", self.app_source)
        self.assertIn("PlayerHandoffService", self.app_source)
        self.assertIn("StationService", self.app_source)
        self.assertNotIn("_MANUAL_NEXT_STATES", self.app_source)
        self.assertNotIn("_MANUAL_NEXT_WORKERS", self.app_source)
        self.assertIn("_get_manual_next_orchestrator().perform_action", self.app_source)
        self.assertIn("_get_station_service().start()", self.app_source)
        self.assertIn("_get_station_service().stop()", self.app_source)

    def test_player_modules_have_no_flask_sqlite_or_app_import(self) -> None:
        for source in (self.player_source, self.station_source):
            self.assertNotIn("from flask", source)
            self.assertNotIn("import flask", source)
            self.assertNotIn("sqlite3", source)
            self.assertNotIn("import app", source)

    def test_handoff_reuses_reserved_authoritative_queue_head(self) -> None:
        state = {
            "enabled": True,
            "active": "a",
            "generation": 7,
            "lines": ["active"],
            "player_index": {"a": 0},
        }
        transition_calls: list[dict] = []

        def mutate(callback):
            return callback(state)

        service = PlayerHandoffService(
            PlayerHandoffDependencies(
                get_active_station_key=lambda: "station.db",
                build_queue_plan=lambda station: ["target", "tail"],
                line_info=lambda line: {
                    "queue_id": 22 if line == "target" else 23,
                    "track_id": 122 if line == "target" else 123,
                },
                read_player_state=lambda: dict(state),
                mutate_player_state=mutate,
                native_station_state=lambda station: {
                    "running": True,
                    "active_deck": "A",
                    "queue_id": 21,
                },
                reconcile_stale_transition=lambda station, native: False,
                trace_manual_next=lambda *args, **kwargs: None,
                resolve_native_live_player=lambda active, timeout: (
                    "a",
                    {"a_uri": "active"},
                    {},
                ),
                same_queue_identity=lambda left, right: left == right,
                start_transition=lambda station, **kwargs: transition_calls.append(
                    {"station": station, **kwargs}
                ) or True,
                wake_autodj_worker=lambda: None,
            )
        )
        result = service.direct_handoff(
            "station.db",
            reserved_queue_lines=["target", "tail"],
            reservation_id="mn-1",
        )
        self.assertTrue(result and result["success"])
        self.assertEqual(result["target_queue_id"], 22)
        self.assertEqual(state["lines"], ["active", "target", "tail"])
        self.assertEqual(transition_calls[0]["manual_next_request_id"], "mn-1")
        self.assertTrue(transition_calls[0]["manual_next_fast"])

    def test_manual_next_lifecycle_commit_is_station_scoped(self) -> None:
        traces: list[tuple] = []

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        service = ManualNextOrchestrator(
            ManualNextDependencies(
                resolve_station_key=lambda station: station,
                get_active_station_key=lambda: "station-a.db",
                trace=lambda *args, **kwargs: traces.append((args, kwargs)),
                station_runtime_context=runtime_context,
                read_reserved_plan=lambda station: (["target"], 42, 142),
                native_station_state=lambda station: {"running": True, "queue_id": 0},
                native_queue_contains_queue_id=lambda station, qid: qid == 42,
                perform_direct_handoff=lambda *args, **kwargs: {"success": True},
                signal_monitor_wake=lambda station, reason: None,
                wake_autodj_worker=lambda: None,
            ),
            commit_timeout_seconds=2.0,
        )
        accepted = service.enqueue("station-a.db")
        self.assertTrue(accepted["accepted"])
        deadline = time.monotonic() + 1.0
        while service.public_status("station-a.db").get("active_target_queue_id") != 42:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        service.mark_lifecycle("station-b.db", 42, success=True)
        self.assertTrue(service.public_status("station-a.db")["in_progress"])
        service.mark_lifecycle("station-a.db", 42, success=True)
        deadline = time.monotonic() + 1.0
        while service.public_status("station-a.db")["in_progress"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertEqual(service.public_status("station-a.db")["last_error"], "")

    def test_station_start_failure_stops_engine_and_rolls_back(self) -> None:
        calls: list[str] = []

        class Engine:
            def clear_icecast_output(self, output_id, *, station_key):
                calls.append("clear")
            def configure_icecast_output(self, *, station_key, **config):
                calls.append("configure")
            def start(self, *, station_key):
                calls.append("start")
                return {"running": True}
            def stop(self, *, station_key):
                calls.append("rollback_stop")
                return {"running": False}

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        deps = StationServiceDependencies(
            get_active_station_key=lambda: "station.db",
            get_engine=lambda: Engine(),
            station_runtime_context=runtime_context,
            native_station_state=lambda station: {"running": False},
            invalidate_status_cache=lambda: None,
            get_started_at=lambda station: None,
            set_started_at=lambda station: None,
            clear_started_at=lambda station: calls.append("clear_started_at"),
            prepare_start_state=lambda station: None,
            build_queue_plan=lambda station: ["line"],
            startup_autodj_fill=lambda station: {},
            load_output_configs=lambda station: [],
            bootstrap_queue_plan=lambda lines, station: False,
            start_autodj_worker=lambda station: None,
            notify_on_air=lambda station, reason: None,
            mark_runtime_started=lambda station: None,
            cleanup_failed_start=lambda station: calls.append("cleanup_failed_start"),
            prepare_stop_state=lambda station: {},
            restore_failed_stop_state=lambda station, context: None,
            finalize_stop_state=lambda station, context: None,
            stop_off_air_automation=lambda station: None,
            clear_now_playing=lambda station: None,
            mark_runtime_stopped=lambda station: None,
        )
        payload, status = StationService(deps).start()
        self.assertEqual(status, 503)
        self.assertFalse(payload["success"])
        self.assertEqual(calls.index("start") + 1, calls.index("rollback_stop"))
        self.assertIn("cleanup_failed_start", calls)
        self.assertIn("clear_started_at", calls)

    def test_station_stop_restores_player_state_when_daemon_still_running(self) -> None:
        calls: list[str] = []

        class Engine:
            def stop(self, *, station_key):
                calls.append("stop")
                raise RuntimeError("busy")

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        deps = StationServiceDependencies(
            get_active_station_key=lambda: "station.db",
            get_engine=lambda: Engine(),
            station_runtime_context=runtime_context,
            native_station_state=lambda station: {"running": True},
            invalidate_status_cache=lambda: None,
            get_started_at=lambda station: None,
            set_started_at=lambda station: None,
            clear_started_at=lambda station: None,
            prepare_start_state=lambda station: None,
            build_queue_plan=lambda station: [],
            startup_autodj_fill=lambda station: {},
            load_output_configs=lambda station: [],
            bootstrap_queue_plan=lambda lines, station: True,
            start_autodj_worker=lambda station: None,
            notify_on_air=lambda station, reason: None,
            mark_runtime_started=lambda station: None,
            cleanup_failed_start=lambda station: None,
            prepare_stop_state=lambda station: {"pre_stop_state": {"enabled": True}},
            restore_failed_stop_state=lambda station, context: calls.append("restore"),
            finalize_stop_state=lambda station, context: calls.append("finalize"),
            stop_off_air_automation=lambda station: None,
            clear_now_playing=lambda station: None,
            mark_runtime_stopped=lambda station: None,
        )
        payload, status = StationService(deps).stop()
        self.assertEqual(status, 500)
        self.assertFalse(payload["success"])
        self.assertEqual(calls, ["stop", "restore"])


if __name__ == "__main__":
    unittest.main()
