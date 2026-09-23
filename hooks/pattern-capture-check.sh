#!/bin/sh
# SessionEnd: measure pattern-journal adherence (feedback_pattern_capture).
# One line per day in memory/pattern_adherence.log: HIT if patterns_observed.md
# gained a dated entry today, MISS otherwise. A later HIT upgrades the day's
# MISS — recovery counts. This is measurement, not nagging: the SessionStart
# stale banner (operator-scorecard.mjs) does the nudging, only when >2d stale.
# Installed 2026-07-19 on the operator's explicit go, after his 2nd audit of the layer.
MEM="$HOME/.claude/projects/$(printf '%s' "$HOME" | sed 's#/#-#g')/memory"
J="$MEM/patterns_observed.md"
LOG="$MEM/pattern_adherence.log"
today=$(date +%Y-%m-%d)
[ -f "$J" ] || exit 0
if grep -q "$today" "$J"; then status=HIT; else status=MISS; fi
touch "$LOG"
grep -q "^$today HIT" "$LOG" && exit 0
if [ "$status" = "HIT" ] && grep -q "^$today MISS" "$LOG"; then
  sed -i '' "s/^$today MISS/$today HIT/" "$LOG"
  exit 0
fi
grep -q "^$today " "$LOG" || echo "$today $status" >> "$LOG"
exit 0
