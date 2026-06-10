# INGEST → SUBSTRATE SYNC PROTOCOL (v3.14.6+)

**The contract:** *No meeting transcript or email is ever read on-demand without
its entities being reconciled into the workspace substrate.*

If Command Room loads a transcript or an email body for ANY reason — a topic
search, a "what did X say" lookup, call prep, an ad-hoc thread read — and that
source contains a person, org, or commitment the workspace doesn't know about
yet, that information MUST land in substrate. Reading it and dropping it on the
floor is the bug this protocol exists to make un-shippable.

## Why this exists (the bug class)

Entity extraction (people → `people_writer`, orgs, commitments + decisions via
`meeting-notes`) historically lived ONLY inside the scheduled orchestrators
(`cr-past-meetings` Phase 4 / 4.5b / 4.6, `cr-inbox` inbox pass). Every
on-demand path that also touches a transcript or email — `transcript-search`,
`people-crm`'s 90-day pull, `call-prep`'s "Where We Left Off" — was built
read-only and bypassed that extraction layer.

Result (M, 2026-05-28): *"I pulled a transcript for a certain reason, and it had
an individual, and that person was never actually added as a new individual."*
The meeting had simply never been processed; the only thing that processes it is
the 5 PM scheduled task, which had never seen it. The on-demand pull read the
transcript, showed M what he asked for, and discarded the new person.

This is the same architectural theme as `feedback_verify_consumers_before_ship.md`
and `feedback_enforcement_gates_architectural_theme.md`: a capability exists in
one place (the orchestrator) and the runtime path that should reuse it silently
does without.

## The rule

Any skill that fetches a meeting transcript or an email body on-demand MUST, for
each source it loads, run the **reconcile pass** before it finishes:

1. **Dedup check by `source_ref`.** Compute the source ref the orchestrators use
   — `granola:<meeting_id>` / `fireflies:<id>` for transcripts, the message-id
   hash for emails. Scan `events.jsonl` for a `meeting` event (transcripts) or a
   processed-message marker (emails) carrying that `source_ref` /
   `source_ref_hash`.
   - **Found** → already in substrate. No-op. STOP — do not reprocess.
   - **Not found** → this source has been read but never captured. Continue.

2. **Capture the DATA layer (not the deliverables).** Run the existing
   extraction — invoke `meeting-notes` on a transcript; the inbox extraction pass
   on an email — to emit:
   - the canonical `meeting` event (with `source_ref` for idempotency),
   - `commitment` / `decision` events,
   - new people through `shared/scripts/people_writer.py` (`find_existing_person`
     dedup FIRST, then `create_person` / `person_proposal` per
     `orchestrator-past-meetings.md` Phase 4.5b — NEVER hand-rolled
     `entities.json` writes),
   - new orgs through the workspace-manager org path / `org_proposed`.

   **Do NOT generate the heavyweight deliverables** on an on-demand read: no
   `.docx` brief, no per-attendee follow-up drafts, no chat widget. Those are
   orchestrator-owned outputs. The reconcile pass is the *data substrate* only —
   it makes the people/orgs/commitments exist; it does not produce documents.

3. **Confidence + dedup are unchanged.** Reuse the orchestrator's thresholds:
   high-confidence auto-applies, low-confidence writes a `pending_review` /
   `person_proposal` event that the next Pulse fire surfaces. Honor the
   cross-meeting fusion guardrail and speaker-attribution ambiguity guard from
   `orchestrator-past-meetings.md` — the same transcript text is in hand, so the
   same safety checks apply.

4. **Idempotent by construction.** Because step 1 dedups on `source_ref`, the
   same transcript surfaced by ten different searches captures exactly once. A
   later scheduled `cr-past-meetings` fire that re-encounters it also no-ops via
   the same `source_ref` dedup (Phase 4 "Idempotency note").

5. **Surface it in one plain-English line.** After reconciling, tell the user
   what changed, without jargon or event-type names (CONTRACT Rule 4):
   *"Two of these meetings weren't in your workspace yet — I've captured them,
   including 1 new person (Quinn Sample). Say `past meetings` or check your next
   Pulse to review."* If nothing was unprocessed, say nothing.

## Multi-result reads

For a search that surfaces several transcripts (e.g. `transcript-search` top 5),
run the reconcile pass for each surfaced source that fails the dedup check. The
surfaced set is exactly the set the user is looking at, so capturing those is
proportionate. Do not crawl beyond the surfaced results.

## Exemption clause

A skill is exempt from the reconcile pass ONLY if it provably cannot encounter an
unprocessed source — e.g. it reads exclusively from already-emitted `meeting`
events in `events.jsonl` and never touches a raw connector transcript/email. If a
skill claims exemption, it MUST say so explicitly where it references this
protocol, with the reason. "It's read-only" is NOT a valid exemption — read-only
over the *display* surface is fine, but the entities still have to be reconciled.

## Enforcement

`tests/run_ingest_substrate_sync_test.py` asserts every on-demand
transcript/email reader references this protocol (or states its exemption). A new
on-demand reader that lands without the marker fails the battery. The fix is to
wire the reconcile pass (or document the exemption) — NEVER to add the skill to
an exception list (exception lists are how this bug class recurs).

## See also

- `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`
  — Phase 4 / 4.5b / 4.6: the canonical extraction the reconcile pass reuses.
- `skills/meeting-notes/SKILL.md` — the extraction skill invoked per transcript.
- `shared/scripts/people_writer.py` — canonical person create/dedup.
- `shared/PASSIVE_CAPTURE.md` — the `source_ref` dedup contract.
- `feedback_verify_consumers_before_ship.md` — the parent bug-class memory.
