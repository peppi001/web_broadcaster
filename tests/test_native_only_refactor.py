from __future__ import annotations
import ast
import inspect
import unittest
from pathlib import Path
from audio_engine import create_audio_engine

class NativeOnlyRefactorTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.app_source = (cls.root / 'app.py').read_text(encoding='utf-8')
        cls.base_template = (cls.root / 'html' / 'base.html').read_text(encoding='utf-8')
        cls.dashboard_template = (cls.root / 'html' / 'dashboard.html').read_text(encoding='utf-8')
        cls.nav_source = (cls.root / 'html' / 'static' / 'audio_engine_nav.js').read_text(encoding='utf-8')
        cls.broadcaster_source = (cls.root / 'html' / 'static' / 'broadcaster.js').read_text(encoding='utf-8')
        cls.broadcaster_template = (cls.root / 'html' / 'broadcaster.html').read_text(encoding='utf-8')
        cls.native_engine_source = (cls.root / 'native_engine' / 'src' / 'engine.c').read_text(encoding='utf-8')
        cls.native_icecast_source = (cls.root / 'native_engine' / 'src' / 'icecast_output.c').read_text(encoding='utf-8')
        cls.native_audio_probe_source = (cls.root / 'native_engine' / 'src' / 'audio_probe.c').read_text(encoding='utf-8')
        cls.native_header_source = (cls.root / 'native_engine' / 'include' / 'engine.h').read_text(encoding='utf-8')

    @classmethod
    def _function_source(cls, name: str) -> str:
        tree = ast.parse(cls.app_source)
        node = next((item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name))
        lines = cls.app_source.splitlines()
        return '\n'.join(lines[node.lineno - 1:node.end_lineno])

    def test_v5070_retired_descriptor_and_mismatch_read_aliases_are_removed(self) -> None:
        protocol_source = (self.root / 'audio_engine' / 'protocol.py').read_text(encoding='utf-8')
        native_client_source = (self.root / 'audio_engine' / 'native_engine.py').read_text(encoding='utf-8')
        for retired_read in ('seconds_as_ms("fade_in", "liq_fade_in")', 'seconds_as_ms("fade_out", "wb_fade_duration"', '"liq_cue_in"', '"liq_cue_out"', '"liq_fade_in"', '"liq_fade_out"'):
            self.assertNotIn(retired_read, protocol_source)
        self.assertNotIn('legacy = f"audio_shadow_mismatch_{suffix}"', native_client_source)
        self.assertNotIn('audio_shadow_mismatch_count;', self.native_header_source)
        self.assertNotIn('native_audio_shadow_mismatch', self.native_audio_probe_source)
        self.assertIn('native_audio_runtime_mismatch', self.native_audio_probe_source)
        self.assertIn('"audio_runtime_mismatch_count"', protocol_source)

    def test_v5079_descriptor_fade_fields_remain_canonical(self) -> None:
        helper_start = self.app_source.index('def _ab_native_runtime_timing_metadata(')
        playlist_start = self.app_source.index('def _build_station_queue_plan(', helper_start)
        generated = self.app_source[helper_start:playlist_start]
        self.assertIn('f\'cue_out="{cue_out:.3f}"\'', generated)
        self.assertIn('\'fade_in="0.000"\'', generated)
        self.assertIn('f\'fade_out="{fade_out:.3f}"\'', generated)
        self.assertNotIn('wb_fade_out="', generated)
        self.assertNotIn('wb_fade_duration="', generated)
        sanitizer_start = self.app_source.index('def _ab_sanitize_timing_metadata')
        sanitizer_end = self.app_source.index('def _parse_annotate_meta', sanitizer_start)
        sanitizer = self.app_source[sanitizer_start:sanitizer_end]
        self.assertIn('src["cue_out"]', sanitizer)
        self.assertIn('src["fade_in"]', sanitizer)
        self.assertIn('src["fade_out"]', sanitizer)
        self.assertNotIn('src["wb_fade_out"]', sanitizer)
        self.assertNotIn('src["wb_fade_duration"]', sanitizer)

    def test_native_pause_protocol_is_exposed_in_state(self) -> None:
        self.assertIn('strcmp(command, "set_paused")', self.native_engine_source)
        self.assertIn('\\"paused\\":%s', self.native_engine_source)
        status = self._function_source('_native_api_status_payload')
        self.assertIn('paused = bool(state.get("paused"))', status)
        self.assertIn('"pause_active": paused', status)

    def test_native_ui_status_fallback_is_defined_before_runtime_override(self) -> None:
        tree = ast.parse(self.app_source)
        positions = {node.name: node.lineno for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in {'_ab_get_current_ui_status_wallclock_fallback', '_ab_get_current_ui_status'}}
        self.assertIn('_ab_get_current_ui_status_wallclock_fallback', positions)
        self.assertIn('_ab_get_current_ui_status', positions)
        self.assertLess(positions['_ab_get_current_ui_status_wallclock_fallback'], positions['_ab_get_current_ui_status'])
        fallback = self._function_source('_ab_get_current_ui_status_wallclock_fallback')
        self.assertNotIn('_ab_get_current_ui_status(', fallback)
        self.assertNotIn('_ab_get_current_ui_status_wallclock_fallback = _ab_get_current_ui_status', self.app_source)

    def test_runtime_has_no_native_test_stream_defaults(self) -> None:
        runtime_source = self.native_engine_source + '\n' + self.native_icecast_source
        self.assertNotIn('/native-test.mp3', runtime_source)
        self.assertNotIn('Web Broadcaster Native Test', runtime_source)
    def test_repository_contains_no_retired_backend_identifier(self) -> None:
        retired_token = "liquid" + "soap"
        text_suffixes = {".py", ".c", ".h", ".md", ".txt", ".html", ".js", ".css", ".wbs"}
        hits = []
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in text_suffixes:
                continue
            if any(part in {"__pycache__", ".pytest_cache"} for part in path.parts):
                continue
            if path.name == "version.txt":
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            if retired_token in content.lower() or retired_token in path.name.lower():
                hits.append(str(path.relative_to(self.root)))
        self.assertEqual(hits, [])

if __name__ == '__main__':
    unittest.main()
