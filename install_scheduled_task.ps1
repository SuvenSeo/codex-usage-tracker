$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$runner = Join-Path $scriptDir "run_tracker.ps1"
$taskName = "CodexAppUsageTracker"
$taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$runner`""

schtasks.exe /Create /TN $taskName /SC MINUTE /MO 15 /TR $taskCommand /F | Out-Host
schtasks.exe /Query /TN $taskName /V /FO LIST | Out-Host
