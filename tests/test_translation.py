from types import SimpleNamespace

import requests

from us_market_monitor.notifications.formatter import format_rich_article
from us_market_monitor.processors.translator import Translator


def translator():
    return Translator(
        SimpleNamespace(
            translator_key="private-test-key",
            translator_region="global",
            translator_endpoint="https://api.cognitive.microsofttranslator.com/",
        )
    )


def response(texts):
    return SimpleNamespace(
        status_code=200,
        json=lambda: [{"translations": [{"text": text, "to": "ko"}]} for text in texts],
    )


def test_translate_cache_and_preserve_source(monkeypatch, event_factory):
    event = event_factory(
        "Micron reports revenue of $10 billion",
        summary="MU reports revenue of $10 billion.",
        related_tickers=("MU",),
    )
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        return response(["마이크론 매출 $10 billion 발표", "MU 매출 $10 billion 발표"])

    monkeypatch.setattr(requests, "post", post)
    client = translator()
    assert client.translate(event)
    assert client.translate(event)
    assert len(calls) == 1
    assert calls[0]["params"]["textType"] == "html"
    assert "Ocp-Apim-Subscription-Region" not in calls[0]["headers"]
    assert "notranslate" in calls[0]["json"][0]["Text"]
    assert event.title == "Micron reports revenue of $10 billion"
    assert format_rich_article(event)["blocks"][0]["text"] == event.title_ko


def test_changed_amount_falls_back_without_key_in_logs(monkeypatch, event_factory, caplog):
    event = event_factory("Micron revenue $10 billion", summary="Revenue $10 billion.")
    monkeypatch.setattr(
        requests, "post", lambda *a, **kw: response(["매출 $11 billion", "매출 $10 billion"])
    )
    assert not translator().translate(event)
    assert not event.title_ko
    assert "private-test-key" not in caplog.text
    assert format_rich_article(event)["blocks"][0]["text"] == event.title


def test_translation_failure_does_not_block_news(monkeypatch, event_factory):
    def fail(*args, **kwargs):
        raise requests.ReadTimeout("private-test-key")

    monkeypatch.setattr(requests, "post", fail)
    event = event_factory()
    assert not translator().translate(event)
    assert not event.translation_hash


def test_filing_repeated_numbers_and_overlapping_tokens(monkeypatch, event_factory):
    from bs4 import BeautifulSoup

    event = event_factory(
        "TSM 6-K 공시",
        summary="EX-99.1 2 EX-99.1 Document reports EPS of NT$29.43 in 2026, compared with 2026.",
        related_tickers=("TSM",),
    )

    def post(*args, **kwargs):
        payload = kwargs["json"]
        tokens = [
            span.get_text()
            for item in payload
            for span in BeautifulSoup(item["Text"], "html.parser").select("span")
        ]
        assert "99.1 D" not in tokens
        return response([item["Text"] for item in payload])

    monkeypatch.setattr(requests, "post", post)
    assert translator().translate(event)


def test_repeated_filing_number_cannot_disappear(monkeypatch, event_factory):
    event = event_factory("TSM 6-K 공시", summary="Exhibit 99.1 references Exhibit 99.1.")
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: response(["TSM 6-K 공시", "첨부 99.1을 참조합니다."]),
    )
    assert not translator().translate(event)


def test_translation_saved_and_reused(repo, classifier, filters, event_factory, monkeypatch):
    event = classifier.classify(event_factory())
    monkeypatch.setattr(
        requests, "post", lambda *a, **kw: response(["엔비디아 실적 발표", "매출 $10 billion"])
    )
    client = translator()
    assert client.translate(event)
    event_id = repo.ingest(event, filters)
    restored = repo.row_event(
        repo.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cache must avoid API")),
    )
    assert client.translate(restored)
    assert restored.summary_ko == event.summary_ko
