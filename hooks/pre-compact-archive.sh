#!/bin/bash
# PreCompact: copy the raw transcript aside before Claude Code compacts it —
# near-free insurance against detail silently lost between /log runs
# (2026-07-22 ecosystem scan; backstops the conversation-log tier).
# 30-day retention keeps growth bounded. Must never fail the compaction.
DIR="$HOME/.claude/hub/compact-archive"
mkdir -p "$DIR" 2>/dev/null
TP=$(jq -r '.transcript_path // empty' 2>/dev/null)
if [ -n "$TP" ] && [ -f "$TP" ]; then
  cp "$TP" "$DIR/$(date +%F-%H%M%S)-$(basename "$TP")" 2>/dev/null
fi
find "$DIR" -name '*.jsonl' -mtime +30 -delete 2>/dev/null
exit 0
