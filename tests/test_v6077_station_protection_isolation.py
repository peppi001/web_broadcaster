from __future__ import annotations

import ast
import contextlib
import threading
import unittest
from collections.abc import MutableMapping
from pathlib import Path


class _StationScopedFixture(MutableMapping):
    def __init__(self, current_station: dict[str, str], snapshots: dict[str, dict]):
        self.current_station = current_station
        self.snapshots = snapshots

    def _state(self) -> dict:
        return self.snapshots[self.current_station["value"]]

    def __getitem__(self, key):
        return self._state()[key]

    def __setitem__(self, key, value):
        self._state()[key] = value

    def __delitem__(self, key):
        del self._state()[key]

    def __iter__(self):
        return iter(self._state())

    def __len__(self):
        return len(self._state())


class V6077StationProtectionIsolationTests(unittest.TestCase):
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

    def _compile_function(self, name: str, namespace: dict) -> None:
        node = self.functions[name]
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, str(self.app_path), "exec"), namespace)

    def _namespace(self) -> dict:
        current_station = {"value": "db-A.db"}
        snapshots = {
            "db-A.db": {
                "lines": ["scheduler-stream|qid=101"],
                "player_index": {"a": 0},
                "current_index": 0,
            },
            "db-B.db": {
                "lines": ["normal-local|qid=202"],
                "player_index": {"a": 0},
                "current_index": 0,
            },
        }

        @contextlib.contextmanager
        def station_runtime_context(station_key: str):
            previous = current_station["value"]
            current_station["value"] = station_key
            try:
                yield station_key
            finally:
                current_station["value"] = previous

        def find_line(lines, *, path="", queue_id=0):
            token = f"qid={int(queue_id or 0)}"
            for index, line in enumerate(lines):
                if token in str(line):
                    return index
            return -1

        states = {
            "db-A.db": {
                "running": True,
                "active_deck": "A",
                "queue_id": 101,
                "native_audio_probe_path": "https://radio.example/live.ogg",
            },
            "db-B.db": {
                "running": True,
                "active_deck": "A",
                "queue_id": 202,
                "native_audio_probe_path": "/music/normal.mp3",
            },
        }

        def line_info(line: str) -> dict:
            if str(line).startswith("scheduler-stream"):
                return {"queue_origin": "scheduler", "stream_source": True}
            if str(line).startswith("normal-local"):
                return {"queue_origin": "", "stream_source": False}
            return {}

        namespace = {
            "station_runtime_context": station_runtime_context,
            "_AB_PLAYER_LOCK": threading.RLock(),
            "_AB_PLAYER_STATE": _StationScopedFixture(current_station, snapshots),
            "_ab_find_line_index_by_identity": find_line,
            "normalize_media_path": lambda value: str(value or ""),
            "_native_station_state": lambda station: dict(states[station]),
            "_ab_line_info": line_info,
            "NOW_PLAYING_LOCK": threading.RLock(),
            "_get_now_playing_store": lambda _station: {},
        }
        self._compile_function("_native_status_line_for_state", namespace)
        self._compile_function("_station_scheduler_playback_active", namespace)
        self._compile_function("_station_url_playback_active", namespace)
        return namespace

    def test_native_status_lookup_pins_requested_station_context(self) -> None:
        ns = self._namespace()
        state_b = ns["_native_station_state"]("db-B.db")
        line = ns["_native_status_line_for_state"]("db-B.db", state_b)
        self.assertEqual(line, "normal-local|qid=202")

    def test_scheduler_protection_does_not_leak_to_another_station(self) -> None:
        ns = self._namespace()
        self.assertTrue(ns["_station_scheduler_playback_active"]("db-A.db"))
        self.assertFalse(ns["_station_scheduler_playback_active"]("db-B.db"))

    def test_url_protection_does_not_leak_to_another_station(self) -> None:
        ns = self._namespace()
        self.assertTrue(ns["_station_url_playback_active"]("db-A.db"))
        self.assertFalse(ns["_station_url_playback_active"]("db-B.db"))


if __name__ == "__main__":
    unittest.main()
