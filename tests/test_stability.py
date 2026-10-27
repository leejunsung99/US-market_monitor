from types import SimpleNamespace

import pytest

from us_market_monitor.notifications.compact import nominal
from us_market_monitor.scheduler.jobs import Radar


@pytest.mark.parametrize('title,summary,kind', [
    ('Lam Research Corporation Reports Financial Results for the Quarter Ended September 27, 2026',
     'Lam Research today announced financial results for the quarter ended September 27, 2026.', 'earnings'),
    ('TSLA Stock Reverses As Tesla Cuts Full-Year Capex Guidance To $20B',
     'Tesla cuts its full-year capex guidance.', 'earnings_guidance'),
    ('NVIDIA expects revenue growth of 10%', 'NVIDIA expects revenue growth of 10%.', 'earnings_guidance'),
    ('Micron Technology Maps AI Memory Future With HBM, DRAM and NAND Roadmap',
     'Micron Technology outlined a technology and product roadmap centered on AI memory, including DRAM and NAND.',
     'semiconductor_technology'),
    ('Microsoft signs $300 million contract for data center', '', 'contract_investment'),
])
def test_material_alerts_previously_lost_to_strict_gates(classifier, event_factory, title, summary, kind):
    event = classifier.classify(event_factory(title, summary=summary))
    assert event is not None
    assert event.event_type == kind


@pytest.mark.parametrize('title,summary', [
    ('Meta Posts Q3 Double Beat', 'Meta reports financial results after the closing bell on Wednesday.'),
    ('Should I Buy Bitcoin Before It Doubles?', 'Bitcoin revenue could grow 10%.'),
    ('Meta Dropped 9% This Week: Time to Sell or Load Up?', 'Meta reports quarterly results next week.'),
    ('The Hidden $496B Catalyst Waiting to Ignite Amazon Stock', 'Amazon reports Q3 results.'),
    ('See What 5 Builders Are Making With Gemini 4', 'Google launches a new AI platform.'),
])
def test_relaxation_keeps_previews_advice_and_promotions_out(classifier, event_factory, title, summary):
    assert classifier.classify(event_factory(title, summary=summary)) is None


def test_preparation_failure_does_not_hold_next_article_or_spend_telegram_retries(
    repo, classifier, filters, event_factory, monkeypatch,
):
    first = classifier.classify(event_factory())
    second = classifier.classify(event_factory('Microsoft reports quarterly results',
        source_event_id='200', source_url='https://example.com/200'))
    first_id, second_id = repo.ingest(first, filters), repo.ingest(second, filters)
    monkeypatch.setattr('us_market_monitor.scheduler.jobs.enrich_article', lambda event, **kw: event)
    sent = []

    def translate(event):
        if event.title == first.title:
            event.metadata['processing_failure'] = {'code': 'unsupported_claim_numbers'}
            return False
        return True

    radar = Radar([], repo, classifier, translator=SimpleNamespace(translate=translate),
                  telegram=SimpleNamespace(send_article=lambda event: sent.append(event.title) or 42))
    radar.dispatch()
    assert radar.notification_due == 0
    radar.dispatch()
    assert sent == [second.title]
    assert repo.db.execute('SELECT status FROM notifications WHERE event_id=?', (second_id,)).fetchone()[0] == 'sent'
    row = repo.db.execute('SELECT * FROM notifications WHERE event_id=?', (first_id,)).fetchone()
    assert row['preparation_attempts'] == 1 and row['retry_count'] == 0
    assert repo.db.execute('SELECT code FROM processing_attempts WHERE success=0').fetchone()[0] == 'unsupported_claim_numbers'


def test_preparation_outage_retains_item_beyond_eight_attempts(repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    event_id = repo.ingest(event, filters)
    repo.preparation_failure(event_id, event, attempts=8)
    row = repo.db.execute('SELECT * FROM notifications').fetchone()
    assert row['status'] == 'pending' and row['preparation_attempts'] == 9
    assert row['retry_count'] == 0


def test_pipeline_status_separates_selection_delivery_and_processing(repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    event_id = repo.ingest(event, filters)
    event.metadata['processing_timing'] = {'seconds': 42.5}
    repo.record_preparation(event_id, event, True)
    status = repo.pipeline_health()
    assert status['unique_observations'] == 1
    assert status['selected_by_delivery_status'] == {'pending': 1}
    assert status['processing_successes'] == 1
    assert status['processing_median_seconds'] == 42.5


@pytest.mark.parametrize('text,wanted', [('포함한다.', '포함함'), ('유지된다.', '유지됨'),
                                         ('목표를 유지했다.', '목표를 유지했음')])
def test_nominal_outline_endings(text, wanted):
    assert nominal(text) == wanted
