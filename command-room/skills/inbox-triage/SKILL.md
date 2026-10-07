---
name: inbox-triage
surfaces: both
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

**Connector-agnostic + account-scope (connector-agnostic-v1).** Resolve mail tools through the seam in ONE call each — `tool_discovery.discover_mail_search_tool(tools, declared=connector_config.declared_backend("email"))`, `tool_discovery.discover_mail_thread_fetch_tool(tools, declared=connector_config.declared_backend("email"))`, and `tool_discovery.discover_mail_draft_tool(tools, declared=connector_config.declared_backend("email"))`. The seam resolves the declared backend first — using that provider's own operation vocabulary — and refuses to substitute another product's tool; with nothing declared it falls back to fingerprint discovery (empty map = today's behavior, R4) and names both connectors in `result.mail_ambiguous` when the workspace has two. Never name a provider tool, query operator, provider field, or URL host directly — express intent (unread, in-sent, since) and let `connector_adapters/mail.py` compile it per provider. **Account scope (R1, `shared/ACCOUNT_SCOPE.md`):** an email from a `write_to_business: off` account (personal / mixed-unknown-sender) may still be *surfaced* in the triage list if its `surface` dial is on, but **no `interaction` event is written for it** — the writer wall (`account_scope_gate.enforce_scope`, enforced structurally inside the append path) rejects a provenance whose `account_id` resolves out-of-scope. Where the account map is empty, every account is in-scope (unchanged behavior).

**Promote-queue (R8, ACCOUNT_SCOPE §8) — the mixed-account business-by-association loop.** A `mixed`-role account files by association: mail whose sender resolves to a known entity (person_ids/counterparty resolved) writes normally; a sender NOT in the entity graph is walled. For each such walled sender that *looks* business (a real human, business domain or business content — not bulk/newsletter), append ONE `person_proposal` event via `event_gate.append_event` with `data: {name, email, promote_queue: true, origin: "connector", account_address: <the mixed account>, provenance: <the read's provenance>, evidence: <one line>}` (the `promote_queue: true` flag is what makes the proposal writable despite the wall — it IS the review surface), deduped against open proposals for the same email. Surface it in the triage output as *"[Name] ([email]) on [account] looks like business — file them? (`file it` / `keep personal`)"*. On **`file it`**: hand to people-crm — it creates the person as a USER-CONFIRMED add (`create_person` WITHOUT provenance kwargs — the user is the authority; the record wall is for unconfirmed connector derivations) and future mail from that sender is in scope by association. On **`keep personal`**: write a per-sender override via `connector_config.set_sender_scope_override(root, <account>, <sender>, write_to_business=False, reason="user demoted")` so the proposal never re-fires. Never promote silently — the write dial stays fail-closed throughout (H-G).

**Commitment extraction (v2.7.15+; writer added INCAP1 v5.12.1).** When an email body contains explicit commitment language — an inbound promise from a counterparty ("I'll send the deck by Friday", "I owe you the contract"), an inbound ask the CEO is expected to answer (a warm intro, a direct question, a document request), or an outbound promise the user is making in a draft ("I'll get back to you with…", "Will deliver by…") — a `type: commitment` event lands alongside the `interaction`, carrying **`data.origin: "connector"`** (it was extracted from a connector read — ACCOUNT_SCOPE §4a; the account-scope wall treats connector-origin commitments strictly). **Compose it through `shared/scripts/inbound_capture.py` `capture_inbound_items` — never by hand.** That module is the writer for this lane; it is what stamps the `<provider>:<message_id>` source_ref both orchestrators' circularity fences read, and the `thread_ref` the reply gates need. Schema and trigger conditions in `shared/COMMITMENT_SCHEMA.md`; see "Step: Extract Commitments" below for the recipe and the direction table. This is the mail-side counterpart to `meeting-notes`' `meeting_capture` and `reconcile-sent`'s `sent_capture` — the same shared capture gate behind all three.

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
changes. Read config through the verb below — never the raw file. It runs
`skill_config_writer.get_config` over the DEFAULTS block, and reports
`is_configured` beside it.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"defaults": {"default_action": "draft_replies", "discard_aggressiveness": "standard", "vip_seed": []}, "skill_name": "inbox-triage", "workspace_root": "<WS>"}, "name": "inbox_helpers:triage_config"}'
```

The answer is `{config, configured}`. `config` is the saved choices deep-merged
over the defaults below, so a decision added in a later version falls back to its
default while every choice already saved is honoured. `configured` is false on a
first fire, which is the one gate the "Make this yours" block renders behind.

```json
{
  "discard_aggressiveness": "standard",
  "vip_seed": [],
  "default_action": "draft_replies"
}
```

On the FIRST fire only, save those defaults before rendering — one write, the
only write door, in the shape `skill_config_writer.save_skill_config` writes
(the DEFAULTS above under a `config` key, with the origin stamp):

```bash
python3 "$RT/shared/scripts/workspace_access.py" write --json '{"data": "<the defaults above, as JSON>", "expected_mtime": null, "rel": "_hq/data/skill_config/inbox-triage.json"}'
```

`discard_aggressiveness` shifts the Step 4 Discard-bucket threshold (`aggressive` = more into
Discard; `conservative` = fewer). `vip_seed` augments the PEOPLE.md VIP tier with up to 5
inferred-then-confirmed senders (it SEEDS the tiering the catalog references; never overrides an
existing PEOPLE.md tier — additive). `default_action` sets whether the run drafts replies (default)
or returns brief-only — persisting the existing "and draft the replies" / "just the brief" modifier.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "triage my inbox" | run triage with the returned `config`. On the FIRST fire only (`configured` is false): write the defaults BEFORE rendering, then append the first-run block. |
| **Show settings** | "show inbox-triage settings" | render current config in plain English; no triage. |
| **Tune** | "tune inbox-triage" | pre-filled re-questionnaire OR freeform (table below) → write the merged config → re-run triage. |
| **Reset** | "reset inbox-triage to defaults" | write the defaults back over the saved config, the way `skill_config_writer.wipe_skill_config` clears it → the next fire is a first fire again. |

**The first-run block (transport):** when a Reply Now widget renders this fire, the three decisions
ride as `fr1`/`fr2`/`fr3` items in a "Make this yours" section at the BOTTOM of that all_batch_widget
(the documented fr-item preselect exception — see `shared/CHAT_ACTION_WIDGET.md`); the `vip_seed`
row uses the optional `[text]` extra to confirm/edit the inferred top-5. When NO widget renders this
fire (no Reply Now drafts), use a 2–3 line FOOTER after the brief headline instead:

> *First time triaging for you. I set 3 defaults: **normal filtering on what to discard** ·
> **your top senders: [names]** · **drafting replies by default**. Say "tune inbox triage" to
> change any, or just tell me ("be more aggressive" / "brief only, don't draft").*

Tap/answer → apply-choices → the merged config is written, stamped as a first-fire override.
The block renders exactly once ever, behind the `configured` gate.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "be more aggressive" / "discard more" | `discard_aggressiveness = aggressive` |
| "be more conservative" / "don't discard so much" | `discard_aggressiveness = conservative` |
| "just the brief, don't draft" / "brief only" | `default_action = brief_only` |
| "draft the replies again" | `default_action = draft_replies` |
| "add [name] to my VIPs" | append to `vip_seed` |
| "drop [name] from VIPs" | remove from `vip_seed` |

After applying: write the merged config, re-run triage, confirm in one line.

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

   **The rule:** before asserting "needs reply" / "Reply Now" / "stalled" / "no reply in N days" / "awaiting them" / who-owes-the-reply for any thread, call the resolved **thread-fetch** tool (`discover_mail_thread_fetch_tool(tools, declared=connector_config.declared_backend("email"))`) requesting FULL message content, and read the LAST message in the returned `messages` array. Do NOT infer state from mail-search snippets or the search result's partial `messages` list. (On providers whose thread-fetch returns full content by default, the full-content request is a no-op; the point is: read the newest message, not a search snippet.)

   **MAILTRUST1 (2026-07-29) — thread-fetch alone is NOT sufficient for a NEGATIVE claim.** On 2026-07-29 this rule was followed and still failed: the full-content thread-fetch was itself short by one message (5 returned, 6 existed), and a differently-shaped all-mail recency sweep (3-day window, the seam's recency intent) found the missing one immediately — a sender-scoped search had gone stale too. Any single read can only prove presence, never absence. So before asserting any NEGATIVE ("no reply", "stalled", "awaiting them", "went quiet"), corroborate the thread-fetch with a **broad recency sweep of a different shape** (an N-day recency window scoped across ALL mail — never a sender-scoped search), and run both reads through `shared/scripts/mail_absence.py::corroborate_absence(primary_read, sweep, thread_id=...)`. On `corroborated: False` you MUST NOT assert the negative — say the reads disagree and name what you could not confirm. Positive claims ("they replied, here's the newest message") still need only the thread-fetch: presence is provable from one read; absence never is. Do not edit this paragraph into a form that presents thread-fetch alone as sufficient for a negative — that exact reading is what failed.

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
     # >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
     # The substrate is on the customer's machine; this process may not be. Every
     # read, helper and write goes through workspace_access ON the host that holds
     # the data. Never open, copy or tar a workspace file into this session, and
     # never write one from here.
     #  1 RESOLVE, once per call. The four lines below name the plugin root, the
     #    environment, and -- on a seat whose files are local -- the workspace. On a
     #    merged seat resolve instead with `workspace_access.py discover`, hand the
     #    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
     #    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
     #    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
     #    before the first plan, so a saved document can name the folder the
     #    customer opens. A runtime that is absent, or a runtime_version that
     #    differs, is a STOP: run the update-bridge install step. There is no
     #    container fallback.
     #  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
     #  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
     #    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
     #    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
     #    variables in front of python3, which carry the writer identity and the
     #    run mode to the host that holds the data. The reply is one JSON
     #    envelope; ok:false is a stop, never a hand retry.
     #  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
     #    redirect, an in-place edit, a heredoc into the workspace, or a python body
     #    that opens a substrate file.
     #  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
     #    sandbox seat, or a Code session on the customer's own machine -- the same
     #    verbs run in this shell, and the four lines below resolve it for them.
     #  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
     #    sentence with no file, script, path, variable, shell text or mechanism
     #    in it -- "One step could not run here; what is below is complete." or
     #    "... is partial." Never narrate a workaround, never say what you tried.
     #  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
     #    render -- lives in this session's own scratch, never under the
     #    workspace. Nothing under `_hq/` is created, copied or removed by a
     #    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
     #    removed by `plan remove`, and a removal is reported in the envelope's
     #    own words -- removed, moved aside, or still there -- never as done.
     #  8 WRITERS. A document, a receipt, a close or a re-pin is written by
     #    `plan run_writer` naming a writer on its list -- never by importing a
     #    writer in a shell. The door forwards who you are; a writer with no
     #    identity on this seat refuses in one sentence, and that sentence is the
     #    whole answer.
     SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
     PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
     eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
     WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
     [ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
     # <<< CR ACCESS PREAMBLE v6 <<<
     printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context email
     ```

     On exit 1 (`FAIL`), rewrite the flagged lines and re-run until it exits 0 (`pass`/`warn`). Never surface a draft the detector still fails. A phrase the sender's calibrated Voice Block demonstrably allows is exempt via `allow_phrases`; never improvise the override.
7. **Write the triage brief.**
   - Save: `_hq/inbox/TRIAGE_[YYYY-MM-DD_HH-MM].docx` — the triage brief is ALWAYS a `.docx` (never a `.md` file), matching the "Saved triage brief file" contract below.
   - **Rendering (SPEC TRIAGEROUTE).** Render the `.docx` via the canonical `shared/scripts/brief_writer.py` `make_brief(brief_kind="inbox_triage", ...)`, passing the sections payload and exec header in "Output Structure" below. That route is mandatory, not a preference: it is what runs the output-contract gate, the voice-tell gate and the post-render leak scanner, and it enforces canonical typography and heading hierarchy. Before this route existed, the step named a `.docx` and no way to produce one, so the brief was hand-rolled every morning — every gate skipped, on the one document in this product assembled entirely out of the CEO's mail.
     - **NEVER hand-roll the brief** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or PII-leaking brief (the v3.20.0 failure mode) — and this brief carries senders, subjects and quoted body text lifted straight out of real mail, which is exactly what the leak scan exists to catch.
     - **NEVER create, render, copy, upload, or update the brief — or any part, derivative, or restatement of it ("the top five", "a summary", "just the drafts") — through Claude Docs (the built-in docs / artifact page), Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/inbox/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so I can read it on the way in", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the triage in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link.
   - Record timestamp in `_hq/inbox/LAST_TRIAGE.txt`
8. **Return:** file link + headline ("12 overnight emails. 3 top items flagged. 2 replies drafted. 1 decision needed: Acme pricing.") — the same sentence is the brief's exec-header verdict, written once and used twice.

## Step: Extract Commitments (MANDATORY in every triage run)

After classifying each email but before writing the brief, scan each message body for **commitment language** — forward-looking promises about a specific deliverable made by an identifiable owner, and inbound asks the CEO is expected to answer. Hand each match to the capture writer named below; it composes and appends the `type: commitment` events per the canonical schema in `shared/COMMITMENT_SCHEMA.md`.

### Direction matters — who owes whom

| Pattern in email body | Direction | Owner |
|---|---|---|
| Inbound from counterparty: "I'll send X by Y" | They owe you — `waiting_on` | counterparty's `person_id` |
| Inbound from counterparty: "I owe you the …" | They owe you — `waiting_on` | counterparty's `person_id` |
| Inbound from counterparty: "Will deliver …" | They owe you — `waiting_on` | counterparty's `person_id` |
| Outbound (draft user is sending): "I'll send X by Y" | You owe them | user's `person_id` |
| Outbound: "I'll get back to you with …" | You owe them | user's `person_id` |
| Inbound ask the CEO is expected to answer — a warm intro, a direct question, a document request ("Can you …?", "Connecting you two", "Could you send over …") | You owe them a reply — `reply_owed`, a **reply-shaped commitment** captured confirm-tier | user's `person_id` |

**Why the last row is a capture and not a skip (INCAP1, v5.12.1).** It used to read *skip — not a commitment until accepted*, which sounded careful and was the single largest hole in the product: the most common thing in a mailbox is a message someone is waiting on a reply to, and it was the one thing never tracked. Three dogfood records found the same absence. A reply-shaped commitment is **proposed, not asserted** — it lands with `pending_review: true`, so it stays out of chase and out of every closure gate until the CEO adjudicates it, and it drops off the moment they reply. That is what "not until accepted" was reaching for; skipping it was never the way to get there.

**Direction doctrine still holds, unchanged.** The CEO's OWN messages never create waiting-on-them items through this lane. Sent-mail promises are `reconcile-sent`'s lane (`shared/scripts/sent_capture.py`), on the sent rail's evidence. The writer refuses a message whose sender resolves to — or is named as — the primary user.

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

### The writer (MANDATORY — never hand-append)

Every capture in this step goes through the capture verb. Build one extraction dict per qualifying item and hand the batch over — the verb runs the shared Stage-D / S2 / Stage-E capture gate, mints `source_ref` and `thread_ref` from the connector's real ids, dedups against what is already on the book, applies the relevance gate and the per-fire volume cap, and composes the survivors. The append is yours, and it is ONE call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"items": [<one dict per qualifying item: message_id, thread_id, ts, direction, sender_person_id|sender_name, title, kind, due|no_due, evidence, org_id, person_ids, classification_confidence, below_bar|below_bar_reason>], "provider": "<the seam-resolved provider>", "source_skill": "inbox-triage", "user_person_id": "<resolved, never guessed>", "workspace_root": "<WS>"}, "name": "inbox_helpers:plan_inbound_capture"}'
```

The answer carries `rows`, `counters` and `errors`. The rows are composed, not
written — append them in ONE call:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "inbox-triage", "rel": "_hq/data/events.jsonl", "rows": [<the rows from the answer above>]}'
```

An empty `rows` list needs no append.

- **`direction` is required per item** and is never defaulted — it is the whole table above, and a wrong default writes the promise onto the wrong person's plate.
- **Ids are the connector's real ones.** Never a draft id: the writer refuses `draft:`-shaped message and thread ids outright (F-22), because a row anchored to a draft can never be matched against the message that was actually sent.
- **`thread_id` is what makes a capture closable.** It becomes `data.thread_ref`, which is the anchor the reply gates read when the counterparty (or the CEO) answers on that thread. Omitting it is safe and leaves those gates inert for that item — pass it whenever the connector returns one.
- **Below-floor items are declared, not dropped silently.** An item that fails the Stage-D floor goes in the batch with `below_bar: True` + a `below_bar_reason`; the writer counts it and never writes it. That count is what separates "nothing cleared the floor" from "the capture step never ran".
- **This step never closes anything.** Extraction is a WRITE. Reconciling a reply against an open item is the reconcile rails' job (`reconcile-sent` at 6:45, the inbox orchestrator's CRU pass); the writer refuses to compose any non-writer event.

### Field mapping

The shape the capture verb composes, for reference — read it to understand what lands, do not hand-build it:

```json
{
  "type": "commitment",
  "source_skill": "inbox-triage",
  "primary_thread_id": "<project this email belongs to — same as the interaction event>",
  "classification_confidence": <inherited from the interaction event>,
  "person_ids": ["<owner_id>", "<counterparty_id>", "<user_id>"],
  "ts": "<email send/receive ISO timestamp>",
  "data": {
    "owner_id": "<resolved per the table above — empty string when the sender has no person record yet>",
    "owner_external": "<the sender's free-text name — stamped INSTEAD of a resolved owner_id on a waiting_on item from an unrecognised sender, alongside pending_review: true. See Owner resolution below.>",
    "counterparty_id": "<person_id of who the deliverable is owed TO / who owes it — for email commitments this is almost always the OTHER party on the thread. MUST populate when determinable (Stage E receipts, F5): it feeds the CRU candidacy gate directly (Bug #103 fix). Retires requester_id for NEW writes — readers keep the alias chain forever.>",
    "counterparty_name": "<free-text fallback — SHOULD set when the counterparty is named but has no person record>",
    "title": "<short verb-phrase summarising the deliverable, ≤120 chars>",
    "kind": "promise" | "task" | "scheduling",
    "due": "<ISO date if explicit; empty if not — pair empty with no_due: true>",
    "status": "open" | "overdue",
    "source_ref": "<provider>:<message_id>",
    "thread_ref": "<provider>:<thread_id> — present whenever the fetch carried a thread id",
    "evidence": "<quoted phrase from email body, ≤200 chars>"
  }
}
```

**Status:** stamped by the writer — `"overdue"` when `due` parses and is in the past relative to today (UTC), otherwise `"open"`.

**No `source_event_seq` on this leg** (and don't add one back). The parent `interaction` event is not guaranteed to exist for a fetched thread, so — exactly as on the sent and Slack legs — the `source_ref` IS the provenance. A seq pointing at an event that may never be written is a dangling read, not a link.

**Owner resolution:** use `aliases.json` to canonicalize the sender's display name / email to a `person_id` and pass it as `sender_person_id`. If the sender is not yet in entities.json, surface a one-line suggestion in the brief ("💡 [Sender] isn't in your contacts yet — want me to add them?") and **still capture** — pass `sender_name` instead. The writer lands the item with an empty `owner_id`, the name in `data.owner_external`, and `pending_review: true`, so it is visible and one click from real rather than lost. It cannot close while unowned; that is the honest consequence of not knowing who someone is.

**Dedup:** handled by the writer, two layers. Per-message identity `(source_ref, title)` — first 60 chars, case-insensitive, compared as canonical keys so one message under two provider labels is one identity — plus a cross-channel restatement match against the open set, so a promise already tracked from a meeting or from Slack MERGES instead of double-tracking. Re-fires over the same mailbox therefore write nothing; do not add a dedup pass of your own.

**Volume:** one fire captures at most the per-fire cap the verb reports back as `cap`. On a catch-up spanning months the remainder is DEFERRED, not dropped — nothing is marked captured, so the next fire takes the next slice — and `counters.n_capped` says how many are waiting. Never raise the cap to "get through the backlog" in one morning.

> **Sent-mail reconciliation is NOT done here (v3.18.12 — Bug #98-v3).** Closing commitments the CEO completed by emailing directly is the dedicated silent `reconcile-sent` task's single job (it fires 6:45 AM). It was briefly folded into this triage pass (v3.18.11) and got skipped in real use — same structural reason the brief skipped it: an invisible substrate write loses to the visible deliverable. Don't re-add it here. Extract NEW commitments above; the `reconcile-sent` task closes the ones already sent.

### Surface in the triage brief

Add one line to the brief output:

```
## Commitments I Caught
- 3 they owe you (Aria will send pricing by Fri, Bowie will redline MSA, Carol will introduce VC)
- 1 you owe (reply to Sam with Q3 plan by Mon)
- 2 replies you're on the hook for (warm intro from Dana; document request from Ellis)
```

The third line is the `reply_owed` captures — say "replies you're on the hook for", never the event vocabulary. Take the counts from `r['n_waiting_on']` / `r['n_reply_owed']`; do not re-tally them from your own notes. If `r['n_capped']` is non-zero, add one line — *"N more from the backlog will be picked up on the next pass"* — because a silently truncated catch-up is the one thing worse than a long list.

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
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"data_view": <the data view built above>, "name_hint": "inbox-triage", "wrapper": "fragment", "workspace_root": "<WS>"}, "name": "inbox_helpers:render_inbox_page"}'
```

Then persist the audit page — the only write door. `inbox_helpers:render_inbox_page` runs the same renderer, the same wrapper contract and the same leak scan as `widget_transport.render_and_persist`; the prose you compose around it still goes through `validate_chat_output`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" write --json '{"data": "<the html from the answer above, verbatim>", "expected_mtime": null, "rel": "<the page_rel from the answer above>"}'
```

Then append the answer's `pending_rows` — every element, in order, nothing
dropped. They are the ledger's record that this page went through the gates
rather than being hand-composed, and the render helper has no write door of its
own, so a row left in that field is a row the ledger never gets. When the field
is empty this call is skipped:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "inbox", "rel": "_hq/data/events.jsonl", "rows": [<every row in pending_rows from the render answer above, in order>]}'
```

**Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.

Pass the returned `html` to `mcp__visualize__show_widget` as `widget_code`
(EW2+T, F-15 — `shared/CHAT_ACTION_WIDGET.md` § Transport). Never hand-compose
or post-process it.

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

## "Treat [vendor] as billing" — the phrase door (SPEC FIXTRAIN 6.2)

When an invoice or a statement lands in a bucket the CEO would not have put it in, the fix is that the sender should count as billing from now on. On 09-11 this surface handed the CEO a file path under the workspace's internal folder and told them to edit it. **Never do that.** They do not have that folder open, the path means nothing to them, and it is the exact shape of leak the gate exists to stop.

Offer the door instead, in the customer's own words, and let the product do the write:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"sender": "<the sender address or domain>", "vendor": "<vendor display name>", "workspace_root": "<WS>"}, "name": "inbox_helpers:billing_door"}'
```

Behind the verb: `surface_composers.billing_door_line(vendor)` composes the
offer, `surface_composers.add_billing_domain`'s ordering composes the file,
and the whole reply leaves through the one door — `surface_composers.post(
whole_reply, surface="inbox", workspace=workspace_root,
relayed=composed_text)` — whose return is the entire reply.

The verb composes `surface_composers.billing_door_line` for the offer and
`surface_composers.billing_door_receipt` for the answer, and it composes the
file the way `surface_composers.add_billing_domain` writes it — order preserved,
the new domain appended last, created on first use.

The answer carries `line` (the offer, next to the misfiled message), `receipt`
(what to say once they used the door), `result` (the domain and the counts — the
customer's own vendor, theirs to see) and, when the list would change, `text`:
the whole file's new contents, order preserved, the new domain appended last.
Write it in ONE call, and only when `text` is not null:

```bash
python3 "$RT/shared/scripts/workspace_access.py" write --json '{"data": "<the text from the answer above>", "expected_mtime": null, "rel": "<the rel from the answer above>"}'
```

The writer is idempotent, keeps the list in order, and creates it on first use. A vendor NAME with no domain in it comes back asking which sender they mean rather than writing a junk line — the receipt composer says that for you. The file the phrase writes is never named in anything the CEO reads, in this skill or any other.

**The whole reply goes through one door (SPEC FIXTRAIN v5.31.0 6.1, R-25 — MANDATORY).** A composer gates the sentence it built; it cannot gate the sentences typed after it. Eleven of the thirteen leaks on the v5.31.0 record were exactly that shape — a clean composed answer, then an ungated paragraph naming files, functions, event names and writer ids. A door whose receipt is gated and whose explanation is not still hands the reader the path. So compose everything you intend to post, hand it to `post` ONCE, and print what it returns as your entire reply.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"customer_rows": [<the rows a section was composed around, or null>], "relayed": "<the composed line, verbatim>", "surface": "inbox", "text": "<everything you intend to post>", "workspace_root": "<WS>"}, "name": "inbox_helpers:gate_reply"}'
```

`relayed` is `composed_text`: the composer's own return from this same run —
`billing_door_line`'s or `billing_door_receipt`'s, relayed line for line, never
retyped. The answer's `text` is your entire
reply. The answer also carries `pending_rows`: the door's own record that this
reply was gated. Append it, in the same call as anything else this fire owes:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "inbox-triage", "rel": "_hq/data/events.jsonl", "rows": [<the pending_rows from the answer above>]}'
```

`relayed` is the composer's own return, and the door checks it twice: for PRESENCE, IN ITS OWN ORDER (paraphrasing it instead of relaying it is a refusal, not a style — and so is shuffling its lines or repeating one of them: a relay is the composer's return, not its ingredients) and for ORIGIN — every line of it must be a line a composer returned in THIS same run, and the check is in full: one unvouched line refuses the whole post. There is no share of a turn a caller may claim as already-checked. `customer_rows` is how a reply says it was composed around the CEO's own words: you name the ROWS (by seq, or by row id) and **the door reads their customer-typed fields off the book itself**. There is no argument for the words — you cannot tell this door what they typed, only which of their rows to go and read, and a call with no `workspace` declares nothing at all. **Be exact about what a declaration does**: it blanks the declared fragment out of the copy the INTERNAL-NAME classes read — `_hq/` paths, data-file and module names, script names, build codes, the vocabulary roster and the record counter — which is most of this gate, so it is not something to hand yourself. Record ids and the absolute-path scan read the whole text whatever was declared. Everything undeclared is this product's own words and is scanned in full. `post` raises rather than returning, and nothing is caught. **If it refuses, post the composer's return on its own** — it is already gated, and the paragraph that could not pass is the paragraph that should not have been written; **if even that refuses, post `surface_composers.refused_line(<surface>)` and nothing else** — one honest sentence that it could not put the answer together, with the phrase offered again. **There is no sentence after it.**

## Triggers

- "triage my inbox"
- "inbox triage"
- "what's in my inbox"
- "process my inbox"
- "go through my email"
- "email triage"
- "morning email pass"
- "treat [vendor] as billing" (the phrase door above — only inside a triage run or a reply to one)

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

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

## Narration leak scan (CUT-C item 8 — MANDATORY on every composed line)

Widget bodies are scanned inside the render verb; the PROSE this skill composes around them is not, unless this step runs. Before posting any sentence you composed — an ack, a header, a summary, a pointer, a "why" line — run `validate_chat_output(<the text>)` from `chat_output_renderer.py` (`shared/scripts/`). It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_` / `bp_` / `pcand:` wire id), an event or field name, a file name or path, or a score. ABORT the post and rewrite the sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(<WORKSPACE>))` is the one substitution). NEVER catch the error and post anyway. Text relayed byte-exact from a driver or the transport is already scanned and is not re-composed.

## Routing (full trigger corpus)

The settings-trigger family for this skill, relocated verbatim from the pre-G11-diet description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Also handles first-run personalization settings — use when the user says 'tune inbox triage', 'tune inbox-triage', 'show inbox triage settings', 'show inbox-triage settings', 'reset inbox triage to defaults', 'reset inbox-triage to defaults'.
## Draft date scan (DRAFTDATE1 — MANDATORY on every rendered draft)

**A draft never states a date, day or deadline the row does not hold.** On 2026-09-07 the drafts on this product invented three: "I'll have it finished by Friday" and "this is on your calendar today" on rows with no due date and no calendar event, and "let's get this paid this week" on a row with neither. Nobody had promised any of those days; sending one makes a commitment the book does not know about.

Before you show or save ANY draft you composed — status note, nudge, chase, follow-up, reply, invite body — run it through the scan:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"draft_text": "<the draft text>", "row": <the row>, "today": "<the workspace's own day>", "workspace_root": "<WS>"}, "name": "inbox_helpers:check_draft_dates"}'
```

The answer is `{ok, phrases, detail}`. A false `ok` names every phrase the row
cannot support — the verb runs `draft_date_scan.assert_draft_dates` and reports
what it raised instead of raising.

A date phrase is allowed only when it traces to (1) the row's due date, (2) a calendar event on the row, or (3) a date in the row's OWN words — its title, its quote, the thread subject. `today` is the workspace's day (`tz.py`), never a UTC re-slice.

**NEVER catch the error and send anyway, and never invent a date so the sentence reads better.** The fix is one of two things: drop the day from the draft ("I'll come back to you with a date" is honest and costs nothing), or set a real date on the row first and then say it. A draft with no date at all is always allowed.

