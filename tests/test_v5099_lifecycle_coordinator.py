from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from audio_engine.lifecycle import NativeLifecycleCoordinator


class NativeLifecycleCoordinatorTests(unittest.TestCase):
    @staticmethod
    def _event(name: str, *, queue_id: int = 0, token: str = "", source: str = ""):
        return SimpleNamespace(
            event=name,
            station_key="db-AirFM.db",
            queue_id=queue_id,
            slot_token=token,
            deck="B",
            payload={"source": source},
        )

    def _coordinator(self, **overrides):
        wakes: list[tuple[str, str]] = []
        defaults = {
            "process_track_started": lambda event: True,
            "track_started_signature": lambda event: f"{event.station_key}|{event.queue_id}|{event.slot_token}",
            "load_requested_next_track": lambda event, request_key: None,
            "mark_seek_pending": lambda event: True,
            "mark_seek_applied": lambda event: True,
            "reconcile_transition_finished": lambda event: True,
            "mark_hard_handoff_claimed": lambda event: True,
            "mark_hard_handoff_completed": lambda event: True,
            "signal_monitor_wake": lambda station, reason="": wakes.append((station, reason)),
        }
        defaults.update(overrides)
        return NativeLifecycleCoordinator(**defaults), wakes

    def test_transition_finished_is_reconciled_and_wakes_monitor(self) -> None:
        calls: list[int] = []
        coordinator, wakes = self._coordinator(
            reconcile_transition_finished=lambda event: calls.append(event.queue_id) or True
        )
        coordinator.handle_event(self._event("transition_finished", queue_id=42, token="q42"))
        self.assertEqual(calls, [42])
        self.assertEqual(wakes, [("db-AirFM.db", "transition_finished")])

    def test_track_started_is_ordered_and_deduplicated_by_queue_id(self) -> None:
        processed: list[tuple[int, str]] = []
        ready = threading.Event()

        def process(event):
            processed.append((event.queue_id, event.slot_token))
            ready.set()
            return True

        coordinator, _wakes = self._coordinator(process_track_started=process)
        coordinator.start(SimpleNamespace(subscribe_events=lambda callback: lambda: None))
        coordinator.handle_event(
            self._event("track_started", queue_id=51, token="first", source="native_select_command")
        )
        coordinator.handle_event(
            self._event("track_started", queue_id=51, token="second", source="native_select_command")
        )
        self.assertTrue(ready.wait(1.0))
        time.sleep(0.05)
        self.assertEqual(processed, [(51, "first")])

    def test_need_next_inflight_is_deduplicated(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []

        def load(event, request_key):
            calls.append(request_key)
            entered.set()
            release.wait(1.0)

        coordinator, _wakes = self._coordinator(load_requested_next_track=load)
        event = self._event("native_need_next_track", queue_id=62, token="q62")
        coordinator.handle_event(event)
        self.assertTrue(entered.wait(1.0))
        coordinator.handle_event(event)
        release.set()
        time.sleep(0.05)
        self.assertEqual(len(calls), 1)

    def test_app_contains_only_thin_lifecycle_forwarder(self) -> None:
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "app.py").read_text(encoding="utf-8")
        lifecycle_source = (root / "audio_engine" / "lifecycle.py").read_text(encoding="utf-8")
        self.assertIn("class NativeLifecycleCoordinator", lifecycle_source)
        self.assertIn("_track_queue_id_pending", lifecycle_source)
        self.assertIn("_get_native_lifecycle_coordinator().handle_event(event)", app_source)
        self.assertNotIn("_NATIVE_TRACK_EVENT_QUEUE", app_source)
        self.assertNotIn("_NATIVE_NEXT_LOAD_INFLIGHT", app_source)
        self.assertNotIn("def _native_track_started_worker", app_source)


if __name__ == "__main__":
    unittest.main()
