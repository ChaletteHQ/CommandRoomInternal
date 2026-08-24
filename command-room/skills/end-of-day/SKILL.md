---
name: end-of-day
surfaces: both
description: "Close the day in one pass. Fires on 'end of day', 'close out my day', 'daily wrap', 'wrap my day', and the scheduled evening chat: reconciles the day's sent mail and chat, scores the day against this morning's plan, surfaces what slipped with one-tap verbs, and records what tomorrow is about. Does NOT fire on 'morning briefing', 'weekly recap', 'friday wrap', 'end session', or 'process the call'."
---

# End of Day — the evening bookend

**For:** the 5 PM weekday close, and any time the CEO says the day is over. One
chat, not two. The day-close should make the open book SMALLER most days, and
what it reads it writes to memory with a resolvable pointer — the daily close
IS the daily memory commit.

The scheduled fire runs
`skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`
under EITHER of two taskIds — `end-of-day` (the current id, minted by EOD2) or
`past-meetings` (the predecessor, still registered on every machine that has
not taken the rename offer). Both map to that one file, so both fire this same
pack at the same hour, forever; the rename is proposed, never applied, and a
workspace that ignores it loses nothing. The file is the fire's exact steps;
this skill is the on-demand entry point and the source of truth for the render
contract all paths share. The fire's receipt keeps the `past-meetings` task_id
on both ids (`end_of_day.TASK_ID`) so the day-close series never splits.

## The four phases, in order. The order IS the contract.

1. **Close** — reconcile what the day discharged, IN THIS FIRE. Same machinery
   the maintenance jobs run (`reconcile_sent_commitments.reconcile_and_receipt`
   for mail, `chat_reconcile.reconcile_chat_and_receipt` for chat), same
   cursor, same audit event. The 17:45 maintenance pass then finds an advanced
   cursor and closes nothing twice.
2. **Reconcile** — resolve what changed: people, deal stages, identities. The
   existing maintenance machinery, run same-day and receipted. No new logic.
3. **Read** — the seven blocks below. This is the surface.
4. **Capture, last and fenced** — meeting processing is the FINAL leg, and it
   is last for a reason that is not tidiness: a capture written earlier in the
   fire would be scored by this same fire's reconcile and counted by this same
   fire's read. Capture last is the circularity fence.

## The eight blocks

`surface_drivers.build_end_of_day_pack(workspace_root, mode=...)` builds all of
them in ONE call (the t3 FB-9 pattern). Render order is the contract:

| Block | What it is | The rule that governs it |
|---|---|---|
| `alarm_lines` | `substrate_health.substrate_alarm_lines` | Verbatim, pinned top, never suppressed |
| `coverage` | What this fire actually READ, per capability | Verbatim, first under the alarms, **never suppressed and never softened** — see below |
| `score` | This morning's plan against today's closures, plus the saved digest read back | **No morning receipt → "No plan on record this morning." NEVER a guessed score.** An item with no close on file is **"Not recorded", never "not done"**. `first_move` is an ANNOTATION, not a string; `ledger` is the book's movement — see below |
| `wins` | What moved since the morning fire, by NAME | Zero wins → one honest line. Never padding. `title_source` and `more_line` govern the rows — see below |
| `slipped` | The ball-is-on-you rows | Max 3, verbs on each, and every row comes from the GATED needs-attention set — never a fresh scan. `more_line` carries the denominator. An item 3+ days overdue is asked about ONCE and then rests until answered (OVERDUE1) — see below |
| `confirm` | `confirm_flow.select_confirm_items`, relocated here, plus (PERSONLOOP1) `end_of_day.compute_person_candidates` | Cap 5, stakes-then-age. A held capture can never enter it. `more_line` carries the denominator. The candidate rows sit at the END of the block and are capped at 2 — they are the reason the block is as long as it is |
| `tomorrow` | The wide calendar look, the rollover, the day-intent draft | The draft is a PROPOSAL until tapped. It is written only on confirm |
| `sign_off` | Computed, never composed | Zero urgent → "Nothing else needs you before tomorrow's brief." verbatim |

A ninth key, `catchup`, is not a block: it is the fire-level LABEL a late
day-close arrives under, and it renders above everything (see "The catch-up
read" below). On every non-degrade fire its `renders` is False and there is
nothing to place.

### `coverage` — say what you read before you say what you found

The fire asks for email, calendar and chat per capability and receipts every
gap under `connector_gaps` — and said nothing about any of it in chat, by
design. The cost was measured: a chat cursor sat five days behind while the
surface reported the day's closes with no qualification at all, and a calendar
outage rendered byte-identically to a genuinely empty tomorrow. The reader
could not tell *nothing happened* from *I could not look*.

`coverage["lines"]` is composed in code and printed VERBATIM, first, under the
alarms. One line per capability: mail and chat through their own cursors —
**naming the span when a cursor is behind** — calendar present or absent, and
the capture leg's window with what is on record in it and what is still owed. A
capability that was skipped says so in the plain English the leg already
receipted. The last line, when present, is the data-quality note: the count of
closes in this window citing no artifact anyone can open.

**Never suppressed and never softened**, the same posture as `alarm_lines` and
for the same reason. A degraded read is exactly when the reader most needs to
know what the aperture was. Do not re-word a line, do not drop the stale-cursor
clause because the numbers look right, and do not add a reassuring sentence
after it. `coverage["capabilities"]["calendar"]["read"]` and
`tomorrow["calendar_available"]` are ONE boolean by construction; never write a
sentence that puts them in conflict.

### `score.ledger` — what the day did to the open book

Present on BOTH score branches, `no_plan` included: a day with no morning plan
still moved the book. Render `ledger["line"]` verbatim.

`status: "movement"` — book at open → `+opened` → `−closed` → `−dropped` → book
now, with the delta named. When `residual_line` is set, print it: the four
movements did not account for the whole change and saying so is cheaper than a
number that does not add up. Never absorb the residual into a movement.

`status: "no_opening_figure"` — **no morning fire, so there is "no opening
figure on record" and NO arithmetic.** The opening figure is READ off the
morning fire's own `brief_state`, never inferred. Render the line as given and
stop: no substituted count, no delta of zero, no "flat day". A guessed baseline
is indistinguishable from a measured one once it is on screen —
`NO_PLAN_LINE`'s doctrine, one field down.

### The catch-up read — a late day-close arrives labelled, not skipped

On the degrade tier (>24h late) this surface RENDERS, opening with
`pack["catchup"]["lines"]`: the span it covers, how late it is, and the slot it
missed. It used to post `late_fire`'s `degrade_notice` alone, so a skipped
Tuesday was never scored — not that evening, not ever. **Do not post
`degrade_notice` here**: it says "Skipped the full End of Day", which is true on
every other scheduled surface and false on this one the moment it renders, and
it is a shared constant that is deliberately not being reworded. One read covers
the whole span; never one surface per missed day. And `skip_render` is
untouched — a slot already delivered posts its `ack` and stops. Late is not
duplicate.

### `score.first_move` — render the STATUS, not just the line

`first_move` is `{text, status, checked_against, matched}`, and the status is
the whole point of the field. `open` — this morning's suggested first move is
still live; render it as this morning's plan. `stale` — something that closed
today NAMES that line (`matched` says which), so render it as CONTEXT about
this morning's plan and **never as an instruction**: *"This morning's first
move was X; it closed at 12:30"* is right, *"Your first move is X"* is the
defect this replaces. `unverifiable` — nothing was on file to check it
against, so say that plainly in the same breath or leave the line out; never
promote it to a live instruction. `first_move: null` means the brief printed
none: render nothing, and never compose one.

This rule is here and not only in the fire's own text because the manual
family renders the same pack. The failure it closes was field-observed: two
live packs on 2026-08-17 carried the same already-discharged line, byte for
byte, at 9 PM, as though it were the next thing to do.

### `wins.title_source` and `wins.more_line`

Each row says where its name came from. `snapshot` / `joined` — render the
title. `generic` — the row's `title` IS the whole honest sentence (*"a
commitment was closed"*), so render it as is and never dress it up with a
name, an id, or a guess at which one it was.

`more_line`, when non-empty, prints VERBATIM as the block's last line. The
block shows at most six rows and a live day can put five times that through
it, so the pointer is what keeps the cap a render bound instead of a silence —
the same rule the morning brief's needs-attention lane keeps. Never top the
block up to close the gap, and never report `n_total` as if it were the number
of rows on screen.

**`slipped` and `confirm` carry the same line, from the same helper
(`end_of_day.more_line`), and every one of them STATES ITS DENOMINATOR** —
"shows 3 of 41", not "shows at most 3". A cap without a denominator is not a
summary; it is a claim about size, and it was a wrong one: `slipped` bound 3 of
41 and `confirm` 5 of 67 in silence. One helper and one shape, so no block
acquires its own dialect of "there is more than this".

**An overdue item asks once, then it rests (SPEC OVERDUE1, M's ruling R-3:
"I would do it for 3-4 days").** Three items due Aug 6-8 rendered identically
in this block every night for two weeks. Past a few nights, repetition stops
being a reminder. So an item **3 or more days past its due date** (the knob is
`overdue_ask_after_days` on this skill's config; default 3) is pinned to the
TOP of the block once, with a direct question — *"{title} — 8 days overdue.
Done, new date, or drop?"* — carrying the block's existing verbs. From the
next fire it is **suppressed from this block only** until it is answered, and
one trailing line says how many are resting and where to find them.

Resting is not disappearing: the item stays on the open book, stays on
`my plate`, stays in the morning brief's needs-attention lane, and stays
inside this block's own `n_total`. **Answered** means any real movement —
`mark done`, `drop`, `push to [date]`, or a re-word / re-owner / re-date. A
new due date re-arms the clock from that date, with no second write: the mark
records WHICH deadline it asked about, so it stops matching the moment the
deadline moves. The mark itself is `commitment_state.mark_asked`, modelled on
`watch_gate.park_in_watch`, and it is deliberately **not movement** — asking
about a quiet item must not make it read as freshly touched.

Two consequences worth stating plainly, because both are places the rule
could have gone quiet and does not. **The sign-off still counts a resting
item** — it stopped being repeated, not late — so "3 items are still overdue"
stays true on a night the block shows one of them. And **being asked is not
being touched**: the mark is exempt from the un-confirm bar, so if you confirm
an old overdue item and the evening chat asks about it that night, `undo
confirm` still works the next morning. A watch park is a decision and still
blocks that undo; a question the system asked is not.

This is NOT `cap_needs_attention`'s rotation rule and must never be described
as one. That rule exists so nothing is suppressed FOREVER; this one exists
because something is shown EVERY NIGHT. Opposite problems, separate code.

`window_source` says which window these rows were read from. `morning_anchor`
— the day's morning brief fired and the window opens there. `day_floor` — no
morning brief fired, so the window opens at workspace-LOCAL midnight of this
fire's own day and never earlier. **A day with no morning brief still renders
its wins**; it counts them from midnight, and `more_line` is already spelled
for that window (*"…more moved since midnight"* rather than *"…more moved
today"*). Print it as given and never re-word it to claim "today" over a
floored window. This rule is here and not only in the fire's own text because
the manual family renders the same pack.

**Monday** adds the prior week's day-scores after the day-close blocks, and the
weekly development read SLOT, which renders NOTHING until DEVREAD1 ships.
**Friday** is a plain day-close with no hand-off line: the Friday chat must not
compete with `weekly-recap`.

The roll-up has **three row shapes** and each renders what its own row says:

| Shape | Renders |
|---|---|
| The chat never ran that day (`recorded: False`) | "no close was recorded" |
| The chat ran but predates the score (`counted: False`) | "the day ran before this count existed" |
| Both (`counted: True`) | the numbers |

**The middle shape is the one that matters at ship.** This fire serves a taskId
that has existed for many releases, so on the first Monday after the upgrade
every day of the prior week is a pre-EOD1 receipt: a real fire, with no score on
it. It renders its own line. Never a zero, never a dash, never a blank row, and
never a week summarized as slow — none of those is a thing the record says.
Zero is a measurement; these two are not.

### Why the wording is load-bearing

"Not recorded" and "not done" are different claims and only one of them is
supportable. A missing close event says nothing wrote the close down; it does
not say the work did not happen. Say the second and the CEO argues with the
surface, and a surface the CEO argues with stops getting read. The phrasing
lives in `end_of_day.NOT_RECORDED` / `NOT_RECORDED_LINE`, so there is one place
to read it and one place to change it.

## Capture: the admission gates, and the flip that is OFF

The capture leg's doctrine is CAPTUREFLOW's, unchanged: every extracted
commitment goes through `meeting_capture.route_meeting_captures`, and a
below-floor capture routes to the REVIEW tier. It is never silently dropped and
never deleted — a wrong floor call costs one tap in the queue rather than a
lost promise.

**Decision 1's routing flip ships DARK, and this is the paragraph that says so
out loud.** `shared/scripts/held_tier.py` carries a config-gated branch that
would route the weakest of those rows to a HELD tier — out of sight: not
queued, not badged, not counted, retrievable on request. The knob is
`weak_capture_routing` on this skill's config and its default is `review`.

**Turning it on is not this workspace's decision, and the fence is
`shared/scripts/operator_capability.py`.** `held_tier.enable_held_routing`
calls `held_tier.capability_status(...)`, which asks
`operator_capability.capability_status("held_tier_routing")`, and REFUSES while
that capability has not been granted. Nothing is written on a refusal, not even
a pending marker.

The grant lives in the plugin payload
(`shared/config/operator_capabilities.json`), so it arrives with a release and
by no other route. This is deliberate: the flip turns on when the people who
build Command Room are satisfied the disposition is safe. They reach that on
their own book — nobody reads this workspace's captures to decide it. It is
**not** a measurement a workspace takes of itself. It used to be, and that
was wrong in a way worth stating once — the thing that produced
the measurement never shipped, so the check was red in every workspace forever
and read as an accusation about that owner's accuracy when it meant that the
instrument had never been delivered.

Three things follow and none of them is optional:

- **Never enable it from inside a fire.** Enabling is a deliberate action taken
  once the disposition has been judged safe. A fire that flips it has decided
  the thing that judgement exists to decide.
- **Never route around the refusal** by writing the config value directly. The
  value alone does not enable anything: `held_tier.flip_status` re-asks the
  capability on every fire, so a stored request is a request and never a grant.
- **Never tell an owner their own numbers are the reason.** The refusal
  sentence is pinned once, as `held_tier.REFUSAL_LINE`, and it says the flip is
  not a setting in this workspace and that nothing about their numbers is
  holding it back. Both halves are true and the second one is the point.

When the flip is off — which is every workspace today — `apply_held_routing`
hands the routing back unchanged and the held lane is empty. Off is
byte-identical to no flip at all, and that is asserted, not assumed.

## Mechanics

- **Personification.** Read `shared/PERSONIFICATION.md` and call
  `personification.get_brain_name(workspace_root)`. The evening intro is
  `"Evening, {first_name} — {brain_name} closing out your day."` — the ONLY
  greeting. One name in the intro, one in the sign-off; nowhere else.
- **Receipt BEFORE post (BRIEFFIX1 Item C).** `end_of_day.log_end_of_day_receipt`
  is the ONE receipt writer for this fire and it runs before the surface posts.
  It carries `confirm_ids` (the numbered map, in the order the surface numbers
  them) and the day-intent proposal, so a later tap resolves positionally. A
  fire that posts a numbered surface with no recorded numbering makes every
  one-tap action on it ambiguous, and the resolver refuses those rather than
  guessing.
- **Writer wall.** Personal-account items may render; they write nothing. No
  entity write, no commitment write, no capture from a personal-account read.
- **Every close carries its pointer.** Each close this fire writes goes through
  `commitment_state.close_commitment(..., source_ref=<the sent message's
  artifact key / the chat pointer / the meeting id>)`. A close with nothing to
  point at still lands — since SPEC PROVMINT1 the writer mints
  `session:<source_skill>:<now>` for it and marks the ref `surface_minted`, so
  it points at the ACT rather than at nothing, and PROV1's bare marker is no
  longer reachable from this writer. The floor is not a licence to drop a
  pointer this fire HAD: a minted ref counts as its own share of the coverage
  split rather than inside the artifact-backed number, and
  `end_of_day.unsourced_closes` still reports it as a close citing no artifact.
- **Widgets.** Any row-list — slipped, confirm, tomorrow — is produced by
  `widget_transport.render_and_persist` and its `html` passed to
  `show_widget` byte-exact. Never hand-composed, never restyled.
- **Timezone.** Every rendered timestamp goes through
  `shared/scripts/tz.py::to_local(value, workspace_path=<WORKSPACE>)`. The
  fire's own date comes from `end_of_day.workspace_today`, which resolves
  workspace-local: at 9 PM Pacific the UTC calendar has already rolled over,
  and this fire runs in that window.
- **First-run (FRP1).** Three decisions, rendered once ever as the footer:
  tone (scoreboard or journal), the slipped section on or off, the sign-off on
  or off. Defaults live in `held_tier.CONFIG_DEFAULTS`.

## Connectors — ask per capability, skip and receipt what is absent

This build reaches for three capabilities and no more: **email**, **calendar**,
**chat**. Each leg asks for its OWN capability through the seam
(`tool_discovery.discover_for_category(...)` with
`connector_config.declared_backend(...)`), and a capability that is not present
is SKIPPED and recorded on the fire's receipt under `connector_gaps` — never
silently, and never as an empty section that reads as "nothing there".

The asks are written per capability from day one precisely so CONN1/CONN2 can
add Drive and DocuSign later by adding rows, not by redesigning the fire.

## One-tap verbs

Every verb below resolves through `end_of_day.resolve_choice(workspace_root,
n, action=...)` against the fire's own receipt. A stale or missing map is
refused in plain English — never clamped, never guessed.

| Verb | Block | What it does |
|---|---|---|
| `mark done [n]` | slipped, confirm | Closes through `commitment_state.close_commitment` WITH `source_ref` |
| `push to [date]` | slipped | `commitment_state.apply_later` — moves the due date on the owner's own item (`commitment_updated` carrying `new_due`), or dates a mute when the item is owed to them |
| `draft [n]` | slipped | Hands off to `email-writer` per the EW1 delegation rules |
| `drop [n]` | slipped, confirm | The existing drop path, unchanged |
| `confirm [n]` | confirm | The existing confirm path, unchanged |
| tomorrow confirm | tomorrow | `end_of_day.resolve_intent_confirm`, then `day_intent.write_day_intent(..., origin="wrap")` |
| `add person [n]` | person_candidate | `person_candidates.resolve_candidate(action="add person")` — creates the contact with every observed spelling as an alias, then drains every capture that was blocked on the name |
| `same as [existing] [n]` | person_candidate | Same call, `action="same as [existing]"` — the alias write, then the same drain |
| `not a person [n]` | person_candidate | Same call, `action="not a person"` — suppresses the PROPOSAL for that name, permanently and per org. Never a capture |

The three candidate verbs are legal on the `person_candidate` block and
nowhere else, and `mark done` / `drop` are refused ON it: there is nothing to
close on a name. Those rows are DERIVED (`person_candidates.derive_candidates`
reads the pending queue live), so `resolve_choice` hands back the row's own
`data` payload with them — a candidate has no substrate id to look up. The
fire's receipt carries `person_candidate_counts` (counts only, never a name),
which is what makes "did the waiting pile shrink?" answerable.

A tomorrow-block confirm writes `origin="wrap"` because the CEO tapped. The
pre-confirm draft is `origin="proposed"`, exists transiently, and is never
rendered as fact — `day_intent.load_day_intent` skips proposed rows by default,
so a surface cannot render a guess as the CEO's word by forgetting a flag.

## Routing (full trigger corpus)

The complete trigger family and its fences. The description carries the stems
the runtime router reads; this section is the declared family and is binding at
fire time.

**Fires on:** 'end of day', 'close out my day', 'daily wrap', 'wrap my day',
'close my day', 'end my day', 'close out the day', plus the scheduled evening
chat and its Run Now button.

(Trigger phrases in this section are single-quoted deliberately: that is the
house convention the mechanical matcher reads. Everything in the fence list
below is BACKTICKED for the opposite reason — a quoted phrase outside a
negative clause reads as a claim on it, and this skill claims none of them.)

**DOES NOT fire on:** 'add end of day' — that is a REGISTRATION ask, not a
day-close (SPEC EOD2: it is the rename switch, and the exact phrase the
retirement line hands a customer still running `past-meetings`). It routes to
`change-schedule` → registration Phase 6, which registers `end-of-day` and
disables the predecessor in one step. This one is single-quoted deliberately,
unlike the backticked list below: it sits inside the negative clause the
mechanical matcher actually scans, so quoting it here is what makes the fence
real rather than decorative. The bare `end of day` still fires this skill — a
phrase only becomes the registration ask when the `add` verb is on it.

Also DOES NOT fire on:

- `morning briefing` / `brief me` / `start my day` — that is `morning-briefing`,
  the other bookend. The two share a receipt vocabulary and nothing else.
- `weekly recap` / `what happened this week` / `friday wrap` — that is
  `weekly-recap`. The Friday End of Day is deliberately a plain day-close with
  no hand-off line so the two never compete for the same moment.
- `end session` — that is `workspace-manager` wrapping a working session, which
  is a different thing from closing a calendar day.
- `process the call` / `meeting notes` / `process the meeting` — that is
  `meeting-notes`, one meeting at a time. The End of Day fire processes the
  day's meetings as its final leg; a single named call is still that skill.
- `triage my inbox` — that is `inbox-triage`.
- `tomorrow is about [X]` / `what's tomorrow about` — that is
  `workspace-manager`'s day-intent handler (BK1). The End of Day fire OFFERS a
  draft for tomorrow; a sentence stating the day's intent outright is the
  manual path and stays there.

## See also

- `shared/scripts/end_of_day.py` — the blocks, the words, the receipt, the resolver
- `shared/scripts/held_tier.py` — the dark flip
- `shared/scripts/operator_capability.py` — the operator grant the flip is fenced on
- `shared/scripts/day_intent.py` — the tomorrow record (SPEC BK1)
- `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md` — the scheduled fire
