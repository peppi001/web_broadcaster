"""Unix-socket client for the native audio-engine daemon.

The native daemon is the authoritative two-deck PCM mixer, DSP, encoder and
Icecast output backend and the only supported runtime audio path.
"""

from __future__ import annotations

import atexit
import errno
import json
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Web Broadcaster targets Linux
    fcntl = None
from typing import Any, Callable, Mapping

from .base import AudioEngine
from .events import EngineEvent, EngineEventBus, normalize_deck
from .protocol import PROTOCOL_VERSION, JsonlProtocolLogger, extract_annotated_identity


class NativeEngineError(RuntimeError):
    """Base error raised by the native control client."""


class NativeEngineUnavailable(NativeEngineError):
    """Raised when the native daemon is not reachable or disconnects."""


class NativeEngineTimeout(NativeEngineError):
    """Raised when a native request does not receive a reply in time."""


@dataclass(slots=True)
class _PendingReply:
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None
    error: BaseException | None = None


class NativeEngine(AudioEngine):
    """Thread-safe JSONL-over-Unix-socket client.

    Request IDs are allocated by Python, replies are matched by ``reply_to``,
    and asynchronous daemon events are published through the common event bus.
    A failed connection is cached briefly so a missing native daemon cannot
    create a reconnect storm when the UI polls state frequently.
    """

    def __init__(
        self,
        *,
        socket_path: str = "/tmp/web-broadcaster-engine.sock",
        connect_timeout_sec: float = 0.25,
        request_timeout_sec: float = 0.75,
        reconnect_delay_sec: float = 1.0,
        protocol_logger: JsonlProtocolLogger | None = None,
        station_key_resolver: Callable[[], str] | None = None,
        daemon_binary_path: str | None = None,
        daemon_log_path: str | None = None,
        daemon_start_timeout_sec: float = 8.0,
    ) -> None:
        raw_path = str(socket_path or "").strip()
        if not raw_path:
            raise ValueError("native engine socket path must not be empty")
        self.socket_path = raw_path
        self.connect_timeout_sec = max(0.05, float(connect_timeout_sec))
        self.request_timeout_sec = max(0.05, float(request_timeout_sec))
        self.reconnect_delay_sec = max(0.05, float(reconnect_delay_sec))
        self._protocol_logger = protocol_logger or JsonlProtocolLogger(None, engine_name=self.name)
        self._station_key_resolver = station_key_resolver
        self._event_bus = EngineEventBus(engine_name=self.name, protocol_logger=self._protocol_logger)

        self._connection_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._pending_lock = threading.RLock()
        self._pending: dict[int, _PendingReply] = {}
        self._socket: socket.socket | None = None
        self._reader_thread: threading.Thread | None = None
        self._connection_generation = 0
        self._closing = False
        self._last_error = ""
        self._next_connect_monotonic = 0.0
        self._last_station_key = ""

        binary = str(daemon_binary_path or "").strip()
        self.daemon_binary_path = str(Path(binary).resolve()) if binary else ""
        log_path = str(daemon_log_path or "").strip()
        self.daemon_log_path = str(Path(log_path).resolve()) if log_path else ""
        self.daemon_start_timeout_sec = max(0.5, float(daemon_start_timeout_sec))
        self._daemon_process: subprocess.Popen[bytes] | None = None
        self._daemon_start_lock = threading.RLock()
        self._atexit_registered = False
        if self.daemon_binary_path:
            atexit.register(self.close)
            self._atexit_registered = True

    @property
    def name(self) -> str:
        return "native"

    @property
    def protocol_log_path(self) -> str:
        return str(self._protocol_logger.path or "")

    @property
    def session_id(self) -> str:
        return self._protocol_logger.session_id

    @property
    def app_version(self) -> str:
        return self._protocol_logger.app_version

    @property
    def native_daemon_version(self) -> str:
        return self._protocol_logger.native_daemon_version

    @property
    def connected(self) -> bool:
        with self._connection_lock:
            return self._socket is not None

    @property
    def last_error(self) -> str:
        return self._last_error

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
        return self._event_bus.publish(
            event,
            station_key=station_key,
            queue_id=queue_id,
            slot_token=slot_token,
            deck=deck,
            track_id=track_id,
            path=path,
            payload=payload,
        )

    def subscribe_events(self, callback: Callable[[EngineEvent], Any]) -> Callable[[], None]:
        return self._event_bus.subscribe(callback)

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending_items = tuple(self._pending.values())
            self._pending.clear()
        for pending in pending_items:
            pending.error = error
            pending.event.set()

    def _disconnect(self, error: BaseException | None = None, *, generation: int | None = None) -> None:
        with self._connection_lock:
            if generation is not None and generation != self._connection_generation:
                return
            sock = self._socket
            self._socket = None
            if error is not None:
                self._last_error = f"{type(error).__name__}: {error}"
                self._next_connect_monotonic = time.monotonic() + self.reconnect_delay_sec
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if error is not None:
            self._fail_pending(error)

    def close(self) -> None:
        with self._connection_lock:
            if self._closing:
                return
            self._closing = True
        self._disconnect(NativeEngineUnavailable("native engine client closed"))
        self._stop_managed_daemon()

    def _open_socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.connect_timeout_sec)
        try:
            sock.connect(self.socket_path)
            sock.settimeout(None)
            return sock
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
            raise

    def _probe_socket(self) -> bool:
        try:
            probe = self._open_socket()
        except OSError:
            return False
        try:
            probe.close()
        except OSError:
            pass
        return True

    def _managed_daemon_log_handle(self):
        if not self.daemon_log_path:
            return None
        path = Path(self.daemon_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("ab", buffering=0)

    def _ensure_managed_daemon(self) -> None:
        if not self.daemon_binary_path:
            return
        binary = Path(self.daemon_binary_path)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise NativeEngineUnavailable(
                f"bundled native engine is missing or not executable: {binary}"
            )

        with self._daemon_start_lock:
            process = self._daemon_process
            if process is not None and process.poll() is None:
                return
            if process is not None:
                self._daemon_process = None

            lock_path = Path(f"{self.socket_path}.launch.lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a+b") as lock_file:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    if self._probe_socket():
                        return
                    socket_file = Path(self.socket_path)
                    try:
                        if socket_file.exists() or socket_file.is_socket():
                            socket_file.unlink()
                    except OSError:
                        pass

                    environment = os.environ.copy()
                    # The daemon has a pinned DT_RPATH to the bundled custom
                    # libav* directory.  Legacy FFmpeg CLI overrides and loader
                    # injection variables must not alter the managed runtime.
                    environment.pop("WEB_BROADCASTER_FFMPEG", None)
                    environment.pop("LD_PRELOAD", None)
                    environment.pop("LD_AUDIT", None)
                    environment["WEB_BROADCASTER_ENGINE_SOCKET"] = self.socket_path
                    environment["WEB_BROADCASTER_ENGINE_PARENT_PID"] = str(os.getpid())
                    environment["WEB_BROADCASTER_ENGINE_MANAGED"] = "1"
                    log_handle = self._managed_daemon_log_handle()
                    try:
                        if log_handle is None:
                            # Keep the daemon's routine startup/status stdout quiet in
                            # normal operation, but inherit stderr so genuine native and
                            # libav errors remain visible on the application console.
                            daemon_stdout = subprocess.DEVNULL
                            daemon_stderr = None
                        else:
                            # DEBUG=1 preserves the pre-v6015 combined daemon log file.
                            daemon_stdout = log_handle
                            daemon_stderr = subprocess.STDOUT
                        launched = subprocess.Popen(
                            [str(binary), self.socket_path],
                            cwd=str(binary.parents[2]),
                            env=environment,
                            stdin=subprocess.DEVNULL,
                            stdout=daemon_stdout,
                            stderr=daemon_stderr,
                            close_fds=True,
                        )
                    finally:
                        if log_handle is not None:
                            log_handle.close()
                    self._daemon_process = launched

                    deadline = time.monotonic() + self.daemon_start_timeout_sec
                    last_error = ""
                    while time.monotonic() < deadline:
                        return_code = launched.poll()
                        if return_code is not None:
                            raise NativeEngineUnavailable(
                                f"bundled native engine exited during startup with code {return_code}"
                                + (
                                    f"; see {self.daemon_log_path}"
                                    if self.daemon_log_path
                                    else "; native daemon errors were written to the console; use DEBUG=1 for the full daemon log"
                                )
                            )
                        try:
                            probe = self._open_socket()
                        except OSError as exc:
                            last_error = str(exc)
                            time.sleep(0.025)
                            continue
                        try:
                            probe.close()
                        except OSError:
                            pass
                        return
                    raise NativeEngineUnavailable(
                        f"bundled native engine did not create a usable socket within "
                        f"{self.daemon_start_timeout_sec:.1f}s: {last_error}"
                        + (
                            f"; see {self.daemon_log_path}"
                            if self.daemon_log_path
                            else "; native daemon errors were written to the console; use DEBUG=1 for the full daemon log"
                        )
                    )
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _stop_managed_daemon(self) -> None:
        with self._daemon_start_lock:
            process = self._daemon_process
            self._daemon_process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        except OSError:
            pass

    def ensure_ready(self) -> dict[str, Any]:
        """Start the bundled daemon when needed and verify the control channel."""
        return self.ping()

    def _connect(self) -> socket.socket:
        with self._connection_lock:
            if self._closing:
                raise NativeEngineUnavailable("native engine client is closed")
            if self._socket is not None:
                return self._socket
            now = time.monotonic()
            if now < self._next_connect_monotonic:
                raise NativeEngineUnavailable(self._last_error or "native reconnect backoff is active")

            try:
                sock = self._open_socket()
            except OSError as first_error:
                can_autostart = bool(self.daemon_binary_path) and first_error.errno in {
                    errno.ENOENT, errno.ECONNREFUSED, errno.ENOTSOCK
                }
                if not can_autostart:
                    wrapped = NativeEngineUnavailable(
                        f"cannot connect to native engine socket {self.socket_path}: {first_error}"
                    )
                    self._last_error = str(wrapped)
                    self._next_connect_monotonic = time.monotonic() + self.reconnect_delay_sec
                    raise wrapped from first_error
                self._ensure_managed_daemon()
                try:
                    sock = self._open_socket()
                except OSError as exc:
                    wrapped = NativeEngineUnavailable(
                        f"cannot connect to managed native engine socket {self.socket_path}: {exc}"
                    )
                    self._last_error = str(wrapped)
                    self._next_connect_monotonic = time.monotonic() + self.reconnect_delay_sec
                    raise wrapped from exc

            self._connection_generation += 1
            generation = self._connection_generation
            self._socket = sock
            self._last_error = ""
            self._next_connect_monotonic = 0.0
            reader = threading.Thread(
                target=self._reader_loop,
                args=(sock, generation),
                name="native-engine-reader",
                daemon=True,
            )
            self._reader_thread = reader
            reader.start()
            return sock

    def _reader_loop(self, sock: socket.socket, generation: int) -> None:
        buffer = bytearray()
        error: BaseException | None = None
        try:
            while not self._closing:
                chunk = sock.recv(65536)
                if not chunk:
                    raise NativeEngineUnavailable("native engine closed the socket")
                buffer.extend(chunk)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        if len(buffer) > 4 * 1024 * 1024:
                            raise NativeEngineError("native engine message exceeded 4 MiB")
                        break
                    raw_line = bytes(buffer[:newline]).strip()
                    del buffer[: newline + 1]
                    if not raw_line:
                        continue
                    try:
                        message = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        self.publish_event(
                            "engine_error",
                            payload={
                                "phase": "protocol_decode",
                                "error": f"{type(exc).__name__}: {exc}",
                            },
                        )
                        continue
                    if isinstance(message, dict):
                        self._handle_message(message)
        except BaseException as exc:  # reader thread must release all waiters
            error = exc
        finally:
            if error is None and not self._closing:
                error = NativeEngineUnavailable("native engine reader stopped")
            self._disconnect(error, generation=generation)

    def _observe_native_version(self, message: Mapping[str, Any]) -> None:
        candidates: list[Any] = [message.get("native_daemon_version")]
        for key in ("payload", "result", "state"):
            value = message.get(key)
            if isinstance(value, Mapping):
                candidates.append(value.get("native_daemon_version"))
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized:
                self._protocol_logger.set_native_daemon_version(normalized)
                return

    def _handle_message(self, message: Mapping[str, Any]) -> None:
        self._observe_native_version(message)
        reply_to = message.get("reply_to")
        if reply_to is not None:
            try:
                request_id = int(reply_to)
            except (TypeError, ValueError):
                return
            with self._pending_lock:
                pending = self._pending.pop(request_id, None)
            if pending is not None:
                pending.response = dict(message)
                pending.event.set()
            return

        event_name = str(message.get("event") or "").strip()
        if not event_name:
            return
        payload = message.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        self.publish_event(
            event_name,
            station_key=str(message.get("station_key") or ""),
            queue_id=self._safe_int(message.get("queue_id")),
            slot_token=str(message.get("slot_token") or ""),
            deck=str(message.get("deck") or ""),
            track_id=self._safe_int(message.get("track_id")),
            path=str(message.get("path") or ""),
            payload=dict(payload),
        )

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0


    def _resolve_station_key(self, station_key: str = "") -> str:
        explicit = str(station_key or "").strip()
        if explicit:
            self._last_station_key = explicit
            return explicit
        resolver = self._station_key_resolver
        if resolver is not None:
            try:
                resolved = str(resolver() or "").strip()
            except Exception:
                resolved = ""
            if resolved:
                self._last_station_key = resolved
                return resolved
        return str(self._last_station_key or "")

    def request(
        self,
        command: str,
        *,
        response_timeout_sec: float | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        request_id = self._protocol_logger.next_request_id()
        daemon_commands = {"ping", "list_stations", "get_all_station_states", "create_station", "remove_station", "stop_all_stations"}
        if command not in daemon_commands:
            explicit_station = str(fields.get("station_key") or "").strip()
            if not explicit_station:
                nested_track = fields.get("track")
                if isinstance(nested_track, Mapping):
                    explicit_station = str(nested_track.get("station_key") or "").strip()
            routed_station = self._resolve_station_key(explicit_station)
            if routed_station:
                fields["station_key"] = routed_station
        message = {
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "command": str(command),
            "session_id": self.session_id,
            "app_version": self.app_version,
            **fields,
        }
        pending = _PendingReply()
        with self._pending_lock:
            self._pending[request_id] = pending

        logged_message = dict(message)
        if "password" in logged_message:
            logged_message["password"] = "[redacted]" if logged_message.get("password") else ""
        command_record = {
            "direction": "python_to_engine",
            "record_type": "command",
            **logged_message,
            "transport": "unix_socket",
            "socket_path": self.socket_path,
        }
        suppress_success = self._protocol_logger.suppress_successful_poll(command)
        command_logged = False

        def ensure_command_logged() -> None:
            nonlocal command_logged
            if command_logged:
                return
            self._protocol_logger.write(command_record)
            command_logged = True

        if not suppress_success:
            ensure_command_logged()

        try:
            sock = self._connect()
            encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            with self._send_lock:
                sock.sendall(encoded)
        except BaseException as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            if not isinstance(exc, NativeEngineError):
                exc = NativeEngineUnavailable(str(exc))
            ensure_command_logged()
            self._protocol_logger.reply(request_id, ok=False, error=f"{type(exc).__name__}: {exc}")
            if self.connected:
                self._disconnect(exc)
            raise exc

        wait_timeout = (
            self.request_timeout_sec
            if response_timeout_sec is None
            else max(0.05, float(response_timeout_sec))
        )
        if not pending.event.wait(wait_timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            error = NativeEngineTimeout(
                f"native command {command!r} timed out after {wait_timeout:.3f}s"
            )
            ensure_command_logged()
            self._protocol_logger.reply(request_id, ok=False, error=str(error))
            self._disconnect(error)
            raise error
        if pending.error is not None:
            error = pending.error
            ensure_command_logged()
            self._protocol_logger.reply(request_id, ok=False, error=f"{type(error).__name__}: {error}")
            if isinstance(error, BaseException):
                raise error
            raise NativeEngineUnavailable(str(error))

        response = dict(pending.response or {})
        self._observe_native_version(response)
        ok = bool(response.get("ok"))
        logged_result = response.get("result", response.get("state"))
        if suppress_success and ok:
            self._protocol_logger.routine_poll_success(
                command=command,
                station_key=str(fields.get("station_key") or ""),
                result=logged_result,
            )
        else:
            if not ok:
                ensure_command_logged()
            if ok and command == "sync_event":
                self._protocol_logger.compact_command_success_reply(
                    request_id,
                    command=command,
                    result=logged_result,
                )
            else:
                self._protocol_logger.reply(
                    request_id,
                    ok=ok,
                    result=logged_result,
                    error=str(response.get("error") or ""),
                )
        if not ok:
            raise NativeEngineError(str(response.get("error") or f"native command {command!r} failed"))
        return response

    def ping(self) -> dict[str, Any]:
        return self.request("ping")

    def create_station(self, station_key: str) -> dict[str, Any]:
        return dict(self.request("create_station", station_key=str(station_key or "").strip()).get("result") or {})

    def remove_station(self, station_key: str) -> dict[str, Any]:
        return dict(self.request("remove_station", station_key=str(station_key or "").strip(), response_timeout_sec=5.0).get("result") or {})

    def list_stations(self) -> dict[str, Any]:
        return dict(self.request("list_stations").get("result") or {})

    def get_all_station_states(self) -> dict[str, Any]:
        return dict(self.request("get_all_station_states").get("result") or {})

    def stop_all_stations(self) -> dict[str, Any]:
        return dict(self.request("stop_all_stations", response_timeout_sec=10.0).get("result") or {})

    def start(self, *, station_key: str = "") -> Any:
        return self.request("start", station_key=self._resolve_station_key(station_key)).get("result")

    def stop(self, *, station_key: str = "") -> Any:
        return self.request("stop", station_key=self._resolve_station_key(station_key)).get("result")

    def set_paused(self, paused: bool, *, station_key: str = "") -> Any:
        return self.request(
            "set_paused",
            station_key=self._resolve_station_key(station_key),
            paused=bool(paused),
        ).get("result")

    def reload(self, *, station_key: str = "") -> Any:
        return self.request("reload", station_key=self._resolve_station_key(station_key)).get("result")

    def get_state(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("get_state", station_key=self._resolve_station_key(station_key))
        state = response.get("state", response.get("result", {}))
        if not isinstance(state, dict):
            raise NativeEngineError("native get_state reply did not contain an object")
        return dict(state)

    def get_fault_state(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("get_fault", station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise NativeEngineError("native get_fault reply did not contain an object")
        return dict(result)

    def configure_fault(
        self,
        mode: str,
        *,
        target_deck: str = "",
        target_slot_token: str = "",
        after_ms: int = 3000,
        duration_ms: int = 6000,
        once: bool = True,
        station_key: str = "",
    ) -> dict[str, Any]:
        response = self.request(
            "configure_fault",
            station_key=self._resolve_station_key(station_key),
            mode=str(mode or "").strip(),
            target_deck=normalize_deck(target_deck) if str(target_deck or "").strip() else "",
            target_slot_token=str(target_slot_token or "").strip(),
            after_ms=max(0, int(after_ms or 0)),
            duration_ms=max(0, int(duration_ms or 0)),
            once=bool(once),
        )
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def clear_fault(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("clear_fault", station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def get_diagnostics_state(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("get_diagnostics", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise NativeEngineError("native diagnostics reply did not contain an object")
        return dict(result)

    def emit_diagnostics_snapshot(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("emit_diagnostics_snapshot", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def get_icecast_output_state(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("get_icecast_output", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise NativeEngineError("native Icecast output reply did not contain an object")
        return dict(result)

    def configure_icecast_output(
        self,
        *,
        output_id: str = "mp3",
        codec: str = "mp3",
        enabled: bool,
        host: str,
        port: int,
        mount: str,
        username: str = "source",
        password: str = "",
        bitrate_kbps: int = 192,
        stream_name: str = "Web Broadcaster",
        stream_description: str = "",
        stream_genre: str = "",
        stream_url: str = "",
        public_stream: bool = False,
        add_year_to_metadata: bool = False,
        dsp_enabled: bool = False,
        dsp_config_path: str = "",
        station_key: str = "",
    ) -> dict[str, Any]:
        response = self.request(
            "configure_icecast_output",
            station_key=self._resolve_station_key(station_key),
            response_timeout_sec=2.0,
            output_id=str(output_id or "mp3").strip(),
            codec=str(codec or "mp3").strip(),
            enabled=bool(enabled),
            host=str(host or "").strip(),
            port=int(port),
            mount=str(mount or "").strip(),
            username=str(username or "source").strip(),
            password=str(password or ""),
            bitrate_kbps=int(bitrate_kbps),
            stream_name=str(stream_name or "Web Broadcaster").strip(),
            stream_description=str(stream_description or "").strip(),
            stream_genre=str(stream_genre or "").strip(),
            stream_url=str(stream_url or "").strip(),
            public_stream=bool(public_stream),
            add_year_to_metadata=bool(add_year_to_metadata),
            dsp_enabled=bool(dsp_enabled),
            dsp_config_path=str(dsp_config_path or "").strip(),
        )
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def clear_icecast_output(self, output_id: str = "", *, station_key: str = "") -> dict[str, Any]:
        response = self.request(
            "clear_icecast_output", response_timeout_sec=2.0,
            station_key=self._resolve_station_key(station_key),
            output_id=str(output_id or "").strip(),
        )
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def kill_icecast_encoder(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("kill_icecast_encoder", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def kill_native_dsp(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("kill_native_dsp", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        result = response.get("result", {})
        return dict(result) if isinstance(result, dict) else {}

    def inject_late_events(self, *, station_key: str = "") -> dict[str, Any]:
        response = self.request("inject_late_events", response_timeout_sec=2.0, station_key=self._resolve_station_key(station_key))
        state = response.get("state", response.get("result", {}))
        return dict(state) if isinstance(state, dict) else {}

    def load_deck(
        self,
        deck: str,
        uri: str,
        *,
        attempts: int = 8,
        retry_delay: float = 0.35,
        clear_slot: bool = False,
        manual_next_fast: bool = False,
        station_key: str = "",
    ) -> bool:
        identity = extract_annotated_identity(uri)
        routed_station = self._resolve_station_key(str(station_key or identity.get("station_key") or ""))
        response = self.request(
            "load",
            station_key=routed_station,
            deck=normalize_deck(deck),
            uri=str(uri or ""),
            track={
                "station_key": str(identity.get("station_key") or routed_station),
                "queue_id": int(identity.get("queue_id") or 0),
                "slot_token": str(identity.get("slot_token") or ""),
                "track_id": int(identity.get("track_id") or 0),
                "path": str(identity.get("path") or ""),
                "cue_in_ms": int(identity.get("cue_in_ms") or 0),
                "cue_out_ms": int(identity.get("cue_out_ms") or 0),
                "audio_start_ms": int(identity.get("audio_start_ms") or 0),
                "play_start_ms": int(identity.get("play_start_ms") or identity.get("cue_in_ms") or 0),
                "transition_at_ms": int(identity.get("transition_at_ms") or identity.get("cue_out_ms") or 0),
                "effective_end_ms": int(identity.get("effective_end_ms") or 0),
                "source_end_ms": int(identity.get("source_end_ms") or 0),
                "fade_in_ms": int(identity.get("fade_in_ms") or 0),
                "fade_out_ms": int(identity.get("fade_out_ms") or 0),
                "analysis_requested": bool(identity.get("analysis_requested")),
                "manual_timing": bool(identity.get("manual_timing")),
                "hard_clean": bool(identity.get("hard_clean")),
                "short_no_crossfade": bool(identity.get("short_no_crossfade")),
                "stream_source": bool(identity.get("stream_source")),
                "stream_infinite": bool(identity.get("stream_infinite")),
                "stream_duration_ms": int(identity.get("stream_duration_ms") or 0),
                "analysis_window_ms": int(identity.get("analysis_window_ms") or 10),
                "analysis_sustain_ms": int(identity.get("analysis_sustain_ms") or 30),
                "analysis_artifact_max_ms": int(identity.get("analysis_artifact_max_ms") or 300),
                "analysis_artifact_silence_ms": int(identity.get("analysis_artifact_silence_ms") or 250),
                "no_crossfade_max_duration_ms": int(identity.get("no_crossfade_max_duration_ms") or 65000),
                "crossfade_fallback_ms": int(identity.get("crossfade_fallback_ms") or 3000),
                "crossfade_min_ms": int(identity.get("crossfade_min_ms") or 100),
                "crossfade_max_ms": int(identity.get("crossfade_max_ms") or 6000),
                "gap_start_threshold_dbfs": float(identity.get("gap_start_threshold_dbfs") or -20.0),
                "gap_end_threshold_dbfs": float(identity.get("gap_end_threshold_dbfs") or -24.0),
                "crossfade_trigger_relative_db": float(identity.get("crossfade_trigger_relative_db") or -7.0),
                "artist": str(identity.get("artist") or ""),
                "title": str(identity.get("title") or ""),
                "year": str(identity.get("year") or ""),
            },
            options={
                "attempts": int(attempts),
                "retry_delay": float(retry_delay),
                "clear_slot": bool(clear_slot),
                "manual_next_fast": bool(manual_next_fast),
            },
        )
        result = response.get("result")
        if isinstance(result, Mapping) and "accepted" in result:
            return bool(result.get("accepted"))
        return True


    def cancel_deck_load(
        self,
        deck: str,
        *,
        queue_id: int = 0,
        slot_token: str = "",
        reason: str = "live_load_failed",
        station_key: str = "",
    ) -> bool:
        """Cancel one still-pending native load without touching a confirmed deck identity."""

        response = self.request(
            "cancel_load",
            station_key=self._resolve_station_key(station_key),
            deck=normalize_deck(deck),
            queue_id=max(0, int(queue_id or 0)),
            slot_token=str(slot_token or ""),
            reason=str(reason or "live_load_failed"),
        )
        result = response.get("result")
        if isinstance(result, Mapping):
            return bool(result.get("cancelled"))
        return False

    def sync_live_event(self, event_record: Mapping[str, Any]) -> dict[str, Any]:
        """Apply one exact lifecycle event to the authoritative native daemon state.

        The command remains flattened so the small C protocol parser can read the
        exact queue/deck identity without implementing a general JSON DOM.
        The daemon replies with its complete post-sync state.
        """
        record = dict(event_record or {})
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}
        return self.request(
            "sync_event",
            source_event=str(record.get("event") or ""),
            station_key=str(record.get("station_key") or ""),
            queue_id=self._safe_int(record.get("queue_id")),
            slot_token=str(record.get("slot_token") or ""),
            deck=normalize_deck(str(record.get("deck") or "")),
            track_id=self._safe_int(record.get("track_id")),
            path=str(record.get("path") or ""),
            artist=str(record.get("artist") or payload.get("artist") or ""),
            title=str(record.get("title") or payload.get("title") or ""),
            year=str(record.get("year") or payload.get("year") or ""),
            cue_in_ms=self._safe_int(payload.get("cue_in_ms")),
            cue_out_ms=self._safe_int(payload.get("cue_out_ms")),
            audio_start_ms=self._safe_int(payload.get("audio_start_ms")),
            play_start_ms=self._safe_int(payload.get("play_start_ms") or payload.get("cue_in_ms")),
            transition_at_ms=self._safe_int(payload.get("transition_at_ms") or payload.get("cue_out_ms")),
            effective_end_ms=self._safe_int(payload.get("effective_end_ms")),
            source_end_ms=self._safe_int(payload.get("source_end_ms")),
            fade_in_ms=self._safe_int(payload.get("fade_in_ms")),
            fade_out_ms=self._safe_int(payload.get("fade_out_ms")),
            seek_position_ms=self._safe_int(payload.get("seek_position_ms")),
            seek_from_position_ms=self._safe_int(payload.get("seek_from_position_ms")),
            descriptor_complete=bool(
                str(record.get("slot_token") or "")
                and str(record.get("path") or "")
                and (
                    "play_start_ms" in payload
                    or "cue_in_ms" in payload
                    or "transition_at_ms" in payload
                    or "cue_out_ms" in payload
                    or "source_end_ms" in payload
                )
            ),
            event_monotonic_time_ms=self._safe_int(record.get("event_monotonic_time_ms")),
            event_wall_time_unix_ms=self._safe_int(record.get("event_wall_time_unix_ms")),
            source_payload=dict(payload),
        )

    def select_deck(self, deck: str, *, timeout_sec: float = 1.0, station_key: str = "") -> Any:
        return self.request(
            "select",
            station_key=self._resolve_station_key(station_key),
            deck=normalize_deck(deck),
            timeout_sec=float(timeout_sec),
        ).get("result")

    def hard_handoff_to(
        self,
        deck: str,
        *,
        timeout_sec: float = 1.0,
        station_key: str = "",
    ) -> Any:
        return self.request(
            "hard_handoff",
            station_key=self._resolve_station_key(station_key),
            deck=normalize_deck(deck),
            timeout_sec=float(timeout_sec),
        ).get("result")

    def transition_to(
        self,
        deck: str,
        duration: float,
        *,
        timeout_sec: float = 1.0,
        station_key: str = "",
    ) -> Any:
        return self.request(
            "transition",
            station_key=self._resolve_station_key(station_key),
            deck=normalize_deck(deck),
            duration=float(duration),
            timeout_sec=float(timeout_sec),
        ).get("result")


    # Native-only compatibility aliases for routes that retained their v50xx names.
    def get_native_fault_state(self, *, station_key: str = "") -> dict[str, Any]:
        return self.get_fault_state(station_key=station_key)

    def configure_native_fault(self, mode: str, **kwargs: Any) -> dict[str, Any]:
        return self.configure_fault(mode, **kwargs)

    def clear_native_fault(self, *, station_key: str = "") -> dict[str, Any]:
        return self.clear_fault(station_key=station_key)

    def get_native_icecast_output_state(self, *, station_key: str = "") -> dict[str, Any]:
        return self.get_icecast_output_state(station_key=station_key)

    def configure_native_icecast_output(self, **kwargs: Any) -> dict[str, Any]:
        return self.configure_icecast_output(**kwargs)

    def clear_native_icecast_output(self, output_id: str = "", *, station_key: str = "") -> dict[str, Any]:
        return self.clear_icecast_output(output_id, station_key=station_key)

    def kill_native_icecast_encoder(self, *, station_key: str = "") -> dict[str, Any]:
        return self.kill_icecast_encoder(station_key=station_key)

    def inject_native_late_events(self, *, station_key: str = "") -> dict[str, Any]:
        return self.inject_late_events(station_key=station_key)

    def emit_native_diagnostics_snapshot(self, *, station_key: str = "") -> dict[str, Any]:
        return self.emit_diagnostics_snapshot(station_key=station_key)

    def get_native_diagnostics_summary(self, *, station_key: str = "") -> dict[str, Any]:
        state = self.get_state(station_key=station_key)
        diagnostics = self.get_diagnostics_state(station_key=station_key)
        outputs = self.get_icecast_output_state(station_key=station_key)
        return {
            "app_version": self.app_version,
            "native_daemon_version": self.native_daemon_version,
            "live_engine": "native",
            "station_key": str(station_key or state.get("station_key") or ""),
            "state": state,
            "diagnostics": diagnostics,
            "icecast_output": outputs,
        }

    def diagnostic_state(self) -> dict[str, Any]:
        """Local client state; never used as the broadcaster's live status."""
        return {
            "socket_path": str(Path(self.socket_path)),
            "connected": self.connected,
            "last_error": self.last_error,
            "protocol_log_path": self.protocol_log_path,
            "managed_daemon": bool(self.daemon_binary_path),
            "daemon_binary_path": self.daemon_binary_path,
            "daemon_log_path": self.daemon_log_path,
            "daemon_pid": int(self._daemon_process.pid) if self._daemon_process is not None and self._daemon_process.poll() is None else 0,
        }
