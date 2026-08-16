from __future__ import annotations

import unittest
from pathlib import Path


class V6009LiveDspSourceSwapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.engine_header = (cls.root / "native_engine/include/engine.h").read_text(encoding="utf-8")
        cls.output_source = (cls.root / "native_engine/src/icecast_output.c").read_text(encoding="utf-8")

    def test_encoder_input_is_stable_and_dsp_is_in_process(self) -> None:
        self.assertIn("pthread_mutex_t pcm_route_lock", self.engine_header)
        self.assertNotIn("pthread_t dsp_reader_thread", self.engine_header)
        self.assertNotIn("pthread_t dsp_writer_thread", self.engine_header)
        self.assertIn("ssnative_process_s16_interleaved", self.output_source)
        self.assertIn("wb_libav_encoder_group_push_pcm", self.output_source)
        self.assertNotIn("dsp_output_reader_thread_main", self.output_source)

    def test_live_selector_routes_dry_until_processed_pcm_is_ready(self) -> None:
        self.assertIn("dsp_route_active", self.output_source)
        self.assertIn("dsp_live_bypass_until_ready", self.output_source)
        self.assertIn("reconfigure_live_dsp", self.output_source)
        self.assertIn('"native_dsp_source_swapped"', self.output_source)
        self.assertIn("(dsp_configuration_changed && !live_dsp_switch_candidate)", self.output_source)

    def test_python_control_path_documents_no_encoder_or_icecast_restart(self) -> None:
        self.assertIn("live PCM source selector", self.app_source)
        self.assertIn("without restarting the encoder", self.app_source)
        self.assertIn("reconnecting any Icecast output", self.app_source)

    def test_versions_are_synchronized(self) -> None:
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6009 - 2026-07-22", history)


if __name__ == "__main__":
    unittest.main()
