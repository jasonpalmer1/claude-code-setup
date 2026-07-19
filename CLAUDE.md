# claude-code-setup

Opinionated Claude Code configuration template built around tiered file-based memory, cost discipline, project indexing, and headless automation.

## Purpose
Power-user harness for Claude Code: mechanical delegation rules (explicit model per subagent call, default cheapest tier for read-shaped work), context-firewall hook to avoid bulk reads in expensive main loop, token-ledger tracking, and self-maintaining project maps (`CLAUDE.md` per repo).

## Architecture
- **Tier 1 (MEMORY.md)**: always-loaded index, ~40 lines max
- **Tier 2 (project-local)**: `CLAUDE.md` in each project's root
- **Tier 3 (ARCHIVE.md)**: dormant projects, killed ideas, rarely-accessed references
- **One-Chat Hub**: mission-control dispatcher in `~`, triages tasks to workers, maintains durable board

## Entry Points
- `README.md` — setup guide and design rationale
- `CLAUDE.md.template` — global instructions skeleton (copy to `~/.claude/CLAUDE.md`)
- `commands/hub.md` — Hub protocol and routing
- `hooks/context-firewall.py` — nudges away from bulk reads in main loop (PostToolUse)
- `hooks/session-end-log.sh` + `hooks/token-ledger.py` — cost tracking
- `settings.example.json` — hook registration template

## Tech Stack
- Shell (Bash) — hook runners, routine templates
- Python — token ledger parser (pure parsing, no model calls)
- launchd/cron — scheduled, headless routine triggers
- Claude Code CLI — dispatch targets (`-p`, `--model`, `--allowedTools`)

## Key Files & Directories
- `commands/` — slash commands: `/hub`, `/index`, `/log`, `/tokens`, `/ship`, `/preflight`, `/shot`, `/standup`, `/pulse`, `/triage`, `/client-brief`, `/new-project`
- `hooks/` — PostToolUse (firewall, index-reminder), SessionEnd (logging, ledger)
- `routines/` — templates for recurring reports (Monday cockpit, weekly token review)
- `templates/` — `hub-board.md` (disk-durable state), empty scaffolds

## Token Economy Principles
1. Every subagent call explicitly states `model:` (no inheritance of expensive defaults)
2. Default Haiku for read-shaped work (search, extract, summarize); Sonnet for code/config changes
3. Context firewall nudges bulk reads to subagents, keeps main-loop context lean
4. Cache discipline: batch independent calls, avoid mid-session edits, log spend per session

## Setup
1. Copy `CLAUDE.md.template` → `~/.claude/CLAUDE.md`, fill placeholders
2. Copy `commands/` → `~/.claude/commands/`, `hooks/` → `~/.claude/hooks/`, `routines/` → `~/.claude/routines/`
3. Merge `settings.example.json` into `~/.claude/settings.json`
4. Adjust paths and watched project roots in hook configs
5. Create memory directory with empty `MEMORY.md` index
