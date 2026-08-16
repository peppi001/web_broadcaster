from __future__ import annotations

import unittest
from pathlib import Path


class V6042DeploymentModesDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.guide = (cls.root / "docs" / "DEPLOYMENT_GUIDE.md").read_text(encoding="utf-8")
        cls.nginx_guide = (cls.root / "docs" / "NGINX_PUBLIC_HTTPS.md").read_text(encoding="utf-8")
        cls.app = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.native_header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")

    def test_version_is_synchronized(self) -> None:
        self.assertIn('APP_VERSION = "6042"', self.app)
        self.assertIn('#define WB_NATIVE_DAEMON_VERSION "6042"', self.native_header)

    def test_lan_only_mode_is_explicitly_documented(self) -> None:
        for marker in (
            'Mode A — trusted LAN only',
            'PUBLIC_INTERNET_MODE="OFF"',
            'BIND_HOST="0.0.0.0"',
            'TRUSTED_HOSTS=""',
            'HTTPS_MODE="OFF"',
            'PROXY_COUNT="0"',
            'http://192.168.1.50:15000',
            "ss -ltnp | grep ':15000'",
            'Do not create an Internet/NAT port-forward for TCP 15000.',
            'nginx, a public domain name, Certbot, and a TLS certificate are not required.',
        ):
            self.assertIn(marker, self.guide)

    def test_lan_security_boundary_is_documented(self) -> None:
        for marker in (
            'HTTP traffic itself is not encrypted',
            'Use this mode only on a trusted private network.',
            'one-time setup token',
            'db/.session_secret',
        ):
            self.assertIn(marker, self.guide)

    def test_public_nginx_mode_remains_documented(self) -> None:
        for marker in (
            'Mode B — public Internet through nginx',
            'PUBLIC_INTERNET_MODE="ON"',
            'BIND_HOST="127.0.0.1"',
            'TRUSTED_HOSTS="radio.example.com"',
            'PROXY_COUNT="1"',
            'docs/NGINX_PUBLIC_HTTPS.md',
            'docs/nginx_web_broadcaster.conf.example',
        ):
            self.assertIn(marker, self.guide)

    def test_detailed_nginx_guide_points_back_to_mode_guide(self) -> None:
        self.assertIn('DEPLOYMENT_GUIDE.md', self.nginx_guide)
        self.assertIn('including direct LAN-only operation without nginx, a domain, or TLS', self.nginx_guide)


if __name__ == "__main__":
    unittest.main()
