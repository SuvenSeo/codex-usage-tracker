$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$tracker = Join-Path $scriptDir "codex_app_tracker.py"
$outputDir = Join-Path $scriptDir "out"
$stateFile = Join-Path $scriptDir ".tracker_state.json"

python $tracker --output-dir $outputDir --state-file $stateFile run --sync-wakatime --wakatime-since-minutes 240 --wakatime-interval-minutes 10 --wakatime-max-heartbeats 40
