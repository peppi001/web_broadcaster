from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from audio_engine import NativeEngine
from audio_engine.protocol import JsonlProtocolLogger, ProtocolSessionContext


class NativeDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine" / "bin" / "web_broadcaster_engine"

    def test_periodic_resource_snapshot_and_manual_state(self) -> None:
        if not self.binary.exists() or not os.access(self.binary, os.X_OK):
            self.skipTest("native daemon binary is not available")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            socket_path = tmp_path / "engine.sock"
            log_path = tmp_path / "native.jsonl"
            environment = os.environ.copy()
            environment["WEB_BROADCASTER_DIAGNOSTIC_INTERVAL_MS"] = "100"
            process = subprocess.Popen(
                [str(self.binary), str(socket_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                env=environment,
            )
            logger = JsonlProtocolLogger(
                log_path,
                engine_name="native",
                session_context=ProtocolSessionContext(
                    session_id="diagnostics-test",
                    app_version="5050",
                    native_daemon_version="not_connected",
                ),
            )
            native: NativeEngine | None = None
            try:
                deadline = time.monotonic() + 5.0
                while not socket_path.exists() and time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertTrue(socket_path.exists())
                native = NativeEngine(
                    socket_path=str(socket_path),
                    request_timeout_sec=2.0,
                    reconnect_delay_sec=0.05,
                    protocol_logger=logger,
                )
                last_ready_error: Exception | None = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail(f"native daemon exited during startup with code {process.returncode}")
                    try:
                        native.ping()
                        last_ready_error = None
                        break
                    except Exception as exc:  # startup readiness only
                        last_ready_error = exc
                        time.sleep(0.02)
                if last_ready_error is not None:
                    self.fail(f"native daemon did not become protocol-ready: {last_ready_error}")
                state = native.get_diagnostics_state()
                for field in (
                    "runtime_ms",
                    "rss_kb",
                    "virtual_memory_kb",
                    "cpu_user_ms",
                    "cpu_system_ms",
                    "thread_count",
                    "fd_count",
                    "child_process_count",
                    "active_voice_count",
                    "candidate_count",
                    "deck_a_ring_high_water_bytes",
                    "deck_b_ring_high_water_bytes",
                    "icecast_deck_a_fifo_high_water_bytes",
                    "icecast_deck_b_fifo_high_water_bytes",
                    "max_rss_kb",
                    "max_thread_count",
                    "max_fd_count",
                ):
                    self.assertIn(field, state)
                self.assertGreater(int(state["rss_kb"]), 0)
                self.assertGreaterEqual(int(state["thread_count"]), 3)
                self.assertGreaterEqual(int(state["fd_count"]), 3)

                snapshot_deadline = time.monotonic() + 2.0
                snapshots: list[dict] = []
                while time.monotonic() < snapshot_deadline:
                    time.sleep(0.05)
                    if not log_path.exists():
                        continue
                    snapshots = []
                    for line in log_path.read_text(encoding="utf-8").splitlines():
                        record = json.loads(line)
                        if record.get("record_type") == "event" and record.get("event") == "native_resource_snapshot":
                            snapshots.append(record)
                    if snapshots:
                        break
                self.assertTrue(snapshots)
                payload = dict(snapshots[-1].get("payload") or {})
                self.assertEqual(payload.get("reason"), "periodic")
                self.assertGreater(int(payload.get("snapshot_count") or 0), 0)
                self.assertIn("child_pids", payload)

                before = int(native.get_diagnostics_state().get("snapshot_count") or 0)
                manual = native.emit_diagnostics_snapshot()
                self.assertGreaterEqual(int(manual.get("snapshot_count") or 0), before + 1)
            finally:
                if native is not None:
                    native.close()
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)


if __name__ == "__main__":
    unittest.main()
