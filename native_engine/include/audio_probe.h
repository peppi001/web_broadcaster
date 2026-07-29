#ifndef WB_AUDIO_PROBE_H
#define WB_AUDIO_PROBE_H

#include "engine.h"

int wb_audio_probe_init(WbEngineState *state);
void wb_audio_probe_destroy(WbEngineState *state);
void wb_audio_probe_prepare_deck(WbEngineState *state, char deck, const WbDeckState *track);
void wb_audio_probe_activate_deck(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    bool validate_descriptor,
    int64_t requested_activation_monotonic_ms
);
size_t wb_audio_probe_prime_deck(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    unsigned char *pcm,
    size_t pcm_capacity,
    int64_t requested_activation_monotonic_ms
);
bool wb_audio_probe_retime_activation(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t requested_activation_monotonic_ms
);
bool wb_audio_probe_get_position_ms(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t *position_ms
);
bool wb_audio_probe_is_prebuffer_ready(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    size_t *ring_fill_bytes
);
void wb_audio_probe_seek_track(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    int64_t seek_position_ms
);
void wb_audio_probe_stop_track(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    const char *reason
);
void wb_audio_probe_stop_all(WbEngineState *state, const char *reason);

#endif
