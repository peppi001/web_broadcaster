from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Optional


@dataclass(frozen=True)
class PlaybackRepositoryDependencies:
    """Small application-owned seams required by the storage layer."""

    open_connection: Callable[[str], sqlite3.Connection]
    ensure_autodj_settings: Callable[[sqlite3.Connection], None]
    guess_metadata: Callable[[str], Mapping[str, Any]]
    normalize_media_path: Callable[[str], str]
    active_station_key: Callable[[], str]
    now: Callable[[], datetime] = datetime.now


@dataclass(frozen=True)
class QueueHistoryCommitResult:
    committed: bool
    reason: str
    station_key: str
    queue_id: int = 0
    track_id: int = 0
    played_at: str = ""
    song_key: str = ""
    normalized_path: str = ""
    choice_reason: str = ""
    history_written: bool = False


class PlaybackRepository:
    """Station-local queue, history, runtime-state and rotation persistence.

    This class owns SQLite reads/writes only. Playback decisions, native deck
    operations, UI notifications and queue replanning stay in the application
    services that call it.
    """

    def __init__(self, dependencies: PlaybackRepositoryDependencies) -> None:
        self._deps = dependencies
        self._locks_guard = threading.Lock()
        self._station_locks: dict[str, threading.RLock] = {}

    def _station_key(self, station_key: str = "") -> str:
        resolved = str(station_key or "").strip()
        if not resolved:
            resolved = str(self._deps.active_station_key() or "").strip()
        return resolved

    def _lock_for_station(self, station_key: str) -> threading.RLock:
        key = station_key or "__default__"
        with self._locks_guard:
            lock = self._station_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._station_locks[key] = lock
            return lock

    def _open(self, station_key: str = "") -> sqlite3.Connection:
        return self._deps.open_connection(self._station_key(station_key))


    @staticmethod
    def ensure_runtime_playback_state_schema(conn: sqlite3.Connection) -> None:
        """Create the canonical station-local playback reconciliation state."""
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_playback_state (
                id INTEGER PRIMARY KEY CHECK(id = 1),
                current_track_path TEXT,
                current_title TEXT,
                current_artist TEXT,
                current_started_at TEXT,
                current_queue_id INTEGER,
                current_track_id INTEGER,
                current_history_written INTEGER NOT NULL DEFAULT 0,
                last_callback_at TEXT,
                last_callback_source TEXT,
                last_commit_key TEXT,
                last_commit_queue_id INTEGER,
                last_commit_track_id INTEGER,
                last_commit_played_at TEXT
            )
            """
        )
        cursor.execute(
            """
            INSERT OR IGNORE INTO runtime_playback_state (
                id, current_track_path, current_title, current_artist,
                current_started_at, current_queue_id, current_track_id,
                current_history_written, last_callback_at, last_callback_source,
                last_commit_key, last_commit_queue_id, last_commit_track_id,
                last_commit_played_at
            ) VALUES (1, '', '', '', NULL, NULL, NULL, 0, NULL, '', '', NULL, NULL, NULL)
            """
        )

    @staticmethod
    def song_key(song: Mapping[str, Any]) -> str:
        try:
            path = str(song.get("file") or song.get("path") or "").strip()
        except Exception:
            path = ""
        if path:
            try:
                return os.path.realpath(path)
            except Exception:
                return path
        try:
            artist = str(song.get("artist") or "").strip()
            title = str(song.get("title") or "").strip()
        except Exception:
            artist = ""
            title = ""
        return f"{artist}|{title}".strip("|")

    @classmethod
    def get_runtime_state_row(cls, conn: sqlite3.Connection) -> dict[str, Any]:
        cls.ensure_runtime_playback_state_schema(conn)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM runtime_playback_state WHERE id = 1").fetchone()
            return dict(row) if row else {}
        finally:
            conn.row_factory = previous_factory

    def mark_history_commit(
        self,
        conn: sqlite3.Connection,
        song: Mapping[str, Any],
        queue_id: int,
        track_id: int,
        played_at: str,
        station_key: str = "",
    ) -> None:
        self.ensure_runtime_playback_state_schema(conn)
        queue_value = int(queue_id or 0)
        track_value = int(track_id or 0)
        key = self.song_key(song)
        conn.execute(
            """
            UPDATE runtime_playback_state
               SET current_track_path = ?,
                   current_title = ?,
                   current_artist = ?,
                   current_started_at = COALESCE(current_started_at, ?),
                   current_queue_id = ?,
                   current_track_id = ?,
                   current_history_written = 1,
                   last_commit_key = ?,
                   last_commit_queue_id = ?,
                   last_commit_track_id = ?,
                   last_commit_played_at = ?
             WHERE id = 1
            """,
            (
                str(song.get("file") or song.get("path") or ""),
                str(song.get("title") or ""),
                str(song.get("artist") or ""),
                played_at,
                queue_value if queue_value > 0 else None,
                track_value if track_value > 0 else None,
                key,
                queue_value if queue_value > 0 else None,
                track_value if track_value > 0 else None,
                played_at,
            ),
        )

    def get_rotation_cursor(
        self,
        rotation_entries: list[dict],
        rotation_signature: str,
        station_key: str = "",
    ) -> int:
        rotation_length = len(rotation_entries or [])
        if rotation_length <= 0:
            return 0
        conn = self._open(station_key)
        try:
            self._deps.ensure_autodj_settings(conn)
            row = conn.execute(
                "SELECT id, rotation_next_index, rotation_signature "
                "FROM autodj_settings ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO autodj_settings "
                    "(no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, "
                    "keep_queue, editor_text, created_at, rotation_next_index, rotation_signature) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (60, 60, 60, 3, "", self._deps.now().isoformat(timespec="seconds"), 0, rotation_signature),
                )
                conn.commit()
                return 0
            row_id = int(row["id"] if hasattr(row, "keys") else row[0])
            stored_index = int((row["rotation_next_index"] if hasattr(row, "keys") else row[1]) or 0)
            stored_signature = str((row["rotation_signature"] if hasattr(row, "keys") else row[2]) or "")
            if stored_signature != rotation_signature:
                conn.execute(
                    "UPDATE autodj_settings SET rotation_next_index = ?, rotation_signature = ? WHERE id = ?",
                    (0, rotation_signature, row_id),
                )
                conn.commit()
                return 0
            return stored_index % rotation_length
        finally:
            conn.close()

    def set_rotation_cursor(
        self,
        next_index: int,
        rotation_signature: str,
        station_key: str = "",
    ) -> None:
        conn = self._open(station_key)
        try:
            self._deps.ensure_autodj_settings(conn)
            row = conn.execute("SELECT id FROM autodj_settings ORDER BY id ASC LIMIT 1").fetchone()
            index = int(next_index or 0)
            if row:
                row_id = int(row["id"] if hasattr(row, "keys") else row[0])
                conn.execute(
                    "UPDATE autodj_settings SET rotation_next_index = ?, rotation_signature = ? WHERE id = ?",
                    (index, rotation_signature, row_id),
                )
            else:
                conn.execute(
                    "INSERT INTO autodj_settings "
                    "(no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, "
                    "keep_queue, editor_text, created_at, rotation_next_index, rotation_signature) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (60, 60, 60, 3, "", self._deps.now().isoformat(timespec="seconds"), index, rotation_signature),
                )
            conn.commit()
        finally:
            conn.close()

    def get_queue_head_id(self, station_key: str = "") -> int:
        conn = self._open(station_key)
        try:
            row = conn.execute(
                "SELECT id FROM queue_items ORDER BY position ASC, id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return 0
            return int((row["id"] if hasattr(row, "keys") else row[0]) or 0)
        finally:
            conn.close()

    def list_queue_items(self, station_key: str = "") -> list[dict]:
        conn = self._open(station_key)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT queue_items.id AS queue_id,
                       tracks.id AS track_id,
                       tracks.filename AS filename,
                       tracks.path AS path,
                       tracks.cue_duration_seconds AS cue_duration_seconds,
                       queue_items.position AS position
                FROM queue_items
                JOIN tracks ON tracks.id = queue_items.track_id
                ORDER BY queue_items.position ASC, queue_items.id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows or []]
        finally:
            conn.row_factory = previous_factory
            conn.close()

    def list_history_items(self, station_key: str = "", limit: int = 200) -> list[dict]:
        conn = self._open(station_key)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT h.id,
                       h.played_at,
                       t.filename,
                       t.cue_duration_seconds AS cue_duration_seconds
                FROM play_history h
                JOIN tracks t ON t.id = h.track_id
                ORDER BY h.played_at DESC, h.id DESC
                LIMIT ?
                """,
                (max(1, int(limit or 200)),),
            ).fetchall()
            return [dict(row) for row in rows or []]
        finally:
            conn.row_factory = previous_factory
            conn.close()

    def enqueue_track_ids(
        self,
        track_ids: list[int],
        priority: str = "end",
        station_key: str = "",
    ) -> list[int]:
        normalized: list[int] = []
        for track_id in track_ids or []:
            try:
                value = int(track_id)
            except (TypeError, ValueError):
                continue
            if value > 0:
                normalized.append(value)
        if not normalized:
            return []
        resolved_station = self._station_key(station_key)
        priority_value = str(priority or "end").strip().lower() or "end"
        created_queue_ids: list[int] = []
        with self._lock_for_station(resolved_station):
            conn = self._open(resolved_station)
            try:
                conn.execute("BEGIN IMMEDIATE")
                created_at = self._deps.now().isoformat(timespec="seconds")
                if priority_value in {"next", "immediate"}:
                    row = conn.execute("SELECT COALESCE(MIN(position), 0) FROM queue_items").fetchone()
                    position = int(row[0] if row and row[0] is not None else 0)
                    iterable = list(reversed(normalized))
                    for track_id in iterable:
                        position -= 10
                        cursor = conn.execute(
                            "INSERT INTO queue_items (track_id, position, created_at) VALUES (?, ?, ?)",
                            (track_id, position, created_at),
                        )
                        created_queue_ids.append(int(cursor.lastrowid or 0))
                    created_queue_ids.reverse()
                else:
                    row = conn.execute("SELECT COALESCE(MAX(position), 0) FROM queue_items").fetchone()
                    position = int(row[0] if row and row[0] is not None else 0)
                    for track_id in normalized:
                        position += 10
                        cursor = conn.execute(
                            "INSERT INTO queue_items (track_id, position, created_at) VALUES (?, ?, ?)",
                            (track_id, position, created_at),
                        )
                        created_queue_ids.append(int(cursor.lastrowid or 0))
                conn.commit()
                return [queue_id for queue_id in created_queue_ids if queue_id > 0]
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def reorder_queue(self, queue_ids: list[int], station_key: str = "") -> int:
        normalized: list[int] = []
        seen: set[int] = set()
        for queue_id in queue_ids or []:
            try:
                value = int(queue_id)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in seen:
                normalized.append(value)
                seen.add(value)
        if not normalized:
            return 0
        resolved_station = self._station_key(station_key)
        with self._lock_for_station(resolved_station):
            conn = self._open(resolved_station)
            try:
                conn.execute("BEGIN IMMEDIATE")
                updated = 0
                for position, queue_id in enumerate(normalized, start=1):
                    updated += max(
                        0,
                        int(
                            conn.execute(
                                "UPDATE queue_items SET position = ? WHERE id = ?",
                                (position, queue_id),
                            ).rowcount
                            or 0
                        ),
                    )
                conn.commit()
                return updated
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def move_queue_items_to_front(self, queue_ids: list[int], station_key: str = "") -> bool:
        normalized: list[int] = []
        seen: set[int] = set()
        for queue_id in queue_ids or []:
            try:
                value = int(queue_id)
            except (TypeError, ValueError):
                continue
            if value > 0 and value not in seen:
                normalized.append(value)
                seen.add(value)
        if not normalized:
            return False
        conn = self._open(station_key)
        try:
            rows = conn.execute(
                "SELECT id FROM queue_items ORDER BY position ASC, id ASC"
            ).fetchall()
            all_ids = [int((row["id"] if hasattr(row, "keys") else row[0]) or 0) for row in rows or []]
        finally:
            conn.close()
        ordered = normalized + [queue_id for queue_id in all_ids if queue_id not in seen]
        return self.reorder_queue(ordered, station_key) > 0

    def remove_queue_items(self, queue_ids: list[int], station_key: str = "") -> int:
        normalized: list[int] = []
        for queue_id in queue_ids or []:
            try:
                value = int(queue_id)
            except (TypeError, ValueError):
                continue
            if value > 0:
                normalized.append(value)
        if not normalized:
            return 0
        resolved_station = self._station_key(station_key)
        with self._lock_for_station(resolved_station):
            conn = self._open(resolved_station)
            try:
                conn.execute("BEGIN IMMEDIATE")
                removed = 0
                for queue_id in normalized:
                    removed += max(
                        0,
                        int(conn.execute("DELETE FROM queue_items WHERE id = ?", (queue_id,)).rowcount or 0),
                    )
                conn.commit()
                return removed
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def get_queue_snapshot(self, station_key: str = "") -> list[dict]:
        conn = self._open(station_key)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT qi.id AS queue_id, qi.track_id AS track_id,
                       t.filename AS filename, t.path AS path
                FROM queue_items qi
                JOIN tracks t ON t.id = qi.track_id
                ORDER BY qi.position ASC, qi.id ASC
                """
            ).fetchall()
        finally:
            conn.row_factory = previous_factory
            conn.close()
        result: list[dict] = []
        for row in rows or []:
            metadata = self._deps.guess_metadata(str(row["filename"] or row["path"] or ""))
            result.append(
                {
                    "queue_id": int(row["queue_id"]),
                    "track_id": int(row["track_id"]),
                    "artist": str(metadata.get("artist") or ""),
                    "title": str(metadata.get("title") or ""),
                }
            )
        return result

    def get_recent_history_snapshot(
        self,
        cutoff_artist: Optional[str],
        cutoff_title: Optional[str],
        cutoff_track: Optional[str],
        station_key: str = "",
    ) -> list[dict]:
        cutoffs = [value for value in (cutoff_artist, cutoff_title, cutoff_track) if value]
        if not cutoffs:
            return []
        conn = self._open(station_key)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT h.track_id AS track_id, h.played_at AS played_at,
                       t.filename AS filename, t.path AS path
                FROM play_history h
                JOIN tracks t ON t.id = h.track_id
                WHERE h.played_at >= ?
                ORDER BY h.played_at DESC
                """,
                (min(cutoffs),),
            ).fetchall()
        finally:
            conn.row_factory = previous_factory
            conn.close()
        result: list[dict] = []
        for row in rows or []:
            metadata = self._deps.guess_metadata(str(row["filename"] or row["path"] or ""))
            result.append(
                {
                    "track_id": int(row["track_id"]),
                    "played_at": str(row["played_at"] or ""),
                    "artist": str(metadata.get("artist") or ""),
                    "title": str(metadata.get("title") or ""),
                }
            )
        return result

    def get_category_tracks(self, category_id: int, station_key: str = "") -> list[dict]:
        conn = self._open(station_key)
        previous_factory = conn.row_factory
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT t.id AS track_id, t.filename AS filename, t.path AS path
                FROM category_tracks ct
                JOIN tracks t ON t.id = ct.track_id
                WHERE ct.category_id = ?
                """,
                (int(category_id),),
            ).fetchall()
        finally:
            conn.row_factory = previous_factory
            conn.close()
        result: list[dict] = []
        for row in rows or []:
            metadata = self._deps.guess_metadata(str(row["filename"] or row["path"] or ""))
            result.append(
                {
                    "track_id": int(row["track_id"]),
                    "artist": str(metadata.get("artist") or ""),
                    "title": str(metadata.get("title") or ""),
                }
            )
        return result

    def enqueue_track_items(self, track_items: list[dict], station_key: str = "") -> int:
        normalized: list[dict] = []
        for item in track_items or []:
            try:
                track_id = int(item.get("track_id") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if track_id <= 0:
                continue
            normalized.append(
                {
                    "track_id": track_id,
                    "category_id": int(item.get("category_id") or 0),
                    "norules": bool(item.get("norules")),
                    "rotation_index": item.get("rotation_index"),
                    "rotation_sig": str(item.get("rotation_sig") or ""),
                }
            )
        if not normalized:
            return 0
        resolved_station = self._station_key(station_key)
        conn = self._open(resolved_station)
        try:
            row = conn.execute("SELECT COALESCE(MAX(position), 0) FROM queue_items").fetchone()
            position = int(row[0] if row and row[0] is not None else 0)
            created_at = self._deps.now().isoformat(timespec="seconds")
            for item in normalized:
                position += 10
                conn.execute(
                    """
                    INSERT INTO queue_items (
                        track_id, position, created_at, clean_transition,
                        autodj_rotation_index, autodj_rotation_category_id,
                        autodj_rotation_norules, autodj_rotation_sig
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(item["track_id"]),
                        position,
                        created_at,
                        0,
                        item.get("rotation_index"),
                        int(item["category_id"]),
                        1 if item["norules"] else 0,
                        str(item["rotation_sig"]),
                    ),
                )
            conn.commit()
            return len(normalized)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def queue_count(self, station_key: str = "") -> int:
        conn = self._open(station_key)
        try:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM queue_items").fetchone()
            if row is None:
                return 0
            return max(0, int((row["cnt"] if hasattr(row, "keys") else row[0]) or 0))
        finally:
            conn.close()

    @staticmethod
    def _normalize_metadata(value: str) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _derive_row_metadata(self, row: sqlite3.Row) -> tuple[str, str]:
        candidate = str(row["filename"] or row["path"] or "")
        metadata = self._deps.guess_metadata(candidate)
        return str(metadata.get("artist") or "").strip(), str(metadata.get("title") or "").strip()

    def _choose_queue_row(
        self,
        rows: list[sqlite3.Row],
        song: Mapping[str, Any],
        normalized_file: str,
        current_title: str,
        current_artist: str,
    ) -> tuple[Optional[sqlite3.Row], str]:
        try:
            incoming_queue_id = int(song.get("queue_id") or 0)
        except Exception:
            incoming_queue_id = 0
        try:
            incoming_track_id = int(song.get("track_id") or 0)
        except Exception:
            incoming_track_id = 0

        if incoming_queue_id > 0:
            for row in rows:
                if int(row["queue_id"] or 0) == incoming_queue_id:
                    return row, "queue_id_exact"

        if incoming_track_id > 0:
            for row in rows:
                if int(row["track_id"] or 0) != incoming_track_id:
                    continue
                path = str(row["path"] or "").strip()
                if normalized_file and path:
                    if os.path.realpath(path) == normalized_file:
                        return row, "track_id_plus_path"
                elif not normalized_file:
                    return row, "track_id_only"

        if normalized_file:
            path_matches = [row for row in rows if str(row["path"] or "").strip() and os.path.realpath(str(row["path"])) == normalized_file]
            if len(path_matches) == 1:
                return path_matches[0], "path_exact_unique"

            wanted_basename = os.path.basename(normalized_file)
            basename_matches = [
                row
                for row in rows
                if str(row["path"] or "").strip()
                and os.path.basename(os.path.realpath(str(row["path"]))) == wanted_basename
            ]
            if wanted_basename and len(basename_matches) == 1:
                return basename_matches[0], "basename_match_unique"

        if current_title or current_artist:
            for row in rows:
                artist, title = self._derive_row_metadata(row)
                normalized_title = self._normalize_metadata(title)
                normalized_artist = self._normalize_metadata(artist)
                if current_title and normalized_title and current_title == normalized_title and (
                    not current_artist or (normalized_artist and current_artist == normalized_artist)
                ):
                    return row, "metadata_match"
                if current_artist and normalized_artist and current_artist == normalized_artist and (
                    not current_title or (normalized_title and current_title == normalized_title)
                ):
                    return row, "metadata_match"

        if song.get("_no_head_fallback"):
            return None, "no_head_fallback"
        return (rows[0], "queue_head_fallback") if rows else (None, "empty_queue")

    def commit_started_track(
        self,
        song: Optional[Mapping[str, Any]],
        *,
        station_key: str,
    ) -> QueueHistoryCommitResult:
        """Atomically write history/runtime state and remove one queue item."""
        resolved_station = self._station_key(station_key)
        if not resolved_station:
            return QueueHistoryCommitResult(False, "missing_station_key", "")

        song_data: Mapping[str, Any] = song or {}
        file_path = self._deps.normalize_media_path(str(song_data.get("file") or song_data.get("path") or ""))
        normalized_file = os.path.realpath(file_path) if file_path else ""
        title = str(song_data.get("title") or "").strip()
        artist = str(song_data.get("artist") or "").strip()
        current_title = self._normalize_metadata(title)
        current_artist = self._normalize_metadata(artist)
        key = (normalized_file or f"{current_artist} - {current_title}").strip().lower()

        with self._lock_for_station(resolved_station):
            conn = self._open(resolved_station)
            previous_factory = conn.row_factory
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """
                    SELECT qi.id AS queue_id,
                           qi.track_id AS track_id,
                           t.path AS path,
                           t.filename AS filename
                    FROM queue_items qi
                    JOIN tracks t ON t.id = qi.track_id
                    ORDER BY qi.position ASC, qi.id ASC
                    """
                ).fetchall()
                if not rows:
                    conn.rollback()
                    return QueueHistoryCommitResult(False, "empty_queue", resolved_station, song_key=key, normalized_path=normalized_file)

                chosen, choice_reason = self._choose_queue_row(
                    list(rows), song_data, normalized_file, current_title, current_artist
                )
                if chosen is None:
                    conn.rollback()
                    return QueueHistoryCommitResult(
                        False,
                        choice_reason or "no_match",
                        resolved_station,
                        song_key=key,
                        normalized_path=normalized_file,
                    )

                queue_id = int(chosen["queue_id"] or 0)
                track_id = int(chosen["track_id"] or 0)
                runtime_state = self.get_runtime_state_row(conn)
                last_commit_queue_id = int(runtime_state.get("last_commit_queue_id") or 0)
                if queue_id > 0 and last_commit_queue_id == queue_id:
                    conn.rollback()
                    return QueueHistoryCommitResult(
                        False,
                        "already_committed",
                        resolved_station,
                        queue_id=queue_id,
                        track_id=track_id,
                        song_key=key,
                        normalized_path=normalized_file,
                        choice_reason=choice_reason,
                    )

                played_at = self._deps.now().isoformat(timespec="seconds")
                history_written = False
                if track_id > 0:
                    conn.execute(
                        "INSERT INTO play_history (track_id, played_at) VALUES (?, ?)",
                        (track_id, played_at),
                    )
                    history_written = True
                    conn.execute(
                        "UPDATE tracks SET play_count = COALESCE(play_count, 0) + 1 WHERE id = ?",
                        (track_id,),
                    )

                self.mark_history_commit(
                    conn,
                    song_data,
                    queue_id,
                    track_id,
                    played_at,
                    station_key=resolved_station,
                )
                deleted = conn.execute("DELETE FROM queue_items WHERE id = ?", (queue_id,)).rowcount
                if int(deleted or 0) != 1:
                    raise RuntimeError(f"queue item {queue_id} was not deleted exactly once")
                conn.commit()
                return QueueHistoryCommitResult(
                    True,
                    "committed",
                    resolved_station,
                    queue_id=queue_id,
                    track_id=track_id,
                    played_at=played_at,
                    song_key=key,
                    normalized_path=normalized_file,
                    choice_reason=choice_reason,
                    history_written=history_written,
                )
            except Exception as exc:
                conn.rollback()
                raise
            finally:
                conn.row_factory = previous_factory
                conn.close()
