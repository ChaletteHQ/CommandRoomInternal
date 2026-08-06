---
name: enable-workspace-map
surfaces: cowork
slack_fallback: "The workspace map is a desktop sidebar feature — from Slack, ask 'list active projects' for the same tree as text."
description: "Install or refresh the Workspace Map sidebar artifact — a stripped-down navigation tree of orgs + projects pinned to the Cowork sidebar. Manual `↻ Refresh` button on the artifact triggers an ad-hoc rebuild (no scheduled auto-refresh). Triggers: 'install workspace map', 'enable workspace map', 're-install workspace map', 'rebuild workspace map'. Legacy aliases (backward compat for users with the artifact already pinned): 'install orgs map', 'enable orgs map', 'rebuild orgs map' (the artifact id remains `orgs-map`). Also called silently by `command-room-update-bridge` (initial install). Idempotent: if already installed, regenerates with current data."
---

# enable-workspace-map (Workspace Map artifact)

Installs or regenerates the **Workspace Map** Live Artifact via the v2.7.13+ renderer pipeline. Skill renamed from `enable-orgs-map` to `enable-workspace-map` in v3.5.0 to match the artifact's user-facing name; the underlying artifact id stays `orgs-map` for backward compat with users who already have it pinned, and the legacy `install orgs map` / `enable orgs map` trigger phrases still route here.

## Phase 1: Cowork detection

If `mcp__cowork__create_artifact` is unavailable, abort cleanly:

> *"The Workspace Map lives in your Cowork sidebar, so I can't show it here. You can still get everything in chat — try `list active` or `go [project]`."*

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "The Orgs Map only renders in Cowork's sidebar."
- GOOD: "The Workspace Map lives in your Cowork sidebar." (the customer-facing name is always **Workspace Map** — never "Orgs Map", which survives only as the internal artifact id)

Log `plugin_update_deferred` with reason `"cowork-not-available"`. Stop.

## Phase 2: Idempotency

Read the last 200 lines of `_hq/data/events.jsonl`. If `{"type":"artifact_installed","artifact":"orgs-map"}` already exists:

- **Interactive mode** — confirm: *"Your Workspace Map is already on your sidebar. Want me to refresh it with current data? (yes / no)"*. If yes → Phase 3, log `artifact_refreshed`. If no → exit silently.
- **Silent mode** (called by bridge / onboarding) — skip the prompt. Regenerate. Log `artifact_refreshed`.

## Phase 3: Build via renderer pipeline

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')

# 1. Project entities + events into input.json (shared projector — all 3 split artifacts use it)
python3 "$PLUGIN_ROOT/shared/scripts/build_workspace_map_input.py" \
  --workspace-root "$WORKSPACE" \
  --output /tmp/cr-wm-input.json

# 2. Render template → byte-deterministic output
python3 "$PLUGIN_ROOT/shared/scripts/render_artifact.py" \
  --template "$PLUGIN_ROOT/skills/enable-workspace-map/references/orgs-map-artifact.html" \
  --input /tmp/cr-wm-input.json \
  --output /tmp/cr-orgs.html
```

The renderer warns about unused placeholders (`THREADS_JSON`, `COMMITMENTS_JSON`, `INBOX_JSON`, etc.) — those are for sibling artifacts, harmless. As of v2.14.11+, the Workspace Map template uses `ORGS_JSON` + `PROJECTS_JSON` + `PEOPLE_JSON` + `OWES_BY_ORG_JSON` + `CEO_DISPLAY_NAME` + `LAST_BUILT`.

## Phase 4: Install

Read `/tmp/cr-orgs.html`. Pass full contents (verbatim, byte-for-byte) to `mcp__cowork__create_artifact`:

- `id: "orgs-map"` — **VERBATIM**. Do NOT invent variants like `orgs-map-v2`, `orgs-map-canonical`, etc.
- `widget_code:` the rendered HTML
- `mcp_tools: []` (this artifact does not call connectors directly)

If the existing artifact id already exists, use `update_artifact` instead.

## Phase 5: Rule 8 verification (source-side only — see bridge SKILL for honest scope)

Verify the rendered output contains:

1. `<!doctype html>` (line 1)
2. `<meta charset="utf-8">` (line 2)
3. `data-artifact="orgs-map"` (canonical marker)
4. No `â€` mojibake bytes outside HTML comments (the source-side check; Rule 8 cannot verify installed bytes — see bridge SKILL.md)
5. Size ≥ 80% of canonical template post-substitution

If any check fails, log `artifact_install_failed` with `{artifact: "orgs-map", reason: "verification_failed:<which-check>"}` and STOP. Do NOT regenerate by hand. Do NOT delegate to a subagent.

## Phase 6: Log

```jsonl
{"type":"artifact_installed","artifact":"orgs-map","installed_at":"<ISO>","installed_by":"<caller>"}
```

`<caller>` = `"enable-workspace-map"` (interactive — was `"enable-orgs-map"` pre-v3.5.0; both values may appear in events.jsonl history) or the calling skill (`"command-room-update-bridge"` / `"command-room-onboarding"`).

## Forbidden behaviors

- **Do NOT delegate the relay step to a subagent.** Subagent context lacks the canonical bytes and confabulates ids + data. If the bytes don't fit your output context, log `packaging_problem` and STOP.
- **Do NOT invent variant ids.** `orgs-map`, verbatim. No `-v2`, `-fixed`, `-new`, `-canonical` suffixes.
- **Do NOT improvise a "compact equivalent"** if create_artifact fails. The renderer output is the only legitimate payload.
