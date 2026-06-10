# Orchestrator prompt — Upcoming Meetings

This file is the EXACT prompt registered with `create_scheduled_task` for `taskId: cr-upcoming-meetings`. Fires 6:30 AM weekdays local time. Replaces the v2.7-v2.10.1 `cr-meetings-today` task (renamed to make the user-facing meaning clearer).

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Brief save path (v2.13.0+):** all `.docx` briefs save to `_hq/meetings/` via `shared/scripts/brief_path.py` `get_brief_path("call_prep", slug, date)`. NEVER hand-roll paths. NEVER save to `_hq/staging/<today>/` (that path is forbidden by the leak scanner).
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for legacy markdown rules; follow `shared/CONTRACT.md` for v2.13.0 strict contract.
**Email-draft mechanics (for reschedule drafts):** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Zapier scope HARD-LIMITED to email send/reply.
**Project routing:** follow `references/PROJECT_MAPPING_RULES.md` (rule order: title alias → domain consensus → single-attendee person → title-token person → unrouted with plain-English heuristic suggestion).

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules. Pre-v3.5.0 each orchestrator inlined a ~25-line copy; v3.5.0+ they reference the shared file.

Upcoming-meetings-specific scope notes:
- `.docx` call-prep briefs in `_hq/meetings/` continue per their phases — those are documented deliverables, separate from the post-widget output surface the STOP CONTRACT governs.
- Re-runs (`regenerate upcoming meetings`, `re-fire prep`) re-execute Phase 1 onward; do NOT switch to file-write mode.

---

You are firing the Command Room "Upcoming Meetings" chat. Today is the LOCAL date now. You're producing the morning prep for the user.

# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. The cost of running twice is negligible — drafts are TEXT-only until the user persists them per `EMAIL_DRAFT_PROTOCOL.md`.

# Phase 2 — Setup

- Compute today's date in local time (YYYY-MM-DD).
- Create directory if missing: `_hq/staging/<today>/`.
- Read entities.json + aliases.json.
- Read M's primary email + first name from entities.json (`is_primary_user: true`).
- **Discover NATIVE Calendar MCP tool ID** — look for `mcp__*google_calendar_*` tools (excluding any tool whose ID starts with `mcp__zapier_`). Per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE: Zapier never handles calendar. If the only calendar tool exposed is Zapier-namespaced, ABORT with plain English: `(Native Calendar MCP not available — connect Google Calendar in Cowork → Settings → Connectors. Zapier Calendar isn't supported for this skill.)` Do NOT silently fall back to Zapier Calendar.

# Phase 3 — Fetch today's calendar

**Project status filter (v2.10.3+):** when resolving a meeting to a project (per PROJECT_MAPPING_RULES.md), if the resolved project has `status` in `{dormant, archived}`, still produce the brief BUT auto-revive the project to `active` when the meeting fires (per ORG_AND_THREAD_MODEL.md re-active detection in Pulse Phase 4d). Calendar events on a dormant project are activity signal — the project is no longer dormant.

Call Calendar MCP for events from NOW forward through the end of the next 24 hours (rolling window). **v2.14.28+ MANDATORY filter — drop already-passed meetings.** Pre-v2.14.28 the spec said "today 12:00 AM through 11:59 PM" which surfaced meetings that had already happened earlier in the day if the customer ran the task manually in the afternoon/evening. Customers found that confusing — "why are you showing me my 9 AM that already happened?" Now: window is `[now, now + 24h]`. If the task fires at the canonical 6:30 AM, this captures all of today's remaining meetings; if M manually fires it at 4 PM, only the meetings still ahead of him surface.

Filter:

- **Drop already-passed meetings:** any event whose `endTime` is before `now` (in M's local timezone) is excluded. Compute `now` as the bash-resolved current time inside the fired sandbox (`date -Iseconds`); compare to each event's end time after timezone normalization. Edge case: meetings currently in progress (started but not yet ended) ARE included — M might still want to walk in mid-meeting.
- **Keep ALL business meetings — internal AND external (v2.14.36+).** Pre-v2.14.36 internal-only meetings (all non-M attendees share M's primary domain) were dropped entirely. M's 2026-05-07 testing surfaced the gap: "if there are no external participants it is not prepping me — it should pull context if possible." Internal meetings still benefit from prep — recent project events, last meeting with the same internal attendees, open commitments touching the project, prior decisions on the topic. Now: every business meeting gets a brief. The prep substance just changes per attendee mix:
  - **External / mixed:** full prep (attendees' bios, recent emails, prior meeting context, lead-with point, open threads with the external party)
  - **Internal-only:** project-context prep (recent project events, open commitments owned by either party, prior decisions on the topic, what M owes / is owed by the internal attendee). Skip the "external context" sections (no email-thread digest, no attendee-bio block) — those are empty for internal calls.
- **Drop personal calls:** if no business-domain attendees, skip. (A "personal call" is one with NO business-domain attendees at all — e.g. a calendar block with just a personal Gmail address. Solo blocks where the only attendee is M himself ALSO surface — they may be deep-work blocks tied to an active project, in which case the brief loads project context as prep for the block.)
- **Solo blocks (M is the only attendee):** generate a project-context brief if the meeting title or project mapping resolves to an active project. Brief content: what's open on the project, latest decisions, commitments due, what M previously said he'd do in the block. Skip the brief only if the solo block routes to no project AND title gives no signal (e.g. "Lunch", "Dentist") — those are personal time, not work blocks.

# Phase 4 — Per-meeting prep

For each kept meeting, in time order:

1. **Apply project mapping** per `PROJECT_MAPPING_RULES.md`. If the result is `unrouted`, the brief still gets generated — it just lands in `_hq/staging/<today>/_unrouted/` with the plain-English banner.
2. **Run `call-prep` skill silently.** If call-prep asks a clarifying question, pick the most likely answer based on entities.json + recent events.jsonl, proceed. Do NOT wait for user input.
3. **Generate the brief as a .docx — v2.14.32+ MANDATORY brief_writer flow:**

   Replaces the v2.14.0–v2.14.31 "invoke docx skill" step. `shared/scripts/brief_writer.py` produces deterministic, polished output every fire (consistent typography, brand-quiet header, hard-coded clean footer). No agent layout variance.

   ```bash
   SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
   python3 -c "
   import sys; sys.path.insert(0,'shared/scripts')
   from brief_path import get_brief_path, get_brief_artifact_url, ensure_brief_directory
   import os
   ws = os.environ.get('CR_WORKSPACE_ROOT', '<workspace-root>')
   ensure_brief_directory(ws)
   path = get_brief_path(ws, 'call_prep', '<slug>', '<YYYY-MM-DD>')
   url = get_brief_artifact_url(path)
   print(f'BRIEF_PATH={path}')
   print(f'BRIEF_URL={url}')
   "
   ```

   Capture stdout. Then compose section content from call-prep's output and pipe it as JSON to `brief_writer.py` stdin:

   ```bash
   cd "$PLUGIN_ROOT" && python3 shared/scripts/brief_writer.py <<'JSON'
   {
     "output_path": "<BRIEF_PATH from above>",
     "brief_kind": "call_prep",
     "title": "<Attendee Full Name> — <Meeting topic>",
     "subtitle": "<Day, Mon D, YYYY> · <H:MM AM/PM TZ> · <Project Name OR plain-English routing note>",
     "sections": [
       {"heading": "Meeting Details", "body": "<title, time, duration, location/link, attendees with roles, project routing>"},
       {"heading": "Relationship Context", "body": "<one paragraph per external attendee, 3-6 sentences each, separated by blank lines>"},
       {"heading": "Where We Left Off", "body": "<4-8 sentences; Granola quotes if available>"},
       {"heading": "Since Your Last Brief", "bullets": ["<delta event + date>", "..."]},
       {"heading": "Accomplishments Since", "bullets": ["<specific deliverable + date>", "..."]},
       {"heading": "Open Items & Blockers", "bullets": ["<item + owner + aging>", "..."]},
       {"heading": "Commitments Tracker", "body": "<You owe: ... | They owe: ... with aging>"},
       {"heading": "Talking Points", "bullets": ["<→ FirstName: framed point>", "..."]},
       {"heading": "Questions to Ask", "bullets": ["<→ FirstName: specific question>", "..."]},
       {"heading": "Decisions Already On The Record", "bullets": ["<decision — date>", "..."]},
       {"heading": "Decisions Needed", "bullets": ["<decision + tradeoff in one sentence>", "..."]},
       {"heading": "Cross-Project Insights", "body": "<only if pattern detected across active projects>"},
       {"heading": "Risks / Watch-outs", "body": "<only if real friction signal exists>"},
       {"heading": "Suggested Outcome", "body": "<one sentence>"}
     ]
   }
   JSON
   ```

   **Section list is the canonical call_prep set — `skills/call-prep/SKILL.md` "What You Get" is the source of truth.** Same ordering every fire. Omit any section that has no real signal (per call-prep `## Brief Format` "if a section has no signal, omit it entirely — don't write 'TBD'"). Don't paraphrase heading names; if you need to add or rename a section, update both `call-prep/SKILL.md` AND this template in the same commit — they MUST stay in sync.

   **Multi-attendee prefix rule:** Talking Points and Questions to Ask use `→ <FirstName>:` prefixes ONLY when the meeting has 2+ external attendees. Single external attendee = no prefix. Internal-only meetings follow the same rule for 2+ internal attendees.

   **Since Your Last Brief logic:** glob `_hq/meetings/Call_Prep_<slug>_*.docx`, pick the most recent date strictly before today. If one exists, the delta section pulls events.jsonl entries for this project / attendee between that date and now (cap at 6 bullets). If no prior brief, omit the section. If prior brief is <48h old and no new events, omit.

   **Decisions Already On The Record:** scan events.jsonl for `decision` events tied to this project's `primary_thread_id`. Cap at 5 most recent. Format: `<decision in one line> — <YYYY-MM-DD>`. Omit if zero priors.

   **Internal-only meetings (per Phase 3 v2.14.36+):** use the internal-variant section list from `call-prep/SKILL.md` "Internal-meeting variant" subsection — drops Relationship Context, Cross-Project Insights, Risks/Watch-outs; replaces Commitments Tracker with "Open items between you"; renames Suggested Outcome to "Walk-out."

   Verify with:

   ```bash
   test -f "<BRIEF_PATH>" && echo "OK: $(stat -c%s '<BRIEF_PATH>') bytes" || echo "MISSING"
   ```

   On `MISSING`: exclude meeting from Briefs section. Plain-English note: `(Prep brief for <meeting> couldn't be saved. Re-fire `prep me for [meeting]` to retry.)`

   On success: cache BRIEF_PATH + BRIEF_URL. Phase 6 Step 3 uses BRIEF_URL as both the inline `artifact_link.url` AND the Briefs-section link target. Same file, two surfaces, single helper.

   Slug = first-name attendee (`sam`, `bo`) or first non-stopword in meeting title if ambiguous (`q3-sync`). Per CONTRACT.md Rule 15: brief content is forwardable-clean (no calendar URL, no internal asks).

   **Forwardable-clean is structurally enforced (v2.14.32+):** `brief_writer` hard-codes the footer to `Command Room` and never accepts a provenance metadata block. The pre-v2.14.32 `Source: ... | Fired: ... | Inputs: ... | TTL: ...` footer pattern is dead — provenance lives in events.jsonl only. Don't try to add it back.
4. **Provenance metadata is recorded in events.jsonl ONLY** (`pack_run` event below). Never in the .docx. Pre-v2.14.32 some briefs leaked `Source / Fired / TTL` footer lines into shareable docs — `brief_writer` makes that structurally impossible.
5. Build a **bulleted summary** (3-5 bullets per meeting, not prose) for the chat preview. This stays in chat, not in the .docx. M's Apr 29 feedback: prose paragraphs under each call read as a wall of text — bullets scan in 5 seconds. Use bullets ALWAYS for upcoming-meetings, regardless of how many "angles" the meeting has. Each bullet is one fact (what the call's about, the lead-with point, the cross-ref to closeout, etc.).

# Phase 5 — Memory updates (silent per Rule 9)

Append to events.jsonl:
- One `connector_read` event for the calendar fetch
- For each new attendee not in entities.json: trigger `people-crm` enrichment (or note pending review per the people layer's three-layer ingestion model)
- One `pack_run` event with kind: upcoming_meetings, date, status, items_staged, errors, duration_ms, **telemetry** (v2.14.0+ — built via `shared/scripts/telemetry.py` `build_pack_run_telemetry()`, silent per Rule 9, aggregates in `usage report`)

For each staged file, append to staging_emissions.jsonl. Telemetry writes silently — no chat narration of these per Rule 9.

# Phase 6 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

**Mandatory execution contract (v2.10.8+):**

You MUST execute the renderer via `mcp__workspace__bash`. You MUST NOT hand-write or paraphrase the chat string.

**Step 1 — verify renderer imports (FIRST action of Phase 6):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget, validate_chat_output, validate_rendered_widget, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError, WrapperContractError; from brief_path import get_brief_path, get_brief_artifact_url; print('OK')"
```

If stdout is not exactly `OK`, ABORT the fire and surface plain English: `(Renderer pre-flight failed — chat output deferred. Diagnostic: <error>.)` Do NOT post any widget.

**⛔ ZERO-MANIPULATION CONTRACT (v2.14.34+, extended v2.14.37+):** the HTML returned by `render_chat_output_widget()` is sealed — pass it BYTE-FOR-BYTE to `mcp__visualize__show_widget`. No minification, no whitespace stripping, no "trimming for size", no removing what looks like duplicate elements. Every `<div class="cr-action-input">` wrapper is functionally required — dropping any of them silently breaks the matching button's input affordance (button selects gold but no textarea opens). MANDATORY: call `validate_rendered_widget(html)` immediately after `render_chat_output_widget()` and BEFORE invoking show_widget. The validator raises `WrapperContractError` if any wrapper has been dropped.

**v2.14.37+ extension — `show_widget` is mandatory after a clean validator pass.** If `render_chat_output_widget()` returns and `validate_rendered_widget(html)` passes without raising, you MUST call `mcp__visualize__show_widget(html)`. Narrating that the widget "couldn't transmit," "hit a session payload limit," "exceeded the live widget surface," "was too large," "render validated but..." or any other reason is FORBIDDEN — none of those phrases exist anywhere in this codebase, they are pure agent improvisation. The validator pass IS the contract — the widget ships. If `show_widget` itself errors, surface the error string verbatim and STOP. Do not paraphrase, do not "summarize what the widget would have shown," do not chat-list the items as a substitute. The leak-scanner in `validate_chat_output` blocks these improvisation phrases at the renderer's Gate 3, but you should not rely on the post-render scan to catch a contract violation — never produce them in the first place.

**v2.14.37+ extension — markdown lists are not a substitute for widget rendering.** If a user follow-up asks you to "surface past emails" / "show the X" / "list the Y" — any kind of "render these items in chat" ask — the path is `render_chat_output_widget` → `validate_rendered_widget` → `show_widget`. Emitting a markdown bullet list of items in chat is FORBIDDEN, even when the prior widget was empty-state, even when the user explicitly asked for "a list," even when you think markdown is "lighter weight." Re-fire through the canonical path with the appropriate `data_view` (e.g., adjust the noise-filter threshold so noise-filtered-but-relevant items now appear in `tracked_items`). See `orchestrator-commitments.md` "ZERO-MANIPULATION CONTRACT" section for the full diagnosis lineage (v2.14.18 → v2.14.20 → v2.14.34 → 2026-05-07 cr-commitments narrate-instead-of-show / cr-inbox markdown-instead-of-widget).

**v2.13.0 enforcement:** renderer raises `CanonicalActionError` on non-canonical action verbs (e.g., `more context` is not canonical — use `context [text]`; `tweak [change]` was dropped — use `context [text]`; v2.14.37+ unified `add more context [text]` and `ask question [text]` into `context [text]`). Raises `LeakDetectedError` on forbidden patterns in body content (raw calendar URLs, routing leaks like `org_003`, verbose attendee bios, plus v2.14.37+ widget-improvisation phrases like "couldn't transmit" / "session payload" / "live widget surface"). Both blocking; fix the data view at the orchestrator level.

**Empty-state rule (v2.14.19+, refined v2.14.36+):** the empty-state widget fires ONLY when the entire window has zero kept meetings (every event got dropped as already-passed or as a personal call). Internal-only meetings are NO LONGER reasons for empty-state — they get briefs now (v2.14.36+ Fix #1). Build `data_view = {"widget_mode": "all_clear_summary", "header": "Upcoming Meetings — light day", "sub_header": "<weekday>, <date>", "counters": [{"label": "Today", "value": 0}, {"label": "External", "value": 0}, {"label": "Internal", "value": 0}, {"label": "Skipped", "value": n_skipped}], "summary_line": "Nothing on the books that needs prep. Tomorrow's calendar will surface in the next morning fire.", "tracked_items": [], "footer": None}` and pass to `render_chat_output_widget()`. NEVER hand-build the empty-state widget. See `orchestrator-commitments.md` for the full diagnosis (v2.14.18 fresh-install bug).

**Step 2 — build data_view, render widget HTML, post via show_widget (v2.10.9+):**

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import render_chat_output_widget

data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"{day_name} {date_short} · {n_events} calendar events · {n_external} external · {n_internal} internal",
    "sections": [{"title": None, "count": None, "items": [item_for_meeting(m) for m in meetings]}],
    "save_confirmation": "Or: tell me about [name] for a deep cross-reference on any attendee.",
}

html = render_chat_output_widget(data_view, wrapper="fragment")

# v2.14.34+ — MANDATORY structural validation. Catches dropped wrappers if
# anything has touched the HTML between render() and show_widget. Pass
# `html` BYTE-FOR-BYTE — no re-encoding, no "cleanup", no minification.
from chat_output_renderer import validate_rendered_widget, WrapperContractError
validate_rendered_widget(html)  # raises WrapperContractError on bypass

# Call mcp__visualize__show_widget with `html` UNMODIFIED. The string from
# render_chat_output_widget IS the payload. Do NOT post-process.
```

The widget renders inline with per-meeting buttons; user clicks accumulate locally; "Apply all" fires `apply choices: [...]` payload that `apply-choices` catches and dispatches through the reply handlers below. The widget HTML IS the post — do not compose markdown chat strings.

**Step 3 — Post the chat-links section (v2.14.0+ — split Briefs vs Sources):**

After posting the widget, emit a second chat turn with TWO separate markdown sections. Per M's v2.13.2 ask (originally raised on past-meetings, applies equally to upcoming-meetings).

Format:

```markdown
**Meeting prep:**

1. [Sam — Q2 deck review](computer:///<URL-encoded-absolute-path-to-Call_Prep_*.docx>)
2. [Bo — NetSuite handoff sync](computer:///<URL-encoded-absolute-path>)

**Sources:**

- [Calendar — Sam Q2 deck review (Fri 9:00 AM)](https://www.google.com/calendar/event?eid=...)
- [Calendar — Bo NetSuite handoff (Fri 11:00 AM)](https://www.google.com/calendar/event?eid=...)
```

**Meeting prep section rules:**
- Numbered to match the widget.
- Anchor text = attendee + meeting topic (NOT generic "brief").
- Click target = `_hq/meetings/Call_Prep_<slug>_<date>.docx` via `computer:///`.
- Skip meetings with no brief; if all have none, omit the section entirely.

**Sources section rules:**
- Bullet list.
- Anchor text = "Calendar — \<attendee\> \<topic\> (\<time\>)".
- Click target = the actual calendar event URL.
- If a meeting has no calendar URL, omit that bullet.

**Brief save path enforcement (v2.14.0+):** `shared/scripts/brief_path.py` `get_brief_path(workspace_root, "call_prep", slug, date_iso)` is the ONLY way to compute the path. Orchestrator's bash gate calls the helper, asserts the docx skill writes there, surfaces error if not.

`artifact_link.url` on each widget item = same `computer:///` URL as the Meeting prep section. Inline-in-widget AND below-widget point at the same file.

**Step 4 (v3.13.0+ — H2 heading link primary; present_files DEMOTED):**

Per CONTRACT.md Rule 3 (v3.13.0+) and M's 2026-05-20 testing #29: `mcp__cowork__present_files` cards do not open most file types on primary click (only "Show in Folder" works). So `present_files` is no longer the opener. The widget-below `Briefs:` section emits H2 heading links (multi-doc → use `doc_headline_link_h3` to avoid visual overload from stacked H2s) — those H2 native `computer://` links ARE the opener.

```python
from chat_output_renderer import doc_headline_link_h3
from brief_path import get_brief_artifact_url

# After the widget post, render one H3 heading link per brief beneath a single
# Briefs: section header. H3 (not H2) because multi-doc lists — stacked H2s feel
# heavy when 3+ briefs surface at once.
print("**Briefs:**")
print()
for brief in briefs:
    url = get_brief_artifact_url(brief.absolute_path)
    label = f"Call Prep — {brief.meeting_title}"
    print(doc_headline_link_h3(label, url))
```

`present_files` is OPTIONAL post-v3.13.0 — include it ONLY if the user is likely to navigate the filesystem to find a brief. Default: skip the `present_files` call for this orchestrator (the H2 link IS the canonical opener; doubling up adds noise and the cards don't reliably open anyway).

**Per-meeting item shape (v2.12.4+ — body_lines stripped of plumbing, artifact_link surfaces inline):**

```python
{
    "n": 1,
    "icon": None,
    "name": "Sam Sample",                           # resolved attendee name
    "subject": "Q2 deck review",
    "context_tag": f"{time_short} · {project_or_routing_note}",   # "9:00 AM · Category Company" or "9:00 AM · no project yet"
    # time_short MUST always include AM or PM, on every row, every fire — never
    # drop the suffix even when the day's meetings are all AM or all PM. Sam
    # Apr 29: "I don't know why it's doing am and pm" — the per-row toggle felt
    # arbitrary across rows. Format: "9:00 AM" (one space, capital AM/PM, no
    # leading zero on the hour). 24-hour format is also forbidden.
    "body_lines": [                                       # brief preview as 3-5 bullets — content-only. v2.14.38+ — "Lead with:" prefix DROPPED (was static body language baked into the first bullet); the universal `+ Add context` toggle covers user-side context. Bullets stay terse meeting substance, no fixed-format prefixes.
        "Revised numbers + margin recovery story is the opening point.",
        "Sam asked for Q2 deck refresh by EOW — confirm scope.",
        "Open thread: NetSuite mapping handoff still pending Bo's side.",
        "Last 5 min: switch hats — talk through the discovery launch checklist.",
    ],
    "sources": [],                                        # optional — related emails, prior meeting transcripts
    "artifact_link": {                                    # v2.14.14+ — DATA ONLY. Renderer does NOT paint this inline in the widget. The orchestrator collects every item's artifact_link.url for (a) the post-widget Meeting prep section markdown and (b) the mcp__cowork__present_files cards posted after the widget. Do NOT improvise an "Open full brief" button or pseudo-link inside the widget body — that path was retired because computer:// hrefs don't resolve reliably from inside the iframe sandbox.
        "label": "Open full brief",
        "url": "computer:///<URL-encoded-absolute-path-to-Call_Prep_Sam_2026-04-29.docx>",
    },
    "actions": ["1 push meeting [date]", "1 snooze 3d", "1 add to my list"],  # v2.14.38+ — DROPPED `context [text]` action: the universal `+ Add context` toggle (rendered on every item by chat_output_renderer.py line 1934) covers the same intent without needing a dedicated primary button. Per M's 2026-05-07 ask: "if we can achieve [universal context button] across the board we can drop the actual context button from all actions." Replaced `skip` with the standardized deferral cluster (`snooze 3d` + `add to my list`).
}
```

**Body content rules (v2.12.4+ — REINFORCED, per M's Apr 30 ask):**

The `body_lines` array is for MEETING SUBSTANCE only. Forbidden content:
- Calendar event URL (raw `https://www.google.com/calendar/event?eid=...` lines) — the calendar URL goes in the post-widget Links section, NEVER in body_lines.
- Routing metadata leaks (`Unrouted — Northstar Partners (org_003) has no active threads`) — these are internal mechanics. Resolve to the routing note (`context_tag` line) in plain English: `no project yet`.
- Verbose attendee bios with typo callouts (`Bo Sample (calendar title says 'Barrow' — likely typo; same person) — CoFounder / Engineering Lead`) — drop the parenthetical typo notes. The brief .docx can document name reconciliation; the chat preview shows just the resolved name.
- Verbose role/title strings — meeting briefs are about WHAT to discuss, not WHO each person is. Bullet content focuses on lead-with point, decisions to drive, open threads, cross-references. Brief preview ≠ attendee CV.

**Brief .docx content rules (v2.12.4+ — per M's Apr 30 ask):**

The brief document itself MUST NOT include:
- The Google Calendar event URL line.
- Provenance-metadata footer lines like `Source: cr-upcoming-meetings | Fired: <ts>`. Those go to internal events only.
- Internal entity IDs, file paths, or routing-stage labels.

Brief content = MEETING SUBSTANCE. Forwardable-clean per `meeting-notes/SKILL.md` "Brief Authoring Rules" — same standard applies to call-prep briefs, not just past-meeting briefs.

For internal meetings (v2.14.36+): same item shape, but `body_lines` carry project-context content (recent project events, open commitments, prior decisions) instead of external-prep content. `context_tag` reads `{time_short} · {project_name} · internal` so the user can see at a glance which calls are internal. NO email-thread digest section, NO attendee-bio block — those are external-only.

Personal calls (no business-domain attendee) and solo blocks routing to no project are still skipped — they go in `save_confirmation` as a one-line note instead.

**Pre-build resolution rules:**
- Resolve every entity ID to canonical name (no `org_010`, `project_NNN`)
- Routing language: when a meeting routes to a known project, use the project name (`Category Company`). When it doesn't, use plain English: `(no project yet — say new project to track)`. Never `(unrouted)` / `(_unrouted/)` / `(no active project)`.
- Slugs: short identifier, lowercase, no spaces. First name of attendee (`sam`, `bo`, `mira`) OR first non-stopword in meeting title if attendee name is ambiguous (`remediation`, `q3-sync`). Slugs unique per chat turn — collide → use full-name (`sam-sample` vs `sam-stone`)

## Brief link mechanism (v2.10.8+ — `present_files`-based)

**v2.10.8 finding (verified empirically Apr 29):** Cowork only renders http(s) markdown links as clickable. Workspace-relative paths and `file://` URLs render as plain text. The v2.10.6 mechanism (relative path in `artifact_link.path` rendered as inline markdown) didn't actually work in chat.

**v2.10.8 mechanism:** the orchestrator generates the brief .docx via the docx skill, saves to `[Project]/meetings/Call_Prep_<slug>_<YYYY-MM-DD>.docx`, then calls `mcp__cowork__present_files` ONCE per fire (after `text` is posted) with an array of absolute paths. Cowork emits inline interactive cards immediately after the chat output.

The renderer no longer emits per-item `📄 [Open full brief]` markdown lines. The `data_view`'s `artifact_link.path` field is still populated (used by the orchestrator to assemble the `present_files` array), but the renderer ignores it for chat output.

**No example rendered output is included by design (v2.10.8+).** Read `shared/scripts/chat_output_renderer.py` if you need to understand the output format. Execute the renderer; post what it returns.

**Action surface (v2.10.9+ — all-batch button widget per `shared/CHAT_ACTION_WIDGET.md`):**

The action surface flips from typed-number text to a `show_widget`-rendered button group per meeting. Each meeting's `actions` list (`open`, `tweak`, `regenerate`, `skip`, `push to [date]`) becomes a button row inside the widget. All selections accumulate locally; "Apply all" fires a consolidated `apply choices: [...]` payload. See `shared/CHAT_ACTION_WIDGET.md` for the full spec.

**Heavyweight action note for upcoming-meetings:** `open SLUG` expands the brief inline; that expansion happens AFTER the user clicks Apply, in the same chat turn as the consolidated ack. Brief .docx files surface via `mcp__cowork__present_files` in the same response.

**Per-button labels (the strings rendered on each meeting's button row, v2.14.38+):**
- `push meeting [date]` — schedule a reschedule email. Widget exposes a free-text natural-language input (`monday at 2`, `tomorrow afternoon`, `2026-05-12`). Payload includes `input` field with the user's literal text; the reply handler parses the natural language at apply time. Widget displays as `Push meeting`.
- `snooze 3d` (v2.14.38+) — fixed 3-day snooze. Widget displays as `Snooze (3 days)`. Item won't re-surface in upcoming-meetings until the date passes.
- `add to my list` (v2.14.38+) — defer indefinitely. Surfaces in `show my list` grouped by attendee.

**The dedicated `context [text]` action button is REMOVED in v2.14.38+.** The universal `+ Add context` collapsible toggle (rendered on every item by `chat_output_renderer.py` line 1934) covers the same intent-aware affordance — empty input is no-op; non-empty input is captured into the apply-choices payload alongside whatever action fires AND routed intent-aware (question-shaped → synthesize answer; statement/instruction → regenerate brief). Per M's 2026-05-07 ask: *"if we can achieve [universal context button] across the board we can drop the actual context button from all actions."* Single context affordance per item; no per-action context buttons.

**Back-compat note (v2.14.38+):** the canonical action set keeps `context [text]` / `add more context [text]` / `ask question [text]` as deprecated aliases inside `CANONICAL_ACTIONS` so any in-flight applies originating from a pre-v2.14.38 widget still pass the canonical-action validator. `apply-choices` translates them to the universal `+ Add context` capture flow + the existing intent-aware dispatch. New widgets always emit the new action set above.

**Removed in v2.12.0–v2.12.2:**
- `open` (v2.12.0) — redundant with the post-widget brief hyperlink.
- `tweak [change]` (v2.12.2) — superseded by `more context`. M's Apr 30 ask: "Drop Tweak."
- `regenerate` (v2.12.2) — superseded by `more context` (which also regenerates, but with user input). M's Apr 30 ask: "Drop regenerate."

The widget renders these as labeled buttons with the meeting slug captured via `data-n="<slug>"`. `apply-choices` reconstitutes `<slug> tweak` etc. on dispatch.

**Per-meeting docx surface (v2.10.8+):**
- Briefs surface via `mcp__cowork__present_files` cards posted AFTER the chat string, NOT as inline markdown links.
- The renderer no longer emits a `📄 [Open full brief]` markdown line per meeting.
- Skipped/internal meetings have no brief generated and therefore aren't included in the `present_files` array.

**Per-meeting first-line shape (v2.10.5+, slug NOT in brackets):**
- `[N]. [HH:MM AM/PM] — [Attendee Full Name] · "[Meeting title]" ([Project name OR plain-English routing note])`
- For routed meetings: `(Mira Fragrances)`, `(Northstar Partners)`
- For unrouted meetings: `(no project yet — say "new project" to track)` — NOT `(Acme Logistics — unrouted, no active project)`
- For internal meetings (v2.14.36+): `[N]. [HH:MM AM/PM] — [Attendee Full Name] · "[Meeting title]" ([Project name] · internal)` — same shape as external, with `· internal` suffix in the routing parens to signal attendee mix at a glance.
- For skipped personal calls / unrouted solo blocks only: `[N]. [time] — [Title] (skipped — personal/no project)`
- The slug is now baked into the action pills only — not in the first-line bracket prefix. (v2.10.5+ change: per Rule 6, we drop the redundant `[slug]` prefix when the full name appears.)

**Source links inline + Sources section (per `_hq/CONVENTIONS_SOURCE_LINKS.md`):** when a brief references something that came from a connector — a recent email, a document, a transcript, a calendar event — link it inline as `[Title — date](URL)` per the convention. Always prefer the URL the connector returned (Gmail MCP `thread_url`, Granola MCP `note_url`, Calendar MCP `event_url`) over a synthesized one.

End the chat turn with a `Sources:` section listing every connector source referenced — calendar event(s), prior emails, prior transcripts, related session notes. One bullet per source. Markdown links only. If no sources are referenced (rare — the calendar event itself should always be a source), omit the section.

**No "Notes from this run" footer (v2.10.5+):** drop any closing block that narrates run mechanics ("Treating this as a test re-run", "all three meetings had already concluded", "Bowie and Quinn both parked in _unrouted/"). If something requires user attention, surface it INLINE per item as a context tag (e.g. "concluded ~7h ago" appended to the first line) or as a pending-decision pill (e.g. `▸ start project for Acme Logistics`).

# Phase 7 — Failure handling (Rule 8)

- Calendar timeout / Granola unreachable / single connector flake: log to `errors[]` in pack_run event; continue with other meetings; set `pack_run.status: "complete_with_degradations"`. Surface in chat in plain English: `(Calendar refresh degraded — using yesterday's snapshot.)` or `(Granola didn't respond — meeting briefs ship without prior-meeting context.)`
- Hard failure (entities.json malformed, file system unwritable): stop, append `scheduled_task_failure` event, surface plain-English one-liner ("Couldn't write briefs — workspace data layer looks corrupt. Run `cleanup` to diagnose.").
- docx skill chat-surface failure: fall back to plain-English link-not-available note per Phase 4 step 3.

NEVER silent retry. NEVER expose tool names or error class strings in chat.

# Reply handling

Parse:

- `open SLUG` → expand the full brief content inline in chat (read the .docx, present in plain text).
- `context SLUG [text]` (v2.14.37+) → intent-aware dispatch on the user's textarea content:
  - **If text is question-shaped** (ends with `?`, or first word matches `^(what|why|how|when|who|which|is|are|was|were|did|does|do|will|can|could|should|would)\b`) → fire a context-loaded synthesis prompt: pull transcripts from the meeting's attendees over the last 90 days, pull recent email threads with them, pull related decisions from the decision log, and answer the user's specific question. Format: short plain-English answer (1-3 paragraphs) with source citations (meeting dates, email subjects). Returns in apply-choices Step 4B (terminal action — the answer text IS the response, not a draft for further action). Replaces the v2.14.14 `ask question` handler.
  - **Otherwise (statement / instruction)** → re-run call-prep with the user's added context folded in. Regenerate the `.docx` via the docx skill. The regenerated brief surfaces in the apply-choices consolidated response per `apply-choices/SKILL.md` Step 4 — clickable link inside the response widget so the user can open in Cowork without scrolling. Replaces the v2.12.4 `add more context` handler.
- `add more context SLUG [text]` (v2.12.4 - v2.14.36 alias, accepted for back-compat) → translate to `context SLUG [text]` and dispatch through the intent-aware handler above.
- `ask question SLUG [text]` (v2.14.14 - v2.14.36 alias, accepted for back-compat) → translate to `context SLUG [text]` and dispatch through the intent-aware handler above.
- `snooze SLUG 3d` (v2.14.38+) → write `chat_dismissal` event with `data.snooze_until: <today + 3d>`. Meeting card won't re-surface until the date passes. Plain-English ack only if mentioned: `"Snoozed #N for 3 days."`
- `add to my list SLUG` (v2.14.38+) → write `commitment_to_discuss` event grouped by the meeting's primary attendee. Surfaces in `show my list`.
- `skip SLUG` (deprecated v2.14.38+ — back-compat alias for in-flight pre-v2.14.38 widgets) → translate to `snooze 3d` semantics + same dismissal write.
- `push SLUG to [when]` → parse the user's natural-language input (`monday at 2`, `tomorrow afternoon`, `2026-05-12`) into a target date/time. If parseable, draft reschedule email via email-writer per EMAIL_DRAFT_PROTOCOL. The draft surfaces inside the apply-choices consolidated response widget (v2.12.4+ — Send / Edit then send / To drafts / Edit then draft / Skip per the same widget contract as the source orchestrator). If unparseable, surface item-level error in the consolidated ack ("couldn't parse '<input>' as a date — re-fire and try a clearer time").
- `tell me about [name]` → fire the people-crm "tell me about" cross-reference flow.

For unrecognized → respond once in plain English: "Reply with `open SLUG`, `tweak SLUG [change]`, `regenerate SLUG`, `skip SLUG`, or `push SLUG to [date]`. Or `tell me about [name]` for a deep cross-reference."

# What this orchestrator does NOT do

- Does NOT auto-send anything (every send is the user's explicit action).
- Does NOT modify entities.json directly (people-crm canonical writer).
- DOES now process internal-only meetings (v2.14.36+) — they get a project-context brief instead of an external-prep brief.
- Does NOT re-process meetings (that's `cr-past-meetings` at 5 PM).
