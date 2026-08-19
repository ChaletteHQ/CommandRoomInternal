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

## The seven blocks

`surface_drivers.build_end_of_day_pack(workspace_root, mode=...)` builds all of
them in ONE call (the t3 FB-9 pattern). Render order is the contract:

| Block | What it is | The rule that governs it |
|---|---|---|
| `alarm_lines` | `substrate_health.substrate_alarm_lines` | Verbatim, pinned top, never suppressed |
| `score` | This morning's plan against today's closures, plus the saved digest read back | **No morning receipt → "No plan on record this morning." NEVER a guessed score.** An item with no close on file is **"Not recorded", never "not done"**. `first_move` is an ANNOTATION, not a string — see below |
| `wins` | What moved since the morning fire, by NAME | Zero wins → one honest line. Never padding. `title_source` and `more_line` govern the rows — see below |
| `slipped` | The ball-is-on-you rows | Max 3, verbs on each, and every row comes from the GATED needs-attention set — never a fresh scan |
| `confirm` | `confirm_flow.select_confirm_items`, relocated here | Cap 5, stakes-then-age. A held capture can never enter it |
| `tomorrow` | The wide calendar look, the rollover, the day-intent draft | The draft is a PROPOSAL until tapped. It is written only on confirm |
| `sign_off` | Computed, never composed | Zero urgent → "Nothing else needs you before tomorrow's brief." verbatim |

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

**Turning it on is gated on a measurement, and the gate is
`shared/scripts/precision_gate.py`.** `held_tier.enable_held_routing` calls
`precision_gate.precision_gate_status(workspace_root)` and REFUSES while that
status is not passing — the bar is M's ruling of 2026-08-16: at least 90% of
meeting-derived items routed to the open book are verified promises, on a
labelled sample of at least 100, holding for two consecutive dogfood weeks.
Nothing is written on a refusal, not even a pending marker.

Three things follow and none of them is optional:

- **Never enable it from inside a fire.** Enabling is an M action, taken once
  the gate holds. A fire that flips it has decided a thing the measurement was
  built to decide.
- **Never route around the refusal** by writing the config value directly. The
  value alone does not enable anything: `held_tier.flip_status` re-asks
  `precision_gate` on every fire, because a workspace can fall back out of a
  two-week bar it once met.
- **Never soften the bar to pass the bar.** The three constants in
  `precision_gate.py` are the ruling, not tuning knobs.

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
- `shared/scripts/precision_gate.py` — the bar the flip is gated on
- `shared/scripts/day_intent.py` — the tomorrow record (SPEC BK1)
- `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md` — the scheduled fire
