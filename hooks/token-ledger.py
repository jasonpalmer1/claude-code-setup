#!/usr/bin/env python3
"""Token ledger: parse a Claude Code session transcript and write a usage row.

Pure parsing — no model call, costs nothing to run. Sums tokens per model from
each assistant message's usage block, estimates cost with the price table below,
and writes one Markdown table row to token_ledger.md per session.

A session that is resumed and ends again later re-parses its (now longer)
transcript from scratch and UPDATES its existing row in place (same line,
fresh cumulative totals) rather than appending a duplicate — /tokens and other
consumers assume exactly one row per session id.

The write is atomic (temp file + os.replace) so a crash or kill mid-write can
never truncate or corrupt the live ledger. Any failure is caught and appended
to ERROR_LOG instead of raising, so the SessionEnd hook never blocks on this.

Invoked by the SessionEnd hook with the transcript path on stdin (hook JSON) or
as argv[1]. Safe to run manually:  token-ledger.py <transcript.jsonl>

Spawn-tree rollup (2026-08-09): the delegation alarm above is per-session, so
splitting one job across `fleet spawn` peers evades it. When CLAUDE_FLEET_PARENT
is present in the environment (every fleet-launched session has it — set by
fleet/run), this session's row gets an invisible trailing HTML comment
recording {parent, main_tier}; a comment, not a new table column, so a plain
(non-fleet) session's row is BYTE-IDENTICAL to before — the whole feature is
inert unless CLAUDE_FLEET_PARENT is set. After writing the row, the whole
ledger is walked to find this session's tree (root + every descendant) and,
if the tree's total is >$50 with the root's own main-loop tier at >60% of
that total, a "spawn-tree" line is appended to delegation-alarms.log.
"""
import json, sys, os, re, glob, datetime, traceback

_HOME_SLUG = os.path.expanduser("~").replace(os.sep, "-")
LEDGER = os.path.expanduser(
    "~/.claude/projects/%s/memory/token_ledger.md" % _HOME_SLUG
)
ALARMS = os.path.expanduser("~/.claude/hub/delegation-alarms.log")
ERROR_LOG = os.path.expanduser("~/.claude/hub/hook-errors.log")
PROJECTS = os.path.expanduser("~/.claude/projects")

# $ per million tokens: (input, output, cache_write_5m=1.25x, cache_read=0.1x)
# VERIFIED 2026-09-01 against the official Anthropic rate card (claude-api skill,
# cached 2026-06-24): fable $10/$50, opus $5/$25, sonnet $3/$15, haiku 4.5 $1/$5
# per MTok, with cache-write = 1.25x input and cache-read = 0.1x input. Every row
# below matches. This closes the open item from the 2026-09-01 spend analysis,
# where the fable row alone determined 63% of the day's estimate and had never
# been checked against the real card — it is right, so that headline stands.
# One caveat that only affects days before 2026-09-01: Sonnet 5 carried an intro
# price of $2/$10 through 2026-08-31, so sonnet lines on 08-29..08-31 are slightly
# OVERSTATED here (immaterial — sonnet+haiku were ~2% of that period's spend).
# These are list API prices, an estimate proxy — never a subscription bill.
PRICES = {
    "fable":  (10.00, 50.00, 12.50, 1.00),
    "opus":   (5.00, 25.00, 6.25, 0.50),
    "sonnet": (3.00, 15.00, 3.75, 0.30),
    "haiku":  (1.00,  5.00, 1.25, 0.10),
}

HEADER = [
    "# Token Ledger\n",
    "\n",
    "Per-session usage, appended by the SessionEnd hook (`token-ledger.py`). "
    "Pure parsing of the transcript — no model call. Review with `/tokens`.\n",
    "\n",
    "| Date | Session | Input | Output | CacheWrite | CacheRead | HitRate | Est.Cost | By model |\n",
    "|------|---------|-------|--------|-----------|-----------|---------|----------|----------|\n",
]

def tier(model: str):
    m = (model or "").lower()
    for key in PRICES:
        if key in m:
            return key
    return None

def read_transcript_path() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    data = sys.stdin.read()
    if not data.strip():
        return ""
    try:
        return json.loads(data).get("transcript_path", "")
    except Exception:
        return data.strip()

def accumulate(path, acc, models):
    with open(path, errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            # Per-line fault isolation (2026-07-22 audit): one structurally-bad
            # entry must skip, not zero the whole session's row + alarm.
            try:
                msg = obj.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                t = tier(msg.get("model", ""))
                if not t:
                    continue
                models.add(msg.get("model"))
                a = acc.setdefault(t, [0, 0, 0, 0])
                a[0] += int(usage.get("input_tokens") or 0)
                a[1] += int(usage.get("output_tokens") or 0)
                a[2] += int(usage.get("cache_creation_input_tokens") or 0)
                a[3] += int(usage.get("cache_read_input_tokens") or 0)
            except Exception:
                continue

def log_error(msg):
    # Best-effort diagnostics — must never itself raise or block the hook.
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(ERROR_LOG, "a") as f:
            f.write(f"[{ts}] token-ledger.py: {msg}\n")
    except Exception:
        pass

def agent_transcripts(tp, sid_full):
    """Every subagent transcript belonging to this session, wherever it landed.

    Fixed 2026-07-24 — the old glob was `<dir(tp)>/<sid>/subagents/*.jsonl`,
    one level deep in one project dir, and silently dropped two real layouts:

      * Workflow agents nest deeper:  <sid>/subagents/workflows/wf_*/agent-*.jsonl
      * A session that cd's into a project gets a SECOND dir keyed by that
        project's slug: ~/.claude/projects/<other-slug>/<sid>/...

    So every Workflow-driven session under-reported its agent spend. Measured
    on 9294ff22: 105 workflow agents / $104 missing. That skewed the delegation
    alarm PESSIMISTIC (agents are usually the cheap tiers, so dropping them
    inflates the main-loop share) — the ledger made delegation look worse than
    it was. Recursive + all project dirs, deduped by realpath.
    """
    main_rp = os.path.realpath(tp)
    seen, out = set(), []
    for root in glob.glob(os.path.join(PROJECTS, "*", sid_full)):
        for p in glob.iglob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            # journal.jsonl is the workflow's own bookkeeping — no usage blocks
            if os.path.basename(p) == "journal.jsonl":
                continue
            rp = os.path.realpath(p)
            if rp == main_rp or rp in seen:
                continue
            seen.add(rp)
            out.append(rp)
    return sorted(out)

def build_row(tp, sid_full):
    """Re-parse the FULL transcript (+ any subagent transcripts) from scratch.
    For a resumed session this naturally recomputes cumulative totals across
    the whole (now longer) history — not just the new increment."""
    acc, models = {}, set()
    # Main transcript first, kept separately: whichever tier dominates HERE is
    # the main-loop model, which is what the delegation alarm measures. Derived,
    # never hardcoded — the main-loop slot changes (opus -> fable 2026-07-19,
    # fable -> opus 2026-07-24) and the alarm must not care which model holds it.
    main_acc = {}
    accumulate(tp, main_acc, models)
    for t, v in main_acc.items():
        acc[t] = list(v)
    for sub in agent_transcripts(tp, sid_full):
        accumulate(sub, acc, models)
    if not acc:
        return None
    tot_in = tot_out = tot_cw = tot_cr = cost = 0
    per_tier_cost = {}
    for t, (i, o, cw, cr) in acc.items():
        pi, po, pcw, pcr = PRICES[t]
        c = (i*pi + o*po + cw*pcw + cr*pcr) / 1_000_000
        per_tier_cost[t] = c
        cost += c
        tot_in += i; tot_out += o; tot_cw += cw; tot_cr += cr
    # cache hit rate = cache_read / (cache_read + cache_creation + input)
    denom = tot_cr + tot_cw + tot_in
    hit = (tot_cr / denom * 100) if denom else 0
    # use transcript mtime, not today — correct for backfills; identical for live runs
    date = datetime.date.fromtimestamp(os.path.getmtime(tp)).isoformat()
    sess = sid_full[:8]
    mix = " ".join(
        f"{t}=${per_tier_cost[t]:.2f}" for t in sorted(per_tier_cost)
    )
    row = (
        f"| {date} | {sess} | {tot_in:,} | {tot_out:,} | {tot_cw:,} | "
        f"{tot_cr:,} | {hit:.0f}% | ${cost:.2f} | {mix} |\n"
    )
    # Costliest tier in the MAIN transcript = the main-loop model this session ran on.
    main_cost = {
        t: sum(n * p for n, p in zip(v, PRICES[t])) / 1_000_000
        for t, v in main_acc.items()
    }
    main_tier = max(main_cost, key=main_cost.get) if main_cost else None
    return sess, row, cost, per_tier_cost, date, main_tier

def parse_ledger_rows(path):
    """Every data row in the ledger, decoded back into a dict. Tolerant of rows
    written before this feature existed (they just have no parent/main_tier —
    treated as roots) and of the header/separator lines (skipped by column-0
    sniffing, not a hardcoded line count, since the header wording can change)."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        for line in f:
            if not line.startswith("| "):
                continue
            parts = line.split("|")
            if len(parts) < 10:
                continue
            date = parts[1].strip()
            sess = parts[2].strip()
            if date in ("Date", "") or date.startswith("-") or sess.startswith("-"):
                continue  # header or |---|---| separator
            try:
                cost = float(parts[8].strip().lstrip("$"))
            except Exception:
                continue
            mix = parts[9].strip()
            trailer = parts[10] if len(parts) > 10 else ""
            m_p = re.search(r"parent:(\S+)", trailer)
            m_t = re.search(r"main:(\S+)", trailer)
            parent = m_p.group(1) if m_p else ""
            main_tier = m_t.group(1) if m_t else None
            tiers = {}
            for tok in mix.split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    try:
                        tiers[k] = float(v.lstrip("$"))
                    except Exception:
                        pass
            rows.append({"date": date, "sess": sess, "cost": cost, "parent": parent,
                         "main_tier": main_tier, "tiers": tiers})
    return rows


def check_tree_alarm(sess, date, ledger_path=None, alarms_path=None):
    """Roll up spend across this session's whole spawn tree (root + every
    descendant reachable via the parent pointers written into the ledger) and
    alarm if the ROOT's own main-loop tier is carrying >60% of a >$50 tree
    total — the same shape as the per-session alarm, one level up. Root's
    tier is used (not each session's own) because the thing being checked is
    "is the top tier directing while peers execute", and only the root has a
    real main-loop role; a peer with no rows of its own can't be scored, so
    this silently no-ops rather than guessing."""
    ledger_path = ledger_path or LEDGER
    alarms_path = alarms_path or ALARMS
    rows = parse_ledger_rows(ledger_path)
    by_id = {r["sess"]: r for r in rows}

    # Walk up parent pointers to the root. Cycle-safe (a malformed/adversarial
    # chain can't spin this forever) via `seen`.
    seen, cur = set(), sess
    while cur in by_id and by_id[cur]["parent"] and cur not in seen:
        seen.add(cur)
        cur = by_id[cur]["parent"]
    root = cur
    if root not in by_id:
        return  # root has no row of its own yet — nothing to score against

    # Reverse BFS from root over parent pointers to collect the whole tree.
    children = {}
    for r in rows:
        if r["parent"]:
            children.setdefault(r["parent"], []).append(r["sess"])
    tree_ids, frontier = set(), [root]
    while frontier:
        nid = frontier.pop()
        if nid in tree_ids or nid not in by_id:
            continue
        tree_ids.add(nid)
        frontier.extend(children.get(nid, []))

    main_tier = by_id[root]["main_tier"]
    if not main_tier:
        return
    tree_total = sum(by_id[i]["cost"] for i in tree_ids)
    if tree_total <= 50:
        return
    main_cost = sum(by_id[i]["tiers"].get(main_tier, 0.0) for i in tree_ids)
    if main_cost / tree_total <= 0.60:
        return

    tag = f"spawn-tree root={root}"
    existing = ""
    if os.path.exists(alarms_path):
        with open(alarms_path) as f:
            existing = f.read()
    if tag in existing:
        return  # already flagged this tree
    with open(alarms_path, "a") as f:
        f.write(
            f"{date} {tag} main-loop={main_tier} ${main_cost:.0f} = "
            f"{main_cost / tree_total * 100:.0f}% of ${tree_total:.0f} across "
            f"{len(tree_ids)} session(s) — spawn-tree evading the per-session alarm?\n"
        )


def write_row(sess, row):
    import fcntl
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_row_unlocked(sess, row)


def _write_row_unlocked(sess, row):
    """Update the row for `sess` in place if it already exists (same line,
    refreshed totals); otherwise append a new row. Written atomically via
    temp file + os.replace so a mid-write failure can't truncate the ledger."""
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            lines = f.readlines()
        if not lines:
            lines = list(HEADER)
    else:
        lines = list(HEADER)
    marker = f"| {sess} |"
    idx = next((i for i, l in enumerate(lines) if marker in l), None)
    if idx is not None:
        lines[idx] = row          # resumed/re-ended session — refresh in place
    else:
        lines.append(row)         # brand-new session — append
    tmp = f"{LEDGER}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, LEDGER)        # atomic on POSIX — never a truncated ledger

def main():
    tp = read_transcript_path()
    if not tp or not os.path.isfile(tp):
        return
    try:
        sid_full = os.path.basename(tp).replace(".jsonl", "")
        built = build_row(tp, sid_full)
        if built is None:
            return
        sess, row, cost, per_tier, date, main_tier = built

        # Fleet tracking comment — additive, invisible to the table structure.
        # Only a session launched via fleet spawn has CLAUDE_FLEET_PARENT in
        # its environment at all; a plain session's row is untouched, byte
        # for byte, same as before this feature existed.
        fleet_tracked = "CLAUDE_FLEET_PARENT" in os.environ
        if fleet_tracked:
            raw_parent = os.environ.get("CLAUDE_FLEET_PARENT", "").strip()
            parent_id = "" if raw_parent.lower() in ("", "unknown") else raw_parent[:8]
            row = row.rstrip("\n") + f" <!-- fleet parent:{parent_id} main:{main_tier or ''} -->\n"

        write_row(sess, row)
        # Delegation alarm (split rule, 2026-07-22 audit): an OPS session shouldn't
        # burn >60% of a >$50 session on the main-loop tier. Signal, not a block —
        # BUILD sessions ignore it deliberately. Surfaced at next SessionStart
        # (operator-scorecard.mjs). Deduped by session id so resumes don't re-alarm.
        #
        # MODEL-AGNOSTIC since 2026-07-24: measures whichever tier the main loop
        # actually ran on, not a hardcoded "fable". The rule is about the SHAPE
        # (top tier directing, cheaper tiers executing), not about one model —
        # and it stays true in both directions, including an opus main loop that
        # deliberately spawns fable agents for work that needs that tier.
        try:
            main_c = per_tier.get(main_tier, 0) if main_tier else 0
            # TOKEN_LEDGER_NO_ALARM=1 → recompute rows only. Used when backfilling
            # historical sessions, so a corrected row can't inject a months-old
            # "alarm" that nobody will ever review.
            if os.environ.get("TOKEN_LEDGER_NO_ALARM"):
                return
            if cost > 50 and main_c / cost > 0.60:
                alarms = os.path.expanduser("~/.claude/hub/delegation-alarms.log")
                existing = ""
                if os.path.exists(alarms):
                    with open(alarms) as f:
                        existing = f.read()
                if sess not in existing:
                    with open(alarms, "a") as f:
                        f.write(
                            f"{date} {sess} main-loop={main_tier} ${main_c:.0f} = "
                            f"{main_c / cost * 100:.0f}% of ${cost:.0f} — BUILD or leakage?\n"
                        )
        except Exception:
            log_error("delegation-alarm: " + traceback.format_exc(limit=2))

        try:
            if fleet_tracked and not os.environ.get("TOKEN_LEDGER_NO_ALARM"):
                check_tree_alarm(sess, date)
        except Exception:
            log_error("spawn-tree-alarm: " + traceback.format_exc(limit=2))
    except Exception:
        log_error(f"{tp}: {traceback.format_exc(limit=4)}")

if __name__ == "__main__":
    main()
