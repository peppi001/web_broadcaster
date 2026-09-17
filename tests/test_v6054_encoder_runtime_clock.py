from __future__ import annotations

import ast
import contextlib
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


class V6054EncoderRuntimeClockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_path = cls.root / "app.py"
        cls.app_source = cls.app_path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.app_source)
        cls.functions = {
            node.name: node
            for node in cls.tree.body
            if isinstance(node, ast.FunctionDef)
        }

    def _source(self, name: str) -> str:
        return ast.get_source_segment(self.app_source, self.functions[name]) or ""

    def _load_function(self, name: str, namespace: dict[str, object]):
        module = ast.Module(body=[self.functions[name]], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace.setdefault("Any", Any)
        exec(compile(module, str(self.app_path), "exec"), namespace)
        return namespace[name]

    def test_real_encoder_start_clears_stale_clock_before_native_start(self) -> None:
        events: list[tuple[str, int]] = []
        stored: list[tuple[int, str]] = []
        namespace: dict[str, object] = {
            "datetime": datetime,
            "clear_encoder_started_at": lambda stream_id: events.append(("clear", int(stream_id))),
            "_encoder_action_native": lambda stream_id, action: (
                events.append((str(action), int(stream_id))) or {"ok": True}
            ),
            "set_encoder_started_at": lambda stream_id, value: stored.append((int(stream_id), str(value))),
        }
        start = self._load_function("_start_encoder_with_runtime_clock", namespace)

        result = start(7)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(events, [("clear", 7), ("start", 7)])
        self.assertEqual(stored[0][0], 7)
        datetime.fromisoformat(stored[0][1])

    def test_failed_encoder_start_keeps_runtime_clock_cleared(self) -> None:
        events: list[tuple[str, int]] = []
        stored: list[tuple[int, str]] = []

        def fail_start(stream_id: int, action: str):
            events.append((str(action), int(stream_id)))
            raise RuntimeError("start failed")

        namespace: dict[str, object] = {
            "datetime": datetime,
            "clear_encoder_started_at": lambda stream_id: events.append(("clear", int(stream_id))),
            "_encoder_action_native": fail_start,
            "set_encoder_started_at": lambda stream_id, value: stored.append((int(stream_id), str(value))),
        }
        start = self._load_function("_start_encoder_with_runtime_clock", namespace)

        with self.assertRaisesRegex(RuntimeError, "start failed"):
            start(8)

        self.assertEqual(events, [("clear", 8), ("start", 8)])
        self.assertEqual(stored, [])

    def test_all_true_on_air_start_paths_use_fresh_clock_helper(self) -> None:
        autostart = self._source("_start_encoder_if_autostart_on_air")
        configure = self._source("api_encoder_configure")
        manual = self._source("api_encoder_start")

        self.assertIn("_start_encoder_with_runtime_clock", autostart)
        self.assertIn("_start_encoder_with_runtime_clock", configure)
        self.assertIn("_start_encoder_with_runtime_clock", manual)
        self.assertNotIn("set_encoder_started_at", manual)

    def test_live_dsp_reconfigure_does_not_reset_encoder_clock(self) -> None:
        source = self._source("_apply_live_dsp_setting")
        self.assertIn('_encoder_action_native(stream_id, "start")', source)
        self.assertNotIn("_start_encoder_with_runtime_clock", source)
        self.assertNotIn("clear_encoder_started_at", source)

    def test_station_autostart_clears_old_clocks_before_configure(self) -> None:
        source = self._source("_load_native_output_runtime_configs")
        self.assertIn("clear_encoder_started_at", source)
        self.assertLess(source.index("clear_encoder_started_at"), source.index("_native_stream_config_from_row"))

    def test_successful_station_autostart_marks_enabled_encoder_clocks(self) -> None:
        timestamps: list[tuple[int, str]] = []
        progress: dict[str, dict[str, object]] = {}

        namespace: dict[str, object] = {
            "RADIO_STATE_LOCK": contextlib.nullcontext(),
            "RADIO_STATE": {},
            "PROGRESS_LOCK": contextlib.nullcontext(),
            "_get_progress_state": lambda station: progress.setdefault(station, {}),
            "_native_encoder_runtime_snapshot": lambda station: (
                station == "db-Test.db",
                {
                    "stream_2": {"enabled": True},
                    "stream_9": {"enabled": True},
                    "stream_4": {"enabled": False},
                    "legacy": {"enabled": True},
                },
            ),
            "datetime": datetime,
            "set_encoder_started_at": lambda stream_id, value: timestamps.append((int(stream_id), str(value))),
        }
        mark = self._load_function("_station_mark_runtime_started", namespace)

        mark("db-Test.db")

        self.assertEqual([item[0] for item in timestamps], [2, 9])
        self.assertEqual(timestamps[0][1], timestamps[1][1])
        datetime.fromisoformat(timestamps[0][1])

    def test_v6054_encoder_clock_regression_remains_in_current_release(self) -> None:
        import re

        app_match = re.search(r'^APP_VERSION = "([0-9]+)"$', self.app_source, re.MULTILINE)
        self.assertIsNotNone(app_match)
        self.assertGreaterEqual(int(app_match.group(1)), 6054)
        header = (self.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")
        native_match = re.search(r'^#define WB_NATIVE_DAEMON_VERSION "([0-9]+)"$', header, re.MULTILINE)
        self.assertIsNotNone(native_match)
        self.assertEqual(app_match.group(1), native_match.group(1))
        history = (self.root / "version.txt").read_text(encoding="utf-8")
        self.assertIn("v6054 - 2026-09-05", history)
        self.assertIn("encoder START lifecycle", history)


if __name__ == "__main__":
    unittest.main()
