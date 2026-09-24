"""Regression coverage for URL-source metadata forwarding in the native decoder."""
from __future__ import annotations

import contextlib
import http.server
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.parse import unquote

from tests import test_native_icecast_output as icecast_tests


class _AudioHttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, audio: bytes, *, icy: bool = False):
        self.audio = audio
        self.icy = icy
        self.connections = 0
        self._lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _AudioHandler)
        self.worker = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self):
        self.worker.start()
        return self

    def close(self):
        self.shutdown()
        self.server_close()
        self.worker.join(timeout=3)


class _AudioHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        return

    def do_GET(self):
        source: _AudioHttpServer = self.server
        with source._lock:
            source.connections += 1
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg" if source.icy else "application/ogg")
        if source.icy:
            self.send_header("icy-metaint", "8192")
            self.send_header("icy-name", "This is a station name, not a song")
        self.end_headers()
        try:
            if not source.icy:
                for index in range(0, len(source.audio), 1024):
                    self.wfile.write(source.audio[index:index + 1024])
                    self.wfile.flush()
                    time.sleep(.025)
            else:
                # The two titles arrive on the SAME HTTP connection as PCM.
                for index in range(0, len(source.audio) - 8192, 8192):
                    self.wfile.write(source.audio[index:index + 8192])
                    song = "Initial Artist - Initial Song" if index < 16384 else "Next Artist - Next Song"
                    text = f"StreamTitle='{song}';".encode("utf-8")
                    blocks = (len(text) + 15) // 16
                    self.wfile.write(bytes([blocks]) + text.ljust(blocks * 16, b"\0"))
                    self.wfile.flush()
                    time.sleep(.3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class V6080UpstreamMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.binary = cls.root / "native_engine/bin/web_broadcaster_engine"
        cls.ffmpeg = shutil.which("ffmpeg")

    def _exercise(self, *, icy: bool):
        if not self.ffmpeg or not self.binary.is_file():
            self.skipTest("native daemon and ffmpeg are required")
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / ("input.mp3" if icy else "input.ogg")
            # Keep the mock URL alive throughout the verification window. A
            # short finite Ogg file can close/reconnect before a busy release
            # builder starts the asynchronously prebuffered native deck.
            # This test is for a live URL, not finite-file EOF/reconnection.
            duration = 18 if icy else 90
            command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency=461:duration={duration}", "-ac", "2", "-ar", "48000"]
            if icy:
                command += ["-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]
            else:
                command += ["-codec:a", "libvorbis", "-b:a", "128k", "-metadata", "TITLE=Vorbis Song", "-metadata", "ARTIST=Vorbis Artist", "-f", "ogg"]
            subprocess.run(command + ["-y", str(audio)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            upstream = _AudioHttpServer(audio.read_bytes(), icy=icy).start()
            icecast = icecast_tests._MockIcecastSource()
            icecast.start()
            harness = icecast_tests.NativeIcecastOutputTests("test_metadata_reapply_and_encoder_kill_recovery")
            harness.binary = self.binary
            try:
                with harness._daemon(station_key="source-metadata-test") as (native, _tmp):
                    native.configure_icecast_output(
                        enabled=True, host="127.0.0.1", port=icecast.port,
                        mount="/source-meta.mp3", username="source", password="secret",
                        bitrate_kbps=128, stream_name="Metadata forwarding", public_stream=False,
                    )
                    native.start()
                    url = f"http://127.0.0.1:{upstream.server_address[1]}/radio"
                    token = "upstream-icy" if icy else "upstream-vorbis"
                    uri = (
                        f'annotate:queue_id="60801",track_id="60802",station_key="source-metadata-test",'
                        f'wb_ab_slot_token="{token}",wb_stream_source="1",wb_stream_infinite="1",'
                        f'wb_manual_timing="1",title="Streaming":{url}'
                    )
                    self.assertTrue(native.load_deck("A", uri, clear_slot=True))
                    payload = {"cue_in_ms": 0, "cue_out_ms": 0, "audio_start_ms": 0,
                               "play_start_ms": 0, "transition_at_ms": 0, "effective_end_ms": 0,
                               "source_end_ms": 0}
                    def sync(event_name: str) -> None:
                        native.sync_live_event({
                            "event": event_name, "station_key": "source-metadata-test",
                            "queue_id": 60801, "track_id": 60802, "deck": "A", "slot_token": token,
                            "path": url, "title": "Streaming", "stream_source": True,
                            "event_monotonic_time_ms": int(time.monotonic() * 1000),
                            "event_wall_time_unix_ms": int(time.time() * 1000), "payload": payload,
                        })

                    sync("deck_loaded")
                    # A load command is not a playback-readiness barrier. Match
                    # the real URL handoff path: commit the audible identity
                    # only after the native decoder has filled its prebuffer.
                    ready_deadline = time.monotonic() + 10
                    ready_state = {}
                    while time.monotonic() < ready_deadline:
                        ready_state = native.get_state()
                        if (ready_state.get("native_audio_probe_prebuffer_ready")
                                and ready_state.get("native_audio_probe_deck") == "A"
                                and ready_state.get("native_audio_probe_slot_token") == token):
                            break
                        time.sleep(.05)
                    self.assertTrue(
                        ready_state.get("native_audio_probe_prebuffer_ready")
                        and ready_state.get("native_audio_probe_deck") == "A"
                        and ready_state.get("native_audio_probe_slot_token") == token,
                        f"Source URL did not prebuffer before track_started: {ready_state!r}",
                    )
                    sync("track_started")
                    expected = b"song=Next%20Artist%20-%20Next%20Song" if icy else b"song=Vorbis%20Artist%20-%20Vorbis%20Song"
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        if any(expected in req for req in icecast.metadata_requests):
                            break
                        time.sleep(.075)
                    self.assertTrue(any(expected in req for req in icecast.metadata_requests),
                                    [unquote(req.decode("latin1", errors="replace")) for req in icecast.metadata_requests])
                    self.assertEqual(upstream.connections, 1, "metadata must reuse the audio HTTP connection")
                    if not icy:
                        # A second URL may decode/prebuffer in the other deck,
                        # but its tags must not replace the currently audible A.
                        preload_file = Path(directory) / "preloaded.ogg"
                        subprocess.run(
                            [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
                             "-f", "lavfi", "-i", "sine=frequency=481:duration=18",
                             "-ac", "2", "-ar", "48000", "-codec:a", "libvorbis", "-b:a", "128k",
                                            "-metadata", "TITLE=Wrong Preloaded Song",
                                            "-metadata", "ARTIST=Wrong Preloaded Artist",
                                            "-f", "ogg", "-y", str(preload_file)],
                            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        )
                        preload = _AudioHttpServer(preload_file.read_bytes()).start()
                        try:
                            preload_url = f"http://127.0.0.1:{preload.server_address[1]}/next"
                            second_uri = (
                                'annotate:queue_id="60803",track_id="60804",'
                                'station_key="source-metadata-test",wb_ab_slot_token="preload-only",'
                                'wb_stream_source="1",wb_stream_infinite="1",wb_manual_timing="1",'
                                f'title="Streaming":{preload_url}'
                            )
                            self.assertTrue(native.load_deck("B", second_uri, clear_slot=True))
                            native.sync_live_event({
                                "event": "deck_loaded", "station_key": "source-metadata-test",
                                "queue_id": 60803, "track_id": 60804, "deck": "B",
                                "slot_token": "preload-only", "path": preload_url, "title": "Streaming",
                                "stream_source": True, "event_monotonic_time_ms": int(time.monotonic() * 1000),
                                "event_wall_time_unix_ms": int(time.time() * 1000), "payload": payload,
                            })
                            preload_deadline = time.monotonic() + 3
                            while preload.connections < 1 and time.monotonic() < preload_deadline:
                                time.sleep(.05)
                            self.assertEqual(preload.connections, 1)
                            time.sleep(.5)
                            self.assertNotIn(
                                b"Wrong%20Preloaded", b" ".join(icecast.metadata_requests),
                                "preloaded URL must never overwrite the active output's metadata",
                            )
                        finally:
                            preload.close()
            finally:
                upstream.close()
                icecast.close()

    def test_ogg_vorbis_48khz_title_and_artist_forwarded(self):
        self._exercise(icy=False)

    def test_icy_title_updates_forwarded_over_same_http_connection(self):
        self._exercise(icy=True)


if __name__ == "__main__":
    unittest.main()
