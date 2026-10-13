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
  observation_hash TEXT PRIMARY KEY, observed_at TEXT NOT NULL, rules_version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS review_candidates (
  observation_hash TEXT PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
  title TEXT NOT NULL, evidence TEXT NOT NULL, published_at TEXT NOT NULL,
  reason TEXT NOT NULL, score REAL NOT NULL, rules_version TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_review_expiry ON review_candidates(expires_at);
CREATE TABLE IF NOT EXISTS filter_counts (
  day TEXT NOT NULL, source TEXT NOT NULL, reason TEXT NOT NULL, rules_version TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day,source,reason,rules_version)
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
            # Additive migration preserves existing events, baselines and delivery states.
            event_columns = {row[1] for row in self.db.execute("PRAGMA table_info(events)")}
            for name, declaration in {
                "event_stage": "TEXT NOT NULL DEFAULT ''",
                "evidence_level": "TEXT NOT NULL DEFAULT ''",
                "actor": "TEXT NOT NULL DEFAULT ''",
                "impact_tickers": "TEXT NOT NULL DEFAULT '[]'",
                "importance_score": "REAL NOT NULL DEFAULT 0",
                "score_breakdown": "TEXT NOT NULL DEFAULT '{}'",
            }.items():
                if name not in event_columns:
                    self.db.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
            if "rules_version" not in {
                row[1] for row in self.db.execute("PRAGMA table_info(seen)")
            }:
                self.db.execute(
                    "ALTER TABLE seen ADD COLUMN rules_version TEXT NOT NULL DEFAULT ''"
                )
            self.db.commit()

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

    def seen(self, event, rules_version=None):
        version = (
            rules_version if rules_version is not None else event.metadata.get("rules_version")
        )
        query = "SELECT 1 FROM seen WHERE observation_hash=?"
        values = [self.observation_key(event)]
        if version is not None:
            query += " AND rules_version=?"
            values.append(version)
        return self.db.execute(query, values).fetchone() is not None

    def discard(self, event):
        with self.db:
            already_seen = self.seen(event)
            self._remember(event)
            decision = event.metadata.get("classification_decision", {})
            if decision and not already_seen:
                self.db.execute(
                    "INSERT INTO filter_counts VALUES(?,?,?,?,1) "
                    "ON CONFLICT(day,source,reason,rules_version) DO UPDATE SET count=count+1",
                    (
                        utcnow().date().isoformat(),
                        event.source,
                        decision["reason"],
                        decision["rules_version"],
                    ),
                )
            if decision.get("status") == "review":
                retention = event.metadata.get("review_retention_days", 7)
                self.db.execute(
                    "INSERT OR REPLACE INTO review_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        self.observation_key(event),
                        event.source,
                        event.source_url,
                        event.title[:350],
                        decision.get("evidence", event.summary)[:700],
                        event.published_at.isoformat(),
                        decision["reason"],
                        decision.get("score", 0),
                        decision["rules_version"],
                        (utcnow() + timedelta(days=retention)).isoformat(),
                    ),
                )
                maximum = event.metadata.get("review_max_rows", 500)
                self.db.execute(
                    "DELETE FROM review_candidates WHERE observation_hash IN "
                    "(SELECT observation_hash FROM review_candidates ORDER BY expires_at DESC "
                    "LIMIT -1 OFFSET ?)",
                    (maximum,),
                )

    def _remember(self, event):
        self.db.execute(
            "INSERT INTO seen(observation_hash,observed_at,rules_version) VALUES(?,?,?) "
            "ON CONFLICT(observation_hash) DO UPDATE SET rules_version=excluded.rules_version",
            (
                self.observation_key(event),
                utcnow().isoformat(),
                event.metadata.get("rules_version", ""),
            ),
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
                "UPDATE events SET event_stage=?,evidence_level=?,actor=?,impact_tickers=?,"
                "importance_score=?,score_breakdown=? WHERE id=?",
                (
                    event.event_stage,
                    event.evidence_level,
                    event.actor,
                    json.dumps(event.impact_tickers),
                    event.importance_score,
                    json.dumps(event.score_breakdown),
                    event_id,
                ),
            )
            self.db.execute(
                "DELETE FROM review_candidates WHERE observation_hash=?",
                (self.observation_key(event),),
            )
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
        def optional(name, default):
            return row[name] if name in row.keys() else default

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
            event_stage=optional("event_stage", ""),
            evidence_level=optional("evidence_level", ""),
            actor=optional("actor", ""),
            impact_tickers=tuple(json.loads(optional("impact_tickers", "[]"))),
            importance_score=optional("importance_score", 0),
            score_breakdown=json.loads(optional("score_breakdown", "{}")),
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
            self.db.execute(
                "DELETE FROM review_candidates WHERE expires_at<=?", (utcnow().isoformat(),)
            )
            self.db.execute(
                "DELETE FROM filter_counts WHERE day<?",
                ((utcnow() - timedelta(days=30)).date().isoformat(),),
            )

    def reviews(self, limit=20):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT source,title,evidence,source_url,published_at,reason,score,rules_version,expires_at "
                "FROM review_candidates WHERE expires_at>? ORDER BY score DESC,published_at DESC LIMIT ?",
                (utcnow().isoformat(), limit),
            )
        ]

    def filter_report(self):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT day,reason,sum(count) AS count FROM filter_counts GROUP BY day,reason ORDER BY day DESC,count DESC"
            )
        ]

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
