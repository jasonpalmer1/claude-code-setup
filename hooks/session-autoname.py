#!/usr/bin/env python3
"""
session-autoname.py — UserPromptSubmit hook.

Makes every Claude Code session name itself after what it's actually working on,
so the phone list / Agent View / the HQ Fleet tab show "<product-b>-pricing" instead
of "operator-d4".

Built 2026-07-26 (switchboard Phase 2). the operator's original ask, verbatim:
"they can dynamically name themselves".

HOW IT WORKS
  Claude Code keeps one registry file per live session at ~/.claude/sessions/<pid>.json
  carrying {sessionId, name, nameSource, ...}. `claude agents --json` reads straight
  from those files — verified live 2026-07-26 by writing a name and seeing it appear
  immediately, no restart. So the session renames itself by rewriting its own file.

WHEN IT STAYS OUT OF THE WAY (all verified against real registry files)
  - nameSource != "derived"  -> the operator renamed it himself (his manual "creative" session
    has no nameSource at all). Never overwrite a human's choice.
  - the current name doesn't look auto-generated -> already named, leave it.
    Auto names are "<something>-<2 hex>" (operator-d4, projects-ca).
  - slash commands, or a prompt with fewer than 2 real words left after filler is
    stripped -> a name derived from "keep going on next steps" is worse than none.

SAFETY
  Never throws, never blocks a prompt, never writes to stdout. UserPromptSubmit stdout
  is injected into the model's context, so this printing anything would silently
  pollute every single turn.
"""

import json
import os
import re
import sys
import tempfile

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")

# Auto-generated names look like "<prefix>-<2 hex>": operator-d4, projects-ca.
AUTO_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-[0-9a-f]{2}$", re.I)

# Filler that carries no signal about what the session is DOING.
STOP = {
    "a", "about", "actually", "all", "also", "am", "an", "and", "any", "anything", "are",
    "as", "at", "back", "basically", "be", "been", "but", "by", "can", "could", "did",
    "do", "does", "doing", "done", "dont", "down", "else", "even", "every", "everything",
    "few", "first", "for", "from", "get", "gets", "getting", "give", "go", "going", "good",
    "got", "had", "has", "have", "he", "help", "her", "here", "hey", "him", "his", "how",
    "i", "if", "im", "in", "into", "is", "it", "its", "ive", "just", "keep", "kind", "know",
    "last", "let", "lets", "like", "little", "look", "lot", "make", "many", "match", "may",
    "maybe", "me", "mine", "more", "most", "much", "my", "need", "needs", "new", "next",
    "no", "not", "now", "of", "off", "ok", "okay", "on", "once", "one", "only", "or",
    "other", "our", "out", "over", "own", "please", "pretty", "put", "really", "right",
    "said", "same", "say", "see", "set", "she", "should", "so", "some", "something",
    "start", "step", "steps", "still", "such", "sure", "take", "than", "that", "the",
    "their", "them", "then", "there", "these", "they", "thing", "things", "think", "this",
    "those", "through", "to", "today", "too", "try", "up", "us", "use", "very", "want",
    "was", "way", "we", "well", "were", "what", "when", "where", "which", "while", "who",
    "why", "will", "with", "work", "would", "yeah", "yes", "yet", "you", "your", "yourself",
}

MAX_WORDS = 3
MAX_LEN = 28

# UserPromptSubmit also fires for text the HARNESS injects, not just the operator typing:
# background-task completion notices, local-command output, system reminders. Those
# named 5 of his 7 live sessions "task-notification" on 2026-07-26 — every chat
# looking identical is the exact problem this hook exists to solve. Anything opening
# with an XML-ish tag is machine-authored; nothing the operator types starts with "<".
SYSTEM_MARKERS = (
    "task-notification",
    "system-reminder",
    "local-command",
    "command-name",
    "caveat:",
)

# Names produced by that bug before it was fixed. Treated as overwritable so the
# affected sessions self-heal on the operator's next real prompt — safer than reaching in
# and rewriting registry files that other live processes are actively holding.
OVERWRITABLE = {"task-notification", "task-notification-agent", "local-command"}


def is_system_prompt(text):
    head = text.lstrip()[:200].lower()
    if head.startswith("<"):
        return True
    return any(m in head for m in SYSTEM_MARKERS)


def derive_name(prompt):
    """Prompt -> short kebab label, or None if there isn't enough signal."""
    text = prompt.strip()
    if not text or text.startswith("/"):
        return None
    if is_system_prompt(text):
        return None

    # Only the first line/sentence matters — long prompts bury the topic in detail.
    text = text.split("\n", 1)[0][:400]
    # Drop code spans and paths, which produce unreadable labels.
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"[~/][\w./-]+", " ", text)

    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", text.lower()) if w not in STOP]
    # Preserve order, drop repeats.
    seen, content = set(), []
    for w in words:
        if w not in seen:
            seen.add(w)
            content.append(w)

    if len(content) < 2:
        return None

    name = "-".join(content[:MAX_WORDS])[:MAX_LEN].rstrip("-")
    # Never emit something that looks auto-generated, or we'd rename it again forever.
    if AUTO_NAME_RE.match(name) or len(name) < 4:
        return None
    return name


def find_registry(session_id):
    try:
        files = os.listdir(SESSIONS_DIR)
    except OSError:
        return None, None
    for fn in files:
        if not fn.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, fn)
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except Exception:
            continue
        if rec.get("sessionId") == session_id:
            return path, rec
    return None, None


def main():
    payload = json.load(sys.stdin)
    session_id = payload.get("session_id")
    prompt = payload.get("prompt") or ""
    if not session_id:
        return

    path, rec = find_registry(session_id)
    if not rec:
        return

    # Only ever touch a name Claude Code generated. A human's name is final.
    if rec.get("nameSource") != "derived":
        return
    current = rec.get("name") or ""
    if not AUTO_NAME_RE.match(current) and current.lower() not in OVERWRITABLE:
        return

    name = derive_name(prompt)
    if not name or name == rec.get("name"):
        return

    rec["name"] = name
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        cases = [
            "Patch it and build the naming",
            "a web app I can deploy on a Cloudflare temporary link",
            "keep going on next steps and do as much as you can yourself",
            "/log",
            "fix the <product-b> pricing page so the buy button works",
            "yes",
            "Can you please help me refactor the trading backtest engine?",
            "<task-notification>Agent fleet-needs-you completed</task-notification>",
            "<local-command-caveat>Caveat: the messages below were generated",
            "Caveat: The messages below were generated by the user",
        ]
        for c in cases:
            print(f"{c[:52]:<54} -> {derive_name(c)}")
        sys.exit(0)
    try:
        main()
    except Exception:
        pass  # a naming nicety must never break a prompt
    sys.exit(0)
