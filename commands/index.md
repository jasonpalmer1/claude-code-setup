---
description: Create or update a project's CLAUDE.md codebase map
argument-hint: "[project path — defaults to current working directory]"
---

Create or update the `CLAUDE.md` at the root of the target project so future sessions can answer questions about it without re-exploring the code.

Target: `$ARGUMENTS` (if empty, use the current working directory).

Steps:
1. If a `CLAUDE.md` already exists there, read it first and update it rather than rewriting from scratch — preserve any hand-written conventions.
2. Explore the project: directory structure, entry points, build/run commands, tech stack, data flow, and any non-obvious conventions.
3. Write `CLAUDE.md` at the project root with these sections:
   - **Overview** — what this project is, in 1-2 lines
   - **Tech stack**
   - **File map** — key directories/files and what they hold
   - **Architecture & data flow** — how the pieces connect
   - **Entry points** — how to run, build, test
   - **Conventions** — anything a new session must know
4. Store *what's true now and how it connects* — not a line-by-line dump. Keep it scannable.

This is delegable work — if the project is large, hand it to a mid-tier subagent and tell it to read any existing `CLAUDE.md` first.
