"""Process-wide native audio-engine runtime ownership.

This module owns the lazy engine singleton, thread-scoped station context and
normalized application-to-engine telemetry.  Flask routes and playback
business logic remain in ``app.py``; daemon transport stays in
``native_engine.py``.
"""

from __future__ import annotations

import contextlib
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .base import AudioEngine
from .factory import create_audio_engine
from .lifecycle import EngineLifecycleIdentityRegistry


@dataclass(slots=True)
class _RuntimeConfig:
    app_version: str = "unknown"
    app_root: str = ""
    protocol_log_path: str = ""
    station_key_resolver: Callable[[], str] | None = None
    log_callback: Callable[[str], Any] | None = None


_AUDIO_ENGINE_INSTANCE: AudioEngine | None = None
_AUDIO_ENGINE_INSTANCE_LOCK = threading.RLock()
_STATION_CONTEXT_LOCAL = threading.local()
_RUNTIME_CONFIG = _RuntimeConfig()
_ENGINE_LIFECYCLE_IDENTITIES = EngineLifecycleIdentityRegistry(
    deck_loaded_dedupe_seconds=2.0
)


def configure_audio_engine_runtime(
    *,
    app_version: str,
    app_root: str = "",
    protocol_log_path: str = "",
    station_key_resolver: Callable[[], str] | None = None,
    log_callback: Callable[[str], Any] | None = None,
) -> None:
    """Configure lazy process-wide engine construction.

    Configuration is intentionally side-effect free: the native daemon is not
    connected or started until :func:`get_audio_engine` is first called.
    """
    normalized_root = str(Path(app_root).resolve()) if str(app_root or "").strip() else ""
    normalized_log = str(protocol_log_path or "").strip()
    if not normalized_log and normalized_root:
        normalized_log = str(Path(normalized_root) / "logs" / "audio_engine_protocol.jsonl")

    with _AUDIO_ENGINE_INSTANCE_LOCK:
        _RUNTIME_CONFIG.app_version = str(app_version or "unknown").strip() or "unknown"
        _RUNTIME_CONFIG.app_root = normalized_root
        _RUNTIME_CONFIG.protocol_log_path = normalized_log
        _RUNTIME_CONFIG.station_key_resolver = station_key_resolver
        _RUNTIME_CONFIG.log_callback = log_callback


def get_audio_engine() -> AudioEngine:
    """Return the process-wide authoritative native audio engine."""
    global _AUDIO_ENGINE_INSTANCE
    if _AUDIO_ENGINE_INSTANCE is not None:
        return _AUDIO_ENGINE_INSTANCE

    with _AUDIO_ENGINE_INSTANCE_LOCK:
        if _AUDIO_ENGINE_INSTANCE is None:
            config = _RUNTIME_CONFIG
            protocol_log_path = str(config.protocol_log_path or "").strip()
            if not protocol_log_path:
                root = config.app_root or os.getcwd()
                protocol_log_path = os.path.join(root, "logs", "audio_engine_protocol.jsonl")
            _AUDIO_ENGINE_INSTANCE = create_audio_engine(
                log_callback=config.log_callback,
                protocol_log_path=protocol_log_path,
                app_version=config.app_version,
                station_key_resolver=config.station_key_resolver,
            )
        return _AUDIO_ENGINE_INSTANCE


def close_audio_engine_runtime() -> None:
    """Close and forget the process-wide engine instance."""
    global _AUDIO_ENGINE_INSTANCE
    with _AUDIO_ENGINE_INSTANCE_LOCK:
        engine = _AUDIO_ENGINE_INSTANCE
        _AUDIO_ENGINE_INSTANCE = None
    if engine is not None:
        close = getattr(engine, "close", None)
        if callable(close):
            close()


def _reset_audio_engine_runtime_for_tests() -> None:
    """Reset module state for isolated unit tests."""
    close_audio_engine_runtime()
    with _AUDIO_ENGINE_INSTANCE_LOCK:
        _RUNTIME_CONFIG.app_version = "unknown"
        _RUNTIME_CONFIG.app_root = ""
        _RUNTIME_CONFIG.protocol_log_path = ""
        _RUNTIME_CONFIG.station_key_resolver = None
        _RUNTIME_CONFIG.log_callback = None


def station_runtime_override() -> str:
    """Return the thread-scoped station database filename, if any."""
    return os.path.basename(
        str(getattr(_STATION_CONTEXT_LOCAL, "station_key", "") or "").strip()
    )


@contextlib.contextmanager
def station_runtime_context(station_key: str):
    """Pin station-scoped helpers to one station for the current thread."""
    previous = getattr(_STATION_CONTEXT_LOCAL, "station_key", "")
    normalized = os.path.basename(str(station_key or "").strip())
    if normalized and not normalized.endswith(".db"):
        normalized = ""
    _STATION_CONTEXT_LOCAL.station_key = normalized
    try:
        yield normalized
    finally:
        _STATION_CONTEXT_LOCAL.station_key = previous


def publish_audio_engine_event(
    event: str,
    *,
    station_key: str = "",
    queue_id: int = 0,
    slot_token: str = "",
    deck: str = "",
    track_id: int = 0,
    path: str = "",
    payload: Mapping[str, Any] | None = None,
):
    """Publish normalized telemetry without affecting the audio path."""
    try:
        return get_audio_engine().publish_event(
            event,
            station_key=str(station_key or ""),
            queue_id=max(0, int(queue_id or 0)),
            slot_token=str(slot_token or ""),
            deck=str(deck or ""),
            track_id=max(0, int(track_id or 0)),
            path=str(path or ""),
            payload=dict(payload or {}),
        )
    except Exception as exc:
        try:
            print(
                f"Audio engine event telemetry failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
        except Exception:
            pass
        return None


def _normalize_year_metadata(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"(19|20)\d{2}", raw)
    return match.group(0) if match else ""


def publish_audio_engine_track_seeked(
    *,
    station_key: str,
    deck: str,
    identity: Mapping[str, Any],
    target_seconds: float,
    from_seconds: float = 0.0,
    source: str = "native_seek_command",
) -> dict[str, Any]:
    """Apply a seek to the daemon and publish normalized seek telemetry."""
    station = str(station_key or "")
    identity_map = dict(identity or {})
    resolved = _ENGINE_LIFECYCLE_IDENTITIES.resolve_confirmed(station, identity_map)

    station_value = str(
        station or identity_map.get("station_key") or resolved.get("station_key") or ""
    )
    queue_id_value = int(identity_map.get("queue_id") or resolved.get("queue_id") or 0)
    slot_token_value = str(
        identity_map.get("slot_token") or resolved.get("slot_token") or ""
    )
    deck_value = str(
        deck or identity_map.get("deck") or resolved.get("deck") or ""
    ).upper()
    track_id_value = int(identity_map.get("track_id") or resolved.get("track_id") or 0)
    path_value = str(
        identity_map.get("path")
        or identity_map.get("file")
        or resolved.get("path")
        or ""
    )
    payload = {
        "seek_position_ms": max(0, int(round(float(target_seconds or 0.0) * 1000.0))),
        "seek_from_position_ms": max(
            0, int(round(float(from_seconds or 0.0) * 1000.0))
        ),
        "cue_in_ms": max(
            0, int(round(float(identity_map.get("cue_in") or 0.0) * 1000.0))
        ),
        "cue_out_ms": max(
            0, int(round(float(identity_map.get("cue_out") or 0.0) * 1000.0))
        ),
        "audio_start_ms": max(
            0, int(round(float(identity_map.get("audio_start") or 0.0) * 1000.0))
        ),
        "play_start_ms": max(0, int(round(float(target_seconds or 0.0) * 1000.0))),
        "transition_at_ms": max(
            0, int(round(float(identity_map.get("cue_out") or 0.0) * 1000.0))
        ),
        "effective_end_ms": max(
            0, int(round(float(identity_map.get("audio_end") or 0.0) * 1000.0))
        ),
        "source_end_ms": max(
            0, int(round(float(identity_map.get("orig_total") or 0.0) * 1000.0))
        ),
        "fade_in_ms": max(
            0, int(round(float(identity_map.get("fade_in") or 0.0) * 1000.0))
        ),
        "fade_out_ms": max(
            0, int(round(float(identity_map.get("fade_out") or 0.0) * 1000.0))
        ),
        "source": str(source or "native_seek_command"),
        "flush_native_pcm": True,
        "cancelled_transition_timers": 0,
    }
    event_record = {
        "event": "track_seeked",
        "station_key": station_value,
        "queue_id": queue_id_value,
        "slot_token": slot_token_value,
        "deck": deck_value,
        "track_id": track_id_value,
        "path": path_value,
        "artist": str(identity_map.get("artist") or ""),
        "title": str(identity_map.get("title") or ""),
        "year": _normalize_year_metadata(identity_map.get("year") or ""),
        "event_monotonic_time_ms": int(round(time.monotonic() * 1000.0)),
        "event_wall_time_unix_ms": int(round(time.time() * 1000.0)),
        "payload": payload,
    }
    engine = get_audio_engine()
    sync_live_event = getattr(engine, "sync_live_event", None)
    if not callable(sync_live_event):
        raise RuntimeError("native_seek_not_supported")
    response = dict(sync_live_event(event_record) or {})
    publish_audio_engine_event(
        "track_seeked",
        station_key=station_value,
        queue_id=queue_id_value,
        slot_token=slot_token_value,
        deck=deck_value,
        track_id=track_id_value,
        path=path_value,
        payload=payload,
    )
    state = response.get("state", response.get("result", {}))
    return dict(state) if isinstance(state, dict) else {}
