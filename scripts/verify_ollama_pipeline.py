"""Exercise the production background scheduler against a test-only database/channel."""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402
from us_market_monitor.processors.classifier import Classifier  # noqa: E402
from us_market_monitor.processors.ollama import OllamaProcessor  # noqa: E402
from us_market_monitor.scheduler.jobs import Radar  # noqa: E402


def main():
    config = Config.load(root=ROOT)
    target = os.getenv('TELEGRAM_TEST_CHAT_ID', '')
    if not target or target == config.telegram_chat_id:
        raise ValueError('Separate test channel required')
    output = ROOT / 'data/ollama-production'
    receipt = output / 'scheduler-test-receipt.json'
    if receipt.exists():
        raise ValueError('Integration test already sent')
    database = output / 'scheduler-test.sqlite3'
    if database.exists():
        raise ValueError('Inspect previous integration database before retry')
    live = sqlite3.connect('file:' + str(config.data_dir / 'radar.sqlite3') + '?mode=ro', uri=True)
    copy = sqlite3.connect(database)
    live.backup(copy)
    copy.close()
    live.close()
    repo = Repository(database)
    event = repo.row_event(repo.db.execute('SELECT * FROM events WHERE id=98').fetchone())
    # The validated artifact was produced by the actual GPU model, not a mock.
    event.metadata['ollama_summary'] = json.loads((output / 'event-98.json').read_text(encoding='utf-8'))
    with repo.db:
        repo.update_article(98, event)
        repo.db.execute("UPDATE notifications SET status='expired' WHERE status='pending'")
        repo.db.execute("UPDATE notifications SET status='pending',next_attempt_at='2000-01-01',retry_count=0 WHERE event_id=98")
    radar = Radar([], repo, Classifier(config.watchlist, config.filters),
                  translator=OllamaProcessor(config), telegram=Telegram(config.telegram_token, target),
                  max_notification_age=168)
    started = time.monotonic()
    try:
        while time.monotonic() - started < 120:
            radar.dispatch()
            row = repo.db.execute('SELECT * FROM notifications WHERE event_id=98').fetchone()
            if row['status'] == 'sent':
                result = {'event_id': 98, 'message_id': row['telegram_message_id'], 'target': 'test',
                          'backend': 'ollama', 'scheduler': 'production_background_dispatch'}
                receipt.write_text(json.dumps(result), encoding='utf-8')
                print(json.dumps(result), flush=True)
                return
            if row['retry_count']:
                raise RuntimeError('Production pipeline held: ' + row['error_message'])
            time.sleep(0.25)
        raise RuntimeError('Integration test deadline exceeded')
    finally:
        if radar.summary_executor:
            radar.summary_executor.shutdown(wait=True)
        repo.close()


if __name__ == '__main__':
    main()
