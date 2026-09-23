#!/usr/bin/env python3
"""SessionStart / UserPromptSubmit hook — spreading-activation memory retrieval.

Replaces "load the whole index every turn" with "activate what this moment needs".
Given the session's cwd and opening prompt, it activates seed nodes in the memory
graph, spreads across typed edges, and injects a SHORT pointer block naming the
handful of memory files worth reading — plus any contradiction the graph knows about.

Fails open, always: if the graph is missing, stale, or the retrieval errors, this
prints nothing and exits 0. A memory hook must never be able to block a session.

Wire in settings.json:
  {"hooks": {"SessionStart": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/memory-activate.py"}]}]}}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _home_memory_dir():
    """~/.claude/projects/<home-slug>/memory -- the slug Claude Code
    derives from the real $HOME path (str(Path.home()).replace('/', '-')),
    computed at runtime so this hook works on any machine, not just the
    one it was written on."""
    slug = str(Path.home()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


GRAPH_DIR = _home_memory_dir() / "graph"
K = 5
MIN_ACTIVATION = 0.5     # below this it's noise; say nothing rather than guess
STALE_DAYS = 14


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        payload = {}

    cwd = payload.get("cwd") or os.getcwd()
    query = (payload.get("prompt") or payload.get("user_prompt") or "").strip()

    # SessionStart has no prompt yet — the cwd alone is the signal.
    if not query and not cwd:
        return 0

    graph_json = GRAPH_DIR / "graph.json"
    if not graph_json.exists():
        return 0

    try:
        sys.path.insert(0, str(GRAPH_DIR))
        from retrieve import load, retrieve  # type: ignore

        g = load(graph_json)
        r = retrieve(g, query or Path(cwd).name, cwd, "graph", K)
    except Exception:
        return 0   # fail open, always

    hits = [x for x in r["results"] if x["activation"] >= MIN_ACTIVATION]
    if not hits:
        return 0

    # Don't re-surface what this session has already been shown. Without this the
    # UserPromptSubmit path repeats the same pointer block every single turn,
    # which is exactly the always-on context tax this system exists to remove.
    sid = payload.get("session_id") or "nosession"
    seen_path = Path(f"/tmp/claude-memact-{sid}.json")
    try:
        seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
        if payload.get("hook_event_name") == "SessionStart":
            seen = set()  # re-surface relevant pointers after resume/compaction
    except (json.JSONDecodeError, OSError):
        seen = set()

    fresh = [h for h in hits if h["id"] not in seen]
    if not fresh:
        return 0
    try:
        seen_path.write_text(json.dumps(sorted(seen | {h["id"] for h in fresh})))
    except OSError:
        pass
    hits = fresh

    lines = ["<memory-activation>",
             "Spreading-activation retrieval over the memory graph surfaced these as",
             "most relevant to this session. Read the ones you actually need — this is",
             "a pointer list, not loaded content.", ""]
    for h in hits:
        # L-0917 gate B1 (same hole as worker-lessons): no tag can open or close.
        desc = (h.get("description") or "").strip().replace("<", "\u2039").replace(">", "\u203a")
        if len(desc) > 96:
            desc = desc[:93] + "..."
        lines.append(f"  · {h['id']}  [{h['cluster']}]")
        if desc:
            lines.append(f"      {desc}")

    contradictions = [f for f in r.get("flags", []) if f["kind"] == "contradicts"]
    if contradictions:
        lines.append("")
        lines.append("  UNRESOLVED CONTRADICTIONS between live memories — do not silently")
        lines.append("  pick one; surface the conflict:")
        for f in contradictions:
            lines.append(f"    ! {f['pair'][0]}  <->  {f['pair'][1]}")

    superseded = [f for f in r.get("flags", []) if f["kind"] == "superseded"]
    if superseded:
        lines.append("")
        for f in superseded:
            lines.append(f"  ~ {f['pair'][1]} is superseded by {f['pair'][0]} — ignore the older one.")

    lines.append("</memory-activation>")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)   # never block a session
