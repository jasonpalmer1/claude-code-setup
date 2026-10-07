#!/usr/bin/env python3
"""Live cost meter: warn DURING a session, not after it ends.

Why this exists (2026-08-02): the SessionEnd ledger only prices a session once
it's over, so a session can reach $2,484 with nobody noticing. Five sessions
crossed $1,300 each and 10 sessions accounted for 55% of $23,986 all-time spend.
This fires on every prompt and surfaces the running total once it crosses a
threshold.

PERFORMANCE — this is a per-prompt blocking hook, so it must stay in the tens of
milliseconds even when the transcript is 100 MB. It never re-reads the whole
file: per-file byte offsets and running token totals are cached in STATE_DIR and
each run parses ONLY the bytes appended since last time. JSONL is append-only, so
offset resume is sound. Cost is recomputed from the cached totals, not re-summed
from disk.

Pricing and tier detection are imported from token-ledger.py so there is exactly
one price table on this machine. If that import fails the meter goes silent
rather than guessing at prices.

Fails silent and always exits 0 — a cost warning must never block a prompt.
"""
import json, sys, os, glob, datetime, importlib.util

HOOKS = os.path.expanduser("~/.claude/hooks")
STATE_DIR = os.path.expanduser("~/.claude/hub/cost-meter")
LOG = os.path.expanduser("~/.claude/hub/cost-meter.log")
ERROR_LOG = os.path.expanduser("~/.claude/hub/hook-errors.log")

# Escalating so it informs once and then gets out of the way. Past this list it
# repeats every $500. Tuned to the observed damage: $50 is "this is a real
# session now", $150 is "this is in the top quartile", $500+ is "the top 10".
THRESHOLDS = [50, 150, 300, 500, 750, 1000, 1500, 2000]


def load_ledger():
    """Reuse token-ledger.py's price table + tier detection (hyphenated name)."""
    spec = importlib.util.spec_from_file_location(
        "token_ledger", os.path.join(HOOKS, "token-ledger.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log_error(msg):
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(ERROR_LOG, "a") as f:
            f.write(f"[{ts}] cost-meter.py: {msg}\n")
    except Exception:
        pass


def parse_new(path, offset, acc, tier):
    """Parse only the bytes after `offset`. Returns the new offset.

    Caller guarantees the file has not shrunk (see the reset check in main) —
    a shrink invalidates the cached totals, so it is handled there by wiping
    state entirely rather than here, where clearing one file's contribution
    from a shared accumulator is impossible without double-counting.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return offset
    if size <= offset:
        return offset
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
        new_offset = f.tell()
    text = chunk.decode("utf-8", errors="ignore")
    # A final partial line means the writer is mid-append; rewind to the last
    # complete newline so no record is ever half-parsed or double-counted.
    cut = text.rfind("\n")
    if cut == -1:
        return offset
    new_offset = offset + len(text[: cut + 1].encode("utf-8"))
    for line in text[:cut].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            t = tier(msg.get("model", ""))
            if not t:
                continue
            a = acc.setdefault(t, [0, 0, 0, 0])
            a[0] += int(usage.get("input_tokens") or 0)
            a[1] += int(usage.get("output_tokens") or 0)
            a[2] += int(usage.get("cache_creation_input_tokens") or 0)
            a[3] += int(usage.get("cache_read_input_tokens") or 0)
        except Exception:
            continue
    return new_offset


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        return
    tp = payload.get("transcript_path") or ""
    sid = payload.get("session_id") or ""
    if not tp or not os.path.isfile(tp) or not sid:
        return

    L = load_ledger()
    os.makedirs(STATE_DIR, exist_ok=True)
    statef = os.path.join(STATE_DIR, f"{sid}.json")

    state = {"offsets": {}, "acc": {}, "alerted": 0}
    if os.path.exists(statef):
        try:
            with open(statef) as f:
                state = json.load(f)
        except Exception:
            pass
    acc = {k: list(v) for k, v in state.get("acc", {}).items()}
    offsets = dict(state.get("offsets", {}))

    # Main transcript + every subagent transcript (workflow agents nest deeper
    # and a session that cd's gets a second project dir — agent_transcripts
    # already handles both; that spend was 55% invisible before 2026-07-24).
    paths = [os.path.realpath(tp)]
    try:
        paths += L.agent_transcripts(tp, sid)
    except Exception:
        pass

    # If any tracked file is now SMALLER than its recorded offset it was
    # truncated or replaced, and the cached totals include bytes that no longer
    # exist. There is no way to subtract one file's share back out of a shared
    # accumulator, so wipe everything and re-derive from disk. Rare; correct.
    shrank = False
    for p, off in offsets.items():
        try:
            if os.path.exists(p) and os.path.getsize(p) < int(off):
                shrank = True
                break
        except OSError:
            continue
    if shrank:
        acc, offsets = {}, {}

    for p in paths:
        offsets[p] = parse_new(p, int(offsets.get(p, 0)), acc, L.tier)

    cost = 0.0
    per_tier = {}
    for t, (i, o, cw, cr) in acc.items():
        pi, po, pcw, pcr = L.PRICES[t]
        c = (i * pi + o * po + cw * pcw + cr * pcr) / 1_000_000
        per_tier[t] = c
        cost += c

    state = {"offsets": offsets, "acc": acc, "alerted": state.get("alerted", 0)}

    # Highest threshold crossed so far; only fire when it's higher than the last
    # one announced, so a long session warns a handful of times, not every turn.
    level = 0
    for t in THRESHOLDS:
        if cost >= t:
            level = t
    if cost >= THRESHOLDS[-1]:
        step = int((cost - THRESHOLDS[-1]) // 500)
        level = THRESHOLDS[-1] + step * 500

    fire = level > state["alerted"]
    if fire:
        state["alerted"] = level

    tmp = f"{statef}.tmp-{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, statef)

    if not fire:
        return

    mix = "  ".join(f"{t} ${per_tier[t]:.0f}" for t in sorted(per_tier, key=lambda k: -per_tier[k]))
    # the operator 2026-09-23 standing rule: no chat ever asks him to /clear or stops for
    # size; it keeps its log current and auto-compact trims context (L-0910).
    msg = (f"COST METER — this session has used about ${cost:.0f} token-equivalent (list-price estimate; subscription, not billed) ({mix}). Nothing for you "
           f"to do: it keeps its log current and auto-compact trims its context.")

    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(
                f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                f"{sid[:8]} ${cost:.2f} token-equiv crossed ${level}\n"
            )
    except Exception:
        pass

    # systemMessage surfaces to the operator; additionalContext tells the assistant so
    # it can act on it (his rule: he shouldn't have to ask what's happening).
    print(json.dumps({
        "systemMessage": f"💸 {msg}",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[cost-meter] Session usage is now about ${cost:.0f} token-equivalent (list-price estimate, subscription, not real dollars) ({mix}). "
                f"Tell the operator plainly, in one line. Never suggest /clear or "
                f"stopping (the operator's rule, 2026-09-23): make sure this session's "
                f"log is current (Resume state + Pickup prompts, committed), "
                f"then keep working."
            ),
        },
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log_error(traceback.format_exc(limit=4))
    sys.exit(0)
