# Onboarding — let Claude install this for you

Two copy-paste prompts. Open Claude Code, paste one, and it performs the whole install
interactively — including a short interview so the config gets personalized to you, not to me.

**Which one?**

- **Variant A — fresh install.** You have little or no existing `~/.claude` configuration.
- **Variant B — merge.** You already have a real setup (a global `CLAUDE.md`, hooks, or a memory
  directory) and want to adopt this architecture without losing anything of yours.

Both prompts tell Claude to double-check which situation you're actually in and switch if you
picked wrong. Neither one ever pushes to a git remote.

---

## Variant A — fresh install

Paste this into Claude Code:

```text
Set me up with the Claude Code architecture from
https://github.com/jasonpalmer1/claude-code-setup. Work through all the steps; only stop to
interview me where noted. Never push anything to any git remote.

1. Clone the repo to a scratch directory and read its README.md fully before doing anything.
2. Safety check: if ~/.claude/CLAUDE.md, ~/.claude/hooks/, or an existing memory directory
   already contain real content, STOP and tell me — I should use the merge prompt from the
   repo's ONBOARDING.md instead.
3. Interview me in ONE message: what I do, my active projects and where they live on disk,
   which Claude plan I'm on, how cost-sensitive I am, and anything you should always know
   about me or how I like to work.
4. Personalize CLAUDE.md.template into ~/.claude/CLAUDE.md: fill every <PLACEHOLDER> from my
   answers and delete sections that don't apply to me. Choose a memory directory path, create
   it with an empty MEMORY.md index, and seed the first memory files from my interview answers
   (one fact per file, typed frontmatter, indexed in MEMORY.md).
5. Show me the commands/ list with one-line descriptions and let me drop any I won't use,
   then install my picks into ~/.claude/commands/. Keep /log, /index, and /tokens — those
   three are the core of the system.
6. Install hooks/ into ~/.claude/hooks/: chmod +x the shell hooks, replace <MEMORY_DIR> and
   project-root placeholders, and merge the hook wiring from settings.example.json into
   ~/.claude/settings.json. Pipe-test each hook with a fake payload and show me it firing.
7. For each project directory I named: create a CLAUDE.md codebase map at its root (the /index
   command you just installed describes the format). Touch nothing outside ~/.claude and those
   project directories without asking.
8. Finish with a one-page cheat sheet: my new file layout, what each hook does, which commands
   I installed, and the session rhythm — end every meaningful session with /log; when it prints
   "✅ Logged — safe to /compact now", /clear and start the next session fresh. Log-then-clear
   beats letting one session run for days.
```

---

## Variant B — merge into an existing setup

This variant works the same way whether it's your first time merging this template into an
existing setup or you're picking up changes since a previous merge — it always diffs against
what you already have rather than assuming a blank slate.

Paste this into Claude Code:

```text
I have an existing Claude Code setup. Merge the architecture from
https://github.com/jasonpalmer1/claude-code-setup into it additively — nothing of mine gets
destroyed, whether this is my first merge of this template or I'm picking up changes since an
earlier one. Never push anything to any git remote.

1. Back up first: copy ~/.claude/CLAUDE.md, ~/.claude/settings.json, ~/.claude/commands/, and
   ~/.claude/hooks/ into a timestamped backup directory and tell me where it is.

2. Clone the repo to a scratch directory and read its README.md. Then, for every tracked file
   in the repo (each file under commands/, hooks/, CLAUDE.md.template, settings.example.json),
   classify it against what I already have and show me a short table (file / category / note):
   - MISSING — I have no file that corresponds to it. New adopt.
   - PREVIOUSLY ADOPTED — I already have a file that's recognizably an earlier copy of this
     same one (same name, same purpose, maybe with my own edits layered in). This is an update,
     not a fresh install: diff the fresh clone's version against mine and summarize what
     changed upstream before touching anything.
   - MINE, UNRELATED — I have a same-named or similar file that isn't from this repo. Leave it
     alone and tell me you're skipping it.

3. Baseline audit (skip if I tell you I've run this before and already have a number): scan my
   recent session transcripts for subagent calls that never stated a model explicitly — count
   how many silently inherited an expensive default. Report the number before changing
   anything; it's the before-picture the token ledger will improve on.

4. Merge additively, file by file, by the category from step 2:
   - MISSING files: copy in, chmod +x the shell/python ones, fill any placeholders, and wire
     any new hook registrations into settings.json.
   - PREVIOUSLY ADOPTED files: never overwrite wholesale. Weave the new or changed behavior
     from the fresh clone into my existing copy, preserving whatever customization I've already
     made to it. If the upstream change only adds new optional behavior (e.g. something gated
     behind an environment variable or a new code path nothing calls unless configured), that's
     a safe drop-in — apply it directly. If it changes existing behavior, tell me what changes
     before applying it.
   - CLAUDE.md.template sections: for every section in the fresh template that's new or
     materially different from what I already have, check my own CLAUDE.md for an equivalent
     rule first — compare what it actually requires me to do, not just whether a similar
     heading exists. Then:
     - Mine already covers the same ground → leave mine as-is, don't duplicate.
     - Mine covers it, but more weakly or in different words → keep mine (mine wins on
       conflict), but list the gap for me so I can decide whether to strengthen it.
     - No equivalent exists in mine → add the new section, adapted to fit my document's
       existing structure, heading style, and voice — don't paste it in as a foreign block.
     - It looks like it would genuinely conflict with something I set on purpose → stop and ask
       me rather than guessing.
   - settings.json: merge any new hook registrations into my existing hook arrays additively —
     append entries, never replace the file or remove entries I already have.

   Do not, anywhere in this step, run git reset, wholesale-replace, or wipe any of the
   following: my own project- or domain-specific instructions in CLAUDE.md, the content of my
   memory directory, my existing model or permission settings in settings.json, or any
   custom/domain-specific agents or commands I already have that didn't come from this repo.
   Protecting exactly those things is the reason this merge path exists instead of a fresh
   install — if you're ever about to replace one of them instead of adding to it, stop and ask
   me first.

5. Reorganize my existing memory/notes into the three tiers: an always-loaded index under a
   hard line cap, project-local sections in each repo's own CLAUDE.md, and an archive file for
   everything dormant. Move content — never delete it.

6. Pipe-test every hook — mine, newly adopted ones, and any I updated in step 4 — and show me
   each firing. For an updated hook, confirm its old behavior still works before showing me
   what's new.

7. Finish with a diff-style summary (everything added, updated, or moved — nothing deleted) and
   the session rhythm: end meaningful sessions with /log or /checkpoint, then /clear; review
   /tokens weekly.
```

---

## After install — the daily rhythm

The setup only compounds if you feed it. Three habits:

1. **End sessions with `/log`.** Before you close or compact a session that did anything worth
   remembering, type `/log`. Claude writes a summary under 30 lines — what was asked, what was
   done, decisions, and a "resume state" a cold session could pick up from — into your memory
   directory's conversation log, and indexes it. When you see `✅ Logged — safe to /compact now`,
   you can `/clear` without losing anything. Next session, just ask "check the conversation log
   for when we set up X" — Claude reads the index, not your whole chat history. `/checkpoint`
   runs this same log-then-disk-state sequence on demand, in any chat, when you want it done
   right now instead of waiting for a natural pause.
2. **`/tokens` weekly.** The SessionEnd hook has been logging every session's spend per model.
   Look at the trend, find what was expensive, tune your delegation habits from data.
3. **`/index` when you enter an unmapped project.** A `CLAUDE.md` map at a project's root is
   what makes every future session (and every cheap subagent) start oriented instead of blind.

Everything else — memory tiers, the hub, headless routines — is explained in
[README.md](README.md).
