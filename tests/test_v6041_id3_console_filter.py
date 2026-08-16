from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6041Id3ConsoleFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.bridge_source = (
            cls.root / "native_engine" / "src" / "libav_bridge.c"
        ).read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

    def test_native_filter_does_not_require_avclass_context(self) -> None:
        self.assertIn("suppress_noisy_id3_metadata_log", self.bridge_source)
        self.assertIn("(void)avcl;", self.bridge_source)
        self.assertNotIn(
            'strcmp(av_class->class_name, "AVFormatContext") != 0',
            self.bridge_source,
        )
        self.assertIn(
            'strcmp(format, "Cannot read BOM value, input too short\\n") == 0',
            self.bridge_source,
        )
        self.assertIn('return valid_id3_frame_name(frame_name);', self.bridge_source)

    def test_process_console_has_exact_second_line_of_defense(self) -> None:
        self.assertIn("b'Cannot read BOM value, input too short'", self.app_source)
        self.assertIn("marker = b'Error reading frame '", self.app_source)

    def test_real_malformed_tcon_open_is_quiet_but_other_libav_error_remains(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")

        ffmpeg_include = self.root / "native_engine" / "ffmpeg_sdk" / "include"
        ffmpeg_lib = self.root / "lib"
        if not ffmpeg_include.is_dir() or not ffmpeg_lib.is_dir():
            self.skipTest("Bundled FFmpeg SDK/runtime is not staged in the source tree")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            malformed = work / "malformed_tcon.mp3"
            frame = b"TCON" + (1).to_bytes(4, "big") + b"\x00\x00" + b"\x01"
            size = len(frame)
            syncsafe = bytes(
                [
                    (size >> 21) & 0x7F,
                    (size >> 14) & 0x7F,
                    (size >> 7) & 0x7F,
                    size & 0x7F,
                ]
            )
            malformed.write_bytes(b"ID3\x03\x00\x00" + syncsafe + frame)

            source = work / "id3_real_open_smoke.c"
            binary = work / "id3_real_open_smoke"
            source.write_text(
                r'''#include "libav_bridge.h"
#include <libavformat/avformat.h>
#include <stdio.h>

int main(int argc, char **argv) {
    char error[256] = "";
    AVFormatContext *context = NULL;
    int result;

    if (argc != 2) return 9;
    if (wb_libav_runtime_init(NULL, error, sizeof(error)) != 0) {
        fprintf(stderr, "runtime init failed: %s\n", error);
        return 2;
    }

    result = avformat_open_input(&context, argv[1], NULL, NULL);
    if (context != NULL) avformat_close_input(&context);
    wb_libav_runtime_shutdown();
    return result < 0 ? 0 : 3;
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
                [str(binary), str(malformed)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn("Cannot read BOM value, input too short", run.stderr)
            self.assertNotIn("Error reading frame TCON, skipped", run.stderr)
            self.assertIn("Failed to find two consecutive MPEG audio frames", run.stderr)


if __name__ == "__main__":
    unittest.main()
