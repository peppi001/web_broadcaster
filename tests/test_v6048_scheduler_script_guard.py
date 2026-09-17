from __future__ import annotations

import ast
import contextlib
import os
import re
import sqlite3
import threading
import unittest
from pathlib import Path

from player import ManualNextDependencies, ManualNextOrchestrator


class _FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.row_factory = None
        self.closed = False

    def execute(self, _sql):
        return self

    def fetchall(self):
        return list(self.rows)

    def close(self):
        self.closed = True


class V6048SchedulerScriptGuardTests(unittest.TestCase):
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

    def _compile_function(self, name: str, namespace: dict) -> dict:
        node = self.functions[name]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace

    def test_station_schema_persists_queue_origin(self) -> None:
        self.assertIn("CREATE TABLE IF NOT EXISTS queue_item_metadata", self.source)
        self.assertIn("CREATE TRIGGER IF NOT EXISTS queue_item_metadata_cleanup", self.source)
        self.assertNotIn("ALTER TABLE", self.source)

    def test_scheduler_queue_paths_mark_every_created_item(self) -> None:
        end_source = self._function_source("_enqueue_track_ids_for_station")
        action_source = self._function_source("_apply_scheduler_rule_queue_action_for_station")
        self.assertIn('_mark_queue_items_origin_for_station(station_key, created_queue_ids, "scheduler")', end_source)
        self.assertIn('_mark_queue_items_origin_for_station(station_key, created_queue_ids, "scheduler")', action_source)
        self.assertIn("remove_queue_items(created_queue_ids", end_source)
        self.assertIn("remove_queue_items(created_queue_ids", action_source)

    def test_local_queue_descriptor_carries_scheduler_origin(self) -> None:
        rows = [{
            "queue_id": 71,
            "track_id": 81,
            "clean_transition": 0,
            "script_clean_transition": 0,
            "enqueue_origin": "scheduler",
            "path": "/music/Scheduled Artist - Scheduled Title.mp3",
            "filename": "Scheduled Artist - Scheduled Title.mp3",
            "cue_in_seconds": None,
            "cue_out_seconds": None,
            "cue_duration_seconds": None,
            "cue_fade_start_seconds": None,
            "audio_start_seconds": None,
            "audio_end_seconds": None,
        }]
        connection = _FakeConnection(rows)
        namespace = {
            "os": os,
            "re": re,
            "sqlite3": sqlite3,
            "get_active_station_key": lambda: "db-test.db",
            "get_db_for_station": lambda station_key: connection,
            "_ab_sam_settings_from_row": lambda row: {},
            "_ab_get_settings_from_conn": lambda conn: {},
            "normalize_media_path": lambda value: value,
            "_build_seek_restart_descriptor": lambda track, station: "",
            "read_media_metadata": lambda path: {"artist": "Artist", "title": "Title", "year": ""},
            "_normalize_year_metadata": lambda value: "",
            "_ab_native_runtime_timing_metadata": lambda *args, **kwargs: "",
            "_ab_build_native_stream_descriptor": lambda *args, **kwargs: "unused",
        }
        self._compile_function("_build_station_queue_plan", namespace)
        plan = namespace["_build_station_queue_plan"]("db-test.db")
        self.assertEqual(len(plan), 1)
        self.assertIn('wb_queue_origin="scheduler"', plan[0])

    def test_stream_descriptor_carries_scheduler_origin(self) -> None:
        namespace = {"_build_annotate_uri": lambda meta, path: (meta, path)}
        self._compile_function("_ab_build_native_stream_descriptor", namespace)
        meta, path = namespace["_ab_build_native_stream_descriptor"](
            "https://radio.example/live",
            0,
            queue_id=11,
            track_id=12,
            station_key="db-test.db",
            queue_origin="scheduler",
        )
        self.assertEqual(path, "https://radio.example/live")
        self.assertEqual(meta["wb_queue_origin"], "scheduler")

    def test_active_scheduler_item_blocks_script_next(self) -> None:
        namespace = {
            "_station_script_interrupt_block_reason": lambda _station: "scheduler_playback_active",
            "_get_manual_next_orchestrator": lambda: None,
        }
        self._compile_function("_perform_player_manual_next_action", namespace)
        result = namespace["_perform_player_manual_next_action"](
            "db-AirFM.db", action="next", source="script", guarded_queue_ids=[1]
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "scheduler_playback_active")
        self.assertEqual(result["mode"], "scheduled_script_skipped_scheduler_playback")

    def test_serialized_handoff_rechecks_scheduler_guard(self) -> None:
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
                scheduled_script_url_active=lambda _station: False,
                scheduled_script_scheduler_active=lambda _station: True,
                cancel_scheduled_script_queue=lambda station, ids, reason: cancelled.append((station, list(ids), reason)),
            )
        )
        result = service._execute_one(
            "db-AirFM.db",
            {
                "request_id": "mn-script-scheduler",
                "action": "next",
                "source": "script",
                "guarded_queue_ids": [901, 902],
            },
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["mode"], "scheduled_script_skipped_scheduler_playback")
        self.assertEqual(result["reason"], "scheduler_playback_active")
        self.assertEqual(cancelled, [("db-AirFM.db", [901, 902], "scheduler_playback_active")])
        self.assertEqual(direct_calls, [])

    def test_active_scheduler_detection_uses_descriptor_origin(self) -> None:
        lock = threading.RLock()
        namespace = {
            "_native_station_state": lambda _station: {
                "running": True,
                "queue_id": 44,
                "native_audio_probe_path": "/music/scheduled.mp3",
            },
            "_native_status_line_for_state": lambda _station, _state: "line",
            "_ab_line_info": lambda _line: {"queue_origin": "scheduler"},
            "normalize_media_path": lambda value: str(value or ""),
            "NOW_PLAYING_LOCK": lock,
            "_get_now_playing_store": lambda _station: {},
        }
        self._compile_function("_station_scheduler_playback_active", namespace)
        self.assertTrue(namespace["_station_scheduler_playback_active"]("db-AirFM.db"))


if __name__ == "__main__":
    unittest.main()
