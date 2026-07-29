from __future__ import annotations

import ast
import os
import tempfile
import unittest
from pathlib import Path


class NativeOnlyTimingCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.function_names = {
            node.name
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        node = next(
            item
            for item in self.tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == name
        )
        return ast.get_source_segment(self.source, node) or ""

    def test_python_pcm_analyzer_and_workers_are_removed(self) -> None:
        retired = {
            "_ab_check_soundfile_backend",
            "_ab_analyze_cue_points_soundfile",
            "_ab_analyze_audio_boundaries_soundfile",
            "_ab_analyze_and_store_soundfile_cues",
            "_ab_schedule_audio_boundary_analysis",
            "_ab_request_autocue_probe",
            "_ab_wait_for_autocue",
            "_ab_apply_runtime_autocue_to_uri",
            "_ab_prewarm_autocue_lines_async",
            "_ab_prewarm_short_item_lookahead",
            "_ab_prewarm_queue_head_lookahead_for_station",
            "_cue_analysis_worker",
            "ensure_track_cue_analysis",
            "enqueue_full_cue_analysis",
            "api_cue_analysis_status",
        }
        self.assertTrue(retired.isdisjoint(self.function_names))
        for marker in (
            "import soundfile",
            "import numpy",
            "ab-soundfile-cue-analysis",
            "ab-soundfile-cue-request",
            "CUE_ANALYSIS_QUEUE",
            "_AB_AUTOCUE_CACHE",
            "/api/cue-analysis/status",
        ):
            self.assertNotIn(marker, self.source)

    def test_python_requirements_no_longer_install_pcm_analysis_packages(self) -> None:
        requirements = (self.root / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertNotIn("soundfile", requirements)
        self.assertNotIn("numpy", requirements)

    def test_automatic_tracks_still_request_native_analysis(self) -> None:
        helper = self._function_source("_ab_native_runtime_timing_metadata")
        self.assertIn('wb_native_analyze="1"', helper)
        self.assertIn('wb_manual_timing="0"', helper)
        self.assertIn('wb_cue_source="native_runtime_pending"', helper)
        self.assertIn('wb_native_analyze="0"', helper)
        self.assertIn('wb_manual_timing="1"', helper)

    def test_manual_clean_transition_uses_db_bounds_without_audio_decode(self) -> None:
        source = self._function_source("_clean_transition_audio_bounds")
        self.assertIn("manual_db_audio_boundary", source)
        self.assertIn("manual_full_file_fallback", source)
        self.assertIn("probe_duration_seconds", source)
        self.assertNotIn("soundfile", source.lower())
        self.assertNotIn("subprocess", source.lower())

        namespace = {
            "normalize_media_path": lambda value: str(value or ""),
            "probe_duration_seconds": lambda _path: 10.0,
            "_ab_clamp_audio_boundary_values": lambda start, end, total: (
                max(0.0, min(float(start), float(total))),
                max(0.0, min(float(end), float(total))),
                max(0.0, float(total)),
            ),
            "os": os,
        }
        exec(source, namespace)
        with tempfile.NamedTemporaryFile(suffix=".mp3") as handle:
            bounded = namespace["_clean_transition_audio_bounds"](
                handle.name, 1.25, 8.5, 99.0
            )
            fallback = namespace["_clean_transition_audio_bounds"](
                handle.name, 0.0, 0.0, 99.0
            )
        self.assertEqual(bounded, (1.25, 8.5, 10.0, "manual_db_audio_boundary"))
        self.assertEqual(fallback, (0.0, 10.0, 10.0, "manual_full_file_fallback"))

    def test_retired_studio_analysis_polling_is_removed(self) -> None:
        javascript = (self.root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
        template = (self.root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        css = (self.root / "html" / "static" / "broadcaster.css").read_text(encoding="utf-8")
        for content in (javascript, template, css):
            self.assertNotIn("studio-analysis-indicator", content)
        self.assertNotIn("loadCueAnalysisStatus", javascript)
        self.assertNotIn("STUDIO_CUE_ANALYSIS_POLL_MS", javascript)


if __name__ == "__main__":
    unittest.main()
