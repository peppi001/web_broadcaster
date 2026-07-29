"""Framework-neutral AutoDJ queue filling and worker orchestration.

The service owns AutoDJ decisions and transient worker state. Persistence and
application side effects are supplied through callbacks so this module has no
Flask, SQLite or native-engine dependency.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import random
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class AutoDJFillResult:
    """One queue-fill attempt result."""

    added_count: int = 0
    queue_count_before: int = 0
    queue_count_after: int = 0
    keep_queue: int = 0
    rotation_count: int = 0
    notice_code: str = ""
    message: str = ""

    @property
    def filled(self) -> bool:
        return self.added_count > 0

    @property
    def success(self) -> bool:
        return self.queue_count_after > 0

    def as_startup_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "filled": self.filled,
            "added_count": int(self.added_count),
            "queue_count_before": int(self.queue_count_before),
            "queue_count_after": int(self.queue_count_after),
            "keep_queue": int(self.keep_queue),
            "rotation_count": int(self.rotation_count),
            "notice_code": str(self.notice_code or ""),
            "message": str(self.message or ""),
        }


@dataclass(frozen=True)
class AutoDJDependencies:
    """Application callbacks used by :class:`AutoDJService`."""

    get_active_station_key: Callable[[], str]
    get_settings: Callable[[], Mapping[str, Any]]
    get_rotation: Callable[[], Sequence[Mapping[str, Any]]]
    get_rotation_cursor: Callable[[list[dict[str, Any]], str], int]
    set_rotation_cursor: Callable[[int, str], None]
    get_queue_snapshot: Callable[[], list[dict[str, Any]]]
    get_recent_history_snapshot: Callable[[Optional[str], Optional[str], Optional[str]], list[dict[str, Any]]]
    get_category_tracks: Callable[[int], list[dict[str, Any]]]
    enqueue_track_items: Callable[[list[dict[str, Any]]], None]
    queue_count: Callable[[], int]
    get_now_playing: Callable[[], Mapping[str, Any]]
    publish_startup_event: Callable[[str, str, Mapping[str, Any]], None]
    replan_after_fill: Callable[[str, bool], None]
    schedule_replan_fallback: Callable[[str], None]
    native_station_state: Callable[[str], Mapping[str, Any]]
    station_runtime_context: Callable[[str], AbstractContextManager[Any]]
    log_exception: Callable[[str, BaseException], None]


class AutoDJService:
    """Station-scoped AutoDJ business logic and background worker owner."""

    def __init__(self, dependencies: AutoDJDependencies) -> None:
        self._deps = dependencies
        self._thread_lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._notice_lock = threading.RLock()
        self._notices: dict[str, dict[str, Any]] = {}
        self._wake = threading.Event()

    @staticmethod
    def rotation_signature(rotation: Sequence[Mapping[str, Any]]) -> str:
        parts: list[str] = []
        for position, entry in enumerate(rotation or []):
            try:
                category_id = int(entry.get("category_id") or 0)
            except (TypeError, ValueError):
                category_id = 0
            norules = 1 if entry.get("norules") else 0
            parts.append(f"{position}:{category_id}:{norules}")
        return "|".join(parts)

    @staticmethod
    def normalize_text(value: str) -> str:
        text = str(value or "").strip().lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _minutes(settings: Mapping[str, Any], key: str) -> int:
        try:
            return max(0, int(settings.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    def set_notice(self, station_key: str, code: str, message: str) -> None:
        station = str(station_key or "").strip()
        with self._notice_lock:
            self._notices[station] = {
                "code": str(code or ""),
                "message": str(message or ""),
                "ts": time.time(),
            }

    def clear_notice(self, station_key: str) -> None:
        station = str(station_key or "").strip()
        with self._notice_lock:
            self._notices.pop(station, None)

    def get_notice(self, station_key: str) -> Optional[dict[str, Any]]:
        station = str(station_key or "").strip()
        with self._notice_lock:
            notice = self._notices.get(station)
            return dict(notice) if notice else None

    def wake(self) -> None:
        self._wake.set()

    def worker_alive(self, station_key: str) -> bool:
        station = str(station_key or "").strip()
        with self._thread_lock:
            worker = self._threads.get(station)
            return bool(worker and worker.is_alive())

    def _pick_track_for_category(
        self,
        category_id: int,
        settings: Mapping[str, Any],
        *,
        norules: bool = False,
        excluded_track_ids: Optional[set[int]] = None,
    ) -> Optional[int]:
        queue_rows = list(self._deps.get_queue_snapshot() or [])
        queue_track_ids = {int(row.get("track_id") or 0) for row in queue_rows}
        queue_track_ids.discard(0)
        queue_track_ids.update(int(value) for value in (excluded_track_ids or set()))

        candidates = list(self._deps.get_category_tracks(int(category_id)) or [])
        if not candidates:
            return None
        random.shuffle(candidates)

        if norules:
            eligible = [
                int(row.get("track_id") or 0)
                for row in candidates
                if int(row.get("track_id") or 0) > 0
                and int(row.get("track_id") or 0) not in queue_track_ids
            ]
            return int(random.choice(eligible)) if eligible else None

        no_artist = self._minutes(settings, "no_repeat_artist_minutes")
        no_title = self._minutes(settings, "no_repeat_title_minutes")
        no_track = self._minutes(settings, "no_repeat_track_minutes")
        now = datetime.now()
        cutoff_artist = (now - timedelta(minutes=no_artist)).isoformat(timespec="seconds") if no_artist else None
        cutoff_title = (now - timedelta(minutes=no_title)).isoformat(timespec="seconds") if no_title else None
        cutoff_track = (now - timedelta(minutes=no_track)).isoformat(timespec="seconds") if no_track else None

        queue_artists = {
            self.normalize_text(str(row.get("artist") or ""))
            for row in queue_rows
            if row.get("artist")
        }
        queue_titles = {
            self.normalize_text(str(row.get("title") or ""))
            for row in queue_rows
            if row.get("title")
        }
        history_rows = list(
            self._deps.get_recent_history_snapshot(cutoff_artist, cutoff_title, cutoff_track) or []
        )
        recent_track_ids: set[int] = set()
        recent_artists: set[str] = set()
        recent_titles: set[str] = set()
        for row in history_rows:
            played_at = str(row.get("played_at") or "")
            if cutoff_track and played_at >= cutoff_track:
                recent_track_ids.add(int(row.get("track_id") or 0))
            if cutoff_artist and played_at >= cutoff_artist and row.get("artist"):
                recent_artists.add(self.normalize_text(str(row.get("artist") or "")))
            if cutoff_title and played_at >= cutoff_title and row.get("title"):
                recent_titles.add(self.normalize_text(str(row.get("title") or "")))

        now_playing = dict(self._deps.get_now_playing() or {})
        current_artist = self.normalize_text(str(now_playing.get("artist") or ""))
        current_title = self.normalize_text(str(now_playing.get("title") or ""))
        if current_artist:
            recent_artists.add(current_artist)
        if current_title:
            recent_titles.add(current_title)

        for row in candidates:
            track_id = int(row.get("track_id") or 0)
            if track_id <= 0 or track_id in queue_track_ids:
                continue
            if no_track and track_id in recent_track_ids:
                continue
            artist = self.normalize_text(str(row.get("artist") or ""))
            title = self.normalize_text(str(row.get("title") or ""))
            if no_artist and artist and (artist in queue_artists or artist in recent_artists):
                continue
            if no_title and title and (title in queue_titles or title in recent_titles):
                continue
            return track_id
        return None

    def fill_queue_once(self, *, replan_after_fill: bool = True) -> bool:
        settings = dict(self._deps.get_settings() or {})
        try:
            keep_queue = max(0, int(settings.get("keep_queue") or 0))
        except (TypeError, ValueError):
            keep_queue = 0
        station = str(self._deps.get_active_station_key() or "").strip()
        if keep_queue <= 0:
            return False

        rotation_raw = list(self._deps.get_rotation() or [])
        rotation = [
            {
                "category_id": int(item.get("category_id")),
                "norules": bool(int(item.get("norules") or 0)),
            }
            for item in rotation_raw
            if item.get("category_id") is not None
        ]
        if not rotation:
            return False

        queue_before = max(0, int(self._deps.queue_count() or 0))
        signature = self.rotation_signature(rotation)
        start_index = int(self._deps.get_rotation_cursor(rotation, signature) or 0)
        if queue_before >= keep_queue:
            return False

        needed = keep_queue - queue_before
        picked: list[dict[str, Any]] = []
        picked_track_ids: set[int] = set()
        index = start_index
        attempts = 0
        max_attempts = max(len(rotation), len(rotation) * max(1, needed))
        while len(picked) < needed and attempts < max_attempts:
            rotation_index = index % len(rotation)
            entry = rotation[rotation_index]
            index += 1
            attempts += 1
            track_id = self._pick_track_for_category(
                int(entry["category_id"]),
                settings,
                norules=bool(entry["norules"]),
                excluded_track_ids=picked_track_ids,
            )
            if track_id is None:
                continue
            picked_track_ids.add(track_id)
            picked.append(
                {
                    "track_id": track_id,
                    "category_id": int(entry["category_id"]),
                    "norules": bool(entry["norules"]),
                    "rotation_index": rotation_index,
                    "rotation_sig": signature,
                }
            )

        self._deps.set_rotation_cursor(index, signature)
        if not picked:
            self.set_notice(
                station,
                "NO_MATCHING_TRACKS",
                "AutoDJ could not find any track that matches the current rules/library. "
                "Add more music or relax the rules.",
            )
            return False

        self._deps.enqueue_track_items(picked)
        if replan_after_fill:
            try:
                self._deps.replan_after_fill("autodj_refill", True)
            except Exception:
                try:
                    self._deps.schedule_replan_fallback("autodj_refill_fallback")
                except Exception:
                    pass
        self.wake()
        self.clear_notice(station)
        return True

    def startup_fill_once(self, station_key: str) -> dict[str, Any]:
        station = str(station_key or self._deps.get_active_station_key() or "").strip()
        settings = dict(self._deps.get_settings() or {})
        rotation = list(self._deps.get_rotation() or [])
        try:
            keep_queue = max(0, int(settings.get("keep_queue") or 0))
        except (TypeError, ValueError):
            keep_queue = 0
        rotation_count = len(rotation)
        queue_before = max(0, int(self._deps.queue_count() or 0))
        self._deps.publish_startup_event(
            "autodj_startup_fill_requested",
            station,
            {
                "queue_count_before": queue_before,
                "keep_queue": keep_queue,
                "rotation_count": rotation_count,
            },
        )
        try:
            filled = bool(self.fill_queue_once(replan_after_fill=False))
        except Exception as exc:
            queue_after = max(0, int(self._deps.queue_count() or 0))
            result = AutoDJFillResult(
                added_count=max(0, queue_after - queue_before),
                queue_count_before=queue_before,
                queue_count_after=queue_after,
                keep_queue=keep_queue,
                rotation_count=rotation_count,
                notice_code="worker_exception",
                message=f"AutoDJ startup fill failed: {type(exc).__name__}: {exc}",
            )
            payload = result.as_startup_dict()
            self._deps.publish_startup_event(
                "autodj_startup_fill_failed", station, {**payload, "error": result.message}
            )
            return payload

        queue_after = max(0, int(self._deps.queue_count() or 0))
        if queue_after > 0:
            result = AutoDJFillResult(
                added_count=max(0, queue_after - queue_before) if filled else 0,
                queue_count_before=queue_before,
                queue_count_after=queue_after,
                keep_queue=keep_queue,
                rotation_count=rotation_count,
            )
            payload = result.as_startup_dict()
            self._deps.publish_startup_event("autodj_startup_fill_completed", station, payload)
            return payload

        notice = self.get_notice(station) or {}
        notice_code = str(notice.get("code") or "").strip().lower()
        message = str(notice.get("message") or "").strip()
        if keep_queue <= 0:
            notice_code = notice_code or "keep_queue_disabled"
            message = message or "AutoDJ queue filling is disabled because keep_queue is zero."
        elif rotation_count <= 0:
            notice_code = notice_code or "rotation_empty"
            message = message or "AutoDJ has no rotation categories to select from."
        else:
            notice_code = notice_code or "no_playable_tracks"
            message = message or "AutoDJ could not add a playable track from the current library and rules."
        result = AutoDJFillResult(
            added_count=0,
            queue_count_before=queue_before,
            queue_count_after=queue_after,
            keep_queue=keep_queue,
            rotation_count=rotation_count,
            notice_code=notice_code,
            message=message,
        )
        payload = result.as_startup_dict()
        self._deps.publish_startup_event(
            "autodj_startup_fill_failed", station, {**payload, "error": message}
        )
        return payload

    def run_loop(self, station_key: str = "") -> None:
        station = str(station_key or self._deps.get_active_station_key() or "").strip()
        if not station:
            return
        try:
            with self._deps.station_runtime_context(station):
                time.sleep(0.5)
                while bool(self._deps.native_station_state(station).get("running")):
                    try:
                        if self.fill_queue_once():
                            continue
                    except Exception:
                        pass
                    self._wake.wait(timeout=2.0)
                    self._wake.clear()
        except Exception as exc:
            self._deps.log_exception(station, exc)
        finally:
            with self._thread_lock:
                current = self._threads.get(station)
                if current is threading.current_thread():
                    self._threads.pop(station, None)

    def start_thread(self, station_key: str = "") -> None:
        station = str(station_key or self._deps.get_active_station_key() or "").strip()
        if not station:
            return
        with self._thread_lock:
            existing = self._threads.get(station)
            if existing is not None and existing.is_alive():
                return
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.basename(station)) or "station"
            worker = threading.Thread(
                target=self.run_loop,
                args=(station,),
                name=f"native-autodj-{safe_name}",
                daemon=True,
            )
            self._threads[station] = worker
            worker.start()
