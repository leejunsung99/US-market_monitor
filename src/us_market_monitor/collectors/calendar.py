"""Read exact scheduled times from the Census official calendar."""

from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError

CALENDAR_URL = "https://www.census.gov/economic-indicators/calendar-listview.html"


class CensusCalendarCollector:
    name = "calendar:census"
    is_calendar = True
    interval = 21600

    def __init__(self, client):
        self.client = client

    def collect(self, since, cursor):
        soup = BeautifulSoup(self.client.get(CALENDAR_URL).text, "html.parser")
        current_year = str(utcnow().astimezone(ZoneInfo("America/New_York")).year)
        year_link = next(
            (
                a
                for a in soup.select("a[href]")
                if current_year + " Economic Indicator Calendar" in a.get_text(" ", strip=True)
            ),
            None,
        )
        calendar_url = urljoin(CALENDAR_URL, year_link["href"]) if year_link else CALENDAR_URL
        if calendar_url != CALENDAR_URL:
            soup = BeautifulSoup(self.client.get(calendar_url).text, "html.parser")
        dates = soup.select("td[sorttable_customkey]")
        if not dates:
            raise FetchError("census_calendar_layout_unrecognized")
        schedule = []
        for cell in dates:
            raw = cell.get("sorttable_customkey", "")
            if len(raw) != 12 or not raw.isdigit():
                continue
            when = datetime.strptime(raw, "%Y%m%d%H%M").replace(tzinfo=ZoneInfo("America/New_York"))
            if not utcnow() - timedelta(days=1) <= when <= utcnow() + timedelta(days=90):
                continue
            title = cell.find_parent("tr").find("td").get_text(" ", strip=True)
            aliases = {
                "Advance Monthly Sales for Retail": "retail_sales",
                "Advance Report on Durable": "durable_goods",
                "Advance Monthly Manufacturers": "durable_goods",
                "New Residential Construction": "housing_starts",
                "New Residential Sales": "home_sales",
                "New Home Sales": "home_sales",
                "Business Formation": "bfs",
                "Monthly Wholesale": "wholesale",
                "Construction Spending": "construction_value",
            }
            indicator = next((value for key, value in aliases.items() if key in title), None)
            if indicator:
                schedule.append(
                    {
                        "indicator": "census_" + indicator,
                        "title": title,
                        "scheduled_at": when.isoformat(),
                        "source_url": calendar_url,
                    }
                )
        cursor["expected_releases"] = len(schedule)
        cursor["calendar_url"] = calendar_url
        return schedule
