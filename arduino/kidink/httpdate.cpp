#include "httpdate.h"

#include "civil.h"

#include <stdint.h>
#include <string.h>

namespace
{

const char *const kMonths[] = {"Jan", "Feb", "Mar", "Apr", "May", "Jun",
                               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"};

bool twoDigits(const char *p, int *out)
{
    if (p[0] < '0' || p[0] > '9' || p[1] < '0' || p[1] > '9')
        return false;
    *out = (p[0] - '0') * 10 + (p[1] - '0');
    return true;
}

bool fourDigits(const char *p, int *out)
{
    int value = 0;
    for (int i = 0; i < 4; ++i)
    {
        if (p[i] < '0' || p[i] > '9')
            return false;
        value = value * 10 + (p[i] - '0');
    }
    *out = value;
    return true;
}

} // namespace

bool parseHttpDate(const char *value, time_t *outUtcEpoch)
{
    if (!value || !outUtcEpoch)
        return false;
    while (*value == ' ')
        ++value;
    // IMF-fixdate is fixed width: "Www, DD Mmm YYYY HH:MM:SS GMT".
    if (strlen(value) < 29)
        return false;
    if (value[3] != ',' || value[4] != ' ' || value[7] != ' ' || value[11] != ' ' ||
        value[16] != ' ' || value[19] != ':' || value[22] != ':' || value[25] != ' ')
        return false;
    if (strncmp(value + 26, "GMT", 3) != 0)
        return false;

    int day = 0, year = 0, hour = 0, minute = 0, second = 0;
    if (!twoDigits(value + 5, &day) || !fourDigits(value + 12, &year) ||
        !twoDigits(value + 17, &hour) || !twoDigits(value + 20, &minute) ||
        !twoDigits(value + 23, &second))
        return false;

    int month = 0;
    for (int i = 0; i < 12; ++i)
    {
        if (strncmp(value + 8, kMonths[i], 3) == 0)
        {
            month = i + 1;
            break;
        }
    }
    if (month == 0)
        return false;
    if (day < 1 || day > 31 || hour > 23 || minute > 59 || second > 60)
        return false;
    // A sanity window, not a correctness check: a wildly wrong clock would set
    // an RTC alarm years away and effectively brick the wake cycle.
    if (year < 2020 || year > 2100)
        return false;

    const int32_t days = kidink::daysFromCivil(year, month, day);
    // Leap seconds are reported as :60; clamp so the epoch stays monotonic.
    if (second > 59)
        second = 59;
    *outUtcEpoch =
        (time_t)((int64_t)days * 86400 + hour * 3600 + minute * 60 + second);
    return true;
}
