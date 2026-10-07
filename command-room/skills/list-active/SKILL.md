---
name: list-active
surfaces: both
description: "Zero-interaction list of all projects in the workspace — the org tree with canonical names, aliases, and last-activity dates. Triggers: 'list projects', 'list active projects', 'list all projects', 'active projects', 'project list', 'review my projects', 'show projects', 'show me projects', 'what projects', 'what projects do I have', 'show archived', 'roster', 'project roster'. DOES NOT fire on 'what's going on' or 'workspace status' (full daily briefing — workspace-manager) or 'lets work' (silent load — workspace-manager) or 'new project' (project creation — workspace-manager)."
---

## Skill Boundary (v2.1)

- **Use list-active for:** instant recall of what projects exist when you've forgotten a name. Renders the full org tree inline in chat. No classification, no briefing, no scan — just the list.
- **Use `workspace-manager` for:** engaging a specific project ("go [project]"), starting/ending sessions, or the full "what's going on" daily briefing.
- **Use `morning-briefing` for:** the daily digest with calendar + email + urgency flags.

list-active is a 5-second discovery tool. It is NOT a briefing and NOT a status scan.

## Personification Contract (v3.13.8.4+)

Before rendering the tree, read the canonical voice spec at `shared/PERSONIFICATION.md` and call `get_brain_name(workspace_root)` from `shared/scripts/personification.py`. The tree footer (last line, after the render) is a single line: `"— {brain_name}"`. Default `{brain_name}` is `"Penelope"`. No intro line — list-active is a zero-interaction tool, the footer is the only personification surface.

## Writer Contract

Read-only. No events written. No entities mutated. No connector calls. Pure render over existing `entities.json` + `events.jsonl`.

---

# List Active — Project Roster

Render the full org tree with every project (active, dormant, archived optionally filtered) so the CEO can see canonical names and aliases at a glance. Designed for the "wait, what did I call that?" moment mid-session.

## Triggers

| Phrase | Mode |
|--------|------|
| `list projects` / `list active projects` / `active projects` | default (active only) |
| `project list` / `show projects` / `show me projects` | default |
| `what projects` / `what projects do I have` | default |
| `roster` / `project roster` | default |
| `list all projects` / `show archived` | include archived |

Default mode filters out `status: archived` projects. Archived view includes everything.

## How It Works

### Step 1: Read the data layer

1. `[WORKSPACE_ROOT]/_hq/data/entities.json` — orgs + projects + people
2. `[WORKSPACE_ROOT]/_hq/data/events.jsonl` — compute `last_activity` per project by scanning most recent event per `primary_thread_id`
3. `[WORKSPACE_ROOT]/_hq/data/aliases.json` — for each project, collect any aliases that point to it

If `entities.json` doesn't exist (pre-v2 workspace), fall through to **folder fallback** (Step 4).

### Step 2: Build the tree

Group projects under their orgs. Order:

1. **Primary focus orgs** (where `is_primary_focus: true`), grouped with their children:
   - Holdings first (those with `scope: holding`), their operating children nested underneath
   - Standalone operating orgs next (no parent)
2. **Advisory** orgs (`scope: advisory` or `relationship_type: advisor`)
3. **Other** orgs (`scope: beneficiary` / `other` / `personal`)
4. **Workspace-level projects** (projects with `affiliation_id` = workspace, no org parent) at the end

Within each org, sort projects by `last_activity` descending (freshest first).

### Step 3: Render

Compact, scannable. One project per line. Format:

```
PRIMARY FOCUS

[Holding Co] (holding)
  ├─ Command Room (product) · last Apr 21
  └─ _[Company] HQ_ (operating) · last Apr 21

NorthStar (operating · client)
  ├─ Margin analysis · last Apr 19
  └─ Sales materials · last Apr 12

Acme Property (operating · aka [Operating Co], Property Alpha) · last Apr 18
  ├─ Property Alpha remediation · last Apr 18
  │   └─ Vendor coordination · last Apr 17
  └─ GC entity formation · last Apr 10

Acme Co (operating · aka acme, the sourcing co)
  └─ Sourcing bot rollout · last Apr 15
      ├─ Vendor onboarding · last Apr 15
      └─ Bid leveling · last Apr 14

TalentCorp (operating) · last Apr 12

ADVISORY

Traders Inc (advisory · aka the trading group, quant desk) · last Apr 19

OTHER

Personal · last Apr 5

NOT TIED TO A COMPANY

(none)
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "WORKSPACE-LEVEL" / "Total: 12 projects across 4 orgs."
- GOOD: "NOT TIED TO A COMPANY" / "Total: 12 projects across 4 companies."

Rules:
- Canonical org/project name first.
- Aliases in parens after the name when aliases.json has entries.
- `scope` and/or `relationship_type` in parens when useful (e.g., "operating · client", "holding", "advisory").
- Sub-projects (those with `parent_thread_id` set) nest under their parent using `├─`/`└─`.
- `last [date]` on every line. When no events exist for that project, render `last —` (see Edge Case B) — never omit the line.
- Empty sections render as `(none)`. Do not omit the section header.

### Step 4: Folder fallback (no entities.json)

If `_hq/data/entities.json` doesn't exist:

1. Scan `[WORKSPACE_ROOT]/` for top-level folders.
2. Skip: `_hq`, `_archive`, `_people`, anything starting with `.` or `_`.
3. For each folder, look for `SESSION_NOTES_*.md`. Use its mod time as the activity date.
4. Render flat list (no org hierarchy available):

```
PROJECTS (showing folders only — workspace not fully set up yet)

NorthStar · last Apr 19
Acme Property · last Apr 18
Acme Co · last Apr 15
TalentCorp · last Apr 12
Skyler · last Apr 19

Say "onboard me" to set up the full project view.
```

Fallback output always includes the onboarding hint at the bottom.

## Edge Cases

**A. Empty workspace (no projects at all):**
Render: `No projects found. Say "onboard me" to set up your workspace, or "new project [name]" to create one.`

**B. Project exists in entities.json but has zero events:**
Render the line with `last —` instead of a date. Do not omit.

**C. Orphan project (project with `affiliation_id` pointing to a missing org):**
Render under a `NEEDS A HOME` section at the bottom with a one-line note: `This project isn't linked to a company yet. Say "cleanup" and I'll sort it.`

**D. Aliases list too long:**
If a project has more than 3 aliases, render the first 3 followed by `+N more` — full list available via `show aliases for [project]` (handled by workspace-manager).

**E. Sub-project deeper than 2 levels:**
Support arbitrary depth in the tree with indented `├─`/`└─`, but cap visible depth at 3. If deeper nesting exists, render `+N deeper — say 'expand [project]' to see` (workspace-manager handles the expand).

**F. Very large workspace (>50 projects):**
Render the full tree anyway — this is discovery, not briefing. Long output is the right answer. Add a footer: `Total: [N] projects across [M] companies.`

**G. Archived vs active filter:**
Default mode excludes projects with `status: archived`. When the user triggers with `list all projects` or `show archived`, include those with an `[archived]` suffix on the line.

## Implementation Notes

- The reference script `render_tree.py` does the org-tree traversal + last-activity computation. It's a pure read over entities.json + events.jsonl and can be invoked directly or embedded as the render logic inside the skill response.
- Last-activity date is computed from the most recent event where `primary_thread_id` equals the project's id. Events where the project appears only in `related_thread_ids[]` do NOT count toward last-activity (cross-refs don't promote the thread's primary activity date).
- This skill does not mutate state and does not interrupt the CEO with follow-up prompts — it renders and returns.

## Cross-skill handoff

- **workspace-manager** — after the list is rendered, the CEO typically says "go [project name]" which hands off to workspace-manager for the actual engagement.
- **morning-briefing** — can reference list-active at the top: "You have [N] active projects." (soft link; morning-briefing remains its own skill)
- **cleanup** — uses the same org-tree rendering logic for its audit report; they can share the `render_tree.py` helper.

## What It Doesn't Do

- Does not scan connectors (no Gmail/Calendar/Slack reads). Pure data-layer render.
- Does not interpret activity (no "this project is stuck" commentary — that's insight-generator).
- Does not archive, create, or modify projects (workspace-manager owns lifecycle).
- Does not produce a report file — the output is chat-only.
- Does not surface people or relationships — that's people-crm.
- Does not render briefing content (calendar, email, Slack) — that's morning-briefing.

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
