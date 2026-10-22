from types import SimpleNamespace

import requests

from us_market_monitor.notifications.article import enrich_article, image_url
from us_market_monitor.notifications.formatter import format_rich_article
from us_market_monitor.notifications.telegram import Telegram


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
