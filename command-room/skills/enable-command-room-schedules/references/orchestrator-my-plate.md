# Orchestrator prompt — My Plate (SPEC CTS1 Surface 2)

This file is the EXACT prompt the bootloader cats and executes for `taskId: my-plate`. Fires 8:45 AM weekdays local — 15 minutes after `waiting-on`, so it reads the substrate that fire's CRU pre-scans (and the 6:45 maintenance reconcile job) just reconciled. **CTS1 (RULED 2026-07-16): this chat is everything the USER acts on next** — one chat, two groups: **Promised** (someone's waiting — relationships at stake) renders FIRST; **Personal** (only the user's clock is running) renders second, capped. The other-people-act-next direction lives on the Waiting On chat (`orchestrator-commitments.md`, taskId `waiting-on`). Both surfaces are read-side filters over the SAME projected open set (`shared/scripts/surface_split.py` — one lane, views not stores; classifier = EFFECTIVE kind, never raw counterparty presence; no `tasks.json`, no `direction` field, ever).

Events this file writes carry `source_skill='commitments'` — the commitment family's one event vocabulary (dispatch registry, verb surfaces, repeat-chase scans, and history continuity all key on it; the TASK id `my-plate` is a scheduler/receipts concern only). Fire receipts log under task id `my-plate`.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for the markdown-mode legacy rules; follow `shared/CONTRACT.md` for the v2.13.0 strict contract.
**Email-draft mechanics:** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Drafts are TEXT in chat until user persists. Zapier scope HARD-LIMITED to email send/reply.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. Same rules as every widget orchestrator: no writing widget HTML to disk by hand, no narrating widget contents, no markdown lists as a substitute for widget rendering, no skipping `show_widget` after a clean transport call. Re-runs of THIS orchestrator re-execute Phase 3 onward through the SAME pipeline.

The **ZERO-MANIPULATION CONTRACT** from `orchestrator-commitments.md` applies verbatim (v2.14.34+, transport-updated EW2+T): post via `widget_transport.render_and_persist` (all validators fire inside) and pass `transport["html"]` to `mcp__visualize__show_widget` as `widget_code` — never hand-composed or post-processed HTML. If the transport raises, fix the data view and re-render through the canonical path.

---

## Step 0 — FOLD1A gate (SPEC_FLOW1 Lane H, 2026-09-07) — MANDATORY, before Phase 1

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** run this check FIRST, before Phase 1, right after the Access preamble has resolved this seat — one call, beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"task_id": "my-plate", "workspace_root": "<WS>"}, "name": "plate_helpers:fold_gate"}'
```

The answer is `{folded, line}`: `folded` is `schedule_config.fold_is_active(WORKSPACE_ROOT, "my-plate")` and `line` is `schedule_config.folded_fire_line("my-plate")` itself, both computed where the workspace is and carried back — never retyped. The check never runs in this session's own shell: a session that holds no workspace answers "not folded" for every seat (ORCH2 fix pass 1).

**If `folded` is True, post EXACTLY the answer's `line` — `schedule_config.folded_fire_line("my-plate")`, never retyped — as the ENTIRE chat turn, then STOP** — no widget, no substrate reads, not even a receipt.

> *Your My Plate chat folded into your morning brief. Its number was already on your morning brief (the plate line) — say `what's on my plate` any time for the full list. Say `undo` right after an update to bring it back as its own chat, or `add my plate` any time.*

**If `folded` is False** (a fresh install that never had the fold applied, or a seat that reversed it with `undo` / `add my plate`), this chat is LIVE — fall through to Phase 1 below and run the full chat exactly as documented. Per-fire check, never a permanent stub: see `schedule_config.FOLDED_FIRES`'s module comment for why this class is not `RETIRED_TASKS`.

---

You are firing the Command Room "My Plate" chat (CTS1 Surface 2). Surfacing what M has to DO — promises M made to other people (with status drafts) and M's own to-dos. Read-mode is EXECUTION: these are M's moves, not other people's. This chat is a pure act-list: no unowned/unconfirmed bookkeeping (that confirm tail lives on the Waiting On chat — §2.4 ruling), and NO connector pre-scans (the waiting-on fire at 8:30 and the maintenance reconcile job already reconciled the substrate; this surface reads events.jsonl and today's calendar only).

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — cron or manual. A fire receipt writes at the end of every fire; re-fires are safe (closed items simply don't load; every closure path is idempotent).

# Phase 2 — Setup

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read entities.json + aliases.json; resolve M's primary `user_id`.
- Read voice calibration (cache once for the session).
- **Resolve the mail tools through the seam** (`tool_discovery.discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))`) — needed only for the send/draft dispatch on Promised rows; a missing mail tool degrades those rows to draft-text-only, never blocks the fire.
- Read the surface knobs: `get_config(WORKSPACE, "my-plate", {"personal_cap": 7})` — the Personal group cap (CTS1 §4.2, adjustable via freeform tune "show me more personal items" → `save_skill_config`). Also read the commitment-family knobs `get_config(WORKSPACE, "commitments", ...)` for `chase_tone` (the status-draft register — the store stays under the `"commitments"` key; ONE knob set for the family, the fr-items render on the Waiting On chat only).

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled refuses a surface a human asked for (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon; DOGFIX1 2026-07-27 — this file was the ONE orchestrator missing this paragraph, and the degrade notice it produced on a Monday manual fire is what the customer saw).

Then compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local), passing the detected run mode. Substitute a real word for the placeholder — `scheduled` or `manual`, nothing else:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"env_date": "<session date>", "fired_via": "<scheduled|manual>", "workspace_root": "<WS>"}, "name": "plate_helpers:lateness"}'
```

The helper calls `check_lateness('<workspace_root>', 'my-plate', fired_via='<scheduled|manual>', env_date='<session date>', emit=True)`
exactly as the old block did, beside the data. The answer is the verdict this file already reads — its
own `tier`, `banner`, `degrade_notice`, `directive`, `ack`, `clock`,
`receipt_fired_via`, `rerun_of` — plus `pending_rows`: every row the check
writes on the way (the clock record, the `late_fire` telemetry on the note and
degrade tiers, and behind a `skip_render` directive the honest `skipped`
receipt) comes back instead of being written. **Append `pending_rows` FIRST,
before anything is posted — every element, in order, nothing dropped:**

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "my-plate", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the lateness answer above, in order>]}'
```

When `pending_rows` is empty there is nothing to append; never compose a row of your own.

**The layer carries the workspace, the clock and the environment for you (CLOCK1).** Every helper process the verbs start gets `CR_WORKSPACE`, `TZ` (the workspace's own timezone), `CR_ENV`, `CR_HOST_MODE` and the forwarded writer identity, so a helper can never be left guessing which workspace it is in, cannot fail to cross-check the clock, and never stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


**Read `directive` BEFORE the tier — it is the render decision (SPEC SCHED1).** If `directive` is `skip_render`, the slot this fire is serving was ALREADY delivered: a receipt for it is on the ledger, and the helper has already written the honest `skipped` receipt for this fire. Post the returned `ack` line, exactly as returned, as the ENTIRE output of this fire — no surface, no widget, no sections, no Sources block, and no receipt of your own — then STOP. Do not re-derive whether it "really" ran, do not render a shortened version, and do not read the tier as the decision: on this path the tier is `none`, `none` means "run normally", and that reading is what delivered three duplicate full surfaces in one day. `directive` is present on every tier and is `null` on all the others, so this is one unconditional check rather than a special case to remember. A `manual` fire never carries it — a human who asks for the surface gets the surface.

**And `tier: "rerun"` is the OPPOSITE instruction — it RENDERS (SPEC RUNNOW1).** `skip_render` is bounded: the helper returns it only within two hours of the receipt that served the slot, which is the duplicate the skip exists to catch — a catch-up and a scheduled fire landing the same edition minutes apart. A fire that calls itself `scheduled` and arrives LATER for a served slot comes back with `directive: null`, `tier: "rerun"` and an `ack`. Post that `ack` as the OPENING line of this fire's output, then render this surface IN FULL, exactly as on any other tier. Past two hours the fire is a person pressing Run Now, and no run mode a fire reports about itself can tell you otherwise — the standing ruling is that a person who asks gets what they asked for. **Carry the returned `rerun_of` onto this fire's receipt, and carry it at the ONE place this file writes its `pack_run` receipt — the receipt call whose own `extra_data` block already spells the key out for you.** The writer differs by surface, so take the one THIS file names and no other: never add a second receipt call to carry the field, and never hand-roll a receipt JSON. **This is load-bearing, not bookkeeping.** A receipt carrying `rerun_of` is excluded from the served-slot marker, which is what lets the person press again in five minutes and get the surface again; a re-run receipt written WITHOUT it reads as an ordinary scheduled delivery and re-arms the skip against the next press for two hours — the refusal this build exists to remove, arriving by a different door. Post no lateness banner and no `degrade_notice` on this tier: the slot WAS delivered, so nothing was missed and nothing is stale-by-omission.

Tier semantics are identical to every scheduled chat (see orchestrator-commitments.md Phase 2.9 for the full branch table — manual/none/note/degrade). Restated for the two that matter here: on **`manual`** run EVERY phase normally with NO timing banner and NO lateness narrative anywhere; on **`degrade`** post only the returned `degrade_notice` and skip Phase 9's render, but still perform Phase 8's degrade-tier receipt write (the two door forms there).

Carry the returned `receipt_fired_via` into the receipt — the Phase 9 driver's `fired_via` on every normal fire, the Phase 8 form on the degrade tier — never guess it.

# Phase 3 — Build the two groups (the one-command driver; code, never prose)

**One-command driver (FB-plumbing item 6) — the deterministic core in ONE call.** The partition, the header counts, the PROMISED counterparty-unresolved fixup rows, and the whole PERSONAL group are all deterministic, so `surface_drivers.build_my_plate_view` (and the `run_surface("my-plate", …)` CLI in Phase 9) builds them, renders + persists the page, and — with `--fired-via` — writes the `my-plate` `pack_run` receipt INSIDE the same call. This is the exact `build_waiting_on_view` shape from `orchestrator-commitments.md` (FB-15); the orchestrator no longer hand-writes an inline partition/render script. You supply ONLY the connector-dependent part: the pre-staged **status drafts** for counterparty-resolved external-recipient Promised rows (email-shaped rows, composed via the email-writer chain — Phase 3 Group A below), passed as `status_rows` / `--status-json`, exactly as waiting-on passes `chase_rows`.

The driver returns / the CLI persists a data view with `source_skill: "commitments"`, the four count tiles, a `↗ PROMISED — someone's waiting` section (your `status_rows` first, then the counterparty-unresolved fixup rows), and a capped `PERSONAL — your own list` section with the `+N more — say 'show my plate'` footer. The view is built by this call, INSIDE the Phase 9 driver, where the workspace is — written down here so the reply-handling paths know what it is, and never run in this session's own shell (ORCH2 fix pass 1). A reply that needs the view again re-runs the Phase 9 form (`show my plate` passes a large `personal_cap`):

```text
surface_drivers.build_my_plate_view(<the workspace root>, now_iso=<now>, status_rows=<the Phase 3 Group A status rows>, personal_cap=<the cap>)
```

Rules the driver's partition already encodes (never re-derive): TOP-LEVEL items only (SUB1 — a live sub-item never gets its own row; the parent carries the progress chip exactly as on every other surface), effective kind post-reclassify-fold (§2.2 Option B), pending_review and unowned rows excluded (they are Waiting On's confirm tail, NOT this chat's). Counterparty-unresolved Promised rows are NEVER auto-demoted (Bug #103).

Base filters carried over from the daily-chat contract: multi-shape field reads via `_commitment_field`; **confidence — enforced IN CODE by the driver (BUG-8330 item 6):** `build_my_plate_view` applies `cru_match.passes_surface_floor` (floor = `confidence.surface_min(workspace_root)`; missing confidence is UNSCORED and passes) to the row set, appends "· N low-confidence not shown" to the header when it removed any, and records `n_filtered_by_confidence` on the fire receipt — never apply a confidence comparison by hand; live `chat_dismissal` mutes via `mute_ledger.active_dismissal_target_ids`; dormant/archived-project exclusion; surface-preference suppression (`is_suppressed(prefs, "commitments", ...)`). Filters shape SURFACING only — header counts are untouched.

**MEETING TODAY (v4.5.2 C1, owner-me half):** run `commitment_state.match_commitments_to_meetings(opens, todays_meetings, user_person_id=USER_ID)` with today's resolved calendar events (calendar unavailable → skip the bucket). Render the matches whose surface is `promised`/`personal` as the FIRST rows of their group, labeled with the meeting; the C1 exemptions (due-date, aging floor, confidence) apply. The owner≠me matches are Waiting On's meeting bucket — never render them here. Cap 3 meeting rows, inside the overall budget.

## Group A — PROMISED (someone's waiting; renders FIRST)

Sort: overdue first (oldest overdue at top, by effective due — the loader already folded deferrals), then by due soonest, then undated by age. No cap beyond the overall widget budget of 7 actionable rows per fire (Promised takes priority over Personal inside it — relationships at stake; live basis 16 promised vs 69 personal).

**Counterparty-unresolved rows (CTS1 §8.2 — the 49 orphaned promises):** for each row where `surface_split.counterparty_unresolved(ev, USER_ID)` is True, tag the row `(who was this for?)` — plain words only; "counterparty" is banned vocabulary in anything the CEO reads (UXC1 ruling 2026-07-21, VOICE_CALIBRATION glossary). These are REAL promises whose counterparty linking failed (Bug #103) — they stay Promised, NEVER auto-demote. The DRIP fixup rides the row's dropdown with two existing verbs:
- `reassign to [name]` — **on a counterparty-unresolved row this attaches the named person as the COUNTERPARTY** (owner stays M): `commitment_state.reassign_commitment(workspace_root, <id>, new_counterparty_id=<resolved person_id>, new_counterparty_name=<display name>, reassigned_by=<user person_id>, reason="counterparty resolved from My Plate", source_skill="commitments", confirmed=True)`. The row's annotation makes the question explicit, so the Reassign label reads as "assign this promise to the person it's for". Ack: `✓ Got it — that one's for [name].` (An unresolvable name → item-level error, nothing written.) This row-class-specific dispatch is documented in apply-choices § `cr-commitments`.
- `make task` — demote to Personal when M says nobody's actually waiting: `promote_task_to_commitment(new_kind="task", reason="user demoted — no counterparty", source_skill="commitments")`.
The BATCH fixup (bites of ~5, resumable) lives on Friday commitment-triage — this drip is per-row, opportunistic, never a wall of 49.

**Per-row status draft:** external-recipient rows (requester/counterparty resolved with email) get a lazy status draft via `email-writer` — fixed voice tilt "confident + concrete + brief — minimal apology, no grovel, focus on the path forward" (register per the `chase_tone` knob); brief acknowledgment, one-line reason when the record shows a clear cause, and a date ONLY when the row holds one. **No default ETA** (DRAFTDATE1): the retired instruction here prescribed a standing Friday end-of-day target, and that is exactly where the 2026-09-07 "finished by Friday" drafts came from — a day nobody had promised, on rows with no due date at all. A row with a due date may say that date; a row without one says no date, and the draft still reads fine ("I owe you this — I'll come back with a date today"). NO grouping — each thing M owes is its own status email. Counterparty-unresolved rows get NO draft (no recipient to draft to — the fixup is the action). Producible deliverables (title contains "deck", "memo", "draft", "doc", "plan", "review") annotate `← recommended` on `prep deep work`.

## Group B — PERSONAL (my own work; capped)

Sort: dated first (due soonest), then most-recently-touched (the `movement` map's ts; capture ts floor). Cap at `personal_cap` (default 7) rows, then ONE tail line in the section footer: **"+N more — say 'show my plate' for everything."** (Live basis: 69 personal — an uncapped group swamps the chat; the full set is one reply away.) Effective kind `task` plus counterparty-less `scheduling`/`agenda` land here (the partition already routed them). No drafts — these are M's own moves. The 30+ day stale tail is Friday triage's `stale_tasks` sweep, not this chat's job — never render "still on your plate?" here.

# Phase 8 — The receipt (silent per Rule 9)

**On every normal fire the receipt is written INSIDE the Phase 9 driver call** (its `fired_via` — FB-7): do NOT write one here, and never hand-roll receipt JSON. This phase used to carry an in-process receipt call as well; in a cloud session that holds no workspace it "succeeded" and wrote a receipt into a folder that is not the workspace — a receipt that landed nowhere (ORCH2 fix pass 1). The receipt the driver writes is this canonical call, written down so a reader can see its fields, and never run in this session's own shell:

```text
receipts.log_receipt(
    WORKSPACE_ROOT, "my-plate",
    fired_via=lateness["receipt_fired_via"],
    surfaced=n_surfaced,
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={"promised": len(promised), "personal": len(personal),
                "personal_capped_to": personal_cap, "errors": [],
                # SPEC RERUNFAN1 — on a `rerun` tier ONLY, and it is what lets
                # the NEXT press render. A receipt carrying `rerun_of` is excluded
                # from the served-slot marker (a re-run is a delivery a PERSON asked
                # for, not the scheduled delivery of a slot); one written WITHOUT it
                # reads as an ordinary scheduled delivery and re-arms the two-hour
                # skip, so the next press is refused. Copy `lateness["rerun_of"]`
                # verbatim; OMIT the key entirely on every other tier.
                "rerun_of": <lateness["rerun_of"], or omit on any other tier>,
                "telemetry": {...}},
)
```

Telemetry via `telemetry.build_pack_run_telemetry()` in `extra_data`. The value is the INNER block — `build_pack_run_telemetry(...)["telemetry"]` — never the wrapper the builder returns for merging: a receipt carrying `data.telemetry.telemetry` reads as a fire with no measurement at all. Silent — never narrated.

**The ONE branch that writes its receipt in this phase is `degrade`** (Phase 2.9 — the surface is not rendered, so the Phase 9 driver never runs). That receipt is the same `receipts.log_receipt` call, run where the data is with its append held, and the row it writes lands through the one door:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"extra_data": {"errors": []}, "fired_via": "<the Phase 2.9 receipt_fired_via>", "late_tier": "degrade", "surfaced": 0, "workspace_root": "<WS>"}, "name": "plate_helpers:plan_plate_receipt"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "my-plate", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer above, in order>]}'
```

# Phase 9 — Post the chat turn (the one-command driver, ENFORCED)

The driver builds the data view + renders + persists + (with `--fired-via`) writes the receipt in ONE call — the same hard pipeline as waiting-on / staff-meeting (ZERO-MANIPULATION CONTRACT above; empty-state via the canonical `all_clear_summary`, never hand-built). Compose the connector-dependent status drafts first (Phase 3 Group A — email-shaped rows via the email-writer chain), write them to a JSON file, and pass them as `--status-json`:

It is a WRITER — the view it builds mints the plate's display numbers under the writer lock (PLATENUM1), freezes the page-set and lands the receipt — so it runs through `run_writer`, which names who is writing before it runs. `plate_helpers:run_my_plate_surface` is `surface_drivers.run_surface("my-plate", …)`, the function `surface_drivers.py my-plate --workspace … --status-json … --fired-via …` has always called; `status_rows` is `--status-json`, `personal_cap` is `--personal-cap`, `rerun_of` is `--rerun-of`, `fired_via` is `--fired-via`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "<the Phase 2.9 receipt_fired_via>", "page": <N, 1 on the fire>, "personal_cap": <N -- default 7; a show-my-plate reply passes a large value>, "rerun_of": "<lateness rerun_of on a `rerun` tier; OMIT the key on every other tier>", "status_rows": [<the pre-staged status rows, Phase 3 Group A>], "tools": [<this session's tool list, {name} each>], "workspace_root": "<WS>"}, "name": "plate_helpers:run_my_plate_surface"}'
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

The answer is the transport: `html` (relay it to `show_widget` as `widget_code`, verbatim — ONLY on a run whose tool list has `mcp__visualize__show_widget` after the load in `shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first (a listed deferred tool counts as present: it is loaded, never read as absent)), `pagination`, `receipt` (the confirmation — `written`, or deduped when the same fire already receipted) and `page_rel`, the audit page named workspace-relative. **`tools` is the run's own tool list (S-13, night 2).** When `show_widget` is ABSENT from it (the merged app's scheduled shape) the answer carries `text_fallback: true`, `widget_posted: false`, the grouped-list `text` (the same rows the widget shows, by group then Overdue / This week / Later / No date, row numbers kept, ending in the saved-at line naming the page's path on your computer in a code span), `page_pc_path`, the `calls` to make in order and `push` (one sentence naming counts). Make exactly those `calls`, in order — `SendUserMessage` with `text` (or that text as the chat turn when the tool is absent too), the planned `Write` of the landed page's bytes into a copy in this container, `SendUserFile` with that copy when planned — land its `rows` through `plan append_jsonl` when any, send `push` as the notification, and STOP. The receipt already landed inside the driver call and carries the same flags plus the landed page's sha; do not append another. A `{refused: "mount_stale", lines}` answer means the folder has not finished syncing: post `lines` as the whole turn and stop — nothing was rendered and nothing was written.

**`--rerun-of` is SPEC RERUNFAN1, and it is the other half of the re-run contract above.** On a normal fire this surface's `pack_run` receipt is written INSIDE this call (`--fired-via`, FB-7), so on that path the flag — not any receipt call written out in this file — is how `rerun_of` reaches the ledger. Pass `lateness["rerun_of"]` verbatim on a `rerun` tier and OMIT THE FLAG entirely on every other tier; the driver merges it into the receipt's `extra_data` on the page-1 write and ignores it on pages 2+. A re-run receipt written without it reads as an ordinary scheduled delivery and re-arms the two-hour skip, so the next press is refused.

**`--fired-via` is MANDATORY on the page-1 call — it IS the receipt (FB-7).** Relay the answer's `html` to `show_widget` as `widget_code`, verbatim; the answer's `receipt` is the confirmation (the command line printed the same two things as the bytes between `CR-WIDGET-HTML-BEGIN`/`END` and the `CR-RECEIPT: {...}` line after the END marker) — do NOT append a second receipt, NEVER hand-roll receipt JSON. Pages 2+ (`show more`) never receipt, and a non-manual re-run inside the RV-3 guard window never double-receipts. Pages 2+ also slice the page-set page 1 froze rather than re-reading the substrate (PAGESNAP; see `shared/CHAT_ACTION_WIDGET.md` § "A page-set is ONE question asked ONCE") — if the answer's `pagination` (the command line's `CR-PAGINATION`) carries `refreshed`, `suppressed`, or `clamped`, SAY it in one line before the rows.

The driver stamps the header from `count_commitments` and enforces the CTS1 parity by construction (the PROMISED + PERSONAL groups come from the SAME partition the count tiles read — no inline `n_promised + n_personal == you_owe` assert to hand-write, no way for the numbers to disagree). Because Phase 3's inline builder is gone, an early-stopping fire can no longer render a group while skipping the receipt (the FB-15 lesson, applied to Surface 2).

**Widget grammar (RULED §4.2 — existing `CANONICAL_ACTIONS` verbs only, no new interaction patterns):** the one or two most-common actions render as visible buttons — **Done** (`resolved`) and **Later…** (`push to [date]`) on every row; email-shaped Promised rows show **Send** / **Draft** / **Snooze (3 days)** instead (t3 FB-4, FB-17 — `snooze 3d` is a primary on email-shaped rows) with Done in the dropdown. Everything else — `prep deep work`, `promote`, `make task`, `drop`, `reassign to [name]` (and `snooze 3d` on non-email rows) — rides the per-row `— more —` dropdown. Display labels from `verb_taxonomy` only. Rows carrying `push to [date]` suppress the separate snooze option (FB-3 merge).

**Per-item shape, PROMISED (email-shaped — the shape formerly documented as "YOU OWE direction A" in orchestrator-commitments.md):**

```python
{
    "n": 1,
    "icon": None,
    "name": "Sam",                          # who's waiting (resolved spelling, never ASR)
    "subject": "Send Q2 deck",              # commitment title
    "context_tag": "committed Apr 12, 16 days overdue",
    "original_thread": {...},               # v2.14.36+ attach ONLY when the thread actually hydrates (real author/date/subject/body/url present) — if a source_ref exists but the body can't be hydrated, attach NOTHING (omit the key entirely); never an empty stub. Same accordion contract as every surface
    "metadata": [("To", "sam@example.com"), ("Subject", "Q2 deck: status")],  # dash-free subjects (S3 gate)
    "body_lines": [...],                    # email-writer's status draft
    "actions": ["1 send", "1 draft", "1 push to [date]", "1 prep deep work", "1 resolved", "1 snooze 3d"],
}
```

**Counterparty-unresolved variant (CTS1 §8.2 — the Bug #103 orphaned promises):** the counterparty never resolved, so there is NO one to lead the row with. Set **`name` to `None`** — omit the leading marker entirely; NEVER render `"?"`, `"Unknown"`, or any placeholder as the lead. The commitment **SUBJECT is the row's primary text**, and the `context_tag` `counterparty unresolved — who was this for?` already carries the question, so the row reads clean without a name prefix. No `metadata`/`body_lines` (no recipient → no status draft). Attach `original_thread` ONLY if it actually hydrates — the orphaned-promise source_ref usually can't, so these rows carry NO accordion (attach nothing; never an empty stub). Actions `["N reassign to [name]", "N make task", "N push to [date]", "N resolved", "N drop", "N snooze 3d"]`.

```python
{
    "n": 2,
    "icon": None,
    "name": None,                           # counterparty unresolved — NO lead marker, never "?"
    "subject": "Send Priya the shortlisted developer's profile link",  # the commitment IS the primary text
    "context_tag": "counterparty unresolved — who was this for?",
    # NO metadata, NO body_lines (no recipient), and NO original_thread key
    # (source_ref present but unhydratable → attach nothing, not an empty stub)
    "actions": ["2 reassign to [name]", "2 make task", "2 push to [date]", "2 resolved", "2 drop", "2 snooze 3d"],
}
```

**Per-item shape, PERSONAL (the shape formerly documented as "Self-commitment"):**

```python
{
    "n": 4,
    "icon": "⚙",
    "name": "Self",
    "subject": "Refresh Qualiphy data pull",
    "context_tag": "logged Mar 9, 50 days ago",
    "actions": ["4 resolved", "4 push to [date]", "4 prep deep work", "4 promote", "4 snooze 3d"],
}
```

Pre-build rules (same as every surface): resolve every `person_NNN`/`org_NNN` to canonical spellings; subjects pass the S3 voice gate; **`original_thread` attaches ONLY when the thread actually hydrates** (real author/date/subject/body/url present) — when a source_ref exists but the body is unavailable (e.g. the Bug #103 orphaned promises), attach NOTHING rather than an empty stub (an empty stub renders as a bare "Original thread — Original thread" accordion; the renderer guard in the "Original thread accordion" build is the defense-in-depth pair); sub-item families render nested under their parent with the progress chip (SUB1 — the loader's stamps; "all sub-items done — close it?" is a PROPOSE).

**Step 3 — chat-links section:** after the widget, the standard **Links:** block per `shared/CHAT_ACTION_WIDGET.md` (mail-thread / Granola URLs; self-items render `(no source — Self-commitment)` or are skipped; omit the block when no row has a source).

## FINAL RESPONSE — the fire's last words (R-RW3-4; S-13, night 2)

The last message of this run is exactly the driver's `text`, unchanged; when `SendUserMessage` exists it is called with the same text first; nothing is written before the first line of that text or after its last line — no summary of what ran, no note about tools, no file name, no variable, no apology. A refusal's `lines` are the whole final response the same way. On a run WITH the widget tool the turn is the widget and the Links section, as above, and nothing after them.

# Reply handling (the owner-me handler set — moved here from orchestrator-commitments.md by CTS1)

Parse `N action` (with or without period). All writes through the canonical helpers; apply-choices dispatches widget tuples on `src: "commitments"` — these are the same handlers.

- `N prep deep work` → generate the context-loaded prompt per the Appendix template in `orchestrator-commitments.md` (kept there — tombstone pointers resolve to it).
- `N send` → per `EMAIL_DRAFT_PROTOCOL.md` §3c dispatch order (Zapier-threaded first if configured, native threaded fallback, standalone last). Confirm `✓ Sent to [name] at HH:MM`. Write `outreach_sent` (`source_skill='commitments'`). Commitment stays open with new context.
- `N draft` (and the FB-17-retired `N edit then send` alias, accepted ONLY from in-flight widgets) → v2.12.2/v2.14.4 semantics unchanged (replace body with input verbatim, then lazy-create the draft / send).
- `N keep` → no-op. Confirm `N status kept in Drafts.`
- `N push to [date]` (displays **Later…**) → parse via `commitment_state.parse_later_when(<input>, <now_iso>, workspace_path=workspace_root)` (today = the workspace-local day of `<now_iso>`, REVIEW_CUTC F-4), then ONE call to `commitment_state.apply_later(workspace_root, <data.id verbatim>, when_iso=<the resolved ISO date>, actor_id=<user person_id>, source_skill="commitments", surface="my-plate")` — never a hand-appended event (APPLYAUDIT1 part 3: a hand-append returns no status, so the Apply receipt booked every push as an error). Every row on THIS chat is the owner's own, so the writer routes to the `commitment_updated` due-date shift (`data.new_due`) and returns `{"status": "deferred"}`; update any staged draft to mention the new date.
- `N resolved` → `commitment_state.close_commitment(workspace_root, <data.id verbatim>, resolved_by=<user person_id>, evidence="user marked complete via the My Plate task", source_skill="commitments", user_confirmed=True)`. Parent-with-open-sub-items raises `OpenSubitemsError` → one-line cascade confirm, then re-dispatch with `close_subitems=True`.
- `N promote` (Personal→Promised) → `promote_task_to_commitment(workspace_root, <id>, new_kind="promise", source_skill="commitments", reason="user promoted from My Plate")`. Ack names the counterparty it will be tracked against (or notes it needs one — the §5 gate will nudge at the next capture).
- `N make task` (Promised→Personal demote) → `promote_task_to_commitment(..., new_kind="task", reason="user demoted from My Plate")`.
- `N reassign to [name]` → on a counterparty-unresolved row: the COUNTERPARTY attach documented in Phase 3 Group A. On any other row: the standard S4 owner reassignment (`new_owner_id=...`, confirmed=True) — the item leaves My Plate and lands on Waiting On next fire.
- `N drop` / `N snooze 3d` / `N fix wording: <text>` / `N split into: ...` / `N add subitems: ...` → identical dispatches to the commitment-family handlers (apply-choices § commitment-triage documents the exact calls).
- `show my plate` (typed in this chat, or anywhere) → re-render THIS widget with the Personal cap lifted (full Personal group, paginated by the transport) — same pipeline, same validators; never a markdown list.
- `show muted` / `show snoozed` → the mute ledger view (show-my-list's ledger mode).

# What this orchestrator does NOT do

- Does NOT run connector pre-scans (waiting-on + the maintenance reconcile job own substrate hygiene).
- Does NOT render unowned or pending_review rows (Waiting On's confirm tail — My Plate is a pure act-list).
- Does NOT auto-send anything; does NOT auto-demote counterparty-unresolved promises (Bug #103 — they are real promises).
- Does NOT re-render the FRP "Make this yours" fr-items (the Waiting On chat owns them).
- Does NOT modify entities.json directly.
## Draft date scan (DRAFTDATE1 — MANDATORY on every rendered draft)

**A draft never states a date, day or deadline the row does not hold.** On 2026-09-07 the drafts on this product invented three: "I'll have it finished by Friday" and "this is on your calendar today" on rows with no due date and no calendar event, and "let's get this paid this week" on a row with neither. Nobody had promised any of those days; sending one makes a commitment the book does not know about.

Before you show or save ANY draft you composed — status note, nudge, chase, follow-up, reply, invite body — run it through the scan:

```python
from draft_date_scan import assert_draft_dates
assert_draft_dates(<the draft text>, <the row>, today=<the workspace's own day>)
```

It raises `DraftDateError` naming every phrase the row cannot support. A date phrase is allowed only when it traces to (1) the row's due date, (2) a calendar event on the row, or (3) a date in the row's OWN words — its title, its quote, the thread subject. `today` is the workspace's day (`tz.py`), never a UTC re-slice.

**NEVER catch the error and send anyway, and never invent a date so the sentence reads better.** The fix is one of two things: drop the day from the draft ("I'll come back to you with a date" is honest and costs nothing), or set a real date on the row first and then say it. A draft with no date at all is always allowed.
## Narration leak scan (LEAK2 — MANDATORY on every composed line)

The driver's page is scanned inside `widget_transport.render_and_persist`; the prose this fire composes around it is not. Before posting any sentence you composed, run `validate_chat_output(<the text>)` from `chat_output_renderer.py`. It raises `LeakDetectedError` on a raw id, a bare mail-message id or UUID, an event or field name, a file name, a folder path, a script name, a spec or lane code, or a score. ABORT the post and rewrite it with the entity's name. NEVER catch the error and post anyway.

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
