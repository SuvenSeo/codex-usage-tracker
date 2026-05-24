# Roadmap

This roadmap is based on a 2026-05-24 scan of adjacent usage-tracking tools,
OpenAI pricing/usage docs, WakaTime plugin docs, and the current project state.

## Positioning

The strongest near-term position is:

> The simplest local-first Codex usage dashboard with WakaTime AI-coding sync.

Do not compete head-on with large multi-agent tools first. Own the Codex-specific
workflow, then expand after the product feels polished.

## Current Alpha

Already implemented in the repo:

- Static local HTML/CSV/JSON reports.
- Terminal reports for daily, weekly, monthly, session, project, and model views.
- `doctor` and `demo` first-run commands.
- `--redact` and `--hash-projects` privacy controls.
- Optional WakaTime `ai coding` heartbeat sync.
- GitHub CI, issue templates, and a trusted-publishing PyPI release workflow.

## Research Snapshot

Adjacent tools show what developers already star and share:

- `ryoppippi/ccusage`: very strong benchmark. It supports many coding agents,
  daily/weekly/monthly/session reports, compact terminal output, JSON, timezone
  filtering, project/instance grouping, cache-token handling, and statusline use.
- `kenn-io/agentsview`: broader local-first session intelligence. It has a
  web UI, SQLite indexing, full-text search, live updates, heatmaps, exports,
  Docker, desktop builds, and multi-agent support.
- `angristan/codex-wakatime`: focused Codex CLI hook integration for WakaTime.
  It does one job clearly and documents hooks, config, files, and troubleshooting.
- `Nihondo/AgentLimits`: shows the appeal of usage limit widgets, pace tracking,
  heatmaps, menu-bar display, and notifications.

Platform docs also point to useful improvements:

- OpenAI's Codex rate card is token-based and separates input, cached input,
  and output token credit rates.
- OpenAI's Usage/Costs API can provide authoritative API spend, but Codex app
  credit usage still needs clear "estimate vs invoice" labeling.
- WakaTime supports `ai coding` activity and AI metadata fields such as input
  tokens, output tokens, prompt length, session id, and AI agent cost.

## High-Leverage Product Bets

### 1. Make The First Run Excellent

Goal: someone should be able to run one command and immediately see value.

- Add `codex-usage-tracker doctor`.
- Detect Codex data path, WakaTime config, Python version, OS, and output path.
- Print a clear checklist: found logs, parsed sessions, WakaTime ready, pricing
  table version.
- Add `codex-usage-tracker demo` using bundled fake data.
- Add installation options for `pipx`, `uv tool`, and direct GitHub release.

### 2. Add A Real Local Web App Mode

Goal: make it feel like a product, not just a generated HTML file.

- Add `codex-usage-tracker serve`.
- Serve a local dashboard on `127.0.0.1`.
- Add auto-refresh while Codex is active.
- Add filters: date range, project, model, reasoning effort.
- Add charts: daily tokens, credits, cache hit rate, output ratio, active time.
- Add "most expensive threads" and "most improved cache ratio" views.

### 3. Improve The CLI Reports

Goal: match the terminal usefulness users expect from `ccusage`.

- Add `daily`, `weekly`, `monthly`, `session`, `project`, and `model` commands.
- Add `--json`, `--csv`, `--compact`, `--timezone`, `--since`, `--until`.
- Add `statusline` output for shells, Starship, tmux, and PowerShell prompts.
- Add exit-code thresholds for budget automation.

### 4. Privacy And Shareability

Goal: users should feel safe sharing screenshots and examples.

- Add `--redact` to hide local paths and thread titles.
- Add `--hash-projects` for anonymized project names.
- Add `export-public-demo` that produces a sanitized screenshot and JSON sample.
- Add docs explaining exactly what leaves the machine during WakaTime sync.

### 5. Better WakaTime AI Sync

Goal: make the WakaTime integration richer than generic time heartbeats.

- Include Codex session id in WakaTime `ai_session` when possible.
- Send `ai_input_tokens`, `ai_output_tokens`, and prompt length when supported.
- Add `--wakatime-mode conservative|rich`.
- Add bulk heartbeat mode where supported.
- Add a verification command that confirms heartbeats landed.

### 6. Pricing And Cost Accuracy

Goal: reduce confusion around "estimate vs actual".

- Move pricing tables into `pricing.json`.
- Add `pricing update` to refresh known rates from checked-in release data.
- Track rate table version and source date in every report.
- Add optional OpenAI API Costs sync for API usage, clearly separate from Codex
  app credits.
- Add budget warnings: daily, weekly, monthly, and per-project.

### 7. Packaging And Distribution

Goal: lower friction for stars and adoption.

- Publish to PyPI.
- Add GitHub Releases with zip artifacts and checksums.
- Add Windows installer script and macOS/Linux install scripts.
- Add Homebrew tap later if adoption justifies it.
- Add a short screen recording GIF to the README.

### 8. Multi-Agent Expansion Later

Goal: expand only when the Codex experience is strong.

- Add parser interface: `AgentParser`.
- Keep Codex as the best-supported parser.
- Add optional Claude Code, Cursor, OpenCode, Gemini CLI support only if the
  dashboard can show cross-agent comparisons clearly.

## 7-Day Execution Plan

### Day 1

- Add `doctor`, `demo`, `daily`, and `session` commands.
- Add `--json`, `--since`, `--until`, and `--timezone`.

### Day 2

- Add privacy modes: `--redact`, `--hash-projects`, public export.
- Update README with a clearer privacy promise and screenshots.

### Day 3

- Add `serve` local web dashboard with filters and auto-refresh.
- Keep the frontend dependency-free at first.

### Day 4

- Improve WakaTime sync with token/session metadata where supported.
- Add `verify-wakatime` command.

### Day 5

- Publish to PyPI.
- Add install docs for `pipx`, `uv tool`, and `pip`.
- Tag `v0.1.0`.

### Day 6

- Create 8-12 GitHub issues from this roadmap.
- Enable Discussions.
- Add issue templates.
- Add comparison section: "Codex Usage Tracker vs ccusage vs agentsview".

### Day 7

- Record a short demo GIF.
- Post launch content.
- Submit to relevant communities and ask for feedback, not just stars.

## Issue Backlog

Suggested first issues:

1. Add `serve` local dashboard.
2. Add WakaTime rich AI metadata sync.
3. Add pricing table source/version metadata.
4. Add statusline output for shells, Starship, tmux, and PowerShell prompts.
5. Add budget thresholds and non-zero exit codes.
6. Add public screenshot export.
7. Add parser interface for future multi-agent support.
8. Add install docs for `pipx`, `uv tool`, and direct GitHub releases.
9. Add short demo GIF.
10. Add OpenAI API Costs sync for API usage, clearly separate from Codex app credits.
