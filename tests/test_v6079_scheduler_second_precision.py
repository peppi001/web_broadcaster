from __future__ import annotations

import ast
from datetime import datetime, timedelta
import re
import shutil
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path


class V6079SchedulerSecondPrecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(cls.app_source)
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {
            "_parse_time_hhmm", "compute_next_run_at"
        }]
        namespace = {
            "re": re,
            "datetime": datetime,
            "_dt": datetime,
            "timedelta": timedelta,
            "_WEEKDAYS": dict(zip(
                ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"),
                range(7),
            )),
        }
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
                     str(cls.root / "app.py"), "exec"), namespace)
        cls.parse_time = staticmethod(namespace["_parse_time_hhmm"])
        cls.compute = staticmethod(namespace["compute_next_run_at"])

    def test_clock_parser_accepts_seconds_and_legacy_minutes(self) -> None:
        self.assertEqual(self.parse_time("20:00:15"), (20, 0, 15))
        self.assertEqual(self.parse_time("20:00"), (20, 0, 0))
        self.assertEqual(self.parse_time("0:00:01"), (0, 0, 1))
        for value in ("20:00:60", "24:00:00", "20:60:00", "20:00:0", "20:00:15:04"):
            with self.subTest(value=value):
                self.assertIsNone(self.parse_time(value))

    def test_one_shot_second_and_legacy_schedule(self) -> None:
        now = datetime(2026, 9, 19, 19, 59, 59)
        self.assertEqual(self.compute("2026-09-19 20:00:15", now), "2026-09-19T20:00:15")
        self.assertEqual(self.compute("2026-09-19 20:00", now), "2026-09-19T20:00:00")
        self.assertIsNone(self.compute("2026-09-19 20:00:15", datetime(2026, 9, 19, 20, 0, 15)))
        self.assertIsNone(self.compute("2026-09-19 20:00:60", now))

    def test_everyday_preserves_seconds_across_days(self) -> None:
        self.assertEqual(self.compute("Everyday 20:00:15", datetime(2026, 9, 19, 20, 0, 14)),
                         "2026-09-19T20:00:15")
        self.assertEqual(self.compute("Everyday 20:00:15", datetime(2026, 9, 19, 20, 0, 15)),
                         "2026-09-20T20:00:15")
        self.assertEqual(self.compute("Everyday 20:00", datetime(2026, 9, 19, 19, 59, 59)),
                         "2026-09-19T20:00:00")
        self.assertIsNone(self.compute("Everyday 20:00:99", datetime(2026, 9, 19)))

    def test_weekday_preserves_seconds_across_weeks(self) -> None:
        monday = datetime(2026, 9, 21, 20, 0, 14)
        self.assertEqual(self.compute("Monday 20:00:15", monday), "2026-09-21T20:00:15")
        self.assertEqual(self.compute("Monday 20:00:15", monday + timedelta(seconds=1)),
                         "2026-09-28T20:00:15")
        self.assertEqual(self.compute("Monday 20:00", monday.replace(second=0) - timedelta(seconds=1)),
                         "2026-09-21T20:00:00")
        self.assertIsNone(self.compute("Monday 20:00:99", monday))

    def test_due_query_does_not_fire_before_target_second(self) -> None:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("CREATE TABLE scheduler_rules (id INTEGER, next_run_at TEXT, is_enabled INTEGER)")
            conn.execute("INSERT INTO scheduler_rules VALUES (1, ?, 1)",
                         (self.compute("Everyday 20:00:15", datetime(2026, 9, 19, 19, 59, 59)),))
            def due(at: str) -> list[int]:
                return [row[0] for row in conn.execute(
                    "SELECT id FROM scheduler_rules WHERE is_enabled = 1 "
                    "AND next_run_at IS NOT NULL AND next_run_at <= ?",
                    (at,),
                )]
            self.assertEqual(due("2026-09-19T20:00:14"), [])
            self.assertEqual(due("2026-09-19T20:00:15"), [1])
            self.assertEqual(due("2026-09-19T20:00:16"), [1])
        self.assertIn("_SCHEDULER_POLL_SECONDS = 1.0", self.app_source)
        self.assertIn('next_run_at <= ? ORDER BY next_run_at ASC, id ASC', self.app_source)

    def test_second_precision_editor_and_browser_countdown(self) -> None:
        html = (self.root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        self.assertIn('id="run_time" step="1"', html)
        self.assertIn("filename='scheduler.js', v=app_version", html)
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js not installed")
        result = subprocess.run([node, str(self.root / "tests" / "js" / "scheduler_second_precision.test.js")],
                                check=False, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Scheduler second precision JS tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
