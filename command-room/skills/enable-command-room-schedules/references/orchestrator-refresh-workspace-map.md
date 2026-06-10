# Orchestrator prompt — Workspace Map refresh

This file is the EXACT prompt registered with `create_scheduled_task` for `taskId: cr-refresh-workspace-map`. Default cron: `0 16 * * 1-5` (4 PM weekdays local). Per-workspace override via `workspace.schedule_config` in entities.json.

**Purpose (v2.14.11+):** silently rebuild the Workspace Map sidebar artifact with current data so the user wakes up to a fresh map. Solves the v2.7.x stale-artifact problem without making the artifact live.

**Chat output:** none. This task fires silently in the background. The user sees the result via the refreshed sidebar artifact next time they look at it. NO chat post per Rule 9 (silent memory updates).

---

# Phase 1 — Always run (no idempotency gate)

This task is silent and idempotent — re-runs are safe. No need to gate on "already ran today."

# Phase 2 — Setup + Cowork-detection

Discover plugin root + workspace root via the standard pattern:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
```

Check that `mcp__cowork__update_artifact` is available. If not, log silently and exit — the artifact can't be refreshed without Cowork. Don't surface to chat (this is a silent task; failures are logged for usage-report telemetry but don't interrupt the user).

Check that an `artifact_installed` event for `orgs-map` exists in `events.jsonl`. If NOT, the user hasn't installed the Workspace Map artifact yet — exit silently. Refresh-only task; doesn't install.

# Phase 3 — Run the renderer pipeline

Same pipeline `enable-workspace-map` uses for fresh installs:

```bash
cd "$PLUGIN_ROOT"

# 1. Project current entities + events into input.json
python3 shared/scripts/build_workspace_map_input.py \
  --workspace-root "$WORKSPACE" \
  --output /tmp/cr-wm-input.json

# 2. Render template with current data
python3 shared/scripts/render_artifact.py \
  --template skills/enable-workspace-map/references/orgs-map-artifact.html \
  --input /tmp/cr-wm-input.json \
  --output /tmp/cr-wm.html
```

If either bash step fails (non-zero exit), log a `pack_run` event with `status: failed` + the stderr, and exit. The user sees yesterday's-end-of-day map until tomorrow's fire (or their next manual `rebuild workspace map`).

# Phase 4 — Update the live artifact

Read `/tmp/cr-wm.html` (full byte-for-byte) and call:

```
mcp__cowork__update_artifact(id="orgs-map", widget_code=<contents>)
```

Use `update_artifact` not `create_artifact` — the artifact already exists. The artifact id stays `orgs-map` for backward compat (existing users have it pinned with that id; renaming would create a duplicate).

# Phase 5 — Rule 8 verification (source-side only)

Run the source-side check on `/tmp/cr-wm.html`:

1. Contains `<!doctype html>` (line 1)
2. Contains `data-artifact="orgs-map"` (canonical marker)
3. Size ≥ 80% of canonical template post-substitution
4. No `â€` mojibake bytes outside HTML comments

If verification fails, log `artifact_install_failed` with reason and STOP. Do NOT call `update_artifact` with bad bytes.

# Phase 6 — Memory updates (silent per Rule 9)

Append a single `pack_run` event to events.jsonl:

```jsonl
{"type":"pack_run","ts":"<ISO>","data":{"kind":"refresh_workspace_map","status":"complete","duration_ms":<N>,"telemetry":{...}}}
```

Use `shared/scripts/telemetry.py` `build_pack_run_telemetry()` for the telemetry block — same pattern as the other 5 orchestrators. Silent — never narrate to chat.

# Phase 7 — STOP

No chat output. No widget. No Links section. Pure silent refresh.

# What this orchestrator does NOT do

- Does NOT install the artifact (`enable-workspace-map` does that on first install)
- Does NOT modify entities.json or any data file (read-only)
- Does NOT post to chat (silent fire)
- Does NOT call any external connector (Gmail / Calendar / Granola / etc.)
- Does NOT rebuild any other artifact (Quick Commands has its own enable skill if it ever needs refresh; this orchestrator is Workspace Map only)
