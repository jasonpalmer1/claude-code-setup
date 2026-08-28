#!/usr/bin/env python3
"""
session-autoname.py — UserPromptSubmit hook.

Makes every Claude Code session name itself after what it's actually working
on, so a session list shows "pricing-page-bug" instead of an opaque generated
id. Useful once you routinely run more than one or two sessions at a time and
telling them apart by name beats scrolling transcripts.

HOW IT WORKS
  If your harness keeps a per-session registry file (e.g. one JSON file per
  live session carrying {sessionId, name, nameSource, ...}) that a session
  list command reads live, this hook renames the CURRENT session by rewriting
  its own registry entry — no restart needed. Adjust SESSIONS_DIR and the
  registry shape to whatever your harness actually exposes; the derive_name()
  logic below is harness-agnostic.

WHEN IT STAYS OUT OF THE WAY
  - nameSource != "derived" -> you (or something else) renamed it already; a
    manually-chosen name has no nameSource at all. Never overwrite a human's
    choice.
  - the current name doesn't look auto-generated -> already named, leave it.
    Auto names are assumed to look like "<something>-<2 hex>".
  - slash commands, or a prompt with fewer than 2 real words left after filler
    is stripped -> a name derived from "keep going on next steps" is worse
    than none.

SAFETY
  Never throws, never blocks a prompt, never writes to stdout. UserPromptSubmit
  stdout is injected into the model's context, so this printing anything would
  silently pollute every single turn.
"""

import json
import os
import re
import sys
import tempfile

SESSIONS_DIR = os.path.expanduser("~/.claude/sessions")

# Auto-generated names look like "<prefix>-<2 hex>": myuser-d4, projects-ca.
# Adjust this to match whatever shape your harness actually generates.
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

# UserPromptSubmit also fires for text the HARNESS injects, not just what you
# typed: background-task completion notices, local-command output, system
# reminders. Naming a session after one of those makes every such chat look
# identical, which defeats the point of naming at all. Anything opening with
# an XML-ish tag is assumed machine-authored.
SYSTEM_MARKERS = (
    "task-notification",
    "system-reminder",
    "local-command",
    "command-name",
    "caveat:",
)

# Names a naming bug could plausibly produce before you notice and fix it.
# Treated as overwritable so affected sessions self-heal on the next real
# prompt — safer than reaching in and rewriting registry files that other
# live processes may be actively holding.
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

    # Only ever touch a name the harness generated. A human's name is final.
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
            "fix the pricing page so the checkout button works",
            "yes",
            "Can you please help me refactor the backtest engine?",
            "<task-notification>Agent worker-1 completed</task-notification>",
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
