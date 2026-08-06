---
name: commitment-backlog-sweep
surfaces: both
description: "One pass over the whole OPEN commitment backlog, using months of mail history the daily passes never look at again. Fires on: 'clean up my commitments', 'sweep my backlog', 'commitment backlog', 'backlog sweep', 'commitment amnesty'. Closes only what historical delivery evidence settles (each with its evidence and one-word undo), then asks about the rest in ONE digest: looks handled, written twice, gone quiet for months. Add `show me first` for a preview that changes nothing. On demand only — never scheduled, registers nothing. DOES NOT fire on 'triage my commitments' / 'review my open commitments' / 'burn down my commitments' (commitment-triage — the full-set widget, no mail history), 'clean up my workspace' / 'tidy up' / 'weekly cleanup' (cleanup), 'show my list' (show-my-list), 'reconcile my sent mail' (reconcile-sent — the daily forward pass), or 'scan for commitments' (extraction backfill). Windows, caps and rails: Routing section in the body."
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

**On demand only.** It is not a scheduled task, it registers nothing, and it does
not touch the schedule set-up. It runs because somebody asked.

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
  `close_commitments` and merges through `commitment_state.supersede_commitment`.

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
cfg = load_skill_config(WORKSPACE, "commitment-backlog-sweep") or {}

window_days  = cfg.get("window_days",  sweep.DEFAULT_WINDOW_DAYS)    # 180
age_out_days = cfg.get("age_out_days", sweep.DEFAULT_AGE_OUT_DAYS)   # 45
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
view = sweep.digest_view(receipt, page=1)
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

## Routing (full trigger corpus)

Fires on: 'clean up my commitments', 'sweep my backlog', 'commitment backlog',
'backlog sweep', 'commitment amnesty', 'clear my commitment backlog',
'sweep my commitment backlog', 'find commitments i already finished',
'close out old commitments'.

Deliberately NOT claimed: `go back through my commitments` (backticked here on purpose — a quoted phrase in this section IS a claim, and claiming it is the whole problem). The catch-all
(workspace-manager) owns bare `go` as a navigation trigger, so that phrasing
matches two skills and routes to neither cleanly. A phrase that collides with the
default handler is not a trigger, it is a coin toss — the blind routing probe for
this family found it, and the honest fix is to stop advertising it rather than to
carve a hole in the catch-all.

Modifiers, not triggers (they ride a firing phrase, they never route on their
own — so they are named in backticks here, deliberately, because a bare generic
word is not this skill's to claim): `show me first` / `preview` / `dry run` for preview mode; `last N days` or `last N months` for the window; `age out at N days`
for the staleness bar.

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
