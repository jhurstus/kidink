// Host harness for arduino/kidink/cron.cpp.
//
// Compiled and driven by server/app/firmware/test_cron_cpp.py, which feeds it
// the shared conformance table from cron_cases.py - the same rows the Python
// reference is checked against. Reads tab-separated
// "expression<TAB>after<TAB>expected" lines on stdin (expected is an ISO stamp,
// "NONE" for an expression that never fires, or "INVALID" for one the parser
// must reject) and prints one OK/FAIL line per case.
//
// This lives in a sibling directory rather than under arduino/kidink/ because
// arduino-cli compiles every source in the sketch folder, and a second main()
// would collide at link time.

#include "../kidink/cron.cpp"

#include <stdio.h>
#include <string.h>

namespace
{

bool parseStamp(const char *text, struct tm *out)
{
    int year = 0, month = 0, day = 0, hour = 0, minute = 0, second = 0;
    if (sscanf(text, "%d-%d-%dT%d:%d:%d", &year, &month, &day, &hour, &minute,
               &second) != 6)
        return false;
    memset(out, 0, sizeof(*out));
    out->tm_year = year - 1900;
    out->tm_mon = month - 1;
    out->tm_mday = day;
    out->tm_hour = hour;
    out->tm_min = minute;
    out->tm_sec = second;
    out->tm_isdst = -1;
    return true;
}

void formatStamp(const struct tm &value, char *out, size_t outLen)
{
    snprintf(out, outLen, "%04d-%02d-%02dT%02d:%02d:%02d", value.tm_year + 1900,
             value.tm_mon + 1, value.tm_mday, value.tm_hour, value.tm_min, value.tm_sec);
}

} // namespace

int main()
{
    char line[512];
    int failures = 0;
    while (fgets(line, sizeof(line), stdin))
    {
        size_t len = strlen(line);
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = '\0';
        if (len == 0)
            continue;

        char *afterText = strchr(line, '\t');
        if (!afterText)
        {
            printf("FAIL malformed input line\n");
            ++failures;
            continue;
        }
        *afterText++ = '\0';
        char *expected = strchr(afterText, '\t');
        if (!expected)
        {
            printf("FAIL malformed input line\n");
            ++failures;
            continue;
        }
        *expected++ = '\0';
        const char *expr = line;

        CronSpec spec;
        char err[128];
        const bool parsed = cronParse(expr, &spec, err, sizeof(err));

        if (strcmp(expected, "INVALID") == 0)
        {
            if (parsed)
            {
                printf("FAIL %s: expected a parse error, got a valid spec\n", expr);
                ++failures;
            }
            else
            {
                printf("OK\n");
            }
            continue;
        }

        if (!parsed)
        {
            printf("FAIL %s: unexpected parse error (%s)\n", expr, err);
            ++failures;
            continue;
        }

        struct tm after;
        if (!parseStamp(afterText, &after))
        {
            printf("FAIL %s: could not parse after=%s\n", expr, afterText);
            ++failures;
            continue;
        }

        struct tm result;
        const bool found = cronNext(spec, after, &result);
        if (strcmp(expected, "NONE") == 0)
        {
            if (found)
            {
                char got[32];
                formatStamp(result, got, sizeof(got));
                printf("FAIL %s after %s: expected no fire, got %s\n", expr, afterText,
                       got);
                ++failures;
            }
            else
            {
                printf("OK\n");
            }
            continue;
        }

        if (!found)
        {
            printf("FAIL %s after %s: expected %s, got no fire\n", expr, afterText,
                   expected);
            ++failures;
            continue;
        }
        char got[32];
        formatStamp(result, got, sizeof(got));
        if (strcmp(got, expected) != 0)
        {
            printf("FAIL %s after %s: expected %s, got %s\n", expr, afterText, expected,
                   got);
            ++failures;
            continue;
        }
        // tm_wday must be consistent with the returned date, since the firmware
        // logs it and the RTC alarm is set from the same struct.
        struct tm check = result;
        const int32_t serial =
            daysFromCivil(check.tm_year + 1900, check.tm_mon + 1, check.tm_mday);
        if (weekdayFromDays(serial) != check.tm_wday)
        {
            printf("FAIL %s after %s: tm_wday %d inconsistent with date\n", expr,
                   afterText, check.tm_wday);
            ++failures;
            continue;
        }
        printf("OK\n");
    }
    return failures == 0 ? 0 : 1;
}
