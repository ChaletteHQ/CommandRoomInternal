---
name: stalled-projects
surfaces: both
description: "Surface every project that has gone quiet — no meetings, commitments, or decisions within the threshold — so the CEO can resurrect, snooze, or archive each with one tap. Fires on: 'stalled projects', 'what's stalled', 'which projects went quiet', 'stale projects', 'what's gathering dust', plus 'tune stalled-projects'. Renders a widget with draft re-engagement / status check / keep paused / snooze / archive actions dispatched through the standard path; suppressions learned from repeated dismissals are honored. Does NOT fire on 'who went dark' / 'dormant customers' (dormant-customer-scan — people, not projects), 'warm threads to revive' (thread-resurrection — conversations), or 'list projects' (list-active). Thresholds, scoring, and fences: Routing section in the body."
---

## Recommended Model

**Default: Sonnet.** This skill reads the activity timeline + renders a list. No drafting, no voice work. Sonnet is plenty.

---

## Skill Boundary (v2.1)

- **Use stalled-projects for:** project-focused activity-gap detection. Output is a ranked list of stalled projects with a recommended action per item.
- **Use `dormant-customer-scan` for:** people-focused cadence breaks (a customer relationship that's gone quiet). Different unit of analysis.
- **Use `list-active` for:** rendering the whole project roster without filtering by activity.
- **Use `cleanup` for:** broader workspace hygiene that includes stall as one check among many.
- **The `lifecycle` maintenance job WRITES; this skill ASKS.** The weekly job (`shared/scripts/lifecycle_pass.py`) proposes the dormancy questions and applies the transitions the lifecycle rules already decided (30-day ask, 60-day dormant after an unanswered ask, 180-day archive, revive on new activity). This skill never flips a status of its own accord — it renders the job's questions and the CEO's tap is what answers them.
- **Use `pipeline-tracker` for:** deal threads. `kind="deal"` threads are EXCLUDED from this scan by fence (SPEC PIPE1 D7 — `stall_detector.detect_stalled_projects` skips them in code): deal rot is stage-dependent (a negotiation goes stale in 7 days, a lead in 10) and reports through the pipeline surface's per-stage thresholds. One quiet deal must never be double-flagged by both scans.
- **Use `objectives` for:** standing-objective threads. `kind="objective"` threads are EXCLUDED from this scan by the same in-code fence (SPEC OBJ1, DRAFT): objective drift is binding-dependent (a self-reported objective drifts on missed check-ins, a meeting-reviewed one on undiscussed sessions — not on generic thread quiet) and reports through the objectives surfaces with a suggested move. One drifting objective must never be double-flagged.

## Writer Contract

- **Reads from:** `_hq/data/entities.json` (projects), events via `shared/scripts/thread_activity.py::derive_thread_activity` (shard-transparent; top-level `primary_thread_id` + `related_thread_ids` + legacy id spellings, filtered by activity event types from config, `honor_reclassifications=True` — RECL1: user-approved corrections move activity with the event, so day-counts are honest in both directions), `_hq/data/skill_config/stalled-projects.json` (first-run questionnaire answers), and the prior `pack_run` receipt (nag-dedup, below).
- **NEVER from `thread.last_activity`.** The field is DEPRECATED (v4.5.2, FINDINGS F-54/F-61): the cleanup autopsy proved no code path maintains it — ranking by it reported "43 days quiet" on a project with two same-day meetings. Staleness derives from events at read time; `stall_detector` consults the stored field only for threads with zero event history. See `references/ORG_AND_THREAD_MODEL.md` § last_activity deprecation.
- **Writes to:** three declared, narrow paths — (1) `_hq/data/skill_config/stalled-projects.json` on first fire, tune, and reset, always via `skill_config_writer` (`save_skill_config` / `wipe_skill_config`); (2) a snooze-suppression event (the `snooze 14d` action's `dont_forget_snooze`-style append, dispatched through apply-choices) via `atomic_append_jsonl`; (3) a `pack_run` scan receipt at the end of every Detect fire via `receipts.log_receipt` (v4.5.2 C3 — see "Scan receipt" below). Detection itself persists nothing else — it surfaces flags only. The stall-state write side (emitting `project_stalled_flagged` events on state change) lands in v3.14.2 when the cleanup integration adds the once-weekly recording call.
- **No raw substrate writes.** Every write above (and the v3.14.2 write side when it ships) goes through the canonical writer helpers — `skill_config_writer` / `atomic_append_jsonl` / `receipts.log_receipt` (per Bug #81 architectural fix). Hand-rolled writes are forbidden.

## What It Doesn't Do

- Does NOT chase, archive, or modify anything. Surfaces the list; CEO decides.
- Does NOT draft outreach emails for stalled projects. If asked, delegates to `email-writer` or `follow-up-ritual`.
- Does NOT filter customers / people — that's `dormant-customer-scan`.
- Does NOT modify project status. If a project is exploring and stalled at 35 days, the skill flags it but does not auto-move it to dormant.

## How to Use

```
"show me stalled projects"
"what's stalled"
"stalled project check"
"what projects have gone quiet"
"project hygiene check"
"show me dead projects"
```

Optional scope modifiers:
- "stalled active projects" — limit to active status
- "stalled exploring projects" — limit to exploring status
- "stalled for 30+ days" — override default threshold inline

## How It Works

### First-Run Personalization (SPEC FRP1) — Reference Implementation

This skill is the named **Reference Implementation** of the First-Run Personalization Protocol
(`shared/FIRST_RUN_PROTOCOL.md`). The four-mode dispatch, first-fire auto-save of defaults, the
once-ever tune footer, and the freeform tune table below are the shape every adopting skill copies.

Canonical helper contract for any skill generalizing this pattern (`shared/scripts/skill_config_writer.py`):
read config via `get_config(workspace_root, "stalled-projects", DEFAULTS)` (deep-merges saved over
defaults — a v+1 decision never breaks an old config); gate the first-run block with
`is_configured(workspace_root, "stalled-projects")` so it renders exactly once; persist with
`save_skill_config(...)`; reset with `wipe_skill_config(...)`. Here the per-skill defaults dict is
named `DEFAULT_CONFIG` (the protocol's `DEFAULTS`) — see the Detect-mode flow below; new adopters
should name theirs `DEFAULTS` per the protocol. The Detect flow below uses the equivalent
`load_skill_config(...) is None` first-fire check; `is_configured(...)` is the protocol's preferred
form for the same gate.

### Trigger intent — four modes (SHOW-THEN-TUNE pattern)

This skill follows the CR-canonical **"Show, then tune"** pattern: produce useful output IMMEDIATELY with sensible defaults, then offer to tune. Never block first-time users behind a questionnaire.

Before running any logic, parse which mode the user invoked:

| Mode | Example phrases | Behavior |
|---|---|---|
| **Detect** (default) | "show me stalled projects", "what's stalled", "project hygiene check" | **No questions, ever.** Load config (or use defaults if none saved). Run detection. Render widget. On FIRST fire only: auto-save defaults + append a one-time "tune this if you want" footer. |
| **Show settings** | "show my stall settings", "what are my stall settings", "show stalled-projects config" | Load config → display current answers in a read-only widget. Do NOT run detection. Do NOT ask any questions. |
| **Tune** | "tune stalled-projects", "change my stall settings", "change stall thresholds", "reconfigure stalled-projects", "redo stalled-projects setup" | Load current config → walk the 3 questions with current answers pre-filled as defaults → save → re-run detection + render with new settings. |
| **Reset to defaults** | "reset stalled-projects to defaults", "reset stall settings" | Call `wipe_skill_config(workspace_root, "stalled-projects")` → confirm "Settings reset. Next fire uses defaults again." Do NOT run detection. |

### Detect mode — the "show, then tune" flow

**This is the default path. ~95% of fires hit this path.**

```python
config = load_skill_config(workspace_root, "stalled-projects")
is_first_fire = (config is None)

# Detect uses defaults if no config saved (stall_detector handles this internally)
flags = detect_stalled_projects(workspace_root)

# LIVE-CHECK GATE (v4.5.2 C3 — REQUIRED, see "Live-check gate" below):
# live-check every flagged project against Gmail + Calendar, then
flags, dropped = apply_live_check(flags, live_signals)

# Render widget with flags (or "nothing's stalled — nice work" if empty);
# mention dropped false-positives in one honest line each

# SCAN RECEIPT (v4.5.2 C3 — REQUIRED, every Detect fire; see "Scan receipt")
log_receipt(workspace_root, "stalled-projects", fired_via="manual",
            surfaced=len(flags),
            extra_data={"flagged_thread_ids": [f["thread_id"] for f in flags],
                        "live_check_dropped": len(dropped)})

if is_first_fire:
    # Auto-save defaults so subsequent fires skip this branch
    save_skill_config(workspace_root, "stalled-projects", DEFAULT_CONFIG)
    # Append one-time tune offer below the widget
    append_chat("""
First time checking your projects for stalls. I used the recommended defaults — 14 days for active
projects, 30 for exploring, 45 for paused, 14 for blocked, 90 for dormant. Archived projects are
never flagged.

Want to change what counts as stalled, or what counts as activity (meetings, emails, decisions)? Say
**"tune my stall settings"** to walk a quick 3-question setup, or just tell me what you'd change
("flag paused projects sooner" / "I don't care about exploring projects") and I'll figure out
which setting that maps to.
""")
```

**Subsequent fires:** config exists → load it → run detection → live-check gate → render widget → scan receipt. No footer, no nag.

### Live-check gate (v4.5.2 C3 — MUST, before anything renders as quiet)

> **No project may be flagged stalled from substrate-only data. You MUST live-check every flagged candidate against Gmail + Calendar before rendering it, and respect the result.** This is the same enforcement gate dormant-customer-scan carries (FINDINGS F-57 proved the discipline; F-54 is what shipping without it looks like).

For every flag `detect_stalled_projects` returns:

1. Discover connector tools ONCE per fire via `shared/scripts/live_contact_check.py::discover_live_check_tools(available_tools)`.
2. Query Gmail + Calendar for the most recent touch since the flag's baseline, scoped to the project: search by the project's org/display name and the email addresses of its `stakeholder_person_ids` / `owner_person_id` (resolve via entities). Cost-bound it — you need only "is there anything newer than the baseline," not a full history.
3. Build `live_signals = {thread_id: {"live_last_iso": "<ISO date>", "source": "gmail"|"calendar", "detail": {...}} | {}}` and call `stall_detector.apply_live_check(flags, live_signals)`. The helper enforces the merge: a live touch under the threshold DROPS the flag (with the reason); a live touch newer than substrate but still over threshold corrects the day-count.
4. **Dropped flags get one honest line each in the chat output** — "Set aside N that looked quiet in saved history but aren't: [Project] — emailed 2 days ago." Silent drops hide the discipline; per F-57, visible drops are what earn trust.
5. **Connector unavailable ≠ skip the honesty.** If neither Gmail nor Calendar can be checked (sandbox, connector failure), you still must NOT silently render substrate-only flags as fact. Render them with the caveat: "I couldn't check live email/calendar just now, so these are from saved history only — double-check before acting."

A project with same-day substrate activity never reaches this gate — the event-scan baseline already excludes it. The gate exists for the opposite hole: activity that happened live but was never written to the substrate (the Bug #28 class).

### Scan receipt (v4.5.2 C3 — REQUIRED, every Detect fire)

The dogfood found the sibling scan (dormant customers, F-57) surfacing flags and leaving zero substrate trace — the next scan couldn't dedup its own nags and value receipts couldn't count the work. Same contract here, one line via the canonical helper:

```python
from receipts import log_receipt
log_receipt(WORKSPACE_ROOT, "stalled-projects",
            fired_via="manual",            # "scheduled" on a scheduled fire
            surfaced=len(flags),
            extra_data={"flagged_thread_ids": [f["thread_id"] for f in flags],
                        "live_check_dropped": len(dropped)})
```

**Nag-dedup rule:** before rendering, read the previous `stalled-projects` receipt (`receipts.iter_receipts(WORKSPACE_ROOT, task_ids=["stalled-projects"])`, newest first). A project in the prior receipt's `flagged_thread_ids` that is flagged again is a REPEAT — render it with "still quiet — flagged last scan too" instead of a fresh alarm, and honor any active snooze regardless. Empty scans write the receipt too (`surfaced=0`) — a scan that finds nothing still happened.

### Tune mode — re-run questionnaire with current values pre-filled

When the user fires a tune trigger (after seeing output OR proactively later):

```python
current = load_skill_config(workspace_root, "stalled-projects")
# If somehow current is None (race condition), fall back to DEFAULT_CONFIG values

# Walk the 3 questions with current["config"] values pre-checked / pre-selected.
# User can accept-all (keeps everything as-is) or change individual answers.

save_skill_config(workspace_root, "stalled-projects", new_config)
# save_skill_config auto-detects (file exists) → emits skill_reconfigured event.

# Immediately re-render detection with the new config so the user sees the impact.
new_flags = detect_stalled_projects(workspace_root)
render_chat_output_widget(new_flags, ...)
append_chat("Updated. Showing your stalls with the new settings.")
```

Pre-filling current values is what makes tune feel like "edit settings" rather than "start over." Re-rendering with the new config is what makes the cause-and-effect visible — user sees the threshold they just changed actually changing the output.

**The 3 questions:**

1. **Stall threshold per project status?** (All 6 canonical statuses per `references/ORG_AND_THREAD_MODEL.md`)
   - Active project: 14 days (recommended) / 7 / 21 / 28
   - Exploring project: 30 days (recommended) / 21 / 45
   - Paused project: 45 days (recommended) / 30 / 60
   - Blocked project: 14 days (recommended) / 7 / 21 — blocked too long is itself a signal
   - Dormant project: 90 days (recommended) / 60 / 120 — mostly for archive review
   - Archived projects are never flagged (intentionally retired)

2. **What counts as "activity"?**
   - ☑ Meetings
   - ☑ Commitments (any direction)
   - ☑ Decisions
   - ☑ Real conversations (email, chat)
   - ☐ Intel notes
   - ☐ Status changes

3. **Where should stall flags also surface?** (in addition to on-demand here)
   - ☑ The weekly lifecycle pass (the background job that asks about quiet projects)
   - ☑ Friday Wrap weekly summary
   - ☐ Morning brief if any stall detected
   - ☐ cleanup only

### Freeform tune (preferred when user gives natural-language feedback)

If the user responds to a stall list with natural-language feedback rather than firing the structured tune flow — e.g., "flag paused projects sooner" or "I don't care about exploring projects" — parse the intent and apply the implied config change directly without walking all 3 questions:

| User says | Config change |
|---|---|
| "Flag paused projects sooner" | Lower `thresholds.paused_days` (e.g., 45 → 30) |
| "Don't flag exploring projects" | Bump `thresholds.exploring_days` to a very high value (365) — effectively disables |
| "Only show me active stalls" | Bump non-active thresholds to 365 |
| "Be more aggressive" | Lower all thresholds by ~30% |
| "Be less aggressive" | Raise all thresholds by ~50% |
| "Skip Slack mentions" | Remove `interaction` from `activity_event_types` |
| "Show flags in my morning brief too" | Add `morning_brief_if_any` to `surface_locations` |

After applying the change: `save_skill_config(..., is_reconfigure=True)` + re-render detection + confirm in plain English ("Got it — paused threshold is now 30 days. Here's the updated list."). Don't make the user walk the full questionnaire if a single sentence captured their intent.

### Show settings (read-only)

```python
current = load_skill_config(workspace_root, "stalled-projects")
if current is None:
    respond("I haven't checked your projects for stalls yet. Say 'show me stalled projects' and I'll use the defaults — you can tune anytime after that.")
else:
    # Render current["config"] as a read-only widget with one action: "tune"
    # (which dispatches to tune mode)
```

No questions, no detection. Just shows the current answers + offers a one-click path to tune.

### Reset to defaults

```python
existed = wipe_skill_config(workspace_root, "stalled-projects")
if existed:
    respond("Settings reset. Next time you ask for stalled projects I'll use the recommended settings — and I'll offer the setup options once.")
else:
    respond("No settings to reset — you're already on defaults.")
```

### Subsequent fires — detection + render

```python
import sys
# Rule 22: run from $PLUGIN_ROOT (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}")
sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT
from stall_detector import detect_stalled_projects, apply_live_check

flags = detect_stalled_projects(workspace_root)
# ... live-check gate (see above), then apply_live_check(flags, live_signals)
```

`flags` is a list of dicts, one per stalled project:

```python
{
    "thread_id": "project_NNN",
    "thread_status": "active",
    "days_since_activity": 18,      # derived from EVENTS at read time (C3)
    "threshold_days": 14,           # the threshold this status was judged against
    "last_event_seq": 4203,         # None unless baseline_source == "event_scan"
    "last_event_type": "meeting",   # None unless baseline_source == "event_scan"
    "baseline_source": "event_scan",  # "last_activity" (zero-event record-stamp
                                      # fallback) | "first_seen" (zero history)
    "recommended_action": "18 days since last activity — worth a touch this week."
}
```

The day-count here and the `lifecycle` job's day-count come from the SAME derivation (`thread_activity.derive_thread_activity`, same activity-type set, `honor_reclassifications=True`) — the surface that ASKS and the job that WRITES can never quote different numbers for the same project on the same day (F-54's 21d-vs-37d split, inherited from the Pulse phase this replaced).

### The dormancy questions (SPEC LIFECYCLE1 — THE asking surface, MANDATORY)

**This skill is the ONE place the CEO is asked whether a quiet project has actually gone dormant.** Nothing else asks. Skip this block and the questions have no door at all.

How it got here: the weekly `lifecycle` maintenance job (`shared/scripts/lifecycle_pass.py`) proposes a dormancy question for every active project past 30 days quiet. STAFFCUT §3.7 (M's ruling 2026-08-02) demoted those rows to ON-DEMAND — whether a quiet project is dormant is a judgment the CEO makes when he asks, not weekly homework on the staff meeting — and the retired Pulse chat's REVIEW section used to be where they showed. LIFECYCLE1 moved the asking here, to the surface that already asks about quiet projects, with the same unit of analysis and near-identical verbs.

Read them alongside the stall flags on EVERY Detect fire:

```python
import sys; sys.path.insert(0, "shared/scripts")   # cwd == $PLUGIN_ROOT per Rule 22
from brain_proposals import load_open_proposals
dormancy_rows = [i for i in load_open_proposals(WORKSPACE_ROOT, "on-demand")
                 if i.get("kind") == "dormancy"]
```

- `"on-demand"` is the surface argument and it is load-bearing: a plain `load_open_proposals(ws, "stalled-projects")` returns NONE of these rows (the projector's demotion filter drops the kind for every other named surface). Never work around that filter — pass the hint.
- **Render each row VERBATIM.** `render_line` is the ask ("<Project> has gone quiet — still active, or archive it?"), `evidence` is the day-count, and `action_tuples` carries the registered verbs — `active` / `archive` / `snooze 14d`. Take them from the row; never invent a verb and never re-word the ask (Bug #92b).
- **Wire ids embedded verbatim (F2):** each row's `n` is its proposal `id`, so `apply-choices` dispatches it statelessly through the `cr-brain` `kind: dormancy` handlers that have always owned these verbs. You dispatch nothing yourself.
- **Own section, own honest count** — `QUIET PROJECTS — still active? (N)`, after the stall flags. A dormancy question and a stall flag are different asks: a stall flag says *this needs a touch*, a dormancy question says *should we stop tracking this*. Never merge them into one list.
- **Drop-empty.** No open rows → no section, no "nothing to review" line.
- These rows are ALREADY live-checked and already deduped by the projector (snoozes, declines and the shared 60-day cooldown are applied before you see them). Do NOT re-run the stall live-check gate over them, and never re-surface one the projector withheld.

### Rendering the surface

Render flags as a widget via `render_chat_output_widget()` per CONTRACT.md Rule 1, posted via `widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`) (`shared/CHAT_ACTION_WIDGET.md` § Transport).

**Executive Output Standard (EXEC1, v3.20.0+) — queues get triage math, NOT meaning.** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`, this is a queue/ranked list, so the synthesis-lead rule FORBIDS a narrative lead — **the lead is a quantified count line**, not a theme: *"6 stalled · 2 touch revenue — $292K · oldest 47d."* Compute it from the flags: total count · how many trace to a valued org · the summed dollar (via `quantify.money_time_tag` / the org's revenue field — ONLY the figures that derive from substrate, never an estimate) · the oldest age. When building the item dict for `money_time_tag`, set its `last_activity` key from the flag's DERIVED baseline (today − `days_since_activity`) — never from the thread record's deprecated `last_activity` field, or the tile contradicts the row it sits on. Each item then carries its own quantify tag (`"47d quiet · $180K"`) when `money_time_tag` returns non-None; date-only otherwise. No manufactured "what this means about your portfolio" sentence — the reader's next act is triage.

Per-item action set (all canonical — P1.1 respec 2026-07-02; dispatch in apply-choices' `stalled-projects` source entry):
- `draft re-engagement` (displays "Draft re-engagement") — opens `email-writer` or `follow-up-ritual` for that project (lazy draft — nothing sends)
- `snooze 14d` (displays "Snooze (14 days) — hide until then", UXR1 D7a) — suppresses re-flag for 14 days (writes a `dont_forget_snooze`-style event referencing the project)
- `mark paused` (displays "Mark paused") — updates the project's status via `workspace-manager`'s writer
- `archive` (displays "Archive") — moves the project to archive
- `status check` (displays "Status check") — surfaces the project's recent-events digest inline before deciding

The CEO sees: project title + "N days quiet" + recommended action + action buttons. Scannable in under a minute even with 10 stalled projects.

**Ranked-report layout (SPEC OUT2 §4 — this queue is one of the four ranked-report surfaces; contract in `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The ranked report").** The widget above maps to the contract — align, don't duplicate: the quantified count line's figures MAY additionally render as the shared **tile summary band** (components.py fragment) at the top of the widget — **stalled** (count) · **$ at stake** (the substrate-derived sum, only when non-None) · **oldest** (max days quiet) — same values as the count line's computation, never a second pass; drop-empty per F-60, and when only the count tile has data skip the band (the count line already carries it). Each flag IS the scored row: rank (severity order) · name (project) · quantify tag (`money_time_tag`, date-only fallback) · why-now ("N days quiet" + the honest live-check status) · action (the recommended action). The widget's per-item buttons are the ask block (one-ask-surface) — never a prose twin.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Next time you fire 'show me stalled projects', it'll offer the tune flow on first re-fire."
- Good: "Next time you ask for stalled projects I'll use the recommended settings — and I'll offer the setup options once."

### Voice tone

Friendly, not alarming. "3 projects haven't moved in 2+ weeks — worth a touch?" — NOT "STALE PROJECTS DETECTED: 3 CRITICAL." Per CONTRACT.md Rule 4 voice.

### Zero-history fallback

A project created 30 days ago with zero activity events flags at the 14-day active threshold via the `first_seen` date on its entities.json record. Recommended action reads: "Created N days ago with no activity since — decide whether to start or archive." These are often the loudest stalls (something started but never followed up).

### No-projects-yet case

If the workspace has zero projects (new install, or all archived), the skill responds: "No projects in your workspace yet. Add one and I'll watch it for stalls." NOT an error.

## Versioning

This skill ships in v3.14.1. The `stall_detector.py` helper it calls is the canonical detector. v3.14.2 adds the once-weekly recording call (`record_stall_state_changes`) wired into cleanup. This skill itself remains the on-demand surface across all v3.14.x releases.

LIFECYCLE1 (2026-08-02): the Pulse chat is retired and this skill inherits the one thing it asked that nothing else did — the dormancy questions (see "The dormancy questions" above). Detection and the retire/revive transitions moved to the weekly `lifecycle` maintenance job, which writes; this skill asks.

v4.5.2 (C3, FINDINGS F-54): staleness now derives from events at read time via `thread_activity.derive_thread_activity` — the deprecated `thread.last_activity` record stamp can never override events. Adds the mandatory live-check gate (`apply_live_check`, F-57 discipline) and the `pack_run` scan receipt with nag-dedup.

RECL1 (2026-07): the derivation honors user-approved reclassifications (`honor_reclassifications=True` in `stall_detector`). Day-counts are now honest in both directions — a thread borrowing misclassified activity may newly flag (correct), and a thread whose activity was moved onto it stops false-flagging. The `lifecycle` job uses the same call shape, so the F-54 one-day-count contract holds through corrections.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Surface every project that has gone quiet — no meetings, commitments, decisions, or real conversations in the configured threshold — so the CEO can decide whether to resurrect, snooze, or archive each one before it falls through the cracks. Reads from the workspace activity timeline + project graph. Use when the CEO says 'show me stalled projects', 'show me stalled', 'what's stalled', 'stalled projects', 'stalled project check', 'what projects are stalled', 'which projects have stalled', 'projects that have gone quiet', 'show me projects that haven't moved', 'project hygiene check', 'show me dead projects', 'what should I prune'. Also handles first-run personalization settings — use when the CEO says 'tune my stall settings', 'tune stalled-projects', 'show stalled-projects settings', 'reset stalled-projects to defaults', 'reconfigure stalled-projects', 'change stalled-projects settings', 'change my stall settings', 'redo stalled-projects setup', 'change stall thresholds', 'show my stall settings', 'what are my stall settings', 'show stalled-projects config', 'reset stall settings'. Runs on demand and is wired into the weekly lifecycle pass + Friday Wrap. DOES NOT fire on 'who went dark' or 'dormant customers' — that's `dormant-customer-scan`, which is people-focused, not project-focused. DOES NOT fire on 'show me my projects' or 'list active projects' — that's `list-active`, which renders the whole roster without filtering by activity. DOES NOT fire on bare 'cleanup' — that's `cleanup`, which surfaces multiple workspace hygiene checks of which stall is one.

> DOES NOT fire on 'which deals are stalling' / 'deal is stalled' (pipeline-tracker, SPEC PIPE1 — this scan excludes deal threads in code, so a quiet deal is never double-flagged).
