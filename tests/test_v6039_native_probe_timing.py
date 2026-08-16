from __future__ import annotations

import re
import unittest
from pathlib import Path


class V6039NativeProbeTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.text = (cls.root / "tests" / "test_native_audio_probe.py").read_text(encoding="utf-8")

    def _function_body(self, name: str) -> str:
        match = re.search(
            rf"(?ms)^    def {re.escape(name)}\(.*?(?P<body>\n.*?)(?=^    def |^if __name__)",
            self.text,
        )
        self.assertIsNotNone(match, name)
        return match.group("body")

    def test_stop_duration_waits_for_measurable_playback_progress(self) -> None:
        body = self._function_body("test_stopping_settles_to_stopped_and_final_duration_is_frozen")
        self.assertIn("native_audio_probe_played_duration_ms", body)
        self.assertIn(">= 20", body)
        self.assertIn("probe did not make measurable playback progress before stop", body)
        self.assertNotIn("time.sleep(0.12)", body)

    def test_progress_sensitive_probe_cases_use_condition_waits(self) -> None:
        for name in (
            "test_infinite_http_stream_stays_active_until_stop",
            "test_track_seeked_restarts_exact_active_voice_at_new_position",
            "test_old_track_ended_does_not_stop_new_active_probe",
            "test_exact_identity_descriptor_change_emits_audio_mismatch_and_rebuilds_candidate",
            "test_early_eof_during_crossfade_removes_only_outgoing_deck",
        ):
            body = self._function_body(name)
            self.assertIn("self._wait_for_state(", body, name)

    def test_wait_helper_polls_state_with_a_deadline(self) -> None:
        body = self._function_body("_wait_for_state")
        self.assertIn("deadline = time.monotonic() + timeout", body)
        self.assertIn("state = native.get_state()", body)
        self.assertIn("self.assertTrue(predicate(state)", body)


if __name__ == "__main__":
    unittest.main()
