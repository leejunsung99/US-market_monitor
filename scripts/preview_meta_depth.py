"""Reviewed source-backed depth previews. Never changes the production pipeline."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from send_recent_compact import format_message  # noqa: E402

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402

PDF = 'https://s21.q4cdn.com/399680738/files/content_files/META-Q3-2026-Earnings-Call-Transcript.pdf'
RELEASE = 'https://www.sec.gov/Archives/edgar/data/1326801/000162828026066700/meta-09302026xexhibit991.htm'


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def build_cards():
    folder = ROOT / 'data/meta-review'
    release = normalized((folder / 'release.txt').read_text(encoding='utf-8'))
    transcript = normalized((folder / 'transcript.txt').read_text(encoding='utf-8'))
    audit = []

    def claim(source, text, *evidence):
        for quote in evidence:
            if normalized(quote) not in source:
                raise ValueError('Missing evidence: ' + quote)
        audit.append({'text_ko': text, 'evidence': list(evidence),
                      'source': RELEASE if source == release else PDF})
        return text

    def r(text, *evidence):
        return claim(release, text, *evidence)

    def t(text, *evidence):
        return claim(transcript, text, *evidence)

    results = {
        '핵심내용': [r('2026년 3분기 매출 성장에도 법률 비용 반영으로 영업이익 감소',
                    'Revenue $ 64,601 $ 51,242 26 %',
                    'Income from operations $ 12,988 $ 20,535 (37) %',
                    '$9.89 billion charge related to this agreement')],
        '주요 실적': [
            r('매출: 646.01억 달러 (전년 동기 대비 +26%)', 'Revenue $ 64,601 $ 51,242 26 %'),
            r('영업이익: 129.88억 달러 (전년 동기 대비 -37%)',
              'Income from operations $ 12,988 $ 20,535 (37) %'),
            r('순이익: 98.60억 달러 (전년 동기 대비 +264%)', 'Net income $ 9,860 $ 2,709 264 %'),
            r('희석 EPS: 3.83달러 (전년 동기 대비 +265%)',
              'Diluted earnings per share (EPS) $ 3.83 $ 1.05 265 %'),
            r('영업이익률: 20% (전년 동기 40%)', 'Operating margin 20 % 40 %'),
        ],
        '알아야 할 내용': [
            r('소송 합의 관련 비용: 98.9억 달러 반영', '$9.89 billion charge related to this agreement'),
            r('해당 비용 제외 시 희석 EPS: 7.40달러 (회사 제시 수치)',
              'Diluted EPS of $3.83 would have increased by $3.57 to $7.40'),
            r('광고 노출 수: 전년 동기 대비 +10%', 'increased by 10% year-over-year'),
            r('광고 평균 단가: 전년 동기 대비 +14%', 'increased by 14% year-over-year'),
        ],
        '투자·현금흐름': [
            r('설비투자: 349.1억 달러 (금융리스 원금 상환 포함)',
              'Capital expenditures, including principal payments on finance leases, were $34.91 billion'),
            r('잉여현금흐름: -21.2억 달러', 'free cash flow was negative $2.12 billion'),
        ],
        '향후 전망': [
            r('2026년 4분기 매출: 730억~760억 달러', 'fourth quarter 2026 total revenue', '$73-76 billion'),
            r('2026년 연간 총비용: 1,750억~1,790억 달러 (소송 비용 포함)', '$175-179 billion'),
            r('2026년 설비투자: 1,350억~1,450억 달러 (기존 1,300억~1,450억 달러)',
              '$135-145 billion, narrowed from our prior outlook of $130-145 billion'),
        ],
        '주주 관련 사항': [
            r('기존 주식 1주당 무의결권 Class C 주식 1주 배당 계획·경제적 효과는 2대1 분할',
              'one share of Class C common stock for each outstanding share', 'two-for-one stock split'),
        ],
    }
    call = {
        '핵심내용': [t('광고 성장과 Muse 초기 확산 확인·대규모 투자에 따른 비용·현금흐름 부담',
                     'ad revenue was $63.1 billion, up 26%',
                     'more than 10 million people in the US and Canada',
                     'Free cash flow was negative $2.1 billion')],
        '주요 실적': [
            t('매출: 약 646억 달러 (전년 동기 대비 +26%)', 'total revenue was $64.6 billion, up 26%'),
            t('영업이익: 약 130억 달러 (전년 동기 대비 -37%)',
              'operating income was $13.0 billion, representing a 37% decline'),
            t('소송 비용 제외 시 영업이익: 전년 동기 대비 +11% (CFO 설명)',
              'our third quarter operating income would have increa sed 11% year-over-year'),
            t('설비투자: 약 349억 달러·잉여현금흐름: 약 -21억 달러',
              'were $34.9 billion', 'Free cash flow was negative $2.1 billion'),
        ],
        '성장 근거·경영진 설명': [
            t('광고 노출 수 +10%·평균 단가 +14% (전년 동기 대비)',
              'ad impressions served across our services increased 10%', 'price per ad increased 14% year -over-year'),
            t('Muse: 출시 약 6주 만에 미국·캐나다 설정 이용자 1,000만 명 초과·설정 이용자 약 25%의 일간 사용',
              'In just a month and a half, more than 10 million people',
              'About 25% of people who have set up Muse use it on any given day'),
            t('AI 광고 보조 도구 권고 채택 광고주: 유사 미채택군 대비 전환 약 +5%·광고비 +9% (회사 추정)',
              'almost 5% more conversions', 'increased their advertising investment by 9% compared with similar non-adopters'),
        ],
        '주요 질의응답': [
            t('Muse 글로벌 확장의 제약: 높은 연산비용과 지역별 최적화·효율 개선 선행 필요 (CEO)',
              'because this is a compute - intensive service to run, we have not launched it globally yet',
              'wanting to optimize it for each place'),
            t('Muse 수익화: 기존 서비스보다 조기 추진 구상·거래 수수료·유료 구독·기업 서비스 중심 (CEO)',
              'work on monetization somewhat sooner', 'paid a fee based on the transactions',
              'they subscribe directly and pay', 'companies that want people to use Muse for work'),
            t('2027년 신규 AI 사업의 추가 매출 기여 기대·구체적 연간 가이던스는 미제시 (CFO)',
              'begin contributing incremental revenue in 2027',
              'not providing a full-year 2027 revenue outlook'),
        ],
        '향후 전망·확인할 변수': [
            t('2026년 4분기 매출: 730억~760억 달러', 'fourth quarter 2026 total revenue', '$73 -76 billion'),
            t('Muse 연산비용 효율: 향후 1년간 10배 초과 개선 목표·총 서비스 비용은 증가 예상',
              'total serving costs to rise', 'improve comp ute cost efficiency by more than tenfold over the next year'),
            t('Reality Labs: 2026년 영업손실 180억~190억 달러 전망·2027년 약 150억 달러로 축소 기대',
              'a range of $18-19 billion', 'losses to narrow next year to around $15 billion'),
        ],
    }
    # Keep each financial metric on its own line, including the transcript preview.
    call['주요 실적'][-1:] = [
        t('설비투자: 약 349억 달러 (금융리스 원금 상환 포함)',
          'Capital expenditures, including principal payments on finance leases, were $34.9 billion'),
        t('잉여현금흐름: 약 -21억 달러', 'Free cash flow was negative $2.1 billion'),
    ]
    cards = [('release', format_message('메타(META) | 실적발표 요약', results, RELEASE)),
             ('call', format_message('메타(META) | 어닝콜 요약', call, PDF))]
    (folder / 'evidence.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, message in cards:
        if len(message.encode('utf-16-le')) // 2 > 4096:
            raise ValueError('Message too long')
        (folder / (name + '-preview.html')).write_text(message, encoding='utf-8')
    return cards


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    cards = build_cards()
    if args.send:
        target = os.getenv('TELEGRAM_TEST_CHAT_ID', '').strip()
        if not target or target == config.telegram_chat_id:
            raise ValueError('Separate test channel required')
        telegram = Telegram(config.telegram_token, target)
        for name, message in cards:
            receipt = {'preview': name, 'message_id': telegram.send(message), 'target': 'test'}
            with (ROOT / 'data/meta-review/receipts.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(receipt) + '\n')
            print(json.dumps(receipt), flush=True)
    else:
        print(json.dumps({'prepared': [name for name, _ in cards]}))


if __name__ == '__main__':
    main()
