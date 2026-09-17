from __future__ import annotations

import errno
import gc
import _pyio
import sys
import types
import unittest
from unittest import mock

from cheroot_cleanup import (
    _close_writer_before_socket,
    configure_cheroot_connection_cleanup,
)


class _RecordingWriter:
    def __init__(self, events, error: OSError | None = None) -> None:
        self.events = events
        self.error = error
        self.closed = False

    def close(self) -> None:
        self.events.append("writer")
        self.closed = True
        if self.error is not None:
            raise self.error


class _BadDescriptorRaw(_pyio.RawIOBase):
    def writable(self) -> bool:
        return True

    def write(self, data: bytes) -> int:
        raise OSError(errno.EBADF, "Bad file descriptor")


class V6043CherootConnectionCleanupTests(unittest.TestCase):
    def test_expected_disconnect_errors_are_suppressed_after_writer_is_closed(self) -> None:
        for error_number in (
            errno.EBADF,
            errno.EPIPE,
            errno.ECONNRESET,
            errno.ECONNABORTED,
            errno.ENOTCONN,
        ):
            with self.subTest(error_number=error_number):
                writer = _RecordingWriter([], OSError(error_number, "disconnect"))
                _close_writer_before_socket(writer)
                self.assertTrue(writer.closed)

    def test_unexpected_writer_close_error_is_not_hidden(self) -> None:
        writer = _RecordingWriter([], OSError(errno.EIO, "unexpected I/O failure"))
        with self.assertRaises(OSError) as caught:
            _close_writer_before_socket(writer)
        self.assertEqual(caught.exception.errno, errno.EIO)
        self.assertTrue(writer.closed)

    def test_pyio_buffered_writer_is_closed_even_when_ebadf_flush_fails(self) -> None:
        writer = _pyio.BufferedWriter(_BadDescriptorRaw(), buffer_size=8192)
        writer.write(b"pending response bytes")
        _close_writer_before_socket(writer)
        self.assertTrue(writer.closed)

        # A closed writer no longer retries the failed flush from IOBase.__del__.
        seen = []
        original_hook = sys.unraisablehook
        sys.unraisablehook = lambda value: seen.append(value)
        try:
            del writer
            gc.collect()
        finally:
            sys.unraisablehook = original_hook
        self.assertEqual(seen, [])

    def test_connection_subclass_closes_writer_before_base_socket_cleanup(self) -> None:
        events = []

        class FakeCherootHTTPConnection:
            def close(self) -> None:
                events.append("base")

        fake_package = types.ModuleType("cheroot")
        fake_server_module = types.ModuleType("cheroot.server")
        fake_server_module.HTTPConnection = FakeCherootHTTPConnection
        server = types.SimpleNamespace(ConnectionClass=FakeCherootHTTPConnection)

        with mock.patch.dict(
            sys.modules,
            {"cheroot": fake_package, "cheroot.server": fake_server_module},
        ):
            configure_cheroot_connection_cleanup(server)

        conn = server.ConnectionClass()
        conn.wfile = _RecordingWriter(events)
        conn.close()
        self.assertEqual(events, ["writer", "base"])
        self.assertTrue(conn.wfile.closed)

    def test_base_cleanup_still_runs_when_writer_close_raises_unexpected_error(self) -> None:
        events = []

        class FakeCherootHTTPConnection:
            def close(self) -> None:
                events.append("base")

        fake_package = types.ModuleType("cheroot")
        fake_server_module = types.ModuleType("cheroot.server")
        fake_server_module.HTTPConnection = FakeCherootHTTPConnection
        server = types.SimpleNamespace(ConnectionClass=FakeCherootHTTPConnection)

        with mock.patch.dict(
            sys.modules,
            {"cheroot": fake_package, "cheroot.server": fake_server_module},
        ):
            configure_cheroot_connection_cleanup(server)

        conn = server.ConnectionClass()
        conn.wfile = _RecordingWriter(events, OSError(errno.EIO, "unexpected"))
        with self.assertRaises(OSError):
            conn.close()
        self.assertEqual(events, ["writer", "base"])

    def test_real_cheroot_class_uses_custom_connection_when_dependency_is_available(self) -> None:
        try:
            from cheroot.wsgi import Server as CherootServer
        except ImportError:
            self.skipTest("Cheroot is installed by the official buildkit before regression tests")

        server = CherootServer(("127.0.0.1", 0), lambda _env, _start: [b""])
        original = server.ConnectionClass
        configure_cheroot_connection_cleanup(server)
        self.assertIsNot(server.ConnectionClass, original)
        self.assertTrue(issubclass(server.ConnectionClass, original))


if __name__ == "__main__":
    unittest.main()
