from __future__ import annotations

import unittest
from pathlib import Path


class RetiredNativeSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.template = (root / "html" / "broadcaster.html").read_text(encoding="utf-8")
        cls.javascript = (root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
        cls.stylesheet = (root / "html" / "static" / "broadcaster.css").read_text(encoding="utf-8")

    def test_settings_navigation_contains_no_native_test_or_native_icecast(self) -> None:
        for retired in (
            "Native Test",
            "Native Icecast",
            'data-settings-section="native-test"',
            'data-settings-section="native-icecast"',
            'data-settings-panel="native-test"',
            'data-settings-panel="native-icecast"',
        ):
            self.assertNotIn(retired, self.template)

    def test_browser_has_no_retired_native_controls_or_api_calls(self) -> None:
        for retired in (
            "studio-native-test",
            "studio-native-icecast",
            "/api/audio-engine/fault-test",
            "/api/audio-engine/native-icecast",
            "refreshNativeFaultTest",
            "refreshNativeIcecastOutput",
        ):
            self.assertNotIn(retired, self.javascript)
        self.assertNotIn("studio-native-test", self.stylesheet)
        self.assertNotIn("studio-native-icecast", self.stylesheet)


if __name__ == "__main__":
    unittest.main()
