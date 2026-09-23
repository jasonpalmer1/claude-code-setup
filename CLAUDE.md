# Global Instructions (worked example)

This is a real, filled-in example of `CLAUDE.md.template` — not a second template. Read
`CLAUDE.md.template` first for the design and the placeholders; this file shows one way
they end up filled in day to day, so `OPERATOR.md` shows the same thing for the contract
layer. Copy the structure, not the specifics — the file paths, thresholds, and rule names
below are illustrative, not prescriptive.

`@OPERATOR.md` at the very top of your real `CLAUDE.md` is what makes the contract
auto-load into every session; this example starts from that same line.

## Delegation

Every subagent call states `model:` explicitly, never inherited (a `model-guard.py`
hook can enforce this mechanically). Cheapest tier for read-shaped work (search,
extract, census). Mid tier for code-shaped and executor work. Workers are typed,
stateless calls to files under `agents/` that write a report and exit. The hub
supervises and should act mainly as a dispatcher, not do all the work itself — but
still: one owner per repo at a time, every handoff names the scope and what the
receiver now owns, and a handoff is never a route to perform something refused or
gated in the sending session. Read the project's `CLAUDE.md` before spawning agents;
delegated workers do too. No bulk reading in the main loop — a third same-shape read
means delegate instead.

## Memory

Three tiers under your memory directory: `MEMORY.md` (the always-loaded router) →
each project's own `CLAUDE.md` under a `## Session memory` heading → an `ARCHIVE.md`
plus a `conversations/` log directory for anything dormant. "Remember this" saves to
the right tier immediately, never duplicates an existing entry. Keep the project-local
tier capped at a size you're comfortable re-sending every turn (a few tens of
thousands of characters is a reasonable starting cap).

## Token economy

In rough order of leverage: a context firewall (delegate bulk reads instead of paying
for them in the main loop) beats cache discipline (batch independent tool calls) beats
cheap-first escalation (start cheap, escalate only on a verified failure) beats
verify-by-stakes (heavier checks where the stakes are higher) beats hygiene triggers
(two failed fixes in a row means stop and log/clear) beats simply measuring spend and
tuning from data (`/tokens`, a token ledger hook).

## Session hygiene

Every session logs at natural pause points (`/log`, a board update, a commit) and ends
each reply with a short status line naming the model in use — never a request for you
to type `/clear` yourself. At a size threshold, a session writes its own resume state
(what's done, what's next) and keeps working; let your harness's own context/compaction
management handle the rest. A session that truly cannot continue hands off to whatever
supervises it (a hub session, or you directly) rather than just stopping silently.

## Hub mode

A session started in your hub directory (or wherever you designate as "not inside a
project") is the hub: it reads `commands/hub.md` and runs that protocol. A `/hub`
command can arm it anywhere; a message prefixed `inline:` stays in the current chat
with no dispatch.

## Workflow discipline

For anything non-trivial: a written plan first (a `planner` agent, or plan mode),
review and approval, then build, then a user-perspective playtest, then a bug check on
the exact diff (a hard FAIL blocks shipping), then a final human read of the actual
code or page before it goes live. Corrections you give the assistant become a
`feedback`-type memory immediately, not just a one-off chat aside.

## Storage

If you use cloud storage, state your own default here (e.g. "archives and finished
artifacts go to cloud storage; local disk holds only what's actively being worked
on"). Regenerable things (`node_modules`, build output, caches) are deleted, never
uploaded. Irreplaceable things are copied to the cloud and verified present before the
local copy is removed.

## Security

Secrets live in `.env` or a dedicated secret store, never committed or echoed into a
chat, log, or memory file. Never hot-edit your live `settings.json` from inside a
running session if your harness reloads it live into every active chat — let changes
wait for a fresh session. If you use MCP connectors, tier them by risk: money-moving or
mass-send actions need a per-action go-ahead; outward writes need an explicit go;
internal reads can run under your normal autonomy default.
