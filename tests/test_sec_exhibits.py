from datetime import UTC, datetime
from types import SimpleNamespace

from us_market_monitor.collectors.sec import SecCollector


def test_sec_6k_follows_only_local_exhibit_links():
    published = datetime(2026, 1, 1, tzinfo=UTC)
    payload = {
        "name": "TSMC",
        "filings": {
            "recent": {
                "form": ["6-K"],
                "acceptanceDateTime": [published.isoformat()],
                "items": [""],
                "accessionNumber": ["0001046179-26-000001"],
                "primaryDocument": ["cover.htm"],
            }
        },
    }
    cover = """<h1>6-K Cover</h1><a href="ex991.htm">Exhibit 99.1 Press release</a>
    <a href="https://untrusted.com/99.htm">Press release 99.2</a>
    <a href="../escape.htm">Exhibit 99.2</a>"""

    class Client:
        def __init__(self):
            self.urls = []

        def json(self, *_):
            return payload

        def get(self, url):
            self.urls.append(url)
            return SimpleNamespace(
                text=cover
                if url.endswith("cover.htm")
                else "TSMC announces quarterly financial results"
            )

    client = Client()
    collector = SecCollector(client, SimpleNamespace(cik=lambda _: 1046179), "TSM")
    events = collector.collect(published, {})
    assert "TSMC announces quarterly financial results" in events[0].body
    assert len(client.urls) == 2
    assert client.urls[-1].endswith("/000104617926000001/ex991.htm")
