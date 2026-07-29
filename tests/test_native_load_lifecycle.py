from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from audio_engine import NativeEngine


class NativeLoadLifecycleTests(unittest.TestCase):
    @contextmanager
    def _running_native(self) -> Iterator[NativeEngine]:
        root = Path(__file__).resolve().parents[1]
        binary = root / "native_engine" / "bin" / "web_broadcaster_engine"
        if not binary.exists() or not os.access(binary, os.X_OK):
            self.skipTest("native daemon binary is not available")

        temporary = tempfile.TemporaryDirectory()
        socket_path = Path(temporary.name) / "engine.sock"
        process = subprocess.Popen(
            [str(binary), str(socket_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        native: NativeEngine | None = None
        try:
            deadline = time.monotonic() + 3.0
            while not socket_path.exists() and time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(socket_path.exists(), "native daemon socket was not created")
            native = NativeEngine(
                socket_path=str(socket_path),
                request_timeout_sec=1.0,
                reconnect_delay_sec=0.05,
            )
            yield native
        finally:
            if native is not None:
                native.close()
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            if process.stdout is not None:
                process.stdout.close()
            temporary.cleanup()

    @staticmethod
    def _uri(queue_id: int, token: str, path: str = "/music/test.mp3") -> str:
        return (
            f'annotate:queue_id="{queue_id}",track_id="{queue_id + 100}",'
            f'station_key="station",wb_ab_slot_token="{token}":{path}'
        )

    @staticmethod
    def _event(
        queue_id: int,
        token: str,
        *,
        event: str = "deck_loaded",
        deck: str = "A",
    ) -> dict[str, object]:
        return {
            "event": event,
            "station_key": "station",
            "queue_id": queue_id,
            "slot_token": token,
            "deck": deck,
            "track_id": queue_id + 100,
            "path": "/music/test.mp3",
            "payload": {"source": "native-load-lifecycle-test"},
        }

    def test_load_is_immediately_confirmed_by_authoritative_daemon(self) -> None:
        with self._running_native() as native:
            response = native.request(
                "load",
                station_key="station",
                deck="A",
                uri=self._uri(14741, "token-8"),
                track={
                    "station_key": "station",
                    "queue_id": 14741,
                    "slot_token": "token-8",
                    "track_id": 14841,
                    "path": "/music/test.mp3",
                },
                options={"clear_slot": True},
            )["result"]
            self.assertTrue(response["accepted"])
            self.assertEqual(response["load_state"], "confirmed")
            self.assertFalse(response["deduplicated"])

            state = native.get_state(station_key="station")
            self.assertEqual(state["deck_a_queue_id"], 14741)
            self.assertEqual(state["deck_a_slot_token"], "token-8")
            self.assertFalse(state["deck_a_load_pending"])
            self.assertEqual(state["deck_a_planned_queue_id"], 0)
            self.assertEqual(state["planned_load_count"], 1)
            self.assertEqual(state["confirmed_load_count"], 1)
            self.assertEqual(state["cancelled_load_count"], 0)

    def test_stop_rejects_late_load_and_ignores_late_deck_loaded(self) -> None:
        with self._running_native() as native:
            native.start(station_key="station")
            self.assertTrue(native.get_state(station_key="station")["accepting_loads"])
            native.stop(station_key="station")
            stopped = native.get_state(station_key="station")
            self.assertFalse(stopped["running"])
            self.assertFalse(stopped["accepting_loads"])

            self.assertFalse(
                native.load_deck(
                    "A",
                    self._uri(14790, "late-after-stop"),
                    station_key="station",
                )
            )
            after_load = native.get_state(station_key="station")
            self.assertEqual(after_load["deck_a_slot_token"], "")
            self.assertEqual(after_load["late_load_rejected_count"], 1)

            native.sync_live_event(self._event(14790, "late-after-stop"))
            after_event = native.get_state(station_key="station")
            self.assertEqual(after_event["deck_a_slot_token"], "")
            self.assertFalse(after_event["native_audio_deck_a_running"])
            self.assertEqual(after_event["late_event_ignored_count"], 1)

    def test_newer_identity_survives_stale_track_end(self) -> None:
        with self._running_native() as native:
            self.assertTrue(native.load_deck("A", self._uri(100, "old-token")))
            self.assertTrue(native.load_deck("A", self._uri(101, "new-token")))
            before = native.get_state(station_key="station")
            self.assertEqual(before["deck_a_slot_token"], "new-token")

            native.sync_live_event(
                self._event(100, "old-token", event="track_ended")
            )
            after = native.get_state(station_key="station")
            self.assertEqual(after["deck_a_queue_id"], 101)
            self.assertEqual(after["deck_a_slot_token"], "new-token")
            self.assertEqual(after["native_audio_deck_a_candidate_primary_slot_token"], "new-token")

    def test_clear_slot_load_is_confirmed_without_pending_shadow_state(self) -> None:
        with self._running_native() as native:
            self.assertTrue(
                native.load_deck(
                    "B",
                    self._uri(200, "clear-slot-token"),
                    clear_slot=True,
                )
            )
            state = native.get_state(station_key="station")
            self.assertFalse(state["deck_b_load_pending"])
            self.assertEqual(state["deck_b_queue_id"], 200)
            self.assertEqual(state["deck_b_slot_token"], "clear-slot-token")
            self.assertEqual(state["planned_load_count"], 1)
            self.assertEqual(state["confirmed_load_count"], 1)
            self.assertEqual(state["cancelled_load_count"], 0)

    def test_duplicate_load_is_idempotent_and_does_not_increment_counters(self) -> None:
        with self._running_native() as native:
            fields = {
                "station_key": "station",
                "deck": "A",
                "uri": self._uri(300, "duplicate-token"),
                "track": {
                    "station_key": "station",
                    "queue_id": 300,
                    "slot_token": "duplicate-token",
                    "track_id": 400,
                    "path": "/music/test.mp3",
                },
                "options": {
                    "attempts": 8,
                    "retry_delay": 0.35,
                    "clear_slot": True,
                    "manual_next_fast": False,
                },
            }
            first = native.request("load", **fields)["result"]
            second = native.request("load", **fields)["result"]
            self.assertEqual(first["load_state"], "confirmed")
            self.assertFalse(first["deduplicated"])
            self.assertEqual(second["load_state"], "confirmed")
            self.assertTrue(second["deduplicated"])

            state = native.get_state(station_key="station")
            self.assertEqual(state["planned_load_count"], 1)
            self.assertEqual(state["confirmed_load_count"], 1)
            self.assertEqual(state["cancelled_load_count"], 0)
            self.assertEqual(state["deck_a_slot_token"], "duplicate-token")

    def test_unique_load_counter_model_matches_authoritative_confirmations(self) -> None:
        with self._running_native() as native:
            unique_count = 12
            for index in range(unique_count):
                queue_id = 1000 + index
                token = f"confirmed-{index}"
                deck = "A" if index % 2 == 0 else "B"
                uri = self._uri(queue_id, token)
                self.assertTrue(native.load_deck(deck, uri, clear_slot=True))
                self.assertTrue(native.load_deck(deck, uri, clear_slot=True))

            state = native.get_state(station_key="station")
            self.assertEqual(state["planned_load_count"], unique_count)
            self.assertEqual(state["confirmed_load_count"], unique_count)
            self.assertEqual(state["cancelled_load_count"], 0)
            self.assertFalse(state["deck_a_load_pending"])
            self.assertFalse(state["deck_b_load_pending"])


if __name__ == "__main__":
    unittest.main()
