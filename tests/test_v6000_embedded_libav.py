from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine


class V6000EmbeddedLibavTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.makefile = (cls.root / "native_engine" / "Makefile").read_text(encoding="utf-8")
        cls.probe_source = (cls.root / "native_engine" / "src" / "audio_probe.c").read_text(encoding="utf-8")
        cls.analysis_source = (cls.root / "native_engine" / "src" / "audio_analysis.c").read_text(encoding="utf-8")
        cls.output_source = (cls.root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")
        cls.bridge_source = (cls.root / "native_engine" / "src" / "libav_bridge.c").read_text(encoding="utf-8")

    def _connect_ready_socket(
        self,
        socket_path: Path,
        process: subprocess.Popen[str],
        *,
        timeout: float = 5.0,
    ) -> socket.socket:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self.fail(f"native daemon exited during startup with code {process.returncode}")
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(1.0)
            try:
                client.connect(str(socket_path))
                return client
            except OSError as exc:
                last_error = exc
                client.close()
                time.sleep(0.02)
        self.fail(f"native daemon did not accept connections: {last_error}")
        raise AssertionError("unreachable")

    def test_major_version_and_build_contract(self) -> None:
        app = (self.root / "app.py").read_text(encoding="utf-8")
        header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        self.assertIn("src/libav_bridge.c", self.makefile)
        for library in (
            "libavformat.so.61",
            "libavcodec.so.61",
            "libswresample.so.5",
            "libavutil.so.59",
        ):
            self.assertIn(library, self.makefile)
        self.assertIn("--disable-new-dtags", self.makefile)
        self.assertIn("$ORIGIN/../../lib", self.makefile)
        self.assertIn("$ORIGIN/../../bin/soundsolution", self.makefile)
        self.assertIn("libsoundsolution.so.2", self.makefile)

    def test_ffmpeg_cli_is_not_spawned_by_audio_paths(self) -> None:
        for source in (self.probe_source, self.analysis_source, self.bridge_source):
            self.assertNotIn("fork(", source)
            self.assertNotIn("execv(", source)
            self.assertNotIn("execl(", source)
            self.assertNotIn("execvp(", source)
            self.assertNotIn("posix_spawn", source)
        self.assertEqual(self.output_source.count("fork()"), 0)
        self.assertEqual(self.output_source.count("execv("), 0)
        self.assertIn("ssnative_process_s16_interleaved", self.output_source)
        self.assertNotIn('"ffmpeg"', self.output_source)

    def test_native_dsp_pcm_and_library_contract_is_preserved(self) -> None:
        self.assertIn("ssnative_create()", self.output_source)
        self.assertIn("ssnative_load_dat(context, config_path)", self.output_source)
        self.assertIn("ssnative_process_s16_interleaved", self.output_source)
        self.assertIn("ssnative_destroy", self.output_source)
        self.assertNotIn("fork()", self.output_source)
        self.assertNotIn("execv(", self.output_source)
        header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        self.assertIn("#define WB_AUDIO_SAMPLE_RATE 44100", header)
        self.assertIn("#define WB_AUDIO_CHANNELS 2", header)
        self.assertIn("#define WB_AUDIO_BYTES_PER_SAMPLE 2", header)

    def test_binary_pins_and_loads_bundled_libav(self) -> None:
        dynamic = subprocess.run(
            ["readelf", "-d", str(self.binary)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        self.assertIn("Library rpath: [$ORIGIN/../../lib:$ORIGIN/../../bin/soundsolution]", dynamic)
        loaded = subprocess.run(
            ["ldd", str(self.binary)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        bundled = str((self.root / "lib").resolve())
        for library in ("libavformat.so.61", "libavcodec.so.61", "libswresample.so.5", "libavutil.so.59"):
            line = next((item for item in loaded.splitlines() if library in item), "")
            target = line.split("=>", 1)[1].strip().split(" ", 1)[0] if "=>" in line else ""
            self.assertEqual(str(Path(target).resolve().parent), bundled)

    def test_running_station_has_no_ffmpeg_child_process(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "v6000.sock"
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            native = None
            try:
                with self._connect_ready_socket(socket_path, process) as raw_client:
                    with raw_client.makefile("r", encoding="utf-8") as raw_file:
                        ready = json.loads(raw_file.readline())
                payload = dict(ready.get("payload") or {})
                self.assertTrue(payload.get("embedded_libav"), payload)
                self.assertFalse(payload.get("ffmpeg_subprocesses"), payload)
                self.assertEqual(payload.get("ffmpeg_source"), "linked_libav")

                native = NativeEngine(socket_path=str(socket_path), request_timeout_sec=3.0)
                native.ping()
                native.start(station_key="v6000-process-test.db")
                time.sleep(0.2)
                task_root = Path(f"/proc/{process.pid}/task")
                children: set[str] = set()
                for children_file in task_root.glob("*/children"):
                    children.update(children_file.read_text(encoding="utf-8").strip().split())
                self.assertEqual(children, set())
                state = native.get_state(station_key="v6000-process-test.db")
                self.assertTrue(state.get("running"), state)
                self.assertTrue(state.get("embedded_libav"), state)
                self.assertFalse(state.get("ffmpeg_subprocesses"), state)
                native.stop(station_key="v6000-process-test.db")
            finally:
                if native is not None:
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
                if process.stdout is not None:
                    process.stdout.close()


if __name__ == "__main__":
    unittest.main()
