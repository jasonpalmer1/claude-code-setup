#!/bin/sh
# SessionEnd hook — token ledger, pattern check, and a LOGGING RECEIPT.
#
# 2026-09-02 rewrite. the operator: "when you tell me you do stuff automatically, you
# sometimes won't... I wonder if you're actually logging the right way."
# A forensic sweep of 2026-09-01 found the answer, and it was worse than a gap:
# the headless `/log` leg this hook fired could not write at all. Probed all
# five ways (2026-09-02, reproducible):
#     cwd inside a normal dir ............... writes fine
#     ~/.claude/** from outside ............. BLOCKED
#     ~/.claude/** with --add-dir ........... BLOCKED
#     ~/.claude/** with cwd = that dir ...... BLOCKED
#     ~/.claude/** with --allowedTools ...... BLOCKED
#     ~/.claude/** with bypassPermissions ... writes
# So `.claude/**` is protected against unattended agents by anything short of
# bypass. Every auto-log run since has been burning Sonnet tokens to produce a
# summary it could never save, and reporting nothing when it failed. The logs
# that DO exist were written by sessions logging themselves before their clear
# signal — the suspenders were cut and only the belt was holding.
#
# What this hook does now: no model call, no pretending. Pure shell — which is
# NOT subject to the tool permission gate — checks whether the session that just
# ended left a log behind, and records ok/MISS. Coverage becomes a measured
# number surfaced at every session start by log-coverage.py, instead of a claim.
#
# L-0928 item 3, 2026-09-23: the old opt-in `AUTOLOG_BYPASS=1` writer leg is
# DELETED. It ran an unattended `claude -p /log` with bypassPermissions -- an
# agent with unrestricted tools reading an untrusted transcript -- because the
# model itself had to write under ~/.claude, which the probe above shows is
# refused short of a full bypass. It was dormant (set nowhere) and is fully
# replaced by the zero-tool writer below.
#
# hooks/pre-compact-real-log.sh (L-0920 Option B) solves the exact same "how
# do I get a close-out log written" problem a different, safer way: the model
# gets ZERO tools (`--tools ""`, not a bypass) and only ever prints text to
# stdout; the SHELL does the actual write + git commit. That sidesteps the
# whole risk the old leg carried, so it now runs BY DEFAULT (not opt-in)
# whenever this session ended with no log at all -- see the MISS branch below,
# which fires it with `--trigger sessionend`, detached (backgrounded + nohup)
# so it survives this hook's own process exiting, bounded by its own internal
# watchdog (default 20s). It also has its own internal guard against
# double-logging (skips quietly if a real log for this session already exists
# at/after the transcript's last activity, or if the transcript is under 50
# lines) -- on top of this script's own footer-grep above already gating the
# MISS branch in the first place.

[ -n "$CLAUDE_AUTOLOG" ] && exit 0

input=$(cat)
tp=$(printf '%s' "$input" | python3 -c "import sys,json
try: print(json.load(sys.stdin).get('transcript_path',''))
except Exception: print('')" 2>/dev/null)

[ -z "$tp" ] && exit 0
[ -f "$tp" ] || exit 0

MEM="$HOME/.claude/projects/$(printf '%s' "$HOME" | sed 's#/#-#g')/memory"
CONV="$MEM/conversations"
AUTOLOG="$HOME/.claude/hub/autolog.log"
sid=$(basename "$tp" .jsonl)
size=$(wc -c < "$tp" | tr -d ' ')
stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Token ledger first — pure parsing, free, no model call.
python3 "$HOME/.claude/hooks/token-ledger.py" "$tp" >/dev/null 2>>"$HOME/.claude/hub/hook-errors.log" || true

# Pattern-journal adherence (structural enforcement since 2026-07-19).
sh "$HOME/.claude/hooks/pattern-capture-check.sh" 2>>"$HOME/.claude/hub/hook-errors.log" || true

# --- the receipt -----------------------------------------------------------
# Only sessions big enough to be worth logging are judged (500KB ~= $22).
if [ "${size:-0}" -lt 500000 ]; then
  echo "$stamp skip sid=$sid size=$size — below the log-worthy threshold" >> "$AUTOLOG"
  exit 0
fi

# L-0776: a resumed or continued conversation runs under a NEW session id but
# keeps its background-task dir under the ORIGINAL id, so the only id it can see
# is the old one and its log footer names that. Such a log is accepted only when
# BOTH hold: the harness itself (queue-operation records, not tool output) ties
# this transcript to that lineage id, AND this session itself wrote that footer
# line into the log (a trivial edit to an old lineage log, or a command that only
# mentions the path, does not count). The log on disk must still carry it.
lin=""
if ! grep -rqs "logged-session: $sid" "$CONV" 2>/dev/null; then
  linf=$(mktemp)
  python3 - "$tp" "$CONV" > "$linf" 2>>"$HOME/.claude/hub/hook-errors.log" <<'PY'
import sys, re, json, os
tp, conv = sys.argv[1], sys.argv[2]
sid = os.path.basename(tp)[:-6]
taskdir = re.compile(r'/tmp/claude-\d+/[^/"\\]+/([0-9a-f-]{36})/tasks/')
foot = re.compile(r'logged-session:\s*([0-9a-f-]{36})')
# destination only: redirect/tee target, or the LAST operand of cp/mv
shellw = re.compile(r'(?:>>?\s*|tee\s+(?:-a\s+)?|(?:cp|mv)\s+(?:-\w+\s+)*\S+\s+)["\']?\S*conversations/([\w.-]+\.md)')
lineage, wrote = set(), {}   # wrote: log basename -> footer ids THIS session put in it
for l in open(tp, errors='ignore'):
    if l.startswith('{"type":"queue-operation"') and '/tasks/' in l:
        lineage.update(taskdir.findall(l))
    elif '"tool_use"' in l and 'logged-session' in l:
        try: d = json.loads(l)
        except Exception: continue
        m = d.get('message') if isinstance(d, dict) else None
        content = m.get('content') if isinstance(m, dict) else None
        for c in content if isinstance(content, list) else []:
            if not (isinstance(c, dict) and c.get('type') == 'tool_use'): continue
            i = c.get('input') if isinstance(c.get('input'), dict) else {}
            n = c.get('name')
            if n in ('Write', 'Edit', 'MultiEdit'):
                fp = str(i.get('file_path', ''))
                if '/memory/conversations/' not in fp: continue
                body = i.get('content') or i.get('new_string') or ''
                if n == 'MultiEdit':
                    body = ' '.join(str(e.get('new_string', '')) for e in i.get('edits') or [] if isinstance(e, dict))
                names = [os.path.basename(fp)]
            elif n == 'Bash':
                body = str(i.get('command', ''))
                names = shellw.findall(body)
            else: continue
            ids = set(foot.findall(str(body)))
            for name in names: wrote.setdefault(name, set()).update(ids)
lineage.discard(sid)
# Pass only if THIS session wrote a lineage-id footer into a log that still carries it.
for name in sorted(wrote):
    hit = wrote[name] & lineage
    if not hit: continue
    try: ondisk = set(foot.findall(open(os.path.join(conv, name), errors='ignore').read()))
    except OSError: continue
    if hit & ondisk:
        print(sorted(hit & ondisk)[0], name); sys.exit(0)
sys.exit(1)
PY
  lin=$(cat "$linf"); rm -f "$linf"
fi

if grep -rqs "logged-session: $sid" "$CONV" 2>/dev/null; then
  echo "$stamp ok   sid=$sid size=$size — session logged itself (footer found)" >> "$AUTOLOG"
elif [ -n "$lin" ]; then
  echo "$stamp ok   sid=$sid size=$size — logged under its lineage id (resumed session): $lin" >> "$AUTOLOG"
elif grep -rqs "$sid" "$CONV" 2>/dev/null; then
  echo "$stamp ok?  sid=$sid size=$size — id appears in a log, no footer (pre-footer log)" >> "$AUTOLOG"
else
  echo "$stamp MISS sid=$sid size=$size — ended with NO log. Closing rites did not run." >> "$AUTOLOG"

  # L-0920 scope add (a): fire the close-out writer (Option B, reused via
  # --trigger sessionend). A clean, minimal payload -- {session_id, transcript_path}
  # using the SAME sid this script already trusts (not the raw hook payload's own
  # session_id field, to stay consistent with the lineage-id handling above).
  # Detached so it outlives this hook's own process.
  PAYLOAD_FILE=$(mktemp)
  python3 -c "import json,sys; print(json.dumps({'session_id': sys.argv[1], 'transcript_path': sys.argv[2]}))" "$sid" "$tp" > "$PAYLOAD_FILE" 2>/dev/null
  if [ -s "$PAYLOAD_FILE" ]; then
    (
      nohup "$HOME/.claude/hooks/pre-compact-real-log.sh" --trigger sessionend < "$PAYLOAD_FILE" \
        >>"$HOME/.claude/hub/hook-errors.log" 2>&1
      rm -f "$PAYLOAD_FILE"
    ) &
    disown 2>/dev/null || true
  else
    rm -f "$PAYLOAD_FILE"
  fi
fi

exit 0
