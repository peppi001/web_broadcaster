from __future__ import annotations

import contextlib
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from autodj import AutoDJDependencies, AutoDJService


class AutoDJServiceTests(unittest.TestCase):
    def make_dependencies(self, **overrides):
        state = {
            "queue": [],
            "history": [],
            "category_tracks": {1: [{"track_id": 10, "artist": "Artist", "title": "Title"}]},
            "cursor": 0,
            "events": [],
            "replans": [],
            "running": True,
        }

        @contextlib.contextmanager
        def station_context(_station):
            yield

        deps = AutoDJDependencies(
            get_active_station_key=lambda: "station.db",
            get_settings=lambda: {
                "keep_queue": 1,
                "no_repeat_artist_minutes": 60,
                "no_repeat_title_minutes": 60,
                "no_repeat_track_minutes": 60,
            },
            get_rotation=lambda: [{"category_id": 1, "norules": 0}],
            get_rotation_cursor=lambda _rotation, _signature: state["cursor"],
            set_rotation_cursor=lambda index, _signature: state.__setitem__("cursor", index),
            get_queue_snapshot=lambda: list(state["queue"]),
            get_recent_history_snapshot=lambda *_cutoffs: list(state["history"]),
            get_category_tracks=lambda category_id: list(state["category_tracks"].get(category_id, [])),
            enqueue_track_items=lambda items: state["queue"].extend(
                {"track_id": item["track_id"], "artist": "Artist", "title": "Title"}
                for item in items
            ),
            queue_count=lambda: len(state["queue"]),
            get_now_playing=lambda: {},
            publish_startup_event=lambda event, station, fields: state["events"].append(
                (event, station, dict(fields))
            ),
            replan_after_fill=lambda reason, async_replan: state["replans"].append(
                (reason, async_replan)
            ),
            schedule_replan_fallback=lambda reason: state["replans"].append((reason, None)),
            native_station_state=lambda _station: {"running": state["running"]},
            station_runtime_context=station_context,
            log_exception=lambda _station, exc: (_ for _ in ()).throw(exc),
        )
        for key, value in overrides.items():
            deps = replace(deps, **{key: value})
        return state, deps

    def test_empty_queue_fills_to_keep_queue_and_replans(self):
        state, deps = self.make_dependencies(
            get_settings=lambda: {"keep_queue": 2},
            get_rotation=lambda: [{"category_id": 1}, {"category_id": 2}],
            get_category_tracks=lambda category_id: [
                {"track_id": 10 + category_id, "artist": f"Artist {category_id}", "title": f"Title {category_id}"}
            ],
        )
        service = AutoDJService(deps)
        self.assertTrue(service.fill_queue_once())
        self.assertEqual([row["track_id"] for row in state["queue"]], [11, 12])
        self.assertEqual(state["replans"], [("autodj_refill", True)])
        self.assertEqual(state["cursor"], 2)

    def test_startup_fill_emits_requested_and_completed_events(self):
        state, deps = self.make_dependencies()
        service = AutoDJService(deps)
        result = service.startup_fill_once("station.db")
        self.assertTrue(result["success"])
        self.assertEqual(result["added_count"], 1)
        self.assertEqual([event for event, _station, _fields in state["events"]], [
            "autodj_startup_fill_requested",
            "autodj_startup_fill_completed",
        ])

    def test_empty_rotation_reports_precise_notice(self):
        state, deps = self.make_dependencies(get_rotation=lambda: [])
        service = AutoDJService(deps)
        result = service.startup_fill_once("station.db")
        self.assertFalse(result["success"])
        self.assertEqual(result["notice_code"], "rotation_empty")
        self.assertEqual(state["events"][-1][0], "autodj_startup_fill_failed")

    def test_keep_queue_zero_reports_disabled(self):
        _state, deps = self.make_dependencies(get_settings=lambda: {"keep_queue": 0})
        service = AutoDJService(deps)
        result = service.startup_fill_once("station.db")
        self.assertFalse(result["success"])
        self.assertEqual(result["notice_code"], "keep_queue_disabled")

    def test_repeat_rules_exclude_queue_and_recent_history(self):
        state, deps = self.make_dependencies(
            get_settings=lambda: {
                "keep_queue": 2,
                "no_repeat_artist_minutes": 60,
                "no_repeat_title_minutes": 60,
                "no_repeat_track_minutes": 60,
            },
            get_category_tracks=lambda _category: [
                {"track_id": 10, "artist": "Same", "title": "Old"},
                {"track_id": 11, "artist": "Fresh", "title": "Fresh"},
            ],
        )
        state["queue"].append({"track_id": 9, "artist": "Same", "title": "Queued"})
        service = AutoDJService(deps)
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))
        self.assertEqual(state["queue"][-1]["track_id"], 11)

    def test_worker_is_station_scoped_and_singleton(self):
        state, deps = self.make_dependencies()
        service = AutoDJService(deps)
        entered = threading.Event()
        release = threading.Event()

        def native_state(_station):
            entered.set()
            if not release.is_set():
                return {"running": True}
            return {"running": False}

        service._deps = replace(deps, native_station_state=native_state)  # controlled test seam
        service.start_thread("station.db")
        self.assertTrue(entered.wait(2.0))
        first_threads = list(service._threads.values())
        service.start_thread("station.db")
        self.assertEqual(first_threads, list(service._threads.values()))
        release.set()
        service.wake()
        deadline = time.monotonic() + 3.0
        while service.worker_alive("station.db") and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(service.worker_alive("station.db"))

    def test_app_owns_only_repository_and_facade_not_autodj_worker_state(self):
        root = Path(__file__).resolve().parents[1]
        app_source = (root / "app.py").read_text(encoding="utf-8")
        service_source = (root / "autodj" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("AUTODJ_THREADS", app_source)
        self.assertNotIn("AUTODJ_STATE", app_source)
        self.assertNotIn("AUTO_DJ_NOTICE_BY_STATION", app_source)
        self.assertIn("class AutoDJService", service_source)
        self.assertIn("self._threads", service_source)
        self.assertIn("self._notices", service_source)
        self.assertNotIn("self._rotation_index", service_source)


if __name__ == "__main__":
    unittest.main()
