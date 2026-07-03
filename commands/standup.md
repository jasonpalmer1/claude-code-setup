---
description: Read-only portfolio cockpit — git status of every active project at a glance, with a suggested next action for each
argument-hint: "(no args — scans all active repos under your projects root)"
---

Show the state of the whole project fleet in one table so the user can decide what to work on next. This is a **morning-cockpit** view: where every project stands and the single most obvious next move for each.

**READ-ONLY — hard rule.** This command *only reports*. It must **never** modify any repo: no `add`, `commit`, `push`, `pull`, `fetch`, `checkout`, `stash`, or anything that touches working trees or remotes. Pure inspection. If you think a repo needs action, *suggest* it in the table — don't do it (that's what `/ship` etc. are for).

Source of truth for what's active vs archived: a conventions file at the projects root, if one exists (e.g. `~/projects/CONVENTIONS.md`). Scan only git repos directly under the projects root; **skip non-git dirs** and skip anything the conventions file marks archived.

**Delegable.** The scan is mechanical, cheap, and self-contained — hand it to a cheap-tier subagent and have it return only the gathered rows. The main model just infers the next-action column and formats the table.

Steps:

1. **Scan in one shell loop** (not many round-trips). For each git repo directly under the projects root, gather: name, current branch, uncommitted count, ahead/behind, last-commit age. Something like:

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

2. **Infer one suggested next action per project** — terse, imperative, and **DEPLOY-AWARE**. Deploy models differ per project (document yours in the conventions file):
   - **explicit-deploy projects** — git push is neutral; deploy is a separate explicit step (e.g. `/ship`).
   - **push-to-deploy projects** — **push to `main` IS a production deploy** (CF Pages Git integration / GitHub Action). Never suggest a casual "push"; for WIP suggest a preview branch / PR.

   Mapping:
   - uncommitted changes → `commit WIP (N files)` (committing is safe — it does not deploy)
   - clean + ahead, explicit-deploy project → `deploy via /ship`
   - clean + ahead, push-to-deploy project → `⚠ push = PROD deploy — use a preview branch unless shipping`
   - clean + behind → `pull`
   - clean + no upstream → `set upstream / push branch (mind deploy trigger)`
   - clean + up-to-date → `read CLAUDE.md → "Current focus / next steps"`

3. **Output a tight Markdown table**, sorted **most-recently-active first** (by last-commit timestamp):

   | Project | Branch | Δ uncommitted | ahead/behind | last commit | suggested next |
   |---|---|---|---|---|---|

   Keep columns terse (e.g. `↑2 ↓0`, or `—` for no-upstream). Don't pad with prose.

4. **End with a one-line summary**, e.g. `3 projects have uncommitted work; 2 ahead of remote; <project> most recently active.`

Keep it scannable and fast — a cockpit, not a report. No repo is ever modified.
