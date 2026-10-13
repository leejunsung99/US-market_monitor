import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(ValueError):
    pass


def boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigError(f"{name} must be true or false")
    return value in {"true", "1"}


def number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        raise ConfigError(f"{name} must be numeric") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ConfigError(f"{name} must be between {low} and {high}")
    return value


@dataclass
class Config:
    root: Path
    data_dir: Path
    log_dir: Path
    watchlist: dict
    filters: dict
    telegram_token: str = field(repr=False)
    telegram_chat_id: str = field(repr=False)
    finnhub_key: str = field(repr=False)
    sec_user_agent: str = field(repr=False)
    sec_enabled: bool = True
    fed_enabled: bool = True
    finnhub_enabled: bool = True
    sec_interval: float = 120
    fed_interval: float = 120
    finnhub_interval: float = 120
    finnhub_rpm: float = 30
    timeout: float = 20
    max_attempts: int = 3
    max_notification_age: float = 24
    log_level: str = "INFO"

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @classmethod
    def load(cls, root: Path | None = None, env_file: Path | None = None):
        root = (root or Path.cwd()).resolve()
        load_dotenv(env_file or root / ".env", override=False)

        def path(name, default):
            candidate = Path(os.getenv(name, default))
            return candidate if candidate.is_absolute() else root / candidate

        try:
            watchlist = yaml.safe_load(
                path("WATCHLIST_PATH", "settings/watchlist.yaml").read_text(encoding="utf-8")
            )
            filters = yaml.safe_load(
                path("FILTERS_PATH", "settings/filters.yaml").read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            raise ConfigError("Unable to read watchlist/filter YAML files") from None
        if not isinstance(watchlist, dict) or not isinstance(watchlist.get("companies"), dict):
            raise ConfigError("watchlist requires a companies mapping")
        for ticker, info in watchlist["companies"].items():
            if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", str(ticker)) or not isinstance(info, dict):
                raise ConfigError("Invalid company entry in watchlist")
            if not isinstance(info.get("aliases"), list) or not info["aliases"]:
                raise ConfigError("Each company requires an aliases list")
            if not all(isinstance(alias, str) and alias.strip() for alias in info["aliases"]):
                raise ConfigError("Company aliases must be nonempty strings")
            if not isinstance(info.get("industry"), str) or not info["industry"].strip():
                raise ConfigError("Each company requires a nonempty industry")
        if not isinstance(filters, dict) or not isinstance(filters.get("rules"), list):
            raise ConfigError("filters requires a rules list")
        for rule in filters["rules"]:
            if not isinstance(rule, dict) or not {"type", "label", "patterns"} <= rule.keys():
                raise ConfigError("Each filter rule requires type, label and patterns")
            if not isinstance(rule["patterns"], list):
                raise ConfigError("Rule patterns must be a list")
            try:
                for pattern in rule["patterns"]:
                    re.compile(pattern, re.I)
            except (re.error, TypeError):
                raise ConfigError("Invalid rule regular expression") from None
        for name in (
            "exclusions",
            "speculation",
            "critical_patterns",
            "material_update_patterns",
            "material_scale_patterns",
        ):
            try:
                for pattern in filters.get(name, []):
                    re.compile(pattern, re.I)
            except (re.error, TypeError):
                raise ConfigError(f"Invalid {name} expression") from None
        for name, low, high in (
            ("high_threshold", 1, 10),
            ("critical_threshold", 1, 10),
            ("dedup_window_hours", 1, 168),
            ("dedup_similarity", 0, 1),
            ("minimum_deal_usd", 1, 1e15),
        ):
            value = filters.get(name)
            if not isinstance(value, (int, float)) or not low <= value <= high:
                raise ConfigError(f"Invalid filter setting: {name}")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if bool(token) != bool(chat):
            raise ConfigError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set together")
        if token and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
            raise ConfigError("Invalid TELEGRAM_BOT_TOKEN format")
        if chat and not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,}", chat):
            raise ConfigError("Invalid TELEGRAM_CHAT_ID format")
        ua = os.getenv("SEC_USER_AGENT", "").strip()
        if ua and ("\n" in ua or "\r" in ua or not re.search(r"\S+@\S+\.\S+", ua)):
            raise ConfigError("SEC_USER_AGENT requires an application name and contact email")
        if boolean("AI_SUMMARY_ENABLED", False):
            raise ConfigError("AI summary is not implemented in v1; use AI_SUMMARY_ENABLED=false")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError("Invalid LOG_LEVEL")
        return cls(
            root,
            path("DATA_DIR", "data"),
            path("LOG_DIR", "logs"),
            watchlist,
            filters,
            token,
            chat,
            os.getenv("FINNHUB_API_KEY", "").strip(),
            ua,
            boolean("SEC_ENABLED", True),
            boolean("FED_ENABLED", True),
            boolean("FINNHUB_ENABLED", True),
            number("SEC_INTERVAL_SECONDS", 120, 60, 86400),
            number("FED_INTERVAL_SECONDS", 120, 60, 86400),
            number("FINNHUB_INTERVAL_SECONDS", 120, 60, 86400),
            number("FINNHUB_REQUESTS_PER_MINUTE", 30, 1, 60),
            number("HTTP_TIMEOUT_SECONDS", 20, 1, 120),
            int(number("HTTP_MAX_ATTEMPTS", 3, 1, 5)),
            number("MAX_NOTIFICATION_AGE_HOURS", 24, 1, 168),
            level,
        )
