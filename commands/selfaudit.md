---
description: Mine your own accumulated data for improvements to Claude's behavior that nobody has written down yet
---

Mine your accumulated data for improvements to MY OWN behavior that nobody has written down yet.

The memory system already captures corrections **when I notice them in the moment**. This command is the part that runs when nobody noticed — it reads the accumulated record looking for what the evidence keeps showing and the rules never learned.

**Delegate the mining pass** to one subagent (mid tier — multi-file synthesis with judgment; state the model explicitly per the delegation rule). Main loop verifies and writes. Never run the reads in the main loop: this touches dozens of files and the whole point is a distilled conclusion.

## What the subagent hunts (the DELTA, never a summary)

Give it: your pattern journal (if you keep one) · `MEMORY.md` (= the "already known" list) · every `feedback_*.md` memory file · the conversation-log index + the newest handful of conversation-log files · a delegation-alarms log, if you keep one · the token ledger · a decisions ledger, if you keep one.

It looks for, in priority order:
1. **Recurring mistakes** — the same class of error in 2+ sessions with no rule, *or with a rule that clearly isn't working since it recurred anyway.*
2. **Rules that contradict each other**, or that later evidence quietly invalidated (cite both dates).
3. **Rules that never fire** — codified guidance no recent session shows any sign of using. Dead weight or badly placed; say which.
4. **Cost/delegation patterns in the numbers** — read the alarms log and ledger as evidence. Is main-loop-heavy spend correlated with a session SHAPE? Name the shape.
5. **Things you've now said 2+ times in different words** — a repeated ask means the system failed to absorb it the first time.

Hard constraints on its output: every finding cites file + date + what it actually said (no citation = drop it); no finding may restate an existing rule (it must run its own adversarial pass — "is this already covered?" — and drop the duplicates); ranked by how much a fix changes future behavior; **at most 6, and fewer real ones beats six padded ones.** Each finding: claim · evidence · why-it's-new (name the rule someone would think covers it) · the exact rule sentence to add, with a WHY.

## Then the main loop does the part the subagent can't

- **Verify before writing.** Findings are leads, not truths. Cheap direct checks — grep the log, read the setting, count the rows. A finding that fails verification gets dropped and *said out loud*, not quietly shelved.
- **Fold into EXISTING files.** Append a dated rule to the feedback file that already owns the topic; do not create new memory files and do not bloat `MEMORY.md` past its cap. New file only if genuinely no existing rule owns the subject.
- **Clear what the audit exposes as unanswered** — e.g. delegation alarms with no verdict. Answer them honestly, including the misses inside sessions that were otherwise justified.
- **Report in plain English**, leading with the single finding that changes something today. Not a list of five equals — the one that matters, then the rest in a line each.

## Standing rule this command exists to satisfy

An improvement that requires you to ask for it isn't self-improvement. Run this unprompted at natural boundaries: after a long build session, when a log entry gets written, or whenever an alarm backlog appears at session start.
