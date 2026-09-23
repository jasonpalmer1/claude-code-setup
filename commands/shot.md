---
description: Screenshot any URL at phone size (390×660) with the permanent headless-Chrome rig — the visual-test primitive behind /preflight
argument-hint: "<url> [more urls] [--desktop] [--full]  (local URLs fine if the dev server is running)"
---

Capture screenshots of the given URL(s) and **send them to the operator** so he sees them inline. This is the one-command version of the technique in [[reference_screenshot_technique]] — system Chrome.app crashes under automation; the working rig lives permanently at `~/.claude/tools/shotter/`.

Args: `$ARGUMENTS` → one or more URLs, plus optional `--desktop` (adds a 1280×800 shot) and `--full` (full-page capture).

Steps:

1. **If the URL is local** (`localhost`/`127.0.0.1`) confirm the dev server is up (`curl -s -o /dev/null -w "%{http_code}"`). If it's down, start it per the project's CLAUDE.md before shooting — don't shoot a connection-refused page.

2. **Shoot** into the scratchpad dir:
   ```bash
   node ~/.claude/tools/shotter/shot.mjs <url> <scratchpad>/shot-<slug> [--desktop] [--full]
   ```
   Produces `shot-<slug>-mobile.png` (390×660 @2x — the operator's standard feature-test viewport per [[feedback_test_new_features]]) and optionally `-desktop.png`.

3. **Look at each shot yourself** (Read the image) before sending — catch blank pages, broken layout, or error states and say so rather than silently attaching a broken render.

4. **SendUserFile** the images with a one-line caption per shot.

Notes:
- For interaction sequences (tap → screenshot → tap), write a one-off puppeteer script in the scratchpad importing from `~/.claude/tools/shotter/node_modules` — copy the pattern in `shot.mjs`.
- A blank/white mobile shot on a page that works on desktop smells like the iOS inline-base64 problem — see [[feedback_ios_inline_base64]].
