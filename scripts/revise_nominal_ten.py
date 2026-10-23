"""Source-reviewed outline-style edits of the user's latest ten main-channel alerts."""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

import revise_evening_alerts as editor  # noqa: E402
from send_recent_compact import format_message  # noqa: E402

from us_market_monitor.config import Config  # noqa: E402

FOLDER = ROOT / 'data/nominal-ten'


def prepare(config):
    rows = json.loads((FOLDER / 'originals.json').read_text(encoding='utf-8'))
    if len(rows) != 10 or {row['id'] for row in rows} != {98, 99, 100, 101, 102, 104, 105, 106, 107, 108}:
        raise ValueError('Unexpected reviewed batch')
    cards, audit = [], []
    for row in reversed(rows):
        event_id = row['id']
        source = '\n'.join(row.get(key, '') for key in ['title', 'summary', 'article_summary', 'body'])
        normalized = re.sub(r'\s+', ' ', source)
        sections = {}

        def add(category, text, *evidence, normalized=normalized, event_id=event_id, sections=sections):
            if not evidence or any(re.sub(r'\s+', ' ', quote) not in normalized for quote in evidence):
                raise ValueError('Missing evidence for event ' + str(event_id) + ': ' + text)
            sections.setdefault(category, []).append(text)
            audit.append({'event_id': event_id, 'text_ko': text, 'evidence': evidence})

        if event_id == 98:
            title = '독일 규제당국 | 은행 영업허가 취소'
            add('핵심내용', '함부르크 소재 이란 사데라트은행(Bank Saderat Iran) 영업허가 취소',
                'German regulator revokes license of Bank Saderat Iran in Hamburg')
        elif event_id == 99:
            title = '미국 주간 실업수당 청구 | 경제지표'
            add('신규 청구', '10월 17일 종료 주 신규 청구: 194,000명·전주 수정치 대비 -6,000명 (계절조정)',
                'week ending October 17', 'initial claims was 194,000, a decrease of 6,000')
            add('신규 청구', '신규 청구 4주 이동평균: 197,750명·전주 수정치 대비 -1,000명',
                '4 -week moving average was 197,750, a decrease of 1,000')
            add('계속 수급', '10월 10일 종료 주 계속 수급: 1,728,000명·전주 수정치 대비 +25,000명 (계절조정)',
                'week ending October 10 was 1,728,000, an increase of 25,000')
            add('계속 수급', '피보험 실업률: 1.1%·전주 미수정치 대비 변화 없음',
                'insured unemployment rate was 1.1 percent', "unchanged from the previous week's unrevised rate")
        elif event_id == 100:
            title = '퀄컴(QCOM) | 실적발표 일정'
            add('발표 일정', '2026회계연도 4분기·연간 실적: 미국 현지 2026년 11월 4일 장 마감 후 발표 예정',
                'fourth quarter and fiscal 2026', 'November 4, 2026, after the close of the market')
            add('발표 일정', '컨퍼런스콜: 2026년 11월 4일 오후 1:45 예정 (미국 태평양시간)',
                'November 4, 2026, beginning at 1:45 p.m. Pacific Time')
            add('다시 듣기', '콜 종료 후 오디오 리플레이 30일간 제공 예정', 'following the live call for 30 days thereafter')
        elif event_id == 101:
            title = '테슬라(TSLA) | 경영진 발언'
            add('설비투자 전망', '올해 설비투자 전망: 200억 달러로 조정 (CFO 발언)', 'Revising CAPEX Guidance To $20B This Year')
            add('에너지 사업', '매출총이익률: 20.4% → 33.1% 개선', 'Gross Margins Improved From 20.4% To 33.1%')
            add('에너지 사업', '주요 개선 요인: 관세 환급·보증 충당금 하향 조정으로 설명',
                'Primarily Attributed To Tariff Refunds And Warranty True Downs')
        elif event_id == 102:
            title = '미국·글로벌 증시 | 시장 동향'
            add('핵심내용', 'AI 관련주 약세·유가 상승이 글로벌 증시에 부담으로 작용했음',
                'Drops for stocks in the artificial-intelligence industry are dragging down markets worldwide', 'oil prices hit their highest levels since May')
            add('주요 지수·주가', 'S&P 500: -0.8%·나스닥: -1.5%·다우: -0.1% (기사 작성 시점)',
                'S&P 500 sank 0.8%', 'Nasdaq composite was down 1.5%', 'Dow Jones Industrial Average was down a modest 0.1%')
            add('주요 지수·주가', '아시아: 코스피 -3.5%·항셍 -3%·닛케이225 -2.7%',
                "Kospi tumbled 3.5%", "Hang Seng dropped 3%", "Nikkei 225 sank 2.7%")
            add('주요 지수·주가', '램리서치: -7.7%·실적 예상치 상회에도 연말 이익 가이던스 중간값은 예상치 소폭 하회',
                "Lam Research's tumbled 7.7%", 'slightly stronger than analysts expected', 'midpoint was slightly below')
            add('주요 지수·주가', '메타: -5.8%·매출 예상치 상회, 이익 예상치 하회',
                'lost 5.8%', 'stronger revenue', 'profit fell short')
            add('주요 지수·주가', '테슬라: +0.3%·초반 하락 후 반등, 이익 예상치 상회·매출 예상치 하회',
                'Tesla erased an early drop to rise 0.3%', 'revenue was weaker than forecast')
            add('주요 지수·주가', '엔비디아: -2.3%·코어위브: -3.8%', "Nvidia down 2.3%", 'It dropped 3.8%')
            add('유가·금리', '브렌트유: 배럴당 111.66달러·+2.6% 상승, 장중 5개월 최고 수준 도달',
                'Brent crude oil rose 2.6% to $111.66', 'highest level in five months')
            add('유가·금리', '미국 10년물 금리: 5.13% → 5.03% 하락·안전자산 매수 흐름 반영',
                '10-year Treasury fell to 5.03% from 5.13%', 'buying U.S. government bonds in search of something safer')
            add('주요 배경', 'AI 투자 자금 조달 비용 상승과 관련주 고평가 우려 지속',
                "expensive it's become to borrow money", 'prices simply shot too high')
            add('주요 배경', '프랑스 CAC40: -1%·국채 금리 상승 및 재정 부담 우려',
                "France's CAC 40", 'drop of 1%', 'increase spending', 'further to its debt')
            add('방어 요인', 'P&G·록히드마틴·CSX 실적 예상치 상회·주가 상승이 시장 낙폭 제한에 기여했음',
                'Procter & Gamble, Lockheed Martin and CSX', "gains for their stocks helped limit the market's losses")
            add('방어 요인', 'P&G: +5.4%·지정학·경제 난관 예상에도 연간 전망 유지',
                'rose 5.4%', 'stood by its forecasts')
        elif event_id == 104:
            title = '스태그웰(STGW)·팔란티어(PLTR) | 파트너십 확대'
            add('핵심내용', '기업 고객 대상 AI 마케팅 솔루션 개발을 위한 전략적 파트너십 확대 발표',
                'announced an expansion of their strategic partnership', 'AI-powered marketing solutions for enterprise clients')
            add('협력 구조', '2025년 11월 계약을 기반으로 팔란티어 AI 소프트웨어와 스태그웰 마케팅·에이전트 기술 결합 추진',
                'Building on their November 2025 agreement', "Palantir's advanced AI software", "Stagwell's marketing expertise and agentic technology")
            add('협력 구조', '스태그웰: 팔란티어의 주요 마케팅 전환 파트너 역할 수행 예정',
                'Stagwell will serve as a premier marketing transformation partner for Palantir')
            add('적용 분야', 'Machine OS·The Knowledge Machine·Audience Machine·The Media Machine에 팔란티어 기술 활용 예정',
                'Machine OS', 'The Knowledge Machine', 'audience machine', 'The Media Machine')
            add('적용 분야', '온톨로지·에이전트 프레임워크를 활용한 데이터·분석·실행 워크플로 연결 추진',
                "ontologies and agent framework", 'connected workflows, intelligence and activation')
            add('데이터 통제', '고객의 자체 데이터·기술 통제권 유지 및 기존 시스템과의 상호운용성 강조',
                'enterprises maintain control over their proprietary data', 'interoperability, flexibility')
            add('경영진 발언', '양사 역량 결합을 통한 기업 마케팅 전환과 AI 도입 가속 기대 (마크 펜)',
                'said Mark Penn', 'accelerate AI adoption at scale')
            add('경영진 발언', '고객별 독자 모델의 소유·배포가 시장 접근 방식을 바꿀 것으로 설명 (알렉스 카프)',
                'Alex Karp', 'sovereign models, designed individually for each customer')
        elif event_id == 105:
            title = '에르메스 | 중국 명품 소비 동향'
            add('핵심내용', '분기 매출 예상치 하회·중국 수요 정체로 주가 약 -6% 하락 보도',
                'plunged around 6%', 'quarterly sales missed expectations as Chinese demand stalled')
            add('사업 현황', '디자이너 의류 매출 감소 전환 보도', 'designer clothing slipped into decline')
        elif event_id == 106:
            title = 'P&G·존슨앤드존슨·코카콜라 | 실적·주가 동향'
            add('핵심내용', 'P&G 실적 예상치 상회·방어적 소비재·헬스케어 종목으로 자금 유입 흐름',
                'strong earnings beat', 'Defensive consumer and healthcare stocks are drawing fresh money')
            add('P&G 주요 실적', '2027회계연도 1분기 매출: 232.8억 달러·예상치 228.2억 달러 상회',
                'Q1 FY2027', '$23.28 billion', '$22.82 billion')
            add('P&G 주요 실적', '핵심 EPS: 2달러·예상치 1.89달러 상회',
                'core earnings per share (EPS) of $2', '$1.89 consensus estimate')
            add('P&G 주요 실적', '유기적 판매량: +2% 증가', 'Organic volume at Procter & Gamble rose 2%')
            add('P&G 주요 실적', '핵심 매출총이익률: 전년 동기 대비 -150bp', 'core gross margin fell 150 basis points year over year')
            add('주가 반응', 'P&G: +6%·166.19달러 (기사 작성 시점)', 'up 6% to $166.19')
            add('주가 반응', '존슨앤드존슨: +3%·264.89달러', 'up 3% to $264.89')
            add('주가 반응', '코카콜라: +2%·94.56달러', 'up 2% to $94.56')
            add('향후 전망', '연간 유기적 매출 성장률 +1~3%·핵심 EPS 6.89~7.11달러 전망 유지',
                'full-year outlook', 'organic sales growth of 1% to 3%', '$6.89 to $7.11')
            add('향후 전망', '상반기 유기적 매출 +3%·하반기 성장세 소폭 가속 예상 (경영진)',
                'first-half organic sales growth of 3% with slight acceleration in the second half')
            add('확인할 변수', '회계연도 세후 원자재 비용 부담: 14억 달러 예상·저소득층 소비자의 가격 인상 수용 여부가 관건',
                '$1.4 billion after-tax commodity cost pressure', 'lower-income shoppers keep absorbing higher prices')
            add('주주환원', '회계연도 배당 100억 달러 초과·자사주 매입 약 50억 달러 계획',
                'more than $10 billion in dividends and approximately $5 billion in buybacks planned this fiscal year')
        elif event_id == 107:
            # Reuse exactly the reviewed style example already sent to test.
            text = (ROOT / 'data/nominal-style-preview/netflix.html').read_text(encoding='utf-8')
            cards.append({'event_id': event_id, 'message_id': row['telegram_message_id'], 'text': text})
            (FOLDER / f'event-{event_id}.html').write_text(text, encoding='utf-8')
            continue
        else:
            title = '세일즈포스(CRM) | 브레이즈 인수 관련 보도'
            add('핵심내용', '브레이즈(Braze) 인수 계획 발표·실시간 AI 마케팅 역량 강화 추진',
                'Salesforce plans to acquire Braze', 'AI-driven marketing capabilities')
            add('거래 조건', '거래 기업가치: 약 56.6억 달러·주당 현금 47.50달러 지급 예정',
                '$5.66 billion in enterprise value', '$47.50 in cash per share')
            add('거래 조건', '종결 시점: 세일즈포스 2028회계연도 예상·주주 및 규제 승인 등 조건 충족 필요',
                'fiscal 2028', 'Braze shareholder approval, regulatory review')
            add('거래 조건', '인수 자금: 전액 현금 조달 예상·현재 차입 필요성은 없다는 설명',
                'fund the acquisition with cash', 'does not currently expect to require debt financing')
            add('거래 평가', '인수가격: 브레이즈 2028회계연도 예상 매출의 약 5배·세일즈포스 기업가치의 약 2.5% (경영진 설명)',
                'five times Braze', '2.5% of Salesforce')
            add('거래 평가', '인수금액: 세일즈포스 2027회계연도 예상 잉여현금흐름 약 4개월분에 해당 (경영진 설명)',
                'roughly four months', 'fiscal 2027 annual free cash flow')
            add('사업 결합', '모바일·웹·이메일·SMS·WhatsApp 고객 접점의 실시간 맞춤형 마케팅 강화 추진',
                'mobile apps, the web, email, SMS, WhatsApp')
            add('사업 결합', 'Data 360·Customer 360·Agentforce·Slack과 브레이즈의 실시간 참여·AI 최적화 기술 결합 기대',
                'Data 360, Customer 360, Agentforce and Slack', 'AI optimization capabilities')
            add('사업 결합', '브레이즈 고객사: 약 2,700개·브레이즈 공동창업자·경영진 합류 및 기존 CEO 유지 예상',
                'approximately 2,700 customers', 'Both Braze founders', 'broader leadership team', 'continue as CEO of Braze within Salesforce')
            add('향후 전망', '매출 성장 기여 기대·단기 수익성 희석 예상',
                'growth accretion while creating near-term profitability dilution')
            add('향후 전망', '종결 후 운영 효율 개선 예상·1년간 브레이즈 매출 기여도 공개 계획',
                'operating efficiencies following the close', 'revenue contribution for one year')
            add('향후 전망', '세일즈포스 2027회계연도 가이던스: 변경 없음',
                'making no change to its fiscal 2027 guidance')
        text = format_message(title, sections, row['source_url'])
        if len(text.encode('utf-16-le')) // 2 > 4096 or re.search(r'습니다[.!]?|말했다[.!]?', text):
            raise ValueError('Invalid outline card')
        cards.append({'event_id': event_id, 'message_id': row['telegram_message_id'], 'text': text})
        (FOLDER / f'event-{event_id}.html').write_text(text, encoding='utf-8')
    (FOLDER / 'evidence.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    (FOLDER / 'prepared.json').write_text(json.dumps({'target': config.telegram_chat_id, 'messages': cards},
                                                     ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'prepared': len(cards), 'message_ids': [item['message_id'] for item in cards]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edit', action='store_true')
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    if args.edit:
        editor.OUTPUT = FOLDER
        editor.MANIFEST = FOLDER / 'prepared.json'
        editor.RECEIPTS = FOLDER / 'receipts.jsonl'
        editor.edit(config)
    else:
        prepare(config)


if __name__ == '__main__':
    main()
