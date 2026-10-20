import pytest
from conftest import ROOT
from filelock import FileLock, Timeout

from us_market_monitor.config import Config, ConfigError
from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.main import build_collectors, main
from us_market_monitor.processors.normalizer import canonical_url, parse_time


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    names = [
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "FINNHUB_API_KEY",
        "SEC_USER_AGENT",
        "AI_SUMMARY_ENABLED",
        "FED_ENABLED",
        "SEC_ENABLED",
        "FINNHUB_ENABLED",
        "FED_INTERVAL_SECONDS",
        "WATCHLIST_PATH",
        "FILTERS_PATH",
        "LOG_LEVEL",
        "ECONOMIC_ENABLED",
        "ECONOMIC_INTERVAL_SECONDS",
        "ECONOMIC_INITIAL_LOOKBACK_HOURS",
        "BLS_CPI_RSS_URL",
        "BLS_PPI_RSS_URL",
        "BLS_EMPLOYMENT_RSS_URL",
        "BEA_RSS_URL",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    # Tests below isolate the original optional sources. Economic coverage has
    # dedicated tests and is enabled by default in the application.
    monkeypatch.setenv("ECONOMIC_ENABLED", "false")
    monkeypatch.setenv("PUBLIC_SOURCES_ENABLED", "false")
    monkeypatch.setenv("RELEASE_MONITOR_ENABLED", "false")


def test_missing_optional_keys_disables_sources():
    import threading

    config = Config.load(ROOT, ROOT / "nonexistent.env")
    collectors = build_collectors(config, threading.Event())
    assert all(c.name.startswith("fed:") for c in collectors)
    assert not config.telegram_enabled


@pytest.mark.parametrize(
    "name,value",
    [
        ("TELEGRAM_BOT_TOKEN", "bad-format"),
        ("SEC_USER_AGENT", "no-contact"),
        ("AI_SUMMARY_ENABLED", "true"),
        ("FED_INTERVAL_SECONDS", "0"),
        ("FED_INTERVAL_SECONDS", "nan"),
        ("FED_ENABLED", "typo"),
        ("ECONOMIC_ENABLED", "typo"),
        ("ECONOMIC_INTERVAL_SECONDS", "0"),
        ("BLS_CPI_RSS_URL", "https://example.com/feed"),
    ],
)
def test_invalid_settings_fail_without_secret_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Config.load(ROOT, ROOT / "nonexistent.env")


def test_utc_and_kst_dates_and_missing_timezone():
    assert parse_time("2026-01-01T12:00:00+09:00").hour == 3
    assert parse_time("2026-01-01T12:00:00", naive_zone="America/New_York").hour == 17
    with pytest.raises(ValueError):
        parse_time("2026-01-01T12:00:00")


def test_canonical_url_removes_tracking_but_preserves_document_params():
    assert (
        canonical_url("https://Example.com/article?utm_source=x&id=42#top")
        == "https://example.com/article?id=42"
    )
    with pytest.raises(ValueError):
        canonical_url("file:///tmp/x")


def test_naive_event_date_rejected():
    with pytest.raises(ValueError):
        Event("s", "i", "t", "", "https://example.com", utcnow().replace(tzinfo=None))


def test_process_lock_blocks_second_owner(tmp_path):
    with FileLock(tmp_path / "radar.lock", timeout=0):
        with pytest.raises(Timeout):
            FileLock(tmp_path / "radar.lock", timeout=0).acquire()


def test_dry_run_does_not_create_persistent_db(monkeypatch, tmp_path):
    import us_market_monitor.main as main_module

    monkeypatch.setenv("WATCHLIST_PATH", str(ROOT / "settings/watchlist.yaml"))
    monkeypatch.setenv("FILTERS_PATH", str(ROOT / "settings/filters.yaml"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    class Collector:
        name, interval = "fake", 120

        def collect(self, *_):
            return []

    monkeypatch.setattr(main_module, "build_collectors", lambda *_: [Collector()])
    assert main(["--root", str(tmp_path), "--dry-run", "--once"]) == 0
    assert not (tmp_path / "data/radar.sqlite3").exists()


def test_no_collectors_error(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHLIST_PATH", str(ROOT / "settings/watchlist.yaml"))
    monkeypatch.setenv("FILTERS_PATH", str(ROOT / "settings/filters.yaml"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FED_ENABLED", "false")
    assert main(["--root", str(tmp_path), "--once"]) == 2
