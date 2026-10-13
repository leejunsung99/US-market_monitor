from dataclasses import dataclass, field
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Event:
    source: str
    source_event_id: str
    title: str
    summary: str
    source_url: str
    published_at: datetime
    detected_at: datetime = field(default_factory=utcnow)
    body: str = ""
    related_tickers: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)
    event_type: str = ""
    priority: str = ""
    entity_key: str = ""
    industry: str = ""
    reason: str = ""
    observation_hash: str = ""
    event_stage: str = ""
    evidence_level: str = ""
    actor: str = ""
    impact_tickers: tuple[str, ...] = ()
    importance_score: float = 0
    score_breakdown: dict = field(default_factory=dict)
    article_summary: str = ""
    image_url: str = ""

    def __post_init__(self):
        for name in ("published_at", "detected_at"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
            setattr(self, name, value.astimezone(UTC))
