from __future__ import annotations

import ast
import os
import re
import sqlite3
import unittest
from pathlib import Path


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


class DbQueuePlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    @classmethod
    def _function_source(cls, name: str) -> str:
        node = next(
            item for item in cls.tree.body
            if isinstance(item, ast.FunctionDef) and item.name == name
        )
        lines = cls.source.splitlines()
        return "\n".join(lines[node.lineno - 1:node.end_lineno])

    def test_queue_plan_is_built_in_memory_without_runtime_m3u_io(self) -> None:
        helper = self._function_source("_build_station_queue_plan")
        self.assertIn("FROM queue_items", helper)
        self.assertIn("_ab_native_runtime_timing_metadata", helper)
        self.assertNotIn("open(", helper)
        self.assertNotIn("os.replace", helper)
        self.assertNotIn("get_runtime_dir", helper)
        self.assertNotIn("os.path.join", helper)

    def test_queue_rows_become_native_descriptors_in_database_order(self) -> None:
        rows = [
            {
                "queue_id": 11,
                "track_id": 21,
                "clean_transition": 0,
                "script_clean_transition": 0,
                "path": "/music/Artist - First.mp3",
                "filename": "Artist - First.mp3",
                "cue_in_seconds": None,
                "cue_out_seconds": None,
                "cue_duration_seconds": None,
                "cue_fade_start_seconds": None,
                "audio_start_seconds": None,
                "audio_end_seconds": None,
            },
            {
                "queue_id": 12,
                "track_id": 22,
                "clean_transition": 0,
                "script_clean_transition": 0,
                "path": "/music/Artist - Second.mp3",
                "filename": "Artist - Second.mp3",
                "cue_in_seconds": None,
                "cue_out_seconds": None,
                "cue_duration_seconds": None,
                "cue_fade_start_seconds": None,
                "audio_start_seconds": None,
                "audio_end_seconds": None,
            },
        ]
        connection = _FakeConnection(rows)
        namespace = {
            "os": os,
            "re": re,
            "sqlite3": sqlite3,
            "get_active_station_key": lambda: "db-test.db",
            "get_db_for_station": lambda station_key: connection,
            "_ensure_queue_items_clean_transition_schema": lambda conn: None,
            "_ab_sam_settings_from_row": lambda row: {"crossfade_fallback_seconds": 3.0},
            "_ab_get_settings_from_conn": lambda conn: {},
            "normalize_media_path": lambda value: value,
            "_build_seek_restart_descriptor": lambda track, station: "",
            "read_media_metadata": lambda path: {
                "artist": "Tagged Artist",
                "title": "Tagged Title",
                "year": "1999",
            },
            "_normalize_year_metadata": lambda value: str(value or ""),
            "_ab_native_runtime_timing_metadata": (
                lambda path, row, station_key, sam_settings, escape:
                ',wb_native_analyze="1",fade_in="0.000",fade_out="5.000"'
            ),
            "_build_annotate_uri": lambda meta, path: f"URL:{path}",
        }
        exec(self._function_source("_build_station_queue_plan"), namespace)
        plan = namespace["_build_station_queue_plan"]("db-test.db")

        self.assertEqual(len(plan), 2)
        self.assertIn('queue_id="11"', plan[0])
        self.assertIn('track_id="21"', plan[0])
        self.assertIn('station_key="db-test.db"', plan[0])
        self.assertIn('artist="Tagged Artist"', plan[0])
        self.assertIn('title="Tagged Title"', plan[0])
        self.assertNotIn('title="First"', plan[0])
        self.assertIn('year="1999"', plan[0])
        self.assertIn('/music/Artist - First.mp3', plan[0])
        self.assertIn('queue_id="12"', plan[1])
        self.assertIn('/music/Artist - Second.mp3', plan[1])
        self.assertTrue(connection.closed)


    def test_queue_plan_uses_filename_fallback_when_media_tags_are_missing(self) -> None:
        rows = [{
            "queue_id": 13,
            "track_id": 23,
            "clean_transition": 0,
            "script_clean_transition": 0,
            "path": "/music/Fallback Artist - Fallback Title.mp3",
            "filename": "Fallback Artist - Fallback Title.mp3",
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
            "read_media_metadata": lambda path: {"artist": "", "title": "", "year": ""},
            "_normalize_year_metadata": lambda value: str(value or ""),
            "_ab_native_runtime_timing_metadata": lambda *args, **kwargs: "",
            "_ab_build_native_stream_descriptor": lambda *args, **kwargs: "unused",
        }
        exec(self._function_source("_build_station_queue_plan"), namespace)
        plan = namespace["_build_station_queue_plan"]("db-test.db")

        self.assertEqual(len(plan), 1)
        self.assertIn('artist="Fallback Artist"', plan[0])
        self.assertIn('title="Fallback Title"', plan[0])

    def test_url_queue_row_becomes_native_stream_descriptor(self) -> None:
        rows = [{
            "queue_id": 31,
            "track_id": 41,
            "clean_transition": 0,
            "script_clean_transition": 0,
            "path": "URL:60:https://radio.example:8443/live",
            "filename": "",
            "cue_in_seconds": None,
            "cue_out_seconds": None,
            "cue_duration_seconds": None,
            "cue_fade_start_seconds": None,
            "audio_start_seconds": None,
            "audio_end_seconds": None,
        }]
        connection = _FakeConnection(rows)
        calls = []
        namespace = {
            "os": os,
            "re": re,
            "sqlite3": sqlite3,
            "get_active_station_key": lambda: "db-test.db",
            "get_db_for_station": lambda station_key: connection,
            "_ensure_queue_items_clean_transition_schema": lambda conn: None,
            "_ab_sam_settings_from_row": lambda row: {},
            "_ab_get_settings_from_conn": lambda conn: {},
            "normalize_media_path": lambda value: value,
            "_build_seek_restart_descriptor": lambda track, station: "",
            "read_media_metadata": lambda path: {},
            "_normalize_year_metadata": lambda value: "",
            "_ab_native_runtime_timing_metadata": lambda *args, **kwargs: "",
            "_build_annotate_uri": lambda meta, path: "unused",
            "_ab_build_native_stream_descriptor": (
                lambda url, duration, **kwargs: calls.append((url, duration, kwargs)) or
                f'NATIVE_STREAM:{duration}:{url}'
            ),
        }
        exec(self._function_source("_build_station_queue_plan"), namespace)
        plan = namespace["_build_station_queue_plan"]("db-test.db")
        self.assertEqual(plan, ["NATIVE_STREAM:60:https://radio.example:8443/live"])
        self.assertEqual(calls[0][0], "https://radio.example:8443/live")
        self.assertEqual(calls[0][1], 60)
        self.assertEqual(calls[0][2]["queue_id"], 31)
        self.assertEqual(calls[0][2]["track_id"], 41)
        self.assertEqual(calls[0][2]["station_key"], "db-test.db")

    def test_need_next_keeps_url_streams_on_native_decks(self) -> None:
        helper = self._function_source("_native_load_requested_next_track")
        self.assertNotIn("native_need_next_track_webradio_deferred", helper)
        self.assertNotIn("_start_webradio_direct_from_python", helper)
        self.assertIn("_ab_push", helper)


if __name__ == "__main__":
    unittest.main()
