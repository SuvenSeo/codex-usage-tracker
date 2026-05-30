# Privacy Notes

This tool is designed to be local-first.

## What It Reads

- Codex rollout JSONL files under `~/.codex/sessions`
- Codex metadata from `~/.codex/state_5.sqlite`, if available
- Claude Code project JSONL files under `~/.claude/projects`, when selected with `--sources claude` or `--sources all`
- Cursor AI tracking metadata from `~/.cursor/ai-tracking/ai-code-tracking.db`, when selected with `--sources cursor` or `--sources all`
- WakaTime config presence from `~/.wakatime.cfg`, only to verify that an API key exists

## What It Writes

- Local JSON, CSV, and HTML reports under `out/` by default
- A state file used to avoid sending duplicate WakaTime heartbeats. The default CLI location is `~/.codex-usage-tracker/state.json`; the included Windows scheduled task stores `.tracker_state.json` in the repo checkout.

## What Can Be Sensitive

Generated reports can contain:

- Local filesystem paths
- Project names
- Thread titles
- Model names and token counts
- App/source names
- Cursor AI edit activity and request counts
- Estimated usage costs

The repository `.gitignore` excludes generated reports by default. Review reports before sharing screenshots or files publicly.

## Share-Safe Reports

Use:

```bash
codex-usage-tracker --redact --hash-projects report
```

`--redact` hides thread titles, local folders, and log paths. `--hash-projects` replaces project names with stable anonymous labels.

For multi-source reports, use:

```bash
codex-usage-tracker --sources all --redact --hash-projects report
```

For issue reports, prefer:

```bash
codex-usage-tracker --redact --hash-projects doctor
```

## WakaTime Sync

When `sync-wakatime` is enabled, the tool sends WakaTime heartbeats with:

- Activity category: `ai coding`
- Entity type: `app`
- Project name/folder
- Timestamp

It does not send token counts, prompts, responses, or transcript content to WakaTime.
WakaTime sync currently sends Codex-derived activity only; Claude Code and Cursor
records stay in local reports.
