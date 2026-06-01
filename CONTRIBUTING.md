# Contributing

Contributions are welcome.

AI Coding Usage Tracker is local-first developer tooling. The main contribution
rule is: do not require users to upload prompts, responses, raw Codex/Claude
Code logs, Cursor databases, or private local paths to use or debug the project.

## Good First Issues

- Add support for more Codex log shapes.
- Improve dashboard filtering and charts.
- Add macOS/Linux scheduling helpers.
- Add a pricing-rate update command.
- Improve WakaTime heartbeat grouping.

The public issue tracker has labelled
[good first issues](https://github.com/SuvenSeo/ai-coding-usage-tracker/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
and roadmap work.

## Development Setup

Use Python 3.10 or newer:

```bash
git clone https://github.com/SuvenSeo/ai-coding-usage-tracker.git
cd ai-coding-usage-tracker
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\ai-coding-usage-tracker.exe demo
```

On macOS/Linux, activate the virtualenv with `source .venv/bin/activate`.

## Checks

Before opening a PR, run:

```bash
python -m py_compile codex_app_tracker.py
python -m unittest discover -s tests -v
```

If your change touches packaging, also run:

```bash
python -m pip install build
python -m build
```

## Privacy Checklist

- Do not commit generated `out/` reports or `.tracker_state.json`; they can
  contain private local paths and thread names.
- Do not paste private prompts, responses, API keys, raw rollout logs, Claude
  Code transcripts, Cursor databases, or raw `~/.codex` contents into issues.
- Use `ai-coding-usage-tracker --redact --hash-projects doctor` when sharing debug
  output.
- New integrations should clearly document what data leaves the machine.

## Pull Request Checklist

- Explain the user-visible change.
- Include tests or a manual verification command.
- Update README/docs when commands, outputs, privacy behavior, or install steps
  change.
- Keep changes focused; unrelated refactors should be separate PRs.

