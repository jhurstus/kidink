#include "fetch.h"

#include "httpdate.h"
#include "log.h"

#include <HTTPClient.h>
#include <WiFi.h>

namespace
{

// HTTPClient::setTimeout takes a uint16_t, and it is a per-read socket timeout
// rather than a deadline for the whole exchange. Cap it well below the overall
// budget and let our own millis() deadline be the real bound.
const uint32_t kSocketTimeoutMs = 30000;

FetchOutcome failure(const char *reason, int status)
{
    FetchOutcome outcome = {};
    outcome.result = FetchResult::Failed;
    outcome.httpStatus = status;
    outcome.failure = reason;
    return outcome;
}

} // namespace

bool kidinkWifiConnect(const char *ssid, const char *password, uint32_t timeoutMs)
{
    // Don't write credentials to NVS on every boot: they come from config.h,
    // and the flash wear is pointless.
    WiFi.persistent(false);
    WiFi.mode(WIFI_STA);
    WiFi.begin(ssid, password);

    const uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED)
    {
        // Unsigned subtraction, so this stays correct across a millis() wrap.
        if (millis() - start >= timeoutMs)
        {
            KIDINK_LOGF("wifi: timed out after %ums joining '%s'",
                        (unsigned)timeoutMs, ssid);
            return false;
        }
        delay(50);
    }
    KIDINK_LOGF("wifi: joined '%s' in %ums, rssi %ddBm, ip %s", ssid,
                (unsigned)(millis() - start), WiFi.RSSI(),
                WiFi.localIP().toString().c_str());
    return true;
}

void kidinkWifiStop()
{
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
}

bool kidinkFrameLooksValid(const uint8_t *frame, size_t length)
{
    for (size_t i = 0; i < length; ++i)
    {
        const uint8_t high = frame[i] >> 4;
        const uint8_t low = frame[i] & 0x0F;
        if ((high & 1) || (low & 1) || high > 0x0A || low > 0x0A)
        {
            KIDINK_LOGF("frame: byte %u is 0x%02X, not two packed palette indices",
                        (unsigned)i, frame[i]);
            return false;
        }
    }
    return true;
}

FetchOutcome kidinkFetch(const char *url, const char *ifNoneMatch, uint8_t *frame,
                         size_t frameBytes, uint32_t timeoutMs)
{
    const uint32_t start = millis();

    WiFiClient client;
    HTTPClient http;
    if (!http.begin(client, url))
        return failure("malformed URL", 0);

    const uint32_t socketTimeout =
        timeoutMs < kSocketTimeoutMs ? timeoutMs : kSocketTimeoutMs;
    http.setConnectTimeout((int32_t)socketTimeout);
    http.setTimeout((uint16_t)socketTimeout);
    http.setReuse(false);
    http.setUserAgent("kidink-inkplate/1 (esp32s3)");
    http.addHeader("Accept", "application/octet-stream");
    // A reverse proxy in front of Flask could otherwise gzip the body, which
    // would defeat the Content-Length check below.
    http.addHeader("Accept-Encoding", "identity");
    if (ifNoneMatch && ifNoneMatch[0])
        http.addHeader("If-None-Match", ifNoneMatch);

    const char *headerKeys[] = {"ETag", "Date", "Content-Encoding"};
    http.collectHeaders(headerKeys, 3);

    const int status = http.GET();
    if (status <= 0)
    {
        // errorToString returns a temporary, so log it here and hand back a
        // static string - FetchOutcome::failure outlives this scope.
        KIDINK_LOGF("http: transport error %d (%s)", status,
                    HTTPClient::errorToString(status).c_str());
        http.end();
        return failure("transport error", status);
    }

    FetchOutcome outcome = {};
    outcome.httpStatus = status;

    const String etag = http.header("ETag");
    if (etag.length() > 0 && etag.length() < KIDINK_ETAG_MAX)
        strcpy(outcome.etag, etag.c_str());
    else if (etag.length() >= KIDINK_ETAG_MAX)
        KIDINK_LOGF("etag: %u bytes is too long to store; will always full-fetch",
                    etag.length());

    const String date = http.header("Date");
    if (date.length() > 0 && !parseHttpDate(date.c_str(), &outcome.serverDate))
    {
        outcome.serverDate = 0;
        KIDINK_LOGF("date: could not parse '%s'", date.c_str());
    }

    if (status == HTTP_CODE_NOT_MODIFIED)
    {
        http.end();
        outcome.result = FetchResult::NotModified;
        return outcome;
    }
    if (status != HTTP_CODE_OK)
    {
        http.end();
        return failure("unexpected status", status);
    }

    const String encoding = http.header("Content-Encoding");
    if (encoding.length() > 0 && !encoding.equalsIgnoreCase("identity"))
    {
        http.end();
        return failure("body is content-encoded", status);
    }

    // getSize() is -1 for a chunked response, which getStreamPtr() would hand
    // back with the chunk framing still in it - so this one check rejects a
    // chunked body, a truncated one, and an oversized one alike.
    const int advertised = http.getSize();
    if (advertised != (int)frameBytes)
    {
        http.end();
        KIDINK_LOGF("body: Content-Length %d, expected %u", advertised,
                    (unsigned)frameBytes);
        return failure("wrong or missing Content-Length", status);
    }

    WiFiClient *stream = http.getStreamPtr();
    size_t received = 0;
    while (received < frameBytes)
    {
        if (millis() - start >= timeoutMs)
        {
            http.end();
            KIDINK_LOGF("body: deadline hit after %u of %u bytes", (unsigned)received,
                        (unsigned)frameBytes);
            return failure("timed out reading the body", status);
        }
        const size_t available = stream->available();
        if (available == 0)
        {
            if (!http.connected())
            {
                http.end();
                KIDINK_LOGF("body: closed after %u of %u bytes", (unsigned)received,
                            (unsigned)frameBytes);
                return failure("connection closed early", status);
            }
            delay(2);
            continue;
        }
        const size_t want =
            available < frameBytes - received ? available : frameBytes - received;
        const int read = stream->readBytes(frame + received, want);
        if (read <= 0)
        {
            delay(2);
            continue;
        }
        received += (size_t)read;
    }
    http.end();

    if (!kidinkFrameLooksValid(frame, frameBytes))
        return failure("body is not a packed 4bpp frame", status);

    KIDINK_LOGF("body: %u bytes in %ums", (unsigned)frameBytes,
                (unsigned)(millis() - start));
    outcome.result = FetchResult::Fresh;
    return outcome;
}
