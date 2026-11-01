"""Prepare a fresh server .env with only Python's standard library."""

import getpass
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.security import password_hash  # noqa: E402


def validate_domain(domain):
    try:
        normalized = domain.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Некорректный домен") from None
    labels = normalized.split(".")
    if (
        len(normalized) > 253
        or len(labels) < 2
        or not any(c.isalpha() for c in labels[-1])
        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
    ):
        raise ValueError("Укажите домен без https://, пути и порта")
    return normalized


def build_env(password, domain=""):
    if not 12 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов")
    domain = validate_domain(domain) if domain else ""
    db_password = secrets.token_hex(24)
    replacements = {
        "ADMIN_DOMAIN": domain.lower(),
        "ADMIN_ACCESS_MODE": "https" if domain else "ssh_tunnel",
        "COOKIE_SECURE": "true" if domain else "false",
        "ADMIN_PASSWORD_HASH": password_hash(password),
        "POSTGRES_PASSWORD": db_password,
        "DATABASE_URL": f"postgresql+psycopg://smena:{db_password}@db:5432/smena",
    }
    lines = []
    for line in (ROOT / "deploy/.env.server.example").read_text(encoding="utf-8").splitlines():
        key = line.partition("=")[0]
        lines.append(f"{key}='{replacements[key]}'" if key in replacements else line)
    return "\n".join(lines) + "\n"


def main():
    target = ROOT / ".env"
    if target.exists():
        raise SystemExit(".env уже существует и не будет перезаписан. Настройте его вручную.")
    domain = input("Домен админки (Enter — доступ через SSH-туннель): ").strip()
    password = getpass.getpass("Пароль админки (минимум 12 символов): ")
    if password != getpass.getpass("Повторите пароль: "):
        raise SystemExit("Пароли не совпадают")
    try:
        content = build_env(password, domain)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    # Exclusive creation prevents overwriting a configuration created during the prompts.
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    print(
        "Создан .env. Добавьте ключи и подтверждённый курс APICC_USD_RATE, затем: bash deploy/manage.sh start"
    )


if __name__ == "__main__":
    main()
