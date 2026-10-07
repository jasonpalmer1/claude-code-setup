#!/usr/bin/env python3
"""UserPromptSubmit hook, L-1559 item 4. When this chat wakes for ANY reason, print ONE line per
queued peer message (stdout is added to the turn's context), then archive them. Race-safe: each
item is claimed by an atomic rename; only the winner prints it. Never blocks (always exit 0).
Env overrides (tests): PEER_BATCH_INBOX_DIR, PEER_BATCH_SESSIONS_DIR."""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
MAX_LINES = 15


def main():
    import peer_classify as pc
    d = json.loads(sys.stdin.read() or "{}")
    sid = d.get("session_id")
    if "!now DIGEST:" in str(d.get("prompt", ""))[:400]:
        return 0  # this wake IS a digest; idle-sweep archives its items on confirmed delivery
    home = os.path.expanduser("~/.claude")
    sessions_dir = os.environ.get("PEER_BATCH_SESSIONS_DIR", os.path.join(home, "sessions"))
    inbox = os.environ.get("PEER_BATCH_INBOX_DIR", os.path.join(home, "hub", "inbox"))
    me = [s for s in pc.load_sessions(sessions_dir) if s.get("sessionId") == sid]
    if not sid or len(me) != 1:
        return 0
    pid = me[0]["pid"]
    lines, n = [], 0
    for f in pc.list_items(inbox, pid):
        if n >= MAX_LINES:
            break
        it = pc.read_item(f)
        dst = pc.archive(f, inbox, pid) if it else None
        if dst:
            lines.append("[queued peer msg] " + pc.line_for(it) + " (full: " + dst.replace(home, "~/.claude") + ")")
            n += 1
    left = len(pc.list_items(inbox, pid))
    if lines:
        print("\n".join(lines) + ("\n[+%d more queued, shown on next wake]" % left if left else ""))
    return 0


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
