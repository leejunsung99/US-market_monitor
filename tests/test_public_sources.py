from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from us_market_monitor.collectors.calendar import CensusCalendarCollector
from us_market_monitor.collectors.official import OfficialCollector
from us_market_monitor.collectors.public_economy import PublicEconomyCollector
from us_market_monitor.database.models import utcnow
from us_market_monitor.notifications.formatter import format_event
from us_market_monitor.processors.briefing import polish_korean


def test_claims_uses_release_date_not_week_ending(monkeypatch):
    text = (
        "TRANSMISSION EMBARGOED 8:30 A.M. (Eastern) Thursday, October 8, 2026 "
        "UNEMPLOYMENT INSURANCE WEEKLY CLAIMS SEASONALLY ADJUSTED DATA "
        "In the week ending October 3, initial claims were 197,000."
    )
    monkeypatch.setattr(
        "us_market_monitor.collectors.public_economy.PdfReader",
        lambda _: SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)]),
    )
    client = SimpleNamespace(get=lambda _: SimpleNamespace(content=b"%PDF-test"))
    collector = PublicEconomyCollector(client, "claims", "https://www.dol.gov/ui/data.pdf")
    events = collector.collect(datetime(2026, 10, 1, tzinfo=UTC), {})
    assert events[0].published_at == datetime(2026, 10, 8, 12, 30, tzinfo=UTC)
    assert events[0].metadata["indicator"] == "claims"
    assert "197,000" in events[0].summary


def test_eia_uses_release_date_and_thousand_barrel_units():
    text = "Release_Date,Date,Region,Sourcekey,Value,Suppression_Flag\n2026-10-07,2026-10-02,U.S.,WCESTUS1,424134,\n2026-10-07,2026-09-25,U.S.,WCESTUS1,427320,\n"
    client = SimpleNamespace(get=lambda _: SimpleNamespace(text=text))
    events = PublicEconomyCollector(client, "oil", "https://ir.eia.gov/wpsr/psw00.csv").collect(
        datetime(2026, 10, 1, tzinfo=UTC), {}
    )
    assert events[0].published_at.day == 7
    assert "424,134천 배럴" in events[0].summary
    assert "-3,186천 배럴" in events[0].summary


def test_same_static_indicator_url_does_not_hide_next_release(
    classifier, repo, filters, event_factory
):
    now = utcnow() - timedelta(days=62)
    for number in (1, 2):
        event = event_factory(
            "Advance Monthly Sales for Retail and Food Services",
            source="Census",
            source_url="https://www.census.gov/retail/index.html",
            source_event_id=f"retail:{number}",
            published_at=now + timedelta(days=30 * number),
            metadata={
                "official": True,
                "verified_official": True,
                "indicator": "census_retail_sales",
            },
            summary="Retail sales rose 1.2 percent.",
        )
        assert repo.ingest(classifier.classify(event), filters)
    assert repo.status()["important_events"] == 2


def test_comparison_does_not_make_current_earnings_historical(classifier, event_factory):
    event = event_factory(
        "NVIDIA reports quarterly results compared with last year",
        summary="Revenue was $10 billion, compared with $8 billion a year ago.",
    )
    assert classifier.classify(event).event_type == "earnings"
    historical = event_factory(
        "NVIDIA announced new GPU architecture last year",
        summary="NVIDIA announced new GPU architecture last year.",
    )
    assert classifier.classify(historical) is None


def test_official_broad_scope_still_requires_material_company_news(
    classifier, event_factory
):
    title = "Acme announces upcoming investor conference"
    official = event_factory(
        title,
        source="Official/amd",
        source_url="https://ir.amd.com/news-events/press-releases/detail/1",
        metadata={"official": True, "verified_official": True, "issuer_ticker": "AMD"},
        related_tickers=("AMD",),
    )
    assert classifier.classify(official) is None
    assert classifier.classify(event_factory(title)) is None


def test_lifestyle_headlines_rejected_even_with_product_and_financial_body(
    classifier, event_factory
):
    titles = [
        'Bünyamin Aydın’s new collection, “From Earth to Sky,” soars with iPad Pro',
        "Explore this year's most searched Halloween trends",
        "Check out this year's top Halloween trends",
        "올해 가장 화제가 된 할로윈 트렌드를 확인해 보세요.",
        '분야민 아이딘의 신작 컬렉션 "From Earth to Sky"가 아이패드 프로와 함께 빛난다',
        "How to use Gemini for your Halloween costume ideas",
    ]
    for title in titles:
        event = event_factory(
            title,
            source="Official/apple",
            source_url="https://www.apple.com/newsroom/story",
            related_tickers=("AAPL",),
            metadata={"official": True, "verified_official": True, "issuer_ticker": "AAPL"},
            summary="Discover how creators use the new iPad Pro.",
            body="Apple reports quarterly results. Apple introduces a new iPad Pro.",
        )
        assert classifier.classify(event) is None, title
        assert (
            event.metadata["classification_decision"]["reason"]
            == "lifestyle_or_product_usage_story"
        )


def test_material_company_announcements_still_pass(classifier, event_factory):
    for title, ticker in [
        ("Apple reports quarterly results", "AAPL"),
        ("Apple introduces the new iPad Pro", "AAPL"),
        ("NVIDIA launches a new CUDA-X library cuPotential", "NVDA"),
        ("Google launches a new AI model", "GOOGL"),
    ]:
        event = event_factory(
            title,
            source="Official/test",
            source_url="https://example.com/release",
            related_tickers=(ticker,),
            metadata={"official": True, "verified_official": True, "issuer_ticker": ticker},
        )
        assert classifier.classify(event) is not None, title


def test_broad_government_release_remains_enabled(classifier, event_factory):
    event = event_factory(
        "Commerce Department publishes enforcement notice",
        source="BIS",
        source_url="https://www.bis.gov/news-updates/notice",
        metadata={"official": True, "verified_official": True},
    )
    assert classifier.classify(event).event_type == "official_announcement"


def test_fomc_minutes_are_separate_from_rate_decision(classifier, event_factory):
    event = event_factory(
        "Minutes of the Federal Open Market Committee, September 15–16, 2026",
        source="Federal Reserve",
        source_url="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261007a.htm",
        metadata={"official": True},
    )
    assert classifier.classify(event).event_type == "fomc_minutes"


def test_missing_release_alert_is_sent_only_once(repo, filters):
    when = utcnow() - timedelta(minutes=45)
    repo.save_expected_releases(
        "calendar:census",
        [
            {
                "indicator": "census_retail_sales",
                "title": "Retail Sales",
                "scheduled_at": when.isoformat(),
                "source_url": "https://www.census.gov/economic-indicators/calendar-listview.html",
            }
        ],
    )
    repo.queue_missing_releases(filters)
    repo.queue_missing_releases(filters)
    assert repo.status()["important_events"] == 1
    event = repo.row_event(repo.db.execute("SELECT * FROM events").fetchone())
    assert event.event_type == "monitor_alert"
    assert "수집 기록" in event.summary


def test_observed_release_does_not_raise_missing_alert(repo, classifier, filters, event_factory):
    when = utcnow() - timedelta(minutes=45)
    event = event_factory(
        "Advance Monthly Sales for Retail and Food Services",
        source="Census",
        source_url="https://www.census.gov/retail/index.html",
        source_event_id="retail:today",
        published_at=when,
        metadata={"official": True, "verified_official": True, "indicator": "census_retail_sales"},
        summary="Retail sales rose 1.2 percent.",
    )
    repo.ingest(classifier.classify(event), filters)
    repo.save_expected_releases(
        "calendar:census",
        [
            {
                "indicator": "census_retail_sales",
                "title": "Retail Sales",
                "scheduled_at": when.isoformat(),
                "source_url": event.source_url,
            }
        ],
    )
    repo.queue_missing_releases(filters)
    assert repo.status()["important_events"] == 1


def test_canceled_calendar_item_does_not_raise_alert(repo, filters):
    entry = {
        "indicator": "census_retail_sales",
        "title": "Retail Sales",
        "scheduled_at": (utcnow() - timedelta(minutes=45)).isoformat(),
        "source_url": "https://www.census.gov/retail/",
    }
    repo.save_expected_releases("calendar:census", [entry])
    repo.save_expected_releases("calendar:census", [])
    repo.queue_missing_releases(filters)
    assert repo.status()["important_events"] == 0


def test_calendar_uses_current_year_link_and_eastern_time(monkeypatch):
    monkeypatch.setattr(
        "us_market_monitor.collectors.calendar.utcnow", lambda: datetime(2026, 10, 15, tzinfo=UTC)
    )
    calls = []

    def get(url):
        calls.append(url)
        return SimpleNamespace(
            text='<a href="/economic-indicators/calendar-listview-2026.html">2026 Economic Indicator Calendar</a>'
            if len(calls) == 1
            else '<table><tr><td>Advance Monthly Sales for Retail and Food Services</td><td sorttable_customkey="202610160830">October 16, 2026</td></tr></table>'
        )

    entries = CensusCalendarCollector(SimpleNamespace(get=get)).collect(utcnow(), {})
    assert calls[-1].endswith("calendar-listview-2026.html")
    assert entries[0]["scheduled_at"] == "2026-10-16T08:30:00-04:00"


def test_date_only_release_is_not_presented_with_invented_time(event_factory):
    event = event_factory(
        metadata={"publication_precision": "date", "publication_date": "2026-10-15"}
    )
    assert "2026-10-15 (발표일 · 시각 미제공)" in format_event(event)
    assert polish_korean("NT$29.43, US$4.60", "") == "NT$29.43, US$4.60"


def test_foreign_dollars_are_not_compared_as_us_dollars(event_factory):
    from us_market_monitor.processors.earnings import earnings_facts

    event = event_factory(
        "TSMC reports quarterly results",
        summary="Revenue was NT$1,494.24 billion. Net income was NT$763.06 billion. In US dollars, revenue was $46.72 billion.",
    )
    metrics = earnings_facts(event)["metrics"]
    assert metrics["actual:revenue"] == ["46720000000"]
    assert "actual:profit" not in metrics


def test_official_cursor_avoids_fetching_same_article_twice():
    now = (utcnow() - timedelta(hours=1)).isoformat()
    feed = f'<rss version="2.0"><channel><item><title>AMD reports quarterly results</title><link>https://ir.amd.com/article</link><pubDate>{now}</pubDate><description>AMD reports revenue of $10 billion.</description></item></channel></rss>'.encode()
    calls = []

    def get(url):
        calls.append(url)
        return SimpleNamespace(
            content=feed, text="<article><p>AMD reports revenue of $10 billion.</p></article>"
        )

    collector = OfficialCollector(
        SimpleNamespace(get=get),
        {
            "name": "amd",
            "kind": "rss",
            "url": "https://ir.amd.com/rss",
            "hosts": ["ir.amd.com"],
            "ticker": "AMD",
        },
    )
    cursor = {}
    assert len(collector.collect(utcnow() - timedelta(days=1), cursor)) == 1
    assert collector.collect(utcnow() - timedelta(days=1), cursor) == []
    assert calls.count("https://ir.amd.com/article") == 1


def test_rejected_image_falls_back_but_ambiguous_delivery_does_not(monkeypatch, event_factory):
    import pytest

    from us_market_monitor.notifications.telegram import DeliveryError, Telegram

    event = event_factory()
    event.image_url = "https://example.com/photo.jpg"
    client = Telegram("test", "chat")
    calls = []

    def request(method, payload):
        calls.append((method, payload))
        if len(calls) == 1:
            raise DeliveryError("telegram_rejected_400")
        return 99

    monkeypatch.setattr(client, "_request", request)
    assert client.send_article(event) == 99
    assert calls[1][0] == "sendMessage"
    assert "<b><u>" in calls[1][1]["text"]

    def unknown(*args):
        raise DeliveryError("delivery_outcome_unknown", ambiguous=True)

    monkeypatch.setattr(client, "_request", unknown)
    with pytest.raises(DeliveryError, match="delivery_outcome_unknown"):
        client.send_article(event)
