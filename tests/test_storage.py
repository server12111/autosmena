import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, text

from app import storage
from app.db import make_engine
from app.security import verify_password


def test_hosted_default_uses_persistent_volume_without_database_variable(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setattr(storage, "hosted", lambda: True)
    assert storage.database_url("") == "sqlite:////app/data/smena.db"
    assert storage.database_url("sqlite:///./smena.db") == "sqlite:////app/data/smena.db"


def test_postgres_override_is_preserved():
    url = "postgresql+psycopg://user:password@external/database"
    assert storage.database_url(url) == url


def test_schema_and_balance_survive_repeated_initialization(tmp_path):
    path = tmp_path / "persistent.db"
    url = "sqlite:///" + str(path)
    storage.prepare_database(url, url)
    engine = make_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users(id,username,balance,blocked,created_at) VALUES(123,'tester',3000000,0,100)"
            )
        )
    engine.dispose()
    storage.prepare_database(url, url)
    engine = make_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT balance FROM users WHERE id=123")) == 3000000
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert connection.scalar(text("PRAGMA synchronous")) == 2
        assert "app_settings" in inspect(connection).get_table_names()
    engine.dispose()


def test_parallel_startup_does_not_race_schema_creation(tmp_path):
    url = "sqlite:///" + str(tmp_path / "parallel.db")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: storage.prepare_database(url, url), range(4)))
    engine = make_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM alembic_version")) == 1
    engine.dispose()


def test_legacy_backup_includes_uncheckpointed_wal_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "ROOT", tmp_path)
    original = tmp_path / "legacy.db"
    destination = tmp_path / "data" / "legacy.db"
    destination.parent.mkdir()
    # An earlier read-only status check may already have created an empty SQLite file.
    destination.touch()
    with sqlite3.connect(original) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE example(value INTEGER)")
        source.execute("INSERT INTO example VALUES(42)")
        source.commit()
        storage.import_legacy_database(destination, "sqlite:///" + str(original))
        with sqlite3.connect(destination) as copy:
            assert copy.execute("SELECT value FROM example").fetchone()[0] == 42
        source.execute("UPDATE example SET value=99")
        source.commit()
        storage.import_legacy_database(destination, "sqlite:///" + str(original))
        with sqlite3.connect(destination) as copy:
            assert copy.execute("SELECT value FROM example").fetchone()[0] == 42


def test_unknown_database_is_not_overwritten(tmp_path):
    path = tmp_path / "unknown.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE other(value INTEGER)")
    url = "sqlite:///" + str(path)
    with pytest.raises(ValueError, match="нет истории"):
        storage.prepare_database(url, url)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='other'").fetchone()


def test_admin_credentials_survive_restart_without_environment_variable(tmp_path):
    first = storage.admin_password(tmp_path)
    password = (tmp_path / "admin-access.txt").read_text(encoding="utf-8").split("Password: ")[1].strip()
    assert verify_password(password, first)
    assert storage.admin_password(tmp_path) == first
    assert json.loads((tmp_path / "admin-auth.json").read_text(encoding="utf-8"))["hash"] == first


def test_startup_command_initializes_database_without_telegram_requests(tmp_path):
    import os

    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(tmp_path),
            "DATABASE_URL": "sqlite:///" + str(tmp_path / "runtime-test.db"),
            "BOT_TOKEN": "",
            "APICC_API_KEY": "",
            "ADMIN_PASSWORD_HASH": "",
        }
    )
    result = subprocess.run(
        [sys.executable, "main.py", "--check"], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runtime-test.db").exists()
    assert (tmp_path / "admin-auth.json").exists()
    with sqlite3.connect(tmp_path / "runtime-test.db") as connection:
        connection.execute(
            "INSERT INTO users(id,username,balance,blocked,created_at) VALUES(999,'restart',1230000,0,100)"
        )
    second = subprocess.run(
        [sys.executable, "main.py", "--check"], env=env, capture_output=True, text=True, timeout=30
    )
    assert second.returncode == 0, second.stderr
    with sqlite3.connect(tmp_path / "runtime-test.db") as connection:
        assert connection.execute("SELECT balance FROM users WHERE id=999").fetchone()[0] == 1230000


def test_supervisor_stops_remaining_services_when_one_fails(monkeypatch):
    from app import runtime

    class Process:
        def __init__(self, code):
            self.code = code
            self.stopped = False
            self.pid = 123

        def poll(self):
            return self.code

        def terminate(self):
            self.stopped = True
            self.code = 0

        def wait(self, timeout=None):
            return self.code

    processes = [Process(1), Process(None)]
    items = iter(processes)
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *a, **k: next(items))
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runtime, "stop_process", lambda process: process.terminate())
    with pytest.raises(RuntimeError, match="exit=1"):
        runtime.supervise([["admin"], ["worker"]])
    assert processes[1].stopped is True


def test_plain_password_in_admin_password_hash_is_accepted(tmp_path):
    from app.security import verify_password

    first = storage.admin_password(tmp_path, "my-plain-pass")
    assert first.startswith("scrypt:") and verify_password("my-plain-pass", first)
    assert storage.admin_password(tmp_path, "my-plain-pass") == first  # stable across restarts
    assert not (tmp_path / "admin-access.txt").exists()  # the owner already knows it


def test_damaged_login_record_issues_new_password_instead_of_crashing(tmp_path):
    from app.security import verify_password

    for broken in ('{"hash": "plain"}', "not json", "[]"):
        (tmp_path / "admin-auth.json").write_text(broken, encoding="utf-8")
        hashed = storage.admin_password(tmp_path)
        password = (tmp_path / "admin-access.txt").read_text(encoding="utf-8").split(": ", 1)[1].strip()
        assert verify_password(password, hashed)
