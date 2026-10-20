from types import SimpleNamespace

import requests

from us_market_monitor.collectors.sec import filing_text
from us_market_monitor.notifications.article import enrich_article
from us_market_monitor.notifications.formatter import format_event
from us_market_monitor.processors.briefing import (
    VERSION,
    display_summary,
    financial_fact_korean,
    polish_korean,
    prepare_brief,
    select_points,
)
from us_market_monitor.processors.translator import Translator


def test_complete_sentences_ranges_and_no_clipped_teaser():
    text = (
        "News Summary: Acme introduces a chip simulator with 2-3x acceleration in early-access testing."
        "NanoVDB improves simulation by up to 30x. An unfinished description of"
    )
    points = select_points(text)
    assert len(points) == 2
    assert points[0].endswith("in early-access testing.")
    assert "2-3x" in points[0]
    assert points[1].endswith("up to 30x.")
    assert "News Summary" not in " ".join(points)


def test_similar_metrics_with_different_values_are_preserved():
    points = select_points("Revenue was $10.3 billion in Q1. Revenue was $11.3 billion in Q2.")
    assert len(points) == 2


def test_repeated_performance_claim_is_removed():
    points = select_points(
        "Acme introduces a library for simulation, accelerating already GPU-optimized workflows "
        "by an additional 2-3x in early-access testing. "
        "In early-access testing, the library accelerated already GPU-optimized workflows "
        "by an additional 2-3x."
    )
    assert len(points) == 1


def test_sec_cover_is_not_presented_as_facts(event_factory):
    event = event_factory(
        "MS 8-K 공시",
        source="SEC EDGAR",
        summary="UNITED STATES SECURITIES AND EXCHANGE COMMISSION. Principal executive offices.",
    )
    text = display_summary(event)
    assert "공시 유형: 8-K" in text
    assert "본문 요약을 확보하지 못했습니다" in text
    assert "COMMISSION" not in text


def test_sec_earnings_selects_release_not_cover(event_factory):
    text = filing_text(
        "<div>EX-99.1 Document Acme Earnings Results NEW YORK, October 14, 2026 – "
        "Acme reported revenue of $20.3 billion compared with $18.2 billion a year ago. "
        "Net income was $4.9 billion, or $3.03 per diluted share. "
        "Forward-looking statements: actual results may differ.</div>"
    )
    event = event_factory("ACME 8-K 공시", source="SEC EDGAR", summary="Cover page.")
    prepare_brief(event, text)
    assert event.article_summary.startswith("Acme reported")
    assert "$3.03" in event.article_summary
    assert "Document" not in event.article_summary
    assert "Forward-looking" not in event.article_summary


def test_article_body_overrides_clipped_api_description(event_factory):
    event = event_factory("Acme launches simulator", summary="News Summary: Acme launches a simula")
    client = SimpleNamespace(
        get=lambda _: SimpleNamespace(
            url=event.source_url,
            text="<nav>Subscribe for more.</nav><article><h1>Acme launches simulator</h1>"
            "<p>Acme launches a new simulator for chip factories.</p>"
            "<p>It achieves 2–3x acceleration in early-access testing.</p></article>",
        )
    )
    enrich_article(event, client=client)
    assert "2–3x" in event.article_summary
    assert "simula\n" not in event.article_summary
    assert "Subscribe" not in event.article_summary
    assert format_event(event).count("• ") == 2


def test_translates_individual_points_and_preserves_whole_range(monkeypatch, event_factory):
    event = event_factory(
        "Acme launches a simulator",
        summary=(
            "Acme launches a simulator for chip factories. "
            "It achieves 2-3x acceleration in early-access testing."
        ),
    )
    client = Translator(
        SimpleNamespace(
            translator_key="test",
            translator_region="global",
            translator_endpoint="https://example.com",
        )
    )
    calls = []

    def post(*_, **kwargs):
        calls.append(kwargs["json"])
        return SimpleNamespace(
            status_code=200,
            json=lambda: [
                {"translations": [{"text": t}]}
                for t in [
                    "Acme 시뮬레이터 출시",
                    "Acme가 반도체 공장용 시뮬레이터를 출시했습니다.",
                    "초기 접근 테스트에서 2-3x 가속을 달성했습니다.",
                ]
            ],
        )

    monkeypatch.setattr(requests, "post", post)
    assert client.translate(event)
    assert len(calls[0]) == 3
    assert '<span class="notranslate">2-3x</span>' in calls[0][2]["Text"]
    assert "\n• 성능·수치: 초기 테스트에서 2–3배" in display_summary(event)
    assert event.translation_hash.startswith(VERSION + ":")


def test_filing_metadata_and_selected_facts_survive_queue(repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    event.metadata.update(form="8-K", items=["2.02"], issuer="Acme")
    prepare_brief(event, "Acme reported revenue of $10 billion. Net income was $2 billion.")
    event_id = repo.ingest(event, filters)
    restored = repo.row_event(
        repo.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    )
    assert restored.metadata["items"] == ["2.02"]
    assert restored.metadata["issuer"] == "Acme"
    assert restored.article_summary == event.article_summary


def test_empty_or_old_translation_does_not_reappear(event_factory):
    event = event_factory("Acme launches simulator", summary="Acme launches a new chip simulator.")
    event.summary_ko = "잘린 예전 번역"
    event.translation_hash = "old-cache"
    assert "잘린 예전" not in display_summary(event)
    assert "Acme launches a new chip simulator." in display_summary(event)


def test_income_and_eps_cannot_swap_in_translation():
    source = (
        "Net income applicable to Acme was $4.9 billion, or $3.03 per diluted share, "
        "compared with $4.6 billion, or $2.80 per diluted share, for the same period a year ago."
    )
    result = financial_fact_korean(source)
    assert "순이익: 49억 달러 (전년 동기 46억 달러)" in result
    assert "희석 EPS: 3.03 달러 (전년 동기 2.8 달러)" in result
    assert not financial_fact_korean("Acme expects revenue of $20 billion next quarter.")
    assert not financial_fact_korean("Adjusted net income was $2 billion.")


def test_number_display_conversion_is_exact():
    assert polish_korean("매출 $20.3 billion, EPS $3.03", "") == "매출 203억 달러, EPS 3.03 달러"
    assert polish_korean("자산 $10.5 trillion", "") == "자산 10.5조 달러"
    assert (
        polish_korean("초기 접근 테스트에서 한 2-3x 가속", "an additional 2-3x")
        == "초기 테스트에서 추가로 2–3배 가속"
    )
    assert (
        polish_korean("3D 시뮬레이션이 최대 30x 향상", "up to 30x")
        == "3D 시뮬레이션이 최대 30배 향상"
    )
