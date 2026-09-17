from __future__ import annotations

import ast
import queue
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path


class V6058ScriptTickRecoveryTests(unittest.TestCase):
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

    def test_busy_station_defers_ticks_instead_of_dropping_them(self) -> None:
        dispatch_source = self._function_source("_script_engine_dispatch_station")
        worker_source = self._function_source("_script_engine_station_worker_loop")
        self.assertIn("_SCRIPT_ENGINE_STATION_DEFERRED_LATEST", dispatch_source)
        self.assertIn("return True", dispatch_source)
        self.assertIn("timedelta(seconds=1)", worker_source)
        self.assertIn("_SCRIPT_ENGINE_STATION_DEFERRED_MAX_SECONDS", worker_source)

    def test_busy_worker_replays_the_exact_595940_tick(self) -> None:
        namespace = {
            "_console_queue": queue,
            "threading": threading,
            "timedelta": timedelta,
            "_SCRIPT_ENGINE_STATION_WORKERS_LOCK": threading.Lock(),
            "_SCRIPT_ENGINE_STATION_WORKER_QUEUES": {},
            "_SCRIPT_ENGINE_STATION_WORKER_THREADS": {},
            "_SCRIPT_ENGINE_STATION_WORK_PENDING": set(),
            "_SCRIPT_ENGINE_STATION_DEFERRED_LATEST": {},
            "_SCRIPT_ENGINE_STATION_DEFERRED_MAX_SECONDS": 60,
            "_script_engine_debug_warning": lambda *args, **kwargs: None,
        }
        first_started = threading.Event()
        release_first = threading.Event()
        processed: list[datetime] = []
        processed_lock = threading.Lock()

        def process_station(_station_key, now_dt):
            with processed_lock:
                processed.append(now_dt)
                first = len(processed) == 1
            if first:
                first_started.set()
                release_first.wait(timeout=2.0)

        namespace["_script_engine_process_station"] = process_station
        for name in ("_script_engine_station_worker_loop", "_script_engine_dispatch_station"):
            node = self.functions[name]
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            exec(compile(module, str(self.app_path), "exec"), namespace)

        base = datetime(2026, 9, 5, 19, 59, 39)
        self.assertTrue(namespace["_script_engine_dispatch_station"]("db-Test.db", base))
        self.assertTrue(first_started.wait(timeout=0.5))

        self.assertTrue(namespace["_script_engine_dispatch_station"]("db-Test.db", base + timedelta(seconds=1)))
        self.assertTrue(namespace["_script_engine_dispatch_station"]("db-Test.db", base + timedelta(seconds=2)))
        self.assertTrue(namespace["_script_engine_dispatch_station"]("db-Test.db", base + timedelta(seconds=3)))
        release_first.set()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with processed_lock:
                snapshot = list(processed)
            if base + timedelta(seconds=3) in snapshot:
                break
            time.sleep(0.01)

        with processed_lock:
            snapshot = list(processed)
        self.assertEqual(
            snapshot[:4],
            [base + timedelta(seconds=offset) for offset in range(4)],
        )
        self.assertIn(base + timedelta(seconds=1), snapshot)

    def test_feature_remains_present_in_current_release(self) -> None:
        import re

        app_match = re.search(r'^APP_VERSION = "([0-9]+)"$', self.source, re.MULTILINE)
        native_header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        native_match = re.search(
            r'^#define WB_NATIVE_DAEMON_VERSION "([0-9]+)"$',
            native_header,
            re.MULTILINE,
        )
        self.assertIsNotNone(app_match)
        self.assertIsNotNone(native_match)
        self.assertEqual(app_match.group(1), native_match.group(1))
        self.assertGreaterEqual(int(app_match.group(1)), 6058)


if __name__ == "__main__":
    unittest.main()
