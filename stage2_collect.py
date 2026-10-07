#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys

from collection.dispatcher import Job, run_job
from collection.interface import AcquireMode, Cred, Host
from collection.collectors.leechagent import LeechAgentCollector

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="samRAM Stage 2 live remote collection")
    p.add_argument("--target", required=True, help="target IP to connect to")
    p.add_argument("--hostname", help="target machine name for the Kerberos SPN "
                                      "(cifs/<name>); defaults to --target")
    p.add_argument("--principal", required=True,
                   help=r"Kerberos identity, DOMAIN\\user or user@REALM")
    p.add_argument("--kdc", help="KDC/DC host for Kerberos (e.g. the DC IP)")
    p.add_argument("--mode", choices=["remote_fs", "live_query"], default="remote_fs",
                   help="acquire mode (default: remote_fs - dumpless live analysis)")
    p.add_argument("--agent-dir", default="LeechCore/files/agent/x64",
                   help="local folder holding the built LeechAgent payload")
    p.add_argument("--memprocfs", help="Windows MemProcFS.exe command for the read "
                                       "leg (else endpoint-only verify)")
    p.add_argument("--secure-service", action="store_true",
                   help="use the authenticated persistent service instead of "
                        "insecure-interactive (KNOWN LIMITATION on Win11 26200)")
    args = p.parse_args(argv)

    host = Host(
        identifier=args.target,
        os_family="windows",
        fingerprint={k: v for k, v in (
            ("hostname", args.hostname or args.target),
            ("kdc", args.kdc or ""),
        ) if v},
    )
    creds = Cred(principal=args.principal)
    mode = AcquireMode(args.mode)
    collector = LeechAgentCollector(
        agent_dir=args.agent_dir, memprocfs_cmd=args.memprocfs,
        secure_service=args.secure_service)

    print(f"Stage 2: {mode.value} on {host.identifier} as {creds.principal} "
          f"[{'authenticated service' if args.secure_service else 'insecure interactive'}]")
    result = run_job(Job(host=host, creds=creds, chosen_method=mode), collector=collector)

    print("\n=== result ===")
    print(f"host : {result.host_id}")
    print(f"ok   : {result.ok}")
    if result.verify:
        print(f"verify: parses={result.verify.parses}  {result.verify.detail}")
    if result.artifact:
        findings_json = result.artifact.extra.pop("findings", None) if result.artifact.extra else None
        print("sidecar:")
        for k, v in result.artifact.sidecar().items():
            print(f"    {k}: {v}")
        if findings_json is not None:
            import json as _json
            findings = _json.loads(findings_json)
            print(f"\n=== triage findings ({len(findings)}) ===")
            if not findings:
                print("    (none flagged)")
            for f in findings:
                pid = f.get("pid")
                print(f"    [{f['severity'].upper():6}] {f['rule']:13} "
                      f"pid={pid} {f.get('name','')}")
                print(f"             {f['detail']}")
    if not result.ok:
        print(f"detail: {result.detail}", file=sys.stderr)
    return 0 if result.ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
