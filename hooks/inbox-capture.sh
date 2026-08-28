#!/bin/bash
# UserPromptSubmit hook — ambient raw capture of every prompt you type, so
# nothing is silently lost if a session forgets to write it to real memory.
#
# Appends one line per prompt: `YYYY-MM-DD HH:MM:SS · cwd · prompt text`
# (newlines flattened) to a local, gitignored log. This is a FALLBACK feed,
# not a substitute for real memory files — it's what a periodic extraction
# sweep (see the "Memory autopilot" section in commands/hub.md, if you're
# using that pattern) can diff against to catch anything that got said but
# never turned into a durable memory entry.
#
# Local-only by construction: point LOG at a path under a directory your
# config repo's .gitignore already excludes, so nothing here ever leaves the
# machine. Scrubs key-shaped tokens at capture time and caps each entry at
# 2000 chars — this is meant to be an index of what you asked, not an archive
# of pasted payloads (a large agent report pasted back into a prompt would
# otherwise bloat this file unbounded).
LOG="$HOME/.claude/hub/inbox.log"
TS=$(date '+%Y-%m-%d %H:%M:%S')
jq -r --arg ts "$TS" '"\($ts) · \(.cwd // "?") · \(.prompt // empty | gsub("\n"; " ⏎ ") | .[0:2000])"' 2>/dev/null \
  | sed -E 's/(sk-ant-|sk-|re_|ghp_|gho_|xox[bp]-|AKIA)[A-Za-z0-9_-]{16,}/\1[SCRUBBED]/g' \
  >> "$LOG" 2>/dev/null || true
