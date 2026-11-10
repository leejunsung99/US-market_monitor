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


def test_outline_normalized_emphasis_survives_validation(event_factory):
    """ground_display must emphasize the delivered outline, not the pre-outline wording."""
    event, record = sample(event_factory)
    revenue = record['result']['items'][1]['source_id']
    basis = next(u['text'] for u in record['units'] if u['id'] == revenue)
    item = ground_display(
        {'text_ko': 'Grace 최근 12 개월 매출 $5 billion 증가했습니다', 'focus_ko': '', 'section': '', 'priority': 2},
        basis,
    )
    # nominal() rewrites the ending, so a pre-normalization span would be lost here.
    assert item['text_ko'] == 'Grace 최근 12 개월 매출 $5 billion 증가'
    assert item['focus_ko'] in item['text_ko']
    result = {'title_ko': record['result']['title_ko'],
              'items': [{**item, 'source_id': revenue, 'priority': 2}]}
    assert validate_caption_result(result, record['units'], record['source'])


def test_polite_verb_endings_outside_the_whitelist_become_nominal():
    """A faithful bullet must not be held forever by the outline style gate alone."""
    from us_market_monitor.notifications.compact import nominal
    from us_market_monitor.processors.ollama_grounded import checked_claim

    assert nominal('3분기 재무 결과를 공표했습니다') == '3분기 재무 결과를 공표'
    assert nominal('신규 공장 가동을 개시하였습니다') == '신규 공장 가동을 개시'
    assert nominal('계약이 체결되었습니다') == '계약이 체결'
    source = ('On October 28, 2026, GE Vernova Inc. released its third-quarter 2026 '
              'financial results on its investor relations website.')
    units = source_units(source)
    claim = checked_claim(
        {'source_ids': [1], 'text_ko': 'GE Vernova는 2026년 3분기 재무 결과를 공표했습니다'}, units, source)
    assert claim['text_ko'].endswith('공표')


def test_long_subject_is_shortened_instead_of_failing_every_retry(event_factory, watchlist, monkeypatch):
    from us_market_monitor.processors.caption import short_title

    # Figures are stripped before the length check, so this 33-char title fits.
    assert short_title('GE Vernova, Trench Group 인수') == 'GE Vernova, Trench Group 인수'
    assert short_title('연준 FOMC 성명: 경제 활동 강세, 인플레이션 목표 달성 위한 추가 정보 수집') == '연준 FOMC 성명'
    assert short_title('샌디스크 주가 상승, AI 메모리 수요 증가 및 실적 발표 전 기대감') == '샌디스크 주가 상승'
    long = '알파벳 Q3 실적: 매출 25% 증가, 클라우드 수익 103% 급증, 2027 년 CAPEX 대폭 확대 전망'
    assert short_title(long) == '알파벳 Q3 실적'
    for title in ('GE Vernova, Trench Group 인수', long, '샌디스크 주가 상승, AI 메모리 수요 증가 및 실적 발표 전 기대감'):
        assert 1 <= len(short_title(title)) <= 32

    event, record = sample(event_factory)
    client = CaptionProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://127.0.0.1:11435',
                                              ollama_timeout=10, watchlist=watchlist))
    output = copy.deepcopy(record['result'])
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        'CPU 사업·실적 전망: 매출 두 배 이상 증가 및 Grace 누적 매출 확대 전망',
        [i['source_id'] for i in output['items']]))
    monkeypatch.setattr(client, '_request', lambda units, *a, **kw: {
        'title_ko': output['title_ko'], 'items': [i for i in output['items'] if i['source_id'] == units[0]['id']]})
    assert client.translate(event)
    assert len(event.metadata['caption_brief']['result']['title_ko']) <= 32


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


def test_untranslated_content_is_never_published(event_factory, watchlist, monkeypatch):
    """A secondary bullet left in English is dropped, never delivered."""
    event, record = sample(event_factory)
    client = CaptionProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://localhost',
                                             ollama_timeout=10, watchlist=watchlist))
    bad = copy.deepcopy(record['result'])
    bad['items'][1].update(text_ko='Revenue is expected to double.', focus_ko='double')
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        bad['title_ko'], [i['source_id'] for i in bad['items']]))
    monkeypatch.setattr(client, '_request', lambda units, *a, **kw: {
        'title_ko': bad['title_ko'],
        'items': [copy.deepcopy(i) for i in bad['items'] if i['source_id'] == units[0]['id']]})
    assert client.translate(event)
    published = visible_text(render_caption(event, event.metadata['caption_brief']))
    assert 'Revenue is expected to double' not in published
    assert 'FY28 CPU 매출 두 배 이상 증가 예상' in published
    assert event.metadata['caption_brief']['stats']['dropped'][0]['code'] == 'not_korean'


def test_production_holds_when_every_fact_is_invalid(event_factory, watchlist, monkeypatch):
    """With no verifiable fact at all there is nothing to deliver, so the alert is held."""
    event, record = sample(event_factory)
    client = CaptionProcessor(SimpleNamespace(ollama_model='local', ollama_base_url='http://localhost',
                                             ollama_timeout=10, watchlist=watchlist))
    bad = copy.deepcopy(record['result'])
    for item in bad['items']:
        item.update(text_ko='Revenue is expected to double.', focus_ko='double')
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        bad['title_ko'], [i['source_id'] for i in bad['items']]))
    monkeypatch.setattr(client, '_request', lambda units, *a, **kw: {
        'title_ko': bad['title_ko'],
        'items': [copy.deepcopy(i) for i in bad['items'] if i['source_id'] == units[0]['id']]})
    assert not client.translate(event)
    assert 'caption_brief' not in event.metadata
    assert event.metadata['processing_failure']['code']


def test_unverifiable_secondary_bullet_is_dropped_but_the_lead_fact_is_required(
        event_factory, watchlist, monkeypatch):
    """One bad unit out of many must not suppress the verified facts."""
    event, record = sample(event_factory)
    config = SimpleNamespace(ollama_model='local', ollama_base_url='http://127.0.0.1:11435',
                             ollama_timeout=10, watchlist=watchlist)
    client = CaptionProcessor(config)
    output = copy.deepcopy(record['result'])
    good, bad = output['items'][0], output['items'][1]
    # The second unit keeps inventing a figure the cited unit does not support.
    bad['text_ko'], bad['focus_ko'] = 'Grace 매출 $9 billion 초과', '매출 $9 billion 초과'
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        output['title_ko'], [good['source_id'], bad['source_id']]))
    monkeypatch.setattr(client, '_request', lambda units, *a, **kw: {
        'title_ko': output['title_ko'],
        'items': [copy.deepcopy(i) for i in output['items'] if i['source_id'] == units[0]['id']]})

    assert client.translate(event)
    result = event.metadata['caption_brief']['result']
    assert [i['source_id'] for i in result['items']] == [good['source_id']]
    assert event.metadata['caption_brief']['stats']['dropped'][0]['code'] == 'unsupported_claim_numbers'

    # The lead fact itself is not optional: if it fails, the alert is still held.
    event.metadata.pop('caption_brief', None)
    event.metadata.pop('caption_work', None)
    monkeypatch.setattr(client, '_select', lambda units, headline: (
        output['title_ko'], [bad['source_id'], good['source_id']]))
    assert not client.translate(event)
    assert 'caption_brief' not in event.metadata


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
