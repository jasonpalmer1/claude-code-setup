---
description: Scaffold a new web project matching your stack conventions (React 19 + Cloudflare Pages)
argument-hint: "<name> [--next | --vite] [--supabase]  (default: vite)"
---

Scaffold a new web project at `~/projects/<name>` that matches your established conventions, then print the exact command to see it live. Goal: **MVP fast** — minimal, working, conventional.

Args: `$ARGUMENTS` → first token is `<name>`. Flags: `--next` (Next.js 16) or `--vite` (default if neither), `--supabase` (add client boilerplate).

**The canonical stack** (edit this to match your own — then don't ask, just apply): React 19 + TypeScript, deployed to **Cloudflare Pages via wrangler**. Vite + Tailwind v4 for interactive apps (default); Next.js 16 for content/marketing sites (`--next`). Supabase only when `--supabase` is passed.

Steps:
1. **Pick the stack.** If `--next` → Next.js 16; else Vite + React 19 + TS + Tailwind v4. Bail if `~/projects/<name>` already exists.
2. **Scaffold.**
   - *Vite path:* scaffold a React-TS Vite app. Wire Tailwind v4 the convention way — `@tailwindcss/vite` plugin in `vite.config.ts`, **no `tailwind.config.js`**; design tokens live in `src/index.css` under `@theme`. Add a starter component class so the design system has a home.
   - *Next path:* `npx create-next-app@latest <name> --typescript --tailwind --app`. Then read `node_modules/next/dist/docs/` before writing any app code — **Next 16 has breaking changes vs. training data**. Put that rule in the project's `AGENTS.md` and point `CLAUDE.md` at it via `@AGENTS.md`, so every future session inherits it.
3. **CLAUDE.md codebase map.** Generate one at the project root following the `/index` convention (Overview / Tech stack / File map / Architecture / Entry points / Conventions). Keep it scannable — it's the file future sessions read instead of re-exploring.
4. **Cloudflare Pages deploy.** Add the standard npm scripts:
   ```
   "deploy:preview": "npm run build && wrangler pages deploy dist --project-name <name> --branch preview",
   "deploy:prod":    "npm run build && wrangler pages deploy dist --project-name <name> --branch main"
   ```
   (Next path: deploy the framework build output, not `dist/`.) Assumes wrangler is already OAuth-logged-in — no config file needed. Ensure `dev` / `build` / `preview` / `lint` scripts exist.
5. **Supabase (only if `--supabase`).** Add `@supabase/supabase-js`, a `src/lib/supabase.ts` with `createClient` + an `isSupabaseConfigured` guard, and a `.env.example` listing `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` (Next: `NEXT_PUBLIC_*`). Never commit real keys — `.env` stays gitignored.
6. **Init git.** `git init`, a sensible `.gitignore` (node_modules, dist/.next, .env), and one initial commit.
7. **Say what to run.** End by printing the single exact next command to see it live locally — e.g. `cd ~/projects/<name> && npm run dev` — plus the one-liner to ship a preview (`npm run deploy:preview`).

This is delegable scaffolding — hand the mechanical setup to a mid-tier subagent and tell it to read this command and the conventions above first. Don't over-build: a clean, deployable hello-world on the right stack beats a feature-rich half-thing.
