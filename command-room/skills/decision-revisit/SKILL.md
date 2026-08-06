---
name: decision-revisit
surfaces: both
description: "Surface past decisions worth re-examining — because time has passed, new contradictory signal arrived, or the original rationale's named conditions no longer hold. Fires on: 'revisit the [topic] decision', 'what decisions should I revisit', 'decisions to revisit', 'decision audit', 'is the [topic] decision still right'. Each surfaced decision carries the original context, what changed since, and one-tap actions: revisit now (chains into decision-memo-composer pre-filled), still valid, replace it, snooze. Does NOT fire on 'what did we decide' / 'decision history' (decision-log — retrieval), 'decision memo on [topic]' (decision-memo-composer — a NEW tradeoff), or 'we decided X' (decision-log — logging). Detection signals and action wiring: Routing section in the body."
---

## Skill Boundary (v2.1)

- **Use decision-revisit for:** proactive surfacing of decisions due for review. Reads the decision log, scores revisit-worthiness, surfaces top candidates with per-item action set.
- **Use `decision-log` for:** capturing a NEW decision.
- **Use `decision-memo-composer` for:** writing a NEW decision memo (forward-looking tradeoff analysis).
- **Use `workspace-manager` for:** retrieval of a specific known decision ("what did we decide about pricing").

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `decision_reaffirmed` when the user confirms a decision is still valid. Carries `{decision_event_seq, reaffirmation_reason, reviewed_at, snooze_until}`.
- `_hq/data/events.jsonl` — event type `decision_superseded` when the user supersedes a decision (chained invocation to `decision-log` to write the NEW decision; `supersedes_seq` field links the two). This event type already exists in the schema (added in v3.4.5).
- `_hq/data/events.jsonl` — event type `decision_revisit_scheduled` when the user snoozes a decision. Carries `{decision_event_seq, snooze_until_ts}`. (Snooze surfaces in the next scan as eligible for re-surface after `snooze_until_ts`.)

**Append through the locked writer (SPEC GATE1 / A1):** the `decision_reaffirmed` and `decision_revisit_scheduled` events MUST be written via `atomic_append_jsonl(events_path, [event], holder="decision-revisit")` (omit `seq`/`ts` — auto-stamped inside the lock), NOT a hand-rolled append. The `decision_superseded` path chains to `decision-log`, which writes its `decision` event through the same locked recipe. See `shared/WORKSPACE_API.md` → Append Protocol §3.

**Reads from:**
- `_hq/data/events.jsonl` — every `type == "decision"` event (the input universe).
- `_hq/data/events.jsonl` — every event with `data.references_decision_seq == <decision_seq>` OR matching topic / project-id / affected-entity heuristics (the "contradictory signal" pass).
- `_hq/data/entities.json` — for the named-condition check. If a decision's `data.rationale` mentioned "we don't have ops capacity" and entities.json now shows a person with `role: "Head of Ops"` added after the decision date, that's a signal worth surfacing.
- `_hq/views/DECISION_LOG.md` is NOT the source — that's a regenerated view. Read directly from events.jsonl per shared/CONTRACT.md Rule 24 (substrate is the source).

**Conflict boundary:** sole writer of `decision_reaffirmed` and `decision_revisit_scheduled` events. `decision_superseded` writes go through `decision-log` (chained invocation, not direct write).

---

# decision-revisit

The companion to `decision-log`. Decisions are append-only forever; pre-v3.8.0 the substrate had no way to surface "this decision was made 94 days ago, and three events since suggest the rationale no longer holds." This skill closes that loop.

## What It Does

Scans every decision event in events.jsonl. For each `status == "active"` decision, computes a revisit-worthiness score from:

1. **Time elapsed** — decisions older than 60 days enter the candidate pool; >120 days weight higher.
2. **Contradictory signal** — count of subsequent events that reference the decision's topic, affected entities, or project but represent state changes inconsistent with the decision (e.g., a decision "stay agency-only" followed by a `person_added` event with `role: "Head of Ops"`).
3. **Named-condition shift** — if the decision's `data.rationale` contains phrases like "we don't have X" / "until Y exists" / "while Z is the bottleneck", check whether those conditions still hold via entities.json + recent events. Each shift adds to the score.
4. **Stakeholder change** — if a decision's stakeholders (from `person_ids`) have changed roles or left the workspace, score up.

Rank decisions by score. Surface top 5 in a widget with per-decision context ("why revisit?") and the action set.

## How to Use

```
"what decisions should I revisit"
"decision audit"
"decisions to revisit"
"old decisions worth re-examining"
"which decisions are stale"
```

Runs on-demand. Can also be scheduled through `change-schedule` (operator-led setup).

## How It Works

### Phase 1 — Load candidate decisions

Read `_hq/data/events.jsonl` filtered to `type == "decision"` AND `status == "active"` (not superseded, not reaffirmed in the last 30 days, not snoozed). Build a candidate set.

**Named-decision branch ("revisit the hiring decision" / "revisit the pricing decision"):** when the trigger names a specific decision, resolve it against the candidate set (topic/entity fuzzy match; if two candidates tie, ask which). Skip Phases 2–3 entirely — no scoring, no ranking — and render Phase 4's widget with just that ONE decision card (same "why revisit?" snippet, same four actions). Only the bare scan-all triggers ("what decisions should I revisit", "decision audit") run the full rank.

### Phase 2 — Score each candidate

For each candidate:
- `time_score` = clamp(`days_since_decision / 60`, 0, 3)
- `contradictory_signal_score` = count of events in [decision_ts, now) that reference the decision's topic/entities/project but represent state changes inconsistent with the rationale
- `named_condition_score` = +1 per phrase in rationale that maps to a named condition that has demonstrably shifted (entities.json now contains an entity that contradicts it, OR events.jsonl contains evidence of the condition no longer holding)
- `stakeholder_score` = +0.5 per stakeholder who has changed role / left workspace since decision date
- `total = time + contradictory + named_condition + stakeholder`

**Named-condition extraction (ADV1 — how to actually compute `named_condition_score`):** scan the decision's rationale text for the patterns **"until X", "while X", "as long as X", "we don't have X", "once X", "if X changes"**. Each match is a named condition. **"Demonstrably shifted"** means entities.json or events.jsonl now contradicts it — the role got hired (a person with that title now exists), the budget constraint lifted (a funding/revenue event landed), the dependency moved (that project's status changed). **Quote the original condition AND the contradicting evidence in the widget — never score a shift you can't cite.** A condition you can't point to a contradiction for scores 0, not a guess.

**Coefficients are tunable constants, not arbitrary** (so a future session doesn't treat them as magic): `time_score` clamps at **60 days** (the floor where a decision is old enough to re-pressure-test without nagging) up to a max of 3; `stakeholder_score` is **0.5/stakeholder** (a personnel change is a real but secondary signal — half the weight of a directly-contradicted named condition); `named_condition` and `contradictory_signal` are **1.0 each** (the strongest signals — the world directly disagreeing with the rationale). Adjust these if real usage shows the ranking is too eager or too sleepy.

### Phase 3 — Rank + surface

Take top 5 by total score. Build a "why revisit?" snippet for each — naming the specific events / entity changes / time gap that drove the score. Render as a widget.

### Phase 4 — Render widget

Per-item action set (all four are CANONICAL_ACTIONS members — the Phase 4 deliberation-set extension; dispatch lives in apply-choices' `decision-revisit` source entry):
- `revisit` (displays "Revisit now") — opens `decision-memo-composer` pre-filled with the original decision's framing + the contradictory signal pass as starting context.
- `still valid` (displays "Still valid") — appends `decision_reaffirmed` event. Decision exits the candidate pool for 30 days.
- `replace` (displays "Replace it") — chains to `decision-log` to capture the new decision; this skill writes `decision_superseded` linking to the original (the event type keeps its name — only the button label is friendly).
- `snooze 30d` (displays "Snooze (30 days)") — appends `decision_revisit_scheduled` with `snooze_until_ts = now + 30d`. Decision is re-eligible after that.

Widget follows `shared/CHAT_ACTION_WIDGET.md` contract. Validators per `shared/CONTRACT.md` Rules 1-4.

## Output Structure (widget)

```
5 decisions worth a second look

1. Acme Co as agency vs in-house     [decided 2026-02-14, about 3 months ago]
   Original reasoning: "we don't have ops capacity"
   What's changed: you hired Mira Sample on April 8 as Head of Ops, and
   recent activity points to internal capacity now beating the agency.

   [Revisit now]  [Still valid]  [Replace it]  [Snooze (30 days)]

2. Quarterly pricing reviews          [decided 2026-01-03, about 4 months ago]
   Original reasoning: "monthly is too noisy"
   What's changed: two customers cancelled recently citing "pricing
   surprise" in their exit notes.

   [Revisit now]  [Still valid]  [Replace it]  [Snooze (30 days)]

[Show 3 more]
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary (event types like `decision_superseded` stay internal; the buttons say "Replace it" / "Still valid").
- Bad: "[Supersede] — writes decision_superseded referencing seq 412"
- Good: "[Replace it] — I'll log the new decision and link it to the old one"

## DOES NOT

- Mutate prior decision events. Per references/DATA_CONTRACT.md: events are append-only. Revisit produces NEW events (`decision_reaffirmed`, `decision_superseded`, `decision_revisit_scheduled`); the original `decision` event is never rewritten.
- Auto-resolve. Every action is user-initiated via the widget.
- Surface decisions still inside their snooze window (`snooze_until_ts > now`).
- Read DECISION_LOG.md as truth — it's a regenerated view. Source is events.jsonl.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Surface decisions worth re-examining based on elapsed time, contradictory new signal in events.jsonl, and whether the original rationale's named conditions still hold in entities.json. Use when the CEO says 'what decisions should I revisit', 'decision audit', 'decisions to revisit', 'decision revisit', 'decision review', 'old decisions worth revisiting', 'which decisions are stale', 'review my decisions', 'decisions worth re-examining', 'revisit the hiring decision', 'revisit the pricing decision', 'revisit the' (any single-decision revisit-by-name). Reads every decision event from events.jsonl + subsequent events that reference each decision + entities.json for the named-condition check. Writes decision_reaffirmed or decision_superseded events on user action (mirrors meeting-notes' decision-CRU pattern from v3.4.5). Renders as a widget with per-decision actions (Revisit now / Still valid / Replace it / Snooze 30d — the Phase 4 deliberation verb set: `revisit` / `still valid` / `replace` / `snooze 30d`, all in CANONICAL_ACTIONS). DOES NOT fire on 'log decision' (that's decision-log — capture), 'decision memo' (that's decision-memo-composer — forward-looking new decision), 'what did we decide about X' (that's decision-log — it owns decision retrieval), or 'show decision log' (that's a view file read).
