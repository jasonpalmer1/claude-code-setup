---
description: Portfolio traffic pulse — human-vs-bot visits across your live sites, trend vs last reading
argument-hint: "(no args; needs analytics API tokens configured per your own reference doc)"
---

Report real (human) traffic across your live sites and how it's trending. If distribution/reach is a binding constraint for your projects, this command is how you see whether anything is actually reaching people. **Read-only; touches nothing.**

**Delegable:** the whole gather step is mechanical — hand it to a cheap-tier subagent; the top-tier model interprets trends.

Steps:

1. **Read your analytics reference doc first** for the current script location, env vars/tokens, and per-site setup state. If tokens aren't activated for a site, report it as `not wired` — don't guess numbers.

2. **Run the analytics pull** for each wired site over the last 7 days. Use a human-vs-bot estimate (e.g. crawler-referrer + pages/visit heuristic) — the headline number is **human visits**, bots listed separately.

3. **Compare to the previous reading.** Pulse history lives at `<PULSE_LOG_PATH>` (e.g. `~/projects/pulse-log.md`; create it on first run: date, site, human visits, bot visits, note). Report the delta per site. Append today's reading after reporting.

4. **Output a tight table** — site | human visits (7d) | Δ vs last | bots | notable referrer/page — then 2-3 sentences of interpretation: what's growing, what's dead, whether any distribution experiment shows a signal.

5. **Never invent numbers.** API errors or missing tokens → say exactly that for that site.

Best run weekly — consider scheduling it as a recurring routine (see `routines/`) rather than remembering to run it by hand.
