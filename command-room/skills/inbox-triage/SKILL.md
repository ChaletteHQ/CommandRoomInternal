---
name: inbox-triage
description: "Morning inbox pass: reads overnight email, classifies into Reply Now / Decision Needed / FYI / Discard / Deep Read. Surfaces the 3–5 that matter, drafts replies for 2–3. Triggers: 'triage my inbox', 'inbox triage', 'what's in my inbox', 'process my inbox', 'go through my email', 'email triage', 'morning email pass'. Owns all 'inbox' + deep-email phrasing. Plus 'tune inbox-triage'. Does NOT fire on bare 'morning briefing' or 'brief me' — those go to morning-briefing for the daily digest."
---

## Skill Boundary (v2.1)

- **Owns:** deep email triage — 5-bucket classification + 2-3 drafted replies + top-of-pile ranking.
- **Pairs with `morning-briefing`** as the one-two daily start: briefing first (context), triage second (email action). Never duplicates briefing's 1-line email summaries.
- **Does NOT fire on "brief me" / "morning briefing"** — that's morning-briefing's daily digest at a summary level.
- **Does NOT fire on "draft follow-ups"** — that's follow-up-ritual (meeting context, not inbox context).

If user says "brief me and triage my inbox" — run morning-briefing first, then this skill.

## Voice Calibration

When drafting replies (Reply Now bucket + "draft a decision-needed response" flow), this skill applies `shared/VOICE_CALIBRATION.md`. Reads `_hq/voice/voice-block-inbox-triage.md` (the customer voice override, per `shared/VOICE_CALIBRATION.md`), extracts voice markers, applies recipient modifier based on sender's entities.json record, runs the forbidden-phrase check. Drafts are never sent automatically — always returned for CEO review and one-click send.

**Customer voice-block override (B1):** before drafting, read `_hq/voice/voice-block-inbox-triage.md` if it exists — it supersedes this SKILL.md's `## Voice Block` section-by-section (override sections replace same-named defaults; absent sections fall through). The universal banned-phrase list still applies except where the override's Taboos explicitly carve out an item. Staleness reads the override's `Last refreshed:` first.

## Writer Contract

Every email read during triage from an **in-scope** account emits an inbound `interaction` event to `events.jsonl` per `shared/PASSIVE_CAPTURE.md` (v3). Drafted replies (when sent) emit corresponding outbound interaction events. Dedup via source_ref hash prevents double-counting across morning-briefing, workspace-manager, and this skill.

**Connector-agnostic + account-scope (connector-agnostic-v1).** Resolve mail tools through the seam — `tool_discovery.discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))`, falling back to `discover_mail_search_tool` / `discover_mail_thread_fetch_tool` / `discover_mail_draft_tool` (empty map = today's behavior, R4). Never name a provider tool, query operator, provider field, or URL host directly — express intent (unread, in-sent, since) and let `connector_adapters/mail.py` compile it per provider. **Account scope (R1, `shared/ACCOUNT_SCOPE.md`):** an email from a `write_to_business: off` account (personal / mixed-unknown-sender) may still be *surfaced* in the triage list if its `surface` dial is on, but **no `interaction` event is written for it** — the writer wall (`account_scope_gate.enforce_scope`, enforced structurally inside the append path) rejects a provenance whose `account_id` resolves out-of-scope. Where the account map is empty, every account is in-scope (unchanged behavior).

**Promote-queue (R8, ACCOUNT_SCOPE §8) — the mixed-account business-by-association loop.** A `mixed`-role account files by association: mail whose sender resolves to a known entity (person_ids/counterparty resolved) writes normally; a sender NOT in the entity graph is walled. For each such walled sender that *looks* business (a real human, business domain or business content — not bulk/newsletter), append ONE `person_proposal` event via `event_gate.append_event` with `data: {name, email, promote_queue: true, origin: "connector", account_address: <the mixed account>, provenance: <the read's provenance>, evidence: <one line>}` (the `promote_queue: true` flag is what makes the proposal writable despite the wall — it IS the review surface), deduped against open proposals for the same email. Surface it in the triage output as *"[Name] ([email]) on [account] looks like business — file them? (`file it` / `keep personal`)"*. On **`file it`**: hand to people-crm — it creates the person as a USER-CONFIRMED add (`create_person` WITHOUT provenance kwargs — the user is the authority; the record wall is for unconfirmed connector derivations) and future mail from that sender is in scope by association. On **`keep personal`**: write a per-sender override via `connector_config.set_sender_scope_override(root, <account>, <sender>, write_to_business=False, reason="user demoted")` so the proposal never re-fires. Never promote silently — the write dial stays fail-closed throughout (H-G).

**Commitment extraction (v2.7.15+).** When an email body contains explicit commitment language — either an inbound promise from a counterparty ("I'll send the deck by Friday", "I owe you the contract") or an outbound promise the user is making in a draft ("I'll get back to you with…", "Will deliver by…") — emit a `type: commitment` event alongside the `interaction`, with **`data.origin: "connector"`** (it was extracted from a connector read — ACCOUNT_SCOPE §4a; the account-scope wall treats connector-origin commitments strictly). Schema and trigger conditions in `shared/COMMITMENT_SCHEMA.md`. See "Step: Extract Commitments" below for the recipe. This is the gmail-side counterpart to `meeting-notes`'s commitment extraction; together they're the only routine producers of new commitment events for typical CEO workflow (Slack-side extraction is a v2.7.16 candidate).

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

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). All
three decisions are **show-then-tune (STT)** — the triage always runs first, then offers one-tap
changes. Read config through `get_config` — never the raw file.

```python
# Resolve the plugin root first (CONTRACT Rule 22) — the placeholder form
# silently no-opped. Bash preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
# PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1); then run python FROM $PLUGIN_ROOT:
import sys; sys.path.insert(0, "shared/scripts")  # valid because cwd == $PLUGIN_ROOT per the preamble above
from skill_config_writer import get_config, save_skill_config, wipe_skill_config, is_configured

DEFAULTS = {
    "discard_aggressiveness": "standard",  # standard | aggressive | conservative
    "vip_seed": [],                        # inferred top-5 VIP senders, confirm/edit (optional extra)
    "default_action": "draft_replies",     # draft_replies | brief_only
}
cfg = get_config(workspace_root, "inbox-triage", DEFAULTS)
```

`discard_aggressiveness` shifts the Step 4 Discard-bucket threshold (`aggressive` = more into
Discard; `conservative` = fewer). `vip_seed` augments the PEOPLE.md VIP tier with up to 5
inferred-then-confirmed senders (it SEEDS the tiering the catalog references; never overrides an
existing PEOPLE.md tier — additive). `default_action` sets whether the run drafts replies (default)
or returns brief-only — persisting the existing "and draft the replies" / "just the brief" modifier.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "triage my inbox" | run triage with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "inbox-triage", DEFAULTS)` BEFORE rendering, then append the first-run block. |
| **Show settings** | "show inbox-triage settings" | render current config in plain English; no triage. |
| **Tune** | "tune inbox-triage" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-run triage. |
| **Reset** | "reset inbox-triage to defaults" | `wipe_skill_config(workspace_root, "inbox-triage")` → next fire is a first-fire again. |

**The first-run block (transport):** when a Reply Now widget renders this fire, the three decisions
ride as `fr1`/`fr2`/`fr3` items in a "Make this yours" section at the BOTTOM of that all_batch_widget
(the documented fr-item preselect exception — see `shared/CHAT_ACTION_WIDGET.md`); the `vip_seed`
row uses the optional `[text]` extra to confirm/edit the inferred top-5. When NO widget renders this
fire (no Reply Now drafts), use a 2–3 line FOOTER after the brief headline instead:

> *First time triaging for you. I set 3 defaults: **normal filtering on what to discard** ·
> **your top senders: [names]** · **drafting replies by default**. Say "tune inbox triage" to
> change any, or just tell me ("be more aggressive" / "brief only, don't draft").*

Tap/answer → apply-choices → `save_skill_config(..., is_reconfigure=True, origin="first_fire_override")`.
The block renders exactly once ever (`is_configured` gate).

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "be more aggressive" / "discard more" | `discard_aggressiveness = aggressive` |
| "be more conservative" / "don't discard so much" | `discard_aggressiveness = conservative` |
| "just the brief, don't draft" / "brief only" | `default_action = brief_only` |
| "draft the replies again" | `default_action = draft_replies` |
| "add [name] to my VIPs" | append to `vip_seed` |
| "drop [name] from VIPs" | remove from `vip_seed` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-run triage + confirm in one line.

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
   - **Inclusion criterion:** the **unread-in-inbox** intent is the canonical query (compiled to the connected provider's operators by `connector_adapters/mail.py` — never a hardcoded operator string). Every unread thread is a candidate regardless of how old it is. This closes the 2026-05-20 mis-classification gap where a 28-day stale LAST_TRIAGE timestamp caused a silent collapse to a 24h window, missing an active $300K Dustin thread whose last message was 2 days old.
   - **Ranking criterion:** time window (the difference between now and LAST_TRIAGE) ranks recency within the candidate set. Threads with messages in the last few days rank higher; older threads rank lower. But age never excludes — that's the unread state's job.
   - **No silent window collapse.** Pre-v3.13.0: if `now - LAST_TRIAGE` was large, the skill silently shrunk the window to 24h. v3.13.0+: large gaps trigger a full **unread-in-inbox** sweep, surfacing every old-but-active unread thread in the main brief body (not in a "notes for next pass" footnote).
2. **Pull unread / flagged email** via the declared mail connector (resolved through the seam per the Writer Contract; empty map = today's behavior).
3. **Enrich each message.**
   - Sender importance: VIP if in `_hq/PEOPLE.md` with `tier: board|investor|customer|top-vendor`; otherwise rank by historical reply frequency
   - Project context: existing OPEN commitments tied to the project this email belongs to. **Use `shared/scripts/cru_match.py::load_open_commitments(events.jsonl_path)`** filtered by `primary_thread_id` or by counterparty `person_id` — NOT MASTER_TRACKER (per `references/SOURCE_OF_TRUTH.md`, MASTER_TRACKER is a Tier 2 view and may be stale). `load_open_commitments` is the canonical reader: it handles all 5 commitment-event shape variants and treats both `commitment_resolved` and `thread_resolved` as valid closers, so commitments that fired through the dashboard ✓ done path are correctly filtered out. **Keep the confirmed half — `cru_match.split_pending_review(opens)[0]` (INTAKE).** The raw reader is deliberately unfiltered, so it still carries UNCONFIRMED extractions; enriching an email with "you already owe them this" off a guess sends the triage decision the wrong way. Those rows belong to the needs-your-call queue (`needs your call`), not to the project context on an inbox card.
   - Urgency signals: deadlines mentioned in the body, explicit "need by…" phrasing
3.5. **Fetch the full thread BEFORE classifying state (v3.13.0+ MANDATORY — closes the "stalled on you" inversion bug).**

   Pre-v3.13.0 this skill derived thread state from mail-SEARCH results alone — which return a TRUNCATED, NON-LATEST slice of the thread (a snippet from an older matching message, NOT the newest message). On 2026-05-20 this produced a load-bearing failure: the Dustin / Rio Designs thread (active $300K offer cluster) was filed as *"stalled — no reply from you in 10 days"* when the actual state was that Dustin owed M the next deliverable (M replied May 18, Dustin confirmed he'd build it out — ball was in his court, not stalled on M's).

   **The rule:** before asserting "needs reply" / "Reply Now" / "stalled" / "no reply in N days" / "awaiting them" / who-owes-the-reply for any thread, call the resolved **thread-fetch** tool (`discover_mail_thread_fetch_tool` / `discover_for_category("email","thread_fetch",…)`) requesting FULL message content, and read the LAST message in the returned `messages` array. Do NOT infer state from mail-search snippets or the search result's partial `messages` list. (On providers whose thread-fetch returns full content by default, the full-content request is a no-op; the point is: read the newest message, not a search snippet.)

   **Determining ball-in-court from the latest message:**
   - If the connector marks the newest message as SENT BY THE USER (the provider's sent-flag — a sent label, a sent-items folder membership, whatever the connector's message shape exposes; resolved per provider by the adapter, never a hardcoded field name) OR `sender == <the primary user's address>` (resolve the person_id via `shared/scripts/primary_user.py::resolve_primary_user(workspace_root)`, then read that person record's email(s) from entities.json — never hard-code an address): **the user has already replied → classify as "awaiting counterparty" / "owed-to-you"**, NOT "Reply Now" or "stalled on you".
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
6. **Draft Reply Now replies.** 2-3 drafts max (more than that and drafts become noise). Voice-calibrated via `_hq/voice/voice-block-inbox-triage.md` (the customer voice override, per `shared/VOICE_CALIBRATION.md`) if available.
   - **Mechanical voice-tell gate (B2 — bash-gated, not prose).** After drafting each reply body and before surfacing it in the widget, run it through the deterministic detector. It hard-fails on the exact banned phrases in `shared/VOICE_CALIBRATION.md`; structural tells warn. This backstops the Step 2 critique, it does not replace it:

     ```bash
     SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
     PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
     printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context email
     ```

     On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0 (`pass`/`warn`). Never surface a draft the detector still fails. A phrase the sender's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.
7. **Write the triage brief.**
   - Save: `_hq/inbox/TRIAGE_[YYYY-MM-DD_HH-MM].docx` — the triage brief is ALWAYS a `.docx` (never a `.md` file), matching the "Saved triage brief file" contract below.
   - **Rendering (SPEC TRIAGEROUTE).** Render the `.docx` via the canonical `shared/scripts/brief_writer.py` `make_brief(brief_kind="inbox_triage", ...)`, passing the sections payload and exec header in "Output Structure" below. That route is mandatory, not a preference: it is what runs the output-contract gate, the voice-tell gate and the post-render leak scanner, and it enforces canonical typography and heading hierarchy. Before this route existed, the step named a `.docx` and no way to produce one, so the brief was hand-rolled every morning — every gate skipped, on the one document in this product assembled entirely out of the CEO's mail.
     - **NEVER hand-roll the brief** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking brief (the v3.20.0 failure mode) — and this brief carries senders, subjects and quoted body text lifted straight out of real mail, which is exactly what the leak scan exists to catch.
     - **NEVER create, render, copy, upload, or update the brief — or any part, derivative, or restatement of it ("the top five", "a summary", "just the drafts") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/inbox/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so I can read it on the way in", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the triage in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link.
   - Record timestamp in `_hq/inbox/LAST_TRIAGE.txt`
8. **Return:** file link + headline ("12 overnight emails. 3 top items flagged. 2 replies drafted. 1 decision needed: Acme pricing.") — the same sentence is the brief's exec-header verdict, written once and used twice.

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

Vague phrases ("I'll think about it", "let's circle back", "we should consider") DO NOT qualify. See `shared/COMMITMENT_SCHEMA.md` § "Extraction triggers" for the full list of qualifying vs disqualifying patterns — and the **capture floor (Stage D 2026-07)**: clear owner + clear deliverable + real consequence, all three, or skip silently (below-floor items bury real promises). If `_hq/config/commitment-rules.md` exists, read it BEFORE writing and skip any item matching a user-taught `never-track` pattern.

**Classify `data.kind` at capture (Stage D — REQUIRED; the gate rejects a kind-less commitment on the strict path):** email commitments almost always have a counterparty → `"promise"` (the sender or the user owes the other party); a self-note the user emails to themselves with no counterparty → `"task"`; scheduling intent ("I'll set up time with…") → `"scheduling"`; genuinely ambiguous → `"promise"` + `data.pending_review: true`. **Due-date nudge (S2):** propose `due` from the email language OR set explicit `data.no_due: true`.

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
    "counterparty_id": "<person_id of who the deliverable is owed TO / who owes it — for email commitments this is almost always the OTHER party on the thread. MUST populate when determinable (Stage E receipts, F5): it feeds the CRU candidacy gate directly (Bug #103 fix). Retires requester_id for NEW writes — readers keep the alias chain forever.>",
    "counterparty_name": "<free-text fallback — SHOULD set when the counterparty is named but has no person record>",
    "title": "<short verb-phrase summarising the deliverable, ≤120 chars>",
    "kind": "promise" | "task" | "scheduling",
    "due": "<ISO date if explicit; empty if not — pair empty with no_due: true>",
    "status": "open" | "overdue",
    "source_event_seq": <seq of the interaction event for this email>,
    "source_ref": "gmail:<message_id>",
    "evidence": "<quoted phrase from email body, ≤200 chars>"
  }
}
```

**Status:** if `due` is parsed and is in the past relative to today (UTC), set `"overdue"`. Otherwise `"open"`.

**Owner resolution:** use `aliases.json` to canonicalize sender's display name / email to a `person_id`. If the email is from someone not yet in entities.json, surface a one-line suggestion in the brief ("💡 [Sender] isn't in your contacts yet — want me to add them?") but DO NOT skip the commitment — emit it with `owner_id: ""` so it's not lost.

**Dedup:** Match on `(source_ref, title)`. The same email shouldn't produce two equivalent commitments across re-runs. Skip if `(gmail:<message_id>, title)` already exists for a `type: commitment` event in events.jsonl.

> **Sent-mail reconciliation is NOT done here (v3.18.12 — Bug #98-v3).** Closing commitments the CEO completed by emailing directly is the dedicated silent `reconcile-sent` task's single job (it fires 6:45 AM). It was briefly folded into this triage pass (v3.18.11) and got skipped in real use — same structural reason the brief skipped it: an invisible substrate write loses to the visible deliverable. Don't re-add it here. Extract NEW commitments above; the `reconcile-sent` task closes the ones already sent.

### Surface in the triage brief

Add one line to the brief output:

```
## Commitments I Caught
- 3 they owe you (Aria will send pricing by Fri, Bowie will redline MSA, Carol will introduce VC)
- 1 you owe (reply to Sam with Q3 plan by Mon)
```

If zero commitments captured, omit the section — don't print "0 commitments".

---

## Output Structure (saved triage brief file)

Rendered by `make_brief(brief_kind="inbox_triage", ...)`. Nothing below is new content — it is the brief
this skill has always produced, expressed as the structured payload the chokepoint takes, plus the exec
header every STANDARD_KIND carries. `title` is the `# Inbox Triage — …` line, `subtitle` is the `Window: …`
line, and each `##` heading below is one entry in `sections`.

**Exec header (SPEC EXEC1 element 1 — `make_brief` REFUSES the render without it).** `inbox_triage` is
brief-family, so it renders the FULL three-line eyebrow (verdict + CHANGED / DECIDE / NEEDED), not the
verdict-only lead the memo / one-pager kinds use — a triage brief is a since-last-pass digest, which is
exactly what that scaffold is for. You have already computed all four; they are the Step 8 return line:

- **verdict** = what the inbox amounts to, in one sentence. *"Two threads are waiting on you and one needs a call you have not made."* Concrete or nothing — never a count dressed up as a finding.
- **changed** = what arrived since the last pass · **decide** = the one item only the CEO can answer · **needs** = the drafted replies waiting on approval. Nothing-forms are legal and encouraged on a quiet morning.

**Depth floors (SPEC B3 — the output-contract gate blocks the save on a violation).** Sync rule: these
mirror `output_contract_validator.RULES_BY_KIND["inbox_triage"]` — change one, change the other. The CAPS
carry the weight; the floors are 1 on purpose, because this brief's length is a function of the inbox, not
of effort, and a floor of 3 would force exactly the padding the Gotchas ban.

| Section | Presence | Bullets | Where the bound comes from |
|---|---|---|---|
| tile band (unread · flagged · drafted) | optional, drop-empty | — | a real zero renders; an unknown datum is omitted |
| `Top of the Pile` | conditional on candidates | 1–5 | Step 5's "surface 3–5 items"; past 5 it is the appendix moved up |
| `By Bucket` | **required** | 1–5 | the five-bucket model is a closed taxonomy — a 6th bullet is an invented bucket |
| `Commitments I Caught` | omit entirely when zero | — | already stated above: never print a zero line |
| `Reply Drafts` | absent under `default_action = brief_only` | 1–3 | Step 6's "2-3 drafts max" and its own reason |

**Exemplar anchor (SPEC OUT8).** Before composing, load `exemplars.get_exemplar("inbox_triage", workspace_root)`
(`shared/scripts/exemplars.py`) and anchor STRUCTURE on it — section order, visual placement, proportions.
Workspace exemplar beats the shipped seed; `None` = compose on the layout below, unchanged. **Contract beats
exemplar beats default**, and it anchors structure, never facts: no name, subject, or number from the
exemplar may appear in the brief.

**Visual pass (SPEC OUT2 §3, after the save):** run the render-then-critique pass per
`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass", then log it either way. Warn-only forever — a
finding never refuses a save, and the pass never loops.

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

## Reply Drafts (review + send in chat)
- Reply to Aria (pricing) — drafted; sends when you click send
- Reply to Lyra (call reschedule) — drafted; sends when you click send
```

## Chat Output Format (v3.13.1+ — editable widget for Reply Now drafts)

**Follows `shared/EMAIL_DRAFT_PROTOCOL.md`** (v3.13.0+ universal scope — every recipient-bound email draft surface follows the same protocol, whether the trigger is scheduled or on-demand).

**Reply Now drafts surface as an editable widget, not as blockquote previews.** Per M's 2026-05-20 feedback #15 ("for threads to revive, it's also generating emails without the widget") plus #13/#14 ("if it is to be sent to sender it should open in the widget"). Pre-v3.13.1 inbox-triage rendered each draft as a `> To: / > Subject: / > Body` blockquote that forced back-and-forth chat-turn edits. v3.13.1+ uses the canonical email-writer widget cascade — one widget item per Reply Now draft, with `send / draft / snooze 3d` actions inline. Edit happens on the widget; no chat-turn round-trips required.

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
        "actions": [f"{i} send", f"{i} draft", f"{i} snooze 3d"],
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
from widget_transport import render_and_persist
data_view = json.loads('''<DATA_VIEW_JSON>''')
transport = render_and_persist(data_view=data_view, wrapper='fragment',
                               persist_dir='<WORKSPACE>/_hq/.system/widgets',
                               name_hint='inbox-triage')
print(transport['html'])
"
# Pass the rendered HTML (transport["html"]) to mcp__visualize__show_widget as widget_code (EW2+T, F-15 —
# shared/CHAT_ACTION_WIDGET.md § Transport). Never hand-compose or post-process the HTML.
```

**Action semantics** — same lazy contract as email-writer Phase 4 (per `shared/EMAIL_DRAFT_PROTOCOL.md` §1). The draft text lives in the widget; NO connector draft exists until the user acts (the tool named is the resolved draft/send path on the declared backend per EMAIL_DRAFT_PROTOCOL §0.5/§3c — Gmail via Zapier leg, Superhuman native, read-only backend degrades to paste):
- `N send` — apply-choices creates the draft and sends it in one motion via the resolved send dispatch (EMAIL_DRAFT_PROTOCOL §3c order). Logs `email_drafted` + `email_sent`. (Body edits happen directly on the card before Apply — FB-10 inline body; `edit then send` is retired per FB-17, never emitted anew.)
- `N draft` — apply-choices creates the draft on click; it lands in the connector's Drafts for later. Logs `email_drafted`.
- `N snooze 3d` — NO connector call; mutes the card for 3 days (`chat_dismissal` event). The FB-17 third primary button.

**Decision Needed / Deep Read / FYI items do NOT get the widget surface** — those don't carry a draft to send. Decision Needed renders as a normal list item with a "decide" action; Deep Read as a list with attachment notes; FYI as a one-line summary.

**Sources section stays.** Below the widget, append the canonical `Sources:` section linking each triaged thread per `_hq/CONVENTIONS_SOURCE_LINKS.md`. Use **the URL the mail connector returns** on the thread-fetch/search call — never synthesize a provider URL host (`connector_adapters/mail.py::deep_link` prefers the returned URL and degrades to no link if none is returned, N8). Format: `[Sender — short subject](<connector-returned thread URL>)`.

```markdown
Sources:
- [Aria (Acme) — pricing redline?](<connector-returned thread URL>)
- [Skyler — call reschedule](<connector-returned thread URL>)
```

If no sources were referenced (rare), omit the section.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I made 3 calls: standard discard aggressiveness · VIP seed: [senders]"
- Good: "I set 3 defaults: normal filtering on what to discard · your top senders: [names]"

**Saved triage brief file** (the `.docx` at `_hq/inbox/TRIAGE_[YYYY-MM-DD_HH-MM].docx` — always `.docx`, separate from the chat widget) — the brief lists the reply drafts under "Reply Drafts" by recipient + subject. No `gmail://drafts/<id>` URLs are stamped at fire time, because under lazy creation no Gmail draft exists until the user clicks `draft`/`send` (per `shared/EMAIL_DRAFT_PROTOCOL.md` §1). The body of each draft does NOT need to appear inside the brief — the widget carries it in chat. (Same simplification follow-up-ritual got in v3.13.0 — the .docx stopped embedding the email body once the widget became the editing surface.)

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
- **Every mail backend is a peer (Rule 21 connector parity).** Discover the mail tool via `tool_discovery` and run against whichever is connected — Gmail, Superhuman, Outlook — never assume Gmail-only, never refuse to try another. Only if NO mail connector is detected at all, stop early with: "Inbox triage needs your email connected. Connect your mail account and run again."
- **Respect VIP classification.** If the sender is on `_hq/PEOPLE.md` with a top-tier mark, never drop them into Discard — push them up the ranking even if the body is short.
- **Never classify based on subject line alone.** Read the body — a two-line subject can be critical, a 400-word body can be noise.
- **If the CEO's Reply Now drafts conflict with a decision in Decision Needed**, pause on those drafts and flag the dependency ("Can't draft reply to Aria — depends on board decision in item 2").
- **If inbox > 200 messages, warn and ask whether to run** — this may take a minute and hit rate limits. Offer to restrict to VIP senders only.
- **Don't over-classify.** Err toward Reply Now + Decision Needed getting smaller rather than padding the "top 5" with weak items.

## Capability surface (A6 — feature-detected, connector-agnostic-v1)

Everything here is gated on the DECLARED backend's capability manifest (`connector_adapters.capabilities.supports(provider, <key>)`, detected row overriding the known default). A backend without the capability degrades per the tell-once/silent-skip split: a capability the user would notice missing gets ONE plain-English note per session ("your mail connector doesn't do X — skipping that part"); a pure convenience is skipped silently. Never hard-fail, never fake it.

- **Splits pre-classification (`splits`).** When the backend exposes inbox Splits (Important/Other — Superhuman-class), fetch the split assignment per thread BEFORE scoring and use it as a prior: an "Other"-split thread starts with a noise penalty, an "Important"-split thread skips the automated-domain demotion. The five-bucket classification still runs — Splits sharpen the priors, they never replace the read. No splits capability → silent skip (scoring is unchanged from today).
- **Inbox hygiene (`unsubscribe`, `mark_spam`).** When supported, the Discard bucket may OFFER (never auto-fire) two extra per-item actions: `N unsubscribe` (recurring newsletter the CEO never opens) and `N spam`. Both are user-click actions through apply-choices, logged as `chat_dismissal`-class events with the action noted. Capability absent → the actions simply don't render (silent).
- **Attachment→workspace pipe (`attachments`).** When the backend can read attachments and a triaged item's attachment is clearly workspace-relevant (a contract, a deck the CEO owes a review on), OFFER "pull [filename] into the workspace" — on click, fetch via the connector's attachment tool and route the FILE through `file-documents`' routing rules (never dump to root). Read-only attachment capability = offer only on explicit ask; absent → silent skip.

Deferred from A6 (logged in the build report): inline scheduling and the NL-retrieval primitive — no consumer contract firm enough to write against yet (YAGNI posture; the manifest keys exist, wiring lands with their first real consumer).

## Scheduling

The canonical Inbox scheduled task (7:15 AM weekdays) already exists in the standard schedule set — customers turn it on via `set up command room schedules` and adjust it via `change my schedule`. Do NOT offer to register a separate ad-hoc recurring run from this skill.

## Integration

- **Pairs with `morning-briefing`** — inbox-triage output can be embedded as a section of the briefing
- **Pulls from `_hq/PEOPLE.md`** for VIP ranking (Tier 2 view per `references/SOURCE_OF_TRUTH.md` — fine for static "who is a VIP" tier lookup; not used for "what's outstanding")
- **Pulls open commitments from `_hq/data/events.jsonl`** via `cru_match.load_open_commitments`, confirmed half only (`cru_match.split_pending_review(...)` — INTAKE; unconfirmed extractions are needs-your-call queue members, not context for a triage decision). Canonical Tier 1 source. NOT from MASTER_TRACKER — see `references/SOURCE_OF_TRUTH.md` overlay rule.
- **Drafts via the declared mail backend** (seam-resolved, never named here) — never direct send
- **Voice from `_hq/voice/voice-block-inbox-triage.md` (the customer voice override, per `shared/VOICE_CALIBRATION.md`)** (optional)

## What It Doesn't Do

- Doesn't auto-send, auto-archive, or auto-delete
- Doesn't build long-form replies (use `one-pager-composer` or write in your mail client)
- Doesn't track outbound emails (separate concern)

## Connected Tools

- **Mail connector** (required — whichever backend is declared; discovered via `tool_discovery` per Rule 21)
- **PEOPLE.md** — VIP ranking (Tier 2 view; static-tier lookup only)
- **`_hq/data/events.jsonl`** — open-commitment overlap (Tier 1 source, read via `cru_match.load_open_commitments` then `cru_match.split_pending_review(...)`, confirmed half)
- **`_hq/voice/voice-block-inbox-triage.md`** (optional) — customer voice override (per `shared/VOICE_CALIBRATION.md`)
- **morning-briefing skill** — embedding target

## Routing (full trigger corpus)

The settings-trigger family for this skill, relocated verbatim from the pre-G11-diet description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Also handles first-run personalization settings — use when the user says 'tune inbox triage', 'tune inbox-triage', 'show inbox triage settings', 'show inbox-triage settings', 'reset inbox triage to defaults', 'reset inbox-triage to defaults'.
