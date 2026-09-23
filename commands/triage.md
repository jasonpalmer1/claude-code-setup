---
description: Cheap 10-minute pre-filter for a new venture/product idea — checks it against settled kills and known veto patterns BEFORE burning a full /go-no-go run
argument-hint: "<one-paragraph idea description>"
---

Pre-filter the idea in `$ARGUMENTS` so the operator doesn't spend a full `/go-no-go` run (hours + real dollars) on something already killed or obviously veto-bound. **Cost cap ≈ $5** — this is a filter, not a verdict. **Triage can KILL or ESCALATE; it can never greenlight.** Only `/go-no-go` greenlights ([[feedback_go_no_go_protocol]]).

Steps:

1. **Check the graveyard first — don't re-derive settled kills** ([[project_venture_hunt_findings]]). Read `~/projects/VENTURE-HUNT-2026-07-01.md` plus kill-verdict memories (car bid/ask NO-GO, STR alerts NO-GO 27.95-veto). If this idea is one of them, or a re-skin of one, verdict is **KILL (settled)** — cite the prior verdict and stop.

2. **Apply the known veto patterns** — each has killed ideas before:
   - **Distribution is the operator's binding constraint.** Solo / no-network / organic-growth-dependent ideas die. Who reaches the first 100 users, concretely? "SEO + Reddit" is a fail.
   - **Commoditized wedge** — is the core feature already free from incumbents (the CarSnipe/Swoopa lesson)?
   - **Legal/eligibility walls** (PDT-style account rules, WOSB-style ownership requirements, licensing).
   - **Proximity** ([[project_defense_venture]]) — does it require insider access the operator doesn't have and can't cheaply get?

3. **One cheap reality check** — a single **Sonnet** subagent, ~10 min of web search: who already owns this space, is the wedge already free, any regulatory wall. Distilled findings only, no report.

4. **Verdict**, a few lines each:
   - **KILL** — matches a settled kill or hard veto; name it. Append one line recording the kill (idea, date, reason) to `~/projects/VENTURE-HUNT-2026-07-01.md` so it stays settled.
   - **ESCALATE** — survives triage; recommend `/go-no-go` and hand over the triage findings as its starting context.
   - **NEED-INFO** — one specific question for the operator that determines which way it goes.

Keep it brutal and fast. The point is protecting the expensive pipeline, not being fair to the idea.
