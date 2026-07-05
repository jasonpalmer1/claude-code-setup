---
description: Summarize the current session into the persistent conversation log
---

Write a concise summary of the current session to the conversation log.

Memory dir: `<MEMORY_DIR>`

Steps:
1. Pick a slug from the session's main topic. Build a filename `conversations/YYYY-MM-DD-<slug>.md` using today's absolute date.
2. Write that file — **under 30 lines**. Capture: what was asked, what was done/decided, key file paths touched, and any follow-ups or open threads. Facts worth persisting long-term go into a real memory file instead (and link to it), not here.
3. Append one line to `conversations_index.md`:
   `- [YYYY-MM-DD <Title>](conversations/YYYY-MM-DD-<slug>.md) — one-line hook`
4. If any durable fact, preference, or correction surfaced this session, save it to the appropriate memory file and add it to `MEMORY.md` — don't bury it only in the log.
5. End the log file with a short **Resume state** section: open threads, exact next steps, and anything mid-flight (running agents, undeployed changes, unpushed commits) — written so a fresh session could pick up cold from it.
6. After all files are written, end your reply with exactly one closing line: `✅ Logged — safe to /compact now.` (This command is usually run right before a manual /compact; the log must capture full-context state BEFORE compaction, never after.)

Keep it terse. The log is for "what happened when," not a transcript.
