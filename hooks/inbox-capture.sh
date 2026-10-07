#!/bin/bash
# Ambient memory capture (hub inbox). Appends one line per user prompt:
#   YYYY-MM-DD HH:MM:SS · cwd · prompt text (newlines flattened to ⏎)
# Local-only: ~/.claude/hub/ is gitignored in claude-config; nothing leaves the machine.
# The hub's extraction sweep + monthly reflection distill this raw feed into real memories.
# 2026-07-22 hardening (system audit): scrub key-shaped tokens at capture time and cap
# each entry at 2000 chars — this is an index of what the operator asked, not an archive of
# pasted payloads; giant agent report-backs were bloating the log unbounded.
# L-1576 (2026-10-01): entries cut 2000 -> 300 chars (short summary per prompt) and the file is
# capped at ~2MB (oldest lines dropped to the newest 4000) so it can never grow unbounded again.
LOGF=~/.claude/hub/inbox.log
if [ -f "$LOGF" ] && [ "$(stat -f%z "$LOGF" 2>/dev/null || echo 0)" -gt 2097152 ]; then
  tail -n 4000 "$LOGF" > "$LOGF.tmp" 2>/dev/null && mv "$LOGF.tmp" "$LOGF" 2>/dev/null
fi
TS=$(date '+%Y-%m-%d %H:%M:%S')
jq -r --arg ts "$TS" '"\($ts) · \(.cwd // "?") · \(.prompt // empty | gsub("\n"; " ⏎ ") | .[0:300])"' 2>/dev/null \
  | sed -E 's/(sk-ant-|sk-|re_|ghp_|gho_|xox[bp]-|AKIA)[A-Za-z0-9_-]{16,}/\1[SCRUBBED]/g' \
  >> ~/.claude/hub/inbox.log 2>/dev/null || true
