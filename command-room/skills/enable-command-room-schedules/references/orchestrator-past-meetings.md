# Orchestrator prompt — Past Meetings

This file is the EXACT prompt registered with `create_scheduled_task` for `taskId: past-meetings`. Fires 5:00 PM weekdays local. Replaces the v2.7-v2.10.1 `cr-meetings-processed` task (renamed for executive clarity). Events this file writes carry `source_skill='past-meetings'` (bare since v2.14.27); workspaces with pre-rename history at `source_skill='cr-past-meetings'` stay valid as append-only history.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Brief save path (v2.13.0+):** all `.docx` briefs save to `_hq/meetings/` via `shared/scripts/brief_path.py` `get_brief_path("past_meeting", slug, date)`. NEVER hand-roll paths. NEVER save to `[Project]/meetings/` (that path didn't always resolve in Cowork's sandbox).
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for legacy rules; follow `shared/CONTRACT.md` for v2.13.0 strict contract.
**Email-draft mechanics (follow-up drafts):** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Past Meetings produces follow-up draft TEXT only — actual sends happen through Inbox / Commitments where `N send` follows §3c.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules. Pre-v3.5.0 each orchestrator inlined a ~25-line copy; v3.5.0+ they reference the shared file.

Past-meetings-specific scope notes:
- `.docx` meeting briefs in `_hq/meetings/` continue per Phase 4 — those are documented per-meeting deliverables, separate from the post-widget output surface the STOP CONTRACT governs.
- Re-runs of past-meetings (`regenerate past meetings`, `re-process today's meetings`) re-execute Phase 1 onward; do NOT save intermediate outputs.

---

You are firing the Command Room "Past Meetings" chat. End-of-day pass: auto-process today's meetings, commit the obvious, surface only ambiguous items for the user's call.

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


Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (the widget-render/post phase): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Find unprocessed meetings (everything since the last successful run)

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
- Check events.jsonl for `meeting_processed` event with matching meeting_id → skip if exists.
- Check `meeting_skipped` event → skip if exists.
- Filter out personal calls (no business attendees, single 1:1 with non-business contact).
- Filter out internal-only meetings UNLESS they have decisions worth committing.

Up to 5 unprocessed meetings to process this fire.

**⛔ BATCH-CAP HONESTY GATE (SPEC CATCHUP1 F-1) — MANDATORY.** That cap of 5 and a widened window are a trap together: a window that finds 12 unprocessed meetings processes 5, the fire's receipt lands, and the NEXT window starts after that receipt — silently orphaning the other 7 forever, with a green receipt on the record. So the receipt has to record how far processing ACTUALLY reached, not when the fire happened:

- Sort the unprocessed set OLDEST FIRST and process from the oldest end. The backlog drains in order; the newest meetings are the ones the next fire re-finds, and the next fire is hours away.
- If ANY meeting inside `[start, end]` is left unprocessed when the fire ends — batch cap hit, transcript fetch failed, Granola timed out mid-batch — Phase 5's receipt MUST carry `window_incomplete_before: "<ISO start time of the OLDEST still-unprocessed meeting>"`. `catchup_window` resumes from that value instead of the receipt time, so nothing is stranded. **Compute the value, never hand-write it:** `from catchup import receipt_window_marker; marker = receipt_window_marker(window, incomplete=True, oldest_unhandled=<the oldest still-unprocessed meeting's start>)` — it clamps into the window and returns `None` when the fire drained it, meaning omit the key. This surface CAN name `oldest_unhandled` because its cap truncates a chronologically sorted list, so the marker advances as the backlog drains; `weekly-recap`'s cannot, and passes None (its own gate explains why).
- A meeting deliberately excluded (personal, internal-only, `meeting_skipped`) is NOT unprocessed — it is handled. Only meetings this fire still owes work for count.
- **Carry the marker forward.** If this fire processed nothing (Granola unavailable, zero capacity) and the previous receipt already carried a `window_incomplete_before`, the receipt this fire writes carries the SAME value. A receipt without the marker means "everything before this point is handled" — writing one while a backlog is outstanding is the orphaning bug, restated.
- Only when the fire drained its entire window does the receipt omit the field.

# Phase 4 — Per-meeting auto-processing

For each meeting:

1. **Fetch transcript** via Granola MCP.
2. **Run `meeting-notes` skill** silently. Extracts decisions, commitments (with owner/due/requester per shared/COMMITMENT_SCHEMA.md), action items, discussion topics.
3. **Run `follow-up-ritual` skill** silently. Drafts per-attendee follow-up emails (voice-calibrated). Lazy creation per EMAIL_DRAFT_PROTOCOL — TEXT only.
4. **Score each extracted item by confidence** (existing scan-for-commitments / meeting-notes scoring):
   - HIGH confidence (auto-commit): clear owner + clear date + entity in entities.json + confidence ≥ 0.8
   - LOW confidence (surface as pending): ambiguous owner, vague timeline, conflicting info, new entity not in entities.json, sensitive decisions (firing / pricing / contract terms — flag regardless of confidence)
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
   )
   append_event('<workspace_root>/_hq/data/events.jsonl',
                routed['book'] + routed['review'] + routed['observed'],
                holder='past-meetings.commitments')
   print(json.dumps(routed['summary']))
   "
   ```

   What the helper enforces, in ONE place, because none of it was enforced anywhere before: the **capture floor** (owner + concrete deliverable + consequence — stated in `meeting-notes/SKILL.md` since Stage D and never enforced, which is why a third of this rail's captures were discussed-only), the **cross-meeting fusion guardrail** (the verbatim check below, in code), and **party-only relevance scoping** (`capture_gate.classify_capture`, whose `party-only` default this rail had never consulted because no meeting writer called the gate). Its four outputs: `book` (ordinary open commitments), `review` (written `pending_review` so they land in the needs-your-call queue and never in the open book — BOTH fusion refusals, marked `data.fusion_unverified`, and below-floor captures, marked `data.floor_gated` with their `FLOOR_*` reason as `review_reason`), `observed` (`commitment_observed` — kept and searchable, no open item, no count, no row), `skipped` (near-empty since the ruling below; never narrated). **M RULING 2026-08-01 — below-floor captures are NEVER silently dropped.** They used to be, and the review measured what that cost: on the audit's own hand-judged sample the floor destroyed a real promise for every junk capture it stopped, with no event, no counter and no row left behind. The floor's verdict is now a routing decision, so a wrong call costs one tap in the queue rather than a lost promise. The dated case routes to the queue too — the observed writer refuses dated/money items, and 'surfaces nowhere' was never an acceptable reading of a rail that says a dated item ALWAYS surfaces. Carry `routed['summary']['n_book'] + n_review` into the `meeting_processed` receipt's `extracted_count` / `pending_review_count`, and pass the whole return as `capture_summary=routed` on that same receipt (step 9) so the gates' own counts persist.

6. **Surface LOW confidence items as pending:** your confidence scoring still applies to everything the helper returns in `book` — an ambiguous owner, a vague timeline, conflicting info, a new entity, or a sensitive category (firing / pricing / contract terms) means you pass that item with `pending_review=True` and a `review_reason` in its kwargs, exactly as the safety inversion requires. DECISIONS (not commitments) keep the write path they already had: `meeting_capture.build_decision_event` + `event_gate.append_event`, `committed: true` on high confidence. Add pending commitments to the chat-turn output as a `⚠ Needs your call` sub-block for that meeting.
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

   **If `BRIEF_SESSION_SCOPED=True`** (v5.9.2 — the workspace is a cloud mount, e.g. Google Drive; there is no host-native path and the `computer://` BRIEF_URL will fail with "Failed to load local file." on the customer's machine — QMG field reports 2026-07-28 / 2026-07-31): after the brief file is written and synced, look up its web link through the discovered drive tool (per `tool_discovery.discover_drive_tool()` — search the filename under `_hq/meetings/`) and carry that URL forward for Step 4's links via `brief_path.get_brief_opener_url(path, drive_web_url)`. If the lookup finds nothing, proceed with the `computer://` form — `get_brief_opener_url` falls back on its own.

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

   If output is `MISSING`: the writer failed to save. EXCLUDE this meeting from the Meeting briefs section (no broken links). Surface plain-English: `(Brief for <meeting> couldn't be saved to _hq/meetings/. Re-fire `process the call <name>` to retry.)` Append a `brief_save_failed` event silently.

   On success: cache the BRIEF_PATH + BRIEF_URL on the meeting record. Phase 6 Step 3 uses BRIEF_URL as the `artifact_link.url` (inside widget) AND as the Briefs-section link target (below widget). Single source of truth — no path drift.
8. **Write canonical `meeting` event** (v2.14.19+ — REQUIRED, not optional) to events.jsonl. This is the authoritative record that the meeting occurred. Shape: `{type: "meeting", ts: <meeting_start_local_ISO>, source_skill: "past-meetings", primary_thread_id: <resolved or null>, org_ids: [<the counterparty org(s) this meeting was WITH, when resolved — including an org this very run just created for the counterparty; NEVER the CEO's own org>], person_ids: [<all attendees resolved>], data: {title, source_ref: "granola:<meeting_id>", duration_min, brief_path, attendees_external: [<names not in entities.json>], meeting_type: <sales|internal_1_1|external|board|… — the same classification Phase 4.7's grading derives; ALWAYS stamp it here>}}`. `org_ids` matters even when `primary_thread_id` resolves: a sales call with a new prospect routes to the CEO's own product/GTM thread, which attributes the event to the CEO's org — leaving the prospect org structurally unlinked from the one event that should seed its pipeline record (the PIPE1 D9.1 live gap). Use `ts` = meeting START time per Granola's metadata, NOT the processing timestamp. `meeting_type` is a load-bearing read for the deal-signal detector (PIPE1 D9.1: `meeting_type: "sales"` on an org with no deal coverage proposes deal creation) — stamp it on every meeting event, not only graded ones. This event is what `tell me about <person>` and "when did I last meet with X" queries read from — without it, there's no canonical meeting record (only `meeting_processed` which is a status event, not a meeting event).

9. **Write `meeting_processed` event** to events.jsonl with `meeting_id`, `processed_at`, `extracted_count`, `pending_review_count`. Build it with `meeting_capture.build_meeting_processed_event(..., capture_summary=routed)` — passing Phase 4 step 5's `route_meeting_captures` return stamps `data.capture_counts` = `{n_book, n_review, n_observed, n_skipped, n_floor_gated, floor_reasons, skipped_reasons}`, which is the ONLY record anywhere of what the admission gates did. `n_floor_gated` is the share of `n_review` the capture floor routed — a SUBSET of it, never added to it — and `floor_reasons` tallies which `FLOOR_*` condition gated each one; those two are what make the floor's tuning measurable, and without them a mis-tuned floor is undetectable and the acceptance re-measure has nothing to read. None of it goes in the chat card. Counts and reason tallies only — never a title. This is a SEPARATE event from #8 — `meeting_processed` records that THE ORCHESTRATOR processed this transcript (status), while `meeting` records that THE MEETING happened (data substrate). Both must exist.

**Idempotency note:** if the orchestrator re-fires on a transcript that already has both events, skip the writes (use `source_ref` dedup). Do NOT write a second `meeting` event for the same Granola meeting_id.

**Cross-meeting fusion guardrail (v2.14.19+ — REQUIRED; MECHANIZED 2026-08-01, CAPTUREFLOW A3):** a commitment's verbatim phrase (a 5+ word normalized substring of `data.evidence`, falling back to `data.title`) must actually appear in the transcript text of the meeting it is being attributed to. If it doesn't, the extraction has crossed meetings and the event is NOT written to the book — it is written `pending_review` with the reason "extracted phrase not in the source transcript", which lands it in the needs-your-call queue.

**For commitments this is no longer your job: `route_meeting_captures` (step 5 above) runs the check** — `meeting_capture.fusion_refusal_reason` is the implementation, and passing the step-1 transcript as `transcript_text` is what arms it. Omitting the transcript leaves the check inert (skip-not-fail), so pass it. For DECISIONS, which keep their own write path, run the same test by eye before appending: same rule, same reason string, same outcome.

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
- **Pass `transcript_ts` — THIS meeting's own START time** (EVORDER layer 3, F-27). `fire_start` fences against the start of THIS FIRE, which is a different question: a commitment captured before the fire but AFTER the meeting ended sails straight through it, and then the meeting's transcript closes a promise that did not exist while anyone was talking. Demonstrated by execution: a commitment captured 20:00, a transcript from an 18:00 meeting saying "I sent the revised pricing sheet already, it is done", `fire_start` at 23:00 → `auto_resolve` at 0.75. This is the same value Phase 4 step 8 stamps as the `meeting` event's `ts`. **Pass it with its offset (or in UTC) — never a naive local wall-clock string:** a bare `2026-07-28T18:00:00` is read as UTC, which silently moves the fence by your offset (the CATCHUP1 F-1 connector-boundary class). Omitting `transcript_ts` leaves layer 3 inert, which is safe; a present-but-unparseable value fails SAFE and LOUD — the pass closes nothing at all for that transcript and prints `RECONFENCE: transcript_ts=…` on stderr. Never invent one from the processing clock: "now" is always after every commitment, so a guessed value fences nothing and reads as if it did.
- **Accumulate `diagnostics` across every transcript in the fire** and carry the total onto Phase 5's receipt as `n_stale_evidence_skipped` — the same key both mail rails already report. Pass ONE dict to every `match_transcript_to_commitments` call (the matcher adds to it, never resets it) and read `diag.get('stale_evidence_dropped', 0)` at the end. A non-zero value is the fence **working**, not an error, and needs no report to the user. A fence that drops silently is how F-11 hid for a week.
- **Pass `transcript_source_ref`** — THIS meeting's own ref (`granola:<id>`), the ref Phase 4 stamped on its extractions. `cru_match.commitment_source_refs` is what the fence compares against, so a merged survivor's absorbed refs are covered too.
- **Thread ONE `already_proposed` set across every transcript in the fire**, seeded from `cru_match.open_review_proposal_ids(events_path)` and applied via `cru_match.filter_duplicate_review_targets` — one open review proposal per commitment, on disk and within the fire. Two transcripts in one batch proposing the same commitment (observed live at scores 1.0 and 0.571) is one question rendered twice.

Skip entirely if:
- No newly-processed meetings this fire (nothing to cross-reference against).
- Open-commitment count is zero (helper returns `[]`).

Otherwise, for EACH newly-processed meeting transcript, execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_transcript_to_commitments,
    build_commitment_updated_event,
    build_pending_review_event,
    open_review_proposal_ids,
    filter_duplicate_review_targets,
)
from commitment_state import close_commitment, CommitmentIdError, PendingReviewError
from atomic_write import atomic_append_jsonl

workspace_root = '<absolute path to the workspace root>'
events_path = '<absolute path to _hq/data/events.jsonl>'
fire_start = '<UTC ISO recorded BEFORE Phase 4 appended anything>'
# ONE set for the whole fire — seeded from disk, mutated per transcript.
already_proposed = open_review_proposal_ids(events_path)
# ONE diagnostics dict for the whole fire — EVORDER layer 3 counts into it.
cru_diag = {}
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

# Stage B (F2): auto-resolves close through commitment_state.close_commitment
# — THE closure path. Matching (Path 3) is unchanged; only the write moved.
#
# §6 dedup guard: ONE open review proposal per commitment. The filter MUTATES
# already_proposed, so the same set carried to the next transcript in this
# fire suppresses the second ask for a commitment this transcript claimed.
review_ok = {r['commitment_id'] for r in filter_duplicate_review_targets(
    [r for r in results if r['recommendation'] in ('pending_review', 'supersede')],
    already_proposed=already_proposed)}
n_resolved = 0
next_seq = <peek-next-seq>  # for updated/pending events only
to_append = []
for r in results:
    rec = r['recommendation']
    evidence = f\"Past meeting transcript ({r.get('has_completion_signal') and 'completion language' or r.get('has_schedule_shift_signal') and 'schedule-shift language' or r.get('has_new_ask_signal') and 'new-ask language' or 'title match'})\"
    # v2.14.7+: full coverage. HIGH-confidence → auto-resolve (or
    # commitment_updated when schedule-shift signal). MEDIUM and supersede
    # → pending_review for the confirm queue to surface as one-click
    # confirm/skip.
    if rec == 'auto_resolve':
        try:
            res = close_commitment(
                workspace_root, r['commitment_id'],
                resolved_by=r['owner_id'],
                evidence=evidence,
                source_skill='past-meetings',
            )
            if res['status'] == 'closed':
                n_resolved += 1
        except (CommitmentIdError, PendingReviewError) as e:
            print(f'CRU skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
    elif rec == 'commitment_updated':
        to_append.append(build_commitment_updated_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='past-meetings',
            change_summary='Schedule shifted in transcript',
            evidence=evidence,
            next_seq=next_seq,
        ))
        next_seq += 1
    elif rec == 'pending_review' and r['commitment_id'] in review_ok:
        to_append.append(build_pending_review_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='past-meetings',
            proposed_resolution='auto_resolve',
            score=r['score'],
            evidence=evidence,
            next_seq=next_seq,
            # WATCHGATE — the matcher's OWN fulfillment finding, carried
            # rather than discarded. The accept surface screens on it; without
            # it the only thing separating a bare guess from a bulk confirm is
            # whether the evidence prose happens to say 'title match'.
            has_completion_signal=r.get('has_completion_signal'),
            # WATCHGATE 2.5 — the meeting's own start, the SAME value passed
            # as transcript_ts above. Lets the accept surface refuse, at apply
            # time, evidence that predates the promise.
            evidence_ts='<THIS meeting start ts — the same value as transcript_ts>',
        ))
        next_seq += 1
    elif rec == 'supersede' and r['commitment_id'] in review_ok:
        to_append.append(build_pending_review_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='past-meetings',
            proposed_resolution='supersede',
            score=r['score'],
            evidence=evidence,
            next_seq=next_seq,
            has_completion_signal=r.get('has_completion_signal'),
            evidence_ts='<THIS meeting start ts — the same value as transcript_ts>',
        ))
        next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU past-meetings: resolved={n_resolved} updated={sum(1 for e in to_append if e[\"type\"]==\"commitment_updated\")} pending={sum(1 for e in to_append if e[\"type\"]==\"commitment_review_proposed\")} stale_evidence_skipped={cru_diag.get(\"stale_evidence_dropped\", 0)}')
"
```

**Carry `stale_evidence_skipped` to Phase 5.** The last number on that stdout line is EVORDER layer 3's refusal count for the whole fire; put it on the fire receipt as `extra_data={"n_stale_evidence_skipped": <that number>, ...}` (the spelling both mail rails use — `reconcile_sent` / `reconcile_inbound` put it in `signal_fields`, and an improvised synonym here is invisible to anyone reading across the three rails). Zero is a legitimate value and is written, not omitted: an absent key reads as "this rail has no fence", which is the state this build ended.

**The stdout is for diagnostic logging only.** Per CONTRACT.md Rule 4 forbidden-pattern list: `commitment_resolved`, `commitment_updated`, and `commitment_review_proposed` event-type names never appear in chat. The user sees the resolution effect on the next Commitments fire — items disappear from the OWED TO YOU / YOU OWE columns when they're auto-resolved here.

**Threshold tuning:** the helper uses `HIGH_CONFIDENCE_THRESHOLD = 0.55` and `PENDING_REVIEW_THRESHOLD = 0.30`. These are deliberately conservative for v2.14.6 launch. Once telemetry shows real auto-resolve rates and false-positive rates from the confirm queue's pending-review confirmations, tighten or loosen.

**Failure handling:** if the CRU pass errors (events.jsonl read failure, helper import fails, transcript empty), swallow silently and continue. Phase 4.6 is best-effort enrichment; the Phase 4 commitment writes already succeeded. **Append a `pack_run.data.errors[]` entry** (v3.5.0+) so the failure is auditable via `usage report` even though the user doesn't see it: `{"phase": "4.6_commitment_cru", "reason": "<short>", "detail": "<truncated stderr or exception message>", "meeting_id": "<id>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 4.6.b — Decision CRU pass: auto-resolve / supersede open decisions (v3.4.5+)

Per `shared/scripts/decision_match.py`. Sister to Phase 4.6 but scoped to decisions. After commitment-CRU completes, scan each newly-processed transcript against pre-existing open decisions. The premise: many decisions get executed or reversed in conversation — auto-detecting closes the historical log without the user manually marking decisions resolved/superseded.

**Conservative — HIGH-confidence auto-close only.** Threshold is tighter than commitments (0.65 vs 0.55) because decision false-positives lose real history. No `pending_review` queue yet — borderline matches simply don't act. If telemetry shows the threshold misses too many real closures, we'll add a Pulse review surface.

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
    build_decision_superseded_event,
)
from atomic_write import atomic_append_jsonl

events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_decisions(events_path)

next_seq = <peek-next-seq>
to_append = []
for transcript in <list of newly-processed transcripts>:
    results = match_transcript_to_decisions(
        open_decisions=opens,
        attendee_person_ids=transcript['attendee_person_ids'],
        transcript_text=transcript['text'],
    )
    for r in results:
        rec = r['recommendation']
        evidence = f\"Past meeting transcript ({r.get('has_reversal_signal') and 'reversal language' or r.get('has_completion_signal') and 'completion language' or 'title match'})\"
        if rec == 'decision_resolved':
            to_append.append(build_decision_resolved_event(
                decision_id=r['decision_id'],
                primary_thread_id=r['primary_thread_id'],
                source_skill='past-meetings',
                evidence=evidence,
                next_seq=next_seq,
            ))
            next_seq += 1
        elif rec == 'decision_superseded':
            to_append.append(build_decision_superseded_event(
                decision_id=r['decision_id'],
                primary_thread_id=r['primary_thread_id'],
                source_skill='past-meetings',
                evidence=evidence,
                next_seq=next_seq,
            ))
            next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU decisions: resolved={sum(1 for e in to_append if e[\"type\"]==\"decision_resolved\")} superseded={sum(1 for e in to_append if e[\"type\"]==\"decision_superseded\")}')
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

# Phase 5 — Memory updates (silent per Rule 9)

Append to events.jsonl:
- `connector_read` for Granola fetch
- All extracted events (decisions, commitments, follow_ups) — high-conf flagged committed, low-conf flagged pending_review
- `meeting_processed` per meeting
- CRU resolution events (Phase 4.6) — already appended in Phase 4.6 itself; mentioned here for completeness of the audit trail
- The fire receipt — **ONE call to the canonical receipt helper (`shared/scripts/receipts.py`, v4.5.2 R1); NEVER hand-roll the receipt JSON** (the hand-rolled `past_meetings`/`cr-past-meetings`/`lateness_tier` drift of FINDINGS F-49/F-50 P2c came from this file's old prose): `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "past-meetings", fired_via=<the Phase 2.9 receipt_fired_via: manual|scheduled|catchup>, surfaced=n_meetings, duration_ms=elapsed_ms, late_tier=<the lateness tier when note/degrade, else None>, extra_data={"errors": [], "window_start": "<the Phase 3 window start>", "window_end": "<the Phase 3 window end>", "window_incomplete_before": <ISO of the oldest still-unprocessed meeting, per the Phase 3 batch-cap gate — OMIT the key entirely when the fire drained its window>, "n_stale_evidence_skipped": <the Phase 4.6 `cru_diag` total — EVORDER layer 3's refusals across every transcript this fire; write 0 rather than omitting it>, "telemetry": build_pack_run_telemetry(...)})` — `receipt_fired_via` is what Phase 2.9's helper returned, never guessed; the field name is `window_incomplete_before` and nothing else (`shared/scripts/catchup.py` `WINDOW_INCOMPLETE_FIELD` is the one spelling — an improvised synonym is invisible to the reader and re-opens the orphaning bug, the F-50 P2c class); telemetry silent per Rule 9

Append to staging_emissions.jsonl per .docx generated. Telemetry writes silently — no chat narration.

# Phase 5.9 — Surface-preference filter (Phase 6 Loop 2 — before rendering)

Drop any surfaced item the CEO has taught the system to stop showing (insight-generator Pass 14 → `_hq/data/surface-preferences.json`): `from surface_preferences import load_surface_preferences, is_suppressed`; keep an item only if `not is_suppressed(prefs, "past-meetings", item_class=<the item's class, e.g. "decision_needed"|"open_item">, entity_id=<meeting or person id>)`. Missing store → no-op. Hides the prompt only; the processed meeting + its captured substrate are untouched. Same filter every widget orchestrator applies.

# Phase 6 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

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

**Empty-state rule (v2.14.19+):** if zero meetings happened in the Phase 3 window (or all of them resolved cleanly with no pending sub-items), DO NOT improvise a "no meetings to process" widget by hand-typing HTML. Build `data_view = {"widget_mode": "all_clear_summary", "header": "Past Meetings — nothing to process", "sub_header": "<weekday>, <date> · <time> check", "counters": [{"label": <the window label: "Last 24h" on a normal fire, "Since <weekday>" when Phase 3 returned `extended: true`>, "value": n_meetings}, {"label": "Auto-processed", "value": n_auto}, {"label": "Pending review", "value": 0}, {"label": "Skipped", "value": n_skipped}], "summary_line": "All transcripts were either auto-processed cleanly or skipped (internal/personal). Nothing pending your call.", "tracked_items": [], "footer": None}` and pass to `render_chat_output_widget()`. NEVER hand-build the empty-state widget. The counter label states the window that was actually searched — a widened catch-up window labelled "Last 24h" is a false claim about what was looked at. See `orchestrator-commitments.md` for the full diagnosis (v2.14.18 fresh-install bug).

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

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "past-meetings",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    # Window phrase comes from the Phase 3 result, never a hardcoded "last 24h":
    # "last 24h" on a normal fire, "since <weekday>" when `extended` is true.
    "header": f"Past meetings · {window_phrase} · {n_processed} newly processed, {n_reprocessed} re-processed, {n_skipped} skipped",
    "sections": [{"title": None, "count": None, "items": [item_for_meeting(m) for m in meetings]}],
    "quick_read": quick_read,
    "save_confirmation": skipped_summary,    # e.g. "Skipped: Office painting plan, Self-call 'Command room update'"
}

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="past-meetings")
# EW2+T (F-15): the transport runs the full validator chain (canonical
# actions, data shape, leak scan, wrapper contract) and persists the sealed
# render. Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) — never
# a hand-composed variant, never a post-processed one.
```

The widget renders inline with per-item buttons; user clicks accumulate locally; "Apply all" fires `apply choices: [...]` payload that `apply-choices` skill catches and dispatches through the reply handlers below. Do not compose chat strings or paraphrase — the widget HTML IS the post.

**Step 3 — Post the chat-links section (v2.14.0+ — split Briefs vs Sources):**

After posting the widget, emit a second chat turn with TWO separate markdown sections. Per M's v2.13.2 ask: *"the brief hyperlink should just have the name of the meeting. And what's underlined ('Sam UX review continuation') is sending you to granola, which should be sources, not links."*

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
- Each item numbered to match the widget.
- Anchor text = meeting name (resolved attendee + topic). NOT the generic word "brief."
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
# v5.9.2 — get_brief_opener_url is Drive-aware: on a cloud-mounted workspace
# (session-scoped path) it returns the file's Drive web link when one was
# resolved (see the BRIEF_SESSION_SCOPED step above); on a host-native
# workspace it returns the same computer:// form as before.
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
