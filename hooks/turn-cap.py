#!/usr/bin/env python3
"""Worker turn cap (L-1559 item 3a). Wired ONLY through agents/builder.md frontmatter hooks
(PreToolUse + PostToolUse), so no settings.json edit is needed.

Behavior (per agent_id; tool calls >= turns, so it fires slightly early, which is fine):
  PreToolUse : count the call. At >= CAP_BLOCK (115) exit 2 on everything except Write/Edit
               under ~/.claude/hub/reports/ (so the builder can still write handoff.md + report).
               Prints NOTHING on the pass path (0 model tokens per turn).
  PostToolUse: at >= CAP_NUDGE (90), once per agent, emit additionalContext "write handoff.md now".
Exempt: agent_type bug-gate/playtester/planner/search-demand, and any event with no agent_id
(main loop). Fails OPEN (exit 0) on any internal error.

State: ${TURN_CAP_STATE_DIR:-~/.claude/hub/state/turns}/<agent_id> is an append-only file, one
byte per PreToolUse call, opened O_APPEND. The count is the file size (fstat), so parallel tool
calls and two agents at once cannot lose an increment (no read-modify-write, no lock).
<agent_id>.nudged is created O_EXCL so the nudge fires once. Files older than 24h are pruned
(checked at most once an hour via the .pruned marker's mtime).

UNCONFIRMED: whether the harness honours PostToolUse hookSpecificOutput.additionalContext for
a subagent. To verify: run a builder brief of ~100 `ls` calls, then inspect the subagent
transcript (~/.claude/projects/*/<session>/subagents/agent-<id>.jsonl) for the text
"TURN CAP NUDGE" after call 90. Present = honoured. The PreToolUse exit-2 block at 115 is the
guaranteed path and does not depend on the nudge.
"""
import json, os, sys, time

CAP_NUDGE = 90
CAP_BLOCK = 115
EXEMPT = {"bug-gate", "playtester", "planner", "search-demand"}
HOME = os.path.expanduser("~")
REPORTS = os.path.join(HOME, ".claude", "hub", "reports") + os.sep
BLOCK_MSG = ("turn cap reached: write handoff.md (done / next / files / commands / open questions) "
             "and your report, then stop")


def state_dir():
    return os.environ.get("TURN_CAP_STATE_DIR") or os.path.join(HOME, ".claude", "hub", "state", "turns")


def prune(d):
    marker = os.path.join(d, ".pruned")
    now = time.time()
    try:
        if now - os.stat(marker).st_mtime < 3600:
            return
    except OSError:
        pass
    open(marker, "a").close()
    os.utime(marker, None)
    for n in os.listdir(d):
        p = os.path.join(d, n)
        try:
            if n != ".pruned" and now - os.stat(p).st_mtime > 86400:
                os.unlink(p)
        except OSError:
            pass


def allowed_write(tool, ti):
    if tool not in ("Write", "Edit"):
        return False
    fp = (ti or {}).get("file_path") or ""
    fp = os.path.realpath(os.path.expanduser(fp)) if fp else ""
    return fp.startswith(os.path.realpath(REPORTS) + os.sep)


def main():
    ev = json.load(sys.stdin)
    aid = ev.get("agent_id")
    if not aid or ev.get("agent_type") in EXEMPT:
        return 0
    aid = "".join(c for c in str(aid) if c.isalnum() or c in "-_")[:80]
    if not aid:
        return 0
    d = state_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, aid)
    phase = ev.get("hook_event_name")
    if phase == "PreToolUse":
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, b".")
            n = os.fstat(fd).st_size
        finally:
            os.close(fd)
        try:
            prune(d)
        except Exception:
            pass
        if n >= CAP_BLOCK and not allowed_write(ev.get("tool_name"), ev.get("tool_input")):
            sys.stderr.write(BLOCK_MSG + "\n")
            return 2
    elif phase == "PostToolUse":
        n = os.stat(path).st_size
        if n >= CAP_NUDGE:
            try:
                os.close(os.open(path + ".nudged", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
            except FileExistsError:
                return 0
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                  "additionalContext": "TURN CAP NUDGE: ~%d tool calls used; write handoff.md now "
                  "(done / next / files / commands / open questions), then finish." % n}}))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        rc = 0  # fail open
    sys.exit(rc)
