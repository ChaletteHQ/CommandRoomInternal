---
name: show-my-list
description: "The retired discuss-later list, drain-only — renders whatever is still open read-only so each item can be cleared or dropped; nothing adds to it anymore (new items go to My Plate or a contact note). Triggers: 'show my list', 'whats on my list', 'what to discuss', 'what do i need to discuss', 'discuss list', 'show discuss list', 'my list'. Also hosts the mute ledger (see Routing). Does NOT fire on 'show my reminders' / 'my reminders' / 'remind me about' (show-my-reminders — the date-pinned reminder lane)."
---

# show-my-list

**The list is RETIRED (MLK1, M ruling 2026-07-21).** The `Add to my list` capture verb (formerly `Log to discuss` pre-v2.14.4) no longer renders anywhere and no capture path writes new `commitment_to_discuss` events — the live workspace showed the captures were context-free fragments, undecidable later (UX review findings 1 + 11). New items go to My Plate (`add to my plate`) or land as a note on the person/thread they belong to.

This skill remains for two things: (1) the FOSSIL READER — whatever `commitment_to_discuss` events are still open render read-only here so the user can drain them (clear or drop; the backlog only shrinks); (2) the MUTE LEDGER (`show muted` / `show snoozed`), which is unaffected by the retirement and lives here permanently.

## Behavior

### Step 1 — Discover plugin root + render via the audit harness

Per CONTRACT.md Rule 22, every multi-step bash invocation uses dynamic discovery:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')

# Read events.jsonl, filter to commitment_to_discuss events, exclude items
# already resolved or skipped.
#
# v3.11.4+: canonical closure shape per references/SOURCE_OF_TRUTH.md.
# The `resolved` click writes commitment_resolved with data.commitment_id;
# `skip` writes chat_dismissal with data.target_id. Both reference the
# original commitment_to_discuss event's seq.
#
# Backwards-compat: pre-v3.11.4 wrote thread_resolved with data.target_id —
# the legacy shape didn't match any consumer filter (the bug this section
# fixes). Accept both shapes here so in-flight items close correctly.
events_path = '$WORKSPACE/_hq/data/events.jsonl'
with open(events_path, 'r', encoding='utf-8') as f:
    events = [json.loads(line) for line in f if line.strip()]

# Find all commitment_to_discuss events
discuss_items = [e for e in events if e.get('type') == 'commitment_to_discuss']

# Collect resolved seqs from all known closer shapes
resolved_seqs = set()
for e in events:
    et = e.get('type')
    d = e.get('data') or {}
    if et == 'commitment_resolved':
        # canonical (v3.11.4+): data.commitment_id is the discuss event's seq
        cid = d.get('commitment_id') or d.get('id') or d.get('target_id')
        if cid is not None:
            resolved_seqs.add(cid)
    elif et == 'thread_resolved':
        # legacy show-my-list shape: data.target_id pointed at the discuss seq
        tid = d.get('target_id') or d.get('id') or d.get('thread_id')
        if tid is not None:
            resolved_seqs.add(tid)

# v4.6.0 S4 — dismissal liveness comes from THE mute ledger: honors the
# TTL the dismissal was written with AND any later unmute
# (chat_dismissal_cleared). Pre-S4 this filter treated every historic
# skip as permanent — a 24h skip suppressed the item forever.
import datetime
from mute_ledger import active_dismissal_target_ids
dismissed_seqs = active_dismissal_target_ids(
    events, datetime.datetime.now(datetime.timezone.utc).isoformat())

open_items = [e for e in discuss_items
              if e.get('seq') not in resolved_seqs
              and e.get('seq') not in dismissed_seqs]

# Group by person (who you'd discuss with). Person resolution order per
# item — first hit wins:
#   1. data.person_id present -> display name from entities.json people
#   2. no person_id: resolve the captured name string via aliases.json
#      (exact match) to a person id, then entities.json for the display name
#   3. still unresolved: fuzzy-match the name string against entities.json
#      people display names
#   4. no match at all: group under the literal captured name (never drop
#      the item just because the person didn't resolve)
# (groups items so the user sees 'next time you talk to X, mention these' rather than chronological order)
print(f'OPEN_COUNT={len(open_items)}')
"
```

Capture stdout. Then build a data view:

**Executive Output Standard (EXEC1, v3.20.0+) — queues get triage math, NOT meaning.** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`, this is a queue, so the synthesis-lead rule FORBIDS a narrative lead — **the header is a quantified count line**, not a theme. Compute it from the open items: total · how many trace to a valued org · the summed dollar (via `quantify.money_time_tag` / the org's revenue field — only figures that derive from substrate, never an estimate) · the oldest age. Each item carries its own quantify tag when `money_time_tag` returns non-None; otherwise no tag (never a fabricated dollar). No "what this list says about your week" sentence — the reader's next act is triage.

```python
from quantify import money_time_tag  # EXEC1: the only sanctioned source of $ tags

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "show-my-list",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    # EXEC1 quantified count line (triage math, no narrative). Plain English —
    # "{n} tied to revenue", never "{n} touch revenue" (reads broken), and ages
    # spelled out ("12 days"), never cryptic "12d". Two degraded renders when
    # revenue doesn't derive — never leave a dangling "—":
    #   (a) items touch revenue but no dollar derives from substrate ->
    #       "Your list — {n_open} to discuss · {n_revenue} tied to revenue · oldest {oldest_days} days"
    #   (b) nothing touches revenue (n_revenue == 0) -> drop the clause entirely:
    #       "Your list — {n_open} to discuss · oldest {oldest_days} days"
    "header": f"Your list — {n_open} to discuss · {n_revenue} tied to revenue (${total_k}K) · oldest {oldest_days} days",
    # MLK1 retirement line — REQUIRED on every fossil render, verbatim:
    "sub_header": "This list is retired — new items go to My Plate or a contact note.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            # Group by person/attendee — items each share a "next time you talk to X" context
            {
                "n": i,
                "icon": "💬",
                "name": person_name,
                # Per-item quantify tag appended ONLY when non-None (never an estimate).
                "context_tag": f"next time you talk to {person_name} ({item_count})"
                               + (f" · {tag}" if (tag := money_time_tag(items_for_person[0], entities)) else ""),
                "body_lines": [f"- {item['data']['summary']}" for item in items_for_person],
                # MLK1 D3 — drain verbs ONLY (Done / Drop). `skip` is gone
                # from new renders: a snooze grows nothing but keeps the
                # fossil circling; the two offered verbs both shrink it.
                "actions": [f"{i} resolved", f"{i} drop"],
            }
            for i, (person_name, items_for_person) in enumerate(grouped, start=1)
        ],
    }],
}
```

### Step 2 — Render through the canonical pipeline

```python
from widget_transport import render_and_persist
transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="show-my-list")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

The canonical renderer applies all v2.13.0+ validators (action verbs in CANONICAL_ACTIONS, no leak patterns, data-shape OK, wrapper contract) inside the transport call. Same enforcement as scheduled-task surfaces.

### Step 3 — Write a fire-marker event so apply-choices can identify this surface (v2.14.19+)

Before posting the widget, append a `pack_run` event to events.jsonl. **Use the canonical helper `log_pack_run` (`shared/scripts/log_pack_run.py`), which routes through the locked writer `atomic_append_jsonl` (SPEC GATE1 / A1) — do NOT hand-roll a `next_seq`+`open('a')` append or a raw `>>`.** The event shape:

```python
from log_pack_run import log_pack_run
log_pack_run(
    workspace_root=WORKSPACE_ROOT,
    kind="list",                 # non-task fire-marker; not a scheduled-task receipt
    surfaced=n_open,
    duration_ms=elapsed_ms,
    source_skill="show-my-list",
    fired_via="manual",          # canonical vocabulary (v4.5.2 R1); legacy "user-trigger" still parses
)
```

apply-choices reads the most recent fire-marker (within 60 min) to identify which orchestrator's handler to dispatch through. Without this event, clicking `resolved` or `drop` on a discuss-list item silently no-ops because apply-choices can't tell what surface it came from. (This was the v2.7.x→v2.14.18 silent-no-op bug from the simplify-pass Batch 7 finding #4.)

### Step 4 — Post widget, then STOP

Widget IS the post. No commentary.

If 0 items match: the backlog has drained — surface ONLY the plain-English empty state: `Your list is empty. This list is retired — new items go to My Plate or a contact note.` No widget, no fire-marker (nothing to dispatch), no retirement widget banner — the empty state is the whole render.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary (these are **scheduled chats** to the customer — never "scheduled tasks" or "daily check-ins" in rendered copy).
- BAD: "Your list — 5 to discuss · 3 touch revenue — $45K · oldest 12d"
- GOOD: "Your list — 5 to discuss · 3 tied to revenue ($45K) · oldest 12 days"

## What this skill does NOT do

- Writes exactly ONE event per fire: the Step 3 `pack_run` fire-marker (via `log_pack_run` → the locked writer). Beyond that single append it does not modify events.jsonl — clicking widget actions writes new events through apply-choices, that's separate
- Does NOT cross-reference against open commitments (that's CRU layer scope, v2.14.5+)
- Does NOT auto-surface based on calendar (the user pulls when they want; not pushed)
- Does NOT touch connectors (no Gmail / Calendar / Granola fetches)

Local read of events.jsonl + the one Step 3 fire-marker append + render through canonical pipeline.

## Mute ledger mode — `show muted` / `show snoozed` (v4.6.0 S4)

The SECOND surface this skill renders: every live mute, so a timed mute
stops being a one-way door. On "show muted" / "show snoozed":

```python
import sys, datetime; sys.path.insert(0, "shared/scripts")
from mute_ledger import live_mutes
from cru_match import load_events_defensively
events, _ = load_events_defensively("<WORKSPACE>/_hq/data/events.jsonl")
rows = live_mutes(events, datetime.datetime.now(datetime.timezone.utc).isoformat())
```

One widget row per ledger row, oldest first: what was muted (the row's
`note`, else its `target_id` resolved to the referenced item's title, else
the dismissal's `reason`), which chat it came from (`surface`), and the
remaining time VERBATIM from `ttl_label` ("3 days left" — every mute states
its duration, the F-59 rule). Actions per row: `unmute` (**Unmute**) ·
`skip` (**Snooze (1 day)** — hides the ledger row for a day, never touches
the underlying mute). Embed the row's dismissal `seq` verbatim (widget
identity contract); pass `source_skill: "show-my-list"` and write the same
Step 3 fire-marker. Dispatch: apply-choices § show-my-list mute-ledger rows
(`unmute` → the ledger's clear writer; ack says the item re-surfaces on its
next chat). 0 live mutes → plain English: `Nothing is muted right now.` —
no widget, no fire-marker.

Fences: permanent never-track rules are NOT in this ledger (they are
suppression rules in the workspace config, lifted by editing the rules
file — when any exist, add ONE footer line with their count); learned
suppressions (surface-preferences) are a separate durable layer, also not
timed mutes.

## Trigger pattern

`show my list` / `whats on my list` / `what to discuss` / `what do i need to discuss` / `discuss list` / `show discuss list` / `my list` / `show muted` / `show snoozed`

## Routing (full trigger corpus)

The mute-ledger phrase family (v4.6.0 S4) rides on this skill — the
description is budget-capped (G11), so these route via this section (same
rule as the other budget-capped skills; enforced by tests/triggers.yaml).

> Also fires on: 'show muted', 'show snoozed', 'what's muted', 'what did I snooze' — the mute-ledger mode: every live snooze/mute with its remaining time and an Unmute action. Does NOT fire on 'never track' rule management (those are permanent suppression rules, not timed mutes).

## Why the retirement (MLK1, 2026-07-21)

`add to my list` was the most confusable verb in the product: a sibling of `Add to My Plate` on the same rows, and a third "my ___" lane beside My Plate and my reminders. M's live list showed what the capture-then-curate design actually produced — context-free fragments with no decidable next step. The verb is gone from every surface; existing history stays readable here until it drains, and persisted old widgets still dispatch (the retired wire id keeps its original meaning — see apply-choices).

## See also

- `show-my-reminders` — the OTHER personal lane, fenced by design: reminders are date-pinned nags (`reminder` events, pin to the morning brief daily until cleared); this list is the retired discuss-later queue (`commitment_to_discuss`, drain-only, surfaces only when pulled). Neither skill reads the other's events, and the trigger families never cross ("my list" vs "my reminders")
- `add to my plate` (CHAT_ACTION_WIDGET.md action reference) — the own-it-now verb that covers the old capture intent when the item is really a task
- apply-choices § orphan-note re-route — a typed widget note with no action selected becomes a `note` on the resolved person/thread (or is declined honestly), never a list item
- `usage report` — separate read of events.jsonl for telemetry; this skill is for action items
