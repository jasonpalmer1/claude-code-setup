#!/usr/bin/env python3
"""UserPromptSubmit hook — ledger status banner + surfaced items + hub-lease
heartbeat/banner (L-0911) + real-clock line (L-0939).

Prints the same "Open: N * Needs you: M" / WORKING-or-IDLE banner and the
surface lines the `ledger` CLI computes (~/.claude/hub/bin/ledger banner /
surface), both built from the shared ledger_lib.banner_lines() and
surface_lines() functions so the hook and the CLI can never drift apart.

L-0939 adds the first line inside the <ledger-status> block: the current
Central time, e.g. "Now: 5:22pm CT, Wed 9/23" (lowercase am/pm, no
leading zero on the hour, computed with zoneinfo America/Chicago so it is
correct across the DST boundary). This exists because chats kept writing
guessed Central times into messages -- three misses on 2026-09-23 despite
a written rule -- so the real clock now rides on every single prompt,
impossible to miss. The clock line is independent of the ledger-content
lines below it (A1 pattern): it prints even if the ledger read/fold fails,
and the block itself now appears whenever the clock succeeds, even with no
ledger content. LEDGER_BANNER_HOOK_NOW_UTC (ISO 8601, e.g.
"2026-07-15T22:22:00+00:00") overrides "now" for deterministic tests; unset
in production, where it always uses the real clock.

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
errors, the clock line still prints and the hook exits 0. A status hook
must never be able to block a session. The clock block, the ledger-content
block, and the hub-lease block are independent try/excepts — a bug in one
must never take out the other (A1).

Wire in settings.json:
  {"hooks": {"UserPromptSubmit": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/ledger-banner-hook.py"}]}]}}
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Perf (L-0939): set PYTHONTZPATH to the platform-standard zoneinfo search
# dirs *before* importing zoneinfo, if the caller hasn't already set it.
# zoneinfo._tzpath.reset_tzpath() otherwise resolves TZPATH via
# sysconfig.get_config_var(), which on macOS pulls in sysconfig +
# _osx_support + re just to recompute the exact same default list --
# measured ~3-4ms of pure per-process overhead for zero behavior change.
# setdefault() means a real PYTHONTZPATH from the environment always wins;
# this only fills in the gap on hosts that don't already have one, and a
# wrong guess just fails ZoneInfo() lookups the normal way (caught by
# _clock_lines()'s own try/except below, same fail-open guarantee).
os.environ.setdefault(
    "PYTHONTZPATH",
    os.pathsep.join(
        ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo", "/etc/zoneinfo")
    ),
)
from zoneinfo import ZoneInfo  # noqa: E402 -- must follow the PYTHONTZPATH setdefault above

LEDGER_BIN_DIR = Path.home() / ".claude/hub/bin"
HOME = Path.home()
HUB_CWDS = (str(HOME), str(HOME / ".claude/hub"))

_central_tz_cache: ZoneInfo | None = None


def _central_tz() -> ZoneInfo:
    """Lazy, cached construction of the America/Chicago zone.

    Deliberately NOT built at module level (A1 violation caught in review):
    ZoneInfo("America/Chicago") does real lookup work (tzdata file I/O) and
    can raise (bad/missing/empty PYTHONTZPATH, corrupt tzdata, etc). Built
    at import time, that exception would kill the whole module before
    main() even runs -- taking the ledger-content block and the hub-lease
    heartbeat down with it, not just the clock line. Built lazily here, the
    only place it's called is inside _now_line(), which _clock_lines()
    already wraps in its own try/except -- so a broken zone can only ever
    silence the clock line, never the rest of the hook. Cached because
    within one process (one hook invocation) it's the same lookup every
    call; there's no cross-process cache since each prompt is a fresh
    interpreter anyway.
    """
    global _central_tz_cache
    if _central_tz_cache is None:
        _central_tz_cache = ZoneInfo("America/Chicago")
    return _central_tz_cache


def _now_utc() -> datetime:
    """The current UTC instant, or a fixed test instant from
    LEDGER_BANNER_HOOK_NOW_UTC (ISO 8601) when set. Test-only override,
    same convention as LEDGER_PATH / HUB_LEASE_BIN_DIR elsewhere in this
    hook family."""
    override = os.environ.get("LEDGER_BANNER_HOOK_NOW_UTC")
    if override:
        dt = datetime.fromisoformat(override)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _now_line() -> str:
    """"Now: 5:22pm CT, Wed 9/23" — current Central time, DST-correct via
    zoneinfo. Lowercase am/pm, no leading zero on the hour or the
    month/day (built by hand, not strftime, so it can't silently regain a
    leading zero on a platform without %-m/%-d)."""
    now_ct = _now_utc().astimezone(_central_tz())
    hour12 = now_ct.hour % 12 or 12
    ampm = "am" if now_ct.hour < 12 else "pm"
    return f"Now: {hour12}:{now_ct.minute:02d}{ampm} CT, {now_ct.strftime('%a')} {now_ct.month}/{now_ct.day}"


def _clock_lines() -> list[str]:
    try:
        return [_now_line()]
    except Exception:
        return []  # fail open, always — the ledger-content block below is independent (A1)


def _ledger_content_lines() -> list[str]:
    try:
        sys.path.insert(0, str(LEDGER_BIN_DIR))
        from ledger_lib import LEDGER_PATH, banner_lines, fold, read_events, surface_lines  # type: ignore

        if not LEDGER_PATH.exists():
            return []
        records = fold(read_events())
    except Exception:
        return []  # fail open, always — never let a ledger bug take out the clock line (A1)

    if not records:
        return []

    lines = list(banner_lines(records))
    for line in surface_lines(records):
        lines.append(f"  · {line}")
    return lines


def _gate_content(session_id, content: list[str]) -> list[str]:
    """L-1559 item 10: drop the ledger-content lines when byte-identical to
    what this session was last shown (hash keyed by session + hook). The
    hash covers every line, so a changed NEEDS-YOU/Open/WORKING count or
    surface item always re-emits. Resets on SessionStart (memory-activate)
    (no periodic refresh). Any error -> emit (fail open). The Now: clock
    line rides only with changed content (2026-10-05)."""
    if not content or not session_id or os.environ.get("EMIT_ONCE_DISABLE"):
        return content
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
        import emit_once  # type: ignore

        if emit_once.should_emit(session_id, "ledger-banner", "\n".join(content)):
            return content
        return []
    except Exception:
        return content


def _ledger_block(session_id=None) -> list[str]:
    """the operator 2026-10-05 (tap 22): the whole block, including "Now:", prints only
    when the ledger content changed since this session last saw it. A new clock
    time alone is never a reason to emit."""
    content = _gate_content(session_id, _ledger_content_lines())
    if not content:
        return []
    clock = _clock_lines()
    return ["<ledger-status>", *clock, *content, "</ledger-status>"]


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
    lines.extend(_ledger_block(session_id))
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
