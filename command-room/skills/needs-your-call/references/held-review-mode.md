# needs-your-call — Mode: would-hold review ("show me what you'd hide")

**Read-only mode of `needs-your-call`** (SKILLMERGE1 D4, 2026-09-03 — formerly the `held-review` skill; every phrase it answered routes to needs-your-call, whose Routing section sends the held phrases here). The queue and this mode render the SAME rows: the queue is where a row is answered, this mode is where the question *"would it be acceptable to stop asking about these?"* is answered. Nothing here resolves a row. The widget `src`, the `source_skill` on every event this mode writes (`held-review`) and the apply-choices dispatch entry are unchanged — persisted vocabulary stays canonical (`source_skill_compat.FOLDED_SKILL_ALIASES`).

The held tier is a second disposition for the weakest captures: **held** means
out of sight — not queued, not badged, not counted — and still on disk,
retrievable on request. Nothing is ever deleted.

It ships **off**, and this mode is the reading chair in front of the switch.
Before anything stops being asked about, somebody looks at exactly what would
stop being asked about. That is the whole surface: a list, counted, each row
carrying the reason it was refused at the capture floor.

**Rendering the list IS the review.** There is no score, no sample size, no
weekly ritual and no bar to clear. You read it, and then you decide.

## Routing

| The user says | Fires |
|---|---|
| "show me what you'd hide" / "what would you hide" | this mode, Step 1 |
| "show me what you would have hidden" / "what would you have hidden" | this mode, Step 1 |
| "show me what you would have held" / "held candidates" | this mode, Step 1 |
| "turn on held" / "hide the weak ones" | this mode, Step 3 |
| "turn off held" / "stop hiding the weak ones" | this mode, Step 4 |
| "needs your call" / "clear the queue" / "unconfirmed extractions" | the queue itself (needs-your-call Step 1) |
| "triage my commitments" / "commitment triage" | commitment-triage |
| "clean up my commitments" / "backlog sweep" | commitment-backlog-sweep |
| "end of day" / "close out my day" | end-of-day |

### Fences

- **Not the queue.** The queue (needs-your-call Steps 1-3) owns every verb that
  resolves a row — confirm, already done, drop, not mine. This mode owns the
  question *"would it be acceptable to stop asking about these?"* and owns no
  verb that answers a row. The same rows appear in both places; only one of
  them can change anything.
- **The enable is a separate, deliberate phrase, and it is offered nowhere
  else.** Not on a button, not in the morning brief, not at end of day, not
  from Apply. Apply means "carry out my choices"; a routing change is not a
  choice about a row, and putting it behind that button would make an
  intentional decision feel like a side effect.
- **Looking changes nothing.** No row is confirmed, closed, reopened,
  re-dated, muted or dropped by this mode, ever.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. This mode has exactly three writes and
every one of them goes through `shared/scripts/held_review.py`:

- **the render receipt** → one `held_review_rendered` event per render,
  written by `held_review.render_review_page`. It is what
  `held_review.has_reviewed` reads, and it is the evidence behind Step 3's
  first condition: the switch may not be thrown in a workspace where nobody
  has been shown the rows.
- **an objection** → `held_review.capture_objection`, one
  `held_review_objection` event, at most one per row ever. It records a note.
  It does not confirm, drop, close, reopen or re-flag the row, and it never
  comes back to be cleared.
- **the routing value** → `held_review.turn_on_held` /
  `held_review.turn_off_held`, which call `held_tier.enable_held_routing` /
  `held_tier.disable_held_routing`. Those are the ONLY writers of the routing
  value. Never write it by hand, never through `skill_config_writer`
  directly, and never by editing a config file: the fence lives inside
  `enable_held_routing` and a second path around it is a fence with a hole.

**A refusal writes nothing.** Not the value, not a pending marker, not an
event, not a receipt. If Step 3 refuses, say the sentence it hands you and
stop.

## Step 1 — Show what would be held

Discover the plugin root first (CONTRACT Rule 22) and run FROM `$PLUGIN_ROOT`.
Every widget on this surface goes through `render_and_persist`; relay the
returned `html` to show_widget **byte-exact**, and never hand-compose it:

```python
import sys; sys.path.insert(0, "shared/scripts")  # cwd == $PLUGIN_ROOT
from held_review import render_review_page

transport = render_review_page("<WORKSPACE>", page=1)
# transport["html"]              -> show_widget widget_code, byte-exact
# transport["group_pagination"]  -> {page, total_pages, has_more, total_groups, …}
# transport["view"]["total"]     -> the count the header leads with
```

`render_review_page` builds the scoped view
(`needs_review_queue.build_queue_view(..., scope="would_hold")` — the ordinary
queue's rows, filtered to the below-floor captures and nothing else), packs
WHOLE calls onto each page, and hands the page to
`widget_transport.render_and_persist` with an explicit page and page size.
`show more` re-fires it with `page=N+1`.

**What the header says, and why.** The count leads, because the count is the
question. Then the weeks the rows actually span — derived from the rows in
hand, never a window imposed on them, so nothing that would be hidden is
quietly left off the list you are judging. Then the promise the rows keep:
each one says why it was weak.

**What every row carries.** The same context line the queue draws — how old,
what it rests on, and last, `why it's here:` followed by the plain-words
reason the capture floor refused it. That last clause is the point of the
surface. Relay the widget; do not summarize the rows as text, do not re-sort
or re-number them, and do not trim the reasons.

Empty list: the header says so in one line. Relay it and stop.

**Clusters render display-only (CLUSTER1, 2026-08-25).** Rows the shipped
clusterer reads as the same real-world item render as one line — the oldest
row's title plus `+N folded`, the folded rows in a read-only expand, and the
header counting items with the true capture count in the same sentence. On
THIS surface that is the whole feature: the cluster line carries no
`keep as one`, embeds no folded ids, and resolves nothing — the DD-4 fence
holds on cluster lines exactly as on plain rows. Answering a cluster is a
queue gesture, made where the queue's verbs live.

## Step 2 — "Wrong to hide"

Each row carries one button, **Wrong to hide**. It is dispatched by
apply-choices through `held_review.capture_objection` and it writes one note
saying this capture should never be routed out of sight.

It is one capture and it is finished. The row does not move, nothing is
resolved, nothing is queued, and the button is not offered again on a row that
already carries an objection — a second click would turn a note into a thing
to keep answering. If the user objects to a row twice, say so in one line
("already noted") and write nothing.

There is no confirm here, no drop, no already-done and no not-mine. Those are
`needs-your-call`'s, on the ordinary queue, and mixing them into a review
would make reading the list destructive.

## Step 3 — "turn on held"

Offered in ONE place: a single line under the rendered list, after the user
has read it. Not before, not anywhere else.

```python
import sys; sys.path.insert(0, "shared/scripts")
from held_review import turn_on_held

res = turn_on_held("<WORKSPACE>")
# res["status"] -> "enabled" | "refused"
# res["line"]   -> the one sentence to relay, verbatim
```

`turn_on_held` checks three things and refuses on any of them, writing
nothing:

1. **The list has been read here.** If not, it hands back one sentence saying
   to look first. Relay it and run Step 1.
2. **The release allows it.** The disposition is switched on in the Command
   Room release itself by the people who build it, once they are satisfied it
   is safe — read through `held_tier.capability_status`, which asks
   `operator_capability.capability_status` and takes nothing from this
   workspace at all. Nobody reads this workspace's captures to decide it. If
   the release does not allow it, relay `res["line"]` and stop: nothing about
   this workspace's own numbers is holding it back, and the sentence says so.
3. **This is a workspace it was turned on for.** Same answer shape, same
   rule: it is decided outside the workspace and there is nothing to change in
   here. Relay the sentence and stop.

When it succeeds, relay `res["line"]` verbatim. It says plainly what changes:
from the next end-of-day pass, below-floor captures stop appearing in the
queue, stop being counted and stop being asked about — and they are still
written, still on disk, still retrievable, with the phrase that undoes it.

**Never** report success from anywhere but `res["status"] == "enabled"`, and
never describe the flip as on because a config value was written — the value
and the grant are two different things and `held_tier.flip_status` re-asks
both every time.

## Step 4 — "turn off held"

```python
from held_review import turn_off_held
res = turn_off_held("<WORKSPACE>")
```

Never fenced. Reverting to the shipped default is always allowed — a switch
that is hard to turn off is a switch nobody turns on. Already off is a no-op
that says so. Relay `res["line"]`.

## What this mode does NOT do

- It does not confirm, drop, close, reopen, re-date or mute any row.
- It does not decide anything on its own, and it never enables the routing as
  a consequence of rendering, of an Apply, or of an objection.
- It does not read, quote or send anyone's captures anywhere. The list is
  rendered in the workspace it belongs to and nowhere else.
- It does not measure the workspace. There is no accuracy bar, no sample
  floor, no weekly census and no score — that fence was retired on
  2026-08-22 because it measured something no workspace could produce.

## See also

- `shared/scripts/held_review.py` — the scoped view builder
  (`build_would_hold_view`), the paged render (`render_review_page`), the one
  objection writer (`capture_objection`), the three-part check
  (`enable_status`) and the two switches (`turn_on_held`, `turn_off_held`).
- `shared/scripts/held_tier.py` — the routing itself:
  `apply_held_routing`, the ONE routing writer `enable_held_routing`, and
  `capability_status`, its single seam onto the release's decision.
- `shared/scripts/operator_capability.py` — `capability_status`, the read
  that decides whether the release allows the disposition at all. Anchored to
  the plugin payload and takes no workspace argument.
- `shared/scripts/needs_review_queue.py` — `build_queue_view` and its
  `scope="would_hold"` selector; the ordinary queue and every verb that
  resolves a row.
- `skills/needs-your-call/SKILL.md` — the queue these rows live in, and the skill this mode belongs to.
- `skills/end-of-day/SKILL.md` — the fire that routes captures, and where the
  disposition takes effect.
