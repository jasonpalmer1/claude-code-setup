#!/bin/sh
# SessionEnd hook: auto-summarize the just-ended session via /log.
# Guards:
#  - CLAUDE_AUTOLOG prevents the headless /log run from recursively triggering itself.
#  - Reads transcript_path from the hook stdin payload so the fresh headless
#    session can actually see what happened.
#  - Runs on a cheaper model (summarization is delegable work, not top-tier).

[ -n "$CLAUDE_AUTOLOG" ] && exit 0

input=$(cat)
tp=$(printf '%s' "$input" | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('transcript_path',''))
except Exception: print('')" 2>/dev/null)

[ -z "$tp" ] && exit 0
[ -f "$tp" ] || exit 0

# Token ledger first — pure parsing, free, no model call.
python3 "$HOME/.claude/hooks/token-ledger.py" "$tp" >/dev/null 2>&1 || true

# Then the summary on a cheaper model (delegable work).
CLAUDE_AUTOLOG=1 claude -p "/log Read the just-ended session transcript at $tp and write its summary." \
  --model sonnet --permission-mode acceptEdits >/dev/null 2>&1 &

exit 0
