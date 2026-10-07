#!/usr/bin/env python3
"""L-1559 item 4: peer-message batching core (classifier + queue + flush plan).

Whitelist classifier: a message is batched ONLY when every condition holds. Anything
unknown, odd, or errored is sent as today (fail open). Used by:
  hooks/peer-batch.py    (PreToolUse SendMessage)
  hooks/inbox-inject.py  (UserPromptSubmit)
  routines/idle-sweep.sh (CLI: flush-plan / flush-commit)
"""
import json, os, re, sys, time, glob, secrets

MAX_CHARS = 1200
URGENT_RE = re.compile(
    r"\b(urgent\w*|blocked|blocker\w*|broken|broke|breakage|down|outage|fail\w*|rollback|roll\s+back|"
    r"alarm\w*|security|leak\w*|deploy\w*|prod|production|lease|ready-to-restart|asap|immediately|"
    r"emergency|critical|stuck|crash\w*|corrupt\w*|dead)\b|"
    r"\bneeds?\s+operator\b|\b(?:operator|user)\s+(needs|must|to\s+tap|to\s+approve)\b|\bwaiting\s+on\b|\bawaiting\b|"
    r"\bright\s+now\b|\bdeadline\b|\bwithin\s+\d+\s*(m|min|mins|minutes|h|hr|hrs|hours)\b|"
    r"\b(before|by|until|no\s+later\s+than)\s+\d{1,2}(:\d{2})?\s*(am|pm|a\.m\.|p\.m\.|ct|cst|cdt|utc)?\b|"
    r"\b(before|by)\s+(noon|midnight|tonight|tomorrow|eod|end\s+of\s+day)\b|\bplease\s+reply\b|\breply\s+(needed|required)\b",
    re.I)
BATCHABLE_RE = re.compile(
    r"\b(finished|done|complete[d]?|pass(ed)?|report\s+at|status|fyi|ack(nowledged)?|"
    r"requesting\s+next\s+ticket|next\s+ticket)\b", re.I)
# Added after the first replay hand-check (hub->lane instructions that merely contained "done"/"ack"):
# the batchable word must LEAD the message (first LEAD_CHARS), and instructions or questions always send.
LEAD_CHARS = 90
INSTRUCT_RE = re.compile(
    r"\b(next\s+for\s+you|new\s+(job|item|ticket|task)|your\s+(job|ticket|lane|next)|you\s+(should|need|must|will|own)|"
    r"please|do\s+this|go\s+ahead|proceed|start\s+(on|with|now)|claimed\s+for\s+you|for\s+your\s+queue|"
    r"take\s+(the|this|it)|rotate|rebase|run\s+`|correction|not\s+approved|reassign\w*|"
    r"stop|hold|don'?t|do\s+not|never|must|need\s+you|i\s+need|can\s+you|could\s+you|would\s+you)\b|\?",
    re.I)
IMPERATIVE_RE = re.compile(
    r"(^|[.!:;\u2014]\s+|\n\s*[-*\d.)]*\s*)(send|start|ship|add|use|queue|carry|file|remove|delete|merge|push|run|check|read|"
    r"write|make|put|keep|reuse|tell|update|fix|unhold|go|pick|next|then|first|also|after|only|good|nice)\b|"
    r"\b(do|start|ship|run|send|merge|push|fix|use)\b[^.\n]{0,20}\bnow\b|\bis\s+a\s+go\b|\bgoes\s+first\b|\bsend\s+me\b|\bship\s+it\b|\bwill\s+not\s+start\b|\bunhold\b|\bgo\b|\bnew\s+(queued\s+)?(ticket|queue)\b",
    re.I)
WORK_ORDER_RE = re.compile(r"\bL-\d{4}\b", re.I)
WORK_VERB_RE = re.compile(r"\b(assign\w*|dispatch\w*|brief\w*)\b", re.I)
SUBAGENT_RE = re.compile(r"^a[0-9a-f]{16}$")
ESCAPE = "!now"


def classify(to, message, summary=""):
    """Return (batch: bool, reason: str). Pure function."""
    if not isinstance(message, str) or not message.strip():
        return False, "non-text"
    m = message.lstrip()
    if m.lower().startswith(ESCAPE):
        return False, "escape !now"
    if isinstance(to, str) and SUBAGENT_RE.match(to.strip()):
        return False, "subagent id"
    if len(message) > MAX_CHARS:
        return False, "too long"
    text = message + "\n" + (summary if isinstance(summary, str) else "")
    mu = URGENT_RE.search(text)
    if mu:
        return False, "urgent token: " + mu.group(0)[:30]
    if WORK_ORDER_RE.search(text) and WORK_VERB_RE.search(text):
        return False, "work order"
    if INSTRUCT_RE.search(message) or IMPERATIVE_RE.search(message):
        return False, "instruction or question"
    if not BATCHABLE_RE.search(message[:LEAD_CHARS]):
        return False, "no leading batchable pattern"
    return True, "batchable"


# ---------------------------------------------------------------- sessions
def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except Exception:
        return False


def load_sessions(sessions_dir):
    out = []
    for f in glob.glob(os.path.join(sessions_dir, "*.json")):
        try:
            d = json.load(open(f))
            if isinstance(d, dict) and d.get("pid") and _pid_alive(d["pid"]):
                out.append(d)
        except Exception:
            continue
    return out


def resolve_recipient(to, sessions):
    """Live peer session dict for `to`, or None (unknown/ambiguous/dead -> caller sends)."""
    if not isinstance(to, str):
        return None
    t = to.strip()
    if not t or t == "*" or SUBAGENT_RE.match(t):
        return None
    if t.startswith("uds:"):
        sock = t[4:]
        hit = [s for s in sessions if s.get("messagingSocketPath") == sock]
        return hit[0] if len(hit) == 1 else None
    if t.isdigit():
        hit = [s for s in sessions if str(s.get("pid")) == t]
        return hit[0] if len(hit) == 1 else None
    suffix = None
    m = re.match(r"^(.*?)\s*\[([0-9a-f]{4,12})\]$", t)
    if m:
        t, suffix = m.group(1), m.group(2)
    hit = [s for s in sessions if s.get("sessionId") == t]
    if not hit:
        hit = [s for s in sessions if s.get("name") == t]
        if suffix:
            hit = [s for s in hit if str(s.get("sessionId", "")).startswith(suffix)]
    return hit[0] if len(hit) == 1 else None


def safe(s, n=40):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:n] or "unknown"


# ---------------------------------------------------------------- queue
def queue_write(inbox_dir, pid, sender, summary, message, now=None):
    """Atomic, unique-name write of one queued item. Returns final path."""
    now = time.time() if now is None else now
    d = os.path.join(inbox_dir, str(int(pid)))
    os.makedirs(d, exist_ok=True)
    base = "%d-%s-%s.md" % (int(now * 1000), safe(sender), secrets.token_hex(3))
    tmp = os.path.join(d, ".tmp-" + base)
    body = "from: %s\nat: %d\nsummary: %s\n\n%s\n" % (
        safe(sender), int(now), re.sub(r"[\r\n]+", " ", str(summary or ""))[:200], message)
    with open(tmp, "w") as fh:
        fh.write(body)
    final = os.path.join(d, base)
    os.rename(tmp, final)
    return final


def read_item(path):
    hdr, body = {}, ""
    try:
        raw = open(path, errors="replace").read()
        head, _, body = raw.partition("\n\n")
        for ln in head.splitlines():
            k, _, v = ln.partition(": ")
            hdr[k] = v
    except Exception:
        return None
    try:
        at = int(hdr.get("at") or os.path.getmtime(path))
    except Exception:
        at = int(time.time())
    return {"path": path, "from": hdr.get("from", "?"), "at": at,
            "summary": hdr.get("summary", ""), "body": body.strip()}


def list_items(inbox_dir, pid):
    d = os.path.join(inbox_dir, str(int(pid)))
    return sorted(p for p in glob.glob(os.path.join(d, "*.md")) if not os.path.basename(p).startswith("."))


def archive(path, inbox_dir, pid):
    """Atomic claim: rename into the archive. Only ONE caller wins; losers get None."""
    ad = os.path.join(inbox_dir, "_archive", str(int(pid)))
    os.makedirs(ad, exist_ok=True)
    dst = os.path.join(ad, os.path.basename(path))
    try:
        os.rename(path, dst)  # ENOENT if another writer already took it
        return dst
    except OSError:
        return None


def line_for(it, width=140):
    t = time.strftime("%H:%M", time.localtime(it["at"]))
    txt = it["summary"] or it["body"]
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"[<>]", "", txt)[:width]
    return "%s from %s: %s" % (t, it["from"], txt)


# ---------------------------------------------------------------- flush (idle-sweep)
def flush_plan(inbox_dir, sessions_dir, now, max_items=10, min_age_s=300, force_age_s=1800,
               skip_socks=()):
    """One digest per live recipient with queued items. Does NOT move anything."""
    plans = []
    for s in load_sessions(sessions_dir):
        sock = s.get("messagingSocketPath")
        if not sock or sock in skip_socks:
            continue
        files = list_items(inbox_dir, s["pid"])
        items = [i for i in (read_item(f) for f in files) if i]
        if not items:
            continue
        items.sort(key=lambda i: i["at"])
        oldest_age = now - items[0]["at"]
        if oldest_age < min_age_s:
            continue
        take = items[:max_items]
        lines = ["- " + line_for(i) for i in take]
        more = len(items) - len(take)
        text = ("!now DIGEST: %d queued peer message(s), none urgent (urgent ones were sent at once). "
                "Read them, act on what is yours, no reply needed unless one asks.\n%s%s\n"
                "Full text: ~/.claude/hub/inbox/_archive/%s/") % (
            len(take), "\n".join(lines),
            ("\n(+%d more next tick)" % more) if more else "", s["pid"])
        plans.append({"pid": s["pid"], "sid": s.get("sessionId", ""), "name": s.get("name", ""),
                      "sock": sock, "text": text, "files": [i["path"] for i in take],
                      "forced": oldest_age > force_age_s, "oldest_age_s": int(oldest_age)})
    return plans


def flush_commit(inbox_dir, pid, files):
    n = 0
    for f in files:
        if archive(f, inbox_dir, pid):
            n += 1
    return n


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a and a[0] == "flush-plan":
            inbox, sess, now = a[1], a[2], float(a[3])
            mx = int(a[4]) if len(a) > 4 else 10
            skip = a[5].split(",") if len(a) > 5 and a[5] else []
            print(json.dumps(flush_plan(inbox, sess, now, mx, skip_socks=skip)))
        elif a and a[0] == "flush-commit":
            print(flush_commit(a[1], a[2], a[3:]))
        elif a and a[0] == "classify":
            print(json.dumps(classify(a[1], sys.stdin.read())))
    except Exception as e:
        print("[]" if a and a[0] == "flush-plan" else "0")
        sys.stderr.write("peer_classify error: %s\n" % e)
