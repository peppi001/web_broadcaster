#ifndef WB_ICECAST_OUTPUT_H
#define WB_ICECAST_OUTPUT_H

#include "engine.h"

int wb_icecast_output_init(WbEngineState *state);
void wb_icecast_output_destroy(WbEngineState *state);
void wb_icecast_output_set_engine_running(WbEngineState *state, bool running);
void wb_icecast_output_set_paused(WbEngineState *state, bool paused, int64_t pause_duration_ms);
void wb_icecast_output_activate_track(
    WbEngineState *state,
    char deck,
    const WbDeckState *track
);
void wb_icecast_output_seek_track(
    WbEngineState *state,
    char deck,
    const char *slot_token,
    const unsigned char *bridge_pcm,
    size_t bridge_bytes
);
void wb_icecast_output_stop_track(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token
);
void wb_icecast_output_transition_started(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    int64_t start_monotonic_ms,
    int64_t entry_start_monotonic_ms,
    int64_t release_duration_ms,
    int64_t fade_out_duration_ms,
    int64_t entry_ramp_ms,
    int64_t silence_hold_ms
);
void wb_icecast_output_transition_finished(WbEngineState *state, char to_deck);
bool wb_icecast_output_get_deck_buffered_ms(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t *buffered_ms
);
int wb_icecast_output_schedule_hard_handoff(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    const WbDeckState *from_track,
    const WbDeckState *to_track,
    int64_t handoff_at_monotonic_ms,
    const unsigned char *primed_pcm,
    size_t primed_bytes,
    char *error,
    size_t error_size
);
bool wb_icecast_output_has_pending_hard_handoff(
    WbEngineState *state,
    char from_deck,
    const WbDeckState *from_track
);
bool wb_icecast_output_handle_terminal_eof(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t early_by_ms,
    bool early_eof
);
void wb_icecast_output_handle_early_eof(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t early_by_ms
);
void wb_icecast_output_push_pcm(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    const unsigned char *data,
    size_t bytes,
    bool seek_restart
);
int wb_icecast_output_configure(
    WbEngineState *state,
    const char *output_id,
    const char *codec,
    bool enabled,
    const char *host,
    int port,
    const char *mount,
    const char *username,
    const char *password,
    int bitrate_kbps,
    const char *stream_name,
    const char *stream_description,
    const char *stream_genre,
    const char *stream_url,
    bool public_stream,
    bool add_year_to_metadata,
    bool dsp_enabled,
    const char *dsp_config_path,
    char *error,
    size_t error_size
);
int wb_icecast_output_clear_stream(
    WbEngineState *state, const char *output_id, char *error, size_t error_size
);
void wb_icecast_output_clear(WbEngineState *state);
int wb_icecast_output_kill_encoder(WbEngineState *state, char *error, size_t error_size);
int wb_icecast_output_kill_dsp(WbEngineState *state, char *error, size_t error_size);
int wb_icecast_output_state_json(WbEngineState *state, char *output, size_t output_size);

#endif
