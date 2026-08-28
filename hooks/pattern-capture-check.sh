#!/bin/sh
# SessionEnd hook — measures adherence to a "pattern journal" (see the Memory
# autopilot section in commands/hub.md, if you're using that pattern): a
# dated, accumulating log of recurring behavioral/strategic patterns you've
# noticed, distinct from a static one-time summary of how you work.
#
# Writes one line per day to memory/pattern_adherence.log: HIT if the journal
# gained a dated entry today, MISS otherwise. A later HIT the same day upgrades
# that day's MISS — recovery counts. This is measurement only, not nagging —
# pair it with a SessionStart hook (or a check in your own dashboard) that
# reads this log and surfaces a nudge only when it's been stale for more than
# a day or two, so the system observes constantly but only speaks up when
# something's actually off.
#
# Customize MEM to your own memory directory (see <MEMORY_DIR> in
# CLAUDE.md.template).
MEM="$HOME/.claude/projects/<your-id>/memory"
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
