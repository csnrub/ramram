# samRAM v3.1 — redistributable

A self-contained copy of samRAM ready for deployment. Includes the full source, the
analysis stack integration (Orochi), and the memory engines/binaries needed to run it.

## What's included
- **All samRAM source** — the console (FastAPI/Jinja2), collection plane, storage,
  enumeration, hunt/detection stubs, and the root scripts.
- **MemProcFS** (`bin/memprocfs/` — `MemProcFS.exe` + Python runtime + DLLs) for live /
  dumpless analysis.
- **LeechCore + LeechAgent** (`bin/LeechCore/files/agent/x64/` — `leechagent.exe`,
  `leechcore.dll`, `winpmem_x64.sys`, `vcruntime140.dll`) for the live remote path.
- **go-winpmem** (`bin/go-winpmem_amd64_1.0-rc2_signed.exe`) for full raw dumps.
- Setup/provisioning scripts (`setup.sh`, `start.sh`, `provision-*.{sh,ps1}`),
  `pyproject.toml`, and the docs (`README.md`, `docs/`).
- **OPSEC audit docs** (`docs/flow-in-depth/`) covering exactly what runs on target
  computers at the OS level, for security auditors.
- **Component flow docs** (`docs/flows/`) — deep walkthroughs with draw.io diagrams.
- **Architecture diagram** (`docs/diagrams/samram-architecture.drawio`).

## What was deliberately left OUT (bring your own)
- **Memory dumps** — no `*.raw`, `captures/`, `vol_work/`, or `storage/blobs/`.
- **Credentials + secrets** — no Kerberos ccache (`*.ccache`), no encrypted vault
  (`storage/vault.enc`), no `.env.local`. Supply your own.
- **Orochi** — clone separately:
  `git clone https://github.com/LDO-CERT/orochi.git analysis/orochi`
- Build junk (`__pycache__`, `*.egg-info`, nested `.git`).

## Quick start (see `README.md` for full detail)
1. **Host:** Windows 11 + WSL2 + Docker Desktop.
2. In WSL: `bash setup.sh` (creates the venv, installs deps).
3. Bring up Postgres: `cd deploy && bash up.sh`.
4. Generate a master key: `python -c "import storage.crypto as c; print(c.generate_master_key())"`
5. Copy `.env.local.example` to `.env.local` and fill in the key.
6. Start the console: `bash start.sh`, then open the printed URL.

## Notes
- Source has been stripped of comments and docstrings. Vendored trees
  (MemProcFS, LeechCore) are left untouched.
