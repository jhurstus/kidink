#include "cron.h"

#include "civil.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

namespace
{

// Four years covers the worst legal case, "Feb 29" starting from March of a
// leap year. Beyond that an expression is treated as unsatisfiable.
const int32_t kSearchLimitDays = 366 * 4 + 1;

const char *const kMonthNames[] = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                                   "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"};
const char *const kDowNames[] = {"SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"};

struct Macro
{
    const char *name;
    const char *expansion;
};

const Macro kMacros[] = {
    {"@hourly", "0 * * * *"},   {"@daily", "0 0 * * *"},   {"@midnight", "0 0 * * *"},
    {"@weekly", "0 0 * * 0"},   {"@monthly", "0 0 1 * *"}, {"@yearly", "0 0 1 1 *"},
    {"@annually", "0 0 1 1 *"},
};

void setError(char *err, size_t errLen, const char *field, const char *detail)
{
    if (err && errLen)
        snprintf(err, errLen, "%s: %s", field, detail);
}

char upperAscii(char c)
{
    return (c >= 'a' && c <= 'z') ? (char)(c - 'a' + 'A') : c;
}

bool equalsIgnoreCase(const char *a, const char *b)
{
    while (*a && *b)
    {
        if (upperAscii(*a) != upperAscii(*b))
            return false;
        ++a;
        ++b;
    }
    return *a == *b;
}

using kidink::civilFromDays;
using kidink::daysFromCivil;
using kidink::weekdayFromDays;

// --- Field parsing --------------------------------------------------------

bool parseValue(const char *token, size_t len, int lo, int hi, const char *const *names,
                int nameCount, int *out, char *err, size_t errLen, const char *field)
{
    char buf[16];
    if (len == 0 || len >= sizeof(buf))
    {
        setError(err, errLen, field, "empty or overlong value");
        return false;
    }
    memcpy(buf, token, len);
    buf[len] = '\0';

    for (int i = 0; i < nameCount; ++i)
    {
        if (equalsIgnoreCase(buf, names[i]))
        {
            // Names index from the field minimum: months are 1-based, weekdays 0.
            *out = (names == kMonthNames) ? i + 1 : i;
            return true;
        }
    }
    for (size_t i = 0; i < len; ++i)
    {
        if (buf[i] < '0' || buf[i] > '9')
        {
            setError(err, errLen, field, "not a number or known name");
            return false;
        }
    }
    const long value = strtol(buf, NULL, 10);
    if (value < lo || value > hi)
    {
        setError(err, errLen, field, "value out of range");
        return false;
    }
    *out = (int)value;
    return true;
}

bool parseField(const char *text, size_t len, int lo, int hi, const char *const *names,
                int nameCount, uint64_t *mask, char *err, size_t errLen,
                const char *field)
{
    *mask = 0;
    if (len == 0)
    {
        setError(err, errLen, field, "empty field");
        return false;
    }
    size_t pos = 0;
    while (pos <= len)
    {
        size_t end = pos;
        while (end < len && text[end] != ',')
            ++end;
        const size_t partLen = end - pos;
        if (partLen == 0)
        {
            setError(err, errLen, field, "empty list element");
            return false;
        }
        const char *part = text + pos;

        // Split the element into body and optional "/step".
        size_t slash = 0;
        while (slash < partLen && part[slash] != '/')
            ++slash;
        const bool hasStep = slash < partLen;
        int step = 1;
        if (hasStep)
        {
            // A step is a bare positive integer, unbounded by the field's own
            // range: "*/90" on minutes is legal and simply yields {0}.
            const size_t stepLen = partLen - slash - 1;
            if (stepLen == 0 || stepLen > 4)
            {
                setError(err, errLen, field, "invalid step");
                return false;
            }
            int parsed = 0;
            for (size_t i = 0; i < stepLen; ++i)
            {
                const char c = part[slash + 1 + i];
                if (c < '0' || c > '9')
                {
                    setError(err, errLen, field, "invalid step");
                    return false;
                }
                parsed = parsed * 10 + (c - '0');
            }
            if (parsed == 0)
            {
                setError(err, errLen, field, "invalid step");
                return false;
            }
            step = parsed;
        }
        const size_t bodyLen = slash;
        const char *body = part;

        int start = 0;
        int stop = 0;
        if (bodyLen == 1 && body[0] == '*')
        {
            start = lo;
            stop = hi;
        }
        else
        {
            // Look for a range separator past index 0, so a leading '-' stays an
            // error rather than splitting into an empty low bound.
            size_t dash = 1;
            while (dash < bodyLen && body[dash] != '-')
                ++dash;
            if (dash < bodyLen)
            {
                if (!parseValue(body, dash, lo, hi, names, nameCount, &start, err,
                                errLen, field) ||
                    !parseValue(body + dash + 1, bodyLen - dash - 1, lo, hi, names,
                                nameCount, &stop, err, errLen, field))
                    return false;
                if (stop < start)
                {
                    setError(err, errLen, field, "range runs backwards");
                    return false;
                }
            }
            else
            {
                if (!parseValue(body, bodyLen, lo, hi, names, nameCount, &start, err,
                                errLen, field))
                    return false;
                // Vixie extension: "N/S" runs N through the field maximum, while
                // a bare "N" is just that one value.
                stop = hasStep ? hi : start;
            }
        }
        for (int v = start; v <= stop; v += step)
            *mask |= (uint64_t)1 << v;

        if (end == len)
            break;
        pos = end + 1;
        if (pos == len)
        {
            setError(err, errLen, field, "empty list element");
            return false;
        }
    }
    return true;
}

bool dayMatches(const CronSpec &spec, int32_t day, int32_t dow)
{
    // Vixie's rule: when either day field is starred the two are ANDed (so the
    // starred one is usually a no-op); when neither is, they are ORed - which is
    // why "0 0 13 * FRI" fires on the 13th *or* on any Friday.
    const bool domOk = (spec.dom & ((uint32_t)1 << day)) != 0;
    const bool dowOk = (spec.dow & ((uint8_t)1 << dow)) != 0;
    if (spec.domStar || spec.dowStar)
        return domOk && dowOk;
    return domOk || dowOk;
}

} // namespace

bool cronParse(const char *expr, CronSpec *out, char *err, size_t errLen)
{
    if (err && errLen)
        err[0] = '\0';
    if (!expr || !out)
        return false;

    // Trim surrounding whitespace, then expand an @macro to its 5-field form.
    while (*expr == ' ' || *expr == '\t')
        ++expr;
    char expanded[64];
    size_t exprLen = strlen(expr);
    while (exprLen > 0 && (expr[exprLen - 1] == ' ' || expr[exprLen - 1] == '\t'))
        --exprLen;
    if (exprLen >= sizeof(expanded))
    {
        setError(err, errLen, "cron", "expression too long");
        return false;
    }
    memcpy(expanded, expr, exprLen);
    expanded[exprLen] = '\0';

    if (expanded[0] == '@')
    {
        const char *expansion = NULL;
        for (size_t i = 0; i < sizeof(kMacros) / sizeof(kMacros[0]); ++i)
        {
            if (equalsIgnoreCase(expanded, kMacros[i].name))
            {
                expansion = kMacros[i].expansion;
                break;
            }
        }
        if (!expansion)
        {
            setError(err, errLen, "cron", "unknown macro");
            return false;
        }
        strcpy(expanded, expansion);
    }

    // Split into exactly five whitespace-separated fields.
    const char *fields[5];
    size_t lengths[5];
    int count = 0;
    const char *p = expanded;
    while (*p)
    {
        while (*p == ' ' || *p == '\t')
            ++p;
        if (!*p)
            break;
        const char *start = p;
        while (*p && *p != ' ' && *p != '\t')
            ++p;
        if (count == 5)
        {
            setError(err, errLen, "cron", "expected 5 fields");
            return false;
        }
        fields[count] = start;
        lengths[count] = (size_t)(p - start);
        ++count;
    }
    if (count != 5)
    {
        setError(err, errLen, "cron", "expected 5 fields");
        return false;
    }

    // Quartz extensions we deliberately do not implement.
    for (int i = 0; i < 5; ++i)
    {
        for (size_t j = 0; j < lengths[i]; ++j)
        {
            const char c = upperAscii(fields[i][j]);
            if (c == 'L' || c == 'W' || c == '#' || c == '?')
            {
                setError(err, errLen, "cron", "unsupported character");
                return false;
            }
        }
    }

    uint64_t minute = 0, hour = 0, dom = 0, month = 0, dowRaw = 0;
    if (!parseField(fields[0], lengths[0], 0, 59, NULL, 0, &minute, err, errLen,
                    "minute") ||
        !parseField(fields[1], lengths[1], 0, 23, NULL, 0, &hour, err, errLen, "hour") ||
        !parseField(fields[2], lengths[2], 1, 31, NULL, 0, &dom, err, errLen,
                    "day-of-month") ||
        !parseField(fields[3], lengths[3], 1, 12, kMonthNames, 12, &month, err, errLen,
                    "month") ||
        !parseField(fields[4], lengths[4], 0, 7, kDowNames, 7, &dowRaw, err, errLen,
                    "day-of-week"))
        return false;

    // 7 is an alias for Sunday, so fold it down after parsing over 0-7.
    if (dowRaw & ((uint64_t)1 << 7))
        dowRaw |= 1;

    out->minute = minute;
    out->hour = (uint32_t)hour;
    out->dom = (uint32_t)dom;
    out->month = (uint16_t)month;
    out->dow = (uint8_t)(dowRaw & 0x7F);
    out->domStar = fields[2][0] == '*';
    out->dowStar = fields[4][0] == '*';
    return true;
}

bool cronNext(const CronSpec &spec, const struct tm &after, struct tm *out)
{
    if (!out)
        return false;

    int32_t year = after.tm_year + 1900;
    int32_t month = after.tm_mon + 1;
    int32_t day = after.tm_mday;
    int32_t hour = after.tm_hour;
    int32_t minute = after.tm_min;

    int32_t serial = daysFromCivil(year, month, day);
    const int32_t limit = serial + kSearchLimitDays;

    // Strictly after: step one minute past the starting stamp, discarding
    // seconds (a match at HH:MM:30 still belongs to minute HH:MM).
    if (++minute > 59)
    {
        minute = 0;
        if (++hour > 23)
        {
            hour = 0;
            ++serial;
        }
    }

    while (serial <= limit)
    {
        civilFromDays(serial, &year, &month, &day);

        if (!(spec.month & ((uint16_t)1 << month)))
        {
            // Jump to the first instant of the next month.
            const int32_t nextYear = month == 12 ? year + 1 : year;
            const int32_t nextMonth = month == 12 ? 1 : month + 1;
            serial = daysFromCivil(nextYear, nextMonth, 1);
            hour = 0;
            minute = 0;
            continue;
        }
        if (!dayMatches(spec, day, weekdayFromDays(serial)))
        {
            ++serial;
            hour = 0;
            minute = 0;
            continue;
        }
        if (!(spec.hour & ((uint32_t)1 << hour)))
        {
            minute = 0;
            if (++hour > 23)
            {
                hour = 0;
                ++serial;
            }
            continue;
        }
        if (!(spec.minute & ((uint64_t)1 << minute)))
        {
            if (++minute > 59)
            {
                minute = 0;
                if (++hour > 23)
                {
                    hour = 0;
                    ++serial;
                }
            }
            continue;
        }

        memset(out, 0, sizeof(*out));
        out->tm_year = year - 1900;
        out->tm_mon = month - 1;
        out->tm_mday = day;
        out->tm_hour = hour;
        out->tm_min = minute;
        out->tm_sec = 0;
        out->tm_wday = weekdayFromDays(serial);
        out->tm_isdst = -1;
        return true;
    }
    return false;
}
