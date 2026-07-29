#ifndef WB_ENGINE_H
#define WB_ENGINE_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <pthread.h>
#include <sys/types.h>

#define WB_SLOT_TOKEN_SIZE 192
#define WB_STATION_KEY_SIZE 192
#define WB_PATH_SIZE 4096
#define WB_EVENT_NAME_SIZE 128
#define WB_SESSION_ID_SIZE 192
#define WB_APP_VERSION_SIZE 64
#define WB_AUDIO_STATUS_SIZE 64
#define WB_AUDIO_ERROR_SIZE 512
#define WB_FAULT_MODE_SIZE 64
#define WB_FAULT_REASON_SIZE 128
#define WB_AUDIO_ALIAS_CAPACITY 32
#define WB_NATIVE_DAEMON_VERSION "6024"
#define WB_AUDIO_SAMPLE_RATE 44100
#define WB_AUDIO_CHANNELS 2
#define WB_AUDIO_BYTES_PER_SAMPLE 2
#define WB_AUDIO_FRAME_BYTES (WB_AUDIO_CHANNELS * WB_AUDIO_BYTES_PER_SAMPLE)
#define WB_ICECAST_HOST_SIZE 256
#define WB_ICECAST_MOUNT_SIZE 512
#define WB_ICECAST_USER_SIZE 128
#define WB_ICECAST_PASSWORD_SIZE 256
#define WB_ICECAST_NAME_SIZE 256
#define WB_ICECAST_DESCRIPTION_SIZE 512
#define WB_ICECAST_GENRE_SIZE 256
#define WB_ICECAST_URL_SIZE 512
#define WB_ICECAST_STATUS_SIZE 64
#define WB_ICECAST_ERROR_SIZE 512
#define WB_ICECAST_METADATA_SIZE 512
#define WB_NATIVE_OUTPUT_MAX 8
#define WB_NATIVE_OUTPUT_ID_SIZE 64
#define WB_NATIVE_OUTPUT_CODEC_SIZE 32
#define WB_NATIVE_OUTPUT_CONTENT_TYPE_SIZE 64
#define WB_DSP_STATUS_SIZE 64
#define WB_DSP_ERROR_SIZE 512
#define WB_TRACK_ARTIST_SIZE 256
#define WB_TRACK_TITLE_SIZE 256
#define WB_TRACK_YEAR_SIZE 16
#define WB_DIAGNOSTIC_REASON_SIZE 128
#define WB_FFMPEG_SOURCE_SIZE 64
#define WB_FFMPEG_VERSION_SIZE 128
#define WB_FFMPEG_BUILD_SIZE 256
#define WB_FFMPEG_ERROR_SIZE 512
#define WB_ANALYSIS_SOURCE_SIZE 64
#define WB_ANALYSIS_ERROR_SIZE 256
#define WB_BUNDLED_FFMPEG_RUNTIME_ID "7.1.5-for-web-broadcaster-r13"

typedef struct {
    bool loaded;
    int64_t queue_id;
    int64_t track_id;
    int64_t cue_in_ms;
    int64_t cue_out_ms;
    int64_t audio_start_ms;
    int64_t play_start_ms;
    int64_t transition_at_ms;
    int64_t effective_end_ms;
    int64_t source_end_ms;
    int64_t fade_in_ms;
    int64_t fade_out_ms;
    bool analysis_requested;
    bool analysis_ready;
    bool analysis_failed;
    bool manual_timing;
    bool hard_clean;
    bool short_no_crossfade;
    bool stream_source;
    bool stream_infinite;
    int64_t stream_duration_ms;
    bool playback_started;
    bool consumed;
    bool terminal;
    int64_t analysis_window_ms;
    int64_t analysis_sustain_ms;
    int64_t analysis_artifact_max_ms;
    int64_t analysis_artifact_silence_ms;
    int64_t no_crossfade_max_duration_ms;
    int64_t crossfade_fallback_ms;
    int64_t crossfade_min_ms;
    int64_t crossfade_max_ms;
    double gap_start_threshold_dbfs;
    double gap_end_threshold_dbfs;
    double crossfade_trigger_relative_db;
    double analysis_reference_dbfs;
    double analysis_trigger_dbfs;
    double analysis_tail_peak_dbfs;
    double analysis_tail_rms_dbfs;
    int64_t analysis_ignored_artifact_ms;
    int64_t analysis_trailing_silence_ms;
    char analysis_source[WB_ANALYSIS_SOURCE_SIZE];
    char analysis_error[WB_ANALYSIS_ERROR_SIZE];
    char slot_token[WB_SLOT_TOKEN_SIZE];
    char station_key[WB_STATION_KEY_SIZE];
    char path[WB_PATH_SIZE];
    char artist[WB_TRACK_ARTIST_SIZE];
    char title[WB_TRACK_TITLE_SIZE];
    char year[WB_TRACK_YEAR_SIZE];
} WbDeckState;

typedef struct WbEngineState WbEngineState;

typedef int (*WbEngineEventSink)(
    void *context,
    WbEngineState *state,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
);

typedef struct {
    WbEngineState *owner;
    pthread_t thread;
    bool thread_created;
    bool worker_shutdown;
    size_t slot_index;
    bool configured;
    bool enabled;
    bool public_stream;
    bool add_year_to_metadata;
    bool connected;
    bool encoder_ready;
    bool metadata_pending;
    int port;
    int bitrate_kbps;
    int encoder_stdout_fd;
    int icecast_fd;
    unsigned char *encoded_fifo;
    size_t encoded_fifo_capacity;
    size_t encoded_fifo_read_pos;
    size_t encoded_fifo_write_pos;
    size_t encoded_fifo_fill;
    size_t encoded_fifo_high_water;
    uint64_t encoded_fifo_overrun_count;
    uint64_t encoded_fifo_overrun_bytes;
    char output_id[WB_NATIVE_OUTPUT_ID_SIZE];
    char codec[WB_NATIVE_OUTPUT_CODEC_SIZE];
    char content_type[WB_NATIVE_OUTPUT_CONTENT_TYPE_SIZE];
    char host[WB_ICECAST_HOST_SIZE];
    char mount[WB_ICECAST_MOUNT_SIZE];
    char username[WB_ICECAST_USER_SIZE];
    char password[WB_ICECAST_PASSWORD_SIZE];
    char stream_name[WB_ICECAST_NAME_SIZE];
    char stream_description[WB_ICECAST_DESCRIPTION_SIZE];
    char stream_genre[WB_ICECAST_GENRE_SIZE];
    char stream_url[WB_ICECAST_URL_SIZE];
    char status[WB_ICECAST_STATUS_SIZE];
    char error[WB_ICECAST_ERROR_SIZE];
    char current_metadata[WB_ICECAST_METADATA_SIZE];
    char current_metadata_slot_token[WB_SLOT_TOKEN_SIZE];
    char metadata_error[WB_ICECAST_ERROR_SIZE];
    int64_t current_metadata_queue_id;
    int64_t metadata_not_before_monotonic_ms;
    int64_t next_reconnect_monotonic_ms;
    int64_t last_encoded_data_monotonic_ms;
    int64_t last_icecast_send_monotonic_ms;
    int64_t last_successful_send_monotonic_ms;
    int64_t output_gap_started_monotonic_ms;
    int64_t max_output_gap_ms;
    uint64_t config_generation;
    uint64_t metadata_generation;
    uint64_t metadata_applied_generation;
    uint64_t connect_count;
    uint64_t disconnect_count;
    uint64_t reconnect_count;
    uint64_t send_error_count;
    uint64_t consecutive_send_errors;
    uint64_t icecast_stall_count;
    uint64_t metadata_requested_count;
    uint64_t metadata_applied_count;
    uint64_t metadata_failed_count;
    uint64_t encoded_bytes_total;
    uint64_t icecast_sent_bytes_total;
    uint64_t discarded_encoded_bytes_total;
    uint64_t output_gap_count;
    int reconnect_backoff_seconds;
    char last_output_gap_reason[WB_DIAGNOSTIC_REASON_SIZE];
} WbNativeStreamOutput;

typedef struct {
    WbEngineState *owner;
    pthread_mutex_t lock;
    pthread_mutex_t encoder_control_lock;
    pthread_mutex_t pcm_route_lock;
    pthread_cond_t cond;
    pthread_t thread;
    pthread_t metadata_thread;
    bool thread_created;
    bool metadata_thread_created;
    bool shutdown;
    bool enabled;
    bool engine_running;
    bool paused;
    bool public_stream;
    bool add_year_to_metadata;
    bool connected;
    bool encoder_running;
    bool dsp_enabled;
    bool dsp_running;
    bool dsp_ready;
    bool dsp_route_active;
    bool dsp_live_bypass_until_ready;
    bool dsp_output_failed;
    bool restart_requested;
    bool metadata_pending;
    WbNativeStreamOutput streams[WB_NATIVE_OUTPUT_MAX];
    size_t stream_count;
    uint64_t stream_config_generation;
    uint64_t encoder_generation;
    int port;
    int bitrate_kbps;
    pid_t encoder_pid;
    void *encoder_context;
    void *dsp_context;
    pid_t dsp_pid;
    int encoder_stdin_fd;
    int encoder_stdout_fd;
    int dsp_output_fd;
    unsigned char *dsp_input_fifo;
    size_t dsp_input_fifo_capacity;
    size_t dsp_input_fifo_read_pos;
    size_t dsp_input_fifo_write_pos;
    size_t dsp_input_fifo_fill;
    size_t dsp_input_fifo_high_water;
    uint64_t dsp_input_fifo_overrun_count;
    uint64_t dsp_input_fifo_overrun_bytes;
    uint64_t dsp_input_fifo_startup_drop_bytes;
    uint64_t dsp_input_bytes_enqueued;
    uint64_t dsp_input_bytes_written;
    uint64_t dsp_write_poll_timeout_count;
    uint64_t dsp_write_error_count;
    uint64_t dsp_writer_generation;
    uint64_t dsp_reader_generation;
    uint64_t dsp_output_bytes_read;
    uint64_t dsp_output_push_error_count;
    uint64_t dsp_live_switch_count;
    int64_t dsp_started_monotonic_ms;
    int64_t dsp_writer_last_progress_monotonic_ms;
    int64_t dsp_writer_backpressure_started_monotonic_ms;
    int64_t dsp_writer_max_backpressure_ms;
    int64_t dsp_writer_error_since_monotonic_ms;
    int dsp_writer_last_errno;
    short dsp_writer_last_revents;
    int icecast_fd;
    char host[WB_ICECAST_HOST_SIZE];
    char mount[WB_ICECAST_MOUNT_SIZE];
    char username[WB_ICECAST_USER_SIZE];
    char password[WB_ICECAST_PASSWORD_SIZE];
    char stream_name[WB_ICECAST_NAME_SIZE];
    char stream_description[WB_ICECAST_DESCRIPTION_SIZE];
    char stream_genre[WB_ICECAST_GENRE_SIZE];
    char stream_url[WB_ICECAST_URL_SIZE];
    char status[WB_ICECAST_STATUS_SIZE];
    char error[WB_ICECAST_ERROR_SIZE];
    char dsp_executable_path[WB_PATH_SIZE];
    char dsp_config_path[WB_PATH_SIZE];
    char dsp_log_path[WB_PATH_SIZE];
    char dsp_status[WB_DSP_STATUS_SIZE];
    char dsp_error[WB_DSP_ERROR_SIZE];
    char current_metadata[WB_ICECAST_METADATA_SIZE];
    char current_metadata_slot_token[WB_SLOT_TOKEN_SIZE];
    char metadata_error[WB_ICECAST_ERROR_SIZE];
    int64_t current_metadata_queue_id;
    int64_t metadata_not_before_monotonic_ms;
    uint64_t metadata_generation;
    uint64_t metadata_applied_generation;
    unsigned char *deck_a_fifo;
    unsigned char *deck_b_fifo;
    size_t fifo_capacity;
    size_t deck_a_read_pos;
    size_t deck_a_write_pos;
    size_t deck_a_fill;
    size_t deck_b_read_pos;
    size_t deck_b_write_pos;
    size_t deck_b_fill;
    char deck_a_slot_token[WB_SLOT_TOKEN_SIZE];
    char deck_b_slot_token[WB_SLOT_TOKEN_SIZE];
    int64_t deck_a_queue_id;
    int64_t deck_b_queue_id;
    bool deck_a_started;
    bool deck_b_started;
    char primary_deck;
    bool transitioning;
    char transition_from_deck;
    char transition_to_deck;
    int64_t transition_start_monotonic_ms;
    int64_t transition_entry_start_monotonic_ms;
    int64_t transition_entry_requested_monotonic_ms;
    int64_t transition_entry_pcm_start_monotonic_ms;
    bool transition_entry_waiting_for_pcm;
    int64_t transition_duration_ms;
    int64_t transition_fade_out_ms;
    int64_t transition_entry_ramp_ms;
    int64_t transition_silence_hold_ms;
    bool hard_handoff_pending;
    bool hard_handoff_wait_outgoing_drain;
    char hard_handoff_from_deck;
    char hard_handoff_to_deck;
    int64_t hard_handoff_at_monotonic_ms;
    int64_t hard_handoff_requested_monotonic_ms;
    WbDeckState hard_handoff_from_track;
    WbDeckState hard_handoff_to_track;
    uint64_t hard_handoff_count;
    uint64_t hard_handoff_early_eof_count;
    uint64_t connect_count;
    uint64_t disconnect_count;
    uint64_t reconnect_count;
    uint64_t send_error_count;
    uint64_t encoder_restart_count;
    uint64_t pipeline_restart_count;
    uint64_t encoder_stall_count;
    uint64_t icecast_stall_count;
    uint64_t consecutive_send_errors;
    uint64_t metadata_requested_count;
    uint64_t metadata_applied_count;
    uint64_t metadata_failed_count;
    uint64_t encoder_kill_test_count;
    uint64_t dsp_start_count;
    uint64_t dsp_restart_count;
    uint64_t dsp_process_replacement_count;
    uint64_t dsp_stall_count;
    uint64_t dsp_kill_test_count;
    uint64_t dsp_startup_silence_frames;
    uint64_t dsp_input_backpressure_count;
    int64_t encoder_started_monotonic_ms;
    int64_t last_encoded_data_monotonic_ms;
    int64_t last_icecast_send_monotonic_ms;
    int reconnect_backoff_seconds;
    uint64_t encoded_bytes_sent;
    uint64_t encoded_bytes_total;
    uint64_t icecast_sent_bytes_total;
    uint64_t output_gap_count;
    int64_t max_output_gap_ms;
    int64_t last_successful_send_monotonic_ms;
    int64_t output_gap_started_monotonic_ms;
    char last_output_gap_reason[WB_DIAGNOSTIC_REASON_SIZE];
    size_t deck_a_fifo_high_water_bytes;
    size_t deck_b_fifo_high_water_bytes;
    pid_t old_encoder_pid;
    pid_t old_dsp_pid;
    pid_t new_dsp_pid;
    bool old_dsp_reaped;
    uint64_t dsp_reap_count;
    uint64_t dsp_zombie_count;
    int64_t dsp_reap_duration_ms;
    pid_t new_encoder_pid;
    bool old_encoder_reaped;
    uint64_t encoder_reap_count;
    uint64_t zombie_encoder_count;
    int64_t encoder_reap_duration_ms;
    uint64_t mixed_frames;
    uint64_t silence_frames;
    uint64_t deck_fifo_empty_count;
    uint64_t mixed_output_silence_count;
    uint64_t output_underrun_count;
    uint64_t output_underrun_event_count;
    uint64_t output_underrun_suppressed_event_count;
    uint64_t output_underrun_suppressed_since_last_event;
    int64_t last_output_underrun_event_monotonic_ms;
    uint64_t transition_early_eof_count;
    uint64_t active_early_eof_count;
    uint64_t fifo_overrun_count;
    uint64_t fifo_overrun_bytes;
    uint64_t stale_pcm_drop_count;
    uint64_t stale_pcm_drop_bytes;
    uint64_t seek_flush_count;
    bool deck_a_seek_pending;
    bool deck_b_seek_pending;
    char deck_a_seek_slot_token[WB_SLOT_TOKEN_SIZE];
    char deck_b_seek_slot_token[WB_SLOT_TOKEN_SIZE];
    uint64_t seek_bridge_count;
    uint64_t seek_bridge_bytes;
    uint64_t seek_bridge_drop_bytes;
    uint64_t seek_old_pcm_drop_count;
    uint64_t seek_old_pcm_drop_bytes;
    uint64_t transition_entry_pcm_start_count;
} WbIcecastOutput;

typedef struct {
    WbEngineState *owner;
    pthread_cond_t cond;
    pthread_t thread;
    bool thread_created;
    bool shutdown;
    bool request_pending;
    bool running;
    uint64_t generation;
    char deck;
    WbDeckState track;
} WbAudioAnalysisWorker;

typedef struct {
    WbEngineState *owner;
    pthread_cond_t cond;
    pthread_t thread;
    bool thread_created;
    bool shutdown;
    int next_request_lead_ms;
    int hard_handoff_arm_lead_ms;
    int poll_interval_ms;
    int next_request_retry_ms;
    int64_t last_next_request_monotonic_ms;
    int64_t requested_for_queue_id;
    char requested_for_slot_token[WB_SLOT_TOKEN_SIZE];
    int64_t scheduled_for_queue_id;
    char scheduled_for_slot_token[WB_SLOT_TOKEN_SIZE];
    bool transition_completion_pending;
    int64_t transition_completion_monotonic_ms;
    char transition_from_deck;
    char transition_to_deck;
    WbDeckState transition_from_track;
    WbDeckState transition_to_track;
    uint64_t next_track_request_count;
    uint64_t hard_handoff_arm_count;
    uint64_t transition_start_count;
    uint64_t transition_complete_count;
} WbNativeTimingWorker;

typedef struct {
    WbEngineState *owner;
    pthread_cond_t cond;
    pthread_t thread;
    bool thread_created;
    bool shutdown;
    bool request_pending;
    bool activated;
    bool stop_requested;
    bool running;
    bool eof;
    bool prebuffer_ready;
    bool final_duration_valid;
    bool replacement_pending;
    bool replacement_activate;
    bool replacement_is_seek;
    bool audio_mismatch_emitted;
    bool fault_armed;
    bool fault_triggered;
    uint64_t generation;
    uint64_t seek_restart_generation;
    uint64_t candidate_serial;
    uint64_t decoded_samples;
    uint64_t played_samples;
    int64_t decoded_duration_ms;
    int64_t played_duration_ms;
    int64_t position_ms;
    int64_t final_actual_duration_ms;
    int64_t activation_monotonic_ms;
    int64_t first_sample_monotonic_ms;
    int64_t fault_after_ms;
    int64_t fault_duration_ms;
    int64_t fault_trigger_monotonic_ms;
    int64_t requested_activation_monotonic_ms;
    pid_t child_pid;
    char deck;
    char status[WB_AUDIO_STATUS_SIZE];
    char error[WB_AUDIO_ERROR_SIZE];
    char stop_reason[WB_EVENT_NAME_SIZE];
    char fault_mode[WB_FAULT_MODE_SIZE];
    WbDeckState track;
    size_t alias_count;
    WbDeckState aliases[WB_AUDIO_ALIAS_CAPACITY];
    WbDeckState replacement_track;
    int64_t replacement_activation_monotonic_ms;
    int64_t seek_from_position_ms;
    int64_t seek_target_position_ms;
    unsigned slot_index;
    unsigned char *ring_buffer;
    size_t ring_capacity;
    size_t ring_read_pos;
    size_t ring_write_pos;
    size_t ring_fill;
    size_t ring_high_water_bytes;
    size_t prebuffer_target_bytes;
} WbAudioDeckProbe;

struct WbEngineState {
    pthread_mutex_t lock;
    pthread_mutex_t send_lock;
    bool running;
    bool paused;
    int64_t pause_started_monotonic_ms;
    bool accepting_loads;
    bool transitioning;
    char active_deck;
    uint64_t live_sync_count;
    uint64_t planned_load_count;
    uint64_t confirmed_load_count;
    uint64_t cancelled_load_count;
    uint64_t late_load_rejected_count;
    uint64_t late_event_ignored_count;
    char last_live_event[WB_EVENT_NAME_SIZE];
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    WbDeckState deck_a;
    WbDeckState deck_b;
    WbDeckState planned_deck_a;
    WbDeckState planned_deck_b;

    int client_fd;
    char station_key[WB_STATION_KEY_SIZE];
    pthread_mutex_t *shared_send_lock;
    WbEngineEventSink event_sink;
    void *event_sink_context;
    bool audio_probe_enabled;
    bool audio_probe_realtime;
    int audio_probe_sample_rate;
    int audio_probe_channels;
    int audio_ring_capacity_ms;
    int audio_prebuffer_ms;
    int audio_start_timeout_ms;
    int audio_seek_start_timeout_ms;
    int audio_seek_hard_timeout_ms;
    char ffmpeg_path[WB_PATH_SIZE];
    char ffmpeg_source[WB_FFMPEG_SOURCE_SIZE];
    char ffmpeg_version[WB_FFMPEG_VERSION_SIZE];
    char ffmpeg_runtime_build[WB_FFMPEG_BUILD_SIZE];
    char ffmpeg_runtime_error[WB_FFMPEG_ERROR_SIZE];
    bool ffmpeg_runtime_valid;
    bool ffmpeg_system_fallback_used;
    uint64_t audio_candidate_serial;
    uint64_t audio_candidate_evicted_count;
    uint64_t audio_candidate_cancelled_count;
    uint64_t audio_runtime_mismatch_count;
    uint64_t audio_runtime_mismatch_total_count;
    uint64_t audio_runtime_mismatch_recovered_count;
    bool fault_enabled;
    bool fault_once;
    char fault_mode[WB_FAULT_MODE_SIZE];
    char fault_target_deck;
    char fault_target_slot_token[WB_SLOT_TOKEN_SIZE];
    int64_t fault_after_ms;
    int64_t fault_duration_ms;
    uint64_t fault_arm_count;
    uint64_t fault_trigger_count;
    uint64_t audio_buffer_underrun_count;
    char fault_last_mode[WB_FAULT_MODE_SIZE];
    char fault_last_slot_token[WB_SLOT_TOKEN_SIZE];
    char fault_last_terminal_reason[WB_FAULT_REASON_SIZE];
    int64_t last_live_event_monotonic_ms;
    int64_t last_live_event_wall_time_unix_ms;
    WbAudioDeckProbe audio_deck_a;
    WbAudioDeckProbe audio_deck_a_alt;
    WbAudioDeckProbe audio_deck_b;
    WbAudioDeckProbe audio_deck_b_alt;
    WbAudioAnalysisWorker analysis_deck_a;
    WbAudioAnalysisWorker analysis_deck_b;
    int audio_analysis_timeout_ms;
    uint64_t audio_analysis_started_count;
    uint64_t audio_analysis_ready_count;
    uint64_t audio_analysis_failed_count;
    uint64_t audio_analysis_superseded_count;
    WbNativeTimingWorker native_timing;
    WbIcecastOutput icecast_output;

    pthread_t diagnostics_thread;
    bool diagnostics_thread_created;
    bool diagnostics_shutdown;
    int diagnostics_interval_ms;
    int64_t diagnostics_started_monotonic_ms;
    uint64_t diagnostic_snapshot_count;
    uint64_t diagnostic_max_rss_kb;
    uint64_t diagnostic_max_virtual_memory_kb;
    uint64_t diagnostic_max_thread_count;
    uint64_t diagnostic_max_fd_count;
    uint64_t diagnostic_last_rss_kb;
    uint64_t diagnostic_last_virtual_memory_kb;
    uint64_t diagnostic_last_cpu_user_ms;
    uint64_t diagnostic_last_cpu_system_ms;
    uint64_t diagnostic_last_thread_count;
    uint64_t diagnostic_last_fd_count;
};

void wb_engine_init(WbEngineState *state);
void wb_engine_destroy(WbEngineState *state);
void wb_engine_client_connected(WbEngineState *state, int client_fd);
void wb_engine_client_disconnected(WbEngineState *state, int client_fd);
int wb_engine_send_ready(WbEngineState *state, int client_fd, const char *socket_path);
int wb_engine_handle_line(WbEngineState *state, int client_fd, const char *line);
int wb_engine_send_event_to_fd(
    WbEngineState *state,
    int fd,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
);
int wb_engine_send_event(
    WbEngineState *state,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
);

#endif
