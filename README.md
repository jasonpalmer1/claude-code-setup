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
Code itself perform the whole install, personalized to you via a short interview. There's also
a scripted `install.sh` if you'd rather run something deterministic yourself — see
"Installing with the script" below.

## The two-file contract: OPERATOR.md + CLAUDE.md

Everything here rests on a split between two files, both always-loaded:

- **`OPERATOR.md`** is the AI-agnostic contract — how you want to be treated, what needs your
  sign-off, the proof-not-claims standard, and your red lines. It's written so you could hand it
  to a different AI tool entirely and only need to edit its one small "adapter" section at the
  bottom. `CLAUDE.md`'s first line (`@OPERATOR.md`) is what pulls it into every session.
- **`CLAUDE.md.template`** (→ your real `CLAUDE.md`) is the harness-specific "constitution" —
  the memory-tier design, delegation rules, token economy, and hub mechanics that make the
  contract above actually run inside Claude Code. `CLAUDE.md` in this repo's own root is a real,
  filled-in worked example of the template, kept side by side so you can see the difference
  between the design doc and a day-to-day copy.

Edit `OPERATOR.md` first — it's the one with your name, your red lines, your approval default.
`CLAUDE.md.template` mostly works as shipped; its placeholders are structural (a memory
directory path), not personal.

## Running AI safely when you're not an engineer

I don't read every line an agent writes before it runs — that's the whole point of directing one
instead of doing the work by hand. What follows isn't a security essay; it's the practical habits
that make it safe to hand over that much control, plus the mechanical backstops so the habits
hold on a day I'm moving fast and would otherwise skip a step.

**Permission modes are a gate I pick on purpose, not a default I tolerate.** The mode should
match the task's risk, not whatever I happened to be in last: a quick local edit can run loose,
but anything hard to reverse or outward-facing gets a mode that stops and asks first. "Ask before
doing this" is a feature I lean on, not friction to route around — and since that rule is easy to
skip under time pressure, `hooks/mcp-guard.py` makes the highest-risk case (money, mass outbound,
destroying live state) a hard block instead of a prompt I could talk myself past, unless I
explicitly confirm it in that exact conversation.

**A written, explicit never-do list is a real file, not something I trust myself to remember.**
Anything an agent should never touch or publish — a project not ready to be named publicly, a
class of data that shouldn't leave the machine — goes in a file every agent touching that kind of
work is told to read, the same way this repo's own do-not-touch registry works. Writing it down
is what lets an agent under a deadline, and a future me who's forgotten the context, both get it
right — neither can be trusted to "just remember."

**The pass that produces content should never be the pass that clears it for release.** An agent
optimizing to finish a task is a bad judge of whether that same output is safe to publish — it's
motivated to be done, not to doubt itself. A second pass, in fresh context, told explicitly to
read the output adversarially ("hunt for exactly what the never-do list forbids, like a
leak-scanner, not a collaborator") catches what the first pass won't. `hooks/safety-guard.py` is
the mechanical floor under the same idea: it blocks a secret-shaped string or a credential literal
from ever reaching a file, on every write, so the adversarial pass isn't the only thing standing
between a mistake and a commit — and `hooks/security-tripwire.py` checks, on every session start,
that guards like that one are actually still firing, since a control that's quietly stopped
working is worse than no control at all.

**A subagent's report describes what it intended, not necessarily what happened.** Before
anything ships, I check the actual diff, the actual file, the actual live page — not the summary
of it. That's the same discipline behind the standing pre-ship checklist I run before anything
public or client-facing (`/preflight` in this repo): a distilled list of the mistakes that have
actually shipped before — a secret in a diff, an unverified claim in public copy, realistic-looking
demo data, a stale cache serving the old page, a layout bug only a real phone shows. Running it
mechanically, every time, is cheaper than living through the same incident twice.

**None of this is affordable if every check runs on the expensive model.** Routing mechanical
work — scans, read-throughs, first-pass drafts — to a cheaper, faster tier and saving the
expensive one for judgment calls and reviewing what the cheap tier produced
(`hooks/model-guard.py` makes that choice explicit, not optional) is what makes it realistic to
run enough of the above instead of skipping half of it to save money.

**And I close out work in disposable, bounded chunks instead of letting one conversation run for
days.** A fresh session with a clear scope is something I — or a second reviewer — can actually
audit end to end; a context accumulating for a week is not.

## What's here

- **`ONBOARDING.md`** — two paste-prompts that have Claude Code install and personalize all of
  this for you (fresh-install and merge-into-existing variants).
- **`CLAUDE.md.template`** — global instructions (the "constitution"): the tiered memory-system design, the mechanical delegation rules, token-economy levers, index-first exploration, and workflow + security principles. Sanitized — add your own specifics.
- **`commands/`** — custom slash commands:
  - `/hub` — mission-control dispatcher: one chat that triages everything you paste and delegates the rest, with disk-durable state (see "One-Chat Hub" below)
  - `/index` — create/update a project's `CLAUDE.md` codebase map
  - `/log` — summarize the current session into a persistent conversation log
  - `/checkpoint` — run closing rites on demand (extraction sweep → log → disk state → the exact next keystroke) in any chat, not just the hub
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
  - `/hub-audit` — monthly adversarial self-audit of the hub: a fresh, context-free worker hunts for drift, stale rules, and self-flattering bookkeeping across the board, ledger, and memory
  - `/forge` — proactive idea generation from your OWN inventory (warm channels, live assets, reusable stack), pre-screened against your own settled kills — the generative counterpart to `/triage`
  - `/selfaudit` — mines your own accumulated logs/memory/ledger for behavior corrections nobody's written down yet, so self-improvement doesn't require you to notice and ask for it
- **`hooks/`** — automation:
  - `index-reminder.sh` (PostToolUse) — nudges you to map a project that has no `CLAUDE.md`
  - `session-end-log.sh` (SessionEnd) — auto-summarizes the session (on a cheaper model) and appends a usage row
  - `token-ledger.py` — parses a session transcript (and its subagents' transcripts) and logs per-model token spend (pure parsing, no model call, costs nothing); also rolls up spend across a whole peer-session spawn tree if sessions tag their parent (see the file's header comment)
  - `context-firewall.py` (PostToolUse) — nudges the main loop when it's doing bulk direct reads instead of delegating them
  - `instructions-bloat-check.py` (SessionStart) — warns when the current project's `CLAUDE.md` has crossed a size cap, since it's re-sent (and re-billed) on every turn of every session working there
  - `mcp-guard.py` (PreToolUse) — hard-blocks an MCP tool call that moves money, sends mass outbound, or destroys live state unless explicitly bypassed for that one call — the enforcement backstop behind a written connector risk policy
  - `model-guard.py` (PreToolUse) — blocks any subagent spawn with no explicit model, so a spawn can never silently inherit your most expensive tier by accident
  - `safety-guard.py` (PreToolUse) — blocks a small set of catastrophic shell commands and a secret/credential literal from ever reaching a file, on every write
  - `security-tripwire.py` (SessionStart) — re-verifies, on every session start, that controls like `safety-guard.py` are actually still firing, and scans your tracked config files for a credential-shaped literal before it gets pushed
  - `cost-meter.py` (UserPromptSubmit) — a live running-cost warning during a session (not just after it ends), so a long session gets flagged while there's still time to wrap it up
  - `transcript-monitor.py` (UserPromptSubmit) — warns when the transcript itself has grown large enough that a checkpoint is due, independent of dollar cost
  - `pre-compact-archive.sh` (PreCompact) — copies the raw transcript aside before compaction, cheap insurance against detail a summary would drop
  - `session-autoname.py` (UserPromptSubmit) — renames a session after what it's actually working on, so a list of concurrent sessions is legible at a glance
  - `inbox-capture.sh` (UserPromptSubmit) — ambient, secret-scrubbed raw capture of every prompt, as a fallback feed a memory-extraction sweep can diff against
  - `pattern-capture-check.sh` (SessionEnd) — logs whether your pattern journal (see the Memory autopilot section of `commands/hub.md`) gained a same-day entry, for a staleness nudge elsewhere to key off
  - `memory-activate.py` / `memory-graph-refresh.sh` / `memory-health-nag.py` — an optional evolution of the flat memory index into a small linked graph with spreading-activation retrieval (see "Memory graph" below)
- **`agents/`** — the three-gate workflow as typed sub-agent definitions: `planner.md` (plan
  before any new scope), `playtester.md` (user-perspective pass after a build), `bug-gate.md`
  (correctness check on the exact diff before it ships), and `search-demand.md` (checks real
  search demand before a new page/site). Spawn them with the Agent tool, one task each, model
  stated explicitly every time.
- **`bin/`** — small CLI helpers the hub/board pattern leans on: a session-lease pair
  (`hub-claim`/`hub-who`, so two sessions never silently collide on one repo), a `ledger` CLI
  over the token-ledger data, and `disk-guard` (a threshold check before anything that deletes
  or frees disk space). Each ships with its own test file — run those after any edit.
- **`fleet/`** — `spawn`/`run` plus `lib.sh`: the peer-session pattern from `CLAUDE.md.template`'s
  "Peer sessions" section made concrete — launching a genuinely separate chat identity (not just
  an in-session subagent) for a long-lived, independently-drivable lane of work.
- **`templates/`** — drop-in file skeletons, e.g. `hub-board.md`, the empty board the One-Chat Hub reads and writes (see below).
- **`routines/`** — templates for scheduled, headless Claude Code runs (a Monday portfolio-cockpit report, a weekly token review, a weekly `CLAUDE.md` bloat-surgery pass), example `launchd` job definitions, and notes on keeping tool grants narrow.
- **`settings.json.template`** — how to register every hook in this kit, with a deliberately
  minimal `permissions` block (nothing autonomous, no deploy permissions) — add your own as you
  earn trust in a task. `settings.example.json` is kept as a smaller worked example of the same
  idea for just the original four core hooks.

## Setup

Hands-off path: paste a prompt from [`ONBOARDING.md`](ONBOARDING.md) and Claude does all of
this for you — recommended, since it also personalizes the config via a short interview and
can merge intelligently into an existing setup.

### Installing with the script

If you'd rather run something deterministic yourself:

```sh
git clone https://github.com/jasonpalmer1/claude-code-setup.git
cd claude-code-setup
./install.sh                 # fresh install into ~/.claude
./install.sh --merge         # additive: never overwrites a file you already have
./install.sh --dest DIR      # install somewhere other than ~/.claude
```

It copies every file above into place, `chmod +x`'s the shell/Python/JS hooks, creates an
empty memory directory (`MEMORY.md` + `ARCHIVE.md`), an empty hub board, an empty token ledger,
and an empty `safety-guard.local.json` extension point — then prints exactly what it did **not**
set up (no git remote, no deploy credentials, no autonomous permission mode). It asks exactly
one question, with a scriptable default: run in auto mode (keeps going without asking, the
default) or conservative (`--conservative`, asks before edits). It never touches a git remote
and never installs anything that pushes on your behalf.

### Installing by hand

1. Copy `CLAUDE.md.template` → `~/.claude/CLAUDE.md` and `OPERATOR.md` → `~/.claude/OPERATOR.md`, then fill in the placeholders in both (see "The two-file contract" above).
2. Copy `commands/`, `agents/`, `bin/`, `fleet/`, and `hooks/` → the matching directories under `~/.claude/`, then `chmod +x` the shell/Python/JS ones. Copy `routines/` too if you want scheduled runs.
3. Copy `settings.json.template` → `~/.claude/settings.json`, editing paths/permissions as needed (or merge just the hook entries from it into a settings.json you already have).
4. Create your memory directory and an empty `MEMORY.md` index inside it — the path is `~/.claude/projects/<your-home-path-with-slashes-replaced-by-dashes>/memory/`.
5. Copy `templates/hub-board.md` → `~/.claude/hub/board.md` if you want the One-Chat Hub.

## Why it's built this way

I delegate anything that doesn't need top-tier reasoning to a cheaper model — file searches, mechanical edits, project maps, portfolio scans — and reserve the expensive model for judgment calls and reviewing what the cheaper model produced. Token cost is a first-class constraint here, not an afterthought: batching independent tool calls, avoiding mid-session edits that bust the prompt cache, and logging spend per session with a hook are what make it possible to run several agents without losing track of the bill. Memory is just files, one fact per file, indexed by a single markdown table of contents, because that's a substrate an agent can read, update, and reason about directly, with no database or service to run. It's plain text, it diffs cleanly, and nothing in it goes stale without leaving a trace.

### Tiered memory

The single biggest change in this iteration: memory isn't one flat, ever-growing index. It's three tiers:

- **Tier 1 (`MEMORY.md`)** is the only thing every session pays for — cap it hard. Every line in it should answer yes to "does every future session need this?"
- **Tier 2** is project-local: a `## Session memory` section inside each project's own `CLAUDE.md`. It's free most of the time (it only loads when you're actually working in that project) and it's exactly where a subagent doing index-first exploration will find it.
- **Tier 3 (`ARCHIVE.md`)** holds anything dormant — killed ideas, wound-down projects, rarely-needed references — indexed but never auto-loaded. Nothing gets deleted, just demoted out of the context every session pays for.

The effect: the always-loaded index stays small no matter how much history accumulates, because history has somewhere else to live.

**Memory graph (optional evolution).** Once you have enough memory files that a flat index stops being enough — you can't remember which file covers what, or two files have quietly drifted into saying contradictory things — the index can grow a lightweight graph of typed links between memory files instead of staying a plain table of contents: a small `graph/build.py` that derives the links, and a `graph/retrieve.py` that does spreading-activation retrieval — given the session's working directory and opening prompt, activate a few seed files and spread across their links to surface the handful actually relevant right now, instead of loading the whole index every turn. The same graph can carry a `contradicts`/`superseded` flag between two memories, so a session is told about a live contradiction instead of silently picking one side. `hooks/memory-activate.py` is the retrieval hook that injects this pointer block at session start; `hooks/memory-graph-refresh.sh` debounces a background rebuild whenever a memory file changes, instead of waiting on a weekly job; `hooks/memory-health-nag.py` surfaces one line, only when something's actually wrong, from a health-check log a `graph/maintain.py` script would write. This trio is scaffolding around a small graph engine you write yourself once you're at the scale that needs it — not a database, just another layer of plain files.

### Delegation philosophy

Two mechanical rules, backed by the context-firewall hook, because prose rules alone get skipped under time pressure:

1. **Every subagent call states its model explicitly.** Don't rely on a default — an unspecified model can silently inherit your most expensive tier, and it's easy not to notice until the bill does.
2. **Default to the cheapest capable tier for read-shaped work** — searching, extracting, summarizing, mechanical edits — and reserve the mid tier for actual code/config changes that need judgment. Escalate on a verified failure, not by reflex.

`hooks/context-firewall.py` backs rule 2 mechanically: a `PostToolUse` hook on `Read`/`Bash` that fires only in the main loop (subagent transcripts are excluded — subagents are supposed to read directly) and nudges you to delegate when a single result is large or when direct reads keep piling up. It's a nudge, not a block — it feeds Claude an additional-context note, it doesn't fail the tool call.

### Headless routines

`routines/` templates a pattern for recurring reports that show up without you asking: a shell script that calls `claude -p "<prompt>" --model <tier> --allowedTools "<narrow list>"`, scheduled by a `launchd` plist (the macOS mechanism; substitute cron/systemd elsewhere). Because a headless run has no one watching to approve a permission prompt, the tool grant should be the minimum that lets the routine do its one job — a fixed read-only script by exact path, a couple of read-only command prefixes, write access to exactly one output file. See `routines/README.md` for the full pattern and what to fill in.

### Peer sessions

The delegation rules above (state the model, cheap tier for read-shaped work) govern subagents *within* one session. If your harness can also spawn genuinely separate peer sessions — their own chat identity, possibly their own working directory — the same economic logic extends one level up: a long-lived, independently-drivable lane of work (a second project, a build worth watching progress on) is often cheaper handed to a fresh peer session than ground through in one context that keeps growing. The goal is keeping your best model available for the most time, not parallelism for its own sake.

Peer sessions need their own guardrails or they're easy to under-govern: every session-hygiene rule still applies to each one (a "long-lived lane" is a long-lived chat identity that logs and clears on its own cadence, never a context resumed for days on end); the autonomy tier travels with the *task*, not the session, so a permissive spawn can never be used to route around a permission the delegating session would itself be refused; and cost gets judged on the whole spawn tree, not session-by-session, which is what the parent-tagging feature in `hooks/token-ledger.py` is for. Full pattern in the "Peer sessions" section of `CLAUDE.md.template`.

### Installing on a second machine

If you run Claude Code from more than one machine on the same account, the fastest path is
`install.sh --merge` from this repo (or your private fork of it) on the new machine — it's
additive by design, so it never overwrites anything already there and just reports what it
skipped. Two things make that safe across machines: every hook uses `$HOME`/`~`, never a
hardcoded home directory, so the same tracked files work under a different username with zero
edits; and `install.sh` only ever touches files under its own `--dest` (default `~/.claude`) —
it never reaches into a project directory or installs anything machine-specific like
screenshot-testing binaries (install those separately, same as any other dependency).

If you keep your own fork with real customizations, the same idea works as a private git repo:
mirror it onto the second machine, clone over `~/.claude` (or run `install.sh --merge` from it),
verify hooks fire with a sample payload, and keep a whitelist-style `.gitignore` — ignore
everything, opt in `CLAUDE.md`/`OPERATOR.md`/`settings.json`/`commands/`/`agents/`/`bin/`/
`fleet/`/`hooks/`/`templates/`/`routines/`/the memory tree explicitly, so caches, transcripts,
and credentials never get swept in by accident. The one machine-specific thing worth calling
out: if your harness keys its per-project data directory off the OS username, bridge that with
a local, untracked symlink rather than editing tracked paths per machine.

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
