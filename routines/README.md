# Routines — headless scheduled Claude Code runs

These scripts run Claude Code **headlessly** (`claude -p "..."`) on a schedule via macOS
`launchd`, so recurring reports show up without you remembering to ask for them. The pattern
generalizes to cron/systemd timers on other platforms — the `.plist` files here are just the
macOS scheduling mechanism.

## Files

- **`monday-cockpit.sh`** — runs `/standup` + `/pulse` (or your equivalents) and writes a
  combined markdown report plus fires a desktop notification.
- **`tokens-review.sh`** — runs `/tokens` on the cheapest model tier (it's a pure file read)
  and writes a report plus notification.
- **`standup-scan.sh`** — the read-only git-fleet scan that `/standup` and `monday-cockpit.sh`
  both use; kept as its own file so a launchd job can allowlist it **by exact path** instead of
  granting broad shell access.
- **`*.plist.example`** — launchd job definitions. Copy (don't symlink) into
  `~/Library/LaunchAgents/`, rename to a reverse-DNS label you own (e.g.
  `com.<you>.claude.monday-cockpit.plist`), fix the paths inside, then:
  ```bash
  launchctl load -w ~/Library/LaunchAgents/com.<you>.claude.monday-cockpit.plist
  ```

## Fill in before use

- `<REPORTS_DIR>` — where reports land, e.g. `~/projects/cockpit-reports`.
- `<PULSE_LOG_PATH>` — where the traffic-pulse history lives, e.g. `~/projects/pulse-log.md`.
- `<HOME>` in the `.plist` files — launchd doesn't expand `$HOME`, so this has to be a literal
  absolute path to your home directory.
- The `Bash(...)`/`Write(...)`/`Edit(...)` entries in `--allowedTools` — these are intentionally
  narrow. A headless run has no one watching to approve a permission prompt, so the tool grant
  should be the minimum that lets the routine do its one job: a fixed read-only scan script by
  exact path, a couple of read-only git/node command prefixes, and write access to exactly one
  output file. Never grant unrestricted `Bash` or `Write` to a headless routine.

## Why headless + narrow tools

`claude -p` runs non-interactively and exits — no chat, no approvals. That's what makes a
launchd job possible, but it also means any tool grant is a standing, unattended capability.
Treat the `--allowedTools` list for a scheduled routine as a mini security review: enumerate
the exact commands and exact file paths it needs, nothing broader.

## Machine-local, not portable as-is

The `.plist` files hardcode an absolute home directory and a `Label` you should own
(reverse-DNS, e.g. `com.<you>.claude.<routine-name>`). Treat them as examples to adapt, not
shared config — they are **not meant to be copied verbatim between machines or users**. If you
keep a private config repo synced across machines, leave real `.plist` files out of it (or note
clearly which machine is "primary") so a scheduled job doesn't silently double-run from two
machines at once.
