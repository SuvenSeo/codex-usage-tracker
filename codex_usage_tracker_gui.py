#!/usr/bin/env python3
"""Windows GUI launcher for packaged Codex Usage Tracker builds."""

from __future__ import annotations

import sys

from codex_app_tracker import main


if __name__ == "__main__":
    args = sys.argv[1:] or ["gui"]
    raise SystemExit(main(args))
