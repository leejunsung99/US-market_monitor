import json
import sqlite3
from datetime import timedelta
from pathlib import Path

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.processors.deduplicator import (
    identities,
    material_facts,
    meaningful_update,
    same_story,
)
from us_market_monitor.processors.normalizer import digest, parse_time

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL, source_event_id TEXT NOT NULL, title TEXT NOT NULL,
  summary TEXT NOT NULL, event_type TEXT NOT NULL,
  priority TEXT NOT NULL CHECK(priority IN ('CRITICAL','HIGH')),
  related_tickers TEXT NOT NULL, source_url TEXT NOT NULL,
  published_at TEXT NOT NULL, detected_at TEXT NOT NULL, created_at TEXT NOT NULL,
  entity_key TEXT NOT NULL, industry TEXT NOT NULL, reason TEXT NOT NULL,
  material_facts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_story ON events(event_type, published_at);
CREATE TABLE IF NOT EXISTS notifications (
  event_id INTEGER PRIMARY KEY REFERENCES events(id),
  telegram_message_id INTEGER,
  status TEXT NOT NULL CHECK(status IN
    ('pending','sending','sent','failed','unknown','expired')),
  sent_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0, error_message TEXT,
  next_attempt_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notification_queue ON notifications(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS collector_state (
  source TEXT PRIMARY KEY, last_cursor TEXT NOT NULL,
  last_checked_at TEXT, last_success_at TEXT, error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS event_identity (
  identity_hash TEXT PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id)
);
CREATE TABLE IF NOT EXISTS seen (
  observation_hash TEXT PRIMARY KEY, observed_at TEXT NOT NULL
);
"""


class Repository:
    def __init__(self, path, *, readonly=False):
        target = Path(path).resolve().as_uri() + "?mode=ro" if readonly else str(path)
        self.db = sqlite3.connect(target, timeout=30, uri=readonly)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=30000")
        if readonly:
            self.db.execute("PRAGMA query_only=ON")
        else:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def ensure_source(self, source, started_at=None):
        started_at = started_at or utcnow()
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO collector_state(source,last_cursor) VALUES(?,?)",
                (source, json.dumps({"baseline_at": started_at.isoformat()})),
            )
        return self.state(source)

    def state(self, source):
        row = self.db.execute("SELECT * FROM collector_state WHERE source=?", (source,)).fetchone()
        return dict(row) if row else None

    def checked(self, source):
        with self.db:
            self.db.execute(
                "UPDATE collector_state SET last_checked_at=? WHERE source=?",
                (utcnow().isoformat(), source),
            )

    def success(self, source, cursor):
        with self.db:
            self.db.execute(
                "UPDATE collector_state SET last_cursor=?,last_success_at=?,"
                "error_count=0 WHERE source=?",
                (json.dumps(cursor), utcnow().isoformat(), source),
            )

    def failure(self, source):
        with self.db:
            self.db.execute(
                "UPDATE collector_state SET error_count=error_count+1 WHERE source=?", (source,)
            )

    @staticmethod
    def observation_key(event):
        # Only this hash is persisted for discarded ordinary news.
        if not event.observation_hash:
            event.observation_hash = digest(
                f"{event.source}:{event.source_event_id}:{event.source_url}:"
                f"{event.title}:{event.summary}:{event.body}"
            )
        return event.observation_hash

    def seen(self, event):
        return (
            self.db.execute(
                "SELECT 1 FROM seen WHERE observation_hash=?", (self.observation_key(event),)
            ).fetchone()
            is not None
        )

    def discard(self, event):
        with self.db:
            self._remember(event)

    def _remember(self, event):
        self.db.execute(
            "INSERT OR IGNORE INTO seen VALUES(?,?)",
            (self.observation_key(event), utcnow().isoformat()),
        )

    def ingest(self, event, filters):
        if event.priority not in {"HIGH", "CRITICAL"}:
            raise ValueError("Only classified important events may be stored")
        keys = identities(event)
        facts = material_facts(event, filters)
        window = timedelta(hours=filters["dedup_window_hours"])
        with self.db:
            if self.seen(event):
                return None
            exact = self.db.execute(
                "SELECT DISTINCT e.* FROM events e JOIN event_identity i ON i.event_id=e.id "
                "WHERE i.identity_hash IN (?,?,?)",
                keys,
            ).fetchall()
            nearby = self.db.execute(
                "SELECT * FROM events WHERE event_type=? AND published_at BETWEEN ? AND ?",
                (
                    event.event_type,
                    (event.published_at - window).isoformat(),
                    (event.published_at + window).isoformat(),
                ),
            ).fetchall()
            candidates = {row["id"]: row for row in [*exact, *nearby]}
            exact_ids = {row["id"] for row in exact}
            for row in candidates.values():
                if row["id"] in exact_ids or same_story(event, row, filters):
                    newer = event.published_at >= parse_time(row["published_at"])
                    if not newer or not meaningful_update(facts, json.loads(row["material_facts"])):
                        self._remember(event)
                        return None
            event_key = digest(
                f"{event.source}:{event.source_event_id}:"
                f"{json.dumps(facts, sort_keys=True)}:{event.title}"
            )
            row = self.db.execute(
                "INSERT OR IGNORE INTO events(event_key,source,source_event_id,title,summary,"
                "event_type,priority,related_tickers,source_url,published_at,detected_at,"
                "created_at,entity_key,industry,reason,material_facts) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_key,
                    event.source,
                    event.source_event_id,
                    event.title,
                    event.summary[:1000],
                    event.event_type,
                    event.priority,
                    json.dumps(event.related_tickers),
                    event.source_url,
                    event.published_at.isoformat(),
                    event.detected_at.isoformat(),
                    utcnow().isoformat(),
                    event.entity_key,
                    event.industry,
                    event.reason,
                    json.dumps(facts),
                ),
            )
            if not row.rowcount:
                self._remember(event)
                return None
            event_id = row.lastrowid
            self.db.execute(
                "INSERT INTO notifications(event_id,status,next_attempt_at) VALUES(?,'pending',?)",
                (event_id, utcnow().isoformat()),
            )
            for key in keys:
                self.db.execute(
                    "INSERT INTO event_identity VALUES(?,?) ON CONFLICT(identity_hash) "
                    "DO UPDATE SET event_id=excluded.event_id",
                    (key, event_id),
                )
            self._remember(event)
            return event_id

    @staticmethod
    def row_event(row):
        return Event(
            row["source"],
            row["source_event_id"],
            row["title"],
            row["summary"],
            row["source_url"],
            parse_time(row["published_at"]),
            detected_at=parse_time(row["detected_at"]),
            related_tickers=tuple(json.loads(row["related_tickers"])),
            event_type=row["event_type"],
            priority=row["priority"],
            entity_key=row["entity_key"],
            industry=row["industry"],
            reason=row["reason"],
        )

    def recover_inflight(self):
        with self.db:
            self.db.execute(
                "UPDATE notifications SET status='unknown',"
                "error_message='interrupted_delivery_outcome_unknown' "
                "WHERE status='sending'"
            )

    def pending(self, max_age_hours=24, limit=20):
        now = utcnow()
        with self.db:
            self.db.execute(
                "UPDATE notifications SET status='expired',"
                "error_message='notification_age_limit' WHERE status='pending' "
                "AND event_id IN (SELECT id FROM events WHERE published_at<?)",
                ((now - timedelta(hours=max_age_hours)).isoformat(),),
            )
        return self.db.execute(
            "SELECT e.*,n.retry_count FROM events e JOIN notifications n ON n.event_id=e.id "
            "WHERE n.status='pending' AND n.next_attempt_at<=? "
            "ORDER BY CASE e.priority WHEN 'CRITICAL' THEN 0 ELSE 1 END,e.published_at LIMIT ?",
            (now.isoformat(), limit),
        ).fetchall()

    def claim(self, event_id):
        with self.db:
            return bool(
                self.db.execute(
                    "UPDATE notifications SET status='sending' "
                    "WHERE event_id=? AND status='pending'",
                    (event_id,),
                ).rowcount
            )

    def sent(self, event_id, message_id):
        with self.db:
            self.db.execute(
                "UPDATE notifications SET status='sent',telegram_message_id=?,"
                "sent_at=?,error_message=NULL WHERE event_id=?",
                (message_id, utcnow().isoformat(), event_id),
            )

    def delivery_failure(self, event_id, error, retry_count):
        count = retry_count + 1
        status = (
            "unknown"
            if error.ambiguous
            else ("pending" if error.retryable and count < 8 else "failed")
        )
        delay = max(error.retry_after, min(3600, 5 * 2 ** min(count, 10)))
        with self.db:
            self.db.execute(
                "UPDATE notifications SET status=?,retry_count=?,error_message=?,"
                "next_attempt_at=? WHERE event_id=?",
                (
                    status,
                    count,
                    str(error),
                    (utcnow() + timedelta(seconds=delay)).isoformat(),
                    event_id,
                ),
            )

    def prune_seen(self):
        with self.db:
            self.db.execute(
                "DELETE FROM seen WHERE observed_at<?",
                ((utcnow() - timedelta(days=30)).isoformat(),),
            )

    def status(self):
        return {
            "collectors": [dict(r) for r in self.db.execute("SELECT * FROM collector_state")],
            "notifications": {
                r[0]: r[1]
                for r in self.db.execute(
                    "SELECT status,count(*) FROM notifications GROUP BY status"
                )
            },
            "important_events": self.db.execute("SELECT count(*) FROM events").fetchone()[0],
        }
