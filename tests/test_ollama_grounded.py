import copy

import pytest

from us_market_monitor.processors.ollama_grounded import (
    GroundedOllama,
    checked_claim,
    source_units,
    validate_financial_meaning,
)

SOURCE = 'Meta reported revenue growth of 26%.\nNet income was $9.9 billion.\nGuidance remains unchanged.'


def test_references_are_owned_by_application_and_numbers_must_be_in_cited_units():
    units = source_units(SOURCE)
    claim = checked_claim({'source_ids': [1], 'text_ko': '매출 26% 성장'}, units, SOURCE)
    assert claim['evidence'] == SOURCE.splitlines()[0]
    with pytest.raises(ValueError, match='unsupported_claim_numbers'):
        checked_claim({'source_ids': [3], 'text_ko': '가이던스 26% 성장'}, units, SOURCE)
    with pytest.raises(ValueError, match='invalid_source_reference'):
        checked_claim({'source_ids': [999], 'text_ko': '성장 유지'}, units, SOURCE)


def test_facts_cannot_mix_figures_from_different_units():
    units = source_units(SOURCE)
    with pytest.raises(ValueError, match='invalid_source_reference'):
        checked_claim({'source_ids': [1, 3], 'text_ko': '순이익 $9.9 billion'}, units, SOURCE)


def test_explicit_to_from_monetary_comparison_is_rendered_in_correct_korean_order():
    source = 'Net cash used declined to $55.1 million from $63.7 million a year earlier.'
    claim = checked_claim({'source_ids': [1],
                           'text_ko': '영업 현금 사용액 $55.1 million에서 $63.7 million로 감소함'},
                          source_units(source), source)
    assert '$63.7 million에서 $55.1 million로' in claim['text_ko']


def test_verified_facts_survive_failed_repair_and_next_attempt_only_repairs_bad_fact(monkeypatch):
    client = GroundedOllama()
    work, calls = {}, []
    bad = {'source_ids': [2], 'text_ko': '순이익 $8.1 billion'}

    def request(units, metadata, repair=None):
        calls.append([u['id'] for u in units])
        if len(calls) == 1:
            return {'title_ko': '메타 9999년 실적', 'items': [
                {'source_ids': [1], 'text_ko': '매출 26% 성장'}, bad]}, {}
        if len(calls) == 2:
            return {'title_ko': '요약', 'items': [bad]}, {}
        return {'title_ko': '요약', 'items': [{'source_ids': [2], 'text_ko': '순이익 $9.9 billion'}]}, {}

    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(ValueError, match='unsupported_claim_numbers'):
        client.summarize_resumable(SOURCE, {}, work)
    assert len(work['batches']['0']['claims']) == 1
    result, _ = client.summarize_resumable(SOURCE, {}, copy.deepcopy(work))
    assert calls == [[1, 2, 3], [2], [2]]
    assert result['title_ko'] == '매출 26% 성장'
    assert result['points'][0]['text_ko'] == '순이익 $9.9 billion'


def test_long_source_units_cover_every_character_without_truncation():
    source = 'Revenue remains material. ' * 1200
    units = source_units(source)
    assert ''.join(source[u['start']:u['end']] for u in units) == source
    assert all(len(u['text']) <= 1800 for u in units)


def test_currency_tokens_are_restored_per_claim_without_poisoning_other_claims(monkeypatch):
    client = GroundedOllama()
    payloads = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {'done': True, 'message': {'content': '{"title_ko":"요약","items":['
                    '{"source_ids":[1],"text_ko":"매출 __VALUE_A__ 증가"},'
                    '{"source_ids":[2],"text_ko":"순이익 __VALUE_UNKNOWN__"}]}'}}

    def post(url, **kwargs):
        payloads.append(kwargs['json'])
        return Response()

    monkeypatch.setattr(client.session, 'post', post)
    result, _ = client._request(source_units(SOURCE), {})
    assert result['items'][0]['text_ko'] == '매출 26% 증가'
    assert '__VALUE_UNKNOWN__' in result['items'][1]['text_ko']
    assert '$9.9' not in payloads[0]['messages'][1]['content']
    assert payloads[0]['format']['properties']['items']['items']['properties']['source_ids']['items']['enum'] == [1, 2, 3]


def test_bad_repair_id_keeps_original_source_basis_on_next_retry(monkeypatch):
    client, work, calls = GroundedOllama(), {}, []

    def request(units, metadata, repair=None):
        calls.append([u['id'] for u in units])
        item = ({'source_ids': [2], 'text_ko': '순이익 $8.1 billion'} if len(calls) == 1 else
                {'source_ids': [-1], 'text_ko': '순이익 $9.9 billion'} if len(calls) == 2 else
                {'source_ids': [2], 'text_ko': '순이익 $9.9 billion'})
        return {'title_ko': '순이익', 'items': [item]}, {}

    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(ValueError, match='invalid_source_reference'):
        client.summarize_resumable(SOURCE, {}, work)
    result, _ = client.summarize_resumable(SOURCE, {}, work)
    assert calls == [[1, 2, 3], [2], [2]]
    assert result['summary']['evidence'] == SOURCE.splitlines()[1]


@pytest.mark.parametrize('text,evidence,code', [
    ('실적 예상치 미달', 'TTM fell despite beating estimates.', 'reversed_earnings_comparison'),
    ('실적 예상치 상회', 'Graco missed estimates.', 'reversed_earnings_comparison'),
    ('전년 대비 이익 전망 유지', 'Nokia kept its full-year profit outlook.', 'changed_comparison_period'),
    ('전환 가정 55%에서 75%로 하향', 'Conversion assumption cut to 35% to 55%, from 55% to 75%.', 'incomplete_numeric_range'),
    ('현금 사용액 $55.1 million에서 $63.7 million로 감소',
     'Net cash used declined to $55.1 million from $63.7 million a year earlier.', 'reversed_comparison_values'),
    ('자본 지출 전분기 대비 감소', 'Capital spending declined in the prior-year quarter.', 'changed_comparison_period'),
])
def test_financial_polarity_period_and_range_errors_are_held(text, evidence, code):
    with pytest.raises(ValueError, match=code):
        validate_financial_meaning(text, evidence)
