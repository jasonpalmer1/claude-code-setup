#!/usr/bin/env bash
# test-lane-lock.sh — self-test for hub/bin/lane-lock (L-0310). Proves:
#   1. claim/release basic ownership
#   2. a live other-owner claim is REFUSED (exit 2)
#   3. a dead-pid holder self-heals (claim by a new owner succeeds)
#   4. the gate-fail circuit breaker actually TRIPS after threshold FAILs,
#      and a subsequent claim is REFUSED (exit 3) — the core proof this
#      ticket asked for
#   5. one PASS after a trip does NOT silently clear it (manual-reset only)
#   6. an explicit `reset` clears the trip and claim succeeds again
#
# Added 2026-09-27 to close the bug-gate FAIL on this ticket
# (hub/reports/2026-09-27-buggate-l0310.md), one assertion per finding:
#   7. `claim` without --pid is refused (exit 1), never silently records
#      lane-lock's own dying CLI pid (finding #1)
#   8. a corrupt breaker.json fails CLOSED (claim refused, exit 3) and the
#      corruption itself is written to the alarms log (finding #2)
#   9. a wrong-type path at the critsec lock location (a plain file, not a
#      directory) fails fast with a clear error instead of hanging
#      forever (finding #3) — bounded with a background job + poll loop,
#      since macOS ships no `timeout` binary
#  10. a pid that is alive but whose recorded process-start-time no longer
#      matches (in the CURRENT tagged fingerprint format) is treated as a
#      different process, not the same live holder (finding #4's identity
#      check)
#  11. `force-release` mechanically clears a stuck lane and logs who did
#      it and why to the alarms log (finding #4's override command)
#
# Added 2026-09-27 to close the re-gate r2 FAIL
# (hub/reports/2026-09-27-buggate-l0310-r2.md), one assertion per finding:
#  12. TZ/LANG pinning (blocker #1): claim under TZ=America/Chicago, check
#      under TZ=UTC, with a genuinely live `sleep` holder — the lane must
#      STAY held, never self-heal purely because the caller's env differs
#  13. legacy-format tolerance (blocker #1's other half): a start_key in
#      the OLD bare-string format (pre-TZ-pinning) must be treated as
#      UNKNOWN identity — assume alive, alarm — never as proof of death
#  14. a corrupt owner.json fails CLOSED (claim AND release refused,
#      pointing at force-release) instead of being silently read as
#      "unheld" (blocker #2)
#  15. `--pid 0` and negative pids are rejected before any write (nit)
#
# Runs entirely against a throwaway state root (LANE_LOCK_ROOT) and a
# throwaway alarms log — never touches production hub state. Exits 0 iff
# every assertion PASSes.
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LL="$HERE/lane-lock"
TMP=$(mktemp -d -t lane-lock-test)
export LANE_LOCK_ROOT="$TMP/lanes"
export LANE_LOCK_ALARMS_LOG="$TMP/alarms.log"
export LANE_BREAKER_THRESHOLD=2
LANE="test-lane-$$"

FAILS=0
pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILS=$((FAILS+1)); }

cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

echo "== test root: $TMP =="
echo "== lane: $LANE =="
echo

# --- 1. basic claim/release ---
out=$("$LL" claim "$LANE" --owner ownerA --pid $$ --reason "self-test basic claim" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == CLAIMED* ]] && pass "1. basic claim succeeds" || fail "1. basic claim succeeds (rc=$rc, out=$out)"

# --- 2. different LIVE owner refused (ownerA's pid = $$, this shell, definitely alive) ---
out=$("$LL" claim "$LANE" --owner ownerB --pid 1 2>&1); rc=$?
echo "$out"
[[ $rc -eq 2 && "$out" == REFUSED* ]] && pass "2. live other-owner claim refused, exit 2" || fail "2. live other-owner claim refused (rc=$rc, out=$out)"

out=$("$LL" release "$LANE" --owner ownerA 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == RELEASED* ]] && pass "2b. owner can release its own claim" || fail "2b. release (rc=$rc, out=$out)"

# --- 3. dead-pid holder self-heals ---
# 999999 is astronomically unlikely to be a live pid on this machine.
out=$("$LL" claim "$LANE" --owner ownerDead --pid 999999 --reason "will die" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 ]] && pass "3a. setup claim by ownerDead succeeds" || fail "3a. setup claim by ownerDead should succeed (rc=$rc, out=$out)"
out=$("$LL" claim "$LANE" --owner ownerC --pid $$ --reason "should self-heal" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == *"self-healed"* ]] && pass "3b. dead-pid holder self-heals, new claim succeeds" || fail "3b. self-heal (rc=$rc, out=$out)"
"$LL" release "$LANE" --owner ownerC >/dev/null 2>&1

# --- 4. THE CORE PROOF: circuit breaker trips after threshold FAILs ---
out=$("$LL" record-fail "$LANE" --ticket L-9001 --gate bug-gate --detail "fail 1 of 2" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == OK* ]] && pass "4a. first FAIL recorded, breaker not yet tripped" || fail "4a. first FAIL (rc=$rc, out=$out)"

out=$("$LL" record-fail "$LANE" --ticket L-9001 --gate bug-gate --detail "fail 2 of 2, over threshold" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 3 && "$out" == TRIPPED* ]] && pass "4b. second FAIL trips the breaker, exit 3" || fail "4b. breaker trip (rc=$rc, out=$out)"

grep -q "BREAKER TRIPPED" "$LANE_LOCK_ALARMS_LOG" 2>/dev/null \
  && pass "4c. trip was written to the alarms log" \
  || fail "4c. alarm log missing the trip line ($(cat "$LANE_LOCK_ALARMS_LOG" 2>/dev/null))"

out=$("$LL" claim "$LANE" --owner ownerD --pid $$ --reason "should be refused, breaker tripped" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 3 && "$out" == REFUSED* && "$out" == *"TRIPPED"* ]] && pass "4d. claim on a tripped lane is REFUSED, exit 3" || fail "4d. claim refused while tripped (rc=$rc, out=$out)"

# --- 5. one PASS after a trip does not silently clear it ---
out=$("$LL" record-pass "$LANE" --ticket L-9001 --gate bug-gate --detail "fixed, gate passed" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == *"still TRIPPED"* ]] && pass "5a. record-pass resets streak but says still tripped" || fail "5a. record-pass after trip (rc=$rc, out=$out)"

out=$("$LL" claim "$LANE" --owner ownerE --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 3 ]] && pass "5b. claim still refused after a mere PASS (manual-reset only, as designed)" || fail "5b. claim after PASS-but-not-reset (rc=$rc, out=$out)"

# --- 6. explicit reset clears the trip ---
out=$("$LL" reset "$LANE" --by hub --reason "reviewed the diff, fix confirmed live" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == OK* ]] && pass "6a. explicit reset succeeds" || fail "6a. reset (rc=$rc, out=$out)"

out=$("$LL" claim "$LANE" --owner ownerF --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == CLAIMED* ]] && pass "6b. claim succeeds again after reset" || fail "6b. claim after reset (rc=$rc, out=$out)"

# --- 7. finding #1: --pid is now REQUIRED for claim ---
NOPID_LANE="nopid-lane-$$"
out=$("$LL" claim "$NOPID_LANE" --owner noPidOwner --reason "omitted --pid on purpose" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 1 && "$out" == *"--pid is required"* ]] && pass "7a. claim without --pid is refused, exit 1" || fail "7a. claim without --pid (rc=$rc, out=$out)"
out=$("$LL" status "$NOPID_LANE" 2>&1)
[[ "$out" == *"owner=(none, unheld)"* ]] && pass "7b. the refused no-pid claim left the lane unheld (no footgun claim recorded under lane-lock's own dying pid)" || fail "7b. lane should still be unheld (out=$out)"

# --- 8. finding #2: a corrupt breaker.json fails CLOSED, not silently un-tripped ---
CORRUPT_LANE="corrupt-lane-$$"
mkdir -p "$LANE_LOCK_ROOT/$CORRUPT_LANE"
printf '{ "fail_streak": 2, "tripped": tru' > "$LANE_LOCK_ROOT/$CORRUPT_LANE/breaker.json"
out=$("$LL" claim "$CORRUPT_LANE" --owner sneakIn --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 3 && "$out" == *"REFUSED"* && "$out" == *"TRIPPED"* ]] && pass "8a. corrupt breaker.json fails CLOSED, claim refused exit 3 (never silently un-tripped)" || fail "8a. corrupt breaker claim (rc=$rc, out=$out)"
grep -q "breaker.json is CORRUPT" "$LANE_LOCK_ALARMS_LOG" 2>/dev/null \
  && pass "8b. the corruption itself was written to the alarms log" \
  || fail "8b. alarm log missing the corruption line ($(cat "$LANE_LOCK_ALARMS_LOG" 2>/dev/null))"

# --- 9. finding #3: a wrong-type critsec lock path fails fast, never hangs ---
# macOS ships no `timeout` binary — bound the wait with a background job +
# a short poll loop instead, so a real hang FAILS this assertion loudly
# rather than wedging the whole self-test forever.
WRONGTYPE_LANE="wrongtype-lane-$$"
mkdir -p "$LANE_LOCK_ROOT/$WRONGTYPE_LANE"
touch "$LANE_LOCK_ROOT/$WRONGTYPE_LANE/.critsec.lock"   # plain FILE, not a dir
LANE_LOCK_WAIT_S=1 "$LL" claim "$WRONGTYPE_LANE" --owner x --pid $$ >"$TMP/out9" 2>&1 &
bgpid=$!
waited=0
while kill -0 "$bgpid" 2>/dev/null && [[ $waited -lt 16 ]]; do
  sleep 0.5
  waited=$((waited+1))
done
if kill -0 "$bgpid" 2>/dev/null; then
  kill -9 "$bgpid" 2>/dev/null
  fail "9. wrong-type critsec lock HUNG past the bounded wait (had to be force-killed — this is the exact bug-gate repro)"
else
  wait "$bgpid" 2>/dev/null; rc=$?
  out=$(cat "$TMP/out9")
  echo "$out"
  [[ $rc -ne 0 && "$out" == *"not a directory"* ]] && pass "9. wrong-type critsec lock fails fast, non-zero, clear error (no hang)" || fail "9. wrong-type critsec lock (rc=$rc, out=$out)"
fi

# --- 10. finding #4: pid identity check (alive pid, mismatched start time
#          IN THE CURRENT TAGGED FORMAT == a different process, not the
#          same live holder). NOTE: this forges a dict tagged with the
#          real fmt but a wrong value — a genuine same-format mismatch —
#          NOT a bare/legacy string, which test 13 below proves is
#          handled differently (tolerated as unknown, never as dead;
#          re-gate r2 blocker #1). Keep this fmt string in sync with
#          START_KEY_FMT in lane-lock if that constant ever changes. ---
IDLANE="identity-lane-$$"
out=$("$LL" claim "$IDLANE" --owner origOwner --pid $$ --reason "orig holder" 2>&1); rc=$?
[[ $rc -eq 0 ]] || fail "10a. setup claim for identity test (rc=$rc, out=$out)"
python3 - "$LANE_LOCK_ROOT/$IDLANE/owner.json" <<'PYEOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
# Current tagged format, but a value that can never match the live
# process's real (pinned) start time -- a genuine identity mismatch.
d["start_key"] = {"fmt": "lstart-C-UTC-v1", "value": "Mon Jan  1 00:00:00 1990"}
json.dump(d, open(p, "w"))
PYEOF
out=$("$LL" claim "$IDLANE" --owner newOwner --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == *"self-healed"* ]] && pass "10b. alive pid with mismatched (same-format) start-time is treated as a different process, self-heals" || fail "10b. identity mismatch should self-heal (rc=$rc, out=$out)"

# --- 11. finding #4: force-release is a mechanical override that logs who/why ---
out=$("$LL" force-release "$IDLANE" --by hub --reason "regression test override" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == *"FORCE-RELEASED"* ]] && pass "11a. force-release succeeds" || fail "11a. force-release (rc=$rc, out=$out)"
grep -q "FORCE-RELEASED by hub" "$LANE_LOCK_ALARMS_LOG" 2>/dev/null \
  && pass "11b. force-release logged who and why to the alarms log" \
  || fail "11b. alarm log missing the force-release line ($(cat "$LANE_LOCK_ALARMS_LOG" 2>/dev/null))"
out=$("$LL" claim "$IDLANE" --owner freshOwner --pid $$ 2>&1); rc=$?
[[ $rc -eq 0 && "$out" == CLAIMED* ]] && pass "11c. lane is claimable again after force-release" || fail "11c. claim after force-release (rc=$rc, out=$out)"

# --- 12. re-gate r2 blocker #1: TZ/LANG pinning — a genuinely live holder
#          claimed under one TZ must NOT be declared dead by a check
#          running under a different TZ. Uses a real, long-lived `sleep`
#          process as the holder so this is a real cross-process,
#          cross-env repro, not a hand-forged string. ---
TZLANE="tz-lane-$$"
sleep 30 &
HOLDER_PID=$!
out=$(TZ=America/Chicago "$LL" claim "$TZLANE" --owner realHolder --pid "$HOLDER_PID" --reason "long-lived holder, claimed under Chicago time" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == CLAIMED* ]] && pass "12a. setup: claim under TZ=America/Chicago" || fail "12a. tz claim setup (rc=$rc, out=$out)"
out=$(TZ=UTC "$LL" claim "$TZLANE" --owner intruder --pid $$ --reason "should be refused, real holder still alive" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 2 && "$out" == REFUSED* ]] && pass "12b. lane STAYS held when checked under a different TZ (no false self-heal purely from an env difference)" || fail "12b. cross-TZ identity check (rc=$rc, out=$out)"
kill -0 "$HOLDER_PID" 2>/dev/null && pass "12c. the real holder process was never touched, still alive throughout" || fail "12c. holder process should still be alive"
kill "$HOLDER_PID" 2>/dev/null; wait "$HOLDER_PID" 2>/dev/null
"$LL" force-release "$TZLANE" --by hub --reason "cleanup after tz cross-check test" >/dev/null 2>&1

# --- 13. re-gate r2 blocker #1 (other half): a start_key in the OLD
#          bare-string format (pre-TZ-pinning) must be treated as UNKNOWN
#          identity -- assumed alive, alarmed -- never as proof of death.
#          Also uses a real live `sleep` holder so a wrongly-"dead"
#          verdict would show up as an actual self-heal, not just a
#          string comparison. ---
LEGACYLANE="legacy-lane-$$"
sleep 30 &
LEGACY_PID=$!
"$LL" claim "$LEGACYLANE" --owner legacyHolder --pid "$LEGACY_PID" --reason "will get a legacy-format start_key" >/dev/null 2>&1
python3 - "$LANE_LOCK_ROOT/$LEGACYLANE/owner.json" <<'PYEOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["start_key"] = "Sun Sep 27 23:12:15 2026"  # bare string == the pre-fix, unpinned format
json.dump(d, open(p, "w"))
PYEOF
out=$("$LL" claim "$LEGACYLANE" --owner intruder2 --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 2 && "$out" == REFUSED* ]] && pass "13a. a legacy-format start_key is tolerated as unknown-assume-alive, never treated as dead" || fail "13a. legacy format tolerance (rc=$rc, out=$out)"
grep -q "unrecognized or legacy format" "$LANE_LOCK_ALARMS_LOG" 2>/dev/null \
  && pass "13b. the legacy-format mismatch logged an alarm" \
  || fail "13b. missing legacy-format alarm ($(cat "$LANE_LOCK_ALARMS_LOG" 2>/dev/null))"
kill "$LEGACY_PID" 2>/dev/null; wait "$LEGACY_PID" 2>/dev/null
"$LL" force-release "$LEGACYLANE" --by hub --reason "cleanup after legacy-format test" >/dev/null 2>&1

# --- 14. re-gate r2 blocker #2: a corrupt owner.json fails CLOSED
#          (claim AND release refused), never silently read as "unheld";
#          force-release is the documented recovery path. ---
CORRUPT_OWNER_LANE="corrupt-owner-lane-$$"
mkdir -p "$LANE_LOCK_ROOT/$CORRUPT_OWNER_LANE"
printf '{ "owner": "realOwner", "pid": 1, "brok' > "$LANE_LOCK_ROOT/$CORRUPT_OWNER_LANE/owner.json"
out=$("$LL" claim "$CORRUPT_OWNER_LANE" --owner intruder3 --pid $$ 2>&1); rc=$?
echo "$out"
[[ $rc -eq 2 && "$out" == *"REFUSED"* ]] && pass "14a. corrupt owner.json fails CLOSED, claim refused (never silently unheld)" || fail "14a. corrupt owner.json claim (rc=$rc, out=$out)"
out=$("$LL" release "$CORRUPT_OWNER_LANE" --owner intruder3 2>&1); rc=$?
echo "$out"
[[ $rc -eq 2 && "$out" == *"CORRUPT"* ]] && pass "14b. corrupt owner.json also refuses release, points at force-release" || fail "14b. corrupt owner.json release (rc=$rc, out=$out)"
grep -q "owner.json is CORRUPT" "$LANE_LOCK_ALARMS_LOG" 2>/dev/null \
  && pass "14c. owner.json corruption logged to the alarms log" \
  || fail "14c. missing owner.json corruption alarm"
out=$("$LL" force-release "$CORRUPT_OWNER_LANE" --by hub --reason "clearing corrupt owner.json" 2>&1); rc=$?
echo "$out"
[[ $rc -eq 0 && "$out" == *"FORCE-RELEASED"* ]] && pass "14d. force-release recovers a corrupt-owner lane" || fail "14d. force-release after corrupt owner (rc=$rc, out=$out)"
out=$("$LL" claim "$CORRUPT_OWNER_LANE" --owner freshOwner2 --pid $$ 2>&1); rc=$?
[[ $rc -eq 0 && "$out" == CLAIMED* ]] && pass "14e. lane claimable again after force-release" || fail "14e. claim after recovery (rc=$rc, out=$out)"

# --- 15. nit: reject --pid <= 0 before any write ---
out=$("$LL" claim "zero-pid-lane-$$" --owner x --pid 0 2>&1); rc=$?
echo "$out"
[[ $rc -eq 1 && "$out" == *"positive integer"* ]] && pass "15a. --pid 0 is rejected" || fail "15a. --pid 0 (rc=$rc, out=$out)"
out=$("$LL" claim "neg-pid-lane-$$" --owner x --pid -5 2>&1); rc=$?
echo "$out"
[[ $rc -eq 1 && "$out" == *"positive integer"* ]] && pass "15b. a negative --pid is rejected" || fail "15b. --pid -5 (rc=$rc, out=$out)"

echo
if [[ $FAILS -eq 0 ]]; then
  echo "ALL PASS"
  exit 0
else
  echo "$FAILS ASSERTION(S) FAILED"
  exit 1
fi
