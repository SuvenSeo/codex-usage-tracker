#!/usr/bin/env python3
"""Windows GUI launcher for packaged Codex Usage Tracker builds."""

from __future__ import annotations

import sys

from codex_app_tracker import main

DEFAULT_GUI_ARGS = ["--sources", "all", "gui"]


def launcher_args(argv: list[str] | None = None) -> list[str]:
    args = list(sys.argv[1:] if argv is None else argv)
    return args or DEFAULT_GUI_ARGS.copy()


if __name__ == "__main__":
    raise SystemExit(main(launcher_args()))
