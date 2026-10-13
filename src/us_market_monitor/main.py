import argparse
import json
import logging
import signal
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from filelock import FileLock, Timeout

from us_market_monitor.collectors.fed import FEEDS, FedCollector
from us_market_monitor.collectors.finnhub import FinnhubCollector
from us_market_monitor.collectors.sec import SecCollector, SecMapping
from us_market_monitor.config import Config, ConfigError
from us_market_monitor.database.repository import Repository
from us_market_monitor.http import HttpClient, Stopped
from us_market_monitor.notifications.telegram import DeliveryError, Telegram
from us_market_monitor.processors.classifier import Classifier
from us_market_monitor.scheduler.jobs import Radar

log = logging.getLogger(__name__)


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets):
        super().__init__("%(asctime)s UTC %(levelname)s %(name)s: %(message)s")
        self.converter = time.gmtime
        self.secrets = [value for value in secrets if value]

    def format(self, record):
        result = super().format(record)
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        return result


def setup_logging(config):
    config.log_dir.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(sys.stderr),
        RotatingFileHandler(
            config.log_dir / "radar.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        ),
    ]
    formatter = RedactingFormatter(
        [config.telegram_token, config.telegram_chat_id, config.finnhub_key, config.sec_user_agent]
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=config.log_level, handlers=handlers, force=True)
    # Transport debug messages expose request URLs; keep them suppressed.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("filelock").setLevel(logging.WARNING)


def build_collectors(config, stop):
    collectors = []
    shared = {"timeout": config.timeout, "attempts": config.max_attempts, "stop": stop}
    if config.fed_enabled:
        fed_http = HttpClient(**shared, spacing=1.0, headers={"User-Agent": "USMarketRadar/0.2"})
        collectors += [FedCollector(fed_http, name, config.fed_interval) for name in FEEDS]
    if config.sec_enabled and config.sec_user_agent:
        sec_http = HttpClient(
            **shared,
            spacing=0.25,
            headers={"User-Agent": config.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
        )
        mapping = SecMapping(sec_http)
        collectors += [
            SecCollector(sec_http, mapping, ticker, config.sec_interval)
            for ticker in config.watchlist["companies"]
        ]
    else:
        log.warning("SEC disabled: SEC_ENABLED=false or identifying SEC_USER_AGENT is missing")
    if config.finnhub_enabled and config.finnhub_key:
        news_http = HttpClient(
            **shared,
            spacing=60 / config.finnhub_rpm,
            headers={"X-Finnhub-Token": config.finnhub_key},
        )
        collectors.append(FinnhubCollector(news_http, interval=config.finnhub_interval))
        collectors += [
            FinnhubCollector(news_http, ticker, config.finnhub_interval)
            for ticker in config.watchlist["companies"]
        ]
    else:
        log.warning(
            "Finnhub disabled: key missing or FINNHUB_ENABLED=false; broad market news "
            "is NOT monitored by SEC/Fed alone"
        )
    return collectors


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="US Market Radar — important events only")
    parser.add_argument("--once", action="store_true", help="Poll each enabled source once")
    parser.add_argument(
        "--dry-run", action="store_true", help="No Telegram or persistent DB changes"
    )
    parser.add_argument(
        "--test-telegram", action="store_true", help="Send one clearly labeled test"
    )
    parser.add_argument(
        "--status", action="store_true", help="Show local collector/delivery status"
    )
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="Project configuration directory"
    )
    parser.add_argument("--env-file", type=Path, help="Optional .env path")
    args = parser.parse_args(argv)
    if args.dry_run and (args.test_telegram or args.status):
        parser.error("--dry-run cannot be combined with --test-telegram or --status")
    if args.test_telegram and args.status:
        parser.error("--test-telegram cannot be combined with --status")
    repo = None
    stop = threading.Event()
    try:
        config = Config.load(args.root, args.env_file)
        setup_logging(config)
        db_path = config.data_dir / "radar.sqlite3"
        if args.status:
            if not db_path.exists():
                print(json.dumps({"status": "not_initialized"}))
                return 0
            repo = Repository(db_path, readonly=True)
            print(json.dumps(repo.status(), indent=2, ensure_ascii=False))
            return 0
        config.data_dir.mkdir(parents=True, exist_ok=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        lock_name = "radar.dry-run.lock" if args.dry_run else "radar.lock"
        with FileLock(config.data_dir / lock_name, timeout=0):
            if args.test_telegram:
                if not config.telegram_enabled:
                    raise ConfigError("Telegram is disabled; configure both Telegram variables")
                Telegram(
                    config.telegram_token,
                    config.telegram_chat_id,
                    timeout=config.timeout,
                    stop=stop,
                ).send("🧪 US Market Radar 연결 테스트 — 실제 뉴스가 아닙니다.")
                log.info("Telegram connection test succeeded")
                return 0
            repo = Repository(":memory:" if args.dry_run else db_path)
            repo.recover_inflight()
            telegram = None
            if config.telegram_enabled and not args.dry_run:
                telegram = Telegram(
                    config.telegram_token,
                    config.telegram_chat_id,
                    timeout=config.timeout,
                    stop=stop,
                )
            else:
                log.warning(
                    "Telegram delivery disabled (%s)",
                    "dry-run"
                    if args.dry_run
                    else "credentials missing; important events remain pending",
                )
            collectors = build_collectors(config, stop)
            if not collectors:
                raise ConfigError("No collectors enabled; configure at least one source")
            log.info(
                "Starting Radar: collectors=%d dry_run=%s once=%s",
                len(collectors),
                args.dry_run,
                args.once,
            )
            radar = Radar(
                collectors,
                repo,
                Classifier(config.watchlist, config.filters),
                telegram=telegram,
                dry_run=args.dry_run,
                max_notification_age=config.max_notification_age,
                stop=stop,
            )
            radar.run(once=args.once)
            log.info("Stopped safely; status=%s", json.dumps(repo.status(), ensure_ascii=False))
            if args.once and any(repo.state(c.name)["error_count"] for c in collectors):
                return 1
            return 0
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except Timeout:
        print("Another Radar process is already running for this data directory.", file=sys.stderr)
        return 2
    except DeliveryError as error:
        log.error("Telegram test failed: %s", error)
        return 1
    except (KeyboardInterrupt, Stopped):
        return 0
    except Exception as error:
        # Fail loud without disclosing external request URLs or credentials.
        log.error("Fatal startup/storage error: %s", type(error).__name__)
        return 1
    finally:
        if repo:
            repo.close()


if __name__ == "__main__":
    raise SystemExit(main())
