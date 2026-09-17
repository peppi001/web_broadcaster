from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6046ControlStartAndMp3ConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.bridge_source = (
            cls.root / "native_engine" / "src" / "libav_bridge.c"
        ).read_text(encoding="utf-8")

    def _load_console_filter(self):
        tree = ast.parse(self.app_source)
        wanted = {
            "_CONSOLE_SUPPRESSED_LINE_FRAGMENTS",
            "_ID3_CONSOLE_EXACT_MESSAGES",
            "_MP3_RECOVERABLE_CONSOLE_EXACT_MESSAGES",
        }
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = {target.id for target in node.targets if isinstance(target, ast.Name)}
                if names & wanted:
                    nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name == "_console_should_suppress_record":
                nodes.append(node)
        namespace = {}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<console-filter>", "exec"), namespace)
        return namespace["_console_should_suppress_record"]

    def test_control_start_uses_service_result_and_preserves_root_cause(self) -> None:
        for marker in (
            "start_payload, start_status = _get_station_service().start(station_key)",
            'detail = str(start_payload.get("error") or "Native station start failed.").strip()',
            'detail = f"{detail}; bootstrap detail: {bootstrap_detail}"',
            "raise _StationControlStartError(detail)",
            "except _StationControlStartError as e:",
            'app.logger.error("api_control station start failed (action=%s): %s", action, e)',
        ):
            self.assertIn(marker, self.app_source)
        control_source = self.app_source[
            self.app_source.index("def api_control():"):
            self.app_source.index("def _get_seek_end_guard_seconds", self.app_source.index("def api_control():"))
        ]
        self.assertNotIn("print_exc", control_source)

    def test_process_console_hides_only_known_recoverable_mp3_frame_noise(self) -> None:
        should_suppress = self._load_console_filter()
        for record in (
            b"[mp3float @ 0x1234] invalid new backstep -1\n",
            b"[mp3float @ 0x1234] big_values too big\n",
            b"[mp3float @ 0x1234] invalid block type\n",
            b"[mp3float @ 0x1234] Error while decoding MPEG audio frame.\n",
            b"[mp3 @ 0x5678] invalid new backstep 3\n",
        ):
            self.assertTrue(should_suppress(record), record)

        for record in (
            b"[aac @ 0x1234] invalid block type\n",
            b"[mp3float @ 0x1234] Invalid data found when processing input\n",
            b"Error while decoding MPEG audio frame.\n",
            b"application error: big_values too big\n",
        ):
            self.assertFalse(should_suppress(record), record)

    def test_native_callback_matches_exact_recoverable_mp3_formats(self) -> None:
        self.assertIn("suppress_recoverable_mp3_decode_log", self.bridge_source)
        self.assertIn("is_mp3_decoder_context", self.bridge_source)
        for marker in (
            'strcmp(format, "invalid new backstep %d\\n") == 0',
            'strcmp(format, "big_values too big\\n") == 0',
            'strcmp(format, "invalid block type\\n") == 0',
            'strcmp(format, "Error while decoding MPEG audio frame.\\n") == 0',
        ):
            self.assertIn(marker, self.bridge_source)
        self.assertIn("av_log_default_callback(avcl, level, format, arguments);", self.bridge_source)

    def test_native_callback_suppresses_recoverable_mp3_noise_but_keeps_other_errors(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")
        ffmpeg_include = self.root / "native_engine" / "ffmpeg_sdk" / "include"
        ffmpeg_lib = self.root / "lib"
        if not ffmpeg_include.is_dir() or not ffmpeg_lib.is_dir():
            self.skipTest("Bundled FFmpeg SDK/runtime is not staged in the source tree")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "mp3_recoverable_log_smoke.c"
            binary = work / "mp3_recoverable_log_smoke"
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

    av_log(context, AV_LOG_ERROR, "invalid new backstep %d\n", -1);
    av_log(context, AV_LOG_ERROR, "big_values too big\n");
    av_log(context, AV_LOG_ERROR, "invalid block type\n");
    av_log(context, AV_LOG_ERROR, "Error while decoding MPEG audio frame.\n");
    av_log(context, AV_LOG_ERROR, "v6046 sentinel real mp3 error\n");
    av_log(NULL, AV_LOG_ERROR, "big_values too big\n");

    avcodec_free_context(&context);
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
            for noisy in (
                "invalid new backstep -1",
                "invalid block type",
                "Error while decoding MPEG audio frame.",
            ):
                self.assertNotIn(noisy, run.stderr)
            # One generic NULL-context line is intentionally still visible.
            self.assertEqual(run.stderr.count("big_values too big"), 1, run.stderr)
            self.assertIn("v6046 sentinel real mp3 error", run.stderr)


if __name__ == "__main__":
    unittest.main()
