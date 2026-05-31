param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\CodexUsageTracker"),
    [switch]$KeepDesktopShortcut
)

$ErrorActionPreference = "Stop"

$startMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Codex Usage Tracker.lnk"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex Usage Tracker.lnk"

if (Test-Path -LiteralPath $startMenuShortcut) {
    Remove-Item -LiteralPath $startMenuShortcut -Force
}

if (-not $KeepDesktopShortcut -and (Test-Path -LiteralPath $desktopShortcut)) {
    Remove-Item -LiteralPath $desktopShortcut -Force
}

if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

Write-Host "Uninstalled Codex Usage Tracker"
