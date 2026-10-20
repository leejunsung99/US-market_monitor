from typing import Protocol

from us_market_monitor.database.models import Event


class Summarizer(Protocol):
    """Extension seam: implementations must preserve source attribution and facts."""

    def summarize(self, event: Event) -> tuple[str, str]: ...


class RuleSummarizer:
    def summarize(self, event: Event) -> tuple[str, str]:
        from us_market_monitor.processors.briefing import (
            display_summary,
            economic_title,
            translation_fingerprint,
        )

        if event.event_type == "economic_indicator":
            return economic_title(event), display_summary(event)

        title = event.title_ko if event.translation_hash == translation_fingerprint(event) else ""
        return title or event.title, display_summary(event)
