#!/usr/bin/env python3
"""PreToolUse hook (L-0920, Option A) — the actual guarantee behind
"a real /log stays <=50min old at every auto-compact": once a LIVE session's
real log for this session_id is more than 50 minutes stale (or the session
itself is that old with no log yet), non-exempt tool calls are DENIED until
a real log lands. See hub/reports/L-0920-plan.md for the full design, and
hub/reports/L-0920-plan.md's "Lane audit" (L1-L6) and "HUB AUDIT" (H1-H5)
sections for the amendments this implementation follows exactly — both
override the planner's original draft; this file implements the AMENDED
design, not the draft.

## Staleness formula
  real_log   = find_real_log(session_id, convos_dir)   (hooks/lib/real_log.py
               -- the SAME lookup pre-compact-stub.py uses, unchanged behavior
               except L2: a file may carry several `logged-session:` footers,
               one per session that appended to it; ANY footer for THIS
               session_id anywhere in the file counts.)
  deadline   = mtime of real_log, if found, else this session's own start time
               (session_start_time() below: transcript st_birthtime, else the
               first timestamped JSONL line, else st_ctime, else "now" — an
               unreadable transcript is treated as a session that just
               started, i.e. never stale, never a false block).
  stale      = (now - deadline) > 50 minutes
This gives "sessions under 50 minutes old are exempt" for free: with no log
yet, deadline falls back to session_start_time, so a young session can never
be stale regardless of whether it has logged.

## Exemptions (checked in this order, first match wins, all before the
## staleness check) — L-0920 lane audit L1 DROPPED the planner's draft
## "layer 3" (env-var inheritance exemption for anything that merely LOOKS
## like a headless/child session) because the hub and every lane also run
## with that exact env signature, which would have exempted the sessions
## this ticket exists to cover. There is no env-var-shape exemption any
## more; only an explicit opt-out a launcher sets on itself.
  1. LOG_ENFORCER_DISABLE=1        -- kill switch, checked first, always wins.
  2. LOG_ENFORCER_EXEMPT=1          -- explicit self-declared opt-out. Any
     headless `claude -p` launcher that could plausibly run past 50 minutes
     sets this in its OWN env right before its `claude` call (see L-0920
     build report for the grepped list). The default with nothing set is
     ENFORCED. Checked before the tool is even inspected -- this is a
     whole-process opt-out, not tool-specific.
  3. Tool not in the gated set (Bash/Write/Edit/MultiEdit/Task/Agent/
     WebFetch/WebSearch/NotebookEdit) -- always allow. Defensive only; the
     settings.json matcher already restricts which calls reach this script.
  [FIX ROUND 2 -- B1, reopened by re-gate, hub ruling, binding]: round 1
  deleted rule "4b" (Task/Agent spawn always allowed) but left the H1
  worker-exemption (payload carries `agent_id`/`agent_type` -> unconditional
  allow) running BEFORE tool_name was even inspected. The re-gate's live rig
  proved a worker's OWN nested spawn call (worker A calling Task/Agent to
  create worker B) carries worker A's `agent_id`/`agent_type` in that SAME
  spawn payload, exactly like its ordinary Bash calls do -- so the H1
  exemption alone let any already-running worker chain-spawn unlimited
  further workers while the top-level session stayed stale forever, one hop
  deeper than round 1 closed. The fix: `tool_name in ("Task", "Agent")` is
  now checked BEFORE the H1 exemption, unconditionally -- a spawn call is
  gated by staleness whether or not agent_id/agent_type is present on its
  payload. The H1 exemption now applies ONLY to non-spawn tools (Bash,
  Write, Edit, MultiEdit, WebFetch, WebSearch, NotebookEdit), where it still
  means exactly what it always meant: a worker's own ORDINARY tool call is
  never blocked by its parent's staleness. Staleness itself is still judged
  from the payload's session_id alone (never agent_id) -- a nested spawn's
  session_id is the TOP-level session's id (the re-gate's rig confirmed this
  is what a real nested payload carries), so gating on it is gating on the
  thing that's actually stale, not on the worker's own identity.
  4. L6 structural allow-list shapes (see ALWAYS_ALLOWED_* below) -- the
     exact sequence /log itself needs (write the file, `git add`, `git
     commit`) is never gated, so the block can always be cleared.
  5. Deadline not yet due (not stale) -- allow.
Anything else: DENY (exit 2), message built by build_deny_message() (L5
wording + H2 exact-path naming, both below).

## Why a spawn call is gated first, then non-spawn calls get the H1 exemption
`main()` now branches on `tool_name in ("Task", "Agent")` immediately after
the gated-tool-set check: a spawn call skips the H1 agent_id/agent_type
exemption entirely and goes straight to the L6/staleness gate below (same
path the round-1 fix already built); any OTHER gated tool still gets the H1
exemption first, unconditionally, exactly as before. This means a worker
already mid-flight when staleness begins keeps making its own ordinary tool
calls (Bash/Write/etc, still carrying its own agent_id, still exempt) but
can never spawn a NEW worker once its top-level session is stale -- closing
the nested-spawn hole the re-gate found, without reopening H1's original
purpose (never blocking a worker's own non-spawn work).

## Fail-open, always
The entire main() body is wrapped in try/except; ANY exception (bad JSON,
missing dir, regex error, unreadable transcript) -> exit 0 (allow) + one
alarm line, same convention as pre-compact-stub.py's alarm(). Hook timeout
is set to 8s in settings.json; a hook timeout is treated as a failed hook,
which combined with fail-open-by-design means a timeout can only ever mean
"allowed," never "stuck denied."

## Test-only overrides (never set in production)
  LOG_ENFORCER_MEMORY_CONVOS_DIR   -- convos_dir override (never touch the
                                       real memory dir from a test).
  LOG_ENFORCER_ALARM_LOG           -- alarm log override.
  LOG_ENFORCER_SESSION_START_OVERRIDE -- ISO8601 timestamp; when set, used
                                       AS session_start_time instead of
                                       stat-ing the transcript. Exists only
                                       so a test (or the H1 live-proof rig)
                                       can backdate a session without faking
                                       filesystem timestamps on a live
                                       `claude -p` run, where the transcript
                                       path and its birthtime are owned by
                                       the harness, not by the test.
  LOG_ENFORCER_STALE_MINUTES       -- override the 50-minute threshold, for
                                       faster test iteration. Defaults to 50.
  LOG_ENFORCER_DEBUG_PAYLOAD_LOG   -- when set, append every raw stdin
                                       payload this hook receives to this
                                       file, verbatim, one JSON object per
                                       line. Used to prove (not just assert)
                                       what a real subagent's PreToolUse
                                       payload carried.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.real_log import (  # noqa: E402
    DEFAULT_MEMORY_CONVOS_DIR,
    find_real_log,
    has_footer_line,
    validate_session_id,
)

try:
    from zoneinfo import ZoneInfo
    CENTRAL = ZoneInfo("America/Chicago")
except Exception:  # pragma: no cover - stdlib always has zoneinfo on py>=3.9
    CENTRAL = timezone(timedelta(hours=-6))

DEFAULT_ALARM_LOG = Path.home() / ".claude/hub/delegation-alarms.log"
STALE_MINUTES_DEFAULT = 50

# Only these tools are ever gated -- Read/Grep/Glob/TodoWrite (and anything
# else) is cheap, needed to compose a log, and always allowed by construction.
GATED_TOOLS = {
    "Bash", "Write", "Edit", "MultiEdit", "Task", "Agent",
    "WebFetch", "WebSearch", "NotebookEdit",
}

# --- L6 structural allow-list -----------------------------------------------
# Write/Edit/MultiEdit into one of the three files /log itself writes
# (commands/log.md steps 2-3-4): the log file itself, the index, or MEMORY.md.
ALLOWED_WRITE_BASENAMES = {"conversations_index.md", "MEMORY.md"}

# [FIX ROUND 1 -- B2] Bash allow-list, rewritten from scratch. The OLD rule
# allowed any Bash command that merely CONTAINED the literal substring
# "memory/conversations" anywhere -- the bug-gate found this defeated BOTH
# the staleness gate (a wholly unrelated command with a trailing comment
# mentioning the path cleared it) AND the footerless-log refusal (a Bash
# heredoc writing a brand-new footerless file into the real conversations
# dir was allowed outright, because the command text merely mentioned the
# directory). That substring rule and the `cd <dir> && git ...` pattern are
# BOTH deleted, not tightened.
#
# The new rule: while stale, Bash is allowed ONLY for git status/diff/add/
# commit/log invocations, tokenized with shlex (so quoting is honored, not
# just substring-matched), where:
#   - the command contains NONE of: `;` `&` `|` a backtick `$(` `<` `>`
#     (no redirection, no heredoc, no `&&`/`||`, no pipes, no command
#     substitution) and no embedded newline (no multi-line/heredoc bodies);
#   - the first token is exactly "git" (no `cd ... &&`, no wrapper);
#   - every pathspec (any non-flag token, or every token after a `--` for
#     `commit`) resolves to a path under the real conversations dir;
#   - `add`/`commit` must name at least one such pathspec explicitly (a
#     bare `git add -A` or a pathspec-less `git commit` -- which would
#     stage/commit whatever happens to already be staged, not provably
#     scoped to a log file -- is DENIED, not allowed).
# `status`/`diff`/`log` may be given with zero pathspecs (plain, harmless,
# read-only) or with pathspecs that must still resolve under the
# conversations dir.
DANGEROUS_BASH_CHARS = re.compile(r"[;&|`<>]|\$\(")


def alarm_log_path() -> Path:
    override = os.environ.get("LOG_ENFORCER_ALARM_LOG")
    return Path(override) if override else DEFAULT_ALARM_LOG


def alarm(msg: str) -> None:
    try:
        log = alarm_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log.open("a") as f:
            f.write(f"{stamp} L-0920 log-staleness-enforcer {msg}\n")
    except Exception:
        pass  # even the alarm must never raise


def convos_dir() -> Path:
    override = os.environ.get("LOG_ENFORCER_MEMORY_CONVOS_DIR")
    return Path(override) if override else DEFAULT_MEMORY_CONVOS_DIR


def stale_minutes_threshold() -> int:
    try:
        return int(os.environ.get("LOG_ENFORCER_STALE_MINUTES", STALE_MINUTES_DEFAULT))
    except Exception:
        return STALE_MINUTES_DEFAULT


def _first_timestamp_in_transcript(transcript_path: str, max_lines: int = 50) -> datetime | None:
    """Scan the first few JSONL lines for a top-level `timestamp` field (the
    very first lines of a transcript are metadata-only and rarely carry
    one). Best-effort; returns None on any failure."""
    try:
        with open(transcript_path, "r", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts = d.get("timestamp") if isinstance(d, dict) else None
                if ts:
                    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return None


def session_start_time(transcript_path: str) -> datetime:
    """This session's own start time. Test-only override first (see module
    docstring); then transcript st_birthtime (macOS/APFS); then the first
    timestamped JSONL line; then st_ctime; then "now" (an unreadable
    transcript reads as a session that just started -- never a false
    block)."""
    override = os.environ.get("LOG_ENFORCER_SESSION_START_OVERRIDE")
    if override:
        try:
            return datetime.fromisoformat(override.replace("Z", "+00:00"))
        except Exception:
            pass

    try:
        st = os.stat(transcript_path)
        birth = getattr(st, "st_birthtime", None)
        if birth:
            return datetime.fromtimestamp(birth, tz=timezone.utc)
    except Exception:
        pass

    ts = _first_timestamp_in_transcript(transcript_path) if transcript_path else None
    if ts:
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    try:
        st = os.stat(transcript_path)
        return datetime.fromtimestamp(st.st_ctime, tz=timezone.utc)
    except Exception:
        pass

    return datetime.now(timezone.utc)


def _central(dt: datetime) -> str:
    try:
        return dt.astimezone(CENTRAL).strftime("%-I:%M %p CT")
    except Exception:
        return dt.isoformat()


def build_deny_message(session_id: str, real_log: str | None, deadline: datetime,
                        stale_minutes: int, now: datetime) -> str:
    """H2 + L5. L5: the message must say EXACTLY "Log is <N> min stale.
    Write/append this session's log (Resume state + Pickup prompts), commit
    it, then continue." -- never mentions /clear or stopping, times in
    Central. H2: names this session's EXACT log path when one exists; when
    none exists, names the conversations dir, the required filename shape,
    the footer line, and a one-line command shape that satisfies it."""
    # [FIX ROUND 1 -- B1] Exact one-step fix, with the real session_id in the
    # lead sentence itself (not only buried in the detail block): run /log,
    # or write this exact footer line into the session's log by hand.
    footer_line = f"<!-- logged-session: {session_id} -->"
    lead = (
        f"Log is {stale_minutes} min stale. One-step fix: run /log now, or "
        f"write this exact line into this session's log yourself: {footer_line} "
        "-- then commit it and continue."
    )

    script = str(Path.home() / ".claude/hub/bin/hub-log-append")
    sid_for_cmd = session_id
    cant_edit = (
        "\n\nIf this session cannot Edit/Write (background-session isolation: "
        "\"Call EnterWorktree first\"), run this ONE Bash command instead "
        "(single-quoted text, literal \\n for newlines, no ; & | $ ` < > in the text; "
        "it must contain the words Resume state and Pickup prompts):\n"
        f"  {script} {real_log or str(convos_dir() / (now.astimezone(CENTRAL).strftime('%Y-%m-%d') + '_<slug>.md'))} "
        f"{sid_for_cmd} 'Resume state: <...>\\nPickup prompts: <...>'"
    )
    if real_log:
        detail = (
            f"\n\nThis session's log: {real_log}\n"
            f"(last real log activity: {_central(deadline)}; now: {_central(now)})\n"
            "Append a new dated section (Resume state + Pickup prompts) and this "
            f"session's footer line to it:\n  {footer_line}\n"
            # [FIX ROUND 1 -- B2] No `&&` in the recommended command any more
            # -- the Bash allow-list itself now rejects any command
            # containing `&&` (see _bash_matches_allowlist), so the fix this
            # message recommends must itself be two separate Bash calls, not
            # a chained one-liner.
            "Then run (as two separate Bash calls -- no `&&` chaining):\n"
            f"  git add {real_log}\n"
            f"  git commit -m 'log: session update' -- {real_log}"
        )
    else:
        convos = convos_dir()
        example_name = f"{now.astimezone(CENTRAL).strftime('%Y-%m-%d')}_<slug>.md"
        example_path = str(convos / example_name)
        detail = (
            f"\n\nNo log exists yet for this session (session started "
            f"{_central(deadline)}; now: {_central(now)}).\n"
            "Note: a log with no `logged-session` footer for THIS session "
            "never counts as found, even if a file with the right name and "
            "content otherwise exists -- the footer line below is required, "
            "not optional.\n"
            f"Write one under: {convos}\n"
            f"Required filename shape: YYYY-MM-DD_<slug>.md (e.g. {example_path})\n"
            f"Required footer line (last line of the file):\n  {footer_line}\n"
            "Then run (as two separate Bash calls -- no `&&` chaining):\n"
            f"  git add {example_path}\n"
            f"  git commit -m 'log: session summary' -- {example_path}"
        )
    return lead + detail + cant_edit


def _tool_matches_write_allowlist(tool_input: dict) -> bool:
    """MEMORY.md / conversations_index.md only -- these are not /log files
    themselves (no footer convention applies to them), so they stay an
    unconditional allow. Convos-dir *.md files are handled separately by
    _convos_dir_footer_check() below (L-0920 scope add (c)), which can deny
    even though the OLD version of this function would have allowed any
    write into the convos dir unconditionally."""
    fp = str(tool_input.get("file_path") or "")
    if not fp:
        return False
    return os.path.basename(fp) in ALLOWED_WRITE_BASENAMES


def _in_convos_dir_md(fp: str) -> bool:
    if not fp or not fp.endswith(".md"):
        return False
    try:
        resolved = os.path.realpath(fp)
        convos_real = os.path.realpath(str(convos_dir()))
        return os.path.dirname(resolved) == convos_real
    except Exception:
        return False


def _simulate_resulting_content(tool_name: str, tool_input: dict) -> str | None:
    """Best-effort reconstruction of what FILE_PATH's content would be AFTER
    this Write/Edit/MultiEdit lands, for the L-0920 scope-add (c) footer
    check. Returns None when it cannot be determined (unreadable existing
    file, malformed tool_input) -- callers must treat None as "cannot judge,
    do not deny on this," never as "no footer."""
    fp = str(tool_input.get("file_path") or "")
    if tool_name == "Write":
        content = tool_input.get("content")
        return content if isinstance(content, str) else None

    try:
        current = Path(fp).read_text(errors="ignore")
    except Exception:
        return None  # can't read the existing file -- fail open, don't block

    if tool_name == "Edit":
        old = tool_input.get("old_string")
        new = tool_input.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        return current.replace(old, new) if tool_input.get("replace_all") else current.replace(old, new, 1)

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        text = current
        for e in edits:
            if not isinstance(e, dict):
                return None
            old = e.get("old_string")
            new = e.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
            text = text.replace(old, new) if e.get("replace_all") else text.replace(old, new, 1)
        return text

    return None


def build_footer_deny_message(session_id: str, file_path: str) -> str:
    """L-0920 scope add (c): a Write/Edit/MultiEdit into memory/conversations/
    *.md that would leave the file with no exact-line `logged-session`
    footer for THIS session is refused outright -- independent of staleness
    (a fresh session gets this too), because find_real_log() requires that
    exact footer line and nothing else counts as a real log. Gives the exact
    line to add, so this can never deadlock: a Write/Edit whose result DOES
    carry the footer is always allowed (see _convos_dir_footer_check)."""
    footer_line = f"<!-- logged-session: {session_id} -->"
    return (
        f"Refused: {file_path} would end up with no `logged-session` footer "
        "for this session. A log with no footer for this session never "
        "counts as found (find_real_log requires an exact-line match, no "
        "substring/fuzzy match) -- so it could never clear staleness either. "
        "This check runs regardless of whether this session is currently "
        "stale.\n"
        f"Add this exact line to the file:\n  {footer_line}"
    )


def _convos_dir_footer_check(session_id: str, tool_name: str, tool_input: dict) -> str:
    """Returns "allow", "deny", or "skip" (not applicable / can't judge --
    caller falls through to whatever the rest of main() would otherwise do).
    Only called for Write/Edit/MultiEdit whose file_path resolves into the
    conversations dir with a .md extension and is NOT one of the
    ALLOWED_WRITE_BASENAMES special files (those are handled earlier and
    never reach here)."""
    fp = str(tool_input.get("file_path") or "")
    if not _in_convos_dir_md(fp):
        return "skip"
    resulting = _simulate_resulting_content(tool_name, tool_input)
    if resulting is None:
        return "skip"  # can't determine -- fail open, don't block on unknowns
    return "allow" if has_footer_line(resulting, session_id) else "deny"


def _pathspec_under_convos(tok: str) -> bool:
    """True iff TOK, taken as a filesystem path, resolves under the real
    conversations dir. Must work for paths that don't exist yet (a `git add`
    of a brand-new log file, e.g. the deny message's own recommended
    example_path) as well as ones that do. Only absolute paths are
    accepted: every path this allow-list is meant to cover is always
    absolute, and accepting a relative path here would make the check
    depend on an unknown cwd.

    Two comparison modes, chosen by whether TOK exists on disk:
      - exists: realpath BOTH sides and compare -- closes a symlink escape,
        the same discipline find_real_log() uses.
      - does not exist yet: compare lexically-normalized (os.path.normpath)
        forms of BOTH sides, with NEITHER side realpath'd. Realpath-ing only
        the base while leaving an unresolved candidate would always
        mismatch on a host where the conversations dir sits behind a
        symlink (e.g. macOS's $TMPDIR is under /var, which is itself a
        symlink to /private/var) -- exactly the "not created yet" case this
        function exists to allow, since the deny message's own
        example_path is built from convos_dir() without ever calling
        realpath. A nonexistent path can't be symlink-escaped on the
        filesystem in the first place (there's nothing there to resolve),
        so lexical (".."-collapsing) comparison is the correct and
        sufficient check for this branch."""
    if not tok or not os.path.isabs(tok):
        return False
    base_raw = str(convos_dir())
    cand = os.path.normpath(tok)
    if os.path.exists(cand):
        try:
            base_real = os.path.realpath(base_raw)
            cand_real = os.path.realpath(cand)
        except Exception:
            return False
        return cand_real == base_real or cand_real.startswith(base_real + os.sep)
    base_norm = os.path.normpath(base_raw)
    return cand == base_norm or cand.startswith(base_norm + os.sep)


def _hub_log_append_paths() -> set:
    here = Path(__file__).resolve().parent.parent / "hub/bin/hub-log-append"
    home = Path.home() / ".claude/hub/bin/hub-log-append"
    return {str(here), str(home)}


def _is_hub_log_append(tok: str) -> bool:
    """L-1774: first token must be the absolute path of the sanctioned log
    script, byte-exact (no relative path, no PATH lookup, no `..`)."""
    return tok in _hub_log_append_paths()


_HLA_CMD_RE = re.compile(
    r"(/[A-Za-z0-9._/-]+) (/[A-Za-z0-9._/-]+) "
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12} "
    r"'[^'$`()*?\[\]{}~=!#;&|<>\r\n]*'")
_GIT_SAFE_TOKEN_RE = re.compile(r"[A-Za-z0-9._/:,@+ -]*")


def _bash_matches_allowlist(tool_input: dict) -> bool:
    """[FIX ROUND 1 -- B2] See the module-level comment above
    DANGEROUS_BASH_CHARS for the full rule. Fails closed (not allow-listed)
    on anything ambiguous: unparseable quoting, an unrecognized subcommand,
    a missing required pathspec, or a pathspec outside the conversations
    dir."""
    cmd = str(tool_input.get("command") or "")
    if not cmd or "\n" in cmd:
        return False  # no multi-line commands -- rules out any heredoc body
    if DANGEROUS_BASH_CHARS.search(cmd):
        return False  # no redirection/heredoc/;/&&/||/pipes/$(/backticks
    m = _HLA_CMD_RE.fullmatch(cmd)
    if m and m.group(1) in _hub_log_append_paths():
        # L-1774 R1: the WHOLE raw string must fullmatch a strict shape, so
        # zsh glob qualifiers, =(..), ?, [, {, ~, ! can never run code. The
        # script itself re-validates id, headings and content length.
        return _pathspec_under_convos(m.group(2))
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return False  # unbalanced quoting etc -- fail closed
    # R1: every token must be plain (no glob/qualifier/process-subst chars)
    # before any path is resolved -- same hole existed in the git rule.
    if not all(_GIT_SAFE_TOKEN_RE.fullmatch(t) for t in tokens):
        return False
    if len(tokens) < 2 or tokens[0] != "git":
        return False
    sub, rest = tokens[1], tokens[2:]
    if sub not in ("status", "diff", "log", "add", "commit"):
        return False
    if sub == "commit":
        if "--" not in rest:
            return False  # commit must explicitly scope itself to a pathspec
        paths = rest[rest.index("--") + 1:]
    else:
        paths = [t for t in rest if not t.startswith("-")]
    if sub in ("add", "commit") and not paths:
        return False  # add/commit must name at least one in-scope path
    return all(_pathspec_under_convos(p) for p in paths)


def main() -> int:
    try:
        raw_in = sys.stdin.read()
    except Exception:
        raw_in = ""
    try:
        payload = json.loads(raw_in) if raw_in.strip() else {}
    except Exception:
        payload = {}

    # Test-only introspection: when set, append the raw stdin payload to a
    # file so a test (e.g. the H1 live-proof rig) can prove what a real
    # subagent's PreToolUse payload actually carried. No effect unless set;
    # never set in production.
    dump_path = os.environ.get("LOG_ENFORCER_DEBUG_PAYLOAD_LOG")
    if dump_path:
        try:
            with open(dump_path, "a") as f:
                f.write(raw_in.strip() + "\n")
        except Exception:
            pass

    try:
        # 1. Kill switch -- always wins, checked first.
        if os.environ.get("LOG_ENFORCER_DISABLE") == "1":
            return 0

        # 2. Explicit self-declared opt-out (L1 replacement for the dropped
        # env-shape exemption). Default with nothing set is ENFORCED. Checked
        # before the tool is even inspected -- whole-process opt-out.
        if os.environ.get("LOG_ENFORCER_EXEMPT") == "1":
            return 0

        tool_name = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        # 3. Only the gated tool set is ever a candidate for denial.
        if tool_name not in GATED_TOOLS:
            return 0

        # [FIX ROUND 2 -- B1, reopened] A spawn call (Task/Agent) is checked
        # BEFORE the H1 worker exemption, unconditionally -- whether or not
        # this payload carries agent_id/agent_type. This is what actually
        # closes the nested-spawn hole: a worker's own Task/Agent call DOES
        # carry its agent_id (the re-gate's live rig proved this), so if H1
        # ran first here, any already-running worker could chain-spawn
        # forever while the top-level session stayed stale. Only non-spawn
        # tools reach the H1 exemption below. See module docstring.
        if tool_name not in ("Task", "Agent"):
            # H1 (blocker-class): a worker's OWN non-spawn tool call is
            # always allowed. Checked from the payload itself, no env
            # inheritance needed.
            if payload.get("agent_id") or payload.get("agent_type"):
                return 0

        session_id_raw = str(payload.get("session_id") or "unknown")
        session_id = validate_session_id(session_id_raw)
        transcript_path = str(payload.get("transcript_path") or "")

        # 4. L6 structural allow-list -- the exact /log sequence is never
        # gated, so the block can always be cleared. MEMORY.md /
        # conversations_index.md stay an unconditional allow (not log
        # files, no footer convention). A Write/Edit/MultiEdit into the
        # conversations dir itself now goes through the L-0920 scope-add
        # (c) footer check instead of an unconditional allow -- see
        # _convos_dir_footer_check(): this runs REGARDLESS of staleness
        # (even a fresh session is refused a footerless entry), separate
        # from the staleness gate below.
        if tool_name in ("Write", "Edit", "MultiEdit"):
            if _tool_matches_write_allowlist(tool_input):
                return 0
            verdict = _convos_dir_footer_check(session_id, tool_name, tool_input)
            if verdict == "allow":
                return 0
            if verdict == "deny":
                fp = str(tool_input.get("file_path") or "")
                print(build_footer_deny_message(session_id, fp), file=sys.stderr)
                return 2
            # "skip" -- not a convos-dir .md write (or can't be judged) --
            # falls through to the staleness gate below, same as before.
        if tool_name == "Bash" and _bash_matches_allowlist(tool_input):
            return 0

        real_log = find_real_log(session_id, convos_dir())
        now = datetime.now(timezone.utc)
        if real_log:
            deadline = datetime.fromtimestamp(os.stat(real_log).st_mtime, tz=timezone.utc)
        else:
            deadline = session_start_time(transcript_path)

        threshold_min = stale_minutes_threshold()
        elapsed = now - deadline
        stale = elapsed > timedelta(minutes=threshold_min)

        # 5. Deadline not yet due -- allow. Structurally exempts every
        # session under the threshold, with or without a log.
        if not stale:
            return 0

        stale_minutes = int(elapsed.total_seconds() // 60)
        msg = build_deny_message(session_id, real_log, deadline, stale_minutes, now)
        print(msg, file=sys.stderr)
        return 2
    except SystemExit:
        raise
    except Exception:
        alarm("error during evaluation — failing open (allow)")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
