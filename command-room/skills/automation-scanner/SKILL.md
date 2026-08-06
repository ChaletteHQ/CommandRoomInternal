---
name: automation-scanner
surfaces: both
description: "Find the low-hanging automation wins hiding in the CEO's own workspace and rank by time-saved-vs-build-effort. Use when the CEO says 'what can be automated', 'automation scan', 'automation audit', 'show me automations', 'where am I wasting time', 'where am I leaking time', 'hidden time cost', 'what should I automate first'. Produces a ranked list with scope notes and suggested build approach (the recurring-meeting 'hidden time-cost' report is coach deliverable-catalog 2.5, rendered by this scan). DOES NOT fire on 'set up [specific tool]' (that's implementation, not scanning) or 'build me a [workflow]' (that's a design/build request — this skill identifies opportunities, doesn't build them)."
---

## Skill Boundary (v2.1)

- **Use automation-scanner for:** surveying the CEO's current workflows and surfacing ranked automation opportunities. Output is advisory — a prioritized list.
- **Does NOT build automations** — it identifies them. Once the CEO picks one, a specialized skill or custom build handles implementation.

## Writer Contract (v3.7.1+ — substrate-aware)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Primary writer for:**
- `_hq/audit-reports/automation-scan-[YYYY-MM-DD].docx` — ranked automation-opportunity report. Per CONTRACT Rule 27 (no .md deliverables); the report is `.docx` so it renders correctly in Word / Pages when the user opens it.

**Appends to:**
- `_hq/data/events.jsonl` — one `automation_opportunity_surfaced` event per ranked opportunity, with `{title, scope_summary, current_pattern_summary, estimated_time_saved_minutes_per_week, suggested_build_approach, rank, scan_report_path}`. Emitting per opportunity (not one event per scan) lets `scaffold-automation` reference a specific opportunity by `seq`, and lets `cleanup` track surfaced-but-not-scaffolded items over time ("you've surfaced 8 opportunities this quarter, scaffolded 2, deployed 1 — what's blocking the rest?").

**Reads from:**
- Session notes, meeting transcripts (the prose corpus for repetitive-work pattern detection).
- `_hq/data/events.jsonl` — for recurring patterns AND for prior `automation_opportunity_surfaced` events (so the same opportunity doesn't get re-surfaced every scan; dedup by `title`-similarity + scan-window comparison).
- `_hq/data/events.jsonl` `automation_deployed` events — so opportunities already shipped via `scaffold-automation` get filtered out of the next scan, not re-ranked.
- Local workspace file structure.

**Conflict boundary:** sole writer of `automation_opportunity_surfaced` events. The paired `scaffold-automation` skill (v3.8.0) is the sole writer of `automation_scaffolded` and `automation_deployed`.

**Why the upgrade (v3.7.1 note):** pre-v3.7.1 this skill was advisory-only — the scan produced a `.docx` report and nothing else. There was no way to know which opportunities had been scanned, which were scaffolded, and which were sitting on the floor. The per-opportunity event closes the loop and prepares the substrate for the v3.8.0 `scaffold-automation` pairing.

**Also reads (SPEC OUT2 §5):** `_hq/data/skill_config/automation-scanner.json` — first-run knobs, via `skill_config_writer.get_config` (see First-Run Personalization below).

**Also writes (SPEC OUT2 §5):** `_hq/data/skill_config/automation-scanner.json` on first fire, tune, and reset — always via `skill_config_writer` (`save_skill_config` / `wipe_skill_config`), never a raw file write.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — knobs only; this skill deliberately takes no SCL1
customization layer (the knobs suffice). Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22). Bash preamble:
# SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "ranking_weights": "balanced",                # balanced (today's formula) | favor_time_saved | favor_quick_builds
    "horizon_buckets": ["quick_wins", "medium"],  # buckets rendered by default (today: long_term omitted unless asked)
}
cfg = get_config(workspace_root, "automation-scanner", DEFAULTS)
```

- `ranking_weights` biases the ORDER, never the arithmetic (the `rank_score` formula below always
  computes and always renders so the CEO can challenge the inputs): `balanced` sorts by `rank_score`
  (today); `favor_time_saved` sorts by hours-saved-per-year first (rank_score as tiebreak — big wins
  float even when the build is chunky); `favor_quick_builds` sorts by build-hours ascending first
  (momentum bias — smallest builds float).
- `horizon_buckets` sets which sections render: any subset of `quick_wins` / `medium` / `long_term`.
  Default `["quick_wins", "medium"]` is today's behavior (long-term omitted unless the CEO asks);
  adding `long_term` renders the long-term section every scan.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "automation scan" | scan with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "automation-scanner", DEFAULTS)` BEFORE rendering, then append the first-run footer after the report link. |
| **Show settings** | "show automation-scanner settings" | render current config in plain English; no scan. |
| **Tune** | "tune automation-scanner" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)`. |
| **Reset** | "reset automation-scanner to defaults" | `wipe_skill_config(workspace_root, "automation-scanner")` → next fire is a first-fire again. |

**The first-run block (footer — after the report link):**

> *First time scanning for automations. I set 2 defaults: **ranked by payback (time saved against
> build effort)** · **quick wins and medium builds shown, long-term ideas on request**. Say **"tune
> automation-scanner"** to change either, or just tell me ("show me the long-term ideas too" /
> "smallest builds first").*

The footer renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "biggest time savings first" | `ranking_weights = favor_time_saved` |
| "smallest builds first" / "easiest wins first" | `ranking_weights = favor_quick_builds` |
| "back to balanced ranking" | `ranking_weights = balanced` |
| "show me the long-term ideas too" | add `long_term` to `horizon_buckets` |
| "just the quick wins" | `horizon_buckets = ["quick_wins"]` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line.

---

# Automation Opportunity Scanner

**For:** CEOs who want to identify the low-hanging fruit for building internal tools and workflows that save time.

## What It Does

Scans your workspace systematically to find patterns — repeated tasks, processes described in meetings, email patterns, recurring prep work — and surfaces concrete automation opportunities ranked by ROI (time saved per week) and build complexity.

Runs in two modes:
1. **CEO-facing mode** ("what can be automated?"): Shows opportunities during onboarding or as a scheduled scan. Creates wow moments. Builds trust in the Command Room system.
2. **Operator mode** (maintenance audit): Surfaces patterns that might not be obvious at first glance — repeated manual tasks, undocumented processes described in meetings, email templates that should exist, session history showing repeated manual requests.

## How to Use

```
"What can I automate?"
"Run an automation scan"
"Show me automation opportunities"
"What processes could we build?"
"Automation audit"
```

## What It Scans

### 1. Email Patterns
- Emails sent repeatedly to the same recipients with similar content (follow-ups, status updates, vendor requests, check-ins)
- Email templates that should exist but don't
- Responses to common questions that recur weekly/monthly
- Scheduling requests or calendar coordination patterns
- Finance/expense/invoice patterns
- External communication loops (customer updates, board reports, investor check-ins)

**Source:** Google Gmail — look for repeated message patterns over the last **60 days** (fixed window — long enough to surface monthly recurrence, short enough to stay current).

### 2. Calendar Patterns
- Recurring prep work before meetings (prep time blocked, same meeting setup every week)
- Recurring post-meeting work (debrief, follow-up scheduling, note distribution)
- Calendar blocks for repetitive administrative work
- Meeting types that always need the same context pulled (all-hands, board calls, 1:1s, etc.)
- Scheduling bottlenecks (back-and-forth emails to find meeting times)

**Source:** Google Calendar — examine recurring events and blocks over last 30 days.

### 3. Task Tracker Patterns
- Tasks that reappear in the same format (weekly status update, monthly board report, recurring cleanup)
- Items with the same tags/labels appearing frequently
- Tasks that keep bouncing between "blocked" and "in progress"
- Recurring administrative work (expense reports, timesheets, check-ins)

**Source:** `_hq/data/events.jsonl` (Tier 1 canonical event log per `references/SOURCE_OF_TRUTH.md`) — read commitment events directly via `shared/scripts/cru_match.py::load_open_commitments` for the open set — then keep the confirmed half, `cru_match.split_pending_review(...)` (INTAKE: unconfirmed extractions are needs-your-call queue members, and a guess should never seed a suggested automation) — plus a full scan for historical recurring patterns. Do NOT analyze MASTER_TRACKER.md as the source — it's a Tier 2 projection that regenerates at coarse cadence and would mask the month-over-month repetition signal automation-scanner needs.

### 4. Meeting Notes Patterns
- Processes described verbally in meetings that aren't in any system yet
- Action items that follow the same template repeatedly
- Decisions that need templates
- Onboarding or repetitive workflows mentioned in session notes
- Questions asked repeatedly in meetings

**Source:** SESSION_NOTES files in each project folder — scan for repeated action items, recurring questions, described workflows that should be systems.

### 5. Session History Patterns
- Things the user asks you to do manually that could be skills (e.g., "prep me for every Monday meeting", "send weekly status to board", "pull metrics for our dashboard")
- Requests that follow the same pattern weekly/monthly
- Context you assemble manually that could be an automated dashboard or report
- Manual data entry or copy-paste work

**Source:** Read this session's recent history and check the workspace for README or logs of recurring requests.

## Output

**Deliverable link (CONTRACT Rule 3 — H2 heading link, LAST in the turn):** surface the .docx via `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_path))` as the final line of the chat response — after the widget/summary and Sources, never interspliced mid-body, never a plain-text path, never a hand-built `computer://` URL. Format

**Rendering (v3.13.8+ — Bug #53):** render the `.docx` via the canonical `shared/scripts/brief_writer.py` `make_brief(brief_kind="automation_scan", ...)`. brief_writer enforces canonical typography, Heading 1/2/3 hierarchy, and runs the universal post-render leak scanner (Bug #57/#59/#54) automatically. Use the v3.13.8 `table` primitive for the ranked-list section rather than synthesizing bullets for column-shaped data.

**That call is the only render path (DOCFENCE1):**

- **NEVER hand-roll the scan** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking scan (the v3.20.0 failure mode).
- **NEVER create, render, copy, upload, or update the scan — or any part, derivative, or restatement of it ("the ranked list", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so the ops lead can add to it", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the scan in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link. (Scanning FOR a connector-based automation is the skill's job; DELIVERING this document through one is not.)

**Executive Output Standard (SPEC OUT2 §4 — `automation_scan` is now a STANDARD_KIND; `make_brief` REFUSES the render without this).** Pass `exec_header`:
- **verdict = top opportunity + payback** — the #1 ranked item with its arithmetic: *"Automate the Monday roundtable prep — 15 min/week back for a 2-hour build."* When the scan finds nothing above threshold: *"No automation worth building this scan — your recurring work is already covered."*
- **changed** = movement since the last scan (newly surfaced vs previously-surfaced-still-unbuilt), or the nothing-form. **decide** = which ONE quick win to build this week. **needs** = "say 'scaffold #1'" (or "Nothing from you" on an empty scan).
- **Subsumption (net length must not increase):** the header REPLACES the former "Here's what I found you could automate — [DATE] / Found [N] opportunities" lead lines in the output template below — the count moves into the tile band, the conclusion into the verdict.

**Ranked-report layout (SPEC OUT2 §4 — this scan is one of the four ranked-report surfaces; contract in `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The ranked report"):**
- **Tile summary band first** — pass the first section a `tiles` list derived from the SAME scan computation: **opportunities** (count surfaced) · **time back** (summed estimated min/week across quick wins) · **quick wins** (count with rank_score ≥ 10). Drop-empty per F-60: a tile with no real datum is omitted, never rendered as 0-filler.
- **Scored rows** — each ranked item carries: rank (#N) · name (what it automates) · quantify tag (the `rank_score` + time-saved estimate, shown so the CEO can challenge the inputs, not the math) · why-now ("Where I saw it" — the concrete workspace example) · action ("scaffold #N").
- **Actions last** — the "What's next" block stays the closing action surface; on a widget turn the widget IS the ask block (one-ask-surface), no prose twin.

**Visual pass (SPEC OUT2 §3, after the save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever).

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("automation_scan", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions (the ranked-report contract above stays authoritative; the exemplar anchors layout within it). Workspace exemplar (`_hq/exemplars/automation_scan/`) beats the shipped seed; `None` = compose on the layout above, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the scan. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered scan ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="automation_scan", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

**Ranking formula (compute, don't vibe):**

```
build_hours = {"S": 2, "M": 5, "L": 12}[complexity]
rank_score  = (time_saved_per_week_minutes × 52 / 60) / build_hours
            # = hours saved per year ÷ hours to build
```

Categorize by `rank_score`: **quick win = ≥ 10** (surface ALL); **medium = 3–10** (surface the top 2); **long-term = < 3** (omit unless the CEO asks). **Show the score next to each item** so the CEO can challenge the inputs, not the math.

Ranked list with time-saved estimate, build complexity, and one-line description. Use this structure:

```
[Exec header (OUT2 §4) replaces the former "Here's what I found… / Found [N]…" lead lines:]
**[Top opportunity + payback — e.g. "Automate the Monday roundtable prep — 15 min/week back for a 2-hour build."]**
CHANGED   [movement since the last scan, or "Nothing new — same 3 opportunities still unbuilt."]
DECIDE    [the ONE quick win to build this week]
NEEDED    [say 'scaffold #1', or "Nothing from you."]

[Tile band: opportunities · time back/week · quick wins — from the same scan computation]

#1 — saves about [TIME_SAVED] a week
What it does: [What the automation does]
Build size: [Small / Medium / Large]
Where I saw it: [your email / your calendar / your task list / your meeting notes / things you've asked me to do by hand]
Time to build: [e.g., "about 2-4 hours to set up"]
Example from your workspace: [Concrete example]

#2 — saves about [TIME_SAVED] a week
[same format]

---
Quick wins (do these first):
1. [Small build + high time savings]
2. [Next]

Bigger builds, bigger payoff:
1. [Medium build]

Long-term — these change how you work:
1. [Large build]

What's next:
- Pick ONE quick win to build this week
- I'll help you build it
- Run it for 2 weeks, then we'll check how much time it actually saved
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "Time to build: 2-4 hours to build the skill + template"
- Good: "Time to build: about 2-4 hours to set up"

### Complexity Definitions

- **S (Small):** Template + simple Gmail or Slack integration. Can build in 1-2 hours. Examples: email template, weekly email reminder, Slack checklist.
- **M (Medium):** Skill with data pulls from 2-3 sources (Calendar + Gmail, or Task tracker + project context). Conditional logic. Build in 3-6 hours. Examples: weekly status digest, meeting prep brief, automated report.
- **L (Large):** Multi-step workflow, connects 4+ sources, requires custom logic. Build in 8+ hours. Examples: full onboarding workflow, automated dashboard with live data pulls.

### ROI Calculation

Time saved = (minutes per occurrence) × (frequency per week)

Examples:
- Email you send 3x/week that takes 8 minutes = 24 minutes/week = "24 min/week"
- Weekly meeting prep that takes 15 minutes = "15 min/week"
- Monthly report that takes 2 hours = "30 min/week" (2 hours ÷ 4 weeks)

## Triggers

- "what can be automated"
- "automation opportunities"
- "show me what I could automate"
- "automation scan"
- "automation audit"
- "what processes could we build"

## Connected Tools

- **Google Gmail** — Search for repeated email patterns (follow-ups, status updates, vendor requests, check-ins)
- **Google Calendar** — Analyze recurring meetings, prep blocks, post-meeting work
- **Slack** — Scan for repeated questions or administrative patterns (optional)
- **Granola** — Review meeting notes for undocumented processes
- **`_hq/data/events.jsonl`** — Analyze commitment and interaction patterns for repetition (canonical Tier 1 source per `references/SOURCE_OF_TRUTH.md`; NOT MASTER_TRACKER.md, which projects at coarse cadence)
- **SESSION_NOTES files** — Search project notes for processes described verbally
- **PROJECT_CONTEXT.md** — Extract workflow details

## Gotchas

### "But we already tried automation and it didn't work"
**What happens**: You see an opportunity but remember a previous tool that didn't stick.
**Instead**: Ask: "Why didn't it work?" Common reasons: tool was too slow, wasn't integrated into your flow, required extra steps. This time it lives inside your Command Room — it will feel integrated because it IS.

### Opportunity is too vague
**What happens**: Scanner surfaces "meeting prep could be faster" without specifics.
**Instead**: Always provide a concrete example from the workspace. "Every Monday you spend 15 minutes pulling context on the CEO roundtable. You ask for attendee background, check past notes, and search for relevant emails. Your Command Room could do this in 10 seconds."

### Email/Calendar patterns that are false positives
**What happens**: You sent 3 emails to the same person but they're all different (not a real pattern).
**Instead**: Only flag if there's a genuine template hiding in the pattern. Look for: same recipient + similar subject + similar structure.

### Task tracker patterns aren't visible without context
**What happens**: Scanning MASTER_TRACKER shows "status update" tasks but they're from different projects (not really a pattern).
**Instead**: Only flag if the same task appears repeatedly in the SAME project or across multiple projects (suggesting a system-wide process).

### Session notes contain aspirational patterns ("we should automate X")
**What happens**: Session notes say "we need a better way to track this" but no one is actually doing it manually.
**Instead**: Focus on things that ARE happening now. "You should automate X" is great for future builds, but actual automation opportunities are things you're already doing manually.

### Skipping connected sources
**What happens**: Gmail or Calendar isn't connected, so the scan returns fewer opportunities.
**Instead**: Skip gracefully. Build the scan from whatever sources ARE connected. If only local files (SESSION_NOTES, MASTER_TRACKER) are available, scan those deeply. Say at the end: "Note: Gmail and Calendar aren't connected. If they were, I'd likely find 3-5 more email/meeting prep opportunities."

### Opportunity requires systems you don't have yet
**What happens**: "You should automate expense reports" but you don't have an expense tracking system.
**Instead**: Flag it but mark it lower priority. "This would require first building an expense tracker" — include it in suggestions but note the dependency.

### Measurement is hard
**What happens**: You estimate "30 min/week saved" but after building, it only saves 10 minutes.
**Instead**: Always suggest measuring. "After you build this, run it for 2 weeks and track actual time saved. Adjust the estimate."

## Mode Details

### CEO-Facing Mode ("What can I automate?")

**Trigger**: User asks "what can be automated?" during onboarding or as a standalone scan.

**Output**: Shorter, more digestible. Focus on wow moments. Highlight 3-5 quick wins and 1-2 medium-complexity ideas.

**Tone**: "Here's where I see opportunity to reclaim time. Let's start with the quick wins."

**Next step**: Ask "Want me to build the first one?" — on yes, say `scaffold #N` semantics: hand the picked opportunity (by its `automation_opportunity_surfaced` seq) to **scaffold-automation**, which generates the config + setup recipe.

### Operator Mode (Audit)

**Trigger**: the operator is running a periodic maintenance audit. Scans systematically for patterns that aren't obvious.

**Output**: Full detailed report. Include everything, ranked by ROI. Provide concrete examples with dates and specifics.

**Tone**: "Here are the patterns I'm seeing. Some of these might be worth discussing with the CEO."

**Next step**: "Want me to present the top 3 quick wins to the CEO, or build one directly?"

## Rules

- **Be specific.** Every opportunity needs a concrete example from the workspace (with dates if possible).
- **Measure everything.** Always estimate time saved per week. If you can't, it's probably not a real opportunity.
- **Prioritize ruthlessly.** Show quick wins first (S complexity + high ROI). Don't overwhelm with long-term infrastructure.
- **Consider effort, not just impact.** A 10-minute/week opportunity with S complexity is better than a 60-minute/week opportunity with L complexity (for momentum).
- **Skip connected sources gracefully.** If Gmail isn't connected, don't say "can't scan emails" — just note it at the end.
- **Don't guess.** Opportunities must be based on actual patterns in the workspace, not assumptions.

## What It Doesn't Do

- It doesn't build the automation (say **"scaffold #N"** — scaffold-automation builds from the surfaced opportunity)
- It doesn't estimate cost or resourcing
- It doesn't prioritize based on company strategy (you do that)
- It doesn't measure realized ROI after deployment (deployment state IS tracked — the Writer Contract's per-opportunity events + scaffold-automation's `automation_deployed` events carry built/deployed status; what nothing measures yet is actual-vs-estimated time saved)

## Next Steps

After the scan:
- Pick ONE quick win (S complexity)
- Say **"scaffold #N"** — scaffold-automation builds it
- Test it for 2 weeks
- Measure actual time saved
- Repeat

## Routing (full trigger corpus)

The description above carries the scan triggers; this section carries the settings family added by SPEC OUT2 §5 (the description budget is capped per G11 — the runtime router and the trigger tests read the description and this Routing corpus together). Everything below remains binding at fire time.

> Also handles first-run personalization settings — use when the CEO says 'tune automation-scanner', 'tune the automation scan', 'show automation-scanner settings', 'reset automation-scanner to defaults'. DOES NOT fire on 'tune output' / 'show output settings' (workspace-manager — the cross-skill document profile, not this scan's ranking knobs).
