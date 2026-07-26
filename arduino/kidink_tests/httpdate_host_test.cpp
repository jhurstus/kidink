// Host harness for arduino/kidink/httpdate.cpp, driven by
// server/app/firmware/test_httpdate_cpp.py. Reads "input<TAB>expected" lines on
// stdin, where expected is a decimal UTC epoch or "INVALID", and prints one
// OK/FAIL line per case.
//
// Sibling directory, not under arduino/kidink/: arduino-cli compiles every
// source in the sketch folder and a second main() would collide.

#include "../kidink/httpdate.cpp"

#include <stdio.h>
#include <stdlib.h>
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

        time_t epoch = 0;
        const bool ok = parseHttpDate(input, &epoch);

        if (strcmp(expected, "INVALID") == 0)
        {
            if (ok)
            {
                printf("FAIL %s: expected rejection, got %lld\n", input,
                       (long long)epoch);
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
        const long long want = atoll(expected);
        if ((long long)epoch != want)
        {
            printf("FAIL %s: expected %lld, got %lld\n", input, want,
                   (long long)epoch);
            ++failures;
            continue;
        }
        printf("OK\n");
    }
    return failures == 0 ? 0 : 1;
}
