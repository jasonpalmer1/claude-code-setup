#!/usr/bin/env python3
"""Additively merge settings.json.template into a settings.json (install.sh step).

Never overwrites: existing keys, hooks, and permissions are kept as they are. A hook is
added only if no hook with the same command is already registered under that event
(compared with $HOME / ~ / the absolute home path treated as equal). Writes a timestamped
backup next to the file before changing anything, and refuses (exit 1, no write) if the
existing file is not valid JSON.

Usage: merge-settings.py TEMPLATE SETTINGS [--apply]   (without --apply: report only)
"""
import json, os, shutil, sys, time

def norm(cmd):
    home = os.path.expanduser("~")
    return cmd.replace("$HOME", "~").replace("${HOME}", "~").replace(home, "~").strip()

def merge(tpl, cur):
    added = []
    chooks = cur.setdefault("hooks", {})
    for event, groups in (tpl.get("hooks") or {}).items():
        egroups = chooks.setdefault(event, [])
        have = {norm(h.get("command", "")) for g in egroups for h in g.get("hooks", [])}
        for g in groups:
            new = [h for h in g.get("hooks", []) if norm(h.get("command", "")) not in have]
            if not new:
                continue
            for h in new:
                have.add(norm(h.get("command", "")))
                added.append(f"{event}: {h['command']}")
            tgt = next((x for x in egroups if x.get("matcher") == g.get("matcher")
                        and set(x) <= {"matcher", "hooks"}), None)
            if tgt is not None and g.get("matcher") is not None:
                tgt.setdefault("hooks", []).extend(new)
            else:
                ng = {k: v for k, v in g.items() if k != "hooks"}
                ng["hooks"] = new
                egroups.append(ng)
    perms = cur.setdefault("permissions", {})
    for kind, vals in (tpl.get("permissions") or {}).items():
        if isinstance(vals, list):
            lst = perms.setdefault(kind, [])
            for v in vals:
                if v not in lst:
                    lst.append(v)
                    added.append(f"permissions.{kind}: {v}")
    return added

def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply_ = "--apply" in sys.argv
    if len(args) != 2:
        print(__doc__); return 2
    tpl_p, cur_p = args
    tpl = json.load(open(tpl_p))
    try:
        cur = json.load(open(cur_p)) if os.path.exists(cur_p) else {}
    except ValueError as e:
        print(f"merge-settings: {cur_p} is not valid JSON ({e}); not touching it. Merge by hand.", file=sys.stderr)
        return 1
    added = merge(tpl, cur)
    for a in added:
        print(("added " if apply_ else "would add ") + a)
    if not added:
        print("settings already contain every hook from the template; nothing to add")
    elif apply_:
        if os.path.exists(cur_p):
            bak = f"{cur_p}.bak-{time.strftime('%Y%m%d%H%M%S')}"
            shutil.copy2(cur_p, bak)
            print(f"backup: {bak}")
        os.makedirs(os.path.dirname(os.path.abspath(cur_p)), exist_ok=True)
        with open(cur_p, "w") as f:
            json.dump(cur, f, indent=2); f.write("\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
