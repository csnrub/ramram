# samRAM Changelog

---

## v3.1 — 2026-09-04

### Bug fix: MemProcFS fails to launch from paths containing spaces

`_run_live` in `console/live.py` was building the subprocess argv with
`shlex.split(config.MEMPROCFS)`. `shlex` treats spaces as token boundaries, so
running from a directory whose name contains a space (e.g. `samRAM redistributable`)
caused the path to be split mid-token — subprocess received the project directory as
`argv[0]` and tried to exec it, producing `[Errno 13] Permission denied: '<dir>'`.

Fixed by passing `config.MEMPROCFS` directly as `argv[0]` without shell splitting.
`MEMPROCFS` is a path, not a shell command string. The unused `import shlex` was also
removed.

Affected files: `console/live.py`.

---

### Page context panels — Memory Acquisition and Live Analysis

Both pages now open with an "How this works" information panel explaining the
acquisition process, the binaries involved, and the access requirements. Intended for
analysts and admins who need to understand or justify what the tool is doing on the wire
and on the target before authorising a collection.

**Memory Acquisition** panel covers:
- Kerberos/SMB3 authentication via impacket
- SCM service-exec primitive (same as PsExec) used to load WinPMEM as SYSTEM
- `winpmem_x64.sys` kernel driver maps physical address space on target
- Image streamed back over Admin$, SHA-256 verified, sealed encrypted at rest
- Driver and throwaway service entry removed on teardown
- Two-column breakdown: analyst machine (impacket, LeechCore, samRAM) vs target (winpmem driver only — no persistent agent)

**Live Analysis** panel covers:
- LeechAgent deployed via SMB3/SCM, started as orphaned SYSTEM process
- MemProcFS.exe (Windows binary) called via WSL interop, connects over RPC tcp/28473
- All memory reads transit the agent's encrypted RPC channel — nothing written to disk
- Two-column breakdown: analyst machine (impacket, MemProcFS.exe, vmm.dll, leechcore.dll) vs target (LeechAgent.exe, winpmem driver, agent deps in C:\LeechAgent\ — all removed on session close)
- Requirements note: local admin, Admin$/SCM, tcp/28473 reachable, Kerberos ticket

CSS for `.info-panel` added to `base.html` (shared; available across all pages).

Affected files: `console/templates/base.html`, `console/templates/collect.html`,
`console/templates/live.html`.

---

## v3.0 — 2026-09-04

### Agent Deploy State widget (Network & Connectivity)

New card on the Network & Connectivity page that probes how far a LeechAgent deploy
got on a target and offers a force-clean when the target is stuck.

**Probe — four checks run over one SMB3 connection:**

| Phase | What it checks |
|---|---|
| Phase 1 | SMB3 connect — Kerberos auth + encryption asserted |
| Phase 2 | Files staged — all four agent files present in `C:\LeechAgent\` |
| Phase 3 | Agent running — tcp/28473 RPC endpoint reachable |
| SCM stale entry | `samram_la_start` registered in SCM — causes `ERROR_SERVICE_EXISTS (0x431)` on next deploy |

Pill labels are neutral ("not staged", "not running") when the target is clean
before any deploy, so an idle target doesn't look broken.

**Verdict banner** appears after every probe and interprets the combination of
phase states:

- **Ready** — Phase 1 pass, no files, no agent, no stale SCM entry. Safe to open a live session.
- **Session active** — Phase 1 pass, files present, agent running, SCM clean.
- **Stuck** — Phase 1 pass, stale SCM entry or partial files with no running agent. Force clean required.
- **Cannot connect** — Phase 1 failed. Check credentials and network reachability.

**Auto-fill:**

- Principal is pre-filled from the active Kerberos ccache (`klist` / `KRB5CCNAME`).
- Hostname is auto-resolved via reverse DNS (`gethostbyaddr`) if left blank. The
  field note explains how the value was obtained.
- KDC is auto-detected by resolving the realm from the principal (`gethostbyname(realm)`).
  Human-readable error shown if the KDC is not reachable.

**Force Clean:**

- Enabled as soon as Phase 1 passes, regardless of other phase states.
- Sequence: `sc stop winpmem` → `sc delete winpmem` → `taskkill /F /IM leechagent.exe`
  (repeated after a short wait) → delete agent files in `C:\LeechAgent\` → delete
  `samram_la_start` SCM entry via explicit `hRDeleteService`.
- File deletion treats `OBJECT_NAME_NOT_FOUND` / `NOT_FOUND` as success — does not
  abort when files are already gone.
- `force_delete_service` failure is surfaced explicitly rather than swallowed.
- When cleanup is incomplete, a copy-paste command block is shown for an admin to run
  directly on the target:
  ```
  taskkill /F /IM leechagent.exe
  sc stop winpmem
  sc delete winpmem
  rmdir /s /q C:\LeechAgent
  ```
- The Force Clean result message stays visible after the automatic re-probe fires.

**Backend additions:**

- `connectivity.probe_agent_state(host, hostname, kdc, principal)` in `console/connectivity.py`
- `deploy_leechagent.force_delete_service(conn, target, name)` — explicit SCM `hRDeleteService`
  for a named entry; stops the service first if running, returns `False` if not found.
- `deploy_leechagent.run_remote_command` is now idempotent: deletes any stale SCM entry with
  the same tag before creating a new one, so a crashed cleanup on a previous run does not block
  the next one.
- Routes added to `console/app.py`:
  - `POST /connectivity/agent-state`
  - `POST /connectivity/agent-cleanup`

---

### Live session deploy failure — connectivity link

When opening a live session fails for any reason, the error now includes a direct link to
`/connectivity?host=<target>`. The link pre-fills the target IP in the Agent Deploy State
widget so the user can probe and force-clean without retyping anything, then return to open
the session.

This applies to both the streaming deploy console (JS error path) and the server-rendered
error banner (page-reload path).

---

### Phase 1: Kerberos / SMB hardening (carried from v2)

- SMB3 encryption asserted fail-closed via `secure_smb.assert_encrypted()`.
- Kerberos SPN requires a real hostname. If only an IP is given, hostname is resolved via
  rDNS before connecting so the SPN is valid (`cifs/<hostname>`, not `cifs/<ip>`).
- KDC detection: realm parsed from the principal, KDC found via `gethostbyname(realm)`.
  Human-readable error if the KDC is unreachable (`[Errno -2] Name or service not known` →
  "KDC not reachable — fill in the KDC / DC IP field and try again").

---

### winpmem teardown (carried from v2)

`cleanup_target` now stops and removes the winpmem driver before deleting agent files,
which releases the kernel-level file lock on `winpmem_x64.sys` that previously caused
cleanup to report files still locked even after the agent process was killed.
