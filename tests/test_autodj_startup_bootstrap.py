from __future__ import annotations

import ast
import contextlib
import os
import threading
import unittest

from station import StationService, StationServiceDependencies
from pathlib import Path


class AutoDjStartupBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.station_source = (cls.root / "station" / "service.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_empty_queue_is_filled_before_native_start(self) -> None:
        start_pos = self.station_source.index("def start(")
        body = self.station_source[start_pos:]
        fill_pos = body.index("startup_autodj_fill")
        rebuild_pos = body.index("build_queue_plan", fill_pos)
        output_pos = body.index("clear_icecast_output", rebuild_pos)
        engine_start_pos = body.index("engine.start", output_pos)
        self.assertLess(fill_pos, rebuild_pos)
        self.assertLess(rebuild_pos, output_pos)
        self.assertLess(output_pos, engine_start_pos)
        self.assertIn("autodj_startup_filled", body)

    def test_station_start_bootstraps_empty_queue_and_starts_engine(self) -> None:
        calls = []
        plans = iter([[], ["queue-line"]])
        native_states = iter([{"running": False}, {"running": True, "active_deck": "A"}])

        class Engine:
            def clear_icecast_output(self, output_id, *, station_key):
                calls.append(("clear_output", output_id, station_key))
            def configure_icecast_output(self, *, station_key, **config):
                calls.append(("configure_output", station_key, config))
            def start(self, *, station_key):
                calls.append(("engine_start", station_key))
                return {"running": True}
            def stop(self, *, station_key):
                calls.append(("engine_stop", station_key))

        @contextlib.contextmanager
        def runtime_context(station_key):
            calls.append(("context", station_key))
            yield

        deps = StationServiceDependencies(
            get_active_station_key=lambda: "station.db",
            get_engine=lambda: Engine(),
            station_runtime_context=runtime_context,
            native_station_state=lambda station_key: next(native_states),
            invalidate_status_cache=lambda: None,
            get_started_at=lambda station_key: None,
            set_started_at=lambda station_key: calls.append(("started_at", station_key)),
            clear_started_at=lambda station_key: None,
            prepare_start_state=lambda station_key: None,
            build_queue_plan=lambda station_key: next(plans),
            startup_autodj_fill=lambda station_key: calls.append(("autodj_fill", station_key)) or {"success": True},
            load_output_configs=lambda station_key: [],
            bootstrap_queue_plan=lambda lines, station_key: calls.append(("bootstrap", tuple(lines), station_key)) or True,
            start_autodj_worker=lambda station_key: calls.append(("autodj_thread", station_key)),
            notify_on_air=lambda station_key, reason: None,
            mark_runtime_started=lambda station_key: None,
            cleanup_failed_start=lambda station_key: None,
            prepare_stop_state=lambda station_key: {},
            restore_failed_stop_state=lambda station_key, context: None,
            finalize_stop_state=lambda station_key, context: None,
            stop_off_air_automation=lambda station_key: None,
            clear_now_playing=lambda station_key: None,
            mark_runtime_stopped=lambda station_key: None,
        )
        payload, status = StationService(deps).start()
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["autodj_startup_filled"])
        self.assertIn(("autodj_fill", "station.db"), calls)
        self.assertIn(("bootstrap", ("queue-line",), "station.db"), calls)
        self.assertIn(("engine_start", "station.db"), calls)
        self.assertLess(calls.index(("autodj_fill", "station.db")), calls.index(("engine_start", "station.db")))

    def test_startup_fill_wrapper_delegates_to_autodj_service(self) -> None:
        node = self.functions["_autodj_startup_fill_once"]
        calls = []

        class Service:
            def startup_fill_once(self, station_key):
                calls.append(station_key)
                return {"success": True, "queue_count_after": 3}

        namespace = {"_get_autodj_service": lambda: Service()}
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(self.root / "app.py"), "exec"),
            namespace,
        )
        result = namespace["_autodj_startup_fill_once"]("station.db")
        self.assertTrue(result["success"])
        self.assertEqual(calls, ["station.db"])

    def test_startup_trace_is_always_published_to_protocol_events(self) -> None:
        node = self.functions["_autodj_startup_trace"]
        published = []
        namespace = {
            "os": os,
            "get_active_station_key": lambda: "station.db",
            "_publish_audio_engine_event": lambda *args, **kwargs: published.append((args, kwargs)),
        }
        exec(
            compile(ast.Module(body=[node], type_ignores=[]), str(self.root / "app.py"), "exec"),
            namespace,
        )
        namespace["_autodj_startup_trace"](
            "autodj_startup_fill_requested",
            station_key="/tmp/station.db",
            queue_count_before=0,
            keep_queue=3,
        )
        self.assertEqual(len(published), 1)
        args, kwargs = published[0]
        self.assertEqual(args[0], "autodj_startup_fill_requested")
        self.assertEqual(kwargs["station_key"], "station.db")
        self.assertEqual(kwargs["payload"]["queue_count_before"], 0)
        self.assertEqual(kwargs["payload"]["keep_queue"], 3)


if __name__ == "__main__":
    unittest.main()
