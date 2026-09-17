from pathlib import Path


def test_missing_ss18_uses_studio_modal_and_recovery_action() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "html" / "static" / "broadcaster.js").read_text(encoding="utf-8")
    template = (root / "html" / "broadcaster.html").read_text(encoding="utf-8")

    assert "studio-engine-error-window" in template
    assert "Use found path and retry" in template
    assert "openStudioEngineErrorModal" in source
    assert "repairSoundSolutionConfigPath" in source
    assert "/api/studio/settings/soundsolution-recovery" in source
    assert "return requestAudioEngineCommand(cmd, false);" in source
    assert "window.alert(`Unable to ${actionLabel}.\\n\\n${detail}`);" not in source


def test_backend_discovers_verified_ss18_and_updates_station_setting() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app.py").read_text(encoding="utf-8")

    assert '_BUNDLED_SS18_SHA256 = "a645ef67b888c6a87956420567fe39f09c51e8e540d9f320fe99995559f71b71"' in source
    assert "def _discover_soundsolution_config_path" in source
    assert "_is_recovery_soundsolution_config" in source
    assert '"type": "soundsolution_config_missing"' in source
    assert '@app.route("/api/studio/settings/soundsolution-recovery", methods=["POST"])' in source
    assert "UPDATE settings SET ssproc_appimage = ?, updated_at = ? WHERE id = ?" in source
    assert 'payload["recovery"] = recovery' in source
