---
description: Closing rites on demand — log, save state to disk (Resume state + Pickup prompts), then keep working. Never tells the operator to /clear. Works in any chat; the token-hygiene primitive.
argument-hint: "(no args — run the rites for this session now)"
---

# /checkpoint — make this chat disposable, right now

the operator invoked this because he wants this session's state OFF the chat and ON the disk, cheaply. Do all of it, in order, without asking questions:

0. **⛔ READ THE NEIGHBORS BEFORE YOU WRITE ANYTHING (the operator, 2026-08-15, standing).** He asked for this as a *pre*-write step, distinct from the post-write verify in step 5: *"check the logs before this log... Think of it like a file system. You must check the files around it to make sure it's in the right place."* **A commit is not a log** — that was the miss that produced this rule. You cannot tell whether an entry is correct, duplicative, or misfiled until you have read what sits around it.
   - List the memory `conversations/` dir; read the last 2-3 entries. Read `MEMORY.md`. Read the tail of the project's `## Session memory`. Is this session a *continuation* of one of them? Say so in the log and name the predecessor file.
   - **Audit the index against disk in BOTH directions**: every pointer resolves to a real file, AND every file has a pointer. Unindexed logs are invisible to every future session — silent data loss. Fix it while you are here; do not report it and move on. (First run of this check, 2026-08-15, found 20 orphaned logs and a 65-line index against a 40-line cap.)
   - Check the caps *before* adding (`MEMORY.md` lines, project `CLAUDE.md` chars) so you know whether this entry forces an archive pass in the same edit.
   - Ask what the new entry **supersedes**, and archive that one now.
   - If you cannot honestly summarize an orphaned log without reading it, index it as unsummarized and say so. **Never invent a hook to make an index look clean.**

1. **Extraction sweep.** Scan this session for durable facts (people, decisions + why, corrections, project state changes, patterns) and file each to the right memory tier / the repo's `## Session memory` (~120-word cap per entry; public repo → `CLAUDE.local.md`). Never duplicate — update existing entries.
2. **Log.** Run `/log` (or if unavailable, append a dated summary to the memory conversations dir + index line).
3. **Disk state.** Update `~/.claude/hub/board.md` for anything this session started/landed (one line per item, absolute dates), and sync the Notion "🎛 Mission Control — Live Board" page (id `3b767521ca24814e8f5bc333c7be029a`, via `notion-update-page`; skip if the Notion MCP is absent). Commit + push any repo work that is finished and verified; commit WIP on a branch if mid-flight. Uncommitted work must not exist after a checkpoint.
4. **Cost line + self-verdict.** One line in chat: rough token/$ shape of this session and its biggest spend (bulk reads? retries? long resume?). Then append the session's verdict to `~/.claude/hub/delegation-alarms.log`:
   `YYYY-MM-DD <sid8>|<lane-name> self-verdict=BUILD|OPS main-loop=<tier> delegated="<what went to workers, or 'nothing'>" tree=<n sessions spawned, or 0> — self-reported`
   (sid8 = first 8 hex of the session id, REQUIRED as of 2026-08-31 — newest `.jsonl` in your own `~/.claude/projects/<dir-slug>/`; lane name goes after the pipe. Lane-name-only lines can't be matched against alarm keys (`date|sid8`) — that mismatch caused a false 8-alarm backlog read on 2026-08-31. Spawners report the TREE aggregate. Be honest — a heavy-main-loop OPS session names the miss. Spec: memory `feedback_lane_self_verdict.md`.)
5. **⛔ VERIFY THE RITES LANDED — MANDATORY GATE BEFORE ANY CLEAR RECOMMENDATION (the operator, 2026-08-15, standing).** Do not take steps 1-4 on trust because you just ran them. **Go look at the disk, and look at the files AROUND the one you wrote** — his words: *"Think of it like a file system. You must check the files around it to make sure it's in the right place."* Run these checks and state the result:
   - `ls -la ~/.claude/projects/<home-slug>/memory/conversations/ | grep <today>` — **a log for THIS session must exist, with today's date.** Neighbouring logs from earlier today or from other sessions are NOT yours; check the timestamp and the content, not just the date in the filename. (This exact failure happened 2026-08-15: two logs dated today existed, both from other sessions, and the clear was recommended anyway.)
   - `git status --porcelain` in every repo touched → **no uncommitted work of yours may remain.**
   - `git log origin/main..HEAD` → **nothing of yours left unpushed.**
   - The conversations index has a line pointing at the new log; the board reflects what this session actually did; the self-verdict line is in `delegation-alarms.log`.
   - **Anything built in a session scratchpad is GONE at clear — copy it somewhere durable first**, then verify the copy exists.
   - **PROCESS SWEEP (the operator, 2026-09-15, standing).** `ListAgents` and the task list are blind to anything started inside a Bash call. Run `ps -ax -o pid,etime,command | grep -Ev "^ *[0-9]+ +[0-9-]+:" | grep -E "node |python|http.server|wrangler|tsx |vite|puppeteer|chrome-headless"` and **kill anything THIS session started**. Checking a port is not enough — check by process. *(Real miss 2026-09-15: a `python3 -m http.server` started for a screenshot survived a full checkpoint and was alive 23 min later; the operator caught it. The same sweep found 7 orphans from earlier sessions, some 1-2 days old.)*
   **If any check fails, fix it and re-verify before step 6.** Never say "everything is logged" from memory of having done it — that is the proxy-evidence rule ([[feedback_verify_the_endpoint_exists]]) applied to your own rites, and it is the single most expensive place to get it wrong, because the evidence disappears the moment he clears.

6. **Log, then keep working — never a keystroke for the operator (the operator, 2026-09-23: he never has to
   be told to clear again).** Rites landed and verified (steps 0-5), including the log's Resume
   state + Pickup prompts sections — that's the state a fresh session would need. Do NOT end by
   telling him to type `/clear` or `/compact`; auto-compact handles context on its own (L-0802
   tightens exactly when). Continue with the next ticket in the same reply. If this session is
   truly unable to continue (hard cap, genuinely stuck), SendMessage the hub with the pickup
   prompt instead of stopping — the hub opens a fresh chat and hands the work over, no action
   from the operator either way.

**Steps 0 and 5 are the two halves of one rule and neither is optional: read the neighbors BEFORE writing, verify the write AFTER.** Skipping either means the log is a guess. Step 6 exists so a handoff (to the hub, or to a future resume) is clean — but nothing here is a keystroke handed to the operator, and it isn't attempted until 0 and 5 both pass.

**Do NOT narrow this pattern to the commands you think you ran.** On 2026-09-15 the first version of this sweep grepped `tsx scripts` and therefore MISSED `tsx /tmp/regate-watchdog-check.mts` — a bug-gate leftover that had been running 50 minutes — and I told the operator "nothing of mine is running" a SECOND time. A filter that only matches the happy path reads as clean. Sweep WIDE (`node `, `python`, `tsx `, `vite`, `puppeteer`, `chrome-headless`) and judge the rows, rather than pre-filtering to what you expect. Subagents spawn processes too, and they are yours.
