import threading
import unittest
from pathlib import Path
from unittest import mock

from audio_engine import runtime


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (ROOT / "app.py").read_text(encoding="utf-8")
RUNTIME_SOURCE = (ROOT / "audio_engine" / "runtime.py").read_text(encoding="utf-8")


class _FakeEngine:
    def __init__(self):
        self.events = []
        self.synced = []
        self.closed = False

    def publish_event(self, event, **kwargs):
        record = {"event": event, **kwargs}
        self.events.append(record)
        return record

    def sync_live_event(self, event):
        self.synced.append(dict(event))
        return {"state": {"running": True, "position_ms": 1234}}

    def close(self):
        self.closed = True


class V5098EngineRuntimeExtractionTests(unittest.TestCase):
    def tearDown(self):
        runtime._reset_audio_engine_runtime_for_tests()

    def test_app_no_longer_owns_engine_singleton_or_station_thread_local(self):
        self.assertNotIn("_AUDIO_ENGINE_INSTANCE", APP_SOURCE)
        self.assertNotIn("_AUDIO_ENGINE_INSTANCE_LOCK", APP_SOURCE)
        self.assertNotIn("_STATION_CONTEXT_LOCAL", APP_SOURCE)
        self.assertNotIn("EngineLifecycleIdentityRegistry", APP_SOURCE)
        self.assertNotIn("create_audio_engine(", APP_SOURCE)
        self.assertIn("configure_audio_engine_runtime(", APP_SOURCE)
        self.assertIn("station_runtime_override as _station_runtime_override", APP_SOURCE)

    def test_runtime_module_owns_lazy_singleton(self):
        engine = _FakeEngine()
        with mock.patch.object(runtime, "create_audio_engine", return_value=engine) as factory:
            runtime.configure_audio_engine_runtime(
                app_version="5098",
                app_root=str(ROOT),
                station_key_resolver=lambda: "db-test.db",
            )
            first = runtime.get_audio_engine()
            second = runtime.get_audio_engine()
        self.assertIs(first, engine)
        self.assertIs(second, engine)
        factory.assert_called_once()
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["app_version"], "5098")
        self.assertTrue(kwargs["protocol_log_path"].endswith("logs/audio_engine_protocol.jsonl"))
        self.assertEqual(kwargs["station_key_resolver"](), "db-test.db")

    def test_station_context_is_nested_and_thread_scoped(self):
        self.assertEqual(runtime.station_runtime_override(), "")
        with runtime.station_runtime_context("db-one.db"):
            self.assertEqual(runtime.station_runtime_override(), "db-one.db")
            with runtime.station_runtime_context("db-two.db"):
                self.assertEqual(runtime.station_runtime_override(), "db-two.db")
            self.assertEqual(runtime.station_runtime_override(), "db-one.db")

            seen = []

            def worker():
                seen.append(runtime.station_runtime_override())
                with runtime.station_runtime_context("db-thread.db"):
                    seen.append(runtime.station_runtime_override())
                seen.append(runtime.station_runtime_override())

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=2.0)
            self.assertEqual(seen, ["", "db-thread.db", ""])
        self.assertEqual(runtime.station_runtime_override(), "")

    def test_seek_bridge_forwards_daemon_event_and_normalized_telemetry(self):
        engine = _FakeEngine()
        runtime._AUDIO_ENGINE_INSTANCE = engine
        state = runtime.publish_audio_engine_track_seeked(
            station_key="db-test.db",
            deck="b",
            identity={
                "queue_id": 41,
                "track_id": 9,
                "slot_token": "41-9-token-1",
                "path": "/music/test.mp3",
                "artist": "Artist",
                "title": "Title",
                "year": "released 2004",
                "cue_in": 1.25,
                "cue_out": 88.5,
                "audio_start": 0.5,
                "audio_end": 90.0,
                "orig_total": 91.0,
                "fade_in": 2.0,
                "fade_out": 3.0,
            },
            target_seconds=12.5,
            from_seconds=7.0,
        )
        self.assertEqual(state, {"running": True, "position_ms": 1234})
        self.assertEqual(len(engine.synced), 1)
        synced = engine.synced[0]
        self.assertEqual(synced["deck"], "B")
        self.assertEqual(synced["queue_id"], 41)
        self.assertEqual(synced["year"], "2004")
        self.assertEqual(synced["payload"]["seek_position_ms"], 12500)
        self.assertEqual(synced["payload"]["seek_from_position_ms"], 7000)
        self.assertEqual(len(engine.events), 1)
        self.assertEqual(engine.events[0]["event"], "track_seeked")
        self.assertEqual(engine.events[0]["slot_token"], "41-9-token-1")

    def test_runtime_module_documents_single_ownership_boundary(self):
        self.assertIn("Process-wide native audio-engine runtime ownership", RUNTIME_SOURCE)
        self.assertIn("daemon transport stays in", RUNTIME_SOURCE)


if __name__ == "__main__":
    unittest.main()
