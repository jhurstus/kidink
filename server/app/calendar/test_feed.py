"""Tests for the ICS HTTP fetch (spec §6.1). No real sockets — httpx is faked."""

import httpx
import pytest
from pydantic import SecretStr

from app.calendar.feed import CalendarFetchError, fetch_ics

_SECRET = "https://calendar.example/private-abc123/basic.ics"


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_fetch_returns_body_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse("ICS-BODY"))
    assert fetch_ics(SecretStr(_SECRET)) == "ICS-BODY"


def test_transport_error_raises_calendar_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a: object, **k: object) -> _FakeResponse:
        raise httpx.ConnectError(f"failed connecting to {_SECRET}")

    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(CalendarFetchError) as exc_info:
        fetch_ics(SecretStr(_SECRET))
    # The raised error must not echo the secret feed URL (CLAUDE.md, §18).
    assert _SECRET not in str(exc_info.value)
    assert "calendar.example" not in str(exc_info.value)


def test_http_status_error_raises_calendar_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ErrorResponse:
        text = ""

        def raise_for_status(self) -> None:
            request = httpx.Request("GET", _SECRET)
            httpx.Response(500, request=request).raise_for_status()

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _ErrorResponse())
    with pytest.raises(CalendarFetchError):
        fetch_ics(SecretStr(_SECRET))
