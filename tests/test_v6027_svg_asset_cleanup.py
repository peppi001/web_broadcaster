from __future__ import annotations

import unittest
from pathlib import Path


class V6027SvgAssetCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.html_root = cls.root / "html"
        cls.static_root = cls.html_root / "static"

    def test_retired_icons_directory_is_absent(self) -> None:
        self.assertFalse((self.static_root / "icons").exists())

    def test_no_svg_web_assets_remain(self) -> None:
        self.assertEqual(list(self.html_root.rglob("*.svg")), [])
        self.assertEqual(list(self.html_root.rglob("*.SVG")), [])

    def test_no_svg_or_icons_directory_references_remain_in_web_source(self) -> None:
        offenders = []
        for path in self.html_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".html", ".css", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            if ".svg" in text.lower() or "icons/" in text.lower():
                offenders.append(str(path.relative_to(self.root)))
        self.assertEqual(offenders, [])

    def test_v6026_css_configure_icon_is_preserved(self) -> None:
        broadcaster = (self.html_root / "broadcaster.html").read_text(encoding="utf-8")
        css = (self.static_root / "broadcaster.css").read_text(encoding="utf-8")
        self.assertEqual(broadcaster.count('class="encoder-config-icon"'), 2)
        self.assertIn('rotate(48deg) translateY(-.4px)', css)
        self.assertIn('translate(.25px,.35px) scale(.72)', css)
        self.assertIn('translateY(-.2px) scaleY(1.24)', css)


if __name__ == "__main__":
    unittest.main()
