#define _POSIX_C_SOURCE 200809L

#include "protocol.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>

static const char *find_value(const char *json, const char *key) {
    char pattern[256];
    int written;
    const char *cursor;

    if (json == NULL || key == NULL) {
        return NULL;
    }
    written = snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    if (written <= 0 || (size_t)written >= sizeof(pattern)) {
        return NULL;
    }
    cursor = json;
    while ((cursor = strstr(cursor, pattern)) != NULL) {
        const char *after = cursor + (size_t)written;
        while (*after != '\0' && isspace((unsigned char)*after)) {
            ++after;
        }
        if (*after != ':') {
            cursor = after;
            continue;
        }
        ++after;
        while (*after != '\0' && isspace((unsigned char)*after)) {
            ++after;
        }
        return after;
    }
    return NULL;
}

static int hex_value(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
}

bool wb_json_get_string(const char *json, const char *key, char *out, size_t out_size) {
    const char *value = find_value(json, key);
    size_t used = 0;

    if (out == NULL || out_size == 0) {
        return false;
    }
    out[0] = '\0';
    if (value == NULL || *value != '"') {
        return false;
    }
    ++value;
    while (*value != '\0' && *value != '"') {
        unsigned char ch = (unsigned char)*value++;
        if (ch == '\\') {
            ch = (unsigned char)*value++;
            switch (ch) {
                case '"': ch = '"'; break;
                case '\\': ch = '\\'; break;
                case '/': ch = '/'; break;
                case 'b': ch = '\b'; break;
                case 'f': ch = '\f'; break;
                case 'n': ch = '\n'; break;
                case 'r': ch = '\r'; break;
                case 't': ch = '\t'; break;
                case 'u': {
                    int h1 = hex_value(value[0]);
                    int h2 = hex_value(value[1]);
                    int h3 = hex_value(value[2]);
                    int h4 = hex_value(value[3]);
                    if (h1 < 0 || h2 < 0 || h3 < 0 || h4 < 0) {
                        return false;
                    }
                    unsigned code = (unsigned)((h1 << 12) | (h2 << 8) | (h3 << 4) | h4);
                    value += 4;
                    if (code < 0x80U) {
                        ch = (unsigned char)code;
                    } else {
                        ch = '?';
                    }
                    break;
                }
                default:
                    return false;
            }
        }
        if (used + 1 < out_size) {
            out[used++] = (char)ch;
        }
    }
    if (*value != '"') {
        return false;
    }
    out[used] = '\0';
    return true;
}

bool wb_json_get_i64(const char *json, const char *key, int64_t *out) {
    const char *value = find_value(json, key);
    char *end = NULL;
    long long parsed;
    if (value == NULL || out == NULL) return false;
    errno = 0;
    parsed = strtoll(value, &end, 10);
    if (errno != 0 || end == value) return false;
    *out = (int64_t)parsed;
    return true;
}

bool wb_json_get_double(const char *json, const char *key, double *out) {
    const char *value = find_value(json, key);
    char *end = NULL;
    double parsed;
    if (value == NULL || out == NULL) return false;
    errno = 0;
    parsed = strtod(value, &end);
    if (errno != 0 || end == value) return false;
    *out = parsed;
    return true;
}

bool wb_json_get_bool(const char *json, const char *key, bool *out) {
    const char *value = find_value(json, key);
    if (value == NULL || out == NULL) return false;
    if (strncmp(value, "true", 4) == 0) {
        *out = true;
        return true;
    }
    if (strncmp(value, "false", 5) == 0) {
        *out = false;
        return true;
    }
    return false;
}

bool wb_json_get_object(const char *json, const char *key, char *out, size_t out_size) {
    const char *value = find_value(json, key);
    const char *cursor;
    size_t used = 0;
    unsigned depth = 0;
    bool in_string = false;
    bool escaped = false;

    if (out == NULL || out_size == 0) return false;
    out[0] = '\0';
    if (value == NULL || *value != '{') return false;

    cursor = value;
    while (*cursor != '\0') {
        char ch = *cursor++;
        if (used + 1 >= out_size) {
            out[0] = '\0';
            return false;
        }
        out[used++] = ch;

        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                in_string = false;
            }
            continue;
        }
        if (ch == '"') {
            in_string = true;
            continue;
        }
        if (ch == '{') {
            depth += 1U;
        } else if (ch == '}') {
            if (depth == 0U) {
                out[0] = '\0';
                return false;
            }
            depth -= 1U;
            if (depth == 0U) {
                out[used] = '\0';
                return true;
            }
        }
    }
    out[0] = '\0';
    return false;
}

size_t wb_json_escape(const char *input, char *output, size_t output_size) {
    size_t used = 0;
    const unsigned char *cursor = (const unsigned char *)(input == NULL ? "" : input);
    if (output == NULL || output_size == 0) return 0;
    while (*cursor != '\0') {
        char escaped[7];
        const char *piece = escaped;
        size_t piece_len;
        unsigned char ch = *cursor++;
        switch (ch) {
            case '"': strcpy(escaped, "\\\""); break;
            case '\\': strcpy(escaped, "\\\\"); break;
            case '\b': strcpy(escaped, "\\b"); break;
            case '\f': strcpy(escaped, "\\f"); break;
            case '\n': strcpy(escaped, "\\n"); break;
            case '\r': strcpy(escaped, "\\r"); break;
            case '\t': strcpy(escaped, "\\t"); break;
            default:
                if (ch < 0x20U) {
                    (void)snprintf(escaped, sizeof(escaped), "\\u%04x", (unsigned)ch);
                } else {
                    escaped[0] = (char)ch;
                    escaped[1] = '\0';
                }
                break;
        }
        piece_len = strlen(piece);
        if (used + piece_len + 1 >= output_size) break;
        memcpy(output + used, piece, piece_len);
        used += piece_len;
    }
    output[used] = '\0';
    return used;
}

int wb_send_all(int fd, const char *data, size_t length) {
    size_t sent = 0;
    while (sent < length) {
        ssize_t result = send(fd, data + sent, length - sent, MSG_NOSIGNAL);
        if (result < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        if (result == 0) return -1;
        sent += (size_t)result;
    }
    return 0;
}

int wb_send_line(int fd, const char *line) {
    size_t length = strlen(line);
    if (wb_send_all(fd, line, length) != 0) return -1;
    return wb_send_all(fd, "\n", 1);
}

int wb_send_error_reply(int fd, int64_t request_id, const char *error) {
    char escaped[2048];
    char line[2304];
    wb_json_escape(error, escaped, sizeof(escaped));
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"reply_to\":%lld,\"ok\":false,\"error\":\"%s\"}",
        WB_PROTOCOL_VERSION,
        (long long)request_id,
        escaped
    );
    return wb_send_line(fd, line);
}

int wb_send_simple_reply(int fd, int64_t request_id, const char *result_json) {
    char line[4096];
    (void)snprintf(
        line,
        sizeof(line),
        "{\"version\":%d,\"reply_to\":%lld,\"ok\":true,\"result\":%s}",
        WB_PROTOCOL_VERSION,
        (long long)request_id,
        result_json == NULL ? "null" : result_json
    );
    return wb_send_line(fd, line);
}
