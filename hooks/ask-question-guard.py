#!/usr/bin/env python3
"""L-1672 PreToolUse guard for AskUserQuestion (matcher: AskUserQuestion).

AskUserQuestion blocks the hub until answered; it froze for 8h/18h/9h on
9/28, 10/03, 10/04. This BLOCKS (exit 2, reason on stderr) when:
  - local America/Chicago time is 21:00-07:00, or
  - the last genuine user-typed message is > 10 min old (or none found).
ASK_GUARD_OFF=1 allows. Any internal error FAILS OPEN (allow) and logs one
line to the delegation-alarms log.
Test overrides: ASK_GUARD_NOW (ISO8601 UTC), ASK_GUARD_ALARM_LOG.
"""
import json, os, sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
MAX_AGE = timedelta(minutes=10)
REASON = ("ask-question-guard: blocked AskUserQuestion ({why}). Decide it yourself or put it "
          "as a one-line tappable item in your reply text + ledger NEEDS-OPERATOR; keep working.")
NOT_HUMAN_PREFIXES = ("<task-notification", "<local-command", "<system-reminder",
                      "<command-name", "Another Claude session", "[Cross-session",
                      "This session is being continued", "Caveat:")


def alarm_log():
    return os.environ.get("ASK_GUARD_ALARM_LOG") or os.path.expanduser("~/.claude/hub/delegation-alarms.log")


def log_alarm(msg):
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(alarm_log(), "a") as f:
            f.write(f"{ts} ask-question-guard FAIL-OPEN: {msg}\n")
    except Exception:
        pass


def now_utc():
    v = os.environ.get("ASK_GUARD_NOW")
    return parse_ts(v) if v else datetime.now(timezone.utc)


def parse_ts(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def is_genuine(d):
    if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain") or d.get("isCompactSummary"):
        return False
    origin = d.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    c = (d.get("message") or {}).get("content")
    if isinstance(c, str):
        text = c
    elif isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        text = "".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        if not text and not any(isinstance(b, dict) and b.get("type") == "image" for b in c):
            return False
    else:
        return False
    return not text.lstrip().startswith(NOT_HUMAN_PREFIXES)


def last_user_ts(path):
    """Newest genuine-user timestamp, or None. Raises on unreadable/corrupt file."""
    best, bad, total = None, 0, 0
    with open(path, errors="strict") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            try:
                d = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(d, dict) and is_genuine(d) and d.get("timestamp"):
                t = parse_ts(d["timestamp"])
                if best is None or t > best:
                    best = t
    if total and bad * 2 > total:
        raise ValueError(f"corrupt transcript ({bad}/{total} bad lines)")
    return best


def main():
    if os.environ.get("ASK_GUARD_OFF") == "1":
        return 0
    try:
        data = json.loads(sys.stdin.read() or "{}")
        now = now_utc()
        hour = now.astimezone(CT).hour
        if hour >= 21 or hour < 7:
            sys.stderr.write(REASON.format(why="night hours 21:00-07:00 CT") + "\n")
            return 2
        tp = data.get("transcript_path")
        if not tp:
            raise ValueError("no transcript_path")
        ts = last_user_ts(os.path.expanduser(tp))
        if ts is None:
            sys.stderr.write(REASON.format(why="no genuine user message found") + "\n")
            return 2
        age = now - ts
        if age > MAX_AGE:
            sys.stderr.write(REASON.format(why=f"last user message {int(age.total_seconds()//60)} min ago, limit 10") + "\n")
            return 2
        return 0
    except Exception as e:  # fail open
        log_alarm(f"{type(e).__name__}: {str(e)[:160]}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
