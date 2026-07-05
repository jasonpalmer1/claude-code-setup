#!/usr/bin/env python3
"""PostToolUse hook (Read|Bash): nudges the MAIN top-tier-model loop to delegate bulk reading.

Enforces a context-firewall rule mechanically: rules in prose don't execute,
hooks do. Fires only in the main session (subagent transcripts live under
.../<session-id>/subagents/ and are skipped — subagents SHOULD read directly).
Nudges on (a) any single huge tool result, (b) cumulative direct-read counts.
"""
import json
import re
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if "/subagents/" in d.get("transcript_path", ""):
    sys.exit(0)  # main loop only

tool = d.get("tool_name", "")
inp = d.get("tool_input") or {}

counts = False
if tool == "Read":
    counts = True
elif tool == "Bash":
    cmd = (inp.get("command") or "").lstrip()
    if re.match(r"(cat|head|tail|grep|rg|find|awk|jq)\b", cmd):
        counts = True
if not counts:
    sys.exit(0)

sid = d.get("session_id", "nosession")
state = f"/tmp/claude-firewall-{sid}"
try:
    with open(state) as f:
        n = int(f.read().strip() or 0)
except Exception:
    n = 0
n += 1
try:
    with open(state, "w") as f:
        f.write(str(n))
except Exception:
    pass

try:
    rlen = len(json.dumps(d.get("tool_response", "")))
except Exception:
    rlen = 0

msg = None
if rlen > 60000:
    msg = (f"Context firewall: that result put ~{rlen // 1000}KB into main context, "
           "re-billed on every later turn. For bulk reading/extraction, delegate to a "
           "cheap-tier subagent that returns a distilled conclusion.")
elif n in (10, 25, 50):
    msg = (f"Context firewall: {n} direct read-type calls in main context this session. "
           "Fine if these were targeted edits/lookups; if you are exploring or extracting, "
           "route the rest through a subagent.")

if msg:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}}))
