---
description: Deploy-and-verify a Cloudflare Pages project — safe by default (preview, never prod without --prod)
argument-hint: "[--prod]  (default: preview; --prod requires explicit confirmation)"
---

Deploy the project in the **current working directory** to Cloudflare Pages and verify it's live. Reuse the project's *existing* deploy scripts — never invent new deploy logic.

Args: `$ARGUMENTS` → `--prod` means "promote to production." Absence of `--prod` = **preview** (the safe default).

**Safety contract (🟡 yellow tier — see [[feedback_autonomy_ladder]]).** Deploys default to PREVIEW. Never promote to production unless `--prod` is passed **and** you've explicitly confirmed with the operator in this turn. DB migrations are 🔴 red — never run them automatically.

Steps:

1. **Detect the project + its deploy method** by reading `package.json` (+ any `wrangler.*`, `next.config.*`, `.github/workflows/`). Three patterns exist in his fleet:
   - **A — npm deploy scripts** (e.g. `our-place`): `deploy:preview` / `deploy:prod` already wrap `wrangler pages deploy dist --branch preview|main`. Use them verbatim.
   - **B — manual wrangler, no script** (e.g. `jasonwpalmer-com`: Next.js `output: "export"` → `out/`): build, then `wrangler pages deploy out --project-name <name> --branch preview` (or `--branch main` for prod). ⚠️ `jasonwpalmer-com` is the public site → 🔴 red: do not `--prod` without explicit sign-off.
   - **C — laptop deploy scripts (Who’s Starting and <product-b>):** inspect the current project contract and cron worktree first. Who’s Starting uses `scripts/deploy-prod.sh`; <product-b> uses the sanctioned `scripts/nightly-local.sh deploy-only` from its clean, synced cron checkout. GitHub Actions do not deploy these sites. Never fall back to manual wrangler from another checkout. Preserve the project's push-before-deploy and regenerated-data rules. A push can be picked up by the next scheduled deployment, so it is production scope.


2. **Build first.** Run the project's `build` script (for `<product-b>` the app lives in `site/`). If the build fails, **abort** — report the error and do not deploy.

3. **Quality gate (before any deploy — preview *and* prod).** A proportionate pre-ship sanity check, per `~/projects/CONVENTIONS.md`:
   - **For `--prod`, public sites, or client-facing apps: run the full `/preflight` checklist first** (build, secrets, sample-data names, truth scan, iOS base64, cache-bust, 390×660 visual test, diff review). PREFLIGHT FAIL = do not deploy.
   - **Build clean** — covered by step 2; a broken build already aborts here. Never deploy a broken build.
   - **Review the diff.** Run `/code-review` on the working diff (the uncommitted/un-deployed changes) at **low/medium effort** — this is a sanity check, not a full audit. (Skip if `/preflight` just ran — it includes this.)
   - **Decision rule.** If the review surfaces **blocking / high-severity** findings, **STOP** — report them to the operator and let him decide whether to proceed; do not deploy. If clean (or only minor findings), continue to deploy. This gate doesn't replace the `--prod` confirmation below — prod still requires `--prod` + explicit sign-off on top of passing the gate.

4. **Deploy to PREVIEW by default** (patterns A/B). Use the project's own script/branch convention. Only on `--prod`: first state exactly what will go live, get an explicit yes, *then* run `deploy:prod` (branch `main`).

5. **Laptop-deploy projects (pattern C).** Use the current sanctioned script after the authorized source commit is on the correct remote branch. Check active processes and locks first. Never edit the cron checkout or use an unrelated worktree's deploy command. A green push is not deployment proof; inspect the script exit and feature evidence on bare production URLs.

6. **Verify after deploy.** `curl -sS -o /dev/null -w "%{http_code}"` the resulting URL (preview or prod) and report status — or run the project's smoke check if it has one. For pattern C, verify the script result and bare URLs, including a feature/build marker and each changed route. **our-place only:** if a migration is pending, remind him to run `npm run db:migrate` — but **do not run it** (🔴 red).

7. **Print the live preview URL** (the deployed `*.pages.dev` URL from wrangler's output, or the production domain on `--prod`) as the last line.

8. **Search engines — immediately, on any prod deploy that adds/changes public pages** (the operator standing rule 2026-08-31, [[feedback_seo_on_new_pages]]): (a) verify the sitemap carries every new URL with a sane lastmod (regenerate via the project's sitemap step if not); (b) run `node ~/.claude/tools/seo/sitemap-autopilot.mjs --site <domain>` — audits, IndexNow-pings Bing+partners, submits to Google Search Console; (c) live-probe EACH new URL: 200 not 3xx, real title, self-canonical, no noindex; (d) report the submit + probe results in the ship summary. Never for preview deploys, and only for public sites registered in `~/.claude/tools/seo/sites.json` (a new public site gets registered via `/site-launch` §5 first).

Keep it tight: reuse existing scripts, stop at preview unless told otherwise, and never touch DB migrations or the public site's prod without a clear go-ahead.
