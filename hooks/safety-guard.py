#!/usr/bin/env python3
"""PreToolUse guard: the CLAUDE.md security section made deterministic (2026-07-22
ecosystem scan). Since 2026-07-12 the operator ships without asking — this restores the
can't-happen layer for the catastrophic class. TIGHT patterns only: a false positive
here costs more trust than it saves. Exit 2 = block. Never blocks on its own failure."""
import json, sys, re, os, subprocess, time, shlex

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
# --- ClickFix / remote-execution one-liners (2026-09-09) -------------------
# the operator's employer flagged ClickFix: a web page tells a human it needs a
# "fix" or a human check, hands them a command, and the human pastes it into
# a terminal. The payload is never read by anyone before it runs.
#
# On THIS machine the human does not run commands — I do. So the same attack
# arrives pointed at me instead: a fetched page, an email, a job description,
# an MCP result, a README that says "run this to continue." I have a real
# terminal and I am the one who can be talked into using it, which makes the
# agent the softer target, not the harder one.
#
# The defence is identical in both directions: never execute code in the same
# breath as downloading it. There is deliberately NO override token here. The
# threat model is that the caller has been deceived, so a caller-supplied
# override would be written by the attacker. The honest path is always open:
# fetch to a file, READ it, then run the file — or the operator runs it himself with
# the `!` prefix. Two steps, and a human or an unfooled agent sees the code.
FETCH = r"(?:curl|wget)\b[^|;&\n]*"
INTERP = r"(?:(?:ba|z|k|da)?sh|python[\d.]*|perl|ruby|node|osascript)(?!\s+-m\b)"
REMOTE_EXEC = [
    (rf"{FETCH}\|\s*(?:sudo\s+)?{INTERP}\b",
     "a download piped straight into an interpreter (curl ... | sh)"),
    (r"(?:eval|source)\s+[\"']?[$`]\(?\s*(?:sudo\s+)?(?:curl|wget)\b",
     "a download evaluated as code (eval \"$(curl ...)\")"),
    (r"[$`]\(\s*(?:curl|wget)\b[^)]*\)\s*\|\s*(?:sudo\s+)?(?:(?:ba|z)?sh|python[\d.]*)\b",
     "command substitution feeding a download to an interpreter"),
    (rf"base64\s+(?:-{{1,2}}[a-zA-Z-]+\s+)*(?:-d|-D|--decode)\b[^|;\n]*\|\s*(?:sudo\s+)?{INTERP}\b",
     "a base64 blob decoded straight into an interpreter"),
    # Order-independent: the payload may exec() around the fetch or after it.
    (r"python[\d.]*\s+-c\b"
     r"(?=[^\n]*(?:urlopen|urlretrieve|requests\.get|urllib|http))"
     r"(?=[^\n]*(?:exec\(|eval\(|os\.system|popen|subprocess))",
     "python fetching and executing remote code inline"),
    (r"osascript\b[^\n]*do shell script[^\n]*(?:curl|wget)\b",
     "AppleScript shelling out to a downloader"),
    # Shapes a Windows ClickFix page hands out. They cannot run on macOS, but a
    # Bash call carrying one means something pasted a payload through me.
    (r"\b(?:powershell|pwsh)\b[^\n]*\s-(?:e|en|enc|encoded|encodedcommand)\b",
     "a base64-encoded PowerShell payload"),
    (r"\bmshta\s+(?:https?:|javascript:)", "mshta executing remote content"),
    (r"\bcertutil\b[^\n]*-urlcache", "certutil used as a file downloader"),
    (r"\biex\s*\(\s*(?:new-object|iwr|invoke-webrequest)", "PowerShell download-and-execute"),
]

SECRET = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{30,}"
    r"|xox[bp]-[A-Za-z0-9-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)

# --- added 2026-07-28, after a security audit found three leaks this guard
# --- would have sailed straight past. Vendor-prefixed keys were the only thing
# --- it knew about; every real leak was a plain literal with no prefix at all:
#       a short hardcoded passcode gating access to customer data
#       a password compared against a literal string in client-side code
#       a live credential echoed into a deploy script's output, landing in commits
# A password does not look like a password. What marks it is the IDENTIFIER
# sitting next to it, so match on that instead — assignment or comparison.
CRED_WORD = (
    r"(?:pass(?:code|word|phrase)?|pw|pwd|passwd|secret|token|api[_-]?key|"
    r"auth[_-]?key|admin[_-]?pass|access[_-]?key|private[_-]?key)"
)

# Shape 1 — a quoted literal after an assignment or a comparison. Covers both
# the hardcoded-constant form and the compare-against-a-literal form, which is
# how the client-side admin gate leaked.
CREDENTIAL_LINE = re.compile(
    r"(?i)\b" + CRED_WORD + r"\b[^\n]{0,24}?(?:==|===|!=|!==|=|:)\s*"
    r"(?P<q>[\"'`])(?P<val>[^\"'`\n]{2,60})(?P=q)"
)

# Shape 2 — an unquoted, secret-looking token immediately after the word. This
# is how credentials leak into markdown, memory files, and shell echo lines,
# where nothing is quoted. Gated by _looks_secret so ordinary prose such as
# "the password is stored in Cloudflare" never trips it.
# Bare "pass" is deliberately EXCLUDED from the loose rule below. In prose it is
# overwhelmingly a noun — a review pass, a QA pass — usually followed by a date
# or a number, which the entropy check then reads as a credential. The tripwire
# caught this misfiring on real memory files within minutes of being written.
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

# guard-fixes round 5, item 5 (L-0885): the same ALL_CAPS false positive
# L-0848 fixed in hooks/security-tripwire.py -- CREDENTIAL_LOOSE's entropy
# check (a digit, or 2+ `-`/`_` groups) is satisfied by an env-var NAME
# mentioned in prose right after a credential word ("the token
# NIGHTLY_NTFY_TOPIC is set", "rotate FLEET_POST_TOKEN"), not just an
# actual value. Same regex, same rule, copied verbatim from L-0848's fix in
# security-tripwire.py (2026-09-23, fb592b84): an ALL_CAPS identifier --
# letters/digits/underscore with no hyphen, OR letters/hyphens with no
# digit -- reads as a NAME, not a value. A real secret that mixes hyphens
# AND digits (`ABCD-1234-EFGH-5678`, the L-0848 regression test) still
# fails BOTH alternatives and stays caught; a real secret with mixed case
# (the overwhelmingly common shape for anything 16+ chars, and the shape
# every fixture in this file's own must-BLOCK section uses) never matches
# an ALL_CAPS-only pattern in the first place.
CRED_IDENTIFIER = re.compile(r"^(?:[A-Z][A-Z0-9_]+|[A-Z][A-Z-]+)$")

def _looks_secret(val: str) -> bool:
    """Entropy signature of a credential rather than a word: digits, or several
    separator groups. Conservative on purpose — shape 1 already covers quoted
    cases, so this only needs the obvious unquoted ones."""
    if PLACEHOLDER.match(val) or NOT_A_SECRET.match(val) or CRED_IDENTIFIER.match(val):
        return False
    has_digit = any(c.isdigit() for c in val)
    groups = val.count("-") + val.count("_")
    return (has_digit and len(val) >= 8) or groups >= 2

# Things that look like credentials but are not — placeholders, env lookups,
# and the sort of prose that shows up in docs. Kept generous: this guard runs on
# every write, and a false block costs more trust than it saves.
PLACEHOLDER = re.compile(
    r"(?i)^\s*("
    r"x{3,}|\.{3,}|-+|change[_-]?me|your[_-]?[\w-]+|[\w-]*example[\w-]*|[\w-]*sample[\w-]*|"
    r"dummy[\w-]*|placeholder[\w-]*|test[\w-]*|fake[\w-]*|redacted[\w-]*|none|null|true|false|"
    r"\$\{[^}]*\}|\$[A-Z_]+|process\.env\.[\w.]+|env\.[\w.]+|os\.environ.*|"
    r"<[^>]*>|\{\{[^}]*\}\}|[\w-]*here|"
    # HTML/form attribute VALUES. A login field declares a passcode name and a
    # password type side by side; the regex reads that as a credential word
    # followed by a quoted value, but the value is the field type, not a secret.
    # Hit while writing the fix for the iOS auto-capitalize lockout.
    r"password|passcode|current-password|new-password|one-time-code|"
    r"off|on|none|text|email|username|tel|search|hidden|submit"
    r")\s*$"
)

# --- settings.json / settings.local.json protection (2026-09-16 guard-fixes
# --- item 1) -----------------------------------------------------------
# CLAUDE.md: "Never hot-edit ~/.claude/settings.json in a running session...
# changes wait for a fresh session or the operator's explicit go." Zero code in this
# file mentioned settings.json before this — the rule was 100% prose. This is
# the hook-layer half (defense in depth for cp/jq+mv/python-rewrite, the 3
# mechanisms a `permissions.ask` rule in settings.json can't reach on its
# own); the harness-layer half is the matching Edit() ask rule in
# settings.json itself.
SETTINGS_PATH = r"(?:~|\$HOME|" + "/Users" + r"/[\w.-]+)/\.claude/settings(?:\.local)?\.json"
SETTINGS_PATH_RE = re.compile(SETTINGS_PATH)

# Hub audit amendment A1 (2026-09-16): the original Bash-branch check fired
# whenever the command contained BOTH a write-shaped token (>, sed -i, tee,
# cp, mv, jq, python -c, json.dump) ANYWHERE and the settings path ANYWHERE
# else — so `grep -n deploy ~/.claude/settings.json 2>/dev/null` asked (">"
# from "2>/dev/null" is unrelated to the path) and so did a routine
# `python3 -c "json.load(open(...))"` validation. Ask only when the settings
# path is the WRITE TARGET itself: a redirect target, a tee/sed -i argument,
# the DESTINATION (last) argument of cp/mv, or a python/node call that opens
# it in a write mode or hands it to os.replace/json.dump/writeFileSync.
SETTINGS_BASH_WRITE_RE = re.compile(
    r">>?\s*[\"']?" + SETTINGS_PATH + r"|"                              # redirect target
    r"\btee\b[^\n;&|]*" + SETTINGS_PATH + r"|"                          # tee argument
    r"\bsed\s+-i[^\n;&|]*" + SETTINGS_PATH + r"|"                       # sed -i ... path
    r"\b(?:cp|mv)\b[^\n;&|]*\s" + SETTINGS_PATH + r"\s*(?:[;&|]|$)|"    # cp/mv DESTINATION
    r"open\(\s*[\"']" + SETTINGS_PATH + r"[\"']\s*,\s*[\"'][wax]|"      # python open(path,'w'/'a'/'x')
    r"os\.replace\([^)]*" + SETTINGS_PATH + r"|"
    r"json\.dump\([^)]*" + SETTINGS_PATH + r"|"
    r"writeFileSync\(\s*[\"']" + SETTINGS_PATH + r"[\"']"
)

# 2026-09-16 guard-fixes step 10, bug-gate 4c: SETTINGS_BASH_WRITE_RE's
# cp/mv branch only fires when the settings path is the DESTINATION (last
# argument). `mv ~/.claude/settings.json /tmp/x.json` has it as the SOURCE
# instead, so nothing caught it, and none of the CATASTROPHIC `rm` patterns
# cover `mv`/`rm`/`unlink`/`truncate` either. This catches the settings path
# in ANY position as an operand of a command that moves, deletes, or
# truncates it -- same ask, not a hard block, since a human may genuinely
# want to relocate/delete it. `cp` (copy, not remove) stays out of this list
# on purpose: "cp ~/.claude/settings.json /tmp/x.json" (source, not dest)
# must stay a silent allow, and cp-as-destination is already covered above.
#
# guard-fixes step 11 (hub follow-up on 4c, amendment A1 regression): the
# step-10 version matched the verb ANYWHERE before the path, not just at a
# real command position -- `grep -n "rm -rf" ~/.claude/settings.json` and
# `grep -c "Bash(rm" ~/.claude/settings.json` both asked, because "rm"
# showed up inside a quoted search pattern, nowhere near being an actual
# command. Fixed by requiring the destructive verb to sit at a genuine
# command-introducing position: start of a segment (after `;`, `&&`, `||`,
# `|`, a newline, `` ` ``, or `$(`), optionally after `sudo `, or introduced
# by `find ... -exec`, `bash -c "`/`sh -c '`, or `eval "`.
#
# guard-fixes step 12 (re-gate FAIL on step 11, same root cause one
# character class wider): step 11's position anchors are still pure TEXT
# matching with no awareness of shell quoting, so a `;` or `|` sitting
# INSIDE a quoted grep/awk search pattern (`grep -E "rm|mv" ...`,
# `grep -n "a;rm -rf" ...`) still read as a real command boundary and asked
# -- the exact bug class step 11 was built to close, just with `;`/`|`
# instead of the `(` case it disclosed and tested for. The three regexes
# below are KEPT, unchanged, but demoted to a fallback used only when the
# real tokenizer (below) can't parse a line at all (unbalanced quotes etc).
# `_settings_destructive` itself is now the tokenizer-based rewrite further
# down; these three names are no longer called from the main path.
_DESTRUCTIVE_VERB = r"(?:mv|rm|unlink|truncate)"
_SETTINGS_DESTRUCTIVE_AT_POSITION = re.compile(
    r"(?:"
    r"(?:^|[;&|\n]|\$\(|`)\s*(?:sudo\s+)?" + _DESTRUCTIVE_VERB + r"\b"
    r"|\bfind\b[^\n;&|]*?-exec\s+(?:sudo\s+)?" + _DESTRUCTIVE_VERB + r"\b"
    r"|\b(?:bash|sh)\s+-c\s+[\"']\s*(?:sudo\s+)?" + _DESTRUCTIVE_VERB + r"\b"
    r"|\beval\s+[\"']\s*(?:sudo\s+)?" + _DESTRUCTIVE_VERB + r"\b"
    r")[^\n;&|]*" + SETTINGS_PATH
)
# `find ~/.claude -name settings.json -exec rm {} \;` never spells the path
# as one literal token (SETTINGS_PATH can't match it), so it needs its own
# shape: a home-rooted `find` root under `.claude`, a `-name`/`-iname` match
# on settings(.local).json, and a destructive `-exec`, in that order, all in
# the same segment.
_FIND_SETTINGS_DESTRUCTIVE = re.compile(
    r"\bfind\b[^\n;&|]*(?:~|\$HOME|" + "/Users" + r"/[\w.-]+)[^\n;&|]*?\.claude\b"
    r"[^\n;&|]*-i?name\s+[\"']?settings(?:\.local)?\.json[\"']?"
    r"[^\n;&|]*-exec\s+(?:sudo\s+)?" + _DESTRUCTIVE_VERB + r"\b"
)
# xargs decouples the argument's text position from the verb -- in
# `echo ~/.claude/settings.json | xargs rm` the path sits BEFORE `xargs rm`,
# read from stdin, not after it. Checked as a whole-command shape instead of
# "verb, then path, same segment" -- step 12's re-gate flagged (as a NOTE,
# not a second FAIL) that this makes it unscoped to its own pipeline, so
# `echo x | xargs rm; cat ~/.claude/settings.json` wrongly asked on the
# unrelated `cat`. The tokenizer rewrite below fixes that scoping too; this
# regex stays only as the ValueError fallback's xargs shape.
_XARGS_DESTRUCTIVE = re.compile(
    r"\bxargs\b(?:\s+-{1,2}[\w-]+(?:\s+\S+)?)*\s+" + _DESTRUCTIVE_VERB + r"\b"
)


def _settings_destructive_fallback(line):
    """The step-10/11 regex approach, unchanged. Used ONLY when the step-12
    tokenizer below can't parse a line (shlex ValueError -- an unbalanced
    quote etc). Fails safe toward asking, same as before."""
    if _SETTINGS_DESTRUCTIVE_AT_POSITION.search(line):
        return True
    if _FIND_SETTINGS_DESTRUCTIVE.search(line):
        return True
    if _XARGS_DESTRUCTIVE.search(line) and SETTINGS_PATH_RE.search(line):
        return True
    return False


# guard-fixes step 12: tokenizer-based rewrite of _settings_destructive.
# Text-position regexes (steps 10, 11) cannot tell a real shell separator
# from the same character sitting inside a quoted string -- that gap kept
# reopening one character class at a time (first `(`, then `;`/`|`). A real
# tokenizer sidesteps the whole class at once: shlex with posix=True and
# punctuation_chars=True keeps quoted text as ONE token and emits
# `;`/`|`/`&&`/`||`/`&`/`(`/`)` as their own tokens, so `grep -E "rm|mv" ...`
# tokenizes to ['grep', '-E', 'rm|mv', ...] -- the pipe inside the quotes
# never becomes a separator, and `grep` (not `rm`) is unambiguously the
# first word.
_DESTRUCTIVE_VERBS = {"mv", "rm", "unlink", "truncate"}
_PIPELINE_BREAK = {";", "&&", "||", "&"}
_VAR_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# sudo flags that consume the FOLLOWING token as their value (conservative,
# common set -- covers the required `sudo -u root rm ...` shape).
_SUDO_VALUE_FLAGS = {"-u", "-g", "-p", "-h", "-C", "-r", "-t"}
_BARE_WRAPPERS = {"command", "nohup", "time", "exec", "then", "do", "else", "{", "!"}
_PROJECT_SETTINGS_FULL_RE = re.compile(
    r"^" + "/Users" + r"/[\w.-]+/\.claude/settings(?:\.local)?\.json$"
)


def _settings_real_targets():
    """Real, resolved settings.json / settings.local.json paths under the
    real HOME -- computed at call time from os.path.expanduser("~"), same
    pattern as _rites_allowed, so a scratch-HOME test gets its own set."""
    home = os.path.realpath(os.path.expanduser("~"))
    claude = os.path.join(home, ".claude")
    return {
        os.path.realpath(os.path.join(claude, "settings.json")),
        os.path.realpath(os.path.join(claude, "settings.local.json")),
    }


def _expand_home_token(token):
    """Expand ~, $HOME, and ${HOME} the way a shell would, without
    invoking one."""
    home = os.path.expanduser("~")
    token = token.replace("${HOME}", home).replace("$HOME", home)
    if token == "~":
        token = home
    elif token.startswith("~/"):
        token = home + token[1:]
    return token


# guard-fixes round 5, item 1 (bug-gate round 3/4 regression): `rm
# $(echo ~/.claude/settings.json)`, `` rm `echo ~/.claude/settings.json` ``,
# `mv ~/.claude/settings.json{,.bak}`, `sed -i "..." $(echo ...)`, and
# `tee $(echo ...)` all silently ALLOWED. Root causes, both upstream of
# _is_settings_operand's exact-token match:
#   (a) $(...) tokenizes (shlex, punctuation_chars=True) to bare `$`, `(`,
#       ...inner tokens..., `)` -- and _split_pipelines treats a bare `(`/
#       `)` as a SUBSHELL-GROUP boundary (needed for the legitimate
#       `( rm ~/.claude/settings.json )` case step 12 already covers), so
#       `rm $(echo ~/.claude/settings.json)` silently splits into TWO
#       unrelated "pipelines" -- `rm $` and `echo ~/.claude/settings.json`
#       -- and the verb `rm` never sees the path at all.
#   (b) backticks keep the path in the SAME simple command's tokens
#       (`` `echo `` and `~/.claude/settings.json` `` `` as two tokens) but
#       neither is an EXACT match for the settings path, so the existing
#       exact-token check still misses it.
#   (c) brace expansion (`settings.json{,.bak}`) stays ONE shlex token --
#       a real shell would expand it into TWO words, one of which (the
#       empty alternative) is exactly the settings path -- but nothing here
#       ever performs that expansion.
# Fix: (1) extract $(...) / `...` spans BEFORE tokenizing and replace each
# with an opaque single-token placeholder (so they can never be misread as
# subshell-group punctuation, and never split a pipeline), stashing the
# inner text so _is_settings_operand can inspect it; (2) brace-expand a
# token with a `{a,b,...}` group and check each alternative. Both checks
# are deliberately loose/substring-based rather than exact-match -- per the
# round-5 brief, "prefer asking" for this class over a silent miss.
_SUBST_MAP = {}
_SUBST_PLACEHOLDER_RE = re.compile(r"\x00SGSUBST(\d+)\x00")
_SUBST_COUNTER = [0]


def _extract_substitutions(text):
    """Replace every $(...) and `...` span in `text` with an opaque
    placeholder token (no shell-special or whitespace characters, so it can
    never be re-split by the tokenizer or _split_pipelines). Returns the
    rewritten text; inner (unexpanded) text is stashed in _SUBST_MAP keyed
    by the placeholder for _is_settings_operand to inspect. Module-level
    map is fine here -- this is a single-shot CLI process, one JSON line on
    stdin per invocation, no concurrency."""
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            depth = 1
            j = i + 2
            while j < n and depth > 0:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            inner = text[i + 2:j - 1] if depth == 0 else text[i + 2:j]
            key = "\x00SGSUBST%d\x00" % _SUBST_COUNTER[0]
            _SUBST_COUNTER[0] += 1
            _SUBST_MAP[key] = inner
            out.append(key)
            i = j
            continue
        if ch == "`":
            j = text.find("`", i + 1)
            if j == -1:
                out.append(ch)
                i += 1
                continue
            inner = text[i + 1:j]
            key = "\x00SGSUBST%d\x00" % _SUBST_COUNTER[0]
            _SUBST_COUNTER[0] += 1
            _SUBST_MAP[key] = inner
            out.append(key)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_BRACE_GROUP_RE = re.compile(r"\{([^{}]*)\}")


def _brace_expand(token, limit=32):
    """One non-nested {a,b,c} group -> list of expansions (the token
    itself if there's no group, more than `limit` alternatives, or fewer
    than 2 -- not real brace syntax). Deliberately not a full bash-grammar
    implementation (no nesting, no ranges) -- the named bypass shape is a
    single trailing group (`settings.json{,.bak}`), and this is meant to
    catch that conservatively, not to be a shell."""
    m = _BRACE_GROUP_RE.search(token)
    if not m:
        return [token]
    alts = m.group(1).split(",")
    if len(alts) < 2 or len(alts) > limit:
        return [token]
    prefix, suffix = token[:m.start()], token[m.end():]
    return [prefix + a + suffix for a in alts]


def _is_settings_operand(token):
    """A settings operand is a TOKEN (exact match, never a substring
    search) that, after ~/$HOME/${HOME} expansion, is either the real
    resolved settings.json/settings.local.json under the real HOME, or
    matches the pre-tokenizer absolute-path form of the settings file
    shape directly under any user's home (kept for parity with the old
    SETTINGS_PATH behavior -- a project-level path with anything ELSE
    between the username and `.claude` still does not match, so
    an absolute nested-project settings.json path stays silent, same
    as before). Round 5: also true when the token is (or embeds) a
    substitution placeholder whose inner text mentions the settings path,
    or when a brace-expansion alternative resolves to it."""
    expanded = _expand_home_token(token)
    for m in _SUBST_PLACEHOLDER_RE.finditer(expanded):
        inner = _SUBST_MAP.get(m.group(0), "")
        if SETTINGS_PATH_RE.search(_expand_home_token(inner)):
            return True
    for candidate in _brace_expand(expanded):
        cand = _expand_home_token(candidate)
        try:
            real = os.path.realpath(cand)
        except Exception:
            real = cand
        if real in _settings_real_targets():
            return True
        if _PROJECT_SETTINGS_FULL_RE.match(cand):
            return True
    return False


def _tokenize_line(line):
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _split_pipelines(tokens):
    """One line's tokens -> list of pipelines; each pipeline is a list of
    simple commands (token lists) joined only by a bare `|`. Pipelines
    themselves are separated by `;`, `&&`, `||`, `&`, `(`, `)` -- this is
    what lets the xargs check (item d) scope to "an earlier command in the
    SAME pipeline" instead of anywhere in the whole line."""
    pipelines = []
    pipeline = []
    cmd = []
    for tok in tokens:
        if tok == "|":
            if cmd:
                pipeline.append(cmd)
                cmd = []
            continue
        if tok in _PIPELINE_BREAK or tok in ("(", ")"):
            if cmd:
                pipeline.append(cmd)
                cmd = []
            if pipeline:
                pipelines.append(pipeline)
                pipeline = []
            continue
        cmd.append(tok)
    if cmd:
        pipeline.append(cmd)
    if pipeline:
        pipelines.append(pipeline)
    return pipelines


def _strip_wrappers(tokens):
    """Repeatedly peel leading wrapper tokens off a simple command until
    the real command word is at index 0: sudo (+ its flags/args), env (+
    VAR=val words), a bare VAR=val word, command/nohup/time/exec/then/do/
    else/{/!."""
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "sudo":
            i += 1
            while i < n and tokens[i].startswith("-") and tokens[i] != "-":
                flag = tokens[i]
                i += 1
                if flag in _SUDO_VALUE_FLAGS and i < n:
                    i += 1
            continue
        if tok == "env":
            i += 1
            while i < n and _VAR_ASSIGN_RE.match(tokens[i]):
                i += 1
            continue
        if _VAR_ASSIGN_RE.match(tok):
            i += 1
            continue
        if tok in _BARE_WRAPPERS:
            i += 1
            continue
        break
    return tokens[i:]


def _normalize_verb(word):
    """basename + drop one leading backslash (shlex's own posix escape
    handling already collapses `\\rm` to `rm` before this ever sees it, but
    this stays defensive)."""
    if word.startswith("\\"):
        word = word[1:]
    if "/" in word:
        word = word.rsplit("/", 1)[-1]
    return word


def _cmd_is_destructive_on_settings(cmd_tokens):
    """Item (a): first word is a destructive verb, any LATER token is a
    settings operand."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped:
        return False
    verb = _normalize_verb(stripped[0])
    if verb in _DESTRUCTIVE_VERBS:
        return any(_is_settings_operand(t) for t in stripped[1:])
    return False


def _find_is_destructive_on_settings(cmd_tokens):
    """Item (c): `find` referencing `.claude` plus settings(.local).json (a
    -name value or a path token), with a destructive -exec/-execdir."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or _normalize_verb(stripped[0]) != "find":
        return False
    has_claude = any(".claude" in t for t in stripped)
    has_settings_name = any(
        t.strip("\"'") in ("settings.json", "settings.local.json") for t in stripped
    ) or any(_is_settings_operand(t) for t in stripped)
    if not (has_claude and has_settings_name):
        return False
    for i, t in enumerate(stripped):
        if t in ("-exec", "-execdir") and i + 1 < len(stripped):
            if _normalize_verb(stripped[i + 1]) in _DESTRUCTIVE_VERBS:
                return True
    return False


def _bash_c_is_destructive(cmd_tokens, depth):
    """Item (b): bash/sh/zsh -c "<string>", or eval "<string>" -- recurse
    into the wrapped text as its own command. `depth` bounds the recursion
    (a wrapper can only wrap something SMALLER each time in practice; this
    is just a hard backstop so a pathological input fails to a quiet
    "stop looking" rather than a RecursionError, consistent with this
    file's "never blocks on its own failure" rule)."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or depth <= 0:
        return False
    verb = _normalize_verb(stripped[0])
    if verb in ("bash", "sh", "zsh") and "-c" in stripped:
        idx = stripped.index("-c")
        if idx + 1 < len(stripped):
            return _settings_destructive(stripped[idx + 1], depth - 1)
    if verb == "eval":
        rest = " ".join(stripped[1:])
        return _settings_destructive(rest, depth - 1)
    return False


def _settings_destructive(cmd, depth=6):
    if depth <= 0:
        return False
    for line in cmd.split("\n"):
        if not line.strip():
            continue
        try:
            # round 5, item 1: extract $(...) / `...` BEFORE tokenizing --
            # see _extract_substitutions's docstring for why (a bare `(`/`)`
            # from an un-extracted $(...) gets misread as a subshell-group
            # boundary by _split_pipelines, silently separating the verb
            # from the path it operates on).
            tokens = _tokenize_line(_extract_substitutions(line))
        except ValueError:
            # unbalanced quote etc -- fall back to the step-10/11 regex for
            # just this line, failing safe toward asking.
            if _settings_destructive_fallback(line):
                return True
            continue
        for pipeline in _split_pipelines(tokens):
            for idx, cmd_tokens in enumerate(pipeline):
                if not cmd_tokens:
                    continue
                if _cmd_is_destructive_on_settings(cmd_tokens):
                    return True
                if _find_is_destructive_on_settings(cmd_tokens):
                    return True
                if _bash_c_is_destructive(cmd_tokens, depth):
                    return True
                stripped = _strip_wrappers(cmd_tokens)
                if stripped and _normalize_verb(stripped[0]) == "xargs":
                    # item (d): xargs's own verb list, scoped to an EARLIER
                    # simple command in the SAME pipeline (joined only by
                    # `|`) -- not the whole line, which was step 11's
                    # disclosed NOTE-grade gap.
                    if any(
                        _normalize_verb(t) in _DESTRUCTIVE_VERBS
                        for t in stripped[1:]
                    ):
                        for earlier in pipeline[:idx]:
                            if any(_is_settings_operand(t) for t in earlier):
                                return True
                            if any(
                                t.strip("\"'")
                                in ("settings.json", "settings.local.json")
                                for t in earlier
                            ):
                                return True
    return False


# guard-fixes step 13 (hub-approved follow-up on step 12): the hub found
# the SAME quoting-blindness bug class in a sibling function -- item 1's
# SETTINGS_BASH_WRITE_RE (the settings-WRITE check, present since before
# step 10, unrelated in intent to 4c's destructive-verb check above but
# built the same text-position way). Its cp/mv-DESTINATION, `tee`, and
# `sed -i` branches all matched the verb ANYWHERE in the text before the
# path, so `grep -E "rm|mv" ~/.claude/settings.json`, `grep -n
# "Bash(rm|mv" ...`, `grep "tee" ...`, and `grep "sed -i" ...` all asked --
# the disclosed, out-of-scope gap from step 12's report, now closed by
# moving those three branches onto the SAME tokenizer walk `_settings_
# destructive` already uses (reusing its helpers directly: _tokenize_line,
# _split_pipelines, _strip_wrappers, _normalize_verb, _is_settings_operand
# -- the whole 4c-plus-write family now shares one quote-aware parser).
#
# What stays plain text regex, unchanged, on purpose: the `>`/`>>` redirect
# branch and the three interpreter-string-shaped branches (python
# open(path,'w'/'a'/'x'), os.replace(...), json.dump(...), writeFileSync(
# ...)) -- these either aren't shell command syntax at all (they live
# inside a quoted -c/-m string, or are Node/Python source text handed to
# python3/node), or are inherently positional in a way a shell tokenizer
# doesn't model (a redirect target isn't an "operand" of anything).
SETTINGS_TEXT_WRITE_RE = re.compile(
    r">>?\s*[\"']?" + SETTINGS_PATH + r"|"                          # redirect target
    r"open\(\s*[\"']" + SETTINGS_PATH + r"[\"']\s*,\s*[\"'][wax]|"  # python open(path,'w'/'a'/'x')
    r"os\.replace\([^)]*" + SETTINGS_PATH + r"|"
    r"json\.dump\([^)]*" + SETTINGS_PATH + r"|"
    r"writeFileSync\(\s*[\"']" + SETTINGS_PATH + r"[\"']"
)

# Redirect operators are punctuation tokens too (shlex's default
# punctuation_chars set includes `<`/`>`), but `_split_pipelines` doesn't
# treat them as command separators (a redirect isn't a new command), so
# they -- and the token right after them, their target -- land inside the
# same simple command's token list. That target is not a real "argument"
# to cp/mv/tee/sed in the sense these checks care about (it's already
# covered by the kept SETTINGS_TEXT_WRITE_RE redirect branch when it's an
# output target, and is actively the WRONG thing to treat as a write
# target when it's an input source, e.g. `tee /tmp/x < ~/.claude/
# settings.json`). Stripped out before any operand check below.
_REDIRECT_OPS = {"<", ">", ">>", "<<", "<<<", "<>", "&>", "&>>"}


def _strip_redirect_targets(tokens):
    out = []
    i, n = 0, len(tokens)
    while i < n:
        if tokens[i] in _REDIRECT_OPS:
            i += 2  # skip the operator and its target
            continue
        out.append(tokens[i])
        i += 1
    return out


_DEST_LAST_ARG_VERBS = ("cp", "mv", "ln", "install")


def _cp_mv_write_target(cmd_tokens):
    """cp/mv/ln/install DESTINATION: first word is one of those, LAST
    non-flag operand is a settings operand. `-t <dir>`/`--target-directory`
    (which move the destination to a different position) are out of scope,
    per the brief, as a rare shape not worth the extra complexity here.
    `ln`/`install` added round 5, item 1 (conservative-rule verb list) --
    `ln -sf /tmp/evil ~/.claude/settings.json` replaces settings.json with
    a symlink the same way a `cp`/`mv` destination would overwrite it, and
    `install` is the same DESTINATION shape as `cp`."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or _normalize_verb(stripped[0]) not in _DEST_LAST_ARG_VERBS:
        return False
    operands = [
        t for t in _strip_redirect_targets(stripped[1:]) if not t.startswith("-")
    ]
    if not operands:
        return False
    return _is_settings_operand(operands[-1])


def _tee_write_target(cmd_tokens):
    """tee: first word is tee, ANY non-flag operand is a settings operand
    (tee can write to several files at once; `-a` doesn't change that)."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or _normalize_verb(stripped[0]) != "tee":
        return False
    operands = [
        t for t in _strip_redirect_targets(stripped[1:]) if not t.startswith("-")
    ]
    return any(_is_settings_operand(t) for t in operands)


_INPLACE_EDIT_VERBS = ("sed", "perl")


def _sed_inplace_write_target(cmd_tokens):
    """sed/perl -i: first word is sed or perl, a token is exactly `-i`,
    starts with `-i` (the attached-suffix form, `-i.bak`), or is
    `--in-place`, AND any later token is a settings operand. `perl` added
    round 5, item 1 (conservative-rule verb list) -- `perl -i -pe '...'
    ~/.claude/settings.json` is the same in-place-rewrite shape as
    `sed -i`. `python` is deliberately NOT in this set: python's `-i` flag
    means interactive mode, not in-place edit, so adding it would ask on
    every ordinary `python3 -i` REPL invocation."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or _normalize_verb(stripped[0]) not in _INPLACE_EDIT_VERBS:
        return False
    rest = _strip_redirect_targets(stripped[1:])
    has_inplace = any(t.startswith("-i") or t == "--in-place" for t in rest)
    if not has_inplace:
        return False
    return any(_is_settings_operand(t) for t in rest)


def _dd_write_target(cmd_tokens):
    """dd of=<path>: first word is dd, an `of=...` token's value is a
    settings operand. Round 5, item 1 (conservative-rule verb list)."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or _normalize_verb(stripped[0]) != "dd":
        return False
    for t in stripped[1:]:
        if t.startswith("of="):
            return _is_settings_operand(t[3:])
    return False


def _redirect_write_target(cmd_tokens):
    """`>`/`>>` immediately followed by a settings operand -- a tokenizer-
    based backstop alongside the plain-text SETTINGS_TEXT_WRITE_RE redirect
    branch, so a substitution/brace-expansion-wrapped redirect target
    (`echo x > $(echo ~/.claude/settings.json)`) is caught the same way a
    literal one already is. `<`/`<<`/`<<<` (read redirects) are
    deliberately excluded -- reading INTO a variable/heredoc from the
    settings path is not a write."""
    for i, t in enumerate(cmd_tokens):
        if t in (">", ">>") and i + 1 < len(cmd_tokens):
            if _is_settings_operand(cmd_tokens[i + 1]):
                return True
    return False


def _bash_c_is_write_destination(cmd_tokens, depth):
    """Same bash/sh/zsh -c / eval recursion pattern as
    _bash_c_is_destructive, for the write-destination check instead."""
    stripped = _strip_wrappers(cmd_tokens)
    if not stripped or depth <= 0:
        return False
    verb = _normalize_verb(stripped[0])
    if verb in ("bash", "sh", "zsh") and "-c" in stripped:
        idx = stripped.index("-c")
        if idx + 1 < len(stripped):
            return _settings_write_destination(stripped[idx + 1], depth - 1)
    if verb == "eval":
        rest = " ".join(stripped[1:])
        return _settings_write_destination(rest, depth - 1)
    return False


def _settings_write_destination(cmd, depth=6):
    """guard-fixes step 13: item 1's write-target check, split into the
    part that stays plain text regex (SETTINGS_TEXT_WRITE_RE -- redirects
    and interpreter-string shapes) and the part now on the tokenizer
    (cp/mv, tee, sed -i). On a shlex ValueError for a line, falls back to
    the ORIGINAL, full SETTINGS_BASH_WRITE_RE for just that line -- same
    fail-safe-toward-asking pattern as _settings_destructive's fallback."""
    if depth <= 0:
        return False
    if SETTINGS_TEXT_WRITE_RE.search(cmd):
        return True
    for line in cmd.split("\n"):
        if not line.strip():
            continue
        try:
            # round 5, item 1: same $(...) / `...` extraction as
            # _settings_destructive, and for the same reason -- see
            # _extract_substitutions's docstring.
            tokens = _tokenize_line(_extract_substitutions(line))
        except ValueError:
            if SETTINGS_BASH_WRITE_RE.search(line):
                return True
            continue
        for pipeline in _split_pipelines(tokens):
            for cmd_tokens in pipeline:
                if not cmd_tokens:
                    continue
                if _cp_mv_write_target(cmd_tokens):
                    return True
                if _tee_write_target(cmd_tokens):
                    return True
                if _sed_inplace_write_target(cmd_tokens):
                    return True
                if _dd_write_target(cmd_tokens):
                    return True
                if _redirect_write_target(cmd_tokens):
                    return True
                if _bash_c_is_write_destination(cmd_tokens, depth):
                    return True
    return False


# the operator, 2026-09-23, verbatim: "Several chats have still been asking me to
# approve commands. make sure this doesn't happen again. they are all
# approved forever. period." No hook may pop a permission prompt for him.
# The cases that used to _ask() here (settings.json writes/moves, secret
# reads, malformed tool_input) all still needed a stop -- so they became a
# BLOCK instead of a question. Blocking tightens the guard, it never
# loosens it: the call is refused with a reason and a concrete alternative,
# never left waiting on a tappable "ask".
def _refuse(reason):
    print("Blocked (safety guard): " + reason, file=sys.stderr)
    sys.exit(2)

# --- Bash reads that print secret files (2026-09-16 guard-fixes item 2) ---
# The Bash branch never checked file CONTENT being read at all — only the
# Write/Edit branch had SECRET/CREDENTIAL checks. "Secrets never appear in a
# chat reply" had zero mechanical backstop for a Bash cat/head/grep/python-
# open of the vault or a .env file. Ask (not block — reading his own vault to
# help him log into something is sometimes legitimate; hard-blocking would
# just make the agent tell him to look it up himself, worse UX for the same
# outcome since he sees it either way).
#
# Hub audit amendment A2 (2026-09-16): the original SECRET_FILE left the
# boundary before .env/.dev.vars/.envrc fully optional, so the literal
# substring ".env" inside "process.env" satisfied it — `grep -rn
# "process.env" src/` would have asked in every web project. Require a real
# path boundary (start of string, whitespace, quote, "=", or "/") before the
# filename, and treat .env.example/.env.sample/.env.template as public
# fixtures, not secrets.
SECRET_FILE = re.compile(
    r"(?:^|[\s\"'=/])(?:"
    r"(?:~|" + "/Users" + r"/[\w.-]+)?/?\.claude/hub/manual/00-credentials\.md"
    r"|\.env(?!\.(?:example|sample|template)\b)(?:\.[\w-]+)?"
    r"|\.dev\.vars"
    r"|\.envrc"
    r")\b"
)
SECRET_READ_VERBS = re.compile(
    r"\b(cat|head|tail|less|more|bat|od|xxd|strings|base64|cp|nl)\b[^\n]*" + SECRET_FILE.pattern,
    re.I,
)
SECRET_GREP = re.compile(r"\bgrep\b[^\n]*" + SECRET_FILE.pattern, re.I)
SECRET_PY_OPEN = re.compile(
    r"(?:python[\d.]*|node)\b[^\n]*open\(\s*['\"][^'\"]*(?:00-credentials\.md|\.env|\.dev\.vars)['\"]",
    re.I,
)
SECRET_VAR_INTERP = re.compile(
    r"\becho\b[^\n]*\$\{?[A-Za-z_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Za-z_]*\}?",
    re.I,
)
# A2, tightened by bug-gate 4b (2026-09-16 guard-fixes step 10): "copying
# between env files is not exposure" — a fresh worktree build copying
# .env.local in is the named failure mode to keep working. The original
# version only checked the cp/mv DESTINATION and exempted the whole command
# string; both were the bug. Now: the command is split into shell segments
# first (SEGMENT_SPLIT), and a SEGMENT is exempt only when it is a cp/mv
# whose EVERY non-flag operand — every source AND the destination — is
# itself env-shaped. "Regardless of source" is gone: a real secret file (the
# vault, or any non-env file) disguised only by an env-shaped destination
# name no longer qualifies, and a non-exempt segment chained onto an exempt
# one is checked on its own.
SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|\n]")
ENV_SHAPED_OPERAND = re.compile(
    r"^(?:\S*/)?(?:\.env(?:\.[\w-]+)?|\.dev\.vars|\.envrc)$"
)


def _is_env_to_env_segment(segment):
    """True only when `segment` is a cp/mv command and every operand (all
    sources and the destination) is env-shaped and not the vault. A single
    non-env operand — e.g. the credentials vault renamed to look like a
    destination .env file — disqualifies the whole segment."""
    m = re.match(r"\s*(?:sudo\s+)?(?:cp|mv)\b(.*)$", segment, re.S)
    if not m:
        return False
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        return False
    operands = [t for t in tokens if not t.startswith("-")]
    if len(operands) < 2:
        return False
    return all(
        ENV_SHAPED_OPERAND.match(op) and "00-credentials.md" not in op
        for op in operands
    )

# Where a real secret is allowed to live.
SECRET_SAFE_PATH = (".env", ".dev.vars", ".envrc")

# Paths where a credential literal is especially dangerous: anything that gets
# committed, published, or read back into an AI context later.
def _high_risk(path: str) -> bool:
    p = path.lower()
    return (
        "/memory/" in p
        or "/hub/" in p
        or p.endswith((".md", ".mjs", ".js", ".ts", ".jsx", ".tsx", ".py", ".sh", ".toml", ".json", ".yml", ".yaml", ".ipynb"))
    )

# --- chat-kill guard (the operator, 2026-09-06) -----------------------------------
# A chat killed from bash dies with no goodbye: the server side reports
# "Session failed: Process exited with error cse_...", and — the part that costs
# something — the chat never runs its own closing rites. On the day this was
# written the ledger read 20 MISS / 4 ok: a 32MB session's whole day of work
# ended with no log at all, because the headless auto-log leg cannot write to
# the memory dir ([[feedback_logging_receipt_not_promise]]).
#
# So a kill aimed at a CHAT process is blocked unless the caller proves the log
# already landed. Proof is a path on disk, not a claim
# ([[feedback_verify_rites_on_disk]]):
#     RITES-DONE:/full/path/to/the/log.md   — file must exist, mtime < 6h
#     HUNG-CHAT-OVERRIDE                    — the chat is unreachable
# Kills aimed at anything else (chrome, vite, headless -p workers, subagents)
# are untouched. Fails open on any error, like the rest of this guard.
KILL_CMD = re.compile(r"(?:^|[;&|]|\n)\s*(?:sudo\s+)?(kill|pkill|killall)\b([^;&|\n]*)")
KILL_ANY = re.compile(r"(?:^|[;&|]|\n)\s*(?:sudo\s+)?(?:kill|pkill|killall)\b")
CHAT_PROC = re.compile(r"code/sessions/cse_|--remote-control")
CLAUDE_BIN = re.compile(r"(^|/)claude(\s|$)")
# `claude <subcommand>` is CLI plumbing, not a chat someone is reading.
CLAUDE_SUBCMD = re.compile(
    r"(^|/)claude\s+(agents|mcp|config|doctor|update|install|plugin|rc|api|"
    r"migrate-installer|setup-token|help|--version|--help|-v|-h)\b")
KILL_LOG = os.path.expanduser("~/.claude/hub/chat-kill-guard.log")
# 2026-09-16 guard-fixes item 7: the outer except below silently swallowed
# every unplanned exception (a regression here means this guard protects
# nothing, invisibly). This is a pure observability addition — same
# fail-open behavior, but now leaves a trace instead of vanishing.
FAILOPEN_LOG = os.path.expanduser("~/.claude/hub/safety-guard.log")


def _log_failopen(e):
    try:
        os.makedirs(os.path.dirname(FAILOPEN_LOG), exist_ok=True)
        with open(FAILOPEN_LOG, "a") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                      + " FAIL-OPEN error=%s: %s\n" % (type(e).__name__, e))
    except Exception:
        pass


# guard-fixes round 5, item 2 (L-0430, bug-gate round 4): tool_input.command
# (Bash) or tool_input.file_path/notebook_path (Write/Edit/NotebookEdit)
# arriving as a list/dict/int -- instead of the str the harness always sends
# -- raised deep inside the regex/tokenizer matching logic. The outer
# `except Exception: _log_failopen(e)` at the bottom of this file swallowed
# that, and swallowing it there means NOTHING printed: the harness reads a
# silent, no-output exit as an unconditional ALLOW. Every check in this file
# -- CATASTROPHIC, ClickFix, settings-write, settings-destructive,
# secret-read, kill-guard, Write/Edit SECRET/CREDENTIAL -- was skipped for
# that one call. Two layers, neither of which can fail open again:
#   1. _coerce_str -- every field this file inspects gets turned into a
#      plain str (json.dumps for list/dict) before any regex/tokenizer ever
#      sees it, so a weird-but-not-adversarial shape (e.g. a null) still
#      gets checked instead of raising.
#   2. _unexpected_type -- a Bash command or a Write/Edit/NotebookEdit path
#      that is not a plain string (and not merely absent) is itself a shape
#      no legitimate tool call produces. Ask immediately, before the
#      coerced text is even matched -- belt and suspenders: even if the
#      json.dumps'd text somehow slipped past every pattern below, the type
#      check alone still stops a silent allow.
def _coerce_str(val):
    if isinstance(val, str):
        return val
    if val is None:
        return ""
    try:
        return json.dumps(val)
    except Exception:
        return str(val)


def _unexpected_type(val):
    return val is not None and not isinstance(val, str)


def _cmdline(pid):
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                              capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def _is_chat(cl):
    """A chat owns a transcript a human reads. A headless -p worker does not."""
    if not cl:
        return False
    if CHAT_PROC.search(cl):
        return True
    if CLAUDE_SUBCMD.search(cl):
        return False
    if CLAUDE_BIN.search(cl) and "--print" not in cl and " -p " not in cl:
        return True
    return False


def _chat_targets(cmd):
    """PIDs named by this command that are live chat processes."""
    found = []
    for verb, args in KILL_CMD.findall(cmd):
        pids = []
        if verb == "kill":
            pids = re.findall(r"(?<![\w.-])(\d{2,7})(?![\w.-])", args)
            if ("$(" in args or "`" in args) and "claude" in args.lower():
                found.append(("<substitution over claude processes>", args.strip()[:80]))
        else:
            pat = re.sub(r"(^|\s)-\S+", " ", args).strip().strip("\"'")
            if pat:
                try:
                    flag = "-x" if verb == "killall" and "-f" not in args else "-f"
                    pids = subprocess.run(["pgrep", flag, pat], capture_output=True,
                                          text=True, timeout=3).stdout.split()
                except Exception:
                    pids = []
        for pid in pids:
            cl = _cmdline(pid)
            if _is_chat(cl):
                found.append((pid, cl[:120]))
    return found


# 2026-09-16 guard-fixes item 5: the audit reproduced the exact bypass live
# — a `touch`'d file anywhere, containing one throwaway comment line,
# satisfied the old os.path.isfile()+mtime check completely. The bypass
# syntax (RITES-DONE:<path>) is disclosed in this guard's own block message,
# so anyone who trips it once learns exactly what to fabricate. Three layers,
# since the strongest one (layer 3, target-id binding) can't reach every
# case — see the honest limitation in its docstring below.
def _rites_allowed(path):
    """Layer 1, fixed for bug-gate 4a (2026-09-16 guard-fixes step 10): the
    original was `RITES_ALLOWED.search(suffix)` — an end-anchored regex that
    only checked the tail of the path string, so any tree anywhere writable
    that happened to END in `/.claude/hub/reports/<name>.md` (or the other
    three allowed suffixes) qualified, real home directory or not. This
    resolves both sides with os.path.realpath (symlinks and `..` included)
    and accepts the proof path only if it IS, after resolution, one of the
    three fixed rites files under the real $HOME/.claude/hub, or a direct
    child of $HOME/.claude/hub/reports ending in .md — never a lookalike
    tree elsewhere, a `..` traversal out of reports, a symlink inside
    reports pointing outside it, or a nested subdirectory under reports.
    Computed from os.path.expanduser("~") at call time, not a module-level
    constant, so a test pointing HOME at a scratch dir gets its own
    allowlist, not the real machine's."""
    home = os.path.realpath(os.path.expanduser("~"))
    hub = os.path.join(home, ".claude", "hub")
    real = os.path.realpath(path)
    fixed = {
        os.path.realpath(os.path.join(hub, "board.md")),
        os.path.realpath(os.path.join(hub, "board-archive.md")),
        os.path.realpath(os.path.join(hub, "delegation-alarms.log")),
    }
    if real in fixed:
        return True
    reports_dir = os.path.realpath(os.path.join(hub, "reports"))
    return os.path.dirname(real) == reports_dir and real.endswith(".md")


# Hub audit amendment A5: "DEAD" in content.upper() is satisfied by
# "deadline" / "dead code" — require the EXACT closing signal CLAUDE.md
# names ("signals ❌❌❌ DEAD when closing").
RITES_MARKER = "❌❌❌ DEAD"
SESSION_ID_RE = re.compile(r"code/sessions/cse_([A-Za-z0-9_-]+)")


def _rites_proof(cmd, targets):
    """RITES-DONE:<path> where the file resolves under an ALLOWED rites
    location, is fresh, and contains the real closing marker — and, where a
    target's cmdline carries an extractable session id, that id too.

    Honest limitation (disclosed, not hidden): layer 3 (id binding) only
    works for the IDE/remote-control chat shape (`code/sessions/cse_...` in
    the cmdline). A plain terminal `claude` process's cmdline carries no
    session id at all, so for that shape layers 1+2+4 are the only
    protection — "can no longer be forged with a one-line touch anywhere",
    not unforgeable in the absolute sense.
    """
    m = re.search(r"RITES-DONE:\s*(\S+)", cmd)
    if not m:
        return None
    path = os.path.expanduser(m.group(1).strip("\"',;"))
    try:
        # layer 1: path allowlist — a throwaway file anywhere else no longer
        # qualifies, regardless of content.
        if not _rites_allowed(path):
            return False
        # layer 4: existence + freshness (unchanged from the original check).
        if not (os.path.isfile(path) and (time.time() - os.path.getmtime(path)) < 6 * 3600):
            return False
        # layer 2: a real closing signal, not just any content.
        with open(path, "r", errors="ignore") as fh:
            content = fh.read()
        if RITES_MARKER not in content:
            return False
        # layer 3: best-effort target binding, where the cmdline allows it.
        for _pid, cl in targets:
            sid = SESSION_ID_RE.search(cl or "")
            if sid and sid.group(1) not in content:
                return False
        return path
    except Exception:
        pass
    return False


def _worktree_guard_test(path):
    """True only for hooks/test-safety-guard.sh at the top of a real git worktree
    of ~/.claude, i.e. ~/.claude-worktrees/<name>/hooks/test-safety-guard.sh whose
    <name>/.git file points back into ~/.claude/.git/worktrees/. A branch copy of the
    suite needs the same fixture exemption as the live one (2026-09-23, L-0885: the
    live guard refused adding tests to the worktree copy). Both sides go through
    realpath, so a symlink or '..' out of the worktrees dir never qualifies, and a
    lookalike tree elsewhere fails the prefix and the .git back-pointer."""
    try:
        home = os.path.realpath(os.path.expanduser("~"))
        wt_root = os.path.join(home, ".claude-worktrees") + os.sep
        real = os.path.realpath(os.path.expanduser(path))
        if not real.startswith(wt_root):
            return False
        parts = real[len(wt_root):].split(os.sep)
        if parts[1:] != ["hooks", "test-safety-guard.sh"] or not parts[0]:
            return False
        with open(os.path.join(wt_root, parts[0], ".git")) as fh:
            gitdir = fh.read().strip()
        want = os.path.join(os.path.realpath(os.path.join(home, ".claude", ".git")), "worktrees") + os.sep
        return gitdir.startswith("gitdir:") and os.path.realpath(gitdir[7:].strip()).startswith(want)
    except Exception:
        return False


def _note(line):
    try:
        with open(KILL_LOG, "a") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + line + "\n")
    except Exception:
        pass


try:
    data = json.load(sys.stdin)
    name = data.get("tool_name", "")
    tin = data.get("tool_input") or {}
    if name == "Bash":
        cmd_raw = tin.get("command", "")
        if _unexpected_type(cmd_raw):
            _refuse(
                "tool_input.command is not a plain string (got %s) — this "
                "guard cannot safely pattern-match a non-string command, so "
                "it refuses instead of silently skipping every check "
                "(CATASTROPHIC/ClickFix/settings/secret-read/kill-guard). "
                "Malformed tool input refused — fix the caller to send a "
                "plain string and retry." % type(cmd_raw).__name__
            )
        cmd = _coerce_str(cmd_raw)
        # guard-fixes round 5, item 3 (L-0405): a `bash -c "rm -rf ~"` /
        # `sh -c '...'` / `eval "rm -rf ~"` wrapper puts a closing quote
        # character immediately after the target ("~", "/", "$HOME"), which
        # broke both CATASTROPHIC alternatives -- the end-anchored one
        # needs `\s*$` right after the target (a `"` isn't whitespace-or-
        # end), and the other needs a literal `\s` right after it (same
        # problem). Running the same patterns again against a copy with
        # every quote character stripped closes that gap: `rm -rf ~"`
        # becomes `rm -rf ~` with nothing after it, which the end-anchored
        # alternative matches. Accepted tradeoff, same as the rest of this
        # file's "ask/block costs less than a miss" stance for the
        # CATASTROPHIC class specifically: quoted PROSE that happens to
        # contain "rm -rf ~" right at a quote boundary could now also match
        # -- rare in practice (the existing settings.json grep/awk noask
        # tests below keep passing because their targets are never a bare
        # `~`/`/`/`$HOME`), and a hard block is always escapable with the
        # `!` prefix by the operator himself.
        cmd_dequoted = cmd.replace('"', "").replace("'", "")
        for pat in CATASTROPHIC:
            if re.search(pat, cmd) or re.search(pat, cmd_dequoted):
                print(
                    "Blocked (safety guard): command matches a catastrophic pattern "
                    "(destructive rm at a root path / force-push to main / chmod 777 /). "
                    "If genuinely intended, the operator runs it by hand with the `!` prefix.",
                    file=sys.stderr,
                )
                sys.exit(2)

        # ClickFix class: downloaded code executed without anyone reading it.
        for pat, why in REMOTE_EXEC:
            if re.search(pat, cmd, re.IGNORECASE):
                print(
                    "Blocked (ClickFix guard): this command is " + why + ".\n"
                    "Code that is downloaded and executed in one step is never read by "
                    "anyone before it runs. That is the whole mechanism behind ClickFix, "
                    "and the same shape works on an AI with a terminal: a page, an email, "
                    "a README or a tool result says 'run this to continue', and the agent "
                    "obliges.\n"
                    "There is no override flag on this guard on purpose — if the caller was "
                    "deceived, the override would be the attacker's too.\n"
                    "Do it in two steps instead:\n"
                    "  curl -fsSL <url> -o /tmp/x.sh   # fetch\n"
                    "  less /tmp/x.sh                  # READ it, report what it does\n"
                    "  sh /tmp/x.sh                    # then run it\n"
                    "Or the operator runs it himself with the `!` prefix.",
                    file=sys.stderr,
                )
                sys.exit(2)

        # settings.json / settings.local.json write-target check (item 1,
        # tokenizer-based since guard-fixes step 13 -- see
        # _settings_write_destination for the redirect/interpreter-string
        # branches kept as regex vs. the cp/mv/tee/sed-i branches now on
        # the shared tokenizer).
        if _settings_write_destination(cmd):
            _refuse(
                "this command writes to ~/.claude/settings.json or "
                "settings.local.json — CLAUDE.md says changes wait for a "
                "fresh session or the operator's explicit go, not a hot-edit "
                "mid-session. the operator applies settings changes himself; write "
                "the exact change into a hub/READY-FOR-OPERATOR-*.md note and "
                "tell the hub."
            )

        # settings.json / settings.local.json move/delete check (bug-gate 4c).
        if _settings_destructive(cmd):
            _refuse(
                "this command moves, deletes, or truncates ~/.claude/settings.json "
                "or settings.local.json — CLAUDE.md says changes wait for a "
                "fresh session or the operator's explicit go, not a hot-edit "
                "mid-session. the operator applies settings changes himself; write "
                "the exact change into a hub/READY-FOR-OPERATOR-*.md note and "
                "tell the hub."
            )

        # secret-read check (item 2) — after settings-write, before kill-guard.
        # 2026-09-16 guard-fixes step 10, bug-gate 4b: the old ENV_TO_ENV_COPY
        # exemption inspected only the DESTINATION shape and exempted the
        # WHOLE command string, so (i) `cp <vault> /tmp/x/.env` slipped
        # through disguised only by the destination's name, and (ii) because
        # the regex matches anywhere in the string, a chained
        # `cp .env.local .env; cat <vault>` had its exemption swallow the
        # unrelated, non-exempt `cat` segment too. Fix: split on shell
        # separators first, decide exemption PER SEGMENT (every operand —
        # every source and the destination — must be env-shaped, not just
        # the last one), and run the secret-read check on every non-exempt
        # segment independently.
        for segment in SEGMENT_SPLIT.split(cmd):
            if _is_env_to_env_segment(segment):
                continue
            if (
                SECRET_READ_VERBS.search(segment)
                or SECRET_GREP.search(segment)
                or SECRET_PY_OPEN.search(segment)
                or SECRET_VAR_INTERP.search(segment)
            ):
                _refuse(
                    "this command reads/prints a secrets file or a credential-shaped "
                    "variable. Never print secrets; use the hash-only / in-process "
                    "comparison pattern instead "
                    "(feedback_secret_files_hash_only_from_the_first_command.md)."
                )

        # A chat must not die by bash kill — it dies through the front door,
        # after its rites land. See the chat-kill guard block above.
        if KILL_ANY.search(cmd):
            targets = _chat_targets(cmd)
            if targets:
                if "HUNG-CHAT-OVERRIDE" in cmd:
                    _note("override(hung) targets=%s" % [t[0] for t in targets])
                else:
                    proof = _rites_proof(cmd, targets)
                    if proof:
                        _note("allowed rites=%s targets=%s" % (proof, [t[0] for t in targets]))
                    else:
                        _note("BLOCKED targets=%s" % targets)
                        why = ("the RITES-DONE path does not exist on disk, or is older than 6h"
                               if proof is False else "no proof of closing rites was given")
                        print(
                            "Blocked (chat-kill guard): this kill targets a live CHAT, not a worker.\n"
                            "  " + "\n  ".join("%s  %s" % t for t in targets) + "\n"
                            "A chat killed from bash dies with no goodbye: the server reports "
                            "'Session failed: Process exited with error cse_...' and the chat never "
                            "writes its closing log. On 2026-09-06 that cost 20 unlogged sessions "
                            "against 4 logged, including a 32MB day of work.\n"
                            "Right order: rites first (log + board + verdict), then the DEAD stamp, "
                            "then let it die through the front door — closed from the phone/web for "
                            "a cloud chat, /clear or exit for a terminal one.\n"
                            "Blocked because " + why + ". To proceed, either:\n"
                            "  - add RITES-DONE:/full/path/to/the/log.md (must exist, written in the last 6h), or\n"
                            "  - add HUNG-CHAT-OVERRIDE if the chat is hung and cannot close itself.",
                            file=sys.stderr,
                        )
                        sys.exit(2)
    elif name in ("Write", "Edit", "NotebookEdit"):
        # item 3: NotebookEdit gets the SAME settings-path + SECRET/CREDENTIAL
        # checks as Write/Edit, reading its own field names.
        #
        # guard-fixes round 5, item 2 (L-0430): same non-string fail-open as
        # the Bash branch above, mirrored here for file_path/notebook_path
        # (and content/new_source/new_string, coerced but not type-gated --
        # a weird content shape isn't itself suspicious the way a weird PATH
        # is, so it's just coerced to text and still scanned for secrets).
        if name == "NotebookEdit":
            path_raw = tin.get("notebook_path", "")
            if _unexpected_type(path_raw):
                _refuse(
                    "tool_input.notebook_path is not a plain string (got %s) "
                    "— this guard cannot safely check a non-string path, so "
                    "it refuses instead of silently skipping every check. "
                    "Malformed tool input refused — fix the caller to send a "
                    "plain string and retry." % type(path_raw).__name__
                )
            content = _coerce_str(tin.get("new_source", ""))
            path = _coerce_str(path_raw)
        else:
            path_raw = tin.get("file_path", "")
            if _unexpected_type(path_raw):
                _refuse(
                    "tool_input.file_path is not a plain string (got %s) — "
                    "this guard cannot safely check a non-string path, so it "
                    "refuses instead of silently skipping every check. "
                    "Malformed tool input refused — fix the caller to send a "
                    "plain string and retry." % type(path_raw).__name__
                )
            content = _coerce_str(tin.get("content", "")) + _coerce_str(tin.get("new_string", ""))
            path = _coerce_str(path_raw)
        exempt = (
            "/scratchpad" in path
            or path.endswith((".dev.vars", ".env"))
            or "/.private-archives/" in path
            # This guard's own test suite. A security test necessarily contains
            # secret-SHAPED fixtures — that is the whole point of it — so the
            # guard would otherwise make itself untestable. Exactly one path,
            # spelled out in full, rather than a marker comment any file could
            # claim. The fixtures inside it are fabricated, never live values.
            or path.endswith("/.claude/hooks/test-safety-guard.sh")
            # The same suite inside a real git worktree of ~/.claude (see
            # _worktree_guard_test: realpath-anchored, .git back-pointer checked).
            or _worktree_guard_test(path)
            # The designated credentials vault (the operator, 2026-08-11): gitignored,
            # under hub/manual/, and its own header states it is "the ONLY place
            # these values are written down." Writing a real credential here is
            # the file doing its job, not a leak. Exactly one path, spelled out
            # in full, same pattern as the test-suite exemption above.
            or path.endswith("/.claude/hub/manual/00-credentials.md")
        )
        # item 1: settings.json/settings.local.json — computed here, but the
        # ask is emitted AFTER the existing SECRET/CREDENTIAL checks below so
        # a write that is BOTH targeting settings.json AND secret-shaped still
        # gets the stronger exit-2 hard block, not merely downgraded to ask.
        settings_ask = SETTINGS_PATH_RE.search(path)
        if not exempt and SECRET.search(content):
            print(
                "Blocked (safety guard): secret-shaped string headed into a file. "
                "Secrets live only in untracked .env/.dev.vars or the platform secret "
                "store — never in tracked files, logs, or memory.",
                file=sys.stderr,
            )
            sys.exit(2)

        # Credential literal next to a credential-shaped identifier.
        if not path.endswith(SECRET_SAFE_PATH) and not exempt and _high_risk(path):
            hits = [m.group("val") for m in CREDENTIAL_LINE.finditer(content)
                    if not PLACEHOLDER.match(m.group("val"))
                    and not CRED_IDENTIFIER.match(m.group("val"))]
            hits += [m.group("val") for m in CREDENTIAL_LOOSE.finditer(content)
                     if _looks_secret(m.group("val"))]
            for val in hits:
                print(
                    "Blocked (safety guard): a literal value is being written next to a "
                    "credential-shaped name (password/passcode/secret/token/api_key).\n"
                    "This is the exact shape of the three leaks the 2026-07-28 audit found: "
                    "a 2-character passcode hardcoded in a Worker, an admin password that "
                    "shipped in page source, and a live passcode echoed by a deploy script.\n"
                    "Read it from the platform secret store or an untracked .env instead. "
                    "If this really is a placeholder, make it obviously fake "
                    "(CHANGEME, your-key-here, ${VAR}).",
                    file=sys.stderr,
                )
                sys.exit(2)

        if settings_ask:
            _refuse(
                "this writes to ~/.claude/settings.json or settings.local.json — "
                "CLAUDE.md says changes wait for a fresh session or the operator's "
                "explicit go, not a hot-edit mid-session. the operator applies settings "
                "changes himself; write the exact change into a "
                "hub/READY-FOR-OPERATOR-*.md note and tell the hub."
            )
except SystemExit:
    raise
except Exception as e:
    _log_failopen(e)
sys.exit(0)
