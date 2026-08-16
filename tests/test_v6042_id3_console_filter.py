from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6042Id3ConsoleFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.bridge_source = (
            cls.root / "native_engine" / "src" / "libav_bridge.c"
        ).read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

    @staticmethod
    def _syncsafe(size: int) -> bytes:
        return bytes([
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ])

    @staticmethod
    def _frame(name: str, payload: bytes) -> bytes:
        return name.encode("ascii") + len(payload).to_bytes(4, "big") + b"\x00\x00" + payload

    def _write_id3_file(self, path: Path, frames: list[bytes]) -> None:
        body = b"".join(frames)
        path.write_bytes(b"ID3\x03\x00\x00" + self._syncsafe(len(body)) + body)

    def test_native_filter_covers_metadata_parser_family(self) -> None:
        for marker in (
            'strcmp(format, "Cannot read BOM value, input too short\\n") == 0',
            'strcmp(format, "Incorrect BOM value\\n") == 0',
            'strcmp(format, "Error reading comment frame, skipped\\n") == 0',
            'strcmp(format, "Error reading lyrics, skipped\\n") == 0',
            'strcmp(format, "Error reading frame %s, skipped\\n") == 0',
            'return valid_id3_frame_name(frame_name);',
        ):
            self.assertIn(marker, self.bridge_source)
        self.assertNotIn('strcmp(frame_name, "TCON") == 0', self.bridge_source)

    def test_process_console_second_line_is_generic_but_id3_shaped(self) -> None:
        for marker in (
            "b'Cannot read BOM value, input too short'",
            "b'Incorrect BOM value'",
            "b'Error reading comment frame, skipped'",
            "b'Error reading lyrics, skipped'",
            "marker = b'Error reading frame '",
            "return _is_id3_frame_name_bytes(frame_name)",
        ):
            self.assertIn(marker, self.app_source)

    def test_real_observed_id3_failures_are_quiet_but_real_libav_error_remains(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")

        ffmpeg_include = self.root / "native_engine" / "ffmpeg_sdk" / "include"
        ffmpeg_lib = self.root / "lib"
        if not ffmpeg_include.is_dir() or not ffmpeg_lib.is_dir():
            self.skipTest("Bundled FFmpeg SDK/runtime is not staged in the source tree")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            malformed_comment = work / "malformed_comment.mp3"
            malformed_text = work / "malformed_text.mp3"
            self._write_id3_file(
                malformed_comment,
                [self._frame("COMM", b"\x01eng\x00\x00")],
            )
            self._write_id3_file(
                malformed_text,
                [
                    self._frame("TENC", b"\x01\x00\x00"),
                    self._frame("TCOP", b"\x01\x00\x00"),
                    self._frame("TOPE", b"\x01\x00\x00"),
                ],
            )

            source = work / "id3_real_open_smoke.c"
            binary = work / "id3_real_open_smoke"
            source.write_text(
                r'''#include "libav_bridge.h"
#include <libavformat/avformat.h>
#include <stdio.h>

int main(int argc, char **argv) {
    char error[256] = "";
    int index;

    if (argc < 2) return 9;
    if (wb_libav_runtime_init(NULL, error, sizeof(error)) != 0) {
        fprintf(stderr, "runtime init failed: %s\n", error);
        return 2;
    }

    for (index = 1; index < argc; index++) {
        AVFormatContext *context = NULL;
        int result = avformat_open_input(&context, argv[index], NULL, NULL);
        if (context != NULL) avformat_close_input(&context);
        if (result >= 0) {
            wb_libav_runtime_shutdown();
            return 3;
        }
    }

    wb_libav_runtime_shutdown();
    return 0;
}
''',
                encoding="utf-8",
            )
            command = [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-pthread",
                f"-I{self.root / 'native_engine' / 'include'}",
                f"-I{ffmpeg_include}",
                str(source),
                str(self.root / "native_engine" / "src" / "libav_bridge.c"),
                f"-L{ffmpeg_lib}",
                "-l:libavformat.so.61",
                "-l:libavcodec.so.61",
                "-l:libswresample.so.5",
                "-l:libavutil.so.59",
                f"-Wl,-rpath,{ffmpeg_lib}",
                "-lm",
                "-o",
                str(binary),
            ]
            build = subprocess.run(
                command,
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60.0,
            )
            self.assertEqual(build.returncode, 0, build.stderr)

            run = subprocess.run(
                [str(binary), str(malformed_comment), str(malformed_text)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            for noisy in (
                "Cannot read BOM value, input too short",
                "Incorrect BOM value",
                "Error reading comment frame, skipped",
                "Error reading frame TENC, skipped",
                "Error reading frame TCOP, skipped",
                "Error reading frame TOPE, skipped",
            ):
                self.assertNotIn(noisy, run.stderr)
            self.assertGreaterEqual(
                run.stderr.count("Failed to find two consecutive MPEG audio frames"),
                2,
            )

    def test_frame_name_shape_guard_stays_narrow(self) -> None:
        self.assertIn("length != 3U && length != 4U", self.bridge_source)
        self.assertIn("value >= (unsigned char)'A'", self.bridge_source)
        self.assertIn("value <= (unsigned char)'Z'", self.bridge_source)
        self.assertIn("value >= (unsigned char)'0'", self.bridge_source)
        self.assertIn("value <= (unsigned char)'9'", self.bridge_source)


if __name__ == "__main__":
    unittest.main()
