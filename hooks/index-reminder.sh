#!/bin/sh
# PostToolUse hook (Write|Edit): if a file under ~/projects/<name> was edited and that
# project root has no CLAUDE.md, nudge Claude to run /index.
# Exit 2 feeds the message back to Claude as automated context.
# Add more watched roots to the case statement below as needed.

input=$(cat)
fp=$(printf '%s' "$input" | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))
except Exception: print('')" 2>/dev/null)

[ -z "$fp" ] && exit 0

case "$fp" in
  "$HOME/projects/"*)
    rest=${fp#"$HOME"/projects/}
    name=${rest%%/*}
    root="$HOME/projects/$name" ;;
  *)
    exit 0 ;;
esac

if [ -n "$root" ] && [ ! -f "$root/CLAUDE.md" ]; then
  echo "Note: $root has no CLAUDE.md codebase map. Consider running '/index $root' so future sessions don't have to re-explore it." >&2
  exit 2
fi
exit 0
