#!/usr/bin/env python3
"""emit_once -- hash-gate a per-prompt hook injection (L-1559 item 10).

A UserPromptSubmit hook's stdout stays in the transcript and is re-read on
every later turn. If the block is byte-identical to the one this session
already saw, re-emitting it is pure cost. should_emit() answers "print it?":

  yes  when there is no stored hash for (session_id, hook)   (first prompt)
  yes  when sha256(text) differs from the stored hash          (content changed)
  yes  on every REFRESH_EVERY-th suppressed prompt (off unless EMIT_ONCE_REFRESH=N)
  yes  on ANY error at all                                     (fail OPEN)
  no   otherwise

reset(session_id) forgets every hook's hash for that session; call it on
SessionStart (compaction drops earlier injections from context).

State: one small JSON file per (session, hook) under EMIT_ONCE_DIR
(default ~/.claude/hub/state/hooknote-cache). Sessions never share files.

CLI (for shell callers / tests):
  emit_once.py check <session_id> <hook>   text on stdin; prints text if it
                                           should be emitted, else nothing
  emit_once.py reset <session_id>
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

# the operator 2026-10-05 (efficiency tap 22): emit only when content changed. The old
# every-10th-prompt refresh is off by default (SessionStart/compact still resets).
# EMIT_ONCE_REFRESH=N turns it back on.
REFRESH_EVERY = int(os.environ.get("EMIT_ONCE_REFRESH") or 0)


def _dir() -> Path:
    return Path(os.environ.get("EMIT_ONCE_DIR") or str(Path.home() / ".claude/hub/state/hooknote-cache"))


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(s))[:80] or "x"


def _path(session_id: str, hook: str) -> Path:
    return _dir() / f"{_safe(session_id)}.{_safe(hook)}.json"


def should_emit(session_id, hook: str, text: str) -> bool:
    """True if the caller should print `text`. Never raises; fails open."""
    try:
        if not session_id or not text:
            return True
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
        p = _path(session_id, hook)
        state = {}
        try:
            state = json.loads(p.read_text())
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}  # missing or corrupt -> treat as first prompt
        skipped = state.get("skipped")
        emit = (
            state.get("h") != digest
            or not isinstance(skipped, int)
            or (REFRESH_EVERY > 0 and skipped + 1 >= REFRESH_EVERY)
        )
        new = {"h": digest, "skipped": 0 if emit else skipped + 1}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(new))
        os.replace(tmp, p)
        return emit
    except Exception:
        return True


def reset(session_id) -> None:
    """Forget all stored hashes for this session. Never raises."""
    try:
        if not session_id:
            return
        for f in _dir().glob(f"{_safe(session_id)}.*.json"):
            try:
                f.unlink()
            except OSError:
                pass
    except Exception:
        pass


def main(argv) -> int:
    try:
        if len(argv) >= 4 and argv[1] == "check":
            text = sys.stdin.read()
            if should_emit(argv[2], argv[3], text):
                sys.stdout.write(text)
        elif len(argv) >= 3 and argv[1] == "reset":
            reset(argv[2])
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
