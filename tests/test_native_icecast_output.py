from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

from audio_engine import NativeEngine, NativeEngineError
from audio_engine.events import EngineEvent
from audio_engine.protocol import JsonlProtocolLogger, ProtocolSessionContext


class _MockIcecastSource:
    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(16)
        self._socket.settimeout(0.2)
        self.port = int(self._socket.getsockname()[1])
        self.requests: list[bytes] = []
        self.source_requests: list[bytes] = []
        self.source_request_times: list[float] = []
        self.metadata_requests: list[bytes] = []
        self.data = bytearray()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._clients: set[socket.socket] = set()
        self._source_clients: set[socket.socket] = set()
        self._handlers: list[threading.Thread] = []
        self._thread = threading.Thread(target=self._run, name="mock-icecast", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        self._thread.join(timeout=3.0)
        for handler in list(self._handlers):
            handler.join(timeout=1.0)

    def disconnect_sources(self) -> None:
        with self._lock:
            clients = list(self._source_clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                client, _address = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            client.settimeout(0.2)
            with self._lock:
                self._clients.add(client)
            handler = threading.Thread(target=self._handle_client, args=(client,), daemon=True)
            self._handlers.append(handler)
            handler.start()

    def _handle_client(self, client: socket.socket) -> None:
        request = bytearray()
        try:
            while b"\r\n\r\n" not in request and len(request) < 65536:
                chunk = client.recv(4096)
                if not chunk:
                    break
                request.extend(chunk)
            raw_request = bytes(request)
            is_metadata = raw_request.startswith(b"GET /admin/metadata?")
            with self._lock:
                self.requests.append(raw_request)
                if is_metadata:
                    self.metadata_requests.append(raw_request)
                else:
                    self.source_requests.append(raw_request)
                    self.source_request_times.append(time.monotonic())
                    self._source_clients.add(client)
            client.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            if is_metadata:
                return
            while not self._stop.is_set():
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                with self._lock:
                    self.data.extend(chunk)
        except OSError:
            pass
        finally:
            with self._lock:
                self._clients.discard(client)
                self._source_clients.discard(client)
            try:
                client.close()
            except OSError:
                pass


class _RejectingIcecastSource:
    def __init__(self, status: bytes = b"HTTP/1.0 403 Forbidden\r\nContent-Length: 0\r\n\r\n") -> None:
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(8)
        self._socket.settimeout(0.2)
        self.port = int(self._socket.getsockname()[1])
        self.status = status
        self.request_times: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                client.settimeout(0.5)
                request = bytearray()
                while b"\r\n\r\n" not in request and len(request) < 65536:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    request.extend(chunk)
                self.request_times.append(time.monotonic())
                client.sendall(self.status)
            except OSError:
                pass
            finally:
                try:
                    client.close()
                except OSError:
                    pass


class NativeIcecastOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = shutil.which("ffmpeg")
        cls.ffprobe = shutil.which("ffprobe")
        cls.bundled_ffmpeg = cls.root / "bin" / "ffmpeg"

    @contextmanager
    def _daemon(
        self,
        *,
        logger: JsonlProtocolLogger | None = None,
        ffmpeg_seek_delay_sec: float = 0.0,
        ring_ms: int = 0,
        prebuffer_ms: int = 0,
        station_key: str = "",
    ):
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / "engine.sock"
            environment = os.environ.copy()
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "1"
            if ring_ms > 0:
                environment["WEB_BROADCASTER_NATIVE_AUDIO_RING_MS"] = str(int(ring_ms))
            if prebuffer_ms > 0:
                environment["WEB_BROADCASTER_NATIVE_AUDIO_PREBUFFER_MS"] = str(int(prebuffer_ms))
            environment.pop("WEB_BROADCASTER_FFMPEG", None)
            if ffmpeg_seek_delay_sec > 0.0:
                environment["WEB_BROADCASTER_LIBAV_TEST_SEEK_DELAY_MS"] = str(
                    int(round(ffmpeg_seek_delay_sec * 1000.0))
                )
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 3.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists(), "native daemon socket was not created")
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=2.0,
                    reconnect_delay_sec=0.05,
                    protocol_logger=logger,
                    station_key_resolver=(lambda key=str(station_key or "").strip(): key) if str(station_key or "").strip() else None,
                )
                last_ping_error: Exception | None = None
                while time.monotonic() < deadline:
                    try:
                        native.ping()
                        last_ping_error = None
                        break
                    except Exception as exc:  # startup readiness only
                        last_ping_error = exc
                        time.sleep(0.02)
                if last_ping_error is not None:
                    self.fail(f"native daemon did not become protocol-ready: {last_ping_error}")
                yield native, Path(tmp)
            finally:
                if native is not None:
                    try:
                        native.stop()
                    except Exception:
                        pass
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)

    def test_configuration_state_is_secret_free_and_protocol_log_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as log_tmp:
            log_path = Path(log_tmp) / "native.jsonl"
            logger = JsonlProtocolLogger(
                log_path,
                engine_name="native",
                session_context=ProtocolSessionContext(
                    session_id="icecast-config-test",
                    app_version="5050",
                    native_daemon_version="not_connected",
                ),
            )
            with self._daemon(logger=logger, station_key="config-test") as (native, _tmp):
                state = native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=65534,
                    mount="/native-test.mp3",
                    username="source",
                    password="unlogged-secret",
                    bitrate_kbps=160,
                    stream_name="Native Test",
                    stream_description="Description\r\nInjected: blocked",
                    stream_genre="Pop\nInjected-Genre: blocked",
                    stream_url="https://example.test/stream\r\nInjected-URL: blocked",
                    public_stream=False,
                )
                self.assertTrue(state["enabled"])
                self.assertFalse(state["engine_running"])
                self.assertTrue(state["password_configured"])
                self.assertNotIn("password", state)
                self.assertEqual(state["status"], "configured")
                self.assertEqual(state["stream_description"], "Description\r\nInjected: blocked")
                self.assertEqual(state["stream_genre"], "Pop\nInjected-Genre: blocked")
                self.assertEqual(state["stream_url"], "https://example.test/stream\r\nInjected-URL: blocked")
                self.assertEqual(state["deck_a_gain"], 0.0)
                self.assertEqual(state["deck_b_gain"], 0.0)
                self.assertEqual(native.get_state()["dsp_state"], "bypassed")

                # A blank password preserves the in-memory password on an update.
                state = native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=65534,
                    mount="/native-test-2.mp3",
                    username="source",
                    password="",
                    bitrate_kbps=192,
                    stream_name="Native Test 2",
                    public_stream=False,
                )
                self.assertTrue(state["password_configured"])
                cleared = native.clear_icecast_output()
                self.assertFalse(cleared["enabled"])
                self.assertFalse(cleared["password_configured"])

            log_text = log_path.read_text(encoding="utf-8")
            self.assertNotIn("unlogged-secret", log_text)
            self.assertIn('"password":"[redacted]"', log_text)

    def test_synchronized_transition_uses_expected_ab_gains(self) -> None:
        with self._daemon(station_key="gain-test") as (native, _tmp):
            native.start()
            now_ms = int(time.monotonic() * 1000)
            wall_now_ms = int(time.time() * 1000)
            native.sync_live_event({
                "event": "transition_started",
                "station_key": "gain-test",
                "queue_id": 16002,
                "slot_token": "gain-token-b",
                "deck": "B",
                "track_id": 16102,
                "path": "/music/gain-b.mp3",
                "event_monotonic_time_ms": now_ms,
                "event_wall_time_unix_ms": wall_now_ms,
                "payload": {
                    "from_deck": "A",
                    "fade_seconds": 10.0,
                    "fade_out_duration_ms": 10000,
                    "entry_ramp_ms": 20,
                    "silence_hold_ms": 50,
                    "release_duration_ms": 10050,
                    "transition_started_wall_time_unix_ms": wall_now_ms - 2500,
                    "entry_started_wall_time_unix_ms": wall_now_ms - 2490,
                    "fade_curve": "smoothstep",
                    "entry_curve": "smoothstep",
                },
            })
            state = native.get_icecast_output_state()
            self.assertTrue(state["transitioning"])
            # At one quarter of the fade the outgoing smoothstep gain remains
            # 1 - (x*x*(3-2*x)) = 0.84375. With no exact incoming PCM yet, the
            # native entry ramp must wait instead of expiring on callback time.
            self.assertAlmostEqual(float(state["deck_a_gain"]), 0.84375, delta=0.03)
            self.assertAlmostEqual(float(state["deck_b_gain"]), 0.0, delta=0.001)
            self.assertEqual(state["transition_curve"], "smoothstep")
            self.assertEqual(int(state["transition_entry_start_monotonic_ms"]), 0)
            self.assertGreater(int(state["transition_entry_requested_monotonic_ms"]), 0)
            self.assertEqual(int(state["transition_entry_pcm_start_monotonic_ms"]), 0)
            self.assertTrue(bool(state["transition_entry_waiting_for_pcm"]))
            self.assertEqual(int(state["transition_fade_out_ms"]), 10000)
            self.assertEqual(int(state["transition_entry_ramp_ms"]), 20)
            self.assertEqual(int(state["transition_silence_hold_ms"]), 50)
            self.assertEqual(int(state["transition_release_duration_ms"]), 10050)
            native.sync_live_event({
                "event": "transition_finished",
                "station_key": "gain-test",
                "queue_id": 16002,
                "slot_token": "gain-token-b",
                "deck": "B",
                "track_id": 16102,
                "path": "/music/gain-b.mp3",
                "event_monotonic_time_ms": int(time.monotonic() * 1000),
                "event_wall_time_unix_ms": int(time.time() * 1000),
                "payload": {},
            })
            state = native.get_icecast_output_state()
            self.assertFalse(state["transitioning"])
            self.assertAlmostEqual(float(state["deck_a_gain"]), 0.0, delta=0.001)
            self.assertAlmostEqual(float(state["deck_b_gain"]), 1.0, delta=0.001)

    def test_transition_entry_ramp_begins_on_first_exact_pcm(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="entry-test") as (native, tmp):
                source = tmp / "entry-ramp.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=880:duration=4",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                ready = threading.Event()
                started = threading.Event()

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "entry-token-b":
                        ready.set()
                    if event.event == "native_audio_probe_started" and event.slot_token == "entry-token-b":
                        started.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True,
                        host="127.0.0.1",
                        port=mock.port,
                        mount="/entry-test.mp3",
                        username="source",
                        password="source-secret",
                        bitrate_kbps=128,
                        stream_name="Entry ramp test",
                        public_stream=False,
                    )
                    native.start()
                    uri = (
                        'annotate:queue_id="18002",track_id="18102",station_key="entry-test",'
                        'wb_ab_slot_token="entry-token-b",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="3.000",wb_effective_end="3.800",wb_orig_total="4.000":'
                        f'{source}'
                    )
                    self.assertTrue(native.load_deck("B", uri, clear_slot=True))
                    descriptor = {
                        "cue_in_ms": 0,
                        "cue_out_ms": 3000,
                        "audio_start_ms": 0,
                        "play_start_ms": 0,
                        "transition_at_ms": 3000,
                        "effective_end_ms": 3800,
                        "source_end_ms": 4000,
                    }
                    base = {
                        "station_key": "entry-test",
                        "queue_id": 18002,
                        "slot_token": "entry-token-b",
                        "deck": "B",
                        "track_id": 18102,
                        "path": str(source),
                    }
                    native.sync_live_event({
                        **base,
                        "event": "deck_loaded",
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": descriptor,
                    })
                    self.assertTrue(ready.wait(4.0), "incoming candidate did not prebuffer")

                    now_ms = int(time.monotonic() * 1000)
                    wall_now_ms = int(time.time() * 1000)
                    native.sync_live_event({
                        **base,
                        "event": "transition_started",
                        "event_monotonic_time_ms": now_ms,
                        "event_wall_time_unix_ms": wall_now_ms,
                        "payload": {
                            "from_deck": "A",
                            "fade_out_duration_ms": 2000,
                            "entry_ramp_ms": 200,
                            "silence_hold_ms": 50,
                            "release_duration_ms": 2050,
                            "transition_started_wall_time_unix_ms": wall_now_ms - 1000,
                            "entry_started_wall_time_unix_ms": wall_now_ms - 900,
                        },
                    })
                    waiting = native.get_icecast_output_state()
                    self.assertTrue(waiting["transition_entry_waiting_for_pcm"])
                    self.assertEqual(int(waiting["transition_entry_start_monotonic_ms"]), 0)
                    self.assertAlmostEqual(float(waiting["deck_b_gain"]), 0.0, delta=0.001)

                    native.sync_live_event({
                        **base,
                        "event": "track_started",
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": {},
                    })
                    self.assertTrue(started.wait(3.0), "incoming voice did not start")
                    deadline = time.monotonic() + 1.0
                    active = native.get_icecast_output_state()
                    while active.get("transition_entry_waiting_for_pcm") and time.monotonic() < deadline:
                        time.sleep(0.01)
                        active = native.get_icecast_output_state()
                    self.assertFalse(active["transition_entry_waiting_for_pcm"])
                    self.assertGreater(int(active["transition_entry_pcm_start_monotonic_ms"]), 0)
                    self.assertEqual(
                        int(active["transition_entry_start_monotonic_ms"]),
                        int(active["transition_entry_pcm_start_monotonic_ms"]),
                    )
                    self.assertGreaterEqual(int(active["transition_entry_pcm_start_count"]), 1)
                    self.assertGreater(
                        int(active["transition_entry_pcm_start_monotonic_ms"]),
                        int(active["transition_entry_requested_monotonic_ms"]),
                    )
                    time.sleep(0.25)
                    completed = native.get_icecast_output_state()
                    self.assertAlmostEqual(float(completed["deck_b_gain"]), 1.0, delta=0.02)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()

    def test_persistent_encoder_streams_valid_mp3_and_consumed_track_pcm(self) -> None:
        if self.ffmpeg is None or self.ffprobe is None:
            self.skipTest("ffmpeg and ffprobe are required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="icecast-test") as (native, tmp):
                source = tmp / "tone.mp3"
                capture = tmp / "capture.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=997:duration=8",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                prebuffer = threading.Event()
                started = threading.Event()

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_prebuffer_ready":
                        prebuffer.set()
                    if event.event == "native_audio_probe_started":
                        started.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    state = native.configure_icecast_output(
                        enabled=True,
                        host="127.0.0.1",
                        port=mock.port,
                        mount="/native-test.mp3",
                        username="source",
                        password="source-secret",
                        bitrate_kbps=128,
                        stream_name="Web Broadcaster Test",
                        stream_description="Native station description",
                        stream_genre="Electronic / Test",
                        stream_url="https://example.test/radio",
                        public_stream=False,
                    )
                    self.assertTrue(state["enabled"])
                    native.start()
                    deadline = time.monotonic() + 8.0
                    while time.monotonic() < deadline:
                        state = native.get_icecast_output_state()
                        if state.get("connected") and int(state.get("encoded_bytes_sent") or 0) > 10000:
                            break
                        time.sleep(0.05)
                    self.assertTrue(state.get("connected"), state)
                    self.assertTrue(state.get("encoder_running"), state)

                    uri = (
                        'annotate:queue_id="15001",track_id="15101",station_key="icecast-test",'
                        'wb_ab_slot_token="icecast-token-a",wb_play_start="0.000",'
                        f'wb_crossfade_trigger="7.500",artist="Integration",title="Tone":{source}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    event_base = {
                        "station_key": "icecast-test",
                        "queue_id": 15001,
                        "slot_token": "icecast-token-a",
                        "deck": "A",
                        "track_id": 15101,
                        "path": str(source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                    }
                    native.sync_live_event({
                        **event_base,
                        "event": "deck_loaded",
                        "payload": {
                            "cue_in_ms": 0,
                            "cue_out_ms": 7500,
                            "fade_in_ms": 0,
                            "fade_out_ms": 0,
                        },
                    })
                    self.assertTrue(prebuffer.wait(4.0), "native candidate did not prebuffer")
                    native.sync_live_event({**event_base, "event": "track_started", "payload": {}})
                    self.assertTrue(started.wait(3.0), "native playback voice did not start")
                    time.sleep(1.5)
                    output = native.get_icecast_output_state()
                    engine_state = native.get_state()
                    self.assertEqual(output["primary_deck"], "A")
                    self.assertAlmostEqual(float(output["deck_a_gain"]), 1.0, delta=0.001)
                    self.assertAlmostEqual(float(output["deck_b_gain"]), 0.0, delta=0.001)
                    self.assertGreater(int(output["mixed_frames"]), int(output["silence_frames"]))
                    self.assertGreater(int(output["encoded_bytes_sent"]), 10000)
                    self.assertGreater(int(engine_state["native_audio_probe_played_duration_ms"]), 500)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
                time.sleep(0.2)
                capture.write_bytes(mock.data)
                self.assertGreater(capture.stat().st_size, 10000)
                probe = subprocess.run(
                    [
                        self.ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=format_name,duration",
                        "-of",
                        "json",
                        str(capture),
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr)
                details = json.loads(probe.stdout)
                self.assertEqual(details["format"]["format_name"], "mp3")

            self.assertTrue(mock.requests)
            request = mock.requests[0]
            self.assertTrue(request.startswith(b"SOURCE /native-test.mp3 ICE/1.0"))
            self.assertIn(b"Authorization: Basic ", request)
            self.assertNotIn(b"User-Agent:", request)
            self.assertIn(b"Ice-Bitrate: 128\r\n", request)
            self.assertIn(b"Ice-Description: Native station description\r\n", request)
            self.assertIn(b"Ice-Genre: Electronic / Test\r\n", request)
            self.assertIn(b"Ice-URL: https://example.test/radio\r\n", request)
            self.assertIn(
                b"Ice-Audio-Info: ice-samplerate=44100;ice-bitrate=128;ice-channels=2\r\n",
                request,
            )
            self.assertNotIn(b"source-secret", request)
        finally:
            mock.close()


    def test_pause_keeps_icecast_connected_with_silence_and_freezes_track_clock(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="pause-output-test", prebuffer_ms=500) as (native, tmp):
                source = tmp / "pause-tone.mp3"
                subprocess.run(
                    [
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "sine=frequency=740:duration=6",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                        "-q:a", "3", "-y", str(source),
                    ],
                    check=True,
                )
                native.configure_icecast_output(
                    enabled=True, host="127.0.0.1", port=mock.port,
                    mount="/pause-test.mp3", username="source", password="secret",
                    bitrate_kbps=128, stream_name="Pause Test", public_stream=False,
                )
                native.start()
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    output = native.get_icecast_output_state()
                    if output.get("connected") and int(output.get("encoded_bytes_sent") or 0) > 5000:
                        break
                    time.sleep(0.05)
                self.assertTrue(output.get("connected"), output)

                uri = (
                    'annotate:queue_id="15064",track_id="15163",station_key="pause-output-test",'
                    'wb_ab_slot_token="pause-output-token",wb_play_start="0.000",'
                    f'wb_crossfade_trigger="5.500",artist="Integration",title="Pause Tone":{source}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                event_base = {
                    "station_key": "pause-output-test", "queue_id": 15064,
                    "slot_token": "pause-output-token", "deck": "A",
                    "track_id": 15163, "path": str(source),
                    "event_monotonic_time_ms": int(time.monotonic() * 1000),
                    "event_wall_time_unix_ms": int(time.time() * 1000),
                }
                native.sync_live_event({
                    **event_base, "event": "deck_loaded",
                    "payload": {"cue_in_ms": 0, "cue_out_ms": 5500},
                })
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    if native.get_state().get("native_audio_probe_prebuffer_ready"):
                        break
                    time.sleep(0.025)
                self.assertTrue(native.get_state().get("native_audio_probe_prebuffer_ready"))
                native.sync_live_event({**event_base, "event": "track_started", "payload": {}})
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if int(native.get_state().get("native_audio_probe_position_ms") or 0) >= 350:
                        break
                    time.sleep(0.025)

                native.set_paused(True, station_key="pause-output-test")
                paused_state = native.get_state(station_key="pause-output-test")
                paused_position = int(paused_state.get("native_audio_probe_position_ms") or 0)
                before = native.get_icecast_output_state(station_key="pause-output-test")
                time.sleep(0.55)
                during_state = native.get_state(station_key="pause-output-test")
                during = native.get_icecast_output_state(station_key="pause-output-test")
                self.assertTrue(during_state.get("paused"), during_state)
                self.assertLessEqual(
                    abs(int(during_state.get("native_audio_probe_position_ms") or 0) - paused_position),
                    30,
                )
                self.assertTrue(during.get("connected"), during)
                self.assertGreater(int(during.get("encoded_bytes_sent") or 0), int(before.get("encoded_bytes_sent") or 0))
                self.assertGreater(int(during.get("silence_frames") or 0), int(before.get("silence_frames") or 0))
                self.assertEqual(int(during.get("send_error_count") or 0), 0)
                self.assertEqual(int(during.get("output_gap_count") or 0), 0)

                native.set_paused(False, station_key="pause-output-test")
                time.sleep(0.35)
                resumed = native.get_state(station_key="pause-output-test")
                self.assertFalse(resumed.get("paused"))
                self.assertGreater(
                    int(resumed.get("native_audio_probe_position_ms") or 0),
                    int(during_state.get("native_audio_probe_position_ms") or 0) + 180,
                )
                native.stop(station_key="pause-output-test")
                native.clear_icecast_output(station_key="pause-output-test")
        finally:
            mock.close()

    def test_slow_seek_uses_pcm_bridge_without_mixed_output_silence(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ffmpeg_seek_delay_sec=0.65, station_key="seek-bridge-test") as (native, tmp):
                source = tmp / "seek-bridge.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:duration=8",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                started = threading.Event()
                seek_applied = threading.Event()

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_started" and event.slot_token == "seek-bridge-token":
                        started.set()
                    if event.event == "native_audio_probe_seek_applied" and event.slot_token == "seek-bridge-token":
                        seek_applied.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True,
                        host="127.0.0.1",
                        port=mock.port,
                        mount="/native-test.mp3",
                        username="source",
                        password="source-secret",
                        bitrate_kbps=128,
                        stream_name="Seek bridge test",
                        public_stream=False,
                    )
                    native.start()
                    uri = (
                        'annotate:queue_id="9301",track_id="9401",station_key="seek-bridge-test",'
                        'wb_ab_slot_token="seek-bridge-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="2.000",wb_effective_end="7.500",wb_orig_total="8.000",'
                        f'artist="Test",title="Seek Bridge":{source}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    descriptor = {
                        "cue_in_ms": 0,
                        "cue_out_ms": 2000,
                        "audio_start_ms": 0,
                        "play_start_ms": 0,
                        "transition_at_ms": 2000,
                        "effective_end_ms": 7500,
                        "source_end_ms": 8000,
                    }
                    for event_name in ("deck_loaded", "track_started"):
                        native.sync_live_event({
                            "event": event_name,
                            "station_key": "seek-bridge-test",
                            "queue_id": 9301,
                            "slot_token": "seek-bridge-token",
                            "deck": "A",
                            "track_id": 9401,
                            "path": str(source),
                            "event_monotonic_time_ms": int(time.monotonic() * 1000),
                            "event_wall_time_unix_ms": int(time.time() * 1000),
                            "payload": descriptor,
                        })
                    self.assertTrue(started.wait(3.0), "initial track did not start")
                    time.sleep(0.25)
                    before = native.get_icecast_output_state()
                    native.sync_live_event({
                        "event": "track_seeked",
                        "station_key": "seek-bridge-test",
                        "queue_id": 9301,
                        "slot_token": "seek-bridge-token",
                        "deck": "A",
                        "track_id": 9401,
                        "path": str(source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": {"seek_position_ms": 5000, "seek_from_position_ms": 250},
                    })
                    self.assertTrue(seek_applied.wait(3.0), "seek replacement did not start")
                    time.sleep(0.15)
                    after = native.get_icecast_output_state()
                    self.assertEqual(
                        int(after.get("mixed_output_silence_count") or 0),
                        int(before.get("mixed_output_silence_count") or 0),
                    )
                    self.assertEqual(
                        int(after.get("output_underrun_count") or 0),
                        int(before.get("output_underrun_count") or 0),
                    )
                    self.assertEqual(
                        int(after.get("seek_bridge_count") or 0),
                        int(before.get("seek_bridge_count") or 0) + 1,
                    )
                    self.assertGreater(int(after.get("seek_bridge_bytes") or 0), int(before.get("seek_bridge_bytes") or 0))
                    self.assertEqual(
                        int(after.get("seek_flush_count") or 0),
                        int(before.get("seek_flush_count") or 0) + 1,
                    )
                    self.assertFalse(bool(after.get("deck_a_seek_pending")))
                finally:
                    unsubscribe()
        finally:
            mock.close()

    def test_same_deck_hard_handoff_counts_stale_pcm_without_false_fifo_overrun(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="hard-handoff-test") as (native, tmp):
                old_source = tmp / "old.mp3"
                new_source = tmp / "new.mp3"
                for source, frequency in ((old_source, 440), (new_source, 880)):
                    subprocess.run(
                        [
                            self.ffmpeg,
                            "-nostdin",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-f",
                            "lavfi",
                            "-i",
                            f"sine=frequency={frequency}:duration=4",
                            "-ar",
                            "44100",
                            "-ac",
                            "2",
                            "-codec:a",
                            "libmp3lame",
                            "-q:a",
                            "3",
                            "-y",
                            str(source),
                        ],
                        check=True,
                    )

                ready = {"old-token": threading.Event(), "new-token": threading.Event()}
                started = {"old-token": threading.Event(), "new-token": threading.Event()}

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in ready:
                        ready[event.slot_token].set()
                    if event.event == "native_audio_probe_started" and event.slot_token in started:
                        started[event.slot_token].set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True,
                        host="127.0.0.1",
                        port=mock.port,
                        mount="/native-test.mp3",
                        username="source",
                        password="source-secret",
                        bitrate_kbps=128,
                        stream_name="Hard handoff test",
                        public_stream=False,
                    )
                    native.start()

                    def load_and_confirm(source: Path, queue_id: int, token: str) -> None:
                        uri = (
                            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
                            f'station_key="hard-handoff-test",wb_ab_slot_token="{token}",'
                            'wb_audio_start="0.000",wb_play_start="0.000",'
                            'wb_crossfade_trigger="1.000",wb_effective_end="3.800",'
                            f'wb_orig_total="4.000",artist="Test",title="{token}":{source}'
                        )
                        self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                        native.sync_live_event({
                            "event": "deck_loaded",
                            "station_key": "hard-handoff-test",
                            "queue_id": queue_id,
                            "slot_token": token,
                            "deck": "A",
                            "track_id": queue_id + 100,
                            "path": str(source),
                            "event_monotonic_time_ms": int(time.monotonic() * 1000),
                            "event_wall_time_unix_ms": int(time.time() * 1000),
                            "payload": {
                                "cue_in_ms": 0,
                                "cue_out_ms": 1000,
                                "audio_start_ms": 0,
                                "play_start_ms": 0,
                                "transition_at_ms": 1000,
                                "effective_end_ms": 3800,
                                "source_end_ms": 4000,
                            },
                        })
                        self.assertTrue(ready[token].wait(4.0), f"{token} did not prebuffer")

                    load_and_confirm(old_source, 17001, "old-token")
                    native.sync_live_event({
                        "event": "track_started",
                        "station_key": "hard-handoff-test",
                        "queue_id": 17001,
                        "slot_token": "old-token",
                        "deck": "A",
                        "track_id": 17101,
                        "path": str(old_source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": {},
                    })
                    self.assertTrue(started["old-token"].wait(2.0))

                    load_and_confirm(new_source, 17002, "new-token")
                    before = native.get_icecast_output_state()
                    native.sync_live_event({
                        "event": "track_started",
                        "station_key": "hard-handoff-test",
                        "queue_id": 17002,
                        "slot_token": "new-token",
                        "deck": "A",
                        "track_id": 17102,
                        "path": str(new_source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": {"hard_handoff": True, "previous_deck": "A"},
                    })
                    self.assertTrue(started["new-token"].wait(2.0))
                    time.sleep(0.45)
                    after = native.get_icecast_output_state()

                    self.assertEqual(after["primary_deck"], "A")
                    self.assertEqual(
                        int(after.get("fifo_overrun_count") or 0),
                        int(before.get("fifo_overrun_count") or 0),
                    )
                    self.assertEqual(
                        int(after.get("fifo_overrun_bytes") or 0),
                        int(before.get("fifo_overrun_bytes") or 0),
                    )
                    self.assertGreater(
                        int(after.get("stale_pcm_drop_count") or 0),
                        int(before.get("stale_pcm_drop_count") or 0),
                    )
                    self.assertGreater(
                        int(after.get("stale_pcm_drop_bytes") or 0),
                        int(before.get("stale_pcm_drop_bytes") or 0),
                    )
                    self.assertIn("deck_fifo_empty_count", after)
                    self.assertIn("mixed_output_silence_count", after)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()


    def test_metadata_reapply_and_encoder_kill_recovery(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="metadata-test") as (native, tmp):
                source = tmp / "metadata-recovery.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=523:duration=8",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=mock.port,
                    mount="/native-test.mp3",
                    username="source",
                    password="source-secret",
                    bitrate_kbps=128,
                    stream_name="Metadata recovery test",
                    public_stream=False,
                )
                native.start()
                connect_deadline = time.monotonic() + 5.0
                output = native.get_icecast_output_state()
                while not output.get("connected") and time.monotonic() < connect_deadline:
                    time.sleep(0.05)
                    output = native.get_icecast_output_state()
                self.assertTrue(output.get("connected"), output)

                uri = (
                    'annotate:queue_id="26001",track_id="26101",station_key="metadata-test",'
                    'wb_ab_slot_token="metadata-token",wb_audio_start="0.000",wb_play_start="0.000",'
                    'wb_crossfade_trigger="7.000",wb_effective_end="7.500",wb_orig_total="8.000",'
                    f'artist="Metadata Artist",title="Metadata Title":{source}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                descriptor = {
                    "cue_in_ms": 0,
                    "cue_out_ms": 7000,
                    "audio_start_ms": 0,
                    "play_start_ms": 0,
                    "transition_at_ms": 7000,
                    "effective_end_ms": 7500,
                    "source_end_ms": 8000,
                }
                for event_name in ("deck_loaded", "track_started"):
                    native.sync_live_event({
                        "event": event_name,
                        "station_key": "metadata-test",
                        "queue_id": 26001,
                        "slot_token": "metadata-token",
                        "deck": "A",
                        "track_id": 26101,
                        "path": str(source),
                        "artist": "Metadata Artist",
                        "title": "Metadata Title",
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": descriptor,
                    })

                metadata_deadline = time.monotonic() + 5.0
                output = native.get_icecast_output_state()
                while int(output.get("metadata_applied") or 0) < 1 and time.monotonic() < metadata_deadline:
                    time.sleep(0.05)
                    output = native.get_icecast_output_state()
                self.assertEqual(output.get("metadata_value"), "Metadata Artist - Metadata Title")
                self.assertEqual(int(output.get("queue_id") or 0), 26001)
                self.assertEqual(output.get("slot_token"), "metadata-token")
                self.assertEqual(int(output.get("metadata_requested") or 0), 1)
                self.assertGreaterEqual(int(output.get("metadata_applied") or 0), 1)
                self.assertEqual(int(output.get("metadata_failed") or 0), 0)
                self.assertTrue(mock.metadata_requests)
                metadata_request = mock.metadata_requests[-1]
                self.assertNotIn(b"User-Agent:", metadata_request)
                self.assertIn(b"mount=%2Fnative-test.mp3", metadata_request)
                self.assertIn(b"song=Metadata%20Artist%20-%20Metadata%20Title", metadata_request)
                self.assertNotIn(b"source-secret", metadata_request)

                for field in (
                    "last_encoded_data_monotonic_ms",
                    "last_icecast_send_monotonic_ms",
                    "encoder_stall_count",
                    "icecast_stall_count",
                    "encoder_restart_count",
                    "reconnect_count",
                    "consecutive_send_errors",
                ):
                    self.assertIn(field, output)
                old_generation = int(output.get("encoder_generation") or 0)
                self.assertEqual(int(output.get("encoder_pid") or 0), 0)
                self.assertEqual(output.get("encoder_backend"), "embedded_libav")
                self.assertGreater(old_generation, 0)
                native.kill_icecast_encoder()

                recovery_deadline = time.monotonic() + 8.0
                recovered = native.get_icecast_output_state()
                while time.monotonic() < recovery_deadline:
                    recovered = native.get_icecast_output_state()
                    if (
                        recovered.get("connected")
                        and recovered.get("encoder_running")
                        and int(recovered.get("encoder_generation") or 0) > old_generation
                        and int(recovered.get("encoder_restart_count") or 0) >= 1
                        and int(recovered.get("metadata_applied") or 0) >= 2
                    ):
                        break
                    time.sleep(0.05)
                self.assertTrue(recovered.get("connected"), recovered)
                self.assertTrue(recovered.get("encoder_running"), recovered)
                self.assertEqual(int(recovered.get("encoder_pid") or 0), 0)
                self.assertGreater(int(recovered.get("encoder_generation") or 0), old_generation)
                self.assertEqual(int(recovered.get("encoder_restart_count") or 0), 1)
                self.assertEqual(int(recovered.get("encoder_kill_test_count") or 0), 1)
                self.assertGreaterEqual(int(recovered.get("reconnect_count") or 0), 1)
                self.assertGreaterEqual(int(recovered.get("metadata_applied") or 0), 2)
                self.assertEqual(int(recovered.get("old_encoder_pid") or 0), 0)
                self.assertEqual(int(recovered.get("new_encoder_pid") or 0), 0)
                self.assertFalse(recovered.get("old_encoder_reaped"), recovered)
                self.assertEqual(int(recovered.get("encoder_reap_count") or 0), 0)
                self.assertEqual(int(recovered.get("zombie_encoder_count") or 0), 0)
                self.assertGreaterEqual(int(recovered.get("encoded_bytes_total") or 0), int(recovered.get("icecast_sent_bytes_total") or 0))
                self.assertGreaterEqual(len(mock.metadata_requests), 2)
        finally:
            mock.close()

    def test_metadata_uses_live_tags_optional_year_and_extension_free_fallback(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="metadata-year-test") as (native, tmp):
                source = tmp / "Fallback.Title.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:duration=10",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                configured = native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=mock.port,
                    mount="/native-year-test.mp3",
                    username="source",
                    password="source-secret",
                    bitrate_kbps=128,
                    stream_name="Metadata year test",
                    public_stream=False,
                    add_year_to_metadata=True,
                )
                self.assertTrue(configured["add_year_to_metadata"])
                native.start()
                connect_deadline = time.monotonic() + 5.0
                output = native.get_icecast_output_state()
                while not output.get("connected") and time.monotonic() < connect_deadline:
                    time.sleep(0.05)
                    output = native.get_icecast_output_state()
                self.assertTrue(output.get("connected"), output)

                # Intentionally omit timing fields. The live artist/title/year
                # must still replace any path-derived descriptor metadata.
                native.sync_live_event({
                    "event": "track_started",
                    "station_key": "metadata-year-test",
                    "queue_id": 28001,
                    "slot_token": "metadata-year-token-a",
                    "deck": "A",
                    "track_id": 28101,
                    "path": str(source),
                    "artist": "Live Artist",
                    "title": "Live Title",
                    "year": "Recorded 1999 remaster",
                    "event_monotonic_time_ms": int(time.monotonic() * 1000),
                    "event_wall_time_unix_ms": int(time.time() * 1000),
                    "payload": {},
                })
                first_deadline = time.monotonic() + 5.0
                output = native.get_icecast_output_state()
                while int(output.get("metadata_applied") or 0) < 1 and time.monotonic() < first_deadline:
                    time.sleep(0.05)
                    output = native.get_icecast_output_state()
                self.assertEqual(output.get("metadata_value"), "Live Artist - Live Title (1999)")
                self.assertNotIn(".mp3", str(output.get("metadata_value") or ""))
                self.assertTrue(mock.metadata_requests)
                self.assertIn(
                    b"song=Live%20Artist%20-%20Live%20Title%20%281999%29",
                    mock.metadata_requests[-1],
                )

                first_applied = int(output.get("metadata_applied") or 0)
                reconfigured = native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=mock.port,
                    mount="/native-year-test.mp3",
                    username="source",
                    password="",
                    bitrate_kbps=128,
                    stream_name="Metadata year test",
                    public_stream=False,
                    add_year_to_metadata=False,
                )
                self.assertFalse(reconfigured["add_year_to_metadata"])
                toggle_deadline = time.monotonic() + 8.0
                output = native.get_icecast_output_state()
                while time.monotonic() < toggle_deadline:
                    output = native.get_icecast_output_state()
                    if (
                        output.get("connected")
                        and int(output.get("metadata_applied") or 0) > first_applied
                        and output.get("metadata_value") == "Live Artist - Live Title"
                    ):
                        break
                    time.sleep(0.05)
                self.assertEqual(output.get("metadata_value"), "Live Artist - Live Title")
                self.assertGreater(int(output.get("metadata_applied") or 0), first_applied)

                second_applied = int(output.get("metadata_applied") or 0)
                native.sync_live_event({
                    "event": "track_started",
                    "station_key": "metadata-year-test",
                    "queue_id": 28002,
                    "slot_token": "metadata-year-token-b",
                    "deck": "B",
                    "track_id": 28102,
                    "path": str(source),
                    "artist": "",
                    "title": "",
                    "year": "2001",
                    "event_monotonic_time_ms": int(time.monotonic() * 1000),
                    "event_wall_time_unix_ms": int(time.time() * 1000),
                    "payload": {},
                })
                fallback_deadline = time.monotonic() + 5.0
                output = native.get_icecast_output_state()
                while (
                    int(output.get("metadata_applied") or 0) <= second_applied
                    and time.monotonic() < fallback_deadline
                ):
                    time.sleep(0.05)
                    output = native.get_icecast_output_state()
                self.assertEqual(output.get("metadata_value"), "Fallback.Title")
                self.assertNotIn(".mp3", str(output.get("metadata_value") or ""))
        finally:
            mock.close()

    def test_icecast_disconnect_reconnects_without_encoder_failure_count(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="reconnect-test") as (native, tmp):
                source = tmp / "reconnect.mp3"
                subprocess.run(
                    [
                        self.ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=659:duration=8",
                        "-ar",
                        "44100",
                        "-ac",
                        "2",
                        "-codec:a",
                        "libmp3lame",
                        "-q:a",
                        "3",
                        "-y",
                        str(source),
                    ],
                    check=True,
                )
                native.configure_icecast_output(
                    enabled=True,
                    host="127.0.0.1",
                    port=mock.port,
                    mount="/native-test.mp3",
                    username="source",
                    password="source-secret",
                    bitrate_kbps=128,
                    stream_name="Reconnect test",
                    public_stream=False,
                )
                native.start()
                uri = (
                    'annotate:queue_id="27001",track_id="27101",station_key="reconnect-test",'
                    'wb_ab_slot_token="reconnect-token",wb_audio_start="0.000",wb_play_start="0.000",'
                    'wb_crossfade_trigger="7.000",wb_effective_end="7.500",wb_orig_total="8.000",'
                    f'artist="Reconnect Artist",title="Reconnect Title":{source}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                descriptor = {
                    "cue_in_ms": 0,
                    "cue_out_ms": 7000,
                    "audio_start_ms": 0,
                    "play_start_ms": 0,
                    "transition_at_ms": 7000,
                    "effective_end_ms": 7500,
                    "source_end_ms": 8000,
                }
                for event_name in ("deck_loaded", "track_started"):
                    native.sync_live_event({
                        "event": event_name,
                        "station_key": "reconnect-test",
                        "queue_id": 27001,
                        "slot_token": "reconnect-token",
                        "deck": "A",
                        "track_id": 27101,
                        "path": str(source),
                        "artist": "Reconnect Artist",
                        "title": "Reconnect Title",
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                        "payload": descriptor,
                    })
                ready_deadline = time.monotonic() + 5.0
                before = native.get_icecast_output_state()
                while time.monotonic() < ready_deadline:
                    before = native.get_icecast_output_state()
                    if (
                        before.get("connected")
                        and int(before.get("metadata_applied") or 0) >= 1
                        and int(before.get("icecast_sent_bytes_total") or 0) > 0
                    ):
                        break
                    time.sleep(0.05)
                self.assertTrue(before.get("connected"), before)
                self.assertEqual(int(before.get("encoder_restart_count") or 0), 0)
                initial_connects = int(before.get("connect_count") or 0)
                initial_metadata_applied = int(before.get("metadata_applied") or 0)
                initial_encoder_generation = int(before.get("encoder_generation") or 0)
                self.assertEqual(int(before.get("encoder_pid") or 0), 0)
                self.assertEqual(before.get("encoder_backend"), "embedded_libav")
                self.assertGreater(initial_encoder_generation, 0)
                mock.disconnect_sources()

                reconnect_deadline = time.monotonic() + 8.0
                after = native.get_icecast_output_state()
                while time.monotonic() < reconnect_deadline:
                    after = native.get_icecast_output_state()
                    if (
                        after.get("connected")
                        and int(after.get("connect_count") or 0) > initial_connects
                        and int(after.get("metadata_applied") or 0) > initial_metadata_applied
                    ):
                        break
                    time.sleep(0.05)
                self.assertTrue(after.get("connected"), after)
                self.assertGreater(int(after.get("connect_count") or 0), initial_connects)
                gap_deadline = time.monotonic() + 2.0
                while int(after.get("output_gap_count") or 0) < 1 and time.monotonic() < gap_deadline:
                    time.sleep(0.05)
                    after = native.get_icecast_output_state()
                self.assertGreaterEqual(int(after.get("reconnect_count") or 0), 1)
                self.assertGreaterEqual(int(after.get("disconnect_count") or 0), 1)
                self.assertEqual(int(after.get("encoder_restart_count") or 0), 0)
                self.assertEqual(int(after.get("pipeline_restart_count") or 0), 0)
                self.assertEqual(int(after.get("encoder_pid") or 0), 0)
                self.assertEqual(int(after.get("encoder_generation") or 0), initial_encoder_generation)
                self.assertEqual(int(after.get("dsp_process_replacement_count") or 0), 0)
                self.assertGreaterEqual(int(after.get("output_gap_count") or 0), 1)
                self.assertGreaterEqual(int(after.get("max_output_gap_ms") or 0), 250)
                self.assertEqual(after.get("last_output_gap_reason"), "reconnect_or_encoder_restart")
                self.assertEqual(int(after.get("zombie_encoder_count") or 0), 0)
                self.assertGreater(int(after.get("metadata_applied") or 0), initial_metadata_applied)
                self.assertEqual(after.get("metadata_value"), "Reconnect Artist - Reconnect Title")
                self.assertGreaterEqual(len(mock.source_requests), 2)
                self.assertGreaterEqual(len(mock.metadata_requests), 2)
        finally:
            mock.close()


    def test_late_event_injection_after_stop_keeps_audio_state_empty(self) -> None:
        with self._daemon(station_key="late-event-test") as (native, _tmp):
            native.stop()
            state = native.inject_late_events()
            self.assertGreater(int(state.get("late_load_rejected_count") or 0), 0)
            self.assertGreater(int(state.get("late_event_ignored_count") or 0), 0)
            self.assertFalse(bool(state.get("running")))
            self.assertFalse(bool(state.get("accepting_loads")))
            self.assertEqual(str(state.get("native_audio_probe_slot_token") or ""), "")
            self.assertEqual(str(state.get("native_audio_probe_candidate_primary_slot_token") or ""), "")
            self.assertEqual(int(state.get("native_audio_probe_candidate_alias_count") or 0), 0)
            self.assertEqual(int(state.get("native_audio_probe_ring_buffer_bytes") or 0), 0)
            self.assertEqual(int(state.get("native_audio_deck_a_ring_buffer_bytes") or 0), 0)
            self.assertEqual(int(state.get("native_audio_deck_b_ring_buffer_bytes") or 0), 0)


    def test_initial_activation_is_not_an_underrun_before_first_output_pcm(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=1000, station_key="startup-ready-test") as (native, tmp):
                source = tmp / "startup-ready-tone.mp3"
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=620:duration=3",
                    "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                    "-q:a", "3", "-y", str(source),
                ], check=True)
                prebuffer = threading.Event()
                started = threading.Event()
                underruns: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_prebuffer_ready":
                        prebuffer.set()
                    if event.event == "native_audio_probe_started":
                        started.set()
                    if event.event == "native_output_underrun":
                        underruns.append(event)

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/startup-ready.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Startup ready", public_stream=False,
                    )
                    native.start()
                    uri = (
                        'annotate:queue_id="18991",track_id="19091",station_key="startup-ready-test",'
                        'wb_ab_slot_token="startup-ready-token",wb_audio_start="0.000",'
                        'wb_play_start="0.000",wb_crossfade_trigger="2.500",'
                        f'wb_effective_end="2.900",wb_orig_total="3.000":{source}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    base = {
                        "station_key": "startup-ready-test", "queue_id": 18991,
                        "slot_token": "startup-ready-token", "deck": "A",
                        "track_id": 19091, "path": str(source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                    }
                    descriptor = {
                        "audio_start_ms": 0, "play_start_ms": 0,
                        "transition_at_ms": 2500, "effective_end_ms": 2900,
                        "source_end_ms": 3000,
                    }
                    native.sync_live_event({**base, "event": "deck_loaded", "payload": descriptor})
                    self.assertTrue(prebuffer.wait(4.0))
                    before = native.get_icecast_output_state()
                    self.assertEqual(int(before.get("output_underrun_count") or 0), 0)
                    native.sync_live_event({**base, "event": "track_started", "payload": descriptor})
                    self.assertTrue(started.wait(2.0))
                    time.sleep(0.25)
                    after = native.get_icecast_output_state()
                    self.assertGreater(int(after.get("deck_a_fifo_high_water_bytes") or 0), 0)
                    self.assertEqual(int(after.get("output_underrun_count") or 0), 0, after)
                    self.assertEqual(underruns, [])
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()


    def test_output_underrun_emits_actionable_snapshot(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=250, prebuffer_ms=100, station_key="underrun-test") as (native, tmp):
                source = tmp / "underrun-tone.mp3"
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=640:duration=4",
                    "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                    "-q:a", "3", "-y", str(source),
                ], check=True)
                prebuffer = threading.Event()
                underrun = threading.Event()
                snapshots: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_audio_probe_prebuffer_ready":
                        prebuffer.set()
                    if event.event == "native_output_underrun":
                        snapshots.append(event)
                        underrun.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/underrun-test.mp3", username="source",
                        password="secret", bitrate_kbps=128,
                        stream_name="Underrun diagnostics", public_stream=False,
                    )
                    native.start()
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline:
                        if native.get_icecast_output_state().get("connected"):
                            break
                        time.sleep(0.05)
                    native.configure_fault(
                        "decoder_stall", target_deck="A",
                        target_slot_token="underrun-output-token",
                        after_ms=120, duration_ms=700, once=True,
                    )
                    uri = (
                        'annotate:queue_id="19001",track_id="19101",station_key="underrun-test",'
                        'wb_ab_slot_token="underrun-output-token",wb_audio_start="0.000",'
                        'wb_play_start="0.000",wb_crossfade_trigger="3.500",'
                        f'wb_effective_end="3.900",wb_orig_total="4.000":{source}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    base = {
                        "station_key": "underrun-test", "queue_id": 19001,
                        "slot_token": "underrun-output-token", "deck": "A",
                        "track_id": 19101, "path": str(source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                    }
                    native.sync_live_event({**base, "event": "deck_loaded", "payload": {
                        "audio_start_ms": 0, "play_start_ms": 0,
                        "transition_at_ms": 3500, "effective_end_ms": 3900,
                        "source_end_ms": 4000,
                    }})
                    self.assertTrue(prebuffer.wait(4.0))
                    native.sync_live_event({**base, "event": "track_started", "payload": {}})
                    self.assertTrue(underrun.wait(6.0), "output underrun snapshot was not emitted")
                    payload = snapshots[0].payload
                    self.assertEqual(payload["reason"], "mixed_output_pcm_missing")
                    self.assertEqual(payload["deck_a_queue_id"], 19001)
                    self.assertEqual(payload["deck_a_slot_token"], "underrun-output-token")
                    self.assertTrue(payload["deck_a_expected"])
                    self.assertEqual(payload["deck_a_pcm_bytes"], 0)
                    self.assertGreaterEqual(payload["output_underrun_count"], 1)
                    self.assertIn("deck_a_decoder_ring_bytes", payload)
                    self.assertIn("tick_lateness_ms", payload)
                    self.assertIn("dsp_ready", payload)
                    time.sleep(1.0)
                    state = native.get_icecast_output_state()
                    self.assertGreater(state.get("output_underrun_count", 0), 1, state)
                    self.assertEqual(state.get("output_underrun_event_count"), 1, state)
                    self.assertGreater(state.get("output_underrun_suppressed_event_count", 0), 0, state)
                    self.assertEqual(len(snapshots), 1, snapshots)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()


    def test_natural_eof_releases_output_before_late_stream_enable(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="natural-eof-release") as (native, tmp):
                source = tmp / "short-tone.mp3"
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                    "-q:a", "3", "-y", str(source),
                ], check=True)
                eof = threading.Event()
                events: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    events.append(event)
                    if event.event == "native_audio_probe_eof":
                        eof.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.start()
                    uri = (
                        'annotate:queue_id="19501",track_id="19502",station_key="natural-eof-release",'
                        'wb_ab_slot_token="natural-eof-token",wb_audio_start="0.000",'
                        'wb_play_start="0.000",wb_crossfade_trigger="0.900",'
                        f'wb_effective_end="1.000",wb_orig_total="1.000":{source}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    base = {
                        "station_key": "natural-eof-release", "queue_id": 19501,
                        "slot_token": "natural-eof-token", "deck": "A",
                        "track_id": 19502, "path": str(source),
                        "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000),
                    }
                    native.sync_live_event({**base, "event": "deck_loaded", "payload": {
                        "audio_start_ms": 0, "play_start_ms": 0,
                        "transition_at_ms": 900, "effective_end_ms": 1000,
                        "source_end_ms": 1000,
                    }})
                    native.sync_live_event({**base, "event": "track_started", "payload": {}})
                    self.assertTrue(eof.wait(5.0), "natural EOF did not arrive")
                    eof_event = next(event for event in events if event.event == "native_audio_probe_eof")
                    self.assertEqual(eof_event.payload.get("output_track_released"), True)
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/natural-eof.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Natural EOF", public_stream=False,
                    )
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        if native.get_icecast_output_state().get("connected"):
                            break
                        time.sleep(0.05)
                    time.sleep(0.5)
                    state = native.get_icecast_output_state()
                    self.assertTrue(state.get("connected"), state)
                    self.assertEqual(state.get("output_underrun_count"), 0, state)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()

    def test_mount_busy_403_uses_slow_reconnect_backoff(self) -> None:
        mock = _RejectingIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="mount-busy") as (native, _tmp):
                native.configure_icecast_output(
                    enabled=True, host="127.0.0.1", port=mock.port,
                    mount="/busy.mp3", username="source", password="secret",
                    bitrate_kbps=128, stream_name="Busy", public_stream=False,
                )
                native.start()
                deadline = time.monotonic() + 3.0
                state = {}
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if state.get("reconnect_backoff_seconds"):
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("reconnect_backoff_seconds"), 5, state)
                self.assertIn("403", str(state.get("error") or ""), state)
                time.sleep(1.0)
                self.assertEqual(len(mock.request_times), 1, mock.request_times)
        finally:
            mock.close()


    def test_manager_accepts_eight_outputs_and_rejects_a_ninth(self) -> None:
        with self._daemon(station_key="manager-test") as (native, _tmp):
            for index in range(8):
                state = native.configure_icecast_output(
                    output_id=f"slot{index}",
                    codec="aac_he_v2" if index % 2 else "mp3",
                    enabled=False,
                    host="",
                    port=8000,
                    mount=f"/slot{index}",
                    username="source",
                    password="",
                    bitrate_kbps=64 if index % 2 else 128,
                    stream_name=f"Slot {index}",
                )
                self.assertEqual(state.get("output_count"), index + 1, state)
            self.assertEqual(native.get_icecast_output_state().get("output_count"), 8)
            with self.assertRaisesRegex(NativeEngineError, "Maximum number of native stream outputs reached"):
                native.configure_icecast_output(
                    output_id="slot8", codec="mp3", enabled=False,
                    host="", port=8000, mount="/slot8", username="source",
                    password="", bitrate_kbps=128, stream_name="Slot 8",
                )

    def test_aacplus_output_runs_without_mp3_output(self) -> None:
        aac_mock = _MockIcecastSource()
        aac_mock.start()
        try:
            with self._daemon(station_key="aac-only") as (native, _tmp):
                configured = native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/aac-only.aac",
                    username="source", password="secret", bitrate_kbps=64,
                    stream_name="AAC+ only", stream_description="AAC description",
                    stream_genre="AAC genre", stream_url="https://example.test/aac",
                )
                self.assertEqual(configured.get("output_count"), 1, configured)
                self.assertEqual(configured.get("output_id"), "aacplus", configured)
                self.assertEqual(configured.get("codec"), "aac_he_v2", configured)
                native.start()
                deadline = time.monotonic() + 8.0
                state = native.get_icecast_output_state()
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if state.get("connected_output_count") == 1 and len(aac_mock.data) > 2000:
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("enabled_output_count"), 1, state)
                self.assertEqual(state.get("connected_output_count"), 1, state)
                self.assertEqual(state.get("output_id"), "aacplus", state)
                self.assertEqual(state.get("codec"), "aac_he_v2", state)
                outputs = state.get("outputs") or []
                self.assertEqual(len(outputs), 1, state)
                self.assertEqual(outputs[0].get("output_id"), "aacplus")
                self.assertTrue(outputs[0].get("connected"), state)
                self.assertGreater(len(aac_mock.data), 2000)
                self.assertEqual(bytes(aac_mock.data[:1]), b"\xff")
                self.assertEqual(bytes(aac_mock.data[1:2])[0] & 0xF0, 0xF0)
                self.assertIn(b"Content-Type: audio/aacp", aac_mock.source_requests[0])
                self.assertIn(b"Ice-Bitrate: 64\r\n", aac_mock.source_requests[0])
                self.assertIn(b"Ice-Description: AAC description\r\n", aac_mock.source_requests[0])
                self.assertIn(b"Ice-Genre: AAC genre\r\n", aac_mock.source_requests[0])
                self.assertIn(b"Ice-URL: https://example.test/aac\r\n", aac_mock.source_requests[0])
                self.assertIn(
                    b"Ice-Audio-Info: ice-samplerate=44100;ice-bitrate=64;ice-channels=2\r\n",
                    aac_mock.source_requests[0],
                )
        finally:
            aac_mock.close()

    def test_simultaneous_mp3_and_aacplus_outputs_are_independent(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required to create the source fixture")
        mp3_mock = _MockIcecastSource()
        aac_mock = _MockIcecastSource()
        mp3_mock.start()
        aac_mock.start()
        try:
            with self._daemon(station_key="multi-output") as (native, tmp):
                source = tmp / "multi-output.mp3"
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=16",
                    "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                    "-q:a", "3", "-y", str(source),
                ], check=True)
                native.configure_icecast_output(
                    output_id="mp3", codec="mp3", enabled=True,
                    host="127.0.0.1", port=mp3_mock.port, mount="/multi.mp3",
                    username="source", password="secret", bitrate_kbps=128,
                    stream_name="Native MP3", stream_description="MP3 description",
                    stream_genre="MP3 genre", stream_url="https://example.test/mp3",
                )
                configured = native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/multi.aac",
                    username="source", password="secret", bitrate_kbps=64,
                    stream_name="Native AAC+", stream_description="AAC description",
                    stream_genre="AAC genre", stream_url="https://example.test/aac",
                )
                self.assertTrue(configured["multi_output"])
                self.assertEqual(configured["output_count"], 2)
                self.assertEqual(
                    {(item["output_id"], item["codec"]) for item in configured["outputs"]},
                    {("mp3", "mp3"), ("aacplus", "aac_he_v2")},
                )
                native.start()
                uri = (
                    'annotate:queue_id="31001",track_id="31101",station_key="multi-output",'
                    'wb_ab_slot_token="multi-token",wb_audio_start="0.000",wb_play_start="0.000",'
                    'wb_crossfade_trigger="15.000",wb_effective_end="15.500",wb_orig_total="16.000",'
                    f'artist="Multi",title="Output":{source}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                descriptor = {
                    "cue_in_ms": 0, "cue_out_ms": 15000, "audio_start_ms": 0,
                    "play_start_ms": 0, "transition_at_ms": 15000,
                    "effective_end_ms": 15500, "source_end_ms": 16000,
                }
                for event_name in ("deck_loaded", "track_started"):
                    native.sync_live_event({
                        "event": event_name, "station_key": "multi-output",
                        "queue_id": 31001, "slot_token": "multi-token", "deck": "A",
                        "track_id": 31101, "path": str(source), "artist": "Multi",
                        "title": "Output", "event_monotonic_time_ms": int(time.monotonic() * 1000),
                        "event_wall_time_unix_ms": int(time.time() * 1000), "payload": descriptor,
                    })
                deadline = time.monotonic() + 8.0
                state = native.get_icecast_output_state()
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if state.get("connected_output_count") == 2 and len(mp3_mock.data) > 5000 and len(aac_mock.data) > 3000:
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("connected_output_count"), 2, state)
                self.assertGreater(len(mp3_mock.data), 5000)
                self.assertGreater(len(aac_mock.data), 3000)
                self.assertEqual(bytes(mp3_mock.data[:1]), b"\xff")
                self.assertEqual(bytes(aac_mock.data[:1]), b"\xff")
                self.assertEqual(bytes(aac_mock.data[1:2])[0] & 0xF0, 0xF0)
                self.assertIn(b"Content-Type: audio/mpeg", mp3_mock.source_requests[0])
                self.assertIn(b"Ice-Bitrate: 128\r\n", mp3_mock.source_requests[0])
                self.assertIn(b"Ice-Description: MP3 description\r\n", mp3_mock.source_requests[0])
                self.assertIn(b"Ice-Genre: MP3 genre\r\n", mp3_mock.source_requests[0])
                self.assertIn(b"Ice-URL: https://example.test/mp3\r\n", mp3_mock.source_requests[0])
                self.assertIn(
                    b"Ice-Audio-Info: ice-samplerate=44100;ice-bitrate=128;ice-channels=2\r\n",
                    mp3_mock.source_requests[0],
                )
                self.assertIn(b"Content-Type: audio/aacp", aac_mock.source_requests[0])
                self.assertIn(
                    b"Ice-Audio-Info: ice-samplerate=44100;ice-bitrate=64;ice-channels=2\r\n",
                    aac_mock.source_requests[0],
                )

                encoder_pid = int(state.get("encoder_pid") or 0)
                aac_bytes_before = len(aac_mock.data)
                mp3_connects_before = next(item for item in state["outputs"] if item["output_id"] == "mp3")["connect_count"]
                mp3_mock.disconnect_sources()
                recovery_deadline = time.monotonic() + 5.0
                while time.monotonic() < recovery_deadline:
                    state = native.get_icecast_output_state()
                    mp3_state = next(item for item in state["outputs"] if item["output_id"] == "mp3")
                    if mp3_state["connect_count"] > mp3_connects_before and mp3_state["connected"]:
                        break
                    time.sleep(0.05)
                self.assertEqual(int(state.get("encoder_pid") or 0), encoder_pid)
                self.assertEqual(int(state.get("pipeline_restart_count") or 0), 0)
                self.assertGreater(len(aac_mock.data), aac_bytes_before)
                mp3_state = next(item for item in state["outputs"] if item["output_id"] == "mp3")
                aac_state = next(item for item in state["outputs"] if item["output_id"] == "aacplus")
                self.assertGreater(mp3_state["connect_count"], mp3_connects_before)
                self.assertEqual(aac_state["reconnect_count"], 0)
        finally:
            mp3_mock.close()
            aac_mock.close()


    def test_outputs_can_be_removed_and_readded_independently(self) -> None:
        mp3_mock = _MockIcecastSource()
        aac_mock = _MockIcecastSource()
        mp3_mock.start()
        aac_mock.start()
        try:
            with self._daemon(station_key="switch-output") as (native, _tmp):
                native.configure_icecast_output(
                    output_id="mp3", codec="mp3", enabled=True,
                    host="127.0.0.1", port=mp3_mock.port, mount="/switch.mp3",
                    username="source", password="secret", bitrate_kbps=128,
                    stream_name="Switch MP3",
                )
                native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/switch.aac",
                    username="source", password="secret", bitrate_kbps=64,
                    stream_name="Switch AAC+",
                )
                native.start()
                deadline = time.monotonic() + 8.0
                state = native.get_icecast_output_state()
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if state.get("connected_output_count") == 2 and len(mp3_mock.data) > 2000 and len(aac_mock.data) > 1000:
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("connected_output_count"), 2, state)
                first_encoder_generation = int(state.get("encoder_generation") or 0)
                first_pipeline_restarts = int(state.get("pipeline_restart_count") or 0)
                first_mp3_connects = next(
                    item for item in state["outputs"] if item["output_id"] == "mp3"
                )["connect_count"]
                self.assertEqual(int(state.get("encoder_pid") or 0), 0)
                self.assertGreater(first_encoder_generation, 0)

                mp3_bytes_before_remove = len(mp3_mock.data)
                removed = native.clear_icecast_output(output_id="aacplus")
                self.assertEqual(removed.get("output_count"), 1, removed)
                self.assertEqual([item["output_id"] for item in removed.get("outputs", [])], ["mp3"])
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    outputs = state.get("outputs") or []
                    if (
                        state.get("enabled_output_count") == 1
                        and state.get("connected_output_count") == 1
                        and len(outputs) == 1
                        and outputs[0].get("output_id") == "mp3"
                        and outputs[0].get("connected")
                        and bool(state.get("encoder_running"))
                        and len(mp3_mock.data) > mp3_bytes_before_remove
                    ):
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("enabled_output_count"), 1, state)
                self.assertEqual(state.get("connected_output_count"), 1, state)
                self.assertEqual([item["output_id"] for item in state.get("outputs", [])], ["mp3"])
                self.assertGreater(len(mp3_mock.data), mp3_bytes_before_remove)
                second_encoder_generation = int(state.get("encoder_generation") or 0)
                self.assertEqual(int(state.get("encoder_pid") or 0), 0)
                self.assertEqual(second_encoder_generation, first_encoder_generation)
                self.assertEqual(int(state.get("pipeline_restart_count") or 0), first_pipeline_restarts)
                self.assertEqual(
                    next(item for item in state["outputs"] if item["output_id"] == "mp3")["connect_count"],
                    first_mp3_connects,
                )

                aac_bytes_before_readd = len(aac_mock.data)
                native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/switch.aac",
                    username="source", password="secret", bitrate_kbps=64,
                    stream_name="Switch AAC+",
                )
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if (
                        state.get("connected_output_count") == 2
                        and bool(state.get("encoder_running"))
                        and len(aac_mock.data) > aac_bytes_before_readd
                    ):
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("enabled_output_count"), 2, state)
                self.assertEqual(state.get("connected_output_count"), 2, state)
                self.assertEqual(
                    {item["output_id"] for item in state.get("outputs", [])},
                    {"mp3", "aacplus"},
                )
                self.assertGreater(len(aac_mock.data), aac_bytes_before_readd)
                third_encoder_generation = int(state.get("encoder_generation") or 0)
                self.assertEqual(int(state.get("encoder_pid") or 0), 0)
                self.assertEqual(third_encoder_generation, second_encoder_generation)
                self.assertEqual(int(state.get("pipeline_restart_count") or 0), first_pipeline_restarts)
                self.assertEqual(
                    next(item for item in state["outputs"] if item["output_id"] == "mp3")["connect_count"],
                    first_mp3_connects,
                )
        finally:
            mp3_mock.close()
            aac_mock.close()


    def test_output_can_be_added_on_air_without_restarting_existing_output(self) -> None:
        mp3_mock = _MockIcecastSource()
        aac_mock = _MockIcecastSource()
        mp3_mock.start()
        aac_mock.start()
        try:
            with self._daemon(station_key="live-add-output") as (native, _tmp):
                native.configure_icecast_output(
                    output_id="mp3", codec="mp3", enabled=True,
                    host="127.0.0.1", port=mp3_mock.port, mount="/live-add.mp3",
                    username="source", password="secret", bitrate_kbps=128,
                    stream_name="Live MP3",
                )
                native.start()
                deadline = time.monotonic() + 8.0
                state = native.get_icecast_output_state()
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if (
                        state.get("connected_output_count") == 1
                        and bool(state.get("encoder_running"))
                        and int(state.get("encoder_generation") or 0) > 0
                        and len(mp3_mock.data) > 12000
                    ):
                        break
                    time.sleep(0.05)
                self.assertEqual(state.get("connected_output_count"), 1, state)
                self.assertTrue(state.get("encoder_running"), state)

                # Establish a stable live baseline before testing branch attachment.
                # The old 2 kB threshold sampled only ~125 ms of 128 kbps audio and
                # could observe an unrelated startup recovery on slower/full-suite hosts.
                stable_deadline = time.monotonic() + 2.0
                stable_since: float | None = None
                stable_signature: tuple[int, int, int] | None = None
                while time.monotonic() < stable_deadline:
                    state = native.get_icecast_output_state()
                    mp3_state = next(item for item in state["outputs"] if item["output_id"] == "mp3")
                    signature = (
                        int(state.get("encoder_generation") or 0),
                        int(state.get("pipeline_restart_count") or 0),
                        int(mp3_state.get("connect_count") or 0),
                    )
                    if (
                        state.get("connected_output_count") == 1
                        and bool(state.get("encoder_running"))
                        and len(mp3_mock.data) > 12000
                    ):
                        if signature != stable_signature:
                            stable_signature = signature
                            stable_since = time.monotonic()
                        elif stable_since is not None and time.monotonic() - stable_since >= 0.35:
                            break
                    else:
                        stable_signature = None
                        stable_since = None
                    time.sleep(0.05)
                self.assertIsNotNone(stable_signature, state)
                self.assertIsNotNone(stable_since, state)
                self.assertGreaterEqual(time.monotonic() - stable_since, 0.35, state)

                generation_before, restarts_before, mp3_connects_before = stable_signature
                mp3_bytes_before = len(mp3_mock.data)

                native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/live-add.aac",
                    username="source", password="secret", bitrate_kbps=64,
                    stream_name="Live AAC+",
                )
                immediate = native.get_icecast_output_state()
                self.assertEqual(int(immediate.get("encoder_generation") or 0), generation_before, immediate)
                self.assertEqual(int(immediate.get("pipeline_restart_count") or 0), restarts_before, immediate)

                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    state = native.get_icecast_output_state()
                    if (
                        state.get("connected_output_count") == 2
                        and len(aac_mock.data) > 1000
                        and len(mp3_mock.data) > mp3_bytes_before
                    ):
                        break
                    time.sleep(0.05)

                self.assertEqual(state.get("connected_output_count"), 2, state)
                self.assertEqual(int(state.get("encoder_generation") or 0), generation_before)
                self.assertEqual(int(state.get("pipeline_restart_count") or 0), restarts_before)
                mp3_state_after = next(item for item in state["outputs"] if item["output_id"] == "mp3")
                self.assertTrue(mp3_state_after["connected"])
                self.assertEqual(int(mp3_state_after.get("connect_count") or 0), mp3_connects_before)
                self.assertGreater(len(mp3_mock.data), mp3_bytes_before)
                self.assertGreater(len(aac_mock.data), 1000)
        finally:
            mp3_mock.close()
            aac_mock.close()


    def test_native_hard_handoff_switches_at_audible_audio_end_without_silence(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=300, station_key="hard-boundary-test") as (native, tmp):
                source_a = tmp / "hard-a.mp3"
                source_b = tmp / "hard-b.mp3"
                for path, frequency, duration in ((source_a, 440, 3.0), (source_b, 880, 2.0)):
                    subprocess.run([
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                        "-q:a", "3", "-y", str(path),
                    ], check=True)
                switched = threading.Event()
                payloads: list[dict] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "track_started" and event.payload.get("source") == "native_hard_handoff_boundary":
                        payloads.append(dict(event.payload))
                        switched.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/hard-boundary.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Hard boundary", public_stream=False,
                    )
                    native.start()
                    uri_a = (
                        'annotate:queue_id="20101",track_id="20201",station_key="hard-boundary-test",'
                        'wb_ab_slot_token="hard-a-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="2.500",wb_effective_end="2.500",wb_orig_total="3.000":'
                        f'{source_a}'
                    )
                    uri_b = (
                        'annotate:queue_id="20102",track_id="20202",station_key="hard-boundary-test",'
                        'wb_ab_slot_token="hard-b-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.800",wb_effective_end="1.800",wb_orig_total="2.000":'
                        f'{source_b}'
                    )
                    self.assertTrue(native.load_deck("A", uri_a, clear_slot=True))
                    self.assertTrue(native.load_deck("B", uri_b, clear_slot=True))
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready") and state.get("native_audio_deck_b_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(state.get("native_audio_deck_a_prebuffer_ready"), state)
                    self.assertTrue(state.get("native_audio_deck_b_prebuffer_ready"), state)
                    native.select_deck("A", station_key="hard-boundary-test")
                    time.sleep(0.70)
                    before_handoff = native.get_icecast_output_state()
                    underruns_before = int(before_handoff.get("output_underrun_count") or 0)
                    silence_before = int(before_handoff.get("mixed_output_silence_count") or 0)
                    armed = native.hard_handoff_to("B", station_key="hard-boundary-test")
                    self.assertTrue(armed.get("hard_handoff_armed"), armed)
                    self.assertGreaterEqual(int(armed.get("outgoing_buffered_ms") or 0), 0)
                    self.assertTrue(switched.wait(4.0), "native hard handoff did not switch")
                    time.sleep(0.15)
                    output = native.get_icecast_output_state()
                    self.assertEqual(native.get_state().get("active_deck"), "B")
                    self.assertEqual(
                        int(output.get("output_underrun_count") or 0), underruns_before, output
                    )
                    self.assertEqual(
                        int(output.get("mixed_output_silence_count") or 0), silence_before, output
                    )
                    self.assertEqual(len(payloads), 1)
                    self.assertFalse(payloads[0].get("early_fifo"), payloads[0])
                    self.assertLessEqual(abs(int(payloads[0].get("timing_error_ms") or 0)), 25)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()

    def test_native_timing_does_not_reselect_consumed_short_item_deck(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=300, station_key="retired-deck-test") as (native, tmp):
                source_a = tmp / "retired-a.mp3"
                source_b = tmp / "retired-b.mp3"
                for path, frequency, duration in ((source_a, 440, 2.0), (source_b, 880, 2.0)):
                    subprocess.run([
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                        "-q:a", "3", "-y", str(path),
                    ], check=True)
                switched = threading.Event()
                boundary_queue_ids: list[int] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "track_started" and event.payload.get("source") == "native_hard_handoff_boundary":
                        boundary_queue_ids.append(int(event.queue_id or 0))
                        if event.queue_id == 21102:
                            switched.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/retired-deck.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Retired deck", public_stream=False,
                    )
                    native.start()
                    uri_a = (
                        'annotate:queue_id="21101",track_id="21201",station_key="retired-deck-test",'
                        'wb_ab_slot_token="retired-a-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.500",wb_effective_end="1.500",wb_orig_total="2.000":'
                        f'{source_a}'
                    )
                    uri_b = (
                        'annotate:queue_id="21102",track_id="21202",station_key="retired-deck-test",'
                        'wb_ab_slot_token="retired-b-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.500",wb_effective_end="1.500",wb_orig_total="2.000",'
                        'wb_short_no_crossfade="1",wb_sam_short_no_crossfade="1":'
                        f'{source_b}'
                    )
                    self.assertTrue(native.load_deck("A", uri_a, clear_slot=True))
                    self.assertTrue(native.load_deck("B", uri_b, clear_slot=True))
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready") and state.get("native_audio_deck_b_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    native.select_deck("A", station_key="retired-deck-test")
                    self.assertTrue(switched.wait(5.0), boundary_queue_ids)
                    time.sleep(0.75)
                    self.assertEqual(boundary_queue_ids, [21102], boundary_queue_ids)
                    state = native.get_state()
                    self.assertEqual(state.get("active_deck"), "B")
                    self.assertEqual(int(state.get("queue_id") or 0), 21102)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()

    def test_short_active_track_recovers_when_next_deck_arrives_after_eof(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=300, station_key="late-next-test") as (native, tmp):
                source_a = tmp / "late-a.mp3"
                source_b = tmp / "late-b.mp3"
                for path, frequency, duration in ((source_a, 500, 1.0), (source_b, 900, 2.0)):
                    subprocess.run([
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                        "-q:a", "3", "-y", str(path),
                    ], check=True)
                eof = threading.Event()
                switched = threading.Event()
                requests: list[int] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_need_next_track" and event.queue_id == 22101:
                        requests.append(int((event.payload or {}).get("request_attempt") or 0))
                    if event.event == "native_audio_probe_eof" and event.queue_id == 22101:
                        eof.set()
                    if (
                        event.event == "track_started"
                        and event.queue_id == 22102
                        and event.payload.get("source") == "native_terminal_recovery"
                    ):
                        switched.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/late-next.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Late next", public_stream=False,
                    )
                    native.start()
                    uri_a = (
                        'annotate:queue_id="22101",track_id="22201",station_key="late-next-test",'
                        'wb_ab_slot_token="late-a-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.000",wb_effective_end="1.000",wb_orig_total="1.000",'
                        'wb_short_no_crossfade="1",wb_sam_short_no_crossfade="1":'
                        f'{source_a}'
                    )
                    uri_b = (
                        'annotate:queue_id="22102",track_id="22202",station_key="late-next-test",'
                        'wb_ab_slot_token="late-b-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.800",wb_effective_end="1.800",wb_orig_total="2.000":'
                        f'{source_b}'
                    )
                    self.assertTrue(native.load_deck("A", uri_a, clear_slot=True))
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    native.select_deck("A", station_key="late-next-test")
                    self.assertTrue(eof.wait(3.0), requests)
                    self.assertGreaterEqual(len(requests), 2, requests)
                    self.assertTrue(native.load_deck("B", uri_b, clear_slot=True))
                    self.assertTrue(switched.wait(4.0), native.get_state())
                    state = native.get_state()
                    self.assertEqual(state.get("active_deck"), "B")
                    self.assertEqual(int(state.get("queue_id") or 0), 22102)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()

    def test_native_hard_handoff_drains_early_eof_fifo_into_primed_target(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=300, station_key="hard-eof-test") as (native, tmp):
                source_a = tmp / "hard-eof-a.mp3"
                source_b = tmp / "hard-eof-b.mp3"
                for path, frequency, duration in ((source_a, 510, 2.0), (source_b, 920, 2.0)):
                    subprocess.run([
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration}",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                        "-q:a", "3", "-y", str(path),
                    ], check=True)
                switched = threading.Event()
                early_claimed = threading.Event()
                switch_payloads: list[dict] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_active_early_eof_handled" and event.payload.get("hard_handoff_claimed"):
                        early_claimed.set()
                    if event.event == "track_started" and event.payload.get("source") == "native_hard_handoff_boundary":
                        switch_payloads.append(dict(event.payload))
                        switched.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/hard-eof.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Hard EOF", public_stream=False,
                    )
                    native.start()
                    # Physical A ends at 2.0 s while its analyzed hard boundary is
                    # 2.5 s, mirroring the seeked Corona early-EOF condition.
                    uri_a = (
                        'annotate:queue_id="20301",track_id="20401",station_key="hard-eof-test",'
                        'wb_ab_slot_token="hard-eof-a-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="2.500",wb_effective_end="2.500",wb_orig_total="3.000":'
                        f'{source_a}'
                    )
                    uri_b = (
                        'annotate:queue_id="20302",track_id="20402",station_key="hard-eof-test",'
                        'wb_ab_slot_token="hard-eof-b-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.800",wb_effective_end="1.800",wb_orig_total="2.000":'
                        f'{source_b}'
                    )
                    self.assertTrue(native.load_deck("A", uri_a, clear_slot=True))
                    self.assertTrue(native.load_deck("B", uri_b, clear_slot=True))
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready") and state.get("native_audio_deck_b_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    native.select_deck("A", station_key="hard-eof-test")
                    time.sleep(0.70)
                    before_handoff = native.get_icecast_output_state()
                    underruns_before = int(before_handoff.get("output_underrun_count") or 0)
                    silence_before = int(before_handoff.get("mixed_output_silence_count") or 0)
                    armed = native.hard_handoff_to("B", station_key="hard-eof-test")
                    self.assertTrue(armed.get("hard_handoff_armed"), armed)
                    self.assertTrue(early_claimed.wait(3.0), "early EOF was not claimed by native handoff")
                    self.assertTrue(switched.wait(1.0), "early EOF did not immediately switch to primed target")
                    time.sleep(0.15)
                    output = native.get_icecast_output_state()
                    self.assertEqual(native.get_state().get("active_deck"), "B")
                    self.assertEqual(
                        int(output.get("output_underrun_count") or 0), underruns_before, output
                    )
                    self.assertEqual(
                        int(output.get("mixed_output_silence_count") or 0), silence_before, output
                    )
                    self.assertEqual(len(switch_payloads), 1)
                    self.assertTrue(switch_payloads[0].get("early_fifo"), switch_payloads[0])
                    self.assertTrue(switch_payloads[0].get("waited_for_outgoing_drain"), switch_payloads[0])
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()


    def test_native_hard_handoff_trims_inaudible_early_eof_tail(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("ffmpeg is required")
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(ring_ms=4000, prebuffer_ms=300, station_key="hard-eof-silence-test") as (native, tmp):
                source_a = tmp / "hard-eof-silence-a.mp3"
                source_b = tmp / "hard-eof-silence-b.mp3"
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=510:duration=1.0",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=1.0",
                    "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[out]",
                    "-map", "[out]", "-ar", "44100", "-ac", "2",
                    "-codec:a", "libmp3lame", "-q:a", "3", "-y", str(source_a),
                ], check=True)
                subprocess.run([
                    self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=920:duration=2.0",
                    "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame",
                    "-q:a", "3", "-y", str(source_b),
                ], check=True)
                switched = threading.Event()
                early_claimed = threading.Event()
                early_events: list[EngineEvent] = []
                switch_events: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    if event.event == "native_active_early_eof_handled" and event.payload.get("hard_handoff_claimed"):
                        early_events.append(event)
                        early_claimed.set()
                    if event.event == "track_started" and event.payload.get("source") == "native_hard_handoff_boundary":
                        switch_events.append(event)
                        switched.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=mock.port,
                        mount="/hard-eof-silence.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Hard EOF silence", public_stream=False,
                    )
                    native.start()
                    uri_a = (
                        'annotate:queue_id="20501",track_id="20601",station_key="hard-eof-silence-test",'
                        'wb_ab_slot_token="hard-eof-silence-a-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="2.500",wb_effective_end="2.500",wb_orig_total="3.000":'
                        f'{source_a}'
                    )
                    uri_b = (
                        'annotate:queue_id="20502",track_id="20602",station_key="hard-eof-silence-test",'
                        'wb_ab_slot_token="hard-eof-silence-b-token",wb_audio_start="0.000",wb_play_start="0.000",'
                        'wb_crossfade_trigger="1.800",wb_effective_end="1.800",wb_orig_total="2.000":'
                        f'{source_b}'
                    )
                    self.assertTrue(native.load_deck("A", uri_a, clear_slot=True))
                    self.assertTrue(native.load_deck("B", uri_b, clear_slot=True))
                    deadline = time.monotonic() + 4.0
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if state.get("native_audio_deck_a_prebuffer_ready") and state.get("native_audio_deck_b_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    native.select_deck("A", station_key="hard-eof-silence-test")
                    time.sleep(0.70)
                    before_handoff = native.get_icecast_output_state()
                    underruns_before = int(before_handoff.get("output_underrun_count") or 0)
                    silence_before = int(before_handoff.get("mixed_output_silence_count") or 0)
                    armed = native.hard_handoff_to("B", station_key="hard-eof-silence-test")
                    self.assertTrue(armed.get("hard_handoff_armed"), armed)
                    self.assertTrue(early_claimed.wait(3.0), "early EOF was not claimed")
                    self.assertTrue(switched.wait(0.35), "silent early-EOF tail delayed the ID handoff")
                    self.assertEqual(len(early_events), 1)
                    self.assertEqual(len(switch_events), 1)
                    early_payload = dict(early_events[0].payload)
                    self.assertGreater(int(early_payload.get("trailing_silence_trimmed_ms") or 0), 0, early_payload)
                    self.assertEqual(int(early_payload.get("outgoing_remaining_ms") or 0), 0, early_payload)
                    self.assertLessEqual(
                        int(switch_events[0].monotonic_time_ms) - int(early_events[0].monotonic_time_ms),
                        250,
                    )
                    time.sleep(0.10)
                    output = native.get_icecast_output_state()
                    self.assertEqual(native.get_state().get("active_deck"), "B")
                    self.assertEqual(int(output.get("output_underrun_count") or 0), underruns_before, output)
                    self.assertEqual(int(output.get("mixed_output_silence_count") or 0), silence_before, output)
                finally:
                    unsubscribe()
                    native.stop()
                    native.clear_icecast_output()
        finally:
            mock.close()


if __name__ == "__main__":
    unittest.main()




class NativeDspPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.config = cls.root / "bin" / "soundsolution" / "ss18.dat"

    _daemon = NativeIcecastOutputTests._daemon

    def _wait_state(self, native, predicate, timeout: float = 10.0):
        deadline = time.monotonic() + timeout
        state = native.get_icecast_output_state()
        while time.monotonic() < deadline:
            state = native.get_icecast_output_state()
            if predicate(state):
                return state
            time.sleep(0.05)
        return state

    def test_in_process_dsp_state_has_no_child_process(self) -> None:
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="direct-library-state") as (native, _tmp):
                native.configure_icecast_output(
                    enabled=True, host="127.0.0.1", port=mock.port,
                    mount="/direct-library-state.mp3", username="source", password="secret",
                    bitrate_kbps=128, stream_name="Direct library state",
                    public_stream=False, dsp_enabled=True,
                    dsp_config_path=str(self.config),
                )
                native.start()
                state = self._wait_state(
                    native,
                    lambda item: item.get("connected") and item.get("dsp_ready") and len(mock.data) > 1000,
                )
                self.assertTrue(state.get("dsp_context_active"), state)
                self.assertTrue(state.get("dsp_in_process"), state)
                self.assertEqual(state.get("dsp_backend"), "libsoundsolution.so.2")
                self.assertEqual(int(state.get("dsp_pid") or 0), 0)
                self.assertEqual(state.get("dsp_config_path"), str(self.config))
                self.assertEqual(state.get("dsp_executable_path"), "")
                self.assertEqual(state.get("dsp_log_path"), "")
                diagnostics = native.emit_diagnostics_snapshot()
                self.assertEqual(int(diagnostics.get("child_process_count") or 0), 0, diagnostics)
        finally:
            mock.close()

    def test_direct_dsp_pipeline_and_failure_recovery(self) -> None:
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="native-dsp-test") as (native, _tmp):
                native.configure_icecast_output(
                    enabled=True, host="127.0.0.1", port=mock.port,
                    mount="/native-dsp-test.mp3", username="source", password="secret",
                    bitrate_kbps=128, stream_name="Native DSP test",
                    public_stream=False, dsp_enabled=True,
                    dsp_config_path=str(self.config),
                )
                native.start()
                before = self._wait_state(
                    native,
                    lambda item: item.get("connected") and item.get("dsp_ready") and len(mock.data) > 2000,
                )
                self.assertTrue(before.get("dsp_context_active"), before)
                old_generation = int(before.get("encoder_generation") or 0)
                old_start_count = int(before.get("dsp_start_count") or 0)
                native.kill_native_dsp()
                after = self._wait_state(
                    native,
                    lambda item: (
                        item.get("connected")
                        and item.get("dsp_ready")
                        and item.get("dsp_context_active")
                        and int(item.get("dsp_start_count") or 0) > old_start_count
                        and int(item.get("encoder_generation") or 0) > old_generation
                    ),
                    timeout=15.0,
                )
                self.assertEqual(int(after.get("dsp_pid") or 0), 0, after)
                self.assertEqual(int(after.get("dsp_kill_test_count") or 0), 1, after)
                self.assertEqual(int(after.get("dsp_restart_count") or 0), 1, after)
                self.assertEqual(int(after.get("pipeline_restart_count") or 0), 1, after)
                native.stop()
                stopped = self._wait_state(native, lambda item: not item.get("dsp_context_active"), timeout=4.0)
                self.assertFalse(stopped.get("dsp_context_active"), stopped)
                self.assertTrue(native.ping().get("result", {}).get("pong"), stopped)
        finally:
            mock.close()

    def test_live_dsp_source_swap_keeps_encoder_and_icecast_connection(self) -> None:
        mock = _MockIcecastSource()
        mock.start()
        try:
            with self._daemon(station_key="live-dsp-source-swap") as (native, _tmp):
                common = {
                    "output_id": "mp3", "codec": "mp3", "enabled": True,
                    "host": "127.0.0.1", "port": mock.port,
                    "mount": "/live-dsp-source-swap.mp3", "username": "source",
                    "password": "secret", "bitrate_kbps": 128,
                    "stream_name": "Live DSP source swap", "public_stream": False,
                }
                native.configure_icecast_output(dsp_enabled=False, **common)
                native.start()
                dry = self._wait_state(native, lambda item: item.get("connected") and len(mock.data) > 2000)
                generation = int(dry.get("encoder_generation") or 0)
                restarts = int(dry.get("pipeline_restart_count") or 0)
                stream = next(item for item in dry["outputs"] if item["output_id"] == "mp3")
                connect_count = int(stream.get("connect_count") or 0)
                source_requests = len(mock.source_requests)
                dry_bytes = len(mock.data)

                native.configure_icecast_output(
                    dsp_enabled=True, dsp_config_path=str(self.config), **common,
                )
                enabled = self._wait_state(
                    native,
                    lambda item: (
                        item.get("dsp_ready") and item.get("dsp_route_active")
                        and item.get("dsp_context_active") and len(mock.data) > dry_bytes
                    ),
                )
                self.assertGreaterEqual(int(enabled.get("dsp_live_switch_count") or 0), 1, enabled)
                self.assertEqual(int(enabled.get("encoder_generation") or 0), generation, enabled)
                self.assertEqual(int(enabled.get("pipeline_restart_count") or 0), restarts, enabled)
                stream = next(item for item in enabled["outputs"] if item["output_id"] == "mp3")
                self.assertEqual(int(stream.get("connect_count") or 0), connect_count, enabled)
                self.assertEqual(len(mock.source_requests), source_requests)
                dsp_bytes = len(mock.data)

                native.configure_icecast_output(dsp_enabled=False, **common)
                disabled = self._wait_state(
                    native,
                    lambda item: (
                        not item.get("dsp_enabled") and not item.get("dsp_route_active")
                        and not item.get("dsp_context_active") and len(mock.data) > dsp_bytes
                    ),
                )
                self.assertEqual(disabled.get("dsp_status"), "bypassed", disabled)
                self.assertEqual(int(disabled.get("encoder_generation") or 0), generation, disabled)
                self.assertEqual(int(disabled.get("pipeline_restart_count") or 0), restarts, disabled)
                stream = next(item for item in disabled["outputs"] if item["output_id"] == "mp3")
                self.assertEqual(int(stream.get("connect_count") or 0), connect_count, disabled)
                self.assertEqual(len(mock.source_requests), source_requests)
        finally:
            mock.close()

    def test_live_encoder_add_remove_keeps_shared_dsp_context(self) -> None:
        mp3_mock = _MockIcecastSource()
        aac_mock = _MockIcecastSource()
        mp3_mock.start()
        aac_mock.start()
        try:
            with self._daemon(station_key="live-dsp-branches") as (native, _tmp):
                common = {
                    "username": "source", "password": "secret", "public_stream": False,
                    "dsp_enabled": True, "dsp_config_path": str(self.config),
                }
                native.configure_icecast_output(
                    output_id="mp3", codec="mp3", enabled=True,
                    host="127.0.0.1", port=mp3_mock.port, mount="/live-dsp.mp3",
                    bitrate_kbps=128, stream_name="Live DSP MP3", **common,
                )
                native.start()
                initial = self._wait_state(
                    native,
                    lambda item: item.get("dsp_ready") and item.get("connected_output_count") == 1 and len(mp3_mock.data) > 2000,
                )
                generation = int(initial.get("encoder_generation") or 0)
                restarts = int(initial.get("pipeline_restart_count") or 0)
                dsp_starts = int(initial.get("dsp_start_count") or 0)
                mp3_connects = int(next(item for item in initial["outputs"] if item["output_id"] == "mp3")["connect_count"])
                mp3_bytes = len(mp3_mock.data)

                native.configure_icecast_output(
                    output_id="aacplus", codec="aac_he_v2", enabled=True,
                    host="127.0.0.1", port=aac_mock.port, mount="/live-dsp.aac",
                    bitrate_kbps=64, stream_name="Live DSP AAC+", **common,
                )
                added = self._wait_state(
                    native,
                    lambda item: item.get("connected_output_count") == 2 and len(aac_mock.data) > 1000 and len(mp3_mock.data) > mp3_bytes,
                )
                self.assertTrue(added.get("dsp_context_active"), added)
                self.assertEqual(int(added.get("dsp_start_count") or 0), dsp_starts, added)
                self.assertEqual(int(added.get("encoder_generation") or 0), generation, added)
                self.assertEqual(int(added.get("pipeline_restart_count") or 0), restarts, added)
                self.assertEqual(int(next(item for item in added["outputs"] if item["output_id"] == "mp3")["connect_count"]), mp3_connects)

                native.clear_icecast_output(output_id="aacplus")
                removed = self._wait_state(native, lambda item: item.get("connected_output_count") == 1 and item.get("output_count") == 1)
                self.assertTrue(removed.get("dsp_context_active"), removed)
                self.assertEqual(int(removed.get("dsp_start_count") or 0), dsp_starts, removed)
                self.assertEqual(int(removed.get("encoder_generation") or 0), generation, removed)
                self.assertEqual(int(removed.get("pipeline_restart_count") or 0), restarts, removed)
                self.assertEqual(int(next(item for item in removed["outputs"] if item["output_id"] == "mp3")["connect_count"]), mp3_connects)
        finally:
            mp3_mock.close()
            aac_mock.close()


if __name__ == "__main__":
    unittest.main()
