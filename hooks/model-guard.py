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

LOG = os.environ.get("MODEL_GUARD_LOG") or os.path.expanduser("~/.claude/hub/model-guard.log")
# L-1559 item 7: enforcement of the tightened read-shape gate. OFF (log-only, WOULD-BLOCK lines)
# until the hub flips this default after 48h of log review. Env override exists for tests/replay.
ENFORCE_READ = os.environ.get("MODEL_GUARD_ENFORCE_READ", "0").strip().lower() in ("1", "true", "yes", "on")
# Typed gate agents are never forced down a tier by the new read-shape logic.
NEVER_DOWN_TYPES = frozenset(("planner", "playtester", "bug-gate", "search-demand"))
_SHAPE_LINE_RE = re.compile(r"^[ \t]*shape\s*:\s*(read|judgment|build)[ \t]*$", re.I | re.M)
# Only lines that ARE the report instruction: a "Report: path" line or "write ... report".
# A bare mention of hub/reports/ (e.g. "read the plan at ~/.claude/hub/reports/L-x/plan.md")
# must NOT be stripped: replay 1 showed that dropped the "Build L-1548" line of real build briefs.
_REPORT_LINE_RE = re.compile(r"^\s*report\s*:|\bwrit(?:e|ing)\b[^\n]{0,80}\breports?\b", re.I)
# Clause form: strip only the "write ... report" span so a build verb on the same line survives.
_REPORT_CLAUSE_RE = re.compile(r"\bwrit(?:e|ing)\b[^\n]{0,80}\breports?\b", re.I)
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
#
# L-0942 item 3: moved to hooks/lib/build_shape.py, unchanged, so
# safety-guard.py's new bare-Bash-command guard shares the exact same
# definition instead of a second, driftable copy.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))
from build_shape import BUILD_SHAPE_RE

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

    # L-1387 (2026-09-27): block a brief whose primary ticket (L-#### in the
    # description or first 300 chars of the prompt) is already held by a LIVE
    # worker in any session -- tonight three chats resumed the same dead ticket.
    # DUP-OK anywhere in the prompt overrides (deliberate verifier/second opinion).
    # Fail-open: any error or >3s runtime of the checker never blocks.
    if "DUP-OK" not in prompt:
        try:
            r = subprocess.run(
                [os.environ.get("DUP_CHECK_BIN", os.path.expanduser("~/.claude/hub/bin/dup-worker-check"))],
                input=json.dumps({"description": tin.get("description", ""), "prompt": prompt}),
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 3 and r.stdout.strip():
                log(f"DUP-BLOCK {desc!r}: {r.stdout.strip()[:300]}")
                print(
                    "Duplicate worker (L-1387): " + r.stdout.strip() + "\n"
                    "Do not start a second worker on the same ticket; wait for or message the live one. "
                    "If this is deliberate (verifier, second opinion), add DUP-OK to the prompt and retry.",
                    file=sys.stderr,
                )
                sys.exit(2)
        except SystemExit:
            raise
        except Exception as e:
            log(f"DUP-CHECK fail-open {type(e).__name__}")
    elif re.search(r"\bL-\d{4}\b", prompt[:300] + desc):
        log(f"DUP-OK override {desc!r}")

    read_shaped = re.search(
        r"read[\s-]*only|do\s+not\s+edit|don'?t\s+edit|no\s+edits|report[\s-]*only", prompt, re.I
    )
    judgment = re.search(r"shape\s*:\s*(code|exec|executor|judg(e)?ment)", prompt, re.I)

    # L-0964 (2026-09-23): the read-shape gate above was misfiring on genuine sonnet
    # BUILD briefs whose fence/constraint section reuses a read-shape phrase to LIMIT
    # scope -- "never touch the live tree", "no edits to main", "the gate report is
    # reference material, do not edit it" -- not to declare the WHOLE brief read-only.
    # Real transcripts (hub session 1575c172, this lane's own a8476cd2) showed it
    # blocking 5 genuine sonnet build briefs on exactly this pattern in one evening.
    #
    # Two escapes, checked before the block below fires:
    #
    #   1. shape: build -- an explicit line (own line, case-insensitive, the build-
    #      shaped sibling of the pre-existing "shape: judgment" escape above) that
    #      always keeps the caller's chosen tier and skips this gate, no verb-sniffing
    #      needed. "Own line" means the whole line, trimmed, is exactly "shape: build"
    #      -- text that merely CONTAINS that phrase mid-sentence ("shape: build. You
    #      are a hub worker...", which several of the real blocked briefs actually
    #      wrote, presumably guessing at a convention that didn't exist yet) does not
    #      count as this escape, though such briefs still pass via escape 2 below.
    #   2. A build verb present anywhere in the prompt and not directly negated --
    #      see BUILD_VERB_RE / _first_unnegated_build_verb() below. This is what lets
    #      a real build brief with a "never touch the live tree" fence pass without
    #      the caller having to remember the shape: line at all.
    #
    # Neither escape touches a genuinely read-shaped brief: no shape: build line and
    # no un-negated build verb anywhere -> still blocked below, byte-identical message
    # to before this ticket. shape: judgment's own behavior is completely unchanged.
    SHAPE_BUILD_RE = re.compile(r"^[ \t]*shape\s*:\s*build[ \t]*$", re.I | re.M)

    # --- L-0964 fix round 1 (2026-09-23, bug-gate r1 FAIL) ---------------------
    # Blocker 1: the original 4-word backward window didn't track WHICH verb a
    # negation cue modifies, so a fence clause's own negation ("do not edit main")
    # could swallow an unrelated, un-negated build verb sitting just a few words
    # later ("... main, then build the thing") -- reproducing the exact false-block
    # this ticket exists to fix, for entirely ordinary phrasing, not just adversarial
    # input. Fixed by tokenizing the prompt and walking backward from each build-verb
    # TOKEN with a strict, bounded rule (see _is_negated below): a negator only
    # reaches a build verb through an unbroken chain of at most 2 allowlisted filler
    # words, with no punctuation/clause-word boundary and no OTHER word (including
    # another real verb like "edit") in between. "then"/"and"/"but"/"or" and
    # , ; : . ! ? / newline all break the chain, the same way a real clause boundary
    # would in English.
    #
    # Blocker 2: the previous version called re.findall() on prompt[:m.start()] --
    # the ENTIRE prefix, re-tokenized from scratch -- for every single BUILD_VERB_RE
    # match, which is O(matches x prompt length): quadratic, confirmed at 258s on a
    # 500KB crafted prompt (bug-gate r1). Fixed by tokenizing the WHOLE prompt exactly
    # ONCE (single linear regex pass, _tokenize below) into an indexed list, then
    # doing O(1) bounded work per verb token by indexing directly into that list --
    # total time is now O(prompt length). Verified by the perf test in
    # test-model-guard.sh (group 7): the same 500KB shape now completes in well
    # under 2s (see hub/reports/L-0964-build.md, "Fix round 1", for the timing).
    #
    # Exact whole-word, case-insensitive inflection set (ticket-required base verbs:
    # build, write, create, merge, commit, delete). This is deliberately a SEPARATE
    # set from hooks/lib/build_shape.py's BUILD_SHAPE_RE imported above -- that one
    # detects a literal shell BUILD COMMAND (npm run build, wrangler deploy, ...) for
    # the unrelated L-0789 disk gate; this one detects an English BUILD VERB in a
    # free-text brief for the read-shape gate. Conflating them would be wrong both
    # ways: "npm run build" is not an English build verb, and "create the ticket in
    # the tracker" is not a shell build command.
    #
    # Matched by TOKEN membership now (not a second regex pass) -- see _tokenize /
    # _first_unnegated_build_verb below. A "word" token is a run of \w (letters,
    # digits, underscore) plus apostrophe -- the SAME notion of a word boundary the
    # old \b-anchored regex used, deliberately preserved: "created_at" tokenizes as
    # ONE token ("created_at") that matches nothing here, exactly as \bcreated\b
    # never matched inside "created_at" before this rewrite either (underscore counts
    # as a word character for \b too). This keeps that already-verified-correct
    # behavior instead of accidentally reintroducing it as a new false positive.
    BUILD_VERB_WORDS = frozenset((
        "build", "builds", "building", "built",
        "write", "writes", "writing", "wrote", "written",
        "create", "creates", "creating", "created",
        "merge", "merges", "merging", "merged",
        "commit", "commits", "committing", "committed",
        "delete", "deletes", "deleting", "deleted",
    ))
    NEGATOR_WORDS = frozenset((
        "not", "never", "no", "without",
        "don't", "dont", "doesn't", "doesnt", "didn't", "didnt",
    ))
    # A negator reaches a build verb through at most 2 of these words, and no
    # others -- any word outside this set (including another real verb, a noun,
    # "main", etc.) breaks the chain immediately, no matter how close it is.
    ALLOWLIST_FILLERS = frozenset(("to", "any", "ever", "you", "yet", "need"))
    # Clause-boundary WORDS (as opposed to punctuation, handled separately) -- same
    # chain-breaking effect as a comma or period.
    CLAUSE_BOUNDARY_WORDS = frozenset(("then", "and", "but", "or"))
    # L-0964 fix round 2 (2026-09-23, bug-gate r2 FAIL): round 1 added a
    # "noun-partner" suppression here -- a build verb directly preceded by an
    # article/demonstrative or directly followed by one of a dozen "noun
    # partner" words ("the build report", "commit history") didn't count, to
    # stop those phrasings from rescuing a genuinely read-shaped brief onto
    # sonnet. Bug-gate r2 found it reintroduced the exact false-block class
    # this ticket exists to close, on ordinary NO-article bare-object
    # phrasing: "delete log files", "create log entries", "build base
    # images" -- genuine build/action instructions where the "noun partner"
    # word is the grammatical OBJECT's own modifier, not a nominal reading of
    # the verb -- were wrongly suppressed and the brief false-blocked (6 of 8
    # ordinary phrasings in bug-gate r2's sweep). Removed entirely per
    # coordinator decision: the incidental-noun leak ("the build report"
    # rescuing a read-shaped brief) is accepted as a documented, cost-tier-
    # only, non-blocking cost (this gate is a cost/shape control, not a
    # security boundary -- see hub/reports/L-0964-build.md, "Fix round 2").
    # No noun-partner check remains anywhere in this file as of round 2.

    _TOKEN_RE = re.compile(r"(?P<word>[\w']+)|(?P<punct>[,;:.!?\n])")

    def _tokenize(text):
        """Single linear regex pass over the whole prompt -> a list of
        (is_word, lower_text) tuples. is_word=False marks one of the clause-
        boundary punctuation marks; everything else (whitespace, quotes, parens,
        hyphens, emoji, ...) is simply not a token -- it contributes nothing and
        is skipped, exactly as it was always invisible to the old \\b-anchored
        regex. This is the ONE tokenization pass Blocker 2 requires: called once
        per hook invocation, never per-match."""
        out = []
        for m in _TOKEN_RE.finditer(text):
            word = m.group("word")
            if word is not None:
                out.append((True, word.lower()))
            else:
                out.append((False, m.group("punct")))
        return out

    def _comma_is_transparent(tokens, i):
        # Narrow, explicit special case for "never, ever commit" (and any
        # NEGATOR, FILLER shape) -- a comma directly between an already-confirmed
        # negator and an already-confirmed filler is transparent to the backward
        # walk. This can't rescue an ordinary sentence comma ("do not edit main,
        # then build") because "main" is neither a negator nor a filler word, so
        # the general boundary rule still applies there untouched.
        before = tokens[i - 1] if i - 1 >= 0 else None
        after = tokens[i + 1] if i + 1 < len(tokens) else None
        return (
            before is not None and before[0] and before[1] in NEGATOR_WORDS
            and after is not None and after[0] and after[1] in ALLOWLIST_FILLERS
        )

    def _is_negated(tokens, verb_idx):
        """True if the build-verb token at verb_idx is directly negated, walking
        backward with a strict, bounded chain (see module comment above). O(1)
        per call: at most ~4 steps (the filler cap of 2, plus the negator/
        boundary/other-word check that always terminates the walk), regardless
        of how long the prompt is or how many filler words repeat -- this is
        what makes Blocker 2's fix actually O(n) rather than merely "fewer
        passes"."""
        fillers = 0
        i = verb_idx - 1
        while i >= 0:
            is_word, tok = tokens[i]
            if not is_word:
                if tok == "," and _comma_is_transparent(tokens, i):
                    i -= 1
                    continue
                return False  # punctuation clause boundary
            if tok in NEGATOR_WORDS:
                return True
            if tok in CLAUSE_BOUNDARY_WORDS:
                return False
            if tok in ALLOWLIST_FILLERS:
                fillers += 1
                if fillers > 2:
                    return False  # filler-chain cap exceeded
                i -= 1
                continue
            return False  # any other word (incl. another real verb) breaks it
        return False

    def _first_unnegated_build_verb(text):
        """First build-verb TOKEN in `text` not directly negated, else None.
        Tokenizes `text` exactly once, then does O(1) bounded work per
        candidate token: overall O(len(text)), not the old O(matches x
        len(text)). (Round 2: no longer checks for "nominal" usage like "the
        build report" -- see the CLAUSE_BOUNDARY_WORDS comment above for why
        that check was removed. A build-verb token counts unless it is
        directly negated, full stop.)"""
        tokens = _tokenize(text)
        for idx, (is_word, tok) in enumerate(tokens):
            if not is_word or tok not in BUILD_VERB_WORDS:
                continue
            if _is_negated(tokens, idx):
                continue
            return tok
        return None

    shape_build = SHAPE_BUILD_RE.search(prompt)
    build_verb = None if (judgment or shape_build) else _first_unnegated_build_verb(prompt)

    if model != "haiku" and read_shaped and not judgment and not shape_build and not build_verb:
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

    # L-1559 item 7: tightened read-shape gate. Reached only when the legacy gate above
    # did NOT block. (a) lines that only name hub/reports/ or "write ... report" are dropped
    # before the build-verb scan (writing a report is what read workers do); (b) an own-line
    # `shape: read|judgment|build` is first class: read forces haiku, the others keep any
    # tier; (c) planner/playtester/bug-gate/search-demand are never forced down.
    # (d) LOG-ONLY unless ENFORCE_READ: writes WOULD-BLOCK, blocks only when enforced.
    if model and model != "haiku":
        stype = str(tin.get("subagent_type") or "").strip().lower()
        sm = _SHAPE_LINE_RE.search(prompt)
        sval = sm.group(1).lower() if sm else ""
        new_reason = ""
        if stype in NEVER_DOWN_TYPES or sval in ("judgment", "build") or judgment:
            pass
        elif sval == "read":
            new_reason = "shape:read"
        elif read_shaped:
            stripped = "\n".join(
                _REPORT_CLAUSE_RE.sub("", l) for l in prompt.split("\n")
                if not re.match(r"^\s*report\s*:", l, re.I)
            )
            if not shape_build and not _first_unnegated_build_verb(stripped):
                new_reason = "report-lines-stripped:" + read_shaped.group(0).lower()
        if new_reason:
            log(f"WOULD-BLOCK read-shape ({new_reason}) model={model} enforce={int(ENFORCE_READ)} {desc}")
            if ENFORCE_READ:
                print(
                    "Blocked (delegation rule #2, L-1559 item 7): this brief is read-shaped "
                    f"({new_reason}) but asks for model={model}. Re-issue with model: haiku, or "
                    "add the own line 'shape: judgment' (a read that needs a real verdict) or "
                    "'shape: build' to keep the tier you chose.",
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
    # Item 19 (the operator 2026-10-05): the gate fires only for a build that targets the ROOT disk.
    # A brief that points builds at the USB drive is exempt, and a build keyword that the brief
    # negates ("do NOT run next build", "never deploy.sh") is not a build request at all.
    _NEG_BEFORE_RE = re.compile(
        r"(?:\bnot\b|\bnever\b|\bno\b|\bdon'?t\b|\bwithout\b|\bavoid\b|\bskip\b|\bnor\b|\bnon-)\W+(?:[\w./-]+\W+){0,9}$", re.I)
    def _first_unnegated_shape(text):
        pos = 0
        while pos < len(text):
            m = BUILD_SHAPE_RE.search(text[pos:])
            if m is None:
                return None
            start = pos + m.start()
            pos = start + max(1, m.end() - m.start())
            lead = text[max(0, start - 60):start]
            lead = re.split(r"[.;\n]", lead)[-1]
            if _NEG_BEFORE_RE.search(lead):
                continue
            return m
        return None
    build_match = None
    # CLAUDE_BUILD_ROOT (empty = disabled): a prompt that already points builds at this external
    # build root is exempt from the root-disk pre-dispatch gate.
    _build_root = os.environ.get("CLAUDE_BUILD_ROOT", "")
    if tool in ("Agent", "Task") and not (_build_root and _build_root in prompt):
        build_match = _first_unnegated_shape(prompt)
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
        # L-0964: note which read-shape-gate escape (if any) let this call through,
        # same style as the pre-existing judgment opt-out note, for audit trail.
        escape_note = ""
        if judgment:
            escape_note = " opt-out:" + judgment.group(0)
        elif shape_build:
            escape_note = " shape:build"
        elif build_verb:
            escape_note = " build-verb:" + build_verb
        log(f"OK model={model}{escape_note} {desc}")
except SystemExit:
    raise
except Exception as e:
    log(f"FAIL-OPEN error={type(e).__name__}: {e}")
sys.exit(0)
