---
name: scaffold-automation
surfaces: both
description: "Generate real working artifacts — Zapier zap config, Python script skeleton, n8n flow JSON, or a setup recipe — for an automation opportunity automation-scanner surfaced (or one the CEO names). Fires on: 'scaffold that automation', 'build the automation for [opportunity]', 'set up that zap', 'generate the script for [task]', 'make the automation recipe'. Output is deploy-ready scaffolding with a setup checklist, saved to the matching project; deployment itself stays with the CEO (mark it deployed when live). Does NOT fire on 'what can be automated' / 'automation scan' (automation-scanner — the detection and ranking this consumes), or 'schedule a meeting' (calendar-writer)."
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
- **NEVER create, render, copy, upload, or update either file — or any part, derivative, or restatement of it ("the setup steps", "a summary") — through Claude Docs (the built-in docs / artifact page), Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `automations/<slug>/` where the rest of the scaffold lives (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so the tool owner can follow along", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the recipe in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link.

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

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Generate real working artifacts (Zapier zap config, Python script skeleton, n8n flow JSON, setup recipe) for an automation opportunity that automation-scanner surfaced. Pairs with automation-scanner: scanner identifies opportunities and writes automation_opportunity_surfaced events; this skill scaffolds the picked opportunity and writes automation_scaffolded events. Use when the CEO says 'scaffold the [opportunity name]', 'scaffold automation #N', 'build the automation for X', 'build the automation', 'scaffold the [tool] automation', 'create the automation', 'build out [opportunity]', 'set up the automation for X'. Reads the referenced automation_opportunity_surfaced event from events.jsonl, entities.json for current tool stack, and prior automation_deployed events to avoid duplicates. Writes automation_scaffolded events (and automation_deployed when user marks deployed). DOES NOT fire on 'automation scan' (that's automation-scanner — runs the scan), 'what automations do I have' (that's a query — workspace-manager), or 'fix my zap' (out of scope — troubleshooting).

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```
