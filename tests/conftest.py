from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from us_market_monitor.database.models import Event, utcnow
from us_market_monitor.database.repository import Repository
from us_market_monitor.processors.classifier import Classifier

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def filters():
    return yaml.safe_load((ROOT / "settings/filters.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def watchlist():
    data = yaml.safe_load((ROOT / "settings/watchlist.yaml").read_text(encoding="utf-8"))
    data["industry_actors"] = yaml.safe_load(
        (ROOT / "settings/industry_relations.yaml").read_text(encoding="utf-8")
    )["actors"]
    return data


@pytest.fixture
def classifier(watchlist, filters):
    return Classifier(watchlist, filters)


@pytest.fixture
def repo(tmp_path):
    repository = Repository(tmp_path / "test.sqlite3")
    yield repository
    repository.close()


@pytest.fixture
def event_factory():
    def make(title="NVIDIA reports quarterly results", **kwargs):
        data = dict(
            source="Finnhub/Test Publisher",
            source_event_id="100",
            title=title,
            summary=title,
            source_url="https://example.com/news/100",
            published_at=utcnow() - timedelta(minutes=1),
        )
        data.update(kwargs)
        return Event(**data)

    return make
