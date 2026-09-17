from __future__ import annotations

import errno
from typing import Any


# Cheroot 11.1.2 closes the read stream and kernel socket from
# HTTPConnection.close(), but leaves the buffered write stream for Python's
# later IOBase finalizer. If the peer/socket has already disappeared, that
# delayed finalizer can try to flush pending bytes through a dead descriptor
# and print an "Exception ignored in IOBase.__del__" traceback. Close the
# writer explicitly while the connection still owns the socket.
_EXPECTED_WRITER_CLOSE_ERRNOS = frozenset(
    code
    for code in (
        errno.EBADF,
        errno.EPIPE,
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.ENOTCONN,
        getattr(errno, "ESHUTDOWN", None),
    )
    if code is not None
)


def _close_writer_before_socket(writer: Any) -> None:
    """Close a Cheroot response writer, ignoring only normal disconnect errors."""
    if writer is None or bool(getattr(writer, "closed", False)):
        return

    try:
        writer.close()
    except OSError as exc:
        if exc.errno not in _EXPECTED_WRITER_CLOSE_ERRNOS:
            raise


def configure_cheroot_connection_cleanup(server: Any) -> None:
    """Install Web Broadcaster's deterministic Cheroot connection cleanup."""
    from cheroot.server import HTTPConnection as CherootHTTPConnection

    class WebBroadcasterCherootHTTPConnection(CherootHTTPConnection):
        """Close the buffered response writer before Cheroot closes the socket."""

        def close(self) -> None:
            # The finally is intentional: an unexpected writer-close failure must
            # still allow Cheroot to release rfile/socket resources, then propagate.
            try:
                _close_writer_before_socket(getattr(self, "wfile", None))
            finally:
                super().close()

    server.ConnectionClass = WebBroadcasterCherootHTTPConnection
