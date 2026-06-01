$ErrorActionPreference = "Stop"

$Root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$DistDir = Join-Path $Root "dist"
$WorkDir = Join-Path $Root "build\pyinstaller"
$SpecDir = Join-Path $Root "build\pyinstaller"
$Launcher = Join-Path $Root "codex_usage_tracker_gui.py"
$ExePath = Join-Path $DistDir "AICodingUsageTracker.exe"

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "Missing GUI launcher: $Launcher"
}

Push-Location $Root
try {
    python -m pip show pyinstaller *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing PyInstaller for this Python environment..."
        python -m pip install "pyinstaller>=6.0"
    }

    python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name AICodingUsageTracker `
        --distpath $DistDir `
        --workpath $WorkDir `
        --specpath $SpecDir `
        --hidden-import tkinter `
        --hidden-import tkinter.ttk `
        --hidden-import tkinter.messagebox `
        $Launcher

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE. If the EXE is open, close it and rebuild."
    }

    if (-not (Test-Path -LiteralPath $ExePath)) {
        throw "Expected EXE was not created: $ExePath"
    }

    Write-Host "Built $ExePath"
}
finally {
    Pop-Location
}
