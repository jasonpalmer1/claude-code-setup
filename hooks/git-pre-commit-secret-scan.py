#!/usr/bin/env python3
"""Git pre-commit hook for the ~/.claude repo: refuse a commit that would add a secret (L-0848).

Why: `Bash(git add:*)` and `Bash(git commit:*)` are allowed in settings, so auto mode's own
secret-in-commit check never runs on this repo, and a live passcode has reached GitHub before.
This hook is the enforcement that does not depend on the classifier.

Scans only lines ADDED by the staged diff (so an old, already-pushed hit never blocks unrelated
work; the security tripwire reports those). Three checks:
  1. a staged path that is itself a secrets file (.env*, .dev.vars, 00-credentials.md)
  2. a well-known credential format (Anthropic/OpenAI/GitHub/AWS/Stripe/Slack/Google keys,
     private-key blocks, JWTs) or a `password = <literal>` shape (same rule as security-tripwire.py)
  3. any exact value that lives in hub/manual/00-credentials.md or a local .env / .dev.vars file

It NEVER prints a matched value: only file, line number and which rule fired.
Install: .git/hooks/pre-commit runs this file. Bypass (`--no-verify`) is for the operator only.
Self-test: hooks/test-git-pre-commit-secret-scan.sh
"""
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
# Overridable so the test can point at fixtures instead of the real secret stores.
CRED_FILE = Path(os.environ.get("SECRET_SCAN_CRED_FILE", HOME / ".claude/hub/manual/00-credentials.md"))
ENV_ROOTS = [Path(p) for p in os.environ.get(
    "SECRET_SCAN_ENV_ROOTS", f"{HOME}/projects:{HOME}/.claude").split(":") if p]

SECRET_PATH = re.compile(r"(^|/)(\.env(\.[\w-]+)?|\.dev\.vars|00-credentials\.md)$")
SECRET_PATH_OK = re.compile(r"\.env\.(example|sample|template)$")

FORMATS = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("resend-key", re.compile(r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
# Mirrors security-tripwire.py's pushed-file rule so the two agree on what "credential-shaped" means.
CRED_ASSIGN = re.compile(
    r"(?i)\b(passcode|password|passphrase|passwd|secret|token|api[_-]?key)\b"
    r"[\s:=(`\"']{1,4}([A-Za-z0-9][A-Za-z0-9._-]{9,60})")
PLACEHOLDER = re.compile(
    r"(?i)^(x{3,}|change|your|example|sample|test|fake|redacted|none|<|"
    r"process\.env\.|env\.|os\.environ|\$\{|\$[A-Z_])")
NOT_A_SECRET = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|v?\d+\.\d+[\w.-]*)$")
# Env-var NAMES after "token"/"secret" (<product-b>_D1_TOKEN), not values. Same rule as the tripwire.
IDENTIFIER = re.compile(r"^(?:[A-Z][A-Z0-9_]+|[A-Z][A-Z-]+)$")

# Files that hold fabricated secret-SHAPED fixtures by design (same exemption the tripwire uses).
FIXTURE_FILES = {"hooks/test-safety-guard.sh"}


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, errors="replace").stdout


def known_values():
    """Exact secret values from the real stores. Returned as a set; never printed."""
    vals = set()
    token = re.compile(r"[A-Za-z0-9_\-+/=.]{16,}")
    # Hostnames and emails sit in the credentials file next to the secrets they log into, but
    # they are public identifiers and appear all over the repo (the handle in them has digits).
    # Unambiguous on purpose: dot-separated labels, optional "user@". The first version let
    # `[\w.+-]*` and `(\.[\w-]+)*` overlap and took 5+ s on a 3 KB .dev.vars value (bug-gate).
    hostlike = re.compile(r"(?i)^(?:[\w.+-]+@)?[\w-]+(?:\.[\w-]+)*\.(?:com|dev|net|org|io|ai|app|co|sh|us)$")
    # Same for file names the credentials file cites (e.g. "Full report: 2026-07-28-...md").
    filelike = re.compile(r"(?i)\.(md|json|jsonl|sh|py|mjs|js|ts|html|txt|log|csv|png|jpg|pdf|toml|ya?ml)$")
    # Only .env entries whose NAME says secret; *_URL, *_HOST, account ids, IndexNow keys and
    # other public values are configuration, not credentials.
    secret_name = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASS|PRIVATE|AUTH|CRED|SALT|HMAC|SIGNING)")
    public_name = re.compile(r"(?i)(PUBLIC|INDEXNOW|SITE_?KEY|_URL$|_HOST$|DOMAIN|_ID$)")

    def keep(v):
        v = v.strip().strip("\"'`")
        # Must look like a key, not a word, path, URL, date or hostname: letters AND digits.
        if len(v) >= 16 and re.search(r"[A-Za-z]", v) and re.search(r"\d", v) \
                and not v.startswith(("http", "/", "~")) and not NOT_A_SECRET.match(v) \
                and not (len(v) <= 253 and hostlike.match(v)) and not filelike.search(v):
            vals.add(v)

    try:
        for t in token.findall(CRED_FILE.read_text(errors="ignore")):
            keep(t)
    except OSError:
        pass
    for root in ENV_ROOTS:
        if not root.is_dir():
            continue
        # Shallow on purpose: <root>/<project>/.env*, not a full-disk walk on every commit.
        for pattern in (".env", ".env.*", ".dev.vars", "*/.env", "*/.env.*", "*/.dev.vars"):
            for f in root.glob(pattern):
                if SECRET_PATH_OK.search(f.name) or not f.is_file():
                    continue
                try:
                    for line in f.read_text(errors="ignore").splitlines():
                        if "=" in line and not line.lstrip().startswith("#"):
                            name, val = line.split("=", 1)
                            name = name.replace("export ", "").strip()
                            if secret_name.search(name) and not public_name.search(name):
                                keep(val)
                except OSError:
                    pass
    return vals


def added_lines():
    """Yield (path, line_no, text) for every line the staged diff adds."""
    diff = git("diff", "--cached", "--no-color", "--unified=0", "--no-ext-diff", "--diff-filter=ACMR")
    path, n, in_header = None, 0, False
    for line in diff.splitlines():
        # Header lines only between "diff --git" and the first "@@", so an added content line
        # that itself starts with "++ " is never mistaken for a file header.
        if line.startswith("diff --git "):
            in_header, path = True, None
        elif in_header and line.startswith("+++ "):
            p = line[4:]
            path = p[2:] if p.startswith("b/") else None
        elif line.startswith("@@"):
            in_header = False
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif not in_header and line.startswith("+") and path is not None:
            yield path, n, line[1:]
            n += 1


def main():
    hits = []
    for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines():
        if SECRET_PATH.search(p) and not SECRET_PATH_OK.search(p):
            hits.append(f"{p}: secrets file staged (rule: secret-file-path)")

    values = None
    for path, n, text in added_lines():
        if path in FIXTURE_FILES:
            continue
        rule = next((name for name, rx in FORMATS if rx.search(text)), None)
        if rule is None:
            for m in CRED_ASSIGN.finditer(text):
                v = m.group(2)
                if PLACEHOLDER.match(v) or NOT_A_SECRET.match(v) or IDENTIFIER.match(v):
                    continue
                if any(c.isdigit() for c in v) or (v.count("-") + v.count("_")) >= 2:
                    rule = "credential-assignment"
                    break
        if rule is None:
            if values is None:
                values = known_values()
            if any(v in text for v in values):
                rule = "value-from-secret-store"
        if rule:
            hits.append(f"{path}:{n}: (rule: {rule})")

    if not hits:
        return 0
    print("BLOCKED by secret-scan pre-commit hook (L-0848): the staged change adds what looks "
          "like a secret. Values are not shown.", file=sys.stderr)
    for h in hits[:20]:
        print("  " + h, file=sys.stderr)
    if len(hits) > 20:
        print(f"  ... and {len(hits) - 20} more", file=sys.stderr)
    print("Fix: remove the value (use an env lookup or a pointer to the secret store), "
          "`git add` again, re-commit. Never bypass with --no-verify; if it is a false positive, "
          "tell the hub.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
