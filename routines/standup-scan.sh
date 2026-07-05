#!/bin/zsh
# Read-only fleet scan for /standup — fixed script so headless routines can allowlist it by path.
# Prints: epoch<TAB>name<TAB>branch<TAB>dirty-count<TAB>behind/ahead<TAB>age  (sorted, newest first)
for d in "$HOME"/projects/*/; do
  [ -d "$d/.git" ] || continue
  name=$(basename "$d")
  br=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
  dirty=$(git -C "$d" status --porcelain | wc -l | tr -d ' ')
  ab=$(git -C "$d" rev-list --left-right --count @{u}...HEAD 2>/dev/null | tr '\t' '/')
  age=$(git -C "$d" log -1 --format=%cr 2>/dev/null)
  ts=$(git -C "$d" log -1 --format=%ct 2>/dev/null)
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$name" "$br" "$dirty" "${ab:-no-upstream}" "$age"
done | sort -rn
