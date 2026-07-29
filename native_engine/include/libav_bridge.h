#ifndef WB_LIBAV_BRIDGE_H
#define WB_LIBAV_BRIDGE_H

#include "engine.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

typedef struct WbLibavDecodeSession WbLibavDecodeSession;
typedef struct WbLibavEncoderGroup WbLibavEncoderGroup;

typedef struct {
    const char *path;
    bool stream_source;
    bool stream_infinite;
    int64_t start_ms;
    int64_t duration_ms;
    size_t fifo_capacity;
} WbLibavDecodeConfig;

typedef struct {
    size_t stream_index;
    char codec[WB_NATIVE_OUTPUT_CODEC_SIZE];
    int bitrate_kbps;
} WbLibavEncoderConfig;

typedef void (*WbLibavEncodedSink)(
    void *context,
    size_t stream_index,
    const unsigned char *data,
    size_t length
);

int wb_libav_runtime_init(WbEngineState *state, char *error, size_t error_size);
void wb_libav_runtime_shutdown(void);

int wb_libav_decode_start(
    WbLibavDecodeSession **session,
    const WbLibavDecodeConfig *config,
    char *error,
    size_t error_size
);
ssize_t wb_libav_decode_read(
    WbLibavDecodeSession *session,
    unsigned char *buffer,
    size_t buffer_size
);
void wb_libav_decode_abort(WbLibavDecodeSession *session);
void wb_libav_decode_destroy(WbLibavDecodeSession *session);
bool wb_libav_decode_finished(WbLibavDecodeSession *session);
uint64_t wb_libav_decode_invalid_data_skip_count(WbLibavDecodeSession *session);
void wb_libav_decode_error(
    WbLibavDecodeSession *session,
    char *error,
    size_t error_size
);

int wb_libav_encoder_group_start(
    WbLibavEncoderGroup **group,
    const WbLibavEncoderConfig *configs,
    size_t config_count,
    int pcm_source_fd,
    WbLibavEncodedSink sink,
    void *sink_context,
    char *error,
    size_t error_size
);
int wb_libav_encoder_group_push_pcm(
    WbLibavEncoderGroup *group,
    const unsigned char *data,
    size_t length
);
int wb_libav_encoder_group_add_branch(
    WbLibavEncoderGroup *group,
    const WbLibavEncoderConfig *config,
    char *error,
    size_t error_size
);
int wb_libav_encoder_group_remove_branch(
    WbLibavEncoderGroup *group,
    size_t stream_index,
    char *error,
    size_t error_size
);
bool wb_libav_encoder_group_failed(WbLibavEncoderGroup *group);
void wb_libav_encoder_group_inject_failure(WbLibavEncoderGroup *group, const char *reason);
void wb_libav_encoder_group_error(
    WbLibavEncoderGroup *group,
    char *error,
    size_t error_size
);
void wb_libav_encoder_group_stop(WbLibavEncoderGroup *group);
void wb_libav_encoder_group_destroy(WbLibavEncoderGroup *group);

#endif
