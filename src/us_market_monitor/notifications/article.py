"""Use only publisher-provided article text and images. No generated facts or pictures."""

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from us_market_monitor.http import FetchError, HttpClient
from us_market_monitor.processors.normalizer import canonical_url, plain_text


def image_url(value):
    try:
        url = canonical_url(value)
    except (ValueError, AttributeError):
        return ""
    if re.search(
        r"logo|social-default-image|yahoo_finance_en|/rz/stage/|favicon|placeholder", url, re.I
    ):
        return ""
    # Yahoo's signed resize URLs reject format changes; use their embedded original.
    if urlsplit(url).hostname == "s.yimg.com" and "/mysterio/" in url:
        original = unquote(url.rsplit("/", 1)[-1])
        if urlsplit(original).hostname == "media.zenfs.com":
            return canonical_url(original)
    return url


def source_excerpt(text, title="", limit=800):
    text = plain_text(text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    selected = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
        and sentence.strip() != title.strip()
        and not re.search(
            r"share on|subscribe|read more|click here|all rights reserved|privacy policy",
            sentence,
            re.I,
        )
    ]
    result = " ".join(selected[:3]) or text or title
    if len(result) > limit:
        result = result[: limit - 1].rsplit(" ", 1)[0] + "…"
    return result


def enrich_article(event, *, client=None, stop=None):
    event.image_url = image_url(event.image_url)
    client = client or HttpClient(
        timeout=15, attempts=1, spacing=1, stop=stop, headers={"User-Agent": "USMarketRadar/0.2"}
    )
    try:
        response = client.get(event.source_url)
        page = BeautifulSoup(response.text, "html.parser")
        event.source_url = canonical_url(getattr(response, "url", event.source_url))
        for meta in page.select('meta[property="og:image"],meta[name="twitter:image"]'):
            candidate = image_url(urljoin(event.source_url, meta.get("content", "")))
            if candidate:
                event.image_url = candidate
                break
        if event.source == "Federal Reserve":
            article = page.select_one("#article")
            if article:
                paragraphs = [plain_text(str(p)) for p in article.select("p")]
                substantive = [
                    p
                    for p in paragraphs
                    if len(p) > 100
                    and not re.search(
                        r"For media inquiries|Last Update|For release at|Share|Attachments", p, re.I
                    )
                ]
                event.article_summary = source_excerpt(" ".join(substantive[:2]), event.title)
        if not event.article_summary or event.article_summary.strip() == event.title.strip():
            description = page.select_one(
                'meta[property="og:description"],meta[name="description"]'
            )
            if description:
                event.article_summary = source_excerpt(description.get("content", ""), event.title)
    except (FetchError, ValueError):
        # Source metadata failure must not suppress the news or fabricate an image.
        pass
    event.article_summary = source_excerpt(event.article_summary or event.summary, event.title)
    return event
