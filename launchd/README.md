# launchd templates

`install.sh --apply` fills in `@DEST@` (your install dir) and `@LABEL_PREFIX@`
(default `local.claude`) and writes each `*.plist.template` here to
`~/Library/LaunchAgents/<label>.plist`. It never loads them by itself. Load one when you want it:

    launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.claude.disk-guard.plist

Or pass `--load` to install.sh to do that for you. Unload with `launchctl bootout gui/$(id -u)/<label>`.

| job | what it does | when |
|---|---|---|
| disk-guard | free-space verdict + log (read-only) | every 10 min |
| weekly-process-review | collects 7-day process numbers, writes a recommendations report | Mondays 06:00 |
| tokens-review | token-spend review routine | Fridays 16:03 |
