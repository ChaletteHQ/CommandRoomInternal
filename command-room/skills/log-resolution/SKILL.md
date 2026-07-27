---
name: log-resolution
description: "Logs a `thread_resolved` event to events.jsonl when a LEGACY Command Room dashboard artifact fires its per-click `log resolved:` prompt. Current scheduled-chat widgets and dashboards do NOT route here — their ✓ clicks travel in the consolidated `apply choices: [...]` message handled by apply-choices. Fires silently in chat — minimal response, no clutter. Triggers on the artifact's auto-sent prompts: `log resolved: [id]`, `log resolved: [id] ([kind])`."
---

# log-resolution

Tiny event-writer skill — silently appends a `thread_resolved` event to `_hq/data/events.jsonl` when a dashboard's ✓ done button fires its sendPrompt.

This is the v2.8.0 replacement for the v2.7.x "copy batch + paste" dismiss flow. Each ✓ done click in an artifact now sends a small chat prompt (`log resolved: <id>`); this skill catches that prompt, writes the event, and confirms with a one-line response.

## Status — legacy surface only (no live producer)

No current plugin surface emits `log resolved:`. Today's scheduled-chat widgets and dashboards send every ✓ click through the consolidated `apply choices: [...]` payload, which `apply-choices` dispatches — not this skill. log-resolution stays registered ONLY for legacy pre-v2.14 artifacts a customer may still have open (their `logResolution()` JS fires the old per-click prompt) and the rare manually typed match. If the trigger arrives, handle it exactly as specced below — but never advertise `log resolved:` as a current command.

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

**Output guard (PL.10):** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.

- ❌ "thread_resolved appended (seq 4102) via atomic_append_jsonl"
- ✅ "Done — marked it resolved."

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

3. Idempotency + write, per the kind:
   - **For `<kind> == "commitment"` (Stage B, 2026-07):** go straight to the "Manual commitment-close path" section below — `commitment_state.close_commitment()` handles idempotency itself over the FULL resolved-id set (the pre-Stage-B last-200-lines window could re-close anything older than the tail).
   - **For all other kinds:** read the last 200 lines of `_hq/data/events.jsonl` to check for an existing `thread_resolved` event with the same id (idempotency — re-firing the same dismissal must not create duplicates).
4. If already resolved → respond with a one-line `"already done"` and stop.
5. If not yet resolved → append events per the kind:
   - **For `<kind> == "commitment"`:** the close_commitment call below (plus the back-compat `thread_resolved`).
   - **For all other kinds:** append a single `thread_resolved` event through the locked writer — `atomic_append_jsonl(events_path, [event], holder="log-resolution")` from `shared/scripts/atomic_write.py`. OMIT `seq` — the gate auto-stamps it inside the lock. Never hand-roll an `open('a')` append or a raw `>>`:
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

## Manual commitment-close path (Stage B 2026-07 — REQUIRED, supersedes the v3.11.1 build-and-append procedure)

When the trigger arrives with `<kind>` of `commitment` (or the parsed kind is `commitment`), the closure goes through **`shared/scripts/commitment_state.py::close_commitment()` — the single closure path (F2)**. It writes the canonical `commitment_resolved` event so the CRU consumers (`load_open_commitments`, MASTER_TRACKER aggregation, morning-brief Step 3b counts) recognize the closure, and it owns everything this skill used to hand-roll:

- **Legacy-id normalization.** The widget embeds the commitment's `data.id` verbatim (per `shared/CHAT_ACTION_WIDGET.md`), but historic artifacts fired bare seqs — `log resolved: 86`, `seq_86`, `event_086`, `commitment_seq_86`. close_commitment resolves ALL of those to the canonical id via seq lookup. (A bare-int closure written as-is was the bare-int dead-letter class: the tombstone `"86"` matched nothing and the item stayed open forever.)
- **Loud no-match.** If the id matches no commitment, close_commitment raises `CommitmentIdError` — do NOT write anything; respond `"⚠️ Couldn't find that item — it may have been re-captured. Say 'show my list' to see what's open."` No more orphan tombstones.
- **Full-set idempotency — judged on `commitment_resolved` ONLY.** Already closed (a `commitment_resolved` event anywhere in history, not just the last 200 lines) → the result's `status` field is `already_resolved` → respond `"already done"` and stop (do NOT re-emit `thread_resolved`). A lone pre-existing `thread_resolved` for the same id (legacy artifact wrote it without the canonical closure) does NOT count as already-resolved — close_commitment still runs and backfills the canonical `commitment_resolved` so CRU consumers finally see the closure.
- **pending_review floor.** The ✓ click IS an explicit user action, so pass `user_confirmed=True`.

Procedure for `<kind> == "commitment"`:

1. Parse `<id>` from the trigger — pass it to close_commitment AS RECEIVED (canonical `cmt_<ulid>` or any legacy seq spelling; the normalizer owns the mapping, never pre-convert it yourself).
2. Call:
   ```python
   from commitment_state import close_commitment, CommitmentIdError
   result = close_commitment(
       workspace_root, "<id-as-received>",
       resolved_by="<user_person_id from entities.json>",
       evidence="manual close via dashboard",
       source_skill="log-resolution",
       user_confirmed=True,   # explicit ✓ click
   )
   ```
3. `result["status"] == "already_resolved"` → `"already done"`, stop. `"closed"` → ALSO append the `thread_resolved` event the rest of this skill emits (kept for backwards-compat with v2.7.x consumers that still read `thread_resolved`).
4. One-line confirmation as usual: `"✓ done"`. Silent otherwise.

For all other `<kind>` values (meeting, inbox, priority — note `matter_*` ids infer to `priority` per the Step 2 table) the behavior is unchanged — only `thread_resolved` is written.
