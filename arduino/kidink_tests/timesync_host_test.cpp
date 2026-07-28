// Host harness for arduino/kidink/timesync.cpp, driven by
// server/app/firmware/test_timesync_cpp.py. Reads "input<TAB>expected" lines on
// stdin, where expected is "year month day hour minute second weekday" (weekday
// 0 = Sunday) or "INVALID", and prints one OK/FAIL line per case.
//
// Sibling directory, not under arduino/kidink/: arduino-cli compiles every
// source in the sketch folder and a second main() would collide.

#include "../kidink/timesync.cpp"

#include <stdio.h>
#include <string.h>

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

        char *expected = strchr(line, '\t');
        if (!expected)
        {
            printf("FAIL malformed input line\n");
            ++failures;
            continue;
        }
        *expected++ = '\0';
        const char *input = line;

        LocalTimestamp stamp = {};
        const bool ok = parseLocalTimestamp(input, &stamp);

        if (strcmp(expected, "INVALID") == 0)
        {
            if (ok)
            {
                printf("FAIL %s: expected rejection, got a parse\n", input);
                ++failures;
            }
            else
            {
                printf("OK\n");
            }
            continue;
        }
        if (!ok)
        {
            printf("FAIL %s: expected %s, got a parse error\n", input, expected);
            ++failures;
            continue;
        }
        char got[64];
        snprintf(got, sizeof(got), "%d %d %d %d %d %d %d", stamp.year, stamp.month,
                 stamp.day, stamp.hour, stamp.minute, stamp.second,
                 localTimestampWeekday(stamp));
        if (strcmp(got, expected) != 0)
        {
            printf("FAIL %s: expected %s, got %s\n", input, expected, got);
            ++failures;
            continue;
        }
        printf("OK\n");
    }
    return failures == 0 ? 0 : 1;
}
