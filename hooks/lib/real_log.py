#!/usr/bin/env python3
"""Shared module: locate the real `/log` file for a given session_id.

Extracted (L-0920) from hooks/pre-compact-stub.py (L-0802 round 5, formerly
lines 360-449 of that file) so hooks/log-staleness-enforcer.py (L-0920
Option A) and hooks/pre-compact-real-log.sh (L-0920 Option B) can both use
the exact same lookup pre-compact-stub.py already used -- the ONE mechanical
definition of "does a real log exist for this session" the whole L-0920
design depends on (see hub/reports/L-0920-plan.md, "Log age for this
session_id -- how it's measured").

No behavior change from the extracted pre-compact-stub.py version, with ONE
documented exception (L-0920 lane audit L2 -- see find_real_log() docstring
below): the footer check already allowed a file to carry MULTIPLE
`logged-session: <id>` footer lines (one per session that appended to it,
via commands/log.md's continuation-log rule) and already matched a footer
for THIS session_id anywhere in the file, not only a single terminal
footer. That behavior is preserved unchanged by this extraction; it is
called out here so a future edit never "tightens" this into a
last-line-only match, which would silently break every continuation log.

L-0920 SCOPE ADD (hub, 2026-09-23 4:20pm CT): the original match was a raw
substring check (`needle in text`, needle = f"logged-session: {session_id}").
The logging audit (hub/reports/logging-audit-2026-09-23.md) flagged this as
a possible false-match risk -- a substring check can match text that quotes
or references a footer without actually BEING one (e.g. prose describing a
different session's footer). Investigation of the audit's specific example
(session 56fb87b6 "matched" to a log whose audit-reported footer was
aee25698) found that file legitimately carries TWO full, correctly-formed
footer lines -- one per session, exactly the L2 continuation-log shape -- so
the substring check's answer was actually correct; the audit's own
single-footer regex (`.search()`, first match only) is what mischaracterized
it. See hub/reports/L-0920-build.md's scope-add section for the full
writeup. Regardless, tightening to a real per-line exact match is a genuine
hardening (closes the theoretical false-match risk above, and gives L2's
"multiple footers, one per line" contract a precise definition instead of an
implicit one) with no behavior change for any correctly-formed footer, so
it's implemented below: FOOTER_LINE_RE matches a full line (surrounding
whitespace stripped) that is EXACTLY `<!-- logged-session: <id> -->`, no
prefix/substring/fuzzy matching, and (following directly from full-string
equality) a short-hex id can never match a full-UUID session_id or vice
versa.

pre-compact-stub.py imports find_real_log / validate_session_id /
REAL_LOG_BASENAME_RE / SESSION_ID_RE / DEFAULT_MEMORY_CONVOS_DIR from here
now; see that file's own module docstring for the round-5 field-shape
discipline this lookup is part of.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


def _home_memory_dir():
    """~/.claude/projects/<home-slug>/memory -- the slug Claude Code
    derives from the real $HOME path (str(Path.home()).replace('/', '-')),
    computed at runtime so this hook works on any machine, not just the
    one it was written on."""
    slug = str(Path.home()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


# Where the real_log lookup searches for a real `/log` footer for this
# session. Same default path session-end-log.sh / log-coverage.py use.
DEFAULT_MEMORY_CONVOS_DIR = (
    _home_memory_dir() / "conversations"
)

# session_id must be a canonical UUID (8-4-4-4-12 hex groups) -- the actual
# shape Claude Code issues.
SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# The exact basename shape /log always writes: YYYY-MM-DD_slug.md.
REAL_LOG_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[\w.-]+\.md$")

# L-0920 scope add: a footer line must be the ENTIRE line (surrounding
# whitespace allowed) -- not a substring anywhere in the text. Matches
# `<!-- logged-session: <anything non-whitespace/non-'>'> -->`; the captured
# id is then compared for EXACT string equality against the session_id being
# searched for (see find_real_log), so a short-hex id can never match a full
# UUID and vice versa, and quoted/prose mentions of a footer never count.
FOOTER_LINE_RE = re.compile(r"^<!--\s*logged-session:\s*([^\s>]+)\s*-->$")


def validate_session_id(raw: str) -> str:
    """Strict shape validation: session_id must fullmatch a canonical UUID
    (8-4-4-4-12 hex groups) or it is dropped in favor of the "unknown"
    fallback -- never trusted into a filename or footer unvalidated."""
    return raw if SESSION_ID_RE.fullmatch(raw) else "unknown"


def has_footer_line(text: str, session_id: str) -> bool:
    """True iff TEXT contains a line (surrounding whitespace stripped) that
    is EXACTLY `<!-- logged-session: <session_id> -->` -- full-line match,
    exact string equality on the id, no substring/prefix/fuzzy match. Shared
    by find_real_log() (above) and log-staleness-enforcer.py's L-0920 scope
    add (c) simulated Write/Edit/MultiEdit footer check, so both use the
    exact same definition of "this file has this session's footer."

    [FIX ROUND 2 -- N1, revert] Fix round 1 added a fence-aware toggle that
    skipped any line between an unpaired/odd-count ``` marker and EOF. The
    round-2 re-gate found this was a REGRESSION, not a hardening: an
    unrelated, unmatched code-fence marker anywhere earlier in a shared,
    append-only multi-session log file (commands/log.md's own convention --
    old footers stay in place, new dated sections get appended to the SAME
    file, and those sections routinely contain code/command blocks) would
    permanently hide every REAL footer after it, with no self-recovery path
    (appending yet another entry lands after the same unclosed fence and is
    equally swallowed). The exact-full-line match this function already does
    (FOOTER_LINE_RE, matched against the stripped line, then exact string
    equality on the id) already rejects an inline/prose quote of a footer --
    that was the only real risk the fence-awareness change was meant to
    close, and it's already closed by the full-line-match discipline alone.
    The first bug-gate itself called an illustrative footer inside a
    *properly closed* fence "narrow, not a practical risk." So: back to an
    unconditional per-line scan, no fence state at all, which also keeps
    this function consistent with the live pre-compact-stub.py fallback
    matcher (B3, out of scope this round) that was never updated to match
    the fence logic in the first place."""
    for line in text.splitlines():
        stripped = line.strip()
        m = FOOTER_LINE_RE.match(stripped)
        if m and m.group(1) == session_id:
            return True
    return False


def find_real_log(session_id: str, convos_dir: Path) -> str | None:
    """Mechanical filesystem lookup. A candidate must pass ALL of:

      1. basename matches REAL_LOG_BASENAME_RE -- the exact shape /log
         always writes (YYYY-MM-DD_slug.md);
      2. os.path.realpath(candidate)'s PARENT directory is exactly
         os.path.realpath(convos_dir) -- i.e. it is a genuine, direct child
         of the real conversations dir, not a symlink pointing outside it
         and not a nested subdirectory entry;
      3. it contains a `logged-session: <id>` footer LINE for THIS
         session_id -- ANYWHERE in the file (L-0920 L2: a continuation log
         may carry several footer lines, one per session that appended to
         it via commands/log.md's continuation rule; this check matches
         this session's footer regardless of how many others share the
         file, and regardless of position -- not restricted to the last
         line), matched by FULL LINE (surrounding whitespace stripped)
         against FOOTER_LINE_RE, then exact string equality on the captured
         id vs session_id -- L-0920 scope add: no prefix/substring/fuzzy
         match, and no time-proximity fallback of any kind.

    Any candidate failing any check is dropped, not partially trusted. The
    VALUE returned (when found) is the original, non-realpath'd path string
    (str(latest)) -- realpath is used only for the identity check above, not
    for display, so the returned path stays human-readable and matches
    whatever convos_dir path this process was configured with."""
    try:
        if not convos_dir.exists():
            return None
        convos_real = os.path.realpath(str(convos_dir))
        candidates = []
        for md in convos_dir.glob("*.md"):
            if not REAL_LOG_BASENAME_RE.fullmatch(md.name):
                continue  # dropped -- not /log's exact filename shape
            real_path = os.path.realpath(str(md))
            if os.path.dirname(real_path) != convos_real:
                continue  # dropped -- symlink escape or not a direct child
            try:
                text = md.read_text(errors="ignore")
            except OSError:
                continue
            if not has_footer_line(text, session_id):
                continue  # dropped -- no exact-line footer for THIS session
            candidates.append(md)
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return str(latest)
    except Exception:
        return None
