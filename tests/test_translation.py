"""Production local translator, evidence, cache and output integration."""

from types import SimpleNamespace

from us_market_monitor.notifications.compact import format_compact_event
from us_market_monitor.processors.ollama import OllamaProcessor, card_sections, source_text


def processor(monkeypatch, result=None):
    class Client:
        def __init__(self, model, url, timeout):
            self.model, self.system_prompt = model, ''

        def summarize_resumable(self, source, metadata, work):
            if isinstance(result, Exception):
                raise result
            return result or {
                'title_ko': '메타 실적 발표',
                'summary': {'text_ko': '매출 성장 확인했음', 'evidence': 'Meta reported revenue growth.'},
                'points': [{'text_ko': '매출 성장 보고했음', 'evidence': 'Meta reported revenue growth.'}],
                'limitations_ko': '',
            }, {'wall_seconds': 1}

    monkeypatch.setattr('us_market_monitor.processors.ollama.GroundedOllama', Client)
    return OllamaProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://localhost:11435',
                                           ollama_timeout=600))


def test_production_generates_formats_and_reuses_local_cache(monkeypatch, event_factory):
    event = event_factory('Meta reports results', summary='Meta reported revenue growth.')
    event.related_tickers = ('META',)
    client = processor(monkeypatch)
    assert client.translate(event)
    assert event.translation_hash.startswith('ollama:')
    assert '매출 성장 확인했음' in format_compact_event(event)
    monkeypatch.setattr(client.client, 'summarize_resumable', lambda *args: (_ for _ in ()).throw(AssertionError('cached')))
    assert client.translate(event)


def test_failed_generation_is_held_without_old_cloud_translation(monkeypatch, event_factory):
    event = event_factory()
    assert not processor(monkeypatch, ValueError('invalid_output')).translate(event)
    assert 'ollama_summary' not in event.metadata


def test_article_style_is_rejected(monkeypatch, event_factory):
    result = {'title_ko': '기사', 'summary': {'text_ko': '증가했습니다.', 'evidence': 'Source fact.'},
              'points': [{'text_ko': '상승했음', 'evidence': 'Source fact.'}], 'limitations_ko': ''}
    assert not processor(monkeypatch, result).translate(event_factory())


def test_source_uses_full_body_and_complete_short_headline(event_factory):
    event = event_factory('Tesla CFO revises capex', summary='Clipped...')
    assert source_text(event) == event.title
    event.body = 'Tesla reported profit growth. Tesla expects higher demand.'
    assert 'higher demand' in source_text(event)


def test_commercial_release_keeps_commitment_duration_allocation_and_constraints(event_factory):
    event = event_factory('Oracle announces investment', event_type='contract_investment',
        body='/PRNewswire/ Oracle committed $1 billion.\nSpanning the next four years, the investment expands programs.\n'
             'It encompasses planned investments in training and cloud migration.\n'
             'Oracle provides tools for partners to develop AI solutions.\n'
             'The agreement remains subject to regulatory approval.')
    source = source_text(event)
    assert '$1 billion' in source and 'four years' in source
    assert 'planned investments' in source and 'regulatory approval' in source
    assert 'provides tools' not in source


def test_ollama_evidence_survives_database_roundtrip(monkeypatch, repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    event.body = 'Meta reported revenue growth.'
    assert processor(monkeypatch).translate(event)
    event_id = repo.ingest(event, filters)
    loaded = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())
    assert loaded.metadata['ollama_summary'] == event.metadata['ollama_summary']


def test_failure_diagnostics_and_partial_work_survive_retry(monkeypatch, event_factory, repo, classifier, filters):
    event = classifier.classify(event_factory())
    client = processor(monkeypatch)

    def fail(source, metadata, work):
        work['batches'] = {'0': {'claims': ['verified']}}
        raise ValueError('unsupported_claim_numbers')

    monkeypatch.setattr(client.client, 'summarize_resumable', fail)
    assert not client.translate(event)
    event_id = repo.ingest(event, filters)
    with repo.db:
        repo.update_article(event_id, event)
    loaded = repo.row_event(repo.db.execute('SELECT * FROM events').fetchone())
    assert loaded.metadata['ollama_work']['batches']['0']['claims'] == ['verified']
    assert loaded.metadata['processing_failure']['code'] == 'unsupported_claim_numbers'


def test_gross_margin_label_is_preserved_in_final_korean_card():
    claim = {'text_ko': '에너지 부문 순이익률이 20.4%에서 33.1%로 개선됐다고 밝혔다.',
             'evidence': 'Energy Gross Margins Improved From 20.4% To 33.1%'}
    sections = card_sections({'source': claim['evidence'], 'result': {'summary': claim, 'points': []}})
    text = sections['핵심내용'][0]
    assert '매출총이익률' in text and '순이익률' not in text
    assert text.endswith('밝혔음')
