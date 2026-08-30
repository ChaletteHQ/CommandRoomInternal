---
name: enable-quick-commands
surfaces: cowork
slack_fallback: "Quick-command buttons are a desktop sidebar feature — from Slack, just type what you need (for example 'morning briefing')."
description: "Install or rebuild the Quick Commands Live Artifact — curated cheat sheet of Command Room trigger phrases organized into 3 tabs (Your Day · Shortcuts · How To Run Command Room). Pinned to the Cowork sidebar as a Layer 1 default. Helps users discover deeper capabilities — every row on the Your Day and Shortcuts tabs is clickable, fires the trigger phrase into chat; the third tab teaches plain-language usage. Static reference (rebuilds on plugin update or `rebuild quick commands`, NOT auto-refreshed on a cron). Triggers: `install quick commands`, `enable quick commands`, `rebuild quick commands`. Also called silently by `command-room-update-bridge`."
---

# enable-quick-commands (v2.9.0+)

Installs the **Quick Commands** Live Artifact — a curated cheat sheet of the most-used Command Room trigger phrases, organized into 3 tabs. Rows on the Your Day and Shortcuts tabs are clickable; clicks fire the trigger into chat via `sendPrompt` (Pattern A). The third tab is not clickable — it's plain-language usage guidance, not a trigger list.

This artifact is intentionally **static** — it doesn't read live data. The tabs + commands are baked into the template and rebuilt only when (a) plugin updates, (b) user explicitly says `rebuild quick commands`. No cron schedule required (deliberate design call, 2026-05: a refresh task is NOT registered — static reference content doesn't need one).

The tabs that ship as of QUICKCMD2 (2026-08-28) are:

- **Your Day** — the daily-loop rows (`brief me`, `triage my inbox`, `prep me for my next meeting`, `process the last call`, `what's on my plate`, `go [name]`, `tell me about [person]`, `staff meeting`, `weekly recap`), what arrives on its own on the shipped default schedule, and what the buttons/numbered replies/Run Now/update notes actually do.
- **Shortcuts** — four groups (People & Relationships · Drafts & Docs · Memory & Decisions · Upkeep) covering the rest of the curated trigger set.
- **How To Run Command Room** — no rows to click. Five plain-language principles plus a small set of real, evidenced client use-case scenes, closing on an invitation to just ask in your own words.

This replaces the prior 10-category structure (Daily Loop · Workspace · People · Drafts · Meetings · Memory · Strategy · Ingest · Reporting · Maintenance, v3.6.5+) — QUICKCMD2's content-mining pass (usage ranking + the client quote bank) showed the daily loop dominates real use and the 52-row/10-category card gave everything equal weight. Deals/pipeline rows are retired from this artifact entirely (M ruling 2026-08-28: "deals don't work well yet") and return only by a future ruling.

## Phase 0: Workspace discovery (Rule 22 — run before anything below)

Several phases touch workspace files (`_hq/data/events.jsonl` in Phases 2 and 6, `entities.json` in Phase 3) but a cold session has no resolved workspace path. Resolve both roots first — never assume a cwd, never hardcode:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')
```

Use `$WORKSPACE/_hq/data/events.jsonl` for the idempotency read (Phase 2) and the install log (Phase 6), and `$WORKSPACE/_hq/data/entities.json` for `CEO_DISPLAY_NAME` (Phase 3).

## Phase 1: Cowork detection

If `mcp__cowork__create_artifact` is unavailable, abort cleanly:

> *"Quick Commands lives in your Cowork sidebar, so I can't pin it here. The commands still work — just say them in chat; the sidebar card is only a quick-reference list."*

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Quick Commands only renders in Cowork's sidebar. The trigger phrases work without it."
- GOOD: "Quick Commands lives in your Cowork sidebar. The commands still work — just say them in chat."

Log `plugin_update_deferred` with reason `"cowork-not-available"`. Stop.

## Phase 2: Idempotency

Read the last 200 lines of `_hq/data/events.jsonl`. If `{"type":"artifact_installed","artifact":"quick-commands"}` exists:

- **Interactive mode** — confirm: *"Quick Commands is already on your sidebar. Want me to refresh it? (yes / no)"*. If yes → Phase 3, log `artifact_refreshed`. If no → exit silently.
- **Silent mode** (called by bridge) — skip prompt. Regenerate. Log `artifact_refreshed`.

## Phase 3: Render via pipeline

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')

python3 "$PLUGIN_ROOT/shared/scripts/render_artifact.py" \
  --template "$PLUGIN_ROOT/skills/enable-quick-commands/references/quick-commands-artifact.html" \
  --input - \
  --output /tmp/cr-quick-commands.html \
<<EOF
{
  "CEO_DISPLAY_NAME": "<from entities.json>",
  "LAST_BUILT": "<YYYY-MM-DD HH:MM UTC>"
}
EOF
```

Only two placeholders — `CEO_DISPLAY_NAME` (from `$WORKSPACE/_hq/data/entities.json`'s primary user) and `LAST_BUILT` (current ISO time formatted).

## Phase 4: Install

Pass `/tmp/cr-quick-commands.html` to `mcp__cowork__create_artifact`:

- `id: "quick-commands"` — VERBATIM
- `widget_code:` rendered HTML
- `mcp_tools: []` — artifact does NOT call MCP from inside iframe (Pattern A clicks only)

If id exists, use `update_artifact`.

## Phase 5: Rule 8 verification (source-side)

1. `<!doctype html>` (line 1)
2. `<meta charset="utf-8">` (line 2)
3. `data-artifact="quick-commands"`
4. No `â€` mojibake
5. Size ≥ 80% of canonical template post-substitution

## Phase 6: Log

```jsonl
{"type":"artifact_installed","artifact":"quick-commands","installed_at":"<ISO>","installed_by":"<caller>"}
```

## Why this exists (v2.9.0 architectural shift)

v2.7.x → v2.8.x shipped 5 sidebar dashboards (Orgs Map, People Network, Commitments Tracker, Daily Today, Process Meetings). v2.9.0 retires 4 of them (kept only Orgs Map for the visual workspace browse + demo "wow" moment). The action-delivery surface moves from artifacts to **persistent scheduled chats** (cr-meetings-today, cr-inbox-pulse, cr-commitment-nudge, cr-commitment-chase, cr-cracks-watch) — see `enable-command-room-schedules`.

Quick Commands fills the discovery gap that retiring the dashboards opens: how does a user know what they can ask the system to do? Answer: pin a small artifact to the sidebar with the curated list of triggers. Click any row to fire it. Use this to onboard new users (a new teammate, a future client) and as a self-reminder for what's available beyond the morning chats.

## Adding new commands

Edit `references/quick-commands-artifact.html` directly. Each command is one `<div class="card" data-prompt="trigger phrase">...</div>` row inside a `<div class="grid">` grid, itself inside a `<div class="cat">` group on the Your Day or Shortcuts `<div class="tab-panel">`. The `data-prompt` attribute is what fires when the user clicks — make it natural-language, not a skill name. The How To Run Command Room tab carries no clickable rows — it is plain-language guidance and evidenced use-case scenes, not a trigger list; don't add cards there.

Tabs and their groups are deliberate. The artifact's value is curation, not exhaustiveness — adding every skill to it defeats the purpose. New rows get evidenced, not just added: a use-case scene added to the third tab needs a real, verifiable source (a client quote, a session note, a dated M attestation) recorded at build time — copy that can't be evidenced doesn't ship (QUICKCMD2 ruling, 2026-08-28). Deal/pipeline-feature rows do not belong in this artifact under any category — that's a standing product ruling, not a curation preference.

## Forbidden behaviors

- **No Pattern B.** Quick Commands is purely static + click-to-chat. No `callMcpTool` inside the iframe.
- **No `mcp_tools` in install args.** Pass `mcp_tools: []`.
- **No scheduled-task auto-refresh.** This artifact rebuilds on plugin update or explicit `rebuild quick commands`. Don't register `cr-refresh-quick-commands` — it's static reference content.
- **Don't add commands without the user's review.** This is a curated list, not a skill index. New rows get added intentionally; they shouldn't drift from "every skill the plugin ships" because that's noise.
