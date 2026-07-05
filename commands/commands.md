---
description: Which command should I use? — routes what you're trying to do to the right command, or prints the full cheat-sheet
argument-hint: "[optional: what you're trying to do]"
---

Answer "which command(s) should I use?" from a single source of truth: `<MEMORY_DIR>/reference_command_playbook.md` — a file you maintain listing every custom command, what it does, and when to reach for it. Read it first; don't answer from memory, since it changes as commands are added.

- **With `$ARGUMENTS`** (a described task/situation): recommend the 1-3 applicable commands **in execution order** with a one-line *why* each — e.g. "new feature ready to go live" → `/verify` → `/preflight` → `/ship`. If nothing fits, say so and suggest whether a new command is worth creating.
- **No args:** print the playbook's tables verbatim as the cheat-sheet, then one line flagging anything with a cadence that's overdue (e.g. a weekly command that hasn't been run this week).

Keep it instant — this is a lookup, not an analysis. No subagents.

Note: this command depends on `reference_command_playbook.md` existing in your memory directory — it isn't auto-generated. Update it whenever you add or change a command.
