import logging
from datetime import datetime
from urllib.parse import urlsplit

import feedparser
from bs4 import BeautifulSoup

from us_market_monitor.database.models import Event
from us_market_monitor.http import FetchError
from us_market_monitor.processors.normalizer import canonical_url, parse_time, plain_text

log = logging.getLogger(__name__)
FEEDS = {
    "monetary": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "regulatory": "https://www.federalreserve.gov/feeds/press_bcreg.xml",
    "speeches": "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
}


class FedCollector:
    def __init__(self, client, feed="monetary", interval=120):
        self.client, self.feed, self.interval = client, feed, interval
        self.name = f"fed:{feed}"

    def collect(self, since: datetime, cursor: dict) -> list[Event]:
        parsed = feedparser.parse(self.client.get(FEEDS[self.feed]).content)
        if parsed.bozo and not parsed.entries:
            raise FetchError("invalid_rss")
        if not parsed.get("version"):
            raise FetchError("not_an_rss_feed")
        events = []
        for entry in parsed.entries:
            try:
                published = parse_time(entry.get("published") or entry.get("updated"))
                url = canonical_url(entry.link)
                if urlsplit(url).hostname != "www.federalreserve.gov":
                    raise ValueError("Unexpected Fed source host")
                if published < since:
                    continue
                title = plain_text(entry.title)
                summary = plain_text(entry.get("summary", ""))
                if not title:
                    raise ValueError("Empty title")
                # Retrieve only linked official release pages for recent events.
                # RSS headline alone is insufficient for speeches and generic releases.
                page = BeautifulSoup(self.client.get(url).text, "html.parser")
                article = page.select_one("#article")
                if article is None:
                    raise FetchError("fed_article_layout_unrecognized")
                body = plain_text(str(article))
                events.append(
                    Event(
                        "Federal Reserve",
                        entry.get("id", url),
                        title,
                        summary,
                        url,
                        published,
                        body=body[:100000],
                        metadata={"feed": self.feed, "official": True},
                    )
                )
            except (ValueError, AttributeError, KeyError, TypeError, OverflowError):
                log.warning("%s: skipped entry with invalid publication metadata", self.name)
        return events
