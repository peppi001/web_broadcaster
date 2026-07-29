#ifndef WB_DIAGNOSTICS_H
#define WB_DIAGNOSTICS_H

#include "engine.h"

int wb_diagnostics_init(WbEngineState *state);
void wb_diagnostics_destroy(WbEngineState *state);
int wb_diagnostics_state_json(WbEngineState *state, char *output_json, size_t output_size);
void wb_diagnostics_emit_snapshot(WbEngineState *state, const char *reason);

#endif
