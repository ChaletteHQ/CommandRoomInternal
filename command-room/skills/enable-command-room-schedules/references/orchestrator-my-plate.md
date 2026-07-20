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

You are firing the Command Room "My Plate" chat (CTS1 Surface 2). Surfacing what M has to DO — promises M made to other people (with status drafts) and M's own to-dos. Read-mode is EXECUTION: these are M's moves, not other people's. This chat is a pure act-list: no unowned/unconfirmed bookkeeping (that confirm tail lives on the Waiting On chat — §2.4 ruling), and NO connector pre-scans (the waiting-on fire at 8:30 and the maintenance reconcile job already reconciled the substrate; this surface reads events.jsonl and today's calendar only).

# Phase 1 — Always run (no idempotency gate)

This orchestrator ALWAYS runs when fired — cron or manual. A fire receipt writes at the end of every fire; re-fires are safe (closed items simply don't load; every closure path is idempotent).

# Phase 2 — Setup

- Compute today's date in local time via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)`.
- Read entities.json + aliases.json; resolve M's primary `user_id`.
- Read voice calibration (cache once for the session).
- **Resolve the mail tools through the seam** (`tool_discovery.discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))`) — needed only for the send/draft dispatch on Promised rows; a missing mail tool degrades those rows to draft-text-only, never blocks the fire.
- Read the surface knobs: `get_config(WORKSPACE, "my-plate", {"personal_cap": 7})` — the Personal group cap (CTS1 §4.2, adjustable via freeform tune "show me more personal items" → `save_skill_config`). Also read the commitment-family knobs `get_config(WORKSPACE, "commitments", ...)` for `chase_tone` (the status-draft register — the store stays under the `"commitments"` key; ONE knob set for the family, the fr-items render on the Waiting On chat only).

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2)

Identical contract to every scheduled chat (see orchestrator-commitments.md Phase 2.9 for the full tier semantics — manual/none/note/degrade). Compute via:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'my-plate', fired_via='<scheduled|manual>')))
"
```

Carry the returned `receipt_fired_via` into the Phase 8 receipt — never guess it.

# Phase 3 — Build the two groups (read-side partition; code, never prose)

Load the projected open set ONCE via `cru_match.load_open_commitments` (deferral/wording/reassignment/kind/sub-item folds already applied), then partition:

```python
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
import sys; sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from commitment_state import count_commitments
from commitment_activity import derive_commitment_movement
from surface_split import partition_surfaces, counterparty_unresolved

opens = load_open_commitments(events_path)
movement = derive_commitment_movement(events_path)   # ONE derivation per fire (F-54)
counts = count_commitments(opens, user_person_id=USER_ID, now_iso=NOW, movement=movement)
part = partition_surfaces(opens, USER_ID)
promised = part["promised"]      # Group A — renders FIRST
personal = part["personal"]      # Group B — capped
```

Rules the partition already encodes (never re-derive): TOP-LEVEL items only (SUB1 — a live sub-item never gets its own row; the parent carries the progress chip exactly as on every other surface), effective kind post-reclassify-fold (§2.2 Option B), pending_review and unowned rows excluded (they are Waiting On's confirm tail, NOT this chat's).

Base filters carried over from the daily-chat contract: multi-shape field reads via `_commitment_field`; confidence via `_commitment_confidence >= CONFIDENCE_SURFACE_MIN`; live `chat_dismissal` mutes via `mute_ledger.active_dismissal_target_ids`; dormant/archived-project exclusion; surface-preference suppression (`is_suppressed(prefs, "commitments", ...)`). Filters shape SURFACING only — header counts are untouched.

**MEETING TODAY (v4.5.2 C1, owner-me half):** run `commitment_state.match_commitments_to_meetings(opens, todays_meetings, user_person_id=USER_ID)` with today's resolved calendar events (calendar unavailable → skip the bucket). Render the matches whose surface is `promised`/`personal` as the FIRST rows of their group, labeled with the meeting; the C1 exemptions (due-date, aging floor, confidence) apply. The owner≠me matches are Waiting On's meeting bucket — never render them here. Cap 3 meeting rows, inside the overall budget.

## Group A — PROMISED (someone's waiting; renders FIRST)

Sort: overdue first (oldest overdue at top, by effective due — the loader already folded deferrals), then by due soonest, then undated by age. No cap beyond the overall widget budget of 7 actionable rows per fire (Promised takes priority over Personal inside it — relationships at stake; live basis 16 promised vs 69 personal).

**Counterparty-unresolved rows (CTS1 §8.2 — the 49 orphaned promises):** for each row where `surface_split.counterparty_unresolved(ev, USER_ID)` is True, tag the row `(counterparty unresolved — who was this for?)`. These are REAL promises whose counterparty linking failed (Bug #103) — they stay Promised, NEVER auto-demote. The DRIP fixup rides the row's dropdown with two existing verbs:
- `reassign to [name]` — **on a counterparty-unresolved row this attaches the named person as the COUNTERPARTY** (owner stays M): `commitment_state.reassign_commitment(workspace_root, <id>, new_counterparty_id=<resolved person_id>, new_counterparty_name=<display name>, reassigned_by=<user person_id>, reason="counterparty resolved from My Plate", source_skill="commitments", confirmed=True)`. The row's annotation makes the question explicit, so the Reassign label reads as "assign this promise to the person it's for". Ack: `✓ Got it — that one's for [name].` (An unresolvable name → item-level error, nothing written.) This row-class-specific dispatch is documented in apply-choices § `cr-commitments`.
- `make task` — demote to Personal when M says nobody's actually waiting: `promote_task_to_commitment(new_kind="task", reason="user demoted — no counterparty", source_skill="commitments")`.
The BATCH fixup (bites of ~5, resumable) lives on Friday commitment-triage — this drip is per-row, opportunistic, never a wall of 49.

**Per-row status draft:** external-recipient rows (requester/counterparty resolved with email) get a lazy status draft via `email-writer` — fixed voice tilt "confident + concrete + brief — minimal apology, no grovel, focus on the path forward" (register per the `chase_tone` knob); brief acknowledgment, new ETA (default this Friday EOD), one-line reason when events.jsonl shows clear cause. NO grouping — each thing M owes is its own status email. Counterparty-unresolved rows get NO draft (no recipient to draft to — the fixup is the action). Producible deliverables (title contains "deck", "memo", "draft", "doc", "plan", "review") annotate `← recommended` on `prep deep work`.

## Group B — PERSONAL (my own work; capped)

Sort: dated first (due soonest), then most-recently-touched (the `movement` map's ts; capture ts floor). Cap at `personal_cap` (default 7) rows, then ONE tail line in the section footer: **"+N more — say 'show my plate' for everything."** (Live basis: 69 personal — an uncapped group swamps the chat; the full set is one reply away.) Effective kind `task` plus counterparty-less `scheduling`/`agenda` land here (the partition already routed them). No drafts — these are M's own moves. The 30+ day stale tail is Friday triage's `stale_tasks` sweep, not this chat's job — never render "still on your plate?" here.

# Phase 8 — Memory updates (silent per Rule 9)

ONE call to the canonical receipt helper — never hand-rolled:

```python
from receipts import log_receipt
log_receipt(
    WORKSPACE_ROOT, "my-plate",
    fired_via=lateness["receipt_fired_via"],
    surfaced=n_surfaced,
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={"promised": len(promised), "personal": len(personal),
                "personal_capped_to": personal_cap, "errors": [], "telemetry": {...}},
)
```

Telemetry via `telemetry.build_pack_run_telemetry()` in `extra_data`. Silent — never narrated.

# Phase 9 — Post the chat turn (renderer-driven, ENFORCED)

Renderer pre-flight, then build the data view and post via the transport — the same hard pipeline as every widget orchestrator (ZERO-MANIPULATION CONTRACT above; empty-state via `widget_mode: "all_clear_summary"`, never hand-built):

```python
from widget_transport import render_and_persist

sections = []
if promised_rows:
    sections.append({"title": "↗ PROMISED — someone's waiting", "count": ..., "items": ...})
if personal_rows:
    sections.append({"title": "PERSONAL — your own list", "count": ...,
                     "items": ...,  # capped at personal_cap
                     "footer_note": f"+{n_hidden} more — say 'show my plate' for everything" if n_hidden else None})

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "commitments",  # dispatch family — apply-choices routes these tuples through the commitments handlers; the taskId is a scheduler concern
    # v4.5.2 R4 + v4.6.0 MC2: numbers verbatim from counts["headline"] — the
    # SAME five buckets every surface shows (F-56 parity), re-labeled for this
    # chat's frame. "waiting on others" rows live on the Waiting On chat.
    "header": f"My Plate — {n_you_owe} on your plate ({n_promised} promised · {n_personal} personal) · {n_owed_to_you} waiting on others (see Waiting On) · {n_total} total open",
    "sections": sections,
    "quick_read": quick_read,
}

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="my-plate")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code, verbatim.
```

`n_promised = len(promised)`, `n_personal = len(personal)` — and assert `n_promised + n_personal == counts["headline"]["you_owe"]` before rendering (the CTS1 parity check; a mismatch is a partition defect — fail the fire loudly rather than render disagreeing numbers).

**Widget grammar (RULED §4.2 — existing `CANONICAL_ACTIONS` verbs only, no new interaction patterns):** the one or two most-common actions render as visible buttons — **Done** (`resolved`) and **Later…** (`push to [date]`) on every row; email-shaped Promised rows show **Send** / **Draft** / **Snooze (3 days)** instead (t3 FB-4, FB-17 — `snooze 3d` is a primary on email-shaped rows) with Done in the dropdown. Everything else — `prep deep work`, `promote`, `make task`, `drop`, `reassign to [name]`, `add to my list` (and `snooze 3d` on non-email rows) — rides the per-row `— more —` dropdown. Display labels from `verb_taxonomy` only. Rows carrying `push to [date]` suppress the separate snooze option (FB-3 merge).

**Per-item shape, PROMISED (email-shaped — the shape formerly documented as "YOU OWE direction A" in orchestrator-commitments.md):**

```python
{
    "n": 1,
    "icon": None,
    "name": "Sam",                          # who's waiting (resolved spelling, never ASR)
    "subject": "Send Q2 deck",              # commitment title
    "context_tag": "committed Apr 12, 16 days overdue",
    "original_thread": {...},               # v2.14.36+ MANDATORY when source_ref exists — same accordion contract as every surface
    "metadata": [("To", "sam@example.com"), ("Subject", "Q2 deck: status")],  # dash-free subjects (S3 gate)
    "body_lines": [...],                    # email-writer's status draft
    "actions": ["1 send", "1 draft", "1 push to [date]", "1 prep deep work", "1 resolved", "1 snooze 3d", "1 add to my list"],
}
```

Counterparty-unresolved variant: no `metadata`/`body_lines` (no recipient), `context_tag` carries `counterparty unresolved — who was this for?`, actions `["N reassign to [name]", "N make task", "N push to [date]", "N resolved", "N drop", "N snooze 3d"]`.

**Per-item shape, PERSONAL (the shape formerly documented as "Self-commitment"):**

```python
{
    "n": 4,
    "icon": "⚙",
    "name": "Self",
    "subject": "Refresh Qualiphy data pull",
    "context_tag": "logged Mar 9, 50 days ago",
    "actions": ["4 resolved", "4 push to [date]", "4 prep deep work", "4 promote", "4 snooze 3d", "4 add to my list"],
}
```

Pre-build rules (same as every surface): resolve every `person_NNN`/`org_NNN` to canonical spellings; subjects pass the S3 voice gate; `original_thread` mandatory when a source_ref exists; sub-item families render nested under their parent with the progress chip (SUB1 — the loader's stamps; "all sub-items done — close it?" is a PROPOSE).

**Step 3 — chat-links section:** after the widget, the standard **Links:** block per `shared/CHAT_ACTION_WIDGET.md` (mail-thread / Granola URLs; self-items render `(no source — Self-commitment)` or are skipped; omit the block when no row has a source).

# Reply handling (the owner-me handler set — moved here from orchestrator-commitments.md by CTS1)

Parse `N action` (with or without period). All writes through the canonical helpers; apply-choices dispatches widget tuples on `src: "commitments"` — these are the same handlers.

- `N prep deep work` → generate the context-loaded prompt per the Appendix template in `orchestrator-commitments.md` (kept there — tombstone pointers resolve to it).
- `N send` → per `EMAIL_DRAFT_PROTOCOL.md` §3c dispatch order (Zapier-threaded first if configured, native threaded fallback, standalone last). Confirm `✓ Sent to [name] at HH:MM`. Write `outreach_sent` (`source_skill='commitments'`). Commitment stays open with new context.
- `N draft` (and the FB-17-retired `N edit then send` alias, accepted ONLY from in-flight widgets) → v2.12.2/v2.14.4 semantics unchanged (replace body with input verbatim, then lazy-create the draft / send).
- `N keep` → no-op. Confirm `N status kept in Drafts.`
- `N push to [date]` (displays **Later…**) → parse via `commitment_state.parse_later_when`, then `commitment_state.later_route`: every row on THIS chat is M's own, so it lands as the `commitment_updated` due-date shift (`data.new_due`); update any staged draft to mention the new date.
- `N resolved` → `commitment_state.close_commitment(workspace_root, <data.id verbatim>, resolved_by=<user person_id>, evidence="user marked complete via the My Plate task", source_skill="commitments", user_confirmed=True)`. Parent-with-open-sub-items raises `OpenSubitemsError` → one-line cascade confirm, then re-dispatch with `close_subitems=True`.
- `N promote` (Personal→Promised) → `promote_task_to_commitment(workspace_root, <id>, new_kind="promise", source_skill="commitments", reason="user promoted from My Plate")`. Ack names the counterparty it will be tracked against (or notes it needs one — the §5 gate will nudge at the next capture).
- `N make task` (Promised→Personal demote) → `promote_task_to_commitment(..., new_kind="task", reason="user demoted from My Plate")`.
- `N reassign to [name]` → on a counterparty-unresolved row: the COUNTERPARTY attach documented in Phase 3 Group A. On any other row: the standard S4 owner reassignment (`new_owner_id=...`, confirmed=True) — the item leaves My Plate and lands on Waiting On next fire.
- `N drop` / `N snooze 3d` / `N add to my list` / `N fix wording: <text>` / `N split into: ...` / `N add subitems: ...` → identical dispatches to the commitment-family handlers (apply-choices § commitment-triage documents the exact calls).
- `show my plate` (typed in this chat, or anywhere) → re-render THIS widget with the Personal cap lifted (full Personal group, paginated by the transport) — same pipeline, same validators; never a markdown list.
- `show muted` / `show snoozed` → the mute ledger view (show-my-list's ledger mode).

# What this orchestrator does NOT do

- Does NOT run connector pre-scans (waiting-on + the maintenance reconcile job own substrate hygiene).
- Does NOT render unowned or pending_review rows (Waiting On's confirm tail — My Plate is a pure act-list).
- Does NOT auto-send anything; does NOT auto-demote counterparty-unresolved promises (Bug #103 — they are real promises).
- Does NOT re-render the FRP "Make this yours" fr-items (the Waiting On chat owns them).
- Does NOT modify entities.json directly.
