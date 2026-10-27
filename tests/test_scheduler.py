import json
import threading
from datetime import timedelta
from types import SimpleNamespace

from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError
from us_market_monitor.notifications.telegram import DeliveryError
from us_market_monitor.scheduler.jobs import Radar


def test_local_model_work_does_not_block_scheduler(repo, classifier, filters, event_factory, monkeypatch):
    started, release = threading.Event(), threading.Event()
    sent = []
    repo.ingest(classifier.classify(event_factory()), filters)
    monkeypatch.setattr('us_market_monitor.scheduler.jobs.enrich_article', lambda event, **kw: event)

    def translate(event):
        started.set()
        release.wait(3)
        return True

    radar = Radar([], repo, classifier, translator=SimpleNamespace(background=True, translate=translate),
                  telegram=SimpleNamespace(send_article=lambda event: sent.append(event) or 42))
    try:
        radar.dispatch()
        assert started.wait(1)
        assert repo.db.execute('SELECT status FROM notifications').fetchone()[0] == 'pending'
        radar.dispatch()
        assert sent == []
        release.set()
        radar.summary_work[1].result(timeout=2)
        radar.dispatch()
        assert len(sent) == 1
        assert repo.db.execute('SELECT status FROM notifications').fetchone()[0] == 'sent'
    finally:
        release.set()
        radar.summary_executor.shutdown(wait=True)


def test_translation_failure_holds_delivery_for_retry(repo, classifier, filters, event_factory, monkeypatch):
    sent = []
    repo.ingest(classifier.classify(event_factory()), filters)
    monkeypatch.setattr('us_market_monitor.scheduler.jobs.enrich_article', lambda event, **kw: event)
    radar = Radar([], repo, classifier,
                  translator=SimpleNamespace(translate=lambda event: False),
                  telegram=SimpleNamespace(send_article=lambda event: sent.append(event)))
    radar.dispatch()
    row = repo.db.execute('SELECT * FROM notifications').fetchone()
    assert sent == []
    assert row['status'] == 'pending'
    assert row['error_message'] == 'translation_pending'
    assert row['retry_count'] == 0
    assert row['preparation_attempts'] == 1
    assert radar.notification_due == 0


def test_unstructured_economic_english_is_held(repo, classifier, filters, event_factory, monkeypatch):
    event = classifier.classify(event_factory())
    event.event_type = 'economic_indicator'
    repo.ingest(event, filters)
    sent = []
    monkeypatch.setattr('us_market_monitor.scheduler.jobs.enrich_article', lambda event, **kw: event)
    monkeypatch.setattr('us_market_monitor.scheduler.jobs.economic_digest',
                        lambda event: 'English source.\n※ 공식 발표 원문 발췌 · 예상치 비교 없음')
    radar = Radar([], repo, classifier,
                  telegram=SimpleNamespace(send_article=lambda event: sent.append(event)))
    radar.dispatch()
    assert sent == []
    assert repo.db.execute('SELECT error_message FROM notifications').fetchone()[0] == 'economic_korean_review_pending'


def test_baseline_blocks_backlog_but_not_event_during_first_fetch(repo, classifier, event_factory):
    collector = SimpleNamespace(name="fake", interval=120)
    radar = Radar([collector], repo, classifier)
    baseline = json.loads(repo.state("fake")["last_cursor"])
    from us_market_monitor.processors.normalizer import parse_time

    start = parse_time(baseline["baseline_at"])
    old = event_factory(published_at=start - timedelta(seconds=1))
    new = event_factory(
        source_event_id="101",
        source_url="https://example.com/101",
        published_at=start + timedelta(milliseconds=1),
    )
    radar.process("fake", [old, new], baseline)
    assert repo.status()["important_events"] == 1
    row = repo.db.execute("SELECT source_event_id FROM events").fetchone()
    assert row[0] == "101"


def test_first_failure_does_not_reset_baseline(repo, classifier):
    def fail(*_):
        raise FetchError("http_503")

    collector = SimpleNamespace(name="fake", interval=120, collect=fail)
    radar = Radar([collector], repo, classifier)
    baseline = repo.state("fake")["last_cursor"]
    radar.run(once=True)
    assert repo.state("fake")["error_count"] == 1
    assert repo.state("fake")["last_success_at"] is None
    Radar([collector], repo, classifier)
    assert repo.state("fake")["last_cursor"] == baseline


def test_failed_collector_does_not_stop_other_sources(repo, classifier, event_factory):
    def fail(*_):
        raise FetchError("http_403")

    def success(since, _):
        return [event_factory(published_at=since + timedelta(milliseconds=1))]

    bad = SimpleNamespace(name="bad", interval=120, collect=fail)
    good = SimpleNamespace(name="good", interval=120, collect=success)
    Radar([bad, good], repo, classifier).run(once=True)
    assert repo.state("bad")["error_count"] == 1
    assert repo.state("good")["last_success_at"]
    assert repo.status()["important_events"] == 1


def test_rejected_events_never_enter_event_table(repo, classifier, event_factory):
    collector = SimpleNamespace(name="fake", interval=120)
    radar = Radar([collector], repo, classifier)
    ordinary = event_factory("NVIDIA gaming keyboard promotion", published_at=utcnow())
    radar.process("fake", [ordinary], json.loads(repo.state("fake")["last_cursor"]))
    assert repo.status()["important_events"] == 0
    assert repo.status()["notifications"] == {}
    assert repo.seen(ordinary)


def test_dry_run_never_sends(repo, classifier, filters, event_factory, capsys):
    sent = []
    collector = SimpleNamespace(name="fake", interval=120)
    radar = Radar(
        [collector],
        repo,
        classifier,
        dry_run=True,
        telegram=SimpleNamespace(send=lambda text: sent.append(text)),
    )
    event = event_factory(published_at=utcnow())
    radar.process("fake", [event], json.loads(repo.state("fake")["last_cursor"]))
    radar.dispatch()
    assert sent == []
    assert "NOT SENT" in capsys.readouterr().out


def test_successful_delivery_once(repo, classifier, filters, event_factory):
    sent = []
    repo.ingest(classifier.classify(event_factory()), filters)

    def send(text):
        sent.append(text)
        return 42

    radar = Radar([], repo, classifier, telegram=SimpleNamespace(send=send))
    radar.dispatch()
    radar.notification_due = 0
    radar.dispatch()
    assert len(sent) == 1
    assert repo.status()["notifications"] == {"sent": 1}


def test_ambiguous_delivery_is_held(repo, classifier, filters, event_factory):
    repo.ingest(classifier.classify(event_factory()), filters)

    def send(_):
        raise DeliveryError("timeout", ambiguous=True)

    radar = Radar([], repo, classifier, telegram=SimpleNamespace(send=send))
    radar.dispatch()
    assert repo.status()["notifications"] == {"unknown": 1}
