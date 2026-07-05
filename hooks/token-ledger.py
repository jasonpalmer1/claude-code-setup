#!/usr/bin/env python3
"""Token ledger: parse a Claude Code session transcript and append a usage row.

Pure parsing — no model call, costs nothing to run. Sums tokens per model from
each assistant message's usage block (including subagent transcripts, which
live in a sibling directory), estimates cost with the price table below, and
appends one Markdown table row to token_ledger.md.

Invoked by the SessionEnd hook with the transcript path on stdin (hook JSON) or
as argv[1]. Safe to run manually:  token-ledger.py <transcript.jsonl>
"""
import json, sys, os, glob, datetime

# Customize: where to append the usage table. If you use the tiered-memory
# layout from CLAUDE.md.template, point this at your memory dir instead, e.g.
# os.path.expanduser("~/.claude/projects/-Users-<you>/memory/token_ledger.md")
LEDGER = os.path.expanduser("~/.claude/token_ledger.md")

# $ per million tokens: (input, output, cache_write_5m=1.25x, cache_read=0.1x)
PRICES = {
    "opus":   (5.00, 25.00, 6.25, 0.50),
    "sonnet": (3.00, 15.00, 3.75, 0.30),
    "haiku":  (1.00,  5.00, 1.25, 0.10),
}

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
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            t = tier(msg.get("model", ""))
            if not t:
                continue
            models.add(msg.get("model"))
            a = acc.setdefault(t, [0, 0, 0, 0])
            a[0] += usage.get("input_tokens", 0) or 0
            a[1] += usage.get("output_tokens", 0) or 0
            a[2] += usage.get("cache_creation_input_tokens", 0) or 0
            a[3] += usage.get("cache_read_input_tokens", 0) or 0

def main():
    tp = read_transcript_path()
    if not tp or not os.path.isfile(tp):
        return
    # accumulate per tier: [input, output, cache_write, cache_read]
    acc, models = {}, set()
    accumulate(tp, acc, models)
    # Subagent usage lives in a sibling per-session dir: <session-id>/subagents/*.jsonl
    sid_full = os.path.basename(tp).replace(".jsonl", "")
    sub_glob = os.path.join(os.path.dirname(tp), sid_full, "subagents", "*.jsonl")
    for sub in sorted(glob.glob(sub_glob)):
        accumulate(sub, acc, models)
    if not acc:
        return
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
    new = not os.path.exists(LEDGER)
    if not new:
        # dedupe by session id: drop any prior row for this session before appending
        with open(LEDGER) as f:
            lines = [l for l in f if f"| {sess} |" not in l]
        with open(LEDGER, "w") as f:
            f.writelines(lines)
    with open(LEDGER, "a") as f:
        if new:
            f.write("# Token Ledger\n\n")
            f.write("Per-session usage, appended by the SessionEnd hook (`token-ledger.py`). "
                    "Pure parsing of the transcript — no model call. Review with `/tokens`.\n\n")
            f.write("| Date | Session | Input | Output | CacheWrite | CacheRead | HitRate | Est.Cost | By model |\n")
            f.write("|------|---------|-------|--------|-----------|-----------|---------|----------|----------|\n")
        f.write(row)

if __name__ == "__main__":
    main()
