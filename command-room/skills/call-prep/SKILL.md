---
name: call-prep
description: "Walk into a specific meeting already prepped. Fires on: 'prep me for my 2pm' (any time), 'prep me for the [name] call', 'quick prep me for my 3pm', 'prep the call', 'prep me for the board meeting', 'get me ready for [meeting]', '1:1 brief for [name]', 'meeting prep', plus 'tune call-prep'. Synthesizes calendar, email, Slack, meeting transcripts, and open commitments for every attendee into one scannable brief saved to the meetings folder and linked in chat — with learned per-meeting-type section weights applied. Does NOT fire on 'brief me on today' (morning-briefing — all meetings, summary only), 'process the call' (meeting-notes — post-meeting), 'prep for 1:1 with [direct report]' (team-intelligence), or speaking-engagement prep (memo-writer position paper). Full trigger list and section spec: Routing section in the body."
---

## Deliverable Render Gate (GATE1 — MUST, v3.20.x; ONE GENERATOR v4.5.2 S1)

This skill produces a `.docx` brief deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **ONE GENERATOR (v4.5.2 S1, fixes FINDINGS F-60):** this pipeline — five-block gathering → `prep_pipeline.assemble_prep_sections` → `make_brief` → `receipts.log_prep_receipt` — is THE prep generator. The scheduled auto-prep (`orchestrator-upcoming-meetings.md` Phase 4) runs this same pipeline; there is no thinner scheduled variant. Depth differences come ONLY from the Standard/Deep setting, never from which path fired.
- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="call_prep", ...)`** (see the required call sequence below). That single call runs the output-contract gate (B3 — per-section depth floors), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written. The brief is also where the forwardable-clean / no-provenance rules are enforced.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard or PII-leaking briefs (the v3.20.0 failure mode).
- **NEVER create, render, copy, upload, or update a brief — or any part, derivative, or restatement of one ("talking points", "an agenda", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not on one vendor's API quirk). This is the same severity as the hand-rolled-`.docx` ban and fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root — so the artifact violates the workspace root rule by construction (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "as a copy alongside the canonical file" — **nor a direct instruction**: "put that in a Google Doc" is a request this gate refuses, not an override. Say the canonical brief already exists and hand back its link. Delivery is the `computer://` link to the canonical `.docx` in the meetings folder, only.
- **NEVER answer a prep request with a chat-only brief.** "Quick prep" is still a call-prep request — produce the `.docx` through `make_brief`. A short in-chat heads-up summary may accompany the file, but the file is the deliverable.
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A call-prep fire that yields a brief with NO `gate_ran` event for that turn is a flagged bypass. Pass `workspace_root` to `make_brief` so the event lands in substrate.
- **Visual pass (SPEC OUT2 §3, after every save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

If anything below seems to contradict this gate, THIS GATE WINS.

## Skill Boundary (v2.1)

- **Use call-prep for:** deep single-meeting prep. Pulls every available context source for one specific meeting happening today or soon. Output is a structured brief — **2-4 pages, 800-1500 words, every section substantive**. Skinny output (1-page stub, 1-bullet talking points, generic relationship context) is the failure mode call-prep was built to prevent. See "Brief Format — required content depth" below.
- **Use `morning-briefing` for:** the top-of-day all-meetings digest. One-liner per meeting, no deep prep. Fires 7:30am as a scheduled task.
- **Use `meeting-notes` for:** after the meeting — processes the transcript into decisions, commitments, action items.
- **Pair pattern:** morning-briefing surfaces that you have the meeting → call-prep deep-dives on it → meeting-notes closes the loop after. Three skills, three phases of the same meeting.

## Personification Contract (v3.13.8.4+)

Before composing the .docx brief, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The brief's cover line (replacing the prior `"Call Prep — [Project] — [Date]"` template) uses the shape:

```
Call Prep · {Project} · {Date}
Prepared by {brain_name} for {first_name}
```

where `{first_name}` comes from `entities.json` `workspace.user_first_name` and `{brain_name}` defaults to `"Penelope"`. No additional name references inside the body — keep it formal and substantive, the cover line carries the personification.

## Writer Contract

This skill reads from Gmail, Calendar, Slack, and Granola to build the brief. Every connector read emits corresponding events to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` using the v2.2 shape (`primary_thread_id` + `related_thread_ids[]` + `classification_confidence` + `org_ids[]` + `source_ref_hash` for dedup). Capture happens silently as a side-effect of building the brief — the CEO sees only the brief, but memory accumulates. Classification follows the unified confidence-band routing: ≥0.75 auto-tag, 0.40–0.75 tag provisionally + silent queue for Pass 8, <0.40 tag as unclassified. **Never asks the CEO a classification question mid-prep** — if confidence is low on which project the meeting belongs to, pick the best-guess primary and log for later review.

## Coaching handoff (SPEC COACH1 §4.6) — check this BEFORE building

Some workspaces run the Business Coach Pack. On those, a meeting whose counterpart is a coaching client or a coaching cohort gets a prep built by the pack's own skill, off the client's arc — not this brief. Run the check first; on every other workspace it costs one function call and returns `defer: false`.

```python
from coach_state import coaching_handoff_for_meeting
handoff = coaching_handoff_for_meeting(
    WORKSPACE_ROOT, attendee_person_ids=<resolved attendee ids>, title=<calendar title>)
```

- **`handoff["defer"] is True`** → say one line naming the engagement (`handoff["name"]`), hand off to `coach-session-prep`, and **stop**. Do not also build a call-prep brief; two prep documents for one meeting is the failure this handoff exists to prevent.
- **`handoff["defer"] is False`** → proceed with this skill exactly as before. That is the answer on every non-coach workspace (the pack absent or `workspace.coach.enabled` false), and it is also the answer when the substrate is genuinely ambiguous — two cohorts that fit the same attendee set, or two coaching engagements on one meeting. In the ambiguous case `handoff["reason"]` says so; surface it as a one-line note in the brief so the coach can name the engagement, and keep building.

**This is a declared handoff, not a trigger fight.** `call-prep` keeps its entire trigger family — every phrase it owns today it still owns. The pack's prep skill names its own subject explicitly in every trigger and carries DOES NOT FIRE clauses against this skill (the named-subject pattern: a layered skill stays out of the general skill's way by naming its subject, never by competing for a generic phrase). The affirmative deferral above is the other half, so a coach who says "prep me for my 2pm" — a phrase this skill owns and always will — still lands on the coaching prep without having to phrase anything carefully.

The decision is a substrate question answered in code, never a routing judgment made here: `coach_state.coaching_handoff_for_meeting` resolves the meeting against the cohort ROSTERS (and the coaching threads), and refuses to defer unless the answer is unambiguous. Do not re-derive it from attendee names in prose.

## Project resolution

The meeting's project is its `primary_thread_id` (schema field — the identifier is stable, the vocabulary is "project"). Call-prep resolves this by:
1. Match calendar event → past meeting event (via `source_ref_hash`) → its `primary_thread_id`.
2. If no prior event, infer from attendees' `org_ids[]` + `primary_org_id` + recent projects involving that org (most specific operating child wins).
3. If multiple projects match, include the top match in the brief header and list the runners-up as "Related projects" so the CEO can cross-reference.

The brief header displays the project's canonical org (primary focus first, holding/child if relevant) per `morning-briefing` Step 4 layout rules.

---

# Call Prep

**For:** CEOs in 5-10 meetings a day who need to walk in prepared without spending 20 minutes digging.

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the brief is produced first, then one-tap changes are
offered. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "depth": "standard",   # standard (the canonical substantive brief) | deep (extended dossier)
    "auto_fire": "24h",    # 24h (24h before the meeting) | morning_of | off
}
cfg = get_config(workspace_root, "call-prep", DEFAULTS)
```

**Depth note (deliberate deviation from the catalog's "1-pager" label):** this skill's whole reason
to exist is preventing skinny 1-page briefs (see Skill Boundary + the GATE1 per-section depth
floors). So `standard` is NOT a 1-pager — it is the canonical 2–4 page brief `make_brief` already
enforces. `deep` adds the extended dossier sections (fuller relationship history, prior-meeting
arc, deeper open-thread context). The default never regresses below the enforced depth floor.

`auto_fire` is read by the `upcoming-meetings` scheduled orchestrator to decide when to
pre-generate a brief (`24h` before / `morning_of` / `off` = on-demand only). It never affects the
on-demand path — saying "prep me for X" always produces the brief regardless of this setting.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "prep me for…" | produce the brief with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "call-prep", DEFAULTS)` BEFORE rendering, then append the first-run footer after the brief link. |
| **Show settings** | "show call-prep settings" | render current config in plain English; no brief. |
| **Tune** | "tune call-prep" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-produce the brief. |
| **Reset** | "reset call-prep to defaults" | `wipe_skill_config(workspace_root, "call-prep")` → next fire is a first-fire again. |

**The first-run block (footer — call-prep ends in a chat link to the .docx, not a widget):**

> *First time prepping you. I set 2 defaults: **standard depth** · **auto-prep 24 hours before
> each meeting**. Say "tune call prep" to change either, or just tell me ("go deep on prep" /
> "prep me the morning of").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "go deep on prep" / "give me the full dossier" | `depth = deep` |
| "keep prep standard" / "shorter prep" | `depth = standard` |
| "prep me the morning of" | `auto_fire = morning_of` |
| "stop auto-prepping" / "only prep when I ask" | `auto_fire = off` |
| "auto-prep a day ahead" | `auto_fire = 24h` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-produce the brief + confirm in one line.

## What It Does

Generates a comprehensive meeting brief by pulling context from all your tools at once:

1. Finds the meeting on your Google Calendar
2. Extracts attendees, agenda, and timing
3. Pulls project context from session notes and project references
4. Reads `entities.json` for relationship context on each attendee — how you know them, last interaction, key notes, open items (canonical people layer per `shared/PASSIVE_CAPTURE.md`; legacy `_hq/PEOPLE.md` is still read as a fallback if present)
5. Searches Gmail for recent email threads with attendees
6. Searches Slack for relevant conversations
7. Reads `events.jsonl` for any open commitments tied to this project (legacy `_hq/MASTER_TRACKER.md` is still read as a fallback if present)
8. Retrieves notes from Granola for the last meeting with these people
9. **Resolves the ONE file this meeting's brief lives in** via `prep_pipeline.resolve_prep_brief_path(workspace_root, meeting_id, title=…, date_iso=…)` — the slug is a pure function of the calendar event id (v4.5.2 S1, F-29b). If a brief for this meeting already exists, the regeneration **refreshes it in place**; a second differently-slugged file for the same meeting is a defect (`acme-bo-sample-session` vs `bo-sample` was one meeting).
10. Generates a structured brief with all the context you need, then **writes the per-brief receipt** via `receipts.log_prep_receipt(workspace_root, meeting_id=…, slug=…, brief_path=…, generated_by="call-prep", fired_via="manual", refreshed=…)` — THE signal the morning brief's no-prep detection reads (F-29). A brief without its receipt gets flagged "no prep" tomorrow morning no matter how good it is.

### Brief save path (canonical — v2.12.6+, v2.14.32+ writer)

The brief saves to `_hq/meetings/` under the canonical filename produced by `shared/scripts/brief_path.get_brief_path()`. Do not hand-roll paths in this skill. Per `shared/CONTRACT.md` Rule 3, the prior `[Project]/meetings/` location (v2.10.8 - v2.12.5) didn't always resolve in Cowork's sandbox — users hit "folder cannot be found" on click.

**Required call sequence (v4.5.2 S1 — mirrors `orchestrator-upcoming-meetings.md` Phase 4 step 3; the SAME pipeline both paths run):**

```python
# Add shared/scripts to path (canonical preamble — same as orchestrators)
import sys
from pathlib import Path
SCRIPTS = Path(PLUGIN_ROOT) / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from brief_path import get_brief_artifact_url, ensure_brief_directory
from prep_pipeline import resolve_prep_brief_path, assemble_prep_sections
from brief_writer import make_brief
from receipts import log_prep_receipt

# 1. Resolve the ONE canonical path for this meeting (refresh-in-place, F-29b).
#    NEVER hand-roll a slug — identity is the calendar event id.
ensure_brief_directory(workspace_root)
res = resolve_prep_brief_path(workspace_root, meeting_id, title=meeting_title, date_iso=date_iso)

# 2. Assemble the five blocks (walk-out-with / changed / decide / owed / sourced
#    talking points) + visual layer through the pipeline — it owns section order
#    and rejects unsourced talking points (PrepContractError).
out = assemble_prep_sections(walk_out_with=..., meeting_details=..., ...)

# 3. Render through the gated chokepoint.
make_brief(res["path"], brief_kind="call_prep", title=..., subtitle=...,
           sections=out["sections"], exec_header=out["exec_header"],
           workspace_root=workspace_root)

# 4. Per-brief receipt — THE no-prep detector's signal (F-29). Skipping this
#    step re-creates the "no prep brief" false flag tomorrow morning.
log_prep_receipt(workspace_root, meeting_id=meeting_id, slug=res["slug"],
                 brief_path=res["path"], generated_by="call-prep",
                 fired_via="manual", refreshed=res["refresh"])

# 5. Surface the brief as a clickable link in chat. NEVER as a plain path string
#    or "saved to ..." narration.
artifact_url = get_brief_artifact_url(res["path"])  # native computer:// per v3.13.0+
```

**Surface in chat (v3.13.0+ — per CONTRACT.md Rule 3, H2 heading-link primary, `present_files` demoted):**

1. **H2 heading link at the BOTTOM of the chat turn.** Use `chat_output_renderer.doc_headline_link(label, artifact_url)` to render the canonical format: `## → **[Call Prep — {recipient or meeting title}](computer://...)**`. ("Call Prep" is the ONE name for this deliverable everywhere — chat link, doc cover, folder — never "1:1 Prep".) This is the PRIMARY surface — the link the user clicks to open the brief in Cowork's side panel. Goes at the END of the chat response (after the synthesis + Sources section), NOT interspliced through the body. Per M's 2026-05-20 feedback #9: deliverable links land at the bottom or they get lost.

2. **`mcp__cowork__present_files` is OPTIONAL (reveal-in-folder convenience only).** Pre-v3.13.0 this was the primary opener; M's 2026-05-20 testing surfaced that the cards' primary-click DOESN'T open most file types — only "Show in Folder" works. So `present_files` is no longer the opener. Include it if and only if the user is likely to want to navigate the filesystem to find the brief (rare for call-prep). Default: skip the `present_files` call entirely for this skill.

3. The brief is a `.docx` (not `.md`) — `brief_writer.make_brief` produces a polished Word document with the canonical Command Room typography per v2.14.32.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "First time prepping you. I made 2 calls: standard depth · auto_fire=24h"
- Good: "First time prepping you. I set 2 defaults: standard depth · auto-prep 24 hours before each meeting"

If the meeting has `related_thread_ids[]`, add a cross-ref line at the top of each related project's session notes pointing to the brief's `_hq/meetings/` path (matches the cross-ref convention from `meeting-notes` Step 4).

**Session Notes convention:** Session notes live at `[WORKSPACE_ROOT]/[Project Name]/SESSION_NOTES_[NAME].md` where `[NAME]` is the user's first name (set during onboarding). For example, if the user is Pat and the project is Northstar, session notes are at `Northstar/SESSION_NOTES_Pat.md`. Briefs do NOT save next to session notes — briefs live in `_hq/meetings/` per the canonical path above.

## How to Use

```
"Prep me for my 2pm meeting"
"Prep me for the call with [Company/Person]"
"Get me ready for the [Project Name] kickoff"
"Meeting brief for [Person's] 1:1"
"What do I need to know for the Board meeting?"
```

The skill automatically finds the meeting and builds the brief.

## Executive Output Standard (EXEC1, v3.20.0+) — INVERT THE DOCUMENT

Call-prep was the headline violator: it opened with Meeting Details (which the CEO already knows) and buried "Suggested Outcome" + "Decisions Needed" at the bottom of 2-4 pages. Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`, the document is INVERTED. **Zero new computation — this is pure reordering.** Pass `make_brief(brief_kind="call_prep", ...)` an `exec_header`:

- **verdict** = the current **Suggested Outcome** / **Walk-out** ("Walk out with: Apr 30 cutover confirmed"). The standalone "Suggested Outcome" / "Walk-out" section at the bottom is SUBSUMED — it becomes the verdict at the top, not a duplicate block at the end (no-duplication + net-length rules).
- **DECIDE** = the lead of **Decisions Needed**.
- **CHANGED** = the lead of **Changed Since Last Touch** (or "Nothing new since last touch." when nothing moved — `assemble_prep_sections` fills the nothing-form automatically).
- **NEEDED** = the single reader-action, if any (most call-prep briefs have none → "Nothing from you.").
- **Meeting Details drops to section 2** — directly under the exec header.

The full section content is unchanged; only the ORDER flips and the lead moves to the top. The per-section content floors below still apply. **Sync rule (same commit): the section ORDER change is mirrored in `orchestrator-upcoming-meetings.md` Phase 4 section list.**

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("call_prep", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: visual placement and proportions (for call-prep, `assemble_prep_sections` owns the section list — the exemplar anchors layout within it, never against it). Workspace exemplar (`_hq/exemplars/call_prep/`) beats the shipped seed; `None` = compose on the defaults above, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the brief. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered brief ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="call_prep", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

## What You Get

The brief is structured around the **five blocks** (v4.5.2 S1, the FINDINGS F-60 PROPOSAL): ① walk-out-with ② changed since last touch ③ decide ④ owed both directions ⑤ sourced talking points. `prep_pipeline.assemble_prep_sections` owns the section order — both prep paths call it. Every section is required (omit only when no signal exists for it; never pad with placeholder text). **Order (EXEC1-inverted): the exec header is first.**

> **Sync rule (v3.11.1+ / v4.5.2):** If you add, rename, or reorder any section below, update `prep_pipeline.assemble_prep_sections` AND [`skills/enable-command-room-schedules/references/orchestrator-upcoming-meetings.md`](../enable-command-room-schedules/references/orchestrator-upcoming-meetings.md) Phase 4 **in the same commit**. Pre-v3.6.4 these drifted silently; the upcoming-meetings orchestrator dropped any content from a section it didn't know about.

- **Exec header (EXEC1 — first block; block ①):** verdict (= the walk-out objective, one sentence, the concrete win) + CHANGED / DECIDE / NEEDED leads. Replaces the bottom-of-document "Suggested Outcome" as the lead.
- **At a Glance (visual layer, v4.5.2 S1):** stat-tile band — days since last touch · you-owe / owed-to-you counts with oldest age · engagement touch #. Built by `prep_pipeline.build_prep_tiles` from substrate only; **a tile with no data is DROPPED, never rendered empty** (the band is omitted entirely when nothing is known).
- **Meeting Details:** Who, what, when, where, duration, project routing
- **Relationship Timeline (visual layer, v4.5.2 S1; durable source per SPEC HIST1 D7):** compact strip of meetings + key touches since engagement start, current meeting marked. Source the points from the durable per-person compiler — `render_person_history.person_timeline_points(workspace_root, person_id)` (one derivation, every surface; it already dedups, humanizes, and caps) — and feed them straight into `prep_pipeline.build_relationship_timeline` (which appends the current-meeting marker; still dropped below 2 points). Never re-derive timeline points from a raw events scan here. The same compiler's lineage lines + recorded facts feed the per-attendee Relationship Context block ("prefers Signal", a recent role move) — substrate-derived only.
- **Relationship Context:** Per-attendee — how you know them, last interaction date, top 3 things they care about, what they've delivered / asked for recently, any open commitments to/from them
- **Where We Left Off:** Last meeting summary + what was decided / committed; pulled from Granola transcript if available
- **Changed Since Last Touch (block ②; replaces the prior-brief-gated "Since Your Last Brief"):** what moved since the last meeting with these attendees — project events, reschedules, AND **overnight Gmail scoped to attendee addresses** (cost-bounded: one search per meeting, cap 10 threads). Present whenever there is any delta signal, prior brief or not — the F-60 dogfood brief missed an attendee's overnight deliverables because the old section only fired off a prior brief file. 3-6 bullets, each one fact with its source + date.
- **Progress Since You Last Met:** What's been done across this project since you last met (events.jsonl, recent SESSION_NOTES additions, deliverables)
- **Open Items & Blockers:** What's stuck, who owns what, why it's stuck if known
- **Owed — Both Directions (block ④; replaces "Commitments Tracker"):** a **two-column table** (You owe | Owed to you) built by `prep_pipeline.build_owed_table` from `commitment_state.match_commitments_to_meetings` — matched by counterparty id OR name-mention in the item's own text, **undated items included** ("no date set" renders plainly; a missing due date must not hide an item on the day of the meeting — F-44's fix carried into prep). **INTAKE2 (2026-08-01) — the table renders the CONFIRMED half only.** The matcher deliberately applies no `pending_review` filter (F-44: a missing due date must not hide a relevant item), so filtering is the caller's job: split the matched rows with `cru_match.split_pending_review(...)` and pass only the confirmed half to `build_owed_table`. Pending rows are UNCONFIRMED extractions — nobody agreed they are real work — and walking into the room believing one is exactly the failure this block exists to prevent. **The pre-INTAKE affordance — a pending row rendered as a table cell with its own inline confirm nudge — is RETIRED as a contract**: a second review surface inside a prep brief contradicts the one-list doctrine, and the needs-your-call queue is THE review surface. Note precisely what that means at the code layer: `prep_pipeline._owed_cell` still carries the branch that decorates a pending cell, and it stays there — it simply becomes unreachable once callers pass the confirmed half, because no pending row ever reaches it. Do not read the surviving branch as permission to pass one. When attendees have pending rows, the block carries ONE labelled pointer line beneath the table, mirroring the in-code precedent in `surface_drivers` / `render_master_tracker`: *"N unconfirmed extraction(s) waiting — say `needs your call` to clear them. They are not counted in the table above."* Drop the line at zero; never pad it. Followed by **Parked to Discuss** — the RETIRED discuss-later list (`commitment_to_discuss`) filtered to these attendees. MLK1 (2026-07-21): drain-only — no capture path writes new items, but the block keeps rendering attendee-matched OPEN items until the backlog drains (deleting the reader would strand the live parked intentions invisibly). Omit the block when nothing matches; it disappears naturally once the list is empty.
- **Talking Points (block ⑤):** Key topics to cover — 4-7 specific items with one-line framing for each. **Every line ends with a source cite** (`(email, Jul 7)` / `(meeting, Jun 30)` / `(commitment, May 22)` / `(sweep, Jul 7)`) — `assemble_prep_sections` REJECTS unsourced lines; no ungrounded filler survives to the page. **When 2+ external attendees are on the call, prefix each item with the target attendee** (e.g., `→ Bo: Push on NetSuite cutover date — Apr 30 was soft (email, Apr 22)`). Solo external attendee = no prefix needed.
- **Questions to Ask (block ⑤):** 3-5 specific questions tied to actual blockers, not generic "how's it going" filler. Same source-cite requirement as Talking Points. **When 2+ external attendees, prefix each question with the target attendee** (same convention as Talking Points).
- **Decisions Already On The Record:** Decisions previously logged for this project (from `decision-log` / `events.jsonl` `decision` events) — the "don't relitigate" block. 2-5 bullets max, each one line: decision + date. Skip if no priors exist.
- **Decisions Needed (block ③ — DECIDE):** the open decisions this meeting is positioned to close, from the decision log for this project, with the tradeoffs of each direction
- **Cross-Project Insights:** Patterns from related projects that might bear on this conversation
- **Risks / Watch-outs:** Anything that could derail the call (recent friction, unresolved disagreements, sensitive topics)
- **Suggested Outcome:** *(EXEC1 — now rendered as the exec-header VERDICT at the top, not a separate bottom block)* What "good" looks like for this call in one sentence

**Learned section weights (Phase 6 Loop 3).** Before rendering, consult the section weights insight-generator's Pass 15 learned from prep-vs-transcript grading, stored in this skill's config: `from prep_grading import section_weight` → for each gradable section (Talking Points / Risks — Watch-outs / Questions to Ask / Decisions Needed), if `section_weight(cfg, <meeting_type>, <section>) == 0`, DROP that section for this meeting-type (it's been rendered-but-empty in that context — the CEO approved dropping it). A missing weight defaults to 1.0 (render normally), so a fresh workspace produces every section exactly as before. This never drops a section that has real signal for THIS meeting — it only suppresses one the CEO agreed is dead weight for this meeting-type. `cfg = get_config(workspace_root, "call-prep", DEFAULTS)`; `section_weights` is a learned key populated only by Pass 15 (never asked in the first-run questionnaire).

## Brief Format — required content depth (v2.10.1+)

The brief is a **deliverable, not a stub**. It runs 2-4 pages, 800-1500 words. The .docx is what M opens 5 minutes before the meeting and reads top-to-bottom. Skinny briefs are a fail.

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md` § "Universal writing standards (all composer skills)" (structure, specificity, floors — they do not override this skill's voice).

**Substantivity test — run on EVERY claim before returning the brief:** "Could M act on this in the meeting?"
- FAILS: "Sam is the decision-maker." / "Acme is a freight company."
- PASSES: "Sam approves tech buys solo under $50K — your price is $45K, but last week Rio asked about ROI and Sam seemed surprised Rio cared. Confirm the approval path is still Sam-only."

A claim that only states a fact, with no action it unlocks, fails — cut it or make it actionable.

Per-section content floors (count, then fix — a floor without a count is not a floor):

- **Meeting Details** — full block: title, time, duration, location/link, all attendees with roles, project routing
- **Relationship Context** — one paragraph per attendee (3-6 sentences each). Not a one-liner. Include how user and they last interacted (date + topic), what they care about, what they've been pushing on, any open ask in either direction. If multiple attendees, each gets their own block.
- **Where We Left Off** — at least 1 paragraph (4-8 sentences). If a Granola transcript exists for the last meeting, pull 2-3 DIRECT quotes from it — zero quotes when a transcript is available = failed floor; rewrite. If only email history, summarize the last 2-3 thread movements.
- **Changed Since Last Touch** — bullet list, 3-6 items. Delta = everything since the last meeting with these attendees: project events, calendar reschedules, and overnight Gmail scoped to attendee addresses (cost-bounded — one search per meeting, cap 10 threads). Each bullet is one fact WITH its source + date: new email subject + date, new commitment + owner, new decision + date, new transcript reference. **Tone:** "Here's what's new since last time you walked into this room" — not a restatement of the relationship. Omit only when genuinely nothing moved (then the exec header CHANGED line says so).
- **Progress Since You Last Met** — bullet list, 3-8 items minimum. Tied to specific events / dates / deliverables. "Sent revised pricing model on Apr 18" not "made progress."
- **Open Items & Blockers** — bullet list, every open item from SESSION_NOTES + events.jsonl commitments tied to this project, with owner + aging.
- **Owed — Both Directions** — the two-column table (You owe | Owed to you) from `prep_pipeline.build_owed_table`. Every matched CONFIRMED open commitment, original phrasing + due date + aging; undated rows render "no date set" (never blank, never dropped). Unconfirmed extractions are split out with `cru_match.split_pending_review(...)` before the call and never appear as rows (INTAKE2) — they get the single `needs your call` pointer line described in block ④ above, or nothing. Omit the section only when the matcher returns nothing confirmed in either direction — never render an empty table frame.
- **Talking Points** — 4-7 items. Each item is a one-sentence frame with the tension named, never a bare topic, **ending with its source cite** (`(email, Jul 7)` / `(meeting, Jun 30)` — enforced by `assemble_prep_sections`, which raises `PrepContractError` on an unsourced line; ground the line or cut it). "Pricing tiers" fails; "Push on net-30 — they asked for a discount last call (meeting, Jun 12)" passes. **Multi-attendee prefix rule:** if the meeting has 2+ external attendees, prefix every item with `→ <FirstName>:` so the user can tell at a glance who to push each point with. Single external attendee = no prefix.
- **Questions to Ask** — 3-5 specific questions, each rooted in a known blocker or prior statement, **same source-cite requirement**. Generic questions are banned — "How's the project going?" / "anything we should be aware of?" fail. Tie to actual context ("What did Bo land on for the NetSuite mapping — is the Apr 30 cutover still real? (email, Apr 22)"). **Multi-attendee prefix rule:** same as Talking Points — `→ <FirstName>:` prefix when 2+ external attendees.
- **Decisions Already On The Record** — bullet list, 2-5 items max. Pull from events.jsonl `decision` events tied to this project. Format: `<decision in one line> — <date>`. Skip if no priors exist; never pad. Purpose: stop the user from re-litigating something already settled.
- **Decisions Needed** — list each decision with the options + tradeoff in one sentence per option. If no decisions need to land, omit the section.
- **Cross-Project Insights** — at least 1 if any pattern is detectable across other active projects in this org. Omit if nothing connects.
- **Risks / Watch-outs** — at least 1 if any sensitive thread or recent friction exists. Pull from `cracks_watch_feedback` events or any `decision_pending` events tied to this project. Omit if everything is clean.
- **Suggested Outcome** — one sentence. What does success look like for this call?

**Hard rule:** if a section has no signal, omit it entirely — don't write "TBD" or "no information available." Empty placeholders are worse than missing sections; they signal the brief was generated lazily.

### Output-contract gate (B3 — pre-save, before the voice gate)

`make_brief` validates the structured `sections` against `shared/scripts/output_contract_validator.py` `RULES_BY_KIND["call_prep"]` BEFORE `Document()` is built (canonical order: contract → voice → render → leak scan). It checks the per-section floors above (Talking Points 4-7, Questions to Ask 3-5, Progress Since You Last Met 3-8, Decisions Already On The Record 2-5, Relationship Context ≥3 sentences/paragraph, Where We Left Off ≥4 sentences) and the no-placeholder rule — but **only on sections that are present** (omit-don't-pad means absent sections are never demanded; only Meeting Details is unconditional).

**Rewrite-on-failure loop:** if `make_brief` raises `OutputContractError`, read each violation's `section` + `fix_hint`, rewrite ONLY the failing sections — expand thin ones with real substrate signal (never filler), or remove placeholder text / omit the section — and call `make_brief` again. Maximum 2 retries, then surface the failure plainly to the user instead of shipping a substandard brief.

**Client safety:** the 800-1500 total-word floor is REPORT severity (warn) — it never blocks a save. A young or sparse client workspace genuinely can't reach 800 words; expand with real signal, or pass `contract="report"` and tell the user the brief is thin because the workspace lacks signal. Never pad. For an internal-only meeting, pass `contract_profile="call_prep_internal"` (relaxes the floor to 500-1500 and uses the internal section list below).

**Sync rule:** if you change any count/floor in this section, change the matching entry in `output_contract_validator.py` `RULES_BY_KIND["call_prep"]` (and `PROFILE_RULES["call_prep_internal"]`) in the same commit.

### Internal-meeting variant (v3.6.3+)

When ALL non-user attendees share the user's primary domain (internal-only meeting per orchestrator-upcoming-meetings.md Phase 3), the section template changes. The external prep sections (Relationship Context, Cross-Project Insights, full attendee bios) are dropped — the user knows their teammates. The brief becomes project-context prep instead.

Internal-only section list, in order:

- **At a Glance** — same stat-tile band + drop rule as external (v4.5.2 S1)
- **Meeting Details** — same as external
- **Relationship Timeline** — same as external (dropped below 2 points)
- **Where We Left Off** — last 1:1 or sync with this internal attendee on this project (Granola transcript + last commitments exchanged)
- **Changed Since Last Touch** — same delta logic as external (project events + reschedules + overnight attendee-scoped Gmail since the last touch)
- **Project events since last meeting** — what's moved on the project itself in the last 14 days (events.jsonl scan, filtered to this project's `primary_thread_id`)
- **Owed — Both Directions** — the same two-column table as external, scoped to what user owes the attendee + what the attendee owes user, with aging. This is the core of internal 1:1 prep — interpersonal, not project-wide. Plus **Parked to Discuss** for this attendee (drain-only since MLK1 — renders open leftovers until the retired list empties).
- **Decisions stuck** — decisions logged as `decision_pending` for this project where this attendee is owner or blocker
- **Decisions Already On The Record** — same as external (don't relitigate)
- **What to drive** — talking points, sourced-line rule + multi-attendee prefix rule still apply if 2+ internal attendees
- **Walk-out** — what the user wants decided / committed before leaving the room (one sentence — rendered as `exec_header.verdict`, same as the external walk-out)

Dropped vs external template: Relationship Context, Progress Since You Last Met (folded into "Project events since last meeting"), Questions to Ask (collapsed into "What to drive" — questions ARE the talking points in internal 1:1s), Cross-Project Insights, Risks / Watch-outs.

The internal variant exists because orchestrator v2.14.36+ surfaces internal meetings for prep, but the external section template doesn't fit them. M's 2026-05-07 feedback: *"if there are no external participants it is not prepping me — it should pull context if possible."* Now: it does, with a section list that matches what internal prep actually needs.

**Voice:** match the user's tone — direct, executive, specific, no filler. Voice comes from `shared/VOICE_CALIBRATION.md` plus the workspace's calibrated override at `_hq/voice/voice-block-call-prep.md` if present (the override supersedes section-by-section).

**Provenance (v2.14.32+ correction):** every claim that came from a connector should be traceable through events.jsonl (the `connector_read` events written silently per `shared/PASSIVE_CAPTURE.md`). The brief .docx itself does NOT carry a provenance footer — `shared/scripts/brief_writer.py` hard-codes the footer to `Command Room` per the v2.12.4+ forwardable-clean rule. Pre-v2.14.32 some briefs leaked `Source: ... | Fired: ... | Inputs: ... | TTL: ...` into the document body; that footer pattern is dead. Provenance lives in events.jsonl, not in the shareable doc.

## Triggers

- "prep me for"
- "prep call"
- "meeting prep"
- "brief me for"
- "get me ready for"
- "what do I need to know for"

## Connected Tools

- **Google Calendar** — Find the meeting, extract attendees and agenda
- **Gmail** — Search for recent email threads with attendees
- **Slack** — Pull recent messages about the project or people
- **Granola** — Retrieve notes from past meetings with these people
- **entities.json** — Canonical relationship context on attendees (how you know them, last interaction, key notes) per `shared/PASSIVE_CAPTURE.md`. Legacy `_hq/PEOPLE.md` is read as a fallback if present.
- **events.jsonl** — Open commitments, prior decisions, project events. Legacy `_hq/MASTER_TRACKER.md` is read as a fallback if present.
- **Prior briefs + prep receipts** — `prep_pipeline.find_existing_prep_brief` (by meeting id) for refresh-in-place; `prep_brief` receipts in events.jsonl are the existence signal other surfaces read (never glob filenames to answer "was this meeting prepped?")
- **Session Notes** — Pull project context and history
- **PROJECT_CONTEXT** — Extract relevant background

## Gotchas

- **If a connector isn't available, skip it silently.** Don't say "Gmail is not connected" — just build the brief from whatever sources ARE available. If no connectors are connected at all, build the brief entirely from local sources — canonical first (`entities.json`, `events.jsonl`), then SESSION_NOTES + PROJECT_CONTEXT, with legacy MASTER_TRACKER / PEOPLE.md as last-resort fallbacks. The brief will be less rich but still useful.
- If the meeting isn't on your calendar, use the person/company name — the skill will search Gmail/Slack to find context
- If there's no past meeting in Granola, the skill fills that gap with email history
- The brief is saved for reference, but you can also ask "what did we discuss last time with [Person]?" to search Granola directly
- If multiple meetings exist at that time, the skill asks which one you mean
- Slack search works best if the project or people are mentioned directly in channel names or messages

## Substrate-sync (v3.14.6+ — per `shared/INGEST_SUBSTRATE_SYNC.md`)

Call-prep pulls past-meeting Granola transcripts on-demand to build "Where We
Left Off." When it fetches a transcript that has NEVER been processed (no
`meeting` event in `events.jsonl` carrying its `granola:<id>` `source_ref`), it
MUST reconcile that transcript's entities into substrate before producing the
brief — invoke `meeting-notes` on it (data layer only: `meeting` + commitments +
decisions + new people via `people_writer` dedup; no second brief, no drafts).
Idempotent by `source_ref`. This stops the gap where prepping for a call silently
reads a prior unprocessed meeting whose attendees/commitments never got captured.
Not exempt: reading a transcript for prep still has to capture what's in it.

## What It Doesn't Do

- It doesn't attend the meeting or take notes (use the meeting-notes skill for that)
- It doesn't update MASTER_TRACKER (that's a separate operation)
- It doesn't create calendar events or send invites
- It doesn't generate a second meeting brief during the Step-above reconcile pass — that pass writes the data layer only, not deliverables

## Next Steps

After the call:
- Use **meeting-notes** to process the output and capture decisions
- Use **decision-log** to log any decisions made
- Use **people-crm** to update relationship context

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Walk into a specific meeting already prepped. Synthesizes calendar, Gmail, Slack, Granola transcripts, session notes, and open commitments for every attendee into one scannable brief. Use when the CEO says 'prep me for', 'prep me for my 2pm', 'prep me for the call', 'prep me for the Acme call', 'prep the call', 'prep the call with', 'get me ready for', 'what do I need to know for the board meeting', 'prep me for the board meeting', 'prep call', 'meeting prep', 'prep for my 3pm', 'prep for my 2pm'. Produces a structured brief saved to `_hq/meetings/` and surfaced as a clickable link, ready to review in the 5 minutes before the meeting starts. Also handles first-run personalization settings — use when the CEO says 'tune call prep', 'tune call-prep', 'show call prep settings', 'show call-prep settings', 'reset call prep to defaults', 'reset call-prep to defaults'. DOES NOT fire on 'brief me on today' (that's morning-briefing — all meetings, summary only), 'process the call' (that's meeting-notes — post-meeting), 'prep me for dinner' (that's people-crm). DOES NOT fire on 'prep me for my 1:1' / 'prep for my 1:1' with a direct report (team-intelligence — it owns internal 1:1s; this skill owns external-attendee meetings). DOES NOT fire on 'prep me to speak', 'prep me for the keynote', 'help me prepare for the keynote' — speaking-engagement prep is out of scope in this plugin (v3.9.0+); use memo-writer with memo_type=position_paper or memo_type=board_update for talking-point drafts.
