#ifndef WB_AUDIO_ANALYSIS_H
#define WB_AUDIO_ANALYSIS_H

#include "engine.h"

int wb_audio_analysis_init(WbEngineState *state);
void wb_audio_analysis_destroy(WbEngineState *state);
void wb_audio_analysis_schedule(WbEngineState *state, char deck, const WbDeckState *track);
bool wb_audio_analysis_wait_ready(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    int timeout_ms,
    WbDeckState *result
);
bool wb_audio_analysis_snapshot(
    WbEngineState *state,
    char deck,
    const WbDeckState *identity,
    WbDeckState *result
);

#endif
