#!/usr/bin/env python3
"""Transcript-size tripwire — fires on every user prompt in every session.

Mechanizes the session-hygiene rule in CLAUDE.md.template ("a day boundary is
always a clear point," "checkpoint at pause points"): a WARN threshold means
a checkpoint is due at the next natural pause, and a HARD threshold means run
the closing rites and clear now, because every further turn re-bills this
entire transcript from scratch.

WARN_BYTES / HARD_BYTES below are starting points — once you have a real
token ledger (see the README's "mine your own ledger"), work out your own
$/MB rate for a typical session and recalibrate these to whatever transcript
size corresponds to "this is getting expensive" and "this is genuinely a lot"
for you.

UserPromptSubmit hook: stdout is injected as context for the model.
Exit 0 always — monitoring must never block a prompt.
"""
import json
import os
import sys

WARN_BYTES = 1_500_000   # checkpoint due
HARD_BYTES = 3_000_000   # clear NOW

def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = payload.get("transcript_path") or ""
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
    except Exception:
        return 0  # never block on our own failure

    if size >= HARD_BYTES:
        print(
            f"🚨 TRANSCRIPT MONITOR: {size/1_048_576:.1f} MB (hard cap 3.0 MB). "
            "Run the closing rites NOW — extraction sweep, log the session, "
            "update the board/Session-memory, commit — then end this reply "
            "telling the user to clear now. Every further turn re-bills this "
            "entire transcript."
        )
    elif size >= WARN_BYTES:
        print(
            f"⚠️ TRANSCRIPT MONITOR: {size/1_048_576:.1f} MB (checkpoint threshold 1.5 MB). "
            "A checkpoint is due at the next natural pause: run the closing rites "
            "(extraction sweep, log, board update, commit) and tell the user it's "
            "safe to clear."
        )
    return 0

if __name__ == "__main__":
    sys.exit(main())
