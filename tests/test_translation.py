"""Production local translator, evidence, cache and output integration."""

from types import SimpleNamespace

from us_market_monitor.notifications.compact import format_compact_event
from us_market_monitor.processors.ollama import OllamaProcessor, card_sections, source_text


def processor(monkeypatch, result=None):
    class Client:
        def __init__(self, model, url, timeout):
            self.model, self.system_prompt = model, ''

        def summarize(self, source, metadata):
            if isinstance(result, Exception):
                raise result
            return result or {
                'title_ko': '메타 실적 발표',
                'summary': {'text_ko': '매출 성장 확인했음', 'evidence': 'Meta reported revenue growth.'},
                'points': [{'text_ko': '매출 성장 보고했음', 'evidence': 'Meta reported revenue growth.'}],
                'limitations_ko': '',
            }, {'wall_seconds': 1}

    monkeypatch.setattr('us_market_monitor.processors.ollama.OllamaPreview', Client)
    return OllamaProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://localhost:11435',
                                           ollama_timeout=600))


def test_production_generates_formats_and_reuses_local_cache(monkeypatch, event_factory):
    event = event_factory('Meta reports results', summary='Meta reported revenue growth.')
    event.related_tickers = ('META',)
    client = processor(monkeypatch)
    assert client.translate(event)
    assert event.translation_hash.startswith('ollama:')
    assert '매출 성장 확인했음' in format_compact_event(event)
    monkeypatch.setattr(client.client, 'summarize', lambda *args: (_ for _ in ()).throw(AssertionError('cached')))
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


def test_ollama_evidence_survives_database_roundtrip(monkeypatch, repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    event.body = 'Meta reported revenue growth.'
    assert processor(monkeypatch).translate(event)
    event_id = repo.ingest(event, filters)
    loaded = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())
    assert loaded.metadata['ollama_summary'] == event.metadata['ollama_summary']


def test_invalid_claim_is_regenerated_from_its_evidence_only(monkeypatch, event_factory):
    event = event_factory('Meta results', summary='Meta reported revenue growth. Guidance remains unchanged.')
    client = processor(monkeypatch)
    bad = {'title_ko': '실적',
           'summary': {'text_ko': '매출 성장', 'evidence': 'Meta reported revenue growth.'},
           'points': [{'text_ko': '2027년 가이던스 유지', 'evidence': 'Guidance remains unchanged.'}],
           'limitations_ko': ''}
    calls = []

    def summarize(source, metadata):
        calls.append(source)
        if len(calls) == 1:
            error = ValueError('unsupported_claim_numbers')
            error.result, error.stats = bad, {'wall_seconds': 1}
            raise error
        assert source == 'Guidance remains unchanged.'
        fixed = {'text_ko': '가이던스 유지', 'evidence': source}
        return {'title_ko': '전망', 'summary': fixed, 'points': [fixed], 'limitations_ko': ''}, {'wall_seconds': 1}

    monkeypatch.setattr(client.client, 'summarize', summarize)
    assert client.translate(event)
    assert event.metadata['ollama_summary']['result']['points'][0]['text_ko'] == '가이던스 유지'
    assert len(calls) == 2


def test_gross_margin_label_is_preserved_in_final_korean_card():
    claim = {'text_ko': '에너지 부문 순이익률이 20.4%에서 33.1%로 개선됐다고 밝혔다.',
             'evidence': 'Energy Gross Margins Improved From 20.4% To 33.1%'}
    sections = card_sections({'source': claim['evidence'], 'result': {'summary': claim, 'points': []}})
    text = sections['핵심내용'][0]
    assert '매출총이익률' in text and '순이익률' not in text
    assert text.endswith('밝혔음')
