#!/usr/bin/env python3
"""SessionStart hook — one-line nag when the memory-graph health check looks bad.

Reads only the LAST row of graph/health-history.jsonl (written by
graph/maintain.py) and prints a single short plain-English line ONLY when
something is actually wrong: open issues, a silent (null) benchmark, or a
health check that hasn't run in over a week. Prints nothing when healthy —
this is a nag, not a status dashboard.

Fails open, always: any error (missing file, bad JSON, whatever) means print
nothing and exit 0. A memory hook must never be able to block a session.

Wire in settings.json (this script does not register itself):
  {"hooks": {"SessionStart": [{"hooks": [
     {"type": "command", "command": "python3 ~/.claude/hooks/memory-health-nag.py"}]}]}}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _home_memory_dir():
    """~/.claude/projects/<home-slug>/memory -- the slug Claude Code
    derives from the real $HOME path (str(Path.home()).replace('/', '-')),
    computed at runtime so this hook works on any machine, not just the
    one it was written on."""
    slug = str(Path.home()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


HISTORY = _home_memory_dir() / "graph/health-history.jsonl"
STALE_DAYS = 8

# --- L-1079 part 2: session-end backfill catch-up --------------------------
# Part 1 (session-end-log.sh) fixed the ~99%-failure nohup/disown bug, but a
# backfill can still miss for reasons outside the harness's kill behavior
# (the machine sleeps or loses power between SessionEnd firing and the
# detached worker's headless `claude -p` call finishing). This is the
# independent second net hub-bc's H1-H4 stamp asked for, piggybacked on this
# already-registered SessionStart hook for the same reason logging_coverage()
# below is (no settings.json edit needed).
L1079_HOME = Path.home() / ".claude"
L1079_AUTOLOG = Path(os.environ.get("L1079_BACKFILL_AUTOLOG") or (L1079_HOME / "hub/autolog.log"))
L1079_CONVOS = Path(os.environ.get("L1079_BACKFILL_CONVOS_DIR")
                     or (L1079_HOME / "projects/<home-slug>/memory/conversations"))
L1079_PROJECTS = Path(os.environ.get("L1079_BACKFILL_PROJECTS_DIR") or (L1079_HOME / "projects"))
L1079_STATE_FILE = Path(os.environ.get("L1079_BACKFILL_STATE_FILE")
                         or (L1079_HOME / "hub/admin/sessionend-backfill-attempted.json"))
L1079_LOCK_FILE = Path(os.environ.get("L1079_BACKFILL_LOCK_FILE")
                        or (L1079_HOME / "hub/admin/sessionend-backfill.lock"))
L1079_DETACH_LAUNCHER = Path(os.environ.get("L1079_BACKFILL_DETACH_LAUNCHER")
                              or (L1079_HOME / "hooks/lib/detach_launcher.py"))
L1079_WORKER_SCRIPT = Path(os.environ.get("L1079_BACKFILL_WORKER_SCRIPT")
                            or (L1079_HOME / "hooks/pre-compact-real-log-worker.sh"))
L1079_HOOK_ERRORS_LOG = Path(os.environ.get("L1079_BACKFILL_HOOK_ERRORS_LOG")
                              or (L1079_HOME / "hub/hook-errors.log"))
L1079_WINDOW_HOURS = int(os.environ.get("L1079_BACKFILL_WINDOW_HOURS") or 72)
L1079_CAP = int(os.environ.get("L1079_BACKFILL_CAP") or 3)
L1079_MIN_SIZE = 500_000  # matches session-end-log.sh's own log-worthy gate


def main() -> int:
    try:
        if not HISTORY.exists():
            return 0
        lines = [l for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return 0
        row = json.loads(lines[-1])

        issues = row.get("issues") or 0
        recall_graph = row.get("recall_graph")
        date_str = row.get("date")

        age_days = None
        if date_str:
            try:
                last = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - last).days
            except ValueError:
                age_days = None

        problems: list[str] = []
        if issues:
            problems.append(f"{issues} issue{'s' if issues != 1 else ''}")
        if recall_graph is None:
            problems.append("benchmark silent")
        if age_days is not None and age_days > STALE_DAYS:
            problems.append(f"last run {age_days}d ago")

        if not problems:
            return 0

        print(f"⚠ memory-graph health: {', '.join(problems)} "
              f"— run graph/maintain.py")
    except Exception:
        return 0
    return 0


def _l1079_already_logged(sid: str) -> bool:
    """True if `sid` already carries a real `logged-session:` footer anywhere
    in CONVOS — covers the case where part 1 (or a manual /log, or a prior
    run of this same catch-up) already closed it out since the MISS receipt
    was written. H2: never re-backfill a session that already has a log."""
    if not L1079_CONVOS.exists():
        return False
    needle = f"logged-session: {sid}"
    for md in L1079_CONVOS.glob("*.md"):
        try:
            if needle in md.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def _l1079_resolve_transcript(sid: str) -> Path | None:
    """The transcript for `sid`, iff it is a DIRECT child of a project dir
    (`<project>/<sid>.jsonl`). This is a structural exclusion of subagent and
    worker transcripts, not just a documented assumption: per
    process-register.py, a subagent transcript lives one level deeper, at
    `.../subagents/agent-<agent_id>.jsonl` — a different filename shape,
    keyed by agent id rather than session id, that this direct-child lookup
    can never match. worker-lessons.py also notes subagents get no
    SessionEnd hook at all, so they should never produce a MISS receipt in
    the first place; this is the belt-and-suspenders check H2 asked for on
    top of that."""
    if not L1079_PROJECTS.exists():
        return None
    for proj in L1079_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        cand = proj / f"{sid}.jsonl"
        if cand.is_file():
            return cand
    return None


def _l1079_load_state() -> dict:
    try:
        return json.loads(L1079_STATE_FILE.read_text())
    except Exception:
        return {}


def _l1079_save_state(state: dict) -> None:
    try:
        L1079_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        L1079_STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    except OSError:
        pass


def _l1079_candidates(state: dict) -> list[tuple[datetime, str]]:
    """MISS sids from the last L1079_WINDOW_HOURS, oldest first, minus
    anything already attempted (any outcome — a repeatedly-failing sid is
    never retried forever, per the plan) or already logged."""
    if not L1079_AUTOLOG.exists():
        return []
    now = datetime.now(timezone.utc)
    out: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    try:
        lines = L1079_AUTOLOG.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    for line in lines:
        parts = line.split()
        if len(parts) < 3 or parts[1] != "MISS":
            continue
        sid = next((t[4:] for t in parts if t.startswith("sid=")), "")
        if not sid or sid in seen:
            continue
        try:
            stamp = datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if now - stamp > timedelta(hours=L1079_WINDOW_HOURS):
            continue
        if sid in state or _l1079_already_logged(sid):
            continue
        seen.add(sid)
        out.append((stamp, sid))
    out.sort(key=lambda t: t[0])
    return out


def sessionend_backfill_catchup() -> None:
    """L-1079 part 2 entry point. Fails open, always — must never block a
    session start. H2 (hub-bc stamp): one global lock so only one backfill
    runs machine-wide at a time; capped at 3 per start; skips trivial-size,
    subagent and worker transcripts; never re-backfills an already-logged
    session."""
    lock_fd = None
    try:
        import fcntl
        L1079_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(str(L1079_LOCK_FILE), os.O_CREAT | os.O_RDWR)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return  # another catch-up is already running machine-wide

        state = _l1079_load_state()
        candidates = _l1079_candidates(state)[:L1079_CAP]
        for _stamp, sid in candidates:
            state[sid] = {"attempted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            tp = _l1079_resolve_transcript(sid)
            if tp is None:
                continue
            try:
                if tp.stat().st_size < L1079_MIN_SIZE:
                    continue
            except OSError:
                continue
            rundir = None
            try:
                import tempfile
                rundir = Path(tempfile.mkdtemp(prefix="precompact-reallog."))
                payload = json.dumps({"session_id": sid, "transcript_path": str(tp)})
                (rundir / "input.json").write_text(payload)
                subprocess.run(
                    ["python3", str(L1079_DETACH_LAUNCHER),
                     "--log", str(L1079_HOOK_ERRORS_LOG), "--",
                     "bash", str(L1079_WORKER_SCRIPT),
                     "--input-file", str(rundir / "input.json"),
                     "--trigger", "sessionstart-backfill",
                     "--rundir", str(rundir)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                )
            except Exception:
                if rundir is not None:
                    try:
                        import shutil
                        shutil.rmtree(rundir, ignore_errors=True)
                    except Exception:
                        pass
        _l1079_save_state(state)
    except Exception:
        pass
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass


def logging_coverage() -> None:
    """Piggybacked 2026-09-02 so the logging-coverage line reaches every session
    start WITHOUT editing settings.json, which hot-reloads into every running
    session ([[feedback_never_edit_global_settings_live]]). Cached daily, so the
    scan runs once a day, not once a session. Fails open like everything here."""
    try:
        import runpy
        runpy.run_path(str(Path.home() / ".claude/hooks/log-coverage.py"),
                       run_name="_coverage")
    except Exception:
        pass


if __name__ == "__main__":
    rc = main()
    logging_coverage()
    sessionend_backfill_catchup()
    sys.exit(rc)
