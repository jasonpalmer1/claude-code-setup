"""Shared build-shape detector (L-0942, item 3).

Two entry points, for two different kinds of text:

  BUILD_SHAPE_RE  -- a plain regex, matched as a SUBSTRING search. Used by
                      model-guard.py against an Agent/Task tool's free-text
                      `prompt` (natural language, not a shell command line --
                      there is no command-word/segment structure to
                      tokenize, so a substring search is the right tool
                      there and this regex is UNCHANGED from its original
                      model-guard.py definition, see below).

  bash_build_match(cmd) -- token-aware detector for a REAL shell command
                      LINE (safety-guard.py's Bash-tool guard). Added
                      2026-09-23 after bug-gate (L-0942-a-buggate.md) caught
                      BUILD_SHAPE_RE being substring-matched against raw,
                      un-tokenized Bash command text: `grep -r "npm run
                      build" .`, `git commit -m "npm run build"`, and
                      `echo next build` all hard-blocked, and
                      `grep guarded-build README.md; npm run build` was
                      silently ALLOWED (the bare substring "guarded-build"
                      anywhere on the line exempted an unrelated, actually-
                      unguarded build segment next to it) -- the exact
                      failure this ticket exists to close, reproduced by
                      the guard meant to prevent it. bash_build_match()
                      replaces safety-guard.py's use of BUILD_SHAPE_RE
                      entirely; model-guard.py's prompt-text use is
                      untouched.

  needs_shell_wrap(cmd) -- True if a guarded-build rewrite of `cmd` must
                      fold the WHOLE line into a nested `bash -c <quoted>`
                      argument rather than pass it directly as guarded-
                      build's trailing argv. Two cases, both round 3
                      bug-gate findings:
                        1. `cmd` is compound (more than one shell segment,
                           a real &&/||/;/|/& control operator) -- naively
                           prefixing `guarded-build -- ` only wraps the
                           FIRST segment; later segments (e.g. the `npm
                           run build` in `cd app && npm run build`) would
                           still run bare and get blocked again on the
                           very next attempt.
                        2. `cmd` starts with a shell env-assignment prefix
                           (`FOO=1 npm run build`) even with only one
                           segment -- guarded-build execs its trailing
                           argv directly ("$@", no shell in between), and
                           `FOO=1` is only meaningful as an env-assignment
                           prefix to a REAL shell; as a bare argv0 it is
                           just a nonexistent command named "FOO=1".

BUILD_SHAPE_RE was originally defined inline, only inside model-guard.py
(the L-0789 pre-dispatch disk gate). It moved here on 2026-09-23 so no
second copy could drift -- that move was a pure extraction, no shape added
or removed. bash_build_match() is new, additive, on top of that move.
needs_shell_wrap() is new again on top of that (round 3 bug-gate).
"""
import re
import shlex

# ---------------------------------------------------------------------------
# BUILD_SHAPE_RE -- free-text substring search. model-guard.py's use ONLY.
# Do not point safety-guard.py's Bash-command check at this; see
# bash_build_match() below for that.
#
# L-0988 (2026-09-23): this was originally ONE re.compile(...) whose last two
# alternatives had an unbounded `X.*Y` shape:
#
#   webpack.*(?:--mode[=\s]+production|--production\b|\s-p\b)
#   NODE_ENV=production.*webpack
#
# `.search()` tries a match starting at every position in the text. For each
# position where "webpack" (or "NODE_ENV=production") starts, a *greedy*
# `.*` first consumes the ENTIRE rest of the string, then backtracks one
# character at a time looking for the required suffix. When the suffix never
# appears, that backtrack runs all the way out to the end of the remaining
# text -- O(remaining length) -- and it repeats FROM SCRATCH at every later
# occurrence of "webpack"/"NODE_ENV=production" too, so a prompt with many
# such occurrences and no matching suffix anywhere costs O(occurrences x
# remaining length), i.e. quadratic in input size. Measured on the original
# pattern (see hub/reports/L-0988-build.md): 400 webpack-occurrences/163KB
# 0.50s, 800/326KB 2.19s, 1600/653KB 8.97s, 3200/1.3MB 36.56s, and 8.27s on a
# realistic 100KB prompt -- on the path every Agent/Task dispatch takes.
#
# Fix: BUILD_SHAPE_RE is no longer a single re.Pattern -- it's a small
# object exposing the same .search(text) -> Match-or-None interface, so
# model-guard.py's one call site (`BUILD_SHAPE_RE.search(prompt)`, then
# `.group(0)`) needed no change at all. Internally it evaluates 3
# independent, all-linear-time candidates and returns whichever has the
# leftmost start (re.search's own leftmost-wins semantics); no two of the
# three can ever start at the same position, since their literal prefixes
# ("next"/"npm"/"build+strip"/"wrangler"/"deploy.sh"/"pnpm"/"yarn"/"bun"/
# "turbo"/"vite" vs "webpack" vs "NODE_ENV=production") are all disjoint --
# so there is no alternative-order tie-break to reproduce, only leftmost-
# start-wins:
#
#   1. _SIMPLE_ALT_RE -- the original alternatives 1-8, byte-identical,
#      unchanged. None of them contain an unbounded `.*`; a bounded
#      optional group like `(?: run)?` can never cause the multiplicative
#      blowup above, so re's own matching is already linear here.
#
#   2. _webpack_flag_match() replaces the 9th (webpack) alternative.
#
#   3. _node_env_webpack_match() replaces the 10th (NODE_ENV=production)
#      alternative, with X = "NODE_ENV=production" and Y = "webpack".
#
# IMPORTANT: `.` in Python's re does NOT cross a newline unless re.DOTALL is
# set (this pattern never sets it, originally or now), so `X.*Y` can only
# match with X and Y on the SAME LINE -- it is safe to check only the
# FIRST X occurrence PER LINE, not globally: if Y has no valid start in the
# window from X's first-on-that-line occurrence to that line's own newline
# (or end of text), no LATER X occurrence on the SAME line can have one
# either (its window is a strict, smaller subset -- same newline, later
# start). A DIFFERENT line's X occurrence has an independent window and
# must still be checked. See the full proof, including the wrinkle that Y
# itself (via `\s`) CAN cross a newline even though `.*` can't reach past
# one to find X's own suffix, in the comment above _first_line_bounded_
# match() below -- an earlier draft of this fix missed the per-line scoping
# entirely and was caught by the differential test in
# hooks/test-build-shape.py before it shipped.
#
# Both replacements also reproduce the ORIGINAL match's exact span
# (.start()/.end()/.group(0)), not just its true/false verdict: since `.*`
# is greedy, the original backtracks in FROM THE END of its (per-line)
# reach, so once the (always-leftmost) X occurrence is fixed, it reports
# the RIGHTMOST valid Y in that window, not the leftmost. Both helpers
# reproduce that by scanning the window with finditer() (one linear
# forward pass) and keeping the LAST hit, rather than the first.
#
# webpack is only build-shaped here when it looks like a production build
# invocation -- a bare "webpack" (e.g. reading webpack.config.js, `cat
# webpack.config.js`) must not trip the gate. That behaviour (not the
# construct used to get it) is unchanged.
# ---------------------------------------------------------------------------
_SIMPLE_ALT_RE = re.compile(
    r"next build|npm run build|build\+strip|wrangler (pages )?deploy|deploy\.sh"
    r"|(?:pnpm|yarn|bun)(?: run)? build"
    r"|turbo(?: run)? build"
    r"|vite build",
    re.I,
)
_WEBPACK_RE = re.compile(r"webpack", re.I)
_WEBPACK_FLAG_RE = re.compile(r"--mode[=\s]+production|--production\b|\s-p\b", re.I)
_NODE_ENV_PRODUCTION_RE = re.compile(r"NODE_ENV=production", re.I)


class _BuildShapeMatch:
    """Minimal re.Match stand-in. model-guard.py's one call site only ever
    uses .group(0), but .start()/.end() are exposed too so any future
    caller that needs them gets the real, original-equivalent span rather
    than a partial shim silently missing part of the interface."""

    __slots__ = ("_text", "_start", "_end")

    def __init__(self, text, start, end):
        self._text = text
        self._start = start
        self._end = end

    def group(self, n=0):
        if n not in (0, None):
            raise IndexError("no such group")
        return self._text[self._start:self._end]

    def start(self, n=0):
        return self._start

    def end(self, n=0):
        return self._end

    def span(self, n=0):
        return (self._start, self._end)

    def __bool__(self):
        return True


# ---------------------------------------------------------------------------
# IMPORTANT: `.` in Python's re does NOT match "\n" unless re.DOTALL is set
# (BUILD_SHAPE_RE never sets it, originally or now) -- so the original
# `.*` in `webpack.*(...)`/`NODE_ENV=production.*webpack` can NEVER cross a
# newline. An earlier draft of this fix missed that and treated `.*` as if
# it could reach anywhere later in the whole text; a differential run
# against the frozen original (see hooks/test-build-shape.py) caught the
# resulting false positives (e.g. "webpack\n--production\non its own line"
# -- original: no match, that draft: matched) before this shipped. The
# corrected proof below is scoped PER LINE, not per whole remaining text.
#
# Note one more wrinkle: `.*`'s own reach stops at a newline, but the
# alternation that follows it can still ITSELF cross one -- `[=\s]+` and
# bare `\s` both match "\n" (it's an ordinary member of \s), so e.g.
# "webpack--mode\n\nproduction" DOES match (`.*` matches "--mode" with 0
# chars to spare, landing right at the following "\n", and `[=\s]+` then
# eats both newlines before "production"). So the CONSTRAINT is only on
# where the alternation may START (at or before the next newline after X);
# once started, it can consume however far its own pattern allows.
#
# Proof (scoped to one line): for a search of `X.*Y` bounded this way, fix
# one line, i.e. one span between X's occurrence and that line's own
# newline (or end of text) -- call that Y's "window". If Y has no valid
# start anywhere in the window of X's FIRST occurrence on that line, Y
# has no valid start in the window of any LATER X occurrence on the SAME
# line either, because a later occurrence's window is a strict subset (it
# starts further right; both windows end at the same newline). So only
# the first X occurrence per line ever needs checking -- exactly the
# original proof, just re-scoped from "whole remaining text" to "this
# line", which is what `.*`'s own newline exclusion actually bounds.
# ---------------------------------------------------------------------------
_DASH_OR_SPACE_RE = re.compile(r"[-\s]")


def _rightmost_flag_in_window(text, start, window_end):
    """Rightmost start position in [start, window_end] (inclusive) where
    _WEBPACK_FLAG_RE matches, or None. Every alternative in the flag
    pattern begins with either "-" or a whitespace character, so scanning
    only those candidate positions (one linear _DASH_OR_SPACE_RE pass,
    cheap even when dense -- see the module-level proof for why runs like
    "--mode" + a long "=" tail cost only local, bounded backtracking, not
    a per-candidate blowup) and full-matching only at each is equivalent
    to testing every position, minus the positions that can't possibly
    match. `.match(text, q)` (not `.search`/`finditer` with an endpos) is
    used deliberately: it is unconstrained in how far the match may
    extend past `window_end` once started inside the window (`[=\\s]+`
    crossing further whitespace/newlines, see the wrinkle noted above) --
    an endpos-bounded search would wrongly truncate those and miss real
    matches."""
    last = None
    for c in _DASH_OR_SPACE_RE.finditer(text, start, window_end + 1):
        m = _WEBPACK_FLAG_RE.match(text, c.start())
        if m:
            last = m
    return last


def _rightmost_webpack_in_window(text, start, window_end):
    """Rightmost occurrence of the literal "webpack" with its start in
    [start, window_end] (inclusive), or None. Unlike the flag alternation,
    "webpack" contains no \\s/`.` of its own, so it can never cross the
    newline at window_end -- an endpos-bounded finditer() is exact here,
    no wrinkle to work around."""
    last = None
    for m in _WEBPACK_RE.finditer(text, start, window_end + 1):
        last = m
    return last


def _first_line_bounded_match(x_pattern, y_finder, text):
    """Generic engine for the two `X.*Y` alternatives (`.` newline-bounded,
    see the module-level proof above). Walks X's occurrences in order;
    for each one not already ruled out by a previous line's failure,
    computes that line's window (X's match end, up to the next "\\n" or
    end of text) and asks `y_finder(text, window_start, window_end)` for
    the rightmost valid Y in it. The first line with a hit wins (X
    occurrences are visited left to right, so this is the overall
    leftmost valid match -- re.search()'s own semantics). A line with no
    hit marks everything through its own newline as already-known-empty,
    so later X occurrences on that same line are skipped in O(1) each
    (the finditer() loop still visits them, but does no new work) rather
    than re-scanning an already-ruled-out window -- this is what keeps
    total cost O(len(text)) regardless of how many X occurrences exist."""
    processed_up_to = -1
    for xm in x_pattern.finditer(text):
        if xm.start() <= processed_up_to:
            continue
        window_start = xm.end()
        nl = text.find("\n", window_start)
        window_end = nl if nl != -1 else len(text)
        y = y_finder(text, window_start, window_end)
        if y is not None:
            return _BuildShapeMatch(text, xm.start(), y.end())
        processed_up_to = window_end
    return None


def _webpack_flag_match(text):
    """Linear-time equivalent of the original
    `webpack.*(?:--mode[=\\s]+production|--production\\b|\\s-p\\b)`
    alternative -- same match/no-match verdict and same matched span. See
    this module's BUILD_SHAPE_RE header comment for the full proof."""
    return _first_line_bounded_match(_WEBPACK_RE, _rightmost_flag_in_window, text)


def _node_env_webpack_match(text):
    """Linear-time equivalent of the original
    `NODE_ENV=production.*webpack` alternative -- same proof as
    _webpack_flag_match(), with X = "NODE_ENV=production" and Y =
    "webpack"."""
    return _first_line_bounded_match(
        _NODE_ENV_PRODUCTION_RE, _rightmost_webpack_in_window, text
    )


class _BuildShapeRegex:
    """Drop-in .search()-only replacement for the original single
    re.compile(...) BUILD_SHAPE_RE -- see the header comment above this
    class. model-guard.py's `BUILD_SHAPE_RE.search(prompt)` / `.group(0)`
    call site needs no change."""

    def search(self, text):
        best = None
        for m in (
            _SIMPLE_ALT_RE.search(text),
            _webpack_flag_match(text),
            _node_env_webpack_match(text),
        ):
            if m is not None and (best is None or m.start() < best.start()):
                best = m
        if best is None:
            return None
        if isinstance(best, _BuildShapeMatch):
            return best
        return _BuildShapeMatch(text, best.start(), best.end())


BUILD_SHAPE_RE = _BuildShapeRegex()

# ---------------------------------------------------------------------------
# bash_build_match -- token-aware shell-command detector
# ---------------------------------------------------------------------------

_CONTROL_OPS = ("&&", "||", ";", "|", "&")
_SHELL_C_INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh"}
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_NPM_LIKE = {"npm", "pnpm", "yarn", "bun"}


def _basename(word):
    return word.rsplit("/", 1)[-1]


def _split_segments(cmd):
    """Token-aware split of a shell command LINE into simple-command
    segments, breaking on &&, ||, ;, | and & (newlines are ordinary shlex
    whitespace, so a heredoc body's own lines never start a new segment on
    their own -- they're just more words in whatever segment is currently
    open, e.g. `cat`'s). Quote-aware: shlex only ever treats &&/||/;/|/&
    as operators OUTSIDE quotes -- inside a quoted string (a grep pattern,
    a commit message, an echo argument, a heredoc delimiter's payload)
    they are just characters of that one token, never a split point.

    Raises ValueError on unbalanced quotes -- callers must treat that as
    "cannot parse" and fail OPEN (allow), never block a line this
    tokenizer cannot make sense of."""
    lexer = shlex.shlex(cmd, posix=True, punctuation_chars="&|;")
    lexer.whitespace_split = True
    tokens = list(lexer)  # ValueError here on unbalanced quotes
    segments, current = [], []
    for tok in tokens:
        if tok in _CONTROL_OPS:
            if current:
                segments.append(current)
            current = []
        else:
            current.append(tok)
    if current:
        segments.append(current)
    return segments


def _command_word(segment):
    """Index and value of a segment's own command word, after skipping
    leading env assignments (FOO=1 BAR=2 cmd ...). (None, None) if the
    segment is only env assignments with nothing to run."""
    i = 0
    while i < len(segment) and _ENV_ASSIGN_RE.match(segment[i]):
        i += 1
    if i >= len(segment):
        return None, None
    return i, segment[i]


def _segment_build_shape(idx, segment):
    """True if THIS segment's own command word (not text buried in its
    arguments) invokes a build -- mirrors the shapes BUILD_SHAPE_RE used to
    cover, matched positionally instead of as a substring search."""
    word = segment[idx]
    env_assigns = segment[:idx]
    args = segment[idx + 1:]
    base = _basename(word)

    # NODE_ENV=production webpack -- the env assignment already implies a
    # production build; no --mode/--production flag is required on the
    # invocation itself.
    if base == "webpack" and any(
        a.upper().startswith("NODE_ENV=PRODUCTION") for a in env_assigns
    ):
        return True

    if base in _NPM_LIKE:
        # `npm build` (direct alias) or `npm run build`
        return args[:1] == ["build"] or args[:2] == ["run", "build"]

    if base == "npx":
        return args[:2] == ["next", "build"]

    if base == "next":
        return args[:1] == ["build"]

    if base == "turbo":
        return args[:1] == ["build"] or args[:2] == ["run", "build"]

    if base == "vite":
        return args[:1] == ["build"]

    if base == "webpack":
        for a in args:
            al = a.lower()
            if al in ("--production", "-p"):
                return True
            if al.startswith("--mode=production"):
                return True
        for a, b in zip(args, args[1:]):
            if a.lower() == "--mode" and b.lower() == "production":
                return True
        return False

    if base == "wrangler":
        return args[:1] == ["deploy"] or args[:2] == ["pages", "deploy"]

    if base == "deploy.sh":
        return True

    if base == "build+strip":
        return True

    return False


def bash_build_match(cmd):
    """Return the matched segment (a display string) if `cmd` -- a real
    shell command line -- contains a bare, unwrapped build-shaped
    invocation; otherwise None.

    - Segments split on &&, ||, ;, |, & -- quote-aware (see
      _split_segments). A trigger phrase living inside a quoted string can
      never become a segment's own command word, so it can never match.
    - A segment matches only when ITS OWN command word (first token after
      skipping leading FOO=1-style env assignments) is a build invocation
      -- `cd x` is its own segment and is never itself build-shaped, so
      `cd app && npm run build` still blocks on the second segment.
    - `bash -c "<string>"` (and sh/zsh/dash/ksh -c) is recursed into: the
      quoted payload is itself a full command line, and is exactly the
      kind of wrapper this guard exists to see through -- a bare build
      hidden behind `bash -c "npm run build"` still blocks. A `guarded-
      build -- ...` payload inside the same wrapper still exempts, because
      recursion re-runs the same segment/exemption logic on it.
    - Running an interpreter directly against a known build script (e.g.
      `bash deploy.sh`, with no -c) is also caught: the script argument's
      own basename is checked the same way a bare invocation would be.
    - `guarded-build` exempts PER SEGMENT: a segment's own command word
      must itself be `guarded-build` (bare name, or a path ending
      `/guarded-build`). The literal text appearing anywhere else on the
      line -- `grep guarded-build README.md; npm run build` -- never
      exempts a separate, later, unwrapped build segment; that example
      still blocks on its second segment.
    - On any tokenizer failure (unbalanced quotes) this returns None --
      fail OPEN. The rest of safety-guard.py's fail-open/closed behavior
      elsewhere in the file is unaffected by this function either way.
    """
    try:
        segments = _split_segments(cmd)
    except ValueError:
        return None

    for segment in segments:
        if not segment:
            continue
        idx, word = _command_word(segment)
        if word is None:
            continue
        base = _basename(word)

        if base == "guarded-build":
            continue  # this segment IS the wrapper -- exempt

        if base in _SHELL_C_INTERPRETERS:
            args = segment[idx + 1:]
            if "-c" in args:
                c_idx = args.index("-c")
                if c_idx + 1 < len(args):
                    nested = bash_build_match(args[c_idx + 1])
                    if nested:
                        return nested
                continue
            # Not a -c string-execution form: `bash deploy.sh` runs the
            # script directly -- check the first non-flag argument's own
            # basename the same way a bare invocation would be.
            for a in args:
                if a.startswith("-"):
                    continue
                if _basename(a) in ("deploy.sh", "build+strip"):
                    return f"{word} {a}"
                break
            continue

        if _segment_build_shape(idx, segment):
            return " ".join(segment)

    return None


def is_compound_command(cmd):
    """True if `cmd` splits into more than one shell segment on a real,
    unquoted &&/||/;/|/& control operator. Fails to False (i.e. "treat as
    simple") on a tokenizer error, consistent with bash_build_match()'s
    own fail-open behavior on an unparsable line."""
    try:
        segments = _split_segments(cmd)
    except ValueError:
        return False
    return len(segments) > 1


def needs_shell_wrap(cmd):
    """True if a guarded-build rewrite of `cmd` must fold the whole line
    into a nested `bash -c <quoted>` rather than pass it directly as
    guarded-build's trailing argv -- see this module's docstring for the
    two cases (compound, or a leading env-assignment prefix on an
    otherwise-single segment). Fails to False on a tokenizer error, same
    direction as is_compound_command() and bash_build_match() -- callers
    here only ever reach this after bash_build_match() already matched
    the very same string, so a ValueError would be surprising, but the
    safe direction on any surprise is still the simple, non-nested form."""
    try:
        segments = _split_segments(cmd)
    except ValueError:
        return False
    if len(segments) != 1:
        return True
    segment = segments[0]
    return bool(segment) and bool(_ENV_ASSIGN_RE.match(segment[0]))
