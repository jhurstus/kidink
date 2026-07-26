// Serial logging for the kidink firmware.
//
// One line per stage, all prefixed "[kidink]", so a serial capture of a boot
// reads as a trace. Secrets never appear: the Wi-Fi passphrase is never logged
// in any form (not even its length), and URLs are truncated at the query string
// so a future token-bearing path cannot leak (CLAUDE.md).
#pragma once

#include <Arduino.h>

#define KIDINK_LOGF(fmt, ...) Serial.printf("[kidink] " fmt "\n", ##__VA_ARGS__)

// Log a URL with its query string replaced by "?...".
inline void kidinkLogUrl(const char *label, const char *url)
{
    const char *query = strchr(url, '?');
    if (query)
        Serial.printf("[kidink] %s %.*s?...\n", label, (int)(query - url), url);
    else
        Serial.printf("[kidink] %s %s\n", label, url);
}
