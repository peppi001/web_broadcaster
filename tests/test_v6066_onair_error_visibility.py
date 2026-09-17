from pathlib import Path


def test_studio_onair_toggle_surfaces_backend_error_to_user() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")

    assert "const response = await fetch(`/audio-engine/${cmd}`, {method: 'POST'});" in source
    assert "const result = await response.json().catch(() => ({}));" in source
    assert "response.ok && result && result.success === true" in source
    assert "result.error || result.detail" in source
    assert "openStudioEngineErrorModal" in source
    assert "window.alert(`Unable to ${actionLabel}.\\n\\n${detail}`);" not in source
    assert "return requestSucceeded;" in source


def test_studio_onair_toggle_surfaces_network_failure_to_user() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")

    assert "Unknown network error" in source
    assert "await openStudioEngineErrorModal" in source
    assert "console.error('Unable to toggle audio engine from studio', error);" in source
