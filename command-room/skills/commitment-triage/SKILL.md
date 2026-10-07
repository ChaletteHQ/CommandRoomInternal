---
name: commitment-triage
surfaces: both
description: "Your plate. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments', 'what's on my plate'. Every open commitment grouped by what it wants next — DO IT / CHASE / WAIT / SCHEDULE / CONFIRM / PARKED, then by project, then Overdue / This week / Later / No date. One board, one line a row, one tap (Done); every other verb typed by the row's number. `work my plate` opens the nine-row page; 'show waiting' and 'show scheduling' answer standalone. Every write through the single closure path with undo. On demand only. Does NOT fire on 'clean up my commitments' / 'sweep my backlog' / 'commitment backlog' / 'backlog sweep' / 'commitment amnesty' (commitment-backlog-sweep — the mail-history evidence pass), 'show my list' (show-my-list — the curated discuss-later list), 'scan for commitments' (extraction backfill), or the daily Waiting On chat (the actionable subset, with chase drafts)."
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
# Run the Access preamble first (CONTRACT Rule 22 v6, the block in shared/WORKSPACE_ACCESS.md): it resolves $PLUGIN_ROOT, exports CR_ENV, and cds there.
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

**THE DEFAULT RENDER IS THE BOARD (M's ruling, 2026-09-07).** The plate you
show when someone says "what's on my plate" is one look, not a page: every
open row grouped by project (or by person, where this workspace's brief is
organised that way — the board reads the same customization the brief
does), one line per row, **one tap on a row (Done) and nothing else**, and
as many rows as the widget's size budget measurably holds. When it does not
all fit, the board's own line says how many more there are and names the two
doors — `work my plate` and `show parked`. There is no paging on the board:
never offer "show more", never ask for a page number.

**Every other verb is TYPED, by the row's number** — "later 12 to friday",
"drop 7", "not mine 19", "nudge 4", "follow-up call 22". The numbers are the
row's own and are the same on the board, on the working page and in the text
form, so a number a reader took off the board still means that row after any
re-render.

**The entire load → group → render → fit → persist pipeline is ONE CLI
invocation** (`shared/scripts/surface_drivers.py plate` — it runs Step 1's
`build_plate` internally and hands the data view to
`widget_transport.render_and_persist`; Steps 1–2 above are the normative
spec of what the view contains, never a to-do list of separate commands):

```bash
# Run the Access preamble first (CONTRACT Rule 22 v6, the block in shared/WORKSPACE_ACCESS.md): it resolves $PLUGIN_ROOT, exports CR_ENV, and cds there.
python3 shared/scripts/surface_drivers.py plate --workspace "<WORKSPACE>"
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

Stdout carries `CR-PAGINATION: {...}` (empty for the board — it is not
paged) followed by the persisted view's validated bytes between
`CR-WIDGET-HTML-BEGIN` / `CR-WIDGET-HTML-END` markers. **Relay the bytes
between the markers to `mcp__visualize__show_widget` as `widget_code`,
byte-exact.** `widget_transport.render_and_persist` (all validators + the
audit persist into `_hq/.system/widgets/`) already ran inside the call —
there is nothing else to prepare.

**On a merged seat the bytes go to `show_widget` and nowhere else** — no
device copy of the page, no staging file under the workspace, nothing written
beside the persist the call already did (FIX3 F3-12, preamble rule 7).

## `show waiting` and `show scheduling` — they answer on their own

**Ruled by M, 2026-09-13 (SPEC SURFACEFIX1 5.5).** Both phrases have been
PRINTED under the plate's WAIT and SCHEDULE blocks since PLATE1 as the way
to see the rest, the manifest announced `show waiting` as answering any
time, and every morning brief points at it — and until this build nothing
claimed either one. Typed on its own, `show waiting` answered *"No open
plate in this session"* (attended test, B2.9). **They are this skill's, and
they answer standalone.**

- `show waiting` → the Waiting On view: `surface_drivers.py waiting-on`.
- `show scheduling` → the schedule view: `surface_drivers.py schedule`.

Both go out through `run_surface`, so the leak gate and the transport see
them exactly as they see the plate. **Nothing changes on the reply path:**
on an OPEN plate these are still replies, routed by
`plate_view.reply_surface` off `PLATE_REPLY_SURFACES`, and they still
re-fire that surface instead of re-rendering the board over the page the
reader is answering. One phrase, one answer, whichever way it arrives.

Never point a reader at a phrase that does not answer — that is the whole
of why this section exists.

**THE CARD IS THE ANSWER — never re-type its rows in chat (SCHEDVIEW1 5.1).**
Both views come back from `surface_drivers` with their rows inside
`sections[*]["items"]`, already carrying each row's `display_n` from
PLATENUM1's persisted map, and `widget_transport.render_and_persist` renders
them. Relay that and say nothing else about the rows. Do NOT list them as
text, do NOT summarise them, and above all do NOT number them yourself: a
number you count off the page is a POSITIONAL number, and a verb typed
against it resolves through `plate_view.resolve_display_number` onto a
different item. If the card comes back with no items, say the surface is
empty — never fill it in by hand. (On 2026-09-16 the schedule view returned
its rows under the wrong key, the card rendered empty, and the chat
hand-printed nine rows numbered 1..9 over a book whose real numbers were in
the 130s. The key is fixed; this paragraph is what stops the improvisation
from coming back on the next empty card.)

**`work my plate` — the working page.** A reply of `work my plate` on an
open board (never a fresh trigger) re-fires the SAME driver on the
`plate-page` surface, which is the nine-row page with the full button set,
unchanged: four verbs a row plus the block's one tap, in block order
(CONFIRM → DO IT → CHASE → WAIT → SCHEDULE → PARKED), paged.

```bash
python3 shared/scripts/surface_drivers.py plate-page \
    --workspace "<WORKSPACE>" --page 1
```

On THAT surface the plate is delivered by DESIGN as pages of up to
`chat_output_renderer.DEFAULT_PAGE_SIZE` rows; a `show more` reply re-fires
it with `--page N+1`, which slices the page-set page 1 froze — not a fresh
read (PAGESNAP; see `shared/CHAT_ACTION_WIDGET.md` § "A page-set is ONE
question asked ONCE"). If `CR-PAGINATION` carries `refreshed`,
`suppressed`, or `clamped`, SAY it in one line before the rows. Never chunk
mid-page, never drop rows to fit — every open item reaches a page.

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

**A TYPED number never resolves against the page you are holding
(PLATENUM1 4.2).** A click carries `data.id` verbatim (above) and needs
nothing further. A TYPED verb — `later 83 to friday`, `drop 83`, `not mine
83` — carries only the number the reader read off a render, and that
render can be hours old: close row 5 and rebuild, and the row that used to
be 83 is a different item. Resolve every typed number through
`plate_view.resolve_display_number(workspace_root, n)` (strips a leading
`#`), which reads the persisted `id ↔ number` map — never by re-deriving a
position from the page-set or the last widget you rendered. `None` back
means no row in this workspace has ever carried that number: say so plain
("I don't have a row numbered 83 — say `work my plate` to see the current
list") rather than guessing the nearest one.

- the board (the default render): `resolved` (**Done**) and NOTHING ELSE —
  one tap a row, or none (M's ruling 2026-09-07). Every other verb is typed
  by the row's number;
- the `work my plate` page, every row: `resolved` (**Done**, the button) ·
  `push to [date]` (**Later…**) · `drop` · `not mine` — the four verbs,
  no more (D4);
- the `work my plate` page, CONFIRM rows: `resolved` · `drop` · `not mine`
  (Done confirms + closes).

**Board rows (BOARD1 — the ARTIFACT board, `--format artifact`; a different
surface from the chat board above, which is the plate's default widget
render. The artifact still renders the pre-plate row set; its verb lines are
kept here only so the artifact board's parity pin has a source):**

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
on the board that is "tap **Done** on anything that's finished; for anything
else say what you want and the row's number"; on the `work my plate` page it
is "tap **Done**, or pick from the row's menu (**Later…**, **Drop**, **Not
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

**The two doors are a TABLE, not a paragraph** — `plate_view.PLATE_REPLY_SURFACES`
(`work my plate` -> `plate-page`, `show parked` -> `show-parked`), read by
`surface_drivers.surface_for_reply`. Fire the surface the table names; never
improvise a rebuild.

`undo` (same chat) reopens every closed item via `reopen_commitment`, hands a
disowned item back to its previous owner via `confirm_commitment_owner`,
reverses reclassifications, AND lifts every mute the batch wrote (via
`mute_ledger.clear_dismissals`) — all additive; history keeps the tombstone,
the reopen, and the clear. Never narrate event-type names (CONTRACT Rule
4/9). `show parked`, `work my plate`, `not mine` and `undo <block>` are
replies on an open plate — never treat one as a fresh trigger. `show
waiting` and `show scheduling` are replies on an open plate AND standalone
doors (SPEC SURFACEFIX1 5.5): on an open plate they re-fire their surface
and re-render nothing, typed alone they answer.

**THE BOARD FITS ITSELF; THE WORKING PAGE HONOURS THE PLATE'S CAP.** The
board (the default render) shows as many open rows as the widget's size
budget holds, most important first, and its own line says how many more and
names the doors — relay that line as it is written and never invent a
count. On the `work my plate` page the forty-row cap still applies under
`light`: every overdue and due-this-week row renders always, the rest by
importance, and everything else is behind `show parked`
(`plate_view.widget_hidden_ids` is the rule). Never page past the cap by
hand, and never tell the reader there are more pages than the transport
returned.

**`show parked` — the door, and it is ONE MORE DRIVER CALL.** A reply of
`show parked` on an open board or page re-fires the SAME driver on the
`show-parked` surface, which renders EXACTLY the rows that page held back —
the same build, the same plate, the same row numbers — each one carrying
its own why:

```bash
python3 shared/scripts/surface_drivers.py show-parked \
    --workspace "<WORKSPACE>" --page 1
```

**NEVER open that door by rebuilding the plate.** An earlier version of
this page told you to pass `preset="engaged"` to `build_plate`: that is a
DIFFERENT plate — different block membership, different counts, no cap at
all — and it lands the reader on the whole book over dozens of pages, which
is the surface the ruling called irrelevant. The door shows what was held
back, never everything.

Two things are behind it and the line says which is which: rows that are
**resting** (nothing is asked of them) and rows **further down the list**
(the page's own budget cut them, and each one says why). Say the numbers the
render gives you.

**THE DOOR PAGES, AND IT IS THE WORKING SHAPE, NOT THE BOARD
(REVIEW_ONEPLATE1 N-4).** Say so rather than implying one look: `show
parked` renders the held-back rows the way the working page renders rows —
the full button set, nine to a page — so on a large book it is many pages
(239 rows, 27 pages, on the 2026-09-07 snapshot). M's ruling governs the
DEFAULT render, and this is a door a reader walks through on purpose, so
the shape is legal; it is not a board and must not be described as one.
Read the page count off the transport's own `pagination` and quote it —
never estimate it, and never tell the reader it is one look.

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

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

**In-chat replies (PLATE1)** — DOES NOT fire on 'show parked' / 'work my plate' / 'not mine' / 'undo the parked block' (replies on an OPEN plate — no skill's trigger; ROUTEMISS1 hand rows in tests/triggers.yaml pin that this skill never fires on them).

**`show waiting` / `show scheduling` ARE this skill's, standalone (SPEC SURFACEFIX1 5.5, M's ruling 4 of 2026-09-13)** — they were fenced off every skill here as reply-only, which left two phrases the plate prints, the manifest announces and every brief points at with no owner at all; typed alone they answered "No open plate in this session" (attended test B2.9). On an OPEN plate they are still replies and re-render nothing (`plate_view.reply_surface`); typed on their own they fire this skill, which answers with the Waiting On view and the schedule view through `run_surface`. See "`show waiting` and `show scheduling` — they answer on their own" above.

**Board triggers (BOARD1)** — 'publish my triage board' / 'put my triage on a page' / 'refresh my board' / 'triage board' fire § Publish the board, NOT the widget path. Same surface, different serialization. These live here rather than in the description because the description sits within 13 characters of the G11a cap: adding them needs a deliberate trim decision, not a silent one. DOES NOT fire on 'board pack' / 'board deck' / 'prep the board meeting' (board-pack-assembler — a governance document, not this list).

> Your plate — the FULL open commitment set grouped by the action it wants next (PLATE1; formerly sorted by age) — one widget, one Apply, everything dispatched through the single closure path. Fires on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments', 'what's on my plate' (QUICKCMD2, 2026-08-28 — the on-demand ask the my-plate scheduled task never had a chat trigger for). On demand only: the OPT-IN Friday-afternoon scheduled chat was retired in TASKRET1 (2026-08-17) until the review-tier backlog model settles — `add commitment triage` is refused warmly and the skill is otherwise unchanged. Rows carry done / defer / drop / not mine / make task / promote / never-track-this actions; stale tasks (30d+) surface as 'still on your plate?'. Every action is an APPEND (close_commitment / commitment_updated / commitment_reclassified) — this skill exists so the next cleanup chat never rewrites events.jsonl in place (F4). The post-Apply ack offers undo (additive commitment_reopened). DOES NOT fire on 'clean up my commitments' / 'sweep my backlog' / 'commitment backlog' / 'backlog sweep' / 'commitment amnesty' (commitment-backlog-sweep — the backwards-looking pass that reads months of mail history for delivery evidence, closes what the evidence settles, and surfaces duplicates and months-quiet items; triage reads no mail and closes nothing on evidence), 'show my list' (commitment_to_discuss review — show-my-list), 'scan for commitments' (extraction backfill), 'log resolved: <id>' (log-resolution artifact path), or the daily Commitments chat (orchestrator-commitments — actionable subset with chase drafts; triage is the full-set housekeeping pass).
## Draft date scan (DRAFTDATE1 — MANDATORY on every rendered draft)

**A draft never states a date, day or deadline the row does not hold.** On 2026-09-07 the drafts on this product invented three: "I'll have it finished by Friday" and "this is on your calendar today" on rows with no due date and no calendar event, and "let's get this paid this week" on a row with neither. Nobody had promised any of those days; sending one makes a commitment the book does not know about.

Before you show or save ANY draft you composed — status note, nudge, chase, follow-up, reply, invite body — run it through the scan:

```python
from draft_date_scan import assert_draft_dates
assert_draft_dates(<the draft text>, <the row>, today=<the workspace's own day>)
```

It raises `DraftDateError` naming every phrase the row cannot support. A date phrase is allowed only when it traces to (1) the row's due date, (2) a calendar event on the row, or (3) a date in the row's OWN words — its title, its quote, the thread subject. `today` is the workspace's day (`tz.py`), never a UTC re-slice.

**NEVER catch the error and send anyway, and never invent a date so the sentence reads better.** The fix is one of two things: drop the day from the draft ("I'll come back to you with a date" is honest and costs nothing), or set a real date on the row first and then say it. A draft with no date at all is always allowed.

## The Access preamble this file refers to

Propagated by `scripts/dev/propagate_access_preamble.py`; the canonical copy is in `shared/WORKSPACE_ACCESS.md`.

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```
