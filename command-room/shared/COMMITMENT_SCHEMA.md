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
| `id` | when you want a stable, human-readable id for cross-references | Default if omitted: `commitment_seq_<seq>`. Use this for `commitment_resolved` events to point back. |
| `urgency` | optional flag from extractor | `"high"` / `"normal"` / `"low"`. Currently consumed only by `morning-briefing`. |
| `evidence` | when extracted from text | Raw quoted phrase (≤200 chars) — useful for "why did you log this?" debugging. Do NOT store full transcripts. |
| `meeting_date` | when extracted from a meeting transcript | ISO date `"YYYY-MM-DD"` — the date the source meeting occurred (NOT the date the commitment was logged). Use ONLY as a derivation hint for resolving relative phrases ("tomorrow", "by Friday") at extraction time. **Authoritative due-date is `data.due`. Readers MUST compute "overdue/today/upcoming" against `data.due`, never against `data.meeting_date`.** |
| `owner_external` | when the owner is named but has no entity record yet (`owner_id` is null) | Free-text name string (e.g., `"Rakesh"`) so the surface skill can render "owed to you by Rakesh — add as contact." When `owner_id` is non-null this field is omitted. v2.14.19+: enables the reachability-filter surfacing pattern. |
| `pending_review` | when the commitment was extracted by a low-confidence pass | Boolean. If true, surface skill should flag for explicit user review. Bypasses auto-chase paths until confirmed. |
| `review_reason` | when `pending_review` is true | One-line plain-English explanation of why review is needed (e.g., "Owner is external person — no entity record yet"). Used by Pulse and Commitments surfaces to compose review prompts. |

---

## Extraction triggers (when to write a commitment event)

A commitment exists when a person makes a forward-looking promise about a specific deliverable, and a reasonable third party reading the source would identify a clear owner. Producer skills MUST emit a commitment event when ALL of the following are true:

1. **Forward-looking** — the deliverable is in the future, not a description of past action.
2. **Specific** — there's a concrete artifact, decision, or action ("send the deck", "decide on pricing by Friday", "introduce me to your CFO"). Vague intentions ("we should think about that", "let's circle back") DO NOT qualify.
3. **Owned** — there's an identifiable person taking it on. If the source uses "we" or "the team" without naming an individual, do NOT emit a commitment.
4. **Not duplicate** — `(source_ref, title)` is unique. If the same source has been processed before and produced an equivalent commitment, skip.

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

The aggregator removes the commitment from open queues when it sees a `commitment_resolved` event whose `data.commitment_id` matches an open commitment's `data.id` (or the synthesized `commitment_seq_<seq>` if no explicit id was set).

`thread_resolved` events also close commitments — that's the v2.7.13 batch-resolution path from the DCC's `✓ done` button. Producers can use either; aggregator treats them the same.

---

## Producer skills (who writes commitment events)

| Skill | Trigger | Source event |
|---|---|---|
| `meeting-notes` | After extracting Action Items table | `meeting` event for the same call |
| `inbox-triage` | When email contains commitment language ("I'll send by Friday", "owe you", etc.) | `interaction` event for the email thread |
| `follow-up-ritual` | Same trigger as meeting-notes (it invokes meeting-notes internally) | `meeting` event |
| `scan-for-commitments` | One-shot bulk scan over historic Granola/Gmail data | varies — re-creates source events if missing |

Slack-based commitments are NOT currently extracted (v2.7.16 candidate — needs the slack connector wrapper that doesn't exist yet for inbox-triage).

---

## Consumer skills (who reads commitment events)

| Consumer | Reads via | Surfaces as |
|---|---|---|
| `build_workspace_map_input.py` projector | `_aggregate_commitments` | THREADS_JSON + COMMITMENTS_JSON for the orgs-map / people-network / commitments-tracker artifacts |
| `morning-briefing` | direct events.jsonl scan | "Open commitments: X you owe, Y they owe, Z stuck" line |
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
