---
name: commitment-triage
description: "Batch review of the FULL open commitment set, sorted by age — one widget, one Apply, everything dispatched through the single closure path with undo. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'clean up my commitments', 'burn down my commitments'. Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale to-dos (30d+) surface as 'still on your plate?'; every action is an append and the post-Apply ack offers one-tap undo. Also available as an opt-in Friday-afternoon scheduled chat via 'change my schedule'. Does NOT fire on 'show my list' (show-my-list — the curated discuss-later list), 'scan for commitments' (extraction backfill), or the daily Waiting On chat (actionable subset with chase drafts — this is the full-set housekeeping pass). Action semantics: Routing section in the body."
---

# commitment-triage

The housekeeping surface for the whole open set (Phase 2 Stage D, S4). The
daily Waiting On + My Plate chats (CTS1) surface the actionable SUBSET (capped, filtered,
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
and the daily Waiting On / My Plate chats show (F-47 P2b / F-56: four different open
counts in one day came from each surface folding buckets its own way —
unowned and unconfirmed are their own lines everywhere, never folded into
owed-to-you). `pending_review` rows are the `unconfirmed` bucket; they are
excluded from you-owe/owed-to-you until confirmed.

## Step 2 — Sort + annotate (the full-list layout, delivered by design in pages)

The full-list layout is THE triage design — M picked it over the capped
7-item daily-chat variant during the v4.5.1 dogfood (F-18): the full open set
is surfaced, oldest first, nothing dropped. What CHANGED at T2 is delivery, not
the layout: because the widget_code transport carries one page at a time (Bug
#67 — there is no whole-widget carrier), the full list is delivered as pages of
~10 rows (`page=N`, `show more` re-fires the next page). Every open item still
reaches a page in the same order; the ordering, sections, tiles, and verbs
below are unchanged — the reader just pages through them instead of scrolling
one giant widget. One page's worth of the layout:

- **Header stat tiles** from the bucket export: pass `counters` in the data
  view with `Open` / `You owe` / `Owed to you` / `Unowned` / `Unconfirmed`
  (+ `Undated` if room) — values VERBATIM from `counts["headline"]`, never
  hand-rolled.
- **Unconfirmed block — renders FIRST, above every age section (v4.6.1
  W4b escalation; never age-buried):** anything unconfirmed 7+ days pins to
  a dedicated **"Unconfirmed"** section at the TOP of the widget —
  unconfirmed items don't age into the pool, they escalate to confirmation.
  Build it in code: `from confirm_flow import select_unconfirmed_escalation`
  → `esc = select_unconfirmed_escalation(opens, "<now ISO>")`; render
  `esc["pin"]` rows with their `days_unconfirmed` age and `review_reason`
  in plain English ("captured 12 days ago — still unconfirmed"). Verbs are
  the confirm cluster: `mine` / `theirs to [name]` / `make task` / `drop`
  (duplicate-flagged rows: `merge` / `keep both` / `drop`), dispatched
  exactly per apply-choices § the commitments confirm-section handlers
  (source `commitment-triage`). Rows in `esc["propose_drop"]` (30+ days)
  additionally lead with the question **"sat unconfirmed for [N] days —
  drop it?"** — Drop stays a manual click, never automatic. These rows are
  EXCLUDED from the age sections below (no double-surfacing); they still
  count in `headline["unconfirmed"]` and nowhere else.
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
- **Counterparty-unresolved batch (CTS1 §8.2(b) — OPT-IN, resumable, never a wall):**
  after the age sections are built, compute the orphaned-promise set in code:
  `from surface_split import counterparty_unresolved` → `orphans = [c for c in
  opens if counterparty_unresolved(c, user_id)]`. When non-empty, append ONE
  offer row at the BOTTOM of the widget (not a section of 49 rows): **"N of
  your promises have no person attached — knock out a few?"** with actions
  `confirm` (start a bite) / `skip`. On `confirm`, re-render a bite of ~5
  orphan rows (oldest first), each carrying the drip verbs — `reassign to
  [name]` (attaches the named person as COUNTERPARTY on these rows — the CTS1
  dispatch nuance documented in apply-choices § cr-commitments) and
  `make task` (demote to Personal) — plus `drop`. After a bite applies, offer
  the next bite ("M left — another 5?"); any skip ends the run, and the next
  Friday fire re-offers from wherever it left off (resumable by construction:
  resolved rows leave the set). Also summonable on demand: "fix my orphaned
  promises" / "who were these for" in this chat starts a bite directly.
  NEVER auto-demote — Bug #103 says most of these are REAL promises whose
  counterparty linking failed; the human attaches or demotes, one tap each.
- **No size fallback (T2):** the full open set renders by DESIGN as pages of
  ~10 rows (`page=N`), each relayed as `widget_code` per § Transport; `show
  more` re-fires the next page. Never chunk mid-page, never right-size a page
  below its design cap, never drop rows to fit a "transmission ceiling" (those
  ceilings were byte-relay artifacts; a 199-commitment live fire proved the
  relay wall — pagination is the fix). Every open item reaches a page; the
  full-list layout renders the full list, one page at a time.

## Merging duplicates (chat-phrase path; the Merge verb ships in the W4b confirm flow, v4.6.1)

The same real commitment captured by two writers (meeting + email + sweep)
is two open rows. When the user says **"merge those two"**, **"same
commitment"**, **"those are the same thing"**, or clicks the `merge` verb
(confirm section / Unconfirmed block rows), close the duplicate INTO the
survivor:

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
- **Reassign** — "that's actually Quinn's" / "reassign #N to Quinn".
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
  A parent with open sub-items refuses to split (its parts belong to ONE
  deliverable) — the writer's error says so; surface it verbatim.

Prose around these uses the SAME words as the verb rows — fix wording,
reassign, split (F-13 P2a).

## Sub-items (SUB1 — decomposition; the parent STAYS OPEN)

**Add sub-items** — "break #N into: A / B / C" / "add sub-items to #N: …" /
"steps for #N: …". The sibling of Split with the OPPOSITE closure
semantics: split = the capture was wrong, one item is really N peers, the
original CLOSES; sub-items = the capture was right, one real deliverable
with N internal steps, the parent stays open as the commitment of record.
Dispatch: `commitment_state.add_subitems` via apply-choices (its
commitment-triage entry has the exact call). ≥1 step is valid (unlike
split's ≥2); cap 12 open sub-items per parent (loud writer error above —
a 13-step item is a project); one level deep (no grandchildren); creation
is USER-INITIATED only — extraction/sweeps never mint hierarchies.

How the family renders and behaves on this surface:

- The driver nests sub-item rows INSIDE the parent's row (the widget's
  standard sub-row shape); the parent's context tag carries the progress
  chip — "sub-items 1/3 · next: [step]". Child rows carry their own
  `data.id` verbatim (identity contract) and the per-kind dropdown minus
  `never track this` (that stays parent-level; suppression rules key on
  capture shape — children aren't captures). Children are real
  commitments: Done / Later… / Drop work on them with zero special-casing.
- **Family-atomic pagination:** a parent is never split from its sub-items
  across pages — a family that doesn't fit moves whole to the next page
  (structural: pagination slices top-level rows only).
- When the LAST open sub-item closes, the parent row shows
  **"all sub-items done — close it?"** — a PROPOSE, never an auto-close
  (the parent may carry residual work the steps never listed).
- **Done on a parent with open sub-items** raises `OpenSubitemsError` —
  ask the one-line confirm ("this also closes its N open sub-items — go
  ahead?") and only on yes re-dispatch with `close_subitems=True`. The
  cascade closes children first, parent last; batch undo reopens the whole
  family (the cascade's `closed_subitems` ids join the undo cache).
- Orphan sub-items (parent closed through the cascade crash window) render
  as ordinary top-level rows with a "was part of: [parent title]" note —
  real open work, never hidden.
- Sub-items never enter chase (`cru_eligible` excludes them — the
  counterparty cares about the deliverable, not your step list), never
  count in the headline (a parent with 3 open steps is **1** open
  commitment; the header appends "(+N sub-items)" when any exist), and
  never flag as duplicates of their parent or siblings.
- "close #7" in chat where 7 is a parent: the cascade confirm is the
  safety — never resolve a bare ordinal to a child silently.

Prose uses the same words as the verb row — **Add sub-items** (F-13 P2a).


## Step 3 — Render the widget (ONE driver call — T2.2)

**The entire load → project → build → fit → persist pipeline is ONE CLI
invocation** (`shared/scripts/surface_drivers.py` — it executes Steps 1–2's
canonical helpers internally; those steps above remain the normative spec of
what the view contains, never a to-do list of separate commands):

```bash
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
python3 shared/scripts/surface_drivers.py commitments \
    --workspace "<WORKSPACE>" --page 1
```

Stdout carries `CR-PAGINATION: {...}` (one line of JSON — page / total_pages
/ has_more, for the position narration) followed by the persisted page's
validated bytes between `CR-WIDGET-HTML-BEGIN` / `CR-WIDGET-HTML-END`
markers. **Relay the bytes between the markers to `mcp__visualize__show_widget`
as `widget_code`, byte-exact.** `widget_transport.render_and_persist` (all
validators + the byte-budget fit + the audit persist into
`_hq/.system/widgets/`) already ran inside the call — there is nothing else
to prepare. A `show more` reply re-fires the SAME one-command driver with
`--page N+1`.

**Idempotent single call (RV-3 — the double-render fix):** run the driver
exactly ONCE per page per fire. If you already hold the driver's output for
the requested page, relay it — never re-run "to refresh" or "to be safe"
(each re-run persists a duplicate audit page; that IS the double-render
defect). Never hand-compose or post-process the HTML (the zero-manipulation
contract: the persisted file IS the render), and never fall back to
assembling the view yourself with piecemeal commands — the driver is the only
sanctioned build path for this surface.

The rendered widget is the standard all-batch surface per
`shared/CHAT_ACTION_WIDGET.md` § "Commitment Triage": header stat tiles from
the bucket export, the Unconfirmed block first, age sections oldest-first,
a **Done** one-tap button per row (t3 FB-4) with the tail verbs in the
row's `— more —` dropdown (T2.2 row diet; wire format unchanged).
Actions per row (display labels come from
`shared/scripts/verb_taxonomy.py` — never restate them in widget HTML):

- promise/scheduling rows: `resolved` (**Done**, the button) · `push to
  [date]` (**Later…**) · `drop` · `not mine` · `make task` · `never track
  this` (**Never track (permanent)**) — `skip` stays dispatchable but its
  dropdown option is suppressed by the t3 FB-3 merge (**Later…** covers it;
  the footer Snooze rest still mutes).
- task rows: `resolved` (**Done**, the button) · `push to [date]`
  (**Later…**) · `drop` · `promote` (**Make it a commitment**) · `never
  track this` — same `skip` suppression.
- sub-item rows (SUB1, nested under their parent): the same per-kind set
  MINUS `never track this` (suppression keys on capture shape — children
  aren't captures). `add subitems [items]` (**Add sub-items**) is a
  chat-phrase verb like `split into [items]` — it does not render as a
  dropdown option; see § Sub-items.

**Posting-block rule (t3 FB-11):** chat prose around the widget names ONLY
the controls the rendered card visibly offers, using their exact labels —
"tap **Done**, or pick from the row's menu (**Later…**, **Drop**, …)".
Never enumerate verbs the card doesn't show, and never describe a dropdown
row as if it had buttons. Same-vocabulary rule (F-13 P2a) still applies to
every verb you do name. A Later… pick requires a date or a number of days:
the widget holds Apply and names the missing input inline (F-17) — never
mention Apply being "stuck"; the widget explains itself.

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

- No chase drafts, no email surface — that's the daily Waiting On chat (CTS1).
- No extraction — capture floors live in the producers.
- Never renders tasks in "commitment aging" framing — tasks age on THIS
  surface only (S5); CRU never chases them (`cru_match.cru_eligible`).
- Never deletes or rewrites history — additive events only (F4/§3.1).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Batch review of the FULL open commitment set, sorted by age — one widget, one Apply, everything dispatched through the single closure path. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments'. Also runs as an OPT-IN Friday-afternoon scheduled chat (add via `change my schedule` → add commitment-triage; not first-install). Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale tasks (30d+) surface as 'still on your plate?'. Every action is an APPEND (close_commitment / commitment_updated / commitment_reclassified) — this skill exists so the next cleanup chat doesn't rewrite events.jsonl in place (F4). The post-Apply ack offers undo (additive commitment_reopened). DOES NOT fire on 'show my list' (commitment_to_discuss review — show-my-list), 'scan for commitments' (extraction backfill), 'log resolved: <id>' (log-resolution artifact path), or the daily Commitments chat (orchestrator-commitments — actionable subset with chase drafts; triage is the full-set housekeeping pass).
