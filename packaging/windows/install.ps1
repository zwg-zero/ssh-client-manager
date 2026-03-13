# install.ps1 — Quick install SSH Client Manager from source on Windows
#
# This script installs the app files into %LOCALAPPDATA%\ssh-client-manager
# and creates a Start Menu shortcut and optional PATH entry.
#
# Requirements:
#   - MSYS2 installed with GTK4 / libadwaita / VTE packages
#   - Python 3 (MSYS2 or standalone)
#
# Run from the project root in PowerShell:
#   .\packaging\windows\install.ps1
#
# To uninstall:
#   .\packaging\windows\install.ps1 -Uninstall

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$AppName       = "SSH Client Manager"
$AppId         = "ssh-client-manager"
$InstallDir    = Join-Path $env:LOCALAPPDATA $AppId
$StartMenuDir  = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath  = Join-Path $StartMenuDir "$AppName.lnk"

# ── Uninstall mode ────────────────────────────────────────────────────────────
if ($Uninstall) {
    Write-Host "Uninstalling $AppName..." -ForegroundColor Yellow

    if (Test-Path $InstallDir) {
        Remove-Item -Recurse -Force $InstallDir
        Write-Host "  Removed $InstallDir"
    }
    if (Test-Path $ShortcutPath) {
        Remove-Item -Force $ShortcutPath
        Write-Host "  Removed Start Menu shortcut"
    }

    # Remove from user PATH
    $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($userPath -and $userPath.Contains($InstallDir)) {
        $newPath = ($userPath.Split(";") | Where-Object { $_ -ne $InstallDir }) -join ";"
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        Write-Host "  Removed from user PATH"
    }

    Write-Host "Uninstall complete." -ForegroundColor Green
    exit 0
}

# ── Locate project root ──────────────────────────────────────────────────────
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

if (-not (Test-Path (Join-Path $ProjectRoot "run.py")) -or
    -not (Test-Path (Join-Path $ProjectRoot "src"))) {
    Write-Host "Error: Cannot find project root. Expected run.py and src/ at: $ProjectRoot" -ForegroundColor Red
    exit 1
}

Write-Host "Installing $AppName from source..." -ForegroundColor Cyan
Write-Host "  Project root : $ProjectRoot"
Write-Host "  Install dir  : $InstallDir"

# ── Check MSYS2 is installed ─────────────────────────────────────────────────
Write-Host ""
Write-Host "Checking prerequisites..." -ForegroundColor Cyan

$Msys2Paths = @("C:\msys64", "C:\msys2", "$env:USERPROFILE\msys64")
$Msys2Root  = $null
foreach ($p in $Msys2Paths) {
    if (Test-Path (Join-Path $p "usr\bin\bash.exe")) {
        $Msys2Root = $p
        break
    }
}

if (-not $Msys2Root) {
    Write-Host "Error: MSYS2 not found. Install it from https://www.msys2.org/" -ForegroundColor Red
    Write-Host "Then install GTK4 packages (see README.md for details)."
    exit 1
}

Write-Host "  MSYS2 found at: $Msys2Root"

# Check for python3 in MSYS2 MINGW64
$Mingw64Bin = Join-Path $Msys2Root "mingw64\bin"
$PythonExe  = Join-Path $Mingw64Bin "python3.exe"

if (-not (Test-Path $PythonExe)) {
    Write-Host "Error: Python3 not found in MSYS2 MINGW64 ($PythonExe)" -ForegroundColor Red
    Write-Host "Install with:  pacman -S mingw-w64-x86_64-python"
    exit 1
}
Write-Host "  Python3 found: $PythonExe"

# ── Install application files ────────────────────────────────────────────────
Write-Host ""
Write-Host "Installing application files..." -ForegroundColor Cyan

if (Test-Path $InstallDir) {
    Remove-Item -Recurse -Force $InstallDir
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

Copy-Item (Join-Path $ProjectRoot "run.py") -Destination $InstallDir
Copy-Item (Join-Path $ProjectRoot "src") -Destination (Join-Path $InstallDir "src") -Recurse

$ResourcesDir = Join-Path $ProjectRoot "resources"
if (Test-Path $ResourcesDir) {
    Copy-Item $ResourcesDir -Destination (Join-Path $InstallDir "resources") -Recurse
}

# ── Create launcher batch file ────────────────────────────────────────────────
$LauncherPath = Join-Path $InstallDir "$AppId.bat"
$MingwPython  = "$Msys2Root\mingw64\bin\python3.exe"
@"
@echo off
set PATH=$Msys2Root\mingw64\bin;%PATH%
start "" "$MingwPython" "$InstallDir\run.py" %*
"@ | Set-Content -Path $LauncherPath -Encoding ASCII

Write-Host "  Launcher created: $LauncherPath"

# ── Create Start Menu shortcut ────────────────────────────────────────────────
Write-Host "Creating Start Menu shortcut..." -ForegroundColor Cyan

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath       = $LauncherPath
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Description      = "SSH Client Manager - Modern GTK4 SSH connection manager"
$Shortcut.Save()

Write-Host "  Shortcut: $ShortcutPath"

# ── Add to user PATH (optional) ──────────────────────────────────────────────
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (-not $userPath -or -not $userPath.Contains($InstallDir)) {
    if ($userPath) {
        $newPath = "$InstallDir;$userPath"
    } else {
        $newPath = $InstallDir
    }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Host "  Added $InstallDir to user PATH"
}

Write-Host ""
Write-Host "$AppName installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "  Launch from terminal : $AppId"
Write-Host "  Launch from Start Menu: search for '$AppName'"
Write-Host ""
Write-Host "To uninstall:"
Write-Host "  .\packaging\windows\install.ps1 -Uninstall"
