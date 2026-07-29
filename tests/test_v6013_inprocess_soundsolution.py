from __future__ import annotations

import ctypes
import math
import struct
import subprocess
import unittest
from pathlib import Path


class V6013InProcessSoundSolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.output_source = (cls.root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")
        cls.header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        cls.makefile = (cls.root / "native_engine" / "Makefile").read_text(encoding="utf-8")
        cls.runtime = cls.root / "bin" / "soundsolution"
        cls.library = cls.runtime / "libsoundsolution.so.2"
        cls.config = cls.runtime / "ss18.dat"
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"

    def test_versions_are_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6024"', self.app_source)
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6024"', self.header)

    def test_runtime_contains_only_required_files(self) -> None:
        entries = sorted(path.name for path in self.runtime.iterdir())
        self.assertEqual(entries, ["libsoundsolution.so.2", "ss18.dat"])
        self.assertTrue(self.library.is_file())
        self.assertFalse(self.library.is_symlink())
        self.assertTrue(self.config.is_file())

    def test_daemon_links_to_private_soundsolution_library(self) -> None:
        dynamic = subprocess.run(["readelf", "-d", str(self.binary)], check=True, capture_output=True, text=True).stdout
        self.assertIn("Shared library: [libsoundsolution.so.2]", dynamic)
        self.assertIn("$ORIGIN/../../bin/soundsolution", dynamic)
        self.assertIn("-l:libsoundsolution.so.2", self.makefile)

    def test_audio_path_calls_library_without_dsp_process(self) -> None:
        self.assertIn("ssnative_create()", self.output_source)
        self.assertIn("ssnative_load_dat(context, config_path)", self.output_source)
        self.assertIn("ssnative_process_s16_interleaved", self.output_source)
        self.assertIn("ssnative_destroy", self.output_source)
        self.assertNotIn("fork()", self.output_source)
        self.assertNotIn("execv(", self.output_source)
        self.assertNotIn("ssnative-process-raw", self.output_source)

    def test_real_library_smoke(self) -> None:
        library = ctypes.CDLL(str(self.library))
        library.ssnative_create.restype = ctypes.c_void_p
        library.ssnative_destroy.argtypes = [ctypes.c_void_p]
        library.ssnative_load_dat.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        library.ssnative_load_dat.restype = ctypes.c_int
        library.ssnative_process_s16_interleaved.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_int16),
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        library.ssnative_process_s16_interleaved.restype = ctypes.c_int

        context = library.ssnative_create()
        self.assertTrue(context)
        try:
            self.assertEqual(library.ssnative_load_dat(context, str(self.config).encode()), 0)
            frames = 44100
            pcm = bytearray()
            for index in range(frames):
                left = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / 44100.0))
                right = int(9000 * math.sin(2.0 * math.pi * 880.0 * index / 44100.0))
                pcm.extend(struct.pack("<hh", left, right))
            original = bytes(pcm)
            sample_count = frames * 2
            samples = (ctypes.c_int16 * sample_count).from_buffer(pcm)
            self.assertEqual(library.ssnative_process_s16_interleaved(context, samples, frames, 2, 44100), 0)
            self.assertNotEqual(bytes(pcm), original)
        finally:
            library.ssnative_destroy(context)

    def test_application_sends_only_dat_configuration(self) -> None:
        self.assertIn("get_bundled_soundsolution_library_path", self.app_source)
        self.assertIn('"dsp_config_path": str(config_path)', self.app_source)
        self.assertNotIn('"dsp_executable_path":', self.app_source)
        self.assertNotIn('"dsp_log_path":', self.app_source)


if __name__ == "__main__":
    unittest.main()
