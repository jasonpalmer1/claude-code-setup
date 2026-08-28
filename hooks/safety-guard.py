#!/usr/bin/env python3
"""PreToolUse guard: makes the CLAUDE.md.template security section deterministic
instead of advisory — a can't-happen layer for a small set of catastrophic or
credential-leaking actions, so a fast-moving session can't skip a rule it
never re-reads mid-task.

TIGHT patterns only, on purpose: a false positive here costs more trust than
it saves, so this only blocks things you can describe with very high
confidence, not "looks a bit risky."

Blocks two classes of thing:

1. **Catastrophic shell commands** — a destructive `rm -rf` aimed at a root
   path (`/`, `~`, `$HOME`), a force-push to `main`/`master`, or a world-writable
   `chmod 777 /`. If genuinely intended, run it by hand outside the harness.

2. **Credentials heading into a tracked/committed file** — two shapes:
   - A well-known secret FORMAT (a vendor API key prefix, a private-key PEM
     header) appearing anywhere in Write/Edit content. These are public,
     documented formats — matching on them reveals nothing about any
     particular secret's value.
   - A literal value sitting next to a credential-shaped identifier: the
     general mechanism is "a variable or comparison named password / passcode
     / secret / token / api_key, followed by a quoted or bare literal that
     isn't an obvious placeholder." This catches the hardcoded-constant shape
     (`const PASSWORD = "..."`) and the compare-against-a-literal shape
     (`if (input !== "...")`) without needing to know what any real secret
     looks like — the identifier next to it is the signal, not the value's
     shape. A password does not look like a password; what marks it is the
     name sitting next to it.

Exit 2 = block. Must never block on its OWN failure."""
import json, sys, re

CATASTROPHIC = [
    # rm -rf (any flag order) aimed at /, ~, or $HOME roots
    r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*[rf][a-zA-Z]*\s+(/|~/?|\$HOME/?)\s*$",
    r"rm\s+-[a-zA-Z]*[rf][a-zA-Z]*[rf][a-zA-Z]*\s+(/|~|\$HOME)\s",
    # force-push to main/master, both flag positions
    r"git\s+push[^\n]*\s--force(-with-lease)?\b[^\n]*\s(main|master)\b",
    r"git\s+push[^\n]*\s(main|master)\b[^\n]*\s--force(-with-lease)?\b",
    r"git\s+push\s+-f\s+[^\n]*(main|master)\b",
    # world-writable root
    r"chmod\s+(-R\s+)?777\s+/\s*$",
]
SECRET = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{30,}"
    r"|xox[bp]-[A-Za-z0-9-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)

# The IDENTIFIER next to a literal is the signal a credential is present, not
# the shape of the literal itself — see the docstring. Match on the name.
CRED_WORD = (
    r"(?:pass(?:code|word|phrase)?|pw|pwd|passwd|secret|token|api[_-]?key|"
    r"auth[_-]?key|admin[_-]?pass|access[_-]?key|private[_-]?key)"
)

# Shape 1 — a quoted literal after an assignment or a comparison. Covers both
# the hardcoded-constant form and the compare-against-a-literal form.
CREDENTIAL_LINE = re.compile(
    r"(?i)\b" + CRED_WORD + r"\b[^\n]{0,24}?(?:==|===|!=|!==|=|:)\s*"
    r"(?P<q>[\"'`])(?P<val>[^\"'`\n]{2,60})(?P=q)"
)

# Shape 2 — an unquoted, secret-looking token immediately after the word. This
# is how credentials leak into markdown, memory files, and shell echo lines,
# where nothing is quoted. Gated by _looks_secret so ordinary prose such as
# "the password is stored in your secret manager" never trips it.
# Bare "pass" is deliberately EXCLUDED from the loose rule below — in prose it
# is overwhelmingly a noun (a review pass, a QA pass), usually followed by a
# date or number that a naive entropy check would misread as a credential.
# The quoted rule above still covers the risky assignment form.
CRED_WORD_STRICT = (
    r"(?:passcode|password|passphrase|passwd|secret|token|api[_-]?key|"
    r"auth[_-]?key|admin[_-]?pass|access[_-]?key|private[_-]?key)"
)
CREDENTIAL_LOOSE = re.compile(
    r"(?i)\b" + CRED_WORD_STRICT + r"\b[\s:=(`\"']{1,4}(?P<val>[A-Za-z0-9][A-Za-z0-9._-]{7,60})"
)

# Dates and version strings carry digits and separators but are never secrets.
NOT_A_SECRET = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}|v?\d+\.\d+[\w.-]*|\d{1,4}[-/]\d{1,2}[-/]\d{1,4})$"
)

def _looks_secret(val: str) -> bool:
    """Entropy signature of a credential rather than a word: digits, or several
    separator groups. Conservative on purpose — shape 1 already covers quoted
    cases, so this only needs the obvious unquoted ones."""
    if PLACEHOLDER.match(val) or NOT_A_SECRET.match(val):
        return False
    has_digit = any(c.isdigit() for c in val)
    groups = val.count("-") + val.count("_")
    return (has_digit and len(val) >= 8) or groups >= 2

# Things that look like credentials but are not — placeholders, env lookups,
# and the sort of prose that shows up in docs. Kept generous: this guard runs
# on every write, and a false block costs more trust than it saves.
PLACEHOLDER = re.compile(
    r"(?i)^\s*("
    r"x{3,}|\.{3,}|-+|change[_-]?me|your[_-]?[\w-]+|[\w-]*example[\w-]*|[\w-]*sample[\w-]*|"
    r"dummy[\w-]*|placeholder[\w-]*|test[\w-]*|fake[\w-]*|redacted[\w-]*|none|null|true|false|"
    r"\$\{[^}]*\}|\$[A-Z_]+|process\.env\.[\w.]+|env\.[\w.]+|os\.environ.*|"
    r"<[^>]*>|\{\{[^}]*\}\}|[\w-]*here|"
    # HTML/form attribute VALUES. A login field declares a passcode name and a
    # password TYPE side by side; the regex would otherwise read that as a
    # credential word followed by a quoted value, but the value is the field
    # type, not a secret.
    r"password|passcode|current-password|new-password|one-time-code|"
    r"off|on|none|text|email|username|tel|search|hidden|submit"
    r")\s*$"
)

# Where a real secret is allowed to live. Adjust to your own convention.
SECRET_SAFE_PATH = (".env", ".dev.vars", ".envrc")

# Paths where a credential literal is especially dangerous: anything that gets
# committed, published, or read back into an AI context later.
def _high_risk(path: str) -> bool:
    p = path.lower()
    return (
        "/memory/" in p
        or "/hub/" in p
        or p.endswith((".md", ".mjs", ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".toml", ".json", ".yml", ".yaml"))
    )

try:
    data = json.load(sys.stdin)
    name = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    if name == "Bash":
        cmd = tin.get("command", "") or ""
        for pat in CATASTROPHIC:
            if re.search(pat, cmd):
                print(
                    "Blocked (safety guard): command matches a catastrophic pattern "
                    "(destructive rm at a root path / force-push to main / chmod 777 /). "
                    "If genuinely intended, run it by hand outside the harness.",
                    file=sys.stderr,
                )
                sys.exit(2)
    elif name in ("Write", "Edit"):
        content = (tin.get("content") or "") + (tin.get("new_string") or "")
        path = tin.get("file_path", "") or ""
        exempt = (
            "/scratchpad" in path
            or path.endswith((".dev.vars", ".env"))
            # This guard's own test fixtures necessarily contain secret-SHAPED
            # strings by design — that's the whole point of testing it — so
            # without an exemption the guard would make itself untestable.
            # Point this at your own test-fixture path, spelled out exactly,
            # rather than a marker comment any file could claim.
            or path.endswith("/.claude/hooks/test-safety-guard.sh")
            # A single designated credentials vault file — gitignored, and
            # documented as the ONE place real secrets are allowed to be
            # written down in plain text. Writing a real credential here is
            # the file doing its job, not a leak. Point this at your own
            # vault path, spelled out exactly, same reasoning as above.
            or path.endswith("/.claude/hub/credentials-vault.md")
        )
        if not exempt and SECRET.search(content):
            print(
                "Blocked (safety guard): secret-shaped string headed into a file. "
                "Secrets live only in untracked .env/.dev.vars or your platform's secret "
                "store — never in tracked files, logs, or memory.",
                file=sys.stderr,
            )
            sys.exit(2)

        # Credential literal next to a credential-shaped identifier.
        if not path.endswith(SECRET_SAFE_PATH) and not exempt and _high_risk(path):
            hits = [m.group("val") for m in CREDENTIAL_LINE.finditer(content)
                    if not PLACEHOLDER.match(m.group("val"))]
            hits += [m.group("val") for m in CREDENTIAL_LOOSE.finditer(content)
                     if _looks_secret(m.group("val"))]
            for val in hits:
                print(
                    "Blocked (safety guard): a literal value is being written next to a "
                    "credential-shaped name (password/passcode/secret/token/api_key).\n"
                    "Read it from your platform's secret store or an untracked .env instead. "
                    "If this really is a placeholder, make it obviously fake "
                    "(CHANGEME, your-key-here, ${VAR}).",
                    file=sys.stderr,
                )
                sys.exit(2)
except SystemExit:
    raise
except Exception:
    pass
sys.exit(0)
