#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ ! -f .env ]]; then
    printf '%s\n' 'Сначала выполните python3 deploy/setup.py и заполните .env.' >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'Нужен Docker Engine с Docker Compose v2. Установите его по инструкции Docker для вашей ОС.' >&2
    exit 1
fi

# Read only the domain as data. Never execute/source a file containing API keys.
domain="$(sed -n 's/^ADMIN_DOMAIN=//p' .env | tr -d "'\"\r")"
compose=(docker compose -p smena -f compose.yaml)
if [[ -n "$domain" ]]; then
    if ! python3 -c 'import sys; from deploy.setup import validate_domain; validate_domain(sys.argv[1])' "$domain" 2>/dev/null; then
        printf '%s\n' 'Некорректный ADMIN_DOMAIN в .env.' >&2
        exit 1
    fi
    compose+=(-f deploy/compose.https.yaml)
fi

backup() {
    mkdir -p backups
    chmod 700 backups
    local file="backups/smena-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
    (umask 077; "${compose[@]}" exec -T db pg_dump -U smena -Fc smena > "$file.partial")
    mv -- "$file.partial" "$file"
    printf 'Резервная копия: %s\n' "$file"
}

start_services() {
    "${compose[@]}" up -d --no-deps --remove-orphans admin bot worker
    if [[ -n "$domain" ]]; then
        "${compose[@]}" up -d --no-deps caddy
        printf 'Админка: https://%s\n' "$domain"
    else
        printf '%s\n' 'На своём компьютере: ssh -N -L 8000:127.0.0.1:8000 USER@SERVER'
        printf '%s\n' 'Затем откройте http://127.0.0.1:8000. Админка доступна только через туннель.'
    fi
    "${compose[@]}" ps
}

case "${1:-}" in
    start|update)
        "${compose[@]}" config --quiet
        "${compose[@]}" build
        "${compose[@]}" run --rm --no-deps -e SMENA_PUBLIC_DOMAIN="$domain" admin python -c '
import os
from decimal import Decimal
from app.config import settings as s
s.validate_runtime()
checks = [(s.app_env == "production", "Server setup requires APP_ENV=production"),
          (bool(s.bot_token), "Set BOT_TOKEN in .env"),
          (bool(s.apicc_api_key), "Set APICC_API_KEY in .env")]
for valid, message in checks:
    if not valid:
        raise SystemExit(message)
try:
    rate = Decimal(s.apicc_usd_rate)
except Exception:
    raise SystemExit("Set verified APICC_USD_RATE in .env")
if not rate.is_finite() or rate <= 0:
    raise SystemExit("Set verified APICC_USD_RATE in .env")
if os.environ.get("SMENA_PUBLIC_DOMAIN"):
    if s.admin_access_mode != "https" or not s.cookie_secure:
        raise SystemExit("Domain requires ADMIN_ACCESS_MODE=https and COOKIE_SECURE=true")
elif s.admin_access_mode != "ssh_tunnel":
    raise SystemExit("Without a domain use ADMIN_ACCESS_MODE=ssh_tunnel")
print("Configuration checked")
'
        "${compose[@]}" up -d --wait --wait-timeout 120 db
        if [[ "$1" == update ]]; then
            backup
        fi
        "${compose[@]}" stop bot worker admin
        "${compose[@]}" run --rm --no-deps migrate
        start_services
        ;;
    backup)
        backup
        ;;
    status)
        "${compose[@]}" ps
        ;;
    logs)
        "${compose[@]}" logs --tail=100 -f admin bot worker
        ;;
    *)
        printf '%s\n' 'Использование: bash deploy/manage.sh {start|update|backup|status|logs}' >&2
        exit 1
        ;;
esac
