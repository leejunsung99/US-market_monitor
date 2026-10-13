import sqlite3

import pytest
from conftest import ROOT
from filelock import FileLock

from us_market_monitor.database.repository import Repository
from us_market_monitor.main import main


def test_readonly_status_works_while_process_lock_held(monkeypatch, tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    repo = Repository(data / "radar.sqlite3")
    repo.ensure_source("fed:monetary")
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    with FileLock(data / "radar.lock", timeout=0):
        assert main(["--root", str(ROOT), "--status"]) == 0
    assert "fed:monetary" in capsys.readouterr().out
    readonly = Repository(data / "radar.sqlite3", readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        readonly.ensure_source("must_not_write")
    readonly.close()
    repo.close()
