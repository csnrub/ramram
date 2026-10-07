#!/usr/bin/env bash
# start.sh - turn ON samRAM. One command, from WSL:  ./start.sh
#
# Brings up the case DB (Postgres + Redis), waits for it to be healthy, then
# launches the web console. Idempotent: containers already up are left alone,
# and it re-attaches rather than double-starting. Secrets (the store master key,
# your Kerberos ticket) come from ./.env.local - never hard-coded here.
#
#   ./start.sh                 DB + console on http://localhost:8800
#   ./start.sh --orochi        also bring up the Orochi analysis stack
#   ./start.sh --port 9000     console on a different port
#   ./start.sh --no-docker     skip the Docker-Desktop auto-start (assume it's up)
#
# Stop the DB later with:  docker compose -f deploy/docker-compose.yml down
# (that keeps the volume - your case data survives). Ctrl-C stops the console.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- options ----------------------------------------------------------------
HOST=0.0.0.0
PORT=8800
START_DOCKER=1
WITH_OROCHI=0
VENV="${SAMRAM_VENV:-$HOME/samram-venv}"
DOCKER_DESKTOP="/mnt/c/Program Files/Docker/Docker/Docker Desktop.exe"
COMPOSE="docker compose -f deploy/docker-compose.yml"

while [ $# -gt 0 ]; do
    case "$1" in
        --orochi)    WITH_OROCHI=1 ;;
        --no-docker) START_DOCKER=0 ;;
        --port)      PORT="$2"; shift ;;
        --host)      HOST="$2"; shift ;;
        -h|--help)   sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

c()   { printf '\033[1;36m[samRAM]\033[0m %s\n' "$1"; }
ok()  { printf '\033[1;32m[samRAM]\033[0m %s\n' "$1"; }
warn(){ printf '\033[1;33m[samRAM]\033[0m %s\n' "$1"; }
die() { printf '\033[1;31m[samRAM]\033[0m %s\n' "$1" >&2; exit 1; }

# ---- 1. secrets + env -------------------------------------------------------
if [ -f .env.local ]; then
    set -a; . ./.env.local; set +a
    c "loaded secrets from .env.local"
else
    warn "no .env.local - copy .env.local.example to .env.local and set your key."
fi

# ---- 2. python venv ---------------------------------------------------------
[ -f "$VENV/bin/activate" ] || die "venv not found at $VENV (set SAMRAM_VENV to override)"
# shellcheck disable=SC1090
. "$VENV/bin/activate"
ok "venv active ($(python --version 2>&1))"

# ---- 3. docker reachable (auto-start Docker Desktop if needed) --------------
if ! docker info >/dev/null 2>&1; then
    if [ "$START_DOCKER" = 1 ] && [ -f "$DOCKER_DESKTOP" ]; then
        c "Docker not running - launching Docker Desktop..."
        "$DOCKER_DESKTOP" >/dev/null 2>&1 &
        printf '\033[1;36m[samRAM]\033[0m waiting for docker'
        for _ in $(seq 1 60); do
            docker info >/dev/null 2>&1 && break
            printf '.'; sleep 3
        done
        echo
    fi
    docker info >/dev/null 2>&1 || die "docker still unreachable - start Docker Desktop manually, then re-run."
fi
ok "docker reachable"

# ---- 4. warn on missing master key (console still runs without it) ----------
if [ -z "${SAMRAM_MASTER_KEY:-}" ]; then
    warn "SAMRAM_MASTER_KEY unset - Live Analysis works, but Storage/Vol3 decrypt + full_dump will fail."
fi
[ -n "${KRB5CCNAME:-}" ] && c "Kerberos ticket: $KRB5CCNAME" || warn "KRB5CCNAME unset - get a ticket in the console before collecting."

# ---- 5. case DB (postgres + redis) ------------------------------------------
c "bringing up case DB (postgres + redis)..."
$COMPOSE up -d postgres redis >/dev/null

c "waiting for postgres to report healthy..."
for _ in $(seq 1 40); do
    st="$(docker inspect --format '{{.State.Health.Status}}' samram-postgres 2>/dev/null || echo missing)"
    [ "$st" = healthy ] && break
    sleep 2
done
[ "$st" = healthy ] && ok "case DB healthy on 127.0.0.1:5433" \
                     || warn "postgres health = ${st:-unknown}; the console will show a DB banner if it can't connect."

# ---- 6. optional Orochi analysis stack --------------------------------------
if [ "$WITH_OROCHI" = 1 ]; then
    if [ -d analysis/orochi ]; then
        c "bringing up Orochi..."
        ( cd analysis/orochi && docker compose up -d >/dev/null ) && ok "Orochi up (https://localhost)" \
            || warn "Orochi failed to start - the console runs fine without it."
    else
        warn "analysis/orochi not found - skipping Orochi."
    fi
fi

# ---- 7. launch the console (foreground; Ctrl-C to stop) ---------------------
echo
ok "samRAM is ON - console: http://localhost:${PORT}   (Discover · Collect/Hunt · Live · Vol3 · Strings · Storage)"
c "Ctrl-C stops the console; the DB keeps running. Stop it with: $COMPOSE down"
echo
exec uvicorn console.app:app --host "$HOST" --port "$PORT" --reload
