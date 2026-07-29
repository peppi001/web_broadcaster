"""Authoritative native audio engine for Web Broadcaster."""

from .base import AudioEngine, TrackDescriptor
from .events import EngineEvent, STANDARD_ENGINE_EVENTS, TRACK_SCOPED_EVENTS
from .factory import create_audio_engine
from .lifecycle import EngineLifecycleIdentityRegistry, NativeLifecycleCoordinator
from .runtime import (
    close_audio_engine_runtime,
    configure_audio_engine_runtime,
    get_audio_engine,
    publish_audio_engine_event,
    publish_audio_engine_track_seeked,
    station_runtime_context,
    station_runtime_override,
)
from .native_engine import (
    NativeEngine,
    NativeEngineError,
    NativeEngineTimeout,
    NativeEngineUnavailable,
)

__all__ = [
    "AudioEngine",
    "TrackDescriptor",
    "EngineEvent",
    "EngineLifecycleIdentityRegistry",
    "NativeLifecycleCoordinator",
    "NativeEngine",
    "NativeEngineError",
    "NativeEngineTimeout",
    "NativeEngineUnavailable",
    "STANDARD_ENGINE_EVENTS",
    "TRACK_SCOPED_EVENTS",
    "create_audio_engine",
    "close_audio_engine_runtime",
    "configure_audio_engine_runtime",
    "get_audio_engine",
    "publish_audio_engine_event",
    "publish_audio_engine_track_seeked",
    "station_runtime_context",
    "station_runtime_override",
]
