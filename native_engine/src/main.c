#define _POSIX_C_SOURCE 200809L

#include "daemon.h"
#include "protocol.h"

#include <errno.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>
#ifdef __linux__
#include <sys/prctl.h>
#endif

static volatile sig_atomic_t stop_requested = 0;
static int server_fd = -1;
static const char *socket_path_global = NULL;
static long managed_parent_pid = 0;

typedef struct {
    WbDaemonState *daemon;
    int client_fd;
    char socket_path[108];
} WbClientThreadArgs;


static bool configure_managed_parent(void) {
    const char *value = getenv("WEB_BROADCASTER_ENGINE_PARENT_PID");
    char *end = NULL;
    long expected_parent;
    if (value == NULL || value[0] == '\0') return true;
    errno = 0;
    expected_parent = strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || expected_parent <= 1) {
        fprintf(stderr, "Invalid managed parent pid: %s\n", value);
        return false;
    }
#ifdef __linux__
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) != 0) {
        perror("prctl(PR_SET_PDEATHSIG)");
        return false;
    }
    if ((long)getppid() != expected_parent) {
        fprintf(stderr, "Managed parent exited before native engine startup completed\n");
        return false;
    }
    managed_parent_pid = expected_parent;
#else
    (void)expected_parent;
#endif
    return true;
}

static void *managed_parent_watch_main(void *opaque) {
    struct timespec delay = {0, 50000000L};
    (void)opaque;
    while (!stop_requested && managed_parent_pid > 1) {
        if ((long)getppid() != managed_parent_pid) {
            if (socket_path_global != NULL) (void)unlink(socket_path_global);
            if (server_fd >= 0) close(server_fd);
            _exit(0);
        }
        if (nanosleep(&delay, NULL) != 0 && errno != EINTR) break;
    }
    return NULL;
}

static void start_managed_parent_watch(void) {
    pthread_t thread;
    if (managed_parent_pid <= 1) return;
    if (pthread_create(&thread, NULL, managed_parent_watch_main, NULL) == 0) {
        (void)pthread_detach(thread);
    }
}

static void handle_signal(int signal_number) {
    (void)signal_number;
    stop_requested = 1;
    if (server_fd >= 0) {
        close(server_fd);
        server_fd = -1;
    }
    /* unlink(2) is async-signal-safe; remove the managed socket even if
     * shutdown cannot return through the normal accept-loop cleanup path. */
    if (socket_path_global != NULL) {
        (void)unlink(socket_path_global);
    }
}

static int serve_client(WbDaemonState *daemon, int client_fd, const char *socket_path) {
    char receive_buffer[65536];
    char *message = malloc(WB_MAX_MESSAGE_BYTES + 1U);
    size_t used = 0;
    if (message == NULL) return -1;

    if (wb_daemon_client_connected(daemon, client_fd) != 0) {
        (void)wb_send_error_reply(client_fd, 0, "native control client capacity reached");
        free(message);
        return -1;
    }
    if (wb_daemon_send_ready(daemon, client_fd, socket_path) != 0) {
        wb_daemon_client_disconnected(daemon, client_fd);
        free(message);
        return -1;
    }

    while (!stop_requested) {
        ssize_t received = recv(client_fd, receive_buffer, sizeof(receive_buffer), 0);
        if (received < 0) {
            if (errno == EINTR) continue;
            break;
        }
        if (received == 0) break;
        for (ssize_t i = 0; i < received; ++i) {
            char ch = receive_buffer[i];
            if (ch == '\n') {
                message[used] = '\0';
                if (used > 0 && wb_daemon_handle_line(daemon, client_fd, message) != 0) {
                    wb_daemon_client_disconnected(daemon, client_fd);
                    free(message);
                    return -1;
                }
                used = 0;
            } else if (ch != '\r') {
                if (used >= WB_MAX_MESSAGE_BYTES) {
                    (void)wb_send_error_reply(client_fd, 0, "message too large");
                    wb_daemon_client_disconnected(daemon, client_fd);
                    free(message);
                    return -1;
                }
                message[used++] = ch;
            }
        }
    }
    wb_daemon_client_disconnected(daemon, client_fd);
    free(message);
    return 0;
}

static void *client_thread_main(void *opaque) {
    WbClientThreadArgs *args = opaque;
    (void)serve_client(args->daemon, args->client_fd, args->socket_path);
    close(args->client_fd);
    free(args);
    return NULL;
}

int main(int argc, char **argv) {
    const char *environment_path = getenv("WEB_BROADCASTER_ENGINE_SOCKET");
    const char *socket_path = "/tmp/web-broadcaster-engine.sock";
    struct sockaddr_un address;
    WbDaemonState daemon;

    if (environment_path != NULL && environment_path[0] != '\0') socket_path = environment_path;
    if (argc >= 2 && argv[1][0] != '\0') socket_path = argv[1];
    if (strlen(socket_path) >= sizeof(address.sun_path)) {
        fprintf(stderr, "Socket path is too long: %s\n", socket_path);
        return 2;
    }

    socket_path_global = socket_path;
    /* app.py historically ignored SIGCHLD. A managed daemon inherits signal
     * dispositions across exec(), so restore the default before the optional
     * external SoundSolution DSP child is spawned. Otherwise the DSP can be
     * auto-reaped and waitpid() reports ECHILD. */
    (void)signal(SIGCHLD, SIG_DFL);
    (void)signal(SIGINT, handle_signal);
    (void)signal(SIGTERM, handle_signal);
    (void)signal(SIGPIPE, SIG_IGN);
    if (!configure_managed_parent()) return 4;
    if (wb_daemon_init(&daemon) != 0) {
        fprintf(
            stderr,
            "Embedded FFmpeg/libav runtime validation failed: %s\nPath: %s\n",
            daemon.ffmpeg_runtime_error[0] != '\0' ? daemon.ffmpeg_runtime_error : "unknown error",
            daemon.ffmpeg_path
        );
        wb_daemon_destroy(&daemon);
        return 3;
    }

    server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (server_fd < 0) {
        perror("socket");
        wb_daemon_destroy(&daemon);
        return 1;
    }
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    (void)snprintf(address.sun_path, sizeof(address.sun_path), "%s", socket_path);
    (void)unlink(socket_path);
    if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) != 0) {
        perror("bind");
        close(server_fd);
        wb_daemon_destroy(&daemon);
        return 1;
    }
    (void)chmod(socket_path, 0660);
    if (listen(server_fd, 32) != 0) {
        perror("listen");
        close(server_fd);
        (void)unlink(socket_path);
        wb_daemon_destroy(&daemon);
        return 1;
    }
    start_managed_parent_watch();

    printf("Web Broadcaster native multi-station daemon v%s\n", WB_NATIVE_DAEMON_VERSION);
    printf("Socket: %s\n", socket_path);
    printf("Station capacity: %d\n", WB_NATIVE_STATION_MAX);
    printf("Control clients: up to %d concurrent\n", WB_NATIVE_CLIENT_MAX);
    printf("libav source: %s\n", daemon.ffmpeg_source);
    printf("libav path: %s\n", daemon.ffmpeg_path);
    printf("libav version: %s\n", daemon.ffmpeg_version);
    printf("libav runtime: %s\n", daemon.ffmpeg_runtime_build);
    printf("libav runtime valid: true\n");
    printf("libav system fallback used: false\n");
    fflush(stdout);

    while (!stop_requested) {
        int client_fd = accept(server_fd, NULL, NULL);
        pthread_t thread;
        WbClientThreadArgs *args;
        if (client_fd < 0) {
            if (errno == EINTR || stop_requested) continue;
            perror("accept");
            break;
        }
        args = calloc(1U, sizeof(*args));
        if (args == NULL) {
            close(client_fd);
            continue;
        }
        args->daemon = &daemon;
        args->client_fd = client_fd;
        (void)snprintf(args->socket_path, sizeof(args->socket_path), "%s", socket_path);
        if (pthread_create(&thread, NULL, client_thread_main, args) != 0) {
            close(client_fd);
            free(args);
            continue;
        }
        (void)pthread_detach(thread);
    }

    if (server_fd >= 0) close(server_fd);
    wb_daemon_shutdown_clients(&daemon);
    if (socket_path_global != NULL) (void)unlink(socket_path_global);
    wb_daemon_destroy(&daemon);
    return 0;
}
