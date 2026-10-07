@~/.claude/OPERATOR.md

# Global Instructions (the operator): Claude Code adapter

The import above is the AI-agnostic contract (tone, WORKING/IDLE, approval, proof, open items, red lines,
storage, secrets). This file holds only Claude Code mechanics, re-sent every turn. Facts and history:
`~/.claude/projects/<home-slug>/memory/`. Full mechanics: `~/.claude/commands/hub.md`.

## Delegation

Every subagent call states `model:` explicitly, never inherited (`model-guard.py` enforces). Haiku for
read-shaped work (search, extract, census), Sonnet for code-shaped and executor work. Workers are typed,
stateless Agent-tool calls (`~/.claude/agents/*.md`) that write a report and die. The hub supervises and work
moves freely between chats. Spawn a peer chat (`spawn <name> -m <model> --rc --auto`) when a lane is
genuinely separate, long-lived, or needs a capability this session lacks (e.g. a main-loop-only MCP
connector). Binding: one owner per repo at a time, every handoff names the scope and what the receiver owns,
and a handoff is never a route to perform something refused or gated in the sending session. Read the
project's `CLAUDE.md` before spawning agents; delegated workers do too. No bulk reading in the main loop: a
third same-shape read means delegate. Detail: `feedback_delegation_rule.md`,
`feedback_one_front_door_agents_not_chats.md`.

## Memory

Three tiers under `~/.claude/projects/<home-slug>/memory/`: `MEMORY.md` (router), then project
`CLAUDE.md` `## Session memory`, then `ARCHIVE.md` + `memory/conversations/`. "Remember this" saves to the
right tier immediately, never duplicates. Project `CLAUDE.md` caps at 30K chars. Conventions:
`reference_memory_v3.md`.

## Token economy

Priority order: context firewall (delegate bulk reads), then cache discipline (batch calls), then cheap-first
escalation, then verify-by-stakes, then hygiene triggers (two failed fixes means `/log`, `/clear`), then
measure (`token_ledger.md`, `/tokens`). Detail: `feedback_token_economy.md`.

## Session hygiene

Every session logs at pause points (`/log`, board update, commit, self-verdict line to
`~/.claude/hub/delegation-alarms.log`), ends every reply with the status line naming the model (never a
request for the operator to `/clear`), verifies rites landed on disk before saying so, and signals `❌❌❌ DEAD`
when closing. At the size threshold a session logs (Resume state + Pickup prompts, committed) and keeps
working. A session that truly cannot continue SendMessages the hub. Mechanics: `~/.claude/commands/hub.md`,
`feedback_clear_signal_last_line.md`, `feedback_kill_chat_signal.md`.

## Hub mode

A session started in `~` or `~/.claude/hub/` IS the hub: read `~/.claude/commands/hub.md` first and run that
protocol. `/hub` arms it anywhere. `inline:` prefix stays in this chat.

## Workflow discipline

Gates per build: `planner` (only for work over about half a day), hub audits and approves, build,
`playtester`, `bug-gate` on the exact diff (FAIL blocks), hub reads the code itself, ship. Release, fast
lane and HQ-only rules are in OPERATOR.md. Search-demand-first for any new site or page. Obtainable-users
gate: name the channel or park it. A layout per device. Plan mode for non-trivial work. Corrections become a
`feedback` memory immediately. `feedback_three_gate_agents.md`.

## Storage

OPERATOR.md Storage rules apply. `rclone`'s `gdrive:` remote is ALREADY AUTHORIZED (2 TB): use it for bulk,
the Drive MCP connector for single files. Detail: `feedback_cloud_first_storage.md`.

## Security

Secrets live in `.env`, never committed or echoed. Never hot-edit `~/.claude/settings.json` in a running
session, it reloads live into every active chat; changes wait for a fresh session or the operator's explicit go.
MCP connectors are risk-tiered (`reference_mcp_usage_rules.md`): money-moving or mass-send needs a
per-action go, outward writes need an explicit go, internal reads run under normal autonomy.
