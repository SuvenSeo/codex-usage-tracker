# Contributing

Contributions are welcome.

Good first issues:

- Add support for more Codex log shapes.
- Improve dashboard filtering and charts.
- Add macOS/Linux scheduling helpers.
- Add a pricing-rate update command.
- Improve WakaTime heartbeat grouping.

Before opening a PR:

```bash
python -m py_compile codex_app_tracker.py
python -m unittest discover -s tests -v
```

Do not commit generated `out/` reports or `.tracker_state.json`; they can contain private local paths and thread names.

