"""ask_redact -- one shared secret redactor for HQ Decisions answers (L-1731).
Applied at the earliest arrival point (ask-poller, before ledger / operator-answers.jsonl / hub wake) and on any
display path (ask-operator list). A detected secret value goes to macOS Keychain (service hq-answer-secret-<qid>,
account hub) through `security -i` reading commands from STDIN (never argv), and only the placeholder
"[secret stored in Keychain: hq-answer-secret-<qid>]" is returned. Fails CLOSED: if the Keychain write fails,
store_fn raises and the caller must not write the text anywhere."""
import math, re, subprocess

ACCOUNT = "hub"
PEM_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*KEY[A-Z ]*-----.*?(?:-----END [A-Z0-9 ]*KEY[A-Z ]*-----|\Z)", re.S)
JWT_RE = re.compile(r"(?<![A-Za-z0-9_\-])eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PREFIX_RE = re.compile(
    r"(?<![A-Za-z0-9_\-])(?:"
    r"cf(?:ut|at)_[A-Za-z0-9]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-ant-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{20,}"
    r"|(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}"
    r"|AIza[0-9A-Za-z_\-]{30,}"
    r"|xox[abprs]-[A-Za-z0-9\-]{10,}"
    r")(?![A-Za-z0-9_\-])")
LONG_RE = re.compile(r"(?<![A-Za-z0-9_\-+/=])[A-Za-z0-9_\-+/=]{32,}(?![A-Za-z0-9_\-+/=])")


def _entropy(s):
    return -sum((s.count(c) / len(s)) * math.log2(s.count(c) / len(s)) for c in set(s))


def _high_entropy(tok):
    if tok.count("/") >= 2 or tok.startswith("/"):
        return False  # path-like
    if re.fullmatch(r"[0-9a-fA-F]+", tok) and len(tok) == 40:
        return False  # git sha (64-hex stays redacted: hub bearer tokens are 64-hex)
    if UUID_RE.fullmatch(tok) or ("-" in tok and tok == tok.lower()):
        return False  # uuid / lowercase slug
    if not (re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok)):
        return False
    return _entropy(tok) >= 3.5


def find_secrets(text):
    """Return list of (start, end) spans, non-overlapping, sorted."""
    spans = []
    for rx in (PEM_RE, PREFIX_RE, JWT_RE):
        spans += [m.span() for m in rx.finditer(text)]
    for m in LONG_RE.finditer(text):
        if _high_entropy(m.group(0)):
            spans.append(m.span())
    spans.sort()
    out = []
    for s, e in spans:
        if out and s < out[-1][1]:
            out[-1] = (out[-1][0], max(e, out[-1][1]))
        else:
            out.append((s, e))
    return out


def keychain_store(service, value):
    """Store via `security -i` stdin so the value never appears in argv. Raises on failure.
    The interactive parser mangles newlines, quotes and backslashes, so such values are stored as
    "b64:<base64>" (decode with: security find-generic-password -s <svc> -a hub -w | sed s/^b64:// | base64 -d).
    Plain single-line values are stored raw. Verified by read-back; mismatch raises."""
    import base64
    stored = value
    if re.search(r'[\n\r"\\\s]', value):
        stored = "b64:" + base64.b64encode(value.encode()).decode()
    cmd = 'add-generic-password -U -s %s -a %s -w "%s"\n' % (service, ACCOUNT, stored)
    r = subprocess.run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=20)
    if r.returncode != 0 or "error" in (r.stderr or "").lower():
        raise RuntimeError("keychain write failed for %s (rc=%s)" % (service, r.returncode))
    g = subprocess.run(["security", "find-generic-password", "-s", service, "-a", ACCOUNT, "-w"], capture_output=True, text=True, timeout=20)
    if g.returncode != 0 or g.stdout.rstrip("\n") != stored:
        raise RuntimeError("keychain read-back mismatch for %s" % service)


def redact(text, qid, store_fn=keychain_store):
    """-> (clean_text, [service names]). store_fn(service, value) must raise on failure."""
    if not text:
        return text, []
    spans = find_secrets(text)
    if not spans:
        return text, []
    base = "hq-answer-secret-%s" % qid
    services, pieces, last = [], [], 0
    for i, (s, e) in enumerate(spans):
        svc = base if i == 0 else "%s-%d" % (base, i + 1)
        store_fn(svc, text[s:e])
        services.append(svc)
        pieces.append(text[last:s])
        pieces.append("[secret stored in Keychain: %s]" % svc)
        last = e
    pieces.append(text[last:])
    return "".join(pieces), services
