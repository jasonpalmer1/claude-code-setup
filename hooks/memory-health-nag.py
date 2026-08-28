#!/usr/bin/env python3
"""SessionStart hook — one-line nag when the memory-graph health check looks bad.

Pairs with memory-graph-refresh.sh and memory-activate.py (see the latter's
docstring for what the memory graph is). Reads only the LAST row of
graph/health-history.jsonl — a file you'd write from your own graph/maintain.py
health-check script — and prints a single short plain-English line ONLY when
something is actually wrong: open issues, a silent (null) benchmark, or a
health check that hasn't run in over a week. Prints nothing when healthy —
this is a nag, not a status dashboard.

Fails open, always: any error (missing file, bad JSON, whatever) means print
nothing and exit 0. A memory hook must never be able to block a session.

Customize MEMORY_DIR to your own layout (see <MEMORY_DIR> in
CLAUDE.md.template). Wire in settings.json (this script does not register
itself):
  {"hooks": {"SessionStart": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/memory-health-nag.py"}]}]}}
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Customize: point this at your own memory directory, e.g. the layout from
# CLAUDE.md.template's <MEMORY_DIR>.
HISTORY = Path.home() / ".claude/projects/<your-id>/memory/graph/health-history.jsonl"
STALE_DAYS = 8


def main() -> int:
    try:
        if not HISTORY.exists():
            return 0
        lines = [l for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return 0
        row = json.loads(lines[-1])

        issues = row.get("issues") or 0
        recall_graph = row.get("recall_graph")
        date_str = row.get("date")

        age_days = None
        if date_str:
            try:
                last = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - last).days
            except ValueError:
                age_days = None

        problems: list[str] = []
        if issues:
            problems.append(f"{issues} issue{'s' if issues != 1 else ''}")
        if recall_graph is None:
            problems.append("benchmark silent")
        if age_days is not None and age_days > STALE_DAYS:
            problems.append(f"last run {age_days}d ago")

        if not problems:
            return 0

        print(f"⚠ memory-graph health: {', '.join(problems)} "
              f"— run graph/maintain.py")
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
