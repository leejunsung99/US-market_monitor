"""Collect a KST day's important events into a separate DB, then explicitly replay up to 5."""

import argparse
import json
import signal
import sys
import threading
from datetime import datetime, time, timedelta
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from us_market_monitor.config import Config
from us_market_monitor.database.models import utcnow
from us_market_monitor.database.repository import Repository
from us_market_monitor.http import FetchError, Stopped
from us_market_monitor.main import build_collectors, setup_logging
from us_market_monitor.notifications.article import enrich_article
from us_market_monitor.notifications.formatter import format_event
from us_market_monitor.notifications.telegram import DeliveryError, Telegram
from us_market_monitor.processors.classifier import Classifier
from us_market_monitor.processors.normalizer import parse_time
from us_market_monitor.processors.translator import Translator

KST = ZoneInfo("Asia/Seoul")


def day_window(date, end):
    start = datetime.combine(date, time.min, KST).astimezone(end.tzinfo)
    return start, min(end, start + timedelta(days=1))


def selected_rows(repo, limit):
    return repo.db.execute(
        "SELECT e.*,n.retry_count FROM events e JOIN notifications n ON n.event_id=e.id "
        "WHERE n.status='pending' ORDER BY e.published_at,e.id LIMIT ?",
        (limit,),
    ).fetchall()


def revalidate_saved_news(repo, classifier):
    rejected = []
    rows = repo.db.execute(
        "SELECT e.* FROM events e JOIN notifications n ON n.event_id=e.id "
        "WHERE n.status='pending' AND e.source LIKE 'Finnhub/%'"
    ).fetchall()
    for row in rows:
        if classifier.classify(repo.row_event(row)) is not None:
            continue
        with repo.db:
            repo.db.execute("DELETE FROM event_identity WHERE event_id=?", (row["id"],))
            repo.db.execute("DELETE FROM notifications WHERE event_id=?", (row["id"],))
            repo.db.execute("DELETE FROM events WHERE id=?", (row["id"],))
        rejected.append(row["id"])
    print(
        "REVALIDATED "
        + json.dumps(
            {
                "rejected_unconfirmed": rejected,
                "important_remaining": repo.status()["important_events"],
            }
        ),
        flush=True,
    )


def collect(config, repo, date, stop):
    end = utcnow()
    start, end = day_window(date, end)
    if start > end:
        raise ValueError("Requested day is in the future")
    repo.db.execute("CREATE TABLE IF NOT EXISTS replay_window(start_at TEXT,end_at TEXT)")
    if repo.db.execute("SELECT 1 FROM replay_window").fetchone():
        raise ValueError("This database already has a collection window; use a new database")
    repo.db.execute("INSERT INTO replay_window VALUES(?,?)", (start.isoformat(), end.isoformat()))
    repo.db.commit()
    classifier = Classifier(config.watchlist, config.filters)
    collectors = build_collectors(config, stop)
    counts = {
        "fetched": 0,
        "in_window": 0,
        "important_stored": 0,
        "sources_ok": 0,
        "sources_failed": [],
    }
    print(
        "WINDOW "
        + json.dumps(
            {
                "start_kst": start.astimezone(KST).isoformat(),
                "end_kst": end.astimezone(KST).isoformat(),
            }
        ),
        flush=True,
    )
    for collector in collectors:
        # Shared clients stay within the configured rate ceiling, with additional spacing.
        collector.client.spacing = max(
            collector.client.spacing, 1 if collector.name.startswith("sec:") else 3
        )
        repo.ensure_source(collector.name, start)
        repo.checked(collector.name)
        try:
            events = collector.collect(start, {})
            counts["fetched"] += len(events)
            for event in sorted(events, key=lambda item: item.published_at):
                classifier.prepare(event)
                if not start <= event.published_at <= end or repo.seen(event):
                    continue
                counts["in_window"] += 1
                approved = classifier.classify(event)
                if approved:
                    counts["important_stored"] += bool(repo.ingest(approved, config.filters))
                else:
                    repo.discard(event)
            repo.success(
                collector.name, {"baseline_at": start.isoformat(), "end_at": end.isoformat()}
            )
            counts["sources_ok"] += 1
            print(
                f"COLLECT {collector.name} fetched={len(events)} stored={counts['important_stored']}",
                flush=True,
            )
        except Stopped:
            raise
        except Exception as error:
            repo.failure(collector.name)
            code = str(error) if isinstance(error, FetchError) else type(error).__name__
            counts["sources_failed"].append({"source": collector.name, "error": code})
            print(f"COLLECT_FAILED {collector.name} {code}", flush=True)
            if isinstance(error, FetchError) and error.retry_after:
                if stop.wait(error.retry_after):
                    raise Stopped() from None
        if stop.wait(1):
            raise Stopped()
    print("COLLECTION_RESULT " + json.dumps(counts), flush=True)
    for row in repo.db.execute("SELECT * FROM events ORDER BY published_at,id"):
        print(
            "STORED "
            + json.dumps(
                {
                    "id": row["id"],
                    "title": row["title"],
                    "source": row["source"],
                    "priority": row["priority"],
                    "type": row["event_type"],
                    "published_kst": parse_time(row["published_at"]).astimezone(KST).isoformat(),
                    "url": row["source_url"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def send(config, repo, limit, spacing, stop):
    if not config.telegram_enabled:
        raise ValueError("Telegram credentials are missing")
    window = repo.db.execute("SELECT * FROM replay_window").fetchone()
    if window is None:
        raise ValueError("Collect and save a day window before sending")
    revalidate_saved_news(repo, Classifier(config.watchlist, config.filters))
    repo.recover_inflight()
    attempted = repo.db.execute(
        "SELECT count(*) FROM notifications WHERE status IN ('sent','unknown')"
    ).fetchone()[0]
    rows = selected_rows(repo, max(0, limit - attempted))
    telegram = Telegram(
        config.telegram_token, config.telegram_chat_id, timeout=config.timeout, stop=stop
    )
    sent = 0
    translator = Translator(config) if getattr(config, "translator_key", "") else None
    for index, row in enumerate(rows, 1):
        if stop.is_set() or not repo.claim(row["id"]):
            break
        text = (
            f"🧪 <b>오늘 뉴스 재생 테스트 [{attempted + index}/{limit}]</b>\n"
            f"{escape(parse_time(window['start_at']).astimezone(KST).strftime('%m/%d %H:%M'))}"
            f" ~ {escape(parse_time(window['end_at']).astimezone(KST).strftime('%H:%M'))} KST\n"
            "저장된 실제 기사입니다. 실시간 속보가 아닙니다.\n\n"
            + format_event(repo.row_event(row))
        )
        try:
            if hasattr(telegram, "send_article"):
                event = enrich_article(repo.row_event(row), stop=stop)
                if translator:
                    translator.translate(event)
                with repo.db:
                    repo.update_article(row["id"], event)
                message_id = telegram.send_article(event, test=True)
            else:
                message_id = telegram.send(text)
        except DeliveryError as error:
            repo.delivery_failure(row["id"], error, row["retry_count"])
            print(
                "SEND_FAILED " + json.dumps({"event_id": row["id"], "error": str(error)}),
                flush=True,
            )
            break
        else:
            repo.sent(row["id"], message_id)
            sent += 1
            print(
                "SENT "
                + json.dumps(
                    {
                        "event_id": row["id"],
                        "message_id": message_id,
                        "title": row["title"],
                        "sent_at_kst": utcnow().astimezone(KST).isoformat(),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if index < len(rows) and stop.wait(spacing):
            break
    print(
        "SEND_RESULT "
        + json.dumps(
            {"sent_now": sent, "total_limit": limit, "status": repo.status()["notifications"]}
        ),
        flush=True,
    )


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=lambda value: datetime.strptime(value, "%Y-%m-%d").date(),
        default=utcnow().astimezone(KST).date(),
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--send", action="store_true", help="Send from an already collected database"
    )
    parser.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    parser.add_argument("--spacing", type=float, default=10)
    args = parser.parse_args()
    if args.spacing < 3.1:
        parser.error("Send spacing must be at least 3.1 seconds")
    config = Config.load()
    setup_logging(config)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(args.database) + ".lock", timeout=0):
        repo = Repository(args.database)
        try:
            if args.send:
                send(config, repo, args.limit, args.spacing, stop)
            else:
                collect(config, repo, args.date, stop)
        finally:
            repo.close()


if __name__ == "__main__":
    main()
