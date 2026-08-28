#!/usr/bin/env python3
"""Live cost meter: warn DURING a session, not after it ends.

The SessionEnd ledger hook (token-ledger.py) only prices a session once it's
over — for a session that runs for hours, that means nobody sees the running
total until it's too late to change course. This fires on every prompt and
surfaces the running total once it crosses a threshold, so a long, expensive
session gets flagged while it's still happening.

PERFORMANCE — this is a per-prompt blocking hook, so it must stay fast even
when the transcript is large. It never re-reads the whole file: per-file byte
offsets and running token totals are cached in STATE_DIR and each run parses
ONLY the bytes appended since last time. JSONL is append-only, so offset
resume is sound. Cost is recomputed from the cached totals, not re-summed
from disk.

Pricing and tier detection are imported from token-ledger.py so there is
exactly one price table in your setup. If that import fails the meter goes
silent rather than guessing at prices.

THRESHOLDS below are a starting point — calibrate them from your own ledger
once you have one (see "mine your own ledger" in the README): pick the dollar
level where a session has clearly become "a real session," the level that's
in your own top quartile, and the level that's genuinely rare.

Fails silent and always exits 0 — a cost warning must never block a prompt.
"""
import json, sys, os, datetime, importlib.util

HOOKS = os.path.expanduser("~/.claude/hooks")
STATE_DIR = os.path.expanduser("~/.claude/hub/cost-meter")
LOG = os.path.expanduser("~/.claude/hub/cost-meter.log")
ERROR_LOG = os.path.expanduser("~/.claude/hub/hook-errors.log")

# Escalating so it informs once and then gets out of the way. Past this list
# it repeats every 500 (in whatever currency your price table uses).
# Calibrate the actual numbers to your own ledger.
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

    # Main transcript + every subagent transcript, if your harness's ledger
    # helper exposes them (agent_transcripts) — subagent spend is otherwise
    # invisible to this meter.
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
    note = (
        "end this session and start fresh — cost scales with how long a single "
        "session runs, because every turn re-bills the whole conversation"
    )
    msg = f"COST METER — this session has spent ${cost:.0f} ({mix}). Consider: {note}."

    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(
                f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                f"{sid[:8]} ${cost:.2f} crossed ${level}\n"
            )
    except Exception:
        pass

    # systemMessage surfaces to the user directly; additionalContext tells the
    # assistant so it can act on it unprompted rather than the user having to
    # ask what's happening.
    print(json.dumps({
        "systemMessage": f"💸 {msg}",
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                f"[cost-meter] Session spend is now ${cost:.0f} ({mix}). "
                f"Tell the user plainly, in one line, and suggest wrapping up "
                f"this session with a log/checkpoint then clearing if the work allows."
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
