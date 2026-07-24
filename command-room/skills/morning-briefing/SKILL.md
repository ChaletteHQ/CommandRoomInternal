---
name: morning-briefing
description: "Proactive daily digest — calendar, important email summary, overdue follow-ups, urgent items. Triggers: 'morning briefing', 'daily briefing', 'brief me' (bare, or 'brief me on today'), 'what do I need to know today', 'start my day'. Plus 'tune morning-briefing' and 'customize morning-briefing'. DOES NOT fire on 'triage my inbox', 'process my inbox', 'what's in my inbox' — those go to inbox-triage for a deep classification + drafts pass. DOES NOT fire on 'brief me on the' / 'brief me about' (a topic brief — any subject that isn't today — workspace-manager loads the project and answers; one-pager-composer writes it up)."
---

## Deterministic state computer (mandatory, v3.14.8+)

> **The commitment header counts (`counts["headline"]`: you owe / owed to you / unowned / unconfirmed, plus overdue — v4.5.2 R4, the one bucket export) and the "ball is on you" Needs-Attention list MUST come from `shared/scripts/commitment_state.py::compute_brief_state(...)` (promoted from brief_state.py in Phase 2 Stage A; `brief_state` remains a working import alias) — NOT from re-deriving the open/overdue/whose-turn/drop rules in prose. Its `counts` block delegates to `commitment_state.count_commitments` — the ONE counting API every surface shares. This is the single source of truth for that computation; Steps 3b/3c/3c-bis below document the rules the function already implements and tell you how to gather its inputs. Your job at runtime is to FETCH the inputs (open commitments, per-thread latest-sender, calendar events, per-thread last-activity) and pass them in, then RENDER what comes back. Do not recompute the drops yourself — the function decides, and it is unit-tested (`tests/run_brief_state_test.py`).**

The drop logic drifts when re-derived in prose (the v3.14.7 calendar-close bug was one instance — see references/HISTORY.md). `compute_brief_state` collapses it into one tested function so the same inputs always produce the same surfaced list. See Step 3d for the call.

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

For the open-commitment / overdue-follow-up sections, you MUST call `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)` — do NOT hand-roll an events.jsonl scan. The canonical helper handles closure-suppression (v3.11.4 `data.target_id` defensive id-field), malformed-line tolerance (Sub-bug #14b 2-layer defense), and dual-shape confidence values. If `load_events_defensively()` reports `skipped` lines (substrate corruption), surface a soft banner to the user: "A few entries in your activity log look incomplete — I'll tidy those up during this weekend's cleanup." Do NOT silently filter.

For any name-bearing follow-up ("brief me on Sam's status"), call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` before grep. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use morning-briefing for:** the 60-second daily scan. Calendar + 1-line email summaries + tracker urgency + Slack digest. Designed for scheduled fire at 7:30am weekdays.
- **Use `inbox-triage` for:** the deep email pass — 5-bucket classification (Reply Now / Decision Needed / FYI / Discard / Deep Read) with 2-3 drafted replies. Runs on demand or in sequence after morning-briefing.
- **Pair pattern:** morning-briefing runs first (context), inbox-triage runs second (email action). User can trigger both with "brief me + triage my inbox" or schedule them in sequence.

The email section of morning-briefing is intentionally summary-only — if the user wants drafts, they call inbox-triage.

## Personification Contract (v3.13.8.4+)

Before rendering the briefing, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The briefing chat intro line uses the shape `"Morning, {first_name} — {brain_name} here with today's read."` (default `{brain_name}` = `"Penelope"`); the scheduled-task .docx signature line is `"— {brain_name}"` (already implemented in v3.13.8 scheduled-task orchestrators). Don't over-name — one reference in the intro + one in the signature is the rhythm.

## Writer Contract

This skill reads from the declared mail, calendar, and chat connectors during its daily scan. Every connector read **from an in-scope account** emits corresponding events to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` (v3) — dedup via source_ref hash so running this daily doesn't duplicate events already captured by inbox-triage or workspace-manager. The primary briefing output is never blocked by a capture failure; capture is a side effect.

**Connector-agnostic + account-scope (connector-agnostic-v1).** Resolve mail/calendar tools through the seam (`tool_discovery.discover_for_category` with the declared backend; substring `discover_*` fallback = today's behavior, R4). Never name a provider tool, query operator, field, or URL host — those live in `connector_adapters/`. **The brief is a single-user ephemeral surface (R9):** it MAY show `surface: on, write_to_business: off` personal items (spouse/doctor/school) so the owner sees them — but the brief run writes NOTHING to the substrate for those items (the writer wall enforces this structurally). The forwardable brief **.docx** is an exportable artifact and draws ONLY from `write_to_business`-scoped substrate — personal items never reach it (`shared/ACCOUNT_SCOPE.md` §3).

This skill also reads `_hq/custom/morning-briefing.md` — SCL1 standing customization preferences, via `skill_custom_writer.load_directives` (absent → defaults). See the Customization (SCL1) section below.

---

## Customization (SCL1)

**Customization layer (SCL1):** before producing output, read
`[WORKSPACE_ROOT]/_hq/custom/morning-briefing.md` if it exists and apply its directives to
this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
with defaults. Directives refine WHAT the output contains and HOW it is shaped;
they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md` #limits).
Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root, "morning-briefing")`
— never the raw file; it returns `[]` on a missing or malformed file and never raises.
Directives here shape the brief's arrangement and inclusion rules (e.g. "group by entity",
"lead with anything involving [named org]", "weekends: skip unless something is on fire") —
applied to what the deterministic state computer returns, never to the counts it computes.
Trigger family (owned in the frontmatter `description`): `customize morning-briefing` · `show
morning-briefing customizations` · `reset morning-briefing customizations`. Distinct from the
FRP1 knob family (`tune` / `show settings` / `reset to defaults`). See
`shared/SKILL_CUSTOMIZATION.md` for the writer API, the write-time rejection list, and the
precedence chain. Customer-facing acks are plain English ("Got it — I'll group your brief by
company from here on."); never surface the file, the word "directive", or "SCL1".

---

# Morning Briefing — Proactive Daily Digest

Deliver a concise, actionable morning digest before the user starts their day. This skill is designed to run as a **scheduled task** (fires automatically on weekday mornings) but also works as a manual trigger.

The goal: the user reads this in 60 seconds and knows exactly what needs their attention today. No fluff, no comprehensive status — just what changed overnight and what's due.

## When This Runs

| Mode | Trigger | Output |
|------|---------|--------|
| **Scheduled** | Fires via the `morning-brief` scheduled task (weekdays, per schedule config) | The orchestrator posts the digest as a markdown chat post in the Morning Brief chat |
| **Manual** | "morning briefing", "daily briefing", "brief me" | Same digest, displayed in chat |

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`).
All three decisions are **show-then-tune (STT)** — the brief always renders first, then offers
one-tap changes. Nothing here blocks the digest (CONTRACT Rule 17). Read config through
`get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "depth": "headline",        # headline (headline-first + Top 3) | full
    "leads_with": "synthesis",  # synthesis | calendar | commitments
    "going_quiet": {"enabled": True},  # the "Going quiet" section on/off
}
cfg = get_config(workspace_root, "morning-briefing", DEFAULTS)
```

`depth` selects Step 4 layout (`headline` = synthesis lead + Top 3 + scannable; `full` = every
section expanded). `leads_with` sets what the digest opens with. `going_quiet.enabled=False`
suppresses the "Going quiet" section. These read via `get_config`; the legacy CLAUDE.md
"Briefing Delivery" prose remains a fallback ONLY for the delivery-channel knob (Slack/email/file)
which is not a first-run decision.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "morning briefing", scheduled fire | render the digest with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "morning-briefing", DEFAULTS)` BEFORE rendering, then append the first-run block AFTER the digest. |
| **Show settings** | "show morning-briefing settings" | render current config in plain English; no digest. |
| **Tune** | "tune morning-briefing" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-render the digest with new settings. |
| **Reset** | "reset morning-briefing to defaults" | `wipe_skill_config(workspace_root, "morning-briefing")` → next fire is a first-fire again. |

**The first-run block (transport split):**

- **On-demand chat fire (this skill, manual mode):** a 2–3 line FOOTER after the digest (chat
  output, no widget — so MUST-NOT rule 5 does not apply):
  > *First time briefing you. I made 3 calls: **headline-first depth** · **leads with synthesis** ·
  > **Going-quiet section on**. Say "tune morning-briefing" to change any, or just tell me
  > ("lead with my calendar" / "go full detail").*
- **Scheduled fire (`orchestrator-morning-brief.md`):** the same three decisions ride as
  `fr1`/`fr2`/`fr3` items in a "Make this yours" section at the BOTTOM of the all-batch widget
  (the documented fr-item preselect exception — see `shared/CHAT_ACTION_WIDGET.md`). Tap →
  apply-choices `{n:"fr1", ...}` → `save_skill_config(..., is_reconfigure=True, origin="first_fire_override")`.

The block renders exactly once ever (`is_configured` gate), on whichever surface fires first.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "go full detail" / "show me everything" | `depth = full` |
| "keep it short" / "headline only" | `depth = headline` |
| "lead with my calendar" | `leads_with = calendar` |
| "lead with what I owe" / "open with commitments" | `leads_with = commitments` |
| "lead with the synthesis" / "open with the theme" | `leads_with = synthesis` |
| "turn off going-quiet" / "stop the going-quiet section" | `going_quiet.enabled = False` |
| "show me going-quiet again" | `going_quiet.enabled = True` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-render the digest + confirm in one line. Day/time of the scheduled fire is NOT a morning-briefing setting — that's `change-schedule` (the morning-brief task), not tune.

## Workspace Structure Reference

- Tracker: `[WORKSPACE_ROOT]/_hq/MASTER_TRACKER.md`
- Business context: `[WORKSPACE_ROOT]/_hq/BUSINESS_CONTEXT.md`
- People: `[WORKSPACE_ROOT]/_hq/PEOPLE.md`
- Team: `[WORKSPACE_ROOT]/_people/` (if exists)
- Projects: `[WORKSPACE_ROOT]/[Project Name]/SESSION_NOTES_[NAME].md`

## Step 1: Load Core Context (Fast)

Read only what's needed — this must be lightweight:
1. Read `_hq/MASTER_TRACKER.md` — project list, commitments, next actions, waiting-on
2. Read `CLAUDE.md` if it exists (hot cache for people, projects, terms)
3. Do NOT read per-project session notes or brains — this is a scan, not a deep dive

## Step 2: Scan Connected Sources

Check each available connector. Skip gracefully if not connected — never error.

### Calendar (if connected)
- Pull today's events + tomorrow's first event (this is the **display** fetch for the "Today's calendar" section — narrow on purpose).
- **Do not reuse this narrow pull for the scheduling-verification gate.** Step 3c-bis needs a *much wider* window (~7 days back through ~30 days forward) to see that a "book/lock/propose time" item is already on the calendar days out. Reusing this today/tomorrow pull there starves the gate — a meeting four days from now looks unbooked and the brief tells the CEO to redo it (Bug #93). Step 3c-bis issues its own wide `list_events`.
- For each event: title, time, attendees, project association (match against tracker)
- Flag: meetings with no prep brief, back-to-back blocks, meetings with people who have overdue commitments. The "overdue commitments by attendee" check derives from `_hq/data/events.jsonl` via `load_open_commitments` filtered by `owner_id in <attendee_person_ids>` (per `references/SOURCE_OF_TRUTH.md` — never from PERSON.md commitment tables, which can lag).
- **The "no prep" flag reads receipts, ONLY receipts (v4.5.2 S1 — F-29):** for each of today's meetings, `from receipts import prep_exists_for_meeting; prep_exists_for_meeting(workspace_root, <calendar event id>)`. The `⚠️ no prep` flag may render ONLY when that returns False. NEVER answer "was this meeting prepped?" from folder globs, filename/slug guesses, or memory — that detector/writer mismatch is how the brief claimed "no prep brief" for a 9:15 call while the prep file AND its fire receipt were both on disk (reproduced 2-for-2 days in the v4.5.1 dogfood). Both prep paths now write a per-brief `prep_brief` receipt via `receipts.log_prep_receipt`; the receipt is the contract. A meeting the calendar gives no id for (rare) gets NO flag rather than a guessed one.
- **Build the `todays_meetings` input for Step 3d (v4.5.2 C1 — REQUIRED when the calendar is connected):** for each of today's events, resolve attendee emails to person_ids via `entities.json`/`aliases.json` and collect attendee display names PLUS their alias spellings from `aliases.json` — `{"meeting_id": <event id>, "title": <event title>, "attendee_person_ids": [...], "attendee_names": [...]}`. Step 3d passes this to `compute_and_log_brief_state`, which matches open commitments to today's meetings by counterparty OR name-mention in the item's own text (`commitment_state.match_commitments_to_meetings`). **A missing due date must not make a meeting-relevant item invisible** — the F-44 failure was sweep-recovered items about that morning's 9:15 appearing nowhere in the brief because every ranking bucket keyed on `due`.

### Email (if connected)
- Search for unread or important emails from the last 18 hours
- Filter by: people in PEOPLE.md, project-related subjects, flagged/starred
- Limit to 10 most relevant — summarize each in one line
- Flag: anything that looks like a reply to a "Waiting On" item in the tracker

**Self-reply filter (v3.11.1 — REQUIRED).** The default **in-inbox** query hides messages the user already sent in reply — meaning a thread where M responded an hour ago still shows up as "unread or important from last 18h" and surfaces under Needs Attention as if it's still waiting. For every candidate thread:

1. Fetch the thread's latest message via the resolved thread-fetch tool (`discover_mail_thread_fetch_tool` / the seam) or equivalent.
2. Compare the latest message's `From:` header to the primary user's email (read from `entities.json` — the `person` record where `is_primary_user: true`).
3. If the latest message in the thread is FROM the primary user, **drop the thread entirely** from Needs Attention and Overnight Inbox. M already handled it; surfacing it as outstanding is wrong.
4. The check must run on the thread's LATEST message, not the message that matched the original query (the mail search may have surfaced an earlier inbound message in a thread the user has since replied to).

If the connector supports it, broaden the initial query with the **inbox-or-sent, not-draft** intent — a DISJUNCTION, expressed as `{"any_of": [{"in_inbox": true}, {"in_sent": true}], "not_draft": true}` and compiled per provider by `connector_adapters/mail.py` `compile_search` (never a hardcoded operator string; on Gmail this reproduces the original parenthesized OR-group query byte-for-byte) — and then run step 1-3. This is cheaper than a per-candidate thread fetch but produces the same outcome — threads where M is the latest sender get dropped. Pick whichever path the connected mail tool supports; both are acceptable as long as the latest-sender check is applied.

### Slack (if connected)
- Check for unread DMs and mentions from the last 18 hours
- Check project-related channels for activity
- Limit to 5 most relevant — summarize each in one line

## Step 3: Check Tracker for Urgency

Scan MASTER_TRACKER.md for:
- **Overdue commitments:** Any commitment past its due date → flag with days overdue
- **Stale waiting-on items:** Anything in "Waiting On" with no activity in 7+ days → flag for follow-up
- **Today's deadlines:** Any commitment due today → highlight prominently
- **Urgent flags:** Any project with "urgent" or "critical" in its next action or notes

If `_people/` exists (v3.11.5+ — REQUIRED canonical-source derivation per `references/SOURCE_OF_TRUTH.md`):

The team overdue / dormancy counts MUST derive from `_hq/data/events.jsonl` via `load_open_commitments`, NOT from each PERSON.md file's commitment table. The PERSON.md commitment table is a Tier 2 projection that lags — reading it directly was the v3.11.5 _people/ drift bug (see references/HISTORY.md).

Procedure:

1. Load the team roster from `_people/_team-config.md` → list of person ids (resolve names via `aliases.json` if the roster uses display names).
2. Call `load_open_commitments(events.jsonl)` once. Group results by `owner_id`.
3. **Overdue check** — for each team member, count commitments where `_commitment_field(ev, "due")` parses to a past date in workspace TZ (via `tz.py to_local(due, workspace_path=<WORKSPACE>)`). Flag anyone with 3+ overdue.
4. **Dormancy check** — for each team member, find max ts of any `interaction` / `meeting` / `commitment` event in events.jsonl where `event_references_person(ev, person_id)` is true (per `cru_match.event_references_person`, which handles all shape variants). If max ts is >14 days ago, note as dormant.

PERSON.md files are still fine to read for static profile context (role, working style, flags) — just not for the overdue / dormancy counts that drive the surfaced flag list.

### Step 3a: Layer events.jsonl on top of the tracker (v3.11.1 — REQUIRED)

MASTER_TRACKER.md is a **periodic snapshot**, not a live view. It's regenerated when entities or events change, but a workspace that hasn't triggered a regen for 10 days will surface stale "Last touched" / "quiet since" values for projects that had activity today (the 2026-05-20 overlay incident — see references/HISTORY.md § Overlay bug class).

**Required overlay procedure — apply before rendering ANY "Last touched" / "Waiting On" / "Next Action" value:**

1. Read the tracker's stamp. MASTER_TRACKER.md is generated with `<!-- generated-at: YYYY-MM-DD HH:MM -->` near the top (per `references/VIEW_GENERATION.md`). Parse it. If both the comment-style stamp and a body line like `> Last updated: …` are present, the comment-style stamp wins.
2. If the stamp is **older than 24 hours**, the tracker is stale-by-default — proceed to step 3 for every thread the digest will surface. (If the stamp is within 24h, the tracker is current enough; you may still overlay if helpful but it's not required.)
3. For every thread you're about to render under a primary-focus org section or call out under Needs Attention, scan `_hq/data/events.jsonl` for events where `primary_thread_id == thread.id` AND `ts > tracker_stamp` AND `classification_confidence >= 0.40` (matches the `computed_last_activity` rule in VIEW_GENERATION.md). Use `shared/scripts/atomic_write.py` read helpers if you need a streaming scan; for ≤5000 events a single read pass is fine.
4. If newer events exist, override:
   - **Last touched** → max(ts) of the newer events, rendered with `to_local(ts, workspace_path=<WORKSPACE>)` per B1.
   - **Next Action** → if any newer event has `data.next_step` populated, use the most recent one. Otherwise keep the tracker's Next Action.
   - **Waiting On** → if any newer `commitment_resolved` / `thread_resolved` event closes the item the tracker listed as Waiting On, clear it. If a newer `commitment` event opens a new wait, surface that instead.
5. The overlay is read-only. **Do not** regenerate MASTER_TRACKER.md from morning-brief — that's workspace-manager's job. Just render with the freshened values.

If the tracker stamp can't be parsed, treat the tracker as stale and apply the overlay to every thread. Better to over-overlay than to ship a digest that says "quiet since April 25" about work that happened today.

(The original acceptance criteria for this overlay are recorded in references/HISTORY.md § Overlay bug class.)

### Step 3a-bis: Read what the reconcile-sent task closed — the brief is a READER, not the reconciler (v3.18.12 — Bug #98-v3)

**The brief no longer fetches sent mail or runs reconciliation.** A dedicated silent pass — the reconcile-sent job, FIRST in the `maintenance` task's 6:45 AM fire, before this brief (MAINT1) — does the actual sent-mail fetch, closes commitments the CEO completed by emailing someone directly, advances the cursor, and emits a `sent_reconcile` audit event. The brief just **reads** what that pass already wrote.

**Why it moved (Bug #98-v3):** co-locating an invisible substrate write with a visible deliverable loses every time — three in-brief attempts were all skipped in real use (full post-mortem in references/HISTORY.md § Bug #98). Reconciliation now lives in its own single-purpose task where it IS the job. The brief's role here is two things only:

1. **Surface what was already closed (read, don't do) — through the change feed (LB1).** The read-back now rides ONE narration slot, not two: `change_feed.changes_since(<last brief ts>)` (`shared/scripts/change_feed.py`; the t3 FB-9 driver pack delivers these as `changed.lines` — consume the pack, don't re-derive) aggregates the `sent_reconcile` closures alongside everything else the system did (sweep recoveries, resolved/expired proposals, undos), each line traceable to its audit event. Its closed-from-sent line carries the undo affordance verbatim (*"Closed N commitments matched to your sent mail — say `undo` to reopen any."*). These lines feed the CHANGED contract line in Step 4 — do not ALSO render a separate reconcile tail line (one slot, not two). Do NOT fetch sent mail or run the reconciliation matcher yourself — that's the reconcile-sent task's job; you are reporting its result, not producing it. Enforcement stays on the audit events (the feed is a READER — it can't fake "closed N" any more than this brief could).

2. **The deterministic soften floor (your job, and it's reliable because it's cheap + computed).** `compute_brief_state` (Step 3d) takes `sent_reconcile_cursor` and returns `reconcile_stale` (True when the cursor is absent or >1 day old) plus a per-item `reconcile_stale` flag. **When `reconcile_stale` is True, you MUST soften every you-owe / "ball is on you" item** — render it as *"you may have already handled this — I haven't been able to check your sent mail since [cursor date]"* rather than "reply to / send / follow up with". In normal operation the 6:45 task advances the cursor before you run, so `reconcile_stale` is False and nothing softens. If that task didn't fire (cursor stale), the floor catches it and you still never send the CEO to redo done work. This is the protection that held across all three earlier failures.

   **Plus one explicit staleness line (so the signal is never silent).** When `reconcile_stale` is True, append exactly one line under the commitments list: *"(I haven't been able to check your sent mail in the last day — some of these may already be handled.)"* This is the surface the `reconcile-sent` skill's reliability note points at ("the brief's `reconcile_stale` soften covers the gap"). It is informational — **never block or delay the brief on it**, and never fetch sent mail yourself to "fix" it (that is the reconcile-sent task's job, per Step 3a-bis).

**Why this finally works.** Enforcement is on the EVENT, not a narration: the reconcile-sent task's success is a `sent_reconcile` audit event a validator reads back from `events.jsonl` — a cursor delta backed by a scan count can't be faked the way a sentence can (the gamed v3.18.9 receipt gate is in references/HISTORY.md § Bug #98). The brief can't fake "closed N" either — it reads the real `commitment_resolved` events or it has nothing to report.

**Substrate alarms (FS-04/05/06/15 — MANDATORY, LOUD, render at the very top of the brief).** The one-command brief driver supplies these — `surface_drivers.build_morning_brief_pack` (CLI: `python3 shared/scripts/surface_drivers.py morning-brief --workspace <WS> --mode <scheduled|manual>`) returns `alarm_lines` from `substrate_health.substrate_alarm_lines(WORKSPACE_ROOT)`; run the driver ONCE per fire (t3 FB-9) and place its blocks rather than re-deriving them piecemeal. Any returned line renders verbatim as a pinned alert at the TOP of the brief, above the synthesis lead — these are the log-clobber, unreadable-records, read-time corruption (a file that failed to read during an earlier fire, even if it reads fine now — the sync-cache window), and duplicate-entry alarms. They are the surface FS-04/FS-15 exist for: a silently-degraded substrate must never let the brief report confident-but-wrong counts. Empty list → render nothing. Never suppress an alarm because it's "not today's news."

### Step 3b: Aggregate commitments from events.jsonl (v2.7.15+, v3.4.5+ shape-aware)

Scan `_hq/data/events.jsonl` for `type: commitment` events that haven't been closed by a later `commitment_resolved` / `thread_resolved` event.

**Mark-done affordance (v3.18.3+ — Bug #85; receiving route registered P0.7 2026-07).** Every item surfaced under "Needs Attention" carries a one-tap **`mark done [n]`** action. The route: the fire records `data.needs_attention_ids` on its `pack_run` event — the commitment id (`data.id` verbatim) for each numbered Needs Attention item, in render order — and apply-choices Step 2's `morning-brief` source entry resolves `[n]` against that list and closes through `commitment_state.close_commitment` (the canonical closure path — never a hand-built `commitment_resolved` append). This is the manual close path the 7-day stopgap below was waiting on — the CEO closes a stale "you owe" item in one tap instead of seeing it re-surface daily. Pair it with the auto-close from Step 3a-bis: the system closes what it can prove from Sent mail, and `mark done` covers the rest.

**Prospect-conversion nudge (v3.18.7+ — Bug #92, detect-and-nudge — CONDITIONAL, cheap).** Call `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)` (a fast substrate-only read — no connector fetch, unlike Step 3a-bis). This is the same detector the coach and weekly cleanup use; surfacing it in the daily brief is the highest-visibility nudge. **NEVER auto-flip `relationship_type`** — only surface the suggestion; the CEO runs the Bug #91 `[Name] is now a client` conversion. If the detector returns no candidates, emit nothing (this is a conditional line, NOT a mandatory one — contrast Step 3a-bis's required status line).

**Render EVERY candidate verbatim — do NOT second-guess the detector (v3.18.9+ — Bug #92b).** Each candidate carries a ready-to-render `render_line`. Add that line, exactly as returned, to Needs attention for **every** candidate the detector returns. You MUST NOT apply your own judgment about whether a prospect's project "looks paused", "isn't really active", or "isn't worth surfacing" — the detector already decided who qualifies (it owns the active/archived/paused call), and a surface that drops a candidate on its own discretion is the #92b regression (see references/HISTORY.md). If the detector returns 3 candidates, exactly 3 `🔄` lines appear. The detector is the source of truth for inclusion; your only job is to render its lines.

**Deal-rot line (SPEC PIPE1, D8 — CONDITIONAL, at most ONE line, same slot pattern as the prospect nudge).** A fast substrate-only read: `deal_state.list_open_deals` → `deal_health.compute_deal_health` (recency via `derive_thread_activity(ws, honor_reclassifications=True)` — RECL1, the SAME call shape pipeline-tracker uses so the two surfaces quote one day-count (F-54); next-step via the already-loaded open-commitment set — reuse this fire's, no second scan). Emit AT MOST ONE line, only when at least one open deal is `rotting` or `close_date_passed`: the single worst offender by `pipeline_math.rank_score`, in plain words with the teach-the-phrase close — *"💼 [Deal] ([Org], $40K) has been quiet 12 days in negotiating — say `show my pipeline` to act."* No stats block, no tile band, no second deal (the brief gets the top move; the pipeline report gets the dashboard). Zero flagged deals → emit nothing. Untracked deal threads never surface here.

**Objectives lines (SPEC OBJ1, DRAFT — CONDITIONAL, at most TWO lines, same slot pattern).** A fast substrate-only read: `objective_math.load_objective_inputs` → `compute_objective_health` → `brief_lines(health, max_lines=2, names_by_person_id=<people map>)` — the helper owns line selection and phrasing (line 1: the single worst drifting/at-risk objective WITH its suggested move and the `show my objectives` teach-phrase; line 2: the focus headline). Render its returned lines verbatim, exactly as the prospect nudge renders `render_line` (the helper decided; the surface renders). Zero objectives → the helper returns `[]` → emit nothing. READ-ONLY per FB-20 — these lines surface and suggest, they never ask for input, never render a widget; the weekly touch (Friday Wrap) is where objective asks live, and an objective whose graceful-death ask is pending emits no drift line here (the helper enforces the suppression via the death flag).

**Use the shared shape-aware reader (v3.4.5+ — MANDATORY).** Five distinct commitment-event shapes exist in production workspaces per `shared/COMMITMENT_SCHEMA.md`: canonical (`data.owner_id`), flat-new (top-level `owner_id`), legacy (`owner` no suffix), `owner_person_id`-variant (with `data.state` instead of `data.status`), and pending-review (filtered to Pulse). Direct reads of `data.owner_id` only catch shape #1 — silently drops ~42% of commitments in M's workspace. Always invoke through the helper:

```python
import sys
sys.path.insert(0, "shared/scripts")
from cru_match import _commitment_field, _commitment_confidence, load_open_commitments

# load_open_commitments handles the filter logic (status, closed-by-resolved,
# canonical/legacy shape across all 5 variants) in one call.
opens = load_open_commitments("<absolute path to _hq/data/events.jsonl>")
# Per-event field reads:
owner = _commitment_field(ev, "owner_id")
due   = _commitment_field(ev, "due")
status = _commitment_field(ev, "status")
```

Counts come from `commitment_state.compute_brief_state(...).counts` — which is `commitment_state.count_commitments(...)` verbatim, the one counting API (skip pending-review shape — those go to the Pulse CRU-review surface, not the morning count):
- **You owe:** `owner_id == <user_id>`
- **They owe:** `owner_id` non-empty and `!= <user_id>`
- **Unassigned:** `owner_id` null/missing — an extraction gap, but still an open commitment
- **Overdue:** `due` parses to a past date (re-evaluated at read time). NOT "stuck" — stuck is the movement metric (headline["stuck"], v4.6.0 MC2), a different number.

**Canonical-total parity (v3.18.5+, Bug #85 A85-followup — MANDATORY).** The header total the brief reports MUST equal `counts.total` (= `you_owe + they_owe + unowned` = `len(load_open_commitments(...))`) — the SAME number the coach reports. Do NOT report `you_owe + they_owe` as the total: that silently drops ownerless commitments (the v3.18.4 16-vs-18 split — see references/HISTORY.md § Bug #85). Surface the unassigned items rather than hiding them — in customer copy the word is plain: "2 with no clear owner", never "2 unassigned" (e.g. "13 you owe · 3 they owe · 2 with no clear owner" → 18 total). The coach reports `len(load_open_commitments)`; this reconciliation is what makes the two agree.

**7-day activity stopgap (v3.11.1 — REQUIRED for Needs Attention overdue surfacing).** Commitments accumulate as "open" in events.jsonl because no `commitment_resolved` event fires when the work actually completes (scale of the problem in references/HISTORY.md § Bug #85). Until the full B4 fix lands (meeting-notes / follow-up-ritual emitting `commitment_resolved` + a documented manual close path), the morning-brief "Needs Attention" overdue list MUST filter out commitments whose linked thread has had activity in the last 7 days:

1. For each commitment that would otherwise be surfaced as Stuck/overdue under Needs Attention, look up the linked thread (`primary_thread_id`).
2. Find the max `ts` across all events in events.jsonl where `primary_thread_id == <thread>` (any type — `interaction`, `meeting`, `commitment`, `commitment_resolved`, `intel_logged`, etc.). 
3. If that max `ts` is within the last 7 days, **drop the commitment** from Needs Attention — the work is probably done, just not formally closed, and surfacing it as overdue is noise.
4. The header counts (you owe / they owe / unassigned / total) STILL count all open commitments — the canonical `counts.total` is unaffected by this filter; only the surfaced Needs Attention items are filtered. The header preserves the true workspace state (and equals the coach's count); the surfaced list is the actionable subset.

This stopgap is removed when meeting-notes and follow-up-ritual reliably emit `commitment_resolved` for fulfilled items (planned for the B4 full fix).

If the workspace has **zero commitment events** but ≥3 meeting events on file, surface a one-line nudge in the briefing tail: `"💡 I don't have any commitments tracked yet, even though you've had N meetings — say 'scan for commitments' and I'll pull them out of your past meetings."` This is the discoverability hook for `scan-for-commitments` — most users won't know it exists otherwise.

### Step 3c: Latest-sender re-verification on EVERY "ball is on you" item (v3.13.7+ — MUST-language enforcement gate)

> **For every item the digest would surface as "ball is on you" — fresh inbox scan items, carried-over items from earlier same-day briefs (morning → evening), commitments-derived items from Step 3b — you MUST fetch the linked thread via the seam-resolved thread-fetch tool, requesting FULL message content, and read the LATEST message's `From:` header. If the latest message in the thread is FROM the primary user (M), DROP THE ITEM. The ball is not on the user; they already replied.**

No exceptions for "we already checked this in Step 2" or "this is cached state from an earlier fire." Every "needs your reply" / "propose times" / "ball is on you" surface fires the latest-sender check at digest-build time. This is the structural defense Session-22 Bug #2 documented as missing — the morning→evening re-surface of an already-answered thread (see references/HISTORY.md § Session-22 Bug #2).

The fix is unconditional re-verification:

1. Build the candidate set for "ball is on you" (fresh inbox + carry-over from Step 3b commitments + going-quiet items where the user owes the reply).
2. For each candidate, fetch the thread via the seam-resolved thread-fetch tool with full message content. Read the latest message's `From:` field. **Use a real thread-id, not a message-id** — thread-fetch tools want the thread-level id (the field name is per-provider, via `connector_adapters.mail.threading_field(provider)`); passing a message-id errors. A search result row gives you both, so pass the thread's id, never the message's id.
3. If `From == primary_user.email`, drop the item. Surface nothing for that thread under "ball is on you."
4. **Fail CLOSED on any thread-fetch error — never infer the latest sender from a search snippet (Bug #93, sub-cause c).** If the thread fetch errors for a candidate (wrong id type, API failure, thread not found), you have NOT confirmed the ball is on the user. Do one retry with the corrected thread-level id; if it still fails, **drop the item** rather than surfacing it on a guess (the live failure is memorialized in references/HISTORY.md § Bug #93). A snippet is the message the search matched, not the thread's latest message; inferring latest-sender from it re-introduces exactly the bug Step 3c exists to kill. No confirmation → no surface.
5. The 7-day activity stopgap in Step 3b is COMPATIBLE with this gate (Step 3b can still pre-filter; Step 3c is the final say). When in doubt, Step 3c wins because it reads the actual latest message, not an inferred state.

**Full message content is required, not optional.** Lightweight metadata fetches (subject + date only) don't carry the `From:` field reliably across mail-connector adapter shims. The full-content fetch is the only path that guarantees the latest-sender field is populated. Worth the extra connector cost — this gate fires once per surfaced item, typically 5-15 items per brief.

If the connector supports a single batched thread fetch for multiple thread ids, prefer that. Otherwise per-candidate calls. Don't skip the check to save calls; the trust cost of one false "ball is on you" surface dwarfs the connector cost of 15 thread fetches.

### Step 3c-bis: Calendar-action re-verification on scheduling "ball is on you" items (v3.14.7+ — MUST-language enforcement gate)

> **The Step 3c latest-sender check only recognizes an EMAIL reply as "the user handled it." A scheduling thread almost always closes on the CALENDAR, not in the inbox — the user replies by creating an invite, so the thread's latest message is still the counter-party's and Step 3c keeps the item surfaced. For every candidate that would surface as "reply to X to lock/propose/confirm a time", "set up the call with X", or any scheduling-flavored "ball is on you" item, you MUST also check the calendar before surfacing. If a calendar event exists with that counter-party that the user organized OR the counter-party has accepted, AND it was created/updated at or after the counter-party's last inbound message, DROP THE ITEM — the loop is closed on the calendar.**

This is the surfacing-layer twin of the Path 5 fix (`shared/scripts/cru_match.py::match_calendar_to_commitments`, daily backstop in `orchestrator-commitments.md` Phase 2.7). Path 5 closes the substrate commitment on the daily Commitments fire; this gate stops the morning brief from surfacing a stale "reply to X" item in the window before that fire runs — and catches inbox-derived items that were never a tracked commitment at all (the 2026-05-29 live scheduling-close bug — see references/HISTORY.md § v3.14.7).

Procedure (runs only for scheduling-flavored candidates — detect via `cru_match.detect_scheduling_intent` on the item text, or the obvious surface phrasing "lock / propose times / set up the call / find time / confirm the time / put on the calendar"):

1. Resolve the counter-party on the thread/item to a `person_id` (+ their email via `entities.json` / `aliases.json`).
2. Query the calendar (the declared calendar backend, resolved via `discover_calendar_tool` / `discover_for_category("calendar",…)` — native only, never Zapier, per CONTRACT Rule 8) for events involving that person from ~7 days ago through ~30 days ahead. **This is a DEDICATED wide fetch — do NOT reuse Step 2's today/tomorrow display pull (Bug #93, sub-cause b — see references/HISTORY.md).** The whole point of this gate is to catch a meeting booked days out; a narrow window makes the booked item look unbooked and it gets surfaced as "you still owe this". Issue the wide events query (window ≈ now−7d through now+30d; the neutral start/end are mapped to the provider's window fields by `connector_adapters/calendar.py`) before deciding any scheduling item surfaces. If you already pulled a wide window this fire, reuse that — but never the narrow display pull.
3. **Drop the item** if any matching event meets either bar:
   - the user is the organizer/creator and the event was created/updated at or after the counter-party's last inbound message on the thread, OR
   - the counter-party has accepted the invite (via `connector_adapters/calendar.py::is_accepted`, which reads the provider's RSVP-acceptance field rather than a hardcoded field name).
4. If no such event exists, keep the item — the ball really is on the user.

Step 3c (email latest-sender) and Step 3c-bis (calendar) are both final-say drops: an item surfaces only if it survives BOTH. When in doubt, drop — a missed "you already did this" is far cheaper to trust than a false "you still owe this." This also means a counter-party invite-acceptance (e.g. "Lyra accepted the call") is treated as a close signal here, not discarded as inbox calendar-noise.

### Step 3d: Compute the surfaced state deterministically (v3.14.8+ — MANDATORY)

Steps 3b/3c/3c-bis describe the rules; **`compute_brief_state` is the code that applies them.** Do not re-implement the open/overdue counting or the three drops by hand — gather the inputs and call the function. It is the same-inputs-same-output guarantee that stops this logic from drifting fire to fire.

Gather (this is the connector work — only the FETCH is yours, not the decisions):
- `opens` = `load_open_commitments(events_jsonl_path)` (Step 3b).
- `threads` = for each linked thread you expanded in Step 3c, `{thread_id: {"latest_sender_is_user": <bool from the get_thread latest-message From: check>}}`.
- `calendar_events` = the native-Calendar `list_events` results from Step 3c-bis, each resolved to `{attendee_person_ids, summary, created_ts, accepted_by, calendar_event_id}` (attendee emails → person_ids via `aliases.json`/`entities.json`).
- `thread_activity` = the CANONICAL derivation over the Step 3b events (C3 — do NOT inline your own max(ts) scan; this is the same helper every other recency surface reads): ONE command — `from thread_activity import ALL_TYPES, derive_from_events` then `thread_activity = {tid: act.ts.isoformat() for tid, act in derive_from_events(events, activity_types=ALL_TYPES, honor_reclassifications=True).items()}`. Renderer "last touched" semantics (every event type counts — the 7-day stopgap's original intent), reclassifications folded (RECL1), related threads credited, legacy ts/thread-id spellings parsed, and the standard 0.40 confidence floor applied — an unconfirmed low-confidence classification no longer silently mutes an overdue item. (C3 migration 2026-07-22, the RECL1 M3 spun-off follow-up; the pre-migration hand-rolled scan counted primary-thread ids only and ignored the floor — the canonical derivation is deliberately the fleet-consistent read.)
- `todays_meetings` = the list built in Step 2's calendar scan (meeting_id / title / attendee_person_ids / attendee_names incl. alias spellings). Omit only when the calendar is unavailable.

```python
import sys
sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from commitment_state import compute_brief_state  # promoted home (Stage A); brief_state aliases it

from commitment_state import compute_and_log_brief_state
from primary_user import resolve_primary_user
opens = load_open_commitments("<absolute path to _hq/data/events.jsonl>")
# MUST use compute_and_log_brief_state — NOT a hand-rolled count (Bug #99). It calls
# compute_brief_state and emits a `brief_state` audit event carrying the CODE's real
# numbers, so a bypass is detectable (a brief with no brief_state event hand-rolled).
# Hand-rolling the counts/drops — even when they happen to match — is the #99 bug: the
# drop rules (calendar / email-reply / recent-activity / reconcile_stale) are subtle and
# WILL drift if re-derived in prose. Render ONLY from this state.
state = compute_and_log_brief_state(
    "<workspace root>",
    open_commitments=opens,
    user_person_id=resolve_primary_user("<workspace root>"),  # deterministic (Bug #102) — never guess

    now_iso="<current time ISO, workspace TZ-aware>",
    threads=<dict built above>,
    calendar_events=<list built above>,
    thread_activity=<dict built above>,
    sent_reconcile_cursor=<workspace.sent_reconcile_cursor from entities.json, or None>,
    todays_meetings=<list built in Step 2>,
)
# Render from state — do NOT recompute:
#   state["counts"]["headline"] → the commitments line's numbers (you_owe /
#     owed_to_you / unowned / unconfirmed / overdue / stuck / blocked —
#     v4.5.2 R4 + v4.6.0 MC2, the ONE bucket export every surface renders;
#     F-47 P2b / F-56). Never fold unowned or unconfirmed into a direction.
#     headline["stuck"]/["blocked"] are the REAL movement metric (MC2 —
#     commitment_activity.py; compute_and_log_brief_state derives the
#     movement map automatically). The deprecated TOP-LEVEL counts["stuck"]
#     key is still overdue-by-due-date and still never renders (R1b).
#   state["needs_attention"] → the "ball is on you" items under Needs Attention
#     (SUB1: top-level items only — a sub-item never gets its own brief line;
#     when a row carries n_subitems_open/n_subitems_done, render the progress
#     inline — "2 of 3 sub-items done · next: [step]" — from those keys plus
#     next_subitem_due; a row carrying all_subitems_resolved renders "all
#     sub-items done — close it?" as a PROPOSE, never an auto-close)
#   state["meeting_linked"] → open items relevant to TODAY's meetings, matched
#     by counterparty or name-mention (v4.5.2 C1 / F-44). Render under the
#     matched calendar event (Step 4 "you two have open business" sub-lines).
#     These carry no due-date requirement and are NOT subject to the drops —
#     never suppress one because it is undated, task-kind, or on a recently
#     active thread. Items flagged pending_review render as needing a
#     confirm ("captured from a chat — confirm it's yours"), never as
#     settled fact and never with an auto-chase affordance. SUB1: these
#     rows DO include sub-items (F-44 — a step relevant to today's meeting
#     surfaces on the day of the meeting); a row carrying parent_id/
#     parent_title renders with "part of: [parent title]".
#   state["dropped"] → diagnostic only; never shown to the user (Rule 4/9)
#   state["reconcile_stale"] → Bug #98-v2 floor. If True (cursor absent or >1 day
#     old), reconciliation is behind: render EVERY needs_attention item softened
#     ("you may have already handled this — I haven't been able to check your
#     sent mail since [cursor date]"), NOT as "reply/send/follow up". Items also carry a
#     per-item reconcile_stale flag. This is the deterministic guarantee that a
#     skipped reconcile (Step 3a-bis) can never tell the CEO to redo done work.
```

Render `state["counts"]` as the commitments line and `state["needs_attention"]` as the "ball is on you" items. `state["dropped"]` is for diagnostics only — it explains why an item was suppressed (`calendar_action` / `email_reply` / `recent_activity`); never surface it in chat. If you can't fetch a given input (connector down), pass what you have — the function degrades gracefully (a missing `threads`/`calendar_events`/`thread_activity` just means that drop isn't applied; the item surfaces, which is the safe direction).

### Step 3e: ONE gated source for EVERY "ball is on you" actionable — including Top 3 moves (v3.18.9+ — MUST-language enforcement gate, Bug #93)

> **Every actionable anywhere in the digest that tells the CEO they owe someone an action — "reply to X", "follow up with Y", "book / lock / propose a time with Z", "send the X to W", "get back to V" — MUST be drawn from the gated candidate set, NOT synthesized freehand from your raw inbox/calendar reads. There are two legitimate sources, and ONLY these two: (1) an item in `state["needs_attention"]` (already survived the 3c/3c-bis/7-day drops), or (2) an inbox-derived item that you have personally run through the Step 3c latest-sender check AND the Step 3c-bis calendar check this fire. If an actionable came from neither path, it does not appear — not in Needs attention, not in Top 3 moves, not in Suggested next steps.**

This closes the #93 trust-killer. The Top-3-moves and Suggested-next-steps sections are written in Step 4 as a *separate synthesis* over the morning's inbox/calendar scan — and that synthesis bypassed `compute_brief_state` entirely, so items the gates had already dropped (a meeting booked days out, a thread the CEO already replied to) reappeared at the very top of the brief as "do this now" (live failures in references/HISTORY.md § Bug #93). Telling the CEO to redo finished work is the same trust-killer as the #85 class.

The rule, concretely:
1. **Tracked-commitment actionables** (you owe X per events.jsonl) come ONLY from `state["needs_attention"]`. If `compute_brief_state` dropped it (it's in `state["dropped"]`), it is handled — it may NOT be promoted into Top 3 moves on your own judgment that it "still feels open". The function already applied the calendar / latest-sender / recent-activity drops; second-guessing it is the bug.
2. **Inbox-derived actionables** (a "reply to X" that is NOT a tracked commitment — e.g. an overnight email that needs an answer) are legitimate to surface, but ONLY after you run the SAME two checks the gates apply: Step 3c (`get_thread` latest-sender, fail-closed on error) AND, if it's scheduling-flavored, Step 3c-bis (wide calendar fetch). An inbox-derived "reply to X" where X's thread shows the CEO as latest sender, or where a matching invite is already booked, is dropped exactly like a tracked one.
3. **Non-owing moves are unaffected.** "Read [doc] before the 2pm", "prep for the Acme call", "decide on the pricing" — moves that don't assert the CEO owes a *reply/booking/send* to a counter-party — are normal Top-3-moves and don't go through the drops. The gate governs *ball-is-on-you* actionables specifically.

When in doubt, drop: a missed "you already did this" costs nothing; a false "go redo this" costs trust. This gate is the single chokepoint — there is no second, ungated path to a "ball is on you" line.

### Step 3f: Load active reminders (v4.6.0 W4a — the user's own pins)

Reminders are the user's explicit "remind me about X on [day]" pins — their own event lane (`reminder` / `reminder_updated` / `reminder_cleared`), **never commitments**. They do NOT enter `compute_brief_state`, the commitment counts, Needs Attention, chase, or triage — do not fold them in, do not count them anywhere. Load them with the canonical pure reader; never hand-scan events.jsonl for reminder types:

```python
import sys
sys.path.insert(0, "shared/scripts")
from reminders import load_active_reminders

rems = load_active_reminders(
    "<workspace root>",
    "<today, workspace TZ date>",
    surface="m_facing",   # the brief is an owner-facing surface — personal reminders render here
)
# Each row: {id, summary, due, personal, ref, repeat, remind_from,
#            status: pinned|upcoming|scheduled, days_pinned,
#            escalation: none|bold|top, last_touch}
```

Render rules (M's four settled choices, 2026-07-08):

- **Pinned** (`status == "pinned"`): renders EVERY day until cleared or pushed — a pinned reminder never auto-fades. Each row carries the three affordances as plain chat phrases — done (*"done with the reminder"*), defer (*"defer it to [day]"* / *"push it to [day]"* — same move, and the taxonomy's widget label is **Later…**), keep — plus the daily ask ("Still want this pinned?" phrasing varies naturally). Rows with `escalation == "bold"` render bold (pinned + ignored 3 days); rows with `escalation == "top"` (7 days) LEAVE this section and render at the very top of the brief (see the template). A reminder with `ref` renders its context inline ("about: the Pedro chase") but done/push/keep only ever write reminder events — never a commitment closure. If the user ALSO says the underlying item is done, that closes separately through the canonical closure path.
- **Upcoming reminders** (`status == "upcoming"`, within 3 days): lighter render — one line each, no ask, no affordance row.
- `status == "scheduled"` rows do NOT render in the brief (they show in `show my reminders`).
- Both sections render only when non-empty (never pad). Reminder rows never mention `personal`, event names, ids, or the word "reminder lane" — plain English only.
- These phrases route to the `show-my-reminders` skill's writer helpers (`shared/scripts/reminders.py`) — the brief itself stays read-only (its one write remains the pack_run receipt).

### Step 3g: Confirm-section pointer count (v4.6.1 W4b — one number, read-only)

The daily Waiting On chat (CTS1 — the re-scoped Commitments chat) opens with the "Needs a quick confirm" section (W4b; the selector covers every unadjudicated amber capture younger than the 7-day escalation pin). The brief carries ONE pointer line when that section will be non-empty — never the rows themselves (the Waiting On chat is the triage point; the brief just points). Compute the count with the same selectors that chat uses, over the open set Step 3 already loaded:

```python
from confirm_flow import (select_confirm_items, select_promotion_proposals,
                          load_open_person_proposals, confirm_pointer_line)
from identity_reconcile import count_person_rows
from mute_ledger import active_dismissal_target_ids

dismissed = active_dismissal_target_ids(<all events>, "<now ISO>")
person_rows = load_open_person_proposals(events_path, dismissed_target_ids=dismissed, suppress_on_file=True)  # FS-19: don't count already-on-file people
n_confirm = (len(select_confirm_items(opens, "<now ISO>", dismissed_ids=dismissed))
             + len(select_promotion_proposals(opens, dismissed_ids=dismissed))
             + count_person_rows(person_rows, now_iso="<now ISO>"))  # PID1: count CLUSTERS (one person = one row — the same projection the queue renders), never raw proposal events
pointer = confirm_pointer_line(n_confirm)   # None when the section is empty
```

`pointer` is the exact line the template renders — when it is None the line is OMITTED entirely (never pad, never render "0 items"). These items are ALREADY inside `headline["unconfirmed"]` (they count nowhere else); the pointer adds no numbers to the commitments line, and reminders (Step 3f) are a different lane entirely — never fold the two.

### Step 3h: The money carve-out + the queue pointer (SPEC FB-20 — the brief is read-only)

**The brief does NOT adjudicate anything (FB-20 — M's ruling 2026-07-16: "the morning brief should just be a morning brief").** The LB1 "Needs your eyes" confirm card is **RETIRED from this surface**. No card, no rows, no buttons, no `show_widget` — a widget posted from a brief fire is a contract violation regardless of its contents. The proposal queue is adjudicated at the **staff meeting** (Mon/Wed/Fri by default, or any time the user says `staff meeting`), which is now the sole adjudication surface. This step is what the brief says about that queue instead.

**MANDATORY — DO NOT SKIP (FS-09). Time-of-day INVARIANT.** Both blocks below render whenever the driver's pack carries them, EVERY fire — a late-night "day-close" framing does NOT license dropping them. A brief that says "nothing open tonight" over a non-empty pack is the FS-09 failure. The editorial voice may shift with the hour; the mandated blocks do not.

**The `surface_drivers.py morning-brief` driver call already computed both blocks** (t3 FB-9 — the one-command pack). Render `money_lines` and `queue_pointer.line` from the pack VERBATIM. Do not re-derive either one; the code below documents what the driver does, and remains the path for direct/one-off invocations only:

```python
from brain_proposals import load_open_proposals, money_prose_lines
queue = [i for i in load_open_proposals(WORKSPACE_ROOT, "staff-meeting")
         if i.get("tier") != "auto"]
money_lines = money_prose_lines(queue, cap=3)   # pack["money_lines"]
count = len(queue)                              # pack["queue_pointer"]["count"]
```

- **`money_lines` — THE ONE EXCEPTION.** Money-class proposals (deal signals) are the single class the brief still names outright, as digest PROSE, one sentence each: *"Command Room thinks Northwind is a live deal — say `staff meeting` to confirm."* Money may never go silent — a deal signal sitting unmentioned for a day is the one silence with a price tag. These sentences are **propose-only and carry no verbs**: never attach buttons, never invent a "confirm?" affordance, never act on one from a brief turn. The confirm is a chat phrase at the staff meeting. Place them with NEEDS ATTENTION in the digest body. Empty → nothing renders; **never** pad an all-clear ("no new deals today" — never).
- **`queue_pointer.line` — the handoff.** ONE line, verbatim, as the digest's last content line before SUGGESTED FIRST MOVE: *"7 things need your eyes — say `staff meeting`."* That is the brief's entire adjudication affordance. The count comes from the same projector the staff meeting renders (same surface, same held/mute filters), so it can never over-promise — **never recount it, never adjust it, never round it, never soften it into "a few things."** Nothing queued → the driver returns an empty line and nothing renders (drop-empty; never "0 things need your eyes", never an all-clear pad).
- The Step 3g confirm-pointer stays as-is — it counts the Waiting On chat's OWN confirm section; this is the cross-detector queue. An item can legitimately appear in both; that is not a bug.
- **First-run gate (FRP1) — RETIRED with the card.** `skill_config/system-health.json` key `daily_confirm_card` no longer gates this surface: there is no card to gate, and the two blocks above are substrate truth the brief always owes. The key is still read by the surfaces that do render the card. (An `"off"` value never suppressed the queue anyway — it reached the user through the Staff Meeting and `what's waiting on me`, which is now the only path by design.)

## Step 4: Build the Digest

Format the output as a structured, scannable digest. Skip any section that has nothing to report — never pad an empty section into existence. (The template below shows every POSSIBLE section; a typical day renders a handful.)

**Relationship-grouped thread layout (v2.2):** Active threads render in groups derived from the org tree, not a fixed home/side split. Authoritative rules — read these in order before rendering:

1. Every thread's `affiliation_id` resolves to an `org` record in `entities.json`. Use the **most specific** level available — `org_acme_restaurant`, not the holding `org_acme_co` — so threads appear under the operating unit they belong to.
2. Groups in the briefing are defined by `org.is_primary_focus`:
   - **Primary focus orgs** render prominently and in full detail. There can be **more than one** (a portfolio / holding-co operator may have 2–4). Render them in the order of `last_interaction` (most recent first), with holding orgs rendering as a parent header with operating children nested beneath.
   - **Non-primary orgs** (`is_primary_focus: false`) roll up into a single OTHER ORGS section, grouped by `relationship_type` (board / advisory / investment / client / portfolio_company / beneficiary / partner / other) and collapsed by default.
   - Threads with `affiliation_id: "personal"` are hidden unless the user explicitly asks for them.
3. Section headers use `canonical_name` directly — no hardcoded labels like "HOME ORG" or "SIDE". If the workspace has exactly one primary focus org, that org's name becomes the top section; if multiple, each renders as its own top section.
4. Nested rendering: when a primary focus org has children (scope=holding with operating children), render the holding as a section header and list each operating child as a subheader with its threads underneath. If a holding has ≥4 operating children, collapse the least-recently-active ones into "+ N more" with a show-all option.
5. `relationship_type` badges appear inline next to each thread's org label when non-obvious (e.g., `[board]`, `[advisory]`, `[investment]`). For threads where the CEO has `relationship_type: "operating"`, no badge is rendered — that's the default assumption for primary focus.
6. Briefing layout is derived at render time from what's present in `entities.json`. Do not hardcode a single-org or dual-org shape.

**v3.13.0+ — top-down layout with synthesis lead, top-3 moves up top, going-quiet promoted, and momentum delta.** Per M's 2026-05-20 feedback #30, the digest opens with the answer to "what should I do" before the lower-priority context — top-3 moves render right after the synthesis lead (design history in references/HISTORY.md § v3.13.0).

**Synthesis-lead test.** Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md`: the lead must name a dated moment AND say what CHANGED. Bare metric counts are not a lead.
```
GOOD: The May 14 CEO-group talk was the anchor — $45K early pipeline, two
      booked demos, and it opened the non-profit logistics wedge now running
      as a thread. Everything else this month supported it.
BAD:  This month had a lot of activity. Closed some deals, shipped features.
BAD:  May saw 6 commitments, 4 decisions, 15 emails.   (metrics, no meaning)
```
Test: does the lead name a dated moment AND say what CHANGED in the business? If the commitment counts alone tell the same story, the lead is redundant — rewrite for shape, not numbers.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Your activity log has 3 incomplete entries — recovery pending in next update."
- GOOD: "A few entries in your activity log look incomplete — I'll tidy those up during this weekend's cleanup."

```
Morning briefing — [Day, Month DD, YYYY]

[Escalated reminders — ONLY rows with escalation == "top" (pinned + ignored 7 days). Renders FIRST, above the synthesis lead, per M's escalation choice. Skip entirely when none.]
📌 **You've been carrying this [N] days: [summary]** — done, defer it to a day, or say keep.

[Synthesis lead — one-line theme = the exec-header VERDICT (EXEC1 element 1).] Distill the day in a single sentence:
"Today is gated by the 4:45 negotiation with Acme; everything else is supporting cast."
"Heavy on Acme ops review; the plugin work is the asynchronous backbone."
Match Friday Wrap's lead-paragraph pattern — one anchor moment + theme. Skip
if nothing distinctive (then jump to commitments line).

[The 30-second contract — the three EXEC1 lines, rendered right after the synthesis lead (chat-surface form per shared/EXECUTIVE_OUTPUT_STANDARD.md element 1). This SUBSUMES the standalone "Momentum delta" line below — CHANGED absorbs it; do not render both.]
CHANGED   [what moved since yesterday's brief — named people/threads + numbers/dates, OR "Nothing material since [last brief]." LB1: this line now ALSO carries what the system did on its own — fold in `change_feed.changes_since(<last brief ts>)` (Step 3a-bis), one to three of its lines max, substance first (closures/recoveries before housekeeping), drop-empty. The feed's closed-from-sent line keeps its `undo` affordance verbatim. One narration slot — never a separate reconcile tail line or a second "what I did" block. **MANDATORY (FS-09): when `changes_since` returns any lines, CHANGED MUST cite them — you may NOT write "Nothing material" over a non-empty feed. The feed lines are traceable to audit events; report them, don't editorialize them away.**]  (this is the former Momentum-delta line)
DECIDE    [Your one decision today: X — when a decision-shaped item exists (a decision_pending item on today's meeting threads, or a decide-shaped needs_attention item — both already in compute_brief_state, NO new fetch). Else: "Nothing — execution day."]
NEEDED    [the single most important reader-action today, OR "Nothing from you."]
[Concreteness floor: each line carries a named entity, number, or date, OR uses the explicit nothing-form. The generic-summary shapes ("key developments", "several updates", "busy week across", "lots of movement") are banned — run scan_for_generic_summary on these lines.]

[Pinned — Step 3f rows with status == "pinned" (minus the escalation-top rows already rendered up top). EVERY active pin renders EVERY day until cleared or pushed. escalation == "bold" rows render bold. Skip the section when empty.]
Pinned
📌 [summary] [— about: [ref context]] [· due [date]] — day [days_pinned+1]. Done, push, or keep?
📌 **[summary]** — [days_pinned] days now. Done, push it to a day, or keep?

[Upcoming reminders — Step 3f rows with status == "upcoming" (remind_from within 3 days). One light line each, NO ask, no affordances. Skip when empty.]
Upcoming reminders
· [summary] — from [Weekday]

[Reminders are the user's own pins — NOT commitments. They never appear in the commitments line below, Needs Attention, or Top 3 moves, and nothing chases them.]

[Commitments with context, not raw counts. Every number comes verbatim from
state["counts"]["headline"] — the one bucket export (v4.5.2 R4). ONE line,
ONE template — the pre-R4 "they owe · stuck" form is retired.]
Commitments: [Y] you owe (+[delta] since [last_brief_date], [closed_yesterday] closed) · [Z] owed to you · [U] unowned · [C] unconfirmed · [O] overdue · [S] stuck
[Omit a zero bucket from the line (never pad); omit the whole line when headline["total"] is 0.]

[Dated-Personal echo (CTS1 §4.2, RULED 2026-07-16) — ONE line under the
commitments line, rendered ONLY when at least one owner-me effective-kind-task
item is DUE TODAY (surface_split.partition_surfaces(opens, user_id)["personal"]
filtered to effective due == today):]
[N] personal item[s] due today — they're on your My Plate chat.
[DATED items only — never echo the undated Personal tail here (the 30+ day
stale tail rides Friday triage's "still on your plate?" sweep, and the full
list is the My Plate chat's job). Zero dated-today → omit the line entirely.]
[The overdue number is exactly what it says — items past their due date; never
attach a movement definition to it (R1b).]
[The stuck number is headline["stuck"] — the REAL movement metric (v4.6.0 MC2:
commitment_activity.py derives last movement per commitment from state-change
events, capture ts as floor; compute_and_log_brief_state supplies it
automatically). Inline define it on first mention — legitimate again because
the code now computes exactly this:
("stuck" = no movement in 21+ days, or blocked on a named person)
When headline["blocked"] > 0, say who the wait is on in plain English, e.g.
"2 of them waiting on people you've already chased". If headline carries NO
"stuck" key (movement derivation unavailable), OMIT the segment — never render
0 for a number that wasn't computed.]

[Confirm pointer — Step 3g's `pointer` line VERBATIM, its own line right
after the commitments line. Renders ONLY when the confirm section is
non-empty (pointer is not None) — omit entirely otherwise. ONE line, never
the items themselves, never a count folded into the commitments line above,
and placed clear of the reminders sections (different lane — reminders are
the user's own pins; this points at captures awaiting a confirm). v4.6.1 W4b.]
[N] new items need a 10-second confirm — they're in your Waiting On chat. [Pre-CTS1 workspaces still on the `commitments` task: say "Commitments chat" until the split registers.]

[Top 3 moves before noon — the answer to "what should I do." This is the most important section. Surface it right after commitments so it's seen in the first 15 seconds.]
Top 3 moves today
1. [action] — [why now / what unlocks]
2. [action] — [why now / what unlocks]
3. [action] — [why now / what unlocks]
[Each move is specific (not "review the Acme thing" — "read [person]'s [artifact] against the [doc]"). Rank by: (a) gates a meeting today, (b) deadline today/tomorrow, (c) highest-revenue dependency.]
[GATE (Bug #93): any move here that is a "ball is on you" actionable — reply / follow up / book / propose times / send / confirm with a counter-party — MUST come from the Step 3e gated set (state["needs_attention"], or an inbox item you ran through the 3c + 3c-bis checks this fire). NEVER promote an item compute_brief_state dropped, and never surface a reply/booking move you have not latest-sender + calendar verified. Non-owing moves (read / prep / decide) are exempt.]

[Momentum delta is now the CHANGED line of the 30-second contract above (EXEC1 subsumption) — not a separate block. If it's been ≥3 days since the last brief, CHANGED falls back to the honest steady-state form rather than a hard-to-summarize backfill.]

[Going quiet — promoted from the old "Other Orgs" buried footer.]
Going quiet — [N relationships]
⚠️ [Person/Org] — [N] days since last contact, usual cadence: [baseline], last topic: [topic]
[List the top 3-5 going-quiet relationships ranked by relationship value × deviation
from cadence baseline. Promoted to top-tier per M's feedback #30 (see
references/HISTORY.md § v3.13.0).]

Today's calendar ([X] events)
• [TIME] — [Event title] ([attendees]) [⚠️ no prep / 🔗 related to Project X / ✓ already wrapped]
    ↳ open with [name]: [commitment title] [— due [date] / no date set] [· needs a quick confirm]
[The ↳ sub-lines are state["meeting_linked"] rows rendered under their matched
event (v4.5.2 C1 / F-44) — every open item whose counterparty is in the room or
whose text names someone in the room, INCLUDING undated ones; a missing due
date must not hide an item on the day of the meeting. "no date set" renders
plainly, never as a blank. pending_review rows carry the "needs a quick
confirm" tag and are asks-to-confirm, not settled facts. Omit the sub-line
only when the row is already rendered verbatim in Needs Attention.]
[H2 link to the call-prep brief if one was generated, per CONTRACT Rule 3 — clickable, opens in side panel.]

[Week-ahead horizon.]
This week ahead: [Wed/Thu light · 3 demos Friday · Acme contract due Mon].
[One line. Keeps the user oriented past today without dragging the brief long.]

Needs attention
🟡 [Item waiting on user sign-off / etc.] — [context]
⚡ [Aging cluster] — [N] commitments aged past [threshold] in [project]
🔄 [Prospect that looks converted] looks like a client now ([reason]) — say `[Name] is now a client`
[One 🔄 line per detector candidate, rendered verbatim from its `render_line` — render ALL of them, never a subset (Bug #92b). If the detector returns nothing, skip only the 🔄 lines.]
[If nothing: skip this section.]

Overnight inbox ([X] worth your attention from [Y] total)
📧 [Sender]: [one-line summary] — [why ranked first: "first because it gates today's 4:45 call"]
📧 [Sender]: [one-line summary] — [why ranked: "decision needed before Wed deadline"]
[Top 5 max. Apply self-reply filter per v3.11.1 — drop threads where M is latest sender. Show sort reasoning inline so the order isn't a black box.]

[Primary focus org sections — one per is_primary_focus=true org.]

Command Room
  External (business / GTM)
    • [Thread A] — Next: [action] | Last touched: [date]
    • [Thread B] — Next: [action] | Last touched: [date]
  Internal (plugin build)
    • Plugin ship — Next: [internal-only action]
    [Internal vs External subsections. Pre-v3.13.0 mixed M's CR plugin
    self-development with external client commitments in one list per #6a/#23a.
    Visually separate so client-facing work and internal build work don't blur.
    This split applies only when the user IS the builder of the Command Room
    plugin (M-specific case); regular users see only one section.]

[Acme Co]
  • [Thread C] — Next: [action] | Last touched: [date]
  • [Thread D] — Next: [action] | ⚠️ quiet [X] days

[For nested holdings — render holding as header, operating children indented:]
Summit Company [holding]
  └── Acme Restaurant
      • [Thread E] — Next: [action]
  └── Acme Bakery
      • [Thread F] — Next: [action] | ⚠️ quiet [X] days

Other relationships — [N threads across N orgs]
[Now shorter than pre-v3.13.0, because high-signal aging-out relationships were
promoted to Going quiet above. This section lists only orgs with active threads
that don't fit a primary-focus org. Collapse to top 4 by last_activity if >6;
append "+ N more".]
• [Thread G] ([org canonical_name] [advisory]) — Next: [action]

[Personal threads hidden unless the user explicitly asks for them.]

[Sources section — include ONLY if there's something to cite (Gmail
threads, Granola transcripts, Drive docs that informed the brief). If empty,
omit the "Sources:" header entirely.]
Sources: [optional — only if cited]
- [Title — date](url)

Suggested next steps
[If the Top 3 moves section above captured the morning's shape, this section is optional or
collapsed. Otherwise: 3-5 more specific next-action items by project.]
```

[L — VOCABULARY SCRUB FOR CLIENT PORTABILITY.] When morning-briefing ships for users other than M, scrub M-internal vocabulary on render:
- "EOS 2.0 wedge" → just "EOS" or the canonical phrase the user uses
- "v3 ship-flow shakedown" → drop the version reference, use plain English
- "IP-attorney check before any EOS pitch" → use the user's own framing if it exists in their session notes; otherwise keep generic
- M's specific project nicknames stay (those are personal language M wants); but Chalette-specific build terminology gets generic substitutes for non-M users.

This scrub runs at render time — read the user's session notes / project context for the language they actually use, and prefer that vocabulary over the abstract operator-class terminology.

## Step 5: Deliver the Digest

### Scheduled mode (running as a scheduled task):

The Morning Brief chat IS the surface. The `morning-brief` orchestrator (registered by enable-command-room-schedules, back-filled by command-room-update-bridge) posts the digest as a markdown chat post in that persistent scheduled chat, saves the snapshot copy to `_hq/briefings/morning-[YYYY-MM-DD].md`, and records the `pack_run` receipt (including `data.needs_attention_ids` so `mark done [n]` resolves — see Step 3b). The post is markdown end to end — **no widget on any fire (FB-20)**: Step 3h's money sentences and queue-pointer line are prose inside that same digest, and `show_widget` is never called from this surface. Do not send the digest anywhere else on a scheduled fire.

**Legacy delivery-channel fallback (explicit opt-in only):** if the user's `CLAUDE.md` / `_hq/BUSINESS_CONTEXT.md` carries an explicit "Briefing Delivery" preference naming Slack or email, honor it as an ADDITIONAL copy (Slack DM, or Gmail with subject `Morning briefing — [Day, Month DD]`). Never infer this from a connector merely being connected, and note that `mark done [n]` only works in the Morning Brief chat.

### Manual mode (user triggered in chat):
- Display the digest directly in chat
- No file save needed (the "what's going on" command handles full briefing saves)

## Tone

Direct and specific, like a calm chief of staff. **Opening order (the one canonical answer):** (1) the personified intro line from the Personification section — `"Morning, {first_name} — {brain_name} here with today's read."` — renders first and is the ONLY greeting; (2) the `Morning briefing — [Day, Month DD, YYYY]` header; (3) the synthesis lead. No other greeting anywhere ("Good morning!" / "Here's what's happening!" — never). The content itself reads as friendly plain English, not engineer status-board ("3 commitments aging past 14 days" is fine; "DRIFT: 3 commitments aged past threshold" is not). Per CONTRACT Rule 4 — no all-caps section headers, no scores, no internal mechanism names.

## Gotchas

- **Scheduling threads close on the calendar, not the inbox.** The latest-sender check (Step 3c) only sees email replies. When the user answers "can we set a time?" by creating a calendar invite, the thread's newest *message* is still the counter-party's, so the email-only check keeps surfacing "reply to X to lock the time" for days (the v3.14.7 live bug). Step 3c-bis is the fix — for any scheduling-flavored "ball is on you" item you MUST also check the calendar and drop it if the user organized / the counter-party accepted a matching event. A counter-party invite-acceptance is a close signal, not inbox noise.
- **Don't duplicate "what's going on."** This briefing is shorter and proactive — it fires before the user asks. "What's going on" is the comprehensive interactive version. They complement each other.
- **Don't update the tracker.** This is read-only. Surface what you find; don't change anything. The user decides what to act on during their actual work session.
- **Don't read session notes.** The tracker has enough for a morning scan. Per-project deep dives happen on "go [project]." Keep this fast.
- **Respect quiet periods.** If the tracker shows no active projects (all Steady State or Archived), output a minimal briefing: "Quiet day. Calendar: [events]. Inbox: [count] new." Don't pad.
- **Weekend handling.** If configured as a weekday-only scheduled task, this won't fire on weekends. If the user manually says "morning briefing" on a weekend, run it normally — they're choosing to check in.
- **First-time setup.** If `_hq/MASTER_TRACKER.md` doesn't exist, this workspace hasn't been set up. Output: "Looks like your Command Room isn't set up yet. Say 'set up my command room' and I'll walk you through it." Don't attempt to scan.
- **Connector failures.** If a connector times out or errors, skip it and note: "Couldn't reach [Gmail/Calendar/Slack] right now — I'll try again on the next brief." Don't let one failure block the whole briefing.
- **Morning briefing files are ephemeral.** Files saved to `_hq/briefings/morning-*.md` follow the same 30-day pruning as regular briefings (Rule 4). They're snapshots, not permanent records.

## Reliability

This skill runs as a scheduled task (weekdays 7:30am) and must implement `shared/RELIABILITY.md`. Key rules: skip-not-fail when workspace isn't ready (log to `_hq/logs/scheduled-task-skips.log`, exit clean, never produce empty briefings), OOO detection via `_hq/BUSINESS_CONTEXT.md` (render an OOO-mode briefing with only urgent items), missed-fire recovery (produce one catch-up covering the gap window, max 3 days), 15s per-connector / 60s aggregate timeout budget with graceful degradation, and last-known-good cache at `_hq/caches/[connector]-last-good.json` when a connector fails. Never fabricate data when a connector is unavailable — say "I couldn't reach [source] just now" and continue.

## What It Doesn't Do

- Does not triage individual emails or draft replies — that's `inbox-triage`.
- Does not produce deep per-meeting prep — that's `call-prep`.
- Does not update MASTER_TRACKER, entities.json, or any other workspace state — this skill is read-only, and since FB-20 it is read-only in the stronger sense too: it renders no card, so nothing can be adjudicated, confirmed, or applied from a brief. (It no longer writes the LB1 card's shown-markers either — with no card rendered there is nothing to mark shown, and the staff meeting sees the full queue.)
- Does not generate insights or pattern analysis — that's `insight-generator`.
- Does not deliver on weekends by default — manual trigger only on weekends.

## Routing (full trigger corpus)

The settings-trigger family for this skill, relocated verbatim from the pre-G11-diet description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Also handles first-run personalization settings — use when the user says 'tune morning-briefing', 'show morning-briefing settings', 'reset morning-briefing to defaults'. Also takes standing customization preferences — use when the user says 'customize morning-briefing', 'show morning-briefing customizations', 'reset morning-briefing customizations'.
