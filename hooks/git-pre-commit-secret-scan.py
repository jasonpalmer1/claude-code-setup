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

# Any .env / .env.<anything> (.env.production.local, .env.local.bak-1790026563), .envrc,
# .dev.vars / .dev.vars.<anything>, *.env (secrets.env), and the credentials file (L-1615 fix2).
SECRET_PATH = re.compile(
    r"(^|/)(\.env(\.[^/]+)?|\.envrc|\.dev\.vars(\.[^/]+)?|[^/]*\.env|secrets?\.env\.[^/]+|00-credentials\.md)$")
SECRET_PATH_OK = re.compile(r"\.(example|sample|template)$")

FORMATS = [
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"(?<![A-Za-z0-9])sk-(proj-)?[A-Za-z0-9]{32,}")),
    ("github-token", re.compile(r"(?<![A-Za-z0-9])(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("aws-access-key", re.compile(r"(?<![A-Za-z0-9])(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("stripe-key", re.compile(r"(?<![A-Za-z0-9])[sr]k_live_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("google-api-key", re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{35}\b")),
    ("resend-key", re.compile(r"(?<![A-Za-z0-9])re_[A-Za-z0-9]{8,}_[A-Za-z0-9]{16,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("npm-token", re.compile(r"(?<![A-Za-z0-9])npm_[A-Za-z0-9]{30,}")),
    ("sendgrid-key", re.compile(r"(?<![A-Za-z0-9])SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("aws-secret-key", re.compile(r"(?i)aws_?secret_?access_?key[\"']?\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}")),
    ("stripe-other", re.compile(r"(?<![A-Za-z0-9])(?:pk_live|whsec)_[A-Za-z0-9]{16,}")),
    ("gitlab-token", re.compile(r"(?<![A-Za-z0-9])glpat-[A-Za-z0-9_-]{20,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{20,}")),
    # L-1615 fix2: connection-string passwords, Basic auth, chat webhooks, Telegram bot tokens.
    ("url-password", re.compile(
        r"(?i)\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@\"'<>{}$]{1,64}:(?!(?:pass(?:word)?|pwd|xxx+|\*+|changeme|secret|redacted|your|example)[@:])"
        r"[^\s:/@\"'<>{}$*]{3,64}@")),
    ("basic-auth", re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?basic\s+[A-Za-z0-9+/]{12,}={0,2}")),
    ("webhook-url", re.compile(
        r"https://hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9/_-]{20,}"
        r"|https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d{15,}/[A-Za-z0-9_-]{20,}")),
    ("telegram-bot-token", re.compile(r"(?<![0-9])\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])")),
    ("jwt", re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
# Mirrors security-tripwire.py's pushed-file rule so the two agree on what "credential-shaped" means.
# The keyword may sit inside a longer name (DB_PASSWORD, MY_SECRET, OPENAI_API_KEY, clientSecret)
# and the name may be quoted ("client_secret": "..."), L-1615. Prefix is lazy and anchored to a
# non-identifier boundary, so a long identifier costs linear time. Suffix is a short allow-list so
# prose such as "tokenization" or "secretary" does not match.
CRED_ASSIGN = re.compile(
    r"(?i)(?<![A-Za-z0-9_-])[A-Za-z0-9_-]*?"
    r"(?:passcode|password|passphrase|passwd|secret|token|api[_-]?key|apikey|private[_-]?key)"
    r"(?:s|[_-]?(?:key|value|val|str|string))?[\"']?"
    r"[\s:=(`\"']{1,4}([A-Za-z0-9][A-Za-z0-9._-]{9,60})")
PLACEHOLDER = re.compile(
    r"(?i)^(x{3,}|change|your|example|sample|test|fake|redacted|none|<|"
    r"process\.env\.|env\.|os\.environ|\$\{|\$[A-Z_])")
NOT_A_SECRET = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|v?\d+\.\d+[\w.-]*)$")
# Env-var NAMES after "token"/"secret" (<product-b>_D1_TOKEN), not values. Same rule as the tripwire.
# ALL-CAPS+digits with no underscore or dash (ABCD1234EFGH5678) is a key, not a name (L-1615 fix2).
IDENTIFIER = re.compile(r"^(?:[A-Z][A-Z0-9]*_[A-Z0-9_]*|[A-Z][A-Z-]+)$")

# Files that hold fabricated secret-SHAPED fixtures by design (same exemption the tripwire uses).
FIXTURE_FILES = {"hooks/test-safety-guard.sh"}
# Test, eval, report and QA-script paths quote fake Bearer values and fake KEY=value lines. In
# these paths ONLY the two heuristic rules are skipped; strict formats (ghp_, AKIA, sk-ant, ...)
# and exact values from the secret store still block. (L-1615 fix2, 6 of the last 400 commits.)
FIXTURE_PATH = re.compile(
    r"^(?:hub/reports/|hub/bug-gate/|hub/evals/|hub/playtests/|tools/shotter/)"
    r"|(?:^|/)(?:tests?|__tests__|fixtures?)/"
    r"|(?:^|/)(?:test[_-][^/]*|[^/]*[_.-]tests?\.[a-z]+)$")
HEURISTIC_RULES = {"bearer-token", "credential-assignment"}
# Per-line exemption for a fixture that must stay in a normal file: `secret-scan: allow <reason>`.
# L-1617: it exempts ONLY the heuristic assignment rules (ALLOW_RULES). Strict credential formats,
# private-key blocks, exact secret-store values and secret-file paths can never be exempted.
# The reason must be real: at least 3 words, not just filler (allow-all, todo, ignore, ...).
ALLOW_RULES = {"credential-assignment", "literal-password"}
ALLOW_MARKER = re.compile(r"secret-scan:\s*allow(?![\w-])(?P<reason>[^\n]*)")
REASON_FILLER = {
    "all", "any", "anything", "everything", "ignore", "ignored", "skip", "skipped", "todo", "tbd",
    "fixme", "none", "na", "ok", "okay", "fine", "yes", "no", "test", "tests", "testing",
    "reason", "because", "placeholder", "stuff", "this", "that", "it", "the", "here", "xxx", "foo",
    "bar", "baz", "temp", "tmp", "bypass", "whatever", "just", "please", "allow", "allowed"}


def reason_ok(reason):
    """True when the allow-marker reason is at least 3 distinct real words (letters, 3+ chars,
    not filler)."""
    words = {w for w in re.findall(r"[a-z]{3,}", reason.lower()) if w not in REASON_FILLER}
    return len(words) >= 3


# L-1617 (B2 iii): a literal password assigned to a password-ish name, however short, symbol-heavy
# or widely spaced: password=hunter2, password=Pa$$w0rd!x, password:     Qw9xLm3Zp8Rt. Linear: the
# keyword is a plain literal and whitespace runs are bounded. Env lookups, placeholders, calls,
# attribute chains, type annotations (no digit/symbol) and dates/versions are not flagged.
LITERAL_PW = re.compile(
    r"(?i)(?:passcode|password|passphrase|passwd)s?[\"']?[ \t]{0,300}(?<![=!<>])[:=](?![=>~])[ \t]{0,300}"
    r"[\"'`]?(?P<v>[^\s\"'`,;)}\]]{6,128})")
PW_SKIP = re.compile(
    r"(?i)^(?:[<{$%*.\[@/~#-]|x{3,}|change|your|example|sample|test|fake|redacted|none|null|"
    r"true|false|undefined|process\.env|env[.\[]|os\.|getenv|secrets?[.\[]|config[.\[]|vault|"
    r"[a-z_]\w*(?:\.[a-z_]\w*)+$)")
PW_SYMBOL = re.compile(r"[0-9!@#$%^&*+=?~|\\]")


def literal_password(text):
    for m in LITERAL_PW.finditer(text):
        v = m.group("v")
        if "(" in v or "[" in v or "{" in v or "<" in v:
            continue
        if PW_SKIP.match(v) or NOT_A_SECRET.match(v) or IDENTIFIER.match(v) or not PW_SYMBOL.search(v):
            continue
        return True
    return False


class ScanError(Exception):
    """Anything that stops the scanner from seeing the staged change. Always blocks the commit."""


def git_bytes(*args):
    """Run git, return stdout bytes. Any failure raises ScanError (fail closed, L-1615)."""
    r = subprocess.run(["git", *args], capture_output=True)
    if r.returncode != 0:
        raise ScanError(f"git {args[0]} exited {r.returncode}")
    return r.stdout


def git(*args):
    return git_bytes(*args).decode("utf-8", "replace")


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
        # L-1619: the token regex includes ".", so a value that ends a sentence ("...6u.") was
        # recorded with the period and never matched bare. Record BOTH forms: the raw value and the
        # value without sentence punctuation (a substring of the dotted form, so both directions match).
        # L-1619 fix1 (gate B1): the length floor applies to the RAW value, so a 16-char value ending
        # in "!" is still recorded. The stripped form is recorded only if it clears the same floor
        # (16 chars + letter + digit): any shorter would match ordinary prose and block clean commits.
        raw = v.strip().strip("\"'`")
        stripped = raw.strip(".,;:!?").strip("\"'`")
        def floor(x):
            return len(x) >= 16 and re.search(r"[A-Za-z]", x) and re.search(r"\d", x)
        # Must look like a key, not a word, path, URL, date or hostname: letters AND digits.
        if floor(raw) and not raw.startswith(("http", "/", "~")) and not NOT_A_SECRET.match(raw) \
                and not (len(stripped) <= 253 and hostlike.match(stripped)) and not filelike.search(stripped):
            vals.add(raw)
            if stripped != raw and floor(stripped):
                vals.add(stripped)

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


MAX_BLOB = 20 * 1024 * 1024
ZERO_SHA = "0" * 40
EXTRA_GIT = ["--no-ext-diff", "--no-textconv", "--no-color", "--no-renames", "--src-prefix=a/", "--dst-prefix=b/"]


def staged_changes():
    """Yield (path, old_sha, new_sha) for each added/copied/modified/renamed/type-changed staged file.
    Raw output with -z: no path quoting, no prefix, no textconv, no external diff, so no user
    diff config (diff.noprefix, mnemonicPrefix, textconv, -diff attributes) can change it."""
    out = git_bytes("diff", "--cached", "--raw", "-z", "--diff-filter=ACMRT", *EXTRA_GIT).split(b"\0")
    i = 0
    while i + 1 < len(out) and out[i]:
        meta = out[i].decode("ascii", "replace").lstrip(":").split()
        path = out[i + 1].decode("utf-8", "replace")
        i += 2
        if len(meta) < 5:
            raise ScanError("unparseable git raw output")
        old_mode, new_mode, old_sha, new_sha = meta[0], meta[1], meta[2], meta[3]
        if new_mode == "160000":  # submodule pointer, no content
            continue
        yield path, old_sha, new_sha


class BlobReader:
    """One long-lived `git cat-file --batch` for every blob (was two forks per file)."""
    def __init__(self):
        self.p = subprocess.Popen(["git", "cat-file", "--batch"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, sha):
        self.p.stdin.write(sha.encode("ascii") + b"\n")
        self.p.stdin.flush()
        head = self.p.stdout.readline().split()
        if len(head) != 3 or head[1] != b"blob":
            raise ScanError("git cat-file could not read a staged blob")
        size = int(head[2])
        if size > MAX_BLOB:
            # Refuse (fail closed). Kill the reader now: it may be stuck writing the body into a
            # full pipe, and close() would otherwise wait 10 s for it.
            self.p.kill()
            raise ScanError("staged blob over size cap, cannot scan")
        data = self.p.stdout.read(size)
        self.p.stdout.read(1)  # trailing newline
        if len(data) != size:
            raise ScanError("short read from git cat-file")
        return data

    def close(self):
        try:
            self.p.stdin.close()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


_READER = None


def blob_text_lines(sha):
    """Staged/old blob as text lines. Handles UTF-16 (BOM or NUL-interleaved), other binary
    (printable runs), and refuses (ScanError) a blob too large to scan."""
    global _READER
    if set(sha) == {"0"}:
        return []
    if _READER is None:
        _READER = BlobReader()
    data = _READER.read(sha)
    return decode_blob(data)


def decode_blob(data):
    if b"\0" not in data:
        return data.decode("utf-8", "replace").splitlines()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace").splitlines()
    nuls_even = data[1::2].count(0)
    nuls_odd = data[0::2].count(0)
    if max(nuls_even, nuls_odd) * 3 >= len(data):  # BOM-less UTF-16
        enc = "utf-16-le" if nuls_even >= nuls_odd else "utf-16-be"
        lines = data.decode(enc, "replace").splitlines()
    else:
        lines = []
    # Plain binary (and a fallback for odd UTF-16): every printable run is a "line".
    lines += [m.decode("latin-1") for m in re.findall(rb"[\x20-\x7e\t]{6,}", data)]
    return lines


def added_lines():
    """Yield (path, line_no, text) for every line the staged blobs add versus the old blobs.
    Reads blobs directly (git cat-file) instead of parsing diff output, so no diff setting can
    blind it. A line counts as added when it appears more often in the new blob than the old."""
    from collections import Counter
    for path, old_sha, new_sha in staged_changes():
        new = blob_text_lines(new_sha)
        old = Counter(blob_text_lines(old_sha) if old_sha != ZERO_SHA else [])
        for n, text in enumerate(new, 1):
            if old[text] > 0:
                old[text] -= 1
                continue
            yield path, n, text


def main():
    try:
        return _main()
    finally:
        if _READER is not None:
            _READER.close()


def _main():
    hits = []
    for p, _o, _n in staged_changes():
        if SECRET_PATH.search(p) and not SECRET_PATH_OK.search(p):
            hits.append(f"{p}: secrets file staged (rule: secret-file-path)")

    values = None
    for path, n, text in added_lines():
        # L-1619: a FIXTURE_FILES file skips every pattern rule (it holds fabricated secret-shaped
        # fixtures by design) but still gets the exact secret-store value check below.
        whole_file_fixture = path in FIXTURE_FILES
        in_fixture = bool(FIXTURE_PATH.search(path))
        rule = None if whole_file_fixture else next((name for name, rx in FORMATS if rx.search(text)), None)
        if rule in HEURISTIC_RULES and in_fixture:
            rule = None
        if rule is None and not in_fixture and not whole_file_fixture:
            for m in CRED_ASSIGN.finditer(text):
                v = m.group(1)
                if PLACEHOLDER.match(v) or NOT_A_SECRET.match(v) or IDENTIFIER.match(v):
                    continue
                if text[m.end(1):m.end(1) + 1] == "(":  # `tokens = some_func(x)`: a call
                    continue
                if any(c.isdigit() for c in v) or (v.count("-") + v.count("_")) >= 2:
                    rule = "credential-assignment"
                    break
            if rule is None and literal_password(text):
                rule = "literal-password"
        # L-1617 r1 B1: the exact secret-store value check runs on EVERY added line, whatever other
        # rule matched, and the allow marker can never exempt it.
        if values is None:
            values = known_values()
        if any(v in text for v in values):
            hits.append(f"{path}:{n}: (rule: value-from-secret-store)")
            continue
        if rule:
            am = ALLOW_MARKER.search(text)
            if am and rule in ALLOW_RULES:
                if reason_ok(am.group("reason")):
                    continue
                hits.append(f"{path}:{n}: (rule: {rule}; allow marker needs a real reason, 3+ words)")
                continue
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


def safe_main():
    try:
        return main()
    except Exception as e:  # fail closed: a scanner that cannot see the change blocks the commit
        print(f"BLOCKED by secret-scan pre-commit hook (L-1615): could not scan the staged "
              f"change ({type(e).__name__}: {e}). Fix the cause; do not bypass.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(safe_main())
