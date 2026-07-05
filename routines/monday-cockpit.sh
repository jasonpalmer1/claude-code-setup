#!/bin/zsh
# Monday cockpit: /standup + /pulse via headless Claude, report to a file + notification.
# Scheduled by a launchd plist (see routines/README.md) — Monday morning by default.
# Tool grant is deliberately narrow: fixed read-only scan script, git/node prefixes,
# and writes ONLY to the pulse log — no arbitrary shell, no unrestricted writes.
export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
OUT_DIR="<REPORTS_DIR>"   # e.g. $HOME/projects/cockpit-reports
PULSE_LOG="<PULSE_LOG_PATH>"   # e.g. $HOME/projects/pulse-log.md
OUT="$OUT_DIR/$(date +%F)-monday-cockpit.md"
mkdir -p "$OUT_DIR"

claude -p "Produce a Monday-cockpit report. (1) STANDUP: run the pre-approved scan script ~/.claude/routines/standup-scan.sh and format its output per the /standup command's table + next-action rules (read ~/.claude/commands/standup.md; it is READ-ONLY — never modify any repo). (2) PULSE: follow ~/.claude/commands/pulse.md — read your analytics reference for script location/tokens, run it with node, compare to $PULSE_LOG and append today's reading there (the ONLY file you may write). Output one combined markdown report: standup table, traffic pulse, then a 3-bullet 'what deserves attention this week'. If a step fails, say exactly what failed and continue." \
  --model sonnet \
  --allowedTools "Bash($HOME/.claude/routines/standup-scan.sh*),Bash(bash $HOME/.claude/routines/standup-scan.sh*),Bash(zsh $HOME/.claude/routines/standup-scan.sh*),Bash(git:*),Bash(node:*),Read,Glob,Grep,Write($PULSE_LOG),Edit($PULSE_LOG)" \
  > "$OUT" 2>"$OUT_DIR/$(date +%F)-monday-cockpit.err"

if [ -s "$OUT" ]; then
  osascript -e "display notification \"Report: $OUT\" with title \"Monday cockpit ready\""
else
  osascript -e "display notification \"Run failed — check .err file in reports dir\" with title \"Monday cockpit FAILED\""
fi
