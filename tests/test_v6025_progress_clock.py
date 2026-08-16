from __future__ import annotations

import unittest
from pathlib import Path


class V6025ProgressClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")

    def test_backend_status_poll_frequency_is_unchanged(self) -> None:
        self.assertIn("const STUDIO_STATUS_POLL_MS = 2000;", self.source)
        self.assertIn("jsonFetch('/api/status?with_progress=1')", self.source)
        self.assertIn("setInterval(loadStatus, STUDIO_STATUS_POLL_MS);", self.source)

    def test_local_progress_uses_monotonic_anchor(self) -> None:
        self.assertIn("const STUDIO_PROGRESS_UI_TICK_MS = 100;", self.source)
        self.assertIn("performance.now()", self.source)
        self.assertIn("studioProgressAnchorElapsed", self.source)
        self.assertIn("studioProgressAnchorNowMs", self.source)
        self.assertIn("readStudioProgressElapsed", self.source)
        self.assertIn("syncStudioProgressFromServer", self.source)
        self.assertNotIn("studioLastUpdate", self.source)

    def test_progress_refresh_is_local_and_faster_than_backend_poll(self) -> None:
        self.assertIn(
            "setInterval(updateDeckProgressLocalClock, STUDIO_PROGRESS_UI_TICK_MS);",
            self.source,
        )
        self.assertNotIn("setInterval(updateDeckProgressLocalClock, 1000);", self.source)

    def test_normal_status_jitter_cannot_step_visible_clock_backwards(self) -> None:
        self.assertIn("const STUDIO_PROGRESS_HARD_SYNC_SECONDS = 1.5;", self.source)
        self.assertIn("const STUDIO_PROGRESS_MAX_SOFT_CORRECTION_SECONDS = 0.08;", self.source)
        self.assertIn("Never step the visible clock backwards", self.source)
        self.assertIn("Math.min(STUDIO_PROGRESS_MAX_SOFT_CORRECTION_SECONDS, drift)", self.source)


if __name__ == "__main__":
    unittest.main()
