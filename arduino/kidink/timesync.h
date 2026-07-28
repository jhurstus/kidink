// Parse the `/time` endpoint's local-timestamp body.
//
// The daily clock-sync wake (specs/firmware.md §5) GETs a plain-text
// "YYYY-MM-DD HH:MM:SS" stamp already in the board's own timezone and writes
// it straight into the RTC via setDate()/setTime() - no epoch and no timezone
// math on the device, because the server already did both.
//
// Pure C++ (no Arduino.h) so the host harness can cover it.
#pragma once

// One local wall-clock instant, as served by /time.
struct LocalTimestamp
{
    int year;   // full year, e.g. 2026
    int month;  // 1-12
    int day;    // 1-31, validated against the real calendar
    int hour;   // 0-23
    int minute; // 0-59
    int second; // 0-59
};

// Parse "YYYY-MM-DD HH:MM:SS" (trailing whitespace tolerated - the body ends
// in a newline). Returns false on any malformed field, an impossible calendar
// date, or a year outside 2020-2099: the RTC stores two-digit years, and a
// wildly wrong stamp would arm the next alarm years away.
bool parseLocalTimestamp(const char *text, LocalTimestamp *out);

// tm_wday-convention weekday (0 = Sunday) for the stamp's date. The RTC's
// weekday register must hold this convention: getEpoch() reads it as tm_wday,
// and the alarm comparator matches the weekday field too (specs/firmware.md
// §8 quirks 4 and 6), so a wrong value silently stops scheduled wakes.
int localTimestampWeekday(const LocalTimestamp &stamp);
