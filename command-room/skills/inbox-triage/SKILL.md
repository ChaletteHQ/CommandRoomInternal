---
name: inbox-triage
description: "Morning inbox pass: reads overnight email, classifies into Reply Now / Decision Needed / FYI / Discard / Deep Read. Surfaces the 3–5 that matter, drafts replies for 2–3. Triggers: 'triage my inbox', 'inbox triage', 'what's in my inbox', 'process my inbox', 'go through my email', 'email triage', 'morning email pass'. Owns all 'inbox' + deep-email phrasing. Does NOT fire on bare 'morning briefing' or 'brief me' — those go to morning-briefing for the daily digest."
---

## Skill Boundary (v2.1)

- **Owns:** deep email triage — 5-bucket classification + 2-3 drafted replies + top-of-pile ranking.
- **Pairs with `morning-briefing`** as the one-two daily start: briefing first (context), triage second (email action). Never duplicates briefing's 1-line email summaries.
- **Does NOT fire on "brief me" / "morning briefing"** — that's morning-briefing's daily digest at a summary level.
- **Does NOT fire on "draft follow-ups"** — that's follow-up-ritual (meeting context, not inbox context).

If user says "brief me and triage my inbox" — run morning-briefing first, then this skill.

## Voice Calibration

When drafting replies (Reply Now bucket + "draft a decision-needed response" flow), this skill applies `shared/VOICE_CALIBRATION.md`. Reads `_hq/VOICE_SAMPLES.md`, extracts voice markers, applies recipient modifier based on sender's entities.json record, runs the forbidden-phrase check. Drafts are never sent automatically — always returned for CEO review and one-click send.

## Writer Contract

Every email read during triage emits an inbound `interaction` event to `events.jsonl` per `shared/PASSIVE_CAPTURE.md`. Drafted replies (when sent) emit corresponding outbound interaction events. Dedup via source_ref hash prevents double-counting across morning-briefing, workspace-manager, and this skill.

**Commitment extraction (v2.7.15+).** When an email body contains explicit commitment language — either an inbound promise from a counterparty ("I'll send the deck by Friday", "I owe you the contract") or an outbound promise the user is making in a draft ("I'll get back to you with…", "Will deliver by…") — emit a `type: commitment` event alongside the `interaction`. Schema and trigger conditions in `shared/COMMITMENT_SCHEMA.md`. See "Step: Extract Commitments" below for the recipe. This is the gmail-side counterpart to `meeting-notes`'s commitment extraction; together they're the only routine producers of new commitment events for typical CEO workflow (Slack-side extraction is a v2.7.16 candidate).

---

# Inbox Triage

**For:** Operator-CEOs waking up to 80-150 overnight emails. This skill is the "eliminate my assistant" feeling they actually feel daily. Pairs with `morning-briefing` as the one-two punch that opens every day.

## What It Does

Pass over the unread / flagged inbox from a defined window (overnight, last 24 hours, since last triage). For each message:

1. **Classify** into one of five buckets:
   - **Reply Now** — short reply needed, low-friction
   - **Decision Needed** — you have to choose something before replying
   - **FYI** — read, don't reply
   - **Discard** — mark read or archive
   - **Deep Read** — needs 20+ min focus later (flag for deep-work block)
2. **Surface the 3–5 that matter most** — ranked by sender importance (CEO/board/customer > staff > vendor > other), email thread urgency, and existing commitment exposure.
3. **Draft replies for 2–3 Reply Now items** — ready to review, edit, and send from the widget (no Gmail draft is created until you act on one).
4. **Output a triage brief** — scannable in 60 seconds, with a link or draft ID next to each item.

## How to Use

```
"Triage my inbox"
"Inbox triage"
"What's in my inbox?"
"Process my inbox"
"Go through my email"
"Morning email pass"
"Email triage for the last [24 hours / week]"
```

Optional modifiers:
- Window: "overnight" (since 10pm), "since yesterday", "last 48 hours"
- Scope: "priority senders only" (limit to VIP list from `_hq/PEOPLE.md`)
- Action: "and draft the replies" (default) vs "just the brief"

## How It Works

1. **Define window (v3.13.0+ — unread is the primary inclusion criterion; time window is for ranking only).**
   - **Inclusion criterion:** `is:unread in:inbox` is the canonical query. Every unread thread is a candidate regardless of how old it is. This closes the 2026-05-20 mis-classification gap where a 28-day stale LAST_TRIAGE timestamp caused a silent collapse to a 24h window, missing an active $300K Dustin thread whose last message was 2 days old.
   - **Ranking criterion:** time window (the difference between now and LAST_TRIAGE) ranks recency within the candidate set. Threads with messages in the last few days rank higher; older threads rank lower. But age never excludes — that's the unread state's job.
   - **No silent window collapse.** Pre-v3.13.0: if `now - LAST_TRIAGE` was large, the skill silently shrunk the window to 24h. v3.13.0+: large gaps trigger a full `is:unread in:inbox` sweep, surfacing every old-but-active unread thread in the main brief body (not in a "notes for next pass" footnote).
2. **Pull unread / flagged email** via Gmail connector.
3. **Enrich each message.**
   - Sender importance: VIP if in `_hq/PEOPLE.md` with `tier: board|investor|customer|top-vendor`; otherwise rank by historical reply frequency
   - Project context: existing OPEN commitments tied to the project this email belongs to. **Use `shared/scripts/cru_match.py::load_open_commitments(events.jsonl_path)`** filtered by `primary_thread_id` or by counterparty `person_id` — NOT MASTER_TRACKER (per `references/SOURCE_OF_TRUTH.md`, MASTER_TRACKER is a Tier 2 view and may be stale). `load_open_commitments` is the canonical reader: it handles all 5 commitment-event shape variants and treats both `commitment_resolved` and `thread_resolved` as valid closers, so commitments that fired through the dashboard ✓ done path are correctly filtered out.
   - Urgency signals: deadlines mentioned in the body, explicit "need by…" phrasing
3.5. **`get_thread` BEFORE classifying state (v3.13.0+ MANDATORY — closes the "stalled on you" inversion bug).**

   Pre-v3.13.0 this skill derived thread state from `search_threads` results alone — which return a TRUNCATED, NON-LATEST slice of the thread (a snippet from an older matching message, NOT the newest message). On 2026-05-20 this produced a load-bearing failure: the Dustin / Adan Designs thread (active $300K offer cluster) was filed as *"stalled — no reply from you in 10 days"* when the actual state was that Dustin owed M the next deliverable (M replied May 18, Dustin confirmed he'd build it out — ball was in his court, not stalled on M's).

   **The rule:** before asserting "needs reply" / "Reply Now" / "stalled" / "no reply in N days" / "awaiting them" / who-owes-the-reply for any thread, call `get_thread(threadId, messageFormat=FULL_CONTENT)` and read the LAST message in the returned `messages` array. Do NOT infer state from `search_threads` snippets or the search result's partial `messages` list.

   **Determining ball-in-court from the latest message:**
   - If the newest message has `SENT` in `labelIds` OR `sender == matthew@chaletteholdings.com` (the user's address): **the user has already replied → classify as "awaiting counterparty" / "owed-to-you"**, NOT "Reply Now" or "stalled on you".
   - Only classify "Reply Now" / "stalled on you" when the newest message is INBOUND (from the counterparty).
   - When the newest message is inbound AND contains a forward-looking promise from the counterparty ("I'll send X by Y", "Will deliver…"), emit a `type: commitment` event (owed-to-you) per `shared/COMMITMENT_SCHEMA.md` instead of a reply prompt.
   - **Calendar-close exception (v3.14.7+).** The latest-message check only sees EMAIL replies. A scheduling thread ("can we set a time?", "propose times", "Monday works") usually closes on the CALENDAR — the user replies by creating an invite, so the newest *message* stays inbound and this would mis-file it as "Reply Now". Before classifying a scheduling-flavored thread (detect via `cru_match.detect_scheduling_intent` on the subject/last message, or obvious phrasing — "set a time / propose times / when works / lock / book / move the call") as Reply Now, check the calendar (native Calendar MCP `list_events`, never Zapier per `EMAIL_DRAFT_PROTOCOL.md` §3c) for an event with that counterparty. If the user organized an event created/updated at or after the counterparty's last message, OR the counterparty has `accepted` an invite, the loop is closed → classify as **owed-to-you / handled**, NOT Reply Now. This mirrors morning-briefing Step 3c-bis and the Path 5 substrate resolver (`cru_match.match_calendar_to_commitments`).

   **Performance gate (open question from the 2026-05-20 handoff):** `get_thread` on every non-bulk thread adds N calls per run. Recommended scope: expand only threads whose classification depends on direction (i.e., would otherwise be "Reply Now" or "stalled") + any thread matched against an entities.json counterparty. Bulk/marketing senders skip the expansion (they classify as Discard from the search-result snippet alone).

   **No human thread relegated to footnote without expansion.** If a thread surfaces in any search and involves a counterparty resolvable in entities.json (or any non-bulk sender), force a `get_thread` expansion before the brief is written. Footnote / "Notes for next pass" is reserved for bulk/marketing senders only.
4. **Classify.** Use the five-bucket model:
   - Reply Now: short answer fits in 3 sentences, no decision required. Per Step 3.5 above: **only when the newest-message direction is INBOUND**.
   - Decision Needed: requires the CEO to choose
   - FYI: informational, no expected response
   - Discard: newsletter, marketing, auto-alerts, confirmed-resolved threads
   - Deep Read: dense content, long documents attached, requires focus
5. **Rank the top of the pile.** Surface 3-5 items — these are the ones the CEO reads first. Everything else listed in an appendix.
6. **Draft Reply Now replies.** 2-3 drafts max (more than that and drafts become noise). Voice-calibrated via `_hq/VOICE_SAMPLES.md` if available.
7. **Write the triage brief.**
   - Save: `_hq/inbox/TRIAGE_[YYYY-MM-DD_HH-MM].md`
   - Record timestamp in `_hq/inbox/LAST_TRIAGE.txt`
8. **Return:** file link + headline ("12 overnight emails. 3 top items flagged. 2 replies drafted. 1 decision needed: Acme pricing.")

## Step: Extract Commitments (MANDATORY in every triage run)

After classifying each email but before writing the brief, scan each message body for **commitment language** — forward-looking promises about a specific deliverable made by an identifiable owner. For each match, append one `type: commitment` event to `_hq/data/events.jsonl` per the canonical schema in `shared/COMMITMENT_SCHEMA.md`.

### Direction matters — who owes whom

| Pattern in email body | Direction | Owner |
|---|---|---|
| Inbound from counterparty: "I'll send X by Y" | They owe you | counterparty's `person_id` |
| Inbound from counterparty: "I owe you the …" | They owe you | counterparty's `person_id` |
| Inbound from counterparty: "Will deliver …" | They owe you | counterparty's `person_id` |
| Outbound (draft user is sending): "I'll send X by Y" | You owe them | user's `person_id` |
| Outbound: "I'll get back to you with …" | You owe them | user's `person_id` |
| Inbound: "Can you …?" with no commitment back yet | Skip — not a commitment until accepted |

### Trigger phrases (non-exhaustive)

- "I'll [verb] [thing] by [date]"
- "I owe you [thing]"
- "Will [verb] [thing]"
- "I'll get back to you with …"
- "Will deliver [thing] by [date]"
- "Sending [thing] [day]"
- "Action item: [name] — [verb]"

Vague phrases ("I'll think about it", "let's circle back", "we should consider") DO NOT qualify. See `shared/COMMITMENT_SCHEMA.md` § "Extraction triggers" for the full list of qualifying vs disqualifying patterns.

### Field mapping

For each qualifying email:

```json
{
  "type": "commitment",
  "source_skill": "inbox-triage",
  "primary_thread_id": "<project this email belongs to — same as the interaction event>",
  "classification_confidence": <inherited from the interaction event>,
  "person_ids": ["<owner_id>", "<counterparty_id>", "<user_id>"],
  "ts": "<email send/receive ISO timestamp>",
  "data": {
    "owner_id": "<resolved per the table above>",
    "title": "<short verb-phrase summarising the deliverable, ≤120 chars>",
    "due": "<ISO date if explicit; empty if not>",
    "status": "open" | "overdue",
    "source_event_seq": <seq of the interaction event for this email>,
    "source_ref": "gmail:<message_id>",
    "evidence": "<quoted phrase from email body, ≤200 chars>"
  }
}
```

**Status:** if `due` is parsed and is in the past relative to today (UTC), set `"overdue"`. Otherwise `"open"`.

**Owner resolution:** use `aliases.json` to canonicalize sender's display name / email to a `person_id`. If the email is from someone not yet in entities.json, surface a one-line suggestion in the brief ("💡 [Sender] isn't on file — add to people?") but DO NOT skip the commitment — emit it with `owner_id: ""` so it's not lost.

**Dedup:** Match on `(source_ref, title)`. The same email shouldn't produce two equivalent commitments across re-runs. Skip if `(gmail:<message_id>, title)` already exists for a `type: commitment` event in events.jsonl.

> **Sent-mail reconciliation is NOT done here (v3.18.12 — Bug #98-v3).** Closing commitments the CEO completed by emailing directly is the dedicated silent `reconcile-sent` task's single job (it fires 6:45 AM). It was briefly folded into this triage pass (v3.18.11) and got skipped in real use — same structural reason the brief skipped it: an invisible substrate write loses to the visible deliverable. Don't re-add it here. Extract NEW commitments above; the `reconcile-sent` task closes the ones already sent.

### Surface in the triage brief

Add one line to the brief output:

```
## Commitments Captured This Run
- 3 they owe you (Aria will send pricing by Fri, Bowie will redline MSA, Carol will introduce VC)
- 1 you owe (reply to Sam with Q3 plan by Mon)
```

If zero commitments captured, omit the section — don't print "0 commitments".

---

## Output Structure (saved triage brief file)

```
# Inbox Triage — [YYYY-MM-DD HH:MM]
Window: [start → end] | Unread: [N]

## Top of the Pile
1. **Reply Now** — Aria (Acme) — "pricing redline?" — Draft ready
2. **Decision Needed** — Bowie (Board) — "approve the new hire?" — Your call. [1-line context]
3. **Reply Now** — Lyra (customer) — "can we move the call?" — Draft ready
4. **Deep Read** — Legal — "redlined MSA" — 14 pages. Suggest: [time block]
5. **FYI** — Team — "release notes" — no action

## By Bucket
- Reply Now (5): [list with subject + sender]
- Decision Needed (2): [list]
- FYI (12): [list — safe to bulk-archive]
- Discard (47): [bulk action — mark read?]
- Deep Read (3): [list with attachment size]

## Reply Drafts (review + send in the widget)
- Reply to Aria (pricing) — drafted; fires to Gmail on your click
- Reply to Lyra (call reschedule) — drafted; fires to Gmail on your click
```

## Chat Output Format (v3.13.1+ — editable widget for Reply Now drafts)

**Follows `shared/EMAIL_DRAFT_PROTOCOL.md`** (v3.13.0+ universal scope — every recipient-bound email draft surface follows the same protocol, whether the trigger is scheduled or on-demand).

**Reply Now drafts surface as an editable widget, not as blockquote previews.** Per M's 2026-05-20 feedback #15 ("for threads to revive, it's also generating emails without the widget") plus #13/#14 ("if it is to be sent to sender it should open in the widget"). Pre-v3.13.1 inbox-triage rendered each draft as a `> To: / > Subject: / > Body` blockquote that forced back-and-forth chat-turn edits. v3.13.1+ uses the canonical email-writer widget cascade — one widget item per Reply Now draft, with `send / edit then send / draft / skip` actions inline. Edit happens on the widget; no chat-turn round-trips required.

**Construct the widget the same way email-writer does (per `skills/email-writer/SKILL.md` Phase 4 — single shared pattern, do not re-invent).** Use a multi-item `all_batch_widget` with one item per Reply Now draft. Each item carries email-shaped metadata (To / Subject as a LIST of `[key, value]` pairs — not a dict, not packed into `name`):

```python
items = []
for i, draft in enumerate(reply_now_drafts, start=1):
    items.append({
        "n": i,
        "icon": "✉️",
        "name": draft["recipient_display_name"],
        "metadata": [
            ["To", draft["recipient_email"]],
            ["Subject", draft["subject"]],
        ],
        "context_tag": "Reply to a thread in your inbox",
        "body_lines": [f"> {line}" for line in draft["body_paragraphs"]],
        "actions": [
            f"{i} send",
            f"{i} edit then send",
            f"{i} draft",
            f"{i} skip",
        ],
    })

data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"Replies ready — {len(items)} draft{'s' if len(items)!=1 else ''} for you to review",
    "sub_header": "Review, edit, send, or skip right here.",
    "sections": [{"title": None, "count": None, "items": items}],
}
```

**Render + post:**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from chat_output_renderer import render_chat_output_widget, validate_rendered_widget
data_view = json.loads('''<DATA_VIEW_JSON>''')
html = render_chat_output_widget(data_view, wrapper='fragment')
validate_rendered_widget(html)
print(html)
"
# Pass rendered HTML to mcp__visualize__show_widget byte-for-byte.
```

**Action semantics** — same lazy contract as email-writer Phase 4 (per `shared/EMAIL_DRAFT_PROTOCOL.md` §1). The draft text lives in the widget; NO Gmail draft exists until the user acts:
- `N send` — apply-choices creates the Gmail draft and sends it in one motion (native Gmail MCP `create_draft` → `send_draft`, or Zapier-threaded send if `In-Reply-To` is set). Logs `email_drafted` + `email_sent`.
- `N edit then send` — inline edit input on the widget; on Apply, the send fires.
- `N draft` — apply-choices creates the Gmail draft on click; it lands in Gmail Drafts for later. Logs `email_drafted`.
- `N skip` — NO Gmail call (nothing was created at fire time). Records a `chat_dismissal` event.

**Decision Needed / Deep Read / FYI items do NOT get the widget surface** — those don't carry a draft to send. Decision Needed renders as a normal list item with a "decide" action; Deep Read as a list with attachment notes; FYI as a one-line summary.

**Sources section stays.** Below the widget, append the canonical `Sources:` section linking each triaged thread per `_hq/CONVENTIONS_SOURCE_LINKS.md`. Use the URL the Gmail MCP returns on `get_thread` / `search_threads` — never synthesize. Format: `[Sender — short subject](https://mail.google.com/mail/u/0/#all/{thread_id})`.

```markdown
Sources:
- [Aria (Acme) — pricing redline?](https://mail.google.com/mail/u/0/#all/198abcd...)
- [Skyler — call reschedule](https://mail.google.com/mail/u/0/#all/198defg...)
```

If no sources were referenced (rare), omit the section.

**Saved triage brief file** (the .docx output, separate from the chat widget) — the brief lists the reply drafts under "Reply Drafts" by recipient + subject. No `gmail://drafts/<id>` URLs are stamped at fire time, because under lazy creation no Gmail draft exists until the user clicks `draft`/`send` (per `shared/EMAIL_DRAFT_PROTOCOL.md` §1). The body of each draft does NOT need to appear inside the brief — the widget carries it in chat. (Same simplification follow-up-ritual got in v3.13.0 — the .docx stopped embedding the email body once the widget became the editing surface.)

## Triggers

- "triage my inbox"
- "inbox triage"
- "what's in my inbox"
- "process my inbox"
- "go through my email"
- "email triage"
- "morning email pass"

## Gotchas

- **Drafts only, never auto-send.** Non-negotiable.
- **Never auto-archive anything.** Classify yes — archive no. User does the archiving pass.
- **If Gmail connector isn't available, stop early** with: "Inbox triage needs Gmail. Connect Gmail and run again." Don't try to work around it with Outlook heuristics.
- **Respect VIP classification.** If the sender is on `_hq/PEOPLE.md` with a top-tier mark, never drop them into Discard — push them up the ranking even if the body is short.
- **Never classify based on subject line alone.** Read the body — a two-line subject can be critical, a 400-word body can be noise.
- **If the CEO's Reply Now drafts conflict with a decision in Decision Needed**, pause on those drafts and flag the dependency ("Can't draft reply to Aria — depends on board decision in item 2").
- **If inbox > 200 messages, warn and ask whether to run** — this may take a minute and hit rate limits. Offer to restrict to VIP senders only.
- **Don't over-classify.** Err toward Reply Now + Decision Needed getting smaller rather than padding the "top 5" with weak items.

## Scheduling

After first successful run, offer: "Want me to run this every weekday at 7am and drop it in your morning briefing?" — use `schedule` skill. Pairs naturally with `morning-briefing` (#28).

## Integration

- **Pairs with `morning-briefing`** — inbox-triage output can be embedded as a section of the briefing
- **Pulls from `_hq/PEOPLE.md`** for VIP ranking (Tier 2 view per `references/SOURCE_OF_TRUTH.md` — fine for static "who is a VIP" tier lookup; not used for "what's outstanding")
- **Pulls open commitments from `_hq/data/events.jsonl`** via `cru_match.load_open_commitments` (canonical Tier 1 source). NOT from MASTER_TRACKER — see `references/SOURCE_OF_TRUTH.md` overlay rule.
- **Drafts via Gmail connector** — never direct send
- **Voice from `_hq/VOICE_SAMPLES.md`** (optional)

## Demo Beat (May 14)

Live with the CEO's own inbox during the pitch (if they're comfortable):
1. "Triage my inbox"
2. 15 seconds later: 3 items surface, 2 drafts ready
3. CEO reads — "that's exactly the three I'd have picked"
4. Open one draft in Gmail, click send

If live inbox is too sensitive, use a demo inbox seeded with representative messages.

## What It Doesn't Do

- Doesn't auto-send, auto-archive, or auto-delete
- Doesn't build long-form replies (use `one-pager-composer` or write in Gmail)
- Doesn't track outbound emails (separate concern)
- Doesn't handle Outlook (Microsoft 365 connector, future) — stub for now

## Connected Tools

- **Gmail connector** (required)
- **PEOPLE.md** — VIP ranking (Tier 2 view; static-tier lookup only)
- **`_hq/data/events.jsonl`** — open-commitment overlap (Tier 1 source, read via `cru_match.load_open_commitments`)
- **VOICE_SAMPLES.md** (optional) — reply voice
- **schedule skill** — recurring run
- **morning-briefing skill** — embedding target
