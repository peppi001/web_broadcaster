from __future__ import annotations

import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from audio_engine import NativeEngine
from audio_engine.events import EngineEvent


class NativeAudioProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = shutil.which("ffmpeg")
        if cls.ffmpeg is None:
            raise unittest.SkipTest("ffmpeg is not available")
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.bundled_ffmpeg = cls.root / "bin" / "ffmpeg"
        if not cls.binary.exists() or not os.access(cls.binary, os.X_OK):
            raise unittest.SkipTest("native daemon binary is not available")

    @contextmanager
    def _running_native(
        self,
        *,
        realtime: bool,
        ring_ms: int = 4000,
        prebuffer_ms: int = 1000,
        ffmpeg_seek_delay_sec: float = 0.0,
    ) -> Iterator[tuple[NativeEngine, Path]]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "engine.sock"
            environment = dict(os.environ)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PROBE"] = "1"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "1" if realtime else "0"
            environment.pop("WEB_BROADCASTER_FFMPEG", None)
            if ffmpeg_seek_delay_sec > 0.0:
                environment["WEB_BROADCASTER_LIBAV_TEST_SEEK_DELAY_MS"] = str(
                    int(round(ffmpeg_seek_delay_sec * 1000.0))
                )
            environment["WEB_BROADCASTER_NATIVE_AUDIO_RING_MS"] = str(ring_ms)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PREBUFFER_MS"] = str(prebuffer_ms)
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
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
                )
                native.start(station_key="probe-test")
                yield native, root
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
                if process.stdout is not None:
                    process.stdout.close()

    def _make_mp3(self, path: Path, *, duration: float, frequency: int = 880) -> None:
        subprocess.run(
            [
                str(self.ffmpeg),
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration={duration}",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                "-q:a",
                "4",
                "-y",
                str(path),
            ],
            check=True,
        )

    @staticmethod
    def _uri(
        path: Path,
        *,
        queue_id: int,
        token: str,
        cue_in: float,
        cue_out: float,
    ) -> str:
        return (
            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
            f'station_key="probe-test",wb_ab_slot_token="{token}",'
            f'wb_play_start="{cue_in:.3f}",wb_crossfade_trigger="{cue_out:.3f}",'
            f'artist="Probe",title="Track {queue_id}":{path}'
        )

    @staticmethod
    def _sync(
        native: NativeEngine,
        event: str,
        path: Path,
        *,
        queue_id: int,
        token: str,
        deck: str,
        payload: dict | None = None,
        event_monotonic_time_ms: int = 0,
        event_wall_time_unix_ms: int = 0,
    ) -> None:
        native.sync_live_event(
            {
                "event": event,
                "station_key": "probe-test",
                "queue_id": queue_id,
                "slot_token": token,
                "deck": deck,
                "track_id": queue_id + 100,
                "path": str(path),
                "event_monotonic_time_ms": event_monotonic_time_ms,
                "event_wall_time_unix_ms": event_wall_time_unix_ms,
                "payload": dict(payload or {}),
            }
        )


    def test_timed_http_stream_prebuffers_and_plays_through_native_ffmpeg(self) -> None:
        with self._running_native(realtime=True, ring_ms=4000, prebuffer_ms=250) as (native, root):
            source = root / "radio.mp3"
            self._make_mp3(source, duration=3.0, frequency=777)

            class QuietHandler(http.server.SimpleHTTPRequestHandler):
                def log_message(self, _format: str, *args) -> None:
                    return

            handler = functools.partial(QuietHandler, directory=str(root))
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/radio.mp3"
            terminal = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event in {
                    "native_audio_probe_eof",
                    "native_audio_probe_early_eof",
                    "native_audio_probe_error",
                    "native_audio_probe_skipped",
                }:
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = (
                    'annotate:queue_id="9080",track_id="9180",station_key="probe-test",'
                    'wb_ab_slot_token="http-stream-token",wb_source_type="stream",'
                    'wb_stream_source="1",wb_stream_infinite="0",wb_stream_duration="2",'
                    'wb_native_analyze="0",wb_manual_timing="1",wb_hard_clean_transition="1",'
                    'cue_in="0",cue_out="2",wb_effective_end="2",wb_orig_total="2",'
                    f'fade_in="0",fade_out="0",title="HTTP stream":{url}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    state = native.get_state()
                    if state.get("native_audio_deck_a_prebuffer_ready"):
                        break
                    time.sleep(0.025)
                state = native.get_state()
                self.assertTrue(state.get("native_audio_deck_a_prebuffer_ready"), state)
                self.assertTrue(native.select_deck("A").get("accepted"))
                self.assertTrue(terminal.wait(7.0), "native HTTP stream did not finish")

                names = [event.event for event in observed]
                self.assertIn("native_audio_probe_prebuffer_ready", names)
                self.assertIn("native_audio_probe_started", names)
                self.assertIn("native_audio_probe_eof", names)
                self.assertNotIn("native_audio_probe_skipped", names)
                self.assertNotIn("native_audio_probe_error", names)
                state = native.get_state()
                self.assertEqual(state.get("native_audio_probe_status"), "eof")
                self.assertEqual(state.get("native_audio_probe_error"), "")
                self.assertAlmostEqual(
                    int(state.get("native_audio_probe_actual_duration_ms") or 0),
                    2000,
                    delta=80,
                )
            finally:
                unsubscribe()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

    def test_infinite_http_stream_stays_active_until_stop(self) -> None:
        with self._running_native(realtime=True, ring_ms=3000, prebuffer_ms=250) as (native, root):
            source = root / "loop.mp3"
            self._make_mp3(source, duration=1.0, frequency=640)
            payload = source.read_bytes()
            stop_stream = threading.Event()

            class LoopHandler(http.server.BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.0"

                def log_message(self, _format: str, *args) -> None:
                    return

                def do_GET(self) -> None:
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.end_headers()
                    try:
                        while not stop_stream.is_set():
                            self.wfile.write(payload)
                            self.wfile.flush()
                            time.sleep(0.02)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LoopHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/live.mp3"
            observed: list[EngineEvent] = []
            unsubscribe = native.subscribe_events(observed.append)
            try:
                uri = (
                    'annotate:queue_id="9081",track_id="9181",station_key="probe-test",'
                    'wb_ab_slot_token="http-infinite-token",wb_source_type="stream",'
                    'wb_stream_source="1",wb_stream_infinite="1",wb_stream_duration="0",'
                    'wb_native_analyze="0",wb_manual_timing="1",wb_hard_clean_transition="1",'
                    'cue_in="0",cue_out="0",wb_effective_end="0",wb_orig_total="0",'
                    f'fade_in="0",fade_out="0",title="Infinite HTTP stream":{url}'
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline:
                    if native.get_state().get("native_audio_deck_a_prebuffer_ready"):
                        break
                    time.sleep(0.025)
                self.assertTrue(native.get_state().get("native_audio_deck_a_prebuffer_ready"))
                self.assertTrue(native.select_deck("A").get("accepted"))
                time.sleep(1.2)
                state = native.get_state()
                self.assertTrue(state.get("running"), state)
                self.assertGreater(int(state.get("native_audio_probe_position_ms") or 0), 500)
                names = [event.event for event in observed]
                self.assertNotIn("native_audio_probe_eof", names)
                self.assertNotIn("native_audio_probe_error", names)
                self.assertNotIn("native_need_next_track", names)
            finally:
                unsubscribe()
                stop_stream.set()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2.0)

    def test_one_deck_probe_decodes_cue_window_and_measures_pcm_duration(self) -> None:
        with self._running_native(realtime=False) as (native, root):
            source = root / "cue-window.mp3"
            self._make_mp3(source, duration=0.45)
            playback_started = threading.Event()
            terminal = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if (
                    event.queue_id != 9001
                    or event.slot_token != "probe-token-1"
                    or event.deck != "A"
                ):
                    return
                if event.event == "native_audio_probe_started":
                    playback_started.set()
                    return
                # The deck_loaded preload decoder may finish before activation on
                # a heavily loaded build host. Only accept terminal events that
                # belong to the active playback generation.
                if playback_started.is_set() and event.event in {
                    "native_audio_probe_eof",
                    "native_audio_probe_early_eof",
                    "native_audio_probe_error",
                }:
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = self._uri(
                    source,
                    queue_id=9001,
                    token="probe-token-1",
                    cue_in=0.050,
                    cue_out=0.250,
                )
                self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                self._sync(native, "deck_loaded", source, queue_id=9001, token="probe-token-1", deck="A")
                self._sync(native, "track_started", source, queue_id=9001, token="probe-token-1", deck="A")
                self.assertTrue(
                    playback_started.wait(2.0),
                    "active native audio probe did not start",
                )
                self.assertTrue(terminal.wait(4.0), "native audio probe did not finish")

                names = [event.event for event in observed]
                self.assertIn("native_audio_probe_started", names)
                self.assertIn("native_audio_probe_eof", names)
                self.assertNotIn("native_audio_probe_error", names)
                self.assertNotIn("native_audio_probe_early_eof", names)

                state = native.get_state()
                self.assertTrue(state["native_audio_probe_enabled"])
                self.assertFalse(state["native_audio_probe_running"])
                self.assertTrue(state["native_audio_probe_eof"])
                self.assertEqual(state["native_audio_probe_queue_id"], 9001)
                self.assertEqual(state["native_audio_probe_slot_token"], "probe-token-1")
                self.assertEqual(state["native_audio_probe_cue_in_ms"], 50)
                self.assertEqual(state["native_audio_probe_cue_out_ms"], 250)
                self.assertGreater(state["native_audio_probe_decoded_samples"], 0)
                self.assertAlmostEqual(state["native_audio_probe_actual_duration_ms"], 200, delta=30)
                self.assertAlmostEqual(state["native_audio_probe_position_ms"], 250, delta=30)
                self.assertTrue(state["audio_output_enabled"])
            finally:
                unsubscribe()


    def test_same_file_after_natural_eof_uses_fresh_nonempty_decoder(self) -> None:
        with self._running_native(realtime=False, ring_ms=2000, prebuffer_ms=100) as (native, root):
            source = root / "repeat-after-eof.mp3"
            self._make_mp3(source, duration=0.45, frequency=913)
            observed: list[EngineEvent] = []
            first_terminal = threading.Event()
            second_terminal = threading.Event()

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.slot_token == "repeat-eof-1" and event.event in {
                    "native_audio_probe_eof",
                    "native_audio_probe_early_eof",
                    "native_audio_probe_error",
                }:
                    first_terminal.set()
                if event.slot_token == "repeat-eof-2" and event.event in {
                    "native_audio_probe_eof",
                    "native_audio_probe_early_eof",
                    "native_audio_probe_error",
                }:
                    second_terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                first_uri = self._uri(
                    source,
                    queue_id=9090,
                    token="repeat-eof-1",
                    cue_in=0.0,
                    cue_out=0.40,
                )
                self.assertTrue(native.load_deck("A", first_uri, clear_slot=True))
                self._sync(
                    native, "track_started", source,
                    queue_id=9090, token="repeat-eof-1", deck="A",
                )
                self.assertTrue(first_terminal.wait(4.0), "first playback did not reach EOF")

                second_uri = self._uri(
                    source,
                    queue_id=9091,
                    token="repeat-eof-2",
                    cue_in=0.0,
                    cue_out=0.40,
                )
                self.assertTrue(native.load_deck("A", second_uri, clear_slot=True))

                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    state = native.get_state()
                    if (
                        state.get("native_audio_deck_a_prebuffer_ready")
                        and int(state.get("native_audio_deck_a_ring_buffer_bytes") or 0) > 0
                    ):
                        break
                    time.sleep(0.01)
                state = native.get_state()
                self.assertTrue(state.get("native_audio_deck_a_prebuffer_ready"), state)
                self.assertGreater(
                    int(state.get("native_audio_deck_a_ring_buffer_bytes") or 0),
                    0,
                    state,
                )

                self._sync(
                    native, "track_started", source,
                    queue_id=9091, token="repeat-eof-2", deck="A",
                )
                self.assertTrue(second_terminal.wait(4.0), "second playback did not reach EOF")

                second_events = [event for event in observed if event.slot_token == "repeat-eof-2"]
                second_names = [event.event for event in second_events]
                self.assertIn("native_audio_probe_prebuffer_ready", second_names)
                self.assertIn("native_audio_probe_started", second_names)
                self.assertIn("native_audio_probe_eof", second_names)
                self.assertNotIn("native_audio_probe_error", second_names)
                for event in second_events:
                    if event.event == "native_audio_probe_prebuffer_ready":
                        self.assertGreater(int(event.payload.get("ring_buffer_bytes") or 0), 0)
                    if event.payload.get("decoder_reused"):
                        self.assertGreater(int(event.payload.get("ring_buffer_bytes") or 0), 0)
            finally:
                unsubscribe()


    def test_native_pause_freezes_probe_position_and_resume_continues(self) -> None:
        with self._running_native(realtime=True, ring_ms=5000, prebuffer_ms=500) as (native, root):
            source = root / "pause-resume.mp3"
            self._make_mp3(source, duration=5.0, frequency=660)
            uri = self._uri(
                source,
                queue_id=9063,
                token="pause-token-5064",
                cue_in=0.0,
                cue_out=4.8,
            )
            self.assertTrue(native.load_deck("A", uri, clear_slot=True))
            self._sync(native, "deck_loaded", source, queue_id=9063, token="pause-token-5064", deck="A")

            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if native.get_state().get("native_audio_probe_prebuffer_ready"):
                    break
                time.sleep(0.025)
            self.assertTrue(native.get_state().get("native_audio_probe_prebuffer_ready"))
            self._sync(native, "track_started", source, queue_id=9063, token="pause-token-5064", deck="A")

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if int(native.get_state().get("native_audio_probe_position_ms") or 0) >= 300:
                    break
                time.sleep(0.025)
            before_pause = int(native.get_state().get("native_audio_probe_position_ms") or 0)
            self.assertGreaterEqual(before_pause, 250)

            pause_result = native.set_paused(True, station_key="probe-test")
            self.assertTrue(pause_result.get("paused"), pause_result)
            paused_start = int(native.get_state().get("native_audio_probe_position_ms") or 0)
            time.sleep(0.40)
            paused_end_state = native.get_state()
            paused_end = int(paused_end_state.get("native_audio_probe_position_ms") or 0)
            self.assertTrue(paused_end_state.get("paused"), paused_end_state)
            self.assertLessEqual(abs(paused_end - paused_start), 30)

            # Soft STOP -> PLAY performs a native seek while the engine is paused.
            # That replacement clock must anchor at resume time, not be shifted by
            # the whole earlier pause interval.
            self._sync(
                native,
                "track_seeked",
                source,
                queue_id=9063,
                token="pause-token-5064",
                deck="A",
                payload={
                    "seek_position_ms": 1000,
                    "seek_from_position_ms": paused_end,
                    "cue_in_ms": 0,
                    "cue_out_ms": 4800,
                    "play_start_ms": 1000,
                    "transition_at_ms": 4800,
                    "effective_end_ms": 4900,
                    "source_end_ms": 5000,
                },
            )
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                seek_state = native.get_state()
                seek_position = int(seek_state.get("native_audio_probe_position_ms") or 0)
                if 950 <= seek_position <= 1100:
                    break
                time.sleep(0.025)
            seek_state = native.get_state()
            seek_position = int(seek_state.get("native_audio_probe_position_ms") or 0)
            self.assertTrue(seek_state.get("paused"), seek_state)
            self.assertGreaterEqual(seek_position, 950)
            self.assertLessEqual(seek_position, 1100)
            time.sleep(0.20)
            self.assertLessEqual(
                abs(int(native.get_state().get("native_audio_probe_position_ms") or 0) - seek_position),
                30,
            )

            resume_result = native.set_paused(False, station_key="probe-test")
            self.assertFalse(resume_result.get("paused"), resume_result)
            self.assertGreaterEqual(int(resume_result.get("pause_duration_ms") or 0), 450)
            time.sleep(0.35)
            resumed_state = native.get_state()
            resumed_position = int(resumed_state.get("native_audio_probe_position_ms") or 0)
            self.assertGreater(resumed_position, seek_position + 180)
            self.assertLess(resumed_position, seek_position + 800)
            self.assertFalse(resumed_state.get("paused"))
            self.assertEqual(int(resumed_state.get("audio_runtime_mismatch_count") or 0), 0)

    def test_crossfade_trigger_does_not_truncate_decoder_tail(self) -> None:
        with self._running_native(realtime=False) as (native, root):
            source = root / "full-tail.mp3"
            self._make_mp3(source, duration=2.0, frequency=730)
            terminal = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event in {
                    "native_audio_probe_eof",
                    "native_audio_probe_early_eof",
                    "native_audio_probe_error",
                }:
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = (
                    f'annotate:queue_id="9041",track_id="9141",station_key="probe-test",'
                    f'wb_ab_slot_token="tail-token",wb_audio_start="0.050",wb_play_start="0.050",'
                    f'wb_crossfade_trigger="0.500",wb_effective_end="1.700",'
                    f'wb_orig_total="2.000",fade_out="1.000",'
                    f'artist="Probe",title="Tail Track":{source}'
                )
                self.assertTrue(native.load_deck("A", uri))
                self._sync(native, "deck_loaded", source, queue_id=9041, token="tail-token", deck="A")
                self._sync(native, "track_started", source, queue_id=9041, token="tail-token", deck="A")
                self.assertTrue(terminal.wait(4.0), "full decoder tail did not finish")

                names = [event.event for event in observed]
                self.assertIn("native_audio_probe_eof", names)
                self.assertNotIn("native_audio_probe_early_eof", names)
                self.assertNotIn("native_audio_probe_error", names)

                state = native.get_state()
                self.assertEqual(state["native_audio_probe_cue_out_ms"], 500)
                self.assertEqual(state["native_audio_probe_transition_at_ms"], 500)
                self.assertEqual(state["native_audio_probe_effective_end_ms"], 1700)
                self.assertEqual(state["native_audio_probe_source_end_ms"], 2000)
                # Decoder duration follows the physical source end, not the
                # transition trigger.  The legacy bug produced about 450 ms.
                self.assertAlmostEqual(state["native_audio_probe_actual_duration_ms"], 1950, delta=60)
                self.assertAlmostEqual(state["native_audio_probe_position_ms"], 2000, delta=60)

                eof_event = next(event for event in observed if event.event == "native_audio_probe_eof")
                self.assertEqual(eof_event.payload.get("transition_at_ms"), 500)
                self.assertEqual(eof_event.payload.get("effective_end_ms"), 1700)
                self.assertEqual(eof_event.payload.get("source_end_ms"), 2000)
            finally:
                unsubscribe()

    def test_prebuffered_track_start_catches_up_to_original_event_time(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "activation-catchup.mp3"
            self._make_mp3(source, duration=4.0, frequency=700)
            ready = threading.Event()
            started = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "catchup-token":
                    ready.set()
                if event.event == "native_audio_probe_started" and event.slot_token == "catchup-token":
                    started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(
                    native.load_deck(
                        "A",
                        (
                            f'annotate:queue_id="9048",track_id="9148",station_key="probe-test",'
                            f'wb_ab_slot_token="catchup-token",wb_audio_start="0.000",wb_play_start="0.000",'
                            f'wb_crossfade_trigger="2.000",wb_effective_end="3.800",wb_orig_total="4.000",'
                            f'artist="Probe",title="Catchup":{source}'
                        ),
                    )
                )
                descriptor = {
                    "cue_in_ms": 0,
                    "cue_out_ms": 2000,
                    "audio_start_ms": 0,
                    "play_start_ms": 0,
                    "transition_at_ms": 2000,
                    "effective_end_ms": 3800,
                    "source_end_ms": 4000,
                }
                self._sync(
                    native, "deck_loaded", source, queue_id=9048, token="catchup-token", deck="A", payload=descriptor
                )
                self.assertTrue(ready.wait(2.0), "candidate did not prebuffer")
                original_event_ms = int(time.monotonic() * 1000) - 140
                callback_started = time.monotonic()
                self._sync(
                    native,
                    "track_started",
                    source,
                    queue_id=9048,
                    token="catchup-token",
                    deck="A",
                    payload=descriptor,
                    event_monotonic_time_ms=original_event_ms,
                    event_wall_time_unix_ms=int(time.time() * 1000),
                )
                self.assertTrue(started.wait(1.0), "prebuffered voice did not catch up")
                callback_delay_ms = int((time.monotonic() - callback_started) * 1000)
                event = next(
                    event for event in observed
                    if event.event == "native_audio_probe_started" and event.slot_token == "catchup-token"
                )
                self.assertLess(callback_delay_ms, 80)
                self.assertGreaterEqual(int(event.payload.get("startup_delay_ms") or 0), 120)
                self.assertGreaterEqual(int(event.payload.get("played_duration_ms") or 0), 80)
                self.assertNotIn(
                    "native_audio_runtime_mismatch",
                    [item.event for item in observed if item.slot_token == "catchup-token"],
                )
            finally:
                unsubscribe()

    def test_track_seeked_restarts_exact_active_voice_at_new_position(self) -> None:
        with self._running_native(realtime=True, ffmpeg_seek_delay_sec=0.65) as (native, root):
            source = root / "seek-source.mp3"
            self._make_mp3(source, duration=8.0, frequency=615)
            started = threading.Event()
            seek_restarting = threading.Event()
            seek_pending = threading.Event()
            seek_applied = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_started" and event.slot_token == "seek-token":
                    started.set()
                if event.event == "native_audio_probe_seek_restarting" and event.slot_token == "seek-token":
                    seek_restarting.set()
                if event.event == "native_audio_probe_seek_pending" and event.slot_token == "seek-token":
                    seek_pending.set()
                if event.event == "native_audio_probe_seek_applied" and event.slot_token == "seek-token":
                    seek_applied.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(
                    native.load_deck(
                        "A",
                        (
                            f'annotate:queue_id="9051",track_id="9151",station_key="probe-test",'
                            f'wb_ab_slot_token="seek-token",wb_audio_start="0.000",wb_play_start="0.000",'
                            f'wb_crossfade_trigger="2.000",wb_effective_end="7.500",'
                            f'wb_orig_total="8.000",artist="Probe",title="Seek Track":{source}'
                        ),
                    )
                )
                descriptor = {
                    "cue_in_ms": 0,
                    "cue_out_ms": 2000,
                    "audio_start_ms": 0,
                    "play_start_ms": 0,
                    "transition_at_ms": 2000,
                    "effective_end_ms": 7500,
                    "source_end_ms": 8000,
                }
                self._sync(
                    native, "deck_loaded", source, queue_id=9051, token="seek-token", deck="A", payload=descriptor
                )
                self._sync(
                    native, "track_started", source, queue_id=9051, token="seek-token", deck="A", payload=descriptor
                )
                self.assertTrue(started.wait(2.0), "initial native voice did not start")
                time.sleep(0.15)
                self._sync(
                    native,
                    "transition_started",
                    source,
                    queue_id=9051,
                    token="seek-token",
                    deck="A",
                    payload={"from_deck": "B", "fade_seconds": 10.0},
                    event_monotonic_time_ms=int(time.monotonic() * 1000),
                )
                output_before = native.get_icecast_output_state()
                self.assertTrue(output_before["transitioning"])
                seek_flush_before = int(output_before.get("seek_flush_count") or 0)

                self._sync(
                    native,
                    "track_seeked",
                    source,
                    queue_id=9051,
                    token="seek-token",
                    deck="A",
                    payload={
                        "seek_position_ms": 5000,
                        "seek_from_position_ms": 150,
                    },
                )
                self.assertTrue(seek_restarting.wait(2.0), "old PCM voice was not retired for seek")
                self.assertTrue(seek_pending.wait(2.0), "slow seek restart did not enter pending state")
                self.assertTrue(seek_applied.wait(2.0), "native decoder did not restart at seek target")
                time.sleep(0.12)

                state = native.get_state()
                self.assertTrue(state["native_audio_probe_running"])
                self.assertEqual(state["native_audio_probe_deck"], "A")
                self.assertEqual(state["native_audio_probe_slot_token"], "seek-token")
                self.assertEqual(state["native_audio_probe_cue_in_ms"], 5000)
                self.assertEqual(state["native_audio_probe_play_start_ms"], 5000)
                self.assertEqual(state["native_audio_probe_transition_at_ms"], 2000)
                self.assertEqual(state["native_audio_probe_effective_end_ms"], 7500)
                self.assertEqual(state["native_audio_probe_source_end_ms"], 8000)
                self.assertEqual(state["native_audio_start_timeout_ms"], 500)
                self.assertEqual(state["native_audio_seek_start_timeout_ms"], 1200)
                self.assertEqual(state["audio_runtime_mismatch_count"], 0)
                self.assertGreaterEqual(state["native_audio_probe_position_ms"], 5000)
                # The realtime playback clock remains anchored to the original
                # seek event, so the delayed decoder catches up immediately.
                self.assertLess(state["native_audio_probe_position_ms"], 7000)
                output_after = native.get_icecast_output_state()
                self.assertFalse(output_after["transitioning"])
                self.assertEqual(output_after["primary_deck"], "A")
                self.assertEqual(int(output_after.get("seek_flush_count") or 0), seek_flush_before + 1)

                restarting_event = next(
                    event for event in observed if event.event == "native_audio_probe_seek_restarting"
                )
                applied_event = next(
                    event for event in observed if event.event == "native_audio_probe_seek_applied"
                )
                pending_event = next(
                    event for event in observed if event.event == "native_audio_probe_seek_pending"
                )
                self.assertTrue(bool(pending_event.payload.get("pending")))
                self.assertEqual(int(pending_event.payload.get("timeout_ms") or 0), 1200)
                self.assertFalse(bool(restarting_event.payload.get("terminal")))
                self.assertEqual(int(restarting_event.payload.get("seek_target_position_ms") or 0), 5000)
                self.assertFalse(bool(applied_event.payload.get("terminal")))
                self.assertTrue(bool(applied_event.payload.get("seek_restart")))
                self.assertEqual(int(applied_event.payload.get("seek_target_position_ms") or 0), 5000)
                self.assertNotIn(
                    "native_audio_probe_stopped",
                    [event.event for event in observed if event.slot_token == "seek-token"],
                )
                self.assertNotIn(
                    "native_audio_runtime_mismatch",
                    [event.event for event in observed if event.slot_token == "seek-token"],
                )
            finally:
                unsubscribe()

    def test_slow_seek_stays_healthy_below_hard_timeout(self) -> None:
        with self._running_native(realtime=True, ffmpeg_seek_delay_sec=1.40) as (native, root):
            source = root / "late-seek-source.mp3"
            self._make_mp3(source, duration=8.0, frequency=735)
            started = threading.Event()
            slow = threading.Event()
            applied = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_started" and event.slot_token == "late-seek-token":
                    started.set()
                if event.event == "native_audio_probe_seek_slow" and event.slot_token == "late-seek-token":
                    slow.set()
                if event.event == "native_audio_probe_seek_applied" and event.slot_token == "late-seek-token":
                    applied.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = (
                    f'annotate:queue_id="9052",track_id="9152",station_key="probe-test",'
                    f'wb_ab_slot_token="late-seek-token",wb_audio_start="0.000",wb_play_start="0.000",'
                    f'wb_crossfade_trigger="2.000",wb_effective_end="7.500",'
                    f'wb_orig_total="8.000",artist="Probe",title="Late Seek":{source}'
                )
                descriptor = {
                    "cue_in_ms": 0,
                    "cue_out_ms": 2000,
                    "audio_start_ms": 0,
                    "play_start_ms": 0,
                    "transition_at_ms": 2000,
                    "effective_end_ms": 7500,
                    "source_end_ms": 8000,
                }
                self.assertTrue(native.load_deck("A", uri))
                self._sync(native, "deck_loaded", source, queue_id=9052, token="late-seek-token", deck="A", payload=descriptor)
                self._sync(native, "track_started", source, queue_id=9052, token="late-seek-token", deck="A", payload=descriptor)
                self.assertTrue(started.wait(2.0))
                self._sync(
                    native,
                    "track_seeked",
                    source,
                    queue_id=9052,
                    token="late-seek-token",
                    deck="A",
                    payload={"seek_position_ms": 5000, "seek_from_position_ms": 150},
                )
                self.assertTrue(slow.wait(3.0), "slow seek diagnostic was not emitted")
                self.assertTrue(applied.wait(3.0), "slow seek did not eventually apply")
                state = native.get_state()
                self.assertEqual(state["audio_runtime_mismatch_count"], 0)
                self.assertEqual(state["audio_runtime_mismatch_total_count"], 0)
                self.assertEqual(state["audio_runtime_mismatch_recovered_count"], 0)
                slow_event = next(event for event in observed if event.event == "native_audio_probe_seek_slow")
                self.assertEqual(int(slow_event.payload.get("slow_threshold_ms") or 0), 1200)
                self.assertEqual(int(slow_event.payload.get("hard_timeout_ms") or 0), 2000)
                slow_index = next(i for i, event in enumerate(observed) if event.event == "native_audio_probe_seek_slow")
                applied_index = next(i for i, event in enumerate(observed) if event.event == "native_audio_probe_seek_applied")
                self.assertLess(slow_index, applied_index)
                self.assertNotIn("native_audio_runtime_mismatch", [event.event for event in observed])
            finally:
                unsubscribe()

    def test_seek_beyond_hard_timeout_emits_and_recovers_mismatch(self) -> None:
        with self._running_native(realtime=True, ffmpeg_seek_delay_sec=2.40) as (native, root):
            source = root / "hard-timeout-seek-source.mp3"
            self._make_mp3(source, duration=8.0, frequency=745)
            started = threading.Event()
            mismatch = threading.Event()
            recovered = threading.Event()
            applied = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_started" and event.slot_token == "hard-seek-token":
                    started.set()
                if event.event == "native_audio_runtime_mismatch" and event.slot_token == "hard-seek-token":
                    mismatch.set()
                if event.event == "native_audio_runtime_mismatch_recovered" and event.slot_token == "hard-seek-token":
                    recovered.set()
                if event.event == "native_audio_probe_seek_applied" and event.slot_token == "hard-seek-token":
                    applied.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = (
                    f'annotate:queue_id="9053",track_id="9153",station_key="probe-test",'
                    f'wb_ab_slot_token="hard-seek-token",wb_audio_start="0.000",wb_play_start="0.000",'
                    f'wb_crossfade_trigger="2.000",wb_effective_end="7.500",'
                    f'wb_orig_total="8.000",artist="Probe",title="Hard Seek":{source}'
                )
                descriptor = {
                    "cue_in_ms": 0, "cue_out_ms": 2000, "audio_start_ms": 0,
                    "play_start_ms": 0, "transition_at_ms": 2000,
                    "effective_end_ms": 7500, "source_end_ms": 8000,
                }
                self.assertTrue(native.load_deck("A", uri))
                self._sync(native, "deck_loaded", source, queue_id=9053, token="hard-seek-token", deck="A", payload=descriptor)
                self._sync(native, "track_started", source, queue_id=9053, token="hard-seek-token", deck="A", payload=descriptor)
                self.assertTrue(started.wait(2.0))
                self._sync(
                    native, "track_seeked", source, queue_id=9053,
                    token="hard-seek-token", deck="A",
                    payload={"seek_position_ms": 5000, "seek_from_position_ms": 150},
                )
                self.assertTrue(mismatch.wait(4.0), "hard seek timeout mismatch was not emitted")
                self.assertTrue(applied.wait(4.0), "hard-timeout seek did not eventually apply")
                self.assertTrue(recovered.wait(4.0), "hard-timeout mismatch was not recovered")
                state = native.get_state()
                self.assertEqual(state["audio_runtime_mismatch_count"], 0)
                self.assertGreaterEqual(state["audio_runtime_mismatch_total_count"], 1)
                self.assertGreaterEqual(state["audio_runtime_mismatch_recovered_count"], 1)
                self.assertNotIn("audio_shadow_mismatch_count", state)
                self.assertNotIn("audio_shadow_mismatch_total_count", state)
                self.assertNotIn("audio_shadow_mismatch_recovered_count", state)
                mismatch_event = next(event for event in observed if event.event == "native_audio_runtime_mismatch")
                self.assertEqual(int(mismatch_event.payload.get("timeout_ms") or 0), 2000)
            finally:
                unsubscribe()


    def test_old_track_ended_does_not_stop_new_active_probe(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source_a = root / "a.mp3"
            source_b = root / "b.mp3"
            self._make_mp3(source_a, duration=1.2, frequency=440)
            self._make_mp3(source_b, duration=1.2, frequency=660)
            b_started = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if (
                    event.event == "native_audio_probe_started"
                    and event.queue_id == 9102
                    and event.slot_token == "probe-b"
                ):
                    b_started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(
                    native.load_deck(
                        "A",
                        self._uri(source_a, queue_id=9101, token="probe-a", cue_in=0.0, cue_out=1.0),
                    )
                )
                self._sync(native, "deck_loaded", source_a, queue_id=9101, token="probe-a", deck="A")
                self._sync(native, "track_started", source_a, queue_id=9101, token="probe-a", deck="A")

                self.assertTrue(
                    native.load_deck(
                        "B",
                        self._uri(source_b, queue_id=9102, token="probe-b", cue_in=0.0, cue_out=1.0),
                    )
                )
                self._sync(native, "deck_loaded", source_b, queue_id=9102, token="probe-b", deck="B")
                self._sync(native, "track_started", source_b, queue_id=9102, token="probe-b", deck="B")
                self.assertTrue(b_started.wait(2.0), "incoming B probe did not start")

                # The normal handoff ends A after B has started. This must not
                # terminate the new one-deck probe running for B.
                self._sync(native, "track_ended", source_a, queue_id=9101, token="probe-a", deck="A")
                time.sleep(0.15)
                state = native.get_state()
                self.assertTrue(state["native_audio_probe_running"])
                self.assertEqual(state["native_audio_probe_deck"], "B")
                self.assertEqual(state["native_audio_probe_queue_id"], 9102)
                self.assertEqual(state["native_audio_probe_slot_token"], "probe-b")
                self.assertGreater(state["native_audio_probe_position_ms"], 0)
            finally:
                unsubscribe()


    def test_deck_loaded_prestarts_decoder_and_ring_buffer_is_ready_before_track_start(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source_a = root / "preload-a.mp3"
            source_b = root / "preload-b.mp3"
            self._make_mp3(source_a, duration=2.0, frequency=510)
            self._make_mp3(source_b, duration=2.0, frequency=720)
            ready: dict[str, threading.Event] = {
                "A": threading.Event(),
                "B": threading.Event(),
            }
            started_b = threading.Event()
            started_at: list[float] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.deck in ready:
                    ready[event.deck].set()
                if (
                    event.event == "native_audio_probe_started"
                    and event.queue_id == 9202
                    and event.slot_token == "preload-b"
                ):
                    started_at.append(time.monotonic())
                    started_b.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(
                    native.load_deck(
                        "A",
                        self._uri(source_a, queue_id=9201, token="preload-a", cue_in=0.0, cue_out=1.8),
                    )
                )
                self.assertTrue(
                    native.load_deck(
                        "B",
                        self._uri(source_b, queue_id=9202, token="preload-b", cue_in=0.0, cue_out=1.8),
                    )
                )
                self._sync(native, "deck_loaded", source_a, queue_id=9201, token="preload-a", deck="A")
                self._sync(native, "deck_loaded", source_b, queue_id=9202, token="preload-b", deck="B")
                self.assertTrue(ready["A"].wait(3.0), "A ring buffer was not prefilled")
                self.assertTrue(ready["B"].wait(3.0), "B ring buffer was not prefilled")

                before = native.get_state()
                self.assertTrue(before["pcm_ring_buffer"])
                self.assertTrue(before["deck_preload"])
                self.assertTrue(before["native_audio_deck_a_prebuffer_ready"])
                self.assertTrue(before["native_audio_deck_b_prebuffer_ready"])
                self.assertFalse(before["native_audio_deck_a_activated"])
                self.assertFalse(before["native_audio_deck_b_activated"])
                self.assertGreater(before["native_audio_deck_a_ring_buffer_bytes"], 0)
                self.assertGreater(before["native_audio_deck_b_ring_buffer_bytes"], 0)
                self.assertGreater(before["native_audio_deck_a_decoded_duration_ms"], 0)
                self.assertEqual(before["native_audio_probe_actual_duration_ms"], 0)
                self.assertFalse(before["native_audio_probe_actual_duration_final"])

                activation_at = time.monotonic()
                self._sync(native, "track_started", source_b, queue_id=9202, token="preload-b", deck="B")
                self.assertTrue(started_b.wait(1.0), "preloaded B deck did not start")
                self.assertLess(started_at[0] - activation_at, 0.150)

                active = native.get_state()
                self.assertEqual(active["native_audio_probe_deck"], "B")
                self.assertTrue(active["native_audio_probe_running"])
                self.assertTrue(active["native_audio_probe_prebuffer_ready"])
                self.assertGreater(active["native_audio_probe_decoded_duration_ms"], 0)
                self.assertGreaterEqual(active["native_audio_probe_played_duration_ms"], 0)
                self.assertEqual(active["native_audio_probe_actual_duration_ms"], 0)
                self.assertFalse(active["native_audio_probe_actual_duration_final"])
            finally:
                unsubscribe()

    def test_stopping_settles_to_stopped_and_final_duration_is_frozen(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "stop-state.mp3"
            self._make_mp3(source, duration=2.0, frequency=330)
            started = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_started" and event.queue_id == 9301:
                    started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(
                    native.load_deck(
                        "A",
                        self._uri(source, queue_id=9301, token="stop-token", cue_in=0.0, cue_out=1.8),
                    )
                )
                self._sync(native, "deck_loaded", source, queue_id=9301, token="stop-token", deck="A")
                self._sync(native, "track_started", source, queue_id=9301, token="stop-token", deck="A")
                self.assertTrue(started.wait(2.0), "probe did not start")
                time.sleep(0.12)
                self._sync(native, "track_ended", source, queue_id=9301, token="stop-token", deck="A")

                deadline = time.monotonic() + 2.0
                state = native.get_state()
                while state["native_audio_probe_status"] == "stopping" and time.monotonic() < deadline:
                    time.sleep(0.02)
                    state = native.get_state()
                self.assertEqual(state["native_audio_probe_status"], "stopped")
                self.assertFalse(state["native_audio_probe_running"])
                self.assertTrue(state["native_audio_probe_actual_duration_final"])
                self.assertGreater(state["native_audio_probe_actual_duration_ms"], 0)
                self.assertEqual(
                    state["native_audio_probe_actual_duration_ms"],
                    state["native_audio_probe_played_duration_ms"],
                )
                frozen = state["native_audio_probe_actual_duration_ms"]
                time.sleep(0.08)
                self.assertEqual(native.get_state()["native_audio_probe_actual_duration_ms"], frozen)
            finally:
                unsubscribe()


    def test_exact_slot_token_keeps_older_candidate_buffer_when_newer_token_is_loaded(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "token-race.mp3"
            self._make_mp3(source, duration=3.0, frequency=570)
            ready = {"old-token": threading.Event(), "new-token": threading.Event()}
            started = threading.Event()
            old_terminal = threading.Event()
            started_payload: list[dict] = []
            shared_preload_payload: list[dict] = []
            event_order: list[tuple[str, str]] = []

            def on_event(event: EngineEvent) -> None:
                event_order.append((event.event, event.slot_token))
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in ready:
                    ready[event.slot_token].set()
                if event.event == "native_audio_probe_preload_started" and event.slot_token == "new-token":
                    shared_preload_payload.append(dict(event.payload))
                if event.event == "native_audio_probe_started" and event.slot_token == "old-token":
                    started_payload.append(dict(event.payload))
                    started.set()
                if event.event in {"native_audio_probe_stopped", "native_audio_probe_eof", "native_audio_probe_error"} and event.slot_token == "old-token":
                    old_terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                old_uri = self._uri(source, queue_id=9401, token="old-token", cue_in=0.0, cue_out=2.8)
                new_uri = self._uri(source, queue_id=9401, token="new-token", cue_in=0.0, cue_out=2.8)
                self.assertTrue(native.load_deck("A", old_uri))
                self._sync(native, "deck_loaded", source, queue_id=9401, token="old-token", deck="A")
                self.assertTrue(ready["old-token"].wait(3.0), "old candidate was not prebuffered")

                self.assertTrue(native.load_deck("A", new_uri))
                self._sync(native, "deck_loaded", source, queue_id=9401, token="new-token", deck="A")
                self.assertTrue(ready["new-token"].wait(3.0), "new candidate was not prebuffered")
                self.assertTrue(shared_preload_payload)
                self.assertTrue(shared_preload_payload[0].get("shared_buffer"))
                self.assertTrue(shared_preload_payload[0].get("decoder_reused"))

                activation_at = time.monotonic()
                self._sync(native, "track_started", source, queue_id=9401, token="old-token", deck="A")
                self.assertTrue(started.wait(0.5), "older exact-token candidate did not start")
                self.assertLess(time.monotonic() - activation_at, 0.5)
                self.assertTrue(started_payload)
                self.assertTrue(started_payload[0].get("prebuffered"))
                self.assertLessEqual(int(started_payload[0].get("startup_delay_ms", 9999)), 100)

                state = native.get_state()
                self.assertEqual(state["native_audio_probe_slot_token"], "old-token")
                self.assertEqual(state["native_audio_probe_queue_id"], 9401)
                self.assertTrue(state["playback_voice_separated"])
                self.assertEqual(state["candidate_slots_per_deck"], 2)

                self._sync(native, "track_ended", source, queue_id=9401, token="old-token", deck="A")
                self.assertTrue(old_terminal.wait(2.0), "old playback voice did not emit a terminal event")

                terminal_names = [
                    name for name, token in event_order
                    if token == "old-token" and name in {
                        "native_audio_probe_stopped",
                        "native_audio_probe_eof",
                        "native_audio_probe_early_eof",
                        "native_audio_probe_error",
                    }
                ]
                self.assertEqual(len(terminal_names), 1)
            finally:
                unsubscribe()

    def test_stopping_obsolete_primary_preserves_shared_alias_candidate(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "obsolete-primary-alias.mp3"
            self._make_mp3(source, duration=3.0, frequency=585)
            ready = {"old-primary": threading.Event(), "new-alias": threading.Event()}
            started = threading.Event()
            cancelled_alias = threading.Event()
            started_payload: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in ready:
                    ready[event.slot_token].set()
                if event.event == "native_audio_probe_started" and event.slot_token == "new-alias":
                    started_payload.append(dict(event.payload))
                    started.set()
                if event.event == "native_audio_candidate_cancelled" and event.slot_token == "new-alias":
                    cancelled_alias.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                for token in ("old-primary", "new-alias"):
                    self.assertTrue(
                        native.load_deck(
                            "A",
                            self._uri(source, queue_id=9410, token=token, cue_in=0.0, cue_out=2.8),
                        )
                    )
                    self._sync(native, "deck_loaded", source, queue_id=9410, token=token, deck="A")
                self.assertTrue(ready["old-primary"].wait(3.0))
                self.assertTrue(ready["new-alias"].wait(3.0))

                # Releasing the superseded primary must detach only that token.
                self._sync(native, "track_ended", source, queue_id=9410, token="old-primary", deck="A")
                state = native.get_state()
                self.assertEqual(state["native_audio_deck_a_candidate_primary_slot_token"], "new-alias")
                self.assertTrue(state["native_audio_deck_a_prebuffer_ready"])
                self.assertTrue(state["native_audio_deck_a_running"])

                activation_at = time.monotonic()
                self._sync(native, "track_started", source, queue_id=9410, token="new-alias", deck="A")
                self.assertTrue(started.wait(0.5), "surviving alias did not start")
                self.assertLess(time.monotonic() - activation_at, 0.5)
                self.assertTrue(started_payload[0].get("prebuffered"))
                self.assertFalse(cancelled_alias.is_set())
            finally:
                unsubscribe()


    def test_prebuffer_ready_never_arrives_after_same_token_started(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "no-late-ready.mp3"
            self._make_mp3(source, duration=1.5, frequency=610)
            started = threading.Event()
            terminal = threading.Event()
            sequence: list[str] = []

            def on_event(event: EngineEvent) -> None:
                if event.slot_token != "immediate-token":
                    return
                sequence.append(event.event)
                if event.event == "native_audio_probe_started":
                    started.set()
                if event.event in {"native_audio_probe_eof", "native_audio_probe_stopped", "native_audio_probe_error"}:
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                uri = self._uri(source, queue_id=9501, token="immediate-token", cue_in=0.0, cue_out=1.2)
                self.assertTrue(native.load_deck("B", uri))
                self._sync(native, "deck_loaded", source, queue_id=9501, token="immediate-token", deck="B")
                self._sync(native, "track_started", source, queue_id=9501, token="immediate-token", deck="B")
                self.assertTrue(started.wait(2.0), "immediate candidate did not start")
                self.assertTrue(terminal.wait(3.0), "immediate candidate did not terminate")
                start_index = sequence.index("native_audio_probe_started")
                self.assertNotIn("native_audio_probe_prebuffer_ready", sequence[start_index + 1 :])
            finally:
                unsubscribe()


    def test_new_preload_on_same_deck_does_not_terminate_active_playback_voice(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            active_source = root / "active-voice.mp3"
            next_source = root / "next-candidate.mp3"
            self._make_mp3(active_source, duration=2.5, frequency=430)
            self._make_mp3(next_source, duration=2.5, frequency=730)
            active_started = threading.Event()
            next_ready = threading.Event()
            active_terminal = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_started" and event.slot_token == "voice-active":
                    active_started.set()
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "voice-next":
                    next_ready.set()
                if event.event in {"native_audio_probe_stopped", "native_audio_probe_eof", "native_audio_probe_error"} and event.slot_token == "voice-active":
                    active_terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("A", self._uri(active_source, queue_id=9601, token="voice-active", cue_in=0.0, cue_out=2.3)))
                self._sync(native, "deck_loaded", active_source, queue_id=9601, token="voice-active", deck="A")
                self._sync(native, "track_started", active_source, queue_id=9601, token="voice-active", deck="A")
                self.assertTrue(active_started.wait(2.0))

                self.assertTrue(native.load_deck("A", self._uri(next_source, queue_id=9602, token="voice-next", cue_in=0.0, cue_out=2.3)))
                self._sync(native, "deck_loaded", next_source, queue_id=9602, token="voice-next", deck="A")
                self.assertTrue(next_ready.wait(3.0))
                self.assertFalse(active_terminal.is_set(), "new preload terminated the active playback voice")
                state = native.get_state()
                self.assertEqual(state["native_audio_probe_slot_token"], "voice-active")
                self.assertTrue(state["native_audio_probe_running"])

                self._sync(native, "track_ended", active_source, queue_id=9601, token="voice-active", deck="A")
                self.assertTrue(active_terminal.wait(2.0))
                self._sync(native, "track_started", next_source, queue_id=9602, token="voice-next", deck="A")
                deadline = time.monotonic() + 1.0
                alternate = native.get_state()
                while alternate["native_audio_probe_slot_token"] != "voice-next" and time.monotonic() < deadline:
                    time.sleep(0.02)
                    alternate = native.get_state()
                self.assertEqual(alternate["native_audio_probe_slot_token"], "voice-next")
                self.assertTrue(alternate["native_audio_probe_running"])
            finally:
                unsubscribe()


    def test_many_same_content_tokens_share_one_candidate_and_exact_latest_token_starts(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "many-aliases.mp3"
            self._make_mp3(source, duration=2.5, frequency=515)
            tokens = [f"alias-{index}" for index in range(1, 7)]
            ready = {token: threading.Event() for token in tokens}
            started = threading.Event()
            preload_payloads: dict[str, dict] = {}

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in ready:
                    ready[event.slot_token].set()
                if event.event == "native_audio_probe_preload_started" and event.slot_token in ready:
                    preload_payloads[event.slot_token] = dict(event.payload)
                if event.event == "native_audio_probe_started" and event.slot_token == tokens[-1]:
                    started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                for token in tokens:
                    self.assertTrue(
                        native.load_deck(
                            "B",
                            self._uri(source, queue_id=9701, token=token, cue_in=0.0, cue_out=2.3),
                        )
                    )
                    self._sync(native, "deck_loaded", source, queue_id=9701, token=token, deck="B")
                for token in tokens:
                    self.assertTrue(ready[token].wait(3.0), f"{token} did not share the ready candidate")
                self.assertTrue(preload_payloads[tokens[-1]].get("shared_buffer"))
                self.assertTrue(preload_payloads[tokens[-1]].get("decoder_reused"))

                candidate_state = native.get_state()
                self.assertEqual(
                    candidate_state["native_audio_deck_b_candidate_primary_slot_token"],
                    tokens[0],
                )
                self.assertGreaterEqual(
                    candidate_state["native_audio_deck_b_candidate_alias_count"], 1
                )
                self.assertIn(
                    tokens[-1],
                    candidate_state["native_audio_deck_b_candidate_alias_tokens"],
                )

                activation_at = time.monotonic()
                self._sync(native, "track_started", source, queue_id=9701, token=tokens[-1], deck="B")
                self.assertTrue(started.wait(0.5), "latest exact alias did not activate")
                self.assertLess(time.monotonic() - activation_at, 0.5)
                state = native.get_state()
                self.assertEqual(state["native_audio_probe_slot_token"], tokens[-1])
                self.assertEqual(state["native_audio_probe_queue_id"], 9701)
                self.assertEqual(
                    state["native_audio_probe_candidate_primary_slot_token"],
                    tokens[-1],
                )
            finally:
                unsubscribe()

    def test_track_started_evicts_stale_candidate_and_frees_slot_without_stopping_voice(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            stale_source = root / "stale.mp3"
            active_source = root / "active.mp3"
            next_source = root / "next.mp3"
            self._make_mp3(stale_source, duration=3.0, frequency=410)
            self._make_mp3(active_source, duration=3.0, frequency=610)
            self._make_mp3(next_source, duration=3.0, frequency=810)
            stale_ready = threading.Event()
            active_ready = threading.Event()
            active_started = threading.Event()
            stale_evicted = threading.Event()
            next_ready = threading.Event()
            active_terminal = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "stale-token":
                    stale_ready.set()
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "active-token":
                    active_ready.set()
                if event.event == "native_audio_probe_started" and event.slot_token == "active-token":
                    active_started.set()
                if event.event == "native_audio_candidate_evicted" and event.slot_token == "stale-token":
                    stale_evicted.set()
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "next-token":
                    next_ready.set()
                if event.event in {"native_audio_probe_stopped", "native_audio_probe_eof", "native_audio_probe_error"} and event.slot_token == "active-token":
                    active_terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("A", self._uri(stale_source, queue_id=9711, token="stale-token", cue_in=0.0, cue_out=2.8)))
                self._sync(native, "deck_loaded", stale_source, queue_id=9711, token="stale-token", deck="A")
                self.assertTrue(stale_ready.wait(3.0))

                self.assertTrue(native.load_deck("A", self._uri(active_source, queue_id=9712, token="active-token", cue_in=0.0, cue_out=2.8)))
                self._sync(native, "deck_loaded", active_source, queue_id=9712, token="active-token", deck="A")
                self.assertTrue(active_ready.wait(3.0))
                self._sync(native, "track_started", active_source, queue_id=9712, token="active-token", deck="A")
                self.assertTrue(active_started.wait(0.5))
                self.assertTrue(stale_evicted.wait(1.0), "stale candidate was not deterministically evicted")

                self.assertTrue(native.load_deck("A", self._uri(next_source, queue_id=9713, token="next-token", cue_in=0.0, cue_out=2.8)))
                self._sync(native, "deck_loaded", next_source, queue_id=9713, token="next-token", deck="A")
                self.assertTrue(next_ready.wait(3.0), "freed slot did not accept the next preload")
                self.assertFalse(active_terminal.is_set(), "candidate eviction stopped the active playback voice")
            finally:
                unsubscribe()

    def test_late_deck_loaded_uses_its_own_immutable_descriptor_and_timestamps(self) -> None:
        with self._running_native(realtime=False) as (native, root):
            previous = root / "previous.mp3"
            stale = root / "stale-late.mp3"
            newer = root / "newer.mp3"
            self._make_mp3(previous, duration=0.8, frequency=310)
            self._make_mp3(stale, duration=0.8, frequency=510)
            self._make_mp3(newer, duration=0.8, frequency=710)

            self.assertTrue(native.load_deck("B", self._uri(previous, queue_id=9801, token="prev", cue_in=0.05, cue_out=0.25)))
            self._sync(
                native, "deck_loaded", previous, queue_id=9801, token="prev", deck="B",
                payload={"cue_in_ms": 50, "cue_out_ms": 250},
            )
            self.assertTrue(native.load_deck("B", self._uri(stale, queue_id=9802, token="stale", cue_in=0.10, cue_out=0.40)))
            self.assertTrue(native.load_deck("B", self._uri(newer, queue_id=9803, token="newer", cue_in=0.20, cue_out=0.50)))

            self._sync(
                native, "deck_loaded", stale, queue_id=9802, token="stale", deck="B",
                payload={"cue_in_ms": 100, "cue_out_ms": 400, "fade_in_ms": 25, "fade_out_ms": 75},
                event_monotonic_time_ms=123456789,
                event_wall_time_unix_ms=1784144000123,
            )
            self._sync(
                native, "track_started", stale, queue_id=9802, token="stale", deck="B",
                event_monotonic_time_ms=123456790,
                event_wall_time_unix_ms=1784144000124,
            )
            deadline = time.monotonic() + 3.0
            state = native.get_state()
            while (
                state.get("native_audio_probe_slot_token") != "stale"
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
                state = native.get_state()
            self.assertEqual(state["native_audio_deck_b_slot_token"], "stale")
            self.assertEqual(state["native_audio_probe_slot_token"], "stale")
            self.assertEqual(state["native_audio_probe_path"], str(stale))
            self.assertEqual(state["native_audio_probe_cue_in_ms"], 100)
            self.assertEqual(state["native_audio_probe_cue_out_ms"], 400)
            self.assertEqual(state["last_live_event_monotonic_ms"], 123456790)
            self.assertEqual(state["last_live_event_wall_time_unix_ms"], 1784144000124)

    def test_exact_identity_descriptor_change_emits_audio_mismatch_and_rebuilds_candidate(self) -> None:
        with self._running_native(realtime=False) as (native, root):
            source = root / "descriptor-change.mp3"
            self._make_mp3(source, duration=0.9, frequency=620)
            mismatch = threading.Event()
            ready_with_new_descriptor = threading.Event()
            payloads: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_runtime_mismatch" and event.slot_token == "descriptor-token":
                    payloads.append(dict(event.payload))
                    mismatch.set()
                if (
                    event.event == "native_audio_probe_prebuffer_ready"
                    and event.slot_token == "descriptor-token"
                    and int(event.payload.get("cue_in_ms") or 0) == 100
                ):
                    ready_with_new_descriptor.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("A", self._uri(source, queue_id=9811, token="descriptor-token", cue_in=0.05, cue_out=0.25)))
                self._sync(
                    native, "deck_loaded", source, queue_id=9811, token="descriptor-token", deck="A",
                    payload={"cue_in_ms": 50, "cue_out_ms": 250},
                )
                time.sleep(0.15)
                self._sync(
                    native, "deck_loaded", source, queue_id=9811, token="descriptor-token", deck="A",
                    payload={"cue_in_ms": 100, "cue_out_ms": 350},
                )
                self.assertTrue(mismatch.wait(2.0), "descriptor mismatch was not reported")
                self.assertEqual(payloads[0].get("reason"), "descriptor_mismatch")
                deadline = time.monotonic() + 3.0
                state = native.get_state()
                while state.get("native_audio_probe_cue_in_ms") != 100 and time.monotonic() < deadline:
                    time.sleep(0.02)
                    state = native.get_state()
                self.assertEqual(state["native_audio_probe_cue_in_ms"], 100)
                self.assertEqual(state["native_audio_probe_cue_out_ms"], 350)
                self.assertGreaterEqual(state["audio_runtime_mismatch_count"], 1)
            finally:
                unsubscribe()

    def test_track_ended_closes_exact_voice_with_low_latency_and_reason(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            source = root / "fast-stop.mp3"
            self._make_mp3(source, duration=4.0, frequency=345)
            started = threading.Event()
            stopped = threading.Event()
            stopped_at: list[float] = []
            stopped_payload: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_started" and event.slot_token == "fast-stop-token":
                    started.set()
                if event.event == "native_audio_probe_stopped" and event.slot_token == "fast-stop-token":
                    stopped_at.append(time.monotonic())
                    stopped_payload.append(dict(event.payload))
                    stopped.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("B", self._uri(source, queue_id=9721, token="fast-stop-token", cue_in=0.0, cue_out=3.8)))
                self._sync(native, "deck_loaded", source, queue_id=9721, token="fast-stop-token", deck="B")
                self._sync(native, "track_started", source, queue_id=9721, token="fast-stop-token", deck="B")
                self.assertTrue(started.wait(2.0))
                ended_at = time.monotonic()
                self._sync(native, "track_ended", source, queue_id=9721, token="fast-stop-token", deck="B")
                self.assertTrue(stopped.wait(0.75), "exact voice did not close promptly")
                self.assertLess(stopped_at[0] - ended_at, 0.5)
                self.assertEqual(stopped_payload[0].get("reason"), "track_ended")
            finally:
                unsubscribe()

    def test_full_candidate_table_evicts_oldest_inactive_candidate(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            sources = [root / f"candidate-{index}.mp3" for index in range(3)]
            for index, source in enumerate(sources):
                self._make_mp3(source, duration=2.5, frequency=450 + index * 120)
            ready = {f"candidate-{index}": threading.Event() for index in range(3)}
            evicted = threading.Event()

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in ready:
                    ready[event.slot_token].set()
                if event.event == "native_audio_candidate_evicted" and event.slot_token == "candidate-0":
                    evicted.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                for index in range(2):
                    token = f"candidate-{index}"
                    self.assertTrue(native.load_deck("B", self._uri(sources[index], queue_id=9730 + index, token=token, cue_in=0.0, cue_out=2.3)))
                    self._sync(native, "deck_loaded", sources[index], queue_id=9730 + index, token=token, deck="B")
                    self.assertTrue(ready[token].wait(3.0))

                self.assertTrue(native.load_deck("B", self._uri(sources[2], queue_id=9732, token="candidate-2", cue_in=0.0, cue_out=2.3)))
                self._sync(native, "deck_loaded", sources[2], queue_id=9732, token="candidate-2", deck="B")
                self.assertTrue(evicted.wait(1.0), "oldest inactive candidate was not evicted")
                self.assertTrue(ready["candidate-2"].wait(3.0), "replacement candidate did not prebuffer")
                state = native.get_state()
                self.assertGreaterEqual(state["audio_candidate_evicted_count"], 1)
            finally:
                unsubscribe()

    def test_audio_runtime_mismatch_is_emitted_when_no_inactive_candidate_slot_exists(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            sources = [root / f"voice-{index}.mp3" for index in range(3)]
            for index, source in enumerate(sources):
                self._make_mp3(source, duration=3.0, frequency=500 + index * 100)
            started = {f"voice-{index}": threading.Event() for index in range(2)}
            mismatch = threading.Event()
            mismatch_payload: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_started" and event.slot_token in started:
                    started[event.slot_token].set()
                if event.event == "native_audio_runtime_mismatch" and event.slot_token == "voice-2":
                    mismatch_payload.append(dict(event.payload))
                    mismatch.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                for index in range(2):
                    token = f"voice-{index}"
                    self.assertTrue(native.load_deck("A", self._uri(sources[index], queue_id=9740 + index, token=token, cue_in=0.0, cue_out=2.8)))
                    self._sync(native, "deck_loaded", sources[index], queue_id=9740 + index, token=token, deck="A")
                    self._sync(native, "track_started", sources[index], queue_id=9740 + index, token=token, deck="A")
                    self.assertTrue(started[token].wait(2.0))

                self.assertTrue(native.load_deck("A", self._uri(sources[2], queue_id=9742, token="voice-2", cue_in=0.0, cue_out=2.8)))
                self._sync(native, "deck_loaded", sources[2], queue_id=9742, token="voice-2", deck="A")
                self._sync(native, "track_started", sources[2], queue_id=9742, token="voice-2", deck="A")
                self.assertTrue(mismatch.wait(1.0), "audio-plane mismatch was not reported")
                self.assertEqual(mismatch_payload[0].get("reason"), "no_inactive_candidate_slot")
                state = native.get_state()
                self.assertGreaterEqual(state["audio_runtime_mismatch_count"], 1)
            finally:
                unsubscribe()



    def test_v5013_stale_plus_multi_alias_sequence_starts_exact_confirmed_token(self) -> None:
        with self._running_native(realtime=True) as (native, root):
            stale_source = root / "v5013-stale.mp3"
            target_source = root / "v5013-target.mp3"
            self._make_mp3(stale_source, duration=3.0, frequency=390)
            self._make_mp3(target_source, duration=3.0, frequency=690)
            stale_ready = threading.Event()
            target_ready = {token: threading.Event() for token in ("target-4", "target-5", "target-6", "target-7")}
            started = threading.Event()
            evicted = threading.Event()
            started_payload: list[dict] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "stale-3":
                    stale_ready.set()
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token in target_ready:
                    target_ready[event.slot_token].set()
                if event.event == "native_audio_candidate_evicted" and event.slot_token == "stale-3":
                    evicted.set()
                if event.event == "native_audio_probe_started" and event.slot_token == "target-7":
                    started_payload.append(dict(event.payload))
                    started.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                self.assertTrue(native.load_deck("B", self._uri(stale_source, queue_id=9750, token="stale-3", cue_in=0.0, cue_out=2.8)))
                self._sync(native, "deck_loaded", stale_source, queue_id=9750, token="stale-3", deck="B")
                self.assertTrue(stale_ready.wait(3.0))

                for token in target_ready:
                    self.assertTrue(native.load_deck("B", self._uri(target_source, queue_id=9751, token=token, cue_in=0.0, cue_out=2.8)))
                    self._sync(native, "deck_loaded", target_source, queue_id=9751, token=token, deck="B")
                for token, event in target_ready.items():
                    self.assertTrue(event.wait(3.0), f"{token} was not attached to the shared candidate")

                activation_at = time.monotonic()
                self._sync(native, "track_started", target_source, queue_id=9751, token="target-7", deck="B")
                self.assertTrue(started.wait(0.5), "exact target-7 voice did not start")
                self.assertLess(time.monotonic() - activation_at, 0.5)
                self.assertTrue(started_payload[0].get("prebuffered"))
                self.assertLessEqual(int(started_payload[0].get("startup_delay_ms", 9999)), 100)
                self.assertTrue(evicted.wait(1.0), "stale candidate did not leave the table")

                state = native.get_state()
                self.assertEqual(state["native_audio_probe_slot_token"], "target-7")
                self.assertEqual(state["native_audio_probe_queue_id"], 9751)
                self.assertEqual(state["audio_runtime_mismatch_count"], 0)
            finally:
                unsubscribe()



    def test_v5017_early_eof_fault_is_one_shot_and_token_scoped(self) -> None:
        with self._running_native(realtime=True, ring_ms=300, prebuffer_ms=100) as (native, root):
            source = root / "fault-early-eof.mp3"
            self._make_mp3(source, duration=2.0, frequency=510)
            terminal = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_early_eof" and event.slot_token == "fault-eof-token":
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                armed = native.configure_fault(
                    "early_eof",
                    target_deck="A",
                    target_slot_token="fault-eof-token",
                    after_ms=150,
                    duration_ms=0,
                    once=True,
                )
                self.assertTrue(armed["enabled"])
                uri = self._uri(source, queue_id=9801, token="fault-eof-token", cue_in=0.0, cue_out=1.8)
                self.assertTrue(native.load_deck("A", uri))
                self._sync(native, "deck_loaded", source, queue_id=9801, token="fault-eof-token", deck="A")
                self._sync(native, "track_started", source, queue_id=9801, token="fault-eof-token", deck="A")
                self.assertTrue(terminal.wait(3.0), "injected early EOF did not terminate")
                triggers = [e for e in observed if e.event == "native_audio_fault_triggered"]
                terminals = [e for e in observed if e.event == "native_audio_probe_early_eof"]
                handled = [e for e in observed if e.event == "native_active_early_eof_handled"]
                self.assertEqual(len(triggers), 1)
                self.assertEqual(len(terminals), 1)
                self.assertEqual(len(handled), 1)
                self.assertTrue(handled[0].payload.get("handled"))
                self.assertEqual(handled[0].payload.get("reason"), "early_eof")
                self.assertTrue(terminals[0].payload.get("fault_injected"))
                self.assertEqual(terminals[0].payload.get("terminal_reason"), "early_eof")
                output_state = native.get_icecast_output_state()
                self.assertEqual(output_state["active_early_eof_count"], 1)
                self.assertEqual(output_state["deck_a_fifo_bytes"], 0)
                state = native.get_fault_state()
                self.assertFalse(state["enabled"])
                self.assertEqual(state["trigger_count"], 1)
                self.assertEqual(state["last_slot_token"], "fault-eof-token")
                self.assertFalse(state["active_armed"])
                self.assertFalse(state["active_triggered"])
                self.assertEqual(state["last_terminal_reason"], "early_eof")
                self.assertEqual(state["last_fault_terminal_reason"], "early_eof")
            finally:
                unsubscribe()

    def test_early_eof_during_crossfade_removes_only_outgoing_deck(self) -> None:
        with self._running_native(realtime=True, ring_ms=500, prebuffer_ms=100) as (native, root):
            source_a = root / "early-fade-a.mp3"
            source_b = root / "early-fade-b.mp3"
            self._make_mp3(source_a, duration=2.0, frequency=520)
            self._make_mp3(source_b, duration=2.0, frequency=720)
            ready_a = threading.Event()
            ready_b = threading.Event()
            handled = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_prebuffer_ready":
                    if event.slot_token == "early-fade-a": ready_a.set()
                    if event.slot_token == "early-fade-b": ready_b.set()
                if event.event == "native_transition_early_eof_handled":
                    handled.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                native.configure_fault(
                    "early_eof", target_deck="A", target_slot_token="early-fade-a",
                    after_ms=500, duration_ms=0, once=True,
                )
                self.assertTrue(native.load_deck("A", self._uri(
                    source_a, queue_id=9811, token="early-fade-a", cue_in=0.0, cue_out=1.8
                )))
                self.assertTrue(native.load_deck("B", self._uri(
                    source_b, queue_id=9812, token="early-fade-b", cue_in=0.0, cue_out=1.8
                )))
                self._sync(native, "deck_loaded", source_a, queue_id=9811, token="early-fade-a", deck="A")
                self._sync(native, "deck_loaded", source_b, queue_id=9812, token="early-fade-b", deck="B")
                self.assertTrue(ready_a.wait(3.0))
                self.assertTrue(ready_b.wait(3.0))
                self._sync(native, "track_started", source_a, queue_id=9811, token="early-fade-a", deck="A")
                time.sleep(0.1)
                now_ms = int(time.monotonic() * 1000)
                wall_ms = int(time.time() * 1000)
                self._sync(
                    native, "transition_started", source_b, queue_id=9812, token="early-fade-b", deck="B",
                    event_monotonic_time_ms=now_ms, event_wall_time_unix_ms=wall_ms,
                    payload={
                        "from_deck": "A", "fade_out_duration_ms": 1200,
                        "entry_ramp_ms": 20, "silence_hold_ms": 50,
                        "release_duration_ms": 1250,
                        "transition_started_wall_time_unix_ms": wall_ms,
                        "entry_started_wall_time_unix_ms": wall_ms,
                    },
                )
                self._sync(native, "track_started", source_b, queue_id=9812, token="early-fade-b", deck="B")
                self.assertTrue(handled.wait(3.0), "outgoing early EOF was not handled")
                events = [e for e in observed if e.event == "native_transition_early_eof_handled"]
                self.assertEqual(len(events), 1)
                self.assertTrue(events[0].payload["outgoing_transition"])
                self.assertFalse(events[0].payload["active_primary"])
                state = native.get_icecast_output_state()
                self.assertEqual(state["transition_early_eof_count"], 1)
                self.assertEqual(state["active_early_eof_count"], 0)
                self.assertEqual(state["primary_deck"], "B")
            finally:
                unsubscribe()

    def test_v5017_kill_decoder_fault_reports_exact_terminal_reason(self) -> None:
        with self._running_native(realtime=True, ring_ms=300, prebuffer_ms=100) as (native, root):
            source = root / "fault-kill.mp3"
            self._make_mp3(source, duration=2.0, frequency=610)
            terminal = threading.Event()
            errors: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_error" and event.slot_token == "fault-kill-token":
                    errors.append(event)
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                native.configure_fault("kill_decoder", after_ms=120, once=True)
                uri = self._uri(source, queue_id=9802, token="fault-kill-token", cue_in=0.0, cue_out=1.8)
                self.assertTrue(native.load_deck("B", uri))
                self._sync(native, "deck_loaded", source, queue_id=9802, token="fault-kill-token", deck="B")
                self._sync(native, "track_started", source, queue_id=9802, token="fault-kill-token", deck="B")
                self.assertTrue(terminal.wait(3.0), "killed decoder did not terminate")
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].payload.get("terminal_reason"), "decoder_process_exited")
                self.assertTrue(errors[0].payload.get("fault_injected"))
                self.assertGreater(int(errors[0].payload.get("decoder_signal") or 0), 0)
                state = native.get_fault_state()
                self.assertFalse(state["active_armed"])
                self.assertFalse(state["active_triggered"])
                self.assertEqual(state["last_fault_terminal_reason"], "decoder_process_exited")
            finally:
                unsubscribe()

    def test_v5017_decoder_stall_produces_controlled_ring_underrun(self) -> None:
        with self._running_native(realtime=True, ring_ms=250, prebuffer_ms=100) as (native, root):
            source = root / "fault-underrun.mp3"
            self._make_mp3(source, duration=2.0, frequency=710)
            terminal = threading.Event()
            underrun = threading.Event()
            errors: list[EngineEvent] = []
            underruns: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                if event.event == "native_audio_probe_underrun":
                    underruns.append(event)
                    underrun.set()
                if event.event == "native_audio_probe_error" and event.slot_token == "fault-underrun-token":
                    errors.append(event)
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                native.configure_fault("decoder_stall", after_ms=100, duration_ms=180, once=True)
                uri = self._uri(source, queue_id=9803, token="fault-underrun-token", cue_in=0.0, cue_out=1.8)
                self.assertTrue(native.load_deck("A", uri))
                self._sync(native, "deck_loaded", source, queue_id=9803, token="fault-underrun-token", deck="A")
                self._sync(native, "track_started", source, queue_id=9803, token="fault-underrun-token", deck="A")
                self.assertTrue(underrun.wait(3.0), "underrun diagnostic was not emitted")
                self.assertTrue(terminal.wait(3.0), "underrun voice did not terminate")
                self.assertEqual(len(underruns), 1)
                self.assertNotIn("terminal_reason", underruns[0].payload)
                self.assertFalse(underruns[0].payload.get("terminal", True))
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].payload.get("terminal_reason"), "buffer_underrun")
                state = native.get_fault_state()
                self.assertEqual(state["underrun_count"], 1)
                self.assertFalse(state["active_armed"])
                self.assertFalse(state["active_triggered"])
                self.assertEqual(state["last_fault_terminal_reason"], "buffer_underrun")
            finally:
                unsubscribe()

    def test_v5017_fault_can_be_cleared_before_activation(self) -> None:
        with self._running_native(realtime=False) as (native, _root):
            state = native.configure_fault("missing_file", after_ms=0, once=True)
            self.assertTrue(state["enabled"])
            cleared = native.clear_fault()
            self.assertFalse(cleared["enabled"])
            self.assertFalse(cleared["active_armed"])

    def test_v5017_finished_decoder_fault_is_reported_as_skipped_not_triggered(self) -> None:
        with self._running_native(realtime=True, ring_ms=4000, prebuffer_ms=100) as (native, root):
            source = root / "fault-finished.mp3"
            self._make_mp3(source, duration=0.8, frequency=810)
            ready = threading.Event()
            skipped = threading.Event()
            terminal = threading.Event()
            observed: list[EngineEvent] = []

            def on_event(event: EngineEvent) -> None:
                observed.append(event)
                if event.event == "native_audio_probe_prebuffer_ready" and event.slot_token == "fault-finished-token":
                    ready.set()
                if event.event == "native_audio_fault_skipped" and event.slot_token == "fault-finished-token":
                    skipped.set()
                if event.event == "native_audio_probe_eof" and event.slot_token == "fault-finished-token":
                    terminal.set()

            unsubscribe = native.subscribe_events(on_event)
            try:
                native.configure_fault("kill_decoder", after_ms=100, once=True)
                uri = self._uri(source, queue_id=9804, token="fault-finished-token", cue_in=0.0, cue_out=0.7)
                self.assertTrue(native.load_deck("B", uri))
                self._sync(native, "deck_loaded", source, queue_id=9804, token="fault-finished-token", deck="B")
                self.assertTrue(ready.wait(3.0), "short decoder did not finish prebuffering")
                time.sleep(0.15)
                self._sync(native, "track_started", source, queue_id=9804, token="fault-finished-token", deck="B")
                self.assertTrue(skipped.wait(3.0), "finished decoder fault was not rejected")
                self.assertTrue(terminal.wait(3.0), "short voice did not finish naturally")
                self.assertEqual(len([e for e in observed if e.event == "native_audio_fault_triggered"]), 0)
                skipped_events = [e for e in observed if e.event == "native_audio_fault_skipped"]
                self.assertEqual(len(skipped_events), 1)
                self.assertEqual(skipped_events[0].payload.get("reason"), "decoder_already_finished")
                state = native.get_fault_state()
                self.assertEqual(state["trigger_count"], 0)
                self.assertFalse(state["active_armed"])
                self.assertFalse(state["active_triggered"])
                self.assertEqual(state["last_fault_terminal_reason"], "decoder_already_finished")
            finally:
                unsubscribe()



if __name__ == "__main__":
    unittest.main()
