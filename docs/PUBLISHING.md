# Publishing

This project is ready for a normal PyPI trusted publishing setup.

Current state:

- `v0.1.0` is published as a GitHub Release.
- The release includes `CodexUsageTracker.exe` and a SHA256 checksum asset.
- PyPI is not published yet; use the GitHub tag install path until the PyPI
  project and trusted publisher are configured.
- The GitHub release workflow has already been test-run with `publish_pypi=true`.
  The build job passed, and the publish job reached PyPI, but PyPI rejected it
  with `invalid-publisher` because no pending trusted publisher exists on the
  PyPI account yet.

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

## Pending Trusted Publisher Values

For a new PyPI project, create a pending publisher from:

```text
https://pypi.org/manage/account/publishing/
```

Use these exact values:

```text
PyPI project name: codex-usage-tracker
Owner: SuvenSeo
Repository name: codex-usage-tracker
Workflow filename: release.yml
Environment name: pypi
```

These match the claims emitted by the GitHub workflow:

```text
sub: repo:SuvenSeo/codex-usage-tracker:environment:pypi
repository: SuvenSeo/codex-usage-tracker
workflow_ref: SuvenSeo/codex-usage-tracker/.github/workflows/release.yml@refs/heads/main
environment: pypi
```

After the pending publisher is saved on PyPI, rerun the GitHub `Release`
workflow on `main` with `publish_pypi=true`. PyPI should create the project on
first successful publish and convert the pending publisher into a normal
trusted publisher.

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
