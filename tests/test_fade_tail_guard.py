from __future__ import annotations

import ast
import unittest
from pathlib import Path


class FadeTailGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        wanted = {"_ab_enforce_fade_tail_window", "_ab_sanitize_timing_metadata"}
        nodes = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in wanted
        ]
        namespace: dict[str, object] = {}
        for node in nodes:
            exec(ast.get_source_segment(source, node) or "", namespace)
        cls.guard = staticmethod(namespace["_ab_enforce_fade_tail_window"])
        cls.sanitize = staticmethod(namespace["_ab_sanitize_timing_metadata"])
        cls.source = source

    def test_brooklyn_bounce_trigger_is_moved_before_eof_for_full_fade(self) -> None:
        timing = self.guard(
            play_start=0.0,
            crossfade_trigger=228.479,
            effective_end=228.479,
            fade_duration=5.0,
            source_end=228.479,
        )
        self.assertTrue(timing["guarded"])
        self.assertAlmostEqual(timing["crossfade_trigger"], 223.479, places=3)
        self.assertAlmostEqual(timing["available_tail"], 5.0, places=3)

    def test_existing_valid_dido_fade_window_is_preserved(self) -> None:
        timing = self.guard(
            play_start=0.460,
            crossfade_trigger=526.089,
            effective_end=531.089,
            fade_duration=5.0,
            source_end=531.089,
        )
        self.assertFalse(timing["guarded"])
        self.assertAlmostEqual(timing["crossfade_trigger"], 526.089, places=3)

    def test_short_no_crossfade_id_remains_unchanged(self) -> None:
        timing = self.guard(
            play_start=6.100,
            crossfade_trigger=11.290,
            effective_end=11.290,
            fade_duration=0.0,
            source_end=17.616,
        )
        self.assertFalse(timing["guarded"])
        self.assertAlmostEqual(timing["crossfade_trigger"], 11.290, places=3)

    def test_runtime_uri_sanitizer_applies_same_fade_tail_guard(self) -> None:
        result = self.sanitize({
            "wb_play_start": "0.000",
            "wb_crossfade_trigger": "228.479",
            "wb_effective_end": "228.479",
            "wb_orig_total": "228.479",
            "fade_out": "5.000",
        })
        self.assertEqual(result["wb_crossfade_trigger"], "223.479")
        self.assertEqual(result["wb_effective_end"], "228.479")
        self.assertEqual(result["fade_out"], "5.000")
        self.assertEqual(result["wb_fade_tail_guard"], "1")

    def test_sanitizer_scrubs_but_does_not_read_retired_aliases(self) -> None:
        result = self.sanitize({
            "liq_cue_in": "1.250",
            "liq_cue_out": "10.000",
            "liq_fade_in": "0.500",
            "liq_fade_out": "2.000",
            "liq_disable_autocue": "1",
            "wb_fade_duration": "1.000",
            "wb_fade_out": "0.750",
            "wb_effective_end": "12.000",
            "wb_orig_total": "12.000",
        })
        self.assertEqual(result["cue_in"], "0.000")
        self.assertEqual(result["cue_out"], "12.000")
        self.assertEqual(result["fade_in"], "0.000")
        self.assertEqual(result["fade_out"], "0.000")
        self.assertEqual(result["disable_autocue"], "1")
        self.assertFalse(any(key.startswith("liq_") for key in result))
        self.assertNotIn("wb_fade_duration", result)
        self.assertNotIn("wb_fade_out", result)

    def test_canonical_fields_override_private_wb_aliases(self) -> None:
        result = self.sanitize({
            "cue_in": "1.500",
            "wb_play_start": "1.000",
            "cue_out": "10.000",
            "wb_crossfade_trigger": "9.000",
            "fade_in": "0.400",
            "fade_out": "2.000",
            "wb_fade_duration": "1.000",
            "wb_effective_end": "12.000",
            "wb_orig_total": "12.000",
        })
        self.assertEqual(result["cue_in"], "1.500")
        self.assertEqual(result["cue_out"], "10.000")
        self.assertEqual(result["fade_in"], "0.400")
        self.assertEqual(result["fade_out"], "2.000")
        self.assertNotIn("wb_fade_duration", result)
        self.assertNotIn("wb_fade_out", result)

    def test_monitor_and_duration_helpers_use_the_guard(self) -> None:
        line_info_start = self.source.index("def _ab_line_info")
        line_info_end = self.source.index("def _ab_line_duration_and_fade")
        line_info = self.source[line_info_start:line_info_end]
        duration_start = line_info_end
        duration_end = self.source.index("def _ab_runtime_annotate_uri", duration_start)
        duration_helper = self.source[duration_start:duration_end]
        self.assertIn("_ab_enforce_fade_tail_window", line_info)
        self.assertIn("_ab_enforce_fade_tail_window", duration_helper)


if __name__ == "__main__":
    unittest.main()
