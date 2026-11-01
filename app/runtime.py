"""One container: prepare persistent DB, then supervise admin, bot and payment worker."""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.config import settings
from app.storage import admin_password, data_directory, hosted, prepare_database

log = logging.getLogger("smena.runtime")


def prepare():
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    prepare_database(settings.resolved_database_url, settings.database_url)
    settings.admin_password_hash = admin_password(directory, settings.admin_password_hash)
    # A mounted host directory may initially be root-owned. Drop privileges after bootstrap.
    if os.name != "nt" and os.geteuid() == 0:
        import pwd

        try:
            account = pwd.getpwnam("smena")
        except KeyError:
            account = None
        if account:
            os.chown(directory, account.pw_uid, account.pw_gid)
            from sqlalchemy.engine import make_url

            parsed = make_url(settings.resolved_database_url)
            names = [".schema.lock", ".admin.lock", "admin-auth.json", "admin-access.txt"]
            if parsed.get_backend_name() == "sqlite" and parsed.database:
                db_path = Path(parsed.database)
                if db_path.parent.resolve() == directory.resolve():
                    names += [db_path.name, db_path.name + "-wal", db_path.name + "-shm"]
            for name in names:
                path = directory / name
                if path.exists():
                    os.chown(path, account.pw_uid, account.pw_gid)
                    if name.endswith((".db", "-wal", "-shm")):
                        os.chmod(path, 0o600)
            os.setgroups([])
            os.setgid(account.pw_gid)
            os.setuid(account.pw_uid)
    settings.validate_runtime()
    if (directory / "admin-access.txt").exists():
        log.info("Persistent database prepared; admin access: %s", directory / "admin-access.txt")
    else:
        log.info("Persistent database prepared; existing admin password preserved")


def service_commands():
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("Некорректный порт хостинга")
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.api:app",
            "--host",
            "0.0.0.0" if hosted() else "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        [sys.executable, "-m", "app.worker"],
    ]
    if settings.bot_token:
        commands.append([sys.executable, "-m", "app.bot"])
    return commands


def supervise(commands):
    stopping = False
    previous = {}
    processes = []

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, stop)
        env = os.environ.copy()
        env["ADMIN_PASSWORD_HASH"] = settings.admin_password_hash
        for command in commands:
            process = subprocess.Popen(command, env=env)
            processes.append(process)
            log.info("Service started: %s pid=%s", command[2] if len(command) > 2 else "app", process.pid)
        while not stopping:
            for process in processes:
                code = process.poll()
                if code is not None:
                    raise RuntimeError(
                        f"Одна из служб остановилась (exit={code}); контейнер будет перезапущен"
                    )
            time.sleep(0.2)
    finally:
        for process in processes:
            if process.poll() is None:
                stop_process(process)
        deadline = time.monotonic() + 15
        for process in processes:
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def stop_process(process):
    if os.name == "nt":
        # Windows venv launchers can have a second Python process underneath them.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        process.terminate()


def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Initialize/migrate storage without connecting Telegram"
    )
    args = parser.parse_args()
    prepare()
    if args.check:
        log.info("Startup check passed")
        return
    supervise(service_commands())
