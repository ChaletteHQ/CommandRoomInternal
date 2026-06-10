---
name: follow-up-ritual
description: "Meeting transcript or recording → 60-second close-the-loop pack: summary, per-attendee action items, personalized follow-up email drafts ready to send. Triggers: 'follow up on that call', 'follow up the meeting', 'follow up on', 'process the call and draft follow-ups', 'close the loop', 'close the loop on', 'follow-up ritual', 'draft follow-ups', 'draft follow ups', 'send follow-ups from my last call'. Owns ALL 'follow up' + meeting-context phrasing — meeting-notes does not fire on these."
voice_block_last_refreshed: 2026-04-21
calibration_level: default
template_version: 2.7.1
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving any attendee from the meeting transcript or trigger phrase, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. For the action-item / commitment scan that feeds the follow-up pack, use `shared/scripts/cru_match.py::load_open_commitments` — do NOT hand-roll an events.jsonl scan. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract.

## Skill Boundary (v2.2)

- **Owns:** post-meeting close-the-loop — logging + drafted emails + tracker/decision updates in one pass.
- **Invokes `meeting-notes` internally** for the structural logging step (including v2.2 thread classification with `primary_thread_id` + `related_thread_ids[]` + `classification_confidence`), then adds the email-draft layer on top. Never produces duplicate SESSION_NOTES entries.
- **Does NOT fire on "prep me for…"** — that's `call-prep`.
- **Does NOT fire on bare "meeting notes"** without follow-up language — that's `meeting-notes` standalone.
- **Inherits thread classification from meeting-notes.** The meeting's primary thread determines the folder for the follow-up pack; related threads get cross-ref lines. Follow-up-ritual does not run its own classifier.

If user says "just log the meeting, no follow-ups" — route to meeting-notes. If user says "process the call and draft follow-ups" — this skill.

## Voice Protocol (v3.0 — v2.7.1 architecture)

This skill follows the two-step draft-then-critique protocol defined in `shared/VOICE_CALIBRATION.md`. Voice lives in the `## Voice Block` section of this SKILL.md — NOT in a separate `VOICE_SAMPLES.md`. That file is deprecated as of v2.7.1.

Every follow-up email draft:
1. Uses this skill's Voice Block (cadence, openers, vocabulary, punctuation, taboos — tuned for post-meeting follow-up register).
2. Applies recipient modifier based on the attendee's `entities.json` record (board/peer/customer/team/vendor).
3. Passes the Step 2 critique pass against the Voice Block + universal banned-phrase list.
4. Strips any banned LLM tells before return.

Corrections on user-edited drafts append to `_hq/voice/corrections-follow-up-ritual.jsonl`. The corrections corpus drives the next Voice Block refresh when the operator runs the calibration protocol.

## Personification Contract (v3.13.8.4+)

Before surfacing the pack-drafted acknowledgment, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The chat acknowledgment after drafting the pack uses the shape `"Drafted {N} follow-ups for you, {first_name} — {brain_name} pulled action items from `[Meeting Name]`. Ready to review."` Outbound EMAIL drafts close with the customer's own signature block (read from `workspace.user_signature`), NEVER the brain_name — the brain_name appears only in the chat acknowledgment, never in the email body the recipient sees. Default `{brain_name}` = `"Penelope"`.

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
4. **Decisions log entry** — auto-appended to `_hq/DECISIONS.md` if any decisions were made
5. **Tracker updates** — new commitments appended to `_hq/MASTER_TRACKER.md`

The demo-critical moment: a 60-second mock call → artifacts generated on screen → user hits send.

This is the group's #1 stated pain (Bo's direct feedback). It's the demo beat of the May 14 CEO-group pitch.

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
   - Identify each attendee with role context (pull from `_hq/PEOPLE.md` if available).
   - Surface explicit decisions, not just discussion.
   - Attribute action items by owner + due date. If no date was stated, mark TBD, don't invent.
3. **Pull voice.**
   - Use the baked-in `## Voice Block` in this SKILL.md (Voice Protocol v3.0) as the sole voice source — do not read external `VOICE_SAMPLES.md` files.
   - Default register for uncalibrated workspaces is sharp and warm ("Good talk. Quick recap — …").
4. **Draft the follow-up emails.**
   - One per attendee who owns ≥1 action item.
   - Subject: "Follow-up: [Meeting topic] — [Date]"
   - Body: 1-sentence opener → bullet list of their actions → their dates → sign-off.
   - If a decision concerns them, surface it in one line.
5. **Write artifacts.**
   - Save the meeting-recap pack to `[Primary Thread folder_name]/meetings/FollowUp_[Meeting]_[YYYY-MM-DD].docx` (primary thread comes from meeting-notes classification — `.docx` per CONTRACT Rule 27, no .md deliverables). **v3.13.0+ — the .docx pack does NOT include the email body.** Per M's 2026-05-20 feedback #13: the document is the synthesis/recap; the emails are a separate artifact that belong in the widget where they can be edited. Email bodies stay in the chat-action widget (step 6) and Gmail Drafts only.
   - For each `related_thread_ids[]`, append a cross-ref line to that thread's session notes per the `meeting-notes` Step 4 convention.
   - Append decisions to `_hq/DECISIONS.md` (invoke `decision-log` skill).
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
                   "context_tag": "Ready for your review — Gmail draft fires on click",
                   "body_lines": [f"> {line}" for line in draft.body.split(chr(10))],
                   "actions": [f"{i} send", f"{i} edit then send", f"{i} draft", f"{i} skip"],
               }
               for i, draft in enumerate(per_attendee_drafts, start=1)
           ],
       }],
   }
   html = render_chat_output_widget(data_view, wrapper="fragment")
   validate_rendered_widget(html)
   # Pass html byte-for-byte to mcp__visualize__show_widget.
   ```

   After posting the widget, surface the .docx recap link separately at the bottom per `shared/CONTRACT.md` Rule 3 (H2 link format): `## → **[Follow-up recap — {meeting_topic} ({meeting_date})](computer:///<URL-encoded-path>)**`. One-line summary above the link: "Drafted 3 follow-ups for Aria, Bowie, and Lyra. Logged 2 decisions. Added 4 new commitments to your tracker."

## Step: Surface Open Commitments (v2.7.15+)

Before drafting follow-up emails, scan `_hq/data/events.jsonl` for **open commitments** that involve the meeting's attendees. The pack must surface them so the user knows what's already on the floor before adding more — and so each attendee's follow-up email can include a "still open from before" line where relevant.

### Read recipe

For each attendee `person_id`, find events where:
- `type == "commitment"` AND
- `data.status` (or top-level `status`, for legacy events) is `"open"` or `"overdue"` AND
- the commitment hasn't been closed by a later `commitment_resolved` / `thread_resolved` event referencing its id AND
- `data.owner_id == <attendee_person_id>` OR `data.owner_id == <user_id>` for outbound (you owe this attendee)

Group by attendee. For each attendee with ≥1 open commitment, include a "Still open" subsection in their per-attendee section of the pack:

```
### Still open with [Attendee]
- They owe: [title] (due [date], aged [N] days)  ← from prior meeting/email
- You owe: [title] (due [date])
```

If the meeting just closed one of these (e.g., the attendee delivered the thing or the user did), append a `commitment_resolved` event referencing the prior commitment's id. Use the canonical resolution shape from `shared/COMMITMENT_SCHEMA.md`:

```json
{
  "type": "commitment_resolved",
  "source_skill": "follow-up-ritual",
  "primary_thread_id": "<same thread>",
  "data": {
    "commitment_id": "<id of the original commitment event>",
    "resolved_by": "<person_id>",
    "evidence": "<one-line reason — 'delivered in this meeting' or 'mentioned as done in transcript'>"
  }
}
```

This is how commitments stop accumulating in views over time — without resolution events, every commitment stays "open" forever.

### Surface count in the pack header

Add to the pack header line: `"Open commitments going in: 3 (2 they owe, 1 you owe). Closed in this call: 1."`

If zero on either side, omit that half of the line.

---

## Output Structure

`FollowUp_[Meeting]_[YYYY-MM-DD].docx` content structure (rendered via brief_writer):

```
# Follow-Up: [Meeting Name]
Date: YYYY-MM-DD | Attendees: Aria, Bowie, Skyler | Project: [Project]
Open commitments going in: 3 (2 they owe, 1 you owe). Closed in this call: 1.

## Summary
[3-5 sentence recap]

## Decisions
- [Decision 1]
- [Decision 2]

## Action Items
| Owner | Action | Due |
| Aria | Send pricing redline | Apr 25 |
| Bowie | Intro me to procurement | This week |
| Skyler | Circulate revised SOW | Apr 24 |

## Drafted Emails
### To: Aria <aria@...>
Subject: Follow-up: [Topic] — Apr 20
[Body]

### To: Bowie <bowie@...>
...

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
- **If Gmail connector isn't available, output the email bodies in the markdown pack** with a friendly line at the top: "Gmail isn't connected yet — you can copy these drafts from below for now."
- **Never invent action items, decisions, or dates.** If the meeting transcript is unclear, say so in the summary ("Unclear whether Bowie committed to Friday or Monday — ask").
- **Attribute carefully.** If transcript speaker labels are wrong (Granola sometimes misattributes), flag it rather than confidently draft the wrong email.
- **If the meeting is internal (only team members), skip email drafts — still produce summary + tracker entries.** Ask first if ambiguous.
- **If a decision was made that should be logged as a major decision, tag it clearly** so `decision-log` skill catches it on its next pass.

## Integration

- **Enhances `meeting-notes`** (doesn't replace). `meeting-notes` produces structured notes — this adds the close-the-loop layer on top. If `meeting-notes` already ran for this meeting, reuse its output and add the email drafts.
- **Writes to `MASTER_TRACKER.md`** — new commitments appended per workspace-manager's activity log protocol.
- **Writes to `DECISIONS.md`** — invokes `decision-log` skill for any decisions.
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
- Doesn't handle multi-meeting rollups (use `cleanup` for that)

## Connected Tools

- **Granola MCP** — transcript retrieval
- **Gmail connector** — draft creation
- **PEOPLE.md** — attendee context
- **Voice Block** (baked into this SKILL.md) — voice calibration (v3.0)
- **MASTER_TRACKER / DECISIONS.md** — commitment + decision logging
- **meeting-notes skill** — reuse structured notes if already generated
- **decision-log skill** — decision writer
