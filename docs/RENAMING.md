# Repository Naming

## Current Names (v0.2.6)

| Surface | Name | Notes |
| --- | --- | --- |
| Product / README title | **AI Coding Usage Tracker** | User-facing brand |
| GitHub repo | `ai-coding-usage-tracker` | https://github.com/SuvenSeo/ai-coding-usage-tracker |
| PyPI package | `codex-usage-tracker` | Unchanged for `pip install` compatibility |
| Primary CLI | `ai-coding-usage-tracker` | Preferred command |
| Legacy CLI | `codex-usage-tracker` | Backward-compatible alias |
| Local data dir | `~/.codex-usage-tracker/` | State + GUI cache; unchanged |
| Windows EXE | `AICodingUsageTracker.exe` | Product-branded binary |

GitHub redirects the old `codex-usage-tracker` slug automatically after rename.

## What Was Not Renamed

These stay on the legacy `codex-usage-tracker` identifier on purpose:

- **PyPI package** — existing installs keep working with `pip install codex-usage-tracker`
- **`~/.codex-usage-tracker/`** — avoids orphaning local state and GUI cache files
- **CLI alias** `codex-usage-tracker` — backward-compatible entry point

## After You Rename on GitHub

1. Update PyPI trusted publisher **Repository name** to `ai-coding-usage-tracker`:
   https://pypi.org/manage/account/publishing/

2. Point your local remote at the new URL:
   ```powershell
   git remote set-url origin https://github.com/SuvenSeo/ai-coding-usage-tracker.git
   ```

3. Re-run `gh repo edit` from [GITHUB_PROFILE.md](GITHUB_PROFILE.md) if description/topics need refreshing on the new slug.
