"""Repair the requested evening-to-morning messages in place, with durable receipts."""

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.models import Event  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.article import article_text  # noqa: E402
from us_market_monitor.notifications.compact import format_compact_event  # noqa: E402
from us_market_monitor.processors.briefing import prepare_brief, translation_fingerprint  # noqa: E402
from us_market_monitor.processors.translator import Translator  # noqa: E402

OUTPUT = ROOT / 'data/evening-revision-20261023'
MANIFEST = OUTPUT / 'prepared.json'
RECEIPTS = OUTPUT / 'receipts.jsonl'


def review_prepared():
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for item in manifest['messages']:
        record = dict(item['event'])
        record['published_at'] = datetime.fromisoformat(record['published_at'])
        record['detected_at'] = datetime.fromisoformat(record['detected_at'])
        event = Event(**record)
        if item['event_id'] == 100:
            assert 'November 4, 2026' in event.body and '1:45 p.m.' in event.body
            event.summary_ko = ('2026회계연도 4분기 및 연간 실적 발표 예정: 미국 현지 2026년 11월 4일 장 마감 후\n'
                                '컨퍼런스콜 예정: 2026년 11월 4일 오후 1:45 (미국 태평양시간)\n'
                                '콜 종료 후 오디오 다시 듣기 제공: 30일간')
        if item['event_id'] == 101:
            assert all(value in event.title for value in ['$20B', '20.4%', '33.1%', 'Tariff Refunds', 'Warranty True Downs'])
            event.metadata['issuer_ticker'] = 'TSLA'
            first, second = event.title.split('; ', 1)
            event.article_summary = first + '.\n' + second + '.'
            event.summary_ko = ('올해 설비투자 전망: 200억 달러로 조정 (CFO 발언)\n'
                                '에너지 매출총이익률: 20.4% → 33.1% 개선·주요 요인은 관세 환급 및 보증 충당금 하향 조정')
            event.title_ko = '테슬라 CFO, 설비투자 전망 및 에너지 매출총이익률 설명'
            event.translation_hash = translation_fingerprint(event)
        item['event'] = asdict(event)
        item['event']['published_at'] = event.published_at.isoformat()
        item['event']['detected_at'] = event.detected_at.isoformat()
        item['text'] = format_compact_event(event)
        if '??' in item['text'] or len(item['text'].encode('utf-16-le')) // 2 > 4096:
            raise ValueError('Invalid reviewed card')
        (OUTPUT / f"event-{item['event_id']}.html").write_text(item['text'], encoding='utf-8')
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'reviewed': len(manifest['messages'])}))


def prepare(config):
    if MANIFEST.exists():
        raise RuntimeError('Prepared manifest already exists; inspect before replacing')
    OUTPUT.mkdir(exist_ok=True)
    repo = Repository(config.data_dir / 'radar.sqlite3', readonly=True)
    until = datetime.now(UTC).isoformat()
    try:
        rows = repo.db.execute(
            "SELECT e.*,n.telegram_message_id,n.sent_at FROM events e JOIN notifications n "
            "ON n.event_id=e.id WHERE n.status='sent' AND n.sent_at>=? AND n.sent_at<=? "
            "ORDER BY n.sent_at", ('2026-10-22T09:00:00+00:00', until),
        ).fetchall()
        originals = [dict(row) for row in rows]
        (OUTPUT / 'original-rows.json').write_text(json.dumps(originals, ensure_ascii=False, indent=2),
                                                  encoding='utf-8')
        prepared = []
        translator = Translator(config)
        for row in rows:
            event = repo.row_event(row)
            if row['id'] == 100:
                response = requests.get(event.source_url, timeout=(10, 30))
                response.raise_for_status()
                source = article_text(BeautifulSoup(response.text, 'html.parser'))
                if not source:
                    raise ValueError('Missing Qualcomm source')
                prepare_brief(event, source)
            elif event.event_type not in {'economic_indicator', 'monitor_alert'}:
                prepare_brief(event, event.body or event.article_summary or event.summary)
            if event.event_type not in {'economic_indicator', 'monitor_alert'}:
                translator.disabled_until = 0
                if not translator.translate(event):
                    raise ValueError('Translation validation failed for event ' + str(row['id']))
            message = format_compact_event(event)
            if len(message.encode('utf-16-le')) // 2 > 4096:
                raise ValueError('Edited message would exceed text limit: ' + str(row['id']))
            if not re.search('[가-힣]', message):
                raise ValueError('Missing Korean output')
            (OUTPUT / f"event-{row['id']}.html").write_text(message, encoding='utf-8')
            record = asdict(event)
            record['published_at'] = event.published_at.isoformat()
            record['detected_at'] = event.detected_at.isoformat()
            prepared.append({'event_id': row['id'], 'message_id': row['telegram_message_id'],
                             'event': record, 'text': message})
            print(json.dumps({'prepared': row['id'], 'message_id': row['telegram_message_id'],
                              'chars': len(message), 'translated': bool(event.summary_ko)}), flush=True)
        MANIFEST.write_text(json.dumps({'since_kst': '2026-10-22T18:00:00+09:00', 'until': until,
                                       'target': config.telegram_chat_id, 'messages': prepared},
                                      ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        repo.close()


def edit(config):
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if manifest['target'] != config.telegram_chat_id:
        raise ValueError('Main channel changed')
    completed = set()
    if RECEIPTS.exists():
        completed = {json.loads(line)['event_id'] for line in RECEIPTS.read_text(encoding='utf-8').splitlines()}
    repo = Repository(config.data_dir / 'radar.sqlite3')
    try:
        for item in manifest['messages']:
            if item['event_id'] in completed:
                continue
            mode = 'editMessageText'
            for _ in range(2):
                payload = {'chat_id': config.telegram_chat_id, 'message_id': item['message_id'],
                           'parse_mode': 'HTML'}
                if mode == 'editMessageText':
                    payload.update(text=item['text'], link_preview_options={'is_disabled': True})
                else:
                    if len(item['text'].encode('utf-16-le')) // 2 > 1024:
                        raise ValueError('Existing photo caption needs a separate migration')
                    payload['caption'] = item['text']
                try:
                    response = requests.post(f'https://api.telegram.org/bot{config.telegram_token}/{mode}',
                                             json=payload, timeout=(20, 30))
                    result = response.json()
                except (requests.RequestException, ValueError):
                    raise RuntimeError('Edit outcome unknown for event ' + str(item['event_id'])) from None
                description = result.get('description', '').lower()
                if result.get('ok') or 'message is not modified' in description:
                    break
                if (mode == 'editMessageText' and result.get('error_code') == 400
                        and 'no text' in description):
                    mode = 'editMessageCaption'
                    continue
                raise RuntimeError('Edit rejected for event ' + str(item['event_id'])
                                   + ': ' + str(result.get('error_code')))
            else:
                raise RuntimeError('Edit not confirmed')
            record = dict(item['event'])
            record['published_at'] = datetime.fromisoformat(record['published_at'])
            record['detected_at'] = datetime.fromisoformat(record['detected_at'])
            with repo.db:
                repo.update_article(item['event_id'], Event(**record))
            receipt = {'event_id': item['event_id'], 'message_id': item['message_id'], 'mode': mode,
                       'target': 'main', 'edited_at': datetime.now(UTC).isoformat()}
            with RECEIPTS.open('a', encoding='utf-8') as log:
                log.write(json.dumps(receipt) + '\n')
            print(json.dumps(receipt), flush=True)
            time.sleep(3.2)
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--edit', action='store_true')
    parser.add_argument('--review-prepared', action='store_true')
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    if args.review_prepared:
        review_prepared()
    elif args.edit:
        edit(config)
    else:
        prepare(config)


if __name__ == '__main__':
    main()
