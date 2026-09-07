---
name: commitment-backlog-sweep
surfaces: both
description: "One pass over the OPEN commitment backlog, over mail history the daily passes skip. Fires on: 'clean up my commitments', 'sweep my backlog', 'backlog sweep', 'commitment amnesty', 'review amnesty', 'expire the review pile', 'review amnesty including what I un-did', 'drop everything from that meeting' (not the write-up), 'drop everything that ingest captured', 'drop everything that import captured', 'commitment backlog'. Closes what delivery evidence settles, then asks about the rest in ONE digest. Add `show me first` to preview. Both drains also run on their own, silently. DOES NOT fire on 'process the meeting' / 'meeting notes' (meeting-notes), 'triage my commitments' / 'review my open commitments' / 'burn down my commitments' (commitment-triage — no mail history), 'clean up my workspace' / 'tidy up' / 'weekly cleanup' (cleanup), 'show my list' (show-my-list), 'reconcile my sent mail' (reconcile-sent), or 'scan for commitments'."
---

# commitment-backlog-sweep

The mail matchers are **forward-only**. Both reconcile cursors only move ahead, so
evidence sitting in old mail is never re-read, and nothing in an update is
retroactive. Meanwhile the open list grows for months. This skill is the one pass
that looks backwards.

It sorts the backlog into four honest piles:

1. **already settled by evidence in your mail** — closed on the spot, each with
   the evidence line and the batch id so `undo` is one word;
2. **looks handled** — evidence short of the bar, so it asks;
3. **the same thing written twice** — grouped side by side, never merged for you;
4. **gone quiet** — no evidence and no movement for months: still real?

**The four-pile pass above runs on demand only.** It is not a scheduled task, it
registers nothing, and it does not touch the schedule set-up. It runs because
somebody asked.

**The two DRAINS are the exceptions, and neither of them is that pass.** Since
REVSCHED1 the review-tier drain runs silently as a job inside the
`maintenance` task the workspace already has — daily since UNCONFEXP1 (M's
2026-08-30 ruling: an unconfirmed extraction nags for its short window, then
closes out on its own; Review amnesty, section E); since
SWEEPSCHED1 the confirmed-tier drain runs the same way, weekly (Amnesty, section D).
Both still add no scheduled task of this skill's own and both still register
nothing: each rides that existing taskId. Nothing else above this line ever
fires on a schedule.

## Skill Boundary

- **Use commitment-backlog-sweep for:** the backwards-looking pass that reads
  historical mail for evidence, and the two judgment piles (duplicates, gone
  quiet) that nothing else surfaces.
- **Use `commitment-triage` for:** "triage my commitments" / "review my open
  commitments" / "burn down my commitments" — the full-set housekeeping widget
  sorted by age. It reads no mail history and closes nothing on evidence; it is
  the surface for working the list, this is the surface for shrinking it.
- **Use `cleanup` for:** "clean up my workspace" / "tidy up" / "weekly cleanup" —
  workspace maintenance. Nothing to do with commitments.
- **Use `show-my-list` for:** "show my list" — the retired discuss-later list.
- **Use `reconcile-sent` for:** "reconcile my sent mail" / "catch up my sent
  mail" — the daily forward pass over new mail since the cursor.

## Writer Contract

Read `shared/WORKSPACE_API.md` first. Every write goes through a locked, gated
helper — never a hand-rolled append:

- the scan's own audit event, and the automatic closes it applies — via
  `commitment_backlog_sweep.scan`, which appends the `backlog_sweep` event
  through `atomic_append_jsonl` and closes through
  `commitment_state.close_commitments` (THE single closure path);
- everything the user confirms afterwards — via
  `commitment_backlog_sweep.apply_decisions`, which closes through
  `close_commitments` and merges through `commitment_state.supersede_commitment`;
- the two bulk verbs — via `commitment_backlog_sweep.apply_amnesty` (which
  re-derives its own pile through `amnesty_plan` and hands it to
  `apply_decisions`) and `commitment_backlog_sweep.accept_handled_decisions`
  (which composes rows for `apply_decisions` and writes nothing itself). Neither
  adds a writer, an event type, or a reverser;
- the two REVIEW-TIER bulk verbs — via
  `commitment_backlog_sweep.apply_review_expiry` and
  `commitment_backlog_sweep.apply_ingest_kill`, each re-deriving its own pile
  through `review_expiry_plan` / `ingest_kill_plan` and handing it to
  `apply_decisions`. Same story: no new writer, no new event type, no new
  reverser. These two are the only verbs in this skill that reach the
  UNCONFIRMED pile, and they never reach anything else.

Reads: the mail connector (historical window only), `events.jsonl`,
`entities.json`, `_hq/data/skill_config/commitment-backlog-sweep.json`.

**The reconcile cursors are never read-for-advance and never written.** This sweep
owns its own window and must leave the daily passes' catch-up state exactly where
it was — advancing it would strand every message in between forever.

## The job (do exactly this)

**Before any python snippet (Rule 22):** resolve the plugin root and run every
snippet from it — the cwd never persists and `shared/scripts` only resolves from
the plugin root:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
```

### Step 1 — settings, and the window

```python
import sys; sys.path.insert(0, "shared/scripts")
import commitment_backlog_sweep as sweep
from primary_user import resolve_primary_user
from skill_config_writer import load_skill_config

WORKSPACE = "<absolute path to the workspace root>"
user_id = resolve_primary_user(WORKSPACE)     # deterministic — never guess (Bug #102)

# `load_skill_config` returns the whole ENVELOPE (schema_version, configured_at,
# skill_name, config) — the values live one level down. Reading the envelope
# directly is the bug that makes a configured workspace silently fall back to
# the defaults below, with nothing going red. Unwrap it exactly as the
# verified-correct reader does (`commitment_backlog_sweep._configured_age_out_days`),
# flat-shape tolerance included.
saved = load_skill_config(WORKSPACE, "commitment-backlog-sweep") or {}
cfg = saved.get("config") if isinstance(saved.get("config"), dict) else saved

window_days  = cfg.get("window_days",  sweep.DEFAULT_WINDOW_DAYS)    # 180
age_out_days = cfg.get("age_out_days", sweep.DEFAULT_AGE_OUT_DAYS)   # 30
item_cap     = cfg.get("item_cap",     sweep.DEFAULT_ITEM_CAP)       # 60
```

The command overrides the config: "sweep my backlog, last 90 days" sets
`window_days=90`; "age out at 60 days" sets `age_out_days=60`. Say which numbers
you used, in plain words, in the answer.

A previous run that stopped at the cap left its resume point on its own receipt:

```python
prior = sweep.last_scan(WORKSPACE)            # newest audit's data, or None
resume_after = prior.get("resume_after") if (prior and prior.get("has_more")) else None
```

If `prior` exists and `has_more` is False, say so plainly ("I swept the whole
backlog on <date> — running again only picks up what has changed since") and
continue only if they want it.

### Step 2 — fetch the historical mail, and ask for the window TWO ways

Resolve the mail connector's search/list tool at runtime by tool name (the server
id is per-install — never hard-code it). Then:

```python
from connector_adapters.mail import compile_search
from connector_adapters.provenance import resolve_mail_provider

provider = resolve_mail_provider(WORKSPACE)              # the declared backend
start    = sweep.window_start(days=window_days)          # ISO instant
sent_query    = compile_search(sweep.window_intent(start, direction="sent"), provider)
inbound_query = compile_search(sweep.window_intent(start, direction="inbound"), provider)
extra_params  = sweep.structured_window_params(provider, start)   # e.g. {"start_date": ...}
```

**Pass BOTH**: the compiled query AND `extra_params` into the connector call.
Where a provider exposes a real structured date parameter, that is the one that
enforces; where it does not, the compiled form asks in the provider's own terms.
Neither is trusted — Step 3 re-checks every returned message against the window
itself. On this substrate a floor asked for in words has been ignored by hours and
by months, repeatedly, so it is checked rather than assumed.

Build the two lists exactly as the daily passes do:

- `sent_messages`: `{message_id, ts, thread_id, has_attachment,
  recipient_person_ids, recipient_names, subject, body}`
- `inbound_messages`: `{message_id, ts, sender_person_id, subject, body,
  thread_id, has_attachment}`

**`ts` must be the connector's own raw ISO-8601 timestamp, never a display date.**
It is the one field where being absent is not safe here: across months of mail
MOST messages predate MOST open items, and the ordering check that refuses those
is fed by `ts`. A message with no usable date is DROPPED by the sweep and counted,
rather than scored with the check switched off. `thread_id` and `has_attachment`
are what let a message be recognized as the delivery rather than as words about
it; omitting them is safe but leaves those checks inert, and the receipt says so.
Never infer `has_attachment` from a body that says "attached".

**If the mail read cannot happen at all** — no connector resolves, the budget is
exhausted, every account is unclassified — call Step 3 with empty lists AND
`fetch_blocked="<what was missing, in plain language>"`. The audit lands stamped
blocked, nothing is closed, and the self-check refuses it. Do not hand it empty
lists and let a clean zero stand for a read that never happened.

### Step 3 — the scan (ONE call)

```python
receipt = sweep.scan(
    WORKSPACE,
    user_person_id=user_id,
    sent_messages=sent_messages,
    inbound_messages=inbound_messages,
    window_days=window_days,
    age_out_days=age_out_days,
    item_cap=item_cap,
    resume_after=resume_after,
    provider=provider,
    dry_run=False,          # True for "show me first" — see below
)
```

**Default mode applies the automatic tier** — exactly the closes the daily
matchers would have made in real time, narrowed to the two evidence bases
(you delivered it; they replied in the conversation the item came from). A match
that rests only on the wording of a subject line is never applied automatically
here, however high it scores: over months of mail a subject that names the thing
is the common case, not the rare one. Those land in the "looks handled" pile.

**"show me first" / "preview" / "dry run" sets `dry_run=True`**, and then the run
writes NOTHING except its own audit row — no closes, no archives, no proposals.

An unresolved primary user raises `PrimaryUserUnresolvedError`: nothing is read,
nothing is written, no audit row exists. Do NOT catch it and continue — with no
user, every check is inert and a clean zero would be a lie about an empty backlog.

### Step 4 — self-validate (mandatory)

```python
v = sweep.validate_sweep_ran(WORKSPACE)
# v["ok"] must be True. False -> do not report success; say what happened.
```

### Step 5 — ONE digest, through the canonical transport

```python
from widget_transport import render_and_persist
# CLUSTER1 — pass the workspace so the "Looks handled" and "Gone quiet"
# lists render one line per real-world item (survivor + "+N folded" +
# read-only expand + the `keep as one` tap, ids widget-embedded). The merge
# and auto-closed sections are untouched, and with nothing clustering the
# view is byte-identical.
view = sweep.digest_view(receipt, page=1, workspace_root=WORKSPACE)
transport = render_and_persist(
    data_view=view, wrapper="fragment",
    persist_dir="<WORKSPACE>/_hq/.system/widgets", page=1)
```

Relay `transport["html"]` verbatim as `mcp__visualize__show_widget`'s
`widget_code`. Never hand-write the widget HTML and never write it anywhere else —
the rendered digest already carries the coverage block (what was read, what could
not be reached and why, how many items are anchored to an email conversation at
all, how big the meeting-sourced pile is), and it reads it out of the view under
the key the widget mode it chose actually renders. Do not re-narrate those numbers
above the widget; the widget says them.

Then STOP. Widget, then a short plain-English line if anything needs saying —
`receipt["summary"]` is written for that and can be pasted verbatim. No event-type
names, no field names, no counts you typed yourself.

**One extra line, and only when the quiet pile is big.** When
`receipt["n_age_out"] >= sweep.AMNESTY_OFFER_AT` (10), add exactly one line under
the widget offering the bulk clear: say `commitment amnesty` and the whole quiet
pile goes in one confirm. Below that count the pile is workable row by row and the
offer is noise. Never volunteer it for the other three piles — the duplicate and
looks-handled piles are judgment, not volume.

**And one for the OTHER pile, on the same rule (REVSCHED1 §3-1).** The four piles
above are the CONFIRMED backlog. The unconfirmed pile — the guesses nobody has
answered — is a separate tier with its own bulk verb, and it was reachable only by
a user who remembered the phrase. Ask for its offer line:

```python
offer = sweep.review_offer(WORKSPACE, user_person_id=user_id)   # None below the bar
```

When it returns a dict, paste `offer["line"]` verbatim as a second line under the
widget. `sweep.REVIEW_OFFER_AT` (10) is the bar and `review_offer` applies it — do
not compare a count yourself, and do not compose the sentence: the queue header
prints the same line from the same function so the two surfaces cannot offer two
different things. It is a PURE READ that never refuses; `None` means "no offer",
including on a workspace whose primary user cannot be resolved, and it is silence
rather than an error line under a list the user came to read.

### Step 6 — Apply (only when the user acts)

The digest's Apply button sends the standard `apply choices: [...]` message, which
`apply-choices` dispatches back here (`src: "backlog-sweep"`). Nothing in the
three manual piles is written before that.

```python
out = sweep.apply_decisions(
    WORKSPACE, decisions,             # rows carry commitment_id + bucket + action
    user_person_id=user_id,
    batch_id=receipt["batch_id"],     # the SAME batch, so one `undo` covers the run
)
```

Verbs, and only these: `mark done` on a "looks handled" row (closes, resolution
`done`, the row's own evidence attached); `drop` on a "gone quiet" row (closes,
resolution `dropped`, marked as an age-out so it is distinguishable from a
deliberate drop — nothing is deleted and `undo` reopens it); `merge` on a
duplicate group (folds into the OLDEST item, keeping both sources, the absorbed
item closing as a duplicate). `still valid` / `keep both` / `skip` write nothing
at all — a decision to leave something alone is not an event.

**The in-context bulk verbs.** While the digest is on the table two of the piles
can be answered wholesale instead of row by row. Both need an explicit yes, both
ride the digest's OWN `batch_id`, so one `undo` still covers the entire run.

`mark them all done` / `accept all` — the "looks handled" pile:

```python
rows = sweep.accept_handled_decisions(receipt)   # [] when that pile is empty
out  = sweep.apply_decisions(WORKSPACE, rows, user_person_id=user_id,
                             batch_id=receipt["batch_id"])
```

It composes ONLY the proposed rows, each closing on its own evidence line. An
empty list means there was nothing to accept — say so; never apply `[]` and
report a successful run of zero. This verb lives only while the receipt is in
hand: the mail evidence behind those rows is stored nowhere, so a later turn has
to run the sweep again to get it back. Rows that were already settled elsewhere
come back as no-ops and are acknowledged honestly, not re-closed.

`drop all the quiet ones` — the gone-quiet pile, at the bar this digest used:

```python
plan = sweep.amnesty_plan(WORKSPACE, older_than_days=receipt["age_out_days"])
# show plan["confirm"], get an explicit yes, THEN:
out  = sweep.apply_amnesty(WORKSPACE, user_person_id=user_id,
                           older_than_days=receipt["age_out_days"],
                           batch_id=receipt["batch_id"])
```

A bulk drop gets the same confirm sentence whether it is reached from the digest
or from the phrase — see the Amnesty section below for what that sentence has to
carry and why it is never paraphrased.

## Amnesty — the whole quiet pile in one confirm

**`commitment amnesty` comes straight here.** No mail read, no Steps 2–3. The
quiet pile is a question about the event log alone — no evidence to fetch, no
cursor to touch — so making somebody sit through a 180-day mail pass to answer it
is exactly why that phrase never did what it says. The sweep's other triggers
still run the full pass above.

Why this cannot be a replay of a digest: the sweep's audit event does not persist
the quiet rows, so there is nothing on disk to replay. Amnesty re-derives, every
time, and that is the right shape — an item that stopped being quiet in the
meantime simply is not in the answer.

### A — the plan (writes nothing)

```python
import sys; sys.path.insert(0, "shared/scripts")
import commitment_backlog_sweep as sweep
from primary_user import resolve_primary_user

WORKSPACE = "<absolute path to the workspace root>"
user_id = resolve_primary_user(WORKSPACE)        # deterministic — never guess
plan = sweep.amnesty_plan(WORKSPACE)             # "past 90 days" -> older_than_days=90
```

The bar resolves itself, in this order: the number in the phrase, else the
workspace's configured `age_out_days`, else **30 days**. That default moved from
45 in SWEEPSCHED1 and this phrase inherits it deliberately: the weekly job below
clears at the same bar, and a phrase that offered one number while the schedule
acted on another would be two answers to one question. `amnesty_plan` reads
`events.jsonl` and nothing else, and it writes nothing at all — no audit row, no
close, no cursor.

`plan["ok"]` is False when the bar is under 7 days. Say `plan["reason"]` and
stop: under a week is this week's work, and one confirm is the wrong shape for
it. Do not silently substitute a number they did not give.

### B — the confirm (prose, and it has to be theirs)

Show `plan["preview"]` — the pile, oldest first — then `plan["confirm"]`
VERBATIM. That one sentence already carries the count, the bar, the fact that
nothing is deleted, and the one-word undo. Do not paraphrase it, do not shorten
it, do not split it: a bulk verb is exactly as safe as the sentence the user said
yes to. When the pile is empty `plan["confirm"]` says so — say that and stop.

Wait for an explicit yes. An unanswered offer is not a yes, and neither is
"sure, clean it up" said about something else.

### C — apply, on that yes and nothing else

```python
out = sweep.apply_amnesty(WORKSPACE, user_person_id=user_id,
                          older_than_days=<the same number you showed, or None>)
```

It re-derives the pile itself and takes no row list — there is deliberately no
parameter to hand it one. Anything settled between the offer and the yes falls
out silently, which is correct: what the user confirmed was a pile, not a set of
ids.

Ack with `out["summary"]` verbatim. If `out["n_applied"]` is short of
`out["n_planned"]` the summary already says why — do not smooth that over. If
`out["ran"]` is False nothing was written at all; say `out["reason"]` instead of
reporting a run that cleared zero.

Every close lands reversibly, as an aged-out drop in ONE `swb_` batch, so a
single `undo` reopens all of them.

### D — and it also runs weekly, on its own

Since SWEEPSCHED1 the same clear runs as a silent weekly job inside the already-
registered `maintenance` task (`maintenance_dispatcher.MAINTENANCE_JOBS`,
`age-out`, Sunday, right after the review-tier drain). Nothing about that path is
yours to fire from a chat: the dispatcher decides due-ness in code, and the
registered prompt runs `commitment_backlog_sweep.py age-out --apply`.

**Its first three fires PROPOSE and close nothing.** They line up the same pile
and leave the offer as their one line — the count, the bar, and the fact that
saying `commitment amnesty` does it now. From the fourth fire on it applies by
itself, reversibly, in one `swb_` batch like any other amnesty. The job counts
its own prior fires off its own receipts, so there is no setting anywhere for
anybody to turn on, and nothing for you to check before answering a question
about it.

Three consequences for what you say to the user:

- when the job's `receipt_line` is non-empty, the next staff meeting / end-of-day
  reads THAT ONE LINE out verbatim and nothing else. On a proposing fire it is
  the offer; on an applying fire it names the count, the window, the standing
  `undo`, and `my plate` for what remains;
- an empty plan is a silent no-op — the fire still receipts (that is how it stays
  off the next slot), but its line is empty and the user hears nothing;
- it never touches an unconfirmed capture. Those are the review tier's, and the
  fence is inside `amnesty_plan` itself, not in this job.

## Review amnesty — the UNCONFIRMED pile, which is a different pile

**`review amnesty` comes straight here.** Everything above this heading is about
CONFIRMED work: things somebody agreed were real. This section is about the other
pile entirely — the unconfirmed extractions sitting in the needs-your-call queue,
which on a mature workspace is the biggest number anywhere in it.

Why they need a verb at all: an unconfirmed extraction is a GUESS. It cost
nothing to write, it is barred from auto-close and from chasing, and nothing in
the system ever retires one. So the queue only grows, and a queue nobody can
finish is a queue nobody opens. The per-row verbs on that queue are correct and
unchanged — they are simply not a plan for hundreds of rows.

**Say both halves of this, out loud, whenever you run it.** Clearing the pile
**drains the stock**. It does nothing about the **inflow** — what stops the queue
refilling is the capture side getting better at deciding which extractions to
commit and which to surface, which is separate work. A user who is told only the
first half will clear the pile once, watch it refill, and conclude the product
does not work. Two sentences now is cheaper than that.

There are two verbs, and they are not interchangeable:

| verb | the argument it makes | reaches |
|---|---|---|
| **review expiry** | time — nobody answered inside the window | every unconfirmed capture with no movement for N days |
| **ingest kill** | provenance — one import went wrong | every unconfirmed capture from ONE named meeting or import |

### A — the plan (writes nothing)

```python
import sys; sys.path.insert(0, "shared/scripts")
import commitment_backlog_sweep as sweep
from primary_user import resolve_primary_user

WORKSPACE = "<absolute path to the workspace root>"
user_id = resolve_primary_user(WORKSPACE)        # deterministic — never guess

plan = sweep.review_expiry_plan(WORKSPACE, user_person_id=user_id)
# "expire the review pile past 30 days" -> older_than_days=30
```

The bar resolves itself, in this order: the number in the phrase, else the
workspace's configured `review_expiry_days`, else **2 days**
(`UNCONFIRMED_NAG_DAYS` — UNCONFEXP1, from M's 2026-08-30 ruling: an
unconfirmed extraction "can nag for like a day or two", then it closes out).
That is a much shorter fuse than the confirmed pile's 30, deliberately: a
guess nobody answered while the conversation was still warm is a guess whose
context has gone, and waiting will not improve the answer — the old
escalate-and-nag posture measurably produced accumulation, not answers.
`review_expiry_days` is a separate config key from `age_out_days` — one
number for both piles would silently move whichever was tuned second.

`plan["ok"]` is False in two cases. Under **1 day** the bar is below the floor:
say `plan["reason"]` and stop, because anything younger is what the workspace
heard this morning and one confirm is the wrong shape for it. And when the primary
user cannot be resolved it refuses outright — "yours vs not yours" computed
against nobody is a wrong answer, not a smaller one.

For one bad import, name it instead of picking a date:

```python
plan = sweep.ingest_kill_plan(WORKSPACE, "<meeting or ingest id>",
                              user_person_id=user_id)
```

No threshold and no floor on this one: it argues from a defect rather than from a
date, so a cluster written an hour ago is exactly the case it exists for. Either
spelling of the id works (`granola:<id>` or the bare `<id>`) — use the one the
user's own surface showed them. A blank id is REFUSED rather than matched against
nothing; a confident "nothing to clear" about a pile sitting right there is the
worse answer.

### B — the confirm (prose, and it has to be theirs)

Show `plan["preview"]` — **yours first, then not yours** — and then
`plan["confirm"]` VERBATIM.

The owner split is on screen because roughly half a real queue page is other
people's promises, captured because they were said in the user's meeting. Someone
deciding whether to clear hundreds of rows needs to see which half is which
BEFORE they answer; a single total hides exactly the fact that changes the answer.
`plan["n_review_total"]` is how big the whole tier is, so you can say how many of
the whole pile this window reached instead of implying it found everything there
is.

Do not paraphrase the confirm, do not shorten it, do not split it. It already
carries the count, the split, the bar, the fact that nothing is deleted, the
one-word undo — and the one thing a user could otherwise get wrong: clearing does
NOT stop the same thing being captured again. If it comes up in a later meeting or
a later email it is fresh evidence and the workspace will capture it again,
normally. A user who thinks this means "never hear about this again" is being
asked to say yes to something the system will not do.

Wait for an explicit yes. An unanswered offer is not a yes.

### C — apply, on that yes and nothing else

```python
out = sweep.apply_review_expiry(WORKSPACE, user_person_id=user_id,
                                older_than_days=<the same number you showed, or None>)
# or, for one bad import:
out = sweep.apply_ingest_kill(WORKSPACE, user_person_id=user_id,
                              source_ref="<the id you showed>")
```

Both re-derive their own pile and take no row list — there is deliberately no
parameter to hand them one. Anything confirmed, dropped or answered between the
offer and the yes falls out silently, which is correct: what the user confirmed
was a pile, not a set of ids.

Ack with `out["summary"]` verbatim. When `out["n_applied"]` is short of
`out["n_planned"]` the summary says why **per cause**, read off what the writer
actually returned — do not smooth that over and do not substitute a tidier
reason. There are three, and only the first is a race:

- **already answered** — somebody settled the row between the offer and the yes;
- **held back** — the row owns smaller pieces that are still open, and clearing
  the top one would have closed them too, so the closure path refused. This is
  not a race and it will repeat on every run until those pieces are dealt with,
  which is why the summary says so out loud instead of calling it answered;
- **refused** — anything else. Named as a refusal with no story attached,
  because inventing one is exactly the bug this wording replaced.

When `out["ran"]` is False nothing was written at all; say `out["reason"]` rather
than reporting a run that cleared zero. When `out["ran"]` is True but
`out["n_applied"]` is 0, the summary leads with "Nothing was cleared" and offers
**no** undo — do not add one back, there is no batch to reopen.

Every close lands reversibly in ONE `swb_` batch, so a single `undo` puts the
whole batch back **in the queue** — as unconfirmed captures, exactly the state
they left. Note that the undo is itself movement, so the same expiry phrase will
not immediately re-lapse what the user just put back; that is the point of undo.

### D — the door: `review amnesty including what I un-did`

That shield has a measured cost. An exploratory apply → undo resets the quiet
clock on every reopened row, so the same phrase cannot reach them again for a
whole re-accrual window. On 2026-08-19 one operator `undo` of a 106-row batch
held all 106 out of the bulk verb for a fresh 14 days, and the next run could
reach 21 of the 86 rows that were over the bar. Try-before-you-buy was not a flow
the design permitted.

So there is now an explicit door, and it is a SEPARATE PHRASE — never a default,
never inferred from "the user seems to want the whole pile":

```python
plan = sweep.review_expiry_plan(WORKSPACE, user_person_id=user_id,
                                include_reopened=True)
# ...and the SAME flag again on the apply — it is not carried on the plan object:
out = sweep.apply_review_expiry(WORKSPACE, user_person_id=user_id,
                                include_reopened=True)
```

What the flag does, exactly: it measures quiet against every movement type EXCEPT
the reopen. So a row whose only movement since capture is an `undo` is measured
from what it last actually did and can lapse again — while a row that was reopened
**and then genuinely touched** (re-worded, re-dated, re-owned, chased, adjudicated)
is still shielded by that touch. All other movement still shields. That is the
whole difference between a door and a bulldozer.

`plan["confirm"]` says so out loud when the flag is on — it names that the run
reaches rows the user personally put back, and that anything they have touched
since is left alone. **Show it verbatim like any other confirm and do not
paraphrase that clause away**; it is the only warning the user gets, and an ack
would arrive after the write.

On an ordinary CLOSED-door plan, `plan["n_shielded_by_reopen"]` is how many rows
the door WOULD have reached — i.e. how many are being held out by an undo and
nothing else. Say it when the user asks why the pile is not draining. Do not act
on it.

### E — the drain also runs daily, on its own

Since REVSCHED1 the expiry runs as a silent job inside the already-
registered `maintenance` task (`maintenance_dispatcher.MAINTENANCE_JOBS`,
`review-expiry`) — DAILY since UNCONFEXP1, served at the day's first fire,
before the morning brief (M's 2026-08-30 ruling: an unconfirmed extraction
nags for its short window — `UNCONFIRMED_NAG_DAYS`, default 2 days — and
then closes out on its own, superseding the old escalate-until-answered
posture). It plans internally, applies
when the plan is non-empty, and writes one receipt either way — the empty plan is
a silent no-op. Nothing about that path is yours to fire from a chat: the
dispatcher decides due-ness in code, and the registered prompt runs
`commitment_backlog_sweep.py review-expiry --apply`.

Three consequences for what you say to the user:

- when the job's `receipt_line` is non-empty, the next staff meeting / end-of-day
  reads THAT ONE LINE out verbatim and nothing else. It names the count, the
  window, the standing `undo`, and `needs your call` for what remains;
- the morning brief's CHANGED line discloses a lapse the same way every other
  brain act is disclosed: `change_feed` counts the written `review_expired`
  closures and renders ONE quiet line with the count and the `undo`
  affordance — only when something actually lapsed, never filler;
- the scheduled path never uses the door. It always runs the ordinary closed-door
  window, so a row the user put back stays put back until they say the door
  phrase themselves (the undo is movement, so it also restarts that row's
  quiet clock for a fresh window).

### What these two verbs are NOT

- **NOT a Drop.** A Drop is the CEO looking at one row and letting it go, and the
  workspace learns from that — enough of them and it proposes to stop capturing
  that counterparty at all. A bulk lapse carries no such judgment about anybody,
  so it feeds none of that learning. The per-row Drop on the queue is unchanged.
- **NOT a delete.** The capture stays in history.
- **NOT the inflow fix.** Said again because it is the sentence clients remember.

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

Fires on: 'clean up my commitments', 'sweep my backlog', 'commitment backlog',
'backlog sweep', 'clear my commitment backlog', 'sweep my commitment backlog',
'find commitments i already finished', 'close out old commitments' — each of
which runs the full pass, Steps 1–6.

Fires on 'commitment amnesty' as well, and that one lands somewhere different:
the Amnesty section, not Step 1. Same skill, different door. No mail is read and
no digest is rendered — plan, confirm, apply. It used to trigger the whole
180-day fetch, which meant the one phrase that names a bulk clear was the slowest
way to get one.

Fires on 'review amnesty' — a THIRD door, and a different pile: the Review
amnesty section, for the unconfirmed captures in the needs-your-call queue. Same
plan/confirm/apply shape, no mail, no digest. The two amnesty phrases are not
synonyms and must never be treated as one: 'commitment amnesty' clears quiet
CONFIRMED work, 'review amnesty' lapses UNCONFIRMED guesses, and the module
refuses to let either reach the other's rows.

Fires on 'expire the review pile' (with or without a `past N days` bar) — the SAME
Review amnesty section as 'review amnesty', by the word a user reaches for when
they are thinking about a window rather than a mercy. It shipped with no door:
`expire` appeared in zero descriptions in the whole roster, so two independent
blind routing passes reached this skill zero times out of two on 'expire the
review pile past 30 days' while the number it names was already an honoured
parameter.

Fires on 'review amnesty including what I un-did' — the §0-3 DOOR, section D. It
is a separate phrase because it does something the ordinary phrase deliberately
will not: it reaches rows the user themselves put back with `undo`. Never inferred
and never the default — a request to clear the whole pile is not this phrase.
(Deliberately not written with the obvious bare word in quotes: the mechanical
routing matcher reads a quoted string in this section as a phrase this skill
CLAIMS, so quoting a generic word here to disclaim it is how you claim it. It
collided with two neighbours the first time this paragraph was written.)

Fires on 'drop everything from that meeting', 'drop everything that ingest captured' and 'drop everything that import captured' — a FOURTH door, the same Review amnesty section by its other verb: the ingest kill, for the unconfirmed cluster ONE bad import left behind. It argues from provenance, not from a date, so there is no window to say. These two phrases had no door at all until they were claimed here; the verb was reachable only by a user who first said 'review amnesty' and then read the manual, which is not a door.

Three things that phrasing is NOT. The fences are load-bearing because `drop` and `meeting` are the most contested words in this roster — and they are backticked here for the same reason `go back through my commitments` is below: a quoted phrase in this section IS a claim, and claiming a bare word would hand this skill every phrase that happens to contain it.

- **Not `process the meeting` / `meeting notes` (meeting-notes).** That turns a conversation INTO artifacts. This deletes nothing of the kind — it clears the guesses an extraction already wrote. If the user wants the write-up, they are not asking for this.
- **Not the queue's per-row Drop.** That is one row the user is looking at and has judged, and the workspace learns a per-counterparty suppression signal from it. This is a bulk lapse nobody adjudicated, it teaches the capture side nothing, and the module keeps the two apart on purpose.
- **Not a delete of the meeting, the transcript, or anything confirmed.** Only unconfirmed captures sharing that ingest's id are in reach, and a confirmed commitment from the same meeting survives it.
- **Not undoing the import itself (workspace-ingest / ingest-context).** This is the reason the stems say `captured` rather than `everything from`. To most people "drop everything from that import" means the whole import — the people, the projects, the copied files — and workspace-ingest is the only skill that can take those back. Run this verb on that request and it closes the commitment rows, leaves everything else standing, and reports a partial fix as a whole one. Both ingest skills now disclaim the captured-commitments sense in their own descriptions, and this one disclaims theirs; if the user wants the import gone, hand it over rather than doing the half you can.

If the user names a meeting but wants the work itself gone rather than the guesses, that is the confirmed pile and the wrong verb — say so and offer 'commitment amnesty' instead of clearing something they did not ask about.

Deliberately NOT claimed: `go back through my commitments` (backticked here on purpose — a quoted phrase in this section IS a claim, and claiming it is the whole problem). The catch-all
(workspace-manager) owns bare `go` as a navigation trigger, so that phrasing
matches two skills and routes to neither cleanly. A phrase that collides with the
default handler is not a trigger, it is a coin toss — the blind routing probe for
this family found it, and the honest fix is to stop advertising it rather than to
carve a hole in the catch-all.

Modifiers, not triggers (they ride a firing phrase, they never route on their
own — so they are named in backticks here, deliberately, because a bare generic
word is not this skill's to claim): `show me first` / `preview` / `dry run` for preview mode; `last N days` or `last N months` for the window; `age out at N days`
for the staleness bar; `past N days` — on the amnesty phrase only — for the bar a
bulk clear uses this run, floored at 7.

In-context replies, not triggers either — they are dispatched by the digest that
is already on screen, never by the router: `mark them all done` / `accept all`
(the looks-handled pile) and `drop all the quiet ones` (the gone-quiet pile).
Both are described in Step 6.

Every fence below is deliberately written on ONE line each. The mechanical
routing matcher reads a fence clause up to the first line break, so a phrase that
wraps onto the next line stops being a fence and starts being a trigger of this
skill's own — which is a collision, not a fence.

Does NOT fire on: 'triage my commitments', 'commitment triage', 'review my open commitments', 'show me my commitments', 'burn down my commitments' — all commitment-triage.

Does NOT fire on: 'weekly cleanup', 'clean up my workspace', 'clean up the workspace', 'tidy up', 'deep clean' — all cleanup, which is workspace maintenance and has nothing to do with commitments.

Does NOT fire on: 'show my list', 'whats on my list', 'my list' — show-my-list.

Does NOT fire on: 'reconcile my sent mail', 'catch up my sent mail' — reconcile-sent, the daily forward pass over new mail since the cursor.

Does NOT fire on: 'scan for commitments', 'what am i waiting on' — scan-for-commitments creates items from sources; the daily Waiting On chat renders the actionable subset. This skill settles what is already open.

**Why the fence with commitment-triage is a real line and not a coin toss.** Both
surfaces touch the open set, and they answer different questions.
commitment-triage renders everything open, oldest first, and asks the user to
decide each row — no mail is read, nothing closes on evidence, and the answer is
always some version of what would you like done with these. This skill reads
months of mail history, closes what the evidence settles without asking, and
surfaces two piles commitment-triage cannot compute at all (duplicates already
open, and quiet items measured against a configurable staleness window). Cleaning
up is the verb for shrinking a backlog; triaging and reviewing are the verbs for
working through one. The two trigger sets share no phrase, and each description
names the other by name.

**And with cleanup.** cleanup's own fence already says a bare clean-up-a-thing
phrase is not its trigger — only workspace-shaped cleanup fires it. So the
commitment-shaped phrasing routes here, cleanup keeps the workspace-shaped
phrasing, and neither set contains a phrase from the other.

## What it does NOT do

- **No transcript re-scoring.** Meeting-sourced items cannot be settled by mail,
  and re-reading transcripts for completion signals is not this build. Those items
  are served by the quiet and duplicate piles, and the digest says so — on a
  mature workspace they are most of the pile.
- **No automatic merges, ever.** Two items looking alike is a judgment call, not
  an evidence call.
- **No bulk verb for the duplicate or looks-handled piles beyond accept-all.**
  Amnesty reaches the quiet CONFIRMED pile and nothing else; accept-all reaches
  the looks-handled pile and nothing else; review expiry and ingest kill reach
  the UNCONFIRMED pile and nothing else. There is no wholesale merge and no
  wholesale auto-close, because volume is the reason a pile gets one verb and
  judgment is the reason the others cannot have one. The module refuses a foreign
  row rather than restamping it, and each verb's reach is checked against the
  live substrate at write time — not against the list it was shown.
- **No crossing between the confirmed and unconfirmed piles, in either
  direction.** An amnesty can never clear an unconfirmed capture and a review
  expiry can never close real work. That is enforced in the module, not here:
  prose is advice, and a bulk verb needs a contract.
- **No deletes.** A "gone quiet" item is closed, reversibly, and its capture stays
  in history.
- **No cursor movement.** The daily passes' window is untouched.
- **No schedule.** It cannot be set to run on its own, and it registers nothing.

## Gotchas

- **A big "ignored N older messages" number is the safety check working**, not
  breakage. Across a wide window most mail predates most promises, and a message
  that arrived before the promise cannot be evidence that the promise was kept.
  `receipt["summary"]` already says this in plain words — do not apologise for it.
- **A quiet run is explained, not guessed at.** If almost nothing was reachable,
  the coverage block says how many items have nobody attached and how many have no
  email trail. That is the answer, not "nothing to do".
- **The cap is not a failure.** When `has_more` is True the digest says where it
  stopped and the same phrase again resumes from there.
