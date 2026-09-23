#!/usr/bin/env python3
"""SessionStart hook (matcher: "compact") — re-injects the PreCompact stub that
pre-compact-stub.py wrote just before this compaction.

L-0802 (round 5, after bug-gate FAIL on bcc41d50 — see pre-compact-stub.py's
module docstring for the full round-5 rationale: `cwd` and `files_touched`
were removed from the stub entirely, and every remaining field is now
strictly shape-validated). This is the read-back half of the pair:
PreCompact cannot inject additionalContext (confirmed in
code.claude.com/docs/en/hooks.md), so the stub written to disk before
compaction has to be re-surfaced separately, right after compaction, by a
SessionStart hook whose plain stdout Claude Code adds to context (confirmed
recipe: code.claude.com/docs/en/hooks-guide.md, "Re-inject context after
compaction"). Session id is unchanged across a compaction, so the stub
written for this session_id under PreCompact is found by exact footer match
here.

The stub this hook reads now carries only session_id, written, ticket ids,
commit short-hashes, and real_log (the path of the newest REAL `/log` file
for this session, found by its `logged-session:` footer in the real memory
conversations dir) — there is no cwd field and no files-touched field to
read any more; this hook uses only real_log to compose its instruction:

    - a real /log was found for this session  -> "Resume from <path>
      (Resume state + Pickup prompts)."
    - no real /log exists yet for this session -> "If there's no real /log
      for this session yet, run /log now, then continue."

Round 5 also tightens resume_instruction() itself: the captured `real_log`
value is re-validated against REAL_LOG_VALUE_RE (an absolute path ending in
the exact YYYY-MM-DD_slug.md shape /log always writes) before it is ever
used in the composed instruction — defense-in-depth against a hand-edited
or corrupted stub file feeding an arbitrary string into the "Resume from"
line, on top of pre-compact-stub.py's own write-time validation.

The rest of the (already-mechanical, already-bounded) stub body is still
printed below that instruction for ticket/commit context, unchanged in
spirit from rounds 1-4.

Round-3 fixes (see ~/.claude/hub/reports/buggate-L0802-r2.md, blocker 1),
still in place: stubs are read from their own dedicated dir,
~/.claude/compact-stubs/, never the memory conversations dir.

Round-2 fixes (see ~/.claude/hub/reports/buggate-L0802.md), still in place:

  1. Looks for the `<!-- compact-stub: <id> -->` footer / `stub-*.md`
     filename that pre-compact-stub.py writes. The stub is a safety net,
     never proof a real /log ran — the resume instruction above makes that
     explicit either way.

  2. The stub file it reads is already bounded by pre-compact-stub.py
     (<=12KB), but this script caps the REINJECTED text at MAX_PICKUP bytes
     independently, truncated with a marker, so a future change to the
     writer (or a hand-edited stub) can't dump an unbounded amount of text
     back into context right after the compaction meant to shrink it.

Fail-open, always: prints nothing and exits 0 on any problem — this must never
block or slow session start.

Stub dir is overridable via PRECOMPACT_STUB_DIR (test harnesses point this at
a temp dir instead of the real one). Unset in production — defaults to the
real path, matching pre-compact-stub.py's default exactly.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

DEFAULT_STUB_DIR = Path.home() / ".claude/compact-stubs"
STUB_FOOTER_PREFIX = "compact-stub"
FNAME_PREFIX = "stub"
MAX_PICKUP = 6 * 1024
TRUNC_MARK = "\n...[pickup truncated by session-start-compact-pickup.py]\n"

# Matches the exact `- real_log: <value>` line pre-compact-stub.py writes in
# its structural (unredacted) block. Anything that doesn't match this shape
# is treated as "no real log" — fail toward the safe instruction, not toward
# printing something unexpected.
REAL_LOG_FIELD_RE = re.compile(r"(?m)^- real_log:\s*(.+?)\s*$")
# Round 5: the captured value itself must ALSO look like a real /log path —
# absolute, ending in the exact YYYY-MM-DD_slug.md basename shape /log
# always writes — before this hook will echo it into the "Resume from"
# instruction. Defense-in-depth on top of the writer's own validation.
REAL_LOG_VALUE_RE = re.compile(r"^/.*/\d{4}-\d{2}-\d{2}_[\w.-]+\.md$")
NO_REAL_LOG_SENTINEL = "(none found)"


def resume_instruction(body: str) -> str:
    m = REAL_LOG_FIELD_RE.search(body)
    real_log = m.group(1).strip() if m else ""
    if real_log and real_log != NO_REAL_LOG_SENTINEL and REAL_LOG_VALUE_RE.match(real_log):
        return (
            f"Resume from {real_log} (Resume state + Pickup prompts). "
            "If there's no real /log for this session yet, run /log now, then continue."
        )
    return "If there's no real /log for this session yet, run /log now, then continue."


def main() -> int:
    try:
        raw_in = sys.stdin.read()
    except Exception:
        raw_in = ""
    try:
        payload = json.loads(raw_in) if raw_in.strip() else {}
    except Exception:
        payload = {}

    source = payload.get("source")
    session_id = str(payload.get("session_id") or "")
    # Defensive — the settings.json matcher "compact" should already restrict
    # firing to this case, but don't trust that alone.
    if source != "compact" or not session_id:
        return 0

    try:
        stub_dir = Path(os.environ.get("PRECOMPACT_STUB_DIR") or DEFAULT_STUB_DIR)
        if not stub_dir.exists():
            return 0

        footer = f"{STUB_FOOTER_PREFIX}: {session_id}"
        candidates = []
        for p in stub_dir.glob(f"*{FNAME_PREFIX}-{session_id[:8]}*.md"):
            try:
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if footer in text:
                candidates.append(p)

        if not candidates:
            return 0

        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        body = latest.read_text(errors="ignore")
    except Exception:
        return 0

    message = resume_instruction(body)

    body_bytes = body.encode("utf-8", "ignore")
    if len(body_bytes) > MAX_PICKUP:
        budget = max(MAX_PICKUP - len(TRUNC_MARK.encode("utf-8")), 0)
        body = body_bytes[:budget].decode("utf-8", "ignore") + TRUNC_MARK

    # Plain stdout: Claude Code adds this to context automatically on SessionStart.
    print("Context from just before this compaction (auto-recovered, L-0802):")
    print(message)
    print("")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
