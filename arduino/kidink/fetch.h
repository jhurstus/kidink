// Wi-Fi association and the conditional GET against the kidink server.
//
// The wire contract is fixed by specs/firmware.md §3: a 200 must carry exactly
// KIDINK_FRAME_BYTES of packed 4bpp pixels with a Content-Length, a 304 means
// the panel already shows the right image, and anything else is a failure that
// must not paint.
#pragma once

#include "config.h"

#include <stddef.h>
#include <stdint.h>
#include <time.h>

enum class FetchResult : uint8_t
{
    Fresh,       // 200 with a valid frame: the caller should paint it
    NotModified, // 304: the panel is already correct, skip the refresh
    Failed,      // anything else: do not paint
};

struct FetchOutcome
{
    FetchResult result;
    int httpStatus;              // 0 when the request never got a response
    char etag[KIDINK_ETAG_MAX];  // "" when absent or too long to store
    time_t serverDate;           // 0 when absent or unparseable
    const char *failure;         // static description, nullptr on success
};

// Associate with the access point, giving up after `timeoutMs`.
bool kidinkWifiConnect(const char *ssid, const char *password, uint32_t timeoutMs);

// Drop the association and power down the radio before sleeping.
void kidinkWifiStop();

// Conditional GET. `ifNoneMatch` may be empty to force a full fetch. On
// FetchResult::Fresh exactly `frameBytes` validated bytes have been written to
// `frame`. `timeoutMs` bounds the whole exchange, not just one socket read.
FetchOutcome kidinkFetch(const char *url, const char *ifNoneMatch, uint8_t *frame,
                         size_t frameBytes, uint32_t timeoutMs);

// Unconditional GET of a small plain-text body (the `/time` clock-sync stamp).
// True only on a 200 whose body fit in `out` (always NUL-terminated on
// success); anything else logs and returns false without touching the RTC's
// caller. No streaming deadline machinery: the body is a couple dozen bytes,
// so the per-read socket timeout bounds the exchange.
bool kidinkFetchText(const char *url, uint32_t timeoutMs, char *out, size_t outSize);

// True when every nibble is an even value <= 0x0A, i.e. a legal palette index
// shifted left by one (eink-demo §3). Cheap insurance: the panel driver skips
// out-of-range colours silently, so bad bytes would show as stale pixels rather
// than as an error.
bool kidinkFrameLooksValid(const uint8_t *frame, size_t length);
