from html import unescape
from types import SimpleNamespace

import pytest

from us_market_monitor.notifications.telegram import DeliveryError, Telegram, split_card
from us_market_monitor.processors.briefing import prepare_brief, select_points
from us_market_monitor.processors.ollama_preview import OllamaPreview, validate_result
from us_market_monitor.processors.translator import Translator


def test_material_facts_beyond_old_sentence_and_character_limits():
    sentences = [f'Acme reported revenue of ${i} billion for a distinct business unit '
                 + ('with continuing operations and region-specific reporting ' * 5) + '.'
                 for i in range(1, 16)]
    points = select_points(' '.join(sentences) + ' Subscribe for more news.')
    assert len(points) == 15
    assert sum(map(len, points)) > 2400
    assert '$15 billion' in points[-1]
    assert 'Subscribe' not in ' '.join(points)


def test_count_removal_does_not_admit_paid_promotion():
    text = ('Acme reported revenue of $10 billion. '
            'Lock in MarketBeat All Access for $149 a year. '
            'Should You Invest $1,000 in Meta Platforms Right Now? '
            'Full-year capital expenditures are forecast at $135 billion.')
    points = select_points(text)
    assert len(points) == 2
    assert 'capital expenditures' in points[-1]


def test_reporter_credit_and_sponsored_tail_are_not_material_news():
    text = ('Netflix reported double-digit revenue growth.\n'
            'AP Business Writer Elaine Kurtenbach contributed to this report.\n'
            'By spreading exposure across multiple asset classes, investors can manage risk.\n'
            'FarmTogether offers investments starting at $15,000.')
    points = select_points(text)
    assert points == ['Netflix reported double-digit revenue growth.']


def test_full_source_survives_prepare_and_database(repo, classifier, filters, event_factory):
    event = classifier.classify(event_factory())
    original = 'Acme reported revenue of $10 billion. Subscribe for more news.'
    prepare_brief(event, original)
    assert event.body == original
    event_id = repo.ingest(event, filters)
    loaded = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone())
    assert loaded.body == original
    assert 'Subscribe' not in loaded.article_summary


def test_split_card_preserves_emoji_entities_and_self_contained_headings():
    text = '<b><u>🇺🇸제목</u></b>\n' + '\n'.join('· 항목 &amp; 수치 🇺🇸' for _ in range(500))
    parts = split_card(text)
    assert len(parts) > 1
    assert all(len(p.encode('utf-16-le')) // 2 <= 3800 for p in parts)
    assert '\n'.join(parts) == text
    long_parts = split_card('· ' + '&amp;🇺🇸' * 2000)
    assert ''.join(unescape(p) for p in long_parts) == '· ' + '&🇺🇸' * 2000


def test_multipart_failure_is_held_after_first_success(monkeypatch):
    client, calls = Telegram('test', 'chat'), []

    def send(text):
        calls.append(text)
        if len(calls) == 2:
            raise DeliveryError('connection_timeout', retryable=True)
        return 1

    monkeypatch.setattr(client, 'send', send)
    with pytest.raises(DeliveryError) as error:
        client._send_parts('\n'.join(['body facts'] * 1000))
    assert error.value.ambiguous and not error.value.retryable
    assert len(calls) == 2


def test_llm_schema_accepts_more_than_three_evidenced_details():
    evidence = 'The company reported higher revenue and lower costs.'
    result = {'title_ko': '실적 요약', 'summary': {'text_ko': '매출 성장', 'evidence': evidence},
              'points': [{'text_ko': '중요 내용', 'evidence': evidence} for _ in range(12)],
              'limitations_ko': ''}
    assert len(validate_result(result, evidence)['points']) == 12


def test_large_translation_is_batched_without_dropping_points(monkeypatch, event_factory):
    event = event_factory()
    prepare_brief(event, ' '.join(f'Acme reported revenue of ${i} billion.' for i in range(1, 220)))
    config = SimpleNamespace(translator_key='test', translator_region='global',
                             translator_endpoint='https://example.com')
    batches = []

    def post(*args, **kwargs):
        batch = kwargs['json']
        batches.append(batch)
        return SimpleNamespace(status_code=200, json=lambda: [
            {'translations': [{'text': '번역 ' + item['Text']}]} for item in batch])

    monkeypatch.setattr('requests.post', post)
    assert Translator(config).translate(event)
    assert len(batches) >= 3
    assert sum(map(len, batches)) == 220  # title plus all 219 material facts
    assert len(event.summary_ko.splitlines()) == 219


def test_long_llm_source_processes_every_chunk_without_input_truncation(monkeypatch):
    source = 'The company reported improving revenue and margins. ' * 700
    client = OllamaPreview()
    chunks = []
    summarize = client.summarize

    def local_chunk(text, metadata):
        chunks.append(text)
        suffix = chr(64 + metadata['source_part'])
        evidence = 'The company reported improving revenue and margins.'
        return {'title_ko': '실적 요약',
                'summary': {'text_ko': '핵심' + suffix, 'evidence': evidence},
                'points': [{'text_ko': '세부' + suffix, 'evidence': evidence}],
                'limitations_ko': ''}, {'wall_seconds': 0}

    monkeypatch.setattr(client, 'summarize', local_chunk)
    result, stats = summarize(source, {'category': 'earnings'})
    assert ''.join(chunks) == source
    assert len(chunks) > 1
    assert len(result['points']) == 2 * len(chunks) - 1
    assert len(stats['chunks']) == len(chunks)
