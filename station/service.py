"""Framework-neutral station Start/Stop orchestration.

The service owns operation ordering and rollback. Application-specific A/B state,
AutoDJ, output configuration, UI notifications and script cleanup are supplied
through callbacks, keeping this module free of Flask and app imports.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class StationServiceDependencies:
    get_active_station_key: Callable[[], str]
    get_engine: Callable[[], Any]
    station_runtime_context: Callable[[str], AbstractContextManager[Any]]
    native_station_state: Callable[[str], Mapping[str, Any]]
    invalidate_status_cache: Callable[[], None]
    get_started_at: Callable[[str], Any]
    set_started_at: Callable[[str], None]
    clear_started_at: Callable[[str], None]
    prepare_start_state: Callable[[str], None]
    build_queue_plan: Callable[[str], list[str]]
    startup_autodj_fill: Callable[[str], Mapping[str, Any]]
    load_output_configs: Callable[[str], Sequence[Mapping[str, Any]]]
    bootstrap_queue_plan: Callable[[list[str], str], bool]
    start_autodj_worker: Callable[[str], None]
    notify_on_air: Callable[[str, str], None]
    mark_runtime_started: Callable[[str], None]
    cleanup_failed_start: Callable[[str], None]
    prepare_stop_state: Callable[[str], Mapping[str, Any]]
    restore_failed_stop_state: Callable[[str, Mapping[str, Any]], None]
    finalize_stop_state: Callable[[str, Mapping[str, Any]], None]
    stop_off_air_automation: Callable[[str], None]
    clear_now_playing: Callable[[str], None]
    mark_runtime_stopped: Callable[[str], None]


class StationService:
    """Own the native station Start/Stop sequence and failure rollback."""

    def __init__(self, dependencies: StationServiceDependencies) -> None:
        self._deps = dependencies

    def start(self, station_key: str = "") -> tuple[dict[str, Any], int]:
        station = str(station_key or self._deps.get_active_station_key() or "").strip()
        if not station:
            return {"success": False, "error": "No active station selected."}, 400

        self._deps.invalidate_status_cache()
        engine = self._deps.get_engine()
        started = False
        try:
            with self._deps.station_runtime_context(station):
                current = dict(self._deps.native_station_state(station) or {})
                if bool(current.get("running")):
                    if not self._deps.get_started_at(station):
                        self._deps.set_started_at(station)
                    return {
                        "success": True,
                        "already_running": True,
                        "audio_engine": "native",
                        **current,
                    }, 200

                self._deps.prepare_start_state(station)
                lines = list(self._deps.build_queue_plan(station) or [])
                startup_autodj: Optional[Mapping[str, Any]] = None
                if not lines:
                    startup_autodj = dict(self._deps.startup_autodj_fill(station) or {})
                    lines = list(self._deps.build_queue_plan(station) or [])
                if not lines:
                    reason = str((startup_autodj or {}).get("message") or "").strip()
                    if not reason:
                        reason = "AutoDJ could not add a playable track."
                    raise RuntimeError(
                        "The station queue is empty and startup AutoDJ fill failed: " + reason
                    )

                engine.clear_icecast_output("", station_key=station)
                for config in self._deps.load_output_configs(station):
                    engine.configure_icecast_output(station_key=station, **dict(config))

                start_result = engine.start(station_key=station)
                started = True
                if not self._deps.bootstrap_queue_plan(lines, station):
                    raise RuntimeError("Native A/B bootstrap failed; no deck became authoritative.")

                try:
                    self._deps.start_autodj_worker(station)
                except Exception:
                    pass
                self._deps.invalidate_status_cache()
                self._deps.notify_on_air(station, "native_start_completed")
                final_state = dict(self._deps.native_station_state(station) or {})
                self._deps.mark_runtime_started(station)
                self._deps.set_started_at(station)
                return {
                    "success": True,
                    "audio_engine": "native",
                    "start_result": start_result,
                    "autodj_startup_filled": bool(
                        startup_autodj and startup_autodj.get("success")
                    ),
                    **final_state,
                }, 200
        except Exception as exc:
            if started:
                try:
                    engine.stop(station_key=station)
                except Exception:
                    pass
            self._deps.cleanup_failed_start(station)
            self._deps.invalidate_status_cache()
            self._deps.clear_started_at(station)
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}, 503

    def stop(self, station_key: str = "") -> tuple[dict[str, Any], int]:
        station = str(station_key or self._deps.get_active_station_key() or "").strip()
        if not station:
            return {"success": False, "error": "No active station selected."}, 400

        engine = self._deps.get_engine()
        try:
            with self._deps.station_runtime_context(station):
                stop_context = dict(self._deps.prepare_stop_state(station) or {})
                try:
                    stop_result = engine.stop(station_key=station)
                    stopped = True
                except Exception as exc:
                    state = dict(self._deps.native_station_state(station) or {})
                    if state and bool(state.get("running")):
                        self._deps.restore_failed_stop_state(station, stop_context)
                        raise
                    stop_result = {"already_stopped": True, "detail": str(exc)}
                    stopped = False
                self._deps.finalize_stop_state(station, stop_context)

            try:
                self._deps.stop_off_air_automation(station)
            except Exception:
                pass
            try:
                self._deps.clear_now_playing(station)
            except Exception:
                pass
            self._deps.mark_runtime_stopped(station)
            self._deps.invalidate_status_cache()
            self._deps.notify_on_air(station, "native_stop_completed")
            self._deps.clear_started_at(station)
            return {
                "success": True,
                "stopped": bool(stopped),
                "audio_engine": "native",
                "stop_result": stop_result,
            }, 200
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}, 500
