# provision-windows.ps1 - catch-all provisioning for the Windows side of samRAM.
#
# Checks what's already installed and installs ONLY what's missing (WSL2 + a distro,
# Docker Desktop). Idempotent - safe to re-run on a laptop in any state.
#
# Run in an ADMINISTRATOR PowerShell:
#   powershell -ExecutionPolicy Bypass -File provision-windows.ps1
#
# After this (and a reboot if one was needed), open Ubuntu and run: bash provision-wsl.sh

#Requires -Version 5.1
[CmdletBinding()]
param()

function Info($m) { Write-Host "[provision] $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "[provision] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[provision] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[provision] $m" -ForegroundColor Red }

# Must be admin to install WSL / Docker Desktop.
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Fail "Run this in an Administrator PowerShell, then re-run."; exit 1 }

$rebootNeeded = $false

# ---- 1. WSL2 + a distro ----------------------------------------------------
Info "checking WSL..."
$hasWsl = $null -ne (Get-Command wsl.exe -ErrorAction SilentlyContinue)
$hasDistro = $false
if ($hasWsl) {
    $distros = (& wsl.exe -l -q) 2>$null
    if ($distros) { $distros = ($distros -replace "`0", "").Trim() }
    if ($LASTEXITCODE -eq 0 -and $distros) { $hasDistro = $true }
}
if ($hasWsl -and $hasDistro) {
    Ok "WSL present with a distro ($($distros -join ', '))"
} else {
    Info "installing WSL2 + Ubuntu (wsl --install -d Ubuntu)..."
    & wsl.exe --install -d Ubuntu
    Warn "WSL was installed - a REBOOT is required before the Ubuntu setup can run."
    $rebootNeeded = $true
}

# ---- 2. Docker Desktop -----------------------------------------------------
Info "checking Docker Desktop..."
$dockerExe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$dockerInstalled = (Test-Path $dockerExe) -or `
    ($null -ne (Get-Command docker -ErrorAction SilentlyContinue))
if ($dockerInstalled) {
    Ok "Docker Desktop present"
} else {
    if ($null -ne (Get-Command winget -ErrorAction SilentlyContinue)) {
        Info "installing Docker Desktop (winget)..."
        & winget install -e --id Docker.DockerDesktop `
            --accept-source-agreements --accept-package-agreements
        Warn "Docker Desktop installed - launch it once, then enable"
        Warn "Settings > Resources > WSL Integration for your distro."
        $rebootNeeded = $true
    } else {
        Warn "winget not found - install Docker Desktop manually:"
        Warn "  https://www.docker.com/products/docker-desktop/"
    }
}

# ---- next steps ------------------------------------------------------------
Write-Host ""
if ($rebootNeeded) {
    Warn "Windows layer partially done. Next:"
    Warn "  1. REBOOT."
    Warn "  2. Launch Docker Desktop once (enable WSL integration)."
    Warn "  3. Open Ubuntu (WSL) and run:  bash provision-wsl.sh"
} else {
    Ok "Windows layer ready. Next: open Ubuntu (WSL) and run:  bash provision-wsl.sh"
}
