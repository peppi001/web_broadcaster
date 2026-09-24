"""Regression tests for queue-scoped fixed URL metadata and native ownership."""
from __future__ import annotations

import ast
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tests import test_native_icecast_output as native_tests
from tests import test_v6080_upstream_stream_metadata as upstream_tests


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
FUNCTIONS = {node.name: node for node in ast.parse(SOURCE).body if isinstance(node, ast.FunctionDef)}


def isolated(name: str, namespace: dict):
    node = FUNCTIONS[name]
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(ROOT / "app.py"), "exec"), namespace)
    return namespace[name]


class CustomUrlMetadataTests(unittest.TestCase):
    def test_url_descriptor_custom_metadata_remains_separate_from_shared_url(self):
        ns = {"_build_annotate_uri": lambda meta, path: (meta, path)}
        build = isolated("_ab_build_native_stream_descriptor", ns)
        fixed, path = build("https://radio.example/live", 60, queue_id=11, custom_metadata="Tel*Star - Live Show")
        normal, other_path = build("https://radio.example/live", 60, queue_id=12)
        self.assertEqual(path, other_path)
        self.assertEqual(fixed["title"], "Tel*Star - Live Show")
        self.assertEqual(fixed["wb_custom_metadata"], "1")
        self.assertEqual(fixed["artist"], "")
        self.assertEqual(normal["title"], "Streaming")
        self.assertEqual(normal["wb_custom_metadata"], "0")

    def test_queue_custom_title_is_isolated_by_station_and_queue_id(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("station-a.db", "station-b.db")]
            for path in paths:
                with sqlite3.connect(path) as db:
                    db.executescript("""
                        CREATE TABLE queue_item_custom_metadata (queue_id INTEGER PRIMARY KEY, custom_metadata TEXT NOT NULL);
                        CREATE TABLE queue_items (id INTEGER PRIMARY KEY);
                        CREATE TRIGGER queue_item_custom_metadata_cleanup AFTER DELETE ON queue_items
                        BEGIN DELETE FROM queue_item_custom_metadata WHERE queue_id = OLD.id; END;
                        INSERT INTO queue_items (id) VALUES (101), (102);
                    """)
            path_by_station = {"A": paths[0], "B": paths[1]}
            ns = {"get_db_for_station": lambda station: sqlite3.connect(path_by_station[station])}
            save = isolated("_save_queue_url_custom_metadata", ns)
            save("A", [101], "My Fixed Programme")
            save("B", [101], "Another Station")
            with sqlite3.connect(paths[0]) as db:
                self.assertEqual(db.execute("SELECT custom_metadata FROM queue_item_custom_metadata WHERE queue_id=101").fetchone()[0], "My Fixed Programme")
                self.assertIsNone(db.execute("SELECT 1 FROM queue_item_custom_metadata WHERE queue_id=102").fetchone())
                db.execute("DELETE FROM queue_items WHERE id=101")
                self.assertIsNone(db.execute("SELECT 1 FROM queue_item_custom_metadata WHERE queue_id=101").fetchone())
            with sqlite3.connect(paths[1]) as db:
                self.assertEqual(db.execute("SELECT custom_metadata FROM queue_item_custom_metadata WHERE queue_id=101").fetchone()[0], "Another Station")

    def test_fixed_ui_title_wins_even_if_upstream_metadata_changes(self):
        ns = {"split_combined_artist_title": lambda value, _: (
            (value.split(" - ", 1)[1], value.split(" - ", 1)[0]) if " - " in value else (value, "")
        )}
        isolated("_native_active_url_metadata", ns)
        status = isolated("_native_api_status_payload", ns)
        ns.update({
            "get_autodj_notice": lambda _: None,
            "_native_status_line_for_state": lambda *_: "stream-line",
            "_ab_line_info": lambda _: {"queue_id": 101, "title": "Tel*Star - Live Show", "custom_metadata": True,
                                        "file": "https://radio.example/live", "artist": ""},
            "NOW_PLAYING_LOCK": threading.RLock(),
            "_get_now_playing_store": lambda _: {"title": "Streaming"},
            "normalize_media_path": lambda path: path,
            "_normalize_year_metadata": lambda value: str(value or ""),
            "read_media_metadata": lambda _: {},
            "guess_metadata_from_filename": lambda _: {},
            "format_seconds": lambda seconds: str(int(seconds)),
        })
        state = {"running": True, "queue_id": 101, "slot_token": "current-101", "active_deck": "A",
                 "native_audio_probe_path": "https://radio.example/live",
                 "icecast_state": {"outputs": [{"enabled": True, "queue_id": 101, "slot_token": "current-101",
                                                "metadata_value": "Changed Upstream - Wrong Title"}]}}
        song = status("A", state)["song"]
        self.assertEqual((song["artist"], song["title"]), ("Tel*Star", "Live Show"))
        self.assertTrue(song["custom_stream_metadata"])
        self.assertFalse(song["upstream_stream_metadata"])
        state["queue_id"] = 102
        song = status("A", state)["song"]
        self.assertFalse(song["custom_stream_metadata"], "fixed title cannot cross queue identity")


    def test_enqueue_saves_fixed_title_before_replan_and_preserves_queue_origin(self):
        calls = []
        class Repository:
            def remove_queue_items(self, ids, *, station_key):
                calls.append(("remove", station_key, ids))
        ns = {
            "_enqueue_track_ids_return_queue_ids_for_station": lambda station, ids, priority: [71],
            "_mark_queue_items_origin_for_station": lambda station, ids, origin: calls.append(("origin", station, ids, origin)) or True,
            "_save_queue_url_custom_metadata": lambda station, ids, title: calls.append(("metadata", station, ids, title)),
            "_get_playback_repository": lambda: Repository(),
            "wake_autodj_worker": lambda: calls.append(("wake",)),
            "station_runtime_context": lambda station: __import__("contextlib").nullcontext(),
            "_ab_schedule_async_replan": lambda reason: calls.append(("replan", reason)),
        }
        enqueue = isolated("_enqueue_track_ids_for_station", ns)
        self.assertTrue(enqueue("station-A", [42], "end", custom_metadata="A fixed title"))
        self.assertEqual(calls, [
            ("origin", "station-A", [71], "scheduler"),
            ("metadata", "station-A", [71], "A fixed title"),
            ("wake",),
            ("replan", "scheduler_enqueue"),
        ])
        calls.clear()
        self.assertTrue(enqueue("station-B", [42], "end"))
        self.assertFalse(any(event[0] == "metadata" for event in calls))

    def test_failed_custom_title_write_reverts_only_new_queue_item(self):
        calls = []
        class Repository:
            def remove_queue_items(self, ids, *, station_key):
                calls.append(("remove", station_key, ids))
        def fail_save(*_args):
            raise sqlite3.OperationalError("test persistence failure")
        ns = {
            "_enqueue_track_ids_return_queue_ids_for_station": lambda station, ids, priority: [91],
            "_mark_queue_items_origin_for_station": lambda station, ids, origin: True,
            "_save_queue_url_custom_metadata": fail_save,
            "_get_playback_repository": lambda: Repository(),
        }
        enqueue = isolated("_enqueue_track_ids_for_station", ns)
        self.assertFalse(enqueue("station-A", [42], "end", custom_metadata="Fixed"))
        self.assertEqual(calls, [("remove", "station-A", [91])])

    def test_queue_url_modal_resets_optional_field_and_sends_it_to_api(self):
        html = (ROOT / "html/broadcaster.html").read_text(encoding="utf-8")
        scheduler = (ROOT / "html/static/scheduler.js").read_text(encoding="utf-8")
        ui = (ROOT / "html/static/broadcaster.js").read_text(encoding="utf-8")
        self.assertIn('id="scheduler-url-custom-metadata"', html)
        self.assertIn('id="scheduler-url-custom-row"', html)
        self.assertIn('urlCustomInput.value = opts.defaultCustomMetadata ||', scheduler)
        self.assertIn('urlCustomRow.hidden = !opts.allowCustomMetadata', scheduler)
        self.assertIn('allowCustomMetadata: true', ui)
        self.assertIn('custom_metadata: result.custom_metadata', ui)


class NativeFixedMetadataTests(unittest.TestCase):
    def test_vorbis_source_cannot_override_fixed_icecast_title(self):
        if not (ROOT / "native_engine/bin/web_broadcaster_engine").is_file():
            self.skipTest("native engine binary required")
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.ogg"
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "sine=frequency=461:duration=60", "-ac", "2", "-ar", "48000",
                            "-codec:a", "libvorbis", "-b:a", "128k", "-metadata", "TITLE=Wrong Song",
                            "-metadata", "ARTIST=Wrong Artist", "-f", "ogg", "-y", str(source)],
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            upstream = upstream_tests._AudioHttpServer(source.read_bytes()).start()
            icecast = native_tests._MockIcecastSource()
            icecast.start()
            harness = native_tests.NativeIcecastOutputTests("test_metadata_reapply_and_encoder_kill_recovery")
            harness.binary = ROOT / "native_engine/bin/web_broadcaster_engine"
            try:
                with harness._daemon(station_key="fixed-metadata-test") as (native, _tmp):
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=icecast.port,
                        mount="/fixed-meta.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Fixed title test", public_stream=False,
                    )
                    native.start()
                    url = f"http://127.0.0.1:{upstream.server_address[1]}/radio"
                    token = "fixed-101"
                    descriptor = (f'annotate:queue_id="101",track_id="102",station_key="fixed-metadata-test",'
                                  f'wb_ab_slot_token="{token}",wb_stream_source="1",wb_stream_infinite="1",'
                                  f'wb_custom_metadata="1",wb_manual_timing="1",title="Tel*Star - Live Show":{url}')
                    self.assertTrue(native.load_deck("A", descriptor, clear_slot=True))
                    payload = {key: 0 for key in ("cue_in_ms", "cue_out_ms", "audio_start_ms", "play_start_ms",
                                                  "transition_at_ms", "effective_end_ms", "source_end_ms")}
                    def sync(event):
                        native.sync_live_event({"event": event, "station_key": "fixed-metadata-test",
                                                "queue_id": 101, "track_id": 102, "deck": "A", "slot_token": token,
                                                "path": url, "title": "Tel*Star - Live Show", "stream_source": True,
                                                "event_monotonic_time_ms": int(time.monotonic() * 1000),
                                                "event_wall_time_unix_ms": int(time.time() * 1000), "payload": payload})
                    sync("deck_loaded")
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        state = native.get_state()
                        if (state.get("native_audio_probe_prebuffer_ready") and
                                state.get("native_audio_probe_slot_token") == token):
                            break
                        time.sleep(.05)
                    else:
                        self.fail("URL prebuffer did not become ready")
                    sync("track_started")
                    expected = b"song=Tel%2AStar%20-%20Live%20Show"
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        if any(expected in request for request in icecast.metadata_requests):
                            break
                        time.sleep(.075)
                    self.assertTrue(any(expected in req for req in icecast.metadata_requests),
                                    icecast.metadata_requests)
                    time.sleep(1.6)
                    self.assertFalse(any(b"Wrong%20Song" in req for req in icecast.metadata_requests),
                                     icecast.metadata_requests)
                    self.assertEqual(upstream.connections, 1)
            finally:
                upstream.close()
                icecast.close()


if __name__ == "__main__":
    unittest.main()
