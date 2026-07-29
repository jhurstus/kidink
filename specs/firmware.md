# Inkplate Firmware - Specification (as built)

## 1. Overview

The device side of the board (main spec §2, §19): an Inkplate 13 SPECTRA that
wakes on a schedule, fetches a pre-rendered picture from the kidink server, and
paints it. All the intelligence lives on the server; the ESP32-S3 is a dumb
client.

One cycle per boot, then deep sleep - the whole program is `setup()`, because
waking from deep sleep restarts the sketch:

```
wake → join Wi-Fi → conditional GET /display → paint iff 200 → arm next wake → sleep
```

Once a day (§5) a wake is a **clock sync** instead: it GETs `/time` and writes
the server's local timestamp into the RTC, touching neither the panel nor the
frame path.

The panel is bistable, so a `304 Not Modified` costs nothing and keeps the last
image up. Skipping the download *and* the ~19-second refresh is the entire
battery story.

**There are no retries.** Any failure - Wi-Fi timeout, HTTP timeout, bad status,
malformed body - logs, leaves the panel untouched, and waits for the next
scheduled wake.

Components:

- `arduino/kidink/` - the sketch. `kidink.ino` (state machine), `cron.{h,cpp}`,
  `httpdate.{h,cpp}`, `timesync.{h,cpp}`, `civil.h`, `fetch.{h,cpp}`, `log.h`,
  and the generated, **gitignored** `config.h`.
- `server/app/firmware/` - the deploy CLI (`uv run python -m app.firmware` from
  `server/`), which resolves the app settings into `config.h` and drives
  `arduino-cli` through `app.eink.arduino`.
- `arduino/kidink_tests/` - host harnesses for the pure-C++ units, compiled and
  driven from pytest.

`arduino/mockup/` and `app.eink` are untouched and remain the one-shot
image-push/preview tool ([eink-demo.md](eink-demo.md)).

## 2. Wake sources

**The WAKE button is GPIO18; the PCF85063A's alarm INT is GPIO2.** Both active
low. The library's SPECTRA example claims the two share GPIO18 - **it is wrong
for this board** (§8 quirk 11): armed that way, the alarm interrupt lands on an
unwatched pin, no scheduled wake ever fires, and every cycle silently rides the
timer backstop ~15-20 minutes late. The firmware therefore arms both sources:
`ext0` on GPIO18 for the button and `ext1` (`ANY_LOW`, mask `1<<2`) for the
alarm. The wake causes are distinct (`EXT1` = alarm, `EXT0` = button), and the
RTC's alarm flag still breaks the tie if a press and an alarm land together.

A **timer wakeup is always armed alongside** the RTC alarm, at `sleep × 1.15 +
300 s`. It is a backstop against a failed I2C write, a cleared RTC, or a dead
backup cell. The slack matters: the ESP32's RTC_SLOW_CLK is an internal RC
oscillator with percent-level drift, so a tight margin would let the (inaccurate)
timer beat the (accurate) alarm and cause spurious wakes.

On a button wake the firmware **omits `If-None-Match`**
(`device_repaint_on_button`), so a press always produces a visible refresh rather
than a silent 304 that looks like the button is broken.

Before sleeping, the firmware waits (up to 10 s) for GPIO18 to go high, so a held
button cannot re-trigger ext0 the instant the board sleeps and spin it through
boot after boot.

## 3. HTTP contract

### Request

```
GET /display HTTP/1.1
Host: <device_server_base_url>
User-Agent: kidink-inkplate/1 (esp32s3)
Accept: application/octet-stream
Accept-Encoding: identity
If-None-Match: "<stored etag>"      ; omitted on cold boot and button wake
```

HTTP only - the firmware has no TLS stack, and `https://` is rejected at config
time. No redirects, no auth. The debug args on `/display` (`?raw=1`,
`?quantize=1`, `?date=`, …) are for humans in a browser; the device sends none of
them, though `device_fetch_path` can carry any of them for testing.

### `200 OK` - required to paint

| Header | Requirement |
|---|---|
| `Content-Length` | **exactly `960000`**. Any other value, or absent (i.e. chunked), aborts the fetch. |
| `Transfer-Encoding` | must be absent - see §7. |
| `Content-Encoding` | absent or `identity`. |
| `ETag` | strong validator, **< 80 bytes including quotes**. `/display` serves a quoted SHA-256 hex digest (66 bytes). A longer value is dropped, and the device then simply always full-fetches. |
| `Date` | RFC 9110 IMF-fixdate in GMT, e.g. `Sat, 25 Jul 2026 18:02:38 GMT`. **The board's only clock source** (§5). |

Body: exactly 960,000 bytes in `server/app/eink/pack.py` layout - 4 bits per
pixel, two pixels per byte, **high nibble = left/even-x pixel**, rows top to
bottom, stride 800 bytes, 1200 rows, logical image 1600 × 1200 landscape.
**Nibble = `palette_index << 1`**, so the only legal nibbles are `0x0 0x2 0x4 0x6
0x8 0xA` (black, white, yellow, red, blue, green). Cross-reference
[eink-demo.md](eink-demo.md) §3.

### `304 Not Modified`

No body. The panel already shows the right image, so painting is skipped
entirely and the firmware goes straight to scheduling.

### Anything else

Any other status, a transport error, a length mismatch, a short read, or an
invalid nibble is a failure: log it, do not paint, sleep until the next wake.

### `GET /time` - the daily clock sync (§5)

```
GET /time HTTP/1.1
User-Agent: kidink-inkplate/1 (esp32s3)
Accept: text/plain
```

A `200` carries a plain-text body: one `YYYY-MM-DD HH:MM:SS` line (newline
terminated), the current time **already in the display timezone**, served
`Cache-Control: no-store`. The firmware parses it (`timesync.cpp`: fixed-width
fields, real-calendar validation via `civil.h`, year 2020-2099 because the RTC
stores two-digit years) and writes it into the RTC with `setDate()`/`setTime()`.
Any other status, a body over ~31 bytes, or a malformed stamp logs and leaves
the RTC untouched - the next day's sync is the retry.

## 4. Wake state machine

```
POWER-ON or DEEP-SLEEP WAKE
 |- Serial.begin(115200)
 |- xTaskCreate(bootDeadlineTask)                 <- §7 watchdog
 |- setenv("TZ", KIDINK_POSIX_TZ); tzset()        <- MUST precede every RTC call
 |- validate the RTC_DATA_ATTR block by magic word (else zero it)
 |- display.begin()                               <- setRotation(1), ps_malloc(960000)
 |- wokeFromAlarm = rtc.checkAlarmFlag()          <- read BEFORE anything clears it
 |  rtc.clearAlarmFlag()
 |  buttonWake  = (cause == EXT0) && !wokeFromAlarm
 |  forceRepaint = buttonWake && KIDINK_REPAINT_ON_BUTTON
 |  clockSyncWake = persisted sync flag && !buttonWake   <- §5; flag then cleared
 |- clockValid = rtc.isSet()
 |
 |- CLOCK-SYNC WAKE (clockSyncWake, §5): no frame, no paint
 |    kidinkWifiConnect; GET /time; parseLocalTimestamp
 |    ok -> rtc.setDate(weekday, d, m, y); rtc.setTime(h, m, s); clockValid = true
 |    any failure -> RTC untouched; tomorrow's sync is the retry
 |    WiFi off -> SCHEDULE
 |
 |- DISPLAY WAKE (everything else):
 |    frame = ps_malloc(960000)                   -- NULL -> failure
 |    kidinkWifiConnect(ssid, pass, wifi timeout) -- false -> failure
 |    kidinkFetch(url, forceRepaint ? "" : etag, frame, ...)
 |      |- Fresh (200)   -> clearDisplay; image.draw(frame,0,0,1600,1200); display();
 |      |                   store ETag
 |      |- NotModified   -> no paint
 |      +- Failed        -> no paint
 |    if (Date header parsed) { rtc.setEpoch(utc); clockValid = true }
 |    free(frame); WiFi off
 |
 |- SCHEDULE (every path joins here):
 |    if (clockValid) {
 |      now = rtc.getEpoch()                      <- ONE read; see §8 quirk 3
 |      cronAt = cronNext(schedule, localtime(now)); mktime  (if scheduleValid)
 |        with the MIN_SLEEP_S re-fire guard and MAX_SLEEP_S clamp
 |      syncAt = next KIDINK_CLOCK_SYNC_HOUR:MINUTE local (>= MIN_SLEEP_S away)
 |      fire at the earlier one (tie -> display); persist the sync flag
 |      rtc.setAlarmEpoch(fireAt, RTC_ALARM_MATCH_DHHMMSS)
 |    } else sleep for device_fallback_sleep_seconds, timer only
 |- esp_sleep_enable_timer_wakeup(sleep * 1.15 + 300)
 |- esp_sleep_enable_ext0_wakeup(GPIO_NUM_18, 0)      <- WAKE button
 |- esp_sleep_enable_ext1_wakeup(1<<2, ANY_LOW)       <- RTC alarm INT (§2)
 |- wait for GPIO18 high (<= 10 s)
 +- esp_deep_sleep_start()
```

State that survives deep sleep lives in one `RTC_DATA_ATTR` struct: a magic word,
the boot count, the stored ETag, a consecutive-failure counter, and the
next-wake-is-clock-sync flag (§5). RTC memory is uninitialised garbage on a cold
boot and after a reflash, which is what the magic word detects; the word is
bumped whenever the struct layout changes, so a block persisted by an older
firmware cannot validate against a new shape.

### Clock bootstrap

On a first boot `rtc.isSet()` is false, so the schedule cannot be computed - but
the `Date` header of that very fetch sets the clock, so a *successful* first boot
schedules correctly with no extra round trip. Only if the fetch **also** fails is
there no clock at all; then the firmware arms the timer alone at
`device_fallback_sleep_seconds` (15 minutes) and tries again.

There is no NTP client. The `Date` header is free, accurate to the second, and
re-syncs the RTC on every successful fetch; the daily `/time` sync (§5) is the
belt to that suspender, bounding drift even across a run of failed fetches.

## 5. Clock and timezone

The device has no zoneinfo database, so it gets a **POSIX TZ string**
(`PST8PDT,M3.2.0,M11.1.0`), derived at deploy time from the IANA `timezone`
setting by reading the TZif v2+ footer (`app/firmware/tz.py`).

`setenv("TZ", ...)` **must run before any RTC call**: the library's `setEpoch()`
goes through `localtime()` and `getEpoch()` through `mktime()`, so the RTC
registers hold **local wall-clock time** while the epoch API is UTC. With TZ
unset both would silently mean UTC and the board would wake at the wrong hour.

The one accepted imprecision: `getEpoch()` uses `mktime` with `tm_isdst = -1`, so
during the repeated hour of a fall-back transition the epoch can be off by an
hour, once a year. There is no clean fix with this API, and a wake an hour late
on one autumn morning is not worth more machinery.

### The daily clock sync

Once a day, at `device_clock_sync_time` (default **03:15** local, outside the
display wake window), the armed wake is a **clock sync** rather than a display
fetch: the firmware GETs `/time` (§3) and writes the returned local stamp into
the RTC via `setDate()`/`setTime()` - a deliberately minimal NTP stand-in. The
PCF85063A can drift a couple of seconds a day, and Soldered's own guidance is to
set it about once per day; the opportunistic `Date`-header sync usually does
that already, so what this wake really buys is a *bound*: the clock is never
more than a day from its last set, even if every display fetch in between
failed, and a DST transition is picked up by the next 03:15 sync at the latest.

Mechanics:

- Which kind the next wake is lives in the persisted flag (§4). A **button
  press during a pending sync is still a repaint** - the sync is simply
  re-armed at the next scheduling pass, i.e. it slips to tomorrow if today's
  03:15 has passed. The timer backstop firing instead of the alarm still runs
  the pending sync.
- Scheduling always arms the **earlier** of the next cron fire and the next
  sync instant, so neither schedule can starve the other; on an exact tie the
  display wake wins, since its `Date` header syncs the clock anyway.
- The weekday handed to `setDate()` is **computed on-device** from the civil
  date (`civil.h`, 0 = Sunday), never taken from the wire: the alarm comparator
  matches the weekday register too (§8 quirks 4 and 6), so a wrong weekday
  would silently stop scheduled wakes.
- The timezone question does not arise: `/time` serves wall-clock time already
  in the display timezone, and the RTC registers hold local wall-clock time
  (§8 quirk 2). The server did all the zone math.

## 6. Wake schedule (cron dialect)

Five fields: `minute hour day-of-month month day-of-week`. Default
`0 5-21/2 * * *` - every two hours from 05:00 to 21:00, local.

Supported: `*`, `N`, `A-B`, comma lists, `*/S`, `A-B/S`, `A/S` (Vixie's "N
through the maximum, stepping S"), month names `JAN`–`DEC` and weekday names
`SUN`–`SAT` (case-insensitive), day-of-week `7` as a synonym for Sunday, and the
macros `@hourly @daily @midnight @weekly @monthly @yearly @annually`.

Rejected with a clear error at deploy time: the 6-field (seconds) form, `L`, `W`,
`#`, `?`, `@reboot`, out-of-range values, and a zero step.

**Vixie's day rule**, reproduced exactly: if **either** day field's raw text
begins with `*`, the two are ANDed; otherwise they are ORed. So `0 0 13 * FRI`
fires on the 13th *or* on any Friday, while the everyday `0 0 13 * *` just fires
on the 13th. Note `*/2` counts as starred, so it ANDs while still filtering days.

Evaluation is **naive local wall-clock arithmetic** using civil-calendar helpers
(`civil.h`), never `mktime`, so the evaluator carries no timezone state and is
byte-comparable against its Python twin. Only the caller converts the result to
an instant, and that is where DST lands: cron matches a wall clock, so `mktime`
normalizes a nonexistent local time and picks a side for an ambiguous one.

Two guards on the computed sleep:

- **`MIN_SLEEP_S` (90 s).** An RTC drifting two seconds early would otherwise
  wake at 07:59:58 for a `0 8 * * *` cron, sleep two seconds, wake again, and
  repaint.
- **`MAX_SLEEP_S` (24 h).** The alarm register matches day-of-month, so it cannot
  express a horizon beyond about a month (§8 quirk 4).

## 7. Robustness

- **Chunked bodies.** `HTTPClient::getStreamPtr()` does **not** decode chunked
  transfer encoding - only `writeToStream()` does - so a chunked body would put
  hex chunk headers into the framebuffer. Gating on
  `http.getSize() == KIDINK_FRAME_BYTES` rejects it, since chunked reports `-1`.
  The same check catches a truncated or oversized body.
- **Read deadline.** `HTTPClient::setTimeout` is a `uint16_t` per-*read* socket
  timeout, not a deadline for the exchange; a stalled-but-open socket would hang
  forever. The body loop owns an explicit `millis()` deadline and treats
  "disconnected with nothing buffered" as a short read.
- **Nibble validation.** One pass over the 960,000 bytes asserts both nibbles of
  every byte are even and `<= 0x0A` (~5 ms). Worth it because
  `writePixelInternal` silently skips out-of-range colours, so a corrupt body
  would paint as *stale pixels* rather than fail loudly.
- **Boot-deadline watchdog.** A FreeRTOS task forces deep sleep after
  `BOOT_DEADLINE_MS` (3 minutes). The library's `waitForBusy()` is an unbounded
  spin, so a panel fault would otherwise hold the board awake and energized until
  the battery was flat. The task deliberately touches **no I2C**, so it cannot
  race the main task's `Wire` transactions.
- **PSRAM.** The 960,000-byte download buffer plus the library's own 960,000-byte
  framebuffer is 1.92 MB of 8 MB. `ps_malloc` is NULL-checked, and free PSRAM is
  logged each boot.

**Not done, deliberately:** `memcpy`ing straight into the library's
`DMemory4Bit`. That buffer is panel-native portrait 1200 × 1600 holding raw
`colorPalette[]` codes - a different layout - so the shortcut would break the
shared `pack.py` contract for a few seconds of `drawPixel` calls that are
insignificant next to a ~19-second refresh. Possible v2 if it ever matters.

**Logging** is one `[kidink]`-prefixed line per stage. The Wi-Fi passphrase is
never logged in any form, not even its length, and URLs are truncated at the
query string so a future token-bearing path cannot leak (CLAUDE.md).

## 8. Known library quirks (Inkplate Arduino library 11.1.2)

Expensive to rediscover; all verified against the library source.

1. **WAKE button and RTC INT share GPIO18.** One ext0 source covers both (§2).
2. **`setEpoch()` uses `localtime()`, `getEpoch()` uses `mktime()`.** The RTC
   holds local wall clock; the epoch API is UTC (§5).
3. **`getRtcData()` is not a snapshot.** Every getter (`getHour()`,
   `getMinute()`, …) calls `updateTime()` and re-reads I2C on its own, so reading
   hour then minute can straddle a minute boundary. Always take a single
   `getEpoch()` and `localtime_r()` it.
4. **`setAlarmEpoch`'s `_match` argument is a no-op.** The mask is
   `decToBcd(v) & ~(bit << 7)`; when `bit == 0` the mask is `0xFF`, so the
   active-low `AEN_x` enable stays clear and the field matches anyway. All five
   fields always match. Harmless here because the alarm is always derived from a
   real datetime (so day-of-month and weekday agree), but it caps the horizon at
   about a month - hence `MAX_SLEEP_S` and the timer backstop.
5. **`setAlarmEpoch()` calls `enableAlarm()`, which clears the alarm flag.** Read
   `checkAlarmFlag()` early or the wake cause is lost.
6. **`rtc.setDate()`'s weekday is a trap.** The library example's comment
   claims "0 for Monday" while `updateTime()`/`getEpoch()` treat `Weekday` as
   `tm_wday` (0 = Sunday) - and per quirk 4 the alarm comparator matches the
   weekday register, so believing the comment stops scheduled wakes outright.
   The clock-sync path (§5) therefore never trusts a wire value: it computes
   the weekday from the civil date (`civil.h`, `tm_wday` convention) before
   calling `setDate()`. `setEpoch()` sidesteps the question entirely and stays
   the setter everywhere else.
7. **`initDriver()` calls `setRotation(1)`.** After `begin()` the user-space
   canvas is 1600 × 1200 landscape, so `image.draw(buf, 0, 0, 1600, 1200)` is
   right even though the panel is natively portrait.
8. **`image.draw(const uint8_t*, …)` routes to `drawBitmap3Bit` on SPECTRA**,
   which does `drawPixel(x, y, nibble >> 1)` - i.e. it consumes `pack.py`'s
   format verbatim, `<< 1` included, and handles the rotation.
9. **`waitForBusy()` is an unbounded spin** (§7).
10. **`Inkplate::begin()` returns `void`** and swallows `initDriver`'s PSRAM
    failure, so a failed allocation surfaces later as a null dereference.
11. **The RTC alarm INT is GPIO2, not GPIO18.** The SPECTRA
    `RTC_Alarm_With_Deep_Sleep` example arms ext0 on GPIO18 and claims the INT
    shares the button pin; on this board the alarm interrupt never moves
    GPIO18. Found empirically (2026-07-29): with AF and AIE asserted
    (`CTRL_2 = 0xC0`), GPIO2 is the pin that drops, level-held until the flag
    clears, and an `ext1 ANY_LOW` wake on it ends a deep sleep on the alarm
    second. Cross-check: the official jumper doc puts the RTC's CLKOUT on IO2
    via the (open) JP2 and never mentions the INT at all, so treat IO2's
    documentation as unreliable and this measurement as the source of truth.
    Symptom of getting it wrong: every wake is a timer-backstop wake
    (`sleep × 1.15 + 300 s` late - ~20 min on a 2 h gap) while the clock and
    alarm registers all look perfect.

## 9. Configuration

All keys live in the app settings (`server/app/config.py`, main spec §18) and are
consumed only by the deploy CLI. Every one has a default, so a checkout that
never flashes a board still starts; the CLI is what fails fast.

| Key | Default | Purpose |
|---|---|---|
| `device_wifi_ssid` | `""` (required by the CLI) | Network the panel joins. `SecretStr`. |
| `device_wifi_password` | `""` | Passphrase. `SecretStr`; never logged. |
| `device_server_base_url` | `""` (required by the CLI) | Origin, e.g. `192.168.1.20:5051`. A bare `host:port` gains `http://`; `https://` is rejected. No default - it has to be an address the *board* can reach, and a wrong guess shows up only as a device that silently never updates. |
| `device_fetch_path` | `/display` | Path and any query. |
| `device_time_path` | `/time` | Path of the clock-sync endpoint (§5). With a `--url` override, the time URL follows the override's origin. |
| `device_wake_cron` | `0 5-21/2 * * *` | Wake schedule; validated at load. |
| `device_clock_sync_time` | `03:15` | Local `HH:MM` of the daily clock-sync wake (§5); validated at load. The default sits outside the display window. |
| `device_wifi_timeout_seconds` | `60` | Association deadline. |
| `device_http_timeout_seconds` | `300` | Whole-fetch deadline. Generous: a cold image cache makes the server generate and screenshot inline. |
| `device_fallback_sleep_seconds` | `900` | Sleep when the schedule is uncomputable. |
| `device_repaint_on_button` | `true` | A WAKE press skips `If-None-Match`. |
| `device_posix_tz` | `""` | Override; empty derives from `timezone`. |

Baked constants, not config keys (they guard hardware quirks rather than being
tuned): `MIN_SLEEP_S = 90`, `MAX_SLEEP_S = 86400`, `BOOT_DEADLINE_MS = 180000`,
`ETAG_MAX = 80`, and the frame geometry.

They are emitted into `arduino/kidink/config.h`, which is **gitignored** - as is
`server/.firmware-out/`, since the compiled `.bin` embeds the passphrase in
plaintext. Non-ASCII values are escaped as three-digit **octal**, never `\xNN`:
C hex escapes are greedy, so `"\xc3" "a"` would parse as one escape and silently
corrupt a passphrase with an accented character.

## 10. Build and flash

```
cd server
uv run python -m app.firmware                  # config.h -> compile -> flash
uv run python -m app.firmware --no-upload      # compile only
uv run python -m app.firmware --no-compile     # just write config.h
uv run python -m app.firmware --print-config   # header with secrets redacted
uv run python -m app.firmware --next-fires 10  # preview the schedule
```

Overrides: `--url`, `--cron`, `--tz`, plus `--sketch-dir`, `--out-dir`, `--port`,
`--fqbn` mirroring `app.eink`. The CLI prints the clock-sync schedule and the
next few wake times before writing anything, which is what catches a mistyped
schedule before the board goes back on the wall.

Requires `arduino-cli`, the Soldered board package, and the Inkplate Arduino
library installed (same prerequisites as `app.eink`). The sketch folder name must
equal the `.ino` basename, so an alternative `--sketch-dir` must still be named
`kidink`. Build size: ~994 KB, 31% of the app partition.

### Reading the serial log

The board's only diagnostic channel. Serial runs at **115200** over the same USB
port used for flashing:

```
arduino-cli monitor -p /dev/cu.wchusbserial10 -c baudrate=115200
arduino-cli monitor -p /dev/cu.wchusbserial10 -c baudrate=115200 | tee ~/kidink-serial.log
```

Ctrl-C to exit. Substitute the actual port - it is whatever
`/dev/cu.wchusbserial*` resolves to (the deploy CLI's `resolve_port` glob picks
the single match), and the trailing digits are not stable across reconnects.

Two things make a live monitor confusing if you do not expect them:

- **The board is asleep almost all the time**, so the port is silent between
  wakes. Attaching usually toggles DTR/RTS and resets the board, which starts a
  fresh cycle; if it does not, **press the WAKE button** to force one
  immediately rather than waiting out the schedule. A button wake also skips
  `If-None-Match` (§2), so it produces a real paint instead of a 304.
- **Nothing is buffered for you.** Output that happened during a previous wake is
  gone; only what is printed while the monitor is attached appears.

A healthy cycle:

```
[kidink] wake: RTC alarm
[kidink] boot 7, battery 4.01V, free psram 7808KB
[kidink] wifi: joined 'YourSSID' in 2140ms, rssi -58dBm, ip 192.168.1.31
[kidink] fetch http://192.168.1.20:5051/display
[kidink] body: 960000 bytes in 41200ms
[kidink] http: 200, painting (~19s refresh)
[kidink] paint: done, etag "3a36..."
[kidink] schedule: next wake 2026-07-25 14:15 local (display)
[kidink] sleep: 812s (timer backstop 1233s)
```

A clock-sync cycle (§5) is shorter - no frame, no paint:

```
[kidink] wake: RTC alarm
[kidink] boot 8, battery 4.01V, free psram 7808KB
[kidink] wifi: joined 'YourSSID' in 2140ms, rssi -58dBm, ip 192.168.1.31
[kidink] time fetch http://192.168.1.20:5051/time
[kidink] clock: set to 2026-07-26 03:15:02 local (weekday 0)
[kidink] schedule: next wake 2026-07-26 04:00 local (display)
[kidink] sleep: 2698s (timer backstop 3403s)
```

The log carries its own timings, so host-side timestamps are rarely needed:

| Line | What it tells you |
|---|---|
| `wake:` | `RTC alarm` is the healthy case. `timer backstop` means the alarm did **not** fire and the board is running free - the one thing that produces a *drifting* schedule (§4). |
| `body: ... in NNNNms` | The whole server round trip plus download. `/display` re-renders and re-quantizes per request, so this is usually the dominant cost of a cycle. |
| `schedule: next wake` | The absolute stamp the RTC alarm is armed with, and which kind of wake it is - `(display)` or `(clock sync)` (§5). Compare it against when the ink actually settles: the gap is cycle latency, **not** clock error, because the alarm is absolute (§4) and therefore does not accumulate. |
| `http:` | `304` means the panel was already current and the ~19 s refresh was skipped - the battery win, not a failure. |

A constant offset between the scheduled minute and the visible repaint is
expected and harmless: the panel changes when the cycle *finishes*. It only looks
wrong at a short test cadence like `*/15`, where the cycle time is a large
fraction of the period; at the intended `0 5-21/2 * * *` it is invisible. A
*growing* offset is the real warning sign.

The Wi-Fi passphrase never appears in the log, and URLs are truncated at the
query string (§7).

## 11. Testing

The pure-C++ units (`cron.cpp`, `httpdate.cpp`) only ever run on the ESP32, so
they are compiled for the host and driven from the normal pytest run - no new
tooling, and hermetic enough for `--disable-socket`:

- `server/app/firmware/cron_cases.py` is a **single shared conformance table**.
  `test_cron.py` runs it against the Python reference; `test_cron_cpp.py`
  compiles `arduino/kidink_tests/cron_host_test.cpp` with
  `g++ -std=c++17 -Wall -Wextra -Werror` and pipes it the same rows. One copy, so
  the device implementation and the reference cannot drift.
- `test_httpdate_cpp.py` does the same for the `Date` parser, checking against
  epochs computed by Python's own `datetime`.
- `test_timesync_cpp.py` does the same for the `/time` body parser
  (`timesync.cpp`), including the computed `tm_wday`-convention weekday that
  `setDate()` depends on (§8 quirk 6).
- All skip cleanly when no host `g++` is present.

The remaining Python tests cover the header emitter (escaping, every `#define`,
secret redaction), the POSIX TZ derivation, the CLI, and the new config keys.

The host harnesses live in `arduino/kidink_tests/`, a **sibling** of the sketch
dir: arduino-cli compiles every source under the sketch folder, and a second
`main()` there would collide at link time.

### On-device bring-up

Flashing is an explicit-user-request operation (see the `run-kidink` skill).

1. `--no-upload`, confirm a clean compile and the flash figure.
2. Flash, then watch the serial log (§10, "Reading the serial log") for one full
   cycle: wake cause, Wi-Fi, status, byte count, paint, next fire, sleep seconds.
3. Set `device_wake_cron = "*/5 * * * *"` temporarily and watch three cycles -
   cycles 2 and 3 must return **304 and skip the refresh**.
4. Press WAKE mid-sleep: unconditional repaint, alarm re-armed.
5. Stop the server and force a wake: the panel keeps its image and the device
   sleeps rather than spinning.
6. Cold clock (`rtc.reset()` from a scratch sketch) with the server down:
   confirm the 15-minute fallback wake.
