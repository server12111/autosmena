#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ! -f .env ]]; then
    printf '%s\n' 'First run python3 deploy/setup.py and fill the API keys in .env.' >&2
    exit 1
fi
if grep -Eq '^DATABASE_URL=["\x27]?postgres|^POSTGRES_PASSWORD=' .env; then
    printf '%s\n' 'Existing PostgreSQL configuration detected. Preserve the old container/volume and migrate it separately before using the SQLite installer.' >&2
    exit 1
fi
domain="$(sed -n 's/^ADMIN_DOMAIN=//p' .env | tr -d "'\"\r")"
compose=(docker compose -p smena -f compose.yaml)
if [[ -n "$domain" ]]; then
    python3 -c 'import sys; from deploy.setup import validate_domain; validate_domain(sys.argv[1])' "$domain"
    compose+=(-f deploy/compose.https.yaml)
fi
backup() {
    mkdir -p backups
    chmod 700 backups
    local file="backups/smena-$(date -u +%Y%m%dT%H%M%SZ)-$$.db"
    (umask 077; "${compose[@]}" exec -T app python -c '
import sqlite3,sys,tempfile,os
from sqlalchemy.engine import make_url
from app.config import settings
source=make_url(settings.resolved_database_url)
if source.get_backend_name()!="sqlite": raise SystemExit("External database: use its own backup tools")
fd,path=tempfile.mkstemp();os.close(fd)
try:
    with sqlite3.connect(source.database) as db, sqlite3.connect(path) as copy: db.backup(copy)
    with open(path,"rb") as result:
        while chunk:=result.read(1024*1024): sys.stdout.buffer.write(chunk)
finally: os.unlink(path)
' > "$file.partial")
    mv -- "$file.partial" "$file"
    printf 'Backup: %s\n' "$file"
}
case "${1:-}" in
    start|update)
        "${compose[@]}" config --quiet
        "${compose[@]}" build
        if [[ "$1" == update ]]; then backup; fi
        "${compose[@]}" up -d --remove-orphans
        "${compose[@]}" ps
        if [[ -n "$domain" ]]; then printf 'Admin: https://%s\n' "$domain"; else
            printf '%s\n' 'ssh -N -L 8000:127.0.0.1:8000 USER@SERVER -> http://127.0.0.1:8000'
        fi
        ;;
    backup) backup ;;
    status) "${compose[@]}" ps ;;
    logs) "${compose[@]}" logs --tail=100 -f app ;;
    *) printf '%s\n' 'Usage: bash deploy/manage.sh {start|update|backup|status|logs}' >&2; exit 1 ;;
esac
