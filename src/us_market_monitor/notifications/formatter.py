from html import escape
from zoneinfo import ZoneInfo

from us_market_monitor.processors.summarizer import RuleSummarizer

KST = ZoneInfo("Asia/Seoul")


def format_event(event, summarizer=None):
    title, summary = (summarizer or RuleSummarizer()).summarize(event)
    emoji, label = ("🚨", "긴급") if event.priority == "CRITICAL" else ("🔔", "중요")

    def safe(value, limit):
        return escape(str(value)[:limit], quote=True)

    published = event.published_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    detected = event.detected_at.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")
    return (
        f"{emoji} [{label} / {safe(event.industry, 100)}]\n\n"
        f"<b>{safe(title, 180)}</b>\n\n"
        f"핵심 내용:\n{safe(summary, 350)}\n"
        f"원문 제목: {safe(event.title, 350)}\n"
        f"원문 발췌: {safe(event.summary, 650)}\n\n"
        f"관련 산업: {safe(event.industry, 100)}\n"
        f"관련 기업: {safe(', '.join(event.related_tickers) or '원문에서 확인된 종목 없음', 180)}\n\n"
        "시장 영향: 해당 사건이 관련 시장·사업 전망에 영향을 줄 가능성이 있습니다. "
        "영향의 방향과 크기는 확인되지 않았습니다.\n\n"
        f"출처: {safe(event.source, 120)}\n"
        f"발표 시각: {published}\n수집 시각: {detected}\n"
        f'<a href="{escape(event.source_url, quote=True)}">원문 보기</a>'
    )
