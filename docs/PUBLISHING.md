# Publishing

This project is ready for a normal PyPI trusted publishing setup.

Current state:

- `v0.2.6` is the current package version in `pyproject.toml` and `codex_app_tracker.py`.
- The Windows release asset is built as `AICodingUsageTracker.exe` with a
  SHA256 checksum asset and bundled GUI brand logos.
- PyPI trusted publishing is configured and working.
- `codex-usage-tracker` is published on PyPI:
  `https://pypi.org/project/codex-usage-tracker/`.

## Local Checks

```bash
python -m py_compile codex_app_tracker.py report_cache.py gui_visuals.py
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

## GitHub Release Flow

1. Update the version in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Run local checks.
4. Push the commit to `main`.
5. Run the `Release` GitHub Action manually with `publish_pypi=false` first.
6. Inspect the uploaded `dist` artifact.
7. Run it again with `publish_pypi=true`.
8. Tag the same commit and create a GitHub release.

The release workflow uses PyPI trusted publishing, so no PyPI token should be stored in the repo.

## Trusted Publisher Values

The PyPI trusted publisher was created from:

```text
https://pypi.org/manage/account/publishing/
```

Use these exact values:

```text
PyPI project name: codex-usage-tracker
Owner: SuvenSeo
Repository name: ai-coding-usage-tracker
Workflow filename: release.yml
Environment name: pypi
```

These match the claims emitted by the GitHub workflow:

```text
sub: repo:SuvenSeo/ai-coding-usage-tracker:environment:pypi
repository: SuvenSeo/ai-coding-usage-tracker
workflow_ref: SuvenSeo/ai-coding-usage-tracker/.github/workflows/release.yml@refs/heads/main
environment: pypi
```

These values must stay in sync with `.github/workflows/release.yml`.

## Clean Install Check

Before publishing a new tag, test the GitHub source install from a clean
virtual environment:

```powershell
$tmp = Join-Path $env:TEMP "ai-coding-usage-tracker-install-check"
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
python -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install --upgrade pip
& "$tmp\Scripts\python.exe" -m pip install codex-usage-tracker
& "$tmp\Scripts\ai-coding-usage-tracker.exe" --output-dir "$tmp\demo" demo
```
