# SPEC OBJ1 — Standing Objectives (DRAFT for review)

Status: **DRAFT** — full working build on the `objectives-skill` branch, no
version bump, no release manifest. Every behavior, trigger, and voice choice
below is a proposed default; the complete list of judgment calls is in
§ "Decisions for the maintainer" at the end. Authored in a contractor build
session, 2026-07-17. Built off main `f68ef9d`.

## What this is

A Command-Room-native primitive for the CEO's short list of **standing
objectives** — priorities too big to mark done in a day, spanning weeks,
carrying multiple steps. Deliberately NOT modeled on any external planning
framework: no key results, no scoring, no cascading trees. "Objective" is
the plain English word.

The core design bet: **the CEO makes exactly one deliberate choice per
objective — how it's tracked — and everything else is proposed by the
system and confirmed with a tap.** Progress is then harvested from the
chosen source instead of asking the CEO to report:

| Path | For | Status comes from | Drift means |
|---|---|---|---|
| **meeting** | a team/shared priority already discussed in a standing meeting | the status STATED in that meeting's transcript (harvested by meeting-notes) | not discussed in its forum for N sessions |
| **self** (default) | a personal or judgment-based priority with no paper trail | the owner's own word, asked in ONE weekly batched touch | owner hasn't reported in N cycles (escalates, then asks "still an objective?") |
| **activity** | a priority whose progress shows up in the workspace itself | the linked threads'/deals' own events — "moving"/"quiet", directional only on an unambiguous deal signal | no activity on the linked work in N weeks |

Status honesty is the load-bearing rule: a directional status
(`on_track / at_risk / off_track / blocked`) can come only from a stated
meeting review, the owner's own report, or an unambiguous deal signal.
Everything else renders as "moving" or "quiet since [date]". Nothing
fabricates a directional status — that is the named bug class the writers
and validators reject.

## Data model

**An objective is a thread** with a new `kind: "objective"` (added to the
fixed kind enum in `entities.schema.json`) carrying a nested `objective`
object — the exact v4.8.0 deal pattern (`deal` object on `kind: "deal"`
threads). Owner uses the thread's existing `owner_person_id`. The nested
object (validated by `thread_writer._validate_objective`, the schema floor):

The thread's `canonical_name` is a SHORT NAME (2–4 words, derived at
creation and implicitly confirmed in the ack) — the entity-resolve match
surface for the fragments people actually type ("complete objective
enterprise pilots"); the verbatim statement lives on the objective object
(a full-sentence canonical name cannot fuzzy-match short fragments — a
Gate-15 catch, fixed at creation time).

```
"objective": {
  "statement": "Land three enterprise pilots",   // the CEO's own words; also the topical match surface
  "horizon":   "2026-09-30" | null,              // display + drift context; never fabricates status
  "binding": {                                    // the ONE tracking choice (exactly one per objective, v1)
    "type": "meeting" | "self" | "activity",
    "series_key":   "weekly sales sync",          // meeting only: normalized recurring-meeting title
    "series_match": "title_and_people" | "title_only",  // meeting only: the fingerprint mode
    "series_people": ["person_002", ...],         // meeting only (title_and_people mode)
    "cadence_days": 7,                            // self only (default weekly)
    "entity_ids":  ["project_001", ...],          // activity only: linked threads/deals (never a person)
    "target_note": "deal reaches negotiating"     // activity only, optional, display-only in v1
  },
  "anchor_thread_id": "project_001" | absent,     // optional, any path: the existing work this is about
  "milestones": [{"title": "...", "done": false}], // optional, light; context, never chores
  "outcome": "completed" | "archived" | null,      // terminal; set only by the closure paths
  "outcome_note", "opened_at", "closed_at"
}
```

**There is deliberately NO status field.** Status derives from events at
read time (`objective_math.py`); the validator rejects a stored status by
name. Recency derives from `thread_activity` like everything else.

**The recurring-meeting fingerprint.** The substrate has no calendar
series id — recurrence is recognized by normalized title (+ usual
attendees), the convention already used in three places. `series_match`
has two modes: `title_and_people` (default — disambiguates generic titles
like "1:1") and `title_only` (distinctive unique names like a numbered
leadership call, where attendee churn must not break the match). The
system proposes the mode: title unique in meeting history → `title_only`,
generic → `title_and_people`. One normalizer (`normalize_series_key`) is
shared byte-identically between binding-time and harvest/drift-time. A
renamed meeting surfaces as drift whose suggested move offers one-tap
`rebind` — never a silent stall.

**Why a new kind (vs reusing `initiative`):** every consumer can fence on
the kind in one check (stalled-projects exclusion, the nested-object
guard, surfaces) — the same reason deals got their own kind, with the
same one-time cost (enum edit).

**Why create-with-anchor (vs promoting an existing thread):** cold-start
and "objective about existing work" cases create a NEW objective thread
linked via `anchor_thread_id`; the existing thread's kind is never
mutated. Reversible by plain archive; no consumer surprises.

## Event family (six types, one writer)

All written ONLY by `shared/scripts/objective_state.py` (registered in
`writer_contract_lint.ROUTING_HELPERS`; routes through `thread_writer` +
`event_gate.append_event`; seq/ts auto-stamped in the writer lock).
Registered in `events.schema.json` (the enum home), payload shapes in
`event-payloads.schema.json`, lane table + hard rules in `EVENT_TYPES.md`.

| Event | When | Key payload fields |
|---|---|---|
| `objective_created` | CEO confirms creation | thread_id, statement, binding_type, owner_id, horizon, anchor_thread_id; anchor also rides `related_thread_ids` (the relevance join) |
| `objective_updated` | statement/horizon/milestones/owner change, or a rebind | thread_id + changed fields (binding_type on rebind) |
| `objective_review` | meeting harvest: stated status in the bound forum | thread_id, status, source_ref (idempotency key), context ≤200, meeting_title |
| `objective_report` | the owner's word (weekly touch, widget, or spontaneous) | thread_id, status, note ≤200, reported_by |
| `objective_completed` | done — thread flips `resolved` | thread_id, statement, outcome_note |
| `objective_archived` | no longer an objective (incl. graceful death) — thread flips `archived` | thread_id, statement, outcome_note |

Writer semantics mirror `deal_state`/`commitment_state` doctrine: loud
enum-checked errors that name the fix, idempotent closures (`already_closed`
writes nothing), `record_review` refuses non-meeting-bound objectives and
dedups per meeting `source_ref`, `record_report` is valid on ANY open
objective (the owner's word is always trusted; the weekly touch only ASKS
for self-bound ones).

## Derived status, drift, and the suggested move (`objective_math.py`)

Pure compute (no I/O; `load_objective_inputs` is the one disk assembler,
built on the canonical defensive readers — skipped lines surface as the
standard banner). Per open objective:

- **Status:** freshest stated review/report → directional with its `as_of`
  date. Activity path: an unambiguous deal signal (outcome won/lost →
  on_track/off_track; a stage move within a 14-day lookback → forward
  on_track / backward at_risk) may set directional; otherwise
  "moving"/"quiet since [date]" from linked-entity activity. Stale stated
  statuses keep their date and gain `stale: true` once the binding's
  cadence lapses — never silently repeated as current.
- **Drift (per path, preset-tunable):** meeting = N forum sessions since
  the last review (counted by fingerprint match over meeting events,
  deduped by source_ref); self = N missed cadence cycles (baseline: last
  report, else opened_at); activity = N quiet days on the linked work.
  Defaults: 2 sessions / 2 cycles / 21 days (presets: relaxed 3/3/30,
  aggressive 1/1/14).
- **Graceful death (self path):** at `drift_self_cycles + 2` missed
  cycles the weekly touch stops asking for status and asks "still an
  objective, or has it run its course? (keep / archive)". The morning
  brief suppresses the drift line once the death ask is pending (an
  honest "N waiting on your weekly check-in" instead).
- **Suggested move (every flagged objective, never a bare flag):**
  priority order — an open commitment on the linked/anchor work (the
  concrete next step) → poke the non-CEO owner → raise it in its own
  review meeting → block 30 minutes.
- **Severity ranking:** stated blocked/off_track 4, at_risk 3, drifting
  +2, quiet +1; readout renders worst-first.

## The transcript-attribution approach (the meeting path's make-or-break)

The harvest lives INSIDE meeting-notes (new **Step 5i**) — the objectives
skill never reads transcripts (no parallel scanner, no new cron):

1. **Cheap gate:** `objective_math.forum_objectives(open_objectives,
   title, attendees)` — fingerprint match against open meeting-bound
   objectives. Empty (the overwhelmingly common case) → step over, zero
   cost.
2. **Judgment from the transcript already read:** was the objective
   topically discussed with a status stated or clearly expressed? An
   off-hand name-drop does not qualify. Map to the four-value enum.
3. **Write through the single writer:** `record_review` (idempotent per
   meeting source_ref — reprocessing is a NO-OP). Not discussed, or
   discussed with no clear state → **write nothing**; the absence is the
   drift signal. Commitments/decisions in that discussion stay Steps
   5e/5b's — no double extraction.

`objective_review` is counted by meeting-notes' 9a3 claim audit
(`MEETING_WRITE_TYPES`), so "heard where X stands" in chat must be backed
by the event on disk.

## Relevance capture (signal outside the bound source)

No new scanner and no new attribution machinery: **objectives are
threads, so the existing classification envelope already covers them.**
Every captured event carries `primary_thread_id` / `related_thread_ids` /
`cross_ref_reason` / `classification_confidence`, resolved by the
existing ladder — and the ladder already encodes topic-over-party:
direct markers and alias/statement matches (topical) score in the
auto band (≥0.75); people/org clustering (party overlap) scores 0.70/0.55
— below the auto line — so it can only ever queue a proposal. Rules:

- **Auto-attach requires topical evidence:** an explicit mention of the
  objective (alias/statement match), or the signal already belonging to a
  linked/anchor thread or deal (in which case no attribution is even
  needed — the movement read joins on the linked ids).
- **Party overlap proposes at most** — surfaced in the weekly touch for
  one-tap accept, never auto-attached (a linked stakeholder has plenty of
  unrelated business with the CEO).
- **Context vs status:** attributed signal enriches context and counts as
  movement; it never moves a directional status (that bar belongs to the
  bound source and the owner's word alone).
- **Provenance + reversal:** the envelope's `cross_ref_reason` +
  confidence are the provenance stamp; detach goes through the standard
  `reclassification` event and the weekly review surface, and dismissals
  feed the same suppression learning every capture loop uses.

## Surfacing

- **Morning brief (read-only, FB-20):** ≤2 conditional lines from
  `objective_math.brief_lines`, rendered verbatim (the helper decided;
  the surface renders — the Bug #92b lesson). Line 1: the single worst
  drifting/at-risk objective + its move + the `show my objectives`
  teach-phrase. Line 2: the focus headline. Zero objectives → nothing.
  Never asks, never a widget.
- **Weekly recap section 8c — THE weekly objectives touch (rides Friday
  Wrap; no new scheduled task):** status block (`recap_rows`, worst-first)
  + batched self-report asks (numbered, reply pattern taught inline:
  `objectives: 1 on track, 2 at risk — hiring is the bottleneck`; the
  reply fires the objectives skill's parser → one `record_report` per
  item) + the graceful-death asks + pending proposals. One weekly touch,
  never per-objective pings. **The ordinal contract (Gate-15 catch):**
  the §8c render logs an `objectives` receipt with `due_thread_ids` in
  exact render order; the reply parser maps ordinals against THAT
  receipt, never a fresh recompute (which could silently re-order and
  land a status on the wrong objective); a missing/stale receipt or
  out-of-range ordinal gets one clarifying question, never a guess.
- **The readout (`show my objectives`):** count-line lead, worst-first
  rows in plain words, all-batch widget (`cr-objectives`: report [status]
  / mark complete / archive [reason] / rebind / snooze 14d / skip;
  proposal cards: confirm objective / skip), scan receipt with
  `drifting_thread_ids` (the value-receipt trail), skipped-lines banner.

## Cold start

First readout with zero objectives (and `cold_start_proposals` on):
propose AT MOST 3 candidates mined from what's already on disk — (a)
recurring meeting series (≥4 instances in 30 days, the deliverable-catalog
convention) → meeting-path candidates; (b) most-active threads/deals via
`derive_thread_activity` → activity-path candidates anchored to them; (c)
`THEMES.md` recurring themes when present → self-path candidates. Each is
a full pre-filled card (drafted statement, pre-selected path, proposed
owner) for one-tap confirm/edit/skip. Propose-and-confirm always; skips
stay away 60 days.

## The active set

Soft cap of 7 (`active_cap`, tunable). Creation past the cap succeeds;
the ack proposes parking the lowest-signal objective (archive with reason
"parked" — one tap, reversible by recreating). Focus is a suggestion, not
a wall.

## Trigger table

| Trigger | Does |
|---|---|
| `objectives` / `show my objectives` / `my objectives` / `objectives review` / `what are my objectives` | the ranked readout (or cold start) |
| `new objective ...` / `add an objective` / `objective: [statement]` / `set an objective` | creation flow (toggle → proposed target → confirm) |
| `objectives: [statuses]` | the weekly-touch reply parser |
| `complete objective [name]` / `we hit [objective]` | completion |
| `archive objective [name]` / `drop the objective` | archive with reason |
| `rebind [name]` | re-pick path / fix a renamed meeting (bare `rebind` is claimed as a coined verb) |
| `tune objectives` / `show objectives settings` / `reset objectives to defaults` | FRP1 family |

Fences: `what should I focus on` stays command-room-coach; `stalled
projects` stays stalled-projects; `pipeline`/`deals` stay
pipeline-tracker; `log decision` stays decision-log; `show my list` stays
show-my-list; bare `goal(s)`/`priority`/`priorities`/`focus`/`milestone`/
`OKR`/`key results` deliberately unclaimed. workspace-manager gained a
fence for `archive objective` (it owns bare `archive`). 379/379 trigger
tests green.

## Cross-skill collision map

| Existing path | Relationship |
|---|---|
| meeting-notes / past-meetings | Step 5i added inside its pipeline; commitments/decisions stay owned by 5e/5b; claim audit counts `objective_review`. The scheduled past-meetings twin inherits via the shared builders/contract — **its orchestrator file was NOT edited in this draft** (flagged below). |
| reconcile-sent / session-sweep / intel-intake | Untouched. Their signal reaches objectives only through thread attribution they already do. |
| insight-generator | Theme detection stays its job (cold start reads THEMES once). Objective-targeted provisional classifications listed in the weekly touch; adjudication marks them resolved so Pass 8 never re-asks. Its Pass 7 dormancy probe may probe objective threads — flagged below. |
| stalled-projects | `kind="objective"` excluded in `stall_detector` (the PIPE1 D7 fence shape) — drift is binding-dependent, never double-flagged. |
| pipeline-tracker / deal_state | Objectives READ deal state (the activity path's directional source); never write a deal field. |
| morning-briefing / weekly-recap | Additive blocks only (Step 3b lines; section 8c). No redesign. |
| commitments | A "next step" on an objective is a standard commitment on its anchor/linked thread; suggested moves surface the existing open commitment. No parallel task concept. |
| workspace-manager | Owns thread/org lifecycle as before; `go [objective name]` navigation stays its. One new fence (above). |
| value-receipt | Three display-only metrics (statuses heard / drift flags from receipts / completed); no invented rubric minutes. |

## Where it lives

Core cr1, universal — `command-room/skills/objectives/` + the two shared
helpers. No client-repo coupling, no connector dependencies (everything
reads the substrate). Windows-safe ASCII paths throughout.

## Test list

- `tests/run_objective_state_test.py` — 51 checks: writer validation, all
  three bindings, floor guards (incl. the stored-status rejection), event
  emission through the gate, closure idempotency, malformed-shape
  tolerance.
- `tests/run_objective_math_test.py` — 35 checks: fingerprint matching
  (both modes), forum counting/dedup, per-path status + drift + graceful
  death, deal-signal directional reads, suggested-move priority, ranking,
  due-report batching, render helpers (incl. FB-20 no-ask), end-to-end
  loader smoke. All dates computed relative to today (G14).
- `tests/runtime_exercise_objectives.py` — 78 checks (Gates 13/17): three
  paths end-to-end on a synthetic workspace, harvest-gate idempotency,
  graceful-death arc incl. brief suppression, leak scan over every
  rendered line, corrupt-events-line + malformed-thread degradation,
  writer refusals.
- `tests/eval_prompts_objectives.json` — 6 Gate-15 prompts asserting the
  behavioral contract.
- `tests/triggers.yaml` — 17 new entries incl. ownership guards.
- Pre-existing suites exercised and green: writer_contract_lint,
  source_of_truth, value_receipt (62), receipts (75), trigger (379).

## Decisions for the maintainer

Structural choices the operator approved during the build: (1) new `objective`
thread kind (not reusing `initiative`); (2) the binding as a labeled
sub-object; (3) series fingerprint = normalized title + usual people,
with `title_only` override for distinctive names, auto-proposed.

Everything below is a **drafted default awaiting your call**:

1. **Voice and every trigger phrase** — the whole SKILL.md voice layer,
   the fences, claiming bare `rebind`, the first-run footer copy, all ack
   copy. Drafted in house style; entirely yours to reshape.
2. **The weekly touch rides Friday Wrap** (weekly-recap 8c). Tension
   worth your eyes: the staff-meeting chat is the sanctioned adjudication
   surface (FB-20), but it runs M/W/F and is a later-add; Friday Wrap is
   weekly, first-install, and prose-reply-friendly. I put status asks AND
   proposal adjudication in the Friday touch; if you'd rather route
   proposal adjudication to staff-meeting (splitting the touch), that's a
   ~1-file change.
3. **Drift presets** — standard 2 forum sessions / 2 self cycles / 21
   quiet days; relaxed 3/3/30; aggressive 1/1/14; death at self drift +2.
4. **Active-set soft cap 7**, overflow = offer to park the quietest.
   Hard cap? Different number?
5. **Cold-start mining sources and the ≤3 cap**; 60-day re-propose
   cooldown on skips.
6. **Activity-path directional reads** — deal outcome always directional;
   stage moves directional within a 14-day lookback (forward=on_track,
   backward=at_risk). Numeric-target parsing (`target_note`) is
   display-only in v1 — auto-reading "a number hitting target" needs a
   number source and is deferred (fast follow?).
7. **record_report valid on any binding** (the owner's word is always
   trusted; the touch only ASKS for self-bound). Alternative: refuse
   reports on meeting-bound objectives to keep one source per path.
8. **No promote/adopt path in v1** — an objective about existing work
   anchors to it; the existing thread's kind never mutates.
9. **Owner proposal** — default CEO; a direct report named in the
   statement is proposed via the team-intelligence roster.
10. **Secondary binding** is a fast follow (the sub-object leaves room);
    one primary binding per objective in v1.
11. **View integration deferred** — objective threads appear in
    MASTER_TRACKER as ordinary threads; no dedicated view, no
    MASTER_TRACKER regen-trigger addition, and insight-generator's
    DORMANT grouping / Pass 7 probe were NOT fenced for objective
    threads. If you want objective threads excluded from dormancy
    surfaces (mirroring the stalled-projects fence), that's a small
    follow-up.
12. **The scheduled past-meetings orchestrator** was not edited; Step 5i
    lives in meeting-notes SKILL.md (which the orchestrator executes by
    contract). If you want the 5g text mirrored into
    `orchestrator-past-meetings.md` explicitly, say so.
13. **Pre-existing observation (not this branch's bug):** pipeline-tracker's
    SKILL.md instructs `log_receipt(WS, "pipeline-tracker", ...)`, but
    `receipts.CANONICAL_TASK_IDS` has no such id (stalled-projects got one;
    pipeline didn't) — that call would raise. I registered `objectives`
    properly; the pipeline id looks like a latent gap worth a look.
14. **Value-receipt metrics are display-only** (no invented rubric
    minutes, the documents_produced precedent). If objective statuses
    heard should absorb conservative minutes, name the number.
