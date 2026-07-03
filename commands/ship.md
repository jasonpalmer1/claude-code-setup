---
description: Deploy-and-verify a Cloudflare Pages project — safe by default (preview, never prod without --prod)
argument-hint: "[--prod]  (default: preview; --prod requires explicit confirmation)"
---

Deploy the project in the **current working directory** to Cloudflare Pages and verify it's live. Reuse the project's *existing* deploy scripts — never invent new deploy logic.

Args: `$ARGUMENTS` → `--prod` means "promote to production." Absence of `--prod` = **preview** (the safe default).

**Safety contract.** Deploys default to PREVIEW. Never promote to production unless `--prod` is passed **and** you've explicitly confirmed with the user in this turn. DB migrations are hard-off — never run them automatically.

Steps:

1. **Detect the project + its deploy method** by reading `package.json` (+ any `wrangler.*`, `next.config.*`, `.github/workflows/`). Three common patterns:
   - **A — npm deploy scripts:** `deploy:preview` / `deploy:prod` already wrap `wrangler pages deploy dist --branch preview|main`. Use them verbatim.
   - **B — manual wrangler, no script** (e.g. a Next.js site with `output: "export"` → `out/`): build, then `wrangler pages deploy out --project-name <name> --branch preview` (or `--branch main` for prod). ⚠️ Treat any public-facing production site as requiring explicit sign-off before `--prod`.
   - **C — git push triggers a CF Pages deploy** (Git integration or a GitHub Action on push to `main`). Here **shipping = commit + push.** See step 5.

2. **Build first.** Run the project's `build` script. If the build fails, **abort** — report the error and do not deploy.

3. **Quality gate (before any deploy — preview *and* prod).** A proportionate pre-ship sanity check:
   - **Build clean** — covered by step 2; a broken build already aborts here. Never deploy a broken build.
   - **Review the diff.** Run `/code-review` on the working diff (the uncommitted/un-deployed changes) at **low/medium effort** — this is a sanity check, not a full audit.
   - **Decision rule.** If the review surfaces **blocking / high-severity** findings, **STOP** — report them and let the user decide whether to proceed; do not deploy. If clean (or only minor findings), continue to deploy. This gate doesn't replace the `--prod` confirmation below — prod still requires `--prod` + explicit sign-off on top of passing the gate.

4. **Deploy to PREVIEW by default** (patterns A/B). Use the project's own script/branch convention. Only on `--prod`: first state exactly what will go live, get an explicit yes, *then* run the prod deploy (branch `main`).

5. **Git-push projects (pattern C).** Prod is automatic on push to `main`, so:
   - **no flag (default):** push to a **preview branch** (e.g. `preview/ship-<date>`) or open a PR — never push straight to `main`.
   - **`--prod`:** after explicit confirmation, commit + push to `main` (CI deploys prod). Mention that the CI run is what actually deploys; the live site lags the push by the run time.

6. **Verify after deploy.** `curl -sS -o /dev/null -w "%{http_code}"` the resulting URL (preview or prod) and report status — or run the project's smoke check if it has one. For pattern C, poll the Action / URL until it's live (or tell the user to watch the run). If a DB migration is pending, remind the user to run it — but **do not run it yourself**.

7. **Print the live preview URL** (the deployed `*.pages.dev` URL from wrangler's output, or the production domain on `--prod`) as the last line.

Keep it tight: reuse existing scripts, stop at preview unless told otherwise, and never touch DB migrations or a public site's prod without a clear go-ahead.
