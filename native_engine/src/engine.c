#define _POSIX_C_SOURCE 200809L

#include "engine.h"
#include "audio_probe.h"
#include "audio_analysis.h"
#include "native_timing.h"
#include "icecast_output.h"
#include "diagnostics.h"
#include "protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define WB_HARD_HANDOFF_PRIME_MS 80
#define WB_HARD_HANDOFF_PRIME_BYTES \
    (((WB_AUDIO_SAMPLE_RATE * WB_HARD_HANDOFF_PRIME_MS) / 1000) * WB_AUDIO_FRAME_BYTES)

static WbDeckState *deck_for(WbEngineState *state, char deck) {
    return deck == 'B' ? &state->deck_b : &state->deck_a;
}

static WbDeckState *planned_deck_for(WbEngineState *state, char deck) {
    return deck == 'B' ? &state->planned_deck_b : &state->planned_deck_a;
}

static char normalized_deck(const char *deck) {
    if (deck != NULL && (deck[0] == 'B' || deck[0] == 'b')) return 'B';
    return 'A';
}

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static char optional_deck(const char *deck) {
    if (deck != NULL && (deck[0] == 'A' || deck[0] == 'a')) return 'A';
    if (deck != NULL && (deck[0] == 'B' || deck[0] == 'b')) return 'B';
    return '\0';
}

static void copy_text(char *destination, size_t size, const char *source) {
    if (size == 0) return;
    (void)snprintf(destination, size, "%s", source == NULL ? "" : source);
}

static void set_deck_metadata(WbDeckState *target, const char *artist, const char *title, const char *year) {
    if (target == NULL) return;
    if (artist != NULL && artist[0] != '\0') {
        copy_text(target->artist, sizeof(target->artist), artist);
    }
    if (title != NULL && title[0] != '\0') {
        copy_text(target->title, sizeof(target->title), title);
    }
    if (year != NULL && year[0] != '\0') {
        copy_text(target->year, sizeof(target->year), year);
    }
}

static int state_send_line(WbEngineState *state, int fd, const char *line) {
    int result;
    if (fd < 0) return 0;
    pthread_mutex_t *send_lock = state->shared_send_lock != NULL ? state->shared_send_lock : &state->send_lock;
    (void)pthread_mutex_lock(send_lock);
    result = wb_send_line(fd, line);
    (void)pthread_mutex_unlock(send_lock);
    return result;
}

static void clear_deck_state(WbDeckState *target) {
    if (target != NULL) memset(target, 0, sizeof(*target));
}

static void set_deck_identity(
    WbDeckState *target,
    int64_t queue_id,
    int64_t track_id,
    const char *slot_token,
    const char *station_key,
    const char *path
) {
    target->loaded = true;
    target->queue_id = queue_id < 0 ? 0 : queue_id;
    target->track_id = track_id < 0 ? 0 : track_id;
    copy_text(target->slot_token, sizeof(target->slot_token), slot_token);
    copy_text(target->station_key, sizeof(target->station_key), station_key);
    copy_text(target->path, sizeof(target->path), path);
}

static void normalize_deck_timing(WbDeckState *target) {
    if (target == NULL) return;
    if (target->play_start_ms <= 0 && target->cue_in_ms > 0) {
        target->play_start_ms = target->cue_in_ms;
    }
    if (target->audio_start_ms <= 0 && target->play_start_ms > 0) {
        target->audio_start_ms = target->play_start_ms;
    }
    if (target->transition_at_ms <= 0 && target->cue_out_ms > 0) {
        target->transition_at_ms = target->cue_out_ms;
    }
    if (target->effective_end_ms <= 0) {
        target->effective_end_ms = target->source_end_ms > 0
            ? target->source_end_ms
            : target->transition_at_ms;
    }
    if (target->effective_end_ms < target->transition_at_ms) {
        target->effective_end_ms = target->transition_at_ms;
    }
    if (target->source_end_ms <= 0) {
        target->source_end_ms = target->effective_end_ms > 0
            ? target->effective_end_ms
            : target->transition_at_ms;
    }
    if (target->source_end_ms < target->effective_end_ms) {
        target->source_end_ms = target->effective_end_ms;
    }
    target->cue_in_ms = target->play_start_ms;
    target->cue_out_ms = target->transition_at_ms;
}

static void set_deck_descriptor(
    WbDeckState *target,
    int64_t queue_id,
    int64_t track_id,
    const char *slot_token,
    const char *station_key,
    const char *path,
    int64_t cue_in_ms,
    int64_t cue_out_ms,
    int64_t audio_start_ms,
    int64_t play_start_ms,
    int64_t transition_at_ms,
    int64_t effective_end_ms,
    int64_t source_end_ms,
    int64_t fade_in_ms,
    int64_t fade_out_ms
) {
    clear_deck_state(target);
    set_deck_identity(target, queue_id, track_id, slot_token, station_key, path);
    target->cue_in_ms = cue_in_ms < 0 ? 0 : cue_in_ms;
    target->cue_out_ms = cue_out_ms < 0 ? 0 : cue_out_ms;
    target->audio_start_ms = audio_start_ms < 0 ? 0 : audio_start_ms;
    target->play_start_ms = play_start_ms < 0 ? 0 : play_start_ms;
    target->transition_at_ms = transition_at_ms < 0 ? 0 : transition_at_ms;
    target->effective_end_ms = effective_end_ms < 0 ? 0 : effective_end_ms;
    target->source_end_ms = source_end_ms < 0 ? 0 : source_end_ms;
    target->fade_in_ms = fade_in_ms < 0 ? 0 : fade_in_ms;
    target->fade_out_ms = fade_out_ms < 0 ? 0 : fade_out_ms;
    normalize_deck_timing(target);
}

static void update_deck_identity_preserving_descriptor(
    WbDeckState *target,
    int64_t queue_id,
    int64_t track_id,
    const char *slot_token,
    const char *station_key,
    const char *path
) {
    target->loaded = true;
    target->queue_id = queue_id < 0 ? 0 : queue_id;
    if (track_id > 0) target->track_id = track_id;
    if (slot_token != NULL && slot_token[0] != '\0') {
        copy_text(target->slot_token, sizeof(target->slot_token), slot_token);
    }
    if (station_key != NULL && station_key[0] != '\0') {
        copy_text(target->station_key, sizeof(target->station_key), station_key);
    }
    if (path != NULL && path[0] != '\0') {
        copy_text(target->path, sizeof(target->path), path);
    }
}

static bool identity_matches(
    const WbDeckState *candidate,
    int64_t queue_id,
    const char *slot_token
) {
    if (candidate == NULL || !candidate->loaded) return false;
    if (slot_token != NULL && slot_token[0] != '\0') {
        return strcmp(candidate->slot_token, slot_token) == 0;
    }
    if (queue_id > 0) return candidate->queue_id == queue_id;
    return false;
}

static const WbDeckState *probe_identity_track(
    const WbAudioDeckProbe *probe,
    int64_t queue_id,
    const char *slot_token
) {
    size_t index;
    if (identity_matches(&probe->track, queue_id, slot_token)) return &probe->track;
    for (index = 0U; index < probe->alias_count; index += 1U) {
        if (identity_matches(&probe->aliases[index], queue_id, slot_token)) {
            return &probe->aliases[index];
        }
    }
    return NULL;
}

static void snapshot_protocol_identity(
    WbEngineState *state,
    char *session_id,
    size_t session_id_size,
    char *app_version,
    size_t app_version_size
) {
    (void)pthread_mutex_lock(&state->lock);
    copy_text(session_id, session_id_size, state->session_id);
    copy_text(app_version, app_version_size, state->app_version);
    (void)pthread_mutex_unlock(&state->lock);
}

static void update_protocol_identity(WbEngineState *state, const char *line) {
    char session_id[WB_SESSION_ID_SIZE] = "";
    char app_version[WB_APP_VERSION_SIZE] = "";
    bool has_session = wb_json_get_string(line, "session_id", session_id, sizeof(session_id));
    bool has_app_version = wb_json_get_string(line, "app_version", app_version, sizeof(app_version));
    if (!has_session && !has_app_version) return;
    (void)pthread_mutex_lock(&state->lock);
    if (has_session && session_id[0] != '\0') {
        copy_text(state->session_id, sizeof(state->session_id), session_id);
    }
    if (has_app_version && app_version[0] != '\0') {
        copy_text(state->app_version, sizeof(state->app_version), app_version);
    }
    (void)pthread_mutex_unlock(&state->lock);
}

static int send_context_reply(
    WbEngineState *state,
    int fd,
    int64_t request_id,
    const char *result_json
) {
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char escaped_session[WB_SESSION_ID_SIZE * 2];
    char escaped_app[WB_APP_VERSION_SIZE * 2];
    const char *result = result_json == NULL ? "null" : result_json;
    size_t line_size;
    char *line;
    int written;
    int send_result;
    snapshot_protocol_identity(
        state, session_id, sizeof(session_id), app_version, sizeof(app_version)
    );
    wb_json_escape(session_id, escaped_session, sizeof(escaped_session));
    wb_json_escape(app_version, escaped_app, sizeof(escaped_app));
    line_size = strlen(result) + strlen(escaped_session) + strlen(escaped_app)
        + strlen(WB_NATIVE_DAEMON_VERSION) + 256U;
    if (line_size > 4U * 1024U * 1024U) return -1;
    line = malloc(line_size);
    if (line == NULL) return -1;
    written = snprintf(
        line,
        line_size,
        "{\"version\":%d,\"reply_to\":%lld,\"ok\":true,"
        "\"session_id\":\"%s\",\"app_version\":\"%s\","
        "\"native_daemon_version\":\"%s\",\"result\":%s}",
        WB_PROTOCOL_VERSION,
        (long long)request_id,
        escaped_session,
        escaped_app,
        WB_NATIVE_DAEMON_VERSION,
        result
    );
    if (written < 0 || (size_t)written >= line_size) {
        free(line);
        return -1;
    }
    send_result = state_send_line(state, fd, line);
    free(line);
    return send_result;
}

static int send_context_error(
    WbEngineState *state,
    int fd,
    int64_t request_id,
    const char *error
) {
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char escaped_session[WB_SESSION_ID_SIZE * 2];
    char escaped_app[WB_APP_VERSION_SIZE * 2];
    char escaped_error[2048];
    char line[3072];
    snapshot_protocol_identity(
        state, session_id, sizeof(session_id), app_version, sizeof(app_version)
    );
    wb_json_escape(session_id, escaped_session, sizeof(escaped_session));
    wb_json_escape(app_version, escaped_app, sizeof(escaped_app));
    wb_json_escape(error, escaped_error, sizeof(escaped_error));
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"reply_to\":%lld,\"ok\":false,"
        "\"session_id\":\"%s\",\"app_version\":\"%s\","
        "\"native_daemon_version\":\"%s\",\"error\":\"%s\"}",
        WB_PROTOCOL_VERSION,
        (long long)request_id,
        escaped_session,
        escaped_app,
        WB_NATIVE_DAEMON_VERSION,
        escaped_error
    );
    return state_send_line(state, fd, line);
}


static bool native_fault_mode_valid(const char *mode) {
    if (mode == NULL || mode[0] == '\0') return false;
    return strcmp(mode, "early_eof") == 0
        || strcmp(mode, "kill_decoder") == 0
        || strcmp(mode, "decoder_stall") == 0
        || strcmp(mode, "buffer_underrun") == 0
        || strcmp(mode, "corrupt_input") == 0
        || strcmp(mode, "missing_file") == 0;
}

static void disarm_probe_fault_locked(WbAudioDeckProbe *probe) {
    if (probe == NULL || probe->fault_triggered) return;
    probe->fault_armed = false;
    probe->fault_after_ms = 0;
    probe->fault_duration_ms = 0;
    probe->fault_trigger_monotonic_ms = 0;
    probe->fault_mode[0] = '\0';
}

static void clear_waiting_faults_locked(WbEngineState *state) {
    disarm_probe_fault_locked(&state->audio_deck_a);
    disarm_probe_fault_locked(&state->audio_deck_a_alt);
    disarm_probe_fault_locked(&state->audio_deck_b);
    disarm_probe_fault_locked(&state->audio_deck_b_alt);
}

static int send_fault_state_reply(WbEngineState *state, int fd, int64_t request_id) {
    char mode[WB_FAULT_MODE_SIZE];
    char token[WB_SLOT_TOKEN_SIZE];
    char last_mode[WB_FAULT_MODE_SIZE];
    char last_token[WB_SLOT_TOKEN_SIZE];
    char last_terminal_reason[WB_FAULT_REASON_SIZE];
    char escaped_mode[WB_FAULT_MODE_SIZE * 2];
    char escaped_token[WB_SLOT_TOKEN_SIZE * 2];
    char escaped_last_mode[WB_FAULT_MODE_SIZE * 2];
    char escaped_last_token[WB_SLOT_TOKEN_SIZE * 2];
    char escaped_last_terminal_reason[WB_FAULT_REASON_SIZE * 2];
    char active_mode[WB_FAULT_MODE_SIZE] = "";
    char active_token[WB_SLOT_TOKEN_SIZE] = "";
    char escaped_active_mode[WB_FAULT_MODE_SIZE * 2];
    char escaped_active_token[WB_SLOT_TOKEN_SIZE * 2];
    char active_deck = '\0';
    char target_deck_text[2] = "";
    char active_deck_text[2] = "";
    bool active_armed = false;
    bool active_triggered = false;
    bool enabled;
    bool once;
    char target_deck;
    int64_t after_ms;
    int64_t duration_ms;
    uint64_t arm_count;
    uint64_t trigger_count;
    uint64_t underrun_count;
    WbAudioDeckProbe *probes[4];
    size_t index;
    char result[4096];

    (void)pthread_mutex_lock(&state->lock);
    enabled = state->fault_enabled;
    once = state->fault_once;
    target_deck = state->fault_target_deck;
    after_ms = state->fault_after_ms;
    duration_ms = state->fault_duration_ms;
    arm_count = state->fault_arm_count;
    trigger_count = state->fault_trigger_count;
    underrun_count = state->audio_buffer_underrun_count;
    copy_text(mode, sizeof(mode), state->fault_mode);
    copy_text(token, sizeof(token), state->fault_target_slot_token);
    copy_text(last_mode, sizeof(last_mode), state->fault_last_mode);
    copy_text(last_token, sizeof(last_token), state->fault_last_slot_token);
    copy_text(
        last_terminal_reason,
        sizeof(last_terminal_reason),
        state->fault_last_terminal_reason
    );
    probes[0] = &state->audio_deck_a;
    probes[1] = &state->audio_deck_a_alt;
    probes[2] = &state->audio_deck_b;
    probes[3] = &state->audio_deck_b_alt;
    for (index = 0U; index < 4U; index += 1U) {
        WbAudioDeckProbe *probe = probes[index];
        if (probe->fault_armed || probe->fault_triggered) {
            active_armed = probe->fault_armed;
            active_triggered = probe->fault_triggered;
            active_deck = probe->deck;
            copy_text(active_mode, sizeof(active_mode), probe->fault_mode);
            copy_text(active_token, sizeof(active_token), probe->track.slot_token);
            break;
        }
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (target_deck) target_deck_text[0] = target_deck;
    if (active_deck) active_deck_text[0] = active_deck;
    wb_json_escape(mode, escaped_mode, sizeof(escaped_mode));
    wb_json_escape(token, escaped_token, sizeof(escaped_token));
    wb_json_escape(last_mode, escaped_last_mode, sizeof(escaped_last_mode));
    wb_json_escape(last_token, escaped_last_token, sizeof(escaped_last_token));
    wb_json_escape(
        last_terminal_reason,
        escaped_last_terminal_reason,
        sizeof(escaped_last_terminal_reason)
    );
    wb_json_escape(active_mode, escaped_active_mode, sizeof(escaped_active_mode));
    wb_json_escape(active_token, escaped_active_token, sizeof(escaped_active_token));
    (void)snprintf(
        result,
        sizeof(result),
        "{\"supported\":true,\"enabled\":%s,\"mode\":\"%s\","
        "\"target_deck\":\"%s\",\"target_slot_token\":\"%s\","
        "\"after_ms\":%lld,\"duration_ms\":%lld,\"once\":%s,"
        "\"arm_count\":%llu,\"trigger_count\":%llu,\"underrun_count\":%llu,"
        "\"last_mode\":\"%s\",\"last_slot_token\":\"%s\","
        "\"last_terminal_reason\":\"%s\","
        "\"last_fault_mode\":\"%s\",\"last_fault_slot_token\":\"%s\","
        "\"last_fault_terminal_reason\":\"%s\","
        "\"active_armed\":%s,\"active_triggered\":%s,"
        "\"active_deck\":\"%s\",\"active_mode\":\"%s\","
        "\"active_slot_token\":\"%s\"}",
        enabled ? "true" : "false",
        escaped_mode,
        target_deck_text,
        escaped_token,
        (long long)after_ms,
        (long long)duration_ms,
        once ? "true" : "false",
        (unsigned long long)arm_count,
        (unsigned long long)trigger_count,
        (unsigned long long)underrun_count,
        escaped_last_mode,
        escaped_last_token,
        escaped_last_terminal_reason,
        escaped_last_mode,
        escaped_last_token,
        escaped_last_terminal_reason,
        active_armed ? "true" : "false",
        active_triggered ? "true" : "false",
        active_deck_text,
        escaped_active_mode,
        escaped_active_token
    );
    return send_context_reply(state, fd, request_id, result);
}

static int handle_configure_fault(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char mode[WB_FAULT_MODE_SIZE] = "";
    char deck_text[8] = "";
    char slot_token[WB_SLOT_TOKEN_SIZE] = "";
    int64_t after_ms = 3000;
    int64_t duration_ms = 6000;
    bool once = true;

    (void)wb_json_get_string(line, "mode", mode, sizeof(mode));
    (void)wb_json_get_string(line, "target_deck", deck_text, sizeof(deck_text));
    (void)wb_json_get_string(line, "target_slot_token", slot_token, sizeof(slot_token));
    (void)wb_json_get_i64(line, "after_ms", &after_ms);
    (void)wb_json_get_i64(line, "duration_ms", &duration_ms);
    (void)wb_json_get_bool(line, "once", &once);
    if (!native_fault_mode_valid(mode)) {
        return send_context_error(state, fd, request_id, "unsupported native fault mode");
    }
    if (after_ms < 0 || after_ms > 3600000LL) {
        return send_context_error(state, fd, request_id, "fault after_ms must be between 0 and 3600000");
    }
    if (duration_ms < 0 || duration_ms > 3600000LL) {
        return send_context_error(state, fd, request_id, "fault duration_ms must be between 0 and 3600000");
    }

    (void)pthread_mutex_lock(&state->lock);
    clear_waiting_faults_locked(state);
    state->fault_enabled = true;
    state->fault_once = once;
    state->fault_target_deck = optional_deck(deck_text);
    state->fault_after_ms = after_ms;
    state->fault_duration_ms = duration_ms;
    copy_text(state->fault_mode, sizeof(state->fault_mode), mode);
    copy_text(state->fault_target_slot_token, sizeof(state->fault_target_slot_token), slot_token);
    (void)pthread_mutex_unlock(&state->lock);
    return send_fault_state_reply(state, fd, request_id);
}

static int handle_clear_fault(WbEngineState *state, int fd, int64_t request_id) {
    (void)pthread_mutex_lock(&state->lock);
    state->fault_enabled = false;
    state->fault_mode[0] = '\0';
    state->fault_target_deck = '\0';
    state->fault_target_slot_token[0] = '\0';
    clear_waiting_faults_locked(state);
    (void)pthread_mutex_unlock(&state->lock);
    return send_fault_state_reply(state, fd, request_id);
}


static int send_diagnostics_state_reply(WbEngineState *state, int fd, int64_t request_id) {
    char result[8192];
    int written = wb_diagnostics_state_json(state, result, sizeof(result));
    if (written < 0 || (size_t)written >= sizeof(result)) {
        return send_context_error(state, fd, request_id, "native diagnostics state is too large");
    }
    return send_context_reply(state, fd, request_id, result);
}

static int send_icecast_output_state_reply(WbEngineState *state, int fd, int64_t request_id) {
    char result[65536];
    int written = wb_icecast_output_state_json(state, result, sizeof(result));
    if (written < 0 || (size_t)written >= sizeof(result)) {
        return send_context_error(state, fd, request_id, "native Icecast state is too large");
    }
    return send_context_reply(state, fd, request_id, result);
}

static int handle_configure_icecast_output(
    WbEngineState *state,
    int fd,
    int64_t request_id,
    const char *line
) {
    bool enabled = false;
    bool public_stream = false;
    bool add_year_to_metadata = false;
    bool dsp_enabled = false;
    char output_id[WB_NATIVE_OUTPUT_ID_SIZE] = "mp3";
    char codec[WB_NATIVE_OUTPUT_CODEC_SIZE] = "mp3";
    char host[WB_ICECAST_HOST_SIZE] = "";
    char mount[WB_ICECAST_MOUNT_SIZE] = "";
    char username[WB_ICECAST_USER_SIZE] = "source";
    char password[WB_ICECAST_PASSWORD_SIZE] = "";
    char stream_name[WB_ICECAST_NAME_SIZE] = "";
    char stream_description[WB_ICECAST_DESCRIPTION_SIZE] = "";
    char stream_genre[WB_ICECAST_GENRE_SIZE] = "";
    char stream_url[WB_ICECAST_URL_SIZE] = "";
    char dsp_config_path[WB_PATH_SIZE] = "";
    int64_t port = 8000;
    int64_t bitrate_kbps = 192;
    char error[WB_ICECAST_ERROR_SIZE] = "";
    (void)wb_json_get_bool(line, "enabled", &enabled);
    (void)wb_json_get_string(line, "output_id", output_id, sizeof(output_id));
    (void)wb_json_get_string(line, "codec", codec, sizeof(codec));
    (void)wb_json_get_bool(line, "public_stream", &public_stream);
    (void)wb_json_get_bool(line, "add_year_to_metadata", &add_year_to_metadata);
    (void)wb_json_get_bool(line, "dsp_enabled", &dsp_enabled);
    (void)wb_json_get_string(line, "host", host, sizeof(host));
    (void)wb_json_get_string(line, "mount", mount, sizeof(mount));
    (void)wb_json_get_string(line, "username", username, sizeof(username));
    (void)wb_json_get_string(line, "password", password, sizeof(password));
    (void)wb_json_get_string(line, "stream_name", stream_name, sizeof(stream_name));
    (void)wb_json_get_string(line, "stream_description", stream_description, sizeof(stream_description));
    (void)wb_json_get_string(line, "stream_genre", stream_genre, sizeof(stream_genre));
    (void)wb_json_get_string(line, "stream_url", stream_url, sizeof(stream_url));
    (void)wb_json_get_string(line, "dsp_config_path", dsp_config_path, sizeof(dsp_config_path));
    (void)wb_json_get_i64(line, "port", &port);
    (void)wb_json_get_i64(line, "bitrate_kbps", &bitrate_kbps);
    if (wb_icecast_output_configure(
            state,
            output_id,
            codec,
            enabled,
            host,
            (int)port,
            mount,
            username,
            password,
            (int)bitrate_kbps,
            stream_name,
            stream_description,
            stream_genre,
            stream_url,
            public_stream,
            add_year_to_metadata,
            dsp_enabled,
            dsp_config_path,
            error,
            sizeof(error)
        ) != 0) {
        memset(password, 0, sizeof(password));
        return send_context_error(state, fd, request_id, error[0] != '\0' ? error : "invalid native Icecast configuration");
    }
    memset(password, 0, sizeof(password));
    return send_icecast_output_state_reply(state, fd, request_id);
}

static int handle_clear_icecast_output(
    WbEngineState *state, int fd, int64_t request_id, const char *line
) {
    char output_id[WB_NATIVE_OUTPUT_ID_SIZE] = "";
    char error[WB_ICECAST_ERROR_SIZE] = "";
    (void)wb_json_get_string(line, "output_id", output_id, sizeof(output_id));
    if (output_id[0] != '\0') {
        if (wb_icecast_output_clear_stream(state, output_id, error, sizeof(error)) != 0) {
            return send_context_error(state, fd, request_id, error[0] != '\0' ? error : "cannot clear native output");
        }
    } else {
        wb_icecast_output_clear(state);
    }
    return send_icecast_output_state_reply(state, fd, request_id);
}

int wb_engine_send_event_to_fd(
    WbEngineState *state,
    int fd,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
) {
    char escaped_event[WB_EVENT_NAME_SIZE * 2];
    char escaped_station[WB_STATION_KEY_SIZE * 2];
    char escaped_slot[WB_SLOT_TOKEN_SIZE * 2];
    char escaped_path[WB_PATH_SIZE * 2];
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char escaped_session[WB_SESSION_ID_SIZE * 2];
    char escaped_app[WB_APP_VERSION_SIZE * 2];
    char line[65536];
    const WbDeckState empty = {0};
    const WbDeckState *track = deck_state == NULL ? &empty : deck_state;

    snapshot_protocol_identity(
        state, session_id, sizeof(session_id), app_version, sizeof(app_version)
    );
    wb_json_escape(session_id, escaped_session, sizeof(escaped_session));
    wb_json_escape(app_version, escaped_app, sizeof(escaped_app));
    wb_json_escape(event, escaped_event, sizeof(escaped_event));
    wb_json_escape(track->station_key[0] != '\0' ? track->station_key : state->station_key, escaped_station, sizeof(escaped_station));
    wb_json_escape(track->slot_token, escaped_slot, sizeof(escaped_slot));
    wb_json_escape(track->path, escaped_path, sizeof(escaped_path));
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"event\":\"%s\","
        "\"session_id\":\"%s\",\"app_version\":\"%s\","
        "\"native_daemon_version\":\"%s\",\"station_key\":\"%s\","
        "\"queue_id\":%lld,\"slot_token\":\"%s\",\"deck\":\"%c\","
        "\"track_id\":%lld,\"path\":\"%s\",\"payload\":%s}",
        WB_PROTOCOL_VERSION,
        escaped_event,
        escaped_session,
        escaped_app,
        WB_NATIVE_DAEMON_VERSION,
        escaped_station,
        (long long)track->queue_id,
        escaped_slot,
        deck,
        (long long)track->track_id,
        escaped_path,
        payload_json == NULL ? "{}" : payload_json
    );
    return state_send_line(state, fd, line);
}

int wb_engine_send_event(
    WbEngineState *state,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
) {
    int client_fd;
    if (state->event_sink != NULL) {
        return state->event_sink(
            state->event_sink_context, state, event, deck_state, deck, payload_json
        );
    }
    (void)pthread_mutex_lock(&state->lock);
    client_fd = state->client_fd;
    (void)pthread_mutex_unlock(&state->lock);
    if (client_fd < 0) return 0;
    return wb_engine_send_event_to_fd(state, client_fd, event, deck_state, deck, payload_json);
}

void wb_engine_init(WbEngineState *state) {
    memset(state, 0, sizeof(*state));
    (void)pthread_mutex_init(&state->lock, NULL);
    (void)pthread_mutex_init(&state->send_lock, NULL);
    state->active_deck = 'A';
    state->accepting_loads = true;
    state->client_fd = -1;
    (void)wb_audio_probe_init(state);
    (void)wb_audio_analysis_init(state);
    (void)wb_icecast_output_init(state);
    (void)wb_native_timing_init(state);
    (void)wb_diagnostics_init(state);
}

void wb_engine_destroy(WbEngineState *state) {
    /* Stop playback decisions first, but keep the timing condition alive until
     * the analysis workers have exited: a completing analyser wakes the timing
     * worker after publishing its descriptor. */
    (void)pthread_mutex_lock(&state->lock);
    state->running = false;
    state->paused = false;
    state->accepting_loads = false;
    (void)pthread_cond_broadcast(&state->native_timing.cond);
    (void)pthread_mutex_unlock(&state->lock);
    wb_diagnostics_destroy(state);
    wb_audio_analysis_destroy(state);
    wb_native_timing_destroy(state);
    wb_audio_probe_destroy(state);
    wb_icecast_output_destroy(state);
    (void)pthread_mutex_destroy(&state->send_lock);
    (void)pthread_mutex_destroy(&state->lock);
}

void wb_engine_client_connected(WbEngineState *state, int client_fd) {
    (void)pthread_mutex_lock(&state->lock);
    state->client_fd = client_fd;
    (void)pthread_mutex_unlock(&state->lock);
}

void wb_engine_client_disconnected(WbEngineState *state, int client_fd) {
    (void)pthread_mutex_lock(&state->lock);
    if (state->client_fd == client_fd) state->client_fd = -1;
    (void)pthread_mutex_unlock(&state->lock);
}

int wb_engine_send_ready(WbEngineState *state, int client_fd, const char *socket_path) {
    char escaped_path[1024];
    char escaped_ffmpeg_path[WB_PATH_SIZE * 2];
    char escaped_ffmpeg_source[WB_FFMPEG_SOURCE_SIZE * 2];
    char escaped_ffmpeg_version[WB_FFMPEG_VERSION_SIZE * 2];
    char escaped_ffmpeg_build[WB_FFMPEG_BUILD_SIZE * 2];
    char escaped_ffmpeg_error[WB_FFMPEG_ERROR_SIZE * 2];
    char line[16384];
    wb_json_escape(socket_path, escaped_path, sizeof(escaped_path));
    wb_json_escape(state->ffmpeg_path, escaped_ffmpeg_path, sizeof(escaped_ffmpeg_path));
    wb_json_escape(state->ffmpeg_source, escaped_ffmpeg_source, sizeof(escaped_ffmpeg_source));
    wb_json_escape(state->ffmpeg_version, escaped_ffmpeg_version, sizeof(escaped_ffmpeg_version));
    wb_json_escape(state->ffmpeg_runtime_build, escaped_ffmpeg_build, sizeof(escaped_ffmpeg_build));
    wb_json_escape(state->ffmpeg_runtime_error, escaped_ffmpeg_error, sizeof(escaped_ffmpeg_error));
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"event\":\"engine_ready\","
        "\"session_id\":\"\",\"app_version\":\"\","
        "\"native_daemon_version\":\"%s\",\"payload\":{"
        "\"control_only\":false,\"audio_enabled\":false,\"audio_decode_enabled\":true,\"audio_output_enabled\":true,\"native_icecast_output\":true,\"socket_path\":\"%s\","
        "\"ffmpeg_source\":\"%s\",\"ffmpeg_path\":\"%s\",\"ffmpeg_version\":\"%s\","
        "\"ffmpeg_runtime_build\":\"%s\",\"ffmpeg_runtime_valid\":%s,"
        "\"ffmpeg_system_fallback_used\":%s,\"ffmpeg_runtime_error\":\"%s\","
        "\"live_event_sync\":true,\"two_phase_deck_load\":true,\"deck_preload\":true,\"pcm_ring_buffer\":true,\"candidate_slots_per_deck\":2,\"playback_voice_separated\":true,\"candidate_alias_capacity\":32,\"audio_runtime_validation\":true,\"native_pcm_analysis\":true,\"native_timing_owner\":true,\"native_next_track_request\":true,\"embedded_libav\":true,\"ffmpeg_subprocesses\":false,"
        "\"native_daemon_version\":\"%s\"}}",
        WB_PROTOCOL_VERSION,
        WB_NATIVE_DAEMON_VERSION,
        escaped_path,
        escaped_ffmpeg_source,
        escaped_ffmpeg_path,
        escaped_ffmpeg_version,
        escaped_ffmpeg_build,
        state->ffmpeg_runtime_valid ? "true" : "false",
        state->ffmpeg_system_fallback_used ? "true" : "false",
        escaped_ffmpeg_error,
        WB_NATIVE_DAEMON_VERSION
    );
    return wb_send_line(client_fd, line);
}

typedef struct {
    bool running;
    bool activated;
    bool eof;
    bool prebuffer_ready;
    bool final_duration_valid;
    uint64_t decoded_samples;
    uint64_t played_samples;
    int64_t decoded_duration_ms;
    int64_t played_duration_ms;
    int64_t position_ms;
    int64_t final_actual_duration_ms;
    int64_t activation_monotonic_ms;
    int64_t first_sample_monotonic_ms;
    size_t ring_fill;
    size_t ring_capacity;
    char deck;
    char status[WB_AUDIO_STATUS_SIZE];
    char error[WB_AUDIO_ERROR_SIZE];
    WbDeckState track;
    WbDeckState candidate_primary_track;
    size_t alias_count;
    WbDeckState aliases[WB_AUDIO_ALIAS_CAPACITY];
} WbAudioProbeSnapshot;

static void snapshot_audio_probe(const WbAudioDeckProbe *probe, WbAudioProbeSnapshot *snapshot) {
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->running = probe->running;
    snapshot->activated = probe->activated;
    snapshot->eof = probe->eof;
    snapshot->prebuffer_ready = probe->prebuffer_ready;
    snapshot->final_duration_valid = probe->final_duration_valid;
    snapshot->decoded_samples = probe->decoded_samples;
    snapshot->played_samples = probe->played_samples;
    snapshot->decoded_duration_ms = probe->decoded_duration_ms;
    snapshot->played_duration_ms = probe->played_duration_ms;
    snapshot->position_ms = probe->position_ms;
    snapshot->final_actual_duration_ms = probe->final_actual_duration_ms;
    snapshot->activation_monotonic_ms = probe->activation_monotonic_ms;
    snapshot->first_sample_monotonic_ms = probe->first_sample_monotonic_ms;
    snapshot->ring_fill = probe->ring_fill;
    snapshot->ring_capacity = probe->ring_capacity;
    snapshot->deck = probe->deck;
    snapshot->track = probe->track;
    snapshot->candidate_primary_track = probe->track;
    snapshot->alias_count = probe->alias_count;
    if (snapshot->alias_count > WB_AUDIO_ALIAS_CAPACITY) {
        snapshot->alias_count = WB_AUDIO_ALIAS_CAPACITY;
    }
    memcpy(snapshot->aliases, probe->aliases, sizeof(snapshot->aliases));
    copy_text(snapshot->status, sizeof(snapshot->status), probe->status);
    copy_text(snapshot->error, sizeof(snapshot->error), probe->error);
}


static const WbAudioDeckProbe *select_audio_probe_locked(
    const WbEngineState *state,
    char deck,
    const WbDeckState *confirmed
) {
    const WbAudioDeckProbe *first = deck == 'B' ? &state->audio_deck_b : &state->audio_deck_a;
    const WbAudioDeckProbe *second = deck == 'B' ? &state->audio_deck_b_alt : &state->audio_deck_a_alt;
    bool first_exact = probe_identity_track(first, confirmed->queue_id, confirmed->slot_token) != NULL;
    bool second_exact = probe_identity_track(second, confirmed->queue_id, confirmed->slot_token) != NULL;

    if (first_exact && first->activated) return first;
    if (second_exact && second->activated) return second;
    /* An activated slot is the playback voice even when deck_loaded has already
       moved the confirmed preload identity to a newer candidate. */
    if (first->activated) return first;
    if (second->activated) return second;
    if (first_exact) return first;
    if (second_exact) return second;
    return first->candidate_serial >= second->candidate_serial ? first : second;
}


static void snapshot_selected_audio_probe_locked(
    const WbEngineState *state,
    char deck,
    const WbDeckState *confirmed,
    WbAudioProbeSnapshot *snapshot
) {
    const WbAudioDeckProbe *selected = select_audio_probe_locked(state, deck, confirmed);
    const WbDeckState *identity;
    snapshot_audio_probe(selected, snapshot);
    identity = probe_identity_track(selected, confirmed->queue_id, confirmed->slot_token);
    if (!selected->activated && identity != NULL) snapshot->track = *identity;
}

static void build_alias_tokens_json(
    const WbAudioProbeSnapshot *snapshot,
    char *output,
    size_t output_size
) {
    size_t used = 0U;
    size_t index;
    if (output == NULL || output_size < 3U) return;
    output[used++] = '[';
    for (index = 0U; index < snapshot->alias_count; index += 1U) {
        char escaped[WB_SLOT_TOKEN_SIZE * 2];
        int written;
        wb_json_escape(snapshot->aliases[index].slot_token, escaped, sizeof(escaped));
        written = snprintf(
            output + used,
            output_size - used,
            "%s\"%s\"",
            index == 0U ? "" : ",",
            escaped
        );
        if (written < 0 || (size_t)written >= output_size - used) break;
        used += (size_t)written;
    }
    if (used + 2U > output_size) used = output_size - 2U;
    output[used++] = ']';
    output[used] = '\0';
}

static int send_state_reply(WbEngineState *state, int fd, int64_t request_id) {
    char escaped_station_key[WB_STATION_KEY_SIZE * 2];
    char active_slot[WB_SLOT_TOKEN_SIZE * 2];
    char next_slot[WB_SLOT_TOKEN_SIZE * 2];
    char deck_a_slot[WB_SLOT_TOKEN_SIZE * 2];
    char deck_b_slot[WB_SLOT_TOKEN_SIZE * 2];
    char planned_a_slot[WB_SLOT_TOKEN_SIZE * 2];
    char planned_b_slot[WB_SLOT_TOKEN_SIZE * 2];
    char last_event[WB_EVENT_NAME_SIZE * 2];
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char audio_status[WB_AUDIO_STATUS_SIZE * 2];
    char audio_error[WB_AUDIO_ERROR_SIZE * 2];
    char audio_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_path[WB_PATH_SIZE * 2];
    char audio_a_status[WB_AUDIO_STATUS_SIZE * 2];
    char audio_b_status[WB_AUDIO_STATUS_SIZE * 2];
    char audio_a_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_b_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_primary_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_a_primary_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_b_primary_slot[WB_SLOT_TOKEN_SIZE * 2];
    char audio_alias_tokens[WB_AUDIO_ALIAS_CAPACITY * (WB_SLOT_TOKEN_SIZE * 2 + 4)];
    char audio_a_alias_tokens[WB_AUDIO_ALIAS_CAPACITY * (WB_SLOT_TOKEN_SIZE * 2 + 4)];
    char audio_b_alias_tokens[WB_AUDIO_ALIAS_CAPACITY * (WB_SLOT_TOKEN_SIZE * 2 + 4)];
    char ffmpeg_path[WB_PATH_SIZE * 2];
    char escaped_session[WB_SESSION_ID_SIZE * 2];
    char escaped_app[WB_APP_VERSION_SIZE * 2];
    char icecast_state_json[65536];
    char dsp_state[WB_DSP_STATUS_SIZE];
    char escaped_dsp_state[WB_DSP_STATUS_SIZE * 2];
    char active_analysis_source[WB_ANALYSIS_SOURCE_SIZE * 2];
    char active_analysis_error[WB_ANALYSIS_ERROR_SIZE * 2];
    char deck_a_analysis_source[WB_ANALYSIS_SOURCE_SIZE * 2];
    char deck_b_analysis_source[WB_ANALYSIS_SOURCE_SIZE * 2];
    char line[262144];
    WbDeckState active;
    WbDeckState next;
    WbDeckState deck_a;
    WbDeckState deck_b;
    WbDeckState planned_a;
    WbDeckState planned_b;
    WbAudioProbeSnapshot audio_a;
    WbAudioProbeSnapshot audio_b;
    WbAudioProbeSnapshot audio;
    char active_deck;
    bool running;
    bool paused;
    bool accepting_loads;
    bool transitioning;
    bool audio_enabled;
    bool audio_realtime;
    uint64_t live_sync_count;
    uint64_t planned_load_count;
    uint64_t confirmed_load_count;
    uint64_t cancelled_load_count;
    uint64_t late_load_rejected_count;
    uint64_t late_event_ignored_count;
    uint64_t audio_candidate_evicted_count;
    uint64_t audio_candidate_cancelled_count;
    uint64_t audio_runtime_mismatch_count;
    uint64_t audio_runtime_mismatch_total_count;
    uint64_t audio_runtime_mismatch_recovered_count;
    uint64_t native_next_track_request_count;
    uint64_t native_hard_handoff_arm_count;
    uint64_t native_transition_start_count;
    uint64_t native_transition_complete_count;
    int audio_sample_rate;
    int audio_channels;
    int ring_capacity_ms;
    int prebuffer_ms;
    int start_timeout_ms;
    int seek_start_timeout_ms;
    int64_t startup_delay_ms;
    int64_t last_live_event_monotonic_ms;
    int64_t last_live_event_wall_time_unix_ms;

    (void)pthread_mutex_lock(&state->lock);
    active_deck = state->active_deck;
    deck_a = state->deck_a;
    deck_b = state->deck_b;
    planned_a = state->planned_deck_a;
    planned_b = state->planned_deck_b;
    active = *deck_for(state, active_deck);
    next = *deck_for(state, active_deck == 'A' ? 'B' : 'A');
    running = state->running;
    paused = state->paused;
    accepting_loads = state->accepting_loads;
    transitioning = state->transitioning;
    live_sync_count = state->live_sync_count;
    planned_load_count = state->planned_load_count;
    confirmed_load_count = state->confirmed_load_count;
    cancelled_load_count = state->cancelled_load_count;
    late_load_rejected_count = state->late_load_rejected_count;
    late_event_ignored_count = state->late_event_ignored_count;
    audio_candidate_evicted_count = state->audio_candidate_evicted_count;
    audio_candidate_cancelled_count = state->audio_candidate_cancelled_count;
    audio_runtime_mismatch_count = state->audio_runtime_mismatch_count;
    audio_runtime_mismatch_total_count = state->audio_runtime_mismatch_total_count;
    audio_runtime_mismatch_recovered_count = state->audio_runtime_mismatch_recovered_count;
    native_next_track_request_count = state->native_timing.next_track_request_count;
    native_hard_handoff_arm_count = state->native_timing.hard_handoff_arm_count;
    native_transition_start_count = state->native_timing.transition_start_count;
    native_transition_complete_count = state->native_timing.transition_complete_count;
    last_live_event_monotonic_ms = state->last_live_event_monotonic_ms;
    last_live_event_wall_time_unix_ms = state->last_live_event_wall_time_unix_ms;
    audio_enabled = state->audio_probe_enabled;
    audio_realtime = state->audio_probe_realtime;
    audio_sample_rate = state->audio_probe_sample_rate;
    audio_channels = state->audio_probe_channels;
    ring_capacity_ms = state->audio_ring_capacity_ms;
    prebuffer_ms = state->audio_prebuffer_ms;
    start_timeout_ms = state->audio_start_timeout_ms;
    seek_start_timeout_ms = state->audio_seek_start_timeout_ms;
    snapshot_selected_audio_probe_locked(state, 'A', &deck_a, &audio_a);
    snapshot_selected_audio_probe_locked(state, 'B', &deck_b, &audio_b);
    audio = active_deck == 'B' ? audio_b : audio_a;
    copy_text(session_id, sizeof(session_id), state->session_id);
    copy_text(app_version, sizeof(app_version), state->app_version);
    wb_json_escape(state->last_live_event, last_event, sizeof(last_event));
    wb_json_escape(state->ffmpeg_path, ffmpeg_path, sizeof(ffmpeg_path));
    (void)pthread_mutex_unlock(&state->lock);

    startup_delay_ms = (
        audio.first_sample_monotonic_ms > 0 && audio.activation_monotonic_ms > 0
    ) ? audio.first_sample_monotonic_ms - audio.activation_monotonic_ms : 0;

    wb_json_escape(state->station_key, escaped_station_key, sizeof(escaped_station_key));
    wb_json_escape(session_id, escaped_session, sizeof(escaped_session));
    wb_json_escape(app_version, escaped_app, sizeof(escaped_app));
    wb_json_escape(active.slot_token, active_slot, sizeof(active_slot));
    wb_json_escape(next.slot_token, next_slot, sizeof(next_slot));
    wb_json_escape(deck_a.slot_token, deck_a_slot, sizeof(deck_a_slot));
    wb_json_escape(deck_b.slot_token, deck_b_slot, sizeof(deck_b_slot));
    wb_json_escape(planned_a.slot_token, planned_a_slot, sizeof(planned_a_slot));
    wb_json_escape(planned_b.slot_token, planned_b_slot, sizeof(planned_b_slot));
    wb_json_escape(audio.track.slot_token, audio_slot, sizeof(audio_slot));
    wb_json_escape(audio.track.path, audio_path, sizeof(audio_path));
    wb_json_escape(audio.status, audio_status, sizeof(audio_status));
    wb_json_escape(audio.error, audio_error, sizeof(audio_error));
    wb_json_escape(audio_a.status, audio_a_status, sizeof(audio_a_status));
    wb_json_escape(audio_b.status, audio_b_status, sizeof(audio_b_status));
    wb_json_escape(audio_a.track.slot_token, audio_a_slot, sizeof(audio_a_slot));
    wb_json_escape(audio_b.track.slot_token, audio_b_slot, sizeof(audio_b_slot));
    wb_json_escape(audio.candidate_primary_track.slot_token, audio_primary_slot, sizeof(audio_primary_slot));
    wb_json_escape(audio_a.candidate_primary_track.slot_token, audio_a_primary_slot, sizeof(audio_a_primary_slot));
    wb_json_escape(audio_b.candidate_primary_track.slot_token, audio_b_primary_slot, sizeof(audio_b_primary_slot));
    build_alias_tokens_json(&audio, audio_alias_tokens, sizeof(audio_alias_tokens));
    build_alias_tokens_json(&audio_a, audio_a_alias_tokens, sizeof(audio_a_alias_tokens));
    build_alias_tokens_json(&audio_b, audio_b_alias_tokens, sizeof(audio_b_alias_tokens));
    wb_json_escape(active.analysis_source, active_analysis_source, sizeof(active_analysis_source));
    wb_json_escape(active.analysis_error, active_analysis_error, sizeof(active_analysis_error));
    wb_json_escape(deck_a.analysis_source, deck_a_analysis_source, sizeof(deck_a_analysis_source));
    wb_json_escape(deck_b.analysis_source, deck_b_analysis_source, sizeof(deck_b_analysis_source));
    if (wb_icecast_output_state_json(state, icecast_state_json, sizeof(icecast_state_json)) < 0) {
        copy_text(icecast_state_json, sizeof(icecast_state_json), "{\"supported\":false}");
    }
    (void)pthread_mutex_lock(&state->icecast_output.lock);
    if (!state->icecast_output.dsp_enabled) {
        copy_text(dsp_state, sizeof(dsp_state), "bypassed");
    } else if (state->icecast_output.dsp_status[0] != '\0') {
        copy_text(dsp_state, sizeof(dsp_state), state->icecast_output.dsp_status);
    } else if (state->icecast_output.dsp_ready) {
        copy_text(dsp_state, sizeof(dsp_state), "ready");
    } else if (state->icecast_output.dsp_running) {
        copy_text(dsp_state, sizeof(dsp_state), "starting");
    } else {
        copy_text(dsp_state, sizeof(dsp_state), "stopped");
    }
    (void)pthread_mutex_unlock(&state->icecast_output.lock);
    wb_json_escape(dsp_state, escaped_dsp_state, sizeof(escaped_dsp_state));

    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"reply_to\":%lld,\"ok\":true,"
        "\"session_id\":\"%s\",\"app_version\":\"%s\","
        "\"native_daemon_version\":\"%s\",\"state\":{"
        "\"session_id\":\"%s\",\"app_version\":\"%s\","
        "\"native_daemon_version\":\"%s\",\"station_key\":\"%s\","
        "\"running\":%s,\"paused\":%s,\"accepting_loads\":%s,\"active_deck\":\"%c\",\"position_ms\":%lld,"
        "\"queue_id\":%lld,\"slot_token\":\"%s\","
        "\"next_queue_id\":%lld,\"next_slot_token\":\"%s\","
        "\"deck_a_queue_id\":%lld,\"deck_a_slot_token\":\"%s\","
        "\"deck_b_queue_id\":%lld,\"deck_b_slot_token\":\"%s\","
        "\"deck_a_load_pending\":%s,\"deck_a_planned_queue_id\":%lld,"
        "\"deck_a_planned_slot_token\":\"%s\","
        "\"deck_b_load_pending\":%s,\"deck_b_planned_queue_id\":%lld,"
        "\"deck_b_planned_slot_token\":\"%s\","
        "\"transitioning\":%s,\"live_sync_count\":%llu,"
        "\"planned_load_count\":%llu,\"confirmed_load_count\":%llu,"
        "\"cancelled_load_count\":%llu,\"late_load_rejected_count\":%llu,\"late_event_ignored_count\":%llu,\"audio_candidate_evicted_count\":%llu,\"audio_candidate_cancelled_count\":%llu,\"audio_runtime_mismatch_count\":%llu,\"audio_runtime_mismatch_total_count\":%llu,\"audio_runtime_mismatch_recovered_count\":%llu,\"last_live_event\":\"%s\","
        "\"last_live_event_monotonic_ms\":%lld,\"last_live_event_wall_time_unix_ms\":%lld,"
        "\"native_audio_probe_enabled\":%s,\"native_audio_probe_realtime\":%s,"
        "\"native_audio_probe_running\":%s,\"native_audio_probe_eof\":%s,"
        "\"native_audio_probe_status\":\"%s\",\"native_audio_probe_error\":\"%s\","
        "\"native_audio_probe_deck\":\"%c\",\"native_audio_probe_queue_id\":%lld,"
        "\"native_audio_probe_slot_token\":\"%s\",\"native_audio_probe_path\":\"%s\","
        "\"native_audio_probe_candidate_primary_slot_token\":\"%s\","
        "\"native_audio_probe_candidate_alias_count\":%zu,\"native_audio_probe_candidate_alias_tokens\":%s,"
        "\"native_audio_probe_cue_in_ms\":%lld,\"native_audio_probe_cue_out_ms\":%lld,"
        "\"native_audio_probe_audio_start_ms\":%lld,\"native_audio_probe_play_start_ms\":%lld,"
        "\"native_audio_probe_transition_at_ms\":%lld,\"native_audio_probe_effective_end_ms\":%lld,"
        "\"native_audio_probe_source_end_ms\":%lld,"
        "\"native_audio_probe_position_ms\":%lld,"
        "\"native_audio_probe_decoded_duration_ms\":%lld,"
        "\"native_audio_probe_played_duration_ms\":%lld,"
        "\"native_audio_probe_actual_duration_ms\":%lld,"
        "\"native_audio_probe_actual_duration_final\":%s,"
        "\"native_audio_probe_decoded_samples\":%llu,\"native_audio_probe_played_samples\":%llu,"
        "\"native_audio_probe_prebuffer_ready\":%s,"
        "\"native_audio_probe_ring_buffer_bytes\":%zu,"
        "\"native_audio_probe_ring_capacity_bytes\":%zu,"
        "\"native_audio_probe_startup_delay_ms\":%lld,"
        "\"native_audio_probe_sample_rate\":%d,"
        "\"native_audio_probe_channels\":%d,\"native_audio_probe_ffmpeg\":\"%s\","
        "\"native_audio_ring_capacity_ms\":%d,\"native_audio_prebuffer_ms\":%d,"
        "\"native_audio_start_timeout_ms\":%d,\"native_audio_seek_start_timeout_ms\":%d,"
        "\"native_audio_deck_a_status\":\"%s\",\"native_audio_deck_a_running\":%s,"
        "\"native_audio_deck_a_activated\":%s,\"native_audio_deck_a_prebuffer_ready\":%s,"
        "\"native_audio_deck_a_queue_id\":%lld,\"native_audio_deck_a_slot_token\":\"%s\","
        "\"native_audio_deck_a_candidate_primary_slot_token\":\"%s\","
        "\"native_audio_deck_a_candidate_alias_count\":%zu,\"native_audio_deck_a_candidate_alias_tokens\":%s,"
        "\"native_audio_deck_a_ring_buffer_bytes\":%zu,"
        "\"native_audio_deck_a_decoded_duration_ms\":%lld,\"native_audio_deck_a_played_duration_ms\":%lld,"
        "\"native_audio_deck_a_final_actual_duration_ms\":%lld,"
        "\"native_audio_deck_b_status\":\"%s\",\"native_audio_deck_b_running\":%s,"
        "\"native_audio_deck_b_activated\":%s,\"native_audio_deck_b_prebuffer_ready\":%s,"
        "\"native_audio_deck_b_queue_id\":%lld,\"native_audio_deck_b_slot_token\":\"%s\","
        "\"native_audio_deck_b_candidate_primary_slot_token\":\"%s\","
        "\"native_audio_deck_b_candidate_alias_count\":%zu,\"native_audio_deck_b_candidate_alias_tokens\":%s,"
        "\"native_audio_deck_b_ring_buffer_bytes\":%zu,"
        "\"native_audio_deck_b_decoded_duration_ms\":%lld,\"native_audio_deck_b_played_duration_ms\":%lld,"
        "\"native_audio_deck_b_final_actual_duration_ms\":%lld,"
        "\"native_timing_owner\":true,\"native_pcm_analysis\":true,"
        "\"native_analysis_requested\":%s,\"native_analysis_ready\":%s,"
        "\"native_analysis_failed\":%s,\"native_analysis_source\":\"%s\","
        "\"native_analysis_error\":\"%s\",\"native_analysis_audio_start_ms\":%lld,"
        "\"native_analysis_transition_at_ms\":%lld,\"native_analysis_effective_end_ms\":%lld,"
        "\"native_analysis_source_end_ms\":%lld,\"native_analysis_short_no_crossfade\":%s,"
        "\"native_analysis_ignored_artifact_ms\":%lld,\"native_analysis_trailing_silence_ms\":%lld,"
        "\"native_deck_a_analysis_ready\":%s,\"native_deck_a_analysis_source\":\"%s\","
        "\"native_deck_a_analysis_transition_at_ms\":%lld,\"native_deck_a_analysis_effective_end_ms\":%lld,"
        "\"native_deck_a_short_no_crossfade\":%s,"
        "\"native_deck_b_analysis_ready\":%s,\"native_deck_b_analysis_source\":\"%s\","
        "\"native_deck_b_analysis_transition_at_ms\":%lld,\"native_deck_b_analysis_effective_end_ms\":%lld,"
        "\"native_deck_b_short_no_crossfade\":%s,"
        "\"native_next_track_request_count\":%llu,\"native_hard_handoff_arm_count\":%llu,"
        "\"native_transition_start_count\":%llu,\"native_transition_complete_count\":%llu,"
        "\"dsp_state\":\"%s\",\"icecast_state\":%s,"
        "\"control_only\":false,\"audio_enabled\":false,"
        "\"audio_decode_enabled\":%s,\"audio_output_enabled\":true,\"native_icecast_output\":true,"
        "\"embedded_libav\":true,\"ffmpeg_subprocesses\":false,"
        "\"deck_preload\":true,\"pcm_ring_buffer\":true,\"candidate_slots_per_deck\":2,\"playback_voice_separated\":true,\"candidate_alias_capacity\":32,\"audio_runtime_validation\":true,\"two_phase_deck_load\":true,\"native_pcm_analysis\":true,\"native_timing_owner\":true,\"native_next_track_request\":true}}",
        WB_PROTOCOL_VERSION,
        (long long)request_id,
        escaped_session,
        escaped_app,
        WB_NATIVE_DAEMON_VERSION,
        escaped_session,
        escaped_app,
        WB_NATIVE_DAEMON_VERSION,
        escaped_station_key,
        running ? "true" : "false",
        paused ? "true" : "false",
        accepting_loads ? "true" : "false",
        active_deck,
        (long long)audio.position_ms,
        (long long)active.queue_id,
        active_slot,
        (long long)next.queue_id,
        next_slot,
        (long long)deck_a.queue_id,
        deck_a_slot,
        (long long)deck_b.queue_id,
        deck_b_slot,
        planned_a.loaded ? "true" : "false",
        (long long)planned_a.queue_id,
        planned_a_slot,
        planned_b.loaded ? "true" : "false",
        (long long)planned_b.queue_id,
        planned_b_slot,
        transitioning ? "true" : "false",
        (unsigned long long)live_sync_count,
        (unsigned long long)planned_load_count,
        (unsigned long long)confirmed_load_count,
        (unsigned long long)cancelled_load_count,
        (unsigned long long)late_load_rejected_count,
        (unsigned long long)late_event_ignored_count,
        (unsigned long long)audio_candidate_evicted_count,
        (unsigned long long)audio_candidate_cancelled_count,
        (unsigned long long)audio_runtime_mismatch_count,
        (unsigned long long)audio_runtime_mismatch_total_count,
        (unsigned long long)audio_runtime_mismatch_recovered_count,
        last_event,
        (long long)last_live_event_monotonic_ms,
        (long long)last_live_event_wall_time_unix_ms,
        audio_enabled ? "true" : "false",
        audio_realtime ? "true" : "false",
        (audio.running && audio.activated) ? "true" : "false",
        audio.eof ? "true" : "false",
        audio_status,
        audio_error,
        audio.deck ? audio.deck : active_deck,
        (long long)audio.track.queue_id,
        audio_slot,
        audio_path,
        audio_primary_slot,
        audio.alias_count,
        audio_alias_tokens,
        (long long)audio.track.cue_in_ms,
        (long long)audio.track.cue_out_ms,
        (long long)audio.track.audio_start_ms,
        (long long)audio.track.play_start_ms,
        (long long)audio.track.transition_at_ms,
        (long long)audio.track.effective_end_ms,
        (long long)audio.track.source_end_ms,
        (long long)audio.position_ms,
        (long long)audio.decoded_duration_ms,
        (long long)audio.played_duration_ms,
        (long long)audio.final_actual_duration_ms,
        audio.final_duration_valid ? "true" : "false",
        (unsigned long long)audio.decoded_samples,
        (unsigned long long)audio.played_samples,
        audio.prebuffer_ready ? "true" : "false",
        audio.ring_fill,
        audio.ring_capacity,
        (long long)startup_delay_ms,
        audio_sample_rate,
        audio_channels,
        ffmpeg_path,
        ring_capacity_ms,
        prebuffer_ms,
        start_timeout_ms,
        seek_start_timeout_ms,
        audio_a_status,
        audio_a.running ? "true" : "false",
        audio_a.activated ? "true" : "false",
        audio_a.prebuffer_ready ? "true" : "false",
        (long long)audio_a.track.queue_id,
        audio_a_slot,
        audio_a_primary_slot,
        audio_a.alias_count,
        audio_a_alias_tokens,
        audio_a.ring_fill,
        (long long)audio_a.decoded_duration_ms,
        (long long)audio_a.played_duration_ms,
        (long long)audio_a.final_actual_duration_ms,
        audio_b_status,
        audio_b.running ? "true" : "false",
        audio_b.activated ? "true" : "false",
        audio_b.prebuffer_ready ? "true" : "false",
        (long long)audio_b.track.queue_id,
        audio_b_slot,
        audio_b_primary_slot,
        audio_b.alias_count,
        audio_b_alias_tokens,
        audio_b.ring_fill,
        (long long)audio_b.decoded_duration_ms,
        (long long)audio_b.played_duration_ms,
        (long long)audio_b.final_actual_duration_ms,
        active.analysis_requested ? "true" : "false",
        active.analysis_ready ? "true" : "false",
        active.analysis_failed ? "true" : "false",
        active_analysis_source,
        active_analysis_error,
        (long long)active.audio_start_ms,
        (long long)active.transition_at_ms,
        (long long)active.effective_end_ms,
        (long long)active.source_end_ms,
        active.short_no_crossfade ? "true" : "false",
        (long long)active.analysis_ignored_artifact_ms,
        (long long)active.analysis_trailing_silence_ms,
        deck_a.analysis_ready ? "true" : "false",
        deck_a_analysis_source,
        (long long)deck_a.transition_at_ms,
        (long long)deck_a.effective_end_ms,
        deck_a.short_no_crossfade ? "true" : "false",
        deck_b.analysis_ready ? "true" : "false",
        deck_b_analysis_source,
        (long long)deck_b.transition_at_ms,
        (long long)deck_b.effective_end_ms,
        deck_b.short_no_crossfade ? "true" : "false",
        (unsigned long long)native_next_track_request_count,
        (unsigned long long)native_hard_handoff_arm_count,
        (unsigned long long)native_transition_start_count,
        (unsigned long long)native_transition_complete_count,
        escaped_dsp_state,
        icecast_state_json,
        audio_enabled ? "true" : "false"
    );
    return state_send_line(state, fd, line);
}

static int handle_get_state(WbEngineState *state, int fd, int64_t request_id) {
    return send_state_reply(state, fd, request_id);
}

static int handle_load(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char deck_text[8] = "A";
    char track_json[WB_PATH_SIZE + 2048] = "";
    char options_json[1024] = "";
    char slot_token[WB_SLOT_TOKEN_SIZE] = "";
    char station_key[WB_STATION_KEY_SIZE] = "";
    char path[WB_PATH_SIZE] = "";
    char artist[WB_TRACK_ARTIST_SIZE] = "";
    char title[WB_TRACK_TITLE_SIZE] = "";
    char year[WB_TRACK_YEAR_SIZE] = "";
    int64_t queue_id = 0;
    int64_t track_id = 0;
    int64_t cue_in_ms = 0;
    int64_t cue_out_ms = 0;
    int64_t audio_start_ms = 0;
    int64_t play_start_ms = 0;
    int64_t transition_at_ms = 0;
    int64_t effective_end_ms = 0;
    int64_t source_end_ms = 0;
    int64_t fade_in_ms = 0;
    int64_t fade_out_ms = 0;
    int64_t analysis_window_ms = 10;
    int64_t analysis_sustain_ms = 30;
    int64_t analysis_artifact_max_ms = 300;
    int64_t analysis_artifact_silence_ms = 250;
    int64_t no_crossfade_max_duration_ms = 65000;
    int64_t crossfade_fallback_ms = 3000;
    int64_t crossfade_min_ms = 100;
    int64_t crossfade_max_ms = 6000;
    double gap_start_threshold_dbfs = -20.0;
    double gap_end_threshold_dbfs = -24.0;
    double crossfade_trigger_relative_db = -7.0;
    bool analysis_requested = false;
    bool manual_timing = false;
    bool hard_clean = false;
    bool short_no_crossfade = false;
    bool stream_source = false;
    bool stream_infinite = false;
    int64_t stream_duration_ms = 0;
    bool clear_slot = false;
    bool deduplicated = false;
    bool superseded = false;
    bool rejected_after_stop = false;
    char deck;
    WbDeckState snapshot = {0};
    WbDeckState superseded_snapshot = {0};
    char result[512];

    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    deck = normalized_deck(deck_text);
    if (!wb_json_get_object(line, "track", track_json, sizeof(track_json))) {
        return send_context_error(state, fd, request_id, "missing or invalid track object");
    }
    (void)wb_json_get_string(track_json, "slot_token", slot_token, sizeof(slot_token));
    (void)wb_json_get_string(track_json, "station_key", station_key, sizeof(station_key));
    (void)wb_json_get_string(track_json, "path", path, sizeof(path));
    (void)wb_json_get_string(track_json, "artist", artist, sizeof(artist));
    (void)wb_json_get_string(track_json, "title", title, sizeof(title));
    (void)wb_json_get_string(track_json, "year", year, sizeof(year));
    (void)wb_json_get_i64(track_json, "queue_id", &queue_id);
    (void)wb_json_get_i64(track_json, "track_id", &track_id);
    (void)wb_json_get_i64(track_json, "cue_in_ms", &cue_in_ms);
    (void)wb_json_get_i64(track_json, "cue_out_ms", &cue_out_ms);
    (void)wb_json_get_i64(track_json, "audio_start_ms", &audio_start_ms);
    (void)wb_json_get_i64(track_json, "play_start_ms", &play_start_ms);
    (void)wb_json_get_i64(track_json, "transition_at_ms", &transition_at_ms);
    (void)wb_json_get_i64(track_json, "effective_end_ms", &effective_end_ms);
    (void)wb_json_get_i64(track_json, "source_end_ms", &source_end_ms);
    (void)wb_json_get_i64(track_json, "fade_in_ms", &fade_in_ms);
    (void)wb_json_get_i64(track_json, "fade_out_ms", &fade_out_ms);
    (void)wb_json_get_bool(track_json, "analysis_requested", &analysis_requested);
    (void)wb_json_get_bool(track_json, "manual_timing", &manual_timing);
    (void)wb_json_get_bool(track_json, "hard_clean", &hard_clean);
    (void)wb_json_get_bool(track_json, "short_no_crossfade", &short_no_crossfade);
    (void)wb_json_get_bool(track_json, "stream_source", &stream_source);
    (void)wb_json_get_bool(track_json, "stream_infinite", &stream_infinite);
    (void)wb_json_get_i64(track_json, "stream_duration_ms", &stream_duration_ms);
    (void)wb_json_get_i64(track_json, "analysis_window_ms", &analysis_window_ms);
    (void)wb_json_get_i64(track_json, "analysis_sustain_ms", &analysis_sustain_ms);
    (void)wb_json_get_i64(track_json, "analysis_artifact_max_ms", &analysis_artifact_max_ms);
    (void)wb_json_get_i64(track_json, "analysis_artifact_silence_ms", &analysis_artifact_silence_ms);
    (void)wb_json_get_i64(track_json, "no_crossfade_max_duration_ms", &no_crossfade_max_duration_ms);
    (void)wb_json_get_i64(track_json, "crossfade_fallback_ms", &crossfade_fallback_ms);
    (void)wb_json_get_i64(track_json, "crossfade_min_ms", &crossfade_min_ms);
    (void)wb_json_get_i64(track_json, "crossfade_max_ms", &crossfade_max_ms);
    (void)wb_json_get_double(track_json, "gap_start_threshold_dbfs", &gap_start_threshold_dbfs);
    (void)wb_json_get_double(track_json, "gap_end_threshold_dbfs", &gap_end_threshold_dbfs);
    (void)wb_json_get_double(track_json, "crossfade_trigger_relative_db", &crossfade_trigger_relative_db);
    if (wb_json_get_object(line, "options", options_json, sizeof(options_json))) {
        (void)wb_json_get_bool(options_json, "clear_slot", &clear_slot);
    }
    if (queue_id <= 0 && slot_token[0] == '\0' && path[0] == '\0') {
        return send_context_error(state, fd, request_id, "track object has no usable identity");
    }

    (void)pthread_mutex_lock(&state->lock);
    if (!state->accepting_loads) {
        state->late_load_rejected_count += 1U;
        rejected_after_stop = true;
    } else {
        WbDeckState *planned = planned_deck_for(state, deck);
        WbDeckState *confirmed = deck_for(state, deck);
        if (identity_matches(confirmed, queue_id, slot_token)) {
            snapshot = *confirmed;
            deduplicated = true;
        } else {
            if (planned->loaded) {
                superseded_snapshot = *planned;
                state->cancelled_load_count += 1U;
                superseded = true;
            }
            clear_deck_state(planned);
            set_deck_identity(planned, queue_id, track_id, slot_token, station_key, path);
            set_deck_metadata(planned, artist, title, year);
            planned->cue_in_ms = cue_in_ms < 0 ? 0 : cue_in_ms;
            planned->cue_out_ms = cue_out_ms < 0 ? 0 : cue_out_ms;
            planned->audio_start_ms = audio_start_ms < 0 ? 0 : audio_start_ms;
            planned->play_start_ms = play_start_ms < 0 ? 0 : play_start_ms;
            planned->transition_at_ms = transition_at_ms < 0 ? 0 : transition_at_ms;
            planned->effective_end_ms = effective_end_ms < 0 ? 0 : effective_end_ms;
            planned->source_end_ms = source_end_ms < 0 ? 0 : source_end_ms;
            planned->fade_in_ms = fade_in_ms < 0 ? 0 : fade_in_ms;
            planned->fade_out_ms = fade_out_ms < 0 ? 0 : fade_out_ms;
            planned->analysis_requested = analysis_requested && !manual_timing;
            planned->analysis_ready = !planned->analysis_requested;
            planned->analysis_failed = false;
            planned->manual_timing = manual_timing;
            planned->hard_clean = hard_clean;
            planned->short_no_crossfade = short_no_crossfade;
            planned->stream_source = stream_source;
            planned->stream_infinite = stream_source && stream_infinite;
            planned->stream_duration_ms = stream_duration_ms > 0 ? stream_duration_ms : 0;
            if (planned->stream_source) {
                planned->analysis_requested = false;
                planned->analysis_ready = true;
                planned->manual_timing = true;
                if (planned->stream_duration_ms > 0) {
                    planned->transition_at_ms = planned->stream_duration_ms;
                    planned->effective_end_ms = planned->stream_duration_ms;
                    planned->source_end_ms = planned->stream_duration_ms;
                    planned->cue_out_ms = planned->stream_duration_ms;
                    planned->hard_clean = true;
                } else {
                    planned->transition_at_ms = 0;
                    planned->effective_end_ms = 0;
                    planned->source_end_ms = 0;
                    planned->cue_out_ms = 0;
                }
            }
            planned->analysis_window_ms = analysis_window_ms > 0 ? analysis_window_ms : 10;
            planned->analysis_sustain_ms = analysis_sustain_ms > 0 ? analysis_sustain_ms : 30;
            planned->analysis_artifact_max_ms = analysis_artifact_max_ms > 0 ? analysis_artifact_max_ms : 300;
            planned->analysis_artifact_silence_ms = analysis_artifact_silence_ms > 0 ? analysis_artifact_silence_ms : 250;
            planned->no_crossfade_max_duration_ms = no_crossfade_max_duration_ms > 0 ? no_crossfade_max_duration_ms : 65000;
            planned->crossfade_fallback_ms = crossfade_fallback_ms > 0 ? crossfade_fallback_ms : 3000;
            planned->crossfade_min_ms = crossfade_min_ms > 0 ? crossfade_min_ms : 100;
            planned->crossfade_max_ms = crossfade_max_ms > 0 ? crossfade_max_ms : 6000;
            planned->gap_start_threshold_dbfs = gap_start_threshold_dbfs;
            planned->gap_end_threshold_dbfs = gap_end_threshold_dbfs;
            planned->crossfade_trigger_relative_db = crossfade_trigger_relative_db;
            copy_text(
                planned->analysis_source, sizeof(planned->analysis_source),
                planned->analysis_requested ? "native_pcm_runtime_pending" : (manual_timing ? "manual_override" : "native_analysis_skipped")
            );
            normalize_deck_timing(planned);
            snapshot = *planned;
            *confirmed = *planned;
            clear_deck_state(planned);
            state->planned_load_count += 1U;
            state->confirmed_load_count += 1U;
        }
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (rejected_after_stop) {
        return send_context_reply(
            state, fd, request_id,
            "{\"accepted\":false,\"control_only\":false,\"load_state\":\"rejected\",\"reason\":\"engine_stopped\"}"
        );
    }

    (void)snprintf(
        result, sizeof(result),
        "{\"accepted\":true,\"control_only\":false,\"load_state\":\"confirmed\","
        "\"deduplicated\":%s,\"clear_slot_requested\":%s}",
        deduplicated ? "true" : "false",
        clear_slot ? "true" : "false"
    );
    if (send_context_reply(state, fd, request_id, result) != 0) return -1;
    if (deduplicated) return 0;

    if (snapshot.analysis_requested && !snapshot.manual_timing) {
        wb_audio_analysis_schedule(state, deck, &snapshot);
    } else {
        wb_audio_probe_prepare_deck(state, deck, &snapshot);
    }
    wb_native_timing_wake(state);
    if (superseded && wb_engine_send_event_to_fd(
            state, fd, "deck_load_cancelled", &superseded_snapshot, deck,
            "{\"control_only\":false,\"audio_enabled\":true,"
            "\"source\":\"superseded_by_new_load\"}"
        ) != 0) {
        return -1;
    }
    return wb_engine_send_event_to_fd(
        state, fd, "deck_loaded", &snapshot, deck,
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"source\":\"native_load_command\",\"confirmed\":true}"
    );
}

static int handle_cancel_load(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char deck_text[8] = "A";
    char slot_token[WB_SLOT_TOKEN_SIZE] = "";
    char reason[WB_EVENT_NAME_SIZE] = "cancelled";
    char escaped_reason[WB_EVENT_NAME_SIZE * 2];
    int64_t queue_id = 0;
    char deck;
    bool cancelled = false;
    WbDeckState snapshot = {0};
    char result[256];
    char payload[512];

    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    (void)wb_json_get_string(line, "slot_token", slot_token, sizeof(slot_token));
    (void)wb_json_get_string(line, "reason", reason, sizeof(reason));
    (void)wb_json_get_i64(line, "queue_id", &queue_id);
    deck = normalized_deck(deck_text);

    (void)pthread_mutex_lock(&state->lock);
    WbDeckState *planned = planned_deck_for(state, deck);
    if (identity_matches(planned, queue_id, slot_token)) {
        snapshot = *planned;
        clear_deck_state(planned);
        state->cancelled_load_count += 1U;
        cancelled = true;
    }
    (void)pthread_mutex_unlock(&state->lock);

    (void)snprintf(
        result,
        sizeof(result),
        "{\"accepted\":true,\"cancelled\":%s,\"control_only\":true}",
        cancelled ? "true" : "false"
    );
    if (send_context_reply(state, fd, request_id, result) != 0) return -1;
    if (!cancelled) return 0;
    wb_json_escape(reason, escaped_reason, sizeof(escaped_reason));
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"control_only\":true,\"audio_enabled\":false,\"source\":\"%s\"}",
        escaped_reason
    );
    return wb_engine_send_event_to_fd(state, fd, "deck_load_cancelled", &snapshot, deck, payload);
}

static bool wait_for_analysis_ready(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    WbDeckState *ready_track
) {
    if (track == NULL || !track->loaded) return false;
    if (!track->analysis_requested || track->analysis_ready || track->manual_timing) {
        if (ready_track != NULL) *ready_track = *track;
        return true;
    }
    return wb_audio_analysis_wait_ready(
        state, deck, track->queue_id, track->slot_token,
        state->audio_analysis_timeout_ms > 0 ? state->audio_analysis_timeout_ms : 10000,
        ready_track
    );
}

static int handle_select(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char deck_text[8] = "A";
    char deck;
    char old_deck = '\0';
    char release_deck = '\0';
    bool was_transitioning = false;
    WbDeckState target = {0};
    WbDeckState outgoing = {0};
    char payload[384];

    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    deck = normalized_deck(deck_text);
    (void)pthread_mutex_lock(&state->lock);
    target = *deck_for(state, deck);
    (void)pthread_mutex_unlock(&state->lock);
    if (!target.loaded) {
        return send_context_error(state, fd, request_id, "selected deck is not loaded");
    }
    if (!wait_for_analysis_ready(state, deck, &target, &target)) {
        return send_context_error(state, fd, request_id, "selected deck analysis is not ready");
    }
    (void)pthread_mutex_lock(&state->lock);
    old_deck = state->active_deck;
    was_transitioning = state->transitioning;
    release_deck = was_transitioning ? (deck == 'A' ? 'B' : 'A') : old_deck;
    if (release_deck == 'A' || release_deck == 'B') {
        outgoing = *deck_for(state, release_deck);
    }
    if (!identity_matches(deck_for(state, deck), target.queue_id, target.slot_token)) {
        (void)pthread_mutex_unlock(&state->lock);
        return send_context_error(state, fd, request_id, "selected deck changed while analysis was pending");
    }
    deck_for(state, deck)->playback_started = true;
    deck_for(state, deck)->consumed = false;
    deck_for(state, deck)->terminal = false;
    target = *deck_for(state, deck);
    if ((release_deck == 'A' || release_deck == 'B') && release_deck != deck && outgoing.loaded) {
        deck_for(state, release_deck)->consumed = true;
        deck_for(state, release_deck)->terminal = true;
    }
    state->active_deck = deck;
    state->transitioning = false;
    (void)pthread_mutex_unlock(&state->lock);

    wb_icecast_output_activate_track(state, deck, &target);
    wb_audio_probe_activate_deck(state, deck, &target, true, monotonic_ms());
    wb_icecast_output_transition_finished(state, deck);

    if (send_context_reply(state, fd, request_id, "{\"accepted\":true,\"control_only\":false}") != 0) {
        return -1;
    }
    if (!was_transitioning) {
        if (wb_engine_send_event_to_fd(
                state, fd, "track_started", &target, deck,
                "{\"control_only\":false,\"audio_enabled\":true,"
                "\"source\":\"native_select_command\",\"hard_handoff\":true}"
            ) != 0) {
            return -1;
        }
    } else {
        (void)snprintf(
            payload, sizeof(payload),
            "{\"control_only\":false,\"audio_enabled\":true,"
            "\"source\":\"native_transition_completion\",\"from_deck\":\"%c\"}",
            release_deck
        );
        if (wb_engine_send_event_to_fd(state, fd, "transition_finished", &target, deck, payload) != 0) {
            return -1;
        }
    }

    if ((release_deck == 'A' || release_deck == 'B') && release_deck != deck && outgoing.loaded) {
        wb_audio_probe_stop_track(state, release_deck, outgoing.queue_id, outgoing.slot_token, "track_ended");
        wb_icecast_output_stop_track(state, release_deck, outgoing.queue_id, outgoing.slot_token);
        if (wb_engine_send_event_to_fd(
                state, fd, "track_ended", &outgoing, release_deck,
                "{\"control_only\":false,\"audio_enabled\":true,"
                "\"source\":\"native_select_release\"}"
            ) != 0) {
            return -1;
        }
    }
    return 0;
}

static int handle_hard_handoff(
    WbEngineState *state,
    int fd,
    int64_t request_id,
    const char *line
) {
    char deck_text[8] = "A";
    char deck;
    char from_deck;
    WbDeckState target = {0};
    WbDeckState outgoing = {0};
    int64_t position_ms = 0;
    int64_t boundary_ms;
    int64_t remaining_ms;
    int64_t switch_at_ms;
    int64_t output_buffered_ms = 0;
    int64_t now_ms;
    unsigned char primed_pcm[WB_HARD_HANDOFF_PRIME_BYTES];
    size_t primed_bytes;
    char error[WB_ICECAST_ERROR_SIZE] = "";
    char result[512];
    char payload[640];

    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    deck = normalized_deck(deck_text);
    (void)pthread_mutex_lock(&state->lock);
    from_deck = state->active_deck;
    target = *deck_for(state, deck);
    if (from_deck == 'A' || from_deck == 'B') {
        outgoing = *deck_for(state, from_deck);
    }
    if (
        !state->running || state->paused || state->transitioning
        || (from_deck != 'A' && from_deck != 'B') || from_deck == deck
        || !outgoing.loaded || !target.loaded
    ) {
        (void)pthread_mutex_unlock(&state->lock);
        return send_context_error(state, fd, request_id, "hard handoff state is not eligible");
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (!wait_for_analysis_ready(state, from_deck, &outgoing, &outgoing)) {
        return send_context_error(state, fd, request_id, "active deck analysis is not ready");
    }
    if (!wait_for_analysis_ready(state, deck, &target, &target)) {
        return send_context_error(state, fd, request_id, "target deck analysis is not ready");
    }
    if (!wb_audio_probe_get_position_ms(state, from_deck, &outgoing, &position_ms)) {
        return send_context_error(state, fd, request_id, "active deck position is unavailable");
    }
    boundary_ms = outgoing.effective_end_ms > 0
        ? outgoing.effective_end_ms
        : outgoing.source_end_ms;
    if (boundary_ms <= 0) {
        return send_context_error(state, fd, request_id, "active deck has no hard handoff boundary");
    }
    remaining_ms = boundary_ms - position_ms;
    if (remaining_ms < 0) remaining_ms = 0;
    if (remaining_ms > 5000) {
        return send_context_error(state, fd, request_id, "hard handoff requested too early");
    }
    (void)wb_icecast_output_get_deck_buffered_ms(
        state, from_deck, &outgoing, &output_buffered_ms
    );
    now_ms = monotonic_ms();
    switch_at_ms = now_ms + remaining_ms + output_buffered_ms;
    primed_bytes = wb_audio_probe_prime_deck(
        state,
        deck,
        &target,
        primed_pcm,
        sizeof(primed_pcm),
        switch_at_ms
    );
    if (primed_bytes == 0U) {
        return send_context_error(state, fd, request_id, "target deck is not ready for hard handoff");
    }
    if (wb_icecast_output_schedule_hard_handoff(
            state,
            from_deck,
            deck,
            &outgoing,
            &target,
            switch_at_ms,
            primed_pcm,
            primed_bytes,
            error,
            sizeof(error)
        ) != 0) {
        wb_audio_probe_stop_track(
            state, deck, target.queue_id, target.slot_token, "hard_handoff_arm_failed"
        );
        return send_context_error(
            state, fd, request_id, error[0] != '\0' ? error : "hard handoff arm failed"
        );
    }

    (void)snprintf(
        result,
        sizeof(result),
        "{\"accepted\":true,\"control_only\":false,\"hard_handoff_armed\":true,"
        "\"from_deck\":\"%c\",\"to_deck\":\"%c\","
        "\"position_ms\":%lld,\"boundary_ms\":%lld,\"remaining_ms\":%lld,"
        "\"outgoing_buffered_ms\":%lld,\"handoff_at_monotonic_ms\":%lld,"
        "\"primed_bytes\":%zu}",
        from_deck,
        deck,
        (long long)position_ms,
        (long long)boundary_ms,
        (long long)remaining_ms,
        (long long)output_buffered_ms,
        (long long)switch_at_ms,
        primed_bytes
    );
    if (send_context_reply(state, fd, request_id, result) != 0) return -1;
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"source\":\"native_hard_handoff_command\",\"from_deck\":\"%c\","
        "\"position_ms\":%lld,\"boundary_ms\":%lld,\"remaining_ms\":%lld,"
        "\"outgoing_buffered_ms\":%lld,\"handoff_at_monotonic_ms\":%lld,"
        "\"primed_bytes\":%zu}",
        from_deck,
        (long long)position_ms,
        (long long)boundary_ms,
        (long long)remaining_ms,
        (long long)output_buffered_ms,
        (long long)switch_at_ms,
        primed_bytes
    );
    return wb_engine_send_event_to_fd(
        state, fd, "native_hard_handoff_armed", &target, deck, payload
    );
}

static int handle_transition(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char deck_text[8] = "A";
    char deck;
    char from_deck;
    double duration = 0.0;
    WbDeckState target = {0};
    char payload[512];
    int64_t fade_ms;

    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    (void)wb_json_get_double(line, "duration", &duration);
    if (duration < 0.0) duration = 0.0;
    deck = normalized_deck(deck_text);
    fade_ms = (int64_t)(duration * 1000.0);

    (void)pthread_mutex_lock(&state->lock);
    from_deck = state->active_deck;
    target = *deck_for(state, deck);
    (void)pthread_mutex_unlock(&state->lock);
    if (!target.loaded) {
        return send_context_error(state, fd, request_id, "transition target deck is not loaded");
    }
    if (!wait_for_analysis_ready(state, deck, &target, &target)) {
        return send_context_error(state, fd, request_id, "transition target analysis is not ready");
    }
    (void)pthread_mutex_lock(&state->lock);
    if (!identity_matches(deck_for(state, deck), target.queue_id, target.slot_token)) {
        (void)pthread_mutex_unlock(&state->lock);
        return send_context_error(state, fd, request_id, "transition target changed while analysis was pending");
    }
    deck_for(state, deck)->playback_started = true;
    deck_for(state, deck)->consumed = false;
    deck_for(state, deck)->terminal = false;
    target = *deck_for(state, deck);
    state->active_deck = deck;
    state->transitioning = true;
    (void)pthread_mutex_unlock(&state->lock);

    wb_icecast_output_transition_started(
        state,
        from_deck,
        deck,
        monotonic_ms(),
        monotonic_ms(),
        fade_ms + 50,
        fade_ms,
        20,
        50
    );
    wb_icecast_output_activate_track(state, deck, &target);
    wb_audio_probe_activate_deck(state, deck, &target, true, monotonic_ms());

    if (send_context_reply(state, fd, request_id, "{\"accepted\":true,\"control_only\":false}") != 0) {
        return -1;
    }
    (void)snprintf(
        payload, sizeof(payload),
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"requested_duration\":%.6f,\"fade_seconds\":%.6f,"
        "\"fade_out_duration_ms\":%lld,\"release_duration_ms\":%lld,"
        "\"entry_ramp_ms\":20,\"silence_hold_ms\":50,\"from_deck\":\"%c\","
        "\"source\":\"native_transition_command\"}",
        duration,
        duration,
        (long long)fade_ms,
        (long long)(fade_ms + 50),
        from_deck
    );
    if (wb_engine_send_event_to_fd(state, fd, "transition_started", &target, deck, payload) != 0) {
        return -1;
    }
    return wb_engine_send_event_to_fd(
        state, fd, "track_started", &target, deck,
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"source\":\"native_transition_command\"}"
    );
}

static bool event_has_track_identity(const char *event) {
    return strcmp(event, "deck_loaded") == 0
        || strcmp(event, "track_started") == 0
        || strcmp(event, "transition_started") == 0
        || strcmp(event, "transition_finished") == 0
        || strcmp(event, "metadata_changed") == 0
        || strcmp(event, "early_eof") == 0;
}

static int handle_sync_event(WbEngineState *state, int fd, int64_t request_id, const char *line) {
    char source_event[WB_EVENT_NAME_SIZE] = "";
    char deck_text[8] = "";
    char slot_token[WB_SLOT_TOKEN_SIZE] = "";
    char station_key[WB_STATION_KEY_SIZE] = "";
    char path[WB_PATH_SIZE] = "";
    char artist[WB_TRACK_ARTIST_SIZE] = "";
    char title[WB_TRACK_TITLE_SIZE] = "";
    char year[WB_TRACK_YEAR_SIZE] = "";
    char source_payload[4096] = "";
    char from_deck_text[8] = "";
    double fade_seconds = 0.0;
    int64_t queue_id = 0;
    int64_t track_id = 0;
    int64_t cue_in_ms = 0;
    int64_t cue_out_ms = 0;
    int64_t audio_start_ms = 0;
    int64_t play_start_ms = 0;
    int64_t transition_at_ms = 0;
    int64_t effective_end_ms = 0;
    int64_t source_end_ms = 0;
    int64_t fade_in_ms = 0;
    int64_t fade_out_ms = 0;
    int64_t seek_position_ms = 0;
    int64_t seek_from_position_ms = 0;
    int64_t event_monotonic_time_ms = 0;
    int64_t event_wall_time_unix_ms = 0;
    int64_t transition_started_wall_time_unix_ms = 0;
    int64_t transition_entry_started_wall_time_unix_ms = 0;
    int64_t transition_start_monotonic_ms = 0;
    int64_t transition_entry_start_monotonic_ms = 0;
    int64_t transition_fade_out_ms = 0;
    int64_t transition_entry_ramp_ms = 20;
    int64_t transition_silence_hold_ms = 50;
    int64_t transition_release_duration_ms = 0;
    bool descriptor_complete = false;
    char deck;
    bool prepare_audio_probe = false;
    bool activate_audio_probe = false;
    bool stop_audio_probe = false;
    bool seek_audio_probe = false;
    bool output_transition_start = false;
    bool output_transition_finish = false;
    bool ignored_after_stop = false;
    char transition_from_deck = '\0';
    WbDeckState event_descriptor = {0};
    WbDeckState audio_track = {0};

    if (!wb_json_get_string(line, "source_event", source_event, sizeof(source_event))) {
        return send_context_error(state, fd, request_id, "missing source_event");
    }
    (void)wb_json_get_string(line, "deck", deck_text, sizeof(deck_text));
    (void)wb_json_get_string(line, "slot_token", slot_token, sizeof(slot_token));
    (void)wb_json_get_string(line, "station_key", station_key, sizeof(station_key));
    (void)wb_json_get_string(line, "path", path, sizeof(path));
    (void)wb_json_get_string(line, "artist", artist, sizeof(artist));
    (void)wb_json_get_string(line, "title", title, sizeof(title));
    (void)wb_json_get_string(line, "year", year, sizeof(year));
    (void)wb_json_get_i64(line, "queue_id", &queue_id);
    (void)wb_json_get_i64(line, "track_id", &track_id);
    (void)wb_json_get_i64(line, "cue_in_ms", &cue_in_ms);
    (void)wb_json_get_i64(line, "cue_out_ms", &cue_out_ms);
    (void)wb_json_get_i64(line, "audio_start_ms", &audio_start_ms);
    (void)wb_json_get_i64(line, "play_start_ms", &play_start_ms);
    (void)wb_json_get_i64(line, "transition_at_ms", &transition_at_ms);
    (void)wb_json_get_i64(line, "effective_end_ms", &effective_end_ms);
    (void)wb_json_get_i64(line, "source_end_ms", &source_end_ms);
    (void)wb_json_get_i64(line, "fade_in_ms", &fade_in_ms);
    (void)wb_json_get_i64(line, "fade_out_ms", &fade_out_ms);
    (void)wb_json_get_i64(line, "seek_position_ms", &seek_position_ms);
    (void)wb_json_get_i64(line, "seek_from_position_ms", &seek_from_position_ms);
    (void)wb_json_get_i64(line, "event_monotonic_time_ms", &event_monotonic_time_ms);
    (void)wb_json_get_i64(line, "event_wall_time_unix_ms", &event_wall_time_unix_ms);
    (void)wb_json_get_bool(line, "descriptor_complete", &descriptor_complete);
    if (wb_json_get_object(line, "source_payload", source_payload, sizeof(source_payload))) {
        (void)wb_json_get_string(source_payload, "from_deck", from_deck_text, sizeof(from_deck_text));
        if (artist[0] == '\0') (void)wb_json_get_string(source_payload, "artist", artist, sizeof(artist));
        if (title[0] == '\0') (void)wb_json_get_string(source_payload, "title", title, sizeof(title));
        if (year[0] == '\0') (void)wb_json_get_string(source_payload, "year", year, sizeof(year));
        (void)wb_json_get_double(source_payload, "fade_seconds", &fade_seconds);
        (void)wb_json_get_i64(source_payload, "fade_out_duration_ms", &transition_fade_out_ms);
        (void)wb_json_get_i64(source_payload, "entry_ramp_ms", &transition_entry_ramp_ms);
        (void)wb_json_get_i64(source_payload, "silence_hold_ms", &transition_silence_hold_ms);
        (void)wb_json_get_i64(source_payload, "release_duration_ms", &transition_release_duration_ms);
        (void)wb_json_get_i64(
            source_payload,
            "transition_started_wall_time_unix_ms",
            &transition_started_wall_time_unix_ms
        );
        (void)wb_json_get_i64(
            source_payload,
            "entry_started_wall_time_unix_ms",
            &transition_entry_started_wall_time_unix_ms
        );
    }
    deck = optional_deck(deck_text);
    transition_from_deck = optional_deck(from_deck_text);
    if (fade_seconds < 0.0) fade_seconds = 0.0;
    if (transition_fade_out_ms <= 0 && fade_seconds > 0.0) {
        transition_fade_out_ms = (int64_t)(fade_seconds * 1000.0);
    }
    if (transition_entry_ramp_ms < 0) transition_entry_ramp_ms = 0;
    if (transition_silence_hold_ms < 0) transition_silence_hold_ms = 0;
    if (transition_release_duration_ms <= 0) {
        transition_release_duration_ms = transition_fade_out_ms + transition_silence_hold_ms;
    }
    if (transition_release_duration_ms < transition_fade_out_ms) {
        transition_release_duration_ms = transition_fade_out_ms;
    }
    transition_start_monotonic_ms = event_monotonic_time_ms;
    if (
        transition_started_wall_time_unix_ms > 0
        && event_wall_time_unix_ms >= transition_started_wall_time_unix_ms
        && event_monotonic_time_ms > 0
    ) {
        int64_t callback_age_ms = event_wall_time_unix_ms - transition_started_wall_time_unix_ms;
        if (callback_age_ms > 60000) callback_age_ms = 60000;
        transition_start_monotonic_ms = event_monotonic_time_ms - callback_age_ms;
    }
    transition_entry_start_monotonic_ms = transition_start_monotonic_ms;
    if (
        transition_entry_started_wall_time_unix_ms > 0
        && event_wall_time_unix_ms >= transition_entry_started_wall_time_unix_ms
        && event_monotonic_time_ms > 0
    ) {
        int64_t entry_callback_age_ms = event_wall_time_unix_ms - transition_entry_started_wall_time_unix_ms;
        if (entry_callback_age_ms > 60000) entry_callback_age_ms = 60000;
        transition_entry_start_monotonic_ms = event_monotonic_time_ms - entry_callback_age_ms;
    }

    if (descriptor_complete && deck != '\0' && slot_token[0] != '\0' && path[0] != '\0') {
        set_deck_descriptor(
            &event_descriptor,
            queue_id,
            track_id,
            slot_token,
            station_key,
            path,
            cue_in_ms,
            cue_out_ms,
            audio_start_ms,
            play_start_ms,
            transition_at_ms,
            effective_end_ms,
            source_end_ms,
            fade_in_ms,
            fade_out_ms
        );
        set_deck_metadata(&event_descriptor, artist, title, year);
    }

    (void)pthread_mutex_lock(&state->lock);
    state->live_sync_count += 1U;
    state->last_live_event_monotonic_ms = event_monotonic_time_ms;
    state->last_live_event_wall_time_unix_ms = event_wall_time_unix_ms;
    copy_text(state->last_live_event, sizeof(state->last_live_event), source_event);
    if (
        !state->accepting_loads
        && (
            strcmp(source_event, "deck_loaded") == 0
            || strcmp(source_event, "track_started") == 0
            || strcmp(source_event, "transition_started") == 0
            || strcmp(source_event, "transition_finished") == 0
            || strcmp(source_event, "track_seeked") == 0
        )
    ) {
        state->late_event_ignored_count += 1U;
        ignored_after_stop = true;
    }
    if (!ignored_after_stop && deck != '\0' && event_has_track_identity(source_event)) {
        WbDeckState *confirmed = deck_for(state, deck);
        WbDeckState *planned = planned_deck_for(state, deck);
        bool planned_match = identity_matches(planned, queue_id, slot_token);
        bool confirmed_match = identity_matches(confirmed, queue_id, slot_token);
        if (event_descriptor.loaded) {
            /* Timing remains authoritative and token-scoped. Metadata may be
               inherited only from the same planned/confirmed identity when the
               live callback omits artist/title. */
            if (event_descriptor.artist[0] == '\0') {
                if (planned_match) copy_text(event_descriptor.artist, sizeof(event_descriptor.artist), planned->artist);
                else if (confirmed_match) copy_text(event_descriptor.artist, sizeof(event_descriptor.artist), confirmed->artist);
            }
            if (event_descriptor.title[0] == '\0') {
                if (planned_match) copy_text(event_descriptor.title, sizeof(event_descriptor.title), planned->title);
                else if (confirmed_match) copy_text(event_descriptor.title, sizeof(event_descriptor.title), confirmed->title);
            }
            *confirmed = event_descriptor;
        } else if (planned_match) {
            *confirmed = *planned;
            update_deck_identity_preserving_descriptor(
                confirmed, queue_id, track_id, slot_token, station_key, path
            );
        } else if (confirmed_match) {
            update_deck_identity_preserving_descriptor(
                confirmed, queue_id, track_id, slot_token, station_key, path
            );
        } else {
            clear_deck_state(confirmed);
            set_deck_identity(confirmed, queue_id, track_id, slot_token, station_key, path);
        }
        /* Accepted live metadata is authoritative even when the lifecycle
           event intentionally omits timing fields. */
        set_deck_metadata(confirmed, artist, title, year);
        if (planned_match) {
            clear_deck_state(planned);
            state->confirmed_load_count += 1U;
        }
        audio_track = event_descriptor.loaded ? event_descriptor : *confirmed;
    }
    if (ignored_after_stop) {
        (void)pthread_mutex_unlock(&state->lock);
        return send_state_reply(state, fd, request_id);
    }
    if (deck != '\0' && (
        strcmp(source_event, "track_started") == 0
        || strcmp(source_event, "transition_started") == 0
        || strcmp(source_event, "transition_finished") == 0
    )) {
        state->active_deck = deck;
    }
    if (strcmp(source_event, "transition_started") == 0) {
        state->transitioning = true;
        output_transition_start = true;
    } else if (strcmp(source_event, "transition_finished") == 0) {
        state->transitioning = false;
        output_transition_finish = true;
    }
    if (deck != '\0' && strcmp(source_event, "deck_loaded") == 0) {
        if (!audio_track.loaded) audio_track = *deck_for(state, deck);
        prepare_audio_probe = true;
    }
    if (deck != '\0' && strcmp(source_event, "track_started") == 0) {
        if (!audio_track.loaded) audio_track = *deck_for(state, deck);
        activate_audio_probe = true;
    } else if (deck != '\0' && strcmp(source_event, "track_seeked") == 0) {
        state->active_deck = deck;
        state->transitioning = false;
        seek_audio_probe = true;
    } else if (deck != '\0' && strcmp(source_event, "track_ended") == 0) {
        stop_audio_probe = true;
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (prepare_audio_probe) wb_audio_probe_prepare_deck(state, deck, &audio_track);
    if (activate_audio_probe) {
        wb_icecast_output_activate_track(state, deck, &audio_track);
        wb_audio_probe_activate_deck(
            state,
            deck,
            &audio_track,
            descriptor_complete,
            event_monotonic_time_ms
        );
    }
    if (seek_audio_probe) {
        (void)seek_from_position_ms;
        wb_audio_probe_seek_track(state, deck, queue_id, slot_token, seek_position_ms);
    }
    if (output_transition_start && deck != '\0') {
        wb_icecast_output_transition_started(
            state,
            transition_from_deck,
            deck,
            transition_start_monotonic_ms,
            transition_entry_start_monotonic_ms,
            transition_release_duration_ms,
            transition_fade_out_ms,
            transition_entry_ramp_ms,
            transition_silence_hold_ms
        );
    }
    if (output_transition_finish && deck != '\0') {
        wb_icecast_output_transition_finished(state, deck);
    }
    if (stop_audio_probe) {
        wb_audio_probe_stop_track(state, deck, queue_id, slot_token, "track_ended");
        wb_icecast_output_stop_track(state, deck, queue_id, slot_token);
    }
    return send_state_reply(state, fd, request_id);
}

static void cancel_all_planned(WbEngineState *state) {
    if (state->planned_deck_a.loaded) state->cancelled_load_count += 1U;
    if (state->planned_deck_b.loaded) state->cancelled_load_count += 1U;
    clear_deck_state(&state->planned_deck_a);
    clear_deck_state(&state->planned_deck_b);
}


static int handle_kill_icecast_encoder(WbEngineState *state, int fd, int64_t request_id) {
    char error[WB_ICECAST_ERROR_SIZE] = "";
    if (wb_icecast_output_kill_encoder(state, error, sizeof(error)) != 0) {
        return send_context_error(state, fd, request_id, error[0] != '\0' ? error : "native encoder is not running");
    }
    return send_icecast_output_state_reply(state, fd, request_id);
}

static int handle_kill_native_dsp(WbEngineState *state, int fd, int64_t request_id) {
    char error[WB_ICECAST_ERROR_SIZE] = "";
    if (wb_icecast_output_kill_dsp(state, error, sizeof(error)) != 0) {
        return send_context_error(state, fd, request_id, error[0] != '\0' ? error : "cannot kill native DSP");
    }
    return send_icecast_output_state_reply(state, fd, request_id);
}

static int handle_inject_late_events(WbEngineState *state, int fd, int64_t request_id) {
    char load_line[2048];
    char event_line[3072];
    char token[WB_SLOT_TOKEN_SIZE];
    int64_t now = monotonic_ms();
    bool stopped;
    (void)pthread_mutex_lock(&state->lock);
    stopped = !state->running && !state->accepting_loads;
    (void)pthread_mutex_unlock(&state->lock);
    if (!stopped) {
        return send_context_error(state, fd, request_id, "stop the native engine before injecting late events");
    }
    (void)snprintf(token, sizeof(token), "late-stop-test-%lld", (long long)now);
    (void)snprintf(
        load_line,
        sizeof(load_line),
        "{\"version\":1,\"request_id\":0,\"command\":\"load\",\"deck\":\"A\","
        "\"track\":{\"station_key\":\"late-stop-test\",\"queue_id\":990026,"
        "\"track_id\":990126,\"slot_token\":\"%s\",\"path\":\"/late-stop-test.mp3\","
        "\"artist\":\"Late Test\",\"title\":\"Rejected Load\"},\"options\":{}}",
        token
    );
    (void)handle_load(state, -1, 0, load_line);
    (void)snprintf(
        event_line,
        sizeof(event_line),
        "{\"version\":1,\"request_id\":0,\"command\":\"sync_event\","
        "\"source_event\":\"deck_loaded\",\"station_key\":\"late-stop-test\","
        "\"queue_id\":990026,\"track_id\":990126,\"slot_token\":\"%s\","
        "\"deck\":\"A\",\"path\":\"/late-stop-test.mp3\",\"artist\":\"Late Test\","
        "\"title\":\"Ignored Event\",\"descriptor_complete\":true,\"source_end_ms\":1000,"
        "\"event_monotonic_time_ms\":%lld,\"event_wall_time_unix_ms\":0,\"source_payload\":{}}",
        token,
        (long long)now
    );
    (void)handle_sync_event(state, -1, 0, event_line);
    (void)snprintf(
        event_line,
        sizeof(event_line),
        "{\"version\":1,\"request_id\":0,\"command\":\"sync_event\","
        "\"source_event\":\"track_started\",\"station_key\":\"late-stop-test\","
        "\"queue_id\":990026,\"track_id\":990126,\"slot_token\":\"%s\","
        "\"deck\":\"A\",\"path\":\"/late-stop-test.mp3\",\"artist\":\"Late Test\","
        "\"title\":\"Ignored Event\",\"event_monotonic_time_ms\":%lld,"
        "\"event_wall_time_unix_ms\":0,\"source_payload\":{}}",
        token,
        (long long)now
    );
    (void)handle_sync_event(state, -1, 0, event_line);
    return send_state_reply(state, fd, request_id);
}

static int64_t shift_clock_across_pause(
    int64_t clock_ms,
    int64_t pause_started_ms,
    int64_t resumed_ms
) {
    if (clock_ms <= 0 || pause_started_ms <= 0 || resumed_ms <= pause_started_ms) return clock_ms;
    if (clock_ms <= pause_started_ms) {
        return clock_ms + (resumed_ms - pause_started_ms);
    }
    /* A seek/replacement scheduled during pause starts when playback resumes. */
    return resumed_ms;
}

static void shift_probe_pause_clock_locked(
    WbAudioDeckProbe *probe,
    int64_t pause_started_ms,
    int64_t resumed_ms
) {
    if (probe == NULL) return;
    probe->activation_monotonic_ms = shift_clock_across_pause(
        probe->activation_monotonic_ms, pause_started_ms, resumed_ms
    );
    probe->requested_activation_monotonic_ms = shift_clock_across_pause(
        probe->requested_activation_monotonic_ms, pause_started_ms, resumed_ms
    );
    probe->replacement_activation_monotonic_ms = shift_clock_across_pause(
        probe->replacement_activation_monotonic_ms, pause_started_ms, resumed_ms
    );
    probe->fault_trigger_monotonic_ms = shift_clock_across_pause(
        probe->fault_trigger_monotonic_ms, pause_started_ms, resumed_ms
    );
}

static int handle_set_paused(
    WbEngineState *state,
    int client_fd,
    int64_t request_id,
    const char *line
) {
    bool paused = false;
    bool running;
    bool changed = false;
    int64_t now_ms = monotonic_ms();
    int64_t pause_started_ms = 0;
    int64_t pause_duration_ms = 0;
    char result[256];

    if (!wb_json_get_bool(line, "paused", &paused)) {
        return send_context_error(state, client_fd, request_id, "missing paused flag");
    }

    (void)pthread_mutex_lock(&state->lock);
    running = state->running;
    if (running && paused != state->paused) {
        changed = true;
        if (paused) {
            state->paused = true;
            state->pause_started_monotonic_ms = now_ms;
        } else {
            pause_started_ms = state->pause_started_monotonic_ms;
            if (pause_started_ms > 0 && now_ms > pause_started_ms) {
                pause_duration_ms = now_ms - pause_started_ms;
            }
            state->paused = false;
            state->pause_started_monotonic_ms = 0;
            shift_probe_pause_clock_locked(&state->audio_deck_a, pause_started_ms, now_ms);
            shift_probe_pause_clock_locked(&state->audio_deck_a_alt, pause_started_ms, now_ms);
            shift_probe_pause_clock_locked(&state->audio_deck_b, pause_started_ms, now_ms);
            shift_probe_pause_clock_locked(&state->audio_deck_b_alt, pause_started_ms, now_ms);
        }
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (!running) {
        return send_context_error(state, client_fd, request_id, "engine is stopped");
    }
    wb_icecast_output_set_paused(state, paused, pause_duration_ms);
    (void)snprintf(
        result,
        sizeof(result),
        "{\"running\":true,\"paused\":%s,\"changed\":%s,\"pause_duration_ms\":%lld}",
        paused ? "true" : "false",
        changed ? "true" : "false",
        (long long)pause_duration_ms
    );
    return send_context_reply(state, client_fd, request_id, result);
}

int wb_engine_handle_line(WbEngineState *state, int client_fd, const char *line) {
    int64_t request_id = 0;
    int64_t version = 0;
    char command[128] = "";

    update_protocol_identity(state, line);
    if (!wb_json_get_i64(line, "request_id", &request_id)) {
        return send_context_error(state, client_fd, 0, "missing or invalid request_id");
    }
    if (!wb_json_get_i64(line, "version", &version) || version != WB_PROTOCOL_VERSION) {
        return send_context_error(state, client_fd, request_id, "unsupported protocol version");
    }
    if (!wb_json_get_string(line, "command", command, sizeof(command))) {
        return send_context_error(state, client_fd, request_id, "missing command");
    }

    if (strcmp(command, "ping") == 0) {
        return send_context_reply(state,
            client_fd,
            request_id,
            "{\"pong\":true,\"control_only\":false,\"audio_enabled\":false,\"audio_decode_enabled\":true,\"audio_output_enabled\":true,\"native_icecast_output\":true,"
            "\"live_event_sync\":true,\"two_phase_deck_load\":true,\"deck_preload\":true,\"pcm_ring_buffer\":true,\"candidate_slots_per_deck\":2,\"playback_voice_separated\":true,\"candidate_alias_capacity\":32,\"audio_runtime_validation\":true,\"native_pcm_analysis\":true,\"native_timing_owner\":true,\"native_next_track_request\":true,\"embedded_libav\":true,\"ffmpeg_subprocesses\":false,"
            "\"native_resource_diagnostics\":true,\"drift_diagnostics\":true,\"output_gap_diagnostics\":true,\"encoder_reap_diagnostics\":true,"
            "\"native_daemon_version\":\"" WB_NATIVE_DAEMON_VERSION "\"}"
        );
    }
    if (strcmp(command, "start") == 0) {
        (void)pthread_mutex_lock(&state->lock);
        state->running = true;
        state->paused = false;
        state->pause_started_monotonic_ms = 0;
        state->accepting_loads = true;
        (void)pthread_mutex_unlock(&state->lock);
        wb_icecast_output_set_engine_running(state, true);
        wb_native_timing_reset(state);
        wb_native_timing_wake(state);
        return send_context_reply(state, client_fd, request_id, "{\"running\":true,\"control_only\":false,\"audio_output_enabled\":true}");
    }
    if (strcmp(command, "stop") == 0) {
        (void)pthread_mutex_lock(&state->lock);
        state->running = false;
        state->paused = false;
        state->pause_started_monotonic_ms = 0;
        state->accepting_loads = false;
        state->transitioning = false;
        cancel_all_planned(state);
        (void)pthread_mutex_unlock(&state->lock);
        wb_native_timing_reset(state);
        wb_audio_probe_stop_all(state, "engine_stop");
        wb_icecast_output_set_engine_running(state, false);
        return send_context_reply(state, client_fd, request_id, "{\"running\":false,\"control_only\":false,\"audio_output_enabled\":true}");
    }
    if (strcmp(command, "set_paused") == 0) {
        return handle_set_paused(state, client_fd, request_id, line);
    }
    if (strcmp(command, "reload") == 0) {
        return send_context_reply(state, client_fd, request_id, "{\"accepted\":true,\"control_only\":false,\"audio_output_enabled\":true}");
    }
    if (strcmp(command, "get_fault") == 0) {
        return send_fault_state_reply(state, client_fd, request_id);
    }
    if (strcmp(command, "configure_fault") == 0) {
        return handle_configure_fault(state, client_fd, request_id, line);
    }
    if (strcmp(command, "clear_fault") == 0) {
        return handle_clear_fault(state, client_fd, request_id);
    }
    if (strcmp(command, "get_diagnostics") == 0) {
        return send_diagnostics_state_reply(state, client_fd, request_id);
    }
    if (strcmp(command, "emit_diagnostics_snapshot") == 0) {
        wb_diagnostics_emit_snapshot(state, "manual");
        return send_diagnostics_state_reply(state, client_fd, request_id);
    }
    if (strcmp(command, "get_icecast_output") == 0) {
        return send_icecast_output_state_reply(state, client_fd, request_id);
    }
    if (strcmp(command, "configure_icecast_output") == 0) {
        return handle_configure_icecast_output(state, client_fd, request_id, line);
    }
    if (strcmp(command, "clear_icecast_output") == 0) {
        return handle_clear_icecast_output(state, client_fd, request_id, line);
    }
    if (strcmp(command, "kill_icecast_encoder") == 0) {
        return handle_kill_icecast_encoder(state, client_fd, request_id);
    }
    if (strcmp(command, "kill_native_dsp") == 0) {
        return handle_kill_native_dsp(state, client_fd, request_id);
    }
    if (strcmp(command, "inject_late_events") == 0) {
        return handle_inject_late_events(state, client_fd, request_id);
    }
    if (strcmp(command, "get_state") == 0) {
        return handle_get_state(state, client_fd, request_id);
    }
    if (strcmp(command, "load") == 0) {
        return handle_load(state, client_fd, request_id, line);
    }
    if (strcmp(command, "cancel_load") == 0) {
        return handle_cancel_load(state, client_fd, request_id, line);
    }
    if (strcmp(command, "hard_handoff") == 0) {
        return handle_hard_handoff(state, client_fd, request_id, line);
    }
    if (strcmp(command, "select") == 0) {
        return handle_select(state, client_fd, request_id, line);
    }
    if (strcmp(command, "transition") == 0) {
        return handle_transition(state, client_fd, request_id, line);
    }
    if (strcmp(command, "sync_event") == 0) {
        return handle_sync_event(state, client_fd, request_id, line);
    }
    return send_context_error(state, client_fd, request_id, "unknown command");
}
