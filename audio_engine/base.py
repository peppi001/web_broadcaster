"""Stable contract shared by the current and future audio backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .events import EngineEvent


@dataclass(frozen=True, slots=True)
class TrackDescriptor:
    """Complete identity and playback description of one queued track."""

    queue_id: int
    slot_token: str
    path: str
    cue_in_ms: int = 0
    cue_out_ms: int = 0
    audio_start_ms: int = 0
    play_start_ms: int = 0
    transition_at_ms: int = 0
    effective_end_ms: int = 0
    source_end_ms: int = 0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    analysis_requested: bool = False
    manual_timing: bool = False
    hard_clean: bool = False
    short_no_crossfade: bool = False
    analysis_window_ms: int = 10
    analysis_sustain_ms: int = 30
    analysis_artifact_max_ms: int = 300
    analysis_artifact_silence_ms: int = 250
    no_crossfade_max_duration_ms: int = 65000
    crossfade_fallback_ms: int = 3000
    crossfade_min_ms: int = 100
    crossfade_max_ms: int = 6000
    gap_start_threshold_dbfs: float = -20.0
    gap_end_threshold_dbfs: float = -24.0
    crossfade_trigger_relative_db: float = -7.0
    artist: str = ""
    title: str = ""
    year: str = ""


class AudioEngine(ABC):
    """Behavior-preserving facade for the real-time audio backend.

    Command methods deliberately return ``Any`` in the compatibility phase.
    The existing Flask handlers return Response objects and ``(Response, code)``
    tuples; retaining them exactly prevents the refactor from changing the HTTP
    contract. The normalized event interface is already stable for the future
    native backend.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Configured backend name."""
        raise NotImplementedError

    @property
    @abstractmethod
    def protocol_log_path(self) -> str:
        """Absolute JSONL protocol-log path, or an empty string when disabled."""
        raise NotImplementedError

    @abstractmethod
    def publish_event(
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
        raise NotImplementedError

    @abstractmethod
    def subscribe_events(self, callback: Callable[[EngineEvent], Any]) -> Callable[[], None]:
        """Subscribe to normalized events and return an unsubscribe callback."""
        raise NotImplementedError

    @abstractmethod
    def start(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def reload(self) -> Any:
        raise NotImplementedError

    def set_paused(self, paused: bool, *, station_key: str = "") -> Any:
        """Pause or resume playback while keeping the output stream alive."""
        raise NotImplementedError

    @abstractmethod
    def get_state(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def load_deck(
        self,
        deck: str,
        uri: str,
        *,
        attempts: int = 8,
        retry_delay: float = 0.35,
        clear_slot: bool = False,
        manual_next_fast: bool = False,
    ) -> bool:
        """Load one source into an A/B deck.

        ``uri`` is the canonical annotated descriptor transported by the Python-to-native
        protocol. The native backend converts it to a typed TrackDescriptor.
        """
        raise NotImplementedError

    @abstractmethod
    def select_deck(self, deck: str, *, timeout_sec: float = 1.0) -> Any:
        """Immediately select one A/B deck and preserve the backend reply."""
        raise NotImplementedError

    @abstractmethod
    def transition_to(
        self,
        deck: str,
        duration: float,
        *,
        timeout_sec: float = 1.0,
    ) -> Any:
        """Transition to one A/B deck and preserve the backend reply."""
        raise NotImplementedError
