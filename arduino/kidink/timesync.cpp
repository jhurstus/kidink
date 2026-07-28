#include "timesync.h"

#include "civil.h"

#include <stdint.h>
#include <string.h>

namespace
{

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

bool parseLocalTimestamp(const char *text, LocalTimestamp *out)
{
    if (!text || !out)
        return false;
    while (*text == ' ')
        ++text;
    // Fixed width: "YYYY-MM-DD HH:MM:SS".
    if (strlen(text) < 19)
        return false;
    if (text[4] != '-' || text[7] != '-' || text[10] != ' ' || text[13] != ':' ||
        text[16] != ':')
        return false;
    // Only whitespace may follow - HTTPClient hands over the body verbatim,
    // newline included.
    for (const char *p = text + 19; *p; ++p)
    {
        if (*p != ' ' && *p != '\t' && *p != '\r' && *p != '\n')
            return false;
    }

    LocalTimestamp stamp = {};
    if (!fourDigits(text, &stamp.year) || !twoDigits(text + 5, &stamp.month) ||
        !twoDigits(text + 8, &stamp.day) || !twoDigits(text + 11, &stamp.hour) ||
        !twoDigits(text + 14, &stamp.minute) || !twoDigits(text + 17, &stamp.second))
        return false;
    if (stamp.month < 1 || stamp.month > 12 || stamp.day < 1 || stamp.hour > 23 ||
        stamp.minute > 59 || stamp.second > 59)
        return false;
    // The same sanity window as the Date header (httpdate.cpp), capped at 2099
    // because setDate() stores the year as two digits.
    if (stamp.year < 2020 || stamp.year > 2099)
        return false;
    // Round-trip through the civil-day number to reject Feb 30 and friends.
    int32_t y = 0, m = 0, d = 0;
    kidink::civilFromDays(kidink::daysFromCivil(stamp.year, stamp.month, stamp.day),
                          &y, &m, &d);
    if (y != stamp.year || m != stamp.month || d != stamp.day)
        return false;

    *out = stamp;
    return true;
}

int localTimestampWeekday(const LocalTimestamp &stamp)
{
    return (int)kidink::weekdayFromDays(
        kidink::daysFromCivil(stamp.year, stamp.month, stamp.day));
}
