from __future__ import annotations

import ast
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


class RuntimeDurationSelfHealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        wanted_functions = {
            "_runtime_duration_bool",
            "_runtime_duration_int",
            "_runtime_duration_candidate",
            "_runtime_duration_endpoint_is_automatic",
            "_verify_runtime_duration_after_playback",
        }
        wanted_assignments = {
            "_RUNTIME_DURATION_VERIFY_EVENTS",
            "_RUNTIME_DURATION_CORRECTION_THRESHOLD_SECONDS",
            "_RUNTIME_DURATION_ENDPOINT_MATCH_TOLERANCE_SECONDS",
        }
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in wanted_functions:
                nodes.append(node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = set()
                if isinstance(node, ast.Assign):
                    names = {target.id for target in node.targets if isinstance(target, ast.Name)}
                elif isinstance(node.target, ast.Name):
                    names = {node.target.id}
                if names & wanted_assignments:
                    nodes.append(node)
        cls.nodes = nodes
        cls.native_source = (cls.root / "native_engine" / "src" / "audio_probe.c").read_text(encoding="utf-8")

    def make_namespace(self, db_path: Path):
        def get_db_for_station(_station_key: str):
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        namespace = {
            "os": os,
            "sqlite3": sqlite3,
            "datetime": datetime,
            "normalize_media_path": lambda value: os.path.abspath(str(value or "")),
            "get_db_for_station": get_db_for_station,
        }
        module = ast.Module(body=self.nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace

    @staticmethod
    def create_schema(db_path: Path, media_path: Path, *, cue_out=373.0, cue_trimmed=373.0, audio_end=373.0):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                cue_duration_seconds REAL,
                cue_out_seconds REAL,
                cue_trimmed_seconds REAL,
                audio_end_seconds REAL,
                runtime_duration_seconds REAL,
                runtime_duration_verified_at TEXT,
                runtime_duration_file_size INTEGER,
                runtime_duration_file_mtime_ns INTEGER,
                runtime_duration_source TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tracks (
                id, path, cue_duration_seconds, cue_out_seconds,
                cue_trimmed_seconds, audio_end_seconds
            ) VALUES (1, ?, 373.0, ?, ?, ?)
            """,
            (str(media_path), cue_out, cue_trimmed, audio_end),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def event(media_path: Path, *, event_name="native_audio_probe_eof", **payload_overrides):
        payload = {
            "manual_timing": False,
            "stream_source": False,
            "terminal_reason": "natural_eof" if event_name == "native_audio_probe_eof" else "early_eof",
            "decoder_exit_code": 0,
            "decoder_signal": 0,
            "corrupt_input_skipped_count": 0,
            "fault_injected": False,
            "play_start_ms": 0,
            "played_duration_ms": 301234,
            "final_actual_duration_ms": 301234,
            "source_position_ms": 301234,
        }
        payload.update(payload_overrides)
        return SimpleNamespace(
            event=event_name,
            station_key="db-AirFM.db",
            track_id=1,
            path=str(media_path),
            payload=payload,
        )

    def test_wrong_mutagen_duration_is_corrected_and_verified_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "Floorfilla - Technoromance.mp3"
            media.write_bytes(b"test-audio")
            db_path = root / "station.db"
            self.create_schema(db_path, media)
            namespace = self.make_namespace(db_path)

            first = namespace["_verify_runtime_duration_after_playback"](self.event(media))
            self.assertTrue(first["verified"])
            self.assertTrue(first["corrected"])
            self.assertEqual(first["duration_seconds"], 301.234)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                """
                SELECT cue_duration_seconds, cue_out_seconds, cue_trimmed_seconds,
                       audio_end_seconds, runtime_duration_seconds,
                       runtime_duration_verified_at, runtime_duration_source
                FROM tracks WHERE id = 1
                """
            ).fetchone()
            conn.close()
            self.assertEqual(row[0:5], (301.234, 301.234, 301.234, 301.234, 301.234))
            self.assertTrue(row[5])
            self.assertEqual(row[6], "native_natural_eof")

            second = namespace["_verify_runtime_duration_after_playback"](self.event(media))
            self.assertFalse(second["verified"])
            self.assertEqual(second["reason"], "already_verified")

    def test_manual_endpoint_values_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "manual.mp3"
            media.write_bytes(b"test-audio")
            db_path = root / "station.db"
            self.create_schema(db_path, media, cue_out=280.0, cue_trimmed=279.5, audio_end=290.0)
            namespace = self.make_namespace(db_path)

            result = namespace["_verify_runtime_duration_after_playback"](self.event(media))
            self.assertTrue(result["corrected"])
            self.assertEqual(result["changed_endpoints"], [])
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT cue_duration_seconds, cue_out_seconds, cue_trimmed_seconds, audio_end_seconds FROM tracks"
            ).fetchone()
            conn.close()
            self.assertEqual(row, (301.234, 280.0, 279.5, 290.0))

    def test_clean_early_eof_can_repair_overestimated_length(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "Floorfilla.mp3"
            media.write_bytes(b"test-audio")
            db_path = root / "station.db"
            self.create_schema(db_path, media)
            namespace = self.make_namespace(db_path)
            result = namespace["_verify_runtime_duration_after_playback"](
                self.event(media, event_name="native_audio_probe_early_eof")
            )
            self.assertTrue(result["corrected"])
            self.assertEqual(result["source"], "native_clean_early_eof")

    def test_manual_fault_or_corrupt_eof_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "unsafe.mp3"
            media.write_bytes(b"test-audio")
            db_path = root / "station.db"
            self.create_schema(db_path, media)
            namespace = self.make_namespace(db_path)
            verify = namespace["_verify_runtime_duration_after_playback"]
            self.assertEqual(verify(self.event(media, manual_timing=True))["reason"], "ineligible_event")
            self.assertEqual(verify(self.event(media, fault_injected=True))["reason"], "ineligible_event")
            self.assertEqual(
                verify(self.event(media, corrupt_input_skipped_count=1))["reason"],
                "ineligible_event",
            )

    def test_native_events_expose_manual_timing_and_callback_is_nonblocking(self) -> None:
        self.assertIn(r'\"manual_timing\":%s', self.native_source)
        self.assertIn('track->manual_timing ? "true" : "false"', self.native_source)
        self.assertIn("_schedule_runtime_duration_verification(event)", self.source)
        self.assertIn('APP_VERSION = "6024"', self.source)


if __name__ == "__main__":
    unittest.main()
