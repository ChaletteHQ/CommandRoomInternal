---
name: scaffold-automation
surfaces: both
description: "Generate real working artifacts — Zapier zap config, Python script skeleton, n8n flow JSON, or a setup recipe — for an automation opportunity automation-scanner surfaced (or one the CEO names). Fires on: 'scaffold that automation', 'build the automation for [opportunity]', 'set up that zap', 'generate the script for [task]', 'make the automation recipe'. Output is deploy-ready scaffolding with a setup checklist, saved to the matching project; deployment itself stays with the CEO (mark it deployed when live). Does NOT fire on 'what can be automated' / 'automation scan' (automation-scanner — the detection and ranking this consumes), or 'schedule a meeting' (calendar-writer). Artifact types and recipe format: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use scaffold-automation for:** generating the actual artifacts for an automation opportunity (zap config, Python skeleton, n8n flow, setup recipe). Pairs with automation-scanner.
- **Use `automation-scanner` for:** identifying opportunities (the upstream scan).
- **NOT for troubleshooting existing automations** — that's manual debug work.
- **NOT for arbitrary code generation** — must reference an opportunity surfaced by automation-scanner (or an explicit description of one).

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for (artifacts):**
- `<workspace>/automations/<slug>/zap-config.json` (if Zapier is the target tool)
- `<workspace>/automations/<slug>/flow.json` (if n8n is the target)
- `<workspace>/automations/<slug>/script.py` (if a Python script is the target)
- `<workspace>/automations/<slug>/setup-recipe.docx` — user-facing setup instructions. Per CONTRACT Rule 27 (no .md deliverables) the setup recipe is `.docx`.
- `<workspace>/automations/<slug>/rollback.docx` — how to undo if the automation breaks. Also `.docx`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `automation_scaffolded` with `{opportunity_event_seq, slug, target_tool, estimated_time_saved_minutes_per_week, artifacts: {zap_config_path?, flow_path?, script_path?, setup_recipe_path}}`. The `opportunity_event_seq` links back to the `automation_opportunity_surfaced` event that this scaffold came from.
- `_hq/data/events.jsonl` — event type `automation_deployed` when the user marks the automation deployed (via the widget action). Carries `{scaffold_event_seq, deployed_at_ts}`. Canonical-shape substrate intended for future consumers (a 30-day-later verification pass in cleanup or insight-generator) — no consumer reads it yet as of v3.12.0, but the event records the deployment so the verifier can be added later without re-shaping existing events.

**Reads from:**
- `_hq/data/events.jsonl` — the referenced `automation_opportunity_surfaced` event by seq (the input).
- `_hq/data/entities.json` — current tool stack (Gmail, QB, Sheets, etc.) so the scaffold targets tools the user actually uses.
- `_hq/data/events.jsonl` — prior `automation_scaffolded` and `automation_deployed` events to avoid duplicating existing automations.
- `<workspace>/automations/` — existing automations so the new one doesn't conflict.

**Conflict boundary:** sole writer of `automation_scaffolded` and `automation_deployed` events. Reads but does not write `automation_opportunity_surfaced` (that's automation-scanner's domain).

---

# scaffold-automation

Closes the loop on `automation-scanner`. Pre-v3.8.0 the scanner produced a `.docx` of ranked opportunities — and that was it. No path from "this is an opportunity" to "this is a working automation." Users opened the report, were inspired, and then nothing happened.

This skill takes a picked opportunity and produces the actual artifacts: zap config you can drag into Zapier, Python script you can pip-install-and-run, n8n flow JSON you can import, plus user-facing setup instructions and a rollback doc.

## What It Does

For a picked opportunity (referenced by automation_opportunity_surfaced event seq OR opportunity number from the most recent scan):

1. Loads the opportunity from events.jsonl.
2. Picks the right target tool based on the opportunity's pattern and the user's available tools (entities.json tool stack).
3. Asks 2-3 scoping questions if needed (which Gmail account, which Sheet, what trigger frequency).
4. Generates the artifact files in `<workspace>/automations/<slug>/`.
5. Writes the `automation_scaffolded` event.
6. Surfaces a widget with setup instructions + "Mark deployed" action.

## How to Use

```
"scaffold #3"                    (after automation-scanner just fired; refs item 3)
"scaffold the QB-estimates one"
"build the automation for [opportunity title]"
"scaffold opportunity 1247"     (explicit event seq)
"set up the gmail-to-sheets automation"
```

If no opportunity is identifiable (no recent scan, no event seq, no clear title match), surface "Run `automation scan` first — I need an opportunity to scaffold."

## How It Works

### Phase 1 — Resolve the opportunity

Parse the trigger for opportunity reference:
- Numeric (`#3`, `opportunity 1247`) → look up by event seq OR by rank in most recent scan
- Title fragment (`"QB estimates"`) → fuzzy match against `automation_opportunity_surfaced` events from the last 30 days
- No match → surface "which opportunity? Here are the recent ones:" with a quick list

Load the `automation_opportunity_surfaced` event by seq. Capture `opportunity_event_seq`.

### Phase 2 — Pick target tool

From the opportunity's `suggested_build_approach` + user's tool stack (entities.json):
- If the trigger pattern is email → action, and Zapier is in the stack → Zapier
- If the action requires custom logic that Zapier can't express → Python script
- If the user has n8n in the stack and prefers it (preference flagged in BUSINESS_CONTEXT) → n8n flow
- Default to setup-recipe-only (manual steps) if no tool fits

### Phase 3 — Ask scoping questions

For the picked tool, surface 2-3 questions only if necessary:
- "Which Gmail account?" (if multiple connected)
- "Which Google Sheet?" (URL or new)
- "Trigger on every match or daily batch?"
- "Notify you on errors?" (default: Yes, via Slack DM)

Skip questions whose answers can be inferred from prior automations or BUSINESS_CONTEXT.

### Phase 4 — Generate artifacts (via canonical helper)

Use `shared/scripts/scaffold_automation.py` for all filesystem operations. The skill prompt composes artifact CONTENT (Zap JSON, Python skeleton, n8n flow, recipe text); the helper handles slug derivation + directory creation + atomic writes + pre-flight conflict detection. Hand-rolling `Write` calls is forbidden — same discipline as `brief_writer.py` / `people_writer.py`.

**Templates by tool:**

**Zapier:**
- `zap-config.json` — import-ready Zap definition
- `setup-recipe.docx` — step-by-step: import, connect accounts, paste sheet URL, test, turn on

**Python script:**
- `script.py` — runnable script with config block at top
- `requirements.txt` — pinned dependencies
- `setup-recipe.docx` — pip install, env-var setup, cron / Task Scheduler instructions

**n8n:**
- `flow.json` — import-ready workflow
- `setup-recipe.docx` — import, configure credentials, activate

**All targets:**
- `rollback.docx` — explicit undo instructions in case the automation misfires

**Invocation pattern** (run inside the canonical `cd "$PLUGIN_ROOT"` block per CONTRACT Rule 22):

```bash
cd "$PLUGIN_ROOT" && WORKSPACE="$WORKSPACE" python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from scaffold_automation import slugify, write_artifacts, make_recipe_docx
import os

workspace_root = os.environ['WORKSPACE']
title = '<opportunity.title from Phase 1>'
slug = slugify(title)

# Skill prompt has composed the artifact CONTENT in these variables:
zap_config_json = '''<json string>'''       # if Zapier target
flow_json       = '''<json string>'''       # if n8n target
script_py       = '''<python string>'''     # if Python target
rollback_md     = '''<plain text — converted to .docx via make_brief>'''

# Build the file map for the picked tool. setup-recipe.docx + rollback.docx
# are written by make_recipe_docx + make_brief, NOT through write_artifacts.
files = {}
if target == 'zapier':
    files['zap-config.json'] = zap_config_json
elif target == 'n8n':
    files['flow.json'] = flow_json
elif target == 'python':
    files['script.py'] = script_py
    files['requirements.txt'] = requirements_txt

# Atomic write of code/config artifacts. Pre-flight conflict check fires
# BEFORE any write — if any target file exists, raises FileExistsError
# and nothing is written.
paths = write_artifacts(workspace_root, slug, files)

# Render the user-facing .docx setup recipe via brief_writer (CONTRACT Rule 27).
recipe_path = os.path.join(workspace_root, 'automations', slug, 'setup-recipe.docx')
make_recipe_docx(
    recipe_path,
    title=title,
    subtitle='<one-line summary>',
    steps=[<list of setup steps>],
    rollback_steps=[<list of rollback steps>],
    estimated_time_saved_minutes_per_week=<int>,
)

# Render the .docx rollback doc via brief_writer directly (same pipeline).
from brief_writer import make_brief
rollback_path = os.path.join(workspace_root, 'automations', slug, 'rollback.docx')
make_brief(
    rollback_path,
    brief_kind='automation_recipe',  # same eyebrow-style layout
    title=f'{title} — rollback',
    subtitle='How to undo if the automation misfires',
    sections=[{'heading': 'Rollback steps', 'bullets': [<list>]}],
    footer_text='Command Room — automation rollback',
)

print(json.dumps({'slug': slug, 'paths': paths, 'recipe': recipe_path, 'rollback': rollback_path}))
"
```

**Both `.docx` files render through brief_writer, and nothing else (DOCFENCE1).** The recipe and the rollback doc are deliverables the user acts on, so they carry the same render discipline as every other Command Room document:

- **NEVER hand-roll either file** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking doc (the v3.20.0 failure mode) — and a rollback doc that skipped the gates is the worst one to get wrong.
- **NEVER create, render, copy, upload, or update either file — or any part, derivative, or restatement of it ("the setup steps", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `automations/<slug>/` where the rest of the scaffold lives (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so the tool owner can follow along", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the recipe in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link.

**On `FileExistsError`** from `write_artifacts`: the slug is taken. Surface plain English: *"There's already an automation by that name. Want to give this one a different name? Say `scaffold #N as <new-name>`."* Do NOT improvise by appending `-2` or overwriting.

**On `ValueError` for empty slug**: the opportunity title cleaned to nothing (all punctuation). Ask the user for a name: *"I need a short name for this automation — what should I call it?"*

### Phase 5 — Write event + render widget

Append `automation_scaffolded` event. Render the deployment widget:

```
Built: QuickBooks estimates to Sheets

I put everything in one folder for you. Open the setup recipe first — it walks you through it.
  - zap-config.json     (the Zap, ready to import)
  - setup-recipe.docx   (step-by-step — start here)
  - rollback.docx       (how to undo it if you need to)

Setup takes about 10 minutes:
  1. zapier.com → Create Zap → Import → drag in zap-config.json
  2. Connect Gmail when it asks
  3. Open the Sheets template, File → Copy
  4. Paste your copy's URL into the Zap's Action step
  5. Turn the Zap on
  6. Send a test email that matches "Estimate from [vendor]: $[amount]"

This should save you about 36 minutes a week — roughly 30 hours a year.

Open the setup recipe: [setup-recipe.docx H2 link — Rule 3 doc link, not a button]

[Mark done]  [Snooze (7 days)]
```

### Phase 6 — On `mark done` (displays "Mark done" — the deployed confirmation; P1.1 respec, dispatch in apply-choices' `scaffold-automation` source entry)

Append `automation_deployed` event. **No verifier consumes it yet** (true state per the Writer Contract above): the event records the deployment so a future 30-day verification pass — planned for cleanup or insight-generator — can check "did the manual pattern stop?" without re-shaping existing events. Never tell the user a verification will fire; it doesn't yet. `snooze 7d` (displays "Snooze (7 days)") re-surfaces the deployed-yet? check in a week.

## DOES NOT

- Deploy the automation itself. The scaffold produces artifacts + instructions; user does the deploy. Marking deployed in the widget is the user attesting the deploy is live; this skill doesn't reach into Zapier's API to turn things on.
- Scaffold an opportunity that's already been deployed (filter via `automation_deployed` events).
- Generate arbitrary code. Must reference an opportunity surfaced by automation-scanner OR an explicit user-provided pattern description that follows the same shape.
- Modify or override existing automations under `<workspace>/automations/<slug>/`. New automations get a new slug.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Generate real working artifacts (Zapier zap config, Python script skeleton, n8n flow JSON, setup recipe) for an automation opportunity that automation-scanner surfaced. Pairs with automation-scanner: scanner identifies opportunities and writes automation_opportunity_surfaced events; this skill scaffolds the picked opportunity and writes automation_scaffolded events. Use when the CEO says 'scaffold the [opportunity name]', 'scaffold automation #N', 'build the automation for X', 'build the automation', 'scaffold the [tool] automation', 'create the automation', 'build out [opportunity]', 'set up the automation for X'. Reads the referenced automation_opportunity_surfaced event from events.jsonl, entities.json for current tool stack, and prior automation_deployed events to avoid duplicates. Writes automation_scaffolded events (and automation_deployed when user marks deployed). DOES NOT fire on 'automation scan' (that's automation-scanner — runs the scan), 'what automations do I have' (that's a query — workspace-manager), or 'fix my zap' (out of scope — troubleshooting).
