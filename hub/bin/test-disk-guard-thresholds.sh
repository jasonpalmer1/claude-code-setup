#!/bin/zsh
# Self-test for hub/bin/disk-guard's OWN free-space threshold logic (L-0942
# item 1: WARN raised 30Gi -> 40Gi, CRIT unchanged at 15Gi).
#
# Why this is a separate file from hooks/test-disk-guard.sh: despite the
# name, that file is a self-test for hooks/lib/protected_paths.py, not for
# hub/bin/disk-guard's threshold arithmetic (confirmed by reading it --
# its own header says "Self-test for hooks/lib/protected_paths.py").
# hub/bin/disk-guard had no fixture-based threshold test of its own before
# this ticket. Rather than repurpose a file whose established job is
# something else, this is a new, clearly-named file living next to the
# script it tests.
#
# Fixture mechanism: DISK_GUARD_FREE_GI_OVERRIDE (added to disk-guard by
# this same ticket, test-only, documented in disk-guard's own header) lets
# this drive an EXACT free-Gi value through the real script, real threshold
# branches, real exit codes -- without needing a real volume of that exact
# size. DISK_GUARD_LOG is pointed at a throwaway file so this never writes
# to the real ~/.claude/hub/disk-guard.log.
set -u

SCRIPT_DIR="$(cd "$(dirname "${(%):-%x}")" && pwd)"
G="${1:-$SCRIPT_DIR/disk-guard}"
[[ -f "$G" ]] || { print -r -- "no script at $G"; exit 2; }
print -r -- "testing: $G"

TMPLOG=$(mktemp); export DISK_GUARD_BIG_CACHE=$(mktemp)
trap 'rm -f "$TMPLOG"' EXIT

PASS=0
FAIL=0

check() { # $1=label $2=free_gi $3=expected_rc
  local label="$1" free="$2" expected="$3" rc line
  line=$(DISK_GUARD_FREE_GI_OVERRIDE="$free" DISK_GUARD_LOG="$TMPLOG" zsh "$G")
  rc=$?
  if [[ "$rc" == "$expected" ]]; then
    PASS=$((PASS + 1))
    print -r -- "  ok    $label (${free}Gi -> rc=$rc)"
  else
    FAIL=$((FAIL + 1))
    print -r -- "  FAIL  $label (${free}Gi -> rc=$rc, expected $expected) :: $line"
  fi
}

print "CRIT boundary (unchanged, < 15):"
check "14Gi -> CRIT" 14 2
check "0Gi -> CRIT" 0 2
check "15Gi -> WARN (boundary itself is NOT CRIT)" 15 1

print "WARN band, the actual L-0942 change (now 15-39, was 15-29):"
check "20Gi -> WARN" 20 1
check "29Gi -> WARN (already true before this ticket)" 29 1
check "30Gi -> WARN (the OLD boundary -- used to be OK, must now be WARN)" 30 1
check "35Gi -> WARN (plan's named done-test value)" 35 1
check "39Gi -> WARN (one below the new floor)" 39 1

print "OK boundary, the new floor:"
check "40Gi -> OK (the new floor itself)" 40 0
check "41Gi -> OK" 41 0
check "100Gi -> OK" 100 0

print "df failure still fails CRIT-safe (unaffected by the override hook):"
line=$(DISK_GUARD_VOLUME=/no/such/volume/xyz DISK_GUARD_LOG="$TMPLOG" zsh "$G")
rc=$?
if [[ "$rc" == 2 ]]; then
  PASS=$((PASS + 1)); print -r -- "  ok    df failure on a bogus volume -> CRIT (rc=2)"
else
  FAIL=$((FAIL + 1)); print -r -- "  FAIL  df failure on a bogus volume -> rc=$rc, expected 2 :: $line"
fi

print "L-1589 BIG-WT alarm (info only, exit code unchanged):"
_R=$(mktemp -d)
trap 'rm -f "$TMPLOG"; /bin/rm -r "$_R"' EXIT
for n in big small; do
  mkdir -p "$_R/x-wt/$n/.next"; print -r -- "gitdir: /nowhere" > "$_R/x-wt/$n/.git"
done
head -c 3000000 /dev/zero > "$_R/x-wt/big/.next/blob"
head -c 10 /dev/zero > "$_R/x-wt/small/.next/blob"
line=$(DISK_GUARD_BIG_SYNC=1 DISK_GUARD_BIG_CACHE="$_R/cache.txt" DISK_GUARD_WT_ROOT="$_R" DISK_GUARD_BIG_KB=1000 DISK_GUARD_FREE_GI_OVERRIDE=100 DISK_GUARD_LOG="$TMPLOG" zsh "$G"); rc=$?
if [[ "$rc" == 0 && "$line" == *"BIG-WT"*"big("* && "$line" != *"small("* ]]; then
  PASS=$((PASS + 1)); print -r -- "  ok    big orphan worktree named, small one not, rc still 0"
else
  FAIL=$((FAIL + 1)); print -r -- "  FAIL  BIG-WT rc=$rc :: $line"
fi
line=$(DISK_GUARD_BIG_SYNC=1 DISK_GUARD_BIG_CACHE="$_R/cache.txt" DISK_GUARD_WT_ROOT="$_R" DISK_GUARD_BIG_KB=99999999 DISK_GUARD_FREE_GI_OVERRIDE=100 DISK_GUARD_LOG="$TMPLOG" zsh "$G")
if [[ "$line" != *"BIG-WT"* ]]; then PASS=$((PASS + 1)); print -r -- "  ok    under threshold -> no BIG-WT note"
else FAIL=$((FAIL + 1)); print -r -- "  FAIL  unexpected BIG-WT :: $line"; fi

print "DISK_GUARD_CRIT_GI validation (L-1775 R1: bad values fall back to 15, never disable CRIT):"
checkc() { # $1=label $2=crit_env $3=free $4=expected_rc
  local rc line
  line=$(DISK_GUARD_CRIT_GI="$2" DISK_GUARD_FREE_GI_OVERRIDE="$3" DISK_GUARD_LOG="$TMPLOG" zsh "$G"); rc=$?
  if [[ "$rc" == "$4" ]]; then PASS=$((PASS + 1)); print -r -- "  ok    $1 (rc=$rc)"
  else FAIL=$((FAIL + 1)); print -r -- "  FAIL  $1 (rc=$rc, expected $4) :: $line"; fi
}
checkc "20-digit crit at 0Gi -> CRIT" 99999999999999999999 0 2
checkc "crit 0 at 3Gi -> CRIT (fallback 15)" 0 3 2
checkc "crit -1 at 3Gi -> CRIT" -1 3 2
checkc "crit abc at 3Gi -> CRIT" abc 3 2
checkc "crit '6 ' at 8Gi -> CRIT (trailing space rejected)" "6 " 8 2
checkc "crit 06 at 8Gi -> CRIT (leading zero rejected)" 06 8 2
checkc "crit 16 at 3Gi -> CRIT (over cap)" 16 3 2
checkc "crit empty at 3Gi -> CRIT" "" 3 2
checkc "crit 6 at 8Gi -> not CRIT (valid lower floor)" 6 8 1
checkc "crit 6 at 5Gi -> CRIT" 6 5 2

print
print "passed: $PASS   failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
exit $?
