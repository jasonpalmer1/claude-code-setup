#!/usr/bin/env python3
"""PostToolUse hook (Read|Bash): nudges the MAIN Opus loop to delegate bulk reading.

Enforces the context-firewall rule mechanically: rules in prose don't execute,
hooks do. Fires only in the main session (subagent transcripts live under
.../<session-id>/subagents/ and are skipped — subagents SHOULD read directly).
Nudges on (a) any single large tool result, (b) cumulative bytes read, or
(c) cumulative direct-read counts. Thresholds tuned down 2026-07-07 after the
ledger showed Haiku at $11 lifetime — read-work wasn't being delegated, so the
old 10/25/50 + 60KB gates were too lax to change behavior.
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
        st = json.loads(f.read() or "{}")
    if not isinstance(st, dict):
        st = {}  # migrate old bare-int state files
except Exception:
    st = {}

n = int(st.get("n", 0)) + 1
try:
    rlen = len(json.dumps(d.get("tool_response", "")))
except Exception:
    rlen = 0
total_kb = int(st.get("kb", 0)) + rlen // 1000
kb_fired = bool(st.get("kb_fired", False))

msg = None
if rlen > 40000:
    msg = (f"Context firewall: that result put ~{rlen // 1000}KB into main context, "
           "re-billed on every later turn. For bulk reading/extraction, delegate to a "
           "Haiku subagent that returns a distilled conclusion (feedback_delegation_rule).")
elif not kb_fired and total_kb >= 150:
    kb_fired = True
    msg = (f"Context firewall: ~{total_kb}KB of reads have piled up in main context this "
           "session (re-billed every turn — the slow bleed, not one big read). If you are "
           "still exploring/extracting, hand the rest to a Haiku subagent that returns "
           "conclusions, not file dumps (feedback_delegation_rule).")
elif n in (5, 15, 35):
    msg = (f"Context firewall: {n} direct read-type calls in main context this session. "
           "Fine if these were targeted edits/lookups; if you are exploring or extracting, "
           "route the rest through a Haiku agent (feedback_delegation_rule).")

try:
    with open(state, "w") as f:
        f.write(json.dumps({"n": n, "kb": total_kb, "kb_fired": kb_fired}))
except Exception:
    pass

if msg:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": msg}}))
