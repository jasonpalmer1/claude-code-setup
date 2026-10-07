"""ask_lib -- shared HQ plumbing for ask-operator (CLI) and ask-poller (launchd). urllib only, no deps.
Secrets (HQ_BASE, SUGGEST_POST_TOKEN) are read in-process from ~/.claude/routines/.env and never printed."""
import json, os, sys, urllib.request, urllib.error

ENV_PATH = os.environ.get("ASK_ENV_PATH", os.path.expanduser("~/.claude/routines/.env"))
UA = "hq-ask-operator/1.0 (+<mission-control-project>)"  # default Python UA is 403'd by Cloudflare


def read_env(path=ENV_PATH):
    out = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                out[k.strip()] = v
    except OSError:
        pass
    return out


def config():
    env = read_env()
    base = (os.environ.get("ASK_HQ_BASE") or env.get("HQ_BASE") or "").rstrip("/")
    tok = env.get("SUGGEST_POST_TOKEN") or os.environ.get("SUGGEST_POST_TOKEN")
    if not base or not tok:
        sys.stderr.write("ask-operator: missing HQ_BASE / SUGGEST_POST_TOKEN in %s\n" % ENV_PATH)
        sys.exit(2)
    return base, tok


def call(method, path, payload=None, timeout=20):
    """Returns (status, json|None). Raises OSError on network failure."""
    base, tok = config()
    headers = {"User-Agent": UA, "Authorization": "Bearer " + tok}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace"); st = r.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace"); st = e.code
    except urllib.error.URLError as e:
        raise OSError(str(getattr(e, "reason", e)))
    try:
        return st, json.loads(raw)
    except ValueError:
        return st, None
