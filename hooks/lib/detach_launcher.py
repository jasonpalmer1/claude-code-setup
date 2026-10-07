#!/usr/bin/env python3
"""L-0953 round 2: fully detach a worker command from this launcher's own
process group so it survives the parent (and the parent's whole process
group) being SIGKILLed.

Usage:
    python3 detach_launcher.py [--log LOGFILE] -- <command> [args...]

Why this exists: bug-gate reproduction against the real installed Claude
Code binary (hub/reports/L-0953-buggate.md) showed the harness kills an
async PreCompact hook's own process (and everything sharing its process
group) at ~16-28s, regardless of settings.json's declared "timeout" or any
watchdog constant inside the hook script itself -- the process is simply
dead before any internal timer beyond ~16s can matter. No amount of tuning
a watchdog INSIDE that doomed process fixes this; the process has to not
be a member of the group that gets killed.

Mechanism: subprocess.Popen(..., start_new_session=True) calls os.setsid()
in the freshly forked child, BEFORE it execs the target command. setsid()
moves that child into a brand-new session and a brand-new process group
(pgid == the child's own pid) -- this only succeeds because a just-forked
child is never already a process-group leader. Once moved, the child is no
longer a member of the launcher's (or the launcher's caller's) process
group, so a `kill -9 -$OLD_PGID` aimed at that old group never reaches it.

This launcher does NOT wait() for the child it starts -- Popen() returns as
soon as the fork+exec completes (near-instant), which is the point: the
caller (the hook dispatcher, pre-compact-real-log.sh) can exit immediately
right after, well inside whatever window the harness uses before it kills
an async hook's process/group.

Exit code: 0 if the child was launched successfully (says nothing about
whether the child's OWN work later succeeds -- that is entirely the
worker's job, including its own fail-open alarm path); non-zero only if
the fork/exec itself could not be started (caller should alarm on this).
"""
import argparse
import os
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch a command fully detached (new session/pgid) and return immediately."
    )
    parser.add_argument(
        "--log",
        default=None,
        help="file to append the detached child's stdout/stderr to (default: discard)",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to launch; pass a literal -- before it",
    )
    args = parser.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("detach_launcher.py: no command given (pass -- <command> [args...])", file=sys.stderr)
        return 2

    devnull_in = None
    out = None
    try:
        devnull_in = open(os.devnull, "rb")
        out = open(args.log, "ab") if args.log else open(os.devnull, "wb")
    except OSError as e:
        print(f"detach_launcher.py: could not open stdio files: {e}", file=sys.stderr)
        return 2

    try:
        subprocess.Popen(
            cmd,
            stdin=devnull_in,
            stdout=out,
            stderr=out,
            start_new_session=True,  # setsid() before exec -- new session + new pgid
            close_fds=True,
        )
    except OSError as e:
        print(f"detach_launcher.py: failed to launch {cmd!r}: {e}", file=sys.stderr)
        return 2
    finally:
        if devnull_in is not None:
            devnull_in.close()
        if out is not None:
            out.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
