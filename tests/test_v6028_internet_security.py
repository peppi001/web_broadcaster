import re
import unittest
from pathlib import Path


class V6028InternetSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.base = (cls.root / "html" / "base.html").read_text(encoding="utf-8")
        cls.login = (cls.root / "html" / "login.html").read_text(encoding="utf-8")
        cls.setup = (cls.root / "html" / "setup.html").read_text(encoding="utf-8")
        cls.broadcaster = (cls.root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        cls.requirements = (cls.root / "requirements.txt").read_text(encoding="utf-8")
        cls.native_header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")


    def test_no_known_default_secret_remains(self) -> None:
        self.assertNotIn("change_this_secret_key", self.app)
        self.assertIn('Path(DB_DIR) / ".session_secret"', self.app)
        self.assertIn("secrets.token_urlsafe(48)", self.app)
        self.assertIn("os.O_EXCL, 0o600", self.app)

    def test_authentication_is_deny_by_default(self) -> None:
        self.assertIn('_PUBLIC_ENDPOINTS = frozenset({"login", "setup", "static"})', self.app)
        self.assertIn("def _internet_security_gate():", self.app)
        self.assertIn('if endpoint not in _PUBLIC_ENDPOINTS and not session.get("user_id"):', self.app)
        for marker in (
            '@app.route("/stations/select", methods=["POST"])\n@login_required',
            '@app.route("/api/audio-engine/status", methods=["GET"], endpoint="api_audio_engine_status")\n@login_required',
            '@app.route("/api/encoders", methods=["GET"])\n@login_required',
            '@app.route("/api/dashboard_overview", methods=["GET"])\n@login_required',
        ):
            self.assertIn(marker, self.app)

    def test_csrf_protection_covers_unsafe_methods_and_browser_fetch(self) -> None:
        self.assertIn('_SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})', self.app)
        self.assertIn('request.headers.get("X-CSRF-Token")', self.app)
        self.assertIn("secrets.compare_digest(supplied, expected)", self.app)
        self.assertIn('<meta name="csrf-token" content="{{ csrf_token }}">', self.base)
        self.assertIn('headers.set("X-CSRF-Token", csrfToken)', self.base)
        self.assertIn('name="_csrf_token" value="{{ csrf_token }}"', self.login)
        self.assertIn('name="_csrf_token" value="{{ csrf_token }}"', self.setup)
        self.assertIn('@app.route("/logout", methods=["POST"])', self.app)
        self.assertNotRegex(self.broadcaster, r'href="\{\{ url_for\([\'\"]logout')

    def test_first_run_requires_private_setup_token(self) -> None:
        self.assertIn('Path(DB_DIR) / ".setup_token"', self.app)
        self.assertIn("secrets.token_urlsafe(32)", self.app)
        self.assertIn("Invalid initial setup token.", self.app)
        self.assertIn('name="setup_token"', self.setup)
        self.assertIn("_consume_setup_token()", self.app)

    def test_session_host_and_request_hardening(self) -> None:
        for marker in (
            'SESSION_COOKIE_NAME="wb_session"',
            'SESSION_COOKIE_HTTPONLY=True',
            'SESSION_COOKIE_SAMESITE="Lax"',
            'PERMANENT_SESSION_LIFETIME=timedelta(hours=8)',
            'MAX_CONTENT_LENGTH=4 * 1024 * 1024',
            'MAX_FORM_MEMORY_SIZE=256 * 1024',
            'MAX_FORM_PARTS=100',
            'TRUSTED_HOSTS=_parse_trusted_hosts',
        ):
            self.assertIn(marker, self.app)

    def test_security_headers_are_present(self) -> None:
        for header in (
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
            "Cross-Origin-Resource-Policy",
            "Content-Security-Policy",
            "Strict-Transport-Security",
            "Cache-Control",
        ):
            self.assertIn(header, self.app)

    def test_production_server_and_direct_tls_are_used(self) -> None:
        self.assertNotIn("app.run(", self.app)
        self.assertIn("from cheroot.wsgi import Server as CherootServer", self.app)
        self.assertIn("from cheroot.ssl.builtin import BuiltinSSLAdapter", self.app)
        self.assertIn("tls_adapter = BuiltinSSLAdapter(args.cert_file, args.key_file)", self.app)
        self.assertIn("tls_adapter.context.minimum_version = ssl.TLSVersion.TLSv1_2", self.app)
        self.assertIn("server.ssl_adapter = tls_adapter", self.app)
        self.assertIn("server.max_request_header_size = 64 * 1024", self.app)
        self.assertIn("ProxyFix", self.app)
        self.assertIn('parser.add_argument("--public-internet"', self.app)
        self.assertIn('--public-internet refuses plain HTTP', self.app)

    def test_current_web_dependency_pins_are_explicit(self) -> None:
        self.assertIn("flask==3.1.3", self.requirements)
        self.assertIn("werkzeug==3.1.8", self.requirements)
        self.assertIn("Flask-Limiter==4.1.1", self.requirements)
        self.assertIn("cheroot==11.1.2", self.requirements)

    def test_new_password_policy(self) -> None:
        self.assertGreaterEqual(self.app.count("len(password) < 12"), 3)
        self.assertIn('minlength="12"', self.setup)
        self.assertIn('minlength="12"', self.broadcaster)

    def test_global_rate_limit_is_present(self) -> None:
        self.assertIn('default_limits=["600 per minute"]', self.app)


if __name__ == "__main__":
    unittest.main()
