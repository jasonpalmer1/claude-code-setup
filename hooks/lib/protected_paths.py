#!/usr/bin/env python3
"""protected_paths.py — the computed protected-path set + the disk-delete guard's
pure decision function (L-0925, 2026-09-23 disk-cleanup incident:
~/.claude/hub/reports/disk-cleanup-2026-09-23.md, plan:
~/.claude/hub/reports/L-disk-guard-plan.md, hub-approved amendments A1-A9).

Two things this module gives a caller:

  1. get_protected_set() -> dict[str, str]      realpath -> human reason
     Built from launchd plist references, symlink targets under ~/projects and
     ~/.claude-worktrees, cheap untracked-config-marker stats at each worktree
     root (A3 — no `git ls-files --ignored` per worktree, too costly across
     ~80 worktrees), a short hardcoded + memory-sourced hidden-deps list, and
     a "path contains 'cron'" net. Cached to a JSON file OUTSIDE ~/.claude/hub
     (A2 — headless routines are refused writes there), TTL-bounded, both
     overridable by env var for tests.

  2. check_command(cmd: str, cwd: str) -> (blocked: bool, reason: str)
     Pure — no execution, no side effects beyond reading the filesystem/cache.
     Tokenizes cmd (stripping wrapper/indirection commands — sudo, command,
     builtin, env, nice, nohup, time, timeout, exec, doas — and normalizing
     the verb's basename so /bin/rm, ./rm, \rm all match same as rm;
     unwrapping bash -c/sh -c/zsh -c/eval, resolving simple $()/`` and
     $VAR/${VAR} substitutions against assignments made earlier on the same
     line, expanding brace groups, combining `echo X | xargs rm -rf`
     pipelines, tracking `cd X && ...` chains), classifies each resulting
     simple command against the destructive-verb list (rm -r*, trash, unlink,
     mv <source>, rsync --delete* <dest>, find -delete/-exec rm, git worktree
     remove, git clean -x*), resolves every target path (~, glob, brace,
     relative-to-cwd, realpath) and checks it against the protected set.

     Failure mode (A4): if the protected set cannot be built (exception or a
     soft wall-clock timeout), or a specific target can't be resolved at all
     (an opaque substitution/variable reference — A9, fix round 1), this
     function does NOT fail open across the board. It fails closed for a
     recursive-delete/worktree-removal/mv/rsync-delete whose (best-effort-
     resolved, or textually-hinted, or cwd-implied) target sits under one
     of four fixed roots: ~/projects, ~/.claude-worktrees, ~/Library/Caches,
     ~/.claude. Everything else — including every ordinary `rm` this guard
     can't even classify as destructive — passes through untouched.

`iter_simple_commands()` is exposed separately (not prefixed `_`) because the
hub's guard-fixes round 8 catastrophic-rm check is expected to reuse this same
tokenizer/unwrapper rather than re-deriving quote/substitution/wrapper
handling a second time (hub addendum A9).

Env overrides (all test hooks; unset for production use):
  PROTECTED_PATHS_HOME              default: real $HOME (os.path.expanduser('~'))
  PROTECTED_PATHS_PROJECTS_ROOT     default: {HOME}/projects
  PROTECTED_PATHS_WORKTREES_ROOT    default: {HOME}/.claude-worktrees
  PROTECTED_PATHS_LAUNCHAGENTS_DIR  default: {HOME}/Library/LaunchAgents
  PROTECTED_PATHS_CLAUDE_DIR        default: {HOME}/.claude
  PROTECTED_PATHS_CACHES_DIR        default: {HOME}/Library/Caches
  PROTECTED_PATHS_MEMORY_DIR        default: {CLAUDE_DIR}/projects/<home-slug>/memory
  PROTECTED_PATHS_CACHE_FILE        default: ~/.cache/claude/protected-paths.json (REAL home,
                                     not PROTECTED_PATHS_HOME — a test always sets this
                                     explicitly so it never touches the real cache)
  PROTECTED_PATHS_CACHE_TTL         default: 60 (seconds)
  PROTECTED_PATHS_BUILD_BUDGET      default: 3.0 (seconds, soft wall-clock cap on a build)
"""
from __future__ import annotations

import fnmatch
import glob
import json
import os
import subprocess
import re
import time

_FIRMLINK_PREFIX = "/System/Volumes/Data"


def _realpath(p):
    """os.path.realpath plus macOS firmlink normalisation (L-1521): /System/Volumes/Data/<users-dir>/x
    and /<users-dir>/x are the same file but realpath() keeps both spellings, so a protected-path
    check could be sidestepped by the firmlink spelling. Strip the prefix after resolving."""
    try:
        sp = os.fspath(p)
        if isinstance(sp, str) and sp.startswith(_FIRMLINK_PREFIX + "/"):
            p = sp[len(_FIRMLINK_PREFIX):]
    except TypeError:
        pass
    r = os.path.realpath(p)
    if r == _FIRMLINK_PREFIX:
        return "/"
    if r.startswith(_FIRMLINK_PREFIX + "/"):
        r = r[len(_FIRMLINK_PREFIX):]
    return r


# ----------------------------------------------------------------------------
# Env-overridable locations
# ----------------------------------------------------------------------------

def _home() -> str:
    return os.environ.get("PROTECTED_PATHS_HOME") or os.path.expanduser("~")


def _projects_root() -> str:
    return os.environ.get("PROTECTED_PATHS_PROJECTS_ROOT") or os.path.join(_home(), "projects")


def _worktrees_root() -> str:
    return os.environ.get("PROTECTED_PATHS_WORKTREES_ROOT") or os.path.join(_home(), ".claude-worktrees")


def _launchagents_dir() -> str:
    return os.environ.get("PROTECTED_PATHS_LAUNCHAGENTS_DIR") or os.path.join(_home(), "Library/LaunchAgents")


def _claude_dir() -> str:
    return os.environ.get("PROTECTED_PATHS_CLAUDE_DIR") or os.path.join(_home(), ".claude")


def _caches_dir() -> str:
    return os.environ.get("PROTECTED_PATHS_CACHES_DIR") or os.path.join(_home(), "Library/Caches")


def _memory_dir() -> str:
    return os.environ.get("PROTECTED_PATHS_MEMORY_DIR") or os.path.join(
        _claude_dir(), "projects/<home-slug>/memory"
    )


def _cache_file() -> str:
    return os.environ.get("PROTECTED_PATHS_CACHE_FILE") or os.path.expanduser(
        "~/.cache/claude/protected-paths.json"
    )


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("PROTECTED_PATHS_CACHE_TTL", "60"))
    except ValueError:
        return 60.0


def _build_budget() -> float:
    try:
        return float(os.environ.get("PROTECTED_PATHS_BUILD_BUDGET", "3.0"))
    except ValueError:
        return 3.0


# launchd Label prefixes that get the worktree-root ancestor climb (section 1a
# of the plan). An unrecognized job's literal path is still protected by the
# flat per-string check regardless — this list only controls generalization.
CLIMB_LABEL_PREFIXES = (
    "com.operator.",
    "com.<product-a>.",
    "com.<product-b>.",
    "com.vendor.",
    "com.sous.",
)

# Interpreter/tool paths a plist's ProgramArguments routinely names — never
# load-bearing project data, just noise if added to the protected set.
SYSTEM_BIN_PREFIXES = (
    "/usr/", "/bin/", "/sbin/", "/opt/homebrew/", "/System/",
    "/Applications/", "/Library/Developer/", "/Library/Frameworks/",
)

FALLBACK_ROOT_NAMES = ("projects", "worktrees", "caches", "claude")


def _fallback_roots() -> dict:
    return {
        "projects": _projects_root(),
        "worktrees": _worktrees_root(),
        "caches": _caches_dir(),
        "claude": _claude_dir(),
    }


class _BuildTimeout(Exception):
    pass


# ----------------------------------------------------------------------------
# 1. The protected-path set builder
# ----------------------------------------------------------------------------

# Fix round 3 (bug-gate r3 item 2): widened from the original 5 to the full
# regenerable-cache set build-cache-sweep.sh itself already targets
# (.next out .turbo dist coverage build), plus a few more common heavy dirs
# that were making a fresh build slower than it needed to be under load.
PRUNE_DIRS = {
    "node_modules", ".git", ".next", ".venv", "out",
    "dist", ".turbo", "build", "coverage",
    ".cache", "__pycache__", ".pytest_cache", ".mypy_cache",
}
ONE_LEVEL_EXACT = {"site", "app"}
ONE_LEVEL_PREFIX = "worker"
MAX_WALK_DEPTH = 4

# Fix round 3: bumped from the implicit round-1/2 format (bare reason
# strings) to 2 when every protected-set value became a [reason, kind]
# pair (item 1's marker-vs-dependency fix). _load_cache() refuses any cache
# file whose "schema" doesn't match this exactly, so an old-format file
# left over from a prior version of this module (or a future format change)
# is treated as absent rather than fed to code that expects the new shape.
_CACHE_SCHEMA_VERSION = 2


def _climb_git_root(path: str, boundary: str):
    """Walk upward from path until a directory containing a `.git` entry
    (file or dir — a linked worktree's `.git` is a file) is found, never
    climbing above `boundary`. None if nothing found before the boundary."""
    boundary = boundary.rstrip("/")
    cur = path.rstrip("/")
    if not cur.startswith(boundary):
        return None
    while True:
        if os.path.exists(os.path.join(cur, ".git")):
            return cur
        if cur == boundary:
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def _has_markers(d: str):
    """First basename in d matching .env*, .venv, *.pem, or .git-credentials —
    the cheap A3 stat-only substitute for `git ls-files --ignored`."""
    try:
        names = os.listdir(d)
    except OSError:
        return None
    for n in names:
        if n.startswith(".env") or n == ".venv" or n.endswith(".pem") or n == ".git-credentials":
            return n
    return None


def _check_worktree_markers(root: str, protected: dict) -> bool:
    """Registers TWO entries, both kind="marker" (fix round 3, bug-gate r3
    item 1): the worktree ROOT itself, and the actual marker FILE path. Kept
    separate so _overlap() can apply the restricted marker-kind match rule
    (IS the root/marker, CONTAINS the root/marker -- never "is INSIDE the
    root") without also needing the root to somehow enumerate every marker
    file nested under a one-level-exact subdir. Returns True iff a marker
    was found, so the caller can stop descending into this worktree's
    subtree once its root is already registered (r3 item 2, walk-cost)."""
    hit = _has_markers(root)
    if hit:
        marker_path = os.path.join(root, hit)
        protected.setdefault(root, (f"holds untracked {hit} at the worktree root", "marker"))
        protected.setdefault(marker_path, (f"untracked {hit} at the worktree root", "marker"))
        return True
    try:
        children = os.listdir(root)
    except OSError:
        children = []
    for name in children:
        if name in ONE_LEVEL_EXACT or name.startswith(ONE_LEVEL_PREFIX):
            sub = os.path.join(root, name)
            if os.path.isdir(sub):
                h2 = _has_markers(sub)
                if h2:
                    marker_path = os.path.join(sub, h2)
                    protected.setdefault(root, (f"holds untracked {h2} in {name}/", "marker"))
                    protected.setdefault(marker_path, (f"untracked {h2} in {name}/", "marker"))
                    return True
    return False


def _walk_tree(base: str, protected: dict, deadline: float):
    """(b)+(c)+(e): symlink targets, worktree-root config markers, and the
    'cron' substring net, in one bounded-depth pass. Prunes descent into
    node_modules/.git/.next/.venv/out (A3) but still RECORDS an entry with
    one of those names if it is itself a symlink (checked before the prune
    decision, since a symlink is never descended into either way)."""
    if not os.path.isdir(base):
        return
    base = base.rstrip("/")
    base_depth = base.count(os.sep)
    for root, dirnames, _filenames in os.walk(base, topdown=True, followlinks=False):
        if time.monotonic() > deadline:
            raise _BuildTimeout(f"walk of {base} exceeded budget")

        depth = root.count(os.sep) - base_depth
        if depth >= MAX_WALK_DEPTH:
            dirnames[:] = []
            continue

        stop_descent = False
        if os.path.exists(os.path.join(root, ".git")) and root != base:
            # Fix round 3: register the REALPATH'd root, not the raw os.walk
            # path -- _scan_launchd already realpaths everything it
            # registers, and every query side (_resolve_target) always
            # realpaths its candidate too. On a machine where some ancestor
            # of $HOME is itself a symlink (e.g. macOS's /var ->
            # /private/var, hit by any tmpdir-based test fixture), a raw,
            # un-resolved registration here would silently never match a
            # (always-resolved) query -- found while building this round's
            # marker-vs-dependency test fixtures, not by the gate.
            resolved_root = _realpath(root)
            marker_hit = _check_worktree_markers(resolved_root, protected)
            cron_hit = "cron" in resolved_root.lower()
            if cron_hit:
                # Fix round 3: tagged "dependency" (not "marker") -- a
                # scheduled job's own worktree really is depended on as a
                # whole tree, unlike an untracked-config marker, so it keeps
                # the broader "candidate is INSIDE this root" match in
                # _overlap(). In practice _scan_launchd's climb (which runs
                # BEFORE this walk, in build_protected_set) already usually
                # wins this setdefault race for a worktree with a real
                # launchd job; this is the fallback for the "cron" substring
                # heuristic alone.
                protected.setdefault(
                    resolved_root,
                    ("path contains 'cron' — likely a scheduled-job worktree", "dependency"),
                )
            # Fix round 3 (bug-gate r3 item 2): once this worktree root is
            # already registered (marker or cron-net), stop descending
            # further into its subtree -- this iteration already scanned its
            # own immediate children for symlinks below, and a marker-kind
            # root's protection doesn't extend to its subdirectories anyway
            # (item 1's fix), so deeper descent only costs budget here for
            # no additional protection. A nested symlink 2+ levels inside
            # such a worktree would be missed by this optimization -- an
            # accepted, documented trade-off (see Honest limits).
            stop_descent = marker_hit or cron_hit

        keep = []
        for d in dirnames:
            full = os.path.join(root, d)
            if os.path.islink(full):
                target = _realpath(full)
                protected.setdefault(target, (f"symlinked from {full}", "dependency"))
                groot = _climb_git_root(target, _projects_root()) or _climb_git_root(
                    target, _worktrees_root()
                )
                if groot:
                    protected.setdefault(
                        groot, (f"symlinked from {full} (via its repo root)", "dependency")
                    )
                continue  # os.walk never recurses into a symlinked dir anyway
            if d in PRUNE_DIRS:
                continue  # real (non-symlink) instance — skip, cost pruning
            keep.append(d)
        dirnames[:] = [] if stop_descent else keep


def _scan_launchd(protected: dict):
    try:
        import plistlib
    except ImportError:
        return
    d = _launchagents_dir()
    try:
        entries = os.listdir(d)
    except OSError:
        return
    projects_root = _projects_root()
    worktrees_root = _worktrees_root()
    home_rp = _realpath(_home())
    for name in entries:
        if not name.endswith(".plist"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, "rb") as fh:
                data = plistlib.load(fh)
        except Exception:
            continue
        label = data.get("Label", name)
        strings = []

        def _walk(v):
            if isinstance(v, str):
                strings.append(v)
            elif isinstance(v, dict):
                for vv in v.values():
                    _walk(vv)
            elif isinstance(v, list):
                for vv in v:
                    _walk(vv)

        _walk(data)
        climb = any(label.startswith(p) for p in CLIMB_LABEL_PREFIXES)
        for s in strings:
            if not (s.startswith("/") or s.startswith("~/") or s.startswith("~")):
                continue
            if s.startswith(SYSTEM_BIN_PREFIXES):
                continue  # /usr/bin/env, /bin/bash etc — noise, not load-bearing data
            p = os.path.expanduser(s) if s.startswith("~") else s
            rp = _realpath(p)
            if rp == home_rp or home_rp.startswith(rp + os.sep):
                # fix round 1 (found while re-deriving the callsite patch,
                # bug-gate FAIL "4b control: mv between two env files"):
                # a WorkingDirectory of bare $HOME (a routine launchd
                # default, e.g. com.operator.claude.weekly-drill on this
                # machine) or any shallower ancestor is not "this job's own
                # data" the way a project subdirectory is -- registering it
                # would make the ENTIRE home directory (or an ancestor of
                # it) "protected," so every ordinary file operation anywhere
                # under $HOME false-positives via the ancestor/descendant
                # overlap check. Skip it; a job's actual script/data path
                # (further down ProgramArguments) still gets registered
                # normally.
                continue
            # Fix round 3: "dependency" kind -- a launchd job's own
            # referenced path, and the worktree it climbs to, really are
            # depended on as a whole tree (the job reads/writes anywhere
            # under them), unlike an untracked-config marker.
            protected.setdefault(rp, (f"launchd job {label}", "dependency"))
            if climb and (
                rp.startswith(projects_root + os.sep) or rp.startswith(worktrees_root + os.sep)
            ):
                root = _climb_git_root(rp, projects_root) or _climb_git_root(rp, worktrees_root)
                if root:
                    protected.setdefault(
                        root, (f"launchd job {label} (runs from this worktree)", "dependency")
                    )


HIDDEN_DEP_REF_GLOB = "reference_*hidden_dep*.md"
BACKTICK_PATH = re.compile(r"`(~[^`]*|/[^`]*)`")


def _scan_playwright_pins(protected: dict, caches: str):
    """Protect ms-playwright/<browser>-<rev> for every revision pinned by the
    <product-a> repo's playwright-core."""
    import json
    bj = os.path.join(_projects_root(), "<product-a>", "node_modules", "playwright-core", "browsers.json")
    try:
        with open(bj) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    for b in data.get("browsers", []):
        name, rev = b.get("name"), b.get("revision")
        if not name or not rev:
            continue
        dd = os.path.join(caches, "ms-playwright", name.replace("-", "_") + "-" + str(rev))
        protected.setdefault(dd, (f"pinned by {bj} (<product-a> playwright revision {rev})", "dependency"))


def _scan_hidden_deps(protected: dict):
    caches = _caches_dir()
    protected.setdefault(
        os.path.join(caches, "ms-playwright"),
        ("a known hidden dependency (<product-a> nightly test suite reads this)", "dependency"),
    )
    # 2026-10-05: 1228 (pinned by <product-a>'s playwright-core) vanished while
    # the parent dir was "protected" -- also pin each revision dir the repo's
    # node_modules/playwright-core/browsers.json expects, by exact name.
    _scan_playwright_pins(protected, caches)
    mdir = _memory_dir()
    try:
        names = os.listdir(mdir)
    except OSError:
        return
    for name in names:
        if not fnmatch.fnmatch(name, HIDDEN_DEP_REF_GLOB):
            continue
        path = os.path.join(mdir, name)
        try:
            with open(path, "r", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        for m in BACKTICK_PATH.finditer(content):
            raw = m.group(1)
            p = os.path.expanduser(raw) if raw.startswith("~") else raw
            protected.setdefault(
                _realpath(p), (f"a known hidden dependency per {name}", "dependency")
            )


def build_protected_set() -> dict:
    """Fresh build, no cache. Raises on a hard failure or soft-timeout —
    caller (get_protected_set / check_command) decides what that means."""
    deadline = time.monotonic() + _build_budget()
    protected: dict = {}
    _scan_launchd(protected)
    if time.monotonic() > deadline:
        raise _BuildTimeout("launchd scan exceeded budget")
    _walk_tree(_projects_root(), protected, deadline)
    _walk_tree(_worktrees_root(), protected, deadline)
    _scan_hidden_deps(protected)
    return protected


def _load_cache():
    try:
        with open(_cache_file(), "r") as fh:
            data = json.load(fh)
        built_at = float(data.get("built_at", 0))
        if (time.time() - built_at) > _cache_ttl():
            return None
        # Fix round 3: the on-disk cache format changed this round (every
        # value became a 2-element [reason, kind] pair instead of a bare
        # reason string, to carry the marker-vs-dependency distinction —
        # item 1's fix). A cache file written by the OLD code can still be
        # sitting on disk, within its own still-valid TTL, the instant this
        # NEW code first runs (e.g. mid-deploy, or any time an older/newer
        # protected_paths.py alternate briefly). Without a schema check, a
        # stale-format cache load would hand _overlap() bare strings where
        # it expects (reason, kind) tuples, raising ValueError on unpack —
        # uncaught (this code path sits outside check_command's own
        # build-failure try/except), which would crash the whole guard
        # rather than degrade to A4's fail-closed fallback. A schema tag
        # plus a real per-entry shape check makes any old- or malformed-
        # format cache file simply miss (treated as absent -> a fresh
        # build), never crash.
        if data.get("schema") != _CACHE_SCHEMA_VERSION:
            return None
        paths = data.get("paths")
        if not isinstance(paths, dict):
            return None
        for val in paths.values():
            if (
                not isinstance(val, (list, tuple))
                or len(val) != 2
                or not isinstance(val[0], str)
                or val[1] not in ("marker", "dependency")
            ):
                return None
        return {p: (v[0], v[1]) for p, v in paths.items()}
    except Exception:
        return None


def _write_cache(protected: dict):
    try:
        path = _cache_file()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".tmp-{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(
                {"built_at": time.time(), "schema": _CACHE_SCHEMA_VERSION, "paths": protected},
                fh,
            )
        os.replace(tmp, path)
    except Exception:
        pass  # cache is an optimization; a write failure must not break the build


def get_protected_set(force_rebuild: bool = False) -> dict:
    """Cached accessor. Raises on build failure (A4's trigger) — never
    silently returns an empty/incomplete set as if it were real."""
    if not force_rebuild:
        cached = _load_cache()
        if cached is not None:
            return cached
    protected = build_protected_set()
    _write_cache(protected)
    return protected


# ----------------------------------------------------------------------------
# 2. The tokenizer / wrapper-unwrapper — reused by round 8 (hub addendum A9)
# ----------------------------------------------------------------------------

AMBIG = "\x01PP_AMBIGUOUS_SUBST\x01"
_SUBST_RE = re.compile(r"\$\((?P<a>[^()]*)\)|`(?P<b>[^`]*)`")
_TRIVIAL_ECHO_RE = re.compile(r"^(?:echo|printf(?:\s+['\"]%s['\"])?)\s+(.+)$")
_SIMPLE_WORD_RE = re.compile(r"^[\w~./$-]+$")

JOIN_OPS = {";", "&&", "||", "&"}
PIPE_OP = "|"
WRAPPER_VERBS = {"bash", "sh", "zsh"}
DESTRUCTIVE_RM_VERBS = {"rm"}

# Fix round 1 (bug-gate blocker 1): indirection/wrapper commands that leave
# the *real* verb further down the argv, plus PATH/basename-style spellings
# of the verb itself (/bin/rm, ./rm, \rm) that a literal `head == "rm"`
# check never saw. WRAPPER_STRIP_SIMPLE are stripped with no argument
# handling of their own; sudo/env/nice/timeout get their own logic below
# since each can carry its own flags/args before the real command starts.
WRAPPER_STRIP_SIMPLE = {"command", "builtin", "exec", "doas", "nohup", "time"}
_ASSIGNMENT_HEAD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_wrappers(stage):
    """Repeatedly peel leading indirection/wrapper tokens off a stage so the
    destructive-verb match sees the real command: sudo [flags], command,
    builtin, env [VAR=val...] [-flags], nice [-n N|-N], nohup, time,
    timeout [flags] N, exec, doas. Order-independent -- wrappers can stack
    (e.g. `nice nohup command rm ...`), so this loops until nothing more
    peels off."""
    stage = list(stage)
    changed = True
    while stage and changed:
        changed = False
        head = stage[0]
        # L-1495: bare `NAME=value cmd` (no `env`) -- peel it, but only when something follows;
        # a stage that is ENTIRELY one assignment must reach _try_record_assignment unstripped.
        if _ASSIGNMENT_HEAD_RE.match(head) and len(stage) > 1:
            stage = stage[1:]
            changed = True
            continue
        if head == "sudo":
            i = 1
            while i < len(stage) and stage[i].startswith("-"):
                i += 1
            stage = stage[i:]
            changed = True
            continue
        if head in WRAPPER_STRIP_SIMPLE:
            stage = stage[1:]
            changed = True
            continue
        if head == "env":
            i = 1
            while i < len(stage) and (
                stage[i].startswith("-") or _ASSIGNMENT_HEAD_RE.match(stage[i])
            ):
                i += 1
            stage = stage[i:]
            changed = True
            continue
        if head == "nice":
            i = 1
            while i < len(stage) and stage[i].startswith("-"):
                if stage[i] == "-n" and i + 1 < len(stage):
                    i += 2
                else:
                    i += 1
            stage = stage[i:]
            changed = True
            continue
        if head == "timeout":
            i = 1
            while i < len(stage) and stage[i].startswith("-"):
                i += 1
            if i < len(stage):
                i += 1  # the duration argument itself (e.g. "10", "10s")
            stage = stage[i:]
            changed = True
            continue
    return stage


def _verb_basename(head: str) -> str:
    """/bin/rm, ./rm, \\rm -> rm -- so the verb match is PATH/spelling
    independent (bug-gate blocker 1)."""
    h = head[1:] if head.startswith("\\") else head
    return h.rsplit("/", 1)[-1]


def _expand_word(word: str, home: str) -> str:
    out = word.replace("${HOME}", home).replace("$HOME", home)
    if out == "~":
        return home
    if out.startswith("~/"):
        return home + out[1:]
    return out


# Fix round 1 (bug-gate blocker 2): $VAR / ${VAR} resolved from assignments
# made earlier on the SAME command line (`X=~/projects/.../node_modules;
# rm -rf $X`), not just $()/backtick substitution. An unresolved reference
# (a var never assigned, or assigned to something too complex to trust —
# same "simple word" bar _resolve_substitutions already applies to a
# trivial `echo`) makes the whole word ambiguous, so it falls through to
# check_command's existing A4 fail-closed handling rather than silently
# resolving to a bogus literal path that trivially fails to overlap anything.
_VAR_REF_RE = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_ASSIGNMENT_FULL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _apply_var_env(word: str, var_env: dict, home: str) -> str:
    if "$" not in word:
        return word

    unresolved = False

    def repl(m):
        nonlocal unresolved
        name = m.group(1) or m.group(2)
        if name == "HOME":
            return home
        if name in var_env:
            return var_env[name]
        unresolved = True
        return ""

    out = _VAR_REF_RE.sub(repl, word)
    return AMBIG if unresolved else out


def _try_record_assignment(stage, var_env: dict, home: str) -> bool:
    """If stage is exactly one `[export] NAME=value` token, record it (or,
    for a value too complex to trust, drop any prior binding so a later
    reference fails closed as unresolved) and return True. Anything else
    (including `NAME=value somecommand ...`, which is NOT a bare assignment)
    returns False and is left for normal classification."""
    if not stage:
        return False
    idx = 1 if (stage[0] == "export" and len(stage) >= 2) else 0
    if idx != len(stage) - 1:
        return False
    m = _ASSIGNMENT_FULL_RE.match(stage[idx])
    if not m:
        return False
    name, val = m.group(1), m.group(2).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        val = val[1:-1]
    if val == "" or _SIMPLE_WORD_RE.match(val):
        var_env[name] = _expand_word(val, home)
    else:
        var_env.pop(name, None)
    return True


def _resolve_substitutions(cmd: str) -> str:
    """Replace $(...) / `...` with a resolved literal when it is a trivial
    `echo <word>` / `printf <word>`, else with the AMBIG sentinel. Textual —
    works whether the substitution sits inside double quotes or bare, and
    naturally handles a backtick pair split by shlex."""
    home = _home()

    def repl(m):
        inner = (m.group("a") if m.group("a") is not None else m.group("b")).strip()
        em = _TRIVIAL_ECHO_RE.match(inner)
        if em:
            val = em.group(1).strip().strip("\"'")
            if _SIMPLE_WORD_RE.match(val):
                return _expand_word(val, home)
        return AMBIG

    return _SUBST_RE.sub(repl, cmd)


def _shlex_tokens(cmd: str):
    import shlex

    lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        # unbalanced quote etc — can't safely tokenize.
        raise


def _split_top_level(tokens):
    """tokens -> list[segment]; segment -> list[stage]; stage -> list[word].
    A segment is a `;`/`&&`/`||`/`&`-joined chain (cwd tracking crosses these
    within one call); a stage is one `|`-pipeline member."""
    segments = []
    cur_segment = []
    cur_stage = []
    for tok in tokens:
        if tok in JOIN_OPS:
            if cur_stage:
                cur_segment.append(cur_stage)
                cur_stage = []
            if cur_segment:
                segments.append(cur_segment)
                cur_segment = []
        elif tok == PIPE_OP:
            if cur_stage:
                cur_segment.append(cur_stage)
                cur_stage = []
        elif tok in ("(", ")"):
            continue  # subshell grouping — flattened, an honest limitation
        else:
            cur_stage.append(tok)
    if cur_stage:
        cur_segment.append(cur_stage)
    if cur_segment:
        segments.append(cur_segment)
    return segments


def _strip_sudo(stage):
    """Kept as the historical name both call sites already use; delegates to
    the broader _strip_wrappers (fix round 1, blocker 1) so sudo AND every
    other indirection wrapper get stripped in one pass."""
    return _strip_wrappers(stage)


def _rm_is_recursive(flag_tokens) -> bool:
    for t in flag_tokens:
        if t == "--recursive":
            return True
        if t.startswith("--"):
            continue
        if t.startswith("-") and len(t) > 1:
            if any(c in ("r", "R") for c in t[1:]):
                return True
    return False


def _classify_stage(stage, cwd):
    """stage: list[str]. Returns (verb, targets, ambiguous) or
    (None, None, False). Fix round 1 (blocker 1): normalizes wrappers
    (sudo/command/env/nice/.../timeout) and the verb's basename first, so
    /bin/rm, env rm, nice rm, command rm etc. all reach the same checks a
    bare `rm` would."""
    stage = _strip_wrappers(stage)
    if not stage:
        return None, None, False
    head = _verb_basename(stage[0])

    if head == "rm":
        flags = [t for t in stage[1:] if t.startswith("-")]
        targets = [t for t in stage[1:] if not t.startswith("-")]
        if targets and _rm_is_recursive(flags):
            amb = any(AMBIG in t for t in targets)
            return "rm", targets, amb
        return None, None, False

    if head == "trash":
        targets = [t for t in stage[1:] if not t.startswith("-")]
        if targets:
            amb = any(AMBIG in t for t in targets)
            return "trash", targets, amb
        return None, None, False

    if head == "unlink":
        # fix round 1 (blocker 4): single-file delete, same overlap check as
        # every other verb here.
        targets = [t for t in stage[1:] if not t.startswith("-")]
        if targets:
            amb = any(AMBIG in t for t in targets)
            return "unlink", targets, amb
        return None, None, False

    if head == "mv":
        # fix round 1 (blocker 4): block when any SOURCE argument is/contains
        # a protected path -- the destination is irrelevant to this check.
        # `mv a b c... dest` -- last non-flag arg is the destination, the
        # rest are sources.
        args = [t for t in stage[1:] if not t.startswith("-")]
        if len(args) >= 2:
            sources = args[:-1]
            amb = any(AMBIG in t for t in sources)
            return "mv", sources, amb
        return None, None, False

    if head == "rsync":
        # fix round 1 (blocker 4): rsync --delete* with a protected
        # destination mirrors what an rm would have done to it.
        flags = [t for t in stage[1:] if t.startswith("-")]
        args = [t for t in stage[1:] if not t.startswith("-")]
        if args and any(f.startswith("--delete") for f in flags):
            dest = args[-1]
            amb = AMBIG in dest
            return "rsync_delete", [dest], amb
        if len(args) >= 2 and any(f == "--remove-source-files" for f in flags):
            # fix round 2 (bug-gate r2 item 2): --remove-source-files
            # deletes every SOURCE file once transferred -- the opposite
            # side from --delete above, which threatens the destination.
            # `rsync --remove-source-files -a <src>/ <dest>/` -- last
            # non-flag arg is the destination, the rest are sources.
            sources = args[:-1]
            amb = any(AMBIG in t for t in sources)
            return "rsync_remove_source", sources, amb
        return None, None, False

    if head == "tar":
        # fix round 2 (bug-gate r2 item 2): tar --remove-files deletes every
        # member it just archived. Block on those members (the source
        # files/dirs), not the archive path itself.
        flags = [t for t in stage[1:] if t.startswith("-")]
        if not any(f == "--remove-files" for f in flags):
            return None, None, False
        args = stage[1:]
        # Find the archive-filename token so it's excluded from targets: it
        # follows -f/--file/--file=X, or a combined short-flag cluster that
        # contains 'f' (e.g. -cf, -xf).
        archive_idx = None
        i = 0
        while i < len(args):
            t = args[i]
            if t in ("-f", "--file"):
                archive_idx = i + 1
                i += 2
                continue
            if t.startswith("--file="):
                i += 1
                continue
            if t.startswith("-") and not t.startswith("--") and "f" in t[1:]:
                archive_idx = i + 1
                i += 2
                continue
            i += 1
        targets = [t for idx, t in enumerate(args) if not t.startswith("-") and idx != archive_idx]
        if targets:
            amb = any(AMBIG in t for t in targets)
            return "tar_remove_files", targets, amb
        return None, None, False

    if head == "find":
        i = 1
        paths = []
        while i < len(stage) and not stage[i].startswith("-"):
            paths.append(stage[i])
            i += 1
        rest = stage[i:]
        has_delete = "-delete" in rest
        has_exec_rm = False
        if "-exec" in rest:
            j = rest.index("-exec")
            execargs = []
            k = j + 1
            while k < len(rest) and rest[k] not in (";", "+"):
                execargs.append(rest[k])
                k += 1
            if execargs and _verb_basename(execargs[0]) == "rm":
                if not execargs[1:] or _rm_is_recursive(execargs[1:]) or True:
                    # find -exec rm ... is destructive regardless of -r: it
                    # deletes every matched file/dir the find turned up.
                    has_exec_rm = True
        if (has_delete or has_exec_rm) and paths:
            amb = any(AMBIG in p for p in paths)
            return "find", paths, amb
        return None, None, False

    if head == "git" and len(stage) >= 2:
        if stage[1] == "worktree" and "remove" in stage[2:]:
            idx = stage.index("remove", 2)
            targets = [t for t in stage[idx + 1 :] if not t.startswith("-")]
            if targets:
                amb = any(AMBIG in t for t in targets)
                return "worktree_remove", targets, amb
            return None, None, False
        if stage[1] == "clean":
            flags = stage[2:]
            destructive = any(
                f.startswith("-") and not f.startswith("--") and "x" in f.lower() for f in flags
            ) or "--force" in flags and any("x" in f.lower() for f in flags if f.startswith("-"))
            if destructive:
                return "git_clean", [cwd], False
        return None, None, False

    return None, None, False


def _combine_xargs(prev_stage, xargs_stage):
    """`echo <items> | xargs rm -rf` -> a synthetic ['rm', '-rf', <items>]
    stage. Returns None if the data source isn't a literal echo/printf (an
    xargs fed from anything else is target-ambiguous, handled by the caller
    marking it destructive-but-unresolvable instead)."""
    prev = _strip_sudo(prev_stage)
    if not prev or prev[0] not in ("echo", "printf"):
        return None
    items = [t for t in prev[1:] if not t.startswith("-")]
    j = 1
    while j < len(xargs_stage) and xargs_stage[j].startswith("-"):
        j += 1
    cmd_tokens = xargs_stage[j:]
    if not cmd_tokens:
        return None
    return cmd_tokens + items


def _maybe_unwrap(stage):
    """bash -c "..."/sh -c/zsh -c/eval "..." -> the nested command string, or
    None if this stage isn't a wrapper."""
    if not stage:
        return None
    if stage[0] in WRAPPER_VERBS and len(stage) >= 3 and stage[1] == "-c":
        return " ".join(stage[2:])
    if stage[0] == "eval" and len(stage) >= 2:
        return " ".join(stage[1:])
    return None


def iter_simple_commands(cmd: str, cwd: str, var_env: dict = None):
    """Yield dict(verb=str, targets=list[str-raw-tokens], cwd=str,
    ambiguous=bool) for every destructive-shaped simple command found in cmd,
    after unwrapping wrappers, resolving trivial substitutions, resolving
    $VAR/${VAR} references against assignments made earlier on the same
    line, expanding brace groups in targets (done downstream in
    _resolve_target), combining xargs pipelines, and tracking `cd X && ...`
    within a chain. Non-destructive simple commands are silently skipped
    (nothing to classify). var_env is fresh per top-level call (NOT shared
    into a bash -c/sh -c/eval unwrap — an unexported shell variable would not
    be visible to a real child shell either, so a fresh dict there is the
    conservative-and-correct default, not just a simplification)."""
    home = _home()
    if var_env is None:
        var_env = {}
    resolved_text = _resolve_substitutions(cmd)
    tokens = _shlex_tokens(resolved_text)
    segments = _split_top_level(tokens)

    effective_cwd = cwd
    for pipe_stages in segments:
        stripped = [_strip_wrappers(s) for s in pipe_stages]

        if len(stripped) == 1 and _try_record_assignment(stripped[0], var_env, home):
            continue

        stripped = [[_apply_var_env(t, var_env, home) for t in s] for s in stripped]

        if len(stripped) == 1 and stripped[0] and stripped[0][0] == "cd" and len(stripped[0]) >= 2:
            raw_dir = stripped[0][1]
            cands = _resolve_target(raw_dir, effective_cwd)
            if cands:
                effective_cwd = cands[0]
            continue

        unwrapped_any = False
        for stage in stripped:
            nested = _maybe_unwrap(stage)
            if nested is not None:
                unwrapped_any = True
                yield from iter_simple_commands(nested, effective_cwd)
        if unwrapped_any:
            continue

        prev = None
        for stage in stripped:
            if stage and stage[0] == "xargs" and prev is not None:
                synthetic = _combine_xargs(prev, stage)
                if synthetic is not None:
                    verb, targets, amb = _classify_stage(synthetic, effective_cwd)
                    if verb:
                        yield {"verb": verb, "targets": targets, "cwd": effective_cwd, "ambiguous": amb}
                else:
                    # xargs rm -rf fed from something we can't statically
                    # read (not a literal echo/printf) — destructive-shaped,
                    # target unknown. A9: ambiguous, A4 fallback applies.
                    j = 1
                    while j < len(stage) and stage[j].startswith("-"):
                        j += 1
                    if stage[j:j + 1] and stage[j] == "rm":
                        yield {"verb": "rm", "targets": [], "cwd": effective_cwd, "ambiguous": True}
            verb, targets, amb = _classify_stage(stage, effective_cwd)
            if verb:
                yield {"verb": verb, "targets": targets, "cwd": effective_cwd, "ambiguous": amb}
            prev = stage


# ----------------------------------------------------------------------------
# 3. Target resolution + overlap check
# ----------------------------------------------------------------------------

# Fix round 1 (bug-gate blocker 3): brace expansion (`{a,b}` and capped
# `{lo..hi}` ranges) BEFORE path resolution, so `rm -rf path/{node_modules,foo}`
# is checked against every alternative, not left as one literal (and
# nonexistent) path that trivially fails to overlap anything.
_BRACE_RE = re.compile(r"\{([^{}]*)\}")
MAX_BRACE_EXPANSIONS = 100


def _expand_one_brace(text: str):
    m = _BRACE_RE.search(text)
    if not m:
        return [text]
    inner = m.group(1)
    if "," in inner:
        parts = inner.split(",")
    else:
        rm_ = re.match(r"^(-?\d+)\.\.(-?\d+)$", inner)
        if not rm_:
            return [text]  # not a recognized brace shape (e.g. glob `{}`) — leave literal
        lo, hi = int(rm_.group(1)), int(rm_.group(2))
        step = 1 if hi >= lo else -1
        parts = [str(n) for n in range(lo, hi + step, step)]
    if not parts or len(parts) > MAX_BRACE_EXPANSIONS:
        return [text]
    pre, post = text[: m.start()], text[m.end() :]
    return [pre + p + post for p in parts]


def _expand_braces(word: str):
    frontier = [word]
    for _ in range(4):  # cap sequential/nested groups, not just alternatives
        nxt = []
        any_expanded = False
        for w in frontier:
            exp = _expand_one_brace(w)
            if exp != [w]:
                any_expanded = True
            nxt.extend(exp)
            if len(nxt) > MAX_BRACE_EXPANSIONS:
                return nxt[:MAX_BRACE_EXPANSIONS]
        frontier = nxt
        if not any_expanded:
            break
    return frontier


def _resolve_target(raw: str, cwd: str):
    if AMBIG in raw:
        return []
    if "{" in raw and "}" in raw:
        variants = _expand_braces(raw)
        if len(variants) > 1:
            out = []
            for v in variants:
                out.extend(_resolve_target(v, cwd))
            return out
    home = _home()
    val = _expand_word(raw, home)
    if not val.startswith("/"):
        val = os.path.join(cwd, val)
    if any(ch in val for ch in "*?["):
        try:
            matches = glob.glob(val)
        except Exception:
            matches = []
        return [_realpath(m) for m in matches]
    return [_realpath(val)]


def _worktree_remove_safe(path: str) -> bool:
    """Hub 2026-10-05: `git worktree remove` of a fully pushed, clean worktree
    only FREES disk, but the name-based marker heuristic (_has_markers) blocked it
    (a TRACKED .env.example looked like an untracked secret). Safe iff: path is a
    linked worktree (.git FILE); `git status --porcelain --ignored` shows no
    untracked file and no ignored marker-named file (so nothing unsaved or secret
    is lost); and HEAD is contained in a remote branch (pushed). Any doubt -> False
    (the block stands). Only "marker" kind entries are waived by the caller."""
    try:
        if not os.path.isfile(os.path.join(path, ".git")):
            return False
        def git(*a):
            r = subprocess.run(["git", "-C", path, *a], capture_output=True, text=True, timeout=20)
            return r.returncode, r.stdout
        rc, out = git("status", "--porcelain", "--ignored")
        if rc != 0:
            return False
        for line in out.splitlines():
            code, name = line[:2], line[3:].strip().rstrip("/")
            base = os.path.basename(name)
            if code == "??":
                return False
            if code == "!!":
                if (base.startswith(".env") or base == ".venv" or base.endswith(".pem")
                        or base == ".git-credentials"):
                    return False
                continue
            return False  # modified/staged tracked change
        rc, out = git("branch", "-r", "--contains", "HEAD")
        return rc == 0 and bool(out.strip())
    except Exception:
        return False


def _overlap(candidate: str, protected: dict):
    """Fix round 3 (bug-gate r3 item 1): each protected entry now carries a
    kind, "marker" or "dependency". A "dependency" entry (symlink target,
    launchd job path/worktree, a known hidden dep like ms-playwright) really
    is depended on as a whole tree, so a candidate sitting INSIDE it is
    still blocked -- the original, broader rule. A "marker" entry (an
    untracked-config-holding worktree root, or the marker file itself) only
    guards the exact path and anything that would take it down as a side
    effect (the candidate IS it, or CONTAINS it) -- a regenerable subdir
    like .next/node_modules/dist under a marked root is NOT "inside a
    marker" for this purpose, so clearing a build cache under an ordinary
    marked worktree is allowed again."""
    c = candidate.rstrip("/") or "/"
    if c in protected:
        reason, _kind = protected[c]
        return c, reason
    for p, val in protected.items():
        reason, kind = val
        pp = p.rstrip("/") or "/"
        if c == pp:
            return p, reason
        if kind == "dependency" and c.startswith(pp + os.sep):
            # candidate sits inside a dependency dir -- the whole tree
            # matters, so this is still a hit.
            return p, reason
        if pp.startswith(c + os.sep):
            # candidate is an ancestor of (CONTAINS) a protected path --
            # deleting it would take the protected path down too, for
            # either kind.
            return p, reason
    return None


def _under_fallback_root(resolved_path: str):
    rp = resolved_path.rstrip("/") or "/"
    for name, root in _fallback_roots().items():
        r = root.rstrip("/") or "/"
        if rp == r or rp.startswith(r + os.sep):
            return r
    return None


def _raw_hints_fallback_root(raw_cmd: str) -> bool:
    home = _home()
    hints = []
    for root in _fallback_roots().values():
        hints.append(root)
        if root.startswith(home):
            suffix = root[len(home):]
            hints.append("~" + suffix)
            hints.append("$HOME" + suffix)
            hints.append("${HOME}" + suffix)
    return any(h in raw_cmd for h in hints)


def _ambiguous_prefix_hints_root(raw: str, cwd: str) -> bool:
    """Fix round 1 (bug-gate blocker 2): for a raw target containing the
    AMBIG sentinel (an unresolved $()/backtick/$VAR reference), fail closed
    when the resolvable LITERAL PREFIX before that reference sits under (or
    is an ancestor of) an A4 fallback root, OR the command's own cwd does —
    per the plan's explicit promise that an unresolvable target near a
    protected root is denied, not silently allowed."""
    home = _home()
    prefix = raw.split(AMBIG, 1)[0]
    prefix = _expand_word(prefix, home)
    if prefix and not prefix.startswith("/"):
        prefix = os.path.join(cwd, prefix)
    elif not prefix:
        prefix = cwd
    prefix = (prefix or "/").rstrip("/") or "/"
    for root in _fallback_roots().values():
        r = root.rstrip("/") or "/"
        if prefix == r or prefix.startswith(r + os.sep) or r.startswith(prefix + os.sep):
            return True
    return False


def _cwd_under_fallback_root(cwd: str) -> bool:
    if not cwd:
        return False
    c = cwd.rstrip("/") or "/"
    for root in _fallback_roots().values():
        r = root.rstrip("/") or "/"
        if c == r or c.startswith(r + os.sep):
            return True
    return False


def _looks_destructive_raw(cmd: str) -> bool:
    """Last-resort, tokenizer-failed check — cheap substring test only."""
    return bool(
        re.search(r"\brm\s+-[a-zA-Z]*[rR]", cmd)
        or re.search(r"\btrash\b", cmd)
        or re.search(r"\bfind\b.*(-delete|-exec\s+.*rm)", cmd)
        or re.search(r"\bgit\s+worktree\s+remove\b", cmd)
        or re.search(r"\bgit\s+clean\s+-[a-zA-Z]*x", cmd)
    )


# ----------------------------------------------------------------------------
# 4. check_command — the public decision function
# ----------------------------------------------------------------------------

DENY_TAIL = "Disk cleanup goes through routines/worktree-janitor.sh or build-cache-sweep.sh."


def _deny(path: str, reason: str) -> str:
    return f"Blocked (disk guard): {path} is used by {reason}. {DENY_TAIL}"


def check_command(cmd: str, cwd: str = None):
    """Pure decision function. Never executes anything. Returns
    (blocked: bool, reason: str) — reason is '' when not blocked."""
    cwd = cwd or os.getcwd()

    try:
        commands = list(iter_simple_commands(cmd, cwd))
    except Exception:
        if _looks_destructive_raw(cmd) and _raw_hints_fallback_root(cmd):
            return True, _deny(
                "this command", "a target that could not be safely parsed, near a protected root"
            )
        return False, ""

    if not commands:
        return False, ""

    try:
        protected = get_protected_set()
        build_ok = True
    except Exception:
        protected = {}
        build_ok = False

    for c in commands:
        for raw_target in c["targets"]:
            for cand in _resolve_target(raw_target, c["cwd"]):
                if build_ok:
                    prot = protected
                    if c["verb"] == "worktree_remove" and _worktree_remove_safe(cand):
                        prot = {k: v for k, v in protected.items() if v[1] != "marker"}
                    hit = _overlap(cand, prot)
                    if hit:
                        path, reason = hit
                        return True, _deny(path, reason)
                else:
                    root = _under_fallback_root(cand)
                    if root:
                        return True, _deny(
                            root,
                            "under a protected root while the protected-path set could not be "
                            "built this run (fail-closed fallback: ~/projects, "
                            "~/.claude-worktrees, ~/Library/Caches, ~/.claude only)",
                        )
        if c["ambiguous"]:
            # Fix round 1 (bug-gate blocker 2): fail closed on an
            # unresolvable $()/backtick/$VAR/piped-input target when EITHER
            # (a) a specific target's resolvable literal prefix sits under a
            # protected root, (b) the command's cwd sits under one, or (c)
            # the raw command text otherwise names one (the pre-existing
            # check — still needed for the xargs-fed-from-a-pipe case, which
            # has no target word to inspect at all).
            per_target_hit = any(
                AMBIG in raw_target and _ambiguous_prefix_hints_root(raw_target, c["cwd"])
                for raw_target in c["targets"]
            )
            if per_target_hit or _cwd_under_fallback_root(c["cwd"]) or _raw_hints_fallback_root(cmd):
                return True, _deny(
                    "this command",
                    "a target that could not be resolved (unresolvable substitution, "
                    "variable reference, or piped input), near a protected root or cwd",
                )

    return False, ""


# ----------------------------------------------------------------------------
# CLI — used by worktree-janitor.sh / build-cache-sweep.sh one-liners, and by
# manual/test invocation.
# ----------------------------------------------------------------------------

def _main(argv):
    if len(argv) >= 2 and argv[0] == "--check":
        target = _realpath(os.path.expanduser(argv[1]))
        try:
            protected = get_protected_set()
        except Exception as e:
            print(f"BUILD-FAILED: {e}")
            return 3
        hit = _overlap(target, protected)
        if hit:
            path, reason = hit
            print(f"PROTECTED: {path} :: {reason}")
            return 0
        # Fix round 2 (bug-gate r2 item (b)): an explicit, exact token --
        # not the vaguer "OK" -- so a caller's shell wrapper can require an
        # EXACT stdout match (not just "some non-empty output") before ever
        # treating this as "safe to proceed." Paired with rc==1: a crashed
        # or syntax-broken module produces Python's own generic exit code,
        # which is ALSO 1, so rc alone can never be trusted as this signal
        # -- only rc==1 AND this literal token together can.
        print("NOT_PROTECTED")
        return 1

    if len(argv) >= 1 and argv[0] == "--list":
        try:
            protected = get_protected_set(force_rebuild="--fresh" in argv)
        except Exception as e:
            print(f"BUILD-FAILED: {e}")
            return 3
        for p, val in sorted(protected.items()):
            reason, kind = val
            print(f"{p}\t[{kind}]\t{reason}")
        return 0

    if len(argv) >= 2 and argv[0] == "--check-command":
        cwd = argv[2] if len(argv) >= 3 else os.getcwd()
        blocked, reason = check_command(argv[1], cwd)
        print(("BLOCKED: " + reason) if blocked else "OK")
        return 2 if blocked else 0

    print("usage: protected_paths.py --check <path> | --list [--fresh] | --check-command <cmd> [cwd]")
    return 64


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv[1:]))


# ----------------------------------------------------------------------------
# L-0926 (guard round 8): tokenizer-based CATASTROPHIC rm check. The regexes
# in safety-guard.py's CATASTROPHIC list only see `rm -rf ~` spelled in one
# narrow shape; round-7 bypasses (command substitution, xargs, find -exec,
# flag-case variants like -Rf, no-space terminators like `rm -rf ~;ls`) all
# slip past. This reuses the L-0925 shell tokenizer (wrappers, bash -c/eval
# unwrap, $VAR/substitution resolution, xargs combining) and flags any
# recursive rm whose operand IS the filesystem root, the home dir, or the
# direct children glob of either (`/*`, `~/*`). Pure: never executes anything.
# ----------------------------------------------------------------------------
def catastrophic_rm(cmd: str, cwd: str = None) -> bool:
    cwd = cwd or os.getcwd()
    home = _home().rstrip("/") or "/"
    bad = {"/", home, "/*", home + "/*"}
    for sc in iter_simple_commands(cmd, cwd):
        if sc.get("verb") != "rm":
            continue
        for t in sc.get("targets") or []:
            if AMBIG in t:
                continue
            w = _expand_word(t, home)
            for cand in _expand_braces(w):
                c = re.sub(r"/{2,}", "/", cand)
                if len(c) > 1:
                    c = c.rstrip("/") if not c.endswith("/*") else c
                if c in bad:
                    return True
    return False
