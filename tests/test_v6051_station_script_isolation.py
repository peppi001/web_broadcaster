from __future__ import annotations

import ast
import queue
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path


class V6051StationScriptIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _function_source(self, name: str) -> str:
        return ast.get_source_segment(self.source, self.functions[name]) or ""

    def test_scheduler_uses_per_station_daemon_workers(self) -> None:
        self.assertIn("_SCRIPT_ENGINE_STATION_WORKER_QUEUES", self.source)
        self.assertIn("_SCRIPT_ENGINE_STATION_WORKER_THREADS", self.source)
        self.assertIn("_SCRIPT_ENGINE_STATION_LOCKS", self.source)
        self.assertIn("_SCRIPT_ENGINE_STATION_WORK_PENDING", self.source)
        dispatch = self._function_source("_script_engine_dispatch_station")
        self.assertIn("daemon=True", dispatch)
        self.assertIn("Queue(maxsize=1)", dispatch)
        self.assertIn("_SCRIPT_ENGINE_STATION_WORK_PENDING", dispatch)

    def test_due_occurrences_have_independent_in_flight_dedup(self) -> None:
        self.assertIn("_SCRIPT_ENGINE_IN_FLIGHT", self.source)
        process_source = self._function_source("_script_engine_process_station")
        self.assertIn("_script_engine_claim_occurrence", process_source)
        self.assertIn("_script_engine_release_occurrence", process_source)
        self.assertIn("_SCRIPT_ENGINE_LAST_RUN[(station_key, script_id)] = occurrence_key[2]", process_source)

    def test_one_station_dispatch_failure_cannot_stop_later_stations(self) -> None:
        source = self._function_source("script_engine_process_due_once")
        self.assertRegex(source, r"for station_key in station_keys:[\s\S]+?try:[\s\S]+?_script_engine_dispatch_station")
        self.assertIn("continue", source)
        self.assertIn("Station script scheduler dispatch failed", source)


    def test_slow_station_worker_does_not_block_another_station(self) -> None:
        namespace = {
            "_console_queue": queue,
            "threading": threading,
            "_SCRIPT_ENGINE_STATION_WORKERS_LOCK": threading.Lock(),
            "_SCRIPT_ENGINE_STATION_WORKER_QUEUES": {},
            "_SCRIPT_ENGINE_STATION_WORKER_THREADS": {},
            "_SCRIPT_ENGINE_STATION_WORK_PENDING": set(),
            "_SCRIPT_ENGINE_STATION_DEFERRED_LATEST": {},
            "_SCRIPT_ENGINE_STATION_DEFERRED_MAX_SECONDS": 60,
            "timedelta": timedelta,
            "_script_engine_debug_warning": lambda *args, **kwargs: None,
        }
        release_slow = threading.Event()
        fast_ran = threading.Event()

        def process_station(station_key, _now_dt):
            if station_key == "slow":
                release_slow.wait(timeout=2.0)
            elif station_key == "fast":
                fast_ran.set()

        namespace["_script_engine_process_station"] = process_station
        for name in ("_script_engine_station_worker_loop", "_script_engine_dispatch_station"):
            node = self.functions[name]
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(self.app_path), "exec"), namespace)

        now_dt = datetime(2026, 9, 4, 20, 59, 40)
        self.assertTrue(namespace["_script_engine_dispatch_station"]("slow", now_dt))
        self.assertTrue(namespace["_script_engine_dispatch_station"]("fast", now_dt))
        try:
            self.assertTrue(fast_ran.wait(timeout=0.5))
        finally:
            release_slow.set()

    def test_new_script_diagnostics_are_debug_gated(self) -> None:
        helper = self._function_source("_script_engine_debug_warning")
        self.assertIn("if not _RUNTIME_LOGGING_ENABLED", helper)
        self.assertIn("logger.warning", helper)
        for message in (
            "Scheduled script execution failed",
            "Station script worker iteration failed",
            "Station script scheduler dispatch failed",
            "Script engine scheduler iteration failed",
        ):
            self.assertEqual(self.source.count(message), 1)
        self.assertNotIn("logging.debug(", self.source)

    def test_global_scheduler_lock_only_wraps_dispatch(self) -> None:
        source = self._function_source("script_engine_process_due_once")
        self.assertIn("_script_engine_dispatch_station(station_key, now_dt)", source)
        self.assertNotIn("_run_station_script_once", source)


if __name__ == "__main__":
    unittest.main()
