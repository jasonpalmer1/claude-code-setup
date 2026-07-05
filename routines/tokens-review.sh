#!/bin/zsh
# Friday spend review: /tokens via headless Claude (cheap tier — pure ledger read), report + notification.
# Scheduled by a launchd plist (see routines/README.md) — Friday afternoon by default.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
OUT_DIR="<REPORTS_DIR>"   # e.g. $HOME/projects/cockpit-reports
OUT="$OUT_DIR/$(date +%F)-tokens.md"
mkdir -p "$OUT_DIR"

claude -p "/tokens" --model haiku --allowedTools "Read" \
  > "$OUT" 2>"$OUT_DIR/$(date +%F)-tokens.err"

if [ -s "$OUT" ]; then
  osascript -e "display notification \"Report: $OUT\" with title \"Weekly token review ready\""
else
  osascript -e "display notification \"Run failed — check .err file in reports dir\" with title \"Token review FAILED\""
fi
