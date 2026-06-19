# Claude Code — power-user setup

[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-D97757?style=flat-square&logo=anthropic&logoColor=white)](https://claude.com/claude-code)

An opinionated [Claude Code](https://claude.com/claude-code) configuration built around three ideas:

1. **Persistent, file-based memory** — Claude keeps an indexed set of single-fact memory files plus a per-session conversation log, so each session starts with context instead of re-deriving it.
2. **Cost discipline** — explicit rules for delegating non-reasoning work to cheaper models, plus a token-ledger hook that measures spend per session so you can tune from data.
3. **Self-maintaining project maps** — an `/index` command and a hook that nudges you to keep a `CLAUDE.md` codebase map in every project.

It's a template: copy what's useful, fill in the `<PLACEHOLDERS>`, delete the rest.

## What's here

- **`CLAUDE.md.template`** — global instructions (the "constitution"): the memory-system design, the delegation rule, token-economy levers, index-first exploration, and workflow + security principles. Sanitized — add your own specifics.
- **`commands/`** — custom slash commands:
  - `/index` — create/update a project's `CLAUDE.md` codebase map
  - `/log` — summarize the current session into a persistent conversation log
  - `/tokens` — review the token ledger and report spend trends
- **`hooks/`** — automation:
  - `index-reminder.sh` (PostToolUse) — nudges you to map a project that has no `CLAUDE.md`
  - `session-end-log.sh` (SessionEnd) — auto-summarizes the session (on a cheaper model) and appends a usage row
  - `token-ledger.py` — parses a session transcript and logs per-model token spend (pure parsing, no model call, costs nothing)
- **`settings.example.json`** — how to register the hooks and set defaults.

## Setup

1. Copy `CLAUDE.md.template` → `~/.claude/CLAUDE.md` and fill in the placeholders.
2. Copy `commands/` → `~/.claude/commands/` and `hooks/` → `~/.claude/hooks/` (then `chmod +x` the shell hooks).
3. Merge `settings.example.json` into `~/.claude/settings.json`, editing paths as needed.
4. Create your memory directory and an empty `MEMORY.md` index inside it.
5. Search-and-replace the placeholders: `<MEMORY_DIR>` (where memory files live) and adjust the watched project roots in `index-reminder.sh`.

## Notes

Built and iterated with Claude Code itself. Nothing here contains secrets or machine-specific data — paths are placeholders.

## License

MIT
