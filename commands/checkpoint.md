---
description: Closing rites on demand — log, save state to disk, then tell the user the exact next keystroke. Works in any chat; the token-hygiene primitive.
argument-hint: "(no args — run the rites for this session now)"
---

# /checkpoint — make this chat disposable, right now

The user invoked this because they want this session's state OFF the chat and ON the disk,
cheaply. Do all of it, in order, without asking questions:

1. **Extraction sweep.** Scan this session for durable facts (decisions + why, corrections,
   project state changes, patterns worth remembering) and file each to the right memory tier
   or the relevant project's `## Session memory` section (keep each entry short — a pointer
   plus the non-obvious facts, not a transcript; public repo → a gitignored
   `CLAUDE.local.md` instead). Never duplicate — update existing entries.
2. **Log.** Run `/log` (or, if that command isn't installed, append a dated summary to your
   conversation-log directory and its index).
3. **Disk state.** Update the board (e.g. `~/.claude/hub/board.md`, if you're using this
   repo's hub pattern) for anything this session started or landed — one line per item,
   absolute dates. Commit and push any repo work that's finished and verified; commit WIP on a
   branch if it's mid-flight. Uncommitted work shouldn't exist after a checkpoint.
4. **Cost line.** One line on the rough token/dollar shape of this session and its biggest
   spend driver, if you can tell (bulk reads? retries? a long resume?).
5. **The keystroke.** End your reply with exactly one instruction:
   - Everything landed and the next topic is unrelated → **"Type `/clear` now."** (state is on
     disk; a fresh session re-wakes from the board/memory tiers)
   - Mid-task with real in-context state that disk can't carry → **"Type `/compact <one-line
     focus>` now."**
   The message should end with a sentence or two on what's next after the clear/compact — see
   the pre-compact-line convention in `CLAUDE.md.template`'s "Session hygiene" section.

Never skip step 5 — the whole point of this command is that the user gets one keystroke to
type, not a judgment call to make.
