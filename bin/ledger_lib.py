"""Shared fold/read/write logic for the ledger CLI and ledger-migrate.

Not a public entry point — imported by ./ledger and ./ledger-migrate, which
both live in this same directory. See ./ledger's module docstring for the
full schema and the design doc at
~/.claude/hub/strategy/agent-framework-2026-09-06/F-ledger-design.md.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# LEDGER_PATH env var overrides the default so tests (and anything else) can
# point every command at an isolated /tmp copy without ever touching the real
# file. Read once per process at import time — every CLI invocation here is a
# fresh process, so there's no staleness risk.
LEDGER_PATH = Path(os.environ.get("LEDGER_PATH") or (Path.home() / ".claude/hub/ledger.jsonl"))
LOCK_PATH = Path(str(LEDGER_PATH) + ".lock")
VALID_STATUSES = ("open", "active", "needs-operator", "done", "dropped")
VALID_ASKED_BY = ("operator", "hub", "worker")
STALE_HOURS = 48

# Tolerant reading (rename, 2026-09-23): this tool used to spell the human
# operator's role literally as "operator" in a handful of field/status names
# (asked_by, the needs-operator status, the operator_ack ack field). Renamed to the
# generic role so this ledger tool works for anyone who installs it, not just
# one person. Every OLD line already appended to a real ledger.jsonl before
# this rename must still fold correctly -- these aliases are read-only
# compatibility, never written by any code from here on.
_LEGACY_ASKED_BY_ALIASES = {"operator": "operator"}

# A3 (L-0763 hub audit, fixed 2026-09-22): fold() appends every `proof` event
# to rec["proof"] regardless of `type`, and the done-gate in `ledger`'s
# cmd_status used to check bare truthiness of that list -- so a
# `metric-claim` (D4's ship-time claim convention) or a generator-written
# `metric-claim-verdict` (D5's scorecard) would have satisfied `status done`
# with no real proof behind it. These two types are excluded from what
# COUNTS toward the done-gate (see has_done_gate_proof() below) but are
# never filtered out of fold()'s rec["proof"] itself -- they stay fully
# visible as events, same as every other proof type.
DONE_GATE_EXCLUDED_PROOF_TYPES = frozenset({"metric-claim", "metric-claim-verdict"})


@contextlib.contextmanager
def ledger_lock():
    """Exclusive fcntl.flock on the sidecar `<ledger>.lock` file.

    Closes the concurrency hole in FIX 1 of BG-framework.md: `next_id()` reads
    the file and computes max+1 with no lock between that read and the
    append, so two concurrent `ledger add` calls could compute the same id
    and silently clobber each other on fold. Every command that assigns a new
    id (`add`, and `ledger-migrate`'s per-line id generation) holds this lock
    across read-next-id + append; every other command that only appends an
    event against an already-known id holds it across that single append.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def claim_actor() -> dict | None:
    """Best-effort identity of the process making a claim (root cause of
    L-0372: a `status active` event recorded WHEN via `ts`, which every event
    already carries, but never WHO -- so a claim with no live worker behind
    it was indistinguishable from real in-flight work).

    Claude Code sets CLAUDE_PID and CLAUDE_CODE_SESSION_ID for the lifetime
    of a top-level session process; both are inherited by every Bash call
    made from it, and subagents launched via the Agent tool are children of
    that same process (see hooks/subagent-stop-reap.py's module docstring).
    So this PID is a correct liveness proxy for "could anything still be
    working this ticket": if the process is gone, everything it could have
    spawned for this ticket is gone too.

    Returns None outside a Claude Code process (e.g. manual CLI testing, or
    a shell with these env vars unset) -- callers must treat that as
    "unknown claimant", never as "dead claimant".
    """
    pid_raw = os.environ.get("CLAUDE_PID")
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not pid_raw and not sid:
        return None
    actor: dict = {}
    if pid_raw:
        try:
            actor["pid"] = int(pid_raw)
        except ValueError:
            pass
    if sid:
        actor["session_id"] = sid
    return actor or None


def parse_ts(ts: str) -> datetime:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)


def read_events() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    events = []
    with LEDGER_PATH.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Ledger record must be an object")
                events.append(value)
            except json.JSONDecodeError:
                raise ValueError("Malformed ledger JSON; refusing incomplete counts")
    return events


def append_event(event: dict) -> None:
    validate_event(event)
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False)
    # O_APPEND: each write is atomic for lines under the platform pipe buffer,
    # so concurrent sessions never interleave mid-line.
    fd = os.open(str(LEDGER_PATH), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


HIGHWATER_PATH = Path(str(LEDGER_PATH) + ".highwater")


def _read_highwater() -> int:
    """Highest id ever ISSUED, independent of what the file currently holds."""
    try:
        return int(HIGHWATER_PATH.read_text().strip() or 0)
    except Exception:
        return 0


def _write_highwater(n: int) -> None:
    try:
        HIGHWATER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(str(HIGHWATER_PATH) + ".tmp")
        tmp.write_text(str(n))
        os.replace(str(tmp), str(HIGHWATER_PATH))
    except Exception:
        pass


def has_done_gate_proof(rec: dict) -> bool:
    """True iff `rec` has at least one proof event that COUNTS toward the
    `status done` hard-proof gate (A3, L-0763 hub audit, 2026-09-22).

    `rec["proof"]` (built by fold(), unfiltered) may contain `metric-claim`/
    `metric-claim-verdict` entries -- a claim ABOUT a metric, or a scorecard
    verdict computed from one, is not evidence the underlying work actually
    happened. Those types are excluded here; every other type (file, report,
    commit, url, deploy, plan, gate, playtest, ...) counts, same as before
    this fix. A non-dict proof entry (the one legacy shape seen in the real
    ledger, pre-dating the typed `{type, ref}` convention `ledger proof`
    always writes) counts as real, since it cannot be a metric-claim -- only
    `ledger proof` ever writes those, always as a dict.
    """
    for p in rec.get("proof", []):
        if isinstance(p, dict):
            if p.get("type") not in DONE_GATE_EXCLUDED_PROOF_TYPES:
                return True
        else:
            return True
    return False


def next_id(events: list[dict]) -> str:
    """Allocate the next id, monotonically, never reusing one.

    FIX (2026-09-07): deriving the next id from max(ids currently in the file)
    silently reuses ids whenever the file gets SHORTER than it once was. The
    ledger is tracked in git, so a checkout, stash, or merge that rewinds it
    drops the high ids, and the next `add` hands an in-use id to a different
    item. Measured: 8 ids carried two unrelated items each - L-0149 held both a
    hook bug and the operator's Mediavine ask, L-0161 held a rankings plan and a
    report-gate item. The collisions were HOURS apart, not seconds, which rules
    out a lock race (the flock here is correct and stays). The surface the operator
    actually reads renders the CREATED title, so a reused id shows him the
    wrong task on his own open-item list.

    A sidecar high-water mark records the highest id ever issued. It only ever
    moves up, so a rewound ledger cannot cause reuse. Callers already hold
    ledger_lock() across read-next_id-append, so this read-modify-write is
    protected by the same lock.
    """
    max_n = 0
    for e in events:
        eid = e.get("id", "")
        if eid.startswith("L-"):
            try:
                n = int(eid[2:])
                max_n = max(max_n, n)
            except ValueError:
                continue
    max_n = max(max_n, _read_highwater())
    new_n = max_n + 1
    _write_highwater(new_n)
    return f"L-{new_n:04d}"


def normalize_status(value):
    value = str(value or "").lower().replace("_", "-")
    return {
        "working": "active", "in-progress": "active", "planning": "active",
        # Tolerant reading (rename, 2026-09-23): any status literally
        # recorded as "needs-operator" in an existing ledger.jsonl line folds
        # to the current name, never written under the old name again.
        "needs-operator": "needs-operator",
    }.get(value, value)


def normalize_asked_by(value):
    """Tolerant reading (rename, 2026-09-23): an existing ledger line may
    still carry the old literal "operator" asked_by value; fold it to the
    current generic role so callers never have to special-case the old
    spelling."""
    return _LEGACY_ASKED_BY_ALIASES.get(str(value or "").lower(), value)


def validate_event(event):
    if not isinstance(event, dict) or not str(event.get("id", "")).startswith("L-"):
        raise ValueError("Ledger event requires an id")
    if event.get("event") not in {"created", "status", "proof", "ack", "surfaced", "note", "decision", "owner", "reviewed"}:
        raise ValueError("Use canonical ledger events; legacy forms are read-only")
    if event.get("event") == "status" and event.get("status") not in VALID_STATUSES:
        raise ValueError("Invalid ledger status")
    if event.get("event") == "created" and not event.get("title"):
        raise ValueError("Created event requires title")
    if event.get("event") == "proof" and not event.get("proof"):
        raise ValueError("Proof event requires evidence")
    if event.get("event") in {"note", "decision", "reviewed"} and not event.get("note"):
        raise ValueError("Note/decision/reviewed requires text")
    if event.get("event") == "owner" and not event.get("owner"):
        raise ValueError("Owner event requires owner")


def fold(events: list[dict]) -> dict[str, dict]:
    """Read canonical and legacy events without rewriting history or guessing closure.

    Ambiguous cross-project/reused IDs remain visible under schema_issues.
    Unknown events never advance updated_at. Decisions do not imply completion.
    """
    records = {}
    for e in events:
        eid = e.get("id")
        if not eid:
            continue
        rec = records.setdefault(eid, {
            "id": eid, "title": "", "status": "open", "asked_on": None,
            "asked_by": "hub", "owner": "unowned", "trigger": "", "project": None,
            "proof": [], "last_surfaced": None, "operator_ack": False,
            "created_at": None, "updated_at": None, "notes": [], "decisions": [],
            "schema_issues": [], "reviewed_upto": 0,
        })
        ts, ev = e.get("ts"), e.get("event")
        text = e.get("note") or e.get("text") or e.get("title") or e.get("detail")
        if ev == "created" and rec["title"] and e.get("title") != rec["title"]:
            # Preserve the existing last-created identity convention; do not attach
            # an older colliding task's completion/proof to the new task.
            rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "reused id; older task remains in raw history"})
            rec.update(title="", status="open", project=None, proof=[], notes=[], decisions=[],
                       operator_ack=False, created_at=None, updated_at=None, owner="unowned")
        if rec["project"] and e.get("project") and rec["project"] != e["project"]:
            rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "cross-project id; event retained in raw ledger, not applied"})
            continue
        if ev in ("created", "ask"):
            title = e.get("title") or e.get("text") or ""
            if rec["title"] and title != rec["title"]:
                rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "reused id with different title"})
                continue
            rec["title"] = title
            rec["created_at"] = rec["created_at"] or ts
            for field in ("asked_by", "asked_on", "owner", "trigger", "project"):
                if e.get(field) is not None:
                    rec[field] = normalize_asked_by(e[field]) if field == "asked_by" else e[field]
            if ev == "ask" and normalize_status(e.get("status")) in VALID_STATUSES:
                rec["status"] = normalize_status(e["status"])
        elif ev in ("status", "done", "needs_operator", "needs_operator") or (ev is None and e.get("status")):
            status = normalize_status(e.get("status") or {
                "done": "done",
                # "needs_operator" is the old (pre-rename) event-name spelling;
                # "needs_operator" is the current one. Both fold to the same
                # current status name -- see normalize_status()'s own
                # "needs-operator" alias for the equivalent literal-status form.
                "needs_operator": "needs-operator", "needs_operator": "needs-operator",
            }.get(ev))
            if status not in VALID_STATUSES:
                rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "invalid status"})
                continue
            rec["status"] = status
            if e.get("owner"): rec["owner"] = e["owner"]
            if e.get("proof"): rec["proof"].append(e["proof"])
            if text: rec["notes"].append({"ts": ts, "text": text})
        elif ev == "proof":
            if e.get("proof"): rec["proof"].append(e["proof"])
        elif ev == "ack":
            # Tolerant reading: an old ack event recorded "operator_ack"; a
            # current one records "operator_ack". Either satisfies this.
            rec["operator_ack"] = bool(e.get("operator_ack", e.get("operator_ack", True)))
        elif ev == "surfaced":
            rec["last_surfaced"] = ts
        elif ev == "owner":
            # L-0377(b): the only way to reassign ownership after `add` --
            # `claim`/`release`/`reassign` in ./ledger all emit this event.
            # Deliberately independent of `status`: who holds a ticket and
            # whether anyone is actively working it (`status active`, which
            # already has its own claimed_by/claimed_at plumbing) are two
            # different questions and must be free to move separately.
            if e.get("owner"): rec["owner"] = e["owner"]
        elif ev == "reviewed":
            # L-0791: a human-reviewed ack of this item's schema_issues so far.
            # Marks by position in the append-only file, not by timestamp, so
            # any issue a LATER event appends lands past the mark and re-flags.
            # History is untouched (schema_issues keeps every entry), and it
            # deliberately does not advance updated_at: reviewing old ledger
            # shape must never silence the 48h-stale nag on real work.
            rec["reviewed_upto"] = len(rec["schema_issues"])
            if text: rec["notes"].append({"ts": ts, "text": text})
            continue
        elif ev in ("note", "updated", "answered", "decided", "decision", "closed"):
            if text: rec["notes"].append({"ts": ts, "text": text})
            if ev in ("answered", "decided", "decision") and text:
                rec["decisions"].append({"ts": ts, "text": text})
            if e.get("proof"): rec["proof"].append(e["proof"])
            if ev == "closed":
                rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "closed is ambiguous: done or dropped; explicit status needed"})
            if ev == "answered" and rec["status"] == "needs-operator":
                rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "answer retained; explicit next status needed"})
        else:
            rec["schema_issues"].append({"ts": ts, "event": ev, "reason": "unsupported event"})
            continue
        if ts: rec["updated_at"] = ts
    return records


def is_stale(rec: dict) -> bool:
    if rec["status"] not in ("open", "active"):
        return False
    updated = rec.get("updated_at")
    if not updated:
        return False
    return datetime.now(timezone.utc) - parse_ts(updated) > timedelta(hours=STALE_HOURS)


def needs_you(rec: dict) -> bool:
    """True only when the operator's own input is genuinely required to proceed.

    L-0377(a): this used to also fire for status == "done" with
    asked_by == "operator" and no `ack` event -- the rubber-stamp rule
    OPERATOR.md abolished 2026-09-15 ("finished internal work is not a
    decision"). bin/status's classify() already dropped that bucket
    (commit 62fa024, L-0372); this brings ledger_lib's needs_you() /
    banner_lines() / surface_lines() -- which back `ledger banner`,
    `ledger surface`, and the UserPromptSubmit hook that runs on every
    prompt in every live session -- into line with the same rule. An
    `ack` is still recorded when it happens (rec["operator_ack"]), it just
    never gates this or any other display bucket again.
    """
    return rec["status"] == "needs-operator"


_IMPORTED_NOTE_TITLE_RE = re.compile(
    # optional leading board status word ("ACTIVE 2026-09-06 · hub · ..."),
    # which ledger-migrate's extract_title() left on some lines
    r"^(?:[A-Z]{3,10}\s+)?\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2})?\s*·"
)


def is_imported_board_note(rec: dict) -> bool:
    """True for a ticket that ledger-migrate folded in from an old board.md
    line (one-shot run, 2026-09-06 -- see ./ledger-migrate), not one anyone
    created live with `ledger add`.

    L-0377(c): ledger-migrate's extract_title() only strips the emoji status
    marker from a board.md line, never the "YYYY-MM-DD[ HH:MM] · project ·"
    prefix a hand-written board line carries -- so every migrated title has
    that shape verbatim, and a live `ledger add` title (free text, no fixed
    shape) essentially never does. These are a snapshot of board prose from
    days-to-weeks before the fold ran, not a task anyone is tracking; their
    created_at and updated_at are frozen at migration time because nothing
    real ever happens to them again, which is exactly what made
    surface_lines()'s 48h-stale check nag about them forever.

    Measured against the live ledger 2026-09-21 (758 folded records): 122
    match, 24 of them open/active -- and those 24 were precisely the set
    `ledger surface` was printing as perpetual "STALE 48h+ ... still owned
    by hub?" lines. One known false negative (a migrated line whose date
    used a "->" range instead of a plain date, e.g. L-0106) is safe by
    construction: it just keeps nagging rather than going silently missed,
    and it was already `done` so it never reached the STALE check anyway.
    See hub/reports/2026-09-21-L0361-closing-a-ticket.md for the full
    census and the by-hand spot check.

    Narrow by design: this only ever gates the STALE-48h+ line in
    surface_lines() below. It never changes status, never edits or removes
    an event -- the ledger stays append-only either way -- and it leaves
    needs-operator handling for imported lines untouched.
    """
    return bool(_IMPORTED_NOTE_TITLE_RE.match(rec.get("title") or ""))


def unreviewed_issues(rec: dict) -> list[dict]:
    """The schema_issues that still need a human look -- what the banner counts.

    L-0791: a done/dropped item's historic shape problems are settled by its
    status, so they never count. On a live item, only issues recorded after
    its last `reviewed` event count. fold() still records every issue; this
    only decides what is counted.
    """
    if rec.get("status") in ("done", "dropped"):
        return []
    return rec.get("schema_issues", [])[rec.get("reviewed_upto", 0):]


MAX_SURFACE = 5


def banner_lines(records: dict[str, dict]) -> list[str]:
    """The two-line status banner: `Open: N * Needs you: M` then WORKING/IDLE.

    Shared by `ledger banner` and the UserPromptSubmit hook so both render
    from the exact same fold, never composed separately from memory.
    """
    items = list(records.values())
    open_n = sum(1 for r in items if r["status"] in ("open", "active", "needs-operator"))
    needs_you_n = sum(1 for r in items if needs_you(r))
    working_n = sum(1 for r in items if r["status"] == "active")
    lines = [f"Open: {open_n} · Needs you: {needs_you_n}"]
    lines.append(f"WORKING: {working_n}" if working_n > 0 else "IDLE")
    issues = sum(bool(unreviewed_issues(r)) for r in items)
    if issues: lines.append(f"Ledger history needs review: {issues} IDs; counts are provisional")
    return lines


def surface_lines(records: dict[str, dict], limit: int = MAX_SURFACE) -> list[str]:
    """Items the nag policy says to raise now, one line each, capped at `limit`.

    Priority order: needs-operator, then done-but-unacked operator asks, then
    48h-stale open/active items, then operator asks never started.
    """
    items = sorted(records.values(), key=lambda r: r["id"])
    printed: set[str] = set()
    lines: list[str] = []

    for r in items:
        if r["status"] == "needs-operator" and r["id"] not in printed:
            lines.append(f"NEEDS-OPERATOR {r['id']}: {r['title']} (owner: {r['owner']})")
            printed.add(r["id"])

    # L-0377(a): no CONFIRM/rubber-stamp block here -- a done ticket with no
    # `ack` is not a pending-on-the operator item (see needs_you()'s docstring).

    for r in items:
        if r["id"] in printed:
            continue
        if is_stale(r) and not is_imported_board_note(r):
            lines.append(f"STALE 48h+ {r['id']}: {r['title']} - still owned by {r['owner']}?")
            printed.add(r["id"])

    for r in items:
        if r["id"] in printed:
            continue
        if r["asked_by"] == "operator" and r["status"] == "open":
            lines.append(f"NEVER-STARTED {r['id']}: {r['title']} (asked {r['asked_on']})")
            printed.add(r["id"])

    return lines[:limit]
