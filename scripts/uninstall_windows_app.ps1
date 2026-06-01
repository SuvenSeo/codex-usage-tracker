param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\AICodingUsageTracker"),
    [switch]$KeepDesktopShortcut
)

$ErrorActionPreference = "Stop"

$startMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\AI Coding Usage Tracker.lnk"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "AI Coding Usage Tracker.lnk"
$legacyInstallDir = Join-Path $env:LOCALAPPDATA "Programs\CodexUsageTracker"
$legacyStartMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Codex Usage Tracker.lnk"
$legacyDesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex Usage Tracker.lnk"

if (Test-Path -LiteralPath $startMenuShortcut) {
    Remove-Item -LiteralPath $startMenuShortcut -Force
}

if (-not $KeepDesktopShortcut -and (Test-Path -LiteralPath $desktopShortcut)) {
    Remove-Item -LiteralPath $desktopShortcut -Force
}

if (Test-Path -LiteralPath $InstallDir) {
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
}

foreach ($legacyPath in @($legacyStartMenuShortcut, $legacyDesktopShortcut)) {
    if ((-not $KeepDesktopShortcut -or $legacyPath -ne $legacyDesktopShortcut) -and (Test-Path -LiteralPath $legacyPath)) {
        Remove-Item -LiteralPath $legacyPath -Force
    }
}

if ($legacyInstallDir -ne $InstallDir -and (Test-Path -LiteralPath $legacyInstallDir)) {
    Remove-Item -LiteralPath $legacyInstallDir -Recurse -Force
}

Write-Host "Uninstalled AI Coding Usage Tracker"
