#define _POSIX_C_SOURCE 200809L

#include "libav_bridge.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/audio_fifo.h>
#include <libavutil/avutil.h>
#include <libavutil/channel_layout.h>
#include <libavutil/error.h>
#include <libavutil/log.h>
#include <libavutil/mem.h>
#include <libavutil/opt.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>

#define WB_LIBAV_DECODE_FIFO_DEFAULT (1024U * 1024U)
#define WB_LIBAV_ENCODER_FIFO_BYTES (2U * 1024U * 1024U)
#define WB_LIBAV_ENCODER_READ_BYTES 65536U
#define WB_LIBAV_IO_POLL_MS 100
#define WB_LIBAV_OUTPUT_SAMPLE_RATE WB_AUDIO_SAMPLE_RATE
#define WB_LIBAV_OUTPUT_CHANNELS WB_AUDIO_CHANNELS
#define WB_LIBAV_OUTPUT_FORMAT AV_SAMPLE_FMT_S16
#define WB_LIBAV_MAX_CONSECUTIVE_INVALID_DATA 256U

static pthread_mutex_t g_runtime_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned int g_runtime_users = 0U;

static bool suppress_noisy_mp3_header_log(void *avcl, int level, const char *format) {
    const AVClass *av_class;
    const char *item_name;

    if (avcl == NULL || format == NULL || level > AV_LOG_ERROR) return false;
    if (strstr(format, "Header missing") == NULL) return false;

    av_class = *(const AVClass * const *)avcl;
    if (av_class == NULL || av_class->item_name == NULL) return false;
    item_name = av_class->item_name(avcl);
    return item_name != NULL
        && (strcmp(item_name, "mp3float") == 0 || strcmp(item_name, "mp3") == 0);
}

static bool valid_id3_frame_name(const char *frame_name) {
    size_t length;
    size_t index;

    if (frame_name == NULL) return false;
    length = strlen(frame_name);
    if (length != 3U && length != 4U) return false;
    for (index = 0U; index < length; index++) {
        const unsigned char value = (unsigned char)frame_name[index];
        if (!((value >= (unsigned char)'A' && value <= (unsigned char)'Z')
              || (value >= (unsigned char)'0' && value <= (unsigned char)'9'))) {
            return false;
        }
    }
    return true;
}

static bool suppress_noisy_id3_metadata_log(
    void *avcl,
    int level,
    const char *format,
    va_list arguments
) {
    (void)avcl;

    /* FFmpeg 7.1.5 reports malformed ID3 text/comment metadata at
     * AV_LOG_ERROR even though the parser only drops the bad metadata frame.
     * Match only the exact parser formats. Audio/container failures continue
     * to the default libav callback unchanged. */
    if (format == NULL || level > AV_LOG_ERROR) return false;

    if (strcmp(format, "Cannot read BOM value, input too short\n") == 0
        || strcmp(format, "Incorrect BOM value\n") == 0
        || strcmp(format, "Error reading comment frame, skipped\n") == 0
        || strcmp(format, "Error reading lyrics, skipped\n") == 0) {
        return true;
    }

    if (strcmp(format, "Error reading frame %s, skipped\n") == 0) {
        const char *frame_name;
        va_list copy;

        va_copy(copy, arguments);
        frame_name = va_arg(copy, const char *);
        va_end(copy);
        return valid_id3_frame_name(frame_name);
    }

    return false;
}

static void wb_libav_log_callback(
    void *avcl,
    int level,
    const char *format,
    va_list arguments
) {
    if (suppress_noisy_mp3_header_log(avcl, level, format)) return;
    if (suppress_noisy_id3_metadata_log(avcl, level, format, arguments)) return;
    av_log_default_callback(avcl, level, format, arguments);
}

static void copy_text(char *destination, size_t size, const char *source) {
    size_t length;
    if (destination == NULL || size == 0U) return;
    if (source == NULL) source = "";
    length = strnlen(source, size - 1U);
    if (length > 0U) memcpy(destination, source, length);
    destination[length] = '\0';
}

static void ff_error_text(int code, char *destination, size_t size) {
    char buffer[AV_ERROR_MAX_STRING_SIZE] = "";
    if (destination == NULL || size == 0U) return;
    if (av_strerror(code, buffer, sizeof(buffer)) < 0) {
        (void)snprintf(destination, size, "FFmpeg error %d", code);
    } else {
        copy_text(destination, size, buffer);
    }
}

static bool strip_last_path_component(char *path) {
    char *slash;
    if (path == NULL || path[0] == '\0') return false;
    slash = strrchr(path, '/');
    if (slash == NULL) return false;
    if (slash == path) path[1] = '\0';
    else *slash = '\0';
    return true;
}

static void resolve_linked_library_path(char *destination, size_t destination_size) {
    char executable[WB_PATH_SIZE];
    ssize_t length;
    int written;
    if (destination == NULL || destination_size == 0U) return;
    destination[0] = '\0';
    length = readlink("/proc/self/exe", executable, sizeof(executable) - 1U);
    if (length <= 0 || (size_t)length >= sizeof(executable)) {
        copy_text(destination, destination_size, "linked:libavformat.so.61");
        return;
    }
    executable[length] = '\0';
    if (!strip_last_path_component(executable)
        || !strip_last_path_component(executable)
        || !strip_last_path_component(executable)) {
        copy_text(destination, destination_size, "linked:libavformat.so.61");
        return;
    }
    written = snprintf(
        destination,
        destination_size,
        "%s/lib/libavformat.so.61",
        executable
    );
    if (written < 0 || (size_t)written >= destination_size) {
        copy_text(destination, destination_size, "linked:libavformat.so.61");
    }
}

static bool protocol_available(const char *name, int output) {
    void *opaque = NULL;
    const char *protocol;
    while ((protocol = avio_enum_protocols(&opaque, output)) != NULL) {
        if (strcmp(protocol, name) == 0) return true;
    }
    return false;
}

int wb_libav_runtime_init(WbEngineState *state, char *error, size_t error_size) {
    const AVCodec *mp3_encoder;
    const AVCodec *aac_encoder;
    const AVCodec *mp3_decoder;
    const AVOutputFormat *mp3_muxer;
    const AVOutputFormat *adts_muxer;
    int result = 0;

    if (error != NULL && error_size > 0U) error[0] = '\0';
    (void)pthread_mutex_lock(&g_runtime_lock);
    if (g_runtime_users == 0U) {
        result = avformat_network_init();
        if (result < 0) {
            char detail[128];
            ff_error_text(result, detail, sizeof(detail));
            if (error != NULL && error_size > 0U) {
                (void)snprintf(error, error_size, "libav network initialization failed: %s", detail);
            }
            (void)pthread_mutex_unlock(&g_runtime_lock);
            return -1;
        }
        av_log_set_callback(wb_libav_log_callback);
        av_log_set_level(AV_LOG_ERROR);
    }
    g_runtime_users += 1U;
    (void)pthread_mutex_unlock(&g_runtime_lock);

    mp3_encoder = avcodec_find_encoder_by_name("libmp3lame");
    aac_encoder = avcodec_find_encoder_by_name("libfdk_aac");
    mp3_decoder = avcodec_find_decoder(AV_CODEC_ID_MP3);
    mp3_muxer = av_guess_format("mp3", NULL, NULL);
    adts_muxer = av_guess_format("adts", NULL, NULL);
    if (mp3_encoder == NULL || aac_encoder == NULL || mp3_decoder == NULL
        || mp3_muxer == NULL || adts_muxer == NULL
        || !protocol_available("file", 0)
        || !protocol_available("http", 0)
        || !protocol_available("https", 0)) {
        if (error != NULL && error_size > 0U) {
            copy_text(error, error_size, "linked libav runtime is missing a required decoder, encoder, muxer or input protocol");
        }
        wb_libav_runtime_shutdown();
        return -1;
    }

    if (state != NULL) {
        state->ffmpeg_runtime_valid = true;
        state->ffmpeg_system_fallback_used = false;
        state->ffmpeg_runtime_error[0] = '\0';
        copy_text(state->ffmpeg_source, sizeof(state->ffmpeg_source), "linked_libav");
        copy_text(state->ffmpeg_version, sizeof(state->ffmpeg_version), av_version_info());
        copy_text(
            state->ffmpeg_runtime_build,
            sizeof(state->ffmpeg_runtime_build),
            WB_BUNDLED_FFMPEG_RUNTIME_ID
        );
        resolve_linked_library_path(state->ffmpeg_path, sizeof(state->ffmpeg_path));
    }
    return 0;
}

void wb_libav_runtime_shutdown(void) {
    (void)pthread_mutex_lock(&g_runtime_lock);
    if (g_runtime_users > 0U) {
        g_runtime_users -= 1U;
        if (g_runtime_users == 0U) avformat_network_deinit();
    }
    (void)pthread_mutex_unlock(&g_runtime_lock);
}

struct WbLibavDecodeSession {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    pthread_t thread;
    bool thread_created;
    atomic_bool abort_requested;
    bool eof;
    bool failed;
    char error[WB_AUDIO_ERROR_SIZE];
    WbLibavDecodeConfig config;
    char path[WB_PATH_SIZE];
    unsigned char *fifo;
    size_t fifo_capacity;
    size_t fifo_read_pos;
    size_t fifo_write_pos;
    size_t fifo_fill;
    atomic_uint_fast64_t invalid_data_skip_count;
    atomic_uint consecutive_invalid_data_count;
};

static bool decode_skip_invalid_data(
    WbLibavDecodeSession *session,
    int result,
    const char *stage,
    char *error,
    size_t error_size
) {
    if (session == NULL || result != AVERROR_INVALIDDATA) return false;
    (void)atomic_fetch_add_explicit(
        &session->invalid_data_skip_count, 1U, memory_order_relaxed
    );
    unsigned int consecutive = atomic_fetch_add_explicit(
        &session->consecutive_invalid_data_count, 1U, memory_order_relaxed
    ) + 1U;
    if (consecutive <= WB_LIBAV_MAX_CONSECUTIVE_INVALID_DATA) {
        return true;
    }
    (void)snprintf(
        error,
        error_size,
        "libav %s exceeded corrupt-input recovery limit (%u consecutive invalid-data errors)",
        stage == NULL ? "decoder" : stage,
        consecutive
    );
    return false;
}

static void decode_note_valid_frame(WbLibavDecodeSession *session) {
    if (session != NULL) {
        atomic_store_explicit(
            &session->consecutive_invalid_data_count, 0U, memory_order_relaxed
        );
    }
}

static size_t byte_fifo_write(
    unsigned char *fifo,
    size_t capacity,
    size_t *write_pos,
    size_t *fill,
    const unsigned char *data,
    size_t length
) {
    size_t available = capacity - *fill;
    size_t accepted = length < available ? length : available;
    size_t first;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*write_pos + first > capacity) first = capacity - *write_pos;
    memcpy(fifo + *write_pos, data, first);
    if (accepted > first) memcpy(fifo, data + first, accepted - first);
    *write_pos = (*write_pos + accepted) % capacity;
    *fill += accepted;
    return accepted;
}

static size_t byte_fifo_read(
    unsigned char *fifo,
    size_t capacity,
    size_t *read_pos,
    size_t *fill,
    unsigned char *data,
    size_t length
) {
    size_t available = *fill;
    size_t accepted = length < available ? length : available;
    size_t first;
    if (accepted == 0U) return 0U;
    first = accepted;
    if (*read_pos + first > capacity) first = capacity - *read_pos;
    memcpy(data, fifo + *read_pos, first);
    if (accepted > first) memcpy(data + first, fifo, accepted - first);
    *read_pos = (*read_pos + accepted) % capacity;
    *fill -= accepted;
    return accepted;
}

static int decode_interrupt_callback(void *opaque) {
    WbLibavDecodeSession *session = opaque;
    return session != NULL && atomic_load_explicit(&session->abort_requested, memory_order_relaxed);
}

static bool decode_test_seek_delay(WbLibavDecodeSession *session) {
    const char *value;
    char *end = NULL;
    long delay_ms;
    long elapsed_ms = 0;
    if (session == NULL || session->config.start_ms <= 0) return true;
    value = getenv("WEB_BROADCASTER_LIBAV_TEST_SEEK_DELAY_MS");
    if (value == NULL || value[0] == '\0') return true;
    errno = 0;
    delay_ms = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || delay_ms <= 0 || delay_ms > 30000) {
        return true;
    }
    while (elapsed_ms < delay_ms) {
        long step_ms = delay_ms - elapsed_ms;
        struct timespec delay;
        if (step_ms > 20) step_ms = 20;
        if (atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) return false;
        delay.tv_sec = step_ms / 1000;
        delay.tv_nsec = (step_ms % 1000) * 1000000L;
        while (nanosleep(&delay, &delay) != 0 && errno == EINTR) {
            if (atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) return false;
        }
        elapsed_ms += step_ms;
    }
    return !atomic_load_explicit(&session->abort_requested, memory_order_relaxed);
}

static int decode_fifo_push(
    WbLibavDecodeSession *session,
    const unsigned char *data,
    size_t length
) {
    size_t offset = 0U;
    while (offset < length) {
        size_t accepted;
        (void)pthread_mutex_lock(&session->lock);
        while (!atomic_load_explicit(&session->abort_requested, memory_order_relaxed)
            && session->fifo_fill >= session->fifo_capacity) {
            (void)pthread_cond_wait(&session->cond, &session->lock);
        }
        if (atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) {
            (void)pthread_mutex_unlock(&session->lock);
            return -1;
        }
        accepted = byte_fifo_write(
            session->fifo,
            session->fifo_capacity,
            &session->fifo_write_pos,
            &session->fifo_fill,
            data + offset,
            length - offset
        );
        (void)pthread_cond_broadcast(&session->cond);
        (void)pthread_mutex_unlock(&session->lock);
        if (accepted == 0U) continue;
        offset += accepted;
    }
    return 0;
}

static int64_t frame_source_start_ms(const AVStream *stream, const AVFrame *frame) {
    int64_t timestamp;
    int64_t start_time;
    if (stream == NULL || frame == NULL) return AV_NOPTS_VALUE;
    timestamp = frame->best_effort_timestamp;
    if (timestamp == AV_NOPTS_VALUE) return AV_NOPTS_VALUE;
    start_time = stream->start_time == AV_NOPTS_VALUE ? 0 : stream->start_time;
    return av_rescale_q(timestamp - start_time, stream->time_base, (AVRational){1, 1000});
}

static int decode_write_frame(
    WbLibavDecodeSession *session,
    SwrContext *resampler,
    AVStream *stream,
    AVFrame *frame,
    int64_t start_ms,
    bool *seek_complete,
    int64_t *samples_written,
    int64_t max_samples,
    char *error,
    size_t error_size
) {
    uint8_t *output = NULL;
    int output_linesize = 0;
    int maximum_samples;
    int converted;
    int64_t frame_start_ms;
    int skip_samples = 0;
    int accepted_samples;
    int result;

    maximum_samples = swr_get_out_samples(resampler, frame->nb_samples);
    if (maximum_samples <= 0) maximum_samples = frame->nb_samples + 64;
    result = av_samples_alloc(
        &output,
        &output_linesize,
        WB_LIBAV_OUTPUT_CHANNELS,
        maximum_samples,
        WB_LIBAV_OUTPUT_FORMAT,
        0
    );
    if (result < 0) {
        ff_error_text(result, error, error_size);
        return -1;
    }
    converted = swr_convert(
        resampler,
        &output,
        maximum_samples,
        (const uint8_t * const *)frame->extended_data,
        frame->nb_samples
    );
    if (converted < 0) {
        ff_error_text(converted, error, error_size);
        av_freep(&output);
        return -1;
    }

    if (!*seek_complete && start_ms > 0) {
        frame_start_ms = frame_source_start_ms(stream, frame);
        if (frame_start_ms != AV_NOPTS_VALUE) {
            int64_t frame_end_ms = frame_start_ms
                + ((int64_t)converted * 1000LL) / WB_LIBAV_OUTPUT_SAMPLE_RATE;
            if (frame_end_ms <= start_ms) {
                av_freep(&output);
                return 0;
            }
            if (frame_start_ms < start_ms) {
                int64_t skip_ms = start_ms - frame_start_ms;
                skip_samples = (int)((skip_ms * WB_LIBAV_OUTPUT_SAMPLE_RATE) / 1000LL);
                if (skip_samples > converted) skip_samples = converted;
            }
        }
        *seek_complete = true;
    }

    accepted_samples = converted - skip_samples;
    if (max_samples > 0 && *samples_written + accepted_samples > max_samples) {
        accepted_samples = (int)(max_samples - *samples_written);
    }
    if (accepted_samples > 0) {
        const unsigned char *start = output
            + (size_t)skip_samples * WB_AUDIO_FRAME_BYTES;
        size_t bytes = (size_t)accepted_samples * WB_AUDIO_FRAME_BYTES;
        if (decode_fifo_push(session, start, bytes) != 0) {
            av_freep(&output);
            return 1;
        }
        *samples_written += accepted_samples;
    }
    av_freep(&output);
    return max_samples > 0 && *samples_written >= max_samples ? 2 : 0;
}

static int decode_drain_frames(
    WbLibavDecodeSession *session,
    AVCodecContext *decoder,
    SwrContext *resampler,
    AVStream *stream,
    AVFrame *frame,
    int64_t start_ms,
    bool *seek_complete,
    int64_t *samples_written,
    int64_t max_samples,
    char *error,
    size_t error_size
) {
    for (;;) {
        int result = avcodec_receive_frame(decoder, frame);
        int write_result;
        if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) return 0;
        if (result < 0) {
            if (decode_skip_invalid_data(
                    session, result, "frame receive", error, error_size
                )) {
                av_frame_unref(frame);
                return 0;
            }
            if (error != NULL && error[0] != '\0') return -1;
            ff_error_text(result, error, error_size);
            return -1;
        }
        decode_note_valid_frame(session);
        write_result = decode_write_frame(
            session,
            resampler,
            stream,
            frame,
            start_ms,
            seek_complete,
            samples_written,
            max_samples,
            error,
            error_size
        );
        av_frame_unref(frame);
        if (write_result != 0) return write_result;
    }
}

static void decode_session_fail(WbLibavDecodeSession *session, const char *message) {
    (void)pthread_mutex_lock(&session->lock);
    session->failed = true;
    copy_text(session->error, sizeof(session->error), message);
    (void)pthread_cond_broadcast(&session->cond);
    (void)pthread_mutex_unlock(&session->lock);
}

static void *decode_thread_main(void *context) {
    WbLibavDecodeSession *session = context;
    AVFormatContext *format = NULL;
    AVCodecContext *decoder = NULL;
    SwrContext *resampler = NULL;
    AVPacket *packet = NULL;
    AVFrame *frame = NULL;
    AVStream *stream = NULL;
    const AVCodec *codec;
    AVDictionary *options = NULL;
    AVChannelLayout output_layout;
    int audio_index;
    int result;
    bool seek_complete;
    int64_t samples_written = 0;
    int64_t max_samples = session->config.duration_ms > 0
        ? (session->config.duration_ms * WB_LIBAV_OUTPUT_SAMPLE_RATE) / 1000LL
        : 0;
    char error[WB_AUDIO_ERROR_SIZE] = "";

    if (!decode_test_seek_delay(session)) return NULL;

    format = avformat_alloc_context();
    if (format == NULL) {
        decode_session_fail(session, "cannot allocate libav input context");
        return NULL;
    }
    format->interrupt_callback.callback = decode_interrupt_callback;
    format->interrupt_callback.opaque = session;
    format->error_recognition = AV_EF_IGNORE_ERR;
    if (session->config.stream_source) {
        av_dict_set(&options, "reconnect", "1", 0);
        av_dict_set(&options, "reconnect_streamed", "1", 0);
        av_dict_set(&options, "reconnect_at_eof", session->config.stream_infinite ? "1" : "0", 0);
        av_dict_set(&options, "reconnect_delay_max", "5", 0);
        av_dict_set(&options, "rw_timeout", "15000000", 0);
    }
    result = avformat_open_input(&format, session->path, NULL, &options);
    av_dict_free(&options);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, sizeof(error), "libav input open failed: %s", detail);
        goto failed;
    }
    result = avformat_find_stream_info(format, NULL);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, sizeof(error), "libav stream discovery failed: %s", detail);
        goto failed;
    }
    audio_index = av_find_best_stream(format, AVMEDIA_TYPE_AUDIO, -1, -1, &codec, 0);
    if (audio_index < 0 || codec == NULL) {
        copy_text(error, sizeof(error), "libav found no decodable audio stream");
        goto failed;
    }
    stream = format->streams[audio_index];
    decoder = avcodec_alloc_context3(codec);
    if (decoder == NULL) {
        copy_text(error, sizeof(error), "cannot allocate libav decoder");
        goto failed;
    }
    decoder->err_recognition = AV_EF_IGNORE_ERR;
    result = avcodec_parameters_to_context(decoder, stream->codecpar);
    if (result < 0) {
        ff_error_text(result, error, sizeof(error));
        goto failed;
    }
    result = avcodec_open2(decoder, codec, NULL);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, sizeof(error), "libav decoder open failed: %s", detail);
        goto failed;
    }

    av_channel_layout_default(&output_layout, WB_LIBAV_OUTPUT_CHANNELS);
    result = swr_alloc_set_opts2(
        &resampler,
        &output_layout,
        WB_LIBAV_OUTPUT_FORMAT,
        WB_LIBAV_OUTPUT_SAMPLE_RATE,
        &decoder->ch_layout,
        decoder->sample_fmt,
        decoder->sample_rate,
        0,
        NULL
    );
    av_channel_layout_uninit(&output_layout);
    if (result < 0 || resampler == NULL) {
        ff_error_text(result < 0 ? result : AVERROR(ENOMEM), error, sizeof(error));
        goto failed;
    }
    result = swr_init(resampler);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, sizeof(error), "libav resampler initialization failed: %s", detail);
        goto failed;
    }

    packet = av_packet_alloc();
    frame = av_frame_alloc();
    if (packet == NULL || frame == NULL) {
        copy_text(error, sizeof(error), "cannot allocate libav decode packet/frame");
        goto failed;
    }

    seek_complete = session->config.start_ms <= 0;
    if (session->config.start_ms > 0 && !session->config.stream_source) {
        int64_t stream_start = stream->start_time == AV_NOPTS_VALUE ? 0 : stream->start_time;
        int64_t target = stream_start + av_rescale_q(
            session->config.start_ms,
            (AVRational){1, 1000},
            stream->time_base
        );
        result = avformat_seek_file(format, audio_index, INT64_MIN, target, target, 0);
        if (result < 0) {
            result = av_seek_frame(format, audio_index, target, AVSEEK_FLAG_BACKWARD);
        }
        if (result >= 0) avcodec_flush_buffers(decoder);
        else seek_complete = true;
    }

    while (!atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) {
        result = av_read_frame(format, packet);
        if (result == AVERROR_EOF) break;
        if (result == AVERROR(EAGAIN)) continue;
        if (result < 0) {
            if (decode_skip_invalid_data(
                    session, result, "input read", error, sizeof(error)
                )) {
                av_packet_unref(packet);
                continue;
            }
            if (error[0] != '\0') goto failed;
            char detail[128];
            ff_error_text(result, detail, sizeof(detail));
            (void)snprintf(error, sizeof(error), "libav input read failed: %s", detail);
            goto failed;
        }
        if (packet->stream_index == audio_index) {
            int send_result = avcodec_send_packet(decoder, packet);
            if (send_result < 0 && send_result != AVERROR(EAGAIN)) {
                if (decode_skip_invalid_data(
                        session, send_result, "packet submit", error, sizeof(error)
                    )) {
                    av_packet_unref(packet);
                    continue;
                }
                if (error[0] != '\0') {
                    av_packet_unref(packet);
                    goto failed;
                }
                ff_error_text(send_result, error, sizeof(error));
                av_packet_unref(packet);
                goto failed;
            }
            result = decode_drain_frames(
                session,
                decoder,
                resampler,
                stream,
                frame,
                session->config.start_ms,
                &seek_complete,
                &samples_written,
                max_samples,
                error,
                sizeof(error)
            );
            if (result < 0) {
                av_packet_unref(packet);
                goto failed;
            }
            if (result > 0) {
                av_packet_unref(packet);
                goto finished;
            }
        }
        av_packet_unref(packet);
    }
    if (!atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) {
        (void)avcodec_send_packet(decoder, NULL);
        result = decode_drain_frames(
            session,
            decoder,
            resampler,
            stream,
            frame,
            session->config.start_ms,
            &seek_complete,
            &samples_written,
            max_samples,
            error,
            sizeof(error)
        );
        if (result < 0) goto failed;
    }

finished:
    (void)pthread_mutex_lock(&session->lock);
    session->eof = true;
    (void)pthread_cond_broadcast(&session->cond);
    (void)pthread_mutex_unlock(&session->lock);
    av_frame_free(&frame);
    av_packet_free(&packet);
    swr_free(&resampler);
    avcodec_free_context(&decoder);
    avformat_close_input(&format);
    return NULL;

failed:
    if (!atomic_load_explicit(&session->abort_requested, memory_order_relaxed)) {
        decode_session_fail(session, error[0] == '\0' ? "libav decoder failed" : error);
    }
    av_frame_free(&frame);
    av_packet_free(&packet);
    swr_free(&resampler);
    avcodec_free_context(&decoder);
    avformat_close_input(&format);
    return NULL;
}

int wb_libav_decode_start(
    WbLibavDecodeSession **session_out,
    const WbLibavDecodeConfig *config,
    char *error,
    size_t error_size
) {
    WbLibavDecodeSession *session;
    size_t capacity;
    if (session_out == NULL || config == NULL || config->path == NULL || config->path[0] == '\0') {
        copy_text(error, error_size, "invalid libav decode configuration");
        return -1;
    }
    *session_out = NULL;
    session = calloc(1U, sizeof(*session));
    if (session == NULL) {
        copy_text(error, error_size, "cannot allocate libav decode session");
        return -1;
    }
    capacity = config->fifo_capacity > 0U ? config->fifo_capacity : WB_LIBAV_DECODE_FIFO_DEFAULT;
    capacity -= capacity % WB_AUDIO_FRAME_BYTES;
    if (capacity < WB_AUDIO_FRAME_BYTES * 1024U) capacity = WB_AUDIO_FRAME_BYTES * 1024U;
    session->fifo = malloc(capacity);
    if (session->fifo == NULL) {
        free(session);
        copy_text(error, error_size, "cannot allocate libav decode FIFO");
        return -1;
    }
    session->fifo_capacity = capacity;
    session->config = *config;
    copy_text(session->path, sizeof(session->path), config->path);
    session->config.path = session->path;
    atomic_init(&session->abort_requested, false);
    atomic_init(&session->invalid_data_skip_count, 0U);
    atomic_init(&session->consecutive_invalid_data_count, 0U);
    if (pthread_mutex_init(&session->lock, NULL) != 0
        || pthread_cond_init(&session->cond, NULL) != 0) {
        free(session->fifo);
        free(session);
        copy_text(error, error_size, "cannot initialize libav decode synchronization");
        return -1;
    }
    if (pthread_create(&session->thread, NULL, decode_thread_main, session) != 0) {
        (void)pthread_cond_destroy(&session->cond);
        (void)pthread_mutex_destroy(&session->lock);
        free(session->fifo);
        free(session);
        copy_text(error, error_size, "cannot start libav decode thread");
        return -1;
    }
    session->thread_created = true;
    *session_out = session;
    if (error != NULL && error_size > 0U) error[0] = '\0';
    return 0;
}

ssize_t wb_libav_decode_read(
    WbLibavDecodeSession *session,
    unsigned char *buffer,
    size_t buffer_size
) {
    size_t received;
    bool eof;
    bool failed;
    if (session == NULL || buffer == NULL || buffer_size == 0U) return -1;
    (void)pthread_mutex_lock(&session->lock);
    received = byte_fifo_read(
        session->fifo,
        session->fifo_capacity,
        &session->fifo_read_pos,
        &session->fifo_fill,
        buffer,
        buffer_size
    );
    eof = session->eof;
    failed = session->failed;
    if (received > 0U) (void)pthread_cond_broadcast(&session->cond);
    (void)pthread_mutex_unlock(&session->lock);
    if (received > 0U) return (ssize_t)received;
    if (failed) return -1;
    if (eof) return -2;
    return 0;
}

void wb_libav_decode_abort(WbLibavDecodeSession *session) {
    if (session == NULL) return;
    atomic_store_explicit(&session->abort_requested, true, memory_order_relaxed);
    (void)pthread_mutex_lock(&session->lock);
    (void)pthread_cond_broadcast(&session->cond);
    (void)pthread_mutex_unlock(&session->lock);
}

bool wb_libav_decode_finished(WbLibavDecodeSession *session) {
    bool finished;
    if (session == NULL) return true;
    (void)pthread_mutex_lock(&session->lock);
    finished = session->eof || session->failed;
    (void)pthread_mutex_unlock(&session->lock);
    return finished;
}

uint64_t wb_libav_decode_invalid_data_skip_count(WbLibavDecodeSession *session) {
    if (session == NULL) return 0U;
    return atomic_load_explicit(
        &session->invalid_data_skip_count, memory_order_relaxed
    );
}

void wb_libav_decode_error(
    WbLibavDecodeSession *session,
    char *error,
    size_t error_size
) {
    if (error == NULL || error_size == 0U) return;
    error[0] = '\0';
    if (session == NULL) return;
    (void)pthread_mutex_lock(&session->lock);
    copy_text(error, error_size, session->error);
    (void)pthread_mutex_unlock(&session->lock);
}

void wb_libav_decode_destroy(WbLibavDecodeSession *session) {
    if (session == NULL) return;
    wb_libav_decode_abort(session);
    if (session->thread_created) {
        (void)pthread_join(session->thread, NULL);
        session->thread_created = false;
    }
    (void)pthread_cond_destroy(&session->cond);
    (void)pthread_mutex_destroy(&session->lock);
    free(session->fifo);
    free(session);
}

typedef struct WbLibavEncoderBranch WbLibavEncoderBranch;

struct WbLibavEncoderGroup {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    pthread_t thread;
    bool thread_created;
    atomic_bool stop_requested;
    bool failed;
    char error[WB_ICECAST_ERROR_SIZE];
    unsigned char *pcm_fifo;
    size_t pcm_fifo_capacity;
    size_t pcm_fifo_read_pos;
    size_t pcm_fifo_write_pos;
    size_t pcm_fifo_fill;
    int source_fd;
    WbLibavEncodedSink sink;
    void *sink_context;
    WbLibavEncoderBranch *branches;
    size_t branch_capacity;
    size_t branch_count;
};

struct WbLibavEncoderBranch {
    WbLibavEncoderGroup *group;
    bool active;
    size_t stream_index;
    char codec_name[WB_NATIVE_OUTPUT_CODEC_SIZE];
    int bitrate_kbps;
    AVCodecContext *codec;
    AVFormatContext *format;
    AVStream *stream;
    AVIOContext *avio;
    unsigned char *avio_buffer;
    SwrContext *resampler;
    AVAudioFifo *fifo;
    int64_t next_pts;
};

static int encoder_write_callback(void *opaque, const uint8_t *buffer, int buffer_size) {
    WbLibavEncoderBranch *branch = opaque;
    if (branch == NULL || branch->group == NULL || buffer == NULL || buffer_size < 0) return AVERROR(EINVAL);
    if (atomic_load_explicit(&branch->group->stop_requested, memory_order_relaxed)) return AVERROR_EXIT;
    if (buffer_size > 0 && branch->group->sink != NULL) {
        branch->group->sink(
            branch->group->sink_context,
            branch->stream_index,
            buffer,
            (size_t)buffer_size
        );
    }
    return buffer_size;
}

static void encoder_group_fail(WbLibavEncoderGroup *group, const char *message) {
    (void)pthread_mutex_lock(&group->lock);
    if (!group->failed) {
        group->failed = true;
        copy_text(group->error, sizeof(group->error), message);
    }
    (void)pthread_cond_broadcast(&group->cond);
    (void)pthread_mutex_unlock(&group->lock);
}

static int choose_sample_rate(const AVCodec *codec, int requested) {
    const int *rates = NULL;
    int rate_count = 0;
    int best = 0;
    int best_delta = 0x7fffffff;
    if (
        codec == NULL ||
        avcodec_get_supported_config(
            NULL,
            codec,
            AV_CODEC_CONFIG_SAMPLE_RATE,
            0,
            (const void **)&rates,
            &rate_count
        ) < 0 ||
        rates == NULL || rate_count <= 0
    ) {
        return requested;
    }
    for (int index = 0; index < rate_count; index += 1) {
        int rate = rates[index];
        int delta = rate > requested ? rate - requested : requested - rate;
        if (delta < best_delta) {
            best = rate;
            best_delta = delta;
        }
        if (rate == requested) return requested;
    }
    return best > 0 ? best : requested;
}

static enum AVSampleFormat choose_sample_format(const AVCodec *codec) {
    const enum AVSampleFormat *formats = NULL;
    int format_count = 0;
    if (
        codec == NULL ||
        avcodec_get_supported_config(
            NULL,
            codec,
            AV_CODEC_CONFIG_SAMPLE_FORMAT,
            0,
            (const void **)&formats,
            &format_count
        ) < 0 ||
        formats == NULL || format_count <= 0
    ) {
        return AV_SAMPLE_FMT_FLTP;
    }
    for (int index = 0; index < format_count; index += 1) {
        if (formats[index] == AV_SAMPLE_FMT_S16) return formats[index];
    }
    return formats[0];
}

static int encoder_branch_open(
    WbLibavEncoderGroup *group,
    WbLibavEncoderBranch *branch,
    const WbLibavEncoderConfig *config,
    char *error,
    size_t error_size
) {
    const AVCodec *codec;
    const char *codec_name;
    const char *format_name;
    AVChannelLayout stereo;
    AVDictionary *codec_options = NULL;
    AVDictionary *mux_options = NULL;
    int result;

    memset(branch, 0, sizeof(*branch));
    branch->group = group;
    branch->stream_index = config->stream_index;
    branch->bitrate_kbps = config->bitrate_kbps;
    copy_text(branch->codec_name, sizeof(branch->codec_name), config->codec);
    if (strcmp(config->codec, "aac_he_v2") == 0) {
        codec_name = "libfdk_aac";
        format_name = "adts";
    } else {
        codec_name = "libmp3lame";
        format_name = "mp3";
    }
    codec = avcodec_find_encoder_by_name(codec_name);
    if (codec == NULL) {
        (void)snprintf(error, error_size, "required encoder is unavailable: %s", codec_name);
        return -1;
    }
    result = avformat_alloc_output_context2(&branch->format, NULL, format_name, NULL);
    if (result < 0 || branch->format == NULL) {
        ff_error_text(result < 0 ? result : AVERROR(ENOMEM), error, error_size);
        return -1;
    }
    branch->stream = avformat_new_stream(branch->format, NULL);
    branch->codec = avcodec_alloc_context3(codec);
    if (branch->stream == NULL || branch->codec == NULL) {
        copy_text(error, error_size, "cannot allocate libav encoder stream/context");
        return -1;
    }
    branch->codec->sample_rate = choose_sample_rate(codec, WB_LIBAV_OUTPUT_SAMPLE_RATE);
    branch->codec->sample_fmt = choose_sample_format(codec);
    branch->codec->bit_rate = (int64_t)config->bitrate_kbps * 1000LL;
    branch->codec->time_base = (AVRational){1, branch->codec->sample_rate};
    av_channel_layout_default(&branch->codec->ch_layout, WB_LIBAV_OUTPUT_CHANNELS);
    if (strcmp(config->codec, "aac_he_v2") == 0) {
        branch->codec->profile = FF_PROFILE_AAC_HE_V2;
        av_dict_set(&codec_options, "afterburner", "1", 0);
    }
    if ((branch->format->oformat->flags & AVFMT_GLOBALHEADER) != 0) {
        branch->codec->flags |= AV_CODEC_FLAG_GLOBAL_HEADER;
    }
    result = avcodec_open2(branch->codec, codec, &codec_options);
    av_dict_free(&codec_options);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, error_size, "libav encoder open failed for %s: %s", codec_name, detail);
        return -1;
    }
    branch->stream->time_base = branch->codec->time_base;
    result = avcodec_parameters_from_context(branch->stream->codecpar, branch->codec);
    if (result < 0) {
        ff_error_text(result, error, error_size);
        return -1;
    }

    branch->avio_buffer = av_malloc(32768U);
    if (branch->avio_buffer == NULL) {
        copy_text(error, error_size, "cannot allocate libav encoder AVIO buffer");
        return -1;
    }
    branch->avio = avio_alloc_context(
        branch->avio_buffer,
        32768,
        1,
        branch,
        NULL,
        encoder_write_callback,
        NULL
    );
    if (branch->avio == NULL) {
        copy_text(error, error_size, "cannot allocate libav encoder AVIO context");
        return -1;
    }
    branch->format->pb = branch->avio;
    branch->format->flags |= AVFMT_FLAG_CUSTOM_IO;
    if (strcmp(config->codec, "aac_he_v2") != 0) {
        av_dict_set(&mux_options, "write_xing", "0", 0);
        av_dict_set(&mux_options, "id3v2_version", "0", 0);
    }
    result = avformat_write_header(branch->format, &mux_options);
    av_dict_free(&mux_options);
    if (result < 0) {
        char detail[128];
        ff_error_text(result, detail, sizeof(detail));
        (void)snprintf(error, error_size, "libav muxer header failed for %s: %s", format_name, detail);
        return -1;
    }

    av_channel_layout_default(&stereo, WB_LIBAV_OUTPUT_CHANNELS);
    result = swr_alloc_set_opts2(
        &branch->resampler,
        &branch->codec->ch_layout,
        branch->codec->sample_fmt,
        branch->codec->sample_rate,
        &stereo,
        WB_LIBAV_OUTPUT_FORMAT,
        WB_LIBAV_OUTPUT_SAMPLE_RATE,
        0,
        NULL
    );
    av_channel_layout_uninit(&stereo);
    if (result < 0 || branch->resampler == NULL) {
        ff_error_text(result < 0 ? result : AVERROR(ENOMEM), error, error_size);
        return -1;
    }
    result = swr_init(branch->resampler);
    if (result < 0) {
        ff_error_text(result, error, error_size);
        return -1;
    }
    branch->fifo = av_audio_fifo_alloc(
        branch->codec->sample_fmt,
        branch->codec->ch_layout.nb_channels,
        branch->codec->frame_size > 0 ? branch->codec->frame_size * 4 : 8192
    );
    if (branch->fifo == NULL) {
        copy_text(error, error_size, "cannot allocate libav encoder sample FIFO");
        return -1;
    }
    return 0;
}

static void encoder_branch_close(WbLibavEncoderBranch *branch) {
    if (branch == NULL) return;
    if (branch->format != NULL && branch->format->pb != NULL) {
        (void)av_write_trailer(branch->format);
    }
    av_audio_fifo_free(branch->fifo);
    branch->fifo = NULL;
    swr_free(&branch->resampler);
    avcodec_free_context(&branch->codec);
    if (branch->avio != NULL) {
        av_freep(&branch->avio->buffer);
        avio_context_free(&branch->avio);
        branch->avio_buffer = NULL;
    } else {
        av_freep(&branch->avio_buffer);
    }
    if (branch->format != NULL) {
        branch->format->pb = NULL;
        avformat_free_context(branch->format);
        branch->format = NULL;
    }
    branch->active = false;
    branch->group = NULL;
}

static int encoder_branch_write_packets(
    WbLibavEncoderBranch *branch,
    AVFrame *frame,
    char *error,
    size_t error_size
) {
    AVPacket *packet;
    int result;
    packet = av_packet_alloc();
    if (packet == NULL) {
        copy_text(error, error_size, "cannot allocate libav encoded packet");
        return -1;
    }
    result = avcodec_send_frame(branch->codec, frame);
    if (result < 0) {
        ff_error_text(result, error, error_size);
        av_packet_free(&packet);
        return -1;
    }
    for (;;) {
        result = avcodec_receive_packet(branch->codec, packet);
        if (result == AVERROR(EAGAIN) || result == AVERROR_EOF) break;
        if (result < 0) {
            ff_error_text(result, error, error_size);
            av_packet_free(&packet);
            return -1;
        }
        av_packet_rescale_ts(packet, branch->codec->time_base, branch->stream->time_base);
        packet->stream_index = branch->stream->index;
        result = av_interleaved_write_frame(branch->format, packet);
        av_packet_unref(packet);
        if (result < 0) {
            ff_error_text(result, error, error_size);
            av_packet_free(&packet);
            return -1;
        }
    }
    av_packet_free(&packet);
    return 0;
}

static int encoder_branch_drain_fifo(
    WbLibavEncoderBranch *branch,
    bool flush,
    char *error,
    size_t error_size
) {
    int frame_size = branch->codec->frame_size > 0 ? branch->codec->frame_size : 1024;
    while (av_audio_fifo_size(branch->fifo) >= frame_size
        || (flush && av_audio_fifo_size(branch->fifo) > 0)) {
        int available = av_audio_fifo_size(branch->fifo);
        int sample_count = available >= frame_size ? frame_size : available;
        AVFrame *frame = av_frame_alloc();
        int result;
        if (frame == NULL) {
            copy_text(error, error_size, "cannot allocate libav encoder frame");
            return -1;
        }
        frame->nb_samples = frame_size;
        frame->format = branch->codec->sample_fmt;
        frame->sample_rate = branch->codec->sample_rate;
        if (av_channel_layout_copy(&frame->ch_layout, &branch->codec->ch_layout) < 0) {
            av_frame_free(&frame);
            copy_text(error, error_size, "cannot copy libav encoder channel layout");
            return -1;
        }
        result = av_frame_get_buffer(frame, 0);
        if (result < 0) {
            ff_error_text(result, error, error_size);
            av_frame_free(&frame);
            return -1;
        }
        result = av_frame_make_writable(frame);
        if (result < 0) {
            ff_error_text(result, error, error_size);
            av_frame_free(&frame);
            return -1;
        }
        if (sample_count > 0) {
            result = av_audio_fifo_read(branch->fifo, (void **)frame->data, sample_count);
            if (result < sample_count) {
                av_frame_free(&frame);
                copy_text(error, error_size, "libav encoder FIFO read failed");
                return -1;
            }
        }
        if (sample_count < frame_size) {
            av_samples_set_silence(
                frame->data,
                sample_count,
                frame_size - sample_count,
                branch->codec->ch_layout.nb_channels,
                branch->codec->sample_fmt
            );
        }
        frame->pts = branch->next_pts;
        branch->next_pts += frame_size;
        result = encoder_branch_write_packets(branch, frame, error, error_size);
        av_frame_free(&frame);
        if (result != 0) return -1;
    }
    if (flush) return encoder_branch_write_packets(branch, NULL, error, error_size);
    return 0;
}

static int encoder_branch_push_pcm(
    WbLibavEncoderBranch *branch,
    const unsigned char *pcm,
    size_t length,
    char *error,
    size_t error_size
) {
    int input_samples = (int)(length / WB_AUDIO_FRAME_BYTES);
    int maximum_samples;
    uint8_t **converted = NULL;
    int linesize = 0;
    const uint8_t *input_data[1];
    int converted_samples;
    int result;
    if (input_samples <= 0) return 0;
    maximum_samples = av_rescale_rnd(
        swr_get_delay(branch->resampler, WB_LIBAV_OUTPUT_SAMPLE_RATE) + input_samples,
        branch->codec->sample_rate,
        WB_LIBAV_OUTPUT_SAMPLE_RATE,
        AV_ROUND_UP
    );
    if (maximum_samples <= 0) maximum_samples = input_samples + 64;
    result = av_samples_alloc_array_and_samples(
        &converted,
        &linesize,
        branch->codec->ch_layout.nb_channels,
        maximum_samples,
        branch->codec->sample_fmt,
        0
    );
    if (result < 0) {
        ff_error_text(result, error, error_size);
        return -1;
    }
    input_data[0] = pcm;
    converted_samples = swr_convert(
        branch->resampler,
        converted,
        maximum_samples,
        input_data,
        input_samples
    );
    if (converted_samples < 0) {
        ff_error_text(converted_samples, error, error_size);
        av_freep(&converted[0]);
        av_freep(&converted);
        return -1;
    }
    result = av_audio_fifo_realloc(
        branch->fifo,
        av_audio_fifo_size(branch->fifo) + converted_samples
    );
    if (result < 0) {
        ff_error_text(result, error, error_size);
        av_freep(&converted[0]);
        av_freep(&converted);
        return -1;
    }
    result = av_audio_fifo_write(branch->fifo, (void **)converted, converted_samples);
    av_freep(&converted[0]);
    av_freep(&converted);
    if (result < converted_samples) {
        copy_text(error, error_size, "libav encoder FIFO write failed");
        return -1;
    }
    return encoder_branch_drain_fifo(branch, false, error, error_size);
}

static int encoder_group_process_pcm(
    WbLibavEncoderGroup *group,
    const unsigned char *data,
    size_t length,
    char *error,
    size_t error_size
) {
    size_t aligned = length - (length % WB_AUDIO_FRAME_BYTES);
    size_t index;
    int result = 0;
    if (aligned == 0U) return 0;
    (void)pthread_mutex_lock(&group->lock);
    for (index = 0U; index < group->branch_capacity; index += 1U) {
        WbLibavEncoderBranch *branch = &group->branches[index];
        if (!branch->active) continue;
        if (encoder_branch_push_pcm(branch, data, aligned, error, error_size) != 0) {
            result = -1;
            break;
        }
    }
    (void)pthread_mutex_unlock(&group->lock);
    return result;
}

static void *encoder_thread_main(void *context) {
    WbLibavEncoderGroup *group = context;
    unsigned char buffer[WB_LIBAV_ENCODER_READ_BYTES + WB_AUDIO_FRAME_BYTES];
    size_t carry = 0U;
    char error[WB_ICECAST_ERROR_SIZE] = "";

    while (!atomic_load_explicit(&group->stop_requested, memory_order_relaxed)) {
        ssize_t received = 0;
        if (group->source_fd >= 0) {
            struct pollfd descriptor = {.fd = group->source_fd, .events = POLLIN | POLLHUP};
            int poll_result = poll(&descriptor, 1, WB_LIBAV_IO_POLL_MS);
            if (poll_result < 0) {
                if (errno == EINTR) continue;
                (void)snprintf(error, sizeof(error), "DSP output poll failed: %s", strerror(errno));
                encoder_group_fail(group, error);
                break;
            }
            if (poll_result == 0) continue;
            if ((descriptor.revents & (POLLERR | POLLNVAL)) != 0) {
                copy_text(error, sizeof(error), "DSP output pipe failed");
                encoder_group_fail(group, error);
                break;
            }
            received = read(group->source_fd, buffer + carry, sizeof(buffer) - carry);
            if (received == 0) {
                if (!atomic_load_explicit(&group->stop_requested, memory_order_relaxed)) {
                    encoder_group_fail(group, "DSP PCM output closed");
                }
                break;
            }
            if (received < 0) {
                if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) continue;
                (void)snprintf(error, sizeof(error), "DSP PCM read failed: %s", strerror(errno));
                encoder_group_fail(group, error);
                break;
            }
        } else {
            size_t got;
            (void)pthread_mutex_lock(&group->lock);
            while (!atomic_load_explicit(&group->stop_requested, memory_order_relaxed)
                && group->pcm_fifo_fill == 0U) {
                (void)pthread_cond_wait(&group->cond, &group->lock);
            }
            if (atomic_load_explicit(&group->stop_requested, memory_order_relaxed)) {
                (void)pthread_mutex_unlock(&group->lock);
                break;
            }
            got = byte_fifo_read(
                group->pcm_fifo,
                group->pcm_fifo_capacity,
                &group->pcm_fifo_read_pos,
                &group->pcm_fifo_fill,
                buffer + carry,
                sizeof(buffer) - carry
            );
            (void)pthread_cond_broadcast(&group->cond);
            (void)pthread_mutex_unlock(&group->lock);
            received = (ssize_t)got;
        }
        if (received > 0) {
            size_t total = carry + (size_t)received;
            size_t aligned = total - (total % WB_AUDIO_FRAME_BYTES);
            if (aligned > 0U && encoder_group_process_pcm(group, buffer, aligned, error, sizeof(error)) != 0) {
                encoder_group_fail(group, error[0] == '\0' ? "libav encoding failed" : error);
                break;
            }
            carry = total - aligned;
            if (carry > 0U) memmove(buffer, buffer + aligned, carry);
        }
    }
    if (!group->failed) {
        size_t index;
        (void)pthread_mutex_lock(&group->lock);
        for (index = 0U; index < group->branch_capacity; index += 1U) {
            WbLibavEncoderBranch *branch = &group->branches[index];
            if (!branch->active) continue;
            if (encoder_branch_drain_fifo(branch, true, error, sizeof(error)) != 0) {
                if (!atomic_load_explicit(&group->stop_requested, memory_order_relaxed)) {
                    if (!group->failed) {
                        group->failed = true;
                        copy_text(
                            group->error,
                            sizeof(group->error),
                            error[0] == '\0' ? "libav encoder flush failed" : error
                        );
                    }
                }
                break;
            }
        }
        if (group->failed) (void)pthread_cond_broadcast(&group->cond);
        (void)pthread_mutex_unlock(&group->lock);
    }
    return NULL;
}

int wb_libav_encoder_group_start(
    WbLibavEncoderGroup **group_out,
    const WbLibavEncoderConfig *configs,
    size_t config_count,
    int pcm_source_fd,
    WbLibavEncodedSink sink,
    void *sink_context,
    char *error,
    size_t error_size
) {
    WbLibavEncoderGroup *group;
    size_t index;
    int flags;
    if (group_out == NULL || configs == NULL || config_count == 0U || sink == NULL) {
        copy_text(error, error_size, "invalid libav encoder group configuration");
        return -1;
    }
    *group_out = NULL;
    group = calloc(1U, sizeof(*group));
    if (group == NULL) {
        if (pcm_source_fd >= 0) close(pcm_source_fd);
        copy_text(error, error_size, "cannot allocate libav encoder group");
        return -1;
    }
    group->branches = calloc(WB_NATIVE_OUTPUT_MAX, sizeof(*group->branches));
    group->pcm_fifo = malloc(WB_LIBAV_ENCODER_FIFO_BYTES);
    if (group->branches == NULL || group->pcm_fifo == NULL) {
        if (pcm_source_fd >= 0) close(pcm_source_fd);
        free(group->branches);
        free(group->pcm_fifo);
        free(group);
        copy_text(error, error_size, "cannot allocate libav encoder buffers");
        return -1;
    }
    group->branch_capacity = WB_NATIVE_OUTPUT_MAX;
    group->branch_count = 0U;
    group->pcm_fifo_capacity = WB_LIBAV_ENCODER_FIFO_BYTES;
    group->source_fd = pcm_source_fd;
    group->sink = sink;
    group->sink_context = sink_context;
    atomic_init(&group->stop_requested, false);
    if (pthread_mutex_init(&group->lock, NULL) != 0) {
        if (group->source_fd >= 0) close(group->source_fd);
        free(group->branches);
        free(group->pcm_fifo);
        free(group);
        copy_text(error, error_size, "cannot initialize libav encoder mutex");
        return -1;
    }
    if (pthread_cond_init(&group->cond, NULL) != 0) {
        (void)pthread_mutex_destroy(&group->lock);
        if (group->source_fd >= 0) close(group->source_fd);
        free(group->branches);
        free(group->pcm_fifo);
        free(group);
        copy_text(error, error_size, "cannot initialize libav encoder condition");
        return -1;
    }
    if (group->source_fd >= 0) {
        flags = fcntl(group->source_fd, F_GETFL, 0);
        if (flags >= 0) (void)fcntl(group->source_fd, F_SETFL, flags | O_NONBLOCK);
    }
    for (index = 0U; index < config_count; index += 1U) {
        size_t stream_index = configs[index].stream_index;
        WbLibavEncoderBranch *branch;
        if (stream_index >= group->branch_capacity) {
            copy_text(error, error_size, "libav encoder stream index is out of range");
            goto start_failed;
        }
        branch = &group->branches[stream_index];
        if (branch->active) {
            copy_text(error, error_size, "duplicate libav encoder stream index");
            goto start_failed;
        }
        if (encoder_branch_open(group, branch, &configs[index], error, error_size) != 0) {
            encoder_branch_close(branch);
            goto start_failed;
        }
        branch->active = true;
        group->branch_count += 1U;
    }
    if (pthread_create(&group->thread, NULL, encoder_thread_main, group) != 0) {
        copy_text(error, error_size, "cannot start libav encoder thread");
        goto start_failed;
    }
    group->thread_created = true;
    *group_out = group;
    if (error != NULL && error_size > 0U) error[0] = '\0';
    return 0;

start_failed:
    for (index = 0U; index < group->branch_capacity; index += 1U) {
        if (group->branches[index].active || group->branches[index].group != NULL) {
            encoder_branch_close(&group->branches[index]);
        }
    }
    (void)pthread_cond_destroy(&group->cond);
    (void)pthread_mutex_destroy(&group->lock);
    if (group->source_fd >= 0) close(group->source_fd);
    free(group->branches);
    free(group->pcm_fifo);
    free(group);
    return -1;
}

int wb_libav_encoder_group_push_pcm(
    WbLibavEncoderGroup *group,
    const unsigned char *data,
    size_t length
) {
    size_t accepted;
    if (group == NULL || data == NULL || length == 0U || group->source_fd >= 0) return -1;
    (void)pthread_mutex_lock(&group->lock);
    if (group->failed || atomic_load_explicit(&group->stop_requested, memory_order_relaxed)
        || group->pcm_fifo_capacity - group->pcm_fifo_fill < length) {
        (void)pthread_mutex_unlock(&group->lock);
        return -1;
    }
    accepted = byte_fifo_write(
        group->pcm_fifo,
        group->pcm_fifo_capacity,
        &group->pcm_fifo_write_pos,
        &group->pcm_fifo_fill,
        data,
        length
    );
    (void)pthread_cond_broadcast(&group->cond);
    (void)pthread_mutex_unlock(&group->lock);
    return accepted == length ? 0 : -1;
}

int wb_libav_encoder_group_add_branch(
    WbLibavEncoderGroup *group,
    const WbLibavEncoderConfig *config,
    char *error,
    size_t error_size
) {
    WbLibavEncoderBranch *branch;
    if (group == NULL || config == NULL || config->stream_index >= WB_NATIVE_OUTPUT_MAX) {
        copy_text(error, error_size, "invalid libav encoder branch configuration");
        return -1;
    }
    (void)pthread_mutex_lock(&group->lock);
    if (group->failed || atomic_load_explicit(&group->stop_requested, memory_order_relaxed)) {
        copy_text(error, error_size, "libav encoder group is not accepting new branches");
        (void)pthread_mutex_unlock(&group->lock);
        return -1;
    }
    branch = &group->branches[config->stream_index];
    if (branch->active) {
        copy_text(error, error_size, "libav encoder branch already exists");
        (void)pthread_mutex_unlock(&group->lock);
        return -1;
    }
    if (encoder_branch_open(group, branch, config, error, error_size) != 0) {
        encoder_branch_close(branch);
        (void)pthread_mutex_unlock(&group->lock);
        return -1;
    }
    branch->active = true;
    group->branch_count += 1U;
    (void)pthread_mutex_unlock(&group->lock);
    if (error != NULL && error_size > 0U) error[0] = '\0';
    return 0;
}

int wb_libav_encoder_group_remove_branch(
    WbLibavEncoderGroup *group,
    size_t stream_index,
    char *error,
    size_t error_size
) {
    WbLibavEncoderBranch *branch;
    if (group == NULL || stream_index >= WB_NATIVE_OUTPUT_MAX) {
        copy_text(error, error_size, "invalid libav encoder branch index");
        return -1;
    }
    (void)pthread_mutex_lock(&group->lock);
    branch = &group->branches[stream_index];
    if (!branch->active) {
        (void)pthread_mutex_unlock(&group->lock);
        if (error != NULL && error_size > 0U) error[0] = '\0';
        return 0;
    }
    branch->active = false;
    if (group->branch_count > 0U) group->branch_count -= 1U;
    encoder_branch_close(branch);
    (void)pthread_mutex_unlock(&group->lock);
    if (error != NULL && error_size > 0U) error[0] = '\0';
    return 0;
}

void wb_libav_encoder_group_inject_failure(WbLibavEncoderGroup *group, const char *reason) {
    if (group == NULL) return;
    encoder_group_fail(group, reason == NULL ? "injected embedded encoder failure" : reason);
    wb_libav_encoder_group_stop(group);
}

bool wb_libav_encoder_group_failed(WbLibavEncoderGroup *group) {
    bool failed;
    if (group == NULL) return true;
    (void)pthread_mutex_lock(&group->lock);
    failed = group->failed;
    (void)pthread_mutex_unlock(&group->lock);
    return failed;
}

void wb_libav_encoder_group_error(
    WbLibavEncoderGroup *group,
    char *error,
    size_t error_size
) {
    if (error == NULL || error_size == 0U) return;
    error[0] = '\0';
    if (group == NULL) return;
    (void)pthread_mutex_lock(&group->lock);
    copy_text(error, error_size, group->error);
    (void)pthread_mutex_unlock(&group->lock);
}

void wb_libav_encoder_group_stop(WbLibavEncoderGroup *group) {
    if (group == NULL) return;
    atomic_store_explicit(&group->stop_requested, true, memory_order_relaxed);
    (void)pthread_mutex_lock(&group->lock);
    (void)pthread_cond_broadcast(&group->cond);
    (void)pthread_mutex_unlock(&group->lock);
}

void wb_libav_encoder_group_destroy(WbLibavEncoderGroup *group) {
    size_t index;
    if (group == NULL) return;
    wb_libav_encoder_group_stop(group);
    if (group->thread_created) {
        (void)pthread_join(group->thread, NULL);
        group->thread_created = false;
    }
    if (group->source_fd >= 0) {
        close(group->source_fd);
        group->source_fd = -1;
    }
    for (index = 0U; index < group->branch_capacity; index += 1U) {
        if (group->branches[index].active || group->branches[index].group != NULL) {
            encoder_branch_close(&group->branches[index]);
        }
    }
    (void)pthread_cond_destroy(&group->cond);
    (void)pthread_mutex_destroy(&group->lock);
    free(group->branches);
    free(group->pcm_fifo);
    free(group);
}
