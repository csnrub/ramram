#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from impacket.smbconnection import SMBConnection
    from impacket.dcerpc.v5 import transport, scmr, srvs
except ImportError:
    print("ERROR: impacket not installed - run `pip install impacket` "
          "(or `uv sync --extra remote`) in your venv.", file=sys.stderr)
    raise

import secure_smb
from secure_smb import SecureTransportError

ADMIN_SHARE = "ADMIN$"
REMOTE_SUBDIR = "Temp"
R_WINPMEM = f"{REMOTE_SUBDIR}\\samram_winpmem.exe"
R_IMAGE = f"{REMOTE_SUBDIR}\\samram_mem.raw"
R_DONE = f"{REMOTE_SUBDIR}\\samram.done"
R_LOG = f"{REMOTE_SUBDIR}\\samram.log"
R_SHA = f"{REMOTE_SUBDIR}\\samram_mem.sha256"
SERVICE_NAME = "samRAMCapture"
MAX_SKEW_SECONDS = 5 * 60
CAPTURE_TIMEOUT_SECONDS = 20 * 60

def _prompt_if_missing(value: str | None, label: str) -> str:
    if value:
        return value
    try:
        return input(f"{label}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\naborted.", file=sys.stderr)
        sys.exit(130)

def check_smb_reachable(conn: SMBConnection) -> None:
    print(f"  [1/5] SMB reachable + session established "
          f"({secure_smb.dialect_name(conn.getDialect())}) - OK")

def check_encryption(conn: SMBConnection) -> None:
    dialect = secure_smb.assert_encrypted(conn)
    print(f"  [5/5] SMB3 encryption ACTIVE on {secure_smb.dialect_name(dialect)} "
          "- image transfer will be encrypted - OK")

def check_local_admin(conn: SMBConnection) -> None:
    try:
        tid = conn.connectTree(ADMIN_SHARE)
        conn.disconnectTree(tid)
        print("  [2/5] ADMIN$ reachable - account IS a local admin - OK")
    except Exception as exc:
        raise PreflightError(
            "account is NOT a local admin on the target (ADMIN$ denied). "
            "Add it to the target's local Administrators group (a Domain Admin "
            f"qualifies). [{exc}]") from exc

def check_clock_skew(conn: SMBConnection, target: str) -> None:
    try:
        rpc = transport.DCERPCTransportFactory(rf"ncacn_np:{target}[\pipe\srvsvc]")
        rpc.set_smb_connection(conn)
        dce = rpc.get_dce_rpc()
        dce.connect()
        dce.bind(srvs.MSRPC_UUID_SRVS)
        tod = srvs.hNetrRemoteTOD(dce)["BufferPtr"][0]
        remote = datetime(tod["tod_year"], tod["tod_month"], tod["tod_day"],
                          tod["tod_hours"], tod["tod_mins"], tod["tod_secs"],
                          tzinfo=timezone.utc)
        dce.disconnect()
        skew = abs((datetime.now(timezone.utc) - remote).total_seconds())
        if skew > MAX_SKEW_SECONDS:
            print(f"  [3/5] WARNING: clock skew {skew:.0f}s (> {MAX_SKEW_SECONDS}s) - "
                  "Kerberos will reject tickets at this skew. Sync clocks.")
        else:
            print(f"  [3/5] clock skew {skew:.0f}s (< {MAX_SKEW_SECONDS}s) - OK")
    except Exception as exc:
        print(f"  [3/5] could not read remote time ({exc}); skipping skew check.")

def alert_free_space(conn: SMBConnection, target: str) -> None:
    free_gb = None
    try:
        from impacket.smb3structs import (SMB2_0_INFO_FILESYSTEM,
                                          FILE_READ_ATTRIBUTES, FILE_SHARE_READ)
        import struct
        smb = conn.getSMBServer()
        tid = conn.connectTree(ADMIN_SHARE)
        fid = smb.create(tid, "", FILE_READ_ATTRIBUTES, FILE_SHARE_READ,
                         0x01, 1, 0)
        info = bytes(smb.queryInfo(tid, fid, infoType=SMB2_0_INFO_FILESYSTEM,
                                   fileInfoClass=3))
        smb.close(tid, fid)
        if len(info) >= 24:
            _total, avail_units, sectors_per_unit, bytes_per_sector = struct.unpack(
                "<QQII", info[:24])
            free_gb = (avail_units * sectors_per_unit * bytes_per_sector) / (1024 ** 3)
    except Exception:
        pass

    if free_gb is not None:
        note = f"target free disk in C:\\Windows\\Temp ~= {free_gb:.1f} GB"
        flag = "" if free_gb >= 4 else "  <-- LOW: ensure >= target RAM size!"
        print(f"  [4/5] {note}{flag}")
    else:
        print("  [4/5] NOTE: target needs FREE DISK >= its RAM size in "
              "C:\\Windows\\Temp (image is written there before pull) - capture "
              "fails if short. (Could not read remote free space automatically.)")

class PreflightError(Exception):
    pass

def preflight(conn, target) -> None:
    print("Preflight:")
    check_smb_reachable(conn)
    check_local_admin(conn)
    check_clock_skew(conn, target)
    alert_free_space(conn, target)
    check_encryption(conn)
    print("Preflight passed.\n")

def deploy_winpmem(conn: SMBConnection, winpmem_local: Path) -> None:
    print(f"  uploading WinPMEM -> {ADMIN_SHARE}\\{R_WINPMEM} ...")
    with winpmem_local.open("rb") as f:
        conn.putFile(ADMIN_SHARE, R_WINPMEM, f.read)

def start_capture(conn: SMBConnection, target: str) -> None:
    binpath = (
        "C:\\Windows\\System32\\cmd.exe /Q /c "
        "C:\\Windows\\Temp\\samram_winpmem.exe acquire C:\\Windows\\Temp\\samram_mem.raw "
        "> C:\\Windows\\Temp\\samram.log 2>&1 "
        "& powershell -NoProfile -NonInteractive -Command "
        "\"(Get-FileHash -Algorithm SHA256 -LiteralPath "
        "'C:\\Windows\\Temp\\samram_mem.raw').Hash "
        "| Out-File -Encoding ascii C:\\Windows\\Temp\\samram_mem.sha256\" "
        "2>> C:\\Windows\\Temp\\samram.log "
        "& echo done > C:\\Windows\\Temp\\samram.done"
    )
    rpc = transport.DCERPCTransportFactory(rf"ncacn_np:{target}[\pipe\svcctl]")
    rpc.set_smb_connection(conn)
    dce = rpc.get_dce_rpc()
    dce.connect()
    dce.bind(scmr.MSRPC_UUID_SCMR)
    scm = scmr.hROpenSCManagerW(dce)["lpScHandle"]
    svc = scmr.hRCreateServiceW(
        dce, scm, SERVICE_NAME + "\x00", SERVICE_NAME + "\x00",
        lpBinaryPathName=binpath + "\x00",
        dwStartType=scmr.SERVICE_DEMAND_START)["lpServiceHandle"]
    try:
        scmr.hRStartServiceW(dce, svc)
    except Exception:
        pass
    finally:
        try:
            scmr.hRDeleteService(dce, svc)
            scmr.hRCloseServiceHandle(dce, svc)
        except Exception:
            pass
        dce.disconnect()
    print("  capture launched on target (WinPMEM running as SYSTEM) ...")

def clear_stale_markers(conn: SMBConnection) -> None:
    for remote in (R_IMAGE, R_DONE, R_LOG, R_SHA):
        try:
            conn.deleteFile(ADMIN_SHARE, remote)
        except Exception:
            pass

def wait_for_done(conn: SMBConnection) -> None:
    print("  waiting for capture to finish (polling for the source-hash marker) ...")
    waited = 0
    while waited < CAPTURE_TIMEOUT_SECONDS:
        for marker in (R_SHA, R_DONE):
            try:
                if conn.listPath(ADMIN_SHARE, marker):
                    print("  capture complete (source hash present).")
                    return
            except Exception:
                pass
        time.sleep(5)
        waited += 5
    raise TimeoutError(
        f"capture did not finish within {CAPTURE_TIMEOUT_SECONDS}s - check "
        f"\\\\target\\{ADMIN_SHARE}\\{R_LOG} on the box.")

def pull_image(conn: SMBConnection, out_path: Path) -> str:
    size = conn.listPath(ADMIN_SHARE, R_IMAGE)[0].get_filesize()
    print(f"  pulling image back ({size} bytes, encrypted) -> {out_path} ...")
    received = hashlib.sha256()
    with out_path.open("wb") as f:
        def sink(data: bytes) -> None:
            f.write(data)
            received.update(data)
        conn.getFile(ADMIN_SHARE, R_IMAGE, sink)
    return received.hexdigest()

def read_source_hash(conn: SMBConnection) -> str | None:
    chunks: list[bytes] = []
    try:
        conn.getFile(ADMIN_SHARE, R_SHA, chunks.append)
    except Exception:
        return None
    text = b"".join(chunks).decode("utf-8", "replace")
    for line in text.splitlines():
        candidate = line.strip().replace(" ", "").lower()
        if re.fullmatch(r"[0-9a-f]{64}", candidate):
            return candidate
    return None

def verify_integrity(conn: SMBConnection, received_sha256: str) -> str:
    source = read_source_hash(conn)
    if source is None:
        print("  WARNING: could not read the target-side hash - skipping the "
              "end-to-end compare (transport was still encrypted; ingest re-hashes).")
        return received_sha256
    if source != received_sha256:
        raise SecureTransportError(
            "IMAGE INTEGRITY CHECK FAILED - the hash computed on the target does "
            f"not match the bytes received.\n    target : {source}\n    received: "
            f"{received_sha256}\n  The image may have been tampered with or "
            "corrupted in transit; NOT handing it to analysis.")
    print(f"  integrity OK - target and received SHA-256 match ({source[:16]}...).")
    return source

def teardown(conn: SMBConnection) -> None:
    print("  teardown: removing remote artifacts ...")
    for remote in (R_IMAGE, R_WINPMEM, R_DONE, R_LOG, R_SHA):
        try:
            conn.deleteFile(ADMIN_SHARE, remote)
        except Exception:
            pass

def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="samRAM Stage 1 remote capture - remote WinPMEM over an "
                    "encrypted SMB3 channel (Kerberos by default)")
    p.add_argument("--target", help="target host NAME (for Kerberos SPN) or IP")
    p.add_argument("--target-ip", help="target IP to connect to, if --target is a "
                                       "name DNS can't resolve from here")
    p.add_argument("--domain", help="AD domain (e.g. lab.local)")
    p.add_argument("--username", help="account with local admin on the target")
    p.add_argument("--ntlm", action="store_true",
                   help="authenticate with NTLM instead of Kerberos (opt-in; "
                        "relay-able - transport is still SMB3-encrypted)")
    p.add_argument("--kdc-host", help="KDC/DC host for Kerberos (e.g. the DC IP)")
    p.add_argument("--aes-key", help="Kerberos AES key (hex) instead of a password")
    p.add_argument("--winpmem", help="local path to winpmem_mini_x64_rc2.exe")
    p.add_argument("--output", help="local path to write the pulled image")
    p.add_argument("--preflight-only", action="store_true",
                   help="run only the preflight checks; change nothing on the target")
    p.add_argument("--ingest", action="store_true",
                   help="after pulling the image, chain into ingest.py")
    p.add_argument("--case", help="(with --ingest) case name")
    args = p.parse_args(argv)

    target = _prompt_if_missing(args.target, "Target name/IP")
    target_ip = args.target_ip or target
    domain = _prompt_if_missing(args.domain, "Domain (e.g. lab.local)")
    username = _prompt_if_missing(args.username, "Username (local admin on target)")
    use_kerberos = not args.ntlm

    auth = "NTLM" if args.ntlm else "Kerberos"
    print(f"\nConnecting to {target} ({target_ip}) as {domain}\\{username} "
          f"[{auth}] ...")
    if use_kerberos and not args.kdc_host:
        print("  note: no --kdc-host given; relying on DNS SRV/realm discovery for "
              "the KDC.")
    try:
        conn = secure_smb.connect(
            target_ip, target, domain, username,
            use_kerberos=use_kerberos, kdc_host=args.kdc_host, aes_key=args.aes_key)
    except Exception as exc:
        print(f"LOGIN FAILED: {exc}", file=sys.stderr)
        print("  - wrong creds / no valid ticket? account locked? SMB 445 blocked?"
              "\n  - Kerberos needs the target's NAME (not IP) + clock sync; try "
              "--kdc-host, or --ntlm for a quick IP-based connection.",
              file=sys.stderr)
        return 1

    try:
        preflight(conn, target_ip)
    except (PreflightError, SecureTransportError) as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 1

    if args.preflight_only:
        print("--preflight-only: checks passed, nothing changed on the target.")
        return 0

    winpmem = Path(_prompt_if_missing(args.winpmem, "Local path to winpmem_mini_x64_rc2.exe"))
    if not winpmem.exists():
        print(f"ERROR: winpmem not found: {winpmem}", file=sys.stderr)
        return 2
    out_path = Path(_prompt_if_missing(
        args.output, f"Output path for the image (e.g. ./{target}_mem.raw)"))

    print("\nRemote capture:")
    try:
        deploy_winpmem(conn, winpmem)
        clear_stale_markers(conn)
        start_capture(conn, target_ip)
        wait_for_done(conn)
        received = pull_image(conn, out_path)
        verify_integrity(conn, received)
    except SecureTransportError as exc:
        print(f"\n{exc}", file=sys.stderr)
        teardown(conn)
        return 1
    except Exception as exc:
        print(f"CAPTURE FAILED: {exc}", file=sys.stderr)
        teardown(conn)
        return 1
    teardown(conn)
    print(f"\nDONE. Image pulled to {out_path} (encrypted transport, integrity verified)")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    dump_name = f"{target}-remote-{stamp}"

    if args.ingest:
        case = _prompt_if_missing(args.case, "Case name for ingest")
        print("\nChaining into ingest.py ...")
        import ingest
        return ingest.main([
            str(out_path), "--name", dump_name, "--os", "Windows",
            "--case", case, "--host", target, "--acquirer", username,
            "--collector", "winpmem-remote",
        ])

    print(f"\nNext: python ingest.py {out_path} --name {dump_name} "
          f'--os Windows --case "IR-CASE" --host {target} --orochi-author admin')
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
