#!/usr/bin/env python3
"""PostToolUse(Bash) hook: registers spawned helpers. That is ALL it does --
no kills, no verdicts. It is the positive-attribution half of the orphan
reaper (plan section 2.3), the thing that makes "owner gone" checkable at all
later instead of guessing from ppid==1 alone (section 2.2 explains why
ppid==1 alone is not sufficient: it can't tell a Claude-spawned leak from
the operator's own long-running `nohup`'d process, e.g. the 8-day `python -m
sous.relay` seen live on this machine, and it can't tell a genuine new orphan
from a process THIS reaper just killed a moment ago and is still settling).

WHEN IT FIRES: only when this Bash call looks like it backgrounded
something -- either shell syntax (`&`, `nohup`, `disown`) or the Bash tool's
own structured `run_in_background: true` flag (plan section 2.3: "the
brief's examples are manually-backgrounded shell syntax, but the Bash tool's
built-in flag produces the identical OS-level shape and must be covered
too"). On a trigger, it takes ONE ps snapshot and registers any NEW,
small-etime process whose command matches the allow-list
(orphan-reaper-patterns.json) and isn't already registered.

SECTION 9 CORRECTION C3 -- hardening `owner_claude_pid`:
`os.getppid()` alone is *probably* right (traced live: a Bash tool shell is a
direct child of the session process) but it silently assumes the harness
never inserts a wrapper shell between the hook and the session -- and
settings.json already invokes some hooks via `bash ~/.claude/hooks/...`.
Fixed here by walking UP the ppid chain to the first ancestor whose COMMAND
is actually the claude binary, not merely "lives under ~/.claude" (a naive
substring check on ".claude" would false-positive on every hook script's own
path, e.g. this very file's own ancestry runs from inside ~/.claude/hooks/ --
that is exactly the false-positive this hardening exists to avoid). The
match is: the first whitespace-delimited token of the command line has a
basename of exactly "claude", OR its path contains "/claude/versions/" (the
real installed-binary directory; verified live in this build:
`$HOME/.local/share/claude/versions/2.1.271 --print --sdk-url
...`). If the walk finds no claude ancestor within a bounded number of hops,
`owner_claude_pid` is registered as null and Layer B treats that entry as
unregistered -- fail toward NOT killing, per C3's explicit instruction.

Also records `agent_id` (extracted from `transcript_path`'s
`.../subagents/agent-{agent_id}.jsonl` shape, same convention report-gate.py
already relies on) so subagent-stop-reap.py (section 9 correction C2) can
scope narrowly to one subagent's own helpers without touching the main
loop's or a sibling subagent's. Null when the call came from the main loop.

L-0365 ROUND 2 FIX (2026-09-28, bug-gate blocker B2): the round-1 candidate-
ancestry walk above is structurally blind to the single most common real
case -- a process backgrounded with plain shell `&` syntax. By the time this
hook fires (PostToolUse runs after the triggering Bash call returns), the
wrapper shell that ran that command has already exited, and the OS has
already reparented the backgrounded child to pid 1 (confirmed live on this
machine, within seconds -- same finding `hub/bin/dev-server-reaper`'s
docstring already records for L-1395). `find_claude_ancestor` on such a
candidate hits `parent <= 1` on the FIRST hop and returns None, so the
ancestry check alone would silently register almost nothing for this,
the primary real-world case.

Fix: `CLAUDE_CODE_SESSION_ID` is set in the Bash tool's own wrapper-shell
environment (confirmed live: matches this hook's own `session_id` field
exactly) and, unlike ppid, environment variables are copied at fork time
and are NEVER altered by reparenting -- a backgrounded child keeps its
spawning session's id in its own environ forever, even once its ppid
becomes 1. `read_candidate_session_id()` reads exactly that one named,
non-secret variable from the candidate's real environment and discards
the rest immediately -- see its own docstring and L-0952 (reading another
process's env can expose secrets in general; this never logs or returns
the raw env blob, only one named variable's value, used only for an
equality check).

L-0365 ROUND 3 FIX (2026-09-28, bug-gate blocker C2): round 2's
`read_candidate_session_id()` parsed `ps eww`'s TEXT output, which
concatenates argv and env with no reliable separator -- a candidate could
spoof attribution just by putting the literal string
"CLAUDE_CODE_SESSION_ID=<x>" somewhere in its own command-line arguments.
Fixed by reading the candidate's environment directly via macOS's
KERN_PROCARGS2 sysctl (`_proc_env_strings()`), whose buffer layout starts
with a 4-byte argc that marks exactly where argv ends and env begins --
argv is never consulted at all, closing the spoof. New hard rule as of
this round (fleet-wide, not just this file): never print any process env
to a terminal or log, ever -- filter to the one named variable in memory
and never surface the raw blob, including while debugging.

A candidate is now attributed to this session if EITHER the ancestry walk
lands on the same claude pid (round-1 path, still what correctly SKIPS the
nested-claude case: a nested session gets its own fresh
CLAUDE_CODE_SESSION_ID, so it fails the env check too, not just ancestry)
OR its own CLAUDE_CODE_SESSION_ID matches this invocation's session_id
(new path, catches the reparented-background-child case ancestry cannot).
Both still fail toward NOT registering: a candidate matching neither is
registered to nobody, same as before.

L-0365 ROUND 1 FIX (2026-09-28, ~/.claude/hub/reports/L-0365/plan.md section 3a):
the registration loop below used to stamp EVERY newly-observed, allow-list-
matching process in the machine-wide snapshot with the CALLING hook's own
`owner_pid`/`session_id`, with no check that the candidate actually descends
from that session. Live-tested and confirmed broken 2026-09-15: a sibling
session's own Chrome helpers got misattributed this way because they simply
matched the pattern and were new in the same snapshot. Fixed by walking the
CANDIDATE's own ppid chain (via `find_claude_ancestor`, the same walk
`resolve_owner_claude_pid` does for the hook itself, generalized to take any
start pid) and registering it under this session only if that walk lands on
the exact same claude pid already resolved as this invocation's own
`owner_pid`. A candidate whose ancestry reaches a different session's claude
pid, or is already severed (ppid==1 before any claude ancestor is found), is
registered to NOBODY -- never to the observer -- per the 2026-09-16 hub note.

Fails OPEN: bare try/except at the top, bounded runtime, always exits 0 --
same shape as ~/.claude/hooks/context-firewall.py.
"""
import ctypes
import ctypes.util
import datetime
import importlib.util
import json
import os
import re
import signal
import struct
import subprocess
import sys

HOME = os.path.expanduser("~")
# Overridable via env for test isolation (orphan-reaper.sh supports the same
# two vars) -- production always falls back to the real paths below.
REGISTRY_PATH = os.environ.get("ORPHAN_REGISTRY", f"{HOME}/.claude/hub/state/orphan-registry.jsonl")
PATTERNS_PATH = os.environ.get("ORPHAN_PATTERNS", f"{HOME}/.claude/routines/orphan-reaper-patterns.json")
ERR_LOG = f"{HOME}/.claude/hub/hook-errors.log"
NEW_PROCESS_MAX_AGE_S = 30   # "small etime" -- the hook runs right after the
                              # Bash call returns, so a genuinely new child is
                              # seconds old, not minutes
MAX_ANCESTOR_HOPS = 12

_spec = importlib.util.spec_from_file_location(
    "session_end_reap", f"{HOME}/.claude/hooks/session-end-reap.py"
)
_ser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ser)  # reuse take_snapshot()/etime_to_seconds() -- one
                                  # definition of "how we read the process table"


def _err(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(ERR_LOG), exist_ok=True)
        with open(ERR_LOG, "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                     f"process-register: {msg}\n")
    except Exception:
        pass


def load_patterns():
    try:
        with open(PATTERNS_PATH) as f:
            doc = json.load(f)
        out = []
        for p in doc.get("patterns", []):
            try:
                out.append((re.compile(p["pattern"]), p.get("label", ""),
                             bool(p.get("kill_without_registry", False))))
            except re.error as e:
                _err(f"bad pattern {p!r}: {e}")
        return out
    except Exception as e:
        _err(f"could not load patterns: {e}")
        return []


def is_background_trigger(tool_input: dict) -> bool:
    if tool_input.get("run_in_background") is True:
        return True
    cmd = (tool_input.get("command") or "")
    if re.search(r"(?<!&)&\s*$", cmd.strip()):
        return True
    if re.search(r"\bnohup\b|\bdisown\b", cmd):
        return True
    return False


CLAUDE_ANCESTOR_RE = re.compile(r"/claude/versions/", re.I)


def is_claude_binary_command(command: str) -> bool:
    """Deliberately NOT a substring check on '.claude' -- that would match
    almost every script on this machine (they all live under ~/.claude/...).
    Requires either the first token's basename to be exactly 'claude', or the
    first token's path to contain the real installed-binary directory."""
    if not command:
        return False
    first_token = command.strip().split(None, 1)[0] if command.strip() else ""
    if not first_token:
        return False
    if os.path.basename(first_token) == "claude":
        return True
    if CLAUDE_ANCESTOR_RE.search(first_token):
        return True
    return False


def find_claude_ancestor(start_pid, snapshot: dict):
    """Walk UP from start_pid (inclusive) looking for the first ancestor whose
    command is the claude binary itself. Returns an int pid, or None if no
    such ancestor is found within the hop bound (fail toward null). Shared by
    `resolve_owner_claude_pid` (start_pid = this hook's own ppid) and the
    L-0365 candidate-ancestry check (start_pid = a candidate process's own
    ppid) -- same walk, same bound, applied to two different starting points
    so the two answers are directly comparable."""
    pid = start_pid
    for _ in range(MAX_ANCESTOR_HOPS):
        row = snapshot.get(pid)
        if row is None:
            return None
        if is_claude_binary_command(row["command"]):
            return pid
        parent = row["ppid"]
        if parent == pid or parent <= 1:
            return None
        pid = parent
    return None


CTL_KERN = 1
KERN_PROCARGS2 = 49


def _proc_env_strings(pid: int):
    """L-0365 round 3, blocker C2: return ONLY the environment strings for
    `pid`, never argv, via macOS's KERN_PROCARGS2 sysctl -- the same call
    `ps` itself uses under the hood. The round-2 fix parsed `ps eww`'s
    text output (argv and env concatenated with no reliable separator),
    which a candidate could spoof just by putting the literal string
    "CLAUDE_CODE_SESSION_ID=<x>" somewhere in its OWN COMMAND LINE -- `ps
    eww`'s text has no way to tell that apart from a real env var. The
    KERN_PROCARGS2 buffer instead starts with a 4-byte argc, and layout is
    fixed: argc, then the exec path, then exactly argc NUL-terminated argv
    strings, then the environment strings -- so argc tells us precisely
    where argv ends and env begins, and we only ever return what comes
    after that boundary. Never logs or returns the raw buffer or the argv
    portion; returns None on any failure (process already gone, permission
    denied, not Darwin, malformed buffer) -- fails toward no env, same
    fail-open-to-no-attribution posture as find_claude_ancestor above."""
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
    size = ctypes.c_size_t(0)
    if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value < 4:
        return None
    buf = ctypes.create_string_buffer(size.value)
    if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
        return None
    data = buf.raw[: size.value]
    argc = struct.unpack("<I", data[0:4])[0]
    end = len(data)
    off = data.find(b"\x00", 4)
    if off == -1:
        return None
    while off < end and data[off] == 0:  # NUL padding after the exec path
        off += 1
    for _ in range(argc):  # skip exactly argc argv strings, never read them
        nul = data.find(b"\x00", off)
        if nul == -1:
            return None
        off = nul + 1
    while off < end and data[off] == 0:  # NUL padding before envp
        off += 1
    return [s.decode("utf-8", "replace") for s in data[off:end].split(b"\x00") if s]


def read_candidate_session_id(pid: int):
    """L-0365 round 3, blocker C2: read exactly ONE named env var
    (CLAUDE_CODE_SESSION_ID) from a candidate process's REAL environment
    (see `_proc_env_strings` -- argv is never consulted, so this cannot be
    spoofed by a candidate's own command-line text). Parsed in memory and
    discarded immediately -- never logged, never written to the registry,
    never included in an error message, per the fleet-wide hard rule
    (2026-09-28): never print any process env anywhere, ever. Returns None
    on any failure (process already gone, no such var) -- fails toward NOT
    matching, same fail-open-to-no-attribution posture as
    find_claude_ancestor above."""
    try:
        env_strs = _proc_env_strings(pid)
    except Exception:
        return None
    if not env_strs:
        return None
    for entry in env_strs:
        if entry.startswith("CLAUDE_CODE_SESSION_ID="):
            return entry.split("=", 1)[1]
    return None


def resolve_owner_claude_pid(snapshot: dict):
    """Walk UP from this hook process's own ppid (the process that actually
    invoked us -- normally the Bash tool's ephemeral wrapper shell) looking
    for the first ancestor whose command is the claude binary itself. Returns
    an int pid, or None if no such ancestor is found within the hop bound
    (fail toward null -> unregistered -> Layer B never kills on this alone)."""
    return find_claude_ancestor(os.getppid(), snapshot)


AGENT_ID_RE = re.compile(r"/subagents/agent-([^/]+?)\.jsonl$")


def extract_agent_id(transcript_path: str):
    if not transcript_path:
        return None
    m = AGENT_ID_RE.search(transcript_path)
    return m.group(1) if m else None


def load_registry_pid_lstart_set():
    seen = set()
    try:
        with open(REGISTRY_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    seen.add((e.get("pid"), e.get("lstart")))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return seen


def append_registry(entry: dict) -> None:
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    with open(REGISTRY_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main() -> int:
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(15)
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return 0
        data = json.loads(raw)

        if data.get("tool_name") != "Bash":
            return 0
        tool_input = data.get("tool_input") or {}
        if not is_background_trigger(tool_input):
            return 0

        patterns = load_patterns()
        if not patterns:
            return 0

        snapshot = _ser.take_snapshot()
        if not snapshot:
            return 0

        owner_pid = resolve_owner_claude_pid(snapshot)
        session_id = data.get("session_id")
        agent_id = extract_agent_id(data.get("transcript_path", ""))
        already = load_registry_pid_lstart_set()
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
        now_epoch = datetime.datetime.now().timestamp()

        registered = 0
        for pid, row in snapshot.items():
            age_s = _ser.etime_to_seconds(row["etime"])
            if age_s > NEW_PROCESS_MAX_AGE_S:
                continue
            if (pid, row["lstart"]) in already:
                continue
            for regex, label, kill_without_registry in patterns:
                if kill_without_registry:
                    continue  # C1's exception needs no registry entry at all
                if regex.search(row["command"]):
                    # L-0365 fix: the candidate must itself descend from the
                    # exact claude pid this invocation resolved as its own
                    # caller -- never attribute by pattern-match-plus-recency
                    # alone (2026-09-15 live-test failure, see module docstring).
                    if owner_pid is None:
                        break
                    candidate_owner = find_claude_ancestor(row["ppid"], snapshot)
                    match_method = None
                    if candidate_owner == owner_pid:
                        match_method = "ancestry"
                    elif session_id is not None and read_candidate_session_id(pid) == session_id:
                        # B2: ppid ancestry is already severed (reparented to
                        # pid 1) for a `&`-backgrounded child by the time this
                        # hook fires -- fall back to the env marker, which
                        # survives reparenting.
                        match_method = "env"
                    if match_method is None:
                        break
                    entry = {
                        "pid": pid,
                        "ppid_at_registration": row["ppid"],
                        "lstart": row["lstart"],
                        "command": row["command"],
                        "matched_pattern": label,
                        "session_id": session_id,
                        "agent_id": agent_id,
                        "owner_claude_pid": owner_pid,
                        "owner_match_method": match_method,
                        "registered_at": now_iso,
                        "registered_at_epoch": now_epoch,
                        "keep_until": None,
                    }
                    append_registry(entry)
                    registered += 1
                    break
        return 0
    except Exception as e:
        _err(f"internal failure, failing open: {e}")
        return 0
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
