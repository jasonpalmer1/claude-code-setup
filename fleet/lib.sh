# ~/.claude/fleet/lib.sh — shared logic for spawn/run. Sourced, never executed
# directly: defining these as pure functions (no top-level side effects) means
# each one can be exercised standalone —
#   bash -c 'source ~/.claude/fleet/lib.sh; fleet_check_depth 1 0'
# — without ever driving VS Code, which spawn's own top-level code does.
#
# Added 2026-08-09 (spawn safety audit): depth cap, repo locks, concurrent
# ceiling. Three independent guardrails against runaway/overlapping fleets.
#
# 2026-09-03 (fleet-folder-policy, hub/strategy/fleet-folder-policy-2026-09-03.md):
# two more additions closing the gap that let pids 5533 and 57236 BOTH sit live
# in ~/projects/<product-a> at once (found via `lsof -a -p <pid> -d cwd` over
# every live claude pid):
#   - fleet_check_lock is now LIVENESS-based, not TTL-based (see below).
#   - fleet_resolve_worktree gives every lane its own git worktree, so two
#     lanes are never even ASKED to share one directory in the first place.

FLEET_LOCKS_DIR="${FLEET_LOCKS_DIR:-$HOME/.claude/fleet/locks}"
FLEET_LOCK_TTL=86400  # 24h. No longer used to decide staleness in fleet_check_lock
                       # (a lock is stale iff its owner is provably gone, not because
                       # it's old) -- kept only for fleet_check_ceiling's "fresh lock"
                       # window, which is a capacity heuristic, not a mutex.

# Cross-platform mtime (macOS/BSD stat vs GNU stat).
_fleet_mtime() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0
}

# ps `etime=` (e.g. "3-01:02:03", "01:02:03", "02:03") -> seconds.
_fleet_etime_secs() {
  awk -F'[-:]' '{
    if (NF==2) print $1*60+$2;
    else if (NF==3) print $1*3600+$2*60+$3;
    else if (NF==4) print $1*86400+$2*3600+$3*60+$4;
    else print 0 }' <<<"$1"
}

# Epoch seconds a still-alive pid started, empty if the pid does not exist.
# Precision is 1s (ps etime granularity) -- callers compare with slack, not
# exact equality.
_fleet_pid_start_epoch() {
  local etime secs
  etime=$(ps -o etime= -p "$1" 2>/dev/null | tr -d ' ')
  [ -n "$etime" ] || return 1
  secs="$(_fleet_etime_secs "$etime")"
  echo $(( $(date +%s) - secs ))
}

fleet_lock_path() {  # <abs-dir> -> prints the lock file path
  local dir="$1" hash
  hash="$(printf '%s' "$dir" | shasum -a 1 | awk '{print $1}')"
  printf '%s/%s.lock\n' "$FLEET_LOCKS_DIR" "$hash"
}

# Depth cap — refuse to create a job at depth >= 2 unless forced.
# Args: caller_depth force(0/1). On success prints the NEW job's depth to
# stdout and returns 0. On refusal prints the reason to stderr and returns 1.
fleet_check_depth() {
  local caller_depth="${1:-0}" force="${2:-0}"
  [[ "$caller_depth" =~ ^[0-9]+$ ]] || caller_depth=0
  local new_depth=$((caller_depth + 1))
  if [ "$new_depth" -ge 2 ] && [ "$force" != "1" ]; then
    echo "spawn: refused — this session is already at fleet depth $caller_depth;" \
         "spawning would create a depth-$new_depth job (a peer spawning a peer)." \
         "Pass --force-depth to do this deliberately." >&2
    return 1
  fi
  echo "$new_depth"
}

# Is a lock's holder still alive? Returns 0 alive, 1 dead, 2 unknowable (pid null = a lock
# written before this fix, or a session that died before stamping). Callers MUST distinguish
# 2 from 1 -- "unknown" is not "dead", and treating it as dead is how you steal a live lock.
#
# 2026-09-03: also guards against a RECYCLED pid. kill -0 only proves *some* process owns
# that pid number right now, not that it's the same process that wrote the lock — pid
# numbers wrap and get reused. fleet_stamp_lock_pid records the owning process's start
# time (pid_start) alongside its pid; if the pid is alive but its CURRENT start time
# doesn't match what was recorded, it's a different process that happens to have inherited
# the old number, and the real owner is gone (return 1, not 0).
fleet_lock_alive() {  # <lockfile>
  local pid pid_start cur_start diff
  pid=$(sed -n 's/.*"pid":\([0-9]*\).*/\1/p' "$1" 2>/dev/null)
  [ -n "$pid" ] || return 2
  kill -0 "$pid" 2>/dev/null || return 1

  pid_start=$(sed -n 's/.*"pid_start":\([0-9]*\).*/\1/p' "$1" 2>/dev/null)
  # No recorded start time (a pre-2026-09-03 lock stamped before this field existed,
  # or one whose pid_start was itself unresolvable at stamp time) -- kill -0 succeeding
  # is real evidence and the best we have; call it alive rather than unknowable. This
  # loses recycled-pid protection for that one lock, never gains a false "dead".
  [ -n "$pid_start" ] || return 0

  cur_start="$(_fleet_pid_start_epoch "$pid")" || return 1   # pid vanished mid-check
  diff=$(( cur_start - pid_start )); [ "$diff" -lt 0 ] && diff=$(( -diff ))
  # ps etime has 1s granularity and the write-time and check-time samples are never
  # taken at the exact same instant -- a few seconds of slack is the real process,
  # not a different one.
  [ "$diff" -le 5 ] || return 1
  return 0
}

# Repo lock — refuse spawning into a dir another LIVE job already owns.
# Args: lockfile steal(0/1). On success prints one word (none|dead|stolen-live|
# stolen-unknown) to stdout — informational only, callers don't need to branch
# on it — and returns 0. On refusal, the reason goes to stderr and it returns 1.
#
# 2026-09-03 REWRITE (fleet-folder-policy): this used to compare the lockfile's
# mtime against FLEET_LOCK_TTL and print "stale — ignoring, ok to overwrite" once
# it passed 24h, REGARDLESS of whether the owning session was still running.
# Live proof it was wrong: pids 5533 and 57236 were BOTH found live in
# ~/projects/<product-a> at once. A lock is now held exactly as long as its
# owning process is alive, checked via fleet_lock_alive — never by the clock.
# The unknowable case (pid never recorded) has NO time-based auto-clear anymore:
# it fails toward "still locked", never toward "free" — see the comment inline.
fleet_check_lock() {
  local lockfile="$1" steal="${2:-0}"
  if [ ! -e "$lockfile" ]; then
    echo "none"
    return 0
  fi
  local owner
  owner="$(grep -o '"name" *: *"[^"]*"' "$lockfile" 2>/dev/null | head -1 | sed -E 's/.*"([^"]*)"$/\1/')"
  [ -n "$owner" ] || owner="(unknown)"

  fleet_lock_alive "$lockfile"
  local alive_rc=$?

  if [ "$alive_rc" -eq 0 ]; then
    if [ "$steal" = "1" ]; then
      echo "spawn: ⚠ STEALING A LIVE LOCK — '$owner' is CONFIRMED ALIVE (pid + start time verified)" \
           "and still running in this directory right now. Overwriting the lock will not stop it;" \
           "it keeps working and will happily overwrite the lock again on its own exit. Only" \
           "proceed if you mean to run two sessions in this directory on purpose." >&2
      echo "stolen-live"
      return 0
    fi
    echo "spawn: refused — dir is locked by '$owner', and that process is CONFIRMED ALIVE" \
         "(pid + start time verified). Pass --steal to override anyway (it will print a loud" \
         "warning, not act quietly)." >&2
    return 1
  fi

  if [ "$alive_rc" -eq 1 ]; then
    echo "spawn: dead lock from '$owner' (owning pid no longer running, or a different process" \
         "now holds that pid number) — reclaiming automatically" >&2
    echo "dead"
    return 0
  fi

  # alive_rc == 2: pid was never recorded (null) -- e.g. spawn wrote the lock but the
  # launched session hasn't reached fleet_stamp_lock_pid yet, or this is a pre-2026-09-03
  # lock format. Liveness is genuinely unknowable here. The old code treated "unknowable
  # + old" as "stale, ok to overwrite" -- exactly the check that could never come out
  # negative that caused this whole bug. There is no time-based auto-clear anymore for
  # this case: it is always treated as still held, and --steal is the only way past it,
  # same as a provably-alive lock just without the "CONFIRMED ALIVE" language, since we
  # cannot honestly claim to know that.
  local age; age=$(( $(date +%s) - $(_fleet_mtime "$lockfile") ))
  if [ "$steal" = "1" ]; then
    echo "spawn: stealing lock from '$owner' (age $((age / 60))m; liveness UNKNOWN — no pid" \
         "recorded, so this could not be verified either way)" >&2
    echo "stolen-unknown"
    return 0
  fi
  echo "spawn: refused — dir is locked by '$owner' (age $((age / 60))m). Liveness cannot be" \
       "determined (no pid recorded), so this is treated as STILL HELD, not free. Pass" \
       "--steal to override." >&2
  return 1
}

# Concurrent ceiling — refuse when >= 6 fresh locks already exist.
# Args: force(0/1). Prints the current fresh count to stdout on success.
# NOTE: "fresh" here is still the TTL/age heuristic (a capacity signal, not a
# mutex) — a lock outliving FLEET_LOCK_TTL stops counting toward the ceiling
# even if its owner is still alive. That's a known, separate gap from the lock
# mutex above (out of scope for the 2026-09-03 fix); the mutex itself never
# relies on this being accurate.
fleet_check_ceiling() {
  local force="${1:-0}"
  local now fresh=0 f mtime age
  now="$(date +%s)"
  if [ -d "$FLEET_LOCKS_DIR" ]; then
    for f in "$FLEET_LOCKS_DIR"/*.lock; do
      [ -e "$f" ] || continue
      mtime="$(_fleet_mtime "$f")"
      age=$((now - mtime))
      [ "$age" -lt "$FLEET_LOCK_TTL" ] && fresh=$((fresh + 1))
    done
  fi
  if [ "$fresh" -ge 6 ] && [ "$force" != "1" ]; then
    echo "spawn: refused — $fresh fresh fleet locks already exist (ceiling 6)." \
         "Pass --force to spawn anyway." >&2
    return 1
  fi
  echo "$fresh"
}

# Fill in the real PID (and its start time) once the session process exists. spawn writes
# the lock before the session is born (pid unknown, honestly null); `fleet/run` calls this
# the moment it starts, because THIS process is the session and the answer is knowable
# right here. pid_start lets fleet_lock_alive tell a live owner from a recycled pid number.
fleet_stamp_lock_pid() {  # <lockfile> <pid>
  local lockfile="$1" pid="$2" start
  [ -f "$lockfile" ] || return 0
  start="$(_fleet_pid_start_epoch "$pid")" || start=0
  local tmp="$lockfile.tmp.$$"
  sed -e 's/"pid":null/"pid":'"$pid"'/' \
      -e 's/"pid_start":null/"pid_start":'"$start"'/' \
      "$lockfile" > "$tmp" 2>/dev/null && mv "$tmp" "$lockfile"
}

fleet_write_lock() {  # <lockfile> <dir> <name>
  local lockfile="$1" dir="$2" name="$3"
  mkdir -p "$(dirname "$lockfile")"
  printf '{"dir":"%s","name":"%s","pid":null,"pid_start":null,"created":%s}\n' \
    "$dir" "$name" "$(date +%s)" > "$lockfile"
}

# fleet_resolve_worktree — decide the actual session directory for a spawn target.
# Args: requested_dir lane_name in_place(0/1). Prints the FINAL directory to use
# (one line) on stdout and returns 0. On a fatal collision it prints the reason
# to stderr and returns 1 — callers must treat that as a refusal, same as the
# lock/depth/ceiling checks.
#
# Added 2026-09-03 (fleet-folder-policy): a git repo's root is no longer a
# session's cwd by default. Each lane gets `<repo>-wt/<lane>` on branch
# `lane/<lane>`, off origin/main, created if absent and reused if already
# checked out at that exact path. This is what makes the (now liveness-based)
# lock in fleet_check_lock trustworthy again — two lanes are never even asked
# to share one directory, so a live process's cwd unambiguously identifies
# which lane owns it (fleet-reaper.sh's newest_transcript() and CWD_COUNT logic
# depend on exactly this).
fleet_resolve_worktree() {
  local dir="$1" name="$2" in_place="${3:-0}"

  # Not a git repo at all (e.g. spawning into $HOME) -- unaffected, always has
  # been, always will be. Nothing to isolate.
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "$dir"; return 0; }

  local toplevel
  toplevel="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)"
  [ -n "$toplevel" ] || { echo "$dir"; return 0; }   # couldn't resolve -- don't touch

  if [ -f "$toplevel/.git" ]; then
    # $dir is already inside a LINKED worktree: every `git worktree add` checkout
    # gets a .git that is a plain gitdir-pointer FILE, unlike the main checkout's
    # .git DIRECTORY. Already isolated -- use as-is. This is what makes `-d`
    # pointing at an existing lane worktree idempotent instead of nesting a
    # worktree inside a worktree.
    echo "$dir"
    return 0
  fi

  # $dir sits inside the MAIN worktree -- the root, reserved (2026-09-03) for the
  # hub's own merges/deploys, never a lane's cwd by default.
  if [ "$in_place" = "1" ]; then
    echo "spawn: ⚠ --in-place — running directly in the repo ROOT ($toplevel), not an" \
         "isolated lane worktree. Gives up: git-level isolation from every other session" \
         "that also touches this root, including the hub's own merges/deploys -- a stray" \
         "commit, checkout, or stash here can collide with someone else's. Use only when" \
         "the task genuinely needs the root itself." >&2
    echo "$dir"
    return 0
  fi

  local wt_root="${toplevel}-wt"
  local wt_dir="${wt_root}/${name}"
  local branch="lane/${name}"
  mkdir -p "$wt_root"

  # Already registered as a worktree of THIS repo on the lane's branch? Reuse it --
  # this is what makes a repeat spawn under the same lane name land back in the
  # same place instead of erroring on "branch already checked out" every time.
  local existing_path
  existing_path="$(git -C "$toplevel" worktree list --porcelain 2>/dev/null \
    | awk -v b="refs/heads/$branch" '/^worktree /{p=$2} /^branch /{if ($2==b) print p}')"

  if [ -n "$existing_path" ]; then
    if [ "$existing_path" != "$wt_dir" ]; then
      # Branch lane/<name> is checked out somewhere OTHER than the expected
      # <repo>-wt/<name> -- a collision git itself would refuse anyway ("branch
      # already checked out"). Surface it plainly instead of a cryptic failure
      # three lines down, and never silently pick one path over the other.
      echo "spawn: refused — branch '$branch' is already checked out at" \
           "'$existing_path', not the expected '$wt_dir'. Resolve by hand" \
           "(git -C $toplevel worktree list) before spawning '$name' here." >&2
      return 1
    fi
    echo "$wt_dir"
    return 0
  fi

  if [ -e "$wt_dir" ]; then
    # Path exists but git doesn't recognize it as a worktree on this branch --
    # could be a leftover plain directory, an unrelated file, or (if two repos
    # share a name) somebody else's worktree entirely. Never silently reuse it:
    # a name collision that always resolves quietly is the same shape of bug
    # this whole fix exists to close.
    echo "spawn: refused — '$wt_dir' already exists and is not a worktree on" \
         "branch '$branch' (checked: git -C $toplevel worktree list). Remove or" \
         "rename it by hand, or pick a different lane name." >&2
    return 1
  fi

  git -C "$toplevel" fetch origin main >/dev/null 2>&1 || true   # best-effort; offline isn't fatal
  if ! git -C "$toplevel" worktree add -b "$branch" "$wt_dir" origin/main >/dev/null 2>&1; then
    # -b fails if the branch already exists locally (e.g. left over from a lane
    # worktree that was later removed by hand while the branch itself lived on).
    # Retry checking out the existing branch instead of creating it.
    if ! git -C "$toplevel" worktree add "$wt_dir" "$branch" >/dev/null 2>&1; then
      echo "spawn: refused — 'git worktree add' failed for '$wt_dir' on branch" \
           "'$branch'. Run it by hand in $toplevel to see why." >&2
      return 1
    fi
  fi

  echo "$wt_dir"
  return 0
}
