"""Thread-safe identity support for normalized audio-engine lifecycle telemetry.

Live engine events contain transient queue/deck identity that is not always
present in the cached descriptor.  This registry keeps the last
confirmed identity per station/deck and supplies only missing fields when the
candidate still refers to the same track.  It also suppresses immediately
repeated ``deck_loaded`` telemetry without hiding legitimate later reloads.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Mapping

from .events import normalize_deck


_IDENTITY_FIELDS = (
    "deck",
    "queue_id",
    "track_id",
    "slot_token",
    "path",
    "artist",
    "title",
    "year",
)


def _normalize_identity(identity: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(identity or {})
    return {
        "deck": normalize_deck(str(source.get("deck") or "")),
        "queue_id": max(0, int(source.get("queue_id") or 0)),
        "track_id": max(0, int(source.get("track_id") or 0)),
        "slot_token": str(source.get("slot_token") or "").strip(),
        "path": str(source.get("path") or ""),
        "artist": str(source.get("artist") or "").strip(),
        "title": str(source.get("title") or "").strip(),
        "year": str(source.get("year") or "").strip(),
    }


def _same_track(candidate: Mapping[str, Any], confirmed: Mapping[str, Any]) -> bool:
    candidate_deck = normalize_deck(str(candidate.get("deck") or ""))
    confirmed_deck = normalize_deck(str(confirmed.get("deck") or ""))
    if candidate_deck and confirmed_deck and candidate_deck != confirmed_deck:
        return False

    candidate_queue = max(0, int(candidate.get("queue_id") or 0))
    confirmed_queue = max(0, int(confirmed.get("queue_id") or 0))
    if candidate_queue and confirmed_queue:
        return candidate_queue == confirmed_queue

    candidate_token = str(candidate.get("slot_token") or "").strip()
    confirmed_token = str(confirmed.get("slot_token") or "").strip()
    if candidate_token and confirmed_token:
        return candidate_token == confirmed_token

    candidate_path = str(candidate.get("path") or "")
    confirmed_path = str(confirmed.get("path") or "")
    if candidate_path and confirmed_path:
        return candidate_path == confirmed_path

    candidate_track = max(0, int(candidate.get("track_id") or 0))
    confirmed_track = max(0, int(confirmed.get("track_id") or 0))
    return bool(candidate_track and confirmed_track and candidate_track == confirmed_track)


class EngineLifecycleIdentityRegistry:
    """Remember confirmed deck identities and deduplicate immediate load events."""

    def __init__(self, *, deck_loaded_dedupe_seconds: float = 2.0) -> None:
        self.deck_loaded_dedupe_seconds = max(0.0, float(deck_loaded_dedupe_seconds))
        self._lock = threading.RLock()
        self._confirmed_by_station: dict[str, dict[str, dict[str, Any]]] = {}
        self._last_deck_loaded: dict[tuple[str, str], tuple[str, float]] = {}

    def remember_confirmed(
        self,
        station_key: str,
        identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = _normalize_identity(identity)
        station = str(station_key or "").strip()
        deck = str(normalized.get("deck") or "")
        if not station or not deck:
            return normalized
        with self._lock:
            station_state = self._confirmed_by_station.setdefault(station, {})
            station_state[deck] = dict(normalized)
        return normalized

    def resolve_confirmed(
        self,
        station_key: str,
        identity: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Fill missing fields from the same confirmed station/deck identity.

        Existing non-empty candidate values are never replaced.  A fallback is
        used only when queue/token/path/track evidence shows that both records
        describe the same track, preventing a recycled deck from donating the
        identity of a newly preloaded item to an older ``track_ended`` event.
        """

        candidate = _normalize_identity(identity)
        station = str(station_key or "").strip()
        deck = str(candidate.get("deck") or "")
        if not station or not deck:
            return candidate
        with self._lock:
            confirmed = dict(
                (self._confirmed_by_station.get(station) or {}).get(deck) or {}
            )
        if not confirmed or not _same_track(candidate, confirmed):
            return candidate

        merged = dict(candidate)
        for field in _IDENTITY_FIELDS:
            if field in {"queue_id", "track_id"}:
                if int(merged.get(field) or 0) <= 0:
                    merged[field] = int(confirmed.get(field) or 0)
            elif not str(merged.get(field) or ""):
                merged[field] = confirmed.get(field) or ""
        return _normalize_identity(merged)

    def accept_deck_loaded(
        self,
        station_key: str,
        identity: Mapping[str, Any] | None,
        *,
        monotonic_time: float | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Remember a confirmed load and decide whether telemetry should emit."""

        normalized = self.remember_confirmed(station_key, identity)
        station = str(station_key or "").strip()
        deck = str(normalized.get("deck") or "")
        now = time.monotonic() if monotonic_time is None else float(monotonic_time)
        signature = "|".join(
            (
                deck,
                str(int(normalized.get("queue_id") or 0)),
                str(int(normalized.get("track_id") or 0)),
                str(normalized.get("slot_token") or ""),
                str(normalized.get("path") or ""),
            )
        )
        key = (station, deck)
        with self._lock:
            previous = self._last_deck_loaded.get(key)
            self._last_deck_loaded[key] = (signature, now)
        if previous is None:
            return True, normalized
        previous_signature, previous_time = previous
        duplicate = (
            signature == previous_signature
            and now >= previous_time
            and (now - previous_time) <= self.deck_loaded_dedupe_seconds
        )
        return (not duplicate), normalized

    def clear(self) -> None:
        with self._lock:
            self._confirmed_by_station.clear()
            self._last_deck_loaded.clear()


class NativeLifecycleCoordinator:
    """Own native event subscription, ordering, deduplication and dispatch.

    The coordinator is deliberately framework-neutral.  Application-specific
    queue/history/AutoDJ decisions remain callbacks supplied by ``app.py``;
    this class owns only the asynchronous lifecycle mechanics that must stay
    ordered and non-blocking for the native socket reader.
    """

    _TRACK_STARTED_SOURCES = frozenset(
        {
            "native_select_command",
            "native_transition_command",
            "native_timing_transition",
            "native_hard_handoff_boundary",
            "native_terminal_recovery",
        }
    )
    _SEEK_PENDING_EVENTS = frozenset(
        {
            "track_seeked",
            "native_audio_probe_seek_restarting",
            "native_audio_probe_seek_pending",
        }
    )
    _HARD_HANDOFF_EOF_EVENTS = frozenset(
        {
            "native_active_early_eof_handled",
            "native_active_terminal_eof_handled",
            "native_audio_probe_early_eof",
        }
    )

    def __init__(
        self,
        *,
        process_track_started,
        track_started_signature,
        load_requested_next_track,
        mark_seek_pending,
        mark_seek_applied,
        reconcile_transition_finished,
        mark_hard_handoff_claimed,
        mark_hard_handoff_completed,
        signal_monitor_wake,
        report_exception=None,
        queue_size: int = 512,
        track_retry_count: int = 2,
        track_done_ttl_seconds: float = 600.0,
    ) -> None:
        import queue

        self._queue_module = queue
        self._process_track_started = process_track_started
        self._track_started_signature = track_started_signature
        self._load_requested_next_track = load_requested_next_track
        self._mark_seek_pending = mark_seek_pending
        self._mark_seek_applied = mark_seek_applied
        self._reconcile_transition_finished = reconcile_transition_finished
        self._mark_hard_handoff_claimed = mark_hard_handoff_claimed
        self._mark_hard_handoff_completed = mark_hard_handoff_completed
        self._signal_monitor_wake = signal_monitor_wake
        self._report_exception = report_exception
        self._track_retry_count = max(0, int(track_retry_count))
        self._track_done_ttl_seconds = max(1.0, float(track_done_ttl_seconds))

        self._track_queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._lock = threading.RLock()
        self._track_pending: set[str] = set()
        self._track_done: dict[str, float] = {}
        self._track_queue_id_pending: set[str] = set()
        self._track_queue_id_done: dict[str, float] = {}
        self._next_load_inflight: set[str] = set()
        self._worker_started = False
        self._unsubscribe = None

    @staticmethod
    def _station_key(event) -> str:
        return str(getattr(event, "station_key", "") or "")

    @staticmethod
    def _queue_id(event) -> int:
        return int(getattr(event, "queue_id", 0) or 0)

    @staticmethod
    def _slot_token(event) -> str:
        return str(getattr(event, "slot_token", "") or "")

    @staticmethod
    def _deck(event) -> str:
        return str(getattr(event, "deck", "") or "")

    def _report(self, message: str, exc: Exception) -> None:
        callback = self._report_exception
        if callable(callback):
            try:
                callback(message, exc)
                return
            except Exception:
                pass
        return


    def _wake(self, station_key: str, *, reason: str) -> None:
        try:
            self._signal_monitor_wake(station_key, reason=reason)
        except Exception:
            pass

    def _run_next_load(self, event, request_key: str) -> None:
        try:
            self._load_requested_next_track(event, request_key)
        finally:
            with self._lock:
                self._next_load_inflight.discard(request_key)

    def _dispatch_need_next(self, event, payload: Mapping[str, Any]) -> None:
        station_key = self._station_key(event)
        request_key = "|".join(
            (
                station_key,
                str(self._queue_id(event)),
                self._slot_token(event),
            )
        )
        with self._lock:
            if request_key in self._next_load_inflight:
                return
            self._next_load_inflight.add(request_key)
        thread = threading.Thread(
            target=self._run_next_load,
            args=(event, request_key),
            name=f"native-next-{station_key or 'station'}",
            daemon=True,
        )
        thread.start()

    def _cleanup_track_done_locked(self, now: float) -> None:
        ttl = self._track_done_ttl_seconds
        for old_signature, old_ts in list(self._track_done.items()):
            if now - float(old_ts or 0.0) > ttl:
                self._track_done.pop(old_signature, None)
        for old_identity, old_ts in list(self._track_queue_id_done.items()):
            if now - float(old_ts or 0.0) > ttl:
                self._track_queue_id_done.pop(old_identity, None)

    def _queue_track_started(self, event) -> None:
        station_key = self._station_key(event)
        signature = str(self._track_started_signature(event) or "")
        queue_id = self._queue_id(event)
        queue_identity = f"{station_key}|{queue_id}" if station_key and queue_id > 0 else ""
        now = time.monotonic()
        with self._lock:
            self._cleanup_track_done_locked(now)
            if signature in self._track_pending or signature in self._track_done:
                return
            if queue_identity and (
                queue_identity in self._track_queue_id_pending
                or queue_identity in self._track_queue_id_done
            ):
                return
            self._track_pending.add(signature)
            if queue_identity:
                self._track_queue_id_pending.add(queue_identity)
        try:
            self._track_queue.put_nowait((event, signature, queue_identity, 0))
        except self._queue_module.Full:
            with self._lock:
                self._track_pending.discard(signature)
                if queue_identity:
                    self._track_queue_id_pending.discard(queue_identity)

    def handle_event(self, event) -> None:
        """Route one native event without blocking the socket reader."""
        try:
            event_name = str(getattr(event, "event", "") or "")
            payload = dict(getattr(event, "payload", {}) or {})
            station_key = self._station_key(event)

            if event_name == "native_need_next_track":
                self._dispatch_need_next(event, payload)
                return

            if event_name in self._SEEK_PENDING_EVENTS:
                pending = bool(self._mark_seek_pending(event))
                self._wake(station_key, reason=event_name)
                return

            if event_name == "native_audio_probe_seek_applied":
                applied = bool(self._mark_seek_applied(event))
                self._wake(station_key, reason=event_name)
                return

            if event_name == "transition_finished":
                reconciled = bool(self._reconcile_transition_finished(event))
                self._wake(station_key, reason=event_name)
                return

            if event_name in self._HARD_HANDOFF_EOF_EVENTS:
                claimed = bool(self._mark_hard_handoff_claimed(event))
                self._wake(station_key, reason=event_name)
                if claimed:
                    pass
                return

            if event_name != "track_started":
                return
            source = str(payload.get("source") or "")
            if source not in self._TRACK_STARTED_SOURCES:
                return
            if source == "native_hard_handoff_boundary":
                completed = bool(self._mark_hard_handoff_completed(event))
                self._wake(station_key, reason="native_hard_handoff_boundary")
            self._queue_track_started(event)
        except Exception as exc:
            self._report("native lifecycle callback failed", exc)

    def _track_worker(self) -> None:
        while True:
            event, signature, queue_identity, attempt = self._track_queue.get()
            success = False
            try:
                if int(attempt or 0) == 0:
                    time.sleep(0.10)
                success = bool(self._process_track_started(event))
            except Exception as exc:
                self._report("native track_started lifecycle failed", exc)
            if not success and int(attempt or 0) < self._track_retry_count:
                time.sleep(0.20 * (int(attempt or 0) + 1))
                self._track_queue.put(
                    (event, signature, queue_identity, int(attempt or 0) + 1)
                )
                continue
            with self._lock:
                self._track_pending.discard(signature)
                if queue_identity:
                    self._track_queue_id_pending.discard(queue_identity)
                if success:
                    completed_at = time.monotonic()
                    self._track_done[signature] = completed_at
                    if queue_identity:
                        self._track_queue_id_done[queue_identity] = completed_at

    def start(self, engine, *, event_handler=None) -> None:
        """Subscribe once and start the single ordered lifecycle worker."""
        with self._lock:
            if self._unsubscribe is None:
                self._unsubscribe = engine.subscribe_events(event_handler or self.handle_event)
            if self._worker_started:
                return
            self._worker_started = True
            thread = threading.Thread(
                target=self._track_worker,
                name="native-track-started-1",
                daemon=True,
            )
            thread.start()
