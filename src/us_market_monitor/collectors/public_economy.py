"""Key-free DOL releases and EIA CSV download."""

import csv
import re
from datetime import datetime, time
from decimal import Decimal
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo

from pypdf import PdfReader

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.processors.briefing import prepare_brief


class PublicEconomyCollector:
    def __init__(self, client, kind, url, interval=600, initial_lookback_hours=24):
        self.client, self.kind, self.url = client, kind, url
        self.name = "economic:" + kind
        self.interval, self.initial_lookback_hours = interval, initial_lookback_hours

    def collect(self, since, cursor):
        response = self.client.get(self.url)
        if self.kind == "claims":
            if not response.content.startswith(b"%PDF"):
                raise FetchError("claims_not_a_pdf")
            try:
                body = " ".join(
                    (p.extract_text() or "") for p in PdfReader(BytesIO(response.content)).pages[:1]
                )
            except Exception:
                raise FetchError("claims_pdf_unreadable") from None
            body = re.sub(r"\s+", " ", body)
            date = re.search(
                r"(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+([A-Za-z]+ \d{1,2}, \d{4})", body
            )
            if not date or "UNEMPLOYMENT INSURANCE WEEKLY CLAIMS" not in body:
                raise FetchError("claims_release_layout_unrecognized")
            published = datetime.strptime(date[1], "%B %d, %Y").replace(
                hour=8, minute=30, tzinfo=ZoneInfo("America/New_York")
            )
            start = body.find("SEASONALLY ADJUSTED DATA")
            summary = body[start + len("SEASONALLY ADJUSTED DATA") :][:4000]
            title, source = "Unemployment Insurance Weekly Claims — " + date[1], "DOL"
            source_url = "https://www.dol.gov/ui/data.pdf"
        else:
            rows = list(csv.DictReader(StringIO(response.text)))
            if not rows or not {"Release_Date", "Date", "Sourcekey", "Value"}.issubset(rows[0]):
                raise FetchError("eia_csv_layout_unrecognized")
            release = max(row["Release_Date"] for row in rows)
            published = datetime.combine(
                datetime.strptime(release, "%Y-%m-%d").date(),
                time(10, 30),
                ZoneInfo("America/New_York"),
            )
            labels = {
                "WCESTUS1": "상업용 원유 재고(SPR 제외)",
                "WGTSTUS1": "휘발유 재고",
                "WDISTUS1": "정제유 재고",
            }
            lines = []
            for key, label in labels.items():
                series = sorted(
                    [
                        r
                        for r in rows
                        if r["Release_Date"] == release
                        and r["Sourcekey"] == key
                        and r["Value"]
                        and not r.get("Suppression_Flag")
                    ],
                    key=lambda r: r["Date"],
                    reverse=True,
                )
                if len(series) < 2:
                    continue
                value, previous = Decimal(series[0]["Value"]), Decimal(series[1]["Value"])
                lines.append(
                    f"{label}: {value:,.0f}천 배럴, 전주 대비 {value - previous:+,.0f}천 배럴 ({series[0]['Date']} 기준)."
                )
            if not lines:
                raise FetchError("eia_inventory_series_missing")
            title, source = "Weekly Petroleum Status Report — " + release, "EIA"
            summary, source_url = "\n".join(lines), "https://www.eia.gov/petroleum/supply/weekly/"
        cursor["latest_release_at"] = published.isoformat()
        if published < since or published > utcnow():
            return []
        event = Event(
            source,
            self.kind + ":" + published.date().isoformat(),
            title,
            summary,
            source_url,
            published,
            metadata={
                "official": True,
                "verified_official": True,
                "indicator": self.kind,
                "original_download_url": self.url,
            },
        )
        if self.kind == "claims":
            event.metadata["indicator"] = "claims"
        else:
            event.metadata["indicator"] = "oil_inventories"
        prepare_brief(event)
        return [event]
