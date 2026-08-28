#!/bin/bash
# PostToolUse hook — debounced background rebuild of the memory graph (see
# memory-activate.py for what the graph is and why it exists).
#
# Without this, the only rebuild trigger might be a weekly scheduled job, so a
# newly written memory file would be unretrievable by the graph for up to a
# week. This makes any edit to a memory .md file (outside graph/, which the
# graph itself lives in) kick a rebuild almost immediately, without making the
# edit itself wait on it.
#
# Reads the PostToolUse JSON payload on stdin, checks tool_input.file_path, and
# if it's under the memory dir but not under memory/graph/, runs build.py then
# validate.py in the background and logs one line. Debounced: skips if
# graph.json was rebuilt in the last 90s (a burst of edits should not spawn a
# rebuild per edit).
#
# This assumes you maintain graph/build.py and graph/validate.py yourself (see
# memory-activate.py's docstring) — this script is the trigger, not the engine.
#
# MUST fail open: never blocks, never exits nonzero, never makes noise on
# stdout/stderr that could be mistaken for tool output. Wire in settings.json
# (this script does not register itself):
#   {"hooks": {"PostToolUse": [{"hooks": [
#      {"type": "command", "command": "bash ~/.claude/hooks/memory-graph-refresh.sh"}]}]}}

# Customize: point this at your own memory directory, e.g. the layout from
# CLAUDE.md.template's <MEMORY_DIR>.
MEMORY_DIR="$HOME/.claude/projects/<your-id>/memory"
GRAPH_DIR="$MEMORY_DIR/graph"
LOG="$HOME/.claude/hub/memory-health.log"
DEBOUNCE_SECS=90

payload="$(cat 2>/dev/null)"
[ -z "$payload" ] && exit 0

file_path="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    fp = (d.get("tool_input") or {}).get("file_path") or ""
    print(fp)
except Exception:
    print("")
' 2>/dev/null)"

[ -z "$file_path" ] && exit 0

case "$file_path" in
    "$MEMORY_DIR"/*) ;;
    *) exit 0 ;;
esac

case "$file_path" in
    "$GRAPH_DIR"/*) exit 0 ;;
esac

if [ -f "$GRAPH_DIR/graph.json" ]; then
    now_ts=$(date +%s 2>/dev/null)
    mtime=$(stat -f %m "$GRAPH_DIR/graph.json" 2>/dev/null || stat -c %Y "$GRAPH_DIR/graph.json" 2>/dev/null)
    if [ -n "$now_ts" ] && [ -n "$mtime" ]; then
        age=$(( now_ts - mtime ))
        if [ "$age" -lt "$DEBOUNCE_SECS" ] 2>/dev/null; then
            exit 0
        fi
    fi
fi

mkdir -p "$(dirname "$LOG")" 2>/dev/null

nohup bash -c '
    cd "'"$MEMORY_DIR"'" 2>/dev/null || exit 0
    build_out=$(python3 graph/build.py --quiet 2>&1)
    build_rc=$?
    val_out=$(python3 graph/validate.py 2>&1)
    val_rc=$?
    hard=$(printf "%s\n" "$val_out" | grep -c "^  FAIL")
    ts=$(date "+%Y-%m-%dT%H:%M:%S%z" 2>/dev/null)
    status="ok"
    [ "$build_rc" -ne 0 ] && status="build-failed"
    if [ "$val_rc" -ne 0 ] && [ "$status" = "ok" ]; then status="validate-failed"; fi
    echo "$ts refresh trigger=\"'"$file_path"'\" status=$status hard_failures=$hard" >> "'"$LOG"'"
' >/dev/null 2>&1 &
disown 2>/dev/null

exit 0
