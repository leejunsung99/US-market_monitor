"""Public company newsrooms and US Commerce BIS announcements."""

import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import feedparser
from bs4 import BeautifulSoup

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.notifications.article import article_text, image_url
from us_market_monitor.processors.briefing import prepare_brief
from us_market_monitor.processors.normalizer import canonical_url, digest, parse_time, plain_text

log = logging.getLogger(__name__)


class OfficialCollector:
    def __init__(self, client, definition, interval=600, initial_lookback_hours=24):
        self.client, self.definition = client, definition
        self.name = "official:" + definition["name"]
        self.interval, self.initial_lookback_hours = interval, initial_lookback_hours

    def collect(self, since, cursor):
        d = self.definition
        response = self.client.get(d["url"])
        candidates = []
        if d["kind"] == "rss":
            parsed = feedparser.parse(response.content)
            if not parsed.get("version") or (parsed.bozo and not parsed.entries):
                raise FetchError("invalid_official_rss")
            for e in parsed.entries:
                try:
                    candidates.append(
                        (
                            plain_text(e.title),
                            e.link,
                            parse_time(e.get("published") or e.get("updated")),
                            plain_text(e.get("summary", "")),
                            "time",
                        )
                    )
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    log.warning("%s: skipped invalid entry metadata", self.name)
        else:
            soup = BeautifulSoup(response.text, "html.parser")
            if d["kind"] == "bis":
                for card in soup.select("li[data-date]"):
                    anchor = card.select_one('a[href*="/press-release/"]')
                    if anchor:
                        raw = re.sub(r"\s*\(.*\)$", "", card["data-date"])
                        date = datetime.strptime(raw, "%a %b %d %Y %H:%M:%S GMT%z")
                        candidates.append(
                            (
                                anchor.get_text(" ", strip=True),
                                anchor["href"],
                                date,
                                plain_text(str(card)),
                                "time",
                            )
                        )
            else:
                for anchor in soup.select('a[href^="/english/news/"]'):
                    date = anchor.select_one(".date")
                    if not date:
                        continue
                    published = datetime.strptime(
                        date.get_text(strip=True).strip("()"), "%Y/%m/%d"
                    ).replace(tzinfo=ZoneInfo("Asia/Taipei"))
                    title = (
                        anchor.get_text(" ", strip=True)
                        .replace(date.get_text(strip=True), "")
                        .strip()
                    )
                    candidates.append((title, anchor["href"], published, "", "date"))
            if not candidates:
                raise FetchError("official_listing_layout_unrecognized")
        events, seen = [], set()
        known = cursor.setdefault("article_fingerprints", {})
        cursor["article_errors"] = 0
        cursor["feed_entries"] = len(candidates)
        if candidates:
            cursor["latest_release_at"] = max(c[2] for c in candidates).isoformat()
        for title, link, published, summary, precision in candidates:
            try:
                url = canonical_url(urljoin(d["url"], link))
            except ValueError:
                continue
            if urlsplit(url).hostname not in d["hosts"] or urlsplit(url).scheme != "https":
                continue
            if url in seen or published < since or published > utcnow():
                continue
            seen.add(url)
            fingerprint = digest(title + "\n" + summary + "\n" + published.isoformat())
            if known.get(url) == fingerprint:
                continue
            fetched = True
            try:
                page = BeautifulSoup(self.client.get(url).text, "html.parser")
            except FetchError as error:
                page, fetched = BeautifulSoup("", "html.parser"), False
                cursor["article_errors"] = cursor.get("article_errors", 0) + 1
                log.warning(
                    "%s: article unavailable (%s); preserving release headline", self.name, error
                )
            body = article_text(page)
            if not body:
                # Some official sites use div-only article containers. Scope to
                # main content and discard navigation instead of reading the page.
                main = page.select_one('main, [role="main"], .content-area')
                if main:
                    for node in main.select("nav, header, footer, aside, script, style"):
                        node.decompose()
                    body = plain_text(str(main))
            if not body:
                description = page.select_one(
                    'meta[property="og:description"], meta[name="description"]'
                )
                body = description.get("content", "") if description else summary
            ticker = d.get("ticker", "")
            event = Event(
                "BIS" if d["kind"] == "bis" else "Official/" + d["name"],
                url,
                title,
                (summary or body)[:3000],
                url,
                published,
                body=body[:100000],
                related_tickers=(ticker,) if ticker else (),
                metadata={
                    "official": True,
                    "verified_official": True,
                    "issuer_ticker": ticker,
                    "publication_precision": precision,
                    "publication_date": published.date().isoformat(),
                },
            )
            photo = page.select_one('meta[property="og:image"], meta[name="twitter:image"]')
            if photo:
                event.image_url = image_url(urljoin(url, photo.get("content", "")))
            prepare_brief(event, body)
            events.append(event)
            if fetched:
                known[url] = fingerprint
        cursor["article_fingerprints"] = {key: value for key, value in known.items() if key in seen}
        return events
