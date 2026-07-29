from __future__ import annotations

import hashlib
import json
import os
import platform
import signal
import socket
import sys
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine


class BundledFfmpegRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"
        cls.ffmpeg = cls.root / "bin" / "ffmpeg"
        cls.lib_dir = cls.root / "lib"

    def test_package_contains_ffmpeg_but_not_ffprobe(self) -> None:
        self.assertTrue(self.ffmpeg.is_file())
        self.assertTrue(os.access(self.ffmpeg, os.X_OK))
        self.assertFalse((self.ffmpeg.parent / "ffprobe").exists())
        version = subprocess.run(
            [str(self.ffmpeg), "-hide_banner", "-version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        self.assertIn("ffmpeg version 7.1.5", version)

    def test_exact_debian12_runtime_layout_and_hashes(self) -> None:
        expected_libraries = {
            "libavcodec.so.61",
            "libavfilter.so.10",
            "libavformat.so.61",
            "libavutil.so.59",
            "libswresample.so.5",
            "libmp3lame.so.0",
            "libfdk-aac.so.2",
        }
        self.assertTrue(self.ffmpeg.is_file())
        self.assertTrue(self.lib_dir.is_dir())
        self.assertEqual({item.name for item in self.lib_dir.iterdir()}, expected_libraries)
        for item in (self.ffmpeg, *self.lib_dir.iterdir()):
            self.assertTrue(item.is_file())
            self.assertFalse(item.is_symlink())

        digest = hashlib.sha256(self.ffmpeg.read_bytes()).hexdigest()
        expected_digests = {
            "x86_64": "454a072ca09a96f13ad6e1aa445e84b09e38c49358d5a12b430988624a880a3f",
            "amd64": "454a072ca09a96f13ad6e1aa445e84b09e38c49358d5a12b430988624a880a3f",
            "aarch64": "da09a63dc573c9750ee1ddb47d4c4b76fe40806b70c26d5ee33ab228b6a487d3",
            "arm64": "da09a63dc573c9750ee1ddb47d4c4b76fe40806b70c26d5ee33ab228b6a487d3",
        }
        machine = platform.machine().lower()
        self.assertIn(machine, expected_digests)
        self.assertEqual(digest, expected_digests[machine])
        dynamic = subprocess.run(
            ["readelf", "-d", str(self.ffmpeg)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        self.assertIn("Library runpath: [$ORIGIN/../lib]", dynamic)


    def test_bundled_runtime_encodes_he_aac_v2_adts(self) -> None:
        encoders = subprocess.run(
            [str(self.ffmpeg), "-hide_banner", "-encoders"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ).stdout
        self.assertIn("libfdk_aac", encoders)
        result = subprocess.run(
            [
                str(self.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "s16le",
                "-ar",
                "44100",
                "-ac",
                "2",
                "-i",
                "/dev/zero",
                "-t",
                "0.05",
                "-c:a",
                "libfdk_aac",
                "-profile:a",
                "aac_he_v2",
                "-b:a",
                "64k",
                "-f",
                "adts",
                "-y",
                "/dev/null",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10.0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_daemon_uses_bundled_runtime_without_system_fallback(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "engine.sock"
            poison_dir = Path(temporary) / "poison-loader"
            poison_dir.mkdir()
            (poison_dir / "libavutil.so.59").write_bytes(b"not-an-elf-library")
            environment = os.environ.copy()
            environment.pop("WEB_BROADCASTER_FFMPEG", None)
            environment["PATH"] = "/nonexistent"
            environment["LD_LIBRARY_PATH"] = str(poison_dir)
            environment["LD_PRELOAD"] = ""
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=environment,
            )
            native = None
            try:
                deadline = time.monotonic() + 5.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                if not socket_path.exists():
                    output = process.stdout.read() if process.stdout is not None else ""
                    self.fail(f"native daemon failed to start: {output}")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw_client:
                    raw_client.connect(str(socket_path))
                    with raw_client.makefile("r", encoding="utf-8") as raw_file:
                        ready = json.loads(raw_file.readline())
                payload = dict(ready.get("payload") or {})
                self.assertEqual(Path(payload["ffmpeg_path"]), self.lib_dir / "libavformat.so.61")
                self.assertEqual(payload["ffmpeg_source"], "linked_libav")
                self.assertEqual(payload["ffmpeg_version"], "7.1.5")
                self.assertTrue(payload["ffmpeg_runtime_valid"])
                self.assertFalse(payload["ffmpeg_system_fallback_used"])
                self.assertEqual(payload["ffmpeg_runtime_error"], "")
                self.assertEqual(payload["ffmpeg_runtime_build"], "7.1.5-for-web-broadcaster-r13")
                self.assertTrue(payload["embedded_libav"])
                self.assertFalse(payload["ffmpeg_subprocesses"])

                native = NativeEngine(socket_path=str(socket_path), request_timeout_sec=2.0)
                state = native.get_state()
                self.assertEqual(
                    Path(state["native_audio_probe_ffmpeg"]),
                    self.lib_dir / "libavformat.so.61",
                )
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


    def test_daemon_restores_inherited_sigchld_for_optional_dsp_child(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        with tempfile.TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "ignored-sigchld.sock"
            launcher = (
                "import os, signal, sys; "
                "signal.signal(signal.SIGCHLD, signal.SIG_IGN); "
                "os.execv(sys.argv[1], [sys.argv[1], sys.argv[2]])"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", launcher, str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=os.environ.copy(),
            )
            try:
                deadline = time.monotonic() + 5.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                if not socket_path.exists():
                    output = process.stdout.read() if process.stdout is not None else ""
                    self.fail(f"daemon did not recover inherited SIGCHLD=SIG_IGN: {output}")
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw_client:
                    raw_client.connect(str(socket_path))
                    with raw_client.makefile("r", encoding="utf-8") as raw_file:
                        ready = json.loads(raw_file.readline())
                payload = dict(ready.get("payload") or {})
                self.assertTrue(payload.get("ffmpeg_runtime_valid"), payload)
                self.assertEqual(payload.get("ffmpeg_version"), "7.1.5")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
                if process.stdout is not None:
                    process.stdout.close()

    def test_ffmpeg_cli_is_not_required_by_embedded_runtime(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        original = self.ffmpeg
        hidden = original.with_name("ffmpeg.runtime-test-hidden")
        original.rename(hidden)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                socket_path = Path(temporary) / "embedded-libav.sock"
                process = subprocess.Popen(
                    [str(self.binary), str(socket_path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=os.environ.copy(),
                )
                try:
                    deadline = time.monotonic() + 5.0
                    while not socket_path.exists() and time.monotonic() < deadline:
                        if process.poll() is not None:
                            break
                        time.sleep(0.01)
                    if not socket_path.exists():
                        output = process.stdout.read() if process.stdout is not None else ""
                        self.fail(f"daemon unexpectedly required ffmpeg CLI: {output}")
                    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as raw_client:
                        raw_client.connect(str(socket_path))
                        with raw_client.makefile("r", encoding="utf-8") as raw_file:
                            ready = json.loads(raw_file.readline())
                    payload = dict(ready.get("payload") or {})
                    self.assertTrue(payload.get("embedded_libav"), payload)
                    self.assertFalse(payload.get("ffmpeg_subprocesses"), payload)
                    self.assertEqual(payload.get("ffmpeg_source"), "linked_libav")
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3.0)
                    if process.stdout is not None:
                        process.stdout.close()
        finally:
            hidden.rename(original)

    def test_corrupt_linked_runtime_fails_before_daemon_start(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        library = self.lib_dir / "libavformat.so.61"
        backup = library.with_name("libavformat.so.61.runtime-test-real")
        library.rename(backup)
        library.write_bytes(b"not-an-elf-library")
        try:
            socket_path = Path(tempfile.gettempdir()) / f"wb-linked-loader-test-{os.getpid()}.sock"
            socket_path.unlink(missing_ok=True)
            process = subprocess.run(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=os.environ.copy(),
                timeout=5.0,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("libavformat.so.61", process.stdout)
            self.assertFalse(socket_path.exists())
        finally:
            library.unlink(missing_ok=True)
            backup.rename(library)


if __name__ == "__main__":
    unittest.main()
