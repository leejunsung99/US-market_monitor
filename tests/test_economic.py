from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from us_market_monitor.collectors.economic import EconomicCollector
from us_market_monitor.config import Config
from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.main import build_collectors
from us_market_monitor.scheduler.jobs import Radar

CPI_URL = "https://www.bls.gov/news.release/archives/cpi_10142026.htm"


def rss(
    title="CPI for all items increases 0.6% in September",
    link=CPI_URL,
    description="Consumer prices rose 0.6 percent in September and 3.7 percent over the year.",
):
    return (
        f'<rss version="2.0"><channel><title>Official releases</title><item>'
        f"<title>{title}</title><link>{link}</link>"
        "<pubDate>2026-10-14T07:50:00-04:00</pubDate>"
        f"<description>{description}</description></item></channel></rss>"
    ).encode()


class Client:
    def __init__(self, content=None, blocked=False):
        self.content = content or rss()
        self.blocked, self.calls = blocked, []

    def get(self, url):
        self.calls.append(url)
        if self.blocked and len(self.calls) == 1:
            raise FetchError("http_403")
        return SimpleNamespace(content=self.content)


def test_bls_fallback_keeps_official_host_and_respects_embargo(monkeypatch):
    monkeypatch.setattr(
        "us_market_monitor.collectors.economic.utcnow", lambda: datetime(2026, 10, 15, tzinfo=UTC)
    )
    client = Client(blocked=True)
    c = EconomicCollector(client, "cpi", "https://www.bls.gov/feed/cpi.rss")
    events = c.collect(datetime(2026, 10, 13, tzinfo=UTC), {})
    assert client.calls[-1] == "https://www.bls.gov/feed/cpi.rss?format=xml"
    assert len(events) == 1
    assert events[0].published_at == datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
    assert "3.7 percent" in events[0].article_summary


def test_bls_does_not_send_feed_entry_before_embargo(monkeypatch):
    monkeypatch.setattr(
        "us_market_monitor.collectors.economic.utcnow",
        lambda: datetime(2026, 10, 14, 12, tzinfo=UTC),
    )
    c = EconomicCollector(Client(), "cpi", "https://www.bls.gov/feed/cpi.rss")
    assert not c.collect(datetime(2026, 10, 13, tzinfo=UTC), {})


@pytest.mark.parametrize(
    "feed,title,expected",
    [
        ("ppi", "PPI for final demand increases 0.4% in September", "ppi"),
        ("employment", "Payroll employment increases; unemployment rate 4.2%", "employment"),
        ("bea", "Personal Income and Outlays, September 2026", "pce"),
        ("bea", "GDP, (Third Estimate), Industries and State GDP, 2nd Quarter 2026", "gdp"),
        ("bea", "Gross Domestic Product by State, 2nd Quarter 2026", None),
        ("bea", "U.S. International Trade in Goods and Services, August 2026", None),
    ],
)
def test_selects_only_targeted_releases(monkeypatch, feed, title, expected):
    monkeypatch.setattr(
        "us_market_monitor.collectors.economic.utcnow", lambda: datetime(2026, 10, 15, tzinfo=UTC)
    )
    link = (
        "https://www.bea.gov/news/2026/release"
        if feed == "bea"
        else CPI_URL.replace("cpi_", ("empsit" if feed == "employment" else feed) + "_")
    )
    c = EconomicCollector(Client(rss(title, link)), feed, "https://example.com/test-feed")
    events = c.collect(datetime(2026, 10, 13, tzinfo=UTC), {})
    assert len(events) == (1 if expected else 0)
    if events:
        assert events[0].metadata["indicator"] == expected


def test_official_cpi_passes_and_duplicate_is_not_queued_twice(
    classifier, repo, filters, event_factory
):
    event = event_factory(
        "CPI for all items increases 0.6% in September",
        source="BLS",
        source_url=CPI_URL,
        source_event_id=CPI_URL,
        summary="Prices rose 0.6 percent, compared with a year ago.",
        metadata={"official": True, "indicator": "cpi"},
    )
    assert classifier.classify(event) is event
    assert event.event_type == "economic_indicator"
    assert event.priority == "HIGH"
    event_id = repo.ingest(event, filters)
    assert event_id
    assert repo.ingest(event, filters) is None
    restored = repo.row_event(
        repo.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    )
    assert restored.metadata["indicator"] == "cpi"
    event.source_url = "https://example.com/cpi"
    assert classifier.classify(event) is None


def test_bad_feed_fails_without_marking_success():
    collector = EconomicCollector(
        Client(b"<html>Access denied</html>"), "cpi", "https://www.bls.gov/feed/cpi.rss"
    )
    with pytest.raises(FetchError, match="invalid_economic_rss"):
        collector.collect(utcnow(), {})


def test_initial_lookback_applies_only_to_new_economic_source(repo, classifier):
    source = EconomicCollector(Client(), "cpi", "https://www.bls.gov/feed/cpi.rss")
    before = utcnow()
    Radar([source], repo, classifier)
    baseline = repo.state(source.name)["last_cursor"]
    import json

    from us_market_monitor.processors.normalizer import parse_time

    time = parse_time(json.loads(baseline)["baseline_at"])
    assert timedelta(hours=23, minutes=59) < before - time < timedelta(hours=24, seconds=2)
    source.initial_lookback_hours = 48
    Radar([source], repo, classifier)
    assert repo.state(source.name)["last_cursor"] == baseline


def test_environment_connects_four_collectors(monkeypatch):
    import threading
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for key in ("SEC_ENABLED", "FED_ENABLED", "FINNHUB_ENABLED", "AI_SUMMARY_ENABLED"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setenv("ECONOMIC_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_SOURCES_ENABLED", "false")
    monkeypatch.setenv("RELEASE_MONITOR_ENABLED", "false")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    config = Config.load(root, root / "nonexistent.env")
    collectors = build_collectors(config, threading.Event())
    assert {c.name for c in collectors} == {
        "economic:cpi",
        "economic:ppi",
        "economic:employment",
        "economic:bea",
        "economic:jolts",
        "economic:census",
        "economic:claims",
        "economic:oil",
    }


def test_economic_release_sends_one_text_message(monkeypatch, event_factory):
    import requests

    from us_market_monitor.notifications.telegram import Telegram

    event = event_factory(summary="Consumer prices rose 0.6 percent in September.")
    event.event_type = "economic_indicator"
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return SimpleNamespace(json=lambda: {"ok": True, "result": {"message_id": 123}})

    monkeypatch.setattr(requests, "post", post)
    assert Telegram("test-token", "test-chat").send_article(event) == 123
    assert len(calls) == 1
    assert calls[0][0].endswith("/sendMessage")
    assert "0.6 percent" in calls[0][1]["text"]


@pytest.mark.parametrize(
    "indicator,summary,expected",
    [
        (
            "cpi",
            "In September, the Consumer Price Index for All Urban Consumers rose 0.6 percent, seasonally adjusted (SA), and rose 3.7 percent over the year, not seasonally adjusted (NSA). The index for all items less food and energy rose 0.3 percent in September (SA), up 2.6 percent over the year (NSA).",
            ["+0.6% (계절조정)", "+3.7% (계절조정 전)", "+0.3%", "+2.6%"],
        ),
        (
            "ppi",
            "The Producer Price Index for final demand moved up 0.4 percent in August. Prices for final demand rose 5.4 percent for the 12 months ended in August.",
            ["+0.4%", "+5.4%"],
        ),
        (
            "employment",
            "Both nonfarm payroll employment (+29,000) and the unemployment rate (4.2 percent) changed little in September.",
            ["+29,000명", "4.2%"],
        ),
        (
            "employment",
            "Total nonfarm payroll employment decreased by 23,000 in July, and the unemployment rate was unchanged at 4.1 percent.",
            ["-23,000명", "4.1%"],
        ),
        (
            "gdp",
            "Real gross domestic product (GDP) increased at an annual rate of 2.2 percent in the second quarter of 2026.",
            ["실질 GDP 증가율(연율): +2.2%"],
        ),
    ],
)
def test_economic_metrics_keep_roles_without_machine_translation(
    event_factory, indicator, summary, expected
):
    from us_market_monitor.processors.briefing import display_summary

    event = event_factory(summary=summary, metadata={"indicator": indicator})
    event.event_type = "economic_indicator"
    event.summary_ko = "incorrect translation of SA"
    result = display_summary(event)
    assert all(value in result for value in expected)
    assert "incorrect translation" not in result
