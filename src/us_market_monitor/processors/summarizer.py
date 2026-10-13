from typing import Protocol

from us_market_monitor.database.models import Event


class Summarizer(Protocol):
    """Extension seam: implementations must preserve source attribution and facts."""

    def summarize(self, event: Event) -> tuple[str, str]: ...


class RuleSummarizer:
    def summarize(self, event: Event) -> tuple[str, str]:
        actor = event.actor or ", ".join(event.related_tickers)
        title = f"{actor + ' · ' if actor else ''}{event.reason}"
        # Korean category is rule-derived. Factual details remain a source quotation
        # rather than an unsupported machine translation.
        summary = "원문에서 아래 사건 관련 내용을 확인했습니다. 상세 수치·조건은 원문을 확인하세요."
        return title, summary
