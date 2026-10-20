"""Free official release feeds. Consensus is never inferred from reported figures."""

import logging
import re
from datetime import datetime, time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.processors.briefing import prepare_brief
from us_market_monitor.processors.normalizer import canonical_url, parse_time, plain_text

log = logging.getLogger(__name__)
DEFAULT_FEEDS = {
    "cpi": ("BLS_CPI_RSS_URL", "https://www.bls.gov/feed/cpi.rss"),
    "ppi": ("BLS_PPI_RSS_URL", "https://www.bls.gov/feed/ppi.rss"),
    "employment": ("BLS_EMPLOYMENT_RSS_URL", "https://www.bls.gov/feed/empsit.rss"),
    "bea": ("BEA_RSS_URL", "https://apps.bea.gov/rss/rss.xml"),
    "jolts": ("BLS_JOLTS_RSS_URL", "https://www.bls.gov/feed/jolts.rss"),
    "census": ("CENSUS_RSS_URL", "https://www.census.gov/economic-indicators/indicator.xml"),
}
LABELS = {
    "cpi": "소비자물가(CPI)",
    "ppi": "생산자물가(PPI)",
    "employment": "고용·실업률",
    "gdp": "국내총생산(GDP)",
    "pce": "개인소득·소비·PCE",
    "jolts": "구인·이직(JOLTS)",
    "claims": "주간 실업수당 청구",
    "oil_inventories": "주간 원유·석유제품 재고",
}
CENSUS_INDICATORS = {
    "retail_sales": "소매판매",
    "durable_goods": "내구재 주문",
    "housing_starts": "주택착공",
    "home_sales": "신규주택 판매",
    "bfs": "기업 설립",
    "wholesale": "도매 판매·재고",
    "intl_trade": "국제 무역",
    "mfg_orders": "제조업 주문",
    "construction_value": "건설 지출",
    "adv_intl_trade": "상품 무역 속보",
    "adv_mwts": "도매 재고 속보",
    "adv_mrts": "소매 재고 속보",
    "business_sales": "기업 재고·판매",
    "esbr075": "서비스업 분기 조사",
    "qfr_retail": "소매기업 분기 실적",
    "qfr_trade": "기업 분기 실적",
    "rental_vacancy": "임대주택 공실률",
    "homeownership": "주택 보유율",
}
LABELS.update({"census_" + key: value for key, value in CENSUS_INDICATORS.items()})


def indicator_for(feed, title):
    patterns = {
        "cpi": r"\bCPI\b|consumer price index",
        "ppi": r"\bPPI\b|producer price index",
        "employment": r"payroll employment|nonfarm payroll|employment situation|unemployment rate",
        "jolts": r"job openings|hires|separations|labor turnover",
    }
    if feed in patterns:
        return feed if re.search(patterns[feed], title, re.I) else None
    if feed == "bea":
        if re.search(r"^Personal Income and Outlays\b", title, re.I):
            return "pce"
        if re.search(r"^(?:GDP\b|Gross Domestic Product\b)", title, re.I) and not re.search(
            r"^Gross Domestic Product by (?:State|County|Metropolitan)", title, re.I
        ):
            return "gdp"
    return None


def trusted_release(event):
    indicator = event.metadata.get("indicator")
    if indicator not in LABELS or not event.metadata.get("official"):
        return False
    parts = urlsplit(event.source_url)
    if indicator in {"cpi", "ppi", "employment", "jolts"}:
        prefix = {"cpi": "cpi", "ppi": "ppi", "employment": "empsit", "jolts": "jolts"}[indicator]
        valid = (
            event.source == "BLS"
            and parts.hostname == "www.bls.gov"
            and bool(
                re.fullmatch(r"/news\.release/archives/" + prefix + r"_\d{8}\.htm", parts.path)
            )
        )
        feed = indicator
    elif indicator in {"gdp", "pce"}:
        valid = (
            event.source == "BEA"
            and parts.hostname in {"bea.gov", "www.bea.gov"}
            and parts.path.startswith("/news/")
        )
        feed = "bea"
    else:
        hosts = {"claims": ("DOL", "www.dol.gov"), "oil_inventories": ("EIA", "www.eia.gov")}
        expected_source, expected_host = hosts.get(indicator, ("Census", "www.census.gov"))
        return bool(
            event.metadata.get("verified_official")
            and event.source == expected_source
            and parts.scheme == "https"
            and parts.hostname == expected_host
            and re.search(r"\d", event.summary)
        )
    return bool(
        valid
        and parts.scheme == "https"
        and indicator_for(feed, event.title) == indicator
        and re.search(r"\d", event.summary)
    )


class EconomicCollector:
    def __init__(self, client, feed, url, interval=120, initial_lookback_hours=24):
        self.client, self.feed, self.url = client, feed, url
        self.interval, self.initial_lookback_hours = interval, initial_lookback_hours
        self.name = "economic:" + feed

    def collect(self, since: datetime, cursor: dict) -> list[Event]:
        try:
            response = self.client.get(self.url)
        except FetchError as error:
            # Some BLS cache variants reject the unqualified XML URL. Keep the
            # request on the same official endpoint and ask explicitly for XML.
            if self.feed in {"bea", "census"} or str(error) != "http_403":
                raise
            parts = urlsplit(self.url)
            query = dict(parse_qsl(parts.query))
            query["format"] = "xml"
            response = self.client.get(urlunsplit(parts._replace(query=urlencode(query))))
        parsed = feedparser.parse(response.content)
        if not parsed.get("version") or (parsed.bozo and not parsed.entries):
            raise FetchError("invalid_economic_rss")
        events = []
        cursor["feed_entries"] = len(parsed.entries)
        for entry in parsed.entries:
            try:
                title = plain_text(entry.title)
                indicator = (
                    ("census_" + entry.get("id", ""))
                    if self.feed == "census" and entry.get("id") in CENSUS_INDICATORS
                    else indicator_for(self.feed, title)
                )
                if not indicator:
                    continue
                url = canonical_url(entry.link)
                published = parse_time(entry.get("published") or entry.get("updated"))
                if self.feed not in {"bea", "census"}:
                    # Feed publication can precede the 08:30 ET release embargo.
                    local = published.astimezone(ZoneInfo("America/New_York"))
                    embargo = datetime.combine(
                        local.date(),
                        time(10, 0) if self.feed == "jolts" else time(8, 30),
                        local.tzinfo,
                    )
                    published = max(published, embargo).astimezone(since.tzinfo)
                latest = cursor.get("latest_release_at")
                if not latest or published > parse_time(latest):
                    cursor["latest_release_at"] = published.isoformat()
                if published < since or published > utcnow():
                    continue
                event = Event(
                    "Census" if self.feed == "census" else ("BEA" if self.feed == "bea" else "BLS"),
                    entry.get("id", "") + ":" + published.date().isoformat()
                    if self.feed == "census"
                    else url,
                    title,
                    plain_text(entry.get("summary", "")),
                    url,
                    published,
                    metadata={
                        "official": True,
                        "verified_official": True,
                        "indicator": indicator,
                        "feed": self.feed,
                    },
                )
                if not trusted_release(event):
                    raise ValueError("Unexpected official release metadata")
                prepare_brief(event)
                events.append(event)
            except (ValueError, AttributeError, KeyError, TypeError, OverflowError):
                log.warning("%s: skipped invalid release metadata", self.name)
        log.info(
            "%s: feed entries=%d eligible_releases=%d", self.name, len(parsed.entries), len(events)
        )
        return events
