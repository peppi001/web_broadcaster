"""Normalized event model for the authoritative native audio engine."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .protocol import PROTOCOL_VERSION, JsonlProtocolLogger

STANDARD_ENGINE_EVENTS = frozenset({
    "engine_ready",
    "deck_load_planned",
    "deck_load_cancelled",
    "deck_loaded",
    "track_started",
    "track_progress",
    "track_seeked",
    "cue_out_reached",
    "transition_started",
    "transition_finished",
    "track_ended",
    "early_eof",
    "metadata_changed",
    "dsp_timeout",
    "dsp_recovered",
    "icecast_connected",
    "icecast_disconnected",
    "engine_error",
    "native_resource_snapshot",
    "native_position_drift_snapshot",
    "native_audio_probe_seek_restarting",
    "native_audio_probe_seek_pending",
    "native_audio_probe_seek_applied",
    "native_audio_probe_seek_slow",
    "native_output_underrun",
    "native_transition_early_eof_handled",
    "native_active_early_eof_handled",
    "native_active_terminal_eof_handled",
    "native_audio_probe_handoff_primed",
    "native_hard_handoff_armed",
})

TRACK_SCOPED_EVENTS = frozenset({
    "deck_load_planned",
    "deck_load_cancelled",
    "deck_loaded",
    "track_started",
    "track_progress",
    "track_seeked",
    "cue_out_reached",
    "transition_started",
    "transition_finished",
    "track_ended",
    "early_eof",
    "metadata_changed",
    "native_position_drift_snapshot",
    "native_audio_probe_seek_restarting",
    "native_audio_probe_seek_pending",
    "native_audio_probe_seek_applied",
    "native_audio_probe_seek_slow",
    "native_output_underrun",
    "native_transition_early_eof_handled",
    "native_active_early_eof_handled",
    "native_active_terminal_eof_handled",
    "native_audio_probe_handoff_primed",
    "native_hard_handoff_armed",
})


def normalize_deck(deck: str) -> str:
    value = str(deck or "").strip().upper()
    if value.endswith("A"):
        return "A"
    if value.endswith("B"):
        return "B"
    return ""


@dataclass(frozen=True, slots=True)
class EngineEvent:
    event: str
    engine: str
    station_key: str = ""
    queue_id: int = 0
    slot_token: str = ""
    deck: str = ""
    track_id: int = 0
    path: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    monotonic_time_ms: int = field(default_factory=lambda: int(round(time.monotonic() * 1000.0)))
    wall_time_unix_ms: int = field(default_factory=lambda: int(round(time.time() * 1000.0)))

    def __post_init__(self) -> None:
        object.__setattr__(self, "event", str(self.event or "").strip())
        object.__setattr__(self, "engine", str(self.engine or "unknown").strip())
        object.__setattr__(self, "station_key", str(self.station_key or "").strip())
        object.__setattr__(self, "queue_id", max(0, int(self.queue_id or 0)))
        object.__setattr__(self, "track_id", max(0, int(self.track_id or 0)))
        object.__setattr__(self, "slot_token", str(self.slot_token or "").strip())
        object.__setattr__(self, "deck", normalize_deck(self.deck))
        object.__setattr__(self, "path", str(self.path or ""))
        object.__setattr__(self, "payload", dict(self.payload or {}))
        if not self.event:
            raise ValueError("engine event name must not be empty")

    def to_record(self) -> dict[str, Any]:
        wall_iso = datetime.fromtimestamp(self.wall_time_unix_ms / 1000.0, timezone.utc).isoformat(timespec="milliseconds")
        return {
            "version": PROTOCOL_VERSION,
            "event": self.event,
            "station_key": self.station_key,
            "queue_id": self.queue_id,
            "slot_token": self.slot_token,
            "deck": self.deck,
            "track_id": self.track_id,
            "path": self.path,
            "event_monotonic_time_ms": self.monotonic_time_ms,
            "event_wall_time_unix_ms": self.wall_time_unix_ms,
            "event_wall_time_utc": wall_iso,
            "payload": dict(self.payload),
        }


class EngineEventBus:
    """In-process event fan-out plus the authoritative JSONL capture."""

    def __init__(self, *, engine_name: str, protocol_logger: JsonlProtocolLogger) -> None:
        self.engine_name = str(engine_name or "unknown")
        self.protocol_logger = protocol_logger
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[EngineEvent], Any]] = []

    def subscribe(self, callback: Callable[[EngineEvent], Any]) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("event subscriber must be callable")
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def publish(
        self,
        event: str,
        *,
        station_key: str = "",
        queue_id: int = 0,
        slot_token: str = "",
        deck: str = "",
        track_id: int = 0,
        path: str = "",
        payload: Mapping[str, Any] | None = None,
    ) -> EngineEvent:
        item = EngineEvent(
            event=event,
            engine=self.engine_name,
            station_key=station_key,
            queue_id=queue_id,
            slot_token=slot_token,
            deck=deck,
            track_id=track_id,
            path=path,
            payload=dict(payload or {}),
        )
        self.protocol_logger.event(item.to_record())
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(item)
            except Exception:
                # One observer must never prevent the others or affect playback.
                continue
        return item
