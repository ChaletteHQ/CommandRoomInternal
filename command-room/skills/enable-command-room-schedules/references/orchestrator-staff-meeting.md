# Orchestrator prompt — Staff Meeting

This file is the EXACT prompt the bootloader cats and executes for `taskId: staff-meeting`. Fires 9:00 AM Monday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 9 * * 1`) — the Sunday-evening maintenance jobs (deal-signals among them) have just refilled the Living Brain queue, and Friday already carries wrap (13:00) + triage (15:00). NEW in LB1 (R3). **NOT a first-install task** — propose-only later-add posture: it registers via `change-schedule` / Phase 6 `add` / the update-bridge proposal, never silently, never on a fresh workspace. Weekly by default; cadence is tunable via `change-schedule` like any chat (a second weekly slot is a user choice, never a default — cleanup's card-health line is the evidence for whether the queue earns it).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. The Staff Meeting is a **full widget action surface** built from the shipped primitives (canonical per-item verbs per `shared/CHAT_ACTION_WIDGET.md` § Living Brain card; email verbs on outreach items; batch Apply-all footer + undo; per-item context notes; pagination as design): post via `widget_transport.render_and_persist` — the full validator chain (canonical verbs, leak scan, `validate_rendered_widget`) runs inside the one call — then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15). Each page relays as widget_code — **size can never force improvisation**; pagination below is design, not a fallback.

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section". The narration lines + widget are the ENTIRE chat turn.

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for the Staff Meeting surface owned by `skills/system-health/SKILL.md` (§ "The Staff Meeting surface") — the same surface its on-demand `staff meeting` / `run our staff meeting` triggers fire. The queue projector is `shared/scripts/brain_proposals.py`, the narration reader is `shared/scripts/change_feed.py`, and resolutions dispatch through apply-choices (`src: "cr-brain"`). This orchestrator's job: (a) resolve paths, (b) execute that surface's steps through THIS file's door forms (Phase 5's `run_writer` form is the surface's driver), never the skill's typed-path command, (c) post ONE widget via the driver call that also writes the receipt (FB-7), (d) verify the receipt, (e) STOP. The skill's section is the surface's reference, not a script for this fire: on a seat whose shell holds no workspace, a command run in this session draws an empty card and receipts it (ORCH2 fix pass 1).

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The narration + widget IS the chat turn. After it posts (plus the post-widget Links section), YOU STOP.** No exceptions. Applies to first fires AND re-runs.

**Forbidden — zero tolerance:**

1. **No writing the rendered widget to disk by hand** — the transport's own persist into `_hq/.system/widgets/` (performed by `render_and_persist` itself, per `shared/STOP_CONTRACT.md` rule 1) is the only sanctioned widget write.
2. **No narrating what's in the widget rows.** The user can see the queue. The two feed lines ("what I did on my own" / "what's waiting on you") are the ONLY narration.
3. **No post-widget summary block.** The turn ends after the widget + Links section.
4. **No resolving anything yourself.** Every resolution is a user click dispatched through apply-choices → the item's own writer. This surface proposes and narrates; it never adjudicates.
5. **No auto-send on outreach items.** `send` is always a user click dispatched through apply-choices → email-writer.
6. **No re-deciding inclusion.** The projector (`load_open_proposals`) owns who qualifies; render what it returns, verbatim `render_line`s included (Bug #92b).
7. **No invented bulk verbs (FS-10, D10).** The ONLY verbs on a queue row are the registered per-row verbs the proposal carries (`confirm proposal` / `dismiss proposal` / `snooze proposal 7d` for brain rows; each legacy family's own shipped verbs). A widget-level "Confirm-close all," "Review the list," "Dismiss all," or any other bulk affordance you compose is FORBIDDEN — the standard Apply-all footer is the ONLY batch control, and it fires the rows the user selected. If you feel the queue needs a bulk action, it doesn't; that impulse is the FS-10 improvisation.
8. **No opaque clustering — the HAND-clustering ban, unchanged.** The queue renders as the `build_card_view` sections (MONEY / IDENTITY / HYGIENE with honest counts), each row `{name — badge · evidence-with-date · consequence}`. Never collapse the queue into hand-labeled buckets of your own, and never replace it with a summary of "N deal signals, M cleanup items."

   **This ban is about YOUR improvisation, not about grouping as such.** Since STAFFCUT (2026-08-02) the driver itself emits EVIDENCE-CLASS DIGEST rows — one row standing for every item that rests on the same evidence — and those are not the thing this rule forbids. The difference is total: a digest is built by `proposal_digests.group_into_digests` inside the driver, it states its own honest member count, it carries every member's id verbatim in `data.digest_members`, and each member is still adjudicated by its own handler through the same fence. A hand-labeled bucket is a sentence you wrote that nobody can click. Render the driver's digests exactly as it returns them (`render_line` verbatim, Bug #92b) and compose none of your own.

**Self-check before posting anything after the widget:** "is this required by spec?" If no → don't post it.

---

You are firing the Command Room "Staff Meeting" chat — the Living Brain's weekly review. Today is Monday in workspace LOCAL time. You're showing the CEO everything the brain did on its own in the window Phase 3 resolves and names, and everything waiting on their eyes (the COMPLETE queue — this surface is deliberately exempt from the daily card's cross-surface dedup, R2), all resolvable in one sitting. Relationship moves are NOT here: ruling 6 of 2026-09-03 put "this week's moves" on the Friday wrap — and M closed that on 2026-09-17: the wrap gets no moves section either, and the capability answers ON DEMAND through `skills/relationship-moves/SKILL.md`. Do not read this line as saying some other surface carries one; see Phase 4.

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — by cron or the manual `staff meeting` / `run our staff meeting` trigger (which routes through system-health to this same surface). A `pack_run` receipt writes on every fire — INSIDE the Phase 5 driver call (`--fired-via`, FB-7); only the degrade branch writes it in Phase 6. Re-fires are safe: the queue projector is tombstone-aware, and the driver never double-receipts the same fire re-rendering (WRAPSTAFF1 4.6) or a non-manual re-run inside the guard.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Continue with:

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read `<WORKSPACE>/_hq/data/entities.json` — primary user, people, emails, relationship tiers.

# Phase 2.9 — Run mode + lateness check (v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt; `manual` when a human caused the fire. **When uncertain, it is `manual`** (F-47 P1a).

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"env_date": "<session date>", "fired_via": "<scheduled|manual>", "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:lateness"}'
```

The helper calls `check_lateness('<workspace_root>', 'staff-meeting', fired_via='<scheduled|manual>', env_date='<session date>', emit=True)`
exactly as the old block did, beside the data. The answer is the verdict this file already reads — its
own `tier`, `banner`, `degrade_notice`, `directive`, `ack`, `clock`,
`receipt_fired_via`, `rerun_of` — plus `pending_rows`: every row the check
writes on the way (the clock record, the `late_fire` telemetry on the note and
degrade tiers, and behind a `skip_render` directive the honest `skipped`
receipt) comes back instead of being written. **Append `pending_rows` FIRST,
before anything is posted — every element, in order, nothing dropped:**

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "staff-meeting", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the lateness answer above, in order>]}'
```

When `pending_rows` is empty there is nothing to append; never compose a row of your own.

**The layer carries the workspace, the clock and the environment for you (CLOCK1).** Every helper process the verbs start gets `CR_WORKSPACE`, `TZ` (the workspace's own timezone), `CR_ENV`, `CR_HOST_MODE` and the forwarded writer identity, so a helper can never be left guessing which workspace it is in, cannot fail to cross-check the clock, and never stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


**Read `directive` BEFORE the tier — it is the render decision (SPEC SCHED1).** If `directive` is `skip_render`, the slot this fire is serving was ALREADY delivered: a receipt for it is on the ledger, and the helper has already written the honest `skipped` receipt for this fire. Post the returned `ack` line, exactly as returned, as the ENTIRE output of this fire — no surface, no widget, no sections, no Sources block, and no receipt of your own — then STOP. Do not re-derive whether it "really" ran, do not render a shortened version, and do not read the tier as the decision: on this path the tier is `none`, `none` means "run normally", and that reading is what delivered three duplicate full surfaces in one day. `directive` is present on every tier and is `null` on all the others, so this is one unconditional check rather than a special case to remember. A `manual` fire never carries it — a human who asks for the surface gets the surface.

**And `tier: "rerun"` is the OPPOSITE instruction — it RENDERS (SPEC RUNNOW1).** `skip_render` is bounded: the helper returns it only within two hours of the receipt that served the slot, which is the duplicate the skip exists to catch — a catch-up and a scheduled fire landing the same edition minutes apart. A fire that calls itself `scheduled` and arrives LATER for a served slot comes back with `directive: null`, `tier: "rerun"` and an `ack`. Post that `ack` as the OPENING line of this fire's output, then render this surface IN FULL, exactly as on any other tier. Past two hours the fire is a person pressing Run Now, and no run mode a fire reports about itself can tell you otherwise — the standing ruling is that a person who asks gets what they asked for. **Carry the returned `rerun_of` onto this fire's receipt, and carry it at the ONE place this file writes its `pack_run` receipt — the receipt call whose own `extra_data` block already spells the key out for you.** The writer differs by surface, so take the one THIS file names and no other: never add a second receipt call to carry the field, and never hand-roll a receipt JSON. **This is load-bearing, not bookkeeping.** A receipt carrying `rerun_of` is excluded from the served-slot marker, which is what lets the person press again in five minutes and get the surface again; a re-run receipt written WITHOUT it reads as an ordinary scheduled delivery and re-arms the skip against the next press for two hours — the refusal this build exists to remove, arriving by a different door. Post no lateness banner and no `degrade_notice` on this tier: the slot WAS delivered, so nothing was missed and nothing is stale-by-omission.

Branch on `tier` exactly as every scheduled chat does:

- **`manual` / `none` / `exempt` / `unknown`** — run every phase normally, no timing narrative anywhere. A `suppressed` reason means the ledger found the slot served — believe it.
- **`note` (3–24h late)** — run ALL phases normally; the chat output OPENS with the returned `banner` line verbatim.
- **`degrade` (>24h late)** — do NOT render the surface. Execute every substrate write the fire owes (the expiry-safe projector reads write nothing; the receipt still writes — Phase 6's degrade branch, since the driver never runs), post ONLY the returned `degrade_notice` line, and STOP. The queue keeps; the next fire renders it.

Carry the returned `receipt_fired_via` into the Phase 6 receipt — never guess it independently.

# Phase 3 — Load the two halves (read-only)

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<ISO of now>", "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:staff_window"}'
```

The answer is `{window, feed, n_open, queue}`. WRAPSTAFF1 4.4 — the window is
ONE read, in code: the marker AND the words for it come from
`surface_drivers.staff_meeting_window(ws, now_iso=<ISO of now>)`; never derive
either here. `feed` is `change_feed.changes_since(ws, window['since_ts'])` and
`queue` is `rank_proposals(load_open_proposals(ws, 'staff-meeting'))` — both
read where the data is.

**SAY THE WINDOW YOU USED (WRAPSTAFF1 4.4).** Print `window['label']` verbatim as the block's window sentence — it is one of exactly two, already filled in: *"since your last staff meeting on Sep 8"* or *"over the last seven days"*. Never both, never neither, and never a sentence of your own. On 2026-09-15 this surface named the last meeting in its window sentence while the counts under it came from a seven-day read — it reported 17 / 12 / 42 where the ledger held 6 / 2 / 5 since Monday's fire, and 42 was exactly the seven-day figure (ATTENDED_TEST_v5.31.0 B2.7). The label described one window and the numbers came from the other. Every count you render under that label comes from the `changes_since` call above and from no other read.

- **"What I did on my own"** — the feed's lines, rendered verbatim (each is already plain English with its undo affordance where one applies). Cap 3 lines; drop-empty.
- **Unnamed-speaker count line (PID1 §0-4 — ONE prose line, this surface only).** `n` is `identity_reconcile.count_open_annotations(ws)` — a separate read-only call, never folded into the queue load above, asked where the workspace is:

  ```bash
  python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "staff_meeting_helpers:unnamed_speakers"}'
  ```

  The answer is `{n}`. When `n > 0`, render ONE line after the feed lines: *"N unnamed speakers pending identification — resolving against calendars."* When `n == 0`, render nothing (drop-empty). This is the annotations' ONLY render anywhere — never a queue row, never a verb, never mentioned in the brief.
- **"What's waiting on you"** — the ranked queue. `surface="staff-meeting"` sees the full PROJECTION (no daily-dedup filter), and the driver then bounds what ONE FIRE RENDERS to about two screens. Zero items → the section says so honestly and the widget renders the `all_clear_summary` data view.

  **The queue is BOUNDED per fire since STAFFCUT (2026-08-02) — this replaces the old "no cap" contract.** The 2026-08-02 load audit measured one fire at 105 rows over 7 screens, with three kinds accounting for 93 of the 101 queue rows, so "render everything, every week" had stopped being honesty and become the reason the page went unread. Two driver-side passes now sit between the projector and the builder, and **you do not perform either of them** — `surface_drivers.build_staff_meeting_view` does, inside the one Phase-5 call:

  - **Evidence-class digests** (`proposal_digests.group_into_digests`): sent-match review rows group by their EVIDENCE (16 distinct lines carried 54 rows on the audit day, 34 of them on one line), name-only identity mentions group into one "N new names from your calls" row, and unadjudicated fact rows group into one. A digest changes PRESENTATION only: every member keeps its own id (`data.digest_members`), its own verbs and its own resolution path, a grouped confirm rides the same shared bulk-accept fence, and nothing auto-closes.
  - **A page bound** (`proposal_digests.bound_page`, default `STAFF_PAGE_ROW_CAP` = 21 rows for the whole page, appended sections included): the meeting fold's shipped volume guard applied to the queue lane. Budget is split across the shapes present so no lane is starved, the ranked FRONT of each lane is what shows, and the honest full totals plus a pointer ride the section titles (`IDENTITY (8) — 25 items grouped into 8 rows · showing the front 8 of 24 — the rest stay queued and lead the next one`). It bounds the PAGE-SET, never the projector: nothing is resolved, nothing is hidden, and answering the front is what advances the rotation.

  **CORRECTED 2026-09-15 (REVIEW_NIGHT11C M-10): THE HEADER CARRIES NO COUNT AT ALL.** This line used to say the header count equals the rows the widget shows. Since FOLD1-B (ruling R-4, M's design rule of 2026-09-06) `brain_proposals.surface_card_header` returns the words **"Staff Meeting"** and nothing else, and `assert_no_debt_header` RAISES on any digit in it — a customer opening a surface to answer two questions is not met with a number that reads as an accusation. Neither number is lost: the tiles carry the honest per-shape totals and the section titles carry what the page bound held back. The full arithmetic lives there for exactly that reason. Read them; do not recompute them, and never write a count into the header.

  **THE TILES EQUAL THE BODY (WRAPSTAFF1 4.5, M's ruling R-19b, 2026-09-17 — this REVERSES LIFECYCLE1 §7b).** §7b put the honest per-shape total of the whole open queue on the tiles, on the reasoning that two counting conventions on one widget is worse than either. The 09-15 card then tiled 28 / 7 over a body of 27 / 6 (ATTENDED_TEST_v5.31.0 B2.7) and M ruled the other way: a number on a card is something the reader should be able to count on screen. The tiles now count the rows the card RENDERS for that shape, and the honest full totals stay where they already mean something — on the section titles, which `proposal_digests.section_notes` writes (`IDENTITY (8) — showing the front 8 of 24 — the rest stay queued and lead the next one`). The driver still supplies `shape_totals`; the tiles no longer read it. Do not recompute either number.

  **Two kinds no longer appear here at all**, and their absence is the ruling, not a bug: **dormancy** rows are ON-DEMAND (M ruling 2026-08-02 — whether a quiet project is dormant is a judgment the CEO makes when he asks; the asking surface is `stalled projects`, and `load_open_proposals(ws, "on-demand")` is the read), and **schedule_add** rows are RETIRED at the projector (`brain_proposals.RETIRED_KINDS`) with the writer stopped too (LIFECYCLE1 §7a — STAFFCUT retired the legacy adapter, but the migrated bp-rail writer kept minting rows and one of them rendered here the next morning; 0 of 4 ever produced a registration, which only ever happens through the change-schedule path). Do not add either back "for completeness."

  **⛔ THIS CARD IS NOW THE ONLY DOOR (FB-19 / FB-20 — M's ruling 2026-07-16).** The morning brief went read-only: it names deal signals in prose and points here, and nothing else in the system asks the user to confirm anything. Every item's ONLY path to adjudication runs through this card, which raises the bar for what may occupy a row:

  - **Every row states its ask and carries verbs, or it does not render.** A row that names no subject, asks no question, or offers no way to answer is not a row — it is a shrug, and it teaches the user to stop reading. This is enforced upstream in the projector (`brain_proposals._adapt_commitment_reviews` drops a review it cannot phrase an ask for), so an un-askable row never reaches the builder. Do NOT re-add one here, and do NOT invent a verb for a row that arrived without any (STOP rule 7).

    **This contract was being violated by the projector itself until STAFFCUT.** Three legacy adapters shipped `action_tuples: []` hardcoded — org/project, dormancy, schedule_add — so on the audit day six org rows and one dormancy row rendered with NO BUTTONS: permanently unanswerable, sitting on the surface that is the only door. `propose()` refuses empty tuples at source, so those fossil adapters were the only way to produce one. All three are now resolved in code: org/project rows carry `confirm [type]` / `not relevant` (the verbs apply-choices was already dispatching for those kinds), dormancy rows carry `active` / `archive` / `snooze 14d`, and schedule_add is retired. If you ever see a buttonless row again, that is a projector defect to report — never something to paper over with a verb of your own.
  - **Held items are absent, not greyed.** `load_open_proposals` already filters them (FB-19's `mute_ledger.hold_item` writes a 14d `chat_dismissal` with `reason: "held"`). A parked item re-appearing is the live 2026-07-16 defect — it reads as the system ignoring what the user said. Never re-surface one "for completeness."
  - **From your meetings (CAPTUREFLOW §C, 2026-08-01) — a SECTION, not an appointment.** The Phase-5 driver appends ONE extra section, `FROM YOUR MEETINGS (N items, K calls)`, built by `needs_review_queue.staff_meeting_group_section` — the SAME per-meeting grouping the on-demand `needs your call` queue renders, from the same builder, with the same `confirm` / `already done` / `drop` / `not mine` verbs answered through the same shared fence (`needs_review_queue.confirm_items` / `needs_review_queue.done_items` → `watch_gate.screen_bulk_accept`). The verb list is `needs_review_queue.QUEUE_ROW_ACTIONS`, read by both render sites — never re-typed per surface. **`already done` (DONE1, 2026-08-03)** is the answer for a promise the CEO already kept off-mail: it confirms the capture and closes it as `done` on the CEO's own attestation, so those items stop being counted as dismissals of that counterparty's captures the way a `drop` is. It is PER-ITEM ONLY — every id must be named on its own, so `all`, a range and a call phrase never reach it. One queue, two places to answer it; there is no second ledger and no new scheduled task. You do NOT build this section — the driver does. Its volume guard is built in: whole calls only (a split call asks half a question), oldest call first, at most 3 calls and 8 rows, honest full totals in the title, and a pointer to `needs your call` for the remainder. Oldest-first IS the rotation rule — the front of the queue is what shows, so no call can be suppressed forever. **What the rows are (2026-08-01):** the same three kinds the on-demand queue carries — an unsure extraction, a capture whose evidence was not found in its transcript (`data.fusion_unverified`), and a capture the admission floor gated (`data.floor_gated`, its `FLOOR_*` reason printed on the row). The third kind used to be dropped silently; M ruled it routes here instead, because the floor was measured wrong as often as right and a silent drop destroyed real promises. Render all three the same way — they are one question, *did we hear this right?* — and never filter a `floor_gated` row out of the section.
  - **People the graph keeps missing (SPEC PERSONLOOP1, 2026-08-19) — the second section, and the reason the first one is so long.** The Phase-5 driver appends ONE more extra section, `PEOPLE THE GRAPH KEEPS MISSING (N of M, K waiting captures)`, built by `person_candidates.candidate_section` — the SAME builder the on-demand `needs your call` queue and the End of Day read, with the SAME verbs (`add person` / `same as [existing]` / `not a person`) answered through the SAME writer (`person_candidates.resolve_candidate`). One question, three places to answer it; no second ledger, no new scheduled task, and you do NOT build the section — the driver does. A row is a name that has failed person-resolution across **at least two different calls**, carrying the count of captures currently blocked behind it: a name with no contact record cannot resolve, so every meeting that mentions it mints more waiting rows and none of them can ever drain. Capped at two rows per fire (`person_candidates.PROPOSAL_CAP`) and drop-empty like every other section. It PROPOSES and never creates — a person record is a durable graph write, and minting one off a transcript spelling is exactly the mistake this section exists to avoid — and `not a person` silences the proposal only, never a capture.
  - **Consequence floor — drop-empty, all the way up.** Sections tile only when they have rows; the builder drops empty shapes and refuses 0-value tiles. If NOTHING clears the bar — no money, no new identities, no actionable hygiene — then **render no queue card at all**: the `all_clear_summary` view. Never render an empty frame, a zero count, or a card whose only content is "nothing to review". An honest silence beats a card that wasted the trip.

# Phase 4 — vacant

**"This week's moves" left this surface on 2026-09-17 (WRAPSTAFF1 4.7).** Ruling 6 of 2026-09-03 puts it on the **Friday wrap**, not here, and it was still rendering here.

**The wrap does not carry a moves section, and it is not getting one. M ruled on 2026-09-17 that "this week's moves" adds NOTHING to the wrap**, and that the on-demand skill is what ruling 6's "on demand" means. The machinery is untouched and answers through `skills/relationship-moves/SKILL.md` ("who should I reach out to"), which owns the MUST-run live-contact check and the 7-day exclusion window. The reason, recorded once: a moves row carries three verbs, and the wrap is one of the surfaces the three-surfaces rule keeps question-free. **Do not add a moves section to the wrap, and do not read anything here as saying the wrap has one.** Ruling 6 is closed by this pair — off this surface, on demand — not half-built.

This phase is left vacant rather than renumbered — the pattern the onboarding skill already uses for its own vacated Phase 4 — so every later phase number in this file, in the receipts and in the reviews still points at the same step. **Do not compute, render or emit relationship moves on this fire.**

# Phase 5 — Render + post ONE widget (ONE driver call — T2.2)

**The entire build → fit → persist pipeline is ONE call through the WRITE
door** — the driver runs the Phase-3 projector (`load_open_proposals` +
`rank_proposals`) and the canonical builder (`build_card_view`) internally,
then `widget_transport.render_and_persist` (all validators + byte-fit + audit
persist), all where the workspace is. Do NOT hand-assemble sections or rows
(FS-10 mechanization), and do NOT re-run the Phase-3 loads separately for the
render — the driver reads them itself. WRAPSTAFF1 4.7 — no moves rows: Phase 4
is vacant and this surface renders no moves section.

It is a WRITER — on page 1 it runs the watch-expiry pass, freezes the
page-set, persists the audit page and writes the receipt — so it runs through
`run_writer`, which names who is writing before it runs, and NEVER in this
session's own shell: run in a session that holds no workspace, the old
`surface_drivers.py staff-meeting` command drew an EMPTY card there — a false
all-clear (ORCH2 fix pass 1). `staff_meeting_helpers:run_staff_meeting_surface`
is `surface_drivers.run_surface("staff-meeting", …)`, the function
`surface_drivers.py staff-meeting --workspace … --page … --fired-via …` has
always called; `page` is `--page`, `rerun_of` is `--rerun-of`, `fired_via` is
`--fired-via`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<the Phase 2.9 receipt_fired_via>", "page": <N, 1 on the fire>, "rerun_of": "<lateness rerun_of on a `rerun` tier; OMIT the key on every other tier>", "tools": [<this session's tool list, {name} each>], "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:run_staff_meeting_surface"}'
```

**`rerun_of` (`--rerun-of`) is SPEC RERUNFAN1, and it is the other half of the re-run contract above.** On a normal fire this surface's `pack_run` receipt is written INSIDE this call (`fired_via`, `--fired-via`, FB-7), so on that path the key — not any receipt call written out in this file — is how `rerun_of` reaches the ledger. Pass `lateness["rerun_of"]` verbatim on a `rerun` tier and OMIT THE KEY entirely on every other tier; the driver merges it into the receipt's `extra_data` on the page-1 write and ignores it on pages 2+. A re-run receipt written without it reads as an ordinary scheduled delivery and re-arms the two-hour skip, so the next press is refused.

**`fired_via` (`--fired-via`) is MANDATORY on the page-1 call — it is the receipt (FB-7).**
The driver appends the canonical `pack_run` receipt itself (via
`receipts.log_receipt`, the one chokepoint the manual `staff meeting` fire
also goes through) inside this same call, so the render and the
receipt can never be separated again — the 2026-07 live miss was exactly the
receipt living in a prose step after the widget post, where the STOP contract
ends the turn. A non-manual re-run within 15 minutes never double-receipts
(the RV-3 guard).

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

The answer is the transport — the same three things the command line printed
as `CR-PAGINATION: {...}`, the bytes between `CR-WIDGET-HTML-BEGIN` /
`CR-WIDGET-HTML-END`, and `CR-RECEIPT: {...}`: `pagination` (position metadata
for the `show more` narration), `html` (the persisted page's validated bytes),
`receipt` (`"status": "written"`, or `"deduped_refire"` on an RV-3 re-run),
`maintenance_line` when there is one, and `page_rel`, the audit page named
workspace-relative. A `{refused: "mount_stale", lines}` answer means the
folder has not finished syncing: post `lines` as the whole turn and stop —
nothing was rendered and nothing was written. **Relay `html` to
`mcp__visualize__show_widget` as `widget_code`, byte-exact — ONLY on a run
whose tool list has that tool after the load in `shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first (a listed deferred tool counts as present: it is loaded, never read as absent).** `tools` is the run's own tool list (S-13,
night 2): when `show_widget` is ABSENT (the merged app's scheduled shape) the
answer carries `text_fallback: true`, `widget_posted: false`, the grouped-list
`text` (the confirm queue with row numbers, then the change feed, then this
week's moves — the same rows the widget shows — ending in the saved-at line
naming the page's path on your computer in a code span), `page_pc_path`, the
`calls` to make in order and `push` (one sentence naming counts). Make exactly
those `calls`, in order — `SendUserMessage` with `text` (or that text as the
chat turn when the tool is absent too), the planned `Write` of the landed
page's bytes into a copy in this container, `SendUserFile` with that copy when
planned — land its `rows` through `plan append_jsonl` when any, send `push`
as the notification, and STOP; the two-line narration and the Links section
belong to the widget branch only. The receipt already landed inside the
driver call and carries the same flags plus the landed page's sha. A `show
more` reply re-runs the SAME one-command driver form with `page` N+1 — pages
2+ never write a receipt, and they slice the page-set page 1 froze rather
than re-reading the substrate (PAGESNAP; see `shared/CHAT_ACTION_WIDGET.md`
§ "A page-set is ONE question asked ONCE"). This is the 2026-07-28 defect
where page 2 repeated page-1 rows 14/15 and the header moved 18 -> 22 mid
fire. If `pagination` carries `refreshed`, `suppressed`, or `clamped`,
SAY it in one line before the rows.

**Idempotent single call (RV-3):** run the driver exactly ONCE per page per fire
— if you already hold its output for the requested page, relay it; a
re-run persists a duplicate audit page (the commitments double-render
defect).

The driver's `build_card_view` produces the sections VERBATIM: MONEY / IDENTITY / HYGIENE, each titled with its **honest count**, each row `{name — badge · evidence-with-date · consequence}` carrying ONLY the proposal's registered verbs, rendered as the row's verb dropdown (T2.2; brain rows: `confirm proposal` / `dismiss proposal` / `snooze proposal 7d`; legacy rows: their own shipped verbs — enriched person rows carry `add person` / `proposal not relevant` / `snooze proposal 7d`). Every row embeds its proposal id + target ids VERBATIM (F2) with a sequential visible number (`display_n` — wire ids never render as row text, RV-5). The header is the words "Staff Meeting", with no count in it (FOLD1-B R-4; `brain_proposals.assert_no_debt_header` raises on a digit there) — never write one, and never restate a count of your own anywhere on the page. No row holds Apply on a missing input (F-17) — inputs are optional, empty applies as proposed. Do not add, relabel, or reorder verbs; do not compose a bulk verb (STOP rule 7).

- **Pagination as design:** page 1 first; the position line teaches `show more`; section titles keep the full honest counts on every page. Never chunk the post itself, never render the full unbounded set in one page (§ Transport). Since STAFFCUT the page-set is itself bounded (~2 pages), so `show more` walks the fire's own page-set; the rows the bound held back are not on any page of this fire — they stay queued and lead the next one, which is what the section-title pointer says.
- Footer: batch Apply-all + the standard undo affordance. The consolidated ack (apply-choices) ends with **"Say `undo` to reverse this."** — EXCEPT when the batch included a record merge (`merge person records`): then the ack instead ends **"Say `undo` to reverse this — except the record merge, that one is permanent."** (UXC1 — the blanket undo promise must never cover the one action it cannot reverse.) — batch undo reverses commitment closes, mutes, and brain-batch auto-applies additively (`brain_undo.undo_batch`); never edit or delete prior events.

The two-line narration (Phase 3's two halves, one line each, drop-empty, opening with `window['label']` and no other window words) goes ABOVE the widget; nothing goes below except the Links section.

## FINAL RESPONSE — the fire's last words (R-RW3-4; S-13, night 2)

The last message of this run is exactly the driver's `text`, unchanged; when `SendUserMessage` exists it is called with the same text first; nothing is written before the first line of that text or after its last line — no summary of what ran, no note about tools, no file name, no variable, no apology. A refusal's `lines` are the whole final response the same way. On a run WITH the widget tool the turn is the widget and the Links section, as above, and nothing after them.

# Phase 6 — Receipt verification + STOP

**Normal fires: the receipt already wrote INSIDE the Phase 5 driver call** (`fired_via` — the answer's `receipt`, the old `CR-RECEIPT: {...}` line, is the confirmation). Do NOT append a second one, and **NEVER hand-roll receipt JSON**.

Since STAFFCUT that receipt also carries PER-KIND counts (`open_by_kind`, `surfaced_by_kind`) and the digest/bound arithmetic (`queue_rows_rendered` vs `queue_items_represented`, `digest_rows`, `page_bound`) on `extra_data`. The scalar `surfaced` keeps its exact meaning — the rows the widget showed. This is additive telemetry so load history is MEASURABLE: the 2026-08-02 audit had to reconstruct 23 fires from an upper-bound model because no receipt had ever recorded what was surfaced. Nothing here is for you to compute, narrate, or repeat in the chat.

The ONE branch that still writes its receipt here is **degrade** (Phase 2.9 — the surface never rendered, so the driver never ran): append it via the canonical helper: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "staff-meeting", fired_via=<the Phase 2.9 receipt_fired_via>, surfaced=0, extra_data={"rerun_of": <lateness["rerun_of"], or omit on any other tier>})` — same `rerun_of` rule as the driver call above (SPEC RERUNFAN1): copy `lateness["rerun_of"]` verbatim on a `rerun` tier, OMIT the key entirely on every other tier, and this branch is one of the others.

That call — `receipts.log_receipt` with its append held — runs where the data is, and the row it writes lands through the one door:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"extra_data": {<"rerun_of" only on a rerun tier -- this branch never is, so empty>}, "fired_via": "<the Phase 2.9 receipt_fired_via>", "surfaced": 0, "workspace_root": "<WS>"}, "name": "staff_meeting_helpers:plan_staff_receipt"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "staff-meeting", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer above, in order>]}'
```

Then STOP — narration + widget + Links section is the whole turn.

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
