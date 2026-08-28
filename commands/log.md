---
description: Summarize the current session into the persistent conversation log
---

Write a concise summary of the current session to the conversation log.

Memory dir: `<MEMORY_DIR>`

Steps:
0. **Read the neighbors before writing.** Check what's already there *before* picking a slug or a tier, not only after writing — you can't tell whether an entry is correct, duplicative, or misfiled until you've read what sits around it.
   - `ls -t` the conversations dir and read the last 2-3 entries. If this session **continues** one of them, say so in the log and name the predecessor file — don't write a standalone entry that silently forks the story.
   - Read the index. **Audit it against disk in BOTH directions**: every pointer resolves to a real file, and every file has a pointer. An orphaned log is invisible to every future session — that's silent data loss. Fix what you can while you're here; for a log you haven't actually read, index it as unsummarized rather than inventing a hook just to make the index look clean.
   - Check the index's line cap and the project `CLAUDE.md` char cap *before* adding, so you know up front whether this entry forces an archive pass in the same edit.
   - Ask what this entry **supersedes** and archive that one now.
1. Pick a slug from the session's main topic. Build a filename `conversations/YYYY-MM-DD-<slug>.md` using today's absolute date.
2. Write that file — **under 30 lines**. Capture: what was asked, what was done/decided, key file paths touched, and any follow-ups or open threads. Facts worth persisting long-term go into a real memory file instead (and link to it), not here.
3. Append one line to `conversations_index.md`:
   `- [YYYY-MM-DD <Title>](conversations/YYYY-MM-DD-<slug>.md) — one-line hook`
4. If any durable fact, preference, or correction surfaced this session, save it to the appropriate memory file and add it to `MEMORY.md` — don't bury it only in the log.
5. End the log file with a short **Resume state** section: open threads, exact next steps, and anything mid-flight (running agents, undeployed changes, unpushed commits) — written so a fresh session could pick up cold from it.
6. **Verify before you claim it.** Don't report the log as written from the fact that you just wrote it — go look at the disk, and look at the files AROUND yours, not just the one you touched.
   - List the conversations dir: **your file must exist, with today's date AND a timestamp from this session.** Another log dated today may belong to a different session — check the timestamp and the content, not just the date in the filename.
   - Confirm the index line actually landed and its link resolves to the file you just wrote.
   - Confirm any memory file from step 4 is on disk — a successful-looking write isn't proof it survived if the store is shared across concurrent sessions with no lock.
   If a check fails, fix it and re-verify. Never say "everything is logged" from memory of having done it.
7. Only after step 6 passes, end your reply with exactly one closing line: `✅ Logged — safe to /compact now.` (This command is usually run right before a manual /compact; the log must capture full-context state BEFORE compaction, never after.)

Keep it terse. The log is for "what happened when," not a transcript.
