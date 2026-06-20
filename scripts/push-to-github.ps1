$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

Set-Location -LiteralPath $Repo

Write-Host "Pushing main to GitHub..." -ForegroundColor Cyan
git status -sb

$ghStatus = gh auth status -h github.com 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "GitHub CLI auth needs attention. Configuring git credential helper..." -ForegroundColor Yellow
    gh auth setup-git 2>&1 | Out-Host
}

git push -u origin main

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Push failed." -ForegroundColor Yellow
    Write-Host "  Network: confirm github.com resolves (nslookup github.com)" -ForegroundColor Yellow
    Write-Host "  Auth:    gh auth login -h github.com  OR  gh auth refresh -h github.com" -ForegroundColor Yellow
    pause
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Push succeeded." -ForegroundColor Green
git status -sb
Start-Sleep -Seconds 3
