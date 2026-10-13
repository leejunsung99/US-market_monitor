import threading
from datetime import UTC, date, datetime

from scripts.replay_day import day_window, send


def test_replay_window_uses_korean_midnight():
    end = datetime(2026, 10, 13, 4, 18, tzinfo=UTC)
    start, cutoff = day_window(date(2026, 10, 13), end)
    assert start == datetime(2026, 10, 12, 15, tzinfo=UTC)
    assert cutoff == end


def test_replay_caps_at_five_and_does_not_resend(
    monkeypatch, repo, classifier, filters, event_factory, capsys
):
    from types import SimpleNamespace

    import scripts.replay_day as replay

    repo.db.execute("CREATE TABLE replay_window(start_at TEXT,end_at TEXT)")
    repo.db.execute(
        "INSERT INTO replay_window VALUES(?,?)",
        ("2026-10-12T15:00:00+00:00", "2026-10-13T04:18:00+00:00"),
    )
    repo.db.commit()
    names = ["NVIDIA", "Apple", "Microsoft", "Amazon", "Tesla", "Broadcom"]
    for index, name in enumerate(names):
        approved = classifier.classify(
            event_factory(
                f"{name} reports quarterly results",
                source_event_id=str(index),
                source_url=f"https://example.com/{index}",
            )
        )
        repo.ingest(approved, filters)
    messages = []

    class FakeTelegram:
        def __init__(self, *_args, **_kwargs):
            pass

        def send(self, text):
            messages.append(text)
            return len(messages)

    monkeypatch.setattr(replay, "Telegram", FakeTelegram)
    config = SimpleNamespace(
        telegram_enabled=True,
        telegram_token="placeholder",
        telegram_chat_id="placeholder",
        timeout=20,
        watchlist={"companies": classifier.companies},
        filters=filters,
    )
    send(config, repo, 5, 0, threading.Event())
    send(config, repo, 5, 0, threading.Event())
    assert len(messages) == 5
    assert all("실시간 속보가 아닙니다" in text for text in messages)
    assert repo.status()["notifications"] == {"pending": 1, "sent": 5}
    assert "SEND_RESULT" in capsys.readouterr().out
