"""One-off user-requested replacement of the pre-rollout Lam article alert."""

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from send_recent_compact import format_message  # noqa: E402

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402


def main():
    config = Config.load(root=ROOT)
    repo = Repository(config.data_dir / 'radar.sqlite3', readonly=True)
    try:
        row = repo.db.execute(
            "SELECT e.*, n.telegram_message_id FROM events e JOIN notifications n "
            "ON n.event_id=e.id WHERE e.id=96 AND n.status='sent'"
        ).fetchone()
        event = repo.row_event(row)
        assert row['telegram_message_id'] == 7
        for evidence in ['in-line Q1 FY2027', '54.4% sales growth', 'fell 6.4% after hours']:
            assert evidence in event.article_summary
        message = format_message('램리서치(LRCX) | 실적 관련 보도', {
            '핵심내용': ['2027회계연도 1분기 실적, 시장 예상에 부합'],
            '주요 실적': ['매출 성장률: +54.4%'],
            '주가 반응': ['시간외 주가: -6.4%', '실적 발표 후 주가 하락 보도'],
        }, event.source_url)
    finally:
        repo.close()
    output = ROOT / 'data/recent-compact'
    receipt_path = output / 'lam-replacement.json'
    if receipt_path.exists():
        raise RuntimeError('Replacement receipt exists; do not resend')
    (output / 'event-96-replacement.html').write_text(message, encoding='utf-8')
    try:
        response = requests.post(
            f'https://api.telegram.org/bot{config.telegram_token}/deleteMessage',
            json={'chat_id': config.telegram_chat_id, 'message_id': 7}, timeout=(20, 20),
        )
        payload = response.json()
    except (requests.RequestException, ValueError):
        raise RuntimeError('Deletion outcome unknown') from None
    if payload.get('ok') is not True or payload.get('result') is not True:
        raise RuntimeError('Deletion rejected: ' + str(payload.get('error_code')))
    print(json.dumps({'deleted_message_id': 7}), flush=True)
    new_id = Telegram(config.telegram_token, config.telegram_chat_id).send(message)
    receipt = {'event_id': 96, 'deleted_message_id': 7, 'message_id': new_id, 'target': 'main'}
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
