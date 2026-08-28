#!/usr/bin/env python3
"""SessionStart tripwire — a standing re-check that your OTHER safety controls
are actually still working, not just present.

The failure mode this exists for isn't "a control is missing" — it's that a
control can quietly stop working (crash on every call, get bypassed by a
refactor, silently start logging failures nobody reads) while everything
still *looks* fine, because nothing else notices a control that has stopped
firing. This re-verifies a small set of controls on every session start so a
broken one surfaces immediately instead of after days of false confidence.

Design rules:
  * NEVER block and never fail a session. Prints a short banner or nothing.
  * Local and cheap only (filesystem + a subprocess or two). No secret values
    are ever read, printed, or logged — only their absence or shape.
  * Silence means checked-and-clean, not skipped: it prints an all-clear line
    so a crashed tripwire is distinguishable from a passing one. A tripwire
    that goes silent when it breaks is worse than no tripwire at all.

Adjust the probe list at the bottom to whatever controls and paths matter in
your own setup — the three below are generic starting points.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
findings = []
checked = 0


def check(fn):
    """Run one probe; a broken probe must never take the session down, but it
    must also never be mistaken for a passing one."""
    global checked
    try:
        fn()
        checked += 1
    except Exception as e:
        findings.append(f"tripwire probe {fn.__name__} errored ({type(e).__name__}) — not verified")


def guard_is_alive():
    """If you run safety-guard.py (see hooks/safety-guard.py in this repo),
    prove it still loads AND still blocks, rather than trusting that the file
    is present and looks right."""
    g = HOME / ".claude/hooks/safety-guard.py"
    if not g.exists():
        return  # not using that guard — nothing to check
    probe = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
    r = subprocess.run([sys.executable, str(g)], input=probe, capture_output=True,
                       text=True, timeout=10)
    if r.returncode != 2:
        findings.append(
            f"safety-guard.py did NOT block a catastrophic command (rc={r.returncode}) — "
            "it is protecting nothing right now.")


def env_permissions():
    """Secrets files should not be world-readable. Adjust the glob to match
    where your own projects keep local env files."""
    loose = []
    for p in HOME.glob("projects/*/.env*"):
        if p.is_file() and p.suffix != ".example" and not p.name.endswith(".example"):
            if oct(p.stat().st_mode)[-2:] != "00":
                loose.append(str(p).replace(str(HOME), "~"))
    if loose:
        findings.append(f"{len(loose)} secret file(s) readable beyond you: {', '.join(loose[:3])}"
                        + (" …" if len(loose) > 3 else "") + "  → chmod 600")


def tracked_secrets():
    """A credential-shaped literal inside a file that's tracked in your config
    repo — i.e. would get pushed to a remote. Scans git-tracked files only, so
    it's fast and never touches gitignored/untracked content."""
    repo = HOME / ".claude"
    if not (repo / ".git").exists():
        return
    out = subprocess.run(["git", "-C", str(repo), "ls-files"],
                         capture_output=True, text=True, timeout=30).stdout.split("\n")
    # Same word list as safety-guard.py's strict rule, and for the same reason:
    # bare "pass" is a noun in prose (a review pass, a QA pass) and is usually
    # followed by a date, which the entropy test below would misread as a secret.
    cred = re.compile(
        r"(?i)\b(passcode|password|passphrase|passwd|secret|token|api[_-]?key)\b"
        r"[\s:=(`\"']{1,4}([A-Za-z0-9][A-Za-z0-9._-]{9,60})")
    # Env lookups and template vars are the CORRECT pattern — they are what a
    # remediated file looks like, so flagging them would train the reader to
    # ignore this tripwire.
    placeholder = re.compile(
        r"(?i)^(x{3,}|change|your|example|sample|test|fake|redacted|none|<|"
        r"process\.env\.|env\.|os\.environ|\$\{|\$[A-Z_])")
    not_a_secret = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|v?\d+\.\d+[\w.-]*)$")
    hits = []
    for rel in out:
        if not rel or not rel.endswith((".md", ".json", ".sh", ".py", ".mjs")):
            continue
        # This guard's own test fixtures contain fabricated secret-SHAPED
        # strings by design — same single-path exemption safety-guard.py
        # carries. Point this at your own test-fixture path if you have one.
        if rel.endswith("hooks/test-safety-guard.sh"):
            continue
        f = repo / rel
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for m in cred.finditer(text):
            val = m.group(2)
            if placeholder.match(val) or not_a_secret.match(val):
                continue
            if any(c.isdigit() for c in val) or (val.count("-") + val.count("_")) >= 2:
                hits.append(rel)
                break
    if hits:
        findings.append(f"credential-shaped literal in {len(hits)} tracked file(s) "
                        f"(would be pushed): {', '.join(hits[:2])}  → rotate, then purge")


PROBES = (guard_is_alive, env_permissions, tracked_secrets)
for probe in PROBES:
    check(probe)

if findings:
    print("🔒 SECURITY TRIPWIRE — " + str(len(findings)) + " issue(s):")
    for f in findings:
        print("   ⚠ " + f)
else:
    print(f"🔒 security tripwire: {checked}/{len(PROBES)} controls verified working")
sys.exit(0)
