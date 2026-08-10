#!/usr/bin/env python3
"""SessionStart hook: warns when the current directory's CLAUDE.md has crossed
a size cap.

Backs the "instructions-file size" rule in CLAUDE.md.template mechanically: a
project-local CLAUDE.md auto-loads into every turn of every session that
works in that project, so its size is a per-message tax, not a one-time cost.
A file that quietly grows to hundreds of KB re-bills that whole cost on every
turn, for every session, indefinitely — and prose reminders to keep it small
get skipped under time pressure the same way any other prose rule does.

Silent when the file is missing or under the cap. Never blocks the session —
this is a nudge, delivered as SessionStart additionalContext, not a gate.

Customize CAP_CHARS to taste; 30,000 characters is a starting point (roughly
7-8k tokens), calibrated from real projects that silently grew 10x past it
before anyone noticed.
"""
import json
import os
import sys

CAP_CHARS = 30_000


def main():
    path = os.path.join(os.getcwd(), "CLAUDE.md")
    try:
        size = os.path.getsize(path)
    except OSError:
        return  # no CLAUDE.md here — nothing to check

    if size <= CAP_CHARS:
        return

    msg = (
        f"Note: this project's CLAUDE.md is {size // 1000}K characters "
        f"(cap {CAP_CHARS // 1000}K) — it gets re-sent and re-billed on every "
        "turn of every session working here. Consider archiving the oldest "
        "entries (e.g. a '## Session memory' section) verbatim to an "
        "ARCHIVE.md that doesn't auto-load, and keeping only standing rules "
        "and recent entries active."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": msg}}))


if __name__ == "__main__":
    sys.exit(main() or 0)
