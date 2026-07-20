---
name: calendar-writer
description: "Schedule meetings — find mutual availability, draft the invite with a context-aware agenda (open commitments with each attendee plus recent project context), create the calendar event, and optionally auto-arrange call prep before it. Fires on: 'schedule a [length] with [name]', 'set up a 30-min with [name]', 'book time with [name]', 'find time with [name]', 'book a meeting with [name]', 'put [name] on the calendar', 'block 90 min for [topic]', 'set up lunch with [name]', 'put it on my calendar'. Does NOT fire on 'cancel my meeting' or 'reschedule' (out of current scope — new invites only), 'prep me for [meeting]' (call-prep), or 'process meeting' (meeting-notes). Availability rules and agenda sourcing: Routing section in the body."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving the meeting attendee(s) from the trigger phrase, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. For the substrate-aware agenda (open commitments with each attendee), call `shared/scripts/cru_match.py::load_open_commitments` and filter by attendee — do NOT hand-roll an events.jsonl scan — passing the org-scoped rows: `load_open_commitments(events_path, events=org_events)` with `org_events` from `events_io.load_events_org_scoped` (PGUARD2 D2 — the agenda lands in the invite body attendees read). See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Use calendar-writer for:** scheduling a NEW meeting. Finds mutual availability, drafts the invite, creates the event.
- **Use `call-prep` for:** preparing for an EXISTING upcoming meeting.
- **Use `meeting-notes` for:** processing a PAST meeting's transcript.
- **Use `follow-up-ritual` for:** drafting follow-up emails after a meeting.

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `meeting_scheduled` with `{attendee_person_ids[], primary_thread_id, scheduled_at_ts, duration_minutes, source_thread_id, calendar_event_id, agenda_summary, surfaced_commitment_seqs[]}`. `source_thread_id` links the scheduling action to the thread / project it came from. `surfaced_commitment_seqs[]` records which open commitments were folded into the agenda so downstream skills (cr-commitments) can detect "this meeting will likely close commitment X."

**External writes (via MCP connectors):**
- Calendar MCP — creates the actual calendar event. Returns `calendar_event_id` captured in the meeting_scheduled event.
- Gmail MCP (when invite needs an email) — invite goes out via Calendar's native invite mechanism, not a separate email-writer call.

**Reads from:** All `events.jsonl` reads come from ONE org-scoped load — **read via the org-scoped reader, never a raw load** (PGUARD2 D-B — the substrate-aware agenda is written into the invite body, which the attendees read: an external surface): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then filter by `type` at the call site. The reader applies the account-scope mask and drops personal-lane rows by design, so masked-account history can never be quoted into an invite.
- Calendar MCP — for availability across attendees (your calendar + each attendee's if Calendar MCP exposes free-busy).
- `_hq/data/entities.json` — attendee email lookup, relationship history, role, org. If the trigger names a person ambiguously ("Bo"), resolves via `aliases.json`.
- `_hq/data/entities.json` — project context if the meeting topic references a project.
- `_hq/data/events.jsonl` — `type == "commitment"` events with `status == "open"` involving attendees — via the seam, `load_open_commitments(events_path, events=org_events)` (PGUARD2 D2 — never the no-arg owner form here) — so the agenda can surface what's owed in either direction.
- `_hq/data/events.jsonl` — `type == "meeting"` events with the same attendees (from the org-scoped load) to detect usual cadence and propose a time that matches their 1:1 rhythm if applicable.
- `_hq/data/events.jsonl` — `type == "interaction"` events with attendees (from the org-scoped load) to seed the agenda's "since last touch" context.

**Conflict boundary:** sole writer of `meeting_scheduled` events. Does NOT write to entities.json people records (that's people-crm's domain).

---

# calendar-writer

Closes a known v3.5.0 gap: "Reads calendar (morning-brief, call-prep) but doesn't write events." The CEO has been able to ASK "what's on my calendar today" since v1 but couldn't SAY "set up a 30-min with Aria next Tuesday." This skill fixes that, and uses the substrate to make the resulting invite materially better than a generic calendar tool's.

## What It Does

For a request like "set up a 30-min with Aria Sample next Tuesday afternoon":

1. Resolves "Aria Sample" via aliases.json + entities.json
2. Queries Calendar MCP for free-busy on both sides next Tuesday PM
3. Proposes 2-3 windows that work
4. Drafts the invite with substrate-aware agenda: open commitments with Aria, last conversation summary, project context
5. Renders widget — user picks time + edits agenda + sends
6. Creates the calendar event via Calendar MCP
7. Writes `meeting_scheduled` event
8. (Optional) Schedules a call-prep brief generation 24h before the meeting

## How to Use

```
"set up a 30-min with Aria Sample next Tuesday afternoon"
"schedule lunch with Bo next week"
"book time with Rio Friday"
"put Sam on my calendar for 45 min next week"
"block 90 min for the pricing review tomorrow"
"set up the kickoff call with Acme — propose 3 times"
```

If duration isn't specified, default to 30 minutes (or 60 for "lunch" / "deep dive" / "review" / "kickoff").

If timing isn't specified, propose 3 windows in the next 5 business days that fit your usual cadence with that attendee.

## How It Works

### Phase 1 — Parse request

Extract: attendee name(s), duration (default 30 min), time window (default next 5 business days), topic if mentioned.

### Phase 2 — Resolve attendees

For each attendee, resolve via `aliases.json` to a `person_id`. Pull their email from entities.json. If ambiguous, surface disambiguation. If not in entities.json, ask whether to add them via people-crm first.

### Phase 3 — Find availability

Query Calendar MCP for free-busy on your calendar over the requested window. If the attendee has shared free-busy with you (via Calendar MCP's cross-account free-busy if supported), pull theirs too. Otherwise propose times based only on your availability and let the attendee respond.

Score windows by:
- Both-sides clear
- Matches your historical cadence with this attendee (if you usually do Tuesday 2:30 PM 1:1s with them, weight that slot up)
- Avoids back-to-back blocks
- Avoids your usual deep-work hours (configured in BUSINESS_CONTEXT)

Top 3 candidates.

### Phase 4 — Draft agenda from substrate

Build a draft agenda by pulling:
- Open commitments with attendee (both directions, per cr-commitments shape rules)
- Topic if specified in trigger
- "Since last touched" — summary of any interactions since the last meeting with this attendee
- "[open for attendee]" placeholder

### Phase 5 — Render widget for approval (v3.13.0+ — explicit-approval-before-write per CONTRACT Rule 2)

**Output guard (PL.10):** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.

- ❌ "meeting_scheduled event written; agenda rendered from open commitment records"
- ✅ "Invite's on your calendar — the agenda covers the two things you and Aria still owe each other."

**Critical:** the calendar event is NOT created until the user explicitly clicks `send` via apply-choices. Per M's 2026-05-20 feedback #3, calendar-writer must confirm event details and get approval before writing to Google Calendar — no silent writes. The widget surfaces every field the user might want to edit (time, attendees, subject, body) so revisions happen in-place rather than requiring a regenerate.

```python
from widget_transport import render_and_persist

data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"Schedule with {attendee_display_name} — {org_or_topic}",
    "sub_header": f"Found 3 windows that work for both of you. Pick one (the bolded default), or edit before sending.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [{
            "n": 1,
            "icon": "📅",
            "name": attendee_display_name,
            # Email-shaped metadata so edit-then-send opens the multi-field input.
            # Subject = calendar event title; To = invitees; Body = agenda lines.
            "metadata": [
                ["To", ", ".join(attendee_emails)],
                ["Subject", event_title],
                ["Time", recommended_time_iso],
                ["Duration", f"{duration_minutes} min"],
                ["Location", location_or_meet_link],
            ],
            "context_tag": (
                f"recommended {recommended_time_human}; alternates {alternate_times_human}"
            ),
            "body_lines": [
                "Agenda:",
                *(f"> {line}" for line in agenda_lines),
            ],
            "actions": ["1 send", "1 skip"],
        }],
    }],
}
transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="calendar-writer")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport; validators fire inside the call).
# STOP. Wait for the user's apply-choices reply.
```

The card is `send` / `skip` (FB-17 — `edit then send` retired; the popup multi-field editor is gone). The agenda body is directly editable on the card (FB-10). To change the time, attendees, or title before creating, the user says the correction in chat and the card re-renders — never a popup form. The Calendar MCP write doesn't fire until the user confirms via Apply.

### Phase 6 — Create event + write to substrate (ONLY on `1 send` from Apply)

This phase fires ONLY when apply-choices dispatches an `{n: 1, action: "send", ...}` from the user's Apply click (or, from an in-flight pre-FB-17 widget, the deprecated `{action: "edit then send", input: {...}}` alias). Never auto-fire from Phase 5.

On dispatch:
1. If the action carries an `input` object (a deprecated in-flight `edit then send` payload): override the corresponding fields (To/Subject/Body/Time/Duration/Location) before creating.
2. Discover the create tool via `shared/scripts/tool_discovery.py::discover_calendar_tool(tools, operation="create_event")` — works across Google Calendar AND Outlook (never hard-code a platform tool id; per CONTRACT Rule 8 calendar never goes through Zapier). Call the discovered tool with the (possibly edited) fields. Returns `calendar_event_id`.
3. Append `meeting_scheduled` event with `{attendee_person_ids[], primary_thread_id, scheduled_at_ts, duration_minutes, calendar_event_id, agenda_summary, surfaced_commitment_seqs[]}` per atomic_write rules.
4. **CRU real-time leg — auto-resolve the scheduling commitment this event fulfills (v3.14.7+).** Creating the event IS the fulfillment of any "set up the call with X / lock time with X / propose times to X" commitment the user owed. Run `shared/scripts/cru_match.py::match_calendar_to_commitments` with the just-created event so the commitment closes immediately, instead of waiting for the daily Commitments Phase 2.7 backstop:

   ```python
   import sys
   # Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
   sys.path.insert(0, "shared/scripts")
   from cru_match import load_open_commitments, match_calendar_to_commitments, build_pending_review_event
   from commitment_state import close_commitment, CommitmentIdError, PendingReviewError
   from atomic_write import atomic_append_jsonl

   # OWNER-side auto-close matching: the no-arg (raw) form is CORRECT here —
   # closing a commitment is substrate hygiene, not attendee-facing output, and
   # a personal-lane commitment must stay closeable by its calendar event
   # (PGUARD2 D2: only the AGENDA composition reads use the org-scoped
   # events= injection form from the Reads section).
   opens = load_open_commitments("<events.jsonl>")
   results = match_calendar_to_commitments(
       open_commitments=opens,
       user_person_id="<primary user person_id>",
       calendar_events=[{
           "attendee_person_ids": attendee_person_ids,
           "summary": agenda_summary or title,
           "created_ts": "<now ISO>",
           "accepted_by": [],                      # fresh invite, no acceptances yet
           "calendar_event_id": calendar_event_id,
       }],
   )
   # Stage B (F2): auto_resolve → close_commitment(workspace_root, r["commitment_id"],
   #   resolved_by=<user>, evidence=r["evidence"], source_skill="calendar-writer")
   #   — THE closure path (catch CommitmentIdError/PendingReviewError → treat as
   #   not-closed and fall through to the Phase 2.7 backstop). Matching unchanged.
   # pending_review → build_pending_review_event(source_skill="calendar-writer", ...)
   # Capture each auto_resolved commitment's {title, due} so Phase 7 can surface it.
   ```
   **Surface the closure — do NOT stay silent (v3.19.x — FIX1 item 23).** This is a user-initiated action, so a calendar-driven auto-close is a substrate change the user should SEE (unlike the scheduled-CRU silence the inbox/commitments orchestrators keep). In the Phase 7 confirm, add one plain-language line per auto-resolved commitment: `✓ This likely closes: '[commitment title]' (was due [date]). Say `undo` if that's wrong.` Plain language only — never print the `commitment_resolved` event-type name (CONTRACT Rule 4 forbids the event NAME in chat, not the fact that something closed). De-dups with Phase 2.7 by construction (both read `load_open_commitments`, which excludes already-closed commitments).

   **On helper failure, do NOT swallow silently (FIX1 item 23).** If `match_calendar_to_commitments` (or the append) raises, log the error to `CONFLICTS.md` per `shared/RELIABILITY.md` AND add one line to the Phase 7 confirm: `(Couldn't verify whether this closed any open commitments — check tomorrow's Commitments list.)` The Phase 2.7 daily backstop is still the safety net, but the user is told the real-time leg didn't complete instead of being left to assume it did.
5. If the user opted into call-prep auto-fire (via a secondary widget option or follow-up turn), write a scheduled-task entry for call-prep at `scheduled_at_ts - 24h`.

On `1 skip`: abort. Write a `chat_dismissal` event for substrate visibility (so M can see in show-my-list that an invite was drafted but not sent). Do NOT call Calendar MCP.

**The contract — never silently create:**
- ❌ Phase 5 must not call the discovered calendar create tool (event creation is the explicit user-action).
- ❌ Don't pre-create as draft. Calendar drafts aren't a first-class concept the way Gmail Drafts are; the user expects the click to BE the write.
- ❌ Don't fire on free-text confirmation ("yes" / "go" / etc.) without an apply-choices payload. The widget IS the consent surface.

### Phase 7 — Confirm in chat

Render the result inline:
```
Done — scheduled with Aria Sample, 30 min, Tue May 26 at 2:30 PM ET.
  Calendar event: [link]
  I'll have your prep brief ready Mon May 25 at 2:30 PM.
  ✓ This likely closes: 'Set up the build call with Aria' (was due May 20). Say `undo` if that's wrong.
```
(Render the `✓ This likely closes:` line only when Phase 6 Step 4 auto-resolved at least one commitment — one line per closure. If Step 4's helper errored, render the `(Couldn't verify…)` line instead, per Phase 6 Step 4 above.)

## DOES NOT

- Cancel or reschedule existing meetings. Out of scope for v3.8.0 (could be added later as a `--reschedule` mode).
- Send a draft email instead of a calendar invite. The deliverable IS the calendar invite via Calendar MCP — Google sends the invite email natively. No `email-writer` chain.
- Schedule recurring meetings via this surface. One-off meetings only in v3.8.0 (recurring would need a separate trigger like "set up weekly 1:1 with [name]").
- Override busy time without explicit user confirmation. If no mutual-clear window exists, surface "no clear windows; closest matches with conflicts" and let user choose.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Schedule meetings — find mutual availability, draft the invite with substrate-aware agenda (open commitments with attendee + recent project context), create the calendar event, optionally auto-fire call-prep 24h before. Use when the CEO says 'schedule a [length] with [name]', 'set up a [length] with [name]', 'set up a 30-min with [name]', 'book time with [name]', 'find time with [name]', 'book a meeting with [name]', 'put [name] on the calendar', 'add a meeting with [name]', 'block 90 min for [topic]', 'schedule [name] [time]', 'set up lunch with [name]', 'put it on my calendar'. Reads Calendar MCP for availability, entities.json for attendee emails + relationship context, events.jsonl for open commitments with each attendee (so the agenda surfaces what's already on the floor with them). Writes meeting_scheduled events. DOES NOT fire on 'cancel my meeting with' or 'reschedule' (out of scope — no cancel/reschedule surface exists yet; calendar-writer is scoped to NEW invites), 'prep me for' (call-prep — pre-existing meeting), or 'process meeting' (meeting-notes — post-meeting).
