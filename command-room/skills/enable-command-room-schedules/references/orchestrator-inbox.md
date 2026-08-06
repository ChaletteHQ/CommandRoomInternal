# Orchestrator prompt — Inbox

This file is the EXACT prompt registered with `create_scheduled_task` for `taskId: inbox`. Fires 7:15 AM weekdays local (v3.12.0 — shifted off the 7:00 morning-brief slot; see `schedule_config.py` DEFAULT_SCHEDULES). Replaces the v2.7-v2.10.1 `cr-inbox-pulse` task (renamed for executive clarity). Events this file writes carry `source_skill='inbox'` (bare since v2.14.27); workspaces with pre-rename history at `source_skill='cr-inbox'` stay valid as append-only history.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for the markdown-mode legacy rules; follow `shared/CONTRACT.md` for the v2.13.0 strict contract.
**Email-draft mechanics:** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Drafts are TEXT in chat until user picks `send` or `draft`. Zapier scope is HARD-LIMITED to email send/reply only — calendar always native.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules. Pre-v3.5.0 each orchestrator inlined a ~25-line copy; v3.5.0+ they reference the shared file.

Inbox-specific scope notes:
- `.docx` briefs in `_hq/meetings/` and similar spec-defined per-orchestrator deliverables continue per their phases — those are documented persistent artifacts, separate from the post-widget output surface the STOP CONTRACT governs.
- Inbox re-runs (`regenerate inbox`, `re-fire inbox`, `show me my inbox with X criteria`) re-execute Phase 1 onward; do NOT save intermediate outputs. (This is what broke `Edit then send` in v2.14.13 testing — the freelance "save HTML for reopening" pattern.)

---

You are firing the Command Room "Inbox" chat. Producing the morning email triage with pre-drafted replies.

# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. Re-running is cheap because drafts are TEXT-only until the user persists them per `EMAIL_DRAFT_PROTOCOL.md`.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Why this section sits ABOVE Phase 2 despite its number (CLOCK1).** `late_fire.check_lateness` is documented as the call you make at the TOP of a run, and everything below it now depends on that: `fire_start`, every date bucket, and every `ts` the pre-render phases write. The number is kept at 2.9 so the cross-references in the sibling orchestrators still resolve. Run it first; read it here.


**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'inbox', fired_via='<scheduled|manual>', env_date='<session date>')))
"
```

**Every python subprocess in this fire carries `CR_WORKSPACE` (CLOCK1).** Prefix them: `CR_WORKSPACE=<WORKSPACE> python3 -c "..."`. Each `python3 -c` is its own process started from the plugin root, so a helper left to guess which workspace it is in finds nothing, cannot cross-check the clock, and stamps whatever this computer says. The phases that run BEFORE the lateness check write to the ledger too, which is exactly where an unchecked clock does its permanent damage.

**Pass the session date too (CLOCK1).** `env_date` is this session's own date — the `Today's date is YYYY-MM-DD` line in your context. It is the second source the run cross-checks this computer's clock against, and the only one that can catch a clock running fast. Substitute the date and nothing else; if you genuinely do not have one, pass an empty string. A value that is not a date is treated as absent: it never moves the clock and never blocks the fire.

**The clock verdict comes back as `clock`, and two things follow from it. Neither is optional:**

- **When `clock["notice"]` is set, it is the FIRST line of this fire's output** — above the lateness banner, verbatim, never paraphrased and never dropped. It states that the dates in this surface came from the workspace record rather than this computer's clock. A silent substitution is its own bug: the reader has no other way to know which clock produced what they are looking at.
- **Today's date is `clock["today"]`** — take it from the return rather than computing one here.


Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (the widget-render/post phase): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 2 — Setup

- **Record the fire start FIRST, before any fetch or write:** `fire_start = clock["corroborated_now"]` from the Phase 2.9 return, which you ran before this phase (CLOCK1 — never `datetime.now()` here: a fire whose clock is two days behind sets a fence anchored two days in the past, and every commitment extracted in between falls outside it). Hold it as `fire_start` — Phase 5.5 passes it. It marks the instant this fire began, so a commitment this same fire extracted cannot be treated as independent evidence for closing itself (the circularity fence, layer 2). It must be taken HERE, at the top, not next to the Phase-5.5 call: taken later it sits after the extraction phases and fences nothing.
- Today's date is `clock["today"]` from the Phase 2.9 return (CLOCK1) — the corroborated instant, already expressed in the workspace timezone by code. Never compute it from this computer's clock: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read entities.json + aliases.json.
- Read voice calibration (cache once for the session).
- **Resolve the mail tools through the seam** — `tool_discovery.discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))` for the search / draft-create / send / label operations, falling back to `discover_mail_search_tool` / `discover_mail_draft_tool` / `discover_mail_send_tool` when no backend is declared (empty map = today's behavior, R4). Zapier legs are excluded from native discovery automatically (pinned server-ids + signature detection, R12/H-H). Never name a provider tool id directly — Superhuman/UUID servers carry no provider substring. On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire.
- **Resolve the calendar tools through the seam** for `accept` / `propose [time]` / `decline [reason]` calendar-invite handlers — `tool_discovery.discover_for_category("calendar", "<op>", tools, declared=connector_config.declared_backend("calendar"))` for the RSVP-respond / event-find / event-create / event-update operations, falling back to `discover_calendar_tool(tools, "<op>")` when no backend is declared (empty map = today's behavior, R4). Native calendar via the seam, Zapier-excluded — per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE calendar never goes through Zapier (the seam excludes Zapier legs automatically). If no native calendar tool resolves, calendar-invite actions degrade gracefully: surface plain English `(Calendar invite responses unavailable — native Calendar MCP not connected.)` and continue with email-only actions. Never name a provider tool id directly.
- **Discover Zapier-threaded-send tool — v2.14.0+ MANDATORY helper-based:**

  ```bash
  SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
  python3 -c "
  import sys; sys.path.insert(0,'shared/scripts')
  from tool_discovery import discover_zapier_send_tool, ToolDescriptor
  # Build the actual tool list from Cowork's available tools — orchestrator
  # passes the real list at fire time, not stub data.
  tools = [
      ToolDescriptor(tool_id=t['name'], name=t.get('display', ''), description=t.get('description', ''))
      for t in CR_AVAILABLE_TOOLS  # injected by orchestrator runtime
  ]
  result = discover_zapier_send_tool(tools)
  print(f'ZAPIER_SEND_TOOL_ID={result.tool_id or \"NONE\"}')
  print(f'ZAPIER_REASON={result.reason}')
  print(f'CANDIDATES_CONSIDERED={result.candidates_considered}')
  "
  ```

  This calls the canonical helper instead of letting the agent improvise. The helper runs three matching paths (name slug → description fuzzy → permissive `gmail|email` + `send|reply` filter excluding calendar/drive). Per `EMAIL_DRAFT_PROTOCOL.md` §3c v2.12.6+ logic.

  Capture stdout. If `ZAPIER_SEND_TOOL_ID` is `NONE`, cache that fact for the session — `N send` falls back to native Gmail threaded reply (no error, just degraded threading). If a tool ID came back, cache it; `N send` uses it.

  **No agent improvisation:** the orchestrator does NOT scan Zapier tools itself. The helper is the source of truth. Same enforcement model as `CANONICAL_ACTIONS` for action labels.
- Read M's primary email from entities.json.

# Phase 3 — Fetch unread mail

Fetch via the seam-resolved mail-search tool with the `{"in_inbox": true, "unread": true, "newer_than": "14d"}` intent (compiled per provider by `connector_adapters/mail.py`), pageSize 50 (or the connector's equivalent cap). Pass-through providers (Superhuman-class) take the structured intent directly.

Parse response: list of threads with messages, dates, senders, subjects, snippets.

# Phase 4 — Priority scoring

For each thread, compute priority score:
- +30 if reply to a thread M started (M is in the from: history)
- +25 if sender is in entities.json with `org_id` set (real relationship)
- +20 if subject/body contains deadline language ("by Friday", "EOD", "asap", explicit dates)
- +15 per day of age, capped at 7d (older = more aging penalty)
- −20 if sender domain is automated (`no-reply@`, `notifications@`, `mailer@`, `do-not-reply@`)
- −15 if newsletter signal (`unsubscribe` in body OR `List-Unsubscribe` header)
- −10 if sender's email is in `_hq/data/known-newsletters.txt` (if file exists)

**Financial-signal override (v3.1+):** if the sender matches EITHER:
- local-part regex `^(billing|invoices?|estimates?|payments?|accounting)@` (case-insensitive — catches generic billing-system addresses), OR
- sender domain (right of `@`) is listed in `_hq/data/known-billing-domains.txt` (treat-as-empty-if-missing; same pattern as `known-newsletters.txt`), OR
- (fallback when that file is missing or empty) the sender domain matches the conservative built-in set `{intuit.com, stripe.com, bill.com}` by domain-suffix, so `notifications.stripe.com` / `quickbooks.intuit.com` count too. This fallback exists ONLY so a first-fire workspace (where the operator hasn't seeded the file yet) doesn't score a Stripe/QuickBooks/Bill.com invoice as junk. It is read-only — it does NOT write or seed the file. Once the operator seeds `known-billing-domains.txt`, that file is authoritative and the fallback is moot,

then the financial-signal flag is set on this thread. When the flag is set, the −20 automated-domain rule and the −15 newsletter-signal rule and the −10 known-newsletters rule DO NOT apply, AND a +30 financial-signal bonus is added. Net effect: a $10,400 QuickBooks estimate that would previously have scored −35 (auto + List-Unsubscribe) lands at +30 instead and surfaces in the priority list. Why this rule exists: M's testing 2026-05-07 (Acme Logistics $10,400 estimate from QuickBooks) showed real-money signals being demoted out of top-5 by the automated-domain rule. Billing systems are STRUCTURALLY automated by design — the −20 demote is wrong for that class of sender.

Recommended seed for `_hq/data/known-billing-domains.txt` (one domain per line, lowercase, no `@`): `notification.intuit.com`, `quickbooks.intuit.com`, `intuit.com`, `stripe.com`, `notifications.stripe.com`, `bill.com`, `notifications.bill.com`, `invoice.docusign.net`, `invoices.pandadoc.com`. The file is workspace-local — agent should NOT seed it on first fire (that would be agent-improvises-around-canonical-paths); the operator seeds it during onboarding or as a `cleanup` recommendation surfaces specific demoted senders worth allowlisting.

**Learned sender-priority rules (Phase 6 Loop 1 — applied LAST, after the hardcoded rules + financial-signal override, BEFORE ranking):** the CEO's own triage behavior is captured as `triage_feedback` events and turned into approved priority rules by insight-generator Pass 13. Apply them here so the highest-frequency surface becomes personally accurate instead of statically scored:

```python
import sys; sys.path.insert(0, "shared/scripts")
from triage_feedback import load_sender_priority_rules, apply_rules_to_score
rules = load_sender_priority_rules("<abs workspace root>")["rules"]   # treat-as-empty-if-missing
# for each candidate thread, after all hardcoded scoring:
score = apply_rules_to_score(score, sender="<sender addr>", domain="<sender domain>", rules=rules)
```

A learned `demote` rule (`you've skipped everything from this newsletter`) subtracts; a `promote` rule (`you always act on this sender`) adds — the exact generalization of the hand-coded financial-signal +30, but learned per workspace and covering every sender class. This is a scoring input only; it never auto-acts on mail. `load_sender_priority_rules` treats a missing store as empty, so a fresh workspace behaves exactly as before.

Take top 5 non-noise items by score. If no item scores >0, surface the top by aging only.

**Phase 4.5 — Counterparty-level already-replied dedup (v2.10.8+):**

Per-thread "did the user already reply" check is structurally insufficient. Bare `Re:` subjects, header-stripping mail clients, and Gmail's threading-by-subject fallback regularly cause M's reply to land in a NEW thread instead of continuing the original — so the original thread reads "unanswered" while a sibling thread holds the reply. This was the empirical Apr 29 bug: Sam's diagnostic dump thread surfaced as priority despite M having replied 16 hours earlier in a split sibling thread.

Fix at this layer: for each candidate thread that survived priority scoring, run ONE Gmail search per candidate:

```
<seam-resolved mail-search tool>(query = compile_search(
    {"from_me": true, "to": "<counterparty_email>", "newer_than": "2d"},
    <declared provider>), pageSize: 5)
```

For each match in the result, compute crude n-gram overlap (3-grams or higher) between the match's body or subject and the candidate's last inbound message body or subject. If overlap ratio ≥ 0.30 (tunable), suppress the candidate from the top-5 surface — the user has already replied, just to a sibling thread. Log a `chat_suppressed` event with `reason: "counterparty_reply_in_sibling_thread"` and `suppressed_thread_id` for audit/cleanup visibility.

Why crude n-gram is fine for v1: false suppression cost is low (the same email still surfaces in Gmail; M can `re-run` Inbox to retrigger) while false positive cost is high (Sam's complaint). Tighten later if needed (BM25, embedding similarity). The cheap check captures 80% of the bare-`Re:` thread-split cases.

This filter runs AFTER priority scoring AND AFTER the Phase 5 type-detection noise-filter. It's the last gate before the top-5 list is finalized.

# Phase 5 — Type detection (counts ALL threads, not just top-5)

Classify every thread so noise can be counted and surfaced:

- **email_reply** (default) — standard threaded reply needed.
- **calendar_invite** — message has `text/calendar` part OR sender is `noreply@google.com` style with subject like "Invitation:" / "When:".
- **contract** — sender is DocuSign/PandaDoc OR subject contains "ready for signature" / "envelope".
- **noise** — drop from top-5, but count by sub-category for the visibility line:
  - `listings` — real estate (Zillow, Redfin, Realtor.com, MLS), job alerts (LinkedIn, Indeed), property/inventory listings
  - `marketing` — newsletters, promotional, has `List-Unsubscribe`, "weekly digest", domain in known-newsletters.txt. **Exception (v3.1+):** if the financial-signal flag from Phase 4 is set on the thread, do NOT classify as `marketing` — billing systems carry `List-Unsubscribe` for legal compliance but the message is real money. Financial-signal threads route to `email_reply` regardless of newsletter-shaped headers.
  - `calendar` — automated Google/Outlook calendar reminders/auto-edits, NOT actual invites
  - `security` — sign-in alerts, security notices, 2FA codes, account verifications
  - `self_test` — sender email == M's own primary email

Maintain a counter: `{listings, marketing, calendar, security, self_test}`. This drives the noise-breakdown line in Phase 8 (REQUIRED — surface even when all sub-counts are zero, so the filter's work is visible).

# Phase 5.5 — CRU pass: cross-reference inbound mail against open commitments (v3.14.5+, silent)

Per `shared/scripts/cru_match.py` Path 4. Sister to past-meetings Phase 4.6 (transcript) but scoped to inbound email. The premise: when a counter-party emails the user, their message is often the delivery on something they owed ("here's the deck", "attached the report as promised"). Auto-detecting closes the loop on OWED-TO-YOU commitments without the user manually clicking `mark received`.

**This runs over ALL fetched threads from Phase 3 — not just the top-5 priority set.** A delivery email can land below the priority cut yet still resolve a commitment. Run the CRU scan against every inbound thread.

**How a reply closes something (REPLYCLOSE).** Three bases, in the user's terms: (1) their message quotes the item strongly enough AND says it's done — the long-standing behavior, unchanged; (2) **they replied on the thread the item came from** with either "it's done" wording or the document the item asked for — that closes outright; (3) **the same thing off-thread** becomes a confirm, never a close, because off the thread the only link is wording, and wording is what has been missing the real deliveries all along. A reply that fits two open items closes neither — it asks. Every closure stays reversible via `undo`.

**Only THEIR reply counts.** A message from the CEO is refused outright, not filtered per item — on a thread the CEO answered last, "the latest message" is the CEO's, and matching it here would close the CEO's own promises as though a counterparty had delivered them. That is the sent path's job (`reconcile-sent`), on the sent path's evidence.

Skip entirely if:
- Zero inbound threads fetched this fire (nothing to cross-reference).
- Open-commitment count is zero (helper returns `[]`).

**These skips are exhaustive — the run mode never adds one (v4.5.2 R2).** Scheduled and manual fires BOTH run this scan in full; "autonomous run, no connector fetch" is improvisation (FINDINGS F-47 P1a's class).

Otherwise, build ONE list over ALL fetched threads — for each, resolve the sender's email to a `person_id` (via entities.json + aliases.json, already loaded in Phase 2) and take the latest message's subject, body, conversation id, attachment flag, and **its own ISO-8601 timestamp** (`ts` — the connector's raw value, never a reformatted display date; EVORDER layer 3 reads it). **A thread whose sender does not resolve still goes in the list with `sender_person_id: ''`** — the helper counts it as unresolvable rather than letting it vanish, which is how a whole quiet fire gets explained. Then execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from reconcile_inbound_commitments import (
    reconcile_inbound_and_receipt,
    validate_inbound_reconcile_ran,
    PrimaryUserUnresolvedError,
)
from primary_user import resolve_primary_user

workspace_root = '<absolute path to the workspace root>'
user_id = resolve_primary_user(workspace_root)   # deterministic — do NOT guess (Bug #102)

# One dict per fetched thread's latest inbound message.
inbound_messages = <[{'message_id', 'ts', 'sender_person_id', 'subject', 'body',
                      'thread_id', 'has_attachment'}, ...]>

receipt = reconcile_inbound_and_receipt(
    workspace_root, inbound_messages,
    user_person_id=user_id,
    source_skill='inbox',
    fired_via='scheduled',            # 'manual' on a chat-phrase fire
    exclude_captured_since=fire_start, # from the top of this fire — fence layer 2
    provider='<the seam-resolved provider>',
    # TRAINFIX F-4 — leave None on a real read. Set it to the plain-English
    # reason when the inbound read could not happen at all (paragraph below).
    fetch_blocked=None,
)
print('CRU inbox: closed=%s pending=%s updated=%s batch=%s'
      % (receipt['n_auto_closed'], receipt['n_pending'],
         receipt['n_updated'], receipt['batch_id']))
"
```

`thread_id` and `has_attachment` are what let a reply be recognized as the delivery rather than as words about it; omitting them is safe but leaves those checks inert, and the receipt says so. Never infer `has_attachment` from a body that says "attached" — it is the connector's flag or nothing. **`ts` is the one field where omitting is safe but MALFORMING is not (EVORDER).** Layer 3 refuses to close a commitment captured after the reply arrived. Absent `ts` leaves it inert; a present-but-unparseable `ts` (a display string, or date-only) fails SAFE and LOUD — the pass closes nothing at all and prints `RECONFENCE: inbound_ts=…` on stderr. Pass the connector's raw timestamp through unformatted. `n_stale_evidence_skipped` in the receipt counts what layer 3 refused; non-zero is the fence working, not an error.

**The circularity fence (REPLYCLOSE §3).** Layer 1 needs no argument here — the helper derives each message's own ref internally and drops any commitment attributed to that very message. That matters most on THIS rail: this same skill stamps `data.source_ref: gmail:<message_id>` on the commitments it extracts from inbound mail, so without it the message that created a waiting-on item would be the message that closes it on the next scan. Layer 2 is `exclude_captured_since=fire_start`. Anything captured BEFORE the fire start stays fully matchable, so a reply that genuinely delivers on an earlier promise still closes it.

**Self-validate (mandatory).** `v = validate_inbound_reconcile_ran(workspace_root, since_ts=fire_start)` — `v["ok"]` must be True. False means this pass did not actually run; append the `pack_run.data.errors[]` entry below and do not treat the zero as clean. Also read `receipt["signal_fields"]`: if messages were scored but neither the conversation nor the attachment field was present on any of them, the reply checks could not run at all and a zero closure count means nothing — `receipt["summary"]` carries a plain-language heads-up in exactly that state.

**An unresolved user ABORTS this pass** — `reconcile_inbound_and_receipt` raises `PrimaryUserUnresolvedError`, writes no audit event, and closes nothing. Do NOT catch it and continue: direction is derived from owner vs the user, so with no user the reply bases are inert and a clean zero would be a lie. Check that the path passed is the WORKSPACE ROOT, not `_hq`.

**If the inbound read cannot happen at all — no mail connector resolves, the connector budget is exhausted, or every account is still unclassified — do NOT call this helper with an empty list and let it write a clean zero (TRAINFIX F-4).** A fire that read nothing and a fire that read everything and found nothing produce the identical `inbound_scanned_count: 0` audit, and the first one is a dead rail wearing the second one's receipt. Call it with `inbound_messages=[]` AND `fetch_blocked="<what was missing, named in plain language>"`: the audit lands stamped blocked with that reason, nothing is closed and no confirm is queued, and `validate_inbound_reconcile_ran` correctly refuses it. Stay silent to the CEO per `shared/RELIABILITY.md` §1 and log the one-line skip — loud in the SUBSTRATE, quiet in the chat. (The sent rail has carried this since MAILSEAM item 8; this rail shipped without it.)

**The stdout is for diagnostic logging only.** Per CONTRACT.md Rule 4/9: `commitment_resolved`, `commitment_updated`, `commitment_review_proposed` event-type names NEVER appear in chat. The user sees the effect on the next Waiting On fire — resolved items drop off. Do NOT narrate "I closed 2 commitments" in the inbox widget or anywhere in this fire.

**Why pre-filter by sender, not recipient:** on outbound (Path 1) the USER is the owner and we resolve what the user owed. On inbound (Path 4) the SENDER is the owner — their email is evidence THEY delivered on what they owed the user. New-ask language ("can you also send X") is intentionally NOT acted on here: that's the counter-party asking the user for something new (inbox-triage's job to spawn a user-owed commitment), not a resolution of the sender's own commitment.

**Failure handling:** if the CRU pass errors (events.jsonl read failure, helper import fails, JSON malformed), swallow silently and continue — this is best-effort enrichment and must NEVER block the inbox render. **Append a `pack_run.data.errors[]` entry** (per Phase 7): `{"phase": "5.5_inbound_cru", "reason": "<short>", "detail": "<truncated stderr>", "thread_id": "<id>", "ts": "<UTC ISO — never the local wall clock>"}`. An unresolvable SENDER is not an error — it is a counted outcome in the receipt; do not drop those messages before the call.

**Threshold tuning:** same `HIGH_CONFIDENCE_THRESHOLD = 0.55` / `PENDING_REVIEW_THRESHOLD = 0.30` as the other paths. Conservative for launch; tighten/loosen once Pulse pending-review confirmation telemetry exists.

# Phase 5.6 — Surface-preference filter (Phase 6 Loop 2 — runs before rendering)

The CEO's repeated dismissals are learned suppressions (insight-generator Pass 14 → `_hq/data/surface-preferences.json`). Drop any top-5 item the CEO has told the system to stop surfacing, BEFORE building the data_view:

```python
import sys; sys.path.insert(0, "shared/scripts")
from surface_preferences import load_surface_preferences, is_suppressed
prefs = load_surface_preferences("<abs workspace root>")   # treat-as-empty-if-missing
top5 = [t for t in top5
        if not is_suppressed(prefs, "inbox", item_class="<sender|newsletter>",
                             entity_id="<sender domain or person_id>")]
```

Missing store → no-op (fresh workspace unchanged). This only hides a surfaced prompt; the thread stays in the mailbox and in the substrate. Every widget orchestrator applies this same filter (commitments, pulse, past/upcoming-meetings, friday-wrap, relationship-moves, morning-brief).

# Phase 6 — Per-thread DRAFT TEXT generation (lazy)

Per `EMAIL_DRAFT_PROTOCOL.md`: do NOT create email drafts at fire time. Generate TEXT only.

**email_reply:**
- Run `email-writer` skill with voice calibration. Pass: thread context (last 3 messages), sender info from entities.json if available, recommended reply type.
- Capture draft body TEXT in chat-session memory.
- Show full draft inline in Phase 8.

**calendar_invite:**
- No draft creation. Surface in chat with action set: accept / propose [time] / decline / skip.

(v2.12.2 retired the standalone "contract" category — contracts that show up in inbox surface as `email_reply` like any other thread; the user invokes a specialized contract-review skill on demand if needed. v2.14.19 confirmed the retirement during the structural audit.)

# Phase 7 — Memory updates (silent per Rule 9)

Append to events.jsonl:
- `connector_read` for the mail fetch
- The fire receipt — **ONE call to the canonical receipt helper (`shared/scripts/receipts.py`, contract in `shared/RECEIPT_CONTRACT.md` — v4.5.2 R1). NEVER hand-roll the receipt JSON**: hand-rolled shapes are exactly how the id/field drift that broke health-check and usage-report happened (FINDINGS F-10b/F-49).

```python
from telemetry import build_pack_run_telemetry
from receipts import log_receipt

# Track connector calls during the fire (append to a list as each call fires)
connector_calls = [
    {"connector": "<declared provider>", "operation": "<seam-resolved search op>", "ms": 320},
    {"connector": "<declared provider>", "operation": "<seam-resolved thread-fetch op>", "ms": 180},
    # ... etc
]

# At fire end, build the telemetry block
tel = build_pack_run_telemetry(
    prompt_text=ORCHESTRATOR_PROMPT_TEXT,    # the registered prompt content
    response_text=widget_html + briefs_md,    # widget HTML + post-widget sections
    connector_calls=connector_calls,
    duration_ms=elapsed_ms,
)

log_receipt(
    WORKSPACE_ROOT, "inbox",
    fired_via=lateness["receipt_fired_via"],  # from Phase 2.9 — manual | scheduled | catchup; never guess it
    surfaced=n_drafts,
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={"items_drafted_text": n_drafts, "items_persisted_to_gmail": 0,
                "errors": [], **tel},
)
```

The helper stamps the canonical field set (task_id + kind, status, fired_via, machine) and validates the vocabulary; seq/ts are auto-stamped inside the locked writer.

Telemetry writes silently per CONTRACT.md Rule 9 — NEVER surfaced to chat. The `usage report` skill is the on-demand path that aggregates and displays.

NO `draft_created` events at fire time. Append to staging_emissions.jsonl per draft TEXT generated.

# Phase 8 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

**Mandatory execution contract (v2.10.8+):**

You MUST execute the renderer via `mcp__workspace__bash`. You MUST NOT hand-write or paraphrase the chat string under any circumstance. There is no "Example rendered output" in this file by design — earlier versions included one and the LLM (you) paraphrased it instead of running the Python. v2.10.8 removes that escape hatch.

**Step 1 — verify renderer imports (FIRST action of Phase 8, before anything else):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from widget_transport import render_and_persist; from chat_output_renderer import validate_chat_output, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError, WrapperContractError; print('OK')"
```

If stdout is not exactly `OK`, ABORT the fire. Surface the error to chat in plain English: `(Renderer pre-flight failed — chat output deferred. Diagnostic: <error>.)` Do NOT post any widget. Do NOT paraphrase. Do NOT continue.

**⛔ ZERO-MANIPULATION CONTRACT (v2.14.34+, transport-updated EW2+T):** the render is sealed — post via `widget_transport.render_and_persist` and pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`, never hand-composed or post-processed HTML. No minification, no whitespace stripping, no "trimming for size", no removing what looks like duplicate elements — not on `transport["html"]`, not on the persisted file. Every `<div class="cr-action-input">` wrapper is functionally required. The transport runs `validate_rendered_widget` internally and raises `WrapperContractError` if any wrapper is missing.

**v2.14.37+ extension (EW2+T) — `show_widget` mandatory after a clean transport call.** If `render_and_persist()` returns without raising, you MUST call `mcp__visualize__show_widget` with `transport["html"]` as `widget_code`. Narrating that the widget "couldn't transmit," "hit a session payload limit," "exceeded the live widget surface," "was too large," or any other reason is FORBIDDEN — none of those phrases exist in this codebase, and pagination (~10 rows/page) keeps every page inside the relay budget. If `show_widget` itself errors, surface the error string verbatim and STOP.

**v2.14.37+ extension — markdown lists are not a substitute for widget rendering.** Triggered specifically by the 2026-05-07 cr-inbox follow-up where the agent emitted a 10-item markdown list in response to "surface past emails" instead of re-firing the widget with the noise filter peeled back. Any "surface past emails" / "show me the X" / "list the Y" follow-up goes through `render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`). Markdown bullet lists in chat as a substitute are FORBIDDEN. Re-fire with the adjusted filter threshold so noise-filtered-but-relevant items appear as read-only `tracked_items` rows in the widget.

See `orchestrator-commitments.md` "ZERO-MANIPULATION CONTRACT" section for the full diagnosis lineage.

**v2.13.0 architectural enforcement:** the renderer raises `CanonicalActionError` if any item has an action verb not in `CANONICAL_ACTIONS` (e.g., `keep as draft`, `your call`, numbered `send 1` — all rejected). It raises `LeakDetectedError` if the rendered output contains forbidden patterns (entity IDs, internal file paths, event-type names, plugin-version refs, the apply-choices payload string, etc.). Both are BLOCKING — no silent fallback, no "post anyway."

If `CanonicalActionError` fires: the orchestrator's data view has a non-canonical action verb. Fix at the data layer — pick a canonical verb from `CANONICAL_ACTIONS`. NEVER add the misspelled verb to the allow-list.

If `LeakDetectedError` fires: the data view (or a body line, header, context_tag, sub-item summary) contains a forbidden pattern. Strip the leak from the data view. NEVER add the leaking pattern to the allow-list.

**Empty-state rule (v2.14.19+, v3.2.3+ tracked_items population):** if filtering produces 0 surviving threads (no priority items, no calendar invites, no contracts), DO NOT improvise an "inbox is clean" widget by hand-typing HTML. Build the `all_clear_summary` data view and pass to `render_chat_output_widget()` — never hand-build.

**`tracked_items` population (v3.2.3+):** the empty-state widget renders a "WHAT'S ON THE BOOKS" section from the `tracked_items` array — pre-v3.2.3 the orchestrator passed `tracked_items: []` empty, making the empty-state surface less useful. v3.2.3+ populates it from the noise-filtered classes that survived Phase 5 type detection but didn't make the Phase 4 priority cut. Three classes contribute, in this order (cap total at 7 rows):

1. **Vendor estimates / financial-signal threads** (from §4 financial-signal override) that scored above 0 but didn't make the top 5 priority list. Direction: `Read-only`. Title: `<sender display name> · <subject (truncated 60 chars)>`. Due: plain-English age, e.g. `2 days old`.
2. **Auto-decline / auto-accept calendar invites** processed this fire (no user action needed; surface as context). Direction: `Auto-handled`. Title: `<decline | accept>: <invite subject>`. Due: `today` or the event time.
3. **Outbound awaiting reply** — threads where M sent something ≥3 days ago with no reply (Phase 3's secondary scan if implemented; otherwise skip this class for v3.2.3 — covered by cr-commitments OWED TO YOU). Direction: `Sent · awaiting`. Title: `<recipient name> · <subject>`. Due: `<N> days waiting`.

Final data view shape:

```python
data_view = {
    "widget_mode": "all_clear_summary",
    "header": "Inbox — nothing pressing this morning",
    "sub_header": "<weekday>, <date> · <time> check",
    "counters": [
        {"label": "Unread", "value": n_unread},
        {"label": "Priority", "value": 0},
        {"label": "Calendar invites", "value": n_invites},
        {"label": "Drafted replies", "value": 0},
    ],
    "summary_line": "Overnight inbox is clean — N unread but nothing surfaced as priority. ...",
    "tracked_items": tracked_items,  # populated per v3.2.3 rule above; empty list if no items in the three classes
    "footer": None,
}
```

If the three classes together yield zero rows, pass `tracked_items: []` and let the widget show only the summary callout — the rule is "populate what's there, don't pad." NEVER hand-build the empty-state widget. See `orchestrator-commitments.md` for the full diagnosis of why this rule exists (v2.14.18 fresh-install bug — model bypassed renderer at empty-state, broke 3 contracts).

**Step 2 — build data_view, render widget HTML, post via show_widget (v2.10.9+):**

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist

# Build data view from Phase 4-7 results
data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "inbox",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly (no 60-min fire-marker window)
    "header": f"Inbox · {today_short} · {n_priority} priority threads. Drafts ready to review.",
    "sub_header": (
        f"Noise filtered ({total_noise} total): "
        f"listings ({n_listings}), marketing ({n_marketing}), "
        f"calendar ({n_calendar}), security ({n_security}), self-test ({n_self_test})"
    ),
    "sections": [{"title": None, "count": None, "items": [item_for_thread(t) for t in top_threads]}],
    "quick_read": quick_read_summary,            # 1-3 sentences, omit if N <= 2 — see consistency rules below
}

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="inbox")

# Phase 6 Loop 1 — cache each item's triage context alongside the recipient/
# subject cache so apply-choices Step 3e can write the triage_feedback event at
# dispatch: {sender, domain, bucket_assigned (the Phase-5 label: "surfaced" for a
# top-5 item, "noise:<subcat>"/"fyi" for a demoted one), draft_offered (True when
# a draft body was generated in Phase 6)}. This is per-item context the widget
# already keys by n — extend it, don't add a second cache.

# EW2+T (F-15): the transport runs the full validator chain (canonical
# actions, data shape, leak scan, wrapper contract) and persists the sealed
# render. Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) — never
# a hand-composed variant, never a post-processed one.
```

The widget renders inline with per-item buttons; user clicks accumulate locally; "Apply all" fires `apply choices: [...]` consolidated payload that the `apply-choices` skill catches and dispatches through the reply handlers below. Do NOT compose chat strings or paraphrase — the widget HTML IS the post.

**Quick Read consistency rule (v2.14.29+ — HARD CONTRACT):** `quick_read_summary` may ONLY reference threads present in `top_threads` (the items rendered in the widget). NEVER reference noise-filtered threads, below-threshold threads, or threads suppressed by `chat_dismissal`. Even if the most "interesting" overnight signal lives in a noise-filtered thread (e.g., a QuickBooks invoice notification, a marketing email with surprising content, an automated security alert), Quick Read MUST NOT mention it.

Why this rule exists: M's testing 2026-05-06 (item #10) flagged that Quick Read referenced a Acme Logistics / QuickBooks invoice email that wasn't in the widget's top-5. Investigation found that the invoice was correctly demoted by Phase 4 scoring (auto-domain + List-Unsubscribe → −35 score → falls out of top-5) and properly counted in the noise sub-header. But the LLM still saw it during the data-gathering pass and pulled it into Quick Read commentary, creating a "you mentioned this but I can't find it" inconsistency for the customer. Quick Read commentary is LLM-owned per `SHARED_CHAT_OUTPUT_PROTOCOL.md`, so until v2.14.29 nothing prevented this; the customer-facing fix is the explicit rule above.

**Note (v3.1+ — financial-signal override):** the specific Acme Logistics demote case described above is now fixed by the financial-signal override in Phase 4 — QuickBooks-shaped senders no longer get the −20/−15/−10 demotes and surface in the priority list directly. The Quick Read consistency rule still stands; it just no longer fires on this specific example.

If the most interesting overnight signal IS in a noise-filtered thread and you genuinely think the customer should know about it, that's a signal to revisit Phase 4–5's noise filter — not to leak it through Quick Read. File the case in events.jsonl as a `noise_filter_review_needed` event with the thread metadata. **Nothing renders that event today** — it is an audit trail, not a hand-off, and saying otherwise would be the second wrong answer in a row: the retired Pulse chat used to be named here, and the round-1 correction pointed at a weekly pass that does not read it either. File it and move on; if these cases accumulate, the filter is what needs revisiting. Do NOT smuggle the reference into today's Quick Read.

**Step 3 — Post the chat-links section (v2.12.0+):**

After posting the widget, emit a second chat turn with markdown source thread links per item. Format per `shared/CHAT_ACTION_WIDGET.md` § "Post-widget chat-links section":

```markdown
**Links:**

1. [<Sender> — <subject>](<thread URL — the connector-returned URL, else connector_adapters.mail.deep_link(provider, thread_id)>)
2. ...
```

- Numbering matches the widget items exactly.
- Use the URL the mail MCP returned on the thread-fetch / search call; fall back to `connector_adapters.mail.deep_link` only when the connector returned none (N8: no known host for the provider → drop the link, never synthesize a broken one).
- Inbox items have no `.docx` brief — just source thread links.
- If 0 items have a thread URL (rare — should always exist for unread email), omit the block.

This is the surface that lets the user open the original thread in their mail client and respond there if they want, per Sam's Apr 30 ask: *"this needs to be a response to an email. I'd want to see the link to the most recent email on the subject so you can click it and respond."*

The noise-breakdown line is REQUIRED in `sub_header` — surface even when all sub-counts are zero, so the filter's work is visible. Hidden noise is the bug; visible noise is the feature.

**Per-item data shape** (build this in `item_for_thread`, v2.12.1+ — includes `original_thread`; v2.12.4+ adds `url`):

For every email-shaped item, populate `original_thread` with the source thread snippet so the widget can render a collapsible "Original thread" block above the draft. Pull from Gmail MCP at fire time (you already have the thread loaded for draft generation). Shape:

```python
"original_thread": {
    "author": "<sender display name + email>",      # "Sam Sample <sam@example.com>"
    "date": "<localized timestamp>",                # "Apr 28, 2:14 PM"
    "subject": "<exact subject line of the most recent message in the thread>",
    "body": "<plaintext body of the most recent message — first ~800 chars; truncate with ellipsis if longer>",
    "url": "<connector-returned thread URL, else connector_adapters.mail.deep_link(provider, thread_id)>",  # v2.12.4+ REQUIRED when known
}
```

The renderer wraps this in `<details>` so it's collapsed by default; user clicks "Original thread" to expand. Sam's Apr 30 ask: he wanted to see what he was responding to inline before reviewing the draft.

**The `url` field is REQUIRED whenever a thread URL is available** (v2.12.4+, per M's Apr 30 ask: *"I dont see the link to see the original thread in gmail"*). The renderer adds an "↗ Open in [mail client]" link at the top of the expanded block when `url` is set. Use the URL the mail MCP returns on the thread-fetch / search call — don't synthesize (deep_link is the only sanctioned fallback; it degrades to None for providers with no stable host, N8).

If thread content can't be retrieved (rate limit, permission error, etc.), omit the entire `original_thread` field — the renderer skips the block silently. Don't surface partial data.



- **email_reply** (v2.14.38+ — replaced `skip` with `snooze 3d` + `not relevant` per M's standardization across all deferral clusters): `{n, icon: "✉", name: "<resolved name>", subject, context_tag, metadata: [("Subject", real_subject), ("To", recipient_email)], body_lines: [...], actions: ["N send", "N draft", "N escalate to memo", "N snooze 3d", "N not relevant"]}`
- **calendar_invite** (v2.14.38+ — calendar invites get a tighter cluster: a snooze doesn't fit the "decide now or push" mental model. Just accept / propose / decline plus `not relevant` for "this shouldn't have been routed to me / wrong invite" per M 2026-05-07): `{n, icon: "📅", name, subject, context_tag: "<day time, conflict info>", actions: ["N accept", "N propose [time]", "N decline [reason]", "N not relevant"]}` (no metadata or body)
- **contract**: retired in v2.12.2 — contracts show up as `email_reply` like any other thread. No separate item shape.

**Pre-build resolution rules (your job, BEFORE calling renderer):**
- Resolve every `person_NNN` / `org_NNN` to canonical name
- Subject fallback: if source thread's subject is "Re:" with nothing after, use `latest_message_subject` from the thread; if blank, walk back through prior messages; if still blank, generate a 5-word descriptor from the body. Never put a blank subject in the metadata
- For email_reply, the body draft is whatever `email-writer` returned — split into lines, strip trailing whitespace, pass to `body_lines`

**No example rendered output is included by design (v2.10.8+).** Read `shared/scripts/chat_output_renderer.py` if you need to understand the output format — but never paraphrase from any rendered example you find anywhere. Execute the renderer; post what it returns.

`N send` and `N. send` (with period) both parse on user reply. Accept either.

`re-run` re-fires (no `--force` flag in user-facing language).

Drafts are TEXT only in this chat turn — they have NOT been written to Gmail/Outlook yet. Per-item user choice (`N send` / `N draft`) determines what lands in mail. Per `EMAIL_DRAFT_PROTOCOL.md`.

# Phase 9 — Failure handling (Rule 8)

- Mail rate limit: degrade gracefully, surface `(Mail snapshot from earlier this morning — refresh with re-run.)`.
- Send fail mid-batch: stop at failure, surface inline retry: `(Send stopped at item N — retry with send all from N.)`.
- Voice calibration unreadable: fall back to neutral professional tone, surface inline note `(Voice calibration unreadable — using neutral tone for now.)`.
- docx skill failure on memo escalation: per Rule 8, plain-English note instead of tool name.

# Reply handling (lazy mail interaction)

**Action surface (v2.10.9+ — all-batch button widget per `shared/CHAT_ACTION_WIDGET.md`):** the per-item actions below render as buttons in a `show_widget`-rendered card, with all selections accumulating in widget local state and one "Apply all" submission firing a consolidated `apply choices: [...]` payload. The receiving `apply-choices` skill parses the JSON payload and dispatches each `{n, action}` tuple through the same handlers below.

**Heavyweight action note for inbox:** `N escalate to memo` produces inline memo content AFTER the user clicks Apply, not before. The widget stays compact during selection; memo expansion happens in the consolidated response.

## email_reply actions (v2.12.2+ — combined edit + disposition)

Per M's Apr 30 ask: standalone `edit` always required a follow-up disposition pick (send vs draft), which forced two rounds. v2.12.2 collapses into combined actions where editing AND deciding what to do happen in one click. v2.14.4+ then consolidated `to drafts` + `edit then draft` into the single `draft` verb (always opens the multi-field edit before saving to Drafts).

Action set (FB-17, 2026-07-19 — `edit then send` retired; the FB-10 inline-editable body is the edit surface):
- `N send` (no input) — compose+send the current draft as-is (edits happen directly on the card body before Apply).
- `N draft` (textarea pre-populated with body, v2.14.4+ consolidated) — user reviews/edits, edited body saves to Gmail Drafts.
- `N escalate to memo` (no input) — promote to memo-writer.
- `N snooze 3d` (no input, v2.14.38+; a primary button since FB-17) — fixed 3-day snooze. Item won't re-surface in inbox until 3 days from now.
- `N not relevant` (no input, v2.14.38+) — 60-day cooldown dismissal. The duration is internal mechanics — never shown to the user. Stronger than the deprecated 24h `skip`; meant for "this shouldn't have surfaced as a priority reply" rather than "I'll deal with it tomorrow."

Display labels (Title Case): `Send`, `Draft`, `Escalate to memo`, `Snooze (3 days)`, `Not relevant`. (`Edit then send` is a LEGACY_DISPLAY_LABEL — never render it on a new widget.)

Handlers:

- `N send` → on demand, compose+send. Use the cached `ZAPIER_SEND_TOOL_ID` from Phase 2 setup if non-NONE; otherwise fall back to native Gmail/Outlook threaded send. Per `EMAIL_DRAFT_PROTOCOL.md` §3c.

  **Confirmation copy (v2.14.0+ — clean, no path narration):**
  - On success: `✓ Sent at HH:MM — Re: <subject> → <recipient>` (one line per send; 24-hour time).
  - **Do NOT add a tail explaining which path was used.** Per M's v2.13.2 ask: *"the trailing 'Note: the Zapier-threaded send tool wasn't detected on this workspace, so the dispatcher fell through to native Gmail reply' message is borderline trailing narration."* The user does not need to know whether Zapier or native Gmail handled it. The send worked. Done.
  - **Only surface a Zapier-not-detected note if Zapier was EXPECTED but the discovery returned NONE AND a send actually FAILED to thread.** Then it's actionable: `(Zapier send tool not detected — sending via native Gmail. Check that your Zap is named exactly 'Command Room — Send Threaded Email' in Cowork → Settings → Connectors → Zapier.)` — surfaced ONCE per session, not per send.

  Write `outreach_sent` event with `via: "zapier" | "gmail_mcp_threaded" | "gmail_mcp_standalone"` indicating the path used. The `via` field is internal audit only — never exposed in chat.
- `N edit then send` *(retired FB-17 — deprecated alias, accepted ONLY from in-flight widgets, never emitted anew)* (with `input` field, v2.12.2+) → replace `body_lines` with the user's edited input verbatim. Then dispatch to the `N send` handler with the new body. Single round.
- `N draft` (with `input` field — multi-field edit on the widget, v2.14.4+ consolidated form) → replace `body_lines` (and any edited To/Cc/Subject) with the user's edited input, then lazy-create the Gmail/Outlook draft. Try to apply `cr-staged-<today>` label; if scope error, continue without (per §3b). Surface plain-English note once per session if labels are blocked. Write `draft_created` event. Confirm `N saved to Drafts.`
  - **Pre-v2.14.4 note:** the legacy verbs `N to drafts` + `N edit then draft` were two separate handlers (one straight-save, one edit-then-save). v2.14.4 consolidated to a single `draft` that ALWAYS opens the edit field — review-then-save is the only semantic. The renderer rejects the legacy verbs.
- `N escalate to memo` → fire memo-writer through the standard chat invocation. The memo-writer produces a .docx via the docx skill and surfaces the link the standard Cowork way. Do NOT emit `file://` links yourself. Then surface in plain English: "Want to send this as the email body, attach it to the reply, or send it standalone?"
- `N snooze 3d` (v2.14.38+) → write `chat_dismissal` event with 3-day TTL (`data.snooze_until: <today + 3d>`). Item won't re-surface in inbox until the date passes. Plain-English ack: `"Snoozed #N for 3 days."` only if mentioned in the consolidated ack.
- `N not relevant` (v2.14.38+) → write `chat_dismissal` event with 60-day TTL AND `data.reason: "not_relevant"`. The 60-day window is internal mechanics — NEVER surface the duration in chat. Plain-English ack: `"Marked #N as not relevant."` only if mentioned. Used for "this shouldn't have been priority-routed" rather than "deal with later."

**Zapier scope (v2.12.3+ — clarified per M's Apr 30):** Zapier is **only** used by `send` and `draft` paths (including a deprecated in-flight `edit then send` alias resolving to `send`). All other actions (`escalate to memo`, `accept` / `propose [time]` / `decline [reason]` for calendar invites, `skip`) don't touch Zapier. If Zapier isn't configured, only the send + drafts paths feel the difference: they fall back to the seam-resolved native draft-create (with the provider's threading field per `connector_adapters.mail.threading_field`) + native send where the backend supports it (less robust threading; some thread splits possible) but still succeed. Every other action is Zapier-independent.

(Removed in v2.12.2: standalone `N edit` action — combined `edit then send` / `draft` (consolidated v2.14.4+; was previously two separate verbs) replace it. Removed in v2.12.0: `N edit [change]` directive — direct text edit replaces directives.)

## Bulk

- `send all` → sequential sends across non-noise items.
- `to drafts all` → bulk save.
- `show more` → re-render with top 10 instead of top 5.
- `skip all` → bulk dismissal.

## calendar_invite actions (v2.14.38+ — tighter cluster than email items)

- `N accept` → call Calendar MCP to accept; confirm.
- `N propose [time]` → on demand, generate proposed-time reply via email-writer + create draft + (try) label.
- `N decline [reason]` → call Calendar MCP to decline; widget exposes textarea for the reason. Note attached to the decline.
- `N not relevant` (v2.14.38+) → 60-day cooldown dismissal. Use when the invite shouldn't have been routed (wrong invitee, irrelevant meeting). Different from `decline` because no decline notice goes back to the organizer — it just suppresses the invite locally for 60 days. Write `chat_dismissal` with `data.reason: "not_relevant"`.

`snooze 3d` intentionally NOT on calendar invites — calendar items are decisions, not deferrals (M 2026-05-07; the since-retired `add to my list` was excluded here for the same reason).

(Removed in v2.12.2: `contract` action category. v2.14.38: `skip` removed in favor of `not relevant` for stronger semantics; the daily fire's no-action behavior provides the same "ask me again tomorrow" effect that `skip` used to.)

For unrecognized → respond in plain English: "Reply with the item number + action — `N send`, `N draft`, `N snooze 3d`, `N not relevant`, `N accept` (calendar), `N propose [time]` (calendar). Or `send all` / `show more`."

# What this orchestrator does NOT do

- Does NOT bulk-process all 50 unread (top-5 only — the rest aren't priority).
- Does NOT auto-send anything (every send is the user's explicit action).
- Does NOT modify entities.json directly (people-crm canonical writer).
- Does NOT create nested mail labels (flat `cr-staged-<date>` only).
