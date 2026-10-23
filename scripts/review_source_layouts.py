"""Audit all retained events and render experimental layouts; send only to test."""

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from us_market_monitor.config import Config  # noqa: E402
from us_market_monitor.database.repository import Repository  # noqa: E402
from us_market_monitor.notifications.preview_layout import (  # noqa: E402
    build_card,
    render_card,
)
from us_market_monitor.notifications.telegram import Telegram  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-ids", type=int, nargs="+", default=[])
    parser.add_argument("--translate-ids", type=int, nargs="+", default=[])
    args = parser.parse_args()
    config = Config.load(root=ROOT)
    target = os.getenv("TELEGRAM_TEST_CHAT_ID", "").strip()
    if args.send_ids and (not target or target == config.telegram_chat_id):
        parser.error("A separate test channel is required")
    output = ROOT / "data" / "source-layout-review"
    output.mkdir(exist_ok=True)
    cache_path = output / "translations.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def translate(text):
        # Historical layout audit is offline. New Korean summaries are generated
        # through the production OllamaProcessor, never through a cloud service.
        return cache.get(text, text)

    repo = Repository(config.data_dir / "radar.sqlite3", readonly=True)
    telegram = Telegram(config.telegram_token, target) if args.send_ids else None
    counts, families, samples, sent = Counter(), Counter(), [], []
    found = set()
    try:
        rows = repo.db.execute("SELECT * FROM events ORDER BY id").fetchall()
        for row in rows:
            event = repo.row_event(row)
            card = build_card(event, row["id"], config.watchlist["companies"])
            counts[event.source] += 1
            families[card.family] += 1
            samples.append(asdict(card))
            # Offline audit does not spend translation quota on every retained article.
            translated_ids = set(args.send_ids) | set(args.translate_ids)
            message = render_card(card, translate if row["id"] in translated_ids else lambda t: t)
            (output / f"event-{row['id']}.html").write_text(message, encoding="utf-8")
            if row["id"] in args.send_ids:
                found.add(row["id"])
                if len(message) > 4096:
                    raise ValueError("Preview exceeds Telegram limit")
                message_id = telegram.send(message)
                receipt = {"event_id": row["id"], "message_id": message_id, "source": event.source}
                sent.append(receipt)
                # Persist each confirmed delivery immediately; do not automatically retry.
                with (output / "receipts.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(receipt) + "\n")
                print(json.dumps(receipt), flush=True)
        report = {
            "scope": "Retained approved events only; rejected raw articles are not retained",
            "event_count": len(rows), "sources": dict(counts), "families": dict(families),
            "omitted_fragments": sum(c["omitted"] for c in samples),
            "empty_cards": [c["event_id"] for c in samples if not c["sections"]],
            "samples": samples,
        }
        (output / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if set(args.send_ids) - found:
            raise ValueError("Requested event IDs not found")
        print(json.dumps({"events": len(rows), "sources": dict(counts),
                          "families": dict(families), "sent": sent}, ensure_ascii=True))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
