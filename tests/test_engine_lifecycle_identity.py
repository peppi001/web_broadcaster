from __future__ import annotations

import unittest

from audio_engine.lifecycle import EngineLifecycleIdentityRegistry


class EngineLifecycleIdentityTests(unittest.TestCase):
    def test_missing_slot_token_is_filled_only_for_same_confirmed_track(self) -> None:
        registry = EngineLifecycleIdentityRegistry()
        registry.remember_confirmed(
            "station-1",
            {
                "deck": "B",
                "queue_id": 14753,
                "track_id": 847,
                "slot_token": "14753-847-live-token",
                "path": "/music/ID4.mp3",
                "artist": "ID Artist",
                "title": "ID Title",
                "year": "1998",
            },
        )

        resolved = registry.resolve_confirmed(
            "station-1",
            {
                "deck": "B",
                "queue_id": 14753,
                "track_id": 847,
                "slot_token": "",
                "path": "/music/ID4.mp3",
            },
        )
        self.assertEqual(resolved["slot_token"], "14753-847-live-token")
        self.assertEqual(resolved["year"], "1998")

        different = registry.resolve_confirmed(
            "station-1",
            {
                "deck": "B",
                "queue_id": 14754,
                "track_id": 847,
                "slot_token": "",
                "path": "/music/ID4.mp3",
            },
        )
        self.assertEqual(different["slot_token"], "")

    def test_immediate_identical_deck_loaded_is_deduplicated(self) -> None:
        registry = EngineLifecycleIdentityRegistry(deck_loaded_dedupe_seconds=2.0)
        identity = {
            "deck": "A",
            "queue_id": 10,
            "track_id": 20,
            "slot_token": "10-20-token",
            "path": "/music/test.mp3",
        }
        first, _ = registry.accept_deck_loaded("station-1", identity, monotonic_time=10.0)
        duplicate, _ = registry.accept_deck_loaded("station-1", identity, monotonic_time=10.1)
        later_reload, _ = registry.accept_deck_loaded("station-1", identity, monotonic_time=12.2)

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertTrue(later_reload)

    def test_recycled_deck_does_not_donate_new_track_identity_to_old_end(self) -> None:
        registry = EngineLifecycleIdentityRegistry()
        registry.remember_confirmed(
            "station-1",
            {
                "deck": "A",
                "queue_id": 101,
                "track_id": 1,
                "slot_token": "old-token",
                "path": "/music/old.mp3",
            },
        )
        captured_old = registry.resolve_confirmed(
            "station-1",
            {
                "deck": "A",
                "queue_id": 101,
                "track_id": 1,
                "slot_token": "",
                "path": "/music/old.mp3",
            },
        )
        registry.remember_confirmed(
            "station-1",
            {
                "deck": "A",
                "queue_id": 103,
                "track_id": 3,
                "slot_token": "new-token",
                "path": "/music/new.mp3",
            },
        )
        completed_old = registry.resolve_confirmed("station-1", captured_old)
        self.assertEqual(completed_old["queue_id"], 101)
        self.assertEqual(completed_old["slot_token"], "old-token")


if __name__ == "__main__":
    unittest.main()
