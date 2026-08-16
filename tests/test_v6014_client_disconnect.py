from __future__ import annotations

import unittest
from pathlib import Path


class V6015ClientDisconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.source = (cls.root / "app.py").read_text(encoding="utf-8")

    def test_obsolete_werkzeug_request_handler_is_removed(self) -> None:
        self.assertNotIn("_WebBroadcasterRequestHandler", self.source)
        self.assertNotIn("WSGIRequestHandler", self.source)
        self.assertNotIn("_IGNORABLE_CLIENT_SOCKET_ERRNOS", self.source)

    def test_production_http_server_is_cheroot(self) -> None:
        self.assertIn("from cheroot.wsgi import Server as CherootServer", self.source)
        self.assertIn("server = CherootServer((host, port), app, numthreads=16)", self.source)
        self.assertNotIn("app.run(", self.source)


if __name__ == "__main__":
    unittest.main()
