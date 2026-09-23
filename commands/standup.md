---
description: Read-only portfolio cockpit — git status of every active project at a glance, with a suggested next action for each
argument-hint: "(no args — scans all active repos under ~/projects)"
---

Show the operator the state of his whole fleet in one table so he can decide what to work on next. This is a **morning-cockpit** view: where every project stands and the single most obvious next move for each.

**READ-ONLY — hard rule.** This command *only reports*. It must **never** modify any repo: no `add`, `commit`, `push`, `pull`, `fetch`, `checkout`, `stash`, or anything that touches working trees or remotes. Pure inspection. If you think a repo needs action, *suggest* it in the table — don't do it (use `/ship` etc. yourself).

Source of truth for what's active vs archived: `~/projects/CONVENTIONS.md`. Scan only git repos directly under `~/projects/`; **skip non-git dirs** and **skip `~/trading` entirely** (archived).

**Delegable.** The scan is mechanical, cheap, and self-contained — hand it to a **Haiku subagent** and have it return only the gathered rows. Opus just infers the next-action column and formats the table.

Steps:

1. **Scan in one shell loop** (not many round-trips). For each git repo directly under `~/projects/`, gather: name, current branch, uncommitted count, ahead/behind, last-commit age. Something like:

   ```bash
   for d in ~/projects/*/; do
     [ -d "$d/.git" ] || continue
     name=$(basename "$d")
     br=$(git -C "$d" rev-parse --abbrev-ref HEAD 2>/dev/null)
     dirty=$(git -C "$d" status --porcelain | wc -l | tr -d ' ')
     ab=$(git -C "$d" rev-list --left-right --count @{u}...HEAD 2>/dev/null || echo "no-upstream")
     age=$(git -C "$d" log -1 --format=%cr 2>/dev/null)
     ts=$(git -C "$d" log -1 --format=%ct 2>/dev/null)
     printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$ts" "$name" "$br" "$dirty" "$ab" "$age"
   done | sort -rn
   ```

   `git rev-list --left-right --count @{u}...HEAD` prints `behind<TAB>ahead`. When there's no upstream the command errors → treat as **no-upstream** and say so gracefully (don't show a bogus 0/0).

2. **Infer one suggested next action per project** — terse, imperative, and **DEPLOY-AWARE**. Deploy models differ (see the `reference_deploy_mechanisms` memory / `~/projects/CONVENTIONS.md`):
   - **our-place** — git push is neutral; deploy is the explicit `/ship` step.
   - **<product-a> and <product-b>** — approved laptop scripts deploy from their designated checkout; scheduled jobs consume origin/main, so a push may ship at the next tick. Never describe GitHub Actions as their deployment path. For WIP suggest an isolated branch; for a release follow the current project contract and script.
   - **<your-domain>** — verify the current deployment contract before recommending any push; do not infer its mechanism from another project.

   Mapping:
   - uncommitted changes → `commit WIP (N files)` (committing is safe — it does not deploy)
   - clean + ahead, our-place → `deploy via /ship`
   - clean + ahead, push-to-deploy project → `⚠ push = PROD deploy — use a preview branch unless shipping`
   - clean + behind → `pull`
   - clean + no upstream → `set upstream / push branch (mind deploy trigger)`
   - clean + up-to-date → `read CLAUDE.md → "Current focus / next steps"`

3. **Output a tight Markdown table**, sorted **most-recently-active first** (by last-commit timestamp):

   | Project | Branch | Δ uncommitted | ahead/behind | last commit | suggested next |
   |---|---|---|---|---|---|

   Keep columns terse (e.g. `↑2 ↓0`, or `—` for no-upstream). Don't pad with prose.

4. **End with a one-line summary**, e.g. `3 projects have uncommitted work; 2 ahead of remote; <product-a> most recently active.`

Keep it scannable and fast — a cockpit, not a report. No repo is ever modified.
