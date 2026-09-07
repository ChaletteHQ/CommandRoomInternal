---
name: commitment-triage
surfaces: both
description: "Your plate. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments', 'what's on my plate'. Every open commitment grouped by what it wants next — DO IT / CHASE / WAIT / SCHEDULE / CONFIRM / PARKED, then by project, then Overdue / This week / Later / No date. One widget, four verbs (done / later / drop / not mine), one Apply, everything through the single closure path with undo. On demand only (the opt-in Friday chat is retired). Does NOT fire on 'clean up my commitments' / 'sweep my backlog' / 'commitment backlog' / 'backlog sweep' / 'commitment amnesty' (commitment-backlog-sweep — the mail-history evidence pass), 'show my list' (show-my-list — the curated discuss-later list), 'scan for commitments' (extraction backfill), or the daily Waiting On chat (the actionable subset, with chase drafts)."
---

# commitment-triage

**This surface is the plate** (SPEC PLATE1, 2026-09-03). Every open
commitment renders in ONE shape — the action block it wants next, then its
project, then its horizon — and every row carries the same four verbs. The
brief, the day-close, the Friday wrap and the board read the SAME shape from
the same code (`shared/scripts/plate_view.py`); this skill is the full,
verb-bearing view of it. Client grounding: repeated customer asks for
one-click "move this to done", complaints that items were "not going away",
and the operator book at 279 rows sorted by age — age is a maintenance
signal, not a plan.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. This skill writes events.jsonl ONLY via
`commitment_state` helpers (`close_commitment` / `reopen_commitment` /
`promote_task_to_commitment` / `supersede_commitment`) and
`atomic_append_jsonl` for `commitment_updated` defers — every write is an append through the Phase 1
gate. **NO in-place mutation, ever (F4):** flipping `data.status` on an
existing event is the forbidden write class this skill was built to replace.
It also appends suppression rules to `_hq/config/commitment-rules.md`
(atomic write; create the file with a one-line header if absent).

## Step 1 — The plate model (ONE grouping, in code)

```python
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}")
import sys; sys.path.insert(0, "shared/scripts")
from plate_view import build_plate, render_plate
view = build_plate("<WORKSPACE>", now_iso="<now ISO>")   # resolves the primary user itself
out = render_plate(view, "plate", True)                    # owns every word
```

`build_plate` is THE grouping — no surface re-derives a block. It calls the
canonical readers in this order and nothing else: `load_open_commitments` →
`split_pending_review` → `render_clusters` → `classify_commitments` →
`bucket_of`, reads the full history through `events_io`, and places every
top-level open row in exactly ONE block:

- **CONFIRM** — a row carrying a question (`data.question`), an extractor's
  guess (`pending_review`), or no owner on record. A row with a question is
  a question, not work: CONFIRM beats every other rule. Overdue questions
  render FIRST inside the block with the OVERDUE badge, and their **Done**
  confirms and closes in one tap (P3).
- **DO IT** — you owe it.
- **CHASE** — owed to you and quiet longer than the other party's cadence or
  5 days, whichever is shorter.
- **WAIT** — owed to you, still inside that window (a nudge you already sent
  resets the clock).
- **SCHEDULE** — `kind: scheduling`, not on the calendar yet.
- **PARKED** — a row you said isn't yours (`not mine` — it waits for someone
  to claim it, never lapses to dropped), a parked hint, or an undated personal
  task with no movement for 30 days. **PARKED renders open with its reason
  line, never hidden** (P2 / OVERDUE1 D3: the resting item stays on the book).

Level 2 is the project (`primary_thread_id`; "No project" last). Level 3 is
the horizon from the effective due in the workspace timezone: Overdue · This
week · Later · No date. One line per real-world item (CLUSTER1, default-on):
a cluster's survivor carries "+N more like it". Sub-items ride their parent
as a `steps done/total` chip and never render as rows (SUB1). Observed-tier
rows never render (D5).

**Header numbers** come from `count_commitments(..., user_person_id=...)`
— the view carries `counts` (the `["headline"]` export verbatim) and
`block_totals`; the block totals partition that same headline, pinned by
`tests/run_plate1_test.py`. Never hand-roll a number, never fold unowned or
a guess into a direction.

**No primary user → no lanes.** `build_plate` REFUSES when the workspace's
primary user cannot be resolved (`{"error": <one plain line>}`); the
renderer prints that one line and nothing else. Never render a plate that
reads "you owe 0 / owed to you N" because the pointer was unset — say the
line, and let the update bridge's `write_user_pointer` action (or `set my
name`) fix the pointer.

## Step 2 — The shape and the words (`render_plate` owns both)

`render_plate(view, surface, verbs)` is the ONLY renderer. Surfaces pass
`surface` (`plate` here; `brief` / `eod` / `wrap` / `board` for the other
adoptions) and `verbs`. It composes:

- the block titles and their one-clause subtitle ("DO IT (12) — you owe
  these"), the project headings, the horizon labels;
- one row line: title · the other person's NAME · due · badges · "+N more
  like it" · steps chip · the reason line · one evidence chip (the newest
  open proposal's evidence, else the last movement — "you nudged them Aug
  28"). Never an id, a score, a seq, or a tier word;
- the collapsed-count line for WAIT and SCHEDULE ("12 waiting — say `show
  waiting`"); DO IT, CHASE, CONFIRM and PARKED open by default (D2, P2);
- the verb strip: exactly **Done · Later… · Drop · Not mine** on every row
  (D4); CONFIRM rows carry Done · Drop · Not mine with the one-line
  "fewer options" note (F-59).

A jargon gate runs on the renderer's own words: the words *unconfirmed*,
*stuck*, *unowned*, *pending_review*, ids and scores never render (P4 —
NUMBERS1's plain-words list is folded in). Stored review reasons are re-said
in plain words for display only; the stored clause is never rewritten.

**Orphaned promises (CTS1 §8.2(b), on demand, resumable):** rows with no
person attached (`from surface_split import counterparty_unresolved`) are a
CONFIRM question like any other; the bite-sized batch — "fix my orphaned
promises" / "who were these for" — still runs from this chat exactly as
before: ~5 oldest orphan rows per bite with `reassign to [name]` / `make
task` / `drop`, resumable by construction, never auto-demoted (Bug #103).

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


## Step 3 — Render the widget (ONE driver call)

**The entire load → group → render → fit → persist pipeline is ONE CLI
invocation** (`shared/scripts/surface_drivers.py plate` — it runs Step 1's
`build_plate` + `render_plate` internally and hands the data view to
`widget_transport.render_and_persist`; Steps 1–2 above are the normative
spec of what the view contains, never a to-do list of separate commands):

```bash
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}")
python3 shared/scripts/surface_drivers.py plate \
    --workspace "<WORKSPACE>" --page 1
```

Stdout carries `CR-PAGINATION: {...}` (one line of JSON — page / total_pages
/ has_more, for the position narration) followed by the persisted page's
validated bytes between `CR-WIDGET-HTML-BEGIN` / `CR-WIDGET-HTML-END`
markers. **Relay the bytes between the markers to `mcp__visualize__show_widget`
as `widget_code`, byte-exact.** `widget_transport.render_and_persist` (all
validators + the byte-budget fit + the audit persist into
`_hq/.system/widgets/`) already ran inside the call — there is nothing else
to prepare. The plate is delivered by DESIGN as pages of up to
`chat_output_renderer.DEFAULT_PAGE_SIZE` rows in block order (CONFIRM → DO
IT → CHASE → WAIT → SCHEDULE → PARKED); a `show more` reply re-fires the
SAME one-command driver with `--page N+1`, which slices the page-set page 1
froze — not a fresh read (PAGESNAP; see `shared/CHAT_ACTION_WIDGET.md` § "A
page-set is ONE question asked ONCE"). If `CR-PAGINATION` carries
`refreshed`, `suppressed`, or `clamped`, SAY it in one line before the rows.
Never chunk mid-page, never drop rows to fit — every open item reaches a
page.

**Idempotent single call (RV-3 — the double-render fix):** run the driver
exactly ONCE per page per fire. If you already hold the driver's output for
the requested page, relay it — never re-run "to refresh" or "to be safe"
(each re-run persists a duplicate audit page; that IS the double-render
defect). Never hand-compose or post-process the HTML (the zero-manipulation
contract: the persisted file IS the render). **The widget is relayed
byte-exact and NEVER re-assembled from pieces** — not "with earlier
styling", not "from the driver's rows", not to fit: a page assembled by
hand carries no F-17 hold, so a Later… with no date reaches Apply (CUT-C
item 5, ATTENDED_TEST_v5.28.0 B2.5). When `CR-PAGINATION` carries
`over_budget`, the driver ALSO prints the page's TEXT form on stdout between
`CR-WIDGET-TEXT-BEGIN` and `CR-WIDGET-TEXT-END` (this is the transport's
`transport["text"]`: numbered by the same display numbers the persisted page
holds, every row's verbs named including Follow-up call / Nudge) — relay
EXACTLY the block between those two markers, byte-exact, instead of the
widget, and say in one line that the page came as text because it was too
large for the widget; a typed `follow-up call 265` then dispatches by number
against the persisted page (CUT-C item 7, B2.6; REVIEW_CUTC F-2: the markers
are the only place the text form reaches you — never compose one). Never fall
back to assembling the view yourself with piecemeal commands — the driver is
the only sanctioned build path for this surface.

**Narration around the widget:** one line naming what the page is ("CONFIRM
first — 9 questions, 3 of them overdue; then DO IT"), the collapsed-count
lines the render carries (`quick_read`), and nothing about lanes, buckets or
tiles. The three tiles the widget shows are DO IT / CHASE / WAIT — the only
numbers on this surface (D9); the brief shows one attention number and a
pointer instead (NUMBERS1 R-1), never these three.

Every row embeds the commitment's `data.id` VERBATIM (widget identity
contract, Stage B) with `source_skill: "commitment-triage"` so tuples carry
`src` for stateless dispatch (W4). Row verbs (display labels from
`shared/scripts/verb_taxonomy.py` — never restated in widget HTML):

- every row: `resolved` (**Done**, the button) · `push to [date]`
  (**Later…**) · `drop` · `not mine` — the four verbs, no more (D4);
- CONFIRM rows: `resolved` · `drop` · `not mine` (Done confirms + closes).

**Board rows (BOARD1 — the artifact still renders the pre-plate row set
until night 2 adopts the board; its verb lines are kept here only so the
board's parity pin has a source):**

- promise/scheduling rows: `resolved` · `push to [date]` · `drop` · `not
  mine` · `make task` · `never track this` — `skip` stays dispatchable but
  its dropdown option is suppressed (t3 FB-3).
- task rows: `resolved` · `push to [date]` · `drop` · `promote` · `never
  track this` — same `skip` suppression.
- sub-item rows (SUB1, nested under their parent): the same per-kind set
  MINUS `never track this`. `add subitems [items]` is a chat-phrase verb
  like `split into [items]` — see § Sub-items.

**Posting-block rule (t3 FB-11):** chat prose around the widget names ONLY
the controls the rendered card visibly offers, using their exact labels —
"tap **Done**, or pick from the row's menu (**Later…**, **Drop**, **Not
mine**)". Never enumerate verbs the card doesn't show, and never describe a
dropdown row as if it had buttons. Same-vocabulary rule (F-13 P2a) applies
to every verb you name. A Later… pick requires a date or a number of days:
the widget holds Apply and names the missing input inline (F-17) — never
mention Apply being "stuck"; the widget explains itself.

## Step 4 — Dispatch

Handled by `apply-choices` § `commitment-triage` (all writes through
`commitment_state`; see that section for the exact calls). The four verbs:

- **Done** → `close_commitment(..., resolution="done", user_confirmed=True,
  resolved_by_match="id")` — the id came off the persisted page (CLOSEID2).
  On a CONFIRM row it confirms and closes in ONE tap: a row whose `pending`
  flag is true (an extractor's guess) goes through
  `needs_review_queue.done_items(..., source_skill="apply-choices")` (the
  DONE1 twin — that writer accepts only the dispatcher's own name); every
  other CONFIRM row (no owner on record, a system question) is a real item
  and closes through `close_commitment` like any row.
- **Later…** → `apply_later` (your own item moves its date; someone else's
  leaves the view until then).
- **Drop** → `close_commitment(..., resolution="dropped")`.
- **Not mine** → `commitment_state.disown_commitment` — the owner is
  cleared and the row PARKS with "whose is this?"; it never closes and the
  unconfirmed drain never lapses it (P3). Name the real owner ("that's
  Quinn's") and it ROUTES via `reassign to [name]` instead.

Two blocks carry ONE more tap, because they want one more thing than a
decision (D4 — the wire ids are the shipped ones, not new verbs):

- **CHASE → Nudge** (`nudge`) — drafts the chase email on click, draft
  posture, nothing sends. Same handler as the Waiting On delegated row.
- **SCHEDULE → Follow-up call** (`follow-up call`) — drafts the invite
  request for a meeting that was agreed and never booked. Nothing books
  itself.

Neither renders on any other block, and `verbs=False` surfaces (the board,
the day-close, the wrap, a would-hold read) carry neither. CONFIRM's own
one-tap is the attribution pick-list, which the capture lane owns; until it
lands CONFIRM shows its three verbs.

The consolidated ack is plain English ("Closed 6, moved 2, parked 1 — DO IT
is down to N.") and ALWAYS ends with:

> *Say `undo` to reverse this.*

`undo` (same chat) reopens every closed item via `reopen_commitment`, hands a
disowned item back to its previous owner via `confirm_commitment_owner`,
reverses reclassifications, AND lifts every mute the batch wrote (via
`mute_ledger.clear_dismissals`) — all additive; history keeps the tombstone,
the reopen, and the clear. Never narrate event-type names (CONTRACT Rule
4/9). `show waiting`, `show scheduling`, `not mine` and `undo <block>` are
replies on an open plate — never treat one as a fresh trigger.

## Publish the board (BOARD1 — the artifact surface, copy-paste apply)

Fires on **"publish my triage board"** / **"put my triage on a page"** /
**"refresh my board"**. Same surface, same view, different serialization: the
full open set renders as ONE self-contained page with working controls (no
byte-relay ceiling, so nothing pages and nothing drops), and it lives at a
stable URL you can read from a phone between sessions.

```bash
# Rule 22 preamble REQUIRED before this runs (as in Step 3).
python3 shared/scripts/surface_drivers.py commitments \
    --workspace "<WORKSPACE>" --format artifact
```

Stdout carries `CR-BOARD: {...}` (one line of JSON — the generated-at stamp,
the per-tab counts, the persisted path) followed by the board's validated
bytes between `CR-BOARD-HTML-BEGIN` / `CR-BOARD-HTML-END`. Those markers are
deliberately NOT the widget's: board bytes go to the **Artifact** tool, widget
bytes go to `show_widget`, and relaying either into the other is the wrong
surface. Everything the widget path validates has already run inside the call
(the pre-render gate family, the leak scan, the board's structural contract,
the persist into `_hq/.system/widgets/`) — there is nothing to prepare and
nothing to post-process. **Never hand-edit the HTML.**

Publish it with the Artifact tool as a **default-private** page, and pass back
the URL stored in **`_hq/config/artifact_board.json`** (read it with
`artifact_board.load_board_url`, write it with `save_board_url`) so a redeploy
keeps the SAME link — a board that moves every week is not a bookmark. **Never
share, publicize, or widen the board**: it renders the user's real client and
person names, and it is theirs alone. If the running surface has no Artifact
tool, say so plainly and give the saved file path — never silently skip the
publish and never describe an unpublished file as a board.

What the board does and does not do:

- **It never writes.** Selections are local to the page; the Copy button
  composes the exact `apply choices: [...]` line and the user pastes it into
  any Command Room chat. Dispatch is the existing `apply-choices` §
  `commitment-triage` path, unchanged, so cascade confirms, Later-date
  validation, ambiguous reassign and `undo` all keep working.
- **A board is stale by design.** It shows the list as of its stamp — which
  the page prints prominently — and does not refresh itself. A pasted tuple
  for something already closed acks honestly ("that one was already closed")
  and writes nothing; say that in one line and move on.
- **Layout:** two pinned strips above three tabs — **Unconfirmed** (the 7d+
  escalation pins) and **Unowned**, never folded into a tab (F-47 P2b /
  F-56) — then **My Tasks** / **I Owe** / **Owed to Me**, each keeping its age
  sections oldest-first. One Copy button covers every tab.
- **Auto-republish is OFF.** The board is on-demand only until the user asks
  otherwise.

## Scheduled mode — RETIRED (SPEC TASKRET1, M's ruling 2026-08-17)

**There is no scheduled mode.** The weekly Friday 3 PM chat is READINESS-
retired: a pass over the whole open set only helps once the sorting that
decides what belongs on that set is trustworthy, and that work is still in
flight. `commitment-triage` is out of `DEFAULT_SCHEDULES`,
`references/orchestrator-commitment-triage.md` is a retirement stub, and
`add commitment triage` is refused warmly by change-schedule with
`schedule_config.retirement_line("commitment-triage")`.

**Everything above this section is untouched and fully live.** Steps 1–4 run
exactly as specified whenever the user says `triage my commitments` — same
full open set, same verbs, same undo. Only the weekly fire is gone, and with
it the late-fire check and `pack_run` receipt that only a scheduled fire ever
needed.

The two things the weekly cadence used to own — S5's 30-day task staleness
("still on your plate?") and the undated-share target (< 30%) — are reviewed
on the on-demand run, in the same Steps. They were never separate machinery;
they were sections of this pass that happened to be read on a Friday.

## What this skill does NOT do

- No chase drafts, no email surface — that's the daily Waiting On chat (CTS1).
- No extraction — capture floors live in the producers.
- Never renders tasks in "commitment aging" framing — tasks age on THIS
  surface only (S5); CRU never chases them (`cru_match.cru_eligible`).
- Never deletes or rewrites history — additive events only (F4/§3.1).

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

**In-chat replies (PLATE1)** — DOES NOT fire on 'show waiting' / 'show scheduling' / 'not mine' / 'undo the parked block' (replies on an OPEN plate — no skill's trigger; ROUTEMISS1 hand rows in tests/triggers.yaml pin that this skill never fires on them).

**Board triggers (BOARD1)** — 'publish my triage board' / 'put my triage on a page' / 'refresh my board' / 'triage board' fire § Publish the board, NOT the widget path. Same surface, different serialization. These live here rather than in the description because the description sits within 13 characters of the G11a cap: adding them needs a deliberate trim decision, not a silent one. DOES NOT fire on 'board pack' / 'board deck' / 'prep the board meeting' (board-pack-assembler — a governance document, not this list).

> Your plate — the FULL open commitment set grouped by the action it wants next (PLATE1; formerly sorted by age) — one widget, one Apply, everything dispatched through the single closure path. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments', 'what's on my plate' (QUICKCMD2, 2026-08-28 — the on-demand ask the my-plate scheduled task never had a chat trigger for). On demand only: the OPT-IN Friday-afternoon scheduled chat was retired in TASKRET1 (2026-08-17) until the review-tier backlog model settles — `add commitment triage` is refused warmly and the skill is otherwise unchanged. Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale tasks (30d+) surface as 'still on your plate?'. Every action is an APPEND (close_commitment / commitment_updated / commitment_reclassified) — this skill exists so the next cleanup chat doesn't rewrite events.jsonl in place (F4). The post-Apply ack offers undo (additive commitment_reopened). DOES NOT fire on 'clean up my commitments' / 'sweep my backlog' / 'commitment backlog' / 'backlog sweep' / 'commitment amnesty' (commitment-backlog-sweep — the backwards-looking pass that reads months of mail history for delivery evidence, closes what the evidence settles, and surfaces duplicates and months-quiet items; triage reads no mail and closes nothing on evidence), 'show my list' (commitment_to_discuss review — show-my-list), 'scan for commitments' (extraction backfill), 'log resolved: <id>' (log-resolution artifact path), or the daily Commitments chat (orchestrator-commitments — actionable subset with chase drafts; triage is the full-set housekeeping pass).
