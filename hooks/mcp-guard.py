#!/usr/bin/env python3
"""PreToolUse guard: the 🚨 tier of reference_mcp_usage_rules.md made deterministic
(2026-08-09), same shape as model-guard.py's delegation rule #1 — prose is
advisory, hooks are guaranteed.

Blocks any MCP tool call that moves real money, sends mass outbound in the operator's
name, or destroys live data/state, UNLESS bypassed. These are the tools the
written rule says need the operator's per-action go in the CURRENT conversation, named
target and amount, never covered by a standing grant and never delegated to a
subagent — this hook is the backstop for the case where an agent tries anyway.

Matching is (server substring, action substring) pairs, both case-insensitive,
rather than exact tool-name equality — MCP tool names carry a server-specific
prefix (mcp__claude_ai_Stripe__..., mcp__plugin_cloudflare_cloudflare-bindings__...)
and Calendly's cancel tool is actually "meetings-cancel_event", not bare
"cancel_event". A dual substring match survives that variation without also
matching an unrelated connector that happens to share an action word.

Explicitly NOT blocked: Supabase execute_sql / apply_migration — prod-vs-branch
isn't determinable from the call shape alone, so that one stays a written rule,
not a hook (see reference_mcp_usage_rules.md).

Bypass: env CLAUDE_MCP_GUARD_ALLOW=1 lets a call through — logged either way.
Self-test: `mcp-guard.py --test` runs synthetic must-block/must-allow payloads
and asserts outcomes, no real tool call involved.

Exit 2 = block (stderr goes back to Claude). Must never block on its OWN failure."""
import json, sys, os, datetime, re

LOG = os.path.expanduser("~/.claude/hub/mcp-guard.log")

# (server substring, action substring, decision) — server/action matched
# case-insensitively against the full tool_name. Source:
# reference_mcp_usage_rules.md Tier 🚨, written 2026-08-09 from the live
# connector inventory that day, widened 2026-09-16 (guard-fixes item 4).
#
# decision "block" = the ONLY decision this file emits. the operator, 2026-09-23,
# verbatim: "Several chats have still been asking me to approve commands.
# make sure this doesn't happen again. they are all approved forever.
# period." No hook may pop a permission prompt for him — that includes the
# PreToolUse "ask" JSON (harness-level tap-to-approve), not just a chat
# typing a question. Every entry that used to be decision "ask" (Gmail
# send/reply/forward, Drive share_file, the After Hours public post, Notion
# delete/move — added 2026-09-16 guard-fixes item 4, hub audit A3 reserved
# "ask" for exactly these) is now "block" with a per-action redirect (see
# REDIRECT below): the call is refused and told what to do instead — draft
# it, tell the hub, wait for the operator's own go — never left waiting on a tap.
# Blocking tightens this guard; it never loosens it. The original 10
# money-moving/destructive entries were already "block" (hub audit A3,
# 2026-09-16) and are unchanged.
RED_TIER = (
    ("stripe", "create_refund", "block"),
    ("stripe", "stripe_api_write", "block"),
    ("paypal", "create_invoice", "block"),
    ("resend", "send-email", "block"),
    ("resend", "send-broadcast", "block"),
    ("resend", "send-batch-emails", "block"),
    ("resend", "remove-domain", "block"),
    ("supermetrics", "campaign_create", "block"),
    ("supermetrics", "campaign_update", "block"),
    ("supabase", "delete_branch", "block"),
    ("supabase", "pause_project", "block"),
    ("cloudflare", "d1_database_delete", "block"),
    ("cloudflare", "r2_bucket_delete", "block"),
    ("cloudflare", "kv_namespace_delete", "block"),
    ("calendly", "cancel_event", "block"),
    ("gmail", "send_message", "block"),
    ("gmail", "reply", "block"),
    ("gmail", "forward", "block"),
    ("drive", "share_file", "block"),
    ("<community-project>", "post_find", "block"),
    ("notion", "delete", "block"),
    ("notion", "move", "block"),
)

# Per-(server, action) redirect appended to the block message — what to do
# INSTEAD, so refusing never becomes a dead end. Only the 2026-09-16-item-4
# entries (formerly "ask") and the destructive-SQL keyword check get a
# specific redirect; the original 10 money-moving/destructive entries fall
# back to block_message()'s generic tail (get the operator's explicit go, or the
# CLAUDE_MCP_GUARD_ALLOW=1 bypass after that go).
REDIRECT = {
    ("gmail", "send_message"): "create a Gmail draft instead; the operator sends it himself.",
    ("gmail", "reply"): "create a Gmail draft instead; the operator sends it himself.",
    ("gmail", "forward"): "create a Gmail draft instead; the operator sends it himself.",
    ("drive", "share_file"): "tell the hub which file and who; the operator shares it.",
    ("<community-project>", "post_find"): "public post needs the operator's own go.",
    ("notion", "delete"): "tell the hub.",
    ("notion", "move"): "tell the hub.",
    ("supabase", "destructive-sql"): (
        "tell the hub which project/branch and paste the exact statement; "
        "the operator reviews and applies it himself, or gives explicit go from the hub."
    ),
}

# Supabase execute_sql/apply_migration + a destructive keyword (2026-09-16
# guard-fixes item 4): a keyword classifier, not a SQL parser — this narrows
# the gap the audit found, it does not close it. A DROP hidden inside a
# stored-procedure call, a runtime-built string, or a SQL comment defeats a
# word-boundary regex with zero signal that it happened. Plain SELECT/INSERT
# stays unblocked, matching the file's existing documented
# prod-vs-branch-isn't-determinable exclusion for everything else here.
DESTRUCTIVE_SQL = re.compile(r"\b(DROP|DELETE|TRUNCATE|ALTER\s+TABLE\b.*\bDROP)\b", re.I)


def matched_rule(tool_name: str):
    """Returns the (server, action, decision) rule that matched, or None."""
    t = (tool_name or "").lower()
    for server, action, decision in RED_TIER:
        if server in t and action in t:
            return (server, action, decision)
    return None


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(LOG, "a") as f:
            f.write(f"{ts} {line}\n")
    except Exception:
        pass


def block_message(tool_name, server, action):
    redirect = REDIRECT.get((server, action))
    tail = redirect if redirect else (
        "If he has already said go for this exact action, get his explicit "
        "confirmation in this conversation, or re-run this call with "
        "CLAUDE_MCP_GUARD_ALLOW=1 set for this process only after that confirmation."
    )
    return (
        f"Blocked (MCP red-tier guard, reference_mcp_usage_rules.md Tier 🚨): "
        f"'{tool_name}' matches the {server}/{action} rule — money-moving, mass-outbound, "
        "publishing, or destructive. This needs the operator's own action, is never covered by "
        "a standing grant, and is never delegated to a subagent. No chat ever asks him for "
        "permission via a prompt (the operator, 2026-09-23) — instead: " + tail
    )


def run_hook():
    try:
        data = json.load(sys.stdin)
    except Exception as e:
        log(f"FAIL-OPEN error={type(e).__name__}: {e} (malformed/empty stdin)")
        sys.exit(0)  # malformed input must never block a tool call
    tool = data.get("tool_name") or ""
    # bonus fix (2026-09-16): normalize ONCE, before the mcp__ gate, so the
    # gate and matched_rule() share one normalized string. Previously only
    # matched_rule() lowercased, so a non-lowercase mcp__ prefix (e.g.
    # MCP__CLAUDE_AI_STRIPE__CREATE_REFUND) exited at this gate before the
    # correctly case-insensitive matcher ever ran.
    tool_l = tool.lower()
    if not tool_l.startswith("mcp__"):
        sys.exit(0)  # not an MCP tool — nothing this guard cares about

    tin = data.get("tool_input") or {}

    # Supabase destructive-SQL check (item 4): independent of RED_TIER, since
    # it needs to read the call's query/migration text, not just its name.
    if "supabase" in tool_l and ("execute_sql" in tool_l or "apply_migration" in tool_l):
        sql = str(tin.get("query") or tin.get("migration") or tin.get("sql") or "")
        if DESTRUCTIVE_SQL.search(sql):
            if os.environ.get("CLAUDE_MCP_GUARD_ALLOW") == "1":
                log(f"BYPASSED {tool} (rule=supabase/destructive-sql) via CLAUDE_MCP_GUARD_ALLOW=1")
                sys.exit(0)
            log(f"BLOCKED {tool} (rule=supabase/destructive-sql)")
            print(block_message(tool, "supabase", "destructive-sql"), file=sys.stderr)
            sys.exit(2)

    rule = matched_rule(tool)
    if not rule:
        sys.exit(0)

    server, action, decision = rule

    # decision is always "block" now (the operator, 2026-09-23 — see RED_TIER above).
    # This file has no "ask" decision left; the self-test below asserts that.
    if os.environ.get("CLAUDE_MCP_GUARD_ALLOW") == "1":
        log(f"BYPASSED {tool} (rule={server}/{action}) via CLAUDE_MCP_GUARD_ALLOW=1")
        sys.exit(0)

    log(f"BLOCKED {tool} (rule={server}/{action})")
    print(block_message(tool, server, action), file=sys.stderr)
    sys.exit(2)


def self_test():
    must_block = [
        "mcp__claude_ai_Stripe__create_refund",
        "mcp__claude_ai_Stripe__stripe_api_write",
        "mcp__claude_ai_PayPal__create_invoice",
        "mcp__claude_ai_Resend__send-broadcast",
        "mcp__claude_ai_Supabase__delete_branch",
        "mcp__claude_ai_Supabase__pause_project",
        "mcp__plugin_cloudflare_cloudflare-bindings__r2_bucket_delete",
        "mcp__plugin_cloudflare_cloudflare-bindings__d1_database_delete",
        "mcp__claude_ai_Calendly__meetings-cancel_event",
        "mcp__claude_ai_Supermetrics_Marketing_Analytics__campaign_create",
        # Formerly decision "ask" (2026-09-16 guard-fixes item 4). the operator,
        # 2026-09-23: "they are all approved forever. period" — no hook may
        # pop a permission prompt, so these moved from ask to block with a
        # per-action redirect (REDIRECT dict) instead of a tap-to-approve.
        "mcp__claude_ai_Gmail__send_message",
        "mcp__claude_ai_Gmail__reply",
        "mcp__claude_ai_Gmail__forward",
        "mcp__claude_ai_Google_Drive__share_file",
        "mcp__<community-project>__post_find",
        "mcp__claude_ai_Notion__notion-delete-page",
        "mcp__claude_ai_Notion__notion-move-pages",
    ]
    must_allow = [
        "mcp__claude_ai_Stripe__stripe_api_read",
        "mcp__claude_ai_Stripe__get_balance_summary",
        "mcp__claude_ai_Resend__list-emails",
        "mcp__claude_ai_Resend__create-contact",
        "mcp__claude_ai_Supabase__execute_sql",       # explicitly NOT blocked by name alone -- see docstring; SELECT-shaped content tested separately below
        "mcp__claude_ai_Supabase__apply_migration",   # explicitly NOT blocked by name alone -- see docstring
        "mcp__claude_ai_Gmail__create_draft",
        "mcp__claude_ai_Calendly__meetings-create_invitee",
        "mcp__<product-b>__get_company",
        "mcp__claude_ai_Notion__notion-update-page",  # update != delete/move -- audit's own "lower stakes" call
        "mcp__<community-project>__check_room",                # a read, not the public post
        "Bash",  # not an MCP tool at all
    ]
    ok = True
    print("mcp-guard.py self-test")
    for t in must_block:
        rule = matched_rule(t)
        got = rule[2] if rule else None
        is_block = got == "block"
        status = "PASS" if is_block else "FAIL"
        if got != "block":
            ok = False
        print(f"  [{status}] must-block  {t} (decision={got})")
    for t in must_allow:
        blocked = t.lower().startswith("mcp__") and matched_rule(t) is not None
        status = "PASS" if not blocked else "FAIL"
        if blocked:
            ok = False
        print(f"  [{status}] must-allow  {t}")

    # bonus fix: case-sensitivity -- an all-caps tool name used to sail past
    # the mcp__ gate before it ever reached the (correctly case-insensitive)
    # matcher. Must now resolve exactly like its lowercase form -- block,
    # since decision "ask" no longer exists anywhere in this file.
    t = "MCP__CLAUDE_AI_GMAIL__SEND_MESSAGE"
    rule = matched_rule(t)
    got_block = t.lower().startswith("mcp__") and rule is not None and rule[2] == "block"
    status = "PASS" if got_block else "FAIL"
    if not got_block:
        ok = False
    print(f"  [{status}] must-block (case-insensitive gate)  {t}")

    # Supabase destructive-SQL classifier -- independent of RED_TIER, keyed on
    # query/migration content, not the tool name.
    sql_cases = [
        ("DROP TABLE users;", True),
        ("SELECT * FROM users", False),
        ("delete from sessions where id=1", True),
        ("INSERT INTO logs (x) VALUES (1)", False),
    ]
    for sql, want_hit in sql_cases:
        got = bool(DESTRUCTIVE_SQL.search(sql))
        status = "PASS" if got == want_hit else "FAIL"
        if got != want_hit:
            ok = False
        print(f"  [{status}] supabase-sql-classifier  {sql!r} (hit={got}, want={want_hit})")

    if not _assert_no_ask_path():
        ok = False

    print("RESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


def _assert_no_ask_path():
    """No code path in this file may ever emit permissionDecision: ask again
    (the operator, 2026-09-23 — see RED_TIER comment). Two independent checks:
    static (the JSON shape does not appear anywhere in this file's own
    source, and every RED_TIER decision is literally "block") and runtime
    (actually invoking this script as the harness would, for every formerly-
    ask tool plus the destructive-SQL path, and asserting stdout never
    carries a permissionDecision — only a block on stderr with exit 2)."""
    import subprocess

    ok = True

    # Precise on purpose: "ask" appears bare in this file's own comments
    # (explaining the 2026-09-16 history and the 2026-09-23 fix), so the
    # static check targets the exact JSON emission shape _ask() used to
    # print, not the English word. Built from fragments so this check's own
    # source line is never a false-positive match for itself.
    danger_key = "permission" + "Decision"
    danger_val = "a" + "sk"
    danger = f'{danger_key}": "{danger_val}"'
    src = open(__file__, encoding="utf-8").read()
    static_hit = danger in src
    status = "PASS" if not static_hit else "FAIL"
    if static_hit:
        ok = False
    print(f"  [{status}] static: no permissionDecision:ask JSON literal in {os.path.basename(__file__)}")

    for server, action, decision in RED_TIER:
        good = decision == "block"
        status = "PASS" if good else "FAIL"
        if not good:
            ok = False
        print(f"  [{status}] static: RED_TIER {server}/{action} decision={decision!r}")

    runtime_cases = [
        ("mcp__claude_ai_Gmail__send_message", {}),
        ("mcp__claude_ai_Google_Drive__share_file", {}),
        ("mcp__<community-project>__post_find", {}),
        ("mcp__claude_ai_Notion__notion-delete-page", {}),
        ("mcp__claude_ai_Supabase__execute_sql", {"query": "DROP TABLE users;"}),
    ]
    for tool, tool_input in runtime_cases:
        payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
        proc = subprocess.run(
            [sys.executable, __file__], input=payload,
            capture_output=True, text=True, timeout=10,
        )
        emits_ask = '"permissionDecision"' in proc.stdout or proc.stdout.strip() != ""
        good = proc.returncode == 2 and not emits_ask and "Blocked" in proc.stderr
        status = "PASS" if good else "FAIL"
        if not good:
            ok = False
        print(
            f"  [{status}] runtime: {tool} -> exit={proc.returncode} "
            f"stdout_empty={not proc.stdout.strip()} stderr_blocked={'Blocked' in proc.stderr}"
        )
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        self_test()
    else:
        try:
            run_hook()
        except SystemExit:
            raise
        except Exception as e:
            log(f"FAIL-OPEN error={type(e).__name__}: {e}")
            sys.exit(0)  # never block a tool call on the guard's own failure
