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
    assert blocks[2]["text"] == "Publisher facts. Another fact."
    assert blocks[3]["text"][1]["url"] == event.source_url
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return SimpleNamespace(json=lambda: {"ok": True, "result": {"message_id": 12}})

    monkeypatch.setattr(requests, "post", post)
    assert Telegram("test", "chat").send_article(event) == 12
    assert calls[0][0].endswith("/sendRichMessage")
    assert calls[0][1]["rich_message"]["blocks"] == blocks


def test_publisher_photo_and_logo_exclusion(event_factory):
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
