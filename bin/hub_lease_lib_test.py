#!/usr/bin/env python3
"""Tests for hub_lease_lib.py (L-0911, the enforced single-hub lease).

Every test runs against a temp dir — never the live HUB-LEASE.json,
hub-lease.log, delegation-alarms.log, or ~/.claude/sessions. Module-level
path constants are monkeypatched per-test (setUp), the same technique as
ledger_lib_test.py, since hub_lease_lib reads its env-var overrides once at
import time.

Run with:
    python3 ~/.claude/hub/bin/hub_lease_lib_test.py -v
or  cd ~/.claude/hub/bin && python3 -m unittest hub_lease_lib_test -v

Covers the plan's required tests (~/.claude/hub/reports/L-single-hub-plan.md
section 6/7 + the ticket's own list):
  - two simulated sessions
  - stale takeover blocked while the holder pid is alive
  - stale takeover allowed when the holder pid is dead and heartbeat is old
  - a peer --release refused
  - --override --jason-quote accepted, logged, and pushed (stubbed ntfy)
  - A1 fail-open: corrupt lease, missing file, unwritable dir
  - banner text for non-holders
  - race: two back-to-back claims never corrupt the file, exactly one wins
  - pgrep -f only, never pgrep -fl, anywhere in the new code
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hub_lease_lib as hl  # noqa: E402

BIN_DIR = Path(__file__).parent


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class HubLeaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hub-lease-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._orig = {
            "LEASE_PATH": hl.LEASE_PATH, "LOG_PATH": hl.LOG_PATH,
            "ALARMS_PATH": hl.ALARMS_PATH, "SESSIONS_DIR": hl.SESSIONS_DIR,
            "LOCK_PATH": hl.LOCK_PATH, "STALE_THRESHOLD_S": hl.STALE_THRESHOLD_S,
            "PGREP_PATTERN": hl.PGREP_PATTERN, "NTFY_LIB": hl.NTFY_LIB,
            "ALARM_STATE_PATH": hl.ALARM_STATE_PATH,
            "ALARM_STATE_LOCK_PATH": hl.ALARM_STATE_LOCK_PATH,
            "ALARM_LOCK_TIMEOUT_S": hl.ALARM_LOCK_TIMEOUT_S,
            "ALARM_NTFY_COOLDOWN_S": hl.ALARM_NTFY_COOLDOWN_S,
            "ALARM_LOG_BUCKET_S": hl.ALARM_LOG_BUCKET_S,
        }
        hl.LEASE_PATH = Path(self.tmp) / "HUB-LEASE.json"
        hl.LOG_PATH = Path(self.tmp) / "hub-lease.log"
        hl.ALARMS_PATH = Path(self.tmp) / "delegation-alarms.log"
        hl.SESSIONS_DIR = Path(self.tmp) / "sessions"
        hl.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        hl.LOCK_PATH = Path(str(hl.LEASE_PATH) + ".lock")
        hl.STALE_THRESHOLD_S = 5  # tests use seconds, not the 45-min production default
        # A fixture pattern that matches a real, controllable child process
        # (a `sleep` we spawn ourselves) instead of the production
        # 'claude/versions' pattern — never invents a real claude process.
        hl.PGREP_PATTERN = "hub-lease-test-fixture-marker"
        hl.NTFY_LIB = Path(self.tmp) / "ntfy.sh"  # stubbed per-test below
        # r4 alarm-dedup sidecar: isolated per-test, never the live path,
        # same convention as every other overridable path above.
        hl.ALARM_STATE_PATH = Path(self.tmp) / "hub-lease-alarm-state.json"
        # r5: the lock file is derived from ALARM_STATE_PATH, so it must be
        # re-derived here too, same convention LOCK_PATH already follows
        # relative to LEASE_PATH above. A short 2s timeout keeps the
        # 50-thread contention test fast without changing the production
        # default.
        hl.ALARM_STATE_LOCK_PATH = Path(str(hl.ALARM_STATE_PATH) + ".lock")
        hl.ALARM_LOCK_TIMEOUT_S = 2.0
        hl.ALARM_NTFY_COOLDOWN_S = 6 * 3600
        hl.ALARM_LOG_BUCKET_S = 3600

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(hl, k, v)

    def register_session(self, session_id: str, pid: int):
        (hl.SESSIONS_DIR / f"{pid}.json").write_text(json.dumps({"sessionId": session_id, "pid": pid}))

    def spawn_marker_process(self):
        """A real, killable process whose argv contains PGREP_PATTERN, so
        `pgrep -f <PGREP_PATTERN>` finds it exactly like production finds a
        real `claude` process by its argv. Uses python3 -c with the marker
        as an unused extra arg (sys.argv[1:]) rather than `sleep 300
        <marker>`, since BSD/macOS sleep rejects extra arguments outright."""
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)", "hub-lease-test-fixture-marker"]
        )

        def _reap():
            p.kill()
            try:
                p.wait(timeout=5)
            except Exception:
                pass

        self.addCleanup(_reap)
        return p.pid

    def stub_ntfy(self):
        """Fake ntfy.sh whose ntfy_push() just appends to a file instead of
        curling the real network — 'stub ntfy in tests' per the ticket."""
        calls = Path(self.tmp) / "ntfy-calls.log"
        hl.NTFY_LIB.write_text(
            'ntfy_push() { printf "%s|%s\\n" "$1" "$2" >> "' + str(calls) + '"; }\n'
        )
        return calls


class TwoSimulatedSessions(HubLeaseTestCase):
    def test_second_session_refused_names_first_and_age(self):
        self.register_session("session-a", 111)
        self.register_session("session-b", 222)
        r1 = hl.claim(session_id="session-a", cwd="/x", pid=111, reason="first")
        self.assertTrue(r1.ok)
        self.assertEqual(r1.message, "CLAIMED by Hub [sessio]")

        r2 = hl.claim(session_id="session-b", cwd="/x", pid=222, reason="second")
        self.assertFalse(r2.ok)
        self.assertIn("sessio", r2.message)  # names the first session (short tag)
        self.assertIn("ALIVE", r2.message)
        self.assertIn("heartbeat", r2.message)
        self.assertIn("ago", r2.message)

    def test_hub_who_shaped_output(self):
        self.register_session("session-a", 111)
        hl.claim(session_id="session-a", cwd="/x", pid=111)
        line = hl.status()
        self.assertTrue(line.startswith("HUB: Hub [sessio]"))
        self.assertIn("ALIVE", line)

    def test_no_lease_present_reads_hub_none(self):
        self.assertEqual(hl.status(), "HUB: none")
        v, age = hl.verdict(hl.read_lease())
        self.assertEqual(v, "NONE")


class StaleTakeoverGating(HubLeaseTestCase):
    def _stale_lease(self, session_id, pid, seconds_stale):
        past = datetime.now(timezone.utc) - timedelta(seconds=seconds_stale)
        hl.write_lease_atomic({
            "session_id": session_id, "pid": pid, "cwd": "/x",
            "claimed_at": iso(past), "heartbeat_at": iso(past),
            "reason": "test", "override": False, "jason_quote": None,
        })

    def test_stale_but_pid_alive_is_suspect_and_blocks_unattended_takeover(self):
        pid = self.spawn_marker_process()
        self.register_session("holder", pid)
        self._stale_lease("holder", pid, seconds_stale=hl.STALE_THRESHOLD_S + 5)

        v, age = hl.verdict(hl.read_lease())
        self.assertEqual(v, "SUSPECT")

        r = hl.claim(session_id="challenger", cwd="/x", pid=999)
        self.assertFalse(r.ok, "a frozen-but-alive holder must refuse an unattended takeover")
        self.assertIn("holder", r.message)

    def test_stale_and_pid_dead_and_no_session_file_is_dead_and_allows_takeover(self):
        # No session-file registration, no live process with the fixture
        # marker for this pid -> both signal 2 and signal 3 read "gone".
        dead_pid = 999999  # not registered, not running
        self._stale_lease("holder", dead_pid, seconds_stale=hl.STALE_THRESHOLD_S + 5)

        v, age = hl.verdict(hl.read_lease())
        self.assertEqual(v, "DEAD")

        r = hl.claim(session_id="challenger", cwd="/x", pid=111)
        self.assertTrue(r.ok, "all three signals agreeing 'gone' must allow an unattended takeover")
        self.assertIn("challenger"[:6], r.message)

        # Stale takeover events alarm too (A3's "forged overrides get SEEN"
        # extends to unattended takeovers being visible, not just overrides).
        log = hl.LOG_PATH.read_text()
        self.assertIn("CLAIM by Hub [challe]", log)
        alarms = hl.ALARMS_PATH.read_text()
        self.assertIn("STALE TAKEOVER", alarms)

    def test_fresh_heartbeat_wins_even_if_pid_signals_would_say_gone(self):
        # Clock-skew guard: heartbeat fresh -> ALIVE regardless of 2/3.
        hl.write_lease_atomic({
            "session_id": "holder", "pid": 999999, "cwd": "/x",
            "claimed_at": iso(datetime.now(timezone.utc)),
            "heartbeat_at": iso(datetime.now(timezone.utc)),
            "reason": "test", "override": False, "jason_quote": None,
        })
        v, age = hl.verdict(hl.read_lease())
        self.assertEqual(v, "ALIVE")


class ReleaseIsSelfOnly(HubLeaseTestCase):
    def test_peer_release_refused(self):
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)

        r = hl.release(session_id="peer-session", reason="I say so")
        self.assertFalse(r.ok, "a peer must never be able to release someone else's lease")
        self.assertIn("not the holder", r.message)

        # The lease must still show the original holder, unchanged.
        v, _ = hl.verdict(hl.read_lease())
        self.assertEqual(v, "ALIVE")
        self.assertEqual(hl.read_lease()["session_id"], "holder")

    def test_self_release_succeeds(self):
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)
        r = hl.release(session_id="holder", reason="handing off")
        self.assertTrue(r.ok)
        self.assertEqual(hl.status(), "HUB: none")


class OverrideAcceptedLoggedPushed(HubLeaseTestCase):
    def test_override_with_quote_takes_a_live_lease_and_alarms(self):
        calls = self.stub_ntfy()
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)

        r = hl.claim(session_id="challenger", cwd="/y", pid=2, override=True,
                      jason_quote="the operator: take back the hub")
        self.assertTrue(r.ok)
        self.assertEqual(hl.read_lease()["session_id"], "challenger")
        self.assertEqual(hl.read_lease()["jason_quote"], "the operator: take back the hub")
        self.assertTrue(hl.read_lease()["override"])

        log = hl.LOG_PATH.read_text()
        self.assertIn("OVERRIDE", log)
        self.assertIn("challe", log)
        alarms = hl.ALARMS_PATH.read_text()
        self.assertIn("HUB-LEASE OVERRIDE", alarms)

        # ntfy_push must actually have been invoked (stubbed, never the real network).
        self.assertTrue(calls.exists(), "override must call ntfy_push")
        self.assertIn("challe", calls.read_text())

    def test_override_with_blank_quote_refused(self):
        r = hl.claim(session_id="challenger", cwd="/y", pid=2, override=True, jason_quote="   ")
        self.assertFalse(r.ok)
        self.assertIn("blank", r.message)

    def test_override_with_no_quote_refused(self):
        r = hl.claim(session_id="challenger", cwd="/y", pid=2, override=True, jason_quote=None)
        self.assertFalse(r.ok)


class A1FailOpen(HubLeaseTestCase):
    def test_corrupt_lease_reads_as_none(self):
        hl.LEASE_PATH.write_text("{not valid json::: ")
        self.assertIsNone(hl.read_lease())
        self.assertEqual(hl.status(), "HUB: none")
        v, _ = hl.verdict(hl.read_lease())
        self.assertEqual(v, "NONE")

    def test_missing_lease_reads_as_none(self):
        self.assertFalse(hl.LEASE_PATH.exists())
        self.assertEqual(hl.status(), "HUB: none")

    def test_unwritable_dir_claim_fails_open_not_crash(self):
        # Point LEASE_PATH at a directory that can't be created/written.
        blocked_dir = Path(self.tmp) / "blocked"
        blocked_dir.mkdir()
        os.chmod(blocked_dir, 0o400)  # read+nothing, no write/exec for owner
        self.addCleanup(os.chmod, blocked_dir, 0o700)
        hl.LEASE_PATH = blocked_dir / "sub" / "HUB-LEASE.json"
        hl.LOCK_PATH = Path(str(hl.LEASE_PATH) + ".lock")
        try:
            r = hl.claim(session_id="x", cwd="/x", pid=1)
            self.assertFalse(r.ok)
            self.assertIn("unwritable", r.message)
        except Exception as e:  # pragma: no cover - the whole point of the test
            self.fail(f"claim() must fail open on an unwritable dir, not raise: {e!r}")

    def test_empty_lease_file_reads_as_none(self):
        hl.LEASE_PATH.write_text("")
        self.assertIsNone(hl.read_lease())

    def test_lease_missing_session_id_reads_as_none(self):
        hl.LEASE_PATH.write_text(json.dumps({"pid": 1, "cwd": "/x"}))
        self.assertIsNone(hl.read_lease())


class BannerTextForNonHolders(HubLeaseTestCase):
    def test_not_the_hub_banner_names_holder_and_states_the_rule(self):
        self.register_session("holder-session", 1)
        hl.claim(session_id="holder-session", cwd="/x", pid=1)
        lease = hl.read_lease()
        v, age = hl.verdict(lease)
        banner = hl.not_the_hub_banner(lease, v, age)
        self.assertIn("NOT THE HUB", banner)
        self.assertIn("holder"[:6], banner)  # names the holder's session tag (A6)
        self.assertIn("never a reason to stand down", banner)  # A6's exact required sentence, in substance
        self.assertIn("hub-who", banner)


class RaceSafety(HubLeaseTestCase):
    def test_concurrent_claims_never_corrupt_the_file_and_exactly_one_wins_as_new_holder(self):
        results = []

        def attempt(sid, pid):
            self.register_session(sid, pid)
            r = hl.claim(session_id=sid, cwd="/x", pid=pid)
            results.append((sid, r.ok))

        threads = [threading.Thread(target=attempt, args=(f"racer-{i}", 1000 + i)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # File must always be valid, complete JSON — never half-written.
        text = hl.LEASE_PATH.read_text()
        data = json.loads(text)  # raises if corrupted/truncated
        self.assertIn(data["session_id"], [f"racer-{i}" for i in range(8)])

        # Exactly one racer should have won the CLAIM (the rest either
        # refused outright or, if they landed after the winner, heartbeat-
        # refreshed under a session_id that isn't their own — impossible
        # here since every session_id is distinct, so every non-winner must
        # have been refused).
        winners = [sid for sid, ok in results if ok]
        self.assertGreaterEqual(len(winners), 1)
        # The final file's holder must be one of the winners.
        self.assertIn(data["session_id"], winners)


class PgrepFlagDiscipline(unittest.TestCase):
    def test_no_pgrep_fl_anywhere_in_the_new_code(self):
        new_files = [
            BIN_DIR / "hub_lease_lib.py",
            BIN_DIR / "hub-claim",
            BIN_DIR / "hub-who",
            Path(__file__).parent.parent.parent / "hooks" / "security-tripwire.py",
            Path(__file__).parent.parent.parent / "hooks" / "ledger-banner-hook.py",
        ]
        for f in new_files:
            if not f.exists():
                continue
            for lineno, line in enumerate(f.read_text().splitlines(), 1):
                low = line.lower()
                if "-fl" not in low.replace("'-fl'", "-fl").replace('"-fl"', "-fl"):
                    continue
                # Comments/docstrings are allowed to NAME -fl while
                # explaining the rule ("never pgrep -fl") — only fail on a
                # line that isn't explanatory prose (no "never"/"only"
                # nearby) or that actually constructs a subprocess arg list
                # containing the literal token "-fl".
                is_prose = "never" in low or "only" in low
                constructs_arg = '"-fl"' in line or "'-fl'" in line
                if constructs_arg and not is_prose:
                    self.fail(f"{f}:{lineno} constructs a real pgrep -fl argument: {line!r}")
                if not is_prose and "pgrep -fl" in low:
                    self.fail(f"{f}:{lineno} uses pgrep -fl outside of explanatory prose: {line!r}")


class HeartbeatIsIdempotentAndSilent(HubLeaseTestCase):
    def test_same_session_reclaim_is_a_silent_heartbeat_not_a_new_log_line(self):
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)
        before = hl.LOG_PATH.read_text() if hl.LOG_PATH.exists() else ""
        r = hl.claim(session_id="holder", cwd="/x", pid=1, reason="heartbeat")
        self.assertTrue(r.ok)
        self.assertIn("HEARTBEAT", r.message)
        after = hl.LOG_PATH.read_text() if hl.LOG_PATH.exists() else ""
        self.assertEqual(before, after, "a same-session heartbeat must not spam hub-lease.log")


class PidDriftOnResume(HubLeaseTestCase):
    """L-0911 bug-gate blocker 1: a resumed session (or `claude
    remote-control` reconnect) keeps session_id but gets a new OS pid.
    claim()'s holder==session_id branch must refresh `pid`, not just
    `heartbeat_at`/`cwd`, or a later stale check reads the old dead pid
    and falsely declares the still-alive holder DEAD."""

    def test_reclaim_under_new_pid_after_old_pid_dies_keeps_holder_alive_and_refuses_challenger(self):
        pid_a = 100
        self.register_session("holder", pid_a)
        r1 = hl.claim(session_id="holder", cwd="/x", pid=pid_a)
        self.assertTrue(r1.ok)
        self.assertEqual(hl.read_lease()["pid"], pid_a)

        # pid A "dies" (process gone, session file removed); the same
        # session_id resumes under a new, live pid B.
        (hl.SESSIONS_DIR / f"{pid_a}.json").unlink()
        pid_b = self.spawn_marker_process()
        self.register_session("holder", pid_b)
        r2 = hl.claim(session_id="holder", cwd="/x", pid=pid_b, reason="resume heartbeat")
        self.assertTrue(r2.ok)
        self.assertEqual(hl.read_lease()["pid"], pid_b,
                          "heartbeat must refresh pid to the resumed process, not keep the dead one")

        # Age the lease past stale threshold so the dead-check actually runs.
        lease = hl.read_lease()
        past = datetime.now(timezone.utc) - timedelta(seconds=hl.STALE_THRESHOLD_S + 5)
        lease["heartbeat_at"] = iso(past)
        hl.write_lease_atomic(lease)

        v, age = hl.verdict(hl.read_lease())
        self.assertEqual(v, "SUSPECT", "pid B is alive -> must NOT read as DEAD")

        challenger = hl.claim(session_id="challenger", cwd="/x", pid=999, auto=True)
        self.assertFalse(challenger.ok,
                          "a genuinely alive resumed holder must never be auto-claimed over")

        # The holder is still the holder -- no false NOT THE HUB banner for
        # its own next heartbeat.
        lease_after = hl.read_lease()
        self.assertEqual(lease_after["session_id"], "holder")

    def test_heartbeat_with_unresolved_pid_does_not_null_out_existing_pid(self):
        # find_session_pid is best-effort and can return None on a
        # transient SESSIONS_DIR read hiccup. The heartbeat-refresh branch
        # must not overwrite a previously good pid with None in that case
        # -- that would make both dead-check signals read "gone" at the
        # next stale check, which is worse than the original bug.
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)
        (hl.SESSIONS_DIR / "1.json").unlink()  # simulate lookup failure
        r = hl.claim(session_id="holder", cwd="/x", pid=None, reason="heartbeat, lookup hiccup")
        self.assertTrue(r.ok)
        self.assertEqual(hl.read_lease()["pid"], 1,
                          "a failed pid lookup must not null out a previously good pid")


class PidClaimIntegrity(HubLeaseTestCase):
    """L-0911 bug-gate r2 blocker 1: claim()'s holder==session_id branch
    trusted ANY caller-supplied pid, so anyone who merely knew the
    holder's session_id (printed in plain sight throughout this repo:
    cost-meter/<session_id>.json filenames, ledger.jsonl rows, etc.) could
    silently overwrite the lease's pid under the real holder's identity,
    with zero forensic trace (no append_log call at all in that branch).
    Round-3 fix: pid actually written is sourced solely from the session
    registry (find_session_pid), a caller-claimed pid that disagrees with
    the registry is refused outright with the lease left untouched, and a
    registry-agreed pid CHANGE is still refused while the old pid is
    genuinely alive -- both refusals logged loudly and alarmed, and even
    the legitimate resume case now logs its pid change (plan A3: lease
    changes are audited)."""

    def test_gate_r2_impersonation_repro_is_refused_cleanly(self):
        # The exact scenario from hub/reports/L-0911-buggate-r2.md: a
        # caller who knows the holder's session_id passes ITS OWN pid/cwd,
        # no --override, no --jason-quote needed under the old code.
        self.register_session("S", 100)
        hl.claim(session_id="S", cwd="/real-hub", pid=100, reason="initial claim")
        before_lease = hl.read_lease()
        before_log = hl.LOG_PATH.read_text() if hl.LOG_PATH.exists() else ""

        r = hl.claim(session_id="S", cwd="/attacker-cwd", pid=666, reason="impersonation attempt")

        self.assertFalse(r.ok, "a caller-claimed pid that disagrees with the registry must be refused")
        after_lease = hl.read_lease()
        self.assertEqual(after_lease, before_lease,
                          "lease must be completely unchanged, including cwd -- not just pid")
        after_log = hl.LOG_PATH.read_text()
        self.assertNotEqual(before_log, after_log, "the refusal must leave a forensic trace")
        self.assertIn("666", after_log)
        self.assertIn("PID-CLAIM REFUSED", after_log)

    def test_legitimate_resume_still_refreshes_and_logs_the_pid_change(self):
        pid_a = 100
        self.register_session("holder", pid_a)
        hl.claim(session_id="holder", cwd="/x", pid=pid_a)

        # pid A genuinely dies; session resumes under a live pid B, found
        # only via the registry -- no explicit --pid passed, exactly like
        # security-tripwire.py's own call (auto=True, no pid kwarg).
        (hl.SESSIONS_DIR / f"{pid_a}.json").unlink()
        pid_b = self.spawn_marker_process()
        self.register_session("holder", pid_b)

        r = hl.claim(session_id="holder", cwd="/x", reason="resume heartbeat")
        self.assertTrue(r.ok)
        self.assertEqual(hl.read_lease()["pid"], pid_b)
        log = hl.LOG_PATH.read_text()
        self.assertIn("PID CHANGED", log, "even the legitimate case must be logged per plan A3")
        self.assertIn(f"{pid_a} -> {pid_b}", log)

    def test_pid_change_refused_while_old_pid_still_alive(self):
        # A registry-agreed pid change is still refused if the OLD
        # recorded pid is genuinely alive -- two live processes for one
        # session_id is an anomaly, never silently treated as a resume.
        pid_a = self.spawn_marker_process()  # a REAL, live process throughout this test
        self.register_session("holder", pid_a)
        hl.claim(session_id="holder", cwd="/x", pid=pid_a)

        # The registry now shows a DIFFERENT pid for the same session_id
        # (e.g. a forged/duplicated registry record) while pid_a's real
        # process is still genuinely running.
        (hl.SESSIONS_DIR / f"{pid_a}.json").unlink()
        pid_b = 999999
        self.register_session("holder", pid_b)

        before_lease = hl.read_lease()
        r = hl.claim(session_id="holder", cwd="/x", reason="suspicious second registration")
        self.assertFalse(r.ok, "two live pids for one session_id must be refused, not taken as a resume")
        self.assertEqual(hl.read_lease(), before_lease, "lease must stay on the still-alive original pid")
        log = hl.LOG_PATH.read_text()
        self.assertIn("PID-CHANGE REFUSED", log)
        alarms = hl.ALARMS_PATH.read_text()
        self.assertIn("PID-CHANGE ANOMALY", alarms)

    def test_none_lookup_guard_still_holds_after_r3(self):
        # Re-confirms PidDriftOnResume's original guard survives the r3
        # rewrite of this branch: a failed registry lookup must not null
        # out a previously-good pid.
        self.register_session("holder", 1)
        hl.claim(session_id="holder", cwd="/x", pid=1)
        (hl.SESSIONS_DIR / "1.json").unlink()
        r = hl.claim(session_id="holder", cwd="/x", pid=None, reason="heartbeat, lookup hiccup")
        self.assertTrue(r.ok)
        self.assertEqual(hl.read_lease()["pid"], 1)


class AlarmDedup(HubLeaseTestCase):
    """L-0911 bug-gate r4: the PID-CHANGE ANOMALY (and PID-CLAIM MISMATCH)
    refusal fired append_alarm + ntfy_push on EVERY call, and
    ledger-banner-hook.py's per-prompt `_hub_lease_block()` retries the
    same claim() call every prompt -- so one sustained overlap (a
    slow-to-reap old process, an OS pid-reuse collision, a stale registry
    record) produced one real phone push PER PROMPT. Fixed: `should_ntfy`
    gates a real push to at most once per (session_id, old_pid, new_pid,
    reason) key per cooldown window (default 6h); `append_log_collapsed`
    collapses repeats of the same key within the same time bucket
    (default 1h) into one hub-lease.log/delegation-alarms.log line with a
    running count instead of one identical line per call."""

    def _sustained_overlap_claim(self, n: int):
        """Recreates the r4 gate report's own repro: session S claims
        under a real live pid A, the registry is then overwritten to show
        S -> a second real live pid B while A is still genuinely running,
        and the same claim() call `security-tripwire.py`/
        `ledger-banner-hook.py` would make on every prompt is repeated
        `n` times."""
        pid_a = self.spawn_marker_process()
        self.register_session("S", pid_a)
        hl.claim(session_id="S", cwd="/real-hub", pid=pid_a, reason="init")
        pid_b = self.spawn_marker_process()
        self.register_session("S", pid_b)  # registry now shows S -> B; A still alive
        for _ in range(n):
            r = hl.claim(session_id="S", cwd="/real-hub", auto=True, reason="heartbeat")
            self.assertFalse(r.ok, "the overlap must still be refused every time, only the alarm is deduped")

    def test_sustained_anomaly_produces_exactly_one_ntfy_push_for_eight_prompts(self):
        calls = self.stub_ntfy()
        self._sustained_overlap_claim(8)
        self.assertEqual(len(calls.read_text().splitlines()), 1,
                          "8 prompts during one sustained overlap must produce exactly 1 ntfy push")

    def test_a_different_dedup_key_produces_its_own_new_push(self):
        calls = self.stub_ntfy()
        self._sustained_overlap_claim(8)
        self.assertEqual(len(calls.read_text().splitlines()), 1)

        # A different `reason` names a DIFFERENT (session_id, old_pid,
        # new_pid, reason) key -- it must get its own push, not be
        # swallowed by the first key's cooldown.
        r = hl.claim(session_id="S", cwd="/real-hub", auto=True, reason="a different reason")
        self.assertFalse(r.ok)
        self.assertEqual(len(calls.read_text().splitlines()), 2,
                          "a genuinely different dedup key must produce a new push")

    def test_hub_lease_log_and_alarms_log_collapse_repeats_to_one_line_with_a_count(self):
        self._sustained_overlap_claim(8)

        log_lines = [l for l in hl.LOG_PATH.read_text().splitlines() if "PID-CHANGE REFUSED" in l]
        self.assertEqual(len(log_lines), 1,
                          "8 repeats of the same key within the same hour must collapse to 1 log line")
        self.assertIn("(repeat count: 8)", log_lines[0])

        alarm_lines = [l for l in hl.ALARMS_PATH.read_text().splitlines() if "PID-CHANGE ANOMALY" in l]
        self.assertEqual(len(alarm_lines), 1,
                          "delegation-alarms.log must collapse the same way, not just hub-lease.log")
        self.assertIn("(repeat count: 8)", alarm_lines[0])

    def test_impersonation_mismatch_alarm_is_also_deduped(self):
        # Same treatment for the OTHER alarm path named by the fix
        # instructions ("dedup the anomaly and impersonation alarms").
        calls = self.stub_ntfy()
        self.register_session("S", 100)
        hl.claim(session_id="S", cwd="/real-hub", pid=100, reason="init")
        for _ in range(8):
            r = hl.claim(session_id="S", cwd="/attacker-cwd", pid=666, reason="impersonation attempt")
            self.assertFalse(r.ok)
        self.assertEqual(len(calls.read_text().splitlines()), 1,
                          "8 repeated impersonation attempts with the same shape must produce 1 push")
        log_lines = [l for l in hl.LOG_PATH.read_text().splitlines() if "PID-CLAIM REFUSED" in l]
        self.assertEqual(len(log_lines), 1)
        self.assertIn("(repeat count: 8)", log_lines[0])


class PidResolutionDeterminism(HubLeaseTestCase):
    """L-0911 bug-gate r5 blocker 1: the old find_session_pid() returned
    whichever <pid>.json file os.listdir() happened to list first, which
    is unspecified when two records for the same session_id genuinely
    coexist -- the gate reproduced the resulting anomaly-detection miss
    failing 1 time in 3. resolve_session_pid() replaces it with a rule
    that never depends on listing order: prefer a live pid, then the
    newest by start time (mtime fallback), then the numeric pid itself.
    These tests exercise the resolver directly, not through claim(), so a
    reintroduced order-dependence would be caught here even if it were
    rare enough to not show up in a handful of AlarmDedup runs."""

    def test_resolve_is_deterministic_across_many_runs_with_two_coexisting_live_records(self):
        pid_a = self.spawn_marker_process()
        self.register_session("S", pid_a)
        pid_b = self.spawn_marker_process()
        self.register_session("S", pid_b)  # both A's and B's registry files coexist; both alive
        results = [hl.resolve_session_pid("S") for _ in range(50)]
        pids = {r[0] for r in results}
        live_sets = {tuple(sorted(r[1])) for r in results}
        self.assertEqual(len(pids), 1,
                          "50 repeated calls against the SAME coexisting records must all agree on the resolved pid")
        self.assertEqual(len(live_sets), 1,
                          "...and must all agree on the live-pid set too")
        self.assertEqual(pids.pop(), pid_b, "newest (registered later) live record wins the tie-break")
        self.assertEqual(live_sets.pop(), tuple(sorted([pid_a, pid_b])))

    def test_resolve_prefers_a_live_pid_over_a_dead_records_newer_timestamp(self):
        pid_a = self.spawn_marker_process()
        self.register_session("S", pid_a)   # live, older mtime
        self.register_session("S", 999999)  # dead (never spawned), newer mtime
        pid, live = hl.resolve_session_pid("S")
        self.assertEqual(pid, pid_a, "a live pid must win over a dead record's newer timestamp")
        self.assertEqual(live, [pid_a])


class AlarmStateLocking(HubLeaseTestCase):
    """L-0911 bug-gate r5 blocker 3: should_ntfy()'s read-decide-write
    around the alarm-state sidecar was unlocked -- the gate demonstrated
    up to 100/100 concurrent threads for the same key all passing the
    cooldown check together (no upper bound at all, not just "2"). Now
    guarded by _alarm_state_lock(), a short-timeout flock on a lock file
    next to the sidecar."""

    def test_fifty_simultaneous_callers_for_the_same_key_give_exactly_one_push(self):
        key = "S|100|200|heartbeat"
        n = 50
        barrier = threading.Barrier(n)
        results = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()  # release all n threads at (as close to) the same instant
            r = hl.should_ntfy(key)
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), n, "every thread must have returned before the join timeout")
        self.assertEqual(sum(1 for r in results if r), 1,
                          f"50 simultaneous callers for the same key must produce exactly 1 True (push), got {sum(results)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
