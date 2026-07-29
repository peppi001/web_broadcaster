import os

APP_VERSION = "6024"


def _environment_switch_enabled(name: str) -> bool:
    value = str(os.environ.get(name, "") or "").strip().lower()
    return value in {"1", "true", "yes", "on", "enabled"}


# DEBUG=1 restores only the logging streams that existed before v6015.
# It does not enable Flask debug mode, protocol verbose mode or any extra trace path.
_RUNTIME_LOGGING_ENABLED = _environment_switch_enabled("DEBUG")

import sys
import shutil
import codecs as _console_codecs
import collections as _console_collections
import queue as _console_queue
import threading as _console_threading
import time as _console_time

from autodj import AutoDJDependencies, AutoDJService
from storage import PlaybackRepository, PlaybackRepositoryDependencies
from player import (
    ManualNextDependencies,
    ManualNextOrchestrator,
    PlayerHandoffDependencies,
    PlayerHandoffService,
)
from station import StationService, StationServiceDependencies

from audio_engine import (
    NativeLifecycleCoordinator,
    configure_audio_engine_runtime,
    get_audio_engine,
    publish_audio_engine_event as _publish_audio_engine_event,
    publish_audio_engine_track_seeked as _publish_audio_engine_track_seeked,
    station_runtime_context,
    station_runtime_override as _station_runtime_override,
)


configure_audio_engine_runtime(
    app_version=APP_VERSION,
    app_root=os.path.dirname(os.path.abspath(__file__)),
    station_key_resolver=lambda: str(get_active_station_key() or ""),
    # Engine selection details remain available in protocol telemetry, not stdout.
    log_callback=lambda _message: None,
)


class _ConsoleBroadcastHub:
    """Thread-safe raw console fan-out with a bounded in-memory scrollback."""

    def __init__(self, max_chars: int = 2_000_000) -> None:
        self.max_chars = max(64_000, int(max_chars))
        self._chunks = _console_collections.deque()
        self._char_count = 0
        self._subscribers = set()
        self._lock = _console_threading.RLock()

    def publish(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._chunks.append(text)
            self._char_count += len(text)
            while self._char_count > self.max_chars and self._chunks:
                removed = self._chunks.popleft()
                self._char_count -= len(removed)
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            try:
                subscriber.put_nowait(text)
            except _console_queue.Full:
                # Keep a slow browser from consuming unbounded server memory. Drop
                # its oldest pending chunk while the authoritative ring remains intact.
                try:
                    subscriber.get_nowait()
                except _console_queue.Empty:
                    pass
                try:
                    subscriber.put_nowait(text)
                except _console_queue.Full:
                    pass

    def subscribe(self):
        subscriber = _console_queue.Queue(maxsize=4096)
        with self._lock:
            snapshot = ''.join(self._chunks)
            self._subscribers.add(subscriber)
        return subscriber, snapshot

    def unsubscribe(self, subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def snapshot(self) -> str:
        with self._lock:
            return ''.join(self._chunks)


def _console_buffer_limit() -> int:
    try:
        return int(os.environ.get('WEB_BROADCASTER_CONSOLE_BUFFER_CHARS', '2000000') or 2_000_000)
    except (TypeError, ValueError):
        return 2_000_000


CONSOLE_HUB = _ConsoleBroadcastHub(max_chars=_console_buffer_limit())
_CONSOLE_TEE_STARTED = False
_CONSOLE_MIRROR_FDS = []

# Keep known noisy mpg123/libmpg123 diagnostics out of the terminal,
# systemd stdout/stderr stream and Settings Console view.
_CONSOLE_SUPPRESSED_LINE_FRAGMENTS = tuple(
    value.encode('utf-8')
    for value in (
        'error: dequantization failed!',
        'Illegal Audio-MPEG-Header',
        'Trying to resync...',
        'Hit end of (available) data during resync.',
        'Skipped ',
    )
)

def _console_line_filter_enabled() -> bool:
    value = os.environ.get('WEB_BROADCASTER_CONSOLE_FILTER', '1').strip().lower()
    return value not in {'0', 'false', 'no', 'off'}


def _console_should_suppress_record(record: bytes) -> bool:
    return any(fragment in record for fragment in _CONSOLE_SUPPRESSED_LINE_FRAGMENTS)


def _console_write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except InterruptedError:
            continue
        if written <= 0:
            break
        view = view[written:]


def _console_fd_reader(read_fd: int, mirror_fd: int, encoding: str) -> None:
    decoder = _console_codecs.getincrementaldecoder(encoding)(errors='replace')
    filter_enabled = _console_line_filter_enabled()
    pending = bytearray()

    def emit(data: bytes) -> None:
        if not data:
            return
        try:
            _console_write_all(mirror_fd, data)
        except Exception:
            pass
        try:
            text = decoder.decode(data, final=False)
            if text:
                CONSOLE_HUB.publish(text)
        except Exception:
            pass

    def flush_complete_records() -> None:
        while pending:
            newline_pos = pending.find(b'\n')
            carriage_pos = pending.find(b'\r')
            positions = [pos for pos in (newline_pos, carriage_pos) if pos >= 0]
            if not positions:
                return
            delimiter_pos = min(positions)
            if pending[delimiter_pos] == 13:  # CR
                # Keep CRLF together so a suppressed line does not leave an empty
                # LF record behind. If CR is currently the last buffered byte,
                # wait for the next read to determine whether LF follows.
                if delimiter_pos + 1 >= len(pending):
                    return
                end_pos = delimiter_pos + (2 if pending[delimiter_pos + 1] == 10 else 1)
            else:
                end_pos = delimiter_pos + 1
            record = bytes(pending[:end_pos])
            del pending[:end_pos]
            if not _console_should_suppress_record(record):
                emit(record)

    try:
        while True:
            try:
                data = os.read(read_fd, 8192)
            except InterruptedError:
                continue
            except OSError:
                break
            if not data:
                break
            if not filter_enabled:
                emit(data)
                continue
            pending.extend(data)
            flush_complete_records()
    finally:
        if filter_enabled and pending:
            record = bytes(pending)
            if not _console_should_suppress_record(record):
                emit(record)
        try:
            tail = decoder.decode(b'', final=True)
            if tail:
                CONSOLE_HUB.publish(tail)
        except Exception:
            pass
        try:
            os.close(read_fd)
        except Exception:
            pass
        try:
            os.close(mirror_fd)
        except Exception:
            pass


def _install_console_fd_tee() -> None:
    """Mirror process stdout/stderr to their original destinations and the UI hub.

    This works the same when stdout/stderr point to a terminal or to systemd's
    journal file descriptors. No journal reader is used.
    """
    global _CONSOLE_TEE_STARTED
    if _CONSOLE_TEE_STARTED:
        return
    if os.environ.get('WEB_BROADCASTER_CONSOLE_MIRROR', '1').strip().lower() in {'0', 'false', 'no', 'off'}:
        return

    created = []
    try:
        for fd, stream_name in ((1, 'stdout'), (2, 'stderr')):
            mirror_fd = os.dup(fd)
            read_fd, write_fd = os.pipe()
            os.dup2(write_fd, fd)
            os.close(write_fd)
            created.append((read_fd, mirror_fd, stream_name))

        _CONSOLE_TEE_STARTED = True
        _CONSOLE_MIRROR_FDS.extend((read_fd, mirror_fd) for read_fd, mirror_fd, _ in created)
        for read_fd, mirror_fd, stream_name in created:
            stream = sys.stdout if stream_name == 'stdout' else sys.stderr
            encoding = getattr(stream, 'encoding', None) or 'utf-8'
            thread = _console_threading.Thread(
                target=_console_fd_reader,
                args=(read_fd, mirror_fd, encoding),
                name=f'wb-console-{stream_name}',
                daemon=True,
            )
            thread.start()

        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(line_buffering=True, write_through=True)
            except Exception:
                pass
    except Exception:
        for read_fd, mirror_fd, _ in created:
            for fd in (read_fd, mirror_fd):
                try:
                    os.close(fd)
                except Exception:
                    pass


_install_console_fd_tee()
import hashlib
from mutagen import File as MutagenFile


def ensure_fts_ready(conn):
    """Ensure an FTS5 index exists for tracks.

    The tracks table schema may vary between versions. We only index columns that
    actually exist to avoid trigger errors like: sqlite3.OperationalError: no such column: new.title

    Returns True if FTS is available and initialized.
    """
    c = conn.cursor()

    # Only index columns that exist in the tracks table.
    try:
        c.execute("PRAGMA table_info(tracks)")
        track_cols = [row[1] for row in c.fetchall()]
    except Exception:
        track_cols = []

    candidates = ["filename", "path", "title", "artist", "album"]
    fts_cols = [col for col in candidates if col in track_cols]
    if not fts_cols:
        # Nothing to index.
        return False

    try:
        # If an older/incorrect FTS schema exists, recreate it.
        recreate = False
        try:
            c.execute("PRAGMA table_info(tracks_fts)")
            existing = [row[1] for row in c.fetchall()]
            if existing:
                # For FTS virtual tables, PRAGMA includes internal columns too; compare the prefix.
                existing_user = [x for x in existing if x not in ("", None)]
                # Keep only user columns that match our candidate set.
                existing_user = [x for x in existing_user if x in candidates]
                if existing_user != fts_cols:
                    recreate = True
        except Exception:
            recreate = True

        if recreate:
            # Drop triggers first, then the FTS table.
            for trg in ("tracks_ai", "tracks_ad", "tracks_au"):
                try:
                    c.execute(f"DROP TRIGGER IF EXISTS {trg}")
                except Exception:
                    pass
            try:
                c.execute("DROP TABLE IF EXISTS tracks_fts")
            except Exception:
                pass

        cols_sql = ", ".join(fts_cols)
        c.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
                {cols_sql},
                content='tracks',
                content_rowid='id'
            )
        """)

        insert_cols_sql = ", ".join(fts_cols)
        insert_vals_new = ", ".join([f"new.{col}" for col in fts_cols])
        insert_vals_old = ", ".join([f"old.{col}" for col in fts_cols])

        c.execute(f"""
            CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
                INSERT INTO tracks_fts(rowid, {insert_cols_sql})
                VALUES (new.id, {insert_vals_new});
            END;
        """)
        c.execute(f"""
            CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, {insert_cols_sql})
                VALUES('delete', old.id, {insert_vals_old});
            END;
        """)
        c.execute(f"""
            CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
                INSERT INTO tracks_fts(tracks_fts, rowid, {insert_cols_sql})
                VALUES('delete', old.id, {insert_vals_old});
                INSERT INTO tracks_fts(rowid, {insert_cols_sql})
                VALUES (new.id, {insert_vals_new});
            END;
        """)

        # Populate / rebuild index from existing tracks (safe to run multiple times)
        c.execute("INSERT INTO tracks_fts(tracks_fts) VALUES('rebuild')")
        conn.commit()
        return True

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False

import logging
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using the in-memory storage for tracking rate limits"
)


_LOG_LEVEL = logging.WARNING if _RUNTIME_LOGGING_ENABLED else logging.ERROR
_WERKZEUG_LOG_LEVEL = logging.ERROR

logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
# Normal operation suppresses routine warning traffic but keeps genuine
# ERROR/CRITICAL failures visible on the console. DEBUG=1 restores the existing
# warning/error policy without enabling any additional debug/verbose logging.
logging.disable(logging.NOTSET)

logging.getLogger("werkzeug").setLevel(_WERKZEUG_LOG_LEVEL)



def _apply_log_levels(target_logger=None):
    """Apply the production warning/error log-level policy."""
    try:
        (target_logger or logging.getLogger()).setLevel(_LOG_LEVEL)
    except Exception:
        pass
    try:
        logging.getLogger("werkzeug").setLevel(_WERKZEUG_LOG_LEVEL)
    except Exception:
        pass


import os
import re
import json
import signal
import sys


# Normalize handler formatters for werkzeug to avoid ANSI color sequences appearing in logs (strip color codes).
try:
    import logging as _logging_local, re as _re_local
    _werk = _logging_local.getLogger('werkzeug')

    class _SuppressNoisyEndpointFilter(_logging_local.Filter):
        def filter(self, record):
            try:
                msg = record.getMessage()
            except Exception:
                pass
            return True

    _suppress_noisy_endpoint_filter = _SuppressNoisyEndpointFilter()
    _werk.addFilter(_suppress_noisy_endpoint_filter)
    for _h in list(_werk.handlers):
        try:
            _h.addFilter(_suppress_noisy_endpoint_filter)
        except Exception:
            pass
        try:
            _h.setFormatter(_logging_local.Formatter('%(asctime)s %(levelname)s %(message)s'))
        except Exception:
            pass
except Exception:
    pass

# Keep the root logger aligned with the central runtime logging gate.
try:
    import logging as _logging_root
    _logging_root.getLogger().setLevel(_LOG_LEVEL)
except Exception:
    pass



# --- Terminal safety: ensure ANSI attributes are reset on exit (prevents stuck colors in screen) ---
import atexit
import signal

def _reset_terminal_ansi() -> None:
    try:
        sys.sys.stdout.flush()
    except Exception:
        pass
    try:
        sys.stderr.write("\x1b[0m")
        sys.stderr.flush()
    except Exception:
        pass

# Always reset at process exit.
atexit.register(_reset_terminal_ansi)

def _install_terminal_signal_handlers() -> None:
    def _handler(signum, frame):
        _reset_terminal_ansi()
        # Re-raise default handler to preserve expected Ctrl+C behavior
        try:
            signal.signal(signum, signal.SIG_DFL)
        except Exception:
            pass
        os.kill(os.getpid(), signum)

    for _sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if _sig is None:
            continue
        try:
            signal.signal(_sig, _handler)
        except Exception:
            pass

_install_terminal_signal_handlers()
import sqlite3
import shutil
import threading
import copy
from collections.abc import MutableMapping
import time
import logging
import errno
import math
from typing import Any, Mapping, Optional
from pathlib import Path
from functools import wraps
from datetime import datetime, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.serving import WSGIRequestHandler


def _utc_now_naive() -> datetime:
    """Return current UTC while preserving the existing naive database format."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# These socket errors mean that the HTTP client disappeared while Werkzeug was
# writing or flushing a response. They are normal client disconnects, not Web
# Broadcaster failures, so keep them out of the production console while still
# allowing every unrelated server exception to be reported.
_IGNORABLE_CLIENT_SOCKET_ERRNOS = frozenset({
    errno.EPIPE,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.ETIMEDOUT,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
})


def _is_ignorable_client_socket_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in _IGNORABLE_CLIENT_SOCKET_ERRNOS


class _WebBroadcasterRequestHandler(WSGIRequestHandler):
    """Treat a vanished HTTP client as a normal disconnected request."""

    def handle(self) -> None:
        try:
            super().handle()
        except OSError as exc:
            if _is_ignorable_client_socket_error(exc):
                return
            raise

    def finish(self) -> None:
        try:
            super().finish()
        except OSError as exc:
            if _is_ignorable_client_socket_error(exc):
                return
            raise


class NoActiveStationError(Exception):
    """Raised when a station-scoped DB operation is requested without an active station."""
    pass



def _safe_dirname(name: str) -> str:
    """Filesystem-safe directory name for per-station state."""
    s = (name or "").strip() or "unknown"
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:120]


def _bundled_soundsolution_path(*parts: str) -> Path:
    """Resolve one file inside the application-local in-process DSP runtime."""
    return (Path(BASE_DIR) / "bin" / "soundsolution" / Path(*parts)).resolve()


def get_bundled_soundsolution_library_path() -> str:
    """Return the application-local SoundSolution shared-library path."""
    return str(_bundled_soundsolution_path("libsoundsolution.so.2"))


def get_default_soundsolution_path() -> str:
    """Return the bundled SoundSolution ``.dat`` configuration path."""
    return str(_bundled_soundsolution_path("ss18.dat"))


def _normalize_soundsolution_config_setting(configured_path: str | None) -> str:
    """Map an empty or older bundled DSP setting to the current native ``.dat``.

    The existing ``settings.ssproc_appimage`` column remains the canonical
    database field so no schema migration is required. It stores a station
    SoundSolution ``.dat`` path. Older AppImage and v6012 package-local paths
    transparently resolve to the bundled bit-exact configuration.
    """
    configured = str(configured_path or "").strip()
    if not configured:
        return get_default_soundsolution_path()
    candidate = Path(configured)
    if candidate.suffix.lower() == ".appimage" or candidate.name == "ssproc-WB.AppImage":
        return get_default_soundsolution_path()
    if (
        not candidate.is_file()
        and candidate.name == "ss18.dat"
        and any(part in {"soundsolution-native", "soundsolution"} for part in candidate.parts)
    ):
        return get_default_soundsolution_path()
    return configured


def get_soundsolution_config_path() -> str:
    """Read the canonical native SoundSolution ``.dat`` path from settings."""
    settings = get_settings()
    configured = ""
    if settings:
        try:
            if isinstance(settings, dict):
                configured = str(settings.get("ssproc_appimage") or "").strip()
            else:
                configured = str(settings["ssproc_appimage"] or "").strip()
        except Exception:
            configured = ""
    return _normalize_soundsolution_config_setting(configured)


def prepare_native_soundsolution_runtime() -> dict[str, str]:
    """Validate the in-process SoundSolution library and station configuration.

    The multi-station native daemon links directly to ``libsoundsolution.so.2``
    and owns one opaque DSP context per station. There is no DSP executable,
    subprocess, pipe bridge or separate DSP log.
    """
    if not get_dsp_enabled(default=True):
        raise RuntimeError("SoundSolution DSP is disabled in station settings.")

    library_path = Path(get_bundled_soundsolution_library_path()).resolve()
    if not (library_path.is_file() and os.access(library_path, os.R_OK)):
        raise RuntimeError(f"Native SoundSolution library is not readable: {library_path}")

    config_path = Path(get_soundsolution_config_path()).expanduser().resolve()
    if not (config_path.is_file() and os.access(config_path, os.R_OK)):
        raise RuntimeError(f"SoundSolution .dat configuration is not readable: {config_path}")

    return {"dsp_config_path": str(config_path)}

def guess_metadata_from_filename(path_or_name: str) -> dict:
    """Generate basic (artist, title) metadata from a filename.

    This is used as a fallback when the audio file and native descriptor have no
    usable title/artist fields. Common patterns:
      - "Artist - Title.ext"
      - "Artist-Title.ext"
      - "01 - Artist - Title.ext"
    """
    try:
        name = os.path.basename(path_or_name or "")
        # Strip extension
        name = re.sub(r"\.[^.]+$", "", name)
        # Normalize separators
        name = name.replace("%20", " ").replace("_", " ").strip()
        name = re.sub(r"\s+", " ", name).strip()

        # Drop a leading track number prefix like "01 - " / "01." / "01_"
        name = re.sub(r"^\s*\d+\s*[-._]\s*", "", name).strip()

        artist = ""
        title = name

        if " - " in name:
            a, t = name.split(" - ", 1)
            if a.strip() and t.strip():
                artist, title = a.strip(), t.strip()
        elif "-" in name:
            a, t = name.split("-", 1)
            if a.strip() and t.strip():
                artist, title = a.strip(), t.strip()

        return {"artist": artist, "title": title}
    except Exception:
        return {"artist": "", "title": (path_or_name or "").strip()}


def _normalize_year_metadata(value) -> str:
    """Return a clean 4-digit year or an empty string.

    Some tags contain whitespace-only year/date fields. Those must be treated
    as missing so stream metadata never gets an empty "(    )" suffix.
    """
    try:
        raw = str(value or "").strip()
        if not raw:
            return ""
        m = re.search(r"(19|20)\d{2}", raw)
        return m.group(0) if m else ""
    except Exception:
        return ""



_MEDIA_METADATA_CACHE_LOCK = threading.Lock()
_MEDIA_METADATA_CACHE: dict[str, dict] = {}
_MEDIA_METADATA_CACHE_MAX = 512


def _media_metadata_cache_key(path: str) -> tuple[str, int, int] | None:
    """Return a stable cache key for local media metadata reads."""
    try:
        p = str(path or "").strip()
        if not p or p.startswith("http://") or p.startswith("https://"):
            return None
        st = os.stat(p)
        return (p, int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1000000000))), int(st.st_size))
    except Exception:
        return None


def _get_cached_media_metadata(path: str) -> dict | None:
    """Return cached local media metadata without opening the media file."""
    key = _media_metadata_cache_key(path)
    if key is None:
        return None
    cache_key = repr(key)
    try:
        with _MEDIA_METADATA_CACHE_LOCK:
            item = _MEDIA_METADATA_CACHE.get(cache_key)
            if not item:
                return None
            # Move-to-end behavior with a plain dict.
            _MEDIA_METADATA_CACHE.pop(cache_key, None)
            _MEDIA_METADATA_CACHE[cache_key] = item
            return dict(item)
    except Exception:
        return None


def _set_cached_media_metadata(path: str, metadata: dict) -> None:
    """Store local media metadata and keep the cache bounded."""
    key = _media_metadata_cache_key(path)
    if key is None:
        return
    cache_key = repr(key)
    try:
        with _MEDIA_METADATA_CACHE_LOCK:
            _MEDIA_METADATA_CACHE[cache_key] = dict(metadata or {})
            while len(_MEDIA_METADATA_CACHE) > _MEDIA_METADATA_CACHE_MAX:
                try:
                    oldest = next(iter(_MEDIA_METADATA_CACHE))
                    _MEDIA_METADATA_CACHE.pop(oldest, None)
                except StopIteration:
                    break
    except Exception:
        pass


def read_media_metadata(path_or_name: str) -> dict:
    """Best-effort media tag read for UI metadata fallback.

    Returns title/artist/album/year when available. Falls back to filename parsing
    for title/artist if tags are missing. This is UI-only and does not affect the
    outgoing stream metadata format.
    """
    out = {"title": "", "artist": "", "album": "", "year": ""}
    try:
        path = str(path_or_name or "").strip()
        if not path:
            return out

        cached = _get_cached_media_metadata(path)
        if cached is not None:
            return cached

        try:
            from mutagen import File as _MutagenFile  # type: ignore
            mf = _MutagenFile(path)
        except Exception:
            mf = None

        def _first_tag(*keys: str) -> str:
            if mf is None:
                return ""
            try:
                tags = getattr(mf, "tags", None)
                if not tags:
                    return ""
                for key in keys:
                    if key in tags:
                        val = tags.get(key)
                        if isinstance(val, (list, tuple)):
                            val = val[0] if val else ""
                        if hasattr(val, 'text'):
                            txt = getattr(val, 'text', None)
                            if isinstance(txt, (list, tuple)):
                                val = txt[0] if txt else ""
                        s = str(val or "").strip()
                        if s:
                            return s
            except Exception:
                return ""
            return ""

        out["title"] = _first_tag("title", "TIT2", "TITLE")
        out["artist"] = _first_tag("artist", "TPE1", "ARTIST", "albumartist", "ALBUMARTIST")
        out["album"] = _first_tag("album", "TALB", "ALBUM")
        year_val = _first_tag("date", "year", "originaldate", "TDRC", "TYER", "YEAR")
        out["year"] = _normalize_year_metadata(year_val)

        if not out["artist"] or not out["title"]:
            guessed = guess_metadata_from_filename(path)
            if not out["artist"]:
                out["artist"] = str(guessed.get("artist") or "").strip()
            if not out["title"]:
                out["title"] = str(guessed.get("title") or "").strip()
        _set_cached_media_metadata(path, out)
    except Exception:
        pass
    return out

def parse_embedded_querystring_meta(title: str, artist: str) -> tuple[str, str]:
    """Parse legacy Icecast edge-case where the entire querystring is stuffed into the title.

    Example incoming title:
        title=Charles+McThorn+-+Winds+Will+Turn+%28Edit%29&artist=

    Returns (title, artist) decoded with unquote_plus. If parsing fails, returns inputs.
    """
    try:
        t = (title or "").strip().strip('"')
        if not (t.startswith("title=") and "&artist=" in t):
            return (title or "", artist or "")
        from urllib.parse import parse_qs, unquote_plus
        qs = parse_qs(t, keep_blank_values=True, strict_parsing=False)
        t_dec = unquote_plus((qs.get("title") or [""])[0] or "")
        a_dec = unquote_plus((qs.get("artist") or [""])[0] or "")
        out_title = t_dec.strip() or (title or "").strip()
        out_artist = a_dec.strip() or (artist or "").strip()
        return (out_title, out_artist)
    except Exception:
        return (title or "", artist or "")


def split_combined_artist_title(title: str, artist: str) -> tuple[str, str]:
    """Split a single combined stream title like 'Artist - Title' for UI use."""
    try:
        t = str(title or "").strip().strip('"')
        a = str(artist or "").strip().strip('"')
        if not t or a:
            return (t, a)
        for sep in (" - ", " – ", " | ", ": "):
            if sep in t:
                left, right = t.split(sep, 1)
                left = left.strip()
                right = right.strip()
                if left and right:
                    return (right, left)
        return (t, a)
    except Exception:
        return (str(title or "").strip(), str(artist or "").strip())

def normalize_media_path(value: str) -> str:
    """Normalize URI-style file references into a plain filesystem path.

    Handles common forms:
      - file:///abs/path
      - file://abs/path
      - annotate:...:/abs/path
    """
    try:
        s = str(value or "").strip()
        if not s:
            return ""
        if s.startswith("file://"):
            # Keep exactly one leading slash for file:// URIs.
            s = s[7:]
            if s.startswith("/"):
                return s
            return "/" + s
        if s.startswith("annotate:"):
            # Our generated M3U entries use annotate:...:<path>
            # Split from the right to keep any ':' inside metadata part.
            parts = s.rsplit(":", 1)
            if len(parts) == 2:
                s = parts[1]
        return s
    except Exception:
        return str(value or "").strip()

# The managed native engine and its FFmpeg/DSP child processes must remain
# waitable.  Ignoring SIGCHLD makes the kernel auto-reap them, causing
# successful child commands to look like waitpid(ECHILD) failures.
signal.signal(signal.SIGCHLD, signal.SIG_DFL)


# Simple TCP PCM relay to feed multiple encoder processes on 127.0.0.1:12345


def get_base_dir() -> str:
    """Return the application base directory.

    When running from a PyInstaller build, runtime files must live next to the
    executable, not inside the bundled _internal directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = get_base_dir()
DB_DIR = os.path.join(BASE_DIR, "db")


os.makedirs(DB_DIR, exist_ok=True)

# Global SQLite database (stores users + station registry).
GLOBAL_DATABASE = os.path.join(DB_DIR, "web-broadcaster.db")


def _resolve_station_id_to_db(station_id: str) -> str:
    """Accept either a DB filename (preferred) or a numeric station id, return db filename or ''."""
    sid = (station_id or "").strip()
    if not sid:
        return ""
    if sid.endswith(".db"):
        return os.path.basename(sid)
    try:
        sid_int = int(sid)
    except Exception:
        return ""
    try:
        stations = get_registered_stations() or []
        for st in stations:
            try:
                if str(st.get("id")) == str(sid_int):
                    return os.path.basename(st.get("db_filename") or "")
            except Exception:
                continue
    except Exception:
        pass
    return ""


def resolve_station_db_path(filename: Optional[str]) -> str:
    """Resolve a station DB filename to an absolute path under DB_DIR."""
    if filename:
        fn = os.path.basename(str(filename))
        if fn.endswith(".db"):
            return os.path.join(DB_DIR, fn)
    return ""


def get_active_station_db_path() -> str:
    """Return the active station DB path.

    A thread-scoped engine/callback override takes precedence so concurrent
    stations never fall back to the process-global active selection.

    Order of preference:
    0) thread-scoped station runtime override
    1) current request session (active_station_db)
    2) global app_state (active_station_db)
    3) global app_state (last_station_db)
    4) first registered station (auto-select, for background threads)
    """
    override = _station_runtime_override()
    if override:
        resolved_override = resolve_station_db_path(override)
        try:
            if resolved_override and os.path.exists(resolved_override):
                return resolved_override
        except Exception:
            pass

    active = None

    # 1) Prefer the current web session (request context).
    try:
        active = session.get("active_station_db")
    except Exception:
        active = None

    # 2) Fall back to globally stored active station (works for background threads).
    if not active:
        try:
            active = _app_state_get("active_station_db") or ""
        except Exception:
            active = ""

    # If still empty, use the last valid station selected by this installation.
    if not active:
        try:
            active = _app_state_get("last_station_db") or ""
        except Exception:
            active = ""

    active = os.path.basename(str(active or "").strip())
    if active and not active.endswith(".db"):
        active = ""

    # Resolve to a full path and validate existence.
    resolved = resolve_station_db_path(active)
    try:
        if active and resolved and os.path.exists(resolved):
            return resolved
    except Exception:
        pass

    # 4) Auto-select the first *valid* station if one exists (important for background threads).
    try:
        init_global_db()
        stations = build_station_list() or []
        for st in stations:
            sid = st.get("id") if isinstance(st, dict) else str(st)
            sid = os.path.basename(str(sid or "").strip())
            if not sid.endswith(".db"):
                continue
            p = resolve_station_db_path(sid)
            if p and os.path.exists(p):
                try:
                    _app_state_set("active_station_db", sid)
                    _app_state_set("last_station_db", sid)
                except Exception:
                    pass
                return p
    except Exception:
        pass

    # No active station selected.
    return resolve_station_db_path("")
def ensure_active_station_selected() -> Optional[str]:
    """Ensure there is an active station in session + global app_state; return station_id or None."""
    try:
        init_global_db()
    except Exception:
        pass

    # If session already has one, validate and mirror to app_state.
    try:
        sid = os.path.basename(str(session.get("active_station_db") or "").strip())
    except Exception:
        sid = ""
    if sid and sid.endswith(".db"):
        try:
            p = resolve_station_db_path(sid)
            if p and os.path.exists(p):
                try:
                    _app_state_set("active_station_db", sid)
                    _app_state_set("last_station_db", sid)
                except Exception:
                    pass
                return sid
        except Exception:
            pass

    # Fall back to global state.
    sid = os.path.basename(str(_app_state_get("active_station_db") or "").strip())
    if (not sid) or (not sid.endswith(".db")):
        sid = os.path.basename(str(_app_state_get("last_station_db") or "").strip())

    # If still none, pick first station if exists.
    if (not sid) or (not sid.endswith(".db")):
        try:
            stations = build_station_list()
        except Exception:
            stations = []
        if stations:
            # stations entries might be dicts {id,name}; use id if present else assume string
            first = stations[0].get("id") if isinstance(stations[0], dict) else str(stations[0])
            sid = os.path.basename(str(first or "").strip())

    # Validate
    if sid and sid.endswith(".db"):
        try:
            p = resolve_station_db_path(sid)
            if p and os.path.exists(p):
                try:
                    session["active_station_db"] = sid
                except Exception:
                    pass
                try:
                    _app_state_set("active_station_db", sid)
                    _app_state_set("last_station_db", sid)
                except Exception:
                    pass
                return sid
        except Exception:
            pass
    return None


def get_active_station_key() -> str:
    """Return the active station key used for per-station process bookkeeping.
    We use the active station DB filename (e.g. 'Teszt1.db') as the stable key.
    """
    try:
        sid = os.path.basename(get_active_station_db_path() or "").strip()
        return sid if sid.endswith(".db") else ""
    except Exception:
        return ""


def read_station_display_name(db_path: str) -> str:
    """Read a station's display name (radio_name) from its settings table."""
    try:
        if not os.path.exists(db_path):
            return ""
        conn = _connect_sqlite_reusable(db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")
        if c.fetchone() is None:
            conn.close()
            return ""
        c.execute("SELECT radio_name FROM settings ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if row is None:
            return ""
        try:
            return (row["radio_name"] or "").strip()
        except Exception:
            return ""
    except Exception:
        return ""


def build_station_list() -> list[dict]:
    """Build a list of stations for the UI dropdown."""
    return get_registered_stations()


def _generate_station_db_filename(station_name: str) -> str:
    """Generate the canonical station DB filename based on the requested station name."""
    base = _safe_dirname((station_name or '').strip() or 'station').strip('._-') or 'station'
    return f"db-{base}.db"




def _get_sqlite_sidecar_paths(db_path: str) -> list[str]:
    db_path = str(db_path or '').strip()
    if not db_path:
        return []
    return [f"{db_path}-wal", f"{db_path}-shm"]



def _checkpoint_sqlite_database(db_path: str) -> None:
    """Flush WAL content back into the main DB so renames do not leave orphan sidecars."""
    db_path = str(db_path or '').strip()
    if not db_path or not os.path.exists(db_path):
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except Exception:
            try:
                conn.execute('PRAGMA wal_checkpoint(FULL)')
            except Exception:
                pass
        try:
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass



def _remove_sqlite_sidecars(db_path: str) -> None:
    for sidecar_path in _get_sqlite_sidecar_paths(db_path):
        try:
            if os.path.exists(sidecar_path):
                os.remove(sidecar_path)
        except Exception:
            pass



def _clone_station_database_for_rename(source_db_filename: str, new_station_name: str) -> tuple[str, str]:
    """Clone an existing station DB into a new station DB and retitle station-local settings."""
    source_filename = os.path.basename(str(source_db_filename or '').strip())
    if not source_filename.endswith('.db'):
        raise ValueError('Invalid source station.')

    source_path = resolve_station_db_path(source_filename)
    if not source_path or not os.path.exists(source_path):
        raise ValueError('Source station database was not found.')

    _checkpoint_sqlite_database(source_path)

    target_filename = _generate_station_db_filename(new_station_name)
    target_path = resolve_station_db_path(target_filename)
    if not target_path:
        raise ValueError('Could not allocate a destination database path.')

    shutil.copy2(source_path, target_path)

    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        now_iso = datetime.now().isoformat()
        try:
            c.execute(
                "UPDATE settings SET radio_name = ?, updated_at = COALESCE(updated_at, ?) WHERE id = (SELECT id FROM settings ORDER BY id DESC LIMIT 1)",
                (new_station_name, now_iso),
            )
        except Exception:
            c.execute(
                "UPDATE settings SET radio_name = ? WHERE id = (SELECT id FROM settings ORDER BY id DESC LIMIT 1)",
                (new_station_name,),
            )
        conn.commit()
    finally:
        conn.close()

    _checkpoint_sqlite_database(target_path)
    _remove_sqlite_sidecars(target_path)

    return target_filename, target_path

TEMP_RUNTIME_SUBDIR = "temp"


def get_runtime_dir() -> str:
    """
    Return the directory under the current working directory where all
    runtime-generated files (station playlists, staging, etc.)
    are stored. The directory is created if it does not exist.
    """
    # IMPORTANT: use the directory that contains this application, not the
    # current working directory. In production (systemd, supervisors, etc.)
    # the CWD can be different, causing runtime artifacts (configs, playlists)
    # to be generated into an unexpected location.
    base_dir = BASE_DIR
    runtime_dir = os.path.join(base_dir, TEMP_RUNTIME_SUBDIR)
    os.makedirs(runtime_dir, exist_ok=True)
    return runtime_dir








app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "html"),
    static_folder=os.path.join(BASE_DIR, "html", "static"),
)

@app.context_processor
def inject_app_version():
    return {"app_version": APP_VERSION}


@app.before_request
def _ensure_station_in_session():
    # Persist station id for per-station DB lookups.
    try:
        if session.get("station_id"):
            return
        # common alternatives
        for key in ("current_station_id", "nav_station_id"):
            if session.get(key):
                session["station_id"] = session.get(key)
                return
        # accept from query param if provided (e.g., initial navigation)
        sid = request.args.get("station_id", type=int)
        if sid:
            session["station_id"] = sid
    except Exception:
        pass


# Rate limiting for sensitive endpoints (login, setup, user creation).
# Defaults are intentionally empty to avoid impacting normal UI/API usage.
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
)


_apply_log_levels(app.logger)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_first_run() and request.endpoint != "setup":
            return redirect(url_for("setup"))

        if "user_id" not in session:
            return redirect(url_for("login"))

        # If the user is logged in but no stations exist yet, keep the UI in the
        # empty-state screen. API endpoints should handle this gracefully on their own.
        try:
            empty_state_endpoints = {
                "dashboard",
                "broadcaster",
                "new_station",
                "settings",
                "logout",
            }
            if (not request.path.startswith("/api/")) and request.endpoint not in empty_state_endpoints:
                if not build_station_list():
                    return redirect(url_for("dashboard"))
        except Exception:
            pass
        return f(*args, **kwargs)

    return decorated_function


@app.route('/api/console/stream')
@login_required
def console_event_stream():
    """Read-only stdout/stderr mirror for the authenticated Settings console.

    The stream uses the application's existing Server-Sent Events stack, so no
    additional WebSocket package is required. Console chunks are JSON encoded to
    preserve ANSI escape sequences, carriage returns and embedded newlines.
    """
    subscriber, snapshot = CONSOLE_HUB.subscribe()

    def _encode_console_chunk(chunk: str) -> str:
        return f"data: {json.dumps(str(chunk), ensure_ascii=False)}\n\n"

    def _stream():
        try:
            yield "retry: 2000\n\n"
            # Avoid sending the complete scrollback as one oversized SSE event.
            for offset in range(0, len(snapshot), 32768):
                yield _encode_console_chunk(snapshot[offset:offset + 32768])
            while True:
                try:
                    chunk = subscriber.get(timeout=15.0)
                except _console_queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield _encode_console_chunk(chunk)
        except GeneratorExit:
            return
        except Exception:
            return
        finally:
            CONSOLE_HUB.unsubscribe(subscriber)

    return Response(
        _stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _honeypot_triggered(form_or_payload: dict) -> bool:
    """Return True if the hidden honeypot field was filled.

    We use a generic field name ("website") across forms.
    """
    try:
        v = (form_or_payload.get("website") or "").strip()
        return bool(v)
    except Exception:
        return False



@app.route("/")
def root():
    """Root entrypoint redirects authenticated users to the Studio dashboard."""
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return redirect(url_for("dashboard"))


def build_new_station_defaults() -> dict:
    """Return canonical native-only defaults for the Add New Station modal."""
    return {
        "gap_killer_start_dbfs": -20.0,
        "gap_killer_end_dbfs": -24.0,
        "crossfade_trigger_relative_db": -7.0,
        "crossfade_fallback_seconds": 3.0,
        "crossfade_min_seconds": 0.1,
        "crossfade_max_seconds": 6.0,
        "crossfade_fade_out_seconds": 5.0,
        "no_crossfade_max_duration_sec": 65.0,
        "radio_name": "",
        "music_library_path": "",
        "soundsolution_path": get_default_soundsolution_path(),
        "dsp_enabled": 1,
    }


@app.route("/dashboard")
@login_required
def dashboard():
    """Studio dashboard."""
    new_station_defaults = build_new_station_defaults()
    try:
        get_db().close()
    except NoActiveStationError:
        return render_template("no_stations.html", hide_navbar=True, new_station_defaults=new_station_defaults)
    except Exception:
        pass
    return render_template(
        "dashboard.html",
        hide_navbar=True,
        new_station_defaults=new_station_defaults,
    )



@app.route("/stations/<path:station_id>/audio-engine/<cmd>", methods=["POST"], endpoint="station_audio_engine_command")
@login_required
def station_audio_engine_command(station_id: str, cmd: str):
    """Start/stop a specific station from the Stations Dashboard without switching the user's active station."""
    cmd_norm = (cmd or "").strip().lower()
    if cmd_norm not in ("start", "stop"):
        return jsonify({"success": False, "error": "invalid_command"}), 400

    resolved = _resolve_station_id_to_db(station_id)
    if not resolved or not resolved.endswith(".db"):
        return jsonify({"success": False, "error": "invalid_station"}), 400

    prev_station = session.get("active_station_db")
    session["active_station_db"] = resolved

    try:
        with station_runtime_context(resolved):
            # Ensure the station DB schema exists (safe no-op if already initialized).
            try:
                init_db()
            except Exception:
                pass

            if cmd_norm == "start":
                return station_start()
            return station_stop()
    finally:
        if prev_station:
            session["active_station_db"] = prev_station
        else:
            try:
                session.pop("active_station_db", None)
            except Exception:
                pass



def get_audio_engine_started_at_for_station(station_key: str) -> Optional[str]:
    """Return the native station start timestamp from the global runtime table."""
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT started_at FROM audio_engine_state WHERE station_key = ?", (station_key or "",))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return row["started_at"]
        except Exception:
            return row[0]
    except Exception:
        return None


def set_audio_engine_started_at_for_station(station_key: str, started_at: str | None = None) -> None:
    """Persist native station start time for dashboard uptime display."""
    key = str(station_key or "").strip()
    if not key:
        return
    value = str(started_at or datetime.now().isoformat(timespec="seconds"))
    try:
        conn = get_global_db()
        conn.execute(
            "INSERT OR REPLACE INTO audio_engine_state (station_key, started_at) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def clear_audio_engine_started_at_for_station(station_key: str) -> None:
    """Clear persisted native station start time after a successful stop."""
    key = str(station_key or "").strip()
    if not key:
        return
    try:
        conn = get_global_db()
        conn.execute("DELETE FROM audio_engine_state WHERE station_key = ?", (key,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def is_station_running(station_key: str) -> bool:
    """Return whether the station is running in the native audio daemon."""
    try:
        return bool(_native_station_state(str(station_key or "").strip()).get("running"))
    except Exception:
        return False



@app.route("/stations/<path:station_id>/delete", methods=["POST"])
@login_required
def delete_station(station_id: str):
    """Delete a station completely (DB + runtime artifacts)."""
    try:
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "unauthorized"}), 401

        payload = request.get_json(silent=True) or {}
        password = str(payload.get("password") or "")

        # Verify current user's password for safety.
        try:
            gconn = get_global_db()
            gconn.row_factory = sqlite3.Row
            cur = gconn.cursor()
            cur.execute("SELECT * FROM users WHERE id = ?", (session.get("user_id"),))
            user = cur.fetchone()
            gconn.close()
        except Exception:
            user = None

        if not user or (not check_password_hash(user["password_hash"], password)):
            return jsonify({"success": False, "error": "invalid_password"}), 403

        db_filename = _resolve_station_id_to_db(station_id)
        if not db_filename:
            return jsonify({"success": False, "error": "station_not_found"}), 404

        # Do not allow deletion while running (UI hides it, but enforce server-side too).
        if is_station_running(db_filename):
            return jsonify({"success": False, "error": "station_running"}), 400

        # Look up station name before deletion for filesystem cleanup.
        station_name = ""
        try:
            gconn = get_global_db()
            gconn.row_factory = sqlite3.Row
            cur = gconn.cursor()
            cur.execute("SELECT name FROM stations WHERE db_filename = ?", (db_filename,))
            row = cur.fetchone()
            station_name = (row["name"] if row else "") or ""
            gconn.close()
        except Exception:
            station_name = ""

        # Delete from global DB (registry + process pid rows).
        try:
            gconn = get_global_db()
            cur = gconn.cursor()
            cur.execute("DELETE FROM stations WHERE db_filename = ?", (db_filename,))
            cur.execute("DELETE FROM audio_engine_state WHERE station_key = ?", (db_filename,))
            gconn.commit()
            gconn.close()
        except Exception:
            pass

        # If this station was selected as active, clear app_state so selection can fall back.
        try:
            if os.path.basename(str(_app_state_get("active_station_db") or "")) == os.path.basename(db_filename):
                _app_state_set("active_station_db", "")
            if os.path.basename(str(_app_state_get("last_station_db") or "")) == os.path.basename(db_filename):
                _app_state_set("last_station_db", "")
        except Exception:
            pass

        try:
            if os.path.basename(str(session.get("active_station_db") or "")) == os.path.basename(db_filename):
                session["active_station_db"] = ""
        except Exception:
            pass

        # Delete station DB file.
        try:
            db_path = resolve_station_db_path(db_filename)
            if db_path and os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            pass

        # Delete per-station runtime artifacts under ./temp

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": "exception", "detail": str(e)}), 500

@app.route("/api/dashboard_overview", methods=["GET"])
def api_dashboard_overview():
    """Return native status, uptime and now-playing data for every station."""
    try:
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "unauthorized"}), 401

        stations = build_station_list() or []
        out = []
        now_ts = datetime.now().timestamp()

        for st in stations:
            try:
                station_key = (st.get("db_filename") or st.get("db") or st.get("db_file") or st.get("id") or "")
                name = st.get("name") or station_key or "Unknown"
            except Exception:
                station_key = ""
                name = "Unknown"

            native_state = _native_station_state(station_key) if station_key else {}
            running = bool(native_state.get("running"))
            progress = {"elapsed": None, "total": None}
            if running and station_key:
                try:
                    native_status = _native_api_status_payload(station_key, native_state, with_progress=True)
                    song = native_status.get("song") if isinstance(native_status, dict) else None
                    if isinstance(song, dict):
                        progress = {
                            "elapsed": song.get("elapsed"),
                            "total": song.get("duration"),
                        }
                except Exception:
                    progress = {"elapsed": None, "total": None}

            started_at = get_audio_engine_started_at_for_station(station_key) if running and station_key else None
            uptime_sec = None
            if started_at:
                try:
                    dt = datetime.fromisoformat(str(started_at))
                    uptime_sec = max(0, int(now_ts - dt.timestamp()))
                except Exception:
                    uptime_sec = None

            with NOW_PLAYING_LOCK:
                store = _get_now_playing_store(station_key or "")
                title = store.get("title", "") or ""
                artist = store.get("artist", "") or ""
                album = store.get("album", "") or ""
                updated_at = float(store.get("updated_at") or 0.0)

            title, artist = parse_embedded_querystring_meta(title, artist)
            title, artist = split_combined_artist_title(title, artist)

            out.append(
                {
                    "station_key": station_key,
                    "name": name,
                    "running": running,
                    "paused": bool(native_state.get("paused")),
                    "started_at": started_at,
                    "uptime_sec": uptime_sec,
                    "progress": progress,
                    "now_playing": {
                        "title": title,
                        "artist": artist,
                        "album": album,
                        "updated_at": updated_at,
                    },
                }
            )

        cpu_usage = None
        try:
            cpu_usage = float(get_total_cpu_usage_percent(0.1))
        except Exception:
            cpu_usage = None

        return jsonify({"success": True, "stations": out, "cpu_usage": cpu_usage})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _read_total_cpu_times():
    """Read aggregate CPU times from /proc/stat.

    Returns a tuple of (idle_time, total_time) for all CPU cores combined,
    or None if the data cannot be read.
    """
    try:
        with open("/proc/stat", "r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
        if not first_line.startswith("cpu "):
            return None
        parts = first_line.split()
        values = [int(v) for v in parts[1:]]
        if len(values) < 4:
            return None
        idle_time = values[3]
        if len(values) > 4:
            idle_time += values[4]
        total_time = sum(values)
        return idle_time, total_time
    except Exception:
        return None


def get_total_cpu_usage_percent(sample_seconds: float = 0.1):
    """Return total CPU usage percentage for all cores combined using /proc/stat."""
    first = _read_total_cpu_times()
    if not first:
        return None
    try:
        time.sleep(max(0.0, float(sample_seconds)))
    except Exception:
        time.sleep(0.1)
    second = _read_total_cpu_times()
    if not second:
        return None
    idle_delta = second[0] - first[0]
    total_delta = second[1] - first[1]
    if total_delta <= 0:
        return None
    usage = 100.0 * (1.0 - (idle_delta / total_delta))
    return max(0.0, min(100.0, usage))


# Console logging
logging.basicConfig(level=_LOG_LEVEL, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("web_broadcaster")

def _safe_call(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


# Keep application and root loggers on the production warning/error policy.
logger.setLevel(_LOG_LEVEL)
logging.getLogger().setLevel(_LOG_LEVEL)





def _ensure_runtime_playback_state_schema(conn) -> None:
    PlaybackRepository.ensure_runtime_playback_state_schema(conn)


def _runtime_song_key(song: dict) -> str:
    return PlaybackRepository.song_key(song)


def _runtime_get_state_row(conn) -> dict:
    return PlaybackRepository.get_runtime_state_row(conn)


def _runtime_mark_history_commit(
    conn,
    song: dict,
    queue_id: int,
    track_id: int,
    played_at: str,
    station_key: str = "",
) -> None:
    _get_playback_repository().mark_history_commit(
        conn,
        song,
        queue_id,
        track_id,
        played_at,
        station_key=station_key,
    )



# ----------------------
# Playback repository bridge
# ----------------------

_PLAYBACK_REPOSITORY_LOCK = threading.RLock()
_PLAYBACK_REPOSITORY: Optional[PlaybackRepository] = None


def _playback_repo_open_connection(station_key: str):
    sk = str(station_key or "").strip()
    return get_db_for_station(sk) if sk else get_db()




def _get_playback_repository() -> PlaybackRepository:
    global _PLAYBACK_REPOSITORY
    with _PLAYBACK_REPOSITORY_LOCK:
        if _PLAYBACK_REPOSITORY is None:
            _PLAYBACK_REPOSITORY = PlaybackRepository(
                PlaybackRepositoryDependencies(
                    open_connection=_playback_repo_open_connection,
                    ensure_autodj_settings=ensure_autodj_settings_table,
                    guess_metadata=guess_metadata_from_filename,
                    normalize_media_path=normalize_media_path,
                    active_station_key=lambda: str(get_active_station_key() or ""),
                )
            )
        return _PLAYBACK_REPOSITORY


# ----------------------
# AutoDJ repository bridge and service facade
# ----------------------

_AUTODJ_SERVICE_LOCK = threading.RLock()
_AUTODJ_SERVICE: Optional[AutoDJService] = None


def _autodj_repo_get_rotation_cursor(rot_entries: list[dict], rotation_sig: str) -> int:
    return _get_playback_repository().get_rotation_cursor(rot_entries, rotation_sig)


def _autodj_repo_set_rotation_cursor(next_index: int, rotation_sig: str) -> None:
    _get_playback_repository().set_rotation_cursor(next_index, rotation_sig)


def _autodj_repo_get_queue_snapshot() -> list[dict]:
    return _get_playback_repository().get_queue_snapshot()


def _autodj_repo_get_recent_history_snapshot(
    cutoff_artist: Optional[str],
    cutoff_title: Optional[str],
    cutoff_track: Optional[str],
) -> list[dict]:
    return _get_playback_repository().get_recent_history_snapshot(
        cutoff_artist,
        cutoff_title,
        cutoff_track,
    )


def _autodj_repo_get_category_tracks(category_id: int) -> list[dict]:
    return _get_playback_repository().get_category_tracks(category_id)


def _autodj_repo_enqueue_track_items(track_items: list[dict]) -> None:
    added = _get_playback_repository().enqueue_track_items(track_items)
    if added > 0:
        _publish_ui_queue_history_changed(get_active_station_key() or "", "autodj_refill")


def _autodj_repo_queue_count() -> int:
    return _get_playback_repository().queue_count()


def _autodj_startup_trace(event: str, station_key: str = "", **fields) -> None:
    """Persist one AutoDJ startup event in the normal protocol log."""
    sk = os.path.basename(str(station_key or get_active_station_key() or "").strip())
    payload = {str(key): value for key, value in fields.items()}
    try:
        _publish_audio_engine_event(
            str(event or "autodj_startup_event"),
            station_key=sk,
            queue_id=0,
            track_id=0,
            deck="",
            payload=payload,
        )
    except Exception:
        pass


def _autodj_publish_startup_event(event: str, station_key: str, fields: dict) -> None:
    _autodj_startup_trace(event, station_key=station_key, **dict(fields or {}))


def _autodj_current_now_playing() -> dict:
    try:
        with NOW_PLAYING_LOCK:
            return dict(NOW_PLAYING)
    except Exception:
        return {}


def _autodj_replan_after_fill(reason: str, async_replan: bool) -> None:
    _sync_reload_and_rebootstrap_after_queue_mutation(
        reason=str(reason or "autodj_refill"),
        async_replan=bool(async_replan),
    )


def _autodj_log_worker_exception(station_key: str, exc: BaseException) -> None:
    try:
        logger.exception("[AutoDJ] native station loop crashed for %s: %s", station_key, exc)
    except Exception:
        pass




def _get_autodj_service() -> AutoDJService:
    global _AUTODJ_SERVICE
    service = _AUTODJ_SERVICE
    if service is not None:
        return service
    with _AUTODJ_SERVICE_LOCK:
        service = _AUTODJ_SERVICE
        if service is None:
            service = AutoDJService(
                AutoDJDependencies(
                    get_active_station_key=lambda: str(get_active_station_key() or ""),
                    get_settings=lambda: dict(get_autodj_settings() or {}),
                    get_rotation=lambda: list(get_autodj_rotation() or []),
                    get_rotation_cursor=_autodj_repo_get_rotation_cursor,
                    set_rotation_cursor=_autodj_repo_set_rotation_cursor,
                    get_queue_snapshot=_autodj_repo_get_queue_snapshot,
                    get_recent_history_snapshot=_autodj_repo_get_recent_history_snapshot,
                    get_category_tracks=_autodj_repo_get_category_tracks,
                    enqueue_track_items=_autodj_repo_enqueue_track_items,
                    queue_count=_autodj_repo_queue_count,
                    get_now_playing=_autodj_current_now_playing,
                    publish_startup_event=_autodj_publish_startup_event,
                    replan_after_fill=_autodj_replan_after_fill,
                    schedule_replan_fallback=lambda reason: _ab_schedule_async_replan(reason),
                    native_station_state=lambda station: dict(_native_station_state(station) or {}),
                    station_runtime_context=station_runtime_context,
                    log_exception=_autodj_log_worker_exception,
                )
            )
            _AUTODJ_SERVICE = service
        return service


def wake_autodj_worker() -> None:
    _get_autodj_service().wake()


def _autodj_startup_fill_once(station_key: str) -> dict:
    return _get_autodj_service().startup_fill_once(station_key)


def autodj_fill_queue_once(*, replan_after_fill: bool = True) -> bool:
    return _get_autodj_service().fill_queue_once(replan_after_fill=replan_after_fill)


def autodj_loop(station_key: str = "") -> None:
    _get_autodj_service().run_loop(station_key)


def start_autodj_thread(station_key: str = "") -> None:
    _get_autodj_service().start_thread(station_key)


def set_autodj_notice(station_key: str, code: str, message: str) -> None:
    _get_autodj_service().set_notice(station_key, code, message)


def clear_autodj_notice(station_key: str) -> None:
    _get_autodj_service().clear_notice(station_key)


def get_autodj_notice(station_key: str):
    return _get_autodj_service().get_notice(station_key)


# Now Playing cache (authoritative for UI): updated when a track is confirmed started (dequeue) or via UI NEXT/SKIP.
NOW_PLAYING_LOCK = threading.Lock()
# Station-scoped now-playing cache
_NOW_PLAYING_BY_STATION: dict[str, dict] = {}


def _get_now_playing_store(station_key: Optional[str]) -> dict:
    sk = station_key or ""
    store = _NOW_PLAYING_BY_STATION.get(sk)
    if store is None:
        store = {"title": "", "artist": "", "album": "", "file": "", "elapsed": 0.0, "duration": 0.0, "updated_at": 0.0}
        _NOW_PLAYING_BY_STATION[sk] = store
    store.setdefault("logical_track_id", 0)
    store.setdefault("playback_instance_id", 0)
    store.setdefault("seek_session_id", 0)
    store.setdefault("display_track_logical_id", 0)
    store.setdefault("display_original_duration", 0.0)
    store.setdefault("display_seek_base", 0.0)
    store.setdefault("source_seek_base_seconds", 0.0)
    store.setdefault("display_hold_until_metadata", False)
    store.setdefault("display_hold_started_at", 0.0)
    store.setdefault("pending_seek_restart", False)
    store.setdefault("pending_seek_path", "")
    store.setdefault("pending_seek_queue_id", 0)
    store.setdefault("pending_seek_track_id", 0)
    store.setdefault("pending_seek_queue_restore", False)
    store.setdefault("seek_queue_restore_scheduled", False)
    store.setdefault("seek_isolated_playlist_active", False)
    store.setdefault("seek_generation", 0)
    store.setdefault("seek_generation_started_at", 0.0)
    store.setdefault("seek_singleflight_active", False)
    store.setdefault("seek_singleflight_pending", None)
    store.setdefault("seek_singleflight_started_at", 0.0)
    store.setdefault("seek_cooldown_until", 0.0)
    store.setdefault("seek_pin_active", False)
    store.setdefault("seek_pinned_path", "")
    store.setdefault("seek_pinned_queue_id", 0)
    store.setdefault("seek_pinned_track_id", 0)
    store.setdefault("seek_pinned_generation", 0)
    store.setdefault("seek_pinned_until", 0.0)
    store.setdefault("seek_grace_active", False)
    store.setdefault("seek_grace_path", "")
    store.setdefault("seek_grace_queue_id", 0)
    store.setdefault("seek_grace_track_id", 0)
    store.setdefault("seek_grace_generation", 0)
    store.setdefault("seek_grace_until", 0.0)
    store.setdefault("seek_restore_waiting", False)
    store.setdefault("seek_restore_target_path", "")
    store.setdefault("seek_restore_queue_id", 0)
    store.setdefault("seek_restore_track_id", 0)
    store.setdefault("seek_restore_generation", 0)
    store.setdefault("seek_restore_started_at", 0.0)
    store.setdefault("seek_restore_same_path_seen_at", 0.0)
    store.setdefault("seek_restore_last_foreign_at", 0.0)
    store.setdefault("seek_restore_status_match_count", 0)
    store.setdefault("seek_restore_last_status_path", "")
    store.setdefault("seek_owner_active", False)
    store.setdefault("seek_owner_path", "")
    store.setdefault("seek_owner_queue_id", 0)
    store.setdefault("seek_owner_track_id", 0)
    store.setdefault("seek_owner_generation", 0)
    store.setdefault("seek_owner_started_at", 0.0)
    store.setdefault("seek_owner_until", 0.0)
    store.setdefault("seek_eof_completion_done", False)
    store.setdefault("seek_eof_completion_path", "")
    store.setdefault("seek_handoff_duplicate_guard_active", False)
    store.setdefault("seek_handoff_duplicate_guard_key", "")
    store.setdefault("seek_handoff_duplicate_guard_until", 0.0)
    return store


def clear_now_playing_for_station(station_key: Optional[str]) -> None:
    """Clear cached now playing metadata for a station.

    Used when a station is stopped so dashboards don't keep showing the last track.
    """
    try:
        sk = (station_key or "").strip()
        with NOW_PLAYING_LOCK:
            store = _get_now_playing_store(sk)
            store["title"] = ""
            store["artist"] = ""
            store["album"] = ""
            store["file"] = ""
            try:
                store["cue_in_seconds"] = None
            except Exception:
                pass
            try:
                store["cue_out_seconds"] = None
            except Exception:
                pass
            store["elapsed"] = 0.0
            store["duration"] = 0.0
            store["logical_track_id"] = 0
            store["playback_instance_id"] = 0
            store["seek_session_id"] = 0
            store["display_track_logical_id"] = 0
            store["display_original_duration"] = 0.0
            store["display_seek_base"] = 0.0
            store["source_seek_base_seconds"] = 0.0
            store["display_hold_until_metadata"] = False
            store["display_hold_started_at"] = 0.0
            if bool(store.get("seek_restore_waiting")):
                target_restore_path = normalize_media_path(str(store.get("seek_restore_target_path") or ""))
                if target_restore_path and incoming_path_norm == target_restore_path:
                    store["seek_restore_same_path_seen_at"] = time.time()
                    store["seek_restore_status_match_count"] = 0
                    store["seek_restore_last_status_path"] = ""
                elif incoming_path_norm and target_restore_path and incoming_path_norm != target_restore_path:
                    store["seek_restore_last_foreign_at"] = time.time()
                    store["seek_restore_status_match_count"] = 0
                    store["seek_restore_last_status_path"] = incoming_path_norm
            store["pending_seek_restart"] = False
            store["pending_seek_path"] = ""
            store["pending_seek_queue_id"] = 0
            store["pending_seek_track_id"] = 0
            store["pending_seek_queue_restore"] = False
            store["seek_queue_restore_scheduled"] = False
            store["seek_generation"] = 0
            store["seek_generation_started_at"] = 0.0
            store["seek_singleflight_active"] = False
            store["seek_singleflight_pending"] = None
            store["seek_singleflight_started_at"] = 0.0
            store["seek_cooldown_until"] = 0.0
            store["seek_pin_active"] = False
            store["seek_pinned_path"] = ""
            store["seek_pinned_queue_id"] = 0
            store["seek_pinned_track_id"] = 0
            store["seek_pinned_generation"] = 0
            store["seek_pinned_until"] = 0.0
            store["seek_grace_active"] = False
            store["seek_grace_path"] = ""
            store["seek_grace_queue_id"] = 0
            store["seek_grace_track_id"] = 0
            store["seek_grace_generation"] = 0
            store["seek_grace_until"] = 0.0
            store["seek_restore_waiting"] = False
            store["seek_restore_target_path"] = ""
            store["seek_restore_queue_id"] = 0
            store["seek_restore_track_id"] = 0
            store["seek_restore_generation"] = 0
            store["seek_restore_started_at"] = 0.0
            store["seek_restore_same_path_seen_at"] = 0.0
            store["seek_restore_last_foreign_at"] = 0.0
            store["seek_restore_status_match_count"] = 0
            store["seek_restore_last_status_path"] = ""
            store["seek_owner_active"] = False
            store["seek_owner_path"] = ""
            store["seek_owner_queue_id"] = 0
            store["seek_owner_track_id"] = 0
            store["seek_owner_generation"] = 0
            store["seek_owner_started_at"] = 0.0
            store["seek_owner_until"] = 0.0
            store["seek_eof_completion_done"] = False
            store["seek_eof_completion_path"] = ""
            store["seek_handoff_duplicate_guard_active"] = False
            store["seek_handoff_duplicate_guard_key"] = ""
            store["seek_handoff_duplicate_guard_until"] = 0.0
            store["updated_at"] = float(datetime.now().timestamp())
        with PROGRESS_LOCK:
            ps = _get_progress_state(sk)
            ps["recent_track_path"] = ""
            ps["recent_track_started_at"] = 0.0
            ps["recent_track_cue_base"] = 0.0
    except Exception:
        return



def _clear_seek_pin_locked(store: dict) -> None:
    """Clear temporary seek pin state. Caller must hold NOW_PLAYING_LOCK."""
    try:
        store["seek_pin_active"] = False
        store["seek_pinned_path"] = ""
        store["seek_pinned_queue_id"] = 0
        store["seek_pinned_track_id"] = 0
        store["seek_pinned_generation"] = 0
        store["seek_pinned_until"] = 0.0
    except Exception:
        pass


def _clear_seek_grace_locked(store: dict) -> None:
    """Clear temporary seek grace state. Caller must hold NOW_PLAYING_LOCK."""
    try:
        store["seek_grace_active"] = False
        store["seek_grace_path"] = ""
        store["seek_grace_queue_id"] = 0
        store["seek_grace_track_id"] = 0
        store["seek_grace_generation"] = 0
        store["seek_grace_until"] = 0.0
    except Exception:
        pass


def _clear_seek_restore_wait_locked(store: dict) -> None:
    """Clear delayed backward-seek restore state. Caller must hold NOW_PLAYING_LOCK."""
    try:
        store["seek_restore_waiting"] = False
        store["seek_restore_target_path"] = ""
        store["seek_restore_queue_id"] = 0
        store["seek_restore_track_id"] = 0
        store["seek_restore_generation"] = 0
        store["seek_restore_started_at"] = 0.0
        store["seek_restore_same_path_seen_at"] = 0.0
        store["seek_restore_last_foreign_at"] = 0.0
        store["seek_restore_status_match_count"] = 0
        store["seek_restore_last_status_path"] = ""
    except Exception:
        pass


def _clear_seek_owner_locked(store: dict) -> None:
    """Clear seek ownership lock. Caller must hold NOW_PLAYING_LOCK."""
    try:
        store["seek_owner_active"] = False
        store["seek_owner_path"] = ""
        store["seek_owner_queue_id"] = 0
        store["seek_owner_track_id"] = 0
        store["seek_owner_generation"] = 0
        store["seek_owner_started_at"] = 0.0
        store["seek_owner_until"] = 0.0
        store["seek_eof_completion_done"] = False
        store["seek_eof_completion_path"] = ""
    except Exception:
        pass


def _clear_seek_transient_state_for_new_seek_locked(store: dict) -> None:
    """Cancel stale seek timers and guards before accepting a new manual seek. Caller must hold NOW_PLAYING_LOCK."""
    try:
        store["pending_seek_restart"] = False
        store["pending_seek_path"] = ""
        store["pending_seek_queue_id"] = 0
        store["pending_seek_track_id"] = 0
        store["pending_seek_queue_restore"] = False
        store["seek_queue_restore_scheduled"] = False
        store["seek_generation"] = int(store.get("seek_generation") or 0) + 1
        store["seek_generation_started_at"] = 0.0
        store["seek_singleflight_pending"] = None
        _clear_seek_pin_locked(store)
        _clear_seek_grace_locked(store)
        _clear_seek_restore_wait_locked(store)
        _clear_seek_owner_locked(store)
        store["seek_restart_pending_metadata"] = False
        store["seek_crossfade_hold_active"] = False
        store["seek_crossfade_hold_file"] = ""
        store["seek_crossfade_hold_title"] = ""
        store["seek_crossfade_hold_artist"] = ""
        store["seek_crossfade_hold_album"] = ""
        store["seek_crossfade_hold_duration"] = 0.0
        store["seek_crossfade_hold_seek_base"] = 0.0
        store["seek_isolated_playlist_active"] = False
        store["seek_eof_completion_done"] = False
        store["seek_eof_completion_path"] = ""
    except Exception:
        pass



def _normalize_station_key(raw_key: str) -> str:
    """Normalize an incoming station key from engine event payloads.

    We use the station DB filename (e.g. 'MyStation.db') as the stable key.
    Only accept safe basename values ending in '.db' to avoid path traversal
    and accidental cross-station overwrites.
    """
    try:
        k = (raw_key or "").strip()
        k = os.path.basename(k)
        if not k or not k.endswith(".db"):
            return ""
        if re.fullmatch(r"[A-Za-z0-9_.-]+\.db", k) is None:
            return ""
        return k
    except Exception:
        return ""


_NOW_PLAYING_EVENT_BY_STATION: dict[str, threading.Event] = {}


def _get_now_playing_event(station_key: Optional[str]) -> threading.Event:
    sk = (station_key or "").strip() or "__default__"
    ev = _NOW_PLAYING_EVENT_BY_STATION.get(sk)
    if ev is None:
        ev = threading.Event()
        _NOW_PLAYING_EVENT_BY_STATION[sk] = ev
    return ev


NOW_PLAYING = {
    "title": "",
    "artist": "",
    "album": "",
    "duration": 0.0,
    "elapsed": 0.0,
    "file": "",
    "updated_at": 0.0,
}


_startup_lock = threading.Lock()
_startup_done = False

@app.before_request
def _startup_once():
    """Run one-time cleanup on the first incoming request (Flask 3.x compatible)."""
    global _startup_done
    if _startup_done:
        return
    with _startup_lock:
        if _startup_done:
            return
        try:
            # Ensure the canonical station schema is initialized before serving requests.
            init_global_db()
            ensure_active_station_in_session()
            # Only initialize station schema if an actual station DB exists.
            # This prevents accidentally creating a new empty station DB from
            # stale state (e.g., an old last_station_db value).
            try:
                active_path = get_active_station_db_path()
                if active_path and os.path.exists(active_path):
                    init_db()
            except Exception:
                pass
        except Exception as e:
            print(f"_startup_once: cleanup failed: {e}")
        _startup_done = True

@app.before_request
def enforce_setup_on_first_run():
    """Force the initial setup flow when there are no users yet (fresh DB).

    This also prevents stale sessions from bypassing setup when the users table is empty.
    """
    try:
        # Allow setup assets/endpoints to load
        if request.endpoint in ("static", "setup"):
            return
        # Allow favicon etc
        if request.path in ("/setup",):
            return
        if is_first_run():
            # Clear any stale session so we don't accidentally look logged-in
            try:
                session.clear()
            except Exception:
                pass
            return redirect(url_for("setup"))
    except Exception:
        # Never break request handling due to setup guard
        return



@app.context_processor
def inject_current_settings():
    try:
        settings = get_settings()
    except Exception:
        settings = None

    stations = build_station_list()
    active_station_id = os.path.basename(get_active_station_db_path())

    nav_station_label = None

    is_stations_dashboard = False
    try:
        # Use path check because some routes are wrapped by decorators that change endpoint names.
        if request and getattr(request, "path", "") in ("/", "/dashboard"):
            is_stations_dashboard = True
    except Exception:
        pass

    # If we are on a multi-station overview page, always show the dashboard label
    try:
        if is_stations_dashboard:
            nav_station_label = "Dashboard"
    except Exception:
        pass

    # Otherwise resolve active station name from the station DB so the exact
    # Radio name casing is preserved in the header/dropdown label.
    if not nav_station_label:
        try:
            for st in stations or []:
                # Stations list uses db filename as id (st["id"])
                db_name = os.path.basename(st.get("db_filename") or st.get("id") or "")
                if db_name and db_name == active_station_id:
                    db_path = resolve_station_db_path(db_name)
                    display_name = read_station_display_name(db_path) if db_path else ""
                    nav_station_label = (display_name or "").strip() or (st.get("name") or "").strip() or db_name
                    break
        except Exception:
            pass

    # Fallback to settings.radio_name if still empty
    if not nav_station_label:
        try:
            if settings and getattr(settings, "radio_name", None):
                nav_station_label = settings.radio_name
        except Exception:
            pass

    return {
        "current_settings": settings,
        "stations": stations,
        "active_station_id": active_station_id,
        "nav_station_label": nav_station_label,
        "is_stations_dashboard": is_stations_dashboard,
    }



app.secret_key = os.environ.get("RADIO_AUTOMATION_SECRET_KEY", "change_this_secret_key")

_DB_CONNECT_TIMEOUT_SECONDS = 30.0
_DB_BUSY_TIMEOUT_MS = 30000
_db_init_lock = threading.Lock()
_initialized_station_dbs: set[str] = set()
_SQLITE_WAL_CONFIGURED_PATHS: set[str] = set()
_SQLITE_WAL_CONFIG_LOCK = threading.Lock()
_STATION_APP_STATE_READY_PATHS: set[str] = set()
_STATION_APP_STATE_READY_LOCK = threading.Lock()
_STATION_SCRIPTS_READY_PATHS: set[str] = set()
_STATION_SCRIPTS_READY_LOCK = threading.Lock()

# These schema/registry checks create SQLite journal/WAL churn even when they do
# not change data.  Background OFF AIR pollers call the related getters often,
# so keep the expensive idempotent checks process-local and run them only once.
_GLOBAL_DB_SCHEMA_READY = False
_GLOBAL_DB_SCHEMA_LOCK = threading.Lock()
_STATION_REGISTRY_READY = False
_STATION_REGISTRY_LOCK = threading.Lock()

# Keep reusable SQLite connections in a bounded cache. Reusing connections avoids
# WAL/SHM open/unlink churn in high-frequency background pollers while the LRU
# limit prevents helper/request threads from retaining unbounded descriptors.
_PERSISTENT_SQLITE_MAX_CONNECTIONS = 6
_PERSISTENT_SQLITE_CONNECTIONS: dict[tuple[int, str], sqlite3.Connection] = {}
_PERSISTENT_SQLITE_LAST_USED: dict[tuple[int, str], float] = {}
_PERSISTENT_SQLITE_LOCK = threading.RLock()


class ReusableSQLiteConnection(sqlite3.Connection):
    """SQLite connection whose public close() keeps the handle reusable.

    Existing code closes connections after each helper call. For high-frequency
    background loops that is too expensive in WAL mode because SQLite repeatedly
    opens/unlinks the -shm sidecar. close() therefore mimics SQLite close safety
    by rolling back any uncommitted transaction, marks the handle idle, and keeps
    it reusable until the bounded cache evicts it. hard_close() physically closes
    the handle on process exit, invalidation, or LRU pruning.
    """

    def _mark_in_use(self, value: bool) -> None:
        try:
            setattr(self, "_wb_in_use", bool(value))
        except Exception:
            pass

    def close(self):  # type: ignore[override]
        try:
            if self.in_transaction:
                self.rollback()
        except Exception:
            pass
        self._mark_in_use(False)
        try:
            key = getattr(self, "_wb_cache_key", None)
            if key is not None:
                with _PERSISTENT_SQLITE_LOCK:
                    _PERSISTENT_SQLITE_LAST_USED[key] = time.time()
                    _prune_reusable_sqlite_cache_locked()
        except Exception:
            pass

    def hard_close(self) -> None:
        try:
            if self.in_transaction:
                self.rollback()
        except Exception:
            pass
        self._mark_in_use(False)
        sqlite3.Connection.close(self)


def _reusable_sqlite_conn_idle(conn: sqlite3.Connection) -> bool:
    try:
        if bool(getattr(conn, "_wb_in_use", False)):
            return False
    except Exception:
        pass
    try:
        if conn.in_transaction:
            return False
    except Exception:
        return False
    return True


def _prune_reusable_sqlite_cache_locked() -> None:
    """Prune idle reusable SQLite handles to the configured LRU limit."""
    max_conn = max(1, int(_PERSISTENT_SQLITE_MAX_CONNECTIONS))
    if len(_PERSISTENT_SQLITE_CONNECTIONS) <= max_conn:
        return
    candidates = sorted(
        _PERSISTENT_SQLITE_CONNECTIONS.keys(),
        key=lambda k: float(_PERSISTENT_SQLITE_LAST_USED.get(k, 0.0) or 0.0),
    )
    for key in candidates:
        if len(_PERSISTENT_SQLITE_CONNECTIONS) <= max_conn:
            break
        conn = _PERSISTENT_SQLITE_CONNECTIONS.get(key)
        if conn is None or not _reusable_sqlite_conn_idle(conn):
            continue
        try:
            if hasattr(conn, "hard_close"):
                conn.hard_close()  # type: ignore[attr-defined]
            else:
                sqlite3.Connection.close(conn)
        except Exception:
            pass
        _PERSISTENT_SQLITE_CONNECTIONS.pop(key, None)
        _PERSISTENT_SQLITE_LAST_USED.pop(key, None)


def _connect_sqlite_reusable(db_path: str, *, timeout: float | None = None) -> sqlite3.Connection:
    """Return a bounded thread-local reusable SQLite connection for db_path."""
    abs_path = os.path.abspath(str(db_path))

    key = (threading.get_ident(), abs_path)
    with _PERSISTENT_SQLITE_LOCK:
        now_ts = time.time()
        conn = _PERSISTENT_SQLITE_CONNECTIONS.get(key)
        if conn is not None:
            # Nested get_db()/get_db_for_station() calls can happen inside routes
            # that already hold a cached connection (for example encoder configure
            # stopping/restarting the station while updating encoder settings). Do
            # not hand out the same in-use handle again: an inner helper may call
            # close(), mark the outer handle idle, and LRU pruning can physically
            # close it before the outer cursor finishes its UPDATE.  Use a short
            # lived fresh connection for nested access; this is rare and keeps the
            # normal low-IO reusable path intact.
            try:
                if bool(getattr(conn, "_wb_in_use", False)):
                    nested = sqlite3.connect(
                        abs_path,
                        timeout=_DB_CONNECT_TIMEOUT_SECONDS if timeout is None else float(timeout),
                        check_same_thread=False,
                    )
                    return configure_sqlite_connection(nested)
            except Exception:
                pass
            try:
                conn._mark_in_use(True) if hasattr(conn, "_mark_in_use") else setattr(conn, "_wb_in_use", True)  # type: ignore[attr-defined]
                _PERSISTENT_SQLITE_LAST_USED[key] = now_ts
                conn.execute("SELECT 1")
                return conn
            except Exception:
                try:
                    if hasattr(conn, "hard_close"):
                        conn.hard_close()  # type: ignore[attr-defined]
                    else:
                        sqlite3.Connection.close(conn)
                except Exception:
                    pass
                _PERSISTENT_SQLITE_CONNECTIONS.pop(key, None)
                _PERSISTENT_SQLITE_LAST_USED.pop(key, None)

        conn = sqlite3.connect(
            abs_path,
            timeout=_DB_CONNECT_TIMEOUT_SECONDS if timeout is None else float(timeout),
            factory=ReusableSQLiteConnection,
            check_same_thread=False,
        )
        try:
            setattr(conn, "_wb_cache_key", key)
            setattr(conn, "_wb_in_use", True)
        except Exception:
            pass
        configure_sqlite_connection(conn)
        _PERSISTENT_SQLITE_CONNECTIONS[key] = conn
        _PERSISTENT_SQLITE_LAST_USED[key] = now_ts
        _prune_reusable_sqlite_cache_locked()
        return conn


def _hard_close_reusable_sqlite(db_path: str | None = None) -> None:
    """Physically close cached SQLite handles, optionally limited to one DB path."""
    target = os.path.abspath(str(db_path)) if db_path else None
    with _PERSISTENT_SQLITE_LOCK:
        items = list(_PERSISTENT_SQLITE_CONNECTIONS.items())
        for key, conn in items:
            _, path = key
            if target and path != target:
                continue
            try:
                if hasattr(conn, "hard_close"):
                    conn.hard_close()  # type: ignore[attr-defined]
                else:
                    sqlite3.Connection.close(conn)
            except Exception:
                pass
            _PERSISTENT_SQLITE_CONNECTIONS.pop(key, None)
            _PERSISTENT_SQLITE_LAST_USED.pop(key, None)


try:
    import atexit
    atexit.register(_hard_close_reusable_sqlite)
except Exception:
    pass

_SCRIPT_ENGINE_WAKE_EVENT = threading.Event()
_SCHEDULER_WAKE_EVENT = threading.Event()

# UI change events are lightweight invalidations used to keep low polling rates
# while still refreshing visible state immediately after real backend changes.
# Events never carry authoritative DB rows; browsers re-fetch the existing APIs.
_UI_EVENT_CONDITION = threading.Condition()
_UI_EVENT_SEQ = 0
_UI_EVENT_LAST: dict = {}
_UI_EVENT_HISTORY: list[dict] = []
_UI_EVENT_HISTORY_LIMIT = 128


def _publish_ui_event(event_type: str, station_key: str = "", reason: str = "", extra: dict | None = None) -> None:
    """Publish one best-effort Studio UI invalidation event."""
    global _UI_EVENT_SEQ, _UI_EVENT_LAST, _UI_EVENT_HISTORY
    try:
        event_name = str(event_type or "ui_state_changed").strip() or "ui_state_changed"
        sk = str(station_key or "").strip()
        if not sk:
            try:
                sk = str(get_active_station_key() or "").strip()
            except Exception:
                sk = ""
        payload = {
            "type": event_name,
            "station_key": sk,
            "reason": str(reason or event_name),
            "ts": time.time(),
        }
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key not in {"type", "seq"}:
                    payload[str(key)] = value
        with _UI_EVENT_CONDITION:
            _UI_EVENT_SEQ += 1
            payload["seq"] = _UI_EVENT_SEQ
            _UI_EVENT_LAST = dict(payload)
            _UI_EVENT_HISTORY.append(dict(payload))
            if len(_UI_EVENT_HISTORY) > _UI_EVENT_HISTORY_LIMIT:
                del _UI_EVENT_HISTORY[:-_UI_EVENT_HISTORY_LIMIT]
            _UI_EVENT_CONDITION.notify_all()
    except Exception:
        pass


def _publish_ui_queue_history_changed(station_key: str = "", reason: str = "") -> None:
    """Notify connected browsers that queue/history data changed."""
    _publish_ui_event("queue_history_changed", station_key, reason or "queue_history_changed")


def _publish_ui_on_air_state_changed(station_key: str = "", reason: str = "") -> None:
    """Notify connected browsers that native audio-engine ON-AIR state changed."""
    _publish_ui_event("on_air_state_changed", station_key, reason or "on_air_state_changed")


def _publish_ui_encoders_changed(station_key: str = "", reason: str = "", stream_id: int | None = None) -> None:
    """Notify connected browsers that encoder configuration or runtime state changed."""
    extra = {"stream_id": int(stream_id)} if stream_id is not None else None
    _publish_ui_event("encoders_changed", station_key, reason or "encoders_changed", extra)


def _notify_on_air_state_changed(station_key: str = "", reason: str = "") -> None:
    """Wake OFF-AIR helpers and immediately invalidate browser ON-AIR state."""
    for ev in (_SCRIPT_ENGINE_WAKE_EVENT, _SCHEDULER_WAKE_EVENT):
        try:
            ev.set()
        except Exception:
            pass
    _publish_ui_on_air_state_changed(station_key, reason or "on_air_state_changed")

def _wait_idle_helper_event(ev: threading.Event, timeout_seconds: float) -> None:
    """Sleep until a start/stop notification or the idle timeout expires."""
    try:
        ev.wait(timeout=max(0.1, float(timeout_seconds)))
        ev.clear()
    except Exception:
        time.sleep(max(0.1, float(timeout_seconds or 1.0)))

def _sqlite_main_database_path(conn: sqlite3.Connection) -> str:
    """Return the main database file path for a SQLite connection, if available."""
    try:
        rows = conn.execute("PRAGMA database_list").fetchall() or []
        for row in rows:
            try:
                if str(row[1]) == "main":
                    return os.path.abspath(str(row[2] or ""))
            except Exception:
                continue
    except Exception:
        pass
    return ""

def configure_sqlite_connection(conn: sqlite3.Connection) -> sqlite3.Connection:
    try:
        conn.execute(f"PRAGMA busy_timeout = {_DB_BUSY_TIMEOUT_MS}")
    except Exception:
        pass

    # journal_mode=WAL is persistent per database.  Running this PRAGMA on every
    # short-lived read connection creates needless WAL/SHM churn while the station
    # is idle. Configure each DB path once per app process instead.
    db_path = _sqlite_main_database_path(conn)
    should_configure_wal = True
    if db_path:
        with _SQLITE_WAL_CONFIG_LOCK:
            should_configure_wal = db_path not in _SQLITE_WAL_CONFIGURED_PATHS
            if should_configure_wal:
                _SQLITE_WAL_CONFIGURED_PATHS.add(db_path)
    if should_configure_wal:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            if db_path:
                with _SQLITE_WAL_CONFIG_LOCK:
                    _SQLITE_WAL_CONFIGURED_PATHS.discard(db_path)

    try:
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    conn.row_factory = sqlite3.Row
    return conn


# --- Global DB (users + station registry) ---

def get_global_db():
    """Return a connection to the global database (users + station registry)."""
    os.makedirs(DB_DIR, exist_ok=True)
    return _connect_sqlite_reusable(GLOBAL_DATABASE)


def init_global_db(force: bool = False) -> None:
    """Initialize global DB schema once per app process."""
    global _GLOBAL_DB_SCHEMA_READY
    if (not force) and _GLOBAL_DB_SCHEMA_READY:
        return
    with _GLOBAL_DB_SCHEMA_LOCK:
        if (not force) and _GLOBAL_DB_SCHEMA_READY:
            return
        _init_global_db_uncached()
        _GLOBAL_DB_SCHEMA_READY = True


def _init_global_db_uncached() -> None:
    """Initialize global DB schema (idempotent on disk, uncached helper)."""
    conn = get_global_db()
    c = conn.cursor()

    # Auth users (login is app-wide; station switching must not require re-auth).
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            db_filename TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    # Native station start time for dashboard uptime display.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS audio_engine_state (
            station_key TEXT PRIMARY KEY,
            started_at TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduler_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            is_enabled INTEGER NOT NULL DEFAULT 0,
            auto_start INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            run_when TEXT NOT NULL,
            insert_kind TEXT NOT NULL,
            insert_value TEXT NOT NULL,
            next_run_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS station_scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_path TEXT NOT NULL,
            auto_start INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Stopped',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def _app_state_get(key: str) -> str:
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT value FROM app_state WHERE key = ?", (key,))
        row = c.fetchone()
        conn.close()
        if row is None:
            return ""
        return str(row["value"] or "")
    except Exception:
        return ""


def _app_state_set(key: str, value: str) -> None:
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()
    except Exception:
        pass


def _ensure_station_app_state_table(conn: sqlite3.Connection) -> None:
    """Ensure the station-local key/value state table exists once per DB path."""
    db_path = _sqlite_main_database_path(conn)
    if db_path:
        with _STATION_APP_STATE_READY_LOCK:
            if db_path in _STATION_APP_STATE_READY_PATHS:
                return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    try:
        conn.commit()
    except Exception:
        pass
    if db_path:
        with _STATION_APP_STATE_READY_LOCK:
            _STATION_APP_STATE_READY_PATHS.add(db_path)


def _station_state_get(key: str) -> str:
    conn = None
    try:
        conn = get_db()
        _ensure_station_app_state_table(conn)
        c = conn.cursor()
        c.execute("SELECT value FROM app_state WHERE key = ?", (key,))
        row = c.fetchone()
        if row is None:
            return ""
        try:
            return str(row["value"] or "")
        except Exception:
            return str(row[0] or "")
    except Exception:
        return ""
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _station_state_set(key: str, value: str) -> None:
    conn = None
    try:
        conn = get_db()
        _ensure_station_app_state_table(conn)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def ensure_station_scripts_table(conn: sqlite3.Connection) -> None:
    """Ensure station_scripts schema once per station DB per process.

    This helper is called by the active script-engine poller. Running
    CREATE TABLE IF NOT EXISTS + COMMIT on every one-second tick creates SQLite
    WAL/journal churn even when nothing changes, so cache the schema check per
    database path.
    """
    db_path = _sqlite_main_database_path(conn)
    normalized = os.path.abspath(db_path) if db_path else ""
    if normalized:
        with _STATION_SCRIPTS_READY_LOCK:
            if normalized in _STATION_SCRIPTS_READY_PATHS:
                return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS station_scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_path TEXT NOT NULL,
            auto_start INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Stopped',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    if normalized:
        with _STATION_SCRIPTS_READY_LOCK:
            _STATION_SCRIPTS_READY_PATHS.add(normalized)

def _read_station_music_library_path(station_key: str) -> str:
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT music_library_path FROM settings ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if not row:
            return ""
        try:
            return str(row["music_library_path"] or "").strip()
        except Exception:
            return str(row[0] or "").strip()
    except Exception:
        return ""


def _resolve_station_script_file_path(station_key: str, script_path: str) -> str:
    raw = str(script_path or "").strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    base = _read_station_music_library_path(station_key)
    if not base:
        return raw
    try:
        return os.path.abspath(os.path.join(base, raw))
    except Exception:
        return raw


def _script_status_base(status: str) -> str:
    """Return a stable status class for persistence throttling."""
    value = str(status or "Stopped").strip() or "Stopped"
    if value.startswith("Waiting for time "):
        # The ETA suffix changes every second. Keep the scheduled target as the
        # stable class so countdown-only changes can be throttled.
        m = re.match(r"^(Waiting for time \d{2}:\d{2}:\d{2})", value)
        return m.group(1) if m else "Waiting for time"
    return value


def _script_status_should_persist(key: tuple[str, int], new_status: str, current_status: str) -> bool:
    """Decide whether a status update needs an SQLite write now."""
    now_ts = time.time()
    new_status = str(new_status or "Stopped")
    current_status = str(current_status or "")
    new_base = _script_status_base(new_status)
    current_base = _script_status_base(current_status)

    # Non-countdown states must be durable immediately.
    if not new_status.startswith("Waiting for time "):
        return current_status != new_status

    # Entering/leaving/changing the scheduled target must be durable immediately.
    if current_base != new_base:
        return True

    # Countdown text differs every second; write it only occasionally so the UI
    # still receives a fresh persisted status without keeping the disk busy.
    last_ts = float(_SCRIPT_STATUS_LAST_PERSIST_TS.get(key, 0.0) or 0.0)
    if (now_ts - last_ts) >= _SCRIPT_STATUS_WAITING_PERSIST_INTERVAL_SECONDS:
        return current_status != new_status
    return False


def _set_station_script_status(station_key: str, script_id: int, status: str) -> None:
    """Set script status with write throttling for dynamic ETA countdowns.

    The active script engine ticks once per second so it can catch exact script
    fire times. The previous implementation stored the full countdown text on
    every tick, which forced an SQLite write every second while ON AIR. This
    function keeps the latest status in memory immediately, but persists only
    meaningful changes or periodic countdown refreshes.
    """
    new_status = str(status or "Stopped")
    key = (str(station_key or ""), int(script_id or 0))

    with _SCRIPT_STATUS_LOCK:
        _SCRIPT_STATUS_RUNTIME_CACHE[key] = new_status
        cached_persisted = _SCRIPT_STATUS_PERSISTED_CACHE.get(key)

    conn = None
    db_changed = False
    current_status = str(cached_persisted or "")
    try:
        if cached_persisted is None:
            conn = get_db_for_station(station_key)
            ensure_station_scripts_table(conn)
            c = conn.cursor()
            c.execute("SELECT status FROM station_scripts WHERE id = ?", (int(script_id),))
            row = c.fetchone()
            if row is not None:
                try:
                    current_status = str(row["status"] or "")
                except Exception:
                    current_status = str(row[0] or "")
            with _SCRIPT_STATUS_LOCK:
                _SCRIPT_STATUS_PERSISTED_CACHE[key] = current_status

        if not _script_status_should_persist(key, new_status, current_status):
            return

        if conn is None:
            conn = get_db_for_station(station_key)
            ensure_station_scripts_table(conn)
        c = conn.cursor()
        now = _utc_now_naive().isoformat(timespec="seconds")
        c.execute(
            "UPDATE station_scripts SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, now, int(script_id)),
        )
        conn.commit()
        db_changed = True
        with _SCRIPT_STATUS_LOCK:
            _SCRIPT_STATUS_PERSISTED_CACHE[key] = new_status
            _SCRIPT_STATUS_LAST_PERSIST_TS[key] = time.time()
    except Exception:
        try:
            if conn is not None:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    # studio_scripts is a compatibility mirror. Only update it when SQLite was
    # actually changed; otherwise a throttled countdown would still write app_state.
    if not db_changed:
        return
    try:
        raw = _station_state_get("studio_scripts")
        if not raw:
            return
        items = json.loads(raw)
        if not isinstance(items, list):
            return
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("id") or 0) == int(script_id):
                    if str(item.get("status") or "") != new_status:
                        item["status"] = new_status
                        changed = True
            except Exception:
                continue
        if changed:
            _station_state_set("studio_scripts", json.dumps(items, ensure_ascii=False))
    except Exception:
        pass

def _read_station_scripts(station_key: str) -> list[dict]:
    scripts: list[dict] = []
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT id, script_path, auto_start, status FROM station_scripts ORDER BY id ASC")
        rows = c.fetchall() or []
        conn.close()
        for row in rows:
            try:
                item = dict(row)
            except Exception:
                item = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
            script_path = str(item.get("script_path") or "").strip()
            if not script_path:
                continue
            scripts.append({
                "id": int(item.get("id") or (len(scripts) + 1)),
                "script_path": script_path,
                "auto_start": 1 if int(item.get("auto_start") or 0) else 0,
                "status": str(item.get("status") or "Stopped").strip() or "Stopped",
            })
    except Exception:
        return []
    return scripts


_SCRIPT_ENGINE_LOCK = threading.Lock()

def _reset_station_script_statuses_on_startup() -> None:
    station_keys = get_registered_station_keys() or []
    if not station_keys:
        try:
            active_key = (get_active_station_key() or "").strip()
            if active_key:
                station_keys = [active_key]
        except Exception:
            station_keys = []

    for station_key in station_keys:
        try:
            scripts = _read_station_scripts(station_key)
            for item in scripts:
                script_id = int(item.get("id") or 0)
                auto_start = 1 if int(item.get("auto_start") or 0) else 0
                wait_value = ""
                if auto_start:
                    if not is_station_on_air(station_key):
                        _set_station_script_status(station_key, script_id, "Stopped")
                        continue
                    try:
                        raw_path = str(item.get("script_path") or "").strip()
                        entry = _load_station_script_definition_for_start(station_key, script_id, raw_path)
                        wait_value = _script_definition_wait_value(entry)
                    except Exception:
                        wait_value = ""
                    new_status = _format_script_waiting_status(wait_value, datetime.now().replace(microsecond=0)) if wait_value else "Waiting"
                    _set_station_script_status(station_key, script_id, new_status)
                else:
                    _set_station_script_status(station_key, script_id, "Stopped")
        except Exception as exc:
            pass


_SCRIPT_ENGINE_LOCK = threading.Lock()
_SCRIPT_ENGINE_LAST_RUN: dict[tuple[str, int], str] = {}
_SCRIPT_ENGINE_ACTIVE_POLL_SECONDS = 1.0
_SCRIPT_ENGINE_IDLE_POLL_SECONDS = 30.0
_SCRIPT_ENGINE_LAST_ON_AIR_BY_STATION: dict[str, bool] = {}

# Persisting the full "Waiting for time ... ETA ..." string every active tick
# caused a SQLite write every second while ON AIR, even with no browser open.
# Runtime status is still kept in memory immediately, but dynamic countdown
# statuses are only written to SQLite periodically. Terminal states and real
# state changes are persisted immediately.
_SCRIPT_STATUS_LOCK = threading.Lock()
_SCRIPT_STATUS_RUNTIME_CACHE: dict[tuple[str, int], str] = {}
_SCRIPT_STATUS_PERSISTED_CACHE: dict[tuple[str, int], str] = {}
_SCRIPT_STATUS_LAST_PERSIST_TS: dict[tuple[str, int], float] = {}
_SCRIPT_STATUS_WAITING_PERSIST_INTERVAL_SECONDS = 30.0


def _sync_script_status_caches(station_key: str, script_id: int, status: str, *, persisted: bool = True) -> None:
    """Keep in-memory script status caches aligned with direct DB writes."""
    key = (str(station_key or ""), int(script_id or 0))
    status_value = str(status or "Stopped").strip() or "Stopped"
    with _SCRIPT_STATUS_LOCK:
        _SCRIPT_STATUS_RUNTIME_CACHE[key] = status_value
        if persisted:
            _SCRIPT_STATUS_PERSISTED_CACHE[key] = status_value
            _SCRIPT_STATUS_LAST_PERSIST_TS[key] = time.time()


def _clear_script_status_caches(station_key: str, script_id: int) -> None:
    key = (str(station_key or ""), int(script_id or 0))
    with _SCRIPT_STATUS_LOCK:
        _SCRIPT_STATUS_RUNTIME_CACHE.pop(key, None)
        _SCRIPT_STATUS_PERSISTED_CACHE.pop(key, None)
        _SCRIPT_STATUS_LAST_PERSIST_TS.pop(key, None)





def _script_runtime_resolve_value(expr: str, variables: dict[str, str]) -> str:
    value = str(expr or "").strip()
    if not value:
        return ""
    value = value.strip()
    if (value.startswith("(") and value.endswith(")")):
        inner = value[1:-1].strip()
        if inner:
            value = inner
    if ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
        return value[1:-1]
    if value in variables:
        return str(variables.get(value) or "")
    return value


def _parse_station_script_text(raw: str) -> dict:
    actions: list[tuple] = []
    variables: dict[str, str] = {}
    for line in (raw or "").splitlines():
        s = str(line or "").strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("while true"):
            continue

        m_assign = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)$", s)
        if m_assign and not s.lower().startswith("queue_add_file"):
            variables[m_assign.group(1)] = _script_runtime_resolve_value(m_assign.group(2), variables)
            continue

        m_wait = re.match(r'^wait_for_time\(\s*["\']([^"\']+)["\']\s*\)\s*$', s, flags=re.I)
        if m_wait:
            actions.append(("wait_for_time", m_wait.group(1).strip()))
            continue

        m_queue = re.match(r"^queue_add_file\(\s*(.+?)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", s, flags=re.I)
        if m_queue:
            path_expr = m_queue.group(1).strip()
            queue_pos = m_queue.group(2).strip().lower()
            actions.append(("queue_add_file", _script_runtime_resolve_value(path_expr, variables), queue_pos))
            continue

        m_queue_random = re.match(r"^queue_add_random_file\(\s*(.+?)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", s, flags=re.I)
        if m_queue_random:
            path_expr = m_queue_random.group(1).strip()
            queue_pos = m_queue_random.group(2).strip().lower()
            actions.append(("queue_add_random_file", _script_runtime_resolve_value(path_expr, variables), queue_pos))
            continue

        m_queue_dir = re.match(r"^queue_add_directory\(\s*(.+?)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*$", s, flags=re.I)
        if m_queue_dir:
            path_expr = m_queue_dir.group(1).strip()
            queue_pos = m_queue_dir.group(2).strip().lower()
            sort_mode = m_queue_dir.group(3).strip().lower()
            actions.append(("queue_add_directory", _script_runtime_resolve_value(path_expr, variables), queue_pos, sort_mode))
            continue

        if re.match(r"^next\(\s*\)\s*$", s, flags=re.I):
            actions.append(("next",))
            continue

    wait_value = ""
    queue_files: list[dict[str, str]] = []
    queue_path = ""
    queue_pos = "end"
    queue_directory_path = ""
    queue_directory_pos = "end"
    queue_directory_sort = "sorted"
    do_next = False
    for action in actions:
        if action[0] == "wait_for_time" and not wait_value:
            wait_value = str(action[1] or "").strip()
        elif action[0] == "queue_add_file":
            file_path = str(action[1] or "").strip()
            file_pos = str(action[2] or "end").strip().lower() or "end"
            if file_path:
                queue_files.append({"path": file_path, "pos": file_pos})
            if not queue_path:
                queue_path = file_path
                queue_pos = file_pos
        elif action[0] == "queue_add_random_file":
            file_path = str(action[1] or "").strip()
            file_pos = str(action[2] or "end").strip().lower() or "end"
            if file_path:
                queue_files.append({"path": ("__RANDOMDIR__:" + file_path), "pos": file_pos})
        elif action[0] == "queue_add_directory" and not queue_directory_path:
            queue_directory_path = str(action[1] or "").strip()
            queue_directory_pos = str(action[2] or "end").strip().lower() or "end"
            queue_directory_sort = str(action[3] or "sorted").strip().lower() or "sorted"
        elif action[0] == "next":
            do_next = True

    return {
        "wait_for_time": wait_value,
        "queue_files": queue_files,
        "queue_path": queue_path,
        "queue_pos": queue_pos,
        "queue_directory_path": queue_directory_path,
        "queue_directory_pos": queue_directory_pos,
        "queue_directory_sort": queue_directory_sort,
        "do_next": do_next,
    }


# v2793: active scripts use a parsed in-memory definition. The runtime engine
# must not reopen .wbs files on every one-second tick; script files are read only
# when their content is opened/edited or when a script is explicitly activated
# through manual Start / ON-AIR auto-start.
_SCRIPT_DEFINITION_CACHE_LOCK = threading.Lock()
_SCRIPT_DEFINITION_CACHE: dict[tuple[str, int], dict] = {}


def _script_definition_cache_key(station_key: str, script_id: int) -> tuple[str, int]:
    return (str(station_key or "").strip(), int(script_id or 0))


def _cache_station_script_definition(station_key: str, script_id: int, script_path: str, parsed: dict, *, source: str = "memory") -> dict:
    entry = {
        "script_path": str(script_path or "").strip(),
        "parsed": dict(parsed or {}),
        "loaded_at": time.time(),
        "source": str(source or "memory"),
    }
    with _SCRIPT_DEFINITION_CACHE_LOCK:
        _SCRIPT_DEFINITION_CACHE[_script_definition_cache_key(station_key, script_id)] = entry
    return entry


def _invalidate_station_script_definition_cache(station_key: str, script_id: int | None = None) -> None:
    station_key = str(station_key or "").strip()
    with _SCRIPT_DEFINITION_CACHE_LOCK:
        if script_id is None:
            for key in list(_SCRIPT_DEFINITION_CACHE.keys()):
                if key[0] == station_key:
                    _SCRIPT_DEFINITION_CACHE.pop(key, None)
        else:
            _SCRIPT_DEFINITION_CACHE.pop(_script_definition_cache_key(station_key, int(script_id)), None)


def _get_cached_station_script_definition(station_key: str, script_id: int, script_path: str = "") -> dict | None:
    key = _script_definition_cache_key(station_key, script_id)
    expected_path = str(script_path or "").strip()
    with _SCRIPT_DEFINITION_CACHE_LOCK:
        entry = _SCRIPT_DEFINITION_CACHE.get(key)
        if not entry:
            return None
        if expected_path and str(entry.get("script_path") or "").strip() != expected_path:
            _SCRIPT_DEFINITION_CACHE.pop(key, None)
            return None
        return dict(entry)


def _load_station_script_definition_for_start(station_key: str, script_id: int, script_path: str) -> dict | None:
    """Read and parse a .wbs file only at explicit activation time."""
    raw_path = str(script_path or "").strip()
    if not raw_path:
        return None
    full_script_path = _resolve_station_script_file_path(station_key, raw_path)
    if not full_script_path or not os.path.isfile(full_script_path):
        return None
    with open(full_script_path, "r", encoding="utf-8", errors="ignore") as fh:
        parsed = _parse_station_script_text(fh.read())
    entry = _cache_station_script_definition(station_key, script_id, raw_path, parsed, source="file")
    try:
        entry["full_script_path"] = full_script_path
    except Exception:
        pass
    return entry


def _script_definition_wait_value(entry: dict | None) -> str:
    try:
        parsed = (entry or {}).get("parsed") or {}
        if isinstance(parsed, dict):
            return str(parsed.get("wait_for_time") or "").strip()
    except Exception:
        pass
    return ""


def _script_next_run_datetime(wait_value: str, now_dt: datetime) -> datetime | None:
    token = str(wait_value or "").strip()
    if not token:
        return None
    m = re.match(r"^(XX|\*|\d{1,2}):(\d{2}):(\d{2})$", token, flags=re.I)
    if not m:
        return None
    hour_token = m.group(1).upper()
    minute_val = int(m.group(2))
    second_val = int(m.group(3))

    candidates = []
    base = now_dt.replace(microsecond=0)
    for day_offset in range(0, 2):
        current_day = base + timedelta(days=day_offset)
        if hour_token in {"XX", "*"}:
            for hour_val in range(0, 24):
                dt = current_day.replace(hour=hour_val, minute=minute_val, second=second_val)
                if dt >= base:
                    candidates.append(dt)
        else:
            hour_val = int(hour_token)
            dt = current_day.replace(hour=hour_val, minute=minute_val, second=second_val)
            if dt >= base:
                candidates.append(dt)
    if not candidates:
        return None
    return min(candidates)

def _script_wait_eta_seconds(wait_value: str, now_dt: datetime) -> int | None:
    target = _script_next_run_datetime(wait_value, now_dt)
    if target is None:
        return None
    base = now_dt.replace(microsecond=0)
    return max(0, int((target - base).total_seconds()))

def _format_eta_compact(total_seconds: int | None) -> str:
    if total_seconds is None:
        return "0s"
    total = max(0, int(total_seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or hours > 0 or days > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def format_seconds_hhmmss(total_seconds: int | float | None) -> str:
    try:
        total = max(0, int(float(total_seconds or 0)))
    except Exception:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_seconds(total_seconds: int | float | None) -> str:
    return format_seconds_hhmmss(total_seconds)


def parse_seek_target_to_seconds(value) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(math.floor(float(raw))))
    except Exception:
        pass
    parts = raw.split(":")
    if not parts:
        return 0
    try:
        parts = [int(float(part.strip() or 0)) for part in parts]
    except Exception:
        return 0
    if len(parts) == 1:
        return max(0, parts[0])
    if len(parts) == 2:
        minutes, seconds = parts
        return max(0, minutes * 60 + seconds)
    hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
    return max(0, hours * 3600 + minutes * 60 + seconds)


def _format_script_waiting_status(wait_value: str, now_dt: datetime) -> str:
    target = _script_next_run_datetime(wait_value, now_dt)
    eta = _script_wait_eta_seconds(wait_value, now_dt)
    if target is None or eta is None:
        return "Waiting"
    return f"Waiting for time {target.strftime('%H:%M:%S')} (ETA: -{_format_eta_compact(eta)})"


def _script_waiting_payload_from_cached_definition(station_key: str, script_id: int, script_path: str, status: str, now_dt: datetime | None = None) -> dict:
    """Return client-side countdown metadata without reading the .wbs file.

    v2794: the Scripts/Scheduler window must be able to count down smoothly while
    the browser is open, but the frontend must not poll SQLite every second just
    to get a changed ETA string.  Active scripts already have their parsed .wbs
    definition in memory from manual Start / ON-AIR auto-start, so expose the
    next target time derived from that cache.
    """
    now_dt = (now_dt or datetime.now()).replace(microsecond=0)
    script_id = int(script_id or 0)
    station_key = str(station_key or "").strip()
    script_path = str(script_path or "").strip()
    base_status = str(status or "Stopped").strip() or "Stopped"

    runtime_status = ""
    try:
        with _SCRIPT_STATUS_LOCK:
            runtime_status = str(_SCRIPT_STATUS_RUNTIME_CACHE.get((station_key, script_id)) or "").strip()
    except Exception:
        runtime_status = ""
    if runtime_status:
        base_status = runtime_status

    payload = {
        "status": base_status,
        "wait_for_time": "",
        "next_run_at": "",
        "next_run_time": "",
        "server_time": now_dt.isoformat(sep=" ", timespec="seconds"),
    }

    if not _script_status_is_active(base_status):
        return payload

    entry = _get_cached_station_script_definition(station_key, script_id, script_path)
    wait_value = _script_definition_wait_value(entry) if entry else ""
    if not wait_value:
        return payload

    target = _script_next_run_datetime(wait_value, now_dt)
    if target is None:
        payload["wait_for_time"] = wait_value
        payload["status"] = "Waiting"
        return payload

    payload["wait_for_time"] = wait_value
    payload["next_run_at"] = target.isoformat(sep=" ", timespec="seconds")
    payload["next_run_time"] = target.strftime("%H:%M:%S")
    payload["status"] = _format_script_waiting_status(wait_value, now_dt)
    return payload

def _script_status_is_active(status: str) -> bool:
    value = str(status or "").strip()
    if not value:
        return False
    upper = value.upper()
    if upper in {"STOPPED", "ERROR"}:
        return False
    if upper == "RUNNING":
        return True
    if value.startswith("Waiting for time "):
        return True
    if upper == "WAITING":
        return True
    return False

def _script_due_run_datetime(wait_value: str, now_dt: datetime, grace_seconds: int = 5) -> datetime | None:
    """Return the scheduled timestamp that is due now, with a small late-tick grace.

    The engine ticks once per second, but queue/AB work can delay a tick by a
    few seconds.  A script scheduled at XX:59:40 must still run at XX:59:41..45
    instead of being skipped for an entire hour.
    """
    token = str(wait_value or "").strip()
    if not token:
        return None
    m = re.match(r"^(XX|\*|\d{1,2}):(\d{2}):(\d{2})$", token, flags=re.I)
    if not m:
        return None
    hour_token = m.group(1).upper()
    minute_val = int(m.group(2))
    second_val = int(m.group(3))
    base = now_dt.replace(microsecond=0)
    candidates: list[datetime] = []
    for day_offset in (-1, 0):
        day = base + timedelta(days=day_offset)
        if hour_token in {"XX", "*"}:
            for hour_val in range(24):
                candidates.append(day.replace(hour=hour_val, minute=minute_val, second=second_val))
        else:
            candidates.append(day.replace(hour=int(hour_token), minute=minute_val, second=second_val))
    grace = max(0, int(grace_seconds))
    due = [dt for dt in candidates if dt <= base and (base - dt).total_seconds() <= grace]
    if not due:
        return None
    return max(due)


def _script_resolve_runtime_vars(path_template: str, now_dt: datetime) -> str:
    next_hour_2digit = f"{((int(now_dt.hour) + 1) % 24):02d}"
    value = str(path_template or "")
    value = value.replace("{next_hour_2digit}", next_hour_2digit)
    value = value.replace("{hour_2digit}", f"{now_dt.hour:02d}")
    return value


def _ensure_track_for_station_path(station_key: str, file_path: str) -> int | None:
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        track_id = ensure_track(conn, file_path)
        conn.commit()
        conn.close()
        return int(track_id) if track_id else None
    except Exception:
        return None


def _resolve_directory_to_track_ids_for_station(station_key: str, directory_path: str, sort_mode: str = "sorted") -> list[int]:
    """Resolve a directory tree to station track IDs in deterministic ABC order across all subfolders."""
    raw_dir = str(directory_path or "").strip()
    if not raw_dir:
        return []

    resolved_dir = _resolve_station_script_file_path(station_key, raw_dir)
    if not resolved_dir or not os.path.isdir(resolved_dir):
        return []
    if str(sort_mode or "sorted").strip().lower() != "sorted":
        return []

    audio_exts = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac"}
    try:
        file_paths: list[str] = []
        for walk_base, walk_dirs, walk_files in os.walk(resolved_dir):
            walk_dirs.sort(key=lambda name: str(name or "").lower())
            for filename in sorted(walk_files, key=lambda name: str(name or "").lower()):
                if os.path.splitext(filename)[1].lower() not in audio_exts:
                    continue
                full_path = os.path.join(walk_base, filename)
                if os.path.isfile(full_path):
                    file_paths.append(full_path)
        file_paths.sort(key=lambda full_path: os.path.relpath(full_path, resolved_dir).lower())
    except Exception:
        return []

    if not file_paths:
        return []

    track_ids: list[int] = []
    for full_path in file_paths:
        track_id = _ensure_track_for_station_path(station_key, full_path)
        if not track_id:
            continue
        track_ids.append(int(track_id))
    return track_ids


def _enqueue_directory_for_station(station_key: str, directory_path: str, priority: str, sort_mode: str = "sorted") -> list[int]:
    """Re-read a directory tree and enqueue all supported audio files in deterministic ABC order."""
    track_ids = _resolve_directory_to_track_ids_for_station(station_key, directory_path, sort_mode)
    if not track_ids:
        return []
    return _enqueue_track_ids_return_queue_ids_for_station(station_key, track_ids, priority)


def _get_station_queue_head_id(station_key: str) -> int:
    try:
        return _get_playback_repository().get_queue_head_id(station_key)
    except Exception:
        return 0


def _perform_station_next_action(station_key: str) -> bool:
    """Run the same backend NEXT helper that the UI manual Next button uses."""
    try:
        resolved_station = _resolve_station_id_to_db(station_key or "") or os.path.basename(str(station_key or "").strip())
        result = _perform_player_manual_next_action(resolved_station, action="next", source="scheduler")
        success = bool((result or {}).get("success"))
        return bool(success)
    except Exception as exc:
        return False


def _station_url_playback_active(station_key: str) -> bool:
    """Return True only when the station's currently audible native source is a URL."""
    station = str(station_key or "").strip()
    if not station:
        return False
    try:
        state = dict(_native_station_state(station) or {})
    except Exception:
        state = {}
    if not bool(state.get("running")):
        return False

    active_path = normalize_media_path(str(state.get("native_audio_probe_path") or "").strip())
    if active_path.startswith(("http://", "https://")):
        return True

    try:
        active_line = _native_status_line_for_state(station, state)
        if active_line and bool((_ab_line_info(active_line) or {}).get("stream_source")):
            return True
    except Exception:
        pass
    return False


def _mark_station_script_url_skip(
    station_key: str,
    script_id: int,
    wait_value: str,
    due_dt: datetime,
    run_key: str,
) -> None:
    """Consume one due occurrence without queueing it for later catch-up."""
    cache_key = (str(station_key), int(script_id))
    _SCRIPT_ENGINE_LAST_RUN[cache_key] = str(run_key or "")
    next_reference = due_dt.replace(microsecond=0) + timedelta(seconds=1)
    _set_station_script_status(
        station_key,
        script_id,
        _format_script_waiting_status(wait_value, next_reference),
    )
    if _RUNTIME_LOGGING_ENABLED:
        logger.warning(
            "Scheduled script skipped because URL playback is active: station=%s script_id=%s due=%s",
            station_key,
            int(script_id),
            str(run_key or ""),
        )


def _cancel_scheduled_script_queue_items(
    station_key: str,
    queue_ids,
    reason: str = "url_playback_active",
) -> int:
    """Remove only queue rows created by a skipped scheduled-script occurrence."""
    normalized: list[int] = []
    seen: set[int] = set()
    for queue_id in queue_ids or []:
        try:
            value = int(queue_id)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in seen:
            normalized.append(value)
            seen.add(value)
    if not normalized:
        return 0

    try:
        removed = int(
            _get_playback_repository().remove_queue_items(
                normalized,
                station_key=station_key,
            )
            or 0
        )
    except Exception:
        removed = 0
    if removed <= 0:
        return 0

    try:
        _publish_ui_queue_history_changed(station_key, "scheduled_script_url_skip")
    except Exception:
        pass
    try:
        wake_autodj_worker()
    except Exception:
        pass
    try:
        with station_runtime_context(station_key):
            _ab_replan_after_queue_mutation(reason="scheduled_script_url_skip_rollback")
    except Exception:
        pass
    if _RUNTIME_LOGGING_ENABLED:
        logger.warning(
            "Scheduled script queue rollback after URL guard: station=%s queue_ids=%s reason=%s",
            station_key,
            normalized,
            str(reason or "url_playback_active"),
        )
    return removed


def _run_station_script_once(station_key: str, script_row: dict, now_dt: datetime) -> None:
    script_id = int(script_row.get("id") or 0)
    raw_path = str(script_row.get("script_path") or "").strip()
    if not raw_path:
        _set_station_script_status(station_key, script_id, "Stopped")
        return

    entry = _get_cached_station_script_definition(station_key, script_id, raw_path)
    if not entry:
        _set_station_script_status(station_key, script_id, "Error")
        return
    parsed = entry.get("parsed") or {}
    if not isinstance(parsed, dict):
        _set_station_script_status(station_key, script_id, "Error")
        return

    wait_value = str(parsed.get("wait_for_time") or "").strip()
    queue_files_raw = parsed.get("queue_files") or []
    queue_files: list[dict[str, str]] = []
    if isinstance(queue_files_raw, list):
        for item in queue_files_raw:
            if not isinstance(item, dict):
                continue
            item_path = str(item.get("path") or "").strip()
            item_pos = str(item.get("pos") or "end").strip().lower() or "end"
            if item_path:
                queue_files.append({"path": item_path, "pos": item_pos})
    queue_path = str(parsed.get("queue_path") or "").strip()
    queue_pos = str(parsed.get("queue_pos") or "end").strip().lower() or "end"
    queue_directory_path = str(parsed.get("queue_directory_path") or "").strip()
    queue_directory_pos = str(parsed.get("queue_directory_pos") or "end").strip().lower() or "end"
    queue_directory_sort = str(parsed.get("queue_directory_sort") or "sorted").strip().lower() or "sorted"
    do_next = bool(parsed.get("do_next"))

    if not wait_value or (not queue_files and not queue_path and not queue_directory_path):
        _set_station_script_status(station_key, script_id, "Error")
        return

    due_dt = _script_due_run_datetime(wait_value, now_dt)
    waiting_status = _format_script_waiting_status(wait_value, now_dt if due_dt is None else (due_dt + timedelta(seconds=1)))
    _set_station_script_status(station_key, script_id, waiting_status)
    if due_dt is None:
        return

    run_dt = due_dt
    run_key = run_dt.strftime("%Y-%m-%d %H:%M:%S")
    cache_key = (str(station_key), int(script_id))
    if _SCRIPT_ENGINE_LAST_RUN.get(cache_key) == run_key:
        return

    # First guard: an already-running URL consumes this occurrence without any
    # queue mutation. The missed announcement is intentionally not replayed.
    if _station_url_playback_active(station_key):
        _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
        return

    created_queue_ids = []
    ok = False

    if queue_directory_path:
        runtime_directory_path = _script_resolve_runtime_vars(queue_directory_path, run_dt)
        full_directory_path = _resolve_station_script_file_path(station_key, runtime_directory_path)
        if not full_directory_path or not os.path.isdir(full_directory_path):
            _set_station_script_status(station_key, script_id, "Error")
            _SCRIPT_ENGINE_LAST_RUN[cache_key] = run_key
            return
        directory_track_ids = _resolve_directory_to_track_ids_for_station(
            station_key,
            full_directory_path,
            queue_directory_sort,
        )
        if not directory_track_ids:
            _set_station_script_status(station_key, script_id, "Error")
            _SCRIPT_ENGINE_LAST_RUN[cache_key] = run_key
            return
        # Second guard: check again immediately before the first persistent
        # queue write, closing the normal scheduler/URL-start race window.
        if _station_url_playback_active(station_key):
            _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
            return
        created_queue_ids = _enqueue_track_ids_return_queue_ids_for_station(
            station_key,
            directory_track_ids,
            queue_directory_pos,
        )
        ok = bool(created_queue_ids)
    else:
        file_entries = queue_files or ([{"path": queue_path, "pos": queue_pos}] if queue_path else [])
        resolved_file_entries: list[dict[str, object]] = []
        for file_entry in file_entries:
            media_path = _script_resolve_runtime_vars(str(file_entry.get("path") or ""), run_dt)
            if media_path.startswith("__RANDOMDIR__:"):
                import random
                random_dir = media_path.split(":",1)[1]
                full_random_dir = _resolve_station_script_file_path(station_key, random_dir)
                if not full_random_dir or not os.path.isdir(full_random_dir):
                    continue
                candidates=[]
                for n in os.listdir(full_random_dir):
                    p=os.path.join(full_random_dir,n)
                    if os.path.isfile(p):
                        candidates.append(p)
                if not candidates:
                    continue
                full_media_path = random.choice(candidates)
                media_path = full_media_path
            
            media_pos = str(file_entry.get("pos") or "end").strip().lower() or "end"
            full_media_path = _resolve_station_script_file_path(station_key, media_path)
            if not full_media_path or not os.path.isfile(full_media_path):
                _SCRIPT_ENGINE_LAST_RUN[cache_key] = run_key
                _set_station_script_status(station_key, script_id, _format_script_waiting_status(wait_value, now_dt + timedelta(seconds=1)))
                return

            track_id = _ensure_track_for_station_path(station_key, full_media_path)
            if not track_id:
                _set_station_script_status(station_key, script_id, "Error")
                _SCRIPT_ENGINE_LAST_RUN[cache_key] = run_key
                return
            resolved_file_entries.append({"track_id": int(track_id), "pos": media_pos, "path": full_media_path})

        created_queue_ids = []
        top_queue_ids: list[int] = []
        group_track_ids: list[int] = []
        group_pos: str | None = None

        url_skip_detected = False

        def flush_file_group() -> bool:
            nonlocal group_track_ids, group_pos, created_queue_ids, top_queue_ids, url_skip_detected
            if not group_track_ids:
                return True
            # Check before every grouped queue insert. If an URL became active
            # after an earlier group, the caller rolls those rows back as well.
            if _station_url_playback_active(station_key):
                url_skip_detected = True
                return False
            current_pos = str(group_pos or "end").strip().lower() or "end"
            group_queue_ids = _enqueue_track_ids_return_queue_ids_for_station(station_key, [int(tid) for tid in group_track_ids], current_pos)
            if not group_queue_ids:
                return False
            created_queue_ids.extend(group_queue_ids)
            # Script-created files are ordinary queue items. Their playback mode is
            # determined by the same duration-based short-item and crossfade rules
            # as files inserted manually through the queue UI.
            if current_pos == "top":
                top_queue_ids.extend(group_queue_ids)
            group_track_ids = []
            group_pos = None
            return True

        for resolved_entry in resolved_file_entries:
            entry_pos = str(resolved_entry.get("pos") or "end").strip().lower() or "end"
            entry_track_id = int(resolved_entry.get("track_id") or 0)
            if group_pos is None:
                group_pos = entry_pos
            if entry_pos != group_pos:
                if not flush_file_group():
                    ok = False
                    break
                group_pos = entry_pos
            group_track_ids.append(entry_track_id)
        else:
            ok = flush_file_group()

        if url_skip_detected:
            _cancel_scheduled_script_queue_items(station_key, created_queue_ids)
            _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
            return

        if ok and top_queue_ids:
            try:
                _ab_invalidate_pending_replans(reason="script_top_group_before_reorder")
            except Exception:
                pass
            moved = _move_station_queue_ids_to_front(station_key, top_queue_ids)
            ok = bool(moved)

    # Directory scripts use the same ordinary queue-item playback path as file
    # scripts; no script-specific clean or hard-transition metadata is added.

    if not ok and created_queue_ids:
        _cancel_scheduled_script_queue_items(station_key, created_queue_ids, "script_queue_insert_failed")

    # Third guard: if URL playback became active during queue construction,
    # remove exactly this occurrence's rows before any reorder or replan.
    if ok and _station_url_playback_active(station_key):
        _cancel_scheduled_script_queue_items(station_key, created_queue_ids)
        _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
        return

    if ok and queue_directory_path and queue_directory_pos == "top":
        try:
            _ab_invalidate_pending_replans(reason="script_directory_top_before_reorder")
        except Exception:
            pass
        moved = _move_station_queue_ids_to_front(station_key, created_queue_ids)
        ok = bool(moved)

    if ok:
        try:
            try:
                wake_autodj_worker()
            except Exception:
                pass
            with station_runtime_context(station_key):
                _ab_replan_after_queue_mutation(reason="script_move_to_front")
        except Exception as exc:
            ok = False

    # Fourth guard: replan can overlap an URL track start. Roll back the
    # announcement before submitting the serialized Manual Next request.
    if ok and _station_url_playback_active(station_key):
        _cancel_scheduled_script_queue_items(station_key, created_queue_ids)
        _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
        return

    if ok and do_next:
        # Script next() must use exactly the same backend NEXT helper as the UI
        # manual Next button.  Do not call /api/control and do not use a separate
        # script-break implementation here.
        time.sleep(0.2)
        next_result = _perform_player_manual_next_action(
            station_key,
            action="next",
            source="script",
            guarded_queue_ids=created_queue_ids,
        )
        next_ok = bool((next_result or {}).get("success"))
        if bool((next_result or {}).get("skipped")):
            _cancel_scheduled_script_queue_items(station_key, created_queue_ids)
            _mark_station_script_url_skip(station_key, script_id, wait_value, due_dt, run_key)
            return
    elif ok:
        try:
            wake_autodj_worker()
        except Exception:
            pass

    _SCRIPT_ENGINE_LAST_RUN[cache_key] = run_key
    _set_station_script_status(station_key, script_id, _format_script_waiting_status(wait_value, now_dt) if ok else "Error")


def script_engine_process_due_once() -> bool:
    if not _SCRIPT_ENGINE_LOCK.acquire(blocking=False):
        return True
    try:
        now_dt = datetime.now().replace(microsecond=0)
        any_station_on_air = False
        station_keys = get_registered_station_keys() or []
        if not station_keys:
            try:
                active_key = (get_active_station_key() or "").strip()
                if active_key:
                    station_keys = [active_key]
            except Exception:
                station_keys = []
        for station_key in station_keys:
            station_on_air = is_station_on_air(station_key)

            if not station_on_air:
                was_on_air = _SCRIPT_ENGINE_LAST_ON_AIR_BY_STATION.get(station_key)
                _SCRIPT_ENGINE_LAST_ON_AIR_BY_STATION[station_key] = False
                # Only touch the station DB on startup or on the ON-AIR -> OFF-AIR
                # edge.  The old loop re-wrote Stopped every second while idle.
                if was_on_air is None or was_on_air is True:
                    scripts = _read_station_scripts(station_key)
                    for item in scripts:
                        script_id = int(item.get("id") or 0)
                        status_value = str(item.get("status") or "Stopped").strip() or "Stopped"
                        auto_start = 1 if int(item.get("auto_start") or 0) else 0
                        script_active = _script_status_is_active(status_value)
                        if auto_start or script_active:
                            _set_station_script_status(station_key, script_id, "Stopped")
                continue

            any_station_on_air = True
            _SCRIPT_ENGINE_LAST_ON_AIR_BY_STATION[station_key] = True
            scripts = _read_station_scripts(station_key)
            for item in scripts:
                script_id = int(item.get("id") or 0)
                status_value = str(item.get("status") or "Stopped").strip() or "Stopped"
                auto_start = 1 if int(item.get("auto_start") or 0) else 0
                script_active = _script_status_is_active(status_value)
                if script_active:
                    _run_station_script_once(station_key, item, now_dt)
                else:
                    # Auto-start is intentionally handled only by the ON-AIR transition
                    # endpoint. Do not restart an auto-start script just because the
                    # station is still on-air; a manual STOP must keep it stopped.
                    if auto_start:
                        pass
                    if status_value != "Stopped":
                        _set_station_script_status(station_key, script_id, "Stopped")
        return any_station_on_air
    finally:
        _SCRIPT_ENGINE_LOCK.release()


def _script_engine_loop():
    while True:
        sleep_seconds = _SCRIPT_ENGINE_ACTIVE_POLL_SECONDS
        any_on_air = False
        try:
            any_on_air = script_engine_process_due_once()
            if not any_on_air:
                sleep_seconds = _SCRIPT_ENGINE_IDLE_POLL_SECONDS
        except Exception:
            pass
        if any_on_air:
            time.sleep(sleep_seconds)
        else:
            _wait_idle_helper_event(_SCRIPT_ENGINE_WAKE_EVENT, sleep_seconds)


_SCRIPT_ENGINE_THREAD = None
_SCRIPT_ENGINE_THREAD_LOCK = threading.Lock()

def start_script_engine_thread():
    global _SCRIPT_ENGINE_THREAD
    with _SCRIPT_ENGINE_THREAD_LOCK:
        if _SCRIPT_ENGINE_THREAD is not None and _SCRIPT_ENGINE_THREAD.is_alive():
            return
        t = threading.Thread(target=_script_engine_loop, daemon=True)
        _SCRIPT_ENGINE_THREAD = t
        t.start()


def ensure_station_registry(force: bool = False) -> None:
    """Ensure the station registry exists once per app process."""
    global _STATION_REGISTRY_READY
    if (not force) and _STATION_REGISTRY_READY:
        return
    with _STATION_REGISTRY_LOCK:
        if (not force) and _STATION_REGISTRY_READY:
            return
        _ensure_station_registry_uncached()
        _STATION_REGISTRY_READY = True


def _ensure_station_registry_uncached() -> None:
    """Ensure the current station registry exists and prune invalid rows."""
    init_global_db()
    conn = None
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT id, db_filename FROM stations")
        rows = c.fetchall() or []
        deleted_any = False
        for row in rows:
            filename = os.path.basename(str(row["db_filename"] or "").strip())
            station_exists = False
            if filename.endswith(".db"):
                try:
                    station_exists = bool(os.path.exists(resolve_station_db_path(filename)))
                except Exception:
                    station_exists = False
            if not station_exists:
                c.execute("DELETE FROM stations WHERE id = ?", (int(row["id"]),))
                deleted_any = True
        if deleted_any:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()


def get_registered_stations() -> list[dict]:
    """Return stations list for the UI dropdown."""
    ensure_station_registry()
    out: list[dict] = []
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT name, db_filename FROM stations ORDER BY id ASC")
        rows = c.fetchall()
        conn.close()
        for r in rows:
            out.append({"id": r["db_filename"], "name": r["name"]})
    except Exception:
        pass
    return out


def get_registered_station_keys() -> list[str]:
    """Return station DB filenames from the registry, normalized and de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    try:
        for st in (get_registered_stations() or []):
            sk = os.path.basename(str((st or {}).get("id") or "").strip())
            if not sk or (not sk.endswith(".db")) or sk in seen:
                continue
            seen.add(sk)
            out.append(sk)
    except Exception:
        pass
    return out


def ensure_active_station_in_session() -> None:
    """Pick an active station for the session if missing."""
    ensure_station_registry()
    try:
        active = session.get("active_station_db")
    except Exception:
        active = None

    # Validate any existing cookie/session value.
    # If it points to a non-existent file (or a non-.db name), do NOT keep it,
    # otherwise SQLite would happily create a brand new empty file on first connect.
    if active:
        try:
            active_fn = os.path.basename(str(active))
            active_path = resolve_station_db_path(active_fn)
            if active_path and os.path.exists(active_path):
                return
        except Exception:
            pass
        try:
            session.pop("active_station_db", None)
        except Exception:
            pass

    # Try to restore the last-used station, but ONLY if it still exists.
    # Old installs may have a stale last_station_db value which would otherwise
    # cause SQLite to create a brand new empty station DB file on first connect.
    last = _app_state_get("last_station_db").strip()
    if last:
        last_fn = os.path.basename(last)
        last_path = resolve_station_db_path(last_fn)
        if last_path and os.path.exists(last_path):
            session["active_station_db"] = last_fn
            try:
                _app_state_set("active_station_db", last_fn)
                _app_state_set("last_station_db", last_fn)
            except Exception:
                pass
            return

    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT db_filename FROM stations ORDER BY id ASC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if row is not None and row["db_filename"]:
            candidate_fn = os.path.basename(str(row["db_filename"] or ""))
            candidate_path = resolve_station_db_path(candidate_fn)
            if candidate_path and os.path.exists(candidate_path):
                session["active_station_db"] = candidate_fn
                try:
                    _app_state_set("active_station_db", candidate_fn)
                    _app_state_set("last_station_db", candidate_fn)
                except Exception:
                    pass
    except Exception:
        pass


def ensure_active_station_global() -> Optional[str]:
    """Ensure there is an active station stored in global app_state for background threads."""
    try:
        ensure_station_registry()
    except Exception:
        pass

    # If already set and valid, keep it.
    try:
        active = str(_app_state_get("active_station_db") or "").strip()
    except Exception:
        active = ""
    if active:
        fn = os.path.basename(active)
        if fn.endswith(".db"):
            p = resolve_station_db_path(fn)
            if p and os.path.exists(p):
                return fn

    # Fall back to last_station_db.
    try:
        last = str(_app_state_get("last_station_db") or "").strip()
    except Exception:
        last = ""
    if last:
        fn = os.path.basename(last)
        if fn.endswith(".db"):
            p = resolve_station_db_path(fn)
            if p and os.path.exists(p):
                try:
                    _app_state_set("active_station_db", fn)
                except Exception:
                    pass
                return fn

    # Pick the first valid station in the current registry.
    try:
        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT db_filename FROM stations ORDER BY id ASC")
        rows = c.fetchall()
        try:
            conn.close()
        except Exception:
            pass

        for row in rows or []:
            fn = os.path.basename(str(row["db_filename"] or "").strip())
            if not fn.endswith(".db"):
                continue
            p = resolve_station_db_path(fn)
            if p and os.path.exists(p):
                try:
                    _app_state_set("active_station_db", fn)
                    _app_state_set("last_station_db", fn)
                except Exception:
                    pass
                return fn
    except Exception:
        pass
    return None
def get_db():
    # Ensure a station is selected for this session.
    try:
        ensure_active_station_global()
    except Exception:
        pass

    # Ensure a station is selected for this session.
    try:
        ensure_active_station_in_session()
    except Exception:
        pass


    db_path = get_active_station_db_path()
    if not db_path:
        raise NoActiveStationError("No active station selected")
    # Never auto-create a station DB.
    # Station DB files are created only via "Add New Station".
    if not os.path.exists(db_path):
        raise NoActiveStationError("No active station selected")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    return _connect_sqlite_reusable(db_path)


def get_db_for_station(station_key: str):
    """Open a station DB by explicit station_key (db filename or numeric station id).

    This must NOT depend on the active web session. It is used by background tasks and
    by native callbacks so queue/history updates are correct even when the station
    is not currently selected in the browser.
    """
    sid = _resolve_station_id_to_db(station_key or "")
    if not sid:
        sid = os.path.basename((station_key or "").strip())
    db_path = resolve_station_db_path(sid)
    if not db_path or not db_path.endswith(".db"):
        raise NoActiveStationError("Invalid station")
    if not os.path.exists(db_path):
        raise NoActiveStationError("Station DB not found")
    return _connect_sqlite_reusable(db_path)


def ensure_studio_layout_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS studio_layout_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            layout_name TEXT NOT NULL,
            panel_id TEXT NOT NULL,
            x INTEGER NOT NULL DEFAULT 0,
            y INTEGER NOT NULL DEFAULT 0,
            width INTEGER,
            height INTEGER,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, layout_name, panel_id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS studio_layout_preferences (
            user_id INTEGER PRIMARY KEY,
            preferred_layout TEXT NOT NULL DEFAULT 'layout-1',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def get_studio_layout_state_for_user(conn: sqlite3.Connection, user_id: int) -> dict:
    ensure_studio_layout_tables(conn)
    cur = conn.cursor()
    cur.execute(
        "SELECT preferred_layout FROM studio_layout_preferences WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    preferred_layout = row[0] if row else 'layout-1'
    cur.execute(
        """
        SELECT layout_name, panel_id, x, y, width, height
        FROM studio_layout_state
        WHERE user_id = ?
        """,
        (user_id,),
    )
    layouts = {'layout-1': {}, 'layout-2': {}}
    for item in cur.fetchall():
        layout_name = str(item['layout_name'] or '').strip() or 'layout-1'
        panel_id = str(item['panel_id'] or '').strip()
        if not panel_id:
            continue
        layouts.setdefault(layout_name, {})[panel_id] = {
            'x': int(item['x'] or 0),
            'y': int(item['y'] or 0),
            'width': int(item['width']) if item['width'] is not None else None,
            'height': int(item['height']) if item['height'] is not None else None,
        }
    for default_layout_name in ('layout-1', 'layout-2'):
        if layouts.get(default_layout_name):
            continue
        try:
            layouts[default_layout_name] = load_studio_layout_template_file(default_layout_name)
        except Exception:
            layouts[default_layout_name] = layouts.get(default_layout_name) or {}
    return {
        'preferred_layout': preferred_layout if preferred_layout in {'layout-1', 'layout-2'} else 'layout-1',
        'layouts': layouts,
    }


def save_studio_layout_state_for_user(
    conn: sqlite3.Connection,
    user_id: int,
    layout_name: str,
    panels: dict,
    preferred_layout: str | None = None,
) -> None:
    ensure_studio_layout_tables(conn)
    layout_name = _normalize_studio_layout_name(layout_name)
    normalized_panels = _normalize_studio_layout_panels(panels)
    now_iso = datetime.now().isoformat()
    cur = conn.cursor()
    if preferred_layout in {'layout-1', 'layout-2'}:
        cur.execute(
            """
            INSERT INTO studio_layout_preferences (user_id, preferred_layout, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                preferred_layout = excluded.preferred_layout,
                updated_at = excluded.updated_at
            """,
            (user_id, preferred_layout, now_iso),
        )
    cur.execute(
        "DELETE FROM studio_layout_state WHERE user_id = ? AND layout_name = ?",
        (user_id, layout_name),
    )
    for panel_id, values in normalized_panels.items():
        cur.execute(
            """
            INSERT INTO studio_layout_state (user_id, layout_name, panel_id, x, y, width, height, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, layout_name, str(panel_id), values['x'], values['y'], values['width'], values['height'], now_iso),
        )
    conn.commit()


def _normalize_studio_layout_name(layout_name: str | None) -> str:
    layout_name = str(layout_name or 'layout-1').strip()
    return layout_name if layout_name in {'layout-1', 'layout-2'} else 'layout-1'


def _normalize_studio_layout_panels(panels: dict | None) -> dict:
    normalized: dict[str, dict[str, int | None]] = {}
    for panel_id, raw in (panels or {}).items():
        if not panel_id:
            continue
        values = raw or {}
        x = int(round(float(values.get('x', 0) or 0)))
        y = int(round(float(values.get('y', 0) or 0)))
        width = values.get('width')
        height = values.get('height')
        width = int(round(float(width))) if width not in (None, '', False) else None
        height = int(round(float(height))) if height not in (None, '', False) else None
        normalized[str(panel_id)] = {
            'x': x,
            'y': y,
            'width': width,
            'height': height,
        }
    return normalized


def get_studio_layout_template_path(layout_name: str) -> str:
    normalized = _normalize_studio_layout_name(layout_name)
    filename = 'Layout2_default.pos' if normalized == 'layout-2' else 'Layout1_default.pos'
    return os.path.join(BASE_DIR, 'html', filename)


def save_studio_layout_template_file(layout_name: str, panels: dict | None) -> str:
    path = get_studio_layout_template_path(layout_name)
    payload = {
        'layout': _normalize_studio_layout_name(layout_name),
        'saved_at': datetime.now().isoformat(),
        'panels': _normalize_studio_layout_panels(panels),
    }
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return path


def load_studio_layout_template_file(layout_name: str) -> dict:
    path = get_studio_layout_template_path(layout_name)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, 'r', encoding='utf-8') as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and isinstance(payload.get('panels'), dict):
        return _normalize_studio_layout_panels(payload.get('panels'))
    if isinstance(payload, dict):
        return _normalize_studio_layout_panels(payload)
    return {}


def initialize_studio_layout_defaults_for_user(conn: sqlite3.Connection, user_id: int) -> None:
    ensure_studio_layout_tables(conn)
    for layout_name in ('layout-1', 'layout-2'):
        try:
            panels = load_studio_layout_template_file(layout_name)
        except Exception:
            panels = {}
        if not panels:
            continue
        save_studio_layout_state_for_user(
            conn,
            user_id,
            layout_name,
            panels,
            preferred_layout='layout-1',
        )


def get_encoder_started_at(stream_id: int):
    """Return the stored encoder start timestamp (ISO 8601) for a stream, or None."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT started_at FROM encoder_runtime_state WHERE stream_id = ?", (int(stream_id),))
        row = c.fetchone()
        conn.close()
        if not row:
            return None
        try:
            return row["started_at"]
        except Exception:
            return row[0]
    except Exception:
        return None


def set_encoder_started_at(stream_id: int, started_at_iso: str):
    """Persist encoder start timestamp for a stream."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO encoder_runtime_state (stream_id, started_at) VALUES (?, ?)",
            (int(stream_id), started_at_iso),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"set_encoder_started_at error: {e}")


def clear_encoder_started_at(stream_id: int):
    """Clear encoder start timestamp for a stream."""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM encoder_runtime_state WHERE stream_id = ?", (int(stream_id),))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"clear_encoder_started_at error: {e}")


def _create_canonical_settings_table(cursor, table_name: str = "settings") -> None:
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            radio_name TEXT,
            music_library_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            ssproc_appimage TEXT,
            dsp_enabled INTEGER NOT NULL DEFAULT 1,
            cue_in_threshold REAL NOT NULL DEFAULT -34.0,
            cue_out_threshold REAL NOT NULL DEFAULT -38.0,
            cross_threshold REAL NOT NULL DEFAULT -7.0,
            overlap_seconds REAL NOT NULL DEFAULT 2.0,
            cue_start_offset_seconds REAL NOT NULL DEFAULT 3.0,
            gap_killer_start_dbfs REAL NOT NULL DEFAULT -20.0,
            gap_killer_end_dbfs REAL NOT NULL DEFAULT -24.0,
            crossfade_trigger_relative_db REAL NOT NULL DEFAULT -7.0,
            crossfade_fallback_seconds REAL NOT NULL DEFAULT 3.0,
            crossfade_min_seconds REAL NOT NULL DEFAULT 0.1,
            crossfade_max_seconds REAL NOT NULL DEFAULT 6.0,
            crossfade_fade_out_seconds REAL NOT NULL DEFAULT 5.0,
            no_crossfade_max_duration_sec REAL NOT NULL DEFAULT 65.0
        )
        """
    )



def init_db(force: bool = False):
    db_path = get_active_station_db_path()
    if not db_path:
        raise NoActiveStationError("No active station selected")
    normalized_db_path = os.path.abspath(db_path)
    if not force and normalized_db_path in _initialized_station_dbs:
        return

    with _db_init_lock:
        if not force and normalized_db_path in _initialized_station_dbs:
            return
        conn = get_db()
        init_ok = False
        try:
            c = conn.cursor()
            _create_canonical_settings_table(c)

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS icecast_streams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    mount TEXT NOT NULL,
            password TEXT,
                    codec TEXT,
                    bitrate INTEGER,
                    autostart INTEGER DEFAULT 0,
                    add_year_to_icecast_meta INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    station_description TEXT,
                    genre TEXT,
                    website_url TEXT
                )
                """
            )


            c.execute(
                """
            CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    color TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )


            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    cue_in_seconds REAL,
                    cue_out_seconds REAL,
                    cue_trimmed_seconds REAL,
                    cue_duration_seconds REAL,
                    cue_fade_start_seconds REAL,
                    cue_analyzed_at TEXT,
                    audio_start_seconds REAL,
                    audio_end_seconds REAL,
                    audio_analyzed_at TEXT,
                    analysis_file_size INTEGER,
                    analysis_file_mtime_ns INTEGER,
                    analysis_settings_hash TEXT,
                    analysis_analyzer_version TEXT,
                    analysis_updated_at TEXT,
                    analysis_source TEXT,
                    analysis_error TEXT,
                    runtime_duration_seconds REAL,
                    runtime_duration_verified_at TEXT,
                    runtime_duration_file_size INTEGER,
                    runtime_duration_file_mtime_ns INTEGER,
                    runtime_duration_source TEXT
                )
                """
            )


            c.execute(
                """
                CREATE TABLE IF NOT EXISTS category_tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    track_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(category_id, track_id)
                )
                """
            )

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS autodj_rotation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position INTEGER NOT NULL,
                    category_id INTEGER NOT NULL,
                    norules INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )


            c.execute(
                """
                CREATE TABLE IF NOT EXISTS autodj_settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    no_repeat_artist_minutes INTEGER NOT NULL DEFAULT 0,
                    no_repeat_title_minutes INTEGER NOT NULL DEFAULT 0,
                    no_repeat_track_minutes INTEGER NOT NULL DEFAULT 0,
                    keep_queue INTEGER NOT NULL DEFAULT 0,
                    editor_text TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    rotation_next_index INTEGER NOT NULL DEFAULT 0,
                    rotation_signature TEXT NOT NULL DEFAULT ''
                )
                """
            )



            # Ensure a single row exists
            try:
                c.execute("SELECT COUNT(*) AS cnt FROM autodj_settings")
                row = c.fetchone()
                if not row or row["cnt"] == 0:
                    c.execute(
                        "INSERT INTO autodj_settings (no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, keep_queue, created_at) VALUES (?, ?, ?, ?, ?)",
                        (60, 60, 60, 3, datetime.now().isoformat(timespec="seconds")),
                    )
            except Exception:
                pass

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    clean_transition INTEGER NOT NULL DEFAULT 0,
                    script_clean_transition INTEGER NOT NULL DEFAULT 0,
                    autodj_rotation_index INTEGER,
                    autodj_rotation_category_id INTEGER,
                    autodj_rotation_norules INTEGER NOT NULL DEFAULT 0,
                    autodj_rotation_sig TEXT NOT NULL DEFAULT ''
                )
                """
            )


            c.execute(
                """
                CREATE TABLE IF NOT EXISTS play_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    track_id INTEGER NOT NULL,
                    played_at TEXT NOT NULL
                )
                """
            )

            _ensure_runtime_playback_state_schema(conn)

            # Per-encoder runtime timestamps for real stream uptime display.
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS encoder_runtime_state (
                    stream_id INTEGER PRIMARY KEY,
                    started_at TEXT
                )
                """
            )

            # Scheduler rules (UI groundwork; execution engine to be added later)
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduler_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    is_enabled INTEGER NOT NULL DEFAULT 0,
                    auto_start INTEGER NOT NULL DEFAULT 0,
                    name TEXT NOT NULL,
                    run_when TEXT NOT NULL,
                    insert_kind TEXT NOT NULL,
                    insert_value TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    last_run_at TEXT,
                    next_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.commit()
            init_ok = True
        finally:
            try:
                if init_ok:
                    _initialized_station_dbs.add(normalized_db_path)
            finally:
                conn.close()

def is_first_run():
    init_global_db()
    conn = get_global_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS cnt FROM users")
    user_count = int(c.fetchone()["cnt"] or 0)
    conn.close()
    return user_count == 0



def get_settings():
    try:
        conn = get_db()
    except NoActiveStationError:
        return None
    c = conn.cursor()
    try:
        c.execute("SELECT * FROM settings ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
    except Exception:
        row = None
    conn.close()
    return row


def _parse_checkbox_value(value) -> int:
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "on"} else 0
    return 1 if bool(value) else 0


def get_dsp_enabled(default: bool = True) -> bool:
    settings = get_settings()
    if not settings:
        return bool(default)

    try:
        if isinstance(settings, dict):
            if "dsp_enabled" in settings:
                raw = settings.get("dsp_enabled")
            else:
                return bool(default)
        elif "dsp_enabled" in settings.keys():
            raw = settings["dsp_enabled"]
        else:
            return bool(default)

        if raw is None:
            return bool(default)
        if isinstance(raw, str):
            raw_s = raw.strip().lower()
            if raw_s in {"1", "true", "yes", "on"}:
                return True
            if raw_s in {"0", "false", "no", "off", ""}:
                return False
        return bool(int(raw))
    except Exception:
        return bool(default)


_AUDIO_ENGINE_STATUS_CACHE_LOCK = threading.Lock()
_AUDIO_ENGINE_STATUS_CACHE = {"ts": 0.0, "station_key": "", "data": None}
_AUDIO_ENGINE_STATUS_CACHE_TTL = 1.5


def invalidate_audio_engine_status_cache() -> None:
    """Clear the short native audio-engine status cache after start/stop actions."""
    try:
        with _AUDIO_ENGINE_STATUS_CACHE_LOCK:
            _AUDIO_ENGINE_STATUS_CACHE["ts"] = 0.0
            _AUDIO_ENGINE_STATUS_CACHE["station_key"] = ""
            _AUDIO_ENGINE_STATUS_CACHE["data"] = None
    except Exception:
        pass


@app.route("/api/studio/layout-state", methods=["GET"])
@login_required
def api_studio_layout_state_get():
    conn = get_db()
    try:
        state = get_studio_layout_state_for_user(conn, int(session.get("user_id")))
        return jsonify({"ok": True, **state})
    finally:
        conn.close()


@app.route("/api/studio/layout-state", methods=["POST"])
@login_required
def api_studio_layout_state_save():
    payload = request.get_json(silent=True) or {}
    layout_name = str(payload.get("layout") or "layout-1").strip()
    panels = payload.get("panels") or {}
    preferred_layout = payload.get("preferred_layout")
    if not isinstance(panels, dict):
        return jsonify({"ok": False, "error": "invalid_panels"}), 400
    conn = get_db()
    try:
        save_studio_layout_state_for_user(
            conn,
            int(session.get("user_id")),
            layout_name,
            panels,
            preferred_layout=preferred_layout,
        )
        state = get_studio_layout_state_for_user(conn, int(session.get("user_id")))
        return jsonify({"ok": True, **state})
    finally:
        conn.close()

@app.route("/api/studio/layout-template/save", methods=["POST"])
@login_required
def api_studio_layout_template_save():
    payload = request.get_json(silent=True) or {}
    layout_name = _normalize_studio_layout_name(payload.get("layout"))
    panels = payload.get("panels") or {}
    if not isinstance(panels, dict):
        return jsonify({"ok": False, "error": "invalid_panels"}), 400
    try:
        path = save_studio_layout_template_file(layout_name, panels)
        return jsonify({"ok": True, "layout": layout_name, "path": path})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/studio/layout-template/load", methods=["POST"])
@login_required
def api_studio_layout_template_load():
    payload = request.get_json(silent=True) or {}
    layout_name = _normalize_studio_layout_name(payload.get("layout"))
    preferred_layout = _normalize_studio_layout_name(payload.get("preferred_layout") or layout_name)
    conn = get_db()
    try:
        panels = load_studio_layout_template_file(layout_name)
        save_studio_layout_state_for_user(
            conn,
            int(session.get("user_id")),
            layout_name,
            panels,
            preferred_layout=preferred_layout,
        )
        state = get_studio_layout_state_for_user(conn, int(session.get("user_id")))
        return jsonify({"ok": True, "layout": layout_name, "panels": panels, **state})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "template_not_found"}), 404
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/setup", methods=["GET", "POST"])
@limiter.limit("3 per 5 minutes", methods=["POST"])
def setup():
    init_global_db()

    if not is_first_run():
        return redirect(url_for("broadcaster"))

    if request.method == "POST":
        if _honeypot_triggered(request.form):
            # Pretend it just failed normally.
            flash("Username and both password fields are required.", "error")
            return render_template("setup.html", hide_navbar=True)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        password2 = request.form.get("password2", "").strip()

        if not username or not password or not password2:
            flash("Username and both password fields are required.", "error")
            return render_template("setup.html", hide_navbar=True)

        if password != password2:
            flash("Passwords do not match.", "error")
            return render_template("setup.html", hide_navbar=True)

        conn = get_global_db()
        c = conn.cursor()

        try:
            c.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), datetime.now().isoformat()),
            )
        except sqlite3.IntegrityError:
            flash("That username already exists.", "error")
            return render_template("setup.html", hide_navbar=True)

        new_user_id = c.lastrowid

        conn.commit()
        conn.close()

        session["user_id"] = new_user_id
        session["username"] = username
        return redirect(url_for("dashboard"))

    return render_template("setup.html", hide_navbar=True)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per 5 minutes", methods=["POST"])
def login():
    init_global_db()

    if is_first_run():
        return redirect(url_for("setup"))

    if request.method == "POST":
        if _honeypot_triggered(request.form):
            flash("Invalid username or password.", "error")
            return render_template("login.html", hide_navbar=True)

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_global_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = c.fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        else:
            flash("Invalid username or password.", "error")

    return render_template("login.html", hide_navbar=True)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users/add", methods=["POST"])
@login_required
@limiter.limit("3 per 5 minutes")
def add_user():
    """Add a new login user (same permissions as everyone)."""
    init_global_db()

    # Accept JSON (preferred from modal) or form POST.
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        payload = {}

    username = (payload.get("username") or request.form.get("username") or "").strip()
    password = (payload.get("password") or request.form.get("password") or "").strip()
    password2 = (payload.get("password2") or request.form.get("password2") or "").strip()

    if _honeypot_triggered(payload) or _honeypot_triggered(request.form):
        return jsonify({"ok": False, "error": "invalid"}), 400

    if not username or not password or not password2:
        return jsonify({"ok": False, "error": "missing_fields"}), 400
    if password != password2:
        return jsonify({"ok": False, "error": "password_mismatch"}), 400

    conn = get_global_db()
    c = conn.cursor()

    try:
        c.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), datetime.now().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return jsonify({"ok": False, "error": "username_exists"}), 409
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        conn.close()
        return jsonify({"ok": False, "error": "server_error"}), 500

    conn.close()
    return jsonify({"ok": True})


@app.route("/api/users/list", methods=["GET"])
@login_required
def api_users_list():
    init_global_db()
    conn = get_global_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, username, created_at FROM users ORDER BY LOWER(username) ASC, id ASC")
        users = []
        for row in c.fetchall() or []:
            try:
                created_at = row["created_at"] if "created_at" in row.keys() else None
            except Exception:
                created_at = None
            users.append({
                "id": row["id"],
                "username": row["username"],
                "created_at": created_at,
            })
        return jsonify({"ok": True, "users": users, "current_user_id": session.get("user_id")})
    finally:
        conn.close()


@app.route("/api/users/delete", methods=["POST"])
@login_required
def api_users_delete():
    init_global_db()
    payload = request.get_json(silent=True) or {}
    try:
        user_id = int(payload.get("user_id") or 0)
    except Exception:
        user_id = 0
    if user_id <= 0:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400
    if int(session.get("user_id") or 0) == user_id:
        return jsonify({"ok": False, "error": "cannot_delete_current_user"}), 400

    conn = get_global_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        conn.close()


@app.route("/api/users/change-password", methods=["POST"])
@login_required
def api_users_change_password():
    init_global_db()
    payload = request.get_json(silent=True) or {}
    try:
        user_id = int(payload.get("user_id") or 0)
    except Exception:
        user_id = 0
    current_password = str(payload.get("current_password") or "").strip()
    password = str(payload.get("password") or "").strip()
    password2 = str(payload.get("password2") or "").strip()
    if user_id <= 0:
        return jsonify({"ok": False, "error": "missing_user_id"}), 400
    if not current_password or not password or not password2:
        return jsonify({"ok": False, "error": "missing_fields"}), 400
    if password != password2:
        return jsonify({"ok": False, "error": "password_mismatch"}), 400

    conn = get_global_db()
    try:
        c = conn.cursor()
        c.execute("SELECT id, password_hash FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        if row is None:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        stored_hash = row["password_hash"] if hasattr(row, 'keys') else row[1]
        if not stored_hash or not check_password_hash(stored_hash, current_password):
            return jsonify({"ok": False, "error": "invalid_current_password"}), 403
        c.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password), user_id),
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"ok": False, "error": "server_error"}), 500
    finally:
        conn.close()



@app.route("/stations/select", methods=["POST"])
def select_station():
    """Select a station (DB file) for this session."""
    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    station_id = (payload.get("station_id") or "").strip()
    station_id = _resolve_station_id_to_db(station_id)
    if not station_id or not station_id.endswith(".db"):
        return jsonify({"ok": False, "error": "Invalid station"}), 400

    db_path = resolve_station_db_path(station_id)
    if not os.path.exists(db_path):
        return jsonify({"ok": False, "error": "Station not found"}), 404

    # Switching stations should not require re-authentication.
    session["active_station_db"] = station_id
    try:
        _app_state_set("last_station_db", station_id)
        _app_state_set("active_station_db", station_id)
    except Exception:
        pass

    # Ensure station schema exists for the selected DB.
    try:
        init_db()
    except Exception:
        pass
    return jsonify({"ok": True, "redirect": url_for("broadcaster")})


def _create_station_with_settings(*, radio_name: str, gap_killer_start_dbfs: float = -20.0, gap_killer_end_dbfs: float = -24.0, crossfade_trigger_relative_db: float = -7.0, crossfade_fallback_seconds: float = 3.0, crossfade_min_seconds: float = 0.1, crossfade_max_seconds: float = 6.0, crossfade_fade_out_seconds: float = 5.0, no_crossfade_max_duration_sec: float = 65.0, music_library_path: str, soundsolution_path: str | None, dsp_enabled: int = 1):
    gap_killer_start_dbfs = float(gap_killer_start_dbfs)
    gap_killer_end_dbfs = float(gap_killer_end_dbfs)
    crossfade_trigger_relative_db = float(crossfade_trigger_relative_db)
    crossfade_fallback_seconds = float(crossfade_fallback_seconds)
    crossfade_min_seconds = float(crossfade_min_seconds)
    crossfade_max_seconds = float(crossfade_max_seconds)
    crossfade_fade_out_seconds = float(crossfade_fade_out_seconds)
    no_crossfade_max_duration_sec = float(no_crossfade_max_duration_sec)
    if not (-120.0 <= gap_killer_start_dbfs <= 0.0 and -120.0 <= gap_killer_end_dbfs <= 0.0):
        raise ValueError("Gap Killer levels must be between -120 and 0 dBFS.")
    if not (-120.0 <= crossfade_trigger_relative_db <= 0.0):
        raise ValueError("Crossfade trigger must be between -120 and 0 dB.")
    if min(crossfade_fallback_seconds, crossfade_min_seconds, crossfade_max_seconds, crossfade_fade_out_seconds, no_crossfade_max_duration_sec) < 0.0:
        raise ValueError("Crossfade time values must be 0 or greater.")
    if crossfade_max_seconds < crossfade_min_seconds:
        raise ValueError("Maximum crossfade window must not be shorter than the minimum window.")

    existing = build_station_list()
    if len(existing) >= 10:
        raise ValueError("Maximum number of stations reached (10).")

    init_global_db()
    station_name = (radio_name or "").strip() or "New Station"
    base_name = _generate_station_db_filename(station_name)
    db_path = resolve_station_db_path(base_name)
    if db_path and os.path.exists(db_path):
        raise ValueError("A station with this Radio name already exists. Please choose a different name.")

    requested_name_cf = station_name.casefold()
    for st in (existing or []):
        existing_name = str((st.get("name") if isinstance(st, dict) else "") or "").strip()
        existing_db = str((st.get("db_filename") if isinstance(st, dict) else "") or "").strip()
        if existing_name.casefold() == requested_name_cf or existing_db == base_name:
            raise ValueError("A station with this Radio name already exists. Please choose a different name.")

    conn = sqlite3.connect(db_path, timeout=_DB_CONNECT_TIMEOUT_SECONDS)
    configure_sqlite_connection(conn)
    conn.close()

    session["active_station_db"] = os.path.basename(db_path)
    _app_state_set("last_station_db", session["active_station_db"])
    _app_state_set("active_station_db", session["active_station_db"])
    init_db()

    now_iso = datetime.now().isoformat()
    gconn = get_global_db()
    gconn.execute(
        "INSERT OR IGNORE INTO stations (name, db_filename, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (station_name, session["active_station_db"], now_iso, now_iso),
    )
    gconn.commit()
    gconn.close()

    sconn = get_db()
    if session.get("user_id"):
        try:
            initialize_studio_layout_defaults_for_user(sconn, int(session.get("user_id")))
        except Exception:
            pass
    c = sconn.cursor()
    c.execute("SELECT id FROM settings ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    settings_id = None
    if row is not None:
        try:
            settings_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        except Exception:
            settings_id = None

    values = (
        station_name,
        -34.0,
        -38.0,
        -7.0,
        float(_AB_FIXED_OVERLAP_SECONDS),
        3.0,
        gap_killer_start_dbfs,
        gap_killer_end_dbfs,
        crossfade_trigger_relative_db,
        crossfade_fallback_seconds,
        crossfade_min_seconds,
        crossfade_max_seconds,
        crossfade_fade_out_seconds,
        no_crossfade_max_duration_sec,
        music_library_path,
        now_iso,
        now_iso,
        soundsolution_path,
        int(dsp_enabled),
    )
    if settings_id is None:
        c.execute(
            """
            INSERT INTO settings (
                radio_name, cue_in_threshold, cue_out_threshold,
                cross_threshold, overlap_seconds, cue_start_offset_seconds,
                gap_killer_start_dbfs, gap_killer_end_dbfs,
                crossfade_trigger_relative_db, crossfade_fallback_seconds,
                crossfade_min_seconds, crossfade_max_seconds,
                crossfade_fade_out_seconds, no_crossfade_max_duration_sec,
                music_library_path, created_at, updated_at,
                ssproc_appimage, dsp_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    else:
        c.execute(
            """
            UPDATE settings SET
                radio_name = ?, cue_in_threshold = ?, cue_out_threshold = ?,
                cross_threshold = ?, overlap_seconds = ?, cue_start_offset_seconds = ?,
                gap_killer_start_dbfs = ?, gap_killer_end_dbfs = ?,
                crossfade_trigger_relative_db = ?, crossfade_fallback_seconds = ?,
                crossfade_min_seconds = ?, crossfade_max_seconds = ?,
                crossfade_fade_out_seconds = ?, no_crossfade_max_duration_sec = ?,
                music_library_path = ?, created_at = COALESCE(created_at, ?), updated_at = ?,
                ssproc_appimage = ?, dsp_enabled = ?
            WHERE id = ?
            """,
            values + (settings_id,),
        )
    sconn.commit()
    sconn.close()

    try:
        gconn = get_global_db()
        gconn.execute(
            "UPDATE stations SET name = ?, updated_at = ? WHERE db_filename = ?",
            (station_name, now_iso, session["active_station_db"]),
        )
        gconn.commit()
        gconn.close()
    except Exception:
        pass
    return {"name": station_name, "db_filename": session["active_station_db"]}


def _perform_station_rename(source_db_filename: str, new_name: str):
    source_db_filename = str(source_db_filename or "").strip()
    new_name = str(new_name or "").strip()
    if not source_db_filename or not source_db_filename.endswith('.db'):
        raise ValueError("Station not found.")
    if not new_name:
        raise ValueError("Please enter a new station name.")

    source_db_path = resolve_station_db_path(source_db_filename)
    if not source_db_path or not os.path.exists(source_db_path):
        raise ValueError("Station database not found.")

    source_station_name = source_db_filename.replace('.db', '')
    try:
        gconn = get_global_db()
        gconn.row_factory = sqlite3.Row
        cur = gconn.cursor()
        cur.execute("SELECT name FROM stations WHERE db_filename = ?", (source_db_filename,))
        row = cur.fetchone()
        if row is not None:
            source_station_name = str(row["name"] or source_station_name)
        cur.execute("SELECT 1 FROM stations WHERE LOWER(name) = LOWER(?) AND db_filename <> ? LIMIT 1", (new_name, source_db_filename))
        if cur.fetchone() is not None:
            gconn.close()
            raise ValueError("Another station already uses this name.")
        gconn.close()
    except ValueError:
        raise
    except Exception:
        pass

    if is_station_running(source_db_filename):
        raise ValueError("Stop the station before renaming it.")

    new_db_filename, new_db_path = _clone_station_database_for_rename(source_db_filename, new_name)
    now_iso = datetime.now().isoformat()

    gconn = get_global_db()
    try:
        cur = gconn.cursor()
        cur.execute(
            "INSERT INTO stations (name, db_filename, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (new_name, new_db_filename, now_iso, now_iso),
        )
        cur.execute("DELETE FROM stations WHERE db_filename = ?", (source_db_filename,))
        # A station can only be renamed while stopped, so no uptime row should
        # survive under either the old or newly allocated database key.
        cur.execute(
            "DELETE FROM audio_engine_state WHERE station_key IN (?, ?)",
            (source_db_filename, new_db_filename),
        )
        gconn.commit()
    except Exception:
        try:
            gconn.rollback()
        except Exception:
            pass
        raise
    finally:
        gconn.close()


    try:
        _checkpoint_sqlite_database(source_db_path)
        if os.path.exists(source_db_path):
            os.remove(source_db_path)
        _remove_sqlite_sidecars(source_db_path)
    except Exception as exc:
        try:
            if os.path.exists(new_db_path):
                os.remove(new_db_path)
            _remove_sqlite_sidecars(new_db_path)
        except Exception:
            pass
        raise RuntimeError(f'Failed to remove old station database: {exc}')

    session["active_station_db"] = new_db_filename
    _app_state_set("active_station_db", new_db_filename)
    _app_state_set("last_station_db", new_db_filename)
    try:
        init_db()
    except Exception:
        pass

    return {
        "name": new_name,
        "db_filename": new_db_filename,
    }


@app.route("/api/studio/stations/rename", methods=["POST"])
@login_required
def studio_rename_station():
    payload = request.get_json(silent=True) or request.form
    station_id = str(payload.get("station_id", "") or "").strip()
    new_name = str(payload.get("new_name", "") or "").strip()
    if not station_id:
        return jsonify({"ok": False, "error": "Missing station."}), 400
    if not new_name:
        return jsonify({"ok": False, "error": "Please enter a new station name."}), 400

    source_db_filename = _resolve_station_id_to_db(station_id)
    if not source_db_filename or not source_db_filename.endswith('.db'):
        return jsonify({"ok": False, "error": "Station not found."}), 404

    previous_active_station_db = session.get("active_station_db")
    try:
        station = _perform_station_rename(source_db_filename, new_name)
        return jsonify({
            "ok": True,
            "station": station,
            "stopped_before_rename": False,
            "redirect": url_for("broadcaster"),
        })
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to rename station: {exc}"}), 500
    finally:
        current_active = session.get("active_station_db")
        if not current_active and previous_active_station_db:
            session["active_station_db"] = previous_active_station_db
            try:
                _app_state_set("active_station_db", previous_active_station_db)
                _app_state_set("last_station_db", previous_active_station_db)
            except Exception:
                pass

@app.route("/api/studio/stations/create", methods=["POST"])
@login_required
def studio_create_station():
    payload = request.get_json(silent=True) or request.form
    radio_name = str(payload.get("radio_name", "") or "").strip()
    music_library_path = str(payload.get("music_library_path", "") or "").strip()
    soundsolution_path = str(payload.get("soundsolution_path", "") or "").strip() or None
    dsp_enabled = _parse_checkbox_value(payload.get("soundsolution_enabled"))
    previous_active_station_db = session.get("active_station_db")

    try:
        gap_killer_start_dbfs = float(str(payload.get("gap_killer_start_dbfs", "-20.0") or "-20.0").strip())
        gap_killer_end_dbfs = float(str(payload.get("gap_killer_end_dbfs", "-24.0") or "-24.0").strip())
        crossfade_trigger_relative_db = float(str(payload.get("crossfade_trigger_relative_db", "-7.0") or "-7.0").strip())
        crossfade_fallback_seconds = float(str(payload.get("crossfade_fallback_seconds", "3.0") or "3.0").strip())
        crossfade_min_seconds = float(str(payload.get("crossfade_min_seconds", "0.1") or "0.1").strip())
        crossfade_max_seconds = float(str(payload.get("crossfade_max_seconds", "6.0") or "6.0").strip())
        crossfade_fade_out_seconds = float(str(payload.get("crossfade_fade_out_seconds", "5.0") or "5.0").strip())
        no_crossfade_max_duration_sec = float(str(payload.get("no_crossfade_max_duration_sec", "65.0") or "65.0").strip())
    except Exception:
        return jsonify({"ok": False, "error": "Crossfade values must be numeric."}), 400

    try:
        station = _create_station_with_settings(
            radio_name=radio_name,
            gap_killer_start_dbfs=gap_killer_start_dbfs,
            gap_killer_end_dbfs=gap_killer_end_dbfs,
            crossfade_trigger_relative_db=crossfade_trigger_relative_db,
            crossfade_fallback_seconds=crossfade_fallback_seconds,
            crossfade_min_seconds=crossfade_min_seconds,
            crossfade_max_seconds=crossfade_max_seconds,
            crossfade_fade_out_seconds=crossfade_fade_out_seconds,
            no_crossfade_max_duration_sec=no_crossfade_max_duration_sec,
            music_library_path=music_library_path,
            soundsolution_path=soundsolution_path,
            dsp_enabled=dsp_enabled,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to create station: {exc}"}), 500
    finally:
        if previous_active_station_db:
            session["active_station_db"] = previous_active_station_db
            _app_state_set("last_station_db", previous_active_station_db)
            _app_state_set("active_station_db", previous_active_station_db)

    return jsonify({"ok": True, "station": station, "redirect": url_for("broadcaster")})


@app.route("/broadcaster")
@login_required
def broadcaster():
    try:
        conn = get_db()
    except NoActiveStationError:
        return render_template("no_stations.html", hide_navbar=True, new_station_defaults=build_new_station_defaults())
    except Exception:
        conn = None

    settings_row = None
    default_soundsolution_path = get_default_soundsolution_path()
    try:
        if conn is not None:
            row = conn.execute("SELECT * FROM settings ORDER BY id DESC LIMIT 1").fetchone()
            if row is None:
                settings_row = dict(build_new_station_defaults())
                settings_row["ssproc_appimage"] = default_soundsolution_path
            else:
                settings_row = dict(row)
                configured_dsp = str(settings_row.get("ssproc_appimage") or "").strip()
                settings_row["ssproc_appimage"] = _normalize_soundsolution_config_setting(
                    configured_dsp or default_soundsolution_path
                )
                settings_row["dsp_enabled"] = int(settings_row.get("dsp_enabled", 1) or 0)
                for key, default in (
                    ("gap_killer_start_dbfs", -20.0),
                    ("gap_killer_end_dbfs", -24.0),
                    ("crossfade_trigger_relative_db", -7.0),
                    ("crossfade_fallback_seconds", 3.0),
                    ("crossfade_min_seconds", 0.1),
                    ("crossfade_max_seconds", 6.0),
                    ("crossfade_fade_out_seconds", 5.0),
                    ("no_crossfade_max_duration_sec", 65.0),
                ):
                    settings_row[key] = float(settings_row.get(key) if settings_row.get(key) is not None else default)
    except Exception:
        settings_row = None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass

    return render_template(
        "broadcaster.html",
        hide_navbar=True,
        settings=settings_row,
        new_station_defaults=build_new_station_defaults(),
    )


@app.route("/api/studio/settings/dsp", methods=["POST"])
@login_required
def api_studio_settings_dsp():
    """Persist and apply the station-wide DSP checkbox immediately.

    This endpoint intentionally accepts only the DSP flag. Every other General or
    Crossfade setting remains part of the normal Save settings workflow.
    """
    current_db_path = get_active_station_db_path()
    if not current_db_path:
        return jsonify({"ok": False, "error": "No station selected."}), 400

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or "enabled" not in payload:
        return jsonify({"ok": False, "error": "DSP enabled state is required."}), 400

    requested_dsp_enabled = _parse_checkbox_value(payload.get("enabled"))
    station_key = str(get_active_station_key() or "").strip()
    conn = get_db()
    c = conn.cursor()
    current = c.execute("SELECT * FROM settings ORDER BY id DESC LIMIT 1").fetchone()
    if current is None:
        conn.close()
        return jsonify({"ok": False, "error": "Station settings are missing."}), 409

    settings_id = current["id"] if "id" in current.keys() else None
    current_dsp_enabled = int(current["dsp_enabled"] if "dsp_enabled" in current.keys() else 1)
    station_on_air = bool(station_key and is_station_on_air(station_key))
    dsp_changed = requested_dsp_enabled != current_dsp_enabled

    live_result = {
        "station_running": station_on_air,
        "live_applied": False,
        "reconfigured_stream_id": None,
    }
    if not dsp_changed:
        conn.close()
        return jsonify({
            "ok": True,
            "message": "DSP is already enabled." if requested_dsp_enabled else "DSP is already disabled.",
            "dsp_enabled": bool(requested_dsp_enabled),
            "dsp": live_result,
        })

    now_iso = datetime.now().isoformat()
    c.execute(
        "UPDATE settings SET dsp_enabled = ?, updated_at = ? WHERE id = ?",
        (requested_dsp_enabled, now_iso, settings_id),
    )
    conn.commit()

    if station_on_air:
        try:
            live_result = _apply_live_dsp_setting(station_key)
        except Exception as exc:
            try:
                c.execute(
                    "UPDATE settings SET dsp_enabled = ?, updated_at = ? WHERE id = ?",
                    (current_dsp_enabled, datetime.now().isoformat(), settings_id),
                )
                conn.commit()
                _apply_live_dsp_setting(station_key)
            except Exception:
                pass
            conn.close()
            return jsonify({
                "ok": False,
                "error": f"DSP setting could not be applied live: {exc}",
                "dsp_enabled": bool(current_dsp_enabled),
            }), 500

        _publish_ui_encoders_changed(
            station_key,
            "dsp_live_reconfigured",
            live_result.get("reconfigured_stream_id"),
        )

    conn.close()
    state_label = "enabled" if requested_dsp_enabled else "disabled"
    return jsonify({
        "ok": True,
        "message": (
            f"DSP {state_label} immediately while the station remained ON AIR."
            if station_on_air
            else f"DSP {state_label}."
        ),
        "dsp_enabled": bool(requested_dsp_enabled),
        "dsp": live_result,
    })


@app.route("/api/studio/settings", methods=["POST"])
@login_required
def api_studio_settings():
    current_db_path = get_active_station_db_path()
    if not current_db_path:
        return jsonify({"ok": False, "error": "No station selected."}), 400

    current_db_filename = os.path.basename(current_db_path)
    conn = get_db()
    c = conn.cursor()
    current = c.execute("SELECT * FROM settings ORDER BY id DESC LIMIT 1").fetchone()

    def _current_value(name, default=None):
        if current is None:
            return default
        try:
            if name in current.keys():
                value = current[name]
                return default if value is None else value
        except Exception:
            pass
        return default

    def _form_or_current_text(name, current_name=None, default=""):
        current_name = current_name or name
        if name in request.form:
            return str(request.form.get(name, "") or "").strip()
        return str(_current_value(current_name, default) or "").strip()

    def _form_or_current_float(name, current_name=None, default=0.0):
        current_name = current_name or name
        raw = request.form.get(name, default) if name in request.form else _current_value(current_name, default)
        return float(str(raw if raw is not None else default).strip() or default)

    radio_name = _form_or_current_text("radio_name", default="")
    try:
        gap_killer_start_dbfs = _form_or_current_float("gap_killer_start_dbfs", default=-20.0)
        gap_killer_end_dbfs = _form_or_current_float("gap_killer_end_dbfs", default=-24.0)
        crossfade_trigger_relative_db = _form_or_current_float("crossfade_trigger_relative_db", default=-7.0)
        crossfade_fallback_seconds = _form_or_current_float("crossfade_fallback_seconds", default=3.0)
        crossfade_min_seconds = _form_or_current_float("crossfade_min_seconds", default=0.1)
        crossfade_max_seconds = _form_or_current_float("crossfade_max_seconds", default=6.0)
        crossfade_fade_out_seconds = _form_or_current_float("crossfade_fade_out_seconds", default=5.0)
        no_crossfade_max_duration_sec = _form_or_current_float("no_crossfade_max_duration_sec", default=65.0)
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"ok": False, "error": "Crossfade values must be numeric."}), 400

    current_radio_name = str(_current_value("radio_name", "") or "").strip()
    current_music_library_path = str(_current_value("music_library_path", "") or "").strip()
    current_soundsolution_path = str(_current_value("ssproc_appimage", "") or "").strip()
    effective_current_soundsolution_path = _normalize_soundsolution_config_setting(current_soundsolution_path)
    current_dsp_enabled = int(_current_value("dsp_enabled", 1) or 0)
    current_float_values = {
        "gap_killer_start_dbfs": float(_current_value("gap_killer_start_dbfs", -20.0)),
        "gap_killer_end_dbfs": float(_current_value("gap_killer_end_dbfs", -24.0)),
        "crossfade_trigger_relative_db": float(_current_value("crossfade_trigger_relative_db", -7.0)),
        "crossfade_fallback_seconds": float(_current_value("crossfade_fallback_seconds", 3.0)),
        "crossfade_min_seconds": float(_current_value("crossfade_min_seconds", 0.1)),
        "crossfade_max_seconds": float(_current_value("crossfade_max_seconds", 6.0)),
        "crossfade_fade_out_seconds": float(_current_value("crossfade_fade_out_seconds", 5.0)),
        "no_crossfade_max_duration_sec": float(_current_value("no_crossfade_max_duration_sec", 65.0)),
    }

    music_library_path = _form_or_current_text("music_library_path", default="")
    soundsolution_path = _form_or_current_text(
        "soundsolution_path",
        current_name="ssproc_appimage",
        default=effective_current_soundsolution_path,
    ) or None
    if "soundsolution_enabled" in request.form or any(name in request.form for name in ("radio_name", "music_library_path", "soundsolution_path")):
        dsp_enabled = _parse_checkbox_value(request.form.get("soundsolution_enabled"))
    else:
        dsp_enabled = current_dsp_enabled

    if not (-120.0 <= gap_killer_start_dbfs <= 0.0 and -120.0 <= gap_killer_end_dbfs <= 0.0):
        conn.close()
        return jsonify({"ok": False, "error": "Gap Killer levels must be between -120 and 0 dBFS."}), 400
    if not (-120.0 <= crossfade_trigger_relative_db <= 0.0):
        conn.close()
        return jsonify({"ok": False, "error": "Crossfade trigger must be between -120 and 0 dB."}), 400
    if min(crossfade_fallback_seconds, crossfade_min_seconds, crossfade_max_seconds, crossfade_fade_out_seconds, no_crossfade_max_duration_sec) < 0.0:
        conn.close()
        return jsonify({"ok": False, "error": "Crossfade time values must be 0 or greater."}), 400
    if crossfade_max_seconds < crossfade_min_seconds:
        conn.close()
        return jsonify({"ok": False, "error": "Maximum crossfade window must not be shorter than the minimum window."}), 400

    station_key = str(get_active_station_key() or "").strip()
    station_on_air = bool(station_key and is_station_on_air(station_key))
    dsp_changed = int(dsp_enabled) != int(current_dsp_enabled)
    if station_on_air:
        requested_float_values = {
            "gap_killer_start_dbfs": gap_killer_start_dbfs,
            "gap_killer_end_dbfs": gap_killer_end_dbfs,
            "crossfade_trigger_relative_db": crossfade_trigger_relative_db,
            "crossfade_fallback_seconds": crossfade_fallback_seconds,
            "crossfade_min_seconds": crossfade_min_seconds,
            "crossfade_max_seconds": crossfade_max_seconds,
            "crossfade_fade_out_seconds": crossfade_fade_out_seconds,
            "no_crossfade_max_duration_sec": no_crossfade_max_duration_sec,
        }
        non_live_changes = []
        if str(radio_name or "").strip() != current_radio_name:
            non_live_changes.append("radio_name")
        if str(music_library_path or "").strip() != current_music_library_path:
            non_live_changes.append("music_library_path")
        if str(soundsolution_path or "").strip() != effective_current_soundsolution_path:
            non_live_changes.append("soundsolution_path")
        for name, value in requested_float_values.items():
            if not math.isclose(float(value), float(current_float_values[name]), rel_tol=0.0, abs_tol=1e-9):
                non_live_changes.append(name)
        if non_live_changes:
            conn.close()
            return jsonify({
                "ok": False,
                "error": "While the station is ON AIR, only the Enable DSP checkbox can be changed.",
            }), 400

    requested_radio_name = str(radio_name or "").strip()
    original_radio_name = ""
    try:
        gconn = get_global_db()
        grow = gconn.execute("SELECT name FROM stations WHERE db_filename = ? LIMIT 1", (current_db_filename,)).fetchone()
        if grow is not None:
            original_radio_name = str(grow["name"] or "").strip()
        gconn.close()
    except Exception:
        pass
    if not original_radio_name:
        original_radio_name = os.path.splitext(os.path.basename(current_db_filename))[0].replace("db-", "").strip()

    existing_id = None
    if current is not None:
        try:
            existing_id = current["id"] if "id" in current.keys() else None
        except Exception:
            existing_id = None
    now_iso = datetime.now().isoformat()
    values = (
        radio_name if radio_name else None,
        gap_killer_start_dbfs,
        gap_killer_end_dbfs,
        crossfade_trigger_relative_db,
        crossfade_fallback_seconds,
        crossfade_min_seconds,
        crossfade_max_seconds,
        crossfade_fade_out_seconds,
        no_crossfade_max_duration_sec,
        float(_AB_FIXED_OVERLAP_SECONDS),
        music_library_path,
        soundsolution_path,
        dsp_enabled,
        now_iso,
    )
    saved_settings_id = existing_id
    if existing_id is None:
        c.execute(
            """
            INSERT INTO settings (
                radio_name, gap_killer_start_dbfs, gap_killer_end_dbfs,
                crossfade_trigger_relative_db, crossfade_fallback_seconds,
                crossfade_min_seconds, crossfade_max_seconds, crossfade_fade_out_seconds,
                no_crossfade_max_duration_sec, overlap_seconds, music_library_path,
                ssproc_appimage, dsp_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values[:-1] + (now_iso, now_iso),
        )
        saved_settings_id = c.lastrowid
    else:
        c.execute(
            """
            UPDATE settings SET
                radio_name = ?, gap_killer_start_dbfs = ?, gap_killer_end_dbfs = ?,
                crossfade_trigger_relative_db = ?, crossfade_fallback_seconds = ?,
                crossfade_min_seconds = ?, crossfade_max_seconds = ?, crossfade_fade_out_seconds = ?,
                no_crossfade_max_duration_sec = ?, overlap_seconds = ?, music_library_path = ?,
                ssproc_appimage = ?, dsp_enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            values + (existing_id,),
        )
    conn.commit()

    dsp_live_result = {
        "station_running": bool(station_on_air),
        "live_applied": False,
        "reconfigured_stream_id": None,
    }
    if station_on_air and dsp_changed:
        try:
            dsp_live_result = _apply_live_dsp_setting(station_key)
        except Exception as exc:
            try:
                if saved_settings_id is not None:
                    c.execute(
                        "UPDATE settings SET dsp_enabled = ?, updated_at = ? WHERE id = ?",
                        (int(current_dsp_enabled), datetime.now().isoformat(), saved_settings_id),
                    )
                    conn.commit()
                _apply_live_dsp_setting(station_key)
            except Exception:
                pass
            conn.close()
            return jsonify({
                "ok": False,
                "error": f"DSP setting could not be applied live: {exc}",
            }), 500

        _publish_ui_encoders_changed(
            station_key,
            "dsp_live_reconfigured",
            dsp_live_result.get("reconfigured_stream_id"),
        )

    if (not station_on_air) and requested_radio_name and requested_radio_name != original_radio_name:
        try:
            conn.close()
        except Exception:
            pass
        try:
            _perform_station_rename(current_db_filename, requested_radio_name)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Settings saved, but station rename failed: {exc}"}), 500
        current_db_path = get_active_station_db_path()
        current_db_filename = os.path.basename(current_db_path) if current_db_path else current_db_filename
    else:
        conn.close()

    try:
        init_global_db()
        gconn = get_global_db()
        active_fn = os.path.basename(get_active_station_db_path())
        gconn.execute(
            "UPDATE stations SET name = ?, updated_at = ? WHERE db_filename = ?",
            (radio_name or "Station", now_iso, active_fn),
        )
        if gconn.total_changes == 0:
            gconn.execute(
                "INSERT OR IGNORE INTO stations (name, db_filename, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (radio_name or "Station", active_fn, now_iso, now_iso),
            )
        gconn.commit()
        gconn.close()
    except Exception:
        pass

    return jsonify({
        "ok": True,
        "message": (
            "Settings updated successfully. DSP changed while the station remained ON AIR."
            if station_on_air and dsp_changed
            else "Settings updated successfully."
        ),
        "station": {"name": (radio_name or "").strip() or "Station", "db_filename": os.path.basename(current_db_filename or "")},
        "dsp": dsp_live_result,
    })


@app.route("/api/studio/scripts", methods=["GET"])
@login_required
def api_studio_scripts_list():
    current_db_path = get_active_station_db_path()
    if not current_db_path:
        return jsonify({"ok": True, "scripts": [], "has_station": False})
    init_db()

    scripts_by_path = {}

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT id, script_path, auto_start, status FROM station_scripts ORDER BY id ASC")
        rows = c.fetchall() or []
        conn.close()
        for row in rows:
            try:
                item = dict(row)
            except Exception:
                item = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
            script_path = str(item.get("script_path") or "").strip()
            if not script_path:
                continue
            scripts_by_path[script_path] = {
                "id": int(item.get("id") or (len(scripts_by_path) + 1)),
                "script_path": script_path,
                "auto_start": 1 if int(item.get("auto_start") or 0) else 0,
                "status": str(item.get("status") or "Stopped").strip() or "Stopped",
            }
    except Exception:
        pass

    try:
        raw = _station_state_get("studio_scripts")
        if raw:
            data = json.loads(raw)
            if isinstance(data, list):
                for index, item in enumerate(data, start=1):
                    if not isinstance(item, dict):
                        continue
                    script_path = str(item.get("script_path") or "").strip()
                    if not script_path:
                        continue
                    existing = scripts_by_path.get(script_path, {})
                    scripts_by_path[script_path] = {
                        "id": int(existing.get("id") or item.get("id") or index),
                        "script_path": script_path,
                        "auto_start": 1 if (existing.get("auto_start") or item.get("auto_start")) else 0,
                        "status": str(existing.get("status") or item.get("status") or "Stopped").strip() or "Stopped",
                    }
    except Exception:
        pass

    station_key = str(get_active_station_key() or "").strip()
    now_dt = datetime.now().replace(microsecond=0)
    scripts = sorted(scripts_by_path.values(), key=lambda item: int(item.get("id") or 0))
    for item in scripts:
        try:
            payload = _script_waiting_payload_from_cached_definition(
                station_key,
                int(item.get("id") or 0),
                str(item.get("script_path") or ""),
                str(item.get("status") or "Stopped"),
                now_dt,
            )
            item.update(payload)
        except Exception:
            item.setdefault("next_run_at", "")
            item.setdefault("next_run_time", "")
            item.setdefault("wait_for_time", "")
            item.setdefault("server_time", now_dt.isoformat(sep=" ", timespec="seconds"))
    return jsonify({"ok": True, "scripts": scripts, "server_time": now_dt.isoformat(sep=" ", timespec="seconds")})


@app.route("/api/studio/scripts/<int:script_id>", methods=["DELETE"])
@login_required
def api_studio_scripts_delete(script_id: int):
    init_db()
    station_key = str(get_active_station_key() or "").strip()
    deleted = False
    active_status = "Stopped"
    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT status FROM station_scripts WHERE id = ? LIMIT 1", (int(script_id),))
        row = c.fetchone()
        if row:
            try:
                active_status = str(row["status"] or "Stopped").strip() or "Stopped"
            except Exception:
                active_status = str(row[0] or "Stopped").strip() or "Stopped"
        # If the script is active, stop it before deletion.
        if active_status and active_status.lower() != "stopped":
            now = _utc_now_naive().isoformat(timespec="seconds")
            c.execute("UPDATE station_scripts SET status = ?, updated_at = ? WHERE id = ?", ("Stopped", now, int(script_id)))
            conn.commit()
        c.execute("DELETE FROM station_scripts WHERE id = ?", (int(script_id),))
        deleted = c.rowcount > 0
        conn.commit()
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to delete script: {exc}"}), 500

    try:
        raw = _station_state_get("studio_scripts")
        items = json.loads(raw) if raw else []
        if isinstance(items, list):
            filtered = []
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    item_id = int(item.get("id") or 0)
                except Exception:
                    item_id = 0
                if item_id == int(script_id):
                    changed = True
                    continue
                if item_id and str(item.get("status") or "").strip().lower() != "stopped":
                    item["status"] = "Stopped"
                filtered.append(item)
            if changed:
                _station_state_set("studio_scripts", json.dumps(filtered, ensure_ascii=False))
                deleted = True
    except Exception:
        pass

    try:
        _SCRIPT_ENGINE_LAST_RUN.pop((station_key, int(script_id)), None)
    except Exception:
        pass
    try:
        _invalidate_station_script_definition_cache(station_key, int(script_id))
    except Exception:
        pass
    try:
        _clear_script_status_caches(station_key, int(script_id))
    except Exception:
        pass

    if not deleted:
        return jsonify({"ok": False, "error": "Script not found."}), 404

    return jsonify({"ok": True, "id": int(script_id)})


@app.route("/api/studio/scripts/<int:script_id>/stop", methods=["POST"])
@login_required
def api_studio_scripts_stop(script_id: int):
    init_db()
    station_key = str(get_active_station_key() or "").strip()
    status_value = "Stopped"
    now = _utc_now_naive().isoformat(timespec="seconds")
    updated = False
    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT id FROM station_scripts WHERE id = ? LIMIT 1", (int(script_id),))
        row = c.fetchone()
        if row:
            c.execute(
                "UPDATE station_scripts SET status = ?, updated_at = ? WHERE id = ?",
                ("Stopped", now, int(script_id)),
            )
            conn.commit()
            updated = True
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to stop script: {exc}"}), 500

    try:
        raw = _station_state_get("studio_scripts")
        items = json.loads(raw) if raw else []
        if isinstance(items, list):
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if int(item.get("id") or 0) == int(script_id):
                    item["status"] = "Stopped"
                    changed = True
                    updated = True
            if changed:
                _station_state_set("studio_scripts", json.dumps(items, ensure_ascii=False))
    except Exception as exc:
        pass

    try:
        _SCRIPT_ENGINE_LAST_RUN.pop((station_key, int(script_id)), None)
    except Exception:
        pass
    try:
        _invalidate_station_script_definition_cache(station_key, int(script_id))
    except Exception:
        pass
    try:
        _sync_script_status_caches(station_key, int(script_id), "Stopped")
    except Exception:
        pass


    if not updated:
        return jsonify({"ok": False, "error": "Script not found."}), 404
    return jsonify({"ok": True, "id": int(script_id), "status": "Stopped"})



@app.route("/api/studio/scripts/<int:script_id>/content", methods=["GET"])
@login_required
def api_studio_scripts_content(script_id: int):
    init_db()
    script_path = ""

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT script_path FROM station_scripts WHERE id = ? LIMIT 1", (int(script_id),))
        row = c.fetchone()
        conn.close()
        if row:
            try:
                script_path = str(row["script_path"] or "").strip()
            except Exception:
                script_path = str(row[0] or "").strip()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to load script content: {exc}"}), 500

    if not script_path:
        return jsonify({"ok": False, "error": "Script not found."}), 404

    resolved_path = _resolve_station_script_file_path(get_active_station_key() or "", script_path)
    if not resolved_path or not os.path.isfile(resolved_path):
        return jsonify({"ok": False, "error": "Script file not found."}), 404

    try:
        with open(resolved_path, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to read script content: {exc}"}), 500

    return jsonify({"ok": True, "id": int(script_id), "script_path": script_path, "content": content})


@app.route("/api/studio/scripts/<int:script_id>/config", methods=["POST"])
@login_required
def api_studio_scripts_config(script_id: int):
    init_db()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}
    auto_start = 1 if payload.get("auto_start") else 0
    content = str(payload.get("content") or "")
    content_changed = bool(payload.get("content_changed"))
    updated = False
    script_path = ""

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT script_path FROM station_scripts WHERE id = ? LIMIT 1", (int(script_id),))
        row = c.fetchone()
        if row:
            try:
                script_path = str(row["script_path"] or "").strip()
            except Exception:
                script_path = str(row[0] or "").strip()
        c.execute("UPDATE station_scripts SET auto_start = ?, updated_at = ? WHERE id = ?", (auto_start, _utc_now_naive().isoformat(timespec="seconds"), int(script_id)))
        updated = c.rowcount > 0
        conn.commit()
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to update script config: {exc}"}), 500

    try:
        raw = _station_state_get("studio_scripts")
        items = json.loads(raw) if raw else []
        if isinstance(items, list):
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    if int(item.get("id") or 0) == int(script_id):
                        item["auto_start"] = 1 if auto_start else 0
                        changed = True
                except Exception:
                    pass
            if changed:
                _station_state_set("studio_scripts", json.dumps(items, ensure_ascii=False))
                updated = True
    except Exception:
        pass

    if updated and content_changed:
        station_key_for_cache = get_active_station_key() or ""
        resolved_path = _resolve_station_script_file_path(station_key_for_cache, script_path)
        if not resolved_path or not os.path.isfile(resolved_path):
            return jsonify({"ok": False, "error": "Script file not found."}), 404
        try:
            with open(resolved_path, "w", encoding="utf-8", errors="ignore") as fh:
                fh.write(content)
            # The editor already has the new content in memory, so refresh the
            # active cache from that payload instead of forcing the runtime poll
            # to reopen the .wbs file later.
            try:
                _cache_station_script_definition(station_key_for_cache, int(script_id), script_path, _parse_station_script_text(content), source="editor")
            except Exception:
                _invalidate_station_script_definition_cache(station_key_for_cache, int(script_id))
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Failed to save script content: {exc}"}), 500

    if not updated:
        return jsonify({"ok": False, "error": "Script not found."}), 404

    return jsonify({"ok": True, "id": int(script_id), "auto_start": auto_start})


@app.route("/api/studio/scripts/auto-start-on-air", methods=["POST"])
@login_required
def api_studio_scripts_auto_start_on_air():
    station_key = str(get_active_station_key() or "").strip()
    if not is_station_on_air(station_key):
        return jsonify({"ok": False, "error": "Station is OFF AIR.", "code": "station_off_air"}), 409

    items = []
    rules = []
    try:
        scripts = _read_station_scripts(station_key)
        now = _utc_now_naive().isoformat(timespec="seconds")
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        try:
            c = conn.cursor()
            for item in scripts:
                script_id = int(item.get("id") or 0)
                if not script_id:
                    continue
                auto_start = 1 if int(item.get("auto_start") or 0) else 0
                status_value = str(item.get("status") or "Stopped").strip() or "Stopped"
                if auto_start and not _script_status_is_active(status_value):
                    script_path_for_status = str(item.get("script_path") or "").strip()
                    response_status = "Waiting"
                    try:
                        entry = _load_station_script_definition_for_start(station_key, script_id, script_path_for_status)
                        wait_value = _script_definition_wait_value(entry)
                        if wait_value:
                            response_status = _format_script_waiting_status(wait_value, datetime.now().replace(microsecond=0))
                    except Exception:
                        response_status = "Waiting"
                    c.execute(
                        "UPDATE station_scripts SET status = ?, updated_at = ? WHERE id = ?",
                        (response_status, now, script_id),
                    )
                    try:
                        _sync_script_status_caches(station_key, script_id, response_status)
                    except Exception:
                        pass
                    item = dict(item)
                    item["status"] = response_status
                items.append({
                    "id": script_id,
                    "status": str(item.get("status") or "Stopped")
                })

            c.execute("SELECT * FROM scheduler_rules ORDER BY id ASC")
            scheduler_rows = c.fetchall() or []
            for row in scheduler_rows:
                try:
                    rule = dict(row)
                except Exception:
                    rule = {k: row[k] for k in row.keys()} if hasattr(row, 'keys') else {}
                rule_id = int(rule.get("id") or 0)
                auto_start = 1 if int(rule.get("auto_start") or 0) else 0
                is_enabled = 1 if int(rule.get("is_enabled") or 0) else 0
                next_run_at = str(rule.get("next_run_at") or "").strip()
                if auto_start and (not is_enabled or not next_run_at):
                    next_run = compute_next_run_at(str(rule.get("run_when") or ""), _utc_now_naive().replace(microsecond=0))
                    c.execute(
                        "UPDATE scheduler_rules SET is_enabled = 1, next_run_at = ?, updated_at = ? WHERE id = ?",
                        (next_run, now, rule_id),
                    )
                    rule["is_enabled"] = 1
                    rule["next_run_at"] = next_run
                rules.append({
                    "id": rule_id,
                    "is_enabled": 1 if int(rule.get("is_enabled") or 0) else 0,
                    "next_run_at": str(rule.get("next_run_at") or "")
                })
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to auto-start scripts/scheduler rules: {exc}"}), 500

    return jsonify({
        "ok": True,
        "station_on_air": True,
        "items": items,
        "rules": rules,
    })


@app.route("/api/studio/scripts/stop-active-off-air", methods=["POST"])
@login_required
def api_studio_scripts_stop_active_off_air():
    station_key = str(get_active_station_key() or "").strip()
    try:
        _stop_station_scripts_for_off_air(station_key)
        _stop_station_scheduler_rules_for_off_air(station_key)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to stop scripts/scheduler rules: {exc}"}), 500

    items = []
    try:
        items = _read_station_scripts(station_key)
    except Exception:
        items = []

    rules = []
    conn = None
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, is_enabled, next_run_at FROM scheduler_rules ORDER BY id ASC")
        rules = [dict(row) for row in (c.fetchall() or [])]
        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        rules = []

    return jsonify({
        "ok": True,
        "station_on_air": bool(is_station_on_air(station_key)),
        "items": [{
            "id": int(item.get("id") or 0),
            "status": str(item.get("status") or "Stopped")
        } for item in items],
        "rules": [{
            "id": int(item.get("id") or 0),
            "is_enabled": 1 if int(item.get("is_enabled") or 0) else 0,
            "next_run_at": str(item.get("next_run_at") or "")
        } for item in rules]
    })


@app.route("/api/studio/scripts/<int:script_id>/start", methods=["POST"])
@login_required
def api_studio_scripts_start(script_id: int):
    init_db()
    station_key = str(get_active_station_key() or "").strip()
    if not is_station_on_air(station_key):
        return jsonify({"ok": False, "error": "Scripts can only be started while the player is ON AIR.", "code": "station_off_air"}), 409
    now = _utc_now_naive().isoformat(timespec="seconds")
    updated = False
    response_status = "Running"
    script_path_for_status = ""

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT id, script_path FROM station_scripts WHERE id = ? LIMIT 1", (int(script_id),))
        row = c.fetchone()
        if row:
            script_path_for_status = str((row["script_path"] if hasattr(row, "__getitem__") else "") or "").strip()
            wait_value = ""
            try:
                entry = _load_station_script_definition_for_start(station_key, int(script_id), script_path_for_status)
                wait_value = _script_definition_wait_value(entry)
            except Exception:
                wait_value = ""
            response_status = _format_script_waiting_status(wait_value, datetime.now().replace(microsecond=0)) if wait_value else "Waiting"
            c.execute(
                "UPDATE station_scripts SET status = ?, updated_at = ? WHERE id = ?",
                (response_status, now, int(script_id)),
            )
            conn.commit()
            updated = True
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to start script: {exc}"}), 500

    try:
        raw = _station_state_get("studio_scripts")
        items = json.loads(raw) if raw else []
        if isinstance(items, list):
            changed = False
            for item in items:
                if not isinstance(item, dict):
                    continue
                if int(item.get("id") or 0) == int(script_id):
                    item["status"] = response_status
                    changed = True
                    updated = True
            if changed:
                _station_state_set("studio_scripts", json.dumps(items, ensure_ascii=False))
    except Exception as exc:
        pass

    if not updated:
        return jsonify({"ok": False, "error": "Script not found."}), 404

    _SCRIPT_ENGINE_LAST_RUN.pop((station_key, int(script_id)), None)
    try:
        _sync_script_status_caches(station_key, int(script_id), response_status)
    except Exception:
        pass
    try:
        _SCRIPT_ENGINE_WAKE_EVENT.set()
    except Exception:
        pass
    return jsonify({"ok": True, "id": int(script_id), "status": response_status})


@app.route("/api/studio/scripts", methods=["POST"])
@login_required
def api_studio_scripts_create():
    init_db()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    script_path = str(payload.get("script_path") or "").strip()
    auto_start = 1 if payload.get("auto_start") else 0
    if not script_path:
        return jsonify({"ok": False, "error": "Missing script_path."}), 400
    if not script_path.lower().endswith(".wbs"):
        return jsonify({"ok": False, "error": "Only .wbs files are supported."}), 400

    now = _utc_now_naive().isoformat(timespec="seconds")
    script_id = None

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        ensure_station_scripts_table(conn)
        c = conn.cursor()
        c.execute("SELECT id, status FROM station_scripts WHERE script_path = ? ORDER BY id ASC LIMIT 1", (script_path,))
        existing = c.fetchone()
        if existing:
            existing_id = int(existing["id"])
            existing_status = str(existing["status"] or "Stopped").strip() or "Stopped"
            c.execute(
                "UPDATE station_scripts SET auto_start = ?, updated_at = ? WHERE id = ?",
                (auto_start, now, existing_id),
            )
            script_id = existing_id
            status_value = existing_status
        else:
            c.execute(
                """
                INSERT INTO station_scripts (script_path, auto_start, status, created_at, updated_at)
                VALUES (?, ?, 'Stopped', ?, ?)
                """,
                (script_path, auto_start, now, now),
            )
            script_id = int(c.lastrowid)
            status_value = "Stopped"
        conn.commit()
        conn.close()
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to save script: {exc}"}), 500

    raw = _station_state_get("studio_scripts")
    items = []
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                items = [item for item in loaded if isinstance(item, dict)]
        except Exception:
            items = []

    updated = False
    for item in items:
        if str(item.get("script_path") or "").strip() == script_path:
            item["id"] = int(item.get("id") or script_id or 0)
            item["auto_start"] = 1 if auto_start else 0
            item["status"] = str(item.get("status") or status_value or "Stopped").strip() or "Stopped"
            updated = True
            break
    if not updated:
        items.append({
            "id": int(script_id or (len(items) + 1)),
            "script_path": script_path,
            "auto_start": 1 if auto_start else 0,
            "status": str(status_value or "Stopped"),
        })
    _station_state_set("studio_scripts", json.dumps(items, ensure_ascii=False))
    return jsonify({"ok": True, "id": int(script_id or 0), "status": str(status_value or "Stopped")})


@app.route("/scheduler")
@login_required
def scheduler():
    return redirect(url_for("broadcaster"), code=302)


@app.route("/api/scheduler/rules", methods=["GET"])
@login_required
def api_scheduler_rules_list():
    current_db_path = get_active_station_db_path()
    if not current_db_path:
        return jsonify({"ok": True, "rules": [], "has_station": False})
    init_db()
    conn = get_db()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM scheduler_rules ORDER BY id DESC")
    rows = c.fetchall() or []
    conn.close()
    rules = []
    for row in rows:
        try:
            item = dict(row)
        except Exception:
            item = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
        rules.append(item)
    return jsonify({"ok": True, "rules": rules})

@app.route("/api/scheduler/rules", methods=["POST"])
@login_required
def api_scheduler_create_rule():
    init_db()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    name = (payload.get("name") or "").strip()
    run_when = (payload.get("run_when") or "").strip()
    insert_kind = (payload.get("insert_kind") or "").strip().lower()
    insert_value = (payload.get("insert_value") or "").strip()
    priority = (payload.get("priority") or "").strip().lower()
    auto_start = 1 if int(payload.get("auto_start") or 0) else 0
    is_enabled = 0

    if not name or not run_when or not insert_kind or not insert_value or not priority:
        return jsonify({"ok": False, "error": "Missing required fields."}), 400

    if insert_kind not in ("file", "stream", "dir"):
        return jsonify({"ok": False, "error": "Invalid insert_kind."}), 400

    if priority not in ("next", "immediate", "end"):
        return jsonify({"ok": False, "error": "Invalid priority."}), 400

    next_run = compute_next_run_at(run_when, datetime.now().replace(microsecond=0))
    now = _utc_now_naive().isoformat(timespec="seconds")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO scheduler_rules (
            is_enabled, auto_start, name, run_when, insert_kind, insert_value, priority,
            last_run_at, next_run_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (is_enabled, auto_start, name, run_when, insert_kind, insert_value, priority, next_run, now, now),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "redirect": url_for("broadcaster")})


@app.route("/api/scheduler/rules/<int:rule_id>/toggle", methods=["POST"])
@login_required
def api_scheduler_toggle_rule(rule_id: int):
    init_db()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    is_enabled = 1 if int(payload.get("is_enabled") or 0) else 0

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM scheduler_rules WHERE id = ?", (rule_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"ok": False, "error": "Rule not found."}), 404

    now = _utc_now_naive().isoformat(timespec="seconds")
    next_run = None
    if is_enabled:
        c.execute("SELECT run_when FROM scheduler_rules WHERE id = ?", (rule_id,))
        rr = c.fetchone()
        rw = (rr[0] if rr else "")
        next_run = compute_next_run_at(str(rw or ""), _utc_now_naive().replace(microsecond=0))
    c.execute(
        "UPDATE scheduler_rules SET is_enabled = ?, next_run_at = ?, updated_at = ? WHERE id = ?",
        (is_enabled, next_run, now, rule_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "redirect": url_for("broadcaster")})


@app.route("/api/scheduler/rules/<int:rule_id>", methods=["PUT"])
@login_required
def api_scheduler_update_rule(rule_id: int):
    init_db()
    try:
        payload = request.get_json(force=True) or {}
    except Exception:
        payload = {}

    name = (payload.get("name") or "").strip()
    run_when = (payload.get("run_when") or "").strip()
    insert_kind = (payload.get("insert_kind") or "").strip().lower()
    insert_value = (payload.get("insert_value") or "").strip()
    priority = (payload.get("priority") or "").strip().lower()
    auto_start = 1 if int(payload.get("auto_start") or 0) else 0
    is_enabled = 1 if int(payload.get("is_enabled") or 0) else 0

    if not name or not run_when or not insert_kind or not insert_value or not priority:
        return jsonify({"ok": False, "error": "Missing required fields."}), 400

    if insert_kind not in ("file", "stream", "dir"):
        return jsonify({"ok": False, "error": "Invalid insert_kind."}), 400

    if priority not in ("next", "immediate", "end"):
        return jsonify({"ok": False, "error": "Invalid priority."}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM scheduler_rules WHERE id = ?", (rule_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"ok": False, "error": "Rule not found."}), 404

    next_run = compute_next_run_at(run_when, datetime.now().replace(microsecond=0)) if is_enabled else None
    now = _utc_now_naive().isoformat(timespec="seconds")
    c.execute(
        """
        UPDATE scheduler_rules
        SET is_enabled = ?, auto_start = ?, name = ?, run_when = ?, insert_kind = ?, insert_value = ?, priority = ?, next_run_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (is_enabled, auto_start, name, run_when, insert_kind, insert_value, priority, next_run, now, rule_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "redirect": url_for("broadcaster")})


@app.route("/api/scheduler/rules/<int:rule_id>", methods=["DELETE"])
@login_required
def api_scheduler_delete_rule(rule_id: int):
    init_db()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, is_enabled FROM scheduler_rules WHERE id = ?", (rule_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "error": "Rule not found."}), 404

    was_enabled = 0
    try:
        was_enabled = 1 if int(row[1] if not isinstance(row, sqlite3.Row) else row["is_enabled"] or 0) else 0
    except Exception:
        try:
            was_enabled = 1 if int((dict(row) if hasattr(row, 'keys') else {}).get('is_enabled') or 0) else 0
        except Exception:
            was_enabled = 0

    now = _utc_now_naive().isoformat(timespec="seconds")
    if was_enabled:
        c.execute(
            "UPDATE scheduler_rules SET is_enabled = 0, next_run_at = NULL, updated_at = ? WHERE id = ?",
            (now, rule_id),
        )
    c.execute("DELETE FROM scheduler_rules WHERE id = ?", (rule_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "redirect": url_for("broadcaster"), "stopped_before_delete": bool(was_enabled)})


@app.route("/encoders", methods=["GET", "POST"])
@login_required
def broadcast():
    if request.method == "GET":
        return redirect(url_for("broadcaster"), code=302)

    conn = get_db()
    c = conn.cursor()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            stream_id = request.form.get("stream_id")
            name = request.form.get("name", "").strip()
            host = request.form.get("host", "").strip()
            port = int(request.form.get("port", "8000") or 8000)
            mount = request.form.get("mount", "").strip()
            password = request.form.get("password", "").strip()
            codec = request.form.get("codec", "mp3").strip()
            bitrate = int(request.form.get("bitrate", "128") or 128)
            station_description = request.form.get("station_description", "").strip()
            genre = request.form.get("genre", "").strip()
            website_url = request.form.get("url", "").strip()
            if name and host and mount:
                if stream_id:
                    c.execute(
                        """UPDATE icecast_streams SET name = ?, host = ?, port = ?, mount = ?, password = ?, codec = ?, bitrate = ?, station_description = ?, genre = ?, website_url = ? WHERE id = ?""",
                        (name, host, port, mount, password or None, codec, bitrate, station_description or None, genre or None, website_url or None, stream_id),
                    )
                else:
                    c.execute(
                        """INSERT INTO icecast_streams (name, host, port, mount, password, codec, bitrate, created_at, station_description, genre, website_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (name, host, port, mount, password or None, codec, bitrate, datetime.now().isoformat(timespec="seconds"), station_description or None, genre or None, website_url or None),
                    )
                conn.commit()
        elif action == "toggle_autostart":
            stream_id = request.form.get("stream_id")
            autostart_val = 1 if request.form.get("autostart") in ("1", "true", "on", "yes") else 0
            if stream_id:
                c.execute("UPDATE icecast_streams SET autostart = ? WHERE id = ?", (autostart_val, stream_id))
                conn.commit()
    conn.close()
    return redirect(url_for("broadcaster"), code=302)


def _native_encoder_runtime_snapshot(station_key: str) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Return station-running state and authoritative native encoder outputs.

    ``enabled`` is configuration intent, not live-stream state.  Keeping the
    parent engine state alongside each output prevents the Encoders window from
    treating an enabled but off-air output as still encoding.
    """
    try:
        state = get_audio_engine().get_icecast_output_state(station_key=station_key)
    except Exception:
        return False, {}
    if not isinstance(state, Mapping):
        return False, {}
    station_running = bool(state.get("engine_running"))
    outputs = state.get("outputs")
    if not isinstance(outputs, list):
        return station_running, {}
    result: dict[str, dict[str, Any]] = {}
    for item in outputs:
        if not isinstance(item, Mapping):
            continue
        output_id = str(item.get("output_id") or "").strip()
        if output_id.startswith("stream_"):
            result[output_id] = dict(item)
    return station_running, result


def _native_encoder_outputs_by_id(station_key: str) -> dict[str, dict[str, Any]]:
    """Return daemon outputs owned by rows in the Encoders window."""
    return _native_encoder_runtime_snapshot(station_key)[1]


def _native_encoder_output_is_enabled(stream_id: int, station_key: str) -> bool:
    output = _native_encoder_outputs_by_id(station_key).get(_native_stream_output_id(stream_id), {})
    return bool(output.get("enabled"))


def _start_encoder_if_autostart_on_air(
    stream_id: int,
    *,
    autostart: bool,
    station_key: str = "",
) -> dict[str, Any]:
    """Start a newly saved Autostart encoder immediately while its station is ON AIR."""
    resolved_station = str(station_key or get_active_station_key() or "").strip()
    result = {
        "autostart_requested": bool(autostart),
        "station_running": False,
        "started_immediately": False,
    }
    if not autostart or not resolved_station:
        return result

    result["station_running"] = bool(is_station_on_air(resolved_station))
    if not result["station_running"]:
        return result

    _encoder_action_native(int(stream_id), "start")
    try:
        set_encoder_started_at(int(stream_id), datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass
    result["started_immediately"] = True
    return result


@app.route("/api/encoders", methods=["GET"])
def api_encoders():
    """Return Encoders-window rows with authoritative native runtime status."""
    try:
        if not session.get("user_id"):
            return jsonify({"success": False, "error": "unauthorized"}), 401

        station_key = str(get_active_station_key() or "").strip()
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM icecast_streams ORDER BY id ASC")
        raw_streams = c.fetchall()
        conn.close()

        station_running, runtime_outputs = (
            _native_encoder_runtime_snapshot(station_key)
            if station_key else (False, {})
        )
        streams = []
        for row in raw_streams:
            try:
                stream_dict = dict(row)
            except Exception:
                stream_dict = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}

            stream_id_value = stream_dict.get("id")
            output_id = _native_stream_output_id(int(stream_id_value or 0))
            runtime = runtime_outputs.get(output_id, {})
            runtime_status = str(runtime.get("status") or "").strip().lower()
            is_enabled = bool(runtime.get("enabled"))
            is_connected = bool(runtime.get("connected"))
            is_running = bool(
                station_running
                and is_connected
                and runtime_status == "streaming"
            )
            is_starting = bool(
                station_running
                and is_enabled
                and not is_running
                and runtime_status in {"configured", "starting", "connecting"}
            )
            state = "Running" if is_running else ("Starting" if is_starting else "Stopped")

            started_at = None
            try:
                if stream_id_value is not None:
                    started_at = get_encoder_started_at(int(stream_id_value))
            except Exception:
                started_at = None

            if is_running and not started_at and stream_id_value is not None:
                try:
                    started_at = datetime.now().isoformat(timespec="seconds")
                    set_encoder_started_at(int(stream_id_value), started_at)
                except Exception:
                    started_at = None

            elapsed_seconds = 0
            started_at_epoch = None
            if is_running and started_at:
                try:
                    started_dt = datetime.fromisoformat(str(started_at))
                    elapsed_seconds = max(0, int((datetime.now() - started_dt).total_seconds()))
                    started_at_epoch = float(started_dt.timestamp())
                except Exception:
                    elapsed_seconds = 0
                    started_at_epoch = None
            elif (not is_running) and stream_id_value is not None and started_at:
                try:
                    clear_encoder_started_at(int(stream_id_value))
                except Exception:
                    pass
                started_at = None

            streams.append({
                "id": stream_id_value,
                "name": stream_dict.get("name"),
                "host": stream_dict.get("host"),
                "port": stream_dict.get("port"),
                "mount": stream_dict.get("mount"),
                "codec": stream_dict.get("codec"),
                "bitrate": stream_dict.get("bitrate"),
                "autostart": bool(stream_dict.get("autostart")),
                "add_year_to_icecast_meta": bool(stream_dict.get("add_year_to_icecast_meta")),
                "running": is_running,
                "connected": is_connected,
                "enabled": is_enabled,
                "station_running": station_running,
                "state": state,
                "runtime_output_id": output_id,
                "runtime_status": runtime_status,
                "runtime_error": str(runtime.get("error") or ""),
                "started_at": started_at,
                "started_at_epoch": started_at_epoch,
                "elapsed_seconds": elapsed_seconds,
            })

        return jsonify({"success": True, "streams": streams, "server_now_epoch": time.time()})
    except Exception as e:
        try:
            return jsonify({"success": False, "error": str(e)}), 500
        except Exception:
            return jsonify({"success": False, "error": "server_error"}), 500


@app.route("/api/encoders/<int:stream_id>", methods=["GET"])
def api_encoder_get(stream_id: int):
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM icecast_streams WHERE id = ?", (stream_id,))
        row = c.fetchone()
        if row is None:
            return jsonify({"success": False, "error": "not_found"}), 404
        try:
            stream = dict(row)
        except Exception:
            stream = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
        return jsonify({"success": True, "stream": stream})
    finally:
        conn.close()


@app.route("/api/encoders/create", methods=["POST"])
def api_encoder_create():
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    host = str(payload.get("host") or "").strip()
    mount = str(payload.get("mount") or "").strip()
    password = str(payload.get("password") or "")
    codec = str(payload.get("codec") or "mp3").strip().lower()
    name = str(payload.get("name") or "").strip()
    station_description = str(payload.get("station_description") or "").strip()
    genre = str(payload.get("genre") or "").strip()
    website_url = str(payload.get("website_url") or "").strip()
    autostart = 1 if payload.get("autostart") in (1, True, "1", "true", "on", "yes") else 0
    add_year_to_icecast_meta = 1 if payload.get("add_year_to_icecast_meta") in (1, True, "1", "true", "on", "yes") else 0
    try:
        port = int(payload.get("port") or 8000)
    except Exception:
        port = 8000
    try:
        bitrate = int(payload.get("bitrate") or 128)
    except Exception:
        bitrate = 128
    if not host or not mount or not name:
        return jsonify({"success": False, "error": "missing_required_fields"}), 400
    if codec not in {"mp3", "aacplusv2"}:
        codec = "mp3"
    if port < 1 or port > 65535:
        return jsonify({"success": False, "error": "invalid_port"}), 400

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO icecast_streams (name, host, port, mount, password, codec, bitrate, autostart, add_year_to_icecast_meta, created_at, station_description, genre, website_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                host,
                port,
                mount,
                password or None,
                codec,
                bitrate,
                autostart,
                add_year_to_icecast_meta,
                datetime.now().isoformat(timespec="seconds"),
                station_description or None,
                genre or None,
                website_url or None,
            ),
        )
        conn.commit()
        stream_id = c.lastrowid
    finally:
        conn.close()

    autostart_result = {
        "autostart_requested": bool(autostart),
        "station_running": False,
        "started_immediately": False,
    }
    try:
        autostart_result = _start_encoder_if_autostart_on_air(
            int(stream_id),
            autostart=bool(autostart),
            station_key=str(get_active_station_key() or "").strip(),
        )
    except Exception as exc:
        app.logger.warning("Unable to start newly created Autostart encoder %s: %s", stream_id, exc)
        autostart_result["start_error"] = str(exc)

    _publish_ui_encoders_changed(get_active_station_key() or "", "encoder_created", stream_id)
    return jsonify({"success": True, "stream_id": stream_id, **autostart_result})


@app.route("/api/encoders/<int:stream_id>/configure", methods=["POST"])
def api_encoder_configure(stream_id: int):
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    host = str(payload.get("host") or "").strip()
    mount = str(payload.get("mount") or "").strip()
    password = str(payload.get("password") or "")
    codec = str(payload.get("codec") or "mp3").strip().lower()
    name = str(payload.get("name") or "").strip()
    station_description = str(payload.get("station_description") or "").strip()
    genre = str(payload.get("genre") or "").strip()
    website_url = str(payload.get("website_url") or "").strip()
    autostart = 1 if payload.get("autostart") in (1, True, "1", "true", "on", "yes") else 0
    add_year_to_icecast_meta = 1 if payload.get("add_year_to_icecast_meta") in (1, True, "1", "true", "on", "yes") else 0
    try:
        port = int(payload.get("port") or 8000)
    except Exception:
        port = 8000
    try:
        bitrate = int(payload.get("bitrate") or 128)
    except Exception:
        bitrate = 128
    if not host or not mount or not name:
        return jsonify({"success": False, "error": "missing_required_fields"}), 400
    if codec not in {"mp3", "aacplusv2"}:
        codec = "mp3"
    if port < 1 or port > 65535:
        return jsonify({"success": False, "error": "invalid_port"}), 400

    # Runtime ownership is keyed by stream_<id>; do not infer it from mount
    # reachability because another source client may own the same endpoint.
    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM icecast_streams WHERE id = ?", (stream_id,))
        if c.fetchone() is None:
            return jsonify({"success": False, "error": "not_found"}), 404
    finally:
        conn.close()

    station_key = str(get_active_station_key() or "").strip()
    station_running = bool(station_key and is_station_on_air(station_key))
    was_running = _native_encoder_output_is_enabled(stream_id, station_key) if station_key else False
    if was_running:
        try:
            _encoder_action_native(stream_id, "stop")
        except Exception:
            pass

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute(
            """
            UPDATE icecast_streams
            SET name = ?, host = ?, port = ?, mount = ?, password = ?, codec = ?, bitrate = ?, station_description = ?, genre = ?, website_url = ?, autostart = ?, add_year_to_icecast_meta = ?
            WHERE id = ?
            """,
            (name, host, port, mount, password, codec, bitrate, station_description, genre, website_url, autostart, add_year_to_icecast_meta, stream_id),
        )
        conn.commit()
    finally:
        conn.close()

    should_start = bool(was_running or (autostart and station_running))
    started_immediately = False
    if should_start:
        try:
            _encoder_action_native(stream_id, "start")
            set_encoder_started_at(stream_id, datetime.now().isoformat(timespec="seconds"))
            started_immediately = bool(not was_running and autostart and station_running)
        except Exception as exc:
            app.logger.warning("Unable to start configured encoder %s: %s", stream_id, exc)
    else:
        try:
            clear_encoder_started_at(stream_id)
        except Exception:
            pass

    _publish_ui_encoders_changed(get_active_station_key() or "", "encoder_configured", stream_id)
    return jsonify({
        "success": True,
        "restarted": bool(was_running),
        "started_immediately": bool(started_immediately),
    })


@app.route("/api/encoders/<int:stream_id>", methods=["DELETE"])
def api_encoder_delete(stream_id: int):
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT 1 FROM icecast_streams WHERE id = ?", (stream_id,))
        if c.fetchone() is None:
            return jsonify({"success": False, "error": "not_found"}), 404
    finally:
        conn.close()

    station_key = str(get_active_station_key() or "").strip()
    was_running = _native_encoder_output_is_enabled(stream_id, station_key) if station_key else False
    # Always clear the stable daemon output ID, even while disconnected or
    # reconnecting. After this succeeds a deleted DB row cannot keep streaming.
    try:
        if station_key:
            get_audio_engine().clear_icecast_output(
                _native_stream_output_id(stream_id), station_key=station_key
            )
    except Exception:
        pass

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM icecast_streams WHERE id = ?", (stream_id,))
        conn.commit()
    finally:
        conn.close()

    try:
        clear_encoder_started_at(stream_id)
    except Exception:
        pass

    _publish_ui_encoders_changed(get_active_station_key() or "", "encoder_deleted", stream_id)
    return jsonify({"success": True, "stopped_first": bool(was_running)})


@app.route("/api/encoders/<int:stream_id>/start", methods=["POST"])
def api_encoder_start(stream_id: int):
    """Start an encoder output and return immediately (frontend will poll for status)."""
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    _encoder_action_native(stream_id, "start")
    try:
        set_encoder_started_at(stream_id, datetime.now().isoformat(timespec="seconds"))
    except Exception:
        pass
    _publish_ui_encoders_changed(get_active_station_key() or "", "encoder_start_requested", stream_id)
    return jsonify({"success": True})


@app.route("/api/encoders/<int:stream_id>/stop", methods=["POST"])
def api_encoder_stop(stream_id: int):
    """Stop an encoder output and return immediately (frontend will poll for status)."""
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "unauthorized"}), 401
    _encoder_action_native(stream_id, "stop")
    try:
        clear_encoder_started_at(stream_id)
    except Exception:
        pass
    _publish_ui_encoders_changed(get_active_station_key() or "", "encoder_stop_requested", stream_id)
    return jsonify({"success": True})


def ensure_track(conn, path):
    filename = os.path.basename(path)
    c = conn.cursor()
    # If the track already exists, return its id.
    c.execute(
        "SELECT id, play_count, cue_in_seconds, cue_out_seconds, cue_trimmed_seconds, cue_duration_seconds, cue_fade_start_seconds, cue_analyzed_at FROM tracks WHERE path = ?",
        (path,),
    )
    row = c.fetchone()
    if row:
        track_id = row["id"]
        # Fast backfill: duration only (Mutagen) so UI can show track length immediately.
        try:
            existing_duration = row["cue_duration_seconds"]
            if existing_duration is None or float(existing_duration or 0.0) <= 0.0:
                dur = probe_duration_seconds(path)
                if dur is not None:
                    # If we do not have a cue_out yet, default it to the full duration.
                    cue_out_default = row["cue_out_seconds"]
                    try:
                        cue_out_default_value = float(cue_out_default) if cue_out_default is not None else None
                    except Exception:
                        cue_out_default_value = None
                    if cue_out_default_value is None or cue_out_default_value <= 0.0:
                        cue_out_default_value = dur
                    c.execute(
                        "UPDATE tracks SET cue_duration_seconds=?, cue_in_seconds=COALESCE(cue_in_seconds, 0.0), cue_out_seconds=COALESCE(cue_out_seconds, ?), cue_trimmed_seconds=COALESCE(cue_trimmed_seconds, ?), cue_analyzed_at=COALESCE(cue_analyzed_at, ?), audio_start_seconds=COALESCE(audio_start_seconds, 0.0), audio_end_seconds=COALESCE(audio_end_seconds, ?), audio_analyzed_at=COALESCE(audio_analyzed_at, ?) WHERE id=?",
                        (dur, cue_out_default_value, cue_out_default_value, datetime.now().isoformat(), dur, datetime.now().isoformat(), track_id),
                    )
                    conn.commit()
        except Exception:
            pass

        return track_id

    # New track -> insert immediately.
    created_at = datetime.now().isoformat()
    try:
        c.execute(
            "INSERT INTO tracks (path, filename, created_at) VALUES (?, ?, ?)",
            (path, filename, created_at),
        )
        track_id = c.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        c.execute(
            "SELECT id FROM tracks WHERE path = ?",
            (path,),
        )
        existing = c.fetchone()
        if existing:
            return existing["id"]
        raise

    # Fast duration probe for UI (best-effort)
    try:
        dur = probe_duration_seconds(path)
        if dur is not None:
            c.execute(
                "UPDATE tracks SET cue_duration_seconds=?, cue_in_seconds=COALESCE(cue_in_seconds, 0.0), cue_out_seconds=?, cue_trimmed_seconds=?, cue_analyzed_at=?, audio_start_seconds=COALESCE(audio_start_seconds, 0.0), audio_end_seconds=COALESCE(audio_end_seconds, ?), audio_analyzed_at=COALESCE(audio_analyzed_at, ?) WHERE id=?",
                (dur, dur, dur, datetime.now().isoformat(), dur, datetime.now().isoformat(), track_id),
            )
            conn.commit()
    except Exception:
        pass

    return track_id


@app.route("/api/library/files")
@login_required
def api_library_files():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Music library path is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Music library path does not exist: {base}"}), 400

    sub = request.args.get("sub", "").strip()
    current_dir = os.path.normpath(os.path.join(base, sub)) if sub else base

    try:
        if os.path.commonpath([base, current_dir]) != base:
            return jsonify({"error": "Invalid path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(current_dir):
        return jsonify({"error": f"Directory does not exist: {current_dir}"}), 400

    audio_exts = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac"}

    dirs = []
    files = []

    # Read-only lookup for existing tracks only. Do not create/update tracks while browsing,
    # because that makes directory navigation slow and can lock the station database.
    conn = get_db()
    c = conn.cursor()
    existing_tracks = {}
    try:
        file_paths = []
        for entry in os.listdir(current_dir):
            full_path = os.path.join(current_dir, entry)
            if os.path.isfile(full_path):
                ext = os.path.splitext(entry)[1].lower()
                if ext in audio_exts:
                    file_paths.append(full_path)
        if file_paths:
            placeholders = ",".join(["?"] * len(file_paths))
            c.execute(
                f"SELECT id, path, cue_duration_seconds FROM tracks WHERE path IN ({placeholders})",
                tuple(file_paths),
            )
            for row in c.fetchall():
                existing_tracks[row["path"]] = {
                    "id": row["id"],
                    "cue_duration_seconds": row["cue_duration_seconds"],
                }
    except Exception:
        existing_tracks = {}

    for entry in sorted(os.listdir(current_dir)):
        full_path = os.path.join(current_dir, entry)
        rel_from_base = os.path.relpath(full_path, base)
        if os.path.isdir(full_path):
            dirs.append(
                {
                    "name": entry,
                    "relative_path": rel_from_base,
                    "full_path": full_path,
                    "type": "dir",
                }
            )
        else:
            ext = os.path.splitext(entry)[1].lower()
            if ext not in audio_exts:
                continue
            track_info = existing_tracks.get(full_path, {})
            files.append(
                {
                    "id": track_info.get("id"),
                    "filename": entry,
                    "relative_path": rel_from_base,
                    "full_path": full_path,
                    "type": "file",
                    "cue_duration_seconds": track_info.get("cue_duration_seconds"),
                }
            )

    rel_current = os.path.relpath(current_dir, base)
    if rel_current == ".":
        rel_current = ""

    parent_rel = ""
    if current_dir != base:
        parent_rel = os.path.relpath(os.path.dirname(current_dir), base)
        if parent_rel == ".":
            parent_rel = ""

    try:
        conn.close()
    except Exception:
        pass

    return jsonify(
        {
            "current_sub": rel_current,
            "parent_sub": parent_rel,
            "dirs": dirs,
            "files": files,
        }
    )


def _is_autodj_adj_file(filename: str) -> bool:
    return str(filename or '').lower().endswith('.adj')


def _is_studio_script_file(filename: str) -> bool:
    return str(filename or '').lower().endswith('.wbs')


@app.route("/api/studio/scripts/browser")
@login_required
def api_studio_scripts_browser():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Base directory is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Base directory does not exist: {base}"}), 400

    sub = request.args.get("sub", "").strip()
    current_dir = os.path.normpath(os.path.join(base, sub)) if sub else base

    try:
        if os.path.commonpath([base, current_dir]) != base:
            return jsonify({"error": "Invalid path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(current_dir):
        return jsonify({"error": f"Directory does not exist: {current_dir}"}), 400

    dirs = []
    files = []
    for entry in sorted(os.listdir(current_dir)):
        full_path = os.path.join(current_dir, entry)
        if os.path.isdir(full_path):
            dirs.append({
                "name": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "dir",
            })
            continue
        if os.path.isfile(full_path) and _is_studio_script_file(entry):
            files.append({
                "name": entry,
                "filename": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "file",
            })

    rel_current = os.path.relpath(current_dir, base)
    if rel_current == ".":
        rel_current = ""

    parent_rel = ""
    if current_dir != base:
        parent_rel = os.path.relpath(os.path.dirname(current_dir), base)
        if parent_rel == ".":
            parent_rel = ""

    return jsonify({
        "base_path": base,
        "current_sub": rel_current,
        "parent_sub": parent_rel,
        "dirs": dirs,
        "files": files,
    })


@app.route("/api/studio/autodj/text-browser")
@login_required
def api_studio_autodj_text_browser():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Base music directory is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Base music directory does not exist: {base}"}), 400

    sub = request.args.get("sub", "").strip()
    current_dir = os.path.normpath(os.path.join(base, sub)) if sub else base

    try:
        if os.path.commonpath([base, current_dir]) != base:
            return jsonify({"error": "Invalid path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(current_dir):
        return jsonify({"error": f"Directory does not exist: {current_dir}"}), 400

    dirs = []
    files = []
    for entry in sorted(os.listdir(current_dir)):
        full_path = os.path.join(current_dir, entry)
        if os.path.isdir(full_path):
            dirs.append({
                "name": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "dir",
            })
            continue
        if os.path.isfile(full_path) and _is_autodj_adj_file(entry):
            files.append({
                "name": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "file",
            })

    rel_current = os.path.relpath(current_dir, base)
    if rel_current == ".":
        rel_current = ""

    parent_rel = ""
    if current_dir != base:
        parent_rel = os.path.relpath(os.path.dirname(current_dir), base)
        if parent_rel == ".":
            parent_rel = ""

    return jsonify({
        "base_path": base,
        "current_sub": rel_current,
        "parent_sub": parent_rel,
        "dirs": dirs,
        "files": files,
    })


@app.route("/api/studio/autodj/text-save", methods=["POST"])
@login_required
def api_studio_autodj_text_save():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Base music directory is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Base music directory does not exist: {base}"}), 400

    data = request.get_json(force=True) or {}
    sub = str(data.get("sub") or "").strip()
    filename = str(data.get("filename") or "").strip()
    content = str(data.get("content") or "")

    if not filename:
        return jsonify({"error": "Filename is required."}), 400
    if "/" in filename or "\\" in filename:
        return jsonify({"error": "Filename must not contain path separators."}), 400
    if filename in {".", ".."}:
        return jsonify({"error": "Invalid filename."}), 400
    root_name, ext = os.path.splitext(filename)
    if ext and ext.lower() != ".adj":
        return jsonify({"error": "Filename must use the .adj extension."}), 400
    if not ext:
        filename = f"{filename}.adj"

    current_dir = os.path.normpath(os.path.join(base, sub)) if sub else base
    try:
        if os.path.commonpath([base, current_dir]) != base:
            return jsonify({"error": "Invalid path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(current_dir):
        return jsonify({"error": f"Directory does not exist: {current_dir}"}), 400

    target_path = os.path.abspath(os.path.join(current_dir, filename))
    try:
        if os.path.commonpath([base, target_path]) != base:
            return jsonify({"error": "Invalid target path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid target path"}), 400

    with open(target_path, "w", encoding="utf-8") as handle:
        handle.write(content)

    return jsonify({
        "success": True,
        "filename": os.path.basename(target_path),
        "relative_path": os.path.relpath(target_path, base),
    })


@app.route("/api/studio/autodj/text-load-browser")
@login_required
def api_studio_autodj_text_load_browser():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Base music directory is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Base music directory does not exist: {base}"}), 400

    sub = request.args.get("sub", "").strip()
    current_dir = os.path.normpath(os.path.join(base, sub)) if sub else base

    try:
        if os.path.commonpath([base, current_dir]) != base:
            return jsonify({"error": "Invalid path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid path"}), 400

    if not os.path.isdir(current_dir):
        return jsonify({"error": f"Directory does not exist: {current_dir}"}), 400

    dirs = []
    files = []
    for entry in sorted(os.listdir(current_dir)):
        full_path = os.path.join(current_dir, entry)
        if os.path.isdir(full_path):
            dirs.append({
                "name": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "dir",
            })
        elif os.path.isfile(full_path) and _is_autodj_adj_file(entry):
            files.append({
                "name": entry,
                "relative_path": os.path.relpath(full_path, base),
                "type": "file",
            })

    rel_current = os.path.relpath(current_dir, base)
    if rel_current == ".":
        rel_current = ""

    parent_rel = ""
    if current_dir != base:
        parent_rel = os.path.relpath(os.path.dirname(current_dir), base)
        if parent_rel == ".":
            parent_rel = ""

    return jsonify({
        "base_path": base,
        "current_sub": rel_current,
        "parent_sub": parent_rel,
        "dirs": dirs,
        "files": files,
    })


@app.route("/api/studio/autodj/text-load", methods=["POST"])
@login_required
def api_studio_autodj_text_load():
    settings = get_settings()
    if not settings or not settings["music_library_path"]:
        return jsonify({"error": "Base music directory is not configured in Settings."}), 400

    base = os.path.abspath(settings["music_library_path"])
    if not os.path.isdir(base):
        return jsonify({"error": f"Base music directory does not exist: {base}"}), 400

    data = request.get_json(force=True) or {}
    relative_path = str(data.get("relative_path") or "").strip()
    if not relative_path:
        return jsonify({"error": "Please select a file to load."}), 400

    target_path = os.path.abspath(os.path.normpath(os.path.join(base, relative_path)))
    try:
        if os.path.commonpath([base, target_path]) != base:
            return jsonify({"error": "Invalid file path"}), 400
    except ValueError:
        return jsonify({"error": "Invalid file path"}), 400

    if not os.path.isfile(target_path):
        return jsonify({"error": f"File does not exist: {relative_path}"}), 400
    if not _is_autodj_adj_file(target_path):
        return jsonify({"error": "Only .adj files can be loaded here."}), 400

    try:
        with open(target_path, "r", encoding="utf-8") as handle:
            content = handle.read()
    except UnicodeDecodeError:
        return jsonify({"error": "The selected file is not a UTF-8 text file."}), 400

    return jsonify({
        "success": True,
        "filename": os.path.basename(target_path),
        "relative_path": os.path.relpath(target_path, base),
        "content": content,
    })

def _ensure_library_categories_schema(conn):
    """Create the canonical library category table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT,
            created_at TEXT NOT NULL
        )
        """
    )


@app.route("/api/library/categories", methods=["GET", "POST"])
@login_required
def api_library_categories():
    conn = get_db()
    c = conn.cursor()

    try:
        _ensure_library_categories_schema(conn)
        conn.commit()

        created_category = None
        if request.method == "POST":
            data = request.get_json(force=True, silent=True) or {}
            name = str(data.get("name", "")).strip()
            color = str(data.get("color", "")).strip() or None
            if not name:
                return jsonify({"error": "Category name is required."}), 400

            created_at_value = datetime.now().isoformat()
            try:
                c.execute(
                    "INSERT INTO categories (name, color, created_at) VALUES (?, ?, ?)",
                    (name, color, created_at_value),
                )
            except Exception:
                _ensure_library_categories_schema(conn)
                c = conn.cursor()
                c.execute(
                    "INSERT INTO categories (name, color, created_at) VALUES (?, ?, ?)",
                    (name, color, created_at_value),
                )
            created_id = c.lastrowid
            conn.commit()
            if created_id:
                c.execute("SELECT id, name, color FROM categories WHERE id = ?", (created_id,))
                created_row = c.fetchone()
                if created_row:
                    created_category = {
                        "id": created_row["id"],
                        "name": created_row["name"],
                        "color": created_row["color"] or "#38bdf8",
                    }

        c.execute("SELECT id, name, color FROM categories ORDER BY name ASC, id ASC")
        rows = c.fetchall()

        categories = []
        for row in rows:
            categories.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "color": row["color"] or "#38bdf8",
                }
            )

        payload = {"categories": categories}
        if created_category is not None:
            payload["created_category"] = created_category
        return jsonify(payload)
    finally:
        conn.close()


@app.route("/api/library/category/<int:category_id>/tracks")
@login_required
def api_library_category_tracks(category_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT tracks.id, tracks.filename, tracks.path, tracks.cue_duration_seconds
        FROM category_tracks
        JOIN tracks ON tracks.id = category_tracks.track_id
        WHERE category_tracks.category_id = ?
        ORDER BY tracks.filename ASC
        """,
        (category_id,),
    )
    rows = c.fetchall()
    conn.close()

    tracks = []
    for row in rows:
        tracks.append(
            {
                "id": row["id"],
                "filename": row["filename"],
                "path": row["path"],
                "cue_duration_seconds": row["cue_duration_seconds"],
            }
        )

    return jsonify({"tracks": tracks})


@app.route("/api/library/category/<int:category_id>/assign", methods=["POST"])
@login_required
def api_library_assign(category_id):
    data = request.get_json(force=True)
    track_ids = data.get("track_ids", [])
    directory_paths = data.get("directory_paths", [])
    file_paths = data.get("file_paths", [])

    if not isinstance(track_ids, list):
        return jsonify({"error": "track_ids must be a list"}), 400
    if not isinstance(directory_paths, list):
        return jsonify({"error": "directory_paths must be a list"}), 400
    if not isinstance(file_paths, list):
        return jsonify({"error": "file_paths must be a list"}), 400

    settings = get_settings()
    music_library_path = ""
    if settings:
        if isinstance(settings, dict):
            music_library_path = str(settings.get("music_library_path") or "").strip()
        else:
            try:
                if "music_library_path" in settings.keys():
                    music_library_path = str(settings["music_library_path"] or "").strip()
            except Exception:
                music_library_path = ""
    if not music_library_path:
        return jsonify({"error": "Music library path is not configured in Settings."}), 400

    base = os.path.abspath(music_library_path)
    if not os.path.isdir(base):
        return jsonify({"error": f"Music library path does not exist: {base}"}), 400

    audio_exts = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac"}

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT id FROM categories WHERE id = ?", (category_id,))
    cat = c.fetchone()
    if not cat:
        conn.close()
        return jsonify({"error": "Category not found"}), 404

    resolved_track_ids = set()

    for tid in track_ids:
        try:
            resolved_track_ids.add(int(tid))
        except (TypeError, ValueError):
            continue

    for rel_file in file_paths:
        rel_file_str = str(rel_file or "").strip()
        if not rel_file_str:
            continue
        file_full_path = os.path.abspath(os.path.join(base, rel_file_str))
        try:
            if os.path.commonpath([base, file_full_path]) != base:
                continue
        except ValueError:
            continue
        if not os.path.isfile(file_full_path):
            continue
        ext = os.path.splitext(file_full_path)[1].lower()
        if ext not in audio_exts:
            continue
        try:
            track_id = ensure_track(conn, file_full_path)
        except Exception:
            continue
        resolved_track_ids.add(track_id)

    for rel_dir in directory_paths:
        rel_dir_str = str(rel_dir or "").strip()
        if not rel_dir_str:
            continue
        directory_full_path = os.path.abspath(os.path.join(base, rel_dir_str))
        try:
            if os.path.commonpath([base, directory_full_path]) != base:
                continue
        except ValueError:
            continue
        if not os.path.isdir(directory_full_path):
            continue

        for walk_base, _walk_dirs, walk_files in os.walk(directory_full_path):
            for filename in walk_files:
                ext = os.path.splitext(filename)[1].lower()
                if ext not in audio_exts:
                    continue
                full_path = os.path.join(walk_base, filename)
                try:
                    track_id = ensure_track(conn, full_path)
                except Exception:
                    continue
                resolved_track_ids.add(track_id)

    created_at = datetime.now().isoformat()
    for tid_int in sorted(resolved_track_ids):
        c.execute(
            """
            INSERT OR IGNORE INTO category_tracks (category_id, track_id, created_at)
            VALUES (?, ?, ?)
            """,
            (category_id, tid_int, created_at),
        )

    conn.commit()
    conn.close()

    return jsonify({"success": True, "assigned_track_count": len(resolved_track_ids)})


@app.route("/api/library/category/<int:category_id>/rename", methods=["POST"])
@login_required
def api_library_rename_category(category_id):
    data = request.get_json(force=True)
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"error": "Category name is required."}), 400

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM categories WHERE id = ?", (category_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Category not found"}), 404

    c.execute("UPDATE categories SET name = ? WHERE id = ?", (name, category_id))
    conn.commit()
    c.execute("SELECT id, name, color FROM categories WHERE id = ?", (category_id,))
    updated = c.fetchone()
    conn.close()
    return jsonify({
        "success": True,
        "category": {
            "id": int(updated["id"]),
            "name": updated["name"],
            "color": (updated["color"] or "#38bdf8")
        }
    })
@app.route("/api/library/category/<int:category_id>/delete", methods=["POST"])
@login_required
def api_library_delete_category(category_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM category_tracks WHERE category_id = ?", (category_id,))
    c.execute("DELETE FROM autodj_rotation WHERE category_id = ?", (category_id,))
    c.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


def ensure_autodj_rotation_table(conn: sqlite3.Connection) -> None:
    """Create the canonical AutoDJ rotation table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS autodj_rotation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            norules INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT ''
        )
        """
    )

def seed_autodj_rotation_if_empty(conn: sqlite3.Connection) -> None:
    """If rotation is empty, seed it with all categories (in id order)."""
    try:
        c = conn.cursor()
        # Ensure table exists first
        ensure_autodj_rotation_table(conn)
        c.execute("SELECT COUNT(*) AS cnt FROM autodj_rotation")
        row = c.fetchone()
        try:
            cnt = int((row["cnt"] if row else 0) or 0)
        except Exception:
            cnt = int((row[0] if row else 0) or 0)
        if cnt > 0:
            return
        # Seed from categories
        c.execute("SELECT id FROM categories ORDER BY id ASC")
        cats = [int(r[0] if not hasattr(r, 'keys') else r.get('id', 0)) for r in c.fetchall() or []]
        pos = 1
        for cid in cats:
            if cid <= 0:
                continue
            c.execute(
                "INSERT INTO autodj_rotation (position, category_id, norules) VALUES (?, ?, 0)",
                (pos, cid),
            )
            pos += 1
        conn.commit()
    except Exception:
        pass
def get_autodj_rotation():
    conn = get_db()
    ensure_autodj_rotation_table(conn)
    seed_autodj_rotation_if_empty(conn)
    c = conn.cursor()
    c.execute(
        """
        SELECT r.position, r.category_id, COALESCE(c.name, '') AS name, COALESCE(r.norules, 0) AS norules
        FROM autodj_rotation r
        LEFT JOIN categories c ON c.id = r.category_id
        ORDER BY r.position ASC
        """
    )
    rows = c.fetchall()
    conn.close()
    rotation = []
    for row in rows:
        rotation.append(
            {
                "position": int(row["position"]),
                "category_id": int(row["category_id"]),
                "name": row["name"],
                "norules": int(row["norules"] or 0),
            }
        )
    return rotation


def set_autodj_rotation(items):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM autodj_rotation")
    now = _utc_now_naive().isoformat()
    for idx, it in enumerate(items, start=1):
        if isinstance(it, dict):
            cat_id = it.get("category_id")
            norules = 1 if it.get("norules") else 0
        else:
            cat_id = it
            norules = 0
        c.execute(
            "INSERT INTO autodj_rotation (position, category_id, norules, created_at) VALUES (?, ?, ?, ?)",
            (idx, int(cat_id), int(norules), now),
        )
    conn.commit()
    conn.close()


def parse_autodj_editor_text_to_rotation(conn: sqlite3.Connection, editor_text: str):
    """Parse Studio AutoDJ editor text into autodj_rotation rows."""
    ensure_autodj_rotation_table(conn)
    text = str(editor_text or "")
    lines = [line.strip() for line in text.replace("\r", "").split("\n")]
    pattern = re.compile(
        r"^Cat\['((?:\\.|[^'])+)'\]\.QueueBottom\((.*?)\)\s*;\s*$",
        re.IGNORECASE,
    )

    c = conn.cursor()
    c.execute("SELECT id, name FROM categories")
    category_rows = c.fetchall() or []
    by_name = {}
    by_name_ci = {}
    for row in category_rows:
        cat_id = int(row["id"])
        name = str(row["name"] or "")
        by_name[name] = cat_id
        by_name_ci[name.casefold()] = cat_id

    items = []
    for line in lines:
        if not line:
            continue
        match = pattern.match(line)
        if not match:
            continue

        raw_name = match.group(1)
        args_text = str(match.group(2) or "")
        category_name = raw_name.replace("\\'", "'").replace("\\\\", "\\")
        category_id = by_name.get(category_name)
        if category_id is None:
            category_id = by_name_ci.get(category_name.casefold())
        if category_id is None:
            continue

        args = [arg.strip().casefold() for arg in args_text.split(",") if arg.strip()]
        items.append({
            "category_id": category_id,
            "norules": "norules" in args,
        })

    return items

@app.route("/api/autodj/rotation", methods=["GET"])
@login_required
def api_autodj_get_rotation():
    return jsonify({"rotation": get_autodj_rotation()})


def ensure_autodj_settings_table(conn: sqlite3.Connection) -> None:
    """Create the canonical AutoDJ settings table."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS autodj_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            no_repeat_artist_minutes INTEGER NOT NULL DEFAULT 0,
            no_repeat_title_minutes INTEGER NOT NULL DEFAULT 0,
            no_repeat_track_minutes INTEGER NOT NULL DEFAULT 0,
            keep_queue INTEGER NOT NULL DEFAULT 0,
            editor_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            rotation_next_index INTEGER NOT NULL DEFAULT 0,
            rotation_signature TEXT NOT NULL DEFAULT ''
        )
        """
    )
def get_autodj_settings():
    conn = get_db()
    ensure_autodj_settings_table(conn)
    c = conn.cursor()
    c.execute(
        "SELECT no_repeat_artist_minutes, no_repeat_title_minutes, "
        "no_repeat_track_minutes, keep_queue, editor_text "
        "FROM autodj_settings ORDER BY id ASC LIMIT 1"
    )
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO autodj_settings (no_repeat_artist_minutes, no_repeat_title_minutes, "
            "no_repeat_track_minutes, keep_queue, editor_text, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (60, 60, 60, 3, '', datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        c.execute(
            "SELECT no_repeat_artist_minutes, no_repeat_title_minutes, "
            "no_repeat_track_minutes, keep_queue, editor_text "
            "FROM autodj_settings ORDER BY id ASC LIMIT 1"
        )
        row = c.fetchone()
    result = {
        "no_repeat_artist_minutes": int(row["no_repeat_artist_minutes"] or 0),
        "no_repeat_title_minutes": int(row["no_repeat_title_minutes"] or 0),
        "no_repeat_track_minutes": int(row["no_repeat_track_minutes"] or 0),
        "keep_queue": int(row["keep_queue"] or 0),
        "editor_text": str(row["editor_text"] or ""),
    }
    conn.close()
    return result



@app.route("/api/autodj/settings", methods=["GET"])
@login_required
def api_autodj_get_settings():
    return jsonify({"settings": get_autodj_settings()})


@app.route("/api/autodj/settings", methods=["POST"])
@login_required
def api_autodj_set_settings():
    try:
        payload = request.get_json(force=True) or {}

        def as_nonneg_int(v):
            try:
                n = int(v or 0)
            except Exception:
                n = 0
            return n if n >= 0 else 0

        no_repeat_artist_minutes = as_nonneg_int(payload.get("no_repeat_artist_minutes"))
        no_repeat_title_minutes = as_nonneg_int(payload.get("no_repeat_title_minutes"))
        no_repeat_track_minutes = as_nonneg_int(payload.get("no_repeat_track_minutes"))
        keep_queue = as_nonneg_int(payload.get("keep_queue"))
        editor_text = str(payload.get("editor_text") or "")
        conn = get_db()
        ensure_autodj_settings_table(conn)
        ensure_autodj_rotation_table(conn)
        c = conn.cursor()
        c.execute("SELECT id FROM autodj_settings ORDER BY id ASC LIMIT 1")
        row = c.fetchone()
        if row:
            c.execute(
                "UPDATE autodj_settings SET no_repeat_artist_minutes = ?, no_repeat_title_minutes = ?, "
                "no_repeat_track_minutes = ?, keep_queue = ?, editor_text = ? WHERE id = ?",
                (no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, keep_queue, editor_text, row["id"]),
            )
        else:
            c.execute(
                "INSERT INTO autodj_settings (no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, keep_queue, editor_text, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (no_repeat_artist_minutes, no_repeat_title_minutes, no_repeat_track_minutes, keep_queue, editor_text, datetime.now().isoformat(timespec="seconds")),
            )

        parsed_rotation = parse_autodj_editor_text_to_rotation(conn, editor_text)
        c.execute("DELETE FROM autodj_rotation")
        now = _utc_now_naive().isoformat()
        for idx, item in enumerate(parsed_rotation, start=1):
            c.execute(
                "INSERT INTO autodj_rotation (position, category_id, norules, created_at) VALUES (?, ?, ?, ?)",
                (idx, int(item["category_id"]), 1 if item.get("norules") else 0, now),
            )

        conn.commit()
        conn.close()
        return jsonify({"success": True, "settings": get_autodj_settings(), "rotation": get_autodj_rotation()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/autodj/categories", methods=["GET"])
@login_required
def api_autodj_get_categories():
    """Return 'active' categories (categories that contain at least one track)."""
    conn = get_db()
    c = conn.cursor()
    c.execute(
        """
        SELECT c.id, c.name, c.color
        FROM categories c
        JOIN category_tracks ct ON ct.category_id = c.id
        GROUP BY c.id
        HAVING COUNT(ct.track_id) > 0
        ORDER BY c.name ASC
        """
    )
    rows = c.fetchall()
    conn.close()

    categories = []
    for row in rows:
        categories.append(
            {
                "id": row["id"],
                "name": row["name"],
                "color": row["color"] or "#38bdf8",
            }
        )

    return jsonify({"categories": categories})



@app.route("/api/autodj/rotation", methods=["POST"])
@login_required
def api_autodj_set_rotation():
    try:
        payload = request.get_json(force=True) or {}
        rotation = payload.get("rotation", [])
        items = []
        for item in rotation:
            if isinstance(item, dict):
                cat_id = item.get("category_id")
                norules = 1 if item.get("norules") else 0
            else:
                cat_id = item
                norules = 0
            if cat_id is None:
                continue
            items.append({"category_id": int(cat_id), "norules": int(norules)})
        set_autodj_rotation(items)

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/library/category/<int:category_id>/tracks/delete", methods=["POST"])
@login_required
def api_library_delete_category_tracks(category_id):
    data = request.get_json(force=True)
    track_ids = data.get("track_ids", [])
    if not isinstance(track_ids, list):
        return jsonify({"error": "track_ids must be a list"}), 400
    conn = get_db()
    c = conn.cursor()
    for tid in track_ids:
        try:
            tid_int = int(tid)
        except (TypeError, ValueError):
            continue
        c.execute(
            "DELETE FROM category_tracks WHERE category_id = ? AND track_id = ?",
            (category_id, tid_int),
        )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


def _url_queue_duration_seconds_from_path(path_value):
    """Return display duration for URL:<seconds>:<url> queue items, or 0 for infinite/unknown.

    This helper only affects UI/API duration display and does not change playback timing.
    """
    try:
        raw = str(path_value or "").strip()
        if not raw.startswith("URL:"):
            return 0.0
        parts = raw.split(":", 2)
        if len(parts) < 3:
            return 0.0
        duration = int(float(str(parts[1]).strip() or "0"))
        if duration > 0:
            return float(duration)
    except Exception:
        pass
    return 0.0


@app.route("/api/queue", methods=["GET"])
@login_required
def api_queue():
    try:
        rows = _get_playback_repository().list_queue_items()
    except NoActiveStationError:
        return jsonify({"queue": [], "no_station": True})
    except sqlite3.OperationalError:
        try:
            init_db()
            rows = _get_playback_repository().list_queue_items()
        except Exception:
            raise

    queue = []
    for row in rows:
        path_value = row.get("path")
        cue_duration = row.get("cue_duration_seconds")
        url_duration = _url_queue_duration_seconds_from_path(path_value)
        if url_duration > 0:
            cue_duration = url_duration
        queue.append(
            {
                "id": row.get("queue_id"),
                "track_id": row.get("track_id"),
                "filename": row.get("filename"),
                "path": path_value,
                "cue_duration_seconds": cue_duration,
                "position": row.get("position"),
            }
        )
    return jsonify({"queue": queue})


@app.route("/api/queue/add", methods=["POST"])
@login_required
def api_queue_add():
    data = request.get_json(force=True) or {}
    track_ids = data.get("track_ids", [])
    file_paths = data.get("file_paths", [])
    directory_paths = data.get("directory_paths", [])

    if not isinstance(track_ids, list):
        return jsonify({"error": "track_ids must be a list"}), 400
    if not isinstance(file_paths, list):
        return jsonify({"error": "file_paths must be a list"}), 400
    if not isinstance(directory_paths, list):
        return jsonify({"error": "directory_paths must be a list"}), 400

    resolved_track_ids = []
    seen_track_ids = set()

    def _append_track_id(value):
        try:
            tid_int = int(value)
        except (TypeError, ValueError):
            return
        if tid_int <= 0 or tid_int in seen_track_ids:
            return
        seen_track_ids.add(tid_int)
        resolved_track_ids.append(tid_int)

    for tid in track_ids:
        _append_track_id(tid)

    if file_paths or directory_paths:
        settings = get_settings()
        music_library_path = ""
        if settings:
            if isinstance(settings, dict):
                music_library_path = str(settings.get("music_library_path") or "").strip()
            else:
                try:
                    if "music_library_path" in settings.keys():
                        music_library_path = str(settings["music_library_path"] or "").strip()
                except Exception:
                    music_library_path = ""
        if not music_library_path:
            return jsonify({"error": "Music library path is not configured in Settings."}), 400

        base = os.path.abspath(music_library_path)
        if not os.path.isdir(base):
            return jsonify({"error": f"Music library path does not exist: {base}"}), 400

        audio_exts = {".mp3", ".flac", ".ogg", ".wav", ".m4a", ".aac"}
        conn = get_db()
        c = conn.cursor()
        try:
            for rel_file in file_paths:
                rel_file_str = str(rel_file or "").strip()
                if not rel_file_str:
                    continue
                file_full_path = os.path.abspath(os.path.join(base, rel_file_str))
                try:
                    if os.path.commonpath([base, file_full_path]) != base:
                        continue
                except ValueError:
                    continue
                if not os.path.isfile(file_full_path):
                    continue
                if os.path.splitext(file_full_path)[1].lower() not in audio_exts:
                    continue
                try:
                    _append_track_id(ensure_track(conn, file_full_path))
                except Exception:
                    continue

            for rel_dir in directory_paths:
                rel_dir_str = str(rel_dir or "").strip()
                if not rel_dir_str:
                    continue
                directory_full_path = os.path.abspath(os.path.join(base, rel_dir_str))
                try:
                    if os.path.commonpath([base, directory_full_path]) != base:
                        continue
                except ValueError:
                    continue
                if not os.path.isdir(directory_full_path):
                    continue

                directory_file_paths = []
                for walk_base, walk_dirs, walk_files in os.walk(directory_full_path):
                    walk_dirs.sort(key=lambda name: str(name or "").lower())
                    for filename in sorted(walk_files, key=lambda name: str(name or "").lower()):
                        if os.path.splitext(filename)[1].lower() not in audio_exts:
                            continue
                        full_path = os.path.join(walk_base, filename)
                        if os.path.isfile(full_path):
                            directory_file_paths.append(full_path)
                directory_file_paths.sort(key=lambda full_path: os.path.relpath(full_path, directory_full_path).lower())
                for full_path in directory_file_paths:
                    try:
                        _append_track_id(ensure_track(conn, full_path))
                    except Exception:
                        continue
        finally:
            try:
                conn.close()
            except Exception:
                pass

    if not resolved_track_ids:
        return jsonify({"error": "No queue items were resolved"}), 400

    created_queue_ids = _get_playback_repository().enqueue_track_ids(
        resolved_track_ids,
        "end",
    )
    if not created_queue_ids:
        return jsonify({"error": "Unable to add resolved tracks to queue"}), 500

    # Sync playlist and force A/B decks to follow the updated queue immediately.
    _sync_reload_and_rebootstrap_after_queue_mutation("queue_add")
    _publish_ui_queue_history_changed(get_active_station_key() or "", "queue_add")

    return jsonify({"success": True, "added_track_count": len(resolved_track_ids)})


def _parse_url_duration_from_payload(data, default=-1):
    try:
        value = data.get("duration", default) if isinstance(data, dict) else default
        duration = int(value)
    except (TypeError, ValueError):
        duration = int(default)
    if duration == -1:
        return -1
    if duration <= 0:
        return int(default)
    return duration


@app.route("/api/library/category/<int:category_id>/add-url", methods=["POST"])
@login_required
def api_library_category_add_url(category_id):
    data = request.get_json(force=True) or {}
    raw_url = str(data.get("url", "")).strip()
    if not raw_url:
        return jsonify({"error": "URL is required"}), 400
    if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
        return jsonify({"error": "Only http:// or https:// URLs are supported"}), 400

    station_key = get_active_station_key()
    if not station_key:
        return jsonify({"error": "No active station selected"}), 400

    conn = get_db()
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM categories WHERE id = ?", (category_id,))
        row = c.fetchone()
        if not row:
            return jsonify({"error": "Category not found"}), 404

        duration = _parse_url_duration_from_payload(data, -1)
        track_ids = _resolve_insert_to_track_ids_for_station(station_key, "stream", f"{duration}:{raw_url}")
        if not track_ids:
            return jsonify({"error": "Unable to resolve URL item"}), 500

        created_at = datetime.now().isoformat()
        assigned = 0
        for tid in track_ids:
            try:
                tid_int = int(tid)
            except (TypeError, ValueError):
                continue
            c.execute(
                """
                INSERT OR IGNORE INTO category_tracks (category_id, track_id, created_at)
                VALUES (?, ?, ?)
                """,
                (category_id, tid_int, created_at),
            )
            assigned += 1

        conn.commit()
        return jsonify({"success": True, "track_ids": track_ids, "assigned_track_count": assigned})
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.route("/api/queue/add-url", methods=["POST"])
@login_required
def api_queue_add_url():
    data = request.get_json(force=True) or {}
    raw_url = str(data.get("url", "")).strip()
    if not raw_url:
        return jsonify({"error": "URL is required"}), 400
    if not (raw_url.startswith("http://") or raw_url.startswith("https://")):
        return jsonify({"error": "Only http:// or https:// URLs are supported"}), 400

    station_key = get_active_station_key()
    if not station_key:
        return jsonify({"error": "No active station selected"}), 400

    duration = _parse_url_duration_from_payload(data, -1)
    track_ids = _resolve_insert_to_track_ids_for_station(station_key, "stream", f"{duration}:{raw_url}")
    if not track_ids:
        return jsonify({"error": "Unable to resolve URL item"}), 500

    if not _enqueue_track_ids_for_station(station_key, track_ids, "end"):
        return jsonify({"error": "Unable to add URL to queue"}), 500

    _sync_reload_and_rebootstrap_after_queue_mutation("queue_add_url")
    _publish_ui_queue_history_changed(station_key, "queue_add_url")

    return jsonify({"success": True, "track_ids": track_ids})


def api_queue_reorder():
    """Update queue ordering through the canonical playback repository."""
    data = request.get_json(force=True)
    queue_ids = data.get("queue_ids", [])
    if not isinstance(queue_ids, list) or not queue_ids:
        return jsonify({"error": "queue_ids must be a non-empty list"}), 400

    _get_playback_repository().reorder_queue(queue_ids)
    _sync_reload_and_rebootstrap_after_queue_mutation("queue_reorder")
    _publish_ui_queue_history_changed(get_active_station_key() or "", "queue_reorder")
    return jsonify({"success": True})


@app.route("/api/queue/reorder", methods=["POST"])
@login_required
def api_queue_reorder_route():
    """Flask route wrapper for queue reorder."""
    return api_queue_reorder()

def api_queue_remove():
    data = request.get_json(force=True)
    queue_ids = data.get("queue_ids", [])
    if not isinstance(queue_ids, list) or not queue_ids:
        return jsonify({"error": "queue_ids must be a non-empty list"}), 400

    _get_playback_repository().remove_queue_items(queue_ids)
    _sync_reload_and_rebootstrap_after_queue_mutation("queue_remove")
    _publish_ui_queue_history_changed(get_active_station_key() or "", "queue_remove")
    return jsonify({"success": True})


@app.route("/api/queue/remove", methods=["POST"])
@login_required
def api_queue_remove_route():
    """Flask route wrapper for queue remove."""
    return api_queue_remove()


@app.route("/api/status", methods=["GET"])
@login_required

def api_status():
    """Return Studio state directly from the authoritative native get_state reply."""
    station_key = str(get_active_station_key() or "").strip()
    with_progress = request.args.get("with_progress", "0").strip().lower() in ("1", "true", "yes", "on")
    try:
        state = dict(_native_station_state(station_key) or {})
        with RADIO_STATE_LOCK:
            state["soft_stopped"] = bool(RADIO_STATE.get("stopped", False))
        return jsonify(_native_api_status_payload(station_key, state, with_progress=with_progress))
    except Exception as exc:
        try:
            logger.exception("[Status] native get_state rendering failed: %s", exc)
        except Exception:
            pass
        return jsonify({
            "status": "stopped",
            "paused": False,
            "pause_active": False,
            "song": None,
            "station_id": station_key,
            "audio_engine": "native",
            "error": f"{type(exc).__name__}: {exc}",
        }), 500


@app.route("/api/history")
@login_required
def api_history():
    try:
        try:
            rows = _get_playback_repository().list_history_items(limit=200)
        except NoActiveStationError:
            return jsonify({"items": [], "no_station": True})
        items = []
        for row in rows:
            played_at = row.get("played_at")
            try:
                played_at = datetime.fromisoformat(str(played_at)).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
            items.append(
                {
                    "id": row.get("id"),
                    "played_at": played_at,
                    "filename": row.get("filename"),
                    "cue_duration_seconds": row.get("cue_duration_seconds"),
                }
            )
        return jsonify({"items": items})
    except Exception as exc:
        import traceback as _tb
        _tb.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500



@app.route("/api/ui/events")
@login_required
def api_ui_events():
    """Server-Sent Events for lightweight Studio UI invalidations.

    Queue/history, ON-AIR and encoder state use the same low-overhead channel.
    Periodic polling remains as a fallback if the browser or proxy drops SSE.
    """
    try:
        query_last = int(request.args.get("last", "0") or 0)
    except Exception:
        query_last = 0
    try:
        header_last = int(request.headers.get("Last-Event-ID", "0") or 0)
    except Exception:
        header_last = 0
    last_seen = max(query_last, header_last)

    def _event_stream(start_seq: int):
        seq = int(start_seq or 0)
        try:
            # A first-time connection starts at the current sequence so it does not
            # replay stale invalidations from before the page was opened.
            if seq <= 0:
                with _UI_EVENT_CONDITION:
                    seq = int(_UI_EVENT_SEQ)
            yield ": connected\n\n"
            while True:
                pending_events = []
                with _UI_EVENT_CONDITION:
                    if _UI_EVENT_SEQ <= seq:
                        _UI_EVENT_CONDITION.wait(timeout=25.0)
                    if _UI_EVENT_SEQ > seq:
                        pending_events = [
                            dict(item)
                            for item in _UI_EVENT_HISTORY
                            if int(item.get("seq") or 0) > seq
                        ]
                        # If the client fell behind farther than the retained history,
                        # send the latest invalidation rather than leaving it stale.
                        if not pending_events and _UI_EVENT_LAST:
                            pending_events = [dict(_UI_EVENT_LAST)]

                if pending_events:
                    for event_payload in pending_events:
                        event_seq = int(event_payload.get("seq") or seq)
                        event_name = str(event_payload.get("type") or "ui_state_changed")
                        data = json.dumps(event_payload, separators=(",", ":"))
                        yield f"id: {event_seq}\nevent: {event_name}\ndata: {data}\n\n"
                        seq = max(seq, event_seq)
                else:
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            return
        except Exception:
            return

    return Response(
        _event_stream(last_seen),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )




def _perform_player_manual_next_action(
    station_key: str = "",
    action: str = "next",
    *,
    source: str = "internal",
    guarded_queue_ids=None,
) -> dict:
    """Queue one serialized Manual Next request through the player service."""
    if str(source or "").strip().lower() == "script" and _station_url_playback_active(station_key):
        return {
            "success": True,
            "accepted": False,
            "skipped": True,
            "mode": "scheduled_script_skipped_url_playback",
            "reason": "url_playback_active",
            "source": "script",
        }
    return _get_manual_next_orchestrator().perform_action(
        station_key,
        action=action,
        source=source,
        guarded_queue_ids=guarded_queue_ids,
    )


@app.route("/api/control", methods=["POST"])
@login_required
def api_control():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "").strip().lower()
    station_key = str(get_active_station_key() or "").strip()
    native_state = _native_station_state(station_key) if station_key else {}

    def _ensure_native_station_running() -> dict:
        state = _native_station_state(station_key)
        if bool(state.get("running")):
            return state
        station_start()
        state = _native_station_state(station_key)
        if not bool(state.get("running")):
            raise RuntimeError("The native station could not be started for this control action.")
        return state

    try:
        if not station_key:
            return jsonify({"success": False, "error": "No active station selected.", "action": action}), 400

        if action in {"play", "pause", "next", "skip", "prev", "previous"}:
            native_state = _ensure_native_station_running()

        if action in {"pause", "play"}:
            paused_before = bool(native_state.get("paused"))
            with RADIO_STATE_LOCK:
                stopped_before = bool(RADIO_STATE.get("stopped", False))

            freeze_elapsed = 0.0
            freeze_duration = 0.0
            if action == "pause" and not paused_before:
                try:
                    ui_live = _ab_get_current_ui_status(station_key) or {}
                    freeze_elapsed = float(ui_live.get("elapsed") or 0.0)
                    freeze_duration = float(ui_live.get("duration") or ui_live.get("orig_total") or 0.0)
                except Exception:
                    freeze_elapsed = 0.0
                    freeze_duration = 0.0
                with PROGRESS_LOCK:
                    ps = _get_progress_state(station_key)
                    if freeze_elapsed <= 0.0:
                        freeze_elapsed = float(ps.get("last_elapsed") or 0.0)
                    if freeze_duration <= 0.0:
                        freeze_duration = float(ps.get("last_duration") or 0.0)

            play_restart_result = None
            if action == "pause":
                target_paused = not paused_before
            else:
                target_paused = False
                # PLAY after a soft STOP, or PLAY while already running, restarts
                # the preserved current deck from 0:00. PLAY while paused resumes.
                if stopped_before or not paused_before:
                    try:
                        play_restart_result = _perform_ab_restart_current_track_from_zero(station_key)
                    except Exception as restart_exc:
                        return jsonify({
                            "success": False,
                            "error": str(restart_exc),
                            "action": action,
                            "cmd": "native.seek_active_abs_0",
                        }), 500

            pause_result = get_audio_engine().set_paused(target_paused, station_key=station_key) or {}
            pause_duration_ms = int(pause_result.get("pause_duration_ms") or 0)
            if not target_paused and pause_duration_ms > 0 and play_restart_result is None:
                pause_delta = float(pause_duration_ms) / 1000.0
                with _AB_PLAYER_LOCK:
                    for field in (
                        "started_at",
                        "transition_started_at",
                        "transition_not_before",
                        "pending_cueout_deadline",
                    ):
                        value = float(_AB_PLAYER_STATE.get(field) or 0.0)
                        if value > 0.0:
                            _AB_PLAYER_STATE[field] = value + pause_delta

            with RADIO_STATE_LOCK:
                RADIO_STATE["paused"] = bool(target_paused)
                if action == "play" or not target_paused:
                    RADIO_STATE["stopped"] = False

            with PROGRESS_LOCK:
                ps = _get_progress_state(station_key)
                if target_paused:
                    ps["paused"] = True
                    ps["paused_raw_elapsed"] = float(freeze_elapsed)
                    ps["last_elapsed"] = float(freeze_elapsed)
                    if freeze_duration > 0.0:
                        ps["last_duration"] = float(freeze_duration)
                else:
                    ps["paused"] = False
                    ps["paused_raw_elapsed"] = 0.0
                    if play_restart_result is not None:
                        ps["last_elapsed"] = 0.0
                        ps["last_source_elapsed"] = 0.0
                        ps["last_ui_raw_elapsed"] = 0.0

            wake_autodj_worker()
            response_payload = {
                "success": True,
                "cmd": "native.set_paused",
                "paused": bool(target_paused),
                "pause_active": bool(target_paused),
                "native_result": pause_result,
            }
            if play_restart_result is not None:
                response_payload["restart"] = play_restart_result
                response_payload["elapsed"] = 0.0
                response_payload["target_pos"] = 0.0
            return jsonify(response_payload)

        if action == "stop":
            native_state = _native_station_state(station_key)
            if not bool(native_state.get("running")):
                with RADIO_STATE_LOCK:
                    RADIO_STATE["paused"] = False
                    RADIO_STATE["stopped"] = True
                return jsonify({"success": True, "soft": False, "already_stopped": True})

            pause_result = get_audio_engine().set_paused(True, station_key=station_key) or {}
            with RADIO_STATE_LOCK:
                RADIO_STATE["paused"] = True
                RADIO_STATE["stopped"] = True
            with PROGRESS_LOCK:
                ps = _get_progress_state(station_key)
                ps["last_elapsed"] = 0.0
                ps["last_source_elapsed"] = 0.0
                ps["last_ui_raw_elapsed"] = 0.0
                ps["elapsed_offset"] = 0.0
                ps["offset_track_path"] = ""
                ps["paused"] = True
                ps["paused_raw_elapsed"] = 0.0
            wake_autodj_worker()
            return jsonify({
                "success": True,
                "soft": True,
                "cmd": "native.set_paused",
                "paused": True,
                "elapsed": 0.0,
                "target_pos": 0.0,
                "native_result": pause_result,
            })

        if action in {"next", "skip"}:
            result = _perform_player_manual_next_action(station_key, action=action, source="http")
            return jsonify(result)

        return jsonify({"success": True})
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        try:
            app.logger.exception("api_control failed (action=%s)", action)
        except Exception:
            pass
        return jsonify({"success": False, "error": str(e), "action": action}), 500





def _get_seek_end_guard_seconds(track: dict | None = None) -> float:
    """Return the near-EOF seek drop guard in seconds.

    Full-track seeking must remain available across the complete visible
    timeline. Near-end stability is handled by clamping the exact EOF target and
    restoring the real queue before the restarted segment reaches its handoff
    window, so the legacy last-30-seconds drop guard stays disabled.
    """
    return 0.0


def _get_seek_fade_next_threshold_seconds(track: dict | None = None) -> float:
    """Return the remaining-time window used for near-EOF seek diagnostics.

    Near-EOF manual seeks must still seek to the requested in-track position.
    The transition to the next item is allowed to happen only when the seeked
    segment reaches its natural cue-out/EOF, so this value is now diagnostic
    only and must not convert the seek into an immediate next command.
    """
    fade_out = 0.0
    try:
        fade_out = float((track or {}).get("fade_out_seconds") or 0.0)
    except Exception:
        fade_out = 0.0
    return max(10.0, fade_out + 6.0)


def _build_dropped_seek_response(seek_req: dict) -> dict:
    """Build a successful no-op response for a seek dropped by the EOF guard."""
    duration = float(seek_req.get("total_eff") or seek_req.get("stored_total") or 0.0)
    elapsed = float(seek_req.get("current_elapsed") or 0.0)
    return {
        "success": True,
        "ignored": True,
        "mode": "seek_end_guard_drop",
        "reason": str(seek_req.get("drop_reason") or "seek_too_close_to_track_end"),
        "target_pos": elapsed,
        "elapsed": elapsed,
        "duration": duration,
        "duration_display": _format_track_clock(duration) if duration > 0.0 else "",
        "resp": "seek_ignored_near_track_end",
    }
def _resolve_seek_request(data: dict | None = None) -> dict:
    data = data or {}
    request_started_at = time.time()
    station_key = get_active_station_key() or ""
    with NOW_PLAYING_LOCK:
        store = _get_now_playing_store(station_key)
        current_track = dict(store or {})
        cur_file = normalize_media_path(str(store.get("file") or "").strip())
        stored_total = float(store.get("display_original_duration") or store.get("duration") or 0.0)
        current_queue_id = int(store.get("queue_id") or 0)
        current_track_id = int(store.get("track_id") or 0)
    # Use the best visible elapsed position when deciding whether a seek is
    # backward. Near EOF the now-playing store can briefly hold a stale/segment
    # clock while the progress poller already has the full-track timeline. If we
    # misclassify that seek as forward, the queue restore can reload too early
    # and the playback state may advance to the second queued item instead of restarting
    # the current track.
    current_elapsed_for_direction = 0.0
    try:
        current_elapsed_for_direction = max(current_elapsed_for_direction, float(current_track.get("elapsed") or 0.0))
    except Exception:
        pass
    try:
        with PROGRESS_LOCK:
            ps = _get_progress_state(station_key)
            progress_path = normalize_media_path(str(ps.get("last_seen_now_playing_path") or ps.get("last_live_file") or ""))
            progress_elapsed = float(ps.get("last_elapsed") or 0.0)
        if progress_elapsed > 0.0 and (not progress_path or not cur_file or progress_path == cur_file):
            current_elapsed_for_direction = max(current_elapsed_for_direction, progress_elapsed)
    except Exception:
        pass
    try:
        ui_live = get_station_ui_status(timeout_sec=0.35, station_key=station_key) or {}
        ui_path = normalize_media_path(str(ui_live.get("file") or ""))
        ui_elapsed = float(ui_live.get("elapsed") or 0.0)
        # In native A/B mode the live deck descriptor is authoritative.
        # Refresh the seek request context before parsing the target, otherwise
        # a stale UI/NOW_PLAYING cache can seek the previous track and be
        # rejected as a mismatch.
        if ui_path and ui_path != cur_file:
            cur_file = ui_path
            current_track.update(ui_live)
            try:
                stored_total = float(ui_live.get("duration") or ui_live.get("orig_total") or stored_total or 0.0)
            except Exception:
                pass
            try:
                current_queue_id = int(ui_live.get("queue_id") or 0)
                current_track_id = int(ui_live.get("track_id") or 0)
            except Exception:
                pass
        if ui_elapsed > 0.0 and (not ui_path or not cur_file or ui_path == cur_file):
            current_elapsed_for_direction = max(current_elapsed_for_direction, ui_elapsed)
    except Exception:
        pass
    current_track["elapsed"] = max(float(current_track.get("elapsed") or 0.0), float(current_elapsed_for_direction or 0.0))

    if not cur_file:
        raise RuntimeError("no_active_track")
    if cur_file.startswith("http://") or cur_file.startswith("https://"):
        raise RuntimeError("seek_disabled_for_stream")

    target_pos = None
    if "target_pos" in data and data["target_pos"] is not None:
        try:
            target_pos = float(data["target_pos"])
        except Exception:
            target_pos = None
    if target_pos is None and "position" in data and data["position"] is not None:
        try:
            percent = float(data["position"])
            percent = max(0.0, min(100.0, percent))
            total = float(stored_total or 0.0)
            if total > 0.0:
                target_pos = (percent / 100.0) * total
        except Exception:
            target_pos = None
    if target_pos is None or (not math.isfinite(float(target_pos))):
        target_pos = 0.0

    target_seconds = int(math.floor(float(target_pos)))
    if target_seconds < 0:
        target_seconds = 0
    # Prefer the numeric click/drag position as the authoritative seek target.
    # target_label is only a display fallback; parsing it after target_pos
    # can round or reinterpret the real in-track seek position and cause jumps.
    target_label = str(data.get("target_label") or "").strip()
    if target_label and ("target_pos" not in data or data.get("target_pos") is None):
        target_seconds = parse_seek_target_to_seconds(target_label)

    total_for_clamp = 0.0
    try:
        total_for_clamp = float(stored_total or 0.0)
    except Exception:
        total_for_clamp = 0.0
    if total_for_clamp <= 0.0:
        try:
            total_for_clamp = float(get_track_total_duration_for_station_path(station_key, cur_file) or 0.0)
        except Exception:
            total_for_clamp = 0.0
    if total_for_clamp <= 0.0:
        raise RuntimeError("seek_total_unknown")

    cue_out_hint = current_track.get("cue_out_seconds")
    # Keep the real track cue-in separate from the requested seek target.
    # Older seek-restart code passed target_seconds as cue_in, which could
    # temporarily rewrite cue_in to the seek position after STOP/PLAY and make
    # near-end seeks wait too long before the normal cue-out handoff.
    cue_in_hint = current_track.get("cue_in_seconds")
    cue_in_eff, cue_out_eff, total_eff, seek_base_eff = _normalize_seek_window(total_for_clamp, cue_in_hint, cue_out_hint)
    min_target_seconds = int(math.floor(max(0.0, cue_in_eff)))
    max_target_seconds = int(math.floor(max(cue_in_eff, cue_out_eff) - 0.25))
    if max_target_seconds < min_target_seconds:
        max_target_seconds = min_target_seconds
    if target_seconds < min_target_seconds:
        target_seconds = min_target_seconds
    if target_seconds > max_target_seconds:
        target_seconds = max_target_seconds
    cue_in_eff, cue_out_eff, total_eff, seek_base_eff = _normalize_seek_window(total_for_clamp, cue_in_hint, cue_out_hint)

    remaining_after_target = max(0.0, float(cue_out_eff or total_eff or 0.0) - float(target_seconds or 0.0))
    fade_next_threshold_seconds = _get_seek_fade_next_threshold_seconds(current_track)
    if remaining_after_target <= fade_next_threshold_seconds:
        pass

    seek_end_guard_seconds = _get_seek_end_guard_seconds(current_track)
    if remaining_after_target < seek_end_guard_seconds:
        return {
            "data": dict(data or {}),
            "request_started_at": float(request_started_at),
            "station_key": station_key,
            "current_track": current_track,
            "cur_file": cur_file,
            "stored_total": float(stored_total),
            "current_queue_id": int(current_queue_id or 0),
            "current_track_id": int(current_track_id or 0),
            "current_elapsed": float(current_track.get("elapsed") or 0.0),
            "target_seconds": int(target_seconds),
            "cue_in_eff": float(cue_in_eff),
            "cue_out_eff": float(cue_out_eff),
            "total_eff": float(total_eff),
            "seek_base_eff": float(seek_base_eff),
            "drop_seek": True,
            "drop_reason": "seek_too_close_to_track_end",
            "remaining_after_target": float(remaining_after_target),
            "seek_end_guard_seconds": float(seek_end_guard_seconds),
        }

    restart_track = dict(current_track or {})
    restart_track["station_key"] = station_key
    restart_track["file"] = cur_file
    restart_track["cue_in_seconds"] = float(cue_in_eff)
    restart_track["cue_out_seconds"] = float(cue_out_eff)
    restart_track["fade_in_seconds"] = current_track.get("fade_in_seconds")
    restart_track["fade_out_seconds"] = current_track.get("fade_out_seconds")
    restart_track["cue_duration_seconds"] = float(total_eff)
    restart_track["duration"] = float(total_eff)
    restart_track["display_original_duration"] = float(total_eff)
    restart_track["display_seek_base"] = float(seek_base_eff)
    restart_track["_wb_seek_nofade"] = True
    # Keep the seek restart playlist isolated while the restarted segment is
    # becoming audible. The normal queue tail is written back immediately, but
    # the native deck planner may reload it only near EOF; this prevents a queued
    # track from becoming audible for a split second during seek restart.
    restart_track["_wb_seek_isolated"] = True
    restart_track["queue_id"] = int(current_queue_id or 0)
    restart_track["track_id"] = int(current_track_id or 0)

    return {
        "data": dict(data or {}),
        "request_started_at": float(request_started_at),
        "station_key": station_key,
        "current_track": current_track,
        "cur_file": cur_file,
        "stored_total": float(stored_total),
        "current_queue_id": int(current_queue_id or 0),
        "current_track_id": int(current_track_id or 0),
        "current_elapsed": float(current_elapsed_for_direction or current_track.get("elapsed") or 0.0),
        "is_backward_seek": bool(float(target_seconds) + 0.5 < float(current_elapsed_for_direction or current_track.get("elapsed") or 0.0)),
        "target_seconds": int(target_seconds),
        "cue_in_eff": float(cue_in_eff),
        "cue_out_eff": float(cue_out_eff),
        "total_eff": float(total_eff),
        "seek_base_eff": float(seek_base_eff),
        "restart_track": restart_track,
    }



def _perform_ab_manual_next_direct_handoff(
    station_key: str = "",
    *,
    reserved_queue_lines: list[str] | None = None,
    reservation_id: str = "",
) -> dict | None:
    """Execute one authoritative Manual Next handoff through the player service."""
    return _get_player_handoff_service().direct_handoff(
        station_key,
        reserved_queue_lines=reserved_queue_lines,
        reservation_id=reservation_id,
    )


def _perform_ab_restart_current_track_from_zero(station_key: str = "") -> dict:
    """Restart the preserved audible native deck from absolute 0:00."""
    sk = str(station_key or get_active_station_key() or "").strip()
    result = _perform_native_seek_request({
        "station_key": sk,
        "target_seconds": 0.0,
        "current_elapsed": 0.0,
    })
    result = dict(result or {})
    result["mode"] = "native_restart_current_track_from_zero"
    result["elapsed"] = 0.0
    result["target_pos"] = 0.0
    return result



def _perform_native_seek_request(seek_req: dict) -> dict:
    """Seek the currently audible native decoder without rebuilding the queue.

    The inactive deck remains preloaded.  The daemon performs an exact
    queue-id/slot-token seek, preserves a short PCM bridge while FFmpeg
    reopens, and resets any in-progress native transition.  Python updates its
    UI/transition clock only after the daemon accepts the ``sync_event``.
    """
    station_key = str(seek_req.get("station_key") or get_active_station_key() or "").strip()
    if not station_key:
        raise RuntimeError("no_active_track")

    with station_runtime_context(station_key):
        state = dict(get_audio_engine().get_state(station_key=station_key) or {})
    if not bool(state.get("running")):
        raise RuntimeError("native_engine_not_running")

    active = str(state.get("active_deck") or "A").upper()
    if active not in ("A", "B"):
        active = "A"
    active_lower = active.lower()

    try:
        queue_id = int(
            state.get("queue_id")
            or state.get("native_audio_probe_queue_id")
            or state.get(f"deck_{active_lower}_queue_id")
            or seek_req.get("current_queue_id")
            or 0
        )
    except Exception:
        queue_id = 0
    slot_token = str(
        state.get("slot_token")
        or state.get("native_audio_probe_slot_token")
        or state.get(f"deck_{active_lower}_slot_token")
        or ""
    ).strip()
    path = normalize_media_path(str(
        state.get("native_audio_probe_path")
        or seek_req.get("cur_file")
        or ""
    ))
    if not path or path.startswith("http://") or path.startswith("https://"):
        raise RuntimeError("seek_disabled_for_stream" if path else "no_active_track")
    if queue_id <= 0 or not slot_token:
        raise RuntimeError("native_seek_identity_unavailable")

    line = _native_status_line_for_state(station_key, state)
    info = _ab_line_info(line) if line else {}
    request_path = normalize_media_path(str(seek_req.get("cur_file") or ""))
    request_queue_id = int(seek_req.get("current_queue_id") or 0)
    if request_queue_id > 0 and queue_id > 0 and request_queue_id != queue_id:
        raise RuntimeError("seek_track_changed")
    if request_path and path and request_path != path:
        raise RuntimeError("seek_track_changed")

    target_seconds = max(0.0, float(seek_req.get("target_seconds") or 0.0))
    current_seconds = max(
        0.0,
        float(state.get("native_audio_probe_position_ms") or state.get("position_ms") or 0) / 1000.0,
    )
    total_seconds = max(
        0.0,
        float(seek_req.get("total_eff") or 0.0),
        float(state.get("native_audio_probe_source_end_ms") or 0) / 1000.0,
        float(info.get("orig_total") or 0.0),
    )
    cue_in = max(0.0, float(seek_req.get("cue_in_eff") or info.get("cue_in") or 0.0))
    cue_out = max(0.0, float(seek_req.get("cue_out_eff") or info.get("cue_out") or total_seconds or 0.0))
    if cue_out > 0.0:
        target_seconds = min(target_seconds, max(cue_in, cue_out - 0.001))

    audio_start = max(0.0, float(info.get("audio_start") or 0.0))
    if audio_start <= 0.0:
        audio_start = max(0.0, float(state.get("native_audio_probe_audio_start_ms") or 0) / 1000.0)
    audio_end = max(0.0, float(info.get("audio_end") or 0.0))
    if audio_end <= 0.0:
        audio_end = max(0.0, float(state.get("native_audio_probe_effective_end_ms") or 0) / 1000.0)

    identity = dict(info or {})
    identity.update({
        "station_key": station_key,
        "queue_id": queue_id,
        "track_id": int(info.get("track_id") or seek_req.get("current_track_id") or 0),
        "slot_token": slot_token,
        "deck": active,
        "path": path,
        "file": path,
        "cue_in": cue_in,
        "cue_out": cue_out,
        "orig_total": total_seconds,
        "audio_start": audio_start,
        "audio_end": audio_end,
    })
    seek_requested_at = time.time()
    segment_elapsed = max(0.0, target_seconds - cue_in)
    # Publish the pending state before the daemon command. A very fast decoder
    # may emit seek_applied before the HTTP handler regains control; pre-latching
    # prevents that applied event from being overwritten by a late pending write.
    with _AB_PLAYER_LOCK:
        if bool(_AB_PLAYER_STATE.get("enabled")):
            _AB_PLAYER_STATE["active"] = active_lower
            _AB_PLAYER_STATE["started_at"] = seek_requested_at - segment_elapsed
            _AB_PLAYER_STATE["transitioning"] = False
            _AB_PLAYER_STATE["transition_starting"] = False
            _AB_PLAYER_STATE["transition_started_at"] = 0.0
            _AB_PLAYER_STATE["transition_duration"] = 0.0
            _AB_PLAYER_STATE["transition_target"] = ""
            _AB_PLAYER_STATE["transition_from"] = ""
            _AB_PLAYER_STATE["transition_not_before"] = seek_requested_at + 10.0
            _AB_PLAYER_STATE["pending_cueout_transition"] = False
            _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
            _AB_PLAYER_STATE["pending_cueout_token"] = 0
            _AB_PLAYER_STATE["seek_pending"] = True
            _AB_PLAYER_STATE["seek_pending_active"] = active_lower
            _AB_PLAYER_STATE["seek_pending_queue_id"] = queue_id
            _AB_PLAYER_STATE["seek_pending_slot_token"] = slot_token
            _AB_PLAYER_STATE["seek_pending_deadline"] = seek_requested_at + 10.0
            _AB_PLAYER_STATE["seek_applied_at"] = 0.0
            _AB_PLAYER_STATE["seek_applied_source_position"] = 0.0
            _AB_PLAYER_STATE["generation"] = int(_AB_PLAYER_STATE.get("generation") or 0) + 1

    post_state = _publish_audio_engine_track_seeked(
        station_key=station_key,
        deck=active,
        identity=identity,
        target_seconds=target_seconds,
        from_seconds=current_seconds,
        source="native_seek_command",
    )

    now = time.time()
    seek_still_pending = True
    applied_anchor_at = 0.0
    applied_anchor_position = 0.0
    with _AB_PLAYER_LOCK:
        if bool(_AB_PLAYER_STATE.get("enabled")):
            _AB_PLAYER_STATE["active"] = active_lower
            seek_still_pending = bool(_AB_PLAYER_STATE.get("seek_pending"))
            # Do not overwrite a fast seek_applied callback with the request-time
            # fallback anchor. It is authoritative only while still pending.
            if seek_still_pending:
                _AB_PLAYER_STATE["started_at"] = now - segment_elapsed
            else:
                applied_anchor_at = float(_AB_PLAYER_STATE.get("seek_applied_at") or 0.0)
                applied_anchor_position = float(_AB_PLAYER_STATE.get("seek_applied_source_position") or 0.0)
    anchor_position = target_seconds if seek_still_pending or applied_anchor_position <= 0.0 else applied_anchor_position
    anchor_at = now if seek_still_pending or applied_anchor_at <= 0.0 else applied_anchor_at

    with NOW_PLAYING_LOCK:
        store = _get_now_playing_store(station_key)
        store["elapsed"] = anchor_position
        store["duration"] = total_seconds
        store["display_original_duration"] = total_seconds
        store["display_seek_base"] = cue_in
        store["cue_in_seconds"] = cue_in
        store["cue_out_seconds"] = cue_out
        store["manual_seek_base_seconds"] = cue_in
        store["manual_seek_anchor_abs_seconds"] = anchor_position
        store["manual_seek_anchor_at"] = anchor_at
        store["manual_seek_anchor_path"] = path
        store["manual_seek_track_path"] = path
        store["manual_seek_queue_id"] = queue_id
        store["manual_seek_track_id"] = int(identity.get("track_id") or 0)
        store["seek_session_id"] = int(now * 1000)
        store["pending_seek_restart"] = False
        store["seek_isolated_playlist_active"] = False
        store["file"] = path
        store["queue_id"] = queue_id
        store["track_id"] = int(identity.get("track_id") or 0)
        store["updated_at"] = anchor_at

    with PROGRESS_LOCK:
        progress = _get_progress_state(station_key)
        progress["elapsed_offset"] = 0.0
        progress["offset_track_path"] = ""
        progress["last_source_elapsed"] = anchor_position
        progress["last_elapsed"] = anchor_position
        progress["last_duration"] = total_seconds
        progress["last_ui_raw_elapsed"] = max(0.0, anchor_position - cue_in)
        progress["last_live_file"] = path
        progress["recent_track_path"] = path
        progress["recent_track_started_at"] = anchor_at - anchor_position
        progress["last_seen_now_playing_path"] = path
        progress["seek_end_hold"] = False

    invalidate_audio_engine_status_cache()
    _ensure_ab_monitor_thread()
    _publish_ui_event(
        "now_playing_changed",
        station_key,
        "native_seek",
        {
            "queue_id": queue_id,
            "track_id": int(identity.get("track_id") or 0),
            "file": path,
            "deck": active,
            "elapsed": target_seconds,
        },
    )
    return {
        "success": True,
        "mode": "native_seek",
        "target_pos": target_seconds,
        "elapsed": target_seconds,
        "duration": total_seconds,
        "duration_display": _format_track_clock(total_seconds) if total_seconds > 0.0 else "",
        "deck": active_lower,
        "queue_id": queue_id,
        "resp": "native_sync_event_track_seeked",
    }

@app.route("/api/seek", methods=["POST"])
@login_required
def api_seek():
    """Seek the active native deck to an absolute full-track position."""
    try:
        data = request.get_json(silent=True) or {}

        seek_req = _resolve_seek_request(data)
        station_key = str(seek_req.get("station_key") or "")
        if bool(seek_req.get("drop_seek")):
            return jsonify(_build_dropped_seek_response(seek_req))

        with NOW_PLAYING_LOCK:
            st = _get_now_playing_store(station_key)
            now_ts = time.time()
            cooldown_until = float(st.get("seek_cooldown_until") or 0.0)
            if bool(st.get("seek_singleflight_active")) or cooldown_until > now_ts:
                return jsonify({
                    "success": True,
                    "ignored": True,
                    "mode": "seek_cooldown_drop",
                    "reason": "seek_in_progress_or_cooldown",
                    "target_pos": float(seek_req.get("current_elapsed") or 0.0),
                    "elapsed": float(seek_req.get("current_elapsed") or 0.0),
                    "duration": float(seek_req.get("total_eff") or 0.0),
                    "duration_display": _format_track_clock(float(seek_req.get("total_eff") or 0.0)) if float(seek_req.get("total_eff") or 0.0) > 0.0 else "",
                    "resp": "seek_ignored_cooldown",
                })
            # Only an accepted seek may reset restore/fade state. Requests dropped
            # by the active-seek cooldown return above without touching any pending
            # restore timer, deferred reload, or fade preparation state.
            _clear_seek_transient_state_for_new_seek_locked(st)
            st["seek_singleflight_active"] = True
            st["seek_singleflight_pending"] = None
            st["seek_singleflight_started_at"] = now_ts
            # Keep only a short post-accept cooldown. The singleflight flag still
            # protects the active restart, while repeated in-track seek clicks do
            # not get ignored for several seconds after the first successful seek.
            st["seek_cooldown_until"] = now_ts + 1.0

        result = _perform_native_seek_request(seek_req)
        with NOW_PLAYING_LOCK:
            st = _get_now_playing_store(station_key)
            st["seek_singleflight_active"] = False
            st["seek_singleflight_pending"] = None
            st["seek_singleflight_started_at"] = 0.0
            st["seek_cooldown_until"] = max(float(st.get("seek_cooldown_until") or 0.0), time.time() + 0.75)
        return jsonify(result or {"success": False, "error": "seek_no_result"})
    except RuntimeError as e:
        msg = str(e)
        if msg in {
            "no_active_track",
            "seek_disabled_for_stream",
            "seek_total_unknown",
            "seek_track_changed",
            "native_engine_not_running",
            "native_seek_identity_unavailable",
        }:
            status = 409
        else:
            status = 500
        with NOW_PLAYING_LOCK:
            try:
                station_key = get_active_station_key() or ""
                st = _get_now_playing_store(station_key)
                st["seek_singleflight_active"] = False
                st["seek_singleflight_started_at"] = 0.0
            except Exception:
                pass
        return jsonify({"success": False, "error": msg}), status
    except Exception as e:
        with NOW_PLAYING_LOCK:
            try:
                station_key = get_active_station_key() or ""
                st = _get_now_playing_store(station_key)
                st["seek_singleflight_active"] = False
                st["seek_singleflight_started_at"] = 0.0
            except Exception:
                pass
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/audio-engine/status", methods=["GET"], endpoint="api_audio_engine_status")
def api_audio_engine_status():
    return jsonify(get_audio_engine_status())


def get_station_name_safe():
    """Return the runtime-safe station suffix for the active station."""
    try:
        return get_station_name_safe_for_station(get_active_station_key() or "")
    except Exception:
        return ""


@app.route("/audio-engine/start", methods=["POST"], endpoint="audio_engine_start_route")
@login_required
def audio_engine_start_route():
    """Start the active station through the authoritative native engine."""
    return station_start()


def _normalize_seek_window(total_length: float, cue_in: float | None = None, cue_out: float | None = None) -> tuple[float, float, float, float]:
    """Return normalized seek/cue metadata without inventing a fake cue-in.

    Rules:
    - cue_in stays 0.0 unless a real seek position or DB cue-in exists
    - cue_out falls back to a safe effective track end when a total length is known
    - wb_orig_total keeps the full known track duration
    - wb_seek_base follows the effective cue_in and stays 0.0 for normal non-cued tracks
    """
    try:
        total = float(total_length or 0.0)
    except Exception:
        total = 0.0
    try:
        ci = float(cue_in) if cue_in is not None else 0.0
    except Exception:
        ci = 0.0
    try:
        co = float(cue_out) if cue_out is not None else 0.0
    except Exception:
        co = 0.0

    ci = max(0.0, ci)

    if total <= 0.0:
        co = max(ci, co)
        return ci, co, total, ci

    if ci >= total:
        ci = max(0.0, total - 1.0)

    fallback_out = max(ci + 1.0, total - 3.0)
    if fallback_out > total:
        fallback_out = total
    if fallback_out <= ci:
        fallback_out = min(total, ci + 1.0)

    if co <= ci:
        co = fallback_out
    if co > total:
        co = total
    if co <= ci:
        co = min(total, ci + 1.0)

    wb_orig_total = total
    wb_seek_base = ci
    return ci, co, wb_orig_total, wb_seek_base


def _build_seek_restart_descriptor(prepend_track: dict | None, prepend_station_key: str = "") -> str:
    """Build the annotate line used for a seek restart or technical hold entry."""
    if not prepend_track:
        return ""
    try:
        p0 = str(prepend_track.get("file") or "").strip()
        if not p0:
            return ""
        if p0.startswith("http://") or p0.startswith("https://"):
            return p0

        def _m3u_escape(s: str) -> str:
            return (s or "").replace("\\", "\\\\").replace(chr(34), '\\"')

        a0 = _m3u_escape(str(prepend_track.get("artist") or "").strip())
        t0 = _m3u_escape(str(prepend_track.get("title") or "").strip())
        al0 = _m3u_escape(str(prepend_track.get("album") or "").strip())
        cue_part0 = ""

        if prepend_track.get("_wb_seek_nofade"):
            total0 = 0.0
            try:
                total0 = float(prepend_track.get("cue_duration_seconds") or prepend_track.get("duration") or 0.0)
            except Exception:
                total0 = 0.0
            if total0 <= 0.0:
                try:
                    total0 = float(get_track_total_duration_for_station_path(str(prepend_station_key or prepend_track.get("station_key") or "").strip(), p0) or 0.0)
                except Exception:
                    total0 = 0.0
            ci0 = prepend_track.get("cue_in_seconds")
            co0 = prepend_track.get("cue_out_seconds")
            if prepend_track.get("_wb_seek_hold"):
                # Technical same-track hold: a very short final slice that keeps
                # the legacy request cursor away from the real queue while the
                # normal queue is restored. It must not sound like a repeated track.
                hold_len0 = 0.35
                try:
                    hold_len0 = float(prepend_track.get("_wb_seek_hold_seconds") or hold_len0)
                except Exception:
                    hold_len0 = 0.35
                hold_len0 = max(0.20, min(0.75, hold_len0))
                try:
                    co_for_hold0 = float(prepend_track.get("_wb_seek_hold_cue_out") or co0 or total0 or 0.0)
                except Exception:
                    co_for_hold0 = 0.0
                if co_for_hold0 <= 0.0 and total0 > 0.0:
                    co_for_hold0 = total0
                ci0 = max(0.0, co_for_hold0 - hold_len0)
                co0 = co_for_hold0
            ci_eff0, co_eff0, total_eff0, seek_base0 = _normalize_seek_window(total0, ci0, co0)
            if seek_base0 > 0.0:
                cue_part0 += f',cue_in="{ci_eff0:.3f}",wb_seek_base="{seek_base0:.3f}"'
            if total_eff0 > 0.0:
                cue_part0 += f',wb_orig_total="{total_eff0:.3f}"'
            fade_out0 = None
            try:
                fade_out0 = float(prepend_track.get("fade_out_seconds")) if prepend_track.get("fade_out_seconds") is not None else None
            except Exception:
                fade_out0 = None
            if fade_out0 is None or fade_out0 < 0.0:
                try:
                    db_station_key = str(prepend_station_key or prepend_track.get("station_key") or "").strip()
                    with get_db(db_station_key) as conn:
                        row = conn.execute("SELECT cue_fade_start_seconds, cue_out_seconds FROM tracks WHERE path = ? LIMIT 1", (p0,)).fetchone()
                    if row is not None and row["cue_fade_start_seconds"] is not None and row["cue_out_seconds"] is not None:
                        fade_start = float(row["cue_fade_start_seconds"] or 0.0)
                        fade_end = float(row["cue_out_seconds"] or 0.0)
                        if fade_end > fade_start >= 0.0:
                            fade_out0 = fade_end - fade_start
                except Exception:
                    fade_out0 = None
            # cue_out is the hard handoff point. Do not extend it by
            # overlap/fade; the native transition must begin exactly at the
            # DB/offline cue_out so the opposite deck starts there from cue_in.
            if fade_out0 is None or fade_out0 < 0.0:
                fade_out0 = 0.0
            if co_eff0 > 0.0:
                cue_part0 += f',cue_out="{co_eff0:.3f}"'
            # v2500: never emit fade metadata for A/B local music handoff.
            # The next deck must start immediately at cue_out with no delay.
            qid0 = int(prepend_track.get("queue_id") or 0)
            tid0 = int(prepend_track.get("track_id") or 0)
            skey0 = _m3u_escape(str(prepend_track.get("station_key") or prepend_station_key or "").strip())
            if prepend_track.get("_wb_seek_hold"):
                cue_part0 += f',queue_id="{qid0}",track_id="{tid0}",station_key="{skey0}",wb_seek_hold="1"'
                cue_part0 += ',wb_seek_virtual_player="1"'
            else:
                cue_part0 += f',queue_id="{qid0}",track_id="{tid0}",station_key="{skey0}",wb_seek_restart="1"'
                # Keep seek restarts eligible for the normal end-of-track fade. Earlier
                # older versions added nofade/cross overrides here, which could make
                # the engine perform a hard handoff when the seeked track reached EOF.
                cue_part0 += ',wb_seek_virtual_player="1"'

        return f'annotate:artist="{a0}",title="{t0}",album="{al0}"{cue_part0}:{p0}'
    except Exception:
        return ""


def _ab_native_runtime_timing_metadata(
    path: str,
    row,
    *,
    station_key: str,
    sam_settings: dict,
    escape,
) -> str:
    """Build the canonical v5082 descriptor contract.

    Automatic cue/audio-end values are intentionally not read from the tracks
    table.  Local media is analysed by the native PCM worker on every load.
    Scripted clean-transition rows remain explicit manual overrides.
    """
    def _row_float(name: str, default: float = 0.0) -> float:
        try:
            return float(row[name]) if name in row.keys() and row[name] is not None else float(default)
        except Exception:
            return float(default)

    try:
        manual_clean = int(row["clean_transition"]) if "clean_transition" in row.keys() else 0
        script_clean = int(row["script_clean_transition"]) if "script_clean_transition" in row.keys() else 0
        manual_timing = bool(manual_clean or script_clean)
    except Exception:
        manual_timing = False

    try:
        total = float(get_track_total_duration_for_station_path(str(station_key or "").strip(), path) or 0.0)
    except Exception:
        total = 0.0
    total = max(0.0, total)

    if manual_timing:
        audio_start = max(0.0, _row_float("audio_start_seconds"))
        audio_end = max(0.0, _row_float("audio_end_seconds"))
        cue_in = max(0.0, _row_float("cue_in_seconds"))
        cue_out = max(0.0, _row_float("cue_out_seconds"))
        audio_start, audio_end, total, boundary_source = _clean_transition_audio_bounds(
            path, audio_start, audio_end, total
        )
        if audio_end > audio_start:
            cue_in, cue_out = audio_start, audio_end
        cue_in, cue_out, total, seek_base = _normalize_seek_window(total, cue_in, cue_out)
        effective_end = audio_end if audio_end > 0.0 else total
        fields = [
            f'wb_orig_total="{total:.3f}"',
            f'wb_audio_start="{audio_start:.3f}"',
            f'wb_audio_end="{effective_end:.3f}"',
            f'wb_play_start="{cue_in:.3f}"',
            f'wb_seek_base="{seek_base:.3f}"',
            f'cue_in="{cue_in:.3f}"',
            f'cue_out="{cue_out:.3f}"',
            'fade_in="0.000"',
            'fade_out="0.000"',
            f'wb_crossfade_trigger="{cue_out:.3f}"',
            f'wb_effective_end="{effective_end:.3f}"',
            'disable_autocue="1"',
            'wb_native_analyze="0"',
            'wb_manual_timing="1"',
            'wb_cue_source="manual_override"',
            f'wb_audio_boundary_source="{escape(str(boundary_source or "manual_override"))}"',
            'wb_hard_clean_transition="1"',
            'wb_clean_transition="1"',
            'wb_script_clean="1"',
            'nofade="1"',
        ]
        return "," + ",".join(fields)

    fallback = max(0.0, float(sam_settings.get("crossfade_fallback_seconds") or 0.0))
    fade_out = max(0.0, float(sam_settings.get("crossfade_fade_out_seconds") or 0.0))
    provisional_end = total
    provisional_trigger = max(0.0, provisional_end - fallback) if provisional_end > 0.0 else 0.0
    # The provisional values are decoder-safe fallbacks only.  Once analysis_ready
    # is published the native descriptor atomically replaces all four boundaries.
    fields = [
        f'wb_orig_total="{total:.3f}"',
        'wb_audio_start="0.000"',
        f'wb_audio_end="{provisional_end:.3f}"',
        'wb_play_start="0.000"',
        'wb_seek_base="0.000"',
        'cue_in="0.000"',
        f'cue_out="{provisional_trigger:.3f}"',
        'fade_in="0.000"',
        f'fade_out="{fade_out:.3f}"',
        f'wb_crossfade_trigger="{provisional_trigger:.3f}"',
        f'wb_effective_end="{provisional_end:.3f}"',
        'disable_autocue="1"',
        'wb_native_analyze="1"',
        'wb_manual_timing="0"',
        'wb_cue_source="native_runtime_pending"',
        'wb_analysis_window_ms="10"',
        'wb_analysis_sustain_ms="30"',
        'wb_analysis_artifact_max_ms="300"',
        'wb_analysis_artifact_silence_ms="250"',
        f'wb_gap_start_dbfs="{float(sam_settings.get("gap_killer_start_dbfs") or -20.0):.3f}"',
        f'wb_gap_end_dbfs="{float(sam_settings.get("gap_killer_end_dbfs") or -24.0):.3f}"',
        f'wb_crossfade_trigger_relative_db="{float(sam_settings.get("crossfade_trigger_relative_db") or -7.0):.3f}"',
        f'wb_crossfade_fallback="{fallback:.3f}"',
        f'wb_crossfade_min="{max(0.0, float(sam_settings.get("crossfade_min_seconds") or 0.0)):.3f}"',
        f'wb_crossfade_max="{max(0.0, float(sam_settings.get("crossfade_max_seconds") or 0.0)):.3f}"',
        f'wb_no_crossfade_max_duration="{max(0.0, float(sam_settings.get("no_crossfade_max_duration_sec") or 0.0)):.3f}"',
    ]
    return "," + ",".join(fields)


def _build_station_queue_plan(
    station_key: str,
    *,
    prepend_track: dict | None = None,
    skip_queue_id: int = 0,
    skip_track_id: int = 0,
    skip_path: str = "",
) -> list[str]:
    """Build the native A/B queue plan directly from SQLite.

    Native playback consumes descriptors in memory. The database queue is the
    only persisted source of truth; no runtime playlist file is written.
    """
    sk = str(station_key or get_active_station_key() or "").strip()
    if not sk:
        return []

    conn = get_db_for_station(sk)
    try:
        conn.row_factory = sqlite3.Row
        sam_settings = _ab_sam_settings_from_row(_ab_get_settings_from_conn(conn))
        rows = conn.execute(
            """
            SELECT
                queue_items.id AS queue_id,
                queue_items.track_id AS track_id,
                COALESCE(queue_items.clean_transition, 0) AS clean_transition,
                COALESCE(queue_items.script_clean_transition, 0) AS script_clean_transition,
                tracks.path AS path,
                tracks.filename AS filename,
                tracks.cue_in_seconds AS cue_in_seconds,
                tracks.cue_out_seconds AS cue_out_seconds,
                tracks.cue_duration_seconds AS cue_duration_seconds,
                tracks.cue_fade_start_seconds AS cue_fade_start_seconds,
                tracks.audio_start_seconds AS audio_start_seconds,
                tracks.audio_end_seconds AS audio_end_seconds
            FROM queue_items
            JOIN tracks ON tracks.id = queue_items.track_id
            GROUP BY
                queue_items.id,
                queue_items.position,
                queue_items.track_id,
                queue_items.clean_transition,
                queue_items.script_clean_transition,
                tracks.path,
                tracks.filename,
                tracks.cue_in_seconds,
                tracks.cue_out_seconds,
                tracks.cue_duration_seconds,
                tracks.cue_fade_start_seconds,
                tracks.audio_start_seconds,
                tracks.audio_end_seconds
            ORDER BY queue_items.position ASC, queue_items.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    def _escape(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace('"', '\\"')

    try:
        skip_queue_id = int(skip_queue_id or 0)
    except Exception:
        skip_queue_id = 0
    try:
        skip_track_id = int(skip_track_id or 0)
    except Exception:
        skip_track_id = 0
    skip_path_norm = normalize_media_path(str(skip_path or "").strip())
    skipped_current_once = False

    plan: list[str] = []
    if prepend_track:
        descriptor = _build_seek_restart_descriptor(prepend_track, sk)
        if descriptor:
            plan.append(descriptor)
            if bool((prepend_track or {}).get("_wb_seek_cursor_guard")):
                plan.append(descriptor)

    for row in rows:
        raw_path = (
            (row["path"] if "path" in row.keys() else None)
            or (row["filename"] if "filename" in row.keys() else None)
            or ""
        )
        media_path = str(raw_path).strip()
        if not media_path:
            continue

        if not skipped_current_once:
            try:
                row_queue_id = int(row["queue_id"] or 0)
            except Exception:
                row_queue_id = 0
            try:
                row_track_id = int(row["track_id"] or 0)
            except Exception:
                row_track_id = 0
            row_path_norm = normalize_media_path(media_path)
            if (
                (skip_queue_id and row_queue_id == skip_queue_id)
                or (skip_track_id and row_track_id == skip_track_id)
                or (skip_path_norm and row_path_norm == skip_path_norm)
            ):
                skipped_current_once = True
                continue

        if media_path.startswith("URL:"):
            parts = media_path.split(":", 2)
            if len(parts) >= 3 and parts[2].strip():
                try:
                    stream_duration = max(0, int(float(parts[1].strip() or "0")))
                except Exception:
                    stream_duration = 0
                try:
                    queue_id = int(row["queue_id"] or 0)
                except Exception:
                    queue_id = 0
                try:
                    track_id = int(row["track_id"] or 0)
                except Exception:
                    track_id = 0
                descriptor = _ab_build_native_stream_descriptor(
                    parts[2].strip(), stream_duration,
                    queue_id=queue_id, track_id=track_id, station_key=sk,
                )
                if descriptor:
                    plan.append(descriptor)
            continue

        if media_path.startswith("http://") or media_path.startswith("https://"):
            try:
                queue_id = int(row["queue_id"] or 0)
            except Exception:
                queue_id = 0
            try:
                track_id = int(row["track_id"] or 0)
            except Exception:
                track_id = 0
            descriptor = _ab_build_native_stream_descriptor(
                media_path, 0, queue_id=queue_id, track_id=track_id, station_key=sk,
            )
            if descriptor:
                plan.append(descriptor)
            continue

        logical = re.sub(
            r"\s+",
            " ",
            os.path.splitext(os.path.basename(media_path))[0],
        ).strip()
        artist = ""
        title = logical
        if " - " in logical:
            artist, title = [part.strip() for part in logical.split(" - ", 1)]

        metadata = read_media_metadata(media_path)
        year = _normalize_year_metadata(metadata.get("year"))
        cue_part = _ab_native_runtime_timing_metadata(
            media_path,
            row,
            station_key=sk,
            sam_settings=sam_settings,
            escape=_escape,
        )
        try:
            queue_id = int(row["queue_id"] or 0)
        except Exception:
            queue_id = 0
        try:
            track_id = int(row["track_id"] or 0)
        except Exception:
            track_id = 0
        runtime_meta = (
            f',queue_id="{queue_id}",track_id="{track_id}",'
            f'station_key="{_escape(sk)}"'
        )
        year_part = f',year="{_escape(year)}"' if year else ""
        plan.append(
            f'annotate:artist="{_escape(artist)}",title="{_escape(title)}"'
            f'{year_part}{cue_part}{runtime_meta}:{media_path}'
        )

    return plan


def _format_track_clock(total_seconds: float | int | None) -> str:
    """Format seconds as M:SS or H:MM:SS for UI duration display."""
    try:
        total = int(round(float(total_seconds or 0.0)))
    except Exception:
        total = 0
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def get_track_total_duration_for_station_path(station_key: str, path: str) -> float:
    """Return the best known full-track duration for a station track path."""
    norm_path = normalize_media_path(str(path or "").strip())
    if not norm_path:
        return 0.0
    conn = None
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            """
            SELECT cue_duration_seconds, cue_out_seconds
            FROM tracks
            WHERE path = ? OR filename = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (norm_path, norm_path),
        )
        row = c.fetchone()
        if row:
            try:
                db_duration = float(row["cue_duration_seconds"] or 0.0)
            except Exception:
                db_duration = 0.0
            if db_duration <= 0.0:
                try:
                    db_duration = float(row["cue_out_seconds"] or 0.0)
                except Exception:
                    db_duration = 0.0
            if db_duration > 0.0:
                return float(db_duration)
    except Exception:
        pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    try:
        probed = float(probe_duration_seconds(norm_path) or 0.0)
        if probed > 0.0:
            return probed
    except Exception:
        pass
    return 0.0


def _ab_clamp_audio_boundary_values(audio_start: float, audio_end: float, duration: float) -> tuple[float, float, float]:
    """Clamp audio boundary values to a known local-file duration."""
    try:
        start = max(0.0, float(audio_start or 0.0))
    except Exception:
        start = 0.0
    try:
        end = max(0.0, float(audio_end or 0.0))
    except Exception:
        end = 0.0
    try:
        total = max(0.0, float(duration or 0.0))
    except Exception:
        total = 0.0
    if total > 0.0:
        start = min(start, total)
        if end <= start or end > total + 0.250:
            end = total
        else:
            end = max(start, min(end, total))
    else:
        end = max(start, end)
    return start, end, total


def _clean_transition_audio_bounds(path: str, audio_start: float, audio_end: float, fallback_total: float = 0.0) -> tuple[float, float, float, str]:
    """Return explicit DB boundaries for a manual no-overlap item.

    Automatic silence/cue analysis belongs exclusively to the native PCM
    analyzer. Python only validates a stored manual boundary and clamps it to
    the Mutagen duration. When no valid manual boundary exists, the complete
    local file is used without starting a decoder or background analyzer.
    """
    try:
        start = max(0.0, float(audio_start or 0.0))
    except Exception:
        start = 0.0
    try:
        end = max(0.0, float(audio_end or 0.0))
    except Exception:
        end = 0.0
    try:
        total = max(0.0, float(fallback_total or 0.0))
    except Exception:
        total = 0.0

    norm_path = normalize_media_path(str(path or "").strip())
    try:
        if norm_path and os.path.isfile(norm_path):
            probed = max(0.0, float(probe_duration_seconds(norm_path) or 0.0))
            if probed > 0.0:
                total = probed
    except Exception:
        pass

    start, end, total = _ab_clamp_audio_boundary_values(start, end, total)
    if not (end > start):
        start = 0.0
        end = total
        source = "manual_full_file_fallback"
    else:
        source = "manual_db_audio_boundary"
    return start, end, max(0.0, total), source


def dequeue_song_to_history(
    song: Optional[dict] = None,
    *,
    sync_and_reload: bool = True,
    station_key: Optional[str] = None,
) -> bool:
    """Atomically commit one audible queue item through the storage repository."""
    song = song or {}
    sk = str(station_key or "").strip()
    if not sk:
        return False

    try:
        result = _get_playback_repository().commit_started_track(song, station_key=sk)
        if not result.committed:
            event_by_reason = {
                "empty_queue": "dequeue_empty_queue",
                "no_head_fallback": "dequeue_no_match_no_head_fallback",
                "already_committed": "dequeue_skip_already_committed",
            }
            return False
        _publish_ui_queue_history_changed(sk, "track_started")

        if sync_and_reload:
            try:
                _ab_schedule_async_replan("history_dequeue_db_queue_refresh")
            except Exception:
                pass
        return True
    except Exception as exc:
        try:
            logger.exception("[Queue] Failed to dequeue song to history: %s", exc)
        except Exception:
            pass
        return False


# Progress freeze state (so UI progress bar stops while paused)
# IMPORTANT: multi-station safe (keep progress state separated per station).
PROGRESS_LOCK = threading.Lock()
PROGRESS_STATE_BY_STATION = {}

def _get_progress_state(station_key: str):
    k = (station_key or "default").strip() or "default"
    st = PROGRESS_STATE_BY_STATION.get(k)
    if st is None:
        st = {
            "last_elapsed": 0.0,
            "last_duration": 0.0,
            # Offset applied to the native source clock to account for paused time.
            "elapsed_offset": 0.0,
            "paused": False,
            "paused_raw_elapsed": 0.0,
            # Track path that owns the current elapsed_offset. This prevents a
            # seek-derived offset from leaking into the next natural track during
            # crossfade before progress fully settles.
            "offset_track_path": "",
            # Short guard window after a real track change so stale source
            # progress from the previous track cannot leak into the new UI timer
            # during overlap/crossfade.
            "recent_track_path": "",
            "recent_track_started_at": 0.0,
            "recent_track_cue_base": 0.0,
            # Raw live source elapsed from the native daemon. This detects a real
            # track clock reset (<0.5s after previously advancing), which is a more
            # reliable UI timing boundary than delayed metadata during crossfade.
            "last_source_elapsed": 0.0,
            # Last raw UI clock/file snapshot from native status.
            "last_ui_raw_elapsed": 0.0,
            "last_live_file": "",
            # Hold a seeked track at its visible end until native lifecycle confirms the
            # next real track metadata. This prevents the UI clock from jumping
            # back to the seek position while old metadata is still being reported.
            "seek_end_hold": False,
            "seek_end_hold_path": "",
            "seek_end_hold_queue_id": 0,
            "seek_end_hold_track_id": 0,
            "virtual_next_started_at": 0.0,
            "virtual_next_from_path": "",
            "logical_track_instance_id": 0,
            "last_seen_logical_track_id": 0,
            "last_seen_display_track_logical_id": 0,
            "last_seen_now_playing_path": "",
        }
        PROGRESS_STATE_BY_STATION[k] = st
    return st


def _mark_progress_track_start(
    station_key: str,
    path: str,
    cue_base: float | None = None,
    started_at: float | None = None,
    logical_track_id: int | None = None,
    display_track_logical_id: int | None = None,
    duration: float | None = None,
) -> None:
    sk = (station_key or "").strip()
    ts = float(started_at or time.time())
    path_norm = normalize_media_path(str(path or ""))
    logical_id = int(logical_track_id or 0)
    display_id = int(display_track_logical_id or logical_id or 0)
    duration_val = max(0.0, float(duration or 0.0))

    # A real track change must always start the UI timer from the audible start of
    # the new track. Do not carry any cue/seek offset into the next file, even if
    # the source briefly reports stale progress during overlap/crossfade.
    with PROGRESS_LOCK:
        ps = _get_progress_state(sk)
        ps["paused"] = False
        ps["paused_raw_elapsed"] = 0.0
        ps["recent_track_path"] = str(path or "")
        ps["recent_track_started_at"] = ts
        ps["recent_track_cue_base"] = 0.0
        ps["elapsed_offset"] = 0.0
        ps["offset_track_path"] = ""
        ps["last_source_elapsed"] = 0.0
        ps["last_ui_raw_elapsed"] = 0.0
        ps["last_live_file"] = str(path or "")
        ps["seek_end_hold"] = False
        ps["seek_end_hold_path"] = ""
        ps["seek_end_hold_queue_id"] = 0
        ps["seek_end_hold_track_id"] = 0
        ps["virtual_next_started_at"] = 0.0
        ps["virtual_next_from_path"] = ""
        ps["last_elapsed"] = 0.0
        ps["last_duration"] = duration_val
        ps["last_seen_logical_track_id"] = logical_id
        ps["last_seen_display_track_logical_id"] = display_id
        ps["last_seen_now_playing_path"] = path_norm


RADIO_STATE_LOCK = threading.Lock()
RADIO_STATE = {"paused": False, "stopped": False}


def probe_duration_seconds(path: str) -> float | None:
    """Fast duration probe using Mutagen (no ffmpeg)."""
    try:
        audio = MutagenFile(path)
        if audio is None:
            return None
        info = getattr(audio, "info", None)
        if info is None:
            return None
        length = getattr(info, "length", None)
        if length is None:
            return None
        v = float(length)
        return v if v > 0 else None
    except Exception:
        return None


def get_station_name_raw() -> str:
    """Return the station name (radio_name) from DB settings or empty string."""
    try:
        settings = get_settings()
        if settings and "radio_name" in settings.keys() and settings["radio_name"]:
            return str(settings["radio_name"]).strip()
    except Exception:
        pass
    return ""


def get_station_instance_id() -> str:
    """Return the stable runtime instance identifier for the active station.

    It must be unique per station. We derive it from the active station DB filename
    (e.g. 'Teszt1.db' -> 'Teszt1'), not from radio_name (which can be non-unique).
    """
    try:
        sid = os.path.basename(get_active_station_db_path() or "").strip()
        if sid.endswith(".db"):
            sid = sid[:-3]
        sid = re.sub(r"[^A-Za-z0-9_-]+", "_", sid).strip("_")
        if sid:
            return sid
    except Exception:
        pass
    # Fallback: use radio_name, sanitized
    try:
        name = str(get_station_name_raw()).strip()
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
        if name:
            return name
    except Exception:
        pass
    return "station"


def _sanitize_station_runtime_name(name: str) -> str:
    """Return a filesystem-safe runtime station name from a display label."""
    try:
        import unicodedata
        ascii_name = unicodedata.normalize("NFKD", str(name or "").strip()).encode("ascii", "ignore").decode("ascii")
    except Exception:
        ascii_name = str(name or "").strip()
    ascii_name = re.sub(r"\s+", "_", ascii_name)
    ascii_name = re.sub(r"[^A-Za-z0-9_-]+", "", ascii_name)
    ascii_name = re.sub(r"_+", "_", ascii_name)
    return ascii_name.strip("_-")


def get_station_name_safe_for_station(station_key: str) -> str:
    """Return the runtime-safe station suffix for an explicit station DB."""
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        try:
            c = conn.cursor()
            c.execute("SELECT radio_name FROM settings ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            if row is not None:
                try:
                    raw_name = row["radio_name"]
                except Exception:
                    raw_name = row[0]
                safe = _sanitize_station_runtime_name(raw_name or "")
                if safe:
                    return safe
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception:
        pass
    try:
        sid = os.path.basename(str(station_key or "").strip())
        if sid.endswith(".db"):
            sid = sid[:-3]
        sid = re.sub(r"^db-", "", sid, flags=re.IGNORECASE)
        sid = re.sub(r"[^A-Za-z0-9_-]+", "_", sid).strip("_")
        if sid:
            return sid
    except Exception:
        pass
    return "station"



_AB_PLAYER_LOCK = threading.RLock()
_AB_PLAYER_DEFAULT_STATE = {
    "enabled": True,
    "station_key": "",
    "active": "a",
    "lines": [],
    "durations": [],
    "fadeouts": [],
    "current_index": 0,
    "next_index": 1,
    "player_index": {"a": 0, "b": 1},
    "started_at": 0.0,
    "transitioning": False,
    "transition_started_at": 0.0,
    "transition_duration": 0.0,
    "transition_not_before": 0.0,
    "transition_target": "",
    "transition_from": "",
    "pending_cueout_transition": False,
    "pending_cueout_deadline": 0.0,
    "pending_cueout_active": "",
    "pending_cueout_target": "",
    "pending_cueout_current_index": -1,
    "pending_cueout_target_index": -1,
    "pending_cueout_fade": 0.0,
    "pending_cueout_generation": 0,
    "pending_cueout_token": 0,
    "pending_cueout_reason": "",
    "seek_pending": False,
    "seek_pending_active": "",
    "seek_pending_queue_id": 0,
    "seek_pending_slot_token": "",
    "seek_pending_deadline": 0.0,
    "seek_applied_at": 0.0,
    "seek_applied_source_position": 0.0,
    "hard_handoff_armed": False,
    "hard_handoff_active": "",
    "hard_handoff_target": "",
    "hard_handoff_current_index": -1,
    "hard_handoff_target_index": -1,
    "hard_handoff_generation": 0,
    "hard_handoff_station_key": "",
    "hard_handoff_from_queue_id": 0,
    "hard_handoff_from_slot_token": "",
    "hard_handoff_to_queue_id": 0,
    "hard_handoff_to_slot_token": "",
    "hard_handoff_native_claimed": False,
    "hard_handoff_completed": False,
    "hard_handoff_completion_source": "",
    "hard_handoff_deadline": 0.0,
    "generation": 0,
    "last_load_error": "",
    # A/B source generation currently loaded into each native deck slot.
    # This is stricter than player_index/path: a stale queued request can have
    # the same path but an old wb_ab_generation and must not validate as live.
    "player_generation": {},
    # v2439: transition target tracks are made authoritative at transition start.
    # Track which target has already been seeded so transition completion and
    # delayed lifecycle callbacks cannot restart the UI/progress timeline.
    "last_seed_key": "",
    "last_commit_key": "",
}
_AB_PLAYER_STATES: dict[str, dict] = {}


def _ab_runtime_station_key() -> str:
    key = _station_runtime_override()
    if not key:
        try:
            key = str(get_active_station_key() or "").strip()
        except Exception:
            key = ""
    return os.path.basename(key) if key else "__default__"


class _StationScopedABState(MutableMapping):
    """Route shared A/B state access to the current station context."""

    def _state(self) -> dict:
        key = _ab_runtime_station_key()
        with _AB_PLAYER_LOCK:
            state = _AB_PLAYER_STATES.get(key)
            if state is None:
                state = copy.deepcopy(_AB_PLAYER_DEFAULT_STATE)
                state["station_key"] = "" if key == "__default__" else key
                _AB_PLAYER_STATES[key] = state
            return state

    def __getitem__(self, key):
        return self._state()[key]

    def __setitem__(self, key, value):
        self._state()[key] = value

    def __delitem__(self, key):
        del self._state()[key]

    def __iter__(self):
        return iter(tuple(self._state().keys()))

    def __len__(self):
        return len(self._state())

    def clear(self):
        state = self._state()
        state.clear()

    def copy(self):
        return dict(self._state())


_AB_PLAYER_STATE = _StationScopedABState()

# v2802: queue replan invalidation serial.  Queue mutations can overlap with
# slow native deck loads. A replan that started from
# an older queue snapshot must not load its stale inactive deck after a newer
# queue mutation or scripted time/spot break has already changed the queue head.
_AB_REPLAN_LOCK = threading.RLock()
_AB_REPLAN_SERIALS: dict[str, int] = {}


def _ab_invalidate_pending_replans(reason: str = "") -> int:
    station = _ab_runtime_station_key()
    try:
        with _AB_REPLAN_LOCK:
            serial = int(_AB_REPLAN_SERIALS.get(station) or 0) + 1
            _AB_REPLAN_SERIALS[station] = serial
        return serial
    except Exception:
        return 0


def _ab_begin_replan_serial(reason: str = "") -> int:
    return _ab_invalidate_pending_replans(reason=f"begin:{reason or 'queue_mutation'}")


def _ab_is_replan_serial_current(serial: int) -> bool:
    station = _ab_runtime_station_key()
    try:
        with _AB_REPLAN_LOCK:
            return int(serial or 0) == int(_AB_REPLAN_SERIALS.get(station) or 0)
    except Exception:
        return False


def _ab_abort_stale_replan_if_needed(serial: int, reason: str, stage: str) -> bool:
    if _ab_is_replan_serial_current(serial):
        return False
    try:
        with _AB_REPLAN_LOCK:
            live_serial = int(_AB_REPLAN_SERIALS.get(_ab_runtime_station_key()) or 0)
    except Exception:
        live_serial = 0
    return True


_AB_MONITOR_STOP = threading.Event()

# Native terminal events must be able to interrupt the adaptive no-crossfade
# wait.  A station-scoped serial/condition pair avoids polling and cannot lose
# a wake that arrives just before the monitor begins waiting.
_AB_MONITOR_WAKE_CONDITION = threading.Condition(threading.RLock())
_AB_MONITOR_WAKE_SERIALS: dict[str, int] = {}
_AB_MONITOR_WAKE_REASONS: dict[str, str] = {}


def _ab_monitor_wake_key(station_key: str = "") -> str:
    value = os.path.basename(str(station_key or "").strip())
    if not value:
        value = _ab_runtime_station_key()
    return value or "__default__"


def _ab_monitor_wake_snapshot(station_key: str = "") -> int:
    key = _ab_monitor_wake_key(station_key)
    with _AB_MONITOR_WAKE_CONDITION:
        return int(_AB_MONITOR_WAKE_SERIALS.get(key) or 0)


def _ab_signal_monitor_wake(station_key: str = "", *, reason: str = "") -> int:
    """Wake one station monitor without doing audio work on the socket reader."""
    key = _ab_monitor_wake_key(station_key)
    with _AB_MONITOR_WAKE_CONDITION:
        serial = int(_AB_MONITOR_WAKE_SERIALS.get(key) or 0) + 1
        _AB_MONITOR_WAKE_SERIALS[key] = serial
        _AB_MONITOR_WAKE_REASONS[key] = str(reason or "native_event")
        _AB_MONITOR_WAKE_CONDITION.notify_all()
        return serial


def _ab_wait_monitor_interruptible(
    station_key: str,
    observed_serial: int,
    timeout_seconds: float,
) -> tuple[int, bool, str]:
    """Wait for the next monitor tick or a native event, whichever comes first."""
    key = _ab_monitor_wake_key(station_key)
    timeout = max(0.001, float(timeout_seconds or 0.10))
    with _AB_MONITOR_WAKE_CONDITION:
        current = int(_AB_MONITOR_WAKE_SERIALS.get(key) or 0)
        if current == int(observed_serial or 0):
            _AB_MONITOR_WAKE_CONDITION.wait(timeout=timeout)
            current = int(_AB_MONITOR_WAKE_SERIALS.get(key) or 0)
        woke = current != int(observed_serial or 0)
        reason = str(_AB_MONITOR_WAKE_REASONS.get(key) or "") if woke else ""
        return current, woke, reason



# SAM-style Gap Killer and one-sided crossfade defaults.  v2859 stores the
# active values per station in the settings table; these constants remain the
# Native timing defaults.
_AB_SAM_GAP_START_THRESHOLD_DBFS = -20.0
_AB_SAM_GAP_END_THRESHOLD_DBFS = -24.0
_AB_SAM_CROSSFADE_TRIGGER_OFFSET_DB = 7.0
_AB_SAM_CROSSFADE_FALLBACK_SEC = 3.0
_AB_SAM_CROSSFADE_MIN_SEC = 0.100
_AB_SAM_CROSSFADE_MAX_SEC = 6.0
_AB_SAM_FADE_OUT_SEC = 5.0
_AB_SAM_NO_CROSSFADE_MAX_DURATION_SEC = 65.0
_AB_FIXED_OVERLAP_SECONDS = 2.0


# Short items need immediate lookahead because a recycled A/B deck may have only
# a few seconds to become ready after the previous deck is released.
_AB_SHORT_ITEM_PRELOAD_SECONDS = 15.0
_AB_NORMAL_INACTIVE_PRELOAD_DELAY = 5.0
_AB_URGENT_BUSY_RETRY_DELAY = 0.20
_AB_URGENT_BUSY_RETRY_WINDOW = 12.0


_META_RE = re.compile(r'([A-Za-z0-9_]+)="([^"]*)"')


def _annotate_escape_value(value: object) -> str:
    """Escape a value for the canonical annotate: key="value" descriptor field."""
    try:
        v = str(value or "")
    except Exception:
        v = ""
    return v.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", " ").replace("\r", " ")


def _split_annotate_uri(uri: str) -> tuple[dict, str] | None:
    """Return (metadata, path), preserving URI schemes containing colons."""
    u = str(uri or "").strip()
    if not u.startswith("annotate:"):
        return None
    try:
        body = u[len("annotate:"):]
        in_quotes = False
        escaped = False
        separator = -1
        for index, char in enumerate(body):
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_quotes:
                escaped = True
                continue
            if char == '"':
                in_quotes = not in_quotes
                continue
            if char == ":" and not in_quotes:
                separator = index
                break
        if separator < 0:
            return None
        meta_head = body[:separator]
        tail = body[separator + 1:]
        meta = {m.group(1): m.group(2) for m in _META_RE.finditer(meta_head)}
        return meta, tail
    except Exception:
        return None


def _build_annotate_uri(meta: dict, path: str) -> str:
    """Build a canonical annotate URI from parsed metadata and a path."""
    parts = []
    for k, v in (meta or {}).items():
        kk = str(k or "").strip()
        if not kk or not re.match(r"^[A-Za-z0-9_]+$", kk):
            continue
        parts.append(f'{kk}="{_annotate_escape_value(v)}"')
    return "annotate:" + ",".join(parts) + ":" + str(path or "")


def _ab_build_native_stream_descriptor(
    url: str,
    duration_seconds: int | float,
    *,
    queue_id: int = 0,
    track_id: int = 0,
    station_key: str = "",
    title: str = "Streaming",
    artist: str = "",
) -> str:
    """Return one native-deck descriptor for an HTTP/HTTPS radio source.

    Timed streams use the configured duration as a manual PCM-clock boundary.
    A zero duration means an infinite stream: native analysis and automatic
    transition scheduling are disabled until manual Next or Stop.
    """
    stream_url = str(url or "").strip()
    if not (stream_url.startswith("http://") or stream_url.startswith("https://")):
        return ""
    try:
        duration = max(0, int(float(duration_seconds or 0)))
    except Exception:
        duration = 0
    timed = duration > 0
    boundary = str(duration if timed else 0)
    meta = {
        "queue_id": str(int(queue_id or 0)),
        "track_id": str(int(track_id or 0)),
        "station_key": str(station_key or ""),
        "artist": str(artist or ""),
        "title": str(title or "Streaming"),
        "webradio_url": stream_url,
        "webradio_dur": boundary,
        "wb_source_type": "stream",
        "wb_stream_source": "1",
        "wb_stream_infinite": "0" if timed else "1",
        "wb_stream_duration": boundary,
        "wb_native_analyze": "0",
        "wb_manual_timing": "1",
        "wb_play_start": "0",
        "wb_seek_base": "0",
        "wb_audio_start": "0",
        "wb_audio_end": boundary,
        "wb_crossfade_trigger": boundary,
        "wb_effective_end": boundary,
        "wb_orig_total": boundary,
        "cue_in": "0",
        "cue_out": boundary,
        "fade_in": "0",
        "fade_out": "0",
        "disable_autocue": "1",
        "wb_cue_source": "native_stream_duration" if timed else "native_stream_infinite",
        "wb_hard_clean_transition": "1",
        "wb_clean_transition": "1",
        "nofade": "1",
    }
    return _build_annotate_uri(meta, stream_url)


def _ab_enforce_fade_tail_window(
    *,
    play_start: float,
    crossfade_trigger: float,
    effective_end: float,
    fade_duration: float,
    source_end: float = 0.0,
) -> dict:
    """Guarantee decodable outgoing audio for the complete fade window.

    ``wb_crossfade_trigger`` is the instant at which the target deck starts,
    while ``wb_effective_end``/``wb_orig_total`` bound the outgoing decoder.
    A trigger at the terminal boundary makes a configured fade purely nominal:
    the decoder reaches EOF before the mixer can lower its gain.  Preserve any
    earlier analysis trigger, but move a too-late trigger to ``end - fade``.
    """
    def _value(raw) -> float:
        try:
            return max(0.0, float(raw or 0.0))
        except Exception:
            return 0.0

    start = _value(play_start)
    trigger = _value(crossfade_trigger)
    end = _value(effective_end)
    total = _value(source_end)
    fade = _value(fade_duration)

    if end <= start and total > start:
        end = total
    if total > 0.0:
        start = min(start, total)
        if end > 0.0:
            end = min(end, total)
    if end <= start:
        return {
            "play_start": start,
            "crossfade_trigger": max(start, trigger),
            "effective_end": end,
            "fade_duration": 0.0,
            "guarded": False,
        }

    available = max(0.0, end - start)
    fade = min(fade, available)
    if trigger <= 0.0:
        trigger = end
    trigger = min(max(start, trigger), end)
    latest_trigger = max(start, end - fade) if fade > 0.0 else end
    guarded = bool(fade > 0.0 and trigger > latest_trigger + 0.0005)
    if guarded:
        trigger = latest_trigger
    return {
        "play_start": start,
        "crossfade_trigger": trigger,
        "effective_end": end,
        "fade_duration": fade,
        "guarded": guarded,
        "latest_trigger": latest_trigger,
        "available_tail": max(0.0, end - trigger),
    }


def _ab_sanitize_timing_metadata(meta: dict) -> dict:
    """Return one canonical native A/B timing descriptor.

    cue_in/cue_out/fade_in/fade_out are the public timing contract. Private
    wb_* keys are retained only for computed playback boundaries and source
    limits used by the A/B scheduler. Retired timing aliases are scrubbed but
    are no longer interpreted as descriptor input.
    """
    src = dict(meta or {})

    def _first_float(*keys: str) -> float:
        for key in keys:
            try:
                raw = str(src.get(key, "") or "").strip()
                if raw:
                    return max(0.0, float(raw))
            except Exception:
                pass
        return 0.0

    play_start = _first_float(
        "cue_in", "wb_play_start", "wb_seek_base", "audio_start",
        "wb_audio_start",
    )
    crossfade_trigger = _first_float(
        "cue_out", "wb_crossfade_trigger",
    )
    fade_in = _first_float("fade_in")
    effective_end = _first_float(
        "wb_effective_end", "wb_audio_end", "audio_end",
    )
    fade_duration = _first_float("fade_out")
    total = _first_float("wb_orig_total", "duration")

    timing = _ab_enforce_fade_tail_window(
        play_start=play_start,
        crossfade_trigger=crossfade_trigger,
        effective_end=effective_end,
        fade_duration=fade_duration,
        source_end=total,
    )
    play_start = float(timing["play_start"])
    crossfade_trigger = float(timing["crossfade_trigger"])
    effective_end = float(timing["effective_end"])
    fade_duration = float(timing["fade_duration"])
    if bool(timing.get("guarded")):
        src["wb_fade_tail_guard"] = "1"

    # Remove every conflicting timing key, including retired aliases, then
    # restore one canonical cue/fade set plus the private boundary fields.
    for key in (
        "cue_in", "cue_out", "fade_in", "fade_out",
        "wb_fade_duration", "wb_fade_out",
        "liq_cue_in", "liq_cue_out", "liq_fade_in", "liq_fade_out",
        "liq_cross_start_duration", "liq_cross_end_duration",
        "liq_cross_max_start_duration",
        "disable_autocue", "liq_disable_autocue",
        "audio_start", "audio_end", "duration",
    ):
        src.pop(key, None)

    src["wb_play_start"] = f"{play_start:.3f}"
    src["wb_seek_base"] = f"{play_start:.3f}"
    # The canonical cue starts at the same Gap Killer position as the private
    # scheduler boundary, so physical playback and the logical clock agree.
    src["cue_in"] = f"{play_start:.3f}"
    src["cue_out"] = f"{crossfade_trigger:.3f}"
    src["fade_in"] = f"{fade_in:.3f}"
    src["fade_out"] = f"{fade_duration:.3f}"
    src["wb_crossfade_trigger"] = f"{crossfade_trigger:.3f}"
    src["wb_effective_end"] = f"{effective_end:.3f}"
    if total > 0.0:
        src["wb_orig_total"] = f"{total:.3f}"
    src["disable_autocue"] = "1"
    return src

def _parse_annotate_meta(uri: str) -> dict:
    uri = str(uri or "")
    if not uri.startswith("annotate:"):
        return {}
    split = _split_annotate_uri(uri)
    return dict(split[0]) if split else {}

def _ab_line_path(uri: str) -> str:
    """Return the local path or complete HTTP/HTTPS URL from a request URI."""
    try:
        u = str(uri or "").strip()
        if not u:
            return ""
        if u.startswith("annotate:"):
            split = _split_annotate_uri(u)
            if split:
                return normalize_media_path(split[1])
        return normalize_media_path(u)
    except Exception:
        return str(uri or "").strip()


def _ab_line_info(uri: str) -> dict:
    """Parse metadata and timing from an A/B request URI for UI/status use."""
    meta = _parse_annotate_meta(uri)
    path = _ab_line_path(uri)

    def _f(*keys, default=0.0):
        for k in keys:
            try:
                v = str(meta.get(k, "") or "").strip()
                if v:
                    return float(v)
            except Exception:
                pass
        return float(default)

    # v2480: UI/status can display duration, but cue points must come only
    # from canonical descriptor metadata. Never synthesize cue_out from duration.
    cue_in = _f("cue_in", "wb_play_start", "wb_seek_base", "audio_start", "wb_audio_start", default=0.0)
    cue_out = _f("cue_out", "wb_crossfade_trigger", default=0.0)
    fade_in = _f("fade_in", default=0.0)
    fade_out = _f("fade_out", default=0.0)
    orig_total = _f("wb_orig_total", "duration", default=0.0)
    audio_start = _f("wb_audio_start", "audio_start", "wb_play_start", default=0.0)
    audio_end = _f("wb_effective_end", "wb_audio_end", "audio_end", default=0.0)
    fade_window = _ab_enforce_fade_tail_window(
        play_start=cue_in or audio_start,
        crossfade_trigger=cue_out,
        effective_end=audio_end,
        fade_duration=fade_out,
        source_end=orig_total,
    )
    cue_out = float(fade_window["crossfade_trigger"])
    audio_end = float(fade_window["effective_end"])
    fade_out = float(fade_window["fade_duration"])
    segment_duration = max(0.0, cue_out - cue_in) if cue_out > cue_in else max(0.0, orig_total - cue_in)
    if orig_total <= 0.0:
        orig_total = segment_duration
    title = str(meta.get("title") or "").strip()
    artist = str(meta.get("artist") or "").strip()
    album = str(meta.get("album") or "").strip()
    year = _normalize_year_metadata(meta.get("year") or meta.get("date") or "")
    is_stream = bool(
        str(meta.get("wb_stream_source") or "").strip().lower() in ("1", "true", "yes", "on")
        or str(meta.get("wb_source_type") or "").strip().lower() == "stream"
        or path.startswith("http://") or path.startswith("https://")
    )
    if path and not is_stream and ((not title) or (not artist) or (not album) or (not year)):
        try:
            media_meta = read_media_metadata(path)
        except Exception:
            media_meta = {}
        if not title:
            title = str(media_meta.get("title") or "").strip()
        if not artist:
            artist = str(media_meta.get("artist") or "").strip()
        if not album:
            album = str(media_meta.get("album") or "").strip()
        if not year:
            year = _normalize_year_metadata(media_meta.get("year"))
    if (not title or not artist) and path and not is_stream:
        guessed = guess_metadata_from_filename(path)
        title = title or str(guessed.get("title") or "").strip()
        artist = artist or str(guessed.get("artist") or "").strip()
    def _i(*keys, default=0):
        for k in keys:
            try:
                v = str(meta.get(k, "") or "").strip()
                if v:
                    return int(float(v))
            except Exception:
                pass
        return int(default)

    hard_clean = str(meta.get("wb_hard_clean_transition") or meta.get("wb_script_clean") or meta.get("wb_clean_transition") or "").strip().lower() in ("1", "true", "yes", "on")
    short_no_crossfade = str(meta.get("wb_short_no_crossfade") or meta.get("wb_sam_short_no_crossfade") or "").strip().lower() in ("1", "true", "yes", "on")

    return {
        "file": path,
        "title": title,
        "artist": artist,
        "album": album,
        "year": year,
        "queue_id": _i("queue_id", "wb_queue_id", default=0),
        "track_id": _i("track_id", "wb_track_id", default=0),
        "station_key": str(meta.get("station_key") or "").strip(),
        "slot_token": str(meta.get("wb_ab_slot_token") or "").strip(),
        "cue_in": max(0.0, cue_in),
        "cue_out": max(0.0, cue_out),
        "fade_in": max(0.0, fade_in),
        "fade_out": max(0.0, fade_out),
        "orig_total": max(0.0, orig_total),
        "audio_start": max(0.0, audio_start),
        "audio_end": max(0.0, audio_end),
        "segment_duration": max(0.0, segment_duration),
        "cue_source": str(meta.get("wb_cue_source") or "").strip(),
        "hard_clean": bool(hard_clean),
        "short_no_crossfade": bool(short_no_crossfade),
        "no_overlap": bool(hard_clean or short_no_crossfade),
        "script_clean": bool(hard_clean),
        "stream_source": bool(is_stream),
        "stream_infinite": bool(is_stream and str(meta.get("wb_stream_infinite") or "").strip().lower() in ("1", "true", "yes", "on")),
        "stream_duration": max(0.0, _f("wb_stream_duration", "webradio_dur", default=0.0)),
        "raw_uri": str(uri or ""),
    }


def _ab_parse_webradio_line(uri: str) -> dict:
    """Return native stream descriptor data, including infinite streams."""
    try:
        u = str(uri or "").strip()
        if not u:
            return {}
        meta = _parse_annotate_meta(u)
        raw = _ab_line_path(u) if u.startswith("annotate:") else u
        url = str(meta.get("webradio_url") or "").strip()
        dur_s = str(meta.get("webradio_dur") or meta.get("wb_stream_duration") or "").strip()
        if not url and raw.startswith("URL:"):
            parts = raw.split(":", 2)
            if len(parts) >= 3:
                dur_s = parts[1].strip()
                url = parts[2].strip()
        if not url and (raw.startswith("http://") or raw.startswith("https://")):
            url = raw
        if not (url.startswith("http://") or url.startswith("https://")):
            return {}
        try:
            dur = max(0, int(float(dur_s or "0")))
        except Exception:
            dur = 0
        info = _ab_line_info(u)
        return {
            "url": url,
            "dur": dur,
            "infinite": dur <= 0 or bool(info.get("stream_infinite")),
            "queue_id": int(info.get("queue_id") or 0),
            "track_id": int(info.get("track_id") or 0),
            "raw_uri": u,
        }
    except Exception:
        return {}


def _row_value(row, key: str, default=None):
    """Return a value from sqlite Row/dict/tuple-like objects without raising."""
    try:
        if row is None:
            return default
        if isinstance(row, dict):
            return row.get(key, default)
        if hasattr(row, "keys") and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _ab_float_from_settings(settings, key: str, default: float) -> float:
    try:
        value = _row_value(settings, key, default)
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _ab_sam_settings_from_row(settings=None) -> dict:
    """Return validated per-station SAM crossfade settings."""
    gap_start = _ab_float_from_settings(settings, "gap_killer_start_dbfs", _AB_SAM_GAP_START_THRESHOLD_DBFS)
    gap_end = _ab_float_from_settings(settings, "gap_killer_end_dbfs", _AB_SAM_GAP_END_THRESHOLD_DBFS)
    trigger_relative = _ab_float_from_settings(settings, "crossfade_trigger_relative_db", -_AB_SAM_CROSSFADE_TRIGGER_OFFSET_DB)
    fallback_seconds = _ab_float_from_settings(settings, "crossfade_fallback_seconds", _AB_SAM_CROSSFADE_FALLBACK_SEC)
    min_seconds = _ab_float_from_settings(settings, "crossfade_min_seconds", _AB_SAM_CROSSFADE_MIN_SEC)
    max_seconds = _ab_float_from_settings(settings, "crossfade_max_seconds", _AB_SAM_CROSSFADE_MAX_SEC)
    fade_out_seconds = _ab_float_from_settings(settings, "crossfade_fade_out_seconds", _AB_SAM_FADE_OUT_SEC)
    no_crossfade_max_duration_sec = _ab_float_from_settings(settings, "no_crossfade_max_duration_sec", _AB_SAM_NO_CROSSFADE_MAX_DURATION_SEC)

    gap_start = min(0.0, max(-120.0, float(gap_start)))
    gap_end = min(0.0, max(-120.0, float(gap_end)))
    trigger_relative = min(0.0, max(-120.0, float(trigger_relative)))
    fallback_seconds = max(0.0, float(fallback_seconds))
    min_seconds = max(0.0, float(min_seconds))
    max_seconds = max(min_seconds, float(max_seconds))
    fade_out_seconds = max(0.0, float(fade_out_seconds))
    no_crossfade_max_duration_sec = max(0.0, float(no_crossfade_max_duration_sec))
    return {
        "gap_killer_start_dbfs": gap_start,
        "gap_killer_end_dbfs": gap_end,
        "crossfade_trigger_relative_db": trigger_relative,
        "crossfade_fallback_seconds": fallback_seconds,
        "crossfade_min_seconds": min_seconds,
        "crossfade_max_seconds": max_seconds,
        "crossfade_fade_out_seconds": fade_out_seconds,
        "no_crossfade_max_duration_sec": no_crossfade_max_duration_sec,
    }


def _ab_get_sam_crossfade_settings(station_key: str = "") -> dict:
    """Load validated native analyzer/crossfade settings for one station."""
    conn = None
    try:
        sk = _normalize_station_key(str(station_key or "").strip())
        if not sk:
            sk = _normalize_station_key(str(get_active_station_key() or "").strip())
        conn = get_db_for_station(sk) if sk else get_db()
        return _ab_sam_settings_from_row(_ab_get_settings_from_conn(conn))
    except Exception:
        return _ab_sam_settings_from_row(None)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def _ab_get_settings_from_conn(conn: sqlite3.Connection):
    try:
        c = conn.cursor()
        c.execute("SELECT * FROM settings ORDER BY id DESC LIMIT 1")
        return c.fetchone()
    except Exception:
        return None


def _ab_timing_values_active_duration(values: dict) -> tuple[float, str]:
    """Return the real audible/active segment length from timing values.

    Prefer trimmed audio boundaries when they are real boundaries, then cue
    window length.  Full-file audio_start=0/audio_end=duration is only a
    fallback and must not hide a shorter cue_in/cue_out window.
    """
    try:
        cue_in = max(0.0, float((values or {}).get("cue_in") or 0.0))
    except Exception:
        cue_in = 0.0
    try:
        cue_out = max(0.0, float((values or {}).get("cue_out") or 0.0))
    except Exception:
        cue_out = 0.0
    try:
        audio_start = max(0.0, float((values or {}).get("audio_start") or 0.0))
    except Exception:
        audio_start = 0.0
    try:
        audio_end = max(0.0, float((values or {}).get("audio_end") or 0.0))
    except Exception:
        audio_end = 0.0
    try:
        total = max(0.0, float((values or {}).get("orig_total") or (values or {}).get("duration") or 0.0))
    except Exception:
        total = 0.0
    try:
        segment = max(0.0, float((values or {}).get("segment_duration") or 0.0))
    except Exception:
        segment = 0.0

    # Use audio boundaries only when they are actual trims.  The runtime cue
    # annotator can store audio_start=0/audio_end=duration as a safe fallback;
    # for short IDs with cue_in/cue_out that fallback would otherwise make the
    # item look like the full file length.
    if audio_end > audio_start:
        audio_is_trimmed = bool(audio_start > 0.05 or (total > 0.0 and audio_end < max(0.0, total - 0.05)))
        if audio_is_trimmed:
            return max(0.0, audio_end - audio_start), "audio_boundary"
    if cue_out > cue_in:
        return max(0.0, cue_out - cue_in), "cue_window"
    if segment > 0.0:
        return segment, "segment_duration"
    if total > cue_in:
        return max(0.0, total - cue_in), "total_minus_cue_in"
    return 0.0, "missing"


def _ab_line_effective_duration(uri: str) -> float:
    """Return the active duration encoded in the native deck descriptor."""
    try:
        info = _ab_line_info(uri)
        duration, _source = _ab_timing_values_active_duration(info)
        return max(0.0, float(duration or 0.0))
    except Exception:
        return 0.0



def _ab_is_short_item_line(uri: str) -> bool:
    """True when the active window is short enough to need urgent one-shot handling."""
    try:
        dur = float(_ab_line_effective_duration(uri) or 0.0)
        return bool(0.0 < dur <= float(_AB_SHORT_ITEM_PRELOAD_SECONDS))
    except Exception:
        return False


def _ab_preload_delay_for_active_line(uri: str) -> float:
    """Use urgent preload timing only while the currently audible item is short."""
    if _ab_is_short_item_line(uri):
        return 0.0
    return float(_AB_NORMAL_INACTIVE_PRELOAD_DELAY)


def _ab_log_track_cue_data(stage: str, info: dict | None = None, *, player: str = "", source: str = "", extra: dict | None = None) -> None:
    """Emit native timing data for each A/B track without changing playback state."""
    try:
        data = dict(info or {})
        path = normalize_media_path(str(data.get("file") or ""))
        fields = {
            "stage": str(stage or ""),
            "player": str(player or ""),
            "source": str(source or ""),
            "title": str(data.get("title") or ""),
            "artist": str(data.get("artist") or ""),
            "file": os.path.basename(path) if path else "",
            "path": path,
            "queue_id": int(data.get("queue_id") or 0),
            "track_id": int(data.get("track_id") or 0),
            "cue_in": float(data.get("cue_in") or 0.0),
            "cue_out": float(data.get("cue_out") or 0.0),
            "duration": float(data.get("orig_total") or 0.0),
            "segment_duration": float(data.get("segment_duration") or 0.0),
        }
        try:
            meta = _parse_annotate_meta(str(data.get("raw_uri") or ""))
            def _meta_float(*keys):
                for key in keys:
                    try:
                        value = str(meta.get(key, "") or "").strip()
                        if value:
                            return float(value)
                    except Exception:
                        pass
                return 0.0
            fields["fade_in"] = _meta_float("fade_in")
            fields["fade_out"] = _meta_float("fade_out")
            fields["raw_cue_in"] = str(meta.get("cue_in") or "")
            fields["raw_cue_out"] = str(meta.get("cue_out") or "")
        except Exception:
            pass
        if extra:
            for key, value in dict(extra).items():
                fields[str(key)] = value
    except Exception as exc:
        pass

def _ab_apply_now_playing_line(station_key: str, uri: str, *, reset_progress: bool = True) -> None:
    """Seed the Python UI now-playing store from the active A/B request.

    Native A/B mode does not rely on retired /now_playing callbacks from the
    old playlist graph, so the UI/seek code needs this Python-side authoritative
    current-track state.
    """
    try:
        sk = station_key or (get_active_station_key() or "")
        info = _ab_line_info(uri)
        path = normalize_media_path(str(info.get("file") or ""))
        if not sk or not path:
            return
        title = str(info.get("title") or "").strip()
        artist = str(info.get("artist") or "").strip()
        album = str(info.get("album") or "").strip()
        orig_total = float(info.get("orig_total") or 0.0)
        cue_in = float(info.get("cue_in") or 0.0)
        cue_out = float(info.get("cue_out") or orig_total or 0.0)
        queue_id = int(info.get("queue_id") or 0)
        track_id = int(info.get("track_id") or 0)
        _ab_log_track_cue_data("now_playing_seed", info, source="python_ui_seed", extra={"reset_progress": bool(reset_progress)})
        seed_key = f"{queue_id}:{track_id}:{path}:{cue_in:.3f}"
        with _AB_PLAYER_LOCK:
            last_seed_key = str(_AB_PLAYER_STATE.get("last_seed_key") or "")
        if reset_progress and seed_key and seed_key == last_seed_key:
            return
        logical_id = int(time.time() * 1000)
        with NOW_PLAYING_LOCK:
            store = _get_now_playing_store(sk)
            old_file = normalize_media_path(str(store.get("file") or ""))
            old_cue = float(store.get("display_seek_base") or 0.0)
            if old_file == path and abs(old_cue - cue_in) < 0.001:
                logical_id = int(store.get("logical_track_id") or logical_id)
            store["title"] = title
            store["artist"] = artist
            store["album"] = album
            store["year"] = str(store.get("year") or "")
            store["file"] = path
            store["queue_id"] = queue_id
            store["track_id"] = track_id
            store["duration"] = float(orig_total or max(0.0, cue_out - cue_in))
            store["display_original_duration"] = float(orig_total or max(0.0, cue_out))
            store["display_seek_base"] = float(cue_in)
            store["source_seek_base_seconds"] = float(cue_in)
            store["cue_in_seconds"] = float(cue_in)
            store["cue_out_seconds"] = float(cue_out)
            store["logical_track_id"] = logical_id
            store["display_track_logical_id"] = logical_id
            store["display_hold_until_metadata"] = False
            store["display_hold_started_at"] = 0.0
            store["seek_session_id"] = 0
            store["pending_seek_restart"] = False
            store["seek_isolated_playlist_active"] = False
            _get_now_playing_event(sk).set()
        if reset_progress:
            try:
                _mark_progress_track_start(sk, path, logical_track_id=logical_id, display_track_logical_id=logical_id, duration=float(orig_total or 0.0))
            except Exception:
                pass
        with _AB_PLAYER_LOCK:
            _AB_PLAYER_STATE["last_seed_key"] = seed_key
    except Exception as exc:
        pass


def _ab_line_duration_and_fade(uri: str) -> tuple[float, float]:
    meta = _parse_annotate_meta(uri)
    def _f(*keys, default=0.0):
        for k in keys:
            try:
                v = str(meta.get(k, "") or "").strip()
                if v:
                    return float(v)
            except Exception:
                pass
        return float(default)
    cue_in = _f("cue_in", "wb_play_start", "wb_seek_base", default=0.0)
    cue_out = _f("cue_out", "wb_crossfade_trigger", "wb_orig_total", default=0.0)
    total = _f("wb_orig_total", "duration", default=0.0)
    if cue_out <= cue_in and total > cue_in:
        cue_out = total
    window_duration = max(1.0, cue_out - cue_in) if cue_out > cue_in else max(1.0, total - cue_in)
    effective_duration = float(_ab_line_effective_duration(uri) or 0.0)
    duration = max(1.0, effective_duration if effective_duration > 0.0 else window_duration)
    short_item = bool(0.0 < effective_duration <= float(_AB_SHORT_ITEM_PRELOAD_SECONDS))
    no_crossfade_item = False
    try:
        hard_clean = str(meta.get("wb_hard_clean_transition") or meta.get("wb_script_clean") or meta.get("wb_clean_transition") or "").strip().lower() in ("1", "true", "yes", "on")
        short_no_crossfade = str(meta.get("wb_short_no_crossfade") or meta.get("wb_sam_short_no_crossfade") or "").strip().lower() in ("1", "true", "yes", "on")
        no_crossfade_item = bool(hard_clean or short_no_crossfade)
    except Exception:
        no_crossfade_item = False
    fade = _f("fade_out", default=0.0)
    if no_crossfade_item:
        fade = 0.0
    elif fade <= 0.0:
        station_key = str(meta.get("station_key") or "").strip()
        fade = float(_ab_get_sam_crossfade_settings(station_key)["crossfade_fade_out_seconds"])
    effective_end = _f("wb_effective_end", "wb_audio_end", "audio_end", default=0.0)
    timing = _ab_enforce_fade_tail_window(
        play_start=cue_in,
        crossfade_trigger=cue_out,
        effective_end=effective_end,
        fade_duration=fade,
        source_end=total,
    )
    cue_out = float(timing["crossfade_trigger"])
    fade = float(timing["fade_duration"])
    window_duration = max(1.0, cue_out - cue_in) if cue_out > cue_in else max(1.0, total - cue_in)
    effective_duration = float(_ab_line_effective_duration(uri) or 0.0)
    duration = max(1.0, effective_duration if effective_duration > 0.0 else window_duration)
    fade = max(0.0, min(duration, fade))
    return duration, fade


def _ab_runtime_annotate_uri(uri: str, *, player: str, generation: int | None = None, slot_token: str = "") -> str:
    """Add transient A/B player identity to a request URI before pushing it.

    The master descriptor stays clean, while lifecycle metadata
    carry wb_ab_player so Python can distinguish real active-player metadata
    from request.queue preload metadata.
    """
    try:
        u = str(uri or "").strip()
        if not u:
            return u
        player = "a" if str(player).lower().endswith("a") else "b"
        gen = int(generation or 0)
        additions = {"wb_ab_player": player, "wb_ab_generation": str(gen)}
        tok = str(slot_token or "").strip()
        if tok:
            additions["wb_ab_slot_token"] = tok
        if u.startswith("annotate:"):
            split = _split_annotate_uri(u)
            if not split:
                return u
            meta, path = split
            meta = _ab_sanitize_timing_metadata(meta)
            meta.update(additions)
            return _build_annotate_uri(meta, path)
        meta = _ab_sanitize_timing_metadata({})
        meta.update(additions)
        return _build_annotate_uri(meta, u)
    except Exception:
        return str(uri or "")

def _ab_prepare_engine_load_uri(player: str, uri: str) -> str:
    """Attach the immutable A/B slot identity before crossing AudioEngine.

    The exact queue_id + slot_token + deck identity is attached before the
    descriptor crosses the native AudioEngine boundary.
    """
    try:
        value = str(uri or "").strip()
        if not value:
            return value
        normalized_player = "a" if str(player).lower().endswith("a") else "b"
        with _AB_PLAYER_LOCK:
            generation = int((_AB_PLAYER_STATE or {}).get("generation") or 0)
        info = _ab_line_info(value)
        existing_token = str(info.get("slot_token") or "").strip()
        path = normalize_media_path(
            str(info.get("file") or _ab_line_path(value) or "")
        )
        if existing_token:
            slot_token = existing_token
        else:
            try:
                path_hash = hashlib.sha1(
                    path.encode("utf-8", errors="ignore")
                ).hexdigest()[:12]
            except Exception:
                path_hash = "nohash"
            slot_token = (
                f'{int(info.get("queue_id") or 0)}-'
                f'{int(info.get("track_id") or 0)}-'
                f'{path_hash}-{generation}'
            )
        return _ab_runtime_annotate_uri(
            value,
            player=normalized_player,
            generation=generation,
            slot_token=slot_token,
        )
    except Exception:
        return str(uri or "")


def _ab_loaded_identity_key(uri: str) -> str:
    """Return the stable queue/track/path identity used for deck reuse."""
    try:
        info = _ab_line_info(uri)
        path = normalize_media_path(
            str(info.get("file") or _ab_line_path(uri) or "")
        )
        queue_id = int(info.get("queue_id") or 0)
        track_id = int(info.get("track_id") or 0)
        if queue_id <= 0 or not path:
            return ""
        return f"{queue_id}:{track_id}:{path}"
    except Exception:
        return ""


def _ab_record_player_loaded_identity(
    player: str,
    uri: str,
    *,
    generation: int | None = None,
) -> bool:
    """Record one confirmed native deck load without trusting playlist indexes.

    The record is accepted only for the A/B generation that produced the load.
    A late successful reply from an older replan therefore cannot mark a stale
    descriptor as the current prepared next item.
    """
    player = "a" if str(player).lower().endswith("a") else "b"
    key = _ab_loaded_identity_key(uri)
    if not key:
        return False
    if generation is None:
        try:
            generation = int(
                float(str(_parse_annotate_meta(uri).get("wb_ab_generation") or "0"))
            )
        except Exception:
            generation = 0
    try:
        load_generation = int(generation or 0)
    except Exception:
        load_generation = 0
    with _AB_PLAYER_LOCK:
        current_generation = int((_AB_PLAYER_STATE or {}).get("generation") or 0)
        if load_generation > 0 and current_generation != load_generation:
            return False
        loaded_keys = dict((_AB_PLAYER_STATE or {}).get("player_loaded_keys") or {})
        loaded_generations = dict((_AB_PLAYER_STATE or {}).get("player_generation") or {})
        loaded_keys[player] = key
        loaded_generations[player] = current_generation
        _AB_PLAYER_STATE["player_loaded_keys"] = loaded_keys
        _AB_PLAYER_STATE["player_generation"] = loaded_generations
    return True


def _ab_native_deck_matches_line(
    native_state: dict,
    player: str,
    line: str,
) -> tuple[bool, str, int, str]:
    """Validate that a physical native deck already owns the requested queue row."""
    player = "a" if str(player).lower().endswith("a") else "b"
    state = dict(native_state or {})
    try:
        expected_queue_id = int(_ab_line_info(line).get("queue_id") or 0)
    except Exception:
        expected_queue_id = 0
    expected_key = _ab_loaded_identity_key(line)
    live_queue_id = 0
    for key in (
        f"deck_{player}_queue_id",
        f"native_audio_deck_{player}_queue_id",
    ):
        try:
            candidate = int(state.get(key) or 0)
        except Exception:
            candidate = 0
        if candidate > 0:
            live_queue_id = candidate
            break
    live_slot_token = ""
    for key in (
        f"deck_{player}_slot_token",
        f"native_audio_deck_{player}_slot_token",
    ):
        candidate = str(state.get(key) or "").strip()
        if candidate:
            live_slot_token = candidate
            break
    matches = bool(
        expected_queue_id > 0
        and live_queue_id == expected_queue_id
        and live_slot_token
        and expected_key
    )
    return matches, expected_key, live_queue_id, live_slot_token


def _ab_push(player: str, uri: str, *, attempts: int = 8, retry_delay: float = 0.35, clear_slot: bool = False, manual_next_fast: bool = False) -> bool:
    """Load an A/B deck through the configured AudioEngine backend."""
    engine_uri = _ab_prepare_engine_load_uri(player, uri)
    ok = bool(
        get_audio_engine().load_deck(
            player,
            engine_uri,
            attempts=attempts,
            retry_delay=retry_delay,
            clear_slot=clear_slot,
            manual_next_fast=manual_next_fast,
        )
    )
    if ok:
        _ab_record_player_loaded_identity(player, engine_uri)
    return ok


def _ab_wait_for_native_deck_prebuffer(
    player: str,
    line: str,
    *,
    station_key: str,
    timeout_sec: float = 4.0,
    poll_interval_sec: float = 0.02,
) -> tuple[bool, dict, str]:
    """Wait until one loaded native deck owns real PCM before selecting it.

    ``load`` is intentionally asynchronous.  Selecting the first deck before its
    decoder reaches ``prebuffer_ready`` can expose one mixer tick with no PCM and
    create a startup underrun.  Match the exact queue identity and require a
    non-empty ring buffer so stale readiness from an older candidate cannot pass.
    """
    player = "a" if str(player or "").lower().endswith("a") else "b"
    station_key = str(station_key or "").strip()
    info = _ab_line_info(line)
    try:
        expected_queue_id = int(info.get("queue_id") or 0)
    except Exception:
        expected_queue_id = 0
    timeout_sec = max(0.10, float(timeout_sec or 0.0))
    poll_interval_sec = min(0.25, max(0.01, float(poll_interval_sec or 0.02)))
    deadline = time.monotonic() + timeout_sec
    last_state: dict = {}
    last_reason = "prebuffer_pending"

    while time.monotonic() < deadline:
        try:
            last_state = dict(get_audio_engine().get_state(station_key=station_key) or {})
        except Exception as exc:
            last_reason = f"state_error:{type(exc).__name__}:{exc}"
            time.sleep(poll_interval_sec)
            continue

        if not bool(last_state.get("running")):
            return False, last_state, "engine_stopped"

        prefix = f"native_audio_deck_{player}_"
        try:
            live_queue_id = int(last_state.get(prefix + "queue_id") or 0)
        except Exception:
            live_queue_id = 0
        try:
            ring_bytes = int(last_state.get(prefix + "ring_buffer_bytes") or 0)
        except Exception:
            ring_bytes = 0
        ready = bool(last_state.get(prefix + "prebuffer_ready"))
        status = str(last_state.get(prefix + "status") or "").strip().lower()
        identity_matches = bool(expected_queue_id <= 0 or live_queue_id == expected_queue_id)

        if identity_matches and ready and ring_bytes > 0:
            return True, last_state, "ready"

        if expected_queue_id > 0 and live_queue_id > 0 and live_queue_id != expected_queue_id:
            last_reason = f"queue_identity_mismatch:{expected_queue_id}!={live_queue_id}"
        elif ready and ring_bytes <= 0:
            last_reason = "ready_without_pcm"
        else:
            last_reason = status or "prebuffer_pending"
        time.sleep(poll_interval_sec)
    return False, last_state, last_reason


def _ab_schedule_inactive_preload_after_start(
    station_key: str,
    *,
    active_player: str,
    inactive_player: str,
    line: str,
    next_index: int,
    delay: float = 5.0,
    urgent: bool = False,
    reason: str = "ab_delayed_inactive_preload",
) -> bool:
    """Load the recycled outgoing A/B deck only after the new on-air deck is stable.

    The transition target must already be loaded before cue_out.  This guard is
    only for putting the following (third) queue item into the deck that just
    finished its audible tail, because that physical branch may still leak for a
    short moment after handoff/blanking.
    """
    try:
        active_player = "a" if str(active_player).lower().endswith("a") else "b"
        inactive_player = "a" if str(inactive_player).lower().endswith("a") else "b"
        next_index = int(next_index)
        delay = max(0.0, float(delay or 0.0))
        urgent_preload = bool(urgent or delay <= 0.05)
        scheduled_at = time.time()
        with _AB_PLAYER_LOCK:
            scheduled_generation = int((_AB_PLAYER_STATE or {}).get("generation") or 0)
            scheduled_current_index = int((_AB_PLAYER_STATE or {}).get("current_index") or 0)

        def _run() -> None:
            try:
                with _AB_PLAYER_LOCK:
                    st = dict(_AB_PLAYER_STATE or {})
                    player_enabled = bool(st.get("enabled"))
                    live_active = str(st.get("active") or "a").lower()
                    live_generation = int(st.get("generation") or 0)
                    current_index = int(st.get("current_index") or 0)
                    started_at = float(st.get("started_at") or 0.0)
                    player_index = dict(st.get("player_index") or {})
                    live_lines = list(st.get("lines") or [])
                    loaded_idx = int(player_index.get(inactive_player, -1))
                native_running = bool(_native_station_state(station_key).get("running"))
                active_age = time.time() - started_at if started_at > 0.0 else time.time() - scheduled_at
                expected_line = live_lines[next_index] if 0 <= next_index < len(live_lines) else ""
                active_line = live_lines[current_index] if 0 <= current_index < len(live_lines) else ""
                active_info = _ab_line_info(active_line) if active_line else {}
                active_duration = float(active_info.get("orig_total") or active_info.get("segment_duration") or 0.0)
                active_is_long_fallback = active_duration >= 600.0
                if not player_enabled or not native_running:
                    return
                elif live_generation != scheduled_generation:
                    try:
                        script_break_until = float(st.get("script_break_until") or 0.0)
                    except Exception:
                        script_break_until = 0.0
                    if script_break_until > time.time():
                        # A scripted break (time file + spot) has replaced the active
                        # plan atomically. Old delayed preload/recovery timers from the
                        # previous song must not replan the queue and reorder the script
                        # group. The fresh script-break state schedules its own preload.
                        return
                    elif active_is_long_fallback and active_age < 30.0:
                        remaining = max(1.0, 30.0 - active_age)
                        try:
                            timer_ltf = threading.Timer(remaining, _run)
                            timer_ltf.daemon = True
                            timer_ltf.start()
                        except Exception:
                            pass
                    else:
                        manual_guard_remaining = _ab_manual_next_guard_remaining()
                        if manual_guard_remaining > 0.0:
                            try:
                                timer_mn = threading.Timer(manual_guard_remaining + 0.25, _run)
                                timer_mn.daemon = True
                                timer_mn.start()
                            except Exception:
                                pass
                        else:
                            try:
                                threading.Thread(
                                    target=_ab_replan_after_queue_mutation,
                                    kwargs={"reason": f"{reason}_stale_generation_recovery"},
                                    daemon=True,
                                ).start()
                            except Exception:
                                pass
                elif live_active != active_player:
                    return
                elif current_index != scheduled_current_index:
                    return
                elif expected_line and not _ab_same_queue_identity(expected_line, line):
                    return
                elif (not urgent_preload) and active_age < max(0.0, delay - 0.25):
                    # The normal 5 second guard belongs only to the recycled/outgoing deck,
                    # not to the already-prepared transition target.  Short items bypass
                    # this fixed wait and retry quickly until the old deck is released.
                    remaining = max(0.10, delay - active_age)
                    try:
                        timer2 = threading.Timer(remaining, _run)
                        timer2.daemon = True
                        timer2.start()
                    except Exception:
                        pass
                else:
                    queue_head_ok, _line_queue_id, _live_head_id = _ab_line_matches_station_queue_head(station_key, line)
                    if not queue_head_ok:
                        try:
                            timer_qh = threading.Timer(1.0, _ab_replan_after_queue_mutation, kwargs={"reason": f"{reason}_queue_head_mismatch_recovery"})
                            timer_qh.daemon = True
                            timer_qh.start()
                        except Exception:
                            pass
                    else:
                        if active_is_long_fallback and active_age < 30.0:
                            remaining = max(1.0, 30.0 - active_age)
                            try:
                                timer_ltf2 = threading.Timer(remaining, _run)
                                timer_ltf2.daemon = True
                                timer_ltf2.start()
                            except Exception:
                                pass
                        else:
                            # Do not trust player_index alone here.  Queue replans can
                            # legitimately reserve index 1 before the recycled deck is
                            # loadable; treating that reservation as "already loaded"
                            # leaves the native engine without a ready next deck at cue_out.
                            with _AB_PLAYER_LOCK:
                                loaded_keys = dict(_AB_PLAYER_STATE.get("player_loaded_keys") or {})
                                loaded_generations = dict(_AB_PLAYER_STATE.get("player_generation") or {})
                            try:
                                line_info = _ab_line_info(line)
                                line_path = normalize_media_path(str(line_info.get("file") or _ab_line_path(line) or ""))
                                expected_key = f'{int(line_info.get("queue_id") or 0)}:{int(line_info.get("track_id") or 0)}:{line_path}'
                            except Exception:
                                expected_key = ""
                            loaded_key = str(loaded_keys.get(inactive_player) or "")
                            try:
                                loaded_generation = int(loaded_generations.get(inactive_player)) if inactive_player in loaded_generations else None
                            except Exception:
                                loaded_generation = None
                            same_loaded = bool(loaded_idx == next_index and expected_key and loaded_key == expected_key and loaded_generation == live_generation)
                            if not same_loaded:
                                # Do not spin aggressively while the native daemon keeps
                                # the old/outgoing deck protected by the transition guard.
                                # Earlier builds tried many immediate ab.load_* calls here;
                                # if all of them hit BUSY_TRANSITION_OLD_DECK the preload
                                # was abandoned and the next cue_out had no ready deck.
                                push_attempts = 8 if urgent_preload else 3
                                push_retry = 0.12 if urgent_preload else 0.25
                                load_ok = bool(_ab_push(inactive_player, line, attempts=push_attempts, retry_delay=push_retry, clear_slot=True))
                                if load_ok:
                                    with _AB_PLAYER_LOCK:
                                        pi2 = dict(_AB_PLAYER_STATE.get("player_index") or {})
                                        pi2[inactive_player] = next_index
                                        _AB_PLAYER_STATE["player_index"] = pi2
                                else:
                                    try:
                                        with _AB_PLAYER_LOCK:
                                            last_load_error = str(_AB_PLAYER_STATE.get("last_load_error") or "")
                                    except Exception:
                                        last_load_error = ""
                                    if "BUSY_TRANSITION_OLD_DECK" in last_load_error:
                                        retry_window = float(_AB_URGENT_BUSY_RETRY_WINDOW) if urgent_preload else 0.0
                                        if urgent_preload and (time.time() - scheduled_at) <= retry_window:
                                            retry_delay_sec = float(_AB_URGENT_BUSY_RETRY_DELAY)
                                        else:
                                            retry_delay_sec = 5.0
                                        try:
                                            timer3 = threading.Timer(retry_delay_sec, _run)
                                            timer3.daemon = True
                                            timer3.start()
                                        except Exception:
                                            pass
            except Exception:
                pass

        timer = threading.Timer(delay, _run)
        timer.daemon = True
        timer.start()
        return True
    except Exception as exc:
        return False



def _ab_select(player: str, *, timeout_sec: float = 1.0):
    """Hard-select an A/B deck through the configured AudioEngine backend."""
    return get_audio_engine().select_deck(player, timeout_sec=timeout_sec)


def _ab_hard_handoff_to(player: str, *, station_key: str = "", timeout_sec: float = 1.0):
    """Arm one native mixer-owned sample-boundary hard handoff."""
    engine = get_audio_engine()
    method = getattr(engine, "hard_handoff_to", None)
    if not callable(method):
        raise RuntimeError("native_hard_handoff_not_supported")
    return method(
        player,
        station_key=str(station_key or ""),
        timeout_sec=float(timeout_sec),
    )


def _ab_transition_to(player: str, duration: float, *, timeout_sec: float = 1.0):
    """Start an A/B transition through the configured AudioEngine backend."""
    return get_audio_engine().transition_to(player, duration, timeout_sec=timeout_sec)


def _native_sync_transition_completion(
    station_key: str,
    *,
    from_deck: str,
    from_line: str,
    to_deck: str,
    to_line: str,
) -> bool:
    """Finalize one native transition without hard-selecting the target deck.

    ``select`` is a destructive handoff: by completion time the outgoing deck
    may already contain the following preloaded item, so selecting the target
    can release the wrong token.  The native mixer already owns the audible
    handoff; completion only needs a token-scoped ``transition_finished`` plus
    ``track_ended`` for the exact outgoing identity.
    """
    engine = get_audio_engine()
    sync_live_event = getattr(engine, "sync_live_event", None)
    if not callable(sync_live_event):
        raise RuntimeError("native_transition_completion_not_supported")

    now_mono_ms = int(round(time.monotonic() * 1000.0))
    now_wall_ms = int(round(time.time() * 1000.0))

    def _record(event_name: str, deck: str, line: str, payload: dict) -> dict:
        info = _ab_line_info(line) if line else {}
        return {
            "event": event_name,
            "station_key": str(station_key or info.get("station_key") or ""),
            "queue_id": int(info.get("queue_id") or 0),
            "slot_token": str(info.get("slot_token") or ""),
            "deck": str(deck or "").upper(),
            "track_id": int(info.get("track_id") or 0),
            "path": normalize_media_path(str(info.get("file") or "")),
            "artist": str(info.get("artist") or ""),
            "title": str(info.get("title") or ""),
            "year": _normalize_year_metadata(info.get("year")),
            "event_monotonic_time_ms": now_mono_ms,
            "event_wall_time_unix_ms": now_wall_ms,
            "payload": dict(payload or {}),
        }

    finished_record = _record(
        "transition_finished",
        to_deck,
        to_line,
        {
            "from_deck": str(from_deck or "").upper(),
            "source": "native_transition_monitor",
        },
    )
    ended_record = _record(
        "track_ended",
        from_deck,
        from_line,
        {
            "to_deck": str(to_deck or "").upper(),
            "reason": "native_transition_finished",
            "source": "native_transition_monitor",
        },
    )
    sync_live_event(finished_record)
    sync_live_event(ended_record)
    _publish_audio_engine_event(
        "transition_finished",
        station_key=finished_record["station_key"],
        queue_id=finished_record["queue_id"],
        slot_token=finished_record["slot_token"],
        deck=finished_record["deck"],
        track_id=finished_record["track_id"],
        path=finished_record["path"],
        payload=finished_record["payload"],
    )
    _publish_audio_engine_event(
        "track_ended",
        station_key=ended_record["station_key"],
        queue_id=ended_record["queue_id"],
        slot_token=ended_record["slot_token"],
        deck=ended_record["deck"],
        track_id=ended_record["track_id"],
        path=ended_record["path"],
        payload=ended_record["payload"],
    )
    return True


def _ab_same_queue_identity(line_a: str, line_b: str) -> bool:
    """Return True only for the same concrete queue item, not merely same file."""
    try:
        ia = _ab_line_info(line_a)
        ib = _ab_line_info(line_b)
        qa, qb = int(ia.get("queue_id") or 0), int(ib.get("queue_id") or 0)
        ta, tb = int(ia.get("track_id") or 0), int(ib.get("track_id") or 0)
        if qa > 0 and qb > 0:
            return qa == qb
        if ta > 0 and tb > 0:
            pa = normalize_media_path(str(ia.get("file") or ""))
            pb = normalize_media_path(str(ib.get("file") or ""))
            return ta == tb and bool(pa and pb and pa == pb)
    except Exception:
        pass
    return False


def _ab_line_matches_station_queue_head(station_key: str, line: str) -> tuple[bool, int, int]:
    """Check that a delayed preload still points to the real DB queue head.

    This protects script-inserted groups like [time file, spot].  A stale delayed
    preload for the second item must not become audible before the first item.
    """
    try:
        info = _ab_line_info(line)
        line_qid = int(info.get("queue_id") or 0)
    except Exception:
        line_qid = 0
    try:
        head_qid = int(_get_station_queue_head_id(station_key) or 0)
    except Exception:
        head_qid = 0
    if line_qid <= 0 or head_qid <= 0:
        return True, line_qid, head_qid
    return line_qid == head_qid, line_qid, head_qid



def _ab_manual_next_guard_remaining() -> float:
    """Return remaining seconds of the manual NEXT A/B stabilization guard."""
    try:
        with _AB_PLAYER_LOCK:
            until = float((_AB_PLAYER_STATE or {}).get("manual_next_until") or 0.0)
        return max(0.0, until - time.time())
    except Exception:
        return 0.0


def _ab_schedule_deferred_replan(reason: str, delay: float) -> bool:
    """Run an A/B queue replan after the current manual NEXT guard expires."""
    try:
        delay = max(0.25, float(delay or 0.0))
        def _run_deferred() -> None:
            try:
                _ab_replan_after_queue_mutation(reason=f"{reason}_deferred_after_manual_next")
            except Exception as exc:
                pass
        timer = threading.Timer(delay, _run_deferred)
        timer.daemon = True
        timer.start()
        return True
    except Exception:
        return False

def _ab_replan_after_queue_mutation(reason: str = "queue_mutation") -> bool:
    """Apply UI queue edits without interrupting the currently audible track.

    Queue edits are authoritative for what should play *after* the current song,
    but they must not stop, seek, reload, or replace the active/on-air deck.  The
    previous v2549 helper called ab.force_reset and bootstrapped from the new DB queue plan
    head, which made a queue clear/add cut the current song and start the new
    queue immediately.  This helper keeps the active deck as index 0 in the local
    A/B plan, then reloads only the inactive deck with the first fresh queue item.
    """
    manual_guard_remaining = _ab_manual_next_guard_remaining()
    if manual_guard_remaining > 0.0:
        # v2818: after an operator NEXT, Python has already selected and
        # committed the new active deck.  AutoDJ refill and delayed preload
        # recovery replans may arrive during that short stabilization window.
        # Do not let them rewrite current_index/active from a stale snapshot;
        # retry after the manual NEXT state is stable.
        _ab_schedule_deferred_replan(reason, manual_guard_remaining + 0.25)
        return False
    replan_serial = _ab_begin_replan_serial(reason)
    station_key = ""
    try:
        station_key = str(get_active_station_key() or "")
    except Exception:
        station_key = ""

    # Queue edits while the station is off-air must update only the persisted
    # database queue.  A delayed AutoDJ/refill replan used to see the cleared A/B state
    # after stop and incorrectly bootstrap both decks into a stopped engine.
    native_state = _native_station_state(station_key) if station_key else {}
    if not bool(native_state.get("running")):
        return True

    with _AB_PLAYER_LOCK:
        handoff_snapshot = dict(_AB_PLAYER_STATE or {})
    if bool(handoff_snapshot.get("hard_handoff_armed")):
        try:
            deadline = float(handoff_snapshot.get("hard_handoff_deadline") or 0.0)
        except Exception:
            deadline = 0.0
        delay = min(6.0, max(0.5, deadline - time.time() + 0.25)) if deadline > 0.0 else 2.0
        _ab_schedule_deferred_replan(reason, delay)
        return False
    if bool(handoff_snapshot.get("seek_pending")):
        try:
            seek_deadline = float(handoff_snapshot.get("seek_pending_deadline") or 0.0)
        except Exception:
            seek_deadline = 0.0
        delay = min(6.0, max(0.25, seek_deadline - time.time() + 0.10)) if seek_deadline > 0.0 else 0.50
        _ab_schedule_deferred_replan(reason, delay)
        return False

    fresh_lines = _build_station_queue_plan(station_key) if station_key else []

    with _AB_PLAYER_LOCK:
        st = dict(_AB_PLAYER_STATE or {})
        was_enabled = bool(st.get("enabled"))
        active = str(st.get("active") or "a").lower()
        if active not in ("a", "b"):
            active = "a"
        inactive = "b" if active == "a" else "a"
        old_lines = list(st.get("lines") or [])
        old_durations = list(st.get("durations") or [])
        old_fadeouts = list(st.get("fadeouts") or [])
        player_index = dict(st.get("player_index") or {})
        try:
            active_index = int(player_index.get(active, st.get("current_index") or 0))
        except Exception:
            active_index = int(st.get("current_index") or 0) if str(st.get("current_index") or "").strip() else 0
        current_line = old_lines[active_index] if 0 <= active_index < len(old_lines) else ""
        current_duration = float(old_durations[active_index]) if 0 <= active_index < len(old_durations) else 0.0
        current_fade = float(old_fadeouts[active_index]) if 0 <= active_index < len(old_fadeouts) else 3.0
        started_at = float(st.get("started_at") or 0.0)

    if not was_enabled or not current_line:
        if _ab_abort_stale_replan_if_needed(replan_serial, reason, "before_bootstrap"):
            return False
        ok_boot = _ab_bootstrap_from_queue_plan(fresh_lines, station_key=station_key)
        return bool(ok_boot)

    replan_native_state: dict = {}
    try:
        live_active, live_status, _live_raw = _ab_resolve_native_live_player(active, timeout_sec=0.45)
        replan_native_state = dict(_native_station_state(station_key) or {})
        live_uri = str((live_status or {}).get(f"{live_active}_uri") or "").strip() if live_active in ("a", "b") else ""
        if live_active in ("a", "b") and live_uri:
            live_info = _ab_line_info(live_uri)
            live_path = normalize_media_path(str(live_info.get("file") or _ab_line_path(live_uri) or ""))
            if live_path:
                if live_active != active or not _ab_same_queue_identity(live_uri, current_line):
                    pass
                active = live_active
                inactive = "b" if active == "a" else "a"
                current_line = live_uri
                current_duration, current_fade = _ab_line_duration_and_fade(current_line)
    except Exception as _live_replan_exc:
        pass

    planned_lines = [current_line]
    for line in fresh_lines:
        if _ab_same_queue_identity(line, current_line):
            continue
        planned_lines.append(line)
    durations, fadeouts = [], []
    for i, line in enumerate(planned_lines):
        if i == 0 and current_duration > 0.0:
            durations.append(current_duration)
            fadeouts.append(current_fade)
        else:
            d, f = _ab_line_duration_and_fade(line)
            durations.append(d)
            fadeouts.append(f)

    preserved_inactive_load = False
    preserved_inactive_key = ""
    preserved_inactive_queue_id = 0
    preserved_inactive_slot_token = ""
    if len(planned_lines) > 1:
        (
            preserved_inactive_load,
            preserved_inactive_key,
            preserved_inactive_queue_id,
            preserved_inactive_slot_token,
        ) = _ab_native_deck_matches_line(
            replan_native_state,
            inactive,
            planned_lines[1],
        )

    next_generation = 0
    if _ab_abort_stale_replan_if_needed(replan_serial, reason, "before_state_apply"):
        return False
    with _AB_PLAYER_LOCK:
        next_generation = int(_AB_PLAYER_STATE.get("generation") or 0) + 1
        _AB_PLAYER_STATE["generation"] = next_generation
        _AB_PLAYER_STATE["enabled"] = True
        _AB_PLAYER_STATE["station_key"] = station_key or str(_AB_PLAYER_STATE.get("station_key") or "")
        _AB_PLAYER_STATE["active"] = active
        _AB_PLAYER_STATE["lines"] = planned_lines
        _AB_PLAYER_STATE["durations"] = durations
        _AB_PLAYER_STATE["fadeouts"] = fadeouts
        _AB_PLAYER_STATE["current_index"] = 0
        _AB_PLAYER_STATE["next_index"] = 1 if len(planned_lines) > 1 else 0
        # Preserve an already confirmed physical preload when the daemon proves
        # that the inactive deck still owns the exact DB queue row.  A queue
        # refill/replan must not generate a new slot token and restart native
        # analysis for an unchanged prepared next item.
        player_index_next = {active: 0}
        if preserved_inactive_load:
            player_index_next[inactive] = 1
        _AB_PLAYER_STATE["player_index"] = player_index_next
        lk = dict(_AB_PLAYER_STATE.get("player_loaded_keys") or {})
        if preserved_inactive_load and preserved_inactive_key:
            lk[inactive] = preserved_inactive_key
        else:
            lk.pop(inactive, None)
        _AB_PLAYER_STATE["player_loaded_keys"] = lk
        pg = dict(_AB_PLAYER_STATE.get("player_generation") or {})
        if preserved_inactive_load:
            pg[inactive] = next_generation
        else:
            pg.pop(inactive, None)
        _AB_PLAYER_STATE["player_generation"] = pg
        _AB_PLAYER_STATE["transitioning"] = False
        _AB_PLAYER_STATE["transition_starting"] = False
        _AB_PLAYER_STATE["transition_target"] = ""
        _AB_PLAYER_STATE["transition_from"] = ""
        _AB_PLAYER_STATE["pending_cueout_transition"] = False
        _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
        _AB_PLAYER_STATE["pending_cueout_token"] = 0
        if started_at > 0.0:
            _AB_PLAYER_STATE["started_at"] = started_at

    ok_inactive = True
    clear_resp = ""
    if len(planned_lines) > 1:
        if _ab_abort_stale_replan_if_needed(replan_serial, reason, "before_inactive_load"):
            return False
        if preserved_inactive_load:
            ok_inactive = True
            clear_resp = "native_inactive_preload_reused"
            _preload_reuse_trace(
                "ab_queue_mutation_replan_reused_native_preload",
                station_key=station_key,
                deck=str(inactive).upper(),
                queue_id=int(preserved_inactive_queue_id or 0),
                slot_token=str(preserved_inactive_slot_token or ""),
                reason=reason,
                active_deck=str(active).upper(),
                generation=next_generation,
            )
        else:
            ok_inactive = bool(_ab_push(inactive, planned_lines[1], attempts=12, retry_delay=0.10, clear_slot=True))
        if _ab_abort_stale_replan_if_needed(replan_serial, reason, "after_inactive_load"):
            return False
        if ok_inactive:
            with _AB_PLAYER_LOCK:
                pi = dict(_AB_PLAYER_STATE.get("player_index") or {})
                pi[active] = 0
                pi[inactive] = 1
                _AB_PLAYER_STATE["player_index"] = pi
        else:
            # The inactive deck can still be the audible tail of the previous
            # transition. The native daemon then rejects deck load with
            # BUSY_TRANSITION_OLD_DECK.  Do not leave the A/B plan without a
            # prepared next item: retry the same preload after the recycled deck
            # has had time to become safe again.
            try:
                _ab_schedule_inactive_preload_after_start(
                    station_key,
                    active_player=active,
                    inactive_player=inactive,
                    line=planned_lines[1],
                    next_index=1,
                    delay=5.0,
                    reason=f"{reason}_retry_after_busy_inactive",
                )
            except Exception as exc:
                pass
    else:
        if _ab_abort_stale_replan_if_needed(replan_serial, reason, "before_inactive_clear"):
            return False
        # No next row exists.  Remove the inactive deck from the Python plan;
        # the native consumed/terminal lifecycle prevents stale playback, and a
        # future clear_slot load replaces the descriptor atomically.
        ok_inactive = True
        clear_resp = "native_no_next_descriptor"
    return bool(ok_inactive)

def _ab_schedule_async_replan(reason: str, delay: float = 0.0) -> bool:
    """Schedule a queue-mutation A/B replan without blocking the caller.

    AutoDJ refills must remain fast DB operations.  The inactive A/B deck may
    still need a slow preload or SoundFile analysis, but that work must not hold the
    AutoDJ loop and delay filling the rest of the queue.
    """
    try:
        delay = max(0.0, float(delay or 0.0))
        reason_text = str(reason or "queue_mutation_async")
        scheduled_station = _ab_runtime_station_key()
        if scheduled_station == "__default__":
            scheduled_station = ""

        def _run_async_replan() -> None:
            try:
                if delay > 0.0:
                    time.sleep(delay)
                if not scheduled_station:
                    return
                with station_runtime_context(scheduled_station):
                    if not bool(_native_station_state(scheduled_station).get("running")):
                        return
                    _ab_replan_after_queue_mutation(reason=reason_text)
            except Exception as exc:
                pass

        t = threading.Thread(target=_run_async_replan, daemon=True)
        t.start()
        return True
    except Exception:
        return False

def _sync_reload_and_rebootstrap_after_queue_mutation(
    reason: str = "queue_mutation",
    *,
    async_replan: bool = False,
) -> None:
    """Apply a DB queue mutation directly to the native inactive deck."""
    try:
        _ab_invalidate_pending_replans(reason=f"queue_mutation:{reason or ''}")
    except Exception:
        pass
    try:
        manual_guard_remaining = _ab_manual_next_guard_remaining()
        if manual_guard_remaining > 0.0:
            _ab_schedule_deferred_replan(reason, manual_guard_remaining + 0.25)
            return
        if async_replan:
            _ab_schedule_async_replan(reason)
            return
        _ab_replan_after_queue_mutation(reason=reason)
    except Exception as exc:
        pass

def _ab_bootstrap_from_queue_plan(
    lines: list[str] | None = None,
    *,
    station_key: str = "",
    hard_select_active: bool = False,
    prepare_only: bool = False,
) -> bool:
    """Load the current DB-backed queue plan into the native A/B engine."""
    del hard_select_active
    station_key = str(station_key or get_active_station_key() or "").strip()
    with _AB_PLAYER_LOCK:
        bootstrap_blocked_by_stop = bool((_AB_PLAYER_STATE or {}).get("stopping"))
    native_state = _native_station_state(station_key) if station_key else {}
    if bootstrap_blocked_by_stop or not bool(native_state.get("running")):
        return False

    plan = list(lines) if lines is not None else _build_station_queue_plan(station_key)
    if not plan:
        return False

    durations: list[float] = []
    fadeouts: list[float] = []
    for line in plan:
        duration, fadeout = _ab_line_duration_and_fade(line)
        durations.append(duration)
        fadeouts.append(fadeout)

    with _AB_PLAYER_LOCK:
        next_generation = int(_AB_PLAYER_STATE.get("generation") or 0) + 1
        _AB_PLAYER_STATE.clear()
        _AB_PLAYER_STATE.update({
            "enabled": True,
            "station_key": station_key,
            "active": "a",
            "lines": plan,
            "durations": durations,
            "fadeouts": fadeouts,
            "current_index": 0,
            "next_index": 1 if len(plan) > 1 else 0,
            "player_index": {"a": 0, "b": 1 if len(plan) > 1 else 0},
            "player_loaded_keys": {},
            "player_generation": {},
            "started_at": time.time(),
            "transitioning": False,
            "transition_started_at": 0.0,
            "transition_duration": 0.0,
            "transition_not_before": 0.0,
            "transition_target": "",
            "transition_from": "",
            "transition_starting": False,
            "generation": next_generation,
            "last_seed_key": "",
            "last_commit_key": "",
        })

    try:
        with station_runtime_context(station_key):
            ok_a = bool(_ab_push("a", plan[0], clear_slot=True))
            ok_b = True
            if len(plan) > 1:
                ok_b = bool(_ab_push("b", plan[1], clear_slot=True))
            if not (ok_a and ok_b):
                raise RuntimeError(f"native deck load failed: A={ok_a} B={ok_b}")

            if prepare_only:
                return True

            ready_a, ready_state, ready_reason = _ab_wait_for_native_deck_prebuffer(
                "a",
                plan[0],
                station_key=station_key,
                timeout_sec=4.0,
            )
            if not ready_a:
                raise RuntimeError(
                    "native deck A prebuffer was not ready before select: "
                    f"reason={ready_reason} state={ready_state}"
                )

            selected = _ab_select("a", timeout_sec=3.0)
            if isinstance(selected, dict) and not bool(selected.get("accepted", True)):
                raise RuntimeError(f"native deck A selection rejected: {selected}")

        started_at = time.time()
        with _AB_PLAYER_LOCK:
            _AB_PLAYER_STATE["started_at"] = started_at
            _AB_PLAYER_STATE["active"] = "a"

        _ensure_ab_monitor_thread()
        return True
    except Exception as exc:
        with _AB_PLAYER_LOCK:
            _AB_PLAYER_STATE["enabled"] = False
            _AB_PLAYER_STATE["last_load_error"] = str(exc)
        return False


def _ab_song_from_line(uri: str) -> dict:
    info = _ab_line_info(uri)
    return {
        "file": normalize_media_path(str(info.get("file") or "")),
        "path": normalize_media_path(str(info.get("file") or "")),
        "title": str(info.get("title") or ""),
        "artist": str(info.get("artist") or ""),
        "album": str(info.get("album") or ""),
        "queue_id": int(info.get("queue_id") or 0),
        "track_id": int(info.get("track_id") or 0),
        "play_token": str(info.get("slot_token") or "").strip(),
        "_no_head_fallback": True,
    }


def _ab_commit_started_track(station_key: str, uri: str, *, reason: str = "ab_track_started") -> bool:
    """Atomically move an A/B track to history when it becomes audible.

    In A/B mode the DB queue must represent upcoming items only. Therefore the
    active item is dequeued and inserted into history when playback starts
    (bootstrap or transition start), not when the previous track finishes.
    Duplicate callbacks are identified by queue_id plus the transient A/B slot
    token whenever the native daemon supplies it. The key is stored only after a
    successful dequeue so a failed commit remains retryable.
    """
    sk = str(station_key or get_active_station_key() or "").strip()
    song = _ab_song_from_line(uri)
    if not sk or not (song.get("queue_id") or song.get("track_id") or song.get("file")):
        return False
    try:
        queue_id = int(song.get("queue_id") or 0)
        track_id = int(song.get("track_id") or 0)
        play_token = str(song.get("play_token") or "").strip()
        path = normalize_media_path(str(song.get("file") or ""))
        commit_key = f"{queue_id}:{play_token}" if queue_id > 0 and play_token else f"{queue_id}:{track_id}:{path}"
        queue_commit_key = f"{_normalize_station_key(sk)}:{queue_id}" if queue_id > 0 else ""
        with _AB_PLAYER_LOCK:
            if (
                commit_key and commit_key == str(_AB_PLAYER_STATE.get("last_commit_key") or "")
            ) or (
                queue_commit_key and queue_commit_key == str(_AB_PLAYER_STATE.get("last_commit_queue_key") or "")
            ):
                return True
        ok = dequeue_song_to_history(song, sync_and_reload=False, station_key=sk)
        if ok:
            with _AB_PLAYER_LOCK:
                _AB_PLAYER_STATE["last_commit_key"] = commit_key
                _AB_PLAYER_STATE["last_commit_queue_key"] = queue_commit_key
        return bool(ok)
    except Exception as exc:
        return False


def _ab_find_line_index_by_identity(lines: list[str], *, path: str = "", queue_id: int = 0, track_id: int = 0) -> int:
    """Find an A/B playlist line without confusing adjacent identical files.

    Identity strength is evaluated in separate passes across the complete list:
    exact queue_id first, then a unique track_id + path match, and finally a
    unique path-only match. This prevents an earlier identical file from
    winning before a later exact queue_id is examined.
    """
    try:
        norm_path = normalize_media_path(str(path or ""))
        parsed = []
        for i, line in enumerate(lines or []):
            info = _ab_line_info(line)
            try:
                lq = int(info.get("queue_id") or 0)
                lt = int(info.get("track_id") or 0)
            except Exception:
                lq, lt = 0, 0
            lp = normalize_media_path(str(info.get("file") or ""))
            parsed.append((i, lq, lt, lp))

        if queue_id:
            for i, lq, _lt, _lp in parsed:
                if lq == int(queue_id):
                    return i

        if track_id and norm_path:
            matches = [i for i, _lq, lt, lp in parsed if lt == int(track_id) and lp == norm_path]
            if len(matches) == 1:
                return matches[0]

        if norm_path:
            matches = [i for i, _lq, _lt, lp in parsed if lp == norm_path]
            if len(matches) == 1:
                return matches[0]
        return -1
    except Exception:
        return -1



def _ab_start_cueout_transition_now(station_key: str, *, active: str, target: str, current_index: int, target_index: int, fade: float, generation: int = 0, reason: str = "", token: int = 0, manual_next_fast: bool = False, hard_handoff: bool = False, no_crossfade_handoff: bool = False, manual_next_request_id: str = "") -> bool:
    """Start an A/B cue-out transition and make the target authoritative immediately.

    This is used by both the normal monitor and the seek-after-near-EOF watchdog.
    It intentionally starts the native transition before preloading the old
    player, because the target player must already be the next audible source.
    """
    try:
        active = "a" if str(active).lower().endswith("a") else "b"
        target = "a" if str(target).lower().endswith("a") else "b"
        fade_raw = max(0.0, float(fade or 0.0))
        hard_select = bool(manual_next_fast or hard_handoff or no_crossfade_handoff)
        fade = fade_raw if hard_select else max(0.05, fade_raw)
        station_key = str(station_key or get_active_station_key() or "")
        claimed_transition = False
        with _AB_PLAYER_LOCK:
            st0 = dict(_AB_PLAYER_STATE or {})
            lines = list(st0.get("lines") or [])
            player_index = dict(st0.get("player_index") or {})
            if not bool(st0.get("enabled")) or not lines:
                return False
            if bool(st0.get("hard_handoff_armed")) and not bool(manual_next_fast):
                return True
            if bool(st0.get("seek_pending")) and not bool(manual_next_fast):
                return True
            # v2436: timer + watchdog can wake on the same deadline. Claim the
            # transition before any telnet command so only one caller can issue
            # ab.to_*; duplicate ab.to_* calls caused state/audio races.
            if bool(st0.get("transitioning")) or bool(st0.get("transition_starting")):
                return False
            if str(st0.get("active") or "a").lower() != active:
                return False
            if target_index < 0 or target_index >= len(lines) or target_index == current_index:
                target_index = int(current_index) + 1
            if target_index < 0 or target_index >= len(lines):
                return False
            target_line = lines[target_index]
            _AB_PLAYER_STATE["transition_starting"] = True
            _AB_PLAYER_STATE["pending_cueout_transition"] = False
            _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
            _AB_PLAYER_STATE["pending_cueout_token"] = 0
            claimed_transition = True
        with _AB_PLAYER_LOCK:
            loaded_index = int((dict(_AB_PLAYER_STATE.get("player_index") or {})).get(target, -1))
            loaded_keys_now = dict(_AB_PLAYER_STATE.get("player_loaded_keys") or {})
            loaded_key_now = str(loaded_keys_now.get(target) or "")
            try:
                loaded_generation = int((dict(_AB_PLAYER_STATE.get("player_generation") or {})).get(target))
            except Exception:
                loaded_generation = None
        need_push = bool(loaded_index != target_index or (loaded_generation is not None and loaded_generation != int(generation or 0)))
        if hard_select:
            try:
                target_info_for_key = _ab_line_info(lines[target_index])
                target_path_for_key = normalize_media_path(str(target_info_for_key.get("file") or _ab_line_path(lines[target_index]) or ""))
                expected_key_for_target = f'{int(target_info_for_key.get("queue_id") or 0)}:{int(target_info_for_key.get("track_id") or 0)}:{target_path_for_key}'
            except Exception:
                expected_key_for_target = ""
            try:
                native_target_matches, _native_key, native_target_queue_id, native_target_slot_token = _ab_native_deck_matches_line(
                    _native_station_state(station_key),
                    target,
                    lines[target_index],
                )
            except Exception:
                native_target_matches = False
                native_target_queue_id = 0
                native_target_slot_token = ""
            # The concrete queue/track/path identity is authoritative.  A queue
            # replan may advance the bookkeeping generation while the exact
            # target remains correctly prebuffered on the physical deck.
            # Reloading that ready deck at audio_end adds an avoidable silence.
            need_push = not bool(
                loaded_index == target_index
                and expected_key_for_target
                and loaded_key_now == expected_key_for_target
                and native_target_matches
            )
            if not need_push:
                _preload_reuse_trace(
                    "ab_hard_select_reused_native_preload",
                    station_key=station_key,
                    deck=str(target).upper(),
                    queue_id=int(native_target_queue_id or 0),
                    slot_token=str(native_target_slot_token or ""),
                    reason=reason,
                    target_index=int(target_index),
                    manual_next_fast=bool(manual_next_fast),
                    manual_next_request_id=str(manual_next_request_id or ""),
                )
        if need_push:
            if not _ab_push(target, lines[target_index], attempts=(8 if hard_select else 3), retry_delay=0.05, clear_slot=True, manual_next_fast=manual_next_fast):
                with _AB_PLAYER_LOCK:
                    if claimed_transition:
                        _AB_PLAYER_STATE["transition_starting"] = False
                return False
        with _AB_PLAYER_LOCK:
            st1 = dict(_AB_PLAYER_STATE or {})
            if bool(st1.get("transitioning")) or str(st1.get("active") or "a").lower() != active:
                if claimed_transition:
                    _AB_PLAYER_STATE["transition_starting"] = False
                return False
        if hard_select:
            # Operator NEXT, terminal native EOF and short_no_crossfade audio-end
            # handoffs are destructive hard switches, not zero-second overlap
            # transitions.  The prepared target is selected directly so neither
            # an artificial entry ramp nor a monitor/EOF silence is inserted.
            # v2816: operator NEXT is a destructive hard switch, not a zero-second
            # overlap transition. A zero-duration transition still leaves the engine in
            # transition=true and protects the outgoing deck as
            # old_deck_still_audible until its natural cue/audio end.  During
            # rapid NEXT testing that blocks the opposite deck from being loaded
            # and the button appears to do nothing until a manual seek force-
            # completes the stale transition.  Use ab.select_* for manual NEXT so
            # the native select immediately releases the old deck and the next NEXT can
            # reuse it right away.
            if manual_next_fast:
                try:
                    select_info = _ab_line_info(lines[target_index])
                except Exception:
                    select_info = {}
                _manual_next_trace(
                    "manual_next_select_dispatched",
                    station_key,
                    str(manual_next_request_id or ""),
                    target_deck=str(target).upper(),
                    target_queue_id=int(select_info.get("queue_id") or 0),
                    target_track_id=int(select_info.get("track_id") or 0),
                    generation=int(generation or 0),
                )
            _ab_select(target)
        else:
            _ab_transition_to(target, fade)
        started_line = lines[target_index]
        target_commit_ok = False  # authoritative native track_started worker commits it

        final_lines = list(lines)
        final_current_index = int(target_index)
        final_next_index = (target_index + 1) if (target_index + 1) < len(final_lines) else target_index
        final_durations = None
        final_fadeouts = None
        rebuilt_tail = False
        if manual_next_fast and target_commit_ok:
            try:
                fresh_tail = _build_station_queue_plan(station_key)
                final_lines = [started_line] + list(fresh_tail or [])
                final_current_index = 0
                final_next_index = 1 if len(final_lines) > 1 else 0
                final_durations = []
                final_fadeouts = []
                for _ln_final in final_lines:
                    _d_final, _f_final = _ab_line_duration_and_fade(_ln_final)
                    final_durations.append(_d_final)
                    final_fadeouts.append(_f_final)
                rebuilt_tail = True
            except Exception as _mn_tail_exc:
                pass
        if final_durations is None or final_fadeouts is None:
            final_durations = []
            final_fadeouts = []
            for _ln_final in final_lines:
                _d_final, _f_final = _ab_line_duration_and_fade(_ln_final)
                final_durations.append(_d_final)
                final_fadeouts.append(_f_final)

        transition_now = time.time()
        with _AB_PLAYER_LOCK:
            pi = dict(_AB_PLAYER_STATE.get("player_index") or {})
            if manual_next_fast:
                pi = {target: final_current_index}
            else:
                pi[target] = final_current_index
            _AB_PLAYER_STATE["active"] = target
            _AB_PLAYER_STATE["current_index"] = final_current_index
            _AB_PLAYER_STATE["started_at"] = transition_now
            _AB_PLAYER_STATE["transitioning"] = False if hard_select else True
            _AB_PLAYER_STATE["transition_started_at"] = 0.0 if hard_select else transition_now
            _AB_PLAYER_STATE["transition_duration"] = 0.0 if hard_select else fade
            _AB_PLAYER_STATE["transition_target"] = "" if hard_select else target
            _AB_PLAYER_STATE["transition_from"] = "" if hard_select else active
            _AB_PLAYER_STATE["transition_from_line"] = "" if hard_select else (lines[current_index] if 0 <= current_index < len(lines) else "")
            _AB_PLAYER_STATE["transition_to_line"] = "" if hard_select else started_line
            _AB_PLAYER_STATE["lines"] = final_lines
            _AB_PLAYER_STATE["durations"] = final_durations
            _AB_PLAYER_STATE["fadeouts"] = final_fadeouts
            _AB_PLAYER_STATE["next_index"] = final_next_index
            _AB_PLAYER_STATE["player_index"] = pi
            if manual_next_fast:
                lk = dict(_AB_PLAYER_STATE.get("player_loaded_keys") or {})
                pg = dict(_AB_PLAYER_STATE.get("player_generation") or {})
                try:
                    _started_info = _ab_line_info(started_line)
                    _started_path = normalize_media_path(str(_started_info.get("file") or _ab_line_path(started_line) or ""))
                    _started_key = f'{int(_started_info.get("queue_id") or 0)}:{int(_started_info.get("track_id") or 0)}:{_started_path}'
                except Exception:
                    _started_key = ""
                if _started_key:
                    lk[target] = _started_key
                    pg[target] = int(generation or 0)
                lk.pop(active, None)
                pg.pop(active, None)
                _AB_PLAYER_STATE["player_loaded_keys"] = lk
                _AB_PLAYER_STATE["player_generation"] = pg
                _AB_PLAYER_STATE["manual_next_until"] = max(float(_AB_PLAYER_STATE.get("manual_next_until") or 0.0), time.time() + 2.0)
            _AB_PLAYER_STATE["transition_starting"] = False
            _AB_PLAYER_STATE["pending_cueout_transition"] = False
            _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
            _AB_PLAYER_STATE["pending_cueout_token"] = 0
        # Now Playing/history/queue mutation is owned by native track_started.
        if manual_next_fast and target_commit_ok and final_next_index > final_current_index and final_next_index < len(final_lines):
            try:
                _next_line_after_manual = final_lines[final_next_index]
                _preload_delay_manual = _ab_preload_delay_for_active_line(started_line)
                _ab_schedule_inactive_preload_after_start(
                    station_key,
                    active_player=target,
                    inactive_player=active,
                    line=_next_line_after_manual,
                    next_index=final_next_index,
                    delay=_preload_delay_manual,
                    urgent=(_preload_delay_manual <= 0.05),
                    reason="manual_next_hard_select_followup_preload",
                )
            except Exception as _mn_preload_exc:
                pass
        return True
    except Exception as exc:
        try:
            with _AB_PLAYER_LOCK:
                _AB_PLAYER_STATE["transition_starting"] = False
        except Exception:
            pass
        return False


def _ab_native_deck_identity(
    native_state: dict,
    player: str,
    fallback_info: dict | None = None,
) -> tuple[int, str]:
    """Return the daemon's immutable queue/token identity for one physical deck."""
    state = dict(native_state or {})
    info = dict(fallback_info or {})
    deck = "a" if str(player).lower().endswith("a") else "b"
    active = str(state.get("active_deck") or "").strip().lower()
    queue_candidates = [
        state.get(f"native_audio_deck_{deck}_queue_id"),
        state.get(f"deck_{deck}_queue_id"),
    ]
    token_candidates = [
        state.get(f"native_audio_deck_{deck}_slot_token"),
        state.get(f"deck_{deck}_slot_token"),
    ]
    if active == deck:
        queue_candidates.extend((
            state.get("native_audio_probe_queue_id"),
            state.get("queue_id"),
        ))
        token_candidates.extend((
            state.get("native_audio_probe_slot_token"),
            state.get("slot_token"),
        ))
    queue_candidates.append(info.get("queue_id"))
    token_candidates.append(info.get("slot_token"))
    queue_id = 0
    for value in queue_candidates:
        try:
            candidate = int(value or 0)
        except Exception:
            candidate = 0
        if candidate > 0:
            queue_id = candidate
            break
    slot_token = ""
    for value in token_candidates:
        candidate = str(value or "").strip()
        if candidate:
            slot_token = candidate
            break
    return queue_id, slot_token


def _ab_hard_handoff_identity_matches(
    expected_queue_id: int,
    expected_slot_token: str,
    live_queue_id: int,
    live_slot_token: str,
) -> bool:
    try:
        expected_queue_id = int(expected_queue_id or 0)
        live_queue_id = int(live_queue_id or 0)
    except Exception:
        return False
    expected_slot_token = str(expected_slot_token or "").strip()
    live_slot_token = str(live_slot_token or "").strip()
    if expected_queue_id <= 0 or live_queue_id != expected_queue_id:
        return False
    if not expected_slot_token or not live_slot_token:
        return False
    return live_slot_token == expected_slot_token


def _ab_native_hard_handoff_state_valid(
    snapshot: dict,
    native_state: dict,
    *,
    station_key: str,
    active: str,
    target: str,
    current_info: dict,
    target_info: dict,
) -> bool:
    """Validate an armed handoff without mutable playlist indexes/generations.

    Once the native mixer accepts a handoff, ownership is tied to the exact
    physical deck plus queue_id/slot_token identities. Queue replans may change
    Python generation and indexes while those two native deck slots remain the
    same; those bookkeeping changes must never re-arm or fall back to select.
    """
    st = dict(snapshot or {})
    if not bool(st.get("hard_handoff_armed")):
        return False
    normalized_station = _ab_monitor_wake_key(station_key)
    stored_station = _ab_monitor_wake_key(str(st.get("hard_handoff_station_key") or station_key))
    active = "a" if str(active).lower().endswith("a") else "b"
    target = "a" if str(target).lower().endswith("a") else "b"
    if stored_station != normalized_station:
        return False
    if str(st.get("hard_handoff_active") or "") != active:
        return False
    if str(st.get("hard_handoff_target") or "") != target:
        return False

    # A claimed terminal EOF or completed boundary is already mixer-owned.
    # Keep Python completely hands-off until the track_started lifecycle rebuild
    # resets the state (or the generous real failure deadline expires).
    if bool(st.get("hard_handoff_native_claimed")) or bool(st.get("hard_handoff_completed")):
        return True

    expected_from_q = int(st.get("hard_handoff_from_queue_id") or 0)
    expected_from_t = str(st.get("hard_handoff_from_slot_token") or "")
    expected_to_q = int(st.get("hard_handoff_to_queue_id") or 0)
    expected_to_t = str(st.get("hard_handoff_to_slot_token") or "")
    live_from_q, live_from_t = _ab_native_deck_identity(native_state, active, current_info)
    live_to_q, live_to_t = _ab_native_deck_identity(native_state, target, target_info)
    return bool(
        _ab_hard_handoff_identity_matches(expected_from_q, expected_from_t, live_from_q, live_from_t)
        and _ab_hard_handoff_identity_matches(expected_to_q, expected_to_t, live_to_q, live_to_t)
    )


def _ab_mark_native_hard_handoff_claimed(event) -> bool:
    """Mark token-scoped early EOF as owned by the already armed native mixer."""
    station_key = str(getattr(event, "station_key", "") or "").strip()
    payload = dict(getattr(event, "payload", {}) or {})
    if not station_key or not bool(payload.get("hard_handoff_claimed")):
        return False
    event_deck = "a" if str(getattr(event, "deck", "")).lower().endswith("a") else "b"
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_token = str(getattr(event, "slot_token", "") or "").strip()
    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            if not bool(_AB_PLAYER_STATE.get("hard_handoff_armed")):
                return False
            if str(_AB_PLAYER_STATE.get("hard_handoff_active") or "") != event_deck:
                return False
            if not _ab_hard_handoff_identity_matches(
                int(_AB_PLAYER_STATE.get("hard_handoff_from_queue_id") or 0),
                str(_AB_PLAYER_STATE.get("hard_handoff_from_slot_token") or ""),
                event_queue_id,
                event_token,
            ):
                return False
            _AB_PLAYER_STATE["hard_handoff_native_claimed"] = True
            _AB_PLAYER_STATE["hard_handoff_completion_source"] = str(getattr(event, "event", "") or "native_terminal_eof")
            return True


def _ab_mark_native_hard_handoff_completed(event) -> bool:
    """Latch native boundary completion before the asynchronous lifecycle runs."""
    station_key = str(getattr(event, "station_key", "") or "").strip()
    payload = dict(getattr(event, "payload", {}) or {})
    if not station_key or str(payload.get("source") or "") != "native_hard_handoff_boundary":
        return False
    event_deck = "a" if str(getattr(event, "deck", "")).lower().endswith("a") else "b"
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_token = str(getattr(event, "slot_token", "") or "").strip()
    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            if not bool(_AB_PLAYER_STATE.get("hard_handoff_armed")):
                return False
            if str(_AB_PLAYER_STATE.get("hard_handoff_target") or "") != event_deck:
                return False
            if not _ab_hard_handoff_identity_matches(
                int(_AB_PLAYER_STATE.get("hard_handoff_to_queue_id") or 0),
                str(_AB_PLAYER_STATE.get("hard_handoff_to_slot_token") or ""),
                event_queue_id,
                event_token,
            ):
                return False
            _AB_PLAYER_STATE["hard_handoff_completed"] = True
            _AB_PLAYER_STATE["hard_handoff_completion_source"] = "native_hard_handoff_boundary"
            # Keep the latch alive while the ordered lifecycle worker commits the
            # queue/history and rebuilds the A/B plan.
            _AB_PLAYER_STATE["hard_handoff_deadline"] = max(
                float(_AB_PLAYER_STATE.get("hard_handoff_deadline") or 0.0),
                time.time() + 2.0,
            )
            return True



def _ab_seek_event_identity_matches(event, snapshot: dict) -> bool:
    """Match one native seek event to the exact audible deck transaction."""
    st = dict(snapshot or {})
    event_deck = "a" if str(getattr(event, "deck", "")).lower().endswith("a") else "b"
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_token = str(getattr(event, "slot_token", "") or "").strip()
    expected_active = str(st.get("seek_pending_active") or st.get("active") or "a").lower()
    if expected_active not in ("a", "b") or event_deck != expected_active:
        return False
    expected_queue_id = int(st.get("seek_pending_queue_id") or 0)
    expected_token = str(st.get("seek_pending_slot_token") or "").strip()
    if expected_queue_id > 0 and event_queue_id != expected_queue_id:
        return False
    if expected_token and event_token != expected_token:
        return False
    if expected_queue_id <= 0 and not expected_token:
        # A track_seeked event can win the race with the HTTP handler. Fall back
        # to the currently planned audible line, but still require a real queue.
        lines = list(st.get("lines") or [])
        player_index = dict(st.get("player_index") or {})
        try:
            current_index = int(player_index.get(event_deck, st.get("current_index") or 0))
        except Exception:
            current_index = -1
        if current_index < 0 or current_index >= len(lines):
            return False
        current_info = _ab_line_info(lines[current_index])
        planned_queue_id = int(current_info.get("queue_id") or 0)
        return bool(planned_queue_id > 0 and planned_queue_id == event_queue_id and event_token)
    return bool(event_queue_id > 0 and event_token)


def _ab_mark_native_seek_pending(event) -> bool:
    """Freeze cue/transition timing until the seeked decoder emits real PCM."""
    station_key = str(getattr(event, "station_key", "") or "").strip()
    if not station_key:
        return False
    event_deck = "a" if str(getattr(event, "deck", "")).lower().endswith("a") else "b"
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_token = str(getattr(event, "slot_token", "") or "").strip()
    event_wall_time = float(getattr(event, "wall_time_unix_ms", 0) or 0) / 1000.0
    if event_wall_time <= 0.0:
        event_wall_time = time.time()
    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            snapshot = dict(_AB_PLAYER_STATE or {})
            if not bool(snapshot.get("enabled")):
                return False
            if bool(snapshot.get("seek_pending")) and not _ab_seek_event_identity_matches(event, snapshot):
                return False
            if not bool(snapshot.get("seek_pending")):
                # Validate the first event against the audible planner slot.
                provisional = dict(snapshot)
                provisional["seek_pending_active"] = event_deck
                provisional["seek_pending_queue_id"] = 0
                provisional["seek_pending_slot_token"] = ""
                if not _ab_seek_event_identity_matches(event, provisional):
                    return False
            _AB_PLAYER_STATE["seek_pending"] = True
            _AB_PLAYER_STATE["seek_pending_active"] = event_deck
            _AB_PLAYER_STATE["seek_pending_queue_id"] = event_queue_id
            _AB_PLAYER_STATE["seek_pending_slot_token"] = event_token
            _AB_PLAYER_STATE["seek_pending_deadline"] = max(
                float(_AB_PLAYER_STATE.get("seek_pending_deadline") or 0.0),
                event_wall_time + 10.0,
            )
            _AB_PLAYER_STATE["transitioning"] = False
            _AB_PLAYER_STATE["transition_starting"] = False
            _AB_PLAYER_STATE["transition_started_at"] = 0.0
            _AB_PLAYER_STATE["transition_duration"] = 0.0
            _AB_PLAYER_STATE["transition_target"] = ""
            _AB_PLAYER_STATE["transition_from"] = ""
    return True


def _ab_mark_native_seek_applied(event) -> bool:
    """Re-anchor the A/B clock to the first real seeked PCM position."""
    station_key = str(getattr(event, "station_key", "") or "").strip()
    if not station_key:
        return False
    payload = dict(getattr(event, "payload", {}) or {})
    source_position = max(0.0, float(payload.get("source_position_ms") or 0) / 1000.0)
    applied_at = float(getattr(event, "wall_time_unix_ms", 0) or 0) / 1000.0
    if applied_at <= 0.0:
        applied_at = time.time()
    event_deck = "a" if str(getattr(event, "deck", "")).lower().endswith("a") else "b"
    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            snapshot = dict(_AB_PLAYER_STATE or {})
            if not bool(snapshot.get("seek_pending")):
                return False
            if not _ab_seek_event_identity_matches(event, snapshot):
                return False
            lines = list(snapshot.get("lines") or [])
            player_index = dict(snapshot.get("player_index") or {})
            try:
                current_index = int(player_index.get(event_deck, snapshot.get("current_index") or 0))
            except Exception:
                current_index = -1
            current_info = _ab_line_info(lines[current_index]) if 0 <= current_index < len(lines) else {}
            play_start = max(0.0, float(current_info.get("cue_in") or current_info.get("audio_start") or 0.0))
            segment_elapsed = max(0.0, source_position - play_start)
            _AB_PLAYER_STATE["active"] = event_deck
            _AB_PLAYER_STATE["started_at"] = applied_at - segment_elapsed
            _AB_PLAYER_STATE["transition_not_before"] = applied_at + 0.02
            _AB_PLAYER_STATE["seek_pending"] = False
            _AB_PLAYER_STATE["seek_pending_active"] = ""
            _AB_PLAYER_STATE["seek_pending_queue_id"] = 0
            _AB_PLAYER_STATE["seek_pending_slot_token"] = ""
            _AB_PLAYER_STATE["seek_pending_deadline"] = 0.0
            _AB_PLAYER_STATE["seek_applied_at"] = applied_at
            _AB_PLAYER_STATE["seek_applied_source_position"] = source_position
        try:
            with NOW_PLAYING_LOCK:
                store = _get_now_playing_store(station_key)
                store["manual_seek_anchor_abs_seconds"] = source_position
                store["manual_seek_anchor_at"] = applied_at
                store["elapsed"] = source_position
                store["updated_at"] = applied_at
            with PROGRESS_LOCK:
                progress = _get_progress_state(station_key)
                progress["last_source_elapsed"] = source_position
                progress["last_elapsed"] = source_position
                progress["last_ui_raw_elapsed"] = max(0.0, source_position - play_start)
                progress["recent_track_started_at"] = applied_at - source_position
        except Exception:
            pass
    return True


def _ab_clear_native_seek_pending_state(*, reason: str = "") -> None:
    with _AB_PLAYER_LOCK:
        was_pending = bool(_AB_PLAYER_STATE.get("seek_pending"))
        station_key = str(_AB_PLAYER_STATE.get("station_key") or "")
        _AB_PLAYER_STATE["seek_pending"] = False
        _AB_PLAYER_STATE["seek_pending_active"] = ""
        _AB_PLAYER_STATE["seek_pending_queue_id"] = 0
        _AB_PLAYER_STATE["seek_pending_slot_token"] = ""
        _AB_PLAYER_STATE["seek_pending_deadline"] = 0.0
    if was_pending:
        pass

def _ab_clear_native_hard_handoff_state() -> None:
    with _AB_PLAYER_LOCK:
        _AB_PLAYER_STATE["hard_handoff_armed"] = False
        _AB_PLAYER_STATE["hard_handoff_active"] = ""
        _AB_PLAYER_STATE["hard_handoff_target"] = ""
        _AB_PLAYER_STATE["hard_handoff_current_index"] = -1
        _AB_PLAYER_STATE["hard_handoff_target_index"] = -1
        _AB_PLAYER_STATE["hard_handoff_generation"] = 0
        _AB_PLAYER_STATE["hard_handoff_station_key"] = ""
        _AB_PLAYER_STATE["hard_handoff_from_queue_id"] = 0
        _AB_PLAYER_STATE["hard_handoff_from_slot_token"] = ""
        _AB_PLAYER_STATE["hard_handoff_to_queue_id"] = 0
        _AB_PLAYER_STATE["hard_handoff_to_slot_token"] = ""
        _AB_PLAYER_STATE["hard_handoff_native_claimed"] = False
        _AB_PLAYER_STATE["hard_handoff_completed"] = False
        _AB_PLAYER_STATE["hard_handoff_completion_source"] = ""
        _AB_PLAYER_STATE["hard_handoff_deadline"] = 0.0

def _ab_arm_native_hard_handoff(
    station_key: str,
    *,
    active: str,
    target: str,
    current_index: int,
    target_index: int,
    generation: int,
    remaining_seconds: float,
) -> bool:
    """Prime and arm one mixer-owned zero-fade switch exactly once.

    After the daemon accepts the switch, queue/token identities own the
    transaction. Mutable Python generation and playlist indexes are retained
    only for diagnostics and must not cause re-arming or destructive fallback.
    """
    active = "a" if str(active).lower().endswith("a") else "b"
    target = "a" if str(target).lower().endswith("a") else "b"
    try:
        with _AB_PLAYER_LOCK:
            st = dict(_AB_PLAYER_STATE or {})
            lines = list(st.get("lines") or [])
            player_index = dict(st.get("player_index") or {})
            if (
                not bool(st.get("enabled"))
                or bool(st.get("transitioning"))
                or bool(st.get("transition_starting"))
                or str(st.get("active") or "a").lower() != active
                or target_index < 0
                or target_index >= len(lines)
                or current_index < 0
                or current_index >= len(lines)
            ):
                return False
            current_line = lines[current_index]
            target_line = lines[target_index]
            current_info = _ab_line_info(current_line)
            target_info = _ab_line_info(target_line)
            already_armed = bool(st.get("hard_handoff_armed"))
            loaded_keys = dict(st.get("player_loaded_keys") or {})
            loaded_generations = dict(st.get("player_generation") or {})

        if already_armed:
            # Never hold the A/B state lock while waiting for a native response:
            # the socket reader may need that lock to latch EOF/boundary events.
            native_now = _native_station_state(station_key)
            return _ab_native_hard_handoff_state_valid(
                st,
                native_now,
                station_key=station_key,
                active=active,
                target=target,
                current_info=current_info,
                target_info=target_info,
            )

        target_path = normalize_media_path(
            str(target_info.get("file") or _ab_line_path(target_line) or "")
        )
        expected_key = (
            f'{int(target_info.get("queue_id") or 0)}:'
            f'{int(target_info.get("track_id") or 0)}:{target_path}'
        )
        try:
            loaded_generation = int(loaded_generations.get(target))
        except Exception:
            loaded_generation = None
        loaded_ok = bool(
            int(player_index.get(target, -1)) == int(target_index)
            and expected_key
            and str(loaded_keys.get(target) or "") == expected_key
            and loaded_generation == int(generation or 0)
        )
        if not loaded_ok:
            if not _ab_push(target, target_line, attempts=4, retry_delay=0.04, clear_slot=True):
                return False
            with _AB_PLAYER_LOCK:
                pi = dict(_AB_PLAYER_STATE.get("player_index") or {})
                pi[target] = target_index
                _AB_PLAYER_STATE["player_index"] = pi

        result = _ab_hard_handoff_to(
            target,
            station_key=station_key,
            timeout_sec=1.0,
        )
        native_after = _native_station_state(station_key)
        from_queue_id, from_slot_token = _ab_native_deck_identity(
            native_after, active, current_info
        )
        to_queue_id, to_slot_token = _ab_native_deck_identity(
            native_after, target, target_info
        )
        if not _ab_hard_handoff_identity_matches(
            int(current_info.get("queue_id") or from_queue_id or 0),
            str(current_info.get("slot_token") or from_slot_token or ""),
            from_queue_id,
            from_slot_token,
        ):
            # The playlist line may not carry the transient runtime token. The
            # daemon identity is authoritative as long as its queue row matches.
            if int(from_queue_id or 0) != int(current_info.get("queue_id") or 0):
                raise RuntimeError("native_hard_handoff_outgoing_identity_mismatch")
        if int(to_queue_id or 0) != int(target_info.get("queue_id") or 0):
            raise RuntimeError("native_hard_handoff_target_identity_mismatch")
        if not from_slot_token or not to_slot_token:
            raise RuntimeError("native_hard_handoff_slot_identity_unavailable")

        armed_at = time.time()
        scheduled_delay = 0.0
        if isinstance(result, dict):
            try:
                scheduled_ms = int(result.get("handoff_at_monotonic_ms") or 0)
                if scheduled_ms > 0:
                    scheduled_delay = max(0.0, (scheduled_ms - int(time.monotonic() * 1000.0)) / 1000.0)
            except Exception:
                scheduled_delay = 0.0
        # Include real audible pipeline delay and a generous lifecycle margin.
        # This is a genuine fault timeout, not a cue timing fallback.
        deadline = armed_at + max(5.0, scheduled_delay + 3.0, float(remaining_seconds or 0.0) + 3.0)
        with _AB_PLAYER_LOCK:
            if str(_AB_PLAYER_STATE.get("active") or "a").lower() != active:
                return False
            _AB_PLAYER_STATE["hard_handoff_armed"] = True
            _AB_PLAYER_STATE["hard_handoff_active"] = active
            _AB_PLAYER_STATE["hard_handoff_target"] = target
            _AB_PLAYER_STATE["hard_handoff_current_index"] = int(current_index)
            _AB_PLAYER_STATE["hard_handoff_target_index"] = int(target_index)
            _AB_PLAYER_STATE["hard_handoff_generation"] = int(generation or 0)
            _AB_PLAYER_STATE["hard_handoff_station_key"] = _ab_monitor_wake_key(station_key)
            _AB_PLAYER_STATE["hard_handoff_from_queue_id"] = int(from_queue_id or 0)
            _AB_PLAYER_STATE["hard_handoff_from_slot_token"] = str(from_slot_token or "")
            _AB_PLAYER_STATE["hard_handoff_to_queue_id"] = int(to_queue_id or 0)
            _AB_PLAYER_STATE["hard_handoff_to_slot_token"] = str(to_slot_token or "")
            _AB_PLAYER_STATE["hard_handoff_native_claimed"] = False
            _AB_PLAYER_STATE["hard_handoff_completed"] = False
            _AB_PLAYER_STATE["hard_handoff_completion_source"] = ""
            _AB_PLAYER_STATE["hard_handoff_deadline"] = deadline
        return True
    except Exception as exc:
        _ab_clear_native_hard_handoff_state()
        return False

def _ab_no_crossfade_monitor_sleep_seconds(
    source_position: float,
    transition_at: float,
    *,
    command_lead_seconds: float = 0.004,
) -> float:
    """Return an adaptive sleep that arms a zero-gap hard handoff at audio_end.

    Normal A/B transitions can tolerate the monitor's 100 ms cadence because
    they open before the outgoing end and overlap.  A ``short_no_crossfade``
    target has no overlap at all: waiting for the decoder EOF creates an
    audible block of silence before the prepared ID/spot.  Stay on the cheap
    cadence while far away, then sleep directly to a few milliseconds before
    the analyzed audio_end so the native ``select`` lands on the boundary.
    """
    try:
        remaining = float(transition_at or 0.0) - float(source_position or 0.0)
        lead = max(0.001, min(0.020, float(command_lead_seconds or 0.004)))
    except Exception:
        return 0.10
    if remaining <= lead:
        return 0.0
    if remaining > 0.50:
        return min(0.10, max(0.01, remaining - 0.50))
    return max(0.001, remaining - lead)


def _ab_transition_point_seconds(
    info: dict,
    *,
    hard_clean: bool = False,
    soft_no_crossfade: bool = False,
    fallback_duration: float = 0.0,
) -> float:
    """Return the absolute source position where the next item must start.

    ``cue_in``/``audio_start`` and ``cue_out``/``audio_end`` are absolute
    positions inside the original media file.  They must never be compared to
    a segment-relative wall clock.  For IDs/spots that intentionally suppress
    overlap, the analyzed ``audio_end`` is authoritative so a long silent tail
    is not played to physical EOF.
    """
    item = dict(info or {})

    def _value(name: str) -> float:
        try:
            return max(0.0, float(item.get(name) or 0.0))
        except Exception:
            return 0.0

    play_start = _value("cue_in") or _value("audio_start")
    cue_out = _value("cue_out")
    audio_end = _value("audio_end")
    source_end = _value("orig_total")

    if (hard_clean or soft_no_crossfade) and audio_end > play_start:
        return audio_end
    if cue_out > play_start:
        return cue_out
    if audio_end > play_start:
        return audio_end
    if source_end > play_start:
        return source_end
    return play_start + max(0.0, float(fallback_duration or 0.0))


def _ab_transition_source_position_seconds(
    info: dict,
    *,
    elapsed_segment_seconds: float,
    native_state: dict | None,
    active_player: str,
) -> tuple[float, str]:
    """Resolve the active deck's absolute source position.

    The native decoder position is preferred and identity-checked against the
    active queue/token.  If the daemon state is temporarily unavailable, the
    fallback adds the analyzed play-start offset to the segment-relative wall
    clock.  This keeps normal starts, non-zero ``audio_start`` and native seek
    on the same absolute timeline.
    """
    item = dict(info or {})
    state = dict(native_state or {})

    def _float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _int(value, default: int = 0) -> int:
        try:
            return int(value or 0)
        except Exception:
            return int(default)

    play_start = max(
        0.0,
        _float(item.get("cue_in") or item.get("audio_start") or 0.0),
    )
    fallback_position = play_start + max(0.0, _float(elapsed_segment_seconds))

    expected_player = str(active_player or "a").strip().lower()
    if expected_player not in {"a", "b"}:
        expected_player = "a"
    state_player = str(state.get("active_deck") or "").strip().lower()
    if not state or not bool(state.get("running")) or state_player != expected_player:
        return fallback_position, "segment_wallclock_plus_play_start"

    expected_queue = _int(item.get("queue_id"))
    expected_token = str(item.get("slot_token") or "").strip()

    def _identity_matches(queue_value, token_value) -> bool:
        queue_id = _int(queue_value)
        token = str(token_value or "").strip()
        if expected_queue > 0 and queue_id > 0 and queue_id != expected_queue:
            return False
        if expected_token and token and token != expected_token:
            return False
        return bool(
            (expected_queue > 0 and queue_id == expected_queue)
            or (expected_token and token == expected_token)
            or (expected_queue <= 0 and not expected_token)
        )

    probe_queue = state.get("native_audio_probe_queue_id")
    probe_token = state.get("native_audio_probe_slot_token")
    if "native_audio_probe_position_ms" in state and _identity_matches(probe_queue, probe_token):
        position_ms = max(0, _int(state.get("native_audio_probe_position_ms")))
        return float(position_ms) / 1000.0, "native_audio_probe_position_ms"

    if "position_ms" in state and _identity_matches(state.get("queue_id"), state.get("slot_token")):
        position_ms = max(0, _int(state.get("position_ms")))
        return float(position_ms) / 1000.0, "native_position_ms"

    return fallback_position, "segment_wallclock_plus_play_start"


def _ab_native_terminal_eof_matches_active(
    native_state: dict | None,
    active_info: dict | None,
    active_player: str,
) -> tuple[bool, str]:
    """Return a token-scoped terminal EOF signal for the audible native deck.

    A decoder can finish a few hundred milliseconds before the analyzed
    ``effective_end``.  In that state its absolute position no longer advances,
    so the normal cue/effective-end comparison can never become true.  Treat
    only an exact active deck/queue/token EOF as a handoff trigger; stale EOF
    state from a previously released candidate must never advance the queue.
    """
    state = dict(native_state or {})
    info = dict(active_info or {})

    def _int(value) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    expected_player = str(active_player or "a").strip().lower()
    if expected_player not in {"a", "b"}:
        expected_player = "a"
    state_player = str(state.get("active_deck") or "").strip().lower()
    probe_player = str(state.get("native_audio_probe_deck") or "").strip().lower()
    if state_player != expected_player or probe_player != expected_player:
        return False, "deck_mismatch"

    probe_status = str(state.get("native_audio_probe_status") or "").strip().lower()
    probe_eof = bool(state.get("native_audio_probe_eof")) or probe_status == "eof"
    if not probe_eof:
        return False, probe_status or "not_eof"

    expected_queue = _int(info.get("queue_id"))
    probe_queue = _int(state.get("native_audio_probe_queue_id"))
    expected_token = str(info.get("slot_token") or "").strip()
    probe_token = str(state.get("native_audio_probe_slot_token") or "").strip()

    if expected_queue > 0 and probe_queue != expected_queue:
        return False, "queue_mismatch"
    if expected_token and probe_token != expected_token:
        return False, "token_mismatch"
    if expected_queue <= 0 and not expected_token:
        return False, "missing_expected_identity"
    if probe_queue <= 0 and not probe_token:
        return False, "missing_probe_identity"

    return True, probe_status or "eof"


def _ab_monitor_loop() -> None:
    monitor_wake_station_key = _ab_monitor_wake_key()
    monitor_wake_serial = _ab_monitor_wake_snapshot(monitor_wake_station_key)
    while not _AB_MONITOR_STOP.is_set():
        try:
            with _AB_PLAYER_LOCK:
                st = dict(_AB_PLAYER_STATE)
            if not st.get("enabled") or not st.get("lines"):
                time.sleep(0.2)
                continue
            now = time.time()
            active = str(st.get("active") or "a").lower()
            if active not in ("a", "b"):
                active = "a"
            generation = int(st.get("generation") or 0)
            transition_not_before = float(st.get("transition_not_before") or 0.0)
            durations = st.get("durations") or []
            fadeouts = st.get("fadeouts") or []
            lines = st.get("lines") or []
            player_index = dict(st.get("player_index") or {})
            try:
                current_index = int(player_index.get(active, st.get("current_index") or 0))
            except Exception:
                current_index = int(st.get("current_index") or 0)
            if current_index < 0 or current_index >= len(lines):
                time.sleep(0.2)
                continue
            duration = float(durations[current_index] if current_index < len(durations) else 0.0)
            fade = float(fadeouts[current_index] if current_index < len(fadeouts) else 3.0)
            elapsed = max(0.0, now - float(st.get("started_at") or now))
            monitor_sleep_seconds = 0.10

            # v2844: preserve the established SAM-style transition timing. The outgoing
            # track's cue_out is the crossfade trigger; the target starts at its
            # own Gap Killer cue_in at that exact instant.
            target_for_timing = "b" if active == "a" else "a"
            try:
                target_index_for_timing = int(player_index.get(target_for_timing, st.get("next_index") or (current_index + 1)))
            except Exception:
                target_index_for_timing = current_index + 1
            if target_index_for_timing < 0 or target_index_for_timing >= len(lines) or target_index_for_timing == current_index:
                target_index_for_timing = current_index + 1
            current_line_for_timing = lines[current_index] if 0 <= current_index < len(lines) else ""
            target_line_for_timing = lines[target_index_for_timing] if 0 <= target_index_for_timing < len(lines) else ""
            current_info_for_timing = _ab_line_info(current_line_for_timing) if current_line_for_timing else {}
            target_info_for_timing = _ab_line_info(target_line_for_timing) if target_line_for_timing else {}
            current_cue_out_for_timing = max(0.0, float(current_info_for_timing.get("cue_out") or current_info_for_timing.get("orig_total") or duration or 0.0))
            current_audio_start_for_timing = max(0.0, float(current_info_for_timing.get("audio_start") or current_info_for_timing.get("cue_in") or 0.0))
            hard_clean_for_timing = bool(
                current_info_for_timing.get("hard_clean")
                or target_info_for_timing.get("hard_clean")
                or _ab_parse_webradio_line(target_line_for_timing)
            )
            soft_no_crossfade_for_timing = bool(
                not hard_clean_for_timing
                and (
                    current_info_for_timing.get("short_no_crossfade")
                    or target_info_for_timing.get("short_no_crossfade")
                )
            )
            transition_at = _ab_transition_point_seconds(
                current_info_for_timing,
                hard_clean=hard_clean_for_timing,
                soft_no_crossfade=soft_no_crossfade_for_timing,
                fallback_duration=duration,
            )
            monitor_station_key = str(st.get("station_key") or get_active_station_key() or "")
            native_clock_state = _native_station_state(monitor_station_key)
            if bool(native_clock_state.get("paused")):
                # The native mixer keeps Icecast alive with silence while paused.
                # Freeze every Python-side transition and cue-out watchdog as well.
                time.sleep(0.10)
                continue
            if bool(st.get("seek_pending")):
                pending_active = str(st.get("seek_pending_active") or active).lower()
                pending_queue_id = int(st.get("seek_pending_queue_id") or 0)
                pending_token = str(st.get("seek_pending_slot_token") or "")
                pending_deadline = float(st.get("seek_pending_deadline") or 0.0)
                pending_identity_ok = bool(
                    pending_active == active
                    and pending_queue_id == int(current_info_for_timing.get("queue_id") or 0)
                    and pending_token
                )
                if pending_identity_ok and (pending_deadline <= 0.0 or now <= pending_deadline):
                    monitor_wake_serial, monitor_woken, monitor_wake_reason = _ab_wait_monitor_interruptible(
                        monitor_wake_station_key,
                        monitor_wake_serial,
                        0.05,
                    )
                    if monitor_woken:
                        pass
                    continue
                _ab_clear_native_seek_pending_state(reason="identity_changed_or_timeout")
                st["seek_pending"] = False
            if bool(native_clock_state.get("native_timing_owner")):
                # v5082: local-file cue, fade, hard handoff and terminal EOF timing
                # are exclusively native. Python retains lifecycle/queue duties but
                # must not issue competing select/transition commands.
                monitor_wake_serial, monitor_woken, monitor_wake_reason = _ab_wait_monitor_interruptible(
                    monitor_wake_station_key,
                    monitor_wake_serial,
                    0.10,
                )
                if monitor_woken:
                    pass
                continue
            source_position, transition_clock_source = _ab_transition_source_position_seconds(
                current_info_for_timing,
                elapsed_segment_seconds=elapsed,
                native_state=native_clock_state,
                active_player=active,
            )
            terminal_eof_due, terminal_eof_status = _ab_native_terminal_eof_matches_active(
                native_clock_state,
                current_info_for_timing,
                active,
            )
            no_crossfade_command_lead = 0.004 if soft_no_crossfade_for_timing else 0.030
            transition_due = bool(
                terminal_eof_due
                or (
                    transition_at > 0.0
                    and source_position >= max(0.0, transition_at - no_crossfade_command_lead)
                )
            )
            hard_handoff_state_valid = _ab_native_hard_handoff_state_valid(
                st,
                native_clock_state,
                station_key=monitor_station_key,
                active=active,
                target=target_for_timing,
                current_info=current_info_for_timing,
                target_info=target_info_for_timing,
            )
            if bool(st.get("hard_handoff_armed")) and not hard_handoff_state_valid:
                _ab_clear_native_hard_handoff_state()
            if hard_handoff_state_valid:
                try:
                    hard_handoff_deadline = float(st.get("hard_handoff_deadline") or 0.0)
                except Exception:
                    hard_handoff_deadline = 0.0
                if hard_handoff_deadline > 0.0 and now > hard_handoff_deadline:
                    _ab_clear_native_hard_handoff_state()
                    hard_handoff_state_valid = False
                else:
                    # The native mixer owns both the exact audio_end boundary and
                    # terminal EOF fallback. Never issue the destructive Python
                    # select while this token-scoped handoff is armed.
                    transition_due = False
                    monitor_sleep_seconds = min(0.05, max(0.005, transition_at - source_position))
            if (
                soft_no_crossfade_for_timing
                and not hard_handoff_state_valid
                and transition_at > source_position
                and (transition_at - source_position) <= 2.0
                and 0 <= target_index_for_timing < len(lines)
            ):
                armed = _ab_arm_native_hard_handoff(
                    monitor_station_key,
                    active=active,
                    target=target_for_timing,
                    current_index=current_index,
                    target_index=target_index_for_timing,
                    generation=generation,
                    remaining_seconds=transition_at - source_position,
                )
                if armed:
                    monitor_sleep_seconds = 0.02
                    transition_due = False
                    hard_handoff_state_valid = True
            if soft_no_crossfade_for_timing and not transition_due and not hard_handoff_state_valid and transition_at > source_position:
                monitor_sleep_seconds = _ab_no_crossfade_monitor_sleep_seconds(
                    source_position,
                    transition_at,
                    command_lead_seconds=no_crossfade_command_lead,
                )
            if hard_clean_for_timing or soft_no_crossfade_for_timing:
                # short_no_crossfade means exactly that: no fade on either side
                # and no overlap.  The outgoing item plays to analyzed audio_end,
                # then the already-prebuffered target is selected directly.
                fade = 0.0
            else:
                fade_station_key = str(_parse_annotate_meta(current_line_for_timing).get("station_key") or "").strip() if current_line_for_timing else ""
                fade = max(0.0, float(_ab_get_sam_crossfade_settings(fade_station_key)["crossfade_fade_out_seconds"]))
            # v2435: reliable near-EOF seek handoff watchdog. A seek_abs near
            # cue-out stores an absolute deadline in A/B state. The monitor checks
            # it on every tick, so the transition is not lost if the one-shot
            # sleeper thread is delayed, cancelled by timing, or misses a race.
            if hard_handoff_state_valid and bool(st.get("pending_cueout_transition")):
                with _AB_PLAYER_LOCK:
                    _AB_PLAYER_STATE["pending_cueout_transition"] = False
                    _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
                    _AB_PLAYER_STATE["pending_cueout_token"] = 0
            if (not hard_handoff_state_valid) and (not bool(st.get("transitioning"))) and (not bool(st.get("transition_starting"))) and bool(st.get("pending_cueout_transition")):
                try:
                    pending_deadline = float(st.get("pending_cueout_deadline") or 0.0)
                except Exception:
                    pending_deadline = 0.0
                pending_active = str(st.get("pending_cueout_active") or "").lower()
                pending_target = str(st.get("pending_cueout_target") or ("b" if active == "a" else "a")).lower()
                try:
                    pending_current_index = int(st.get("pending_cueout_current_index") if st.get("pending_cueout_current_index") is not None else current_index)
                except Exception:
                    pending_current_index = current_index
                try:
                    pending_target_index = int(st.get("pending_cueout_target_index") if st.get("pending_cueout_target_index") is not None else (pending_current_index + 1))
                except Exception:
                    pending_target_index = pending_current_index + 1
                try:
                    pending_fade = float(st.get("pending_cueout_fade") or fade or 0.0)
                except Exception:
                    pending_fade = fade
                try:
                    pending_token = int(st.get("pending_cueout_token") or 0)
                except Exception:
                    pending_token = 0
                pending_reason = str(st.get("pending_cueout_reason") or "seek_abs_near_eof")
                if pending_active and pending_active != active:
                    with _AB_PLAYER_LOCK:
                        _AB_PLAYER_STATE["pending_cueout_transition"] = False
                        _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
                elif pending_deadline > 0.0 and now >= pending_deadline:
                    ok_pending = _ab_start_cueout_transition_now(
                        str(st.get("station_key") or get_active_station_key() or ""),
                        active=active,
                        target=pending_target,
                        current_index=pending_current_index,
                        target_index=pending_target_index,
                        fade=pending_fade,
                        generation=int(st.get("pending_cueout_generation") or generation),
                        reason=pending_reason + "_watchdog",
                        token=pending_token,
                    )
                    if ok_pending:
                        time.sleep(0.05)
                        continue
                    else:
                        # Keep it pending for a short grace and retry next tick.
                        with _AB_PLAYER_LOCK:
                            if bool(_AB_PLAYER_STATE.get("pending_cueout_transition")):
                                _AB_PLAYER_STATE["pending_cueout_deadline"] = time.time() + 0.10
            if (not bool(st.get("transitioning"))) and (not bool(st.get("transition_starting"))) and transition_due and len(lines) > 1:
                if transition_not_before > 0.0 and now < transition_not_before:
                    time.sleep(0.05)
                    continue
                target = "b" if active == "a" else "a"
                try:
                    target_index = int(player_index.get(target, st.get("next_index") or (current_index + 1)))
                except Exception:
                    target_index = current_index + 1
                target_index_needs_push = False
                if target_index < 0 or target_index >= len(lines) or target_index == current_index:
                    target_index = current_index + 1
                    if target_index < len(lines):
                        target_index_needs_push = True
                    else:
                        time.sleep(0.2)
                        continue

                if terminal_eof_due:
                    eof_handoff_ok = _ab_start_cueout_transition_now(
                        monitor_station_key,
                        active=active,
                        target=target,
                        current_index=current_index,
                        target_index=target_index,
                        fade=0.0,
                        generation=generation,
                        reason=f"native_terminal_eof:{terminal_eof_status}",
                        hard_handoff=True,
                    )
                    time.sleep(0.05 if eof_handoff_ok else 0.10)
                    continue

                if soft_no_crossfade_for_timing:
                    no_crossfade_ok = _ab_start_cueout_transition_now(
                        monitor_station_key,
                        active=active,
                        target=target,
                        current_index=current_index,
                        target_index=target_index,
                        fade=0.0,
                        generation=generation,
                        reason="short_no_crossfade_audio_end",
                        no_crossfade_handoff=True,
                    )
                    time.sleep(0.01 if no_crossfade_ok else 0.05)
                    continue

                if target_index_needs_push:
                    if _ab_push(target, lines[target_index], attempts=3, retry_delay=0.05, clear_slot=True):
                        player_index[target] = target_index
                else:
                    try:
                        target_loaded_generation = int((dict(_AB_PLAYER_STATE.get("player_generation") or {})).get(target))
                    except Exception:
                        target_loaded_generation = None
                    if target_loaded_generation is not None and target_loaded_generation != int(generation or 0):
                        if _ab_push(target, lines[target_index], attempts=3, retry_delay=0.05, clear_slot=True):
                            player_index[target] = target_index
                target_line_candidate = lines[target_index] if 0 <= target_index < len(lines) else ""
                # v2441: claim the transition and cancel any near-EOF pending
                # cue-out handoff before sending ab.to_*. In v2440 the normal
                # monitor and the seek timer could both issue ab.to_* within the
                # same second, which restarted the target branch and caused the
                # next track intro to jump / sound doubled.
                with _AB_PLAYER_LOCK:
                    if bool(_AB_PLAYER_STATE.get("transitioning")) or bool(_AB_PLAYER_STATE.get("transition_starting")):
                        time.sleep(0.05)
                        continue
                    _AB_PLAYER_STATE["transition_starting"] = True
                    _AB_PLAYER_STATE["pending_cueout_transition"] = False
                    _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
                    _AB_PLAYER_STATE["pending_cueout_token"] = 0
                _ab_transition_to(target, fade)
                target_line_for_commit = lines[target_index] if 0 <= target_index < len(lines) else ""
                target_commit_ok = False  # authoritative native track_started worker commits it
                # v2434: switch the Python/UI A/B state to the target immediately
                # when the native engine starts the crossfade. It can emit the
                # target track's now_playing before the fade duration completes;
                # keeping Python on the old active player until completion made
                # the beginning of the next track look/sound like a small jump.
                with _AB_PLAYER_LOCK:
                    if int(_AB_PLAYER_STATE.get("generation") or 0) != generation:
                        continue
                    _AB_PLAYER_STATE["active"] = target
                    _AB_PLAYER_STATE["current_index"] = target_index
                    _AB_PLAYER_STATE["started_at"] = now
                    _AB_PLAYER_STATE["transitioning"] = True
                    _AB_PLAYER_STATE["transition_started_at"] = now
                    _AB_PLAYER_STATE["transition_duration"] = fade
                    _AB_PLAYER_STATE["transition_target"] = target
                    _AB_PLAYER_STATE["transition_from"] = active
                    _AB_PLAYER_STATE["transition_from_line"] = current_line_for_timing
                    _AB_PLAYER_STATE["transition_to_line"] = target_line_for_timing
                    _AB_PLAYER_STATE["next_index"] = target_index
                    _AB_PLAYER_STATE["player_index"] = player_index
                    _AB_PLAYER_STATE["transition_starting"] = False
                    _AB_PLAYER_STATE["pending_cueout_transition"] = False
                    _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
                    _AB_PLAYER_STATE["pending_cueout_token"] = 0
            elif bool(st.get("transitioning")):
                if now >= float(st.get("transition_started_at") or now) + float(st.get("transition_duration") or 0.0) + 0.25:
                    with _AB_PLAYER_LOCK:
                        if int(_AB_PLAYER_STATE.get("generation") or 0) != generation:
                            time.sleep(0.05)
                            continue
                    # v2434: active was already switched to the target at
                    # transition start, so completion must not invert active again.
                    old = str(st.get("transition_from") or ("b" if active == "a" else "a")).lower()
                    new_active = str(st.get("transition_target") or active or "a").lower()
                    if new_active not in ("a", "b"):
                        new_active = active if active in ("a", "b") else "a"
                    if old not in ("a", "b") or old == new_active:
                        old = "b" if new_active == "a" else "a"
                    try:
                        new_current_index = int(player_index.get(new_active, st.get("current_index") or 0))
                    except Exception:
                        new_current_index = int(st.get("current_index") or 0)
                    try:
                        completed_index = int(player_index.get(old, -1))
                    except Exception:
                        completed_index = -1
                    completed_line = str(st.get("transition_from_line") or "")
                    target_line_at_transition = str(st.get("transition_to_line") or "")
                    if not target_line_at_transition and 0 <= new_current_index < len(lines):
                        target_line_at_transition = lines[new_current_index]
                    # The completed old track was already committed when it started.
                    # Do not dequeue/history it here.  Finalize with exact token-
                    # scoped events instead of a destructive select, because the
                    # old deck may already hold the following preloaded item.
                    commit_ok = _native_sync_transition_completion(
                        str(st.get("station_key") or get_active_station_key() or ""),
                        from_deck=old,
                        from_line=completed_line,
                        to_deck=new_active,
                        to_line=target_line_at_transition,
                    )
                    next_index = new_current_index + 1
                    preload_ok = False
                    if next_index < len(lines):
                        expected_next_info = _ab_line_info(lines[next_index])
                        expected_next_key = f'{int(expected_next_info.get("queue_id") or 0)}:{int(expected_next_info.get("track_id") or 0)}:{normalize_media_path(str(expected_next_info.get("file") or ""))}'
                        loaded_keys_now = dict(st.get("player_loaded_keys") or {})
                        already_loaded = bool(
                            int(player_index.get(old, -1)) == next_index
                            and expected_next_key
                            and str(loaded_keys_now.get(old) or "") == expected_next_key
                        )
                        if already_loaded:
                            preload_ok = True
                        elif _ab_push(old, lines[next_index], attempts=3, retry_delay=0.05, clear_slot=True):
                            player_index[old] = next_index
                            preload_ok = True
                    with _AB_PLAYER_LOCK:
                        # v2439: the target became authoritative at transition start.
                        # Do not reset started_at here; doing so restarts the target
                        # track timeline and creates an audible/visible jump.
                        _AB_PLAYER_STATE["active"] = new_active
                        _AB_PLAYER_STATE["current_index"] = new_current_index
                        _AB_PLAYER_STATE["next_index"] = next_index if next_index < len(lines) else new_current_index
                        _AB_PLAYER_STATE["player_index"] = player_index
                        _AB_PLAYER_STATE["transitioning"] = False
                        _AB_PLAYER_STATE["transition_started_at"] = 0.0
                        _AB_PLAYER_STATE["transition_duration"] = 0.0
                        _AB_PLAYER_STATE["transition_not_before"] = 0.0
                        _AB_PLAYER_STATE["transition_target"] = ""
                        _AB_PLAYER_STATE["transition_from"] = ""
                        _AB_PLAYER_STATE["transition_from_line"] = ""
                        _AB_PLAYER_STATE["transition_to_line"] = ""
                        _AB_PLAYER_STATE["transition_starting"] = False
            monitor_wake_serial, monitor_woken, monitor_wake_reason = _ab_wait_monitor_interruptible(
                monitor_wake_station_key,
                monitor_wake_serial,
                monitor_sleep_seconds,
            )
            if monitor_woken:
                pass
        except Exception as exc:
            time.sleep(0.5)

_AB_MONITOR_THREADS: dict[str, threading.Thread] = {}
_AB_MONITOR_THREADS_LOCK = threading.RLock()

def _ensure_ab_monitor_thread() -> None:
    """Start one native A/B transition monitor per station."""
    station_key = str(get_active_station_key() or "").strip()
    if not station_key:
        return
    with _AB_MONITOR_THREADS_LOCK:
        existing = _AB_MONITOR_THREADS.get(station_key)
        if existing is not None and existing.is_alive():
            return

        def _run_station_monitor() -> None:
            with station_runtime_context(station_key):
                _ab_monitor_loop()

        thread = threading.Thread(
            target=_run_station_monitor,
            name=f"native-ab-monitor-{_safe_dirname(station_key)}",
            daemon=True,
        )
        _AB_MONITOR_THREADS[station_key] = thread
        thread.start()


def _stop_station_scripts_for_off_air(station_key: str) -> None:
    resolved_station_key = str(station_key or get_active_station_key() or "").strip()
    if not resolved_station_key:
        return
    scripts = _read_station_scripts(resolved_station_key)
    stopped_any = False
    for item in scripts:
        script_id = int(item.get("id") or 0)
        status_value = str(item.get("status") or "Stopped").strip() or "Stopped"
        auto_start = 1 if int(item.get("auto_start") or 0) else 0
        if auto_start or _script_status_is_active(status_value):
            _set_station_script_status(resolved_station_key, script_id, "Stopped")
            _SCRIPT_ENGINE_LAST_RUN.pop((resolved_station_key, script_id), None)
            stopped_any = True
    if stopped_any:
        pass


def _stop_station_scheduler_rules_for_off_air(station_key: str) -> None:
    resolved_station_key = str(station_key or get_active_station_key() or "").strip()
    if not resolved_station_key:
        return
    conn = None
    try:
        conn = get_db_for_station(resolved_station_key)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id, is_enabled, next_run_at FROM scheduler_rules ORDER BY id ASC")
        rows = c.fetchall() or []
        active_rule_ids = []
        for row in rows:
            try:
                item = dict(row)
            except Exception:
                item = {k: row[k] for k in row.keys()} if hasattr(row, 'keys') else {}
            if int(item.get('is_enabled') or 0):
                active_rule_ids.append(int(item.get('id') or 0))
        if not active_rule_ids:
            return
        now = _utc_now_naive().isoformat(timespec="seconds")
        c.executemany(
            "UPDATE scheduler_rules SET is_enabled = 0, next_run_at = NULL, updated_at = ? WHERE id = ?",
            [(now, rule_id) for rule_id in active_rule_ids if int(rule_id or 0)],
        )
        conn.commit()
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


@app.route("/audio-engine/stop", methods=["POST"], endpoint="audio_engine_stop_route")
@login_required
def audio_engine_stop_route():
    return station_stop()


# --- Scheduler execution engine

def _resolve_insert_to_track_ids_for_station(station_key: str, insert_kind: str, insert_value: str) -> list[int]:
    """Station-scoped scheduler insert resolution."""
    kind = (insert_kind or "").strip().lower()
    val = (insert_value or "").strip()
    if not kind or not val:
        return []

    if kind in ("track_id", "track_ids", "id", "ids"):
        out: list[int] = []
        for token in re.split(r"[\s,;]+", val):
            token = token.strip()
            if not token:
                continue
            try:
                out.append(int(token))
            except Exception:
                continue
        return out

    conn = get_db_for_station(station_key)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        if kind in ("stream", "url", "uri") or val.startswith("http://") or val.startswith("https://") or val.startswith("URL:"):
            norm = val
            if kind == "stream":
                if not norm.startswith("URL:"):
                    m2 = re.match(r"^\s*(-?\d+)\s*:\s*(https?://.+)$", norm)
                    if m2:
                        norm = f"URL:{int(m2.group(1))}:{m2.group(2).strip()}"
                    elif norm.startswith("http://") or norm.startswith("https://"):
                        norm = f"URL:60:{norm}"
                    else:
                        norm = f"URL:60:{norm}"
            else:
                if not norm.startswith("URL:"):
                    m2 = re.match(r"^\s*(-?\d+)\s*:\s*(https?://.+)$", norm)
                    if m2:
                        norm = f"URL:{int(m2.group(1))}:{m2.group(2).strip()}"
            c.execute("SELECT id FROM tracks WHERE path = ? LIMIT 1", (norm,))
            row = c.fetchone()
            url_duration = _url_queue_duration_seconds_from_path(norm)
            if row and row[0] is not None:
                if url_duration > 0:
                    try:
                        c.execute(
                            "UPDATE tracks SET cue_duration_seconds = ? WHERE id = ? AND (cue_duration_seconds IS NULL OR cue_duration_seconds <= 0)",
                            (url_duration, int(row[0])),
                        )
                        conn.commit()
                    except Exception:
                        pass
                return [int(row[0])]
            if url_duration > 0:
                c.execute(
                    "INSERT INTO tracks (path, filename, cue_duration_seconds, created_at) VALUES (?, ?, ?, ?)",
                    (norm, norm, url_duration, datetime.now().isoformat()),
                )
            else:
                c.execute(
                    "INSERT INTO tracks (path, filename, created_at) VALUES (?, ?, ?)",
                    (norm, norm, datetime.now().isoformat()),
                )
            conn.commit()
            try:
                return [int(c.lastrowid)]
            except Exception:
                c.execute("SELECT id FROM tracks WHERE path = ? ORDER BY id DESC LIMIT 1", (norm,))
                row = c.fetchone()
                return [int(row[0])] if row and row[0] is not None else []

        if kind in ("file", "path"):
            c.execute("SELECT id FROM tracks WHERE path = ? LIMIT 1", (val,))
            row = c.fetchone()
            if row and row[0] is not None:
                return [int(row[0])]
            c.execute("SELECT id FROM tracks WHERE filename = ? LIMIT 1", (val,))
            row = c.fetchone()
            if row and row[0] is not None:
                return [int(row[0])]
            return []
        if kind in ("filename", "name"):
            c.execute("SELECT id FROM tracks WHERE filename = ? LIMIT 1", (val,))
            row = c.fetchone()
            if row and row[0] is not None:
                return [int(row[0])]
            base = os.path.basename(val)
            c.execute("SELECT id FROM tracks WHERE path LIKE ? ORDER BY id DESC LIMIT 1", (f"%/{base}",))
            row = c.fetchone()
            if row and row[0] is not None:
                return [int(row[0])]
            return []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return []


def _enqueue_track_ids_return_queue_ids_for_station(
    station_key: str,
    track_ids: list[int],
    priority: str,
) -> list[int]:
    """Insert station queue items through the canonical playback repository."""
    try:
        created_queue_ids = _get_playback_repository().enqueue_track_ids(
            track_ids,
            priority,
            station_key=station_key,
        )
        if created_queue_ids:
            _publish_ui_queue_history_changed(station_key, f"queue_{str(priority or 'end').strip().lower() or 'end'}")
        return created_queue_ids
    except Exception as exc:
        return []


def _move_station_queue_ids_to_front(station_key: str, queue_ids: list[int]) -> bool:
    """Move queue items to the front without routing storage through Flask."""
    try:
        moved = _get_playback_repository().move_queue_items_to_front(
            queue_ids,
            station_key=station_key,
        )
        if not moved:
            return False
        with station_runtime_context(station_key):
            _sync_reload_and_rebootstrap_after_queue_mutation("queue_move_to_front")
        _publish_ui_queue_history_changed(station_key, "queue_move_to_front")
        return True
    except Exception as exc:
        return False


def _enqueue_track_ids_for_station(station_key: str, track_ids: list[int], priority: str) -> bool:
    """Insert scheduler track_ids and notify the native DB-backed planner."""
    created_queue_ids = _enqueue_track_ids_return_queue_ids_for_station(station_key, track_ids, priority)
    if not created_queue_ids:
        return False
    try:
        wake_autodj_worker()
    except Exception:
        pass
    with station_runtime_context(station_key):
        _ab_schedule_async_replan("scheduler_enqueue")
    return True


def _apply_scheduler_rule_queue_action_for_station(station_key: str, track_ids: list[int], priority: str) -> bool:
    """Apply scheduler queue changes using the same ordering/next behavior as the classic player flow.

    - end: append and sync
    - next: insert, move to the front via the shared reorder path, then sync/reload
    - immediate: same as next, then trigger the same backend NEXT action as the player button
    """
    normalized_priority = (priority or "").strip().lower() or "end"
    if not track_ids:
        return False

    if normalized_priority == "end":
        return _enqueue_track_ids_for_station(station_key, track_ids, "end")

    created_queue_ids = _enqueue_track_ids_return_queue_ids_for_station(station_key, track_ids, "end")
    if not created_queue_ids:
        return False

    moved = _move_station_queue_ids_to_front(station_key, created_queue_ids)
    if not moved:
        return False

    try:
        try:
            wake_autodj_worker()
        except Exception:
            pass
        with station_runtime_context(station_key):
            _ab_schedule_async_replan("scheduler_move_to_front")
    except Exception as exc:
        return False

    if normalized_priority == "immediate":
        try:
            time.sleep(0.5)
        except Exception:
            pass
        return bool(_perform_station_next_action(station_key))

    return True


def _scheduler_update_after_run_for_station(station_key: str, rule: dict, was_recurring: bool) -> None:
    """Update scheduler_rules after a run: set last_run_at and compute next_run_at or disable."""
    try:
        rule_id = int(rule.get("id"))
    except Exception:
        return

    now_dt = datetime.now().replace(microsecond=0)
    now_iso = now_dt.isoformat(timespec="seconds")
    run_when = (rule.get("run_when") or "").strip()

    next_run_at = None
    is_enabled = 1

    if was_recurring:
        try:
            next_run_at = compute_next_run_at(run_when, now=now_dt)
        except Exception:
            next_run_at = None
        if not next_run_at:
            is_enabled = 0
    else:
        # One-shot rules: disable after firing
        is_enabled = 0
        next_run_at = None

    conn = get_db_for_station(station_key)
    try:
        c = conn.cursor()
        c.execute(
            "UPDATE scheduler_rules SET last_run_at = ?, next_run_at = ?, is_enabled = ? WHERE id = ?",
            (now_iso, next_run_at, int(is_enabled), rule_id),
        )
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

# (enqueues due inserts into upcoming queue) ---

import threading
import time
from datetime import datetime as _dt, timedelta

_SCHEDULER_POLL_SECONDS = 1.0
_SCHEDULER_IDLE_POLL_SECONDS = 30.0
_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_LAST_ON_AIR_BY_STATION: dict[str, bool] = {}

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

def _parse_time_hhmm(s):
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        return None
    return (hh, mm)

def compute_next_run_at(run_when, now=None):
    now_dt = (now or datetime.now()).replace(microsecond=0)
    s = (run_when or "").strip()
    if not s:
        return None

    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})$", s)
    if m:
        tt = _parse_time_hhmm(m.group(2))
        if not tt:
            return None
        dt0 = _dt.strptime(m.group(1), "%Y-%m-%d").replace(
            hour=tt[0], minute=tt[1], second=0, microsecond=0
        )
        return dt0.isoformat(timespec="seconds") if dt0 > now_dt else None

    m = re.match(r"^(Everyday)\s+(\d{1,2}:\d{2})$", s, flags=re.I)
    if m:
        tt = _parse_time_hhmm(m.group(2))
        if not tt:
            return None
        cand = now_dt.replace(hour=tt[0], minute=tt[1], second=0)
        if cand <= now_dt:
            cand += timedelta(days=1)
        return cand.isoformat(timespec="seconds")

    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2}:\d{2})$", s)
    if m:
        day = m.group(1).lower()
        if day not in _WEEKDAYS:
            return None
        tt = _parse_time_hhmm(m.group(2))
        if not tt:
            return None
        cand = now_dt.replace(hour=tt[0], minute=tt[1], second=0)
        days = (_WEEKDAYS[day] - cand.weekday()) % 7
        cand += timedelta(days=days)
        if cand <= now_dt:
            cand += timedelta(days=7)
        return cand.isoformat(timespec="seconds")

    return None



SCHEDULER_OVERDUE_CATCHUP_GRACE_SECONDS = 30

def _scheduler_rule_is_recurring(run_when: str) -> bool:
    token = (run_when or "").strip().split(" ", 1)[0].strip().lower()
    return bool((run_when or "").strip().lower().startswith("everyday") or token in _WEEKDAYS)

def _scheduler_normalize_overdue_rules_for_station(station_key: str, now_dt: datetime) -> int:
    """Move stale scheduler times forward instead of catching up missed runs.

    A rule is allowed to fire if it is only slightly late because of normal polling
    jitter. If the stored next_run_at is older than the grace window, it means the
    scheduler was started/re-enabled after the planned time; in that case we skip
    the missed occurrence and compute the next future run.
    """
    normalized = 0
    grace_cutoff = now_dt - timedelta(seconds=SCHEDULER_OVERDUE_CATCHUP_GRACE_SECONDS)
    conn = None
    try:
        conn = get_db_for_station(station_key)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            "SELECT * FROM scheduler_rules WHERE is_enabled = 1 AND next_run_at IS NOT NULL AND next_run_at < ? ORDER BY next_run_at ASC, id ASC",
            (grace_cutoff.isoformat(timespec="seconds"),),
        )
        rows = c.fetchall() or []
        for row in rows:
            try:
                rule = dict(row)
            except Exception:
                rule = {k: row[k] for k in row.keys()} if hasattr(row, "keys") else {}
            rule_id = int(rule.get("id") or 0)
            run_when = str(rule.get("run_when") or "").strip()
            old_next = str(rule.get("next_run_at") or "").strip()
            updated_at = _utc_now_naive().isoformat(timespec="seconds")
            if _scheduler_rule_is_recurring(run_when):
                new_next = compute_next_run_at(run_when, now=now_dt)
                c.execute(
                    "UPDATE scheduler_rules SET next_run_at = ?, updated_at = ? WHERE id = ?",
                    (new_next, updated_at, rule_id),
                )
            else:
                c.execute(
                    "UPDATE scheduler_rules SET is_enabled = 0, next_run_at = NULL, updated_at = ? WHERE id = ?",
                    (updated_at, rule_id),
                )
            normalized += 1
        if normalized:
            conn.commit()
    except Exception as exc:
        pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return normalized

def scheduler_process_due_once():
    # Non-blocking: skip if another tick is running
    if not _SCHEDULER_LOCK.acquire(blocking=False):
        return
    try:
        try:
            now_local = datetime.now().replace(microsecond=0)
        except Exception:
            pass

        station_keys = get_registered_station_keys() or []
        if not station_keys:
            try:
                active_key = (get_active_station_key() or "").strip()
                if active_key:
                    station_keys = [active_key]
            except Exception:
                station_keys = []
        if not station_keys:
            return False

        any_station_on_air = False
        now = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
        for station_key in station_keys:
            station_on_air = is_station_on_air(station_key)
            if not station_on_air:
                was_on_air = _SCHEDULER_LAST_ON_AIR_BY_STATION.get(station_key)
                _SCHEDULER_LAST_ON_AIR_BY_STATION[station_key] = False
                # The stop cleanup is needed on startup and on the ON-AIR -> OFF-AIR
                # edge only. Re-running it every second while idle keeps opening the
                # station DB and creates needless WAL traffic.
                if was_on_air is None or was_on_air is True:
                    try:
                        _stop_station_scheduler_rules_for_off_air(station_key)
                    except Exception as exc:
                        pass
                continue
            any_station_on_air = True
            _SCHEDULER_LAST_ON_AIR_BY_STATION[station_key] = True
            conn = None
            try:
                conn = get_db_for_station(station_key)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                try:
                    c.execute("SELECT COUNT(*) FROM scheduler_rules WHERE is_enabled = 1")
                    enabled_cnt = (c.fetchone() or [0])[0] or 0
                except Exception:
                    enabled_cnt = None
                try:
                    c.execute("SELECT MIN(next_run_at), MAX(next_run_at) FROM scheduler_rules WHERE is_enabled = 1 AND next_run_at IS NOT NULL")
                    mm = c.fetchone() or (None, None)
                    min_next, max_next = mm[0], mm[1]
                except Exception:
                    min_next, max_next = None, None
                try:
                    normalized_cnt = _scheduler_normalize_overdue_rules_for_station(station_key, datetime.now().replace(microsecond=0))
                    if normalized_cnt:
                        pass
                except Exception as exc:
                    pass
                c.execute(
                    "SELECT * FROM scheduler_rules WHERE is_enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY next_run_at ASC, id ASC",
                    (now,),
                )
                rows = c.fetchall() or []
                rules = [dict(r) for r in rows]
            except NoActiveStationError:
                rules = []
            except Exception as e:
                rules = []
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

            for r in rules:
                try:
                    rule_id = r.get("id")
                    priority = (r.get("priority") or "").strip().lower()
                    insert_kind = (r.get("insert_kind") or "").strip().lower()
                    insert_value = (r.get("insert_value") or "").strip()
                    ok = False
                    if insert_kind == "dir":
                        track_ids = _resolve_directory_to_track_ids_for_station(station_key, insert_value, "sorted")
                    else:
                        track_ids = _resolve_insert_to_track_ids_for_station(station_key, insert_kind, insert_value)

                    if track_ids:
                        ok = _apply_scheduler_rule_queue_action_for_station(station_key, track_ids, priority)
                    else:
                        pass

                    run_when = (r.get("run_when") or "").strip()
                    was_recurring = _scheduler_rule_is_recurring(run_when)
                    _scheduler_update_after_run_for_station(station_key, r, was_recurring)
                except Exception as e:
                    pass
        return any_station_on_air
    finally:
        _SCHED_LOG_RELEASE = True
        _SCHEDULER_LOCK.release()

def _scheduler_loop():
    while True:
        sleep_seconds = _SCHEDULER_POLL_SECONDS
        any_on_air = False
        try:
            any_on_air = scheduler_process_due_once()
            if not any_on_air:
                sleep_seconds = _SCHEDULER_IDLE_POLL_SECONDS
        except Exception as e:
            pass
        if any_on_air:
            time.sleep(sleep_seconds)
        else:
            _wait_idle_helper_event(_SCHEDULER_WAKE_EVENT, sleep_seconds)

_SCHEDULER_THREAD = None
_SCHEDULER_THREAD_LOCK = threading.Lock()


def start_scheduler_thread():
    global _SCHEDULER_THREAD
    with _SCHEDULER_THREAD_LOCK:
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            return
        t = threading.Thread(target=_scheduler_loop, daemon=True)
        _SCHEDULER_THREAD = t
        t.start()


# Start runtime helper threads on import (when app runs)
try:
    # Ensure global schema exists before background threads touch it.
    init_global_db()
    start_scheduler_thread()

    runtime_history_reconciler_started = False
    runtime_history_reconciler_fn = globals().get('start_runtime_history_reconciler_thread')
    if callable(runtime_history_reconciler_fn):
        runtime_history_reconciler_fn()
        runtime_history_reconciler_started = True
    else:
        pass

    # On program startup, only auto-start scripts may remain active.
    # Any script with Auto = No must be forced back to Stopped, even if it was
    # running before the previous shutdown.
    _reset_station_script_statuses_on_startup()
    start_script_engine_thread()
except Exception as e:
    pass


# --- FTS5 SEARCH SETUP ---
@app.route("/api/search_tracks")
@login_required
def search_tracks():
    q_raw = request.args.get("q", "").strip()
    category_id = request.args.get("category_id")
    station_key = (request.args.get("station_key") or "").strip() or (get_active_station_key() or "").strip()

    if len(q_raw) < 3 or not station_key:
        return jsonify([])

    conn = get_db_for_station(str(station_key))
    c = conn.cursor()

    def _cols():
        c.execute("PRAGMA table_info(tracks)")
        return [r[1] if not isinstance(r, dict) else r.get("name") for r in c.fetchall()]

    def _pick(cols, candidates):
        for name in candidates:
            if name in cols:
                return name
        return None

    try:
        cols = _cols()
        # Explicit column mapping (no guessing beyond a fixed set)
        col_id = _pick(cols, ["id", "track_id"])
        col_title = _pick(cols, ["title", "track_title", "name"])
        col_artist = _pick(cols, ["artist", "track_artist"])
        col_album = _pick(cols, ["album", "track_album"])
        col_filename = _pick(cols, ["filename", "file_name"])
        col_path = _pick(cols, ["path", "filepath", "file_path"])
        col_duration = _pick(cols, ["cue_duration_seconds", "duration_seconds", "duration", "length"])
        col_cat = _pick(cols, ["category_id", "cat_id"])

        # Try FTS if possible
        use_fts = ensure_fts_ready(conn)

        if use_fts and col_id and (col_title or col_filename or col_path):
            tokens = [t for t in re.split(r"\s+", q_raw) if t]
            safe = []
            for t in tokens:
                t2 = re.sub(r'[^0-9A-Za-z\u00C0-\u017F_]+', '', t)
                if t2:
                    safe.append(f"{t2}*")
            if safe:
                match_q = " AND ".join(safe)
                sql = f"SELECT t.{col_id} as id"
                if col_title: sql += f", t.{col_title} as title"
                else: sql += ", '' as title"
                if col_artist: sql += f", t.{col_artist} as artist"
                else: sql += ", '' as artist"
                if col_album: sql += f", t.{col_album} as album"
                else: sql += ", '' as album"
                if col_filename: sql += f", t.{col_filename} as filename"
                else: sql += ", '' as filename"
                if col_path: sql += f", t.{col_path} as path"
                else: sql += ", '' as path"
                if col_duration: sql += f", COALESCE(t.{col_duration}, 0) as cue_duration_seconds"
                else: sql += ", 0 as cue_duration_seconds"
                sql += " FROM tracks t JOIN tracks_fts f ON t.id = f.rowid WHERE tracks_fts MATCH ?"
                params = [match_q]
                if category_id and col_cat:
                    sql += f" AND t.{col_cat} = ?"
                    params.append(category_id)
                sql += " LIMIT 50"
                c.execute(sql, params)
                rows = c.fetchall()
            else:
                rows = []
        else:
            rows = []

        if not rows:
            # LIKE fallback across available text columns
            like = f"%{q_raw}%"
            text_cols = [cname for cname in [col_title, col_artist, col_album, col_filename, col_path] if cname]
            if not text_cols:
                return jsonify([])

            where = " OR ".join([f"{cn} LIKE ?" for cn in text_cols])
            sql = f"SELECT {col_id or 'rowid'} as id"
            if col_title: sql += f", {col_title} as title"
            else: sql += ", '' as title"
            if col_artist: sql += f", {col_artist} as artist"
            else: sql += ", '' as artist"
            if col_album: sql += f", {col_album} as album"
            else: sql += ", '' as album"
            if col_filename: sql += f", {col_filename} as filename"
            else: sql += ", '' as filename"
            if col_path: sql += f", {col_path} as path"
            else: sql += ", '' as path"
            if col_duration: sql += f", COALESCE({col_duration}, 0) as cue_duration_seconds"
            else: sql += ", 0 as cue_duration_seconds"
            sql += f" FROM tracks WHERE ({where})"
            params = [like]*len(text_cols)
            if category_id and col_cat:
                sql += f" AND {col_cat} = ?"
                params.append(category_id)
            sql += " LIMIT 50"
            c.execute(sql, params)
            rows = c.fetchall()

        # Normalize output and build display_title fallback
        out_rows = []
        for r in rows:
            d = dict(r) if not isinstance(r, dict) else r
            title = (d.get("title") or "").strip()
            artist = (d.get("artist") or "").strip()
            filename = (d.get("filename") or "").strip()
            path = (d.get("path") or "").strip()
            if title:
                display = f"{artist + ' - ' if artist else ''}{title}"
            elif filename:
                display = filename
            else:
                display = path
            d["display_title"] = display
            out_rows.append(d)

        return jsonify(out_rows)

    except Exception as e:
        return jsonify([])
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# v5056 native-only control/runtime overrides
# ---------------------------------------------------------------------------
# Native-only control and runtime overrides.

def _ab_get_current_ui_status_wallclock_fallback(station_key: Optional[str] = None) -> dict:
    """Return a wall-clock UI status from the Python A/B mirror.

    This is only a startup/recovery fallback when the native daemon cannot map
    its active deck to the current in-memory queue plan. It must remain
    independent from the native status resolver to avoid recursive fallback.
    """
    try:
        sk = str(station_key or get_active_station_key() or "").strip()
        if sk:
            with station_runtime_context(sk):
                with _AB_PLAYER_LOCK:
                    st = dict(_AB_PLAYER_STATE or {})
        else:
            with _AB_PLAYER_LOCK:
                st = dict(_AB_PLAYER_STATE or {})
        if not bool(st.get("enabled")):
            return {}
        lines = list(st.get("lines") or [])
        if not lines:
            return {}
        active_player = str(st.get("active") or "a").lower()
        if active_player not in ("a", "b"):
            active_player = "a"
        player_index = dict(st.get("player_index") or {})
        try:
            idx = int(player_index.get(active_player, st.get("current_index") or 0))
        except Exception:
            idx = int(st.get("current_index") or 0)
        if not (0 <= idx < len(lines)):
            try:
                idx = int(st.get("current_index") or 0)
            except Exception:
                idx = 0
        if not (0 <= idx < len(lines)):
            idx = 0
        info = _ab_line_info(lines[idx])
        path = normalize_media_path(str(info.get("file") or ""))
        if not path:
            return {}
        started_at = float(st.get("started_at") or 0.0)
        now = time.time()
        segment_elapsed = max(0.0, now - started_at) if started_at > 0.0 else 0.0
        segment_duration = max(0.0, float(info.get("segment_duration") or 0.0))
        cue_in = max(0.0, float(info.get("cue_in") or 0.0))
        original_total = max(0.0, float(info.get("orig_total") or 0.0))
        cue_out = max(0.0, float(info.get("cue_out") or 0.0))
        if segment_duration > 0.0:
            segment_elapsed = min(segment_elapsed, segment_duration)
        full_elapsed = cue_in + segment_elapsed
        full_duration = original_total or (cue_in + segment_duration)
        effective_end = cue_out or full_duration
        return {
            "title": str(info.get("title") or ""),
            "artist": str(info.get("artist") or ""),
            "album": str(info.get("album") or ""),
            "year": str(info.get("year") or ""),
            "file": path,
            "elapsed": full_elapsed,
            "duration": full_duration,
            "remaining": max(0.0, effective_end - full_elapsed),
            "seek_base": cue_in,
            "orig_total": original_total,
            "cue_total": cue_out,
            "raw_elapsed": segment_elapsed,
            "raw_duration": segment_duration if segment_duration > 0.0 else full_duration,
            "raw_remaining": (
                max(0.0, segment_duration - segment_elapsed)
                if segment_duration > 0.0
                else max(0.0, effective_end - full_elapsed)
            ),
            "source": "ab_player_wallclock_fallback",
            "active_player": active_player,
            "queue_id": int(info.get("queue_id") or 0),
            "track_id": int(info.get("track_id") or 0),
        }
    except Exception:
        return {}


def _native_station_state(station_key: str = "") -> dict:
    sk = str(station_key or get_active_station_key() or "").strip()
    if not sk:
        return {}
    try:
        with station_runtime_context(sk):
            return dict(get_audio_engine().get_state(station_key=sk) or {})
    except Exception:
        return {}


def _native_status_line_for_state(station_key: str, state: dict) -> str:
    """Resolve the current annotated queue line from native deck identity."""
    active = str(state.get("active_deck") or "A").lower()
    if active not in ("a", "b"):
        active = "a"
    try:
        queue_id = int(state.get("queue_id") or state.get("native_audio_probe_queue_id") or 0)
    except Exception:
        queue_id = 0
    path = normalize_media_path(str(state.get("native_audio_probe_path") or ""))
    with _AB_PLAYER_LOCK:
        snapshot = dict(_AB_PLAYER_STATE or {})
        lines = list(snapshot.get("lines") or [])
        player_index = dict(snapshot.get("player_index") or {})
    index = _ab_find_line_index_by_identity(lines, path=path, queue_id=queue_id)
    if index < 0:
        try:
            index = int(player_index.get(active, snapshot.get("current_index", -1)))
        except Exception:
            index = -1
    return lines[index] if 0 <= index < len(lines) else ""


def _native_api_status_payload(station_key: str, state: dict, *, with_progress: bool = False) -> dict:
    """Translate the native get_state result into the stable Studio status API."""
    sk = str(station_key or state.get("station_key") or "").strip()
    running = bool(state.get("running"))
    paused = bool(state.get("paused"))
    soft_stopped = bool(state.get("soft_stopped"))
    notice = get_autodj_notice(sk)
    manual_next_status_fn = globals().get("_manual_next_public_status")
    manual_next_status = (
        dict(manual_next_status_fn(sk) or {})
        if callable(manual_next_status_fn)
        else {"in_progress": False, "pending_count": 0}
    )
    if not running or soft_stopped:
        return {
            "status": "stopped",
            "paused": False,
            "pause_active": False,
            "song": None,
            "station_id": sk,
            "audio_engine": "native",
            "autodj_notice": notice,
            "manual_next": manual_next_status,
        }

    line = _native_status_line_for_state(sk, state)
    info = _ab_line_info(line) if line else {}
    with NOW_PLAYING_LOCK:
        store = dict(_get_now_playing_store(sk) or {})

    active = str(state.get("active_deck") or "A").lower()
    if active not in ("a", "b"):
        active = "a"
    try:
        queue_id = int(state.get("queue_id") or info.get("queue_id") or store.get("queue_id") or 0)
    except Exception:
        queue_id = 0
    try:
        track_id = int(info.get("track_id") or store.get("track_id") or 0)
    except Exception:
        track_id = 0

    path = normalize_media_path(str(
        state.get("native_audio_probe_path")
        or info.get("file")
        or store.get("file")
        or ""
    ))
    title = str(info.get("title") or store.get("title") or "").strip()
    artist = str(info.get("artist") or store.get("artist") or "").strip()
    album = str(info.get("album") or store.get("album") or "").strip()
    year = _normalize_year_metadata(info.get("year") or store.get("year") or "")
    if path and (not title or not artist or not album or not year):
        try:
            media = read_media_metadata(path)
        except Exception:
            media = {}
        title = title or str(media.get("title") or "").strip()
        artist = artist or str(media.get("artist") or "").strip()
        album = album or str(media.get("album") or "").strip()
        year = year or _normalize_year_metadata(media.get("year"))
    if path and (not title or not artist):
        guessed = guess_metadata_from_filename(path)
        title = title or str(guessed.get("title") or "").strip()
        artist = artist or str(guessed.get("artist") or "").strip()

    elapsed_ms = 0
    if with_progress:
        try:
            probe_queue_id = int(state.get("native_audio_probe_queue_id") or 0)
        except Exception:
            probe_queue_id = 0
        try:
            if int(state.get("native_audio_probe_position_ms") or 0) > 0 and (
                queue_id <= 0 or probe_queue_id <= 0 or probe_queue_id == queue_id
            ):
                elapsed_ms = int(state.get("native_audio_probe_position_ms") or 0)
            else:
                elapsed_ms = int(state.get("position_ms") or 0)
        except Exception:
            elapsed_ms = 0
    # native_audio_probe_position_ms is already an absolute source position;
    # cue_in must never be added a second time.
    elapsed = max(0.0, float(elapsed_ms) / 1000.0)

    duration_candidates = [
        float(info.get("orig_total") or 0.0),
        float(state.get("native_audio_probe_source_end_ms") or 0) / 1000.0,
        float(state.get("native_audio_probe_effective_end_ms") or 0) / 1000.0,
        float(store.get("duration") or 0.0),
    ]
    duration = max((value for value in duration_candidates if value > 0.0), default=0.0)
    if duration > 0.0:
        elapsed = min(elapsed, duration)

    return {
        "status": "play",
        "paused": paused,
        "pause_active": paused,
        "song": {
            "title": title or "Unknown",
            "artist": artist,
            "album": album,
            "year": year,
            "file": path,
            "duration": duration,
            "duration_display": format_seconds(duration) if duration > 0.0 else "",
            "elapsed": elapsed,
            "queue_id": queue_id,
            "track_id": track_id,
            "active_player": active,
            "source": "native_get_state",
        },
        "station_id": sk,
        "audio_engine": "native",
        "autodj_notice": notice,
        "native_transitioning": bool(state.get("transitioning")),
        "manual_next": manual_next_status,
    }


_NATIVE_LIFECYCLE_LOCK = _console_threading.RLock()
_NATIVE_LIFECYCLE_COORDINATOR = None

_RUNTIME_DURATION_VERIFY_EVENTS = frozenset({
    "native_audio_probe_eof",
    "native_audio_probe_early_eof",
})
_RUNTIME_DURATION_VERIFY_LOCK = _console_threading.RLock()
_RUNTIME_DURATION_VERIFY_PENDING: set[str] = set()
_RUNTIME_DURATION_CORRECTION_THRESHOLD_SECONDS = 1.0
_RUNTIME_DURATION_ENDPOINT_MATCH_TOLERANCE_SECONDS = 0.750

# Manual Next/player orchestration and station Start/Stop are lazy singletons.
# Their state and operation ordering live in dedicated framework-neutral modules.
_PLAYER_ORCHESTRATION_LOCK = _console_threading.RLock()
_PLAYER_HANDOFF_SERVICE = None
_MANUAL_NEXT_ORCHESTRATOR = None
_STATION_SERVICE_LOCK = _console_threading.RLock()
_STATION_SERVICE = None


def _preload_reuse_trace(event: str, station_key: str = "", *, deck: str = "", queue_id: int = 0, slot_token: str = "", **fields) -> None:
    """Publish confirmed native preload reuse to the DEBUG-gated protocol log."""
    sk = os.path.basename(str(station_key or get_active_station_key() or "").strip())
    payload = {str(key): value for key, value in fields.items()}
    payload["slot_token"] = str(slot_token or "")
    try:
        _publish_audio_engine_event(
            str(event or "native_preload_reused"),
            station_key=sk,
            queue_id=max(0, int(queue_id or 0)),
            deck=str(deck or ""),
            payload=payload,
        )
    except Exception:
        pass


def _manual_next_trace(event: str, station_key: str = "", request_id: str = "", **fields) -> None:
    """Publish one Manual Next lifecycle event to the DEBUG-gated protocol log.

    When ``DEBUG=1`` this telemetry is written to
    ``audio_engine_protocol.jsonl``; it never raises into the control/audio path.
    """
    sk = os.path.basename(str(station_key or get_active_station_key() or "").strip())
    payload = {str(key): value for key, value in fields.items()}
    if request_id:
        payload["manual_next_request_id"] = str(request_id)
    try:
        _publish_audio_engine_event(
            str(event or "manual_next_event"),
            station_key=sk,
            queue_id=max(0, int(payload.get("target_queue_id") or payload.get("queue_id") or 0)),
            track_id=max(0, int(payload.get("target_track_id") or payload.get("track_id") or 0)),
            deck=str(payload.get("deck") or payload.get("target_deck") or ""),
            payload=payload,
        )
    except Exception:
        pass


def _read_ab_player_state() -> dict:
    with _AB_PLAYER_LOCK:
        return dict(_AB_PLAYER_STATE or {})


def _mutate_ab_player_state(callback):
    with _AB_PLAYER_LOCK:
        return callback(_AB_PLAYER_STATE)


def _get_player_handoff_service() -> PlayerHandoffService:
    global _PLAYER_HANDOFF_SERVICE
    with _PLAYER_ORCHESTRATION_LOCK:
        if _PLAYER_HANDOFF_SERVICE is None:
            _PLAYER_HANDOFF_SERVICE = PlayerHandoffService(
                PlayerHandoffDependencies(
                    get_active_station_key=lambda: str(get_active_station_key() or ""),
                    build_queue_plan=lambda station_key: list(
                        _build_station_queue_plan(station_key) or []
                    ),
                    line_info=lambda line: dict(_ab_line_info(line) or {}),
                    read_player_state=_read_ab_player_state,
                    mutate_player_state=_mutate_ab_player_state,
                    native_station_state=lambda station_key: dict(
                        _native_station_state(station_key) or {}
                    ),
                    reconcile_stale_transition=(
                        _ab_reconcile_stale_transition_before_manual_next
                    ),
                    trace_manual_next=_manual_next_trace,
                    resolve_native_live_player=lambda active, timeout: (
                        _ab_resolve_native_live_player(active, timeout_sec=timeout)
                    ),
                    same_queue_identity=_ab_same_queue_identity,
                    start_transition=_ab_start_cueout_transition_now,
                    wake_autodj_worker=wake_autodj_worker,
                )
            )
        return _PLAYER_HANDOFF_SERVICE


def _manual_next_read_reserved_plan(station_key: str) -> tuple[list[str], int, int]:
    lines = list(_build_station_queue_plan(station_key) or [])
    if not lines:
        return [], 0, 0
    info = _ab_line_info(lines[0])
    return lines, int(info.get("queue_id") or 0), int(info.get("track_id") or 0)


def _resolve_manual_next_station_key(station_key: str) -> str:
    return (
        _resolve_station_id_to_db(station_key or "")
        or os.path.basename(str(station_key or get_active_station_key() or "").strip())
    )


def _get_manual_next_orchestrator() -> ManualNextOrchestrator:
    global _MANUAL_NEXT_ORCHESTRATOR
    with _PLAYER_ORCHESTRATION_LOCK:
        if _MANUAL_NEXT_ORCHESTRATOR is None:
            _MANUAL_NEXT_ORCHESTRATOR = ManualNextOrchestrator(
                ManualNextDependencies(
                    resolve_station_key=_resolve_manual_next_station_key,
                    get_active_station_key=lambda: str(get_active_station_key() or ""),
                    trace=_manual_next_trace,
                    station_runtime_context=station_runtime_context,
                    read_reserved_plan=_manual_next_read_reserved_plan,
                    native_station_state=lambda station_key: dict(
                        _native_station_state(station_key) or {}
                    ),
                    native_queue_contains_queue_id=_native_queue_contains_queue_id,
                    perform_direct_handoff=_perform_ab_manual_next_direct_handoff,
                    signal_monitor_wake=lambda station_key, reason: _ab_signal_monitor_wake(
                        station_key, reason=reason
                    ),
                    wake_autodj_worker=wake_autodj_worker,
                    scheduled_script_url_active=_station_url_playback_active,
                    cancel_scheduled_script_queue=_cancel_scheduled_script_queue_items,
                )
            )
        return _MANUAL_NEXT_ORCHESTRATOR


def _manual_next_station_key(station_key: str = "") -> str:
    return _get_manual_next_orchestrator().station_key(station_key)


def _manual_next_deck_plan_lock(station_key: str):
    return _get_manual_next_orchestrator().deck_plan_lock(station_key)


def _manual_next_public_status(station_key: str = "") -> dict:
    return _get_manual_next_orchestrator().public_status(station_key)


def _manual_next_is_inflight(station_key: str = "") -> bool:
    return _get_manual_next_orchestrator().is_inflight(station_key)


def _manual_next_mark_lifecycle(
    station_key: str,
    queue_id: int,
    *,
    success: bool,
) -> None:
    _get_manual_next_orchestrator().mark_lifecycle(
        station_key,
        queue_id,
        success=success,
    )


def _enqueue_manual_next_action(
    station_key: str,
    *,
    action: str = "next",
    source: str = "internal",
) -> dict:
    return _get_manual_next_orchestrator().enqueue(
        station_key,
        action=action,
        source=source,
    )


def _native_track_started_signature(event) -> str:
    return "|".join((
        str(getattr(event, "station_key", "") or ""),
        str(getattr(event, "deck", "") or "").upper(),
        str(int(getattr(event, "queue_id", 0) or 0)),
        str(getattr(event, "slot_token", "") or ""),
        str(int(getattr(event, "track_id", 0) or 0)),
        normalize_media_path(str(getattr(event, "path", "") or "")),
    ))


def _native_load_requested_next_track(event) -> None:
    """Idempotently satisfy a native next-track request from authoritative deck state.

    The event may arrive before the delayed Python track_started lifecycle worker.
    Therefore event freshness is validated against native get_state, never against
    the mutable Python A/B current index. Repeated native requests are harmless.
    """
    station_key = str(getattr(event, "station_key", "") or "").strip()
    payload = dict(getattr(event, "payload", {}) or {})
    event_deck = str(getattr(event, "deck", "") or payload.get("active_deck") or "").strip().lower()
    target = str(payload.get("target_deck") or "").strip().lower()
    if target not in ("a", "b"):
        target = "b" if event_deck == "a" else "a"
    if not station_key:
        return
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_slot_token = str(getattr(event, "slot_token", "") or "")
    event_path = normalize_media_path(str(getattr(event, "path", "") or ""))
    try:
        with station_runtime_context(station_key):
            native_state = _native_station_state(station_key)
            native_active = str(native_state.get("active_deck") or "").strip().lower()
            native_queue_id = int(native_state.get("queue_id") or 0)
            native_slot_token = str(native_state.get("slot_token") or "")
            identity_ok = bool(
                native_active in ("a", "b")
                and (event_deck not in ("a", "b") or native_active == event_deck)
                and (event_slot_token and native_slot_token == event_slot_token
                     or not event_slot_token and event_queue_id > 0 and native_queue_id == event_queue_id)
            )
            if not identity_ok:
                return

            with _AB_PLAYER_LOCK:
                st = dict(_AB_PLAYER_STATE or {})
                if not bool(st.get("enabled")):
                    return
                lines = list(st.get("lines") or [])
                generation = int(st.get("generation") or 0)
            current_index = _ab_find_line_index_by_identity(
                lines, path=event_path, queue_id=event_queue_id
            )
            if current_index < 0:
                _ab_signal_monitor_wake(station_key, reason="native_need_next_identity_not_in_plan")
                return
            next_index = current_index + 1
            if next_index >= len(lines):
                return
            next_line = lines[next_index]
            next_info = _ab_line_info(next_line)
            next_queue_id = int(next_info.get("queue_id") or 0)
            target_native_queue_id = int(native_state.get(f"deck_{target}_queue_id") or 0)
            target_native_slot = str(native_state.get(f"deck_{target}_slot_token") or "")
            if next_queue_id > 0 and target_native_queue_id == next_queue_id and target_native_slot:
                _ab_record_player_loaded_identity(
                    target,
                    next_line,
                    generation=generation,
                )
                with _AB_PLAYER_LOCK:
                    live_index = dict((_AB_PLAYER_STATE or {}).get("player_index") or {})
                    live_index[target] = next_index
                    _AB_PLAYER_STATE["player_index"] = live_index
                    _AB_PLAYER_STATE["next_index"] = next_index
                return
            ok = _ab_push(target, next_line, attempts=4, retry_delay=0.05, clear_slot=True)
            if ok:
                with _AB_PLAYER_LOCK:
                    if int((_AB_PLAYER_STATE or {}).get("generation") or 0) == generation:
                        live_index = dict((_AB_PLAYER_STATE or {}).get("player_index") or {})
                        live_index[target] = next_index
                        _AB_PLAYER_STATE["player_index"] = live_index
                        _AB_PLAYER_STATE["next_index"] = next_index
            else:
                pass
    except Exception as exc:
        pass


def _native_load_requested_next_track_guarded(event, request_key: str = "") -> None:
    """Load a native need-next request under the shared deck-plan lock."""
    station_key = str(getattr(event, "station_key", "") or "").strip()
    with _manual_next_deck_plan_lock(station_key):
        if _manual_next_is_inflight(station_key):
            return
        _native_load_requested_next_track(event)



def _ab_clear_transition_runtime_fields_locked(*, active_deck: str = "") -> None:
    """Clear Python-only transition bookkeeping while the A/B lock is held."""
    active = str(active_deck or "").strip().lower()
    if active in {"a", "b"}:
        _AB_PLAYER_STATE["active"] = active
        player_index = dict(_AB_PLAYER_STATE.get("player_index") or {})
        try:
            active_index = int(player_index.get(active, _AB_PLAYER_STATE.get("current_index") or 0))
        except Exception:
            active_index = int(_AB_PLAYER_STATE.get("current_index") or 0)
        if active_index >= 0:
            _AB_PLAYER_STATE["current_index"] = active_index
    _AB_PLAYER_STATE["transitioning"] = False
    _AB_PLAYER_STATE["transition_starting"] = False
    _AB_PLAYER_STATE["transition_started_at"] = 0.0
    _AB_PLAYER_STATE["transition_duration"] = 0.0
    _AB_PLAYER_STATE["transition_target"] = ""
    _AB_PLAYER_STATE["transition_from"] = ""
    _AB_PLAYER_STATE["transition_from_line"] = ""
    _AB_PLAYER_STATE["transition_to_line"] = ""
    _AB_PLAYER_STATE["pending_cueout_transition"] = False
    _AB_PLAYER_STATE["pending_cueout_deadline"] = 0.0
    _AB_PLAYER_STATE["pending_cueout_token"] = 0


def _ab_reconcile_native_transition_finished(event) -> bool:
    """Apply one authoritative native transition completion to Python A/B state.

    The socket-reader callback must not issue another engine command.  The
    lifecycle event already proves that the native mixer completed the audible
    handoff, so only station-scoped Python bookkeeping is reconciled here.
    """
    station_key = str(getattr(event, "station_key", "") or "").strip()
    if not station_key:
        return False
    target = str(getattr(event, "deck", "") or "").strip().lower()
    if target not in {"a", "b"}:
        return False
    event_queue_id = int(getattr(event, "queue_id", 0) or 0)
    event_slot_token = str(getattr(event, "slot_token", "") or "").strip()
    payload = dict(getattr(event, "payload", {}) or {})

    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            snapshot = dict(_AB_PLAYER_STATE or {})
            if not bool(snapshot.get("enabled")):
                return False
            active_before = str(snapshot.get("active") or "").strip().lower()
            player_index = dict(snapshot.get("player_index") or {})
            lines = list(snapshot.get("lines") or [])
            try:
                target_index = int(player_index.get(target, -1))
            except Exception:
                target_index = -1
            target_info = _ab_line_info(lines[target_index]) if 0 <= target_index < len(lines) else {}
            expected_queue_id = int(target_info.get("queue_id") or 0)
            expected_slot_token = str(target_info.get("slot_token") or "").strip()
            identity_matches = bool(
                (event_queue_id > 0 and expected_queue_id > 0 and event_queue_id == expected_queue_id)
                or (event_slot_token and expected_slot_token and event_slot_token == expected_slot_token)
            )
            # Ignore a delayed completion from an older transition after a newer
            # hard select has already made the opposite deck authoritative.
            if active_before in {"a", "b"} and active_before != target and not identity_matches:
                return False
            was_transitioning = bool(snapshot.get("transitioning") or snapshot.get("transition_starting"))
            _ab_clear_transition_runtime_fields_locked(active_deck=target)
    return True


def _ab_reconcile_stale_transition_before_manual_next(station_key: str, native_state: dict) -> bool:
    """Drop stale Python transition state when the daemon is already idle.

    This is a safety net for a delayed or lost ``transition_finished`` event.
    The daemon remains authoritative; an actually running native transition is
    never cleared by this path.
    """
    station_key = str(station_key or get_active_station_key() or "").strip()
    state = dict(native_state or {})
    if not station_key or not bool(state.get("running")) or bool(state.get("transitioning")):
        return False
    native_active = str(state.get("active_deck") or "").strip().lower()
    if native_active not in {"a", "b"}:
        native_active = ""

    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            snapshot = dict(_AB_PLAYER_STATE or {})
            # The reproduced fault is a latched completed transition.  Do not
            # clear a fresh transition_starting claim that another Python thread
            # may still be about to dispatch.
            if not bool(snapshot.get("transitioning")):
                return False
            active_before = str(snapshot.get("active") or "").strip().lower()
            _ab_clear_transition_runtime_fields_locked(active_deck=native_active or active_before)
    return True



def _runtime_duration_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _runtime_duration_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _runtime_duration_candidate(event) -> tuple[float, str] | None:
    """Return a verified full-file duration candidate from one clean native EOF.

    ``final_actual_duration_ms`` is the decoded duration after ``play_start_ms``.
    ``source_position_ms`` is therefore the full absolute source position at EOF
    and remains valid when native silence analysis moved the audible start.
    """
    event_name = str(getattr(event, "event", "") or "").strip()
    if event_name not in _RUNTIME_DURATION_VERIFY_EVENTS:
        return None

    payload = dict(getattr(event, "payload", {}) or {})
    if _runtime_duration_bool(payload.get("manual_timing")):
        return None
    if _runtime_duration_bool(payload.get("stream_source")):
        return None
    if _runtime_duration_bool(payload.get("fault_injected")):
        return None
    if _runtime_duration_int(payload.get("decoder_exit_code"), 0) != 0:
        return None
    if _runtime_duration_int(payload.get("decoder_signal"), 0) != 0:
        return None
    if _runtime_duration_int(payload.get("corrupt_input_skipped_count"), 0) != 0:
        return None

    terminal_reason = str(payload.get("terminal_reason") or "").strip()
    if event_name == "native_audio_probe_eof":
        if terminal_reason != "natural_eof":
            return None
        source = "native_natural_eof"
    else:
        # A clean decoder EOF can be classified as early only because the
        # provisional Mutagen duration was too long.  That is precisely the
        # Floorfilla case this self-healing path must correct.
        if terminal_reason != "early_eof":
            return None
        source = "native_clean_early_eof"

    played_ms = _runtime_duration_int(
        payload.get("final_actual_duration_ms"),
        _runtime_duration_int(payload.get("played_duration_ms"), 0),
    )
    play_start_ms = max(0, _runtime_duration_int(payload.get("play_start_ms"), 0))
    source_position_ms = _runtime_duration_int(payload.get("source_position_ms"), 0)
    expected_source_position_ms = play_start_ms + played_ms
    if played_ms <= 0:
        return None
    if source_position_ms <= 0:
        source_position_ms = expected_source_position_ms
    if source_position_ms <= 0:
        return None
    if abs(source_position_ms - expected_source_position_ms) > 1000:
        return None

    duration_seconds = round(float(source_position_ms) / 1000.0, 3)
    if duration_seconds <= 0.0:
        return None
    return duration_seconds, source


def _runtime_duration_endpoint_is_automatic(value, old_duration: float) -> bool:
    try:
        endpoint = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        endpoint = 0.0
    if endpoint <= 0.0:
        return True
    if old_duration <= 0.0:
        return False
    return abs(endpoint - old_duration) <= _RUNTIME_DURATION_ENDPOINT_MATCH_TOLERANCE_SECONDS


def _verify_runtime_duration_after_playback(event) -> dict:
    """Persist one clean native EOF measurement for an unchanged local file.

    The Mutagen duration remains the fast initial value.  After the first clean
    EOF, the exact native source position is stored and a materially wrong full
    duration is corrected.  Endpoint fields are changed only when they still
    equal the old automatic full-file default, preserving manual cue/fade data.
    """
    candidate = _runtime_duration_candidate(event)
    if candidate is None:
        return {"verified": False, "corrected": False, "reason": "ineligible_event"}
    actual_duration, source = candidate

    station_key = str(getattr(event, "station_key", "") or "").strip()
    track_id = max(0, _runtime_duration_int(getattr(event, "track_id", 0), 0))
    event_path = normalize_media_path(str(getattr(event, "path", "") or "").strip())
    if not station_key or not event_path:
        return {"verified": False, "corrected": False, "reason": "missing_identity"}
    if event_path.startswith("http://") or event_path.startswith("https://") or not os.path.isfile(event_path):
        return {"verified": False, "corrected": False, "reason": "not_local_file"}

    try:
        file_stat = os.stat(event_path)
        file_size = int(file_stat.st_size)
        file_mtime_ns = int(file_stat.st_mtime_ns)
    except OSError:
        return {"verified": False, "corrected": False, "reason": "file_stat_failed"}

    conn = get_db_for_station(station_key)
    try:
        conn.row_factory = sqlite3.Row
        if track_id > 0:
            row = conn.execute(
                """
                SELECT id, path, cue_duration_seconds, cue_out_seconds,
                       cue_trimmed_seconds, audio_end_seconds,
                       runtime_duration_seconds, runtime_duration_verified_at,
                       runtime_duration_file_size, runtime_duration_file_mtime_ns
                FROM tracks WHERE id = ? LIMIT 1
                """,
                (track_id,),
            ).fetchone()
        else:
            row = None
        if row is None:
            row = conn.execute(
                """
                SELECT id, path, cue_duration_seconds, cue_out_seconds,
                       cue_trimmed_seconds, audio_end_seconds,
                       runtime_duration_seconds, runtime_duration_verified_at,
                       runtime_duration_file_size, runtime_duration_file_mtime_ns
                FROM tracks WHERE path = ? LIMIT 1
                """,
                (event_path,),
            ).fetchone()
        if row is None:
            return {"verified": False, "corrected": False, "reason": "track_not_found"}

        stored_path = normalize_media_path(str(row["path"] or "").strip())
        if stored_path != event_path:
            return {"verified": False, "corrected": False, "reason": "identity_mismatch"}

        already_verified = bool(
            row["runtime_duration_verified_at"]
            and _runtime_duration_int(row["runtime_duration_file_size"], -1) == file_size
            and _runtime_duration_int(row["runtime_duration_file_mtime_ns"], -1) == file_mtime_ns
        )
        if already_verified:
            return {"verified": False, "corrected": False, "reason": "already_verified"}

        try:
            old_duration = max(0.0, float(row["cue_duration_seconds"] or 0.0))
        except (TypeError, ValueError):
            old_duration = 0.0
        corrected = bool(
            old_duration <= 0.0
            or abs(actual_duration - old_duration) >= _RUNTIME_DURATION_CORRECTION_THRESHOLD_SECONDS
        )

        assignments = [
            "runtime_duration_seconds = ?",
            "runtime_duration_verified_at = ?",
            "runtime_duration_file_size = ?",
            "runtime_duration_file_mtime_ns = ?",
            "runtime_duration_source = ?",
        ]
        values: list = [
            actual_duration,
            datetime.now().isoformat(timespec="seconds"),
            file_size,
            file_mtime_ns,
            source,
        ]

        changed_endpoints: list[str] = []
        if corrected:
            assignments.append("cue_duration_seconds = ?")
            values.append(actual_duration)
            for column in ("cue_out_seconds", "cue_trimmed_seconds", "audio_end_seconds"):
                if _runtime_duration_endpoint_is_automatic(row[column], old_duration):
                    assignments.append(f"{column} = ?")
                    values.append(actual_duration)
                    changed_endpoints.append(column)

        values.append(int(row["id"]))
        conn.execute(
            f"UPDATE tracks SET {', '.join(assignments)} WHERE id = ?",
            tuple(values),
        )
        conn.commit()
        return {
            "verified": True,
            "corrected": corrected,
            "reason": "duration_corrected" if corrected else "duration_confirmed",
            "track_id": int(row["id"]),
            "path": event_path,
            "old_duration_seconds": old_duration,
            "duration_seconds": actual_duration,
            "source": source,
            "changed_endpoints": changed_endpoints,
        }
    finally:
        conn.close()


def _runtime_duration_verification_signature(event) -> str:
    return "|".join((
        str(getattr(event, "station_key", "") or ""),
        str(max(0, _runtime_duration_int(getattr(event, "track_id", 0), 0))),
        normalize_media_path(str(getattr(event, "path", "") or "")),
    ))


def _run_runtime_duration_verification(event, signature: str) -> None:
    try:
        result = _verify_runtime_duration_after_playback(event)
        if not bool(result.get("corrected")):
            return
        station_key = str(getattr(event, "station_key", "") or "").strip()
        with station_runtime_context(station_key):
            invalidate_audio_engine_status_cache()
            wake_autodj_worker()
            _publish_ui_event(
                "track_duration_changed",
                station_key,
                "native_runtime_duration_corrected",
                {
                    "track_id": int(result.get("track_id") or 0),
                    "file": str(result.get("path") or ""),
                    "old_duration_seconds": float(result.get("old_duration_seconds") or 0.0),
                    "duration_seconds": float(result.get("duration_seconds") or 0.0),
                    "source": str(result.get("source") or ""),
                },
            )
            _publish_ui_queue_history_changed(station_key, "native_runtime_duration_corrected")
            _ab_schedule_async_replan("native_runtime_duration_corrected", delay=0.10)
    except Exception as exc:
        try:
            logger.exception("Native runtime duration verification failed: %s", exc)
        except Exception:
            pass
    finally:
        with _RUNTIME_DURATION_VERIFY_LOCK:
            _RUNTIME_DURATION_VERIFY_PENDING.discard(signature)


def _schedule_runtime_duration_verification(event) -> bool:
    if str(getattr(event, "event", "") or "") not in _RUNTIME_DURATION_VERIFY_EVENTS:
        return False
    signature = _runtime_duration_verification_signature(event)
    if not signature.strip("|"):
        return False
    with _RUNTIME_DURATION_VERIFY_LOCK:
        if signature in _RUNTIME_DURATION_VERIFY_PENDING:
            return False
        _RUNTIME_DURATION_VERIFY_PENDING.add(signature)
    thread = _console_threading.Thread(
        target=_run_runtime_duration_verification,
        args=(event, signature),
        name="native-duration-verify",
        daemon=True,
    )
    thread.start()
    return True


def _get_native_lifecycle_coordinator() -> NativeLifecycleCoordinator:
    """Return the process-wide native lifecycle coordinator."""
    global _NATIVE_LIFECYCLE_COORDINATOR
    with _NATIVE_LIFECYCLE_LOCK:
        coordinator = _NATIVE_LIFECYCLE_COORDINATOR
        if coordinator is None:
            coordinator = NativeLifecycleCoordinator(
                process_track_started=_process_native_track_started_event,
                track_started_signature=_native_track_started_signature,
                load_requested_next_track=_native_load_requested_next_track_guarded,
                mark_seek_pending=_ab_mark_native_seek_pending,
                mark_seek_applied=_ab_mark_native_seek_applied,
                reconcile_transition_finished=_ab_reconcile_native_transition_finished,
                mark_hard_handoff_claimed=_ab_mark_native_hard_handoff_claimed,
                mark_hard_handoff_completed=_ab_mark_native_hard_handoff_completed,
                signal_monitor_wake=_ab_signal_monitor_wake,
                report_exception=lambda message, exc: logger.exception("%s: %s", message, exc),
            )
            _NATIVE_LIFECYCLE_COORDINATOR = coordinator
        return coordinator


def _native_engine_event_callback(event) -> None:
    """Forward one native event and schedule non-blocking EOF duration repair."""
    _schedule_runtime_duration_verification(event)
    _get_native_lifecycle_coordinator().handle_event(event)


def _native_queue_contains_queue_id(station_key: str, queue_id: int) -> bool:
    if int(queue_id or 0) <= 0:
        return False
    conn = get_db_for_station(station_key)
    try:
        row = conn.execute("SELECT 1 FROM queue_items WHERE id = ? LIMIT 1", (int(queue_id),)).fetchone()
        return row is not None
    finally:
        conn.close()


def _native_resolve_track_started_line(event) -> str:
    sk = str(getattr(event, "station_key", "") or "").strip()
    queue_id = int(getattr(event, "queue_id", 0) or 0)
    track_id = int(getattr(event, "track_id", 0) or 0)
    path = normalize_media_path(str(getattr(event, "path", "") or ""))
    deck = str(getattr(event, "deck", "") or "A").lower()
    if deck not in ("a", "b"):
        deck = "a"
    with _AB_PLAYER_LOCK:
        lines = list((_AB_PLAYER_STATE or {}).get("lines") or [])
        generation = int((_AB_PLAYER_STATE or {}).get("generation") or 0)
    index = _ab_find_line_index_by_identity(lines, path=path, queue_id=queue_id, track_id=track_id)
    if index < 0:
        lines = _build_station_queue_plan(sk)
        index = _ab_find_line_index_by_identity(lines, path=path, queue_id=queue_id, track_id=track_id)
    if index < 0:
        return ""
    return _ab_runtime_annotate_uri(
        lines[index],
        player=deck,
        generation=generation,
        slot_token=str(getattr(event, "slot_token", "") or ""),
    )


def _native_rebuild_plan_after_track_started(station_key: str, event, started_line: str, state: dict) -> tuple[bool, str]:
    """Keep the audible deck and rebuild only the upcoming native deck plan."""
    fresh_lines = _build_station_queue_plan(station_key)
    planned_lines = [started_line]
    for line in fresh_lines:
        if not _ab_same_queue_identity(line, started_line):
            planned_lines.append(line)

    durations: list[float] = []
    fadeouts: list[float] = []
    for line in planned_lines:
        duration, fadeout = _ab_line_duration_and_fade(line)
        durations.append(duration)
        fadeouts.append(fadeout)

    active = str(getattr(event, "deck", "") or state.get("active_deck") or "A").lower()
    if active not in ("a", "b"):
        active = "a"
    inactive = "b" if active == "a" else "a"
    next_loaded = False
    if len(planned_lines) > 1:
        next_info = _ab_line_info(planned_lines[1])
        try:
            next_queue_id = int(next_info.get("queue_id") or 0)
        except Exception:
            next_queue_id = 0
        try:
            inactive_queue_id = int(state.get(f"deck_{inactive}_queue_id") or 0)
        except Exception:
            inactive_queue_id = 0
        next_loaded = bool(next_queue_id > 0 and inactive_queue_id == next_queue_id)

    started_info = _ab_line_info(started_line)
    started_position, _started_clock_source = _ab_transition_source_position_seconds(
        started_info,
        elapsed_segment_seconds=0.0,
        native_state=state,
        active_player=active,
    )
    started_play_start = max(0.0, float(started_info.get("cue_in") or started_info.get("audio_start") or 0.0))
    started_segment_elapsed = max(0.0, started_position - started_play_start)
    started_wallclock_anchor = time.time() - started_segment_elapsed

    with _AB_PLAYER_LOCK:
        generation = int((_AB_PLAYER_STATE or {}).get("generation") or 0)
        player_index = {active: 0}
        if next_loaded:
            player_index[inactive] = 1
        loaded_keys = dict((_AB_PLAYER_STATE or {}).get("player_loaded_keys") or {})
        loaded_generations = dict((_AB_PLAYER_STATE or {}).get("player_generation") or {})
        if next_loaded and len(planned_lines) > 1:
            next_key = _ab_loaded_identity_key(planned_lines[1])
            if next_key:
                loaded_keys[inactive] = next_key
                loaded_generations[inactive] = generation
        else:
            loaded_keys.pop(inactive, None)
            loaded_generations.pop(inactive, None)
        _AB_PLAYER_STATE.update({
            "enabled": True,
            "station_key": station_key,
            "active": active,
            "lines": planned_lines,
            "durations": durations,
            "fadeouts": fadeouts,
            "current_index": 0,
            "next_index": 1 if len(planned_lines) > 1 else 0,
            "player_index": player_index,
            "player_loaded_keys": loaded_keys,
            "player_generation": loaded_generations,
            "started_at": started_wallclock_anchor,
            "transitioning": bool(state.get("transitioning")),
            "transition_starting": False,
            "seek_pending": False,
            "seek_pending_active": "",
            "seek_pending_queue_id": 0,
            "seek_pending_slot_token": "",
            "seek_pending_deadline": 0.0,
            "seek_applied_at": 0.0,
            "seek_applied_source_position": 0.0,
            "hard_handoff_armed": False,
            "hard_handoff_active": "",
            "hard_handoff_target": "",
            "hard_handoff_current_index": -1,
            "hard_handoff_target_index": -1,
            "hard_handoff_generation": 0,
            "hard_handoff_station_key": "",
            "hard_handoff_from_queue_id": 0,
            "hard_handoff_from_slot_token": "",
            "hard_handoff_to_queue_id": 0,
            "hard_handoff_to_slot_token": "",
            "hard_handoff_native_claimed": False,
            "hard_handoff_completed": False,
            "hard_handoff_completion_source": "",
            "hard_handoff_deadline": 0.0,
        })

    if len(planned_lines) > 1 and not next_loaded:
        if bool(state.get("native_timing_owner")):
            pass
        else:
            delay = _ab_preload_delay_for_active_line(started_line)
            _ab_schedule_inactive_preload_after_start(
                station_key,
                active_player=active,
                inactive_player=inactive,
                line=planned_lines[1],
                next_index=1,
                delay=delay,
                urgent=(delay <= 0.05),
                reason="native_track_started_followup_preload",
            )
    return next_loaded, inactive


def _process_native_track_started_event(event) -> bool:
    sk = str(getattr(event, "station_key", "") or "").strip()
    if not sk:
        return True
    with station_runtime_context(sk):
        with _AB_PLAYER_LOCK:
            player_enabled = bool((_AB_PLAYER_STATE or {}).get("enabled"))
        if not player_enabled:
            return True
        state = _native_station_state(sk)
        if not bool(state.get("running")):
            return True
        queue_id = int(getattr(event, "queue_id", 0) or 0)
        current_queue_id = int(state.get("queue_id") or 0)
        active_deck = str(state.get("active_deck") or "").upper()
        event_deck = str(getattr(event, "deck", "") or "").upper()
        if queue_id > 0 and current_queue_id > 0 and queue_id != current_queue_id:
            return True
        if event_deck and active_deck and event_deck != active_deck:
            return True

        started_line = _native_resolve_track_started_line(event)
        if not started_line:
            return False

        _ab_apply_now_playing_line(sk, started_line, reset_progress=True)
        existed_before = _native_queue_contains_queue_id(sk, queue_id)
        committed = _ab_commit_started_track(sk, started_line, reason="native_track_started_event")
        if existed_before and not committed:
            return False

        filled = autodj_fill_queue_once(replan_after_fill=False)
        state = _native_station_state(sk)
        with _AB_PLAYER_LOCK:
            player_enabled = bool((_AB_PLAYER_STATE or {}).get("enabled"))
        if not player_enabled or not bool(state.get("running")):
            return True
        next_loaded, inactive = _native_rebuild_plan_after_track_started(sk, event, started_line, state)
        start_autodj_thread(sk)
        invalidate_audio_engine_status_cache()
        wake_autodj_worker()
        info = _ab_line_info(started_line)
        _publish_ui_event(
            "now_playing_changed",
            sk,
            "native_track_started",
            {
                "queue_id": int(info.get("queue_id") or queue_id or 0),
                "track_id": int(info.get("track_id") or getattr(event, "track_id", 0) or 0),
                "file": str(info.get("file") or getattr(event, "path", "") or ""),
                "deck": str(getattr(event, "deck", "") or ""),
            },
        )
        _publish_ui_queue_history_changed(sk, "native_track_started")
        _manual_next_mark_lifecycle(
            sk,
            queue_id,
            success=bool(committed or not existed_before),
        )
        return True




def _ensure_native_event_lifecycle() -> None:
    coordinator = _get_native_lifecycle_coordinator()
    coordinator.start(get_audio_engine(), event_handler=_native_engine_event_callback)



def get_audio_engine_status_uncached(station_key: str = ""):
    """Return the authoritative native daemon status for one station."""
    sk = str(station_key or get_active_station_key() or "").strip()
    state = _native_station_state(sk)
    running = bool(state.get("running"))
    return {
        "status": "running" if running else "stopped",
        "pid": None,
        "started_at": None,
        "station_key": sk,
        "audio_engine": "native",
        **state,
    }


def get_audio_engine_status_cached(station_key: str = "", max_age: float = _AUDIO_ENGINE_STATUS_CACHE_TTL) -> dict:
    """Return cached native daemon status for one station."""
    sk = str(station_key or get_active_station_key() or "").strip()
    now = time.time()
    with _AUDIO_ENGINE_STATUS_CACHE_LOCK:
        cached = _AUDIO_ENGINE_STATUS_CACHE.get("data")
        if (
            cached is not None
            and str(_AUDIO_ENGINE_STATUS_CACHE.get("station_key") or "") == sk
            and now - float(_AUDIO_ENGINE_STATUS_CACHE.get("ts") or 0.0) <= float(max_age)
        ):
            return dict(cached)
    data = get_audio_engine_status_uncached(sk)
    with _AUDIO_ENGINE_STATUS_CACHE_LOCK:
        _AUDIO_ENGINE_STATUS_CACHE.update({"ts": now, "station_key": sk, "data": dict(data)})
    return data


def get_audio_engine_status():
    return get_audio_engine_status_cached()


def is_station_on_air(station_key: str) -> bool:
    return bool(_native_station_state(station_key).get("running"))


def _ab_resolve_native_live_player(preferred: str = "a", timeout_sec: float = 0.45) -> tuple[str, dict, str]:
    """Resolve the authoritative native active deck."""
    del timeout_sec
    pref = str(preferred or "a").lower()
    if pref not in ("a", "b"):
        pref = "a"
    sk = str(get_active_station_key() or "").strip()
    state = _native_station_state(sk)
    active = str(state.get("active_deck") or pref).lower()
    if active not in ("a", "b"):
        active = pref
    transitioning = bool(state.get("transitioning"))
    with _AB_PLAYER_LOCK:
        st = dict(_AB_PLAYER_STATE or {})
    lines = list(st.get("lines") or [])
    player_index = dict(st.get("player_index") or {})
    status = {
        "active": active,
        "target": active if transitioning else "",
        "transition": "true" if transitioning else "false",
        "position_ms": int(state.get("position_ms") or 0),
    }
    for player in ("a", "b"):
        try:
            idx = int(player_index.get(player, -1))
        except Exception:
            idx = -1
        status[f"{player}_uri"] = lines[idx] if 0 <= idx < len(lines) else ""
        status[f"{player}_elapsed"] = (
            float(state.get("position_ms") or 0) / 1000.0 if player == active else 0.0
        )
    return active, status, "native"


def _ab_get_current_ui_status(station_key: Optional[str] = None) -> dict:
    sk = str(station_key or get_active_station_key() or "").strip()
    state = _native_station_state(sk)
    if not bool(state.get("running")):
        return {}
    active = str(state.get("active_deck") or "a").lower()
    if active not in ("a", "b"):
        active = "a"
    with station_runtime_context(sk):
        with _AB_PLAYER_LOCK:
            st = dict(_AB_PLAYER_STATE or {})
    lines = list(st.get("lines") or [])
    player_index = dict(st.get("player_index") or {})
    try:
        index = int(player_index.get(active, st.get("current_index") or 0))
    except Exception:
        index = int(st.get("current_index") or 0)
    if not (0 <= index < len(lines)):
        return _ab_get_current_ui_status_wallclock_fallback(station_key=sk)
    info = _ab_line_info(lines[index])
    path = normalize_media_path(str(info.get("file") or ""))
    if not path:
        return {}
    cue_in = max(0.0, float(info.get("cue_in") or 0.0))
    cue_out = max(0.0, float(info.get("cue_out") or 0.0))
    original_total = max(0.0, float(info.get("orig_total") or 0.0))
    segment_duration = max(0.0, float(info.get("segment_duration") or 0.0))
    segment_elapsed = max(0.0, float(state.get("position_ms") or 0) / 1000.0)
    if segment_duration > 0.0:
        segment_elapsed = min(segment_elapsed, segment_duration)
    full_elapsed = cue_in + segment_elapsed
    full_duration = original_total or (cue_in + segment_duration)
    effective_end = cue_out or full_duration
    return {
        "title": str(info.get("title") or ""),
        "artist": str(info.get("artist") or ""),
        "album": str(info.get("album") or ""),
        "year": str(info.get("year") or ""),
        "file": path,
        "elapsed": full_elapsed,
        "duration": full_duration,
        "remaining": max(0.0, effective_end - full_elapsed),
        "seek_base": cue_in,
        "orig_total": original_total,
        "cue_total": cue_out,
        "raw_elapsed": segment_elapsed,
        "raw_duration": segment_duration or full_duration,
        "raw_remaining": max(0.0, (segment_duration or effective_end) - segment_elapsed),
        "source": "native_ab_state",
        "active_player": active,
        "queue_id": int(info.get("queue_id") or 0),
        "track_id": int(info.get("track_id") or 0),
    }


def get_station_ui_status(timeout_sec: float = 0.6, station_key: Optional[str] = None) -> dict:
    del timeout_sec
    return _ab_get_current_ui_status(station_key=station_key)


def _native_stream_output_id(stream_id: int) -> str:
    """Stable native daemon output ID for the Encoders-page database row."""
    return f"stream_{max(0, int(stream_id or 0))}"


def _native_stream_config_from_row(
    row: Mapping[str, Any] | sqlite3.Row,
    *,
    station_key: str,
    enabled: bool,
    dsp_config_path: str = "",
) -> dict[str, Any]:
    """Translate one ``icecast_streams`` database row to native output config."""
    def value(key: str, default: Any = None) -> Any:
        if isinstance(row, dict):
            return row.get(key, default)
        try:
            return row[key] if key in row.keys() else default
        except Exception:
            return default

    stream_id = int(value("id", 0) or 0)
    codec_raw = str(value("codec", "mp3") or "mp3").strip().lower()
    codec = "aac_he_v2" if codec_raw in {
        "aacplusv2", "aacplus", "aac+", "aacv2", "heaac", "he-aac-v2",
        "he_aac_v2", "aac_he_v2",
    } else "mp3"
    mount = str(value("mount", "") or "").strip()
    if mount and not mount.startswith("/"):
        mount = "/" + mount
    bitrate_default = 64 if codec == "aac_he_v2" else 128
    bitrate = int(value("bitrate", bitrate_default) or bitrate_default)
    with station_runtime_context(station_key):
        dsp_enabled = bool(get_dsp_enabled(default=True))
    return {
        "output_id": _native_stream_output_id(stream_id),
        "codec": codec,
        "enabled": bool(enabled),
        "host": str(value("host", "") or "").strip(),
        "port": int(value("port", 8000) or 8000),
        "mount": mount,
        "username": "source",
        "password": str(value("password", "") or ""),
        "bitrate_kbps": bitrate,
        "stream_name": str(value("name", "Web Broadcaster") or "Web Broadcaster").strip(),
        "stream_description": str(value("station_description", "") or "").strip(),
        "stream_genre": str(value("genre", "") or "").strip(),
        "stream_url": str(value("website_url", "") or "").strip(),
        "public_stream": False,
        "add_year_to_metadata": bool(value("add_year_to_icecast_meta", 0)),
        "dsp_enabled": dsp_enabled,
        "dsp_config_path": dsp_config_path if dsp_enabled else "",
    }


def _load_native_output_runtime_configs(station_key: str) -> list[dict[str, Any]]:
    """Return only Encoders-window streams enabled for automatic startup.

    ``icecast_streams`` is the sole user-facing and runtime source of truth.
    Historical Settings-window native output rows are intentionally ignored.
    The station startup path clears every daemon output before applying this
    list, so an output removed from Encoders cannot survive from an older run.
    """
    conn = get_db_for_station(station_key)
    try:
        rows = conn.execute(
            "SELECT * FROM icecast_streams WHERE COALESCE(autostart, 0) = 1 ORDER BY id ASC"
        ).fetchall()
    finally:
        conn.close()

    configs = [
        _native_stream_config_from_row(row, station_key=station_key, enabled=True)
        for row in rows
    ]
    if not configs:
        return []

    # Reject duplicate endpoints in the authoritative Encoders table.  Loading
    # two outputs for the same mount is never useful and makes ownership unclear.
    seen_endpoints: set[tuple[str, int, str]] = set()
    unique_configs: list[dict[str, Any]] = []
    for item in configs:
        endpoint = (
            str(item.get("host") or "").strip().lower(),
            int(item.get("port") or 8000),
            str(item.get("mount") or "").strip(),
        )
        if endpoint in seen_endpoints:
            continue
        seen_endpoints.add(endpoint)
        unique_configs.append(item)

    with station_runtime_context(station_key):
        dsp_requested = any(bool(item.get("dsp_enabled")) for item in unique_configs)
        dsp_runtime = prepare_native_soundsolution_runtime() if dsp_requested else {}
    for item in unique_configs:
        if item.get("dsp_enabled"):
            item.update(dsp_runtime)
    return unique_configs


def _encoder_action_native(stream_id: int, action: str):
    """Start or stop one Encoders-window output through the native daemon."""
    action = str(action or "").strip().lower()
    if action not in {"start", "stop"}:
        raise ValueError("encoder action must be start or stop")
    station_key = str(get_active_station_key() or "").strip()
    if not station_key:
        raise RuntimeError("No active station selected")
    conn = get_db_for_station(station_key)
    try:
        row = conn.execute("SELECT * FROM icecast_streams WHERE id = ?", (int(stream_id),)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"Encoder stream {stream_id} does not exist")

    engine = get_audio_engine()
    output_id = _native_stream_output_id(stream_id)
    if action == "stop":
        return engine.clear_icecast_output(output_id, station_key=station_key)

    with station_runtime_context(station_key):
        dsp_requested = bool(get_dsp_enabled(default=True))
        dsp_runtime = prepare_native_soundsolution_runtime() if dsp_requested else {}
    config = _native_stream_config_from_row(
        row,
        station_key=station_key,
        enabled=True,
        **dsp_runtime,
    )
    return engine.configure_icecast_output(station_key=station_key, **config)


def _apply_live_dsp_setting(station_key: str) -> dict[str, Any]:
    """Apply the persisted station DSP flag through the live PCM source selector.

    DSP is station-wide and shared by every enabled native encoder output. Reconfiguring
    one enabled ``stream_<id>`` output asks the native engine to swap the stable encoder
    input between dry PCM and SoundSolution-processed PCM without restarting the encoder
    group, resetting its PTS timeline or reconnecting any Icecast output.
    """
    resolved_station = str(station_key or "").strip()
    result = {
        "station_running": False,
        "live_applied": False,
        "reconfigured_stream_id": None,
    }
    if not resolved_station:
        return result

    station_running, outputs = _native_encoder_runtime_snapshot(resolved_station)
    result["station_running"] = bool(station_running)
    if not station_running:
        return result

    enabled_stream_ids: list[int] = []
    for output_id, output in outputs.items():
        if not bool(output.get("enabled")) or not str(output_id).startswith("stream_"):
            continue
        try:
            enabled_stream_ids.append(int(str(output_id).split("_", 1)[1]))
        except (TypeError, ValueError, IndexError):
            continue

    if not enabled_stream_ids:
        result["live_applied"] = True
        return result

    stream_id = min(enabled_stream_ids)
    _encoder_action_native(stream_id, "start")
    result["live_applied"] = True
    result["reconfigured_stream_id"] = stream_id
    return result



def _station_prepare_start_state(_station_key: str) -> None:
    with _AB_PLAYER_LOCK:
        _AB_PLAYER_STATE["stopping"] = False


def _station_mark_runtime_started(station_key: str) -> None:
    with RADIO_STATE_LOCK:
        RADIO_STATE["paused"] = False
        RADIO_STATE["stopped"] = False
    with PROGRESS_LOCK:
        progress = _get_progress_state(station_key)
        progress["paused"] = False
        progress["paused_raw_elapsed"] = 0.0


def _station_cleanup_failed_start(station_key: str) -> None:
    with station_runtime_context(station_key):
        with _AB_PLAYER_LOCK:
            _AB_PLAYER_STATE["enabled"] = False
            _AB_PLAYER_STATE["transitioning"] = False


def _station_prepare_stop_state(station_key: str) -> dict:
    stop_serial = _ab_invalidate_pending_replans(reason="native_stop_requested")
    with _AB_PLAYER_LOCK:
        pre_stop_state = dict(_AB_PLAYER_STATE or {})
        stop_generation = int(pre_stop_state.get("generation") or 0) + 1
        _AB_PLAYER_STATE["enabled"] = False
        _AB_PLAYER_STATE["stopping"] = True
        _AB_PLAYER_STATE["generation"] = stop_generation
        _AB_PLAYER_STATE["transitioning"] = False
        _AB_PLAYER_STATE["transition_starting"] = False
    return {
        "pre_stop_state": pre_stop_state,
        "stop_generation": stop_generation,
        "stop_replan_serial": int(stop_serial or 0),
    }


def _station_restore_failed_stop_state(
    _station_key: str,
    stop_context: dict,
) -> None:
    with _AB_PLAYER_LOCK:
        _AB_PLAYER_STATE.clear()
        _AB_PLAYER_STATE.update(dict(stop_context.get("pre_stop_state") or {}))


def _station_finalize_stop_state(
    station_key: str,
    stop_context: dict,
) -> None:
    with _AB_PLAYER_LOCK:
        _AB_PLAYER_STATE.clear()
        _AB_PLAYER_STATE.update({
            "enabled": False,
            "stopping": False,
            "station_key": station_key,
            "generation": int(stop_context.get("stop_generation") or 0),
            "stop_replan_serial": int(stop_context.get("stop_replan_serial") or 0),
        })


def _station_stop_off_air_automation(station_key: str) -> None:
    _stop_station_scripts_for_off_air(station_key)
    _stop_station_scheduler_rules_for_off_air(station_key)


def _station_mark_runtime_stopped(station_key: str) -> None:
    with RADIO_STATE_LOCK:
        RADIO_STATE["paused"] = False
        RADIO_STATE["stopped"] = False
    with PROGRESS_LOCK:
        progress = _get_progress_state(station_key)
        progress["paused"] = False
        progress["paused_raw_elapsed"] = 0.0


def _get_station_service() -> StationService:
    global _STATION_SERVICE
    with _STATION_SERVICE_LOCK:
        if _STATION_SERVICE is None:
            _STATION_SERVICE = StationService(
                StationServiceDependencies(
                    get_active_station_key=lambda: str(get_active_station_key() or ""),
                    get_engine=get_audio_engine,
                    station_runtime_context=station_runtime_context,
                    native_station_state=lambda station_key: dict(
                        _native_station_state(station_key) or {}
                    ),
                    invalidate_status_cache=invalidate_audio_engine_status_cache,
                    get_started_at=get_audio_engine_started_at_for_station,
                    set_started_at=set_audio_engine_started_at_for_station,
                    clear_started_at=clear_audio_engine_started_at_for_station,
                    prepare_start_state=_station_prepare_start_state,
                    build_queue_plan=lambda station_key: list(
                        _build_station_queue_plan(station_key) or []
                    ),
                    startup_autodj_fill=_autodj_startup_fill_once,
                    load_output_configs=lambda station_key: list(
                        _load_native_output_runtime_configs(station_key) or []
                    ),
                    bootstrap_queue_plan=lambda lines, station_key: bool(
                        _ab_bootstrap_from_queue_plan(lines, station_key=station_key)
                    ),
                    start_autodj_worker=start_autodj_thread,
                    notify_on_air=_notify_on_air_state_changed,
                    mark_runtime_started=_station_mark_runtime_started,
                    cleanup_failed_start=_station_cleanup_failed_start,
                    prepare_stop_state=_station_prepare_stop_state,
                    restore_failed_stop_state=_station_restore_failed_stop_state,
                    finalize_stop_state=_station_finalize_stop_state,
                    stop_off_air_automation=_station_stop_off_air_automation,
                    clear_now_playing=clear_now_playing_for_station,
                    mark_runtime_stopped=_station_mark_runtime_stopped,
                )
            )
        return _STATION_SERVICE


def station_start():
    """Start the active station through the station orchestration service."""
    payload, status_code = _get_station_service().start()
    response = jsonify(payload)
    return response if int(status_code) == 200 else (response, int(status_code))



def station_stop():
    """Stop the active station through the station orchestration service."""
    payload, status_code = _get_station_service().stop()
    response = jsonify(payload)
    return response if int(status_code) == 200 else (response, int(status_code))


# Resolve the authoritative backend and attach the non-blocking native lifecycle subscriber.
_ensure_native_event_lifecycle()


if __name__ == "__main__":


    import argparse

    parser = argparse.ArgumentParser(description="Web Broadcaster")
    parser.add_argument("-p", "--port", type=int, default=15000, help="HTTP port for Web Broadcaster")
    args = parser.parse_args()

    APP_HTTP_PORT = int(args.port)
    os.environ["PORT"] = str(APP_HTTP_PORT)
    # Simple development entrypoint. In production use a WSGI server.
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "15000"))
    try:
        engine = get_audio_engine()
        ensure_ready = getattr(engine, "ensure_ready", None)
        ready = ensure_ready() if callable(ensure_ready) else engine.ping()
    except Exception as exc:
        print(
            f"Web Broadcaster cannot start its internal audio runtime: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2) from exc
    # Keep normal startup output intentionally minimal. Flask's banner and
    # Werkzeug informational/warning startup lines are redundant here.
    # Werkzeug INFO/WARNING startup noise stays suppressed, while real HTTP
    # server errors remain visible in both normal and DEBUG modes.
    try:
        from flask import cli as _flask_cli

        _flask_cli.show_server_banner = lambda *args, **kwargs: None
    except Exception:
        pass
    logging.getLogger("werkzeug").setLevel(_WERKZEUG_LOG_LEVEL)

    print(f"Web Broadcaster is starting on port {APP_HTTP_PORT}.", flush=True)
    print(flush=True)
    print(f"Open http://localhost:{APP_HTTP_PORT} in your browser.", flush=True)
    app.run(
        host="0.0.0.0",
        port=APP_HTTP_PORT,
        debug=False,
        request_handler=_WebBroadcasterRequestHandler,
    )

