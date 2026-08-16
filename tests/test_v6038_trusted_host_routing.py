from __future__ import annotations

import re
import unittest
from pathlib import Path


class V6038TrustedHostRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_text = (cls.root / "app.py").read_text(encoding="utf-8")

    def _security_gate_body(self) -> str:
        match = re.search(
            r"(?ms)^@app\.before_request\ndef _internet_security_gate\(\):\n(?P<body>.*?)(?=^@app\.after_request)",
            self.app_text,
        )
        self.assertIsNotNone(match, "internet security gate not found")
        return match.group("body")

    def test_routing_exception_is_propagated_before_auth_redirect(self) -> None:
        body = self._security_gate_body()
        routing_lookup = 'routing_error = getattr(request, "routing_exception", None)'
        routing_raise = "if routing_error is not None:\n        raise routing_error"
        auth_redirect = 'return redirect(url_for("login"))'
        self.assertIn(routing_lookup, body)
        self.assertIn(routing_raise, body)
        self.assertIn(auth_redirect, body)
        self.assertLess(body.index(routing_lookup), body.index(auth_redirect))
        self.assertLess(body.index(routing_raise), body.index(auth_redirect))

    def test_gate_documents_flask_31_trusted_host_routing_order(self) -> None:
        body = self._security_gate_body()
        self.assertIn("Flask 3.1 performs TRUSTED_HOSTS validation during routing", body)
        self.assertIn("invalid Host requests at HTTP 400", body)
        self.assertIn("unknown routes at 404", body)


if __name__ == "__main__":
    unittest.main()
