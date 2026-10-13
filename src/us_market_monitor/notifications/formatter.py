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
    stages = {
        "technical_progress": "기술 진전 발표 — 제품 출시·양산 완료와 구분",
        "announced_plan": "출시 계획 발표 — 실제 출시·양산 미확인",
        "launched": "제품 출시 보도 — 양산 규모·고객 인증은 별도 확인",
        "mass_production": "양산 시작 발표 — 성능·수율은 원문에서 별도 확인",
    }
    details = ""
    if event.event_stage:
        details += f"사건 단계: {safe(stages.get(event.event_stage, event.event_stage), 150)}\n"
        level = (
            "공식 출처"
            if event.evidence_level == "official_source"
            else "회사 발표를 인용한 기사 — 공식 원문 독립 검증 아님"
        )
        details += f"확인 근거: {safe(level, 150)}\n"
    if event.impact_tickers:
        details += (
            f"경쟁관계 기반 관찰 대상: {safe(', '.join(event.impact_tickers), 150)} (설정 기준)\n"
        )
    return (
        f"{emoji} [{label} / {safe(event.industry, 100)}]\n\n"
        f"<b>{safe(title, 180)}</b>\n\n"
        f"핵심 내용:\n{safe(summary, 350)}\n"
        f"원문 제목: {safe(event.title, 350)}\n"
        f"원문 발췌: {safe(event.summary, 650)}\n\n"
        f"관련 산업: {safe(event.industry, 100)}\n"
        f"관련 기업: {safe(', '.join(event.related_tickers) or '원문에서 확인된 종목 없음', 180)}\n\n"
        f"{details}\n"
        "시장 영향: 해당 사건이 관련 시장·사업 전망에 영향을 줄 가능성이 있습니다. "
        "영향의 방향과 크기는 확인되지 않았습니다.\n\n"
        f"출처: {safe(event.source, 120)}\n"
        f"발표 시각: {published}\n수집 시각: {detected}\n"
        f'<a href="{escape(event.source_url, quote=True)}">원문 보기</a>'
    )
