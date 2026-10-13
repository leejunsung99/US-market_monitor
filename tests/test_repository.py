from datetime import timedelta

from us_market_monitor.database.models import utcnow
from us_market_monitor.database.repository import Repository
from us_market_monitor.notifications.telegram import DeliveryError


def test_only_important_events_persist(repo, classifier, filters, event_factory):
    ordinary = event_factory("NVIDIA price target updated")
    assert classifier.classify(ordinary) is None
    repo.discard(ordinary)
    important = classifier.classify(event_factory())
    assert repo.ingest(important, filters)
    assert repo.status()["important_events"] == 1
    assert repo.db.execute("SELECT count(*) FROM seen").fetchone()[0] == 2
    columns = [r[1] for r in repo.db.execute("PRAGMA table_info(seen)")]
    assert columns == ["observation_hash", "observed_at", "rules_version"]
    assert "body" not in [r[1] for r in repo.db.execute("PRAGMA table_info(events)")]


def test_duplicate_across_publishers_and_restart(tmp_path, classifier, filters, event_factory):
    path = tmp_path / "persistent.sqlite3"
    repo = Repository(path)
    first = classifier.classify(event_factory())
    event_id = repo.ingest(first, filters)
    repo.claim(event_id)
    repo.sent(event_id, 42)
    repo.close()
    repo = Repository(path)
    second = classifier.classify(
        event_factory(
            "NVIDIA announces quarterly results",
            source="Finnhub/Another Publisher",
            source_event_id="200",
            source_url="https://other.com/a",
        )
    )
    assert repo.ingest(second, filters) is None
    assert repo.pending() == []
    assert repo.status()["important_events"] == 1
    repo.close()


def test_same_url_tracking_parameters_dedup(repo, classifier, filters, event_factory):
    first = classifier.classify(event_factory(source_url="https://example.com/a?utm_source=x"))
    second = classifier.classify(
        event_factory(source_url="https://example.com/a?utm_source=y", source_event_id="200")
    )
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters) is None


def test_material_guidance_revision_is_allowed(repo, classifier, filters, event_factory):
    first = classifier.classify(
        event_factory(
            "NVIDIA raises revenue outlook to $10 billion",
            published_at=utcnow() - timedelta(minutes=20),
        )
    )
    second = classifier.classify(
        event_factory(
            "NVIDIA revises revenue outlook to $15 billion",
            source_event_id="200",
            source_url="https://example.com/200",
            published_at=first.published_at + timedelta(minutes=10),
        )
    )
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters)
    assert repo.status()["important_events"] == 2


def test_followup_without_new_facts_is_suppressed(repo, classifier, filters, event_factory):
    first = classifier.classify(event_factory("NVIDIA raises revenue outlook to $10 billion"))
    second = classifier.classify(
        event_factory(
            "NVIDIA boosts revenue outlook to $10 billion",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert repo.ingest(first, filters)
    assert repo.ingest(second, filters) is None


def test_claim_is_atomic_and_crash_recovery_unknown(repo, classifier, filters, event_factory):
    event_id = repo.ingest(classifier.classify(event_factory()), filters)
    assert repo.claim(event_id)
    assert not repo.claim(event_id)
    repo.recover_inflight()
    assert repo.status()["notifications"] == {"unknown": 1}
    assert repo.pending() == []


def test_retry_schedule_and_unknown_no_auto_retry(repo, classifier, filters, event_factory):
    event_id = repo.ingest(classifier.classify(event_factory()), filters)
    repo.claim(event_id)
    repo.delivery_failure(
        event_id, DeliveryError("rate_limited", retryable=True, retry_after=60), 0
    )
    row = repo.db.execute("SELECT * FROM notifications").fetchone()
    assert row["status"] == "pending"
    assert row["retry_count"] == 1
    assert repo.pending() == []
    repo.delivery_failure(event_id, DeliveryError("unknown", ambiguous=True), 1)
    assert repo.status()["notifications"] == {"unknown": 1}


def test_old_pending_notifications_expire(repo, classifier, filters, event_factory):
    old = classifier.classify(event_factory(published_at=utcnow() - timedelta(days=2)))
    repo.ingest(old, filters)
    assert repo.pending(max_age_hours=24) == []
    assert repo.status()["notifications"] == {"expired": 1}


def test_wal_enabled(repo):
    assert repo.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
