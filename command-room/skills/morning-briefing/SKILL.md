---
name: morning-briefing
description: "Proactive daily digest — calendar, important email summary, overdue follow-ups, urgent items. Triggers: 'morning briefing', 'daily briefing', 'brief me', 'what do I need to know today', 'start my day'. DOES NOT fire on 'triage my inbox', 'process my inbox', 'what's in my inbox' — those go to inbox-triage for a deep classification + drafts pass."
---

## Deterministic state computer (mandatory, v3.14.8+)

> **The commitment header counts ("you owe / they owe / stuck") and the "ball is on you" Needs-Attention list MUST come from `shared/scripts/brief_state.py::compute_brief_state(...)` — NOT from re-deriving the open/overdue/whose-turn/drop rules in prose. This is the single source of truth for that computation; Steps 3b/3c/3c-bis below document the rules the function already implements and tell you how to gather its inputs. Your job at runtime is to FETCH the inputs (open commitments, per-thread latest-sender, calendar events, per-thread last-activity) and pass them in, then RENDER what comes back. Do not recompute the drops yourself — the function decides, and it is unit-tested (`tests/run_brief_state_test.py`).**

The drop logic lived as prose across three steps for releases and drifted (the v3.14.7 calendar-close bug was one instance). `compute_brief_state` collapses it into one tested function so the same inputs always produce the same surfaced list. See Step 3d for the call.

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

For the open-commitment / overdue-follow-up sections, you MUST call `shared/scripts/cru_match.py::load_open_commitments(events_jsonl_path)` — do NOT hand-roll an events.jsonl scan. The canonical helper handles closure-suppression (v3.11.4 `data.target_id` defensive id-field), malformed-line tolerance (Sub-bug #14b 2-layer defense), and dual-shape confidence values. If `load_events_defensively()` reports `skipped` lines (substrate corruption), surface a soft banner to the user: "Your activity log has {N} incomplete entries — recovery pending in next update." Do NOT silently filter.

For any name-bearing follow-up ("brief me on Sam's status"), call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` before grep. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use morning-briefing for:** the 60-second daily scan. Calendar + 1-line email summaries + tracker urgency + Slack digest. Designed for scheduled fire at 7:30am weekdays.
- **Use `inbox-triage` for:** the deep email pass — 5-bucket classification (Reply Now / Decision Needed / FYI / Discard / Deep Read) with 2-3 drafted replies. Runs on demand or in sequence after morning-briefing.
- **Pair pattern:** morning-briefing runs first (context), inbox-triage runs second (email action). User can trigger both with "brief me + triage my inbox" or schedule them in sequence.

The email section of morning-briefing is intentionally summary-only — if the user wants drafts, they call inbox-triage.

## Personification Contract (v3.13.8.4+)

Before rendering the briefing, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The briefing chat intro line uses the shape `"Morning, {first_name} — {brain_name} here with today's read."` (default `{brain_name}` = `"Penelope"`); the scheduled-task .docx signature line is `"— {brain_name}"` (already implemented in v3.13.8 scheduled-task orchestrators). Don't over-name — one reference in the intro + one in the signature is the rhythm.

## Writer Contract

This skill reads from Gmail, Calendar, and Slack during its daily scan. Every connector read emits corresponding events to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` — dedup via source_ref hash so running this daily doesn't duplicate events already captured by inbox-triage or workspace-manager. The primary briefing output is never blocked by a capture failure; capture is a side effect.

---

# Morning Briefing — Proactive Daily Digest

Deliver a concise, actionable morning digest before the user starts their day. This skill is designed to run as a **scheduled task** (fires automatically on weekday mornings) but also works as a manual trigger.

The goal: the user reads this in 60 seconds and knows exactly what needs their attention today. No fluff, no comprehensive status — just what changed overnight and what's due.

## When This Runs

| Mode | Trigger | Output |
|------|---------|--------|
| **Scheduled** | Fires via scheduled task (recommended: weekdays 7:30 AM) | Structured digest delivered via Slack DM or email |
| **Manual** | "morning briefing", "daily briefing", "brief me" | Same digest, displayed in chat |

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

### Email (if connected)
- Search for unread or important emails from the last 18 hours
- Filter by: people in PEOPLE.md, project-related subjects, flagged/starred
- Limit to 10 most relevant — summarize each in one line
- Flag: anything that looks like a reply to a "Waiting On" item in the tracker

**Self-reply filter (v3.11.1 — REQUIRED).** The default Gmail query (`in:inbox`) hides messages the user already sent in reply — meaning a thread where M responded an hour ago still shows up as "unread or important from last 18h" and surfaces under Needs Attention as if it's still waiting. For every candidate thread:

1. Fetch the thread's latest message via `get_thread` (Gmail MCP) or equivalent.
2. Compare the latest message's `From:` header to the primary user's email (read from `entities.json` — the `person` record where `is_primary_user: true`).
3. If the latest message in the thread is FROM the primary user, **drop the thread entirely** from Needs Attention and Overnight Inbox. M already handled it; surfacing it as outstanding is wrong.
4. The check must run on the thread's LATEST message, not the message that matched the original query (Gmail may have surfaced an earlier inbound message in a thread the user has since replied to).

If the connector supports it, broaden the initial query to `(in:inbox OR in:sent) -in:draft` and then run step 1-3. This is cheaper than `get_thread` per candidate but produces the same outcome — threads where M is the latest sender get dropped. Pick whichever path the available Gmail tool supports; both are acceptable as long as the latest-sender check is applied.

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

The team overdue / dormancy counts MUST derive from `_hq/data/events.jsonl` via `load_open_commitments`, NOT from each PERSON.md file's commitment table. The PERSON.md commitment table is a Tier 2 projection that lags — reading it directly was the v3.11.5 _people/ drift bug (a commitment closed via meeting-notes Step 5e-bis would still show as open in PERSON.md until workspace-manager re-rendered, and morning-brief surfaced it as overdue against the team member).

Procedure:

1. Load the team roster from `_people/_team-config.md` → list of person ids (resolve names via `aliases.json` if the roster uses display names).
2. Call `load_open_commitments(events.jsonl)` once. Group results by `owner_id`.
3. **Overdue check** — for each team member, count commitments where `_commitment_field(ev, "due")` parses to a past date in workspace TZ (via `tz.py to_local(due, workspace_path=<WORKSPACE>)`). Flag anyone with 3+ overdue.
4. **Dormancy check** — for each team member, find max ts of any `interaction` / `meeting` / `commitment` event in events.jsonl where `event_references_person(ev, person_id)` is true (per `cru_match.event_references_person`, which handles all shape variants). If max ts is >14 days ago, note as dormant.

PERSON.md files are still fine to read for static profile context (role, working style, flags) — just not for the overdue / dormancy counts that drive the surfaced flag list.

### Step 3a: Layer events.jsonl on top of the tracker (v3.11.1 — REQUIRED)

MASTER_TRACKER.md is a **periodic snapshot**, not a live view. It's regenerated when entities or events change, but a workspace that hasn't triggered a regen for 10 days will surface "Last touched May 8" / "quiet since April 25" for projects that had activity today. The 2026-05-20 morning-brief bug report documented exactly this — Command Room Plugin and Desktop App both had events that day but the digest reported them as cold.

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

Concretely, the accepts on the next-day re-fire are: (a) Command Room thread's "Last touched" reflects the most recent event in events.jsonl, not the May 8 stamp from the tracker snapshot, and (b) any thread the tracker says is in "Waiting On" but events.jsonl shows the reply landed gets dropped from the Needs Attention overdue list.

### Step 3a-bis: Read what the reconcile-sent task closed — the brief is a READER, not the reconciler (v3.18.12 — Bug #98-v3)

**The brief no longer fetches sent mail or runs reconciliation.** A dedicated silent task — `reconcile-sent`, fires 6:45 AM, before this brief — does the actual Gmail-Sent fetch, closes commitments the CEO completed by emailing someone directly, advances the cursor, and emits a `sent_reconcile` audit event. The brief just **reads** what that task already wrote.

**Why it moved (Bug #98-v3).** Three attempts to make the brief reconcile (v3.18.9 standalone step, v3.18.11 folded onto the fetch, v3.18.11 inbox-triage backstop) were ALL skipped in real use. The brief diagnosed the cause itself: every step it reliably runs feeds the *visible* brief, but reconciliation's product is an *invisible* substrate write, so under pressure to ship a readable brief the invisible-payoff step gets degraded every time — a structural incentive problem, not a willpower one. Co-locating an invisible write with a visible deliverable always loses (and it created a read-after-own-write hazard: closing commitments and rendering them in the same fire). So reconciliation moved to its own single-purpose task where it IS the job. The brief's role here is two things only:

1. **Surface what was already closed (read, don't do).** Read `events.jsonl` for `sent_reconcile` audit events + `commitment_resolved` events with `data.resolved_by == "sent_reconcile"` from this morning (since the reconcile-sent task's fire). If any closed, add ONE tail line: *"Closed N follow-ups you'd already sent — [titles]. Say `undo` to reopen any."* Do NOT fetch sent mail or run the reconciliation matcher yourself — that's the reconcile-sent task's job; you are reporting its result, not producing it.

2. **The deterministic soften floor (your job, and it's reliable because it's cheap + computed).** `compute_brief_state` (Step 3d) takes `sent_reconcile_cursor` and returns `reconcile_stale` (True when the cursor is absent or >1 day old) plus a per-item `reconcile_stale` flag. **When `reconcile_stale` is True, you MUST soften every you-owe / "ball is on you" item** — render it as *"you may have already handled this — sent-mail reconciliation is behind"* rather than "reply to / send / follow up with". In normal operation the 6:45 task advances the cursor before you run, so `reconcile_stale` is False and nothing softens. If that task didn't fire (cursor stale), the floor catches it and you still never send the CEO to redo done work. This is the protection that held across all three earlier failures.

**Why this finally works.** Enforcement is on the EVENT, not a narration: the reconcile-sent task's success is a `sent_reconcile` audit event a validator reads back from `events.jsonl` (a cursor delta backed by a scan count can't be faked the way a sentence can — the v3.18.9 receipt gate WAS gamed by feeding the matcher curated data + printing a truthful line). The brief can't fake "closed N" either — it reads the real `commitment_resolved` events or it has nothing to report.

### Step 3b: Aggregate commitments from events.jsonl (v2.7.15+, v3.4.5+ shape-aware)

Scan `_hq/data/events.jsonl` for `type: commitment` events that haven't been closed by a later `commitment_resolved` / `thread_resolved` event.

**Mark-done affordance (v3.18.3+ — Bug #85).** Every item surfaced under "Needs Attention" carries a one-tap **`mark done [n]`** action (routes to apply-choices' existing `commitment_resolved` writer). This is the manual close path the 7-day stopgap below was waiting on — the CEO closes a stale "you owe" item in one tap instead of seeing it re-surface daily. Pair it with the auto-close from Step 3a-bis: the system closes what it can prove from Sent mail, and `mark done` covers the rest.

**Prospect-conversion nudge (v3.18.7+ — Bug #92, detect-and-nudge — CONDITIONAL, cheap).** Call `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)` (a fast substrate-only read — no connector fetch, unlike Step 3a-bis). This is the same detector the coach and weekly cleanup use; surfacing it in the daily brief is the highest-visibility nudge. **NEVER auto-flip `relationship_type`** — only surface the suggestion; the CEO runs the Bug #91 `[Name] is now a client` conversion. If the detector returns no candidates, emit nothing (this is a conditional line, NOT a mandatory one — contrast Step 3a-bis's required status line).

**Render EVERY candidate verbatim — do NOT second-guess the detector (v3.18.9+ — Bug #92b).** Each candidate carries a ready-to-render `render_line`. Add that line, exactly as returned, to Needs attention for **every** candidate the detector returns. You MUST NOT apply your own judgment about whether a prospect's project "looks paused", "isn't really active", or "isn't worth surfacing" — the detector already decided who qualifies (it owns the active/archived/paused call), and a surface that drops a candidate on its own discretion is the #92b regression: a prior brief silently dropped a true HIGH candidate (a partnership client still mis-tagged as a prospect) on a "paused project" judgment while the cleanup surface correctly rendered it. If the detector returns 3 candidates, exactly 3 `🔄` lines appear. The detector is the source of truth for inclusion; your only job is to render its lines.

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

Counts come from `brief_state.compute_brief_state(...).counts` (skip pending-review shape — those go to the Pulse CRU-review surface, not the morning count):
- **You owe:** `owner_id == <user_id>`
- **They owe:** `owner_id` non-empty and `!= <user_id>`
- **Unassigned:** `owner_id` null/missing — an extraction gap, but still an open commitment
- **Stuck (overdue):** `due` parses to a past date (re-evaluated at read time)

**Canonical-total parity (v3.18.5+, Bug #85 A85-followup — MANDATORY).** The header total the brief reports MUST equal `counts.total` (= `you_owe + they_owe + unowned` = `len(load_open_commitments(...))`) — the SAME number the coach reports. Do NOT report `you_owe + they_owe` as the total: that silently drops ownerless commitments and was the v3.18.4 16-vs-18 split. Surface the unassigned items rather than hiding them (e.g. "13 you owe · 3 they owe · 2 unassigned" → 18 total). The coach reports `len(load_open_commitments)`; this reconciliation is what makes the two agree.

**7-day activity stopgap (v3.11.1 — REQUIRED for Needs Attention overdue surfacing).** Commitments accumulate as "open" in events.jsonl because no `commitment_resolved` event fires when the work actually completes. As of 2026-05-20, M's workspace had 191 open commitments — many already done but never closed. Until the full B4 fix lands (meeting-notes / follow-up-ritual emitting `commitment_resolved` + a documented manual close path), the morning-brief "Needs Attention" overdue list MUST filter out commitments whose linked thread has had activity in the last 7 days:

1. For each commitment that would otherwise be surfaced as Stuck/overdue under Needs Attention, look up the linked thread (`primary_thread_id`).
2. Find the max `ts` across all events in events.jsonl where `primary_thread_id == <thread>` (any type — `interaction`, `meeting`, `commitment`, `commitment_resolved`, `intel_logged`, etc.). 
3. If that max `ts` is within the last 7 days, **drop the commitment** from Needs Attention — the work is probably done, just not formally closed, and surfacing it as overdue is noise.
4. The header counts (you owe / they owe / unassigned / total) STILL count all open commitments — the canonical `counts.total` is unaffected by this filter; only the surfaced Needs Attention items are filtered. The header preserves the true workspace state (and equals the coach's count); the surfaced list is the actionable subset.

This stopgap is removed when meeting-notes and follow-up-ritual reliably emit `commitment_resolved` for fulfilled items (planned for the B4 full fix).

If the workspace has **zero commitment events** but ≥3 meeting events on file, surface a one-line nudge in the briefing tail: `"💡 I don't have any commitments tracked yet, even though you've had N meetings — say 'scan for commitments' and I'll pull them out of your past meetings."` This is the discoverability hook for `scan-for-commitments` — most users won't know it exists otherwise.

### Step 3c: Latest-sender re-verification on EVERY "ball is on you" item (v3.13.7+ — MUST-language enforcement gate)

> **For every item the digest would surface as "ball is on you" — fresh inbox scan items, carried-over items from earlier same-day briefs (morning → evening), commitments-derived items from Step 3b — you MUST call `get_thread(threadId, messageFormat="FULL_CONTENT")` against the linked thread and read the LATEST message's `From:` header. If the latest message in the thread is FROM the primary user (M), DROP THE ITEM. The ball is not on the user; they already replied.**

No exceptions for "we already checked this in Step 2" or "this is cached state from an earlier fire." Every "needs your reply" / "propose times" / "ball is on you" surface fires the latest-sender check at digest-build time. This is the structural defense Session-22 Bug #2 documented as missing:

- Morning brief: surfaced "Bo Sample — propose demo times" (substrate said open commitment; Gmail showed Bo's inbound)
- Mid-day: user replied to Bo
- Evening brief: re-surfaced the same item as "ball is on you"
- Reason: the substrate's open-commitment record didn't auto-close on the outbound; the new fire treated the original inbound as still-active without re-reading the thread

The fix is unconditional re-verification:

1. Build the candidate set for "ball is on you" (fresh inbox + carry-over from Step 3b commitments + going-quiet items where the user owes the reply).
2. For each candidate, `get_thread(threadId, messageFormat="FULL_CONTENT")`. Read the latest message's `From:` field. **Use a real thread-id, not a message-id** — Gmail's `get_thread` wants the `threadId`; passing a message-id errors. A search result row gives you both, so pass `thread.id` / `threadId`, never the `message.id`.
3. If `From == primary_user.email`, drop the item. Surface nothing for that thread under "ball is on you."
4. **Fail CLOSED on any `get_thread` error — never infer the latest sender from a search snippet (Bug #93, sub-cause c).** If `get_thread` errors for a candidate (wrong id type, API failure, thread not found), you have NOT confirmed the ball is on the user. Do one retry with the corrected `threadId`; if it still fails, **drop the item** rather than surfacing it on a guess. The live failure: `get_thread` was called with a message-id, errored, the error was swallowed, and the brief inferred "no reply yet" from a stale search snippet — so it told the CEO to reply to a thread they'd already answered (the reply had even bounced). A snippet is the message Gmail matched, not the thread's latest message; inferring latest-sender from it re-introduces exactly the bug Step 3c exists to kill. No confirmation → no surface.
5. The 7-day activity stopgap in Step 3b is COMPATIBLE with this gate (Step 3b can still pre-filter; Step 3c is the final say). When in doubt, Step 3c wins because it reads the actual latest message, not an inferred state.

**`messageFormat="FULL_CONTENT"` is required, not optional.** Lightweight metadata fetches (subject + date only) don't carry the `From:` field reliably across Gmail / Outlook / Granola adapter shims. The full-content fetch is the only path that guarantees the latest-sender field is populated. Worth the extra connector cost — this gate fires once per surfaced item, typically 5-15 items per brief.

If the connector supports a single batched `get_thread` for multiple threadIds, prefer that. Otherwise per-candidate calls. Don't skip the check to save calls; the trust cost of one false "ball is on you" surface dwarfs the connector cost of 15 thread fetches.

### Step 3c-bis: Calendar-action re-verification on scheduling "ball is on you" items (v3.14.7+ — MUST-language enforcement gate)

> **The Step 3c latest-sender check only recognizes an EMAIL reply as "the user handled it." A scheduling thread almost always closes on the CALENDAR, not in the inbox — the user replies by creating an invite, so the thread's latest message is still the counter-party's and Step 3c keeps the item surfaced. For every candidate that would surface as "reply to X to lock/propose/confirm a time", "set up the call with X", or any scheduling-flavored "ball is on you" item, you MUST also check the calendar before surfacing. If a calendar event exists with that counter-party that the user organized OR the counter-party has accepted, AND it was created/updated at or after the counter-party's last inbound message, DROP THE ITEM — the loop is closed on the calendar.**

This is the surfacing-layer twin of the Path 5 fix (`shared/scripts/cru_match.py::match_calendar_to_commitments`, daily backstop in `orchestrator-commitments.md` Phase 2.7). Path 5 closes the substrate commitment on the daily Commitments fire; this gate stops the morning brief from surfacing a stale "reply to X" item in the window before that fire runs — and catches inbox-derived items that were never a tracked commitment at all (the live scheduling-close bug, 2026-05-29: the user created a Monday 8 AM invite at 8:29 AM, the counter-party accepted at 11:06 AM, and the ~11 AM brief still said "reply to Bo to lock Monday" because the counter-party's "Monday is fine" email was the thread's latest message).

Procedure (runs only for scheduling-flavored candidates — detect via `cru_match.detect_scheduling_intent` on the item text, or the obvious surface phrasing "lock / propose times / set up the call / find time / confirm the time / put on the calendar"):

1. Resolve the counter-party on the thread/item to a `person_id` (+ their email via `entities.json` / `aliases.json`).
2. Query the calendar (native Calendar MCP `list_events` — never Zapier, per `EMAIL_DRAFT_PROTOCOL.md` §3c) for events involving that person from ~7 days ago through ~30 days ahead. **This is a DEDICATED wide fetch — do NOT reuse Step 2's today/tomorrow display pull (Bug #93, sub-cause b).** The whole point of this gate is to catch a meeting booked days out; a 2-day window can't see a June-4 invite on a May-31 brief, so the booked item looks unbooked and gets surfaced as "you still owe this". Issue the wide `list_events` (timeMin ≈ now−7d, timeMax ≈ now+30d) before deciding any scheduling item surfaces. If you already pulled a wide window this fire, reuse that — but never the narrow display pull.
3. **Drop the item** if any matching event meets either bar:
   - the user is the organizer/creator and the event was created/updated at or after the counter-party's last inbound message on the thread, OR
   - the counter-party's `responseStatus == "accepted"`.
4. If no such event exists, keep the item — the ball really is on the user.

Step 3c (email latest-sender) and Step 3c-bis (calendar) are both final-say drops: an item surfaces only if it survives BOTH. When in doubt, drop — a missed "you already did this" is far cheaper to trust than a false "you still owe this." This also means a counter-party invite-acceptance (e.g. "Lyra accepted the call") is treated as a close signal here, not discarded as inbox calendar-noise.

### Step 3d: Compute the surfaced state deterministically (v3.14.8+ — MANDATORY)

Steps 3b/3c/3c-bis describe the rules; **`compute_brief_state` is the code that applies them.** Do not re-implement the open/overdue counting or the three drops by hand — gather the inputs and call the function. It is the same-inputs-same-output guarantee that stops this logic from drifting fire to fire.

Gather (this is the connector work — only the FETCH is yours, not the decisions):
- `opens` = `load_open_commitments(events_jsonl_path)` (Step 3b).
- `threads` = for each linked thread you expanded in Step 3c, `{thread_id: {"latest_sender_is_user": <bool from the get_thread latest-message From: check>}}`.
- `calendar_events` = the native-Calendar `list_events` results from Step 3c-bis, each resolved to `{attendee_person_ids, summary, created_ts, accepted_by, calendar_event_id}` (attendee emails → person_ids via `aliases.json`/`entities.json`).
- `thread_activity` = `{thread_id: <max ts of any event on that thread>}` from the Step 3b events.jsonl scan (drives the 7-day stopgap).

```python
import sys
sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from brief_state import compute_brief_state

from brief_state import compute_and_log_brief_state
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
)
# Render from state — do NOT recompute:
#   state["counts"]  → the "Commitments: Y you owe · Z they owe · S stuck" line
#   state["needs_attention"] → the "ball is on you" items under Needs Attention
#   state["dropped"] → diagnostic only; never shown to the user (Rule 4/9)
#   state["reconcile_stale"] → Bug #98-v2 floor. If True (cursor absent or >1 day
#     old), reconciliation is behind: render EVERY needs_attention item softened
#     ("you may have already handled this — sent-mail reconciliation is behind
#     since [cursor date]"), NOT as "reply/send/follow up". Items also carry a
#     per-item reconcile_stale flag. This is the deterministic guarantee that a
#     skipped reconcile (Step 3a-bis) can never tell the CEO to redo done work.
```

Render `state["counts"]` as the commitments line and `state["needs_attention"]` as the "ball is on you" items. `state["dropped"]` is for diagnostics only — it explains why an item was suppressed (`calendar_action` / `email_reply` / `recent_activity`); never surface it in chat. If you can't fetch a given input (connector down), pass what you have — the function degrades gracefully (a missing `threads`/`calendar_events`/`thread_activity` just means that drop isn't applied; the item surfaces, which is the safe direction).

### Step 3e: ONE gated source for EVERY "ball is on you" actionable — including Top 3 moves (v3.18.9+ — MUST-language enforcement gate, Bug #93)

> **Every actionable anywhere in the digest that tells the CEO they owe someone an action — "reply to X", "follow up with Y", "book / lock / propose a time with Z", "send the X to W", "get back to V" — MUST be drawn from the gated candidate set, NOT synthesized freehand from your raw inbox/calendar reads. There are two legitimate sources, and ONLY these two: (1) an item in `state["needs_attention"]` (already survived the 3c/3c-bis/7-day drops), or (2) an inbox-derived item that you have personally run through the Step 3c latest-sender check AND the Step 3c-bis calendar check this fire. If an actionable came from neither path, it does not appear — not in Needs attention, not in Top 3 moves, not in Suggested next steps.**

This closes the #93 trust-killer. The Top-3-moves and Suggested-next-steps sections are written in Step 4 as a *separate synthesis* over the morning's inbox/calendar scan — and that synthesis bypassed `compute_brief_state` entirely, so items the gates had already dropped (a meeting booked days out, a thread the CEO already replied to) reappeared at the very top of the brief as "do this now". The live failures: "book [person]" for a call already on the calendar four days out (the calendar drop never saw it — sub-cause b), and "reply to [person]" on a thread already answered (the reply had bounced; the latest-sender check was skipped via snippet inference — sub-cause c). Telling the CEO to redo finished work is the same trust-killer as the #85 class.

The rule, concretely:
1. **Tracked-commitment actionables** (you owe X per events.jsonl) come ONLY from `state["needs_attention"]`. If `compute_brief_state` dropped it (it's in `state["dropped"]`), it is handled — it may NOT be promoted into Top 3 moves on your own judgment that it "still feels open". The function already applied the calendar / latest-sender / recent-activity drops; second-guessing it is the bug.
2. **Inbox-derived actionables** (a "reply to X" that is NOT a tracked commitment — e.g. an overnight email that needs an answer) are legitimate to surface, but ONLY after you run the SAME two checks the gates apply: Step 3c (`get_thread` latest-sender, fail-closed on error) AND, if it's scheduling-flavored, Step 3c-bis (wide calendar fetch). An inbox-derived "reply to X" where X's thread shows the CEO as latest sender, or where a matching invite is already booked, is dropped exactly like a tracked one.
3. **Non-owing moves are unaffected.** "Read [doc] before the 2pm", "prep for the Acme call", "decide on the pricing" — moves that don't assert the CEO owes a *reply/booking/send* to a counter-party — are normal Top-3-moves and don't go through the drops. The gate governs *ball-is-on-you* actionables specifically.

When in doubt, drop: a missed "you already did this" costs nothing; a false "go redo this" costs trust. This gate is the single chokepoint — there is no second, ungated path to a "ball is on you" line.

## Step 4: Build the Digest

Format the output as a structured, scannable digest. The digest has 5 sections maximum — skip any section that has nothing to report.

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

**v3.13.0+ — top-down layout with synthesis lead, top-3 moves up top, going-quiet promoted, and momentum delta.** Per M's 2026-05-20 feedback #30 ("I would do all of those and have top 3 moves somewhere up top"), the digest opens with the answer to "what should I do" before the lower-priority context. Pre-v3.13.0 the SUGGESTED FIRST MOVE was a single line at the bottom — easy to miss. v3.13.0+ surfaces top-3 moves right after the synthesis lead so M decides his morning in 15 seconds.

```
Morning briefing — [Day, Month DD, YYYY]

[Synthesis lead — one-line theme.] Distill the day in a single sentence:
"Today is gated by the 4:45 negotiation with Acme; everything else is supporting cast."
"Heavy on Acme ops review; the plugin work is the asynchronous backbone."
Match Friday Wrap's lead-paragraph pattern — one anchor moment + theme. Skip
if nothing distinctive (then jump to commitments line).

[Commitments with context, not raw counts.]
Commitments: [Y] you owe (+[delta] since [last_brief_date], [closed_yesterday] closed) · [Z] they owe · [S] stuck
[Inline define "stuck" on first mention:]
("stuck" = no movement in 21+ days OR blocked on a named person)
[Omit the line if all three are 0.]

[Top 3 moves before noon — the answer to "what should I do." This is the most important section. Surface it right after commitments so it's seen in the first 15 seconds.]
Top 3 moves today
1. [action] — [why now / what unlocks]
2. [action] — [why now / what unlocks]
3. [action] — [why now / what unlocks]
[Each move is specific (not "review the Acme thing" — "read [person]'s [artifact] against the [doc]"). Rank by: (a) gates a meeting today, (b) deadline today/tomorrow, (c) highest-revenue dependency.]
[GATE (Bug #93): any move here that is a "ball is on you" actionable — reply / follow up / book / propose times / send / confirm with a counter-party — MUST come from the Step 3e gated set (state["needs_attention"], or an inbox item you ran through the 3c + 3c-bis checks this fire). NEVER promote an item compute_brief_state dropped, and never surface a reply/booking move you have not latest-sender + calendar verified. Non-owing moves (read / prep / decide) are exempt.]

[Momentum delta — what changed since yesterday.]
Since yesterday's brief: [Person A delivered artifact] · [Person B accepted time] · [Person C went 13d quiet] · [Thread revived].
[Skip if it's been ≥3 days since last brief — backfill is harder to summarize crisply.]

[Going quiet — promoted from the old "Other Orgs" buried footer.]
Going quiet — [N relationships]
⚠️ [Person/Org] — [N] days since last contact, usual cadence: [baseline], last topic: [topic]
[List the top 3-5 going-quiet relationships ranked by relationship value × deviation
from cadence baseline. Pre-v3.13.0 these landed at the bottom under "OTHER ORGS"
where the user missed them. Promotion to top-tier per M's feedback #30.]

Today's calendar ([X] events)
• [TIME] — [Event title] ([attendees]) [⚠️ no prep / 🔗 related to Project X / ✓ already wrapped]
[H2 link to the call-prep brief if one was generated, per CONTRACT Rule 3 — clickable, opens in side panel.]

[Week-ahead horizon.]
This week ahead: [Wed/Thu light · 3 demos Friday · Acme contract due Mon].
[One line. Keeps the user oriented past today without dragging the brief long.]

Needs attention
🟡 [Item waiting on user sign-off / etc.] — [context]
⚡ [Aging cluster] — [N] commitments aged past [threshold] in [project]
🔄 [Prospect that looks converted] looks like a client now ([reason]) — say `[Name] is now a client` to convert
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
Category Company [holding]
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

The delivery mechanism depends on the client's configuration. Check for a delivery preference in `CLAUDE.md` or `_hq/BUSINESS_CONTEXT.md` under a "Preferences" or "Briefing Delivery" section.

**If Slack is connected and preferred (default):**
- Send as a Slack DM to the user
- Use the structured format above with markdown formatting
- Keep under 2000 characters (Slack's comfortable reading length)

**If email is preferred:**
- Draft and send via Gmail to the user's own email
- Subject: `Morning briefing — [Day, Month DD]`
- Plain text format, same structure as above

**If no delivery channel is configured:**
- Save to `_hq/briefings/morning-[YYYY-MM-DD].md`
- The user will see it on their next "what's going on" or "let's work"

### Manual mode (user triggered in chat):
- Display the digest directly in chat
- No file save needed (the "what's going on" command handles full briefing saves)

## Tone

Direct and specific, like a calm chief of staff. Lead with the synthesis sentence, not a greeting ("Good morning!" / "Here's what's happening!" — skip those). But the content itself reads as friendly plain English, not engineer status-board ("3 commitments aging past 14 days" is fine; "DRIFT: 3 commitments aged past threshold" is not). Per CONTRACT Rule 4 — no all-caps section headers, no scores, no internal mechanism names.

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
- Does not update MASTER_TRACKER, entities.json, or any other workspace state — this skill is read-only.
- Does not generate insights or pattern analysis — that's `insight-generator`.
- Does not deliver on weekends by default — manual trigger only on weekends.
