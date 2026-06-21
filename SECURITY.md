# Security Policy

## Supported Versions

This project is early-stage. Security fixes target the latest `main` branch until tagged releases are created.

## Reporting a Vulnerability

Please open a private security advisory on GitHub, or email the maintainer if a public advisory is not available.

Do not paste private Codex rollout logs, Claude Code transcripts, Cursor databases, WakaTime API keys, OpenAI API keys, or generated reports into public issues. The generated `out/` folder can contain local paths, thread titles, project names, model names, token totals, and activity counts.

## Data Handling

The tracker runs locally. Depending on `--sources`, it reads Codex app logs from `~/.codex`, Claude Code logs from `~/.claude/projects`, and Cursor data from Agent transcripts, Composer/agent cache blobs, and `~/.cursor/ai-tracking`. It writes reports to a local output directory and may cache parsed GUI summaries under `~/.codex-usage-tracker/report_cache/`.

When WakaTime sync is enabled, it sends activity heartbeats through `wakatime-cli`. It does not send token totals to WakaTime.

