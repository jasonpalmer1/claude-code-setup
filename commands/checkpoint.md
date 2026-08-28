---
description: Closing rites on demand — log, save state to disk, then tell the user the exact next keystroke. Works in any chat; the token-hygiene primitive.
argument-hint: "(no args — run the rites for this session now)"
---

# /checkpoint — make this chat disposable, right now

The user invoked this because they want this session's state OFF the chat and ON the disk,
cheaply. Do all of it, in order, without asking questions:

0. **Read the neighbors before you write anything.** This is a pre-write check, distinct from
   the post-write verify in step 5 — you can't tell whether an entry is correct, duplicative, or
   misfiled until you've read what sits around it. A commit is not a log.
   - List the memory `conversations/` dir; read the last 2-3 entries. Read `MEMORY.md`. Read the
     tail of the project's `## Session memory`. Is this session a *continuation* of one of them?
     Say so in the log and name the predecessor file.
   - **Audit the index against disk in BOTH directions**: every pointer resolves to a real file,
     AND every file has a pointer. Unindexed logs are invisible to every future session — silent
     data loss. Fix it while you're here; don't just report it and move on.
   - Check the caps *before* adding (index line count, project `CLAUDE.md` char count) so you know
     whether this entry forces an archive pass in the same edit.
   - Ask what the new entry **supersedes**, and archive that one now.
   - If you can't honestly summarize an orphaned log without reading it, index it as unsummarized
     and say so. Never invent a hook just to make an index look clean.

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
5. **Verify the rites landed — a mandatory gate before any clear recommendation.** Don't take
   steps 1-4 on trust just because you just ran them. Go look at the disk, and look at the files
   AROUND the one you wrote:
   - List the conversations dir filtered to today's date — **a log for THIS session must exist,
     with today's date.** A neighboring log from earlier today or from another session is NOT
     yours; check the timestamp and the content, not just the date in the filename.
   - `git status --porcelain` in every repo touched → no uncommitted work of yours may remain.
   - Check for anything of yours left unpushed → nothing should be.
   - The conversations index has a line pointing at the new log; the board reflects what this
     session actually did.
   - **Anything built in a session scratchpad is GONE at clear — copy it somewhere durable
     first**, then verify the copy exists.
   If any check fails, fix it and re-verify before step 6. Never say "everything is logged" from
   memory of having done it — the evidence disappears the moment the session clears, which makes
   this the single most expensive place to get wrong.
6. **The keystroke.** End your reply with exactly one instruction:
   - Everything landed and the next topic is unrelated → **"Type `/clear` now."** (state is on
     disk; a fresh session re-wakes from the board/memory tiers)
   - Mid-task with real in-context state that disk can't carry → **"Type `/compact <one-line
     focus>` now."**
   The message should end with a sentence or two on what's next after the clear/compact — see
   the pre-compact-line convention in `CLAUDE.md.template`'s "Session hygiene" section.

**Steps 0 and 5 are the two halves of one rule and neither is optional:** read the neighbors
BEFORE writing, verify the write AFTER. Skipping either means the final keystroke in step 6 is a
guess dressed up as a fact.
