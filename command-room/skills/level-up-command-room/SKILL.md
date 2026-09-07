---
name: level-up-command-room
surfaces: cowork
slack_fallback: "The sidebar dashboards are a desktop feature — open Cowork and ask to level up Command Room there. From Slack, 'list active projects' gives you the Workspace Map as text and 'triage my commitments' gives you your open commitments."
description: "Install or refresh the sidebar dashboards — Workspace Map, Quick Commands, My Open Commitments. Fires on: 'level up command room', 'level up my command room', 'show me dashboards', 'what dashboards can I install', 'show me what I can enable', 'add more dashboards', 'level me up'. Workspace Map: 'install workspace map', 'enable workspace map', 're-install workspace map', 'rebuild workspace map', legacy 'install orgs map' / 'enable orgs map' / 'rebuild orgs map'. Quick Commands: 'install quick commands', 'enable quick commands', 'rebuild quick commands'. My Open Commitments: 'install my commitments', 'enable my commitments', 'rebuild my commitments dashboard', 'my commitments dashboard'. Called silently by command-room-update-bridge and onboarding; idempotent. Does NOT fire on 'install command room' (command-room-onboarding), 'update command room' (command-room-update-bridge), or 'triage my commitments' (commitment-triage)."
---

# Level Up Command Room — the sidebar dashboards

One skill, three sidebar artifacts, one renderer. Every Live Artifact Command
Room pins to the Cowork sidebar is installed, refreshed and verified here:

| Artifact | id (verbatim) | Mode | Refresh |
|---|---|---|---|
| Workspace Map | `orgs-map` | Mode: Workspace Map | manual `↻ Refresh` on the artifact, the 4 PM weekday refresh task when registered, or `rebuild workspace map` |
| Quick Commands | `quick-commands` | Mode: Quick Commands | plugin update or `rebuild quick commands` (static content, no cron) |
| My Open Commitments | `my-commitments` | Mode: My Open Commitments | `↻ Rebuild` on the artifact or `rebuild my commitments dashboard` |

Until SKILLMERGE1 (2026-09-03) the first two lived in their own skills
(`enable-workspace-map`, `enable-quick-commands` — both folded here) and this
skill was an empty menu. Every phrase either of them answered still lands
here; the artifact ids never changed, so nothing already pinned on a client
machine is touched by the fold.

## Phase 0 — Workspace + plugin discovery (CONTRACT Rule 22, every mode)

Several phases touch workspace files (`_hq/data/events.jsonl` for the
idempotency read and the install log, `_hq/data/entities.json` for the
primary user's display name) and a cold session has no resolved workspace
path. Resolve both roots first — never assume a cwd, never hardcode:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')
```

## Phase 1 — Cowork detection (every mode)

If `mcp__cowork__create_artifact` is unavailable, abort cleanly with the
mode's one-line fallback below, log `plugin_update_deferred` with reason
`"cowork-not-available"`, and stop.

**Output guard:** no internal tokens, paths, event names, or version numbers
in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md`
§ Plain-language glossary. The customer-facing names are always **Workspace
Map**, **Quick Commands** and **My Open Commitments** — never "Orgs Map",
which survives only as the internal artifact id.

## The menu — "level up command room"

When the user fires a menu phrase, read the last 200 lines of
`_hq/data/events.jsonl` for `artifact_installed` events and render the three
dashboards with their state in plain English — installed (with the last
install/refresh date) or not yet installed — then ask which to install or
refresh. Numbered replies and the artifact's own name both work. Never
invent a fourth dashboard; if the user asks for one that does not exist,
say so plainly (no install path exists for it, so there is nothing to
route to).

> *"Your sidebar dashboards:*
> *1. Workspace Map — installed, refreshed this morning.*
> *2. Quick Commands — installed.*
> *3. My Open Commitments — not installed yet.*
> *Say a number to install or refresh one, or `install my commitments` to add the third."*

If the user asks about the Commitment Cockpit (retired in v3.11.0, folded
into the Commitments scheduled chat): *"The Commitment Cockpit has been
retired — what it showed now lives in your daily Waiting On and My Plate
chats and in the My Open Commitments dashboard. If it's still pinned to your sidebar, feel
free to unpin it; it won't refresh anymore."*

Log the visit through the locked writer (SPEC GATE1 / A1; omit `seq`/`ts` —
auto-stamped), never a hand-rolled append, and skip the log if the write
would error — it is informational, not load-bearing:

```jsonl
{"type":"levelup_session","picks":["my-commitments"],"skipped":[],"ran_at":"<ISO>","menu_state":"three"}
```

## Mode: Workspace Map (`orgs-map`) — formerly enable-workspace-map

Installs or regenerates the **Workspace Map** Live Artifact — a stripped-down
navigation tree of orgs + projects. The skill that owned it was renamed from
`enable-orgs-map` to `enable-workspace-map` in v3.5.0 and folded here in
SKILLMERGE1; the artifact id stays `orgs-map` for backward compat with users
who already have it pinned, and the legacy `install orgs map` / `enable orgs
map` / `rebuild orgs map` phrases still route here.

**Cowork-unavailable fallback line:** *"The Workspace Map lives in your
Cowork sidebar, so I can't show it here. You can still get everything in
chat — try `list active` or `go [project]`."*

### Idempotency

If `{"type":"artifact_installed","artifact":"orgs-map"}` already exists in
the last 200 lines of `_hq/data/events.jsonl`:

- **Interactive mode** — confirm: *"Your Workspace Map is already on your
  sidebar. Want me to refresh it with current data? (yes / no)"*. If yes →
  build, log `artifact_refreshed`. If no → exit silently.
- **Silent mode** (called by the bridge / onboarding / the refresh task) —
  skip the prompt. Regenerate. Log `artifact_refreshed`.

### Build via the renderer pipeline

```bash
cd "$PLUGIN_ROOT"

# 1. Project entities + events into input.json (the shared projector)
python3 shared/scripts/build_workspace_map_input.py \
  --workspace-root "$WORKSPACE" \
  --output /tmp/cr-wm-input.json

# 2. Render template -> byte-deterministic output
python3 shared/scripts/render_artifact.py \
  --template skills/level-up-command-room/references/orgs-map-artifact.html \
  --input /tmp/cr-wm-input.json \
  --output /tmp/cr-orgs.html
```

The renderer warns about unused placeholders (`THREADS_JSON`,
`COMMITMENTS_JSON`, `INBOX_JSON`, etc.) — those are for sibling artifacts,
harmless. The Workspace Map template uses `ORGS_JSON` + `PROJECTS_JSON` +
`PEOPLE_JSON` + `OWES_BY_ORG_JSON` + `CEO_DISPLAY_NAME` + `LAST_BUILT`.

### Install

Read `/tmp/cr-orgs.html`. Pass the full contents (verbatim, byte-for-byte)
to `mcp__cowork__create_artifact`:

- `id: "orgs-map"` — **VERBATIM**. Do NOT invent variants like
  `orgs-map-v2`, `orgs-map-canonical`, etc.
- `widget_code:` the rendered HTML
- `mcp_tools: []` (this artifact does not call connectors directly)

If the artifact id already exists, use `update_artifact` instead.

### Verify (Rule 8, source-side only — see the bridge SKILL for honest scope)

1. `<!doctype html>` (line 1)
2. `<meta charset="utf-8">` (line 2)
3. `data-artifact="orgs-map"` (canonical marker)
4. No `â€` mojibake bytes outside HTML comments (the source-side check; Rule
   8 cannot verify installed bytes)
5. Size ≥ 80% of the canonical template post-substitution

If any check fails, log `artifact_install_failed` with `{artifact:
"orgs-map", reason: "verification_failed:<which-check>"}` and STOP. Do NOT
regenerate by hand. Do NOT delegate to a subagent.

### Log

```jsonl
{"type":"artifact_installed","artifact":"orgs-map","installed_at":"<ISO>","installed_by":"<caller>"}
```

`<caller>` = `"level-up-command-room"` (interactive — history may carry
`"enable-workspace-map"` and, pre-v3.5.0, `"enable-orgs-map"`; all three are
valid and read as the same installer) or the calling skill
(`"command-room-update-bridge"` / `"command-room-onboarding"`).

## Mode: Quick Commands (`quick-commands`) — formerly enable-quick-commands

Installs the **Quick Commands** Live Artifact — a curated cheat sheet of the
most-used Command Room trigger phrases, organized into 3 tabs. Rows on the
Your Day and Shortcuts tabs are clickable; clicks fire the trigger into chat
via `sendPrompt` (Pattern A). The third tab is not clickable — it is
plain-language usage guidance, not a trigger list.

This artifact is intentionally **static** — it doesn't read live data. The
tabs + commands are baked into the template and rebuilt only when (a) the
plugin updates, (b) the user explicitly says `rebuild quick commands`. No
cron schedule required (deliberate design call, 2026-05: a refresh task is
NOT registered — static reference content doesn't need one).

The tabs that ship as of QUICKCMD2 (2026-08-28) are:

- **Your Day** — the daily-loop rows (`brief me`, `triage my inbox`, `prep me
  for my next meeting`, `process the last call`, `what's on my plate`, `go
  [name]`, `tell me about [person]`, `staff meeting`, `weekly recap`), what
  arrives on its own on the shipped default schedule, and what the
  buttons/numbered replies/Run Now/update notes actually do.
- **Shortcuts** — four groups (People & Relationships · Drafts & Docs ·
  Memory & Decisions · Upkeep) covering the rest of the curated trigger set.
- **How To Run Command Room** — no rows to click. Five plain-language
  principles plus a small set of real, evidenced client use-case scenes,
  closing on an invitation to just ask in your own words.

This replaced the prior 10-category structure — QUICKCMD2's content-mining
pass (usage ranking + the client quote bank) showed the daily loop dominates
real use and the 52-row/10-category card gave everything equal weight.
Deals/pipeline rows are retired from this artifact entirely (ruling
2026-08-28: "deals don't work well yet") and return only by a future ruling.

**Cowork-unavailable fallback line:** *"Quick Commands lives in your Cowork
sidebar, so I can't pin it here. The commands still work — just say them in
chat; the sidebar card is only a quick-reference list."*

### Idempotency

If `{"type":"artifact_installed","artifact":"quick-commands"}` exists in the
last 200 lines of `_hq/data/events.jsonl`:

- **Interactive mode** — confirm: *"Quick Commands is already on your
  sidebar. Want me to refresh it? (yes / no)"*. If yes → render, log
  `artifact_refreshed`. If no → exit silently.
- **Silent mode** (called by the bridge / onboarding) — skip the prompt.
  Regenerate. Log `artifact_refreshed`.

### Render via the pipeline

```bash
cd "$PLUGIN_ROOT"
python3 shared/scripts/render_artifact.py \
  --template skills/level-up-command-room/references/quick-commands-artifact.html \
  --input - \
  --output /tmp/cr-quick-commands.html \
<<EOF_QC
{
  "CEO_DISPLAY_NAME": "<from entities.json>",
  "LAST_BUILT": "<YYYY-MM-DD HH:MM UTC>"
}
EOF_QC
```

Only two placeholders — `CEO_DISPLAY_NAME` (from
`$WORKSPACE/_hq/data/entities.json`'s primary user) and `LAST_BUILT`
(current ISO time formatted).

### Install

Pass `/tmp/cr-quick-commands.html` to `mcp__cowork__create_artifact`:

- `id: "quick-commands"` — VERBATIM
- `widget_code:` rendered HTML
- `mcp_tools: []` — the artifact does NOT call MCP from inside the iframe
  (Pattern A clicks only)

If the id exists, use `update_artifact`.

### Verify (Rule 8, source-side)

1. `<!doctype html>` (line 1)
2. `<meta charset="utf-8">` (line 2)
3. `data-artifact="quick-commands"`
4. No `â€` mojibake
5. Size ≥ 80% of the canonical template post-substitution

### Log

```jsonl
{"type":"artifact_installed","artifact":"quick-commands","installed_at":"<ISO>","installed_by":"<caller>"}
```

### Adding new commands

Edit `references/quick-commands-artifact.html` directly. Each command is one
`<div class="card" data-prompt="trigger phrase">...</div>` row inside a
`<div class="grid">` grid, itself inside a `<div class="cat">` group on the
Your Day or Shortcuts `<div class="tab-panel">`. The `data-prompt` attribute
is what fires when the user clicks — make it natural-language, not a skill
name. The How To Run Command Room tab carries no clickable rows — it is
plain-language guidance and evidenced use-case scenes, not a trigger list;
don't add cards there.

Tabs and their groups are deliberate. The artifact's value is curation, not
exhaustiveness — adding every skill to it defeats the purpose. New rows get
evidenced, not just added: a use-case scene added to the third tab needs a
real, verifiable source (a client quote, a session note, a dated operator
attestation) recorded at build time — copy that can't be evidenced doesn't
ship (QUICKCMD2 ruling, 2026-08-28). Deal/pipeline-feature rows do not belong
in this artifact under any category — that's a standing product ruling, not a
curation preference.

## Mode: My Open Commitments (`my-commitments`) — the port

The third dashboard: **what the user owes**, grouped by org or by person,
with overdue / stuck / dated filters, read-only. Ported into the plugin in
SKILLMERGE1 D10 from a per-workspace operator script, under the ruling that
anything useful to every client belongs in the plugin.

**What it shows and where the numbers come from.** Scope is *open,
confirmed, owned by the user*. The rows come from the canonical projector
(`cru_match.load_open_commitments`) and keep only the confirmed half via
`cru_match.split_pending_review(...)` (INTAKE); every headline number comes
from the canonical counter (`commitment_state.count_commitments` over that
same open set — the exact math the daily Waiting On / My Plate chats and
`what's on my plate` use), and the stuck / blocked flags from `commitment_activity`. The
builder never re-derives "what is open" or "how many"; a row and the
headline agree because they are one call. Unconfirmed extractions are never
rows — they are the pointer count into `needs your call`, printed in the
scope line.

**Who "you" is — and the one refusal.** The owner filter runs on
`primary_user.resolve_primary_user`. If the workspace cannot say which
person is the user, the build REFUSES with one plain sentence (the script
prints it and exits 3) — relay that sentence verbatim and stop. There is no
fallback guess: a wrong guess would render someone else's promises under
the user's name. `set up command room` is what fixes it.

**Read-only, click-to-chat.** Every control on the artifact sends a chat
phrase (`go [project]`, `who is [name]`, `triage my commitments`, `needs
your call`, `rebuild my commitments dashboard`). Nothing on it closes,
defers, drops or re-dates a row — those verbs live in commitment-triage and
the queue. No ids, source skills or substrate handles reach the HTML.

**Cowork-unavailable fallback line:** *"My Open Commitments lives in your
Cowork sidebar, so I can't pin it here. Say `triage my commitments` for the
same list in chat, with the actions."*

### Idempotency

If `{"type":"artifact_installed","artifact":"my-commitments"}` exists in the
last 200 lines of `_hq/data/events.jsonl`:

- **Interactive mode** — confirm: *"My Open Commitments is already on your
  sidebar. Want me to rebuild it with current data? (yes / no)"*. If yes →
  build, log `artifact_refreshed`. If no → exit silently.
- **Silent mode** (a `rebuild my commitments dashboard` click from the
  artifact's own ↻ button) — skip the prompt. Rebuild. Log
  `artifact_refreshed`.

### Build via the renderer pipeline

```bash
cd "$PLUGIN_ROOT"

# 1. Canonical open set + canonical counts -> render input (refuses, exit 3,
#    when the primary user is unresolved; relay its one line verbatim)
python3 shared/scripts/commitments_dashboard.py \
  --workspace-root "$WORKSPACE" \
  --output /tmp/cr-mc-input.json || { cat /tmp/cr-mc-input.json 2>/dev/null; exit 0; }

# 2. Render template -> byte-deterministic output
python3 shared/scripts/render_artifact.py \
  --template shared/templates/commitments_dashboard.html \
  --input /tmp/cr-mc-input.json \
  --output /tmp/cr-my-commitments.html
```

The template uses exactly three placeholders — `DATA_JSON`, `BUILT`, `USER`
— all produced by step 1. The timezone for `BUILT` and for every age / due
/ overdue / stuck computation is the workspace's own (`tz.py`); if it cannot
be resolved the build says so in the payload and stamps UTC rather than
guessing a zone.

### Install

Read `/tmp/cr-my-commitments.html`. Pass the full contents (verbatim,
byte-for-byte) to `mcp__cowork__create_artifact`:

- `id: "my-commitments"` — **VERBATIM**.
- `widget_code:` the rendered HTML
- `mcp_tools: []` (Pattern A clicks only — the artifact calls no connector)

If the artifact id already exists, use `update_artifact` instead.

### Verify (Rule 8, source-side)

1. `<!doctype html>` (line 1)
2. `<meta charset="utf-8">` (line 2)
3. `data-artifact="my-commitments"`
4. No `â€` mojibake
5. Size ≥ 80% of the canonical template post-substitution

If any check fails, log `artifact_install_failed` with `{artifact:
"my-commitments", reason: "verification_failed:<which-check>"}` and STOP.

### Log

```jsonl
{"type":"artifact_installed","artifact":"my-commitments","installed_at":"<ISO>","installed_by":"level-up-command-room"}
```

This artifact is NOT a Layer 1 default: the update bridge does not install
it on its own. It is offered from the menu and installed the first time the
user asks for it; after that a `rebuild my commitments dashboard` refreshes
it in place.

## Why the sidebar is three artifacts and not more (v2.9.0 architectural reset)

v2.7.x → v2.8.x shipped 5 sidebar dashboards (Orgs Map, People Network,
Commitments Tracker, Daily Today, Process Meetings). v2.9.0 retired 4 of
them (kept only the Workspace Map for the visual workspace browse + demo
"wow" moment). The action-delivery surface moved from artifacts to
**persistent scheduled chats** — see `enable-command-room-schedules`. Quick
Commands fills the discovery gap that retiring the dashboards opened. My Open
Commitments (2026-09) is a READ-ONLY view of the canonical open set, grouped
by person and org — every number on it comes from the same counter every
other surface uses, and every action on it is a click that sends a chat
phrase; it never rewrites anything. Scheduled-task chat threads still beat
snapshot artifacts for anything that needs inline action.

## Forbidden behaviors (every mode)

- **Do NOT delegate the relay step to a subagent.** Subagent context lacks
  the canonical bytes and confabulates ids + data. If the bytes don't fit
  your output context, log `packaging_problem` and STOP.
- **Do NOT invent variant ids.** `orgs-map`, `quick-commands`,
  `my-commitments`, verbatim. No `-v2`, `-fixed`, `-new`, `-canonical`
  suffixes.
- **Do NOT improvise a "compact equivalent"** if `create_artifact` fails. The
  renderer output is the only legitimate payload.
- **No Pattern B.** These artifacts are static + click-to-chat. No
  `callMcpTool` inside the iframe; pass `mcp_tools: []`.
- **Don't add Quick Commands rows without the user's review.** It is a
  curated list, not a skill index.
- **Does not handle uninstall.** "Unpin [X]" is a manual Cowork sidebar
  action, not a skill flow.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill. The description is
budget-capped (G11); everything below remains binding at fire time and is
enforced mechanically by tests/triggers.yaml.

> The menu: 'level up command room', 'level up my command room', 'show me dashboards', 'what dashboards can I install', 'show me what I can enable', 'add more dashboards', 'level me up', 'my dashboards', 'sidebar dashboards'. Workspace Map (artifact id orgs-map, formerly enable-workspace-map): 'install workspace map', 'enable workspace map', 're-install workspace map', 'rebuild workspace map', 'refresh workspace map', 'install orgs map', 'enable orgs map', 'rebuild orgs map'. Quick Commands (artifact id quick-commands, formerly enable-quick-commands): 'install quick commands', 'enable quick commands', 'rebuild quick commands', 'refresh quick commands'. My Open Commitments (artifact id my-commitments): 'install my commitments', 'enable my commitments', 'rebuild my commitments dashboard', 'refresh my commitments dashboard', 'my commitments dashboard', 'my open commitments dashboard', 'install the commitments dashboard'. DOES NOT fire on 'install command room' / 'set up command room' (command-room-onboarding), 'update command room' / 'install my dashboards' / 'install missing dashboards' (command-room-update-bridge — it calls this skill silently for the Layer 1 defaults), 'triage my commitments' / 'show me my commitments' (commitment-triage — the actionable list), or 'set up command room schedules' (enable-command-room-schedules).
