#define _POSIX_C_SOURCE 200809L

#include "diagnostics.h"
#include "protocol.h"

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#define WB_DIAGNOSTICS_DEFAULT_INTERVAL_MS 60000
#define WB_DIAGNOSTICS_MIN_INTERVAL_MS 100
#define WB_DIAGNOSTICS_MAX_INTERVAL_MS 3600000

typedef struct {
    int64_t runtime_ms;
    uint64_t rss_kb;
    uint64_t virtual_memory_kb;
    uint64_t cpu_user_ms;
    uint64_t cpu_system_ms;
    uint64_t thread_count;
    uint64_t fd_count;
    uint64_t child_process_count;
    pid_t decoder_pids[4];
    pid_t encoder_pid;
    pid_t dsp_pid;
    uint64_t active_voice_count;
    uint64_t candidate_count;
    size_t deck_a_ring_buffer_bytes;
    size_t deck_b_ring_buffer_bytes;
    size_t deck_a_ring_high_water_bytes;
    size_t deck_b_ring_high_water_bytes;
    size_t icecast_deck_a_fifo_bytes;
    size_t icecast_deck_b_fifo_bytes;
    size_t icecast_deck_a_fifo_high_water_bytes;
    size_t icecast_deck_b_fifo_high_water_bytes;
    uint64_t snapshot_count;
    uint64_t max_rss_kb;
    uint64_t max_virtual_memory_kb;
    uint64_t max_thread_count;
    uint64_t max_fd_count;
} WbDiagnosticSnapshot;

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
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

static void sleep_ms(int milliseconds) {
    struct timespec delay;
    if (milliseconds <= 0) return;
    delay.tv_sec = milliseconds / 1000;
    delay.tv_nsec = (long)(milliseconds % 1000) * 1000000L;
    while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
        /* retry */
    }
}

static uint64_t count_directory_entries(const char *path) {
    DIR *directory = opendir(path);
    struct dirent *entry;
    uint64_t count = 0U;
    if (directory == NULL) return 0U;
    while ((entry = readdir(directory)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        count += 1U;
    }
    (void)closedir(directory);
    return count;
}

static void read_proc_status(uint64_t *rss_kb, uint64_t *virtual_memory_kb, uint64_t *thread_count) {
    FILE *file = fopen("/proc/self/status", "r");
    char line[512];
    if (rss_kb != NULL) *rss_kb = 0U;
    if (virtual_memory_kb != NULL) *virtual_memory_kb = 0U;
    if (thread_count != NULL) *thread_count = 0U;
    if (file == NULL) return;
    while (fgets(line, sizeof(line), file) != NULL) {
        unsigned long long value = 0ULL;
        if (rss_kb != NULL && sscanf(line, "VmRSS: %llu kB", &value) == 1) {
            *rss_kb = (uint64_t)value;
        } else if (virtual_memory_kb != NULL && sscanf(line, "VmSize: %llu kB", &value) == 1) {
            *virtual_memory_kb = (uint64_t)value;
        } else if (thread_count != NULL && sscanf(line, "Threads: %llu", &value) == 1) {
            *thread_count = (uint64_t)value;
        }
    }
    (void)fclose(file);
}

static uint64_t timeval_ms(const struct timeval *value) {
    if (value == NULL) return 0U;
    return (uint64_t)value->tv_sec * 1000U + (uint64_t)(value->tv_usec / 1000);
}

static bool pid_already_present(const pid_t *pids, size_t count, pid_t pid) {
    size_t index;
    if (pid <= 0) return true;
    for (index = 0U; index < count; index += 1U) {
        if (pids[index] == pid) return true;
    }
    return false;
}

static void collect_snapshot(WbEngineState *state, WbDiagnosticSnapshot *snapshot, bool increment_count) {
    struct rusage usage;
    WbAudioDeckProbe *probes[4];
    size_t index;
    size_t pid_count = 0U;
    int64_t now = monotonic_ms();
    memset(snapshot, 0, sizeof(*snapshot));
    read_proc_status(&snapshot->rss_kb, &snapshot->virtual_memory_kb, &snapshot->thread_count);
    snapshot->fd_count = count_directory_entries("/proc/self/fd");
    if (getrusage(RUSAGE_SELF, &usage) == 0) {
        snapshot->cpu_user_ms = timeval_ms(&usage.ru_utime);
        snapshot->cpu_system_ms = timeval_ms(&usage.ru_stime);
    }

    (void)pthread_mutex_lock(&state->lock);
    if (state->diagnostics_started_monotonic_ms <= 0) state->diagnostics_started_monotonic_ms = now;
    snapshot->runtime_ms = now - state->diagnostics_started_monotonic_ms;
    probes[0] = &state->audio_deck_a;
    probes[1] = &state->audio_deck_a_alt;
    probes[2] = &state->audio_deck_b;
    probes[3] = &state->audio_deck_b_alt;
    for (index = 0U; index < 4U; index += 1U) {
        WbAudioDeckProbe *probe = probes[index];
        if (probe->child_pid > 0 && !pid_already_present(snapshot->decoder_pids, pid_count, probe->child_pid)) {
            snapshot->decoder_pids[pid_count++] = probe->child_pid;
        }
        if (probe->running && probe->activated) snapshot->active_voice_count += 1U;
        if (probe->running && !probe->activated) snapshot->candidate_count += 1U;
        if (probe->deck == 'B') {
            if (probe->ring_fill > snapshot->deck_b_ring_buffer_bytes) snapshot->deck_b_ring_buffer_bytes = probe->ring_fill;
            if (probe->ring_high_water_bytes > snapshot->deck_b_ring_high_water_bytes) snapshot->deck_b_ring_high_water_bytes = probe->ring_high_water_bytes;
        } else {
            if (probe->ring_fill > snapshot->deck_a_ring_buffer_bytes) snapshot->deck_a_ring_buffer_bytes = probe->ring_fill;
            if (probe->ring_high_water_bytes > snapshot->deck_a_ring_high_water_bytes) snapshot->deck_a_ring_high_water_bytes = probe->ring_high_water_bytes;
        }
    }
    if (increment_count) state->diagnostic_snapshot_count += 1U;
    state->diagnostic_last_rss_kb = snapshot->rss_kb;
    state->diagnostic_last_virtual_memory_kb = snapshot->virtual_memory_kb;
    state->diagnostic_last_cpu_user_ms = snapshot->cpu_user_ms;
    state->diagnostic_last_cpu_system_ms = snapshot->cpu_system_ms;
    state->diagnostic_last_thread_count = snapshot->thread_count;
    state->diagnostic_last_fd_count = snapshot->fd_count;
    if (snapshot->rss_kb > state->diagnostic_max_rss_kb) state->diagnostic_max_rss_kb = snapshot->rss_kb;
    if (snapshot->virtual_memory_kb > state->diagnostic_max_virtual_memory_kb) state->diagnostic_max_virtual_memory_kb = snapshot->virtual_memory_kb;
    if (snapshot->thread_count > state->diagnostic_max_thread_count) state->diagnostic_max_thread_count = snapshot->thread_count;
    if (snapshot->fd_count > state->diagnostic_max_fd_count) state->diagnostic_max_fd_count = snapshot->fd_count;
    snapshot->snapshot_count = state->diagnostic_snapshot_count;
    snapshot->max_rss_kb = state->diagnostic_max_rss_kb;
    snapshot->max_virtual_memory_kb = state->diagnostic_max_virtual_memory_kb;
    snapshot->max_thread_count = state->diagnostic_max_thread_count;
    snapshot->max_fd_count = state->diagnostic_max_fd_count;
    (void)pthread_mutex_unlock(&state->lock);

    (void)pthread_mutex_lock(&state->icecast_output.lock);
    snapshot->encoder_pid = state->icecast_output.encoder_pid;
    snapshot->dsp_pid = state->icecast_output.dsp_pid;
    snapshot->icecast_deck_a_fifo_bytes = state->icecast_output.deck_a_fill;
    snapshot->icecast_deck_b_fifo_bytes = state->icecast_output.deck_b_fill;
    snapshot->icecast_deck_a_fifo_high_water_bytes = state->icecast_output.deck_a_fifo_high_water_bytes;
    snapshot->icecast_deck_b_fifo_high_water_bytes = state->icecast_output.deck_b_fifo_high_water_bytes;
    (void)pthread_mutex_unlock(&state->icecast_output.lock);
    snapshot->child_process_count = pid_count
        + (snapshot->encoder_pid > 0 ? 1U : 0U)
        + (snapshot->dsp_pid > 0 ? 1U : 0U);
}

static void format_pid_array(const WbDiagnosticSnapshot *snapshot, char *buffer, size_t size) {
    size_t used = 0U;
    size_t index;
    int written;
    if (size == 0U) return;
    buffer[0] = '\0';
    written = snprintf(buffer, size, "[");
    if (written < 0 || (size_t)written >= size) return;
    used = (size_t)written;
    for (index = 0U; index < 4U; index += 1U) {
        pid_t pid = snapshot->decoder_pids[index];
        if (pid <= 0) continue;
        written = snprintf(buffer + used, size - used, "%s%ld", used > 1U ? "," : "", (long)pid);
        if (written < 0 || (size_t)written >= size - used) break;
        used += (size_t)written;
    }
    if (snapshot->encoder_pid > 0 && used + 2U < size) {
        written = snprintf(buffer + used, size - used, "%s%ld", used > 1U ? "," : "", (long)snapshot->encoder_pid);
        if (written > 0 && (size_t)written < size - used) used += (size_t)written;
    }
    if (snapshot->dsp_pid > 0 && used + 2U < size) {
        written = snprintf(buffer + used, size - used, "%s%ld", used > 1U ? "," : "", (long)snapshot->dsp_pid);
        if (written > 0 && (size_t)written < size - used) used += (size_t)written;
    }
    if (used + 2U <= size) {
        buffer[used++] = ']';
        buffer[used] = '\0';
    }
}

static int snapshot_json(const WbDiagnosticSnapshot *snapshot, const char *reason, char *output_json, size_t output_size) {
    char escaped_reason[WB_DIAGNOSTIC_REASON_SIZE * 2U];
    char pids[256];
    wb_json_escape(reason == NULL ? "manual" : reason, escaped_reason, sizeof(escaped_reason));
    format_pid_array(snapshot, pids, sizeof(pids));
    return snprintf(
        output_json,
        output_size,
        "{\"supported\":true,\"reason\":\"%s\",\"runtime_ms\":%lld,"
        "\"rss_kb\":%llu,\"virtual_memory_kb\":%llu,"
        "\"cpu_user_ms\":%llu,\"cpu_system_ms\":%llu,"
        "\"thread_count\":%llu,\"fd_count\":%llu,"
        "\"child_process_count\":%llu,\"child_pids\":%s,\"encoder_pid\":%ld,\"dsp_pid\":%ld,"
        "\"active_voice_count\":%llu,\"candidate_count\":%llu,"
        "\"deck_a_ring_buffer_bytes\":%zu,\"deck_b_ring_buffer_bytes\":%zu,"
        "\"deck_a_ring_high_water_bytes\":%zu,\"deck_b_ring_high_water_bytes\":%zu,"
        "\"icecast_deck_a_fifo_bytes\":%zu,\"icecast_deck_b_fifo_bytes\":%zu,"
        "\"icecast_deck_a_fifo_high_water_bytes\":%zu,\"icecast_deck_b_fifo_high_water_bytes\":%zu,"
        "\"snapshot_count\":%llu,\"max_rss_kb\":%llu,"
        "\"max_virtual_memory_kb\":%llu,\"max_thread_count\":%llu,\"max_fd_count\":%llu}",
        escaped_reason,
        (long long)snapshot->runtime_ms,
        (unsigned long long)snapshot->rss_kb,
        (unsigned long long)snapshot->virtual_memory_kb,
        (unsigned long long)snapshot->cpu_user_ms,
        (unsigned long long)snapshot->cpu_system_ms,
        (unsigned long long)snapshot->thread_count,
        (unsigned long long)snapshot->fd_count,
        (unsigned long long)snapshot->child_process_count,
        pids,
        (long)snapshot->encoder_pid,
        (long)snapshot->dsp_pid,
        (unsigned long long)snapshot->active_voice_count,
        (unsigned long long)snapshot->candidate_count,
        snapshot->deck_a_ring_buffer_bytes,
        snapshot->deck_b_ring_buffer_bytes,
        snapshot->deck_a_ring_high_water_bytes,
        snapshot->deck_b_ring_high_water_bytes,
        snapshot->icecast_deck_a_fifo_bytes,
        snapshot->icecast_deck_b_fifo_bytes,
        snapshot->icecast_deck_a_fifo_high_water_bytes,
        snapshot->icecast_deck_b_fifo_high_water_bytes,
        (unsigned long long)snapshot->snapshot_count,
        (unsigned long long)snapshot->max_rss_kb,
        (unsigned long long)snapshot->max_virtual_memory_kb,
        (unsigned long long)snapshot->max_thread_count,
        (unsigned long long)snapshot->max_fd_count
    );
}

void wb_diagnostics_emit_snapshot(WbEngineState *state, const char *reason) {
    WbDiagnosticSnapshot snapshot;
    WbDeckState empty = {0};
    char payload[4096];
    collect_snapshot(state, &snapshot, true);
    if (snapshot_json(&snapshot, reason, payload, sizeof(payload)) < 0) return;
    (void)wb_engine_send_event(state, "native_resource_snapshot", &empty, '-', payload);
}

int wb_diagnostics_state_json(WbEngineState *state, char *output_json, size_t output_size) {
    WbDiagnosticSnapshot snapshot;
    collect_snapshot(state, &snapshot, false);
    return snapshot_json(&snapshot, "status", output_json, output_size);
}

static bool diagnostics_should_stop(WbEngineState *state) {
    bool stop;
    (void)pthread_mutex_lock(&state->lock);
    stop = state->diagnostics_shutdown;
    (void)pthread_mutex_unlock(&state->lock);
    return stop;
}

static void *diagnostics_thread_main(void *context) {
    WbEngineState *state = context;
    int elapsed = 0;
    while (!diagnostics_should_stop(state)) {
        sleep_ms(100);
        elapsed += 100;
        if (elapsed >= state->diagnostics_interval_ms) {
            elapsed = 0;
            if (!diagnostics_should_stop(state)) wb_diagnostics_emit_snapshot(state, "periodic");
        }
    }
    return NULL;
}

int wb_diagnostics_init(WbEngineState *state) {
    state->diagnostics_interval_ms = env_int(
        "WEB_BROADCASTER_DIAGNOSTIC_INTERVAL_MS",
        WB_DIAGNOSTICS_DEFAULT_INTERVAL_MS,
        WB_DIAGNOSTICS_MIN_INTERVAL_MS,
        WB_DIAGNOSTICS_MAX_INTERVAL_MS
    );
    state->diagnostics_started_monotonic_ms = monotonic_ms();
    state->diagnostics_shutdown = false;
    if (pthread_create(&state->diagnostics_thread, NULL, diagnostics_thread_main, state) != 0) {
        return -1;
    }
    state->diagnostics_thread_created = true;
    return 0;
}

void wb_diagnostics_destroy(WbEngineState *state) {
    (void)pthread_mutex_lock(&state->lock);
    state->diagnostics_shutdown = true;
    (void)pthread_mutex_unlock(&state->lock);
    if (state->diagnostics_thread_created) {
        (void)pthread_join(state->diagnostics_thread, NULL);
        state->diagnostics_thread_created = false;
    }
}
