#!/usr/bin/env python3
"""SessionStart / UserPromptSubmit hook — spreading-activation memory retrieval.

This is an optional evolution of the flat, index-only memory system described
in CLAUDE.md.template's "Memory system" section. Instead of "load the whole
index every turn," it activates seed nodes in a small memory GRAPH — a
lightweight, typed-edge graph of links between your memory files — spreads
across those edges, and injects a SHORT pointer block naming the handful of
files most relevant to this session, plus any contradiction the graph knows
about between two memories.

This hook is the retrieval half only. It assumes you (or a subagent you task
with it) maintain a small `graph/` toolset next to your memory files:
  - `graph/build.py` — rebuilds `graph/graph.json` from your memory files'
    frontmatter/links (see memory-graph-refresh.sh for the rebuild trigger).
  - `graph/retrieve.py` — exposes `load(path)` and
    `retrieve(graph, query, cwd, mode, k)` returning
    `{"results": [{"id", "cluster", "description", "activation"}, ...],
      "flags": [{"kind": "contradicts"|"superseded", "pair": [id, id]}, ...]}`.
That engine isn't included here — it's a DIY layer once you have enough
memory files that a flat index stops being enough. This hook is the scaffold
that calls it.

Fails open, always: if the graph is missing, stale, or retrieval errors, this
prints nothing and exits 0. A memory hook must never be able to block a
session.

Customize MEMORY_DIR to your own layout (see <MEMORY_DIR> in
CLAUDE.md.template). Wire in settings.json:
  {"hooks": {"SessionStart": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/memory-activate.py"}]}]}}
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Customize: point this at your own memory directory, e.g. the layout from
# CLAUDE.md.template's <MEMORY_DIR>.
MEMORY_DIR = Path.home() / ".claude/projects/<your-id>/memory"
GRAPH_DIR = MEMORY_DIR / "graph"
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

    # Don't re-surface what this session has already been shown. Without this
    # the UserPromptSubmit path repeats the same pointer block every single
    # turn, which is exactly the always-on context tax this system exists to
    # remove.
    sid = payload.get("session_id") or "nosession"
    seen_path = Path(f"/tmp/claude-memact-{sid}.json")
    try:
        seen = set(json.loads(seen_path.read_text())) if seen_path.exists() else set()
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
        desc = (h.get("description") or "").strip()
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
