#!/usr/bin/env python3
"""Transcript-size tripwire — fires on every user prompt in every session.

the operator, 2026-08-11: "every single chat ... constantly monitored ... checkpoint
run once we hit that spot." Mechanizes feedback_clear_signal_last_line's
thresholds (~$45/MB on his ledger): >=1.5MB = checkpoint due at this pause;
>=3MB = log NOW and keep working (2026-09-23: never instruct /clear). Silent below threshold.

UserPromptSubmit hook: stdout is injected as context for the model.
Exit 0 always — monitoring must never block a prompt.
"""
import json
import os
import sys

WARN_BYTES = 2_000_000   # ≈250k context tokens — the operator 2026-09-07 (raised from his initial 200k
                         # in the same session). PROMPT A CLEAR here, not merely 'checkpoint due'.
                         # NOTE: this hook can only see the transcript FILE SIZE, never the live
                         # context-token count, so 250k tokens is approximated at ~2.0MB of
                         # transcript JSONL (measured ratio on this machine ≈125k tokens per MB).
                         # If that ratio drifts, retune this number, not the rule.
HARD_BYTES = 3_000_000   # clear NOW

def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = payload.get("transcript_path") or ""
        size = os.path.getsize(path) if path and os.path.exists(path) else 0
    except Exception:
        return 0  # never block on our own failure

    # the operator 2026-09-23 (standing rule, explicit yes): chats NEVER stop to ask him to /clear.
    # At threshold: write the log, then KEEP WORKING; auto-compact handles context (L-0802).
    if size >= HARD_BYTES:
        print(
            f"🚨 TRANSCRIPT MONITOR: {size/1_048_576:.1f} MB (past 3.0 MB). If this session's log is not "
            "current, update it NOW (/log: Resume state + Pickup prompts, commit), then KEEP WORKING. "
            "Never ask the operator to /clear and never stop for context size: auto-compact handles it "
            "(the operator standing rule 2026-09-23). If you truly cannot continue, SendMessage the hub for a hand-off."
        )
    elif size >= WARN_BYTES:
        print(
            f"⚠️ TRANSCRIPT MONITOR: {size/1_048_576:.1f} MB (~250k context). Checkpoint due: make sure "
            "this session's log is current (/log with Resume state + Pickup prompts, commit), then KEEP "
            "WORKING. Never ask the operator to /clear (the operator standing rule 2026-09-23)."
        )
    return 0

if __name__ == "__main__":
    sys.exit(main())
