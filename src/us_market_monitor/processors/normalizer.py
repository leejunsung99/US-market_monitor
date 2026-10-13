import hashlib
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup


def plain_text(value: str) -> str:
    soup = BeautifulSoup(value or "", "html.parser")
    for element in soup(["script", "style", "noscript", "ix:header"]):
        element.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()


def parse_time(value, *, naive_zone: str | None = None) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            result = parsedate_to_datetime(str(value))
    if result.tzinfo is None:
        if not naive_zone:
            raise ValueError("Publication timezone is missing")
        result = result.replace(tzinfo=ZoneInfo(naive_zone))
    return result.astimezone(UTC)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username:
        raise ValueError("Invalid source URL")
    if len(url) > 1500:
        raise ValueError("Source URL is too long")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
    ]
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(sorted(query)),
            "",
        )
    )


def normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[\w]+", title.casefold()))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
