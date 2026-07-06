// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

// Smoke test for the AnkiEngine C ABI, run on the host via
// `just ios-engine-smoke`: proves a plain C caller (i.e. Swift) can open a
// backend, open a collection, run a service method over the protobuf seam,
// and shut down cleanly — no Xcode required.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "anki_engine.h"

// Append a length-delimited protobuf field (wire type 2). Lengths here are
// tiny, so a single-byte varint is enough.
static size_t emit_str(uint8_t *buf, size_t off, int field, const char *s) {
    size_t len = strlen(s);
    buf[off++] = (uint8_t)((field << 3) | 2);
    buf[off++] = (uint8_t)len;
    memcpy(buf + off, s, len);
    return off + len;
}

int main(void) {
    printf("engine buildhash: %s\n", ante_buildhash());

    // BackendInit { preferred_langs: ["en"] }
    const uint8_t init[] = {0x0A, 0x02, 'e', 'n'};
    AnteBackend *backend = ante_backend_open(init, sizeof(init));
    if (!backend) {
        fprintf(stderr, "FAIL: backend did not open\n");
        return 1;
    }

    char dir[] = "/tmp/ante_smoke_XXXXXX";
    if (!mkdtemp(dir)) {
        fprintf(stderr, "FAIL: mkdtemp\n");
        return 1;
    }
    char col[256], media[256], mediadb[256];
    snprintf(col, sizeof(col), "%s/c.anki2", dir);
    snprintf(media, sizeof(media), "%s/media", dir);
    snprintf(mediadb, sizeof(mediadb), "%s/media.db", dir);

    // OpenCollectionRequest { collection_path=1, media_folder_path=2, media_db_path=3 }
    uint8_t req[1024];
    size_t off = 0;
    off = emit_str(req, off, 1, col);
    off = emit_str(req, off, 2, media);
    off = emit_str(req, off, 3, mediadb);

    AnteBytes out;
    // CollectionService=3, OpenCollection=0 (pinned in rslib/ios-ffi tests)
    int32_t code = ante_backend_run(backend, 3, 0, req, off, &out);
    ante_bytes_free(out);
    if (code != ANTE_OK) {
        fprintf(stderr, "FAIL: open_collection returned %d\n", code);
        return 1;
    }

    // SchedulerService=13, GetTopicMastery=39; empty input = proto defaults
    code = ante_backend_run(backend, 13, 39, NULL, 0, &out);
    if (code != ANTE_OK) {
        fprintf(stderr, "FAIL: get_topic_mastery returned %d\n", code);
        return 1;
    }
    printf("get_topic_mastery: %zu response bytes\n", out.len);
    ante_bytes_free(out);

    // CloseCollection=1; empty input = don't downgrade
    code = ante_backend_run(backend, 3, 1, NULL, 0, &out);
    ante_bytes_free(out);
    if (code != ANTE_OK) {
        fprintf(stderr, "FAIL: close_collection returned %d\n", code);
        return 1;
    }

    ante_backend_close(backend);
    printf("smoke ok\n");
    return 0;
}
