#!/usr/bin/env python3

from __future__ import annotations

import argparse
import socket
import sys
import time

import secure_smb
import deploy_leechagent as la
from collection.collectors.leechagent import LEECHAGENT_RPC_PORT

def _parse_principal(principal: str) -> tuple[str, str]:
    if "\\" in principal:
        domain, user = principal.split("\\", 1)
        return domain, user
    if "@" in principal:
        user, realm = principal.split("@", 1)
        return realm, user
    return "", principal

def _endpoint_up(ip: str, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, LEECHAGENT_RPC_PORT), timeout=timeout):
            return True
    except OSError:
        return False

def _wait_down(ip: str, timeout: float = 20.0) -> bool:
    waited = 0.0
    while waited < timeout:
        if not _endpoint_up(ip):
            return True
        time.sleep(2)
        waited += 2
    return not _endpoint_up(ip)

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Force-clean an orphaned LeechAgent off a target")
    p.add_argument("--target", required=True, help="target IP to connect to")
    p.add_argument("--principal", required=True, help=r"Kerberos principal, e.g. CORP\administrator or admin@corp.example")
    p.add_argument("--hostname", help="target machine name for the SPN (defaults to --target)")
    p.add_argument("--kdc", help="KDC/DC host (e.g. the DC IP)")
    args = p.parse_args(argv)

    ip = args.target
    name = args.hostname or ip
    domain, user = _parse_principal(args.principal)

    print(f"\nConnecting to {name} ({ip}) as {domain}\\{user} [Kerberos] ...")
    try:
        conn = secure_smb.connect(ip, name, domain, user, use_kerberos=True, kdc_host=args.kdc)
        secure_smb.assert_encrypted(conn)
    except Exception as exc:
        print(f"LOGIN FAILED: {exc}", file=sys.stderr)
        return 1

    up = _endpoint_up(ip)
    print(f"  RPC endpoint {ip}:{LEECHAGENT_RPC_PORT} is currently {'UP' if up else 'down'}")

    print("  killing any running leechagent.exe (as SYSTEM) ...")
    try:
        la.run_remote_command(conn, ip, "taskkill /F /IM leechagent.exe", tag="samram_la_cleanup")
    except Exception as exc:
        print(f"    (taskkill step reported: {exc})")

    if up:
        print("  waiting for the agent to exit and release its file locks ...")
        if _wait_down(ip):
            print("    endpoint down - agent exited.")
        else:
            print("    WARNING: endpoint still up after 20s - a handle may still be held.",
                  file=sys.stderr)

    try:
        if la.query_service(conn, ip) is not None:
            print("  a LeechAgent service exists - stopping + uninstalling ...")
            la.control_service(conn, ip, "stop")
            time.sleep(2)
            la.run_remote_command(conn, ip, f'"{la.REMOTE_EXE}" -uninstall', tag="samram_la_cleanup_uninst")
            time.sleep(2)
    except Exception:
        pass

    print("  deleting staged files ...")
    remaining = set(la.AGENT_FILES)
    for i in range(6):
        for fname in list(remaining):
            try:
                conn.deleteFile(la.C_SHARE, f"{la.REMOTE_DIR}\\{fname}")
                remaining.discard(fname)
                print(f"    removed {fname}")
            except Exception:
                pass
        if not remaining:
            break
        if i < 5:
            time.sleep(2)
    try:
        conn.deleteDirectory(la.C_SHARE, la.REMOTE_DIR)
    except Exception:
        pass

    try:
        conn.listPath(la.C_SHARE, la.REMOTE_DIR)
        dir_present = True
    except Exception:
        dir_present = False

    if remaining or dir_present:
        print(f"\nINCOMPLETE: still present -> files={sorted(remaining) or 'none'}, "
              f"dir={'C:\\'+la.REMOTE_DIR if dir_present else 'removed'}", file=sys.stderr)
        print("If the endpoint is still UP, the agent didn't die - check the host "
              "(RDP: taskkill /F /IM leechagent.exe, then rmdir /s /q C:\\LeechAgent).",
              file=sys.stderr)
        return 1

    print(f"\nDONE. {ip} is clean - no agent running, C:\\{la.REMOTE_DIR} removed. Safe to re-open a live session.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
