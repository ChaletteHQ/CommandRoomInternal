---
name: pipeline-tracker
description: "Deal tracking on the workspace itself — capture a deal, watch the pipeline, move deals through lead / qualified / proposal_sent / negotiating, keep a dated next step on each, flag rot, close won or lost with a reason. Fires on: 'pipeline', 'show my pipeline', 'pipeline review', 'deals' (plural), 'show my deals', 'new deal', 'what deals are closing', '[Name] signed', 'closed the deal with [name]', 'we won the [deal]', 'we lost the [deal]', 'mark [deal] won / lost', 'move [deal] to [stage]', 'sent the proposal', 'deal is stalled', 'revenue in play', plus 'tune pipeline-tracker'. Ranked report with tiles and one-tap actions; an explicit win on a prospect org converts them to a client in the same turn. Does NOT fire on 'new prospect' / 'is now a client' (workspace-manager) or 'who went dark' (dormant-customer-scan); never claims bare singular deal. Full trigger family and fences: Routing section in the body."
---

## Entity-resolve enforcement (mandatory)

Every name-bearing trigger ("move [deal] to [stage]", "mark [deal] won", "new deal with [org]", "[Name] signed") MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` FIRST, per `shared/ENTITY_RESOLVE_PROTOCOL.md`. Deals are threads, so they resolve like any entity (`go [deal name]` works for free). **Multiple open deals on one org is a supported, normal shape** — when the resolver returns 2+ candidate deal threads for "move Acme to negotiating", use the disambiguation widget; NEVER first-pick (protocol rule).

## Skill Boundary

- **Use pipeline-tracker for:** the deal lifecycle — capture, stage moves, next steps, rot flags, won/lost closure, the pipeline report and digest. Pre-won, deal-shaped attention.
- **Use `workspace-manager` for:** the org lifecycle — `new prospect [Name]` (org + engagement, no deal thread) and the administrative conversion verbs (`[Name] is now a client`, `promote/convert [Name] to client`). A deal is a thread ON an org; the org record stays workspace-manager's.
- **Use `dormant-customer-scan` for:** post-won customers going quiet. An org can legitimately appear in both (a live deal AND dormant delivery work).
- **Use `stalled-projects` for:** non-deal threads. It excludes `kind="deal"` threads by fence — deal rot reports here, with per-stage thresholds.

## Writer Contract

- **Reads:** `_hq/data/entities.json` threads/orgs (via `deal_state.list_open_deals` / `list_closed_deals` — defensive, wrapper-aware), events via `deal_state.load_deal_events` + `thread_activity.derive_thread_activity` (recency — NEVER the deprecated `thread.last_activity`), open commitments via `cru_match.load_open_commitments` (the next-step signal), `_hq/data/skill_config/pipeline-tracker.json`.
- **Writes — ONLY through `shared/scripts/deal_state.py`,** the single writer of every `deal.*` field and every `deal_*` event (`deal_created` / `deal_updated` / `deal_stage_changed` / `deal_won` / `deal_lost`). It routes through `thread_writer` + `event_gate.append_event` internally. Hand-editing entities.json or hand-appending a deal event is forbidden — that is exactly how engagement labels became a free-text status store.
- **Next steps are commitments** (D3): "next step: send revised proposal by Fri" is a standard `commitment` event with `primary_thread_id` = the deal thread. It closes ONLY via `commitment_state.close_commitment`. No parallel task concept, no `deal.next_step` field.
- **Config** via `skill_config_writer` (`save_skill_config` / `get_config` / `wipe_skill_config`).
- **Scan receipt (REQUIRED, every report fire):** `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "pipeline-tracker", fired_via="manual", surfaced=n_open, extra_data={"flagged_thread_ids": [...], "untracked": n_untracked})` — the next fire reads it to dedup nags; value surfaces count the work.
- If `load_deal_events` returns a non-empty `skipped` list, surface the standard banner ("I couldn't read N lines of saved history — the numbers below may be missing those") — never silently swallow it.

## What It Does

One place for every open deal: what stage it's in, how long it's sat there, whether it has a dated next step, what's rotting, what's closing this month, and what you've won and lost lately — computed from the workspace's own record of meetings, email, and commitments, not from fields someone remembered to update. High-stakes changes (stage, value, close) happen only when you say so; the numbers derive themselves.

## First-Run Personalization (SPEC FRP1)

Show-then-tune (STT), all three decisions. Read config through `get_config` — never the raw file.

```python
# Rule 22 preamble first: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "rot_preset": "standard",   # standard (10/10/7/7 days by stage) | relaxed (15/15/10/10) | aggressive (7/7/5/5)
    "digest": {"enabled": False, "day": "tuesday", "time": "08:00"},  # weekly digest preference — PROPOSED, never auto-registered
    "value_capture": "exact_only",  # exact_only (ask for a number) | ranges_ok (accept 'about 30-50k' as a note, no money math)
}
cfg = get_config(workspace_root, "pipeline-tracker", DEFAULTS)
```

`rot_preset` maps to `deal_health` stage thresholds: standard = lead 10 / qualified 10 / proposal_sent 7 / negotiating 7; relaxed = 15/15/10/10; aggressive = 7/7/5/5. `digest.enabled` records the preference only — registration goes through enable-command-room-schedules' optional proposed set (Part 2), and only when at least one open deal exists; nothing is ever silently scheduled. `value_capture` controls whether a new deal without a number gets one follow-up ask; ranges are stored as a note in the deal's source field and contribute nothing to any dollar figure (never estimated).

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "pipeline", "show my pipeline", "deals" | run the report with `cfg`. FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "pipeline-tracker", DEFAULTS)` BEFORE rendering, then append the first-run footer. |
| **Show settings** | "show pipeline-tracker settings" | render current config in plain English; no report. |
| **Tune** | "tune pipeline-tracker", "tune the pipeline" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-render. |
| **Reset** | "reset pipeline-tracker to defaults" | `wipe_skill_config(workspace_root, "pipeline-tracker")` → next fire is a first-fire again. |

**The first-run footer (chat text under the report, renders exactly once ever):**

> *First time showing your pipeline. I set 3 defaults: **I flag a deal once it's quiet past its stage's usual window (about a week when it's late-stage, ten days early)** · **no weekly digest unless you want one** · **I'll ask for a number when a new deal doesn't have one**. Say "tune the pipeline" to change any, or just tell me ("flag deals sooner" / "stop asking for values").*

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "flag deals sooner" / "be more aggressive" | `rot_preset = aggressive` |
| "give deals more room" / "less naggy" | `rot_preset = relaxed` |
| "send me the weekly digest" / "digest on Tuesdays" | `digest.enabled = True` (+ day if named) → surfaces the schedule PROPOSAL |
| "stop the digest" | `digest.enabled = False` |
| "stop asking for values" / "ranges are fine" | `value_capture = ranges_ok` |
| "always get a number" | `value_capture = exact_only` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-render + one-line confirm.

## How to Use

```
"pipeline" / "show my pipeline"      → the ranked report
"pipeline review"                     → same report, review framing (what moved, what's stuck)
"what deals are closing this month"  → the closing subset
"new deal Acme pilot with Acme Co, $40k"
"move Acme pilot to negotiating"
"mark Acme pilot won" / "mark Acme pilot lost — price"
"Acme signed" / "closed the deal with Acme"   → win + prospect→client conversion in one turn
"we lost the Beacon Logistics deal"
"set the next step on Acme pilot: revised proposal by Friday"
```

## How It Works

### The report ("pipeline" / "show my pipeline" / "pipeline review" / "deals")

**Hard-gated bash — all math in code (Rule 22 preamble, run from $PLUGIN_ROOT):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, datetime
sys.path.insert(0, 'shared/scripts')
import deal_state, deal_health, pipeline_math
from thread_activity import derive_thread_activity
from cru_match import load_open_commitments
ws = '<workspace_root>'
opens = deal_state.list_open_deals(ws)
closed = deal_state.list_closed_deals(ws)
events, skipped = deal_state.load_deal_events(ws)
activity = derive_thread_activity(ws)
open_cmts = load_open_commitments(ws + '/_hq/data/events.jsonl')
next_step_ids = {c.get('primary_thread_id') for c in open_cmts if c.get('primary_thread_id')}
today = datetime.date.today()
# rot thresholds from cfg['rot_preset'] (standard/relaxed/aggressive map above)
health = deal_health.compute_deal_health(
    opens, activity_by_thread=activity, open_commitment_thread_ids=next_step_ids,
    today=today, stage_thresholds=<preset map>, zombie_days=deal_health.zombie_threshold_days(closed))
ranked = pipeline_math.rank_deals(health)
tiles = pipeline_math.pipeline_tiles(opens, health, events, today)
print(json.dumps({'ranked': ranked, 'tiles': tiles, 'n_skipped': len(skipped)}, default=str))
"
```

Render as the OUT2 ranked report (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The ranked report"):

1. **Tile band** — exactly `pipeline_math.pipeline_tiles` output, drop-empty (F-60): Open pipeline $ · Closing this month (n · $) · Stalled (n) · Won rate 90d (only at ≥4 terminal events — never a misleading 100%) · Weighted $ (only when ≥1 deal carries a forecast category). Values come from the SAME computation as the rows — never a second pass, never a prose re-count.
2. **Scored rows, in `rank_deals` order exactly:** `rank · deal (org) · $tag · stage, Nd in stage · next step or ⚠ no next step · why-now · action`. The $tag comes from `quantify.money_time_tag` (deal value leads the trace); absent value = no tag, never an estimate. Why-now cites the flag evidence ("quiet 12d in negotiating — their usual reply gap is 3d"). A deal with zero open commitments renders `⚠ no next step` and ranks up — the single strongest predictor of a deal dying.
3. **Untracked rows** (pre-existing deal threads with no stage tracking) render at the bottom with one-tap `track deal` adoption — never a rot alarm, never a crash.
4. **Widget:** `widget_mode: "all_batch_widget"` via `render_chat_output_widget`, posted via `widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`) (`shared/CHAT_ACTION_WIDGET.md` § Transport), `source_skill: "cr-pipeline"` (the renderer stamps `src`). Per-item actions (all in `CANONICAL_ACTIONS` via the verb taxonomy): `move to [stage]` · `set next step [text]` · `mark won` · `mark lost [reason]` (reason REQUIRED — F-17 hold-with-reason) · `draft re-engagement` · `snooze 14d` · `skip`. Untracked rows carry `track deal` + `skip` only. Dispatch lives in apply-choices' `cr-pipeline` source entry.
5. **Docx on request** ("pipeline report as a doc") via `brief_writer.make_brief(brief_kind="ranked_report", ...)` — CONTRACT Rule 27: never a .md deliverable. Then the OUT2 §3 visual pass (`visual_gate.render_preview` → 6-item checklist → fix at most once → `log_visual_gate`). Surface the file as an H2 heading link LAST in the turn via `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_path))` — never a plain-text path, never a hand-built URL (CONTRACT Rule 3).
6. **Receipt** (Writer Contract above) + the skipped-lines banner when `n_skipped > 0`.

Zero open deals: *"No open deals tracked yet. Say 'new deal [name] with [org]' to start one."* — no tile band, no empty frames.

### "new deal [name] with [org], $[value]"

1. `resolve_all` the org. Unresolved → offer `new prospect [Name]` first (workspace-manager owns org creation — a deal needs a real org). Ambiguous → disambiguation widget.
2. Parse value if stated. `value_capture: exact_only` and no number → one follow-up ask ("Got a number for it? Fine to skip."); `ranges_ok` → store the range text in the deal's source note, value stays empty.
3. **Hard gate — run the block, never hand-write entities.json:**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
import deal_state
t = deal_state.create_deal('<workspace_root>', name='<deal name>', org_id='<resolved org id>',
                           value=<number or None>, expected_close=<ISO date or None>,
                           source=<free text or None>, source_skill='pipeline-tracker')
print('DEAL_CREATED ' + t['id'] + ' stage=' + t['deal']['stage'])
"
```

   The `DEAL_CREATED` line is the proof (thread + `deal_created` event through the gate). The org record is NOT touched — a new deal on a prospect changes nothing about the org.
4. Ack in plain English: *"✓ Acme pilot opened — lead stage, $40K. Give it a next step? ('revised proposal by Friday' works.)"* A stated next step becomes a commitment on the new thread.

### "move [deal] to [stage]"

Resolve the deal (disambiguate on 2+), then `deal_state.set_stage(ws, thread_id, '<stage>', source_skill='pipeline-tracker')`. Backward moves are allowed — deals regress; the event records direction and days-in-stage resets. Invalid stage names get the four real ones offered ("Stages are lead, qualified, proposal_sent, negotiating — won/lost are closes: say 'mark [deal] won'"). Untracked deal thread → offer `track deal` adoption first (`deal_state.adopt_deal`).

### "mark [deal] won" · "mark [deal] lost — [reason]" · "[Name] signed" · "we lost the [deal]"

ALL closes go through `deal_state.close_deal` — the single closure path. Idempotent: `already_closed` is acked honestly ("that one was already closed — nothing changed"), never re-appended.

- **`mark [deal] won`** → `close_deal(ws, thread_id, 'won', source_skill='pipeline-tracker')`. When the return carries `conversion_suggestion`, render it verbatim as the one-liner: *"✓ Won. Acme Co is still marked a prospect — say `Acme Co is now a client` and I'll convert them."* Do NOT flip the org from this verb.
- **`[Name] signed` / `closed the deal with [Name]` / `we won the [deal]`** — the user-explicit win declaration (D6): resolve the org's open deal thread (2+ → disambiguate; the org-level phrasing usually means the furthest-along deal — confirm, don't guess), then `close_deal(ws, thread_id, 'won', convert_prospect=True, ...)`. On a prospect org this closes the deal AND runs the same prospect→client conversion workspace-manager uses — one utterance, one atomic result, acked as one line: *"✓ Acme pilot won — and Acme Co is now a client."* If the org resolves but NO open deal thread exists, hand the turn to workspace-manager's `[Name] is now a client` handler (org-only conversion) and offer `new deal` for next time.
- **`mark [deal] lost` / `we lost the [deal]`** → a loss reason is REQUIRED. If not stated, ask once with the fixed list (no decision · price · competitor · did it themselves · timing · bad fit · other) — `no_decision` first; most losses are indecision, not a rival. Then `close_deal(ws, thread_id, 'lost', loss_reason='<enum>', loss_note='<their words>')`. Thread archives; the loss shows up in board-pack concerns and the loss-pattern readout.
- **Value stated at close** ("closed at 35") → pass `value=35000`; never infer a unit — confirm ambiguous magnitudes.

### "set the next step on [deal]: [text]"

A standard commitment capture with `primary_thread_id` = the deal thread and an EXPLICIT `data.kind` (Gate 21 — never rely on the gate's default): `promise` when the step involves a counterparty ("send the revised proposal to Acme by Fri" — enters chase), `task` when it's purely the user's own move with nobody owed. Owner = the user unless they name someone. Dated next steps get chased by the existing commitment machinery — nothing new; closure only via `commitment_state.close_commitment`. This clears the `⚠ no next step` flag on the next render.

### Detector-observed signals (Part 2 — not in this release)

The deal-signal detector (meeting/email language → proposed stage/value updates) ships in Part 2 as propose-and-confirm via `deal_update_proposed`. Until then, NOTHING infers a deal change: only what the user says moves a deal.

## Executive Output Standard (EXEC1)

Inherits `shared/EXECUTIVE_OUTPUT_STANDARD.md`. The chat lead is the quantified count line, not a theme: *"6 open · $137K · 2 stalled · 2 closing this month."* Exec header on the docx path: verdict = the headline pipeline number; CHANGED = moves since the last receipt; DECIDE = the one deal to push today; NEEDED = the top one-tap. Money via `quantify` only — no estimation.

**Output guard:** no internal tokens, paths, event names, entity ids, or version numbers in anything the CEO sees (`shared/VOICE_CALIBRATION.md` § Plain-language glossary).
- Bad: "deal_stage_changed appended for project_017."
- Good: "✓ Acme pilot moved to negotiating — day 1 of the clock."

## Gotchas

- **Two open deals, one org, is normal.** Disambiguate by deal name; never first-pick, never merge them.
- **`deal.stage` is a word, not a number.** The integer `stage` on threads is the project-lifecycle field — never write sales stages into it (`deal_state` and `thread_writer` both reject drift).
- **Won/lost are not stages.** "move Acme to won" routes to the close path, with the reason flow for losses.
- **Never estimate a value.** No number = no money tag, no tile contribution. Ranges live in the source note.
- **Won-rate needs a floor.** Under 4 closes in 90 days the tile drops — a 1-for-1 quarter is not "100%".
- **Untracked deal threads are data, not errors.** Pre-existing deal threads without stage tracking render as adoption offers.
- **Recency is observed, not asserted.** Rot keys on real contact from the event log — editing a field never resets the clock.

## What It Doesn't Do

- Doesn't create orgs — `new prospect` / `new org` (workspace-manager) own that; a deal needs an existing org.
- Doesn't convert prospects on its own judgment — conversion happens only on the user's explicit win declaration (D6) or workspace-manager's administrative verbs. Observed signals never auto-flip anything.
- Doesn't chase next steps itself — commitments do (the daily Commitments chat, chase drafts, snooze machinery all come free).
- Doesn't draft the follow-up email — `draft re-engagement` hands to email-writer (lazy, draft-never-send).
- Doesn't read QuickBooks — no QBO dependency anywhere; deal values are user-stated.
- Doesn't auto-register the weekly digest — the digest is proposed through the schedule setup's optional set, never silently registered.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill. The routing metadata is budget-capped by the platform (G11); routing correctness is enforced mechanically by tests/triggers.yaml. Everything below remains binding at fire time.

> Deal tracking built on the workspace substrate — capture, stage, next-step, rot, and close every deal from the workspace's own activity record. Use when the CEO says 'pipeline', 'show my pipeline', 'show the pipeline', 'pipeline review', 'deals', 'show my deals', 'my deals', 'open deals', 'what deals are closing', 'what deals are closing this month', 'deals closing this month', 'revenue in play', 'new deal', 'new deal [name] with [org]', 'track this as a pipeline deal', 'track deal', 'move [deal] to [stage]', 'mark [deal] won', 'mark [deal] lost', 'mark won', 'mark lost', 'we won the', 'we lost the', 'closed the deal with', 'signed' ('[Name] signed' — the explicit win declaration; deal-won on a prospect org converts them in the same turn per the single-closure fence), 'sent the proposal' (stage nudge phrasing), 'deal is stalled', 'deal is stalling', 'which deals are stalling', 'set the next step on', 'loss reasons', 'why are we losing deals'. Also handles first-run personalization settings — use when the CEO says 'tune pipeline-tracker', 'tune the pipeline', 'show pipeline-tracker settings', 'reset pipeline-tracker to defaults'.

> Fences (one per line — each stays with its owner):
> DOES NOT fire on 'new prospect' (workspace-manager — org + engagement, no deal thread).
> DOES NOT fire on 'is now a client' / 'promote to client' / 'convert to client' (workspace-manager — the administrative conversion verbs; when the org has an open deal, its handler routes through this skill's closure path).
> DOES NOT fire on 'who went dark' / 'dormant customers' (dormant-customer-scan — post-won customers).
> DOES NOT fire on 'stalled projects' (stalled-projects — non-deal threads; it excludes deal threads by fence).
> DOES NOT fire on 'who owes me money' (deliberately unowned — AR is not pipeline; AR proper stays gated on an accounting connector).
> DOES NOT fire on 'brief me on the' (catch-all topic brief).
> Never claims these bare tokens (deliberately NOT quoted triggers here; each stays with its owner or unowned): deal (singular), lost, move, update, kill, proposal, where are we with.

> Loose-status handoff: *where are we with the [name] deal* stays workspace-manager's catch-all; its ladder hands the turn here when ENTITY_RESOLVE lands on a deal thread. `go [deal name]` navigation stays workspace-manager's.
