# Coach Selection Algorithm — the *how*

`deliverable-catalog.md` is the *what* (the library of deliverable shapes, each with a
signal-condition, a target-entity slot, and a render template). THIS file is the *how*:
given a workspace, which 3–5 entries does Coach pick, and which specific named entity does
each entry's slot resolve to.

Coach reads this after the Mirror + Insights are computed (Phase 2C, "How outputs are
generated"). Everything here is computed from `_hq/data/entities.json` +
`_hq/data/events.jsonl` — no connector fetch, no improvisation. If a value can't be
computed, the entry is ineligible (Phase 2B rule: "no insight without an anchor").

---

## Step 1 — Eligibility filter (by mode)

Determine the mode, then filter the catalog down to the eligible set.

| Mode | Detection | Eligible entries |
|---|---|---|
| **M1 first fire** | no prior `coach_session` event | catalog entries tagged `m1_handoff` or `either`; direct deliverables suppressed except `2.6 Coverage gap memo` |
| **Post-onboarding** | a `coach_session` event exists, newest >14 days ago | entries whose `data_tier` + per-entry accumulation threshold are met |
| **Refresh** | newest `coach_session` event <14 days ago | post-onboarding set MINUS entries already offered and not acted on; entries that WERE acted on (downstream events landed) get a one-line acknowledgement instead of a re-offer |

## Step 2 — Entity-slot resolution (the ranking)

Each eligible entry has a target-entity slot (a project or a person). Resolve it to the
single highest-ranked entity of that kind in this workspace. Ranking is a lexicographic
sort — compute the first key, break ties with the second, then the third.

### Projects (and orgs / threads)

Rank candidate projects by, in order:

1. **7-day mention count** — number of events in `events.jsonl` from the last 7 days whose
   `primary_thread_id` (or org/project attribution) is this project. Higher wins.
2. **Recency** — max `ts` of any event attributed to this project. More recent wins.
3. **People involved** — count of distinct `person_ids` that appear on this project's
   events. More people wins (a deliverable about a multi-stakeholder thread lands harder).

Archived / paused projects are excluded from the candidate pool (the catalog entry's
signal-condition owns the active/paused call — never override it here; that was Bug #92b).

### People

Rank candidate people by, in order:

1. **7-day interaction count** — number of `interaction` / `meeting` / `commitment` events
   in the last 7 days where `event_references_person(ev, person_id)` is true
   (use `cru_match.event_references_person` — it handles every shape variant). Higher wins.
2. **Deep-profile existence** — a person with a populated profile (role, org, history)
   outranks a bare name, because the downstream `tell me about [person]` chain produces a
   richer deliverable. Profiled wins over stub.
3. **Recency** — max `ts` of any event referencing this person. More recent wins.

If the top-ranked entity for a slot is the SAME as one already used by a higher-priority
entry this fire, fall to the next-ranked entity so the 3–5 offers name distinct targets.

## Step 3 — Mix + count

- **M1 first fire:** ~4 chained + 1 direct (`2.6 Coverage gap memo`). Chained entity slots
  resolve from onboarding's Step 5b deep-dive candidate list (the most recent
  `onboarding_checkpoint` event with `phase: "5"`) BEFORE falling back to the Step 2 ranking.
- **Post-onboarding:** 3–5 entries; mix tilts toward direct deliverables as runtime
  accumulates (more events in `events.jsonl` → more direct entries clear their threshold).
- **Refresh:** only newly-eligible entries; never re-offer an un-acted offer.

Cap at 5. If fewer than 3 entries are eligible, offer what's eligible — never pad with an
entry whose signal-condition didn't fire (Phase 2B: drop what you can't anchor).

## Step 4 — Training-complexity gate (M1 graduates — RET1)

Adjusts the *chain length* of the offered deliverables (never which entities rank — Step 2
owns that). Read `training_prompts_fired` (coach Phase 1 — count of distinct `command_slot`s
carrying a `m1_training_prompt_fired` event, 0–3):

- `fired >= 2` → standard mix; 3-step project chains (`go [project]` + downstream +
  `end session`) are fine.
- `fired <= 1` → offer at most one 3-step project chain; bias toward single-step and 2-step
  person chains. A customer who fired ≤1 of the 3 onboarding training commands hasn't built
  muscle memory for the longer chains, so don't front-load them.
- **Absent** (pre-RET1 workspace — no `m1_training_prompt_shown` events at all) → no gate
  applied; standard mix. Never penalize a customer for predating the instrumentation.

