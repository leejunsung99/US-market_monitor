from datetime import timedelta

from us_market_monitor.database.models import utcnow
from us_market_monitor.http import retry_delay


def test_general_hash_retention(repo, event_factory):
    repo.discard(event_factory("ordinary story"))
    repo.db.execute("UPDATE seen SET observed_at=?", ((utcnow() - timedelta(days=31)).isoformat(),))
    repo.db.commit()
    repo.prune_seen()
    assert repo.db.execute("SELECT count(*) FROM seen").fetchone()[0] == 0


def test_invalid_retry_delays_are_not_infinite():
    assert retry_delay("inf") == 0
    assert retry_delay("nan") == 0
    assert retry_delay("-3") == 0
    assert retry_delay("bad") == 0
    assert retry_delay("120") == 120
