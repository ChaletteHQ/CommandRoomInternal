---
name: stalled-projects
description: "Surface every project that has gone quiet — no meetings, commitments, decisions, or real conversations in the configured threshold — so the CEO can decide whether to resurrect, snooze, or archive each one before it falls through the cracks. Reads from the workspace activity timeline + project graph. Use when the CEO says 'show me stalled projects', 'show me stalled', 'what's stalled', 'stalled projects', 'stalled project check', 'what projects are stalled', 'which projects have stalled', 'projects that have gone quiet', 'show me projects that haven't moved', 'project hygiene check', 'show me dead projects', 'what should I prune'. Also handles settings management — use when the CEO says 'reconfigure stalled-projects', 'change stalled-projects settings', 'change my stall settings', 'redo stalled-projects setup', 'change stall thresholds', 'show my stall settings', 'what are my stall settings', 'show stalled-projects config', 'reset stalled-projects to defaults', 'reset stall settings'. Runs on demand and is wired into Pulse + Friday Wrap (in subsequent releases). DOES NOT fire on 'who went dark' or 'dormant customers' — that's `dormant-customer-scan`, which is people-focused, not project-focused. DOES NOT fire on 'show me my projects' or 'list active projects' — that's `list-active`, which renders the whole roster without filtering by activity. DOES NOT fire on bare 'cleanup' — that's `cleanup`, which surfaces multiple workspace hygiene checks of which stall is one."
---

## Recommended Model

**Default: Sonnet.** This skill reads the activity timeline + renders a list. No drafting, no voice work. Sonnet is plenty.

---

## Skill Boundary (v2.1)

- **Use stalled-projects for:** project-focused activity-gap detection. Output is a ranked list of stalled projects with a recommended action per item.
- **Use `dormant-customer-scan` for:** people-focused cadence breaks (a customer relationship that's gone quiet). Different unit of analysis.
- **Use `list-active` for:** rendering the whole project roster without filtering by activity.
- **Use `cleanup` for:** broader workspace hygiene that includes stall as one check among many.

## Writer Contract

- **Reads from:** `_hq/data/entities.json` (projects), `_hq/data/events.jsonl` (filtered by `primary_thread_id` + activity event types from config), `_hq/data/skill_config/stalled-projects.json` (first-run questionnaire answers).
- **Writes to:** none. This skill is read-only in v3.14.1 — surfaces flags without persisting. The write side (emitting `project_stalled_flagged` events on state change) lands in v3.14.2 when Pulse + cleanup integration adds the once-weekly recording call.
- **No raw substrate writes.** When write side ships in v3.14.2, all writes go through `atomic_append_jsonl` (per Bug #81 architectural fix).

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

# Render widget with flags (or "nothing's stalled — nice work" if empty)
render_chat_output_widget(flags, ...)

if is_first_fire:
    # Auto-save defaults so subsequent fires skip this branch
    save_skill_config(workspace_root, "stalled-projects", DEFAULT_CONFIG)
    # Append one-time tune offer below the widget
    append_chat("""
First time using stalled-projects. I used the recommended defaults — 14 days for active threads,
30 for exploring, 45 for paused, 14 for blocked, 90 for dormant. Archived threads are never flagged.

Want to change what counts as stalled or which event types count as activity? Say
**"tune stalled-projects"** to walk a quick 3-question setup, or just tell me what you'd change
("flag paused threads sooner" / "I don't care about exploring threads") and I'll figure out
which setting that maps to.
""")
```

**Subsequent fires:** config exists → load it → run detection → render widget. No footer, no nag.

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

1. **Stall threshold per thread status?** (All 6 canonical statuses per `references/ORG_AND_THREAD_MODEL.md`)
   - Active thread: 14 days (recommended) / 7 / 21 / 28
   - Exploring thread: 30 days (recommended) / 21 / 45
   - Paused thread: 45 days (recommended) / 30 / 60
   - Blocked thread: 14 days (recommended) / 7 / 21 — blocked too long is itself a signal
   - Dormant thread: 90 days (recommended) / 60 / 120 — mostly for archive review
   - Archived threads are never flagged (intentionally retired)

2. **What counts as "activity"?**
   - ☑ Meetings
   - ☑ Commitments (any direction)
   - ☑ Decisions
   - ☑ Real conversations (email, chat)
   - ☐ Intel notes
   - ☐ Status changes

3. **Where should stall flags also surface?** (in addition to on-demand here)
   - ☑ Pulse Phase 9 (Tuesday + Thursday mornings)
   - ☑ Friday Wrap weekly summary
   - ☐ Morning brief if any stall detected
   - ☐ cleanup only

### Freeform tune (preferred when user gives natural-language feedback)

If the user responds to a stall list with natural-language feedback rather than firing the structured tune flow — e.g., "flag paused projects sooner" or "I don't care about exploring threads" — parse the intent and apply the implied config change directly without walking all 3 questions:

| User says | Config change |
|---|---|
| "Flag paused threads sooner" | Lower `thresholds.paused_days` (e.g., 45 → 30) |
| "Don't flag exploring threads" | Bump `thresholds.exploring_days` to a very high value (365) — effectively disables |
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
    respond("You haven't run stalled-projects yet. Say 'show me stalled projects' and I'll use the defaults — you can tune anytime after that.")
else:
    # Render current["config"] as a read-only widget with one action: "tune"
    # (which dispatches to tune mode)
```

No questions, no detection. Just shows the current answers + offers a one-click path to tune.

### Reset to defaults

```python
existed = wipe_skill_config(workspace_root, "stalled-projects")
if existed:
    respond("Settings reset to defaults. Next time you fire 'show me stalled projects', it'll use the recommended thresholds again — and offer you the tune flow on first re-fire.")
else:
    respond("No settings to reset — you're already on defaults.")
```

### Subsequent fires — detection + render

```python
import sys
sys.path.insert(0, "<plugin-root>/shared/scripts")
from stall_detector import detect_stalled_projects

flags = detect_stalled_projects(workspace_root)
```

`flags` is a list of dicts, one per stalled project:

```python
{
    "project_id": "project_NNN",
    "project_status": "active",
    "days_since_activity": 18,
    "last_event_seq": 4203,         # None if zero-history fallback
    "last_event_type": "meeting",   # None if zero-history fallback
    "baseline_source": "activity",  # or "first_seen"
    "recommended_action": "18 days since last activity — worth a touch this week."
}
```

### Rendering the surface

Render flags as a widget via `render_chat_output_widget()` per CONTRACT.md Rule 1.

Per-item action set:
- **Touch this week** — opens `email-writer` or `follow-up-ritual` for that project
- **Snooze 14d** — suppresses re-flag for 14 days (writes a `dont_forget_snooze`-style event referencing the project)
- **Move to dormant** — updates the project's status in entities.json via `workspace-manager`
- **Archive** — moves the project to archive

The CEO sees: project title + "N days quiet" + recommended action + action buttons. Scannable in under a minute even with 10 stalled projects.

### Voice tone

Friendly, not alarming. "3 projects haven't moved in 2+ weeks — worth a touch?" — NOT "STALE PROJECTS DETECTED: 3 CRITICAL." Per CONTRACT.md Rule 4 voice.

### Zero-history fallback

A project created 30 days ago with zero activity events flags at the 14-day active threshold via the `first_seen` date on its entities.json record. Recommended action reads: "Created N days ago with no activity since — decide whether to start or archive." These are often the loudest stalls (something started but never followed up).

### No-projects-yet case

If the workspace has zero projects (new install, or all archived), the skill responds: "No projects in your workspace yet. Add one and I'll watch it for stalls." NOT an error.

## Versioning

This skill ships in v3.14.1. The `stall_detector.py` helper it calls is the canonical detector. v3.14.2 adds the once-weekly recording call (`record_stall_state_changes`) wired into cleanup, plus pulse Phase 9 integration. This skill itself remains the on-demand surface across all v3.14.x releases.
