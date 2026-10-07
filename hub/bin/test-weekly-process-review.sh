#!/bin/zsh
# test-weekly-process-review.sh (L-1780). Run: zsh hub/bin/test-weekly-process-review.sh
setopt pipefail
S=${WPR_UNDER_TEST:-${0:A:h}/weekly-process-review}
T=$(mktemp -d); trap 'rm -rf $T' EXIT
fail=0
ck() { if eval "$2"; then print "PASS $1"; else print "FAIL $1"; fail=1; fi }
NOW=2026-10-07T12:00:00Z
mkdir -p $T/h/admin $T/h/locks/lanes/L-1 $T/h/locks/lanes/L-2 $T/h/locks/lanes/L-3 $T/h/tmp $T/h/state $T/h/bin
# stub ask-operator: records args, one file per call
cat > $T/h/bin/ask-operator <<'EOS'
#!/bin/zsh
id=${${(M)@:#wr-*}[1]}; if grep -qx -- "$id" ${WPR_CARD_DIR}/../seen.txt 2>/dev/null; then print "409 duplicate"; exit 1; fi
print -r -- "$id" >> ${WPR_CARD_DIR}/../seen.txt
print -r -- "$@" > ${WPR_CARD_DIR}/$((++n))-$RANDOM.card; print "asked stub"
EOS
chmod +x $T/h/bin/ask-operator; mkdir $T/cards
# fixtures: this week 3 nights x 22 files (>5% over last week's 20), one file red 3 nights; last week 1 run x 20
mk() { # date nfiles redfile
  python3 - "$1" "$2" "$3" "$T/h/tmp/test-results-$1-$RANDOM.json" <<'EOP'
import json,sys
d,n,red,p=sys.argv[1:]
rows=[{"file":"t%d.mts"%i,"status":"\u0070ass","ms":1000} for i in range(int(n))]
if red!="-": rows.append({"file":red,"status":"fail","ms":60000}); 
json.dump({"generatedAt":d+"T03:00:00Z","rows":rows},open(p,"w"))
EOP
}
mk2() { # date hour nfiles file:status,file:status ...  (extra run with specific per-file statuses)
  /usr/bin/python3 - "$1" "$2" "$3" "$4" "$T/h/tmp/test-results-$1-x$2-$RANDOM.json" <<'EOP'
import json,sys
d,h,n,spec,p=sys.argv[1:]
rows=[{"file":"t%d.mts"%i,"status":"\u0070ass","ms":1000} for i in range(int(n))]
for kv in spec.split(","):
    if kv!="-":
        f,st=kv.split(":"); rows.append({"file":f,"status":st,"ms":1000})
json.dump({"generatedAt":"%sT%s:00:00Z"%(d,h),"rows":rows},open(p,"w"))
EOP
}
mk 2026-10-02 22 slow.mts; mk 2026-10-03 22 slow.mts; mk 2026-10-04 22 slow.mts; mk 2026-09-28 20 -
# real gate cases: l1595-p2 = 2 fails + 2 skips (counted 2, no card); broken 10-01 run (>50% files failed) is a run-level finding, not per-test
mk2 2026-10-02 04 22 test-l1595-p2.mts:fail; mk2 2026-10-03 04 22 test-l1595-p2.mts:fail
mk2 2026-10-04 04 22 test-l1595-p2.mts:skipped; mk2 2026-10-05 04 22 test-l1595-p2.mts:skipped
mk2 2026-10-02 05 22 victim.mts:fail; mk2 2026-10-03 05 22 victim.mts:fail
BAD=$(for i in $(seq 0 14); do printf 'f%d.mts:fail,' $i; done)victim.mts:fail
mk2 2026-10-01 03 5 $BAD
# later-week runs so rules still fire when re-run on 2026-10-12 (next ISO week)
mk 2026-10-09 22 slow.mts; mk 2026-10-10 22 slow.mts; mk 2026-10-11 22 slow.mts
# wrong-shape rows (missing file, ms as string, non-dict): skipped and counted, never a crash
print '{"generatedAt":"2026-10-03T06:00:00Z","rows":[{"status":"\u0070ass","ms":1},{"file":"a.mts","status":"\u0070ass","ms":"5"},"junk"]}' > $T/h/tmp/test-results-bad.json
print 'RELEASE-CLOCK start=2026-10-05T17:14:22Z live= minutes=45.4 lane=full' > $T/h/nightly-x.log
print '{"com.x.job": {"runs": 9, "status": "1", "streak": 4}}' > $T/h/admin/sched-fail-state.json
print '2026-10-06T20:14:48 disk-guard: 7Gi free — CRIT' > $T/h/disk-guard.log
for i in 1 2 3; do print "{\"history\":[{\"ts\":\"2026-10-05T01:00:00+00:00\",\"ticket\":\"L-$i\",\"gate\":\"bug-gate\",\"result\":\"FAIL\"},{\"ts\":\"2026-10-05T02:00:00+00:00\",\"ticket\":\"L-$i\",\"gate\":\"bug-gate\",\"result\":\"PASS\"}]}" > $T/h/locks/lanes/L-$i/breaker.json; done
print '| 2026-10-05 | abc | 1 | 1 | 1 | 1 | 1% | $0.50 | haiku=$0.50 |' > $T/ledger.md
export WPR_HUB=$T/h WPR_TESTS_GLOB="$T/h/tmp/test-results-*.json" WPR_LOGS_GLOB="$T/h/nightly-*.log" \
  WPR_SCHED=$T/h/admin/sched-fail-state.json WPR_DISK_LOG=$T/h/disk-guard.log WPR_LANES_GLOB="$T/h/locks/lanes/*/breaker.json" \
  WPR_TOKEN_LEDGER=$T/ledger.md WPR_OUT=$T/out WPR_ASK_CMD=$T/h/bin/ask-operator WPR_CARD_DIR=$T/cards

# 1 fixtures -> expected recs
out=$(/usr/bin/python3 $S --now $NOW); rc=$?
ck "fixture run rc=0" "[[ $rc == 0 ]]"
J=$T/out/weekly-review-2026-W41.json
ids=$(/usr/bin/python3 -c "import json;print(' '.join(r['id'] for r in json.load(open('$J'))['recommendations']))")
for want in test-growth red-slow-mts slow-deploy gate-first-pass disk-low sched-com-x-job broken-run-2026-10-01T03-00-00; do ck "rule fires: $want" "[[ ' $ids ' == *' $want '* ]]"; done
ck "l1595-p2 (2 fail + 2 skipped) is NOT red: skip is not a failure" "[[ ' $ids ' != *test-l1595-p2* ]]"
ck "victim.mts (2 real fails + 1 in broken run) is NOT red" "[[ ' $ids ' != *victim* ]]"
ck "broken run reported as its own finding, whole-run wording" "grep -q 'run-level failure' $T/out/weekly-review-2026-W41.md"
ck "red card wording: failed on N of last M nights (slow.mts 3 of 4)" "/usr/bin/python3 -c \"import json,sys;r=[x for x in json.load(open('$J'))['recommendations'] if x['id']=='red-slow-mts'][0];sys.exit(0 if 'failed on 3 of last 4 nights' in r['title'] and 'skipped is not counted' in r['evidence'] else 1)\""
ck "malformed rows counted (3 rows)" "grep -q '3 malformed rows skipped' $T/out/weekly-review-2026-W41.md"
ck "gate card states 20 min assumption" "/usr/bin/python3 -c \"import json,sys;r=[x for x in json.load(open('$J'))['recommendations'] if x['id']=='gate-first-pass'][0];sys.exit(0 if 'assumes ~20 min rework per failed first gate' in r['evidence'] else 1)\""
ck "slow-deploy minutes = 30.4" "grep -q '30.4' $T/out/weekly-review-2026-W41.md"
ck "gate rate 0% (all FAIL first)" "/usr/bin/python3 -c \"import json,sys;sys.exit(0 if json.load(open('$J'))['gate']['rate_pct']==0 else 1)\""
ck "tokens per ticket named unknown" "grep -q 'tokens per ticket: unknown' $T/out/weekly-review-2026-W41.md"
# 2 dry run writes no cards
ck "dry run: zero cards" "[[ -z \$(ls $T/cards) ]]"
ck "dry run: says none" "[[ \$out == *'none (dry run)'* ]]"
# 3 post + cap (7 recs fire, cap 5 -> 5 cards, 2 overflow)
/usr/bin/python3 $S --now $NOW --post >/dev/null; rc=$?
ck "post rc=0" "[[ $rc == 0 ]]"
ck "card cap: 5 cards of 7 recs" "[[ \$(ls $T/cards | wc -l | tr -d ' ') == 5 ]]"
ck "overflow recorded" "/usr/bin/python3 -c \"import json,sys;sys.exit(0 if len(json.load(open('$J'))['overflow_not_carded'])==2 else 1)\""
ck "card has recommended 0 + approve/decline" "grep -q -- '--recommended 0' $T/cards/1-*.card && grep -q Approve $T/cards/1-*.card && grep -q Decline $T/cards/1-*.card"
ck "one card per rec (distinct ids)" "[[ \$(cat $T/cards/*.card | grep -o -- '--id wr-[^ ]*' | sort -u | wc -l | tr -d ' ') == 5 ]]"
# 3b same ISO week, different days: no new cards (ids keyed on week), stub 409s any dupe
/usr/bin/python3 $S --now 2026-10-09T12:00:00Z --post >/dev/null; rc=$?
ck "same-week rerun on another day: rc=0, still 5 cards total" "[[ $rc == 0 && \$(ls $T/cards | wc -l | tr -d ' ') == 5 ]]"
/usr/bin/python3 $S --now $NOW --post >/dev/null
ck "same-day rerun: still 5 cards" "[[ \$(ls $T/cards | wc -l | tr -d ' ') == 5 ]]"
ck "card ids carry ISO week" "grep -q -- '--id wr-2026-W41-' $T/cards/1-*.card"
# 3c next week with open cards: not duplicated; a card that got >25% worse is reposted and says so
FIRST=$(cat $T/cards/*.card | grep -o -- '--id wr-2026-W41-[^ ]*' | sed 's/--id wr-2026-W41-//' | sort)
/usr/bin/python3 - $T/out/weekly-review-cards.json <<'EOP'
import json,sys
p=sys.argv[1]; s=json.load(open(p)); assert "slow-deploy" in s, list(s); s["slow-deploy"]["minutes"]=10.0; json.dump(s,open(p,"w"))
EOP
mkdir -p $T/cards2; rm -f $T/seen.txt; export WPR_CARD_DIR=$T/cards2
/usr/bin/python3 $S --now 2026-10-12T12:00:00Z --post >/dev/null
SECOND=$(cat $T/cards2/*.card | grep -o -- '--id wr-2026-W42-[^ ]*' | sed 's/--id wr-2026-W42-//' | sort)
DUPS=$(comm -12 <(print -l $FIRST) <(print -l $SECOND) | grep -v '^slow-deploy$' | wc -l | tr -d ' ')
ck "next week: open cards not duplicated (only the 25%-worse one returns)" "[[ $DUPS == 0 ]]"
ck "worse card reposted and says so on the card" "grep -l 'more than 25% worse' $T/cards2/*.card >/dev/null && grep -h -- '--id wr-2026-W42-slow-deploy' $T/cards2/*.card >/dev/null"
export WPR_CARD_DIR=$T/cards
# 4 empty sources fail soft, never fabricated zero
rm -rf $T/cards/*; mkdir -p $T/empty
env WPR_TESTS_GLOB="$T/empty/none-*" WPR_LOGS_GLOB="$T/empty/none-*" WPR_SCHED=$T/empty/x WPR_DISK_LOG=$T/empty/x WPR_LANES_GLOB="$T/empty/none-*" WPR_TOKEN_LEDGER=$T/empty/x \
  /usr/bin/python3 $S --now $NOW --post > $T/empty.out; rc=$?
ck "empty sources rc=0" "[[ $rc == 0 ]]"
ck "empty: 6 unknown lines" "[[ \$(grep -c 'unknown (' $T/empty.out) == 6 ]]"
ck "empty: no fabricated 0 / no recs / no cards" "! grep -qE ': 0 files|min 0 Gi|0\\.0%' $T/empty.out && grep -q 'None. No rule fired' $T/empty.out && [[ -z \$(ls $T/cards) ]]"
# 5 garbage JSON fails soft
print '{"history":["junk",5,{"ts":"2026-10-05T01:00:00+00:00","ticket":"L-9","gate":"g","result":"PASS"}]}' > $T/h/locks/lanes/L-3/breaker.json
/usr/bin/python3 $S --now $NOW >$T/m.out 2>&1; ck "wrong-shape gate history: rc=0, counted not crashed" "[[ \$? == 0 ]] && grep -q 'malformed rows skipped' $T/m.out"
print '{"a.job":{"status":"1","streak":"4"}}' > $T/h/admin/sched-fail-state.json
/usr/bin/python3 $S --now $NOW >$T/m2.out 2>&1; ck "string streak: rc=0" "[[ \$? == 0 ]]"
print 'not json' > $T/h/admin/sched-fail-state.json
/usr/bin/python3 $S --now $NOW >$T/g.out 2>&1; ck "garbage sched json: rc=0 + unknown" "[[ \$? == 0 ]] && grep -q 'Scheduled-job failures: unknown' $T/g.out"
exit $fail
