from __future__ import annotations

import ctypes
import math
import platform
import struct
import subprocess
import unittest
from pathlib import Path


class V6022SoundSolution610Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.runtime = cls.root / "bin" / "soundsolution"
        cls.library = cls.runtime / "libsoundsolution.so.2"
        cls.config = cls.runtime / "ss18.dat"
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"

    def test_complete_source_tree_is_symlink_free(self) -> None:
        links = sorted(str(item.relative_to(self.root)) for item in self.root.rglob("*") if item.is_symlink())
        self.assertEqual(links, [])

    def test_runtime_is_fixed_inside_source_package(self) -> None:
        self.assertEqual(
            sorted(item.name for item in self.runtime.iterdir()),
            ["libsoundsolution.so.2", "ss18.dat"],
        )
        self.assertTrue(self.library.is_file())
        self.assertFalse(self.library.is_symlink())
        self.assertEqual(self.config.stat().st_size, 18418)

    def test_library_identity_and_soname(self) -> None:
        library = ctypes.CDLL(str(self.library))
        library.ssnative_version.restype = ctypes.c_char_p
        library.ssnative_arithmetic_mode.restype = ctypes.c_char_p
        machine = platform.machine().lower()
        expected = {
            "x86_64": ("6.1.0", "native-binary64-fast"),
            "amd64": ("6.1.0", "native-binary64-fast"),
            "aarch64": ("6.0.0", "native-double"),
            "arm64": ("6.0.0", "native-double"),
        }
        self.assertIn(machine, expected)
        version, arithmetic = expected[machine]
        self.assertEqual(library.ssnative_version().decode(), version)
        self.assertEqual(library.ssnative_arithmetic_mode().decode(), arithmetic)
        if machine in {"aarch64", "arm64"}:
            library.ssnative_state_layout.restype = ctypes.c_char_p
            library.ssnative_control_layout.restype = ctypes.c_char_p
            self.assertEqual(
                library.ssnative_state_layout().decode(),
                "named-state-v14/structured-process-flow",
            )
            self.assertEqual(
                library.ssnative_control_layout().decode(),
                "structured-process-v1/typed-state-loop",
            )
        dynamic = subprocess.run(
            ["readelf", "-d", str(self.library)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("Library soname: [libsoundsolution.so.2]", dynamic)

    def test_native_daemon_links_to_soname_2(self) -> None:
        dynamic = subprocess.run(
            ["readelf", "-d", str(self.binary)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("Shared library: [libsoundsolution.so.2]", dynamic)
        self.assertNotIn("Shared library: [libsoundsolution.so.0]", dynamic)
        self.assertIn("$ORIGIN/../../bin/soundsolution", dynamic)

    def test_real_pcm_processing(self) -> None:
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
            frames = 4096
            pcm = bytearray()
            for index in range(frames):
                left = int(12000 * math.sin(2.0 * math.pi * 440.0 * index / 44100.0))
                right = int(9000 * math.sin(2.0 * math.pi * 880.0 * index / 44100.0))
                pcm.extend(struct.pack("<hh", left, right))
            original = bytes(pcm)
            samples = (ctypes.c_int16 * (frames * 2)).from_buffer(pcm)
            self.assertEqual(
                library.ssnative_process_s16_interleaved(context, samples, frames, 2, 44100),
                0,
            )
            self.assertNotEqual(bytes(pcm), original)
        finally:
            library.ssnative_destroy(context)


if __name__ == "__main__":
    unittest.main()
