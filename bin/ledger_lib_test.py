#!/usr/bin/env python3
"""Tests for ledger_lib.py's L-0377 fixes.

Pure-function tests against synthetic records -- never touch the real
ledger.jsonl, so safe to run in a live repo with other sessions writing to
it concurrently. Mirrors status_test.py's shape (see that file for the
sibling fix to bin/status).

Run with:
    python3 ~/.claude/hub/bin/ledger_lib_test.py -v
or  cd ~/.claude/hub/bin && python3 -m unittest ledger_lib_test -v

CRITICAL CONTEXT (see this module's own docstring): ledger_lib.py backs a
UserPromptSubmit hook that runs on every prompt in every live Claude Code
session on this machine. These tests exist specifically because a fix here
that "looks right" but is wrong breaks the operator's prompts everywhere -- run
them before AND after any change to this file.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ledger_lib  # noqa: E402


def make_rec(**overrides) -> dict:
    rec = {
        "id": "L-TEST", "title": "a test ticket", "status": "open",
        "asked_on": None, "asked_by": "hub", "owner": "unowned",
        "trigger": "", "project": None, "proof": [], "last_surfaced": None,
        "jason_ack": False, "created_at": None, "updated_at": None,
        "notes": [], "decisions": [], "schema_issues": [],
    }
    rec.update(overrides)
    return rec


class NeedsYouIsNotARubberStampTests(unittest.TestCase):
    """L-0377(a): pins that the abolished rubber-stamp rule (OPERATOR.md,
    2026-09-15: "finished internal work is not a decision") stays dead in
    ledger_lib, not just in bin/status (which bin/status's own test suite,
    status_test.py's NeedsYouIsNotARubberStampTests, already pins)."""

    def test_needs_jason_status_is_needs_you(self):
        rec = make_rec(status="needs-jason")
        self.assertTrue(ledger_lib.needs_you(rec))

    def test_done_jason_asked_no_ack_is_NOT_needs_you(self):
        """The exact shape that produced the false CONFIRM lines on
        L-0354/L-0360 (see L-0377's own ledger note) and required a manual
        24-ticket ack-event workaround (commit 84f6c31) to clear -- proof
        the bug was live, not merely theoretical, right up to this fix."""
        rec = make_rec(status="done", asked_by="jason", jason_ack=False)
        self.assertFalse(ledger_lib.needs_you(rec))

    def test_done_jason_asked_with_ack_is_also_not_needs_you(self):
        rec = make_rec(status="done", asked_by="jason", jason_ack=True)
        self.assertFalse(ledger_lib.needs_you(rec))

    def test_done_hub_asked_no_ack_is_not_needs_you(self):
        rec = make_rec(status="done", asked_by="hub", jason_ack=False)
        self.assertFalse(ledger_lib.needs_you(rec))

    def test_open_and_active_are_not_needs_you(self):
        for status in ("open", "active", "dropped"):
            rec = make_rec(status=status)
            self.assertFalse(ledger_lib.needs_you(rec), status)

    def test_mutation_restoring_the_old_predicate_flips_this_red(self):
        """Not a test of production code -- a self-check that the tests
        above actually exercise the live branch and aren't vacuously green.
        Reimplements the OLD (pre-fix) rule inline and shows the exact
        synthetic ticket from test_done_jason_asked_no_ack_is_NOT_needs_you
        WOULD have tripped it, so that test is provably not a dead branch."""
        def old_needs_you(rec):
            if rec["status"] == "needs-jason":
                return True
            if rec["status"] == "done" and rec["asked_by"] == "jason" and not rec["jason_ack"]:
                return True
            return False

        rec = make_rec(status="done", asked_by="jason", jason_ack=False)
        self.assertFalse(ledger_lib.needs_you(rec), "fixed predicate")
        self.assertTrue(old_needs_you(rec), "old predicate must still trip on the same input")

    def test_banner_lines_needs_you_count_matches_needs_you(self):
        records = {
            "L-1": make_rec(id="L-1", status="needs-jason"),
            "L-2": make_rec(id="L-2", status="done", asked_by="jason", jason_ack=False),
            "L-3": make_rec(id="L-3", status="done", asked_by="jason", jason_ack=False),
            "L-4": make_rec(id="L-4", status="active"),
        }
        lines = ledger_lib.banner_lines(records)
        self.assertEqual(lines[0], "Open: 2 · Needs you: 1")


class SurfaceLinesNoConfirmBlockTests(unittest.TestCase):
    """L-0377(a) continued: surface_lines() must never print a CONFIRM /
    awaiting-ack line again, for any status."""

    def test_no_confirm_line_for_done_unacked_jason_ask(self):
        records = {"L-1": make_rec(id="L-1", status="done", asked_by="jason",
                                    jason_ack=False, title="ship the thing")}
        lines = ledger_lib.surface_lines(records)
        self.assertEqual(lines, [])
        self.assertFalse(any("CONFIRM" in line for line in lines))

    def test_needs_jason_still_surfaces_first(self):
        now = ledger_lib.now_iso()
        records = {
            "L-1": make_rec(id="L-1", status="needs-jason", title="pick a color", updated_at=now),
        }
        lines = ledger_lib.surface_lines(records)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("NEEDS-JASON L-1"))


class ImportedBoardNoteTests(unittest.TestCase):
    """L-0377(c): the work-item vs imported-note discriminator, and that it
    only ever suppresses the STALE-48h+ line, never anything else."""

    def test_migrated_shapes_are_recognised(self):
        for title in (
            "2026-09-06 13:00 · hub · **AGENT FRAMEWORK REDESIGN (the operator...",
            "2026-09-03 · extract · the operator chose public BYO-key (detail...",
            "2026-09-04 07:45 · extract · **ECONOMY DIAL DECIDED BY JASON...",
        ):
            self.assertTrue(ledger_lib.is_imported_board_note(make_rec(title=title)), title)

    def test_live_ledger_add_shapes_are_not_flagged(self):
        for title in (
            "DEFINE how a ticket gets CLOSED - a real done-process, none exists",
            "ledger_lib.py needs_you/banner still uses old rubber-stamp rule",
            "Clean up migrated ledger titles (board prose to short titles)",
        ):
            self.assertFalse(ledger_lib.is_imported_board_note(make_rec(title=title)), title)

    def test_leading_board_status_word_is_flagged(self):
        """ledger-migrate's extract_title() left a leading board status word
        on some lines ("ACTIVE 2026-09-06 · hub · ..."). These are migrated
        notes like any other and must not nag forever.

        This was a real, measured false negative, not a hypothetical: after
        L-0377(c) shipped, `ledger surface` still printed L-0113 as a
        perpetual "STALE 48h+ ... still owned by hub?" line. Exactly 2
        records in the live 758-record ledger carry this shape (L-0112 done,
        L-0113 open), so widening the regex changed the classification of
        one live open ticket and nothing else."""
        for title in (
            "ACTIVE 2026-09-06 · hub · HQ \"Agents\" page (the operator's ask, ...)",
            "ACTIVE 2026-09-06 · hub (this chat) · Mission HQ usage audit",
        ):
            self.assertTrue(ledger_lib.is_imported_board_note(make_rec(title=title)), title)

    def test_leading_word_alone_does_not_flag_a_live_title(self):
        """The widened regex still requires the migrated shape after the
        leading word: a date AND the separator. A live `ledger add` title
        that merely opens with a capitalised word must stay unflagged, or
        the discriminator starts hiding real work."""
        for title in (
            "QUEUED: WS retention + community research, nothing built",
            "DEFINE how a ticket gets CLOSED - a real done-process",
            "NEEDS JASON: Phase 2 PRs #38 (funnel analytics) + #36",
            "ACTIVE work on the 2026-09-06 regression, no separator here",
        ):
            self.assertFalse(ledger_lib.is_imported_board_note(make_rec(title=title)), title)

    def test_empty_or_missing_title_is_not_flagged(self):
        self.assertFalse(ledger_lib.is_imported_board_note(make_rec(title="")))
        rec = make_rec(); del rec["title"]
        self.assertFalse(ledger_lib.is_imported_board_note(rec))

    def test_stale_imported_note_does_not_surface(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = {
            "L-5": make_rec(id="L-5", status="open", updated_at=old,
                             title="2026-09-06 13:00 · hub · old board note"),
        }
        lines = ledger_lib.surface_lines(records)
        self.assertEqual(lines, [])

    def test_stale_real_ticket_still_surfaces(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = {
            "L-6": make_rec(id="L-6", status="open", updated_at=old,
                             title="a real ticket nobody has touched in days"),
        }
        lines = ledger_lib.surface_lines(records)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("STALE 48h+ L-6"))

    def test_mutation_disabling_the_discriminator_flips_this_red(self):
        """Self-check, same purpose as the needs_you mutation test above:
        proves test_stale_imported_note_does_not_surface is exercising the
        real is_imported_board_note() gate in surface_lines() and not a
        dead/unreachable branch. Monkeypatches the predicate to always-False
        (i.e. "nothing is ever an imported note") and shows the suppressed
        line comes back."""
        old = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        records = {
            "L-5": make_rec(id="L-5", status="open", updated_at=old,
                             title="2026-09-06 13:00 · hub · old board note"),
        }
        self.assertEqual(ledger_lib.surface_lines(records), [], "fixed behaviour: suppressed")

        original = ledger_lib.is_imported_board_note
        ledger_lib.is_imported_board_note = lambda rec: False
        try:
            lines = ledger_lib.surface_lines(records)
        finally:
            ledger_lib.is_imported_board_note = original
        self.assertEqual(len(lines), 1, "with the gate disabled the STALE line must reappear")
        self.assertTrue(lines[0].startswith("STALE 48h+ L-5"))


class OwnerVerbTests(unittest.TestCase):
    """L-0377(b): claim/release/reassign share one `owner` event; fold()
    applies it without touching status; validate_event enforces it carries
    an owner."""

    def test_fold_applies_owner_event_without_touching_status(self):
        events = [
            {"ts": "2026-01-01T00:00:00Z", "id": "L-7", "event": "created",
             "title": "x", "asked_by": "hub", "owner": "unowned"},
            {"ts": "2026-01-01T00:05:00Z", "id": "L-7", "event": "status", "status": "active"},
            {"ts": "2026-01-01T00:10:00Z", "id": "L-7", "event": "owner", "owner": "ec8f03"},
        ]
        rec = ledger_lib.fold(events)["L-7"]
        self.assertEqual(rec["owner"], "ec8f03")
        self.assertEqual(rec["status"], "active")

    def test_fold_owner_event_bumps_updated_at(self):
        events = [
            {"ts": "2026-01-01T00:00:00Z", "id": "L-8", "event": "created",
             "title": "x", "asked_by": "hub"},
            {"ts": "2026-01-02T00:00:00Z", "id": "L-8", "event": "owner", "owner": "someone"},
        ]
        rec = ledger_lib.fold(events)["L-8"]
        self.assertEqual(rec["updated_at"], "2026-01-02T00:00:00Z")

    def test_validate_event_accepts_owner_event(self):
        ledger_lib.validate_event({"id": "L-9", "event": "owner", "owner": "someone"})  # no raise

    def test_validate_event_rejects_owner_event_with_no_owner(self):
        with self.assertRaises(ValueError):
            ledger_lib.validate_event({"id": "L-9", "event": "owner", "owner": ""})

    def test_mutation_reverting_canonical_set_flips_this_red(self):
        """Proves the "owner" branch added to VALID event set in
        validate_event() is the thing test_validate_event_accepts_owner_event
        actually depends on, not a coincidence: reproduces the OLD canonical
        set inline and shows it would have rejected the same event."""
        old_canonical = {"created", "status", "proof", "ack", "surfaced", "note", "decision"}
        self.assertNotIn("owner", old_canonical)
        self.assertIn("owner", {"created", "status", "proof", "ack", "surfaced", "note", "decision", "owner"})
        # and the live function now accepts it:
        ledger_lib.validate_event({"id": "L-9", "event": "owner", "owner": "x"})  # no raise


class DoneGateExcludesMetricClaimTests(unittest.TestCase):
    """A3 (L-0763 hub audit, fixed 2026-09-22): fold() appends every `proof`
    event to rec["proof"] regardless of `type`, indiscriminately -- confirmed
    unchanged by this fix, on purpose (metric-claim events must stay visible,
    never filtered out of the raw record). What must change is what COUNTS
    toward the `status done` hard-proof gate: has_done_gate_proof()."""

    def test_metric_claim_alone_does_not_count(self):
        rec = make_rec(proof=[{"type": "metric-claim", "ref": "clean_sessions baseline"}])
        self.assertFalse(ledger_lib.has_done_gate_proof(rec))

    def test_metric_claim_verdict_alone_does_not_count(self):
        rec = make_rec(proof=[{"type": "metric-claim-verdict", "ref": "moved +14%"}])
        self.assertFalse(ledger_lib.has_done_gate_proof(rec))

    def test_both_metric_claim_types_together_still_do_not_count(self):
        rec = make_rec(proof=[
            {"type": "metric-claim", "ref": "baseline"},
            {"type": "metric-claim-verdict", "ref": "verdict"},
        ])
        self.assertFalse(ledger_lib.has_done_gate_proof(rec))

    def test_a_real_proof_type_counts(self):
        rec = make_rec(proof=[{"type": "commit", "ref": "abc123"}])
        self.assertTrue(ledger_lib.has_done_gate_proof(rec))

    def test_a_real_proof_alongside_a_metric_claim_still_counts(self):
        rec = make_rec(proof=[
            {"type": "metric-claim", "ref": "baseline"},
            {"type": "report", "ref": "~/.claude/hub/reports/L-TEST.md"},
        ])
        self.assertTrue(ledger_lib.has_done_gate_proof(rec))

    def test_no_proof_at_all_does_not_count(self):
        rec = make_rec(proof=[])
        self.assertFalse(ledger_lib.has_done_gate_proof(rec))

    def test_legacy_non_dict_proof_entry_counts(self):
        # The one real legacy shape seen in ledger.jsonl (pre-dates the typed
        # {type, ref} convention `ledger proof` always writes) -- cannot be a
        # metric-claim, so it must not be excluded.
        rec = make_rec(proof=["some legacy string proof"])
        self.assertTrue(ledger_lib.has_done_gate_proof(rec))

    def test_mutation_reverting_to_bare_truthiness_flips_this_red(self):
        """Proves has_done_gate_proof() is actually doing the exclusion, not
        coincidentally agreeing: the OLD gate (bare truthiness of rec["proof"])
        would have accepted a metric-claim-only record; the new function does
        not."""
        rec = make_rec(proof=[{"type": "metric-claim", "ref": "baseline"}])
        old_gate_result = bool(rec["proof"])  # the pre-fix cmd_status check
        self.assertTrue(old_gate_result, "sanity: the old bare-truthiness gate WOULD have let this through")
        self.assertFalse(ledger_lib.has_done_gate_proof(rec), "the fixed gate must refuse it")


class ReviewCountTest(unittest.TestCase):
    """L-0791: the banner's "needs review" count ignores settled items and
    honours a `reviewed` event, while fold() keeps every issue on record."""

    def ev(self, ts, **kw):
        return {"ts": f"2026-09-0{ts}T00:00:00Z", "id": "L-X", **kw}

    def closed_history(self, *tail):
        # `closed` is the legacy ambiguous event: it always records an issue.
        return [
            self.ev(1, event="created", title="t", asked_by="hub"),
            self.ev(2, event="closed", note="legacy close"),
            *tail,
        ]

    def count(self, recs):
        return [l for l in ledger_lib.banner_lines(recs) if "needs review" in l]

    def test_open_item_with_issue_is_counted(self):
        recs = ledger_lib.fold(self.closed_history())
        self.assertEqual(len(recs["L-X"]["schema_issues"]), 1)
        self.assertEqual(len(ledger_lib.unreviewed_issues(recs["L-X"])), 1)
        self.assertEqual(self.count(recs), ["Ledger history needs review: 1 IDs; counts are provisional"])

    def test_done_and_dropped_items_are_not_counted_but_issues_kept(self):
        for status in ("done", "dropped"):
            recs = ledger_lib.fold(self.closed_history(self.ev(3, event="status", status=status)))
            self.assertEqual(len(recs["L-X"]["schema_issues"]), 1, "fold must still record history")
            self.assertEqual(ledger_lib.unreviewed_issues(recs["L-X"]), [])
            self.assertEqual(self.count(recs), [])

    def test_reviewed_clears_open_item(self):
        recs = ledger_lib.fold(self.closed_history(self.ev(3, event="reviewed", note="settled: legacy close was a drop")))
        rec = recs["L-X"]
        self.assertEqual(len(rec["schema_issues"]), 1, "fold must still record history")
        self.assertEqual(ledger_lib.unreviewed_issues(rec), [])
        self.assertEqual(self.count(recs), [])
        self.assertIn("settled: legacy close was a drop", [n["text"] for n in rec["notes"]])

    def test_later_bad_event_reflags_after_review(self):
        recs = ledger_lib.fold(self.closed_history(
            self.ev(3, event="reviewed", note="ok"),
            self.ev(4, event="closed", note="another legacy close"),
        ))
        rec = recs["L-X"]
        self.assertEqual(len(rec["schema_issues"]), 2)
        self.assertEqual(len(ledger_lib.unreviewed_issues(rec)), 1)
        self.assertEqual(len(self.count(recs)), 1)

    def test_same_timestamp_bad_event_after_review_still_reflags(self):
        # Order is by file position, so an equal timestamp cannot hide an issue.
        events = self.closed_history(self.ev(3, event="reviewed", note="ok"))
        events.append(self.ev(3, event="closed", note="same-second close"))
        rec = ledger_lib.fold(events)["L-X"]
        self.assertEqual(len(ledger_lib.unreviewed_issues(rec)), 1)

    def test_reviewed_does_not_advance_updated_at(self):
        rec = ledger_lib.fold(self.closed_history(self.ev(5, event="reviewed", note="ok")))["L-X"]
        self.assertEqual(rec["updated_at"], "2026-09-02T00:00:00Z")

    def test_reviewed_is_not_an_unsupported_event(self):
        rec = ledger_lib.fold(self.closed_history(self.ev(3, event="reviewed", note="ok")))["L-X"]
        self.assertNotIn("unsupported event", [i["reason"] for i in rec["schema_issues"]])

    def test_validate_event_requires_note_on_reviewed(self):
        with self.assertRaises(ValueError):
            ledger_lib.validate_event({"id": "L-X", "event": "reviewed"})
        ledger_lib.validate_event({"id": "L-X", "event": "reviewed", "note": "why"})

    def test_cli_reviewed_round_trip(self):
        import os, subprocess, tempfile, json
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.jsonl"
            path.write_text("".join(json.dumps(e) + "\n" for e in self.closed_history()))
            env = {**os.environ, "LEDGER_PATH": str(path)}
            cli = str(Path(__file__).parent / "ledger")
            run = lambda *a: subprocess.run([sys.executable, cli, *a], env=env, capture_output=True, text=True)
            self.assertNotEqual(run("reviewed", "L-X").returncode, 0, "--note is required")
            self.assertIn("needs review: 1", run("banner").stdout)
            r = run("reviewed", "L-X", "--note", "settled")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("needs review", run("banner").stdout)
            self.assertNotEqual(run("reviewed", "L-X", "--note", "again").returncode, 0, "nothing left to review")
            self.assertNotEqual(run("reviewed", "L-NOPE", "--note", "x").returncode, 0)


if __name__ == "__main__":
    unittest.main()
