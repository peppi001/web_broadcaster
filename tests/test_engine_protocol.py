from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audio_engine.events import EngineEventBus
from audio_engine.protocol import (
    JsonlProtocolLogger,
    ProtocolSessionContext,
    extract_annotated_identity,
)


class EngineProtocolTests(unittest.TestCase):
    def test_annotated_http_url_preserves_scheme_and_port(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="8",station_key="db-Radio.db",'
            'webradio_url="https://radio.example:8443/live":'
            'https://radio.example:8443/live'
        )
        self.assertEqual(identity["path"], "https://radio.example:8443/live")
        self.assertEqual(identity["queue_id"], 8)
        self.assertTrue(identity["stream_source"])

    def test_native_stream_descriptor_fields_are_parsed(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="18",track_id="28",station_key="db-Radio.db",'
            'wb_source_type="stream",wb_stream_source="1",'
            'wb_stream_infinite="0",wb_stream_duration="60":'
            'http://127.0.0.1:8088/live.mp3'
        )
        self.assertEqual(identity["path"], "http://127.0.0.1:8088/live.mp3")
        self.assertTrue(identity["stream_source"])
        self.assertFalse(identity["stream_infinite"])
        self.assertEqual(identity["stream_duration_ms"], 60000)

    def test_command_reply_and_event_are_jsonl_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.jsonl"
            logger = JsonlProtocolLogger(path, engine_name="native")
            bus = EngineEventBus(engine_name="native", protocol_logger=logger)
            command_id = logger.command("start", station_key="station-1")
            logger.reply(command_id, ok=True, result={"running": True})
            event = bus.publish(
                "track_started",
                station_key="station-1",
                queue_id=18237,
                slot_token="A-94721",
                deck="a",
                track_id=17,
                path="/music/example.mp3",
            )
            self.assertEqual(event.deck, "A")

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([r["record_type"] for r in records], ["command", "reply", "event"])
            self.assertEqual(records[2]["event"], "track_started")
            self.assertEqual(records[2]["queue_id"], 18237)
            self.assertEqual(records[2]["slot_token"], "A-94721")
            self.assertEqual(records[2]["deck"], "A")
            self.assertIn("monotonic_time_ms", records[2])
            self.assertIn("event_monotonic_time_ms", records[2])


    def test_session_identity_is_present_on_every_record_and_shared_between_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = ProtocolSessionContext(
                session_id="session-5011",
                app_version="5011",
                native_daemon_version="not_connected",
            )
            control_path = Path(tmp) / "control.jsonl"
            native_path = Path(tmp) / "native.jsonl"
            control_logger = JsonlProtocolLogger(
                control_path,
                engine_name="native",
                session_context=context,
            )
            native_logger = JsonlProtocolLogger(
                native_path,
                engine_name="native",
                session_context=context,
            )

            control_logger.command("start")
            native_logger.write({"record_type": "event", "event": "engine_ready"})
            native_logger.set_native_daemon_version("5011")
            control_logger.reply(1, ok=True, result=True)
            native_logger.command("ping")

            records = []
            for path in (control_path, native_path):
                records.extend(json.loads(line) for line in path.read_text().splitlines())
            self.assertTrue(records)
            self.assertEqual({record["session_id"] for record in records}, {"session-5011"})
            self.assertEqual({record["app_version"] for record in records}, {"5011"})
            self.assertEqual(
                {record["native_daemon_version"] for record in records},
                {"not_connected", "5011"},
            )

    def test_subscriber_failure_does_not_block_other_subscribers(self) -> None:
        logger = JsonlProtocolLogger(None, engine_name="native")
        bus = EngineEventBus(engine_name="native", protocol_logger=logger)
        received: list[str] = []
        bus.subscribe(lambda event: (_ for _ in ()).throw(RuntimeError("observer failed")))
        bus.subscribe(lambda event: received.append(event.event))
        bus.publish("engine_ready")
        self.assertEqual(received, ["engine_ready"])

    def test_annotated_identity_parser(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="18237",track_id="17",station_key="station-1",'
            'wb_ab_slot_token="A-94721",wb_audio_start="0.100",wb_play_start="0.125",'
            'cue_in="0.125",cue_out="301.777",wb_crossfade_trigger="301.777",'
            'wb_effective_end="302.500",wb_orig_total="305.250",'
            'fade_in="0.500",fade_out="6.000",artist="Artist",title="Title",year="1999":/music/example.mp3'
        )
        self.assertEqual(identity["queue_id"], 18237)
        self.assertEqual(identity["track_id"], 17)
        self.assertEqual(identity["station_key"], "station-1")
        self.assertEqual(identity["slot_token"], "A-94721")
        self.assertEqual(identity["path"], "/music/example.mp3")
        self.assertEqual(identity["cue_in_ms"], 125)
        self.assertEqual(identity["cue_out_ms"], 301777)
        self.assertEqual(identity["audio_start_ms"], 100)
        self.assertEqual(identity["play_start_ms"], 125)
        self.assertEqual(identity["transition_at_ms"], 301777)
        self.assertEqual(identity["effective_end_ms"], 302500)
        self.assertEqual(identity["source_end_ms"], 305250)
        self.assertEqual(identity["fade_in_ms"], 500)
        self.assertEqual(identity["fade_out_ms"], 6000)
        self.assertEqual(identity["artist"], "Artist")
        self.assertEqual(identity["title"], "Title")
        self.assertEqual(identity["year"], "1999")



    def test_native_analysis_descriptor_fields_are_parsed(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="91",wb_native_analyze="1",wb_manual_timing="0",'
            'wb_hard_clean_transition="0",wb_analysis_window_ms="10",'
            'wb_analysis_sustain_ms="40",wb_analysis_artifact_max_ms="320",'
            'wb_analysis_artifact_silence_ms="270",wb_no_crossfade_max_duration="65",'
            'wb_crossfade_fallback="3.5",wb_crossfade_min="0.2",wb_crossfade_max="7",'
            'wb_gap_start_dbfs="-21",wb_gap_end_dbfs="-25",'
            'wb_crossfade_trigger_relative_db="-8":/music/runtime.mp3'
        )
        self.assertTrue(identity["analysis_requested"])
        self.assertFalse(identity["manual_timing"])
        self.assertEqual(identity["analysis_window_ms"], 10)
        self.assertEqual(identity["analysis_sustain_ms"], 40)
        self.assertEqual(identity["analysis_artifact_max_ms"], 320)
        self.assertEqual(identity["analysis_artifact_silence_ms"], 270)
        self.assertEqual(identity["no_crossfade_max_duration_ms"], 65000)
        self.assertEqual(identity["crossfade_fallback_ms"], 3500)
        self.assertEqual(identity["crossfade_min_ms"], 200)
        self.assertEqual(identity["crossfade_max_ms"], 7000)
        self.assertEqual(identity["gap_start_threshold_dbfs"], -21.0)
        self.assertEqual(identity["gap_end_threshold_dbfs"], -25.0)
        self.assertEqual(identity["crossfade_trigger_relative_db"], -8.0)

    def test_annotated_identity_ignores_retired_liq_aliases(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="77",track_id="88",station_key="legacy",'
            'wb_ab_slot_token="legacy-token",liq_cue_in="1.250",'
            'liq_cue_out="9.500",liq_fade_in="0.400",liq_fade_out="2.000":'
            '/music/legacy.mp3'
        )
        self.assertEqual(identity["cue_in_ms"], 0)
        self.assertEqual(identity["cue_out_ms"], 0)
        self.assertEqual(identity["fade_in_ms"], 0)
        self.assertEqual(identity["fade_out_ms"], 0)

    def test_canonical_descriptor_fields_are_not_affected_by_retired_aliases(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="77",cue_in="2.000",liq_cue_in="1.000",'
            'cue_out="8.000",liq_cue_out="7.000",fade_in="0.600",'
            'liq_fade_in="0.300",fade_out="2.500",liq_fade_out="1.500":'
            '/music/canonical.mp3'
        )
        self.assertEqual(identity["cue_in_ms"], 2000)
        self.assertEqual(identity["cue_out_ms"], 8000)
        self.assertEqual(identity["fade_in_ms"], 600)
        self.assertEqual(identity["fade_out_ms"], 2500)

    def test_retired_wb_fade_aliases_are_ignored(self) -> None:
        identity = extract_annotated_identity(
            'annotate:queue_id="78",cue_in="2.000",wb_play_start="1.000",'
            'cue_out="8.000",wb_crossfade_trigger="7.000",fade_in="0.600",'
            'wb_fade_duration="1.500",wb_fade_out="1.000":'
            '/music/canonical-private.mp3'
        )
        self.assertEqual(identity["cue_in_ms"], 2000)
        self.assertEqual(identity["cue_out_ms"], 8000)
        self.assertEqual(identity["fade_in_ms"], 600)
        self.assertEqual(identity["fade_out_ms"], 0)

    def test_annotated_identity_preserves_utf8_and_escapes(self) -> None:
        uri = (
            r'annotate:queue_id="36",artist="Blümchen",'
            r'title="Ich bin wieder hier [käptn \"Nemo\" mix]":'
            r'/music/Blümchen - käptn.mp3'
        )
        identity = extract_annotated_identity(uri)
        self.assertEqual(identity["artist"], "Blümchen")
        self.assertEqual(identity["title"], 'Ich bin wieder hier [käptn "Nemo" mix]')
        self.assertEqual(identity["path"], "/music/Blümchen - käptn.mp3")

    def test_large_native_state_keeps_tail_dsp_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "native.jsonl"
            logger = JsonlProtocolLogger(path, engine_name="native")
            state = {f"field_{index:03d}": index for index in range(180)}
            state.update({
                "dsp_input_fifo_high_water_bytes": 987654,
                "dsp_write_poll_timeout_count": 321,
                "dsp_writer_max_backpressure_ms": 4567,
            })
            logger.reply(7, ok=True, result=state)
            record = json.loads(path.read_text(encoding="utf-8").strip())
            result = record["result"]
            self.assertEqual(result["field_179"], 179)
            self.assertEqual(result["dsp_input_fifo_high_water_bytes"], 987654)
            self.assertEqual(result["dsp_write_poll_timeout_count"], 321)
            self.assertEqual(result["dsp_writer_max_backpressure_ms"], 4567)
            self.assertNotIn("__json_safe_truncated_keys__", result)

    def test_mapping_truncation_is_explicit_not_silent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "native.jsonl"
            logger = JsonlProtocolLogger(path, engine_name="native")
            logger.reply(8, ok=True, result={f"field_{index:04d}": index for index in range(1030)})
            record = json.loads(path.read_text(encoding="utf-8").strip())
            result = record["result"]
            self.assertEqual(result["field_1023"], 1023)
            self.assertEqual(result["__json_safe_truncated_keys__"], 6)

    def test_compact_mode_collapses_polling_and_filters_routine_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compact.jsonl"
            logger = JsonlProtocolLogger(
                path,
                engine_name="native",
                verbose=False,
                heartbeat_interval_sec=3600.0,
            )
            bus = EngineEventBus(engine_name="native", protocol_logger=logger)
            large_state = {
                "running": True,
                "active_deck": "A",
                "position_ms": 123456,
                "queue_id": 77,
                "slot_token": "slot-77",
                **{f"diagnostic_{index:03d}": index for index in range(400)},
            }
            for _index in range(1000):
                logger.routine_poll_success(
                    command="get_state",
                    station_key="db-Test.db",
                    result=large_state,
                )
            bus.publish("native_audio_probe_progress", station_key="db-Test.db")
            bus.publish(
                "native_resource_snapshot",
                station_key="db-Test.db",
                payload={"reason": "periodic", "rss_kb": 12345},
            )
            bus.publish("track_started", station_key="db-Test.db", queue_id=77)

            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([record["record_type"] for record in records], ["poll_heartbeat", "event"])
            self.assertEqual(records[0]["command"], "get_state")
            self.assertEqual(records[0]["summary"]["position_ms"], 123456)
            self.assertNotIn("diagnostic_399", records[0]["summary"])
            self.assertEqual(records[1]["event"], "track_started")
            self.assertLess(path.stat().st_size, 5000)


    def test_compact_sync_event_reply_keeps_health_summary_without_full_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compact-sync.jsonl"
            logger = JsonlProtocolLogger(path, engine_name="native", verbose=False)
            large_state = {
                "running": True,
                "active_deck": "B",
                "position_ms": 223479,
                "queue_id": 88,
                "slot_token": "slot-88",
                **{f"diagnostic_{index:03d}": index for index in range(500)},
            }
            logger.compact_command_success_reply(9, command="sync_event", result=large_state)
            record = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertTrue(record["ok"])
            self.assertTrue(record["result"]["compact"])
            self.assertEqual(record["result"]["command"], "sync_event")
            self.assertEqual(record["result"]["summary"]["position_ms"], 223479)
            self.assertNotIn("diagnostic_499", record["result"]["summary"])
            self.assertLess(path.stat().st_size, 3000)

    def test_compact_mode_keeps_manual_resource_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compact.jsonl"
            logger = JsonlProtocolLogger(path, engine_name="native", verbose=False)
            bus = EngineEventBus(engine_name="native", protocol_logger=logger)
            bus.publish(
                "native_resource_snapshot",
                payload={"reason": "manual", "rss_kb": 12345},
            )
            record = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(record["event"], "native_resource_snapshot")
            self.assertEqual(record["payload"]["reason"], "manual")

    def test_logger_is_fail_open_for_unwritable_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "directory_target"
            directory.mkdir()
            logger = JsonlProtocolLogger(directory, engine_name="native")
            self.assertFalse(logger.write({"record_type": "event", "event": "engine_ready"}))
            self.assertTrue(logger.last_error)

    def test_logger_prunes_oversized_and_out_of_range_old_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.jsonl"
            oversized = path.with_name("engine.jsonl.1")
            out_of_range = path.with_name("engine.jsonl.3")
            retained = path.with_name("engine.jsonl.2")
            oversized.write_bytes(b"x" * 1_100_000)
            out_of_range.write_bytes(b"old")
            retained.write_bytes(b"kept")
            JsonlProtocolLogger(
                path,
                engine_name="native",
                max_bytes=1_000_000,
                backup_count=2,
            )
            self.assertFalse(oversized.exists())
            self.assertFalse(out_of_range.exists())
            self.assertTrue(retained.exists())


if __name__ == "__main__":
    unittest.main()
