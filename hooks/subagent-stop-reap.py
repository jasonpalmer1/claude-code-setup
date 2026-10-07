#!/usr/bin/env python3
"""SubagentStop hook: Layer A's second trigger (plan section 9, correction C2).

`SubagentStop` is already a live event on this machine (report-gate.py).
Subagents are the heaviest helper-spawners here -- playtester runs the
headless-browser harness, and the plan's own section 1 cites a bug-gate
subagent's leftover `tsx /tmp/regate-watchdog-check.mts` as the exact case a
freehand grep missed. Without this hook, a subagent's helpers wait up to a
full 15-minute Layer B sweep; this catches them immediately.

THE CONFLICT C2 NAMES, AND THE RESOLUTION:
Layer A (session-end-reap.py) scopes by `session_id`. Layer B
(orphan-reaper.sh) scopes by `owner_claude_pid` liveness. For a
subagent-spawned helper these two can disagree: the subagent's OWN claude
process is dead (SubagentStop already fired) but the PARENT session's
session_id is still very much alive.

Traced live on this machine while building this (real ancestry, not a
synthetic example): a Bash tool call made BY a subagent shows
    <ephemeral bash-tool shell> -> <subagent's own `claude --print --sdk-url
    ...` process> -> <parent session's `claude rc` process> -> ...
`session_id` in every hook payload for this whole tree (PostToolUse fired
during the subagent's own tool calls, AND this SubagentStop event) is the
PARENT session's id -- report-gate.py's own doc confirms subagent transcripts
live at `.../{session_id}/subagents/agent-{agent_id}.jsonl`, i.e. nested
under the TOP-LEVEL session_id, not a session_id of their own.

That makes `session_id` the WRONG scoping key here: filtering by session_id
would reap helpers the main loop itself registered, or helpers a sibling
subagent still running concurrently registered -- both still very much
wanted alive. The correct, narrow key is `agent_id`, which:
  - IS present and exact on the SubagentStop payload itself (report-gate.py's
    verified field list: ...agent_id, agent_type, last_assistant_message).
  - CAN be recovered at registration time without inventing a new, unverified
    field: process-register.py extracts it from `transcript_path`, whose
    `.../subagents/agent-{agent_id}.jsonl` shape is the same one report-gate.py
    already relies on in production, on both PostToolUse (context-firewall.py
    proves transcript_path is present there) and SubagentStop.

Decision: SessionEnd scopes by session_id (correct there -- the whole tree is
legitimately ending, so every helper under it, however registered, should
go). SubagentStop scopes by agent_id (correct here -- only THIS subagent's
own helpers should go; the main loop and any sibling subagent keep running
untouched). Both reuse the exact same kill pipeline from
session-end-reap.py, unchanged, per C2's instruction -- imported below, not
copy-pasted, so a future correctness fix lands in one place for both.
Tested: see the report's "C2 case" section -- synthetic registry entries with
distinct agent_id values, confirms only the matching agent_id's entries die.

Fails OPEN: bare try/except, bounded runtime, always exits 0 -- same shape as
session-end-reap.py and context-firewall.py.
"""
import datetime
import importlib.util
import json
import os
import re
import signal
import sys

HOME = os.path.expanduser("~")
ERR_LOG = f"{HOME}/.claude/hub/hook-errors.log"

_spec = importlib.util.spec_from_file_location(
    "session_end_reap", f"{HOME}/.claude/hooks/session-end-reap.py"
)
_ser = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ser)  # gives us load_registry, save_registry,
                                  # reap_matching_entries, log_path_for_today, say


def _err(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(ERR_LOG), exist_ok=True)
        with open(ERR_LOG, "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                     f"subagent-stop-reap: {msg}\n")
    except Exception:
        pass


def main() -> int:
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(30)
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return 0
        data = json.loads(raw)
        agent_id = data.get("agent_id")
        if not agent_id:
            # Fail toward doing nothing: no agent_id means we cannot narrowly
            # scope, and scoping by session_id here would be the exact
            # over-reap this hook exists to avoid (see module docstring).
            return 0

        registry = _ser.load_registry()
        matching = [e for e in registry if e.get("agent_id") == agent_id]
        if not matching:
            return 0

        log_path = _ser.log_path_for_today()
        _ser.say(log_path, f"SUBAGENT-STOP: agent_id={agent_id} "
                             f"{len(matching)} registry entr(y/ies) in scope")
        result = _ser.reap_matching_entries(matching, log_path, dry_run=False,
                                              source="SUBAGENT-STOP")

        if result.get("to_drop"):
            drop_ids = {(e.get("pid"), e.get("registered_at")) for e in result["to_drop"]}
            remaining = [e for e in registry
                         if (e.get("pid"), e.get("registered_at")) not in drop_ids]
            _ser.save_registry(remaining)

        return 0
    except Exception as e:
        _err(f"internal failure, failing open: {e}")
        return 0
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
