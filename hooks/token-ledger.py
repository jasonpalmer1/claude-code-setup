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
"""
import json, sys, os, glob, datetime, traceback

# Customize: where to append the usage table. If you use the tiered-memory
# layout from CLAUDE.md.template, point this at your memory dir instead, e.g.
# os.path.expanduser("~/.claude/projects/-Users-<you>/memory/token_ledger.md")
LEDGER = os.path.expanduser("~/.claude/token_ledger.md")

# Where write/parse failures get logged instead of failing silently.
ERROR_LOG = os.path.expanduser("~/.claude/hub/hook-errors.log")

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
    return sess, row

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

def main():
    tp = read_transcript_path()
    if not tp or not os.path.isfile(tp):
        return
    try:
        sid_full = os.path.basename(tp).replace(".jsonl", "")
        built = build_row(tp, sid_full)
        if built is None:
            return
        sess, row = built
        write_row(sess, row)
    except Exception:
        log_error(f"{tp}: {traceback.format_exc(limit=4)}")

if __name__ == "__main__":
    main()
