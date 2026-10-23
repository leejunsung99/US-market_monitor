import pytest

from us_market_monitor.notifications.compact import (
    bullet_lines,
    format_compact_event,
    notification_category,
)
from us_market_monitor.notifications.telegram import DeliveryError, Telegram


def test_earnings_call_title_and_escaped_content(event_factory, monkeypatch):
    event = event_factory('Meta earnings call')
    event.related_tickers = ['META']
    monkeypatch.setattr('us_market_monitor.notifications.compact.RuleSummarizer.summarize',
                        lambda self, event: ('Meta', '• 핵심 발표: 수요 증가했습니다.\n'
                                             '• 주요 실적: 매출: 100 (전년 대비 +10%) · 영업이익: 20\n'
                                             '• 공시 유형: 8-K\n• 향후 계획: A & B 출시 예정입니다.'))
    text = format_compact_event(event)
    assert '메타(META) | 어닝콜 관련 보도' in text
    assert '· 수요 증가\n' in text
    assert '· 매출: 100 (전년 대비 +10%)\n· 영업이익: 20' in text
    assert '공시 유형' not in text
    assert 'A &amp; B 출시 예정' in text


@pytest.mark.parametrize('source,event_type,title,items,expected', [
    ('SEC EDGAR', 'core_product', 'LRCX 8-K', ['2.02'], '실적발표 요약'),
    ('Official/lam', 'earnings', 'Lam quarterly results', [], '실적발표 요약'),
    ('Finnhub/Chartmill', 'earnings', 'Lam quarterly results', [], '실적 관련 보도'),
    ('Finnhub/Yahoo', 'industry_outlook', 'Meta earnings call highlights', [], '어닝콜 관련 보도'),
    ('Official/meta', 'earnings', 'Meta earnings call transcript', [], '어닝콜 요약'),
    ('SEC EDGAR', 'contract_investment', 'LRCX 8-K', ['1.01'], '계약·투자'),
    ('Finnhub/News', 'earnings_guidance', 'Lam outlook', [], '실적 전망 관련 보도'),
    ('Official/qualcomm', 'earnings',
     'Qualcomm Schedules Fourth Quarter and Fiscal 2026 Earnings Release and Conference Call',
     [], '실적발표 일정'),
])
def test_category_uses_source_and_disclosure_metadata(
    event_factory, source, event_type, title, items, expected,
):
    event = event_factory(title)
    event.source = source
    event.event_type = event_type
    event.metadata['items'] = items
    assert notification_category(event) == expected


def test_photo_partial_delivery_never_retries_whole_article(event_factory, monkeypatch):
    event = event_factory()
    event.image_url = 'https://example.com/photo.jpg'
    monkeypatch.setattr('us_market_monitor.notifications.compact.format_compact_event',
                        lambda *args, **kwargs: 'x' * 1100)
    client = Telegram('test', 'chat')
    calls = []

    def request(method, payload):
        calls.append(method)
        if method == 'sendMessage':
            raise DeliveryError('connection_timeout', retryable=True)
        return 1

    monkeypatch.setattr(client, '_request', request)
    with pytest.raises(DeliveryError) as error:
        client.send_article(event)
    assert error.value.ambiguous and not error.value.retryable
    assert calls == ['sendPhoto', 'sendMessage']


def test_short_metrics_stay_together_long_paragraphs_have_blank_lines():
    short = ['시간외 주가: -6.4%', '실적 발표 후 주가 하락 보도']
    assert bullet_lines(short) == ['· ' + item for item in short]
    long = '업계 기업들의 성장에도 불구하고 주가 수준에 대한 우려와 자금 조달 비용 상승 부담이 남아 있다는 설명'
    assert bullet_lines([*short, long, long]) == [
        '· ' + short[0], '· ' + short[1], '', '· ' + long, '', '· ' + long,
    ]
