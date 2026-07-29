from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from storage import PlaybackRepository, PlaybackRepositoryDependencies


class PlaybackRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.paths = {
            "one.db": self.root / "one.db",
            "two.db": self.root / "two.db",
        }
        for path in self.paths.values():
            self._create_database(path)
        self.repository = PlaybackRepository(
            PlaybackRepositoryDependencies(
                open_connection=self._open,
                ensure_autodj_settings=self._ensure_autodj_settings,
                guess_metadata=self._guess_metadata,
                normalize_media_path=lambda value: str(value or ""),
                active_station_key=lambda: "one.db",
                now=lambda: datetime(2026, 7, 20, 20, 0, 0),
            )
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _open(self, station_key: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self.paths[station_key])
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _guess_metadata(value: str) -> dict:
        stem = Path(value).stem
        if " - " in stem:
            artist, title = stem.split(" - ", 1)
        else:
            artist, title = "", stem
        return {"artist": artist, "title": title}

    @staticmethod
    def _ensure_autodj_settings(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS autodj_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                no_repeat_artist_minutes INTEGER NOT NULL DEFAULT 0,
                no_repeat_title_minutes INTEGER NOT NULL DEFAULT 0,
                no_repeat_track_minutes INTEGER NOT NULL DEFAULT 0,
                keep_queue INTEGER NOT NULL DEFAULT 0,
                editor_text TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                rotation_next_index INTEGER NOT NULL DEFAULT 0,
                rotation_signature TEXT NOT NULL DEFAULT ''
            )
            """
        )

    def _create_database(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE tracks (
                id INTEGER PRIMARY KEY,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                cue_duration_seconds REAL,
                play_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                clean_transition INTEGER NOT NULL DEFAULT 0,
                autodj_rotation_index INTEGER,
                autodj_rotation_category_id INTEGER,
                autodj_rotation_norules INTEGER NOT NULL DEFAULT 0,
                autodj_rotation_sig TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE play_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                track_id INTEGER NOT NULL,
                played_at TEXT NOT NULL
            );
            CREATE TABLE category_tracks (
                category_id INTEGER NOT NULL,
                track_id INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO tracks (id, filename, path) VALUES (?, ?, ?)",
            [
                (1, "Artist One - Track One.mp3", "/music/Artist One - Track One.mp3"),
                (2, "Artist Two - Track Two.mp3", "/music/Artist Two - Track Two.mp3"),
                (3, "Artist Three - Track Three.mp3", "/music/Artist Three - Track Three.mp3"),
            ],
        )
        conn.execute("INSERT INTO category_tracks (category_id, track_id) VALUES (7, 3)")
        conn.commit()
        conn.close()

    def _enqueue(self, station: str, track_id: int, position: int) -> int:
        conn = self._open(station)
        cur = conn.execute(
            "INSERT INTO queue_items (track_id, position, created_at) VALUES (?, ?, ?)",
            (track_id, position, "2026-07-20T19:00:00"),
        )
        conn.commit()
        queue_id = int(cur.lastrowid)
        conn.close()
        return queue_id

    def test_snapshots_rotation_and_enqueue_are_station_local(self) -> None:
        self._enqueue("one.db", 1, 10)
        self.repository.enqueue_track_items(
            [{"track_id": 2, "category_id": 4, "rotation_index": 1, "rotation_sig": "sig"}],
            station_key="one.db",
        )
        self.assertEqual([row["track_id"] for row in self.repository.get_queue_snapshot("one.db")], [1, 2])
        self.assertEqual(self.repository.queue_count("two.db"), 0)
        self.assertEqual(self.repository.get_category_tracks(7, "one.db")[0]["track_id"], 3)
        self.assertEqual(self.repository.get_rotation_cursor([{"category_id": 7}], "a", "one.db"), 0)
        self.repository.set_rotation_cursor(5, "a", "one.db")
        self.assertEqual(self.repository.get_rotation_cursor([{"category_id": 7}], "a", "one.db"), 0)


    def test_generic_queue_operations_and_history_views_use_repository(self) -> None:
        created = self.repository.enqueue_track_ids([1, 2], "end", station_key="one.db")
        self.assertEqual(len(created), 2)
        self.assertEqual(self.repository.get_queue_head_id("one.db"), created[0])
        self.assertEqual([row["track_id"] for row in self.repository.list_queue_items("one.db")], [1, 2])

        self.repository.reorder_queue([created[1], created[0]], station_key="one.db")
        self.assertEqual(self.repository.get_queue_head_id("one.db"), created[1])
        self.assertTrue(self.repository.move_queue_items_to_front([created[0]], station_key="one.db"))
        self.assertEqual(self.repository.get_queue_head_id("one.db"), created[0])

        committed = self.repository.commit_started_track(
            {"queue_id": created[0], "track_id": 1, "_no_head_fallback": True},
            station_key="one.db",
        )
        self.assertTrue(committed.committed)
        history = self.repository.list_history_items("one.db")
        self.assertEqual(history[0]["filename"], "Artist One - Track One.mp3")
        self.assertEqual(self.repository.remove_queue_items([created[1]], "one.db"), 1)
        self.assertEqual(self.repository.queue_count("one.db"), 0)

    def test_track_started_commit_is_atomic_and_deduplicated(self) -> None:
        queue_id = self._enqueue("one.db", 1, 10)
        result = self.repository.commit_started_track(
            {
                "queue_id": queue_id,
                "track_id": 1,
                "file": "/music/Artist One - Track One.mp3",
                "artist": "Artist One",
                "title": "Track One",
                "_no_head_fallback": True,
            },
            station_key="one.db",
        )
        self.assertTrue(result.committed)
        conn = self._open("one.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM play_history").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT play_count FROM tracks WHERE id = 1").fetchone()[0], 1)
        runtime = conn.execute(
            "SELECT last_commit_queue_id, last_commit_track_id FROM runtime_playback_state WHERE id = 1"
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(runtime), (queue_id, 1))

        stale = self.repository.commit_started_track(
            {"queue_id": queue_id, "track_id": 1, "_no_head_fallback": True},
            station_key="one.db",
        )
        self.assertFalse(stale.committed)
        self.assertEqual(stale.reason, "empty_queue")

    def test_missing_exact_queue_id_does_not_remove_next_head(self) -> None:
        next_queue_id = self._enqueue("one.db", 2, 10)
        result = self.repository.commit_started_track(
            {"queue_id": 999, "track_id": 1, "_no_head_fallback": True},
            station_key="one.db",
        )
        self.assertFalse(result.committed)
        self.assertEqual(result.reason, "no_head_fallback")
        conn = self._open("one.db")
        rows = conn.execute("SELECT id, track_id FROM queue_items").fetchall()
        conn.close()
        self.assertEqual([(row[0], row[1]) for row in rows], [(next_queue_id, 2)])

    def test_history_failure_rolls_back_queue_runtime_and_play_count(self) -> None:
        queue_id = self._enqueue("one.db", 1, 10)
        conn = self._open("one.db")
        conn.execute(
            """
            CREATE TRIGGER fail_history_insert
            BEFORE INSERT ON play_history
            BEGIN
                SELECT RAISE(FAIL, 'forced history failure');
            END
            """
        )
        conn.commit()
        conn.close()

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.commit_started_track(
                {"queue_id": queue_id, "track_id": 1, "_no_head_fallback": True},
                station_key="one.db",
            )

        conn = self._open("one.db")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM queue_items").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM play_history").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT play_count FROM tracks WHERE id = 1").fetchone()[0], 0)
        runtime_exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='runtime_playback_state'"
        ).fetchone()[0]
        if runtime_exists:
            row = conn.execute(
                "SELECT last_commit_queue_id FROM runtime_playback_state WHERE id = 1"
            ).fetchone()
            self.assertTrue(row is None or row[0] is None)
        conn.close()

    def test_app_keeps_only_thin_repository_facades(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "app.py").read_text(encoding="utf-8")
        repository_source = (root / "storage" / "playback_repository.py").read_text(encoding="utf-8")
        self.assertIn("class PlaybackRepository", repository_source)
        self.assertIn("BEGIN IMMEDIATE", repository_source)
        self.assertIn("_get_playback_repository().commit_started_track", app_source)
        self.assertNotIn("DEQUEUE_LOCK", app_source)
        self.assertNotIn("DEQUEUE_STATE_BY_STATION", app_source)
        self.assertNotIn("INSERT INTO play_history (track_id, played_at)", app_source)
        self.assertNotIn("UPDATE queue_items SET position", app_source)
        self.assertNotIn("DELETE FROM queue_items WHERE id", app_source)


if __name__ == "__main__":
    unittest.main()
