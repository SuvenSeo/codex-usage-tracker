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

    $BrandAssets = Join-Path $Root "assets\gui\brands"
    if (-not (Test-Path -LiteralPath $BrandAssets)) {
        throw "Missing brand assets folder: $BrandAssets. Run scripts/generate_brand_assets.py first."
    }
    foreach ($required in @("codex.png", "claude.png", "cursor.png")) {
        if (-not (Test-Path -LiteralPath (Join-Path $BrandAssets $required))) {
            throw "Missing brand asset: $required. Run scripts/generate_brand_assets.py first."
        }
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
        --add-data "$BrandAssets;assets/gui/brands" `
        --hidden-import gui_visuals `
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
