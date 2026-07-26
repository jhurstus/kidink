// Parse an HTTP `Date:` header into a UTC epoch.
//
// The board has no NTP client and no battery-backed clock it can trust on first
// boot, but every response from the kidink server already carries an accurate
// timestamp. Reading it costs nothing and is good to the second, which is far
// more than a wake schedule needs.
//
// Pure C++ (no Arduino.h) so the host harness can cover it.
#pragma once

#include <time.h>

// Parse an RFC 9110 IMF-fixdate, e.g. "Sat, 25 Jul 2026 18:02:38 GMT". Returns
// false for the obsolete RFC 850 and asctime forms, a non-GMT zone, or any
// malformed input. The day-of-week is not validated against the date: servers
// get it right, and disagreeing with a good timestamp helps nobody.
bool parseHttpDate(const char *value, time_t *outUtcEpoch);
