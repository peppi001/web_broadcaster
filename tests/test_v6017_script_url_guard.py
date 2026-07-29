from __future__ import annotations

import ast
import contextlib
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from player import ManualNextDependencies, ManualNextOrchestrator


class V6018ScriptUrlGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_active_url_detection_uses_native_audible_source(self) -> None:
        node = self.functions["_station_url_playback_active"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        state = {"running": True, "native_audio_probe_path": "https://radio.example/live"}
        namespace = {
            "_native_station_state": lambda _station: dict(state),
            "normalize_media_path": lambda value: str(value or ""),
            "_native_status_line_for_state": lambda _station, _state: "",
            "_ab_line_info": lambda _line: {},
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        self.assertTrue(namespace["_station_url_playback_active"]("db-AirFM.db"))
        state["native_audio_probe_path"] = "/music/song.mp3"
        self.assertFalse(namespace["_station_url_playback_active"]("db-AirFM.db"))

    def test_due_occurrence_is_consumed_before_any_queue_write(self) -> None:
        node = self.functions["_run_station_script_once"]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        due = datetime(2026, 7, 25, 12, 0, 0)
        statuses: list[str] = []
        consumed: list[tuple[str, int, str]] = []
        namespace = {
            "datetime": datetime,
            "timedelta": timedelta,
            "_SCRIPT_ENGINE_LAST_RUN": {},
            "_get_cached_station_script_definition": lambda *_args: {
                "parsed": {
                    "wait_for_time": "XX:00:00",
                    "queue_path": "/announcements/1200.mp3",
                    "queue_pos": "top",
                    "do_next": True,
                }
            },
            "_set_station_script_status": lambda _station, _script_id, status: statuses.append(status),
            "_script_due_run_datetime": lambda _wait, _now: due,
            "_format_script_waiting_status": lambda *_args: "Waiting",
            "_station_url_playback_active": lambda _station: True,
            "_mark_station_script_url_skip": lambda station, script_id, _wait, _due, run_key: consumed.append(
                (station, script_id, run_key)
            ),
        }
        exec(compile(module, str(self.app_path), "exec"), namespace)
        namespace["_run_station_script_once"](
            "db-AirFM.db",
            {"id": 7, "script_path": "/scripts/hourly.wbs"},
            due,
        )
        self.assertEqual(consumed, [("db-AirFM.db", 7, "2026-07-25 12:00:00")])
        self.assertEqual(namespace["_SCRIPT_ENGINE_LAST_RUN"], {})

    def test_general_scheduler_next_is_not_mislabeled_as_station_script(self) -> None:
        source = self._function_source("_perform_station_next_action")
        self.assertIn('source="scheduler"', source)
        self.assertNotIn('source="script"', source)

    def test_script_runtime_has_all_guards_and_no_catchup(self) -> None:
        source = self._function_source("_run_station_script_once")
        self.assertGreaterEqual(source.count("_station_url_playback_active(station_key)"), 4)
        self.assertIn("_mark_station_script_url_skip", source)
        self.assertIn("_cancel_scheduled_script_queue_items", source)
        self.assertIn("guarded_queue_ids=created_queue_ids", source)
        marker = self._function_source("_mark_station_script_url_skip")
        self.assertIn("_SCRIPT_ENGINE_LAST_RUN[cache_key]", marker)
        self.assertIn("due_dt.replace(microsecond=0) + timedelta(seconds=1)", marker)

    def test_serialized_manual_next_cancels_late_url_race(self) -> None:
        cancelled: list[tuple[str, list[int], str]] = []
        direct_calls: list[str] = []

        @contextlib.contextmanager
        def runtime_context(_station: str):
            yield

        service = ManualNextOrchestrator(
            ManualNextDependencies(
                resolve_station_key=lambda station: station,
                get_active_station_key=lambda: "db-AirFM.db",
                trace=lambda *args, **kwargs: None,
                station_runtime_context=runtime_context,
                read_reserved_plan=lambda _station: (["announcement"], 91, 9),
                native_station_state=lambda _station: {"running": True, "queue_id": 12},
                native_queue_contains_queue_id=lambda _station, _qid: True,
                perform_direct_handoff=lambda *args, **kwargs: direct_calls.append("called") or {"success": True},
                signal_monitor_wake=lambda _station, _reason: None,
                wake_autodj_worker=lambda: None,
                scheduled_script_url_active=lambda _station: True,
                cancel_scheduled_script_queue=lambda station, ids, reason: cancelled.append((station, list(ids), reason)),
            )
        )
        result = service._execute_one(
            "db-AirFM.db",
            {
                "request_id": "mn-script",
                "action": "next",
                "source": "script",
                "guarded_queue_ids": [901, 902],
            },
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["mode"], "scheduled_script_skipped_url_playback")
        self.assertEqual(cancelled, [("db-AirFM.db", [901, 902], "url_playback_active")])
        self.assertEqual(direct_calls, [])


if __name__ == "__main__":
    unittest.main()
