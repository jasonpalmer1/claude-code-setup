#!/usr/bin/env python3
"""PreToolUse hook (matcher SendMessage), L-1559 item 4. Queue non-urgent peer messages.

Exit 2 + stderr = the send is blocked and the model is told it was queued, not lost.
Exit 0 = send as today. EVERY error path exits 0 (fail open). Kill switch: PEER_BATCH_OFF=1.
Env overrides (tests): PEER_BATCH_INBOX_DIR, PEER_BATCH_SESSIONS_DIR.
"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))


def main():
    if os.environ.get("PEER_BATCH_OFF") == "1" or os.environ.get("PEER_BATCH_BYPASS") == "1":
        return 0
    import peer_classify as pc
    d = json.loads(sys.stdin.read())
    if not isinstance(d, dict) or d.get("tool_name") != "SendMessage":
        return 0
    ti = d.get("tool_input") or {}
    if ti.get("type") not in (None, "message"):
        return 0
    to, msg, summ = ti.get("to") or ti.get("recipient"), ti.get("message") or ti.get("content"), ti.get("summary", "")
    batch, _why = pc.classify(to, msg, summ)
    if not batch:
        return 0
    home = os.path.expanduser("~/.claude")
    sessions_dir = os.environ.get("PEER_BATCH_SESSIONS_DIR", os.path.join(home, "sessions"))
    inbox = os.environ.get("PEER_BATCH_INBOX_DIR", os.path.join(home, "hub", "inbox"))
    sessions = pc.load_sessions(sessions_dir)
    rcpt = pc.resolve_recipient(to, sessions)
    if not rcpt:
        return 0  # unknown / dead / ambiguous: send as today
    me = [s for s in sessions if s.get("sessionId") == d.get("session_id")]
    if me and me[0].get("pid") == rcpt.get("pid"):
        return 0
    sender = (me[0].get("name") if me else None) or "unknown"
    pc.queue_write(inbox, rcpt["pid"], sender, summ, msg)
    sys.stderr.write("Queued for the next digest, not lost (to %s). Non-urgent peer messages are batched; "
                     "the recipient sees it on its next wake or the next digest. Start a message with "
                     "'!now' to send instantly. Do not resend.\n" % (rcpt.get("name") or rcpt["pid"]))
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
