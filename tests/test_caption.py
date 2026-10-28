import copy
from types import SimpleNamespace

import pytest

from us_market_monitor.notifications.caption import (
    caption_units,
    emphasize,
    render_caption,
    visible_text,
)
from us_market_monitor.notifications.telegram import Telegram
from us_market_monitor.processors.caption import (
    CaptionProcessor,
    caption_fingerprint,
    ground_display,
    validate_caption_result,
)
from us_market_monitor.processors.ollama import source_text
from us_market_monitor.processors.ollama_grounded import source_units


def sample(event_factory):
    event = event_factory('NVIDIA expands CPU business', body=(
        'NVIDIA expects CPU revenue to more than double in fiscal 28.\n'
        'Grace CPU revenue exceeded $5 billion in the trailing twelve months.\n'
        'AMD competes with NVIDIA in CPUs and AI accelerators.'
    ))
    source = source_text(event)
    units = source_units(source)
    growth = next(u['id'] for u in units if 'more than double' in u['text'])
    revenue = next(u['id'] for u in units if 'exceeded $5 billion' in u['text'])
    result = {'title_ko': 'CPU 사업·실적 전망', 'items': [
        {'source_id': growth, 'section': '핵심내용', 'priority': 1,
         'text_ko': '경영진: FY28 CPU 매출 두 배 이상 증가 예상', 'focus_ko': 'CPU 매출 두 배 이상 증가 예상'},
        {'source_id': revenue, 'section': '주요 실적', 'priority': 2,
         'text_ko': 'Grace 최근 12개월 매출 $5 billion 초과', 'focus_ko': '매출 $5 billion 초과'},
    ]}
    record = {'source': source, 'units': units, 'result': result, 'model': 'local',
              'issuer': '엔비디아(NVDA)', 'fingerprint': caption_fingerprint(event, 'local')}
    return event, record


def test_caption_renders_verified_emphasis_and_money_with_original_article_link(event_factory):
    event, record = sample(event_factory)
    text = render_caption(event, record)
    assert '<b>CPU 매출 두 배 이상 증가 예상</b>' in text
    assert '<b>매출 50억 달러 초과</b>' in text
    assert event.source_url in visible_text(text)
    assert visible_text(text).endswith('🇺🇸그로쓰리서치 U.S 채널[실시간 U.S 주요 알림]\nhttps://t.me/growth_us')
    assert '기사 원문' not in visible_text(text)
    assert 'AMD' not in visible_text(text)
    assert caption_units(text) <= 1024


def test_photo_budget_is_measured_after_entities_and_emoji_are_counted_conservatively():
    assert caption_units('<b>A &amp; B</b>') == 5
    assert caption_units('<a href="https://example.com/' + 'x' * 2000 + '">원문</a>') == 2
    assert caption_units('🇺🇸') == 4
    assert emphasize('A & B < C', 'A & B') == '<b>A &amp; B</b> &lt; C'


def test_caption_drops_whole_lower_priority_facts_to_fit_budget(event_factory):
    event, record = sample(event_factory)
    record['result']['items'] += [{**copy.deepcopy(record['result']['items'][1]),
                                  'text_ko': 'Grace 최근 12개월 매출 $5 billion 초과 ' + '기간 기준 확인 ' * i,
                                  'priority': 3} for i in range(1, 30)]
    for item in record['result']['items']:
        item['text_ko'] = item['text_ko'][:175]
    text = render_caption(event, record)
    assert caption_units(text) <= 1024
    assert '<b>CPU 매출 두 배 이상 증가 예상</b>' in text
    assert text.count('<b>') == text.count('</b>')
    assert '…' not in text


def test_invalid_highlight_and_background_fact_cannot_be_published(event_factory):
    event, record = sample(event_factory)
    bad = copy.deepcopy(record['result'])
    bad['items'][0]['focus_ko'] = '매출 확정 증가'
    with pytest.raises(ValueError, match='focus_not_in_text'):
        validate_caption_result(bad, record['units'], record['source'])
    peer = next(u['id'] for u in record['units'] if 'AMD competes' in u['text'])
    bad['items'][0].update(source_id=peer, text_ko='AMD는 CPU에서 엔비디아와 경쟁', focus_ko='엔비디아와 경쟁')
    with pytest.raises(ValueError, match='background_fact'):
        validate_caption_result(bad, record['units'], record['source'])


def test_changed_source_invalidates_caption_cache(event_factory):
    event, record = sample(event_factory)
    event.title += ' changed'
    with pytest.raises(ValueError, match='stale_caption'):
        render_caption(event, record)


def test_photo_and_bold_caption_are_sent_in_one_message_even_if_html_exceeds_1024(monkeypatch):
    client = Telegram('test-token', 'test-chat')
    text = '<b>' + '핵심' * 300 + '</b><a href="https://example.com/' + 'x' * 900 + '">원문</a>'
    assert len(text) > 1024 and caption_units(text) < 1024
    calls = []
    monkeypatch.setattr(client, '_request', lambda method, payload: calls.append((method, payload)) or 42)
    assert client._send_card(text, 'https://example.com/source.jpg') == 42
    assert [method for method, _ in calls] == ['sendPhoto']
    assert calls[0][1]['caption'] == text


def test_primary_company_comes_from_headline_not_all_mentioned_competitors(event_factory, watchlist):
    client = CaptionProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://127.0.0.1:11435',
                                             ollama_timeout=10, watchlist=watchlist))
    event = event_factory('NVIDIA takes CPU share from AMD and Intel')
    event.related_tickers = ('AMD', 'INTC', 'NVDA')
    assert client.issuer(event) == '엔비디아(NVDA)'
    assert client.issuer(event_factory('Virtuix Expects Strong Revenue Growth Following Meta Quest Launch')) == ''


def test_production_repairs_only_invalid_fact_and_persists_verified_caption(event_factory, watchlist, monkeypatch, tmp_path):
    from us_market_monitor.database.repository import Repository

    event, record = sample(event_factory)
    config = SimpleNamespace(ollama_model='local', ollama_base_url='http://127.0.0.1:11435',
                             ollama_timeout=10, watchlist=watchlist)
    client = CaptionProcessor(config)
    output = copy.deepcopy(record['result'])
    good = copy.deepcopy(output['items'][1])
    output['items'][1]['text_ko'] = 'Grace 매출 $9 billion 초과'
    output['items'][1]['focus_ko'] = '매출 $9 billion 초과'
    calls = []
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        output['title_ko'], [i['source_id'] for i in output['items']]))

    def request(units, headline, repair=None):
        calls.append((units, repair))
        items = [good] if repair else [i for i in output['items'] if i['source_id'] == units[0]['id']]
        return {'title_ko': output['title_ko'], 'items': items}

    monkeypatch.setattr(client, '_request', request)
    assert client.translate(event)
    assert len(calls) == 3
    assert [u['id'] for u in calls[2][0]] == [good['source_id']]
    assert 'caption_work' not in event.metadata
    assert caption_units(render_caption(event, event.metadata['caption_brief'])) <= 1024
    assert client.translate(event) and len(calls) == 3  # No repeated GPU call.
    repo = Repository(tmp_path / 'radar.sqlite3')
    # Reuse a schema-valid stored event to verify metadata survives restart.
    repo.db.execute("INSERT INTO events(id,event_key,source,source_event_id,title,summary,event_type,priority,related_tickers,source_url,published_at,detected_at,created_at,entity_key,industry,reason,material_facts) VALUES(1,'key','test','1','title','','business_development','HIGH','[]','https://example.com','2026-10-28','2026-10-28','2026-10-28','','','','{}')")
    repo.update_article(1, event)
    repo.db.execute("UPDATE events SET published_at='2026-10-28T00:00:00+00:00',detected_at='2026-10-28T00:00:00+00:00' WHERE id=1")
    loaded = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=1').fetchone())
    assert loaded.metadata['caption_brief'] == event.metadata['caption_brief']
    repo.close()


def test_production_holds_untranslated_or_invalid_content(event_factory, watchlist, monkeypatch):
    event, record = sample(event_factory)
    client = CaptionProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://localhost',
                                             ollama_timeout=10, watchlist=watchlist))
    bad = copy.deepcopy(record['result'])
    bad['items'][1].update(text_ko='Revenue is expected to double.', focus_ko='double')
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        bad['title_ko'], [i['source_id'] for i in bad['items']]))
    monkeypatch.setattr(client, '_request', lambda units, *a, **kw: {
        'title_ko': bad['title_ko'], 'items': [i for i in bad['items'] if i['source_id'] == units[0]['id']]})
    assert not client.translate(event)
    assert 'caption_brief' not in event.metadata
    assert event.metadata['processing_failure']['code']
    assert event.metadata['caption_work']['claims']  # Good facts survive the retry.


def test_caption_rejects_ttm_changed_to_year_over_year(event_factory):
    event, record = sample(event_factory)
    result = copy.deepcopy(record['result'])
    result['items'][1].update(text_ko='Grace 전년 동기 매출 $5 billion 초과', focus_ko='매출 $5 billion 초과')
    with pytest.raises(ValueError, match='changed_comparison_period'):
        validate_caption_result(result, record['units'], record['source'])


def test_metric_roles_and_attached_korean_currency_particles():
    from us_market_monitor.processors.briefing import polish_korean

    assert polish_korean('$96.221 billion로', '') == '962.21억 달러로'
    assert polish_korean('$17 billion 달러 예상', '') == '170억 달러 예상'
    eps = ground_display({'text_ko': 'EPS $2.22 대비 $2.0887 예상'},
                         'EPS came in at $2.22 against expectations of $2.0887.')
    assert eps['text_ko'] == 'EPS $2.22, 예상 $2.0887 상회'
    assert eps['section'] == '주요 실적'
    guidance = ground_display({'text_ko': 'Q3 매출 $108 billion±2% 예상'},
                              'For Q3, management guided to revenue of $108 billion, plus or minus 2%.')
    assert guidance['section'] == '향후 전망'
    margins = ground_display({'text_ko': 'Q4 매출총이익률 71%~72% 예상. 중국 데이터센터 수익 제외'},
                             'Q4 gross margin guidance comes in at 71% to 72%, and the Q3 outlook excludes Data Center compute revenue from China.')
    assert 'Q3 전망에서 중국' in margins['text_ko']
    assert ground_display(dict(margins), 'Q4 gross margin guidance comes in at 71% to 72%, and the Q3 outlook excludes Data Center compute revenue from China.') == margins


def test_acquisition_talks_cannot_be_rendered_as_completed(event_factory):
    from us_market_monitor.processors.ollama_grounded import checked_claim

    source = 'DoorDash is reportedly in advanced talks to acquire Instacart for $17 billion.'
    units = source_units(source)
    with pytest.raises(ValueError, match='lost_transaction_uncertainty'):
        checked_claim({'source_ids': [1], 'text_ko': '인스타카트 인수 가치 $17 billion'}, units, source)
    assert checked_claim({'source_ids': [1], 'text_ko': '인스타카트 $17 billion 인수 협상 보도'}, units, source)
    restored = ground_display({'text_ko': '인스타카트 인수 가치 $17 billion'}, source)
    assert '협상 보도·미확정' in restored['text_ko']
    assert checked_claim({'source_ids': [1], 'text_ko': restored['text_ko']}, units, source)
    with pytest.raises(ValueError, match='lost_transaction_uncertainty'):
        checked_claim({'source_ids': [1], 'text_ko': '인스타카트 인수 완료 보도'}, units, source)
