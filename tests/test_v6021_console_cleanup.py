from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6021ConsoleCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.bridge_source = (
            cls.root / "native_engine" / "src" / "libav_bridge.c"
        ).read_text(encoding="utf-8")

    def test_deprecated_utcnow_calls_are_gone_without_changing_naive_storage_format(self) -> None:
        self.assertNotIn("datetime.utcnow()", self.app_source)
        self.assertIn("from datetime import datetime, timedelta, timezone", self.app_source)
        self.assertIn(
            "return datetime.now(timezone.utc).replace(tzinfo=None)",
            self.app_source,
        )
        self.assertIn("_utc_now_naive().isoformat", self.app_source)

    def test_mp3_header_filter_is_narrow_and_other_libav_errors_remain_visible(self) -> None:
        self.assertIn("suppress_noisy_mp3_header_log", self.bridge_source)
        self.assertIn('strcmp(item_name, "mp3float") == 0', self.bridge_source)
        self.assertIn('strstr(format, "Header missing")', self.bridge_source)
        self.assertIn("av_log_default_callback", self.bridge_source)
        self.assertIn("av_log_set_callback(wb_libav_log_callback)", self.bridge_source)

    def test_runtime_callback_suppresses_only_mp3_header_missing(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "log_filter_smoke.c"
            binary = work / "log_filter_smoke"
            source.write_text(
                r'''#include "libav_bridge.h"
#include <libavcodec/avcodec.h>
#include <libavutil/log.h>
#include <stdio.h>

int main(void) {
    char error[256] = "";
    const AVCodec *codec;
    AVCodecContext *context;

    if (wb_libav_runtime_init(NULL, error, sizeof(error)) != 0) {
        fprintf(stderr, "runtime init failed: %s\n", error);
        return 2;
    }
    codec = avcodec_find_decoder(AV_CODEC_ID_MP3);
    context = avcodec_alloc_context3(codec);
    if (context == NULL) return 3;

    av_log(context, AV_LOG_ERROR, "Header missing\n");
    av_log(context, AV_LOG_ERROR, "visible libav smoke error\n");

    avcodec_free_context(&context);
    wb_libav_runtime_shutdown();
    return 0;
}
''',
                encoding="utf-8",
            )
            ffmpeg_lib = self.root / "lib"
            command = [
                compiler,
                "-std=c11",
                "-Wall",
                "-Wextra",
                "-Wpedantic",
                "-pthread",
                f"-I{self.root / 'native_engine' / 'include'}",
                f"-I{self.root / 'native_engine' / 'ffmpeg_sdk' / 'include'}",
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
            self.assertNotIn("Header missing", run.stderr)
            self.assertIn("visible libav smoke error", run.stderr)


if __name__ == "__main__":
    unittest.main()
