#define _POSIX_C_SOURCE 200809L

#include "daemon.h"
#include "protocol.h"

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

static int64_t daemon_monotonic_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return 0;
    return (int64_t)now.tv_sec * 1000LL + (int64_t)(now.tv_nsec / 1000000L);
}

static void copy_text(char *destination, size_t size, const char *source) {
    if (destination == NULL || size == 0U) return;
    (void)snprintf(destination, size, "%s", source == NULL ? "" : source);
}

static bool valid_station_key(const char *station_key) {
    const unsigned char *cursor = (const unsigned char *)station_key;
    size_t length;
    if (station_key == NULL || station_key[0] == '\0') return false;
    length = strlen(station_key);
    if (length >= WB_STATION_KEY_SIZE) return false;
    while (*cursor != '\0') {
        if (*cursor < 0x20U || *cursor == 0x7fU) return false;
        cursor += 1;
    }
    return true;
}

static int daemon_send_line(WbDaemonState *daemon, int fd, const char *line) {
    int result;
    if (fd < 0) return 0;
    (void)pthread_mutex_lock(&daemon->send_lock);
    result = wb_send_line(fd, line);
    (void)pthread_mutex_unlock(&daemon->send_lock);
    return result;
}

static void update_protocol_identity(WbDaemonState *daemon, const char *line) {
    char session_id[WB_SESSION_ID_SIZE] = "";
    char app_version[WB_APP_VERSION_SIZE] = "";
    bool has_session = wb_json_get_string(line, "session_id", session_id, sizeof(session_id));
    bool has_app = wb_json_get_string(line, "app_version", app_version, sizeof(app_version));
    if (!has_session && !has_app) return;
    (void)pthread_mutex_lock(&daemon->lock);
    if (has_session && session_id[0] != '\0') copy_text(daemon->session_id, sizeof(daemon->session_id), session_id);
    if (has_app && app_version[0] != '\0') copy_text(daemon->app_version, sizeof(daemon->app_version), app_version);
    (void)pthread_mutex_unlock(&daemon->lock);
}

static int daemon_send_reply(
    WbDaemonState *daemon,
    int fd,
    int64_t request_id,
    bool ok,
    const char *payload,
    bool is_error
) {
    char session_id[WB_SESSION_ID_SIZE];
    char app_version[WB_APP_VERSION_SIZE];
    char escaped_session[WB_SESSION_ID_SIZE * 2];
    char escaped_app[WB_APP_VERSION_SIZE * 2];
    char escaped_error[4096];
    const char *body = payload == NULL ? (is_error ? "" : "null") : payload;
    size_t capacity = strlen(body) + 8192U;
    char *line;
    int written;
    int result;
    if (capacity > 4U * 1024U * 1024U) return -1;
    line = malloc(capacity);
    if (line == NULL) return -1;
    (void)pthread_mutex_lock(&daemon->lock);
    copy_text(session_id, sizeof(session_id), daemon->session_id);
    copy_text(app_version, sizeof(app_version), daemon->app_version);
    (void)pthread_mutex_unlock(&daemon->lock);
    wb_json_escape(session_id, escaped_session, sizeof(escaped_session));
    wb_json_escape(app_version, escaped_app, sizeof(escaped_app));
    if (is_error) {
        wb_json_escape(body, escaped_error, sizeof(escaped_error));
        written = snprintf(
            line,
            capacity,
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
    } else {
        written = snprintf(
            line,
            capacity,
            "{\"version\":%d,\"reply_to\":%lld,\"ok\":%s,"
            "\"session_id\":\"%s\",\"app_version\":\"%s\","
            "\"native_daemon_version\":\"%s\",\"result\":%s}",
            WB_PROTOCOL_VERSION,
            (long long)request_id,
            ok ? "true" : "false",
            escaped_session,
            escaped_app,
            WB_NATIVE_DAEMON_VERSION,
            body
        );
    }
    if (written < 0 || (size_t)written >= capacity) {
        free(line);
        return -1;
    }
    result = daemon_send_line(daemon, fd, line);
    free(line);
    return result;
}

static int daemon_event_sink(
    void *context,
    WbEngineState *state,
    const char *event,
    const WbDeckState *deck_state,
    char deck,
    const char *payload_json
) {
    WbDaemonState *daemon = context;
    int clients[WB_NATIVE_CLIENT_MAX];
    size_t count = 0U;
    size_t index;
    int result = 0;
    (void)pthread_mutex_lock(&daemon->lock);
    count = daemon->client_count;
    if (count > WB_NATIVE_CLIENT_MAX) count = WB_NATIVE_CLIENT_MAX;
    memcpy(clients, daemon->client_fds, count * sizeof(clients[0]));
    (void)pthread_mutex_unlock(&daemon->lock);
    for (index = 0U; index < count; index += 1U) {
        if (wb_engine_send_event_to_fd(state, clients[index], event, deck_state, deck, payload_json) != 0) {
            result = -1;
        }
    }
    return result;
}

static WbStationEntry *find_station_locked(WbDaemonState *daemon, const char *station_key) {
    size_t index;
    for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
        WbStationEntry *entry = &daemon->stations[index];
        if (entry->in_use && strcmp(entry->station_key, station_key) == 0) return entry;
    }
    return NULL;
}

static WbStationEntry *create_station_locked(
    WbDaemonState *daemon,
    const char *station_key,
    char *error,
    size_t error_size
) {
    size_t index;
    WbEngineState *engine;
    WbStationEntry *entry = NULL;
    if (!valid_station_key(station_key)) {
        copy_text(error, error_size, "invalid station_key");
        return NULL;
    }
    for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
        if (!daemon->stations[index].in_use) {
            entry = &daemon->stations[index];
            break;
        }
    }
    if (entry == NULL) {
        copy_text(error, error_size, "native station capacity reached");
        return NULL;
    }
    engine = calloc(1U, sizeof(*engine));
    if (engine == NULL) {
        copy_text(error, error_size, "cannot allocate station engine");
        return NULL;
    }
    wb_engine_init(engine);
    if (!engine->ffmpeg_runtime_valid) {
        copy_text(error, error_size, engine->ffmpeg_runtime_error[0] != '\0' ? engine->ffmpeg_runtime_error : "FFmpeg runtime invalid");
        wb_engine_destroy(engine);
        free(engine);
        return NULL;
    }
    copy_text(engine->station_key, sizeof(engine->station_key), station_key);
    engine->shared_send_lock = &daemon->send_lock;
    engine->event_sink = daemon_event_sink;
    engine->event_sink_context = daemon;
    copy_text(engine->session_id, sizeof(engine->session_id), daemon->session_id);
    copy_text(engine->app_version, sizeof(engine->app_version), daemon->app_version);
    memset(entry, 0, sizeof(*entry));
    entry->in_use = true;
    entry->created_monotonic_ms = daemon_monotonic_ms();
    copy_text(entry->station_key, sizeof(entry->station_key), station_key);
    entry->engine = engine;
    daemon->stations_created_total += 1U;
    return entry;
}

static WbStationEntry *acquire_station(
    WbDaemonState *daemon,
    const char *station_key,
    bool create,
    char *error,
    size_t error_size
) {
    WbStationEntry *entry;
    (void)pthread_mutex_lock(&daemon->lock);
    entry = find_station_locked(daemon, station_key);
    if (entry == NULL && create && !daemon->shutting_down) {
        entry = create_station_locked(daemon, station_key, error, error_size);
    }
    if (entry == NULL && error != NULL && error_size > 0U && error[0] == '\0') {
        copy_text(error, error_size, "station does not exist");
    }
    if (entry != NULL && !entry->removing) {
        entry->ref_count += 1U;
    } else if (entry != NULL) {
        entry = NULL;
        copy_text(error, error_size, "station is being removed");
    }
    (void)pthread_mutex_unlock(&daemon->lock);
    return entry;
}

static void release_station(WbDaemonState *daemon, WbStationEntry *entry) {
    if (entry == NULL) return;
    (void)pthread_mutex_lock(&daemon->lock);
    if (entry->ref_count > 0U) entry->ref_count -= 1U;
    (void)pthread_cond_broadcast(&daemon->cond);
    (void)pthread_mutex_unlock(&daemon->lock);
}

static size_t station_count_locked(const WbDaemonState *daemon) {
    size_t index;
    size_t count = 0U;
    for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
        if (daemon->stations[index].in_use) count += 1U;
    }
    return count;
}

static int build_station_list_json(WbDaemonState *daemon, char *output, size_t output_size, bool include_state) {
    size_t index;
    size_t used = 0U;
    size_t count;
    int written;
    if (output == NULL || output_size < 64U) return -1;
    (void)pthread_mutex_lock(&daemon->lock);
    count = station_count_locked(daemon);
    written = snprintf(
        output,
        output_size,
        "{\"multi_station\":true,\"max_stations\":%d,\"station_count\":%zu,\"stations\":[",
        WB_NATIVE_STATION_MAX,
        count
    );
    if (written < 0 || (size_t)written >= output_size) {
        (void)pthread_mutex_unlock(&daemon->lock);
        return -1;
    }
    used = (size_t)written;
    for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
        WbStationEntry *entry = &daemon->stations[index];
        char escaped[WB_STATION_KEY_SIZE * 2];
        bool running = false;
        bool transitioning = false;
        char active_deck = 'A';
        size_t output_count = 0U;
        if (!entry->in_use || entry->engine == NULL) continue;
        wb_json_escape(entry->station_key, escaped, sizeof(escaped));
        if (include_state) {
            (void)pthread_mutex_lock(&entry->engine->lock);
            running = entry->engine->running;
            transitioning = entry->engine->transitioning;
            active_deck = entry->engine->active_deck;
            (void)pthread_mutex_unlock(&entry->engine->lock);
            (void)pthread_mutex_lock(&entry->engine->icecast_output.lock);
            output_count = entry->engine->icecast_output.stream_count;
            (void)pthread_mutex_unlock(&entry->engine->icecast_output.lock);
        }
        written = snprintf(
            output + used,
            output_size - used,
            "%s{\"station_key\":\"%s\",\"running\":%s,\"transitioning\":%s,\"active_deck\":\"%c\",\"output_count\":%zu,\"created_monotonic_ms\":%lld}",
            used > 0U && output[used - 1U] != '[' ? "," : "",
            escaped,
            running ? "true" : "false",
            transitioning ? "true" : "false",
            active_deck,
            output_count,
            (long long)entry->created_monotonic_ms
        );
        if (written < 0 || (size_t)written >= output_size - used) {
            (void)pthread_mutex_unlock(&daemon->lock);
            return -1;
        }
        used += (size_t)written;
    }
    (void)pthread_mutex_unlock(&daemon->lock);
    written = snprintf(
        output + used,
        output_size - used,
        "],\"stations_created_total\":%llu,\"stations_removed_total\":%llu}",
        (unsigned long long)daemon->stations_created_total,
        (unsigned long long)daemon->stations_removed_total
    );
    if (written < 0 || (size_t)written >= output_size - used) return -1;
    return (int)(used + (size_t)written);
}

static int remove_station(WbDaemonState *daemon, const char *station_key, char *error, size_t error_size) {
    WbStationEntry *entry;
    WbEngineState *engine;
    (void)pthread_mutex_lock(&daemon->lock);
    entry = find_station_locked(daemon, station_key);
    if (entry == NULL) {
        (void)pthread_mutex_unlock(&daemon->lock);
        copy_text(error, error_size, "station does not exist");
        return -1;
    }
    entry->removing = true;
    while (entry->ref_count > 0U) {
        (void)pthread_cond_wait(&daemon->cond, &daemon->lock);
    }
    engine = entry->engine;
    memset(entry, 0, sizeof(*entry));
    daemon->stations_removed_total += 1U;
    (void)pthread_mutex_unlock(&daemon->lock);
    if (engine != NULL) {
        wb_engine_destroy(engine);
        free(engine);
    }
    return 0;
}

int wb_daemon_init(WbDaemonState *daemon) {
    WbEngineState probe;
    if (daemon == NULL) return -1;
    memset(daemon, 0, sizeof(*daemon));
    (void)pthread_mutex_init(&daemon->lock, NULL);
    (void)pthread_cond_init(&daemon->cond, NULL);
    (void)pthread_mutex_init(&daemon->send_lock, NULL);
    wb_engine_init(&probe);
    copy_text(daemon->ffmpeg_path, sizeof(daemon->ffmpeg_path), probe.ffmpeg_path);
    copy_text(daemon->ffmpeg_source, sizeof(daemon->ffmpeg_source), probe.ffmpeg_source);
    copy_text(daemon->ffmpeg_version, sizeof(daemon->ffmpeg_version), probe.ffmpeg_version);
    copy_text(daemon->ffmpeg_runtime_build, sizeof(daemon->ffmpeg_runtime_build), probe.ffmpeg_runtime_build);
    copy_text(daemon->ffmpeg_runtime_error, sizeof(daemon->ffmpeg_runtime_error), probe.ffmpeg_runtime_error);
    daemon->ffmpeg_runtime_valid = probe.ffmpeg_runtime_valid;
    daemon->ffmpeg_system_fallback_used = probe.ffmpeg_system_fallback_used;
    wb_engine_destroy(&probe);
    return daemon->ffmpeg_runtime_valid ? 0 : -1;
}

void wb_daemon_destroy(WbDaemonState *daemon) {
    size_t index;
    if (daemon == NULL) return;
    (void)pthread_mutex_lock(&daemon->lock);
    daemon->shutting_down = true;
    (void)pthread_mutex_unlock(&daemon->lock);
    wb_daemon_shutdown_clients(daemon);
    for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
        WbEngineState *engine = NULL;
        (void)pthread_mutex_lock(&daemon->lock);
        if (daemon->stations[index].in_use) {
            daemon->stations[index].removing = true;
            while (daemon->stations[index].ref_count > 0U) {
                (void)pthread_cond_wait(&daemon->cond, &daemon->lock);
            }
            engine = daemon->stations[index].engine;
            memset(&daemon->stations[index], 0, sizeof(daemon->stations[index]));
        }
        (void)pthread_mutex_unlock(&daemon->lock);
        if (engine != NULL) {
            wb_engine_destroy(engine);
            free(engine);
        }
    }
    (void)pthread_mutex_destroy(&daemon->send_lock);
    (void)pthread_cond_destroy(&daemon->cond);
    (void)pthread_mutex_destroy(&daemon->lock);
}

int wb_daemon_client_connected(WbDaemonState *daemon, int client_fd) {
    size_t index;
    int result = -1;
    if (daemon == NULL || client_fd < 0) return -1;
    (void)pthread_mutex_lock(&daemon->lock);
    if (!daemon->shutting_down) {
        for (index = 0U; index < daemon->client_count; index += 1U) {
            if (daemon->client_fds[index] == client_fd) {
                result = 0;
                break;
            }
        }
        if (result != 0 && daemon->client_count < WB_NATIVE_CLIENT_MAX) {
            daemon->client_fds[daemon->client_count++] = client_fd;
            result = 0;
        }
    }
    (void)pthread_mutex_unlock(&daemon->lock);
    return result;
}

void wb_daemon_client_disconnected(WbDaemonState *daemon, int client_fd) {
    size_t index;
    if (daemon == NULL) return;
    (void)pthread_mutex_lock(&daemon->lock);
    for (index = 0U; index < daemon->client_count; index += 1U) {
        if (daemon->client_fds[index] == client_fd) {
            daemon->client_fds[index] = daemon->client_fds[daemon->client_count - 1U];
            daemon->client_count -= 1U;
            break;
        }
    }
    (void)pthread_cond_broadcast(&daemon->cond);
    (void)pthread_mutex_unlock(&daemon->lock);
}

void wb_daemon_shutdown_clients(WbDaemonState *daemon) {
    int clients[WB_NATIVE_CLIENT_MAX];
    size_t count;
    size_t index;
    if (daemon == NULL) return;
    (void)pthread_mutex_lock(&daemon->lock);
    count = daemon->client_count;
    if (count > WB_NATIVE_CLIENT_MAX) count = WB_NATIVE_CLIENT_MAX;
    memcpy(clients, daemon->client_fds, count * sizeof(clients[0]));
    (void)pthread_mutex_unlock(&daemon->lock);
    for (index = 0U; index < count; index += 1U) {
        (void)shutdown(clients[index], SHUT_RDWR);
    }
    (void)pthread_mutex_lock(&daemon->lock);
    while (daemon->client_count > 0U) {
        (void)pthread_cond_wait(&daemon->cond, &daemon->lock);
    }
    (void)pthread_mutex_unlock(&daemon->lock);
}

int wb_daemon_send_ready(WbDaemonState *daemon, int client_fd, const char *socket_path) {
    char escaped_socket[1024];
    char escaped_path[WB_PATH_SIZE * 2];
    char escaped_source[WB_FFMPEG_SOURCE_SIZE * 2];
    char escaped_version[WB_FFMPEG_VERSION_SIZE * 2];
    char escaped_build[WB_FFMPEG_BUILD_SIZE * 2];
    char escaped_error[WB_FFMPEG_ERROR_SIZE * 2];
    char line[16384];
    wb_json_escape(socket_path, escaped_socket, sizeof(escaped_socket));
    wb_json_escape(daemon->ffmpeg_path, escaped_path, sizeof(escaped_path));
    wb_json_escape(daemon->ffmpeg_source, escaped_source, sizeof(escaped_source));
    wb_json_escape(daemon->ffmpeg_version, escaped_version, sizeof(escaped_version));
    wb_json_escape(daemon->ffmpeg_runtime_build, escaped_build, sizeof(escaped_build));
    wb_json_escape(daemon->ffmpeg_runtime_error, escaped_error, sizeof(escaped_error));
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"event\":\"engine_ready\",\"station_key\":\"\","
        "\"session_id\":\"\",\"app_version\":\"\",\"native_daemon_version\":\"%s\",\"payload\":{"
        "\"multi_station\":true,\"max_stations\":%d,\"station_count\":0,\"multi_client\":true,"
        "\"socket_path\":\"%s\",\"ffmpeg_source\":\"%s\",\"ffmpeg_path\":\"%s\","
        "\"ffmpeg_version\":\"%s\",\"ffmpeg_runtime_build\":\"%s\","
        "\"ffmpeg_runtime_valid\":%s,\"ffmpeg_system_fallback_used\":%s,\"ffmpeg_runtime_error\":\"%s\","
        "\"embedded_libav\":true,\"ffmpeg_subprocesses\":false,"
        "\"native_daemon_version\":\"%s\"}}",
        WB_PROTOCOL_VERSION,
        WB_NATIVE_DAEMON_VERSION,
        WB_NATIVE_STATION_MAX,
        escaped_socket,
        escaped_source,
        escaped_path,
        escaped_version,
        escaped_build,
        daemon->ffmpeg_runtime_valid ? "true" : "false",
        daemon->ffmpeg_system_fallback_used ? "true" : "false",
        escaped_error,
        WB_NATIVE_DAEMON_VERSION
    );
    return daemon_send_line(daemon, client_fd, line);
}

int wb_daemon_handle_line(WbDaemonState *daemon, int client_fd, const char *line) {
    int64_t request_id = 0;
    int64_t version = 0;
    char command[128] = "";
    char station_key[WB_STATION_KEY_SIZE] = "";
    char error[512] = "";
    WbStationEntry *entry;
    int result;
    update_protocol_identity(daemon, line);
    if (!wb_json_get_i64(line, "request_id", &request_id)) {
        return daemon_send_reply(daemon, client_fd, 0, false, "missing or invalid request_id", true);
    }
    if (!wb_json_get_i64(line, "version", &version) || version != WB_PROTOCOL_VERSION) {
        return daemon_send_reply(daemon, client_fd, request_id, false, "unsupported protocol version", true);
    }
    if (!wb_json_get_string(line, "command", command, sizeof(command))) {
        return daemon_send_reply(daemon, client_fd, request_id, false, "missing command", true);
    }
    (void)wb_json_get_string(line, "station_key", station_key, sizeof(station_key));

    if (strcmp(command, "ping") == 0) {
        char payload[1024];
        size_t count;
        size_t clients;
        (void)pthread_mutex_lock(&daemon->lock);
        count = station_count_locked(daemon);
        clients = daemon->client_count;
        (void)pthread_mutex_unlock(&daemon->lock);
        (void)snprintf(
            payload,
            sizeof(payload),
            "{\"pong\":true,\"multi_station\":true,\"max_stations\":%d,\"station_count\":%zu,\"client_count\":%zu,\"native_daemon_version\":\"%s\"}",
            WB_NATIVE_STATION_MAX,
            count,
            clients,
            WB_NATIVE_DAEMON_VERSION
        );
        return daemon_send_reply(daemon, client_fd, request_id, true, payload, false);
    }
    if (strcmp(command, "list_stations") == 0 || strcmp(command, "get_all_station_states") == 0) {
        char payload[65536];
        if (build_station_list_json(daemon, payload, sizeof(payload), true) < 0) {
            return daemon_send_reply(daemon, client_fd, request_id, false, "station list is too large", true);
        }
        return daemon_send_reply(daemon, client_fd, request_id, true, payload, false);
    }
    if (strcmp(command, "create_station") == 0) {
        if (!valid_station_key(station_key)) {
            return daemon_send_reply(daemon, client_fd, request_id, false, "create_station requires station_key", true);
        }
        entry = acquire_station(daemon, station_key, true, error, sizeof(error));
        if (entry == NULL) return daemon_send_reply(daemon, client_fd, request_id, false, error, true);
        {
            char escaped[WB_STATION_KEY_SIZE * 2];
            char payload[1024];
            wb_json_escape(station_key, escaped, sizeof(escaped));
            (void)snprintf(payload, sizeof(payload), "{\"created\":true,\"station_key\":\"%s\",\"multi_station\":true}", escaped);
            release_station(daemon, entry);
            return daemon_send_reply(daemon, client_fd, request_id, true, payload, false);
        }
    }
    if (strcmp(command, "remove_station") == 0) {
        char escaped[WB_STATION_KEY_SIZE * 2];
        char payload[1024];
        if (!valid_station_key(station_key)) {
            return daemon_send_reply(daemon, client_fd, request_id, false, "remove_station requires station_key", true);
        }
        if (remove_station(daemon, station_key, error, sizeof(error)) != 0) {
            return daemon_send_reply(daemon, client_fd, request_id, false, error, true);
        }
        wb_json_escape(station_key, escaped, sizeof(escaped));
        (void)snprintf(payload, sizeof(payload), "{\"removed\":true,\"station_key\":\"%s\"}", escaped);
        return daemon_send_reply(daemon, client_fd, request_id, true, payload, false);
    }
    if (strcmp(command, "stop_all_stations") == 0) {
        size_t index;
        size_t stopped = 0U;
        for (index = 0U; index < WB_NATIVE_STATION_MAX; index += 1U) {
            WbStationEntry *current = NULL;
            (void)pthread_mutex_lock(&daemon->lock);
            if (daemon->stations[index].in_use && !daemon->stations[index].removing) {
                current = &daemon->stations[index];
                current->ref_count += 1U;
            }
            (void)pthread_mutex_unlock(&daemon->lock);
            if (current != NULL) {
                char command_line[1024];
                (void)snprintf(
                    command_line,
                    sizeof(command_line),
                    "{\"version\":%d,\"request_id\":0,\"command\":\"stop\",\"station_key\":\"%s\"}",
                    WB_PROTOCOL_VERSION,
                    current->station_key
                );
                (void)wb_engine_handle_line(current->engine, -1, command_line);
                stopped += 1U;
                release_station(daemon, current);
            }
        }
        {
            char payload[256];
            (void)snprintf(payload, sizeof(payload), "{\"stopped\":%zu}", stopped);
            return daemon_send_reply(daemon, client_fd, request_id, true, payload, false);
        }
    }

    if (station_key[0] == '\0') copy_text(station_key, sizeof(station_key), "__default__");
    if (!valid_station_key(station_key)) {
        return daemon_send_reply(daemon, client_fd, request_id, false, "invalid station_key", true);
    }
    entry = acquire_station(daemon, station_key, true, error, sizeof(error));
    if (entry == NULL) return daemon_send_reply(daemon, client_fd, request_id, false, error, true);
    wb_engine_client_connected(entry->engine, client_fd);
    result = wb_engine_handle_line(entry->engine, client_fd, line);
    release_station(daemon, entry);
    return result;
}
