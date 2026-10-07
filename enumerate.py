#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys

from enumeration.ad import enumerate_computers

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="samRAM AD enumeration")
    p.add_argument("--dc", required=True, help="DC FQDN (Kerberos SPN), e.g. DC01.lab.local")
    p.add_argument("--dc-ip", required=True, help="DC IP (connect + KDC + DNS)")
    p.add_argument("--domain", required=True, help="AD domain, e.g. lab.local")
    p.add_argument("--principal", required=True, help=r"identity, DOMAIN\user or user@REALM")
    p.add_argument("--no-reachable", action="store_true", help="skip the SMB reachability probe")
    args = p.parse_args(argv)

    try:
        hosts = enumerate_computers(args.dc, args.dc_ip, args.domain, args.principal,
                                    check_reachable=not args.no_reachable)
    except Exception as exc:
        print(f"ENUMERATION FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  - DC FQDN must match its ldap/ SPN; clock in sync; ticket valid "
              "(getTGT). Try --no-reachable to isolate LDAP from the SMB probe.",
              file=sys.stderr)
        return 1

    print(f"\n{len(hosts)} computer objects\n")
    print(f"  {'NAME':<20} {'IP':<15} {'ROLE':<11} {'REACH':<6} {'MODES':<32} OS")
    for h in hosts:
        role = "DC" if h["is_dc"] else h["role"]
        reach = "up" if h["reachable"] else ("down" if h["reachable"] is not None else "?")
        modes = ", ".join(h["modes"]) or "-"
        print(f"  {h['name']:<20} {(h['ip'] or '?'):<15} {role:<11} {reach:<6} "
              f"{modes:<32} {h['os'] or ''}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
