#!/usr/bin/env python3
"""UserPromptSubmit hook — ledger status banner + surfaced items + hub-lease
heartbeat/banner (L-0911).

Prints the same "Open: N * Needs you: M" / WORKING-or-IDLE banner and the
surface lines the `ledger` CLI computes (~/.claude/hub/bin/ledger banner /
surface), both built from the shared ledger_lib.banner_lines() and
surface_lines() functions so the hook and the CLI can never drift apart.

L-0911 adds a second, independent block: if THIS session is the current
single-hub lease holder, its heartbeat is refreshed here (silent — this is
the only per-turn touchpoint every hub session has, so it's the natural
home for "prove you're still alive" per hub_lease_lib's dead-check). If it
is NOT the holder and its cwd is the hub's own ($HOME or
$HOME/.claude/hub), the loud NOT-THE-HUB banner prints on EVERY prompt —
this is the direct fix for the 2026-09-23 failure this ticket exists for,
where a session stood down to a peer's unverified claim and stayed
confused across many turns because nothing re-checked the file on each one.

Output goes straight to stdout as plain text: Claude Code folds a
UserPromptSubmit hook's stdout into the prompt's additionalContext
automatically, the exact mechanism ~/.claude/hooks/memory-activate.py
already relies on (see that file's docstring/wiring) — no JSON envelope,
no hookSpecificOutput key.

Fails open, always: if the ledger file is missing, empty, or anything
errors, this prints nothing and exits 0. A status hook must never be able
to block a session. The ledger block and the hub-lease block are
independent try/excepts — a bug in one must never take out the other (A1).

Wire in settings.json:
  {"hooks": {"UserPromptSubmit": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/ledger-banner-hook.py"}]}]}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LEDGER_BIN_DIR = Path.home() / ".claude/hub/bin"
HOME = Path.home()
HUB_CWDS = (str(HOME), str(HOME / ".claude/hub"))


def _ledger_block() -> list[str]:
    try:
        sys.path.insert(0, str(LEDGER_BIN_DIR))
        from ledger_lib import LEDGER_PATH, banner_lines, fold, read_events, surface_lines  # type: ignore

        if not LEDGER_PATH.exists():
            return []
        records = fold(read_events())
    except Exception:
        return []  # fail open, always

    if not records:
        return []

    lines = ["<ledger-status>"]
    lines.extend(banner_lines(records))
    for line in surface_lines(records):
        lines.append(f"  · {line}")
    lines.append("</ledger-status>")
    return lines


def _hub_lease_block(session_id, cwd) -> list[str]:
    """L-0911: heartbeat refresh (silent) if this session holds the lease,
    or the loud per-prompt NOT-THE-HUB banner if it doesn't and it's
    running in the hub's own cwd. Independent try/except from the ledger
    block above — see module docstring, A1."""
    if not session_id or cwd not in HUB_CWDS:
        return []
    try:
        # HUB_LEASE_BIN_DIR lets tests point this at a worktree copy of
        # hub_lease_lib.py; unset (the default) uses the live path, same
        # convention as every path override in hub_lease_lib.py itself.
        bin_dir = os.environ.get("HUB_LEASE_BIN_DIR") or str(LEDGER_BIN_DIR)
        sys.path.insert(0, bin_dir)
        import hub_lease_lib as hl  # type: ignore

        lease = hl.read_lease()
        v, age = hl.verdict(lease)
        if v in ("NONE", "DEAD"):
            return []  # nothing to heartbeat or warn about; security-tripwire claims at session start
        holder = lease.get("session_id")
        if holder == session_id:
            r = hl.claim(session_id=session_id, cwd=cwd, reason="heartbeat", auto=True)
            return []  # silent — a live holder heartbeating itself is not news
        return [hl.not_the_hub_banner(lease, v, age)]
    except Exception:
        return []  # fail open, always (A1)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}
    session_id = payload.get("session_id") if isinstance(payload, dict) else None
    cwd = payload.get("cwd") if isinstance(payload, dict) else None

    lines = []
    lines.extend(_ledger_block())
    lines.extend(_hub_lease_block(session_id, cwd))

    if not lines:
        return 0

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)   # never block a session
