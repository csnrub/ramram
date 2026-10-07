#!/usr/bin/env bash
# setup.sh - one-time bootstrap for a fresh clone. From WSL:  bash setup.sh
#
# Creates the Python venv, installs samRAM and its deps, and verifies the bundled
# native binaries. There is NOTHING to compile: MemProcFS, LeechCore/LeechAgent, and
# go-winpmem ship no easy prebuilts, so their compiled binaries are committed to this
# repo. A clone is self-contained. After this, run ./start.sh.

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV="${SAMRAM_VENV:-$HOME/samram-venv}"

log()  { printf '\033[1;36m[setup]\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m[setup]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[setup]\033[0m %s\n' "$1"; }
die()  { printf '\033[1;31m[setup]\033[0m %s\n' "$1" >&2; exit 1; }

# ---- 1. python venv + install ----------------------------------------------
command -v python3 >/dev/null || die "python3 not found (need 3.12+)."
if [ ! -d "$VENV" ]; then
    log "creating venv at $VENV"
    python3 -m venv "$VENV" || die "could not create venv."
fi
# shellcheck disable=SC1090
. "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip
log "installing samRAM + deps (console, analysis, remote)..."
pip install --quiet -e '.[console,analysis,remote]' || die "pip install failed."
ok "python env ready ($(python --version 2>&1))"

# ---- 2. verify bundled native binaries -------------------------------------
# Also catches a corrupt/incomplete checkout (the classic "the .exe is broken").
log "verifying bundled native binaries..."
if sha256sum -c --quiet deploy/vendor.sha256 >/dev/null 2>&1; then
    ok "all bundled binaries present and intact"
else
    warn "some bundled binaries are missing or changed:"
    sha256sum -c deploy/vendor.sha256 2>/dev/null | grep -vi ': OK$' || true
    warn "if these are MISSING, the clone is incomplete; if CHANGED, the checkout is"
    warn "corrupt (confirm .gitattributes was respected). samRAM still installs, but"
    warn "collection / fileless analysis needs these binaries."
fi

# ---- 3. local env file ------------------------------------------------------
if [ ! -f .env.local ]; then
    cp .env.local.example .env.local
    warn "created .env.local - set SAMRAM_MASTER_KEY + KRB5CCNAME before running."
fi

echo
ok "setup complete. Next:"
echo "    source $VENV/bin/activate"
echo "    ./start.sh                 # brings up the case DB + console on :8800"
