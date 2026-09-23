---
description: Review the token ledger and report spend trends
---

Read `~/.claude/projects/<home-slug>/memory/token_ledger.md` and report:

1. Total estimated spend and total sessions logged.
2. Average cost per session, and the 3 most expensive sessions.
3. Cache hit-rate trend (low hit rates = cache being busted — flag it).
4. Model mix — spend per tier. Read it model-agnostically: identify whichever tier the main loop ran on that week (it changes — opus → fable 2026-07-19 → opus 2026-07-24) and judge THAT share, rather than assuming any one model name is the main loop. A high main-loop share signals delegate more — UNLESS those were declared BUILD sessions (split rule 2026-07-22: builds run the top tier freely; ops sessions don't). Note that a cheaper main-loop model makes the same behaviour bill less, so falling dollars are not by themselves evidence of better delegation.
5. **Delegation ratio** — (sonnet + haiku dollars) / total, per week and trending. IMPORTANT: judge Haiku by work displaced, not dollar share — Haiku is ~15× cheaper than Opus, so report haiku spend alongside its Opus-equivalent (haiku $ × ~15). A "tiny" $2 of Haiku ≈ $30 of Opus work avoided. Dollar share alone makes Haiku look unused even when it's doing its job.
6. **Alarm review** — read `~/.claude/hub/delegation-alarms.log`: for each flagged session (main-loop tier >60% of a >$50 session; lines are written `main-loop=<tier>` since 2026-07-24, and older lines say "fable" only because fable held the slot then), classify BUILD (fine, note it) or OPS LEAKAGE (a miss — name what should have been delegated). **Verify the session's actual workstream before writing a verdict** — on 2026-07-24 a verdict named the wrong session entirely; a keyword count over the transcript settles it in seconds. This is the split rule's weekly close-out.
7. One concrete tuning suggestion based on what the data shows (e.g. "the main-loop tier is 80% of spend across ops sessions — route more file-reading to Haiku"). Health targets: delegation ratio ≥40% on non-build weeks; the balanced-marathon template is top tier directs / Sonnet executes (session 9294ff22, corrected 2026-07-24: $1,690.52 = fable $766 / sonnet $676 / opus $246 / haiku $2). Rows dated before 2026-07-24 that ran Workflows understate agent spend — the ledger's subagent glob missed nested workflow transcripts until then (13 sessions, +$1,284.93 total), so historical delegation ratios were pessimistic.

If the ledger doesn't exist yet, say so — it populates as sessions end (SessionEnd hook) or when `token-ledger.py` is run manually on a transcript.

Keep it short and decision-oriented. This is the measurement feedback loop for [[feedback_token_economy]].
