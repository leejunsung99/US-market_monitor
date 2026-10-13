from datetime import timedelta

import pytest

from us_market_monitor.database.models import utcnow
from us_market_monitor.notifications.formatter import format_event


@pytest.mark.parametrize(
    "title,summary,stage",
    [
        (
            "Micron stock drops on China chip news",
            "CXMT reports progress on a denser DRAM memory chip design.",
            "technical_progress",
        ),
        (
            "China memory update",
            "CXMT announces plans to launch a new DDR5 RDIMM by year-end.",
            "announced_plan",
        ),
        (
            "China memory update",
            "CXMT says it plans to release server DDR5 memory in December.",
            "announced_plan",
        ),
        ("CXMT launches a new DDR5 memory chip", "", "launched"),
        ("CXMT begins mass production of new DRAM chips", "", "mass_production"),
    ],
)
def test_material_competitor_events(classifier, event_factory, title, summary, stage):
    event = classifier.classify(event_factory(title, summary=summary))
    assert event is not None
    assert event.event_type == "semiconductor_technology"
    assert event.priority == "HIGH"
    assert event.event_stage == stage
    assert event.actor == "CXMT"
    assert event.impact_tickers == ("MU",)
    assert event.importance_score >= 4


@pytest.mark.parametrize(
    "summary,reason",
    [
        ("CXMT plans to release a new DDR5 RDIMM by year-end.", "announcement_not_attributed"),
        (
            "CXMT reportedly announces progress on new DRAM architecture.",
            "uncertain_industry_claim",
        ),
        ("UnknownChip announces progress on a new DRAM architecture.", "unknown_industry_actor"),
    ],
)
def test_uncertain_industry_news_is_review_only(classifier, event_factory, summary, reason):
    event = event_factory("Memory industry update", summary=summary)
    assert classifier.classify(event) is None
    assert event.metadata["classification_decision"]["status"] == "review"
    assert event.metadata["classification_decision"]["reason"] == reason


def test_relationship_not_claimed_as_source_mention(classifier, event_factory):
    event = classifier.classify(event_factory("CXMT reports progress on denser DRAM architecture"))
    assert event.related_tickers == ()
    assert event.impact_tickers == ("MU",)
    assert "MU" not in format_event(event)


def test_plan_not_labeled_as_launched_or_mass_production(classifier, event_factory):
    event = classifier.classify(event_factory("CXMT confirms plans to launch new DDR5 memory"))
    assert event.event_stage == "announced_plan"
    assert "plans to launch" in format_event(event)


def test_ordinary_price_drop_and_ads_still_rejected(classifier, event_factory):
    for title, summary in [
        ("Why Micron stock dropped today", "Investors are nervous about chip competition."),
        ("Micron stock picks", "CXMT reports progress on denser DRAM architecture."),
        ("Memory overview", "CXMT reports progress on denser DRAM architecture. Buy the dip."),
    ]:
        assert classifier.classify(event_factory(title, summary=summary)) is None


def test_cannot_borrow_actor_from_other_sentence(classifier, event_factory):
    event = event_factory(
        "Micron chip news",
        summary="CXMT is a competitor. An analyst reports progress on DRAM architecture.",
    )
    assert classifier.classify(event) is None


def test_review_storage_ttl_and_no_event_or_notifications(repo, classifier, event_factory):
    event = event_factory(
        "Memory industry update", summary="CXMT plans to release new DDR5 memory."
    )
    assert classifier.classify(event) is None
    repo.discard(event)
    assert len(repo.reviews()) == 1
    assert repo.status()["important_events"] == 0
    assert repo.status()["notifications"] == {}
    repo.db.execute(
        "UPDATE review_candidates SET expires_at=?",
        ((utcnow() - timedelta(seconds=1)).isoformat(),),
    )
    repo.db.commit()
    assert repo.reviews() == []
    repo.prune_seen()
    assert repo.db.execute("SELECT count(*) FROM review_candidates").fetchone()[0] == 0


def test_rejection_counts_never_store_ordinary_titles(repo, classifier, event_factory):
    event = event_factory("Micron price target raised")
    classifier.classify(event)
    repo.discard(event)
    repo.discard(event)
    assert sum(row["count"] for row in repo.filter_report()) == 1
    assert repo.reviews() == []
    assert repo.status()["important_events"] == 0


def test_rules_change_rechecks_hash_but_cannot_resend_sent_event(
    repo, classifier, filters, event_factory
):
    raw = event_factory("NVIDIA reports quarterly results")
    classifier.prepare(raw)
    classifier.classify(raw)
    event_id = repo.ingest(raw, filters)
    repo.claim(event_id)
    repo.sent(event_id, 42)
    from us_market_monitor.processors.classifier import Classifier

    updated_filters = dict(filters, rules_version="changed")
    updated = Classifier({"companies": classifier.companies}, updated_filters)
    fresh = event_factory("NVIDIA reports quarterly results", published_at=raw.published_at)
    updated.prepare(fresh)
    assert not repo.seen(fresh, updated.version)
    approved = updated.classify(fresh)
    assert repo.ingest(approved, updated_filters) is None
    assert repo.status()["notifications"] == {"sent": 1}


def test_industry_stage_upgrade_allowed_paraphrase_suppressed(
    repo, classifier, filters, event_factory
):
    initial = classifier.classify(
        event_factory(
            "CXMT reports progress on denser DRAM architecture",
            published_at=utcnow() - timedelta(minutes=20),
        )
    )
    assert repo.ingest(initial, filters)
    followup = classifier.classify(
        event_factory(
            "CXMT announces progress on new memory chip design",
            source_event_id="2",
            source_url="https://example.com/2",
            published_at=initial.published_at + timedelta(minutes=1),
        )
    )
    assert repo.ingest(followup, filters) is None
    launched = classifier.classify(
        event_factory(
            "CXMT launches a new DDR5 memory chip",
            source_event_id="3",
            source_url="https://example.com/3",
            published_at=initial.published_at + timedelta(minutes=2),
        )
    )
    assert repo.ingest(launched, filters)
    assert repo.status()["important_events"] == 2


def test_industry_details_survive_database_roundtrip(repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory("CXMT confirms plans to launch new DDR5 memory"))
    event_id = repo.ingest(event, filters)
    stored = repo.row_event(
        repo.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    )
    assert stored.event_stage == event.event_stage
    assert stored.impact_tickers == ("MU",)
    assert stored.importance_score == event.importance_score
