#!/usr/bin/env bash
# install.sh — installs this kit into ~/.claude (or --dest DIR).
#
# Two modes:
#   (default)     fresh install — refuses to overwrite CLAUDE.md/settings.json
#                 if they already exist and look like real content, unless
#                 --force is also given.
#   --merge       additive install — never overwrites an existing file, only
#                 copies files you don't already have and reports what it
#                 skipped so you can merge those by hand (or ask Claude Code
#                 to do it for you — see ONBOARDING.md's Variant B prompt,
#                 which does a smarter, content-aware merge than this script
#                 can).
#
# This script intentionally does NOT:
#   - install anything that pushes to a git remote, or store any git
#     credentials
#   - install the private publish-kit exporter/scanner (those never leave
#     the maintainer's own private config in the first place)
#   - turn on any autonomous/auto-approve permission mode by default
#   - set up deploy credentials, cloud accounts, or paid API keys
#
# Usage:
#   ./install.sh [--dest DIR] [--merge] [--force] [--conservative] [--yes]
set -euo pipefail

DEST="$HOME/.claude"
MODE="fresh"
FORCE=0
CONSERVATIVE=0
ASSUME_YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --merge) MODE="merge"; shift ;;
    --force) FORCE=1; shift ;;
    --conservative) CONSERVATIVE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    -h|--help)
      sed -n '1,25p' "$0"
      exit 0
      ;;
    *)
      echo "install.sh: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "install.sh: installing from $SRC into $DEST (mode=$MODE)"

# Defense in depth: this repo's tracked tree should never contain the
# maintainer's private exporter/scanner or push credentials. Refuse to
# proceed if it somehow does, rather than silently installing them.
for forbidden in "hub/bin/publish-kit-export.sh" "hub/manual/export-blocklist.txt" ".git-credentials"; do
  if [ -e "$SRC/$forbidden" ]; then
    echo "install.sh: refusing to install — found a file that should never be in the public kit: $forbidden" >&2
    exit 1
  fi
done

mkdir -p "$DEST"

copy_file() {
  # copy_file SRC_REL DEST_REL — respects fresh/merge mode.
  local src_rel="$1" dest_rel="$2"
  local src="$SRC/$src_rel" dest="$DEST/$dest_rel"
  [ -f "$src" ] || { echo "install.sh: skip (not in kit): $src_rel"; return; }
  if [ -f "$dest" ]; then
    if [ "$MODE" = "merge" ] && [ "$FORCE" -ne 1 ]; then
      echo "install.sh: MERGE SKIP (already exists, not overwritten): $dest_rel"
      return
    fi
    if [ "$MODE" = "fresh" ] && [ "$FORCE" -ne 1 ]; then
      echo "install.sh: FRESH SKIP (already exists — rerun with --force or use --merge): $dest_rel"
      return
    fi
  fi
  mkdir -p "$(dirname "$dest")"
  cp "$src" "$dest"
  echo "install.sh: installed $dest_rel"
}

copy_dir() {
  # copy_dir SRC_REL DEST_REL CHMOD_X — copies every file in a directory,
  # one at a time through copy_file so fresh/merge skip rules apply per file.
  local src_rel="$1" dest_rel="$2" chmod_x="${3:-0}"
  [ -d "$SRC/$src_rel" ] || return
  while IFS= read -r -d '' f; do
    local rel="${f#"$SRC/$src_rel"/}"
    copy_file "$src_rel/$rel" "$dest_rel/$rel"
    if [ "$chmod_x" = "1" ] && [ -f "$DEST/$dest_rel/$rel" ]; then
      chmod +x "$DEST/$dest_rel/$rel" 2>/dev/null || true
    fi
  done < <(find "$SRC/$src_rel" -type f -print0)
}

# --- contract files ---
copy_file "OPERATOR.md" "OPERATOR.md"
if [ ! -f "$DEST/CLAUDE.md" ] || [ "$FORCE" -eq 1 ]; then
  copy_file "CLAUDE.md.template" "CLAUDE.md"
else
  echo "install.sh: $MODE SKIP (already exists, not overwritten): CLAUDE.md — see CLAUDE.md.template in the kit for the sections to merge in by hand"
fi

# --- commands / agents / bin / fleet / hooks ---
copy_dir "commands" "commands"
copy_dir "agents" "agents"
copy_dir "bin" "bin" 1
copy_dir "fleet" "fleet" 1
copy_dir "hooks" "hooks" 1
if [ -d "$DEST/hooks/lib" ]; then chmod +x "$DEST"/hooks/lib/*.mjs 2>/dev/null || true; fi

# safety-guard.py's optional extension point — always created empty; add
# your own extra_catastrophic_patterns regex list to it later if you want,
# never edit hooks/safety-guard.py itself to do that.
if [ -d "$DEST/hooks" ] && [ ! -f "$DEST/hooks/safety-guard.local.json" ]; then
  printf '{\n  "extra_catastrophic_patterns": []\n}\n' > "$DEST/hooks/safety-guard.local.json"
  echo "install.sh: created empty $DEST/hooks/safety-guard.local.json"
fi

# --- templates / routines ---
copy_dir "templates" "templates"
copy_dir "routines" "routines"

# --- settings.json ---
if [ ! -f "$DEST/settings.json" ] || [ "$FORCE" -eq 1 ]; then
  copy_file "settings.json.template" "settings.json"
else
  echo "install.sh: $MODE SKIP (already exists, not overwritten): settings.json — merge the hook entries from settings.json.template by hand, or ask Claude Code to do it (ONBOARDING.md Variant B)."
fi

# --- memory stubs (S6: every ported hook must tolerate these being empty
# or missing entirely, this just gives a new install a starting point) ---
HOME_SLUG="$(printf '%s' "$HOME" | sed 's#/#-#g')"
MEMORY_DIR="$DEST/projects/$HOME_SLUG/memory"
mkdir -p "$MEMORY_DIR/conversations"
if [ ! -f "$MEMORY_DIR/MEMORY.md" ]; then
  cat > "$MEMORY_DIR/MEMORY.md" <<'EOF'
# MEMORY.md — always-loaded index (Tier 1)

Keep this file small — every session pays for every line. One line per active
project, standing how-you-work rules, and a pointer to ARCHIVE.md for
anything dormant. See CLAUDE.md's memory-system section for the full design.
EOF
  echo "install.sh: created empty MEMORY.md at $MEMORY_DIR/MEMORY.md"
fi
if [ ! -f "$MEMORY_DIR/ARCHIVE.md" ]; then
  cat > "$MEMORY_DIR/ARCHIVE.md" <<'EOF'
# ARCHIVE.md — Tier 3, dormant references

Indexed but never auto-loaded. Move entries here from MEMORY.md when a
project or idea goes dormant; never delete.
EOF
  echo "install.sh: created empty ARCHIVE.md at $MEMORY_DIR/ARCHIVE.md"
fi

# --- hub board stub ---
if [ -f "$SRC/templates/hub-board.md" ]; then
  mkdir -p "$DEST/hub"
  if [ ! -f "$DEST/hub/board.md" ]; then
    cp "$SRC/templates/hub-board.md" "$DEST/hub/board.md"
    echo "install.sh: created empty hub board at $DEST/hub/board.md"
  else
    echo "install.sh: $MODE SKIP (already exists): hub/board.md"
  fi
fi

# --- ledger stub (S6: token-ledger.py / session-end-log.sh must not crash
# on a missing or empty ledger) ---
if [ ! -f "$DEST/hub/token_ledger.md" ]; then
  mkdir -p "$DEST/hub"
  printf '# token_ledger.md — per-session spend log (created empty by install.sh)\n' > "$DEST/hub/token_ledger.md"
  echo "install.sh: created empty $DEST/hub/token_ledger.md"
fi

# --- permission mode: one question, default AUTO, never asked twice ---
if [ "$CONSERVATIVE" -eq 1 ]; then
  MODE_CHOICE="conservative"
elif [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
  MODE_CHOICE="auto"
else
  echo ""
  read -r -p "install.sh: run Claude Code in auto mode (keeps going without asking, recommended) or conservative (asks before edits)? [auto/conservative] (default: auto) " ans || true
  case "${ans:-}" in
    conservative|Conservative|c|C) MODE_CHOICE="conservative" ;;
    *) MODE_CHOICE="auto" ;;
  esac
fi

if [ "$MODE_CHOICE" = "conservative" ]; then
  echo "install.sh: conservative mode selected — start Claude Code with 'claude --permission-mode acceptEdits' (or your harness's equivalent) instead of its full-autonomy mode."
else
  echo "install.sh: auto mode selected (default) — OPERATOR.md's approval-rules section documents what 'auto' still always asks about (money, secrets, anything public/hard to undo). This script does not itself change any harness-level permission flag."
fi

echo ""
echo "install.sh: done."
echo ""
echo "What this did NOT set up (on purpose):"
echo "  - No git remote, git push credentials, or the private publish-kit exporter/scanner"
echo "  - No deploy credentials or cloud/API accounts (Cloudflare, GitHub Actions, model APIs, etc.)"
echo "  - No autonomous/auto-approve permission mode turned on at the harness level"
echo "  - No project-specific commands or agents beyond what's in this kit"
echo "  - Your real name, spending caps, and red lines in OPERATOR.md are still placeholders — edit that file before you rely on it"
echo ""
echo "Next: open OPERATOR.md and CLAUDE.md in $DEST and fill in every <PLACEHOLDER>."
