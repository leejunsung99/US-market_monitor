"""Read-only live source probes. Never save stories or send Telegram messages."""

import argparse
import json
from datetime import timedelta
from pathlib import Path

import feedparser
from bs4 import BeautifulSoup

from us_market_monitor.collectors.fed import FEEDS, FedCollector
from us_market_monitor.collectors.finnhub import FinnhubCollector
from us_market_monitor.collectors.sec import SecCollector, SecMapping
from us_market_monitor.config import Config
from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError, HttpClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    config = Config.load(args.root)
    result = {"checked_at_utc": utcnow().isoformat(), "checks": {}}
    client = HttpClient(timeout=config.timeout, attempts=2, spacing=1)
    for name, url in FEEDS.items():
        try:
            response = client.get(url)
            parsed = feedparser.parse(response.content)
            if not parsed.entries or not parsed.get("version"):
                raise FetchError("invalid_or_empty_feed")
            latest = parsed.entries[0]
            # Use a one-year read-only window but only the newest release as an article probe.
            article = client.get(latest.link)
            has_article = (
                BeautifulSoup(article.text, "html.parser").select_one("#article") is not None
            )
            # Verify collector normalization on just the freshest publication, independently
            # of the live service's baseline, without persisting any source content.
            from us_market_monitor.processors.normalizer import parse_time

            date = parse_time(latest.get("published") or latest.get("updated"))
            normalized = FedCollector(client, name).collect(date, {})
            result["checks"][f"fed:{name}"] = {
                "status": "ok",
                "http_status": response.status_code,
                "entries": len(parsed.entries),
                "latest_published_utc": date.isoformat(),
                "article_layout_verified": has_article,
                "normalized_recent_entries": len(normalized),
            }
        except Exception as error:
            result["checks"][f"fed:{name}"] = {
                "status": "failed",
                "error": str(error) if isinstance(error, FetchError) else type(error).__name__,
            }
    if config.sec_user_agent:
        sec_client = HttpClient(
            headers={"User-Agent": config.sec_user_agent},
            spacing=0.25,
            timeout=config.timeout,
            attempts=2,
        )
        try:
            mapping = SecMapping(sec_client)
            cik = mapping.cik("NVDA")
            recent = SecCollector(sec_client, mapping, "NVDA").collect(
                utcnow() - timedelta(days=30), {}
            )
            result["checks"]["sec"] = {"status": "ok", "cik": cik, "recent_candidates": len(recent)}
        except Exception as error:
            result["checks"]["sec"] = {
                "status": "failed",
                "error": str(error) if isinstance(error, FetchError) else type(error).__name__,
            }
    else:
        result["checks"]["sec"] = {"status": "skipped", "reason": "SEC_USER_AGENT missing"}
    if config.finnhub_key:
        news_client = HttpClient(
            headers={"X-Finnhub-Token": config.finnhub_key},
            spacing=60 / config.finnhub_rpm,
            timeout=config.timeout,
            attempts=2,
        )
        for ticker in (None, "NVDA"):
            name = f"finnhub:{ticker or 'market'}"
            try:
                events = FinnhubCollector(news_client, ticker).collect(
                    utcnow() - timedelta(days=1), {}
                )
                result["checks"][name] = {"status": "ok", "events": len(events)}
            except Exception as error:
                result["checks"][name] = {
                    "status": "failed",
                    "error": str(error) if isinstance(error, FetchError) else type(error).__name__,
                }
    else:
        result["checks"]["finnhub"] = {"status": "skipped", "reason": "FINNHUB_API_KEY missing"}
    result["checks"]["telegram"] = {
        "status": "not_sent",
        "reason": "Run main.py --test-telegram explicitly after setting credentials",
    }
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return int(any(check["status"] == "failed" for check in result["checks"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
