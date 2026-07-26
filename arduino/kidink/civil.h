// Proleptic Gregorian calendar arithmetic (Howard Hinnant's algorithms).
//
// Shared by cron.cpp (advancing wall-clock days without timezone state) and
// httpdate.cpp (turning a GMT stamp into a UTC epoch). Deliberately free of
// mktime/timegm: newlib's mktime applies the TZ rules, which is exactly what
// neither caller wants, and timegm is not portable.
#pragma once

#include <stdint.h>

namespace kidink
{

// Days since 1970-01-01 for a proleptic Gregorian date (month 1-12, day 1-31).
inline int32_t daysFromCivil(int32_t y, int32_t m, int32_t d)
{
    y -= m <= 2;
    const int32_t era = (y >= 0 ? y : y - 399) / 400;
    const uint32_t yoe = (uint32_t)(y - era * 400);
    const uint32_t doy = (uint32_t)((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1);
    const uint32_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + (int32_t)doe - 719468;
}

// Inverse of daysFromCivil.
inline void civilFromDays(int32_t z, int32_t *y, int32_t *m, int32_t *d)
{
    z += 719468;
    const int32_t era = (z >= 0 ? z : z - 146096) / 146097;
    const uint32_t doe = (uint32_t)(z - era * 146097);
    const uint32_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const int32_t yr = (int32_t)yoe + era * 400;
    const uint32_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const uint32_t mp = (5 * doy + 2) / 153;
    *d = (int32_t)(doy - (153 * mp + 2) / 5 + 1);
    *m = (int32_t)(mp + (mp < 10 ? 3 : -9));
    *y = yr + (*m <= 2);
}

// Weekday for a day number, 0 = Sunday. 1970-01-01 was a Thursday.
inline int32_t weekdayFromDays(int32_t z)
{
    const int32_t w = (z + 4) % 7;
    return w < 0 ? w + 7 : w;
}

} // namespace kidink
