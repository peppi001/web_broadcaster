"""Regression coverage for audible upstream URL metadata in Studio and Dashboard."""
from __future__ import annotations

import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace


class V6081UiStreamMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(__file__).resolve().parents[1] / "app.py"
        cls.functions = {
            node.name: node for node in ast.parse(cls.source.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef)
        }

    def namespace(self):
        ns = {
            "split_combined_artist_title": lambda value, artist: (
                (value.split(" - ", 1)[1], value.split(" - ", 1)[0])
                if " - " in value and not artist else (value, artist)
            ),
        }
        for name in ("_native_active_url_metadata", "_native_api_status_payload"):
            node = self.functions[name]
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(self.source), "exec"), ns)
        ns.update({
            "get_autodj_notice": lambda _sk: None,
            "_native_status_line_for_state": lambda _sk, _state: "stream",
            "_ab_line_info": lambda _line: {"title": "Streaming", "artist": "", "file": "https://radio.example/live.ogg", "queue_id": 101},
            "_get_now_playing_store": lambda _sk: {"title": "Streaming", "artist": "", "file": "https://radio.example/live.ogg"},
            "NOW_PLAYING_LOCK": threading.RLock(),
            "normalize_media_path": lambda value: value,
            "_normalize_year_metadata": lambda value: str(value or ""),
            "read_media_metadata": lambda _path: {},
            "guess_metadata_from_filename": lambda _path: {},
            "format_seconds": lambda value: str(int(value)),
        })
        return ns

    @staticmethod
    def state(*, title="The Artists - Real Song", path="https://radio.example/live.ogg", queue=101, token="active-101", enabled=True):
        return {
            "running": True, "active_deck": "A", "queue_id": queue, "slot_token": token,
            "native_audio_probe_path": path,
            "icecast_state": {"enabled": enabled, "outputs": [{
                "enabled": enabled, "queue_id": 101, "slot_token": "active-101", "metadata_value": title,
            }]},
        }

    def test_studio_uses_source_title_and_artist(self):
        ns = self.namespace()
        song = ns["_native_api_status_payload"]("db-A.db", self.state())["song"]
        self.assertEqual((song["artist"], song["title"]), ("The Artists", "Real Song"))
        self.assertTrue(song["upstream_stream_metadata"])

    def test_dynamic_title_change_updates_without_new_track(self):
        ns = self.namespace()
        one = ns["_native_api_status_payload"]("db-A.db", self.state(title="First Artist - First Song"))["song"]
        two = ns["_native_api_status_payload"]("db-A.db", self.state(title="Second Artist - Second Song"))["song"]
        self.assertEqual((one["title"], two["title"]), ("First Song", "Second Song"))
        self.assertEqual(one["queue_id"], two["queue_id"])

    def test_preloaded_or_replaced_url_metadata_does_not_leak(self):
        ns = self.namespace()
        for state in (self.state(queue=202), self.state(token="preload-other")):
            with self.subTest(state=state):
                song = ns["_native_api_status_payload"]("db-A.db", state)["song"]
                self.assertEqual(song["title"], "Streaming")
                self.assertFalse(song["upstream_stream_metadata"])

    def test_local_file_does_not_inherit_url_metadata(self):
        ns = self.namespace()
        ns["_ab_line_info"] = lambda _line: {"title": "Local Song", "artist": "Local Artist", "file": "/music/local.mp3"}
        state = self.state(path="/music/local.mp3")
        song = ns["_native_api_status_payload"]("db-A.db", state)["song"]
        self.assertEqual((song["artist"], song["title"]), ("Local Artist", "Local Song"))
        self.assertFalse(song["upstream_stream_metadata"])

    def test_no_upstream_title_keeps_streaming_fallback(self):
        ns = self.namespace()
        for title in ("", "Streaming"):
            with self.subTest(title=title):
                song = ns["_native_api_status_payload"]("db-A.db", self.state(title=title))["song"]
                self.assertEqual(song["title"], "Streaming")
                self.assertFalse(song["upstream_stream_metadata"])

    def test_disabled_or_missing_encoder_is_not_a_metadata_source(self):
        ns = self.namespace()
        for state in (self.state(enabled=False), {**self.state(), "icecast_state": {}}):
            with self.subTest(state=state):
                self.assertFalse(ns["_native_api_status_payload"]("db-A.db", state)["song"]["upstream_stream_metadata"])

    def test_station_overview_uses_same_upstream_title_as_studio(self):
        ns = self.namespace()
        node = self.functions["api_dashboard_overview"]
        # Flask decorators are not needed in this isolated dashboard contract test.
        node.decorator_list = []
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.source), "exec"), ns)
        state_a = self.state()
        state_b = self.state(path="/music/other.mp3", queue=202, token="local-202")
        ns.update({
            "session": {"user_id": 42},
            "jsonify": lambda data: data,
            "build_station_list": lambda: [
                {"db_filename": "db-A.db", "name": "Station A"},
                {"db_filename": "db-B.db", "name": "Station B"},
            ],
            "_native_station_state": lambda sk: state_a if sk == "db-A.db" else state_b,
            "get_audio_engine_started_at_for_station": lambda sk: None,
            "get_total_cpu_usage_percent": lambda _seconds: 0.0,
            "parse_embedded_querystring_meta": lambda title, artist: (title, artist),
            "datetime": __import__("datetime").datetime,
        })
        # Even with another station's stale now-playing store, the currently
        # audible station must have the source title, and the other keeps its own.
        ns["_get_now_playing_store"] = lambda sk: {
            "title": "Streaming" if sk == "db-A.db" else "Other Station Song",
            "artist": "" if sk == "db-A.db" else "Other Station Artist",
            "file": "https://radio.example/live.ogg" if sk == "db-A.db" else "/music/other.mp3",
        }
        result = ns["api_dashboard_overview"]()
        self.assertTrue(result["success"], result)
        a, b = result["stations"]
        self.assertEqual((a["now_playing"]["artist"], a["now_playing"]["title"]), ("The Artists", "Real Song"))
        self.assertEqual((b["now_playing"]["artist"], b["now_playing"]["title"]),
                         ("Other Station Artist", "Other Station Song"))


if __name__ == "__main__":
    unittest.main()
