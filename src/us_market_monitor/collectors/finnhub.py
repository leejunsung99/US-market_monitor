import logging
from datetime import datetime

from us_market_monitor.database.models import Event
from us_market_monitor.http import FetchError
from us_market_monitor.processors.normalizer import canonical_url, parse_time, plain_text

log = logging.getLogger(__name__)
BASE = "https://finnhub.io/api/v1"


class FinnhubCollector:
    def __init__(self, client, ticker=None, interval=120):
        self.client, self.ticker, self.interval = client, ticker, interval
        self.name = f"finnhub:{ticker or 'market'}"

    def collect(self, since: datetime, cursor: dict) -> list[Event]:
        if self.ticker:
            params = {
                "symbol": self.ticker,
                "from": since.date().isoformat(),
                "to": datetime.now(since.tzinfo).date().isoformat(),
            }
            raw = self.client.json(f"{BASE}/company-news", params=params)
        else:
            # Time overlap is intentional; IDs are deduplicated durably in the repository.
            raw = self.client.json(f"{BASE}/news", params={"category": "general"})
        if not isinstance(raw, list):
            raise FetchError("unexpected_news_payload_or_plan_denied")
        events = []
        for item in raw:
            try:
                published = parse_time(item["datetime"])
                if published < since:
                    continue
                title, source = plain_text(item["headline"]), plain_text(item["source"])
                if not title or not source or not item.get("id"):
                    raise ValueError("Missing source metadata")
                # A company-news query is not proof the queried ticker is an actor in the story.
                events.append(
                    Event(
                        f"Finnhub/{source}",
                        str(item["id"]),
                        title,
                        plain_text(item.get("summary", "")),
                        canonical_url(item["url"]),
                        published,
                        metadata={"provider": "finnhub", "query_ticker": self.ticker},
                    )
                )
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                log.warning("%s: skipped entry with invalid source/date", self.name)
        return events
