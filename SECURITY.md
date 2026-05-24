# Security Policy

## Supported Versions

This project is early-stage. Security fixes target the latest `main` branch until tagged releases are created.

## Reporting a Vulnerability

Please open a private security advisory on GitHub, or email the maintainer if a public advisory is not available.

Do not paste private Codex rollout logs, WakaTime API keys, OpenAI API keys, or generated reports into public issues. The generated `out/` folder can contain local paths, thread titles, and project names.

## Data Handling

The tracker runs locally. It reads Codex app logs from `~/.codex` and writes reports to a local output directory.

When WakaTime sync is enabled, it sends activity heartbeats through `wakatime-cli`. It does not send token totals to WakaTime.

