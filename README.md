# Claude Code — power-user setup

[![Claude Code](https://img.shields.io/badge/Claude_Code-D97757?style=flat-square&logo=anthropic&logoColor=white)](https://claude.com/claude-code)

An opinionated [Claude Code](https://claude.com/claude-code) configuration built around four ideas:

1. **Tiered, file-based memory** — a small always-loaded index, project-local memory that lives in each project's own `CLAUDE.md`, and an archive tier for anything dormant. Right context, right time, instead of one memory file that grows forever.
2. **Cost discipline** — mechanical delegation rules (state the model explicitly on every subagent call, default to the cheapest capable tier for read-shaped work), a context-firewall hook that nudges away from bulk reads in the expensive main loop, and a token-ledger hook that measures spend per session so you can tune from data.
3. **Self-maintaining project maps** — an `/index` command and a hook that nudges you to keep a `CLAUDE.md` codebase map in every project.
4. **Headless routines** — scheduled, unattended Claude Code runs (via `launchd`/cron) for recurring reports, with deliberately narrow tool grants since nobody's watching to approve a prompt.

It's a template: copy what's useful, fill in the `<PLACEHOLDERS>`, delete the rest.

**Setting it up for the first time?** Don't install by hand — [`ONBOARDING.md`](ONBOARDING.md)
has two copy-paste prompts (fresh install, or merge into an existing setup) that make Claude
Code itself perform the whole install, personalized to you via a short interview.

## What's here

- **`ONBOARDING.md`** — two paste-prompts that have Claude Code install and personalize all of
  this for you (fresh-install and merge-into-existing variants).
- **`CLAUDE.md.template`** — global instructions (the "constitution"): the tiered memory-system design, the mechanical delegation rules, token-economy levers, index-first exploration, and workflow + security principles. Sanitized — add your own specifics.
- **`commands/`** — custom slash commands:
  - `/hub` — mission-control dispatcher: one chat that triages everything you paste and delegates the rest, with disk-durable state (see "One-Chat Hub" below)
  - `/index` — create/update a project's `CLAUDE.md` codebase map
  - `/log` — summarize the current session into a persistent conversation log
  - `/tokens` — review the token ledger, spend trends, and a delegation-ratio metric
  - `/ship` — deploy-and-verify a web project, safe by default (preview, never production without an explicit flag + confirmation), gated on `/preflight` before anything public/client-facing
  - `/preflight` — pre-ship checklist covering common shipping-incident classes: real-looking names in demo data, unverified claims in public copy, oversized inline images breaking mobile Safari, stale-cache asset swaps, secrets in the diff, and mobile-viewport visual testing
  - `/shot` — screenshot a URL at phone size with a permanent headless-Chrome rig — the visual-test primitive behind `/preflight`
  - `/standup` — read-only portfolio cockpit: git status of every active project at a glance, with a suggested next action for each
  - `/pulse` — traffic pulse across your live sites (human vs. bot visits), if distribution is a constraint you track
  - `/triage` — cheap pre-filter for a new idea against your own settled-kills file and veto patterns, before burning a full viability-research pass
  - `/client-brief` — draft a client-facing progress update (plain English + screenshots) from recent git history
  - `/commands` — routes "what should I use for X" to the right command, or prints the full cheat-sheet
  - `/new-project` — scaffold a new project matching a set stack convention, end to end (scaffold → codebase map → deploy scripts → first commit)
- **`hooks/`** — automation:
  - `index-reminder.sh` (PostToolUse) — nudges you to map a project that has no `CLAUDE.md`
  - `session-end-log.sh` (SessionEnd) — auto-summarizes the session (on a cheaper model) and appends a usage row
  - `token-ledger.py` — parses a session transcript (and its subagents' transcripts) and logs per-model token spend (pure parsing, no model call, costs nothing)
  - `context-firewall.py` (PostToolUse) — nudges the main loop when it's doing bulk direct reads instead of delegating them
- **`templates/`** — drop-in file skeletons, e.g. `hub-board.md`, the empty board the One-Chat Hub reads and writes (see below).
- **`routines/`** — templates for scheduled, headless Claude Code runs (a Monday portfolio-cockpit report, a weekly token review), example `launchd` job definitions, and notes on keeping tool grants narrow.
- **`settings.example.json`** — how to register the hooks.

## Setup

Hands-off path: paste a prompt from [`ONBOARDING.md`](ONBOARDING.md) and Claude does all of
this for you. By hand:

1. Copy `CLAUDE.md.template` → `~/.claude/CLAUDE.md` and fill in the placeholders.
2. Copy `commands/` → `~/.claude/commands/`, `hooks/` → `~/.claude/hooks/` (then `chmod +x` the shell hooks), and `routines/` → `~/.claude/routines/` if you want scheduled runs.
3. Merge `settings.example.json` into `~/.claude/settings.json`, editing paths as needed.
4. Create your memory directory and an empty `MEMORY.md` index inside it.
5. Search-and-replace the placeholders: `<MEMORY_DIR>` (where memory files live) and adjust the watched project roots in `index-reminder.sh`.

## Why it's built this way

I delegate anything that doesn't need top-tier reasoning to a cheaper model — file searches, mechanical edits, project maps, portfolio scans — and reserve the expensive model for judgment calls and reviewing what the cheaper model produced. Token cost is a first-class constraint here, not an afterthought: batching independent tool calls, avoiding mid-session edits that bust the prompt cache, and logging spend per session with a hook are what make it possible to run several agents without losing track of the bill. Memory is just files, one fact per file, indexed by a single markdown table of contents, because that's a substrate an agent can read, update, and reason about directly, with no database or service to run. It's plain text, it diffs cleanly, and nothing in it goes stale without leaving a trace.

### Tiered memory

The single biggest change in this iteration: memory isn't one flat, ever-growing index. It's three tiers:

- **Tier 1 (`MEMORY.md`)** is the only thing every session pays for — cap it hard. Every line in it should answer yes to "does every future session need this?"
- **Tier 2** is project-local: a `## Session memory` section inside each project's own `CLAUDE.md`. It's free most of the time (it only loads when you're actually working in that project) and it's exactly where a subagent doing index-first exploration will find it.
- **Tier 3 (`ARCHIVE.md`)** holds anything dormant — killed ideas, wound-down projects, rarely-needed references — indexed but never auto-loaded. Nothing gets deleted, just demoted out of the context every session pays for.

The effect: the always-loaded index stays small no matter how much history accumulates, because history has somewhere else to live.

### Delegation philosophy

Two mechanical rules, backed by the context-firewall hook, because prose rules alone get skipped under time pressure:

1. **Every subagent call states its model explicitly.** Don't rely on a default — an unspecified model can silently inherit your most expensive tier, and it's easy not to notice until the bill does.
2. **Default to the cheapest capable tier for read-shaped work** — searching, extracting, summarizing, mechanical edits — and reserve the mid tier for actual code/config changes that need judgment. Escalate on a verified failure, not by reflex.

`hooks/context-firewall.py` backs rule 2 mechanically: a `PostToolUse` hook on `Read`/`Bash` that fires only in the main loop (subagent transcripts are excluded — subagents are supposed to read directly) and nudges you to delegate when a single result is large or when direct reads keep piling up. It's a nudge, not a block — it feeds Claude an additional-context note, it doesn't fail the tool call.

### Headless routines

`routines/` templates a pattern for recurring reports that show up without you asking: a shell script that calls `claude -p "<prompt>" --model <tier> --allowedTools "<narrow list>"`, scheduled by a `launchd` plist (the macOS mechanism; substitute cron/systemd elsewhere). Because a headless run has no one watching to approve a permission prompt, the tool grant should be the minimum that lets the routine do its one job — a fixed read-only script by exact path, a couple of read-only command prefixes, write access to exactly one output file. See `routines/README.md` for the full pattern and what to fill in.

### Second machine

If you run Claude Code from more than one machine on the same account, the config in this repo (or your private fork of it) plus your memory directory can be kept in their own git repo and mirrored onto a second machine: a `SETUP.md` at the repo root that a fresh Claude Code session can follow step-by-step (clone the config over `~/.claude`, verify hooks fire, install anything not tracked in git like screenshot-testing binaries), a whitelist-style `.gitignore` (ignore everything, opt in `CLAUDE.md`/`settings.json`/`commands/`/`hooks/`/`templates/`/`routines/`/the memory tree explicitly, so caches, transcripts, and credentials never get swept in by accident), and username-agnostic paths throughout (`~`/`$HOME`, never a hardcoded home directory) so the same tracked files work under a different username with zero edits. The one machine-specific thing worth calling out: if your harness keys its per-project data directory off the OS username, bridge that with a local, untracked symlink rather than editing tracked paths per machine.

## One-Chat Hub

Most Claude Code sessions carry a hidden tax: which project to open, whether to resume or start fresh, whether you remembered to log before compacting, whether this transcript has quietly gotten expensive. That's the same handful of decisions paid every single sitting — a session-management tax charged to you instead of the tool.

The Hub is one interactive chat — auto-armed whenever an interactive session starts in `~` instead of inside a project directory — that takes those decisions over. Paste anything into it (a task, a bug, a link, three unrelated asks in one message) and it triages each item independently: answer inline, do it inline, or dispatch it to a background worker with an explicit model tier. It tracks what's running, verifies what comes back, works within your own autonomy limits, and does the bookkeeping (board, memory, log, token ledger) without being asked.

**Disk-durable is the core idea.** None of that state lives in the chat transcript — it lives in a board file, the tiered memory system, each repo's own `CLAUDE.md`, and the token ledger. The chat itself is disposable: clear it, lose it, let it get killed, and nothing is lost, because nothing it knows isn't already written down somewhere a fresh session can read.

### How the pieces fit

- **`commands/hub.md`** — the dispatcher protocol: on-start board greeting, per-item triage rules, dispatch rules (model tier, worker prompt template), what to do when a worker finishes, and two autopilots — hygiene (recognizing the right `/log` / `/compact` / `/clear` moment) and ledger (a daily spend pulse with unprompted tuning suggestions).
- **`templates/hub-board.md`** — the board itself: three sections (`ACTIVE`, `WAITING ON USER`, `LANDED`), one line per item. This is the disk-durable state the hub reads on every start and writes on every dispatch and completion.
- **The "Hub mode" section in `CLAUDE.md.template`** — the auto-activation trigger (interactive session, started in `~`, not inside a project dir → this is the hub) and its escape hatch (a message starting `inline:` stays in the current chat, no dispatch).
- **The `SessionEnd` ledger hook** (`hooks/session-end-log.sh` + `hooks/token-ledger.py`, already in this repo) — feeds the ledger autopilot; without it the hub has no spend data to reason about.

### Setup

1. Copy `commands/hub.md` → `~/.claude/commands/hub.md`.
2. Copy `templates/hub-board.md` → `~/.claude/hub/board.md` (create the `hub/` directory first).
3. Merge the "Hub mode" section into your `~/.claude/CLAUDE.md` (already there if you copied the whole `CLAUDE.md.template`).
4. Fill in your own do-not-touch registry and a routing source (the `<YOUR ROUTING SOURCE>` placeholder in `hub.md`) — wherever you track which project owns what, e.g. an entry in your `MEMORY.md`.
5. Confirm the `SessionEnd` hook from this repo is wired in `settings.json` — the ledger autopilot has nothing to read without it.

### Optional: Remote Control for phone use

If your harness has a remote-control/mobile mode, wire its arm command and constraints into the "Remote control (phone)" section of `hub.md`: the keystroke, what it requires (an account-based login, the terminal process staying alive), reconnect-after-outage behavior, push-notification setup, and which commands stay terminal-only. Skip this entirely if you only ever drive Claude Code from a terminal — the hub works exactly the same either way.

### Mine your own ledger

The hygiene-autopilot thresholds in `hub.md` are calibrated, not guessed. One real ledger audit found that multi-day resumed sessions carried roughly 98% of all spend, and every cost blowup spanned 3 or more days, while same-day sessions stayed cheap by comparison — the doctrine that falls out, **log-then-clear beats resume**, is the whole hygiene autopilot in one sentence. Once the hub has been running long enough to build up a real `token_ledger.md`, run the same audit on your own data (`/tokens` is a start) and feed what you find back into the thresholds in `hub.md`. The system is meant to tune itself from its own exhaust.

## Notes

Built and iterated with Claude Code itself. Nothing here contains secrets or machine-specific data — paths are placeholders.
