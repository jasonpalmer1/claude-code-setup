#!/usr/bin/env bash
# install.sh — installs this kit into ~/.claude (or --dest DIR).
#
# DRY RUN BY DEFAULT: with no flags it only prints what it WOULD do and
# writes nothing. Add --apply to actually write.
#
# Usage:
#   ./install.sh                      # dry run, shows the plan
#   ./install.sh --apply              # install for real
#   ./install.sh --apply --merge      # additive: never overwrite a file you already have
#   ./install.sh --apply --force      # overwrite existing files (CLAUDE.md, settings.json too)
#   ./install.sh --dest DIR           # install somewhere other than ~/.claude
#   ./install.sh --apply --load       # also load the launchd jobs (default: written, NOT loaded)
#   ./install.sh --launchd-dir DIR    # where plists go (default ~/Library/LaunchAgents)
#   ./install.sh --label-prefix P     # launchd label prefix (default local.claude)
#   ./install.sh --no-launchd         # skip the launchd step
#   ./install.sh --conservative|--yes # permission-mode answer without the prompt
#
# This script intentionally does NOT: touch a git remote or store git
# credentials, turn on any auto-approve permission mode, or set up deploy
# credentials / cloud accounts / paid API keys. launchd jobs are written but
# never loaded unless you pass --load.
set -euo pipefail

DEST="$HOME/.claude"
MODE="fresh"
FORCE=0
CONSERVATIVE=0
ASSUME_YES=0
APPLY=0
LOAD=0
NO_LAUNCHD=0
LA_DIR=""
LABEL_PREFIX="local.claude"

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --dest) DEST="$2"; shift 2 ;;
    --merge) MODE="merge"; shift ;;
    --force) FORCE=1; shift ;;
    --conservative) CONSERVATIVE=1; shift ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --load) LOAD=1; shift ;;
    --no-launchd) NO_LAUNCHD=1; shift ;;
    --launchd-dir) LA_DIR="$2"; shift 2 ;;
    --label-prefix) LABEL_PREFIX="$2"; shift 2 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LA_DIR="${LA_DIR:-$HOME/Library/LaunchAgents}"
HOME_SLUG="$(printf '%s' "$HOME" | sed 's#/#-#g')"
TAG="install.sh"
[ "$APPLY" -eq 1 ] || TAG="install.sh [dry-run]"
INSTALLED_LIST="$(mktemp -t ccsetup-installed.XXXXXX)"
trap 'rm -f "$INSTALLED_LIST"' EXIT

echo "$TAG: from $SRC into $DEST (mode=$MODE, apply=$APPLY)"
[ "$APPLY" -eq 1 ] || echo "$TAG: nothing will be written. Re-run with --apply to install."

# Defense in depth: the public kit must never carry maintainer-only tooling or credentials.
for forbidden in "hub/bin/publish-kit-export.sh" "hub/manual/export-blocklist.txt" ".git-credentials"; do
  if [ -e "$SRC/$forbidden" ]; then
    echo "install.sh: refusing to install — found a file that should never be in the public kit: $forbidden" >&2
    exit 1
  fi
done

copy_file() {
  # copy_file SRC_REL DEST_REL
  local src_rel="$1" dest_rel="$2"
  local src="$SRC/$src_rel" dest="$DEST/$dest_rel"
  [ -f "$src" ] || { echo "$TAG: skip (not in kit): $src_rel"; return; }
  if [ -f "$dest" ] && [ "$FORCE" -ne 1 ]; then
    if [ "$MODE" = "merge" ]; then
      echo "$TAG: MERGE SKIP (already exists): $dest_rel"
    else
      echo "$TAG: FRESH SKIP (already exists — use --merge or --force): $dest_rel"
    fi
    return
  fi
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
    printf '%s\n' "$dest" >> "$INSTALLED_LIST"
    echo "$TAG: installed $dest_rel"
  else
    echo "$TAG: would install $dest_rel"
  fi
}

copy_dir() {
  # copy_dir SRC_REL DEST_REL CHMOD_X
  local src_rel="$1" dest_rel="$2" chmod_x="${3:-0}"
  [ -d "$SRC/$src_rel" ] || return 0
  local f rel
  while IFS= read -r -d '' f; do
    rel="${f#"$SRC/$src_rel"/}"
    copy_file "$src_rel/$rel" "$dest_rel/$rel"
    if [ "$APPLY" -eq 1 ] && [ "$chmod_x" = "1" ] && [ -f "$DEST/$dest_rel/$rel" ]; then
      chmod +x "$DEST/$dest_rel/$rel" 2>/dev/null || true
    fi
  done < <(find "$SRC/$src_rel" -type f -print0 | sort -z)
}

# --- contract files ---
copy_file "OPERATOR.md" "OPERATOR.md"
if [ ! -f "$DEST/CLAUDE.md" ] || [ "$FORCE" -eq 1 ]; then
  copy_file "CLAUDE.md.template" "CLAUDE.md"
else
  echo "$TAG: SKIP (already exists): CLAUDE.md — see CLAUDE.md.template for sections to merge by hand"
fi

# --- commands / agents / bin / hub tools / fleet / hooks / routines / templates ---
copy_dir "commands" "commands"
copy_dir "agents" "agents"
copy_dir "bin" "bin" 1
copy_dir "hub/bin" "hub/bin" 1
copy_dir "fleet" "fleet" 1
copy_dir "hooks" "hooks" 1
copy_dir "templates" "templates"
copy_dir "routines" "routines" 1

# --- settings.json ---
if [ ! -f "$DEST/settings.json" ] || [ "$FORCE" -eq 1 ]; then
  copy_file "settings.json.template" "settings.json"
else
  echo "$TAG: SKIP (already exists): settings.json — merge the hook entries from settings.json.template by hand (or ONBOARDING.md Variant B)."
fi

# --- stubs the hooks tolerate being empty ---
MEMORY_DIR="$DEST/projects/$HOME_SLUG/memory"
write_stub() { # write_stub PATH CONTENT
  if [ -f "$1" ]; then return 0; fi
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$(dirname "$1")"
    printf '%s\n' "$2" > "$1"
    echo "$TAG: created $1"
  else
    echo "$TAG: would create $1"
  fi
}
write_stub "$MEMORY_DIR/MEMORY.md" "# MEMORY.md — always-loaded index (Tier 1)

Keep this small — every session pays for every line. One line per active project,
standing how-you-work rules, and a pointer to ARCHIVE.md for anything dormant."
write_stub "$MEMORY_DIR/ARCHIVE.md" "# ARCHIVE.md — Tier 3, dormant references

Indexed but never auto-loaded. Move entries here from MEMORY.md when a project goes dormant."
[ "$APPLY" -eq 1 ] && mkdir -p "$MEMORY_DIR/conversations"
write_stub "$DEST/hooks/safety-guard.local.json" '{
  "extra_catastrophic_patterns": []
}'
write_stub "$DEST/hub/token_ledger.md" "# token_ledger.md — per-session spend log (created empty by install.sh)"
write_stub "$DEST/hub/ledger.jsonl" ""
if [ -f "$SRC/templates/hub-board.md" ] && [ ! -f "$DEST/hub/board.md" ]; then
  if [ "$APPLY" -eq 1 ]; then
    mkdir -p "$DEST/hub"; cp "$SRC/templates/hub-board.md" "$DEST/hub/board.md"
    echo "$TAG: created hub board $DEST/hub/board.md"
  else
    echo "$TAG: would create hub board $DEST/hub/board.md"
  fi
fi

# --- placeholder substitution in code installed THIS run (never touches your own edits) ---
# Ported scripts carry <home-slug> / <your-id> where the original had a machine-specific
# Claude Code project-dir name; fill in yours.
if [ "$APPLY" -eq 1 ]; then
  while IFS= read -r f; do
    [ -f "$f" ] || continue
    case "$f" in *.md|*.json|*.template|*.example) continue ;; esac
    if grep -q '<home-slug>\|<your-id>' "$f" 2>/dev/null; then
      sed -i.bak -e "s#<home-slug>#$HOME_SLUG#g" -e "s#<your-id>#$HOME_SLUG#g" "$f" && rm -f "$f.bak"
      echo "$TAG: filled project-dir slug in ${f#"$DEST"/}"
    fi
  done < "$INSTALLED_LIST"
else
  echo "$TAG: would fill <home-slug> placeholders with $HOME_SLUG in installed scripts"
fi

# --- launchd (written, never loaded unless --load) ---
if [ "$NO_LAUNCHD" -eq 0 ] && [ -d "$SRC/launchd" ]; then
  for t in "$SRC"/launchd/*.plist.template; do
    [ -f "$t" ] || continue
    name="$(basename "$t" .plist.template)"
    label="$LABEL_PREFIX.$name"
    out="$LA_DIR/$label.plist"
    if [ -f "$out" ] && [ "$FORCE" -ne 1 ]; then
      echo "$TAG: SKIP (plist exists): $out"
      continue
    fi
    if [ "$APPLY" -eq 1 ]; then
      mkdir -p "$LA_DIR"
      sed -e "s#@DEST@#$DEST#g" -e "s#@LABEL_PREFIX@#$LABEL_PREFIX#g" -e "s#@HOME@#$HOME#g" "$t" > "$out"
      echo "$TAG: wrote $out (NOT loaded)"
      if [ "$LOAD" -eq 1 ]; then
        launchctl bootstrap "gui/$(id -u)" "$out" && echo "$TAG: loaded $label"
      fi
    else
      echo "$TAG: would write $out (not loaded)"
    fi
  done
fi

# --- permission mode: one question, default AUTO ---
if [ "$APPLY" -eq 1 ]; then
  if [ "$CONSERVATIVE" -eq 1 ]; then
    MODE_CHOICE="conservative"
  elif [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
    MODE_CHOICE="auto"
  else
    read -r -p "$TAG: auto mode (keeps going without asking) or conservative (asks before edits)? [auto/conservative] (default auto) " ans || true
    case "${ans:-}" in conservative|Conservative|c|C) MODE_CHOICE="conservative" ;; *) MODE_CHOICE="auto" ;; esac
  fi
  if [ "$MODE_CHOICE" = "conservative" ]; then
    echo "$TAG: conservative — start Claude Code with 'claude --permission-mode acceptEdits'."
  else
    echo "$TAG: auto (default) — OPERATOR.md's approval-rules section lists what it still always asks about. This script changes no harness-level permission flag."
  fi
fi

echo ""
if [ "$APPLY" -eq 1 ]; then echo "$TAG: done."; else echo "$TAG: dry run complete — nothing written."; fi
echo ""
echo "NOT set up, on purpose: git remotes/credentials, deploy or cloud accounts, API keys,"
echo "a harness-level auto-approve mode, and loading any launchd job (use --load or launchctl)."
echo "Next: open OPERATOR.md and CLAUDE.md in $DEST and fill in every <PLACEHOLDER>."
