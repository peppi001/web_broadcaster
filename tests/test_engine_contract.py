from __future__ import annotations
import unittest
from pathlib import Path
from audio_engine import TrackDescriptor, create_audio_engine

class AudioEngineContractTests(unittest.TestCase):

    def test_track_descriptor_preserves_queue_identity(self) -> None:
        track = TrackDescriptor(queue_id=18237, slot_token='A-94721', path='/music/example.mp3', cue_in_ms=125, cue_out_ms=301777, audio_start_ms=100, play_start_ms=125, transition_at_ms=301777, effective_end_ms=302500, source_end_ms=305250, artist='Artist', title='Title', year='1999')
        self.assertEqual(track.queue_id, 18237)
        self.assertEqual(track.slot_token, 'A-94721')
        self.assertEqual(track.source_end_ms, 305250)
        self.assertEqual(track.year, '1999')

    def test_factory_defaults_to_native(self) -> None:
        messages: list[str] = []
        engine = create_audio_engine(environ={'WEB_BROADCASTER_ENGINE_SESSION_ID': 'test-session', 'WEB_BROADCASTER_ENGINE_SOCKET': '/tmp/test-native.sock'}, log_callback=messages.append, app_version='5064')
        self.assertEqual(engine.name, 'native')
        self.assertEqual(engine.session_id, 'test-session')
        self.assertIn('Audio engine selected: native', messages[0])
        self.assertIn('app_version: 5064', messages[0])
        engine.close()

    def test_successful_sync_event_reply_is_compact_in_normal_protocol_mode(self) -> None:
        native_source = (Path(__file__).resolve().parents[1] / 'audio_engine' / 'native_engine.py').read_text(encoding='utf-8')
        protocol_source = (Path(__file__).resolve().parents[1] / 'audio_engine' / 'protocol.py').read_text(encoding='utf-8')
        self.assertIn('command == "sync_event"', native_source)
        self.assertIn('compact_command_success_reply', native_source)
        self.assertIn('normalized != "sync_event"', protocol_source)
        self.assertIn('"summary": self._compact_poll_result', protocol_source)

    def test_factory_uses_compact_protocol_defaults_and_allows_verbose_override(self) -> None:
        compact = create_audio_engine(environ={'WEB_BROADCASTER_ENGINE_SOCKET': '/tmp/test-native-compact.sock', 'WEB_BROADCASTER_NATIVE_PROTOCOL_LOG': 'disabled'}, app_version='5064')
        try:
            self.assertFalse(compact._protocol_logger.verbose)
            self.assertEqual(compact._protocol_logger.max_bytes, 10 * 1024 * 1024)
            self.assertEqual(compact._protocol_logger.backup_count, 2)
            self.assertEqual(compact._protocol_logger.heartbeat_interval_sec, 60.0)
        finally:
            compact.close()
        verbose = create_audio_engine(environ={'WEB_BROADCASTER_ENGINE_SOCKET': '/tmp/test-native-verbose.sock', 'WEB_BROADCASTER_NATIVE_PROTOCOL_LOG': 'disabled', 'WEB_BROADCASTER_ENGINE_PROTOCOL_VERBOSE': '1'}, app_version='5064')
        try:
            self.assertTrue(verbose._protocol_logger.verbose)
        finally:
            verbose.close()
if __name__ == '__main__':
    unittest.main()
