from __future__ import annotations

import contextlib
import unittest
from dataclasses import replace

from autodj import AutoDJDependencies, AutoDJService


class V6063NoRulesSingletonRotationTests(unittest.TestCase):
    def make_service(self):
        state = {
            "queue": [],
            "cursor": 0,
            "next_music": 100,
            "running": True,
        }

        @contextlib.contextmanager
        def station_context(_station):
            yield

        music_tracks = [
            {"track_id": track_id, "artist": f"Artist {track_id}", "title": f"Song {track_id}"}
            for track_id in range(100, 160)
        ]
        id_tracks = [{"track_id": 1, "artist": "", "title": "Ezek az igazi zenek"}]

        def enqueue(items):
            for item in items:
                track_id = int(item["track_id"])
                if track_id == 1:
                    artist, title = "", "Ezek az igazi zenek"
                else:
                    artist, title = f"Artist {track_id}", f"Song {track_id}"
                state["queue"].append(
                    {
                        "track_id": track_id,
                        "artist": artist,
                        "title": title,
                        "category_id": int(item["category_id"]),
                        "rotation_index": int(item["rotation_index"]),
                    }
                )

        deps = AutoDJDependencies(
            get_active_station_key=lambda: "db-Tel_Star.db",
            get_settings=lambda: {
                "keep_queue": 6,
                "no_repeat_artist_minutes": 60,
                "no_repeat_title_minutes": 60,
                "no_repeat_track_minutes": 60,
            },
            get_rotation=lambda: [
                {"category_id": 10, "norules": 0},
                {"category_id": 10, "norules": 0},
                {"category_id": 10, "norules": 0},
                {"category_id": 20, "norules": 1},
            ],
            get_rotation_cursor=lambda _rotation, _signature: state["cursor"],
            set_rotation_cursor=lambda index, _signature: state.__setitem__("cursor", index),
            get_queue_snapshot=lambda: list(state["queue"]),
            get_recent_history_snapshot=lambda *_args: [],
            get_category_tracks=lambda category_id: list(
                id_tracks if int(category_id) == 20 else music_tracks
            ),
            enqueue_track_items=enqueue,
            queue_count=lambda: len(state["queue"]),
            get_now_playing=lambda: {},
            publish_startup_event=lambda *_args, **_kwargs: None,
            replan_after_fill=lambda *_args, **_kwargs: None,
            schedule_replan_fallback=lambda *_args, **_kwargs: None,
            native_station_state=lambda _station: {"running": state["running"]},
            station_runtime_context=station_context,
            log_exception=lambda _station, exc: (_ for _ in ()).throw(exc),
        )
        return state, AutoDJService(deps)

    def test_singleton_norules_id_keeps_every_fourth_rotation_slot(self):
        state, service = self.make_service()
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))
        self.assertEqual(
            [row["rotation_index"] for row in state["queue"]],
            [0, 1, 2, 3, 0, 1],
        )

        # Consume two songs. The next fill reaches rotation slot 3 while the
        # previous copy of the singleton ID is still present in lookahead.
        state["queue"].pop(0)
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))
        state["queue"].pop(0)
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))

        self.assertEqual(state["queue"][-1]["track_id"], 1)
        self.assertEqual(state["queue"][-1]["rotation_index"], 3)
        self.assertEqual(sum(1 for row in state["queue"] if row["track_id"] == 1), 2)


    def test_initial_lookahead_can_queue_the_same_singleton_id_twice(self):
        state, service = self.make_service()
        service._deps = replace(
            service._deps,
            get_settings=lambda: {
                "keep_queue": 10,
                "no_repeat_artist_minutes": 60,
                "no_repeat_title_minutes": 60,
                "no_repeat_track_minutes": 60,
            },
        )
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))
        self.assertEqual(
            [row["rotation_index"] for row in state["queue"]],
            [0, 1, 2, 3, 0, 1, 2, 3, 0, 1],
        )
        self.assertEqual(
            [index for index, row in enumerate(state["queue"]) if row["track_id"] == 1],
            [3, 7],
        )

    def test_norules_still_prefers_a_distinct_candidate_when_available(self):
        state, service = self.make_service()
        service._deps = replace(
            service._deps,
            get_settings=lambda: {"keep_queue": 2},
            get_rotation=lambda: [{"category_id": 20, "norules": 1}],
            get_category_tracks=lambda _category_id: [
                {"track_id": 1, "artist": "", "title": "ID 1"},
                {"track_id": 2, "artist": "", "title": "ID 2"},
            ],
        )
        state["queue"].append({"track_id": 1, "artist": "", "title": "ID 1"})
        self.assertTrue(service.fill_queue_once(replan_after_fill=False))
        self.assertEqual(state["queue"][-1]["track_id"], 2)


if __name__ == "__main__":
    unittest.main()
