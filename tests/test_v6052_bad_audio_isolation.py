from __future__ import annotations

import ast
import math
import os
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from audio_engine import NativeEngine
from audio_engine.events import EngineEvent


class V6052BadAudioIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.app_source = cls.app_path.read_text(encoding="utf-8")
        cls.app_tree = ast.parse(cls.app_source)
        cls.app_functions = {
            node.name: node
            for node in cls.app_tree.body
            if isinstance(node, ast.FunctionDef)
        }
        cls.libav_source = (cls.root / "native_engine" / "src" / "libav_bridge.c").read_text(
            encoding="utf-8"
        )
        cls.analysis_source = (cls.root / "native_engine" / "src" / "audio_analysis.c").read_text(
            encoding="utf-8"
        )
        cls.engine_source = (cls.root / "native_engine" / "src" / "engine.c").read_text(
            encoding="utf-8"
        )
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = cls.root / "bin" / "ffmpeg"

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.app_functions[name]) or ""

    def _exec_function(self, name: str, namespace: dict[str, object]) -> None:
        module = ast.Module(body=[self.app_functions[name]], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)

    def test_decoded_frame_contract_blocks_unsafe_resampler_input(self) -> None:
        self.assertIn("WbLibavInputContract", self.libav_source)
        self.assertIn("decode_input_contract_prepare", self.libav_source)
        self.assertIn("frame->extended_data == NULL", self.libav_source)
        self.assertIn("frame->extended_data[plane] == NULL", self.libav_source)
        self.assertIn("av_channel_layout_compare", self.libav_source)
        self.assertIn("audio format changed inside one file", self.libav_source)
        write_frame = self.libav_source[
            self.libav_source.index("static int decode_write_frame(") :
            self.libav_source.index("static int decode_drain_frames(")
        ]
        self.assertLess(
            write_frame.index("decode_input_contract_prepare"),
            write_frame.index("swr_convert("),
        )

    def test_fatal_analysis_is_terminal_skip_required_and_not_prepared(self) -> None:
        self.assertIn(r'\"skip_required\":%s,\"terminal\":%s', self.analysis_source)
        self.assertIn('"native_analysis_rejected"', self.analysis_source)
        self.assertIn("result.consumed = true", self.analysis_source)
        self.assertIn("result.terminal = true", self.analysis_source)
        self.assertIn("if (!result.analysis_failed)", self.analysis_source)
        self.assertIn("wb_audio_probe_prepare_deck", self.analysis_source)
        self.assertIn("live->analysis_failed || live->terminal || live->consumed", self.analysis_source)
        self.assertIn(
            "if (track->analysis_failed || track->terminal || track->consumed) return false;",
            self.engine_source,
        )
        self.assertIn('native_deck_a_analysis_failed', self.engine_source)
        self.assertIn('native_deck_b_analysis_failed', self.engine_source)

    def test_station_scoped_recovery_removes_only_the_failed_queue_item(self) -> None:
        removed: list[tuple[list[int], str]] = []
        replans: list[str] = []
        published: list[tuple] = []

        @contextmanager
        def station_runtime_context(station_key: str):
            self.assertEqual(station_key, "Pesti Kabar")
            yield

        class Repository:
            def remove_queue_items(self, queue_ids, *, station_key=""):
                removed.append((list(queue_ids), station_key))
                return 1

        namespace: dict[str, object] = {
            "station_runtime_context": station_runtime_context,
            "_native_queue_contains_queue_id": lambda station_key, queue_id: (
                station_key == "Pesti Kabar" and queue_id == 548
            ),
            "_get_playback_repository": lambda: Repository(),
            "_publish_ui_queue_history_changed": lambda *args: published.append(args),
            "_publish_ui_event": lambda *args: published.append(args),
            "invalidate_audio_engine_status_cache": lambda: None,
            "wake_autodj_worker": lambda: None,
            "_native_station_state": lambda _station_key: {
                "running": True,
                "active_deck": "b",
                "native_audio_probe_activated": True,
            },
            "_ab_replan_after_queue_mutation": lambda *, reason: replans.append(reason),
            "_build_station_queue_plan": lambda _station_key: [],
            "autodj_fill_queue_once": lambda **_kwargs: None,
            "_ab_bootstrap_from_queue_plan": lambda *_args, **_kwargs: True,
            "_native_bad_track_debug_warning": lambda *_args, **_kwargs: None,
            "_NATIVE_BAD_TRACK_SKIP_LOCK": threading.RLock(),
            "_NATIVE_BAD_TRACK_SKIP_PENDING": {"signature"},
            "_NATIVE_BAD_TRACK_SKIP_DONE": {},
            "_NATIVE_BAD_TRACK_SKIP_TTL_SECONDS": 3600.0,
            "time": time,
        }
        self._exec_function("_process_native_bad_track_skip", namespace)
        event = SimpleNamespace(
            station_key="Pesti Kabar",
            queue_id=548,
            track_id=426,
            deck="A",
            slot_token="bad-548",
            path="/music/426-02.mp3",
            payload={"skip_required": True},
        )
        namespace["_process_native_bad_track_skip"](event, "signature")

        self.assertEqual(removed, [([548], "Pesti Kabar")])
        self.assertEqual(replans, ["native_bad_track_skipped"])
        self.assertTrue(any("native_bad_track_skipped" in args for args in published))
        self.assertNotIn("signature", namespace["_NATIVE_BAD_TRACK_SKIP_PENDING"])
        self.assertIn("signature", namespace["_NATIVE_BAD_TRACK_SKIP_DONE"])

    def test_bad_track_diagnostics_use_existing_debug_gate(self) -> None:
        helper = self._function_source("_native_bad_track_debug_warning")
        self.assertIn("if not _RUNTIME_LOGGING_ENABLED", helper)
        self.assertIn("logger.warning", helper)
        worker = self._function_source("_process_native_bad_track_skip")
        self.assertIn("_native_bad_track_debug_warning", worker)
        self.assertNotIn("print(", worker)

    def test_startup_retries_with_next_queue_row_after_failed_analysis(self) -> None:
        bootstrap = self._function_source("_ab_bootstrap_from_queue_plan")
        self.assertIn("native_deck_a_analysis_failed", bootstrap)
        self.assertIn("remove_queue_items", bootstrap)
        self.assertIn("_build_station_queue_plan", bootstrap)
        self.assertIn("_bad_track_retry_depth < 32", bootstrap)
        self.assertIn("_bad_track_retry_depth=_bad_track_retry_depth + 1", bootstrap)

    @staticmethod
    def _write_wave(path: Path, *, channels: int, frequency: float, duration_seconds: float) -> None:
        sample_rate = 44100
        total_frames = int(round(sample_rate * duration_seconds))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            block = bytearray()
            for index in range(total_frames):
                value = int(10000 * math.sin(2.0 * math.pi * frequency * index / sample_rate))
                block.extend(struct.pack("<h", value) * channels)
                if len(block) >= 65536:
                    output.writeframesraw(block)
                    block.clear()
            if block:
                output.writeframesraw(block)

    def test_channel_layout_change_isolated_to_bad_station_and_daemon_survives(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is unavailable")
        if not self.ffmpeg.exists() or not os.access(self.ffmpeg, os.X_OK):
            self.skipTest("bundled ffmpeg is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mono_wave = root / "mono.wav"
            stereo_wave = root / "stereo.wav"
            mono_mp3 = root / "mono.mp3"
            stereo_mp3 = root / "stereo.mp3"
            channel_change_mp3 = root / "channel-change.mp3"
            socket_path = root / "engine.sock"
            self._write_wave(mono_wave, channels=1, frequency=440.0, duration_seconds=2.0)
            self._write_wave(stereo_wave, channels=2, frequency=660.0, duration_seconds=4.0)
            for source, destination in ((mono_wave, mono_mp3), (stereo_wave, stereo_mp3)):
                subprocess.run(
                    [
                        str(self.ffmpeg),
                        "-nostdin",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-i",
                        str(source),
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "128k",
                        "-write_xing",
                        "0",
                        "-id3v2_version",
                        "0",
                        "-y",
                        str(destination),
                    ],
                    check=True,
                )
            channel_change_mp3.write_bytes(mono_mp3.read_bytes() + stereo_mp3.read_bytes())

            environment = dict(os.environ)
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PROBE"] = "1"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "0"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_PREBUFFER_MS"] = "250"
            environment["WEB_BROADCASTER_NATIVE_AUDIO_RING_MS"] = "4000"
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 8.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"native daemon exited during startup with code {process.returncode}")
                    time.sleep(0.02)
                self.assertTrue(socket_path.exists(), "native daemon socket was not created")
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=4.0,
                    reconnect_delay_sec=0.05,
                )
                while time.monotonic() < deadline:
                    try:
                        native.ping()
                        break
                    except Exception:
                        time.sleep(0.02)
                else:
                    self.fail("native daemon did not become protocol-ready")

                bad_failed = threading.Event()
                healthy_ready = threading.Event()
                observed: list[EngineEvent] = []

                def on_event(event: EngineEvent) -> None:
                    observed.append(event)
                    if event.event == "native_audio_analysis_failed" and event.station_key == "bad-station":
                        bad_failed.set()
                    if event.event == "native_audio_analysis_ready" and event.station_key == "healthy-station":
                        healthy_ready.set()

                unsubscribe = native.subscribe_events(on_event)
                try:
                    native.start(station_key="healthy-station")
                    native.start(station_key="bad-station")

                    def uri(station: str, path: Path, queue_id: int, token: str, duration: float) -> str:
                        return (
                            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
                            f'station_key="{station}",wb_ab_slot_token="{token}",'
                            f'wb_native_analyze="1",wb_manual_timing="0",wb_orig_total="{duration}",'
                            f'wb_audio_start="0",wb_audio_end="{duration}",wb_play_start="0",'
                            f'wb_crossfade_trigger="{duration}",wb_effective_end="{duration}",'
                            f'wb_source_end="{duration}":{path}'
                        )

                    self.assertTrue(
                        native.load_deck(
                            "A",
                            uri("healthy-station", stereo_mp3, 605211, "healthy-token", 4.0),
                            clear_slot=True,
                            station_key="healthy-station",
                        )
                    )
                    self.assertTrue(healthy_ready.wait(6.0), "healthy station analysis did not finish")
                    ready_deadline = time.monotonic() + 4.0
                    while time.monotonic() < ready_deadline:
                        healthy_before = native.get_state(station_key="healthy-station")
                        if healthy_before.get("native_audio_deck_a_prebuffer_ready"):
                            break
                        time.sleep(0.02)
                    self.assertTrue(healthy_before.get("native_audio_deck_a_prebuffer_ready"), healthy_before)

                    self.assertTrue(
                        native.load_deck(
                            "A",
                            uri("bad-station", channel_change_mp3, 605212, "bad-token", 6.0),
                            clear_slot=True,
                            station_key="bad-station",
                        )
                    )
                    select_outcome: list[object] = []

                    def select_while_analysis_is_pending() -> None:
                        try:
                            select_outcome.append(
                                native.select_deck("A", station_key="bad-station", timeout_sec=3.0)
                            )
                        except Exception as exc:
                            select_outcome.append(exc)

                    select_thread = threading.Thread(target=select_while_analysis_is_pending, daemon=True)
                    select_thread.start()
                    self.assertTrue(bad_failed.wait(8.0), [event.event for event in observed])
                    select_thread.join(timeout=4.0)
                    self.assertFalse(select_thread.is_alive(), "select remained blocked after analysis rejection")
                    self.assertEqual(len(select_outcome), 1)
                    self.assertIsInstance(select_outcome[0], Exception, select_outcome)
                    self.assertIn("analysis is not ready", str(select_outcome[0]))
                    self.assertIsNone(process.poll(), "bad audio terminated the shared daemon")
                    native.ping()

                    failure = [
                        event
                        for event in observed
                        if event.event == "native_audio_analysis_failed"
                        and event.station_key == "bad-station"
                    ][-1]
                    self.assertIs(failure.payload.get("skip_required"), True, failure.payload)
                    self.assertIn("audio format changed inside one file", failure.payload.get("analysis_error", ""))

                    healthy_after = native.get_state(station_key="healthy-station")
                    bad_after = native.get_state(station_key="bad-station")
                    self.assertTrue(healthy_after.get("running"), healthy_after)
                    self.assertEqual(healthy_after.get("native_audio_deck_a_queue_id"), 605211)
                    self.assertTrue(healthy_after.get("native_audio_deck_a_prebuffer_ready"), healthy_after)
                    self.assertTrue(bad_after.get("running"), bad_after)
                    self.assertTrue(bad_after.get("native_deck_a_analysis_failed"), bad_after)
                    self.assertFalse(bad_after.get("native_audio_deck_a_prebuffer_ready"), bad_after)

                    self.assertTrue(
                        native.load_deck(
                            "A",
                            uri("bad-station", stereo_mp3, 605213, "replacement-token", 4.0),
                            clear_slot=True,
                            station_key="bad-station",
                        )
                    )
                    replacement_deadline = time.monotonic() + 8.0
                    while time.monotonic() < replacement_deadline:
                        replacement = native.get_state(station_key="bad-station")
                        if (
                            replacement.get("native_audio_deck_a_queue_id") == 605213
                            and replacement.get("native_audio_deck_a_prebuffer_ready")
                        ):
                            break
                        time.sleep(0.02)
                    self.assertEqual(replacement.get("native_audio_deck_a_queue_id"), 605213, replacement)
                    self.assertTrue(replacement.get("native_audio_deck_a_prebuffer_ready"), replacement)
                    self.assertIsNone(process.poll())
                finally:
                    unsubscribe()
                    for station in ("healthy-station", "bad-station"):
                        try:
                            native.stop(station_key=station)
                        except Exception:
                            pass
            finally:
                if native is not None:
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
