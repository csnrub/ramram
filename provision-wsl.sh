#!/usr/bin/env bash
# provision-wsl.sh - catch-all provisioning for the WSL / Ubuntu side of samRAM.
#
# Checks what's already on the machine and installs ONLY what's missing, then hands
# off to setup.sh (venv + samRAM + binary verify). Idempotent - safe to run as many
# times as you like on a laptop in any state.
#
#   bash provision-wsl.sh
#
# Run the Windows layer first if WSL/Docker itself is missing (see provision-windows.ps1).

set -uo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\033[1;36m[provision]\033[0m %s\n' "$1"; }
ok()   { printf '\033[1;32m[provision]\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m[provision]\033[0m %s\n' "$1"; }

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

need=""
check() {  # $1 probe command, $2 apt package, $3 label
    if command -v "$1" >/dev/null 2>&1; then
        ok "$3 present"
    else
        warn "$3 missing -> will install $2"
        need="$need $2"
    fi
}

if command -v apt-get >/dev/null 2>&1; then
    log "checking system tools..."
    check python3  python3         "Python 3"
    check pip3     python3-pip     "pip"
    check strings  binutils        "strings (binutils, for the Strings page)"
    check nc       netcat-openbsd  "netcat (for connectivity checks)"
    check nslookup dnsutils        "nslookup (dnsutils)"
    check ping     iputils-ping    "ping"
    check klist    krb5-user       "Kerberos client tools"
    check git      git             "git"
    check gcc      build-essential "C toolchain (for building any non-wheel deps)"
    # No standalone command for these - probe differently.
    python3 -c 'import venv' 2>/dev/null && ok "python3-venv present" \
        || { warn "python3-venv missing -> will install"; need="$need python3-venv"; }
    dpkg -s python3-dev >/dev/null 2>&1 && ok "python3-dev present" \
        || { warn "python3-dev missing -> will install"; need="$need python3-dev"; }

    if [ -n "$need" ]; then
        log "installing:$need"
        $SUDO apt-get update -qq
        $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq $need \
            && ok "system packages installed" \
            || warn "apt install reported problems - review the output above."
    else
        ok "all system tools already present - nothing to install"
    fi
else
    warn "no apt-get on this distro. Install the equivalents of: python3 python3-venv"
    warn "python3-pip python3-dev binutils netcat dnsutils iputils-ping krb5-user git build-essential"
fi

# Docker runs on the Windows side (Docker Desktop); from WSL we only check reachability.
if docker info >/dev/null 2>&1; then
    ok "Docker reachable"
else
    warn "Docker not reachable. Install/launch Docker Desktop on Windows and enable"
    warn "Settings > Resources > WSL Integration for this distro (see provision-windows.ps1)."
fi

# Hand off to the project bootstrap (venv + samRAM deps + binary verification).
log "running setup.sh..."
bash setup.sh

echo
ok "WSL provisioning complete. Start samRAM with:  ./start.sh"
