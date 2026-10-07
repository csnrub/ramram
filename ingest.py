#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from collection.collectors.winpmem import WinPmemCollector
from collection.interface import AcquireMode, Artifact
from storage import db

DEFAULT_DB_URL = "postgresql://rampart:rampart@127.0.0.1:5433/rampart_cases"
DEFAULT_OROCHI_DIR = "analysis/orochi"
DEFAULT_OROCHI_MEDIA = "/media/uploads"
DEFAULT_OROCHI_SERVICE = "django_wsgi"

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print(f"    $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kw)

def hand_to_orochi(image: Path, *, orochi_dir: str, service: str, media_dir: str,
                   name: str, os_name: str, author: str,
                   on_process=None, on_output=None) -> None:
    cwd = str(Path(orochi_dir).resolve())
    image = Path(image).resolve()
    compose = ["docker", "compose"]
    in_container = f"{media_dir}/{image.name}"

    def run(cmd: list[str]) -> None:
        header = "$ " + " ".join(cmd)
        print("    " + header)
        if on_output:
            on_output(header)
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        if on_process:
            on_process(proc)
        for line in proc.stdout:
            line = line.rstrip("\n")
            print("      " + line)
            if on_output:
                on_output(line)
        rc = proc.wait()
        if rc != 0:
            if on_output:
                on_output(f"[exited {rc}]")
            raise subprocess.CalledProcessError(rc, cmd)

    run(compose + ["exec", "-T", service, "mkdir", "-p", media_dir])
    run(compose + ["cp", str(image), f"{service}:{in_container}"])
    run(compose + ["run", "--rm", "-T", service, "python", "manage.py", "import_local",
                   "--filepath", in_container, "--name", name,
                   "--os", os_name, "--author", author])

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="samRAM Phase 1 ingest: verify -> register custody -> analyze in Orochi")
    p.add_argument("image", help="path to the captured raw memory image")
    p.add_argument("--name", required=True, help="dump name (shown in Orochi)")
    p.add_argument("--os", default="Windows", choices=["Windows", "Linux", "Mac"],
                   help="operating system of the image (default: Windows)")
    p.add_argument("--case", required=True, help="case name (created if new)")
    p.add_argument("--host", default="unknown", help="source hostname the image came from")
    p.add_argument("--acquirer", default="analyst",
                   help="who captured/ingested it (identity, not a credential)")
    p.add_argument("--collector", default="winpmem", help="collector that produced the image")
    p.add_argument("--expected-bytes", type=int, default=None,
                   help="expected image size (target RAM) for the truncation check")
    p.add_argument("--db-url", default=DEFAULT_DB_URL, help="samRAM case DB URL")
    p.add_argument("--orochi-dir", default=DEFAULT_OROCHI_DIR, help="path to the Orochi clone")
    p.add_argument("--orochi-author", default="admin",
                   help="existing Orochi username to own the dump")
    p.add_argument("--orochi-service", default=DEFAULT_OROCHI_SERVICE)
    p.add_argument("--orochi-media", default=DEFAULT_OROCHI_MEDIA)
    p.add_argument("--no-analyze", action="store_true",
                   help="register custody only; skip the Orochi handoff")
    p.add_argument("--reanalyze", action="store_true",
                   help="if the image is already registered, still (re-)run the "
                        "Orochi handoff - use when a prior handoff failed")
    args = p.parse_args(argv)

    image = Path(args.image).resolve()
    if not image.exists():
        print(f"ERROR: image not found: {image}", file=sys.stderr)
        return 2

    mtime = datetime.fromtimestamp(image.stat().st_mtime, tz=timezone.utc)
    artifact = Artifact(
        host_id=args.host,
        collector_name=args.collector,
        mode=AcquireMode.FULL_DUMP,
        started_at=mtime,
        ended_at=mtime,
        acquirer=args.acquirer,
        tool_name=args.collector,
        tool_version="manual-ingest",
        image_path=str(image),
    )
    if args.expected_bytes:
        artifact.extra["expected_bytes"] = str(args.expected_bytes)

    print(f"[1/3] verifying {image} ...")
    result = WinPmemCollector().verify(artifact)
    if not result.ok:
        print(f"    VERIFY FAILED: {result.detail}", file=sys.stderr)
        return 1
    print(f"    OK  sha256={result.sha256}")
    print(f"        {result.detail}")

    print("[2/3] registering custody in case DB ...")
    conn = db.connect(args.db_url)
    try:
        db.init_schema(conn)
        case_id = db.get_or_create_case(conn, args.case, opened_by=args.acquirer)
        host_id = db.get_or_create_host(conn, case_id, args.host, os_family=args.os.lower())
        acq_id, created = db.register_acquisition(
            conn, artifact, case_id=case_id, host_id=host_id,
            storage_ref=str(image), verified=result.parses)
    finally:
        conn.close()

    if not created:
        if not args.reanalyze:
            print(f"    already registered as {acq_id} - same SHA-256 seen before; "
                  "skipping re-analysis (idempotent). Pass --reanalyze to force the "
                  "Orochi handoff (e.g. if a prior handoff failed).")
            return 0
        print(f"    already registered as {acq_id}; --reanalyze set - re-running handoff.")
    else:
        print(f"    registered acquisition {acq_id} (case {case_id}, host {host_id})")

    if args.no_analyze:
        print("[3/3] --no-analyze set; custody recorded, skipping Orochi handoff.")
        return 0
    print(f"[3/3] handing image to Orochi ({args.orochi_dir}) ...")
    try:
        hand_to_orochi(image, orochi_dir=args.orochi_dir, service=args.orochi_service,
                       media_dir=args.orochi_media, name=args.name, os_name=args.os,
                       author=args.orochi_author)
    except subprocess.CalledProcessError as exc:
        print(f"    OROCHI HANDOFF FAILED (custody row {acq_id} IS recorded): {exc}",
              file=sys.stderr)
        return 1

    print(f"\nDONE. Custody row {acq_id} recorded; Orochi is analyzing '{args.name}'.")
    print("Open https://localhost, find the dump, watch windows.pslist complete.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
