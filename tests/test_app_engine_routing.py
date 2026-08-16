from __future__ import annotations
import ast
import contextlib
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

class AppEngineRoutingTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.app_path = Path(__file__).resolve().parents[1] / 'app.py'
        cls.source = cls.app_path.read_text(encoding='utf-8')
        cls.tree = ast.parse(cls.source)
        cls.functions = {node.name: node for node in cls.tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        cls.lifecycle_source = (cls.app_path.parent / 'audio_engine' / 'lifecycle.py').read_text(encoding='utf-8')
        cls.autodj_source = (cls.app_path.parent / 'autodj' / 'service.py').read_text(encoding='utf-8')
        cls.station_source = (cls.app_path.parent / 'station' / 'service.py').read_text(encoding='utf-8')

    def _function_source(self, name: str) -> str:
        node = self.functions[name]
        return ast.get_source_segment(self.source, node) or ''

    def test_public_ab_operations_route_through_audio_engine(self) -> None:
        self.assertIn('get_audio_engine().load_deck', self._function_source('_ab_push'))
        self.assertIn('get_audio_engine().select_deck', self._function_source('_ab_select'))
        self.assertIn('get_audio_engine().transition_to', self._function_source('_ab_transition_to'))

    def test_slot_identity_is_prepared_before_audio_engine_load(self) -> None:
        wrapper = self._function_source('_ab_push')
        self.assertIn('_ab_prepare_engine_load_uri', wrapper)
        self.assertIn('engine_uri', wrapper)
        helper = self._function_source('_ab_prepare_engine_load_uri')
        self.assertIn('wb_ab_slot_token', self.source)
        self.assertIn('hashlib.sha1', helper)

    def test_retired_python_deck_loaded_publisher_is_removed(self) -> None:
        self.assertNotIn('_publish_deck_loaded_from_uri', self.functions)
        lifecycle_source = (self.app_path.parent / 'audio_engine' / 'lifecycle.py').read_text(encoding='utf-8')
        self.assertIn('def accept_deck_loaded', lifecycle_source)
        self.assertIn('deck_loaded_dedupe_seconds', lifecycle_source)
        self.assertIn('signature == previous_signature', lifecycle_source)

    def test_retired_native_settings_routes_are_removed(self) -> None:
        self.assertNotIn('api_audio_engine_fault_test', self.functions)
        self.assertNotIn('api_audio_engine_native_icecast', self.functions)
        self.assertNotIn('/api/audio-engine/fault-test', self.source)
        self.assertNotIn('/api/audio-engine/native-icecast', self.source)
        self.assertNotIn('native_icecast_outputs', self.source)

    def test_successful_seek_is_mirrored_to_native_engine(self) -> None:
        seek_handler = self._function_source('_perform_native_seek_request')
        runtime_source = (self.app_path.parent / 'audio_engine' / 'runtime.py').read_text(encoding='utf-8')
        self.assertIn('_publish_audio_engine_track_seeked', seek_handler)
        self.assertNotIn('_publish_audio_engine_track_seeked', self.functions)
        self.assertIn('def publish_audio_engine_track_seeked', runtime_source)
        self.assertIn('sync_live_event', runtime_source)
        self.assertIn('"track_seeked"', runtime_source)
        self.assertIn('"seek_position_ms"', runtime_source)
        self.assertIn('"transition_at_ms"', runtime_source)
        self.assertIn('"effective_end_ms"', runtime_source)
        self.assertIn('"source_end_ms"', runtime_source)
        self.assertIn('"flush_native_pcm"', runtime_source)
        self.assertNotIn('_try_direct_seek_abs_request', self.functions)
        self.assertNotIn('_perform_seek_restart_request', self.functions)

    def test_native_seek_preserves_inactive_deck_and_freezes_transition_clock(self) -> None:
        native_seek = self._function_source('_perform_native_seek_request')
        lifecycle_source = self.lifecycle_source
        monitor = self._function_source('_ab_monitor_loop')
        applied = self._function_source('_ab_mark_native_seek_applied')
        cueout = self._function_source('_ab_start_cueout_transition_now')
        replan = self._function_source('_ab_replan_after_queue_mutation')
        self.assertIn('"started_at"', native_seek)
        self.assertIn('now - segment_elapsed', native_seek)
        self.assertIn('"seek_pending"', native_seek)
        self.assertIn('"seek_pending_deadline"', native_seek)
        self.assertIn('"transition_not_before"', native_seek)
        self.assertIn('native_audio_probe_seek_applied', lifecycle_source)
        self.assertIn('_mark_seek_applied', lifecycle_source)
        self.assertIn('if bool(st.get("seek_pending"))', monitor)
        self.assertLess(monitor.index('if bool(st.get("seek_pending"))'), monitor.index('_ab_transition_source_position_seconds'))
        self.assertIn('"started_at"] = applied_at - segment_elapsed', applied)
        self.assertIn('"transition_not_before"] = applied_at + 0.02', applied)
        self.assertIn('"last_source_elapsed"', native_seek)
        self.assertIn('"native_seek"', native_seek)
        seek_guard = 'if bool(st0.get("seek_pending")) and not bool(manual_next_fast):'
        self.assertIn(seek_guard, cueout)
        self.assertLess(cueout.index(seek_guard), cueout.index('_ab_select(target)'))
        replan_seek_guard = 'if bool(handoff_snapshot.get("seek_pending")):'
        self.assertIn(replan_seek_guard, replan)
        self.assertLess(replan.index(replan_seek_guard), replan.index('_ab_bootstrap_from_queue_plan'))
        self.assertIn('_ab_schedule_deferred_replan(reason, delay)', replan)
        self.assertNotIn('load_deck', native_seek)
        self.assertNotIn('clear_slot', native_seek)

    def test_native_seek_applied_reanchors_exact_token_scoped_clock(self) -> None:
        nodes = [self.functions['_ab_seek_event_identity_matches'], self.functions['_ab_mark_native_seek_pending'], self.functions['_ab_mark_native_seek_applied']]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        state = {'enabled': True, 'station_key': 'db-AirFM.db', 'active': 'a', 'lines': ['current'], 'player_index': {'a': 0}, 'current_index': 0, 'seek_pending': False}
        stores = {'db-AirFM.db': {}}
        progress = {'db-AirFM.db': {}}

        @contextlib.contextmanager
        def station_runtime_context(_station_key):
            yield
        namespace = {'station_runtime_context': station_runtime_context, '_AB_PLAYER_LOCK': threading.RLock(), '_AB_PLAYER_STATE': state, '_ab_line_info': lambda _line: {'queue_id': 700, 'cue_in': 0.39, 'audio_start': 0.39}, '_ab_monitor_wake_key': lambda value='': Path(str(value or '__default__')).name, 'NOW_PLAYING_LOCK': threading.RLock(), 'PROGRESS_LOCK': threading.RLock(), '_get_now_playing_store': lambda station: stores.setdefault(station, {}), '_get_progress_state': lambda station: progress.setdefault(station, {}), 'time': time}
        exec(compile(module, str(self.app_path), 'exec'), namespace)
        pending_event = SimpleNamespace(station_key='db-AirFM.db', event='track_seeked', deck='A', queue_id=700, slot_token='seek-token', wall_time_unix_ms=1000000, payload={'seek_position_ms': 206000})
        self.assertTrue(namespace['_ab_mark_native_seek_pending'](pending_event))
        self.assertTrue(state['seek_pending'])
        self.assertEqual(state['seek_pending_queue_id'], 700)
        self.assertEqual(state['seek_pending_slot_token'], 'seek-token')
        applied_event = SimpleNamespace(station_key='db-AirFM.db', event='native_audio_probe_seek_applied', deck='A', queue_id=700, slot_token='seek-token', wall_time_unix_ms=1000829, payload={'source_position_ms': 206092, 'startup_delay_ms': 829})
        self.assertTrue(namespace['_ab_mark_native_seek_applied'](applied_event))
        self.assertFalse(state['seek_pending'])
        applied_at = 1000.829
        self.assertAlmostEqual(state['started_at'], applied_at - (206.092 - 0.39), places=6)
        self.assertAlmostEqual(state['transition_not_before'], applied_at + 0.02, places=6)
        self.assertAlmostEqual(progress['db-AirFM.db']['last_source_elapsed'], 206.092, places=6)

    def test_encoder_streams_are_the_only_runtime_output_source(self) -> None:
        loader = self._function_source('_load_native_output_runtime_configs')
        self.assertIn('FROM icecast_streams', loader)
        self.assertIn('autostart', loader)
        self.assertIn('_native_stream_config_from_row', loader)
        stream_config = self._function_source('_native_stream_config_from_row')
        self.assertIn('"stream_description": str(value("station_description"', stream_config)
        self.assertIn('"stream_genre": str(value("genre"', stream_config)
        self.assertIn('"stream_url": str(value("website_url"', stream_config)
        self.assertIn('seen_endpoints', loader)
        self.assertNotIn('_load_native_output_configs', loader)
        self.assertNotIn('native_icecast_outputs', self.source)
        start_pos = self.station_source.index('def start(')
        start = self.station_source[start_pos:]
        self.assertLess(start.index('clear_icecast_output(""'), start.index('load_output_configs'))
        runtime = self._function_source('prepare_native_soundsolution_runtime')
        self.assertIn('get_bundled_soundsolution_library_path', runtime)
        self.assertIn('get_soundsolution_config_path', runtime)
        self.assertNotIn('"dsp_executable_path"', runtime)
        self.assertIn('"dsp_config_path"', runtime)
        self.assertNotIn('"dsp_log_path"', runtime)
        self.assertIn('SoundSolution .dat configuration is not readable', runtime)
        self.assertNotIn('wineprefix', runtime.lower())
        self.assertNotIn('appimage_runtime', runtime.lower())
        self.assertNotIn('mkdir', runtime)

    def test_encoder_status_and_mutations_use_stable_native_output_identity(self) -> None:
        status = self._function_source('api_encoders')
        configure = self._function_source('api_encoder_configure')
        delete = self._function_source('api_encoder_delete')
        helper = self._function_source('_native_encoder_outputs_by_id')
        snapshot = self._function_source('_native_encoder_runtime_snapshot')
        self.assertIn('get_icecast_output_state', snapshot)
        self.assertIn('_native_encoder_runtime_snapshot', helper)
        self.assertIn('output_id.startswith("stream_")', snapshot)
        self.assertIn('_native_stream_output_id', status)
        self.assertNotIn('icecast_mount_reachable', status)
        self.assertIn('_native_encoder_output_is_enabled', configure)
        self.assertNotIn('icecast_mount_reachable', configure)
        self.assertIn('clear_icecast_output', delete)
        self.assertIn('_native_stream_output_id(stream_id)', delete)
        self.assertNotIn('icecast_mount_reachable', delete)

    def test_encoder_runtime_requires_actual_native_streaming(self) -> None:
        status = self._function_source('api_encoders')
        helper = self._function_source('_native_encoder_runtime_snapshot')
        self.assertIn('state.get("engine_running")', helper)
        self.assertIn('runtime_status == "streaming"', status)
        self.assertIn('station_running', status)
        self.assertIn('is_connected', status)
        self.assertNotIn('is_running = bool(runtime.get("enabled"))', status)

    def test_encoder_browser_counter_uses_authoritative_stream_state(self) -> None:
        js = (self.app_path.parent / 'html' / 'static' / 'broadcaster.js').read_text(encoding='utf-8')
        self.assertIn('function isEncoderRuntimeStreaming(stream)', js)
        self.assertIn("runtimeStatus === 'streaming'", js)
        self.assertIn('stream.station_running', js)
        self.assertIn('stream.connected', js)
        self.assertIn('Encoder starting', js)

    def test_native_status_progress_does_not_double_add_cue_in(self) -> None:
        import threading
        namespace = {'normalize_media_path': lambda value: str(value or ''), '_AB_PLAYER_LOCK': threading.RLock(), '_AB_PLAYER_STATE': {'lines': ['current-line'], 'player_index': {'a': 0}, 'current_index': 0}, '_ab_find_line_index_by_identity': lambda lines, **identity: 0, '_ab_line_info': lambda line: {'file': '/tmp/song.mp3', 'title': 'Title', 'artist': 'Artist', 'album': 'Album', 'year': '2001', 'queue_id': 42, 'track_id': 7, 'orig_total': 202.272, 'cue_in': 2.0}, 'NOW_PLAYING_LOCK': threading.RLock(), '_get_now_playing_store': lambda station: {}, 'get_autodj_notice': lambda station: None, '_normalize_year_metadata': lambda value: str(value or ''), 'read_media_metadata': lambda path: {}, 'guess_metadata_from_filename': lambda path: {}, 'format_seconds': lambda value: str(value)}
        exec(self._function_source('_native_status_line_for_state'), namespace)
        exec(self._function_source('_native_api_status_payload'), namespace)
        payload = namespace['_native_api_status_payload']('db-Test.db', {'running': True, 'active_deck': 'A', 'queue_id': 42, 'native_audio_probe_queue_id': 42, 'native_audio_probe_path': '/tmp/song.mp3', 'native_audio_probe_position_ms': 53522, 'native_audio_probe_source_end_ms': 202272}, with_progress=True)
        self.assertAlmostEqual(payload['song']['elapsed'], 53.522, places=3)
        self.assertAlmostEqual(payload['song']['duration'], 202.272, places=3)

    def test_autodj_lifetime_is_native_and_station_scoped(self) -> None:
        loop = self._function_source('autodj_loop')
        starter = self._function_source('start_autodj_thread')
        self.assertIn('_get_autodj_service().run_loop(station_key)', loop)
        self.assertIn('_get_autodj_service().start_thread(station_key)', starter)
        self.assertIn('native_station_state(station)', self.autodj_source)
        self.assertIn('get("running")', self.autodj_source)
        self.assertIn('self._threads', self.autodj_source)
        self.assertIn('args=(station,)', self.autodj_source)
        self.assertNotIn('AUTODJ_THREADS', self.source)

    def test_native_track_started_owns_control_lifecycle(self) -> None:
        callback = self._function_source('_native_engine_event_callback')
        lifecycle_source = self.lifecycle_source
        lifecycle = self._function_source('_process_native_track_started_event')
        rebuild = self._function_source('_native_rebuild_plan_after_track_started')
        ensure = self._function_source('_ensure_native_event_lifecycle')
        self.assertIn('_get_native_lifecycle_coordinator().handle_event(event)', callback)
        self.assertIn('_track_queue.put_nowait', lifecycle_source)
        self.assertIn('native_select_command', lifecycle_source)
        self.assertIn('native_transition_command', lifecycle_source)
        self.assertIn('time.sleep(0.10)', lifecycle_source)
        self.assertIn('_ab_apply_now_playing_line', lifecycle)
        self.assertIn('_ab_commit_started_track', lifecycle)
        self.assertIn('autodj_fill_queue_once(replan_after_fill=False)', lifecycle)
        self.assertIn('_publish_ui_queue_history_changed', lifecycle)
        self.assertIn('_ab_schedule_inactive_preload_after_start', rebuild)
        self.assertIn('coordinator.start', ensure)
        self.assertIn('subscribe_events', lifecycle_source)

    def test_native_command_paths_do_not_directly_commit_started_tracks(self) -> None:
        bootstrap = self._function_source('_ab_bootstrap_from_queue_plan')
        cueout = self._function_source('_ab_start_cueout_transition_now')
        monitor = self._function_source('_ab_monitor_loop')
        self.assertNotIn('_ab_commit_started_track', bootstrap)
        self.assertNotIn('_ab_commit_started_track', cueout)
        self.assertNotIn('_ab_commit_started_track', monitor)

    def test_transition_completion_is_token_scoped_and_not_select_based(self) -> None:
        monitor = self._function_source('_ab_monitor_loop')
        completion = self._function_source('_native_sync_transition_completion')
        self.assertIn('_native_sync_transition_completion', monitor)
        self.assertNotIn('select_result = _ab_select', monitor)
        self.assertIn('"transition_finished"', completion)
        self.assertIn('"track_ended"', completion)
        self.assertGreaterEqual(completion.count('sync_live_event('), 2)
        cueout = self._function_source('_ab_start_cueout_transition_now')
        self.assertIn('transition_from_line', monitor)
        self.assertIn('transition_from_line', cueout)
        self.assertIn('transition_to_line', cueout)
        self.assertIn('already_loaded', monitor)

    def test_python_pcm_analyzer_and_cache_are_removed(self) -> None:
        retired = ('_ab_check_soundfile_backend', '_ab_analyze_cue_points_soundfile', '_ab_analyze_audio_boundaries_soundfile', '_ab_analyze_and_store_soundfile_cues', '_ab_schedule_audio_boundary_analysis', '_ab_request_autocue_probe', '_ab_wait_for_autocue', '_ab_apply_runtime_autocue_to_uri', '_ab_store_track_analysis_cache', '_ab_load_valid_track_analysis_cache')
        for name in retired:
            self.assertNotIn(f'def {name}(', self.source)
        for marker in ('import soundfile', 'import numpy', '_AB_AUTOCUE_CACHE', 'CUE_ANALYSIS_QUEUE', '/api/cue-analysis/status'):
            self.assertNotIn(marker, self.source)

    def test_db_queue_plan_delegates_automatic_boundaries_to_native_analysis(self) -> None:
        helper = self._function_source('_ab_native_runtime_timing_metadata')
        self.assertIn('wb_native_analyze="1"', helper)
        self.assertIn('wb_cue_source="native_runtime_pending"', helper)
        self.assertIn('wb_manual_timing="1"', helper)
        for name in ('_build_station_queue_plan',):
            source = self._function_source(name)
            self.assertIn('_ab_native_runtime_timing_metadata', source)
            self.assertNotIn('_ab_guard_extreme_early_analysis_end', source)
            self.assertNotIn('_ab_analyze_cue_points_soundfile', source)

    def test_native_runtime_descriptor_ignores_automatic_db_cues_but_keeps_manual_overrides(self) -> None:
        namespace = {'get_track_total_duration_for_station_path': lambda station_key, path: 120.0, '_clean_transition_audio_bounds': lambda path, start, end, total: (start, end, total, 'manual'), '_normalize_seek_window': lambda total, cue_in, cue_out: (cue_in, cue_out, total, cue_in)}
        exec(self._function_source('_ab_native_runtime_timing_metadata'), namespace)
        settings = {'crossfade_fallback_seconds': 3.0, 'crossfade_fade_out_seconds': 5.0, 'crossfade_min_seconds': 0.1, 'crossfade_max_seconds': 6.0, 'no_crossfade_max_duration_sec': 65.0, 'gap_killer_start_dbfs': -20.0, 'gap_killer_end_dbfs': -24.0, 'crossfade_trigger_relative_db': -7.0}
        automatic = {'clean_transition': 0, 'script_clean_transition': 0, 'cue_in_seconds': 11.0, 'cue_out_seconds': 22.0, 'audio_start_seconds': 10.0, 'audio_end_seconds': 23.0}
        metadata = namespace['_ab_native_runtime_timing_metadata']('/music/auto.mp3', automatic, station_key='station.db', sam_settings=settings, escape=lambda value: value)
        self.assertIn('wb_native_analyze="1"', metadata)
        self.assertIn('wb_audio_start="0.000"', metadata)
        self.assertIn('wb_audio_end="120.000"', metadata)
        self.assertNotIn('wb_audio_start="10.000"', metadata)
        self.assertNotIn('wb_audio_end="23.000"', metadata)
        manual = dict(automatic, clean_transition=1)
        metadata = namespace['_ab_native_runtime_timing_metadata']('/music/manual.mp3', manual, station_key='station.db', sam_settings=settings, escape=lambda value: value)
        self.assertIn('wb_native_analyze="0"', metadata)
        self.assertIn('wb_manual_timing="1"', metadata)
        self.assertIn('wb_audio_start="10.000"', metadata)
        self.assertIn('wb_audio_end="23.000"', metadata)

    def test_native_transition_clock_honors_audio_start_and_audio_end(self) -> None:
        namespace: dict = {}
        exec(self._function_source('_ab_transition_point_seconds'), namespace)
        exec(self._function_source('_ab_transition_source_position_seconds'), namespace)
        info = {'queue_id': 15173, 'slot_token': 'id4-token', 'cue_in': 6.1, 'cue_out': 11.29, 'audio_start': 6.1, 'audio_end': 11.29, 'orig_total': 17.616}
        point = namespace['_ab_transition_point_seconds'](info, soft_no_crossfade=True, fallback_duration=5.19)
        self.assertAlmostEqual(point, 11.29, places=3)
        native_position, source = namespace['_ab_transition_source_position_seconds'](info, elapsed_segment_seconds=0.0, native_state={'running': True, 'active_deck': 'A', 'native_audio_probe_queue_id': 15173, 'native_audio_probe_slot_token': 'id4-token', 'native_audio_probe_position_ms': 11290}, active_player='a')
        self.assertAlmostEqual(native_position, 11.29, places=3)
        self.assertEqual(source, 'native_audio_probe_position_ms')
        fallback_position, fallback_source = namespace['_ab_transition_source_position_seconds'](info, elapsed_segment_seconds=5.19, native_state={}, active_player='a')
        self.assertAlmostEqual(fallback_position, 11.29, places=3)
        self.assertEqual(fallback_source, 'segment_wallclock_plus_play_start')

    def test_terminal_native_eof_is_token_scoped_and_forces_hard_handoff(self) -> None:
        helper_node = self.functions['_ab_native_terminal_eof_matches_active']
        module = ast.Module(body=[helper_node], type_ignores=[])
        ast.fix_missing_locations(module)
        namespace: dict = {}
        exec(compile(module, str(self.app_path), 'exec'), namespace)
        helper = namespace['_ab_native_terminal_eof_matches_active']
        active_info = {'queue_id': 15293, 'slot_token': 'corona-token'}
        eof_state = {'active_deck': 'B', 'native_audio_probe_deck': 'B', 'native_audio_probe_status': 'eof', 'native_audio_probe_eof': True, 'native_audio_probe_queue_id': 15293, 'native_audio_probe_slot_token': 'corona-token'}
        matched, reason = helper(eof_state, active_info, 'b')
        self.assertTrue(matched)
        self.assertEqual(reason, 'eof')
        stale_token = dict(eof_state, native_audio_probe_slot_token='old-token')
        self.assertEqual(helper(stale_token, active_info, 'b'), (False, 'token_mismatch'))
        wrong_deck = dict(eof_state, active_deck='A')
        self.assertEqual(helper(wrong_deck, active_info, 'b'), (False, 'deck_mismatch'))
        decoding = dict(eof_state, native_audio_probe_status='decoding', native_audio_probe_eof=False)
        self.assertEqual(helper(decoding, active_info, 'b'), (False, 'decoding'))
        monitor = self._function_source('_ab_monitor_loop')
        handoff = self._function_source('_ab_start_cueout_transition_now')
        self.assertIn('terminal_eof_due', monitor)
        self.assertIn('hard_handoff=True', monitor)
        self.assertIn('hard_select = bool(manual_next_fast or hard_handoff or no_crossfade_handoff)', handoff)
        self.assertIn('_ab_select(target)', handoff)

    def test_monitor_compares_absolute_native_position_to_transition_point(self) -> None:
        monitor = self._function_source('_ab_monitor_loop')
        rebuild = self._function_source('_native_rebuild_plan_after_track_started')
        self.assertIn('_ab_transition_point_seconds', monitor)
        self.assertIn('_ab_transition_source_position_seconds', monitor)
        self.assertIn('transition_due', monitor)
        self.assertIn('native_audio_probe_position_ms', self._function_source('_ab_transition_source_position_seconds'))
        self.assertNotIn('elapsed >= transition_at', monitor)
        self.assertIn('started_wallclock_anchor', rebuild)

    def test_short_item_handoff_is_zero_fade_direct_select_at_audio_end(self) -> None:
        monitor = self._function_source('_ab_monitor_loop')
        handoff = self._function_source('_ab_start_cueout_transition_now')
        self.assertIn('soft_no_crossfade_for_timing', monitor)
        self.assertIn('reason="short_no_crossfade_audio_end"', monitor)
        self.assertIn('no_crossfade_handoff=True', monitor)
        self.assertIn('fade = 0.0', monitor)
        self.assertNotIn('available_tail', monitor)
        self.assertIn('no_crossfade_handoff', handoff)
        self.assertIn('_ab_select(target)', handoff)

    def test_no_crossfade_monitor_arms_just_before_audio_end(self) -> None:
        namespace: dict = {}
        exec(self._function_source('_ab_no_crossfade_monitor_sleep_seconds'), namespace)
        helper = namespace['_ab_no_crossfade_monitor_sleep_seconds']
        self.assertAlmostEqual(helper(100.0, 102.0), 0.1, places=3)
        self.assertAlmostEqual(helper(101.8, 102.0), 0.196, places=3)
        self.assertEqual(helper(101.997, 102.0), 0.0)

    def test_native_early_eof_interrupts_precise_no_crossfade_wait(self) -> None:
        lifecycle_source = self.lifecycle_source
        monitor = self._function_source('_ab_monitor_loop')
        self.assertIn('"native_active_early_eof_handled"', lifecycle_source)
        self.assertIn('"native_audio_probe_early_eof"', lifecycle_source)
        self.assertIn('_signal_monitor_wake', lifecycle_source)
        self.assertIn('_ab_wait_monitor_interruptible', monitor)
        self.assertNotIn('time.sleep(max(0.001, float(monitor_sleep_seconds or 0.10)))', monitor)
        nodes = [self.functions['_ab_monitor_wake_key'], self.functions['_ab_monitor_wake_snapshot'], self.functions['_ab_signal_monitor_wake'], self.functions['_ab_wait_monitor_interruptible']]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {'os': __import__('os'), 'threading': threading, '_ab_runtime_station_key': lambda: '__default__', '_AB_MONITOR_WAKE_CONDITION': threading.Condition(threading.RLock()), '_AB_MONITOR_WAKE_SERIALS': {}, '_AB_MONITOR_WAKE_REASONS': {}}
        exec(compile(module, str(self.app_path), 'exec'), namespace)
        snapshot = namespace['_ab_monitor_wake_snapshot']('db-AirFM.db')
        result: list[tuple[int, bool, str, float]] = []

        def waiter() -> None:
            started = time.monotonic()
            serial, woke, reason = namespace['_ab_wait_monitor_interruptible']('db-AirFM.db', snapshot, 1.0)
            result.append((serial, woke, reason, time.monotonic() - started))
        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.02)
        namespace['_ab_signal_monitor_wake']('db-AirFM.db', reason='native_active_early_eof_handled')
        thread.join(timeout=0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0][1])
        self.assertEqual(result[0][2], 'native_active_early_eof_handled')
        self.assertLess(result[0][3], 0.25)

    def test_native_hard_handoff_ownership_is_immutable_and_blocks_python_fallback(self) -> None:
        monitor = self._function_source('_ab_monitor_loop')
        validator = self._function_source('_ab_native_hard_handoff_state_valid')
        arm = self._function_source('_ab_arm_native_hard_handoff')
        transition = self._function_source('_ab_start_cueout_transition_now')
        replan = self._function_source('_ab_replan_after_queue_mutation')
        lifecycle_source = self.lifecycle_source
        self.assertIn('_ab_native_hard_handoff_state_valid', monitor)
        self.assertNotIn('int(st.get("hard_handoff_generation") or 0) == generation', monitor)
        self.assertNotIn('int(st.get("hard_handoff_current_index") or -1) == current_index', monitor)
        self.assertIn('hard_handoff_from_queue_id', validator)
        self.assertIn('hard_handoff_from_slot_token', validator)
        self.assertIn('hard_handoff_to_queue_id', validator)
        self.assertIn('hard_handoff_to_slot_token', validator)
        self.assertIn('hard_handoff_native_claimed', validator)
        self.assertIn('hard_handoff_completed', validator)
        self.assertIn('already_armed', arm)
        self.assertIn('scheduled_delay', arm)
        hard_handoff_guard = 'if bool(st0.get("hard_handoff_armed")) and not bool(manual_next_fast):'
        self.assertIn(hard_handoff_guard, transition)
        self.assertLess(transition.index(hard_handoff_guard), transition.index('_ab_select(target)'))
        replan_handoff_guard = 'if bool(handoff_snapshot.get("hard_handoff_armed")):'
        self.assertIn(replan_handoff_guard, replan)
        self.assertLess(replan.index(replan_handoff_guard), replan.index('_ab_bootstrap_from_queue_plan'))
        self.assertIn('_mark_hard_handoff_claimed', lifecycle_source)
        self.assertIn('_mark_hard_handoff_completed', lifecycle_source)
        self.assertIn('native_hard_handoff_boundary', lifecycle_source)

    def test_native_hard_handoff_identity_survives_generation_and_index_drift(self) -> None:
        nodes = [self.functions['_ab_native_deck_identity'], self.functions['_ab_hard_handoff_identity_matches'], self.functions['_ab_native_hard_handoff_state_valid']]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        namespace = {'_ab_monitor_wake_key': lambda value='': Path(str(value or '__default__')).name}
        exec(compile(module, str(self.app_path), 'exec'), namespace)
        validator = namespace['_ab_native_hard_handoff_state_valid']
        snapshot = {'hard_handoff_armed': True, 'hard_handoff_station_key': 'db-AirFM.db', 'hard_handoff_active': 'a', 'hard_handoff_target': 'b', 'hard_handoff_generation': 11, 'hard_handoff_current_index': 7, 'hard_handoff_target_index': 8, 'hard_handoff_from_queue_id': 501, 'hard_handoff_from_slot_token': 'out-token', 'hard_handoff_to_queue_id': 502, 'hard_handoff_to_slot_token': 'id4-token'}
        native_state = {'active_deck': 'A', 'native_audio_deck_a_queue_id': 501, 'native_audio_deck_a_slot_token': 'out-token', 'native_audio_deck_b_queue_id': 502, 'native_audio_deck_b_slot_token': 'id4-token'}
        self.assertTrue(validator(snapshot, native_state, station_key='/srv/db-AirFM.db', active='a', target='b', current_info={'queue_id': 501}, target_info={'queue_id': 502}))
        drifted = dict(snapshot, hard_handoff_generation=99, hard_handoff_current_index=0, hard_handoff_target_index=1)
        self.assertTrue(validator(drifted, native_state, station_key='db-AirFM.db', active='a', target='b', current_info={'queue_id': 501}, target_info={'queue_id': 502}))
        stale_target = dict(native_state, native_audio_deck_b_slot_token='stale')
        self.assertFalse(validator(drifted, stale_target, station_key='db-AirFM.db', active='a', target='b', current_info={'queue_id': 501}, target_info={'queue_id': 502}))
        claimed = dict(drifted, hard_handoff_native_claimed=True)
        self.assertTrue(validator(claimed, {}, station_key='db-AirFM.db', active='a', target='b', current_info={}, target_info={}))

    def test_native_hard_handoff_events_latch_claim_and_boundary_before_worker(self) -> None:
        nodes = [self.functions['_ab_hard_handoff_identity_matches'], self.functions['_ab_mark_native_hard_handoff_claimed'], self.functions['_ab_mark_native_hard_handoff_completed']]
        module = ast.Module(body=nodes, type_ignores=[])
        ast.fix_missing_locations(module)
        state = {'hard_handoff_armed': True, 'hard_handoff_active': 'a', 'hard_handoff_target': 'b', 'hard_handoff_from_queue_id': 601, 'hard_handoff_from_slot_token': 'from-token', 'hard_handoff_to_queue_id': 602, 'hard_handoff_to_slot_token': 'to-token', 'hard_handoff_deadline': 0.0}

        @contextlib.contextmanager
        def station_runtime_context(_station_key):
            yield
        namespace = {'station_runtime_context': station_runtime_context, '_AB_PLAYER_LOCK': threading.RLock(), '_AB_PLAYER_STATE': state, 'time': time}
        exec(compile(module, str(self.app_path), 'exec'), namespace)
        claimed_event = SimpleNamespace(station_key='db-AirFM.db', event='native_active_early_eof_handled', deck='A', queue_id=601, slot_token='from-token', payload={'hard_handoff_claimed': True})
        self.assertTrue(namespace['_ab_mark_native_hard_handoff_claimed'](claimed_event))
        self.assertTrue(state['hard_handoff_native_claimed'])
        boundary_event = SimpleNamespace(station_key='db-AirFM.db', event='track_started', deck='B', queue_id=602, slot_token='to-token', payload={'source': 'native_hard_handoff_boundary'})
        self.assertTrue(namespace['_ab_mark_native_hard_handoff_completed'](boundary_event))
        self.assertTrue(state['hard_handoff_completed'])
        self.assertEqual(state['hard_handoff_completion_source'], 'native_hard_handoff_boundary')
        self.assertGreater(state['hard_handoff_deadline'], time.time())

    def test_hard_handoff_reuses_exact_prebuffered_target_across_generation_change(self) -> None:
        handoff = self._function_source('_ab_start_cueout_transition_now')
        hard_block = handoff[handoff.index('if hard_select:'):handoff.index('if need_push:')]
        self.assertIn('loaded_key_now == expected_key_for_target', hard_block)
        self.assertNotIn('loaded_generation ==', hard_block)

    def test_initial_native_select_waits_for_real_pcm_prebuffer(self) -> None:
        bootstrap = self._function_source('_ab_bootstrap_from_queue_plan')
        wait_helper = self._function_source('_ab_wait_for_native_deck_prebuffer')
        self.assertIn('_ab_wait_for_native_deck_prebuffer', bootstrap)
        self.assertLess(bootstrap.index('_ab_wait_for_native_deck_prebuffer'), bootstrap.index('_ab_select("a"'))
        self.assertIn('native_audio_deck_{player}_', wait_helper)
        self.assertIn('prebuffer_ready', wait_helper)
        self.assertIn('ring_buffer_bytes', wait_helper)
        self.assertIn('ring_bytes > 0', wait_helper)
        self.assertIn('queue_identity_mismatch', wait_helper)
        self.assertIn('engine_stopped', wait_helper)

    def test_stop_cancels_deferred_refill_and_offair_replan_cannot_bootstrap(self) -> None:
        stop_prepare = self._function_source('_station_prepare_stop_state')
        replan = self._function_source('_ab_replan_after_queue_mutation')
        async_replan = self._function_source('_ab_schedule_async_replan')
        delayed_preload = self._function_source('_ab_schedule_inactive_preload_after_start')
        lifecycle = self._function_source('_process_native_track_started_event')
        stop_pos = self.station_source.index('def stop(')
        stop_service = self.station_source[stop_pos:]
        self.assertIn('_ab_invalidate_pending_replans(reason="native_stop_requested")', stop_prepare)
        self.assertIn('_AB_PLAYER_STATE["enabled"] = False', stop_prepare)
        self.assertLess(stop_service.index('prepare_stop_state'), stop_service.index('engine.stop'))
        offair_guard = 'if not bool(native_state.get("running")):'
        self.assertIn(offair_guard, replan)
        self.assertLess(replan.index(offair_guard), replan.index('_ab_bootstrap_from_queue_plan'))
        self.assertIn('if not bool(_native_station_state(scheduled_station).get("running")):', async_replan)
        self.assertIn('if not player_enabled or not native_running:', delayed_preload)
        self.assertIn('if not player_enabled:', lifecycle)
        self.assertIn('if not bool(state.get("running")):', lifecycle)
        self.assertLess(lifecycle.index('if not player_enabled:'), lifecycle.index('_native_rebuild_plan_after_track_started'))


    def test_main_starts_internal_audio_runtime_before_flask(self) -> None:
        self.assertIn('ensure_ready = getattr(engine, "ensure_ready", None)', self.source)
        self.assertNotIn('Internal audio runtime is ready', self.source)
        self.assertIn('raise SystemExit(2) from exc', self.source)

    def test_app_keeps_sigchld_waitable_for_managed_native_children(self) -> None:
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
        self.assertIn('signal.signal(signal.SIGCHLD, signal.SIG_DFL)', source)
        self.assertNotIn('signal.signal(signal.SIGCHLD, signal.SIG_IGN)', source)
if __name__ == '__main__':
    unittest.main()
