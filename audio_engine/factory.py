"""Native-only audio-engine construction for Web Broadcaster."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .base import AudioEngine
from .native_engine import NativeEngine
from .protocol import JsonlProtocolLogger, ProtocolSessionContext, new_protocol_session_id

_DISABLED_VALUES = {"0", "false", "no", "off", "disabled", "none"}


def _env_int(environment: Mapping[str, str], key: str, default: int) -> int:
    try:
        return int(str(environment.get(key, default) or default).strip())
    except (TypeError, ValueError):
        return int(default)


def _env_float(environment: Mapping[str, str], key: str, default: float) -> float:
    try:
        return float(str(environment.get(key, default) or default).strip())
    except (TypeError, ValueError):
        return float(default)


def _env_bool(environment: Mapping[str, str], key: str, default: bool) -> bool:
    raw = str(environment.get(key, "1" if default else "0") or "").strip().lower()
    if raw in _DISABLED_VALUES:
        return False
    if raw in {"1", "true", "yes", "on", "enabled"}:
        return True
    return bool(default)


def _configured_path(
    environment: Mapping[str, str],
    key: str,
    fallback: str | None,
) -> str | None:
    configured = str(environment.get(key, "") or "").strip()
    if configured.lower() in _DISABLED_VALUES:
        return None
    return configured or fallback


def create_audio_engine(
    *,
    environ: Mapping[str, str] | None = None,
    log_callback: Callable[[str], Any] | None = None,
    protocol_log_path: str | None = None,
    app_version: str = "unknown",
    station_key_resolver: Callable[[], str] | None = None,
) -> AudioEngine:
    """Create the single authoritative native engine."""

    environment = os.environ if environ is None else environ

    normalized_app_version = str(
        app_version or environment.get("WEB_BROADCASTER_APP_VERSION", "unknown") or "unknown"
    ).strip() or "unknown"
    configured_session_id = str(
        environment.get("WEB_BROADCASTER_ENGINE_SESSION_ID", "") or ""
    ).strip()
    session_id = configured_session_id or new_protocol_session_id(normalized_app_version)
    session_context = ProtocolSessionContext(
        session_id=session_id,
        app_version=normalized_app_version,
        native_daemon_version="not_connected",
    )

    runtime_logging_enabled = _env_bool(environment, "DEBUG", False)
    selected_path = (
        _configured_path(
            environment,
            "WEB_BROADCASTER_NATIVE_PROTOCOL_LOG",
            protocol_log_path,
        )
        if runtime_logging_enabled
        else None
    )
    max_bytes = _env_int(
        environment,
        "WEB_BROADCASTER_ENGINE_PROTOCOL_MAX_BYTES",
        10 * 1024 * 1024,
    )
    backup_count = _env_int(environment, "WEB_BROADCASTER_ENGINE_PROTOCOL_BACKUPS", 2)
    protocol_verbose = _env_bool(
        environment,
        "WEB_BROADCASTER_ENGINE_PROTOCOL_VERBOSE",
        False,
    )
    protocol_heartbeat_sec = _env_float(
        environment,
        "WEB_BROADCASTER_ENGINE_PROTOCOL_HEARTBEAT_SECONDS",
        60.0,
    )
    socket_path = str(
        environment.get("WEB_BROADCASTER_ENGINE_SOCKET", "/tmp/web-broadcaster-engine.sock")
        or "/tmp/web-broadcaster-engine.sock"
    ).strip()
    app_root = Path(__file__).resolve().parents[1]
    autostart_value = str(
        environment.get("WEB_BROADCASTER_NATIVE_AUTOSTART", "1") or "1"
    ).strip().lower()
    autostart_enabled = autostart_value not in _DISABLED_VALUES
    configured_binary = str(
        environment.get("WEB_BROADCASTER_NATIVE_BINARY", "") or ""
    ).strip()
    daemon_binary_path = (
        configured_binary
        or str(app_root / "native_engine" / "bin" / "web_broadcaster_engine")
    ) if autostart_enabled else ""
    configured_daemon_log = str(
        environment.get("WEB_BROADCASTER_NATIVE_DAEMON_LOG", "") or ""
    ).strip()
    daemon_log_path = (
        configured_daemon_log or str(app_root / "logs" / "native_engine.log")
    ) if runtime_logging_enabled else ""

    native_logger = JsonlProtocolLogger(
        selected_path,
        engine_name="native",
        max_bytes=max_bytes,
        backup_count=backup_count,
        session_context=session_context,
        verbose=protocol_verbose,
        heartbeat_interval_sec=protocol_heartbeat_sec,
    )
    engine = NativeEngine(
        socket_path=socket_path,
        connect_timeout_sec=_env_float(
            environment, "WEB_BROADCASTER_NATIVE_CONNECT_TIMEOUT", 0.5
        ),
        request_timeout_sec=_env_float(
            environment, "WEB_BROADCASTER_NATIVE_REQUEST_TIMEOUT", 3.0
        ),
        reconnect_delay_sec=_env_float(
            environment, "WEB_BROADCASTER_NATIVE_RECONNECT_DELAY", 1.0
        ),
        protocol_logger=native_logger,
        station_key_resolver=station_key_resolver,
        daemon_binary_path=daemon_binary_path,
        daemon_log_path=daemon_log_path,
        daemon_start_timeout_sec=_env_float(
            environment, "WEB_BROADCASTER_NATIVE_START_TIMEOUT", 8.0
        ),
    )

    message = (
        f"Audio engine selected: native; session_id: {session_id}; "
        f"app_version: {normalized_app_version}; native socket: {socket_path}; "
        f"managed internally: {'yes' if autostart_enabled else 'no'}; "
        f"protocol mode: {'verbose' if protocol_verbose else 'compact'}; "
        f"protocol heartbeat: {protocol_heartbeat_sec:g}s"
    )
    if engine.protocol_log_path:
        message += f"; protocol log: {engine.protocol_log_path}"
    if log_callback is None:
        print(message, flush=True)
    else:
        log_callback(message)

    engine.publish_event(
        "engine_ready",
        payload={
            "audio_engine": "native",
            "live_engine": "native",
            "native_control_only": False,
            "protocol_log_path": engine.protocol_log_path,
            "native_socket_path": engine.socket_path,
            "session_id": session_id,
            "app_version": normalized_app_version,
            "native_daemon_version": session_context.native_daemon_version,
            "managed_internally": bool(autostart_enabled),
            "protocol_mode": "verbose" if protocol_verbose else "compact",
            "protocol_heartbeat_seconds": protocol_heartbeat_sec,
            "protocol_max_bytes": max_bytes,
            "protocol_backups": backup_count,
        },
    )
    return engine
