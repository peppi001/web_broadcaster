"""Regression coverage for manual-Next AutoDJ overfill and station isolation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import unittest

from autodj import AutoDJDependencies, AutoDJService


class V6084AutoDJManualNextQueueTests(unittest.TestCase):
    @staticmethod
    def make_service(*, gate_first_station_a_count: bool = False):
        station_var = ContextVar("test_autodj_station", default="station-a.db")
        queues = {
            "station-a.db": [{"track_id": track_id} for track_id in (1, 2, 3)],
            "station-b.db": [{"track_id": track_id} for track_id in (4, 5)],
        }
        cursors = {station: 0 for station in queues}
        gate_enabled = threading.Event()
        first_read = threading.Event()
        release_first = threading.Event()
        second_read = threading.Event()
        count_lock = threading.Lock()
        reads_a = 0

        @contextmanager
        def station_context(station):
            token = station_var.set(station)
            try:
                yield
            finally:
                station_var.reset(token)

        def queue_count():
            nonlocal reads_a
            station = station_var.get()
            count = len(queues[station])
            if station == "station-a.db" and gate_first_station_a_count and gate_enabled.is_set():
                with count_lock:
                    reads_a += 1
                    ordinal = reads_a
                if ordinal == 1:
                    first_read.set()
                    release_first.wait(0.5)
                elif ordinal == 2:
                    # On the unfixed service this releases the first caller
                    # after BOTH have observed the stale queue count of two.
                    second_read.set()
                    release_first.set()
            return count

        def enqueue(items):
            queues[station_var.get()].extend({"track_id": int(item["track_id"])} for item in items)

        deps = AutoDJDependencies(
            get_active_station_key=station_var.get,
            get_settings=lambda: {"keep_queue": 3},
            get_rotation=lambda: [{"category_id": 1, "norules": 1}],
            get_rotation_cursor=lambda _rows, _signature: cursors[station_var.get()],
            set_rotation_cursor=lambda index, _signature: cursors.__setitem__(station_var.get(), index),
            get_queue_snapshot=lambda: list(queues[station_var.get()]),
            get_recent_history_snapshot=lambda *_args: [],
            get_category_tracks=lambda _category: [{"track_id": track_id} for track_id in range(10, 20)],
            enqueue_track_items=enqueue,
            queue_count=queue_count,
            get_now_playing=lambda: {},
            publish_startup_event=lambda *_args: None,
            replan_after_fill=lambda *_args: None,
            schedule_replan_fallback=lambda *_args: None,
            native_station_state=lambda _station: {"running": True},
            station_runtime_context=station_context,
            log_exception=lambda _station, exc: (_ for _ in ()).throw(exc),
        )
        return AutoDJService(deps), queues, gate_enabled, first_read, release_first, second_read, station_context

    def test_manual_url_and_repeated_next_stay_at_three_with_simultaneous_refills(self):
        service, queues, gate_enabled, first_read, release_first, second_read, context = self.make_service(
            gate_first_station_a_count=True
        )
        queue = queues["station-a.db"]
        # The manually queued URL counts just like any other queued item.
        queue.insert(0, {"track_id": 99, "source": "url"})
        self.assertEqual(len(queue), 4)
        queue.pop(0)  # URL begins playing.
        with context("station-a.db"):
            self.assertFalse(service.fill_queue_once(replan_after_fill=False))
        self.assertEqual(len(queue), 3)

        gate_enabled.set()
        queue.pop(0)  # Manual Next starts one queued song.
        self.assertEqual(len(queue), 2)
        results = []
        errors = []

        def concurrent_fill():
            try:
                with context("station-a.db"):
                    results.append(service.fill_queue_once(replan_after_fill=False))
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=concurrent_fill)
        second = threading.Thread(target=concurrent_fill)
        first.start()
        self.assertTrue(first_read.wait(2.0), "First refill never read the queue")
        second.start()
        first.join(timeout=3.0)
        second.join(timeout=3.0)
        release_first.set()
        self.assertFalse(first.is_alive() or second.is_alive(), "Concurrent refills deadlocked")
        self.assertFalse(errors, errors)
        self.assertEqual(sum(bool(result) for result in results), 1)
        self.assertEqual(len(queue), 3, "Simultaneous Next and worker refill must not add a fourth song")
        self.assertFalse(second_read.is_set() and len(queue) > 3)

        # Repeat the exact manual Next/dequeue/refill sequence; no 4/3/4/3 oscillation.
        for _ in range(8):
            queue.pop(0)
            self.assertEqual(len(queue), 2)
            with context("station-a.db"):
                self.assertTrue(service.fill_queue_once(replan_after_fill=False))
                self.assertFalse(service.fill_queue_once(replan_after_fill=False))
            self.assertEqual(len(queue), 3)

    def test_station_b_refills_while_station_a_fill_is_waiting(self):
        service, queues, gate_enabled, first_read, release_first, _second_read, context = self.make_service(
            gate_first_station_a_count=True
        )
        gate_enabled.set()
        queues["station-a.db"].pop(0)
        errors = []

        def fill_a():
            try:
                with context("station-a.db"):
                    service.fill_queue_once(replan_after_fill=False)
            except BaseException as exc:
                errors.append(exc)

        a = threading.Thread(target=fill_a)
        a.start()
        try:
            self.assertTrue(first_read.wait(2.0))
            with context("station-b.db"):
                self.assertTrue(service.fill_queue_once(replan_after_fill=False))
            self.assertEqual(len(queues["station-b.db"]), 3)
            self.assertFalse(release_first.is_set(), "Station B must not wait for station A's fill lock")
        finally:
            release_first.set()
            a.join(timeout=3.0)
        self.assertFalse(a.is_alive())
        self.assertFalse(errors, errors)
        self.assertEqual(len(queues["station-a.db"]), 3)
        self.assertEqual(len(queues["station-b.db"]), 3)


if __name__ == "__main__":
    unittest.main()
