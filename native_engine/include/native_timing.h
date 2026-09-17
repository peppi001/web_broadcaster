#ifndef WB_NATIVE_TIMING_H
#define WB_NATIVE_TIMING_H

#include "engine.h"

int wb_native_timing_init(WbEngineState *state);
void wb_native_timing_destroy(WbEngineState *state);
void wb_native_timing_wake(WbEngineState *state);
void wb_native_timing_reset(WbEngineState *state);
int wb_native_timing_start_script_interrupt(
    WbEngineState *state,
    char to_deck,
    const WbDeckState *to_track,
    int64_t fade_ms,
    char *error,
    size_t error_size
);

#endif
