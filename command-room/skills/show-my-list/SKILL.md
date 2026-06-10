---
name: show-my-list
description: "Surfaces the user's curated 'discuss / follow-up later' list — items captured via the `add to my list` action across scheduled tasks. Read-only review surface. Triggers: `show my list`, `whats on my list`, `what to discuss`, `what do i need to discuss`, `discuss list`, `show discuss list`, `my list`. Use this when you want a batch view of everything you've flagged for later conversation, instead of context-switching when each item was originally captured."
---

# show-my-list

Captured-then-curated review surface. Throughout the day, scheduled-task widgets let the user click `Add to my list` (formerly `Log to discuss` pre-v2.14.4) to flag items they want to bring up in a later conversation but don't want to act on right now. Those flags accumulate as `commitment_to_discuss` events in events.jsonl. This skill is the ONE place to review them in batch.

Replaces the v2.10.x pattern of immediately-actionable items with a deferred-review pattern: capture quickly, review at a quiet moment.

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

dismissed_seqs = {(e.get('data') or {}).get('target_id')
                  for e in events if e.get('type') == 'chat_dismissal'}
dismissed_seqs.discard(None)

open_items = [e for e in discuss_items
              if e.get('seq') not in resolved_seqs
              and e.get('seq') not in dismissed_seqs]

# Group by person (who you'd discuss with) — use data.person_id or attendee context
# ... (groups items so the user sees 'next time you talk to X, mention these' rather than chronological order)
print(f'OPEN_COUNT={len(open_items)}')
"
```

Capture stdout. Then build a data view:

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"Your list — {n_open} things to bring up later",
    "sub_header": "Stuff you set aside earlier. Click to clear when handled, or skip.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            # Group by person/attendee — items each share a "next time you talk to X" context
            {
                "n": i,
                "icon": "💬",
                "name": person_name,
                "context_tag": f"next time you talk to {person_name} ({item_count})",
                "body_lines": [f"- {item['data']['summary']}" for item in items_for_person],
                "actions": [f"{i} resolved", f"{i} skip"],
            }
            for i, (person_name, items_for_person) in enumerate(grouped, start=1)
        ],
    }],
}
```

### Step 2 — Render through the canonical pipeline

```python
from chat_output_renderer import render_chat_output_widget
html = render_chat_output_widget(data_view, wrapper="fragment")
# Post via mcp__visualize__show_widget
```

The canonical renderer applies all v2.13.0+ validators (action verbs in CANONICAL_ACTIONS, no leak patterns, data-shape OK). Same enforcement as scheduled-task surfaces.

### Step 3 — Write a fire-marker event so apply-choices can identify this surface (v2.14.19+)

Before posting the widget, append a `pack_run` event to events.jsonl with:

```python
{
    "type": "pack_run",
    "ts": <now ISO>,
    "source_skill": "show-my-list",
    "data": {
        "kind": "list",
        "items_surfaced": n_open,
        "fired_via": "user-trigger",
    }
}
```

apply-choices reads the most recent fire-marker (within 60 min) to identify which orchestrator's handler to dispatch through. Without this event, clicking `resolved` or `skip` on a discuss-list item silently no-ops because apply-choices can't tell what surface it came from. (This was the v2.7.x→v2.14.18 silent-no-op bug from the simplify-pass Batch 7 finding #4.)

### Step 4 — Post widget, then STOP

Widget IS the post. No commentary.

If 0 items match: surface plain English `Your list is empty — nothing set aside right now. Things land here when you click "Add to my list" on a daily check-in.` No widget, no fire-marker (nothing to dispatch).

## What this skill does NOT do

- Does NOT modify events.jsonl directly (read-only — clicking widget actions writes new events through apply-choices, that's separate)
- Does NOT cross-reference against open commitments (that's CRU layer scope, v2.14.5+)
- Does NOT auto-surface based on calendar (the user pulls when they want; not pushed)
- Does NOT touch connectors (no Gmail / Calendar / Granola fetches)

Pure local read of events.jsonl + render through canonical pipeline.

## Trigger pattern

`show my list` / `whats on my list` / `what to discuss` / `what do i need to discuss` / `discuss list` / `show discuss list` / `my list`

## Why this pattern

Per M's v2.14.4 design discussion: capture-then-curate beats interrupt-driven action. Throughout the day you click `Add to my list` on items you want to bring up later, but don't want to context-switch on RIGHT NOW. They accumulate silently. When you have a quiet moment (between meetings, end of day, etc.), `show my list` surfaces them grouped by who you'd discuss them with — so the next time you're talking to that person, you've got the queue ready.

This is the pattern productivity systems like the GTD "next-action list" use — capture is cheap, review is batched. v2.14.4 implements it as a Cowork-native flow.

## See also

- `add-to-my-list` action (defined in CHAT_ACTION_WIDGET.md action reference) — the capture verb
- Past Meetings + Pulse orchestrators — primary surfaces that emit items into the list
- `usage report` — separate read of events.jsonl for telemetry; this skill is for action items
