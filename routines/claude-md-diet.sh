#!/bin/zsh
# CLAUDE.md diet — weekly context-bloat surgery across your project portfolio.
#
# WHY: a project CLAUDE.md is re-sent to the model on EVERY turn, so its size
# is a per-message tax, not a one-time cost. Left unwatched, these files only
# ever grow — a SessionStart bloat check (see hooks/instructions-bloat-check.py
# in this repo) warns once you're in the project, but a warning only helps if
# someone happens to be working there that week. This job does the cutting on
# its own, on a schedule, across every project whether or not anyone's in it.
#
# WHAT IT DOES: finds every CLAUDE.md over the cap, and for each one runs a
# headless Claude session that moves the OLDEST material VERBATIM into that
# project's ARCHIVE.md (never auto-loaded) — history, dated build logs, and
# bulk reference sections. Nothing is deleted or summarized. It verifies no
# original line was lost before writing, then commits.
#
# A mid-tier model, not your top tier: this is mechanical file surgery against
# an explicit, checkable rule — exactly the "executor-shaped work" category in
# CLAUDE.md.template's model-efficiency section.
set -u
CAP=30000
REPORTS="<REPORTS_DIR>"   # e.g. $HOME/projects/cockpit-reports
OUT="$REPORTS/$(date +%F)-claude-md-diet.md"
mkdir -p "$REPORTS"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# Adjust these roots to wherever your own projects live.
FAT=$(find ~/projects -maxdepth 2 -name CLAUDE.md -exec wc -c {} \; 2>/dev/null \
      | awk -v c=$CAP '$1>c {printf "%s (%dK)\n", $2, $1/1000}')

if [[ -z "$FAT" ]]; then
  echo "# CLAUDE.md diet — $(date +%F)\n\nAll project CLAUDE.md files are under ${CAP} chars. Nothing to do." > "$OUT"
  exit 0
fi

PROMPT="You are running the weekly CLAUDE.md diet. These project CLAUDE.md files are over the ${CAP}-character cap:

${FAT}

A project's CLAUDE.md is re-sent to the model on every single turn, so every character is billed on every message in that project. Your job is to get each file under ${CAP} characters WITHOUT LOSING ANY INFORMATION.

For each file listed above:
1. Read it and identify what is (a) standing rules and current architecture a fresh session needs immediately, vs (b) history: dated session-memory entries, dated build logs, changelog narrative, and bulky per-file or per-feature reference detail.
2. Move (b) VERBATIM into that project's ARCHIVE.md (create it if absent; append with a dated section header if it exists). A very large single-topic reference section may instead go to its own file, e.g. FILEMAP.md or MODES.md, with a short pointer left behind. Files that never auto-load cost nothing until read.
3. Keep in CLAUDE.md: the project description, architecture, file map at ONE LINE per entry, all standing rules and warnings (anything containing NEVER, DO NOT, HARD RULE, STANDING, or a safety warning must survive in CLAUDE.md itself, not only in the archive), and roughly the two most recent weeks of session memory.
4. NEVER delete, reword, or summarize archived content. Move it byte-for-byte.
5. VERIFY before writing: every non-blank line of the original must appear in either the new CLAUDE.md or the archive/reference file. If any line would be lost, abort that file and report it.
6. Commit in each repo (message: 'CLAUDE.md diet: archive history verbatim to ARCHIVE.md'). Do not push. Skip cleanly if a directory is not a git repo or has a dirty unrelated working tree.

Then write a report to ${OUT} in markdown: a table of each file's before and after size, what moved where, total characters saved, and anything you skipped or that needs a human. Keep the report under 30 lines."

claude -p "$PROMPT" \
  --model sonnet \
  --allowedTools "Read,Write,Edit,Glob,Grep,Bash(git *),Bash(wc *),Bash(find *),Bash(python3 *)" \
  --permission-mode acceptEdits \
  > "$REPORTS/$(date +%F)-claude-md-diet.log" 2> "$REPORTS/$(date +%F)-claude-md-diet.err"

if [[ -f "$OUT" ]]; then
  osascript -e 'display notification "CLAUDE.md diet finished — see cockpit-reports" with title "Context diet"' 2>/dev/null
else
  osascript -e 'display notification "CLAUDE.md diet FAILED — check the .err file" with title "Context diet"' 2>/dev/null
fi
