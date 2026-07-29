#define _POSIX_C_SOURCE 200809L

#include "audio_analysis.h"
#include "audio_probe.h"
#include "native_timing.h"
#include "protocol.h"
#include "libav_bridge.h"

#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define WB_ANALYSIS_DEFAULT_WINDOW_MS 10
#define WB_ANALYSIS_DEFAULT_SUSTAIN_MS 30
#define WB_ANALYSIS_DEFAULT_ARTIFACT_MAX_MS 300
#define WB_ANALYSIS_DEFAULT_ARTIFACT_SILENCE_MS 250
#define WB_ANALYSIS_MAX_WINDOWS 720000U

typedef struct {
    int64_t start_ms;
    int64_t end_ms;
    double peak_dbfs;
    double rms_dbfs;
} WbAnalysisWindow;

typedef struct {
    WbAnalysisWindow *items;
    size_t count;
    size_t capacity;
    int64_t duration_ms;
} WbAnalysisMetrics;

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static void copy_text(char *destination, size_t size, const char *source) {
    if (size == 0U) return;
    (void)snprintf(destination, size, "%s", source == NULL ? "" : source);
}

static WbAudioAnalysisWorker *worker_for(WbEngineState *state, char deck) {
    return deck == 'B' ? &state->analysis_deck_b : &state->analysis_deck_a;
}

static WbDeckState *deck_for(WbEngineState *state, char deck) {
    return deck == 'B' ? &state->deck_b : &state->deck_a;
}

static bool identity_matches(const WbDeckState *left, const WbDeckState *right) {
    if (left == NULL || right == NULL || !left->loaded || !right->loaded) return false;
    if (right->slot_token[0] != '\0') return strcmp(left->slot_token, right->slot_token) == 0;
    return right->queue_id > 0 && left->queue_id == right->queue_id;
}

static bool local_file_track(const WbDeckState *track) {
    if (track == NULL || track->path[0] == '\0') return false;
    if (strncmp(track->path, "http://", 7U) == 0 || strncmp(track->path, "https://", 8U) == 0) return false;
    if (strncmp(track->path, "URL:", 4U) == 0) return false;
    return access(track->path, R_OK) == 0;
}

static double dbfs_from_peak(int peak) {
    double normalized = (double)peak / 32768.0;
    if (normalized < 1.0e-12) normalized = 1.0e-12;
    return 20.0 * log10(normalized);
}

static double dbfs_from_rms(long double sum_squares, uint64_t sample_count) {
    long double mean;
    double normalized;
    if (sample_count == 0U) return -240.0;
    mean = sum_squares / (long double)sample_count;
    normalized = sqrt((double)mean) / 32768.0;
    if (normalized < 1.0e-12) normalized = 1.0e-12;
    return 20.0 * log10(normalized);
}

static int append_window(
    WbAnalysisMetrics *metrics,
    int64_t start_ms,
    int64_t end_ms,
    int peak,
    long double sum_squares,
    uint64_t sample_count
) {
    WbAnalysisWindow *next;
    size_t capacity;
    if (metrics->count >= WB_ANALYSIS_MAX_WINDOWS) return -1;
    if (metrics->count == metrics->capacity) {
        capacity = metrics->capacity == 0U ? 4096U : metrics->capacity * 2U;
        if (capacity > WB_ANALYSIS_MAX_WINDOWS) capacity = WB_ANALYSIS_MAX_WINDOWS;
        next = realloc(metrics->items, capacity * sizeof(*next));
        if (next == NULL) return -1;
        metrics->items = next;
        metrics->capacity = capacity;
    }
    metrics->items[metrics->count].start_ms = start_ms;
    metrics->items[metrics->count].end_ms = end_ms;
    metrics->items[metrics->count].peak_dbfs = dbfs_from_peak(peak);
    metrics->items[metrics->count].rms_dbfs = dbfs_from_rms(sum_squares, sample_count);
    metrics->count += 1U;
    return 0;
}

static int decode_metrics(
    WbEngineState *state,
    WbAudioAnalysisWorker *worker,
    uint64_t generation,
    const WbDeckState *track,
    WbAnalysisMetrics *metrics,
    char *error,
    size_t error_size
) {
    WbLibavDecodeSession *session = NULL;
    WbLibavDecodeConfig config = {0};
    unsigned char buffer[65536];
    unsigned char carry[WB_AUDIO_FRAME_BYTES] = {0};
    size_t carry_bytes = 0U;
    uint64_t total_frames = 0U;
    uint64_t window_frames;
    uint64_t window_frame_count = 0U;
    uint64_t window_sample_count = 0U;
    long double window_sum_squares = 0.0L;
    int window_peak = 0;
    bool cancelled = false;
    int result = -1;
    int64_t configured_window_ms = track->analysis_window_ms > 0
        ? track->analysis_window_ms : WB_ANALYSIS_DEFAULT_WINDOW_MS;

    if (configured_window_ms < 5) configured_window_ms = 5;
    if (configured_window_ms > 100) configured_window_ms = 100;
    window_frames = ((uint64_t)WB_AUDIO_SAMPLE_RATE * (uint64_t)configured_window_ms) / 1000ULL;
    if (window_frames == 0U) window_frames = 1U;

    config.path = track->path;
    config.stream_source = false;
    config.stream_infinite = false;
    config.start_ms = 0;
    config.duration_ms = 0;
    config.fifo_capacity = 1024U * 1024U;
    if (wb_libav_decode_start(&session, &config, error, error_size) != 0) return -1;

    for (;;) {
        ssize_t received;
        size_t offset = 0U;

        (void)pthread_mutex_lock(&state->lock);
        cancelled = worker->shutdown || worker->generation != generation;
        (void)pthread_mutex_unlock(&state->lock);
        if (cancelled) {
            wb_libav_decode_abort(session);
            result = 1;
            goto finished;
        }

        received = wb_libav_decode_read(session, buffer, sizeof(buffer));
        if (received == -2) break;
        if (received < 0) {
            wb_libav_decode_error(session, error, error_size);
            if (error[0] == '\0') copy_text(error, error_size, "analysis libav decode failed");
            goto finished;
        }
        if (received == 0) continue;

        if (carry_bytes > 0U) {
            size_t need = WB_AUDIO_FRAME_BYTES - carry_bytes;
            size_t take = (size_t)received < need ? (size_t)received : need;
            memcpy(carry + carry_bytes, buffer, take);
            carry_bytes += take;
            offset += take;
            if (carry_bytes == WB_AUDIO_FRAME_BYTES) {
                int16_t samples[WB_AUDIO_CHANNELS];
                size_t channel;
                memcpy(samples, carry, sizeof(samples));
                for (channel = 0U; channel < WB_AUDIO_CHANNELS; channel += 1U) {
                    int value = samples[channel];
                    int abs_value = value < 0 ? -value : value;
                    if (abs_value > window_peak) window_peak = abs_value;
                    window_sum_squares += (long double)value * (long double)value;
                    window_sample_count += 1U;
                }
                total_frames += 1U;
                window_frame_count += 1U;
                carry_bytes = 0U;
            }
        }
        while (offset + WB_AUDIO_FRAME_BYTES <= (size_t)received) {
            int16_t samples[WB_AUDIO_CHANNELS];
            size_t channel;
            memcpy(samples, buffer + offset, sizeof(samples));
            offset += WB_AUDIO_FRAME_BYTES;
            for (channel = 0U; channel < WB_AUDIO_CHANNELS; channel += 1U) {
                int value = samples[channel];
                int abs_value = value < 0 ? -value : value;
                if (abs_value > window_peak) window_peak = abs_value;
                window_sum_squares += (long double)value * (long double)value;
                window_sample_count += 1U;
            }
            total_frames += 1U;
            window_frame_count += 1U;
            if (window_frame_count >= window_frames) {
                int64_t end_ms = (int64_t)((total_frames * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
                int64_t start_ms = end_ms - configured_window_ms;
                if (start_ms < 0) start_ms = 0;
                if (append_window(
                        metrics, start_ms, end_ms, window_peak,
                        window_sum_squares, window_sample_count
                    ) != 0) {
                    copy_text(error, error_size, "analysis metrics allocation failed");
                    goto finished;
                }
                window_frame_count = 0U;
                window_sample_count = 0U;
                window_sum_squares = 0.0L;
                window_peak = 0;
            }
        }
        if (offset < (size_t)received) {
            carry_bytes = (size_t)received - offset;
            memcpy(carry, buffer + offset, carry_bytes);
        }
    }

    wb_libav_decode_error(session, error, error_size);
    if (error[0] != '\0') goto finished;
    if (window_frame_count > 0U) {
        int64_t end_ms = (int64_t)((total_frames * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
        int64_t start_ms = metrics->count > 0U ? metrics->items[metrics->count - 1U].end_ms : 0;
        if (append_window(
                metrics, start_ms, end_ms, window_peak,
                window_sum_squares, window_sample_count
            ) != 0) {
            copy_text(error, error_size, "analysis final metrics allocation failed");
            goto finished;
        }
    }
    metrics->duration_ms = (int64_t)((total_frames * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
    if (metrics->count == 0U || metrics->duration_ms <= 0) {
        copy_text(error, error_size, "analysis produced no PCM");
        goto finished;
    }
    result = 0;

finished:
    if (session != NULL) {
        if (result != 0) wb_libav_decode_abort(session);
        wb_libav_decode_destroy(session);
    }
    return result;
}

static bool window_active(const WbAnalysisWindow *window, double threshold_dbfs) {
    return window != NULL && (
        window->peak_dbfs >= threshold_dbfs
        || window->rms_dbfs >= threshold_dbfs
    );
}

static size_t sustained_start(
    const WbAnalysisMetrics *metrics,
    double threshold_dbfs,
    size_t run_windows
) {
    size_t index;
    size_t run = 0U;
    for (index = 0U; index < metrics->count; index += 1U) {
        if (window_active(&metrics->items[index], threshold_dbfs)) {
            run += 1U;
            if (run >= run_windows) return index + 1U - run;
        } else {
            run = 0U;
        }
    }
    return 0U;
}

typedef struct {
    size_t start;
    size_t end;
} WbWindowRun;

static size_t collect_active_runs(
    const WbAnalysisMetrics *metrics,
    double threshold_dbfs,
    WbWindowRun **runs_out
) {
    WbWindowRun *runs = NULL;
    size_t run_count = 0U;
    size_t run_capacity = 0U;
    size_t index = 0U;
    while (index < metrics->count) {
        size_t start;
        size_t end;
        WbWindowRun *next;
        if (!window_active(&metrics->items[index], threshold_dbfs)) {
            index += 1U;
            continue;
        }
        start = index;
        while (index < metrics->count && window_active(&metrics->items[index], threshold_dbfs)) {
            index += 1U;
        }
        end = index;
        if (run_count == run_capacity) {
            run_capacity = run_capacity == 0U ? 16U : run_capacity * 2U;
            next = realloc(runs, run_capacity * sizeof(*next));
            if (next == NULL) {
                free(runs);
                *runs_out = NULL;
                return 0U;
            }
            runs = next;
        }
        runs[run_count].start = start;
        runs[run_count].end = end;
        run_count += 1U;
    }
    *runs_out = runs;
    return run_count;
}

static int compare_double_ascending(const void *left, const void *right) {
    const double a = *(const double *)left;
    const double b = *(const double *)right;
    return (a > b) - (a < b);
}

static double percentile_db(const WbAnalysisMetrics *metrics, size_t start, size_t end, double percentile) {
    double *values;
    size_t count = 0U;
    size_t index;
    double result = -24.0;
    if (end <= start || end > metrics->count) return result;
    values = malloc((end - start) * sizeof(*values));
    if (values == NULL) return result;
    for (index = start; index < end; index += 1U) {
        double value = metrics->items[index].rms_dbfs;
        if (isfinite(value) && value >= -90.0) values[count++] = value;
    }
    if (count > 0U) {
        size_t target;
        qsort(values, count, sizeof(*values), compare_double_ascending);
        if (percentile < 0.0) percentile = 0.0;
        if (percentile > 100.0) percentile = 100.0;
        target = (size_t)llround((percentile / 100.0) * (double)(count - 1U));
        if (target >= count) target = count - 1U;
        result = values[target];
    }
    free(values);
    return result;
}

static void apply_analysis(
    WbDeckState *track,
    const WbAnalysisMetrics *metrics
) {
    int64_t window_ms = track->analysis_window_ms > 0
        ? track->analysis_window_ms : WB_ANALYSIS_DEFAULT_WINDOW_MS;
    int64_t sustain_ms = track->analysis_sustain_ms > 0
        ? track->analysis_sustain_ms : WB_ANALYSIS_DEFAULT_SUSTAIN_MS;
    int64_t artifact_max_ms = track->analysis_artifact_max_ms > 0
        ? track->analysis_artifact_max_ms : WB_ANALYSIS_DEFAULT_ARTIFACT_MAX_MS;
    int64_t artifact_silence_ms = track->analysis_artifact_silence_ms > 0
        ? track->analysis_artifact_silence_ms : WB_ANALYSIS_DEFAULT_ARTIFACT_SILENCE_MS;
    double start_threshold = isfinite(track->gap_start_threshold_dbfs)
        && track->gap_start_threshold_dbfs < 0.0 ? track->gap_start_threshold_dbfs : -20.0;
    double end_threshold = isfinite(track->gap_end_threshold_dbfs)
        && track->gap_end_threshold_dbfs < 0.0 ? track->gap_end_threshold_dbfs : -24.0;
    double relative_trigger = isfinite(track->crossfade_trigger_relative_db)
        && track->crossfade_trigger_relative_db < 0.0 ? track->crossfade_trigger_relative_db : -7.0;
    size_t sustain_windows = (size_t)((sustain_ms + window_ms - 1) / window_ms);
    size_t start_index;
    size_t end_index;
    WbWindowRun *runs = NULL;
    size_t run_count;
    size_t chosen_run;
    int64_t ignored_artifact_ms = 0;
    int64_t trailing_silence_ms = 0;
    double reference_db;
    double trigger_db;
    int64_t trigger_ms;
    size_t index;

    if (sustain_windows == 0U) sustain_windows = 1U;
    start_index = sustained_start(metrics, start_threshold, sustain_windows);
    run_count = collect_active_runs(metrics, end_threshold, &runs);
    if (run_count > 1U) {
        size_t read_index;
        size_t write_index = 0U;
        int64_t merge_silence_ms = sustain_ms > 120 ? sustain_ms : 120;
        for (read_index = 1U; read_index < run_count; read_index += 1U) {
            int64_t silence_ms = metrics->items[runs[read_index].start].start_ms
                - metrics->items[runs[write_index].end - 1U].end_ms;
            if (silence_ms <= merge_silence_ms) {
                runs[write_index].end = runs[read_index].end;
            } else {
                write_index += 1U;
                runs[write_index] = runs[read_index];
            }
        }
        run_count = write_index + 1U;
    }
    if (run_count == 0U) {
        start_index = 0U;
        end_index = metrics->count;
    } else {
        chosen_run = run_count - 1U;
        while (chosen_run > 0U) {
            WbWindowRun current = runs[chosen_run];
            WbWindowRun previous = runs[chosen_run - 1U];
            int64_t current_duration = metrics->items[current.end - 1U].end_ms
                - metrics->items[current.start].start_ms;
            int64_t silence_before = metrics->items[current.start].start_ms
                - metrics->items[previous.end - 1U].end_ms;
            if (current_duration <= artifact_max_ms && silence_before >= artifact_silence_ms) {
                ignored_artifact_ms += current_duration;
                chosen_run -= 1U;
                continue;
            }
            break;
        }
        end_index = runs[chosen_run].end;
        if (end_index < metrics->count) {
            trailing_silence_ms = metrics->duration_ms - metrics->items[end_index - 1U].end_ms;
            if (trailing_silence_ms < 0) trailing_silence_ms = 0;
        }
    }
    free(runs);

    if (end_index <= start_index || end_index > metrics->count) {
        start_index = 0U;
        end_index = metrics->count;
    }
    track->audio_start_ms = metrics->items[start_index].start_ms;
    track->play_start_ms = track->audio_start_ms;
    track->cue_in_ms = track->play_start_ms;
    track->effective_end_ms = metrics->items[end_index - 1U].end_ms;
    track->source_end_ms = metrics->duration_ms;
    if (track->effective_end_ms <= track->play_start_ms) {
        track->audio_start_ms = 0;
        track->play_start_ms = 0;
        track->cue_in_ms = 0;
        track->effective_end_ms = metrics->duration_ms;
    }

    reference_db = percentile_db(metrics, start_index, end_index, 75.0);
    if (reference_db > 0.0) reference_db = 0.0;
    if (reference_db < -90.0) reference_db = -90.0;
    trigger_db = reference_db + relative_trigger;
    if (trigger_db > 0.0) trigger_db = 0.0;
    if (trigger_db < -120.0) trigger_db = -120.0;
    trigger_ms = track->effective_end_ms - (
        track->crossfade_fallback_ms > 0 ? track->crossfade_fallback_ms : 3000
    );
    if (trigger_ms < track->play_start_ms) trigger_ms = track->play_start_ms;
    for (index = end_index; index > start_index; index -= 1U) {
        const WbAnalysisWindow *window = &metrics->items[index - 1U];
        if (window->rms_dbfs >= trigger_db) {
            if (index < end_index) trigger_ms = metrics->items[index].start_ms;
            break;
        }
    }
    {
        int64_t crossfade = track->effective_end_ms - trigger_ms;
        int64_t min_ms = track->crossfade_min_ms > 0 ? track->crossfade_min_ms : 100;
        int64_t max_ms = track->crossfade_max_ms > 0 ? track->crossfade_max_ms : 6000;
        if (crossfade < min_ms) trigger_ms = track->effective_end_ms - min_ms;
        if (crossfade > max_ms) trigger_ms = track->effective_end_ms - max_ms;
        if (trigger_ms < track->play_start_ms) trigger_ms = track->play_start_ms;
    }

    track->short_no_crossfade = false;
    if (
        track->no_crossfade_max_duration_ms > 0
        && track->effective_end_ms - track->play_start_ms > 0
        && track->effective_end_ms - track->play_start_ms <= track->no_crossfade_max_duration_ms
    ) {
        track->short_no_crossfade = true;
        trigger_ms = track->effective_end_ms;
        track->fade_out_ms = 0;
    }
    track->transition_at_ms = trigger_ms;
    track->cue_out_ms = trigger_ms;
    track->analysis_reference_dbfs = reference_db;
    track->analysis_trigger_dbfs = trigger_db;
    track->analysis_ignored_artifact_ms = ignored_artifact_ms;
    track->analysis_trailing_silence_ms = trailing_silence_ms;
    track->analysis_tail_peak_dbfs = metrics->items[metrics->count - 1U].peak_dbfs;
    track->analysis_tail_rms_dbfs = metrics->items[metrics->count - 1U].rms_dbfs;
    track->analysis_ready = true;
    track->analysis_failed = false;
    copy_text(track->analysis_source, sizeof(track->analysis_source), "native_pcm_runtime");
    track->analysis_error[0] = '\0';
}

static void emit_analysis_event(
    WbEngineState *state,
    const char *event,
    const WbDeckState *track,
    char deck,
    int64_t elapsed_ms
) {
    char escaped_source[WB_ANALYSIS_SOURCE_SIZE * 2U];
    char escaped_error[WB_ANALYSIS_ERROR_SIZE * 2U];
    char payload[2048];
    wb_json_escape(track->analysis_source, escaped_source, sizeof(escaped_source));
    wb_json_escape(track->analysis_error, escaped_error, sizeof(escaped_error));
    (void)snprintf(
        payload, sizeof(payload),
        "{\"native_analysis\":true,\"analysis_requested\":%s,"
        "\"analysis_ready\":%s,\"analysis_failed\":%s,"
        "\"analysis_source\":\"%s\",\"analysis_error\":\"%s\","
        "\"analysis_elapsed_ms\":%lld,\"audio_start_ms\":%lld,"
        "\"play_start_ms\":%lld,\"transition_at_ms\":%lld,"
        "\"effective_end_ms\":%lld,\"source_end_ms\":%lld,"
        "\"fade_out_ms\":%lld,\"short_no_crossfade\":%s,"
        "\"reference_level_dbfs\":%.3f,\"trigger_level_dbfs\":%.3f,"
        "\"tail_peak_dbfs\":%.3f,\"tail_rms_dbfs\":%.3f,"
        "\"ignored_trailing_artifact_ms\":%lld,\"trailing_silence_ms\":%lld}",
        track->analysis_requested ? "true" : "false",
        track->analysis_ready ? "true" : "false",
        track->analysis_failed ? "true" : "false",
        escaped_source,
        escaped_error,
        (long long)elapsed_ms,
        (long long)track->audio_start_ms,
        (long long)track->play_start_ms,
        (long long)track->transition_at_ms,
        (long long)track->effective_end_ms,
        (long long)track->source_end_ms,
        (long long)track->fade_out_ms,
        track->short_no_crossfade ? "true" : "false",
        track->analysis_reference_dbfs,
        track->analysis_trigger_dbfs,
        track->analysis_tail_peak_dbfs,
        track->analysis_tail_rms_dbfs,
        (long long)track->analysis_ignored_artifact_ms,
        (long long)track->analysis_trailing_silence_ms
    );
    (void)wb_engine_send_event(state, event, track, deck, payload);
}

static void *analysis_worker_main(void *context) {
    WbAudioAnalysisWorker *worker = context;
    WbEngineState *state = worker->owner;
    for (;;) {
        WbDeckState track;
        WbDeckState result;
        uint64_t generation;
        int64_t started_ms;
        int decode_result;
        WbAnalysisMetrics metrics = {0};
        char error[WB_ANALYSIS_ERROR_SIZE] = "";
        bool current = false;

        (void)pthread_mutex_lock(&state->lock);
        while (!worker->shutdown && !worker->request_pending) {
            (void)pthread_cond_wait(&worker->cond, &state->lock);
        }
        if (worker->shutdown) {
            (void)pthread_mutex_unlock(&state->lock);
            return NULL;
        }
        worker->request_pending = false;
        worker->running = true;
        generation = worker->generation;
        track = worker->track;
        state->audio_analysis_started_count += 1U;
        (void)pthread_mutex_unlock(&state->lock);

        started_ms = monotonic_ms();
        copy_text(track.analysis_source, sizeof(track.analysis_source), "native_pcm_runtime_pending");
        emit_analysis_event(state, "native_audio_analysis_started", &track, worker->deck, 0);

        result = track;
        if (!track.analysis_requested || track.manual_timing || !local_file_track(&track)) {
            result.analysis_ready = true;
            result.analysis_failed = false;
            copy_text(
                result.analysis_source,
                sizeof(result.analysis_source),
                track.manual_timing ? "manual_override" : "native_analysis_skipped"
            );
            decode_result = 0;
        } else {
            decode_result = decode_metrics(
                state, worker, generation, &track, &metrics, error, sizeof(error)
            );
            if (decode_result == 0) {
                apply_analysis(&result, &metrics);
            } else if (decode_result < 0) {
                result.analysis_ready = true;
                result.analysis_failed = true;
                copy_text(result.analysis_source, sizeof(result.analysis_source), "native_analysis_fallback");
                copy_text(result.analysis_error, sizeof(result.analysis_error), error);
                if (result.source_end_ms <= 0) result.source_end_ms = result.effective_end_ms;
                if (result.effective_end_ms <= 0) result.effective_end_ms = result.source_end_ms;
                if (result.transition_at_ms <= 0) result.transition_at_ms = result.effective_end_ms;
                if (result.cue_out_ms <= 0) result.cue_out_ms = result.transition_at_ms;
            }
        }
        free(metrics.items);

        /* The decoder must be prepared before analysis_ready becomes visible.
         * Otherwise select/transition can wake between the descriptor commit and
         * wb_audio_probe_prepare_deck(), exposing an empty target deck. */
        (void)pthread_mutex_lock(&state->lock);
        current = !worker->shutdown
            && worker->generation == generation
            && identity_matches(deck_for(state, worker->deck), &track);
        (void)pthread_mutex_unlock(&state->lock);
        if (!current) {
            (void)pthread_mutex_lock(&state->lock);
            worker->running = false;
            state->audio_analysis_superseded_count += 1U;
            (void)pthread_cond_broadcast(&worker->cond);
            (void)pthread_mutex_unlock(&state->lock);
            continue;
        }

        wb_audio_probe_prepare_deck(state, worker->deck, &result);

        (void)pthread_mutex_lock(&state->lock);
        current = !worker->shutdown
            && worker->generation == generation
            && identity_matches(deck_for(state, worker->deck), &track);
        if (current) {
            *deck_for(state, worker->deck) = result;
            worker->track = result;
            worker->running = false;
            if (result.analysis_failed) state->audio_analysis_failed_count += 1U;
            else state->audio_analysis_ready_count += 1U;
            (void)pthread_cond_broadcast(&worker->cond);
        } else {
            worker->running = false;
            state->audio_analysis_superseded_count += 1U;
            (void)pthread_cond_broadcast(&worker->cond);
        }
        (void)pthread_mutex_unlock(&state->lock);

        if (!current) continue;
        wb_native_timing_wake(state);
        emit_analysis_event(
            state,
            result.analysis_failed ? "native_audio_analysis_failed" : "native_audio_analysis_ready",
            &result,
            worker->deck,
            monotonic_ms() - started_ms
        );
    }
}

static int init_worker(WbEngineState *state, WbAudioAnalysisWorker *worker, char deck) {
    memset(worker, 0, sizeof(*worker));
    worker->owner = state;
    worker->deck = deck;
    (void)pthread_cond_init(&worker->cond, NULL);
    if (pthread_create(&worker->thread, NULL, analysis_worker_main, worker) != 0) {
        (void)pthread_cond_destroy(&worker->cond);
        return -1;
    }
    worker->thread_created = true;
    return 0;
}

int wb_audio_analysis_init(WbEngineState *state) {
    state->audio_analysis_timeout_ms = 10000;
    if (init_worker(state, &state->analysis_deck_a, 'A') != 0) return -1;
    if (init_worker(state, &state->analysis_deck_b, 'B') != 0) {
        state->analysis_deck_a.shutdown = true;
        (void)pthread_cond_broadcast(&state->analysis_deck_a.cond);
        (void)pthread_join(state->analysis_deck_a.thread, NULL);
        (void)pthread_cond_destroy(&state->analysis_deck_a.cond);
        state->analysis_deck_a.thread_created = false;
        return -1;
    }
    return 0;
}

void wb_audio_analysis_destroy(WbEngineState *state) {
    WbAudioAnalysisWorker *workers[2] = {&state->analysis_deck_a, &state->analysis_deck_b};
    size_t index;
    (void)pthread_mutex_lock(&state->lock);
    for (index = 0U; index < 2U; index += 1U) {
        workers[index]->shutdown = true;
        workers[index]->generation += 1U;
        (void)pthread_cond_broadcast(&workers[index]->cond);
    }
    (void)pthread_mutex_unlock(&state->lock);
    for (index = 0U; index < 2U; index += 1U) {
        if (workers[index]->thread_created) {
            (void)pthread_join(workers[index]->thread, NULL);
            workers[index]->thread_created = false;
        }
        (void)pthread_cond_destroy(&workers[index]->cond);
    }
}

void wb_audio_analysis_schedule(WbEngineState *state, char deck, const WbDeckState *track) {
    WbAudioAnalysisWorker *worker = worker_for(state, deck);
    (void)pthread_mutex_lock(&state->lock);
    worker->generation += 1U;
    worker->track = *track;
    worker->request_pending = true;
    worker->running = false;
    (void)pthread_cond_broadcast(&worker->cond);
    (void)pthread_mutex_unlock(&state->lock);
}

bool wb_audio_analysis_wait_ready(
    WbEngineState *state,
    char deck,
    int64_t queue_id,
    const char *slot_token,
    int timeout_ms,
    WbDeckState *result
) {
    WbAudioAnalysisWorker *worker = worker_for(state, deck);
    struct timespec deadline;
    bool ready = false;
    if (clock_gettime(CLOCK_REALTIME, &deadline) != 0) return false;
    if (timeout_ms < 0) timeout_ms = 0;
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec += 1;
        deadline.tv_nsec -= 1000000000L;
    }
    (void)pthread_mutex_lock(&state->lock);
    for (;;) {
        WbDeckState *live = deck_for(state, deck);
        bool identity = live->loaded && (
            (slot_token != NULL && slot_token[0] != '\0' && strcmp(live->slot_token, slot_token) == 0)
            || ((slot_token == NULL || slot_token[0] == '\0') && queue_id > 0 && live->queue_id == queue_id)
        );
        if (!identity) break;
        if (live->analysis_ready || !live->analysis_requested || live->manual_timing) {
            if (result != NULL) *result = *live;
            ready = true;
            break;
        }
        if (pthread_cond_timedwait(&worker->cond, &state->lock, &deadline) == ETIMEDOUT) break;
    }
    (void)pthread_mutex_unlock(&state->lock);
    return ready;
}

bool wb_audio_analysis_snapshot(
    WbEngineState *state,
    char deck,
    const WbDeckState *identity,
    WbDeckState *result
) {
    bool matched = false;
    (void)pthread_mutex_lock(&state->lock);
    if (identity_matches(deck_for(state, deck), identity)) {
        if (result != NULL) *result = *deck_for(state, deck);
        matched = true;
    }
    (void)pthread_mutex_unlock(&state->lock);
    return matched;
}
