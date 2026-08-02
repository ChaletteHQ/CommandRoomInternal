---
name: needs-your-call
description: "The one queue for unconfirmed extractions — items the workspace THINKS it heard a promise in but will not act on until you say. Fires on: 'needs your call', 'what needs my call', 'clear the queue', 'review the queue', 'confirm queue', 'unconfirmed extractions'. Shows them grouped by the call it came from, numbered, oldest first, so you can answer in batches: 'confirm 1-40', 'drop 41-50', 'confirm that call'. Confirming turns one into an ordinary open commitment; dropping closes it as dropped and nothing is ever deleted. Nothing is confirmed or dropped until you name it — there is no auto-clear. Also fires on 'show watching' / 'what are you watching' / 'what's on watch' — the read-only list of items being checked on quietly, each still answerable by name. Does NOT fire on 'commitment triage' / 'triage my commitments' (commitment-triage owns the full open set), 'show my list' (show-my-list), or 'clean up my commitments' / 'backlog sweep' (commitment-backlog-sweep)."
---

# needs-your-call

The adjudication queue for **unconfirmed extractions** (INTAKE, 2026-07-31).

A commitment carrying `data.pending_review` is a guess. The extractor read a
transcript or an email, thought it saw a promise, and flagged that it was not
sure. Those items have never been auto-closeable or chaseable — but until
INTAKE they still counted in the headline open total and still rendered as
rows anywhere open commitments render. A bad week of extraction inflated the
number the CEO reads as "how many promises am I carrying," and the only way
to fix a wrong guess was to hunt the row down inside a list of everything.

Now an unconfirmed extraction is a **queue member, not an open commitment**.
It counts in exactly one number — `counts["headline"]["unconfirmed"]`, which
is a POINTER at this queue, not a slice of `total` — and it lives in exactly
one list: this one.

## What lands here (CAPTUREFLOW, 2026-08-01)

Three kinds of row, and they are all the same question to the user — *did the
workspace hear this right?* — so they render identically and answer with the
same verbs. Their `review_reason` is what differs, and it is printed under
every row:

| Row | Marker on the capture | What the reason says |
|---|---|---|
| The extractor was unsure | — | whatever the extractor recorded |
| The evidence isn't in the transcript | `data.fusion_unverified` | the phrase could not be found in the source it was attributed to |
| Below the capture floor | `data.floor_gated` | the `FLOOR_*` condition it missed — no owner, no concrete deliverable, nothing depending on it, retold from an earlier call, or discussed-never-accepted |

**The third kind is new, and it is M's 2026-08-01 ruling.** Those captures used
to be dropped where nobody could see them. The review measured the floor
against a hand-judged sample and found it as likely to be wrong as right when
it refuses — for every junk capture it stopped it also destroyed a real
promise, silently. A heuristic that wrong does not get a delete. So the floor
routes here instead, and the cost of it being wrong is one tap. Expect this
queue to carry more rows than it did before the gates, and expect most of them
to be `drop`.

The `floor_gated` marker exists so those rows can be hidden behind a config
toggle once M has run with them for a while. **The toggle is deliberately not
built** — do not invent one, and do not filter these rows out of any render.

## Routing

| The user says | Fires |
|---|---|
| "needs your call" / "what needs my call" | this skill |
| "clear the queue" / "review the queue" / "confirm queue" | this skill |
| "unconfirmed extractions" | this skill |
| "show watching" / "what are you watching" / "what's on watch" | this skill, Step 4 |
| "triage my commitments" / "commitment triage" | commitment-triage |
| "show my list" / "my list" | show-my-list |
| "clean up my commitments" / "backlog sweep" | commitment-backlog-sweep |

### Fences

- **Not commitment-triage.** That skill owns the FULL open set — everything
  confirmed, oldest first, with the whole verb ladder. It still pins
  anything unconfirmed 7+ days in its labelled "Unconfirmed" block
  (escalation, never age-buried) and points the rest here in one line. This
  skill owns the queue itself. Neither renders the other's rows.
- **Not show-my-list** (the retired discuss-later queue) and **not
  show-my-reminders** (the pin-until-cleared personal lane). Different
  ledgers; the word "list" alone never routes here.
- **Not commitment-backlog-sweep.** That reads months of mail for evidence
  that confirmed promises were already kept. It deliberately excludes every
  unconfirmed extraction and says so in its digest.
- **The staff meeting shows the same rows, and that is on purpose**
  (CAPTUREFLOW §C). Its "FROM YOUR MEETINGS" section renders the first few
  meeting groups from THIS queue, through the same builder and the same
  confirm/drop fence — one queue, two places to answer it, no second ledger.
  This skill is still on-demand only and registers no scheduled task; the
  staff meeting is already scheduled, and the fold is a section there, not an
  appointment of its own.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. This skill writes events.jsonl ONLY
through `commitment_state` helpers, reached via
`shared/scripts/needs_review_queue.py`:

- confirm → `commitment_state.clear_review_flags` (one `commitment_updated`
  carrying `data.review_flags_cleared: true`; the loader folds it to clear
  `pending_review` read-side);
- drop → `commitment_state.close_commitment(resolution="dropped",
  user_confirmed=True)` (one `commitment_resolved`).

**Nothing is ever deleted or rewritten.** A dropped item's original capture
stays in history exactly as written — the drop is an appended tombstone. The
user naming a row IS the explicit confirmation a `pending_review` item
requires; no other path may resolve one.

## Step 1 — Show the queue

Discover the plugin root first (CONTRACT Rule 22) and run FROM `$PLUGIN_ROOT`:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
python3 shared/scripts/needs_review_queue.py view "$WORKSPACE"
```

One command. It prints the whole queue **grouped by the call it came from**
(CAPTUREFLOW 2026-08-01), oldest call first, numbered across the whole list —
**every row with its `evidence:` line**: the source-text quote the extractor
saved, or, when there is nothing behind the row but the guess itself (no
evidence, or title-match-only evidence — the 2026-07-27 incident class), an
explicit `evidence: NONE that holds up` marker plus a footer count. **Relay
that output verbatim** — do not re-sort it, re-number it, summarize it, drop
rows to keep it short, or trim the evidence lines. The numbering is the user's
addressing scheme, and the evidence lines are what the user weighs before
answering in bulk; changing either breaks the contract.

Meeting grouping is the point of the surface now: one call's worth of
captures is ONE decision the user makes in one pass, and everything that did
not come from a call sits in a single `(not from a meeting)` group at the
end. Counterparty grouping is still there for a chase-shaped answer — pass
`--group-by counterparty` — but it is not what the queue shows first.

Empty queue: the command says so in one line. Relay that and stop — never
pad an all-clear.

`view-json` returns the same view as JSON when you need the ids without
re-reading.

**The widget form — ONE call, and it paginates by CALL.** Never hand-compose
the rows, and never render this surface unpaginated (an unpaginated
`render_and_persist` skips the 40KB byte-budget fit):

```python
import sys; sys.path.insert(0, "shared/scripts")  # cwd == $PLUGIN_ROOT
from needs_review_queue import render_queue_page

transport = render_queue_page("<WORKSPACE>", page=1)
# transport["html"]              -> show_widget widget_code, byte-exact
# transport["group_pagination"]  -> {page, total_pages, has_more, total_groups, …}
```

`render_queue_page` builds the grouped view, packs WHOLE groups onto each page
(a call is never split across a page boundary — half a group is half a
question), and hands the page to `widget_transport.render_and_persist` with an
explicit page and page size. `show more` re-fires it with `page=N+1`. If
`group_pagination` carries `group_split_by_budget`, the byte fit had to cut an
unusually large call — say so in one line rather than presenting a cut group
as a whole one.

## Step 2 — Take the batch

The user answers in ranges, by group, or both: *"confirm 1-40, drop 41-50"*,
*"confirm the Acme call"*, *"not mine 12"*, *"drop everything from
past-meetings"*.

- **Ranges and `all`** parse in code: `parse_selection(spec, view["total"])`.
- **Group phrases** ("the Acme call", "all Acme rows") resolve in code too:
  `ids_for_group(view, "<what they said>")` matches a group's `group_key`
  exactly, then its display name, then a substring — and raises loudly when
  two groups could match, so you ask which one instead of picking.
  `confirm_group(ws, view, "<what they said>")` is the one-liner for a whole
  call: it passes an EMPTY `confirm_weak_ids`, so the weak-evidence fence
  holds exactly as it does for `all` and a range. Naming a call names no row
  individually.
- **`not mine`** → `not_mine_items(ws, ids, resolved_by=user_id)`. Same
  closure `drop` writes, with the honest reason (the capture was real, it
  just was not theirs). If they NAME the real owner, route to
  `commitment_state.reassign_commitment` instead — this queue never guesses
  at a reassignment.
- **Never confirm or drop anything the user did not name.** There is no
  "clear the rest", no default, no auto-confirm on an empty answer. A bare
  "clear the queue" is a request to SEE it (Step 1), not to empty it.
- **Weak rows never confirm in bulk (BULKGUARD).** A row whose evidence line
  says `NONE that holds up` cannot be swept into the open book by `all`, a
  group phrase, or a range — `confirm_items` HOLDS it (`held_weak_evidence`)
  unless its id rides `confirm_weak_ids`, and an id may ride
  `confirm_weak_ids` ONLY for a display number the user typed as a
  standalone token — which is exactly what `individually_named(spec)`
  returns. Never widen that set yourself: the code below is the whole
  contract. (Group phrases name nothing individually; pass no weak ids for
  them.)

```python
import sys; sys.path.insert(0, "shared/scripts")  # cwd == $PLUGIN_ROOT per the preamble
from needs_review_queue import (GROUP_MEETING, build_queue_view,
                                confirm_group, ids_for_selection,
                                individually_named, parse_selection,
                                confirm_items, drop_items, not_mine_items)
from primary_user import resolve_primary_user

ws = "<WORKSPACE>"
view = build_queue_view(ws, group_by=GROUP_MEETING)
user_id = resolve_primary_user(ws)

confirm_spec, drop_spec = "1-40, 44", "41-50"
keep = ids_for_selection(view, parse_selection(confirm_spec, view["total"]))
gone = ids_for_selection(view, parse_selection(drop_spec, view["total"]))
# Only standalone-typed numbers (here: 44) may override a weak-evidence hold.
named = ids_for_selection(view, sorted(individually_named(confirm_spec)))

c = confirm_items(ws, keep, confirm_weak_ids=named)
d = drop_items(ws, gone, resolved_by=user_id)

# A whole call, when that is what they said. Same fence — no weak override.
# g = confirm_group(ws, view, "<the call they named>")
# n = not_mine_items(ws, ids_for_selection(view, [12]), resolved_by=user_id)
```

Build BOTH id lists from the SAME `view` before writing anything — confirming
changes what is in the queue, so a second `build_queue_view` mid-batch
renumbers the rows out from under the user's sentence.

## Step 3 — Ack

Counts, plainly, in one or two lines. Read them off the return values; never
re-count by hand.

> *"Kept 40 — they're ordinary open items now. Dropped 10; they're closed as
> dropped, and the original capture stays in your history either way."*

- `n_not_pending` (confirm) and `n_already` (drop) mean the item had already
  been settled. Say so honestly — *"2 of those were already handled"* — and
  never re-write. Re-running the same selection is a no-op by design.
- `n_held` (confirm) rows were HELD by the weak-evidence guard. Say which
  rows and why, in plain words, and how to act on one: *"Held 2 — there's
  nothing behind them but the guess itself. Say `confirm 7` to keep one
  anyway, or `drop 7,9` to let them go."* Never re-issue the confirm with
  the held ids in `confirm_weak_ids` on your own — that override belongs to
  the user's next sentence.
- `n_refused` (drop) means an id resolved to an open CONFIRMED commitment —
  the queue never closes those. Point at the ordinary close path ("say
  'mark done'").
- `n_failed` rows carry a `detail`. Name what blocked them in the user's
  words; a `has_subitems` row needs its own one-line confirm before the
  cascade, which the queue never does silently.
- Never print ids, event type names, or field names.

## Step 4 — "show watching"

Some items are not in the queue at all. When the workspace half-recognises
something — the topic came up in a meeting, but nothing anywhere says it
actually got done — it does not put that in front of the user as a question.
It watches it: keeps looking for proof, quietly, and only comes back if it
still cannot find any. That is what keeps this queue short enough to be worth
reading.

`show watching` is the read-only list of those. Same preamble as Step 1, then:

```bash
python3 shared/scripts/watch_gate.py view "$WORKSPACE"
```

Each row says what it rests on and how much longer it will be watched.
**Relay it verbatim.** For the widget form, build the same view in code and
render it through the canonical transport — never hand-compose the rows:

```python
import sys; sys.path.insert(0, "shared/scripts")  # cwd == $PLUGIN_ROOT
from watch_gate import build_watching_view
from widget_transport import render_and_persist

view = build_watching_view("<WORKSPACE>")
transport = render_and_persist(
    data_view=view, wrapper="fragment",
    persist_dir="<WORKSPACE>/_hq/.system/widgets")
```

Any row is answerable on demand — *"confirm 2"* closes that one now,
*"drop 2"* lets it go — and both dispatch through the ordinary per-item paths
above. Left alone, the watch runs its course by itself.

### Explaining it, when the user asks

Plain language, no numbers, and never the words behind the machinery:

> *"Some of what I hear in a meeting is clear — someone says the thing went
> out, and I just close it. Some of it is a gray zone: the topic came up, and
> nothing tells me either way. I don't want to hand you a pile of those to
> rubber-stamp, so I hold onto them and keep looking. If proof turns up, I
> close it and you never hear about it. If it runs out and it's small and
> internal, I let it go and note that I assumed it. If someone outside is
> waiting on it, or there's money or a date involved, I come back and ask you
> one question."*

If they want it to ask more or less often, say that is a setting you can turn
up or down for them — and stop there. Do not name files, levels, windows, or
scores.

## What this skill does NOT do

- Never confirms or drops anything on its own — no sweep, no scheduled task,
  no "obvious ones" shortcut. Every write traces to a selection the user
  typed.
- Never deletes or edits an event.
- Touches no connectors: no mail, calendar, or transcript fetches. Pure
  substrate read plus the two writes above.
- Registers no scheduled task. On demand only.

## See also

- `shared/scripts/needs_review_queue.py` — the view (both groupings), the
  selection and group parsers, the shared per-meeting renderer the staff
  meeting also consumes (`staff_meeting_group_section`), the group-aware
  pager (`render_queue_page`), and the write wrappers (the ONLY path this
  skill writes through).
- `shared/scripts/watch_gate.py` — the evidence-strength vocabulary, THE
  bulk-accept fence both accept surfaces call, the watch state, and the
  `show watching` view.
- `shared/scripts/cru_match.py` — `split_pending_review` /
  `load_needs_review`, the seam every user-facing reader uses to keep
  unconfirmed extractions out of open-commitment numbers and lists.
- commitment-triage — the full confirmed open set; pins 7+ day unconfirmed
  items and points the rest here.
- commitment-backlog-sweep — the mail-history evidence pass; excludes this
  queue and reports the count it excluded.
