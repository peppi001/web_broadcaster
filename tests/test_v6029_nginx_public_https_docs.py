from __future__ import annotations

import unittest
from pathlib import Path


class V6029NginxPublicHttpsDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.guide = (cls.root / "docs" / "NGINX_PUBLIC_HTTPS.md").read_text(encoding="utf-8")
        cls.nginx = (cls.root / "docs" / "nginx_web_broadcaster.conf.example").read_text(encoding="utf-8")
        cls.app = (cls.root / "app.py").read_text(encoding="utf-8")
        cls.native_header = (cls.root / "native_engine" / "include" / "engine.h").read_text(encoding="utf-8")


    def test_guide_documents_exact_supported_nginx_mode(self) -> None:
        for marker in (
            'PUBLIC_INTERNET_MODE="ON"',
            'BIND_HOST="127.0.0.1"',
            'TRUSTED_HOSTS="radio.example.com"',
            'HTTPS_MODE="OFF"',
            'PROXY_COUNT="1"',
            'certbot certonly --nginx -d radio.example.com',
            'certbot renew --dry-run',
            'db/.setup_token',
            'db/.session_secret',
            "ss -ltnp | grep ':15000'",
        ):
            self.assertIn(marker, self.guide)

    def test_nginx_example_is_loopback_only_and_forwards_security_headers(self) -> None:
        self.assertEqual(self.nginx.count('proxy_pass http://127.0.0.1:15000;'), 3)
        for marker in (
            'proxy_set_header Host $host;',
            'proxy_set_header X-Real-IP $remote_addr;',
            'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
            'proxy_set_header X-Forwarded-Host $host;',
            'proxy_set_header X-Forwarded-Proto $scheme;',
            'client_max_body_size 4m;',
            'ssl_protocols TLSv1.2 TLSv1.3;',
        ):
            self.assertIn(marker, self.nginx)

    def test_both_sse_routes_disable_buffering(self) -> None:
        self.assertIn('location = /api/console/stream', self.nginx)
        self.assertIn('location = /api/ui/events', self.nginx)
        self.assertEqual(self.nginx.count('proxy_buffering off;'), 2)
        self.assertEqual(self.nginx.count('proxy_cache off;'), 2)
        self.assertEqual(self.nginx.count('proxy_read_timeout 3600s;'), 2)

    def test_public_edge_exposes_only_http_https(self) -> None:
        self.assertIn('TCP 80  -> nginx host TCP 80', self.guide)
        self.assertIn('TCP 443 -> nginx host TCP 443', self.guide)
        self.assertIn('Do not forward TCP 15000.', self.guide)


if __name__ == "__main__":
    unittest.main()
