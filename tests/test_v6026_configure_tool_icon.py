from __future__ import annotations

import unittest
from pathlib import Path


class V6026ConfigureToolIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.html = (root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        cls.css = (root / "html" / "static" / "broadcaster.css").read_text(encoding="utf-8")

    def test_both_configure_buttons_use_same_css_tool_icon(self) -> None:
        self.assertEqual(self.html.count('class="encoder-config-icon"'), 2)
        self.assertEqual(self.html.count('encoder-config-icon__hammer-head'), 2)
        self.assertEqual(self.html.count('encoder-config-icon__driver-handle'), 2)

    def test_final_icon_geometry_matches_selected_candidate_35(self) -> None:
        self.assertIn('width:15px;height:15px', self.css)
        self.assertIn('rotate(48deg) translateY(-.4px)', self.css)
        self.assertIn('rotate(-48deg) translateY(.4px)', self.css)
        self.assertIn('transform:rotate(180deg)', self.css)
        self.assertIn('translate(.25px,.35px) scale(.72)', self.css)
        self.assertIn('translateY(-.2px) scaleY(1.24)', self.css)

    def test_old_pseudo_element_glyph_is_removed(self) -> None:
        self.assertNotIn('.encoder-config-icon::before', self.css)
        self.assertNotIn('.encoder-config-icon::after', self.css)


if __name__ == "__main__":
    unittest.main()
