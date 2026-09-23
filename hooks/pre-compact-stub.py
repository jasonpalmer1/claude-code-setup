#!/usr/bin/env python3
"""PreCompact hook — mechanical, guaranteed stub log written BEFORE compaction.

L-0802 (round 5, after bug-gate FAIL on bcc41d50 — buggate-L0802-r4.md):
round 4 removed all transcript free text, but two allow-listed fields were
still leak vectors in their own right:

  Blocker 1 — `cwd` was written to the stub verbatim, unredacted, by
  explicit exemption. It is a filesystem path string an attacker or an
  ordinary directory-naming accident can shape however they like (e.g. a
  cloned-repo directory auto-named after a credentialed URL), and it reached
  the NEXT session's injected context unredacted. Worse, the round-4 test
  suite's own structural validator whitelisted any `- cwd: ` line
  unconditionally, so this class of leak could never have gone green.

  Blocker 2 — `files_touched` was filtered only by a four-word substring
  deny-list (.env/secret/credential/token), so well-known private-key and
  credential FILENAMES (id_rsa, .netrc, server.pem, kubeconfig, ...) sailed
  through untouched — the deny-list can always be missing the next word.

Round 5 is structural again, same philosophy as round 4 but pushed one
level further: instead of allow-listing-and-hoping, EVERY remaining field
is now shape-validated by a strict, total regex (or, for real_log, a
filesystem-identity check), and `cwd` / `files_touched` are removed as
STUB FIELDS entirely — not tightened, removed. Neither is needed to
resume: `real_log` already carries the actual context via the real /log
file it points to. Removing them means there is no longer a free-form
string field in the stub at all; every field either fullmatches a fixed
shape or is dropped.

The only fields written are:

  - session_id   must fullmatch a canonical UUID (8-4-4-4-12 hex groups) —
                 anything else falls back to the literal string "unknown"
                 (see validate_session_id()). Never used in a filename or
                 footer unvalidated.
  - written      an ISO timestamp this script generates itself (its own
                 clock) — never derived from any untrusted input.
  - ticket ids   matched by TICKET_RE (\\bL-\\d{4}\\b), deduplicated,
                 sorted, capped at MAX_TICKETS (see validate_tickets()).
  - commit short-hashes   the leading hash token of `git log --oneline`
                 lines only (never the commit message), each fullmatched
                 against ^[0-9a-f]{7,12}$, order preserved, capped at
                 MAX_COMMIT_HASHES (see validate_hashes()). The repo
                 location for this lookup is read from the hook's own
                 payload `cwd` — see the note on `repo_cwd` in main() for
                 why this is NOT a reintroduction of Blocker 1: that value
                 is used only as a `git -C` argument, is NEVER written to
                 the stub in any form, and the only thing that can reach
                 the stub from it is a hash token that has already passed
                 validate_hashes()'s strict shape check.
  - real_log     the path of the newest real `/log` file for THIS
                 session_id — now required to (a) resolve via
                 os.path.realpath to a file whose immediate parent
                 directory IS the real, resolved conversations dir (no
                 symlink escape, no nested subdirectory — closes the r4
                 gate's "Not blocking" note about symlink-followed globs),
                 (b) have a basename matching
                 ^\\d{4}-\\d{2}-\\d{2}_[\\w.-]+\\.md$ (the exact shape /log
                 always writes), and (c) contain this session's
                 `logged-session:` footer. A candidate failing any of the
                 three is dropped, not partially trusted. See
                 find_real_log().

  REMOVED entirely in round 5: `cwd` as a displayed field, and
  `files_touched` (and with it PATH_RE, DENY_PATH_SUBSTRINGS, MAX_FILES —
  all deleted, not just tightened). There is no longer any code path that
  writes an arbitrary filesystem path string into the stub body.

Every field is allow-listed by a strict, TOTAL regex (fullmatch, not just
a leading match) or a filesystem-identity check; anything that doesn't
match is DROPPED, not redacted or truncated-to-fit. redact() is kept only
as a second, defense-in-depth layer over the assembled content block
(tickets/hashes), never over the structural session_id/written/real_log
fields or the footer line the pickup hook matches on exactly.

See hooks/test-l0802-hooks.sh's round-5 sections for: unit tests of
validate_session_id/validate_tickets/validate_hashes/find_real_log
(including a symlink-escape repro and a bad-basename repro), and two
end-to-end repros lifted from the round-4 bug-gate report (a secret-shaped
cwd, and id_rsa/.netrc/.pem/kubeconfig file paths) asserting neither ever
appears anywhere in the stub any more.

Round-4 fixes, still in place: the stub carries no transcript free text —
no "last assistant message" field, no prose of any kind. redact() remains
as a second layer over the ticket/hash content block only.

Round-3 fixes (see ~/.claude/hub/reports/buggate-L0802-r2.md, blocker 1),
still in place: stubs live in their OWN dir, ~/.claude/compact-stubs/
(gitignored — confirmed via `git check-ignore`), never the memory
conversations dir, so neither of session-end-log.sh's greps (the qualified
one at line 120 nor the unqualified fallback at line 124) can ever match a
stub, by construction, not by pattern-tuning.

Round-2 fixes (see ~/.claude/hub/reports/buggate-L0802.md), still in place:

  1. NEVER writes the `<!-- logged-session: <id> -->` footer that
     session-end-log.sh / log-coverage.py treat as proof a REAL `/log` ran.
     This is a safety net, not a log. It writes a distinct footer
     (`<!-- compact-stub: <id> -->`) and a distinct filename prefix
     (`stub-...` instead of `precompact-...`).

  2. Everything is bounded. The transcript is read from the TAIL ONLY (seek
     from the end, capped at MAX_TAIL_BYTES) instead of walking the whole
     file. The whole stub body is capped at MAX_STUB_TOTAL bytes, truncated
     with a visible marker if exceeded. The file is written atomically (temp
     file + os.replace) so a killed/timed-out run cannot leave a
     half-written stub behind. As of round 5, every field is independently
     bounded by its own strict cap (MAX_TICKETS / MAX_COMMIT_HASHES / the
     UUID/hash shapes themselves), so the whole-body cap below is now
     defense-in-depth rather than the primary bound — it is kept rather
     than removed, in case a future round adds a field without the same
     discipline.

Runs on PreCompact (matcher: manual+auto, no matcher = both). PreCompact per
the docs cannot block compaction and cannot inject additionalContext — it can
only write to disk. This script is the write; SessionStart matcher "compact"
(session-start-compact-pickup.py) is the read-back.

Fail-open, always: a broken parse must never affect compaction. On any failure
this appends ONE line to the alarm log so a silent miss is caught by the next
audit instead of assumed fixed because the hook exists
([[feedback_logging_receipt_not_promise]]).

The stub dir, alarm log, and the real memory conversations dir (for the
real_log lookup) are all overridable via env vars (PRECOMPACT_STUB_DIR /
PRECOMPACT_STUB_ALARM_LOG / PRECOMPACT_MEMORY_CONVOS_DIR) so tests never
touch the real memory dir or the real delegation-alarms.log. Unset in
production — all three default to the real paths.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path


def _home_memory_dir():
    """~/.claude/projects/<home-slug>/memory -- the slug Claude Code
    derives from the real $HOME path (str(Path.home()).replace('/', '-')),
    computed at runtime so this hook works on any machine, not just the
    one it was written on."""
    slug = str(Path.home()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


# L-0920: find_real_log / validate_session_id / REAL_LOG_BASENAME_RE /
# SESSION_ID_RE / DEFAULT_MEMORY_CONVOS_DIR now live in hooks/lib/real_log.py
# (extracted, no behavior change -- see that module's docstring) so
# hooks/log-staleness-enforcer.py and hooks/pre-compact-real-log.sh can share
# the exact same lookup this script always used.
#
# L-0920 gate B3: this hook is live on every compact and must always write its
# stub, so the shared import is guarded. If lib/real_log.py ever fails to
# import, fall back to the pre-L-0920 inline lookup (0d7e3a19) below.
sys.path.insert(0, str(Path(__file__).resolve().parent))
_REAL_LOG_IMPORT_ERROR = None
try:
    from lib.real_log import (  # noqa: E402
        DEFAULT_MEMORY_CONVOS_DIR,
        REAL_LOG_BASENAME_RE,
        SESSION_ID_RE,
        find_real_log,
        validate_session_id,
    )
except Exception as _e:  # noqa: BLE001 -- any import failure, including SyntaxError
    _REAL_LOG_IMPORT_ERROR = type(_e).__name__
    DEFAULT_MEMORY_CONVOS_DIR = (
        _home_memory_dir() / "conversations"
    )
    SESSION_ID_RE = re.compile(
        r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    )
    REAL_LOG_BASENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_[\w.-]+\.md$")

    def validate_session_id(raw: str) -> str:
        return raw if SESSION_ID_RE.fullmatch(raw) else "unknown"

    def find_real_log(session_id: str, convos_dir: Path) -> str | None:
        try:
            if not convos_dir.exists():
                return None
            convos_real = os.path.realpath(str(convos_dir))
            needle = f"<!-- logged-session: {session_id} -->"
            candidates = []
            for md in convos_dir.glob("*.md"):
                if not REAL_LOG_BASENAME_RE.fullmatch(md.name):
                    continue
                if os.path.dirname(os.path.realpath(str(md))) != convos_real:
                    continue
                try:
                    text = md.read_text(errors="ignore")
                except OSError:
                    continue
                # Exact full-line footer match, same as lib/real_log.py
                # has_footer_line (84eab834): prose quoting a footer never counts.
                if any(line.strip() == needle for line in text.splitlines()):
                    candidates.append(md)
            if not candidates:
                return None
            return str(max(candidates, key=lambda p: p.stat().st_mtime))
        except Exception:
            return None

DEFAULT_ALARM_LOG = Path.home() / ".claude/hub/delegation-alarms.log"
# Stubs live in their OWN dir, never the memory conversations dir — see
# module docstring "Round-3 fixes" for why this is structural. Gitignored by
# the repo's whitelist .gitignore (confirmed via `git check-ignore`).
DEFAULT_STUB_DIR = Path.home() / ".claude/compact-stubs"

TICKET_RE = re.compile(r"\bL-\d{4}\b")
# Matches only the leading hash token of a `git log --oneline` line — never
# the commit message that follows it, which is free text.
COMMIT_HASH_LINE_RE = re.compile(r"^([0-9a-f]{7,12})\b")
# Round 5: TOTAL (fullmatch) shape checks applied a second time, in
# validate_session_id / validate_hashes / validate_tickets, after
# extraction — belt-and-suspenders so a future change to the extraction
# regex above can't silently widen what ends up in the stub.
HASH_RE = re.compile(r"^[0-9a-f]{7,12}$")
# SESSION_ID_RE / REAL_LOG_BASENAME_RE now live in lib/real_log.py (L-0920
# extraction) — imported above, not redefined here.

MAX_TICKETS = 50
MAX_COMMIT_HASHES = 50

MAX_TAIL_LINES = 400
MAX_TAIL_BYTES = 2 * 1024 * 1024       # cap the transcript READ, not just parsed lines
MAX_STUB_TOTAL = 12 * 1024             # cap the whole stub file (defense-in-depth — see docstring)
TRUNC_MARK = "\n...[truncated by pre-compact-stub.py — see full transcript for the rest]\n"

STUB_FOOTER_PREFIX = "compact-stub"     # distinct from "logged-session"
FNAME_PREFIX = "stub"                   # distinct from "precompact"


def alarm_log_path() -> Path:
    override = os.environ.get("PRECOMPACT_STUB_ALARM_LOG")
    return Path(override) if override else DEFAULT_ALARM_LOG


def alarm(msg: str) -> None:
    try:
        log = alarm_log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with log.open("a") as f:
            f.write(f"{stamp} L-0802 pre-compact-stub {msg}\n")
    except Exception:
        pass  # even the alarm must never raise


# --- Secret redaction — kept ONLY as a second, defense-in-depth layer over
# the assembled content block (tickets/hashes). Both fields are already
# allow-listed by a strict regex before they ever get here, so in practice
# this should never fire; it exists in case a future field is added to the
# content block without the same discipline.
# Mirrors hooks/safety-guard.py's SECRET / CREDENTIAL_LINE patterns.
SECRET_LITERAL = re.compile(
    r"sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[A-Z0-9]{16}"
    r"|ghp_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|re_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)

CRED_WORD = (
    r"(?:pass(?:code|word|phrase)?|pw|pwd|passwd|secret|token|api[_-]?key|"
    r"auth[_-]?key|admin[_-]?pass|access[_-]?key|private[_-]?key|"
    r"session(?:[_-]?id)?|cookie|bearer|auth(?:orization)?)"
)
CREDENTIAL_LINE = re.compile(
    r"(?i)\b" + CRED_WORD + r"\b[^\n]{0,24}?(?:==|===|!=|!==|=|:)\s*"
    r"(?P<q>[\"'`]?)(?P<val>[^\"'`\n]{2,80})(?P=q)"
)

# Connection-string-shaped secrets: any `scheme://user[:pass]@host[:port]` —
# host may be a hostname, a bare IP, or `localhost`; the userinfo before '@'
# is masked regardless of host shape.
URL_CRED = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]{1,15}://)"
    r"(?P<userinfo>[^\s/@]+)@(?P<hostport>[^\s/@]+)"
)

# KEY=value / KEY: value (env files, YAML, JSON, shell exports) whose key
# ends in one of these suffixes (case-insensitive) — the whole value is
# masked, not just a substring, since these keys are the credential by
# construction of the key name alone.
SENSITIVE_KEY_SUFFIX = (
    r"(?:_URL|_URI|_DSN|_KEY|_TOKEN|_SECRET|_PASSWORD|_PASS|_CREDENTIALS)"
)
SENSITIVE_KEY = re.compile(
    r"(?im)\b(?P<key>[A-Za-z][A-Za-z0-9_]*" + SENSITIVE_KEY_SUFFIX + r")"
    r"[\"'`]?\s*[:=]\s*"
    r"(?P<q>[\"'`]?)(?P<val>[^\"'`\n]+)(?P=q)"
)

# Long hex/base64-shaped runs (>=32 chars) — generic catch-all for tokens with
# no recognizable vendor prefix. Deliberately not entropy-gated: a false mask
# on a long non-secret token costs nothing here (this is masking, not
# blocking), a missed real secret costs a lot.
LONG_RUN = re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")

# Bearer / cookie header values, of ANY length.
HEADER_CRED = re.compile(
    r"(?im)\b(?:Authorization\s*:\s*Bearer|Set-Cookie|Cookie)\s*:?\s*"
    r"(?P<val>[^\r\n;]{2,200})"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ALLOWED_EMAIL = "you@example.com"


def _redact_credential_line(m: re.Match) -> str:
    val = m.group("val")
    if not val:
        return m.group(0)
    return m.group(0).replace(val, "[REDACTED]", 1)


def _redact_sensitive_key(m: re.Match) -> str:
    val = m.group("val")
    if not val:
        return m.group(0)
    return m.group(0).replace(val, "[REDACTED]", 1)


def _redact_url_cred(m: re.Match) -> str:
    return f"{m.group('scheme')}[REDACTED-CRED]@{m.group('hostport')}"


def _redact_header_cred(m: re.Match) -> str:
    val = m.group("val")
    if not val:
        return m.group(0)
    return m.group(0).replace(val, "[REDACTED]", 1)


def _redact_email(m: re.Match) -> str:
    return m.group(0) if m.group(0).lower() == ALLOWED_EMAIL else "[REDACTED-EMAIL]"


def redact(text: str) -> str:
    """Mask secret-shaped strings in a block of text. Second layer only —
    the primary defense is that nothing free-text (and, as of round 5, no
    uncontrolled path string of any kind) is ever assembled into the
    content block in the first place. Never applied to session_id, written,
    real_log, or the footer line, which the pickup hook matches on exactly
    and which are already independently shape-validated.

    Order matters: SENSITIVE_KEY and HEADER_CRED run before the generic
    CREDENTIAL_LINE/LONG_RUN catch-alls so a `REDIS_URL=...` or
    `Authorization: Bearer ...` line gets its whole value masked in one
    pass; URL_CRED then still catches a bare credentialed URL with no
    recognizable KEY= prefix at all."""
    if not text:
        return text
    text = SECRET_LITERAL.sub("[REDACTED-SECRET]", text)
    text = SENSITIVE_KEY.sub(_redact_sensitive_key, text)
    text = HEADER_CRED.sub(_redact_header_cred, text)
    text = URL_CRED.sub(_redact_url_cred, text)
    text = CREDENTIAL_LINE.sub(_redact_credential_line, text)
    text = LONG_RUN.sub("[REDACTED-LONGSTR]", text)
    text = EMAIL_RE.sub(_redact_email, text)
    return text


def tail_lines(path: Path, n: int) -> list[str]:
    """Read only the TAIL of a (possibly huge) JSONL file: seek to the last
    MAX_TAIL_BYTES and read that in one bounded call, then take the last n
    lines. No backward block-by-block growth — a single pathological
    multi-MB "line" at the tail can no longer force an unbounded read."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - MAX_TAIL_BYTES)
            f.seek(start)
            data = f.read()
        lines = data.split(b"\n")
        return [l.decode("utf-8", "ignore") for l in lines[-n:]]
    except Exception:
        return []


def parse_transcript(transcript_path: str) -> set[str]:
    """Extract ONLY ticket ids (regex match against raw transcript text,
    nothing else about the matching line is kept) from the tail of the
    transcript. Round 5 removed "files touched" entirely (see module
    docstring) — nothing about tool_use inputs, file paths, or any other
    transcript structure is read here any more; this is a plain regex scan
    over raw text."""
    tickets: set[str] = set()
    if not transcript_path:
        return tickets
    p = Path(transcript_path)
    if not p.exists():
        return tickets

    for raw in tail_lines(p, MAX_TAIL_LINES):
        raw = raw.strip()
        if not raw:
            continue
        tickets.update(TICKET_RE.findall(raw))

    return tickets


def validate_tickets(tickets: set[str]) -> list[str]:
    """Strict shape validation + cap: only \\bL-\\d{4}\\b-shaped ids
    (fullmatch, not just findall's own extraction), sorted, capped at
    MAX_TICKETS. Anything that doesn't fullmatch is dropped, not
    truncated-to-fit — same allow-list philosophy as every other field."""
    valid = sorted(t for t in tickets if TICKET_RE.fullmatch(t))
    return valid[:MAX_TICKETS]


def validate_hashes(hashes: list[str]) -> list[str]:
    """Strict shape validation + cap: only ^[0-9a-f]{7,12}$-shaped tokens,
    order preserved (git log's own most-recent-first order), capped at
    MAX_COMMIT_HASHES. Anything that doesn't fullmatch is dropped."""
    valid = [h for h in hashes if HASH_RE.fullmatch(h)]
    return valid[:MAX_COMMIT_HASHES]


def git_commit_hashes(repo_cwd: str) -> list[str]:
    """Only the leading short-hash token of each `git log --oneline` line —
    never the commit message, which is free text and could contain
    anything a developer typed. `repo_cwd` is used ONLY as the `-C`
    argument below; it is never written anywhere, including on failure
    (the except path below returns [] with no detail)."""
    try:
        out = subprocess.run(
            ["git", "-C", repo_cwd or ".", "log", "--oneline", "-5"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return []
        hashes = []
        for line in out.stdout.splitlines():
            m = COMMIT_HASH_LINE_RE.match(line.strip())
            if m:
                hashes.append(m.group(1))
        return hashes
    except Exception:
        return []


def atomic_write(path: Path, content: str) -> None:
    """Write via temp file + rename so a killed/timed-out run never leaves a
    half-written stub on disk."""
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-" + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main() -> int:
    if _REAL_LOG_IMPORT_ERROR:
        alarm(f"lib/real_log.py import failed ({_REAL_LOG_IMPORT_ERROR}); using inline fallback lookup")
    try:
        raw_in = sys.stdin.read()
    except Exception:
        raw_in = ""
    try:
        payload = json.loads(raw_in) if raw_in.strip() else {}
    except Exception:
        payload = {}
        alarm("could not parse PreCompact stdin JSON — best-effort stub only")

    session_id_raw = str(payload.get("session_id") or "unknown")
    session_id = validate_session_id(session_id_raw)
    # repo_cwd is read from the hook's own trusted payload ONLY to locate
    # the git repo for the commit-hash lookup below. Round 5 removed the
    # cwd DISPLAY field entirely (bug-gate round-4 Blocker 1: an
    # attacker/environment-controlled cwd string reached the next session's
    # injected context verbatim). This internal-only use never writes
    # repo_cwd anywhere, in any form — the only thing that can reach the
    # stub from it is the leading hash token of `git log`'s own output, and
    # that token is independently, strictly shape-validated by
    # validate_hashes() before it is ever written. See module docstring.
    repo_cwd = str(payload.get("cwd") or "")
    transcript_path = str(payload.get("transcript_path") or "")

    try:
        stub_dir = Path(os.environ.get("PRECOMPACT_STUB_DIR") or DEFAULT_STUB_DIR)
        stub_dir.mkdir(parents=True, exist_ok=True)
        convos_dir = Path(
            os.environ.get("PRECOMPACT_MEMORY_CONVOS_DIR") or DEFAULT_MEMORY_CONVOS_DIR
        )

        tickets = validate_tickets(parse_transcript(transcript_path))
        hashes = validate_hashes(git_commit_hashes(repo_cwd))
        real_log = find_real_log(session_id, convos_dir)

        now = datetime.now()
        sid_short = session_id[:8]
        fname = f"{now:%Y-%m-%d}_{now:%H%M%S}_{FNAME_PREFIX}-{sid_short}.md"
        out_path = stub_dir / fname

        footer = f"<!-- {STUB_FOOTER_PREFIX}: {session_id} -->"

        # Structural block — trusted, strictly-validated fields only, NEVER
        # passed through redact(): session_id/written come from this
        # script's own logic (never transcript content), and real_log comes
        # from a filesystem identity check against the real memory dir, not
        # from parsing any transcript prose. real_log is deliberately kept
        # here rather than in the redacted content block below: it is what
        # the pickup hook turns into the "Resume from <path>" instruction,
        # and a real /log filename's slug is routinely a 32+ char hyphenated
        # string that the LONG_RUN safety-net pattern would otherwise
        # mangle — breaking the one feature this round adds would be a
        # worse failure than skipping a redundant redact() pass over an
        # already-trusted, already-shape-validated path. The footer must
        # also survive byte-for-byte for the pickup hook's exact match, so
        # it stays out of the redacted block too.
        real_log_line = real_log if real_log else "(none found)"
        structural = [
            "# PreCompact stub (NOT a /log — mechanical safety net only)",
            "",
            "This file is written mechanically by pre-compact-stub.py, with no model",
            "involvement and no transcript prose. It is NOT proof a real `/log` ran",
            "for this session.",
            "",
            f"- session_id: {session_id}",
            f"- written: {now.isoformat()}",
            "",
            "## Real /log for this session",
            f"- real_log: {real_log_line}",
            "",
        ]

        tickets_line = ", ".join(tickets) if tickets else "(none found)"
        hashes_line = ", ".join(hashes) if hashes else "(none found)"

        content_lines = [
            "## Ticket ids referenced (L-nnnn)",
            tickets_line,
            "",
            "## Recent commit hashes",
            hashes_line,
            "",
        ]

        # redact() as a second, defense-in-depth layer — only over this
        # content block, whose fields are already allow-listed by strict
        # regex before they ever get here.
        content_text = redact("\n".join(content_lines))

        structural_text = "\n".join(structural)
        body = structural_text + content_text + "\n" + footer + "\n"

        body_bytes = body.encode("utf-8", "ignore")
        if len(body_bytes) > MAX_STUB_TOTAL:
            # Reserve room for the structural header and footer so both
            # survive the cap intact — the footer is what SessionStart
            # pickup matches on verbatim, and the structural header carries
            # session_id/written, never subject to truncation. As of round
            # 5 every field is independently capped (see module docstring),
            # so this branch should be unreachable in practice; kept as
            # defense-in-depth.
            reserve = (
                len(structural_text.encode("utf-8"))
                + len(footer.encode("utf-8"))
                + len(TRUNC_MARK.encode("utf-8"))
                + 2
            )
            budget = max(MAX_STUB_TOTAL - reserve, 0)
            content_bytes = content_text.encode("utf-8", "ignore")
            head = content_bytes[:budget].decode("utf-8", "ignore")
            body = structural_text + head + TRUNC_MARK + "\n" + footer + "\n"

        atomic_write(out_path, body)
    except Exception:
        tb = traceback.format_exc(limit=3).strip().splitlines()
        alarm(f"sid={session_id} FAILED: {tb[-1] if tb else 'unknown error'}")

    return 0  # always fail-open — PreCompact must never be blocked by this hook


if __name__ == "__main__":
    raise SystemExit(main())
