---
name: weekly-recap
description: "Pulls 7 (or 30) days of context across every connected source — Gmail/Outlook, Calendar, Slack/Teams, Drive/OneDrive, and every meeting-transcript source — into one structured recap: what happened, decisions made over email, threads of note, new people, anomalies, and what Command Room chats produced. Fires on: 'weekly recap', 'what happened this week', 'recap my week', 'monthly recap', 'what happened this month', plus 'tune weekly-recap'. Runs as the Friday-wrap scheduled chat's engine and on demand. Does NOT fire on 'operator report' / 'show me the value' (operator-report — lift accounting), 'value receipt' (value-receipt), or 'weekly insights' (insight-generator — pattern synthesis, not events digest). Section list and source map: Routing section in the body."
---

# Weekly Recap — 7-Day Cross-Connector Synthesis

**For:** the end-of-first-call demo moment, weekly retrospectives, or any time the user wants a substantive look back at the last 7 days. Bridges the gap between scheduled tasks (which fire on today's window only) and the customer's reasonable expectation that the system has something meaningful to say about the last 7 days.

## Skill Boundary (v2.1)

- **Use weekly-recap for:** a synthesis of the last 7 days across every connector, written as both an inline chat summary and a saved `.docx`. Auto-runs scan-for-commitments as a side effect so the recap's commitment counts are populated.
- **Use `morning-briefing` for:** today's window (last 18h of email/Slack + today's calendar). Daily scan, not weekly.
- **Use `cleanup` for:** workspace health check (file freshness, schema validity, stale projects). Operational, not narrative.
- **Use `meeting-notes` for:** single-meeting processing.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. JSON sources live in `_hq/data/`; markdown views in `_hq/views/` are regenerated and must not be written directly. Violations go to `_hq/CONFLICTS.md`.

**Atomic-write requirement (v2.10.5+):** ALL writes to `_hq/data/entities.json` / `events.jsonl` / `aliases.json` MUST go through `shared/scripts/atomic_write.py`. Use `atomic_append_jsonl` for events.jsonl appends — batch all 7-day capture events into a SINGLE call (one I/O round-trip, not 200).

You are an **appender** for `events.jsonl` — every connector read this skill performs emits a corresponding event (`interaction`, `meeting`, `note`, `file`) tagged with `source_skill: "weekly-recap"`. This is the backfill side effect that primes events.jsonl for the recap's synthesis.

You are the **primary writer** for:
- `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` — the saved artifact (date is the trigger day in workspace TZ).

You do NOT write to entities.json or aliases.json. Person / project / org records discovered during the 7-day pull are queued for `people-crm` on the next turn via `pending_review: true` event annotations — never written into entities.json by this skill.

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. Every connector read emits corresponding events to `events.jsonl` per that contract's rules. Dedup via `source_ref_hash` makes capture idempotent across repeated invocations on the same window.

---

## Output Verbosity Rules (v2 — first-call demo quality)

Outputs must be substantively rich, not just longer. "Rich" means:

1. **More named references, not more adjectives.** Surface specific people, dates, project names, prior commitments, doc names. Never generic summary words. If a section has nothing real to surface, omit it — never pad with "no data captured yet" placeholders.

2. **Cross-references over isolated data.** Every output should connect at least 2-3 entities (this person + that project + that commitment + that older thread). Demonstrates the memory layer at work, not just data retrieval.

3. **Concrete > generic.** "Mira disagreed on sequencing on the Oct 14 call" beats "team has differing views on timing." If the data supports a concrete reference, use it.

4. **Length scales with signal density.** A project with 47 events in 14 days gets a 6-bullet "where things stand"; a project with 4 events gets 2 bullets. No padding to hit a target length.

5. **Every output ends with a clear "what now" line** — either next actions, a question, or a clean handoff to another skill. No outputs that just dump data without telling the user what to do with it.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the recap renders first, then one-tap changes are offered.
Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "lens": "theme_led",            # theme_led (narrative themes lead) | numbers_led (metrics lead)
    "internal_backlog": "split",    # split (internal vs external split view) | external_only
}
cfg = get_config(workspace_root, "weekly-recap", DEFAULTS)
```

`lens` sets whether the recap opens with narrative themes or with the week's numbers.
`internal_backlog=split` shows internal build work separately from external/client work;
`external_only` hides the internal backlog section.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "weekly recap", scheduled Friday fire | render the recap with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "weekly-recap", DEFAULTS)` BEFORE rendering, then append the first-run block. |
| **Show settings** | "show weekly-recap settings" | render current config in plain English; no recap. |
| **Tune** | "tune my weekly recap", "tune weekly-recap" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-render. |
| **Reset** | "reset weekly-recap to defaults" | `wipe_skill_config(workspace_root, "weekly-recap")` → next fire is a first-fire again. |

**The first-run block (transport):** the on-demand fire ends in a chat summary + a `.docx` link
(not an action widget), so it uses a 2–3 line FOOTER after the recap. The scheduled Friday fire
(`orchestrator-friday-wrap.md`) appends the same footer (that orchestrator is a markdown post, not
a widget — see its first-run note).

> *First time recapping your week. I set 2 defaults: **I lead with the themes, not the numbers** ·
> **I split what you owe into work for others vs your own projects**. Say "tune my weekly recap" to
> change either, or just tell me ("lead with the numbers" / "external work only").*

The footer renders exactly once ever (`is_configured` gate). Day/time of the scheduled fire is
`change-schedule` (the friday-wrap task), not tune. The scheduled Friday Wrap fire follows
`shared/RELIABILITY.md` (skip-don't-fail when the workspace isn't ready, OOO handling,
missed-fire recovery, connector-timeout degradation) — the on-demand trigger is unaffected.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "lead with the numbers" / "metrics first" | `lens = numbers_led` |
| "lead with the themes" / "narrative first" | `lens = theme_led` |
| "external work only" / "hide my internal backlog" | `internal_backlog = external_only` |
| "show the internal/external split" | `internal_backlog = split` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-render + confirm in one line.

## Trigger interpretation

Two grouping modes — pick based on trigger phrasing, default to by-project:

| Trigger phrase | Mode |
|---|---|
| `weekly recap` / `weekly summary` / `what happened last week` / `summarize last week` | **By project** (default) |
| `recap last week by project` | **By project** (explicit) |
| `recap last week by day` / `day by day recap` | **By day** |

By-project is default because the customer's mental model is project-shaped (what happened on Acme, what happened on Northstar) more than calendar-shaped. By-day mode is for retrospective use — "what did I actually do Monday vs Friday."

## Window definition

The "last 7 days" window is `[now - 7d, now]` in workspace timezone (`entities.json` `workspace.user_timezone`). Computed at the start of Phase 1 via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (v3.11.1+ — `workspace_path` is REQUIRED). Use the resolved `<window_start>` / `<window_end>` ISO timestamps for every connector query.

The user can override with `weekly recap from <date> to <date>` or `recap the last 14 days` — handle these by re-computing the window before Phase 1. Cap at 30 days max — anything longer falls outside passive-capture's intended scope, and the user should run `backfill [N] months on [project]` for a single-project deeper pull.

---

## Phase 1 — Setup

Resolve plugin + workspace paths (canonical CONTRACT.md Rule 22 preamble):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
```

Read:
- `<WORKSPACE>/_hq/data/entities.json` — primary user (`is_primary_user: true`), timezone, all canonical person/project/org records (for canonicalization during capture).
- `<WORKSPACE>/_hq/data/aliases.json` — for raw→canonical resolution on every captured interaction.
- `<WORKSPACE>/CLAUDE.md` if exists — hot cache.

Compute window in workspace timezone.

**Cost budget:** target ~15-30 sec total wall-clock for Phase 2 + Phase 3 + Phase 4. If the budget overruns, fall through to degraded mode (see Phase 2 caps below).

---

## Phase 2 — Pull 7 days from every connector (parallel reads, capped)

Run all connector queries in parallel where the MCP layer supports it. Skip any connector whose first call exceeds a 5-second timeout — log silently and footnote at the end of the recap.

**Pull strategy — headers/snippets by default, full bodies only for top signals.** The handoff originally specced "Gmail full bodies for project-tagged threads" — that's too slow on a busy workspace (200+ threads × 2 sec each = 7 min). Default to headers + snippets for the 7-day pull; full bodies fetched only for top 20 threads ranked by signal density (canonical-person involvement + project-tag match + thread length).

### Mail (Gmail / Outlook — native MCP, never Zapier for read)

- Window: last 7 days inbox + sent
- Cap: 250 received + 250 sent (default); ranked by importance (canonical-people involved, project alias match in subject, thread length).
- Body strategy: headers + snippets for all; top-20 full bodies (those with canonical-people + project-tag).
- Emit per thread: `type: interaction`, `channel: email`, `direction: inbound|outbound`, `summary: <subject>`, `counterparty_person_ids: [...]`, `primary_thread_id: <resolved project>`, `related_thread_ids: [...]`, `classification_confidence`, `source_ref: "gmail:<thread_id>"`, `source_ref_hash`.

### Calendar (Google / Outlook)

- Window: last 7 days events that already occurred (start_ts < now).
- Cap: no cap (typically <200 events / week).
- Emit per event: `type: meeting`, `data: {title, start_ts, duration_min, attendee_person_ids, source_ref: "gcal:<event_id>"}`.

### Slack / Teams

- Window: last 7 days. DMs + tracked channels (per `_hq/BUSINESS_CONTEXT.md` or any channel with ≥2 canonical people).
- Cap: 200 messages total across all channels.
- Emit per distinct participant-day: `type: interaction`, `channel: slack|teams`.

### Drive / OneDrive / SharePoint

- Window: last 7 days modified or created.
- Cap: 100 files.
- **Names + paths + dates only — NEVER read file content here.**
- Emit per file: `type: note`, `data: {summary: "doc activity: <title>", source_ref: "drive:<file_id>"}`.

### Meeting-transcript sources (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries)

Generalize across all detected MCP connectors — no Granola hardcoding. Whichever transcript-source MCPs are wired contribute.

- Window: last 7 days.
- Cap: 50 transcripts total across all sources.
- Body strategy: summaries by default; full text for the top-5 highest-signal (longest duration × canonical-attendee count).
- Emit per transcript: `type: meeting`, `data: {title, start_ts, duration_min, attendee_person_ids, summary: <first 500 chars>, source_ref: "<connector>:<meeting_id>"}`.

### Command Room chat sessions — what happened in your chats this week (R4)

The connectors above cover meetings and mail, but a lot of the week's real work happens in the CEO's ad-hoc Command Room chats — a decision reasoned out, a commitment made, a deliverable produced. Add that as a recap source so the week reads complete.

- Resolve the session-transcript MCP at runtime by tool-name (the server id is per-install — never hard-code it, same as the meeting-transcript sources above). List the sessions active in the last 7 days and read each transcript.
- Window: last 7 days. Cap: 30 sessions; summaries only, never raw transcript text.
- This is the SAME episodic layer the nightly `session-sweep` promotes into the event log (see `references/HOW_COMMAND_ROOM_WORKS.md` → the three-layer memory model). Read the already-recovered `session_sweep_run` history first: anything the sweep already logged is on file — surface it in the recap, don't re-append it. Only genuinely-new items get emitted, deduped via `source_ref_hash` like every other source (`source_ref: "session:<session_id>"`).
- If no session-transcript MCP is wired, skip this source silently and footnote it — same skip-not-fail posture as every other connector.

**Numeric verification (R6 — `shared/SUBAGENT_VERIFICATION.md`).** The recap fans out parallel reads and synthesizes; any count it renders (open commitments, briefs delivered, drafts written) comes from the canonical helper — `commitment_state.commitment_counts` for commitment totals, `value_receipt.compute_value_receipt` for delivered-work counts — never a hand-tally of the events a sub-read returned.

### Batched append

After all connectors return, batch all captured events into a single `atomic_append_jsonl` call:

```python
import sys
sys.path.insert(0, "shared/scripts")
from atomic_write import atomic_append_jsonl
atomic_append_jsonl(f"{workspace_root}/_hq/data/events.jsonl", all_events)
```

Dedup via `source_ref_hash` against the last 500 events in events.jsonl — any event whose hash already exists is silently skipped. Makes re-running the recap on the same window safe.

---

## Phase 3 — Run scan-for-commitments on the freshly-appended meeting events

Invoke `scan-for-commitments` skill in non-interactive auto-apply mode: `auto_apply: true`, `dry_run: false`. The skill audits the newly-appended `meeting` events (those with `source_skill: "weekly-recap"` in the last few seconds), extracts commitments per `shared/COMMITMENT_SCHEMA.md`, dedups via `(source_ref, title)`, and appends canonical `type: commitment` events.

This is what gives the recap's "Commitments captured this week" section real content even on a brand-new workspace where Past Meetings hasn't run yet.

If `scan-for-commitments` fails or times out, continue without it — surface a footnote: *"I couldn't pull commitments out of the meetings this run — the section below is surface-level only. Say 'scan for commitments' anytime and I'll go deeper."*

---

## Phase 4 — Synthesize the recap

Build the recap structure from events.jsonl over the window — **read via the org-scoped reader, never a raw load** (PGUARD1): `from events_io import load_events_org_scoped; events, skipped = load_events_org_scoped(workspace_root)`. It applies the account-scope mask and drops personal-lane rows by design, so a reclassified personal account or a personal reminder never enters the recap synthesis. Grouping mode per trigger interpretation (default by-project).

### Sections (omit any with no real content — never pad)

> **Executive Output Standard (EXEC1, v3.20.0+).** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`: the **Headline becomes the full exec header** (verdict = the synthesis-lead sentence — weekly-recap is a sanctioned synthesis-lead surface per the standard's synthesis rule — with CHANGED carrying **week-over-week money/time deltas**), and the **"What now" section becomes the ASK block** (the `asks` kwarg, one-tap on the Friday Wrap widget surface — one-ask-surface: the widget IS the ask, no prose twin). Both are SUBSUMED into those two EXEC1 surfaces, not rendered as duplicate standalone sections (net length must not increase).

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("weekly_recap", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions. Workspace exemplar (`_hq/exemplars/weekly_recap/`) beats the shipped seed; `None` = compose on the section list below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the recap. After saving the .docx, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered recap ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="weekly_recap", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

**1. Headline → exec-header VERDICT (EXEC1).** One sentence framing the week, as the verdict; CHANGED = week-over-week money/time deltas ("pipeline +$45K WoW; 2 deals slipped a week"). Pull from the dominant signal: "Heavy week on [Project A] (47 events) and [Project B] (31 events); 3 new people surfaced; 4 commitments captured." This is the exec header, not a standalone "Headline" section.

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md`. The headline names a dated anchor moment AND what changed — not a bare metric count.
```
GOOD: The May 14 CEO-group talk was the anchor — $45K early pipeline, two
      booked demos, and it opened the non-profit logistics wedge now running
      as a thread. Everything else this week supported it.
BAD:  This week had a lot of activity. Closed some deals, shipped features.
BAD:  This week saw 6 commitments, 4 decisions, 15 emails.   (metrics, no meaning)
```
Test: does the lead name a dated moment AND say what CHANGED? If the counts alone tell the same story, rewrite for shape, not numbers.

**2. Top decisions made** — pull `type: decision` events from the window. Bullets, each with: decision text + project + date + who participated. Omit if zero.

**3. Top commitments captured (you owe / they owe)** — split by `owner_id == primary_user_id`, reading `owner_id` via `cru_match._commitment_field(ev, "owner_id")` which covers every commitment-shape variant per `shared/COMMITMENT_SCHEMA.md` — the canonical `data.owner_id`, the flat-shape top-level `owner_id`, the legacy `owner` (no `_id`), and the `data.owner_person_id` variant that cr-past-meetings actively produces. Reading only `data.owner_id` silently drops shape variants 2–4 from the split (Sam bug report 2026-05-17 — partial fix in v3.4.2 covered shapes 1–2; v3.4.3 extends to all variants after M's own workspace audit found 42% of commitments in non-canonical shapes). Each bullet: commitment text + due date + counterparty + project. Cap at 10 in each direction, surface "+N more" if exceeded.

**4. Notable meetings** — rank by (duration × attendee count × first-touch flag for new attendees). Top 6. Each bullet: title + date + duration + attendees + project routing + one-line topic if transcript summary exists.

**5. Email threads of note** — high-priority unresponded (>72h since last inbound from canonical person + project-tagged) + decisions made over email (threads where the body contains decision-language patterns). Cap at 5.

**6. New people surfaced** — anyone interacting with canonical people during the window who isn't yet in `entities.json` as a person record. Flagged as `pending_review: true`. Surface name + how they appeared + suggested next action ("`tell me about [name]` to pull a full profile").

**7. Anomalies** — projects that went quiet vs. their normal cadence (no events but historical mean > 5/week), unusual cadence shifts on people (weekly→silent), new domains in email that don't map to known orgs.

**8. By-project breakdown** (default mode) OR **By-day breakdown** (explicit mode).

  - **By-project:** for each project with ≥3 events in the window, render: project name + event count + status badge + one-line "where it landed" + top open commitment. Sort by event count desc.
  - **By-day:** for each day (Mon → today), render: top 3 events (meetings, key emails, decisions) — one bullet each. Sort chronologically.

**8b. What the system did this week (SPEC LB1 — the change-feed roll-up).** One compact block from `change_feed.changes_since(<window start>)` (`shared/scripts/change_feed.py`): the week's totals in plain English — commitments closed from sent mail, items recovered from ad-hoc chats, proposals confirmed/declined, silent expiries, undos. Render the feed's lines verbatim (they carry the counts), cap 4, substance first; append one pointer when open proposals remain (`brain_proposals.card_health_counts` — *"[N] suggestions are waiting on you — say `staff meeting` to review them."*). Drop the whole section on an all-zero week — never pad. This is the value-narration slot: what running Command Room DID, distinct from what the CEO did.

**8c. Objectives — the weekly touch (SPEC OBJ1, DRAFT — the ONE place objectives ask for anything).** Drop the whole section when no open objectives exist. Otherwise, a fast substrate-only read: `objective_math.load_objective_inputs` → `compute_objective_health` → `recap_rows(health, names_by_person_id=<people map>)` for the status block (one line per objective, worst-first, rendered verbatim — the helper owns status honesty), then `due_self_reports(...)` for the ask block. Two sub-parts, each drop-empty:

  - **Where they stand:** the `recap_rows` lines, capped at the `active_cap` (they are a short list by design).
  - **Your word, batched (self-reported objectives due for a check-in):** number the due objectives and teach the reply inline — *"20 seconds: how do these stand? Reply like `objectives: 1 on track, 2 at risk — hiring is the bottleneck`."* **The numbering is RECORDED, not just rendered (the ordinal contract):** at render time, log the receipt `log_receipt(WORKSPACE_ROOT, "objectives", fired_via="scheduled", surfaced=<n due>, extra_data={"due_thread_ids": [<thread ids in the exact render order>]})` — the objectives skill's reply parser maps "1"/"2" against THIS list, never against a fresh recompute (a substrate write between render and reply would silently re-order a recompute and land a status on the wrong objective — the misattribution bug class). The reply fires the objectives skill (its weekly-touch parser → `objective_state.record_report` per item). An objective past its graceful-death threshold gets the different ask, verbatim: *"[name] — still an objective, or has it run its course? (keep / archive)"* — never another status ask.

  This section is THE weekly objectives touch (the objectives skill's design rule: one batched weekly ask, never per-objective pings, and the morning brief never asks). It rides this surface — no new scheduled task exists for objectives.

**9. What now → ASK block (EXEC1 element 4).** Explicit handoff, rendered as the `asks` close (0–3 reader-actions), not prose. Suggest up to 3 follow-ups: which Past Meeting deserves a follow-up call-prep, which commitment is closest to overdue, which project needs a deeper `go [project]`. On the Friday Wrap widget the widget IS the ask block — one-tap, no prose twin.

### Format constraints

- **Length scales with signal density.** A quiet week with 4 meetings and 12 emails produces a 20-line recap. A heavy week with 47 events on one project produces 50-60 lines. **Never pad to hit a target length.**
- **No placeholders.** Sections with no content are omitted entirely, not stubbed with "No data captured."
- **Named references > adjectives.** "Mira disagreed on sequencing on the Tue Oct 14 call" > "team has differing timing views."
- **Cross-reference at least 2-3 entities per bullet.** This person + that project + that commitment.

---

## Phase 5 — Output: dual surface (inline + .docx)

Per `shared/CONTRACT.md` Rule 3 dual-surface pattern.

### 5.A — Inline chat summary

Post the synthesized recap as a markdown chat turn body. Target ~30-60 lines for a typical week (lower bound for quiet weeks, upper for heavy). Use the section structure from Phase 4. Scan-friendly: bold project names, dates in `YYYY-MM-DD` or `Mon DD` format, named people in plain text (no entity-ID leaks).

### 5.B — Saved `.docx`

Generate the saved artifact via `shared/scripts/brief_writer.py` per the canonical brief-writer pattern (same as `call-prep` skill):

- **NEVER hand-roll the recap** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking recap (the v3.20.0 failure mode) — and this one fires on a schedule, so a bypass here is not one bad document, it is a standing weekly one nobody is watching.
- **NEVER create, render, copy, upload, or update the recap — or any part, derivative, or restatement of it ("the week in numbers", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/meetings/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so the team can read it Monday", "as a copy alongside the canonical file" — **nor a direct instruction**: "put this week's recap in a Google Doc so I can send it round" is a request this gate refuses, not an override. Hand back the `.docx` link and let the user forward the file itself.

The block below is self-contained: it resolves the workspace and today's date itself (Rule 22 discovery), falling back to `CR_WORKSPACE_ROOT` / `CR_TODAY` only when an orchestrator has already exported them — an on-demand fire with neither env var set must not crash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
export CR_WORKSPACE_ROOT="${CR_WORKSPACE_ROOT:-$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')}"
cd "$PLUGIN_ROOT" && python3 -c "
import sys, os
sys.path.insert(0, 'shared/scripts')
from brief_path import get_brief_path, get_brief_artifact_url, ensure_brief_directory
ws = os.environ['CR_WORKSPACE_ROOT']
ensure_brief_directory(ws)
date_iso = os.environ.get('CR_TODAY')  # YYYY-MM-DD in workspace TZ (set on scheduled fires)
if not date_iso:
    from datetime import datetime, timezone
    from tz import to_local
    date_iso = to_local(datetime.now(timezone.utc), workspace_path=ws).strftime('%Y-%m-%d')
path = get_brief_path(ws, 'weekly_recap', '', date_iso)
url = get_brief_artifact_url(path)
print(f'BRIEF_PATH={path}')
print(f'BRIEF_URL={url}')
"
```

Capture stdout. Then compose section content matching the inline recap and pipe to `brief_writer.py` stdin as JSON:

```bash
cd "$PLUGIN_ROOT" && python3 shared/scripts/brief_writer.py <<'JSON'
{
  "output_path": "<BRIEF_PATH from above>",
  "brief_kind": "weekly_recap",
  "title": "Weekly recap — <Mon DD> to <Mon DD>",
  "subtitle": "What happened this week and what's next",
  "exec_header": {
    "verdict": "<the synthesis-lead headline sentence — the dated anchor + what changed>",
    "changed": "<week-over-week money/time deltas: 'pipeline +$45K WoW; 2 deals slipped a week'>",
    "decide": "<the one decision this week needs, or 'Nothing — execution week.'>",
    "needs": "<the single most important reader-action, or 'Nothing from you.'>"
  },
  "sections": [
    {"heading": "The week in numbers",
     "tiles": [
       {"label": "Meetings", "value": "6"},
       {"label": "You owe", "value": "4"},
       {"label": "Owed to you", "value": "7"},
       {"label": "Decisions", "value": "3"},
       {"label": "New people", "value": "2"}
     ]},
    {"heading": "Decisions this week", "body": "..."},
    {"heading": "What you owe (external)",
     "table": {"headers": ["What", "For whom · due"],
               "rows": [["Send the packet", "Sam Sample · Fri Jul 11"]]}},
    {"heading": "What you owe (internal projects)", "body": "..."},
    {"heading": "What they owe you",
     "table": {"headers": ["What", "From whom · age"],
               "rows": [["Updated NetSuite mapping", "Bo Sample · 10 days"]]}},
    {"heading": "Meetings worth noting", "body": "..."},
    {"heading": "Email threads worth noting", "body": "..."},
    {"heading": "New people who came up", "body": "..."},
    {"heading": "Things that look unusual", "body": "..."},
    {"heading": "By project", "body": "..."}
  ],
  "asks": [
    {"text": "<follow-up call-prep / overdue commitment / deeper go [project]>"}
  ]
}
JSON
```

**Visual layer (v4.6.1 S3 — the prep-v2 pattern extended here per F-60's follow-up; M directive: tiles/tables over bullet walls in recurring deliverables):**

- **"The week in numbers" stat-tile band opens the doc** — 1-5 tiles, every value counted from THIS window's events (the same numbers Phase 4 already derived), never estimated. **A tile with no data is DROPPED, never rendered empty**; when nothing is countable the whole section is omitted (brief_writer's `_add_stat_tiles` refuses empty tiles at the render chokepoint, same as prep).
- **The two owe sections render as two-column tables** (what | who · due/age) instead of bullets — same drop rule: no rows, no section. Cap 10 rows per direction with a final "+N more" row.
- Everything else stays prose/bullets — the tiles and tables carry the scannable layer; no decorative charts, no fabricated numbers, substrate-derived only.

**Visual pass (SPEC OUT2 §3, after the .docx save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I made 2 calls: theme-led · internal/external split view"
- Good: "I set 2 defaults: I lead with the themes, not the numbers · I split what you owe into work for others vs your own projects"

The former `"Headline"` section is now `exec_header` (the verdict + CHANGED deltas); the former `"What's next"` section is now the `asks` ASK block (max 3). Neither is rendered as a standalone section — they are subsumed (no-duplication, net length must not increase).

The last grouping section is `"By project"` for week-with-many-projects or `"By day"` for week-with-one-dominant-project — pick whichever fits and use it as the literal heading. Don't ship both. (Pre-v3.13.6 the example had `"By project" (or "By day")` as a parenthetical inside the JSON; that fails to parse.)

The `subtitle` field is REQUIRED by `brief_writer.make_brief_from_json` — omitting it raises `KeyError: 'subtitle'`. Keep it short (one line, sentence case).

**v3.13.0+ internal/external split rule (per M's 2026-05-20 feedback #23a):** pre-v3.13.0 the recap mixed the user's internal plugin backlog ("re-tune the cleanup rubric", "fix the dropped Daily Brief task") with external client/relationship commitments ("Send the packet") in a single "You Owe" section. Same scope-drift class as #6a (insight-generator) and #11 (memo-writer). v3.13.0 splits them:

- **External (You Owe):** commitments to people OUTSIDE the user (clients, prospects, advisors, family-office portfolio, etc.). The default bucket.
- **Internal (You Owe — build / self-development):** commitments to YOURSELF about a system or platform you operate (an internal product build, workspace cleanup, infrastructure improvements). Only applies when `_hq/BUSINESS_CONTEXT.md` identifies the user as the builder/operator of an own platform or internal system; users without one have an empty Internal section and the rendered output omits it.

Classification heuristic — a commitment is "Internal" when ALL of:
- `data.owner_id == user_id` (the workspace's primary user, resolved via `entities.json` `workspace.user_id` or the legacy `is_primary_user: true` person record)
- The topic / `primary_thread_id` matches one of the user's own-platform / internal-infrastructure projects as named in `_hq/BUSINESS_CONTEXT.md` (never a hardcoded project list)
- No external counterparty named in the commitment data

Otherwise the commitment is External (default). When in doubt, classify as External — losing an internal item into the external bucket is mild; surfacing internal-build noise alongside client work is the bug M flagged. The Internal section renders only if it has ≥1 item; otherwise omit per the "no empty sections" rule.

### 5.C — Surface as the canonical H2 heading link at the bottom of the chat turn (v3.13.0+)

After the synthesis content and `Sources:` section, render the recap link as an H2 heading at the very bottom of the chat turn per `shared/CONTRACT.md` Rule 3:

```python
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url

label = f"Weekly Recap — {date_iso}"
url = get_brief_artifact_url(absolute_docx_path)  # native computer:// form (v3.13.0+)
h2_link = doc_headline_link(label, url)
# h2_link == "## → **[Weekly Recap — 2026-05-23](computer://...)**"
# Output as the LAST line of the chat turn.
print(h2_link)
```

Don't put the link inline in the recap body. It MUST be at the bottom or it gets lost between paragraphs (per M's 2026-05-20 feedback #6d/#9/#11).

`mcp__cowork__present_files` is OPTIONAL post-v3.13.0 and is no longer the primary opener — Cowork's Windows resolver doesn't open most file types reliably from card click (per M's 2026-05-20 testing #29). The H2 native `computer://` link IS the opener. If you include `present_files`, position it AFTER the H2 link as a reveal-in-folder convenience only — skip it entirely if the user is unlikely to need filesystem navigation (default for weekly-recap: skip).

### 5.D — Demo closing line (first-call use only)

If invoked during the first-call demo arc (heuristic: `_hq/data/events.jsonl` is <48h old by mtime, AND `<workspace>/SESSION_NOTES_*` files have <5 entries each), end with:

> *"Here's everything Command Room saw from your week. Going forward, this is automatic — Past Meetings catches every call as it happens, Morning Brief surfaces what needs attention before you start your day."*

Skip the demo line on subsequent runs (when the workspace is mature enough that the closing line would feel patronizing).

---

## Phase 6 — Log + close

Append one `weekly_recap_run` event:

```json
{"type": "weekly_recap_run", "source_skill": "weekly-recap", "primary_thread_id": null, "related_thread_ids": [], "classification_confidence": null, "data": {"window_start": "<ISO>", "window_end": "<ISO>", "mode": "by_project|by_day", "events_captured": <N>, "commitments_found": <N>, "recap_path": "<absolute>", "outcome": "complete"}}
```

**STOP.** The chat turn is over. Do not narrate what was just posted.

---

## Idempotency

- All events.jsonl appends dedup via `source_ref_hash`. Re-running on the same window doesn't double-capture.
- The `.docx` overwrites the same date's prior version (filename collision = newer wins). Customers can re-run to refresh.
- The `weekly_recap_run` event ALWAYS appends fresh (one row per fire, even if the captured events were 100% dedups). Provides the audit trail.

## Cost / timing budget

| Phase | Budget | Failure mode |
|---|---|---|
| Phase 2 connector reads | 15-20 sec total | Skip individual connectors that timeout > 5 sec; footnote at end of recap |
| Phase 3 scan-for-commitments | 5-10 sec | Skip silently with footnote |
| Phase 4 synthesis | 5-10 sec | n/a — this is local LLM work |
| Phase 5 `.docx` save | 1-2 sec | If brief_writer fails, surface inline recap only and footnote the missing .docx |
| **Total** | **~25-40 sec** | Within demo-arc tolerance |

If the total budget overruns 60 sec on a heavy workspace, stop the in-progress connector reads, surface a partial recap from what's been captured, and footnote: *"This was a heavier week than usual — I had to wrap before I finished. Run 'weekly recap' again or narrow it with 'recap the last 3 days' and I'll go deeper."*

## What it doesn't do

- Does not modify `entities.json` — new people surfaced are queued for `people-crm` via `pending_review: true` event annotations.
- Does not draft any emails or follow-ups — those are explicit follow-on commands (`follow up with [person]`).
- Does not re-process meetings already processed by Past Meetings — the events.jsonl events from those Past Meetings runs are READ by this recap, not duplicated.
- Does not register or modify scheduled tasks.
- Does not fire on `cleanup` (different skill, different surface).
- Does not exceed a 30-day historical window even when explicitly asked — for longer pulls, route to `backfill [N] months on [project]` per-project.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Pulls 7 days of context across every connected source (Gmail/Outlook, Calendar, Slack/Teams, Drive/OneDrive, every meeting-transcript source — Granola/Fireflies/Otter/Read.ai/Zoom AI Companion/Microsoft Teams), writes interaction events to events.jsonl as it goes (passive backfill side-effect), runs scan-for-commitments on the freshly-captured meeting events, then synthesizes the week into both an inline chat summary and a saved `.docx` artifact at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`. Designed for the end-of-first-call demo (covers the gap between 'scheduled tasks fire on today only' and 'customer wants substantive output by end of call') and for any week-on-week retrospective. Triggers: 'monthly recap', 'what happened this month' (same recap engine over the last calendar month — set the window accordingly), 'weekly recap', 'weekly summary', 'what happened last week', 'last week recap`, `recap of last week`, `summarize last week`, `give me a weekly recap`, `recap last week by project`, `recap last week by day`. Also handles first-run personalization settings — use when the user says 'tune my weekly recap', 'tune weekly-recap', 'show weekly-recap settings', 'reset weekly-recap to defaults'. DOES NOT fire on `cleanup` (that's `cleanup` — a workspace health check, different surface), `morning briefing` (that's `morning-briefing` — daily, not weekly), `process the call` / `process last meeting` (that's `meeting-notes` — single meeting). Idempotent — all events.jsonl appends dedup via `source_ref_hash`, safe to re-run on the same window.
