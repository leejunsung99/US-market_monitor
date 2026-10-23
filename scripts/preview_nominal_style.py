"""Send one reviewed Korean outline-style example to the test channel."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from send_recent_compact import format_message  # noqa: E402

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402


def main():
    config = Config.load(root=ROOT)
    target = os.getenv('TELEGRAM_TEST_CHAT_ID', '').strip()
    if not target or target == config.telegram_chat_id:
        raise ValueError('Separate test channel required')
    repo = Repository(config.data_dir / 'radar.sqlite3', readonly=True)
    try:
        event = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=107').fetchone())
    finally:
        repo.close()
    source = event.body or event.article_summary
    claims = [
        ('핵심내용', '2027년에도 두 자릿수 매출 성장 가능성 제시·회원 수·가격·광고를 성장 동력으로 설명 (CFO)',
         'membership gains, pricing and advertising could sustain double-digit revenue growth into 2027'),
        ('경영진 발언', '시청 시간 증가세는 경영진 기대에 미달했음 (CFO)',
         'viewing hours have not increased as rapidly as management would prefer'),
        ('경영진 발언', '시청 참여도 개선 시 기존 매출 성장에 추가 기여 예상 (CFO)',
         "stronger engagement would add to Netflix's existing revenue momentum"),
        ('사업 현황', '인도 매출 증가율: 약 +20%·강력한 콘텐츠 라인업 효과로 설명 (CFO)',
         'a strong content lineup helped generate nearly 20% revenue growth'),
        ('향후 전망', '2027년 연간 가이던스: 아직 미발표',
         'Netflix has not yet released full-year 2027 guidance'),
        ('향후 전망', '2027년 전망: 4분기 실적 발표 시 제시 예상 (CFO)',
         'expects to provide that outlook with its fourth-quarter results'),
        ('향후 전망', '중장기 목표: 두 자릿수 매출 성장·마진 확대·잉여현금흐름 증가',
         'goals for "double-digit" revenue growth, margin expansion and higher free cash flow'),
        ('외부 평가·우려', '비디오 팟캐스트·유튜브 크리에이터 영입의 향후 성장 기여 가능성 언급 (리치 그린필드)',
         "Greenfield said Netflix's push into video podcasts and efforts to recruit top YouTube creators could drive growth next year"),
        ('외부 평가·우려', '부채 기반 콘텐츠 투자 전략의 지속 가능성에 의문 제기 (차마스 팔리하피티야)',
         "He questioned whether Netflix's debt-funded content strategy reflects a mismatch between assets and liabilities"),
    ]
    sections = {}
    for category, text, evidence in claims:
        if evidence not in source:
            raise ValueError('Missing source evidence')
        sections.setdefault(category, []).append(text)
    message = format_message('넷플릭스(NFLX) | 산업·시장 동향', sections, event.source_url)
    if len(message.encode('utf-16-le')) // 2 > 4096:
        raise ValueError('Example too long')
    output = ROOT / 'data/nominal-style-preview'
    output.mkdir(exist_ok=True)
    receipt_file = output / 'receipt.json'
    if receipt_file.exists():
        raise ValueError('Example already sent')
    (output / 'netflix.html').write_text(message, encoding='utf-8')
    (output / 'evidence.json').write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding='utf-8')
    message_id = Telegram(config.telegram_token, target).send(message)
    receipt = {'event_id': 107, 'message_id': message_id, 'target': 'test', 'style': 'nominal-example'}
    receipt_file.write_text(json.dumps(receipt), encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
