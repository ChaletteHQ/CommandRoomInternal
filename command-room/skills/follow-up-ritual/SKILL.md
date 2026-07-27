---
name: follow-up-ritual
description: "Meeting transcript or recording → 60-second close-the-loop pack: summary, per-attendee action items, personalized follow-up email drafts ready to send. Triggers: 'follow up on that call', 'follow up on the meeting', 'follow up the meeting', 'follow up on the call', 'process the call and draft follow-ups', 'close the loop', 'close the loop on', 'follow-up ritual', 'draft follow-ups', 'draft follow ups', 'send follow-ups from my last call'. Plus 'tune follow-up-ritual'. Owns meeting-context `follow up` phrasing — meeting-notes does not fire on these. DOES NOT fire on 'follow up with [name] about [topic]' with no meeting in context (email-writer — a plain outbound draft; same for dormant-customer-scan and thread-resurrection hand-offs)."
voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 2.7.1
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving any attendee from the meeting transcript or trigger phrase, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. For the action-item / commitment scan that feeds the follow-up pack, use `shared/scripts/cru_match.py::load_open_commitments` — do NOT hand-roll an events.jsonl scan — and pass it the org-scoped rows: `load_open_commitments(events_path, events=org_events)` where `org_events` comes from `events_io.load_events_org_scoped` (PGUARD2 D2 — attendee-facing output must not see personal-lane or masked-account commitments). See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.1)

- **Owns:** post-meeting close-the-loop — logging + drafted emails + tracker/decision updates in one pass.
- **Invokes `meeting-notes` internally** for the structural logging step — **silent-invocation contract (P1.3, mirrored in meeting-notes Step 9b):** the internal meeting-notes run does logging ONLY and MUST skip its own Step 9b (which invokes this skill) — the guard that stops the pair looping. Conversely, when meeting-notes runs top-level it invokes THIS skill silently for drafts, and this skill then must not re-invoke meeting-notes (the transcript is already logged — reuse its output) (including v2.2 thread classification with `primary_thread_id` + `related_thread_ids[]` + `classification_confidence`), then adds the email-draft layer on top. Never produces duplicate SESSION_NOTES entries.
- **Does NOT fire on "prep me for…"** — that's `call-prep`.
- **Does NOT fire on bare "meeting notes"** without follow-up language — that's `meeting-notes` standalone.
- **Inherits thread classification from meeting-notes.** The meeting's primary thread determines the folder for the follow-up pack; related threads get cross-ref lines. Follow-up-ritual does not run its own classifier.

If user says "just log the meeting, no follow-ups" — route to meeting-notes. If user says "process the call and draft follow-ups" — this skill.

## Voice Protocol (v3.0 — v2.7.1 architecture)

This skill follows the two-step draft-then-critique protocol defined in `shared/VOICE_CALIBRATION.md`. Voice lives in the `## Voice Block` section of this SKILL.md — NOT in a separate `VOICE_SAMPLES.md`. That file is deprecated as of v2.7.1.

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-follow-up-ritual.md` if it exists — it supersedes this SKILL.md's `## Voice Block` section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

Every follow-up email draft:
1. Uses this skill's Voice Block (cadence, openers, vocabulary, punctuation, taboos — tuned for post-meeting follow-up register).
2. Applies recipient modifier based on the attendee's `entities.json` record (board/peer/customer/team/vendor).
3. Passes the Step 2 critique pass against the Voice Block + universal banned-phrase list.
4. Strips any banned LLM tells before return.

Corrections on user-edited drafts append to `_hq/voice/corrections-follow-up-ritual.jsonl`. The corrections corpus drives the next Voice Block refresh when the operator runs the calibration protocol.

## Personification Contract (v3.13.8.4+)

Before surfacing the pack-drafted acknowledgment, read `shared/PERSONIFICATION.md`. The chat acknowledgment after drafting the pack is FIRST PERSON, plain text (no backticks around the meeting name): "Drafted {N} follow-ups for you, {first_name} — pulled straight from [Meeting Name]. Ready to review." Never the third-person "{brain_name} pulled…" shape — the AI speaks as itself. Outbound EMAIL drafts close with the customer's own signature block (read from `workspace.user_signature`), NEVER the brain_name — the brain_name never appears in the email body the recipient sees.

## Writer Contract

Every outbound follow-up email drafted AND sent through this skill emits an `interaction` event to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` using the v2.2 shape (`primary_thread_id` + `related_thread_ids[]` + `classification_confidence` + `org_ids[]` + `source_ref_hash`). The primary thread is inherited from the meeting's classification. Direction: outbound, channel: email, counterparty_person_ids resolved from attendees. Idempotent with Gmail-side capture via `source_ref_hash`.

**Commitment events (v2.7.15+).** This skill invokes `meeting-notes` internally for the logging step (decisions / commitments / SESSION_NOTES). `meeting-notes` Step 5e produces the canonical `type: commitment` events for every qualifying action item. Do NOT double-emit — if `meeting-notes` already wrote commitment events for this transcript, this skill consumes them (Step: Surface Open Commitments, below) but does not re-write them.

**Pack-drafted event (v3.7.1+).** Beyond the per-recipient `interaction` events, this skill also appends ONE `followup_pack_drafted` event per ritual run with `{meeting_event_seq, attendee_count, draft_email_count, decisions_logged, commitments_extracted, pack_artifact_path}`. The `meeting_event_seq` links back to the source `meeting` event written by `meeting-notes`, so consumers can resolve "which meeting did this pack come from" without filename heuristics. This is the substrate signal `operator-report` reads to count delivered-without-being-asked work.

**Commitment seq linking (v3.7.1+ formalization).** Every commitment event `meeting-notes` writes during this ritual MUST carry `data.source_event_seq` pointing at the parent `meeting` event's seq. This was implicit before — the v3.7.1 spec formalizes it so consumers (`cr-commitments`, `decision-revisit`, `thread-resurrection`) can traverse the meeting → commitment graph deterministically. If `meeting-notes` is currently failing to set `source_event_seq` on commitment writes invoked from this skill, that's a follow-on fix to land in the same release.

---

# Follow-Up Ritual

**For:** Operator-CEOs who run 5-10 meetings a day and currently spend 20 minutes per meeting on the "and now I email everyone with their action items" tax. This skill collapses that to 60 seconds of review.

## What It Does

Take a meeting (Granola transcript, uploaded recording, or pasted notes) and produce the complete post-meeting close-the-loop pack in one pass:

1. **Structured summary** — what was discussed, what was decided
2. **Per-attendee action items** — clearly attributed, with dates where stated
3. **Personalized follow-up emails** — drafted in the user's voice, one per attendee who owns an action, ready to review and send
4. **Decisions logged** — `decision` events via the `decision-log` skill if any decisions were made (the `_hq/views/DECISION_LOG.md` view regenerates from them)
5. **Tracker updates** — `commitment` events written by the internal `meeting-notes` run (MASTER_TRACKER is a regenerated view; nothing writes it directly)

The demo-critical moment: a 60-second mock call → artifacts generated on screen → user hits send.

This is the group's #1 stated pain (Bo's direct feedback). It's the demo beat of the May 14 CEO-group pitch.

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Read
config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "send_timing": "show_now_send_on_click",  # AF — show_now_send_on_click | queue_same_day | queue_next_morning
    "recipient_default": "attendees_who_owe", # STT — attendees_who_owe | all | me_only
}
cfg = get_config(workspace_root, "follow-up-ritual", DEFAULTS)
```

`send_timing` gates OUTBOUND email behavior, so it is **ask-first (AF)** — the one exception to
output-first. `show_now_send_on_click` (default) keeps the lazy contract email-writer uses (the
pack renders as editable widgets; Gmail is only touched on a click). `queue_same_day` /
`queue_next_morning` create the Gmail drafts and schedule them. `recipient_default` is STT (who
gets a follow-up by default).

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "follow up on that call", "draft follow-ups" | first relevant request only: ask the ONE AF send-timing question (below) BEFORE drafting the pack, then `save_skill_config(workspace_root, "follow-up-ritual", DEFAULTS)`; subsequent fires skip straight to the pack with the saved timing. |
| **Show settings** | "show follow-up-ritual settings" | render current config in plain English; no pack. |
| **Tune** | "tune follow-up-ritual" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → confirm. |
| **Reset** | "reset follow-up-ritual to defaults" | `wipe_skill_config(workspace_root, "follow-up-ritual")` → next fire is a first-fire again. |

**First fire — the ONE AF question (asked once, before the first pack, with a working default-escape):**

On the very first follow-up-ritual fire only (`not is_configured(workspace_root, "follow-up-ritual")`), before drafting the pack, render a single fixed-option micro-widget:

> *First time closing the loop for you. How should I handle the follow-up emails?*
> **[Show me first ▸ recommended]** — I'll show you the drafts right here to review; nothing sends until you click.
> **[Queue same day]** · **[Queue next morning]** — I create the Gmail drafts and schedule them.
> *Just go ahead and I'll use Show-me-first.*

This is the documented current-state fixed-option row (`shared/CHAT_ACTION_WIDGET.md` preselect
exception). Default-escape: proceeding applies `show_now_send_on_click`. After the choice (or skip):
`save_skill_config(workspace_root, "follow-up-ritual", {**DEFAULTS, "send_timing": choice})` with
`origin="first_fire_override"` if changed, else plain DEFAULTS. Then draft the pack. `recipient_default`
is NOT asked at first run (one AF question max; the pack ends in email widgets, so a trailing footer
would violate `CHAT_ACTION_WIDGET.md` MUST-NOT rule 5) — it's discoverable via `tune follow-up-ritual`
and the freeform table. The block renders exactly once ever.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "queue my follow-ups for the same day" | `send_timing = queue_same_day` |
| "queue follow-ups for the next morning" | `send_timing = queue_next_morning` |
| "always show me first" / "stop auto-queuing follow-ups" | `send_timing = show_now_send_on_click` |
| "follow up with everyone" | `recipient_default = all` |
| "only draft for people who owe something" | `recipient_default = attendees_who_owe` |
| "just draft my own follow-ups" / "me only" | `recipient_default = me_only` |

After applying: `save_skill_config(..., is_reconfigure=True)` + confirm in one line. `recipient_default` shapes which attendees get a drafted email; `send_timing` gates the pack's send behavior (still never sends without a click when `show_now_send_on_click`).

## How to Use

```
"Follow up on that call"
"Follow up the [meeting / with Client X / with the team]"
"Process the call and draft follow-ups"
"Close the loop on the [meeting name]"
"Draft follow-ups from my last call"
"Follow-up ritual for the [project] sync"
```

The skill auto-detects the meeting — most recent Granola note, pasted transcript, or named meeting — and builds the pack.

## How It Works

1. **Locate the meeting.**
   - Priority: pasted transcript > uploaded file > most recent Granola note > named meeting (search Granola by name/time).
   - If ambiguous, ask ONE question: "Last call was [Title] at [Time] — use that one?"
2. **Extract attendees, decisions, action items.**
   - Identify each attendee with role context (pull from `_hq/PEOPLE.md` if available — orientation only / static-tier lookup; outstanding-state reads come from events.jsonl via `load_open_commitments(events_path, events=org_events)` over the org-scoped load — PGUARD2 D2, never the no-arg owner form and never from this Tier 2 view).
   - Surface explicit decisions, not just discussion.
   - Attribute action items by owner + due date. If no date was stated, mark TBD, don't invent.
3. **Pull voice.**
   - Use the baked-in `## Voice Block` in this SKILL.md (Voice Protocol v3.0) as the sole voice source — do not read external `VOICE_SAMPLES.md` files.
   - Default register for uncalibrated workspaces is sharp and warm ("Good talk. Quick recap — …").
4. **Draft the follow-up emails.**
   - One per attendee who owns ≥1 action item.
   - Subject: "Follow-up: [Meeting topic], [Date]" — dash-free (S3 subject gate: no dashes as punctuation in subjects; run the drafted subject through the detector with `--context subject`, same bash gate as the body below)
   - Body: 1-sentence opener → bullet list of their actions → their dates → sign-off.
   - If a decision concerns them, surface it in one line.
   - **Warm-but-brief formula.** Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md`:
     - Open with one specific line about THIS meeting ("Good call — the pricing discussion unblocked us"), never politeness filler ("Thanks for taking the time").
     - Actions as facts: "You're sending the deck Thursday" — never "hopefully" / "if you have time."
     - Three sections max, blank lines between. Warmth lives in specificity, not in pleasantries.
   - **Mechanical voice-tell gate (B2 — bash-gated, not prose).** After drafting each follow-up email and before surfacing it, run its body through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn. This backstops the Step 2 critique, it does not replace it:

     ```bash
     SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
     PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
     printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context email
     ```

     On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0 (`pass`/`warn`). Never surface a draft the detector still fails. A phrase the recipient's calibrated Voice Block demonstrably allows is exempt via `allow_phrases` (the Voice Block Taboos carve-out); never improvise the override.
5. **Write artifacts.**
   - Save the meeting-recap pack to `[Primary Thread folder_name]/meetings/FollowUp_[Meeting]_[YYYY-MM-DD].docx` (primary thread comes from meeting-notes classification — `.docx` per CONTRACT Rule 27, no .md deliverables). **v3.13.0+ — the .docx pack does NOT include the email body.** Per M's 2026-05-20 feedback #13: the document is the synthesis/recap; the emails are a separate artifact that belong in the widget where they can be edited. Email bodies stay in the chat-action widget (step 6) and Gmail Drafts only.
   - For each `related_thread_ids[]`, append a cross-ref line to that thread's session notes per the `meeting-notes` Step 4 convention.
   - Log decisions by invoking the `decision-log` skill (it appends `decision` events; the DECISION_LOG view regenerates).
   - Append new commitments to `_hq/MASTER_TRACKER.md` (invoke `workspace-manager` activity log).
   - Generate the follow-up email TEXT only — do NOT create Gmail drafts at fire time (lazy creation per `shared/EMAIL_DRAFT_PROTOCOL.md` §1). The text surfaces in the widget (step 6); a Gmail draft is created only when the user clicks `draft` / `send` / `edit then send`, never auto-send.
6. **Surface in chat via the canonical chat action widget** (v3.13.0+ — per `shared/EMAIL_DRAFT_PROTOCOL.md` universal scope). One widget item per attendee with a follow-up draft, plus a separate H2 link to the .docx recap at the bottom. Data view shape:

   ```python
   data_view = {
       "widget_mode": "all_batch_widget",
       "header": f"Follow-ups: {meeting_topic} — {meeting_date}",
       "sub_header": f"{n_drafts} drafts ready · Recap doc: [link below]",
       "sections": [{
           "title": None,
           "count": None,
           "items": [
               {
                   "n": i,
                   "icon": "✉️",
                   "name": draft.attendee_display_name,
                   # metadata is LIST OF [key, value] PAIRS — required for
                   # email-shaped item validation.
                   "metadata": [
                       ["To", attendee_email],
                       ["Subject", subject],
                   ],
                   "context_tag": "Ready for your review — nothing sends until you click",
                   "body_lines": [f"> {line}" for line in draft.body.split(chr(10))],
                   "actions": [f"{i} send", f"{i} draft", f"{i} snooze 3d"],
               }
               for i, draft in enumerate(per_attendee_drafts, start=1)
           ],
       }],
   }
   from widget_transport import render_and_persist
   transport = render_and_persist(data_view=data_view, wrapper="fragment",
                                  persist_dir="<WORKSPACE>/_hq/.system/widgets",
                                  name_hint="follow-up-ritual")
   # Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) (EW2+T, F-15 —
   # shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
   ```

   After posting the widget, surface the .docx recap link separately at the bottom per `shared/CONTRACT.md` Rule 3: build it with `chat_output_renderer.doc_headline_link(label, brief_path.get_brief_artifact_url(absolute_docx_path))` — never hand-encode a `computer:///` URL. One-line summary above the link: "Drafted 3 follow-ups for Aria, Bowie, and Lyra. Logged 2 decisions. Added 4 new commitments to your tracker."

   **Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
   - Bad: "I draft them as editable widgets — Gmail draft fires on click."
   - Good: "I'll show you the drafts right here to review — nothing sends until you click."

## Step: Surface Open Commitments (v2.7.15+)

Before drafting follow-up emails, scan `_hq/data/events.jsonl` for **open commitments** that involve the meeting's attendees. **Read via the org-scoped reader, never a raw load** (PGUARD2 — the follow-up emails reach the attendees): `from events_io import load_events_org_scoped; org_events, skipped = load_events_org_scoped(workspace_root)`, then feed the open-set projection through the seam — `load_open_commitments(events_path, events=org_events)` (PGUARD2 D2 — the injection keeps personal-lane and masked-account commitments out of the pack; never the no-arg owner form here). The pack must surface them so the user knows what's already on the floor before adding more — and so each attendee's follow-up email can include a "still open from before" line where relevant.

### Read recipe

From the org-scoped `load_open_commitments(events_path, events=org_events)` result, for each attendee `person_id`, keep events where:
- `type == "commitment"` AND
- `data.status` (or top-level `status`, for legacy events) is `"open"` or `"overdue"` AND
- the commitment hasn't been closed by a later `commitment_resolved` / `thread_resolved` event referencing its id (the projection already applies the closer chain) AND
- `data.owner_id == <attendee_person_id>` OR `data.owner_id == <user_id>` for outbound (you owe this attendee)

Group by attendee. For each attendee with ≥1 open commitment, include a "Still open" subsection in their per-attendee section of the pack:

```
### Still open with [Attendee]
- They owe: [title] (due [date], aged [N] days)  ← from prior meeting/email
- You owe: [title] (due [date])
```

If the meeting just closed one of these (e.g., the attendee delivered the thing or the user did), close it through THE closure path (Stage B 2026-07, F2 — supersedes the append-the-JSON-yourself shape):

```python
from commitment_state import close_commitment, CommitmentIdError, PendingReviewError
close_commitment(
    workspace_root, "<id of the original commitment event — verbatim>",
    resolved_by="<person_id>",
    evidence="<one-line reason — 'delivered in this meeting' or 'mentioned as done in transcript'>",
    source_skill="follow-up-ritual",
)
```

It writes the canonical `commitment_resolved` shape from `shared/COMMITMENT_SCHEMA.md`, normalizes legacy id spellings, refuses no-match ids loudly (`CommitmentIdError` → skip; never write an orphan tombstone), is idempotent over the full resolved-id set, and never auto-resolves a `pending_review` item (`PendingReviewError` → leave it for the review surface).

This is how commitments stop accumulating in views over time — without resolution events, every commitment stays "open" forever.

### Surface count in the pack header

Add to the pack header line: `"Open before this call: 3 (they owe 2, you owe 1). Closed on this call: 1."`

If zero on either side, omit that half of the line.

---

## Output Structure

`FollowUp_[Meeting]_[YYYY-MM-DD].docx` content structure (rendered via brief_writer):

**The pack is rendered, never assembled by hand (DOCFENCE1).** `make_brief` is the only path that produces it:

- **NEVER hand-roll the pack** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking recap (the v3.20.0 failure mode).
- **NEVER create, render, copy, upload, or update the pack — or any part, derivative, or restatement of it ("talking points", "a summary", "just the action items") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not the thread's meetings folder (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the recap in a Google Doc" is a request this gate refuses, not an override. Say the canonical pack already exists and hand back its link. Attendee-facing delivery is the per-attendee email drafts in the widget; the recap itself stays the `.docx` link.

> **Executive Output Standard (EXEC1, v3.20.0+).** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`: the pack **opens with "What YOU committed to in this room" before others' items** — the `owner_id == user_id` split is already computed (Step: Surface Open Commitments), so this is pure ordering. Pass `make_brief(brief_kind="followup_pack", ...)` an `exec_header` whose verdict names what the user walked out owing ("You own 2 items from this call — pricing redline by Thu, intro by Fri"); CHANGED = what got decided/closed; DECIDE/NEEDED follow the floor or the nothing-form. The **drafts widget IS the ASK block** (element 4, one-ask-surface) — the per-attendee draft widget is the reader-action surface; do NOT also render a prose "What I need from you" twin in the .docx.

> **Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("followup_pack", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions. Workspace exemplar (`_hq/exemplars/followup_pack/`) beats the shipped seed; `None` = compose on the structure below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header, the YOUR-items-first ordering, or the one-ask-surface rule, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the pack. After saving, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (shared with the visual pass below, warn-only). When the user gives structural feedback on a delivered pack ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="followup_pack", direction=..., section=...)` — capture only; the exemplar itself updates exclusively through insight-generator's confirm-first proposals (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

**Visual pass (SPEC OUT2 §3, after the .docx save):** run the render-then-critique pass per `shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass" — call `shared/scripts/visual_gate.py` `render_preview(<saved path>)`, LOOK at the returned page images against the 7-item checklist (orphaned heading at a page break · empty/placeholder tile · table overflow/wrap damage · cramped spacing · header/footer intact · brand palette applied · chart unreadable / overplotted), fix the sections payload + re-save AT MOST ONCE, then log `visual_gate.log_visual_gate(WORKSPACE_ROOT, doc, rendered, findings, fixed)` either way. `None` from the ladder = no renderer on this machine — log `rendered: false` with a `skipped_reason` and proceed exactly as before (warn-only forever: a finding never refuses a save, and the pass never loops).

```
# Follow-Up: [Meeting Name]
Date: YYYY-MM-DD | Attendees: Aria, Bowie, Skyler | Project: [Project]
Open before this call: 3 (they owe 2, you owe 1). Closed on this call: 1.

[EXEC HEADER — verdict names what YOU walked out owing; CHANGED = decided/closed]

## What you committed to in this room   (EXEC1 — YOUR items first, owner_id == user_id)
- Send the revised pricing model — Thu Apr 25
- Intro Bowie to procurement — this week

## Summary
[3-5 sentence recap]

## Decisions
- [Decision 1]
- [Decision 2]

## Action Items (others)
| Owner | Action | Due |
| Aria | Send pricing redline | Apr 25 |
| Bowie | Intro me to procurement | This week |
| Skyler | Circulate revised SOW | Apr 24 |

## Logged
- 2 decisions added to your decision log
- 4 new commitments added to your tracker
- 3 follow-up drafts ready to review and send
```

## Voice Block

**Last refreshed:** 2026-04-21
**Calibration level:** default
**Sample count:** 0 (uncalibrated — generic warm-but-brief exec defaults)

### Sentence cadence
- Typical length: 10-18 words
- Maximum: 25 words
- Short-punch openers: common ("Quick recap.", "Two things.")

### Openers
- Preferred: "Quick recap from this morning", "Following up on our call", "Two action items from today"
- Avoided: "Per our discussion", "As discussed", "Pursuant to", "I wanted to circle back"
- Never use: "I hope this email finds you well", "Happy to help", "Let me know if you have any questions"

### Vocabulary
- Uses: "quick recap", "next step on your side", "on my side", "you're on point for", "I'll send", "confirmed"
- Avoids: "pursuant to", "per our discussion", "going forward", "touch base", "circle back", "reach out"
- Domain-specific: action-item language ("you own X by [date]", "I'll send Y by [date]", "still open: Z")

### Punctuation
- Em-dashes: occasional
- Semicolons: rare
- Parentheticals: rare
- Line breaks: use liberally between sections (recap / their actions / my actions / open items)

### Structure
- Lead with: the purpose (following up on [meeting]) and a one-line recap.
- Body: 3 sections max — your actions, my actions, open items. Each section 1-3 lines.
- Close: simple sign-off. "—Mira" or first name.

### Tone markers
- Register: warm-but-brief exec, efficient
- Self-reference: first-person used freely
- Hedging: minimal — confirmed action items stated as facts

### Taboos (per-skill)
- Never: "I hope this email finds you well", "pursuant to", "please don't hesitate"
- Never send drafts with vague action items — "touch base next week" gets rewritten as "I'll send the revised doc by Thursday EOD."

### Examples

**Example 1 — Short follow-up to peer:**
```
Good call. Two things:

- You're sending the updated deck by Thursday
- I'm introducing you to Skyler on Monday

Open: pricing on the service tier — we'll revisit next week.

—Mira
```

**Example 2 — Follow-up to customer:**
```
Quick recap from this morning:

- We're moving forward with the pilot starting May 1
- Your team will send the data template by Friday
- My team will have the onboarding deck ready by April 28

I'll circulate a brief project plan by Wednesday so we all have the same
picture.

Mira
```

## Triggers

- "follow up on that call"
- "follow up the meeting"
- "follow up with [person]"
- "process the call and draft follow-ups"
- "close the loop on [meeting]"
- "draft follow-ups"
- "send follow-ups"
- "follow-up ritual"

## Gotchas

- **Drafts only, never auto-send.** User reviews in Gmail. This is non-negotiable.
- **If no mail connector is available, the email bodies stay in the chat-action widget** (they're already there per step 6 — the widget is the edit surface) with a friendly line above it: "Email isn't connected yet — you can copy these drafts straight from the cards for now." There is no markdown pack; the .docx recap never carries email bodies.
- **Never invent action items, decisions, or dates.** If the meeting transcript is unclear, say so in the summary ("Unclear whether Bowie committed to Friday or Monday — ask").
- **Attribute carefully.** If transcript speaker labels are wrong (Granola sometimes misattributes), flag it rather than confidently draft the wrong email.
- **If the meeting is internal (only team members), skip email drafts — still produce summary + tracker entries.** Ask first if ambiguous.
- **If a decision was made that should be logged as a major decision, tag it clearly** so `decision-log` skill catches it on its next pass.

## Integration

- **Enhances `meeting-notes`** (doesn't replace). `meeting-notes` produces structured notes — this adds the close-the-loop layer on top. If `meeting-notes` already ran for this meeting, reuse its output and add the email drafts.
- **Deal-signal post-step (PIPE1 Part 2 — silent tier, MANDATORY).** After the substrate writes land, run meeting-notes Step 5h's exact recipe scoped to this meeting's counterparty org(s): `detect_deal_signals("<WORKSPACE>", org_ids=[...])` → `propose_candidates(...)` (never `run_deal_signal_job` — the Sunday job owns that receipt). When the internal meeting-notes run already executed Step 5h for THIS meeting, skip the propose call (open-fingerprint dedup makes a double-call harmless, but one call is the contract) — still render the lines: each returned candidate's `nudge_line` goes under **Logged** in the pack chat card, verbatim, every candidate (Bug #92b — no re-deciding inclusion). Zero candidates → zero lines. Propose-and-confirm only; nothing here writes a deal field.
- **Commitments** — land as `commitment` events via the internal `meeting-notes` run; MASTER_TRACKER regenerates from the substrate (never written directly).
- **Decisions** — invokes `decision-log` skill (event append; DECISION_LOG view regenerates).
- **Appends to `SESSION_NOTES_[NAME].md`** — 1-line log entry: "Closed the loop on [Meeting] — 3 follow-up emails drafted, 2 decisions logged."

## Demo Beat (May 14)

Live demo flow for the CEO group:
1. Record a 60-second mock call ("Aria, please redline the pricing by Friday. Bowie, please intro me to procurement this week. Agreed we're moving to net-30 terms.")
2. Run: "follow up on that call"
3. Artifacts appear on screen: summary, action items, 2 drafted emails, decision logged.
4. CEO reads, clicks send on one draft, done.

Total stage time: ~90 seconds. Installable mental model.

## What It Doesn't Do

- Doesn't attend the meeting or take live notes (use Granola / `meeting-notes`)
- Doesn't schedule follow-up meetings (use a calendar scheduling flow)
- Doesn't auto-send email — always drafts
- Doesn't handle multi-meeting rollups (use `weekly-recap` for that)

## Connected Tools

- **Transcript connector** (Granola / Fireflies / Otter / Read.ai / Zoom / Teams — resolved via `discover_transcript_tool()`) — transcript retrieval
- **Gmail connector** — draft creation
- **PEOPLE.md** — attendee context
- **Voice Block** (baked into this SKILL.md) — voice calibration (v3.0)
- **events.jsonl substrate** — commitment + decision logging (MASTER_TRACKER / DECISION_LOG views regenerate from it)
- **meeting-notes skill** — reuse structured notes if already generated
- **decision-log skill** — decision writer

## Routing (full trigger corpus)

The settings-trigger family for this skill, relocated verbatim from the pre-G11-diet description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Also handles first-run personalization settings — use when the CEO says 'tune follow-up-ritual', 'show follow-up-ritual settings', 'reset follow-up-ritual to defaults'.
