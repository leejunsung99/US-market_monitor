from datetime import timedelta

from us_market_monitor.database.models import utcnow


def test_same_article_material_revision_and_duplicate_revision(
    repo, classifier, filters, event_factory
):
    first = classifier.classify(
        event_factory(
            "NVIDIA raises revenue outlook to $10 billion",
            published_at=utcnow() - timedelta(minutes=20),
        )
    )
    event_id = repo.ingest(first, filters)
    revised = classifier.classify(
        event_factory(
            "NVIDIA revises revenue outlook to $15 billion",
            published_at=first.published_at + timedelta(minutes=10),
        )
    )
    revision_id = repo.ingest(revised, filters)
    assert revision_id and revision_id != event_id
    repeated = classifier.classify(
        event_factory(
            "NVIDIA revises revenue outlook to $15 billion",
            published_at=first.published_at + timedelta(minutes=10),
        )
    )
    assert repo.ingest(repeated, filters) is None
    assert repo.status()["important_events"] == 2


def test_observation_hash_retains_original_before_classifier_mutation(
    repo, classifier, event_factory
):
    raw = event_factory(summary="The company has issued results. NVIDIA reports quarterly results.")
    assert not repo.seen(raw)
    key = raw.observation_hash
    classifier.classify(raw)
    assert raw.summary != "The company has issued results. NVIDIA reports quarterly results."
    repo.discard(raw)
    fresh = event_factory(
        summary="The company has issued results. NVIDIA reports quarterly results.",
        published_at=raw.published_at,
    )
    assert repo.seen(fresh)
    assert key == fresh.observation_hash
