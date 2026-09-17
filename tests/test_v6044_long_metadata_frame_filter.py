from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class V6044LongMetadataFrameFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.bridge_source = (cls.root / "native_engine" / "src" / "libav_bridge.c").read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

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

    def test_native_filter_covers_observed_long_labels_without_length_guard(self) -> None:
        self.assertIn('strcmp(format, "Error reading frame %s, skipped\\n") == 0', self.bridge_source)
        self.assertNotIn("valid_id3_frame_name", self.bridge_source)
        for label in ("LYRICIST", "MIXARTIST", "INVOLVEDPEOPLE"):
            self.assertIn(label, self.bridge_source)

    def test_process_console_filter_covers_observed_long_labels(self) -> None:
        should_suppress = self._load_console_filter()
        for label in (b"LYRICIST", b"MIXARTIST", b"INVOLVEDPEOPLE"):
            self.assertTrue(should_suppress(b"Error reading frame " + label + b", skipped\n"))
            self.assertTrue(should_suppress(b"[mp3 @ 0x1234] Error reading frame " + label + b", skipped\n"))
        self.assertFalse(should_suppress(b"Error reading frame LYRICIST, fatal\n"))
        self.assertFalse(should_suppress(b"Error reading frame lower_case, skipped\n"))
        self.assertFalse(should_suppress(b"Failed to find two consecutive MPEG audio frames.\n"))

    def test_libav_callback_hides_long_labels_but_keeps_unrelated_error(self) -> None:
        compiler = shutil.which("cc")
        if compiler is None:
            self.skipTest("C compiler is not available")
        ffmpeg_include = self.root / "native_engine" / "ffmpeg_sdk" / "include"
        ffmpeg_lib = self.root / "lib"
        if not ffmpeg_include.is_dir() or not ffmpeg_lib.is_dir():
            self.skipTest("Bundled FFmpeg SDK/runtime is not staged in the source tree")

        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source = work / "id3_long_label_log_smoke.c"
            binary = work / "id3_long_label_log_smoke"
            source.write_text(r'''#include "libav_bridge.h"
#include <libavutil/log.h>
#include <stdio.h>

int main(void) {
    char error[256] = "";
    if (wb_libav_runtime_init(NULL, error, sizeof(error)) != 0) {
        fprintf(stderr, "runtime init failed: %s\n", error);
        return 2;
    }
    av_log(NULL, AV_LOG_ERROR, "Cannot read BOM value, input too short\n");
    av_log(NULL, AV_LOG_ERROR, "Error reading frame %s, skipped\n", "LYRICIST");
    av_log(NULL, AV_LOG_ERROR, "Error reading frame %s, skipped\n", "MIXARTIST");
    av_log(NULL, AV_LOG_ERROR, "Error reading frame %s, skipped\n", "INVOLVEDPEOPLE");
    av_log(NULL, AV_LOG_ERROR, "v6044 sentinel real libav error\n");
    wb_libav_runtime_shutdown();
    return 0;
}
''', encoding="utf-8")
            command = [
                compiler, "-std=c11", "-Wall", "-Wextra", "-Wpedantic", "-pthread",
                f"-I{self.root / 'native_engine' / 'include'}", f"-I{ffmpeg_include}",
                str(source), str(self.root / "native_engine" / "src" / "libav_bridge.c"),
                f"-L{ffmpeg_lib}", "-l:libavformat.so.61", "-l:libavcodec.so.61",
                "-l:libswresample.so.5", "-l:libavutil.so.59",
                f"-Wl,-rpath,{ffmpeg_lib}", "-lm", "-o", str(binary),
            ]
            build = subprocess.run(command, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60.0)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10.0)
            self.assertEqual(run.returncode, 0, run.stderr)
            for noisy in (
                "Cannot read BOM value, input too short",
                "Error reading frame LYRICIST, skipped",
                "Error reading frame MIXARTIST, skipped",
                "Error reading frame INVOLVEDPEOPLE, skipped",
            ):
                self.assertNotIn(noisy, run.stderr)
            self.assertIn("v6044 sentinel real libav error", run.stderr)


if __name__ == "__main__":
    unittest.main()
