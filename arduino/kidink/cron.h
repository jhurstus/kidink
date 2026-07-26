// Crontab parsing and next-fire evaluation for the kidink firmware.
//
// Pure C++ on purpose: no Arduino.h, no I/O, and no mktime/localtime inside, so
// this compiles unchanged on the host and is covered by the same conformance
// table as its Python twin (server/app/firmware/cron.py, cron_cases.py).
// Everything here is naive local wall-clock arithmetic; resolving a wall clock
// to an instant - where DST ambiguity lives - is the caller's job.
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <time.h>

struct CronSpec
{
    uint64_t minute; // bit i set => minute i matches (0..59)
    uint32_t hour;   // bit i set => hour i matches (0..23)
    uint32_t dom;    // bit i set => day-of-month i matches (1..31)
    uint16_t month;  // bit i set => month i matches (1..12)
    uint8_t dow;     // bit i set => weekday i matches (0=Sunday..6)
    bool domStar;    // the raw day-of-month field began with '*'
    bool dowStar;    // the raw day-of-week field began with '*'
};

// Parse a 5-field crontab expression (or an @macro). On failure returns false
// and writes a message into err. Supports '*', N, A-B, comma lists, '*/S',
// 'A-B/S', 'A/S', JAN-DEC and SUN-SAT names, and day-of-week 7 as Sunday.
bool cronParse(const char *expr, CronSpec *out, char *err, size_t errLen);

// First wall-clock minute strictly after `after` that matches `spec`. `after`
// and `*out` use the struct tm fields tm_year/tm_mon/tm_mday/tm_hour/tm_min;
// `out` also gets tm_sec = 0, a correct tm_wday, and tm_isdst = -1 so the
// caller's mktime() resolves the offset. Returns false if nothing matches
// within four years (e.g. "0 0 30 2 *").
bool cronNext(const CronSpec &spec, const struct tm &after, struct tm *out);
