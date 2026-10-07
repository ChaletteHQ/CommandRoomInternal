---
name: show-my-list
surfaces: both
description: "The retired discuss-later list, drain-only — renders whatever is still open read-only so each item can be cleared or dropped; nothing adds to it anymore (new items go to My Plate or a contact note). Triggers: 'show my list', 'whats on my list', 'what to discuss', 'what do i need to discuss', 'discuss list', 'show discuss list', 'my list'. Also hosts the mute ledger (see Routing). Does NOT fire on 'show my reminders' / 'my reminders' / 'remind me about' (show-my-reminders — the date-pinned reminder lane)."
---

# show-my-list

**The list is RETIRED (MLK1, M ruling 2026-07-21).** The `Add to my list` capture verb (formerly `Log to discuss` pre-v2.14.4) no longer renders anywhere and no capture path writes new `commitment_to_discuss` events — the live workspace showed the captures were context-free fragments, undecidable later (UX review findings 1 + 11). New items go to My Plate (`add to my plate`) or land as a note on the person/thread they belong to.

This skill remains for two things: (1) the FOSSIL READER — whatever `commitment_to_discuss` events are still open render read-only here so the user can drain them (clear or drop; the backlog only shrinks); (2) the MUTE LEDGER (`show muted` / `show snoozed`), which is unaffected by the retirement and lives here permanently.

## Behavior

### Step 1 — Discover plugin root + render via the audit harness

Per CONTRACT.md Rule 22, every multi-step bash invocation uses dynamic discovery:

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
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "plate_helpers:discuss_list"}'
```

The answer is `{open_items, open_count}`, read where the data is:

- every `commitment_to_discuss` event, minus those already resolved or
  skipped. The canonical closure shape (v3.11.4+, per
  references/SOURCE_OF_TRUTH.md): the `resolved` click writes
  `commitment_resolved` with `data.commitment_id` = the discuss event's seq;
  `skip` writes `chat_dismissal` with `data.target_id`. The legacy shape
  (pre-v3.11.4 `thread_resolved` with `data.target_id`) closes items too, so
  in-flight items close correctly.
- dismissal liveness comes from THE mute ledger (v4.6.0 S4) —
  `mute_ledger.active_dismissal_target_ids(events, <now>)` honors the TTL the
  dismissal was written with AND any later unmute (`chat_dismissal_cleared`).
  Pre-S4 this filter treated every historic skip as permanent — a 24h skip
  suppressed the item forever.

Group `open_items` by person (who you'd discuss with) — the user sees "next time
you talk to X, mention these" rather than chronological order. Person
resolution order per item, first hit wins: (1) `data.person_id` present ->
display name from entities.json people; (2) no person_id: resolve the captured
name string via aliases.json (exact match) to a person id, then entities.json
for the display name; (3) still unresolved: fuzzy-match the name string against
entities.json people display names; (4) no match at all: group under the
literal captured name (never drop the item just because the person didn't
resolve).

Keep the answer. Then build a data view:

**Executive Output Standard (EXEC1, v3.20.0+) — queues get triage math, NOT meaning.** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`, this is a queue, so the synthesis-lead rule FORBIDS a narrative lead — **the header is a quantified count line**, not a theme. Compute it from the open items: total · how many trace to a valued org · the summed dollar (via `quantify.money_time_tag` / the org's revenue field — only figures that derive from substrate, never an estimate) · the oldest age. Each item carries its own quantify tag when `money_time_tag` returns non-None; otherwise no tag (never a fabricated dollar). No "what this list says about your week" sentence — the reader's next act is triage.

```python
from quantify import money_time_tag  # EXEC1: the only sanctioned source of $ tags

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "show-my-list",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    # EXEC1 quantified count line (triage math, no narrative). Plain English —
    # "{n} tied to revenue", never "{n} touch revenue" (reads broken), and ages
    # spelled out ("12 days"), never cryptic "12d". Two degraded renders when
    # revenue doesn't derive — never leave a dangling "—":
    #   (a) items touch revenue but no dollar derives from substrate ->
    #       "Your list — {n_open} to discuss · {n_revenue} tied to revenue · oldest {oldest_days} days"
    #   (b) nothing touches revenue (n_revenue == 0) -> drop the clause entirely:
    #       "Your list — {n_open} to discuss · oldest {oldest_days} days"
    "header": f"Your list — {n_open} to discuss · {n_revenue} tied to revenue (${total_k}K) · oldest {oldest_days} days",
    # MLK1 retirement line — REQUIRED on every fossil render, verbatim:
    "sub_header": "This list is retired — new items go to My Plate or a contact note.",
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            # Group by person/attendee — items each share a "next time you talk to X" context
            {
                "n": i,
                "icon": "💬",
                "name": person_name,
                # Per-item quantify tag appended ONLY when non-None (never an estimate).
                "context_tag": f"next time you talk to {person_name} ({item_count})"
                               + (f" · {tag}" if (tag := money_time_tag(items_for_person[0], entities)) else ""),
                "body_lines": [f"- {item['data']['summary']}" for item in items_for_person],
                # MLK1 D3 — drain verbs ONLY (Done / Drop). `skip` is gone
                # from new renders: a snooze grows nothing but keeps the
                # fossil circling; the two offered verbs both shrink it.
                "actions": [f"{i} resolved", f"{i} drop"],
            }
            for i, (person_name, items_for_person) in enumerate(grouped, start=1)
        ],
    }],
}
```

### Step 2 — Render through the canonical pipeline

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"data_view": <the data view built above>, "name_hint": "show-my-list", "wrapper": "fragment", "workspace_root": "<WS>"}, "name": "plate_helpers:render_list_page"}'
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

The render runs where the data is — the same renderer and the same
wrapper-contract validator `widget_transport.render_and_persist` runs — and the
answer is `{html, page_rel, bytes_len, pending_rows}`. Land the persisted page
through the one write door, then hand `html` to `mcp__visualize__show_widget`
as `widget_code`, verbatim (EW2+T, F-15 — shared/CHAT_ACTION_WIDGET.md
§ Transport). Never hand-compose or post-process the HTML.

```bash
python3 "$RT/shared/scripts/workspace_access.py" write --json '{"data": "<the html from the answer above, verbatim>", "expected_mtime": null, "rel": "<the page_rel from the answer above>"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "show-my-list", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the render answer above, in order>]}'
```

When the render's `pending_rows` is empty there is nothing to append.

The canonical renderer applies all v2.13.0+ validators (action verbs in CANONICAL_ACTIONS, no leak patterns, data-shape OK, wrapper contract) inside the transport call. Same enforcement as scheduled-task surfaces.

### Step 3 — Write a fire-marker event so apply-choices can identify this surface (v2.14.19+)

Before posting the widget, append a `pack_run` event to events.jsonl. **Use the canonical helper `log_pack_run` (`shared/scripts/log_pack_run.py`), which routes through the locked writer `atomic_append_jsonl` (SPEC GATE1 / A1) — do NOT hand-roll a `next_seq`+`open('a')` append or a raw `>>`.** The event shape:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"duration_ms": <elapsed_ms>, "fired_via": "manual", "kind": "list", "surfaced": <n_open>, "workspace_root": "<WS>"}, "name": "plate_helpers:plan_list_marker"}'
```

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "show-my-list", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the marker answer above, in order>]}'
```

The helper runs `log_pack_run(workspace_root=WORKSPACE_ROOT, kind="list",
surfaced=n_open, duration_ms=elapsed_ms, source_skill="show-my-list",
fired_via="manual")` beside the data with its append held — `kind="list"` is a
non-task fire-marker, not a scheduled-task receipt, and `fired_via="manual"` is
the canonical vocabulary (v4.5.2 R1; legacy "user-trigger" still parses) — and
the ONE row it writes is landed by the append above.

apply-choices reads the most recent fire-marker (within 60 min) to identify which orchestrator's handler to dispatch through. Without this event, clicking `resolved` or `drop` on a discuss-list item silently no-ops because apply-choices can't tell what surface it came from. (This was the v2.7.x→v2.14.18 silent-no-op bug from the simplify-pass Batch 7 finding #4.)

### Step 4 — Post widget, then STOP

Widget IS the post. No commentary.

If 0 items match: the backlog has drained — surface ONLY the plain-English empty state: `Your list is empty. This list is retired — new items go to My Plate or a contact note.` No widget, no fire-marker (nothing to dispatch), no retirement widget banner — the empty state is the whole render.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary (these are **scheduled chats** to the customer — never "scheduled tasks" or "daily check-ins" in rendered copy).
- BAD: "Your list — 5 to discuss · 3 touch revenue — $45K · oldest 12d"
- GOOD: "Your list — 5 to discuss · 3 tied to revenue ($45K) · oldest 12 days"

## What this skill does NOT do

- Writes exactly ONE event per fire: the Step 3 `pack_run` fire-marker (via `log_pack_run` → the locked writer). Beyond that single append it does not modify events.jsonl — clicking widget actions writes new events through apply-choices, that's separate
- Does NOT cross-reference against open commitments (that's CRU layer scope, v2.14.5+)
- Does NOT auto-surface based on calendar (the user pulls when they want; not pushed)
- Does NOT touch connectors (no Gmail / Calendar / Granola fetches)

Local read of events.jsonl + the one Step 3 fire-marker append + render through canonical pipeline.

## Mute ledger mode — `show muted` / `show snoozed` (v4.6.0 S4)

The SECOND surface this skill renders: every live mute, so a timed mute
stops being a one-way door. On "show muted" / "show snoozed":

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "plate_helpers:live_mutes"}'
```

The answer's `mutes` are `mute_ledger.live_mutes(events, <now>)` over the ledger
`cru_match.load_events_defensively` reads — every live mute, oldest first.

One widget row per ledger row, oldest first: what was muted (the row's
`note`, else its `target_id` resolved to the referenced item's title, else
the dismissal's `reason`), which chat it came from (`surface`), and the
remaining time VERBATIM from `ttl_label` ("3 days left" — every mute states
its duration, the F-59 rule). Actions per row: `unmute` (**Unmute**) ·
`skip` (**Snooze (1 day)** — hides the ledger row for a day, never touches
the underlying mute). Embed the row's dismissal `seq` verbatim (widget
identity contract); pass `source_skill: "show-my-list"` and write the same
Step 3 fire-marker. Dispatch: apply-choices § show-my-list mute-ledger rows
(`unmute` → the ledger's clear writer; ack says the item re-surfaces on its
next chat). 0 live mutes → plain English: `Nothing is muted right now.` —
no widget, no fire-marker.

Fences: permanent never-track rules are NOT in this ledger (they are
suppression rules in the workspace config, lifted by editing the rules
file — when any exist, add ONE footer line with their count); learned
suppressions (surface-preferences) are a separate durable layer, also not
timed mutes.

## Trigger pattern

`show my list` / `whats on my list` / `what to discuss` / `what do i need to discuss` / `discuss list` / `show discuss list` / `my list` / `show muted` / `show snoozed`

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside `widget_transport.render_and_persist`; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## Routing (full trigger corpus)

The mute-ledger phrase family (v4.6.0 S4) rides on this skill — the
description is budget-capped (G11), so these route via this section (same
rule as the other budget-capped skills; enforced by tests/triggers.yaml).

> Also fires on: 'show muted', 'show snoozed', 'what's muted', 'what did I snooze' — the mute-ledger mode: every live snooze/mute with its remaining time and an Unmute action. Does NOT fire on 'never track' rule management (those are permanent suppression rules, not timed mutes).

## Why the retirement (MLK1, 2026-07-21)

`add to my list` was the most confusable verb in the product: a sibling of `Add to My Plate` on the same rows, and a third "my ___" lane beside My Plate and my reminders. M's live list showed what the capture-then-curate design actually produced — context-free fragments with no decidable next step. The verb is gone from every surface; existing history stays readable here until it drains, and persisted old widgets still dispatch (the retired wire id keeps its original meaning — see apply-choices).

## See also

- `show-my-reminders` — the OTHER personal lane, fenced by design: reminders are date-pinned nags (`reminder` events, pin to the morning brief daily until cleared); this list is the retired discuss-later queue (`commitment_to_discuss`, drain-only, surfaces only when pulled). Neither skill reads the other's events, and the trigger families never cross ("my list" vs "my reminders")
- `add to my plate` (CHAT_ACTION_WIDGET.md action reference) — the own-it-now verb that covers the old capture intent when the item is really a task
- apply-choices § orphan-note re-route — a typed widget note with no action selected becomes a `note` on the resolved person/thread (or is declined honestly), never a list item
- `usage report` — separate read of events.jsonl for telemetry; this skill is for action items
