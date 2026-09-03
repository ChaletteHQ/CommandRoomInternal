---
name: needs-your-call
surfaces: both
description: "The one queue for unconfirmed extractions — items the workspace THINKS it heard a promise in but will not act on until you say. Fires on: 'needs your call', 'what needs my call', 'clear the queue', 'review the queue', 'confirm queue', 'unconfirmed extractions'. Confirming makes an ordinary open commitment; dropping closes it; nothing clears until you name it. Also 'show watching' / 'what are you watching' / 'what's on watch' — the read-only watch list. Also the project-binding review: 'review project bindings', 'backfill bindings', 'which project does this belong to' — past records naming a project they were never filed under; one tap: Bind / Not this project. Does NOT fire on 'commitment triage' / 'triage my commitments' (commitment-triage), 'show my list' (show-my-list), 'clean up my commitments' / 'backlog sweep' (commitment-backlog-sweep), 'revisit' a decision (decision-revisit), 'clean up my workspace' / 'maintenance' (cleanup)."
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

And since UNCONFEXP1 (M's ruling, 2026-08-30) a queue member does not wait
here forever: it nags for its short window (`UNCONFIRMED_NAG_DAYS`, default
2 days from capture, reset by real movement) and then LAPSES on its own —
closed `dropped` with the `review_expired` reason by the daily maintenance
drain, reversibly, in one batch one `undo` puts back. The queue renders
what is still inside its window; a lapse is disclosed in the next brief's
CHANGED line, and a re-mention of a lapsed item is fresh evidence that gets
captured again. This supersedes the old escalate-until-answered posture.

## What lands here (CAPTUREFLOW, 2026-08-01)

Three kinds of row, and they are all the same question to the user — *did the
workspace hear this right?* — so they render identically and answer with the
same verbs. Their `review_reason` is what differs, and it is printed under
every row:

| Row | Marker on the capture | What the reason says |
|---|---|---|
| The extractor was unsure | — | whatever the extractor recorded |
| The evidence isn't in the transcript | `data.fusion_unverified` | the phrase could not be found in the source it was attributed to |
| Below the capture floor | `data.floor_gated` | the `FLOOR_*` condition it missed — no owner, no concrete deliverable, nothing depending on it, retold from an earlier call, discussed-never-accepted, already done during the call (`FLOOR_DONE_IN_MEETING`), or taken back later in the same conversation (`FLOOR_SUPERSEDED_IN_MEETING`) |

**A `FLOOR_SUPERSEDED_IN_MEETING` row carries `data.superseding_quote`** — the
words, verbatim from the same transcript, that took the promise back. Print it
under the reason when it is there. The verdict is "the call changed its mind
about this"; a user cannot answer that in one tap without seeing the sentence
that changed it.

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

### The SET ASIDE section (OBSERVED1, 2026-08-24)

Below the queue's own rows, the view can carry one more labelled section:
**live observed-tier items** (`commitment_observed` — the relevance gate's
set-asides: heard on the user's calls between other people, kept on file,
deliberately not tracked). They were readable by prep and answerable by
nobody — the reader-coverage gap OBSERVED1 closed. What to know:

- The section is DROP-EMPTY and its rows are numbered CONTINUING after the
  queue (`view["observed_groups"]` / `view["n_observed"]`). Selections parse
  through `selection_numbers(view, spec)`: explicit numbers and ranges reach
  these rows, while **`all` answers the queue's own counted rows only** —
  the header's sentence is the question the user is answering, and a
  one-word sweep must never mint or dismiss the set-aside tier.
  `view["total"]` still counts ONLY unconfirmed extractions; it is the
  number `headline.unconfirmed` points at and the header's counted sentence,
  and observed rows never inflate it (`addressable_total(view)` is the full
  addressable bound the helper validates against).
- The verbs are **confirm / drop only** (`capture_gate.OBSERVED_ROW_ACTIONS`).
  No `already done` (nothing tracked = nothing whose completion can be
  attested) and no `not mine` button (the tier already IS "not mine" — though
  a typed `not mine N` on one of these rows works and writes the same drop).
- **Confirm** = `promote_observed` (the tier's one defined transition — a
  real `commitment` carrying `promoted_from`) **then** `clear_review_flags`,
  in one gesture: the row becomes an ordinary open commitment. **Drop** =
  `promote_observed` then `close_commitment(resolution="dropped")` — the
  standard tombstone, which also feeds the capture gate's dismissal tuning.
  Both are appends; the observed event stays in history unrewritten. You
  never call the tier's functions yourself — `confirm_items` / `drop_items`
  route an `obs_` id automatically.
- Group phrases ("confirm the Acme call") deliberately answer NO set-aside
  row — those answer by number (or one tap) only. The weak-evidence fence
  holds here too, and it matters MORE: confirming an observed row MINTS a
  commitment, so an evidence-less one never confirms in bulk.
- The staff meeting's FROM YOUR MEETINGS fold does NOT carry this section —
  it stays scoped to the queue's own rows.

### One line per real-world item (CLUSTER1, 2026-08-25)

Render-level clustering is **default-on**. Rows the clusterer reads as the
same real-world item — shared content tokens (the shipped duplicate scorer),
a shared counterparty read off the roster, captured within days of each
other; precision over recall, a false join costs trust — render as **one
line**: the oldest row's title plus `+N folded`, with the folded rows one
tap away (expand, read-only, display numbers kept). What to know:

- **A cluster is a display fact until confirmed.** Expanding, ignoring, or
  answering rows individually (their numbers still work — folded rows keep
  their display numbers and stay in the view's selection maps) writes
  nothing and changes nothing.
- **The one tap — `keep as one`** (widget button on the cluster line, or
  typed `keep as one N` where N is the line's number) — makes it durable
  through the EXISTING writers only:
  `commitment_cluster.apply_cluster_merge` → `clear_review_flags` on the
  pending survivor + one `supersede_commitment(user_confirmed=True)` per
  folded row, details in evidence, all in ONE `clu_` batch. One `undo`
  (`brain_undo.undo_batch` on the returned `batch_ref`) splits them back
  out. The widget row embeds `data.id` + `data.folded_ids` verbatim — never
  resolve the ids yourself.
- **The headline count is the information count** — a queue of 41 rows in 9
  clusters says 9, with the true row count in the same sentence and every
  row reachable one level down. `view["total"]` keeps its shipped meaning
  (the reconciled row count `headline.unconfirmed` points at);
  `view["n_lines"]` / `n_clusters` / `n_folded` appear only when something
  clustered.
- **Nothing auto-merges from here.** Auto-merge stays confined to
  `commitment_dedup.auto_merge_eligible` (the zero-ambiguity gate),
  byte-identical. The clusterer proposes; the tap is the verdict.
- The SET ASIDE section never clusters (a different tier with its own
  doctrine), and the `show me what you'd hide` review renders clusters
  display-only (held-review's DD-4 fence — no resolving verb there, ever).
- There is no setting for this and none should be invented: M's ruling is
  that it is baked in for everybody.

## Routing

| The user says | Fires |
|---|---|
| "needs your call" / "what needs my call" | this skill |
| "clear the queue" / "review the queue" / "confirm queue" | this skill |
| "unconfirmed extractions" | this skill |
| "show watching" / "what are you watching" / "what's on watch" | this skill, Step 4 |
| "review project bindings" / "backfill bindings" / "which project does this belong to" | this skill, Step 5 (BACKFILL2) |
| "triage my commitments" / "commitment triage" | commitment-triage |
| "revisit the [X] decision" / "revisit" a decision | decision-revisit |
| "clean up my workspace" / "maintenance" / "deep clean" | cleanup |
| "backfill commitments" / "scan for commitments" | scan-for-commitments |
| "show my list" / "my list" | show-my-list |
| "clean up my commitments" / "backlog sweep" | commitment-backlog-sweep |

### Fences

- **Not commitment-triage.** That skill owns the FULL open set — everything
  confirmed, oldest first, with the whole verb ladder. It still pins
  anything unconfirmed 7+ days in its labelled "Unconfirmed" block and
  points the rest here in one line — though since UNCONFEXP1 (M's
  2026-08-30 ruling) an unconfirmed extraction nags only for its short
  window (`UNCONFIRMED_NAG_DAYS`, default 2 days) and then lapses on its
  own, reversibly — so that block now reaches only rows real movement kept
  alive. This skill owns the queue itself. Neither renders the other's
  rows.
- **Not show-my-list** (the retired discuss-later queue) and **not
  show-my-reminders** (the pin-until-cleared personal lane). Different
  ledgers; the word "list" alone never routes here.
- **Not commitment-backlog-sweep.** That reads months of mail for evidence
  that confirmed promises were already kept. It deliberately excludes every
  unconfirmed extraction and says so in its digest.
- **Not decision-revisit, not cleanup, not scan-for-commitments** (the
  BACKFILL2 neighbours). "Revisit" is a DECISION verb and stays with
  decision-revisit. "Clean up my workspace" / "maintenance" / "deep clean"
  are the tidying pass — cleanup — even though the binding review is a
  hygiene act; the review is a one-tap adjudication sitting, not a sweep.
  "Backfill commitments" / "scan for commitments" is the historic
  commitment scan (scan-for-commitments) — a different backfill of a
  different ledger. Only the three binding stems (Step 5) route here.
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
- **already done** → `needs_review_queue.done_items` → `clear_review_flags`
  **then** `commitment_state.close_commitment(resolution="done",
  user_confirmed=True)`. TWO writes, in that order, because the user is
  making two claims — the capture was real, and it was already fulfilled —
  and the substrate should carry both. The closure's evidence is the user's
  own attestation and NOTHING else: never a match, never a score, never a
  synthesized sent-mail line. It also carries an additive
  `completion_basis: "user_attestation"` stamp so no later reader mistakes an
  attested completion for an evidence-backed one;
- drop → `commitment_state.close_commitment(resolution="dropped",
  user_confirmed=True)` (one `commitment_resolved`);
- **undo a confirm** → `needs_review_queue.undo_confirm_items` →
  `commitment_state.restore_review_flags` (one `commitment_updated` carrying
  `data.review_flags_set: true` + the item's ORIGINAL `review_reason`);
- **undo an already done** → `needs_review_queue.undo_done_items` →
  `commitment_state.reopen_commitment` **then** `restore_review_flags`;
- **confirm on a SET ASIDE row** (an `obs_` id) →
  `capture_gate.promote_observed` (one real `commitment` with
  `data.promoted_from` + `pending_review`) **then** `clear_review_flags` —
  one gesture, two appends, an ordinary open commitment. Routed inside
  `confirm_items`; never call the tier's functions directly;
- **drop on a SET ASIDE row** → `promote_observed` **then**
  `close_commitment(resolution="dropped", user_confirmed=True)` — the same
  standard tombstone a queue drop writes. Routed inside `drop_items`.

**Nothing is ever deleted or rewritten.** A dropped item's original capture
stays in history exactly as written — the drop is an appended tombstone. The
user naming a row IS the explicit confirmation a `pending_review` item
requires; no other path may resolve one.

**Why `already done` is not `drop`.** A drop says the capture should not have
been tracked, and the capture gate reads it that way: a `dropped` / `not_mine`
closure is a dismissal signal for that counterparty, and enough of them make
the gate ASK whether to stop capturing that counterparty at all. Answering
`drop` on promises the CEO actually kept therefore teaches the system to stop
listening. `already done` closes with `resolution="done"`, which is in no
dismissal set.

## Step 1 — Show the queue

Discover the plugin root first (CONTRACT Rule 22) and run FROM `$PLUGIN_ROOT`:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ '{print NF, $0}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||')
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

### The header offer — the names this queue is stuck behind (PERSONLOOP1)

`render_queue_page` also asks `needs_review_queue.person_candidate_offer`
(which reads `person_candidates.derive_candidates`) and, when it returns
something, leads the page with a capped section titled **PEOPLE THE GRAPH KEEPS
MISSING** plus one header line. You do not build either — the render call does.
Both are DROP-EMPTY: a workspace with no recurring unresolved name gets exactly
the page it got before this existed.

What a row is: a name that has failed person-resolution across **at least two
different calls**, with the count of captures currently blocked behind it. That
is the whole reason the queue gets long — a name with no contact record cannot
resolve, so every meeting that mentions it mints more rows and none of them can
ever drain. The row's three answers (`add person` / `same as [existing]` /
`not a person`) are dispatched by apply-choices through
`person_candidates.resolve_candidate`, which creates the record AND clears every
row that was waiting on the name, in one gesture. Relay its ack count verbatim
— "added them, and nine waiting captures cleared" is the sentence that shows
the loop closed.

Three things this offer never does, and each is deliberate:

- **It never creates anybody.** The row is a question; a person record is a
  durable graph write with an org attached, and creating one off a transcript
  spelling would mint a misspelling as a real human being.
- **It never asks about a one-off.** One unresolved name on one capture is
  noise. Two different calls is a hole in the graph.
- **`not a person` silences the PROPOSAL only.** It never stops captures that
  mention the name. If the user seems to be asking for the second thing, say
  plainly that this answer only stops the suggestion.

## Step 2 — Take the batch

The user answers in ranges, by group, or both: *"confirm 1-40, drop 41-50"*,
*"confirm the Acme call"*, *"not mine 12"*, *"drop everything from
past-meetings"*, *"already done 7"*.

The row verbs are `confirm` / `already done` / `drop` / `not mine`, and they
are the same four on this surface and on the staff meeting's FROM YOUR
MEETINGS fold — one list, one dispatch, no per-surface variant.

- **Ranges and `all`** parse in code: `selection_numbers(view, spec)` — THE
  view-aware parse (OBSERVED1). `all` answers the queue's own counted rows
  only; explicit numbers and ranges also reach the SET ASIDE section. Never
  call `parse_selection` with a bound you picked yourself — the two-total
  split (`total` vs `addressable_total`) is exactly what this helper exists
  to get right.
- **Group phrases** ("the Acme call", "all Acme rows") resolve in code too:
  `ids_for_group(view, "<what they said>")` matches a group's `group_key`
  exactly, then its display name, then a substring — and raises loudly when
  two groups could match, so you ask which one instead of picking.
  `confirm_group(ws, view, "<what they said>")` is the one-liner for a whole
  call: it passes an EMPTY `confirm_weak_ids`, so the weak-evidence fence
  holds exactly as it does for `all` and a range. Naming a call names no row
  individually.
- **`already done` — "Already done"** → `done_items(ws, ids,
  resolved_by=user_id, attested_ids=<the ids for the numbers they typed on
  their own>)`. The user is telling you they already did it, off-mail. It
  confirms the capture and closes it as `done`, and its evidence is that
  sentence — never a match. **PER ITEM ONLY, and the bar is stricter than
  confirm's:** every id in the call must be individually named, so
  `already done 7` and `already done 7, 9` work while `already done all`,
  `already done 1-40` and `already done the vendor call` are REFUSED
  (`not_individually_named`) and write nothing. Never widen `attested_ids`
  yourself and never downgrade a refused Done to a confirm — a confirm is a
  fact about what the workspace heard; this is a fact about what the user
  did, and only they can assert it. There is no group form.
- **`not mine`** → `not_mine_items(ws, ids, resolved_by=user_id)`. Same
  closure `drop` writes, with the honest reason (the capture was real, it
  just was not theirs). If they NAME the real owner, route to
  `commitment_state.reassign_commitment` instead — this queue never guesses
  at a reassignment.
- **`keep as one N`** (CLUSTER1) → N must be a CLUSTER line's own display
  number in the CURRENT view. Read the survivor's `cluster` off the view row
  (`row["cluster"]["folded_ids"]`) and dispatch ONE call:
  `commitment_cluster.apply_cluster_merge(ws, <survivor id>, <folded_ids>,
  merged_by=user_id, source_skill="needs-your-call")`. Never re-derive the
  cluster, never pass ids the view row does not carry, and never apply it to
  a line with no cluster — refuse in one line ("that line has nothing folded
  under it"). Relay the returned `summary` verbatim (it names the undo);
  a follow-up `undo` goes to `brain_undo.undo_batch` with the returned
  `batch_ref`.
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
                                individually_named, selection_numbers,
                                confirm_items, done_items, drop_items,
                                not_mine_items)
from primary_user import resolve_primary_user

ws = "<WORKSPACE>"
view = build_queue_view(ws, group_by=GROUP_MEETING)
user_id = resolve_primary_user(ws)

# OBSERVED1 — selection parses through selection_numbers(view, spec), the
# view-aware helper: `all` answers the queue's own counted rows only (the
# header's sentence is the question being answered — a one-word sweep never
# mints or dismisses the set-aside tier), while explicit numbers and ranges
# reach BOTH tiers, validated against addressable_total(view).
confirm_spec, drop_spec = "1-40, 44", "41-50"
keep = ids_for_selection(view, selection_numbers(view, confirm_spec))
gone = ids_for_selection(view, selection_numbers(view, drop_spec))
# Only standalone-typed numbers (here: 44) may override a weak-evidence hold.
named = ids_for_selection(view, sorted(individually_named(confirm_spec)))

c = confirm_items(ws, keep, confirm_weak_ids=named)
d = drop_items(ws, gone, resolved_by=user_id)

# `already done 7, 9` — per item only. `attested_ids` is built from
# individually_named() and nothing else; a range or `all` names nothing, so
# the call writes nothing and reports why.
done_spec = "7, 9"
done_ids = ids_for_selection(view, sorted(individually_named(done_spec)))
a = done_items(ws, done_ids, resolved_by=user_id, attested_ids=done_ids)

# A whole call, when that is what they said. Same fence — no weak override.
# g = confirm_group(ws, view, "<the call they named>")
# n = not_mine_items(ws, ids_for_selection(view, [12]), resolved_by=user_id)
```

Build BOTH id lists from the SAME `view` before writing anything — confirming
changes what is in the queue, so a second `build_queue_view` mid-batch
renumbers the rows out from under the user's sentence.

**`view["offer"]` — the bulk-drain line (REVSCHED1 §3-1).** The view carries one
extra string: an offer to clear the lapsed half of this queue in one confirm,
present only when the lapsed count clears
`commitment_backlog_sweep.REVIEW_OFFER_AT` (10) and `""` otherwise. Show it
**directly under the header, above the rows**, verbatim — `render_text` and
`build_queue_data_view` already place it there, so pasting either one is enough.
Do not compose the sentence, do not compare a count yourself, and do not move it
to a footer: a reader who has started working the list row by row has stopped
reading before a footer arrives, which is how 224 captures produced 3 answers.
`""` means no offer — say nothing at all, never "the pile is small".

**`already done` needs to know who is attesting.** `resolve_primary_user`
returns an empty string when a workspace has no primary user on file, and
`done_items` REFUSES that with a loud error rather than writing an attestation
attributed to nobody — an unattributed "I already did this" is not an
attestation. Unlike the per-row refusals, this one stops the whole call, so
nothing is half-written. Say so plainly and point at the answers that still
work — never invent a setup command you have not confirmed exists: *"I can't
record that you did it until this workspace knows who you are. `confirm 7`
keeps the item and you can close it from your list."* `confirm`, `drop` and
`not mine` are unaffected; only the attestation needs an attributed actor.

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
- `n_done` (already done) rows are confirmed AND closed as completed. Say
  both halves plainly — *"Marked 2 as already done — they're closed as
  completed, and I've noted you told me so."*
- `n_refused` (drop) means an id resolved to an open CONFIRMED commitment —
  the queue never closes those. Point at the ordinary close path ("say
  'mark done'"). `n_refused` (already done) means the row was not named on
  its own: say so in one line and name the row — *"Row 7 wasn't named on its
  own — give me just that number and I'll close it as done."* Never re-issue
  it for them.
- `n_failed` rows carry a `detail`. Name what blocked them in the user's
  words; a `has_subitems` row needs its own one-line confirm before the
  cascade, which the queue never does silently. A `confirmed_not_closed` row
  is the honest half-landing of an `already done`: the item is confirmed and
  still OPEN, nothing was lost, and they can close it the ordinary way.
- Never print ids, event type names, or field names.

**Undo.** Every answer here is reversible, and saying so is part of the ack
("Say `undo` if I got that wrong."). A follow-up `undo` in the same chat
reverses the batch you just wrote, per verb — nothing is deleted, and the
original answer stays in history beside its reversal:

- a confirm → `undo_confirm_items(ws, <the ids you confirmed>,
  restored_by=user_id)`. The item comes back to this queue carrying its
  ORIGINAL reason.
- an `already done` → `undo_done_items(ws, <the ids you closed>,
  restored_by=user_id)`. It reopens AND comes back unconfirmed — both, or it
  is not an undo: a bare reopen would leave an open item you had already
  confirmed on the user's behalf.
- a drop / not mine → `commitment_state.reopen_commitment`, the shipped
  triage batch-undo path.
- **a SET ASIDE confirm** → the same `undo_confirm_items` call with the same
  cached ids — it translates an observed id to the commitment its confirm
  minted. The undone item comes back to THIS QUEUE as an unconfirmed row
  (promotion is permanent; the tier defines no un-promote), carrying the
  promotion's own reason. **A SET ASIDE drop** → `reopen_commitment` on the
  result's `promoted_id` (the observed id itself matches no commitment).

An undo REFUSES rather than steamrolls. `touched_since_confirm` means someone
made a later decision about that item — it was reassigned, parked, re-worded,
closed another way — and the refusal names which; relay that sentence and stop.
`already_unconfirmed` and `not_confirmed` are honest no-ops. Never reverse
anything the user did not just do, and never reverse a batch from an earlier
chat off memory — the ids come from the batch you wrote.

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

**The view also carries the SET ASIDE section (OBSERVED1)** — the same live
observed rows the queue renders (see "The SET ASIDE section" above), numbered
continuing after the watched rows, each with confirm/drop. Dispatch is
identical: hand the row's id to `confirm_items` / `drop_items` and they route
it through the observed transition. On THESE rows the two verbs mean "start
tracking it as an ordinary open item" / "let it go" — unlike a watched row,
nothing was being tracked yet. Drop-empty: no live observed rows, no
section, and the view is byte-identical to before OBSERVED1.

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

## Step 5 — "review project bindings" (BACKFILL2, 2026-09-02)

A different ledger, the same question shape — *did the workspace file this
right?* Some past records (a note, a meeting, an exchange) NAME a project by
its distinctive term and were never filed under it, so that project's memory
reads thinner than its history. The binding backfill (`backfill_bindings.py`,
the R3 engine) proposes those rows; it never binds one on its own — M's
ruling, 2026-08-31: **no auto-bind lane, ever**. This step is the one-tap
surface for that sitting. Fires on `review project bindings`, `backfill
bindings`, `which project does this belong to`.

**The widget form — ONE call, paginated by PROJECT.** Same preamble as Step 1
(discover `$PLUGIN_ROOT` and `$WORKSPACE`, `cd "$PLUGIN_ROOT"`), then:

```python
import sys; sys.path.insert(0, "shared/scripts")  # cwd == $PLUGIN_ROOT
from backfill_widget import render_backfill_page

transport = render_backfill_page("<WORKSPACE>", page=1)
if transport["empty"]:
    print(transport["line"])           # relay this one line and stop
# transport["html"]              -> show_widget widget_code, byte-exact
# transport["group_pagination"]  -> {page, total_pages, has_more, total_groups, …}
```

`render_backfill_page` runs the engine's read-only propose, drops rows the
user snoozed, shapes ONE section per project — each row shows the record's
own words, its date and kind, the term it names, the evidence behind the
match in plain words, and where it is filed today — with the project's
footer line saying what these taps buy it, and hands the page to
`widget_transport.render_and_persist` with an explicit page and page size.
Never hand-compose the rows and never render it unpaginated. `show more`
re-fires it with `page=N+1`. An `empty` result is a one-line all-clear —
relay `line` verbatim and stop; never pad it.

**The rows carry three verbs, and the wire id carries the snapshot.** Every
row offers `bind` / `not this project` / `skip`, from the ONE
`backfill_widget.ROW_ACTIONS` list. Its wire id is
`bind:<record>:<snapshot>` — the propose snapshot it was derived from rides
the button, so a tap that lands hours later still tells the engine which
list the user READ, and the engine refuses if the workspace moved past it.
You never build, parse, or re-derive these ids; apply-choices hands them to
`backfill_widget.apply_choices` verbatim.

- **Bind** files the record under the project — one additive re-tag, the
  project's history gains it. A record already filed under a DIFFERENT
  project keeps that home and gains this one alongside (additive only, by
  the engine's own fence). All the binds of one Apply land in ONE batch
  that a single `undo` reverses.
- **Not this project** records the no, with its evidence tier, in the run
  receipt — the precision dataset M asked for — and the row is never
  proposed again. A recorded no survives `undo` (undo reverses writes; a
  human's ruling stands until re-ruled).
- **Snooze (1 day)** (`skip`) hides the row until tomorrow. It adjudicates
  nothing and records nothing; the row comes back.
- **Nothing binds without a tap.** There is no "bind the rest", no default,
  no confidence threshold, and the footer's *Snooze rest (1 day)* snoozes —
  it never binds. Rows the engine refuses to propose (a name match with no
  other signal, a term two projects both claim) never reach the page at all.

**Typed answers.** If the user answers in chat instead of tapping ("bind 1
and 3, not 2"), map the display numbers to the rows of the CURRENT render
(`transport["report"]["rows"]`, in page order) and dispatch the same tuples
through `backfill_widget.apply_choices` — never call the engine's `apply`
directly, and never pass a seq the user did not name. `all` is NOT an answer
here: it names no row, so it binds nothing; say so in one line.

**Ack.** Relay `result["summary"]` verbatim — it carries the counts in plain
words, the READY change when one happened, and the `undo` offer when a batch
was written. `stale_snapshot` means the list went stale under the user's
hand: relay the summary (it says to re-fire) and write nothing else.
`refused` with no operator on file: the sitting needs an attributed
adjudicator (the receipt IS the precision record) — snoozing still works;
say so plainly, never invent a setup command.

**Undo.** A follow-up `undo` in the same chat →
`backfill_widget.undo_sitting(ws, <the batch id the result returned>,
undone_by=user_id)` — the standing `bkf_` reverser plus ONE gauge rebuild.
A bare `undo` in a later chat finds the same batch through the standing undo
lane ("filed a past record under its project").

**What Step 5 never does.** Never binds without a tap; never reads
transcripts or mail (pure substrate); never touches the queue's own rows;
never changes what the engine proposes, refuses, writes, or records —
the CLI sitting and this widget produce the same receipts.

## What this skill does NOT do

- Never confirms, closes or drops anything on its own — no sweep, no
  scheduled task, no inference, no "obvious ones" shortcut. Every write
  traces to a selection the user typed. `already done` is the strictest of
  them: it is unreachable without a row named on its own, because it asserts
  something only the user knows. The same holds for the project-binding
  review (Step 5): nothing binds without a tap.
- Never deletes or edits an event.
- Touches no connectors: no mail, calendar, or transcript fetches. Pure
  substrate read plus the two writes above.
- Registers no scheduled task. On demand only.

## See also

- `shared/scripts/needs_review_queue.py` — the view (both groupings), the
  selection and group parsers, the shared per-meeting renderer the staff
  meeting also consumes (`staff_meeting_group_section`), the group-aware
  pager (`render_queue_page`), the ONE `QUEUE_ROW_ACTIONS` list both surfaces
  render from, and the write wrappers — `confirm_items`, `done_items`,
  `drop_items`, `not_mine_items`, `undo_confirm_items`, `undo_done_items`
  (the ONLY path this skill writes through).
- `shared/scripts/commitment_state.py` — `restore_review_flags`, THE
  un-confirm writer. Purpose-built: it appends the existing
  `review_flags_set` fold key and carries NO `suspected_duplicate_of`.
  `flag_duplicate_for_review` is the duplicate-PAIR writer and is never the
  reverser of a confirm.
- `shared/scripts/backfill_widget.py` — the project-binding review (Step 5):
  `render_backfill_page` (the ONE render call), `ROW_ACTIONS`,
  `apply_choices` (the rail's one call for `bind:` rows), `undo_sitting`.
  The engine underneath is `shared/scripts/backfill_bindings.py`
  (BACKFILL1 — propose / apply / undo, untouched by the widget).
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
