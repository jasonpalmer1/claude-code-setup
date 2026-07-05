---
description: Pre-ship gate — a checklist distilled from real shipping incidents; run before /ship (mandatory before production or anything public/client-facing)
argument-hint: "(run from the project directory; checks the un-shipped diff)"
---

Run the pre-ship checklist on the **current project's un-deployed changes** (working tree + commits not yet live). Each item below exists because skipping it once caused a real incident — a client, a stakeholder, or the public saw something broken. This gate turns lessons into a deterministic check instead of relying on memory.

**Read-only against the app; never deploys.** Output is a PASS/FAIL verdict — `/ship` acts on it.

**Delegable:** items 2–5 are mechanical greps/scans — batch them to one cheap-tier subagent that returns findings only; the top-tier model judges the results.

The checklist:

1. **Build clean.** Run the project's build. Broken build = instant FAIL, stop here.

2. **Secrets scan.** Diff + staged files contain no keys/tokens/passwords; no `.env*` file is staged or newly committed.

3. **Sample-data scan.** Grep demo/sample/seed data for real-looking company or person names. Anything client-facing must use obviously-fake names ("Acme Corp", "Jane Sample") plus a sample-data disclaimer. (Incident class: a demo shipped with data that looked like a real client's actual name/records, visible to another client.)

4. **Truth scan on public copy.** Any new user-visible claim, number, or stat must be verifiable — flag every one that isn't, with its source status. Never ship an invented number. (Incident class: a fabricated statistic almost shipped in public-facing copy.)

5. **Mobile/iOS hazard scan.** Grep for large inline base64 (`data:image` blobs over roughly 50KB) in anything served to browsers — these can white-screen mobile Safari with no visible error. Images should be served as URL routes, not inlined.

6. **Cache-bust check.** If any image/asset was replaced under the same filename, FAIL until it's renamed — CDN and browser caching will keep serving the stale version for hours, and it will "look like nothing changed."

7. **Visual test.** Screenshot (`/shot` or your own tool) every screen this change touches at a phone viewport (e.g. 390×660) against the local/preview build; if the feature is interactive, drive the interaction and actually look at the results. New features are never shipped on a clean build alone.

8. **Diff review.** Run `/code-review` (or your equivalent) at low/medium effort on the un-shipped diff. Blocking findings = FAIL.

**Output:** a tight table — item | ✅/❌/⚠️/n/a | one-line note — then a single verdict line: **PREFLIGHT PASS** or **PREFLIGHT FAIL: <blocking items>**. ⚠️ (non-blocking) doesn't fail the gate but must be listed. Mark items n/a honestly (e.g. no public copy changed) rather than rubber-stamping ✅.
