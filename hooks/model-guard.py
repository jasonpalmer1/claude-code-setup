#!/usr/bin/env python3
"""PreToolUse guard: makes delegation rule #1 deterministic instead of advisory
(see CLAUDE.md.template's "Model efficiency" section — "every subagent call
states its model explicitly").

Blocks any Agent/Task spawn that doesn't pass an explicit model. The thing
being prevented is INHERITANCE, not any particular model: an unspecified
spawn silently takes whatever tier the main loop happens to be on, which is
easy not to notice until several subagents have quietly run at your most
expensive tier. Deliberately choosing any tier is fine — including one pricier
than the main loop, when a sub-task genuinely wants it. Accidentally choosing
one is not, and that's the only thing this hook cares about.

Also warns (non-blocking) when a batch/workflow script spawns agents with no
model option — same inheritance bug, one layer down, and harder to see because
it's buried in a script instead of a single tool call.

Exit 2 = block (stderr goes back to Claude). Must never block on its OWN failure."""
import json, sys, os, datetime, re

LOG = os.path.expanduser("~/.claude/hub/model-guard.log")

def log(line):
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOG, "a") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        pass

try:
    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") or {}

    # Some harnesses support a scripted batch-spawn tool (here called
    # "Workflow") that programmatically calls an agent()-style function
    # several times in one script. If yours does, adjust this tool name.
    if tool == "Workflow":
        # Advisory only: a regex can't reliably tell which agent() calls in an
        # arbitrary script omit a model option, and a false block kills a
        # whole run over a guess.
        script = tin.get("script") or ""
        if script:
            calls = len(re.findall(r"\bagent\s*\(", script))
            models = len(re.findall(r"\bmodel\s*:", script))
            if calls and models < calls:
                log(f"WARN workflow {calls} agent() calls / {models} model: opts")
                print(
                    f"Advisory (delegation rule #1): this workflow has {calls} agent() "
                    f"call(s) but only {models} explicit model: option(s). Every agent() "
                    "without an explicit model inherits the MAIN-LOOP tier — that's how a "
                    "whole batch of subagents can silently run at your top tier. Set the "
                    "model per stage (any tier, deliberately chosen) unless inheritance is "
                    "what you actually want here.",
                    file=sys.stderr,
                )
        sys.exit(0)

    if not tin.get("model"):
        log(f"BLOCKED {str(tin.get('description', '?'))[:80]}")
        print(
            "Blocked (delegation rule #1): every subagent call states its model "
            "explicitly. Pick by task SHAPE, not by price rank — a cheap tier for "
            "read-shaped work, a mid tier for code-shaped work, and any tier (including "
            "one above the main loop) when that sub-task genuinely needs it. What's "
            "banned is leaving it unset, which silently inherits the main-loop tier. "
            "Re-issue this call with a model parameter.",
            file=sys.stderr,
        )
        sys.exit(2)
except SystemExit:
    raise
except Exception:
    pass
sys.exit(0)
