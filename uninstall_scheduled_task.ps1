$ErrorActionPreference = "Stop"

$taskName = "CodexAppUsageTracker"
schtasks.exe /Delete /TN $taskName /F | Out-Host
