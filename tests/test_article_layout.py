from types import SimpleNamespace

import requests

from us_market_monitor.notifications.article import article_text, enrich_article, image_url
from us_market_monitor.notifications.formatter import format_rich_article
from us_market_monitor.notifications.telegram import Telegram


def test_prnewswire_body_excludes_site_controls_and_company_bio():
    from bs4 import BeautifulSoup

    page = BeautifulSoup('<main>View all news Download<div class="xn-content">'
                         '<p>Qualcomm announced a call on November 4, 2026.</p>'
                         '<p><b>About Qualcomm</b></p><p>Qualcomm is an AI leader.</p>'
                         '</div></main>', 'html.parser')
    assert article_text(page) == 'Qualcomm announced a call on November 4, 2026.'


def test_unknown_publisher_markup_still_yields_the_body_not_the_teaser():
    """A renamed article wrapper used to leave body empty, cutting alerts to one line."""
    from bs4 import BeautifulSoup

    lede = 'Oil prices fell sharply as investors braced for a packed week of jobs reports. ' * 2
    detail = 'The Nasdaq Composite gave up earlier gains as traders weighed the midterm outlook. ' * 2
    page = BeautifulSoup(
        '<body><nav><p>' + 'Markets Pro subscribe now for full access to our coverage. ' * 2
        + '</p></nav><div class="wrapper"><div class="group"><p>' + lede + '</p></div>'
        '<div class="group"><p>' + detail + '</p></div></div>'
        '<footer><p>' + 'Sign up for our newsletter and get alerts delivered daily. ' * 2
        + '</p></footer></body>', 'html.parser')
    text = article_text(page)
    assert lede.strip() in text
    assert detail.strip() in text
    # Chrome holds no long prose, so it must lose the density comparison.
    assert 'subscribe now' not in text
    assert 'newsletter' not in text


def test_promo_heading_before_the_body_does_not_discard_the_article():
    """fool.com puts a promo block above the body; .article-body keeps it exact."""
    from bs4 import BeautifulSoup

    body = 'Anthropic is about to go public in one of the biggest offerings of the year. ' * 2
    page = BeautifulSoup(
        '<div class="contents"><h2>Should You Invest $1,000 in Anthropic Right Now?</h2>'
        '<p>' + 'Our analyst team has an opinion you may want to hear first. ' * 2 + '</p>'
        '<div class="article-body"><p>' + body + '</p></div></div>', 'html.parser')
    text = article_text(page)
    assert body.strip() in text
    assert 'analyst team' not in text


def test_rich_article_order_and_source_only(event_factory, monkeypatch):
    event = event_factory("Chip news", summary="Publisher facts. Another fact.")
    event.article_summary = event.summary
    event.image_url = "https://example.com/article.jpg"
    blocks = format_rich_article(event)["blocks"]
    assert [b["type"] for b in blocks] == ["heading", "photo", "paragraph", "footer"]
    assert "• 핵심 발표: Publisher facts." in blocks[2]["text"]
    assert "\n• 주요 내용: Another fact." in blocks[2]["text"]
    assert blocks[3]["text"][1]["url"] == event.source_url
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return SimpleNamespace(json=lambda: {"ok": True, "result": {"message_id": 12}})

    monkeypatch.setattr(requests, "post", post)
    assert Telegram("test", "chat").send_article(event) == 12
    assert calls[0][0].endswith("/sendPhoto")
    assert calls[0][1]["photo"] == event.image_url
    assert "<b><u>🇺🇸" in calls[0][1]["caption"]
    assert "📍핵심내용" in calls[0][1]["caption"]
    assert "https://t.me/growth_us" in calls[0][1]["caption"]


def test_publisher_photo_and_logo_exclusion(event_factory):
    assert (
        image_url(
            "https://s.yimg.com/lo/mysterio/api/key/resize/https%3A%2F%2Fmedia.zenfs.com%2Fen%2Fphoto.jpg"
        )
        == "https://media.zenfs.com/en/photo.jpg"
    )
    assert image_url("https://example.com/social-default-image-opengraph.jpg") == ""
    assert image_url("javascript:alert(1)") == ""
    event = event_factory("Chip news", summary="Actual publisher summary.")
    client = SimpleNamespace(
        get=lambda _: SimpleNamespace(
            url="https://example.com/story",
            text='<meta property="og:image" content="/photo.jpg">',
        )
    )
    enrich_article(event, client=client)
    assert event.image_url == "https://example.com/photo.jpg"
    assert event.article_summary == "Actual publisher summary."
    event.image_url = ""
    assert [b["type"] for b in format_rich_article(event)["blocks"]] == [
        "heading",
        "paragraph",
        "footer",
    ]
