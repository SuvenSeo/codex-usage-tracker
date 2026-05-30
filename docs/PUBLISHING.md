# Publishing

This project is ready for a normal PyPI trusted publishing setup.

Current state:

- `v0.1.0` is published as a GitHub Release.
- The release includes `CodexUsageTracker.exe` and a SHA256 checksum asset.
- PyPI is not published yet; use the GitHub tag install path until the PyPI
  project and trusted publisher are configured.

## Local Checks

```bash
python -m py_compile codex_app_tracker.py
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

## GitHub Release Flow

1. Create the PyPI project or reserve the package name `codex-usage-tracker`.
2. Configure PyPI trusted publishing for this GitHub repo and the `pypi`
   environment.
3. Run the `Release` GitHub Action manually with `publish_pypi=false` first.
4. Inspect the uploaded `dist` artifact.
5. Run it again with `publish_pypi=true`.
6. Tag the same commit, for example `v0.1.1`.

The release workflow uses PyPI trusted publishing, so no PyPI token should be stored in the repo.

## Clean Install Check

Before publishing a new tag, test the GitHub source install from a clean
virtual environment:

```powershell
$tmp = Join-Path $env:TEMP "codex-usage-tracker-install-check"
Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
python -m venv $tmp
& "$tmp\Scripts\python.exe" -m pip install --upgrade pip
& "$tmp\Scripts\python.exe" -m pip install "codex-usage-tracker @ git+https://github.com/SuvenSeo/codex-usage-tracker.git@v0.1.0"
& "$tmp\Scripts\codex-usage-tracker.exe" --output-dir "$tmp\demo" demo
```
