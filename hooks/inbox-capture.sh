#!/bin/bash
# Ambient memory capture (hub inbox). Appends one line per user prompt:
#   YYYY-MM-DD HH:MM:SS · cwd · prompt text (newlines flattened to ⏎)
# Local-only: ~/.claude/hub/ is gitignored in claude-config; nothing leaves the machine.
# The hub's extraction sweep + monthly reflection distill this raw feed into real memories.
# 2026-07-22 hardening (system audit): scrub key-shaped tokens at capture time and cap
# each entry at 2000 chars — this is an index of what the operator asked, not an archive of
# pasted payloads; giant agent report-backs were bloating the log unbounded.
TS=$(date '+%Y-%m-%d %H:%M:%S')
mkdir -p ~/.claude/hub 2>/dev/null || true
jq -r --arg ts "$TS" '"\($ts) · \(.cwd // "?") · \(.prompt // empty | gsub("\n"; " ⏎ ") | .[0:2000])"' 2>/dev/null \
  | sed -E 's/(sk-ant-|sk-|re_|ghp_|gho_|xox[bp]-|AKIA)[A-Za-z0-9_-]{16,}/\1[SCRUBBED]/g' \
  >> ~/.claude/hub/inbox.log 2>/dev/null || true
