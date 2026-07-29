#ifndef WB_DAEMON_H
#define WB_DAEMON_H

#include "engine.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <pthread.h>

#define WB_NATIVE_STATION_MAX 64
#define WB_NATIVE_CLIENT_MAX 32

typedef struct {
    bool in_use;
    bool removing;
    uint64_t ref_count;
    int64_t created_monotonic_ms;
    char station_key[WB_STATION_KEY_SIZE];
    WbEngineState *engine;
} WbStationEntry;

typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    pthread_mutex_t send_lock;
    WbStationEntry stations[WB_NATIVE_STATION_MAX];
    int client_fds[WB_NATIVE_CLIENT_MAX];
    size_t client_count;
    bool shutting_down;
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char ffmpeg_path[WB_PATH_SIZE];
    char ffmpeg_source[WB_FFMPEG_SOURCE_SIZE];
    char ffmpeg_version[WB_FFMPEG_VERSION_SIZE];
    char ffmpeg_runtime_build[WB_FFMPEG_BUILD_SIZE];
    char ffmpeg_runtime_error[WB_FFMPEG_ERROR_SIZE];
    bool ffmpeg_runtime_valid;
    bool ffmpeg_system_fallback_used;
    uint64_t stations_created_total;
    uint64_t stations_removed_total;
} WbDaemonState;

int wb_daemon_init(WbDaemonState *daemon);
void wb_daemon_destroy(WbDaemonState *daemon);
int wb_daemon_client_connected(WbDaemonState *daemon, int client_fd);
void wb_daemon_client_disconnected(WbDaemonState *daemon, int client_fd);
void wb_daemon_shutdown_clients(WbDaemonState *daemon);
int wb_daemon_send_ready(WbDaemonState *daemon, int client_fd, const char *socket_path);
int wb_daemon_handle_line(WbDaemonState *daemon, int client_fd, const char *line);

#endif
