# Commitment Event Schema (v2.7.15+)

Canonical contract for `type: commitment` events in `_hq/data/events.jsonl`.

Every skill that *produces* commitment events MUST follow this schema. Every skill or aggregator that *reads* commitment events MUST handle both this canonical shape AND the legacy flat shape (pre-v2.7.15) for backward compatibility.

**Why this doc exists:** v2.7.14 shipped commitment views (Workspace Map, Commitments Tracker, People Network thread radar) that all aggregate from `events.jsonl`. As of 2026-04-27, M's events.jsonl had **zero** commitment events despite 11 processed meetings — the extraction pipeline was implicit (the writer contract said "emit commitment events" but the schema and trigger conditions weren't documented). v2.7.15 closes that gap by making the schema explicit and giving every producer skill an unambiguous extraction recipe.

---

## The canonical shape

```json
{
  "seq": 142,
  "ts": "2026-04-27T15:30:00Z",
  "type": "commitment",
  "source_skill": "meeting-notes",
  "primary_thread_id": "project_017",
  "related_thread_ids": [],
  "classification_confidence": 0.92,
  "person_ids": ["person_004", "person_011"],
  "data": {
    "owner_id": "person_004",
    "title": "Send updated pricing deck to Mira",
    "due": "2026-05-02",
    "status": "open",
    "source_event_seq": 138,
    "source_ref": "granola:abc123def456"
  }
}
```

### Field-by-field

**Top-level (standard event envelope):**

| Field | Required | Notes |
|---|---|---|
| `seq` | yes | Reserved by writer helper. Monotonic. |
| `ts` | yes | ISO 8601. The moment the commitment was *made* (meeting start, email send time), not the moment it was logged. |
| `type` | yes | Always `"commitment"`. |
| `source_skill` | yes | Skill that wrote it (e.g., `meeting-notes`, `inbox-triage`, `scan-for-commitments`). |
| `primary_thread_id` | yes | Project this commitment belongs to. Format `project_NNN`. |
| `related_thread_ids` | optional | Other projects touched. Usually empty for commitments. |
| `classification_confidence` | yes | Inherited from the parent meeting/email's classification confidence. |
| `person_ids` | yes | All people involved in the commitment context. At minimum: `[owner_id]`. Include the user too if they're the counter-party. |

**Inside `data` (commitment-specific payload):**

| Field | Required | Notes |
|---|---|---|
| `owner_id` | yes | Canonical `person_NNN` id of who owes the deliverable. If the user owes it, use the user's id (the entity with `is_primary_user: true` or `is_user: true`). |
| `title` | yes | Short verb-phrase describing the deliverable (e.g., "Send updated pricing deck to Mira"). Lowercase verb start. No trailing period. ≤120 chars. |
| `due` | optional | ISO date `"YYYY-MM-DD"` if a due date was stated. Empty string if none. |
| `status` | yes | `"open"` (default) or `"overdue"` (if due date is in the past at write time). Producers should compute `"overdue"` based on `now - due > 0`. |
| `source_event_seq` | yes | The `seq` of the parent event (meeting / interaction / etc.) that this commitment was extracted from. Lets readers trace back to the source. |
| `source_ref` | optional | Connector-scoped id of the underlying artifact: `granola:<meeting_id>`, `gmail:<message_id>`, `slack:<permalink>`. Used by `scan-for-commitments` for dedup on re-scan. |
| `origin` | REQUIRED AT CAPTURE (connector-agnostic-v1, ACCOUNT_SCOPE §4a) | `"connector"` when the commitment was extracted from a connector read (inbox/sent/Slack/meeting scan — the code builders `sent_capture`/`slack_capture`/`meeting_capture`/`capture_gate` promote stamp it automatically); `"user_stated"` when the user stated it in chat ("I'll do X", "remind me to…"). Drives the account-scope wall: connector-origin is STRICT (provenance required + scope-checked); user_stated is exempt. Absent = legacy (provider-sniff scope check + a stderr warning) — new producers MUST stamp it. |

### Optional fields inside `data`

| Field | When to include | Notes |
|---|---|---|
| `id` | ALWAYS present after append (Phase 1, 2026-07) | The append gate mints `cmt_<ulid>` at write time when the producer omits it — ids are written, never synthesized-only. Producers MAY still set an explicit id (it is respected). Historic events without one keep resolving via the legacy synthesized `commitment_seq_<seq>` read-side alias. `commitment_resolved` events point back at this value verbatim. |
| `kind` | REQUIRED AT CAPTURE (Stage D, 2026-07 — was gate-stamped from Phase 1) | Required discriminator, one of `promise` / `task` / `scheduling` / `agenda` (ratified 2026-07-01). **Producers classify at write time:** counterparty determinable → `promise`; self-owed, no counterparty → `task`; scheduling intent → `scheduling`; discuss items stay the separate `commitment_to_discuss` type; ambiguous → `promise` + `pending_review: true`. The strict `append_event()` path REJECTS a missing kind; the legacy burn-in path warns loudly + stamps `promise`. Historic events with no kind read as `promise` forever. **The kind POLICY is code-enforced:** `task` never enters CRU matching (`cru_match.cru_eligible`), tasks auto-stale at 30 days into the Friday triage (`commitment_state.stale_tasks`), never render in commitment aging, and never get chased. Promote/demote is an additive `commitment_reclassified` marker (`commitment_state.promote_task_to_commitment`) — a label change the projector applies read-side; never delete/recreate. |
| `no_due` | when the extraction genuinely proposes no due date | Boolean (S2 due-date nudge). Every extraction proposes a `due` OR sets this explicitly — silence is not an option. Undated items surface in the weekly triage, not the aging view; target undated share < 30%. |
| `urgency` | optional flag from extractor | `"high"` / `"normal"` / `"low"`. Currently consumed only by `morning-briefing`. |
| `evidence` | when extracted from text | Raw quoted phrase (≤200 chars) — useful for "why did you log this?" debugging. Do NOT store full transcripts. |
| `meeting_date` | when extracted from a meeting transcript | ISO date `"YYYY-MM-DD"` — the date the source meeting occurred (NOT the date the commitment was logged). Use ONLY as a derivation hint for resolving relative phrases ("tomorrow", "by Friday") at extraction time. **Authoritative due-date is `data.due`. Readers MUST compute "overdue/today/upcoming" against `data.due`, never against `data.meeting_date`.** |
| `owner_external` | when the owner is named but has no entity record yet (`owner_id` is null) | Free-text name string (e.g., `"Bowie"`) so the surface skill can render "owed to you by Bowie — add as contact." When `owner_id` is non-null this field is omitted. v2.14.19+: enables the reachability-filter surfacing pattern. |
| `counterparty_id` | REQUIRED when determinable (Stage E 2026-07, F5 — extraction receipts) | Canonical `person_NNN` of who the deliverable is owed TO (or, for owed-to-you items, who owes it). Also included in `person_ids`. Feeds the CRU candidacy gate DIRECTLY (`match_send_to_commitments`) — without it the matcher leans on the title-token fallback and misses real completions (the Bug #103 recall class; live yield was 4 closes / 644 scanned). **Retires `requester_id` / `requester_person_id` for NEW writes** — readers keep the `_COMMITMENT_FIELD_ALIASES` chain forever, so the 228 historic requester_* events stay readable. |
| `counterparty_name` | SHOULD set when the counterparty is named but resolves to no person record | Free-text name (e.g., `"Bowie"`, `"Lyra Stone"`). The matcher's candidacy gate matches recipient display names / email local-parts against it (Stage E), so a receipt survives even when entity resolution couldn't land an id. |
| `counterparty_ids` | ONE commitment owed to MULTIPLE people (v4.6.0 MC1) — set ONLY when the source explicitly names 2+ recipients ("send the deck to **the board**") | Ordered list of RESOLVED counterparty `person_NNN` ids, primary first. When set, `counterparty_id` is ALSO set to element 0 (the legacy scalar stays valid forever, so any reader degrades to the first counterparty). **No history migration** — single-counterparty commitments never carry this key. Every consumer unions `{counterparty_id} ∪ counterparty_ids` via `shared/scripts/commitment_parties.py` (the ONE reader — `counterparty_ids()` / `primary_counterparty_id()` / `outstanding_counterparties()`). Each id is also in `person_ids`. Build with `commitment_parties.build_counterparty_fields` (keeps single-counterparty output byte-identical). |
| `counterparty_names` | multi-counterparty items with 2+ UNRESOLVED counterparties (v4.6.0 MC1) | List of free-text names, DISJOINT from `counterparty_ids` (a resolved counterparty carries an id, not a name). Same reader union as above. |
| `received_from` / `received_from_names` | NEVER written on a `commitment` event — accumulated on the PROJECTION by the loader from `commitment_partial_received` events (v4.6.0 MC1) | Resolved ids / free-text names of counterparties who have delivered. When every counterparty is in, the loader stamps `all_counterparties_received: true` (derived, in-memory) — the PROPOSE-closure signal (never an auto-close). |
| `pending_review` | when the commitment was extracted by a low-confidence pass | Boolean. If true, surface skill should flag for explicit user review. Bypasses auto-chase paths until confirmed. |
| `review_reason` | when `pending_review` is true | One-line plain-English explanation of why review is needed (e.g., "Owner is external person — no entity record yet"). Used by Pulse and Commitments surfaces to compose review prompts. |

---

## Extraction triggers (when to write a commitment event)

A commitment exists when a person makes a forward-looking promise about a specific deliverable, and a reasonable third party reading the source would identify a clear owner. Producer skills MUST emit a commitment event when ALL of the following are true — **this is the capture floor (Stage D 2026-07: clear owner + clear deliverable + real consequence — the teachable rule that cut one live open set 71→33; below-floor items bury real promises):**

1. **Forward-looking** — the deliverable is in the future, not a description of past action.
2. **Specific** — there's a concrete artifact, decision, or action ("send the deck", "decide on pricing by Friday", "introduce me to your CFO"). Vague intentions ("we should think about that", "let's circle back") DO NOT qualify.
3. **Owned** — there's an identifiable person taking it on. If the source uses "we" or "the team" without naming an individual, do NOT emit a commitment.
4. **Consequential** — someone is waiting on it, a date depends on it, or dropping it costs something. Musings with an owner and a deliverable but no stakes are noise.
5. **Not duplicate** — `(source_ref, title)` is unique. If the same source has been processed before and produced an equivalent commitment, skip.
6. **Not suppressed** — if `_hq/config/commitment-rules.md` exists, producers read it BEFORE writing and skip items matching a user-taught `never-track` pattern (appended by the triage surface's `never track this` action).

### Linguistic patterns that DO qualify

- "I'll [verb] [thing] by [date]"
- "Can you [verb] [thing]?" → "Yes / sure / will do"
- "[Name] is going to [verb] [thing]"
- "I owe you [thing]"
- "I'll get back to you with [thing]"
- "Action item: [name] — [verb] [thing] by [date]"
- "[Name] to [verb] [thing]"

### Linguistic patterns that DO NOT qualify

- "We should consider X" (no owner, vague)
- "I want to think about X" (no deliverable)
- "Let's circle back" (no specific deliverable)
- "Maybe we could X" (hypothetical)
- "X would be nice" (wishful)

### Status assignment at write time

```
if due and parse_date(due) < today_utc():
    status = "overdue"
else:
    status = "open"
```

Producers do NOT need to update status over time; that's a re-scan/aggregation concern. Specifically: `_aggregate_commitments` re-evaluates overdue at read time using the `due` field, so a commitment written `"open"` with a past due date will surface as overdue in views automatically. The `status` field on the event is the producer's snapshot at write time.

---

## Observed tier — the capture relevance gate (v4.6.1 W4c)

**Principle: capture everything, gate only surfacing.** No setting loses data;
the observed tier is the full record, so the line is movable retroactively —
that is what makes user customization of the gate safe.

**The gate (runs at capture, after the Stage-D/S2/Stage-E block — one shared
implementation, `shared/scripts/capture_gate.py`):** an extracted item enters
the ledger as an OPEN `commitment` only if the workspace owner is a party
(owes it or is owed it — owner/counterparty id match, or a confident name
match; a `task`/`scheduling`/`agenda` item with no party fields is presumed
self-owed). Everything else is stored as a `commitment_observed` event
(`data.tier: "observed"`): third-party↔third-party items, and **amber** items
whose attribution can't be confidently resolved — amber is SILENT by default,
not ask. Observed items are searchable, feed prep context, and are promotable
— but create **no open item, no count, no triage row, no confirm-section
row** (a dedicated event type keeps them out of every open-set reader by
construction; see `EVENT_TYPES.md`).

**Modes (customize layer — SCL1 directives under `_hq/custom/`, the
`scan-for-commitments` policy file governs every capture writer; read fresh
at capture time via `capture_gate.resolve_capture_mode` /
`workspace_capture_context`):**

| Mode | Behavior |
|---|---|
| `party-only` (DEFAULT) | only items where the owner is a party open |
| `team-delegation` | also open items a team member commits to (people in the workspace's own org — `relationship_type: "self"`) |
| `track-everything` | pre-W4c behavior; everything opens |
| `observed-only` (org-override value) | keep everything from that org on file without asking |

Directive grammar: `capture mode: <mode>` (global) · `for <org name or id>:
<mode>` (per-org override — routed via the capture source's RESOLVED org;
overrides beat the global mode). Outcome language only, never numeric
thresholds. Fail-open: when the primary user can't be resolved, the gate is
inert (track-everything) — a broken entities file must never silently swallow
real commitments.

**Asymmetric caution rail (beats every mode and override):** an item carrying
a due date (parseable `data.due`) or a money amount (currency symbol + number,
or number + currency word — deliberately conservative; bare "5k" doesn't
match) ALWAYS surfaces as open. Enforced twice: `classify_capture` routes it
open, and `build_observed_event` refuses to store it observed.

**Corroboration (the checkable promotion rule — never vibes):** an observed
item is promoted into the confirm flow when a LATER event from a DIFFERENT
source (`commitment`/`interaction`/`meeting`/`note`, distinct non-empty
`source_ref`, later timestamp) BOTH (i) shares a party — a person id, or a
≥3-char name token — AND (ii) overlaps its content — stopword-stripped
title-token Jaccard ≥ 0.5, or ≥ 3 shared content tokens
(`capture_gate.corroborates` / `find_corroborations`). An explicit user
reference ("track that", or the track-it affordance on a prep render)
promotes unconditionally. Promotion = `capture_gate.promote_observed`:
appends a REAL `commitment` with `data.pending_review: true`,
`data.promoted_from: <obs id>`, and a review_reason — the daily confirm
section picks it up purely by data contract. Idempotent; the observed event
stays in history (append-only, no rewrite). Prep context
(`prep_context_observed`) SURFACES observed items for meetings with those
parties but never auto-promotes — a weekly recurring meeting must not
re-create the confirm-row volume the gate removed.

**Expiry (HYG1 — derive-on-read, no scheduler):** an observed item that is
older than `capture_gate.OBSERVED_EXPIRY_DAYS` (30) and was never promoted is
EXPIRED: it stops counting in `observed_counts` (which reports it under a
separate `expired` field for the audit line), stops corroborating
(`find_corroborations` skips it — a stale observation must not promote off a
fresh event), refuses promotion (`promote_observed` returns not-ok with the
plain reason; re-capture from a current mention if it's still real), and
never resurfaces in prep context. Promotion is permanent — an item promoted
while live is unaffected by its observed source aging. The event itself is
NEVER deleted (append-only doctrine); expired items stay in the log and stay
searchable via transcript-search.

**Audit affordance:** the weekly cleanup note carries one line — "N items set
aside this week — review" — backed by `capture_gate.observed_counts(root,
since_ts=<7 days ago>)`, which counts LIVE items only (expired items report
separately and never inflate the sentence). A filter whose rejects are
cheaply inspectable is a filter the user can trust.

**Verb-driven tuning (consent, never silent):** Not-mine/Drop signals already
in the log (`commitment_resolved` with a dropped/not-mine resolution,
`commitment_reassigned`, `chat_dismissal`) are mined per counterparty org by
`capture_gate.propose_gate_directives` (floors: ≥5 captured, ≥70% dismissed;
cap 3). **The weekly insights pass consumes these as PROPOSALS** ("You set
aside 12 of the last 15 things I captured about [vendor] — want me to keep
those on file without asking?") riding the existing confirm/edit/skip widget;
an approval calls `capture_gate.apply_gate_proposal` — ONE tap writes ONE
per-org `observed-only` directive through `skill_custom_writer.add_directive`
(origin `learned`); a decline lands the fingerprint (`cgd_<hash>`) in the
proposal ledger's 60-day cooldown. The gate NEVER adjusts itself — a proposal
the user didn't approve changes nothing.

---

## Resolution (how commitments close)

**THE closure path (Phase 2 Stage B, F2):** every closer writes through `shared/scripts/commitment_state.py::close_commitment(workspace_root, commitment_id, *, resolved_by, evidence, source_skill, resolution="done"|"dropped"|"superseded", user_confirmed=...)`. It normalizes legacy id spellings (bare int `86`, `seq_86`, `event_086`, `commitment_seq_86` → canonical via seq lookup), raises `CommitmentIdError` when nothing matches (no orphan tombstones — 74 existed in the live substrate), is idempotent over the FULL resolved-id set, never auto-resolves a `pending_review` item (`PendingReviewError` unless `user_confirmed=True` from an explicit user action), and appends through the Phase 1 gate. Matching logic (cru_match Paths 1–5) stays with the callers; only the write is centralized. Migrated closers: log-resolution, apply-choices, the workspace-manager catch-all, reconcile-sent, the Commitments orchestrator (2.5/2.6/2.7 + resolved/mark-received verbs), orchestrator-inbox 5.5, orchestrator-past-meetings, calendar-writer 3.5, meeting-notes, follow-up-ritual.

A commitment closes when a `commitment_resolved` event references it:

```json
{
  "seq": 167,
  "ts": "2026-05-02T18:00:00Z",
  "type": "commitment_resolved",
  "source_skill": "follow-up-ritual",
  "primary_thread_id": "project_017",
  "data": {
    "commitment_id": "commitment_seq_142",
    "resolved_by": "person_004",
    "evidence": "Mira replied confirming receipt of the deck"
  }
}
```

The aggregator removes the commitment from open queues when it sees a `commitment_resolved` event whose `data.commitment_id` matches an open commitment's `data.id` (or the synthesized `commitment_seq_<seq>` if no explicit id was set). **Read-side amnesty (Phase 2 Stage C, F3):** the closer chain also honors the seq aliases `data.commitment_seq` and `data.source_event_seq` — both map seq → the commitment event at that seq — recovering ~252 of the 289 historic dead-letter closures with no history rewrite. `close_commitment`'s idempotency chain mirrors this exactly. What the read chain cannot recover is repaired ADDITIVELY, once, by `shared/scripts/repair_commitment_closures.py` (preview-by-default; snapshots to `_archive/` before any write; `source_skill: closure-repair-2026-07`; run supervised at dogfood time only).

**In-place mutation is FORBIDDEN (F4, Phase 2 Stage C):** no write path may flip an existing commitment event's `data.status` — the 2026-07-01 audit found 249 commitments closed by in-place mutation (still growing during the audit day), a pattern that violates append-only and correlates with the observed substrate corruption. Closure is a tombstone append through `close_commitment()`, full stop. Readers keep honoring the legacy in-place `status in ("closed", "resolved", "superseded")` values forever — those 249 rows depend on it — and the repair script formalizes them with amnesty tombstones so the class can be retired.

**Fail-loud (Phase 1, 2026-07):** the append gate REJECTS a `commitment_resolved` event that carries no readable id (none of `data.commitment_id` / `data.id` / `data.target_id` / `data.commitment_seq` / `data.source_event_seq`). An id-less closure is a dead letter — 291 of them existed in the live substrate when the gate shipped. Embed the commitment's `data.id` verbatim; never re-derive it.

`thread_resolved` events also close commitments — that's the v2.7.13 batch-resolution path from the DCC's `✓ done` button. Producers can use either; aggregator treats them the same.

**Merge / supersession (`commitment_superseded`, v4.6.0 C4):** capture dedup is source-scoped (`(source_ref, title)` content hashes), so the same real-world commitment captured by different writers — meeting transcript (`granola:X`), follow-up email (`gmail:Y`), nightly sweep (`session:Z`) — lands as distinct open items. Two layers close the hole:

1. **Capture-time flagging (`shared/scripts/commitment_dedup.py`, hooked inside the single append path — caller-agnostic like the gate):** each new `commitment` is compared against the OPEN set within a 14-day window under a conservative rule — owner ids must not conflict, counterparty signals must agree (ids, or lenient name-token match with the Bug #103 title fallback), and the name-stripped titles must overlap strongly (higher bar when no person field corroborates). A suspect is NEVER dropped or auto-merged: it lands with `data.pending_review: true` + `data.suspected_duplicate_of: <open item's id>` + `data.suspected_duplicate_score`, so the confirm flow renders "looks like a duplicate of X — merge / keep both". Fail-open: a check failure appends the batch unflagged. `CR_DEDUP_CHECK=0` disables.
2. **The merge writer (`commitment_state.supersede_commitment(workspace_root, survivor_id, superseded_id, *, merged_by, source_skill, evidence, user_confirmed)`):** emits `commitment_superseded` — `data.commitment_id`/`data.commitment_seq` reference the SUPERSEDED item (closed through the standard closer chains, honored by the loader since v3.14.5), `data.superseded_by` names the survivor, `data.merged_source_refs` unions both sides' provenance. `load_open_commitments` folds the union onto the survivor's in-memory copy (`data.merged_source_refs` / `data.merged_from`) — the survivor "carries" every absorbed source without any history rewrite. Same guards as `close_commitment`: id normalization, loud `CommitmentIdError`, idempotent re-merge, `PendingReviewError` unless `user_confirmed=True` (merging IS the adjudication of a flagged suspect), lock-spanned scan→append. Surfaces: the W4b `merge` verb (v4.6.1 — confirm section + the triage Unconfirmed block) and the chat phrase ("merge those two" / "same commitment") documented in commitment-triage.

**Confirm-flow adjudication (`commitment_updated` markers, v4.6.1 W4b):** two more append-only adjudications ride `commitment_updated`, written ONLY by their `commitment_state` writers and folded read-side by `load_open_commitments`: **Mine** (`confirm_commitment_owner` — `data.owner_confirmed: true` + `data.new_owner_id`; ownership folds to the user and `pending_review` clears, so the item leaves the unconfirmed bucket) and **Keep both** (`clear_review_flags` — `data.review_flags_cleared: true`; clears `pending_review`, `review_reason`, and the C4 `suspected_duplicate_*` flags — confirmed distinct). Adjudication folds are append-order-aware against `commitment_reassigned`: the LATEST adjudication decides `pending_review` (a Mine followed by a later unconfirmed reassignment re-stamps the flag, and vice versa). Unconfirmed items never enter chase and count only in the headline `unconfirmed` bucket; adjudication is always an explicit user action — no writer may auto-confirm.

**Per-person receipt (`commitment_partial_received`, v4.6.0 MC1):** a multi-counterparty commitment ("send the deck to the board") is fulfilled one counterparty at a time. `commitment_state.mark_partial_received(workspace_root, commitment_id, *, received_by, source_skill, counterparty_id=|counterparty_name=)` appends a `commitment_partial_received` naming WHICH counterparty delivered (`data.received_counterparty_id` / `received_counterparty_name`; `data.commitment_id` + `commitment_seq` identify the item — the append gate rejects an id-less one as a dead letter). It is NOT a closer — `load_open_commitments` accumulates `data.received_from` / `received_from_names` on the projection and stamps `data.all_counterparties_received: true` when the roster is complete. Closure is then PROPOSED (the daily chase renders "everyone's received — close it?"), never automatic — the user closes via the normal closure path. The CRU matchers (`match_send_/inbound_/calendar_to_commitments`) return `recommendation: "partial_received"` with `matched_counterparty_ids` for a multi-counterparty match instead of `auto_resolve`, so a send/reply/event to ONE counterparty records that person's receipt rather than whole-closing the item. Single-counterparty commitments are entirely unaffected (the matcher downgrade fires only on multi).

**Deferral (`commitment_updated`, read since Phase 2 Stage A):** a commitment's due date moves via a `commitment_updated` event carrying `data.commitment_id` + `data.new_due` (the orchestrator `push to [date]` verb; `due` / `due_date` accepted as variants). The original commitment event is NEVER rewritten — `load_open_commitments` folds the latest due-carrying update into the returned commitment's effective `data.due` (with `data.due_updated_by_seq` provenance), so consumers reading `_commitment_field(ev, "due")` see the pushed date and a deferred item stops rendering overdue. Updates without a due field (scope/summary changes via `change_summary`) don't affect the effective due.

---

## Producer skills (who writes commitment events)

| Skill | Trigger | Source event |
|---|---|---|
| `meeting-notes` | After extracting Action Items table | `meeting` event for the same call |
| `inbox-triage` | When email contains commitment language ("I'll send by Friday", "owe you", etc.) | `interaction` event for the email thread |
| `follow-up-ritual` | Same trigger as meeting-notes (it invokes meeting-notes internally) | `meeting` event |
| `scan-for-commitments` | One-shot bulk scan over historic Granola/Gmail data | varies — re-creates source events if missing |
| `scan-for-commitments` (Slack leg, v4.6.0 MC3) | Recent-window Slack channel/DM scan when the connector is present | none — no parent event; provenance is `source_ref: slack:<permalink>` |
| `reconcile-sent` (sent-promise capture, v4.6.2 BUG-3719) | Daily silent Sent pass — opens the user's OWN outbound promises nothing tracks yet | none — no parent event (nothing writes outbound `interaction` events); provenance is `source_ref: gmail:<message_id>` |
| `scan-for-commitments` (Sent pass, v4.6.2 BUG-3719) | Historical outbound backfill over the mail connector's Sent folder | none — same `gmail:<message_id>` provenance |

Slack-based commitments are extracted by `scan-for-commitments`'s Slack leg (v4.6.0 MC3) via `shared/scripts/slack_capture.py` — same Stage-D/S2/Stage-E capture block as every writer, `source_ref: slack:<permalink>` (the spelling this schema reserved), user's-own-messages as the promise source / messages-naming-the-user as the owed-to-you source, third-party items refused at the builder. Slack absent = the leg doesn't exist (skip-not-fail). Real-time per-message Slack extraction (an inbox-triage analog) still does not exist — the recent-window scan is the coverage today.

**Sent-mail promises (v4.6.2, BUG-3719):** the user's own outbound email promises are captured via `shared/scripts/sent_capture.py` — the Slack direction doctrine's email analog (the user's sent messages are the PRIMARY promise source; direction is fixed by the surface, so this lane never opens owed-to-you items). Two callers share the one implementation: `reconcile-sent`'s daily capture pass (`reconcile_and_receipt(..., sent_commitment_items=...)`, the rescue for threads read+replied before inbox-triage's `is:unread in:inbox` gate ever saw them) and `scan-for-commitments`' Sent pass (historical backfill). Owner is always the resolved primary user; same capture block, W4c relevance gate, and `(source_ref, title)` idempotency as every writer — PLUS capture-side restatement dedup (`capture_gate.matches_open_commitment`: shared non-user party + content-token overlap vs the open set) so a sent restatement of a meeting- or triage-sourced commitment merges into the existing item instead of double-tracking. Note inbox-triage's extractor DOES cover outbound language in the threads it sees — the gap this lane closes is upstream of extraction: read-before-triage threads never became candidates at all.

---

## Consumer skills (who reads commitment events)

| Consumer | Reads via | Surfaces as |
|---|---|---|
| `build_workspace_map_input.py` projector | `_aggregate_commitments` | THREADS_JSON + COMMITMENTS_JSON for the orgs-map / people-network / commitments-tracker artifacts |
| `morning-briefing` | `commitment_state.compute_brief_state` → `counts["headline"]` | "Commitments: X you owe · Y owed to you · U unowned · C unconfirmed · O overdue" line (v4.5.2 R4 — the one bucket export; never label the overdue number "stuck", per R1b) |
| `follow-up-ritual` | direct events.jsonl scan | per-attendee open commitments listed in the close-the-loop pack |
| `cleanup` | direct events.jsonl scan | overdue/aging commitment counts in the weekly digest |

Consumers MUST handle both:
- Canonical shape: `ev["data"]["owner_id"]`, `ev["data"]["title"]`, etc.
- Legacy flat shape: `ev["owner"]`, `ev["title"]`, top-level fields.

---

## Append example (Python pseudo-code)

```python
import datetime
import sys
from pathlib import Path

# Canonical helpers — every producer skill imports these (see WORKSPACE_API.md §3).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared" / "scripts"))
from atomic_write import atomic_append_jsonl
from next_seq import next_seq

def append_commitment(events_jsonl_path, *, primary_thread_id, owner_id,
                      title, due_iso, source_event_seq, source_ref,
                      person_ids, classification_confidence,
                      source_skill="meeting-notes"):
    seq = next_seq(events_jsonl_path)
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    if due_iso:
        due_dt = datetime.date.fromisoformat(due_iso)
        status = "overdue" if due_dt < datetime.date.today() else "open"
    else:
        status = "open"

    ev = {
        "seq": seq,
        "ts": now_iso,
        "type": "commitment",
        "source_skill": source_skill,
        "primary_thread_id": primary_thread_id,
        "related_thread_ids": [],
        "classification_confidence": classification_confidence,
        "person_ids": list(set([owner_id] + (person_ids or []))),
        "data": {
            "owner_id": owner_id,
            "title": title.strip(),
            "due": due_iso or "",
            "status": status,
            "source_event_seq": source_event_seq,
            "source_ref": source_ref or "",
        },
    }
    # Canonical append: atomic write + Drive-sync safety + automatic view regen.
    # NEVER open(events_jsonl_path, "a") directly — that bypasses all of it (WORKSPACE_API.md §3).
    atomic_append_jsonl(events_jsonl_path, [ev])
    return seq
```

In practice, every producer skill uses the writer helper described in `shared/WORKSPACE_API.md` (which handles seq reservation, conflict logging, and view regeneration). The pseudo-code above shows the shape only — do not replicate seq logic by hand.

---

## Owed vs Task — the two-surface split (SPEC CTS1, 2026-07)

The daily experience partitions the ONE open set into two named surfaces plus a confirm tail. This is a **read-side projection** (`shared/scripts/surface_split.py`) — no new field, no second store, and NEVER a `direction` field (direction is derived from `owner_id` vs the primary user; storing it would create a second source of truth for the same fact).

**Definitions:**

| Term | Definition | Field basis |
|---|---|---|
| **Owed** (a promise) | A deliverable a named party is *waiting on*, because it was **communicated** to them. Either direction. | effective `kind` ≠ `task` (post-`commitment_reclassified` fold) |
| **Task** (a to-do) | Work with **no counterparty** — the user decided it; only their clock is running. | effective `kind` == `task` |
| **Direction** | Who acts next. Derived, never stored. | `owner_id` present and == user → they do · present and ≠ user → they wait on someone · absent → unowned |

**The trap (encode in every capture prompt): the beneficiary is not the counterparty.** Building a dashboard *for* someone is a personal Task until the user tells them "I'll have it by Friday." Owed-ness is a *communicated expectation*, not who benefits. And a due date does NOT make something Owed — "consolidate by Friday" is a dated Task; deadline is time metadata on either kind.

**The classifier is effective kind, never raw counterparty presence** (RULED 2026-07-16, Option B). ~49 live open promises carry owner=user and NO resolvable counterparty — the Bug #103 class where counterparty LINKING failed on real promises. Those stay Owed, rendered on My Plate · Promised tagged "counterparty unresolved" with a drip + Friday-batch fixup; they are never silently demoted.

**The five-way partition invariant** (asserted by `tests/run_cts1_surface_split_test.py`, over TOP-LEVEL items only — SUB1):

```
waiting_on + promised + personal + unowned + unconfirmed == total
```

`waiting_on` == headline `owed_to_you`; `promised + personal` == headline `you_owe`. The surfaces re-group `count_commitments`' buckets — they never re-count them.

**Write-time consistency (warn-level, NEW writes only — the §5 invariant):** `kind: task` ⇒ counterparty empty; `kind: promise` ⇒ counterparty signal present OR `pending_review`. `event_gate.gate_events` warns (stderr, never rejects) when a new commitment violates either half. Historical rows converge via the fixup paths, never via warnings. The counterparty test goes through `commitment_parties` (the MC1 ids/names union) — never the two scalar fields alone.

**Orthogonality guards (keep the line from smearing):**
- A **Task is NOT a Reminder.** A Task has a deliverable you complete; a Reminder ("renew the domain") is pure surfacing with nothing to finish — its own manual lane (`reminders.py`, user-minted only), never in either surface.
- **Discuss-later / "big ideas" stay separate** (`commitment_to_discuss` / show-my-list). Not deliverables → never in the two surfaces.
- **Delegated tasks** (owner ≠ user, kind `task`) render on Waiting On (someone else acts next) but stay out of CRU (`cru_eligible` excludes task kind) — manual nudge only, never auto-chased.

## Migration notes

- Pre-v2.7.15 events.jsonl files may have commitment events with flat top-level fields (`project_id`, `owner`, `title`, `due`, `status`). The aggregator handles these. Do NOT rewrite existing events to canonical shape — append-only is non-negotiable per the events schema.
- New commitment events written by v2.7.15 producers MUST use the canonical shape above.
- `scan-for-commitments` is the migration path for users who want historic transcripts re-processed into commitment events. It writes canonical-shape events with `source_skill: "scan-for-commitments"` so they're distinguishable from per-meeting writes.

**End of commitment schema contract.**
