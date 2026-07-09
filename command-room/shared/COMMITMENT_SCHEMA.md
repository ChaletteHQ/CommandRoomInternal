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

### Optional fields inside `data`

| Field | When to include | Notes |
|---|---|---|
| `id` | ALWAYS present after append (Phase 1, 2026-07) | The append gate mints `cmt_<ulid>` at write time when the producer omits it — ids are written, never synthesized-only. Producers MAY still set an explicit id (it is respected). Historic events without one keep resolving via the legacy synthesized `commitment_seq_<seq>` read-side alias. `commitment_resolved` events point back at this value verbatim. |
| `kind` | REQUIRED AT CAPTURE (Stage D, 2026-07 — was gate-stamped from Phase 1) | Required discriminator, one of `promise` / `task` / `scheduling` / `agenda` (ratified 2026-07-01). **Producers classify at write time:** counterparty determinable → `promise`; self-owed, no counterparty → `task`; scheduling intent → `scheduling`; discuss items stay the separate `commitment_to_discuss` type; ambiguous → `promise` + `pending_review: true`. The strict `append_event()` path REJECTS a missing kind; the legacy burn-in path warns loudly + stamps `promise`. Historic events with no kind read as `promise` forever. **The kind POLICY is code-enforced:** `task` never enters CRU matching (`cru_match.cru_eligible`), tasks auto-stale at 30 days into the Friday triage (`commitment_state.stale_tasks`), never render in commitment aging, and never get chased. Promote/demote is an additive `commitment_reclassified` marker (`commitment_state.promote_task_to_commitment`) — a label change the projector applies read-side; never delete/recreate. |
| `no_due` | when the extraction genuinely proposes no due date | Boolean (S2 due-date nudge). Every extraction proposes a `due` OR sets this explicitly — silence is not an option. Undated items surface in the weekly triage, not the aging view; target undated share < 30%. |
| `urgency` | optional flag from extractor | `"high"` / `"normal"` / `"low"`. Currently consumed only by `morning-briefing`. |
| `evidence` | when extracted from text | Raw quoted phrase (≤200 chars) — useful for "why did you log this?" debugging. Do NOT store full transcripts. |
| `meeting_date` | when extracted from a meeting transcript | ISO date `"YYYY-MM-DD"` — the date the source meeting occurred (NOT the date the commitment was logged). Use ONLY as a derivation hint for resolving relative phrases ("tomorrow", "by Friday") at extraction time. **Authoritative due-date is `data.due`. Readers MUST compute "overdue/today/upcoming" against `data.due`, never against `data.meeting_date`.** |
| `owner_external` | when the owner is named but has no entity record yet (`owner_id` is null) | Free-text name string (e.g., `"Rakesh"`) so the surface skill can render "owed to you by Rakesh — add as contact." When `owner_id` is non-null this field is omitted. v2.14.19+: enables the reachability-filter surfacing pattern. |
| `counterparty_id` | REQUIRED when determinable (Stage E 2026-07, F5 — extraction receipts) | Canonical `person_NNN` of who the deliverable is owed TO (or, for owed-to-you items, who owes it). Also included in `person_ids`. Feeds the CRU candidacy gate DIRECTLY (`match_send_to_commitments`) — without it the matcher leans on the title-token fallback and misses real completions (the Bug #103 recall class; live yield was 4 closes / 644 scanned). **Retires `requester_id` / `requester_person_id` for NEW writes** — readers keep the `_COMMITMENT_FIELD_ALIASES` chain forever, so the 228 historic requester_* events stay readable. |
| `counterparty_name` | SHOULD set when the counterparty is named but resolves to no person record | Free-text name (e.g., `"Rakesh"`, `"Jordan Lee"`). The matcher's candidacy gate matches recipient display names / email local-parts against it (Stage E), so a receipt survives even when entity resolution couldn't land an id. |
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
2. **The merge writer (`commitment_state.supersede_commitment(workspace_root, survivor_id, superseded_id, *, merged_by, source_skill, evidence, user_confirmed)`):** emits `commitment_superseded` — `data.commitment_id`/`data.commitment_seq` reference the SUPERSEDED item (closed through the standard closer chains, honored by the loader since v3.14.5), `data.superseded_by` names the survivor, `data.merged_source_refs` unions both sides' provenance. `load_open_commitments` folds the union onto the survivor's in-memory copy (`data.merged_source_refs` / `data.merged_from`) — the survivor "carries" every absorbed source without any history rewrite. Same guards as `close_commitment`: id normalization, loud `CommitmentIdError`, idempotent re-merge, `PendingReviewError` unless `user_confirmed=True` (merging IS the adjudication of a flagged suspect), lock-spanned scan→append. Surfaces: the W4b Merge verb once it ships; until then the chat phrase ("merge those two" / "same commitment") documented in commitment-triage.

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

Slack-based commitments are extracted by `scan-for-commitments`'s Slack leg (v4.6.0 MC3) via `shared/scripts/slack_capture.py` — same Stage-D/S2/Stage-E capture block as every writer, `source_ref: slack:<permalink>` (the spelling this schema reserved), user's-own-messages as the promise source / messages-naming-the-user as the owed-to-you source, third-party items refused at the builder. Slack absent = the leg doesn't exist (skip-not-fail). Real-time per-message Slack extraction (an inbox-triage analog) still does not exist — the recent-window scan is the coverage today.

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

## Migration notes

- Pre-v2.7.15 events.jsonl files may have commitment events with flat top-level fields (`project_id`, `owner`, `title`, `due`, `status`). The aggregator handles these. Do NOT rewrite existing events to canonical shape — append-only is non-negotiable per the events schema.
- New commitment events written by v2.7.15 producers MUST use the canonical shape above.
- `scan-for-commitments` is the migration path for users who want historic transcripts re-processed into commitment events. It writes canonical-shape events with `source_skill: "scan-for-commitments"` so they're distinguishable from per-meeting writes.

**End of commitment schema contract.**
