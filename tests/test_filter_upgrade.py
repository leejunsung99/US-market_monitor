import sqlite3
from datetime import timedelta

from us_market_monitor.database.models import utcnow
from us_market_monitor.database.repository import SCHEMA, Repository


def test_old_sqlite_schema_is_migrated_without_losing_baseline(tmp_path):
    path = tmp_path / "old.sqlite3"
    old_schema = SCHEMA.replace(", rules_version TEXT NOT NULL DEFAULT ''", "")
    connection = sqlite3.connect(path)
    connection.executescript(old_schema)
    connection.execute(
        "INSERT INTO collector_state(source,last_cursor) VALUES(?,?)",
        ("fed:monetary", '{"baseline_at":"2026-10-13T00:00:00+00:00"}'),
    )
    connection.execute("INSERT INTO seen VALUES('existing-hash','2026-10-13T00:00:00+00:00')")
    connection.commit()
    connection.close()
    upgraded = Repository(path)
    assert (
        upgraded.state("fed:monetary")["last_cursor"]
        == '{"baseline_at":"2026-10-13T00:00:00+00:00"}'
    )
    assert "event_stage" in {r[1] for r in upgraded.db.execute("PRAGMA table_info(events)")}
    assert upgraded.db.execute("SELECT observation_hash FROM seen").fetchone()[0] == "existing-hash"
    upgraded.close()


def test_review_limit_is_bounded(repo, classifier, event_factory):
    classifier.filters["review_max_rows"] = 2
    for index in range(5):
        event = event_factory(
            "Memory industry update",
            source_event_id=str(index),
            source_url=f"https://example.com/{index}",
            summary="CXMT plans to release new DDR5 memory.",
        )
        classifier.classify(event)
        repo.discard(event)
    assert len(repo.reviews()) == 2


def test_memory_families_not_collapsed_into_one_story(repo, classifier, filters, event_factory):
    initial = classifier.classify(
        event_factory(
            "Samsung Electronics reports progress on DRAM memory architecture",
            published_at=utcnow() - timedelta(minutes=10),
        )
    )
    other = classifier.classify(
        event_factory(
            "Samsung Electronics reports progress on NAND memory architecture",
            source_event_id="200",
            source_url="https://example.com/200",
        )
    )
    assert initial.entity_key != other.entity_key
    assert repo.ingest(initial, filters)
    assert repo.ingest(other, filters)
