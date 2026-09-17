from __future__ import annotations

from pathlib import Path

from player import PlayerHandoffDependencies, PlayerHandoffService


def _service(*, remaining_ms: int = 20000, configured_fade: float = 5.0):
    state = {
        "enabled": True,
        "active": "a",
        "generation": 10,
        "lines": ["active"],
        "player_index": {"a": 0},
        "started_at": 123.0,
    }
    transition_calls: list[dict] = []

    def mutate(callback):
        return callback(state)

    service = PlayerHandoffService(
        PlayerHandoffDependencies(
            get_active_station_key=lambda: "station.db",
            build_queue_plan=lambda station: ["script", "tail"],
            line_info=lambda line: {
                "queue_id": 22 if line == "script" else 23,
                "track_id": 122 if line == "script" else 123,
            },
            read_player_state=lambda: dict(state),
            mutate_player_state=mutate,
            native_station_state=lambda station: {
                "running": True,
                "active_deck": "A",
                "queue_id": 21,
                "native_audio_probe_position_ms": 10000,
                "native_audio_probe_effective_end_ms": 10000 + remaining_ms,
            },
            reconcile_stale_transition=lambda station, native: False,
            trace_manual_next=lambda *args, **kwargs: None,
            resolve_native_live_player=lambda active, timeout: (
                "a",
                {"a_uri": "active"},
                {},
            ),
            same_queue_identity=lambda left, right: left == right,
            start_transition=lambda station, **kwargs: transition_calls.append(
                {"station": station, **kwargs}
            ) or True,
            wake_autodj_worker=lambda: None,
            script_interrupt_fade_seconds=lambda station: configured_fade,
        )
    )
    return service, state, transition_calls


def test_script_interrupt_uses_fade_without_manual_hard_switch() -> None:
    service, _state, calls = _service(remaining_ms=20000, configured_fade=5.0)
    result = service.direct_handoff(
        "station.db",
        reserved_queue_lines=["script", "tail"],
        reservation_id="script-1",
        script_interrupt=True,
    )

    assert result and result["success"]
    assert result["mode"] == "script_interrupt_db_head_direct_handoff"
    assert len(calls) == 1
    call = calls[0]
    assert call["fade"] == 5.0
    assert call["script_interrupt"] is True
    assert call["manual_next_fast"] is False
    assert call["reason"] == "script_interrupt_db_head_direct_handoff"


def test_script_interrupt_fade_is_clamped_to_remaining_audio() -> None:
    service, _state, calls = _service(remaining_ms=1200, configured_fade=5.0)
    result = service.direct_handoff(
        "station.db",
        reserved_queue_lines=["script", "tail"],
        reservation_id="script-short-tail",
        script_interrupt=True,
    )

    assert result and result["success"]
    assert len(calls) == 1
    assert abs(float(calls[0]["fade"]) - 1.2) < 0.001


def test_manual_next_keeps_existing_hard_switch_contract() -> None:
    service, _state, calls = _service(remaining_ms=20000, configured_fade=5.0)
    result = service.direct_handoff(
        "station.db",
        reserved_queue_lines=["script", "tail"],
        reservation_id="manual-1",
    )

    assert result and result["success"]
    assert result["mode"] == "manual_next_db_head_direct_handoff"
    assert len(calls) == 1
    call = calls[0]
    assert call["fade"] == 0.0
    assert call["script_interrupt"] is False
    assert call["manual_next_fast"] is True


def test_native_script_interrupt_has_delayed_full_gain_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    timing = (root / "native_engine" / "src" / "native_timing.c").read_text(encoding="utf-8")
    mixer = (root / "native_engine" / "src" / "icecast_output.c").read_text(encoding="utf-8")
    engine = (root / "native_engine" / "src" / "engine.c").read_text(encoding="utf-8")

    assert 'strcmp(command, "script_interrupt") == 0' in engine
    assert "entry_delay_ms = (int64_t)((double)fade_ms * 0.6736481777)" in timing
    assert "wb_icecast_output_prepare_delayed_entry(state, to_deck);" in timing
    assert "wb_icecast_output_activate_track(state, to_deck, &to_track);" in timing
    assert '"entry_ramp_ms\\":0,\\"entry_gain\\":1.0' in timing
    assert "if (elapsed_ms < 0.0) return 0.0;" in mixer
    assert "if (entry_ramp_ms <= 0) return 1.0;" in mixer


def test_script_path_requests_interrupt_mode_but_non_script_path_does_not() -> None:
    root = Path(__file__).resolve().parents[1]
    orchestration = (root / "player" / "orchestration.py").read_text(encoding="utf-8")
    app_source = (root / "app.py").read_text(encoding="utf-8")

    assert 'if source == "script":' in orchestration
    assert 'handoff_kwargs["script_interrupt"] = True' in orchestration
    assert "elif script_interrupt:" in app_source
    assert "_ab_script_interrupt_to(" in app_source
