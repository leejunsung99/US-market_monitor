from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from us_market_monitor.collectors.fed import FedCollector
from us_market_monitor.collectors.finnhub import FinnhubCollector
from us_market_monitor.collectors.sec import SecCollector, SecMapping
from us_market_monitor.http import FetchError

NOW = datetime(2026, 1, 10, 18, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, payload=None, document=""):
        self.payload, self.document = payload, document
        self.calls = []

    def json(self, url, *, params=None):
        self.calls.append((url, params))
        return self.payload

    def get(self, url):
        self.calls.append((url, None))
        text = self.document[url] if isinstance(self.document, dict) else self.document
        return SimpleNamespace(content=text.encode(), text=text)


def test_finnhub_market_news_normalization():
    payload = [
        dict(
            id=12,
            datetime=NOW.timestamp(),
            headline="NVIDIA reports quarterly results",
            summary="<b>Verified</b>",
            source="Publisher",
            url="https://example.com/a",
        )
    ]
    client = FakeClient(payload)
    result = FinnhubCollector(client).collect(NOW - timedelta(seconds=1), {})
    assert result[0].summary == "Verified"
    assert result[0].published_at == NOW
    assert result[0].related_tickers == ()
    assert client.calls[0][0].endswith("/news")


def test_company_query_and_old_news_filter():
    payload = [
        dict(
            id=12,
            datetime=(NOW - timedelta(days=1)).timestamp(),
            headline="Old",
            source="Publisher",
            url="https://example.com/a",
        )
    ]
    client = FakeClient(payload)
    assert FinnhubCollector(client, "NVDA").collect(NOW, {}) == []
    assert client.calls[0][1]["symbol"] == "NVDA"
    assert client.calls[0][1]["from"] == "2026-01-10"


@pytest.mark.parametrize("payload", [{"error": "plan access denied"}, None, "bad"])
def test_finnhub_invalid_payload_is_failure(payload):
    with pytest.raises(FetchError):
        FinnhubCollector(FakeClient(payload)).collect(NOW, {})


def test_finnhub_missing_date_source_skipped():
    client = FakeClient([{"headline": "missing metadata"}])
    assert FinnhubCollector(client).collect(NOW, {}) == []


def test_fed_rss_date_and_article_extraction():
    feed = """<rss version="2.0"><channel><title>Fed</title><link>https://www.federalreserve.gov</link>
      <description>Feed</description><item><title>Federal Reserve issues FOMC statement</title>
      <link>https://www.federalreserve.gov/a.htm</link><guid>release-1</guid>
      <pubDate>Sat, 10 Jan 2026 18:00:00 GMT</pubDate><description>Policy</description>
      </item></channel></rss>"""
    from us_market_monitor.collectors.fed import FEEDS

    client = FakeClient(
        document={
            FEEDS["monetary"]: feed,
            "https://www.federalreserve.gov/a.htm": '<nav>noise</nav><div id="article">Policy release</div>',
        }
    )
    result = FedCollector(client).collect(NOW, {})
    assert len(result) == 1
    assert result[0].body == "Policy release"
    assert result[0].published_at == NOW


def test_fed_invalid_xml_fails():
    with pytest.raises(FetchError):
        FedCollector(FakeClient(document="access denied")).collect(NOW, {})


def test_sec_mapping_cik_padded_and_item_screening():
    client = FakeClient({"0": {"ticker": "NVDA", "cik_str": 1045810}})
    mapping = SecMapping(client)
    assert mapping.cik("NVDA") == 1045810
    payload = {
        "name": "NVIDIA",
        "filings": {
            "recent": {
                "form": ["10-Q", "8-K", "8-K", "6-K"],
                "acceptanceDateTime": [NOW.isoformat()] * 4,
                "items": ["", "5.07", "2.02", ""],
                "accessionNumber": ["0001045810-26-000001"] * 4,
                "primaryDocument": ["release.htm"] * 4,
            }
        },
    }
    client.payload = payload
    client.document = "NVIDIA reports quarterly results"
    result = SecCollector(client, mapping, "NVDA").collect(NOW, {})
    assert len(result) == 2
    assert "/CIK0001045810.json" in client.calls[1][0]
    assert result[0].metadata["items"] == ["2.02"]
    assert result[0].related_tickers == ("NVDA",)
    assert "/1045810/000104581026000001/release.htm" in result[0].source_url


def test_sec_old_filings_do_not_fetch_bodies():
    client = FakeClient({"0": {"ticker": "NVDA", "cik_str": 1045810}})
    mapping = SecMapping(client)
    mapping.cik("NVDA")
    client.payload = {
        "filings": {
            "recent": {
                "form": ["8-K"],
                "acceptanceDateTime": [NOW.isoformat()],
                "accessionNumber": ["0001045810-26-000001"],
                "primaryDocument": ["release.htm"],
            }
        }
    }
    assert SecCollector(client, mapping, "NVDA").collect(NOW + timedelta(seconds=1), {}) == []
    assert len(client.calls) == 2


def test_sec_invalid_submissions_fail():
    mapping = SimpleNamespace(cik=lambda _: 1045810)
    with pytest.raises(FetchError):
        SecCollector(FakeClient({}), mapping, "NVDA").collect(NOW, {})
