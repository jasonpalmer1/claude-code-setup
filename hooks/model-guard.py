#!/usr/bin/env python3
"""PreToolUse guard: delegation rule #1 made deterministic (2026-07-22 ecosystem scan —
prose is advisory, hooks are guaranteed; the 3 unspecified-model spawns the transcript
audit found are exactly the class this closes).

Blocks any Agent/Task spawn without an explicit model. The thing being prevented is
INHERITANCE, not any particular model: an unspecified spawn silently takes whatever
tier the main loop happens to be on that week (opus -> fable 2026-07-19, fable -> opus
2026-07-24), which is how the 69-subagent incident happened. Deliberately choosing any
tier is fine — including a tier PRICIER than the main loop, when that sub-task genuinely
wants it. Accidentally choosing one is not.

Also warns (non-blocking) when a Workflow script spawns agents with no model option —
same inheritance bug, one layer down. Measured 2026-07-24 on session 9294ff22: 105
workflow agents inherited the main-loop tier for $104, invisible to the ledger until
token-ledger.py was taught to read nested workflow transcripts.

Exit 2 = block (stderr goes back to Claude). Must never block on its OWN failure.

L-0789 (2026-09-22, hub audit amendment 1): also blocks a build-shaped Agent/Task
dispatch when ~/.claude/hub/bin/disk-guard reports CRIT (exit 2) -- the pre-dispatch
disk gate lives here, in code, not only as a habit in the worker template. Every
existing behaviour above is unchanged; this is purely additive and reached only after
all prior checks pass. disk-guard failing to run (missing, times out, errors) must
never itself block dispatch -- same "never block on our own failure" rule as the rest
of this hook."""
import json, sys, os, datetime, re, subprocess

LOG = os.path.expanduser("~/.claude/hub/model-guard.log")
# Overridable for tests only; production always uses the real disk-guard.
DISK_GUARD_BIN = os.environ.get("DISK_GUARD_BIN", os.path.expanduser("~/.claude/hub/bin/disk-guard"))
# L-0789 bug-gate blocker 1 (2026-09-22): this list was narrower than the
# fleet's own established "what counts as a build tool" definition --
# disk-guard's BUILD_COUNT scan and build-cache-sweep.sh's worktree_busy()
# already treat npm|npx|next|yarn|pnpm|vite|webpack|turbo as build tools in
# active use. Widened to match that set (plus bun, which neither of those
# scripts covers but the operator asked for here), so a CRIT dispatch of
# `pnpm build`, `yarn build`, `turbo build`, `vite build`, etc. is no longer
# silently let through while `next build`/`npm run build` are blocked.
BUILD_SHAPE_RE = re.compile(
    r"next build|npm run build|build\+strip|wrangler (pages )?deploy|deploy\.sh"
    r"|(?:pnpm|yarn|bun)(?: run)? build"
    r"|turbo(?: run)? build"
    r"|vite build"
    # webpack is only build-shaped here when it looks like a production
    # build invocation -- a bare "webpack" (e.g. reading webpack.config.js,
    # `cat webpack.config.js`) must not trip the gate.
    r"|webpack.*(?:--mode[=\s]+production|--production\b|\s-p\b)"
    r"|NODE_ENV=production.*webpack",
    re.I,
)

def log(line):
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOG, "a") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        pass

try:
    data = json.load(sys.stdin)
    tool = data.get("tool_name") or ""
    tin = data.get("tool_input") or {}

    if tool == "Workflow":
        # Advisory only: a regex can't reliably tell which agent() calls in an
        # arbitrary script omit opts.model, and a false block kills a whole run.
        script = tin.get("script") or ""
        if script:
            calls = len(re.findall(r"\bagent\s*\(", script))
            models = len(re.findall(r"\bmodel\s*:", script))
            if calls and models < calls:
                log(f"WARN workflow {calls} agent() calls / {models} model: opts")
                print(
                    f"Advisory (delegation rule #1): this workflow has {calls} agent() "
                    f"call(s) but only {models} explicit model: option(s). Every agent() "
                    "without opts.model inherits the MAIN-LOOP tier — that is how 105 "
                    "agents silently ran at the top tier on 2026-07-24. Set opts.model "
                    "per stage (any tier, deliberately chosen) unless inheritance is "
                    "what you actually want here.",
                    file=sys.stderr,
                )
        sys.exit(0)

    # Read-shape gate (2026-09-06): the ledger showed Haiku at 1% of spend two weeks
    # running while the rule says Haiku is DEFAULT for reads. A prompt that declares
    # itself read-only / do-not-edit / report-only is read-shaped by its own words, so
    # it runs on haiku unless the caller states "shape: judgment" (a read that needs a
    # real verdict — audits, reviews, adversarial checks) to opt out deliberately.
    if tool not in ("Agent", "Task"):
        sys.exit(0)

    model = str(tin.get("model") or "").lower()
    prompt = str(tin.get("prompt") or "")
    desc = str(tin.get("description", "?"))[:80]
    read_shaped = re.search(
        r"read[\s-]*only|do\s+not\s+edit|don'?t\s+edit|no\s+edits|report[\s-]*only", prompt, re.I
    )
    judgment = re.search(r"shape\s*:\s*(code|exec|executor|judg(e)?ment)", prompt, re.I)
    if model != "haiku" and read_shaped and not judgment:
        log(f"BLOCKED read-shape model={model or 'unset'} {desc}")
        print(
            "Blocked (delegation rule #2, read-shape gate since 2026-09-06): this prompt "
            f"declares itself read-only/report-only ('{read_shaped.group(0)}') but asks for "
            f"model={model or 'unset'}. Haiku is the DEFAULT for read-shaped work. Re-issue "
            "with model: haiku, or — if this read genuinely needs a verdict (audit, review, "
            "adversarial check) — add the line 'shape: judgment' to the prompt to keep the "
            "tier you chose.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not tin.get("model"):
        log(f"BLOCKED {desc}")
        print(
            "Blocked (delegation rule #1, deterministic since 2026-07-22): every subagent "
            "call states model: explicitly. Pick by task SHAPE, not by price rank — haiku "
            "for read-shaped work, sonnet for code-shaped, and any tier (including one "
            "above the main loop) when that sub-task genuinely needs it. What's banned is "
            "leaving it unset, which silently inherits the main-loop tier. Re-issue this "
            "Agent call with a model parameter.",
            file=sys.stderr,
        )
        sys.exit(2)

    # L-0789 disk gate (amendment 1): a build-shaped dispatch while the disk is CRIT
    # makes the emergency worse, not better. Everything above this is unchanged; this
    # only runs once a call has already cleared every prior gate.
    build_match = BUILD_SHAPE_RE.search(prompt) if tool in ("Agent", "Task") else None
    if build_match:
        try:
            r = subprocess.run([DISK_GUARD_BIN], capture_output=True, text=True, timeout=10)
            if r.returncode == 2:
                verdict_line = (r.stdout or "").strip() or "disk-guard reports CRIT"
                log(f"BLOCKED disk-CRIT build-shaped model={model} {desc}")
                print(
                    "Blocked (L-0789 pre-dispatch disk gate): this dispatch looks "
                    f"build-shaped ('{build_match.group(0)}') and {verdict_line}. New "
                    "build dispatches are blocked until free space recovers -- run "
                    "~/.claude/hub/bin/disk-guard for the current number, free space "
                    "(build-cache-sweep.sh) or wait for the automatic sweep, then retry.",
                    file=sys.stderr,
                )
                sys.exit(2)
        except SystemExit:
            raise
        except Exception:
            # disk-guard itself failing (missing, timeout, bad exit) must never block
            # dispatch -- same rule as the rest of this hook.
            log(f"WARN disk-guard check errored, not blocking {desc}")

    if tool in ("Agent", "Task"):
        log(f"OK model={model}{' opt-out:'+judgment.group(0) if judgment else ''} {desc}")
except SystemExit:
    raise
except Exception as e:
    log(f"FAIL-OPEN error={type(e).__name__}: {e}")
sys.exit(0)
