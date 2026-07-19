---
name: thread-resurrection
description: "Surface conversations — email threads, Slack threads, meeting follow-ups — that went silent but carried high-value context worth reviving. Fires on: 'thread resurrection', 'warm threads to revive', 'dead threads worth reviving', 'what conversations went quiet', 'threads I dropped'. Ranks by value signals (deal language, decision proximity, seniority, thread depth) against silence duration, and offers one-tap revival drafts in the CEO's voice. Different from dormant-customer-scan, which finds dormant PEOPLE — this finds dormant CONVERSATIONS; a person can be active while a thread died. Does NOT fire on 'who went dark' (dormant-customer-scan), 'who should I reach out to' (relationship-moves), or 'follow up with [name]' (follow-up-ritual / email-writer). Ranking signals and fences: Routing section in the body."
---

## Entity-resolve + canonical-helper enforcement (mandatory, v3.13.8+)

Before resolving any person / org / project from loose input in your trigger phrase or arguments, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)`. Only after the resolver returns NO candidates may you fall back to substring grep — and that fallback MUST be flagged to the user, not silently surfaced as a single result. For commitment / event surface, call `shared/scripts/cru_match.py::load_open_commitments` — do NOT hand-roll an `events.jsonl` scan. See `shared/ENTITY_RESOLVE_PROTOCOL.md` for the full contract + rationale (the bug class this closes).

## Skill Boundary (v2.1)

- **Use thread-resurrection for:** finding specific high-context THREADS (email or Slack) that died mid-discussion. The unit of analysis is the thread.
- **Use `dormant-customer-scan` for:** finding PEOPLE (or customer orgs) that have gone quiet vs their historical cadence. Different unit of analysis.
- **Use `follow-up-ritual` for:** post-meeting follow-up after a recent meeting. This skill is for surfacing the old threads that never got followed up on.
- **Use `email-writer` directly** for drafting a one-off email to someone (no thread context).

## Writer Contract (v3.8.0+ — substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Appends to:**
- `_hq/data/events.jsonl` — event type `thread_resurrected` when the user clicks "Draft revival" and the draft is created. Carries `{thread_ref, last_msg_ts, days_silent, revival_draft_event_seq, resurrection_hook}`. The `revival_draft_event_seq` points at the `email_drafted` event the chained `email-writer` invocation produced.
- `_hq/data/entities.json` — `relationship.last_touched_at` bumped on the counterparty record(s) when the revival email is sent.

**Reads from:**
- `_hq/data/events.jsonl` — `type == "interaction"` events (the thread activity log) to compute per-thread last-activity and days-silent.
- `_hq/data/events.jsonl` — `type == "meeting"` events to find meetings whose follow-up emails were never sent (no subsequent `interaction` event with `direction == "outbound"` from the host to attendees in the 7 days after the meeting).
- `_hq/data/entities.json` — for the thread → person → org → project graph. "High-context" means: thread touches an active project OR a high-value person (relationship strength tier 1-2) OR an explicit commitment.
- `_hq/data/events.jsonl` — `type == "commitment"` events with `status == "open"` involving thread participants — these get surfaced as "use commitment chase instead" alternative actions.
- `_hq/data/events.jsonl` — prior `thread_resurrected` events to avoid re-surfacing recently revived threads.

**Conflict boundary:** sole writer of `thread_resurrected` events. The chained `email-writer` invocation writes the `email_drafted` event.

---

# thread-resurrection

The companion to `dormant-customer-scan`. Dormant-customer-scan finds dormant PEOPLE (relationship cadence breaks). This skill finds dormant THREADS — specific conversations that died mid-step, where reviving the thread is a different action than reviving the relationship.

## What It Does

Scans interaction events to identify threads where (a) the thread has substantive context (multi-turn, project-tagged or commitment-tied), (b) the thread has been silent for 14+ days, and (c) the silence isn't an intentional resolution (no `thread_resolved` event on the thread_ref).

For each candidate thread, surfaces:
- Last message text excerpt + sender
- Attendees + days silent
- Why this thread matters (project, open commitment, high-value person)
- Suggested revival hook tuned to thread context

Ranks by score (commitment-bearing > project-tagged > high-value-person > generic-context). Top 5 in a widget with per-thread action set.

## How to Use

```
"what conversations went silent"
"warm threads to revive"
"thread resurrection"
"threads to revive"
"conversations to restart"
"what's gone quiet that I should restart"
```

Runs on-demand. Schedulable weekly as a Pulse companion surface.

## How It Works

### Phase 1 — Load candidate threads

Read `_hq/data/events.jsonl` for `type == "interaction"` events. Group by thread (Gmail thread-id, Slack thread-ts, or meeting follow-up cluster). For each thread:
- `last_activity_ts` = max(ts of events in thread)
- `days_silent = (now - last_activity_ts).days`
- `direction_of_last_msg` = direction of the latest interaction event
- `attendees` = union of person_ids across events in thread

Filter to `days_silent >= 14` AND no `thread_resolved` event on the thread_ref.

**Live-check gate (MANDATORY — defined here; REL1 below assumes it).** For each surviving thread, call `shared/scripts/live_contact_check.py::live_contact_check()` on the thread's counterparty (same helper and MUST-language as dormant-customer-scan): overlay live Gmail + Calendar signal on the substrate math BEFORE surfacing. If the live check finds a touch newer than `last_activity_ts` (a reply the substrate missed, a meeting on the calendar), the thread is not silent — DROP it and emit no dormancy signal. Substrate-only resurrection pitches for threads that already resumed are the failure mode this gate closes.

**REL1 — emit the normalized dormancy signal (absolute tier).** For each thread that passes this filter (and its live-check), call `shared/scripts/dormancy.py::emit_dormancy_signal(workspace_root, entity_id=<thread_ref>, entity_type='thread', gap_days=<days_silent>, baseline_days=None, source_skill='thread-resurrection')` — null baseline maps to the absolute 14/30/60-day tiers. The legacy `days_silent >= 14` filter is unchanged.

### Phase 2 — Score for high-context

For each candidate:
- `commitment_score` = +3 if any open commitment event references thread attendees AND mentions thread topic
- `project_score` = +2 if thread is tagged to an active project
- `relationship_score` = +(3 - relationship_tier) — tier 1 person = +2, tier 2 = +1, tier 3+ = 0
- `multi_turn_score` = +1 per turn beyond the third (caps at +3)
- `direction_score` = +1 if the last message was FROM the counterparty (you're the one who hasn't replied; you have leverage to revive)

`total = commitment + project + relationship + multi_turn + direction`

Threshold: `total >= 4` to enter the surface set. Top 5 by score.

### Phase 3 — Build revival hooks

For each surfaced thread, compose a revival hook tuned to thread context. Every hook is SENDABLE text — a message the user could fire as-is, never a strategy note ("nudge them with X" is a note, not a hook). Hooks must also pass the voice-tell gate — "circle back" / "touching base" are banned phrases:
- Open-commitment thread: "Where did we land on [commitment topic]? Any movement?"
- Project-tagged thread: "Where did we land on [topic]?"
- High-value person thread: "Following up on our [date] conversation — [last-message-paraphrase]"
- Multi-turn died-mid-discussion: "Coming back to your [date] note on [topic] — [reframe]"

### Phase 4 — Render widget (v3.13.2+ — canonical action widget per CONTRACT Rule 5)

Surface results via `render_chat_output_widget`. Each candidate thread renders as one item with the canonical action set: `draft / resolved / add to my list / skip`. No bracket-style display labels — those crash the renderer's canonical-action validator. Conditional routing for commitment-bearing threads is handled INSIDE the `draft` action's apply-choices dispatch (see "Action semantics" below) — not as a separate per-item verb.

**Email-draft protocol compliance (v3.13.0+ universal scope per `shared/EMAIL_DRAFT_PROTOCOL.md`):** thread-resurrection follows the protocol VIA the chained `email-writer` invocation. The Phase 4 widget surfaces thread-selection (not the email body), so it doesn't directly emit email-shaped metadata. When the user clicks `draft` on a thread item, apply-choices chains to email-writer, which emits the actual email widget per the protocol §3a/§3c (lazy creation, native Gmail / Zapier-threaded send). Both this widget and email-writer's downstream widget meet CONTRACT Rule 5 + the protocol's lazy-creation rules.

**Data view shape (multi-item `all_batch_widget`):**

```python
items = []
for i, thread in enumerate(candidate_threads, start=1):
    counterparty_label = thread["counterparty_display_name"]
    title_clause = thread["thread_topic"]  # e.g., "partnership exploration"
    days_silent = thread["days_silent"]

    # Per-thread context lines (rendered as body_lines)
    body_lines = [
        f"Last from {thread['last_sender_short']}: \"{thread['last_excerpt']}\"",
        f"Last from you: {thread['your_last_topic_summary']}",
        f"Suggested opener: {thread['revival_hook']}",
    ]
    if thread.get("open_commitment"):
        body_lines.append(f"{thread['debtor_short']} still owes you: {thread['open_commitment']['title']} ({thread['open_commitment']['days_past']} days past)")

    items.append({
        "n": i,
        "icon": "💬",
        "name": f"{counterparty_label} — {title_clause}",
        "context_tag": f"quiet for {days_silent} days",
        "body_lines": body_lines,
        "actions": [
            f"{i} draft",
            f"{i} resolved",
            f"{i} add to my list",
            f"{i} skip",
        ],
    })

data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"{len(items)} conversation{'s' if len(items)!=1 else ''} worth reviving",
    "sub_header": "Pick which conversations to revive — I'll draft each one.",
    "sections": [{"title": None, "count": None, "items": items}],
}
```

**Render + post (same pattern as email-writer Phase 4):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from widget_transport import render_and_persist
data_view = json.loads('''<DATA_VIEW_JSON>''')
transport = render_and_persist(data_view=data_view, wrapper='fragment',
                               persist_dir='<WORKSPACE>/_hq/.system/widgets',
                               name_hint='thread-resurrection')
print(transport['html'])
"
# Pass the rendered HTML (transport["html"]) to mcp__visualize__show_widget as widget_code (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

**Action semantics** (per `apply-choices`):
- `draft` — kicks off the revival-email composition. Chains to `email-writer` with the thread context + revival hook pre-filled. The email-writer widget posts as the post-Apply surface so the user can edit/send/draft inline. Writes `thread_resurrected` event with `revival_draft_event_seq` pointing at the new `email_drafted` event. **For commitment-bearing threads** (where `open_commitment` was set in the data view), apply-choices routes through `cr-commitments`'s chase-email flow instead — same `draft` verb, conditional downstream. The decision rule is in the apply-choices handler, not the user-facing widget: the user just clicks `draft`, the system picks the right flow.
- `resolved` — writes a `thread_resolved` event. The thread is closed; no revival.
- `add to my list` — writes a `commitment_to_discuss` event so the thread surfaces in the user's discuss list (per `show-my-list` SKILL). Defers without resolving.
- `skip` — 24h dismissal (`chat_dismissal` event). Re-surfaces on the next thread-resurrection run after 24h.

**Why `draft` covers the commitment-chase case** (per CONTRACT Rule 5 — no improvised action verbs): both flows produce an email draft from the user's perspective. The internal routing (revival hook vs. chase prompt) is plumbing, not UX. Two verbs for the same user-facing outcome would force a choice the user doesn't need to make.

## Output Structure (widget — what the rendered surface looks like)

The widget renders as a Cowork action card. Conceptually, what the user sees:

```
3 conversations worth reviving
Pick which conversations to revive — I'll draft each one.

  💬 1.  Bo Sample — partnership exploration
        Quiet for 23 days
        Last from Bo: "Let me think about the structure and revert"
        Last from you: discovery questions about their distribution
        Suggested opener: Bo — where did we land on the structure question? Any movement?
        [Draft]  [Resolved]  [Add to my list]  [Skip]

  💬 2.  Rio Sample — investor intro thread
        Quiet for 31 days
        Last from her: intro to 2 partners at Northstar Partners
        Last from you: thank-you reply
        Suggested opener: Rio — those Northstar intros are still warm on my side. We just landed [new traction] — worth me pinging them with that?
        [Draft]  [Resolved]  [Add to my list]  [Skip]

  💬 3.  Sam Sample — Acme Co pilot
        Quiet for 18 days
        Last from Sam: "let me get back to you with the brief"
        Last from you: kickoff scope outline
        Suggested opener: Sam — any movement on the pilot brief?
        Sam still owes you: pilot brief (11 days past)
        [Draft]  [Resolved]  [Add to my list]  [Skip]
```

The bracket-style button labels above are illustrative — the actual rendered display labels come from the renderer's `_action_display_label` (e.g., `draft` → `Draft`, `add to my list` → `Add to my list`).

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary. In this widget the customer-facing noun is "conversations" (email/Slack threads may be called "threads" only when literally naming an email or Slack thread).
- Bad: "Pick which threads to revive — thread_resurrected event will be written."
- Good: "Pick which conversations to revive — I'll draft each one."

## DOES NOT

- Auto-send revival emails. Always drafts to Gmail Drafts; user reviews and clicks Send.
- Re-surface a thread within 30 days of the last `thread_resurrected` event for it.
- Touch threads with `thread_resolved` events (those are explicitly closed).
- Surface threads where the user's last message was unanswered for less than 14 days (that's too early — would harass).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Surface conversations (email threads, Slack threads, meeting follow-ups) that went silent but had high-value context worth reviving — different from dormant-customer-scan which finds dormant PEOPLE. This skill finds dormant THREADS specifically. Use when the CEO says 'what conversations went silent', 'warm threads to revive', 'thread resurrection', 'threads to revive', 'conversations to restart', 'what's gone quiet that I should restart', 'find warm conversations'. Reads interaction events from events.jsonl, cross-references with entities.json (which people/projects/orgs the thread touches) and open commitment events (so threads with open commitments owed to you get surfaced with 'use commitment chase instead' alternative). Writes thread_resurrected events on revival action. DOES NOT fire on 'who went dark' (dormant-customer-scan — people not threads), 'who should I reach out to' / 'relationship moves' / 'weekly outreach' (relationship-moves — the ranked action pack that CONSUMES this skill's detections), 'follow up with [name]' (email-writer — plain outbound draft; meeting-shaped follow-ups are follow-up-ritual), or 'show my open threads' (workspace-manager). The granularity is the differentiator — dormant-customer-scan asks 'which person has gone quiet vs their cadence'; this skill asks 'which specific live conversation died mid-step'.
