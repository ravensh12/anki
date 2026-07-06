// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

// C interface to the Anki backend for the Ante iOS companion.
// Built from rslib/ios-ffi (see `just ios-engine`); consumed by
// ios/Ante/EngineClient.swift via AnkiEngine.xcframework.

#ifndef ANKI_ENGINE_H
#define ANKI_ENGINE_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

// Opaque backend handle.
typedef struct AnteBackend AnteBackend;

// An owned byte buffer returned by the engine. Release with ante_bytes_free.
typedef struct AnteBytes {
    uint8_t *data;
    size_t len;
    size_t cap;
} AnteBytes;

// Result codes for ante_backend_run.
enum {
    ANTE_OK = 0,
    // out holds a serialized anki.backend.BackendError
    ANTE_ERR_BACKEND = 1,
    // null pointers or an internal panic; out is empty
    ANTE_ERR_INVALID = 2,
};

// Build hash of the embedded engine (static string; do not free).
const char *ante_buildhash(void);

// Open a backend from a serialized anki.backend.BackendInit message.
// Returns NULL if the message fails to decode.
AnteBackend *ante_backend_open(const uint8_t *init_bytes, size_t init_len);

// Run one service method over the protobuf seam
// (Backend::run_service_method). The caller owns *out and must call
// ante_bytes_free on it, on success and on error alike.
int32_t ante_backend_run(AnteBackend *backend, uint32_t service,
                         uint32_t method, const uint8_t *input,
                         size_t input_len, AnteBytes *out);

// Release a buffer produced by ante_backend_run.
void ante_bytes_free(AnteBytes bytes);

// Close a backend opened with ante_backend_open.
void ante_backend_close(AnteBackend *backend);

#ifdef __cplusplus
}
#endif

#endif // ANKI_ENGINE_H
