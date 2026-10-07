#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from storage import db, crypto
from storage.blobstore import BlobStore
import ingest
from console import config

def _list(conn) -> int:
    rows = conn.execute(
        "SELECT sha256, plaintext_bytes, ciphertext_bytes, created_at "
        "FROM blobs ORDER BY created_at DESC").fetchall()
    if not rows:
        print("no stored dumps.")
        return 0
    print(f"{'sha256':<20} {'plaintext':>12} {'sealed':>10}  stored")
    for sha, pt, ct, ts in rows:
        print(f"{sha[:18]:<20} {(pt or 0)/1e9:>9.2f}GB {(ct or 0)/1e9:>7.2f}GB  {ts:%Y-%m-%d %H:%M}")
    return 0

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Analyze a stored encrypted dump in Orochi")
    p.add_argument("sha", nargs="?", help="sha256 of the stored dump (prefix ok)")
    p.add_argument("--list", action="store_true", help="list stored dumps and exit")
    p.add_argument("--name", help="dump name shown in Orochi (default: auto)")
    args = p.parse_args(argv)

    conn = db.connect(config.DB_URL)
    try:
        if args.list or not args.sha:
            return _list(conn)

        master = crypto.load_master_key()
        matches = conn.execute(
            "SELECT sha256 FROM blobs WHERE sha256 LIKE %s", (args.sha + "%",)).fetchall()
        if not matches:
            print(f"no stored dump matches '{args.sha}'. Try --list.", file=sys.stderr)
            return 1
        if len(matches) > 1:
            print(f"'{args.sha}' is ambiguous ({len(matches)} matches) - use more chars.",
                  file=sys.stderr)
            return 1
        sha = matches[0][0]
        record = db.get_blob(conn, sha)

        store = BlobStore(config.BLOB_DIR, master)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        name = args.name or f"stored-{sha[:12]}-{stamp}"
        work = os.path.join(config.CAPTURE_DIR, f".reanalyze_{sha[:12]}.raw")
        os.makedirs(config.CAPTURE_DIR, exist_ok=True)

        print(f"decrypting {sha[:16]}... from the store ...")
        try:
            store.open_decrypted(record, work)
            print(f"handing to Orochi as '{name}' ...")
            ingest.hand_to_orochi(
                Path(work), orochi_dir=config.OROCHI_DIR, service="django_wsgi",
                media_dir="/media/uploads", name=name, os_name="Windows",
                author=config.OROCHI_AUTHOR)
        finally:
            try:
                os.remove(work)
            except OSError:
                pass
        print(f"\nDONE. Orochi is analyzing '{name}'. Open {config.OROCHI_URL}.")
        return 0
    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
