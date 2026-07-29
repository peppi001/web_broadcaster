from __future__ import annotations
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
import audio_engine
from audio_engine import NativeEngine, NativeEngineUnavailable, create_audio_engine
from audio_engine.protocol import JsonlProtocolLogger, ProtocolSessionContext

class _FakeNativeServer:

    def __init__(self, socket_path: Path) -> None:
        self.socket_path = socket_path
        self.messages: list[dict] = []
        self.ready = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(2.0):
            raise RuntimeError('fake native server did not start')

    @staticmethod
    def _send(connection: socket.socket, value: dict) -> None:
        connection.sendall((json.dumps(value, separators=(',', ':')) + '\n').encode())

    def _run(self) -> None:
        try:
            self.socket_path.unlink(missing_ok=True)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(self.socket_path))
                server.listen(1)
                self.ready.set()
                server.settimeout(3.0)
                connection, _ = server.accept()
                with connection:
                    self._send(connection, {'version': 1, 'event': 'engine_ready', 'deck': 'A', 'native_daemon_version': '5064', 'payload': {'control_only': False, 'native_daemon_version': '5064'}})
                    buffer = b''
                    while True:
                        chunk = connection.recv(65536)
                        if not chunk:
                            return
                        buffer += chunk
                        while b'\n' in buffer:
                            raw, buffer = buffer.split(b'\n', 1)
                            if not raw.strip():
                                continue
                            message = json.loads(raw)
                            self.messages.append(message)
                            request_id = message['request_id']
                            command = message['command']
                            if command == 'ping':
                                self._send(connection, {'version': 1, 'reply_to': request_id, 'ok': True, 'native_daemon_version': '5064', 'result': {'pong': True, 'native_daemon_version': '5064'}})
                            elif command == 'get_state':
                                self._send(connection, {'version': 1, 'reply_to': request_id, 'ok': True, 'native_daemon_version': '5064', 'state': {'running': True, 'active_deck': 'A', 'position_ms': 1234, 'queue_id': 8, 'slot_token': 'slot-8', **{f'diagnostic_{index:03d}': index for index in range(300)}}})
                            elif command == 'load':
                                self._send(connection, {'version': 1, 'reply_to': request_id, 'ok': True, 'native_daemon_version': '5064', 'result': {'accepted': True, 'load_state': 'confirmed', 'deduplicated': False}})
                                track = message['track']
                                self._send(connection, {'version': 1, 'event': 'deck_loaded', 'native_daemon_version': '5064', 'station_key': track['station_key'], 'queue_id': track['queue_id'], 'slot_token': track['slot_token'], 'deck': message['deck'], 'track_id': track['track_id'], 'path': track['path'], 'payload': {'control_only': False, 'confirmed': True}})
                            else:
                                self._send(connection, {'version': 1, 'reply_to': request_id, 'ok': False, 'native_daemon_version': '5064', 'error': f'unsupported command: {command}'})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        finally:
            self.done.set()
            self.socket_path.unlink(missing_ok=True)

class NativeEngineTests(unittest.TestCase):

    def test_native_client_matches_replies_and_delivers_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / 'engine.sock'
            log_path = Path(tmp) / 'native.jsonl'
            server = _FakeNativeServer(socket_path)
            server.start()
            context = ProtocolSessionContext(session_id='native-test-session', app_version='5064', native_daemon_version='not_connected')
            engine = NativeEngine(socket_path=str(socket_path), request_timeout_sec=1.0, protocol_logger=JsonlProtocolLogger(log_path, engine_name='native', session_context=context))
            events = []
            engine.subscribe_events(events.append)
            self.assertTrue(engine.ping()['result']['pong'])
            uri = 'annotate:queue_id="18237",track_id="17",station_key="station-1",wb_ab_slot_token="A-94721":/music/example.mp3'
            self.assertTrue(engine.load_deck('a', uri))
            deadline = time.monotonic() + 1.0
            while len(events) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual([event.event for event in events], ['engine_ready', 'deck_loaded'])
            self.assertEqual(events[1].queue_id, 18237)
            self.assertEqual(events[1].slot_token, 'A-94721')
            self.assertEqual(server.messages[1]['track']['path'], '/music/example.mp3')
            self.assertEqual(server.messages[0]['session_id'], 'native-test-session')
            self.assertEqual(server.messages[0]['app_version'], '5064')
            records = [json.loads(line) for line in log_path.read_text().splitlines()]
            self.assertIn('command', {record['record_type'] for record in records})
            self.assertIn('reply', {record['record_type'] for record in records})
            self.assertIn('event', {record['record_type'] for record in records})
            self.assertEqual({record['session_id'] for record in records}, {'native-test-session'})
            self.assertEqual({record['app_version'] for record in records}, {'5064'})
            self.assertEqual(engine.native_daemon_version, '5064')
            self.assertEqual(records[-1]['native_daemon_version'], '5064')
            engine.close()

    def test_compact_native_poll_logs_heartbeat_but_keeps_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / 'engine.sock'
            log_path = Path(tmp) / 'native.jsonl'
            server = _FakeNativeServer(socket_path)
            server.start()
            engine = NativeEngine(socket_path=str(socket_path), request_timeout_sec=1.0, protocol_logger=JsonlProtocolLogger(log_path, engine_name='native', verbose=False, heartbeat_interval_sec=3600.0))
            try:
                self.assertTrue(engine.get_state()['running'])
                self.assertTrue(engine.get_state()['running'])
                with self.assertRaisesRegex(Exception, 'unsupported command'):
                    engine.get_diagnostics_state()
                records = [json.loads(line) for line in log_path.read_text().splitlines()]
                heartbeat = [record for record in records if record.get('record_type') == 'poll_heartbeat']
                self.assertEqual(len(heartbeat), 1)
                self.assertEqual(heartbeat[0]['summary']['position_ms'], 1234)
                state_commands = [record for record in records if record.get('record_type') == 'command' and record.get('command') == 'get_state']
                state_replies = [record for record in records if record.get('record_type') == 'reply' and record.get('ok') is True]
                self.assertEqual(state_commands, [])
                self.assertEqual(state_replies, [])
                diagnostics_commands = [record for record in records if record.get('record_type') == 'command' and record.get('command') == 'get_diagnostics']
                failed_replies = [record for record in records if record.get('record_type') == 'reply' and record.get('ok') is False]
                self.assertEqual(len(diagnostics_commands), 1)
                self.assertEqual(len(failed_replies), 1)
            finally:
                engine.close()

    def test_close_is_quiet_and_future_requests_fail_locally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = Path(tmp) / 'engine.sock'
            server = _FakeNativeServer(socket_path)
            server.start()
            engine = NativeEngine(socket_path=str(socket_path), request_timeout_sec=1.0)
            events = []
            engine.subscribe_events(events.append)
            self.assertTrue(engine.ping()['result']['pong'])
            deadline = time.monotonic() + 1.0
            while not events and time.monotonic() < deadline:
                time.sleep(0.01)
            events.clear()
            engine.close()
            self.assertTrue(server.done.wait(1.0))
            self.assertEqual([event for event in events if event.event == 'engine_error'], [])
            with self.assertRaisesRegex(NativeEngineUnavailable, 'closed'):
                engine.ping()
if __name__ == '__main__':
    unittest.main()
