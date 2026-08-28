#!/bin/bash
# PreCompact hook: copy the raw transcript aside before Claude Code compacts
# it — cheap insurance against detail silently lost between log/checkpoint
# runs. Compaction summarizes; a summary can drop something you'd have wanted
# later, and there's no undo once the original is gone. This backstops the
# conversation-log tier, not a replacement for actually logging.
#
# 30-day retention keeps growth bounded. Must never fail the compaction.
DIR="$HOME/.claude/hub/compact-archive"
mkdir -p "$DIR" 2>/dev/null
TP=$(jq -r '.transcript_path // empty' 2>/dev/null)
if [ -n "$TP" ] && [ -f "$TP" ]; then
  cp "$TP" "$DIR/$(date +%F-%H%M%S)-$(basename "$TP")" 2>/dev/null
fi
find "$DIR" -name '*.jsonl' -mtime +30 -delete 2>/dev/null
exit 0
