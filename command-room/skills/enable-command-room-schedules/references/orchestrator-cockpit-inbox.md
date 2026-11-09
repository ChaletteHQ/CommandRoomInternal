# Cockpit inbox (taskId: cockpit-inbox)

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** this run is the Inbox chain, fired for the cockpit page: the fetch, one dict per thread, the one `inbox_helpers:run_inbox_surface` call that lands the inbox page, captures and the chain's receipt, exactly as the Inbox chat does. Then it writes the page's Inbox feed through the artifact database tool. It posts NO widget and delivers NO chat surface of its own (the cockpit page is the reader): the chat turn is one line, then STOP (COCKPIT1, cr1#608; four tasks per cr1#619). Nothing here sends mail; a Send pressed on the page is `cockpit-apply`'s to carry out.

Read `cockpit-shared.md` in this folder first and do its shared step 0 and shared step 1 with leg `inbox`. Phase 0 below is the chain's own resolve and is what the shared step names. There is no lateness phase on this task.

**Chat-output rules:** `shared/CONTRACT.md` for the strict contract; `shared/EMAIL_DRAFT_PROTOCOL.md` for draft mechanics (drafts are TEXT in the feed until the page's Send or Draft is applied). **The unattended rule (IDENT1 I-16):** a scheduled fire never asks, never suggests connecting anything, and never offers a choice.

# Phase 0 — RESOLVE (once, before anything else)

This fire touches the customer's files only through the workspace access layer
(`shared/WORKSPACE_ACCESS.md`, CONTRACT Rule 22 v6). Every phase below is one
verb and one JSON envelope: `run_helper` for a read or a computation, and ONE
`plan run_writer` after the fetch — the whole rest of the chain, beside the data
(Phase 5). The two lateness rows a daily chat appends before its fetch are not
written here: a cockpit task has no lateness slot (`cockpit-shared.md`). There are no inline
python bodies in this file any more, because on a merged seat this process is in
a container and the customer's folder is on their own machine — an inline body
there opens nothing at all, which is how this fire used to stop. On that seat
every form below is RENDERED IN THE CONTAINER (`cd "$PLUGIN_ROOT" && python3
shared/scripts/workspace_access.py plan <verb> --json '…'`) and the printed line
is pasted into the device shell unchanged — never rendered on the device, where
no account lives (IDENT1 I-11).

Resolve first, once:

- **Merged seat.** Run `workspace_access.py discover` here, hand the block it
  prints to the device shell, and keep its answer: `WS` (the workspace root),
  `RT` (the installed runtime), `BRAIN`, `MODE`. `runtime_present: false`, or a
  `runtime_version` that differs from what this plugin expects, is a STOP — run
  the update-bridge install step and say so. There is no container fallback.
- **Legacy or local seat.** The same verbs run in this shell, and the Access
  preamble below is what resolves `$PLUGIN_ROOT` and `$WORKSPACE` for them. Run
  it at the top of every bash call — shell state does not survive between tool
  calls in any of the three environments.

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
#    customer opens. Then hand that answer to `workspace_access.py discover
#    --compare '<its JSON>'` in this shell: `runtime_lags: true` (the runtime
#    is absent, or its manifest_sha is not this plugin's -- the version alone
#    no longer changes between builds) is a STOP: run the update-bridge
#    install step once, resolve and compare again, and if it still lags say
#    `lags_line` and stop. There is no container fallback.
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
#  9 FOLDER. Three tiers, one wording here and in the block the customer
#    pastes into the account's Claude instructions (claude_instructions.py,
#    TIER_LINES). If the owner's computer is connected, work in the Command
#    Room folder; if it is not attached, ask for it by name before doing any
#    work. If the owner's computer is not connected, ask for the Command Room
#    folder to be attached from OneDrive or Google Drive before doing any
#    work. If neither is possible, answer as plain Claude and do not go
#    through Command Room. A scheduled chat asks nothing: it works in the
#    folder it was registered with, or stops in one sentence.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```

**`<WS>` and `<RT>` in every command below are that answer**, substituted before
the command is pasted. An `ok:false` envelope is a stop: surface its `reason` in
plain English and end the fire. It is never a reason to run the operation
another way — the improvised path is the class this layer exists to end.


# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. Re-running is cheap because drafts are TEXT-only until the user persists them per `EMAIL_DRAFT_PROTOCOL.md`.


# Phase 2 — Setup

- **Record the fire start FIRST, before any fetch or write:** `fire_start = <now ISO>`, the clock read beside the data (the layer carries `TZ` and the workspace for every helper it starts; CLOCK1 — never `datetime.now()` here: a fire whose clock is two days behind sets a fence anchored two days in the past, and every commitment extracted in between falls outside it). Hold it as `fire_start` — Phase 5's one call passes it. It marks the instant this fire began, so a commitment this same fire extracted cannot be treated as independent evidence for closing itself (the circularity fence, layer 2). It must be taken HERE, at the top, not next to the Phase-5.5 call: taken later it sits after the extraction phases and fences nothing.
- Today's date is the workspace-local day of `fire_start` (CLOCK1), expressed in the workspace timezone by code (`tz.py`), never re-sliced by hand: an unsynced sandbox clock reading two days behind is what surfaced a meeting that had already happened as upcoming. Connector timestamps you render later still go through `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` exactly as before (REQUIRED `workspace_path`; on `TZResolutionError`, proceed with UTC and note it).
- Read entities.json + aliases.json.
- Read voice calibration (cache once for the session).
- **Resolve the mail tools through the seam, ONE call each** — `tool_discovery.discover_mail_search_tool(tools, declared=connector_config.declared_backend("email"))`, `tool_discovery.discover_mail_draft_tool(tools, declared=connector_config.declared_backend("email"))`, and `tool_discovery.discover_mail_send_tool(tools, declared=connector_config.declared_backend("email"))`. The seam resolves the declared backend first — using that provider's own operation vocabulary — and refuses to substitute another product's tool; with nothing declared it falls back to fingerprint discovery (empty map = today's behavior, R4). Zapier legs are excluded from native discovery automatically (pinned server-ids + signature detection, R12/H-H). Never name a provider tool id directly — Superhuman/UUID servers carry no provider substring. On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire. When a seam's answer carries `mail_ambiguous` (two mail connectors, none declared; MAILPICK1, cr1#266) its `tool_id` is null on purpose: nothing was read from either mailbox. Skip the mail leg and say its `reason` as the one line about it, as it came back (it names the declaring phrase and asks nothing). Never pick a mailbox yourself, and make no claim about which connector belongs to which account (work, personal or an address) beyond what the seam itself returned (cr1#451).
- **The LABEL operation has no seam of its own — resolve it through the category helper**, `tool_discovery.discover_for_category("email", "label", tools, declared=connector_config.declared_backend("email"))`. There is no `discover_mail_label_tool`; the five seams cover send / reply / draft / search / thread-fetch only, so labelling would otherwise have no named path at all and a fire that needs it would improvise a provider tool id. Same declared-first, same Zapier exclusion, same drift handling as the seams above.
- **Resolve the calendar tools through the seam** for `accept` / `propose [time]` / `decline [reason]` calendar-invite handlers — `tool_discovery.discover_for_category("calendar", "<op>", tools, declared=connector_config.declared_backend("calendar"))` for the RSVP-respond / event-find / event-create / event-update operations, or `discover_calendar_tool(tools, "<op>", declared=connector_config.declared_backend("calendar"))`, which routes to the same place and falls back to fingerprint discovery when no backend is declared (empty map = today's behavior, R4). **Never call `discover_calendar_tool` bare** — a bare call cannot honor a declaration the customer has already made. Native calendar via the seam, Zapier-excluded — per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE calendar never goes through Zapier (the seam excludes Zapier legs automatically). If no native calendar tool resolves, calendar-invite actions degrade gracefully: surface plain English `(Calendar invite responses unavailable — native Calendar MCP not connected.)` and continue with email-only actions. Never name a provider tool id directly.
- **Load the connector tools FIRST (IDENT1 I-14; Probes C / C2).** On the merged seat's scheduled shape every connector arrives as a DEFERRED stub — display-named, loaded through the host's tool search — so a fire that looks before it loads sees none and reads that as "no mail backend". Ask for the step, rendered from this workspace's own declaration:

  ```bash
  python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "inbox_helpers:connector_discovery_step"}'
  ```

  Do exactly what its `step` says — the host's tool search for each of its `queries`, ONE tool-list refresh if nothing loaded, then the seams below with `discovery_ran: true`. Still nothing: skip the mail leg, and its `absent_line` is the only thing said about it (never a question, never a suggestion to reconnect).

- **Discover Zapier-threaded-send tool — v2.14.0+ MANDATORY helper-based:**

  ```bash
  python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"accounts": [<the mail connector's linked accounts, from its own account list>], "discovery_ran": true, "tools": [<this session's tool list, {name, display, description} each>], "workspace_root": "<WS>"}, "name": "inbox_helpers:mail_seams"}'
  ```

  The answer names every operation this fire can make, resolved once: under
  `email`, the search / draft / send / thread / label ops; under `calendar`,
  respond / find / create / update; and the declared backend for each category
  beside them. A `tool_id` of null is a seam that did not resolve — degrade the
  actions that needed it and say so in plain English; never fall back to naming
  a provider tool id yourself.

  On a scheduled fire the answer may carry `flag_rows` — the seam found the
  declared connector under the scheduled registry's other name (exactly one
  match, `resolved_via: fingerprint`) or could not (skip-and-flag). Land them
  in ONE call, right here, before any fetch:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "inbox", "rel": "_hq/data/events.jsonl", "rows": [<every row in flag_rows from the answer above>]}'
```

  No `flag_rows` in the answer → no call. Never compose the flag yourself.

  This calls the canonical helper instead of letting the agent improvise. The helper runs three matching paths (name slug → description fuzzy → permissive `gmail|email` + `send|reply` filter excluding calendar/drive). Per `EMAIL_DRAFT_PROTOCOL.md` §3c v2.12.6+ logic.

  Cache the whole answer for the session. Where a send seam came back with a null `tool_id`, `N send` falls back to the native threaded reply — no error, just degraded threading. Where one resolved, `N send` uses it.

  **No agent improvisation:** the orchestrator does NOT scan Zapier tools itself. The helper decides. Same enforcement model as `CANONICAL_ACTIONS` for action labels.
- Read M's primary email from entities.json.

# Phase 3 — Fetch unread mail

Fetch via the seam-resolved mail-search tool with the `{"in_inbox": true, "unread": true, "newer_than": "14d"}` intent (compiled per provider by `connector_adapters/mail.py`), pageSize 50 (or the connector's equivalent cap). Pass-through providers (Superhuman-class) take the structured intent directly.

Parse response: list of threads with messages, dates, senders, subjects, snippets.

# Phase 4 — Read each fetched thread (your judgement; no verb)

**After the fetch there is ONE verb in this file (INBOXDRIVE1, ruling R-RW3-1 (a)).** Everything that used to be Phases 4 through 8 — the learned rules, the sibling-reply cut, the learned suppressions, the capture and reconcile rows, the page, the text, the delivery plan, the read row and the receipt — runs inside `inbox_helpers:run_inbox_surface`, beside the customer's data, in one call. What only you can do is READ each thread and fill in one dict for it. Do that for EVERY thread the fetch returned (all accounts), then make the one call in Phase 5. There is no other step between the fetch and that call: no helper to ask, no row to append, no page to write.

One dict per fetched thread — the connector's own values, never reformatted:

- `thread_id`, `message_id` — the connector's own ids from the Phase 3 fetch (never a draft id, F-22); `ts` — the latest message's raw ISO-8601 timestamp (EVORDER reads it; a display date is worse than none); `url` — the thread URL the connector returned, if any.
- `sender_email`, `sender_name`, and `sender_person_id` when the sender resolves in `entities.json` + `aliases.json` (else `""` — the thread still goes in); `subject`; `body` — the latest INBOUND message's plaintext; `has_attachment` — the connector's flag or nothing (never inferred from "attached"). `attachment_names`: the connector's own name for each attached file, as a list, or nothing (never inferred from the body). A file with no name is only ever a proposal, so read the names.
- `from_me: true` when the newest message on the thread is the CEO's own — such a thread contributes nothing to the surface or the capture (outbound promises belong to `reconcile-sent`).
- `kind` — `email_reply` (default), `calendar_invite` (a `text/calendar` part, or an invitation-shaped sender and subject), or `noise` with `noise_class` one of `listings`, `marketing`, `calendar` (automated reminders, not invites), `security`, `self_test` (sender is the CEO's own address). A noise thread needs only `kind`, `noise_class` and its ids — it is counted, never shown.
- `score` — the hard-coded priority score below. The learned sender rules are applied INSIDE the call, after your score, exactly where they always ran; do not apply them yourself.
- `suppressed: "counterparty_reply_in_sibling_thread"` — only when the sibling check below found the CEO's reply on another thread.
- `financial: true` when the financial-signal override (below) applied; `age` — a plain-English age (`2 days old`).
- `draft` — for an `email_reply` you drafted, the reply's lines, TEXT only (Phase 4c); `why` — one plain line on why it matters; `group` — optional, one of `urgent`, `this_week`, `fyi`, `skip`.
- `captures` — the trackable commitments this thread's inbound message carries (Phase 4b), each `{direction, title, kind, due | no_due, evidence, classification_confidence, below_bar, below_bar_reason}`; the call anchors each to the thread's own ids and timestamp.

**Priority score (the hard-coded rules, applied by you, per thread):**
- +30 if reply to a thread M started (M is in the from: history)
- +25 if sender is in entities.json with `org_id` set (real relationship)
- +20 if subject/body contains deadline language ("by Friday", "EOD", "asap", explicit dates)
- +15 per day of age, capped at 7d (older ranks higher)
- −20 if sender domain is automated (`no-reply@`, `notifications@`, `mailer@`, `do-not-reply@`)
- −15 if newsletter signal (`unsubscribe` in body OR `List-Unsubscribe` header)
- −10 if sender's email is in `_hq/data/known-newsletters.txt` (if file exists)

**Financial-signal override (v3.1+):** if the sender matches EITHER:
- local-part regex `^(billing|invoices?|estimates?|payments?|accounting)@` (case-insensitive — catches generic billing-system addresses), OR
- sender domain (right of `@`) is listed in `_hq/data/known-billing-domains.txt` (treat-as-empty-if-missing; same pattern as `known-newsletters.txt`), OR
- (fallback when that file is missing or empty) the sender domain matches the conservative built-in set `{intuit.com, stripe.com, bill.com}` by domain-suffix, so `notifications.stripe.com` / `quickbooks.intuit.com` count too. This fallback exists ONLY so a first-fire workspace (where the operator hasn't seeded the file yet) doesn't score a Stripe/QuickBooks/Bill.com invoice as junk. It is read-only — it does NOT write or seed the file. Once the operator seeds `known-billing-domains.txt`, that file is authoritative and the fallback is moot,

then the financial-signal flag is set on this thread. When the flag is set, the −20 automated-domain rule and the −15 newsletter-signal rule and the −10 known-newsletters rule DO NOT apply, AND a +30 financial-signal bonus is added, and the thread is never `noise` / `marketing`: billing systems carry `List-Unsubscribe` for legal compliance, but the message is real money. Why this rule exists: M's testing 2026-05-07 (an Acme Logistics estimate for an amount, from QuickBooks) showed real-money signals being demoted out of top-5 by the automated-domain rule. Billing systems are STRUCTURALLY automated by design; the −20 demote is wrong for that class of sender. The file is workspace-local: never seed it from a fire.

Mark a thread `financial: true` when the override applied. The call takes the top 5 by score after the learned rules (by age when nothing scores above zero), drops what the CEO has told the system to stop surfacing (Loop 2), fills the empty state's read-only rows (v3.2.3+ tracked_items: financial-signal threads that scored but missed the top 5, capped at 7 rows — populate what's there, never pad), and counts every noise class in the sub-header — REQUIRED even when every count is zero, so the filter's work is visible.

**THE DAY IN THE HEADER COMES FROM CODE (SCHEDVIEW1 5.5).** Do not write the weekday, and do not write the date. The call reads `HEADER_DATE` ONCE, from `tz.local_header_date` (through `inbox_helpers.header_date`), and prints it on BOTH of the fire's headers: the priority view's header and the all-clear view's header (`HEADER_DATE` on each, from the same one call). On Wednesday 2026-09-16 a header composed in prose printed "Tue Sep 16" — a weekday and a date from different days.

**The sibling-reply check (Phase 4.5, v2.10.8+).** A bare `Re:` or a header-stripping client can land the CEO's reply in a NEW thread, so the original reads "unanswered" while a sibling holds the reply (the Apr 29 case: a thread surfaced as priority sixteen hours after it was answered). For each thread you expect in the top 5, run ONE search with the seam-resolved mail-search tool:

```
<seam-resolved mail-search tool>(query = compile_search(
    {"from_me": true, "to": "<counterparty_email>", "newer_than": "2d"},
    <declared provider>), pageSize: 5)
```

If a match's body or subject overlaps the candidate's last inbound message by 3-grams at a ratio of 0.30 or more, set `suppressed: "counterparty_reply_in_sibling_thread"` on that thread. The call takes it off the surface and lands the `chat_suppressed` audit row naming the thread. False suppression is cheap (the thread is still in the mailbox; `re-run` brings it back); false surfacing is what the check exists to stop.

## Phase 4b — What each thread promises (the capture judgement, INCAP1)

The call captures inbound commitments through `inbound_capture`'s own gate (the gate `inbound_capture.capture_inbound_items` runs) — the Stage-D floor, `source_ref` / `thread_ref` minting, dedup, the per-fire cap — and reconciles every fetched thread against the open commitments (REPLYCLOSE's three bases, the circularity fences, `exclude_captured_since` = the fire start). What a script cannot decide is yours, per thread (`inbox-triage`'s "Step: Extract Commitments" is the semantic contract — read it):

- whether the message carries a real commitment at all (the Stage-D floor; a candidate below it goes in with `below_bar: true` and its reason — declared, never dropped);
- which DIRECTION it runs in — `waiting_on` (the SENDER promised the CEO something) or `reply_owed` (an inbound ask the CEO must answer);
- its `title` and `kind` (`promise` when a named counterparty owes or is owed a deliverable; `task`, `scheduling`, `agenda` otherwise).

The fire's receipt carries every counter the capture computed — `n_candidates`, `n_captured`, `n_deduped`, `n_below_bar`, `n_capped`, `n_capture_errors`, and the two direction counts `n_waiting_on` and `n_reply_owed` (RECEIPTCOUNT1, cr1#143) — which is what separates "the lane ran and the mailbox was quiet" from "the lane did not run".

The Maintenance `inbound-mail` job opens commitments from the same mail on its own schedule (cr1#664). A capture whose thread id plus message id that job receipted in the last 24 hours is skipped here and counted as `n_yielded_to_maintenance`, so one promise is opened once.

The reconcile pass inside the call is the matching half of `reconcile_inbound_and_receipt`, run without its write leg: it lands review proposals and schedule-shift markers and closes nothing — the Maintenance `inbound-mail` job carries the write leg, through the single closure path (cr1#664).

**Thresholds:** the same bar and band as every other path, read from `commitment_policy.py` (the one home; `confidence.py` re-exports them) — no number lives in this file. Calibration moves them per workspace through the Loop-4 override file, never through prose.

**Direction doctrine (hard).** The CEO's OWN messages never create waiting-on-them items through this lane — the writer refuses a message whose sender resolves to, or is named as, the primary user. **This lane WRITES and never closes (EVORDER):** closing runs through the single closure path in the Maintenance `inbound-mail` job, so a reply that delivers on a promise closes it at that job's next run (cr1#664). Never narrate captures, closures or event-type names to the CEO (CONTRACT.md Rule 4/9) — the visible effect is the items on the next Waiting On / My Plate fire. One fire captures at most `inbound_capture.DEFAULT_CAPTURE_CAP` items; the rest wait for the next fire — never raise the cap to clear a backlog.

## Phase 4c — Drafts, TEXT only, in the owner's voice (cr1#653)

Per `EMAIL_DRAFT_PROTOCOL.md`: no email draft is created at fire time, and nothing is queued to the mailbox; the owner's Send or Draft on the page is the only door to it. A scheduled run cannot call the `email-writer` skill, so it does what that skill does, here, in this order. The first build said "run email-writer" and a real run drafted from the summary in the workspace instructions instead: no voice file, no corrections. That is what this phase forbids.

1. **The voice, once per run.** When `_hq/BRAND_VOICE.md` exists, read it through the access layer's `read` verb (`plan read --json '{"rel": "_hq/BRAND_VOICE.md"}'`, its line pasted unchanged) and keep its hard rules and its email voice blocks: they are the calibrated override (`shared/VOICE_CALIBRATION.md`). Then read the plugin's own base, in this shell: the `## Voice Block` section of `$PLUGIN_ROOT/skills/email-writer/SKILL.md`. Where the two disagree, the workspace file wins.
2. **Per thread, the owner's corrections for this recipient.** One read beside the data, the call email-writer makes:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"domain": "<the sender's email domain>", "recipient_id": "<the sender's person id, or null>", "skill": "email-writer", "workspace_root": "<WS>"}, "name": "draft_constraints:load_draft_constraints"}'
```

   Its `lines` are constraints, not suggestions: obey each one. Its `override_block`, when present, supersedes the base voice block. Carry its `voice_block_sha256` on the thread's item as `voice_block_sha256` (it is null when the workspace has no learned block).
3. **Draft** the reply from the thread (the last three messages; the sender's record when there is one) under those rules, short and direct.
4. **No blanks, ever.** A draft never contains a bracketed placeholder (`[state]`, `[title]`, `[date]`, or anything like it). When a fact the reply needs is not in the thread or the book, write around it, or ask for it in the reply; when neither works, leave this thread with no draft (its row says it needs the owner). The page refuses Send on a draft that still carries one, and says so.
5. **The gates.** Run the body through the voice-tell detector, in this shell, and rewrite until it passes:

```bash
printf '%s' "$DRAFT_BODY" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context email
```

   Then the date scan (DRAFTDATE1, below): a draft never states a day the row does not hold.

A `calendar_invite` gets no draft. A thread the owner is only copied on gets no draft.


# Phase 5 — ONE call: the chain after the fetch

Render it in the container like every access-layer line, with the writer pair forwarded (`plan run_writer`), and paste the printed line unchanged:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fire_started_at": "<fire_start>", "fired_via": "<scheduled or manual, from shared step 0>", "items": [<one dict per fetched thread, Phase 4>], "n_accounts": <n_accounts from the seam answer, else 1>, "provider": "<the declared mail backend>", "task_id": "cockpit-inbox", "tools": [<this session's tool list, {name} each>], "workspace_root": "<WS>"}, "name": "inbox_helpers:run_inbox_surface"}'
```

`fired_via` is the run mode from shared step 0, never guessed. There is no lateness verdict on a cockpit task (`cockpit-shared.md` says why), so the call composes the ONE receipt row the Inbox chain has always written and appends it LAST, under THIS task's id (`task_id: cockpit-inbox`, never the chain's `inbox`: a still-registered Inbox chat must never see its slot served by a cockpit run). That row is this run's receipt, so skip the receipt call of the shared last step and do its final response only.

When the inbound read could not happen at all — no mail connector resolved, or every account was out of scope — pass `"items": []` and `"fetch_blocked": "<what was missing, in plain language>"`: the call records a blocked read rather than a clean zero (TRAINFIX F-4), and the surface is the quiet form.

The answer's `result` is `{ok, text, page_rel, page_pc_path, bytes_len, push, calls, rows_written, receipt, counts, n_accounts, branch}` — `rows_written` is every row the call landed, the receipt last, so this run's log shows them. `ok: false` with `refused` and `lines` (the writer could not be named, the folder has not finished syncing, the page could not be saved, the read row did not land) means NOTHING after the refusal point was written: `lines` is the whole final response. If the door itself answers `writer_identity_required`, its `line` is the whole final response.


# Phase 6 — The feed, then STOP

Do NOT do the Inbox chat's delivery: this run has no reader in its chat and the page is the surface. Read the landed page back through the layer's `read` verb (the call's `page_rel`) and hand it, with the same `items` list the call took, to the feed builder:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"fire_stamp": "<fire_start>", "items": [<the same items list>], "now_iso": "<now ISO>", "page_html": "<the landed page bytes>", "page_rel": "<page_rel from the call>", "workspace_root": "<WS>"}, "name": "cockpit_helpers:feed_inbox"}'
```

`set` the answer on `feed/inbox` (pinned by the version rule in `cockpit-shared.md`). The feed carries each row's number from the landed page; that number is the apply-choices wire `cockpit-apply` dispatches on, and `fire_stamp` is what refuses a stale choice later. Light: `done` with "N threads, M drafted." On a refusal from the Phase 5 call (`ok: false`), the light is `error` with the first of its `lines`, and the feed is left as it was. Then the shared last step.

## Draft date scan (DRAFTDATE1 — MANDATORY on every rendered draft)

**A draft never states a date, day or deadline the row does not hold.** On 2026-09-07 the drafts on this product invented three: "I'll have it finished by Friday" and "this is on your calendar today" on rows with no due date and no calendar event, and "let's get this paid this week" on a row with neither. Nobody had promised any of those days; sending one makes a commitment the book does not know about.

Before you show or save ANY draft you composed — status note, nudge, chase, follow-up, reply, invite body — run it through the scan:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"draft_text": "<the draft text>", "row": <the row>, "today": "<the workspace's own day>", "workspace_root": "<WS>"}, "name": "inbox_helpers:check_draft_dates"}'
```

The answer is `{ok, phrases, detail}`. A false `ok` names every phrase the row
cannot support — the verb runs `draft_date_scan.assert_draft_dates` and reports
what it raised instead of raising, because an envelope is the answer here.

A date phrase is allowed only when it traces to (1) the row's due date, (2) a calendar event on the row, or (3) a date in the row's OWN words — its title, its quote, the thread subject. `today` is the workspace's day (`tz.py`), never a UTC re-slice.

**NEVER catch the error and send anyway, and never invent a date so the sentence reads better.** The fix is one of two things: drop the day from the draft ("I'll come back to you with a date" is honest and costs nothing), or set a real date on the row first and then say it. A draft with no date at all is always allowed.

# Phase 9 — Failure handling

A failure is the light's line and nothing else: the light is `error` with one plain sentence of what refused (a mail rate limit, no voice calibration, no mail connector), and the feed is left as it was. This run posts no widget, no card and no note in its chat: the page is the only surface (cr1#678).
