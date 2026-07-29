from __future__ import annotations

import unittest
from pathlib import Path


class V6020TerminalCandidateReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "native_engine" / "src" / "audio_probe.c").read_text(encoding="utf-8")

    def test_terminal_probe_cannot_remain_prebuffer_ready(self) -> None:
        terminal = self.source[self.source.index("static void set_terminal_status_locked"):self.source.index("static void mark_prebuffer_ready")]
        self.assertIn("probe->prebuffer_ready = false;", terminal)

    def test_zero_byte_eof_cannot_emit_prebuffer_ready(self) -> None:
        ready = self.source[self.source.index("static void mark_prebuffer_ready"):self.source.index("static void run_decode")]
        self.assertIn("probe->ring_fill >= WB_AUDIO_FRAME_BYTES", ready)

    def test_shareable_candidate_must_be_live_or_own_pcm(self) -> None:
        helper = self.source[self.source.index("static bool probe_has_live_shareable_content"):self.source.index("static bool identity_matches")]
        self.assertIn("|| probe->eof", helper)
        self.assertIn("probe->ring_fill >= WB_AUDIO_FRAME_BYTES", helper)
        share = self.source[self.source.index("static WbAudioDeckProbe *find_shareable_content_locked"):self.source.index("static WbAudioDeckProbe *find_replacement_identity_locked")]
        self.assertIn("probe_has_live_shareable_content(first)", share)
        self.assertIn("probe_has_live_shareable_content(second)", share)

    def test_track_started_reopens_terminal_or_empty_identity(self) -> None:
        self.assertIn("!probe_can_activate_without_restart(probe)", self.source)
        self.assertIn("queue a fresh decode session instead", self.source)


if __name__ == "__main__":
    unittest.main()
