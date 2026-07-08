---
name: call-prep
description: "Walk into a specific meeting already prepped. Fires on: 'prep me for my 2pm' (any time), 'prep me for the [name] call', 'quick prep me for my 3pm', 'prep the call', 'prep me for the board meeting', 'get me ready for [meeting]', '1:1 brief for [name]', 'meeting prep', plus 'tune call-prep' and 'show call-prep settings'. Synthesizes calendar, email, Slack, meeting transcripts, and open commitments for every attendee into one scannable brief saved to the meetings folder and linked in chat — with learned per-meeting-type section weights applied. Does NOT fire on 'brief me on today' (morning-briefing — all meetings, summary only), 'process the call' (meeting-notes — post-meeting), 'prep for 1:1 with [direct report]' (team-intelligence), or speaking-engagement prep (memo-writer position paper). Full trigger list and section spec: Routing section in the body."
---

## Deliverable Render Gate (GATE1 — MUST, v3.20.x)

This skill produces a `.docx` brief deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="call_prep", ...)`** (see the required call sequence below). That single call runs the output-contract gate (B3 — per-section depth floors), the voice-tell gate (B2), and the post-render leak scan, in that order, BEFORE the file is written. The brief is also where the forwardable-clean / no-provenance rules are enforced.
- **NEVER hand-roll a `.docx`** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship substandard or PII-leaking briefs (the v3.20.0 failure mode).
- **NEVER answer a prep request with a chat-only brief.** "Quick prep" is still a call-prep request — produce the `.docx` through `make_brief`. A short in-chat heads-up summary may accompany the file, but the file is the deliverable.
- **Detectability:** `make_brief` emits a `gate_ran` audit event recording which gates ran. A call-prep fire that yields a brief with NO `gate_ran` event for that turn is a flagged bypass. Pass `workspace_root` to `make_brief` so the event lands in substrate.

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
9. **Checks `_hq/meetings/` for the prior `Call_Prep_<slug>_*.docx`** — if one exists, the brief is delta-aware (only surfaces what's new since the last brief was generated, not the full relationship history again)
10. Generates a structured brief with all the context you need

### Brief save path (canonical — v2.12.6+, v2.14.32+ writer)

The brief saves to `_hq/meetings/` under the canonical filename produced by `shared/scripts/brief_path.get_brief_path()`. Do not hand-roll paths in this skill. Per `shared/CONTRACT.md` Rule 3, the prior `[Project]/meetings/` location (v2.10.8 - v2.12.5) didn't always resolve in Cowork's sandbox — users hit "folder cannot be found" on click.

**Required call sequence (mirrors `orchestrator-upcoming-meetings.md` Phase 4 step 3):**

```python
# Add shared/scripts to path (canonical preamble — same as orchestrators)
import sys
from pathlib import Path
SCRIPTS = Path(PLUGIN_ROOT) / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from brief_path import get_brief_path, get_brief_artifact_url, ensure_brief_directory
from brief_writer import make_brief

# 1. Compute canonical path. `slug` is a short readable identifier
#    (e.g. "sam-q3-review" or "acme-kickoff"); `date_iso` is YYYY-MM-DD.
ensure_brief_directory(workspace_root)
brief_path = get_brief_path(workspace_root, "call_prep", slug, date_iso)

# 2. Hand the structured brief data to brief_writer.py via JSON-via-stdin.
#    Section ordering is canonical; brief_writer enforces typography + footer.
make_brief(brief_kind="call_prep", title=..., sections=[...], output_path=brief_path)

# 3. Surface the brief as a clickable link in chat. NEVER as a plain path string
#    or "saved to ..." narration.
artifact_url = get_brief_artifact_url(brief_path)  # native computer:// per v3.13.0+
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
- **CHANGED** = the lead of **Since Your Last Brief** (or "Nothing new since [prior brief date]" / the nothing-form when there's no prior brief or no delta).
- **NEEDED** = the single reader-action, if any (most call-prep briefs have none → "Nothing from you.").
- **Meeting Details drops to section 2** — directly under the exec header.

The full section content is unchanged; only the ORDER flips and the lead moves to the top. The per-section content floors below still apply. **Sync rule (same commit): the section ORDER change is mirrored in `orchestrator-upcoming-meetings.md` Phase 4 section list.**

## What You Get

The brief is structured. Every section is required (omit only when no signal exists for it; never pad with placeholder text). **Order (EXEC1-inverted): the exec header is first, Meeting Details second.**

> **Sync rule (v3.11.1+):** If you add, rename, or reorder any section below, update [`skills/enable-command-room-schedules/references/orchestrator-upcoming-meetings.md`](../enable-command-room-schedules/references/orchestrator-upcoming-meetings.md) Phase 4 sections list **in the same commit**. Pre-v3.6.4 these drifted silently; the upcoming-meetings orchestrator dropped any content from a section it didn't know about.

- **Exec header (EXEC1 — first block):** verdict (= the walk-out objective) + CHANGED / DECIDE / NEEDED. Replaces the bottom-of-document "Suggested Outcome" as the lead.
- **Meeting Details:** Who, what, when, where, duration, project routing *(now section 2, under the exec header)*
- **Relationship Context:** Per-attendee — how you know them, last interaction date, top 3 things they care about, what they've delivered / asked for recently, any open commitments to/from them
- **Where We Left Off:** Last meeting summary + what was decided / committed; pulled from Granola transcript if available
- **Since Your Last Brief:** *(only present if a prior `Call_Prep_<slug>_*.docx` exists in `_hq/meetings/`)* — delta-only summary of what changed since that brief was generated: new emails, new commitments logged, new decisions, new transcript events. Surfaces in 3-6 bullets. Skip if no prior brief or no delta.
- **Progress Since You Last Met:** What's been done across this project since you last met (events.jsonl, recent SESSION_NOTES additions, deliverables)
- **Open Items & Blockers:** What's stuck, who owns what, why it's stuck if known
- **Commitments Tracker:** What you owe, what they owe, with aging
- **Talking Points:** Key topics to cover — 4-7 specific items with one-line framing for each. **When 2+ external attendees are on the call, prefix each item with the target attendee** (e.g., `→ Bo: Push on NetSuite cutover date — Apr 30 was soft`). Solo external attendee = no prefix needed.
- **Questions to Ask:** 3-5 specific questions tied to actual blockers, not generic "how's it going" filler. **When 2+ external attendees, prefix each question with the target attendee** (same convention as Talking Points).
- **Decisions Already On The Record:** Decisions previously logged for this project (from `decision-log` / `events.jsonl` `decision` events) — the "don't relitigate" block. 2-5 bullets max, each one line: decision + date. Skip if no priors exist.
- **Decisions Needed:** Choices that have to land in this call, with the tradeoffs of each direction
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
- **Since Your Last Brief** — bullet list, 3-6 items. Only present if a prior `Call_Prep_<slug>_*.docx` exists in `_hq/meetings/`. Compute delta = events for this project/attendee since the prior brief's date. Each bullet is one fact: new email subject + date, new commitment + owner, new decision + date, new transcript reference. **Tone:** "Here's what's new since last time you walked into this room" — not a restatement of the relationship. If the prior brief is <48h old and no new events landed, omit the section.
- **Progress Since You Last Met** — bullet list, 3-8 items minimum. Tied to specific events / dates / deliverables. "Sent revised pricing model on Apr 18" not "made progress."
- **Open Items & Blockers** — bullet list, every open item from SESSION_NOTES + events.jsonl commitments tied to this project, with owner + aging.
- **Commitments Tracker** — split into "You owe" and "They owe" sub-sections. Every open commitment, with original phrasing + due date + aging.
- **Talking Points** — 4-7 items. Each item is a one-sentence frame with the tension named, never a bare topic. "Pricing tiers" fails; "Push on net-30 — they asked for a discount last call" passes. ("Push on the Q3 launch date — they were soft last time but the marketing team is now blocked on it" is the shape.) **Multi-attendee prefix rule:** if the meeting has 2+ external attendees, prefix every item with `→ <FirstName>:` so the user can tell at a glance who to push each point with. Single external attendee = no prefix.
- **Questions to Ask** — 3-5 specific questions, each rooted in a known blocker or prior statement. Generic questions are banned — "How's the project going?" / "anything we should be aware of?" fail. Tie to actual context ("What did Bo land on for the NetSuite mapping — is the Apr 30 cutover still real?"). **Multi-attendee prefix rule:** same as Talking Points — `→ <FirstName>:` prefix when 2+ external attendees.
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

- **Meeting Details** — same as external
- **Where We Left Off** — last 1:1 or sync with this internal attendee on this project (Granola transcript + last commitments exchanged)
- **Since Your Last Brief** — same delta logic; pulls events since the prior internal brief if one exists
- **Project events since last meeting** — what's moved on the project itself in the last 14 days (events.jsonl scan, filtered to this project's `primary_thread_id`)
- **Open items between you** — commitments BOTH directions: what user owes the attendee + what the attendee owes user, with aging. This is the core of internal 1:1 prep — different from the external "Commitments Tracker" because the focus is interpersonal, not project-wide.
- **Decisions stuck** — decisions logged as `decision_pending` for this project where this attendee is owner or blocker
- **Decisions Already On The Record** — same as external (don't relitigate)
- **What to drive** — talking points, multi-attendee prefix rule still applies if 2+ internal attendees
- **Walk-out** — what the user wants decided / committed before leaving the room (one sentence, replaces "Suggested Outcome" framing for internal context)

Dropped vs external template: Relationship Context, Progress Since You Last Met (folded into "Project events since last meeting"), Commitments Tracker (replaced by "Open items between you"), Questions to Ask (collapsed into "What to drive" — questions ARE the talking points in internal 1:1s), Cross-Project Insights, Risks / Watch-outs.

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
- **Prior briefs** — `_hq/meetings/Call_Prep_<slug>_*.docx` for the delta-aware "Since Your Last Brief" section
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
