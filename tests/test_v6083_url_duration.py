"""Regression coverage for the optional HH:MM:SS URL duration modal."""
from __future__ import annotations

import ast
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class V6083UrlDurationTests(unittest.TestCase):
    def test_duration_modal_has_blank_infinite_value_without_checkbox(self):
        html = (ROOT / 'html/broadcaster.html').read_text(encoding='utf-8')
        ui = (ROOT / 'html/static/broadcaster.js').read_text(encoding='utf-8')
        scheduler = (ROOT / 'html/static/scheduler.js').read_text(encoding='utf-8')
        self.assertIn('Duration (HH:MM:SS)', html)
        self.assertIn('placeholder="HH:MM:SS"', html)
        self.assertIn('Leave empty for infinite playback.', html)
        self.assertNotIn('id="scheduler-url-infinite"', html)
        self.assertNotIn('defaultDuration: 60', ui)
        self.assertNotIn('defaultInfinite: false', ui)
        self.assertIn("return { url, duration: dur, custom_metadata: customMetadata };", scheduler)
        self.assertIn("lines.unshift('URL:' + String(values.duration) + ':' + values.url)", scheduler)

    def test_legacy_backend_duration_contract_is_preserved(self):
        source = (ROOT / 'app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == '_parse_url_duration_from_payload']
        ns = {}
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])),
                     str(ROOT / 'app.py'), 'exec'), ns)
        parse = ns['_parse_url_duration_from_payload']
        self.assertEqual(parse({'duration': -1}), -1)
        self.assertEqual(parse({'duration': 90}), 90)
        self.assertEqual(parse({'duration': 72015}), 72015)
        self.assertEqual(parse({'duration': ''}), -1)
        self.assertEqual(parse({}), -1)
        self.assertIn('f"{duration}:{raw_url}"', source)

    def test_browser_duration_parser_and_dialog(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node.js not installed')
        result = subprocess.run([node, str(ROOT / 'tests/js/url_duration_hhmmss.test.js')],
                                capture_output=True, text=True, timeout=15, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('URL HH:MM:SS/infinite modal JS regression tests passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
