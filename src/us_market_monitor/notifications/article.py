"""Use only publisher-provided article text and images. No generated facts or pictures."""

import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from us_market_monitor.http import FetchError, HttpClient
from us_market_monitor.processors.briefing import prepare_brief
from us_market_monitor.processors.normalizer import canonical_url, plain_text


def article_text(page):
    """Read article containers only; never summarize navigation or page chrome."""
    article = page.select_one('[itemprop="articleBody"], .caas-body, article, #article')
    if article:
        for node in article.select("script, style, nav, aside, footer, form"):
            node.decompose()
        paragraphs = []
        for node in article.select('p, li, h2, h3'):
            text = plain_text(str(node))
            if node.name in {'h2', 'h3'}:
                if re.match(r'About\s|Should You Invest|Featured Articles|Related (?:Stories|Articles|Videos)',
                            text, re.I):
                    break
                continue
            if node.find_parent(['nav', 'aside', 'footer']):
                continue
            if node.name == 'p' and node.find_parent('li'):
                continue  # The enclosing list item already supplies this paragraph.
            paragraphs.append(text)
        return '\n'.join(paragraphs)[:100000]
    return ""


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


def enrich_article(event, *, client=None, stop=None):
    event.image_url = image_url(event.image_url)
    if event.event_type == "monitor_alert":
        return event
    if event.source in {
        "SEC EDGAR",
        "BLS",
        "BEA",
        "Census",
        "DOL",
        "EIA",
        "BIS",
    } or event.source.startswith("Official/"):
        # The SEC collector already reads the filing and permitted exhibits using
        # its identifying User-Agent. Avoid a second, unidentified cover-page fetch.
        prepare_brief(event)
        return event
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
        if event.source != "SEC EDGAR":
            text = article_text(page)
            if text:
                # Complete article paragraphs take priority over clipped API teasers.
                if not prepare_brief(event, text):
                    event.article_summary = event.summary
        if not event.article_summary or event.article_summary.strip() == event.title.strip():
            description = page.select_one(
                'meta[property="og:description"],meta[name="description"]'
            )
            if description:
                event.article_summary = description.get("content", "")
    except (FetchError, ValueError):
        # Source metadata failure must not suppress the news or fabricate an image.
        pass
    prepare_brief(event)
    return event
