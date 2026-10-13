from datetime import timedelta

import pytest

from us_market_monitor.database.models import utcnow


@pytest.mark.parametrize(
    "title",
    [
        "NVIDIA AI chips are popular",
        "AI tariffs: a decade of history",
        "NVIDIA price target raised following quarterly results",
        "NVIDIA could acquire a new chip company",
        "NVIDIA reportedly agrees to acquire chip company",
        "Why NVIDIA shares rise today",
        "NVIDIA launches a new gaming mouse",
        "Federal Reserve meeting calendar for next year",
        "NVIDIA weekly recap: reports quarterly results",
        "Small unknown company announces quarterly results",
        "CPI released at 3.0% with no confirmed consensus",
        "Microsoft acquires small startup for $2 million",
    ],
)
def test_ordinary_and_speculative_news_rejected(classifier, event_factory, title):
    assert classifier.classify(event_factory(title)) is None


@pytest.mark.parametrize(
    "title,event_type,priority",
    [
        ("NVIDIA reports quarterly results", "earnings", "HIGH"),
        ("Microsoft agrees to acquire major software company", "acquisition", "HIGH"),
        ("Intel halts production at major factory", "production_disruption", "HIGH"),
        ("Federal Reserve issues FOMC statement", "monetary_policy", "HIGH"),
        ("Federal Reserve announces emergency liquidity facility", "monetary_policy", "CRITICAL"),
        (
            "US government announces new AI chip export restrictions",
            "export_restriction",
            "CRITICAL",
        ),
        ("NVIDIA unexpectedly cuts revenue outlook", "earnings_guidance", "CRITICAL"),
        ("Microsoft signs $20 billion contract for data centers", "contract_investment", "HIGH"),
        (
            "World's largest steelmaker halts production at major factory",
            "production_disruption",
            "HIGH",
        ),
    ],
)
def test_evidence_and_materiality(classifier, event_factory, title, event_type, priority):
    result = classifier.classify(event_factory(title))
    assert result is not None
    assert result.event_type == event_type
    assert result.priority == priority


def test_query_ticker_is_not_evidence(classifier, event_factory):
    event = event_factory(
        "Unknown business reports quarterly results",
        metadata={"query_ticker": "NVDA", "provider": "finnhub"},
    )
    assert classifier.classify(event) is None


def test_related_tickers_only_when_named(classifier, event_factory):
    event = classifier.classify(
        event_factory("US government announces AI chip export restrictions")
    )
    assert event.related_tickers == ()


def test_untrusted_source_date_or_url_rejected(classifier, event_factory):
    assert classifier.classify(event_factory(source="")) is None
    assert classifier.classify(event_factory(source_url="javascript:alert(1)")) is None
    assert classifier.classify(event_factory(published_at=utcnow() + timedelta(days=1))) is None


def test_regular_sec_filing_is_not_important(classifier, event_factory):
    event = event_factory(
        "NVDA 8-K 공시",
        source="SEC EDGAR",
        related_tickers=("NVDA",),
        body="Annual meeting of shareholders",
        metadata={"official": True},
    )
    assert classifier.classify(event) is None


def test_sec_earnings_item_requires_release_evidence(classifier, event_factory):
    event = event_factory(
        "NVDA 8-K 공시",
        source="SEC EDGAR",
        related_tickers=("NVDA",),
        summary="Item 2.02 Results of Operations and Financial Condition",
        body="Item 2.02. Financial results are attached in the press release.",
        metadata={"official": True, "items": ["2.02"]},
    )
    result = classifier.classify(event)
    assert result.priority == "HIGH"
    assert result.event_type == "earnings"


def test_threshold_is_configurable(classifier, event_factory):
    classifier.filters["high_threshold"] = 6
    assert classifier.classify(event_factory()) is None
