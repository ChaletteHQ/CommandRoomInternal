---
name: commitment-triage
description: "Batch review of the FULL open commitment set, sorted by age — one widget, one Apply, everything dispatched through the single closure path with undo. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'clean up my commitments', 'burn down my commitments'. Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale to-dos (30d+) surface as 'still on your plate?'; every action is an append and the post-Apply ack offers one-tap undo. Also available as an opt-in Friday-afternoon scheduled chat via 'change my schedule'. Does NOT fire on 'show my list' (show-my-list — the curated discuss-later list), 'scan for commitments' (extraction backfill), or the daily Commitments chat (actionable subset with chase drafts — this is the full-set housekeeping pass). Action semantics: Routing section in the body."
---

# commitment-triage

The housekeeping surface for the whole open set (Phase 2 Stage D, S4). The
daily Commitments chat surfaces the actionable SUBSET (capped, filtered,
chase-drafted); this skill renders EVERYTHING open, oldest first, so the user
can burn down rot in one sitting. Client grounding: repeated customer asks
for one-click "move this to done" and complaints that items were "not going
away"; one live workspace opened with 71 open items, many junk — the capture
floor plus this surface is the fix.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. This skill writes events.jsonl ONLY via
`commitment_state` helpers (`close_commitment` / `reopen_commitment` /
`promote_task_to_commitment`) and `atomic_append_jsonl` for
`commitment_updated` defers — every write is an append through the Phase 1
gate. **NO in-place mutation, ever (F4):** flipping `data.status` on an
existing event is the forbidden write class this skill was built to replace.
It also appends suppression rules to `_hq/config/commitment-rules.md`
(atomic write; create the file with a one-line header if absent).

## Step 1 — Load the projected open set

```python
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
import sys; sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from commitment_state import count_commitments, stale_tasks, commitment_kind
from primary_user import resolve_primary_user

opens = load_open_commitments("<WORKSPACE>/_hq/data/events.jsonl")
user_id = resolve_primary_user("<WORKSPACE>")
counts = count_commitments(opens, user_person_id=user_id, now_iso="<now ISO>")
stale = stale_tasks(opens, "<now ISO>")   # 30d+ tasks — "still on your plate?"
```

The loader is THE projector — deferrals and reclassification markers are
already applied (effective due, effective kind). Never re-derive either from
raw events. Header counts come from `count_commitments` (the one counting
API), never hand-rolled.

## Step 2 — Sort + annotate

- Sort by age, oldest first (event ts via `event_time`). No confidence
  filter, no cap-by-bucket — this is the full-set surface. If the set exceeds
  the widget transmission ceiling (~7 items per page per the Bug #14 sizing),
  paginate with `show more` exactly like the Commitments orchestrator.
- Per row context tag: age in days, effective due (or "undated"), kind
  (`task` rows labeled plainly — "task (yours)"), and for `stale` members the
  literal nudge **"still on your plate?"**.
- `pending_review` rows render with their `review_reason` and only offer
  explicit confirm-shaped actions (done / drop / not mine) — an explicit
  click IS user confirmation (`user_confirmed=True`); nothing here
  auto-resolves them.

## Step 3 — Render the widget

Standard all-batch surface per `shared/CHAT_ACTION_WIDGET.md` § "Commitment
Triage": `render_chat_output_widget(data_view, wrapper="fragment")` →
`validate_rendered_widget` → `mcp__visualize__show_widget`, byte-for-byte
(zero-manipulation contract). Actions per row:

- promise/scheduling rows: `resolved` (Done) · `push to [date]` (Defer) ·
  `drop` · `not mine` · `make task` · `never track this` · `skip`
- task rows: `resolved` (Done) · `push to [date]` · `drop` · `promote`
  (Make it a commitment) · `never track this` · `skip`

Every row embeds the commitment's `data.id` VERBATIM (widget identity
contract, Stage B). Pass `source_skill: "commitment-triage"` in the data view
so tuples carry `src` for stateless dispatch (W4).

## Step 4 — Dispatch

Handled by `apply-choices` § `commitment-triage` (all writes through
`commitment_state`; see that section for the exact calls). The consolidated
ack is plain English ("Closed 6, deferred 2, made 3 tasks — the list is down
to N.") and ALWAYS ends with:

> *Say `undo` to reverse this triage.*

`undo` (same chat) reopens every closed item via `reopen_commitment` and
reverses reclassifications — all additive; history keeps both the tombstone
and the reopen. Never narrate event-type names (CONTRACT Rule 4/9).

## Scheduled mode (opt-in — NOT first-install)

Registered via the `change my schedule` → `add commitment-triage` flow
(enable-command-room-schedules Phase 6 add path; DEFAULT_SCHEDULES carries
the Friday 15:00 default). The scheduled fire runs the identical Steps 1–4
via `references/orchestrator-commitment-triage.md` (which adds the standard
late-fire check + pack_run receipt). Weekly cadence is the point: S5's
30-day task staleness and the undated-share target (< 30%) are reviewed here,
not in the daily chats.

## What this skill does NOT do

- No chase drafts, no email surface — that's the daily Commitments chat.
- No extraction — capture floors live in the producers.
- Never renders tasks in "commitment aging" framing — tasks age on THIS
  surface only (S5); CRU never chases them (`cru_match.cru_eligible`).
- Never deletes or rewrites history — additive events only (F4/§3.1).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Batch review of the FULL open commitment set, sorted by age — one widget, one Apply, everything dispatched through the single closure path. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments'. Also runs as an OPT-IN Friday-afternoon scheduled chat (add via `change my schedule` → add commitment-triage; not first-install). Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale tasks (30d+) surface as 'still on your plate?'. Every action is an APPEND (close_commitment / commitment_updated / commitment_reclassified) — this skill exists so the next cleanup chat doesn't rewrite events.jsonl in place (F4). The post-Apply ack offers undo (additive commitment_reopened). DOES NOT fire on 'show my list' (commitment_to_discuss review — show-my-list), 'scan for commitments' (extraction backfill), 'log resolved: <id>' (log-resolution artifact path), or the daily Commitments chat (orchestrator-commitments — actionable subset with chase drafts; triage is the full-set housekeeping pass).
