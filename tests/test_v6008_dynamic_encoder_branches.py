from __future__ import annotations

import unittest
from pathlib import Path


class V6008DynamicEncoderBranchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.bridge_header = (cls.root / "native_engine" / "include" / "libav_bridge.h").read_text(encoding="utf-8")
        cls.bridge_source = (cls.root / "native_engine" / "src" / "libav_bridge.c").read_text(encoding="utf-8")
        cls.output_source = (cls.root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")
        cls.engine_header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

    def test_libav_group_exposes_live_branch_lifecycle(self) -> None:
        self.assertIn("wb_libav_encoder_group_add_branch", self.bridge_header)
        self.assertIn("wb_libav_encoder_group_remove_branch", self.bridge_header)
        self.assertIn("size_t branch_capacity", self.bridge_source)
        self.assertIn("branch->active = true", self.bridge_source)
        self.assertIn("branch->active = false", self.bridge_source)
        self.assertIn("group->branches[config->stream_index]", self.bridge_source)

    def test_output_uses_a_separate_encoder_lifetime_lock(self) -> None:
        self.assertIn("pthread_mutex_t encoder_control_lock", self.engine_header)
        self.assertIn("add_live_encoder_branch", self.output_source)
        self.assertIn("remove_live_encoder_branch", self.output_source)
        self.assertIn("native_encoder_branch_added", self.output_source)
        self.assertIn("native_encoder_branch_removed", self.output_source)
        self.assertIn("without restarting other outputs", self.output_source)

    def test_live_add_remove_are_selected_only_for_a_stable_pipeline(self) -> None:
        self.assertIn("live_add_candidate = pipeline_live", self.output_source)
        self.assertIn("live_remove_candidate = pipeline_live", self.output_source)
        self.assertIn("!output->restart_requested", self.output_source)
        self.assertIn("dsp_configuration_changed", self.output_source)

    def test_versions_are_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6024"', self.app_source)
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6024"', self.engine_header)
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6009 - 2026-07-22", history)


if __name__ == "__main__":
    unittest.main()
