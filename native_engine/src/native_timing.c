#define _POSIX_C_SOURCE 200809L

#include "native_timing.h"
#include "audio_probe.h"
#include "icecast_output.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#define WB_NATIVE_NEXT_REQUEST_LEAD_MS 15000
#define WB_NATIVE_HARD_ARM_LEAD_MS 5000
#define WB_NATIVE_TIMING_POLL_MS 10
#define WB_NATIVE_NEXT_REQUEST_RETRY_MS 250
#define WB_NATIVE_HARD_PRIME_MS 80
#define WB_NATIVE_HARD_PRIME_BYTES \
    (((WB_AUDIO_SAMPLE_RATE * WB_NATIVE_HARD_PRIME_MS) / 1000) * WB_AUDIO_FRAME_BYTES)

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static void copy_text(char *destination, size_t size, const char *source) {
    if (size == 0U) return;
    (void)snprintf(destination, size, "%s", source == NULL ? "" : source);
}

static WbDeckState *deck_for(WbEngineState *state, char deck) {
    return deck == 'B' ? &state->deck_b : &state->deck_a;
}

static bool identity_matches(const WbDeckState *left, const WbDeckState *right) {
    if (left == NULL || right == NULL || !left->loaded || !right->loaded) return false;
    if (right->slot_token[0] != '\0') return strcmp(left->slot_token, right->slot_token) == 0;
    return right->queue_id > 0 && left->queue_id == right->queue_id;
}

static bool stored_identity_matches(
    int64_t queue_id, const char *slot_token, const WbDeckState *track
) {
    if (track == NULL || !track->loaded) return false;
    if (slot_token != NULL && slot_token[0] != '\0') {
        return strcmp(slot_token, track->slot_token) == 0;
    }
    return queue_id > 0 && queue_id == track->queue_id;
}

static void store_identity(int64_t *queue_id, char *slot_token, const WbDeckState *track) {
    *queue_id = track != NULL ? track->queue_id : 0;
    copy_text(slot_token, WB_SLOT_TOKEN_SIZE, track != NULL ? track->slot_token : "");
}

static void clear_identity(int64_t *queue_id, char *slot_token) {
    *queue_id = 0;
    if (slot_token != NULL) slot_token[0] = '\0';
}

static void emit_need_next(
    WbEngineState *state,
    char active_deck,
    char target_deck,
    const WbDeckState *active,
    int64_t position_ms,
    int64_t remaining_ms,
    uint64_t request_attempt
) {
    char payload[512];
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_timing_owner\":true,\"source\":\"native_timing_worker\"," 
        "\"active_deck\":\"%c\",\"target_deck\":\"%c\"," 
        "\"position_ms\":%lld,\"effective_end_ms\":%lld,\"remaining_ms\":%lld,\"request_attempt\":%llu}",
        active_deck,
        target_deck,
        (long long)position_ms,
        (long long)active->effective_end_ms,
        (long long)remaining_ms,
        (unsigned long long)request_attempt
    );
    (void)wb_engine_send_event(state, "native_need_next_track", active, active_deck, payload);
}

static bool arm_hard_handoff(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    const WbDeckState *from_track,
    const WbDeckState *to_track,
    int64_t position_ms
) {
    int64_t boundary_ms = from_track->effective_end_ms > 0
        ? from_track->effective_end_ms : from_track->source_end_ms;
    int64_t remaining_ms;
    int64_t output_buffered_ms = 0;
    int64_t switch_at_ms;
    unsigned char primed_pcm[WB_NATIVE_HARD_PRIME_BYTES];
    size_t primed_bytes;
    char error[WB_ICECAST_ERROR_SIZE] = "";
    char payload[768];

    if (boundary_ms <= 0) return false;
    remaining_ms = boundary_ms - position_ms;
    if (remaining_ms < 0) remaining_ms = 0;
    if (remaining_ms > state->native_timing.hard_handoff_arm_lead_ms) return false;
    (void)wb_icecast_output_get_deck_buffered_ms(
        state, from_deck, from_track, &output_buffered_ms
    );
    switch_at_ms = monotonic_ms() + remaining_ms + output_buffered_ms;
    primed_bytes = wb_audio_probe_prime_deck(
        state, to_deck, to_track, primed_pcm, sizeof(primed_pcm), switch_at_ms
    );
    if (primed_bytes == 0U) return false;
    if (wb_icecast_output_schedule_hard_handoff(
            state,
            from_deck,
            to_deck,
            from_track,
            to_track,
            switch_at_ms,
            primed_pcm,
            primed_bytes,
            error,
            sizeof(error)
        ) != 0) {
        return false;
    }

    (void)pthread_mutex_lock(&state->lock);
    store_identity(
        &state->native_timing.scheduled_for_queue_id,
        state->native_timing.scheduled_for_slot_token,
        from_track
    );
    state->native_timing.hard_handoff_arm_count += 1U;
    (void)pthread_mutex_unlock(&state->lock);

    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_timing_owner\":true,\"source\":\"native_timing_worker\"," 
        "\"hard_handoff_armed\":true,\"from_deck\":\"%c\",\"to_deck\":\"%c\"," 
        "\"position_ms\":%lld,\"boundary_ms\":%lld,\"remaining_ms\":%lld," 
        "\"outgoing_buffered_ms\":%lld,\"handoff_at_monotonic_ms\":%lld," 
        "\"primed_bytes\":%zu}",
        from_deck,
        to_deck,
        (long long)position_ms,
        (long long)boundary_ms,
        (long long)remaining_ms,
        (long long)output_buffered_ms,
        (long long)switch_at_ms,
        primed_bytes
    );
    (void)wb_engine_send_event(
        state, "native_hard_handoff_armed", to_track, to_deck, payload
    );
    return true;
}

static bool start_terminal_recovery(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    const WbDeckState *from_track,
    const WbDeckState *to_track
) {
    int64_t now_ms = monotonic_ms();
    WbDeckState recovered = {0};
    char started_payload[512];
    char ended_payload[384];

    if (wb_icecast_output_has_pending_hard_handoff(state, from_deck, from_track)) {
        return false;
    }
    (void)pthread_mutex_lock(&state->lock);
    if (
        !state->running || state->paused || state->transitioning
        || state->active_deck != from_deck
        || !identity_matches(deck_for(state, from_deck), from_track)
        || !identity_matches(deck_for(state, to_deck), to_track)
        || !deck_for(state, from_deck)->terminal
        || deck_for(state, to_deck)->consumed
        || deck_for(state, to_deck)->terminal
        || deck_for(state, to_deck)->playback_started
    ) {
        (void)pthread_mutex_unlock(&state->lock);
        return false;
    }
    deck_for(state, from_deck)->consumed = true;
    deck_for(state, from_deck)->terminal = true;
    deck_for(state, to_deck)->playback_started = true;
    deck_for(state, to_deck)->consumed = false;
    deck_for(state, to_deck)->terminal = false;
    recovered = *deck_for(state, to_deck);
    state->active_deck = to_deck;
    state->transitioning = false;
    store_identity(
        &state->native_timing.scheduled_for_queue_id,
        state->native_timing.scheduled_for_slot_token,
        from_track
    );
    (void)pthread_mutex_unlock(&state->lock);

    wb_icecast_output_stop_track(
        state, from_deck, from_track->queue_id, from_track->slot_token
    );
    wb_icecast_output_activate_track(state, to_deck, &recovered);
    wb_audio_probe_activate_deck(state, to_deck, &recovered, true, now_ms);

    (void)snprintf(
        started_payload,
        sizeof(started_payload),
        "{\"native_timing_owner\":true,\"control_only\":false,"
        "\"audio_enabled\":true,\"source\":\"native_terminal_recovery\","
        "\"hard_handoff\":true,\"terminal_recovery\":true,"
        "\"from_deck\":\"%c\",\"actual_monotonic_ms\":%lld}",
        from_deck,
        (long long)now_ms
    );
    (void)wb_engine_send_event(
        state, "track_started", &recovered, to_deck, started_payload
    );
    (void)snprintf(
        ended_payload,
        sizeof(ended_payload),
        "{\"native_timing_owner\":true,\"source\":\"native_terminal_recovery_release\","
        "\"to_deck\":\"%c\",\"actual_monotonic_ms\":%lld}",
        to_deck,
        (long long)now_ms
    );
    (void)wb_engine_send_event(
        state, "track_ended", from_track, from_deck, ended_payload
    );
    wb_native_timing_wake(state);
    return true;
}

static bool start_native_transition(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    const WbDeckState *from_track,
    const WbDeckState *to_track
) {
    int64_t now_ms = monotonic_ms();
    int64_t fade_ms = from_track->fade_out_ms > 0 ? from_track->fade_out_ms : 0;
    int64_t release_ms = fade_ms + 50;
    char payload[640];

    (void)pthread_mutex_lock(&state->lock);
    if (
        !state->running || state->paused || state->transitioning
        || state->active_deck != from_deck
        || !identity_matches(deck_for(state, from_deck), from_track)
        || !identity_matches(deck_for(state, to_deck), to_track)
    ) {
        (void)pthread_mutex_unlock(&state->lock);
        return false;
    }
    state->active_deck = to_deck;
    state->transitioning = true;
    deck_for(state, to_deck)->playback_started = true;
    deck_for(state, to_deck)->consumed = false;
    deck_for(state, to_deck)->terminal = false;
    store_identity(
        &state->native_timing.scheduled_for_queue_id,
        state->native_timing.scheduled_for_slot_token,
        from_track
    );
    state->native_timing.transition_completion_pending = true;
    state->native_timing.transition_completion_monotonic_ms = now_ms + release_ms;
    state->native_timing.transition_from_deck = from_deck;
    state->native_timing.transition_to_deck = to_deck;
    state->native_timing.transition_from_track = *from_track;
    state->native_timing.transition_to_track = *to_track;
    state->native_timing.transition_start_count += 1U;
    (void)pthread_mutex_unlock(&state->lock);

    wb_icecast_output_transition_started(
        state, from_deck, to_deck, now_ms, now_ms,
        release_ms, fade_ms, 20, 50
    );
    wb_icecast_output_activate_track(state, to_deck, to_track);
    wb_audio_probe_activate_deck(state, to_deck, to_track, true, now_ms);

    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_timing_owner\":true,\"control_only\":false," 
        "\"audio_enabled\":true,\"source\":\"native_timing_transition\"," 
        "\"from_deck\":\"%c\",\"fade_out_duration_ms\":%lld," 
        "\"release_duration_ms\":%lld}",
        from_deck,
        (long long)fade_ms,
        (long long)release_ms
    );
    (void)wb_engine_send_event(state, "transition_started", to_track, to_deck, payload);
    (void)wb_engine_send_event(state, "track_started", to_track, to_deck, payload);
    return true;
}

static void finish_native_transition(WbEngineState *state) {
    WbDeckState from_track = {0};
    WbDeckState to_track = {0};
    char from_deck = '\0';
    char to_deck = '\0';
    bool finish = false;
    char ended_payload[384];
    char finished_payload[384];

    (void)pthread_mutex_lock(&state->lock);
    if (
        state->native_timing.transition_completion_pending
        && monotonic_ms() >= state->native_timing.transition_completion_monotonic_ms
    ) {
        finish = true;
        from_deck = state->native_timing.transition_from_deck;
        to_deck = state->native_timing.transition_to_deck;
        from_track = state->native_timing.transition_from_track;
        to_track = state->native_timing.transition_to_track;
        state->native_timing.transition_completion_pending = false;
        state->native_timing.transition_completion_monotonic_ms = 0;
        state->transitioning = false;
        if (identity_matches(deck_for(state, from_deck), &from_track)) {
            deck_for(state, from_deck)->consumed = true;
            deck_for(state, from_deck)->terminal = true;
        }
        state->native_timing.transition_complete_count += 1U;
    }
    (void)pthread_mutex_unlock(&state->lock);
    if (!finish) return;

    wb_icecast_output_transition_finished(state, to_deck);
    wb_audio_probe_stop_track(
        state, from_deck, from_track.queue_id, from_track.slot_token,
        "native_timing_transition_complete"
    );
    wb_icecast_output_stop_track(
        state, from_deck, from_track.queue_id, from_track.slot_token
    );
    (void)snprintf(
        finished_payload,
        sizeof(finished_payload),
        "{\"native_timing_owner\":true,\"source\":\"native_timing_transition_complete\"," 
        "\"from_deck\":\"%c\"}",
        from_deck
    );
    (void)wb_engine_send_event(
        state, "transition_finished", &to_track, to_deck, finished_payload
    );
    (void)snprintf(
        ended_payload,
        sizeof(ended_payload),
        "{\"native_timing_owner\":true,\"source\":\"native_timing_transition_release\"," 
        "\"to_deck\":\"%c\"}",
        to_deck
    );
    (void)wb_engine_send_event(state, "track_ended", &from_track, from_deck, ended_payload);
}

static void reset_identity_if_active_changed(
    WbEngineState *state, const WbDeckState *active
) {
    WbNativeTimingWorker *worker = &state->native_timing;
    (void)pthread_mutex_lock(&state->lock);
    if (!stored_identity_matches(
            worker->requested_for_queue_id,
            worker->requested_for_slot_token,
            active
        )) {
        clear_identity(
            &worker->requested_for_queue_id,
            worker->requested_for_slot_token
        );
        worker->last_next_request_monotonic_ms = 0;
    }
    if (!stored_identity_matches(
            worker->scheduled_for_queue_id,
            worker->scheduled_for_slot_token,
            active
        )) {
        clear_identity(
            &worker->scheduled_for_queue_id,
            worker->scheduled_for_slot_token
        );
    }
    (void)pthread_mutex_unlock(&state->lock);
}

static void *timing_thread_main(void *context) {
    WbNativeTimingWorker *worker = context;
    WbEngineState *state = worker->owner;
    for (;;) {
        struct timespec deadline;
        WbDeckState active = {0};
        WbDeckState target = {0};
        char active_deck = 'A';
        char target_deck = 'B';
        bool running;
        bool paused;
        bool transitioning;
        bool request_sent;
        bool scheduled;
        bool target_ready = false;
        size_t target_ring = 0U;
        int64_t position_ms = 0;
        int64_t output_buffered_ms = 0;
        int64_t audible_remaining_end_ms;
        int64_t audible_remaining_transition_ms;

        (void)clock_gettime(CLOCK_REALTIME, &deadline);
        deadline.tv_nsec += (long)worker->poll_interval_ms * 1000000L;
        while (deadline.tv_nsec >= 1000000000L) {
            deadline.tv_sec += 1;
            deadline.tv_nsec -= 1000000000L;
        }
        (void)pthread_mutex_lock(&state->lock);
        if (!worker->shutdown) {
            (void)pthread_cond_timedwait(&worker->cond, &state->lock, &deadline);
        }
        if (worker->shutdown) {
            (void)pthread_mutex_unlock(&state->lock);
            return NULL;
        }
        running = state->running;
        paused = state->paused;
        transitioning = state->transitioning;
        active_deck = state->active_deck == 'B' ? 'B' : 'A';
        target_deck = active_deck == 'A' ? 'B' : 'A';
        active = *deck_for(state, active_deck);
        target = *deck_for(state, target_deck);
        request_sent = stored_identity_matches(
            worker->requested_for_queue_id, worker->requested_for_slot_token, &active
        );
        scheduled = stored_identity_matches(
            worker->scheduled_for_queue_id, worker->scheduled_for_slot_token, &active
        );
        (void)pthread_mutex_unlock(&state->lock);

        finish_native_transition(state);
        if (
            !running || paused || transitioning || !active.loaded
            || (!active.analysis_ready && !active.terminal)
            || !active.playback_started
        ) continue;
        reset_identity_if_active_changed(state, &active);
        (void)pthread_mutex_lock(&state->lock);
        request_sent = stored_identity_matches(
            worker->requested_for_queue_id, worker->requested_for_slot_token, &active
        );
        scheduled = stored_identity_matches(
            worker->scheduled_for_queue_id, worker->scheduled_for_slot_token, &active
        );
        (void)pthread_mutex_unlock(&state->lock);
        if (scheduled) continue;
        if (active.stream_source && active.stream_infinite) continue;
        if (!wb_audio_probe_get_position_ms(state, active_deck, &active, &position_ms)) continue;
        (void)wb_icecast_output_get_deck_buffered_ms(
            state, active_deck, &active, &output_buffered_ms
        );
        audible_remaining_end_ms = active.effective_end_ms - position_ms + output_buffered_ms;
        audible_remaining_transition_ms = active.transition_at_ms - position_ms + output_buffered_ms;

        target_ready = target.loaded && target.analysis_ready
            && !target.consumed && !target.terminal && !target.playback_started
            && wb_audio_probe_is_prebuffer_ready(
            state, target_deck, &target, &target_ring
        );
        if (
            (active.terminal || audible_remaining_end_ms <= worker->next_request_lead_ms)
            && !target_ready
        ) {
            bool should_emit = false;
            uint64_t request_attempt = 0U;
            int64_t now_ms = monotonic_ms();
            (void)pthread_mutex_lock(&state->lock);
            request_sent = stored_identity_matches(
                worker->requested_for_queue_id, worker->requested_for_slot_token, &active
            );
            if (
                !request_sent
                || worker->last_next_request_monotonic_ms <= 0
                || now_ms - worker->last_next_request_monotonic_ms >= worker->next_request_retry_ms
            ) {
                if (!request_sent) {
                    store_identity(
                        &worker->requested_for_queue_id,
                        worker->requested_for_slot_token,
                        &active
                    );
                }
                worker->last_next_request_monotonic_ms = now_ms;
                worker->next_track_request_count += 1U;
                request_attempt = worker->next_track_request_count;
                should_emit = true;
            }
            (void)pthread_mutex_unlock(&state->lock);
            if (should_emit) {
                emit_need_next(
                    state, active_deck, target_deck, &active,
                    position_ms,
                    active.terminal ? 0 : audible_remaining_end_ms,
                    request_attempt
                );
            }
        }
        if (!target_ready || identity_matches(&target, &active)) continue;

        if (active.terminal) {
            (void)start_terminal_recovery(
                state, active_deck, target_deck, &active, &target
            );
            continue;
        }

        if (
            active.hard_clean || target.hard_clean
            || active.short_no_crossfade || target.short_no_crossfade
        ) {
            if (audible_remaining_end_ms <= worker->hard_handoff_arm_lead_ms) {
                (void)arm_hard_handoff(
                    state, active_deck, target_deck, &active, &target, position_ms
                );
            }
            continue;
        }
        if (active.transition_at_ms > 0 && audible_remaining_transition_ms <= 0) {
            (void)start_native_transition(
                state, active_deck, target_deck, &active, &target
            );
        }
    }
}

int wb_native_timing_init(WbEngineState *state) {
    WbNativeTimingWorker *worker = &state->native_timing;
    memset(worker, 0, sizeof(*worker));
    worker->owner = state;
    worker->next_request_lead_ms = WB_NATIVE_NEXT_REQUEST_LEAD_MS;
    worker->hard_handoff_arm_lead_ms = WB_NATIVE_HARD_ARM_LEAD_MS;
    worker->poll_interval_ms = WB_NATIVE_TIMING_POLL_MS;
    worker->next_request_retry_ms = WB_NATIVE_NEXT_REQUEST_RETRY_MS;
    (void)pthread_cond_init(&worker->cond, NULL);
    if (pthread_create(&worker->thread, NULL, timing_thread_main, worker) != 0) {
        (void)pthread_cond_destroy(&worker->cond);
        return -1;
    }
    worker->thread_created = true;
    return 0;
}

void wb_native_timing_destroy(WbEngineState *state) {
    WbNativeTimingWorker *worker = &state->native_timing;
    (void)pthread_mutex_lock(&state->lock);
    worker->shutdown = true;
    (void)pthread_cond_broadcast(&worker->cond);
    (void)pthread_mutex_unlock(&state->lock);
    if (worker->thread_created) {
        (void)pthread_join(worker->thread, NULL);
        worker->thread_created = false;
    }
    (void)pthread_cond_destroy(&worker->cond);
}

void wb_native_timing_wake(WbEngineState *state) {
    (void)pthread_mutex_lock(&state->lock);
    (void)pthread_cond_broadcast(&state->native_timing.cond);
    (void)pthread_mutex_unlock(&state->lock);
}

void wb_native_timing_reset(WbEngineState *state) {
    WbNativeTimingWorker *worker = &state->native_timing;
    (void)pthread_mutex_lock(&state->lock);
    clear_identity(&worker->requested_for_queue_id, worker->requested_for_slot_token);
    worker->last_next_request_monotonic_ms = 0;
    clear_identity(&worker->scheduled_for_queue_id, worker->scheduled_for_slot_token);
    worker->transition_completion_pending = false;
    worker->transition_completion_monotonic_ms = 0;
    (void)pthread_cond_broadcast(&worker->cond);
    (void)pthread_mutex_unlock(&state->lock);
}
