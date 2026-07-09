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
`promote_task_to_commitment` / `supersede_commitment`) and
`atomic_append_jsonl` for `commitment_updated` defers — every write is an append through the Phase 1
gate. **NO in-place mutation, ever (F4):** flipping `data.status` on an
existing event is the forbidden write class this skill was built to replace.
It also appends suppression rules to `_hq/config/commitment-rules.md`
(atomic write; create the file with a one-line header if absent).

## Step 1 — Load the projected open set

```python
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
import sys; sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from commitment_activity import derive_commitment_movement
from commitment_state import count_commitments, stale_tasks, commitment_kind
from primary_user import resolve_primary_user

opens = load_open_commitments("<WORKSPACE>/_hq/data/events.jsonl")
user_id = resolve_primary_user("<WORKSPACE>")
# v4.6.0 MC2 — THE per-commitment movement map (one derivation, every surface):
# powers headline["stuck"]/["blocked"] and the stale-task age below.
movement = derive_commitment_movement("<WORKSPACE>/_hq/data/events.jsonl")
counts = count_commitments(opens, user_person_id=user_id, now_iso="<now ISO>",
                           movement=movement)
stale = stale_tasks(opens, "<now ISO>", movement=movement)   # 30d+ no-MOVEMENT tasks — "still on your plate?"
```

The loader is THE projector — deferrals and reclassification markers are
already applied (effective due, effective kind). Never re-derive either from
raw events. Header counts come from `counts["headline"]` — the one bucket
export (v4.5.2 R4 + v4.6.0 MC2): render `total` / `you_owe` / `owed_to_you` /
`unowned` / `unconfirmed` (plus `overdue` and `stuck` where the header shows
them) VERBATIM, never hand-rolled. `stuck` is the real movement metric (no
movement 21+ days, or blocked on a named person; `blocked` ⊆ `stuck`) — omit
the segment when the key is absent (not computed, never 0). These are by construction the same numbers the morning brief
and the daily Commitments chat show (F-47 P2b / F-56: four different open
counts in one day came from each surface folding buckets its own way —
unowned and unconfirmed are their own lines everywhere, never folded into
owed-to-you). `pending_review` rows are the `unconfirmed` bucket; they are
excluded from you-owe/owed-to-you until confirmed.

## Step 2 — Sort + annotate (ONE design: the full-list layout)

The full-list layout is THE triage design — M picked it over the 7-per-page
variant during the v4.5.1 dogfood (F-18) and the paginated design is
retired. One scrollable widget:

- **Header stat tiles** from the bucket export: pass `counters` in the data
  view with `Open` / `You owe` / `Owed to you` / `Unowned` / `Unconfirmed`
  (+ `Undated` if room) — values VERBATIM from `counts["headline"]`, never
  hand-rolled.
- **Age sections**, oldest first (event ts via `event_time`): a `30+ DAYS
  OLD` section title above the aged block, the rest below. No confidence
  filter, no cap-by-bucket — this is the full-set surface.
- Per row context tag: age in days, effective due (or "undated"), kind
  (`task` rows labeled plainly — "task (yours)"), and for `stale` members the
  literal nudge **"still on your plate?"**. Stale keys on days since last
  MOVEMENT (v4.6.0 MC2 — the same derivation as the stuck metric; capture ts
  is the floor), so a task the user touched last week never gets the nudge.
  For stuck rows, `commitment_activity.classify_commitments(opens, movement,
  now_iso)` gives the row detail (days_since_movement, blocked_on) — same
  map, never a re-derivation.
- `pending_review` rows render with their `review_reason` and only offer
  explicit confirm-shaped actions (done / drop / not mine) — an explicit
  click IS user confirmation (`user_confirmed=True`); nothing here
  auto-resolves them. Every such row MUST pass `reduced_verbs_reason` so the
  reduced verb set is explained on-surface in one line (F-59), e.g.:
  *"Fewer options — the owner is unconfirmed; clicking Done, Drop, or Not
  mine confirms it."*
- Rows carrying `data.suspected_duplicate_of` (capture-time semantic dedup,
  v4.6.0 C4) render as **"looks like a duplicate of [other item's title] —
  merge, or keep both?"** with the suspect NEXT TO the item it points at,
  never age-buried. "Keep both" = clear the flag via `commitment_updated`
  (pending_review cleared, note "confirmed distinct"); merge = the flow
  below.
- **Size fallback only (not a design):** if the full list exceeds the widget
  transmission ceiling (~57KB, F-47's observed limit), chunk at the largest
  count that transmits (40+ rows is proven) and offer `show more` — same
  layout per chunk, never a different design.

## Merging duplicates (interim surface until the W4b confirm flow ships)

The same real commitment captured by two writers (meeting + email + sweep)
is two open rows. When the user says **"merge those two"**, **"same
commitment"**, **"those are the same thing"**, or clicks a future Merge verb,
close the duplicate INTO the survivor:

```python
from commitment_state import supersede_commitment
supersede_commitment("<WORKSPACE>", survivor_id, superseded_id,
                     merged_by=user_id, source_skill="commitment-triage",
                     evidence="user merged in triage", user_confirmed=True)
```

Survivor choice: default to the item with the richer capture (resolved
counterparty + due date beats a bare sweep recovery); when in doubt, ask in
one line ("keep the one due Jul 8?"). The superseded item closes; the
survivor keeps its id and carries the absorbed source(s) — the loader folds
`merged_source_refs`/`merged_from` onto it, so prep and chase see the full
provenance. NEVER merge by closing one side with `resolution: "done"` — a
duplicate was not done, and the survivor would lose the provenance union.
Idempotent: re-merging an already-merged pair acks honestly ("already
merged"). Never auto-merge — a suspected-duplicate flag is a question, not
a verdict.

## Lifecycle corrections (v4.6.0 S4 — fix wording · reassign · split)

Three chat-phrase verbs for the captures that landed WRONG (all registered
in `verb_taxonomy`, all dispatched through `commitment_state` via
apply-choices — see its commitment-triage entry for the exact calls):

- **Fix wording** — "fix the wording on #N: <corrected text>" / "that should
  say <text>". Mis-extractions were uncorrectable before S4; this appends a
  wording update the projector folds in (newest wins), and the original
  stays in history. Ack shows the corrected line.
- **Reassign** — "that's actually Erick's" / "reassign #N to Erick".
  `Not mine` DISCARDS; reassign ROUTES: the item leaves your you-owe and
  lands on the named owner. Resolve the name via the standard entity path
  (ambiguous → ask, never guess); an explicit name from the user dispatches
  `confirmed=True`. Anything inferred stays unconfirmed — it counts in the
  unconfirmed bucket and is NEVER chased until confirmed (no auto-email on
  a guessed owner). W4b's Theirs → [name] confirm verb lands on this same
  event.
- **Split** — "split #N into: A / B / C". Extraction pre-split stays the
  doctrine (M decision 2026-07-09); this is the manual correction for the
  capture that landed as one atomic item but is really N. Each part becomes
  its own complete commitment carrying the original's provenance; the
  original closes with a "split into …" note. Needs at least two parts.

Prose around these uses the SAME words as the verb rows — fix wording,
reassign, split (F-13 P2a).


## Step 3 — Render the widget

Standard all-batch surface per `shared/CHAT_ACTION_WIDGET.md` § "Commitment
Triage": `render_chat_output_widget(data_view, wrapper="fragment")` →
`validate_rendered_widget` → `mcp__visualize__show_widget`, byte-for-byte
(zero-manipulation contract). Actions per row (display labels come from
`shared/scripts/verb_taxonomy.py` — never restate them in widget HTML):

- promise/scheduling rows: `resolved` (**Done**) · `push to [date]`
  (**Defer**) · `drop` · `not mine` · `make task` · `never track this`
  (**Never track (permanent)**) · `skip` (**Snooze (1 day)**)
- task rows: `resolved` (**Done**) · `push to [date]` (**Defer**) · `drop` ·
  `promote` (**Make it a commitment**) · `never track this` · `skip`

Chat prose around the widget uses the SAME words as the buttons — done,
defer, drop, not mine, make task, never track, snooze (F-13 P2a). A Defer
click requires a date: the widget holds Apply and names the missing date
inline (F-17) — never mention Apply being "stuck"; the widget explains
itself.

Every row embeds the commitment's `data.id` VERBATIM (widget identity
contract, Stage B). Pass `source_skill: "commitment-triage"` in the data view
so tuples carry `src` for stateless dispatch (W4).

## Step 4 — Dispatch

Handled by `apply-choices` § `commitment-triage` (all writes through
`commitment_state`; see that section for the exact calls). The consolidated
ack is plain English ("Closed 6, deferred 2, made 3 tasks — the list is down
to N.") and ALWAYS ends with:

> *Say `undo` to reverse this triage.*

`undo` (same chat) reopens every closed item via `reopen_commitment`,
reverses reclassifications, AND lifts every mute the batch wrote (via
`mute_ledger.clear_dismissals` — v4.6.0 S4, the F-20 P3a fix: undo used to
reopen items while their snoozes stayed in force) — all additive; history
keeps the tombstone, the reopen, and the clear. Never narrate event-type
names (CONTRACT Rule 4/9).

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
