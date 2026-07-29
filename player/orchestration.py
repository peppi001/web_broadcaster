"""Framework-neutral Manual Next and player handoff orchestration.

The module owns station-scoped Manual Next serialization, lifecycle waiting and
construction of the authoritative direct A/B handoff plan. Application state,
native-engine access and telemetry are supplied through callbacks so the module
has no Flask, SQLite or app-module dependency.
"""

from __future__ import annotations

from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass
import os
import threading
import time
from typing import Any, Callable, Mapping, MutableMapping, Optional, Sequence


@dataclass(frozen=True)
class PlayerHandoffDependencies:
    get_active_station_key: Callable[[], str]
    build_queue_plan: Callable[[str], list[str]]
    line_info: Callable[[str], Mapping[str, Any]]
    read_player_state: Callable[[], dict[str, Any]]
    mutate_player_state: Callable[[Callable[[MutableMapping[str, Any]], Any]], Any]
    native_station_state: Callable[[str], Mapping[str, Any]]
    reconcile_stale_transition: Callable[[str, Mapping[str, Any]], bool]
    trace_manual_next: Callable[..., None]
    resolve_native_live_player: Callable[[str, float], tuple[str, Mapping[str, Any], Any]]
    same_queue_identity: Callable[[str, str], bool]
    start_transition: Callable[..., bool]
    wake_autodj_worker: Callable[[], None]


class PlayerHandoffService:
    """Build and execute one authoritative native Manual Next handoff."""

    def __init__(self, dependencies: PlayerHandoffDependencies) -> None:
        self._deps = dependencies

    def direct_handoff(
        self,
        station_key: str = "",
        *,
        reserved_queue_lines: Optional[Sequence[str]] = None,
        reservation_id: str = "",
    ) -> Optional[dict[str, Any]]:
        station = str(station_key or self._deps.get_active_station_key() or "")

        def fresh_queue_head_lines() -> tuple[list[str], str, int, int]:
            try:
                queue_lines = (
                    list(reserved_queue_lines)
                    if reserved_queue_lines is not None
                    else list(self._deps.build_queue_plan(station) or [])
                )
            except Exception:
                queue_lines = []
            head_line = queue_lines[0] if queue_lines else ""
            try:
                info = dict(self._deps.line_info(head_line) or {}) if head_line else {}
                head_qid = int(info.get("queue_id") or 0)
                head_tid = int(info.get("track_id") or 0)
            except Exception:
                head_qid = 0
                head_tid = 0
            return queue_lines, head_line, head_qid, head_tid

        player_state = self._deps.read_player_state()
        py_active = str(player_state.get("active") or "a").lower()
        if py_active not in ("a", "b"):
            py_active = "a"

        native_state = dict(self._deps.native_station_state(station) or {})
        if self._deps.reconcile_stale_transition(station, native_state):
            self._deps.trace_manual_next(
                "manual_next_stale_transition_reconciled",
                station,
                str(reservation_id or ""),
                target_deck=str(native_state.get("active_deck") or "").upper(),
                target_queue_id=int(
                    native_state.get("queue_id")
                    or native_state.get("native_audio_probe_queue_id")
                    or 0
                ),
                native_transitioning=False,
            )

        native_active = str(native_state.get("active_deck") or "").lower()
        if native_active in ("a", "b"):
            py_active = native_active
        active, ab_status, _raw_status = self._deps.resolve_native_live_player(py_active, 0.45)
        if active not in ("a", "b"):
            active = py_active
        ab_status = dict(ab_status or {})

        try:
            current_state = self._deps.read_player_state()
            existing_lines = list(current_state.get("lines") or [])
            if not (bool(current_state.get("enabled")) and existing_lines):
                return None

            queue_lines, target_line, target_qid, target_tid = fresh_queue_head_lines()
            if not target_line:
                return None

            live_uri = str(ab_status.get(f"{active}_uri") or "").strip()
            if not live_uri:
                py_state = self._deps.read_player_state()
                try:
                    py_idx = int((dict(py_state.get("player_index") or {})).get(active, -1))
                except Exception:
                    py_idx = -1
                py_lines = list(py_state.get("lines") or [])
                if 0 <= py_idx < len(py_lines):
                    live_uri = str(py_lines[py_idx] or "")

            planned_lines: list[str] = [live_uri or target_line, target_line]
            for extra_line in queue_lines[1:]:
                try:
                    duplicate = self._deps.same_queue_identity(extra_line, target_line)
                except Exception:
                    duplicate = False
                if not duplicate:
                    planned_lines.append(extra_line)

            current_index = 0
            target_index = 1
            target_player = "b" if active == "a" else "a"

            def reserve_plan(state: MutableMapping[str, Any]) -> int:
                generation = int(state.get("generation") or 0) + 1
                state["generation"] = generation
                state["lines"] = list(planned_lines)
                state["player_index"] = {
                    active: current_index,
                    target_player: target_index,
                }
                state["transition_starting"] = False
                state["pending_cueout_transition"] = False
                state["pending_cueout_deadline"] = 0.0
                state["pending_cueout_token"] = 0
                state["manual_next_until"] = time.time() + 6.0
                return generation

            generation = int(self._deps.mutate_player_state(reserve_plan) or 0)
            direct_ok = self._deps.start_transition(
                station,
                active=active,
                target=target_player,
                current_index=current_index,
                target_index=target_index,
                fade=0.0,
                generation=generation,
                reason="manual_next_db_head_direct_handoff",
                manual_next_fast=True,
                manual_next_request_id=str(reservation_id or ""),
            )
            if direct_ok:
                self._deps.wake_autodj_worker()
                return {
                    "success": True,
                    "mode": "manual_next_db_head_direct_handoff",
                    "player": active,
                    "target_player": target_player,
                    "target_queue_id": int(target_qid or 0),
                    "target_track_id": int(target_tid or 0),
                    "target_pos": 0.0,
                    "elapsed": 0.0,
                    "duration": 0.0,
                    "deferred_dequeue": False,
                }
        except Exception:
            pass
        return None


@dataclass(frozen=True)
class ManualNextDependencies:
    resolve_station_key: Callable[[str], str]
    get_active_station_key: Callable[[], str]
    trace: Callable[..., None]
    station_runtime_context: Callable[[str], AbstractContextManager[Any]]
    read_reserved_plan: Callable[[str], tuple[list[str], int, int]]
    native_station_state: Callable[[str], Mapping[str, Any]]
    native_queue_contains_queue_id: Callable[[str, int], bool]
    perform_direct_handoff: Callable[..., Optional[dict[str, Any]]]
    signal_monitor_wake: Callable[[str, str], None]
    wake_autodj_worker: Callable[[], None]
    scheduled_script_url_active: Callable[[str], bool] = lambda _station: False
    cancel_scheduled_script_queue: Callable[[str, Sequence[int], str], Any] = (
        lambda _station, _queue_ids, _reason: None
    )


class ManualNextOrchestrator:
    """Own station-scoped Manual Next serialization and lifecycle commit waits."""

    def __init__(
        self,
        dependencies: ManualNextDependencies,
        *,
        max_pending: int = 8,
        commit_timeout_seconds: float = 30.0,
    ) -> None:
        self._deps = dependencies
        self._max_pending = max(1, int(max_pending))
        self._commit_timeout_seconds = max(1.0, float(commit_timeout_seconds))
        self._condition = threading.Condition(threading.RLock())
        self._states: dict[str, dict[str, Any]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._deck_lock_guard = threading.RLock()
        self._deck_locks: dict[str, threading.RLock] = {}

    def station_key(self, station_key: str = "") -> str:
        resolved = str(self._deps.resolve_station_key(str(station_key or "")) or "").strip()
        if not resolved:
            resolved = str(station_key or self._deps.get_active_station_key() or "").strip()
        return os.path.basename(resolved)

    def _state_locked(self, station_key: str) -> dict[str, Any]:
        station = self.station_key(station_key)
        state = self._states.get(station)
        if state is None:
            state = {
                "pending": deque(),
                "processing": False,
                "request_seq": 0,
                "active_request_id": "",
                "active_target_queue_id": 0,
                "active_target_track_id": 0,
                "active_started_at": 0.0,
                "last_lifecycle_queue_id": 0,
                "last_lifecycle_success": False,
                "last_lifecycle_at": 0.0,
                "last_completed_request_id": "",
                "last_error": "",
                "last_result": {},
            }
            self._states[station] = state
        return state

    def deck_plan_lock(self, station_key: str) -> threading.RLock:
        station = self.station_key(station_key)
        with self._deck_lock_guard:
            lock = self._deck_locks.get(station)
            if lock is None:
                lock = threading.RLock()
                self._deck_locks[station] = lock
            return lock

    def public_status(self, station_key: str = "") -> dict[str, Any]:
        station = self.station_key(station_key)
        with self._condition:
            state = self._states.get(station)
            if not state:
                return {
                    "in_progress": False,
                    "pending_count": 0,
                    "active_request_id": "",
                    "active_target_queue_id": 0,
                    "last_error": "",
                }
            pending_count = len(state.get("pending") or ())
            processing = bool(state.get("processing"))
            return {
                "in_progress": bool(processing or pending_count),
                "pending_count": int(pending_count + (1 if processing else 0)),
                "queued_count": int(pending_count),
                "active_request_id": str(state.get("active_request_id") or ""),
                "active_target_queue_id": int(state.get("active_target_queue_id") or 0),
                "active_target_track_id": int(state.get("active_target_track_id") or 0),
                "last_completed_request_id": str(state.get("last_completed_request_id") or ""),
                "last_error": str(state.get("last_error") or ""),
            }

    def is_inflight(self, station_key: str = "") -> bool:
        return bool(self.public_status(station_key).get("in_progress"))

    def mark_lifecycle(self, station_key: str, queue_id: int, *, success: bool) -> None:
        station = self.station_key(station_key)
        qid = int(queue_id or 0)
        if not station or qid <= 0:
            return
        request_id = ""
        target_track_id = 0
        with self._condition:
            state = self._states.get(station)
            if not state:
                return
            state["last_lifecycle_queue_id"] = qid
            state["last_lifecycle_success"] = bool(success)
            state["last_lifecycle_at"] = time.monotonic()
            if int(state.get("active_target_queue_id") or 0) == qid:
                request_id = str(state.get("active_request_id") or "")
                target_track_id = int(state.get("active_target_track_id") or 0)
            self._condition.notify_all()
        if request_id:
            self._deps.trace(
                "manual_next_track_started",
                station,
                request_id,
                success=bool(success),
                target_queue_id=qid,
                target_track_id=target_track_id,
                source_event="track_started",
            )

    def _wait_for_lifecycle(self, station_key: str, queue_id: int) -> tuple[bool, str]:
        station = self.station_key(station_key)
        qid = int(queue_id or 0)
        deadline = time.monotonic() + self._commit_timeout_seconds
        with self._condition:
            while True:
                state = self._state_locked(station)
                if int(state.get("last_lifecycle_queue_id") or 0) == qid:
                    if bool(state.get("last_lifecycle_success")):
                        return True, "track_started_committed"
                    return False, "track_started_commit_failed"
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False, "track_started_commit_timeout"
                self._condition.wait(timeout=min(0.25, remaining))

    def _execute_one(self, station_key: str, request: Mapping[str, Any]) -> dict[str, Any]:
        station = self.station_key(station_key)
        request_id = str(request.get("request_id") or "")
        action = str(request.get("action") or "next")
        source = str(request.get("source") or "internal")
        guarded_queue_ids = [
            int(value)
            for value in (request.get("guarded_queue_ids") or [])
            if str(value or "").strip().isdigit() and int(value) > 0
        ]

        def scheduled_script_url_skip() -> Optional[dict[str, Any]]:
            if source != "script":
                return None
            try:
                active = bool(self._deps.scheduled_script_url_active(station))
            except Exception:
                active = False
            if not active:
                return None
            try:
                self._deps.cancel_scheduled_script_queue(
                    station,
                    guarded_queue_ids,
                    "url_playback_active",
                )
            except Exception:
                pass
            self._deps.trace(
                "scheduled_script_skipped_url_playback",
                station,
                request_id,
                action=action,
                source=source,
                reason="url_playback_active",
                guarded_queue_ids=list(guarded_queue_ids),
            )
            return {
                "success": True,
                "skipped": True,
                "mode": "scheduled_script_skipped_url_playback",
                "reason": "url_playback_active",
                "request_id": request_id,
                "source": source,
            }

        deck_lock = self.deck_plan_lock(station)
        with self._deps.station_runtime_context(station):
            with deck_lock:
                skipped = scheduled_script_url_skip()
                if skipped is not None:
                    return skipped
                queue_lines: list[str] = []
                target_qid = 0
                target_tid = 0
                active_head_deadline = time.monotonic() + 3.0
                while True:
                    queue_lines, target_qid, target_tid = self._deps.read_reserved_plan(station)
                    if not queue_lines or target_qid <= 0:
                        self._deps.trace(
                            "manual_next_rejected",
                            station,
                            request_id,
                            action=action,
                            source=source,
                            reason="queue_empty",
                        )
                        return {
                            "success": False,
                            "mode": "manual_next_queue_empty",
                            "error": "queue_empty",
                            "request_id": request_id,
                        }
                    native_state = dict(self._deps.native_station_state(station) or {})
                    if not bool(native_state.get("running")):
                        self._deps.trace(
                            "manual_next_rejected",
                            station,
                            request_id,
                            action=action,
                            source=source,
                            reason="station_not_running",
                        )
                        return {
                            "success": False,
                            "mode": "manual_next_station_not_running",
                            "error": "station_not_running",
                            "request_id": request_id,
                        }
                    native_queue_id = int(native_state.get("queue_id") or 0)
                    if native_queue_id <= 0 or native_queue_id != int(target_qid):
                        break
                    if time.monotonic() >= active_head_deadline:
                        self._deps.trace(
                            "manual_next_rejected",
                            station,
                            request_id,
                            action=action,
                            source=source,
                            reason="active_head_timeout",
                            target_queue_id=int(target_qid),
                        )
                        return {
                            "success": False,
                            "mode": "manual_next_active_head_timeout",
                            "error": "queue_head_is_currently_audible",
                            "request_id": request_id,
                            "target_queue_id": int(target_qid),
                        }
                    time.sleep(0.05)

                with self._condition:
                    state = self._state_locked(station)
                    state["active_target_queue_id"] = int(target_qid)
                    state["active_target_track_id"] = int(target_tid)
                    state["active_started_at"] = time.monotonic()
                    if int(state.get("last_lifecycle_queue_id") or 0) == int(target_qid):
                        state["last_lifecycle_queue_id"] = 0
                        state["last_lifecycle_success"] = False
                    self._condition.notify_all()

                self._deps.trace(
                    "manual_next_target_reserved",
                    station,
                    request_id,
                    action=action,
                    source=source,
                    target_queue_id=int(target_qid),
                    target_track_id=int(target_tid),
                )
                if not self._deps.native_queue_contains_queue_id(station, target_qid):
                    self._deps.trace(
                        "manual_next_rejected",
                        station,
                        request_id,
                        action=action,
                        source=source,
                        reason="reserved_queue_head_missing",
                        target_queue_id=int(target_qid),
                        target_track_id=int(target_tid),
                    )
                    return {
                        "success": False,
                        "mode": "manual_next_reservation_stale",
                        "error": "reserved_queue_head_missing",
                        "request_id": request_id,
                        "target_queue_id": target_qid,
                    }

                skipped = scheduled_script_url_skip()
                if skipped is not None:
                    return skipped

                direct = self._deps.perform_direct_handoff(
                    station,
                    reserved_queue_lines=queue_lines,
                    reservation_id=request_id,
                )
                if not direct or not bool(direct.get("success")):
                    error = str((direct or {}).get("error") or "manual_next_handoff_failed")
                    self._deps.trace(
                        "manual_next_rejected",
                        station,
                        request_id,
                        action=action,
                        source=source,
                        reason="handoff_failed",
                        error=error,
                        target_queue_id=int(target_qid),
                        target_track_id=int(target_tid),
                    )
                    return {
                        "success": False,
                        "mode": str((direct or {}).get("mode") or "manual_next_direct_handoff_unavailable"),
                        "error": error,
                        "request_id": request_id,
                        "target_queue_id": target_qid,
                        "target_track_id": target_tid,
                    }

            lifecycle_ok, lifecycle_reason = self._wait_for_lifecycle(station, target_qid)
            result = dict(direct or {})
            result.update(
                {
                    "success": bool(lifecycle_ok),
                    "request_id": request_id,
                    "target_queue_id": int(target_qid),
                    "target_track_id": int(target_tid),
                    "lifecycle_reason": lifecycle_reason,
                    "mode": (
                        "manual_next_serialized_committed"
                        if lifecycle_ok
                        else "manual_next_serialized_failed"
                    ),
                }
            )
            self._deps.trace(
                "manual_next_lifecycle_committed" if lifecycle_ok else "manual_next_lifecycle_failed",
                station,
                request_id,
                action=action,
                source=source,
                success=bool(lifecycle_ok),
                reason=str(lifecycle_reason or ""),
                target_queue_id=int(target_qid),
                target_track_id=int(target_tid),
            )
            if not lifecycle_ok:
                result["error"] = lifecycle_reason
            return result

    def _worker(self, station_key: str) -> None:
        station = self.station_key(station_key)
        try:
            while True:
                with self._condition:
                    state = self._state_locked(station)
                    pending = state.get("pending")
                    if not pending:
                        state["processing"] = False
                        state["active_request_id"] = ""
                        state["active_target_queue_id"] = 0
                        state["active_target_track_id"] = 0
                        self._workers.pop(station, None)
                        self._condition.notify_all()
                        break
                    request = pending.popleft()
                    state["processing"] = True
                    state["active_request_id"] = str(request.get("request_id") or "")
                    state["active_target_queue_id"] = 0
                    state["active_target_track_id"] = 0
                    state["last_error"] = ""
                    pending_remaining = len(state.get("pending") or ())
                    self._condition.notify_all()

                self._deps.trace(
                    "manual_next_worker_started",
                    station,
                    str(request.get("request_id") or ""),
                    action=str(request.get("action") or "next"),
                    source=str(request.get("source") or "internal"),
                    pending_remaining=int(pending_remaining),
                )
                try:
                    result = self._execute_one(station, request)
                except Exception as exc:
                    result = {
                        "success": False,
                        "mode": "manual_next_worker_exception",
                        "error": f"{type(exc).__name__}: {exc}",
                        "request_id": str(request.get("request_id") or ""),
                    }
                    self._deps.trace(
                        "manual_next_worker_exception",
                        station,
                        str(request.get("request_id") or ""),
                        action=str(request.get("action") or "next"),
                        source=str(request.get("source") or "internal"),
                        reason="worker_exception",
                        error=str(result.get("error") or ""),
                    )

                success = bool(result.get("success"))
                with self._condition:
                    state = self._state_locked(station)
                    state["last_result"] = dict(result)
                    state["last_completed_request_id"] = str(request.get("request_id") or "")
                    state["last_error"] = "" if success else str(
                        result.get("error") or result.get("mode") or "manual_next_failed"
                    )
                    state["active_request_id"] = ""
                    state["active_target_queue_id"] = 0
                    state["active_target_track_id"] = 0
                    if not success:
                        dropped = len(state.get("pending") or ())
                        state.get("pending").clear()
                        if dropped:
                            result["dropped_pending"] = int(dropped)
                    self._condition.notify_all()

                self._deps.trace(
                    "manual_next_serialized_result",
                    station,
                    str(request.get("request_id") or ""),
                    action=str(request.get("action") or "next"),
                    source=str(request.get("source") or "internal"),
                    success=bool(success),
                    mode=str(result.get("mode") or ""),
                    target_queue_id=int(result.get("target_queue_id") or 0),
                    target_track_id=int(result.get("target_track_id") or 0),
                    lifecycle_reason=str(result.get("lifecycle_reason") or ""),
                    error=str(result.get("error") or ""),
                    dropped_pending=int(result.get("dropped_pending") or 0),
                )
        finally:
            try:
                self._deps.signal_monitor_wake(station, "manual_next_worker_idle")
            except Exception:
                pass
            self._deps.wake_autodj_worker()

    def enqueue(
        self,
        station_key: str,
        *,
        action: str = "next",
        source: str = "internal",
        guarded_queue_ids: Optional[Sequence[int]] = None,
    ) -> dict[str, Any]:
        station = self.station_key(station_key)
        action = str(action or "next").strip().lower() or "next"
        source = str(source or "internal").strip().lower() or "internal"
        if not station:
            return {
                "success": False,
                "accepted": False,
                "error": "no_active_station",
                "source": source,
            }

        with self._condition:
            state = self._state_locked(station)
            state["request_seq"] = int(state.get("request_seq") or 0) + 1
            request_id = f"mn-{state['request_seq']}-{int(time.monotonic() * 1000.0)}"
            pending = state.get("pending")
            outstanding = len(pending) + (1 if bool(state.get("processing")) else 0)
            received_event = (
                "manual_next_http_received" if source == "http" else "manual_next_request_received"
            )
            self._deps.trace(
                received_event,
                station,
                request_id,
                action=action,
                source=source,
                outstanding_count=int(outstanding),
            )
            if outstanding >= self._max_pending:
                result = {
                    "success": False,
                    "accepted": False,
                    "in_progress": True,
                    "error": "manual_next_queue_full",
                    "request_id": request_id,
                    "pending_count": int(outstanding),
                    "source": source,
                }
                self._deps.trace(
                    "manual_next_rejected",
                    station,
                    request_id,
                    action=action,
                    source=source,
                    reason="manual_next_queue_full",
                    pending_count=int(outstanding),
                )
                return result

            normalized_guarded_queue_ids: list[int] = []
            for queue_id in guarded_queue_ids or []:
                try:
                    value = int(queue_id)
                except (TypeError, ValueError):
                    continue
                if value > 0 and value not in normalized_guarded_queue_ids:
                    normalized_guarded_queue_ids.append(value)
            request = {
                "request_id": request_id,
                "action": action,
                "source": source,
                "accepted_at": time.monotonic(),
                "guarded_queue_ids": normalized_guarded_queue_ids,
            }
            pending.append(request)
            queued_position = len(pending) + (1 if bool(state.get("processing")) else 0)
            self._deps.trace(
                "manual_next_serialized_accepted",
                station,
                request_id,
                action=action,
                source=source,
                queued_position=int(queued_position),
                pending_count=int(queued_position),
            )
            worker = self._workers.get(station)
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._worker,
                    args=(station,),
                    name=f"manual-next-{station}",
                    daemon=True,
                )
                self._workers[station] = worker
                worker.start()
            self._condition.notify_all()

        return {
            "success": True,
            "accepted": True,
            "in_progress": True,
            "mode": "manual_next_serialized_accepted",
            "request_id": request_id,
            "queued_position": int(queued_position),
            "pending_count": int(queued_position),
            "removed": False,
            "deferred_dequeue": True,
            "source": source,
        }

    def perform_action(
        self,
        station_key: str = "",
        action: str = "next",
        *,
        source: str = "internal",
        guarded_queue_ids: Optional[Sequence[int]] = None,
    ) -> dict[str, Any]:
        station = self.station_key(station_key)
        action = str(action or "next").strip().lower() or "next"
        result = self.enqueue(
            station,
            action=action,
            source=str(source or "internal"),
            guarded_queue_ids=guarded_queue_ids,
        )
        self._deps.wake_autodj_worker()
        return result
