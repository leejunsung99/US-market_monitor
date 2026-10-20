from datetime import UTC, datetime

import pytest

BIGGO_TITLE = "China's CXMT Profit Growth Slows Sharply in Q3; Shares Plunge 8%"
BIGGO_LEAD = (
    "China's largest DRAM maker, Changxin Memory Technologies (CXMT), plunged more than "
    "8% in early trading on the Shanghai stock exchange on October 19, with its market "
    "capitalization falling below the 3 trillion yuan threshold. The selloff came right "
    "after the opening bell, even though the third-quarter earnings preview and share "
    "buyback plan the company released on the night of October 16 had been read as positive."
)


@pytest.mark.parametrize("summary", ["", BIGGO_LEAD])
def test_actual_biggo_cxmt_article_passes(classifier, event_factory, summary):
    event = classifier.classify(event_factory(BIGGO_TITLE, summary=summary))
    assert event is not None
    assert event.event_type == "industry_outlook"
    assert event.priority == "HIGH"
    assert event.actor == "CXMT"
    assert event.related_tickers == ()
    assert event.impact_tickers == ("MU",)
    assert "Shares Plunge" not in event.summary
    assert event.summary.startswith("China's CXMT Profit Growth Slows Sharply")


@pytest.mark.parametrize(
    "title",
    [
        "CXMT profit growth slowed to 1-4% in Q3",
        "Acme revenue growth decelerates sharply",
        "Micron gross margins shrink by 5%",
        "DRAM contract price growth slows to 13-18%",
        "Memory demand drops 20% in latest quarter",
        "Oil inventories jump 15%",
        "Acme order growth accelerates sharply",
        "메모리 가격 상승률 급감",
        "마이크론 영업이익률 5% 하락",
    ],
)
def test_material_trends_are_not_limited_to_known_companies(classifier, event_factory, title):
    event = classifier.classify(event_factory(title, summary=""))
    assert event is not None
    assert event.event_type == "industry_outlook"
    assert event.priority == "HIGH"


@pytest.mark.parametrize(
    "title,summary",
    [
        ("CXMT shares plunge 8%", ""),
        ("CXMT profit growth slows; shares fall 8%", ""),
        ("CXMT profit growth could slow sharply", ""),
        ("CXMT profit growth slows sharply", "Sponsored stock picks for investors."),
        ("Weekly recap: CXMT profit growth slows sharply", ""),
        ("Last year CXMT profit growth slowed sharply", ""),
        ("Industry update", "DRAM demand fell 50% in 2024."),
        ("Acme profit growth did not slow sharply", ""),
        ("DRAM prices remain stable at $10", ""),
        ("Industry update", "No signs of DRAM demand slowing sharply."),
        ("CXMT profit growth slows", "Shares fell 8%."),
        ("CXMT profit growth may slow by 30%", ""),
    ],
)
def test_stock_moves_speculation_and_history_stay_out(classifier, event_factory, title, summary):
    assert classifier.classify(event_factory(title, summary=summary)) is None


def test_evidence_is_bound_to_its_sentence(classifier, event_factory):
    event = classifier.classify(
        event_factory(
            "Memory industry update",
            summary="CXMT profit growth slowed to 1-4%. Micron is another memory producer.",
        )
    )
    assert event.related_tickers == ()
    assert event.impact_tickers == ("MU",)


def test_future_and_invalid_sources_stay_out(classifier, event_factory):
    assert classifier.classify(event_factory(BIGGO_TITLE, source="")) is None
    assert classifier.classify(event_factory(BIGGO_TITLE, source_url="javascript:bad")) is None
    event = event_factory(BIGGO_TITLE, published_at=datetime(2099, 1, 1, tzinfo=UTC))
    assert classifier.classify(event) is None


def test_outlook_gate_can_be_disabled(classifier, event_factory):
    classifier.filters["industry_outlook"]["enabled"] = False
    assert classifier.classify(event_factory(BIGGO_TITLE, summary=BIGGO_LEAD)) is None


def test_specific_critical_guidance_keeps_its_priority(classifier, event_factory):
    event = classifier.classify(
        event_factory("NVIDIA unexpectedly cuts revenue outlook; profit growth slows sharply")
    )
    assert event.event_type == "earnings_guidance"
    assert event.priority == "CRITICAL"


def test_cxmt_industry_events_do_not_merge_with_technology_announcements(
    classifier, event_factory, repo, filters
):
    technology = classifier.classify(
        event_factory("CXMT reports progress on new DRAM architecture")
    )
    outlook = classifier.classify(
        event_factory(BIGGO_TITLE, source_event_id="200", source_url="https://example.com/200")
    )
    assert repo.ingest(technology, filters)
    assert repo.ingest(outlook, filters)
