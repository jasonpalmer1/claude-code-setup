#!/usr/bin/env python3
"""SessionEnd hook: Layer A of the orphan-process reaper (plan section 3, 2026-09-15).

Fires the moment a session ends gracefully. Scopes STRICTLY to registry entries
whose `session_id` equals this event's session_id -- naturally excludes a
concurrently-running sibling session with an identical-looking helper, no
PID-chain inference needed, session_id equality is exact for a real whole-
session end (every helper under that session_id, however it was registered,
is fair game once the whole tree is gone).

Also imported by subagent-stop-reap.py (section 9 correction C2), which reuses
every function below UNCHANGED but scopes by `agent_id` instead of
`session_id` -- see that file's module docstring for why the two must use
different scoping keys.

CORRECTNESS RULES THIS FILE IMPLEMENTS (each from the plan's measured incidents):
  - Snapshot the process tree ONCE per invocation before anything is killed;
    compute the full descendant set from that snapshot; never re-query `ps`
    mid-sweep (a just-killed process's child re-parents to ppid=1 and would
    look like a fresh orphan to a second query).
  - Kill the ENTIRE descendant set, root and children together, not
    root-then-wait-then-children (measured by hand: killing the root alone
    left a node + 5 chrome-headless-shell children alive).
  - After every TERM and every KILL, confirm death by `kill -0` on that exact
    PID. "Signal sent" is never "process dead". A pid surviving both earns a
    named FAILURE line, never a swallowed exception.
  - Liveness + identity: PID plus an `lstart` match (PID-reuse guard). Command
    strings are for classification only, never for liveness.
  - The log records pre-kill set size, post-kill verified-dead count, and
    every FAILURE pid.

Fails OPEN: bare try/except at the top, bounded runtime (signal alarm),
always exits 0. A bug here must never block session close -- only skip the
reap and log the miss (mirrors ~/.claude/hooks/context-firewall.py's shape).

L-0365 FIX (2026-09-28, ~/.claude/hub/reports/L-0365/plan.md sections 3b/3c):
`main()`'s SIGALRM bound is 5s, not the original 30s -- a SessionEnd hook
runs on the session's own exit path, so a slow run delays the session
actually closing, unlike Layer B's unattended periodic sweep. TERM_WAIT_S/
KILL_VERIFY_WAIT_S were tightened to fit inside that bound with margin (see
their own comments below). `SESSION_END_REAP_DRY_RUN=1` routes through the
same `reap_matching_entries(..., dry_run=True)` path Layer B's own tests
already exercise -- logs "WOULD KILL", sends no signal, never touches the
registry for still-live entries.
"""
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
# Overridable via env for test isolation (orphan-reaper.sh supports the same
# two vars) -- production always falls back to the real paths below.
REGISTRY_PATH = os.environ.get("ORPHAN_REGISTRY", f"{HOME}/.claude/hub/state/orphan-registry.jsonl")
PATTERNS_PATH = os.environ.get("ORPHAN_PATTERNS", f"{HOME}/.claude/routines/orphan-reaper-patterns.json")
REAPER_LOG_DIR = os.environ.get("ORPHAN_REAPER_LOG_DIR", f"{HOME}/.claude/hub/reaper-logs")
FAILURE_LEDGER = f"{REAPER_LOG_DIR}/reaper-failures.jsonl"
ERR_LOG = f"{HOME}/.claude/hub/hook-errors.log"
TERM_WAIT_S = 1.5   # L-0365 (2026-09-28): tighter than Layer B's dev-server-reaper
                     # (also 5s) because this hook runs ON session exit under a
                     # 5s SIGALRM bound (main(), below) -- unlike Layer B's
                     # unattended periodic sweep, a slow SessionEnd hook delays
                     # the session actually closing. TERM_WAIT_S + KILL_VERIFY_WAIT_S
                     # must stay well under the alarm bound so a run that needs
                     # to escalate still has time to verify and log before the
                     # alarm fires; proven under test (scenario 5, sessionend
                     # reap tests).
KILL_VERIFY_WAIT_S = 0.5


# --------------------------- small logging helpers ---------------------------

def _err(msg: str) -> None:
    try:
        os.makedirs(os.path.dirname(ERR_LOG), exist_ok=True)
        with open(ERR_LOG, "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                     f"session-end-reap: {msg}\n")
    except Exception:
        pass


def log_path_for_today() -> str:
    stamp = datetime.date.today().isoformat()
    return f"{REAPER_LOG_DIR}/session-end-{stamp}.log"


def say(path: str, line: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except Exception:
        pass


def append_failure_ledger(pid: int, lstart: str, command: str, source: str) -> None:
    try:
        os.makedirs(os.path.dirname(FAILURE_LEDGER), exist_ok=True)
        with open(FAILURE_LEDGER, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "pid": pid, "lstart": lstart, "command": command, "source": source,
            }) + "\n")
    except Exception:
        pass


# --------------------------- registry I/O ---------------------------

def load_registry() -> list:
    entries = []
    try:
        with open(REGISTRY_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return entries


def save_registry(entries: list) -> None:
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    os.replace(tmp, REGISTRY_PATH)


# --------------------------- ps snapshot (ONCE per sweep) ---------------------------

def etime_to_seconds(etime: str) -> int:
    """Mirrors fleet-reaper.sh's etime_secs(): 'D-HH:MM:SS' | 'HH:MM:SS' | 'MM:SS'."""
    etime = (etime or "").strip()
    days = 0
    if "-" in etime:
        dpart, etime = etime.split("-", 1)
        try:
            days = int(dpart)
        except ValueError:
            days = 0
    try:
        parts = [int(p) for p in etime.split(":")]
    except ValueError:
        return days * 86400
    if len(parts) == 2:
        h, m, s = 0, parts[0], parts[1]
    elif len(parts) == 3:
        h, m, s = parts
    else:
        return days * 86400
    return days * 86400 + h * 3600 + m * 60 + s


def norm_lstart(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def lstart_epoch(s):
    s = norm_lstart(s)
    if not s:
        return None
    try:
        return int(datetime.datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp())
    except Exception:
        return None


def lstart_matches(a, b) -> bool:
    """The PID-reuse guard (T4). Plain normalized-string equality, deliberately
    NOT epoch-conversion: `ps -o lstart=` on this OS always prints an
    internally-consistent string (the weekday is derived from the date, never
    stored independently), so string equality after collapsing whitespace
    (macOS pads single-digit days with an extra space) is both simpler and
    fully faithful. An earlier version compared via epoch instead, which
    silently discards the weekday field -- caught live by this file's own
    T4 unit test with an adversarial (weekday-inconsistent) pair that a real
    ps row could never produce, but a corrupted/hand-edited registry line
    could: two visibly different strings parsed to the identical epoch and
    wrongly compared equal. Never treat two empty strings as a match."""
    na, nb = norm_lstart(a), norm_lstart(b)
    return bool(na) and na == nb


def take_snapshot(timeout=10) -> dict:
    """ONE bulk ps call for pid/ppid/etime/command, ONE bulk ps call for
    pid/lstart, issued back-to-back before any kill signal is sent. This is
    the single frozen view of the world this whole sweep acts on -- see the
    module docstring's correctness rules."""
    rows = {}
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime=,command="],
            capture_output=True, text=True, timeout=timeout,
        )
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 3)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            etime = parts[2]
            command = parts[3] if len(parts) > 3 else ""
            rows[pid] = {"ppid": ppid, "etime": etime, "command": command, "lstart": ""}
    except Exception as e:
        _err(f"bulk ps failed: {e}")
        return {}

    try:
        out2 = subprocess.run(
            ["ps", "-axo", "pid=,lstart="],
            capture_output=True, text=True, timeout=timeout,
        )
        for line in out2.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_str, _, lstart = line.partition(" ")
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            if pid in rows:
                rows[pid]["lstart"] = lstart.strip()
    except Exception as e:
        _err(f"lstart ps failed: {e}")
    return rows


def build_children_map(snapshot: dict) -> dict:
    children = {}
    for pid, row in snapshot.items():
        children.setdefault(row["ppid"], []).append(pid)
    return children


def collect_descendants(root_pid: int, children_map: dict) -> set:
    """Recursive walk over the FROZEN snapshot's child map, never a live re-query."""
    seen = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for child in children_map.get(pid, []):
            if child not in seen:
                stack.append(child)
    return seen


# --------------------------- liveness / kill ---------------------------

def _is_zombie(pid: int) -> bool:
    """L-0365 round-3 (found while fixing bug-gate C1): a killed process whose
    real parent never calls wait() on it (e.g. the parent itself later exec'd
    into a program that does no reaping of its own, like `exec sleep 60`)
    lingers as a zombie -- kill -0 keeps succeeding against it, since the PID
    slot is still occupied, even though the process has already exited and
    holds no real resources (confirmed live: a killed `http.server` child
    shows STAT=Z/<defunct>, zero open fds via lsof). Without this check,
    is_alive() reports a zombie as a genuine survivor, logging a false
    FAILURE for something that is, for every purpose that matters, dead."""
    try:
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=2)
        return out.stdout.strip().startswith("Z")
    except Exception:
        return False  # unknown -> don't mask a real survivor as a zombie


def is_alive(pid: int) -> bool:
    """kill -0: the ONLY thing that counts as liveness proof, EXCEPT a zombie
    (see _is_zombie) -- a PermissionError means the pid exists but is owned
    by another user -- alive, just unkillable by us; ProcessLookupError means
    gone."""
    try:
        os.kill(pid, 0)
        return not _is_zombie(pid)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return True  # unknown -> never assume dead



def tree_kill_and_verify(pids: set, log_path: str, dry_run: bool, source: str,
                          snapshot: dict) -> dict:
    """TERM the whole set together, verify by kill -0, escalate survivors to
    KILL, verify again, log any pid that survives both as a named FAILURE.
    Returns {"pre_kill": int, "post_kill_dead": int, "failures": [pid, ...]}."""
    pre_kill = len(pids)
    if pre_kill == 0:
        return {"pre_kill": 0, "post_kill_dead": 0, "failures": []}

    if dry_run:
        for pid in sorted(pids):
            cmd = snapshot.get(pid, {}).get("command", "?")
            say(log_path, f"{source}: WOULD KILL pid={pid} cmd={cmd[:160]}")
        say(log_path, f"{source}: DRY_RUN pre_kill_set_size={pre_kill} "
                        f"(nothing sent, nothing verified)")
        return {"pre_kill": pre_kill, "post_kill_dead": 0, "failures": [],
                "dry_run": True}

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            _err(f"TERM failed pid={pid}: {e}")
    say(log_path, f"{source}: sent SIGTERM to {pre_kill} pid(s): {sorted(pids)}")

    time.sleep(TERM_WAIT_S)
    survivors_term = {p for p in pids if is_alive(p)}
    dead_after_term = pre_kill - len(survivors_term)
    say(log_path, f"{source}: after TERM+{TERM_WAIT_S}s, dead={dead_after_term} "
                    f"survived={len(survivors_term)} {sorted(survivors_term) or ''}")

    failures = []
    if survivors_term:
        for pid in survivors_term:
            say(log_path, f"{source}: pid={pid} survived TERM -- escalating to SIGKILL")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as e:
                _err(f"KILL failed pid={pid}: {e}")
        time.sleep(KILL_VERIFY_WAIT_S)
        for pid in survivors_term:
            if is_alive(pid):
                failures.append(pid)
                cmd = snapshot.get(pid, {}).get("command", "?")
                lstart = snapshot.get(pid, {}).get("lstart", "?")
                say(log_path, f"{source}: FAILURE pid={pid} survived TERM AND KILL "
                                f"cmd={cmd[:160]}")
                append_failure_ledger(pid, lstart, cmd, source)

    post_kill_dead = pre_kill - len(failures)
    say(log_path, f"{source}: pre_kill_set_size={pre_kill} "
                    f"post_kill_verified_dead={post_kill_dead} "
                    f"failures={failures or 'none'}")
    return {"pre_kill": pre_kill, "post_kill_dead": post_kill_dead, "failures": failures}


# --------------------------- the shared reap orchestration ---------------------------

def reap_matching_entries(matching_entries: list, log_path: str, dry_run: bool,
                            source: str) -> dict:
    """Given registry entries already filtered to the right scope (by
    session_id for SessionEnd, by agent_id for SubagentStop), do the full
    tree-kill pipeline. Returns a summary dict and the set of registry
    entries that should be DROPPED from the registry file (reaped, or
    provably stale/pid-reused -- never entries that were merely skipped
    because the owner is still alive or keep_until is in the future)."""
    snapshot = take_snapshot()
    children_map = build_children_map(snapshot)

    now = time.time()
    roots = []
    to_drop = []
    for entry in matching_entries:
        pid = entry.get("pid")
        reg_lstart = entry.get("lstart", "")
        if pid is None:
            continue
        row = snapshot.get(pid)
        if row is None:
            say(log_path, f"{source}: entry pid={pid} not in current snapshot "
                            f"(already gone) -- dropping from registry")
            to_drop.append(entry)
            continue
        if not lstart_matches(reg_lstart, row["lstart"]):
            say(log_path, f"{source}: entry pid={pid} lstart mismatch "
                            f"(registered={reg_lstart!r} now={row['lstart']!r}) "
                            f"-- PID REUSE, not touching this process, dropping stale entry")
            to_drop.append(entry)
            continue
        keep_until = entry.get("keep_until")
        if keep_until:
            try:
                if float(keep_until) > now:
                    say(log_path, f"{source}: pid={pid} skipped: keep_until in future "
                                    f"({keep_until})")
                    continue
            except (TypeError, ValueError):
                pass
        roots.append(pid)

    total_set = set()
    for root in roots:
        total_set |= collect_descendants(root, children_map)

    result = tree_kill_and_verify(total_set, log_path, dry_run, source, snapshot)

    if not dry_run:
        dead_pids = total_set - set(result.get("failures", []))
        for entry in matching_entries:
            if entry.get("pid") in dead_pids:
                to_drop.append(entry)

    result["roots"] = roots
    result["to_drop"] = to_drop
    return result


# --------------------------- SessionEnd entrypoint ---------------------------

def main() -> int:
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError()))
    signal.alarm(5)  # L-0365: bounded well under a second, never let a hung
                       # ps/kill delay session close -- see TERM_WAIT_S comment.
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return 0
        data = json.loads(raw)
        session_id = data.get("session_id")
        if not session_id:
            return 0

        registry = load_registry()
        matching = [e for e in registry if e.get("session_id") == session_id]
        if not matching:
            return 0

        dry_run = os.environ.get("SESSION_END_REAP_DRY_RUN") == "1"
        log_path = log_path_for_today()
        say(log_path, f"SESSION-END: session_id={session_id} "
                        f"{len(matching)} registry entr(y/ies) in scope"
                        f"{' (DRY-RUN)' if dry_run else ''}")
        result = reap_matching_entries(matching, log_path, dry_run=dry_run,
                                         source="SESSION-END")

        if result.get("to_drop"):
            drop_ids = {(e.get("pid"), e.get("registered_at")) for e in result["to_drop"]}
            remaining = [e for e in registry
                         if (e.get("pid"), e.get("registered_at")) not in drop_ids]
            save_registry(remaining)

        return 0
    except Exception as e:
        _err(f"internal failure, failing open: {e}")
        return 0
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
