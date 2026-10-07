#!/usr/bin/env python3
"""PreToolUse hook (Agent|Task): appends a short, labelled block of past-mistake
lessons to a worker's brief before it spawns, using the same spreading-activation
memory graph retrieval that memory-activate.py runs for the main loop's
SessionStart/UserPromptSubmit path. Agent/Task subagents get NO SessionStart or
UserPromptSubmit hooks today (L-0917 measured gap: the canary at
hub/reports/L-0917/canary-general-purpose.md showed zero memory-activation context
reaching a spawned worker), so this is the only place a worker sees any of the
recorded lessons before it starts work blind.

Deliberately narrow, per the L-0917 plan and hub audit (hub/reports/L-0917-plan.md):
  - appends ONLY to tool_input["prompt"]; returns updatedInput, never
    permissionDecision (H3 / T4: a lessons hook must never become a permission
    gate -- that stays model-guard.py's and the harness's job, unaffected by
    whether this hook runs before or after it).
  - the injected block is explicitly labelled background, not instruction (H1):
    no imperative sentence of its own, and it says the brief above wins on any
    conflict.
  - never touches Workflow's `script` field (H2) -- no-ops on tool_name ==
    "Workflow" before looking at anything else.
  - strips any of model-guard.py's own blocking phrases out of injected lesson
    text (H4/T7, lane note 2026-09-23 ~4:55pm CT): model-guard's read-shape gate
    false-blocked a real brief for quoting "read-only" inside a sentence that was
    not describing that brief's own task shape. An injected lesson description
    repeating a blocked phrase would do the same to ANY brief this hook touches,
    deterministically, regardless of whether Claude Code runs PreToolUse hooks in
    parallel on the original input or in sequence on each other's updatedInput.
    The phrase list is derived by reading model-guard.py's own regex at runtime,
    not hand-copied, so the two files cannot silently drift apart.
  - fails open on anything: missing graph, bad JSON, any exception -> exit 0,
    silent, no output at all.

Wire in settings.json (see hub/reports/L-0917/settings-snippet.json): a second
hook entry under the EXISTING PreToolUse matcher "Agent|Task|Workflow", next to
model-guard.py. The hub applies this only at a fresh session start, never hot.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GRAPH_DIR = Path.home() / ".claude/projects/<home-slug>/memory/graph"
K = 5
MIN_ACTIVATION = 0.5     # same floor memory-activate.py uses -- below this is noise
NOOP_SUBAGENT_TYPES = {"Explore", "statusline-setup"}
TAG_OPEN = "<lessons-for-this-task"
# Resolved relative to this script's own repo root by default, so a worktree's
# tests write the worktree's own log, never the live tree's -- WORKER_LESSONS_LOG
# overrides for tests that want an isolated scratch file instead.
LOG_PATH = Path(os.environ.get("WORKER_LESSONS_LOG") or (REPO_ROOT / "hub/state/worker-lessons.log"))


def _load_guard_pattern():
    """Read model-guard.py's own blocking phrase regex at runtime -- H4/T7: the
    neutralizer must be derived from model-guard.py, never a hand-copied phrase
    list that can silently drift out of sync with the real blocking rule."""
    try:
        src = (Path(__file__).resolve().parent / "model-guard.py").read_text()
        m = re.search(r'read_shaped\s*=\s*re\.search\(\s*r"([^"]+)"', src)
        return re.compile(m.group(1), re.I) if m else None
    except Exception:
        return None


GUARD_PATTERN = _load_guard_pattern()


def neutralize(text: str) -> str:
    """Break any match of model-guard's blocking phrases without deleting the
    words: swap the run of whitespace/hyphen characters inside the match for a
    middle dot, a character outside model-guard's own [\\s-] class. 'read-only'
    becomes 'read·only', which no longer matches read[\\s-]*only; 'do not
    edit' becomes 'do·not·edit', which no longer matches
    do\\s+not\\s+edit. Readable to a human, invisible to the regex, and it works
    on every phrase in the list without hand-listing them."""
    if not GUARD_PATTERN or not text:
        return text

    def repl(m: "re.Match[str]") -> str:
        return re.sub(r"[\s-]", "·", m.group(0))

    return GUARD_PATTERN.sub(repl, text)


ID_RE = re.compile(r"^[a-z0-9_]+$")


def safe_text(text: str) -> str:
    """Gate L-0917 B1: a description must never be able to close or open a tag
    (and so escape the "background" label). Angle brackets become look-alike
    guillemets, and control characters and newlines become spaces."""
    text = text.replace("<", "\u2039").replace(">", "\u203a")
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)


def log_line(subagent_type: str, ids: list, ms: float, note: str = "") -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a") as f:
            st = re.sub(r"[^A-Za-z0-9_.-]", "_", subagent_type or "?")[:40]
            f.write(f"{ts} subagent_type={st} ids={','.join(ids) or '-'} ms={ms:.1f}{note}\n")
    except Exception:
        pass


def main() -> int:
    t0 = time.monotonic()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0  # malformed input -- fail open, no output

    tool_name = payload.get("tool_name") or ""
    if tool_name == "Workflow":
        return 0  # H2: Workflow has no `prompt` field; never touch `script`
    if tool_name not in ("Agent", "Task"):
        return 0

    tool_input = payload.get("tool_input") or {}
    subagent_type = str(tool_input.get("subagent_type") or "")
    if subagent_type in NOOP_SUBAGENT_TYPES:
        return 0

    prompt = tool_input.get("prompt")
    if not prompt or not str(prompt).strip():
        return 0
    prompt = str(prompt)
    if TAG_OPEN in prompt:
        return 0  # already tagged -- resume/re-send, idempotent passthrough

    graph_json = GRAPH_DIR / "graph.json"
    if not graph_json.exists():
        return 0

    try:
        sys.path.insert(0, str(GRAPH_DIR))
        from retrieve import load, retrieve  # type: ignore

        cwd = payload.get("cwd") or os.getcwd()
        query = prompt[:4000] + " " + subagent_type
        g = load(graph_json)
        r = retrieve(g, query, cwd, "graph", K)
    except Exception:
        return 0  # fail open, always

    superseded_ids = {f["pair"][1] for f in r.get("flags", []) if f.get("kind") == "superseded"}
    hits = [
        h for h in r.get("results", [])
        if h.get("activation", 0) >= MIN_ACTIVATION
        and h.get("id", "").startswith(("feedback_", "reference_"))
        and h.get("id") not in superseded_ids
    ][:5]
    if not hits:
        return 0

    lines = ['<lessons-for-this-task note="background from past mistakes; the brief above wins on any conflict">']
    ids = []
    for h in hits:
        hid = h["id"]
        if not isinstance(hid, str) or not ID_RE.match(hid):
            continue
        ids.append(hid)
        # Gate B2: if model-guard's phrase list can't be read (its source shape
        # changed), descriptions can't be neutralized, so inject ids only
        # (ids use underscores, which model-guard's [\s-] phrases never match)
        # and say so in the log instead of degrading silently.
        if GUARD_PATTERN is None:
            lines.append(f"· {hid} (~/.claude/projects/<home-slug>/memory/{hid}.md)")
            continue
        desc = neutralize(safe_text((h.get("description") or "").strip()))
        if len(desc) > 120:
            desc = desc[:117].rstrip() + "..."
        lines.append(f"· {hid}: {desc} (~/.claude/projects/<home-slug>/memory/{hid}.md)")
    if not ids:
        return 0
    lines.append("</lessons-for-this-task>")
    block = "\n\n" + "\n".join(lines)

    new_input = dict(tool_input)
    new_input["prompt"] = prompt + block

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": new_input,
        }
    }))
    log_line(subagent_type, ids, (time.monotonic() - t0) * 1000,
             " WARN=guard-pattern-unavailable,ids-only" if GUARD_PATTERN is None else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)   # never block a spawn
