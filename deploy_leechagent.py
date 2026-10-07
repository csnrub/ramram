#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from impacket.dcerpc.v5 import scmr, transport
from impacket.smbconnection import SMBConnection

import secure_smb
from secure_smb import SecureTransportError
from remote_capture import PreflightError, check_encryption, check_local_admin, check_smb_reachable

C_SHARE = "C$"
REMOTE_DIR = "LeechAgent"
AGENT_FILES = ["leechagent.exe", "leechcore.dll", "winpmem_x64.sys", "vcruntime140.dll"]
SERVICE_NAME = "LeechAgent"
REMOTE_EXE = r"C:\LeechAgent\leechagent.exe"

_DEFAULT_AGENT_DIR = str(Path(__file__).resolve().parent
                         / "bin" / "LeechCore" / "files" / "agent" / "x64")

SERVICE_STATE = {1: "STOPPED", 2: "START_PENDING", 3: "STOP_PENDING", 4: "RUNNING"}

def _scm(conn: SMBConnection, target: str):
    rpc = transport.DCERPCTransportFactory(rf"ncacn_np:{target}[\pipe\svcctl]")
    rpc.set_smb_connection(conn)
    dce = rpc.get_dce_rpc()
    dce.connect()
    dce.bind(scmr.MSRPC_UUID_SCMR)
    return dce

def run_remote_command(conn: SMBConnection, target: str, command_line: str,
                       tag: str = "samram_deploy") -> None:
    try:
        from collection import transcript
        transcript.command(command_line, note="on %s as SYSTEM (service-exec, no stdout)" % target)
    except Exception:
        pass
    binpath = r"C:\Windows\System32\cmd.exe /Q /c " + command_line
    dce = _scm(conn, target)
    scm = scmr.hROpenSCManagerW(dce)["lpScHandle"]
    try:
        stale = scmr.hROpenServiceW(dce, scm, tag + "\x00")["lpServiceHandle"]
        scmr.hRDeleteService(dce, stale)
        scmr.hRCloseServiceHandle(dce, stale)
    except Exception:
        pass
    svc = scmr.hRCreateServiceW(
        dce, scm, tag + "\x00", tag + "\x00",
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

def query_service(conn: SMBConnection, target: str, name: str = SERVICE_NAME) -> int | None:
    dce = _scm(conn, target)
    try:
        scm = scmr.hROpenSCManagerW(dce)["lpScHandle"]
        svc = scmr.hROpenServiceW(dce, scm, name + "\x00")["lpServiceHandle"]
        state = scmr.hRQueryServiceStatus(dce, svc)["lpServiceStatus"]["dwCurrentState"]
        scmr.hRCloseServiceHandle(dce, svc)
        return state
    except Exception:
        return None
    finally:
        dce.disconnect()

def force_delete_service(conn: SMBConnection, target: str, name: str) -> bool:
    dce = _scm(conn, target)
    try:
        scm_h = scmr.hROpenSCManagerW(dce)["lpScHandle"]
        try:
            svc_h = scmr.hROpenServiceW(dce, scm_h, name + "\x00")["lpServiceHandle"]
        except Exception:
            return False
        try:
            scmr.hRControlService(dce, svc_h, scmr.SERVICE_CONTROL_STOP)
        except Exception:
            pass
        scmr.hRDeleteService(dce, svc_h)
        scmr.hRCloseServiceHandle(dce, svc_h)
        return True
    finally:
        dce.disconnect()

def control_service(conn: SMBConnection, target: str, action: str,
                    name: str = SERVICE_NAME) -> None:
    dce = _scm(conn, target)
    try:
        scm = scmr.hROpenSCManagerW(dce)["lpScHandle"]
        svc = scmr.hROpenServiceW(dce, scm, name + "\x00")["lpServiceHandle"]
        try:
            if action == "start":
                scmr.hRStartServiceW(dce, svc)
            elif action == "stop":
                scmr.hRControlService(dce, svc, scmr.SERVICE_CONTROL_STOP)
        except Exception:
            pass
        scmr.hRCloseServiceHandle(dce, svc)
    finally:
        dce.disconnect()

def deploy_files(conn: SMBConnection, agent_dir: Path) -> None:
    print(f"  creating C:\\{REMOTE_DIR} and copying agent files ...")
    try:
        conn.createDirectory(C_SHARE, REMOTE_DIR)
    except Exception:
        pass
    for fname in AGENT_FILES:
        local = agent_dir / fname
        if not local.exists():
            raise FileNotFoundError(f"missing agent file: {local}")
        with local.open("rb") as f:
            conn.putFile(C_SHARE, f"{REMOTE_DIR}\\{fname}", f.read)
        print(f"    -> C:\\{REMOTE_DIR}\\{fname}")

def wait_for_service(conn, target, *, want_registered=True, want_state=None,
                     timeout=60) -> int | None:
    waited = 0
    while waited < timeout:
        state = query_service(conn, target)
        if want_registered and want_state is None and state is not None:
            return state
        if want_state is not None and state == want_state:
            return state
        if not want_registered and state is None:
            return None
        time.sleep(3)
        waited += 3
    return query_service(conn, target)

def do_install(conn, target, agent_dir) -> int:
    deploy_files(conn, agent_dir)
    print(f"  registering the {SERVICE_NAME} service (running its -install) ...")
    run_remote_command(conn, target, f'"{REMOTE_EXE}" -install', tag="samram_lainstall")
    state = wait_for_service(conn, target, want_registered=True, timeout=60)
    if state is None:
        print("  ERROR: LeechAgent service did not register after -install.", file=sys.stderr)
        return 1
    print(f"  service registered (state: {SERVICE_STATE.get(state, state)}).")
    print("  starting LeechAgent ...")
    control_service(conn, target, "start")
    state = wait_for_service(conn, target, want_state=4, timeout=30)
    print(f"  LeechAgent is now: {SERVICE_STATE.get(state, state)}")
    if state == 4:
        print(f"\nDONE. LeechAgent deployed and RUNNING on {target}.")
        print("Next: connect from the laptop with MemProcFS -remotefs (NTLM).")
        return 0
    print("  WARNING: service registered but not confirmed RUNNING - check on the target.",
          file=sys.stderr)
    return 1

def do_uninstall(conn, target) -> int:
    print(f"  stopping + uninstalling {SERVICE_NAME} on {target} ...")
    control_service(conn, target, "stop")
    time.sleep(2)
    run_remote_command(conn, target, f'"{REMOTE_EXE}" -uninstall', tag="samram_launinstall")
    wait_for_service(conn, target, want_registered=False, timeout=30)
    for fname in AGENT_FILES + ["leechcore.dll"]:
        try:
            conn.deleteFile(C_SHARE, f"{REMOTE_DIR}\\{fname}")
        except Exception:
            pass
    try:
        conn.deleteDirectory(C_SHARE, REMOTE_DIR)
    except Exception:
        pass
    print("  teardown complete (service removed, files deleted).")
    return 0

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="samRAM Stage 2: deploy LeechAgent over SMB+SCM")
    p.add_argument("--target", required=True, help="target host NAME (Kerberos SPN) or IP")
    p.add_argument("--target-ip", help="target IP to connect to, if --target is a "
                                       "name DNS can't resolve from here")
    p.add_argument("--domain", required=True, help="AD domain (e.g. lab.local)")
    p.add_argument("--username", required=True, help="account with local admin on the target")
    p.add_argument("--ntlm", action="store_true",
                   help="authenticate with NTLM instead of Kerberos (opt-in; "
                        "transport is still SMB3-encrypted)")
    p.add_argument("--kdc-host", help="KDC/DC host for Kerberos (e.g. the DC IP)")
    p.add_argument("--aes-key", help="Kerberos AES key (hex) instead of a password")
    p.add_argument("--agent-dir", default=_DEFAULT_AGENT_DIR,
                   help="local folder holding leechagent.exe + leechcore.dll + winpmem_x64.sys")
    p.add_argument("--uninstall", action="store_true",
                   help="stop, uninstall, and remove LeechAgent from the target")
    args = p.parse_args(argv)

    target_ip = args.target_ip or args.target
    auth = "NTLM" if args.ntlm else "Kerberos"
    print(f"\nConnecting to {args.target} ({target_ip}) as "
          f"{args.domain}\\{args.username} [{auth}] ...")
    try:
        conn = secure_smb.connect(
            target_ip, args.target, args.domain, args.username,
            use_kerberos=not args.ntlm, kdc_host=args.kdc_host, aes_key=args.aes_key)
    except Exception as exc:
        print(f"LOGIN FAILED: {exc}", file=sys.stderr)
        return 1

    print("Preflight:")
    try:
        check_smb_reachable(conn)
        check_local_admin(conn)
        check_encryption(conn)
    except (PreflightError, SecureTransportError) as exc:
        print(f"PREFLIGHT FAILED: {exc}", file=sys.stderr)
        return 1

    if args.uninstall:
        return do_uninstall(conn, target_ip)

    agent_dir = Path(args.agent_dir)
    if not agent_dir.exists():
        print(f"ERROR: agent dir not found: {agent_dir}", file=sys.stderr)
        return 2
    try:
        return do_install(conn, target_ip, agent_dir)
    except Exception as exc:
        print(f"DEPLOY FAILED: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
