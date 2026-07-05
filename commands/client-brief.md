---
description: Draft a client-facing progress update (plain English + screenshots) from recent git history
argument-hint: "[project-dir] [--since <date|ref>]  (default: current project, since last brief)"
---

Turn recent work on a client project into an update the **client** (a non-technical person, not a developer) can read in two minutes. This is a consulting-practice artifact: it demonstrates operator professionalism and keeps the client feeling momentum between deliveries.

Args: `$ARGUMENTS` → project dir (default: current directory) and optional `--since`. Default window: since the last brief in the project's `briefs/` dir, else the last 14 days.

**DRAFT ONLY — never send anything.** You review and send it yourself; client comms are outward-facing and deserve a human check before going out.

Steps:

1. **Gather** (delegable to a cheap-tier subagent): the project's `CLAUDE.md`, `git log --since=... --oneline`, and any deploy notes. Return a distilled change list, not the raw log.

2. **Translate to client language.** Group commits into 3-7 outcome bullets: *what changed and why it matters to their business* — e.g. "Your team can now filter the list down to just their own records" — never "refactored the list-view state management." Drop internal-only work (deps, CI, refactors) or roll it into one "under-the-hood reliability work" line.

3. **Screenshot the visible changes** via `/shot` (or your own screenshot tool) against the live/preview app. Rules for anything a client sees: obviously-fake sample data only; no real numbers or claims that aren't verifiable.

4. **Draft the brief** — short: *Done since last update* (the bullets + shots) / *Up next* (only what's actually agreed or in flight — don't invent roadmap) / *Anything I need from you* (only if actually true). Friendly-professional, zero jargon, no hedging filler.

5. **Save + deliver for review.** Write to `<project>/briefs/YYYY-MM-DD.md` (create the directory if missing), send it along with the screenshots, and remind yourself it's a draft for *you* to send, not the client.
