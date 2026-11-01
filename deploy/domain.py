"""Attach a domain after initial deployment without replacing credentials or database settings."""

import argparse
import os
import secrets
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deploy.setup import validate_domain  # noqa: E402


def update_content(content, domain):
    domain = validate_domain(domain)
    changes = {"ADMIN_DOMAIN": domain, "ADMIN_ACCESS_MODE": "https", "COOKIE_SECURE": "true"}
    output = []
    for line in content.splitlines():
        key = line.partition("=")[0].strip()
        if key in changes:
            output.append(f"{key}='{changes.pop(key)}'")
        else:
            output.append(line)
    output.extend(f"{key}='{value}'" for key, value in changes.items())
    return "\n".join(output) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", help="admin.example.com")
    args = parser.parse_args()
    target = ROOT / ".env"
    if not target.exists():
        raise SystemExit("Сначала выполните python3 deploy/setup.py")
    content = target.read_text(encoding="utf-8")
    try:
        updated = update_content(content, args.domain)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    backup_dir = ROOT / "backups"
    backup_dir.mkdir(exist_ok=True, mode=0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_dir / f"env-before-domain-{stamp}-{secrets.token_hex(4)}.bak"
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
        output.write(content)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=ROOT, suffix=".tmp", delete=False
    ) as output:
        temp_path = Path(output.name)
        output.write(updated)
    try:
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    print("Домен сохранён. После настройки DNS выполните: bash deploy/manage.sh start")


if __name__ == "__main__":
    main()
