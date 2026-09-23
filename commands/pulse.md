---
description: Portfolio traffic pulse — human-vs-bot visits across the live sites, trend vs last reading. Distribution is the binding constraint; this is the gauge
argument-hint: "(no args; needs CF analytics tokens per reference_analytics)"
---

Report real (human) traffic across the operator's live sites and how it's trending. Distribution is the settled binding constraint on everything ([[project_venture_hunt_findings]], [[project_<product-a>]]) — this command is how he sees whether anything is actually reaching people. **Read-only; touches nothing.**

**Delegable:** the whole gather step is mechanical — hand it to a **Haiku/Sonnet** subagent; Opus interprets trends.

Steps:

1. **Read [[reference_analytics]] first** for the current script location (`cf_analytics.mjs`), env vars/tokens, and per-site setup state. If tokens aren't activated for a site, report it as `not wired` — don't guess numbers.

2. **Run the analytics pull** for each wired site (<product-b>, <product-a>, <your-domain>, + any since added) over the last 7 days. Use the script's human-vs-bot estimate (crawler-referrer + pages/visit heuristic) — headline number is **human visits**, bots listed separately.

3. **Compare to the previous reading.** Pulse history lives at `~/projects/pulse-log.md` (create on first run: date, site, human visits, bot visits, note). Report the delta per site. Append today's reading after reporting.

4. **Output a tight table** — site | human visits (7d) | Δ vs last | bots | notable referrer/page — then 2-3 sentences of interpretation: what's growing, what's dead, whether any distribution experiment (WC invite-pool loop, GitHub publishing, /build page) shows a signal.

5. **Never invent numbers** ([[feedback_truth_flagging]]). API errors or missing tokens → say exactly that for that site.

Best run weekly — if the operator isn't invoking it, suggest `/schedule`-ing it as a Monday-morning routine (once, not repeatedly).
