---
name: log-resolution
description: "Logs a `thread_resolved` event to events.jsonl when a CEO clicks ✓ done on an item in a Command Room dashboard or scheduled-chat widget. Fires silently in chat — minimal response, no clutter. Triggers on the artifact's auto-sent prompts: `log resolved: <id>`, `log resolved: <id> (<kind>)`. Replaces the v2.7.x batch-paste flow where the user had to manually paste a clipboard prompt to log dismissals."
---

# log-resolution

Tiny event-writer skill — silently appends a `thread_resolved` event to `_hq/data/events.jsonl` when a dashboard's ✓ done button fires its sendPrompt.

This is the v2.8.0 replacement for the v2.7.x "copy batch + paste" dismiss flow. Each ✓ done click in an artifact now sends a small chat prompt (`log resolved: <id>`); this skill catches that prompt, writes the event, and confirms with a one-line response.

## Trigger patterns

The artifact's `logResolution()` JS function sends one of:

```
log resolved: <id>
log resolved: <id> (<kind>)
log resolved: <id> [<kind>] from <artifact>
```

Where:
- `<id>` is the matter/meeting/commitment id (e.g., `matter_priority_3`, `m_meeting_2026_04_28_jane`)
- `<kind>` is the item type (e.g., `priority`, `meeting`, `commitment`, `inbox`)
- `<artifact>` is which dashboard fired it (rarely included, but supported)

If a CEO types something matching this pattern manually (rare), this skill still fires and logs the event.

## Behavior

1. Parse the trigger to extract `<id>` and optionally `<kind>`.
2. **Infer `<kind>` from the `<id>` prefix if it's missing (v3.11.4+ — REQUIRED per `references/SOURCE_OF_TRUTH.md`).** Pre-v3.11.4, artifact UIs that fired `log resolved: <id>` without the `(<kind>)` suffix on a commitment-class id silently fell through the dual-write path in step 4 below and left the commitment counted as open elsewhere in the workspace. The id-prefix inference table:

   | Id pattern | Inferred kind |
   |---|---|
   | starts with `commitment_` | `commitment` |
   | starts with `m_meeting_` or `meeting_` | `meeting` |
   | starts with `matter_` or `priority_` | `priority` |
   | starts with `inbox_` | `inbox` |
   | pure integer (e.g. `1247` — a raw event seq) | inspect the referenced event's `type` and use that |
   | anything else | `unknown` |

   If the trigger DID supply `<kind>` explicitly, use it as-is — don't override.

3. Read the last 200 lines of `_hq/data/events.jsonl` to check for an existing `thread_resolved` event with the same id (idempotency — re-firing the same dismissal must not create duplicates). For commitment-kind, also check for an existing `commitment_resolved` event with `data.commitment_id == <id>`.
4. If already resolved → respond with a one-line `"already done"` and stop.
5. If not yet resolved → append events per the kind:
   - **For `<kind> == "commitment"` (v3.11.1+):** dual-write per the "Manual commitment-close path" section below. Both `commitment_resolved` (via `cru_match.build_commitment_resolved_event`) AND `thread_resolved`.
   - **For all other kinds:** append a single `thread_resolved` event:
     ```json
     {"type":"thread_resolved","ts":"<ISO-now>","data":{"id":"<id>","kind":"<kind-or-unknown>","source_artifact":"<artifact-or-null>"}}
     ```
6. Respond with a one-line `"✓ done"` confirmation. **No verbose summary, no tangent, no follow-up question.**

## Why it exists

v2.7.x dismiss flow: user clicks ✓ done → JS pushes to dismissedQueue → user clicks "Copy batch" → user pastes into chat → bot processes the batch → events written. Worked but required user discipline. Auto-rebuild every 30 min (v2.8.0 architecture) would un-dismiss anything not yet pasted.

v2.8.0 dismiss flow: user clicks ✓ done → JS sends `log resolved: <id>` → this skill catches → event written instantly → next auto-rebuild's projector reads events.jsonl and pre-filters resolved items. Persistence guaranteed; no user action required beyond the click.

## Forbidden behaviors

- **Do NOT respond verbosely.** This is a silent log. The CEO didn't ask a question; they clicked a button. Confirmation should be one line maximum.
- **Do NOT acknowledge in a way that uses tokens.** Skip the "I've logged the resolution and will continue to monitor..." padding.
- **Do NOT log duplicate events.** Always check existing events.jsonl first.
- **Do NOT use this skill to log user-typed mark-as-done commands** (e.g., "mark X as resolved" — that's a different skill, scan-for-commitments or similar). This skill handles ONLY the artifact-fired pattern.

## Manual commitment-close path (v3.11.1+ — REQUIRED)

When the trigger arrives with `<kind>` of `commitment` (or the parsed kind is `commitment`), this skill writes a `commitment_resolved` event ON TOP OF the existing `thread_resolved` event, so the v3.4.5 CRU consumers (`load_open_commitments`, MASTER_TRACKER aggregation, morning-brief Step 3b counts) recognize the closure. Without this, marking a commitment ✓ done via the artifact UI cleared it from the dashboard but left it counted as open elsewhere.

Procedure for `<kind> == "commitment"`:

1. Parse `<id>` from the trigger — this is the `commitment_id` (the `seq` of the original `commitment` event).
2. Idempotency: scan the last 200 lines of events.jsonl for an existing `commitment_resolved` event with `data.commitment_id == <id>`. If present → `"already done"` and stop (do NOT also re-emit `thread_resolved`).
3. If not present, build via `shared/scripts/cru_match.py::build_commitment_resolved_event(commitment_id=<id>, resolved_by=<user_id_from_entities>, primary_thread_id=<resolved-from-commitment-event-or-null>, source_skill="log-resolution", evidence="manual close via dashboard", next_seq=<next>)`. Append via `atomic_append_jsonl`. Then ALSO append the canonical `thread_resolved` event the rest of this skill already emits (kept for backwards-compat with v2.7.x consumers that still read `thread_resolved`).
4. One-line confirmation as usual: `"✓ done"`. Silent otherwise.

For all other `<kind>` values (matter, meeting, inbox, priority) the behavior is unchanged — only `thread_resolved` is written.
