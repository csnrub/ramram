#!/usr/bin/env python3

from __future__ import annotations

import getpass
import os

from impacket.smbconnection import SMBConnection
from impacket.smb3structs import SMB2_SESSION_FLAG_ENCRYPT_DATA, SMB2_DIALECT_30

class SecureTransportError(Exception):
    pass

_DIALECT_NAMES = {
    0x0202: "SMB 2.0.2", 0x0210: "SMB 2.1",
    0x0300: "SMB 3.0", 0x0302: "SMB 3.0.2", 0x0311: "SMB 3.1.1",
}

def dialect_name(dialect: int) -> str:
    return _DIALECT_NAMES.get(dialect, f"{dialect:#06x}")

def connect(target_host: str, target_name: str, domain: str, username: str, *,
            use_kerberos: bool = True, kdc_host: str | None = None,
            password: str | None = None, aes_key: str | None = None) -> SMBConnection:
    conn = SMBConnection(target_name, target_host)

    if use_kerberos:
        have_cache = bool(os.environ.get("KRB5CCNAME"))
        use_cache = have_cache and password is None and not aes_key
        if not use_cache and not aes_key and password is None:
            password = getpass.getpass(
                f"Kerberos password for {domain}\\{username} "
                "(used only to obtain a ticket; never stored): ")
        conn.kerberosLogin(username, password or "", domain,
                           aesKey=aes_key or "", kdcHost=kdc_host, useCache=use_cache)
    else:
        if password is None:
            password = getpass.getpass("Password (NTLM; not shown, not stored): ")
        conn.login(username, password, domain)

    return conn

def assert_encrypted(conn: SMBConnection) -> int:
    dialect = conn.getDialect()
    if dialect < SMB2_DIALECT_30:
        raise SecureTransportError(
            f"negotiated {dialect_name(dialect)} - below SMB 3.0, so this target "
            "offers NO transport encryption. Refusing to move a memory image in "
            "cleartext. Upgrade the target's SMB stack or acquire it out-of-band.")

    smb = conn.getSMBServer()
    supports = bool(getattr(smb, "_Connection", {}).get("SupportsEncryption", False))
    active = bool(getattr(smb, "_Session", {}).get("SessionFlags", 0)
                  & SMB2_SESSION_FLAG_ENCRYPT_DATA)
    if not (supports and active):
        raise SecureTransportError(
            f"SMB3 session encryption is NOT active on {dialect_name(dialect)} "
            f"(target-supports={supports}, session-active={active}). Refusing to "
            "transfer the image in cleartext.")
    return dialect
