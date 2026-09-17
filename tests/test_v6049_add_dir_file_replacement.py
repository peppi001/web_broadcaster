from __future__ import annotations

import ast
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class V6049AddDirFileReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        tree = ast.parse(cls.source)
        wanted = {
            "_track_file_identity",
            "_track_row_identity",
            "_reset_track_after_file_replacement",
            "ensure_track",
        }
        cls.nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]

    def make_namespace(self, durations: dict[str, float]):
        namespace = {
            "os": os,
            "sqlite3": sqlite3,
            "datetime": datetime,
            "probe_duration_seconds": lambda path: durations.get(str(path)),
        }
        module = ast.Module(body=self.nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace

    @staticmethod
    def create_schema(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                play_count INTEGER NOT NULL DEFAULT 0,
                cue_in_seconds REAL,
                cue_out_seconds REAL,
                cue_trimmed_seconds REAL,
                cue_duration_seconds REAL,
                cue_fade_start_seconds REAL,
                cue_analyzed_at TEXT,
                audio_start_seconds REAL,
                audio_end_seconds REAL,
                audio_analyzed_at TEXT,
                analysis_file_size INTEGER,
                analysis_file_mtime_ns INTEGER,
                analysis_settings_hash TEXT,
                analysis_analyzer_version TEXT,
                analysis_updated_at TEXT,
                analysis_source TEXT,
                analysis_error TEXT,
                runtime_duration_seconds REAL,
                runtime_duration_verified_at TEXT,
                runtime_duration_file_size INTEGER,
                runtime_duration_file_mtime_ns INTEGER,
                runtime_duration_source TEXT
            )
            """
        )
        conn.commit()

    def test_new_track_stores_physical_file_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "scheduled.mp3"
            media.write_bytes(b"first physical file")
            identity = (media.stat().st_size, media.stat().st_mtime_ns)
            namespace = self.make_namespace({str(media): 123.5})
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.create_schema(conn)

            track_id = namespace["ensure_track"](conn, str(media))
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
            self.assertEqual(
                (row["analysis_file_size"], row["analysis_file_mtime_ns"]),
                identity,
            )
            self.assertAlmostEqual(row["cue_duration_seconds"], 123.5, places=3)
            conn.close()

    def test_same_path_replacement_invalidates_old_timing_and_reprobes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "fixed-name.mp3"
            media.write_bytes(b"old song")
            old_identity = (media.stat().st_size, media.stat().st_mtime_ns)
            namespace = self.make_namespace({str(media): 222.25})
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.create_schema(conn)
            conn.execute(
                """
                INSERT INTO tracks (
                    path, filename, created_at, play_count,
                    cue_in_seconds, cue_out_seconds, cue_trimmed_seconds,
                    cue_duration_seconds, cue_fade_start_seconds, cue_analyzed_at,
                    audio_start_seconds, audio_end_seconds, audio_analyzed_at,
                    analysis_file_size, analysis_file_mtime_ns,
                    analysis_settings_hash, analysis_analyzer_version,
                    analysis_updated_at, analysis_source, analysis_error,
                    runtime_duration_seconds, runtime_duration_verified_at,
                    runtime_duration_file_size, runtime_duration_file_mtime_ns,
                    runtime_duration_source
                ) VALUES (?, ?, ?, 7, 4.0, 98.0, 94.0, 100.0, 95.0, ?,
                          4.0, 98.0, ?, ?, ?, 'old-settings', 'old-analyzer', ?,
                          'old-analysis', 'old-error', 100.0, ?, ?, ?, 'old-runtime')
                """,
                (
                    str(media), media.name, datetime.now().isoformat(),
                    datetime.now().isoformat(), datetime.now().isoformat(),
                    old_identity[0], old_identity[1], datetime.now().isoformat(),
                    datetime.now().isoformat(), old_identity[0], old_identity[1],
                ),
            )
            conn.commit()
            track_id = int(conn.execute("SELECT id FROM tracks").fetchone()[0])

            media.write_bytes(b"this is a completely different replacement song")
            os.utime(media, None)
            new_identity = (media.stat().st_size, media.stat().st_mtime_ns)
            self.assertNotEqual(new_identity, old_identity)

            resolved_id = namespace["ensure_track"](conn, str(media))
            self.assertEqual(resolved_id, track_id)
            row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()

            self.assertEqual(row["play_count"], 7)
            self.assertEqual(
                (row["analysis_file_size"], row["analysis_file_mtime_ns"]),
                new_identity,
            )
            self.assertAlmostEqual(row["cue_duration_seconds"], 222.25, places=3)
            self.assertEqual(row["cue_in_seconds"], 0.0)
            self.assertAlmostEqual(row["cue_out_seconds"], 222.25, places=3)
            self.assertAlmostEqual(row["cue_trimmed_seconds"], 222.25, places=3)
            self.assertIsNone(row["cue_fade_start_seconds"])
            self.assertEqual(row["audio_start_seconds"], 0.0)
            self.assertAlmostEqual(row["audio_end_seconds"], 222.25, places=3)
            self.assertIsNone(row["analysis_settings_hash"])
            self.assertIsNone(row["analysis_analyzer_version"])
            self.assertIsNone(row["analysis_source"])
            self.assertIsNone(row["analysis_error"])
            self.assertIsNone(row["runtime_duration_seconds"])
            self.assertIsNone(row["runtime_duration_verified_at"])
            self.assertIsNone(row["runtime_duration_file_size"])
            self.assertIsNone(row["runtime_duration_file_mtime_ns"])
            self.assertIsNone(row["runtime_duration_source"])
            conn.close()

    def test_unchanged_file_does_not_discard_existing_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "unchanged.mp3"
            media.write_bytes(b"same file")
            identity = (media.stat().st_size, media.stat().st_mtime_ns)
            namespace = self.make_namespace({str(media): 90.0})
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.create_schema(conn)
            conn.execute(
                """
                INSERT INTO tracks (
                    path, filename, created_at, cue_duration_seconds,
                    cue_in_seconds, cue_out_seconds, cue_trimmed_seconds,
                    cue_fade_start_seconds, analysis_file_size, analysis_file_mtime_ns,
                    runtime_duration_seconds, runtime_duration_verified_at,
                    runtime_duration_file_size, runtime_duration_file_mtime_ns,
                    runtime_duration_source
                ) VALUES (?, ?, ?, 90.0, 1.5, 88.0, 86.5, 84.0, ?, ?,
                          89.9, ?, ?, ?, 'native_natural_eof')
                """,
                (
                    str(media), media.name, datetime.now().isoformat(),
                    identity[0], identity[1], datetime.now().isoformat(),
                    identity[0], identity[1],
                ),
            )
            conn.commit()

            namespace["ensure_track"](conn, str(media))
            row = conn.execute("SELECT * FROM tracks").fetchone()
            self.assertEqual(row["cue_in_seconds"], 1.5)
            self.assertEqual(row["cue_out_seconds"], 88.0)
            self.assertEqual(row["cue_fade_start_seconds"], 84.0)
            self.assertAlmostEqual(row["runtime_duration_seconds"], 89.9, places=3)
            self.assertEqual(row["runtime_duration_source"], "native_natural_eof")
            conn.close()

    def test_legacy_row_can_use_runtime_fingerprint_as_replacement_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "legacy.mp3"
            media.write_bytes(b"new legacy replacement")
            new_identity = (media.stat().st_size, media.stat().st_mtime_ns)
            namespace = self.make_namespace({str(media): 65.0})
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            self.create_schema(conn)
            conn.execute(
                """
                INSERT INTO tracks (
                    path, filename, created_at, cue_duration_seconds,
                    cue_out_seconds, cue_trimmed_seconds, audio_end_seconds,
                    runtime_duration_seconds, runtime_duration_verified_at,
                    runtime_duration_file_size, runtime_duration_file_mtime_ns,
                    runtime_duration_source
                ) VALUES (?, ?, ?, 200.0, 200.0, 200.0, 200.0,
                          200.0, ?, 10, 20, 'native_natural_eof')
                """,
                (str(media), media.name, datetime.now().isoformat(), datetime.now().isoformat()),
            )
            conn.commit()

            namespace["ensure_track"](conn, str(media))
            row = conn.execute("SELECT * FROM tracks").fetchone()
            self.assertEqual(
                (row["analysis_file_size"], row["analysis_file_mtime_ns"]),
                new_identity,
            )
            self.assertAlmostEqual(row["cue_duration_seconds"], 65.0, places=3)
            self.assertIsNone(row["runtime_duration_seconds"])
            conn.close()

    def test_scheduler_add_dir_still_resolves_each_file_at_execution_time(self) -> None:
        self.assertIn("def _resolve_directory_to_track_ids_for_station", self.source)
        self.assertIn("for full_path in file_paths:", self.source)
        self.assertIn("_ensure_track_for_station_path(station_key, full_path)", self.source)
        self.assertIn("previous_identity != current_identity", self.source)


if __name__ == "__main__":
    unittest.main()
