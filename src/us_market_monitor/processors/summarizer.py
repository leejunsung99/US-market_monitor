from typing import Protocol

from us_market_monitor.database.models import Event


class Summarizer(Protocol):
    """Extension seam: implementations must preserve source attribution and facts."""

    def summarize(self, event: Event) -> tuple[str, str]: ...


class RuleSummarizer:
    def summarize(self, event: Event) -> tuple[str, str]:
        from us_market_monitor.notifications.article import source_excerpt

        return event.title_ko or event.title, event.summary_ko or source_excerpt(
            event.article_summary or event.summary, event.title
        )
