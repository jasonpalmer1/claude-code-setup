#!/usr/bin/env python3
"""Token ledger: parse a Claude Code session transcript and write a usage row.

Pure parsing — no model call, costs nothing to run. Sums tokens per model from
each assistant message's usage block (including subagent transcripts, which
live in a sibling directory), estimates cost with the price table below, and
writes one Markdown table row to token_ledger.md per session.

A session that gets resumed and ends again later re-parses its (now longer)
transcript from scratch and UPDATES its existing row in place (same line,
fresh cumulative totals) instead of appending a duplicate — anything that
reads this ledger should be able to assume one row per session id.

The write is atomic (temp file + os.replace) so a crash or kill mid-write
can't truncate or corrupt the ledger. Failures are caught and appended to
ERROR_LOG instead of raising, so the SessionEnd hook never blocks on this.

Invoked by the SessionEnd hook with the transcript path on stdin (hook JSON) or
as argv[1]. Safe to run manually:  token-ledger.py <transcript.jsonl>

Peer-session spawn-tree rollup (optional, self-contained). If your harness
lets you spawn separate peer sessions (see "Peer sessions" in
CLAUDE.md.template), a per-session cost view can hide the real total: three
peers at $20 each look fine individually but are $60 together, and none of
them alone crosses a per-session threshold. If a peer-spawning session sets
PARENT_ENV_VAR (below) in the child's environment to the parent session's id,
this script records that pointer as an invisible trailing HTML comment on the
row — a comment, not a new table column, so a plain session's row is
byte-identical to before this feature existed, and it's a no-op unless you
actually set the env var. On sessions that have it set, the whole ledger is
walked to find that session's tree (root + every descendant reachable via the
parent pointers) and, if the tree's total spend is large with one tier
dominating the root's own spend, a line is appended to TREE_ALARMS.
"""
import json, sys, os, re, glob, datetime, traceback

# Customize: where to append the usage table. If you use the tiered-memory
# layout from CLAUDE.md.template, point this at your memory dir instead, e.g.
# os.path.expanduser("~/.claude/projects/-Users-<you>/memory/token_ledger.md")
LEDGER = os.path.expanduser("~/.claude/token_ledger.md")

# Where write/parse failures get logged instead of failing silently.
ERROR_LOG = os.path.expanduser("~/.claude/hub/hook-errors.log")

# Set this env var (to the parent session's id) when launching a peer session,
# if you want spawn-tree cost rollup. Name it whatever your peer-spawn
# mechanism already uses, or set it yourself in the launch command.
PARENT_ENV_VAR = "CLAUDE_PEER_PARENT_ID"

# Spawn-tree cost alarms get appended here — informational, never blocking.
TREE_ALARMS = os.path.expanduser("~/.claude/hub/spawn-tree-alarms.log")

# Alarm thresholds: a tree over TREE_ALARM_MIN_COST with one tier carrying
# more than TREE_ALARM_SHARE of it is worth a look. Tune to your own ledger.
TREE_ALARM_MIN_COST = 50.0
TREE_ALARM_SHARE = 0.60

# $ per million tokens: (input, output, cache_write_5m=1.25x, cache_read=0.1x)
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
            # Per-line fault isolation: one structurally-bad entry (string-typed
            # token counts, non-dict JSON) must skip, not zero the whole session.
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

def build_row(tp, sid_full):
    """Re-parse the FULL transcript (+ any subagent transcripts) from scratch.
    For a resumed session this naturally recomputes cumulative totals across
    the whole (now longer) history — not just the new increment."""
    acc, models = {}, set()
    accumulate(tp, acc, models)
    # Subagent usage lives in a sibling per-session dir: <session-id>/subagents/*.jsonl
    sub_glob = os.path.join(os.path.dirname(tp), sid_full, "subagents", "*.jsonl")
    for sub in sorted(glob.glob(sub_glob)):
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
    return sess, row, cost

def write_row(sess, row):
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

def parse_ledger_rows(path):
    """Every data row in the ledger, decoded back into a dict, including its
    per-tier cost breakdown (parsed from the always-present "By model"
    column, so this works even for rows written before the spawn-tree
    feature existed) and its parent pointer, if any (only present on rows
    from sessions that had PARENT_ENV_VAR set). Header/separator lines are
    skipped by shape, not a hardcoded line count."""
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
            tiers = {}
            for tok in parts[9].strip().split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    try:
                        tiers[k] = float(v.lstrip("$"))
                    except Exception:
                        pass
            trailer = parts[10] if len(parts) > 10 else ""
            m_p = re.search(r"parent:(\S+)", trailer)
            parent = m_p.group(1) if m_p else ""
            rows.append({"date": date, "sess": sess, "cost": cost,
                         "parent": parent, "tiers": tiers})
    return rows

def check_tree_alarm(sess, date):
    """Roll up spend across this session's whole spawn tree (root + every
    descendant reachable via the parent pointers written into the ledger) and
    alarm if the tree's total spend is large with one tier — whichever tier
    the ROOT session leaned on most, in its own row — carrying most of it.
    The root's own dominant tier is read straight from its own "By model"
    column, so this works whether or not the root session itself was ever
    tagged with a parent (roots usually aren't). Silently no-ops if the root
    has no row of its own yet."""
    rows = parse_ledger_rows(LEDGER)
    by_id = {r["sess"]: r for r in rows}

    # Walk up parent pointers to the root. Cycle-safe via `seen` — a
    # malformed chain can't spin this forever.
    seen, cur = set(), sess
    while cur in by_id and by_id[cur]["parent"] and cur not in seen:
        seen.add(cur)
        cur = by_id[cur]["parent"]
    root = cur
    if root not in by_id:
        return

    # Collect the whole tree by walking parent pointers forward from root.
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

    root_tiers = by_id[root]["tiers"]
    if not root_tiers:
        return
    dominant = max(root_tiers, key=root_tiers.get)
    tree_total = sum(by_id[i]["cost"] for i in tree_ids)
    if tree_total <= TREE_ALARM_MIN_COST:
        return
    dominant_cost = root_tiers[dominant]
    if dominant_cost / tree_total <= TREE_ALARM_SHARE:
        return

    tag = f"spawn-tree root={root}"
    existing = ""
    if os.path.exists(TREE_ALARMS):
        with open(TREE_ALARMS) as f:
            existing = f.read()
    if tag in existing:
        return  # already flagged this tree
    try:
        os.makedirs(os.path.dirname(TREE_ALARMS), exist_ok=True)
        with open(TREE_ALARMS, "a") as f:
            f.write(
                f"{date} {tag} dominant={dominant} ${dominant_cost:.0f} = "
                f"{dominant_cost / tree_total * 100:.0f}% of ${tree_total:.0f} "
                f"across {len(tree_ids)} session(s) — spawn-tree cost worth a look?\n"
            )
    except Exception:
        log_error("spawn-tree-alarm write: " + traceback.format_exc(limit=2))

def main():
    tp = read_transcript_path()
    if not tp or not os.path.isfile(tp):
        return
    try:
        sid_full = os.path.basename(tp).replace(".jsonl", "")
        built = build_row(tp, sid_full)
        if built is None:
            return
        sess, row, cost = built
        date = row.split("|")[1].strip()

        # Peer-session tagging — additive, invisible to the table structure.
        # Only a session with PARENT_ENV_VAR set gets a trailer at all; a
        # plain session's row is untouched, byte for byte.
        parent_raw = os.environ.get(PARENT_ENV_VAR, "").strip()
        if parent_raw:
            row = row.rstrip("\n") + f" <!-- parent:{parent_raw[:8]} -->\n"

        write_row(sess, row)

        try:
            if parent_raw:
                check_tree_alarm(sess, date)
        except Exception:
            log_error("spawn-tree-alarm: " + traceback.format_exc(limit=2))
    except Exception:
        log_error(f"{tp}: {traceback.format_exc(limit=4)}")

if __name__ == "__main__":
    main()
