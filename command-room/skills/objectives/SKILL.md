---
name: objectives
description: "Standing objectives on the workspace itself — a small set of CEO-level priorities too big to mark done in a day, each tracked through the path the CEO picks (reviewed in a recurring meeting / self-reported in one weekly touch / read from linked work activity), status derived from the workspace's own record. Fires on: 'objectives', 'show my objectives', 'my objectives', 'objectives review', 'new objective', 'add an objective', 'objective: [statement]', 'complete objective [name]', 'archive objective [name]', 'rebind' ('rebind [objective name]'), 'objectives: [statuses]' (the weekly reply), plus 'tune objectives'. Proposes candidates from the workspace at first run. Does NOT fire on 'what should I focus on' (command-room-coach), 'stalled projects' (stalled-projects), 'pipeline'/'deals' (pipeline-tracker), 'log decision' (decision-log). No key results, no scoring — plain-English objectives. Full trigger family and fences: Routing section in the body."
---

## Entity-resolve enforcement (mandatory)

Every name-bearing trigger ("complete objective [name]", "archive objective [name]", "rebind [objective]", "new objective ... for [person]", an anchor like "the Acme work") MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` FIRST, per `shared/ENTITY_RESOLVE_PROTOCOL.md`. Objectives are threads, so they resolve like any entity. 2+ candidate objectives → disambiguation widget, NEVER first-pick. The CEO never sees or picks a raw thread id — the system proposes named targets and the CEO confirms.

## Skill Boundary

- **Use objectives for:** the standing-priority lifecycle — create with a tracking binding, the ranked readout, the weekly batched status touch, drift with one suggested move, complete/archive. Weeks-long, multi-step, CEO-level.
- **Use `pipeline-tracker` for:** individual deals. A deal can be the *tracked signal* of an activity-bound objective — the deal record stays pipeline-tracker's; objectives only read it.
- **Use `stalled-projects` for:** ordinary non-deal thread staleness. It excludes `kind="objective"` threads by fence — objective drift reports here, path-aware, with a suggested move.
- **Use `commitment` machinery for:** the individual promises that serve an objective. A "next step" on an objective is a standard commitment on its anchor/linked thread — no parallel task concept.
- **Use `command-room-coach` for:** "what should I focus on" — advisory synthesis. This skill is the record it can read, not the advice.

## Writer Contract

- **Reads:** open/closed objectives via `objective_state.list_open_objectives` / `list_closed_objectives` (defensive, wrapper-aware), everything else via `objective_math.load_objective_inputs` (objective/meeting/deal events through the canonical defensive reader, recency via `thread_activity.derive_thread_activity` — NEVER the deprecated `thread.last_activity`, open commitments via `cru_match.load_open_commitments`), `_hq/data/skill_config/objectives.json`.
- **Writes — ONLY through `shared/scripts/objective_state.py`,** the single writer of every `objective.*` field and every `objective_*` event (`objective_created` / `objective_updated` / `objective_review` / `objective_report` / `objective_completed` / `objective_archived`). It routes through `thread_writer` + `event_gate.append_event` (`atomic_append_jsonl`) internally. Hand-editing entities.json or hand-appending an objective event is forbidden — a hand-stamped status is the exact bug class the derived-status doctrine exists to kill.
- **Directional status comes from three sources only:** a stated review in the bound meeting (`record_review`, called by meeting-notes — not by this skill), the owner's own word (`record_report`), or an unambiguous linked-deal signal (computed, never written). Everything else renders as "moving" / "quiet since [date]". Never fabricate a directional status.
- **Config** via `skill_config_writer` (`save_skill_config` / `get_config` / `wipe_skill_config`).
- **Scan receipt (REQUIRED, every readout fire):** `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "objectives", fired_via="manual", surfaced=n_open, extra_data={"drifting_thread_ids": [...]})` — the drift-flag trail the monthly value receipt counts (flagged-before-stalled).
- If `load_objective_inputs` returns a non-empty `skipped` list, surface the standard banner ("I couldn't read N lines of saved history — the picture below may be missing those") — never silently swallow it.

## What It Does

Keeps the CEO's short list of standing objectives — the priorities too big for a to-do list — without adding reporting work. At creation the CEO makes exactly one deliberate choice: HOW each objective is tracked (a three-way toggle — reviewed in a recurring meeting / self-reported / read from work activity; default self-reported so it is always trackable). The system proposes the specific target inside the chosen path (which meeting, which linked work, what cadence) and the CEO confirms — the plumbing stays hidden. From then on, progress is harvested from the chosen source: meeting statuses from transcripts already being processed, activity from the linked work's own record, and self-reports batched into ONE weekly touch. Anything drifting surfaces with one suggested move — never a bare flag.

## First-Run Personalization (SPEC FRP1)

Show-then-tune (STT), all three decisions. Read config through `get_config` — never the raw file.

```python
# Rule 22 preamble first: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "drift_preset": "standard",   # standard (2 missed cycles / 21 quiet days) | relaxed (3 / 30) | aggressive (1 / 14)
    "active_cap": 7,              # soft cap on the active set — overflow proposes parking one, never blocks
    "cold_start_proposals": True, # first run proposes candidates mined from the workspace
}
cfg = get_config(workspace_root, "objectives", DEFAULTS)
```

`drift_preset` maps to `objective_math` config: standard = `drift_meeting_cycles 2 / drift_self_cycles 2 / quiet_activity_days 21`; relaxed = `3 / 3 / 30`; aggressive = `1 / 1 / 14`. `death_self_cycles` stays `drift_self_cycles + 2` in every preset (drift escalates before the graceful-death ask, never the reverse). `active_cap` is a FOCUS device, not a wall: creating one past the cap succeeds and the ack offers to park the lowest-signal one.

**Mode dispatch (4 modes):** Detect (default — any readout/creation trigger; first fire saves DEFAULTS before rendering + appends the footer) · Show settings ("show objectives settings") · Tune ("tune objectives" — pre-filled re-questionnaire OR freeform, then `save_skill_config(..., is_reconfigure=True)`) · Reset ("reset objectives to defaults" → `wipe_skill_config`).

**Freeform tune (natural language → config):** "flag drift sooner" / "be stricter" → `drift_preset = aggressive` · "give them more room" / "less naggy" → `drift_preset = relaxed` · "cap at five" → `active_cap = 5` · "skip the proposals" / "no suggestions" → `cold_start_proposals = False` · "propose objectives again" → `cold_start_proposals = True`. After applying: save + one-line confirm + re-render.

**The first-run footer (chat text under the readout, renders exactly once ever):**

> *First time showing your objectives. I set 3 defaults: **I flag an objective after two quiet review cycles (about three weeks of silence for activity-tracked ones)** · **I keep the active set to about seven so focus stays real** · **when the list is empty I propose candidates from your own meetings and work**. Say "tune objectives" to change any, or just tell me ("flag drift sooner" / "cap at five").*

## How to Use

```
"show my objectives" / "objectives"        → the ranked readout
"new objective: land three enterprise pilots by end of Q3"
"objective: get the ops handbook shipped — Bo owns it"
"objectives: 1 on track, 2 at risk — hiring is the bottleneck"   (the weekly reply)
"complete objective enterprise pilots" / "we hit the pilots objective"
"archive objective ops handbook"           → no longer an objective (with a reason, kept)
"rebind enterprise pilots"                 → re-pick the tracking path or fix a renamed meeting
```

## How It Works

### The readout ("show my objectives" / "objectives" / "objectives review")

**Hard-gated bash — all math in code (Rule 22 preamble, run from $PLUGIN_ROOT):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, datetime
sys.path.insert(0, 'shared/scripts')
import objective_math
ws = '<workspace_root>'
inputs = objective_math.load_objective_inputs(ws)
today = datetime.date.today()
health = objective_math.compute_objective_health(
    inputs['open_objectives'], objective_events=inputs['objective_events'],
    meeting_events=inputs['meeting_events'], deal_events=inputs['deal_events'],
    activity_by_thread=inputs['activity_by_thread'], threads_by_id=inputs['threads_by_id'],
    open_commitments=inputs['open_commitments'], today=today,
    primary_user_id=inputs['primary_user_id'], config=<preset map from cfg>)
due = objective_math.due_self_reports(health, inputs['open_objectives'], inputs['objective_events'], today)
print(json.dumps({'health': health, 'due': [d['thread_id'] for d in due],
                  'n_skipped': len(inputs['skipped'])}, default=str))
"
```

Render worst-first, exactly the `health` order:

1. **Count line lead** (EXEC1): *"5 in focus · 1 drifting · 2 waiting on your word."* No tile band — objectives are a short list, not a dashboard.
2. **Per objective:** `name · status in plain words (with its as-of date when stated) · how it's tracked, in plain words ("reviewed in your Monday sales sync" / "your word, weekly" / "read from the Acme pilot") · drift reason + the ONE suggested move when flagged`. A malformed row renders honestly with a one-tap repair offer — never a crash, never dropped.
3. **Widget:** `widget_mode: "all_batch_widget"` via `render_chat_output_widget`, posted via `widget_transport.render_and_persist` → `show_widget`, `source_skill: "cr-objectives"`. Per-item actions (all registered in `verb_taxonomy`, family `objective`): `report [status]` (pick on track / at risk / off track / blocked → `record_report`) · `mark complete` · `archive [reason]` (kept, optional note) · `rebind` · `snooze 14d` · `skip`. Cold-start proposal cards carry `confirm objective` + `skip`. Dispatch lives in apply-choices' `cr-objectives` source entry.
4. **Receipt** (Writer Contract above) + the skipped-lines banner when `n_skipped > 0`.

Zero open objectives + `cold_start_proposals` on → the cold start (below). Zero + proposals off: *"No standing objectives yet. Say 'new objective: [what you're driving at]' to set one."*

### Creation ("new objective ..." / "objective: [statement]" / "add an objective")

1. **Statement + short name** — the statement is the CEO's own words, kept verbatim on the objective object. ALSO derive a short name (2–4 words — "Enterprise pilots" from "Land three enterprise pilots by end of Q3") and pass it as the thread's `name`: the short name is what entity-resolve matches when the CEO later says "complete objective enterprise pilots" — a full-sentence canonical name structurally cannot fuzzy-match the short fragments people actually type. The ack uses the short name, which confirms it implicitly; "call it [x]" corrects it. Parse a horizon if stated ("by end of Q3" → date); never invent one.
2. **The one deliberate choice — the tracking path.** Ask as a plain three-way toggle, defaulting to self-reported: *"How should I track it? (a) it comes up in one of your standing meetings — I'll listen there · (b) just ask me — 20 seconds in the Friday wrap · (c) it shows up in the work itself — I'll watch the linked threads. Default is (b) if you just say go."*
3. **Propose the target inside the path (the plumbing stays hidden):**
   - **meeting** — mine `meeting`/`meeting_processed` events for recurring series (normalized title, ≥3 instances in 60 days), rank by attendee/topic fit, propose ONE: *"Your Monday Sales Sync — that one?"* series_match proposal rule: title unique in the meeting history → `title_only` (distinctive names like a numbered leadership call survive attendee churn); generic title → `title_and_people` with the usual attendees.
   - **activity** — `resolve_all` the statement's names/topics; propose the matching thread(s)/deal(s) by name: *"I'd read this from the Acme pilot deal — right?"* Topic over party: NEVER offer a bare person as the tracked signal.
   - **self** — cadence defaults weekly (the Friday touch); a stated cadence ("monthly is fine") is honored.
4. **Owner** — defaults to the CEO. When the statement names a person, `resolve_all` them; a known direct report (team-intelligence roster) is proposed as owner: *"Bo owns this one?"*
5. **Anchor** — when the objective is clearly about existing work, propose the anchor thread by name (the relevance-capture join). The existing thread is linked, never converted.
6. **Hard gate — run the block, never hand-write entities.json:**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
import objective_state
t = objective_state.create_objective('<workspace_root>',
    statement='<their words>', name='<the short name>',
    binding=<confirmed binding dict>,
    owner_person_id=<resolved id or None>, horizon=<ISO date or None>,
    anchor_thread_id=<resolved id or None>, source_skill='objectives')
print('OBJECTIVE_CREATED ' + t['id'])
"
```

7. **Ack in plain English:** *"✓ On the board: Land three enterprise pilots — tracked through your Monday Sales Sync, yours, aiming for end of Q3."* Past the `active_cap`: *"That's 8 in focus — more than the seven that keeps focus honest. Want me to park the quietest one (Ops handbook, silent 5 weeks)?"* — parking = archive with reason "parked", one tap, fully reversible by recreating.

### The weekly touch (rides the Friday Wrap — never its own ping)

All self-report asks AND pending relevance proposals batch into ONE section of weekly-recap's chat surface (weekly-recap owns the render; this skill owns the reply). The section lists due objectives numbered, and its render logs the numbering: an `objectives` receipt whose `data.due_thread_ids` carries the thread ids in the exact render order (weekly-recap §8c's ordinal contract). **The reply — "objectives: 1 on track, 2 at risk — hiring is the bottleneck" — fires THIS skill:** parse per-objective statuses + trailing note, map ordinals against the MOST RECENT objectives receipt's `due_thread_ids` (read via the receipts reader — NEVER a fresh `due_self_reports` recompute, which could silently re-order and land a status on the wrong objective), then one `record_report` per objective (hard-gated, per the Writer Contract). If no such receipt exists, it is older than 8 days, or an ordinal is out of range: ask ONE clarifying question listing the due objectives by name — never guess the mapping. Ack as one line, never re-render the full readout. A directional word is required per item — "1 fine" maps to on_track, but an unparseable item gets ONE clarifying question, never a guessed status.

**Graceful death:** a self-bound objective past `death_self_cycles` missed check-ins is NOT asked for status again — the touch asks instead: *"Still an objective, or has it run its course? (keep / archive)"*. Archive keeps the record and the reason. No stale morning-brief line fires forever — drift lines stop once the death ask is pending.

### The meeting harvest (meeting-notes' job — pointer, not a second pipeline)

Meeting-notes Step 5i (its file owns the mechanics) checks each processed transcript against open meeting-bound objectives via `objective_math.matches_series`; on a forum match where the objective was actually discussed with a stated status, it calls `objective_state.record_review` (idempotent per meeting). This skill NEVER reads transcripts itself — no parallel scanner, ever. A forum meeting where the objective wasn't discussed writes nothing; that absence is what the drift math counts, and the drift line's suggested move offers the rebind path when the real cause is a renamed/moved meeting ("did this meeting move? say `rebind [objective]`").

### Complete / archive ("complete objective [name]" / "we hit [objective]" / "archive objective [name]" / "drop the objective")

Resolve the objective (2+ → disambiguate; **0 candidates → the protocol's flagged fallback:** a case-insensitive contains-match over open objectives' short names AND statements, surfaced as a flagged match per `ENTITY_RESOLVE_PROTOCOL` — and if that too finds nothing, say so and list the open objectives by name; never silently dead-end, never guess). Then `objective_state.complete_objective` / `archive_objective` — the single closure paths. Idempotent: `already_closed` is acked honestly ("that one was already closed — nothing changed"), never re-appended. Completion asks nothing extra; an archive without a stated reason gets one soft ask ("worth a line on why, for the record? fine to skip"). The thread flips resolved/archived in the same call — it leaves every active surface at once.

### Cold start (first readout, zero objectives)

Never a blank page. Mine three sources, all already on disk — no connector calls: (a) recurring meeting series (≥4 instances in 30 days) → meeting-path candidates named after the series' dominant topic; (b) the most-active threads/deals by `derive_thread_activity` → activity-path candidates anchored to them; (c) `_hq/views/THEMES.md` recurring themes when present → self-path candidates. Propose AT MOST 3, each a full pre-filled card (statement drafted from the evidence, path pre-selected, owner proposed) rendered through the standard widget for one-tap `confirm objective` / `skip` — the SAME two verbs the readout's proposal cards carry (above) and the only two the apply-choices `cr-objectives` dispatch routes for a cold-start card. A card's typed `input` replaces the drafted statement; there is no separate edit verb. Confirm → the same `create_objective` gate. Skipped proposals are not re-proposed for 60 days (receipt trail).

### Relevance capture (the classification envelope — nothing new scans)

An objective is a thread, so the existing capture paths already attribute to it: meeting-notes/passive classification can land the objective in `related_thread_ids` on TOPICAL evidence only — an explicit mention (alias/statement match) or the signal already belonging to a linked/anchor thread. People/org clustering scores below the auto band by design — party overlap alone NEVER auto-attaches; it can at most support a proposal in the weekly touch. Attributed signal enriches context and counts as movement; it NEVER moves a directional status (that bar belongs to the bound source alone). Every attachment carries the envelope's provenance (`cross_ref_reason`, confidence) and reverses through the standard `reclassification` path — a dismissal teaches suppression like every other capture loop.

## Executive Output Standard (EXEC1)

Inherits `shared/EXECUTIVE_OUTPUT_STANDARD.md`. The chat lead is the count line, not a theme: *"5 in focus · 1 drifting · 2 waiting on your word."* No docx path in v1 — the readout is a chat surface (weekly-recap's docx carries the objectives section there).

**Output guard:** no internal tokens, paths, event names, entity ids, or version numbers in anything the CEO sees (`shared/VOICE_CALIBRATION.md` § Plain-language glossary).
- Bad: "objective_review appended for project_021 (confidence 0.85)."
- Good: "✓ Heard it in Monday's sales sync — pilots are at risk; two stuck at security review."

## Gotchas

- **The CEO picks the path; the system picks the target.** Never ask the CEO to choose a thread id, a series key, or a cadence field — propose the named thing, confirm in plain words.
- **Status honesty follows the binding.** A mention in some other meeting, a related email, party overlap — context and movement at most. Directional words come only from the bound source or the owner's mouth.
- **Stale truth decays honestly.** A three-week-old "on track" renders with its date and a stale flag — never silently repeated as current.
- **A renamed meeting looks like drift.** The drift move offers `rebind` — one tap, not a silent stall.
- **Objectives never nag item-by-item.** One weekly batched touch; the morning brief only surfaces (read-only, FB-20) — it never asks.
- **The cap is soft.** Creation past it succeeds; the ack proposes parking one. Focus is a suggestion, not a wall.
- **Milestones are context, not chores.** No per-step prompts, no percent-complete math.

## What It Doesn't Do

- Doesn't scan anything itself — meeting-notes harvests reviews; the classification envelope attributes signal; this skill only reads.
- Doesn't move deals, close commitments, or edit any linked work — it reads their record; their own skills own them.
- Doesn't schedule anything — the weekly touch rides the existing Friday Wrap; no new scheduled task, no per-objective pings.
- Doesn't compute OKR-style scores, percentages, or key results — plain-English statements with honest, source-derived status.
- Doesn't auto-create objectives — cold-start candidates and any detected links are propose-and-confirm, always.
- Doesn't fabricate a directional status from thin signal — "moving" and "quiet since [date]" are complete, honest answers.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill. The routing metadata is budget-capped by the platform (G11); routing correctness is enforced mechanically by tests/triggers.yaml. Everything below remains binding at fire time.

> Standing objectives built on the workspace itself — a small set of long-horizon priorities, each tracked through the CEO's chosen path (meeting-reviewed / self-reported / activity-tracked), with derived status, batched weekly asks, and drift that always carries a suggested move. Use when the CEO says 'objectives', 'show my objectives', 'my objectives', 'objectives review', 'what are my objectives', 'new objective', 'add an objective', 'objective: [statement]', 'set an objective', 'complete objective', 'we hit [objective]', 'archive objective', 'drop the objective', 'rebind' (always followed by an objective name — a coined verb this skill owns), 'objectives: [statuses]' (the weekly-touch reply prefix). Also handles first-run personalization — 'tune objectives', 'show objectives settings', 'reset objectives to defaults'.

> Fences (one per line — each stays with its owner):
> DOES NOT fire on 'what should I focus on' / 'what should I focus on this week' (command-room-coach — advisory synthesis; it may read this skill's record).
> DOES NOT fire on 'stalled projects' (stalled-projects — non-objective threads; it excludes objective threads by fence).
> DOES NOT fire on 'pipeline' / 'deals' / 'show my deals' (pipeline-tracker — a deal can be a tracked signal here, but the deal record is theirs).
> DOES NOT fire on 'log decision' / 'we decided' (decision-log).
> DOES NOT fire on 'show my list' / 'what's on my list' (show-my-list — commitments, not objectives).
> DOES NOT fire on 'weekly recap' / 'what happened this week' (weekly-recap — it renders the objectives section; this skill only handles the reply).
> Never claims these bare tokens (deliberately NOT quoted triggers; each stays with its owner or unowned): goal, goals, priority, priorities, focus, milestone, OKR, key results.

> Loose-status handoff: *where are we with [objective name]* stays workspace-manager's catch-all; its ladder hands the turn here when ENTITY_RESOLVE lands on an objective thread. `go [objective name]` navigation stays workspace-manager's.
