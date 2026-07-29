#ifndef WB_NATIVE_TIMING_H
#define WB_NATIVE_TIMING_H

#include "engine.h"

int wb_native_timing_init(WbEngineState *state);
void wb_native_timing_destroy(WbEngineState *state);
void wb_native_timing_wake(WbEngineState *state);
void wb_native_timing_reset(WbEngineState *state);

#endif
