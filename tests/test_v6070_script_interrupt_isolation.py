from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"


def _function_source(name: str) -> str:
    text = APP_PATH.read_text(encoding="utf-8")
    module = ast.parse(text)
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            source = ast.get_source_segment(text, node)
            assert source is not None
            return source
    raise AssertionError(f"Function not found: {name}")


def test_script_interrupt_keeps_legacy_control_guards_unchanged() -> None:
    source = _function_source("_ab_start_cueout_transition_now")
    assert 'if bool(st0.get("hard_handoff_armed")) and not bool(manual_next_fast):' in source
    assert 'if bool(st0.get("seek_pending")) and not bool(manual_next_fast):' in source
    assert 'reject_if_active_deck=bool(manual_next_fast)' in source
    assert 'reject_if_playback_started=bool(manual_next_fast)' in source


def test_script_interrupt_uses_separate_strict_prebuffer_branch() -> None:
    source = _function_source("_ab_start_cueout_transition_now")
    assert "if script_interrupt and not hard_select:" in source
    assert "if script_interrupt and not hard_select and need_push:" in source
    assert 'manual_next_fast=False' in source
    assert 'reject_if_active_deck=True' in source
    assert 'reject_if_playback_started=True' in source
    assert "_ab_wait_for_native_deck_prebuffer(" in source
    assert "elif script_interrupt:" in source
    assert "_ab_script_interrupt_to(" in source
