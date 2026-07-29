from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine
from tests.test_native_icecast_output import _MockIcecastSource


class NativeMultiStationDaemonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = shutil.which("ffmpeg")

    def _wait(self, predicate, timeout: float = 12.0, interval: float = 0.05):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = predicate()
            if last:
                return last
            time.sleep(interval)
        return last


    def test_control_client_capacity_rejects_untracked_extra_client(self) -> None:
        if not self.binary.exists():
            self.skipTest("native daemon is unavailable")
        with tempfile.TemporaryDirectory() as tmp_name:
            socket_path = Path(tmp_name) / "engine.sock"
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env={**os.environ, "WEB_BROADCASTER_NATIVE_AUDIO_REALTIME": "1"},
            )
            clients: list[socket.socket] = []
            extra: socket.socket | None = None
            try:
                self.assertTrue(self._wait(lambda: socket_path.exists(), timeout=5.0))
                for _index in range(32):
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(3.0)
                    client.connect(str(socket_path))
                    ready = b""
                    while b"\n" not in ready:
                        ready += client.recv(65536)
                    self.assertIn(b'"event":"engine_ready"', ready)
                    clients.append(client)
                extra = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                extra.settimeout(3.0)
                extra.connect(str(socket_path))
                response = b""
                while b"\n" not in response:
                    chunk = extra.recv(65536)
                    if not chunk:
                        break
                    response += chunk
                self.assertIn(b"native control client capacity reached", response)
            finally:
                if extra is not None:
                    extra.close()
                for client in clients:
                    client.close()
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5.0)
                if process.stdout is not None:
                    process.stdout.close()

    def test_two_station_audio_pipelines_and_dsp_recovery_are_isolated(self) -> None:
        if self.ffmpeg is None:
            self.skipTest("system ffmpeg is required to generate fixtures")
        if not self.binary.exists():
            self.skipTest("native daemon is unavailable")
        icecast_a = _MockIcecastSource()
        icecast_b = _MockIcecastSource()
        icecast_a.start()
        icecast_b.start()
        try:
            with tempfile.TemporaryDirectory() as tmp_name:
                tmp = Path(tmp_name)
                socket_path = tmp / "engine.sock"
                tone_a = tmp / "air.mp3"
                tone_b = tmp / "rock.mp3"
                dsp_config = self.root / "bin" / "soundsolution" / "ss18.dat"
                for tone, frequency in ((tone_a, 440), (tone_b, 880)):
                    subprocess.run([
                        self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=12",
                        "-ar", "44100", "-ac", "2", "-codec:a", "libmp3lame", "-q:a", "3",
                        "-y", str(tone),
                    ], check=True)
                env = os.environ.copy()
                env["WEB_BROADCASTER_NATIVE_AUDIO_REALTIME"] = "1"
                env.pop("WEB_BROADCASTER_FFMPEG", None)
                process = subprocess.Popen(
                    [str(self.binary), str(socket_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env,
                )
                native = None
                observer = None
                unsubscribe_observer = None
                try:
                    self.assertTrue(self._wait(lambda: socket_path.exists(), timeout=5.0))
                    native = NativeEngine(
                        socket_path=str(socket_path),
                        request_timeout_sec=3.0,
                        reconnect_delay_sec=0.05,
                    )
                    self.assertTrue(native.ping()["result"]["multi_station"])
                    observed_station_events: set[str] = set()
                    observed_lock = threading.Lock()
                    observer = NativeEngine(
                        socket_path=str(socket_path),
                        request_timeout_sec=3.0,
                        reconnect_delay_sec=0.05,
                    )
                    def observe_station_event(event) -> None:
                        with observed_lock:
                            observed_station_events.add(str(event.station_key or ""))

                    unsubscribe_observer = observer.subscribe_events(observe_station_event)
                    self.assertGreaterEqual(int(observer.ping()["result"]["client_count"]), 2)
                    native.create_station("AirFM.db")
                    native.create_station("RockFM.db")
                    native.configure_icecast_output(
                        station_key="AirFM.db", output_id="mp3", codec="mp3", enabled=True,
                        host="127.0.0.1", port=icecast_a.port, mount="/air.mp3",
                        username="source", password="secret", bitrate_kbps=128,
                        stream_name="AirFM", dsp_enabled=True, dsp_config_path=str(dsp_config),
                    )
                    native.configure_icecast_output(
                        station_key="RockFM.db", output_id="mp3", codec="mp3", enabled=True,
                        host="127.0.0.1", port=icecast_b.port, mount="/rock.mp3",
                        username="source", password="secret", bitrate_kbps=128,
                        stream_name="RockFM", dsp_enabled=True, dsp_config_path=str(dsp_config),
                    )
                    native.start(station_key="AirFM.db")
                    native.start(station_key="RockFM.db")

                    def activate(station: str, queue_id: int, token: str, tone: Path) -> None:
                        uri = (
                            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
                            f'station_key="{station}",wb_ab_slot_token="{token}",'
                            'wb_play_start="0.000",wb_crossfade_trigger="11.000",'
                            f'artist="Test",title="{station}":{tone}'
                        )
                        self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                        base = {
                            "station_key": station,
                            "queue_id": queue_id,
                            "slot_token": token,
                            "deck": "A",
                            "track_id": queue_id + 100,
                            "path": str(tone),
                            "event_monotonic_time_ms": int(time.monotonic() * 1000),
                            "event_wall_time_unix_ms": int(time.time() * 1000),
                        }
                        native.sync_live_event({
                            **base, "event": "deck_loaded",
                            "payload": {"play_start_ms": 0, "transition_at_ms": 11000, "source_end_ms": 12000},
                        })
                        self.assertTrue(self._wait(
                            lambda: native.get_state(station_key=station).get("native_audio_probe_prebuffer_ready"),
                            timeout=5.0,
                        ))
                        native.sync_live_event({**base, "event": "track_started", "payload": {}})

                    activate("AirFM.db", 21001, "air-token", tone_a)
                    activate("RockFM.db", 22001, "rock-token", tone_b)
                    self.assertEqual(
                        {item["station_key"] for item in observer.list_stations()["stations"]},
                        {"AirFM.db", "RockFM.db"},
                    )
                    self.assertTrue(self._wait(
                        lambda: (
                            {"AirFM.db", "RockFM.db"}.issubset(set(observed_station_events))
                        ),
                        timeout=5.0,
                    ), "second control client did not receive station-tagged events")

                    def both_ready():
                        a = native.get_icecast_output_state(station_key="AirFM.db")
                        b = native.get_icecast_output_state(station_key="RockFM.db")
                        return (a, b) if (
                            a.get("connected") and a.get("dsp_ready") and int(a.get("encoded_bytes_total") or 0) > 10000
                            and b.get("connected") and b.get("dsp_ready") and int(b.get("encoded_bytes_total") or 0) > 10000
                        ) else None

                    ready = self._wait(both_ready, timeout=15.0)
                    self.assertIsNotNone(ready)
                    a_before, b_before = ready
                    self.assertEqual(int(a_before["dsp_pid"]), 0)
                    self.assertEqual(int(b_before["dsp_pid"]), 0)
                    self.assertTrue(a_before.get("dsp_in_process"))
                    self.assertTrue(b_before.get("dsp_in_process"))
                    self.assertTrue(a_before.get("dsp_context_active"))
                    self.assertTrue(b_before.get("dsp_context_active"))
                    self.assertEqual(int(a_before["encoder_pid"]), 0)
                    self.assertEqual(int(b_before["encoder_pid"]), 0)
                    air_encoder_generation = int(a_before["encoder_generation"])
                    air_dsp_start_count = int(a_before.get("dsp_start_count") or 0)
                    rock_dsp_start_count = int(b_before.get("dsp_start_count") or 0)
                    rock_encoder_generation = int(b_before["encoder_generation"])
                    rock_bytes = int(b_before["encoded_bytes_total"])

                    native.kill_native_dsp(station_key="AirFM.db")

                    def air_recovered():
                        a = native.get_icecast_output_state(station_key="AirFM.db")
                        return a if (
                            a.get("dsp_ready")
                            and a.get("dsp_context_active")
                            and int(a.get("dsp_start_count") or 0) > air_dsp_start_count
                            and int(a.get("encoder_generation") or 0) > air_encoder_generation
                        ) else None

                    self.assertIsNotNone(self._wait(air_recovered, timeout=15.0))
                    b_after = native.get_icecast_output_state(station_key="RockFM.db")
                    self.assertEqual(int(b_after["dsp_pid"]), 0)
                    self.assertTrue(b_after.get("dsp_context_active"))
                    self.assertEqual(int(b_after.get("dsp_start_count") or 0), rock_dsp_start_count)
                    self.assertEqual(int(b_after["encoder_pid"]), 0)
                    self.assertEqual(int(b_after["encoder_generation"]), rock_encoder_generation)
                    self.assertGreater(int(b_after["encoded_bytes_total"]), rock_bytes)
                    self.assertEqual(int(b_after.get("pipeline_restart_count") or 0), 0)

                    native.stop(station_key="AirFM.db")
                    a_stopped = self._wait(
                        lambda: (lambda state: state if int(state.get("dsp_pid") or 0) == 0 and int(state.get("encoder_pid") or 0) == 0 else None)(
                            native.get_icecast_output_state(station_key="AirFM.db")
                        ),
                        timeout=5.0,
                    )
                    self.assertIsNotNone(a_stopped)
                    b_running = native.get_icecast_output_state(station_key="RockFM.db")
                    self.assertEqual(int(a_stopped.get("dsp_pid") or 0), 0)
                    self.assertEqual(int(a_stopped.get("encoder_pid") or 0), 0)
                    self.assertTrue(b_running.get("encoder_running"))
                    self.assertTrue(native.get_state(station_key="RockFM.db")["running"])
                    listed = native.list_stations()
                    self.assertEqual({item["station_key"] for item in listed["stations"]}, {"AirFM.db", "RockFM.db"})
                    native.remove_station("AirFM.db")
                    self.assertEqual(native.list_stations()["station_count"], 1)
                    self.assertTrue(native.get_state(station_key="RockFM.db")["running"])
                finally:
                    if unsubscribe_observer is not None:
                        try:
                            unsubscribe_observer()
                        except Exception:
                            pass
                    if observer is not None:
                        observer.close()
                    if native is not None:
                        try:
                            native.stop_all_stations()
                        except Exception:
                            pass
                        native.close()
                    process.terminate()
                    try:
                        process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5.0)
                    if process.stdout is not None:
                        process.stdout.close()
        finally:
            icecast_a.close()
            icecast_b.close()


if __name__ == "__main__":
    unittest.main()
