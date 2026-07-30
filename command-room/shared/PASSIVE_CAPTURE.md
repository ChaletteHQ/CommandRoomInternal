# Passive Capture Contract — v3.0

**Purpose:** Memory should accumulate without the CEO doing anything. Every connector read that surfaces a fact worth remembering *from an in-scope account* should produce a corresponding event in `events.jsonl` — silently, on the same turn, as a side effect of the read.

**Applies to every skill that reads from:** Gmail, Google Calendar, Slack, Granola, Google Drive, Superhuman, Outlook, or any other connector that exposes CEO-relevant activity.

This contract sits alongside `WORKSPACE_API.md`, `ACCOUNT_SCOPE.md` (the two-dial account model), and is read whenever a skill touches a connector.

> **v3.0 change (connector-agnostic-v1, 2026-07-11 — review requirement R1).**
> "Read produces Write" is now qualified by the account map's **`write_to_business`
> dial**. The prior blanket doctrine ("no mode loses data") is deliberately
> relaxed for out-of-scope accounts: an account with `write_to_business: off`
> is READ for surfacing (brief/reminders) but **NOTHING is filed** into the
> substrate for it. This is amended here ONCE, in writing, before any skill's
> Writer Contract prose is rewritten, so it is not rewritten twice. The
> per-connector `PASSIVE_CAPTURE_OPTOUT.md` seam is the mechanical precedent —
> v3 generalizes it to per-account/per-item via the account map. **Where the
> account map is empty (live client mid-upgrade), behavior is unchanged: every
> account is in-scope and the original blanket doctrine holds (R4).**

---

## Core Principle

> Read produces Write — for in-scope accounts. Out-of-scope reads surface without filing. No user confirmation. No second turn.

When a skill reads a mail thread, Calendar event, Slack message, or Granola transcript **from an account whose `write_to_business` dial is on** (or from a workspace with no account map yet — the empty-map case is in-scope by default, R4), it MUST append the corresponding events to `events.jsonl` as part of the same turn, via the Append Protocol in WORKSPACE_API.md.

When the read is from an account whose `write_to_business` dial is **off** (personal, or a mixed-account sender not tied to a known entity), the skill still performs the read for its primary purpose and MAY surface the item if the account's `surface` dial is on — but it appends **nothing** to the substrate. The writer helpers enforce this structurally (`ACCOUNT_SCOPE.md` §4): a provenance-required event whose `account_id` resolves out-of-scope is rejected at ingest.

The CEO does not need to say "save this." The act of Claude reading it on the CEO's behalf is the authorization to persist it — **when the account it came from is in business scope.**

**Exception:** raw content of an email body, Slack message, or transcript is NOT written verbatim to events.jsonl. The event stores a summary + entity references + source reference (message id, calendar id), not the full text.

---

## Capture Rules by Connector

**Every emitted event carries:**
- `primary_thread_id` — the single thread that owns this event's SESSION_NOTES append (may be null for HQ-level events)
- `related_thread_ids[]` — other threads touched by the event (cross-refs only, no ownership)
- `cross_ref_reason` — map of each related_thread_id → short human string
- `classification_confidence` — float 0.0–1.0, classifier's confidence in the primary assignment
- `source_ref_hash` — sha256 for dedup per section below

See `references/ORG_AND_THREAD_MODEL.md` for the resolution + confidence model. This section shows the event-type-specific fields only.

### Email (the declared mail backend)

| Trigger | Event emitted |
|---|---|
| Sent email from CEO to any canonical person | `type: interaction`, `data: { direction: "outbound", channel: "email", summary: "<subject>", counterparty_person_ids: [...], source_ref: "<provider>:<message_id>" }` |
| Received email from canonical person | `type: interaction`, `data: { direction: "inbound", channel: "email", summary: "<subject>", counterparty_person_ids: [...], source_ref: "<provider>:<message_id>" }` |
| Email mentioning commitment language ("I'll send by Friday", "owe you") | additional `type: commitment` event, owner inferred from who made the promise |

**Skip (do not capture):**
- Bulk/marketing emails (List-Unsubscribe header present, sender not in entities.json)
- Newsletters, automated notifications
- Auto-replies ("out of office")
- The CEO's own sent CC of a thread (dedup via thread-id; one event per Gmail thread, not per message)

### Google Calendar

| Trigger | Event emitted |
|---|---|
| Calendar event occurred (today or in the past) with ≥1 canonical attendee | `type: meeting`, `data: { title, start_ts, duration_min, attendee_person_ids, source_ref: "gcal:<event_id>" }` |
| Calendar event for the future | do NOT emit; wait for the event to occur |

**Resolution:** a meeting with no matchable attendees to canonical people is captured with `person_ids: []` and surfaced later during "end session" as "unresolved meeting — want to add the attendees to your people registry?"

### Slack

| Trigger | Event emitted |
|---|---|
| Message from canonical person in a tracked channel | `type: interaction`, `data: { direction: "inbound", channel: "slack", summary: "<first 140 chars>", counterparty_person_ids: [sender_id], source_ref: "slack:<team>/<channel>/<ts>" }` |
| CEO sent message in a tracked channel | `type: interaction`, `data: { direction: "outbound", channel: "slack", ... }` |
| Thread reply in a tracked channel | capture as one event per distinct participant-day; don't emit one event per reply |

**Tracked channel definition:** any channel that has been explicitly added to `_hq/BUSINESS_CONTEXT.md` or that contains ≥2 canonical people as members. Non-tracked channels produce no events.

### Granola

| Trigger | Event emitted |
|---|---|
| Meeting transcript processed (via `meeting-notes` skill) | `type: meeting`, `data: { title, start_ts, duration_min, attendee_person_ids, summary (first 500 chars of meeting-notes output), decisions_seq_refs: [...], commitments_seq_refs: [...], source_ref: "granola:<meeting_id>" }` |
| Decisions extracted from transcript | separate `type: decision` events per decision |
| Commitments extracted from transcript | separate `type: commitment` events per commitment |

### Drive

| Trigger | Event emitted |
|---|---|
| CEO created or materially edited a doc tied to a thread | `type: note`, `data: { summary: "doc activity: <doc title>", source_ref: "drive:<file_id>" }` |
| CEO read a doc | do NOT emit — too noisy. Reads aren't facts worth remembering. |

---

## Dedup

Every event carries a deterministic hash computed from:
`sha256(source_ref + direction + data.summary)` (first 12 hex chars, stored in `data.dedup_hash`).

Before appending, the writing skill MUST:
1. Compute the hash.
2. Check the **source_ref dedup index** (SPEC A3): `python3 shared/scripts/source_ref_index.py check <workspace_root> --dedup-hash <hash> [--source-ref <ref>]` (or import `source_ref_index.check(workspace_root, source_ref=..., dedup_hash=...)`). This is an O(1) membership set over `_hq/data/.source_refs.idx` — it catches duplicates of ANY age, not just the last 200 events (an active workspace emits 200 events in well under a week, so the old window silently re-captured month-old sources). The index self-heals: a missing `.idx` rebuilds from events.jsonl on first check.
3. If the check returns a hit, skip the append and emit a `dedup-hit` log line.

> **Fallback (one release only):** if the index helper is unavailable, fall back to scanning the last 200 events for the matching hash. This fallback will be removed once A3 has shipped a release.

The index is maintained automatically: `atomic_append_jsonl`'s `events.jsonl` branch records each appended event's keys into `.source_refs.idx` inside the A1 writer lock, so writers never have to maintain it (they only `check`). cleanup verifies + rebuilds it weekly.

This makes passive capture **idempotent**: running the same connector check twice produces the same single event.

---

## Thread Resolution (v2.2 — multi-thread + confidence)

For every captured event, the classifier returns:
- **`primary_thread_id`** — the single most salient thread (the one whose SESSION_NOTES gets the append)
- **`related_thread_ids[]`** — any other threads the event clearly touches (zero or more)
- **`cross_ref_reason`** — short string per related thread
- **`classification_confidence`** — float 0.0–1.0

### Resolution order (for primary)

1. **Direct marker.** Connector data carries an explicit project/thread marker (email label matching a thread alias, Slack channel name matching a thread alias). Confidence: **0.95**.
2. **Alias match.** `aliases.json` match on subject / title / channel name / body mention. Confidence: **0.85**.
3. **People clustering.** ≥2 canonical people involved share a single `project_ids` membership. Confidence: **0.70**.
4. **Org clustering.** All involved people affiliate to the same org, and that org has exactly one active thread of the relevant kind. Confidence: **0.55**.
5. **Weak signal.** Single-person match or fuzzy topic match. Confidence: **0.25–0.40**.
6. **No signal.** `primary_thread_id: null`, confidence: `null`. Event is captured as HQ-level; cleanup surfaces for manual binding.

### Related threads (cross-refs)

After primary is set, scan for additional threads touched:
- Other people involved with their own threads → their most-active thread is a related candidate
- Vendors/orgs mentioned by name → their relationship thread is a candidate
- Topics referenced that map to a theme thread → candidate

Include a related thread only if confidence ≥ **0.50** for the link itself. Add a `cross_ref_reason` string for each.

### Confidence bands (writer behavior)

| Confidence | Writer action |
|---|---|
| ≥ 0.75 | Auto-append silently. No surfacing. |
| 0.40–0.75 | Auto-append as provisional. Surface in weekly classification review. |
| < 0.40 | Auto-append as low_confidence. Flag in weekly review. |
| null (no signal) | Append with `primary_thread_id: null`. cleanup batches these. |

The CEO is **never prompted mid-session** to resolve low confidence. Capture always succeeds.

### Feedback loop

When the CEO corrects a classification (explicit reclassification, or via the weekly review in `insight-generator`), the correction is written to `_hq/data/classifier_feedback.jsonl`:

```jsonc
{"ts": "...", "event_seq": 1042, "old_primary": "project_087", "new_primary": "project_104", "reason": "user_correction_weekly"}
```

The classifier reads this file on each run and weights future classifications. Capture skills do not need to invoke the feedback loop explicitly — they just emit confidence scores and let downstream curation close the loop.

---

## What NOT to Capture

**Never persist:**
- Email body / Slack message / transcript raw text. Summaries only.
- Content from non-canonical strangers (raw emails from people not in entities.json). Surface to the CEO as "want to add this sender to your people registry?" instead. The actual event isn't captured until the person is canonicalized.
- Anything matching the `email_exclusion_rules` in CLAUDE.md (newsletters, LinkedIn, marketing).
- Drafts (emails in drafts folder, scheduled Slack messages). Only sent/received.
- Failed sends / bounced messages.

---

## Capture Budget

A single turn should not emit more than 20 events from passive capture. If a connector check returns more than 20 capture-eligible items (e.g., CEO returning from vacation with 200 emails), the skill:
1. Captures the 20 most recent / highest-signal items.
2. Appends a single `type: note` event: `{"summary": "passive-capture budget reached — N items unprocessed for YYYY-MM-DD", "data": {...}}`
3. Surfaces to the CEO: "I saw 200 items in Gmail but only captured the top 20 — want me to process the rest?"

This prevents events.jsonl from becoming a firehose of low-signal contact noise.

---

## Voice sample handling — moved to per-skill voice calibration (v3.0+)

The shared `_hq/VOICE_SAMPLES.md` rolling-100 model that lived in PASSIVE_CAPTURE was retired in v3.0. Voice calibration is now per-skill — every composer skill (`email-writer`, `memo-writer`, `one-pager-composer`, `follow-up-ritual`, `inbox-triage`, `decision-memo-composer`) carries its own Voice Block in its SKILL.md, with per-skill correction logs at `_hq/voice/corrections-<skill>.jsonl`. See [`VOICE_CALIBRATION.md`](VOICE_CALIBRATION.md) for the canonical contract.

**What this means for passive capture:** outbound emails / Slack threads / Drive docs that the user authors are still captured as `interaction` events in `events.jsonl` per the rules above. Composer skills draw voice signal from those events at draft time via their per-skill protocol — they no longer read a shared voice file.

User opt-out for voice calibration is now in each composer's voice block, not via a global `voice_samples: off` flag.

### Manual curation

CEO can pin a sample to always be included (protected from rolling eviction) by prefixing with `<!-- pinned -->`. Pinned samples never count against the 100-sample cap, but are capped separately at 20 pins. Used for "this is exactly how I write a key message — always reference this."

---

## Privacy Surface

Passive capture respects, in this order:

1. **The account map's `write_to_business` dial (v3.0 — the primary mechanism).**
   An account with `write_to_business: off` files nothing, regardless of the
   rules below. This supersedes the old `email_exclusion_rules` prose as the
   governing wall. The wall is enforced structurally in the writer helpers per
   `ACCOUNT_SCOPE.md` §4, not by this prose alone.
1b. **Per-sender scope overrides (Layer B, LIVE as of connector-agnostic-v1).**
   `workspace.accounts[].overrides.senders[<addr>].{surface, write_to_business}`
   — read by the wall (`account_scope_gate`), written ONLY via
   `connector_config.set_sender_scope_override` (delegated setter). This is
   the structured replacement for the prose exclusion rules: "never file
   newsletters@x" = `write_to_business: off` for that sender; "always show my
   kids' school" = `surface: on` on a personal account.
2. The legacy `email_exclusion_rules` list in `CLAUDE.md` (CEO-defined senders or
   subject patterns to ignore) — **still honored during the transition**:
   readers/capture paths apply BOTH the structured overrides and the prose
   list; where they disagree, the more conservative (exclude) wins.
   **Migration (one-time, additive):** `command-room-update-bridge` reads the
   workspace CLAUDE.md's `email_exclusion_rules`, converts each SENDER-shaped
   rule to `set_sender_scope_override(root, <account>, <sender>,
   write_to_business=False, reason="migrated from email_exclusion_rules")`,
   and leaves the prose in place with a migrated-note (never deletes user
   config). Pattern-shaped rules (subject regexes) stay prose — the
   structured store is sender-keyed by design (YAGNI: no rule-matrix).
3. Any person record with `status: "archived"` — events referencing only archived people are suppressed (they still happen, but they're not worth tracking).
4. Any thread with `status: "archived"` — same rule; updates to archived threads don't accumulate passive events.

The CEO can disable passive capture for a specific connector by adding to `_hq/PASSIVE_CAPTURE_OPTOUT.md`:
```
<connector>: off
```
(the connector's own name — `gmail`, `superhuman`, `outlook`, `slack`.) When present, skills skip all capture from THAT connector but still perform connector reads for their primary purpose (e.g., surfacing emails in a briefing). **This per-connector seam is the mechanical precedent the v3.0 two-dial model generalizes to per-account/per-item** — a `write_to_business: off` account is exactly this opt-out, scoped to an account instead of a whole connector.

---

## Which skills implement this contract

Every skill that touches a connector is responsible for its own capture. This is not a separate "capture" skill — it's a cross-cutting discipline. Primary implementers:

- **morning-briefing** — captures from Gmail, Calendar, Slack during its daily scan.
- **workspace-manager** — captures during "what's going on" and "go [project]" connector checks.
- **call-prep** — captures from Gmail, Calendar, Slack during pre-meeting scan.
- **meeting-notes** — captures from Granola when processing transcripts.
- **inbox-triage** — captures every triaged email as an interaction event.
- **follow-up-ritual** — captures every outbound follow-up as an outbound interaction event.
- **dormant-customer-scan** — captures any new inbound from a flagged dormant thread as a re-activation signal.

Each skill's SKILL.md should reference this file in its Writer Contract section:

```markdown
## Writer Contract

...standard writer contract language...

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. Every connector read emits corresponding events to events.jsonl per that contract's rules.
```

---

## Failure Mode

If events.jsonl append fails during passive capture:
1. Log to `_hq/CONFLICTS.md` with type `passive-capture-failure`.
2. Do NOT block the skill's primary output. The user still gets their briefing / triage / call prep.
3. `cleanup` flags repeated failures from the same skill for investigation.

The primary user task always wins. Capture is a side effect — never a gate.

---

## What This Contract Does Not Do

- Does not capture CEO's calendar free/busy, reading history, or non-actionable data.
- Does not capture raw message contents — summaries and source refs only.
- Does not infer commitments or decisions from ambiguous text. Those are explicit — either stated as a commitment ("I'll send X by Y") in a transcript, or surfaced by meeting-notes / decision-log extraction.
- Does not capture in real-time — only when a skill actively reads a connector. There is no background listener.

---

**End of passive capture contract.**
