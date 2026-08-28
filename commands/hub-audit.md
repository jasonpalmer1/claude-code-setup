---
description: Monthly adversarial self-audit of the hub — a fresh, context-free worker hunts for drift, stale rules, and self-flattering bookkeeping across board + ledger + memory
argument-hint: "(no args — run first hub start of a new month, or on demand)"
---

# Hub self-audit

The hub writes its own board, files its own memory, reads its own ledger, and tunes its own rules from its own exhaust. Self-referential systems drift *confidently*. This is the outside auditor for the bureaucracy of one — deliberately adversarial, deliberately un-briefed.

Spawn ONE **mid-tier** worker (multi-source judgment) with the prompt below. Give it NO framing beyond the task — it must not inherit the hub's assumptions. Its findings are **leads, not truths** (the hub's own rule); the main loop reviews each against reality before proposing anything to you.

**Worker prompt:**

> You are auditing a personal AI "hub" system for drift. Assume it IS drifting and find the evidence — a clean bill of health is a failure of imagination, but do NOT invent problems (every finding needs a cited file/line or ledger row). Read, read-only: `~/.claude/commands/hub.md` (the protocol), `~/.claude/hub/board.md`, `<MEMORY_DIR>/MEMORY.md`, the last ~30 rows of the token ledger, and spot-check 3–5 memory files the index points to. Then report, terse and specific:
> 1. **Stale rules** — thresholds/heuristics in hub.md that no longer bind (e.g. a calibrated trigger that can no longer fire because behavior changed). Quote the rule + why it's dead.
> 2. **Board/memory vs. reality** — projects listed active that look dead, ownership/registry flags that are wrong, files or paths referenced that don't exist on disk, `[[links]]` with no target. Verify each against the filesystem.
> 3. **Self-flattering bookkeeping** — places the hub graded its own work generously, spend it isn't accounting for (esp. main-loop/hub-session cost that no worker report captures), or LANDED lines claiming "verified/done" without evidence.
> 4. **Dominant waste NOW** — from the ledger, re-derive the single biggest cost pattern this period. Do NOT trust any answer already written in hub.md; the point is that it decays.
> 5. **Wrong optimization target** — anything the hub is busily optimizing that is NOT your actual binding constraint. Is the machine helping you feel productive while the thing that actually matters stays undone?
> Return a numbered findings list, each: what / evidence (file:line or ledger row) / suggested fix. Your final text is data for the main loop, not prose for a human.

On return: the main loop verifies each finding in code+reality, discards the noise, and brings the survivors to you with concrete fixes. Accepted fixes get written into hub.md or a `feedback` memory — the audit's job is to keep the self-tuning honest.
