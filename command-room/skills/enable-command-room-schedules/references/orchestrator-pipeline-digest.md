# Orchestrator prompt — Pipeline Digest

This file is the EXACT prompt the bootloader cats and executes for `taskId: pipeline-digest`. Fires 8:00 AM Tuesday local time per `shared/scripts/schedule_config.py` `DEFAULT_SCHEDULES` (`0 8 * * 2`) — the SPEC-10 slot: Monday carries the Staff Meeting, and Sunday evening's `deal-signals` maintenance job has just refreshed the proposal queue. NEW in PIPE1 Part 2. **NOT a first-install task** — it is proposed only when at least one open tracked deal exists (`schedule_proposals`), and registers via `change-schedule` (`add pipeline digest`) / registration Phase 6 `add` / `command-room-update-bridge`, never on a fresh workspace and never silently. The pipeline-tracker skill's `digest.enabled` config records the user's PREFERENCE only; registration always flows through the schedule machinery.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. Rules 1–18 are non-negotiable. The digest is a **widget action surface**: the ranked pipeline report widget, produced EXACTLY as `skills/pipeline-tracker/SKILL.md` § "The report" specifies — post via `widget_transport.render_and_persist` (`source_skill: "cr-pipeline"`; the full validator chain runs inside the one call), then pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed HTML (`shared/CHAT_ACTION_WIDGET.md` § Transport, F-15).

**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. Surface the link block per `shared/CHAT_ACTION_WIDGET.md` "Post-widget chat-links section".

**Skill delegation rule:** this orchestrator is the SCHEDULED-FIRE wrapper for `skills/pipeline-tracker/SKILL.md`. That skill is the source of truth for the report computation (the hard-gated `deal_state` / `deal_health` / `pipeline_math` block — ONE computation path, never a digest-local re-derivation), the tile band, row shape, ranking, the reconciliation line, the widget actions, and the receipts. This orchestrator's job: (a) resolve plugin + workspace paths, (b) compute the since-last-digest movement, (c) execute the report verbatim with the digest framing, (d) post the widget once, (e) write the receipt, (f) STOP.

**Adjudication stays in the Staff Meeting (FB-20).** Open deal-kind proposals (`deal_update` / `deal_creation`) are COUNTED here and pointed at — never rendered as confirm rows on this surface. The Staff Meeting is the sole proposal-adjudication door; this digest acts on tracked deals directly (its widget's own verbs) and hands suggestions to the door that owns them.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

**The widget (plus the movement header lines and the Links section) IS the chat turn. Then YOU STOP.**

1. **No hand-written widget HTML, ever** — `render_and_persist` or nothing.
2. **No narrating the rows.** The user can see the report.
3. **No proposal confirm rows here** (FB-20 — the Staff Meeting owns adjudication). The pending count line + `staff meeting` pointer is the ceiling.
4. **No auto-anything.** Every deal mutation is a user click through apply-choices → `deal_state`, or a typed command. This fire writes ONLY: its `pack_run` receipt and the transport's own widget persist.
5. **Zero open deals → the honest one-liner** (*"No open deals tracked — say `new deal [name] with [org]` to start one."*), no tiles, no empty frames, receipt still written with `surfaced=0`.

---

You are firing the Command Room "Pipeline Digest" chat — the weekly deal review: what moved since last Tuesday, what's rotting, and the top three moves.

# Phase 1 — Always run

No idempotency gate: a manual `pipeline review` earlier in the week never blocks the Tuesday fire. The receipt trail carries dedup context.

# Phase 2 — Setup

The bootloader already resolved `PLUGIN_ROOT`, `WORKSPACE`, and this orchestrator file path. Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code; never compute it from this computer's clock. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).

# Phase 2.9 — Run mode + lateness check

Per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when Cowork's scheduler executed this registered prompt (catch-up deliveries included); `manual` when a human caused the fire; uncertain = `manual` (F-47 P1a). Then the shared helper, never inline math:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'pipeline-digest', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


Branch on `tier`: `manual` / `none` / `exempt` / `unknown` → run normally, no timing talk. `note` → open the chat with the returned `banner` line verbatim. `degrade` (>24h late) → do NOT render the report; post ONLY the returned `degrade_notice` line, write the receipt (`surfaced=0`), STOP. Carry the returned `receipt_fired_via` into the receipt — never guess it.

# Phase 3 — Since-last-digest movement (code, not memory)

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from receipts import iter_receipts
import deal_state
ws = '<workspace_root>'
last = None
for r in iter_receipts(ws, task_ids=['pipeline-digest']):
    last = r  # receipts iterate oldest-first; keep the latest
since = (last or {}).get('ts') or ''
events, skipped = deal_state.load_deal_events(ws)
moved = [e for e in events if str(e.get('ts') or '') > since
         and e.get('type') in ('deal_created', 'deal_stage_changed', 'deal_won', 'deal_lost')]
counts = {}
for e in moved:
    counts[e['type']] = counts.get(e['type'], 0) + 1
print(json.dumps({'since': since, 'counts': counts, 'n_skipped': len(skipped)}))
"
```

Render as ONE header line in plain deal language, drop-zero per bucket: *"Since last digest: 1 opened · 2 moved · 1 won."* First digest ever (`since` empty) → *"First digest — here's the whole board."* Never event-type names in chat.

# Phase 4 — The report, verbatim

Execute `skills/pipeline-tracker/SKILL.md` § "The report" end to end against this workspace with `cfg` from `get_config(workspace_root, "pipeline-tracker", DEFAULTS)`: the hard-gated computation block, tile band, ranked rows in `rank_deals` order, `⚠ no next step` flags, untracked-deal adoption rows, the D9.1 reconciliation line (`gap` from `pipeline_math.prospects_not_in_pipeline` — render the count + names verbatim, drop at zero), widget via `render_and_persist` → `show_widget`. **Top-3 moves:** after the widget, ONE chat line naming the top 3 ranked rows' actions in plain English (*"This week: push Acme past the redlines, chase the Northwind proposal, give Beacon a next step."*) — derived from the ranked output only, no re-ranking in prose.

**Pending suggestions pointer:** count open deal-kind proposals via `brain_proposals.load_open_proposals(ws)` filtered to `kind in ("deal_update", "deal_creation")`. When >0, ONE line: *"N deal suggestions are waiting — say `staff meeting` to review them."* Zero → nothing.

# Phase 5 — Receipt + STOP

The report's own Writer-Contract receipt (`log_receipt(WORKSPACE_ROOT, "pipeline-tracker", ...)`) belongs to the report path and fires inside Phase 4. This task ALSO writes its own: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "pipeline-digest", fired_via=<Phase 2.9 receipt_fired_via>, surfaced=<n open deals rendered>, extra_data={"moved": <Phase 3 counts>})` — the receipt Phase 3 keys the next since-window on, and the watchdog's liveness read. Then the Links section, then STOP.
