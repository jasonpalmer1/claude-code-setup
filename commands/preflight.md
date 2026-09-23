---
description: Pre-ship gate — checklist distilled from every past shipping incident; run before /ship (mandatory before --prod or anything public/client-facing)
argument-hint: "(run from the project directory; checks the un-shipped diff)"
---

Run the pre-ship checklist on the **current project's un-deployed changes** (working tree + commits not yet live). Every item below exists because skipping it once burned the operator with a real person — a client, Mary, or the public. This gate converts those incidents into a deterministic check instead of relying on memory.

**Read-only against the app; never deploys.** Output is a PASS/FAIL verdict — `/ship` acts on it.

**Delegable:** items 2–5 are mechanical greps/scans — batch them to one **Haiku** subagent that returns findings only. Opus judges the results.

The checklist:

1. **Build clean.** Run the project's build. Broken build = instant FAIL, stop here.

2. **Secrets scan.** Diff + staged files contain no keys/tokens/passwords; no `.env*` file is staged or newly committed. (Global security rule.)

3. **Sample-data scan** ([[feedback_sample_data_names]]). Grep demo/sample/seed data for real-sounding company or person names. Anything client-facing must use obviously-fake names (Acme, Globex, "(sample)") + a sample-data disclaimer. *Incident: a demo once showed real client firm names instead of sample data.*

4. **Truth scan on public copy** ([[feedback_truth_flagging]]). Any new user-visible claim, number, or stat must be verifiable — flag every one that isn't with its source status. Never ship an invented number. *Incident: fabricated "$3" claim nearly shipped in a launch post.*

5. **iOS hazard scan** ([[feedback_ios_inline_base64]]). Grep for large inline base64 (`data:image` blobs over ~50KB) in anything served to browsers — these white-screen iOS Safari with no error. Images must be URL routes.

6. **Cache-bust check** ([[reference_cf_pages_asset_cache]]). If any image/asset was *replaced under the same filename*, FAIL until renamed — CF Pages + browser cache will serve the stale one for ~4h and it'll "look the exact same."

7. **Visual test** ([[feedback_test_new_features]]). `/shot` every screen this change touches at 390×660 against the local/preview build; if the feature is interactive, drive the interaction (script off `~/.claude/tools/shotter/`), and LOOK at the results. New features are never shipped on a clean build alone.

8. **UI + journey verdict** ([[feedback_ui_journey_check_before_every_deploy]], the operator 2026-09-16 ~21:45, standing gate). The playtester's report for THIS exact build must contain its own UI + journey verdict section: PASS/FAIL, phone-size (390×660) screenshots, the click path walked, and what a stranger does next. Missing that section, a verdict left over from a different build, or a FAIL verdict all FAIL this gate. The verdict must judge ease, look, spacing, and placement against the project's session / views-per-session numbers, and whether the next tap is obvious in 2 seconds, not against "the operator asked for it." A FAIL here blocks the ship even for something the operator asked for.

9. **Diff review.** `/code-review` at low/medium effort on the un-shipped diff. Blocking findings = FAIL.

10. **Auth-wall / gate changes: test from a cookie-less isolated context.** If the diff touches any access gate (edge middleware, cookies, auth walls), verify the flow from a FRESH profile with no cookies — and if the site is an installable PWA, reason through the installed-app path explicitly: home-screen apps get an isolated cookie jar, and service workers can cache a gate rejection as the app shell. *Incident: 2026-07-27 our-place edge gate white-screened the operator's & Mary's installed app for 8 days; the SW cached the blank 404 as the shell (fixed 2026-08-04).*

11. **Eval gate on AI judgment** ([[feedback_eval_gate_question]]). Ask every time, out loud: *does this change let an AI make a judgment call that can be wrong in a way nobody would notice?* If no (deterministic UI, data, layout, copy) → n/a, say so and move on. If yes → there must be a versioned eval set scoring **both** the answer and the path, failures must feed back in as new cases, and a red eval must block the ship. No eval loop on a shipping AI-judgment feature = ⚠️ at minimum, ❌ if it touches money, health, or anything client-facing.

**Output:** a tight table — item | ✅/❌/⚠️/n/a | one-line note — then a single verdict line: **PREFLIGHT PASS** or **PREFLIGHT FAIL: <blocking items>**. ⚠️ (non-blocking) doesn't fail the gate but must be listed. Mark items n/a honestly (e.g. no public copy changed) rather than rubber-stamping ✅.
