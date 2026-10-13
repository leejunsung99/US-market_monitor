import logging
import threading
from types import SimpleNamespace

import pytest
import requests

from us_market_monitor.http import FetchError, HttpClient, Stopped
from us_market_monitor.main import RedactingFormatter
from us_market_monitor.notifications.formatter import format_event
from us_market_monitor.notifications.telegram import DeliveryError, Telegram


def response(status=200, payload=None, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: payload)


def test_telegram_payload_and_html_escaping(monkeypatch, classifier, event_factory):
    event = classifier.classify(
        event_factory(summary='NVIDIA reports quarterly results <script>bad</script> & "quote"')
    )
    text = format_event(event)
    assert "&amp;" in text
    assert "<script>" not in text
    assert "발표 시각:" in text and "수집 시각:" in text and "KST" in text
    assert "HIGH" not in text
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return response(payload={"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr(requests, "post", post)
    assert Telegram("test-token", "test-chat").send(text) == 123
    assert calls[0][1]["json"]["parse_mode"] == "HTML"


def test_telegram_rate_limit_respects_retry_after(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_a, **_k: response(
            429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 120}}
        ),
    )
    with pytest.raises(DeliveryError) as raised:
        Telegram("test-token", "test-chat").send("test")
    assert raised.value.retryable
    assert raised.value.retry_after == 120
    assert not raised.value.ambiguous


@pytest.mark.parametrize(
    "exception,ambiguous,retryable",
    [
        (requests.ReadTimeout("token should never appear"), True, False),
        (requests.ConnectTimeout("private URL"), False, True),
        (requests.ConnectionError("private URL"), True, False),
    ],
)
def test_delivery_network_failure_is_sanitized(monkeypatch, exception, ambiguous, retryable):
    def post(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(DeliveryError) as raised:
        Telegram("private-token", "private-chat").send("test")
    assert raised.value.ambiguous == ambiguous
    assert raised.value.retryable == retryable
    assert "private" not in str(raised.value)


def test_telegram_server_error_is_ambiguous(monkeypatch):
    monkeypatch.setattr(
        requests,
        "post",
        lambda *_a, **_k: response(
            500, {"ok": False, "error_code": 500, "description": "private-token"}
        ),
    )
    with pytest.raises(DeliveryError) as raised:
        Telegram("test-token", "test-chat").send("test")
    assert raised.value.ambiguous
    assert "private-token" not in str(raised.value)


def test_http_retries_transient_errors(monkeypatch):
    replies = iter([response(503), response(200, {"ok": True})])
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: next(replies))
    client = HttpClient(attempts=3, spacing=0)
    waits = []
    client.wait = lambda seconds: waits.append(seconds)
    assert client.json("https://example.com") == {"ok": True}
    assert any(seconds >= 1 for seconds in waits)


def test_http_403_not_retried_or_leaked(monkeypatch):
    calls = []

    def get(*args, **kwargs):
        calls.append(args)
        return response(403)

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(FetchError, match="^http_403$"):
        HttpClient(spacing=0).get("https://example.com?token=secret")
    assert len(calls) == 1


def test_long_rate_limit_deferred_to_scheduler(monkeypatch):
    monkeypatch.setattr(
        requests, "get", lambda *_a, **_k: response(429, headers={"Retry-After": "180"})
    )
    client = HttpClient(spacing=0)
    with pytest.raises(FetchError) as raised:
        client.get("https://example.com")
    assert raised.value.retry_after == 180


def test_interruptible_http_stop():
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        HttpClient(stop=stop).get("https://example.com")


def test_secret_redaction_including_exception_text():
    formatter = RedactingFormatter(["private-token", "private-chat"])
    record = logging.LogRecord(
        "test", logging.ERROR, "", 0, "failure private-token private-chat", (), None
    )
    text = formatter.format(record)
    assert "private-token" not in text and "private-chat" not in text
    assert "[REDACTED]" in text
