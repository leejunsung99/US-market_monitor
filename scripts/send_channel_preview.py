"""Preview a retained article; send only with an explicit target and --send."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("main", "test"), required=True)
    parser.add_argument("--event-id", type=int)
    parser.add_argument("--send", action="store_true")
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    chat_id = (
        config.telegram_chat_id
        if args.target == "main"
        else os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    )
    if not chat_id:
        parser.error("Selected channel is not configured")
    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    try:
        if args.event_id:
            row = repo.db.execute("SELECT * FROM events WHERE id=?", (args.event_id,)).fetchone()
        else:
            row = repo.db.execute(
                "SELECT * FROM events WHERE title_ko<>'' "
                "AND event_type NOT IN ('monitor_alert','economic_indicator') "
                "ORDER BY published_at DESC,id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            parser.error("No article found")
        event = repo.row_event(row)
        result = {"target": args.target, "event_id": row["id"], "title": event.title}
        if args.send:
            # Manual samples do not alter production notification/deduplication records.
            result["message_id"] = Telegram(config.telegram_token, chat_id).send_article(
                event, test=args.target == "test"
            )
        print(json.dumps(result, ensure_ascii=True))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
