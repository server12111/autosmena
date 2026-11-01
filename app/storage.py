"""Persistent storage without requiring a database environment variable."""

import json
import os
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]


def hosted():
    return os.name != "nt" and (Path("/app").is_dir() or bool(os.getenv("DATA_DIR")))


def data_directory():
    # DATA_DIR is supplied by some hosts. Users do not need to create it.
    supplied = os.getenv("DATA_DIR")
    return (
        Path(supplied).expanduser().resolve()
        if supplied
        else Path("/app/data")
        if hosted()
        else ROOT / "data"
    )


def database_url(configured=""):
    if not configured.strip():
        return URL.create("sqlite", database=(data_directory() / "smena.db").as_posix()).render_as_string(
            hide_password=False
        )
    url = make_url(configured)
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        path = Path(url.database)
        if hosted() or not path.is_absolute():
            return url.set(database=(data_directory() / path.name).as_posix()).render_as_string(
                hide_password=False
            )
    return configured


@contextmanager
def file_lock(path, timeout=60):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            start = time.monotonic()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() - start >= timeout:
                        raise TimeoutError("Не удалось дождаться блокировки базы") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            start = time.monotonic()
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - start >= timeout:
                        raise TimeoutError("Не удалось дождаться блокировки базы") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def import_legacy_database(destination, configured=""):
    if destination.exists():
        with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as current:
            if current.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone():
                return
    raw = make_url(configured).database if configured else "smena.db"
    if not raw or raw == ":memory:":
        return
    candidates = {
        Path(raw).resolve(),
        (ROOT / Path(raw).name).resolve(),
        (Path("/app") / Path(raw).name).resolve(),
    }
    candidates = {p for p in candidates if p != destination.resolve() and p.is_file()}
    if len(candidates) > 1:
        raise ValueError("Найдено несколько старых баз. Автоматический перенос остановлен, файлы сохранены")
    if not candidates:
        return
    source = candidates.pop()
    temporary = destination.with_name(destination.name + ".import-" + secrets.token_hex(6) + ".tmp")
    try:
        with (
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old,
            closing(sqlite3.connect(temporary)) as new,
        ):
            old.backup(new)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def prepare_database(url, configured=""):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from app.db import make_engine

    parsed = make_url(url)
    directory = (
        Path(parsed.database).parent
        if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:")
        else data_directory()
    )
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / ".schema.lock"):
        if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
            import_legacy_database(Path(parsed.database), configured)
        engine = make_engine(url)
        try:
            with engine.connect() as connection:
                tables = set(inspect(connection).get_table_names())
                if tables and "alembic_version" not in tables:
                    raise ValueError(
                        "У существующей базы нет истории миграций. Автоматическая перезапись запрещена"
                    )
                connection.rollback()
                config = Config(str(ROOT / "alembic.ini"))
                config.set_main_option("script_location", str(ROOT / "migrations"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        finally:
            engine.dispose()


def admin_password(directory, configured=""):
    from app.security import password_hash

    directory.mkdir(parents=True, exist_ok=True)
    record = directory / "admin-auth.json"
    access = directory / "admin-access.txt"
    with file_lock(directory / ".admin.lock"):
        saved = json.loads(record.read_text(encoding="utf-8")) if record.exists() else {}
        if configured and saved.get("hash") != configured:
            saved = {"hash": configured}
            if access.exists():
                access.unlink()
        if not saved:
            password = secrets.token_urlsafe(24)
            saved = {"hash": password_hash(password), "password": password}
        if not str(saved.get("hash", "")).startswith("scrypt:"):
            raise ValueError("Повреждены сохранённые данные входа в админку")
        temporary = directory / ("admin-auth-" + secrets.token_hex(6) + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                json.dump(saved, output)
            os.replace(temporary, record)
        finally:
            if temporary.exists():
                temporary.unlink()
        if saved.get("password"):
            access.write_text("Password: " + saved["password"] + "\n", encoding="utf-8")
            os.chmod(access, 0o600)
        return saved["hash"]
