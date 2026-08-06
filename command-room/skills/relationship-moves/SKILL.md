---
name: relationship-moves
surfaces: both
description: "The weekly proactive outreach surface: the top 3 people worth reaching out to THIS week, each with last touch, an evidence-cited why-now, and a pre-drafted opener in the CEO's voice. Fires on: 'relationship moves', 'who should I reach out to' / 'who should I reach out to this week', 'weekly outreach'. Ranks on a code-computed blend of dormancy, live-thread leverage, and overdue commitments; runs as an optional Sunday-evening scheduled task (added via 'change my schedule', not first-install). Does NOT fire on 'who went dark' (dormant-customer-scan — raw detection this skill consumes), 'warm threads to revive' (thread-resurrection), 'follow up with [name]' (follow-up-ritual), or 'balance check' / 'my white space' / 'plan a date night' (balance — PERSONAL ties; the tie field partitions the entity set, so a spouse or parent never appears here). The line: scans DETECT; this RANKS and hands you drafts. Ranking math and dedupe rules: Routing section in the body."
---

# Relationship Moves — the weekly outreach action pack

One widget, top 3 people worth reaching out to this week, each with a pre-drafted opener. Not a detection report (that's dormant-customer-scan / thread-resurrection) — this RANKS their signals and hands the CEO ready-to-send drafts.

## Skill Boundary (v2.1)

- **Is:** a ranked, pre-drafted action pack. The differentiator vs the detection skills is the opener-in-your-voice + the weekly batch framing.
- **Is NOT** `dormant-customer-scan` (the raw "who's gone quiet" report) or `thread-resurrection` (thread-level revival). This skill CONSUMES their normalized `dormancy_signal` events and turns the top 3 into drafts.

## Ranking is CODE, never prose math

All ranking comes from `shared/scripts/relationship_moves.py` `compute_relationship_moves(...)`:

```
score = 0.5 × normalized_dormancy + 0.3 × thread_context + 0.2 × min(overdue_days/10, 1)
```

`compute_relationship_moves` loads normalized dormancy signals (`dormancy.load_dormancy_signals`, last 14 days, max-score per entity), folds each thread into its counterparty person (one card per human), computes overdue commitments from `cru_match.load_open_commitments`, dedupes anyone already emailed/suggested in the last 7 days or snoozed/dismissed, and returns ≤3 candidates. It NEVER pads below the real candidate count. You do not compute scores in prose.

## Behavior

### Step 1 — Entity-resolve + live-check gate (MANDATORY)

Resolve the workspace per `shared/CONTRACT.md` Rule 22. Then, per candidate, you MUST call `shared/scripts/live_contact_check.py::live_contact_check()` before surfacing — inherit the `dormant-customer-scan` live-check MUST-language verbatim: NO dormancy-driven outreach from substrate-only data. Drop any candidate the live check un-dormants (a recent touch the substrate missed) — EXCEPT candidates whose rank is carried by overdue commitments (overdue component >= dormancy component in the blend): an overdue item is still overdue after a recent touch, so keep the card and cite the recent touch in the why-now line instead of pitching re-engagement.

### Step 2 — Compute the ranked candidates

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "..."
```

```python
import sys; sys.path.insert(0, "shared/scripts")
from relationship_moves import compute_relationship_moves
# thread_totals: pass {} UNLESS thread-resurrection's Phase 2 already ran in THIS
#   session — then reuse its per-counterparty totals verbatim (commitment +3,
#   project +2, relationship-tier +2/+1, multi-turn <=+3, direction +1). Never
#   recompute thread scores ad-hoc inside this skill; {} simply zeroes the
#   thread_context term and the blend renormalizes.
moves = compute_relationship_moves("<abs workspace root>", top_n=3, thread_totals=thread_totals)
```

`compute_relationship_moves` appends one `relationship_move_suggested` event per returned candidate (you do not write it yourself).

### Step 3 — Draft the top-3 openers (email-writer chain)

For each candidate, draft an opener in the CEO's voice via the email-writer chain (intro-broker pattern): build a voice corpus from the CEO's prior `email_drafted` events to this person, fall back to the email-writer Voice Block + the two-step protocol. Seed the opener with the thread-resurrection revival hook when a thread anchors the candidate, else the dormant-customer-scan suggested angle. Drafts are NEVER auto-sent.

### Step 4 — Render ONE widget

Render one `all_batch_widget` per `shared/CHAT_ACTION_WIDGET.md`. Per item:
- person name + the thread topic as the title
- `context_tag` = "last touch [date] · [N] days ago"
- `body_lines` = the why-now line citing real evidence (e.g. *"you owed them a pricing answer since Feb; they announced funding Mar 12"*) + the blockquoted opener
- `metadata` = `[["To", email], ["Subject", subject]]` (email-shaped — required for the data-shape validator)
- actions `["N send", "N draft", "N snooze 3d"]` (use `add email then send` when no address is on file)

All verbs are already in `CANONICAL_ACTIONS` — no renderer change. The `send`/`draft` clicks dispatch through apply-choices into email-writer's Phase 5/6 (`email_drafted` + `email_sent` with `draft_event_seq` linkage) — do NOT reinvent the send lifecycle. Put the Links section after the widget, then STOP (the widget is the entire turn).

**Ranked-report layout (SPEC OUT2 §4 — this pack is one of the four ranked-report surfaces; contract in `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The ranked report").** The widget above maps to the contract — align, don't duplicate: open the widget with the shared **tile summary band** (components.py fragment) derived from the SAME ranking computation — **worth a touch** (candidate count) · **days quiet** (median gap across the 3) · **owed** (count with an overdue commitment component); drop-empty per F-60, and with fewer than 2 real tiles skip the band entirely (a one-tile band is noise on a 3-item pack). Each item IS the scored row: rank (widget position) · name · quantify tag (the days-quiet + overdue-commitment component, code-computed, never an estimate) · why-now (the evidence line) · action (send / draft / snooze 3d). The widget's actions are the ask block (one-ask-surface) — never a prose twin. No synthesis lead: ranked lists lead with the count line.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "last touch [date] · [N] days (dormancy_signal score 0.72)"
- Good: "last touch Feb 3 · 47 days ago"

### Fewer than 3 / zero candidates

Render fewer when fewer qualify — NEVER pad with sub-threshold candidates (padding manufactures outreach pressure from noise). Zero candidates → render the `all_clear_summary` data view (the orchestrator-dont-forget all-clear pattern), never hand-built HTML.

## What this skill does NOT do

- Does not detect dormancy itself — it consumes the normalized `dormancy_signal` events the detectors emit.
- Does not auto-send — `send` is always a user click.
- Does not pad to 3 — it surfaces only real candidates.
- Does not suggest the CEO reach out to HIMSELF. The resolved primary user is dropped from the candidate set in code (`relationship_moves.compute_relationship_moves`, LIFECYCLE1 §7c) — the overdue-commitment term alone used to carry his own record over the threshold, and the 2026-08-03 pack duly told him to nudge himself. If you ever see the CEO's own name in this pack, that is a defect to report, never a row to render.
- Does not double-surface someone already emailed/suggested this week (7-day dedupe) or snoozed/dismissed anywhere — including a `dont_forget_snooze` written by the retired Pulse chat, which `_recently_excluded` still honours as fossil history.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> The weekly proactive outreach surface: the top 3 people worth reaching out to THIS week, each with last touch, a one-line why-now citing real evidence, and a pre-drafted opener in the CEO's voice — actionable via send / draft / snooze 3d buttons. Ranks on a code-computed blend of normalized dormancy (the cooling relationship), live-thread leverage, and overdue commitments. Fires as a Sunday-evening scheduled task and on demand. Triggers: 'relationship moves', 'who should I reach out to', 'who should I reach out to this week', 'weekly outreach'. DOES NOT fire on 'who went dark' / 'dormant customer scan' / 'quiet customers' / 'who hasn't replied in a while' (that's dormant-customer-scan — the raw detection report, not a ranked pre-drafted action pack). DOES NOT fire on 'thread resurrection' / 'warm threads to revive' (that's thread-resurrection — thread-granularity). DOES NOT fire on 'follow up with [name]' (that's follow-up-ritual — a single named follow-up). DOES NOT fire on 'balance check' / 'how's my white space' / 'plan a date night' (that's balance — the PERSONAL white-space surface; `tie: "personal"` people are dropped from this skill's candidate set at the load/score boundary, BAL1 D1.1, and belong to Balance exclusively). The line: dormant-customer-scan and thread-resurrection DETECT; relationship-moves RANKS the detections and hands you drafts.
