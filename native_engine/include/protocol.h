#ifndef WB_PROTOCOL_H
#define WB_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define WB_PROTOCOL_VERSION 1
#define WB_MAX_MESSAGE_BYTES (1024U * 1024U)

bool wb_json_get_string(const char *json, const char *key, char *out, size_t out_size);
bool wb_json_get_i64(const char *json, const char *key, int64_t *out);
bool wb_json_get_double(const char *json, const char *key, double *out);
bool wb_json_get_bool(const char *json, const char *key, bool *out);
bool wb_json_get_object(const char *json, const char *key, char *out, size_t out_size);
size_t wb_json_escape(const char *input, char *output, size_t output_size);
int wb_send_all(int fd, const char *data, size_t length);
int wb_send_line(int fd, const char *line);
int wb_send_error_reply(int fd, int64_t request_id, const char *error);
int wb_send_simple_reply(int fd, int64_t request_id, const char *result_json);

#endif
