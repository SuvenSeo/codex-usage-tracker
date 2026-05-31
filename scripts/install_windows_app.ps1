param(
    [string]$SourceExe = "",
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\CodexUsageTracker"),
    [switch]$NoDesktopShortcut
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")

function Resolve-SourceExe {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        $resolved = Resolve-Path -LiteralPath $ExplicitPath
        return $resolved.Path
    }

    $candidates = @(
        (Join-Path $Root "dist\CodexUsageTracker.exe"),
        (Join-Path $Root "dist\CodexUsageTrackerAllSources.exe")
    )

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "No built EXE found. Run .\scripts\build_windows_exe.ps1 first."
}

function New-AppShortcut {
    param(
        [string]$ShortcutPath,
        [string]$TargetPath,
        [string]$WorkingDirectory
    )

    $parent = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetPath
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = "AI Coding Usage Tracker"
    $shortcut.IconLocation = "$TargetPath,0"
    $shortcut.Save()
}

$source = Resolve-SourceExe -ExplicitPath $SourceExe
$installPath = Join-Path $InstallDir "CodexUsageTracker.exe"
$startMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Codex Usage Tracker.lnk"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Codex Usage Tracker.lnk"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -LiteralPath $source -Destination $installPath -Force

New-AppShortcut -ShortcutPath $startMenuShortcut -TargetPath $installPath -WorkingDirectory $InstallDir

if (-not $NoDesktopShortcut) {
    New-AppShortcut -ShortcutPath $desktopShortcut -TargetPath $installPath -WorkingDirectory $InstallDir
}

Write-Host "Installed Codex Usage Tracker"
Write-Host "App: $installPath"
Write-Host "Start Menu shortcut: $startMenuShortcut"
if (-not $NoDesktopShortcut) {
    Write-Host "Desktop shortcut: $desktopShortcut"
}
