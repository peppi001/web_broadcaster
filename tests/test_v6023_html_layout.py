from __future__ import annotations

import unittest
from pathlib import Path


class V6023HtmlLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / "app.py").read_text(encoding="utf-8")

    def test_web_assets_are_consolidated_under_html(self) -> None:
        self.assertTrue((self.root / "html").is_dir())
        self.assertTrue((self.root / "html" / "static").is_dir())
        self.assertTrue((self.root / "html" / "broadcaster.html").is_file())
        self.assertTrue((self.root / "html" / "base.html").is_file())
        self.assertTrue((self.root / "html" / "static" / "broadcaster.js").is_file())
        self.assertFalse((self.root / "templates").exists())
        self.assertFalse((self.root / "static").exists())

    def test_flask_uses_html_root_and_html_static(self) -> None:
        self.assertIn('template_folder=os.path.join(BASE_DIR, "html")', self.app_source)
        self.assertIn('static_folder=os.path.join(BASE_DIR, "html", "static")', self.app_source)
        self.assertIn("return os.path.join(BASE_DIR, 'html', filename)", self.app_source)


if __name__ == "__main__":
    unittest.main()
