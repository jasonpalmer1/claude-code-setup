---
description: Screenshot any URL at phone size (390×660) with a permanent headless-Chrome rig — the visual-test primitive behind /preflight
argument-hint: "<url> [more urls] [--desktop] [--full]  (local URLs fine if the dev server is running)"
---

Capture screenshots of the given URL(s) and show them to the user inline. System browsers (e.g. Chrome.app) tend to crash under automation, so this uses a small dedicated headless-Chrome rig kept at `~/.claude/tools/shotter/` (see setup note below — the rig itself isn't part of this repo).

Args: `$ARGUMENTS` → one or more URLs, plus optional `--desktop` (adds a 1280×800 shot) and `--full` (full-page capture).

Steps:

1. **If the URL is local** (`localhost`/`127.0.0.1`) confirm the dev server is up (`curl -s -o /dev/null -w "%{http_code}"`). If it's down, start it per the project's `CLAUDE.md` before shooting — don't shoot a connection-refused page.

2. **Shoot** into a scratch directory:
   ```bash
   node ~/.claude/tools/shotter/shot.mjs <url> <scratch-dir>/shot-<slug> [--desktop] [--full]
   ```
   Produces `shot-<slug>-mobile.png` (390×660 @2x — a reasonable default mobile-test viewport) and optionally `-desktop.png`.

3. **Look at each shot yourself** (read the image) before sending — catch blank pages, broken layout, or error states and say so rather than silently attaching a broken render.

4. **Send the images** with a one-line caption per shot.

Notes:
- For interaction sequences (tap → screenshot → tap), write a one-off puppeteer script in the scratch dir importing from `~/.claude/tools/shotter/node_modules` — copy the pattern in `shot.mjs`.
- A blank/white mobile shot on a page that renders fine on desktop is a common symptom of an inline-base64-image issue specific to mobile Safari — check for oversized `data:image` blobs before assuming it's something else.
- **Setup (not included in this repo):** the rig is a small `puppeteer-core` script (`shot.mjs`) plus a `chrome-headless-shell` binary (too large to check into git — install with `npx @puppeteer/browsers install chrome-headless-shell@stable`). Write your own `shot.mjs` or ask Claude to scaffold one; this command just assumes it exists at the path above.
