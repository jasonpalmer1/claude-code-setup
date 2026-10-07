#!/bin/zsh
# test-selfcheck.sh (L-1776 S1). Run: zsh hub/bin/test-selfcheck.sh
# SC_UNDER_TEST=<path> points the suite at a mutated copy (mutation proof).
# Every selfcheck call runs under a 5s alarm so a hang is a FAIL (rc 142), not a hang.
setopt pipefail
SC=${SC_UNDER_TEST:-${0:A:h}/selfcheck}
T=$(mktemp -d); export SELFGATE_LOG=$T/log; trap 'rm -rf $T' EXIT
R=$T/repo; git init -q $R; cd $R
git config user.email t@t; git config user.name t
print 'print ok' > test-ok.sh; print 'exit 3' > test-bad.sh
git add test-ok.sh test-bad.sh; git commit -qm init
SHA=$(git rev-parse HEAD); C=$(git rev-parse --absolute-git-dir)
fail=0
sc() { perl -e 'alarm 5; exec @ARGV' zsh $SC "$@"; }
expect() { # name want reason-substring cmd...  (want 0 ignores reason)
  local n=$1 w=$2 why=$3; shift 3; local out; out=$("$@" 2>&1); local r=$?
  if [[ $r == $w && ( $w == 0 || ( $out == REFUSED:* && $out == *${why}* ) ) ]]; then print "PASS $n -> ${out%%$'\n'*}"
  else print "FAIL $n (rc=$r want $w, reason ~ '$why'): $out"; fail=1; fi
}
pc() { sc precheck --repo $R --sha ${1:-$SHA}; }

expect "missing record REFUSED" 2 "no pregate/selfcheck record" pc
sc run --repo $R --ticket T1 test-ok.sh >/dev/null
expect "valid record PASS" 0 "" pc
expect "wrong sha REFUSED" 2 "not a commit" pc 1111111111111111111111111111111111111111
expect "short sha REFUSED" 2 "40-hex" pc ${SHA:0:12}
expect "uppercase sha REFUSED" 2 "40-hex" pc ${(U)SHA}
# (b) clean record, tree dirtied afterwards
print x >> test-ok.sh
expect "dirty-now REFUSED" 2 "dirty now" pc
git checkout -q test-ok.sh
# (a) record copied from another sha, and symlinked
print y > f2; git add f2; git commit -qm second; SHA2=$(git rev-parse HEAD)
cp $C/selfcheck/$SHA $C/selfcheck/$SHA2
expect "record copied from another sha REFUSED" 2 "does not match" pc $SHA2
rm $C/selfcheck/$SHA2; ln -s $C/selfcheck/$SHA $C/selfcheck/$SHA2
expect "symlinked record REFUSED" 2 "does not match" pc $SHA2
rm $C/selfcheck/$SHA2
# (c) empty / touched record
mkdir -p $C/pregate; rm $C/selfcheck/$SHA; : > $C/pregate/$SHA
expect "empty/touched record REFUSED" 2 "missing or empty" pc
print '{"ts":"x"}' > $C/pregate/$SHA
expect "record without sha REFUSED" 2 "does not match" pc
print "{\"sha\":\"$SHA\",\"checks\":[\"tsc\"]}" > $C/pregate/$SHA
expect "genuine pregate-shaped record PASS" 0 "" pc
print "{\"sha\":\"$SHA\",\"checks\":[\"tsc\"],\"clean\":false}" > $C/pregate/$SHA
expect "record flagged dirty REFUSED" 2 "dirty" pc
rm $C/pregate/$SHA
git reset -q --hard $SHA
print x >> test-ok.sh; sc run --repo $R test-ok.sh >/dev/null   # dirty-tree run
git checkout -q test-ok.sh
expect "dirty-run record REFUSED" 2 "dirty" pc
sc run --repo $R test-bad.sh >/dev/null; rc=$?
[[ $rc == 3 ]] && print "PASS failing test propagates exit 3" || { print "FAIL run rc=$rc"; fail=1; }
expect "deliberate-error branch (exit 3 record) REFUSED" 2 "exit code" pc
# flag as last argument: must refuse fast, never hang (rc 142 = alarm)
EMPTY=
expect "precheck --sha as last arg REFUSED" 64 "needs a value" sc precheck --repo $R --sha $EMPTY
expect "precheck --repo as last arg REFUSED" 64 "needs a value" sc precheck --sha $SHA --repo
expect "run --ticket as last arg REFUSED" 64 "needs a value" sc run --repo $R test-ok.sh --ticket
expect "no args REFUSED" 64 "" sc
# run: test path outside the repo is refused
print true > $T/noop.sh
expect "run outside-repo path REFUSED" 64 "outside the repo" sc run --repo $R $T/noop.sh
expect "run ../ escape REFUSED" 64 "outside the repo" sc run --repo $R ../noop.sh
(( fail )) && { print "RESULT: FAIL"; exit 1; }
print "RESULT: all passed"
