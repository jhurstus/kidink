// kidink - the kids' comic e-ink board, device side.
//
// One cycle per boot: wake (RTC alarm or the WAKE button), join Wi-Fi,
// conditionally GET the packed frame from the kidink server, paint only if the
// server sent one, arm the next wake, and deep sleep. The panel is bistable, so
// a 304 costs nothing and keeps the last image up - that is the whole battery
// story (spec §3.1).
//
// There are no retries: any failure just waits for the next scheduled wake.
//
// Build and flash from server/:  uv run python -m app.firmware
// Full design, wire contract, and library quirks: specs/firmware.md
//
// Adapted from the Inkplate Arduino library's
// Inkplate13SPECTRA_RTC_Alarm_With_Deep_Sleep and Inkpate13SPECTRA_Wake_Up_Button
// examples, Copyright (c) Soldered Electronics, LGPL-3.0 - see
// THIRD_PARTY_NOTICES.md at the repo root.
#ifndef ARDUINO_INKPLATE13SPECTRA
#error "Wrong board selection, please select Soldered Inkplate13SPECTRA in the boards menu."
#endif

#if !__has_include("config.h")
#error "config.h is missing. Generate it from server/: uv run python -m app.firmware --no-compile"
#endif

#include "Inkplate.h"

#include "config.h"
#include "cron.h"
#include "fetch.h"
#include "log.h"
#include "timesync.h"

#include <WiFi.h>
#include <time.h>

// The WAKE button and the PCF85063A's alarm interrupt share this pin, both
// active low - so one ext0 source covers a scheduled wake and a button press
// alike, and they are told apart by the RTC's alarm flag rather than by GPIO.
#define KIDINK_WAKE_GPIO GPIO_NUM_18

Inkplate display;

// Survives deep sleep (but not a reflash or a power cut), so the magic word is
// what tells a genuine resume from uninitialised RTC memory.
struct KidinkPersist
{
    uint32_t magic;
    uint32_t bootCount;
    char etag[KIDINK_ETAG_MAX];
    uint16_t consecutiveFailures;
    // The next armed alarm is the daily clock sync (§5), not a display fetch.
    uint8_t nextWakeIsClockSync;
};

// 'KIDL': bumped from 'KIDK' with the struct layout, so a block persisted by a
// pre-clock-sync firmware cannot validate against the new shape.
static const uint32_t kPersistMagic = 0x4B49444CUL;

RTC_DATA_ATTR KidinkPersist gPersist;

// ---------------------------------------------------------------------------

// Last-resort guard against never reaching deep sleep. The library's
// waitForBusy() spins forever on a panel fault, which on battery means a flat
// cell rather than a stale picture. Deliberately touches no I2C, so it cannot
// race the main task's Wire transactions.
static void bootDeadlineTask(void *)
{
    vTaskDelay(pdMS_TO_TICKS(KIDINK_BOOT_DEADLINE_MS));
    KIDINK_LOGF("watchdog: %dms boot deadline hit, forcing sleep",
                KIDINK_BOOT_DEADLINE_MS);
    Serial.flush();
    esp_sleep_enable_timer_wakeup((uint64_t)KIDINK_FALLBACK_SLEEP_S * 1000000ULL);
    esp_sleep_enable_ext0_wakeup(KIDINK_WAKE_GPIO, 0);
    esp_deep_sleep_start();
}

static void logWakeReason(bool wokeFromAlarm)
{
    switch (esp_sleep_get_wakeup_cause())
    {
    case ESP_SLEEP_WAKEUP_EXT0:
        KIDINK_LOGF("wake: %s", wokeFromAlarm ? "RTC alarm" : "WAKE button");
        break;
    case ESP_SLEEP_WAKEUP_TIMER:
        KIDINK_LOGF("wake: timer backstop (the RTC alarm did not fire)");
        break;
    default:
        KIDINK_LOGF("wake: cold boot or reset");
        break;
    }
}

// Seconds to sleep, and the wall-clock stamp it lands on. Returns 0 when the
// schedule cannot be computed, which happens only if the clock has never been
// set and the fetch that would have carried a Date header failed.
static uint32_t secondsUntilNextWake(const CronSpec &spec, time_t now, time_t *fireAt)
{
    struct tm local;
    localtime_r(&now, &local);

    struct tm next;
    if (!cronNext(spec, local, &next))
    {
        KIDINK_LOGF("schedule: '%s' never fires again", KIDINK_WAKE_CRON);
        return 0;
    }
    next.tm_isdst = -1; // let mktime resolve the offset, DST included
    time_t target = mktime(&next);

    // An RTC that drifts a second or two early would otherwise re-fire the same
    // cron minute and repaint immediately; skip ahead to the following match.
    if (target - now < KIDINK_MIN_SLEEP_S)
    {
        const time_t floorTime = now + KIDINK_MIN_SLEEP_S;
        struct tm floorLocal;
        localtime_r(&floorTime, &floorLocal);
        if (!cronNext(spec, floorLocal, &next))
            return 0;
        next.tm_isdst = -1;
        target = mktime(&next);
    }
    // The PCF85063A alarm matches day-of-month, so it cannot express a horizon
    // beyond about a month. A day keeps a comfortable margin.
    if (target - now > KIDINK_MAX_SLEEP_S)
        target = now + KIDINK_MAX_SLEEP_S;

    *fireAt = target;
    return (uint32_t)(target - now);
}

// The next daily clock-sync instant (§5): KIDINK_CLOCK_SYNC_HOUR:MINUTE local,
// today or tomorrow, at least MIN_SLEEP_S away so a just-finished sync cannot
// re-fire on the same minute. DST is resolved the same way as the cron path:
// mktime normalizes a nonexistent local time and picks a side for an ambiguous
// one.
static time_t nextClockSyncAfter(time_t now)
{
    struct tm local;
    localtime_r(&now, &local);
    local.tm_hour = KIDINK_CLOCK_SYNC_HOUR;
    local.tm_min = KIDINK_CLOCK_SYNC_MINUTE;
    local.tm_sec = 0;
    local.tm_isdst = -1;
    time_t target = mktime(&local);
    if (target - now < KIDINK_MIN_SLEEP_S)
    {
        local.tm_mday += 1; // mktime renormalizes the date
        local.tm_hour = KIDINK_CLOCK_SYNC_HOUR;
        local.tm_min = KIDINK_CLOCK_SYNC_MINUTE;
        local.tm_sec = 0;
        local.tm_isdst = -1;
        target = mktime(&local);
    }
    return target;
}

static void sleepUntil(uint32_t seconds)
{
    // The timer is armed alongside the RTC alarm as a backstop: it covers a
    // failed I2C write, a cleared RTC, or a dead backup cell. The ESP32's
    // RTC_SLOW_CLK is an internal RC oscillator with percent-level drift, so the
    // slack keeps the (accurate) alarm winning under normal conditions.
    const uint64_t backstop = (uint64_t)seconds * 115 / 100 + 300;
    esp_sleep_enable_timer_wakeup(backstop * 1000000ULL);
    esp_sleep_enable_ext0_wakeup(KIDINK_WAKE_GPIO, 0);

    // A held WAKE button would re-trigger ext0 the instant we sleep, spinning
    // the board through boot after boot.
    const uint32_t start = millis();
    while (digitalRead(KIDINK_WAKE_GPIO) == LOW && millis() - start < 10000)
        delay(50);

    KIDINK_LOGF("sleep: %us (timer backstop %llus)", (unsigned)seconds,
                (unsigned long long)backstop);
    Serial.flush();
    esp_deep_sleep_start();
}

// Arm the next wake - the earlier of the cron schedule and the daily clock
// sync (§5) - then deep sleep. Never returns. With a clock but no usable cron
// schedule the sync alone keeps the board waking daily; with no clock at all,
// the timer backstop retries shortly.
static void scheduleNextWake(bool clockValid, bool scheduleValid,
                             const CronSpec &schedule)
{
    if (clockValid)
    {
        // One read: every rtc getter re-reads I2C on its own, so reading hour
        // and minute separately can straddle a minute boundary.
        const time_t now = (time_t)display.rtc.getEpoch();

        time_t cronAt = 0;
        uint32_t cronSeconds = 0;
        if (scheduleValid)
            cronSeconds = secondsUntilNextWake(schedule, now, &cronAt);

        time_t syncAt = nextClockSyncAfter(now);
        // A fall-back DST day can stretch "tomorrow at sync time" past 24h;
        // clamp like the cron path does. The sync just runs up to an hour
        // early, once a year.
        if (syncAt - now > KIDINK_MAX_SLEEP_S)
            syncAt = now + KIDINK_MAX_SLEEP_S;

        // Display wakes win a tie: a fetch re-syncs the clock off the Date
        // header anyway, so a same-minute sync wake would add nothing.
        const bool syncNext = cronSeconds == 0 || syncAt < cronAt;
        const time_t fireAt = syncNext ? syncAt : cronAt;

        struct tm local;
        localtime_r(&fireAt, &local);
        KIDINK_LOGF("schedule: next wake %04d-%02d-%02d %02d:%02d local (%s)",
                    local.tm_year + 1900, local.tm_mon + 1, local.tm_mday,
                    local.tm_hour, local.tm_min,
                    syncNext ? "clock sync" : "display");
        gPersist.nextWakeIsClockSync = syncNext ? 1 : 0;
        display.rtc.setAlarmEpoch((uint32_t)fireAt, RTC_ALARM_MATCH_DHHMMSS);
        sleepUntil((uint32_t)(fireAt - now));
    }

    // No usable clock (first boot with the network down): sleep on the timer
    // alone and try again shortly.
    KIDINK_LOGF("schedule: no clock, falling back to %ds", KIDINK_FALLBACK_SLEEP_S);
    sleepUntil(KIDINK_FALLBACK_SLEEP_S);
}

// ---------------------------------------------------------------------------

void setup()
{
    Serial.begin(115200);
    delay(50); // let the USB-serial bridge settle so the first lines are seen

    xTaskCreate(bootDeadlineTask, "kidink_wd", 2048, nullptr, 1, nullptr);

    // Must precede every RTC call: the library's setEpoch/getEpoch go through
    // localtime/mktime, so the RTC registers hold local wall-clock time and the
    // epoch API is UTC. Without TZ set, both would silently mean UTC.
    setenv("TZ", KIDINK_POSIX_TZ, 1);
    tzset();

    if (gPersist.magic != kPersistMagic)
    {
        memset(&gPersist, 0, sizeof(gPersist));
        gPersist.magic = kPersistMagic;
    }
    gPersist.bootCount++;
    gPersist.etag[sizeof(gPersist.etag) - 1] = '\0';

    display.begin();

    // Read the alarm flag before anything clears it: setAlarmEpoch() calls
    // enableAlarm(), which clears it as a side effect.
    const bool wokeFromAlarm = display.rtc.checkAlarmFlag();
    display.rtc.clearAlarmFlag();
    logWakeReason(wokeFromAlarm);

    const bool buttonWake =
        esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT0 && !wokeFromAlarm;
    const bool forceRepaint = buttonWake && KIDINK_REPAINT_ON_BUTTON;

    // A pending clock sync (§5) is consumed by whichever wake arrives - the
    // alarm or the timer backstop - but a button press always means "repaint",
    // so it falls through to the display path and the sync reschedules below.
    const bool clockSyncWake = gPersist.nextWakeIsClockSync != 0 && !buttonWake;
    gPersist.nextWakeIsClockSync = 0;

    KIDINK_LOGF("boot %u, battery %.2fV, free psram %uKB",
                (unsigned)gPersist.bootCount, display.readBattery(),
                (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));

    CronSpec schedule;
    char cronError[128];
    const bool scheduleValid =
        cronParse(KIDINK_WAKE_CRON, &schedule, cronError, sizeof(cronError));
    if (!scheduleValid)
    {
        // The deploy CLI validates the expression, so this means config.h was
        // hand-edited. Fall back rather than never waking again.
        KIDINK_LOGF("schedule: '%s' is invalid (%s)", KIDINK_WAKE_CRON, cronError);
    }

    bool clockValid = display.rtc.isSet();

    // --- fetch ------------------------------------------------------------
    if (clockSyncWake)
    {
        // The daily clock sync (§5): no frame and no paint - GET the server's
        // local time and write it into the RTC. Failure keeps the current RTC
        // time; the drift another day costs is what this wake exists to bound.
        if (kidinkWifiConnect(KIDINK_WIFI_SSID, KIDINK_WIFI_PASSWORD,
                              (uint32_t)KIDINK_WIFI_TIMEOUT_S * 1000))
        {
            kidinkLogUrl("time fetch", KIDINK_TIME_URL);
            char body[32];
            LocalTimestamp stamp;
            if (!kidinkFetchText(KIDINK_TIME_URL, KIDINK_HTTP_TIMEOUT_MS, body,
                                 sizeof(body)))
            {
                KIDINK_LOGF("clock: time fetch failed, keeping the RTC as is");
            }
            else if (!parseLocalTimestamp(body, &stamp))
            {
                KIDINK_LOGF("clock: could not parse '%s'", body);
            }
            else
            {
                // Weekday is computed, never taken from the wire, and uses the
                // tm_wday convention (0 = Sunday): the alarm comparator matches
                // the weekday register too (§8 quirk 4), so a wrong value would
                // silently stop scheduled wakes.
                const int weekday = localTimestampWeekday(stamp);
                display.rtc.setDate((uint8_t)weekday, (uint8_t)stamp.day,
                                    (uint8_t)stamp.month, (uint16_t)stamp.year);
                display.rtc.setTime((uint8_t)stamp.hour, (uint8_t)stamp.minute,
                                    (uint8_t)stamp.second);
                clockValid = true;
                KIDINK_LOGF("clock: set to %04d-%02d-%02d %02d:%02d:%02d local"
                            " (weekday %d)",
                            stamp.year, stamp.month, stamp.day, stamp.hour,
                            stamp.minute, stamp.second, weekday);
            }
        }
        kidinkWifiStop();

        scheduleNextWake(clockValid, scheduleValid, schedule);
    }

    uint8_t *frame = (uint8_t *)ps_malloc(KIDINK_FRAME_BYTES);
    if (!frame)
    {
        KIDINK_LOGF("psram: could not allocate %d bytes for the frame",
                    KIDINK_FRAME_BYTES);
    }
    else if (kidinkWifiConnect(KIDINK_WIFI_SSID, KIDINK_WIFI_PASSWORD,
                               (uint32_t)KIDINK_WIFI_TIMEOUT_S * 1000))
    {
        kidinkLogUrl(forceRepaint ? "fetch (button: unconditional)" : "fetch",
                     KIDINK_FETCH_URL);
        const FetchOutcome outcome =
            kidinkFetch(KIDINK_FETCH_URL, forceRepaint ? "" : gPersist.etag, frame,
                        KIDINK_FRAME_BYTES, KIDINK_HTTP_TIMEOUT_MS);

        // Sync the clock from the response before anything else uses it: on the
        // very first boot this is what makes the schedule computable at all, and
        // it costs no extra round trip.
        if (outcome.serverDate != 0)
        {
            display.rtc.setEpoch((uint32_t)outcome.serverDate);
            clockValid = true;
        }

        switch (outcome.result)
        {
        case FetchResult::Fresh:
            KIDINK_LOGF("http: 200, painting (~19s refresh)");
            display.clearDisplay();
            display.image.draw(frame, 0, 0, KIDINK_FRAME_WIDTH, KIDINK_FRAME_HEIGHT);
            display.display();
            strncpy(gPersist.etag, outcome.etag, sizeof(gPersist.etag) - 1);
            gPersist.etag[sizeof(gPersist.etag) - 1] = '\0';
            gPersist.consecutiveFailures = 0;
            KIDINK_LOGF("paint: done, etag %s",
                        gPersist.etag[0] ? gPersist.etag : "(none)");
            break;
        case FetchResult::NotModified:
            KIDINK_LOGF("http: 304, panel already current - no refresh");
            gPersist.consecutiveFailures = 0;
            break;
        case FetchResult::Failed:
            gPersist.consecutiveFailures++;
            KIDINK_LOGF("http: failed (%s, status %d); not painting, %u in a row",
                        outcome.failure ? outcome.failure : "unknown",
                        outcome.httpStatus, gPersist.consecutiveFailures);
            break;
        }
    }
    else
    {
        gPersist.consecutiveFailures++;
    }

    if (frame)
        free(frame);
    kidinkWifiStop();

    scheduleNextWake(clockValid, scheduleValid, schedule);
}

void loop()
{
    // Unreachable: setup() always ends in deep sleep, which restarts the sketch.
}
