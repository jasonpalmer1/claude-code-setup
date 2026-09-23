#!/usr/bin/env python3
"""SubagentStop hook: report-file-or-fail (agent-framework-2026-09-06).

FIX 2 (2026-09-06, closes the bug-gate FAIL in ~/.claude/hub/reports/BG-framework.md):
the previous version fell back to "any report file modified in the last N
minutes" when a name match failed. That fallback matched an UNRELATED
worker's fresh report file and passed an agent that wrote no report at all
(BG-framework.md FAIL 2 — the proof gate was close to a no-op whenever more
than one worker had run in the last hour, which is this architecture's
default state). This version never does proximity matching. It reads the
EXACT report path out of the subagent's own brief (its first user/prompt
message) and checks only that one file.

FIELDS THE HARNESS ACTUALLY SENDS (verified live against
code.claude.com/docs/en/hooks.md, W2-hooks resume, re-confirmed here):

  SubagentStop stdin payload = {
    session_id, prompt_id, transcript_path, cwd, permission_mode, effort,
    hook_event_name, agent_id, agent_type, last_assistant_message
  }

`agent_name`, `agent_description`, `stop_reason`, `stop_time` are NOT
documented fields. The brief text and the true start time come from the
subagent's OWN transcript file on disk, not from the stop payload:

  ~/.claude/projects/*/{session_id}/subagents/agent-{agent_id}.jsonl

Its first JSON line carries the original prompt (message.content) and a real
ISO timestamp (the true start time), predating anything the stop payload
gives us.

Rules:
  - "report: none" anywhere in the brief -> exit 0 (exempt), regardless of
    whether a path is also present.
  - Any `~/.claude/hub/<...>/<name>.md` path found in the brief (reports/,
    strategy/, playtests/, ... — see REPORT_PATH_RE) -> that EXACT file must exist
    with mtime >= the agent's start time (small clock-skew buffer), else
    exit 2 naming the missing path.
  - No such path found in the brief (including: brief unreadable at all,
    e.g. missing transcript) -> exit 2 the FIRST time only, then exit 0.
    The brief itself is the thing that's broken, and a subagent cannot
    rewrite its own brief, so this refusal is unactionable by the only
    party that receives it: retrying can never fix it. The pre-2026-09-07
    version blocked unconditionally and the worker looped until it burned
    out (197 blocks vs 26 passes on 2026-09-07, ten agents stuck 12-25
    times each). One refusal gives the worker its one chance to volunteer
    a report anyway; the second stop is allowed through and the offending
    brief is appended to report-gate-badbriefs.log for the hub to fix at
    the DISPATCHER, which is where the defect actually lives.
    (the operator approved this change 2026-09-07: "stop after one refusal".)
    NOTE: the missing-or-stale branch below still fails closed every time.
    That refusal IS actionable — the worker can write the file — and it is
    observed working (block -> worker writes -> pass).
  - Fails OPEN (exit 0) only when stdin itself is malformed or empty -
    never as a substitute for a missing report, and never for any other
    internal error (those fail closed with exit 2 + a logged reason, so a
    hook bug can never silently defeat the gate the way FAIL 2 did).
"""
import json
import sys
import os
import re
import glob
import fcntl
import time
import datetime

LOG = os.path.expanduser("~/.claude/hub/report-gate.log")
STATE = os.path.expanduser("~/.claude/hub/report-gate-state.json")
BADBRIEFS = os.path.expanduser("~/.claude/hub/report-gate-badbriefs.log")
STATE_TTL_S = 86400  # prune agent_ids older than a day
# 2026-09-16 guard-fixes item 6: the audit reproduced a live bypass —
# "report: none" found ANYWHERE via .search(), including inside pasted/
# quoted third-party text, fully exempted a worker even when a real, never-
# written deliverable path was also named. Triple-quoted spans are stripped
# before matching, and the phrase must be alone on its own line (trailing
# period allowed, matching how dispatchers actually sign off), not merely
# present somewhere in the raw text.
EXEMPT_RE = re.compile(r"^\s*report\s*:\s*none\.?\s*$", re.I | re.M)


def _is_exempt(text: str) -> bool:
    stripped = re.sub(r'"""[\s\S]*?"""', "", text)
    stripped = re.sub(r"'''[\s\S]*?'''", "", stripped)
    return bool(EXEMPT_RE.search(stripped))
# Matches any .md report under the hub workspace: ~/.claude/hub/<...>/<name>.md
# (or the absolute-path form). WIDENED 2026-09-07 with the operator's yes.
# The old pattern accepted ONLY hub/reports/, so a worker that wrote a real,
# complete report to hub/strategy/ or hub/playtests/ was scored as having
# written nothing: refused at the gate AND never proof-checked. Three of the
# ten worst-blocked agents on 2026-09-07 were exactly that (42 of 197 blocks;
# the other 155 named no hub path at all, which is a dispatcher defect, not
# this regex). Widening makes the gate STRICTER in practice — those reports
# now get their must-exist check instead of silently skipping it.
REPORT_PATH_RE = re.compile(
    r"(?:~|" + "/Users" + r"/[^\s/]+)/\.claude/hub/(?:[\w.\-]+/)*[\w.\-]+\.md"
)
CLOCK_SKEW_MS = 2000  # a report legitimately written in the same second as start

# FIX 5 (2026-09-23, L-0809): a report must be CREATED during this run, not
# merely touched. Incident: a CSV-output worker's brief named exactly one .md
# path, a "Read first" plan doc owned by another lane. The gate picked it,
# found it stale, said "write it", and the worker APPENDED an addendum to
# that foreign plan to get a fresh mtime. Now:
#   - a picked path that existed before the run never passes, even if its
#     mtime is fresh (birth time is checked; an in-place append keeps it old),
#     and the refusal never tells the worker to edit it;
#   - every agent gets an AUTO report path, reports/workers/<agent_id>.md,
#     named in every refusal. It passes when created this run (needs a real
#     agent_id and a known start time, else it is never offered), so a
#     CSV/JSON-output brief, a no-path brief, or a brief whose only .md is a
#     citation all have a safe place to prove the work.
# Residual (documented, not closed): the Write tool replaces the inode, so a
# worker that fully OVERWRITES a pre-existing picked file with Write gets a
# fresh birth time and passes. The refusal text no longer invites that.
# Platform: st_birthtime exists on macOS/APFS (where this hook runs). On a
# filesystem without it, created_this_run falls back to mtime and the
# in-place-append bypass reopens there.
AUTO_DIR = os.path.expanduser("~/.claude/hub/reports/workers")

# FIX 4 (2026-09-07, the operator approved): FIX 3's widening broke path SELECTION.
# With the old reports/-only pattern, `.search()` (first match) was harmless
# because briefs rarely cited another reports/ file. Widened to all of hub/**,
# first-match grabs the BACKGROUND READING a brief cites near the top instead
# of the deliverable it names at the end. Verified live on agent
# a50127c331a959301: the brief cited hub/strategy/ad-network-applications-
# 2026-09-06.md (exists, old) and then named hub/strategy/<product-a>-grow-
# install-2026-09-07.md as its deliverable. First-match picked the citation ->
# missing-or-stale, which has no strike relief -> infinite loop, refusing the
# worker over a file that was never its job. Worse: if any lane touches a cited
# doc mid-run, the gate PASSES a worker that wrote no report at all - the
# unrelated-fresh-file false PASS that FIX 2 exists to prevent, re-entering
# through the front door. Selection: prefer the LAST cued match, else the LAST
# match. Briefs put citations first and the deliverable last.
CUE_RE = re.compile(
    r"(write|writes|writing|report\s+to|save|output|deliverable|land|file it|"
    r"produce|report\s+path|report\s*:)",
    re.I,
)
CUE_LOOKBACK = 240


def log(line: str) -> None:
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        pass


def extract_text(content) -> str:
    """message.content in the subagent's own transcript can be a plain string
    or a list of content blocks (text/tool_use/...); flatten to text only."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def find_subagent_transcript(session_id: str, agent_id: str):
    pattern = os.path.expanduser(
        f"~/.claude/projects/*/{session_id}/subagents/agent-{agent_id}.jsonl"
    )
    matches = glob.glob(pattern)
    return matches[0] if matches else None


def read_prompt_and_start(transcript_path: str):
    """First line of the subagent's own transcript = its opening user message.
    Returns (prompt_text, start_time_epoch_ms) — either may be None."""
    try:
        with open(transcript_path, "r") as f:
            first_line = f.readline()
        rec = json.loads(first_line)
        prompt_text = extract_text((rec.get("message") or {}).get("content"))
        ts = rec.get("timestamp")
        start_ms = None
        if ts:
            # e.g. "2026-09-02T17:24:32.665Z"
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            start_ms = dt.timestamp() * 1000
        return prompt_text, start_ms
    except Exception:
        return None, None


def select_report_path(text: str):
    """Pick the brief's DELIVERABLE path, not a background doc it merely cites.
    Returns (matched_path_or_None, rule) — rule is logged so the choice stays
    auditable instead of being a silent heuristic."""
    matches = list(REPORT_PATH_RE.finditer(text))
    if not matches:
        return None, "none"
    cued = [
        m for m in matches
        if CUE_RE.search(text[max(0, m.start() - CUE_LOOKBACK):m.start()])
    ]
    if cued:
        return cued[-1].group(0), "cued"
    return matches[-1].group(0), "last"


def auto_report_path(agent_id: str) -> str:
    safe = re.sub(r"[^\w\-]", "_", agent_id or "unknown-agent")
    return os.path.join(AUTO_DIR, f"{safe}.md")


def created_this_run(path: str, start_ms) -> bool:
    """True only if `path` exists and was born (and last written) at or after
    the agent's start. Birth time comes from st_birthtime (macOS/APFS); where
    the platform has none, fall back to mtime, which is the pre-FIX-5 rule."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    if start_ms is None:
        return True
    floor = start_ms - CLOCK_SKEW_MS
    birth_ms = getattr(st, "st_birthtime", st.st_mtime) * 1000
    return birth_ms >= floor and st.st_mtime * 1000 >= floor


def _has_real_content(path: str) -> bool:
    """True only if `path` exists and holds real (non-whitespace) content.
    Guards against a bare touch'd 0-byte file, or a whitespace-only file,
    passing this gate exactly as well as a genuine report would (hub
    decision per OPERATOR.md's own Definition of Done -- "proof, not a
    claim... an existing file" implies real content, not an empty
    placeholder. Bug-gate L-0810/guard-fixes item 6(b))."""
    try:
        return os.path.getsize(path) > 0 and bool(
            open(path, "r", errors="ignore").read().strip()
        )
    except Exception:
        return False


def bump_noreport_strike(agent_id: str) -> int:
    """Count how many times this agent_id has been refused for a missing
    report path. Returns the strike number (1 = first refusal). On any state
    failure returns 1, which blocks — a broken counter must not silently
    reopen the gate."""
    if not agent_id:
        return 1
    now = time.time()
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                try:
                    st = json.loads(raw) if raw.strip() else {}
                except Exception:
                    st = {}
                if not isinstance(st, dict):
                    st = {}
                st = {k: v for k, v in st.items()
                      if isinstance(v, dict) and now - v.get("ts", 0) < STATE_TTL_S}
                entry = st.get(agent_id) or {"n": 0}
                entry["n"] = int(entry.get("n", 0)) + 1
                entry["ts"] = now
                st[agent_id] = entry
                f.seek(0)
                f.truncate()
                f.write(json.dumps(st))
                return entry["n"]
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        log(f"ERROR strike-counter failed for id={agent_id}, treating as first: {e}")
        return 1


def record_bad_brief(agent_type: str, agent_id: str, prompt_text) -> None:
    """The defect is in the DISPATCHER's brief, not the worker. Persist enough
    for the hub to find and fix the caller."""
    try:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        head = (prompt_text or "")[:400].replace("\n", " ")
        with open(BADBRIEFS, "a") as f:
            f.write(f"{ts} agent={agent_type} id={agent_id} brief_head={head!r}\n")
    except Exception:
        pass


def main() -> int:
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        log("ERROR empty stdin, failing open")
        return 0
    try:
        data = json.loads(raw)
    except Exception as e:
        log(f"ERROR could not parse stdin, failing open: {e}")
        return 0

    try:
        session_id = data.get("session_id") or ""
        agent_id = data.get("agent_id") or ""
        agent_type = data.get("agent_type") or ""
        display_name = agent_type or agent_id or "unknown-agent"

        prompt_text, start_ms = None, None
        tpath = find_subagent_transcript(session_id, agent_id) if session_id and agent_id else None
        if tpath:
            prompt_text, start_ms = read_prompt_and_start(tpath)

        # --- exemption: "report: none" in the brief, checked before path search ---
        if prompt_text and _is_exempt(prompt_text):
            log(f"EXEMPT agent={display_name} id={agent_id} source=prompt")
            return 0

        # --- FIX 5: the per-agent auto report path counts if created this run ---
        # Only with a real agent_id AND a known start time: without either, the
        # path is shared or unverifiable, and passing it would let one agent's
        # file pass another (the FIX 2 false PASS). Bug-gate L-0809 FAIL 1.
        auto = auto_report_path(agent_id) if (agent_id and start_ms is not None) else None
        if auto and created_this_run(auto, start_ms) and _has_real_content(auto):
            log(f"PASS agent={display_name} id={agent_id} report={auto} pick=auto")
            return 0
        if auto:
            try:
                os.makedirs(AUTO_DIR, exist_ok=True)
            except Exception:
                pass
        auto_hint = auto or "a NEW file under ~/.claude/hub/reports/"

        # --- required: an exact report path named in the brief ---
        picked, pick_rule = select_report_path(prompt_text or "")
        if not picked:
            if not agent_id:
                # FIX 6 (L-0810): with no agent_id there is no transcript, no
                # auto path and no per-agent strike key, so the one-refusal
                # courtesy can neither be counted nor satisfied: pre-fix it
                # refused forever (strike stuck at 1). Any shared fallback key
                # (session, "anon") would let one id-less agent spend another's
                # refusal (bug-gate L-0810 r1 FAIL). Release at once, loudly,
                # with no shared state. The NAMED-path checks above/below are
                # untouched and still fail closed.
                record_bad_brief(display_name, agent_id, prompt_text)
                log(f"LOOP-BREAK agent={display_name} id= reason=no-agent-id "
                    f"session={data.get('session_id') or ''} action=allowed-through")
                return 0
            strike = bump_noreport_strike(agent_id)
            if strike >= 2:
                # A subagent cannot rewrite its own brief; refusing again can
                # only loop. Let it die, and blame the dispatcher on disk.
                record_bad_brief(display_name, agent_id, prompt_text)
                log(f"LOOP-BREAK agent={display_name} id={agent_id} "
                    f"reason=no-report-path-in-brief strike={strike} "
                    f"transcript_found={bool(tpath)} action=allowed-through")
                return 0
            log(f"BLOCK agent={display_name} id={agent_id} reason=no-report-path-in-brief "
                f"strike={strike} transcript_found={bool(tpath)}")
            print(
                "Brief must name its report path (~/.claude/hub/reports/<name>.md) "
                f"or say report: none. This is your ONE refusal: write a short "
                f"report (what you did, where your output is) to {auto_hint} and name "
                "that path in your final message, then stop. The next stop will "
                "be allowed through.",
                file=sys.stderr,
            )
            return 2

        report_path = os.path.expanduser(picked)
        ncand = len(REPORT_PATH_RE.findall(prompt_text or ""))

        if created_this_run(report_path, start_ms) and _has_real_content(report_path):
            log(f"PASS agent={display_name} id={agent_id} report={report_path} "
                f"pick={pick_rule} candidates={ncand}")
            return 0

        if created_this_run(report_path, start_ms) and not _has_real_content(report_path):
            # item 6(b): a fresh touch'd 0-byte (or whitespace-only) file must
            # not pass this check exactly as well as a real report would.
            log(f"BLOCK agent={display_name} id={agent_id} report={report_path} reason=empty-report "
                f"pick={pick_rule} candidates={ncand}")
            print(
                f"Worker's report file {report_path} exists but is empty (or whitespace-only) "
                "- write real content, then stop.",
                file=sys.stderr,
            )
            return 2

        if os.path.exists(report_path):
            # Existed before this run: it cannot prove this worker's work, and it
            # may be another lane's document. Never tell the worker to edit it.
            log(f"BLOCK agent={display_name} id={agent_id} report={report_path} "
                f"reason=preexisting pick={pick_rule} candidates={ncand} auto={auto}")
            print(
                f"The report path picked from your brief ({report_path}) already "
                "existed before you started, so it cannot prove your work. Do NOT "
                "edit or append to it - it may belong to another lane. Write a short "
                f"report (what you did, where your output is) to {auto_hint}, name that "
                "path in your final message, then stop.",
                file=sys.stderr,
            )
            return 2

        log(f"BLOCK agent={display_name} id={agent_id} report={report_path} reason=missing "
            f"pick={pick_rule} candidates={ncand} auto={auto}")
        print(
            f"Worker finished without its report file {report_path} - write it, then "
            f"stop. If that path is not your deliverable, write your report to {auto_hint} "
            "instead and never edit a file you were only told to read.",
            file=sys.stderr,
        )
        return 2

    except Exception as e:
        # Deliberately fail CLOSED here, not open: the spec restricts fail-open
        # to malformed/empty stdin only, so an internal bug in this hook can
        # never silently reopen the bypass FIX 2 closed.
        log(f"ERROR internal failure, failing closed: {e}")
        print("report-gate: internal error, failing closed - see report-gate.log", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
