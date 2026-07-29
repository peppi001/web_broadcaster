#define _POSIX_C_SOURCE 200809L

#include "audio_probe.h"
#include "icecast_output.h"
#include "libav_bridge.h"
#include "native_timing.h"
#include "protocol.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define WB_PCM_READ_BYTES 16384U
#define WB_PROGRESS_INTERVAL_MS 1000LL
#define WB_EARLY_EOF_TOLERANCE_MS 250LL
#define WB_DEFAULT_AUDIO_START_TIMEOUT_MS 500
#define WB_DEFAULT_AUDIO_SEEK_START_TIMEOUT_MS 1200
#define WB_DEFAULT_AUDIO_SEEK_HARD_TIMEOUT_MS 2000
#define WB_MIN_AUDIO_START_TIMEOUT_MS 100
#define WB_MAX_AUDIO_START_TIMEOUT_MS 10000
#define WB_CHILD_TERM_GRACE_MS 120LL
#define WB_DEFAULT_RING_CAPACITY_MS 4000
#define WB_DEFAULT_PREBUFFER_MS 1000
#define WB_MIN_RING_CAPACITY_MS 250
#define WB_MAX_RING_CAPACITY_MS 30000

static void copy_text(char *destination, size_t size, const char *source) {
    if (size == 0U) return;
    (void)snprintf(destination, size, "%s", source == NULL ? "" : source);
}

static bool env_enabled(const char *name, bool default_value) {
    const char *value = getenv(name);
    if (value == NULL || value[0] == '\0') return default_value;
    if (strcmp(value, "0") == 0 || strcasecmp(value, "false") == 0 || strcasecmp(value, "no") == 0) {
        return false;
    }
    return true;
}

static int env_int(const char *name, int default_value, int minimum, int maximum) {
    const char *value = getenv(name);
    char *end = NULL;
    long parsed;
    if (value == NULL || value[0] == '\0') return default_value;
    errno = 0;
    parsed = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0') return default_value;
    if (parsed < minimum) parsed = minimum;
    if (parsed > maximum) parsed = maximum;
    return (int)parsed;
}

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static int configure_ffmpeg_runtime(WbEngineState *state) {
    char error[WB_FFMPEG_ERROR_SIZE] = "";
    state->ffmpeg_runtime_valid = false;
    state->ffmpeg_system_fallback_used = false;
    state->ffmpeg_runtime_error[0] = '\0';
    state->ffmpeg_version[0] = '\0';
    if (wb_libav_runtime_init(state, error, sizeof(error)) != 0) {
        copy_text(
            state->ffmpeg_runtime_error,
            sizeof(state->ffmpeg_runtime_error),
            error[0] != '\0' ? error : "linked libav runtime initialization failed"
        );
        return -1;
    }
    return 0;
}


static bool fault_mode_valid(const char *mode) {
    if (mode == NULL || mode[0] == '\0') return false;
    return strcmp(mode, "early_eof") == 0
        || strcmp(mode, "kill_decoder") == 0
        || strcmp(mode, "decoder_stall") == 0
        || strcmp(mode, "buffer_underrun") == 0
        || strcmp(mode, "corrupt_input") == 0
        || strcmp(mode, "missing_file") == 0;
}

static void clear_probe_fault_locked(WbAudioDeckProbe *probe) {
    probe->fault_armed = false;
    probe->fault_triggered = false;
    probe->fault_after_ms = 0;
    probe->fault_duration_ms = 0;
    probe->fault_trigger_monotonic_ms = 0;
    probe->fault_mode[0] = '\0';
}

static void finish_probe_fault_locked(
    WbEngineState *state,
    WbAudioDeckProbe *probe,
    const char *terminal_reason
) {
    const char *reason = terminal_reason == NULL ? "" : terminal_reason;
    if (state == NULL || probe == NULL) return;
    if (!probe->fault_armed && !probe->fault_triggered) return;

    if (probe->fault_mode[0] != '\0') {
        copy_text(state->fault_last_mode, sizeof(state->fault_last_mode), probe->fault_mode);
    }
    if (probe->track.slot_token[0] != '\0') {
        copy_text(
            state->fault_last_slot_token,
            sizeof(state->fault_last_slot_token),
            probe->track.slot_token
        );
    }
    if (probe->fault_triggered) {
        copy_text(
            state->fault_last_terminal_reason,
            sizeof(state->fault_last_terminal_reason),
            reason[0] != '\0' ? reason : "unknown"
        );
    } else {
        copy_text(
            state->fault_last_terminal_reason,
            sizeof(state->fault_last_terminal_reason),
            "not_triggered"
        );
    }
    clear_probe_fault_locked(probe);
}

static void arm_fault_for_activation_locked(
    WbEngineState *state,
    WbAudioDeckProbe *probe,
    const WbDeckState *track
) {
    if (state == NULL || probe == NULL || track == NULL) return;
    if (!state->fault_enabled || !fault_mode_valid(state->fault_mode)) return;
    if (state->fault_target_deck != '\0' && state->fault_target_deck != probe->deck) return;
    if (
        state->fault_target_slot_token[0] != '\0'
        && strcmp(state->fault_target_slot_token, track->slot_token) != 0
    ) return;

    clear_probe_fault_locked(probe);
    probe->fault_armed = true;
    probe->fault_after_ms = state->fault_after_ms < 0 ? 0 : state->fault_after_ms;
    probe->fault_duration_ms = state->fault_duration_ms < 0 ? 0 : state->fault_duration_ms;
    copy_text(probe->fault_mode, sizeof(probe->fault_mode), state->fault_mode);
    state->fault_arm_count += 1U;
    if (state->fault_once) state->fault_enabled = false;
}

static void clear_candidate_locked(WbAudioDeckProbe *probe);

static void probe_pair(
    WbEngineState *state,
    char deck,
    WbAudioDeckProbe **first,
    WbAudioDeckProbe **second
) {
    if (deck == 'B') {
        *first = &state->audio_deck_b;
        *second = &state->audio_deck_b_alt;
    } else {
        *first = &state->audio_deck_a;
        *second = &state->audio_deck_a_alt;
    }
}

static bool probe_is_reusable(const WbAudioDeckProbe *probe) {
    return probe != NULL
        && !probe->shutdown
        && !probe->running
        && probe->child_pid <= 0
        && !probe->request_pending
        && !probe->replacement_pending
        && !probe->activated;
}

/* A shared candidate is valid only while its decoder is still live or it owns
   actual buffered PCM.  A terminal EOF/error candidate may retain descriptor
   and accounting fields for diagnostics, but it must never be treated as a
   reusable preload for a later queue identity. */
static bool probe_has_live_shareable_content(const WbAudioDeckProbe *probe) {
    if (
        probe == NULL
        || probe->shutdown
        || probe->activated
        || probe->eof
        || probe->stop_requested
        || probe->replacement_pending
    ) {
        return false;
    }
    if (probe->running || probe->request_pending) return true;
    return probe->prebuffer_ready && probe->ring_fill >= WB_AUDIO_FRAME_BYTES;
}

/* Activation may proceed without reopening the decoder only when the candidate
   is still decoding/pending or has at least one complete PCM frame buffered. */
static bool probe_can_activate_without_restart(const WbAudioDeckProbe *probe) {
    if (
        probe == NULL
        || probe->shutdown
        || probe->activated
        || probe->eof
        || probe->stop_requested
        || probe->replacement_pending
    ) {
        return false;
    }
    return probe->running
        || probe->request_pending
        || probe->ring_fill >= WB_AUDIO_FRAME_BYTES;
}

static bool identity_matches(const WbDeckState *candidate, const WbDeckState *track) {
    if (candidate == NULL || track == NULL || !candidate->loaded || !track->loaded) return false;
    if (track->slot_token[0] != '\0') return strcmp(candidate->slot_token, track->slot_token) == 0;
    return track->queue_id > 0 && candidate->queue_id == track->queue_id;
}

static bool identity_values_match(
    const WbDeckState *candidate,
    int64_t queue_id,
    const char *slot_token
) {
    if (candidate == NULL || !candidate->loaded) return false;
    if (slot_token != NULL && slot_token[0] != '\0') {
        return strcmp(candidate->slot_token, slot_token) == 0;
    }
    return queue_id > 0 && candidate->queue_id == queue_id;
}


static int64_t track_play_start_ms(const WbDeckState *track) {
    if (track == NULL) return 0;
    return track->play_start_ms > 0 ? track->play_start_ms : track->cue_in_ms;
}

static int64_t track_transition_at_ms(const WbDeckState *track) {
    if (track == NULL) return 0;
    return track->transition_at_ms > 0 ? track->transition_at_ms : track->cue_out_ms;
}

static int64_t track_effective_end_ms(const WbDeckState *track) {
    int64_t transition_at;
    if (track == NULL) return 0;
    if (track->effective_end_ms > 0) return track->effective_end_ms;
    if (track->source_end_ms > 0) return track->source_end_ms;
    transition_at = track_transition_at_ms(track);
    return transition_at > 0 ? transition_at : 0;
}

static int64_t track_source_end_ms(const WbDeckState *track) {
    int64_t effective_end;
    if (track == NULL) return 0;
    if (track->source_end_ms > 0) return track->source_end_ms;
    effective_end = track_effective_end_ms(track);
    return effective_end > 0 ? effective_end : track_transition_at_ms(track);
}

static int64_t track_expected_content_duration_ms(const WbDeckState *track) {
    int64_t start = track_play_start_ms(track);
    int64_t end = track_effective_end_ms(track);
    if (end <= start) end = track_source_end_ms(track);
    return end > start ? end - start : 0;
}

static bool same_audio_content(const WbDeckState *left, const WbDeckState *right) {
    if (left == NULL || right == NULL || !left->loaded || !right->loaded) return false;
    return strcmp(left->path, right->path) == 0
        && track_play_start_ms(left) == track_play_start_ms(right)
        && track_source_end_ms(left) == track_source_end_ms(right);
}

static bool same_audio_descriptor(const WbDeckState *left, const WbDeckState *right) {
    return same_audio_content(left, right)
        && left->audio_start_ms == right->audio_start_ms
        && track_transition_at_ms(left) == track_transition_at_ms(right)
        && track_effective_end_ms(left) == track_effective_end_ms(right)
        && left->fade_in_ms == right->fade_in_ms
        && left->fade_out_ms == right->fade_out_ms;
}

static void clear_aliases(WbAudioDeckProbe *probe) {
    probe->alias_count = 0U;
    memset(probe->aliases, 0, sizeof(probe->aliases));
}

static ssize_t alias_index_for_track(const WbAudioDeckProbe *probe, const WbDeckState *track) {
    size_t index;
    if (probe == NULL || track == NULL) return -1;
    for (index = 0U; index < probe->alias_count; index += 1U) {
        if (identity_matches(&probe->aliases[index], track)) return (ssize_t)index;
    }
    return -1;
}

static ssize_t alias_index_for_values(
    const WbAudioDeckProbe *probe,
    int64_t queue_id,
    const char *slot_token
) {
    size_t index;
    if (probe == NULL) return -1;
    for (index = 0U; index < probe->alias_count; index += 1U) {
        if (identity_values_match(&probe->aliases[index], queue_id, slot_token)) {
            return (ssize_t)index;
        }
    }
    return -1;
}

static bool append_alias_locked(WbAudioDeckProbe *probe, const WbDeckState *track) {
    if (probe == NULL || track == NULL) return false;
    if (identity_matches(&probe->track, track) || alias_index_for_track(probe, track) >= 0) {
        return true;
    }
    if (probe->alias_count >= WB_AUDIO_ALIAS_CAPACITY) return false;
    probe->aliases[probe->alias_count++] = *track;
    return true;
}

static bool probe_identity_matches(
    const WbAudioDeckProbe *probe,
    const WbDeckState *track,
    ssize_t *matched_alias_index
) {
    ssize_t index;
    if (matched_alias_index != NULL) *matched_alias_index = -1;
    if (probe == NULL || track == NULL) return false;
    if (identity_matches(&probe->track, track)) return true;
    index = alias_index_for_track(probe, track);
    if (index >= 0) {
        if (matched_alias_index != NULL) *matched_alias_index = index;
        return true;
    }
    return false;
}

static bool probe_identity_values_match(
    const WbAudioDeckProbe *probe,
    int64_t queue_id,
    const char *slot_token,
    ssize_t *matched_alias_index
) {
    ssize_t index;
    if (matched_alias_index != NULL) *matched_alias_index = -1;
    if (probe == NULL) return false;
    if (identity_values_match(&probe->track, queue_id, slot_token)) return true;
    index = alias_index_for_values(probe, queue_id, slot_token);
    if (index >= 0) {
        if (matched_alias_index != NULL) *matched_alias_index = index;
        return true;
    }
    return false;
}

static void remove_alias_at_locked(WbAudioDeckProbe *probe, size_t index) {
    if (probe == NULL || index >= probe->alias_count) return;
    if (index + 1U < probe->alias_count) {
        memmove(
            &probe->aliases[index],
            &probe->aliases[index + 1U],
            (probe->alias_count - index - 1U) * sizeof(probe->aliases[0])
        );
    }
    probe->alias_count -= 1U;
    memset(&probe->aliases[probe->alias_count], 0, sizeof(probe->aliases[0]));
}

static void resolve_alias_as_primary_locked(WbAudioDeckProbe *probe, size_t index) {
    WbDeckState selected;
    if (probe == NULL || index >= probe->alias_count) return;
    selected = probe->aliases[index];
    probe->track = selected;
    clear_aliases(probe);
}

/* Detach an obsolete primary identity without discarding the shared decoder.
   The selected alias becomes the new primary while every other alias remains
   attached to the same PCM candidate. */
static void replace_primary_with_alias_locked(WbAudioDeckProbe *probe, size_t index) {
    WbDeckState selected;
    if (probe == NULL || index >= probe->alias_count) return;
    selected = probe->aliases[index];
    remove_alias_at_locked(probe, index);
    probe->track = selected;
}

static void terminate_child(pid_t child_pid) {
    if (child_pid > 0) (void)kill(child_pid, SIGTERM);
}

static size_t ms_to_bytes(int milliseconds) {
    uint64_t frames;
    if (milliseconds <= 0) return 0U;
    frames = ((uint64_t)milliseconds * (uint64_t)WB_AUDIO_SAMPLE_RATE) / 1000ULL;
    return (size_t)(frames * (uint64_t)WB_AUDIO_FRAME_BYTES);
}

static int64_t samples_to_ms(uint64_t samples) {
    return (int64_t)((samples * 1000ULL) / (uint64_t)WB_AUDIO_SAMPLE_RATE);
}

static void ring_reset(WbAudioDeckProbe *probe) {
    probe->ring_read_pos = 0U;
    probe->ring_write_pos = 0U;
    probe->ring_fill = 0U;
}

static void queue_track_locked(
    WbEngineState *state,
    WbAudioDeckProbe *probe,
    const WbDeckState *track,
    bool activate,
    int64_t requested_activation_ms
) {
    probe->generation += 1U;
    state->audio_candidate_serial += 1U;
    probe->candidate_serial = state->audio_candidate_serial;
    probe->track = *track;
    clear_aliases(probe);
    probe->request_pending = true;
    probe->replacement_pending = false;
    probe->replacement_activate = false;
    probe->replacement_is_seek = false;
    memset(&probe->replacement_track, 0, sizeof(probe->replacement_track));
    probe->activated = activate;
    probe->stop_requested = false;
    probe->running = false;
    probe->eof = false;
    probe->prebuffer_ready = false;
    probe->final_duration_valid = false;
    probe->decoded_samples = 0U;
    probe->played_samples = 0U;
    probe->decoded_duration_ms = 0;
    probe->played_duration_ms = 0;
    probe->position_ms = track->cue_in_ms;
    probe->final_actual_duration_ms = 0;
    probe->requested_activation_monotonic_ms = activate
        ? (requested_activation_ms > 0 ? requested_activation_ms : monotonic_ms())
        : 0;
    probe->activation_monotonic_ms = probe->requested_activation_monotonic_ms;
    probe->first_sample_monotonic_ms = 0;
    probe->audio_mismatch_emitted = false;
    clear_probe_fault_locked(probe);
    if (activate) arm_fault_for_activation_locked(state, probe, track);
    probe->child_pid = 0;
    probe->stop_reason[0] = '\0';
    probe->error[0] = '\0';
    ring_reset(probe);
    copy_text(probe->status, sizeof(probe->status), activate ? "buffering" : "queued");
    (void)pthread_cond_signal(&probe->cond);
}

static void queue_replacement_locked(
    WbAudioDeckProbe *probe,
    const WbDeckState *track,
    bool activate,
    int64_t requested_activation_ms,
    const char *reason
) {
    probe->replacement_track = *track;
    probe->replacement_pending = true;
    probe->replacement_activate = activate;
    probe->replacement_is_seek = false;
    probe->replacement_activation_monotonic_ms = activate
        ? (requested_activation_ms > 0 ? requested_activation_ms : monotonic_ms())
        : 0;
    probe->stop_requested = true;
    copy_text(
        probe->stop_reason,
        sizeof(probe->stop_reason),
        reason == NULL ? "superseded_by_new_load" : reason
    );
    if (probe->running || probe->child_pid > 0 || probe->request_pending) {
        copy_text(probe->status, sizeof(probe->status), "stopping");
    }
    (void)pthread_cond_signal(&probe->cond);
}

static bool promote_replacement_locked(WbEngineState *state, WbAudioDeckProbe *probe) {
    WbDeckState replacement;
    bool activate;
    bool is_seek;
    int64_t requested_activation_ms;
    int64_t seek_from_position_ms;
    int64_t seek_target_position_ms;
    if (!probe->replacement_pending || probe->shutdown) return false;
    replacement = probe->replacement_track;
    activate = probe->replacement_activate;
    is_seek = probe->replacement_is_seek;
    requested_activation_ms = probe->replacement_activation_monotonic_ms;
    seek_from_position_ms = probe->seek_from_position_ms;
    seek_target_position_ms = probe->seek_target_position_ms;
    probe->replacement_pending = false;
    probe->replacement_activate = false;
    probe->replacement_is_seek = false;
    probe->replacement_activation_monotonic_ms = 0;
    memset(&probe->replacement_track, 0, sizeof(probe->replacement_track));
    queue_track_locked(state, probe, &replacement, activate, requested_activation_ms);
    if (is_seek) {
        probe->seek_restart_generation = probe->generation;
        probe->seek_from_position_ms = seek_from_position_ms;
        probe->seek_target_position_ms = seek_target_position_ms;
    }
    return true;
}

static size_t ring_write(WbAudioDeckProbe *probe, const unsigned char *data, size_t bytes) {
    size_t available = probe->ring_capacity - probe->ring_fill;
    size_t accepted = bytes < available ? bytes : available;
    size_t first;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (probe->ring_write_pos + first > probe->ring_capacity) {
        first = probe->ring_capacity - probe->ring_write_pos;
    }
    memcpy(probe->ring_buffer + probe->ring_write_pos, data, first);
    if (accepted > first) memcpy(probe->ring_buffer, data + first, accepted - first);
    probe->ring_write_pos = (probe->ring_write_pos + accepted) % probe->ring_capacity;
    probe->ring_fill += accepted;
    if (probe->ring_fill > probe->ring_high_water_bytes) {
        probe->ring_high_water_bytes = probe->ring_fill;
    }
    return accepted;
}

static size_t ring_read_pcm(WbAudioDeckProbe *probe, unsigned char *destination, size_t bytes) {
    size_t accepted = bytes < probe->ring_fill ? bytes : probe->ring_fill;
    size_t first;
    accepted -= accepted % WB_AUDIO_FRAME_BYTES;
    if (accepted == 0U || destination == NULL) return 0U;
    first = accepted;
    if (probe->ring_read_pos + first > probe->ring_capacity) {
        first = probe->ring_capacity - probe->ring_read_pos;
    }
    memcpy(destination, probe->ring_buffer + probe->ring_read_pos, first);
    if (accepted > first) memcpy(destination + first, probe->ring_buffer, accepted - first);
    probe->ring_read_pos = (probe->ring_read_pos + accepted) % probe->ring_capacity;
    probe->ring_fill -= accepted;
    return accepted;
}

static int send_probe_event(
    WbEngineState *state,
    const char *event,
    const WbDeckState *track,
    char deck,
    uint64_t decoded_samples,
    uint64_t played_samples,
    int64_t decoded_duration_ms,
    int64_t played_duration_ms,
    int64_t source_position_ms,
    size_t buffered_bytes,
    size_t capacity_bytes,
    const char *extra_json
) {
    char payload[24576];
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"probe_only\":true,\"decoder\":\"embedded_libav\"," 
        "\"audio_decode_enabled\":true,\"audio_output_enabled\":true,\"native_icecast_output\":true," 
        "\"sample_rate\":%d,\"channels\":%d," 
        "\"manual_timing\":%s,\"stream_source\":%s,"
        "\"decoded_samples\":%llu,\"played_samples\":%llu," 
        "\"decoded_duration_ms\":%lld,\"played_duration_ms\":%lld," 
        "\"source_position_ms\":%lld,\"cue_in_ms\":%lld," 
        "\"cue_out_ms\":%lld,\"audio_start_ms\":%lld,"
        "\"play_start_ms\":%lld,\"transition_at_ms\":%lld,"
        "\"effective_end_ms\":%lld,\"source_end_ms\":%lld,"
        "\"ring_buffer_bytes\":%zu," 
        "\"ring_buffer_capacity_bytes\":%zu%s%s}",
        WB_AUDIO_SAMPLE_RATE,
        WB_AUDIO_CHANNELS,
        track->manual_timing ? "true" : "false",
        track->stream_source ? "true" : "false",
        (unsigned long long)decoded_samples,
        (unsigned long long)played_samples,
        (long long)decoded_duration_ms,
        (long long)played_duration_ms,
        (long long)source_position_ms,
        (long long)track->cue_in_ms,
        (long long)track->cue_out_ms,
        (long long)track->audio_start_ms,
        (long long)track_play_start_ms(track),
        (long long)track_transition_at_ms(track),
        (long long)track_effective_end_ms(track),
        (long long)track_source_end_ms(track),
        buffered_bytes,
        capacity_bytes,
        (extra_json != NULL && extra_json[0] != '\0') ? "," : "",
        (extra_json != NULL && extra_json[0] != '\0') ? extra_json : ""
    );
    return wb_engine_send_event(state, event, track, deck, payload);
}

static bool probe_current_locked(const WbAudioDeckProbe *probe, uint64_t generation) {
    return !probe->shutdown && probe->generation == generation;
}

static void set_terminal_status_locked(
    WbAudioDeckProbe *probe,
    const char *status,
    const char *error,
    bool eof,
    bool final_duration_valid
) {
    probe->running = false;
    probe->activated = false;
    probe->eof = eof;
    probe->prebuffer_ready = false;
    probe->child_pid = 0;
    probe->final_duration_valid = final_duration_valid;
    probe->final_actual_duration_ms = final_duration_valid ? probe->played_duration_ms : 0;
    copy_text(probe->status, sizeof(probe->status), status);
    copy_text(probe->error, sizeof(probe->error), error);
}

static void mark_prebuffer_ready(
    WbEngineState *state,
    WbAudioDeckProbe *probe,
    uint64_t generation,
    bool pipe_eof
) {
    bool emit = false;
    WbDeckState track = {0};
    WbDeckState aliases[WB_AUDIO_ALIAS_CAPACITY];
    size_t alias_count = 0U;
    uint64_t decoded_samples = 0U;
    int64_t decoded_duration_ms = 0;
    size_t fill = 0U;
    size_t capacity = 0U;
    int64_t buffered_duration_ms = 0;
    char extra[320];
    size_t index;

    memset(aliases, 0, sizeof(aliases));
    (void)pthread_mutex_lock(&state->lock);
    if (
        probe_current_locked(probe, generation)
        && !probe->prebuffer_ready
        && probe->ring_fill >= WB_AUDIO_FRAME_BYTES
        && (probe->ring_fill >= probe->prebuffer_target_bytes || pipe_eof)
    ) {
        probe->prebuffer_ready = true;
        if (!probe->activated) {
            copy_text(probe->status, sizeof(probe->status), "ready");
            track = probe->track;
            alias_count = probe->alias_count;
            if (alias_count > WB_AUDIO_ALIAS_CAPACITY) alias_count = WB_AUDIO_ALIAS_CAPACITY;
            if (alias_count > 0U) {
                memcpy(aliases, probe->aliases, alias_count * sizeof(aliases[0]));
            }
            decoded_samples = probe->decoded_samples;
            decoded_duration_ms = probe->decoded_duration_ms;
            fill = probe->ring_fill;
            capacity = probe->ring_capacity;
            buffered_duration_ms = (int64_t)(((fill / WB_AUDIO_FRAME_BYTES) * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
            emit = true;
        }
    }
    (void)pthread_mutex_unlock(&state->lock);
    if (!emit) return;

    (void)snprintf(
        extra,
        sizeof(extra),
        "\"buffered_duration_ms\":%lld,\"target_prebuffer_ms\":%d,\"source_eof\":%s,\"shared_buffer\":false,\"alias_count\":%zu",
        (long long)buffered_duration_ms,
        state->audio_prebuffer_ms,
        pipe_eof ? "true" : "false",
        alias_count
    );
    (void)send_probe_event(
        state,
        "native_audio_probe_prebuffer_ready",
        &track,
        probe->deck,
        decoded_samples,
        0U,
        decoded_duration_ms,
        0,
        track.cue_in_ms,
        fill,
        capacity,
        extra
    );
    for (index = 0U; index < alias_count; index += 1U) {
        (void)snprintf(
            extra,
            sizeof(extra),
            "\"buffered_duration_ms\":%lld,\"target_prebuffer_ms\":%d,\"source_eof\":%s,\"shared_buffer\":true,\"decoder_reused\":true,\"alias_index\":%zu,\"alias_count\":%zu",
            (long long)buffered_duration_ms,
            state->audio_prebuffer_ms,
            pipe_eof ? "true" : "false",
            index,
            alias_count
        );
        (void)send_probe_event(
            state,
            "native_audio_probe_prebuffer_ready",
            &aliases[index],
            probe->deck,
            decoded_samples,
            0U,
            decoded_duration_ms,
            0,
            aliases[index].cue_in_ms,
            fill,
            capacity,
            extra
        );
    }
}

static void run_decode(WbEngineState *state, WbAudioDeckProbe *probe, uint64_t generation, WbDeckState track) {
    WbLibavDecodeSession *decode_session = NULL;
    WbLibavDecodeConfig decode_config = {0};
    unsigned char buffer[WB_PCM_READ_BYTES];
    bool pipe_eof = false;
    uint64_t total_decoded_bytes = 0U;
    bool stopped = false;
    bool started_emitted = false;
    bool preload_started_emitted = false;
    int64_t next_progress_ms = WB_PROGRESS_INTERVAL_MS;
    char error[WB_AUDIO_ERROR_SIZE] = "";
    char fault_terminal_reason[WB_FAULT_REASON_SIZE] = "";
    char injected_fault_mode[WB_FAULT_MODE_SIZE] = "";
    bool fault_injected = false;
    bool fault_forced_eof = false;
    bool fault_stalled = false;
    bool decoder_runtime_failed = false;
    bool seek_restart = false;
    bool seek_pending_emitted = false;
    bool seek_slow_emitted = false;
    bool seek_timeout_mismatch_active = false;
    int64_t seek_from_position_ms = 0;
    int64_t seek_target_position_ms = track.cue_in_ms;
    int decoder_exit_code = 0;
    int decoder_signal = 0;
    uint64_t corrupt_input_skip_count = 0U;

    if (track.path[0] == '\0') {
        (void)pthread_mutex_lock(&state->lock);
        if (probe_current_locked(probe, generation)) {
            set_terminal_status_locked(probe, "error", "empty track path", false, false);
            finish_probe_fault_locked(state, probe, "decoder_start_failed");
        }
        (void)pthread_mutex_unlock(&state->lock);
        (void)send_probe_event(
            state, "native_audio_probe_error", &track, probe->deck,
            0U, 0U, 0, 0, track.cue_in_ms, 0U, probe->ring_capacity,
            "\"error\":\"empty track path\""
        );
        return;
    }

    if (!track.stream_source) {
        struct stat file_info;
        if (track.path[0] != '/' || stat(track.path, &file_info) != 0 || !S_ISREG(file_info.st_mode)) {
            (void)pthread_mutex_lock(&state->lock);
            if (probe_current_locked(probe, generation)) {
                set_terminal_status_locked(probe, "skipped", "local regular file required", false, false);
                probe->position_ms = track.cue_in_ms;
                finish_probe_fault_locked(state, probe, "source_file_unavailable");
            }
            (void)pthread_mutex_unlock(&state->lock);
            (void)send_probe_event(
                state, "native_audio_probe_skipped", &track, probe->deck,
                0U, 0U, 0, 0, track.cue_in_ms, 0U, probe->ring_capacity,
                "\"reason\":\"non_local_or_missing_file\""
            );
            return;
        }
    }

    decode_config.path = track.path;
    decode_config.stream_source = track.stream_source;
    decode_config.stream_infinite = track.stream_infinite;
    decode_config.start_ms = track_play_start_ms(&track);
    {
        int64_t source_end_ms = track_source_end_ms(&track);
        decode_config.duration_ms = source_end_ms > decode_config.start_ms
            ? source_end_ms - decode_config.start_ms : 0;
    }
    decode_config.fifo_capacity = probe->ring_capacity;
    if (wb_libav_decode_start(&decode_session, &decode_config, error, sizeof(error)) != 0) {
        goto failed_before_child;
    }

    (void)pthread_mutex_lock(&state->lock);
    if (probe_current_locked(probe, generation)) {
        seek_restart = probe->seek_restart_generation == generation;
        seek_from_position_ms = probe->seek_from_position_ms;
        seek_target_position_ms = probe->seek_target_position_ms;
        probe->child_pid = 0;
        probe->running = true;
        copy_text(probe->status, sizeof(probe->status), probe->activated ? "buffering" : "prebuffering");
        probe->error[0] = '\0';
    } else {
        stopped = true;
    }
    (void)pthread_mutex_unlock(&state->lock);
    if (stopped && decode_session != NULL) wb_libav_decode_abort(decode_session);

    if (!stopped) {
        preload_started_emitted = true;
        (void)send_probe_event(
            state, "native_audio_probe_preload_started", &track, probe->deck,
            0U, 0U, 0, 0, track.cue_in_ms, 0U, probe->ring_capacity,
            "\"prestarted_on\":\"deck_loaded\""
        );
    }

    while (!stopped) {
        bool current;
        bool activated;
        bool paused;
        bool stop_requested;
        size_t ring_space;
        size_t ring_fill;
        int64_t activation_ms;
        int64_t now_ms;
        bool did_work = false;
        bool fault_armed = false;
        bool fault_triggered = false;
        int64_t fault_after_ms = 0;
        int64_t fault_duration_ms = 0;
        int64_t fault_trigger_ms = 0;
        int64_t played_snapshot_ms = 0;
        char armed_fault_mode[WB_FAULT_MODE_SIZE] = "";

        (void)pthread_mutex_lock(&state->lock);
        current = probe_current_locked(probe, generation);
        activated = probe->activated;
        paused = state->paused;
        stop_requested = probe->stop_requested;
        ring_space = probe->ring_capacity - probe->ring_fill;
        ring_fill = probe->ring_fill;
        activation_ms = probe->activation_monotonic_ms;
        played_snapshot_ms = probe->played_duration_ms;
        fault_armed = probe->fault_armed;
        fault_triggered = probe->fault_triggered;
        fault_after_ms = probe->fault_after_ms;
        fault_duration_ms = probe->fault_duration_ms;
        fault_trigger_ms = probe->fault_trigger_monotonic_ms;
        copy_text(armed_fault_mode, sizeof(armed_fault_mode), probe->fault_mode);
        (void)pthread_mutex_unlock(&state->lock);

        if (!current || stop_requested) {
            stopped = true;
            if (decode_session != NULL) wb_libav_decode_abort(decode_session);
            break;
        }

        if (
            activated
            && fault_armed
            && !fault_triggered
            && played_snapshot_ms >= fault_after_ms
        ) {
            bool claimed = false;
            bool skipped = false;
            WbDeckState fault_track = {0};
            uint64_t fault_decoded_samples = 0U;
            uint64_t fault_played_samples = 0U;
            int64_t fault_decoded_duration_ms = 0;
            int64_t fault_played_duration_ms = 0;
            int64_t fault_position_ms = track.cue_in_ms;
            size_t fault_ring_fill = 0U;
            size_t fault_ring_capacity = 0U;
            char fault_extra[512];
            int64_t trigger_now = monotonic_ms();
            (void)pthread_mutex_lock(&state->lock);
            if (
                probe_current_locked(probe, generation)
                && probe->fault_armed
                && !probe->fault_triggered
            ) {
                fault_track = probe->track;
                fault_decoded_samples = probe->decoded_samples;
                fault_played_samples = probe->played_samples;
                fault_decoded_duration_ms = probe->decoded_duration_ms;
                fault_played_duration_ms = probe->played_duration_ms;
                fault_position_ms = probe->position_ms;
                fault_ring_fill = probe->ring_fill;
                fault_ring_capacity = probe->ring_capacity;
                if (
                    (
                        strcmp(probe->fault_mode, "kill_decoder") == 0
                        || strcmp(probe->fault_mode, "decoder_stall") == 0
                    )
                    && (pipe_eof || wb_libav_decode_finished(decode_session))
                ) {
                    copy_text(
                        state->fault_last_mode,
                        sizeof(state->fault_last_mode),
                        probe->fault_mode
                    );
                    copy_text(
                        state->fault_last_slot_token,
                        sizeof(state->fault_last_slot_token),
                        probe->track.slot_token
                    );
                    copy_text(
                        state->fault_last_terminal_reason,
                        sizeof(state->fault_last_terminal_reason),
                        "decoder_already_finished"
                    );
                    clear_probe_fault_locked(probe);
                    skipped = true;
                } else {
                    probe->fault_triggered = true;
                    probe->fault_trigger_monotonic_ms = trigger_now;
                    fault_trigger_ms = trigger_now;
                    state->fault_trigger_count += 1U;
                    copy_text(injected_fault_mode, sizeof(injected_fault_mode), probe->fault_mode);
                    claimed = true;
                }
            }
            (void)pthread_mutex_unlock(&state->lock);
            if (skipped) {
                (void)snprintf(
                    fault_extra,
                    sizeof(fault_extra),
                    "\"fault_mode\":\"%s\",\"reason\":\"decoder_already_finished\","
                    "\"fault_applied\":false,\"terminal\":false",
                    armed_fault_mode
                );
                (void)send_probe_event(
                    state, "native_audio_fault_skipped", &fault_track, probe->deck,
                    fault_decoded_samples, fault_played_samples,
                    fault_decoded_duration_ms, fault_played_duration_ms,
                    fault_position_ms, fault_ring_fill, fault_ring_capacity, fault_extra
                );
            }
            if (claimed) {
                fault_injected = true;
                (void)snprintf(
                    fault_extra,
                    sizeof(fault_extra),
                    "\"fault_mode\":\"%s\",\"fault_after_ms\":%lld,\"fault_duration_ms\":%lld,\"once\":true",
                    armed_fault_mode,
                    (long long)fault_after_ms,
                    (long long)fault_duration_ms
                );
                (void)send_probe_event(
                    state, "native_audio_fault_triggered", &fault_track, probe->deck,
                    fault_decoded_samples, fault_played_samples,
                    fault_decoded_duration_ms, fault_played_duration_ms,
                    fault_position_ms, fault_ring_fill, fault_ring_capacity, fault_extra
                );
                if (strcmp(armed_fault_mode, "early_eof") == 0) {
                    copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "early_eof");
                    fault_forced_eof = true;
                    if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                } else if (strcmp(armed_fault_mode, "kill_decoder") == 0) {
                    copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "decoder_process_exited");
                    decoder_signal = SIGKILL;
                    if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                    copy_text(error, sizeof(error), "injected embedded decoder termination");
                    break;
                } else if (
                    strcmp(armed_fault_mode, "decoder_stall") == 0
                    || strcmp(armed_fault_mode, "buffer_underrun") == 0
                ) {
                    copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "buffer_underrun");
                    fault_stalled = true;
                } else if (strcmp(armed_fault_mode, "corrupt_input") == 0) {
                    copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "corrupt_input");
                    copy_text(error, sizeof(error), "injected corrupt input");
                    if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                    break;
                } else if (strcmp(armed_fault_mode, "missing_file") == 0) {
                    copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "source_file_missing");
                    copy_text(error, sizeof(error), "injected missing source file");
                    if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                    break;
                }
            }
        }

        if (!pipe_eof && ring_space >= WB_AUDIO_FRAME_BYTES && !fault_stalled) {
            size_t wanted = ring_space < sizeof(buffer) ? ring_space : sizeof(buffer);
            ssize_t received = wb_libav_decode_read(decode_session, buffer, wanted);
            if (received > 0) {
                size_t accepted;
                (void)pthread_mutex_lock(&state->lock);
                if (probe_current_locked(probe, generation)) {
                    accepted = ring_write(probe, buffer, (size_t)received);
                    total_decoded_bytes += accepted;
                    probe->decoded_samples = total_decoded_bytes / WB_AUDIO_FRAME_BYTES;
                    probe->decoded_duration_ms = samples_to_ms(probe->decoded_samples);
                    ring_fill = probe->ring_fill;
                } else {
                    accepted = 0U;
                }
                (void)pthread_mutex_unlock(&state->lock);
                did_work = accepted > 0U;
            } else if (received == -2) {
                pipe_eof = true;
                did_work = true;
            } else if (received < 0) {
                wb_libav_decode_error(decode_session, error, sizeof(error));
                if (error[0] == '\0') copy_text(error, sizeof(error), "libav PCM decode failed");
                copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "decoder_failed");
                decoder_runtime_failed = true;
                pipe_eof = true;
                did_work = true;
                if (!activated) break;
            }
        }

        mark_prebuffer_ready(state, probe, generation, pipe_eof);

        if (activated) {
            uint64_t target_played_samples;
            uint64_t available_samples;
            uint64_t consume_samples;
            size_t discarded;
            unsigned char playback_pcm[WB_PCM_READ_BYTES];
            bool emit_started = false;
            bool emit_progress = false;
            bool emit_audio_mismatch = false;
            bool emit_seek_pending = false;
            bool emit_seek_slow = false;
            bool emit_seek_mismatch_recovered = false;
            bool emit_buffer_underrun = false;
            WbDeckState event_track = {0};
            uint64_t decoded_samples = 0U;
            uint64_t played_samples = 0U;
            int64_t decoded_duration_ms = 0;
            int64_t played_duration_ms = 0;
            int64_t source_position_ms = track.cue_in_ms;
            size_t buffered_bytes = 0U;
            size_t capacity_bytes = 0U;
            int64_t startup_delay_ms = 0;
            int64_t startup_buffer_ms = 0;
            int64_t mismatch_delay_ms = 0;
            int64_t start_timeout_ms = 0;
            int64_t hard_timeout_ms = 0;

            now_ms = monotonic_ms();
            (void)pthread_mutex_lock(&state->lock);
            if (!probe_current_locked(probe, generation)) {
                (void)pthread_mutex_unlock(&state->lock);
                stopped = true;
                if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                break;
            }
            available_samples = probe->ring_fill / WB_AUDIO_FRAME_BYTES;
            start_timeout_ms = seek_restart
                ? state->audio_seek_start_timeout_ms
                : state->audio_start_timeout_ms;
            hard_timeout_ms = seek_restart
                ? state->audio_seek_hard_timeout_ms
                : state->audio_start_timeout_ms;
            if (track.stream_source && !seek_restart) {
                if (start_timeout_ms < 15000) start_timeout_ms = 15000;
                if (hard_timeout_ms < 15000) hard_timeout_ms = 15000;
            }
            if (
                seek_restart
                && !started_emitted
                && !seek_pending_emitted
                && activation_ms > 0
                && now_ms - activation_ms >= state->audio_start_timeout_ms
                && now_ms - activation_ms < start_timeout_ms
            ) {
                seek_pending_emitted = true;
                emit_seek_pending = true;
                mismatch_delay_ms = now_ms - activation_ms;
            }
            if (
                seek_restart
                && !paused
                && !started_emitted
                && !seek_slow_emitted
                && activation_ms > 0
                && now_ms - activation_ms >= start_timeout_ms
                && now_ms - activation_ms < hard_timeout_ms
                && available_samples == 0U
            ) {
                seek_slow_emitted = true;
                emit_seek_slow = true;
                mismatch_delay_ms = now_ms - activation_ms;
            }
            if (
                !paused
                && !started_emitted
                && !probe->audio_mismatch_emitted
                && activation_ms > 0
                && now_ms - activation_ms >= hard_timeout_ms
                && available_samples == 0U
            ) {
                probe->audio_mismatch_emitted = true;
                state->audio_runtime_mismatch_count += 1U;
                state->audio_runtime_mismatch_total_count += 1U;
                if (seek_restart) seek_timeout_mismatch_active = true;
                emit_audio_mismatch = true;
                mismatch_delay_ms = now_ms - activation_ms;
            }
            if (paused) {
                consume_samples = 0U;
            } else if (state->audio_probe_realtime) {
                int64_t elapsed = now_ms - activation_ms;
                if (elapsed < 0) elapsed = 0;
                target_played_samples = ((uint64_t)elapsed * WB_AUDIO_SAMPLE_RATE) / 1000ULL;
                consume_samples = target_played_samples > probe->played_samples
                    ? target_played_samples - probe->played_samples
                    : 0U;
            } else {
                consume_samples = available_samples;
            }
            if (
                fault_stalled
                && started_emitted
                && (
                    (consume_samples > available_samples && available_samples == 0U)
                    || (
                        fault_duration_ms > 0
                        && fault_trigger_ms > 0
                        && now_ms - fault_trigger_ms >= fault_duration_ms
                    )
                )
            ) {
                emit_buffer_underrun = true;
                state->audio_buffer_underrun_count += 1U;
                copy_text(probe->status, sizeof(probe->status), "underrun");
            }
            if (consume_samples > available_samples) consume_samples = available_samples;
            if (consume_samples * WB_AUDIO_FRAME_BYTES > sizeof(playback_pcm)) {
                consume_samples = sizeof(playback_pcm) / WB_AUDIO_FRAME_BYTES;
            }
            discarded = ring_read_pcm(
                probe,
                playback_pcm,
                (size_t)(consume_samples * WB_AUDIO_FRAME_BYTES)
            );
            if (discarded > 0U) {
                uint64_t consumed = discarded / WB_AUDIO_FRAME_BYTES;
                probe->played_samples += consumed;
                probe->played_duration_ms = samples_to_ms(probe->played_samples);
                probe->position_ms = track.cue_in_ms + probe->played_duration_ms;
                copy_text(probe->status, sizeof(probe->status), "decoding");
                if (!started_emitted) {
                    started_emitted = true;
                    probe->first_sample_monotonic_ms = now_ms;
                    emit_started = true;
                    startup_delay_ms = now_ms - activation_ms;
                    startup_buffer_ms = (int64_t)(((ring_fill / WB_AUDIO_FRAME_BYTES) * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
                    if (seek_restart && seek_timeout_mismatch_active) {
                        if (state->audio_runtime_mismatch_count > 0U) {
                            state->audio_runtime_mismatch_count -= 1U;
                        }
                        state->audio_runtime_mismatch_recovered_count += 1U;
                        probe->audio_mismatch_emitted = false;
                        seek_timeout_mismatch_active = false;
                        emit_seek_mismatch_recovered = true;
                    }
                }
                if (probe->played_duration_ms >= next_progress_ms) emit_progress = true;
            } else if (!probe->prebuffer_ready) {
                copy_text(probe->status, sizeof(probe->status), "buffering");
            }
            event_track = probe->track;
            decoded_samples = probe->decoded_samples;
            played_samples = probe->played_samples;
            decoded_duration_ms = probe->decoded_duration_ms;
            played_duration_ms = probe->played_duration_ms;
            source_position_ms = probe->position_ms;
            buffered_bytes = probe->ring_fill;
            capacity_bytes = probe->ring_capacity;
            (void)pthread_mutex_unlock(&state->lock);

            if (discarded > 0U) {
                wb_icecast_output_push_pcm(
                    state,
                    probe->deck,
                    &event_track,
                    playback_pcm,
                    discarded,
                    seek_restart
                );
            }

            if (emit_buffer_underrun) {
                char underrun_extra[384];
                copy_text(error, sizeof(error), "injected ring-buffer underrun");
                copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "buffer_underrun");
                if (decode_session != NULL) wb_libav_decode_abort(decode_session);
                (void)snprintf(
                    underrun_extra, sizeof(underrun_extra),
                    "\"diagnostic_reason\":\"buffer_underrun\",\"terminal\":false,"
                    "\"fault_injected\":true,\"fault_mode\":\"%s\"",
                    armed_fault_mode
                );
                (void)send_probe_event(
                    state, "native_audio_probe_underrun", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, underrun_extra
                );
                break;
            }

            if (discarded > 0U) did_work = true;
            if (emit_seek_pending) {
                char pending_extra[384];
                (void)snprintf(
                    pending_extra,
                    sizeof(pending_extra),
                    "\"reason\":\"seek_restart_pending\",\"pending\":true,\"terminal\":false,"
                    "\"elapsed_ms\":%lld,\"timeout_ms\":%lld,\"exact_identity\":true",
                    (long long)mismatch_delay_ms,
                    (long long)start_timeout_ms
                );
                (void)send_probe_event(
                    state, "native_audio_probe_seek_pending", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, pending_extra
                );
            }
            if (emit_seek_slow) {
                char slow_extra[448];
                (void)snprintf(
                    slow_extra,
                    sizeof(slow_extra),
                    "\"reason\":\"seek_restart_slow\",\"pending\":true,\"terminal\":false,"
                    "\"elapsed_ms\":%lld,\"slow_threshold_ms\":%lld,\"hard_timeout_ms\":%lld,"
                    "\"exact_identity\":true",
                    (long long)mismatch_delay_ms,
                    (long long)start_timeout_ms,
                    (long long)hard_timeout_ms
                );
                (void)send_probe_event(
                    state, "native_audio_probe_seek_slow", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, slow_extra
                );
            }
            if (emit_audio_mismatch) {
                char mismatch_extra[384];
                (void)snprintf(
                    mismatch_extra,
                    sizeof(mismatch_extra),
                    "\"reason\":\"%s\",\"timeout_ms\":%lld,\"startup_delay_ms\":%lld,"
                    "\"seek_restart\":%s,\"exact_identity\":true",
                    seek_restart ? "seek_restart_timeout" : "voice_start_timeout",
                    (long long)hard_timeout_ms,
                    (long long)mismatch_delay_ms,
                    seek_restart ? "true" : "false"
                );
                (void)send_probe_event(
                    state, "native_audio_runtime_mismatch", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, mismatch_extra
                );
            }
            if (emit_started) {
                char extra[320];
                (void)snprintf(
                    extra,
                    sizeof(extra),
                    "\"clock_mode\":\"%s\",\"startup_delay_ms\":%lld,\"startup_buffer_ms\":%lld,\"prebuffered\":%s,"
                    "\"seek_restart\":%s,\"seek_from_position_ms\":%lld,\"seek_target_position_ms\":%lld,\"terminal\":false",
                    state->audio_probe_realtime ? "realtime" : "unthrottled",
                    (long long)startup_delay_ms,
                    (long long)startup_buffer_ms,
                    probe->prebuffer_ready ? "true" : "false",
                    seek_restart ? "true" : "false",
                    (long long)seek_from_position_ms,
                    (long long)seek_target_position_ms
                );
                (void)send_probe_event(
                    state, seek_restart ? "native_audio_probe_seek_applied" : "native_audio_probe_started", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, extra
                );
            }
            if (emit_seek_mismatch_recovered) {
                char recovered_extra[384];
                (void)snprintf(
                    recovered_extra,
                    sizeof(recovered_extra),
                    "\"reason\":\"seek_restart_completed\",\"recovered\":true,\"terminal\":false,"
                    "\"timeout_ms\":%lld,\"startup_delay_ms\":%lld,\"exact_identity\":true",
                    (long long)start_timeout_ms,
                    (long long)startup_delay_ms
                );
                (void)send_probe_event(
                    state, "native_audio_runtime_mismatch_recovered", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, recovered_extra
                );
            }
            if (emit_progress) {
                while (played_duration_ms >= next_progress_ms) next_progress_ms += WB_PROGRESS_INTERVAL_MS;
                (void)send_probe_event(
                    state, "native_audio_probe_progress", &event_track, probe->deck,
                    decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                    source_position_ms, buffered_bytes, capacity_bytes, NULL
                );
            }
        }

        (void)pthread_mutex_lock(&state->lock);
        ring_fill = probe->ring_fill;
        activated = probe->activated;
        (void)pthread_mutex_unlock(&state->lock);
        if (pipe_eof && activated && ring_fill < WB_AUDIO_FRAME_BYTES) break;

        if (!did_work) {
            struct timespec delay = {.tv_sec = 0, .tv_nsec = 5000000L};
            (void)nanosleep(&delay, NULL);
        }
    }

    if (decode_session != NULL) {
        corrupt_input_skip_count = wb_libav_decode_invalid_data_skip_count(decode_session);
        if (!pipe_eof || stopped || error[0] != '\0') wb_libav_decode_abort(decode_session);
        wb_libav_decode_destroy(decode_session);
        decode_session = NULL;
    }
    if (stopped) {
        bool emit_stopped = false;
        WbDeckState event_track = {0};
        uint64_t decoded_samples = 0U;
        uint64_t played_samples = 0U;
        int64_t decoded_duration_ms = 0;
        int64_t played_duration_ms = 0;
        int64_t position_ms = track.cue_in_ms;
        size_t fill = 0U;
        size_t capacity = 0U;
        int64_t seek_target_position_ms = 0;
        char reason[WB_EVENT_NAME_SIZE] = "superseded_or_stopped";
        char escaped_reason[WB_EVENT_NAME_SIZE * 2];
        char extra[640];

        (void)pthread_mutex_lock(&state->lock);
        if (probe_current_locked(probe, generation)) {
            event_track = probe->track;
            decoded_samples = probe->decoded_samples;
            played_samples = probe->played_samples;
            decoded_duration_ms = probe->decoded_duration_ms;
            played_duration_ms = probe->played_duration_ms;
            position_ms = probe->position_ms;
            fill = probe->ring_fill;
            capacity = probe->ring_capacity;
            copy_text(reason, sizeof(reason), probe->stop_reason[0] ? probe->stop_reason : "superseded_or_stopped");
            seek_target_position_ms = probe->replacement_track.cue_in_ms;
            emit_stopped = started_emitted || preload_started_emitted;
            set_terminal_status_locked(probe, "stopped", "", false, true);
            probe->request_pending = false;
            probe->stop_requested = false;
            finish_probe_fault_locked(state, probe, reason);
        }
        (void)pthread_mutex_unlock(&state->lock);
        if (emit_stopped) {
            const char *terminal_event;
            wb_json_escape(reason, escaped_reason, sizeof(escaped_reason));
            if (strcmp(reason, "seek_reposition") == 0) {
                terminal_event = "native_audio_probe_seek_restarting";
            } else if (started_emitted) {
                terminal_event = "native_audio_probe_stopped";
            } else if (strcmp(reason, "engine_stop") == 0 || strcmp(reason, "track_ended") == 0) {
                terminal_event = "native_audio_candidate_cancelled";
                (void)pthread_mutex_lock(&state->lock);
                state->audio_candidate_cancelled_count += 1U;
                (void)pthread_mutex_unlock(&state->lock);
            } else {
                terminal_event = "native_audio_candidate_evicted";
                (void)pthread_mutex_lock(&state->lock);
                state->audio_candidate_evicted_count += 1U;
                (void)pthread_mutex_unlock(&state->lock);
            }
            (void)snprintf(
                extra,
                sizeof(extra),
                "\"reason\":\"%s\",\"playback_started\":%s,\"candidate_serial\":%llu,"
                "\"terminal\":%s,\"seek_from_position_ms\":%lld,\"seek_target_position_ms\":%lld",
                escaped_reason,
                started_emitted ? "true" : "false",
                (unsigned long long)probe->candidate_serial,
                strcmp(reason, "seek_reposition") == 0 ? "false" : "true",
                (long long)position_ms,
                (long long)seek_target_position_ms
            );
            (void)send_probe_event(
                state, terminal_event, &event_track, probe->deck,
                decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                position_ms, fill, capacity, extra
            );
        }
        if (strcmp(reason, "engine_stop") == 0) {
            (void)pthread_mutex_lock(&state->lock);
            if (probe_current_locked(probe, generation)) clear_candidate_locked(probe);
            (void)pthread_mutex_unlock(&state->lock);
        }
        return;
    }

    if (error[0] != '\0') {
        decoder_exit_code = 1;
        if (fault_terminal_reason[0] == '\0') {
            copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "decoder_failed");
        }
    }
    if (error[0] != '\0') {
        WbDeckState event_track = {0};
        uint64_t decoded_samples = 0U;
        uint64_t played_samples = 0U;
        int64_t decoded_duration_ms = 0;
        int64_t played_duration_ms = 0;
        int64_t position_ms = track.cue_in_ms;
        size_t fill = 0U;
        size_t capacity = 0U;
        int64_t expected_duration_ms = 0;
        int64_t early_by_ms = 0;
        bool terminal_recovery = false;
        char escaped[WB_AUDIO_ERROR_SIZE * 2];
        char escaped_reason[WB_FAULT_REASON_SIZE * 2];
        char escaped_mode[WB_FAULT_MODE_SIZE * 2];
        char extra[WB_AUDIO_ERROR_SIZE * 2 + WB_FAULT_REASON_SIZE * 2 + 512];
        (void)pthread_mutex_lock(&state->lock);
        if (probe_current_locked(probe, generation)) {
            event_track = probe->track;
            decoded_samples = probe->decoded_samples;
            played_samples = probe->played_samples;
            decoded_duration_ms = probe->decoded_duration_ms;
            played_duration_ms = probe->played_duration_ms;
            position_ms = probe->position_ms;
            fill = probe->ring_fill;
            capacity = probe->ring_capacity;
            terminal_recovery = decoder_runtime_failed && started_emitted && !fault_injected;
            set_terminal_status_locked(
                probe,
                "error",
                error,
                terminal_recovery,
                true
            );
            finish_probe_fault_locked(state, probe, fault_terminal_reason);
            if (terminal_recovery) {
                WbDeckState *live = probe->deck == 'B' ? &state->deck_b : &state->deck_a;
                if (live->loaded && strcmp(live->slot_token, event_track.slot_token) == 0) {
                    live->terminal = true;
                }
                expected_duration_ms = track_expected_content_duration_ms(&event_track);
                early_by_ms = expected_duration_ms > played_duration_ms
                    ? expected_duration_ms - played_duration_ms
                    : 0;
            }
        }
        (void)pthread_mutex_unlock(&state->lock);
        if (fault_terminal_reason[0] == '\0') {
            copy_text(fault_terminal_reason, sizeof(fault_terminal_reason), "decoder_error");
        }
        wb_json_escape(error, escaped, sizeof(escaped));
        wb_json_escape(fault_terminal_reason, escaped_reason, sizeof(escaped_reason));
        wb_json_escape(injected_fault_mode, escaped_mode, sizeof(escaped_mode));
        (void)snprintf(
            extra,
            sizeof(extra),
            "\"error\":\"%s\",\"terminal_reason\":\"%s\","
            "\"fault_injected\":%s,\"fault_mode\":\"%s\","
            "\"decoder_exit_code\":%d,\"decoder_signal\":%d,"
            "\"terminal_recovery\":%s,\"expected_duration_ms\":%lld,"
            "\"early_by_ms\":%lld,\"buffer_drained_before_terminal\":%s,"
            "\"corrupt_input_skipped_count\":%llu",
            escaped,
            escaped_reason,
            fault_injected ? "true" : "false",
            escaped_mode,
            decoder_exit_code,
            decoder_signal,
            terminal_recovery ? "true" : "false",
            (long long)expected_duration_ms,
            (long long)early_by_ms,
            terminal_recovery ? "true" : "false",
            (unsigned long long)corrupt_input_skip_count
        );
        (void)send_probe_event(
            state, "native_audio_probe_error", &event_track, probe->deck,
            decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
            position_ms, fill, capacity, extra
        );
        if (terminal_recovery) {
            (void)wb_icecast_output_handle_terminal_eof(
                state,
                probe->deck,
                &event_track,
                early_by_ms,
                true
            );
            wb_native_timing_wake(state);
        }
        return;
    }

    {
        WbDeckState event_track = {0};
        uint64_t decoded_samples = 0U;
        uint64_t played_samples = 0U;
        int64_t decoded_duration_ms = 0;
        int64_t played_duration_ms = 0;
        int64_t position_ms = track.cue_in_ms;
        int64_t expected_duration_ms = 0;
        size_t capacity = 0U;
        bool early_eof;
        char extra[512];

        (void)pthread_mutex_lock(&state->lock);
        if (!probe_current_locked(probe, generation)) {
            (void)pthread_mutex_unlock(&state->lock);
            return;
        }
        event_track = probe->track;
        decoded_samples = probe->decoded_samples;
        played_samples = probe->played_samples;
        decoded_duration_ms = probe->decoded_duration_ms;
        played_duration_ms = probe->played_duration_ms;
        position_ms = probe->position_ms;
        capacity = probe->ring_capacity;
        set_terminal_status_locked(probe, "eof", "", true, true);
        probe->request_pending = false;
        expected_duration_ms = track_expected_content_duration_ms(&track);
        early_eof = fault_forced_eof || (
            expected_duration_ms > 0
            && played_duration_ms + WB_EARLY_EOF_TOLERANCE_MS < expected_duration_ms
        );
        finish_probe_fault_locked(
            state,
            probe,
            early_eof ? "early_eof" : "natural_eof"
        );
        {
            WbDeckState *live = probe->deck == 'B' ? &state->deck_b : &state->deck_a;
            if (live->loaded && strcmp(live->slot_token, event_track.slot_token) == 0) {
                live->terminal = true;
            }
        }
        (void)pthread_mutex_unlock(&state->lock);

        if (early_eof) {
            int64_t early_by_ms = expected_duration_ms > played_duration_ms
                ? expected_duration_ms - played_duration_ms
                : 0;
            wb_icecast_output_handle_early_eof(
                state,
                probe->deck,
                &event_track,
                early_by_ms
            );
            (void)snprintf(
                extra,
                sizeof(extra),
                "\"expected_duration_ms\":%lld,\"early_by_ms\":%lld,\"final_actual_duration_ms\":%lld,"
                "\"terminal_reason\":\"early_eof\",\"fault_injected\":%s,\"fault_mode\":\"%s\","
                "\"decoder_exit_code\":%d,\"decoder_signal\":%d,"
                "\"corrupt_input_skipped_count\":%llu",
                (long long)expected_duration_ms,
                (long long)(expected_duration_ms > played_duration_ms ? expected_duration_ms - played_duration_ms : 0),
                (long long)played_duration_ms,
                fault_injected ? "true" : "false",
                fault_injected ? injected_fault_mode : "",
                decoder_exit_code,
                decoder_signal,
                (unsigned long long)corrupt_input_skip_count
            );
            (void)send_probe_event(
                state, "native_audio_probe_early_eof", &event_track, probe->deck,
                decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                position_ms, 0U, capacity, extra
            );
        } else {
            bool handoff_claimed = wb_icecast_output_handle_terminal_eof(
                state,
                probe->deck,
                &event_track,
                0,
                false
            );
            if (!handoff_claimed) {
                wb_icecast_output_stop_track(
                    state,
                    probe->deck,
                    event_track.queue_id,
                    event_track.slot_token
                );
            }
            (void)snprintf(
                extra,
                sizeof(extra),
                "\"expected_duration_ms\":%lld,\"final_actual_duration_ms\":%lld,"
                "\"terminal_reason\":\"natural_eof\",\"output_track_released\":%s,"
                "\"hard_handoff_claimed\":%s,\"fault_injected\":false,"
                "\"decoder_exit_code\":%d,\"decoder_signal\":%d,"
                "\"corrupt_input_skipped_count\":%llu",
                (long long)expected_duration_ms,
                (long long)played_duration_ms,
                handoff_claimed ? "false" : "true",
                handoff_claimed ? "true" : "false",
                decoder_exit_code,
                decoder_signal,
                (unsigned long long)corrupt_input_skip_count
            );
            (void)send_probe_event(
                state, "native_audio_probe_eof", &event_track, probe->deck,
                decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
                position_ms, 0U, capacity, extra
            );
        }
        wb_native_timing_wake(state);
    }
    return;

failed_before_child:
    (void)pthread_mutex_lock(&state->lock);
    if (probe_current_locked(probe, generation)) {
        set_terminal_status_locked(probe, "error", error, false, false);
        finish_probe_fault_locked(state, probe, "decoder_start_failed");
    }
    (void)pthread_mutex_unlock(&state->lock);
    {
        char escaped[WB_AUDIO_ERROR_SIZE * 2];
        char extra[WB_AUDIO_ERROR_SIZE * 2 + 64];
        wb_json_escape(error, escaped, sizeof(escaped));
        (void)snprintf(extra, sizeof(extra), "\"error\":\"%s\"", escaped);
        (void)send_probe_event(
            state, "native_audio_probe_error", &track, probe->deck,
            0U, 0U, 0, 0, track.cue_in_ms, 0U, probe->ring_capacity, extra
        );
    }
}

static void *audio_deck_thread_main(void *context) {
    WbAudioDeckProbe *probe = context;
    WbEngineState *state = probe->owner;
    for (;;) {
        uint64_t generation;
        WbDeckState track;

        (void)pthread_mutex_lock(&state->lock);
        if (!probe->request_pending) (void)promote_replacement_locked(state, probe);
        while (!probe->shutdown && !probe->request_pending) {
            (void)pthread_cond_wait(&probe->cond, &state->lock);
            if (!probe->request_pending) (void)promote_replacement_locked(state, probe);
        }
        if (probe->shutdown) {
            (void)pthread_mutex_unlock(&state->lock);
            return NULL;
        }
        probe->request_pending = false;
        generation = probe->generation;
        track = probe->track;
        (void)pthread_mutex_unlock(&state->lock);

        run_decode(state, probe, generation, track);

        (void)pthread_mutex_lock(&state->lock);
        (void)promote_replacement_locked(state, probe);
        (void)pthread_mutex_unlock(&state->lock);
    }
}

static int init_deck_probe(WbEngineState *state, WbAudioDeckProbe *probe, char deck, unsigned slot_index, size_t capacity, size_t prebuffer) {
    memset(probe, 0, sizeof(*probe));
    probe->owner = state;
    probe->deck = deck;
    probe->slot_index = slot_index;
    probe->ring_capacity = capacity;
    probe->prebuffer_target_bytes = prebuffer > capacity ? capacity : prebuffer;
    probe->ring_buffer = malloc(capacity);
    if (probe->ring_buffer == NULL) {
        copy_text(probe->status, sizeof(probe->status), "memory_error");
        return -1;
    }
    if (pthread_cond_init(&probe->cond, NULL) != 0) {
        free(probe->ring_buffer);
        probe->ring_buffer = NULL;
        copy_text(probe->status, sizeof(probe->status), "cond_error");
        return -1;
    }
    copy_text(probe->status, sizeof(probe->status), "idle");
    if (pthread_create(&probe->thread, NULL, audio_deck_thread_main, probe) != 0) {
        (void)pthread_cond_destroy(&probe->cond);
        free(probe->ring_buffer);
        probe->ring_buffer = NULL;
        copy_text(probe->status, sizeof(probe->status), "thread_error");
        return -1;
    }
    probe->thread_created = true;
    return 0;
}

int wb_audio_probe_init(WbEngineState *state) {
    size_t capacity;
    size_t prebuffer;
    int a_result;
    int a_alt_result;
    int b_result;
    int b_alt_result;

    if (configure_ffmpeg_runtime(state) != 0) {
        state->audio_probe_enabled = false;
        copy_text(state->audio_deck_a.status, sizeof(state->audio_deck_a.status), "ffmpeg_runtime_error");
        copy_text(state->audio_deck_a_alt.status, sizeof(state->audio_deck_a_alt.status), "ffmpeg_runtime_error");
        copy_text(state->audio_deck_b.status, sizeof(state->audio_deck_b.status), "ffmpeg_runtime_error");
        copy_text(state->audio_deck_b_alt.status, sizeof(state->audio_deck_b_alt.status), "ffmpeg_runtime_error");
        return -1;
    }
    state->audio_probe_enabled = env_enabled("WEB_BROADCASTER_NATIVE_AUDIO_PROBE", true);
    state->audio_probe_realtime = env_enabled("WEB_BROADCASTER_NATIVE_AUDIO_REALTIME", true);
    state->audio_probe_sample_rate = WB_AUDIO_SAMPLE_RATE;
    state->audio_probe_channels = WB_AUDIO_CHANNELS;
    state->fault_enabled = false;
    state->fault_once = true;
    state->fault_after_ms = 3000;
    state->fault_duration_ms = 6000;
    state->audio_ring_capacity_ms = env_int(
        "WEB_BROADCASTER_NATIVE_AUDIO_RING_MS",
        WB_DEFAULT_RING_CAPACITY_MS,
        WB_MIN_RING_CAPACITY_MS,
        WB_MAX_RING_CAPACITY_MS
    );
    state->audio_prebuffer_ms = env_int(
        "WEB_BROADCASTER_NATIVE_AUDIO_PREBUFFER_MS",
        WB_DEFAULT_PREBUFFER_MS,
        0,
        state->audio_ring_capacity_ms
    );
    state->audio_start_timeout_ms = env_int(
        "WEB_BROADCASTER_NATIVE_AUDIO_START_TIMEOUT_MS",
        WB_DEFAULT_AUDIO_START_TIMEOUT_MS,
        WB_MIN_AUDIO_START_TIMEOUT_MS,
        WB_MAX_AUDIO_START_TIMEOUT_MS
    );
    state->audio_seek_start_timeout_ms = env_int(
        "WEB_BROADCASTER_NATIVE_AUDIO_SEEK_START_TIMEOUT_MS",
        WB_DEFAULT_AUDIO_SEEK_START_TIMEOUT_MS,
        state->audio_start_timeout_ms,
        WB_MAX_AUDIO_START_TIMEOUT_MS
    );
    state->audio_seek_hard_timeout_ms = env_int(
        "WEB_BROADCASTER_NATIVE_AUDIO_SEEK_HARD_TIMEOUT_MS",
        WB_DEFAULT_AUDIO_SEEK_HARD_TIMEOUT_MS,
        state->audio_seek_start_timeout_ms,
        WB_MAX_AUDIO_START_TIMEOUT_MS
    );
    if (!state->audio_probe_enabled) {
        copy_text(state->audio_deck_a.status, sizeof(state->audio_deck_a.status), "disabled");
        copy_text(state->audio_deck_a_alt.status, sizeof(state->audio_deck_a_alt.status), "disabled");
        copy_text(state->audio_deck_b.status, sizeof(state->audio_deck_b.status), "disabled");
        copy_text(state->audio_deck_b_alt.status, sizeof(state->audio_deck_b_alt.status), "disabled");
        return 0;
    }

    capacity = ms_to_bytes(state->audio_ring_capacity_ms);
    capacity -= capacity % WB_AUDIO_FRAME_BYTES;
    prebuffer = ms_to_bytes(state->audio_prebuffer_ms);
    prebuffer -= prebuffer % WB_AUDIO_FRAME_BYTES;
    if (capacity < WB_AUDIO_FRAME_BYTES) capacity = WB_AUDIO_FRAME_BYTES;

    a_result = init_deck_probe(state, &state->audio_deck_a, 'A', 0U, capacity, prebuffer);
    a_alt_result = init_deck_probe(state, &state->audio_deck_a_alt, 'A', 1U, capacity, prebuffer);
    b_result = init_deck_probe(state, &state->audio_deck_b, 'B', 0U, capacity, prebuffer);
    b_alt_result = init_deck_probe(state, &state->audio_deck_b_alt, 'B', 1U, capacity, prebuffer);
    if (a_result != 0 || a_alt_result != 0 || b_result != 0 || b_alt_result != 0) {
        state->audio_probe_enabled = false;
        wb_audio_probe_destroy(state);
        return -1;
    }
    return 0;
}

typedef struct {
    bool emit;
    WbDeckState track;
    char deck;
    uint64_t decoded_samples;
    uint64_t played_samples;
    int64_t decoded_duration_ms;
    int64_t played_duration_ms;
    int64_t position_ms;
    size_t ring_fill;
    size_t ring_capacity;
    uint64_t candidate_serial;
    char reason[WB_EVENT_NAME_SIZE];
} WbCandidateNotice;

static void emit_candidate_notice(WbEngineState *state, const WbCandidateNotice *notice) {
    char escaped_reason[WB_EVENT_NAME_SIZE * 2];
    char extra[WB_EVENT_NAME_SIZE * 2 + 128];
    if (notice == NULL || !notice->emit) return;
    wb_json_escape(notice->reason, escaped_reason, sizeof(escaped_reason));
    (void)snprintf(
        extra,
        sizeof(extra),
        "\"reason\":\"%s\",\"playback_started\":false,\"candidate_serial\":%llu",
        escaped_reason,
        (unsigned long long)notice->candidate_serial
    );
    (void)send_probe_event(
        state,
        "native_audio_candidate_evicted",
        &notice->track,
        notice->deck,
        notice->decoded_samples,
        notice->played_samples,
        notice->decoded_duration_ms,
        notice->played_duration_ms,
        notice->position_ms,
        notice->ring_fill,
        notice->ring_capacity,
        extra
    );
}

static WbAudioDeckProbe *find_current_identity_locked(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    ssize_t *matched_alias_index
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    probe_pair(state, deck, &first, &second);
    if (probe_identity_matches(first, track, matched_alias_index)) return first;
    if (probe_identity_matches(second, track, matched_alias_index)) return second;
    if (matched_alias_index != NULL) *matched_alias_index = -1;
    return NULL;
}

static WbAudioDeckProbe *find_shareable_content_locked(
    WbEngineState *state,
    char deck,
    const WbDeckState *track
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    probe_pair(state, deck, &first, &second);
    if (same_audio_content(&first->track, track)
        && probe_has_live_shareable_content(first)) return first;
    if (same_audio_content(&second->track, track)
        && probe_has_live_shareable_content(second)) return second;
    return NULL;
}

static WbAudioDeckProbe *find_replacement_identity_locked(
    WbEngineState *state,
    char deck,
    const WbDeckState *track
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    probe_pair(state, deck, &first, &second);
    if (first->replacement_pending && identity_matches(&first->replacement_track, track)) return first;
    if (second->replacement_pending && identity_matches(&second->replacement_track, track)) return second;
    return NULL;
}

static WbAudioDeckProbe *choose_candidate_slot_locked(WbEngineState *state, char deck) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    probe_pair(state, deck, &first, &second);

    if (probe_is_reusable(first) && probe_is_reusable(second)) {
        return first->candidate_serial <= second->candidate_serial ? first : second;
    }
    if (probe_is_reusable(first)) return first;
    if (probe_is_reusable(second)) return second;

    /* A full table evicts only the oldest inactive candidate. Active playback
       voices are never sacrificed to make room for a preload. */
    if (!first->activated && !second->activated) {
        return first->candidate_serial <= second->candidate_serial ? first : second;
    }
    if (!first->activated) return first;
    if (!second->activated) return second;
    return NULL;
}

static void clear_candidate_locked(WbAudioDeckProbe *probe) {
    probe->generation += 1U;
    probe->request_pending = false;
    probe->replacement_pending = false;
    probe->replacement_activate = false;
    probe->replacement_is_seek = false;
    probe->replacement_activation_monotonic_ms = 0;
    probe->seek_restart_generation = 0U;
    probe->seek_from_position_ms = 0;
    probe->seek_target_position_ms = 0;
    memset(&probe->replacement_track, 0, sizeof(probe->replacement_track));
    probe->activated = false;
    probe->stop_requested = false;
    probe->running = false;
    probe->child_pid = 0;
    probe->prebuffer_ready = false;
    probe->final_duration_valid = false;
    probe->audio_mismatch_emitted = false;
    clear_probe_fault_locked(probe);
    probe->requested_activation_monotonic_ms = 0;
    probe->activation_monotonic_ms = 0;
    probe->first_sample_monotonic_ms = 0;
    memset(&probe->track, 0, sizeof(probe->track));
    clear_aliases(probe);
    ring_reset(probe);
    copy_text(probe->status, sizeof(probe->status), "idle");
    probe->error[0] = '\0';
    probe->stop_reason[0] = '\0';
}

static void request_candidate_retire_locked(
    WbEngineState *state,
    WbAudioDeckProbe *probe,
    const char *reason,
    pid_t *child_pid,
    WbCandidateNotice *notice
) {
    if (probe == NULL || probe->activated || !probe->track.loaded) return;
    if (child_pid != NULL) *child_pid = probe->child_pid;
    if (probe->running || probe->child_pid > 0 || probe->request_pending) {
        probe->stop_requested = true;
        probe->request_pending = false;
        copy_text(probe->stop_reason, sizeof(probe->stop_reason), reason);
        copy_text(probe->status, sizeof(probe->status), "stopping");
        (void)pthread_cond_signal(&probe->cond);
        return;
    }
    if (notice != NULL) {
        notice->emit = true;
        notice->track = probe->track;
        notice->deck = probe->deck;
        notice->decoded_samples = probe->decoded_samples;
        notice->played_samples = probe->played_samples;
        notice->decoded_duration_ms = probe->decoded_duration_ms;
        notice->played_duration_ms = probe->played_duration_ms;
        notice->position_ms = probe->position_ms;
        notice->ring_fill = probe->ring_fill;
        notice->ring_capacity = probe->ring_capacity;
        notice->candidate_serial = probe->candidate_serial;
        copy_text(notice->reason, sizeof(notice->reason), reason);
    }
    state->audio_candidate_evicted_count += 1U;
    clear_candidate_locked(probe);
}

static void retire_unselected_after_activation_locked(
    WbEngineState *state,
    char deck,
    WbAudioDeckProbe *selected,
    const WbDeckState *selected_track,
    pid_t *retired_pid,
    WbCandidateNotice *notice
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    WbAudioDeckProbe *other;
    bool same_identity_family;
    probe_pair(state, deck, &first, &second);
    other = selected == first ? second : first;
    if (other == NULL || other->activated || !other->track.loaded) return;
    same_identity_family = other->track.queue_id == selected_track->queue_id
        || same_audio_content(&other->track, selected_track);
    if (same_identity_family || other->candidate_serial < selected->candidate_serial) {
        request_candidate_retire_locked(
            state,
            other,
            same_identity_family ? "unselected_after_track_started" : "stale_after_track_started",
            retired_pid,
            notice
        );
    }
}

static void emit_no_candidate_mismatch(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    const char *reason
) {
    char escaped_reason[WB_EVENT_NAME_SIZE * 2];
    char extra[512];
    wb_json_escape(reason, escaped_reason, sizeof(escaped_reason));
    (void)pthread_mutex_lock(&state->lock);
    state->audio_runtime_mismatch_count += 1U;
    state->audio_runtime_mismatch_total_count += 1U;
    (void)pthread_mutex_unlock(&state->lock);
    (void)snprintf(
        extra,
        sizeof(extra),
        "\"reason\":\"%s\",\"timeout_ms\":%lld,\"exact_identity\":true,\"candidate_table_full\":true",
        escaped_reason,
        (long long)state->audio_start_timeout_ms
    );
    (void)send_probe_event(
        state,
        "native_audio_runtime_mismatch",
        track,
        deck,
        0U,
        0U,
        0,
        0,
        track->cue_in_ms,
        0U,
        0U,
        extra
    );
}

static void emit_descriptor_mismatch(
    WbEngineState *state,
    char deck,
    const WbDeckState *expected,
    const WbDeckState *actual
) {
    char expected_path[WB_PATH_SIZE * 2];
    char actual_path[WB_PATH_SIZE * 2];
    char extra[WB_PATH_SIZE * 4 + 1024];
    wb_json_escape(expected == NULL ? "" : expected->path, expected_path, sizeof(expected_path));
    wb_json_escape(actual == NULL ? "" : actual->path, actual_path, sizeof(actual_path));
    (void)pthread_mutex_lock(&state->lock);
    state->audio_runtime_mismatch_count += 1U;
    state->audio_runtime_mismatch_total_count += 1U;
    (void)pthread_mutex_unlock(&state->lock);
    (void)snprintf(
        extra,
        sizeof(extra),
        "\"reason\":\"descriptor_mismatch\",\"exact_identity\":true,"
        "\"expected_path\":\"%s\",\"actual_path\":\"%s\","
        "\"expected_cue_in_ms\":%lld,\"actual_cue_in_ms\":%lld,"
        "\"expected_cue_out_ms\":%lld,\"actual_cue_out_ms\":%lld,"
        "\"expected_play_start_ms\":%lld,\"actual_play_start_ms\":%lld,"
        "\"expected_transition_at_ms\":%lld,\"actual_transition_at_ms\":%lld,"
        "\"expected_effective_end_ms\":%lld,\"actual_effective_end_ms\":%lld,"
        "\"expected_source_end_ms\":%lld,\"actual_source_end_ms\":%lld,"
        "\"expected_fade_in_ms\":%lld,\"actual_fade_in_ms\":%lld,"
        "\"expected_fade_out_ms\":%lld,\"actual_fade_out_ms\":%lld",
        expected_path,
        actual_path,
        (long long)(expected == NULL ? 0 : expected->cue_in_ms),
        (long long)(actual == NULL ? 0 : actual->cue_in_ms),
        (long long)(expected == NULL ? 0 : expected->cue_out_ms),
        (long long)(actual == NULL ? 0 : actual->cue_out_ms),
        (long long)(expected == NULL ? 0 : track_play_start_ms(expected)),
        (long long)(actual == NULL ? 0 : track_play_start_ms(actual)),
        (long long)(expected == NULL ? 0 : track_transition_at_ms(expected)),
        (long long)(actual == NULL ? 0 : track_transition_at_ms(actual)),
        (long long)(expected == NULL ? 0 : track_effective_end_ms(expected)),
        (long long)(actual == NULL ? 0 : track_effective_end_ms(actual)),
        (long long)(expected == NULL ? 0 : track_source_end_ms(expected)),
        (long long)(actual == NULL ? 0 : track_source_end_ms(actual)),
        (long long)(expected == NULL ? 0 : expected->fade_in_ms),
        (long long)(actual == NULL ? 0 : actual->fade_in_ms),
        (long long)(expected == NULL ? 0 : expected->fade_out_ms),
        (long long)(actual == NULL ? 0 : actual->fade_out_ms)
    );
    (void)send_probe_event(
        state,
        "native_audio_runtime_mismatch",
        expected,
        deck,
        0U,
        0U,
        0,
        0,
        expected == NULL ? 0 : expected->cue_in_ms,
        0U,
        0U,
        extra
    );
}

static void prepare_or_activate_deck(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    bool activate,
    bool validate_descriptor,
    int64_t requested_activation_monotonic_ms
) {
    WbAudioDeckProbe *probe;
    WbAudioDeckProbe *replacement_probe;
    pid_t retired_pid = 0;
    pid_t child_pid = 0;
    ssize_t matched_alias_index = -1;
    bool emit_shared_preload = false;
    bool emit_shared_ready = false;
    bool no_candidate = false;
    bool descriptor_mismatch = false;
    WbDeckState mismatched_descriptor = {0};
    WbCandidateNotice retired_notice = {0};
    WbDeckState shared_track = {0};
    uint64_t shared_decoded_samples = 0U;
    int64_t shared_decoded_duration_ms = 0;
    size_t shared_fill = 0U;
    size_t shared_capacity = 0U;
    int64_t activation_request_ms = activate
        ? (requested_activation_monotonic_ms > 0 ? requested_activation_monotonic_ms : monotonic_ms())
        : 0;

    if (track == NULL || !state->audio_probe_enabled) return;

    (void)pthread_mutex_lock(&state->lock);
    probe = find_current_identity_locked(state, deck, track, &matched_alias_index);
    if (probe != NULL && !probe->shutdown) {
        const WbDeckState *matched_descriptor = matched_alias_index >= 0
            ? &probe->aliases[(size_t)matched_alias_index]
            : &probe->track;
        if (validate_descriptor && !same_audio_descriptor(matched_descriptor, track)) {
            mismatched_descriptor = *matched_descriptor;
            descriptor_mismatch = true;
            child_pid = probe->child_pid;
            queue_replacement_locked(
                probe, track, activate, activation_request_ms, "descriptor_mismatch"
            );
        } else if (matched_alias_index >= 0 && probe->activated) {
            /* The buffer has already been consumed by a different identity.
               Remove only this alias and prepare a fresh candidate below. */
            remove_alias_at_locked(probe, (size_t)matched_alias_index);
            probe = NULL;
        } else if (
            activate
            && !probe->activated
            && !probe_can_activate_without_restart(probe)
        ) {
            /* A terminal or empty candidate cannot satisfy track_started.  Do
               not promote an alias or mark the dead decoder active; fall
               through to queue a fresh decode session instead. */
            if (matched_alias_index >= 0) {
                remove_alias_at_locked(probe, (size_t)matched_alias_index);
            }
            probe = NULL;
        } else {
            /* A repeated deck_loaded for an alias must be idempotent.  Promote
               an alias only when that exact token is selected for playback;
               promoting it during prepare used to erase the older aliases and
               forced a cold decoder restart at track_started. */
            if (activate && matched_alias_index >= 0) {
                resolve_alias_as_primary_locked(probe, (size_t)matched_alias_index);
            }
            if (activate && !probe->activated) {
                probe->activated = true;
                probe->requested_activation_monotonic_ms = activation_request_ms;
                probe->activation_monotonic_ms = activation_request_ms;
                probe->audio_mismatch_emitted = false;
                arm_fault_for_activation_locked(state, probe, &probe->track);
                copy_text(
                    probe->status,
                    sizeof(probe->status),
                    (probe->prebuffer_ready && probe->ring_fill >= WB_AUDIO_FRAME_BYTES)
                        ? "ready" : "buffering"
                );
                retire_unselected_after_activation_locked(
                    state, deck, probe, &probe->track, &retired_pid, &retired_notice
                );
                (void)pthread_cond_signal(&probe->cond);
            }
            (void)pthread_mutex_unlock(&state->lock);
            terminate_child(retired_pid);
            emit_candidate_notice(state, &retired_notice);
            return;
        }
    }

    if (descriptor_mismatch) {
        (void)pthread_mutex_unlock(&state->lock);
        terminate_child(child_pid);
        emit_descriptor_mismatch(state, deck, track, &mismatched_descriptor);
        return;
    }

    replacement_probe = find_replacement_identity_locked(state, deck, track);
    if (replacement_probe != NULL) {
        if (activate) {
            replacement_probe->replacement_activate = true;
            replacement_probe->replacement_activation_monotonic_ms = activation_request_ms;
        }
        (void)pthread_mutex_unlock(&state->lock);
        return;
    }

    probe = find_shareable_content_locked(state, deck, track);
    if (probe != NULL && append_alias_locked(probe, track)) {
        shared_track = *track;
        shared_decoded_samples = probe->decoded_samples;
        shared_decoded_duration_ms = probe->decoded_duration_ms;
        shared_fill = probe->ring_fill;
        shared_capacity = probe->ring_capacity;
        emit_shared_preload = true;
        emit_shared_ready = probe->prebuffer_ready
            && !probe->activated
            && !probe->eof
            && probe->ring_fill >= WB_AUDIO_FRAME_BYTES;
        if (activate) {
            ssize_t alias_index = alias_index_for_track(probe, track);
            if (alias_index >= 0) resolve_alias_as_primary_locked(probe, (size_t)alias_index);
            probe->activated = true;
            probe->requested_activation_monotonic_ms = activation_request_ms;
            probe->activation_monotonic_ms = activation_request_ms;
            probe->audio_mismatch_emitted = false;
            arm_fault_for_activation_locked(state, probe, &probe->track);
            copy_text(
                probe->status,
                sizeof(probe->status),
                (probe->prebuffer_ready && probe->ring_fill >= WB_AUDIO_FRAME_BYTES)
                    ? "ready" : "buffering"
            );
            retire_unselected_after_activation_locked(
                state, deck, probe, &probe->track, &retired_pid, &retired_notice
            );
            (void)pthread_cond_signal(&probe->cond);
        }
        (void)pthread_mutex_unlock(&state->lock);
        terminate_child(retired_pid);
        emit_candidate_notice(state, &retired_notice);
        if (emit_shared_preload) {
            char extra[256];
            (void)snprintf(
                extra,
                sizeof(extra),
                "\"prestarted_on\":\"shared_candidate\",\"shared_buffer\":true,\"decoder_reused\":true,\"alias_count\":%zu",
                probe->alias_count
            );
            (void)send_probe_event(
                state, "native_audio_probe_preload_started", &shared_track, deck,
                shared_decoded_samples, 0U, shared_decoded_duration_ms, 0,
                shared_track.cue_in_ms, shared_fill, shared_capacity, extra
            );
        }
        if (emit_shared_ready) {
            int64_t buffered_ms = (int64_t)(((shared_fill / WB_AUDIO_FRAME_BYTES) * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
            char extra[384];
            (void)snprintf(
                extra, sizeof(extra),
                "\"buffered_duration_ms\":%lld,\"target_prebuffer_ms\":%d,\"source_eof\":false,\"shared_buffer\":true,\"decoder_reused\":true",
                (long long)buffered_ms,
                state->audio_prebuffer_ms
            );
            (void)send_probe_event(
                state, "native_audio_probe_prebuffer_ready", &shared_track, deck,
                shared_decoded_samples, 0U, shared_decoded_duration_ms, 0,
                shared_track.cue_in_ms, shared_fill, shared_capacity, extra
            );
        }
        return;
    }

    probe = choose_candidate_slot_locked(state, deck);
    if (probe == NULL || probe->shutdown) {
        no_candidate = activate;
        (void)pthread_mutex_unlock(&state->lock);
        if (no_candidate) emit_no_candidate_mismatch(state, deck, track, "no_inactive_candidate_slot");
        return;
    }

    if (probe_is_reusable(probe)) {
        queue_track_locked(state, probe, track, activate, activation_request_ms);
    } else {
        child_pid = probe->child_pid;
        queue_replacement_locked(
            probe,
            track,
            activate,
            activation_request_ms,
            "candidate_table_full_eviction"
        );
    }
    (void)pthread_mutex_unlock(&state->lock);
    terminate_child(child_pid);
}

void wb_audio_probe_prepare_deck(WbEngineState *state, char deck, const WbDeckState *track) {
    prepare_or_activate_deck(state, deck, track, false, true, 0);
}

void wb_audio_probe_activate_deck(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    bool validate_descriptor,
    int64_t requested_activation_monotonic_ms
) {
    prepare_or_activate_deck(
        state,
        deck,
        track,
        true,
        validate_descriptor,
        requested_activation_monotonic_ms
    );
}

size_t wb_audio_probe_prime_deck(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    unsigned char *pcm,
    size_t pcm_capacity,
    int64_t requested_activation_monotonic_ms
) {
    WbAudioDeckProbe *probe;
    ssize_t alias_index = -1;
    pid_t retired_pid = 0;
    WbCandidateNotice retired_notice = {0};
    WbDeckState event_track = {0};
    size_t wanted;
    size_t primed = 0U;
    uint64_t decoded_samples = 0U;
    uint64_t played_samples = 0U;
    int64_t decoded_duration_ms = 0;
    int64_t played_duration_ms = 0;
    int64_t position_ms = 0;
    size_t ring_fill = 0U;
    size_t ring_capacity = 0U;
    int64_t activation_ms;
    char extra[384];

    if (state == NULL || track == NULL || pcm == NULL || pcm_capacity < WB_AUDIO_FRAME_BYTES) return 0U;
    wanted = pcm_capacity - (pcm_capacity % WB_AUDIO_FRAME_BYTES);
    activation_ms = requested_activation_monotonic_ms > 0
        ? requested_activation_monotonic_ms : monotonic_ms();

    (void)pthread_mutex_lock(&state->lock);
    probe = find_current_identity_locked(state, deck, track, &alias_index);
    if (
        probe == NULL || probe->shutdown || probe->activated || probe->eof
        || probe->ring_fill < WB_AUDIO_FRAME_BYTES
    ) {
        (void)pthread_mutex_unlock(&state->lock);
        return 0U;
    }
    if (alias_index >= 0) resolve_alias_as_primary_locked(probe, (size_t)alias_index);
    if (wanted > probe->ring_fill) wanted = probe->ring_fill - (probe->ring_fill % WB_AUDIO_FRAME_BYTES);
    primed = ring_read_pcm(probe, pcm, wanted);
    if (primed > 0U) {
        uint64_t primed_samples = primed / WB_AUDIO_FRAME_BYTES;
        probe->activated = true;
        probe->requested_activation_monotonic_ms = activation_ms;
        probe->audio_mismatch_emitted = false;
        probe->played_samples += primed_samples;
        probe->played_duration_ms = samples_to_ms(probe->played_samples);
        /*
         * The primed PCM already represents the first played samples. Align the
         * realtime playback clock behind the audible handoff boundary by exactly
         * that duration. This lets the decoder produce the continuation as soon
         * as the mixer starts consuming the prime, instead of waiting for the
         * prime duration and risking a one-tick hole.
         */
        probe->activation_monotonic_ms = activation_ms - probe->played_duration_ms;
        probe->position_ms = probe->track.cue_in_ms + probe->played_duration_ms;
        copy_text(probe->status, sizeof(probe->status), "armed");
        arm_fault_for_activation_locked(state, probe, &probe->track);
        retire_unselected_after_activation_locked(
            state, deck, probe, &probe->track, &retired_pid, &retired_notice
        );
        (void)pthread_cond_signal(&probe->cond);
        event_track = probe->track;
        decoded_samples = probe->decoded_samples;
        played_samples = probe->played_samples;
        decoded_duration_ms = probe->decoded_duration_ms;
        played_duration_ms = probe->played_duration_ms;
        position_ms = probe->position_ms;
        ring_fill = probe->ring_fill;
        ring_capacity = probe->ring_capacity;
    }
    (void)pthread_mutex_unlock(&state->lock);

    terminate_child(retired_pid);
    emit_candidate_notice(state, &retired_notice);
    if (primed > 0U) {
        (void)snprintf(
            extra,
            sizeof(extra),
            "\"primed_bytes\":%zu,\"primed_duration_ms\":%lld,"
            "\"activation_monotonic_ms\":%lld,\"handoff_armed\":true",
            primed,
            (long long)samples_to_ms(primed / WB_AUDIO_FRAME_BYTES),
            (long long)activation_ms
        );
        (void)send_probe_event(
            state, "native_audio_probe_handoff_primed", &event_track, deck,
            decoded_samples, played_samples, decoded_duration_ms, played_duration_ms,
            position_ms, ring_fill, ring_capacity, extra
        );
    }
    return primed;
}

bool wb_audio_probe_retime_activation(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t requested_activation_monotonic_ms
) {
    WbAudioDeckProbe *probe;
    ssize_t alias_index = -1;
    bool changed = false;
    int64_t activation_ms = requested_activation_monotonic_ms > 0
        ? requested_activation_monotonic_ms : monotonic_ms();
    if (state == NULL || track == NULL) return false;
    (void)pthread_mutex_lock(&state->lock);
    probe = find_current_identity_locked(state, deck, track, &alias_index);
    if (probe != NULL && !probe->shutdown && probe->activated && !probe->eof) {
        if (alias_index >= 0) resolve_alias_as_primary_locked(probe, (size_t)alias_index);
        probe->requested_activation_monotonic_ms = activation_ms;
        /* Keep the already-primed samples on the same playback clock when an
         * early EOF moves the audible boundary to now. */
        probe->activation_monotonic_ms = activation_ms - probe->played_duration_ms;
        (void)pthread_cond_signal(&probe->cond);
        changed = true;
    }
    (void)pthread_mutex_unlock(&state->lock);
    return changed;
}

bool wb_audio_probe_get_position_ms(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t *position_ms
) {
    WbAudioDeckProbe *probe;
    ssize_t alias_index = -1;
    bool matched = false;
    if (position_ms != NULL) *position_ms = 0;
    if (state == NULL || track == NULL || position_ms == NULL) return false;
    (void)pthread_mutex_lock(&state->lock);
    probe = find_current_identity_locked(state, deck, track, &alias_index);
    if (probe != NULL && !probe->shutdown) {
        *position_ms = probe->position_ms;
        matched = true;
    }
    (void)pthread_mutex_unlock(&state->lock);
    return matched;
}

bool wb_audio_probe_is_prebuffer_ready(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    size_t *ring_fill_bytes
) {
    WbAudioDeckProbe *probe;
    ssize_t alias_index = -1;
    bool ready = false;
    if (ring_fill_bytes != NULL) *ring_fill_bytes = 0U;
    if (state == NULL || track == NULL) return false;
    (void)pthread_mutex_lock(&state->lock);
    probe = find_current_identity_locked(state, deck, track, &alias_index);
    if (probe != NULL && !probe->shutdown) {
        if (ring_fill_bytes != NULL) *ring_fill_bytes = probe->ring_fill;
        ready = probe->prebuffer_ready && probe->ring_fill > 0U;
    }
    (void)pthread_mutex_unlock(&state->lock);
    return ready;
}

void wb_audio_probe_seek_track(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    int64_t seek_position_ms
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    WbAudioDeckProbe *probes[2];
    WbAudioDeckProbe *selected = NULL;
    pid_t child_pid = 0;
    WbDeckState replacement = {0};
    unsigned char *bridge_pcm = NULL;
    size_t bridge_bytes = 0U;
    size_t index;

    if (state == NULL || !state->audio_probe_enabled) return;
    if (seek_position_ms < 0) seek_position_ms = 0;
    probe_pair(state, deck, &first, &second);
    probes[0] = first;
    probes[1] = second;

    (void)pthread_mutex_lock(&state->lock);
    for (index = 0U; index < 2U; index += 1U) {
        ssize_t alias_index = -1;
        WbAudioDeckProbe *probe = probes[index];
        if (!probe_identity_values_match(probe, queue_id, slot_token, &alias_index)) continue;
        if (alias_index >= 0 && probe->activated) {
            resolve_alias_as_primary_locked(probe, (size_t)alias_index);
        } else if (alias_index >= 0) {
            continue;
        }
        if (!probe->activated || !probe->track.loaded) continue;
        selected = probe;
        break;
    }
    if (selected != NULL) {
        replacement = selected->track;
        {
            int64_t source_end_ms = track_source_end_ms(&replacement);
            if (source_end_ms > 0 && seek_position_ms >= source_end_ms) {
                seek_position_ms = source_end_ms - 1;
            }
        }
        replacement.play_start_ms = seek_position_ms;
        replacement.cue_in_ms = seek_position_ms;
        child_pid = selected->child_pid;

        /* Preserve the already decoded future PCM as a short bridge. The
           Icecast mixer keeps playing it while FFmpeg reopens at the seek
           target, then atomically discards any remainder on the first new
           target-position frame. */
        bridge_bytes = selected->ring_fill - (selected->ring_fill % WB_AUDIO_FRAME_BYTES);
        if (bridge_bytes > 0U) {
            bridge_pcm = malloc(bridge_bytes);
            if (bridge_pcm != NULL) {
                bridge_bytes = ring_read_pcm(selected, bridge_pcm, bridge_bytes);
            } else {
                bridge_bytes = 0U;
            }
        }

        selected->replacement_track = replacement;
        selected->replacement_pending = true;
        selected->replacement_activate = true;
        selected->replacement_is_seek = true;
        selected->replacement_activation_monotonic_ms = monotonic_ms();
        selected->seek_from_position_ms = selected->position_ms;
        selected->seek_target_position_ms = seek_position_ms;
        selected->stop_requested = true;
        copy_text(selected->stop_reason, sizeof(selected->stop_reason), "seek_reposition");
        copy_text(selected->status, sizeof(selected->status), "seeking");
        (void)pthread_cond_signal(&selected->cond);
    }
    (void)pthread_mutex_unlock(&state->lock);

    if (selected != NULL) {
        wb_icecast_output_seek_track(
            state,
            deck,
            slot_token,
            bridge_pcm,
            bridge_bytes
        );
    }
    free(bridge_pcm);
    terminate_child(child_pid);

    if (selected == NULL) {
        WbDeckState identity = {0};
        char extra[384];
        identity.loaded = true;
        identity.queue_id = queue_id > 0 ? queue_id : 0;
        copy_text(identity.slot_token, sizeof(identity.slot_token), slot_token);
        (void)pthread_mutex_lock(&state->lock);
        state->audio_runtime_mismatch_count += 1U;
        state->audio_runtime_mismatch_total_count += 1U;
        (void)pthread_mutex_unlock(&state->lock);
        (void)snprintf(
            extra, sizeof(extra),
            "\"reason\":\"seek_active_voice_not_found\",\"seek_target_position_ms\":%lld,\"terminal\":false",
            (long long)seek_position_ms
        );
        (void)send_probe_event(
            state, "native_audio_runtime_mismatch", &identity, deck,
            0U, 0U, 0, 0, seek_position_ms, 0U, 0U, extra
        );
    }
}

void wb_audio_probe_stop_track(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    const char *reason
) {
    WbAudioDeckProbe *first;
    WbAudioDeckProbe *second;
    WbAudioDeckProbe *probes[2];
    pid_t pids[2] = {0, 0};
    size_t index;

    probe_pair(state, deck, &first, &second);
    probes[0] = first;
    probes[1] = second;

    (void)pthread_mutex_lock(&state->lock);
    for (index = 0U; index < 2U; index += 1U) {
        WbAudioDeckProbe *probe = probes[index];
        if (probe->replacement_pending
            && identity_values_match(&probe->replacement_track, queue_id, slot_token)) {
            probe->replacement_pending = false;
            probe->replacement_activate = false;
            probe->replacement_is_seek = false;
            memset(&probe->replacement_track, 0, sizeof(probe->replacement_track));
        }
        {
            ssize_t matched_alias_index = -1;
            if (!probe_identity_values_match(probe, queue_id, slot_token, &matched_alias_index)) continue;

            /* Alias identities never own the playback voice: activation first
               promotes the exact token to probe->track.  Therefore ending an
               alias only detaches that token, even while another identity is
               active on the shared candidate. */
            if (matched_alias_index >= 0) {
                remove_alias_at_locked(probe, (size_t)matched_alias_index);
                continue;
            }

            /* If the obsolete identity is the inactive primary but newer
               aliases still reference the candidate, preserve the decoder and
               ring buffer by promoting the newest surviving alias. */
            if (!probe->activated && probe->alias_count > 0U) {
                replace_primary_with_alias_locked(probe, probe->alias_count - 1U);
                continue;
            }
        }
        pids[index] = probe->child_pid;
        probe->stop_requested = true;
        probe->request_pending = false;
        copy_text(
            probe->stop_reason,
            sizeof(probe->stop_reason),
            reason == NULL ? "track_ended" : reason
        );
        if (probe->running || probe->child_pid > 0) {
            copy_text(probe->status, sizeof(probe->status), "stopping");
        } else {
            set_terminal_status_locked(probe, "stopped", "", false, true);
        }
        (void)pthread_cond_signal(&probe->cond);
    }
    (void)pthread_mutex_unlock(&state->lock);
    terminate_child(pids[0]);
    terminate_child(pids[1]);
}

void wb_audio_probe_stop_all(WbEngineState *state, const char *reason) {
    WbAudioDeckProbe *probes[4] = {
        &state->audio_deck_a,
        &state->audio_deck_a_alt,
        &state->audio_deck_b,
        &state->audio_deck_b_alt,
    };
    pid_t pids[4] = {0, 0, 0, 0};
    size_t index;
    (void)pthread_mutex_lock(&state->lock);
    for (index = 0U; index < 4U; index += 1U) {
        WbAudioDeckProbe *probe = probes[index];
        pids[index] = probe->child_pid;
        probe->replacement_pending = false;
        probe->replacement_activate = false;
        probe->replacement_is_seek = false;
        probe->replacement_activation_monotonic_ms = 0;
        memset(&probe->replacement_track, 0, sizeof(probe->replacement_track));
        clear_aliases(probe);
        probe->stop_requested = true;
        probe->request_pending = false;
        copy_text(
            probe->stop_reason,
            sizeof(probe->stop_reason),
            reason == NULL ? "engine_stop" : reason
        );
        if (probe->running || probe->child_pid > 0) {
            copy_text(probe->status, sizeof(probe->status), "stopping");
        } else {
            clear_candidate_locked(probe);
        }
        (void)pthread_cond_signal(&probe->cond);
    }
    (void)pthread_mutex_unlock(&state->lock);
    for (index = 0U; index < 4U; index += 1U) terminate_child(pids[index]);
}

static void destroy_deck_probe(WbEngineState *state, WbAudioDeckProbe *probe) {
    pid_t child_pid;
    (void)pthread_mutex_lock(&state->lock);
    probe->shutdown = true;
    child_pid = probe->child_pid;
    (void)pthread_cond_broadcast(&probe->cond);
    (void)pthread_mutex_unlock(&state->lock);
    terminate_child(child_pid);
    if (probe->thread_created) {
        (void)pthread_join(probe->thread, NULL);
        probe->thread_created = false;
    }
    (void)pthread_cond_destroy(&probe->cond);
    free(probe->ring_buffer);
    probe->ring_buffer = NULL;
}

void wb_audio_probe_destroy(WbEngineState *state) {
    WbAudioDeckProbe *probes[4] = {
        &state->audio_deck_a,
        &state->audio_deck_a_alt,
        &state->audio_deck_b,
        &state->audio_deck_b_alt,
    };
    size_t index;
    for (index = 0U; index < 4U; index += 1U) {
        if (probes[index]->ring_buffer != NULL || probes[index]->thread_created) {
            destroy_deck_probe(state, probes[index]);
        }
    }
    if (state->ffmpeg_runtime_valid) {
        wb_libav_runtime_shutdown();
        state->ffmpeg_runtime_valid = false;
    }
}
