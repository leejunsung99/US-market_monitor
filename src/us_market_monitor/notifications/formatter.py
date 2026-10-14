from html import escape
from zoneinfo import ZoneInfo

from us_market_monitor.notifications.article import image_url
from us_market_monitor.processors.summarizer import RuleSummarizer

KST = ZoneInfo("Asia/Seoul")


def format_event(event, summarizer=None):
    title, summary = (summarizer or RuleSummarizer()).summarize(event)
    published = event.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    return (
        f"<b>{escape(title[:350])}</b>\n\n{escape(summary[:800])}\n\n"
        f'{published}\n\n📌원문 보러가기\n'
        f'(<a href="{escape(event.source_url, quote=True)}">{escape(event.source_url)}</a>)'
    )


def format_rich_article(event, *, test=False):
    title, summary = RuleSummarizer().summarize(event)
    blocks = [{"type": "heading", "size": 3, "text": ("[테스트] " if test else "") + title[:350]}]
    photo = image_url(event.image_url)
    if photo:
        blocks.append({"type": "photo", "photo": {"type": "photo", "media": photo}})
    blocks.append({"type": "paragraph", "text": summary[:800]})
    published = event.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    blocks.append(
        {
            "type": "footer",
            "text": [
                published + "\n\n📌원문 보러가기\n(",
                {"type": "url", "text": event.source_url, "url": event.source_url},
                ")",
            ],
        }
    )
    return {"blocks": blocks}
