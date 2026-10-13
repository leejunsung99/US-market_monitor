import logging
import re
import threading
import time
from datetime import datetime
from urllib.parse import quote

from bs4 import BeautifulSoup

from us_market_monitor.database.models import Event
from us_market_monitor.http import FetchError
from us_market_monitor.processors.normalizer import parse_time, plain_text

log = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
# Item numbers indicate candidates only. Body-based classification is still required.
IMPORTANT_ITEMS = {"1.01", "1.02", "2.01", "2.02", "2.05", "2.06", "8.01"}


class SecMapping:
    def __init__(self, client):
        self.client = client
        self._mapping = {}
        self._lock = threading.Lock()
        self._failure_until = 0.0

    def cik(self, ticker):
        with self._lock:
            if time.monotonic() < self._failure_until:
                raise FetchError("sec_mapping_temporarily_unavailable", 120)
            if not self._mapping:
                try:
                    raw = self.client.json(TICKERS_URL)
                    self._mapping = {
                        str(row["ticker"]).upper(): int(row["cik_str"]) for row in raw.values()
                    }
                except (FetchError, AttributeError, KeyError, ValueError, TypeError):
                    self._failure_until = time.monotonic() + 120
                    raise FetchError("sec_mapping_unavailable", 120) from None
            # GOOGL and GOOG generally share a CIK; allow the official class alias.
            key = "GOOG" if ticker == "GOOGL" and ticker not in self._mapping else ticker
            if key not in self._mapping:
                raise FetchError("ticker_not_in_sec_mapping")
            return self._mapping[key]


class SecCollector:
    def __init__(self, client, mapping, ticker, interval=120):
        self.client, self.mapping, self.ticker, self.interval = client, mapping, ticker, interval
        self.name = f"sec:{ticker}"

    def collect(self, since: datetime, cursor: dict) -> list[Event]:
        cik = self.mapping.cik(self.ticker)
        payload = self.client.json(SUBMISSIONS.format(cik=cik))
        try:
            recent = payload["filings"]["recent"]
            forms = recent["form"]
            if not isinstance(forms, list):
                raise TypeError("Invalid forms array")
            for field in ("acceptanceDateTime", "accessionNumber", "primaryDocument"):
                if not isinstance(recent.get(field), list) or len(recent[field]) != len(forms):
                    raise TypeError("Missing or mismatched submission arrays")
        except (KeyError, TypeError):
            raise FetchError("invalid_sec_submissions") from None
        events = []
        for i, form in enumerate(forms):
            if form not in {"8-K", "8-K/A", "6-K", "6-K/A"}:
                continue
            try:
                published = parse_time(
                    recent["acceptanceDateTime"][i], naive_zone="America/New_York"
                )
                if published < since:
                    continue
                items = set(re.findall(r"\d\.\d{2}", recent.get("items", [""] * len(forms))[i]))
                if form.startswith("8-K") and not items.intersection(IMPORTANT_ITEMS):
                    continue
                accession, document = recent["accessionNumber"][i], recent["primaryDocument"][i]
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                    raise ValueError("Invalid accession")
                if not document or "/" in document or "\\" in document:
                    raise ValueError("Invalid document")
                url = (
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{accession.replace('-', '')}/{quote(document)}"
                )
                filing_html = self.client.get(url).text
                body = plain_text(filing_html)
                # Foreign issuer 6-K and many 8-K primary documents are cover pages.
                # Follow up to two explicitly attached 99.x HTML exhibits, within
                # this accession directory only; no general web crawling.
                soup = BeautifulSoup(filing_html, "html.parser")
                exhibits = []
                for anchor in soup.find_all("a", href=True):
                    href, label = anchor["href"], anchor.get_text(" ", strip=True)
                    if not re.search(
                        r"99[._-]?[12]|press release|earnings release", label + " " + href, re.I
                    ):
                        continue
                    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", href, re.I) or href == document:
                        continue
                    if href not in exhibits:
                        exhibits.append(href)
                for exhibit in exhibits[:2]:
                    exhibit_url = url.rsplit("/", 1)[0] + "/" + quote(exhibit)
                    body += " " + plain_text(self.client.get(exhibit_url).text)
                # Do not declare all 2.02 filings an earnings surprise.
                # The classifier uses the source body's current-event language.
                events.append(
                    Event(
                        "SEC EDGAR",
                        accession,
                        f"{self.ticker} {form} 공시",
                        body[:1000],
                        url,
                        published,
                        body=body[:150000],
                        related_tickers=(self.ticker,),
                        metadata={
                            "form": form,
                            "items": sorted(items),
                            "official": True,
                            "issuer": payload.get("name", self.ticker),
                        },
                    )
                )
            except (KeyError, IndexError, TypeError, ValueError, OverflowError):
                log.warning("%s: skipped malformed filing metadata", self.name)
        return events
