"""Thread-safe JSONL protocol capture for audio-engine commands and events.

The logger is deliberately fail-open: telemetry must never interrupt playback.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROTOCOL_VERSION = 1

_ROUTINE_POLL_COMMANDS = frozenset({
    "get_state",
    "get_icecast_output",
    "get_diagnostics",
})

_ROUTINE_PROGRESS_EVENTS = frozenset({
    "track_progress",
    "native_audio_probe_progress",
    "native_position_drift_snapshot",
})

_COMPACT_STATE_KEYS = (
    "running",
    "paused",
    "accepting_loads",
    "active_deck",
    "position_ms",
    "queue_id",
    "slot_token",
    "next_queue_id",
    "next_slot_token",
    "transitioning",
    "native_audio_probe_status",
    "native_audio_probe_deck",
    "native_audio_probe_queue_id",
    "native_audio_probe_position_ms",
    "native_audio_probe_prebuffer_ready",
    "audio_runtime_mismatch_count",
    "output_underrun_count",
    "mixed_output_silence_count",
    "output_count",
    "dsp_status",
    "encoder_status",
    "icecast_status",
    "output_gap_count",
)

_COMPACT_OUTPUT_KEYS = (
    "output_id",
    "codec",
    "mount",
    "status",
    "connected",
    "enabled",
    "connect_count",
    "reconnect_count",
    "send_error_count",
    "output_gap_count",
)


class ProtocolSessionContext:
    """Shared immutable run identity plus mutable daemon-version discovery."""

    def __init__(
        self,
        *,
        session_id: str,
        app_version: str,
        native_daemon_version: str = "unknown",
    ) -> None:
        self._lock = threading.RLock()
        self._session_id = str(session_id or "unknown").strip() or "unknown"
        self._app_version = str(app_version or "unknown").strip() or "unknown"
        self._native_daemon_version = (
            str(native_daemon_version or "unknown").strip() or "unknown"
        )

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def app_version(self) -> str:
        return self._app_version

    @property
    def native_daemon_version(self) -> str:
        with self._lock:
            return self._native_daemon_version

    def set_native_daemon_version(self, value: str) -> None:
        normalized = str(value or "").strip()
        if not normalized:
            return
        with self._lock:
            self._native_daemon_version = normalized

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return {
                "session_id": self._session_id,
                "app_version": self._app_version,
                "native_daemon_version": self._native_daemon_version,
            }


def new_protocol_session_id(app_version: str = "") -> str:
    """Return a sortable, collision-resistant identifier for one app run."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    version = str(app_version or "unknown").strip() or "unknown"
    safe_version = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in version)
    return f"wb-{safe_version}-{timestamp}-{uuid.uuid4().hex[:12]}"



_JSON_SAFE_MAX_DEPTH = 5
_JSON_SAFE_MAX_MAPPING_ITEMS = 1024
_JSON_SAFE_MAX_SEQUENCE_ITEMS = 256
_JSON_SAFE_TRUNCATION_KEY = "__json_safe_truncated_keys__"

def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-safe representation suitable for long-running logs.

    Native multi-output state contains more than 100 named diagnostic fields.
    Preserve the complete controlled state while retaining explicit high limits
    for unexpected user-provided mappings and sequences. Any truncation is made
    visible in the record instead of silently dropping tail fields.
    """
    if depth > _JSON_SAFE_MAX_DEPTH:
        return repr(value)[:500]
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000] + "…"
        return value
    if isinstance(value, Mapping):
        existing_omitted = 0
        items = []
        for key, item_value in value.items():
            if str(key) == _JSON_SAFE_TRUNCATION_KEY:
                try:
                    existing_omitted += max(0, int(item_value or 0))
                except (TypeError, ValueError):
                    existing_omitted += 0
                continue
            items.append((key, item_value))
        limited = items[:_JSON_SAFE_MAX_MAPPING_ITEMS]
        result = {str(k): _json_safe(v, depth=depth + 1) for k, v in limited}
        omitted = existing_omitted + len(items) - len(limited)
        if omitted > 0:
            result[_JSON_SAFE_TRUNCATION_KEY] = omitted
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        return [_json_safe(v, depth=depth + 1) for v in items[:_JSON_SAFE_MAX_SEQUENCE_ITEMS]]
    return repr(value)[:1000]


def summarize_result(value: Any) -> Any:
    """Summarize backend return values without serializing Flask response bodies."""
    if value is None or isinstance(value, (bool, int, float, str, dict, list, tuple)):
        return _json_safe(value)
    status_code = getattr(value, "status_code", None)
    if status_code is not None:
        return {"type": type(value).__name__, "status_code": int(status_code)}
    return {"type": type(value).__name__, "repr": repr(value)[:500]}


class JsonlProtocolLogger:
    """Append-only JSONL writer with small, deterministic file rotation."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        engine_name: str,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 2,
        session_context: ProtocolSessionContext | None = None,
        session_id: str = "",
        app_version: str = "unknown",
        native_daemon_version: str = "unknown",
        verbose: bool = True,
        heartbeat_interval_sec: float = 60.0,
    ) -> None:
        raw_path = str(path or "").strip()
        self.path = Path(raw_path).expanduser() if raw_path else None
        self.engine_name = str(engine_name or "unknown")
        self.max_bytes = max(1_000_000, int(max_bytes))
        self.backup_count = max(0, int(backup_count))
        self.session_context = session_context or ProtocolSessionContext(
            session_id=session_id or new_protocol_session_id(app_version),
            app_version=app_version,
            native_daemon_version=native_daemon_version,
        )
        self.verbose = bool(verbose)
        self.heartbeat_interval_sec = max(0.0, float(heartbeat_interval_sec))
        self._lock = threading.RLock()
        self._sequence = itertools.count(1)
        self._request_ids = itertools.count(1)
        self._last_error = ""
        self._poll_heartbeat_state: dict[tuple[str, str], dict[str, Any]] = {}
        self._prune_existing_rotation_files()

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def session_id(self) -> str:
        return self.session_context.session_id

    @property
    def app_version(self) -> str:
        return self.session_context.app_version

    @property
    def native_daemon_version(self) -> str:
        return self.session_context.native_daemon_version

    def set_native_daemon_version(self, value: str) -> None:
        self.session_context.set_native_daemon_version(value)

    def next_request_id(self) -> int:
        with self._lock:
            return next(self._request_ids)

    def suppress_successful_poll(self, command: str) -> bool:
        """Return whether a successful read-only poll should stay off disk.

        The command still runs normally and failures are always captured.  In
        compact mode a small periodic heartbeat replaces thousands of full
        state request/reply pairs.
        """
        return (not self.verbose) and str(command or "").strip() in _ROUTINE_POLL_COMMANDS

    @staticmethod
    def _compact_outputs(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, (list, tuple)):
            return []
        compact: list[dict[str, Any]] = []
        for item in list(value)[:16]:
            if not isinstance(item, Mapping):
                continue
            selected = {
                key: _json_safe(item.get(key))
                for key in _COMPACT_OUTPUT_KEYS
                if key in item
            }
            if selected:
                compact.append(selected)
        return compact

    @classmethod
    def _compact_poll_result(cls, command: str, result: Any) -> dict[str, Any]:
        if not isinstance(result, Mapping):
            return {"result_type": type(result).__name__}
        summary = {
            key: _json_safe(result.get(key))
            for key in _COMPACT_STATE_KEYS
            if key in result
        }
        outputs = cls._compact_outputs(result.get("outputs"))
        if outputs:
            summary["outputs"] = outputs
        if not summary:
            summary["result_keys"] = sorted(str(key) for key in result.keys())[:32]
        return summary

    def compact_command_success_reply(
        self,
        request_id: int,
        *,
        command: str,
        result: Any,
    ) -> bool:
        """Write a bounded success reply for state-heavy lifecycle commands.

        ``sync_event`` commands remain fully visible on disk, but their daemon
        reply normally contains the complete multi-output engine state.  Compact
        mode retains the identity and health summary without repeating ~10 KiB
        of diagnostics after every track/transition lifecycle event.
        """
        normalized = str(command or "").strip()
        if self.verbose or normalized != "sync_event":
            return self.reply(request_id, ok=True, result=result)
        return self.write({
            "direction": "engine_to_python",
            "record_type": "reply",
            "reply_to": int(request_id),
            "ok": True,
            "result": {
                "compact": True,
                "command": normalized,
                "summary": self._compact_poll_result(normalized, result),
            },
            "error": "",
        })

    def routine_poll_success(
        self,
        *,
        command: str,
        station_key: str,
        result: Any,
    ) -> bool:
        """Record one compact heartbeat for many successful polling calls."""
        if not self.suppress_successful_poll(command):
            return False
        normalized_command = str(command or "").strip()
        normalized_station = str(station_key or "").strip()
        now = time.monotonic()
        with self._lock:
            key = (normalized_command, normalized_station)
            item = self._poll_heartbeat_state.setdefault(
                key,
                {"last_emit": 0.0, "suppressed_since_emit": 0, "suppressed_total": 0},
            )
            item["suppressed_since_emit"] = int(item["suppressed_since_emit"]) + 1
            item["suppressed_total"] = int(item["suppressed_total"]) + 1
            last_emit = float(item["last_emit"] or 0.0)
            due = last_emit <= 0.0 or (
                self.heartbeat_interval_sec > 0.0
                and now - last_emit >= self.heartbeat_interval_sec
            )
            if not due:
                return True
            suppressed_since_emit = int(item["suppressed_since_emit"])
            suppressed_total = int(item["suppressed_total"])
            item["last_emit"] = now
            item["suppressed_since_emit"] = 0
        return self.write({
            "direction": "engine_to_python",
            "record_type": "poll_heartbeat",
            "command": normalized_command,
            "station_key": normalized_station,
            "suppressed_successful_polls": suppressed_since_emit,
            "suppressed_successful_polls_total": suppressed_total,
            "summary": self._compact_poll_result(normalized_command, result),
        })

    def _base_record(self) -> dict[str, Any]:
        now = time.time()
        return {
            "version": PROTOCOL_VERSION,
            "sequence": next(self._sequence),
            "engine": self.engine_name,
            **self.session_context.snapshot(),
            "wall_time_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="milliseconds"),
            "wall_time_unix_ms": int(round(now * 1000.0)),
            "monotonic_time_ms": int(round(time.monotonic() * 1000.0)),
        }

    def _prune_existing_rotation_files(self) -> None:
        """Honor current rotation limits for backups left by older releases."""
        if self.path is None:
            return
        try:
            candidates = tuple(self.path.parent.glob(f"{self.path.name}.*"))
        except Exception:
            return
        for candidate in candidates:
            suffix = candidate.name[len(self.path.name) + 1:]
            if not suffix.isdigit():
                continue
            try:
                index = int(suffix)
                oversized = candidate.stat().st_size > self.max_bytes
                outside_retention = index < 1 or index > self.backup_count
                if oversized or outside_retention:
                    candidate.unlink(missing_ok=True)
            except Exception:
                continue

    def _rotate_if_needed(self, incoming_size: int) -> None:
        if self.path is None or self.backup_count <= 0:
            return
        try:
            current_size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if current_size + incoming_size <= self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backup_count}")
        try:
            oldest.unlink(missing_ok=True)
        except Exception:
            pass
        for index in range(self.backup_count - 1, 0, -1):
            src = self.path.with_name(f"{self.path.name}.{index}")
            dst = self.path.with_name(f"{self.path.name}.{index + 1}")
            try:
                if src.exists():
                    os.replace(src, dst)
            except Exception:
                pass
        try:
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        except FileNotFoundError:
            pass

    def write(self, record: Mapping[str, Any]) -> bool:
        if self.path is None:
            return False
        try:
            with self._lock:
                merged = self._base_record()
                merged.update(_json_safe(dict(record)))
                # Run identity is authoritative and cannot be overwritten by payload data.
                merged.update(self.session_context.snapshot())
                line = json.dumps(merged, ensure_ascii=False, separators=(",", ":")) + "\n"
                encoded_size = len(line.encode("utf-8"))
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed(encoded_size)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line)
                self._last_error = ""
            return True
        except Exception as exc:  # telemetry must never affect playback
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False

    def command(self, command: str, **fields: Any) -> int:
        request_id = self.next_request_id()
        self.write({
            "direction": "python_to_engine",
            "record_type": "command",
            "request_id": request_id,
            "command": str(command),
            **fields,
        })
        return request_id

    def reply(self, request_id: int, *, ok: bool, result: Any = None, error: str = "") -> bool:
        return self.write({
            "direction": "engine_to_python",
            "record_type": "reply",
            "reply_to": int(request_id),
            "ok": bool(ok),
            "result": summarize_result(result),
            "error": str(error or ""),
        })

    def event(self, event_record: Mapping[str, Any]) -> bool:
        if not self.verbose:
            event_name = str(event_record.get("event") or "").strip()
            if event_name in _ROUTINE_PROGRESS_EVENTS:
                return True
            if event_name == "native_resource_snapshot":
                payload = event_record.get("payload")
                reason = str(payload.get("reason") or "") if isinstance(payload, Mapping) else ""
                if reason.lower() in {"", "periodic"}:
                    return True
        return self.write({
            "direction": "engine_to_python",
            "record_type": "event",
            **dict(event_record),
        })


def extract_annotated_identity(uri: str) -> dict[str, Any]:
    """Best-effort identity extraction from Web Broadcaster annotate: URIs."""
    import re

    value = str(uri or "").strip()
    metadata: dict[str, str] = {}
    path = value
    if value.startswith("annotate:"):
        try:
            body = value[len("annotate:"):]
            in_quotes = False
            escaped = False
            separator = -1
            for index, char in enumerate(body):
                if escaped:
                    escaped = False
                    continue
                if char == "\\" and in_quotes:
                    escaped = True
                    continue
                if char == '"':
                    in_quotes = not in_quotes
                    continue
                if char == ":" and not in_quotes:
                    separator = index
                    break
            if separator < 0:
                raise ValueError("annotate URI has no path separator")
            head, path = body[:separator], body[separator + 1:]
            pattern = re.compile(r'([A-Za-z0-9_]+)="((?:\\.|[^"\\])*)"')

            def _unescape_annotate_value(raw: str) -> str:
                """Undo only the escapes emitted by Web Broadcaster annotate URIs.

                ``unicode_escape`` must not be used here: it reinterprets the UTF-8
                bytes of non-ASCII metadata as Latin-1 code points (for example
                ``käptn`` becomes ``kÃ¤ptn``).  The builder only escapes backslashes
                and double quotes, so decode those two sequences and preserve every
                Unicode character verbatim.
                """
                chars: list[str] = []
                index = 0
                while index < len(raw):
                    char = raw[index]
                    if char == "\\" and index + 1 < len(raw):
                        following = raw[index + 1]
                        if following in {"\\", '"'}:
                            chars.append(following)
                            index += 2
                            continue
                    chars.append(char)
                    index += 1
                return "".join(chars)

            for match in pattern.finditer(head):
                metadata[match.group(1)] = _unescape_annotate_value(match.group(2))
        except Exception:
            path = value

    def as_int(*keys: str) -> int:
        for key in keys:
            try:
                raw = str(metadata.get(key, "") or "").strip()
                if raw:
                    return max(0, int(float(raw)))
            except (TypeError, ValueError):
                continue
        return 0

    def seconds_as_ms(*keys: str) -> int:
        for key in keys:
            try:
                raw = str(metadata.get(key, "") or "").strip()
                if raw:
                    return max(0, int(round(float(raw) * 1000.0)))
            except (TypeError, ValueError):
                continue
        return 0

    def as_bool(*keys: str, default: bool = False) -> bool:
        for key in keys:
            raw = str(metadata.get(key, "") or "").strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
        return bool(default)

    def as_float(*keys: str, default: float = 0.0) -> float:
        for key in keys:
            try:
                raw = str(metadata.get(key, "") or "").strip()
                if raw:
                    return float(raw)
            except (TypeError, ValueError):
                continue
        return float(default)

    audio_start_ms = seconds_as_ms(
        "audio_start", "wb_audio_start", "cue_in", "wb_play_start", "wb_seek_base"
    )
    play_start_ms = seconds_as_ms(
        "cue_in", "wb_play_start", "wb_seek_base", "audio_start", "wb_audio_start"
    )
    transition_at_ms = seconds_as_ms(
        "cue_out", "wb_crossfade_trigger", "wb_effective_end", "wb_orig_total"
    )
    effective_end_ms = seconds_as_ms(
        "wb_effective_end", "wb_audio_end", "audio_end", "wb_orig_total", "wb_crossfade_trigger", "cue_out"
    )
    source_end_ms = seconds_as_ms(
        "wb_orig_total", "duration", "wb_effective_end", "wb_audio_end", "audio_end", "wb_crossfade_trigger", "cue_out"
    )

    return {
        "station_key": str(metadata.get("station_key") or "").strip(),
        "queue_id": as_int("queue_id", "wb_queue_id"),
        "track_id": as_int("track_id", "wb_track_id"),
        "slot_token": str(metadata.get("wb_ab_slot_token") or "").strip(),
        "path": str(path or "").strip(),
        "cue_in_ms": play_start_ms,
        "cue_out_ms": transition_at_ms,
        "audio_start_ms": audio_start_ms,
        "play_start_ms": play_start_ms,
        "transition_at_ms": transition_at_ms,
        "effective_end_ms": effective_end_ms,
        "source_end_ms": source_end_ms,
        "fade_in_ms": seconds_as_ms("fade_in"),
        "fade_out_ms": seconds_as_ms("fade_out"),
        "analysis_requested": as_bool("wb_native_analyze", "native_analyze", default=False),
        "manual_timing": as_bool("wb_manual_timing", default=False),
        "hard_clean": as_bool("wb_hard_clean_transition", "wb_clean_transition", default=False),
        "short_no_crossfade": as_bool("wb_short_no_crossfade", "wb_sam_short_no_crossfade", default=False),
        "stream_source": as_bool("wb_stream_source", default=False) or str(metadata.get("wb_source_type") or "").strip().lower() == "stream" or str(path or "").startswith(("http://", "https://")),
        "stream_infinite": as_bool("wb_stream_infinite", default=False),
        "stream_duration_ms": max(0, int(round(as_float("wb_stream_duration", "webradio_dur", default=0.0) * 1000.0))),
        "analysis_window_ms": max(5, int(round(as_float("wb_analysis_window_ms", default=10.0)))),
        "analysis_sustain_ms": max(10, int(round(as_float("wb_analysis_sustain_ms", default=30.0)))),
        "analysis_artifact_max_ms": max(20, int(round(as_float("wb_analysis_artifact_max_ms", default=300.0)))),
        "analysis_artifact_silence_ms": max(20, int(round(as_float("wb_analysis_artifact_silence_ms", default=250.0)))),
        "no_crossfade_max_duration_ms": max(0, int(round(as_float("wb_no_crossfade_max_duration", default=65.0) * 1000.0))),
        "crossfade_fallback_ms": max(0, int(round(as_float("wb_crossfade_fallback", default=3.0) * 1000.0))),
        "crossfade_min_ms": max(0, int(round(as_float("wb_crossfade_min", default=0.1) * 1000.0))),
        "crossfade_max_ms": max(0, int(round(as_float("wb_crossfade_max", default=6.0) * 1000.0))),
        "gap_start_threshold_dbfs": as_float("wb_gap_start_dbfs", default=-20.0),
        "gap_end_threshold_dbfs": as_float("wb_gap_end_dbfs", default=-24.0),
        "crossfade_trigger_relative_db": as_float("wb_crossfade_trigger_relative_db", default=-7.0),
        "artist": str(metadata.get("artist") or "").strip(),
        "title": str(metadata.get("title") or "").strip(),
        "year": str(metadata.get("year") or metadata.get("date") or "").strip(),
    }
