from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6040Id3ConsoleFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.bridge_source = (
            cls.root / "native_engine" / "src" / "libav_bridge.c"
        ).read_text(encoding="utf-8")

    def test_filter_still_covers_original_tcon_case(self) -> None:
        self.assertIn("suppress_noisy_id3_metadata_log", self.bridge_source)
        self.assertIn(
            'strcmp(format, "Cannot read BOM value, input too short\\n") == 0',
            self.bridge_source,
        )
        self.assertIn(
            'strcmp(format, "Error reading frame %s, skipped\\n") == 0',
            self.bridge_source,
        )
        self.assertIn('return valid_id3_frame_name(frame_name);', self.bridge_source)
        self.assertIn("va_copy(copy, arguments)", self.bridge_source)
        self.assertIn("av_log_default_callback", self.bridge_source)

    def test_runtime_filter_hides_bom_and_tcon_but_preserves_other_errors(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")

        ffmpeg_include = self.root / "native_engine" / "ffmpeg_sdk" / "include"
        ffmpeg_lib = self.root / "lib"
        if not ffmpeg_include.is_dir() or not ffmpeg_lib.is_dir():
            self.skipTest("Bundled FFmpeg SDK/runtime is not staged in the source tree")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "id3_log_filter_smoke.c"
            binary = work / "id3_log_filter_smoke"
            source.write_text(
                r'''#include "libav_bridge.h"
#include <libavformat/avformat.h>
#include <libavutil/log.h>
#include <stdio.h>

int main(void) {
    char error[256] = "";
    AVFormatContext *context;

    if (wb_libav_runtime_init(NULL, error, sizeof(error)) != 0) {
        fprintf(stderr, "runtime init failed: %s\n", error);
        return 2;
    }

    context = avformat_alloc_context();
    if (context == NULL) return 3;

    av_log(context, AV_LOG_ERROR, "Cannot read BOM value, input too short\n");
    av_log(context, AV_LOG_ERROR, "Error reading frame %s, skipped\n", "TCON");
    av_log(context, AV_LOG_ERROR, "Error reading frame %s, skipped\n", "TIT2");
    av_log(context, AV_LOG_ERROR, "visible libav smoke error\n");

    avformat_free_context(context);
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
                [str(binary)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10.0,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertNotIn("Cannot read BOM value, input too short", run.stderr)
            self.assertNotIn("Error reading frame TCON, skipped", run.stderr)
            self.assertNotIn("Error reading frame TIT2, skipped", run.stderr)
            self.assertIn("visible libav smoke error", run.stderr)


if __name__ == "__main__":
    unittest.main()
