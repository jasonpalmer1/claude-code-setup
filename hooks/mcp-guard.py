#!/usr/bin/env python3
"""PreToolUse guard: makes a written MCP risk policy deterministic instead of advisory.

If you connect MCP tools that can move real money, send mass outbound in your
name, or destroy live data/state, a prose rule saying "ask me first" is easy
to skip under time pressure — especially inside a subagent that never sees the
rule at all. This hook is the backstop: it blocks the tool call outright
unless explicitly bypassed for that one call.

Matching is (server substring, action substring) pairs, both case-insensitive,
against the full tool name, rather than exact tool-name equality — MCP tool
names carry a server-specific prefix (e.g. `mcp__myorg_Stripe__create_refund`)
and some connectors bury the action in a compound name (Calendly's cancel tool
is `meetings-cancel_event`, not bare `cancel_event`). A dual substring match
survives that variation without also matching an unrelated connector that
happens to share an action word.

RED_TIER below is a STARTER example, not a real inventory — swap it for your
own connectors and actions. Anything genuinely ambiguous from the call shape
alone (e.g. "run this SQL" could be a prod database or a disposable branch)
doesn't belong in this list; keep that as a written rule instead, since a hook
can't tell the difference and a wrong block just trains you to bypass it.

Bypass: env CLAUDE_MCP_GUARD_ALLOW=1 lets a call through — logged either way,
so a bypass is always visible in the log even when it was the right call.
Self-test: `mcp-guard.py --test` runs synthetic must-block/must-allow payloads
and asserts outcomes, no real tool call involved.

Exit 2 = block (stderr goes back to Claude). Must never block on its OWN failure."""
import json, sys, os, datetime

LOG = os.path.expanduser("~/.claude/hub/mcp-guard.log")

# (server substring, action substring) — both matched case-insensitively
# against the full tool_name. STARTER EXAMPLES ONLY: edit this to match your
# own connector inventory and your own written money/mass-send/destructive
# policy. Categories worth covering: anything that moves money, anything that
# sends outbound at scale (bulk email, broadcasts), and anything that destroys
# state you can't get back (dropping a database, deleting a bucket, canceling
# a live commitment).
RED_TIER = (
    ("stripe", "create_refund"),
    ("stripe", "charge"),
    ("paypal", "create_invoice"),
    ("email", "send-broadcast"),
    ("email", "send-batch"),
    ("email", "remove-domain"),
    ("database", "delete_branch"),
    ("database", "pause_project"),
    ("storage", "bucket_delete"),
    ("storage", "namespace_delete"),
    ("calendar", "cancel_event"),
)


def matched_rule(tool_name: str):
    """Returns the (server, action) rule that matched, or None."""
    t = (tool_name or "").lower()
    for server, action in RED_TIER:
        if server in t and action in t:
            return (server, action)
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
        f"Blocked (MCP red-tier guard): '{tool_name}' matches the {server}/{action} rule — "
        "money-moving, mass-outbound, or destructive. This class of action needs your "
        "explicit per-action go IN THIS CONVERSATION (the exact target and amount/recipients "
        "shown to you first), should never be covered by a standing grant, and should never "
        "be delegated to a subagent. If you've already decided to do this exact action, "
        "confirm it once more here, or re-run this call with CLAUDE_MCP_GUARD_ALLOW=1 set "
        "for this process only after that confirmation."
    )


def run_hook():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)  # malformed input must never block a tool call
    tool = data.get("tool_name") or ""
    if not tool.startswith("mcp__"):
        sys.exit(0)  # not an MCP tool — nothing this guard cares about

    rule = matched_rule(tool)
    if not rule:
        sys.exit(0)

    server, action = rule
    if os.environ.get("CLAUDE_MCP_GUARD_ALLOW") == "1":
        log(f"BYPASSED {tool} (rule={server}/{action}) via CLAUDE_MCP_GUARD_ALLOW=1")
        sys.exit(0)

    log(f"BLOCKED {tool} (rule={server}/{action})")
    print(block_message(tool, server, action), file=sys.stderr)
    sys.exit(2)


def self_test():
    must_block = [
        "mcp__myorg_Stripe__create_refund",
        "mcp__myorg_Stripe__charge",
        "mcp__myorg_PayPal__create_invoice",
        "mcp__myorg_Email__send-broadcast",
        "mcp__myorg_Database__delete_branch",
        "mcp__myorg_Database__pause_project",
        "mcp__plugin_storage__bucket_delete",
        "mcp__plugin_storage__namespace_delete",
        "mcp__myorg_Calendar__meetings-cancel_event",
    ]
    must_allow = [
        "mcp__myorg_Stripe__get_balance_summary",
        "mcp__myorg_Email__list-emails",
        "mcp__myorg_Email__create-contact",
        "mcp__myorg_Database__execute_sql",       # ambiguous from call shape alone — see docstring
        "mcp__myorg_Calendar__meetings-create_invitee",
        "Bash",  # not an MCP tool at all
    ]
    ok = True
    print("mcp-guard.py self-test")
    for t in must_block:
        blocked = matched_rule(t) is not None
        status = "PASS" if blocked else "FAIL"
        if not blocked:
            ok = False
        print(f"  [{status}] must-block  {t}")
    for t in must_allow:
        blocked = t.startswith("mcp__") and matched_rule(t) is not None
        status = "PASS" if not blocked else "FAIL"
        if blocked:
            ok = False
        print(f"  [{status}] must-allow  {t}")
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
        except Exception:
            sys.exit(0)  # never block a tool call on the guard's own failure
