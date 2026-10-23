import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from us_market_monitor.database.models import utcnow
from us_market_monitor.http import FetchError, Stopped
from us_market_monitor.notifications.article import enrich_article
from us_market_monitor.notifications.formatter import format_event
from us_market_monitor.notifications.telegram import DeliveryError
from us_market_monitor.processors.briefing import economic_digest
from us_market_monitor.processors.normalizer import parse_time

log = logging.getLogger(__name__)


class Radar:
    def __init__(
        self,
        collectors,
        repository,
        classifier,
        *,
        telegram=None,
        dry_run=False,
        max_notification_age=24,
        stop=None,
        translator=None,
        monitor_releases=False,
    ):
        self.collectors = {c.name: c for c in collectors}
        self.repo, self.classifier = repository, classifier
        self.telegram, self.dry_run = telegram, dry_run
        self.translator = translator
        self.monitor_releases = monitor_releases
        self.max_notification_age = max_notification_age
        self.stop = stop or threading.Event()
        self.futures = {}
        self.next_due = {name: 0.0 for name in self.collectors}
        self.notification_due = 0.0
        self.maintenance_due = 0.0
        self.release_monitor_due = 0.0
        # Persist baselines BEFORE any fetch. Failed first requests keep the same baseline.
        # Newly published events during initial fetch are not silently swallowed.
        # Sources usually publish whole-second timestamps. Round down to avoid
        # dropping an event published in the same second just after process startup.
        started_at = utcnow().replace(microsecond=0)
        for name in self.collectors:
            lookback = getattr(self.collectors[name], "initial_lookback_hours", 0)
            self.repo.ensure_source(name, started_at - timedelta(hours=lookback))

    def fetch(self, name):
        state = self.repo.state(name)
        cursor = json.loads(state["last_cursor"])
        baseline = parse_time(cursor["baseline_at"])
        since = baseline
        if state["last_success_at"]:
            # Never advance solely by article timestamp: delayed stories need overlap.
            since = max(baseline, parse_time(state["last_success_at"]) - timedelta(days=2))
        self.repo.checked(name)
        return since, cursor

    def process(self, name, events, cursor):
        if getattr(self.collectors[name], "is_calendar", False):
            self.repo.save_expected_releases(name, events)
            self.repo.success(name, cursor)
            log.info("%s: calendar succeeded expected_releases=%d", name, len(events))
            return
        baseline = parse_time(cursor["baseline_at"])
        accepted = 0
        for event in sorted(events, key=lambda e: e.published_at):
            if self.stop.is_set():
                raise Stopped()
            self.classifier.prepare(event)
            if event.published_at < baseline or self.repo.seen(event, self.classifier.version):
                continue
            classified = self.classifier.classify(event)
            if classified is None:
                self.repo.discard(event)
                continue
            event_id = self.repo.ingest(classified, self.classifier.filters)
            if event_id:
                accepted += 1
                log.info(
                    "%s: important event id=%s priority=%s type=%s",
                    name,
                    event_id,
                    classified.priority,
                    classified.event_type,
                )
                if self.dry_run:
                    print("[DRY RUN — NOT SENT]\n" + format_event(classified), flush=True)
        self.repo.success(name, cursor)
        log.info("%s: collection succeeded fetched=%d important=%d", name, len(events), accepted)

    def dispatch(self):
        if not self.telegram or self.dry_run or self.stop.is_set():
            return
        if time.monotonic() < self.notification_due:
            return
        # One delivery per scheduler tick avoids blocking collection processing behind bursts.
        rows = self.repo.pending(self.max_notification_age, limit=1)
        for row in rows:
            if self.stop.is_set() or not self.repo.claim(row["id"]):
                break
            try:
                event = self.repo.row_event(row)
                if hasattr(self.telegram, "send_article"):
                    enrich_article(event, stop=self.stop)
                    translated = False
                    if self.translator:
                        translated = self.translator.translate(event)
                    with self.repo.db:
                        self.repo.update_article(row["id"], event)
                    if event.event_type not in {'economic_indicator', 'monitor_alert'} and not translated:
                        raise DeliveryError('translation_pending', retryable=True, retry_after=60)
                    if (event.event_type == 'economic_indicator'
                            and '공식 발표 원문 발췌' in economic_digest(event)):
                        raise DeliveryError('economic_korean_review_pending', retryable=True, retry_after=300)
                    message_id = self.telegram.send_article(event)
                else:
                    message_id = self.telegram.send(format_event(event))
            except DeliveryError as error:
                self.repo.delivery_failure(row["id"], error, row["retry_count"])
                if error.retry_after:
                    self.notification_due = time.monotonic() + error.retry_after
                log.warning("Telegram event=%s error=%s", row["id"], error)
            except Stopped:
                # No call was made; restore safely to pending.
                self.repo.db.execute(
                    "UPDATE notifications SET status='pending' WHERE event_id=?", (row["id"],)
                )
                self.repo.db.commit()
                break
            else:
                self.repo.sent(row["id"], message_id)
                self.notification_due = time.monotonic() + 3.1
                log.info("Telegram sent event=%s message_id=%s", row["id"], message_id)

    def run(self, once=False):
        attempted = set()
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="collector") as pool:
            while not self.stop.is_set():
                if time.monotonic() >= self.maintenance_due:
                    self.repo.prune_seen()
                    self.maintenance_due = time.monotonic() + 3600
                for name, collector in self.collectors.items():
                    if name in self.futures or (once and name in attempted):
                        continue
                    if time.monotonic() >= self.next_due[name]:
                        since, cursor = self.fetch(name)
                        future = pool.submit(collector.collect, since, cursor)
                        self.futures[name] = (future, cursor)
                        attempted.add(name)
                for name, (future, cursor) in list(self.futures.items()):
                    if not future.done():
                        continue
                    del self.futures[name]
                    try:
                        self.process(name, future.result(), cursor)
                    except Stopped:
                        self.stop.set()
                        break
                    except Exception as error:
                        # Do not interpolate raw exception text: URLs may contain credentials.
                        self.repo.failure(name)
                        failures = self.repo.state(name)["error_count"]
                        backoff = min(3600, self.collectors[name].interval * 2 ** min(failures, 6))
                        if isinstance(error, FetchError):
                            backoff = max(backoff, error.retry_after)
                        self.next_due[name] = time.monotonic() + backoff + random.uniform(0, 3)
                        code = str(error) if isinstance(error, FetchError) else type(error).__name__
                        log.error("%s: collection failed (%s), retry in %.0fs", name, code, backoff)
                    else:
                        self.next_due[name] = time.monotonic() + self.collectors[name].interval
                if (
                    self.monitor_releases
                    and time.monotonic() >= self.release_monitor_due
                    and not any(
                        name in self.futures for name in ("calendar:census", "economic:census")
                    )
                ):
                    self.repo.queue_missing_releases(self.classifier.filters)
                    self.release_monitor_due = time.monotonic() + 60
                self.dispatch()
                if once and len(attempted) == len(self.collectors) and not self.futures:
                    # Drain eligible ready notifications, but do not spin on deferred retries.
                    while self.telegram and not self.dry_run and not self.stop.is_set():
                        if not self.repo.pending(self.max_notification_age, limit=1):
                            break
                        if self.stop.wait(max(0, self.notification_due - time.monotonic())):
                            break
                        self.dispatch()
                    break
                if not self.collectors:
                    break
                self.stop.wait(0.25)
            for future, _ in self.futures.values():
                future.cancel()
        self.repo.prune_seen()
