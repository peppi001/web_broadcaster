#define _POSIX_C_SOURCE 200809L

#include "icecast_output.h"
#include "audio_probe.h"
#include "native_timing.h"
#include "protocol.h"
#include "libav_bridge.h"
#include "ssnative/ssnative.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/time.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define WB_OUTPUT_TICK_MS 20
#define WB_OUTPUT_TICK_FRAMES ((WB_AUDIO_SAMPLE_RATE * WB_OUTPUT_TICK_MS) / 1000)
#define WB_OUTPUT_TICK_BYTES (WB_OUTPUT_TICK_FRAMES * WB_AUDIO_FRAME_BYTES)
#define WB_OUTPUT_FIFO_MS 3000
#define WB_OUTPUT_CONNECT_TIMEOUT_MS 3000
#define WB_OUTPUT_ENCODER_STOP_GRACE_MS 250
#define WB_OUTPUT_ENCODER_STALL_TIMEOUT_MS 15000
#define WB_OUTPUT_DSP_STARTUP_TIMEOUT_MS 45000
#define WB_OUTPUT_DSP_INPUT_STALL_TIMEOUT_MS 6000
#define WB_OUTPUT_DSP_PIPE_ERROR_GRACE_MS 1500
#define WB_OUTPUT_DSP_WRITER_POLL_MS 100
#define WB_OUTPUT_DSP_INPUT_FIFO_MS 8000
#define WB_OUTPUT_DSP_WRITER_CHUNK 65536U
#define WB_OUTPUT_DSP_READER_CHUNK (WB_OUTPUT_TICK_BYTES * 4U)
#define WB_OUTPUT_ICECAST_SEND_TIMEOUT_MS 1000
#define WB_OUTPUT_METADATA_RETRY_MS 2000
#define WB_OUTPUT_METADATA_RESPONSE_TIMEOUT_MS 3000
#define WB_OUTPUT_GAP_THRESHOLD_MS 250
#define WB_OUTPUT_UNDERRUN_EVENT_INTERVAL_MS 5000
#define WB_OUTPUT_DSP_METADATA_DELAY_MS 1000
#define WB_STREAM_ENCODED_FIFO_BYTES (512U * 1024U)
#define WB_STREAM_WORKER_CHUNK 32768U
/* Early EOF can leave decoded MP3 padding/silence in the outgoing deck FIFO.
 * Preserve real tail audio, but do not hold an ID/spot behind inaudible data. */
#define WB_EARLY_EOF_SILENCE_PEAK_THRESHOLD 16
#define WB_EARLY_EOF_SILENCE_PAD_FRAMES ((WB_AUDIO_SAMPLE_RATE * 5) / 1000)

typedef struct {
    bool occurred;
    char from_deck;
    char to_deck;
    bool early_fifo;
    bool waited_for_outgoing_drain;
    size_t switch_frame;
    int64_t scheduled_monotonic_ms;
    int64_t actual_monotonic_ms;
    WbDeckState from_track;
    WbDeckState to_track;
} WbHardHandoffSnapshot;

typedef struct {
    bool occurred;
    uint64_t count;
    int64_t monotonic_ms;
    int64_t tick_lateness_ms;
    char primary_deck;
    bool transitioning;
    char transition_from_deck;
    char transition_to_deck;
    bool dsp_enabled;
    bool dsp_running;
    bool dsp_ready;
    pid_t dsp_pid;
    pid_t encoder_pid;
    bool active_a;
    bool active_b;
    bool expected_a;
    bool expected_b;
    bool started_a;
    bool started_b;
    size_t got_a;
    size_t got_b;
    size_t fifo_a_before;
    size_t fifo_b_before;
    size_t fifo_a_after;
    size_t fifo_b_after;
    size_t fifo_capacity;
    int64_t queue_id_a;
    int64_t queue_id_b;
    char slot_token_a[WB_SLOT_TOKEN_SIZE];
    char slot_token_b[WB_SLOT_TOKEN_SIZE];
    double gain_a;
    double gain_b;
} WbOutputUnderrunSnapshot;

static void commit_hard_handoff_metadata(
    WbEngineState *state, char deck, const WbDeckState *track
);
static void finalize_hard_handoff(
    WbEngineState *state, const WbHardHandoffSnapshot *handoff
);
static void libav_encoded_sink(
    void *context,
    size_t stream_index,
    const unsigned char *data,
    size_t length
);
static void emit_output_event(WbEngineState *state, const char *event, const char *detail);

static void copy_text(char *destination, size_t size, const char *source) {
    size_t length;
    if (size == 0U) return;
    if (source == NULL) source = "";
    length = strnlen(source, size - 1U);
    if (length > 0U) memcpy(destination, source, length);
    destination[length] = '\0';
}

static int64_t monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static double smoothstep01(double value) {
    if (value <= 0.0) return 0.0;
    if (value >= 1.0) return 1.0;
    return value * value * (3.0 - 2.0 * value);
}

static double wb_fade_out_gain_at(double elapsed_ms, int64_t fade_out_ms) {
    double progress;
    if (fade_out_ms <= 0) return 0.0;
    if (elapsed_ms <= 0.0) return 1.0;
    if (elapsed_ms >= (double)fade_out_ms) return 0.0;
    progress = elapsed_ms / (double)fade_out_ms;
    return 1.0 - smoothstep01(progress);
}

static double wb_entry_gain_at(double elapsed_ms, int64_t entry_ramp_ms) {
    double progress;
    if (entry_ramp_ms <= 0) return 1.0;
    if (elapsed_ms <= 0.0) return 0.0;
    if (elapsed_ms >= (double)entry_ramp_ms) return 1.0;
    progress = elapsed_ms / (double)entry_ramp_ms;
    return smoothstep01(progress);
}

static double wb_fade_out_gain(int64_t elapsed_ms, int64_t fade_out_ms) {
    return wb_fade_out_gain_at((double)elapsed_ms, fade_out_ms);
}

static double wb_entry_gain(int64_t elapsed_ms, int64_t entry_ramp_ms) {
    return wb_entry_gain_at((double)elapsed_ms, entry_ramp_ms);
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

static void close_fd(int *fd) {
    if (fd != NULL && *fd >= 0) {
        close(*fd);
        *fd = -1;
    }
}

static bool native_output_id_valid(const char *value) {
    size_t index;
    if (value == NULL || value[0] == '\0') return false;
    for (index = 0U; value[index] != '\0'; index += 1U) {
        unsigned char ch = (unsigned char)value[index];
        if (!((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z')
            || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_')) return false;
    }
    return index < WB_NATIVE_OUTPUT_ID_SIZE;
}

static bool normalize_codec(const char *value, char *codec, size_t codec_size) {
    const char *source = value == NULL ? "" : value;
    if (strcmp(source, "mp3") == 0 || source[0] == '\0') {
        copy_text(codec, codec_size, "mp3");
        return true;
    }
    if (strcmp(source, "aacplus") == 0 || strcmp(source, "aac+") == 0
        || strcmp(source, "aac_he_v2") == 0 || strcmp(source, "he-aac-v2") == 0) {
        copy_text(codec, codec_size, "aac_he_v2");
        return true;
    }
    return false;
}

static const char *codec_content_type(const char *codec) {
    return codec != NULL && strcmp(codec, "aac_he_v2") == 0 ? "audio/aacp" : "audio/mpeg";
}

static int codec_default_bitrate(const char *codec) {
    return codec != NULL && strcmp(codec, "aac_he_v2") == 0 ? 64 : 192;
}

static bool codec_bitrate_valid(const char *codec, int bitrate_kbps) {
    if (codec != NULL && strcmp(codec, "aac_he_v2") == 0) {
        return bitrate_kbps >= 24 && bitrate_kbps <= 96;
    }
    return bitrate_kbps >= 32 && bitrate_kbps <= 320;
}

static WbNativeStreamOutput *stream_by_id_locked(
    WbIcecastOutput *output,
    const char *output_id,
    bool create
) {
    size_t index;
    WbNativeStreamOutput *free_slot = NULL;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        if (stream->configured && strcmp(stream->output_id, output_id) == 0) return stream;
        if (!stream->configured && free_slot == NULL) free_slot = stream;
    }
    if (!create || free_slot == NULL) return NULL;
    {
        WbEngineState *owner = free_slot->owner;
        pthread_t thread = free_slot->thread;
        bool thread_created = free_slot->thread_created;
        bool worker_shutdown = free_slot->worker_shutdown;
        size_t slot_index = free_slot->slot_index;
        unsigned char *encoded_fifo = free_slot->encoded_fifo;
        size_t encoded_fifo_capacity = free_slot->encoded_fifo_capacity;
        memset(free_slot, 0, sizeof(*free_slot));
        free_slot->owner = owner;
        free_slot->thread = thread;
        free_slot->thread_created = thread_created;
        free_slot->worker_shutdown = worker_shutdown;
        free_slot->slot_index = slot_index;
        free_slot->encoded_fifo = encoded_fifo;
        free_slot->encoded_fifo_capacity = encoded_fifo_capacity;
    }
    free_slot->configured = true;
    free_slot->encoder_stdout_fd = -1;
    free_slot->icecast_fd = -1;
    free_slot->port = 8000;
    copy_text(free_slot->output_id, sizeof(free_slot->output_id), output_id);
    copy_text(free_slot->codec, sizeof(free_slot->codec), "mp3");
    copy_text(free_slot->content_type, sizeof(free_slot->content_type), "audio/mpeg");
    copy_text(free_slot->username, sizeof(free_slot->username), "source");
    free_slot->mount[0] = '\0';
    free_slot->stream_name[0] = '\0';
    copy_text(free_slot->status, sizeof(free_slot->status), "disabled");
    free_slot->bitrate_kbps = 192;
    output->stream_count += 1U;
    return free_slot;
}

static size_t enabled_stream_count_locked(const WbIcecastOutput *output) {
    size_t count = 0U;
    size_t index;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        const WbNativeStreamOutput *stream = &output->streams[index];
        if (stream->configured && stream->enabled) count += 1U;
    }
    return count;
}

static size_t connected_stream_count_locked(const WbIcecastOutput *output) {
    size_t count = 0U;
    size_t index;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        const WbNativeStreamOutput *stream = &output->streams[index];
        if (stream->configured && stream->enabled && stream->connected) count += 1U;
    }
    return count;
}

static WbNativeStreamOutput *default_stream_locked(WbIcecastOutput *output) {
    size_t index;
    WbNativeStreamOutput *first = NULL;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        if (!stream->configured) continue;
        if (first == NULL) first = stream;
        if (strcmp(stream->output_id, "mp3") == 0) return stream;
    }
    return first;
}

static void mirror_default_stream_locked(WbIcecastOutput *output) {
    WbNativeStreamOutput *stream = default_stream_locked(output);
    output->enabled = enabled_stream_count_locked(output) > 0U;
    output->connected = connected_stream_count_locked(output) > 0U;
    if (stream == NULL) {
        output->public_stream = false;
        output->add_year_to_metadata = false;
        output->port = 8000;
        output->bitrate_kbps = 192;
        output->host[0] = '\0';
        output->mount[0] = '\0';
        copy_text(output->username, sizeof(output->username), "source");
        output->password[0] = '\0';
        output->stream_name[0] = '\0';
        output->stream_description[0] = '\0';
        output->stream_genre[0] = '\0';
        output->stream_url[0] = '\0';
        copy_text(output->status, sizeof(output->status), output->enabled ? "configured" : "disabled");
        output->error[0] = '\0';
        return;
    }
    output->public_stream = stream->public_stream;
    output->add_year_to_metadata = stream->add_year_to_metadata;
    output->port = stream->port;
    output->bitrate_kbps = stream->bitrate_kbps;
    copy_text(output->host, sizeof(output->host), stream->host);
    copy_text(output->mount, sizeof(output->mount), stream->mount);
    copy_text(output->username, sizeof(output->username), stream->username);
    copy_text(output->password, sizeof(output->password), stream->password);
    copy_text(output->stream_name, sizeof(output->stream_name), stream->stream_name);
    copy_text(output->stream_description, sizeof(output->stream_description), stream->stream_description);
    copy_text(output->stream_genre, sizeof(output->stream_genre), stream->stream_genre);
    copy_text(output->stream_url, sizeof(output->stream_url), stream->stream_url);
    copy_text(output->status, sizeof(output->status), stream->status);
    copy_text(output->error, sizeof(output->error), stream->error);
    output->connect_count = stream->connect_count;
    output->disconnect_count = stream->disconnect_count;
    output->reconnect_count = stream->reconnect_count;
    output->send_error_count = stream->send_error_count;
    output->icecast_stall_count = stream->icecast_stall_count;
    output->consecutive_send_errors = stream->consecutive_send_errors;
    output->metadata_requested_count = stream->metadata_requested_count;
    output->metadata_applied_count = stream->metadata_applied_count;
    output->metadata_failed_count = stream->metadata_failed_count;
    output->encoded_bytes_total = stream->encoded_bytes_total;
    output->encoded_bytes_sent = stream->icecast_sent_bytes_total;
    output->icecast_sent_bytes_total = stream->icecast_sent_bytes_total;
    output->output_gap_count = stream->output_gap_count;
    output->max_output_gap_ms = stream->max_output_gap_ms;
    output->last_icecast_send_monotonic_ms = stream->last_icecast_send_monotonic_ms;
    output->last_successful_send_monotonic_ms = stream->last_successful_send_monotonic_ms;
    output->output_gap_started_monotonic_ms = stream->output_gap_started_monotonic_ms;
    output->reconnect_backoff_seconds = stream->reconnect_backoff_seconds;
    copy_text(output->last_output_gap_reason, sizeof(output->last_output_gap_reason), stream->last_output_gap_reason);
    output->metadata_pending = stream->metadata_pending;
    output->metadata_generation = stream->metadata_generation;
    output->metadata_applied_generation = stream->metadata_applied_generation;
    output->metadata_not_before_monotonic_ms = stream->metadata_not_before_monotonic_ms;
    output->current_metadata_queue_id = stream->current_metadata_queue_id;
    copy_text(output->current_metadata, sizeof(output->current_metadata), stream->current_metadata);
    copy_text(output->current_metadata_slot_token, sizeof(output->current_metadata_slot_token), stream->current_metadata_slot_token);
    copy_text(output->metadata_error, sizeof(output->metadata_error), stream->metadata_error);
}

static size_t fifo_write(
    unsigned char *buffer,
    size_t capacity,
    size_t *write_pos,
    size_t *fill,
    const unsigned char *data,
    size_t bytes
) {
    size_t available;
    size_t accepted;
    size_t first;
    if (buffer == NULL || capacity == 0U || data == NULL) return 0U;
    available = capacity - *fill;
    accepted = bytes < available ? bytes : available;
    accepted -= accepted % WB_AUDIO_FRAME_BYTES;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*write_pos + first > capacity) first = capacity - *write_pos;
    memcpy(buffer + *write_pos, data, first);
    if (accepted > first) memcpy(buffer, data + first, accepted - first);
    *write_pos = (*write_pos + accepted) % capacity;
    *fill += accepted;
    return accepted;
}

static size_t fifo_read(
    unsigned char *buffer,
    size_t capacity,
    size_t *read_pos,
    size_t *fill,
    unsigned char *destination,
    size_t bytes
) {
    size_t available;
    size_t accepted;
    size_t first;
    if (buffer == NULL || capacity == 0U || destination == NULL) return 0U;
    available = *fill;
    accepted = bytes < available ? bytes : available;
    accepted -= accepted % WB_AUDIO_FRAME_BYTES;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*read_pos + first > capacity) first = capacity - *read_pos;
    memcpy(destination, buffer + *read_pos, first);
    if (accepted > first) memcpy(destination + first, buffer, accepted - first);
    *read_pos = (*read_pos + accepted) % capacity;
    *fill -= accepted;
    return accepted;
}

static int32_t pcm_s16le_sample_at(
    const unsigned char *buffer,
    size_t capacity,
    size_t byte_index
) {
    uint16_t raw;
    size_t lo;
    size_t hi;
    if (buffer == NULL || capacity == 0U) return 0;
    lo = byte_index % capacity;
    hi = (lo + 1U) % capacity;
    raw = (uint16_t)buffer[lo] | ((uint16_t)buffer[hi] << 8U);
    return (raw & 0x8000U) != 0U ? (int32_t)raw - 65536 : (int32_t)raw;
}

static size_t trim_trailing_silence_fifo_locked(
    unsigned char *buffer,
    size_t capacity,
    size_t read_pos,
    size_t *write_pos,
    size_t *fill
) {
    size_t frames;
    size_t frame_index;
    size_t keep_frames = 0U;
    size_t trim_bytes;
    if (
        buffer == NULL || capacity == 0U || write_pos == NULL || fill == NULL
        || *fill < WB_AUDIO_FRAME_BYTES
    ) {
        return 0U;
    }
    *fill -= *fill % WB_AUDIO_FRAME_BYTES;
    frames = *fill / WB_AUDIO_FRAME_BYTES;
    for (frame_index = frames; frame_index > 0U; frame_index -= 1U) {
        size_t frame_offset = (read_pos + (frame_index - 1U) * WB_AUDIO_FRAME_BYTES) % capacity;
        int32_t left = pcm_s16le_sample_at(buffer, capacity, frame_offset);
        int32_t right = pcm_s16le_sample_at(buffer, capacity, frame_offset + 2U);
        if (left < 0) left = -left;
        if (right < 0) right = -right;
        if (
            left > WB_EARLY_EOF_SILENCE_PEAK_THRESHOLD
            || right > WB_EARLY_EOF_SILENCE_PEAK_THRESHOLD
        ) {
            keep_frames = frame_index;
            break;
        }
    }
    if (keep_frames > 0U && keep_frames < frames) {
        size_t padded = keep_frames + WB_EARLY_EOF_SILENCE_PAD_FRAMES;
        keep_frames = padded < frames ? padded : frames;
    }
    trim_bytes = (frames - keep_frames) * WB_AUDIO_FRAME_BYTES;
    if (trim_bytes == 0U) return 0U;
    *fill = keep_frames * WB_AUDIO_FRAME_BYTES;
    *write_pos = (read_pos + *fill) % capacity;
    return trim_bytes;
}

static size_t byte_fifo_write(
    unsigned char *buffer,
    size_t capacity,
    size_t *write_pos,
    size_t *fill,
    const unsigned char *data,
    size_t bytes
) {
    size_t available;
    size_t accepted;
    size_t first;
    if (buffer == NULL || capacity == 0U || data == NULL) return 0U;
    available = capacity - *fill;
    accepted = bytes < available ? bytes : available;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*write_pos + first > capacity) first = capacity - *write_pos;
    memcpy(buffer + *write_pos, data, first);
    if (accepted > first) memcpy(buffer, data + first, accepted - first);
    *write_pos = (*write_pos + accepted) % capacity;
    *fill += accepted;
    return accepted;
}

static size_t byte_fifo_read(
    unsigned char *buffer,
    size_t capacity,
    size_t *read_pos,
    size_t *fill,
    unsigned char *destination,
    size_t bytes
) {
    size_t accepted;
    size_t first;
    if (buffer == NULL || capacity == 0U || destination == NULL) return 0U;
    accepted = bytes < *fill ? bytes : *fill;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*read_pos + first > capacity) first = capacity - *read_pos;
    memcpy(destination, buffer + *read_pos, first);
    if (accepted > first) memcpy(destination + first, buffer, accepted - first);
    *read_pos = (*read_pos + accepted) % capacity;
    *fill -= accepted;
    return accepted;
}

static void reset_encoded_fifo_locked(WbNativeStreamOutput *stream) {
    stream->encoded_fifo_read_pos = 0U;
    stream->encoded_fifo_write_pos = 0U;
    stream->encoded_fifo_fill = 0U;
}

static void reset_dsp_input_fifo_locked(WbIcecastOutput *output, bool advance_generation) {
    output->dsp_input_fifo_read_pos = 0U;
    output->dsp_input_fifo_write_pos = 0U;
    output->dsp_input_fifo_fill = 0U;
    output->dsp_writer_backpressure_started_monotonic_ms = 0;
    output->dsp_writer_error_since_monotonic_ms = 0;
    output->dsp_writer_last_errno = 0;
    output->dsp_writer_last_revents = 0;
    if (advance_generation) output->dsp_writer_generation += 1U;
}

static void reset_fifo_locked(WbIcecastOutput *output, char deck) {
    if (deck == 'B') {
        output->deck_b_read_pos = 0U;
        output->deck_b_write_pos = 0U;
        output->deck_b_fill = 0U;
        output->deck_b_started = false;
    } else {
        output->deck_a_read_pos = 0U;
        output->deck_a_write_pos = 0U;
        output->deck_a_fill = 0U;
        output->deck_a_started = false;
    }
}

static void reset_all_fifos_locked(WbIcecastOutput *output) {
    reset_fifo_locked(output, 'A');
    reset_fifo_locked(output, 'B');
}

static void clear_hard_handoff_locked(WbIcecastOutput *output) {
    output->hard_handoff_pending = false;
    output->hard_handoff_wait_outgoing_drain = false;
    output->hard_handoff_from_deck = '\0';
    output->hard_handoff_to_deck = '\0';
    output->hard_handoff_at_monotonic_ms = 0;
    output->hard_handoff_requested_monotonic_ms = 0;
    memset(&output->hard_handoff_from_track, 0, sizeof(output->hard_handoff_from_track));
    memset(&output->hard_handoff_to_track, 0, sizeof(output->hard_handoff_to_track));
}

static bool identity_matches(const char *active_token, const WbDeckState *track) {
    if (active_token == NULL || track == NULL || active_token[0] == '\0') return false;
    return strcmp(active_token, track->slot_token) == 0;
}

static int base64_encode(const unsigned char *input, size_t length, char *output, size_t output_size) {
    static const char table[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t input_index = 0U;
    size_t output_index = 0U;
    while (input_index < length) {
        uint32_t value = 0U;
        size_t remaining = length - input_index;
        size_t count = remaining >= 3U ? 3U : remaining;
        size_t index;
        if (output_index + 4U >= output_size) return -1;
        for (index = 0U; index < count; index += 1U) {
            value |= (uint32_t)input[input_index + index] << (16U - (unsigned)(index * 8U));
        }
        output[output_index++] = table[(value >> 18U) & 0x3fU];
        output[output_index++] = table[(value >> 12U) & 0x3fU];
        output[output_index++] = count >= 2U ? table[(value >> 6U) & 0x3fU] : '=';
        output[output_index++] = count >= 3U ? table[value & 0x3fU] : '=';
        input_index += count;
    }
    output[output_index] = '\0';
    return 0;
}

/* 0 = success, -1 = socket error, -2 = send watchdog timeout. */
static int send_all_socket(int fd, const unsigned char *data, size_t length) {
    size_t offset = 0U;
    while (offset < length) {
        ssize_t sent = send(fd, data + offset, length - offset, MSG_NOSIGNAL);
        if (sent > 0) {
            offset += (size_t)sent;
            continue;
        }
        if (sent < 0 && errno == EINTR) continue;
        if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            struct pollfd descriptor = {.fd = fd, .events = POLLOUT};
            int result = poll(&descriptor, 1, WB_OUTPUT_ICECAST_SEND_TIMEOUT_MS);
            if (result > 0 && (descriptor.revents & POLLOUT) != 0) continue;
            if (result == 0) return -2;
        }
        return -1;
    }
    return 0;
}

static void set_nonblocking(int fd) {
    int flags;
    if (fd < 0) return;
    flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) (void)fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int url_encode(const char *input, char *output, size_t output_size) {
    static const char hex[] = "0123456789ABCDEF";
    size_t used = 0U;
    const unsigned char *cursor = (const unsigned char *)(input == NULL ? "" : input);
    while (*cursor != '\0') {
        unsigned char value = *cursor++;
        bool safe = (value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z')
            || (value >= '0' && value <= '9') || value == '-' || value == '_' || value == '.' || value == '~';
        if (safe) {
            if (used + 1U >= output_size) return -1;
            output[used++] = (char)value;
        } else {
            if (used + 3U >= output_size) return -1;
            output[used++] = '%';
            output[used++] = hex[(value >> 4U) & 0x0fU];
            output[used++] = hex[value & 0x0fU];
        }
    }
    if (used >= output_size) return -1;
    output[used] = '\0';
    return 0;
}

static int connect_tcp(const char *host, int port, char *error, size_t error_size) {
    struct addrinfo hints;
    struct addrinfo *addresses = NULL;
    struct addrinfo *current;
    char port_text[32];
    int fd = -1;
    int result;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    (void)snprintf(port_text, sizeof(port_text), "%d", port);
    result = getaddrinfo(host, port_text, &hints, &addresses);
    if (result != 0) {
        (void)snprintf(error, error_size, "getaddrinfo failed: %s", gai_strerror(result));
        return -1;
    }
    for (current = addresses; current != NULL; current = current->ai_next) {
        int flags;
        struct pollfd descriptor;
        int socket_error = 0;
        socklen_t socket_error_size = sizeof(socket_error);
        fd = socket(current->ai_family, current->ai_socktype, current->ai_protocol);
        if (fd < 0) continue;
        flags = fcntl(fd, F_GETFL, 0);
        if (flags >= 0) (void)fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        result = connect(fd, current->ai_addr, current->ai_addrlen);
        if (result != 0 && errno != EINPROGRESS) {
            close(fd);
            fd = -1;
            continue;
        }
        descriptor.fd = fd;
        descriptor.events = POLLOUT;
        descriptor.revents = 0;
        result = poll(&descriptor, 1, WB_OUTPUT_CONNECT_TIMEOUT_MS);
        if (result <= 0 || getsockopt(fd, SOL_SOCKET, SO_ERROR, &socket_error, &socket_error_size) != 0 || socket_error != 0) {
            close(fd);
            fd = -1;
            continue;
        }
        if (flags >= 0) (void)fcntl(fd, F_SETFL, flags);
        break;
    }
    freeaddrinfo(addresses);
    if (fd < 0) (void)snprintf(error, error_size, "cannot connect to %s:%d", host, port);
    return fd;
}

static void sanitize_icecast_header_value(
    const char *source,
    char *target,
    size_t target_size
) {
    size_t index;
    copy_text(target, target_size, source == NULL ? "" : source);
    for (index = 0U; target[index] != '\0'; index += 1U) {
        if (target[index] == '\r' || target[index] == '\n') target[index] = ' ';
    }
}

static int icecast_handshake(
    int fd,
    const char *host,
    int port,
    const char *mount,
    const char *username,
    const char *password,
    const char *stream_name,
    const char *stream_description,
    const char *stream_genre,
    const char *stream_url,
    const char *content_type,
    int bitrate_kbps,
    bool public_stream,
    char *error,
    size_t error_size
) {
    char credentials[WB_ICECAST_USER_SIZE + WB_ICECAST_PASSWORD_SIZE + 4U];
    char authorization[(WB_ICECAST_USER_SIZE + WB_ICECAST_PASSWORD_SIZE) * 2U];
    char escaped_name[WB_ICECAST_NAME_SIZE];
    char escaped_description[WB_ICECAST_DESCRIPTION_SIZE];
    char escaped_genre[WB_ICECAST_GENRE_SIZE];
    char escaped_url[WB_ICECAST_URL_SIZE];
    char request[4096];
    char response[4096];
    size_t used = 0U;
    int request_length;
    int64_t deadline;
    struct timeval timeout;
    (void)snprintf(credentials, sizeof(credentials), "%s:%s", username, password);
    if (base64_encode((const unsigned char *)credentials, strlen(credentials), authorization, sizeof(authorization)) != 0) {
        copy_text(error, error_size, "Icecast credentials are too long");
        return -1;
    }
    sanitize_icecast_header_value(stream_name, escaped_name, sizeof(escaped_name));
    sanitize_icecast_header_value(stream_description, escaped_description, sizeof(escaped_description));
    sanitize_icecast_header_value(stream_genre, escaped_genre, sizeof(escaped_genre));
    sanitize_icecast_header_value(stream_url, escaped_url, sizeof(escaped_url));
    request_length = snprintf(
        request,
        sizeof(request),
        "SOURCE %s ICE/1.0\r\n"
        "Host: %s:%d\r\n"
        "Authorization: Basic %s\r\n"
        "Content-Type: %s\r\n"
        "Ice-Name: %s\r\n"
        "Ice-Description: %s\r\n"
        "Ice-Genre: %s\r\n"
        "Ice-URL: %s\r\n"
        "Ice-Public: %d\r\n"
        "Ice-Bitrate: %d\r\n"
        "Ice-Audio-Info: ice-samplerate=%d;ice-bitrate=%d;ice-channels=%d\r\n"
        "\r\n",
        mount,
        host,
        port,
        authorization,
        content_type != NULL && content_type[0] != '\0' ? content_type : "audio/mpeg",
        escaped_name[0] != '\0' ? escaped_name : "Web Broadcaster",
        escaped_description,
        escaped_genre,
        escaped_url,
        public_stream ? 1 : 0,
        bitrate_kbps,
        WB_AUDIO_SAMPLE_RATE,
        bitrate_kbps,
        WB_AUDIO_CHANNELS
    );
    memset(credentials, 0, sizeof(credentials));
    memset(authorization, 0, sizeof(authorization));
    if (request_length <= 0 || (size_t)request_length >= sizeof(request)) {
        copy_text(error, error_size, "Icecast request is too large");
        return -1;
    }
    if (send_all_socket(fd, (const unsigned char *)request, (size_t)request_length) != 0) {
        (void)snprintf(error, error_size, "Icecast source handshake send failed: %s", strerror(errno));
        return -1;
    }
    timeout.tv_sec = 0;
    timeout.tv_usec = 250000;
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    deadline = monotonic_ms() + WB_OUTPUT_CONNECT_TIMEOUT_MS;
    while (used + 1U < sizeof(response) && monotonic_ms() < deadline) {
        ssize_t received = recv(fd, response + used, sizeof(response) - used - 1U, 0);
        if (received > 0) {
            used += (size_t)received;
            response[used] = '\0';
            if (strstr(response, "\r\n\r\n") != NULL || strstr(response, "\n\n") != NULL) break;
            continue;
        }
        if (received < 0 && errno == EINTR) continue;
        if (received < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        break;
    }
    timeout.tv_sec = 0;
    timeout.tv_usec = 0;
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    response[used] = '\0';
    if (used == 0U || strstr(response, " 200 ") == NULL) {
        char first_line[256] = "no response";
        char *newline;
        if (used > 0U) {
            copy_text(first_line, sizeof(first_line), response);
            newline = strpbrk(first_line, "\r\n");
            if (newline != NULL) *newline = '\0';
        }
        (void)snprintf(error, error_size, "Icecast rejected source: %s", first_line);
        return -1;
    }
    return 0;
}

static int icecast_send_metadata(
    const char *host,
    int port,
    const char *mount,
    const char *username,
    const char *password,
    const char *metadata,
    char *error,
    size_t error_size
) {
    char credentials[WB_ICECAST_USER_SIZE + WB_ICECAST_PASSWORD_SIZE + 4U];
    char authorization[(WB_ICECAST_USER_SIZE + WB_ICECAST_PASSWORD_SIZE) * 2U];
    char encoded_mount[WB_ICECAST_MOUNT_SIZE * 3U];
    char encoded_metadata[WB_ICECAST_METADATA_SIZE * 3U];
    char request[8192];
    char response[4096];
    size_t used = 0U;
    int request_length;
    int fd;
    int64_t deadline;
    struct timeval timeout;
    (void)snprintf(credentials, sizeof(credentials), "%s:%s", username, password);
    if (base64_encode((const unsigned char *)credentials, strlen(credentials), authorization, sizeof(authorization)) != 0) {
        memset(credentials, 0, sizeof(credentials));
        copy_text(error, error_size, "Icecast metadata credentials are too long");
        return -1;
    }
    if (url_encode(mount, encoded_mount, sizeof(encoded_mount)) != 0
        || url_encode(metadata, encoded_metadata, sizeof(encoded_metadata)) != 0) {
        memset(credentials, 0, sizeof(credentials));
        memset(authorization, 0, sizeof(authorization));
        copy_text(error, error_size, "Icecast metadata value is too long");
        return -1;
    }
    fd = connect_tcp(host, port, error, error_size);
    if (fd < 0) {
        memset(credentials, 0, sizeof(credentials));
        memset(authorization, 0, sizeof(authorization));
        return -1;
    }
    request_length = snprintf(
        request,
        sizeof(request),
        "GET /admin/metadata?mode=updinfo&mount=%s&song=%s HTTP/1.0\r\n"
        "Host: %s:%d\r\n"
        "Authorization: Basic %s\r\n"
        "Connection: close\r\n\r\n",
        encoded_mount,
        encoded_metadata,
        host,
        port,
        authorization
    );
    memset(credentials, 0, sizeof(credentials));
    memset(authorization, 0, sizeof(authorization));
    if (request_length <= 0 || (size_t)request_length >= sizeof(request)) {
        close(fd);
        copy_text(error, error_size, "Icecast metadata request is too large");
        return -1;
    }
    if (send_all_socket(fd, (const unsigned char *)request, (size_t)request_length) != 0) {
        close(fd);
        copy_text(error, error_size, "Icecast metadata request send failed");
        return -1;
    }
    timeout.tv_sec = 0;
    timeout.tv_usec = 250000;
    (void)setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    deadline = monotonic_ms() + WB_OUTPUT_METADATA_RESPONSE_TIMEOUT_MS;
    while (used + 1U < sizeof(response) && monotonic_ms() < deadline) {
        ssize_t received = recv(fd, response + used, sizeof(response) - used - 1U, 0);
        if (received > 0) {
            used += (size_t)received;
            response[used] = '\0';
            if (strstr(response, "\r\n\r\n") != NULL || strstr(response, "\n\n") != NULL) break;
            continue;
        }
        if (received < 0 && errno == EINTR) continue;
        if (received < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        break;
    }
    close(fd);
    response[used] = '\0';
    if (used == 0U || strstr(response, " 200 ") == NULL) {
        char first_line[256] = "no response";
        char *newline;
        if (used > 0U) {
            copy_text(first_line, sizeof(first_line), response);
            newline = strpbrk(first_line, "\r\n");
            if (newline != NULL) *newline = '\0';
        }
        (void)snprintf(error, error_size, "Icecast metadata rejected: %s", first_line);
        return -1;
    }
    return 0;
}

static int create_dsp_context(
    const char *config_path,
    ssnative_dsp **context_out,
    char *error,
    size_t error_size
) {
    ssnative_dsp *context;
    ssnative_status status;
    const char *detail;
    if (context_out == NULL || config_path == NULL || config_path[0] == '\0') {
        copy_text(error, error_size, "SoundSolution DSP configuration is missing");
        return -1;
    }
    *context_out = NULL;
    context = ssnative_create();
    if (context == NULL) {
        copy_text(error, error_size, "cannot allocate SoundSolution DSP context");
        return -1;
    }
    status = ssnative_load_dat(context, config_path);
    if (status != SSNATIVE_STATUS_OK) {
        detail = ssnative_last_error(context);
        (void)snprintf(
            error,
            error_size,
            "SoundSolution DAT load failed: %s (%s)",
            detail != NULL && detail[0] != '\0' ? detail : ssnative_status_string(status),
            ssnative_status_string(status)
        );
        ssnative_destroy(context);
        return -1;
    }
    *context_out = context;
    return 0;
}

static int start_dsp_context_unlocked(
    WbIcecastOutput *output,
    bool live_bypass_until_ready,
    char *error,
    size_t error_size
) {
    char config_path[WB_PATH_SIZE];
    ssnative_dsp *context = NULL;
    bool accepted = false;
    int64_t started_ms = monotonic_ms();

    (void)pthread_mutex_lock(&output->lock);
    if (!output->dsp_enabled || output->encoder_context == NULL || output->dsp_context != NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        copy_text(error, error_size, "DSP pipeline state is not ready for an in-process context");
        return -1;
    }
    copy_text(config_path, sizeof(config_path), output->dsp_config_path);
    output->dsp_running = false;
    output->dsp_ready = false;
    output->dsp_route_active = false;
    output->dsp_live_bypass_until_ready = live_bypass_until_ready;
    output->dsp_output_failed = false;
    output->dsp_started_monotonic_ms = started_ms;
    copy_text(output->dsp_status, sizeof(output->dsp_status), "loading");
    output->dsp_error[0] = '\0';
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);

    if (create_dsp_context(config_path, &context, error, error_size) != 0) {
        (void)pthread_mutex_lock(&output->lock);
        output->dsp_running = false;
        output->dsp_ready = false;
        output->dsp_output_failed = true;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "failed");
        copy_text(output->dsp_error, sizeof(output->dsp_error), error);
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        return -1;
    }

    (void)pthread_mutex_lock(&output->pcm_route_lock);
    (void)pthread_mutex_lock(&output->lock);
    if (output->dsp_enabled && output->encoder_context != NULL && output->dsp_context == NULL) {
        output->dsp_context = context;
        context = NULL;
        output->dsp_pid = 0;
        output->new_dsp_pid = 0;
        output->dsp_running = true;
        output->dsp_ready = true;
        output->dsp_route_active = false;
        output->dsp_live_bypass_until_ready = live_bypass_until_ready;
        output->dsp_output_failed = false;
        output->dsp_start_count += 1U;
        output->dsp_started_monotonic_ms = started_ms;
        output->dsp_writer_last_progress_monotonic_ms = started_ms;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "ready");
        output->dsp_error[0] = '\0';
        accepted = true;
        (void)pthread_cond_broadcast(&output->cond);
    }
    (void)pthread_mutex_unlock(&output->lock);
    (void)pthread_mutex_unlock(&output->pcm_route_lock);
    if (context != NULL) ssnative_destroy(context);
    if (!accepted) {
        copy_text(error, error_size, "DSP pipeline state changed while the context was loading");
        return -1;
    }
    return 0;
}

static int queue_encoder_pcm(WbIcecastOutput *output, unsigned char *data, size_t length) {
    WbLibavEncoderGroup *group;
    ssnative_dsp *context;
    bool dsp_requested;
    bool dry_fallback;
    bool emit_ready = false;
    bool live_switch = false;
    ssnative_status status = SSNATIVE_STATUS_OK;
    int result;
    if (data == NULL || length == 0U) return 0;
    if (length % WB_AUDIO_FRAME_BYTES != 0U) return -1;

    (void)pthread_mutex_lock(&output->pcm_route_lock);
    (void)pthread_mutex_lock(&output->lock);
    group = (WbLibavEncoderGroup *)output->encoder_context;
    context = (ssnative_dsp *)output->dsp_context;
    dsp_requested = output->dsp_enabled;
    dry_fallback = output->dsp_live_bypass_until_ready;
    if (group == NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->pcm_route_lock);
        return -1;
    }
    if (!dsp_requested) {
        (void)pthread_mutex_unlock(&output->lock);
        result = wb_libav_encoder_group_push_pcm(group, data, length);
        (void)pthread_mutex_unlock(&output->pcm_route_lock);
        return result;
    }
    if (context == NULL || !output->dsp_running) {
        (void)pthread_mutex_unlock(&output->lock);
        result = dry_fallback ? wb_libav_encoder_group_push_pcm(group, data, length) : -1;
        (void)pthread_mutex_unlock(&output->pcm_route_lock);
        return result;
    }
    output->dsp_input_bytes_enqueued += (uint64_t)length;
    (void)pthread_mutex_unlock(&output->lock);

    status = ssnative_process_s16_interleaved(
        context,
        (int16_t *)data,
        length / WB_AUDIO_FRAME_BYTES,
        WB_AUDIO_CHANNELS,
        WB_AUDIO_SAMPLE_RATE
    );
    if (status != SSNATIVE_STATUS_OK) {
        const char *detail = ssnative_last_error(context);
        (void)pthread_mutex_lock(&output->lock);
        output->dsp_running = false;
        output->dsp_ready = false;
        output->dsp_output_failed = true;
        output->dsp_write_error_count += 1U;
        (void)snprintf(
            output->dsp_error,
            sizeof(output->dsp_error),
            "SoundSolution processing failed: %s (%s)",
            detail != NULL && detail[0] != '\0' ? detail : ssnative_status_string(status),
            ssnative_status_string(status)
        );
        copy_text(output->dsp_status, sizeof(output->dsp_status), "failed");
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->pcm_route_lock);
        return -1;
    }

    result = wb_libav_encoder_group_push_pcm(group, data, length);
    (void)pthread_mutex_lock(&output->lock);
    if (output->encoder_context == group && output->dsp_context == context && output->dsp_enabled) {
        int64_t ready_ms = monotonic_ms();
        size_t metadata_index;
        output->dsp_input_bytes_written += (uint64_t)length;
        output->dsp_output_bytes_read += (uint64_t)length;
        output->dsp_writer_last_progress_monotonic_ms = ready_ms;
        if (result == 0) {
            if (!output->dsp_route_active) {
                live_switch = output->dsp_live_bypass_until_ready;
                output->dsp_route_active = true;
                output->dsp_ready = true;
                output->dsp_live_bypass_until_ready = false;
                output->dsp_output_failed = false;
                if (live_switch) output->dsp_live_switch_count += 1U;
                copy_text(output->dsp_status, sizeof(output->dsp_status), "ready");
                output->dsp_error[0] = '\0';
                for (metadata_index = 0U; metadata_index < WB_NATIVE_OUTPUT_MAX; metadata_index += 1U) {
                    WbNativeStreamOutput *stream = &output->streams[metadata_index];
                    if (stream->metadata_pending) {
                        int64_t due = ready_ms + WB_OUTPUT_DSP_METADATA_DELAY_MS;
                        if (stream->metadata_not_before_monotonic_ms < due) {
                            stream->metadata_not_before_monotonic_ms = due;
                        }
                    }
                }
                emit_ready = true;
            }
        } else {
            output->dsp_output_push_error_count += 1U;
            output->dsp_output_failed = true;
            copy_text(
                output->dsp_error,
                sizeof(output->dsp_error),
                "processed DSP PCM could not be queued into the embedded encoder"
            );
        }
        (void)pthread_cond_broadcast(&output->cond);
    }
    (void)pthread_mutex_unlock(&output->lock);
    (void)pthread_mutex_unlock(&output->pcm_route_lock);

    if (emit_ready) {
        emit_output_event(
            output->owner,
            live_switch ? "native_dsp_source_swapped" : "native_dsp_ready",
            live_switch
                ? "live PCM source switched from dry input to in-process SoundSolution DSP"
                : "in-process SoundSolution DSP ready"
        );
    }
    return result;
}

static int start_encoder_unlocked(WbIcecastOutput *output, char *error, size_t error_size) {
    WbLibavEncoderConfig configs[WB_NATIVE_OUTPUT_MAX];
    WbLibavEncoderGroup *group = NULL;
    size_t branch_count = 0U;
    size_t index;
    bool use_dsp;

    (void)pthread_mutex_lock(&output->lock);
    use_dsp = output->dsp_enabled;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        if (!stream->configured || !stream->enabled) continue;
        configs[branch_count].stream_index = index;
        copy_text(configs[branch_count].codec, sizeof(configs[branch_count].codec), stream->codec);
        configs[branch_count].bitrate_kbps = stream->bitrate_kbps;
        stream->encoder_ready = false;
        stream->last_encoded_data_monotonic_ms = monotonic_ms();
        stream->encoder_stdout_fd = -1;
        branch_count += 1U;
    }
    (void)pthread_mutex_unlock(&output->lock);
    if (branch_count == 0U) {
        copy_text(error, error_size, "no enabled native stream outputs");
        return -1;
    }

    if (wb_libav_encoder_group_start(
            &group, configs, branch_count, -1,
            libav_encoded_sink, output, error, error_size
        ) != 0) {
        return -1;
    }

    (void)pthread_mutex_lock(&output->lock);
    reset_dsp_input_fifo_locked(output, true);
    output->encoder_context = group;
    output->encoder_pid = 0;
    output->encoder_stdin_fd = -1;
    output->encoder_stdout_fd = -1;
    output->dsp_output_fd = -1;
    output->new_encoder_pid = 0;
    output->dsp_pid = 0;
    output->new_dsp_pid = 0;
    output->dsp_running = false;
    output->dsp_ready = !use_dsp;
    output->dsp_route_active = false;
    output->dsp_live_bypass_until_ready = false;
    output->dsp_output_failed = false;
    output->dsp_started_monotonic_ms = 0;
    output->encoder_generation += 1U;
    if (!use_dsp) {
        copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
        output->dsp_error[0] = '\0';
    }
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);

    if (use_dsp && start_dsp_context_unlocked(output, false, error, error_size) != 0) {
        (void)pthread_mutex_lock(&output->lock);
        if (output->encoder_context == group) output->encoder_context = NULL;
        output->encoder_running = false;
        for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
            output->streams[index].encoder_ready = false;
        }
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        wb_libav_encoder_group_destroy(group);
        return -1;
    }
    return 0;
}

static int start_encoder(WbIcecastOutput *output, char *error, size_t error_size) {
    int result;
    (void)pthread_mutex_lock(&output->encoder_control_lock);
    result = start_encoder_unlocked(output, error, error_size);
    (void)pthread_mutex_unlock(&output->encoder_control_lock);
    return result;
}

static void stop_encoder_unlocked(WbIcecastOutput *output) {
    WbLibavEncoderGroup *group;
    size_t index;
    (void)pthread_mutex_lock(&output->lock);
    group = (WbLibavEncoderGroup *)output->encoder_context;
    output->encoder_context = NULL;
    reset_dsp_input_fifo_locked(output, true);
    close_fd(&output->encoder_stdin_fd);
    output->encoder_stdout_fd = -1;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        output->streams[index].encoder_stdout_fd = -1;
        output->streams[index].encoder_ready = false;
    }
    output->encoder_pid = 0;
    output->encoder_running = false;
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    wb_libav_encoder_group_destroy(group);
}

/* Return values for live branch changes:
 *   0: the running encoder group was updated in place
 *   1: there is no stable running group to update; caller must let the
 *      normal pipeline start/restart path apply the configuration
 *  -1: the requested branch could not be created or removed
 */
static int add_live_encoder_branch(
    WbIcecastOutput *output,
    size_t stream_index,
    uint64_t expected_config_generation,
    const char *codec,
    int bitrate_kbps,
    char *error,
    size_t error_size
) {
    WbLibavEncoderConfig config = {0};
    WbLibavEncoderGroup *group;
    bool still_current;
    int result;

    config.stream_index = stream_index;
    copy_text(config.codec, sizeof(config.codec), codec);
    config.bitrate_kbps = bitrate_kbps;

    (void)pthread_mutex_lock(&output->encoder_control_lock);
    (void)pthread_mutex_lock(&output->lock);
    group = (WbLibavEncoderGroup *)output->encoder_context;
    if (
        group == NULL || !output->engine_running || !output->encoder_running
        || output->restart_requested
    ) {
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        return 1;
    }
    (void)pthread_mutex_unlock(&output->lock);

    result = wb_libav_encoder_group_add_branch(group, &config, error, error_size);
    if (result != 0) {
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        return -1;
    }

    (void)pthread_mutex_lock(&output->lock);
    still_current = stream_index < WB_NATIVE_OUTPUT_MAX
        && output->streams[stream_index].configured
        && output->streams[stream_index].enabled
        && output->streams[stream_index].config_generation == expected_config_generation
        && strcmp(output->streams[stream_index].codec, codec) == 0
        && output->streams[stream_index].bitrate_kbps == bitrate_kbps;
    if (still_current) {
        output->streams[stream_index].last_encoded_data_monotonic_ms = monotonic_ms();
        (void)pthread_cond_broadcast(&output->cond);
    }
    (void)pthread_mutex_unlock(&output->lock);

    if (!still_current) {
        (void)wb_libav_encoder_group_remove_branch(group, stream_index, NULL, 0U);
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        return 1;
    }
    (void)pthread_mutex_unlock(&output->encoder_control_lock);
    return 0;
}

static int remove_live_encoder_branch(
    WbIcecastOutput *output,
    size_t stream_index,
    char *error,
    size_t error_size
) {
    WbLibavEncoderGroup *group;
    int result;
    (void)pthread_mutex_lock(&output->encoder_control_lock);
    (void)pthread_mutex_lock(&output->lock);
    group = (WbLibavEncoderGroup *)output->encoder_context;
    if (
        group == NULL || !output->engine_running || !output->encoder_running
        || output->restart_requested
    ) {
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        return 1;
    }
    (void)pthread_mutex_unlock(&output->lock);

    result = wb_libav_encoder_group_remove_branch(group, stream_index, error, error_size);
    if (result == 0) {
        (void)pthread_mutex_lock(&output->lock);
        if (stream_index < WB_NATIVE_OUTPUT_MAX) {
            output->streams[stream_index].encoder_ready = false;
            output->streams[stream_index].encoder_stdout_fd = -1;
        }
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
    }
    (void)pthread_mutex_unlock(&output->encoder_control_lock);
    return result == 0 ? 0 : -1;
}


static void stop_dsp_context_unlocked(WbIcecastOutput *output) {
    ssnative_dsp *context;
    (void)pthread_mutex_lock(&output->pcm_route_lock);
    (void)pthread_mutex_lock(&output->lock);
    context = (ssnative_dsp *)output->dsp_context;
    output->dsp_context = NULL;
    output->dsp_pid = 0;
    output->new_dsp_pid = 0;
    output->dsp_running = false;
    output->dsp_ready = false;
    output->dsp_route_active = false;
    output->dsp_live_bypass_until_ready = false;
    output->dsp_output_failed = false;
    copy_text(
        output->dsp_status,
        sizeof(output->dsp_status),
        output->dsp_enabled ? "stopped" : "bypassed"
    );
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    if (context != NULL) ssnative_destroy(context);
    (void)pthread_mutex_unlock(&output->pcm_route_lock);
}

static int reconfigure_live_dsp(
    WbIcecastOutput *output,
    bool enabled,
    char *error,
    size_t error_size
) {
    bool pipeline_live;
    bool had_context;
    int result = 0;

    (void)pthread_mutex_lock(&output->encoder_control_lock);
    (void)pthread_mutex_lock(&output->lock);
    pipeline_live = output->engine_running
        && output->encoder_running
        && output->encoder_context != NULL
        && !output->restart_requested;
    had_context = output->dsp_context != NULL;
    if (enabled) output->dsp_live_bypass_until_ready = true;
    (void)pthread_mutex_unlock(&output->lock);
    if (!pipeline_live) {
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        copy_text(error, error_size, "Shared encoder pipeline is not stable for a live DSP source swap");
        return -1;
    }

    if (had_context) stop_dsp_context_unlocked(output);
    if (enabled) {
        result = start_dsp_context_unlocked(output, true, error, error_size);
    } else {
        (void)pthread_mutex_lock(&output->lock);
        output->dsp_enabled = false;
        output->dsp_running = false;
        output->dsp_ready = false;
        output->dsp_route_active = false;
        output->dsp_live_bypass_until_ready = false;
        output->dsp_output_failed = false;
        output->dsp_config_path[0] = '\0';
        copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
        output->dsp_error[0] = '\0';
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
    }
    (void)pthread_mutex_unlock(&output->encoder_control_lock);

    if (result != 0) {
        (void)pthread_mutex_lock(&output->lock);
        output->dsp_enabled = false;
        output->dsp_running = false;
        output->dsp_ready = false;
        output->dsp_route_active = false;
        output->dsp_live_bypass_until_ready = false;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "failed");
        copy_text(output->dsp_error, sizeof(output->dsp_error), error);
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        return -1;
    }

    emit_output_event(
        output->owner,
        enabled ? "native_dsp_live_ready" : "native_dsp_bypassed_live",
        enabled
            ? "live PCM source switched to in-process SoundSolution DSP"
            : "live PCM source switched from SoundSolution DSP to dry input"
    );
    return 0;
}

static void emit_output_event(WbEngineState *state, const char *event, const char *detail) {
    WbDeckState empty = {0};
    char escaped[WB_ICECAST_ERROR_SIZE * 2U];
    char payload[WB_ICECAST_ERROR_SIZE * 2U + 256U];
    wb_json_escape(detail == NULL ? "" : detail, escaped, sizeof(escaped));
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_icecast_output\":true,\"detail\":\"%s\"}",
        escaped
    );
    (void)wb_engine_send_event(state, event, &empty, '-', payload);
}

static const WbAudioDeckProbe *find_probe_for_token_locked(
    WbEngineState *state,
    char deck,
    const char *slot_token
) {
    const WbAudioDeckProbe *first = deck == 'B' ? &state->audio_deck_b : &state->audio_deck_a;
    const WbAudioDeckProbe *second = deck == 'B' ? &state->audio_deck_b_alt : &state->audio_deck_a_alt;
    if (slot_token != NULL && slot_token[0] != '\0') {
        if (strcmp(first->track.slot_token, slot_token) == 0) return first;
        if (strcmp(second->track.slot_token, slot_token) == 0) return second;
    }
    if (first->activated || first->running) return first;
    if (second->activated || second->running) return second;
    return first;
}

static void emit_output_underrun_event(
    WbEngineState *state,
    const WbOutputUnderrunSnapshot *snapshot
) {
    WbDeckState event_track = {0};
    const WbAudioDeckProbe *probe_a;
    const WbAudioDeckProbe *probe_b;
    pid_t decoder_pid_a = 0;
    pid_t decoder_pid_b = 0;
    size_t decoder_ring_a = 0U;
    size_t decoder_ring_b = 0U;
    int64_t decoder_position_a = 0;
    int64_t decoder_position_b = 0;
    char escaped_token_a[WB_SLOT_TOKEN_SIZE * 2U];
    char escaped_token_b[WB_SLOT_TOKEN_SIZE * 2U];
    char payload[4096];
    WbIcecastOutput *output;
    uint64_t event_count;
    uint64_t suppressed_total;
    uint64_t suppressed_since_last;
    if (state == NULL || snapshot == NULL || !snapshot->occurred) return;

    output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    if (output->last_output_underrun_event_monotonic_ms > 0
        && snapshot->monotonic_ms - output->last_output_underrun_event_monotonic_ms
            < WB_OUTPUT_UNDERRUN_EVENT_INTERVAL_MS) {
        output->output_underrun_suppressed_event_count += 1U;
        output->output_underrun_suppressed_since_last_event += 1U;
        (void)pthread_mutex_unlock(&output->lock);
        return;
    }
    suppressed_since_last = output->output_underrun_suppressed_since_last_event;
    output->output_underrun_suppressed_since_last_event = 0U;
    output->last_output_underrun_event_monotonic_ms = snapshot->monotonic_ms;
    output->output_underrun_event_count += 1U;
    event_count = output->output_underrun_event_count;
    suppressed_total = output->output_underrun_suppressed_event_count;
    (void)pthread_mutex_unlock(&output->lock);

    (void)pthread_mutex_lock(&state->lock);
    probe_a = find_probe_for_token_locked(state, 'A', snapshot->slot_token_a);
    probe_b = find_probe_for_token_locked(state, 'B', snapshot->slot_token_b);
    decoder_pid_a = probe_a->child_pid;
    decoder_pid_b = probe_b->child_pid;
    decoder_ring_a = probe_a->ring_fill;
    decoder_ring_b = probe_b->ring_fill;
    decoder_position_a = probe_a->position_ms;
    decoder_position_b = probe_b->position_ms;
    if (snapshot->primary_deck == 'B') event_track = state->deck_b;
    else if (snapshot->primary_deck == 'A') event_track = state->deck_a;
    (void)pthread_mutex_unlock(&state->lock);

    wb_json_escape(snapshot->slot_token_a, escaped_token_a, sizeof(escaped_token_a));
    wb_json_escape(snapshot->slot_token_b, escaped_token_b, sizeof(escaped_token_b));
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_icecast_output\":true,\"reason\":\"mixed_output_pcm_missing\","
        "\"output_underrun_count\":%llu,\"underrun_event_count\":%llu,"
        "\"suppressed_since_last_event\":%llu,\"suppressed_event_count\":%llu,"
        "\"tick_monotonic_ms\":%lld,\"tick_lateness_ms\":%lld,"
        "\"primary_deck\":\"%c\",\"transitioning\":%s,"
        "\"transition_from_deck\":\"%c\",\"transition_to_deck\":\"%c\","
        "\"deck_a_queue_id\":%lld,\"deck_a_slot_token\":\"%s\","
        "\"deck_b_queue_id\":%lld,\"deck_b_slot_token\":\"%s\","
        "\"deck_a_active\":%s,\"deck_b_active\":%s,"
        "\"deck_a_expected\":%s,\"deck_b_expected\":%s,"
        "\"deck_a_started\":%s,\"deck_b_started\":%s,"
        "\"deck_a_gain\":%.6f,\"deck_b_gain\":%.6f,"
        "\"deck_a_pcm_bytes\":%zu,\"deck_b_pcm_bytes\":%zu,"
        "\"deck_a_output_fifo_before\":%zu,\"deck_b_output_fifo_before\":%zu,"
        "\"deck_a_output_fifo_after\":%zu,\"deck_b_output_fifo_after\":%zu,"
        "\"output_fifo_capacity_bytes\":%zu,"
        "\"deck_a_decoder_pid\":%ld,\"deck_b_decoder_pid\":%ld,"
        "\"deck_a_decoder_ring_bytes\":%zu,\"deck_b_decoder_ring_bytes\":%zu,"
        "\"deck_a_position_ms\":%lld,\"deck_b_position_ms\":%lld,"
        "\"dsp_enabled\":%s,\"dsp_running\":%s,\"dsp_ready\":%s,"
        "\"dsp_pid\":%ld,\"encoder_pid\":%ld}",
        (unsigned long long)snapshot->count,
        (unsigned long long)event_count,
        (unsigned long long)suppressed_since_last,
        (unsigned long long)suppressed_total,
        (long long)snapshot->monotonic_ms,
        (long long)snapshot->tick_lateness_ms,
        snapshot->primary_deck ? snapshot->primary_deck : '-',
        snapshot->transitioning ? "true" : "false",
        snapshot->transition_from_deck ? snapshot->transition_from_deck : '-',
        snapshot->transition_to_deck ? snapshot->transition_to_deck : '-',
        (long long)snapshot->queue_id_a,
        escaped_token_a,
        (long long)snapshot->queue_id_b,
        escaped_token_b,
        snapshot->active_a ? "true" : "false",
        snapshot->active_b ? "true" : "false",
        snapshot->expected_a ? "true" : "false",
        snapshot->expected_b ? "true" : "false",
        snapshot->started_a ? "true" : "false",
        snapshot->started_b ? "true" : "false",
        snapshot->gain_a,
        snapshot->gain_b,
        snapshot->got_a,
        snapshot->got_b,
        snapshot->fifo_a_before,
        snapshot->fifo_b_before,
        snapshot->fifo_a_after,
        snapshot->fifo_b_after,
        snapshot->fifo_capacity,
        (long)decoder_pid_a,
        (long)decoder_pid_b,
        decoder_ring_a,
        decoder_ring_b,
        (long long)decoder_position_a,
        (long long)decoder_position_b,
        snapshot->dsp_enabled ? "true" : "false",
        snapshot->dsp_running ? "true" : "false",
        snapshot->dsp_ready ? "true" : "false",
        (long)snapshot->dsp_pid,
        (long)snapshot->encoder_pid
    );
    (void)wb_engine_send_event(
        state,
        "native_output_underrun",
        &event_track,
        snapshot->primary_deck ? snapshot->primary_deck : '-',
        payload
    );
}

static void emit_metadata_event(
    WbEngineState *state,
    const char *event,
    bool requested,
    bool applied,
    bool failed,
    const char *output_id,
    const char *codec,
    const char *metadata,
    int64_t queue_id,
    const char *slot_token,
    const char *detail
) {
    WbDeckState empty = {0};
    char escaped_output_id[WB_NATIVE_OUTPUT_ID_SIZE * 2U];
    char escaped_codec[WB_NATIVE_OUTPUT_CODEC_SIZE * 2U];
    char escaped_metadata[WB_ICECAST_METADATA_SIZE * 2U];
    char escaped_token[WB_SLOT_TOKEN_SIZE * 2U];
    char escaped_detail[WB_ICECAST_ERROR_SIZE * 2U];
    char payload[WB_ICECAST_METADATA_SIZE * 2U + WB_SLOT_TOKEN_SIZE * 2U + WB_ICECAST_ERROR_SIZE * 2U + 384U];
    wb_json_escape(output_id == NULL ? "" : output_id, escaped_output_id, sizeof(escaped_output_id));
    wb_json_escape(codec == NULL ? "" : codec, escaped_codec, sizeof(escaped_codec));
    wb_json_escape(metadata == NULL ? "" : metadata, escaped_metadata, sizeof(escaped_metadata));
    wb_json_escape(slot_token == NULL ? "" : slot_token, escaped_token, sizeof(escaped_token));
    wb_json_escape(detail == NULL ? "" : detail, escaped_detail, sizeof(escaped_detail));
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_icecast_output\":true,\"output_id\":\"%s\",\"codec\":\"%s\","
        "\"metadata_requested\":%s,"
        "\"metadata_applied\":%s,\"metadata_failed\":%s,"
        "\"metadata_value\":\"%s\",\"queue_id\":%lld,"
        "\"slot_token\":\"%s\",\"detail\":\"%s\"}",
        escaped_output_id,
        escaped_codec,
        requested ? "true" : "false",
        applied ? "true" : "false",
        failed ? "true" : "false",
        escaped_metadata,
        (long long)queue_id,
        escaped_token,
        escaped_detail
    );
    (void)wb_engine_send_event(state, event, &empty, '-', payload);
}

static void sanitize_metadata_text(char *text) {
    size_t index;
    if (text == NULL) return;
    for (index = 0U; text[index] != '\0'; index += 1U) {
        if (text[index] == '\r' || text[index] == '\n' || text[index] == '\t') text[index] = ' ';
    }
}

static void normalize_track_year(const char *value, char *year, size_t year_size) {
    size_t index;
    size_t length;
    if (year_size == 0U) return;
    year[0] = '\0';
    if (value == NULL) return;
    length = strlen(value);
    for (index = 0U; index + 3U < length; index += 1U) {
        if ((value[index] == '1' || value[index] == '2')
            && value[index + 1U] >= '0' && value[index + 1U] <= '9'
            && value[index + 2U] >= '0' && value[index + 2U] <= '9'
            && value[index + 3U] >= '0' && value[index + 3U] <= '9') {
            (void)snprintf(year, year_size, "%.4s", value + index);
            return;
        }
    }
}

static void strip_path_extension(char *text) {
    char *last_dot;
    char *last_slash;
    if (text == NULL || text[0] == '\0') return;
    last_dot = strrchr(text, '.');
    last_slash = strrchr(text, '/');
    if (last_dot != NULL && (last_slash == NULL || last_dot > last_slash)) {
        *last_dot = '\0';
    }
}

static void format_track_metadata(
    const WbDeckState *track,
    bool add_year_to_metadata,
    char *metadata,
    size_t metadata_size
) {
    const char *basename;
    char artist[WB_TRACK_ARTIST_SIZE];
    char title[WB_TRACK_TITLE_SIZE];
    char year[WB_TRACK_YEAR_SIZE];
    char base_metadata[WB_ICECAST_METADATA_SIZE];
    if (metadata_size == 0U) return;
    metadata[0] = '\0';
    if (track == NULL) return;
    copy_text(artist, sizeof(artist), track->artist);
    copy_text(title, sizeof(title), track->title);
    normalize_track_year(track->year, year, sizeof(year));
    sanitize_metadata_text(artist);
    sanitize_metadata_text(title);
    base_metadata[0] = '\0';
    if (artist[0] != '\0' && title[0] != '\0') {
        (void)snprintf(base_metadata, sizeof(base_metadata), "%s - %s", artist, title);
    } else if (title[0] != '\0') {
        copy_text(base_metadata, sizeof(base_metadata), title);
    } else if (artist[0] != '\0') {
        copy_text(base_metadata, sizeof(base_metadata), artist);
    } else {
        basename = strrchr(track->path, '/');
        copy_text(base_metadata, sizeof(base_metadata), basename == NULL ? track->path : basename + 1);
        strip_path_extension(base_metadata);
    }
    sanitize_metadata_text(base_metadata);
    if (add_year_to_metadata && year[0] != '\0' && base_metadata[0] != '\0') {
        (void)snprintf(metadata, metadata_size, "%s (%s)", base_metadata, year);
    } else {
        copy_text(metadata, metadata_size, base_metadata);
    }
    sanitize_metadata_text(metadata);
}

static void close_all_stream_connections(WbIcecastOutput *output, bool count_disconnect) {
    size_t index;
    (void)pthread_mutex_lock(&output->lock);
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        bool was_connected = stream->connected;
        close_fd(&stream->icecast_fd);
        stream->connected = false;
        reset_encoded_fifo_locked(stream);
        if (count_disconnect && was_connected) stream->disconnect_count += 1U;
        if (stream->configured) {
            copy_text(stream->status, sizeof(stream->status), stream->enabled ? "configured" : "disabled");
        }
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
}

static void stop_pipeline(WbIcecastOutput *output, bool count_disconnect) {
    (void)pthread_mutex_lock(&output->encoder_control_lock);
    stop_dsp_context_unlocked(output);
    stop_encoder_unlocked(output);
    (void)pthread_mutex_unlock(&output->encoder_control_lock);
    close_all_stream_connections(output, count_disconnect);
    (void)pthread_mutex_lock(&output->lock);
    output->encoder_running = false;
    if (enabled_stream_count_locked(output) == 0U) {
        output->dsp_enabled = false;
        output->dsp_executable_path[0] = '\0';
        output->dsp_config_path[0] = '\0';
        output->dsp_log_path[0] = '\0';
        output->dsp_running = false;
        output->dsp_ready = false;
        output->dsp_route_active = false;
        output->dsp_live_bypass_until_ready = false;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
        output->dsp_error[0] = '\0';
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
}

static size_t mix_tick_locked(
    WbIcecastOutput *output,
    unsigned char *mixed,
    int64_t now_ms,
    int64_t tick_lateness_ms,
    WbOutputUnderrunSnapshot *underrun,
    WbHardHandoffSnapshot *handoff
) {
    unsigned char deck_a[WB_OUTPUT_TICK_BYTES];
    unsigned char deck_b[WB_OUTPUT_TICK_BYTES];
    size_t got_a = 0U;
    size_t got_b = 0U;
    double gain_a = 0.0;
    double gain_b = 0.0;
    bool active_a = output->deck_a_slot_token[0] != '\0' && output->deck_a_started;
    bool active_b = output->deck_b_slot_token[0] != '\0' && output->deck_b_started;
    bool expected_a = false;
    bool expected_b = false;
    bool audible_expected = false;
    bool audible_pcm_present = false;
    bool transition_active = false;
    bool hard_handoff_pending = output->hard_handoff_pending;
    int64_t hard_handoff_delta_ms = hard_handoff_pending
        ? output->hard_handoff_at_monotonic_ms - now_ms : 0;
    /* Keep the ordinary primary-deck mixer path until the tick that actually
     * contains the hard boundary. Entering the special path for the entire
     * armed window could expose harmless partial outgoing FIFO ticks as PCM
     * holes and, worse, would make the zero-gain target eligible for reads. */
    bool hard_handoff_tick = hard_handoff_pending && (
        output->hard_handoff_wait_outgoing_drain
        || hard_handoff_delta_ms <= WB_OUTPUT_TICK_MS
    );
    bool hard_handoff_switch = false;
    bool hard_handoff_early_fifo = false;
    size_t hard_switch_frame = WB_OUTPUT_TICK_FRAMES + 1U;
    size_t hard_old_frames = 0U;
    size_t hard_target_frames = 0U;
    int64_t transition_elapsed_ms = 0;
    int64_t transition_entry_elapsed_ms = 0;
    size_t frame;
    size_t fifo_a_before = output->deck_a_fill;
    size_t fifo_b_before = output->deck_b_fill;
    if (hard_handoff_pending && !hard_handoff_tick) {
        /* The primed target must remain frozen until the audible boundary. */
        if (output->hard_handoff_to_deck == 'A') active_a = false;
        if (output->hard_handoff_to_deck == 'B') active_b = false;
    }
    if (underrun != NULL) memset(underrun, 0, sizeof(*underrun));
    if (handoff != NULL) memset(handoff, 0, sizeof(*handoff));
    memset(deck_a, 0, sizeof(deck_a));
    memset(deck_b, 0, sizeof(deck_b));
    if (output->paused) {
        memset(mixed, 0, WB_OUTPUT_TICK_BYTES);
        output->mixed_frames += WB_OUTPUT_TICK_FRAMES;
        output->silence_frames += WB_OUTPUT_TICK_FRAMES;
        return WB_OUTPUT_TICK_BYTES;
    }

    if (hard_handoff_tick) {
        char from_deck = output->hard_handoff_from_deck;
        char to_deck = output->hard_handoff_to_deck;
        size_t old_bytes = 0U;
        int64_t delta_ms = hard_handoff_delta_ms;
        bool hard_wait_drain = output->hard_handoff_wait_outgoing_drain;

        if (from_deck == 'B' && active_b) {
            got_b = fifo_read(
                output->deck_b_fifo, output->fifo_capacity,
                &output->deck_b_read_pos, &output->deck_b_fill,
                deck_b, sizeof(deck_b)
            );
            old_bytes = got_b;
        } else if (from_deck == 'A' && active_a) {
            got_a = fifo_read(
                output->deck_a_fifo, output->fifo_capacity,
                &output->deck_a_read_pos, &output->deck_a_fill,
                deck_a, sizeof(deck_a)
            );
            old_bytes = got_a;
        }
        hard_old_frames = old_bytes / WB_AUDIO_FRAME_BYTES;

        if (output->hard_handoff_wait_outgoing_drain) {
            if (hard_old_frames < WB_OUTPUT_TICK_FRAMES) {
                hard_switch_frame = hard_old_frames;
                hard_handoff_early_fifo = true;
            }
        } else if (delta_ms <= 0) {
            hard_switch_frame = 0U;
        } else if (delta_ms < WB_OUTPUT_TICK_MS) {
            uint64_t frames_until = ((uint64_t)delta_ms * WB_AUDIO_SAMPLE_RATE + 999ULL) / 1000ULL;
            if (frames_until > WB_OUTPUT_TICK_FRAMES) frames_until = WB_OUTPUT_TICK_FRAMES;
            hard_switch_frame = (size_t)frames_until;
        }

        if (
            hard_switch_frame <= WB_OUTPUT_TICK_FRAMES
            && !output->hard_handoff_wait_outgoing_drain
            && hard_old_frames < hard_switch_frame
        ) {
            hard_switch_frame = hard_old_frames;
            hard_handoff_early_fifo = true;
        }

        if (hard_switch_frame <= WB_OUTPUT_TICK_FRAMES) {
            size_t target_bytes_wanted =
                (WB_OUTPUT_TICK_FRAMES - hard_switch_frame) * WB_AUDIO_FRAME_BYTES;
            unsigned char *target_buffer = to_deck == 'B' ? deck_b : deck_a;
            size_t *target_got = to_deck == 'B' ? &got_b : &got_a;
            size_t *target_read_pos = to_deck == 'B'
                ? &output->deck_b_read_pos : &output->deck_a_read_pos;
            size_t *target_fill = to_deck == 'B'
                ? &output->deck_b_fill : &output->deck_a_fill;
            unsigned char *target_fifo = to_deck == 'B'
                ? output->deck_b_fifo : output->deck_a_fifo;
            bool target_active = to_deck == 'B' ? active_b : active_a;

            if (target_bytes_wanted == 0U) {
                hard_target_frames = 0U;
                hard_handoff_switch = true;
            } else if (target_active) {
                *target_got = fifo_read(
                    target_fifo, output->fifo_capacity,
                    target_read_pos, target_fill,
                    target_buffer + hard_switch_frame * WB_AUDIO_FRAME_BYTES,
                    target_bytes_wanted
                );
                hard_target_frames = *target_got / WB_AUDIO_FRAME_BYTES;
                hard_handoff_switch = *target_got > 0U;
            }

            if (hard_handoff_switch) {
                output->primary_deck = to_deck;
                output->hard_handoff_pending = false;
                output->hard_handoff_wait_outgoing_drain = false;
                output->hard_handoff_count += 1U;
                if (hard_handoff_early_fifo) output->hard_handoff_early_eof_count += 1U;
                if (handoff != NULL) {
                    handoff->occurred = true;
                    handoff->from_deck = from_deck;
                    handoff->to_deck = to_deck;
                    handoff->early_fifo = hard_handoff_early_fifo;
                    handoff->waited_for_outgoing_drain = hard_wait_drain;
                    handoff->switch_frame = hard_switch_frame;
                    handoff->scheduled_monotonic_ms = output->hard_handoff_at_monotonic_ms;
                    handoff->actual_monotonic_ms = now_ms + (int64_t)(
                        ((uint64_t)hard_switch_frame * 1000ULL) / WB_AUDIO_SAMPLE_RATE
                    );
                    handoff->from_track = output->hard_handoff_from_track;
                    handoff->to_track = output->hard_handoff_to_track;
                }
            }
        }
        gain_a = from_deck == 'A' ? 1.0 : 0.0;
        gain_b = from_deck == 'B' ? 1.0 : 0.0;
    } else {
        if (active_a) {
            got_a = fifo_read(
                output->deck_a_fifo, output->fifo_capacity,
                &output->deck_a_read_pos, &output->deck_a_fill,
                deck_a, sizeof(deck_a)
            );
        }
        if (active_b) {
            got_b = fifo_read(
                output->deck_b_fifo, output->fifo_capacity,
                &output->deck_b_read_pos, &output->deck_b_fill,
                deck_b, sizeof(deck_b)
            );
        }
        if (output->transitioning && output->transition_duration_ms > 0) {
            int64_t elapsed_ms = now_ms - output->transition_start_monotonic_ms;
            int64_t entry_elapsed_ms = output->transition_entry_start_monotonic_ms > 0
                ? now_ms - output->transition_entry_start_monotonic_ms
                : 0;
            double old_gain = wb_fade_out_gain(elapsed_ms, output->transition_fade_out_ms);
            double new_gain = output->transition_entry_start_monotonic_ms > 0
                ? wb_entry_gain(entry_elapsed_ms, output->transition_entry_ramp_ms)
                : 0.0;
            transition_active = true;
            transition_elapsed_ms = elapsed_ms;
            transition_entry_elapsed_ms = entry_elapsed_ms;
            if (output->transition_from_deck == 'A') gain_a = old_gain;
            if (output->transition_from_deck == 'B') gain_b = old_gain;
            if (output->transition_to_deck == 'A') gain_a = new_gain;
            if (output->transition_to_deck == 'B') gain_b = new_gain;
            if (elapsed_ms >= output->transition_duration_ms) {
                output->transitioning = false;
                output->primary_deck = output->transition_to_deck;
                output->transition_entry_waiting_for_pcm = false;
                gain_a = output->primary_deck == 'A' ? 1.0 : 0.0;
                gain_b = output->primary_deck == 'B' ? 1.0 : 0.0;
            }
        } else {
            gain_a = output->primary_deck == 'A' ? 1.0 : 0.0;
            gain_b = output->primary_deck == 'B' ? 1.0 : 0.0;
        }
    }

    if (hard_handoff_tick) {
        size_t old_needed = hard_switch_frame <= WB_OUTPUT_TICK_FRAMES
            ? hard_switch_frame : WB_OUTPUT_TICK_FRAMES;
        size_t target_needed = hard_switch_frame <= WB_OUTPUT_TICK_FRAMES
            ? WB_OUTPUT_TICK_FRAMES - hard_switch_frame : 0U;
        char from_deck = output->hard_handoff_from_deck;
        char to_deck = output->hard_handoff_to_deck;
        expected_a = (from_deck == 'A' && old_needed > 0U)
            || (to_deck == 'A' && target_needed > 0U);
        expected_b = (from_deck == 'B' && old_needed > 0U)
            || (to_deck == 'B' && target_needed > 0U);
        audible_expected = old_needed > 0U || target_needed > 0U;
        audible_pcm_present = hard_old_frames >= old_needed
            && hard_target_frames >= target_needed;
        if (hard_old_frames < old_needed || hard_target_frames < target_needed) {
            output->deck_fifo_empty_count += 1U;
        }
    } else {
        expected_a = active_a && gain_a > 0.0001;
        expected_b = active_b && gain_b > 0.0001;
        audible_expected = expected_a || expected_b;
        audible_pcm_present = (expected_a && got_a > 0U) || (expected_b && got_b > 0U);
        if (expected_a && output->deck_a_started && got_a < sizeof(deck_a)) {
            output->deck_fifo_empty_count += 1U;
        }
        if (expected_b && output->deck_b_started && got_b < sizeof(deck_b)) {
            output->deck_fifo_empty_count += 1U;
        }
    }

    if (audible_expected && !audible_pcm_present) {
        output->mixed_output_silence_count += 1U;
        output->output_underrun_count += 1U;
        if (underrun != NULL) {
            underrun->occurred = true;
            underrun->count = output->output_underrun_count;
            underrun->monotonic_ms = now_ms;
            underrun->tick_lateness_ms = tick_lateness_ms;
            underrun->primary_deck = output->primary_deck;
            underrun->transitioning = output->transitioning;
            underrun->transition_from_deck = output->transition_from_deck;
            underrun->transition_to_deck = output->transition_to_deck;
            underrun->dsp_enabled = output->dsp_enabled;
            underrun->dsp_running = output->dsp_running;
            underrun->dsp_ready = output->dsp_ready;
            underrun->dsp_pid = output->dsp_pid;
            underrun->encoder_pid = output->encoder_pid;
            underrun->active_a = active_a;
            underrun->active_b = active_b;
            underrun->expected_a = expected_a;
            underrun->expected_b = expected_b;
            underrun->started_a = output->deck_a_started;
            underrun->started_b = output->deck_b_started;
            underrun->got_a = got_a;
            underrun->got_b = got_b;
            underrun->fifo_a_before = fifo_a_before;
            underrun->fifo_b_before = fifo_b_before;
            underrun->fifo_a_after = output->deck_a_fill;
            underrun->fifo_b_after = output->deck_b_fill;
            underrun->fifo_capacity = output->fifo_capacity;
            underrun->queue_id_a = output->deck_a_queue_id;
            underrun->queue_id_b = output->deck_b_queue_id;
            copy_text(underrun->slot_token_a, sizeof(underrun->slot_token_a), output->deck_a_slot_token);
            copy_text(underrun->slot_token_b, sizeof(underrun->slot_token_b), output->deck_b_slot_token);
            underrun->gain_a = gain_a;
            underrun->gain_b = gain_b;
        }
    }

    for (frame = 0U; frame < WB_OUTPUT_TICK_FRAMES * WB_AUDIO_CHANNELS; frame += 1U) {
        int16_t sample_a;
        int16_t sample_b;
        int32_t value;
        double frame_gain_a = gain_a;
        double frame_gain_b = gain_b;
        size_t audio_frame = frame / WB_AUDIO_CHANNELS;
        if (hard_handoff_tick) {
            bool before_switch = audio_frame < hard_switch_frame;
            char from_deck = output->hard_handoff_from_deck;
            char to_deck = output->hard_handoff_to_deck;
            frame_gain_a = before_switch
                ? (from_deck == 'A' ? 1.0 : 0.0)
                : (to_deck == 'A' ? 1.0 : 0.0);
            frame_gain_b = before_switch
                ? (from_deck == 'B' ? 1.0 : 0.0)
                : (to_deck == 'B' ? 1.0 : 0.0);
        } else if (transition_active) {
            double offset_ms = ((double)audio_frame * 1000.0) / (double)WB_AUDIO_SAMPLE_RATE;
            double old_gain = wb_fade_out_gain_at(
                (double)transition_elapsed_ms + offset_ms,
                output->transition_fade_out_ms
            );
            double new_gain = output->transition_entry_start_monotonic_ms > 0
                ? wb_entry_gain_at(
                    (double)transition_entry_elapsed_ms + offset_ms,
                    output->transition_entry_ramp_ms
                )
                : 0.0;
            if (output->transition_from_deck == 'A') frame_gain_a = old_gain;
            if (output->transition_from_deck == 'B') frame_gain_b = old_gain;
            if (output->transition_to_deck == 'A') frame_gain_a = new_gain;
            if (output->transition_to_deck == 'B') frame_gain_b = new_gain;
        }
        memcpy(&sample_a, deck_a + frame * sizeof(int16_t), sizeof(sample_a));
        memcpy(&sample_b, deck_b + frame * sizeof(int16_t), sizeof(sample_b));
        value = (int32_t)((double)sample_a * frame_gain_a + (double)sample_b * frame_gain_b);
        if (value > 32767) value = 32767;
        if (value < -32768) value = -32768;
        {
            int16_t final_sample = (int16_t)value;
            memcpy(mixed + frame * sizeof(int16_t), &final_sample, sizeof(final_sample));
        }
    }
    output->mixed_frames += WB_OUTPUT_TICK_FRAMES;
    if (!audible_pcm_present) {
        output->silence_frames += WB_OUTPUT_TICK_FRAMES;
    }
    return sizeof(deck_a);
}

static int reconnect_delay_seconds(size_t index) {
    static const int delays[] = {1, 2, 5, 10, 15};
    size_t count = sizeof(delays) / sizeof(delays[0]);
    if (index >= count) index = count - 1U;
    return delays[index];
}

static int reconnect_delay_for_error(size_t index, const char *error) {
    static const int mount_busy_delays[] = {5, 10, 15, 30, 30};
    size_t count = sizeof(mount_busy_delays) / sizeof(mount_busy_delays[0]);
    bool mount_busy = error != NULL && (
        strstr(error, "403 Forbidden") != NULL
        || strstr(error, "HTTP/1.0 403") != NULL
        || strstr(error, "HTTP/1.1 403") != NULL
        || strstr(error, "409 Conflict") != NULL
    );
    if (!mount_busy) return reconnect_delay_seconds(index);
    if (index >= count) index = count - 1U;
    return mount_busy_delays[index];
}

static void emit_stream_event(
    WbIcecastOutput *output,
    const WbNativeStreamOutput *stream,
    const char *event,
    const char *detail
) {
    WbDeckState empty = {0};
    char escaped_id[WB_NATIVE_OUTPUT_ID_SIZE * 2U];
    char escaped_codec[WB_NATIVE_OUTPUT_CODEC_SIZE * 2U];
    char escaped_mount[WB_ICECAST_MOUNT_SIZE * 2U];
    char escaped_detail[WB_ICECAST_ERROR_SIZE * 2U];
    char payload[WB_ICECAST_MOUNT_SIZE * 2U + WB_ICECAST_ERROR_SIZE * 2U + 512U];
    wb_json_escape(stream == NULL ? "" : stream->output_id, escaped_id, sizeof(escaped_id));
    wb_json_escape(stream == NULL ? "" : stream->codec, escaped_codec, sizeof(escaped_codec));
    wb_json_escape(stream == NULL ? "" : stream->mount, escaped_mount, sizeof(escaped_mount));
    wb_json_escape(detail == NULL ? "" : detail, escaped_detail, sizeof(escaped_detail));
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_icecast_output\":true,\"multi_output\":true,"
        "\"output_id\":\"%s\",\"codec\":\"%s\",\"mount\":\"%s\","
        "\"detail\":\"%s\"}",
        escaped_id,
        escaped_codec,
        escaped_mount,
        escaped_detail
    );
    (void)wb_engine_send_event(output->owner, event, &empty, '-', payload);
}

static bool output_should_run(WbIcecastOutput *output) {
    bool result;
    (void)pthread_mutex_lock(&output->lock);
    result = !output->shutdown && output->engine_running && enabled_stream_count_locked(output) > 0U;
    (void)pthread_mutex_unlock(&output->lock);
    return result;
}

static bool wait_pipeline_retry(WbIcecastOutput *output, int milliseconds) {
    struct timespec deadline;
    bool interrupted;
    (void)clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += milliseconds / 1000;
    deadline.tv_nsec += (long)(milliseconds % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec += 1;
        deadline.tv_nsec -= 1000000000L;
    }
    (void)pthread_mutex_lock(&output->lock);
    if (!output->shutdown && output->engine_running
        && enabled_stream_count_locked(output) > 0U && !output->restart_requested) {
        (void)pthread_cond_timedwait(&output->cond, &output->lock, &deadline);
    }
    interrupted = output->shutdown || !output->engine_running
        || enabled_stream_count_locked(output) == 0U || output->restart_requested;
    (void)pthread_mutex_unlock(&output->lock);
    return interrupted;
}

static void stream_disconnect_locked(
    WbNativeStreamOutput *stream,
    const char *error,
    bool send_error,
    bool schedule_retry
) {
    bool was_connected = stream->connected;
    if (was_connected && schedule_retry && stream->output_gap_started_monotonic_ms <= 0) {
        stream->output_gap_started_monotonic_ms = stream->last_successful_send_monotonic_ms > 0
            ? stream->last_successful_send_monotonic_ms : monotonic_ms();
    }
    close_fd(&stream->icecast_fd);
    stream->connected = false;
    reset_encoded_fifo_locked(stream);
    if (was_connected) stream->disconnect_count += 1U;
    if (send_error) {
        stream->send_error_count += 1U;
        stream->consecutive_send_errors += 1U;
    }
    copy_text(stream->error, sizeof(stream->error), error == NULL ? "" : error);
    if (stream->enabled && schedule_retry) {
        size_t backoff_index = stream->consecutive_send_errors > 0U
            ? (size_t)(stream->consecutive_send_errors - 1U) : 0U;
        int delay = reconnect_delay_seconds(backoff_index);
        stream->reconnect_backoff_seconds = delay;
        stream->next_reconnect_monotonic_ms = monotonic_ms() + (int64_t)delay * 1000LL;
        copy_text(stream->status, sizeof(stream->status), "reconnecting");
    } else {
        stream->reconnect_backoff_seconds = 0;
        stream->next_reconnect_monotonic_ms = 0;
        copy_text(stream->status, sizeof(stream->status), stream->enabled ? "configured" : "disabled");
    }
}

static void *stream_worker_main(void *context) {
    WbNativeStreamOutput *stream = context;
    WbIcecastOutput *output = &stream->owner->icecast_output;
    unsigned char chunk[WB_STREAM_WORKER_CHUNK];
    for (;;) {
        char host[WB_ICECAST_HOST_SIZE] = "";
        char mount[WB_ICECAST_MOUNT_SIZE] = "";
        char username[WB_ICECAST_USER_SIZE] = "";
        char password[WB_ICECAST_PASSWORD_SIZE] = "";
        char stream_name[WB_ICECAST_NAME_SIZE] = "";
        char stream_description[WB_ICECAST_DESCRIPTION_SIZE] = "";
        char stream_genre[WB_ICECAST_GENRE_SIZE] = "";
        char stream_url[WB_ICECAST_URL_SIZE] = "";
        char content_type[WB_NATIVE_OUTPUT_CONTENT_TYPE_SIZE] = "";
        char error[WB_ICECAST_ERROR_SIZE] = "";
        uint64_t config_generation = 0U;
        int port = 8000;
        bool public_stream = false;
        int bitrate_kbps = 0;
        int fd = -1;
        int64_t retry_due = 0;

        (void)pthread_mutex_lock(&output->lock);
        while (!output->shutdown && !stream->worker_shutdown
            && (!stream->configured || !stream->enabled || !output->engine_running
                || !stream->encoder_ready)) {
            if (stream->connected || stream->icecast_fd >= 0) {
                stream_disconnect_locked(stream, "", false, false);
            }
            mirror_default_stream_locked(output);
            (void)pthread_cond_wait(&output->cond, &output->lock);
        }
        if (output->shutdown || stream->worker_shutdown) {
            stream_disconnect_locked(stream, "", false, false);
            (void)pthread_mutex_unlock(&output->lock);
            break;
        }
        retry_due = stream->next_reconnect_monotonic_ms;
        if (retry_due > monotonic_ms()) {
            struct timespec deadline;
            int64_t remaining = retry_due - monotonic_ms();
            (void)clock_gettime(CLOCK_REALTIME, &deadline);
            deadline.tv_sec += remaining / 1000;
            deadline.tv_nsec += (long)(remaining % 1000) * 1000000L;
            if (deadline.tv_nsec >= 1000000000L) {
                deadline.tv_sec += 1;
                deadline.tv_nsec -= 1000000000L;
            }
            (void)pthread_cond_timedwait(&output->cond, &output->lock, &deadline);
            (void)pthread_mutex_unlock(&output->lock);
            continue;
        }
        copy_text(host, sizeof(host), stream->host);
        copy_text(mount, sizeof(mount), stream->mount);
        copy_text(username, sizeof(username), stream->username);
        copy_text(password, sizeof(password), stream->password);
        copy_text(stream_name, sizeof(stream_name), stream->stream_name);
        copy_text(stream_description, sizeof(stream_description), stream->stream_description);
        copy_text(stream_genre, sizeof(stream_genre), stream->stream_genre);
        copy_text(stream_url, sizeof(stream_url), stream->stream_url);
        copy_text(content_type, sizeof(content_type), stream->content_type);
        port = stream->port;
        bitrate_kbps = stream->bitrate_kbps;
        public_stream = stream->public_stream;
        config_generation = stream->config_generation;
        copy_text(stream->status, sizeof(stream->status), "connecting");
        stream->error[0] = '\0';
        mirror_default_stream_locked(output);
        (void)pthread_mutex_unlock(&output->lock);

        fd = connect_tcp(host, port, error, sizeof(error));
        if (fd >= 0 && icecast_handshake(
                fd, host, port, mount, username, password, stream_name,
                stream_description, stream_genre, stream_url,
                content_type, bitrate_kbps, public_stream, error, sizeof(error)
            ) != 0) {
            close(fd);
            fd = -1;
        }
        memset(password, 0, sizeof(password));

        (void)pthread_mutex_lock(&output->lock);
        if (output->shutdown || stream->worker_shutdown || !stream->enabled
            || !output->engine_running || !stream->encoder_ready
            || stream->config_generation != config_generation) {
            if (fd >= 0) close(fd);
            (void)pthread_mutex_unlock(&output->lock);
            continue;
        }
        if (fd < 0) {
            stream->consecutive_send_errors += 1U;
            copy_text(stream->error, sizeof(stream->error), error);
            copy_text(stream->status, sizeof(stream->status), "reconnecting");
            stream->reconnect_backoff_seconds = reconnect_delay_for_error(
                stream->consecutive_send_errors > 0U
                    ? (size_t)(stream->consecutive_send_errors - 1U) : 0U,
                error
            );
            stream->next_reconnect_monotonic_ms = monotonic_ms()
                + (int64_t)stream->reconnect_backoff_seconds * 1000LL;
            mirror_default_stream_locked(output);
            (void)pthread_mutex_unlock(&output->lock);
            emit_stream_event(output, stream, "native_icecast_connect_failed", error);
            continue;
        }

        set_nonblocking(fd);
        stream->icecast_fd = fd;
        stream->connected = true;
        stream->connect_count += 1U;
        if (stream->connect_count > 1U) stream->reconnect_count += 1U;
        stream->consecutive_send_errors = 0U;
        stream->reconnect_backoff_seconds = 0;
        stream->next_reconnect_monotonic_ms = 0;
        stream->error[0] = '\0';
        copy_text(stream->status, sizeof(stream->status), "streaming");
        if (stream->current_metadata[0] != '\0') stream->metadata_pending = true;
        mirror_default_stream_locked(output);
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        emit_stream_event(output, stream, "native_icecast_connected", "native stream connected");

        for (;;) {
            size_t bytes = 0U;
            int send_result;
            (void)pthread_mutex_lock(&output->lock);
            while (!output->shutdown && !stream->worker_shutdown && stream->enabled
                && output->engine_running && stream->connected && stream->encoded_fifo_fill == 0U) {
                (void)pthread_cond_wait(&output->cond, &output->lock);
            }
            if (output->shutdown || stream->worker_shutdown || !stream->enabled
                || !output->engine_running || !stream->connected) {
                if (stream->connected || stream->icecast_fd >= 0) {
                    stream_disconnect_locked(stream, "", false, stream->enabled && output->engine_running);
                }
                mirror_default_stream_locked(output);
                (void)pthread_mutex_unlock(&output->lock);
                break;
            }
            bytes = byte_fifo_read(
                stream->encoded_fifo,
                stream->encoded_fifo_capacity,
                &stream->encoded_fifo_read_pos,
                &stream->encoded_fifo_fill,
                chunk,
                sizeof(chunk)
            );
            fd = stream->icecast_fd;
            (void)pthread_mutex_unlock(&output->lock);
            if (bytes == 0U) continue;

            send_result = send_all_socket(fd, chunk, bytes);
            (void)pthread_mutex_lock(&output->lock);
            if (send_result != 0) {
                if (send_result == -2) stream->icecast_stall_count += 1U;
                (void)snprintf(
                    error,
                    sizeof(error),
                    "%s",
                    send_result == -2 ? "Icecast send watchdog timeout" : "Icecast send failed"
                );
                stream_disconnect_locked(stream, error, true, true);
                mirror_default_stream_locked(output);
                (void)pthread_cond_broadcast(&output->cond);
                (void)pthread_mutex_unlock(&output->lock);
                emit_stream_event(output, stream, "native_icecast_disconnected", error);
                break;
            }
            {
                int64_t sent_ms = monotonic_ms();
                int64_t gap_ms = 0;
                bool recovery_gap = stream->output_gap_started_monotonic_ms > 0;
                if (recovery_gap) gap_ms = sent_ms - stream->output_gap_started_monotonic_ms;
                else if (stream->last_successful_send_monotonic_ms > 0) {
                    gap_ms = sent_ms - stream->last_successful_send_monotonic_ms;
                }
                stream->icecast_sent_bytes_total += (uint64_t)bytes;
                stream->last_icecast_send_monotonic_ms = sent_ms;
                if (gap_ms > WB_OUTPUT_GAP_THRESHOLD_MS) {
                    stream->output_gap_count += 1U;
                    if (gap_ms > stream->max_output_gap_ms) stream->max_output_gap_ms = gap_ms;
                    copy_text(
                        stream->last_output_gap_reason,
                        sizeof(stream->last_output_gap_reason),
                        recovery_gap ? "reconnect_or_encoder_restart" : "stream_send_gap"
                    );
                }
                stream->output_gap_started_monotonic_ms = 0;
                stream->last_successful_send_monotonic_ms = sent_ms;
                stream->consecutive_send_errors = 0U;
            }
            mirror_default_stream_locked(output);
            (void)pthread_mutex_unlock(&output->lock);
        }
    }
    return NULL;
}

static void *icecast_metadata_thread_main(void *context) {
    WbIcecastOutput *output = context;
    for (;;) {
        size_t selected = WB_NATIVE_OUTPUT_MAX;
        char host[WB_ICECAST_HOST_SIZE] = "";
        char mount[WB_ICECAST_MOUNT_SIZE] = "";
        char username[WB_ICECAST_USER_SIZE] = "";
        char password[WB_ICECAST_PASSWORD_SIZE] = "";
        char metadata[WB_ICECAST_METADATA_SIZE] = "";
        char slot_token[WB_SLOT_TOKEN_SIZE] = "";
        char output_id[WB_NATIVE_OUTPUT_ID_SIZE] = "";
        char codec[WB_NATIVE_OUTPUT_CODEC_SIZE] = "";
        char error[WB_ICECAST_ERROR_SIZE] = "";
        int port = 8000;
        int64_t queue_id = 0;
        uint64_t generation = 0U;
        uint64_t config_generation = 0U;
        int result;
        size_t index;

        (void)pthread_mutex_lock(&output->lock);
        for (;;) {
            int64_t now = monotonic_ms();
            int64_t nearest_due = 0;
            if (output->shutdown) break;
            selected = WB_NATIVE_OUTPUT_MAX;
            for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
                WbNativeStreamOutput *stream = &output->streams[index];
                if (!stream->configured || !stream->enabled || !stream->connected
                    || !stream->metadata_pending || stream->current_metadata[0] == '\0'
                    || (output->dsp_enabled && !output->dsp_ready)) continue;
                if (stream->metadata_not_before_monotonic_ms <= now) {
                    selected = index;
                    break;
                }
                if (nearest_due == 0 || stream->metadata_not_before_monotonic_ms < nearest_due) {
                    nearest_due = stream->metadata_not_before_monotonic_ms;
                }
            }
            if (selected < WB_NATIVE_OUTPUT_MAX || output->shutdown) break;
            if (nearest_due > now) {
                struct timespec deadline;
                int64_t wait_ms = nearest_due - now;
                if (wait_ms > 250) wait_ms = 250;
                (void)clock_gettime(CLOCK_REALTIME, &deadline);
                deadline.tv_sec += wait_ms / 1000;
                deadline.tv_nsec += (long)(wait_ms % 1000) * 1000000L;
                if (deadline.tv_nsec >= 1000000000L) {
                    deadline.tv_sec += 1;
                    deadline.tv_nsec -= 1000000000L;
                }
                (void)pthread_cond_timedwait(&output->cond, &output->lock, &deadline);
            } else {
                (void)pthread_cond_wait(&output->cond, &output->lock);
            }
        }
        if (output->shutdown) {
            (void)pthread_mutex_unlock(&output->lock);
            break;
        }
        {
            WbNativeStreamOutput *stream = &output->streams[selected];
            copy_text(host, sizeof(host), stream->host);
            copy_text(mount, sizeof(mount), stream->mount);
            copy_text(username, sizeof(username), stream->username);
            copy_text(password, sizeof(password), stream->password);
            copy_text(metadata, sizeof(metadata), stream->current_metadata);
            copy_text(slot_token, sizeof(slot_token), stream->current_metadata_slot_token);
            copy_text(output_id, sizeof(output_id), stream->output_id);
            copy_text(codec, sizeof(codec), stream->codec);
            port = stream->port;
            queue_id = stream->current_metadata_queue_id;
            generation = stream->metadata_generation;
            config_generation = stream->config_generation;
        }
        (void)pthread_mutex_unlock(&output->lock);

        result = icecast_send_metadata(host, port, mount, username, password, metadata, error, sizeof(error));
        memset(password, 0, sizeof(password));
        (void)pthread_mutex_lock(&output->lock);
        {
            WbNativeStreamOutput *stream = &output->streams[selected];
            bool current = stream->configured && stream->config_generation == config_generation
                && stream->metadata_generation == generation;
            if (current) {
                if (result == 0) {
                    stream->metadata_applied_generation = generation;
                    stream->metadata_pending = false;
                    stream->metadata_applied_count += 1U;
                    stream->metadata_error[0] = '\0';
                } else {
                    stream->metadata_failed_count += 1U;
                    copy_text(stream->metadata_error, sizeof(stream->metadata_error), error);
                    stream->metadata_not_before_monotonic_ms = monotonic_ms() + WB_OUTPUT_METADATA_RETRY_MS;
                }
                mirror_default_stream_locked(output);
            }
        }
        (void)pthread_mutex_unlock(&output->lock);
        if (result == 0) {
            emit_metadata_event(
                output->owner, "native_icecast_metadata_applied", false, true, false,
                output_id, codec, metadata, queue_id, slot_token, "metadata applied"
            );
        } else {
            emit_metadata_event(
                output->owner, "native_icecast_metadata_failed", false, false, true,
                output_id, codec, metadata, queue_id, slot_token, error
            );
        }
    }
    return NULL;
}

typedef enum {
    WB_OUTPUT_FAILURE_NONE = 0,
    WB_OUTPUT_FAILURE_ENCODER = 1,
    WB_OUTPUT_FAILURE_DSP = 2
} WbOutputFailure;

static bool enqueue_stream_encoded_locked(
    WbNativeStreamOutput *stream,
    const unsigned char *data,
    size_t bytes
) {
    size_t accepted;
    if (!stream->connected) {
        stream->discarded_encoded_bytes_total += (uint64_t)bytes;
        return true;
    }
    accepted = byte_fifo_write(
        stream->encoded_fifo,
        stream->encoded_fifo_capacity,
        &stream->encoded_fifo_write_pos,
        &stream->encoded_fifo_fill,
        data,
        bytes
    );
    if (accepted < bytes) {
        stream->encoded_fifo_overrun_count += 1U;
        stream->encoded_fifo_overrun_bytes += (uint64_t)(bytes - accepted);
        stream_disconnect_locked(stream, "encoded network FIFO overrun", true, true);
        stream->discarded_encoded_bytes_total += (uint64_t)bytes;
        return false;
    }
    if (stream->encoded_fifo_fill > stream->encoded_fifo_high_water) {
        stream->encoded_fifo_high_water = stream->encoded_fifo_fill;
    }
    return true;
}

static void libav_encoded_sink(
    void *context,
    size_t stream_index,
    const unsigned char *data,
    size_t length
) {
    WbIcecastOutput *output = context;
    int64_t encoded_ms;
    if (output == NULL || data == NULL || length == 0U || stream_index >= WB_NATIVE_OUTPUT_MAX) return;
    encoded_ms = monotonic_ms();
    (void)pthread_mutex_lock(&output->lock);
    {
        WbNativeStreamOutput *stream = &output->streams[stream_index];
        if (!stream->configured || !stream->enabled) {
            (void)pthread_mutex_unlock(&output->lock);
            return;
        }
        stream->last_encoded_data_monotonic_ms = encoded_ms;
        stream->encoded_bytes_total += (uint64_t)length;
        stream->encoder_ready = true;
        output->last_encoded_data_monotonic_ms = encoded_ms;
        (void)enqueue_stream_encoded_locked(stream, data, length);
        mirror_default_stream_locked(output);
        (void)pthread_cond_broadcast(&output->cond);
    }
    (void)pthread_mutex_unlock(&output->lock);
}

static void commit_hard_handoff_metadata(
    WbEngineState *state, char deck, const WbDeckState *track
) {
    WbIcecastOutput *output = &state->icecast_output;
    typedef struct {
        bool valid;
        char output_id[WB_NATIVE_OUTPUT_ID_SIZE];
        char codec[WB_NATIVE_OUTPUT_CODEC_SIZE];
        char metadata[WB_ICECAST_METADATA_SIZE];
    } MetadataRequest;
    MetadataRequest requests[WB_NATIVE_OUTPUT_MAX];
    size_t index;
    int64_t now_ms;
    if (track == NULL) return;
    memset(requests, 0, sizeof(requests));
    now_ms = monotonic_ms();
    (void)pthread_mutex_lock(&output->lock);
    clear_hard_handoff_locked(output);
    output->primary_deck = deck == 'B' ? 'B' : 'A';
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        char metadata[WB_ICECAST_METADATA_SIZE] = "";
        if (!stream->configured || !stream->enabled) continue;
        format_track_metadata(track, stream->add_year_to_metadata, metadata, sizeof(metadata));
        copy_text(stream->current_metadata, sizeof(stream->current_metadata), metadata);
        copy_text(
            stream->current_metadata_slot_token,
            sizeof(stream->current_metadata_slot_token),
            track->slot_token
        );
        stream->current_metadata_queue_id = track->queue_id;
        stream->metadata_generation += 1U;
        stream->metadata_requested_count += 1U;
        stream->metadata_pending = metadata[0] != '\0';
        stream->metadata_not_before_monotonic_ms = output->dsp_enabled && stream->metadata_pending
            ? now_ms + WB_OUTPUT_DSP_METADATA_DELAY_MS : 0;
        stream->metadata_error[0] = '\0';
        requests[index].valid = true;
        copy_text(requests[index].output_id, sizeof(requests[index].output_id), stream->output_id);
        copy_text(requests[index].codec, sizeof(requests[index].codec), stream->codec);
        copy_text(requests[index].metadata, sizeof(requests[index].metadata), metadata);
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        if (!requests[index].valid) continue;
        emit_metadata_event(
            state, "native_icecast_metadata_requested", true, false, false,
            requests[index].output_id, requests[index].codec, requests[index].metadata,
            track->queue_id, track->slot_token, "hard_handoff_boundary"
        );
    }
}

static void finalize_hard_handoff(
    WbEngineState *state, const WbHardHandoffSnapshot *handoff
) {
    char started_payload[512];
    char ended_payload[384];
    int64_t timing_error_ms;
    if (state == NULL || handoff == NULL || !handoff->occurred) return;

    (void)pthread_mutex_lock(&state->lock);
    state->active_deck = handoff->to_deck == 'B' ? 'B' : 'A';
    state->transitioning = false;
    {
        WbDeckState *to_live = handoff->to_deck == 'B' ? &state->deck_b : &state->deck_a;
        WbDeckState *from_live = handoff->from_deck == 'B' ? &state->deck_b : &state->deck_a;
        if (to_live->loaded && strcmp(to_live->slot_token, handoff->to_track.slot_token) == 0) {
            to_live->playback_started = true;
            to_live->consumed = false;
            to_live->terminal = false;
        }
        if (from_live->loaded && strcmp(from_live->slot_token, handoff->from_track.slot_token) == 0) {
            from_live->consumed = true;
            from_live->terminal = true;
        }
    }
    (void)pthread_mutex_unlock(&state->lock);
    wb_native_timing_wake(state);

    commit_hard_handoff_metadata(state, handoff->to_deck, &handoff->to_track);
    timing_error_ms = handoff->actual_monotonic_ms - handoff->scheduled_monotonic_ms;
    (void)snprintf(
        started_payload,
        sizeof(started_payload),
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"source\":\"native_hard_handoff_boundary\",\"hard_handoff\":true,"
        "\"from_deck\":\"%c\",\"scheduled_monotonic_ms\":%lld,"
        "\"actual_monotonic_ms\":%lld,\"timing_error_ms\":%lld,"
        "\"switch_frame\":%zu,\"early_fifo\":%s,"
        "\"waited_for_outgoing_drain\":%s}",
        handoff->from_deck,
        (long long)handoff->scheduled_monotonic_ms,
        (long long)handoff->actual_monotonic_ms,
        (long long)timing_error_ms,
        handoff->switch_frame,
        handoff->early_fifo ? "true" : "false",
        handoff->waited_for_outgoing_drain ? "true" : "false"
    );
    (void)wb_engine_send_event(
        state, "track_started", &handoff->to_track, handoff->to_deck, started_payload
    );

    wb_audio_probe_stop_track(
        state,
        handoff->from_deck,
        handoff->from_track.queue_id,
        handoff->from_track.slot_token,
        "hard_handoff_complete"
    );
    wb_icecast_output_stop_track(
        state,
        handoff->from_deck,
        handoff->from_track.queue_id,
        handoff->from_track.slot_token
    );
    (void)snprintf(
        ended_payload,
        sizeof(ended_payload),
        "{\"control_only\":false,\"audio_enabled\":true,"
        "\"source\":\"native_hard_handoff_release\",\"to_deck\":\"%c\","
        "\"actual_monotonic_ms\":%lld}",
        handoff->to_deck,
        (long long)handoff->actual_monotonic_ms
    );
    (void)wb_engine_send_event(
        state, "track_ended", &handoff->from_track, handoff->from_deck, ended_payload
    );
}

static void *icecast_output_thread_main(void *context) {
    WbIcecastOutput *output = context;
    size_t backoff_index = 0U;
    for (;;) {
        char error[WB_ICECAST_ERROR_SIZE] = "";
        int64_t next_tick;
        WbOutputFailure failure = WB_OUTPUT_FAILURE_NONE;
        bool configured_restart = false;
        int delay_seconds;
        size_t index;

        (void)pthread_mutex_lock(&output->lock);
        while (!output->shutdown && (!output->engine_running || enabled_stream_count_locked(output) == 0U)) {
            output->encoder_running = false;
            mirror_default_stream_locked(output);
            (void)pthread_cond_wait(&output->cond, &output->lock);
        }
        if (output->shutdown) {
            (void)pthread_mutex_unlock(&output->lock);
            break;
        }
        output->restart_requested = false;
        reset_all_fifos_locked(output);
        (void)pthread_mutex_unlock(&output->lock);

        if (start_encoder(output, error, sizeof(error)) != 0) {
            failure = WB_OUTPUT_FAILURE_ENCODER;
        } else {
            int64_t started_ms = monotonic_ms();
            bool dsp_started;
            (void)pthread_mutex_lock(&output->lock);
            output->encoder_running = true;
            output->encoder_started_monotonic_ms = started_ms;
            output->last_encoded_data_monotonic_ms = started_ms;
            dsp_started = output->dsp_enabled && output->dsp_running;
            for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
                WbNativeStreamOutput *stream = &output->streams[index];
                if (!stream->configured || !stream->enabled) continue;
                stream->last_encoded_data_monotonic_ms = started_ms;
                if (stream->connected) {
                    stream->output_gap_started_monotonic_ms = stream->last_successful_send_monotonic_ms > 0
                        ? stream->last_successful_send_monotonic_ms : started_ms;
                }
            }
            mirror_default_stream_locked(output);
            (void)pthread_cond_broadcast(&output->cond);
            (void)pthread_mutex_unlock(&output->lock);
            emit_output_event(output->owner, "native_encoder_pipeline_started", "shared multi-output encoder started");
            if (dsp_started) emit_output_event(output->owner, "native_dsp_starting", "native SoundSolution pipeline starting");
            next_tick = monotonic_ms();

            while (output_should_run(output)) {
                _Alignas(int16_t) unsigned char mixed[WB_OUTPUT_TICK_BYTES];
                int64_t now = monotonic_ms();
                bool restart;

                if (now >= next_tick) {
                    WbOutputUnderrunSnapshot underrun = {0};
                    WbHardHandoffSnapshot hard_handoff = {0};
                    int64_t tick_lateness_ms = now > next_tick ? now - next_tick : 0;
                    bool dsp_enabled_now;
                    bool dsp_ready_now;
                    bool dsp_live_bypass_now;
                    (void)pthread_mutex_lock(&output->lock);
                    (void)mix_tick_locked(
                        output, mixed, now, tick_lateness_ms, &underrun, &hard_handoff
                    );
                    restart = output->restart_requested;
                    dsp_enabled_now = output->dsp_enabled;
                    dsp_ready_now = output->dsp_ready;
                    dsp_live_bypass_now = output->dsp_live_bypass_until_ready;
                    if (dsp_enabled_now && !dsp_ready_now && !dsp_live_bypass_now) {
                        memset(mixed, 0, sizeof(mixed));
                        output->dsp_startup_silence_frames += WB_OUTPUT_TICK_FRAMES;
                    }
                    (void)pthread_mutex_unlock(&output->lock);
                    if (hard_handoff.occurred) {
                        finalize_hard_handoff(output->owner, &hard_handoff);
                    }
                    if (underrun.occurred) emit_output_underrun_event(output->owner, &underrun);
                    if (restart) {
                        configured_restart = true;
                        break;
                    }
                    if (queue_encoder_pcm(output, mixed, sizeof(mixed)) != 0) {
                        (void)pthread_mutex_lock(&output->lock);
                        if (dsp_enabled_now) {
                            copy_text(
                                output->dsp_error, sizeof(output->dsp_error),
                                "DSP input FIFO saturated after sustained backpressure"
                            );
                        }
                        (void)pthread_mutex_unlock(&output->lock);
                        copy_text(
                            error, sizeof(error),
                            dsp_enabled_now
                                ? "DSP input FIFO saturated after sustained backpressure"
                                : "encoder input FIFO saturated after sustained backpressure"
                        );
                        failure = dsp_enabled_now ? WB_OUTPUT_FAILURE_DSP : WB_OUTPUT_FAILURE_ENCODER;
                        break;
                    }
                    next_tick += WB_OUTPUT_TICK_MS;
                    if (next_tick < now - 200) next_tick = now + WB_OUTPUT_TICK_MS;
                }

                {
                    WbLibavEncoderGroup *group;
                    bool dsp_enabled_now;
                    bool dsp_failed_now;
                    (void)pthread_mutex_lock(&output->lock);
                    group = (WbLibavEncoderGroup *)output->encoder_context;
                    dsp_enabled_now = output->dsp_enabled;
                    dsp_failed_now = output->dsp_output_failed;
                    (void)pthread_mutex_unlock(&output->lock);
                    if (group == NULL || wb_libav_encoder_group_failed(group)) {
                        if (group != NULL) wb_libav_encoder_group_error(group, error, sizeof(error));
                        if (error[0] == '\0') copy_text(error, sizeof(error), "embedded libav encoder failed");
                        failure = dsp_enabled_now && dsp_failed_now
                            ? WB_OUTPUT_FAILURE_DSP : WB_OUTPUT_FAILURE_ENCODER;
                        break;
                    }
                }

                now = monotonic_ms();
                (void)pthread_mutex_lock(&output->lock);
                {
                    bool branch_stalled = false;
                    bool dsp_failed = output->dsp_enabled && output->dsp_output_failed;
                    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
                        WbNativeStreamOutput *stream = &output->streams[index];
                        if (!stream->configured || !stream->enabled) continue;
                        if (now - stream->last_encoded_data_monotonic_ms > WB_OUTPUT_ENCODER_STALL_TIMEOUT_MS) {
                            branch_stalled = true;
                            break;
                        }
                    }
                    if (output->encoder_running && dsp_failed) {
                        copy_text(
                            error,
                            sizeof(error),
                            output->dsp_error[0] != '\0'
                                ? output->dsp_error : "in-process SoundSolution DSP failed"
                        );
                        failure = WB_OUTPUT_FAILURE_DSP;
                    } else if (output->encoder_running && branch_stalled) {
                        output->encoder_stall_count += 1U;
                        copy_text(error, sizeof(error), "shared encoder produced no data within watchdog interval");
                        failure = WB_OUTPUT_FAILURE_ENCODER;
                    }
                    restart = output->restart_requested;
                }
                (void)pthread_mutex_unlock(&output->lock);
                if (restart) {
                    configured_restart = true;
                    break;
                }
                if (failure != WB_OUTPUT_FAILURE_NONE) break;
                sleep_ms(2);
            }
        }

        if (failure != WB_OUTPUT_FAILURE_NONE) {
            (void)pthread_mutex_lock(&output->lock);
            if (failure == WB_OUTPUT_FAILURE_DSP) {
                output->dsp_restart_count += 1U;
                output->dsp_running = false;
                output->dsp_ready = false;
                copy_text(output->dsp_status, sizeof(output->dsp_status), "failed");
                copy_text(output->dsp_error, sizeof(output->dsp_error), error);
            } else {
                output->encoder_restart_count += 1U;
            }
            output->pipeline_restart_count += 1U;
            if (output->dsp_enabled) output->dsp_process_replacement_count += 1U;
            (void)pthread_mutex_unlock(&output->lock);
            emit_output_event(
                output->owner,
                failure == WB_OUTPUT_FAILURE_DSP ? "native_dsp_failed" : "native_icecast_encoder_failed",
                error
            );
        }
        stop_pipeline(output, failure != WB_OUTPUT_FAILURE_NONE || configured_restart);
        if (failure != WB_OUTPUT_FAILURE_NONE && output_should_run(output)) {
            delay_seconds = reconnect_delay_seconds(backoff_index);
            if (!wait_pipeline_retry(output, delay_seconds * 1000) && backoff_index < 4U) backoff_index += 1U;
        } else {
            backoff_index = 0U;
        }
    }
    stop_pipeline(output, false);
    return NULL;
}


int wb_icecast_output_init(WbEngineState *state) {
    WbIcecastOutput *output = &state->icecast_output;
    size_t capacity = ((size_t)WB_OUTPUT_FIFO_MS * WB_AUDIO_SAMPLE_RATE / 1000U) * WB_AUDIO_FRAME_BYTES;
    size_t index;
    memset(output, 0, sizeof(*output));
    output->owner = state;
    output->encoder_stdin_fd = -1;
    output->encoder_stdout_fd = -1;
    output->dsp_output_fd = -1;
    output->icecast_fd = -1;
    output->port = 8000;
    output->bitrate_kbps = 192;
    output->fifo_capacity = capacity;
    output->deck_a_fifo = calloc(1U, capacity);
    output->deck_b_fifo = calloc(1U, capacity);
    if (output->deck_a_fifo == NULL || output->deck_b_fifo == NULL) {
        free(output->deck_a_fifo);
        free(output->deck_b_fifo);
        output->deck_a_fifo = NULL;
        output->deck_b_fifo = NULL;
        return -1;
    }
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        stream->owner = state;
        stream->slot_index = index;
        stream->encoder_stdout_fd = -1;
        stream->icecast_fd = -1;
        stream->encoded_fifo_capacity = WB_STREAM_ENCODED_FIFO_BYTES;
        stream->encoded_fifo = calloc(1U, stream->encoded_fifo_capacity);
        if (stream->encoded_fifo == NULL) {
            size_t cleanup_index;
            for (cleanup_index = 0U; cleanup_index < index; cleanup_index += 1U) {
                free(output->streams[cleanup_index].encoded_fifo);
                output->streams[cleanup_index].encoded_fifo = NULL;
            }
            free(output->deck_a_fifo);
            free(output->deck_b_fifo);
            output->deck_a_fifo = NULL;
            output->deck_b_fifo = NULL;
            return -1;
        }
    }
    (void)pthread_mutex_init(&output->lock, NULL);
    (void)pthread_mutex_init(&output->encoder_control_lock, NULL);
    (void)pthread_mutex_init(&output->pcm_route_lock, NULL);
    (void)pthread_cond_init(&output->cond, NULL);
    copy_text(output->username, sizeof(output->username), "source");
    output->mount[0] = '\0';
    output->stream_name[0] = '\0';
    copy_text(output->status, sizeof(output->status), "disabled");
    copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");

    if (pthread_create(&output->thread, NULL, icecast_output_thread_main, output) != 0) {
        for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) free(output->streams[index].encoded_fifo);
        (void)pthread_cond_destroy(&output->cond);
        (void)pthread_mutex_destroy(&output->pcm_route_lock);
        (void)pthread_mutex_destroy(&output->encoder_control_lock);
        (void)pthread_mutex_destroy(&output->lock);
        free(output->deck_a_fifo);
        free(output->deck_b_fifo);
        output->deck_a_fifo = NULL;
        output->deck_b_fifo = NULL;
        return -1;
    }
    output->thread_created = true;
    if (pthread_create(&output->metadata_thread, NULL, icecast_metadata_thread_main, output) != 0) {
        (void)pthread_mutex_lock(&output->lock);
        output->shutdown = true;
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_join(output->thread, NULL);
        output->thread_created = false;
        for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) free(output->streams[index].encoded_fifo);
        (void)pthread_cond_destroy(&output->cond);
        (void)pthread_mutex_destroy(&output->pcm_route_lock);
        (void)pthread_mutex_destroy(&output->encoder_control_lock);
        (void)pthread_mutex_destroy(&output->lock);
        free(output->deck_a_fifo);
        free(output->deck_b_fifo);
        output->deck_a_fifo = NULL;
        output->deck_b_fifo = NULL;
        return -1;
    }
    output->metadata_thread_created = true;
    return 0;
}

void wb_icecast_output_destroy(WbEngineState *state) {
    WbIcecastOutput *output = &state->icecast_output;
    size_t index;
    (void)pthread_mutex_lock(&output->lock);
    output->shutdown = true;
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        output->streams[index].worker_shutdown = true;
    }
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    if (output->thread_created) {
        (void)pthread_join(output->thread, NULL);
        output->thread_created = false;
    }
    if (output->metadata_thread_created) {
        (void)pthread_join(output->metadata_thread, NULL);
        output->metadata_thread_created = false;
    }
    stop_dsp_context_unlocked(output);
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        if (stream->thread_created) {
            (void)pthread_join(stream->thread, NULL);
            stream->thread_created = false;
        }
        memset(stream->password, 0, sizeof(stream->password));
        free(stream->encoded_fifo);
        stream->encoded_fifo = NULL;
    }
    memset(output->password, 0, sizeof(output->password));
    free(output->deck_a_fifo);
    free(output->deck_b_fifo);
    output->deck_a_fifo = NULL;
    output->deck_b_fifo = NULL;
    (void)pthread_cond_destroy(&output->cond);
    (void)pthread_mutex_destroy(&output->pcm_route_lock);
    (void)pthread_mutex_destroy(&output->encoder_control_lock);
    (void)pthread_mutex_destroy(&output->lock);
}


void wb_icecast_output_set_engine_running(WbEngineState *state, bool running) {
    WbIcecastOutput *output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    output->engine_running = running;
    output->paused = false;
    if (!running) {
        output->transitioning = false;
        output->transition_from_deck = '\0';
        output->transition_to_deck = '\0';
        output->transition_start_monotonic_ms = 0;
        output->transition_entry_start_monotonic_ms = 0;
        output->transition_entry_requested_monotonic_ms = 0;
        output->transition_entry_pcm_start_monotonic_ms = 0;
        output->transition_entry_waiting_for_pcm = false;
        output->transition_duration_ms = 0;
        output->transition_fade_out_ms = 0;
        output->transition_entry_ramp_ms = 0;
        output->transition_silence_hold_ms = 0;
        clear_hard_handoff_locked(output);
        output->primary_deck = '\0';
        output->deck_a_slot_token[0] = '\0';
        output->deck_b_slot_token[0] = '\0';
        output->deck_a_queue_id = 0;
        output->deck_b_queue_id = 0;
        output->deck_a_seek_pending = false;
        output->deck_b_seek_pending = false;
        output->deck_a_seek_slot_token[0] = '\0';
        output->deck_b_seek_slot_token[0] = '\0';
        output->metadata_pending = false;
        output->metadata_not_before_monotonic_ms = 0;
        output->metadata_generation += 1U;
        output->current_metadata[0] = '\0';
        output->current_metadata_slot_token[0] = '\0';
        output->current_metadata_queue_id = 0;
        output->metadata_error[0] = '\0';
        {
            size_t stream_index;
            for (stream_index = 0U; stream_index < WB_NATIVE_OUTPUT_MAX; stream_index += 1U) {
                WbNativeStreamOutput *stream = &output->streams[stream_index];
                stream->metadata_pending = false;
                stream->metadata_not_before_monotonic_ms = 0;
                stream->metadata_generation += 1U;
                stream->current_metadata[0] = '\0';
                stream->current_metadata_slot_token[0] = '\0';
                stream->current_metadata_queue_id = 0;
                stream->metadata_error[0] = '\0';
                reset_encoded_fifo_locked(stream);
            }
        }
        reset_all_fifos_locked(output);
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
}

void wb_icecast_output_set_paused(
    WbEngineState *state,
    bool paused,
    int64_t pause_duration_ms
) {
    WbIcecastOutput *output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    if (!paused && output->paused && pause_duration_ms > 0) {
        int64_t resumed_ms = monotonic_ms();
        int64_t pause_started_ms = resumed_ms - pause_duration_ms;
#define WB_SHIFT_OUTPUT_CLOCK(field) \
        do { \
            if ((field) > 0) { \
                (field) = (field) <= pause_started_ms \
                    ? (field) + pause_duration_ms \
                    : resumed_ms; \
            } \
        } while (0)
        WB_SHIFT_OUTPUT_CLOCK(output->transition_start_monotonic_ms);
        WB_SHIFT_OUTPUT_CLOCK(output->transition_entry_start_monotonic_ms);
        WB_SHIFT_OUTPUT_CLOCK(output->transition_entry_requested_monotonic_ms);
        WB_SHIFT_OUTPUT_CLOCK(output->transition_entry_pcm_start_monotonic_ms);
        WB_SHIFT_OUTPUT_CLOCK(output->hard_handoff_at_monotonic_ms);
        WB_SHIFT_OUTPUT_CLOCK(output->hard_handoff_requested_monotonic_ms);
#undef WB_SHIFT_OUTPUT_CLOCK
    }
    output->paused = paused;
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
}

void wb_icecast_output_activate_track(WbEngineState *state, char deck, const WbDeckState *track) {
    WbIcecastOutput *output = &state->icecast_output;
    typedef struct {
        bool valid;
        char output_id[WB_NATIVE_OUTPUT_ID_SIZE];
        char codec[WB_NATIVE_OUTPUT_CODEC_SIZE];
        char metadata[WB_ICECAST_METADATA_SIZE];
    } MetadataRequest;
    MetadataRequest requests[WB_NATIVE_OUTPUT_MAX];
    size_t index;
    int64_t now_ms;
    if (track == NULL) return;
    memset(requests, 0, sizeof(requests));
    now_ms = monotonic_ms();
    (void)pthread_mutex_lock(&output->lock);
    clear_hard_handoff_locked(output);
    if (deck == 'B') {
        copy_text(output->deck_b_slot_token, sizeof(output->deck_b_slot_token), track->slot_token);
        output->deck_b_queue_id = track->queue_id;
        output->deck_b_seek_pending = false;
        output->deck_b_seek_slot_token[0] = '\0';
        reset_fifo_locked(output, 'B');
    } else {
        copy_text(output->deck_a_slot_token, sizeof(output->deck_a_slot_token), track->slot_token);
        output->deck_a_queue_id = track->queue_id;
        output->deck_a_seek_pending = false;
        output->deck_a_seek_slot_token[0] = '\0';
        reset_fifo_locked(output, 'A');
    }
    if (output->transitioning && output->transition_to_deck == deck && output->transition_entry_ramp_ms > 0) {
        output->transition_entry_start_monotonic_ms = 0;
        output->transition_entry_pcm_start_monotonic_ms = 0;
        output->transition_entry_waiting_for_pcm = true;
    }
    output->primary_deck = deck == 'B' ? 'B' : 'A';
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        char metadata[WB_ICECAST_METADATA_SIZE] = "";
        if (!stream->configured || !stream->enabled) continue;
        format_track_metadata(track, stream->add_year_to_metadata, metadata, sizeof(metadata));
        copy_text(stream->current_metadata, sizeof(stream->current_metadata), metadata);
        copy_text(stream->current_metadata_slot_token, sizeof(stream->current_metadata_slot_token), track->slot_token);
        stream->current_metadata_queue_id = track->queue_id;
        stream->metadata_generation += 1U;
        stream->metadata_requested_count += 1U;
        stream->metadata_pending = metadata[0] != '\0';
        stream->metadata_not_before_monotonic_ms = output->dsp_enabled && stream->metadata_pending
            ? now_ms + WB_OUTPUT_DSP_METADATA_DELAY_MS : 0;
        stream->metadata_error[0] = '\0';
        requests[index].valid = true;
        copy_text(requests[index].output_id, sizeof(requests[index].output_id), stream->output_id);
        copy_text(requests[index].codec, sizeof(requests[index].codec), stream->codec);
        copy_text(requests[index].metadata, sizeof(requests[index].metadata), metadata);
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        if (!requests[index].valid) continue;
        emit_metadata_event(
            state, "native_icecast_metadata_requested", true, false, false,
            requests[index].output_id, requests[index].codec, requests[index].metadata,
            track->queue_id, track->slot_token, "track_started"
        );
    }
}


void wb_icecast_output_seek_track(
    WbEngineState *state,
    char deck,
    const char *slot_token,
    const unsigned char *bridge_pcm,
    size_t bridge_bytes
) {
    WbIcecastOutput *output = &state->icecast_output;
    size_t accepted = 0U;
    (void)pthread_mutex_lock(&output->lock);
    if (deck == 'B') {
        if (slot_token != NULL && slot_token[0] != '\0') {
            copy_text(output->deck_b_slot_token, sizeof(output->deck_b_slot_token), slot_token);
            copy_text(output->deck_b_seek_slot_token, sizeof(output->deck_b_seek_slot_token), slot_token);
        }
        output->deck_b_seek_pending = true;
        if (bridge_pcm != NULL && bridge_bytes > 0U) {
            accepted = fifo_write(
                output->deck_b_fifo, output->fifo_capacity,
                &output->deck_b_write_pos, &output->deck_b_fill,
                bridge_pcm, bridge_bytes
            );
            if (accepted > 0U) {
                output->deck_b_started = true;
                if (output->deck_b_fill > output->deck_b_fifo_high_water_bytes) {
                    output->deck_b_fifo_high_water_bytes = output->deck_b_fill;
                }
            }
        }
    } else {
        if (slot_token != NULL && slot_token[0] != '\0') {
            copy_text(output->deck_a_slot_token, sizeof(output->deck_a_slot_token), slot_token);
            copy_text(output->deck_a_seek_slot_token, sizeof(output->deck_a_seek_slot_token), slot_token);
        }
        output->deck_a_seek_pending = true;
        if (bridge_pcm != NULL && bridge_bytes > 0U) {
            accepted = fifo_write(
                output->deck_a_fifo, output->fifo_capacity,
                &output->deck_a_write_pos, &output->deck_a_fill,
                bridge_pcm, bridge_bytes
            );
            if (accepted > 0U) {
                output->deck_a_started = true;
                if (output->deck_a_fill > output->deck_a_fifo_high_water_bytes) {
                    output->deck_a_fifo_high_water_bytes = output->deck_a_fill;
                }
            }
        }
    }
    output->seek_bridge_count += 1U;
    output->seek_bridge_bytes += (uint64_t)accepted;
    if (accepted < bridge_bytes) {
        output->seek_bridge_drop_bytes += (uint64_t)(bridge_bytes - accepted);
    }
    if (!output->enabled || !output->engine_running) {
        reset_fifo_locked(output, deck);
        if (deck == 'B') {
            output->deck_b_seek_pending = false;
            output->deck_b_seek_slot_token[0] = '\0';
        } else {
            output->deck_a_seek_pending = false;
            output->deck_a_seek_slot_token[0] = '\0';
        }
        output->seek_flush_count += 1U;
    }
    /* Native seek force-completes any active handoff. Mirror that
       immediately, but keep already decoded old-position PCM audible until
       the replacement decoder supplies its first target-position frame. */
    output->transitioning = false;
    output->transition_from_deck = '\0';
    output->transition_to_deck = '\0';
    output->transition_start_monotonic_ms = 0;
    output->transition_entry_start_monotonic_ms = 0;
    output->transition_entry_requested_monotonic_ms = 0;
    output->transition_entry_pcm_start_monotonic_ms = 0;
    output->transition_entry_waiting_for_pcm = false;
    output->transition_duration_ms = 0;
    output->transition_fade_out_ms = 0;
    output->transition_entry_ramp_ms = 0;
    output->transition_silence_hold_ms = 0;
    clear_hard_handoff_locked(output);
    output->primary_deck = deck == 'B' ? 'B' : 'A';
    (void)pthread_mutex_unlock(&output->lock);
}

void wb_icecast_output_stop_track(WbEngineState *state, char deck, int64_t queue_id, const char *slot_token) {
    WbIcecastOutput *output = &state->icecast_output;
    char *active_token;
    (void)queue_id;
    (void)pthread_mutex_lock(&output->lock);
    active_token = deck == 'B' ? output->deck_b_slot_token : output->deck_a_slot_token;
    if (slot_token == NULL || slot_token[0] == '\0' || strcmp(active_token, slot_token) == 0) {
        active_token[0] = '\0';
        if (deck == 'B') {
            output->deck_b_queue_id = 0;
            output->deck_b_started = false;
            output->deck_b_seek_pending = false;
            output->deck_b_seek_slot_token[0] = '\0';
        } else {
            output->deck_a_queue_id = 0;
            output->deck_a_started = false;
            output->deck_a_seek_pending = false;
            output->deck_a_seek_slot_token[0] = '\0';
        }
        reset_fifo_locked(output, deck);
    }
    (void)pthread_mutex_unlock(&output->lock);
}

bool wb_icecast_output_get_deck_buffered_ms(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t *buffered_ms
) {
    WbIcecastOutput *output;
    const char *token;
    int64_t queue_id;
    size_t fill;
    bool matched;
    if (buffered_ms != NULL) *buffered_ms = 0;
    if (state == NULL || track == NULL || buffered_ms == NULL) return false;
    output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    token = deck == 'B' ? output->deck_b_slot_token : output->deck_a_slot_token;
    queue_id = deck == 'B' ? output->deck_b_queue_id : output->deck_a_queue_id;
    fill = deck == 'B' ? output->deck_b_fill : output->deck_a_fill;
    matched = track->slot_token[0] != '\0'
        && strcmp(token, track->slot_token) == 0
        && queue_id == track->queue_id;
    if (matched) {
        uint64_t frames = fill / WB_AUDIO_FRAME_BYTES;
        *buffered_ms = (int64_t)((frames * 1000ULL) / WB_AUDIO_SAMPLE_RATE);
    }
    (void)pthread_mutex_unlock(&output->lock);
    return matched;
}

int wb_icecast_output_schedule_hard_handoff(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    const WbDeckState *from_track,
    const WbDeckState *to_track,
    int64_t handoff_at_monotonic_ms,
    const unsigned char *primed_pcm,
    size_t primed_bytes,
    char *error,
    size_t error_size
) {
    WbIcecastOutput *output;
    const char *from_token;
    int64_t from_queue_id;
    size_t accepted;
    bool valid = false;
    if (error != NULL && error_size > 0U) error[0] = '\0';
    if (
        state == NULL || from_track == NULL || to_track == NULL
        || (from_deck != 'A' && from_deck != 'B')
        || (to_deck != 'A' && to_deck != 'B') || from_deck == to_deck
        || !from_track->loaded || !to_track->loaded
        || from_track->slot_token[0] == '\0' || to_track->slot_token[0] == '\0'
        || primed_pcm == NULL || primed_bytes < WB_AUDIO_FRAME_BYTES
    ) {
        if (error != NULL && error_size > 0U) {
            copy_text(error, error_size, "invalid hard handoff request");
        }
        return -1;
    }
    output = &state->icecast_output;
    primed_bytes -= primed_bytes % WB_AUDIO_FRAME_BYTES;
    (void)pthread_mutex_lock(&output->lock);
    from_token = from_deck == 'B' ? output->deck_b_slot_token : output->deck_a_slot_token;
    from_queue_id = from_deck == 'B' ? output->deck_b_queue_id : output->deck_a_queue_id;
    valid = output->engine_running && output->primary_deck == from_deck
        && strcmp(from_token, from_track->slot_token) == 0
        && from_queue_id == from_track->queue_id;
    if (!valid) {
        (void)pthread_mutex_unlock(&output->lock);
        if (error != NULL && error_size > 0U) {
            copy_text(error, error_size, "active outgoing identity changed");
        }
        return -1;
    }

    clear_hard_handoff_locked(output);
    reset_fifo_locked(output, to_deck);
    if (to_deck == 'B') {
        copy_text(output->deck_b_slot_token, sizeof(output->deck_b_slot_token), to_track->slot_token);
        output->deck_b_queue_id = to_track->queue_id;
        accepted = fifo_write(
            output->deck_b_fifo, output->fifo_capacity,
            &output->deck_b_write_pos, &output->deck_b_fill,
            primed_pcm, primed_bytes
        );
        output->deck_b_started = accepted > 0U;
        if (output->deck_b_fill > output->deck_b_fifo_high_water_bytes) {
            output->deck_b_fifo_high_water_bytes = output->deck_b_fill;
        }
    } else {
        copy_text(output->deck_a_slot_token, sizeof(output->deck_a_slot_token), to_track->slot_token);
        output->deck_a_queue_id = to_track->queue_id;
        accepted = fifo_write(
            output->deck_a_fifo, output->fifo_capacity,
            &output->deck_a_write_pos, &output->deck_a_fill,
            primed_pcm, primed_bytes
        );
        output->deck_a_started = accepted > 0U;
        if (output->deck_a_fill > output->deck_a_fifo_high_water_bytes) {
            output->deck_a_fifo_high_water_bytes = output->deck_a_fill;
        }
    }
    if (accepted != primed_bytes || accepted == 0U) {
        reset_fifo_locked(output, to_deck);
        if (to_deck == 'B') {
            output->deck_b_slot_token[0] = '\0';
            output->deck_b_queue_id = 0;
        } else {
            output->deck_a_slot_token[0] = '\0';
            output->deck_a_queue_id = 0;
        }
        (void)pthread_mutex_unlock(&output->lock);
        if (error != NULL && error_size > 0U) {
            copy_text(error, error_size, "target prime FIFO rejected PCM");
        }
        return -1;
    }

    output->transitioning = false;
    output->transition_from_deck = '\0';
    output->transition_to_deck = '\0';
    output->transition_start_monotonic_ms = 0;
    output->transition_entry_start_monotonic_ms = 0;
    output->transition_entry_requested_monotonic_ms = 0;
    output->transition_entry_pcm_start_monotonic_ms = 0;
    output->transition_entry_waiting_for_pcm = false;
    output->transition_duration_ms = 0;
    output->transition_fade_out_ms = 0;
    output->transition_entry_ramp_ms = 0;
    output->transition_silence_hold_ms = 0;
    output->hard_handoff_pending = true;
    output->hard_handoff_wait_outgoing_drain = false;
    output->hard_handoff_from_deck = from_deck;
    output->hard_handoff_to_deck = to_deck;
    output->hard_handoff_at_monotonic_ms = handoff_at_monotonic_ms > 0
        ? handoff_at_monotonic_ms : monotonic_ms();
    output->hard_handoff_requested_monotonic_ms = monotonic_ms();
    output->hard_handoff_from_track = *from_track;
    output->hard_handoff_to_track = *to_track;
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    return 0;
}

bool wb_icecast_output_has_pending_hard_handoff(
    WbEngineState *state,
    char from_deck,
    const WbDeckState *from_track
) {
    WbIcecastOutput *output;
    bool pending = false;
    if (state == NULL || from_track == NULL) return false;
    output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    pending = output->hard_handoff_pending
        && output->hard_handoff_from_deck == from_deck
        && output->hard_handoff_from_track.loaded
        && strcmp(output->hard_handoff_from_track.slot_token, from_track->slot_token) == 0;
    (void)pthread_mutex_unlock(&output->lock);
    return pending;
}

bool wb_icecast_output_handle_terminal_eof(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t early_by_ms,
    bool early_eof
) {
    WbIcecastOutput *output;
    bool matched = false;
    bool outgoing_transition = false;
    bool active_primary = false;
    bool claimed_handoff = false;
    char target_deck = '\0';
    WbDeckState target_track = {0};
    uint64_t transition_count = 0U;
    uint64_t active_count = 0U;
    size_t trailing_silence_trimmed_bytes = 0U;
    size_t outgoing_remaining_bytes = 0U;
    char payload[1152];
    int64_t now_ms;
    if (state == NULL || track == NULL) return false;
    output = &state->icecast_output;
    now_ms = monotonic_ms();
    (void)pthread_mutex_lock(&output->lock);
    if (deck == 'B') {
        matched = track->slot_token[0] != '\0'
            && strcmp(output->deck_b_slot_token, track->slot_token) == 0
            && output->deck_b_queue_id == track->queue_id;
    } else {
        matched = track->slot_token[0] != '\0'
            && strcmp(output->deck_a_slot_token, track->slot_token) == 0
            && output->deck_a_queue_id == track->queue_id;
    }
    if (matched) {
        outgoing_transition = output->transitioning && output->transition_from_deck == deck;
        active_primary = output->primary_deck == deck && !outgoing_transition;
        if (
            active_primary && output->hard_handoff_pending
            && output->hard_handoff_from_deck == deck
            && output->hard_handoff_from_track.queue_id == track->queue_id
            && strcmp(output->hard_handoff_from_track.slot_token, track->slot_token) == 0
        ) {
            claimed_handoff = true;
            target_deck = output->hard_handoff_to_deck;
            target_track = output->hard_handoff_to_track;
            if (early_eof) {
                if (deck == 'B') {
                    trailing_silence_trimmed_bytes = trim_trailing_silence_fifo_locked(
                        output->deck_b_fifo,
                        output->fifo_capacity,
                        output->deck_b_read_pos,
                        &output->deck_b_write_pos,
                        &output->deck_b_fill
                    );
                    outgoing_remaining_bytes = output->deck_b_fill;
                } else {
                    trailing_silence_trimmed_bytes = trim_trailing_silence_fifo_locked(
                        output->deck_a_fifo,
                        output->fifo_capacity,
                        output->deck_a_read_pos,
                        &output->deck_a_write_pos,
                        &output->deck_a_fill
                    );
                    outgoing_remaining_bytes = output->deck_a_fill;
                }
            } else {
                outgoing_remaining_bytes = deck == 'B' ? output->deck_b_fill : output->deck_a_fill;
            }
            output->hard_handoff_wait_outgoing_drain = outgoing_remaining_bytes > 0U;
            output->hard_handoff_at_monotonic_ms = now_ms;
            if (early_eof) output->active_early_eof_count += 1U;
        } else if (early_eof) {
            if (deck == 'B') {
                output->deck_b_started = false;
                output->deck_b_slot_token[0] = '\0';
                output->deck_b_queue_id = 0;
            } else {
                output->deck_a_started = false;
                output->deck_a_slot_token[0] = '\0';
                output->deck_a_queue_id = 0;
            }
            reset_fifo_locked(output, deck);
            if (outgoing_transition) {
                output->transition_early_eof_count += 1U;
            } else if (active_primary) {
                output->active_early_eof_count += 1U;
            }
        }
    }
    transition_count = output->transition_early_eof_count;
    active_count = output->active_early_eof_count;
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    if (!matched) return false;

    if (claimed_handoff) {
        (void)wb_audio_probe_retime_activation(state, target_deck, &target_track, now_ms);
    }
    (void)snprintf(
        payload,
        sizeof(payload),
        "{\"native_icecast_output\":true,\"handled\":true,"
        "\"reason\":\"%s\",\"early_by_ms\":%lld,"
        "\"outgoing_transition\":%s,\"active_primary\":%s,"
        "\"hard_handoff_claimed\":%s,\"hard_handoff_target_deck\":\"%c\","
        "\"trailing_silence_trimmed_bytes\":%zu,\"trailing_silence_trimmed_ms\":%lld,"
        "\"outgoing_remaining_bytes\":%zu,\"outgoing_remaining_ms\":%lld,"
        "\"transition_early_eof_count\":%llu,\"active_early_eof_count\":%llu}",
        early_eof ? "early_eof" : "natural_eof",
        (long long)(early_by_ms > 0 ? early_by_ms : 0),
        outgoing_transition ? "true" : "false",
        active_primary ? "true" : "false",
        claimed_handoff ? "true" : "false",
        target_deck == 'A' || target_deck == 'B' ? target_deck : '-',
        trailing_silence_trimmed_bytes,
        (long long)(((trailing_silence_trimmed_bytes / WB_AUDIO_FRAME_BYTES) * 1000ULL) / WB_AUDIO_SAMPLE_RATE),
        outgoing_remaining_bytes,
        (long long)(((outgoing_remaining_bytes / WB_AUDIO_FRAME_BYTES) * 1000ULL) / WB_AUDIO_SAMPLE_RATE),
        (unsigned long long)transition_count,
        (unsigned long long)active_count
    );
    (void)wb_engine_send_event(
        state,
        early_eof
            ? (outgoing_transition ? "native_transition_early_eof_handled" : "native_active_early_eof_handled")
            : "native_active_terminal_eof_handled",
        track,
        deck,
        payload
    );
    return claimed_handoff;
}

void wb_icecast_output_handle_early_eof(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    int64_t early_by_ms
) {
    (void)wb_icecast_output_handle_terminal_eof(
        state, deck, track, early_by_ms, true
    );
}

void wb_icecast_output_transition_started(
    WbEngineState *state,
    char from_deck,
    char to_deck,
    int64_t start_monotonic_ms,
    int64_t entry_start_monotonic_ms,
    int64_t release_duration_ms,
    int64_t fade_out_duration_ms,
    int64_t entry_ramp_ms,
    int64_t silence_hold_ms
) {
    WbIcecastOutput *output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    bool target_has_pcm;
    clear_hard_handoff_locked(output);
    output->transition_from_deck = from_deck;
    output->transition_to_deck = to_deck;
    output->transition_start_monotonic_ms = start_monotonic_ms > 0 ? start_monotonic_ms : monotonic_ms();
    output->transition_entry_requested_monotonic_ms = entry_start_monotonic_ms > 0
        ? entry_start_monotonic_ms
        : output->transition_start_monotonic_ms;
    output->transition_duration_ms = release_duration_ms > 0 ? release_duration_ms : 0;
    output->transition_fade_out_ms = fade_out_duration_ms > 0 ? fade_out_duration_ms : 0;
    output->transition_entry_ramp_ms = entry_ramp_ms > 0 ? entry_ramp_ms : 0;
    output->transition_silence_hold_ms = silence_hold_ms > 0 ? silence_hold_ms : 0;
    output->transitioning = release_duration_ms > 0 && (from_deck == 'A' || from_deck == 'B');
    target_has_pcm = to_deck == 'B'
        ? (output->deck_b_started && output->deck_b_fill > 0U)
        : (output->deck_a_started && output->deck_a_fill > 0U);
    if (output->transitioning && output->transition_entry_ramp_ms > 0 && !target_has_pcm) {
        output->transition_entry_start_monotonic_ms = 0;
        output->transition_entry_pcm_start_monotonic_ms = 0;
        output->transition_entry_waiting_for_pcm = true;
    } else {
        output->transition_entry_start_monotonic_ms = output->transition_entry_requested_monotonic_ms;
        output->transition_entry_pcm_start_monotonic_ms = target_has_pcm
            ? output->transition_entry_requested_monotonic_ms
            : 0;
        output->transition_entry_waiting_for_pcm = false;
    }
    output->primary_deck = to_deck;
    (void)pthread_mutex_unlock(&output->lock);
}

void wb_icecast_output_transition_finished(WbEngineState *state, char to_deck) {
    WbIcecastOutput *output = &state->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    clear_hard_handoff_locked(output);
    output->transitioning = false;
    output->transition_from_deck = '\0';
    output->transition_to_deck = '\0';
    output->transition_start_monotonic_ms = 0;
    output->transition_entry_start_monotonic_ms = 0;
    output->transition_entry_requested_monotonic_ms = 0;
    output->transition_entry_pcm_start_monotonic_ms = 0;
    output->transition_entry_waiting_for_pcm = false;
    output->transition_duration_ms = 0;
    output->transition_fade_out_ms = 0;
    output->transition_entry_ramp_ms = 0;
    output->transition_silence_hold_ms = 0;
    output->primary_deck = to_deck;
    (void)pthread_mutex_unlock(&output->lock);
}

void wb_icecast_output_push_pcm(
    WbEngineState *state,
    char deck,
    const WbDeckState *track,
    const unsigned char *data,
    size_t bytes,
    bool seek_restart
) {
    WbIcecastOutput *output = &state->icecast_output;
    size_t accepted = 0U;
    bool identity_matched = false;
    bool dropped_old_seek_pcm = false;
    if (data == NULL || bytes == 0U || track == NULL) return;
    (void)pthread_mutex_lock(&output->lock);
    if (output->enabled && output->engine_running) {
        if (deck == 'B' && identity_matches(output->deck_b_slot_token, track)) {
            identity_matched = true;
            if (
                output->deck_b_seek_pending
                && seek_restart
                && identity_matches(output->deck_b_seek_slot_token, track)
            ) {
                reset_fifo_locked(output, 'B');
                output->deck_b_seek_pending = false;
                output->deck_b_seek_slot_token[0] = '\0';
                output->seek_flush_count += 1U;
            }
            if (output->deck_b_seek_pending && !seek_restart) {
                dropped_old_seek_pcm = true;
            } else {
                accepted = fifo_write(
                    output->deck_b_fifo, output->fifo_capacity,
                    &output->deck_b_write_pos, &output->deck_b_fill,
                    data, bytes
                );
                if (accepted > 0U) {
                    output->deck_b_started = true;
                    if (output->deck_b_fill > output->deck_b_fifo_high_water_bytes) {
                        output->deck_b_fifo_high_water_bytes = output->deck_b_fill;
                    }
                }
            }
        } else if (deck != 'B' && identity_matches(output->deck_a_slot_token, track)) {
            identity_matched = true;
            if (
                output->deck_a_seek_pending
                && seek_restart
                && identity_matches(output->deck_a_seek_slot_token, track)
            ) {
                reset_fifo_locked(output, 'A');
                output->deck_a_seek_pending = false;
                output->deck_a_seek_slot_token[0] = '\0';
                output->seek_flush_count += 1U;
            }
            if (output->deck_a_seek_pending && !seek_restart) {
                dropped_old_seek_pcm = true;
            } else {
                accepted = fifo_write(
                    output->deck_a_fifo, output->fifo_capacity,
                    &output->deck_a_write_pos, &output->deck_a_fill,
                    data, bytes
                );
                if (accepted > 0U) {
                    output->deck_a_started = true;
                    if (output->deck_a_fill > output->deck_a_fifo_high_water_bytes) {
                        output->deck_a_fifo_high_water_bytes = output->deck_a_fill;
                    }
                }
            }
        }
        if (
            accepted > 0U
            && output->transitioning
            && output->transition_to_deck == deck
            && output->transition_entry_waiting_for_pcm
        ) {
            int64_t pcm_start_ms = monotonic_ms();
            output->transition_entry_start_monotonic_ms = pcm_start_ms;
            output->transition_entry_pcm_start_monotonic_ms = pcm_start_ms;
            output->transition_entry_waiting_for_pcm = false;
            output->transition_entry_pcm_start_count += 1U;
        }
        if (dropped_old_seek_pcm) {
            output->seek_old_pcm_drop_count += 1U;
            output->seek_old_pcm_drop_bytes += (uint64_t)bytes;
        } else if (identity_matched && accepted < bytes) {
            output->fifo_overrun_count += 1U;
            output->fifo_overrun_bytes += (uint64_t)(bytes - accepted);
        } else if (!identity_matched) {
            output->stale_pcm_drop_count += 1U;
            output->stale_pcm_drop_bytes += (uint64_t)bytes;
        }
    }
    (void)pthread_mutex_unlock(&output->lock);
}

static int ensure_stream_worker(WbNativeStreamOutput *stream, char *error, size_t error_size) {
    WbIcecastOutput *output;
    int create_result;
    if (stream == NULL || stream->owner == NULL) {
        copy_text(error, error_size, "Native output worker has no owner");
        return -1;
    }
    output = &stream->owner->icecast_output;
    (void)pthread_mutex_lock(&output->lock);
    if (stream->thread_created) {
        (void)pthread_mutex_unlock(&output->lock);
        return 0;
    }
    stream->worker_shutdown = false;
    create_result = pthread_create(&stream->thread, NULL, stream_worker_main, stream);
    if (create_result == 0) stream->thread_created = true;
    (void)pthread_mutex_unlock(&output->lock);
    if (create_result != 0) {
        (void)snprintf(error, error_size, "Cannot create native output worker: %s", strerror(create_result));
        return -1;
    }
    return 0;
}

int wb_icecast_output_configure(
    WbEngineState *state,
    const char *output_id,
    const char *codec_value,
    bool enabled,
    const char *host,
    int port,
    const char *mount,
    const char *username,
    const char *password,
    int bitrate_kbps,
    const char *stream_name,
    const char *stream_description,
    const char *stream_genre,
    const char *stream_url,
    bool public_stream,
    bool add_year_to_metadata,
    bool dsp_enabled,
    const char *dsp_config_path,
    char *error,
    size_t error_size
) {
    WbIcecastOutput *output = &state->icecast_output;
    WbNativeStreamOutput *stream;
    WbDeckState active_track = {0};
    char normalized_codec[WB_NATIVE_OUTPUT_CODEC_SIZE] = "";
    char refreshed_metadata[WB_ICECAST_METADATA_SIZE] = "";
    char event_output_id[WB_NATIVE_OUTPUT_ID_SIZE] = "";
    char event_codec[WB_NATIVE_OUTPUT_CODEC_SIZE] = "";
    bool has_active_track = false;
    bool was_enabled = false;
    bool live_add_candidate = false;
    bool live_remove_candidate = false;
    bool full_restart_required = false;
    bool desired_dsp_enabled = false;
    bool dsp_configuration_changed = false;
    bool stream_configuration_changed = false;
    bool preserve_live_stream = false;
    bool live_dsp_switch_candidate = false;
    bool pipeline_live = false;
    bool defer_dsp_shutdown = false;
    size_t enabled_after = 0U;
    size_t stream_index = 0U;
    uint64_t stream_config_generation = 0U;
    size_t index;
    int64_t now_ms = monotonic_ms();

    if (!native_output_id_valid(output_id)) {
        copy_text(error, error_size, "Native output ID must contain only letters, digits, - or _");
        return -1;
    }
    if (!normalize_codec(codec_value, normalized_codec, sizeof(normalized_codec))) {
        copy_text(error, error_size, "Native output codec must be mp3 or aac_he_v2");
        return -1;
    }
    if (bitrate_kbps <= 0) bitrate_kbps = codec_default_bitrate(normalized_codec);
    if (enabled) {
        if (host == NULL || host[0] == '\0') {
            copy_text(error, error_size, "Icecast host is required");
            return -1;
        }
        if (port < 1 || port > 65535) {
            copy_text(error, error_size, "Icecast port must be between 1 and 65535");
            return -1;
        }
        if (mount == NULL || mount[0] != '/' || strlen(mount) < 2U) {
            copy_text(error, error_size, "Native stream mount must begin with / and must not be empty");
            return -1;
        }
        if (username == NULL || username[0] == '\0') {
            copy_text(error, error_size, "Icecast source username is required");
            return -1;
        }
        if (!codec_bitrate_valid(normalized_codec, bitrate_kbps)) {
            copy_text(
                error,
                error_size,
                strcmp(normalized_codec, "aac_he_v2") == 0
                    ? "AAC+ bitrate must be between 24 and 96 kbit/s"
                    : "MP3 bitrate must be between 32 and 320 kbit/s"
            );
            return -1;
        }
        if (dsp_enabled) {
            if (dsp_config_path == NULL || dsp_config_path[0] != '/') {
                copy_text(error, error_size, "Native DSP .dat configuration path must be absolute");
                return -1;
            }
            if (access(dsp_config_path, R_OK) != 0) {
                (void)snprintf(error, error_size, "Native DSP .dat configuration is not readable: %s", strerror(errno));
                return -1;
            }
        }
    }

    (void)pthread_mutex_lock(&output->lock);
    stream = stream_by_id_locked(output, output_id, true);
    if (stream == NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        copy_text(error, error_size, "Maximum number of native stream outputs reached");
        return -1;
    }
    if (enabled && (password == NULL || password[0] == '\0') && stream->password[0] == '\0') {
        (void)pthread_mutex_unlock(&output->lock);
        copy_text(error, error_size, "Icecast source password is required");
        return -1;
    }
    if (enabled) {
        for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
            WbNativeStreamOutput *other = &output->streams[index];
            if (other == stream || !other->configured || !other->enabled) continue;
            if (other->port == port && strcmp(other->host, host) == 0 && strcmp(other->mount, mount) == 0) {
                (void)pthread_mutex_unlock(&output->lock);
                copy_text(error, error_size, "Another native output already uses this host/port/mount");
                return -1;
            }
        }
    }
    (void)pthread_mutex_unlock(&output->lock);

    if (enabled) {
        (void)pthread_mutex_lock(&state->lock);
        if (state->active_deck == 'B' && state->deck_b.loaded) {
            active_track = state->deck_b;
            has_active_track = true;
        } else if (state->active_deck == 'A' && state->deck_a.loaded) {
            active_track = state->deck_a;
            has_active_track = true;
        }
        (void)pthread_mutex_unlock(&state->lock);
        if (has_active_track) {
            format_track_metadata(&active_track, add_year_to_metadata, refreshed_metadata, sizeof(refreshed_metadata));
        }
    }

    (void)pthread_mutex_lock(&output->lock);
    stream = stream_by_id_locked(output, output_id, false);
    if (stream == NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        copy_text(error, error_size, "Native output disappeared during configuration");
        return -1;
    }
    was_enabled = stream->enabled;
    stream_index = stream->slot_index;
    stream_configuration_changed = was_enabled != enabled
        || strcmp(stream->codec, normalized_codec) != 0
        || strcmp(stream->host, host == NULL ? "" : host) != 0
        || stream->port != port
        || strcmp(stream->mount, mount == NULL ? "" : mount) != 0
        || strcmp(stream->username, username == NULL ? "" : username) != 0
        || ((password != NULL && password[0] != '\0') && strcmp(stream->password, password) != 0)
        || stream->bitrate_kbps != bitrate_kbps
        || strcmp(stream->stream_name, stream_name == NULL ? "" : stream_name) != 0
        || strcmp(stream->stream_description, stream_description == NULL ? "" : stream_description) != 0
        || strcmp(stream->stream_genre, stream_genre == NULL ? "" : stream_genre) != 0
        || strcmp(stream->stream_url, stream_url == NULL ? "" : stream_url) != 0
        || stream->public_stream != public_stream
        || stream->add_year_to_metadata != add_year_to_metadata;
    preserve_live_stream = was_enabled && enabled && !stream_configuration_changed;
    stream->enabled = enabled;
    copy_text(stream->codec, sizeof(stream->codec), normalized_codec);
    copy_text(stream->content_type, sizeof(stream->content_type), codec_content_type(normalized_codec));
    if (host != NULL) copy_text(stream->host, sizeof(stream->host), host);
    if (mount != NULL) copy_text(stream->mount, sizeof(stream->mount), mount);
    if (username != NULL) copy_text(stream->username, sizeof(stream->username), username);
    if (password != NULL && password[0] != '\0') copy_text(stream->password, sizeof(stream->password), password);
    if (stream_name != NULL) copy_text(stream->stream_name, sizeof(stream->stream_name), stream_name);
    if (stream_description != NULL) copy_text(stream->stream_description, sizeof(stream->stream_description), stream_description);
    if (stream_genre != NULL) copy_text(stream->stream_genre, sizeof(stream->stream_genre), stream_genre);
    if (stream_url != NULL) copy_text(stream->stream_url, sizeof(stream->stream_url), stream_url);
    stream->port = port;
    stream->bitrate_kbps = bitrate_kbps;
    stream->public_stream = public_stream;
    stream->add_year_to_metadata = add_year_to_metadata;
    if (stream_configuration_changed) stream->config_generation += 1U;
    stream_config_generation = stream->config_generation;
    if (stream_configuration_changed) {
        stream->next_reconnect_monotonic_ms = 0;
        stream->reconnect_backoff_seconds = 0;
        stream->consecutive_send_errors = 0U;
        reset_encoded_fifo_locked(stream);
        close_fd(&stream->icecast_fd);
        stream->connected = false;
    }
    if (!enabled) {
        stream->metadata_pending = false;
        stream->metadata_not_before_monotonic_ms = 0;
        stream->metadata_generation += 1U;
        copy_text(stream->status, sizeof(stream->status), "disabled");
        stream->error[0] = '\0';
    } else if (stream_configuration_changed) {
        if (has_active_track && refreshed_metadata[0] != '\0') {
            copy_text(stream->current_metadata, sizeof(stream->current_metadata), refreshed_metadata);
            copy_text(stream->current_metadata_slot_token, sizeof(stream->current_metadata_slot_token), active_track.slot_token);
            stream->current_metadata_queue_id = active_track.queue_id;
            stream->metadata_generation += 1U;
            stream->metadata_requested_count += 1U;
            stream->metadata_pending = true;
            stream->metadata_not_before_monotonic_ms = dsp_enabled
                ? now_ms + WB_OUTPUT_DSP_METADATA_DELAY_MS : 0;
        }
        copy_text(stream->status, sizeof(stream->status), output->engine_running ? "connecting" : "configured");
        stream->error[0] = '\0';
    }
    enabled_after = enabled_stream_count_locked(output);
    desired_dsp_enabled = enabled_after > 0U && dsp_enabled;
    dsp_configuration_changed = output->dsp_enabled != desired_dsp_enabled;
    if (desired_dsp_enabled && output->dsp_enabled) {
        dsp_configuration_changed = dsp_configuration_changed
            || strcmp(output->dsp_config_path, dsp_config_path == NULL ? "" : dsp_config_path) != 0;
    }
    pipeline_live = output->engine_running && output->encoder_running
        && output->encoder_context != NULL && !output->restart_requested;
    live_dsp_switch_candidate = pipeline_live
        && dsp_configuration_changed
        && enabled_after > 0U
        && preserve_live_stream;
    defer_dsp_shutdown = pipeline_live && enabled_after == 0U;
    live_add_candidate = pipeline_live && !was_enabled && enabled
        && !dsp_configuration_changed;
    live_remove_candidate = pipeline_live && was_enabled && !enabled
        && enabled_after > 0U && !dsp_configuration_changed;
    full_restart_required = output->engine_running && output->encoder_running && (
        (dsp_configuration_changed && !live_dsp_switch_candidate)
        || (was_enabled && enabled && stream_configuration_changed)
        || (!was_enabled && enabled && !live_add_candidate)
        || (was_enabled && !enabled && enabled_after > 0U && !live_remove_candidate)
    );

    if (!defer_dsp_shutdown) output->dsp_enabled = desired_dsp_enabled;
    if (live_dsp_switch_candidate && desired_dsp_enabled) {
        output->dsp_live_bypass_until_ready = true;
    }
    if (output->dsp_enabled) {
        copy_text(output->dsp_config_path, sizeof(output->dsp_config_path), dsp_config_path);
        copy_text(output->dsp_status, sizeof(output->dsp_status), "configured");
        output->dsp_error[0] = '\0';
    } else {
        output->dsp_config_path[0] = '\0';
        output->dsp_running = false;
        output->dsp_ready = false;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
        output->dsp_error[0] = '\0';
    }
    output->stream_config_generation += 1U;
    if (full_restart_required) output->restart_requested = true;
    copy_text(event_output_id, sizeof(event_output_id), stream->output_id);
    copy_text(event_codec, sizeof(event_codec), stream->codec);
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);

    if (live_dsp_switch_candidate) {
        if (reconfigure_live_dsp(output, desired_dsp_enabled, error, error_size) != 0) {
            return -1;
        }
    }

    if (enabled && ensure_stream_worker(stream, error, error_size) != 0) {
        (void)pthread_mutex_lock(&output->lock);
        stream->enabled = false;
        stream->encoder_ready = false;
        copy_text(stream->status, sizeof(stream->status), "worker_error");
        copy_text(stream->error, sizeof(stream->error), error);
        output->stream_config_generation += 1U;
        if (output->engine_running && output->encoder_running && was_enabled) {
            output->restart_requested = true;
        }
        mirror_default_stream_locked(output);
        (void)pthread_cond_broadcast(&output->cond);
        (void)pthread_mutex_unlock(&output->lock);
        return -1;
    }

    if (live_add_candidate) {
        int live_result = add_live_encoder_branch(
            output,
            stream_index,
            stream_config_generation,
            normalized_codec,
            bitrate_kbps,
            error,
            error_size
        );
        if (live_result < 0) {
            (void)pthread_mutex_lock(&output->lock);
            stream = &output->streams[stream_index];
            if (stream->config_generation == stream_config_generation) {
                stream->enabled = false;
                stream->encoder_ready = false;
                stream_disconnect_locked(stream, error, false, false);
                copy_text(stream->status, sizeof(stream->status), "encoder_error");
                copy_text(stream->error, sizeof(stream->error), error);
                output->stream_config_generation += 1U;
            }
            mirror_default_stream_locked(output);
            (void)pthread_cond_broadcast(&output->cond);
            (void)pthread_mutex_unlock(&output->lock);
            return -1;
        }
        if (live_result > 0) {
            (void)pthread_mutex_lock(&output->lock);
            if (output->engine_running && output->encoder_running) output->restart_requested = true;
            (void)pthread_cond_broadcast(&output->cond);
            (void)pthread_mutex_unlock(&output->lock);
        } else {
            emit_stream_event(output, &output->streams[stream_index], "native_encoder_branch_added", "encoder branch attached without restarting other outputs");
        }
    } else if (live_remove_candidate) {
        int live_result = remove_live_encoder_branch(output, stream_index, error, error_size);
        if (live_result != 0) {
            (void)pthread_mutex_lock(&output->lock);
            if (output->engine_running && output->encoder_running) output->restart_requested = true;
            (void)pthread_cond_broadcast(&output->cond);
            (void)pthread_mutex_unlock(&output->lock);
        } else {
            emit_stream_event(output, &output->streams[stream_index], "native_encoder_branch_removed", "encoder branch detached without restarting other outputs");
        }
    }
    if (enabled && stream_configuration_changed && has_active_track && refreshed_metadata[0] != '\0') {
        emit_metadata_event(
            state, "native_icecast_metadata_requested", true, false, false,
            event_output_id, event_codec, refreshed_metadata,
            active_track.queue_id, active_track.slot_token, "configuration_updated"
        );
    }
    return 0;
}

int wb_icecast_output_clear_stream(
    WbEngineState *state,
    const char *output_id,
    char *error,
    size_t error_size
) {
    WbIcecastOutput *output = &state->icecast_output;
    WbNativeStreamOutput *stream;
    WbEngineState *owner;
    pthread_t thread;
    bool thread_created;
    bool worker_shutdown;
    bool was_enabled;
    bool live_remove_candidate;
    bool branch_detached = false;
    size_t slot_index;
    size_t remaining_enabled;
    unsigned char *encoded_fifo;
    size_t encoded_fifo_capacity;
    void *old_encoder_context;
    uint64_t disabled_generation;
    struct timespec deadline;

    if (!native_output_id_valid(output_id)) {
        copy_text(error, error_size, "Invalid native output ID");
        return -1;
    }

    (void)pthread_mutex_lock(&output->lock);
    stream = stream_by_id_locked(output, output_id, false);
    if (stream == NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        return 0;
    }
    was_enabled = stream->enabled;
    slot_index = stream->slot_index;
    old_encoder_context = output->encoder_context;
    stream->enabled = false;
    stream->encoder_ready = false;
    stream->config_generation += 1U;
    disabled_generation = stream->config_generation;
    stream->metadata_pending = false;
    stream->metadata_not_before_monotonic_ms = 0;
    close_fd(&stream->icecast_fd);
    stream->connected = false;
    reset_encoded_fifo_locked(stream);
    copy_text(stream->status, sizeof(stream->status), "disabled");
    stream->error[0] = '\0';
    remaining_enabled = enabled_stream_count_locked(output);
    live_remove_candidate = was_enabled && remaining_enabled > 0U
        && output->engine_running && output->encoder_running
        && output->encoder_context != NULL && !output->restart_requested;
    output->stream_config_generation += 1U;
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);

    if (live_remove_candidate) {
        int live_result = remove_live_encoder_branch(output, slot_index, error, error_size);
        if (live_result == 0) {
            branch_detached = true;
            emit_stream_event(
                output,
                &output->streams[slot_index],
                "native_encoder_branch_removed",
                "encoder branch detached without restarting other outputs"
            );
        }
    }

    if (was_enabled && !branch_detached && old_encoder_context != NULL) {
        (void)pthread_mutex_lock(&output->lock);
        if (remaining_enabled > 0U && output->encoder_context == old_encoder_context) {
            output->restart_requested = true;
            (void)pthread_cond_broadcast(&output->cond);
        }
        (void)clock_gettime(CLOCK_REALTIME, &deadline);
        deadline.tv_sec += 5;
        while (
            !output->shutdown
            && output->encoder_context == old_encoder_context
            && output->encoder_running
        ) {
            int wait_result = pthread_cond_timedwait(&output->cond, &output->lock, &deadline);
            if (wait_result == ETIMEDOUT) break;
        }
        if (output->encoder_context == old_encoder_context && output->encoder_running) {
            (void)pthread_mutex_unlock(&output->lock);
            copy_text(error, error_size, "Timed out while detaching native encoder branch");
            return -1;
        }
        (void)pthread_mutex_unlock(&output->lock);
    }

    (void)pthread_mutex_lock(&output->lock);
    stream = &output->streams[slot_index];
    if (
        !stream->configured
        || stream->config_generation != disabled_generation
        || stream->enabled
        || strcmp(stream->output_id, output_id) != 0
    ) {
        (void)pthread_mutex_unlock(&output->lock);
        copy_text(error, error_size, "Native output changed while it was being removed");
        return -1;
    }
    owner = stream->owner;
    thread = stream->thread;
    thread_created = stream->thread_created;
    worker_shutdown = stream->worker_shutdown;
    slot_index = stream->slot_index;
    encoded_fifo = stream->encoded_fifo;
    encoded_fifo_capacity = stream->encoded_fifo_capacity;
    memset(stream->password, 0, sizeof(stream->password));
    memset(stream, 0, sizeof(*stream));
    stream->owner = owner;
    stream->thread = thread;
    stream->thread_created = thread_created;
    stream->worker_shutdown = worker_shutdown;
    stream->slot_index = slot_index;
    stream->encoder_stdout_fd = -1;
    stream->icecast_fd = -1;
    stream->encoded_fifo = encoded_fifo;
    stream->encoded_fifo_capacity = encoded_fifo_capacity;
    if (output->stream_count > 0U) output->stream_count -= 1U;
    if (enabled_stream_count_locked(output) == 0U && !output->encoder_running) {
        output->dsp_enabled = false;
        output->dsp_executable_path[0] = '\0';
        output->dsp_config_path[0] = '\0';
        output->dsp_log_path[0] = '\0';
        output->dsp_running = false;
        output->dsp_ready = false;
        copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
        output->dsp_error[0] = '\0';
    }
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    if (error != NULL && error_size > 0U) error[0] = '\0';
    return 0;
}

void wb_icecast_output_clear(WbEngineState *state) {
    WbIcecastOutput *output = &state->icecast_output;
    size_t index;
    (void)pthread_mutex_lock(&output->lock);
    for (index = 0U; index < WB_NATIVE_OUTPUT_MAX; index += 1U) {
        WbNativeStreamOutput *stream = &output->streams[index];
        WbEngineState *owner = stream->owner;
        pthread_t thread = stream->thread;
        bool thread_created = stream->thread_created;
        bool worker_shutdown = stream->worker_shutdown;
        size_t slot_index = stream->slot_index;
        unsigned char *encoded_fifo = stream->encoded_fifo;
        size_t encoded_fifo_capacity = stream->encoded_fifo_capacity;
        close_fd(&stream->icecast_fd);
        memset(stream->password, 0, sizeof(stream->password));
        memset(stream, 0, sizeof(*stream));
        stream->owner = owner;
        stream->thread = thread;
        stream->thread_created = thread_created;
        stream->worker_shutdown = worker_shutdown;
        stream->slot_index = slot_index;
        stream->encoder_stdout_fd = -1;
        stream->icecast_fd = -1;
        stream->encoded_fifo = encoded_fifo;
        stream->encoded_fifo_capacity = encoded_fifo_capacity;
    }
    output->stream_count = 0U;
    output->dsp_enabled = false;
    output->dsp_executable_path[0] = '\0';
    output->dsp_config_path[0] = '\0';
    output->dsp_log_path[0] = '\0';
    copy_text(output->dsp_status, sizeof(output->dsp_status), "bypassed");
    output->restart_requested = true;
    output->stream_config_generation += 1U;
    mirror_default_stream_locked(output);
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
}


int wb_icecast_output_kill_encoder(WbEngineState *state, char *error, size_t error_size) {
    WbIcecastOutput *output = &state->icecast_output;
    WbLibavEncoderGroup *group;
    (void)pthread_mutex_lock(&output->encoder_control_lock);
    (void)pthread_mutex_lock(&output->lock);
    group = (WbLibavEncoderGroup *)output->encoder_context;
    if (!output->enabled || !output->engine_running || !output->encoder_running || group == NULL) {
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->encoder_control_lock);
        copy_text(error, error_size, "embedded native encoder is not running");
        return -1;
    }
    output->encoder_kill_test_count += 1U;
    (void)pthread_mutex_unlock(&output->lock);
    wb_libav_encoder_group_inject_failure(group, "injected embedded encoder termination");
    (void)pthread_mutex_unlock(&output->encoder_control_lock);
    emit_output_event(state, "native_icecast_encoder_kill_requested", "test embedded encoder failure requested");
    return 0;
}


int wb_icecast_output_kill_dsp(WbEngineState *state, char *error, size_t error_size) {
    WbIcecastOutput *output = &state->icecast_output;
    ssnative_dsp *context;
    (void)pthread_mutex_lock(&output->pcm_route_lock);
    (void)pthread_mutex_lock(&output->lock);
    context = (ssnative_dsp *)output->dsp_context;
    if (
        !output->enabled || !output->engine_running || !output->dsp_enabled
        || !output->dsp_running || context == NULL
    ) {
        (void)pthread_mutex_unlock(&output->lock);
        (void)pthread_mutex_unlock(&output->pcm_route_lock);
        copy_text(error, error_size, "in-process SoundSolution DSP is not running");
        return -1;
    }
    output->dsp_context = NULL;
    output->dsp_running = false;
    output->dsp_ready = false;
    output->dsp_route_active = false;
    output->dsp_output_failed = true;
    output->dsp_kill_test_count += 1U;
    copy_text(output->dsp_status, sizeof(output->dsp_status), "failed");
    copy_text(output->dsp_error, sizeof(output->dsp_error), "injected in-process DSP failure");
    (void)pthread_cond_broadcast(&output->cond);
    (void)pthread_mutex_unlock(&output->lock);
    ssnative_destroy(context);
    (void)pthread_mutex_unlock(&output->pcm_route_lock);
    emit_output_event(state, "native_dsp_kill_requested", "test in-process DSP failure requested");
    return 0;
}

static int append_json_text(char *buffer, size_t capacity, size_t *used, const char *format, ...) {
    va_list arguments;
    int written;
    if (buffer == NULL || used == NULL || format == NULL || *used >= capacity) return -1;
    va_start(arguments, format);
    written = vsnprintf(buffer + *used, capacity - *used, format, arguments);
    va_end(arguments);
    if (written < 0) return -1;
    if ((size_t)written >= capacity - *used) { *used = capacity; return -1; }
    *used += (size_t)written;
    return 0;
}

int wb_icecast_output_state_json(WbEngineState *state, char *output_json, size_t output_size) {
    WbIcecastOutput *output = &state->icecast_output;
    char output_id[WB_NATIVE_OUTPUT_ID_SIZE];
    char codec[WB_NATIVE_OUTPUT_CODEC_SIZE];
    char content_type[WB_NATIVE_OUTPUT_CONTENT_TYPE_SIZE];
    char host[WB_ICECAST_HOST_SIZE];
    char mount[WB_ICECAST_MOUNT_SIZE];
    char username[WB_ICECAST_USER_SIZE];
    char stream_name[WB_ICECAST_NAME_SIZE];
    char stream_description[WB_ICECAST_DESCRIPTION_SIZE];
    char stream_genre[WB_ICECAST_GENRE_SIZE];
    char stream_url[WB_ICECAST_URL_SIZE];
    char status[WB_ICECAST_STATUS_SIZE];
    char error[WB_ICECAST_ERROR_SIZE];
    char metadata[WB_ICECAST_METADATA_SIZE];
    char metadata_slot_token[WB_SLOT_TOKEN_SIZE];
    char metadata_error[WB_ICECAST_ERROR_SIZE];
    char dsp_executable_path[WB_PATH_SIZE];
    char dsp_config_path[WB_PATH_SIZE];
    char dsp_log_path[WB_PATH_SIZE];
    char dsp_status[WB_DSP_STATUS_SIZE];
    char dsp_error[WB_DSP_ERROR_SIZE];
    char output_gap_reason[WB_DIAGNOSTIC_REASON_SIZE];
    char escaped_output_gap_reason[WB_DIAGNOSTIC_REASON_SIZE * 2U];
    char escaped_host[WB_ICECAST_HOST_SIZE * 2U];
    char escaped_mount[WB_ICECAST_MOUNT_SIZE * 2U];
    char escaped_username[WB_ICECAST_USER_SIZE * 2U];
    char escaped_name[WB_ICECAST_NAME_SIZE * 2U];
    char escaped_description[WB_ICECAST_DESCRIPTION_SIZE * 2U];
    char escaped_genre[WB_ICECAST_GENRE_SIZE * 2U];
    char escaped_url[WB_ICECAST_URL_SIZE * 2U];
    char escaped_status[WB_ICECAST_STATUS_SIZE * 2U];
    char escaped_error[WB_ICECAST_ERROR_SIZE * 2U];
    char escaped_metadata[WB_ICECAST_METADATA_SIZE * 2U];
    char escaped_metadata_slot_token[WB_SLOT_TOKEN_SIZE * 2U];
    char escaped_metadata_error[WB_ICECAST_ERROR_SIZE * 2U];
    char escaped_dsp_executable_path[WB_PATH_SIZE * 2U];
    char escaped_dsp_config_path[WB_PATH_SIZE * 2U];
    char escaped_dsp_log_path[WB_PATH_SIZE * 2U];
    char escaped_dsp_status[WB_DSP_STATUS_SIZE * 2U];
    char escaped_dsp_error[WB_DSP_ERROR_SIZE * 2U];
    bool enabled;
    bool engine_running;
    bool public_stream;
    bool add_year_to_metadata;
    bool connected;
    bool encoder_running;
    bool dsp_enabled;
    bool dsp_running;
    bool dsp_ready;
    bool dsp_context_active;
    bool transitioning;
    bool has_password;
    bool metadata_pending;
    int port;
    int bitrate;
    int reconnect_backoff_seconds;
    pid_t encoder_pid;
    uint64_t encoder_generation;
    pid_t dsp_pid;
    pid_t old_dsp_pid;
    pid_t new_dsp_pid;
    bool old_dsp_reaped;
    pid_t old_encoder_pid;
    pid_t new_encoder_pid;
    bool old_encoder_reaped;
    char primary_deck;
    char transition_from_deck;
    char transition_to_deck;
    int64_t transition_start_ms;
    int64_t transition_entry_start_ms;
    int64_t transition_entry_requested_ms;
    int64_t transition_entry_pcm_start_ms;
    bool transition_entry_waiting_for_pcm;
    int64_t transition_duration_ms;
    int64_t transition_fade_out_ms;
    int64_t transition_entry_ramp_ms;
    int64_t transition_silence_hold_ms;
    int64_t encoder_started_ms;
    int64_t last_encoded_data_ms;
    int64_t last_icecast_send_ms;
    int64_t metadata_queue_id;
    int64_t metadata_not_before_ms;
    double gain_a = 0.0;
    double gain_b = 0.0;
    size_t fifo_a;
    size_t fifo_b;
    size_t fifo_a_high_water;
    size_t fifo_b_high_water;
    uint64_t connect_count;
    uint64_t disconnect_count;
    uint64_t reconnect_count;
    uint64_t send_error_count;
    uint64_t encoder_restart_count;
    uint64_t pipeline_restart_count;
    uint64_t encoder_stall_count;
    uint64_t icecast_stall_count;
    uint64_t consecutive_send_errors;
    uint64_t metadata_requested_count;
    uint64_t metadata_applied_count;
    uint64_t metadata_failed_count;
    uint64_t metadata_generation;
    uint64_t metadata_applied_generation;
    uint64_t encoder_kill_test_count;
    uint64_t dsp_start_count;
    uint64_t dsp_restart_count;
    uint64_t dsp_process_replacement_count;
    uint64_t dsp_stall_count;
    uint64_t dsp_kill_test_count;
    uint64_t dsp_startup_silence_frames;
    uint64_t dsp_input_backpressure_count;
    size_t dsp_input_fifo_bytes;
    size_t dsp_input_fifo_capacity_bytes;
    size_t dsp_input_fifo_high_water_bytes;
    uint64_t dsp_input_fifo_overrun_count;
    uint64_t dsp_input_fifo_overrun_bytes;
    uint64_t dsp_input_fifo_startup_drop_bytes;
    uint64_t dsp_input_bytes_enqueued;
    uint64_t dsp_input_bytes_written;
    uint64_t dsp_write_poll_timeout_count;
    uint64_t dsp_write_error_count;
    uint64_t dsp_output_bytes_read;
    uint64_t dsp_output_push_error_count;
    uint64_t dsp_live_switch_count;
    bool dsp_route_active;
    bool dsp_live_bypass_until_ready;
    int64_t dsp_writer_last_progress_ms;
    int64_t dsp_writer_backpressure_started_ms;
    int64_t dsp_writer_max_backpressure_ms;
    int64_t dsp_writer_error_since_ms;
    int dsp_writer_last_errno;
    int dsp_writer_last_revents;
    uint64_t dsp_reap_count;
    uint64_t dsp_zombie_count;
    int64_t dsp_reap_duration_ms;
    uint64_t bytes_sent;
    uint64_t encoded_bytes_total;
    uint64_t icecast_sent_bytes_total;
    uint64_t output_gap_count;
    int64_t max_output_gap_ms;
    uint64_t encoder_reap_count;
    uint64_t zombie_encoder_count;
    int64_t encoder_reap_duration_ms;
    uint64_t mixed_frames;
    uint64_t silence_frames;
    uint64_t deck_fifo_empty_count;
    uint64_t mixed_output_silence_count;
    uint64_t underrun_count;
    uint64_t underrun_event_count;
    uint64_t underrun_suppressed_event_count;
    uint64_t underrun_suppressed_since_last_event;
    int64_t last_underrun_event_ms;
    uint64_t transition_early_eof_count;
    uint64_t active_early_eof_count;
    uint64_t overrun_count;
    uint64_t overrun_bytes;
    uint64_t stale_drop_count;
    uint64_t stale_drop_bytes;
    uint64_t seek_flush_count;
    bool deck_a_seek_pending;
    bool deck_b_seek_pending;
    uint64_t seek_bridge_count;
    uint64_t seek_bridge_bytes;
    uint64_t seek_bridge_drop_bytes;
    uint64_t seek_old_pcm_drop_count;
    uint64_t seek_old_pcm_drop_bytes;
    uint64_t transition_entry_pcm_start_count;
    WbNativeStreamOutput stream_snapshots[WB_NATIVE_OUTPUT_MAX];
    size_t configured_output_count = 0U;
    size_t enabled_output_count = 0U;
    size_t connected_output_count = 0U;
    size_t stream_index;
    int base_written;
    size_t used;
    (void)pthread_mutex_lock(&output->lock);
    mirror_default_stream_locked(output);
    {
        WbNativeStreamOutput *default_stream = default_stream_locked(output);
        copy_text(output_id, sizeof(output_id), default_stream != NULL ? default_stream->output_id : "mp3");
        copy_text(codec, sizeof(codec), default_stream != NULL ? default_stream->codec : "mp3");
        copy_text(content_type, sizeof(content_type), default_stream != NULL ? default_stream->content_type : "audio/mpeg");
    }
    for (stream_index = 0U; stream_index < WB_NATIVE_OUTPUT_MAX; stream_index += 1U) {
        stream_snapshots[stream_index] = output->streams[stream_index];
        if (output->streams[stream_index].configured) configured_output_count += 1U;
        if (output->streams[stream_index].configured && output->streams[stream_index].enabled) enabled_output_count += 1U;
        if (output->streams[stream_index].configured && output->streams[stream_index].enabled && output->streams[stream_index].connected) connected_output_count += 1U;
    }
    enabled = output->enabled;
    engine_running = output->engine_running;
    public_stream = output->public_stream;
    add_year_to_metadata = output->add_year_to_metadata;
    connected = output->connected;
    encoder_running = output->encoder_running;
    dsp_enabled = output->dsp_enabled;
    dsp_running = output->dsp_running;
    dsp_ready = output->dsp_ready;
    dsp_context_active = output->dsp_context != NULL;
    transitioning = output->transitioning;
    has_password = output->password[0] != '\0';
    metadata_pending = output->metadata_pending;
    port = output->port;
    bitrate = output->bitrate_kbps;
    reconnect_backoff_seconds = output->reconnect_backoff_seconds;
    encoder_pid = output->encoder_pid;
    encoder_generation = output->encoder_generation;
    dsp_pid = output->dsp_pid;
    old_dsp_pid = output->old_dsp_pid;
    new_dsp_pid = output->new_dsp_pid;
    old_dsp_reaped = output->old_dsp_reaped;
    old_encoder_pid = output->old_encoder_pid;
    new_encoder_pid = output->new_encoder_pid;
    old_encoder_reaped = output->old_encoder_reaped;
    primary_deck = output->primary_deck;
    transition_from_deck = output->transition_from_deck;
    transition_to_deck = output->transition_to_deck;
    transition_start_ms = output->transition_start_monotonic_ms;
    transition_entry_start_ms = output->transition_entry_start_monotonic_ms;
    transition_entry_requested_ms = output->transition_entry_requested_monotonic_ms;
    transition_entry_pcm_start_ms = output->transition_entry_pcm_start_monotonic_ms;
    transition_entry_waiting_for_pcm = output->transition_entry_waiting_for_pcm;
    transition_duration_ms = output->transition_duration_ms;
    transition_fade_out_ms = output->transition_fade_out_ms;
    transition_entry_ramp_ms = output->transition_entry_ramp_ms;
    transition_silence_hold_ms = output->transition_silence_hold_ms;
    encoder_started_ms = output->encoder_started_monotonic_ms;
    last_encoded_data_ms = output->last_encoded_data_monotonic_ms;
    last_icecast_send_ms = output->last_icecast_send_monotonic_ms;
    metadata_queue_id = output->current_metadata_queue_id;
    metadata_not_before_ms = output->metadata_not_before_monotonic_ms;
    fifo_a = output->deck_a_fill;
    fifo_b = output->deck_b_fill;
    fifo_a_high_water = output->deck_a_fifo_high_water_bytes;
    fifo_b_high_water = output->deck_b_fifo_high_water_bytes;
    connect_count = output->connect_count;
    disconnect_count = output->disconnect_count;
    reconnect_count = output->reconnect_count;
    send_error_count = output->send_error_count;
    encoder_restart_count = output->encoder_restart_count;
    pipeline_restart_count = output->pipeline_restart_count;
    encoder_stall_count = output->encoder_stall_count;
    icecast_stall_count = output->icecast_stall_count;
    consecutive_send_errors = output->consecutive_send_errors;
    metadata_requested_count = output->metadata_requested_count;
    metadata_applied_count = output->metadata_applied_count;
    metadata_failed_count = output->metadata_failed_count;
    metadata_generation = output->metadata_generation;
    metadata_applied_generation = output->metadata_applied_generation;
    encoder_kill_test_count = output->encoder_kill_test_count;
    dsp_start_count = output->dsp_start_count;
    dsp_restart_count = output->dsp_restart_count;
    dsp_process_replacement_count = output->dsp_process_replacement_count;
    dsp_stall_count = output->dsp_stall_count;
    dsp_kill_test_count = output->dsp_kill_test_count;
    dsp_startup_silence_frames = output->dsp_startup_silence_frames;
    dsp_input_backpressure_count = output->dsp_input_backpressure_count;
    dsp_input_fifo_bytes = output->dsp_input_fifo_fill;
    dsp_input_fifo_capacity_bytes = output->dsp_input_fifo_capacity;
    dsp_input_fifo_high_water_bytes = output->dsp_input_fifo_high_water;
    dsp_input_fifo_overrun_count = output->dsp_input_fifo_overrun_count;
    dsp_input_fifo_overrun_bytes = output->dsp_input_fifo_overrun_bytes;
    dsp_input_fifo_startup_drop_bytes = output->dsp_input_fifo_startup_drop_bytes;
    dsp_input_bytes_enqueued = output->dsp_input_bytes_enqueued;
    dsp_input_bytes_written = output->dsp_input_bytes_written;
    dsp_write_poll_timeout_count = output->dsp_write_poll_timeout_count;
    dsp_write_error_count = output->dsp_write_error_count;
    dsp_output_bytes_read = output->dsp_output_bytes_read;
    dsp_output_push_error_count = output->dsp_output_push_error_count;
    dsp_live_switch_count = output->dsp_live_switch_count;
    dsp_route_active = output->dsp_route_active;
    dsp_live_bypass_until_ready = output->dsp_live_bypass_until_ready;
    dsp_writer_last_progress_ms = output->dsp_writer_last_progress_monotonic_ms;
    dsp_writer_backpressure_started_ms = output->dsp_writer_backpressure_started_monotonic_ms;
    dsp_writer_max_backpressure_ms = output->dsp_writer_max_backpressure_ms;
    dsp_writer_error_since_ms = output->dsp_writer_error_since_monotonic_ms;
    dsp_writer_last_errno = output->dsp_writer_last_errno;
    dsp_writer_last_revents = (int)(unsigned short)output->dsp_writer_last_revents;
    dsp_reap_count = output->dsp_reap_count;
    dsp_zombie_count = output->dsp_zombie_count;
    dsp_reap_duration_ms = output->dsp_reap_duration_ms;
    bytes_sent = output->encoded_bytes_sent;
    encoded_bytes_total = output->encoded_bytes_total;
    icecast_sent_bytes_total = output->icecast_sent_bytes_total;
    output_gap_count = output->output_gap_count;
    max_output_gap_ms = output->max_output_gap_ms;
    encoder_reap_count = output->encoder_reap_count;
    zombie_encoder_count = output->zombie_encoder_count;
    encoder_reap_duration_ms = output->encoder_reap_duration_ms;
    mixed_frames = output->mixed_frames;
    silence_frames = output->silence_frames;
    deck_fifo_empty_count = output->deck_fifo_empty_count;
    mixed_output_silence_count = output->mixed_output_silence_count;
    underrun_count = output->output_underrun_count;
    underrun_event_count = output->output_underrun_event_count;
    underrun_suppressed_event_count = output->output_underrun_suppressed_event_count;
    underrun_suppressed_since_last_event = output->output_underrun_suppressed_since_last_event;
    last_underrun_event_ms = output->last_output_underrun_event_monotonic_ms;
    transition_early_eof_count = output->transition_early_eof_count;
    active_early_eof_count = output->active_early_eof_count;
    overrun_count = output->fifo_overrun_count;
    overrun_bytes = output->fifo_overrun_bytes;
    stale_drop_count = output->stale_pcm_drop_count;
    stale_drop_bytes = output->stale_pcm_drop_bytes;
    seek_flush_count = output->seek_flush_count;
    deck_a_seek_pending = output->deck_a_seek_pending;
    deck_b_seek_pending = output->deck_b_seek_pending;
    seek_bridge_count = output->seek_bridge_count;
    seek_bridge_bytes = output->seek_bridge_bytes;
    seek_bridge_drop_bytes = output->seek_bridge_drop_bytes;
    seek_old_pcm_drop_count = output->seek_old_pcm_drop_count;
    seek_old_pcm_drop_bytes = output->seek_old_pcm_drop_bytes;
    transition_entry_pcm_start_count = output->transition_entry_pcm_start_count;
    copy_text(host, sizeof(host), output->host);
    copy_text(mount, sizeof(mount), output->mount);
    copy_text(username, sizeof(username), output->username);
    copy_text(stream_name, sizeof(stream_name), output->stream_name);
    copy_text(stream_description, sizeof(stream_description), output->stream_description);
    copy_text(stream_genre, sizeof(stream_genre), output->stream_genre);
    copy_text(stream_url, sizeof(stream_url), output->stream_url);
    copy_text(status, sizeof(status), output->status);
    copy_text(error, sizeof(error), output->error);
    copy_text(metadata, sizeof(metadata), output->current_metadata);
    copy_text(metadata_slot_token, sizeof(metadata_slot_token), output->current_metadata_slot_token);
    copy_text(metadata_error, sizeof(metadata_error), output->metadata_error);
    copy_text(dsp_executable_path, sizeof(dsp_executable_path), output->dsp_executable_path);
    copy_text(dsp_config_path, sizeof(dsp_config_path), output->dsp_config_path);
    copy_text(dsp_log_path, sizeof(dsp_log_path), output->dsp_log_path);
    copy_text(dsp_status, sizeof(dsp_status), output->dsp_status);
    copy_text(dsp_error, sizeof(dsp_error), output->dsp_error);
    copy_text(output_gap_reason, sizeof(output_gap_reason), output->last_output_gap_reason);
    (void)pthread_mutex_unlock(&output->lock);
    if (transitioning && transition_duration_ms > 0) {
        int64_t now_ms = monotonic_ms();
        int64_t elapsed_ms = now_ms - transition_start_ms;
        int64_t entry_elapsed_ms = transition_entry_start_ms > 0 ? now_ms - transition_entry_start_ms : 0;
        double old_gain = wb_fade_out_gain(elapsed_ms, transition_fade_out_ms);
        double new_gain = transition_entry_start_ms > 0
            ? wb_entry_gain(entry_elapsed_ms, transition_entry_ramp_ms) : 0.0;
        if (transition_from_deck == 'A') gain_a = old_gain;
        if (transition_from_deck == 'B') gain_b = old_gain;
        if (transition_to_deck == 'A') gain_a = new_gain;
        if (transition_to_deck == 'B') gain_b = new_gain;
    } else {
        gain_a = primary_deck == 'A' ? 1.0 : 0.0;
        gain_b = primary_deck == 'B' ? 1.0 : 0.0;
    }
    wb_json_escape(host, escaped_host, sizeof(escaped_host));
    wb_json_escape(mount, escaped_mount, sizeof(escaped_mount));
    wb_json_escape(username, escaped_username, sizeof(escaped_username));
    wb_json_escape(stream_name, escaped_name, sizeof(escaped_name));
    wb_json_escape(stream_description, escaped_description, sizeof(escaped_description));
    wb_json_escape(stream_genre, escaped_genre, sizeof(escaped_genre));
    wb_json_escape(stream_url, escaped_url, sizeof(escaped_url));
    wb_json_escape(status, escaped_status, sizeof(escaped_status));
    wb_json_escape(error, escaped_error, sizeof(escaped_error));
    wb_json_escape(metadata, escaped_metadata, sizeof(escaped_metadata));
    wb_json_escape(metadata_slot_token, escaped_metadata_slot_token, sizeof(escaped_metadata_slot_token));
    wb_json_escape(metadata_error, escaped_metadata_error, sizeof(escaped_metadata_error));
    wb_json_escape(dsp_executable_path, escaped_dsp_executable_path, sizeof(escaped_dsp_executable_path));
    wb_json_escape(dsp_config_path, escaped_dsp_config_path, sizeof(escaped_dsp_config_path));
    wb_json_escape(dsp_log_path, escaped_dsp_log_path, sizeof(escaped_dsp_log_path));
    wb_json_escape(dsp_status, escaped_dsp_status, sizeof(escaped_dsp_status));
    wb_json_escape(dsp_error, escaped_dsp_error, sizeof(escaped_dsp_error));
    wb_json_escape(output_gap_reason, escaped_output_gap_reason, sizeof(escaped_output_gap_reason));
    base_written = snprintf(
        output_json,
        output_size,
        "{\"supported\":true,\"enabled\":%s,\"engine_running\":%s,"
        "\"host\":\"%s\",\"port\":%d,\"mount\":\"%s\","
        "\"username\":\"%s\",\"password_configured\":%s,"
        "\"output_id\":\"%s\",\"codec\":\"%s\",\"content_type\":\"%s\","
        "\"bitrate_kbps\":%d,\"sample_rate\":%d,\"channels\":%d,"
        "\"stream_name\":\"%s\",\"stream_description\":\"%s\"," 
        "\"stream_genre\":\"%s\",\"stream_url\":\"%s\"," 
        "\"public_stream\":%s,\"add_year_to_metadata\":%s,"
        "\"status\":\"%s\",\"error\":\"%s\",\"connected\":%s,"
        "\"encoder_running\":%s,\"encoder_pid\":%ld,"
        "\"encoder_generation\":%llu,"
        "\"dsp_enabled\":%s,\"dsp_running\":%s,\"dsp_ready\":%s,"
        "\"dsp_backend\":\"libsoundsolution.so.2\",\"dsp_in_process\":true,"
        "\"dsp_context_active\":%s,\"dsp_pid\":%ld,\"dsp_executable_path\":\"%s\","
        "\"dsp_config_path\":\"%s\",\"dsp_log_path\":\"%s\","
        "\"dsp_status\":\"%s\",\"dsp_error\":\"%s\","
        "\"primary_deck\":\"%c\","
        "\"transitioning\":%s,\"deck_a_gain\":%.6f,\"deck_b_gain\":%.6f,"
        "\"transition_curve\":\"smoothstep\",\"transition_entry_start_monotonic_ms\":%lld,"
        "\"transition_entry_requested_monotonic_ms\":%lld,"
        "\"transition_entry_pcm_start_monotonic_ms\":%lld,"
        "\"transition_entry_waiting_for_pcm\":%s,"
        "\"transition_fade_out_ms\":%lld,"
        "\"transition_entry_ramp_ms\":%lld,\"transition_silence_hold_ms\":%lld,"
        "\"transition_release_duration_ms\":%lld,"
        "\"deck_a_fifo_bytes\":%zu,\"deck_b_fifo_bytes\":%zu,"
        "\"deck_a_fifo_high_water_bytes\":%zu,\"deck_b_fifo_high_water_bytes\":%zu,"
        "\"connect_count\":%llu,\"disconnect_count\":%llu,\"reconnect_count\":%llu,"
        "\"send_error_count\":%llu,\"consecutive_send_errors\":%llu,"
        "\"encoder_restart_count\":%llu,\"pipeline_restart_count\":%llu,\"encoder_stall_count\":%llu,"
        "\"icecast_stall_count\":%llu,\"encoder_kill_test_count\":%llu,"
        "\"dsp_start_count\":%llu,\"dsp_restart_count\":%llu,"
        "\"dsp_process_replacement_count\":%llu,"
        "\"dsp_stall_count\":%llu,\"dsp_kill_test_count\":%llu,"
        "\"dsp_startup_silence_frames\":%llu,\"dsp_input_backpressure_count\":%llu,"
        "\"encoder_started_monotonic_ms\":%lld,"
        "\"last_encoded_data_monotonic_ms\":%lld,"
        "\"last_icecast_send_monotonic_ms\":%lld,"
        "\"reconnect_backoff_seconds\":%d,"
        "\"metadata_requested\":%llu,\"metadata_applied\":%llu,\"metadata_failed\":%llu,"
        "\"metadata_requested_count\":%llu,\"metadata_applied_count\":%llu,\"metadata_failed_count\":%llu,"
        "\"metadata_pending\":%s,\"metadata_generation\":%llu,"
        "\"metadata_not_before_monotonic_ms\":%lld,\"dsp_metadata_delay_ms\":%d,"
        "\"metadata_applied_generation\":%llu,"
        "\"metadata_value\":\"%s\",\"current_metadata\":\"%s\","
        "\"queue_id\":%lld,\"slot_token\":\"%s\",\"metadata_error\":\"%s\","
        "\"encoded_bytes_sent\":%llu,\"encoded_bytes_total\":%llu,"
        "\"icecast_sent_bytes_total\":%llu,\"output_gap_count\":%llu,"
        "\"max_output_gap_ms\":%lld,\"last_output_gap_reason\":\"%s\","
        "\"old_encoder_pid\":%ld,\"new_encoder_pid\":%ld,"
        "\"old_encoder_reaped\":%s,\"encoder_reap_count\":%llu,"
        "\"old_dsp_pid\":%ld,\"new_dsp_pid\":%ld,"
        "\"old_dsp_reaped\":%s,\"dsp_reap_count\":%llu,"
        "\"dsp_reap_duration_ms\":%lld,\"dsp_zombie_count\":%llu,"
        "\"encoder_reap_duration_ms\":%lld,\"zombie_encoder_count\":%llu,"
        "\"mixed_frames\":%llu,\"silence_frames\":%llu,"
        "\"deck_fifo_empty_count\":%llu,\"mixed_output_silence_count\":%llu,"
        "\"output_underrun_count\":%llu,\"output_underrun_event_count\":%llu,"
        "\"output_underrun_suppressed_event_count\":%llu,"
        "\"output_underrun_suppressed_since_last_event\":%llu,"
        "\"last_output_underrun_event_monotonic_ms\":%lld,"
        "\"transition_early_eof_count\":%llu,\"active_early_eof_count\":%llu,"
        "\"fifo_overrun_count\":%llu,\"fifo_overrun_bytes\":%llu,"
        "\"stale_pcm_drop_count\":%llu,\"stale_pcm_drop_bytes\":%llu,"
        "\"seek_flush_count\":%llu,\"deck_a_seek_pending\":%s,\"deck_b_seek_pending\":%s,"
        "\"seek_bridge_count\":%llu,\"seek_bridge_bytes\":%llu,"
        "\"seek_bridge_drop_bytes\":%llu,\"seek_old_pcm_drop_count\":%llu,"
        "\"seek_old_pcm_drop_bytes\":%llu,"
        "\"transition_entry_pcm_start_count\":%llu}",
        enabled ? "true" : "false",
        engine_running ? "true" : "false",
        escaped_host,
        port,
        escaped_mount,
        escaped_username,
        has_password ? "true" : "false",
        output_id,
        codec,
        content_type,
        bitrate,
        WB_AUDIO_SAMPLE_RATE,
        WB_AUDIO_CHANNELS,
        escaped_name,
        escaped_description,
        escaped_genre,
        escaped_url,
        public_stream ? "true" : "false",
        add_year_to_metadata ? "true" : "false",
        escaped_status,
        escaped_error,
        connected ? "true" : "false",
        encoder_running ? "true" : "false",
        (long)encoder_pid,
        (unsigned long long)encoder_generation,
        dsp_enabled ? "true" : "false",
        dsp_running ? "true" : "false",
        dsp_ready ? "true" : "false",
        dsp_context_active ? "true" : "false",
        (long)dsp_pid,
        escaped_dsp_executable_path,
        escaped_dsp_config_path,
        escaped_dsp_log_path,
        escaped_dsp_status,
        escaped_dsp_error,
        primary_deck ? primary_deck : '-',
        transitioning ? "true" : "false",
        gain_a,
        gain_b,
        (long long)transition_entry_start_ms,
        (long long)transition_entry_requested_ms,
        (long long)transition_entry_pcm_start_ms,
        transition_entry_waiting_for_pcm ? "true" : "false",
        (long long)transition_fade_out_ms,
        (long long)transition_entry_ramp_ms,
        (long long)transition_silence_hold_ms,
        (long long)transition_duration_ms,
        fifo_a,
        fifo_b,
        fifo_a_high_water,
        fifo_b_high_water,
        (unsigned long long)connect_count,
        (unsigned long long)disconnect_count,
        (unsigned long long)reconnect_count,
        (unsigned long long)send_error_count,
        (unsigned long long)consecutive_send_errors,
        (unsigned long long)encoder_restart_count,
        (unsigned long long)pipeline_restart_count,
        (unsigned long long)encoder_stall_count,
        (unsigned long long)icecast_stall_count,
        (unsigned long long)encoder_kill_test_count,
        (unsigned long long)dsp_start_count,
        (unsigned long long)dsp_restart_count,
        (unsigned long long)dsp_process_replacement_count,
        (unsigned long long)dsp_stall_count,
        (unsigned long long)dsp_kill_test_count,
        (unsigned long long)dsp_startup_silence_frames,
        (unsigned long long)dsp_input_backpressure_count,
        (long long)encoder_started_ms,
        (long long)last_encoded_data_ms,
        (long long)last_icecast_send_ms,
        reconnect_backoff_seconds,
        (unsigned long long)metadata_requested_count,
        (unsigned long long)metadata_applied_count,
        (unsigned long long)metadata_failed_count,
        (unsigned long long)metadata_requested_count,
        (unsigned long long)metadata_applied_count,
        (unsigned long long)metadata_failed_count,
        metadata_pending ? "true" : "false",
        (unsigned long long)metadata_generation,
        (long long)metadata_not_before_ms,
        WB_OUTPUT_DSP_METADATA_DELAY_MS,
        (unsigned long long)metadata_applied_generation,
        escaped_metadata,
        escaped_metadata,
        (long long)metadata_queue_id,
        escaped_metadata_slot_token,
        escaped_metadata_error,
        (unsigned long long)bytes_sent,
        (unsigned long long)encoded_bytes_total,
        (unsigned long long)icecast_sent_bytes_total,
        (unsigned long long)output_gap_count,
        (long long)max_output_gap_ms,
        escaped_output_gap_reason,
        (long)old_encoder_pid,
        (long)new_encoder_pid,
        old_encoder_reaped ? "true" : "false",
        (unsigned long long)encoder_reap_count,
        (long)old_dsp_pid,
        (long)new_dsp_pid,
        old_dsp_reaped ? "true" : "false",
        (unsigned long long)dsp_reap_count,
        (long long)dsp_reap_duration_ms,
        (unsigned long long)dsp_zombie_count,
        (long long)encoder_reap_duration_ms,
        (unsigned long long)zombie_encoder_count,
        (unsigned long long)mixed_frames,
        (unsigned long long)silence_frames,
        (unsigned long long)deck_fifo_empty_count,
        (unsigned long long)mixed_output_silence_count,
        (unsigned long long)underrun_count,
        (unsigned long long)underrun_event_count,
        (unsigned long long)underrun_suppressed_event_count,
        (unsigned long long)underrun_suppressed_since_last_event,
        (long long)last_underrun_event_ms,
        (unsigned long long)transition_early_eof_count,
        (unsigned long long)active_early_eof_count,
        (unsigned long long)overrun_count,
        (unsigned long long)overrun_bytes,
        (unsigned long long)stale_drop_count,
        (unsigned long long)stale_drop_bytes,
        (unsigned long long)seek_flush_count,
        deck_a_seek_pending ? "true" : "false",
        deck_b_seek_pending ? "true" : "false",
        (unsigned long long)seek_bridge_count,
        (unsigned long long)seek_bridge_bytes,
        (unsigned long long)seek_bridge_drop_bytes,
        (unsigned long long)seek_old_pcm_drop_count,
        (unsigned long long)seek_old_pcm_drop_bytes,
        (unsigned long long)transition_entry_pcm_start_count
    );
    if (base_written < 0) return base_written;
    if ((size_t)base_written >= output_size || base_written == 0 || output_json[base_written - 1] != '}') return base_written;
    used = (size_t)base_written - 1U;
    output_json[used] = '\0';
    if (append_json_text(output_json, output_size, &used,
        ",\"dsp_input_fifo_bytes\":%zu,\"dsp_input_fifo_capacity_bytes\":%zu,"
        "\"dsp_input_fifo_high_water_bytes\":%zu,"
        "\"dsp_input_fifo_overrun_count\":%llu,\"dsp_input_fifo_overrun_bytes\":%llu,"
        "\"dsp_input_fifo_startup_drop_bytes\":%llu,"
        "\"dsp_input_bytes_enqueued\":%llu,\"dsp_input_bytes_written\":%llu,"
        "\"dsp_write_poll_timeout_count\":%llu,\"dsp_write_error_count\":%llu,"
        "\"dsp_writer_last_progress_monotonic_ms\":%lld,"
        "\"dsp_writer_backpressure_started_monotonic_ms\":%lld,"
        "\"dsp_writer_max_backpressure_ms\":%lld,"
        "\"dsp_writer_error_since_monotonic_ms\":%lld,"
        "\"dsp_writer_last_errno\":%d,\"dsp_writer_last_revents\":%d,"
        "\"dsp_route_active\":%s,\"dsp_live_bypass_until_ready\":%s,"
        "\"dsp_output_bytes_read\":%llu,\"dsp_output_push_error_count\":%llu,"
        "\"dsp_live_switch_count\":%llu,"
        "\"dsp_startup_timeout_ms\":%d,\"dsp_input_stall_timeout_ms\":%d,"
        "\"icecast_connect_after_encoder_ready\":true,"
        "\"multi_output\":true,\"max_outputs\":%d,\"output_count\":%zu,"
        "\"enabled_output_count\":%zu,\"connected_output_count\":%zu,"
        "\"encoder_process_model\":\"in_process_libav_multi_output\","
        "\"encoder_backend\":\"embedded_libav\",\"ffmpeg_subprocesses\":false,"
        "\"pcm_fanout\":\"embedded_libav_multi_output\",\"shared_dsp\":true,\"outputs\":[",
        dsp_input_fifo_bytes,
        dsp_input_fifo_capacity_bytes,
        dsp_input_fifo_high_water_bytes,
        (unsigned long long)dsp_input_fifo_overrun_count,
        (unsigned long long)dsp_input_fifo_overrun_bytes,
        (unsigned long long)dsp_input_fifo_startup_drop_bytes,
        (unsigned long long)dsp_input_bytes_enqueued,
        (unsigned long long)dsp_input_bytes_written,
        (unsigned long long)dsp_write_poll_timeout_count,
        (unsigned long long)dsp_write_error_count,
        (long long)dsp_writer_last_progress_ms,
        (long long)dsp_writer_backpressure_started_ms,
        (long long)dsp_writer_max_backpressure_ms,
        (long long)dsp_writer_error_since_ms,
        dsp_writer_last_errno,
        dsp_writer_last_revents,
        dsp_route_active ? "true" : "false",
        dsp_live_bypass_until_ready ? "true" : "false",
        (unsigned long long)dsp_output_bytes_read,
        (unsigned long long)dsp_output_push_error_count,
        (unsigned long long)dsp_live_switch_count,
        WB_OUTPUT_DSP_STARTUP_TIMEOUT_MS,
        WB_OUTPUT_DSP_INPUT_STALL_TIMEOUT_MS,
        WB_NATIVE_OUTPUT_MAX, configured_output_count, enabled_output_count, connected_output_count) != 0) return (int)used;
    {
        bool first_output = true;
        for (stream_index = 0U; stream_index < WB_NATIVE_OUTPUT_MAX; stream_index += 1U) {
            const WbNativeStreamOutput *stream = &stream_snapshots[stream_index];
            char eid[WB_NATIVE_OUTPUT_ID_SIZE * 2U], eco[WB_NATIVE_OUTPUT_CODEC_SIZE * 2U];
            char ect[WB_NATIVE_OUTPUT_CONTENT_TYPE_SIZE * 2U], eh[WB_ICECAST_HOST_SIZE * 2U];
            char em[WB_ICECAST_MOUNT_SIZE * 2U], eu[WB_ICECAST_USER_SIZE * 2U];
            char en[WB_ICECAST_NAME_SIZE * 2U], edesc[WB_ICECAST_DESCRIPTION_SIZE * 2U];
            char egenre[WB_ICECAST_GENRE_SIZE * 2U], eurl[WB_ICECAST_URL_SIZE * 2U];
            char es[WB_ICECAST_STATUS_SIZE * 2U];
            char ee[WB_ICECAST_ERROR_SIZE * 2U], emeta[WB_ICECAST_METADATA_SIZE * 2U];
            char etoken[WB_SLOT_TOKEN_SIZE * 2U], emerr[WB_ICECAST_ERROR_SIZE * 2U];
            char egap[WB_DIAGNOSTIC_REASON_SIZE * 2U];
            if (!stream->configured) continue;
            wb_json_escape(stream->output_id, eid, sizeof(eid)); wb_json_escape(stream->codec, eco, sizeof(eco));
            wb_json_escape(stream->content_type, ect, sizeof(ect)); wb_json_escape(stream->host, eh, sizeof(eh));
            wb_json_escape(stream->mount, em, sizeof(em)); wb_json_escape(stream->username, eu, sizeof(eu));
            wb_json_escape(stream->stream_name, en, sizeof(en));
            wb_json_escape(stream->stream_description, edesc, sizeof(edesc));
            wb_json_escape(stream->stream_genre, egenre, sizeof(egenre));
            wb_json_escape(stream->stream_url, eurl, sizeof(eurl));
            wb_json_escape(stream->status, es, sizeof(es));
            wb_json_escape(stream->error, ee, sizeof(ee)); wb_json_escape(stream->current_metadata, emeta, sizeof(emeta));
            wb_json_escape(stream->current_metadata_slot_token, etoken, sizeof(etoken));
            wb_json_escape(stream->metadata_error, emerr, sizeof(emerr));
            wb_json_escape(stream->last_output_gap_reason, egap, sizeof(egap));
            if (append_json_text(output_json, output_size, &used,
                "%s{\"output_id\":\"%s\",\"codec\":\"%s\",\"content_type\":\"%s\","
                "\"configured\":true,\"enabled\":%s,\"host\":\"%s\",\"port\":%d,"
                "\"mount\":\"%s\",\"username\":\"%s\",\"password_configured\":%s,"
                "\"bitrate_kbps\":%d,\"sample_rate\":%d,\"channels\":%d,\"stream_name\":\"%s\","
                "\"stream_description\":\"%s\",\"stream_genre\":\"%s\",\"stream_url\":\"%s\","
                "\"public_stream\":%s,\"add_year_to_metadata\":%s,\"connected\":%s,"
                "\"encoder_ready\":%s,\"encoder_pid\":%ld,\"status\":\"%s\",\"error\":\"%s\","
                "\"connect_count\":%llu,\"disconnect_count\":%llu,\"reconnect_count\":%llu,"
                "\"send_error_count\":%llu,\"consecutive_send_errors\":%llu,\"icecast_stall_count\":%llu,"
                "\"reconnect_backoff_seconds\":%d,\"metadata_pending\":%s,\"metadata_value\":\"%s\","
                "\"queue_id\":%lld,\"slot_token\":\"%s\",\"metadata_error\":\"%s\","
                "\"metadata_requested_count\":%llu,\"metadata_applied_count\":%llu,\"metadata_failed_count\":%llu,"
                "\"metadata_generation\":%llu,\"metadata_applied_generation\":%llu,"
                "\"metadata_not_before_monotonic_ms\":%lld,\"encoded_bytes_total\":%llu,"
                "\"icecast_sent_bytes_total\":%llu,\"discarded_encoded_bytes_total\":%llu,"
                "\"encoded_fifo_bytes\":%zu,\"encoded_fifo_capacity_bytes\":%zu,"
                "\"encoded_fifo_high_water_bytes\":%zu,\"encoded_fifo_overrun_count\":%llu,"
                "\"encoded_fifo_overrun_bytes\":%llu,\"output_gap_count\":%llu,"
                "\"max_output_gap_ms\":%lld,\"last_output_gap_reason\":\"%s\","
                "\"last_encoded_data_monotonic_ms\":%lld,\"last_icecast_send_monotonic_ms\":%lld,"
                "\"last_successful_send_monotonic_ms\":%lld}",
                first_output ? "" : ",", eid, eco, ect, stream->enabled ? "true" : "false", eh, stream->port, em, eu,
                stream->password[0] != '\0' ? "true" : "false", stream->bitrate_kbps, WB_AUDIO_SAMPLE_RATE, WB_AUDIO_CHANNELS, en,
                edesc, egenre, eurl,
                stream->public_stream ? "true" : "false", stream->add_year_to_metadata ? "true" : "false",
                stream->connected ? "true" : "false", stream->encoder_ready ? "true" : "false", (long)encoder_pid, es, ee,
                (unsigned long long)stream->connect_count, (unsigned long long)stream->disconnect_count,
                (unsigned long long)stream->reconnect_count, (unsigned long long)stream->send_error_count,
                (unsigned long long)stream->consecutive_send_errors, (unsigned long long)stream->icecast_stall_count,
                stream->reconnect_backoff_seconds, stream->metadata_pending ? "true" : "false", emeta,
                (long long)stream->current_metadata_queue_id, etoken, emerr,
                (unsigned long long)stream->metadata_requested_count, (unsigned long long)stream->metadata_applied_count,
                (unsigned long long)stream->metadata_failed_count, (unsigned long long)stream->metadata_generation,
                (unsigned long long)stream->metadata_applied_generation, (long long)stream->metadata_not_before_monotonic_ms,
                (unsigned long long)stream->encoded_bytes_total, (unsigned long long)stream->icecast_sent_bytes_total,
                (unsigned long long)stream->discarded_encoded_bytes_total, stream->encoded_fifo_fill, stream->encoded_fifo_capacity,
                stream->encoded_fifo_high_water, (unsigned long long)stream->encoded_fifo_overrun_count,
                (unsigned long long)stream->encoded_fifo_overrun_bytes, (unsigned long long)stream->output_gap_count,
                (long long)stream->max_output_gap_ms, egap, (long long)stream->last_encoded_data_monotonic_ms,
                (long long)stream->last_icecast_send_monotonic_ms, (long long)stream->last_successful_send_monotonic_ms) != 0) return (int)used;
            first_output = false;
        }
    }
    if (append_json_text(output_json, output_size, &used, "]}") != 0) return (int)used;
    return (int)used;
}

