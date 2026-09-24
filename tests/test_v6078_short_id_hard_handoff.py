"""Regression guards for a short target outlasting the outgoing PCM drain."""

from pathlib import Path
import unittest


class V6078ShortIdHardHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1] / "native_engine"
        cls.probe = (root / "src" / "audio_probe.c").read_text(encoding="utf-8")
        cls.output = (root / "src" / "icecast_output.c").read_text(encoding="utf-8")
        cls.header = (root / "include" / "icecast_output.h").read_text(encoding="utf-8")

    def test_primed_short_target_cannot_consume_before_actual_switch(self) -> None:
        # The original incident consumed the 1.13-second target while the
        # outgoing FIFO still contained 1.45 seconds of audible PCM.
        self.assertIn("wb_icecast_output_is_pending_handoff_target(", self.header)
        playback = self.probe[self.probe.index("if (activated) {", self.probe.index("mark_prebuffer_ready")):]
        guard = playback.index("wb_icecast_output_is_pending_handoff_target(")
        consume = playback.index("consume_samples = 0U;", guard)
        discard = playback.index("discarded = ring_read_pcm(", consume)
        self.assertLess(guard, consume)
        self.assertLess(consume, discard)
        helper = self.output[self.output.index("bool wb_icecast_output_is_pending_handoff_target("):
                             self.output.index("bool wb_icecast_output_has_pending_hard_handoff(")]
        self.assertIn("output->hard_handoff_to_track.queue_id == track->queue_id", helper)
        self.assertIn("strcmp(output->hard_handoff_to_track.slot_token, track->slot_token) == 0", helper)
        self.assertIn("output->hard_handoff_pending", helper)
        self.assertIn("output->primary_deck == deck && state->active_deck != deck", helper)

    def test_actual_boundary_retimes_decoder_before_active_identity_changes(self) -> None:
        finalizer = self.output[self.output.index("static void finalize_hard_handoff(",
                                                  self.output.index("static void finalize_hard_handoff(") + 1):]
        retime = finalizer.index("wb_audio_probe_retime_activation(")
        active = finalizer.index("state->active_deck =")
        self.assertLess(retime, active)
        self.assertIn("handoff->actual_monotonic_ms", finalizer[:active])

    def test_terminal_target_or_stop_releases_reserved_destination(self) -> None:
        handler = self.output[self.output.index("bool wb_icecast_output_handle_terminal_eof("):
                              self.output.index("void wb_icecast_output_handle_early_eof(")]
        self.assertIn("output->hard_handoff_to_deck == deck", handler)
        self.assertIn("output->primary_deck != deck", handler)
        self.assertIn("clear_hard_handoff_locked(output);", handler)
        stop = self.output[self.output.index("void wb_icecast_output_stop_track("):
                           self.output.index("bool wb_icecast_output_get_deck_buffered_ms(")]
        self.assertIn("output->hard_handoff_to_deck == deck", stop)
        self.assertIn("clear_hard_handoff_locked(output);", stop)


if __name__ == "__main__":
    unittest.main()
