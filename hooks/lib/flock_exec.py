#!/usr/bin/env python3
"""L-1079: acquire an exclusive, per-path flock (bounded wait, fail-open on
timeout), then exec into the given command with the locked fd still open --
so the lock is held for that command's ENTIRE remaining lifetime and is
released automatically by the kernel the instant it exits, for ANY reason,
including SIGKILL. No lock-release/re-acquire bookkeeping needed.

Usage:
    python3 flock_exec.py [--on-giveup-rm PATH ...] <lockfile> -- <command> [args...]

(--on-giveup-rm, if used, MUST precede the positional <lockfile> -- argparse's
REMAINDER-based <command> capture otherwise swallows a later optional flag
whole, undocumented but confirmed empirically.)

Why this exists: hub/bug-gate/L-1079/2026-09-24-sessionend-backfill.md
found that pre-compact-real-log-worker.sh has THREE independent callers
(session-end-log.sh's MISS branch, memory-health-nag.py's SessionStart
catch-up, and this file's own sibling pre-compact-real-log.sh) that can all
dispatch a worker for the same session id with zero coordination between
them -- two concurrent workers for the same sid both pass find_real_log()
before either has written anything, both create/append the same file, and
both lose the git commit lock race. The fix serializes at the one place all
three callers actually converge: the worker process itself. The worker
re-execs itself through this helper before its own find_real_log() check
ever runs (see pre-compact-real-log-worker.sh), so a second worker for the
same sid blocks HERE, and by the time it gets in (or is skipped, see below),
the first worker's find_real_log() re-check sees its own freshly-committed
file and the existing "already logged" skip guard takes over -- no
duplicate work.

Mechanism: os.open() + os.set_inheritable(fd, True) so the fd survives the
exec below (Python 3.4+ / PEP 446 makes new fds close-on-exec by default),
then a POLLED (not a single indefinite blocking call) fcntl.flock(LOCK_EX |
LOCK_NB) loop -- portable to macOS, which ships no flock(1) CLI (that is a
util-linux-only tool; this machine is darwin). Same primitive
memory-health-nag.py's own L-1079 catch-up lock already uses.

Bounded, not truly infinite: gives up after FLOCK_EXEC_CEILING_S (default
180s, matching the worker's own SENTINEL_S default -- an in-flight worker's
own internal watchdog/sentinel timers mean it will have completed, one way
or another, well within that window) and exits 0 WITHOUT running the
command at all -- fail-open, matching this whole subsystem's "best-effort,
never wedges" contract (see pre-compact-real-log-worker.sh's own
docstring). A genuinely dead lock-holder can never cause an unbounded wait
here regardless: flock is released by the kernel the instant the holding
process exits, for any reason.

If the lock file itself cannot even be opened (e.g. a filesystem/permission
problem), this fails open the OTHER direction -- runs the command UNLOCKED
rather than silently dropping a backfill over an unrelated FS hiccup. That
degrades to the pre-L-1079 (pre-lock) behavior in an already-rare case, not
a new failure mode.

--on-giveup-rm PATH can be passed (repeatably) BEFORE the -- separator to
name a path (file or directory) to remove if the ceiling is hit. This
exists because `exec`ing into this helper (as pre-compact-real-log-worker.sh
does) replaces the calling script's own process image -- if this helper
gives up and simply returns, there is no remaining shell code left to run
any cleanup of its own (the process is now, and always was from this
point on, this Python process, not the bash script that invoked it). Since
this exact codebase already had one bug-gate-caught class of bug be an
"unbounded leak into $TMPDIR" (hub/reports/L-0953-r2-buggate.md, a
different leak, same root shape -- an owner-less RUNDIR), the give-up path
here removes its caller's RUNDIR itself rather than leaving that class of
bug for a future bug-gate to catch again.

Env overrides (tests only; unset in production):
    FLOCK_EXEC_CEILING_S -- total seconds to keep polling before giving up
                            (default 180).
    FLOCK_EXEC_POLL_S    -- seconds between poll attempts (default 0.5).

Env var SET by this helper for the exec'd command to read: FLOCK_EXEC_FD,
the fd number the lock is held on. This exists because fork() (unlike
exec()) always duplicates the WHOLE fd table regardless of close-on-exec --
so if the exec'd command itself later forks a background child that
outlives it (e.g. a watchdog/sentinel subshell) without that child ever
calling exec, the child inherits its own copy of this same open file
description and keeps the flock held even after the command that actually
acquired it has exited. flock(2) locks live on the open file description,
not any single fd, so the lock is only truly released once EVERY fd
referencing it is closed. A caller that forks such a child MUST close its
own copy of $FLOCK_EXEC_FD immediately (e.g. `eval "exec ${FLOCK_EXEC_FD}<&-"`
in bash) right when that child starts, or the lock can be held hostage by
an orphaned, otherwise-harmless background process for up to this
process's own natural lifetime.
"""
import argparse
import fcntl
import os
import shutil
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Acquire a per-path flock (bounded wait) then exec a command, fail-open throughout."
    )
    parser.add_argument("lockfile", help="path to the lock file (created if missing)")
    parser.add_argument(
        "--on-giveup-rm",
        action="append",
        default=[],
        metavar="PATH",
        help="path to remove (file or directory, best-effort) if the lock is never acquired; repeatable",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to launch once the lock is held; pass a literal -- before it",
    )
    args = parser.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("flock_exec.py: no command given (pass -- <command> [args...])", file=sys.stderr)
        return 2

    try:
        ceiling = float(os.environ.get("FLOCK_EXEC_CEILING_S", "180"))
    except ValueError:
        ceiling = 180.0
    try:
        poll = float(os.environ.get("FLOCK_EXEC_POLL_S", "0.5"))
    except ValueError:
        poll = 0.5

    try:
        os.makedirs(os.path.dirname(args.lockfile), exist_ok=True)
    except OSError:
        pass

    try:
        fd = os.open(args.lockfile, os.O_CREAT | os.O_RDWR)
        os.set_inheritable(fd, True)
    except OSError as e:
        print(f"flock_exec.py: could not open lock file, running unlocked: {e}", file=sys.stderr)
        os.execvp(cmd[0], cmd)
        return 2  # unreachable if exec succeeds

    deadline = time.monotonic() + ceiling
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                # L-1095: leave an alarm trace in the same format other hooks use
                # (best-effort, never raises; DELEGATION_ALARMS_LOG overrides for tests).
                try:
                    alog = os.environ.get("DELEGATION_ALARMS_LOG") or os.path.join(
                        os.path.expanduser("~"), ".claude", "hub", "delegation-alarms.log")
                    with open(alog, "a") as fh:
                        fh.write("%s L-1079 flock_exec gave up after %.0fs waiting on lock %s; command NOT run\n" % (
                            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ceiling, args.lockfile))
                except Exception:
                    pass
                for p in args.on_giveup_rm:
                    try:
                        if os.path.isdir(p) and not os.path.islink(p):
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            os.remove(p)
                    except OSError:
                        pass
                return 0  # fail open: give up, run nothing at all
            time.sleep(poll)

    os.environ["FLOCK_EXEC_FD"] = str(fd)
    os.execvp(cmd[0], cmd)
    return 0  # unreachable if exec succeeds


if __name__ == "__main__":
    sys.exit(main())
