from __future__ import annotations

import unittest
from pathlib import Path


class V6064LiveOutputWatchdogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")

    def test_live_add_publishes_watchdog_start_before_unlock(self) -> None:
        configure_start = self.source.index("int wb_icecast_output_configure")
        configure_end = self.source.index("int wb_icecast_output_clear_stream", configure_start)
        configure = self.source[configure_start:configure_end]

        candidate = configure.index("live_add_candidate = pipeline_live")
        timestamp = configure.index("stream->last_encoded_data_monotonic_ms = monotonic_ms();", candidate)
        ready = configure.index("stream->encoder_ready = false;", candidate)
        unlock = configure.index("pthread_mutex_unlock(&output->lock);", candidate)
        add_branch = configure.index("int live_result = add_live_encoder_branch(", unlock)

        self.assertLess(ready, unlock)
        self.assertLess(timestamp, unlock)
        self.assertLess(unlock, add_branch)

    def test_watchdog_still_times_out_a_branch_that_never_produces_data(self) -> None:
        self.assertIn("WB_OUTPUT_ENCODER_STALL_TIMEOUT_MS 15000", self.source)
        self.assertIn(
            "now - stream->last_encoded_data_monotonic_ms > WB_OUTPUT_ENCODER_STALL_TIMEOUT_MS",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
