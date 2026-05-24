# Publishing

This project is ready for a normal PyPI trusted publishing setup.

## Local Checks

```bash
python -m py_compile codex_app_tracker.py
python -m unittest discover -s tests -v
python -m pip install build
python -m build
```

## GitHub Release Flow

1. Create the PyPI project or reserve the package name `codex-usage-tracker`.
2. Configure PyPI trusted publishing for this GitHub repo and the `pypi` environment.
3. Run the `Release` GitHub Action manually with `publish_pypi=false` first.
4. Inspect the uploaded `dist` artifact.
5. Run it again with `publish_pypi=true`.
6. Tag the same commit, for example `v0.1.0`.

The release workflow uses PyPI trusted publishing, so no PyPI token should be stored in the repo.
