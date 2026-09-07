# Orchestrator prompt — End of Day (taskIds `end-of-day` AND `past-meetings`)

This file is the EXACT prompt registered with `create_scheduled_task` for BOTH ids. Fires 5:00 PM weekdays local. **SPEC EOD1 (2026-08-16) turned this fire from a meeting-processing job into the day's CLOSE**, upgraded IN PLACE rather than replaced: the registered prompt on every live machine names THIS filename and loads its steps fresh at fire time, so upgrading the file the task already reads is the only change that reaches a machine without a re-registration. **SPEC EOD2 pointed the new `end-of-day` taskId at this same file** — one file, two `ORCHESTRATOR_MAP` rows, no fork; the rename is proposed, never applied, so a machine still on `past-meetings` fires this exact pack indefinitely. (Why the filename keeps the old name, and why the receipt does too: see the EOD2 note after the contract block.)

**M's rulings this fire implements (2026-08-16, locked):** ONE chat, not two. The client is never handed a pile. The day-close should make the open book smaller most days. What End of Day reads, it writes to memory with a resolvable pointer — the daily close IS the daily memory commit.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Brief save path (v2.13.0+):** all `.docx` briefs save to `_hq/meetings/` via `shared/scripts/brief_path.py` `get_brief_path("past_meeting", slug, date)`. NEVER hand-roll paths. NEVER save to `[Project]/meetings/` (that path didn't always resolve in Cowork's sandbox).
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for legacy rules; follow `shared/CONTRACT.md` for v2.13.0 strict contract.
**Email-draft mechanics (follow-up drafts):** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Past Meetings produces follow-up draft TEXT only — actual sends happen through Inbox / Commitments where `N send` follows §3c.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules. Pre-v3.5.0 each orchestrator inlined a ~25-line copy; v3.5.0+ they reference the shared file.

End-of-Day-specific scope notes:
- `.docx` meeting briefs in `_hq/meetings/` continue per Phase 4 (inside Phase D) — those are documented per-meeting deliverables, separate from the post-widget output surface the STOP CONTRACT governs.
- Re-runs (`end of day`, `regenerate past meetings`, `re-process today's meetings`) re-execute Phase 1 onward; do NOT save intermediate outputs.

---

## EOD2 — the two taskIds, and the two names that deliberately did NOT move

**The FILENAME keeps saying `past-meetings`.** Registration substitutes `<ORCHESTRATOR_FILENAME>` into each task's bootloader at registration time, so every `past-meetings` prompt already on the fleet names this exact file. Renaming it would break every one of those registrations at their next fire — a plugin upgrade that turns working chats into load errors. Giving `end-of-day` its own file would be worse: two files for one pack, drifting apart from the first edit that only lands in one of them. So `ORCHESTRATOR_MAP` has two rows pointing here, and that is the whole mechanism. (Same reason `waiting-on` → `orchestrator-commitments.md` and `pulse` → `orchestrator-dont-forget.md`.)

**The RECEIPT keeps saying `past-meetings` too** — `end_of_day.TASK_ID`, unchanged by EOD2, and the Phase 5 writer below is unchanged with it. The rename is per-machine and propose-only, so at any moment part of the fleet fires under each id; writing the receipt under whichever id happened to fire would split one day-close history into two half-series at whatever date each customer took the offer, and the week roll-up, the score, `catchup_window` and `late_fire` all read that series. It stays one series. `receipts.TASK_PREDECESSORS["end-of-day"] = ("past-meetings",)` is how the new id reads it.

**What this means when you fire:** nothing. Do not branch on which taskId started you, do not mention the rename in the chat post, and do not write anything under `end-of-day`. Both ids are this pack.

---

## ⛔ SPEC EODSPEED1 — the close reconciles; it does not fetch (2026-08-26)

The EODPHASE1 phase records measured it: the pack build costs 7–10 seconds; the 9–27-minute wall clock was connector fetching and redundant re-scans at close time. The fix moves the day's fetching EARLIER, never does less of it. Four parts, and the fences under them are binding:

**1. The incremental capture pass.** The capture leg (Phases 3 → 4.8 of this file) also runs as the `meeting-capture` job inside the already-authorized `maintenance` scheduled task (6:45 / 12:45 / 16:30 / 17:45 — CAPSLOT1, 2026-08-27, added the 16:30 pre-close slot to close the structural 12:45→17:00 blind window the EODSPEED1 live test measured; `maintenance_dispatcher.MAINTENANCE_JOBS`), so the day's meetings are captured as they land. **The job executes those phases VERBATIM** — same canonical writers, same admission gates, no relaxed floors, `source_skill` values exactly as written — with exactly three differences:

- **Window:** `catchup.catchup_window(<workspace_root>, 'meeting-capture', floor_hours=24, cap_days=30)` instead of the `past-meetings` window. Its receipt carries `window_incomplete_before` under the batch-cap gate exactly as Phase 3 requires, and the next pass resumes from it.
- **Silence (fence):** the pass posts NOTHING — no chat surface, no widget, no notification, no lateness banner. Writes, briefs on disk, and receipts only. The close remains the one narrator.
- **Receipt:** the pass ends with ONE `eod_incremental.log_capture_pass_receipt(...)` call — a `pack_run` under task id `meeting-capture`, carrying the window fields and counts. **NEVER `log_end_of_day_receipt`, and NEVER any receipt under `past-meetings`:** a pack_run on the day-close series would arm `skip_render` against the real 5 PM close and split the series EOD2 keeps whole. Phase C, Phase 5's day-close receipt, and Phase 6 do not run in the job.
- **⛔ MANDATORY (SPEC EODLEG1) — the pass times its own capture leg.** Record `capture_leg_start` (UTC ISO) before the window computation above and `capture_leg_end` (UTC ISO) right before the receipt call; pass `capture_leg_ms=<the elapsed milliseconds>` to `log_capture_pass_receipt`. This job executes Phases 3 → 4.8 VERBATIM, so its wall time IS a capture leg in the same sense the 5 PM close's `PHASE_CAPTURE` is, and it is stamped under that SAME constant — never a second name invented for this surface — so a reader joining `phase_durations_ms` across the `meeting-capture` and `past-meetings` series sees one leg measured twice a day, not two dialects of it. Best-effort: if the elapsed milliseconds cannot be computed, call `log_capture_pass_receipt` without `capture_leg_ms` — the receipt is owed either way (BRIEFFIX1 Item C).

**2. The close re-verifies over disk.** Nothing about THIS fire's own window computation changes — Phase 3 still computes `catchup_window('past-meetings', floor_hours=24, cap_days=30)`, Phase A still fetches from the mail/chat cursors (which the 6:45/12:45 maintenance legs have usually already advanced). What changes is what the fire FINDS: meetings the incremental pass captured come back `skip_processed` from Phase 3.5's dedup, so this fire fetches transcripts only for what arrived since the last pass. **The close's window is NEVER narrowed because incremental receipts exist** — that asymmetry is the machine-off fence: a day where no pass ran (laptop closed) degrades to today's fetch-at-close exactly, slower and complete, never a thinner close.

**3. The close still narrates the day.** The incremental pass is silent, so the briefs it wrote reach the CEO through THIS fire: Phase 5's render-set mapping gives those meetings status `briefed_prior` (they count as briefed, their briefs render in the Meeting briefs section, and the coverage sentence names them — "captured earlier by the background pass"). See the Phase 5 mapping and Phase 6 Step 3.

**4. Stale-evidence skips are recorded once, not re-walked** (the 237-row class): Phase 4.6 consults and feeds the CRU walk ledger (`eod_incremental.already_walked` / `record_walk`). A transcript whose complete walk is on the ledger, inside the evidence window, is not re-fetched and not re-walked — the recorded verdicts stand (sound because every commitment captured after the recorded walk is stale under EVORDER layer 3's strict ordering; the ledger honors nothing it cannot prove). The matcher itself is untouched; an empty or stale ledger walks normally, byte-identically to the pre-EODSPEED1 build.

**The budget (stated and measured):** a close on a day whose captures are current lands within **5 minutes** of the slot at full checking depth (`end_of_day.CLOSE_BUDGET_MS`). The receipt writer stamps `close_budget` from this fire's own `duration_ms` — the EODPHASE1 phase records plus that verdict are the before/after instrument. The budget is met by moving work earlier; **trimming any check, sampling meetings, or capping mail windows for speed is out of scope and stays out.**

**The equivalence fence:** the same day's material, arriving incrementally or in bulk, yields a byte-identical verdict set — closures found, slipped, confirm rows, synthesis grounding. Pinned by `tests/run_eodspeed1_test.py`. Nothing in this spec adds a write path, relaxes a floor, or changes what the close checks.

---

## ⛔ SPEC EODLEG1 — the fire times its own legs (2026-08-27)

EODSPEED1's own live measurement found `phase_order` covering the pack build alone — 0.45% of a 1,855,666 ms fire — because `phase_ledger` was an OPTIONAL argument and no fire ever passed it. **It is optional no longer.** Three timestamp pairs, recorded as plain UTC-ISO wall-clock deltas (never a monotonic timer — this fire's own phases run in separate `python3 -c` processes, and monotonic time carries no meaning across that boundary): `close_leg_start`/`close_leg_end` in Phase A, `capture_leg_start`/`capture_leg_end` around Phase D, `post_leg_end` (its start is `capture_leg_end`, reused) at the top of the reconcile in Phase 5. Phase 5 folds all three into ONE `PhaseLedger` — seeded with the pack's own fourteen `PACK_PHASES` via `merge_snapshot` so they are not silently discarded — and passes it as `phase_ledger=led` to `log_end_of_day_receipt`, which is now a MANDATORY step, not the optional one EODPHASE1 shipped. Each of the three ⛔ markers below (Phase A, Phase D, Phase 5) is load-bearing on its own; skip any one and that leg is simply absent from `phase_order` — never fabricated, never zero, per Ruling 4. Receipt shape is UNCHANGED: same four keys, same reader contract, same vocabulary (`end_of_day.ALL_PHASES` — never invent a phase name). The code half of the mandate is `end_of_day.receipt_missing_capture_phase`, run in the battery's guard tier: a receipt with `n_processed > 0` and no `PHASE_CAPTURE` in `phase_order` fails it by name, because a prose mandate in this file is not code the battery can execute on its own.

---

## ⛔ SPEC CAPFENCE1 — the capture leg gets a time fence (2026-08-27)

The first live `close_budget` verdict put `duration_ms` at 30.9 minutes against the 5-minute budget, ~86% of it the capture leg. The batch cap (Phase 3) bounds meetings by COUNT — 5 — but nothing bounds the fire by TIME, and at that fire's own pace the count cap alone authorized roughly 33 minutes. **`end_of_day.CLOSE_BUDGET_MS` does not move** — it stays the standing 5-minute promise, and it keeps failing honestly until real closes pass it; widening it to fit a failing fire is fixing the thermometer. The fix instead is a NEW, separate constant: `end_of_day.CAPTURE_FENCE_MS` (15 minutes), a stopping rule for Phase D's capture leg alone. Between meetings — never mid-meeting — check the ledger-elapsed time since `capture_leg_start` (EODLEG1's own timestamp, never a second clock read invented here) against the fence via `end_of_day.capture_fence_should_defer`. Crossed, and this fire has already captured at least one meeting at full depth (**the substance floor, Ruling 3 — never zero briefed on a day that had meetings, and the floor is one, not a knob**): finish the in-flight meeting (never half-capture), then STOP — every meeting still queued defers whole, via the SAME `receipt_window_marker`/`window_incomplete_before` machinery the batch cap already uses (`end_of_day.capture_fence_window_marker`), counted on the receipt as `n_time_fence_deferred`. **Every meeting that DID enter Phase 4 still runs every downstream pass (4.5–4.8) at full, unreduced depth — the fence bounds ENTRY into Phase 4, never what happens to a meeting once it is in.** The exact check, the substance floor, and the deferral live in Phase 4, right after step 9, below. The close's own narration names a fence-triggered deferral distinctly from an ordinary batch-cap one — `end_of_day.coverage_disclosure_lead` leads with "N meetings deferred to tonight's background pass — tomorrow's brief will carry them" whenever `n_time_fence_deferred` is present, pointing the reader at MORNCAP1's morning recovery rather than the generic batch-cap wording.

---

You are firing the Command Room **End of Day** chat. You are closing the day: reconcile what it discharged, resolve what changed, read the day back, and — LAST — capture the day's meetings.

## The four phases, and why the order is the contract (SPEC EOD1 §2)

| Order | Phase | What it is |
|---|---|---|
| 1 | **Close** (Phase A) | The in-fire sent/chat reconcile. Same cursor, same audit event as the maintenance jobs |
| 2 | **Reconcile** (Phase B) | People / deal / identity resolution, same-day and receipted. Existing machinery, nothing new |
| 3 | **Read** (Phase C) | The seven blocks. Computed and FROZEN here |
| 4 | **Capture** (Phase D = the existing Phases 3 → 4.8) | Meeting processing, LAST and fenced |

**Capture is last for a reason that is not tidiness.** A capture written earlier in this fire would be scored by this same fire's reconcile ("did you already handle the thing you wrote down four minutes ago?") and counted by this same fire's read. Capture-last is the circularity fence, and it is the same fence Phase 4.6's `exclude_captured_since` enforces one level down.

**The read is computed BEFORE capture and delivered AFTER it.** Phase C builds the pack and holds it; Phase D does the fire's final work; Phase 5 writes the ONE receipt (carrying both the read's numbered map AND the capture leg's window fields); Phase 6 posts. Receipt-before-post is BRIEFFIX1 Item C and is not negotiable — see Phase 5.

# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. Re-running re-processes meetings that already have `meeting_processed` events ONLY if they're missing the v2.10.x extracted-event types (decisions, commitments, follow-ups) — meetings with complete extraction are noted as "already processed" inline but still re-rendered for visibility.

# Phase 2 — Setup

- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read entities.json + aliases.json + voice calibration (cache).
- Discover Granola MCP tool ID (`mcp__<uuid>__list_meetings` or similar).
- Discover Gmail MCP IDs (or Outlook equivalents on M365).
- M's `person_id` from entities.json.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'past-meetings', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


**Read `directive` BEFORE the tier — it is the render decision (SPEC SCHED1).** If `directive` is `skip_render`, the slot this fire is serving was ALREADY delivered: a receipt for it is on the ledger, and the helper has already written the honest `skipped` receipt for this fire. Post the returned `ack` line, exactly as returned, as the ENTIRE output of this fire — no surface, no widget, no sections, no Sources block, and no receipt of your own — then STOP. Do not re-derive whether it "really" ran, do not render a shortened version, and do not read the tier as the decision: on this path the tier is `none`, `none` means "run normally", and that reading is what delivered three duplicate full surfaces in one day. `directive` is present on every tier and is `null` on all the others, so this is one unconditional check rather than a special case to remember. A `manual` fire never carries it — a human who asks for the surface gets the surface.

**And `tier: "rerun"` is the OPPOSITE instruction — it RENDERS (SPEC RUNNOW1).** `skip_render` is bounded: the helper returns it only within two hours of the receipt that served the slot, which is the duplicate the skip exists to catch — a catch-up and a scheduled fire landing the same edition minutes apart. A fire that calls itself `scheduled` and arrives LATER for a served slot comes back with `directive: null`, `tier: "rerun"` and an `ack`. Post that `ack` as the OPENING line of this fire's output, then render this surface IN FULL, exactly as on any other tier. Past two hours the fire is a person pressing Run Now, and no run mode a fire reports about itself can tell you otherwise — the standing ruling is that a person who asks gets what they asked for. **Carry the returned `rerun_of` onto this fire's receipt, and carry it at the ONE place this file writes its `pack_run` receipt — the receipt call whose own `extra_data` block already spells the key out for you.** The writer differs by surface, so take the one THIS file names and no other: never add a second receipt call to carry the field, and never hand-roll a receipt JSON. **This is load-bearing, not bookkeeping.** A receipt carrying `rerun_of` is excluded from the served-slot marker, which is what lets the person press again in five minutes and get the surface again; a re-run receipt written WITHOUT it reads as an ordinary scheduled delivery and re-arms the skip against the next press for two hours — the refusal this build exists to remove, arriving by a different door. Post no lateness banner and no `degrade_notice` on this tier: the slot WAS delivered, so nothing was missed and nothing is stale-by-omission.

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — **this surface RENDERS, LABELLED (SPEC EODLEDGER1 part 3, M's ruling on D3, 2026-08-19). It does not withhold.** Run every phase below exactly as on any other tier, and open the chat output with `pack["catchup"]["lines"]` verbatim — the span the read covers, how late it is, and the slot it missed. Then the surface, in full.

  **What this replaces, and why.** Pre-EODLEDGER1 this branch performed every substrate write and then posted `degrade_notice` alone. The score anchors on TODAY's morning receipt, so a skipped Tuesday was never scored — not that evening, not ever — and the only surviving trace was a Monday roll-up row reading "no close was recorded". The catch-up machinery worked perfectly and its output was shown to nobody. The degrade tier's instinct was right (a stale surface must never be presented as fresh) and M's ruling is that the remedy is to **label the span, not to withhold it**.

  **DO NOT POST `degrade_notice` ON THIS SURFACE.** It reads "Skipped the full End of Day …", which is true on every other scheduled surface and false on this one the moment it renders. It is a SHARED constant and it is not being reworded — every other orchestrator still posts it as its whole output, and changing it would silently re-render stale surfaces fleet-wide, which nobody ruled on. Carry it onto the receipt instead (Phase 5) so the record keeps the tier's own words; the reader sees `pack["catchup"]["lines"]`.

  **ONE READ, NOT ONE PER MISSED DAY.** `pack["catchup"]["days"]` is the whole span and the label names its ends. Do not fire the surface once per missed day, do not stack per-day sections, and do not narrate the tier name or the word "degrade" anywhere in chat. The per-day detail already lives in the Monday roll-up; a stack of stale surfaces is the pile M's ruling removed.

  **THE SCOPE FENCE.** `late_fire.LATENESS_TIERS` keeps its 3h/24h thresholds and `check_lateness` keeps its return contract byte-for-byte. Only THIS orchestrator's degrade branch changed. Every other scheduled surface keeps today's suppress-on-degrade behaviour, and a fire that finds itself in this branch on any other surface is still suppressing.

  **AND `skip_render` IS STILL `skip_render`.** A slot already delivered posts its `ack` and stops — read `directive` BEFORE the tier, as this file has always required. Late is not the same as duplicate: `renders` on the catch-up block is False whenever a directive is present, and conflating the two is what delivered three duplicate full surfaces in one afternoon.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase A — CLOSE: reconcile what the day discharged, in this fire (SPEC EOD1 §2.1)

Record `fire_start` (UTC ISO) **now, before anything below writes**. Phases 4.6 and 4.6.b both need it and it must predate every append this fire makes.

**⛔ MANDATORY (SPEC EODLEG1) — `close_leg_start = fire_start`, the same instant.** This IS the close leg's own start: the mail/chat reconcile below is `end_of_day.PHASE_CLOSE`. Its wall time is measured as a plain UTC-ISO delta rather than a monotonic timer, because this leg's start, its own reconcile calls, and the receipt call that reports it (Phase 5) each run in a SEPARATE `python3 -c` process (line 99's rule) and a monotonic clock carries no meaning across a process boundary — `PhaseLedger`'s own docstring says "monotonic, never the wall clock," and wall-clock is the right tool the one time two different processes have to agree on an elapsed span. Immediately after `close_result` is built below, record `close_leg_end` (UTC ISO). Keep both timestamps in whatever this fire uses to carry state between its own phases (a temp file, same as the pack driver's `--close-json`) — Phase 5 computes `close_leg_ms = close_leg_end − close_leg_start` from them and folds it into the mandatory ledger there.

Run the SAME machinery the maintenance jobs run — same functions, same cursor, same audit event. Not a copy of it: `reconcile-sent` and `reconcile-chat` are jobs inside the `maintenance` task and this fire calls their entry points directly, so the 17:45 maintenance pass finds an ALREADY-ADVANCED cursor and closes nothing twice. A second implementation here would be a second cursor, and two cursors over one mailbox is how a close gets written twice.

**Ask per capability, and skip-and-receipt what is absent.** Resolve mail through the seam (`tool_discovery.discover_for_category("email", "search", tools, declared=connector_config.declared_backend("email"))`) and chat through `chat_seam.resolve_chat_provider`. A capability that is not present is not an error and not a silence: pass the plain-English reason as `fetch_blocked=` so the leg records a BLOCKED run (which never advances a cursor), and carry the reason into Phase 5's `connector_gaps`. This build reaches for **email, calendar and chat** and nothing else; CONN1/CONN2 add Drive and DocuSign later by adding rows to this list, not by redesigning the fire.

**THE SENT FETCH FLOOR (SPEC MAILFLOOR1) — how far back the mail leg reads, in the same three branches as `skills/reconcile-sent/SKILL.md` Step 1.** "The Sent batch you just fetched" below is not a batch of today: choose the floor by mode, and the modes are these three and no others. **First real run on this workspace** — detected as `reconcile_sent_commitments.validate_reconcile_ran(WORKSPACE_ROOT)["ran"] is False` (import it alongside `reconcile_and_receipt` below; this detection runs BEFORE the fetch, so it cannot wait for that block), i.e. no prior `sent_reconcile` audit event exists — fetch the **last 30 days** regardless of the cursor. **Manual catch-up phrasing** ("catch up my sent mail" / "reconcile the last N days" / "reconcile my backlog") — fetch the requested window, default **30 days**, regardless of the cursor. **Otherwise**, a prior audit event exists, so fetch with the `{"in_sent": true, "after": <cursor date>}` intent — the date read from `workspace.sent_reconcile_cursor` — with a **~1-day overlap** behind the cursor, cheap and idempotent and it catches near-cursor stragglers. The intent is compiled per provider by `connector_adapters/mail.py`, exactly as today; never name a provider operator here. **An over-wide window is always safe, which is why you never narrow this one:** matching is idempotent and the cursor never moves backwards, so a wide re-scan closes nothing twice. A window narrower than the gap is the failure that has no such symmetry — `reconcile_and_receipt` advances the cursor to the newest message in whatever batch it was handed, so a fire that fetches only today after a four-day gap strands every send inside those four days permanently, behind a cursor that now claims to have reconciled past them and a green receipt on the record (Bug #101). **This paragraph and `skills/reconcile-sent/SKILL.md` Step 1 are twins:** editing either window here is editing it there, in the same edit, and a test pins the pair equal.

**The chat leg's window is not composed by hand either.** It comes from `chat_reconcile.backfill_floor(WORKSPACE_ROOT)` — the stored chat cursor, or a short fixed backfill on a first-ever run — and nothing else: never a span you compose, never a bound you add to "help", never full history (passive whole-history ingestion is the named anti-goal). It is already correct in code; naming it here is what stops a future edit from improvising one.

```python
# (Inside python3, after the Rule 22 preamble + sys.path.insert)
from reconcile_sent_commitments import reconcile_and_receipt
from chat_reconcile import reconcile_chat_and_receipt

mail = reconcile_and_receipt(
    WORKSPACE_ROOT, sent_messages,           # the Sent batch you just fetched
    user_person_id=USER_PERSON_ID,
    source_skill="past-meetings",
    fired_via=lateness["receipt_fired_via"],
    provider=<the resolved mail provider tag, never a literal>,
    exclude_captured_since=fire_start,       # RECONFENCE
    fetch_blocked=<plain-English reason, or None>,
)
chat = reconcile_chat_and_receipt(
    WORKSPACE_ROOT, chat_messages,
    user_person_id=USER_PERSON_ID,
    source_skill="past-meetings",
    fired_via=lateness["receipt_fired_via"],
    exclude_captured_since=fire_start,
    fetch_blocked=<plain-English reason, or None>,
)
close_result = {"mail": mail, "chat": chat}
```

**⛔ MANDATORY (SPEC EODLEG1) — record `close_leg_end` (UTC ISO) now, immediately after `close_result` is built.** This closes the window `close_leg_start` opened above; carry both onto Phase 5.

**Every close this fire writes carries its pointer.** `reconcile_and_receipt` and `reconcile_chat_and_receipt` already close through `commitment_state.close_commitment` with the sent message's artifact key / the chat pointer; any close YOU write in this fire (a tap the user applies later in the turn — **never a transcript close: since CUT-A (2026-09-06) closing on evidence is off by default and this fire writes none**) passes `source_ref=` the same way — `granola:<meeting_id>` for a transcript close, and for a human one **the ref the resolver RETURNED**: `resolve_choice(...)["source_ref"]` for a numbered tap, `resolve_intent_confirm(...)["source_ref"]` for the tomorrow confirm. Never compose that string yourself and never reuse one across two gestures: a pointer that is the same for every act of an evening resolves to nothing while still counting as "has a pointer" in the coverage metric. A close with nothing to point at still lands — since SPEC PROVMINT1 the writer mints `session:past-meetings:<now>` for it and marks the ref as surface-minted, so it points at the act rather than at nothing. That floor is not a licence: a close that silently drops a pointer it HAD is still the defect (SPEC PROV1), and it now shows up as a surface-minted row in the coverage split instead of hiding inside one flattering percentage.

**THE SOFTEN FLOOR.** If NEITHER leg advanced its cursor, the score and slipped blocks soften and the surface says so in exactly one line. `end_of_day.soften_floor(close_result)` returns that line; do not compose one, and do not suppress it because the numbers "look right". A score computed over a stale mail cursor understates the closes and overstates the slips, and the CEO is the person who would be blamed for the difference. A leg SKIPPED for want of a connector does not soften on its own — a workspace with no chat backend is not a workspace whose chat is behind.

# Phase B — RECONCILE: resolve what changed (SPEC EOD1 §2.2)

Same-day resolution of people, deal stages and identities, receipted. **This is the existing maintenance machinery run here, not new logic** — `identity_reconcile`, `deal_signal_detector` and the contact/person passes keep their own propose-vs-apply postures exactly as the Sunday slot runs them, and nothing here promotes a proposal. Anything they propose lands in the queue the Phase C `confirm` block reads.

If the maintenance dispatcher has already served today's slots, believe it: `maintenance_dispatcher.due_jobs(WORKSPACE_ROOT)` is the ledger and a job it does not return is a job that already ran. Never re-derive dueness in prose.

# Phase C — READ: build the End of Day pack (the ONE driver, t3 FB-9)

```bash
python3 shared/scripts/surface_drivers.py end-of-day \
    --workspace "<WORKSPACE>" --mode <scheduled|manual per Phase 2.9's run mode> \
    --close-json "<a temp file holding close_result from Phase A>" \
    --calendar-json "<a temp file holding the wide now->+3d calendar fetch>" \
    --gaps-json "<a temp file holding this fire's connector_gaps>" \
    --lateness-json "<a temp file holding Phase 2.9's check_lateness return>"
```

The driver prints exactly ONE line: `CR-EOD-PACK: {json}`. That is the whole contract. Run it ONCE per fire — a re-run to "refresh" is the RV-3 double-render class.

Omit `--calendar-json` when no calendar capability is present. The `tomorrow` block then renders its intent half and `calendar_available` comes back False; record the gap in Phase 5's `connector_gaps` and say nothing about connectors in chat beyond the coverage strip's own line.

**`--gaps-json` carries the SAME `connector_gaps` list Phase 5 receipts** — one per skipped capability, `{"capability": "mail"|"chat"|"calendar", "reason": "<the plain-English reason you passed as fetch_blocked>"}`. Pass it: the coverage strip renders those reasons, and without them a skipped leg renders as *"not read, and nothing on the record says why"*, which is honest and worse. This is the same list, written once and used twice — never a second list composed for the strip.

**`--lateness-json` is Phase 2.9's return, VERBATIM.** Do not edit it, do not re-key it, do not recompute lateness. On the degrade tier the pack composes the catch-up label from it; on every other tier `pack["catchup"]["renders"]` is False and there is nothing to place.

**THE SCREEN IS `pack["screen"]["text"]`, VERBATIM (CUT-PLATE, 2026-09-06 — M's hold: no decisions block and no question in the day-close; the evening reads the day in the PLATE's shape).** `end_of_day.compose_screen` composed every line this fire may post, in `end_of_day.SCREEN_ORDER`: `catchup` (degrade tier only) · **`plate` FIRST** — *"Your plate today — N opened · N closed · N slipped"*, the rows in their blocks, one pointer · `day_went` · `what_it_meant` · `worth_remembering` · `slipped_prose` · `echoes` · `coach` (the delta only) · `tomorrow` (a STATED intent as fact, never the proposal) · `sign_off` · then the health lines LAST — `coverage` (only when `end_of_day.coverage_has_disclosure(pack)` is True, SPEC COVERQUIET1) · `alarm_lines` · `dark_surface_lines`. Print that text as given; add nothing, re-order nothing, drop nothing. `render_order` (`end_of_day.RENDER_ORDER`) still rides the pack as the receipt's vocabulary and is unchanged; the placement that used to be a list in this paragraph is code now, and the composer RAISES on a retired sentence ("survived N closes", "consecutive close", "did not move", "what is tomorrow about") or an asking line, so the v5.28.0 shape cannot come back through prose. A turn that stops without posting the screen is INVALID, not "done early". Monday's `week_rollup` renders AFTER the screen, as before (see the Monday bullet).

# ⛔ SPEC EODSYNTH1 — THE EVENING SYNTHESIZES THE DAY (M's ruling, 2026-08-23) — AND, SINCE CUT-PLATE (M's hold, 2026-09-06), ASKS NOTHING

**"I don't think we should score it. I think we should synthesize how the day went."** Four things follow and none is optional. `end_of_day.COMPUTED_ONLY` is the pack's own list of what still runs and renders nowhere; `render_order` is what does render.

**R-1 — THE SCORE IS NOT RENDERED.** `n_closed` / `n_planned`, *"0 of 5 closed"*, *"no net change"*, the ledger's book-at-open arithmetic — none of it reaches the chat and none of it reaches the widget. The FIELDS are still computed and still land on the `pack_run` receipt, because weekly-recap and the trend surfaces read them: **un-render, don't unbuild.** The pack still carries `score` and `score["ledger"]`; you place NEITHER. `eod_synthesis.assert_no_score` is a code fence over the composed text and it RAISES — if you find yourself wanting to say a number about how much of the plan got done, the answer is that this surface no longer says one.

**R-2 — SUPERSEDED (CUT-PLATE, M's hold 2026-09-06): THE DAY-CLOSE ASKS NOTHING.** The tomorrow draft — up to three ranked CANDIDATES (SPEC TOMPICK1) — is still computed and still lands on the receipt as `day_intent_proposal`, but it is NEVER rendered and never asked: no card, no Confirm / Edit, no widget. A STATED intent (the CEO said `tomorrow is about [X]`, workspace-manager / BK1) renders as fact through `end_of_day.TOMORROW_STATED_LINE`; with nothing stated, `tomorrow["line"]` (*"Nothing on file yet for tomorrow."*) is the whole of it. Everything on this surface is READ-ONLY. `pack["confirm_ids"]` is EMPTY by construction (`end_of_day.NUMBERED_BLOCKS` is `()`), so there is nothing numbered to tap and a `[n]` tap is refused in plain English. `resolve_intent_confirm` stays as the resolver for the receipt's data and the on-demand path; this fire offers it nothing to confirm.

**R-3 — THE CONFIRM/DROP QUEUES LEFT THE EVENING.** The slipped rows' *"Done, new date, or drop?"* fork, the needs-your-call rows and the person candidates render on the MORNING surfaces (the morning brief's needs-attention lane, the `needs-your-call` and `my-plate` chats). They are still COMPUTED here — `pack["slipped"]`, `pack["confirm"]` — because the receipt and the synthesis read them; you place none of them and you offer no verb on any of them. **The OVERDUE1 ask-once marker is unchanged; the morning performs it** (`end_of_day.mark_lane_asked`, orchestrator-morning-brief Phase 6.1), so this file's Phase 6.3 no longer asks. Net asks per day must not go UP — that is the thing M counts on the walk.

**R-5 — GROUNDING IS THE BUILD.** Every synthesized sentence carries the rows it came from, and `eod_synthesis.drop_unreferenced` removes any sentence whose ref set is empty BEFORE it can be composed. You do not write these sentences: `eod_synthesis.build_synthesis` composed them in Phase C and the pack carries them as text. **Print what you were handed and add nothing.** A sentence you compose here has no refs, is in no record, and is exactly the freelance narration this build exists to remove.

Binding notes the pack does not enforce for you:

- **coverage — RENDERS ONLY WHEN IT HAS SOMETHING TO DISCLOSE (SPEC COVERQUIET1, superseding EODLEDGER1 part 1's "always render").** `end_of_day.coverage_has_disclosure(pack)` is THE gate — a reduction clause, a deferral (`window_incomplete_before` set), a TASKALARM1 dark-surface line, a `connector_gaps` entry, or a rendering catch-up/degrade note (`catchup["renders"]`). A day with NONE of those is genuinely quiet — no boilerplate "5 meetings on record, 4 processed, all current" — and the briefs section speaks for itself. **The receipt still keeps the full record either way** (`log_end_of_day_receipt` moves `coverage` to `blocks_computed_only` on a quiet day rather than dropping it — this is bookkeeping, not a second render decision you have to make; do not call the gate again for the receipt, only for whether you PLACE the block below). See Phase 5 for where the gate is evaluated and cached onto `pack["coverage_disclosed"]`.

  **WHEN IT RENDERS: `end_of_day.coverage_render_lines(pack)`, PRINTED VERBATIM, FIRST, UNDER THE ALARMS.** Never `coverage["lines"]` directly — `coverage_render_lines` is the disclosure-first composition (§0 ruling 3): its first line names WHAT is being disclosed, with the count scoped to that one clause ("1 meeting deferred to tonight's pass" — never "5 meetings on record, 4 processed, 1 deferred"), followed by every per-capability line `compute_coverage` / the Phase 5 reconcile already composed, unchanged. Mail and chat through their own cursors — **and when a cursor is behind, the line names the span** ("read through Friday, July 24 — 5 days behind"), which is the sentence that did not exist while M's chat cursor sat five days stale and the surface reported the day's closes with no qualification at all. Calendar present or absent. The capture leg's window, with what is on record in it and what is still owed — **as reconciled in Phase 5 (SPEC MEETCOUNT1)**: the meetings line derives from the SAME `meeting_render_set` the Meeting briefs section renders from, with every reduction named in the sentence. Printing the pre-reconcile Phase-C aperture line next to rendered briefs is the two-producer divergence MEETCOUNT1 removes.

  **ONCE IT IS RENDERING, NEVER SUPPRESS A LINE AND NEVER SOFTEN ONE.** Same posture as `alarm_lines`, same reason: a degraded read is exactly when the reader most needs to know what the aperture was, and COVERQUIET1's day-level on/off switch does not license per-line editing underneath it — the gate is ALL-OR-NOTHING. Do not re-word a line to sound better, do not drop the stale-cursor clause because the numbers "look right", and do not add a reassuring sentence of your own after it.

  **"Not read" and "nothing there" are different claims and the strip is where they are separated.** A calendar outage and a genuinely empty tomorrow rendered identically before this. `coverage["capabilities"]["calendar"]["read"]` and `tomorrow["calendar_available"]` are ONE boolean by construction — they cannot disagree, so never write a sentence that puts them in conflict.

  The strip's last line, when present, is the data-quality note: the COUNT of closes in this window that cite no artifact anyone can open. A count, not a section, and not a thing to apologise for or explain away. It is NOT on the §0.1 disclosure list by itself — an unsourced count on an otherwise quiet day does not, alone, put the strip up.
- **score / score.ledger / score.first_move — COMPUTED, RENDERED NOWHERE (SPEC EODSYNTH1 R-1).** All three still arrive on the pack and all three still land on the receipt; you place none of them. There is no *"No plan on record this morning"* line on this surface any more, no *"Open book: 41 this morning…"*, and no *"This morning's first move was X"*. The reason the fields survive is that the surfaces that legitimately grade — weekly-recap, the trend reads, the Monday roll-up — read them off the receipt. The reason the SENTENCES do not is M's ruling: the score anchors on the morning plan, so a day that drifted from its 7 AM plan scored as a failure regardless of what actually got done, and the grade sat next to three closed wins reading as a contradiction. What replaces it is the paragraph below, which is about the day rather than about the plan.
- **wins — COMPUTED, RENDERED NOWHERE.** The named closes feed `day_went`; they are no longer a block of their own. Do not print `wins["rows"]`, `wins["line"]` or `wins["more_line"]`.
- **slipped / confirm — COMPUTED, RENDERED NOWHERE (R-3).** They feed `slipped_prose` and the MORNING surfaces respectively. No Slipped section, no Needs-your-call section, no person-candidate section, no `more_line`, no `resting_line`, and no verbs on any of it — in the prose or in the widget. **A verb offered here is a dead button:** `confirm_ids` is empty, so nothing resolves.
- **plate — RENDERS FIRST (SPEC PLATE1 night 2, D7 `eod`; CUT-PLATE 2026-09-06).** `pack["plate"]` is the day's delta in the plate's shape (opened / closed / slipped over the ledger's own window, one pointer) — the same model and words as `what's on my plate` and the morning brief. It is the first block of `pack["screen"]` — the rows are read-only (no verbs, nothing numbered, R-3 stands for the morning's queues) and a close a later `undo` reversed is not counted closed. It is still NOT a member of `render_order` (byte-pinned); the screen composer places it. `pack["confirm"]` ranks on the plate's evidence (`data.proposal`, P7) — computed only.

  **THE PERSON-CANDIDATE ROWS, NAMED EXPLICITLY, because this file is what the 5 PM fire executes (SPEC PERSONLOOP1 review N-1).** `pack["confirm"]["person_rows"]` is still built for you — by `end_of_day.compute_person_candidates`, which reads `person_candidates.derive_candidates` — and you render **no section at all** for it. That is not the drop-empty rule doing its usual work on an empty day; it is unconditional on this surface since EODSYNTH1. The reason the rows are still computed is that the person-loop's measurement rides the receipt (`person_candidate_counts`), and the receipt now reports `n_shown: 0` honestly rather than claiming a render that did not happen — which was exactly the N-1 defect, arriving through the other door. **Where they DO reach the reader:** the `needs-your-call` chat and `my plate`, both of which derive them from the same builder. You derive nothing here and you render nothing here.
- **day_went — ONE GROUNDED PARAGRAPH, PRINTED VERBATIM.** `pack["day_went"]["text"]` is composed in code from the ledger's own fields and today's named closes, and it is the surface's lead. Print it as given. **Do not extend it, do not add a clause, and do not "improve" a sentence** — every sentence in it carries a ref list in `pack["day_went"]["sentences"]`, and a clause you add carries none, which makes the whole paragraph unfalsifiable. Empty text (a day with nothing to say) → print nothing; never pad an all-clear.

  **THE PARAGRAPH READS THE SAME FLOORED WINDOW THE WINS BLOCK ALWAYS DID (SPEC WINSFLOOR1, still in force).** `pack["window"]["wins"]` and `pack["window"]["closures"]` carry the `window_source` value that says which one: **`morning_anchor`** when the day's morning brief fired — the window opens at that brief — and **`day_floor`** when it did not, in which case it opens at workspace-LOCAL **midnight** of this fire's own day and never earlier. This is not bookkeeping: unfloored, that read returned 2,334 rows on the live workspace on 2026-08-19 and reported them as what moved today. **The paragraph inherits the floor because it inherits the rows** — so a day with no morning brief still gets a paragraph, and the paragraph is about the day from midnight. The spellings the wins block used to print — *"…more moved today"* on the anchor path, *"…more moved since midnight"* on the floored one — are not rendered any more (that block is computed-only), and the paragraph never claims a window in words: it says what moved, and the window it read is on the receipt where a reader can check it. **Never describe the floored day as having no wins, and never describe it as a full day's history.**
- **what_it_meant — THE ARC READ (SPEC EODARC1), PRINTED VERBATIM.** `pack["what_it_meant"]["text"]` answers, in order: which arcs moved today (grounded in what happened), which consequence-carrying arcs **did not move** (what is waiting, and on whom), and where the day's weight went. The arcs are DECLARED only — an objective, the day's stated intent, an org relationship with a live thread, an active workstream, a deal with a stage, a consequence-carrying open commitment no other arc tracks. **A recap lists what changed; a synthesis says what it means for what you are running** — and the fence between the two is code: `eod_synthesis.drop_rows_only` drops any sentence with no arc attached before it can compose, so this block can never be a row-list wearing a heading, and you must never add one back by enumerating rows yourself. A genuinely empty day renders its one honest line ("Nothing on today's record moved a standing arc.") — print it as given, never pad it and never replace it with a theme of your own. **An arc the model infers is not an arc**, and this is the block where inventing one would read as insight. **Ruling 3: deals are read as context, never as state** — the arc read does not depend on deal rows existing or being current, and you write nothing to deal state from this surface, ever. Prose only: zero new actions, buttons, or proposals in this section; the day-close asks nothing (CUT-PLATE) — a stated tomorrow prints as fact inside `pack["screen"]["text"]`.
- **worth_remembering — 1 TO 4 LINES, EACH ONE A ROW.** `pack["worth_remembering"]["lines"]`, printed verbatim, in order. Each line IS a decision or note logged today, not a summary of one, so there is nothing here to rewrite. Empty → no section.
- **slipped_prose — PROSE, AND ONLY THE SLIPS WITH A STATED CONSEQUENCE.** `pack["slipped_prose"]["text"]`, verbatim. It names only items whose slip has a downstream effect stated on the row — a meeting it gates, a person waiting, a date it was owed by. **Everything else that slipped is SILENT here and appears in the morning.** There is no *"137 slipped"* header on this surface and no denominator: `n_silent` is a number on the receipt, not a line on the screen. This section obeys the workspace's own on/off decision — `end_of_day.slipped_prose_enabled(config)`, which reads the migrated `slipped_prose_section` key and falls back to the pre-rename `slipped_section` so an owner who turned it off still has it off.
- **echoes — AT MOST TWO, LABELLED, EACH CITING A PRECEDENT BY ID.** `pack["echoes"]["text"]`, verbatim, and normally EMPTY — absent is the default and a day with no genuine precedent match renders nothing here. Never write one yourself: `eod_synthesis.make_echo` refuses an echo with no precedent id and refuses the banned phrasings outright (*"momentum is building"* and its siblings), and a sentence you compose bypasses both refusals. The form is *"this resembles X, which went Y"*, always labelled as a reading across the record and never as a fact.
- **coach (SPEC EODCOACH2; CUT-PLATE) — THE DELTA ONLY, inside the screen above `tomorrow`.** `pack["coach"]["text"]` is now the intent-vs-outcome delta alone ("You said tomorrow was about X. It didn't move." or the honest "…it shipped."), placed by `compose_screen`. Layer 1's counted patterns ("That's the Nth consecutive close where X sat still.", the recurring-mention line, "X has now survived N closes.") and the push line are still computed, deduped and persisted (`coach["patterns"]`, `coach["push"]`, `coach["layer1"]`, `push_state`) and are NOT printed — the v5.28.0 attended test saw them on the day-close and M ruled for the plate shape with less on the card. **Never compose one yourself** and never print the persisted Layer 1 text: `eod_coach.build_coach` reads the last 7 packs off THIS workspace's own disk and the day's own STATED `day_intent`; no stated intent means the delta renders nothing, honestly.

- **tomorrow.** `intent` is the CEO's own stated record (BK1) — the screen renders it as fact (`end_of_day.TOMORROW_STATED_LINE`). `proposal` is a DRAFT the system guessed: since CUT-PLATE it is NEVER rendered — not as a question, not as a card, not as a statement — and this fire offers no tap on it. It stays on the receipt as data (Phase 6.2).
- **sign_off.** Print `line` verbatim. It is computed; there is nothing to write here.
- **Monday** additionally carries `week_rollup` — render it AFTER the day-close blocks. It also carries `development_read`, whose `renders` is False: render NOTHING for it. No heading, no placeholder, no "coming soon". The slot fills when DEVREAD1 ships.

  **⛔ THE ROLL-UP HAS THREE ROW SHAPES AND EVERY ONE OF THEM IS RENDERED FROM WHAT THE ROW SAYS. Never a zero, never a number you supply.** Each row carries `recorded` (did the evening chat run that day) and `counted` (did that fire record a score). Read both:

  | `recorded` | `counted` | Render |
  |---|---|---|
  | False | False | the row's `line` verbatim — *"no close was recorded"*. The chat did not run |
  | **True** | **False** | the row's `line` verbatim — *"the day ran before this count existed"*. **This is the shape the FIRST MONDAY after this upgrade produces for the whole prior week**, and it is not an error |
  | True | True | `n_closed` of `n_planned` |

  **The middle row is the one that will bite.** This fire serves a taskId that has existed for many releases, so last week's receipts are pre-EOD1: real fires that ran and predate the score. They come back with `n_closed: None` and their own `line`. **Render the line.** Do NOT render `0`, do NOT render a dash, do NOT leave the row blank, do NOT skip the row, and do NOT summarize the week as "a slow week" or "nothing closed" — none of those is a thing the record says. `week_rollup.n_days_uncounted` is the honest count of that shape; a week where it equals 5 is a week that predates the counting, and saying so in one line is the correct read of it.

  The three claims are genuinely different and only one of them is ever about the CEO: zero says the day closed nothing; *"no close was recorded"* says the chat did not run; *"the day ran before this count existed"* says the chat ran and the count did not exist yet. Collapsing any of them into a number is the fabricated-zero failure this surface exists to not commit.
- **Friday** is a plain day-close. **No hand-off line to the Friday Wrap** (Conflict D, M's ruling): the weekly surface owns that moment and two surfaces reaching for it is how the CEO gets handed the same week twice.

**Timezone.** The fire's own day is `end_of_day.workspace_today`, resolved workspace-LOCAL through `tz.py`. Never the machine clock and never UTC: at 9 PM Pacific the UTC calendar has already rolled over, which is the window this fire runs in, so a UTC-derived "today" would score the wrong day and file tomorrow's intent under the day after. Every rendered connector timestamp goes through `to_local(value, workspace_path=<WORKSPACE>)` as it always has.

**Personification.** The intro line is `"Evening, {first_name} — {brain_name} closing out your day."` from `personification.get_brain_name(WORKSPACE)`, and it is the ONLY greeting permitted — and it renders ONLY if the persona block permits: when the workspace CLAUDE.md carries the persona block (`## How {brain_name} talks to …`) and it says skip pleasantries, or its Never-line forbids greeting openers, OMIT the intro line and open with the day's substance (STYLE1 D6 — the persona outranks this shape). One name in the intro (when it renders), one at the sign-off, nowhere else; the sign-off signature stays either way.

**Writer wall.** Personal-account items may RENDER (the R9 ephemeral rule) and write nothing: no entity write, no commitment write, no capture from a personal-account read.

**FRP1 first run.** On the first fire only (`not is_configured(WORKSPACE, "end-of-day")`), `save_skill_config(WORKSPACE, "end-of-day", held_tier.CONFIG_DEFAULTS)` before rendering, then append the one-time footer offering the three decisions: tone (scoreboard or journal), the slipped section on or off, the sign-off on or off. Renders exactly once ever.

**HOLD the pack.** Do not post yet. Phase D runs next, then Phase 5 writes the receipt, then Phase 6 posts what you built here. The pack you post is the pack you built BEFORE capture ran — that is the fence, and re-deriving any block after Phase D defeats it.

# Phase D — CAPTURE, last and fenced (SPEC EOD1 §2.4)

**⛔ MANDATORY (SPEC EODLEG1) — record `capture_leg_start` (UTC ISO) NOW, before Phase 3 below runs.** Everything from here to the end of Phase 4.8 is `end_of_day.PHASE_CAPTURE`, timed the same wall-clock way `close_leg` is (see Phase A): a UTC-ISO delta, never a monotonic timer, because this leg spans its own `python3 -c` processes. Record `capture_leg_end` (UTC ISO) at the END of Phase 4.8, before Phase 5 begins — unconditionally, whether or not any meeting was actually found in the window: Phase D always runs, and a leg that ran and did no work still has a wall time worth recording. Both timestamps carry onto Phase 5.

Everything from Phase 3 to Phase 4.8 below IS this phase, unchanged in what it writes and unchanged in its doctrine. Two things about it are new:

1. **It runs HERE — after the read is built.** Its writes must not reach the surface this fire already computed.
2. **It posts NOTHING of its own.** The pre-EOD1 fire ended with a widget listing every processed meeting and its pending sub-items; that is the "pile" M's ruling removes. The meetings contribute their `.docx` links to Phase 6's Links section and their ambiguous items to the queue the `confirm` block reads on the NEXT fire. Nothing about what each gate did changes; only the pile is gone.

**⛔ THE CAPTURE ROUTING FLIP IS DARK.** `meeting_capture.route_meeting_captures` keeps its four outputs and its ruling: a below-floor capture routes to REVIEW, never silently dropped, never deleted. `held_tier.apply_held_routing(routed, WORKSPACE)` is applied to the return, and with the flip OFF — which is every workspace today — it hands the routing back UNCHANGED with an empty held lane.

```python
from held_tier import apply_held_routing, appendable, held_ids
routed = apply_held_routing(route_meeting_captures(...), WORKSPACE_ROOT)
append_event(EVENTS_PATH, appendable(routed), holder='past-meetings.commitments')
```

`appendable(routed)` is `book + review + observed + HELD`, and held rows are in it deliberately: "out of sight" is a property of the surfaces, not of the disk, and a held capture the fire never wrote could not be retrieved on request.

**Enabling the flip is an operator action and it is FENCED.** `held_tier.enable_held_routing` calls `held_tier.capability_status(...)` — which asks `operator_capability.capability_status("held_tier_routing")` — and REFUSES until that capability has been granted in the plugin payload. It is not a measurement this workspace takes of itself and there is nothing here that can earn it. **This fire never enables it**, never writes the config value to work around the refusal, and never narrates the fence to the user. A fire that flips it has decided the thing the review exists to decide.

# Phase 3 — Find unprocessed meetings (everything since the last successful run) — Phase D, step 1

**Project status note (v2.10.3+):** Past Meetings processes meetings regardless of project status (active / dormant / archived). If a meeting routes to a dormant project, the processing still runs AND the project auto-revives per ORG_AND_THREAD_MODEL.md re-active detection — a meeting just happened, the project is no longer dormant.

**Window (SPEC CATCHUP1 F-1) — compute it, never assume 24 hours.** The pre-CATCHUP1 window was a literal "last 24 hours", measured from `now`. A machine closed Monday through Wednesday meant Thursday's fire saw Wednesday→Thursday only: Monday's and Tuesday's meetings were never processed at all — no notes, no commitments, no follow-ups, and nothing said so. The window is the span since this task's last SUCCESSFUL run, floored at the nominal 24 hours and ceilinged at 30 days:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from catchup import catchup_window
print(json.dumps(catchup_window('<workspace_root>', 'past-meetings', floor_hours=24, cap_days=30)))
"
```

Use the returned **`start_aware` and `end_aware`** as the Granola query bounds — verbatim, never re-derived in prose. Those are the same two instants as `start` / `end` carrying the machine's UTC offset, and an offset-carrying timestamp is the only unambiguous thing to hand a connector (SPEC CATCHUP1 F-1; the naive pair is machine-local, which is right for receipt math and wrong at a connector boundary — see `shared/scripts/catchup.py`'s connector-boundary note). `extended: true` means this fire is doing catch-up work; that is the ONE flag you need, and it changes nothing about how meetings are processed (every phase below runs identically). `capped: true` means the gap was longer than 30 days and the span was truncated at the ceiling. Neither is ever narrated to the user; the catch-up is silent, and the meetings simply get processed. If the helper errors it returns the plain 24-hour window with `error` set — proceed on that; catch-up must never block the fire.

Catch-up applies on EVERY fire here, scheduled or manual, because re-processing is idempotent (the `meeting_processed` / `meeting_skipped` gate below) and catching the backlog up is the point of a manual re-run too.

Call Granola MCP for meetings in `[start_aware, end_aware]`. For each:
- **A RECORD IS NOT A RECEIPT.** Whether a meeting is already processed is decided by `meeting_capture.already_processed` — a `meeting_processed` receipt on disk — and never by the existence of a bare `meeting` event. The two used to disagree: the discovery leg counted a record as processed while the process-the-last-call path required a receipt, so a meeting whose receipt landed a day after its record was invisible to this fire AND unprocessed to the manual path at the same moment (records seq 9462/9468, receipts 9680/9681). A `meeting_skipped` event DOES retire a meeting — a deliberate exclusion is handled, not owed.
- Filter out personal calls (no business attendees, single 1:1 with non-business contact).
- Filter out internal-only meetings UNLESS they have decisions worth committing.

**⛔ THE CATCH-UP SWEEP (SPEC EODFIX1 §2-4) — run it, never re-derive it.** The window above bounds the CONNECTOR fetch. It does not bound the backlog: a meeting recorded three days ago whose receipt never landed is still owed, and no window that starts at the last successful run will ever find it again. So the fire additionally sweeps every meeting that has a record and no receipt, inside CATCHUP1's 30-day ceiling:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from meeting_discovery import unprocessed_backlog
print(json.dumps(unprocessed_backlog('<workspace_root>', limit=5)))
"
```

- The returned `rows` are OLDEST FIRST and carry refs and instants only — never a title, the same discipline every meeting receipt keeps. **They enter at the SAME DOOR every other meeting enters, and that door is Phase 3.5, not Phase 4.** Fetch each row's meeting from the declared transcript backend by its `source_ref`, then hand it to Phase 3.5's `dedup_meetings` with the rest of this fire's records and route on the verdict it returns: `attended` → Phase 4, `non_attendee` → Phase 4.8's lane, `skip_duplicate` / `skip_processed` → neither. "There is no second pipeline" means exactly that — a backlog row is not evidence the user was in the room, and a sweep that walked its rows straight into Phase 4 would put other people's meetings on the user's book at scale, which is the one thing Phase 4.8 exists to prevent.
- The sweep returns TRANSCRIPT refs only. A `meeting` record whose ref names another namespace (a calendar id, a workspace-ingest pointer) is a real record with nothing for Phase 4 step 1 to fetch, so `unprocessed_backlog` never hands you one; `n_no_transcript` counts them, because an exclusion is a number and not a silence. If that count is persistently non-zero on a workspace whose transcript backend writes a different ref namespace, the prefix is what needs overriding — never the filter.
- `n_backlog_remaining` is the honest counter. When it is non-zero the fire did NOT drain the backlog, and Phase 5's receipt must carry `window_incomplete_before` computed from `oldest_unhandled` (`catchup.receipt_window_marker(window, incomplete=True, oldest_unhandled=<the sweep's oldest_unhandled>)`). A receipt that omits the marker while a backlog is outstanding is the orphaning bug with a green receipt on it.
- `start_naive: true` on a row means the stored start carried no offset, so the sweep read it BOTH ways and swept it if either landed in the window. That bias is deliberate: a re-swept meeting costs one dedup hit, a stranded one costs the meeting.
- Never hand-roll this set from your own reading of events.jsonl.

Up to 5 unprocessed meetings to process this fire.

**⛔ BATCH-CAP HONESTY GATE (SPEC CATCHUP1 F-1) — MANDATORY.** That cap of 5 and a widened window are a trap together: a window that finds 12 unprocessed meetings processes 5, the fire's receipt lands, and the NEXT window starts after that receipt — silently orphaning the other 7 forever, with a green receipt on the record. So the receipt has to record how far processing ACTUALLY reached, not when the fire happened:

- Sort the unprocessed set OLDEST FIRST and process from the oldest end. The backlog drains in order; the newest meetings are the ones the next fire re-finds, and the next fire is hours away.
- If ANY meeting inside `[start, end]` is left unprocessed when the fire ends — batch cap hit, transcript fetch failed, Granola timed out mid-batch — Phase 5's receipt MUST carry `window_incomplete_before: "<ISO start time of the OLDEST still-unprocessed meeting>"`. `catchup_window` resumes from that value instead of the receipt time, so nothing is stranded. **Compute the value, never hand-write it:** `from catchup import receipt_window_marker; marker = receipt_window_marker(window, incomplete=True, oldest_unhandled=<the oldest still-unprocessed meeting's start>)` — it clamps into the window and returns `None` when the fire drained it, meaning omit the key. This surface CAN name `oldest_unhandled` because its cap truncates a chronologically sorted list, so the marker advances as the backlog drains; `weekly-recap`'s cannot, and passes None (its own gate explains why).
- A meeting deliberately excluded (personal, internal-only, `meeting_skipped`) is NOT unprocessed — it is handled. Only meetings this fire still owes work for count.
- **Carry the marker forward.** If this fire processed nothing (Granola unavailable, zero capacity) and the previous receipt already carried a `window_incomplete_before`, the receipt this fire writes carries the SAME value. A receipt without the marker means "everything before this point is handled" — writing one while a backlog is outstanding is the orphaning bug, restated.
- Only when the fire drained its entire window does the receipt omit the field.
- **A second, TIME-based trigger can also leave meetings unhandled (SPEC CAPFENCE1, 2026-08-27).** The count cap above is not the only reason a meeting inside `[start, end]` might go unprocessed this fire — the capture leg's own 15-minute stopping rule (`end_of_day.CAPTURE_FENCE_MS`) can stop it earlier, between meetings, subject to the substance floor (never zero briefed on a day that had meetings). See Phase 4's own CAPTURE FENCE block, right after step 9, for the check and the deferral. It writes the SAME `window_incomplete_before` this section describes, via the SAME `receipt_window_marker` call (wrapped as `end_of_day.capture_fence_window_marker`) — a fence-triggered defer is not a second kind of incompleteness, just a second reason for it, and the two never disagree on the resume point for one window.

## Phase 3.5 — Discovery + attendance classification + meeting-level dedup (SPEC GRANOLA1 §A/§A2)

**This is a STEP inside this same fire — there is NO new scheduled task, and this one is never re-registered** (re-registration is how the folder-rename defect class eats tasks).

Ask the declared transcript backend for the same window a SECOND way: with the involvement filters OMITTED, so the return includes meetings the user never joined (a teammate's client call, a shared workspace session). **The Phase 3 catch-up sweep's backlog rows are part of THIS set** — fetch each one's record by its `source_ref` and pass it in with the rest, so it is classified once, by the same code, alongside everything else. Then classify and dedup in code — never by eye:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from meeting_discovery import dedup_meetings, processed_index
prior = processed_index('<workspace_root>', include_bare_meetings=False)
print(json.dumps([{k: d[k] for k in ('source_ref','classification','classification_reason','certain','action','duplicate_of','note_owner')}
                  for d in dedup_meetings(<the raw meeting records from the backend>, processed=prior)]))
"
```

What comes back, and what you do with it:

- **`classification: attended`** (`captured_by_me` OR `listed_as_participant`) → the EXISTING pipeline, Phase 4 onward, completely unchanged. **Arriving shared is not evidence of absence** — many shared notes are of meetings the user did attend, and the classification reads the involvement flags, never how the note arrived. When the only note of an attended meeting is a teammate's, keep `note_owner` on the receipt so a transcript-quality caveat (someone else's capture settings) stays traceable.
- **`classification: non_attendee`** (neither flag, INCLUDING "the backend did not say") → Phase 4.8's lane, never Phase 4. Participant metadata is incomplete by the backend's own admission, so **uncertain is non_attendee**: nothing auto-enters the user's book from that lane, which makes the cheap direction the default one.
`include_bare_meetings=False` is load-bearing on THIS leg: a record is not a receipt, and the default (which the shadow lane keeps) would retire a meeting nothing has processed yet.

- **`action: skip_duplicate` / `skip_processed`** → do not process, and record `duplicate_of` on the receipt. Dedup is on the MEETING, not the document: one real meeting can produce several notes with different document ids (the user's own note plus a teammate's shared note), and document-id dedup alone double-captures every shared meeting. The helper keys on the calendar event id when the backend exposes one, else normalized title + start within a tolerance window + participant overlap — and a recurring series never dedups across occurrences.

Never hand-roll either verdict, and never re-derive the dedup key in prose: the fuzzy key is the thing that keeps a weekly recurring meeting from collapsing into one row, and it is tested (`tests/run_granola1_nonattendee_shadow_test.py`).

# Phase 4 — Per-meeting auto-processing

For each meeting:

1. **Fetch transcript** via Granola MCP.
2. **Run `meeting-notes` skill** silently. Extracts decisions, commitments (with owner/due/requester per shared/COMMITMENT_SCHEMA.md), action items, discussion topics.
3. **Run `follow-up-ritual` skill** silently. Drafts per-attendee follow-up emails (voice-calibrated). Lazy creation per EMAIL_DRAFT_PROTOCOL — TEXT only.
4. **Score each extracted item's `classification_confidence`** (existing scan-for-commitments / meeting-notes scoring) and pass it on the item — it is the ONE confidence field (ATTRIB1-A A2; there is no `data.confidence`):
   - HIGH: clear owner + clear date + entity in entities.json → ≥ 0.8
   - LOW: ambiguous owner, vague timeline, conflicting info, new entity not in entities.json, sensitive decisions (firing / pricing / contract terms) → below the surface floor (0.7 baked — `confidence.surface_min` per workspace)
   You do NOT set `pending_review`. The helper in step 5 derives it from `data.attribution` (owner / counterparty basis), the floor and fusion verdicts, and this number — a caller that passes the flag gets the derived value plus a `capture_contract_violation` note on the row. Pass `span` (the verbatim quote) beside `evidence` when you have it; the helper locates it in the step-1 transcript and records the turn it sits under.
5. **⛔ COMMITMENT ADMISSION GATE (CAPTUREFLOW 2026-08-01) — MANDATORY, and it is CODE now.** Every extracted COMMITMENT goes through `meeting_capture.route_meeting_captures` before anything is appended. Do NOT hand-build commitment dicts here, and do NOT write a commitment on your own confidence score — the helper decides which of four places each item goes, and it is the SAME helper `meeting-notes` calls (one admission path, two legs; see `skills/meeting-notes/SKILL.md` Step 5e for the shape of an `items` entry).

   ```bash
   SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
   python3 -c "
   import sys, json; sys.path.insert(0, 'shared/scripts')
   from meeting_capture import route_meeting_captures
   from event_gate import append_event
   routed = route_meeting_captures(
       <the extracted items for THIS meeting>,
       workspace_root='<workspace_root>',
       source_ref='granola:<meeting_id>',
       transcript_text='<the transcript already loaded in Phase 4 step 1 — never re-fetch>',
       meeting_date='<YYYY-MM-DD>',
       org_id='<the meeting resolved org id or None>',
       org_name='<the meeting resolved org name or None>',
       primary_thread_id='<resolved or None>',
       source_skill='past-meetings',
       attendee_records=<THIS meeting's attendee list from Phase 4 step 1, or None>,
       now_iso='<this fire UTC now, ISO>',
       meeting_person_ids=<the resolved attendee person ids you will stamp on the meeting event in step 8, or None>,  # ATTRIB1-B A5
   )
   append_event('<workspace_root>/_hq/data/events.jsonl',
                routed['book'] + routed['review'] + routed['observed'],
                holder='past-meetings.commitments')
   print(json.dumps(routed['summary']))
   print('\n'.join(routed['receipt_lines']))
   print(routed['transcript_class'])   # ATTRIB1-A — stamp it on the meeting event in step 8
   "
   ```

   **ATTRIB1-A (2026-09-02) — the transcript is classified first; the flag is derived.** The helper declares the transcript's class before any row is built (`named` / `me_them` / `unlabelled` / `dictation`), writes `data.attribution` `{transcript_class, owner_basis, counterparty_basis, span, turn}` on every row, `data.fusion_status` on every row and `data.floor_code` on every gated row, and derives `pending_review` from those — never from a literal you pass. A `dictation` transcript (`Me:` is the only voice — a working session) sends every capture to `observed` with `routed['summary']['working_session'] = True`: kept for prep, no open item, no question; only the caution rail (a due date or money) still opens a row. Carry `routed['transcript_class']` and `routed['summary']['working_session']` onto the `meeting` event in step 8, and render a working session as one line ("Working session — N notes kept, nothing opened"), never as "0 commitments".

   **ATTRIB1-B (2026-09-04) — the owner comes from the turn marker when the grammar agrees, the counterparty from the calendar, and when neither answers the row carries ONE question.** The helper reads your facts against the transcript and the roster: a first-person line under `Me:` is the user's (`owner_basis: speaker`), a second-person line under `Me:` ("you'll send…") belongs to the addressee (`inferred`), the other voice's own promise on a two-party call is that attendee's (`calendar`); the counterparty resolves calendar-2p → vocative → named speaker → meeting `person_ids` → ask, and the ask is `attribution.question = {kind: who_is_you, options, default}` — pass `meeting_person_ids` so the last rung can read. M RULING 2 (2026-09-03): `scheduling` and `agenda` rows ask NOTHING — they book silently with their basis on the record, and the calendar closer finishes them; the only question is the `who_is_you` one, on a `promise`, and only on the meeting card (at most three per meeting). Fences on the same pass: the user is never their own counterparty (stripped, `self_counterparty_stripped: true`); the user's own no-consequence item is an ASIDE → `observed`; advice / an unaccepted request / a declined conditional offer / reported third-party speech / dictation to a tool are each NAMED on the row as `data.speech_act` (EXTRACT1) — a label beside the shipped floor verdict, never instead of it, and never on a row that clears the floor. Quote verbatim, or pass `evidence_kind: "paraphrase"` when you cannot — the guardrail is then honestly inert on that row. DOOR 1: after this meeting's card, render its questions through `attribution_doors.render_card_questions('<workspace_root>', 'granola:<meeting_id>', persist_dir='<workspace_root>/_hq/.system/widgets')` as a second widget when it returns one (≤3 questions, likely answer first); no answer inside the review window and the likely answer is applied on its own, `undo` reverses it.

   **⛔ ATTENDEE1 (2026-08-20) — PASS `attendee_records`, RENDER `receipt_lines`, CARRY `n_auto_created`.** The attendee list you already hold for this meeting is the strongest identity evidence this rail ever sees, and it was being discarded. Pass it in whatever shape the backend gave it (dicts with name+email, the connector's own `"A Name from Org <a@x>, …"` block, or plain strings) — `attendee_evidence.normalize_attendee_records` handles all three and PRESERVES the name↔email pair, which the persisted `meeting` event cannot (it splits emails into `data.attendees` and names into `data.attendees_external`, two lists with no correspondence between them, so re-reading the substrate later recovers nothing).

   Effect: a capture whose counterparty or owner resolved to nobody, but whose name exact-normalizes onto an attendee OF THIS MEETING that carries an email, gets that person created and its id filled in before the pending stamp is evaluated — the row routes clean instead of joining the queue with a question the attendee list already answered. The bar admits nothing weaker: name-only attendee matches, one-token or annotated speaker labels, near spellings, and attendees of other meetings all fall through to the ordinary pending path unchanged, and a name the user set aside as "not a person" is never auto-created. At most 3 people per fire.

   `routed['receipt_lines']` is one line per created person and printing it is MANDATORY — it names who was added, what it came from, what it unblocked, and that `undo` reverses the whole batch (record and drained rows together). An auto-creation with no receipt is the CAPTUREFLOW silent-drop class inverted. Carry `routed['summary']['n_auto_created']` onto the `meeting_processed` receipt with the other counts (it rides `capture_summary=routed` at step 9 automatically). Omitting `attendee_records` is legal and is exactly the pre-ATTENDEE1 behaviour: no records, no evidence, no writes.

   What the helper enforces, in ONE place, because none of it was enforced anywhere before: the **capture floor** (owner + concrete deliverable + consequence — stated in `meeting-notes/SKILL.md` since Stage D and never enforced, which is why a third of this rail's captures were discussed-only), the **cross-meeting fusion guardrail** (the verbatim check below, in code), and **party-only relevance scoping** (`capture_gate.classify_capture`, whose `party-only` default this rail had never consulted because no meeting writer called the gate). Its four outputs: `book` (ordinary open commitments), `review` (written `pending_review` so they land in the needs-your-call queue and never in the open book — BOTH fusion refusals, marked `data.fusion_unverified`, and below-floor captures, marked `data.floor_gated` with their `FLOOR_*` reason as `review_reason`), `observed` (`commitment_observed` — kept and searchable, no open item, no count, no row), `skipped` (near-empty since the ruling below; never narrated). **M RULING 2026-08-01 — below-floor captures are NEVER silently dropped.** They used to be, and the review measured what that cost: on the audit's own hand-judged sample the floor destroyed a real promise for every junk capture it stopped, with no event, no counter and no row left behind. The floor's verdict is now a routing decision, so a wrong call costs one tap in the queue rather than a lost promise. The dated case routes to the queue too — the observed writer refuses dated/money items, and 'surfaces nowhere' was never an acceptable reading of a rail that says a dated item ALWAYS surfaces. Carry `routed['summary']['n_book'] + n_review` into the `meeting_processed` receipt's `extracted_count` / `pending_review_count`, and pass the whole return as `capture_summary=routed` on that same receipt (step 9) so the gates' own counts persist.

   **FLOOR2 (2026-08-06) — two of the floor's conditions read the TRANSCRIPT, not the sentence,** and they are the reason `transcript_text` is not optional here. The V1 interim re-measure found both shapes clearing every sentence-level condition on real calls: `FLOOR_DONE_IN_MEETING` (the action was performed on the call — "just click that right now" — and nothing survives it; never fires when the item carries a due date, a money amount, or a send/share/schedule/follow-up verb, because then the deliverable outlives the call) and `FLOOR_SUPERSEDED_IN_MEETING` (the same conversation took the offer back — the row then carries `data.superseding_quote`, the retracting words verbatim from that same transcript, beside the original evidence). Both are `meeting_capture.transcript_floor_reason`, both route to `review` exactly like any other floor verdict, and both are deliberately conservative: an uncertain case books rather than gates. Extract normally; the one code path decides.

6. **LOW confidence lands in `classification_confidence`, never in a flag (ATTRIB1-A DD-4):** your step-4 scoring is the ONE thing you pass — an ambiguous owner, a vague timeline, conflicting info, a new entity, or a sensitive category (firing / pricing / contract terms) is a LOW `classification_confidence` on that item (below the surface floor, 0.7 baked), and the helper derives `pending_review` from it together with the basis, the floor and the fusion verdicts. Never pass `pending_review` or `review_reason` in an item's kwargs: the route drops a literal flag before the build, and the builder keeps the derived value regardless. DECISIONS (not commitments) keep the write path they already had: `meeting_capture.build_decision_event` + `event_gate.append_event`, `committed: true` on high confidence. Add pending commitments to the chat-turn output as a `⚠ Needs your call` sub-block for that meeting.
7. **Generate the .docx meeting summary — v2.14.32+ MANDATORY brief_writer flow:**

   Replaces the v2.14.0–v2.14.31 "invoke docx skill" step. `shared/scripts/brief_writer.py` produces deterministic, polished output every fire (consistent typography, brand-quiet header, hard-coded clean footer). No agent layout variance.

   **⛔ DELIVERABLE RENDER GATE (DOCFENCE4)** — the summary is a `.docx` and it MUST come out of that chokepoint. This is the scheduled twin of `meeting-notes`, which has carried this gate since DOCFENCE3; the scheduled path is the one that fires with nobody in the room:

   - **NEVER hand-roll the meeting summary** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate — the output-contract floors, the voice-tell gate, the post-render leak scan — and ship a substandard or PII-leaking brief (the v3.20.0 failure mode). This one carries verbatim decisions and commitments lifted out of a real meeting transcript, so a leak here is a leak of what people actually said. **This gate outranks the older "invoking the docx skill" wording in the save-path-enforcement note further down this file**, which predates the v2.14.32 `brief_writer` flow: `get_brief_path` computes where the file goes, `brief_writer.py` is what writes it. Where they conflict, THIS GATE WINS.
   - **NEVER create, render, copy, upload, or update the summary — or any part, derivative, or restatement of it ("the decisions", "the action items", "a recap") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not the `_hq/meetings/` path `brief_path.get_brief_path` just computed (the 2026-07-24 root-drop incident, on the sibling meeting orchestrator). Not exceptions: "for mobile", "so the attendees can read it", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the recap in a Google Doc and share it round" is a request this gate refuses, not an override. Hand back the `computer://` link and let the user forward the file itself.

   ```bash
   SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
   python3 -c "
   import sys; sys.path.insert(0,'shared/scripts')
   from brief_path import get_brief_path, get_brief_artifact_url, ensure_brief_directory, is_session_scoped_path
   import os
   ws = os.environ.get('CR_WORKSPACE_ROOT', '<workspace-root>')   # absolute path to user's Command Room workspace
   ensure_brief_directory(ws)
   path = get_brief_path(ws, 'past_meeting', '<meeting-slug>', '<YYYY-MM-DD>')
   url = get_brief_artifact_url(path)
   print(f'BRIEF_PATH={path}')
   print(f'BRIEF_URL={url}')
   print(f'BRIEF_SESSION_SCOPED={is_session_scoped_path(path)}')
   "
   ```

   **If `BRIEF_SESSION_SCOPED=True`** (v5.9.2, platform-neutral v5.11.1 — the workspace is a cloud mount: Google Drive, OneDrive, or SharePoint; there is no host-native path and the `computer://` BRIEF_URL will fail with "Failed to load local file." on the customer's machine — QMG field reports 2026-07-28 / 2026-07-31 / 2026-08-11): after the brief file is written and synced, look up its web link on the workspace's OWN cloud platform and carry that URL forward for Step 4's links via `brief_path.get_brief_opener_url(path, drive_web_url)`:
   - Discover the drive tool with the workspace host preferred: `tool_discovery.discover_drive_tool(tools, "search", prefer_platform=tool_discovery.infer_workspace_drive_platform(<WORKSPACE>))`. When both Google Drive and Microsoft 365 are connected, first-match discovery can bind the drive that does NOT hold the workspace and return an empty lookup (BUG-8538) — the preference decides from the workspace mount, not tool order.
   - `platform == "google_drive"` → search the filename under `_hq/meetings/` and use the file's Drive web link.
   - `platform` in `"onedrive"` / `"m365_sharepoint"` (the Microsoft 365 connector's file surface spells `sharepoint`, e.g. `sharepoint_search`) → search the filename the same way and use the item's OneDrive/SharePoint web URL.
   - If the lookup finds nothing and another drive platform is connected — whether or not a preference was inferred (a bare `/sessions/...` mount usually infers none) — try that one before giving up. If no lookup succeeds, proceed with the `computer://` form — `get_brief_opener_url` falls back on its own.

   Capture the BRIEF_PATH + BRIEF_URL stdout. Then compose section content from meeting-notes' output and pipe it as JSON to `brief_writer.py` stdin:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/brief_writer.py <<'JSON'
   {
     "output_path": "<BRIEF_PATH from above>",
     "brief_kind": "past_meeting",
     "title": "<Primary External Attendee or Org> — <Meeting topic>",
     "subtitle": "<Day, Mon D, YYYY> · <H:MM AM/PM TZ> · <duration_min> min · <Project Name OR routing note>",
     "sections": [
       {"heading": "Attendees", "bullets": ["<Name 1 — role/org>", "<Name 2 — role/org>", "..."]},
       {"heading": "Summary", "bullets": ["<3-7 third-person factual recap bullets>", "..."]},
       {"heading": "Decisions", "bullets": ["<each decision as a standalone factual statement>", "..."]},
       {"heading": "Commitments", "bullets": ["<Owner: action — due date>", "..."]},
       {"heading": "Scope Changes", "bullets": ["<what changed: from → to>", "<impact>", "..."]},
       {"heading": "Financial", "bullets": ["<item — $amount — context>", "..."]},
       {"heading": "Open items", "bullets": ["<unresolved threads, items needing follow-up>", "..."]},
       {"heading": "Notable quotes", "bullets": ["<verbatim quote — Speaker>", "..."]}
     ]
   }
   JSON
   ```

   **Name spelling (v4.6.1 S3 / F-50 P2b):** every attendee name in `title`, the Attendees section, and the meeting event's title comes from the RESOLVED person record (`entity_resolve` display_name — the record's `canonical_name`), never the transcript's spelling. The dogfood rendered "Myra Samples" on this surface while resolution had correctly matched Mira Sample. Transcript spellings survive only inside verbatim evidence quotes (Notable quotes keeps its original text); an attendee with no record yet (open `person_proposal`) keeps the as-heard spelling until adjudicated. Full rule: `shared/ENTITY_RESOLVE_PROTOCOL.md` § Display names.

   **⛔ The headlined counterparty is the binder's, not yours (SPEC BRIEFBIND1, BUG-8244 / G28 family):** the person a brief's `title` names comes from `meeting_capture.brief_counterparty` over THIS meeting's own record — its `person_ids` / `data.attendees_external`, nothing else. When the binder returns `bound: False`, the title renders WITHOUT a person name (topic only) — unbound per the G28 contract — and you NEVER fill the vacuum from a recent joint session, a topic cluster, a calendar neighbor, or any other association: pass whatever association evidence you hold as `association=` and let the binder record the refusal. A wrong name here is strictly worse than none — downstream surfaces key person context on the headline. `brief_claim_audit(meeting_event, <the name the title carries>)` must come back `supported` before the brief is written; a `supported: False` verdict means the headline is claiming a human the cited record does not back — fix the headline, never the record.

   **Section list is the canonical past_meeting set — `skills/meeting-notes/SKILL.md` "SESSION_NOTES Format" is the source of truth for what meeting-notes extracts.** Same ordering every fire. Omit any section with no signal — never include placeholder/`TBD` content. Don't paraphrase heading names. **If you add or rename a section, update both `meeting-notes/SKILL.md` AND this template in the same commit — they MUST stay in sync.**

   **Scope Changes & Financial — conditional inclusion (v3.6.3+):** these two sections are forwardable by default (vendors / clients / partners expect scope and dollar changes documented), but ONLY include them when actual signal exists. Skip Scope Changes if no scope shift came up in the meeting. Skip Financial if zero dollar amounts / budget / revenue figures were discussed. The "omit if no signal" rule applies harder here than to Decisions/Commitments — empty Financial/Scope sections in a forwardable doc look like extraction failure to the recipient.

   **Internal-only content lives in SESSION_NOTES, NOT the .docx (Phase 4.5a):** meeting-notes also extracts Business Lens (Risks/Opportunities/Timeline/Relationships) and Context & Follow-Up (internal assumptions needing the user's call). Those NEVER go in the .docx — Phase 4.5a appends them to `SESSION_NOTES_<PROJECT>.md` where the user reads them privately when they `go [project]`.

   **Forwardable-clean enforcement (per `meeting-notes/SKILL.md` Brief Authoring Rules):** the brief content above MUST be third-person, shareable. Do NOT include:
   - Internal asks ("M needs to think about…", "your call on…")
   - Follow-up email drafts (those go to the per-attendee follow-up flow, not the brief)
   - Per-attendee notes that one attendee shouldn't see about another
   - Business Lens content (Risks / Opportunities / Timeline / Relationships) — internal-only, goes to SESSION_NOTES
   - Context & Follow-Up assumptions — internal-only, goes to SESSION_NOTES
   - Provenance metadata (`brief_writer` hard-codes the footer to `Command Room`; the pre-v2.14.32 `Source: ... | Fired: ... | meeting_id: ... | TTL: ...` footer pattern is dead)

   After brief_writer returns, run:

   ```bash
   test -f "<BRIEF_PATH>" && echo "OK: $(stat -c%s '<BRIEF_PATH>') bytes" || echo "MISSING"
   ```

   If output is `MISSING`: the writer failed to save. EXCLUDE this meeting from the Meeting briefs section (no broken links). Surface plain-English: `(Brief for <meeting> couldn't be saved to _hq/meetings/. Re-fire `process the call <name>` to retry.)` Append a `brief_save_failed` event silently. **And carry the exclusion into the render set (SPEC MEETCOUNT1):** this meeting's `meeting_render_set` row gets status `brief_failed`, so the coverage line names the reduction instead of the count silently dropping by one.

   On success: cache the BRIEF_PATH + BRIEF_URL on the meeting record. Phase 6 Step 3 uses BRIEF_URL as the `artifact_link.url` (inside widget) AND as the Briefs-section link target (below widget). Single source of truth — no path drift.
8. **Write canonical `meeting` event** (v2.14.19+ — REQUIRED, not optional) to events.jsonl. This is the authoritative record that the meeting occurred. **Construct via `meeting_capture.build_meeting_event()` (BUG-8244 — the one sanctioned constructor; hand-rolled dicts are how 4 incompatible attendee shapes shipped),** passing `brief_path` through the returned event's `data` before appending. Shape the builder produces: `{type: "meeting", ts: <meeting_start_local_ISO>, source_skill: "past-meetings", primary_thread_id: <resolved or null>, org_ids: [<the counterparty org(s) this meeting was WITH, when resolved — including an org this very run just created for the counterparty; NEVER the CEO's own org>], person_ids: [<all attendees resolved>], data: {title, source_ref: "granola:<meeting_id>", duration_min, brief_path, attendees: [<every invitee EMAIL from the calendar invite / backend metadata, verbatim, resolved or not — identity-reconcile corroborates merges from these and the backfill repairs history with them>], attendees_external: [<names not in entities.json>], meeting_type: <sales|internal_1_1|external|board|… — the same classification Phase 4.7's grading derives; ALWAYS stamp it here>}}`. Pass `source_had_attendees=True` whenever the backend listed ANY participants — an empty binding then stamps `data.binding_missing` for the audit instead of vanishing silently. Pass `transcript_class=routed['transcript_class']` and `working_session=routed['summary']['working_session']` from step 5 (ATTRIB1-A): the class is what the per-class re-measure reads, and `working_session: true` is how a dictated session is told apart from a call that produced nothing. `org_ids` matters even when `primary_thread_id` resolves: a sales call with a new prospect routes to the CEO's own product/GTM thread, which attributes the event to the CEO's org — leaving the prospect org structurally unlinked from the one event that should seed its pipeline record (the PIPE1 D9.1 live gap). Use `ts` = meeting START time per Granola's metadata, NOT the processing timestamp. `meeting_type` is a load-bearing read for the deal-signal detector (PIPE1 D9.1: `meeting_type: "sales"` on an org with no deal coverage proposes deal creation) — stamp it on every meeting event, not only graded ones. This event is what `tell me about <person>` and "when did I last meet with X" queries read from — without it, there's no canonical meeting record (only `meeting_processed` which is a status event, not a meeting event).

9. **Write `meeting_processed` event** to events.jsonl with `meeting_id`, `processed_at`, `extracted_count`, `pending_review_count`. Build it with `meeting_capture.build_meeting_processed_event(..., capture_summary=routed)` — passing Phase 4 step 5's `route_meeting_captures` return stamps `data.capture_counts` = `{n_book, n_review, n_observed, n_skipped, n_floor_gated, n_deduped, n_fusion_inert, floor_reasons, skipped_reasons}`, which is the ONLY record anywhere of what the admission gates did. `n_floor_gated` is the share of `n_review` the capture floor routed — a SUBSET of it, never added to it — `n_deduped` counts twin captures of one act that FLOOR3's collapse pass folded into a surviving row (never written, so no other count moves), `n_fusion_inert` counts written rows the fusion guardrail could NOT check at all (a transcript-less fire stamps every row it writes — see the guardrail section below; it cuts across all three lanes, so it is a subset of nothing and is never added to another count), and `floor_reasons` tallies which `FLOOR_*` condition gated each one; together they are what make the floor's tuning measurable, and without them a mis-tuned floor is undetectable and the acceptance re-measure has nothing to read. None of it goes in the chat card. Counts and reason tallies only — never a title. This is a SEPARATE event from #8 — `meeting_processed` records that THE ORCHESTRATOR processed this transcript (status), while `meeting` records that THE MEETING happened (data substrate). Both must exist.

## ⛔ SPEC CAPFENCE1 — THE CAPTURE FENCE (between meetings, 2026-08-27)

**Initialize `n_time_fence_deferred = None` once, before this fire's first meeting starts.** Only the `defer: true` branch below ever sets it to a real count — a fire where the fence never binds carries it through Phase 5 as None, which is what keeps that fire's receipt byte-identical to the pre-CAPFENCE1 shape (§Acceptance's no-op pin).

**Between meetings, never mid-meeting.** After THIS meeting's step 9 above (`meeting_processed` written) and BEFORE starting the NEXT meeting in the oldest-first set Phase 3 handed you, check whether the capture leg's own clock has crossed its stopping rule:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from end_of_day import capture_fence_elapsed_ms, capture_fence_should_defer, CAPTURE_FENCE_MS
elapsed = capture_fence_elapsed_ms('<capture_leg_start recorded at the top of Phase D>')
defer = capture_fence_should_defer(elapsed, <n_captured_full_depth — how many meetings THIS fire has finished step 9 for so far, including the one that just finished>)
print(json.dumps({'elapsed_ms': elapsed, 'fence_ms': CAPTURE_FENCE_MS, 'defer': defer}))
"
```

`elapsed` is LEDGER-ELAPSED — the SAME `capture_leg_start` EODLEG1 already records at the top of Phase D, never a fresh clock read composed for this check alone. `CAPTURE_FENCE_MS` is the ONE constant this check reads; `end_of_day.CLOSE_BUDGET_MS` is a different constant with a different job (§0 Ruling 1) and nothing here reads it.

**THE SUBSTANCE FLOOR (Ruling 3) already lives inside `capture_fence_should_defer` — do not re-implement it in prose, and do not pass a different threshold.** Below one captured meeting the function returns False regardless of `elapsed`, so this fire's FIRST meeting always finishes even if the fence was already crossed before it started (a slow transcript fetch, a cold connector). The floor is one, not a knob.

**`defer: false`** — continue to the next meeting in the oldest-first set exactly as before this spec. Nothing about Phase 4's own steps changes for it.

**`defer: true`** — STOP. Every meeting still left in the oldest-first set (not yet started this fire) is deferred, WHOLE, to the next pass — never trimmed, never half-captured. Compute the resume marker via the SAME machinery the batch cap already uses:

```python
from end_of_day import capture_fence_window_marker
marker = capture_fence_window_marker(
    window,  # the SAME window object Phase 3 computed
    oldest_deferred_start='<the earliest start time among the meetings just deferred>')
n_time_fence_deferred = <count of meetings just deferred>
```

`capture_fence_window_marker` IS `catchup.receipt_window_marker` under a name this spec's own call site can grep for — never a second dialect of the batch cap's own honesty gate. If Phase 3's own count cap ALSO left meetings unhandled beyond its 5-meeting limit, this still resolves correctly: `receipt_window_marker` clamps to the oldest unhandled start regardless of WHICH mechanism made it unhandled, so the two triggers never produce two different resume points for the same window.

**Carry `n_time_fence_deferred` to Phase 5, exactly like `window_incomplete_before` itself: present with the real count when the fence actually deferred something THIS fire, OMITTED when it did not.** A fence that never bound produces a receipt byte-identical to the pre-CAPFENCE1 shape — never write a phantom zero for a mechanism that never engaged. This travels in the SAME `capture_leg` dict `window_incomplete_before` already rides, and it also folds onto the pack (`pack["n_time_fence_deferred"]`, alongside `pack["window_incomplete_before"]` in Phase 5) so the coverage strip's disclosure-first lead can name it.

**⛔ RULING 4, restated at the one place it is load-bearing: nothing about Phase 4.5 / 4.6 / 4.6.b / 4.7 / 4.8 below changes when the fence trips.** Those passes each iterate whatever Phase 4 actually processed — the fence bounds ENTRY into Phase 4, never the depth of what already entered it. A deferred meeting never reaches ANY of those passes (it never entered Phase 4 at all), and every meeting that DID enter Phase 4 runs every one of them exactly as it would on a day the fence never bound. The fence defers whole meetings; it never trims a gate, a floor, or a verification on the meetings it processes.

**Idempotency note:** if the orchestrator re-fires on a transcript that already has both events, skip the writes (use `source_ref` dedup). Do NOT write a second `meeting` event for the same Granola meeting_id.

**Cross-meeting fusion guardrail (v2.14.19+ — REQUIRED; MECHANIZED 2026-08-01, CAPTUREFLOW A3):** a commitment's verbatim phrase (a 5+ word normalized substring of `data.evidence`, falling back to `data.title`) must actually appear in the transcript text of the meeting it is being attributed to. If it doesn't, the extraction has crossed meetings and the event is NOT written to the book — it is written `pending_review` with the reason "extracted phrase not in the source transcript", which lands it in the needs-your-call queue.

**For commitments this is no longer your job: `route_meeting_captures` (step 5 above) runs the check** — `meeting_capture.fusion_status` is the implementation, and passing the step-1 transcript as `transcript_text` is what arms it. Omitting the transcript leaves the check inert (skip-not-fail), so pass it. **An inert run is now visible rather than silent:** every row written while the guardrail could not run carries `data.fusion_inert = true` and is counted as `n_fusion_inert` on the receipt, in every lane. It moves no row — routing still refuses only what the check positively establishes is absent — it stops the absence of a refusal from reading as a pass, which is how 20 of 131 audited captures came to sit on the book carrying evidence that was never verbatim from any transcript. For DECISIONS, which keep their own write path, run the same test by eye before appending: same rule, same reason string, same outcome.

This guardrail addresses the v2.14.18 fresh-install bug where the extraction layer fused two same-topic meetings and wrote a commitment to the wrong source meeting via `data.source_ref`; the language in the evidence string was from the OTHER meeting's transcript. It stayed prose with no code and no test until CAPTUREFLOW, and the 2026-08-01 capture-load audit found a live capture whose evidence appears nowhere in its cited transcript — exactly the thing this paragraph says is impossible.

Implementation notes (what the helper does, so a decision check matches it): use the transcript text already loaded in Phase 4 (never re-fetch); match case-insensitively over whitespace/punctuation-normalized text; check `data.evidence` first and `data.title` second; a string shorter than 5 words is not judged at all, because a check that cannot establish absence must not refuse.

# Phase 4.5 — Project narrative + people sync (v2.10.8+)

After Phase 4 completes per-meeting auto-processing, run these three passes BEFORE Phase 5/6. Goal: when M says `go [project]` later, the project's narrative file already reflects today's meetings, the people layer is current, and high-signal unrouted meetings surface as project-creation candidates.

**4.5a — Project narrative append.** For each meeting that routed to a project (primary_thread_id is set), append a dated section to `<project_folder>/SESSION_NOTES_<PROJECT>.md` (or create the file if missing). Use atomic write per `shared/scripts/atomic_write.py`. Skip for meetings tagged `internal-only` or `skipped` per Phase 3 filters. The narrative append is the user-facing reflection of `meeting_processed` events — events.jsonl is the canonical data layer; SESSION_NOTES is the human-readable story.

**Append shape (v3.6.3+ — mirrors meeting-notes/SKILL.md "SESSION_NOTES Format"):**

The append includes ALL meeting-notes-extracted content, both forwardable and internal:

1. **Meeting title** and date header
2. **Attendees** (resolved names)
3. **Summary** — 3-7 bullets matching the .docx Summary
4. **Decisions** captured (matching the .docx Decisions)
5. **Commitments owed** — both directions (You owe / They owe), matching the .docx Commitments
6. **Scope Changes** — what changed: from → to, with business impact. Same content as the .docx Scope Changes section.
7. **Financial** — item, $amount, scope/note, plus running total if available. Same content as the .docx Financial section.
8. **Context & Follow-Up** (INTERNAL — not in .docx) — assumptions or clarifications needing the user's call, with reasoning
9. **Business Lens** (INTERNAL — not in .docx) — four sub-sections:
   - **Risks:** what could derail this
   - **Opportunities:** what could be leveraged
   - **Timeline:** pressure points, slips, accelerations
   - **Relationships:** trust signals, friction, shifts in dynamic

**Sections 8 and 9 are SESSION_NOTES-only.** They contain the user-facing analysis that's NOT safe to forward to attendees. Pre-v3.6.3 the append dropped these entirely — meeting-notes extracted them but they landed nowhere persistent, so the user never saw Business Lens analysis except in the chat moment when the meeting was processed. Now the SESSION_NOTES file captures them so `go [project]` surfaces the full meeting-notes extraction in context.

**Omit-if-no-signal rule still applies:** if Business Lens has no real signal (uneventful internal sync, nothing changed), omit those sub-sections entirely rather than writing "no risks identified." Empty Business Lens entries train the user to skim past the section.

**4.5b — Real-time people-crm pass.** For each meeting transcript, invoke people-crm with confidence threshold ≥ 0.85 (higher than the weekly identity/fact passes, to avoid false positives at real-time pace). Auto-apply on: new email-signature roles ("CEO at NewCo" mentioned in two or more sources within the transcript), new org affiliations evidenced ≥ 2 sources, normalized name corrections (typo fixes, alternate spellings). Lower-confidence changes (single-source role hints, ambiguous affiliations, unconfirmed email address sightings) get queued to events.jsonl as a `person_update_proposal` event and surface in the confirm queue — current behavior preserved. Real-time pass complements weekly synthesis; doesn't replace it.

**Person-write canonical path (v3.2+ MANDATORY).** All entity creates and updates in this pass go through `shared/scripts/people_writer.py` — never direct `entities.json` writes. Memorialized failure: `person_063` Rio Sample (2026-04-30) and `person_064` Dustin Sample (2026-04-26 duplicate of `person_004`) were both written here with hand-rolled JSON shapes that drifted from the schema, and the Dustin Sample duplicate slipped past because no dedup ran. The contract:

1. **Always dedup before creating.** Before emitting a `person_proposal` event for a newly-mentioned attendee, call `find_existing_person(workspace_root, name=<inferred name>, email=<inferred email or None>, aliases=<inferred aliases>)`. If a match returns, do NOT emit `person_proposal` — instead emit a single `person_update_proposal` event referencing the existing `person_id` with the proposed delta (new role, new last_interaction, new affiliation). The REVIEW widget surfaces this as "Update X — already in your network" instead of "Add X as new person."
2. **Auto-apply path (confidence ≥ 0.85)** uses `update_person(workspace_root, existing_id, ...)` for matches and `create_person(workspace_root, canonical_name=..., primary_org_id=..., source_skill="past-meetings")` for misses. The writer's internal dedup is a backstop — if it raises `DuplicatePersonError`, fall through to update_person.
3. **Pending-review path** writes a `person_proposal` (new) or `person_update_proposal` (existing) event to events.jsonl — NEVER an entity record with `pending_review: true` flag, which is the v3.0/v3.1 anti-pattern. The user's `IDX add [text]` reply at REVIEW time triggers the actual writer call via `apply-choices` Step 3a.
4. **Named humans ONLY (PID1 D5 — MANDATORY).** A person proposal requires a NAME from the source. An unidentified speaker/attendee ("speaker 2", a bare address) NEVER becomes a person proposal — `build_person_proposal_event` raises on empty names by design; never work around it. Write ONE annotation instead via `meeting_capture.build_unidentified_attendee_event("granola:<meeting_id>", attendee_hint=<the source's own label>, attendee_email=<address ONLY when the participant metadata literally carries it, else None>)` + `event_gate.append_event`. Annotations are fully silent (no row, no chat line) — the Sunday identity-reconcile job resolves them against calendars/mail. **Named ORGS are not humans either (WG1-B D-B3):** pass `workspace_root=<WORKSPACE>` on every `build_person_proposal_event` call — a name `find_existing_org` resolves to a tracked org comes back as an `org_proposal` event; append it the same way and let the org rail adjudicate. Never strip the parameter to force a person row.
5. **Attach what the source carries (PID1 D10).** When participant metadata / calendar invitees / mail headers carry a last name or an address for a proposed person, include them: `name` = the fullest spelling observed; the address verbatim in the `evidence`/`source_ref` text so the F-3 observed-email attribution can pick it up. This is the upstream lever that makes future rows confident-matchable or auto-eligible (the parked first-name cooldown stays parked — M ruling).

See `apply-choices/SKILL.md` Step 3a and `people-crm/SKILL.md` Writer Contract for the full helper contract + bash gate snippet.

**4.5d — Speaker-attribution ambiguity guard (v3.2.3+).** Granola transcripts tag each spoken segment with a speaker name. When two attendees share a first name (e.g. Sam's Summit Company has BOTH `person_063` Rio Sample AND person Rio Lange), Granola's tag of "Rio" is ambiguous — and pre-v3.2.3 the resolver silently picked one, attributing commitments / decisions to the wrong person. Memorialized failure: Rio Lange / Rio Sample misattribution flagged repeatedly by Sam through April 2026.

For each `commitment` / `decision` event proposed in this fire that has an attributed speaker:

1. Build a first-name index from the meeting's resolved attendee list:
   ```
   attendee_first_names = { lower(first_name): [person_id, ...] for each attendee }
   ```
2. Take the speaker's first name (Granola-tagged), lowercase it.
3. Check the index:
   - **Exactly one attendee matches the first name AND full name matches** → high confidence. Resolve `owner_id` to that attendee's person_id, proceed normally.
   - **Multiple attendees share the first name** → `data.attribution_ambiguous = true`, `data.attribution_candidates = [person_id_1, person_id_2, ...]`, `owner_id = ""`. Do NOT auto-resolve.
   - **No attendee matches the speaker name** → `data.attribution_unknown = true`, `owner_id = ""`. Could be a guest, late join, or Granola mis-tag.
4. Items with `attribution_ambiguous` OR `attribution_unknown` route to REVIEW (Phase 6) regardless of other confidence scores. They get a sub_item with the existing `IDX add [text]` action set. Body text lists the candidate attendees explicitly so the user can reply with the right name:

   > *"⚠ Granola tagged this commitment to "Rio." Both Rio Lange and Rio Sample were in the meeting. Type `[IDX] add to Rio Lange` or `[IDX] add to Rio Sample` to attribute. Type `[IDX] add to someone else` if it was a guest."*

5. **`IDX add [text]` apply handler** (apply-choices Step 3a): parse user input for "to <name>" pattern. Resolve the name against the meeting's attendee list. Re-emit the commitment with the corrected `owner_id` and remove the `attribution_ambiguous` / `attribution_unknown` flags. If the input doesn't match an attendee, fall through to a manual person create via `people_writer.create_person()`.

This rule is anti-improvisation. The agent does NOT auto-pick the alphabetically-first candidate, the first-mentioned candidate, or the most-frequently-tagged candidate — those heuristics are how the bug shipped pre-v3.2.3. **When ambiguous, surface for user.**

**4.5c — New-project detection for unrouted meetings.** For each meeting that resolved to `unrouted` in Phase 4 (no primary_thread_id), score these signals:
- ≥ 1 external attendee (not in workspace's primary org)
- ≥ 2 commitments extracted in Phase 4 (real exchange of work, not a one-off touch)
- ≥ 1 decision present (not just discussion)
- Named topic recurs ≥ 2 times across messages (real subject, not random)
- Meeting title contains a candidate project name (capitalized noun phrase, NOT generic words like "sync"/"call"/"chat")

If ≥ 3 signals match, surface a sub-item under the meeting in the chat output: `⚠ Possible new project — say "new project [suggested name]" or "skip [N] new project" to dismiss.` Suggested name comes from the meeting title's candidate project name OR the dominant external org. No auto-creation; user-confirmed via the existing `new project` workspace-manager flow.

If < 3 signals, no surface. The meeting still gets its .docx in `_hq/staging/<today>/_unrouted/` and remains in the chat output without the new-project pill.

# Phase 4.6 — CRU pass: cross-reference today's transcripts against open commitments (v2.14.6+)

Per `shared/scripts/cru_match.py` Path 3. After per-meeting auto-processing (Phase 4) and project-narrative + people-sync (Phase 4.5) complete, scan each newly-processed transcript against pre-existing open commitments. The premise: a meeting that just discussed a deliverable often resolves, updates, or layers a new ask onto an open commitment — auto-detecting closes the loop without the user manually marking received.

**Conservative — HIGH-confidence auto-resolve only.** Borderline matches go to a `pending_review` queue the confirm surfaces render for one-click confirm.

**⛔ THE CIRCULARITY FENCE (AUTOAPPLY §6) — MANDATORY, and the reason this phase used to manufacture its own noise.** This phase runs AFTER Phase 4 has appended today's extractions, so an unfenced `load_open_commitments()` hands the matcher the asks this very fire just captured. A commitment extracted from transcript T scores ~1.0 against T and carries no completion language, so it lands `pending_review` — Command Room asking "did you already handle this?" about something it wrote down five minutes earlier, from the same meeting. Three things below are load-bearing; none is optional:

- **Record `fire_start`** (UTC ISO) BEFORE Phase 4 appends anything, and pass it as `exclude_captured_since` — that is what excludes same-fire SIBLING matches (meeting A's ask scored against meeting B's transcript in one batch).
- **Pass `transcript_ts` — THIS meeting's own START time** (EVORDER layer 3, F-27). `fire_start` fences against the start of THIS FIRE, which is a different question: a commitment captured before the fire but AFTER the meeting ended sails straight through it, and then the meeting's transcript closes a promise that did not exist while anyone was talking. Demonstrated by execution: a commitment captured 20:00, a transcript from an 18:00 meeting saying "I sent the revised pricing sheet already, it is done", `fire_start` at 23:00 → `auto_resolve` at 0.75. This is the same value Phase 4 step 8 stamps as the `meeting` event's `ts`. **Pass it with its offset (or in UTC) — never a naive local wall-clock string:** a bare `2026-07-28T18:00:00` is read as UTC, which silently moves the fence by your offset (the CATCHUP1 F-1 connector-boundary class). Omitting `transcript_ts` leaves the matcher's layer 3 inert — and since POLICY1-B the PASS refuses every close for that transcript by name (`NoTranscriptTs` on `close_refusals`; chips still write): a close needs the meeting's own time, so pass it to BOTH calls. The pass re-checks the order itself at the writer's door (`StaleEvidence`: a row captured after the meeting started never closes on it) and refuses a close whose quote is not THE completion turn for the item — one turn that both carries completion language and scores at the bar against the title on its own (`NoCompletionTurn`; the v5.27.0 supervised test watched a 75-minute call close two promises it never mentioned on a transcript-wide title score plus a completion phrase about something else). A present-but-unparseable value fails SAFE and LOUD — the pass closes nothing at all for that transcript and prints `RECONFENCE: transcript_ts=…` on stderr. Never invent one from the processing clock: "now" is always after every commitment, so a guessed value fences nothing and reads as if it did. A machine close is stamped `resolved_by: past-meetings`, never the owner's person id.
- **Accumulate `diagnostics` across every transcript in the fire** and carry the total onto Phase 5's receipt as `n_stale_evidence_skipped` — the same key both mail rails already report. Pass ONE dict to every `match_transcript_to_commitments` call (the matcher adds to it, never resets it) and read `diag.get('stale_evidence_dropped', 0)` at the end. A non-zero value is the fence **working**, not an error, and needs no report to the user. A fence that drops silently is how F-11 hid for a week.
- **Pass `transcript_source_ref`** — THIS meeting's own ref (`granola:<id>`), the ref Phase 4 stamped on its extractions. `cru_match.commitment_source_refs` is what the fence compares against, so a merged survivor's absorbed refs are covered too.
- **Thread ONE `already_proposed` set across every transcript in the fire**, seeded from `cru_match.open_review_proposal_ids(events_path)` and applied via `cru_match.filter_duplicate_review_targets` — one open review proposal per commitment, on disk and within the fire. Two transcripts in one batch proposing the same commitment (observed live at scores 1.0 and 0.571) is one question rendered twice.

- **Thread ONE `review_budget` dict across every transcript in the fire** and pass it to `cru_match.cap_review_proposals`, which writes at most **25** proposals per fire, highest `match_score` first. This is a VOLUME bound and it is **not** a threshold — the match floors below are untouched and stay untouched, and a suppressed candidate scored exactly what it always scored. It exists because the 2026-08-19 fire wrote **70** proposals in about 35 seconds and nothing anywhere asked how many was too many: dedup bounds proposals per commitment, the floors bound them per candidate, and neither is a statement about the size of the pile the CEO opens in the morning. A fresh dict per transcript makes the cap per-transcript and bounds nothing. **Carry `review_budget['proposals_suppressed']` onto Phase 5's receipt** — a cap without its count is a silence, and zero is written rather than omitted.
- **Every proposal carries the matched commitment's own `title` — and, since POLICY1-A, the words that triggered it.** `apply_transcript_results` passes `title=r['title']` and `evidence=r['evidence_quote']` (the completion turn the matcher scored, verbatim, speaker marker stripped, at most 240 characters) on every proposal and every close. The builder refuses an empty title (`ReviewProposalTitleError`) and refuses the retired fixed string `Past meeting transcript (…)` (`FixedEvidenceError`) — 635 of one month's 726 proposals carried that string and not one could be graded from the record. The four words it used to carry (completion / schedule-shift / new-ask / title match) ride on `data.signal` instead.
- **Policy decides before anything is built (SPEC POLICY1 v2, lane A; M ruling 2026-09-03).** `commitment_policy.decide` is the one table for every closer — this pass, the sent-mail rail, the reply rail. The bar it encodes is the standing one (score at the match bar WITH completion language; presets never move it). What changed is the GUESS lane: a row the extractor was unsure about (`pending_review`) whose later transcript says the work was done **closes as done, automatically** — `confirmed_by: "transcript"`, the completion turn verbatim as the evidence, `resolution_reason: auto_closed_transcript_evidence`, on the fire's batch so one `undo` puts it back. Nobody is asked. Below the bar on such a row nothing is written at all, because the row is already a question and the daily drain owns its lapse.
- **The only question-shaped write left is the chip, and it is narrow.** A CORROBORATING match (the topic came up, the words do not say it is done) writes ONE in-row chip carrying the quote — PLATE1's `data.proposal` — and only when the score is inside **0.65–0.80**. Outside that band nothing is written. On the operator's month that turns 763 proposals into **5** chips (the measured replay, recorded in the POLICY1-A replay record under `_hq/audit-reports/`), and every automatic act carries the words that caused it.
- **Once per (item, meeting), and then the chip resolves ITSELF.** The same transcript never chips twice about the same item; a re-score of the same pair APPENDS a chip carrying `supersedes_seq` (readers take the newest; `match_score` is never edited). At `commitment_policy.PROPOSAL_TTL_DAYS` (**four days** — M's standing window) the review-expiry job resolves every standing chip: it APPLIES the chip's own evidence when that evidence meets the close bar (the row closes as done, with the quote, undoable) and RETRACTS it otherwise (a dismissal, never a close). It never waits and never repeats, and a retracted (item, meeting) is never chipped again. A new meeting may chip once.
- **Every close this fire writes is one `undo` away.** `apply_transcript_results` stamps the fire's batch id (`mint_fire_batch_id(fire_start)`, minted ONCE per fire) and `brain_change_class: commitment_close` on each closure, so a bare `undo` in a fresh chat lists the fire and the registered reverser reopens every row it closed. The brief's CHANGED line narrates the count once, from the written closes.
- **THE SWITCH (CUT-A, M ruling R-A 2026-09-06) — closing on evidence ships OFF.** `apply_transcript_results` reads `commitment-policy.auto_close_from_transcript` itself (one reader, failing to off); nothing in this snippet branches on it. While off, the bar is still judged and every refusal still counted, a row that would have closed is counted as `n_close_withheld` and written as a review proposal instead (the chip shape) when it scores at or above the chip band's floor — never a close, on either target state — and `counts["closes_enabled"]` says which world the fire ran in. M turns it on by word (`turn on closing on evidence`, workspace-manager); until then this pass closes nothing, the chip TTL leg retracts instead of closing, and the calendar closer offers without promoting.

Skip entirely if:
- No newly-processed meetings this fire (nothing to cross-reference against).
- Open-commitment count is zero (helper returns `[]`).

**⛔ THE CRU WALK LEDGER (SPEC EODSPEED1 part 4) — consult it BEFORE each transcript's pass, feed it AFTER.** One live fire re-walked 237 stale-evidence rows a prior fire had already refused — same transcript, same open book, same deterministic verdicts, re-fetched to re-derive them. The ledger closes that class:

- **Before** running the matcher for a transcript, call `eod_incremental.already_walked(workspace_root, evidence_ref='granola:<THIS meeting id>', evidence_ts='<THIS meeting start ts — the same value passed as transcript_ts>')`. A non-None return means a COMPLETE walk of this exact evidence is on the ledger inside the evidence window: **skip this transcript's CRU pass entirely** — no matcher call, no re-fetch for this purpose — and count it: `n_cru_walks_ledger_skipped += 1`, `n_stale_evidence_ledger_honored += walked["n_stale"]`. The recorded verdicts already landed as events when the walk ran; there is nothing to re-write.
- **After** a transcript's pass runs to completion — matcher returned, closes and proposals appended — call `eod_incremental.record_walk(workspace_root, evidence_ref='granola:<id>', evidence_ts='<the same transcript_ts>', n_stale=<this transcript's own stale_evidence_dropped delta>, n_results=len(results))`. Record ONLY a completed walk; a pass that died mid-append records nothing and re-walks next fire.
- **Never widen the honor.** The helper refuses anything it cannot prove (different evidence ts, entry older than the evidence window, no `complete` flag) and then you walk normally. An empty ledger is byte-identical to the pre-EODSPEED1 build. The matcher, its thresholds, and its floors are untouched.
- **Carry both counts to Phase 5** in the `capture_leg` block as `n_cru_walks_ledger_skipped` and `n_stale_evidence_ledger_honored` — zero written, never omitted, exactly like `n_stale_evidence_skipped` (which keeps meaning THIS fire's own fresh refusals, unchanged).

Otherwise, for EACH newly-processed meeting transcript, execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_transcript_to_commitments,
    open_review_proposal_ids,
)
# POLICY1-A — the resolution policy's I/O half: every close, proposal and
# silence below is decided by commitment_policy.decide and written by the
# shipped writers inside this ONE function. The ladder that used to live
# inline here (auto_resolve -> close_commitment, pending_review/supersede ->
# build_pending_review_event, a fixed f-string as evidence) is code now, with
# its own suite; nothing in this snippet builds an event.
from commitment_policy_pass import apply_transcript_results
from commitment_policy import mint_fire_batch_id
# SPEC EODSPEED1 — the CRU walk ledger (see the block above this snippet).
from eod_incremental import already_walked, record_walk

workspace_root = '<absolute path to the workspace root>'
events_path = '<absolute path to _hq/data/events.jsonl>'
fire_start = '<UTC ISO recorded BEFORE Phase 4 appended anything>'

# EODSPEED1 — honor a recorded complete walk of THIS evidence; skip the pass.
walked = already_walked(workspace_root,
                        evidence_ref='granola:<THIS meeting id>',
                        evidence_ts='<THIS meeting start ts, e.g. 2026-07-28T18:00:00Z>')
if walked is not None:
    print(f'CRU past-meetings: walk ledger honored for granola:<THIS meeting id> '
          f'(n_stale={walked[\"n_stale\"]}) — pass skipped')
    raise SystemExit(0)  # count n_cru_walks_ledger_skipped / ..._honored outside
# ONE set for the whole fire — seeded from disk, mutated per transcript.
already_proposed = open_review_proposal_ids(events_path)
# ONE diagnostics dict for the whole fire — EVORDER layer 3 counts into it.
cru_diag = {}
# EODSPEED1 — this transcript's own stale delta, for record_walk below.
stale_before = cru_diag.get('stale_evidence_dropped', 0)
# ONE budget dict for the whole fire — TITLEMINT1's volume cap counts into it.
review_budget = {}
# ONE batch id for the whole fire — the RUN (POLICY1-B DD-5). Every close
# this fire writes carries a GROUP batch under it, one group per meeting
# (`apply_transcript_results` mints the group from this run id + the
# meeting ref), so a bare `undo` lists the fire with one line per meeting:
# `undo 1` reverses the fire, `undo 1a` one meeting's closes. Mint the run
# id ONCE before the first transcript and pass the same value to every
# call — a fresh run id per transcript would list each meeting as its own
# run and lose the "whole fire" gesture.
fire_batch_id = mint_fire_batch_id(fire_start)
opens = load_open_commitments(events_path)
results = match_transcript_to_commitments(
    open_commitments=opens,
    attendee_person_ids=['<resolved attendee person_id 1>', ...],
    transcript_text='<full transcript text for THIS meeting>',
    # §6 fence — a transcript never scores against what it just created.
    transcript_source_ref='granola:<THIS meeting id>',
    exclude_captured_since=fire_start,
    # EVORDER layer 3 — THIS meeting's own START time, offset-carrying or UTC,
    # exactly as Granola reported it. A meeting cannot be evidence that a
    # promise captured after it was already kept. Omit only if the metadata
    # truly carried no start time; never substitute the processing clock.
    transcript_ts='<THIS meeting start ts, e.g. 2026-07-28T18:00:00Z>',
    diagnostics=cru_diag,
    # F-28 — the workspace so the roster reader can tell one person written as
    # BOTH an id and that person's name apart from two real counterparties.
    workspace_root=workspace_root,
)

# POLICY1-A — the pass. Per result row, commitment_policy.decide says
# close / confirm_close / propose / none over (target state x evidence class):
#   * a CONFIRMED row at >= the bar WITH completion language CLOSES through
#     commitment_state.close_commitment — evidence = the completion turn the
#     matcher scored (verbatim, marker stripped), source_ref = THIS meeting,
#     resolved_by_match='match' + match_score, the fire batch id;
#   * an UNCONFIRMED row (pending_review) with the same evidence CLOSES AS
#     DONE too (M ruling 2026-09-03), through the writer's confirmed_by
#     door: data.confirmed_by='transcript', resolution_reason=
#     'auto_closed_transcript_evidence', the same quote and batch. No
#     question is written for it, ever;
#   * below the bar on an unconfirmed row NOTHING is written (the row is
#     already a question; the daily review drain owns its lapse);
#   * a CORROBORATING match on a confirmed row inside 0.65-0.80 gets ONE
#     chip per (item, THIS meeting): the same transcript again is silent; a
#     different score APPENDS a chip carrying supersedes_seq (history is
#     never edited); a chip the TTL leg resolved is never written again from
#     this meeting. Outside that band nothing is written;
#   * the matcher's STRUCTURAL fences still outrank all of it: a parent with
#     open sub-items is never closed here (SUB1 D3), and a multi-counterparty
#     row keeps its per-person receipt lane.
# The two per-fire fences are unchanged and run INSIDE the function AFTER
# policy: `already_proposed` (one open proposal per commitment, on disk and
# within the fire) and `review_budget` (TITLEMINT1's 25-per-fire cap — a
# volume bound, not a threshold). The `partial_received` and `no_action`
# rows are left alone exactly as before; `commitment_updated` (a schedule
# shift) is written as before with the quote as evidence.
counts = apply_transcript_results(
    workspace_root, results,
    meeting_ref='granola:<THIS meeting id>',
    transcript_ts='<THIS meeting start ts — the same value as transcript_ts>',
    already_proposed=already_proposed,
    review_budget=review_budget,
    batch_id=fire_batch_id,
)
# EODSPEED1 — a COMPLETED walk goes on the ledger: matcher returned, appends
# landed. A pass that raised before this line records nothing and re-walks.
record_walk(workspace_root,
            evidence_ref='granola:<THIS meeting id>',
            evidence_ts='<THIS meeting start ts — the same value as transcript_ts>',
            n_stale=cru_diag.get('stale_evidence_dropped', 0) - stale_before,
            n_results=len(results))
print(f'CRU past-meetings: resolved={counts[\"n_closed\"]} confirmed_closed={counts[\"n_confirm_closed\"]} withheld={counts[\"n_close_withheld\"]} closes_enabled={counts[\"closes_enabled\"]} updated={counts[\"n_updated\"]} chips={counts[\"n_proposed\"]} rescored={counts[\"n_rescored\"]} silent_pending={counts[\"n_silent_pending\"]} silent_out_of_band={counts[\"n_silent_out_of_band\"]} silent_dup={counts[\"n_silent_dup\"]} close_refused={counts[\"n_close_refused\"]} stale_evidence_skipped={cru_diag.get(\"stale_evidence_dropped\", 0)} proposals_suppressed={review_budget.get(\"proposals_suppressed\", 0)}')
"
```

**Carry `proposals_suppressed` to Phase 5 as well (SPEC TITLEMINT1).** It is the last number on that stdout line and it goes on the fire receipt as `n_review_proposals_suppressed`, next to `n_stale_evidence_skipped` in the same `capture_leg` block. Carry `confirmed_closed` as `n_cru_confirmed_closed` (unconfirmed captures the transcript closed as done — the number M asked to keep measurable), `close_refused` as `n_cru_close_refused` (the writer's own refusals — a parent with open sub-items, a missing quote — counted, never reported as closes), `silent_pending` as `n_cru_silent_pending` (rows policy left alone because they were already a question), and — CUT-A — `withheld` as `n_cru_close_withheld` with `closes_enabled` as `cru_closes_enabled` (rows that met every fence and were PROPOSED, not closed, because closing on evidence is off; while the switch is off the receipt and any line built from it say "proposed" / "held" for those rows, never "closed" — `n_closed` and `n_confirm_closed` are 0 by construction and the brief's `closed_from_meetings` line stays silent on its own). Zero is written, not omitted — an absent key reads as "this rail has no cap", which is the state this build ended. Never report it as a threshold effect: nothing was judged too weak to ask about, the fire simply ran out of the room a person has.

**Carry `stale_evidence_skipped` to Phase 5.** The second-to-last number on that stdout line is EVORDER layer 3's refusal count for the whole fire; put it on the fire receipt as `extra_data={"n_stale_evidence_skipped": <that number>, ...}` (the spelling both mail rails use — `reconcile_sent` / `reconcile_inbound` put it in `signal_fields`, and an improvised synonym here is invisible to anyone reading across the three rails). Zero is a legitimate value and is written, not omitted: an absent key reads as "this rail has no fence", which is the state this build ended.

**The stdout is for diagnostic logging only.** Per CONTRACT.md Rule 4 forbidden-pattern list: `commitment_resolved`, `commitment_updated`, and `commitment_review_proposed` event-type names never appear in chat. The user sees the resolution effect on the next Commitments fire — items disappear from the OWED TO YOU / YOU OWE columns when they're auto-resolved here.

**Thresholds:** there are no numbers in this file. The match bar and the pending band live in ONE place — `shared/scripts/commitment_policy.py` (`MATCH_SCORE_AUTO_RESOLVE`, `MATCH_SCORE_PENDING_REVIEW`; `confidence.py` re-exports them) — and a workspace's Loop-4 calibration override (`_hq/data/confidence-overrides.json`) is read there too. A guard test fails the battery when a closer module or this prose spells a literal threshold.

**Failure handling:** if the CRU pass errors (events.jsonl read failure, helper import fails, transcript empty), swallow silently and continue. Phase 4.6 is best-effort enrichment; the Phase 4 commitment writes already succeeded. **Append a `pack_run.data.errors[]` entry** (v3.5.0+) so the failure is auditable via `usage report` even though the user doesn't see it: `{"phase": "4.6_commitment_cru", "reason": "<short>", "detail": "<truncated stderr or exception message>", "meeting_id": "<id>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 4.6.b — Decision CRU pass: auto-resolve / supersede open decisions (v3.4.5+)

Per `shared/scripts/decision_match.py`. Sister to Phase 4.6 but scoped to decisions. After commitment-CRU completes, scan each newly-processed transcript against pre-existing open decisions. The premise: many decisions get executed or reversed in conversation — auto-detecting closes the historical log without the user manually marking decisions resolved/superseded.

**Conservative — HIGH-confidence only.** Threshold is tighter than commitments (0.65 vs 0.55) because decision false-positives lose real history; below it, nothing acts. **Read this together with the recommend-only rule below, which supersedes the auto-close posture this paragraph used to describe:** a high-confidence COMPLETION still auto-resolves, while a high-confidence REVERSAL is proposed for review rather than written. The review surface this paragraph once said did not exist yet is the decision log itself — a proposal renders on the decision's own line.

**MANDATORY — supersedes are RECOMMEND-ONLY (WALKFIX1 FR-2, 2026-08-10). ⚠ M-STRIKEABLE.** This pass no longer auto-writes `decision_superseded`. A high-scoring reversal match now appends a `decision_supersede_proposed` event, which changes NO decision's status and renders as a `[SUPERSEDE PROPOSED]` note on that decision's own line in the decision log, where the person who owns the decision can adjudicate it.

Why: on 2026-08-10 one fire read TWO transcripts and auto-wrote NINETEEN supersedes. Six targeted decisions the same fire had just written (the fence above kills those). Of the thirteen against older decisions, four of four sampled from the live ledger were plainly wrong — a client onboarding call was recorded as reversing an unrelated internal meeting time, an office-lease decision, a different person's login preference and a different client's video platform. The mechanism is structural, not a tuning miss: the score is a whole-transcript overlap coefficient whose divisor is the SHORT TITLE's token set (so any title whose words all appear anywhere in a long call scores 1.0), ANDed with a whole-transcript reversal boolean containing phrases as common as "instead of", with no requirement that the reversal language be anywhere near the matched title. The attendee filter is no second gate — every affected decision named the operator, who is on every call.

A supersede is a WRITE to the canonical decision ledger and a superseded decision drops out of the active view, so a wrong one silently removes a real decision from the customer's "current decisions". Proposing costs one review click; auto-writing costs real history. `decision_resolved` is UNCHANGED — completion language is a different signal and is not implicated. The scoring is untouched; only the write moves.

**MANDATORY — the same-fire circularity fence (WALKFIX1 Item A, 2026-08-10).** Pass `exclude_captured_since=<the same UTC ISO fire_start Phase 4.6 passes>` on EVERY `match_transcript_to_decisions` call. This pass reads the transcripts Phase 4 just extracted decisions from; without the fence the fire scores its own seconds-old decisions against the words they came out of and supersedes them. Field-reported on the 2026-08-10 fire: 8 decisions written, 6 of them superseded by this pass, evidence "Past meeting transcript (reversal language)". The fence drops same-fire captures as candidates before scoring, so they can never be supersede TARGETS; decisions from EARLIER fires are unaffected by this fence and still match normally — what happens to those matches is the recommend-only rule above (they are PROPOSED, not written). Omitting the argument leaves the fence inert — this is the one argument on this call that is not optional.

**Skip entirely if:**
- No newly-processed transcripts in this fire.
- No open decisions in events.jsonl (helper returns `[]`).

Otherwise, for each newly-processed transcript, execute:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from decision_match import (
    load_open_decisions,
    match_transcript_to_decisions,
    build_decision_resolved_event,
    build_decision_supersede_proposal_event,
)
from atomic_write import atomic_append_jsonl

events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_decisions(events_path)

# NO seq peek (BUG-8330 item 7): next_seq=None below — appender stamps in-lock.
to_append = []
for transcript in <list of newly-processed transcripts>:
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=transcript['attendee_person_ids'],
        transcript_text=transcript['text'],
        # WALKFIX1 Item A — the same-fire circularity fence. Same value Phase
        # 4.6 passes. Never omit it: without it this pass supersedes the
        # decisions Phase 4 wrote seconds earlier from these same transcripts.
        exclude_captured_since='<the same UTC ISO fire_start Phase 4.6 recorded>',
    )
    for r in results:
        rec = r['recommendation']
        # Three states, read by identity (POLICY1-B): True / False / None (never assessed) are three different words.
        _sig = r.get('has_completion_signal')
        _sig_word = ('reversal language' if r.get('has_reversal_signal') is True else 'completion language' if _sig is True else 'title match' if _sig is False else 'completion not assessed')
        evidence = f\"Past meeting transcript ({_sig_word})\"
        if rec == 'decision_resolved':
            to_append.append(build_decision_resolved_event(
                decision_id=r['decision_id'],
                primary_thread_id=r['primary_thread_id'],
                source_skill='past-meetings',
                evidence=evidence,
                next_seq=None,  # appender stamps in-lock
            ))
        elif rec == 'decision_supersede_proposed':
            # WALKFIX1 FR-2 — a PROPOSAL, never a closure. See the
            # recommend-only paragraph above.
            to_append.append(build_decision_supersede_proposal_event(
                decision_id=r['decision_id'],
                primary_thread_id=r['primary_thread_id'],
                source_skill='past-meetings',
                evidence=evidence,
                next_seq=None,  # appender stamps in-lock
                score=r.get('score'),
                title=r.get('title', ''),
            ))
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU decisions: resolved={sum(1 for e in to_append if e[\"type\"]==\"decision_resolved\")} supersede_proposed={sum(1 for e in to_append if e[\"type\"]==\"decision_supersede_proposed\")}')
"
```

**The stdout is for diagnostic logging only.** Per CONTRACT.md Rule 4 forbidden-pattern list: `decision_resolved` and `decision_superseded` event-type names never appear in chat. The user sees the resolution effect on the next DECISION_LOG view regeneration — closed decisions filter out of the "Active" list.

**Failure handling:** if the decision-CRU pass errors (helper import fails, transcript empty, JSON malformed), swallow silently and continue. Best-effort enrichment; the Phase 4 decision writes (newly-extracted decisions from this transcript) already succeeded. **Append a `pack_run.data.errors[]` entry** (v3.5.0+): `{"phase": "4.6b_decision_cru", "reason": "<short>", "detail": "<truncated stderr>", "meeting_id": "<id>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 4.7 — Grade the prep brief against the transcript (Phase 6 Loop 3, silent)

If a `Call_Prep_<slug>_*.docx` exists in `_hq/meetings/` for THIS meeting (join by calendar event id / slug — the same slug both call-prep and this orchestrator derive), grade it now: the product wrote a prediction before the meeting; the transcript is the answer key. Best-effort, silent, never blocks processing.

```python
import sys; sys.path.insert(0, "shared/scripts")
from event_gate import append_event
from prep_grading import grade_brief, build_prep_feedback_event
# predicted_sections: {section: [items]} pulled from the prep brief's gradable
#   sections (Talking Points / Risks — Watch-outs / Questions to Ask / Decisions Needed).
# transcript_topics: the salient topics the model reads out of this transcript.
grade = grade_brief(predicted_sections, transcript_topics)  # default token matcher; a smarter matcher may be supplied
ev = build_prep_feedback_event(meeting_id="granola:<meeting_id>",
                               meeting_type="<internal_1_1|external|board|…>",
                               grade=grade, person_ids=[<attendee person_ids>])
append_event("<abs workspace root>/_hq/data/events.jsonl", [ev], holder="past-meetings.prep_feedback")
```

Only meetings that HAD a prep brief are graded (no brief → no `prep_feedback`, nothing to learn from). insight-generator Pass 15 aggregates these monthly and proposes call-prep section-weight changes. On any error, swallow + append a `pack_run.data.errors[]` entry; grading never blocks the fire.

# Phase 4.8 — Non-attendee lane, SHADOW MODE (SPEC GRANOLA1 §B; silent)

The meetings Phase 3.5 classified `non_attendee` run here, and **nowhere else**. They do NOT enter Phase 4: the attended pipeline's routing assumes the user was in the room, and pointing it at other people's meetings is how other people's homework lands on the user's book at scale.

**⛔ THIS LANE IS IN SHADOW. It writes NOTHING.** It fetches the transcript, runs the same admission gates, works out where each item WOULD go, and hands back counts. `meeting_discovery.shadow_fence` withholds every candidate event, so the lane's one append callsite appends nothing. Leaving shadow is a separate decision gated on this report — it is not something this fire, or a fix to something else, may switch on.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from meeting_discovery import run_shadow_pass, render_shadow_report
from cru_match import load_open_commitments
ws = '<workspace_root>'
events_path = ws + '/_hq/data/events.jsonl'
report = run_shadow_pass(
    # one entry per DISCOVERED meeting — attended ones included, so the report
    # can say what the run saw; the lane only enters the non-attendee ones.
    <[{'meeting': <the backend record>, 'items': <the extracted captures for it>,
       'transcript_text': <the transcript already fetched for it>,
       'attendee_person_ids': [<resolved attendees>],
       'meeting_date': '<YYYY-MM-DD>', 'org_id': <resolved or None>,
       'org_name': <resolved or None>, 'primary_thread_id': <resolved or None>}, …]>,
    workspace_root=ws,
    events_path=events_path,
    open_commitments=load_open_commitments(events_path),
    fire_start='<the same UTC ISO fire_start Phase 4.6 recorded>',
)
print(render_shadow_report(report))
print('SHADOW_JSON=' + json.dumps({'counts': report['counts'], 'would_be': report['would_be']}))
"
```

How the lane routes, and why each way (nothing below reaches the open book):

- **Default is the observed tier.** The user is neither owner nor counterparty, so a teammate's commitment is context, not homework: it adds weight to entity history and team intelligence and zero weight to the book.
- **The user named in absentia is the one book-adjacent case, and it is REVIEW only** — `absent_owner: true` plus a pointer back at the meeting. He was not there to agree to it, so it is never auto-confirmed; it renders in the existing grouped queue under the existing caps. No new surface.
- **A dated or money item still always surfaces** — from this lane, surfacing means the queue.
- **Closure evidence from here is a PROPOSAL, never a close.** A teammate saying the user's deliverable "went out" is weaker provenance than the user's own transcript, so the matcher's auto-resolve verdict is demoted to a proposal carrying the transcript `source_ref` and the weak strength class. Transcript AUTO-close stays confined to attended meetings.
- **Ambiguous speaker attribution goes to review, never observed-silent** — merged or mis-labelled speaker labels are a known transcript failure, and an item whose owner cannot be established is a question, not a silent record.

Best-effort, silent, and it never blocks the fire: on any error swallow it, append a `pack_run.data.errors[]` entry with `{"phase": "4.8_nonattendee_shadow", …}`, and carry on. Nothing from this phase is narrated in chat — Phase 6's surface is unchanged by it.

**⛔ MANDATORY (SPEC EODLEG1) — record `capture_leg_end` (UTC ISO) NOW**, closing the window `capture_leg_start` opened at the top of Phase D. Carry both timestamps into Phase 5.

# Phase 5 — Memory updates + THE RECEIPT, written BEFORE the post (silent per Rule 9)

Append to events.jsonl:
- `connector_read` for Granola fetch
- All extracted events (decisions, commitments, follow_ups) — high-conf flagged committed, low-conf flagged pending_review
- `meeting_processed` per meeting
- CRU resolution events (Phase 4.6) — already appended in Phase 4.6 itself; mentioned here for completeness of the audit trail

**⛔ RECEIPT BEFORE POST (SPEC BRIEFFIX1 Item C, M's ruling 2026-08-09; SPEC EOD1 §4).** The receipt is written HERE and the surface posts in Phase 6, in that order, and the order is the point. Both orders lose something when a fire dies in the middle and they do not lose the same thing. Receipt-then-post leaves a receipt and no post — a state this product has always accepted and the next fire can see. Post-then-receipt leaves a NUMBERED surface on screen that the record cannot explain: the watchdog reads it as a fire that never happened, and every one-tap action on it resolves against an OLDER numbering, so a tap lands on whatever used to be at that position. That is the Bug #98 class with a wrong-close on top, and it happened on 2026-08-09.

**This does NOT license stopping here.** The receipt is bookkeeping, not delivery. A turn that logs and never reaches Phase 6 is an incomplete turn — it is simply now an incomplete turn the record can SEE.

**Why the receipt comes after Phase D rather than straight after Phase C:** it carries the capture leg's own window fields (`window_incomplete_before` above all), and those are only knowable once capture has run. Capture is still the fire's final WORK leg; the receipt is bookkeeping and the post is delivery. The read was frozen in Phase C and is not re-derived here.

**⛔ ONE exception, and it is a RECONCILE, not a re-derivation (SPEC MEETCOUNT1): the coverage strip's meetings line.** Phase C stated the APERTURE — what the fire was about to look at — and this fire has now looked. Rendering that Phase-C guess next to the briefs Phase D actually produced is how the surface said "1 on record in that span" above two rendered briefs on a day whose backend held three. Before calling `log_end_of_day_receipt`, run the reconcile so the receipt carries the strip the reader will see:

```python
from end_of_day import meeting_render_set, reconcile_meetings_line
from eod_incremental import prior_briefed_refs
# ONE row per meeting Phase 3.5 returned for the window — the SAME set the
# Meeting briefs section renders from. Status mapping, verbatim:
#   action process + brief saved            -> "briefed"
#   action process + brief_writer MISSING   -> "brief_failed"
#   action skip_processed + ref in the prior-capture set below
#                                           -> "briefed_prior"  (EODSPEED1)
#   action skip_processed otherwise         -> "already_processed"
#   action skip_duplicate                   -> "duplicate_folded"
#   meeting_skipped / personal / internal   -> "skipped"
#
# SPEC EODSPEED1 — the prior-capture set: briefs the silent incremental pass
# wrote since the last day-close. The pass posts nothing by fence, so THIS
# fire narrates them: their rows count as briefed, their briefs render in
# Phase 6 Step 3 (paths from this same helper — one producer), and the
# coverage sentence names them ("captured earlier by the background pass").
# On a day with no incremental pass the helper returns [] and every row maps
# exactly as it always did — the machine-off degrade fence.
prior = {r["source_ref"]: r for r in prior_briefed_refs("<WORKSPACE>")}
render_set = meeting_render_set([{"source_ref": d["source_ref"], "status": <mapped>}
                                 for d in <the Phase 3.5 decisions>])
pack["coverage"] = reconcile_meetings_line(pack["coverage"], render_set)
```

The count and the briefs now derive from ONE producer, so they cannot diverge — and any reduction (a duplicate fold, an already-processed exclusion, a failed brief save, a deliberate skip) is named IN THE SAME SENTENCE. Never subtract a meeting from the stated count without its clause: silent reduction is the bug, whatever the mechanism. Phase 6's Meeting briefs section renders EXACTLY `render_set["briefed_refs"]`, in order — never a list composed a second time.

**⛔ MANDATORY (SPEC COVERQUIET1) — fold the deferral marker onto the pack and settle the render decision, in that order, right here.** `end_of_day.coverage_has_disclosure(pack)` only takes the pack — it never re-fetches, never re-derives — and `window_incomplete_before` is only knowable now, after Phase D. Without this fold the gate would silently miss a real deferral and render a quiet strip on a day one is genuinely owed.

```python
from end_of_day import coverage_has_disclosure, coverage_render_lines

# The SAME value Phase D computed for the batch-cap gate above (`marker`,
# from `catchup.receipt_window_marker`) — None when the fire drained its
# window. Do not recompute it a second way.
pack["window_incomplete_before"] = marker
# SPEC CAPFENCE1 — folded the SAME instant, so coverage_disclosure_lead can
# tell a fence-triggered defer apart from an ordinary batch-cap one. The
# SAME `n_time_fence_deferred` Phase 4's CAPTURE FENCE block computed —
# None (never 0) on a fire where it never deferred anything.
pack["n_time_fence_deferred"] = n_time_fence_deferred  # None or the count

pack["coverage_disclosed"] = coverage_has_disclosure(pack)
coverage_disclosed = pack["coverage_disclosed"]
coverage_lines = coverage_render_lines(pack)   # [] on a quiet day
if coverage_lines:
    from chat_output_renderer import validate_chat_output
    validate_chat_output("\n".join(coverage_lines))
```

**Carry `coverage_disclosed` and `coverage_lines` to Phase 6 — do not recompute either there.** Phase 6 places `coverage_lines` (never `coverage["lines"]` directly) at the position the `coverage` bullet in Phase C names, and ONLY when `coverage_lines` is non-empty — an empty list means the fire places nothing for this block, exactly like every other drop-empty section. `log_end_of_day_receipt` below calls `coverage_has_disclosure(pack)` itself for the receipt's `blocks_rendered` / `blocks_computed_only` split, so the two answers are guaranteed to agree without this file passing a flag into the writer.

**⛔ MANDATORY (SPEC EODLEG1) — record `post_leg_end` (UTC ISO) now, and build the ONE ledger the receipt call below requires.** Everything from `capture_leg_end` (the end of Phase D) to this instant is `end_of_day.PHASE_POST` — the reconcile above plus whatever else this fire still has to do before the receipt is written. `post_leg_start = capture_leg_end`, the same instant reused, exactly as `close_leg_start = fire_start` was in Phase A. This is the window the EODSPEED1 live-test measurement (2026-08-26) found unaccounted for and labelled "downstream + post" — 186,259 ms, 10.04% of that fire — by reading file mtimes after the fact. EODLEG1 exists so the next fire's receipt carries the number instead of needing the archaeology.

```python
from end_of_day import PhaseLedger, PHASE_CAPTURE, PHASE_CLOSE, PHASE_POST

led = PhaseLedger()
# Fold in the pack build's OWN phase record (the fourteen PACK_PHASES the
# Phase C driver already measured) — merge_snapshot is what keeps this
# ledger from silently DISCARDING them: log_end_of_day_receipt's explicit
# `phase_ledger` argument WINS over `pack["phase_timings"]` outright, so a
# ledger that knows only its own three legs would erase the pack's fourteen
# the moment it is passed below.
led.merge_snapshot(pack.get("phase_timings"))
# The three legs THIS FILE measured, each a wall-clock UTC-ISO delta across
# a process boundary — `record_leg`, never `with led.phase(...)`, which only
# works inside one continuous process (see the note in Phase A and Phase D).
led.record_leg(PHASE_CLOSE, <(close_leg_end − close_leg_start) in ms>)
led.record_leg(PHASE_CAPTURE, <(capture_leg_end − capture_leg_start) in ms>)
led.record_leg(PHASE_POST, <(post_leg_end − post_leg_start) in ms>)
```

**Best-effort, and it must never block Phase 5 (§0 Ruling 4: instrumentation never costs the fire its receipt).** `record_leg` and `merge_snapshot` are no-ops on bad input rather than raises, so build the ledger with whichever legs you actually have timestamps for — a leg you cannot time is a leg you skip, not a reason to skip the others. If constructing `led` raises for a reason you did not anticipate, catch it and call `log_end_of_day_receipt` below WITHOUT `phase_ledger` at all: the pack's own `phase_timings` fallback (EODPHASE1) still carries the pack-build phases, and the receipt is owed regardless of what this block could measure.

**ONE call, and it is the End of Day writer — never a hand-rolled receipt JSON** (the hand-rolled `past_meetings`/`cr-past-meetings`/`lateness_tier` drift of FINDINGS F-49/F-50 P2c came from this file's old prose). `end_of_day.log_end_of_day_receipt` wraps `receipts.log_receipt` and writes the SAME `pack_run` shape under the SAME `past-meetings` taskId, so every existing reader — the watchdog, `catchup_window`, `late_fire`, `usage report` — keeps working byte-for-byte:

```python
from end_of_day import log_end_of_day_receipt
log_end_of_day_receipt(
    WORKSPACE_ROOT, pack,                       # the Phase C pack, unmodified
    fired_via=lateness["receipt_fired_via"],    # manual | scheduled | catchup — never guessed
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    capture_leg={
        "window_start": "<the Phase 3 window start>",
        "window_end": "<the Phase 3 window end>",
        # ISO of the oldest still-unprocessed meeting, per the Phase 3 batch-cap
        # gate. OMIT the key entirely when the fire drained its window. The field
        # name is `window_incomplete_before` and nothing else (catchup.py's
        # WINDOW_INCOMPLETE_FIELD is the one spelling — an improvised synonym is
        # invisible to the reader and re-opens the orphaning bug, F-50 P2c).
        "window_incomplete_before": <ISO or omit>,
        # SPEC CAPFENCE1 — the count of meetings the 15-minute capture
        # fence, not the batch cap, left unhandled this fire. `None` (from
        # the loop-top initializer above) means omit the key entirely — the
        # SAME omission rule `window_incomplete_before` keeps, never a
        # written 0 for a fence that never bound.
        "n_time_fence_deferred": n_time_fence_deferred,
        "n_meetings": n_meetings, "n_processed": n_processed, "n_skipped": n_skipped,
        # EVORDER layer 3's refusals across every transcript this fire. Write 0
        # rather than omitting it: an absent key reads as "this rail has no fence".
        # EODSPEED1: this stays THIS fire's own fresh refusals — ledger-honored
        # walks are counted in the two keys below, never folded into it.
        "n_stale_evidence_skipped": cru_diag_total,
        # SPEC EODSPEED1 — the CRU walk ledger's own measurement: how many
        # transcript walks this fire skipped because a complete walk was on
        # the ledger, and how many stale refusals those recorded walks had
        # already made (the 237-row class, honored instead of re-walked).
        # Zero written, never omitted — an absent key reads as "no ledger".
        "n_cru_walks_ledger_skipped": n_cru_walks_ledger_skipped,
        "n_stale_evidence_ledger_honored": n_stale_evidence_ledger_honored,
        # SPEC TITLEMINT1 — how many title-match candidates the per-fire
        # volume cap did NOT propose. Write 0 rather than omitting it: an
        # absent key reads as "this rail has no cap", and a cap without its
        # count is the silence the receipt exists to prevent. NOT a threshold
        # effect — no floor moved, the fire ran out of room.
        "n_review_proposals_suppressed": review_budget_total,
        "capture_counts": routed["summary"],
        "held_routing": routed.get("held_routing"),
        "n_held": routed["summary"].get("n_held", 0),
    },
    # ⛔ MANDATORY (SPEC EODLEG1) — `phase_ledger=led`, the ledger this same
    # Phase built directly above. This is no longer the optional argument it
    # was under EODPHASE1: a receipt whose phase_order lacks capture on a
    # fire that captured is a defect, and the battery's own guard tier
    # (`end_of_day.receipt_missing_capture_phase`) now names it. Passing
    # `led` is what makes `phase_order` describe the whole fire — close leg,
    # capture leg and post, on top of the fourteen pack-build phases —
    # instead of the pack build alone.
    phase_ledger=led,
    extra_data={"errors": [], "nonattendee_shadow": shadow_counts,
                # SPEC EODLEDGER1 — on a degrade-tier fire this is the record
                # that the day-close was DELIVERED rather than withheld, plus
                # the tier's own `degrade_notice` text, which this surface no
                # longer posts. `late_tier` alone can no longer tell those two
                # states apart, because both of them are "degrade".
                "catchup_read": pack["catchup"],
                # SPEC RUNNOW1 — on a `rerun` tier ONLY, and it is what lets
                # the NEXT press render. A receipt carrying `rerun_of` is
                # excluded from the served-slot marker (a re-run is a delivery
                # a PERSON asked for, not the scheduled delivery of a slot);
                # one written WITHOUT it reads as an ordinary scheduled
                # delivery and re-arms the two-hour skip, so the next press is
                # refused — the exact refusal RUNNOW1 exists to remove. Copy
                # `lateness["rerun_of"]` verbatim; OMIT the key entirely on
                # every other tier, exactly as `window_incomplete_before` above.
                "rerun_of": <lateness["rerun_of"], or omit on any other tier>,
                "telemetry": build_pack_run_telemetry(...)},
)
```

The helper derives `confirm_ids` from the pack itself — the numbered map, in the order the surface numbers them — and carries the day-intent PROPOSAL. **Do not type that list by hand.** A surface that renumbers without rewriting the map is the wrong-close hazard, which is exactly why the derivation lives in code and takes the pack rather than a list.

**Carry the Phase 4.8 shadow report onto that SAME receipt** (`extra_data={"nonattendee_shadow": …}`). This is the ONLY record of what the non-attendee lane would have done, and it is what the write-enable decision reads; the lane itself writes nothing, so a fire that drops these counts leaves the shadow run unmeasured. Counts only — never a meeting title, exactly as `meeting_processed`'s `capture_counts` keeps it. Write zeros rather than omitting the key: an absent key reads as "this fire had no lane", which is a different claim.

The receipt is owed on **every completed fire**, including a degrade-tier fire (which since SPEC EODLEDGER1 posts a labelled catch-up read rather than a notice — the receipt logged before that change and it logs now; withholding it is the Bug #98 class) and a fire whose pack came back entirely empty (nothing to place is not an error).

**The receipt's `data` shape did NOT change for any of this.** `log_end_of_day_receipt` still writes the same `pack_run` under the same `past-meetings` taskId with the same keys, `confirm_ids` is still derived from the pack in render order, and a numbered tap still resolves positionally — the coverage strip and the ledger sit ABOVE the numbered section and do not renumber it. Every existing reader (the watchdog, `catchup_window`, `late_fire`, the usage report, the week roll-up) keeps working byte-for-byte.

**SPEC EODPHASE1 (2026-08-22) put per-phase timing on the receipt; SPEC EODLEG1 (2026-08-27) is what made it TRUE about the whole fire, not the pack build alone.** Before this spec, `phase_durations_ms` / `phase_counts` / `phase_order` carried only the fourteen `PACK_PHASES` the driver itself timed — the ledger block above was the OPTIONAL argument that almost no fire ever passed, and the live measurement is what that omission costs: receipt `eod_20260827T003743Z-48e3f23f`'s `phase_order` covered 0.45% of a 1,855,666 ms fire (the EODSPEED1 live-test measurement, 2026-08-26). The block above is no longer optional, and this is why: the partial record when a phase RAISES and no pack comes back at all — the fire that dies in its slowest phase is exactly the one whose numbers matter — and the capture, close and post legs on the SAME timeline as the build, which is what makes one receipt describe one fire instead of a build with two unaccounted neighbours.

**⛔ §0 Ruling 1's fence, verbatim: "a receipt whose phase_order lacks capture on a fire that captured is a defect."** This is not only a style rule: `end_of_day.receipt_missing_capture_phase` is the code half of this sentence, it runs in the battery's guard tier over exactly this shape, and it exists BECAUSE a prose mandate in an orchestrator file is not code the battery can execute — the next edit to this file that quietly drops the ledger block above is caught there, by name, rather than inferred from mtimes a second time.

**Never invent a phase name.** The declared set is `end_of_day.ALL_PHASES`; these names are a vocabulary anything reading these receipts joins on, so a name spelled here rather than there is a number no reader can ever join to. The phases deliberately do NOT sum to `duration_ms` unless you timed every leg into the ledger, and `phase_order` is what says which ones you did.

**SPEC PERSONLOOP1 (2026-08-19) adds exactly one key, and it is additive:** `data.person_candidate_counts` — `{n_candidates, n_rows_blocked, n_top_rows_blocked, n_shown}` for the confirm block's person-candidate rows. COUNTS ONLY, never a name, the same discipline `capture_counts` keeps. The writer derives it from the pack, so there is nothing for this file to PASS. Numbering is unaffected: the candidate rows are APPENDED to `confirm_ids` after the slipped and confirm rows, so every number a pre-PERSONLOOP1 receipt handed out still points at the same row.

⚠ **But there IS something for the fire to remember, and it is in Phase 6.0: the rows have to be RENDERED.** `n_shown` on this receipt asserts that they reached the screen, and `confirm_ids` numbers them whether they did or not — so a fire that skips the third section receipts a render that never happened, in the optimistic direction, and breaks its own numbering claim. This paragraph said "nothing for a fire to remember" when PERSONLOOP1 landed; that was true of the receipt and false of the render, and the second-eyes review (N-1) caught the gap. The render bullet is in Phase 6.0 and it is not optional.

Append to staging_emissions.jsonl per .docx generated. Telemetry writes silently — no chat narration.

# Phase 5.9 — Surface-preference filter (Phase 6 Loop 2 — before rendering)

Drop any surfaced item the CEO has taught the system to stop showing (insight-generator Pass 14 → `_hq/data/surface-preferences.json`): `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "past-meetings", item_class=<the item's class, e.g. "decision_needed"|"open_item">, entity_id=<meeting or person id>)`. Missing store → no-op. Hides the prompt only; the processed meeting + its captured substrate are untouched. Same filter every widget orchestrator applies.

# Phase 6 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

## Phase 6.0 — WHAT THIS FIRE POSTS (SPEC EOD1 — read this before anything below)

**The surface is the End of Day pack from Phase C. It is not a list of meetings.**

Pre-EOD1 this phase posted one widget per fire listing every processed meeting with its pending sub-items. That is the pile M's ruling removes ("the client is never handed a pile"). Everything else in this phase — the renderer pre-flight, the ZERO-MANIPULATION CONTRACT, the transport, the links sections, the H2 opener rules — applies UNCHANGED to the new surface. Only what goes into `data_view` changed:

- **The prose blocks are `pack["screen"]["text"]`, VERBATIM (CUT-PLATE, 2026-09-06)** — the personified intro above it, then the screen exactly as `end_of_day.compose_screen` ordered it in `SCREEN_ORDER` (`pack["catchup"]["lines"]` on the degrade tier · the plate FIRST · `day_went` · `what_it_meant` · `worth_remembering` · `slipped_prose` · `echoes` · `coach` (the delta only) · `tomorrow` (a stated intent as fact) · `sign_off` · then the health lines LAST — `coverage` only when `coverage_has_disclosure(pack)` is True (SPEC COVERQUIET1; the `coverage_render_lines(pack)` result Phase 5 already computed — never `coverage["lines"]` directly), `alarm_lines`, `dark_surface_lines`), and on Monday the week roll-up after it. The placement is code now (Phase C, THE SCREEN paragraph); this bullet carries no second order of its own — two orders in one file is how the v5.28.0 fire went wrong.

**⛔ NO WIDGET. NO QUESTION. THE SCREEN IS THE TURN (CUT-PLATE, 2026-09-06 — M's hold; supersedes SPEC EODSYNTH1 R-2's tomorrow card).** The prose turn is `pack["screen"]["text"]`, verbatim, with the personified intro line above it (persona permitting) and the Meeting briefs / Sources sections below it. This surface never calls `mcp__visualize__show_widget`: no tomorrow card, no Confirm / Edit, no Slipped section, no Needs-your-call section, no person-candidate section, no score, no ledger line. The tomorrow PROPOSAL is computed and receipted and never shown; a STATED intent is already inside the screen as fact; the morning surfaces carry the queues (R-3). A widget posted from this fire is a contract violation regardless of its contents — the v5.28.0 attended test (B2.2) saw the tomorrow card asked on the day-close, and M's hold removed it.

**Nothing is improvised for an empty day either.** No `proposal` and no `intent` → the screen carries `tomorrow["line"]` (*"Nothing on file yet for tomorrow."*) and that is the whole of it. Never an all-clear card, never a hand-built widget.

**The meetings this fire processed** contribute their `.docx` links to the `Meeting briefs:` section below and NOTHING ELSE. No meeting rows, no per-meeting sub-items, no counters widget. Their ambiguous items are already in the queue and reach the CEO through the MORNING surfaces.

**The development-read slot** (Monday) renders NOTHING. Not a heading, not a placeholder.

**Friday** posts a plain day-close. No hand-off line to the Friday Wrap.

**`confirm_ids` IS EMPTY AND THAT IS THE CONTRACT.** `end_of_day.confirm_ids_from_pack` walks `NUMBERED_BLOCKS`, which is `()`: the evening numbers nothing because it renders no numbered rows. Do not number the tomorrow moves into it — the confirm resolves through `end_of_day.resolve_intent_confirm` off `day_intent_proposal` on the same receipt and never used the map. **Never number a row you are not rendering**, and never render a row the pack did not hand you: a map entry for an invisible row makes every tap past it resolve against something nobody saw, which is the PERSONLOOP1 N-1 finding.

**The empty day still posts.** No proposal and no intent means no widget at all, and the screen is the whole turn: `pack["screen"]["text"]` — the plate's zero line, whatever the day-went paragraph could honestly say, the sign-off, and the coverage strip after it when it has a disclosure. **The coverage strip renders on an empty day IFF it has a disclosure (SPEC COVERQUIET1)** — "nothing happened" and "I could not look" are two different claims and, when the fire genuinely could not look (a reduction, a deferral, a dark surface, a connector gap, a catch-up note), the strip is what tells them apart. A day that is empty AND fully covered — nothing happened and every capability read clean — says so through the day-went paragraph and the sign-off alone; a boilerplate "nothing to report" coverage strip under an already-honest empty day is the noise this spec removes.

## Phase 6.2 — the tomorrow tap (SPEC BK1 writer, EOD1 caller; positional pick per SPEC TOMPICK1) — DORMANT on this fire since CUT-PLATE

**This fire offers no tomorrow tap (CUT-PLATE, M's hold 2026-09-06).** The proposal is never rendered, so there is nothing on screen to confirm or edit, and a reply of "confirm" / "1" is not a route this fire invites. The CEO states tomorrow with `tomorrow is about [X]` (workspace-manager, BK1), which writes the intent directly and is what the next morning and the coach's delta read. The resolver below is kept, unchanged, for the receipt's data (`day_intent_proposal`) and the on-demand path; do not remove it and do not re-render the draft to give it something to resolve.

When a confirm DOES arrive on the on-demand path: **the CEO is picking ONE of up to three ranked candidates, positionally** — a bare "confirm" means rank 1, and a bare digit ("1", "2", "3") means that rank instead. Parse the reply for a leading digit before falling through to "confirm"; anything else that isn't "edit"/"change" is not this route. On confirm:

```python
from end_of_day import resolve_intent_confirm
from day_intent import write_from_proposal
# pick=None for a bare "confirm" (rank 1); pick=<int> for a bare "1"/"2"/"3".
res = resolve_intent_confirm(WORKSPACE_ROOT, pick=<the digit typed, or None>)
if res["ok"]:
    write_from_proposal(WORKSPACE_ROOT, res["proposal"],
                        origin="wrap",                 # the CEO tapped: it is stated now
                        source_ref=res["source_ref"],  # session:<receipt id>:confirm
                        source_skill="past-meetings")
else:
    ...  # say res["refusal"] verbatim and write nothing — an out-of-range
         # pick ("4" when only 2 candidates were offered) refuses the same
         # way a stale map does, never clamped to the nearest real one.
```

**Pass the PROPOSAL, never a list of its texts.** `write_from_proposal` takes the resolver's output whole, so the item keeps the `commitment_id` it was drafted from and the morning brief can join tomorrow's stated intent back to the open book. The shape this replaces handed `write_day_intent` a plain list of the items' `text` values, which dropped every id on the way in — and it dropped them because this file used to instruct exactly that. The `for_date` comes off the proposal too: re-resolving "tomorrow" at write time files a tap that lands either side of midnight under the wrong day.

**`resolve_intent_confirm` already narrows the proposal to the ONE chosen candidate, re-ranked to 1, before it reaches this call** (SPEC TOMPICK1 §0.3) — `write_from_proposal` and `day_intent.write_day_intent` underneath it are UNCHANGED and always write exactly what they are handed. The widening from one candidate to up to three lives entirely in `compute_tomorrow` and in `resolve_intent_confirm`'s positional resolution, never in the writer: **one intent is written, every time**, whichever candidate the CEO picked.

`origin="wrap"` because a tap is the CEO's own word. The pre-confirm draft is `origin="proposed"`, exists transiently, and is NEVER written silently and NEVER rendered as a statement of fact — `load_day_intent` skips proposed rows by default, so a surface cannot render a guess as the CEO's intent by forgetting a flag. On "change", take the CEO's sentence and write it the same way (`write_day_intent(..., origin="wrap")`); do not merge it with the draft.

## Phase 6.3 — the ask MOVED to the morning (SPEC EODSYNTH1 R-3, superseding SPEC OVERDUE1's evening step)

**There is nothing to call here any more, and this section exists to say so out loud rather than to disappear.**

Pre-EODSYNTH1 this phase called `end_of_day.mark_slipped_asked` after the post, to record that the evening had asked the CEO about an overdue row. M's ruling moves that question to the MORNING: the confirm/drop queues leave the evening, and the ask-once marker is written by whichever surface asks. The driver now builds this fire's slipped block with `ask=False`, so `pack["slipped"]["asked_ids"]` is EMPTY and calling the writer would write nothing — but calling it would also be a claim that this surface asked something, and it did not.

**Where the rule lives now:** `end_of_day.apply_overdue_ask` (the shared verdict, called by both bookends) and `end_of_day.mark_lane_asked` (the write), invoked from `orchestrator-morning-brief.md` Phase 6.1. The threshold, the fork label, the rest-until-answered fold and `commitment_state.mark_asked` are all byte-identical; only the surface changed.

**What this means when you fire:** nothing to do. Do not call `mark_slipped_asked`, do not compose a "Done, new date, or drop?" question of your own, and do not mention the move in the chat post. If you find an overdue row that looks like it wants asking about, the morning is where it gets asked.

---

**Mandatory execution contract (v2.10.8+):**

You MUST execute the renderer via `mcp__workspace__bash`. You MUST NOT hand-write or paraphrase the chat string. There is no "Example rendered output" in this file by design — earlier versions included one and the LLM (you) paraphrased it instead of running the Python.

**Step 1 — verify renderer imports (FIRST action of Phase 6, before anything else):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from widget_transport import render_and_persist; from chat_output_renderer import validate_chat_output, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError, WrapperContractError; from brief_path import get_brief_path, get_brief_artifact_url; print('OK')"
```

If stdout is not exactly `OK`, ABORT the fire. Surface plain English: `(Renderer pre-flight failed — chat output deferred. Diagnostic: <error>.)` Do NOT post any widget.

**⛔ ZERO-MANIPULATION CONTRACT (v2.14.34+, transport-updated EW2+T):** the render is sealed — post via `widget_transport.render_and_persist` and pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed or post-processed HTML. No minification, no whitespace stripping, no "trimming for size", no removing what looks like duplicate elements — not on `transport["html"]`, not on the persisted file. Every `<div class="cr-action-input">` wrapper is functionally required. The transport runs `validate_rendered_widget` internally and raises `WrapperContractError` if any wrapper is missing.

**v2.14.37+ extension (EW2+T) — `show_widget` mandatory after a clean transport call.** If `render_and_persist()` returns without raising, you MUST call `mcp__visualize__show_widget` with `transport["html"]` as `widget_code`. Narrating that the widget "couldn't transmit," "hit a session payload limit," "exceeded the live widget surface," "was too large," or any other reason is FORBIDDEN — none of those phrases exist in this codebase, and pagination (~10 rows/page) keeps every page inside the relay budget. If `show_widget` itself errors, surface the error string verbatim and STOP.

**v2.14.37+ extension — markdown lists are not a substitute for widget rendering.** Any "show me the X" / "surface the Y" / "list the Z" follow-up goes through `render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`). Markdown bullet lists in chat as a substitute are FORBIDDEN even when the user explicitly asks for one.

See `orchestrator-commitments.md` "ZERO-MANIPULATION CONTRACT" section for the full diagnosis lineage (Cowork's 2026-05-07 structural diagnostic, v2.14.34 D1 root cause + v2.14.37 narrate/markdown bypass closures).

**v2.13.0 enforcement:** renderer raises `CanonicalActionError` on non-canonical verbs (e.g., `[your call]` is not canonical — use `decide [text]`; `manually` is not canonical — use `add context [text]`; `search emails` was dropped). Raises `LeakDetectedError` on forbidden patterns. Both blocking; fix the data view.

**Empty-state rule (v2.14.19+) — RETIRED, NOT RUN since CUT-PLATE (2026-09-06).** The empty day renders the screen only (see "Nothing is improvised for an empty day either" above): no all-clear widget, no counters, and the fire never names itself "Past Meetings". The paragraph below is kept for history and is not an instruction. ~~ if zero meetings happened in the Phase 3 window (or all of them resolved cleanly with no pending sub-items), DO NOT improvise a "no meetings to process" widget by hand-typing HTML. Build `data_view = {"widget_mode": "all_clear_summary", "header": "Past Meetings — nothing to process", "sub_header": "<weekday>, <date> · <time> check", "counters": [{"label": <the window label: "Last 24h" on a normal fire, "Since <weekday>" when Phase 3 returned `extended: true`>, "value": n_meetings}, {"label": "Auto-processed", "value": n_auto}, {"label": "Pending review", "value": 0}, {"label": "Skipped", "value": n_skipped}], "summary_line": "All transcripts were either auto-processed cleanly or skipped (internal/personal). Nothing pending your call.", "tracked_items": [], "footer": None}` and pass to `render_chat_output_widget()`. NEVER hand-build the empty-state widget. The counter label states the window that was actually searched — a widened catch-up window labelled "Last 24h" is a false claim about what was looked at. See `orchestrator-commitments.md` for the full diagnosis (v2.14.18 fresh-install bug).

**Step 1b — Claim audit (v4.6.1 S3, MANDATORY — count from disk before ANY surface speaks; F-50 P2a: this widget + its summary claimed 7 decisions while disk had 6).** Same contract meeting-notes ships (its Step 9a3), same shared primitive:

```python
import sys; sys.path.insert(0, "shared/scripts")
from meeting_capture import count_meeting_writes

# once per processed meeting, AFTER all Phase 5 appends
counts_by_meeting = {m["source_ref"]: count_meeting_writes("<WORKSPACE>", m["source_ref"])
                     for m in meetings}
# each -> {"meeting": 1, "meeting_processed": 1, "decision": 2, "commitment": 4, "person_proposal": 1, ...}
```

Every number ANY Phase 6 surface renders — the widget header counts, each meeting item's "N decisions / N commitments" lines, the quick_read enumeration, and the `pack_run` receipt's counts — comes from `counts_by_meeting`, never from extraction intent. If a count is lower than what Phase 5 attempted, a write FAILED: say so plainly in the quick_read ("captured 3 decisions for the Bowie call but only 2 saved — say 'process the call Bowie' to retry the missing one") and never render the failed item as logged. The regression suite for the primitive lives with meeting-notes (`run_meeting_notes_writer_parity_test.py`) — this paragraph is the surface half of F-50 P2a.

**Step 2 — build data_view, render widget HTML, post via show_widget (v2.10.9+):**

**Since CUT-PLATE this snippet is NOT RUN on the day-close — there is no widget (Phase 6.0).** It is kept as the mechanics any future row-list on this surface would use — the transport call, the persist dir, the name hint — and nothing else. The pre-EOD1 per-meeting `sections` is retired with the pile; `item_for_meeting` is no longer called from this phase.

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist

data_view = <the Phase 6.0 data_view: source_skill "end-of-day", the two row-list sections in pack order>

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="end-of-day")
# EW2+T (F-15): the transport runs the full validator chain (canonical
# actions, data shape, leak scan, wrapper contract) and persists the sealed
# render. Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) — never
# a hand-composed variant, never a post-processed one.
```

**(Still inside the NOT-RUN Step 2 block.)** The widget rendered inline with per-item buttons; user clicks accumulated locally; "Apply all" fired an `apply choices: [...]` payload that `apply-choices` caught. **The day-close posts no widget since CUT-PLATE (2026-09-06)** — kept as the mechanics of a row-list surface, not as an instruction for this fire.

**Step 3 — Post the chat-links section (v2.14.0+ — split Briefs vs Sources):**

The chat-links section is posted on its own (**there is no widget to post it after — CUT-PLATE, 2026-09-06**), as TWO separate markdown sections. Per M's v2.13.2 ask: *"the brief hyperlink should just have the name of the meeting. And what's underlined ('Sam UX review continuation') is sending you to granola, which should be sources, not links."*

Format:

```markdown
**Meeting briefs:**

1. [Sam — UX review (continuation)](computer:///<URL-encoded-absolute-path-to-.docx>)
2. [Sam — Scheduled tasks walkthrough](computer:///<URL-encoded-absolute-path>)
3. [Quinn — Chaletteholdings onboarding](computer:///<URL-encoded-absolute-path>)

**Sources:**

- [Granola — Sam UX review (Apr 29)](https://notes.granola.ai/d/<note_id>)
- [Granola — Sam scheduled tasks (Apr 29)](https://notes.granola.ai/d/<note_id>)
- [Granola — Quinn Chaletteholdings (Apr 29)](https://notes.granola.ai/d/<note_id>)
```

**Section label is `Meeting briefs:` (v2.14.36+) — NOT `Meeting prep:`.** Briefs are post-meeting recaps; prep is forward-looking. Past-meetings produces briefs, not prep — the label MUST match. Pre-v2.14.36 the label was `Meeting prep:` which created semantic confusion (the same label appeared on upcoming-meetings prep docs). M's 2026-05-07 testing flagged it: "this is not prep it is a post meeting brief." DO NOT freelance the label as `Briefs:` (too generic), `Brief documents:`, `Past meeting briefs:`, etc. — the canonical label is exactly `Meeting briefs:`. Identical text, identical capitalization.

**Meeting briefs section rules:**
- **The row set is `render_set["briefed_refs"]`, in order, and nothing else (SPEC MEETCOUNT1).** The same `meeting_render_set` return that reconciled the coverage strip's meetings line in Phase 5 is the ONE producer this section draws from — one brief per ref, no ref skipped, no brief added. A briefs list composed independently of the render set is the two-producer split that let the strip say "1 on record" above two rendered briefs.
- **A `briefed_prior` ref's path and title come from the SAME `prior_briefed_refs` return Phase 5 mapped it from (SPEC EODSPEED1)** — `prior[ref]["brief_path"]` / `prior[ref]["title"]` — never re-derived, never re-looked-up: the helper is the one producer for that lane, exactly as this fire's own Phase 4 step-7 cache is for the `briefed` lane. These briefs render identically to this fire's own (same H3 link form, same opener URL rules); the coverage sentence has already named where they came from.
- Each item numbered in its own order (the pre-CUT-PLATE rule was "numbered to match the widget"; there is no widget on this fire).
- Anchor text = meeting name (resolved attendee + topic). NOT the generic word "brief." The attendee half is the binder's verdict (SPEC BRIEFBIND1): `meeting_capture.brief_counterparty` over the cited meeting's own record — an unbound brief anchors on topic alone, never on an association-borrowed name.
- Click target = the .docx brief at `_hq/meetings/Past_Meeting_<slug>_<date>.docx` via `computer:///`.
- If a meeting has no brief (rare — only for skipped meetings that didn't generate one), omit that line.
- If 0 meetings have briefs, omit the entire Briefs section.

**Sources section rules:**
- Bullet list, no numbering (sources don't need 1:1 mapping to widget items).
- Anchor text = "Granola — \<meeting title\> (\<date\>)" or similar source-specific format.
- Click target = the underlying transcript / source URL (Granola, calendar event, etc.).
- If 0 meetings have linkable sources, omit the Sources section.
- If both Briefs AND Sources are empty, omit the entire post-widget block.

**Brief save path enforcement (v2.14.0+):** every brief file MUST be saved via `shared/scripts/brief_path.py` `get_brief_path(workspace_root, "past_meeting", slug, date_iso)`. The orchestrator's bash gate calls this helper to compute the absolute path BEFORE invoking the docx skill. After the docx skill returns, the orchestrator verifies the file exists at the expected path; if not, surfaces a plain-English error and excludes that meeting from the Meeting briefs section (no broken links).

The `artifact_link.url` on each item IS the same `computer:///` URL used in the Meeting briefs section. Inline-in-widget link + post-widget Briefs link both point at the same file.

**Step 4 (v3.13.0+ — H2 heading link primary; present_files DEMOTED):**

Per CONTRACT.md Rule 3 (v3.13.0+) and M's 2026-05-20 testing #29: `mcp__cowork__present_files` cards don't reliably open most file types on primary click. So `present_files` is no longer the opener. The post-widget `Briefs:` section emits H3 heading links (multi-doc) per `doc_headline_link_h3()` — those native `computer://` links ARE the opener.

```python
from chat_output_renderer import doc_headline_link_h3
from brief_path import get_brief_opener_url

# After the widget, render one H3 heading link per brief beneath a single
# Briefs: section header. H3 (not H2) because past-meetings often surfaces
# several briefs at once; stacked H2s would visually dominate.
# v5.9.2 (platform-neutral v5.11.1) — get_brief_opener_url is cloud-aware: on
# a cloud-mounted workspace (session-scoped path) it returns the file's web
# link — Google Drive, OneDrive, or SharePoint, whichever hosts the workspace
# — when one was resolved (see the BRIEF_SESSION_SCOPED step above); on a
# host-native workspace it returns the same computer:// form as before.
print("**Briefs:**")
print()
for brief in briefs:
    url = get_brief_opener_url(brief.absolute_path, brief.drive_web_url)
    label = f"Past Meeting Recap — {brief.meeting_title}"
    print(doc_headline_link_h3(label, url))
```

`present_files` is OPTIONAL post-v3.13.0. The 2026-05-20 testing settled the format question: native `computer://` links open reliably; cards don't. If you include `present_files`, position it AFTER the H2/H3 links as a reveal-in-folder convenience. Default: skip — the H2/H3 links are sufficient and doubling up adds noise.

If the native `computer://` link fails for the user despite v3.13.0's native-form fix (rare — would indicate a regression of #19), surface a one-time per-session troubleshooting line: *"If the link above doesn't open, check that you're on the latest Cowork desktop build — older builds may have a `computer://` resolver bug."* Don't fall back to cards as a hidden second surface; cards don't work either.

Replaces v2.10.x's single-surface `present_files`-only design AND v2.12.0–v2.13.x's single-surface markdown-only design. v2.14.0+ uses BOTH.

File save location (v2.10.8+): `[Project]/meetings/Past_Meeting_<slug>_<YYYY-MM-DD>.docx` — same place a user would find them via `go [project]`.

**Per-meeting item shape (v2.12.4+ — multi-person split into separate sub-items, search emails dropped, artifact_link inline):**

```python
{
    "n": 1,
    "icon": None,                              # past-meetings doesn't use envelope/calendar icons
    "name": "Sam Sample",
    "subject": "Q2 deck review",
    "context_tag": "11:30 AM today" if newly_processed else "yesterday 12:50 AM · re-processed",
    "body_lines": [                            # Summary as bullet lines per Rule 10
        "- Sam agreed to the Q3 launch date",
        "- Asked for a refreshed margin model by Friday",
        "- Tabled the partner-tier discussion until next week",
    ],
    "sources": [{"label": "Granola transcript", "url": "https://notes.granola.ai/d/abc123"}],
    "artifact_link": {                         # v2.12.4+ — renders inline INSIDE the widget AND in post-widget Links
        "label": "Open full meeting brief",
        "url": "computer:///<URL-encoded-absolute-path-to-Past_Meeting_Sam_2026-04-29.docx>",
    },
    "sub_items": [                             # pending review items (1a, 1b, ...) — v2.12.6+ shape
        # Multi-person items: each gets its OWN sub_item. NEVER stack as competing actions.
        # Per M's Apr 30 ask: "I am trying to add both people with the same first name but it does not
        # let me select" — one action per item rule means multi-person needs multi-item.
        # When the org is known, bake it into the action label (no [org] placeholder).
        {
            "id": "1a",
            "summary": "Rio Sample — new person mentioned by Sam (project manager).",
            "actions": ["1a add [text]", "1a not relevant"],   # v2.14.38+ — REVIEW unified set (MLK1 retired the `add to my list` defer; not answering defers naturally): `add [text]` opens a textarea (empty = add as inferred / Rio Sample → Summit Company; non-empty = fold corrections, e.g., "actually this was Rio Lange speaking — attribute to him"). `not relevant` 60-day cooldown. Replaces the v2.12.6 `add as person to <Org>` + skip cluster which couldn't handle speaker-attribution corrections without a separate textarea.
        },
        {
            "id": "1b",
            "summary": "Rio Lange — new person mentioned by Sam (project manager).",
            "actions": ["1b add [text]", "1b not relevant"],
        },
        # Vague-timing sub-item — `set date [when]` opens a free-text input on click
        {
            "id": "1c",
            "summary": "Vague timing: \"let's revisit in a few weeks.\" (no specific commitment).",
            "actions": ["1c set date [when]", "1c not relevant"],   # v2.14.38+ — `not relevant` (60d cooldown) replaces `skip` (24h dismissal). MLK1 retired the `add to my list` defer.
        },
        # Decision-needed sub-item — `decide [text]` opens a textarea on click
        {
            "id": "1d",
            "summary": "Decision needed: should we route Aspen / CHS Limelight as its own project or add as a one-off mapping?",
            "actions": ["1d decide [text]", "1d not relevant"],
        },
        # New-org candidate sub-item — v2.14.38+ uses the unified REVIEW
        # `add [text]` action with the candidate name baked into the summary
        # text (no longer in the action label). The textarea lets the user
        # correct the inferred relationship type or any other field.
        {
            "id": "1e",
            "summary": "Acme Co — new org candidate (Quinn's email is @acme.example.com; 5 recent threads reference setup).",
            "actions": ["1e add [text]", "1e not relevant"],
        },
    ],
    "actions": [],                             # meeting-level has no actions; sub-items handle them
    "annotations": ["✓ Auto-committed: 2 decisions, 1 commitment, 1 follow-up draft"],
}
```

**Multi-person sub-item rule (v2.12.4+):** When the meeting mentions N new people who could each be added separately, render N separate sub_items (`1a`, `1b`, ...), each scoped to ONE person. NEVER stack them as alternative actions on a single sub_item — the widget's one-action-per-item rule prevents the user from selecting multiple, so stacking forces an artificial choice. Each person gets their own row.

**Org-inference rule (v2.14.29+ — HARD CONTRACT for new-person sub_items):** Per M's testing 2026-05-06 (item #12), past-meetings widgets surfaced "Add as person to" buttons WITHOUT any org name — customer couldn't tell what org the person would be added to. Cowork's diagnostic confirmed root cause: the orchestrator was emitting the `[org]` placeholder form even when the org WAS knowable from the meeting's existing context, instead of the specific-name form. The renderer correctly strips `[org]` per its rules, leaving the label as "Add as person to" with no destination.

**Default-to-attendee-org rule (v2.14.29+):** when generating a new-person sub_item from a past meeting, the org for the new person defaults to the org that the meeting's primary attendee is already mapped to in entities.json. ONLY emit the placeholder form `add as person to [org]` if BOTH conditions hold:

1. The new person genuinely doesn't appear to belong to ANY org the meeting's attendees are mapped to (e.g., transcript mentions "Rio at Acme" but no Acme attendee is on this call), AND
2. No email domain in the meeting's connector data resolves to an existing org_id

For ALL other cases, emit the specific-name form `add as person to <Org>` using the attendee's org as the default. Acceptable mappings:

| Signal | Org to bake into the action label |
|---|---|
| New person mentioned by name in transcript, primary attendee is from Org X | `add as person to <Org X>` |
| New person's email domain resolves to existing org_id | `add as person to <that org>` |
| New person mentioned by name + signature block names a specific org | `add as person to <that org>` |
| Multiple plausible orgs (attendee from Org A, transcript mentions Org B) | Pick the attendee's org as default, surface alternatives in the sub_item summary so user can override via the per-sub-item "+ Add context" toggle (v2.14.36+) |

Only when ALL signals fail does the placeholder form `[org]` fire. This also means the placeholder form is now the EDGE case, not the default — most past-meetings sub_items should bake the org name in.

**Renderer support for `[org]` placeholder (v2.14.29+):** when the placeholder form does fire, the renderer's `_detect_input_type` now recognizes `[org]` and exposes a single-line text input on click — same pattern as `[when]` for date/time. So if the customer sees "Add as person to org" (placeholder display label) and clicks it, a textbox drops down where they type the org name. Pre-v2.14.29 the placeholder was a dead button with no input affordance — clicking just toggled selected state with no way to record the org. Fixed.

Same rule applies to any case where multiple distinct entities surface together (e.g. two new orgs, three new projects, etc.).

**Sub-item summaries are USER-VISIBLE in v2.12.4+** (per M's Apr 30 ask: connect the action buttons to the email body's numbered items). The renderer now displays the `summary` field as visible text next to each sub-item's action row. So write summaries as plain-English context the user can scan in 2 seconds.

**Action label changes (v2.12.4+):**
- Dropped: `search emails` (per M's Apr 30 ask: not necessary). User can fire `tell me about [name]` directly if they want a deep cross-reference.
- `manually` → `manually [context]` — exposes a textarea on click so user can type any context (org, role, where they met, etc.) before the entity-creation flow runs. Per M's Apr 30 ask: *"If i select add manually it should open up a box for me to type context into"*.

**Pre-build resolution rules:**
- Resolve every entity ID to canonical name (no `org_NNN`, `person_NNN`, `event_NNN`, `project_NNN`)
- Re-run phrasing: "re-run" / "re-processed" — never "force re-emit" / "force re-emitted" / "(seq 128-136)"
- Skipped meetings list goes in `save_confirmation` field, NOT as a separate item
- The `artifact_link` per meeting carries the absolute path of the docx-skill-produced .docx, used for the post-chat `mcp__cowork__present_files` call (v2.10.8+). The renderer no longer emits a per-item `📄 [Open full brief]` markdown line — cards from `present_files` are the surface.

**No example rendered output is included by design (v2.10.8+).** Read `shared/scripts/chat_output_renderer.py` if you need to understand the output format — but never paraphrase from any rendered example you find anywhere. Execute the renderer; post what it returns.

**Per-meeting first-line shape:**
- `[N]. [Attendee or Org Display Name] · "[Meeting title]"` for newly-processed
- `[N]. [Attendee or Org Display Name] · "[Meeting title]" · re-processed` for re-runs (NOT "force re-emit")

**Per-meeting block structure (rule 4 + rule 10):**
- Blank line between meetings
- Blank line between summary bullets and the `✓ Auto-committed:` line
- Blank line between `✓ Auto-committed:` and the `Source:` link
- Blank line between `Source:` and the `⚠ Needs your call:` block (if any)

**Pending review items (per Rule 5 + IDX action token, v2.10.5+ format):**

Visual rule: ONE LINE for the issue description, then a blank line, then ONE LINE for the action shortcuts. No "Reply:" label prefix — indentation already differentiates the action line. Drop redundant subjects in actions ("add Lyra Sample to [org]" → "add to [org]" — the name is in the description above, no need to repeat). Drop trailing "Add or skip?" / "Set a real time?" prompts in the description since the action set IS the answer to that question.

- Sub-IDs are `[N][a/b/c]` — global meeting numbering plus a sub-letter per pending within that meeting
- Action set per pending type (v2.14.38+ — REVIEW unified set: single permissive `add [text]` / `set date [when]` / `decide [text]` affirmative + `not relevant` (60d cooldown). MLK1 retired the `add to my list` indefinite defer — an unanswered pending re-surfaces on a later fire. Replaces the v2.12.6 `add as person to <Org>` + skip / v2.14.5 separate context cluster):
  - **Missing person:** `▸ IDX add [text]  ▸ IDX not relevant` — `add [text]` opens a textarea pre-populated with the inferred fields (`Person: Rio Sample / Org: Summit Company / Source: Sam mentioned as PM`). Empty input adds as inferred; non-empty input folds corrections (e.g., "actually this was Rio Lange speaking; attribute to him" → re-attributes the new person record at create time).
  - **Missing org (new org candidate):** `▸ IDX add [text]  ▸ IDX not relevant` — same textarea pattern. Pre-populated with inferred org name + relationship type + signal.
  - **Vague timing:** `▸ IDX set date [when]  ▸ IDX not relevant` — `set date [when]` keeps its specific verb since it's about pinning a date, not an entity.
  - **Decision needed:** `▸ IDX decide [text]  ▸ IDX not relevant` — `decide [text]` keeps its specific verb since the decision text is the captured artifact.
  - **Sensitive decision (auto-flagged):** `▸ IDX decide [text]  ▸ IDX escalate to memo  ▸ IDX not relevant` — adds escalate.

**Single context affordance per item (v2.14.36+):** every item and every sub-item carries ONE collapsible "+ Add context" toggle button (rendered by chat_output_renderer.py). Pre-v2.14.36 had two affordances: the per-action `IDX add context [text]` button (per-action context capture, fired with that specific action) AND the always-visible per-item note textarea (per-item, fired with whichever action gets selected). M's 2026-05-07 testing flagged the duplication: "duplicate." v2.14.36 collapses to ONE: the per-item collapsible toggle (button hidden until clicked, textarea revealed on click, captured as `context` field in apply-choices payload alongside whichever action the user selects). Cleaner UX, same captured semantic.

**Action label specificity rule (v2.12.6+):** when the orchestrator KNOWS what org / project / etc. is being targeted, bake the name into the action label so the button reads `Add as person to Acme Co` not `Add as person to [org]`. Per M's Apr 30 ask: *"the add to org button should say what the org you are adding to is."* Only use the bracket placeholder `[org]` when the org genuinely isn't determined yet (truly unknown), in which case clicking the button opens a textarea for the user to type the org name.

**Commitment date-prose rule (v2.14.19+ — REQUIRED, no exceptions):** when composing the `summary` text for any pending sub-item that's about a commitment with a stored `data.due` (or `data.due_date`) field, the prose MUST:

1. Read the stored value VERBATIM from the commitment event in events.jsonl.
2. Compute "overdue" / "due today" / "due in N days" / "due [day-of-week]" against TODAY in the workspace timezone (use `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` for the comparison anchor — never UTC, and `workspace_path` is REQUIRED in v3.11.1+).
3. NEVER re-derive a date from prose phrases like "tomorrow relative to May 2" or "by Friday relative to Tuesday's call." Those phrases are extraction-time hints; once `data.due` is stored, the stored value is authoritative.
4. NEVER call a stored due_date "now past" if it equals today's local date. Today-due is `due today`, not past.

Concrete examples:

- Commitment with `data.due: "2026-05-04"` and today is 2026-05-04: summary says `"...due today"`.
- Commitment with `data.due: "2026-05-05"` and today is 2026-05-04: summary says `"...due tomorrow"`.
- Commitment with `data.due: "2026-05-02"` and today is 2026-05-04: summary says `"...2 days overdue"` or `"...due Friday — overdue"`.
- Commitment with `data.due: ""` (no due date) AND `data.meeting_date: "2026-05-03"` AND a relative-phrase hint in `data.evidence` ("tomorrow"): summary says `"...vague timing — set a real date"` and routes to the **Vague timing** sub-item shape. Do NOT compose a "tomorrow relative to May 3" string; the stored `data.due` is empty, so the right surface is the date-input action, not a prose paraphrase.

This rule was added after a v2.14.18 cross-meeting fusion bug surfaced where the prose `"tomorrow relative to May 2 — that date is now past"` contradicted the stored `data.due: "2026-05-04"` (which was today). The renderer painted exactly what it was given; the bug was in the orchestrator's prose composition. See `_hq/.simplify-findings/CHANGES_v2.14.19.md` for the full diagnosis.

**Person vs org distinction (v2.12.6+ + v2.14.5 specific-name parity):** when a meeting mentions someone whose name suggests a NEW PERSON, the action is `add as person to <Org>`. When the same signal generates a new ORG candidate (org name with no entity record), the action is `add as new org <Org Name>` when the name is inferable, else `add as new org`. Per M's Apr 30 ask: *"tate was a person not an org. We need to make sure this is clear to user."* The verbs distinguish: `add as person` vs `add as new org` — never just `add to`.

**Display labels** at render time (Title Case applied automatically):
- `add as person to Acme Co` → button reads `Add as person to Acme Co`
- `add as person to [org]` → button reads `Add as person to org` (with textarea for org name on click)
- `add as new org Acme Co` → button reads `Add as new org Acme Co` (specific-name variant, v2.14.5+)
- `add as new org` → button reads `Add as new org` (fallback when name not inferable)
- `add context [text]` → button reads `Add context` (with textarea for context on click)
- `decide [text]` → button reads `Decide` (with textarea for the user's decision text on click)
- `set date [when]` → button reads `Set date` (with free-text natural-language input on click)

The `IDX [your call]` shorthand prompts the user to type their decision inline (e.g., `3a push to OneDrive` or `3b primary email is tate@acme.example.com`) — the bot parses the text after the IDX as the user's choice. No multiple-choice menu of pre-baked options because real decisions rarely fit a fixed list.

**Action verb shortening rule (v2.10.5+):** in pending-item action lines, prefer 1-2 word verb phrases (`add to [org]`, `set [date]`, `manually`, `search emails`, `skip`, `escalate to memo`) over long ones (`add [name] to [org]`, `confirm — [change]`, `search recent emails for [X]`, `log decision-pending`, `log commitment-discuss`). Tight scan, no redundant context.

If a meeting has NO pending items, the `⚠ Clean run — nothing pending.` line replaces the whole `⚠ Needs your call` block.

The Quick Read closing block (Rule 7) is REQUIRED when total pending across meetings > 2 — bot interpreting clusters, not just listing.

`Or "all clean"` is the bulk-acknowledge for the entire pending queue.

Source links inline (Rule 2): every meeting's transcript gets a clickable `Source: [Granola transcript](url)` line. If a follow-up draft references a specific email or doc, link that inline in the Summary too.

# Phase 7 — Failure handling (Rule 8)

- Granola unavailable: surface plain-English `(Granola unavailable — will retry tomorrow.)`. Skip the entire fire.
- Transcript fetch fails for a single meeting: mark `processing_failed`, surface in chat with retry option: `(Item N — couldn't fetch transcript. Retry with N retry.)`.
- docx skill chat-surface fails on brief generation: per Phase 4 step 7, plain-English fallback note, no tool name leak.

# Reply handling

**Action surface (v2.10.9+ — all-batch button widget per `shared/CHAT_ACTION_WIDGET.md`):** the IDX-tokenized + plain-N actions render as buttons in a `show_widget`-rendered card. Sub-items (`Na`, `Nb`, `Nc` per meeting) batch alongside parent meeting actions in the same widget. All selections accumulate locally; "Apply all" fires a consolidated `apply choices: [...]` payload. The receiving `apply-choices` skill parses the JSON payload and dispatches each `{n, action}` tuple through the handlers below.

**Heavyweight action note for past-meetings:** `IDX add [name] to [org]` and `N regenerate` produce visible content AFTER Apply (new entity confirmations, regenerated brief). Brief expansion via `present_files` still happens in the same chat turn as Apply.

## Pending-item actions (IDX-tokenized)

- `IDX add [text]` (v2.14.38+ unified REVIEW affirmative) → opens textarea pre-populated with the inferred entity record. Empty input adds as inferred (e.g. Rio Sample → Summit Company as person; Acme Co → prospect org); non-empty input folds the user's corrections in at create time (speaker reattribution, relationship-type override, additional context like "met at SF AI dinner"). Dispatches to people-crm `create_person` or workspace-manager `create_org` based on the sub-item's pending type. **For person dispatches (v3.2+): apply-choices Step 3a is binding — the create/update goes through `shared/scripts/people_writer.py`, dedup-first, never hand-rolled JSON.** Replaces v2.12.6 `add as person to <Org>` / v2.14.5 `add as new org` / v2.12.4 `manually [context]` action verbs (all retained as deprecated aliases for in-flight pre-v2.14.38 widgets — auto-translated to `add [text]` at apply-choices dispatch time).
- `IDX set date [when]` (vague timing pending) → user types a natural-language date; orchestrator parses + binds it to the underlying commitment / decision-pending entry.
- `IDX decide [text]` (decision needed pending) → user types their decision; orchestrator writes `decision` event with the user's text as the resolved decision.
- `IDX escalate to memo` → fire memo-writer through the standard chat invocation. The memo-writer produces a .docx via the docx skill and surfaces the link the standard Cowork way. Do NOT emit `file://` links.
- `IDX not relevant` (v2.14.38+) → write `pending_review_dismissed` event with 60-day cooldown. Item won't reappear for that signal for 60 days. Stronger than the deprecated `skip` (which had no cooldown).

## Meeting-level actions

- `N retry` → re-fetch transcript + re-run meeting-notes + follow-up-ritual for that meeting.
- `N reprocess` → same as `N retry` but with full re-extraction (replaces previous `meeting_processed` event).
- `N skip` → writes `meeting_skipped` event.

## Bulk

- `all clean` → respond `✓ Acknowledged. All pending items in this batch dismissed.`
- `re-run` → re-fire the whole orchestrator.

For unrecognized → respond in plain English: "Reply with the item index + action — `1a add Sam to Summit Company`, `3b confirm — push to OneDrive`, `2 retry`. Or `all clean`."

# What this orchestrator does NOT do

- Does NOT auto-process meetings older than the Phase 3 window — which is everything since the last successful run, floored at 24h and ceilinged at 30 days (SPEC CATCHUP1 F-1). For anything older than that ceiling, run `process the last call` manually.
- Does NOT modify entities.json directly except via people-crm (canonical writer).
- Does NOT auto-send any follow-up email (drafts always TEXT inline; user picks `send / draft` per item via Inbox or Commitments).
- Does NOT escalate auto-committed events back to pending_review later (commit is durable; resolution requires explicit M action).
