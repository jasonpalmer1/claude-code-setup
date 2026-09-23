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
# decision "block" = unchanged behavior for all 10 original entries: exit 2,
# the CLAUDE_MCP_GUARD_ALLOW=1 bypass still applies, zero regression. Hub
# audit amendment A3 (2026-09-16): converting these to "ask" was REJECTED —
# red line 1 requires the operator to TYPE the go-ahead having seen the total and
# destination first; a permission-prompt tap is weaker. These stay "block"
# exactly as before, permanently, not just for this build.
#
# decision "ask" = new: emits the PreToolUse ask JSON, exit 0 (the harness
# itself prompts the operator; no env-var bypass needed). Gmail send/reply/forward,
# Drive share_file, and the After Hours public post are literally the
# connector categories reference_mcp_usage_rules.md already calls "needs his
# go" but had zero technical backstop before this.
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
    ("gmail", "send_message", "ask"),
    ("gmail", "reply", "ask"),
    ("gmail", "forward", "ask"),
    ("drive", "share_file", "ask"),
    ("<community-project>", "post_find", "ask"),
    ("notion", "delete", "ask"),
    ("notion", "move", "ask"),
)

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
    return (
        f"Blocked (MCP red-tier guard, reference_mcp_usage_rules.md Tier 🚨): "
        f"'{tool_name}' matches the {server}/{action} rule — money-moving, mass-outbound, "
        "or destructive. This needs the operator's explicit per-action go IN THIS CONVERSATION "
        "(the exact target and amount/recipients shown to him first), is never covered by "
        "a standing grant, and is never delegated to a subagent. If he has already said go "
        "for this exact action, ask him to confirm once more here, or re-run this call with "
        "CLAUDE_MCP_GUARD_ALLOW=1 set for this process only after that confirmation."
    )


def _ask(tool_name, server, action):
    reason = (
        f"'{tool_name}' matches the {server}/{action} rule "
        "(reference_mcp_usage_rules.md Tier 🚨/⚠) — mass-outbound, publishes, or a "
        "destructive-shaped action. Confirm this is intended before it goes out."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))


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
            log(f"ASK {tool} (rule=supabase/destructive-sql)")
            _ask(tool, "supabase", "destructive-sql")
            sys.exit(0)

    rule = matched_rule(tool)
    if not rule:
        sys.exit(0)

    server, action, decision = rule

    if decision == "ask":
        log(f"ASK {tool} (rule={server}/{action})")
        _ask(tool, server, action)
        sys.exit(0)

    # decision == "block" — unchanged behavior, all 10 original entries,
    # never converted to ask (hub audit A3, 2026-09-16 — see RED_TIER above).
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
    ]
    # New for item 4 (2026-09-16 guard-fixes) -- must resolve to decision "ask",
    # not "block": the plan's own reasoning (mass-outbound/publish, not
    # money-moving/destructive) plus hub audit A3, which reserved "block" for
    # the original 10 only.
    must_ask = [
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
    for t in must_ask:
        rule = matched_rule(t)
        got = rule[2] if rule else None
        status = "PASS" if got == "ask" else "FAIL"
        if got != "ask":
            ok = False
        print(f"  [{status}] must-ask    {t} (decision={got})")
    for t in must_allow:
        blocked = t.lower().startswith("mcp__") and matched_rule(t) is not None
        status = "PASS" if not blocked else "FAIL"
        if blocked:
            ok = False
        print(f"  [{status}] must-allow  {t}")

    # bonus fix: case-sensitivity -- an all-caps tool name used to sail past
    # the mcp__ gate before it ever reached the (correctly case-insensitive)
    # matcher. Must now resolve exactly like its lowercase form.
    t = "MCP__CLAUDE_AI_GMAIL__SEND_MESSAGE"
    rule = matched_rule(t)
    got_ask = t.lower().startswith("mcp__") and rule is not None and rule[2] == "ask"
    status = "PASS" if got_ask else "FAIL"
    if not got_ask:
        ok = False
    print(f"  [{status}] must-ask (case-insensitive gate)  {t}")

    # Supabase destructive-SQL classifier -- independent of RED_TIER, keyed on
    # query/migration content, not the tool name.
    sql_cases = [
        ("DROP TABLE users;", True),
        ("SELECT * FROM users", False),
        ("delete from sessions where id=1", True),
        ("INSERT INTO logs (x) VALUES (1)", False),
    ]
    for sql, want_ask in sql_cases:
        got = bool(DESTRUCTIVE_SQL.search(sql))
        status = "PASS" if got == want_ask else "FAIL"
        if got != want_ask:
            ok = False
        print(f"  [{status}] supabase-sql-classifier  {sql!r} (ask={got}, want={want_ask})")

    print("RESULT:", "ALL PASS" if ok else "FAILURES ABOVE")
    sys.exit(0 if ok else 1)


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
