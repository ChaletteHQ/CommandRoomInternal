# Email Draft Protocol (v2.10.0+)

Shared specification for any orchestrator that generates email drafts. Overrides any earlier phase instructions in conflict.

Applies to **every skill that emits a recipient-bound email draft, regardless of whether the trigger is scheduled or on-demand** (v3.13.0+ — expanded scope after the 2026-05-20 widget-cascade audit found on-demand skills not following this protocol). Specifically:

**Scheduled tasks (canonical since v2.10.0):**
- `cr-inbox` / legacy `cr-inbox-pulse`
- `cr-commitments` / legacy `cr-commitment-nudge` + `cr-commitment-chase`
- `cr-cracks-watch` (re-engagement drafts)
- `cr-past-meetings` / legacy `cr-meetings-processed` (follow-up drafts)
- `cr-upcoming-meetings` / legacy `cr-meetings-today` (reschedule drafts on `push SLUG to [date]`)
- `pulse` (re-engagement nudges)

**On-demand skills (v3.13.0+ — newly required to follow this protocol):**
- `email-writer` (direct-draft surface)
- `intro-broker` (double-opt-in + direct-forward drafts)
- `follow-up-ritual` (per-attendee post-meeting follow-up drafts)
- `thread-resurrection` (revival draft via chained email-writer)
- `inbox-triage` (Reply Now bucket drafts)
- Any future skill that emits a recipient-bound draft

This includes email drafts that arise as **sub-steps of another skill's work or of a longer multi-step turn**: chain email-writer (the thread-resurrection precedent; CONTRACT Rule 30), don't compose inline.

Every skill in this list MUST render its draft surface as a chat-action widget via `widget_transport.render_and_persist(data_view, wrapper="fragment")` — the full validator chain runs inside — passing `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code` (`shared/CHAT_ACTION_WIDGET.md` § Transport, Bug #67). Plain-text previews in chat are NOT a valid alternative — they break editability and force back-and-forth chat-turn revisions. See §1 (lazy creation), §2 (numbered actions), §3c (Zapier-threaded send) for the per-action semantics every emitter follows.

## 0. Plain-English chat output (v2.10.0+)

Every chat string the user sees must be plain executive English. Never leave `<N>` / `<slug>` / `<recipient>` placeholders literal — substitute the real value before posting. Use a `[firstname-lower]` slug prefix per item; do NOT repeat per-item action menus (the matrix-of-actions form is the anti-pattern). List actions ONCE at the end with `N` as the substitution token explicitly explained.

This is reinforcement for what each orchestrator's Phase 6/7/8/9 chat-format block specifies — when in doubt, follow the rule here.

## 0.5 Connector-agnostic dispatch (connector-agnostic-v1 — READ FIRST; supersedes the Gmail-specific framing below)

This protocol was written in Gmail terms. As of the connector-agnostic build it is **intent-based**: a skill expresses an intent (draft a reply, send this, create a draft) and a resolver maps it to whatever mail backend the workspace declared. Everything below (§1–§8) still holds — but wherever it names a Gmail tool, read it as "the resolved mail tool on the declared backend." The specifics:

1. **Resolve the backend server-id-first.** Get the declared mail backend from `connector_config.declared_backend("email")` and resolve the operation on that server via `tool_discovery.discover_for_category("email", "<op>", tools, declared=…)`. When no backend is declared (empty map), fall back to `discover_mail_draft_tool` / `discover_mail_send_tool` / `discover_mail_reply_tool` / `discover_mail_thread_fetch_tool` — that fallback IS today's behavior (R4). NEVER name a provider tool (`create_draft`, `send_draft`) directly in skill prose (Rule 21); the per-provider mapping lives in `connector_adapters/mail.py`.
2. **Capability-gate every write.** Read the backend's capabilities from `connector_capabilities.json` (via `connector_adapters.capabilities.supports(provider, cap)`). If the backend can't draft/send (M's Microsoft 365 is READ-ONLY — H-F), **degrade to "here's the text to paste," never a hard fail** (A3). Draft/send/undo-send/threaded-reply availability all come from the manifest, not from assuming Gmail.
3. **The Zapier leg is a GMAIL-ONLY dispatch row (H-D).** §3c below fires ONLY when the declared mail backend is **Gmail** — a Superhuman or Outlook workspace never inherits it (Superhuman sends natively via its own send tool; Outlook, when write-capable, sends via its own). The Zapier server is pinned by server-id in `workspace.connectors._zapier_server_ids` (R12) and recognized even in a UUID-namespaced env (no `mcp__zapier_` prefix) — see `discover_zapier_send_tool(tools, zapier_ids=…)`.
4. **Provenance is structured.** A sent/drafted email records provenance `{connector, provider, native_id, account_id}` via `connector_adapters/provenance.py`, not a `gmail:`/`gcal:` string. Legacy rows stay readable (back-compat); the canonical dedup key reduces old+new to one identity.
5. **Account-scope + outbound routing (R1/B3).** Outbound **never originates from an out-of-scope / personal account**; reply from the account the thread lives in; new outbound from the declared default (business-primary). A `write_to_business: off` account may still be *surfaced* (its mail can appear in a triage list) but a draft composed from it is a business action — route it from a business account. When the address↔server binding is **unverified** (some connectors expose no whoami — H-A, native Gmail), a fail-closed send **degrades to paste-text** rather than risk sending from the wrong account. See `shared/ACCOUNT_SCOPE.md` §5.

## 1. Lazy draft creation (the big rule)

**Do NOT create drafts at fire time.** Generate draft TEXT only. Show full draft inline in the chat turn. Drafts get persisted to the mail connector only when the user EXPLICITLY chooses one of these per-item actions (the tool named is the resolved draft/send tool on the declared backend per §0.5, NOT a hardcoded Gmail tool):

- `N send` → compose + send via the resolved send path (§3c dispatch order).
- `N draft` → opens the inline edit field on the widget; on Apply, calls the resolved draft tool to land the draft in the connector's Drafts. (User reviews/edits via the widget, then the draft persists for further refinement in the connector's UI if desired.)
- `N snooze 3d` → NO connector call; mutes the card for 3 days (FB-17 — the email card's third primary button).

**v2.14.4+ canonical-verb consolidation:** the prior `to drafts` verb was merged into `draft` — same effect (lands in Gmail Drafts), with the edit-then-save flow as the default semantic. The renderer's CANONICAL_ACTIONS set rejects `to drafts` as a per-item verb; only the bulk action `to drafts all` retains the older shape. References below to "to drafts" describe the OLD shape — the current emit MUST use `draft`.

**FB-17 (2026-07-19): `edit then send` RETIRED as an emitted verb** — the FB-10 inline-editable body replaced the popup editor, and the email card is Send / Draft / Snooze (3 days). The wire id survives only as a DEPRECATED_ALIAS (→ `send`) for in-flight widgets; the renderer's CANONICAL_ACTIONS set now REJECTS it at render time, so no new data view may include it. References below to "edit then send" describe dispatch of the in-flight alias, never something to emit.

For `N skip` (surfaces that still offer it — not the email card) — NO Gmail call; just records dismissal.

**Why lazy creation:** v2.7.x → v2.9.2 created drafts eagerly at fire time, which cluttered Gmail Drafts with up to 5+ unwanted drafts every day. v2.9.3+ only persists what the user actively chooses to save via `N send` or `N draft`.

## 2. Numbered action format + collapsed edit modifiers

Action items can be replied as `1. send` or `1 send`. Both formats parse — period is optional convention, not enforcement.

**Collapsed edit format (v2.10.0+):** instead of listing each edit variant separately (`edit firmer`, `edit softer`, `edit shorter`, etc.), the chat turn shows ONE `edit` action in the global actions list at the end:

```
N edit [change]   — rewrite (try: firmer, softer, shorter, factual, specific instruction)
```

Parser accepts all of:
- `N edit firmer` (verb + modifier)
- `N firmer` (modifier alone — shortcut)
- `N edit drop the apology line` (free-form)
- `N shorter and add deadline by Friday` (combined free-form)

Modifier shortcuts the parser knows: `firmer`, `softer`, `shorter`, `longer`, `more apologetic`, `less apologetic`, `factual`, `warmer`, `cooler`, `formal`, `casual`. Anything else is treated as free-form rewrite instruction.

Email-writer is invoked with the modifier as a voice-tilt directive when generating the rewrite.

## 3. Mail-connector defensive handling (resolved backend; verified limitations as of 2026-04-28)

> **Connector-agnostic note (see §0.5):** §3a/§3b below document real Gmail-backend limitations. They apply when the declared mail backend is Gmail. For other backends, the equivalent limitations come from the capability manifest (`connector_capabilities.json`): e.g. Superhuman supports native threaded send + labels + undo-send, so §3a/§3b don't apply; a read-only Outlook connector can't draft or send at all, so both degrade to paste-text (§0.5 point 2). Never assume a Gmail limitation on a non-Gmail backend, and never assume a Gmail capability either — read the manifest.

### 3a. `create_draft` may reject `threadId` parameter

Some workspaces' Gmail MCP doesn't accept `threadId` in `create_draft`. Verified empirically in M's workspace.

**Behavior:** try `create_draft(threadId: <id>, ...)` first. On schema-rejection error, retry as `create_draft(...)` without threadId.

**Surface in chat (once per session, plain English):**
> *"(Heads up: drafts can't be threaded automatically in this workspace — Gmail's connector doesn't support it here. Sending still delivers correctly. For an inline reply, open the draft in Gmail and paste it into a reply on the original thread.)"*

### 3b. `create_label` may fail with insufficient-scope error

Some workspaces' Gmail connector lacks broader scope. Verified empirically in M's workspace.

**Behavior:** try `create_label('cr-staged-<date>')`. On failure, continue without label. Draft still lands in Gmail Drafts unlabeled.

**Surface in chat (once per session, plain English):**
> *"(Heads up: Gmail labels can't be applied — the connector needs broader access. Drafts still land in your Drafts folder, just untagged. Reconnect Gmail with full mail access to turn the cr-staged tags back on.)"*

These limitations DON'T block the orchestrator. They just degrade the UX. Honest surfacing beats silent broken behavior.

## 3c. Zapier-threaded send (v2.10.7+ — preferred path when configured)

For client deployments where threaded replies matter, the workspace can wire a Zapier Zap that sends Gmail with proper `In-Reply-To` + `References` headers. This produces correctly-threaded sends even in workspaces where native Gmail MCP rejects `threadId` (per §3a). The path is OPT-IN and additive; if the Zap isn't wired, behavior falls back to §3a / §3b unchanged.

### Convention

- **Zap name (exact, em-dash):** `Command Room — Send Threaded Email`
- **Slug-equivalent:** `command_room_send_threaded_email`
- The Zap MUST be named exactly this (em-dash, not hyphen). The Zapier setup guide walks the user through wiring it in ~5 minutes.

### Required Zapier action permissions (v2.12.6+)

When the user connects Gmail or Outlook to Zapier, they need these specific actions enabled on the connection:

**Gmail connection:**
- `Send Email` — covers new outbound messages
- `Send Email Reply` (also displayed as `Reply to Email` in newer Zapier UIs) — covers threaded replies, REQUIRED for proper In-Reply-To / References header handling so threads don't split
- (Optional, NOT required) `Create Draft` — Command Room does drafts via native Gmail MCP, not Zapier

**Microsoft 365 / Outlook connection:**
- `Send Email` (Outlook integration) — equivalent to Gmail's Send Email
- `Reply to Email` (Outlook integration) — equivalent to Gmail's threaded reply

Without `Reply to Email` / `Send Email Reply`, the Zap can technically still fire but the recipient sees a NEW thread instead of a continuation of yours — exactly the bug §3c was created to solve. The setup guide walks through enabling this action.

If a user pings about Zapier failing to thread despite the Zap firing successfully: most common root cause is the `Reply to Email` action not being enabled on the underlying Gmail/Outlook Zapier connection. Direct them to Zapier → My Connections → click their Gmail/Outlook → check the "Allowed Actions" list.
- The convention name is NOT configurable per workspace (v2.10.7 — YAGNI; if a client genuinely needs a different name, easy to add later).

### Detection (priority order — first hit wins)

When dispatching `N send`, scan available MCP tools for the Zapier integration. Try THREE detection paths in order (v2.12.6+ — broadened per M's testing where the v2.12.5 narrow match missed his configured Zap):

1. **Tool-name slug match** — any `mcp__zapier_*` tool whose name contains `command_room_send_threaded_email` (case-insensitive, allowing slug variations like `command-room-send-threaded-email`, `command_room__send_threaded_email`, `command_room_send_threaded_email_via_gmail`, etc.). Cowork's Zapier MCP typically exposes Zaps by title slug.
2. **Tool-description fuzzy match** — if no slug match, scan the description of every `mcp__zapier_*` tool for `Command Room` AND (`Send Threaded Email` OR `threaded`). Handles Zapier MCP versions that expose Zaps by UUID and put the Zap title in the description instead.
3. **Permissive Gmail-send fallback (v2.12.6+)** — if neither name-slug nor description-fuzzy matches, scan ALL `mcp__zapier_*` tools for any whose name OR description contains BOTH `gmail` (or `email`) AND (`send` OR `reply`). If exactly ONE matches, use it as the Zapier-send candidate. If multiple match, use the first one whose name contains `command` or `room` (any casing); otherwise pick the first one whose name contains `send` (heuristic). If still ambiguous, surface a one-time per-session note listing the candidates so the user can rename the right Zap to the canonical name. This catches the "user wired up the Zap but used a slightly different name" case without false-positive matching to Calendar / Drive / etc. (Zapier non-email tools have neither `gmail` nor `send-email` in their names.)

**Diagnostic surfacing (v2.12.6+):** when discovery completes (matched or not), log the result internally so the apply-choices `Sent emails` section can include diagnostic context if `send` fails. If discovery FAILED (no Zapier tool matched any of the 3 paths above) AND the user's `N send` falls back to native Gmail with a threading error, surface a one-time per-session note in the consolidated ack:

> *"Zapier send tool not detected — falling back to native Gmail. Confirm your Zap exists in Cowork → Settings → Connectors → Zapier, named exactly `Command Room — Send Threaded Email` (em-dash). If it's there with a different name, rename it; otherwise see the Zapier setup guide."*

Do NOT surface internal tool IDs or schemas — just the plain-English direction. The setup guide is the recovery path.

If multiple tools match, prefer exact slug match over fuzzy match. If still ambiguous, prefer the most recently registered.

### Dispatch (priority order on `N send`)

> **Backend gate (connector-agnostic-v1, H-D):** this Zapier-first dispatch order applies **only when the declared mail backend is Gmail**. On a Superhuman backend, `N send` uses Superhuman's native send (`send_draft` on the declared Superhuman server, resolved via `discover_for_category`) — threaded natively, no Zapier leg, `via: "native_threaded"`. On a write-capable Outlook backend, `N send` uses Outlook's native send/reply. On a READ-ONLY backend (M's M365 — H-F), `N send` is unavailable and degrades to paste-text. The steps below are the **Gmail-backend** dispatch.

1. **Zapier path** (preferred if configured, Gmail backend only): see "Zapier param contract" below. On success: confirm `✓ Sent (threaded) at HH:MM`. Write `outreach_sent` event with `via: "zapier"`. Done.
2. **Native Gmail MCP threaded** (fallback): try the resolved threaded-reply path then send. If the schema rejects `threadId` (per §3a), fall through. On success: confirm `✓ Sent at HH:MM`. Write `outreach_sent` event with `via: "gmail_mcp_threaded"`.
3. **Native Gmail MCP standalone** (last fallback): resolved draft tool without `threadId`, then send. Surface inline note ONCE per session in plain English:
   > *"(Sent as standalone — your Gmail connector doesn't support threaded send. Setting up the Zapier integration fixes this; the setup guide walks through it in about 5 minutes.)"*

   Confirm `✓ Sent at HH:MM`. Write `outreach_sent` event with `via: "gmail_mcp_standalone"`.

### Zapier param contract (v3.2.2+ — corrected format per Sam 2026-05-12)

**The Zapier `gmail.reply_to_message` action exposes a parameter LABELED `thread_id` — but it actually wants the RFC 822 `Message-ID` header VALUE from the latest message in the thread.** That's a string like `<CAGNbKj-...@mail.gmail.com>` (including the angle brackets), NOT a Gmail hex ID. Zapier uses this value to construct the `In-Reply-To` and `References` MIME headers on the outgoing message so the recipient's email client sees the reply as a continuation of the original thread.

**What Zapier rejects (verified empirically 2026-05-12):**
- Gmail thread-level hex ID (e.g. `19de01d8fd988ea6`) — returns "Requested entity was not found", or accepts but silently drops headers.
- Gmail message-resource hex ID (e.g. `19e036260f1c5ddd`) — returns "Requested entity was not found".

**What Zapier accepts:**
- The full RFC 822 Message-ID header value (e.g. `<CAGNbKj-...@mail.gmail.com>`), found in the latest message's `payload.headers` array under header name `Message-ID` (case-insensitive per RFC).

**Why the v2.14.38 fix wasn't enough:** that ship correctly identified the param wants "the latest message ID," but the helper returned the message's *resource ID* (Gmail's hex) — wrong format. For single-message threads, the format coincidentally worked sometimes. For multi-message threads, Zapier rejected. Sam hit it on 2026-05-07 (Josh) and again on 2026-05-12 (4 of 4 replies forked on recipient side).

**Failure mode this fix closes:** the helper at `shared/scripts/zapier_send.py` now extracts the `Message-ID` header value from the standard Gmail API response shape (`messages[-1].payload.headers[name="Message-ID"].value`), with fallbacks for flattened-headers and dict-headers MCP variants. `build_zapier_send_payload` also validates format — rejects bare hex strings to prevent silent regression.

**Required dispatch flow on `N send` (Zapier path):**

1. **Fetch the thread.** Call native Gmail MCP `get_thread` with the source `thread_id`. Get back the response with `messages: [...]` in chronological order.

2. **Extract the latest message ID** via the canonical helper — never agent-improvise this step:

    ```bash
    SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
    python3 -c "
    import sys, json
    sys.path.insert(0, 'shared/scripts')
    from zapier_send import extract_latest_message_id, build_zapier_send_payload
    thread_response = json.loads('''<get_thread response JSON>''')
    payload = build_zapier_send_payload(
        latest_message_id=extract_latest_message_id(thread_response),
        to='<recipient email>',
        subject='<subject>',
        body='<body>',
        cc='<cc emails or empty>',
    )
    print(json.dumps(payload))
    "
    ```

3. **Invoke the matched Zapier tool** with the helper-built payload. The payload's `thread_id` field carries the LATEST message ID — that's the value Zapier needs despite the misleading param name.

**Why a helper instead of just a doc note:** the recurring v2.14.x agent-improvises-around-canonical-paths failure class. Doc-only fixes have shipped before and agents have improvised their way around them at fire time. The helper at `shared/scripts/zapier_send.py` centralizes the extraction so the dispatch can't get it wrong.

**Failure handling:**
- `extract_latest_message_id` raises `ZapierPayloadError` if the thread fetch failed, returned an empty messages array, or returned an unrecognized shape. Fall through to step 2 of the dispatch order (native Gmail threaded).
- `build_zapier_send_payload` raises if any required field is empty. Same fall-through.
- These are loud failures, not silent — the bug class only ships when the helper is bypassed.

### Failure handling

- Zapier tool detected but invocation fails (network, auth, Zap turned off): fall through to step 2 silently. Surface a per-session note ONCE: *"(Zapier send failed — falling back to direct Gmail. Check that the Zap is published and connected.)"*
- Zapier tool detected but the Zap returns a non-2xx: same fall-through.
- All three paths fail: surface inline retry per the orchestrator's existing send-fail handling (Phase 9 in Inbox; equivalent in Commitments). Don't write `outreach_sent`.

### What `N draft` does NOT do

The Zapier path is a SEND path, not a draft path. `N draft` continues to use native Gmail MCP `create_draft` per §3a. Drafts in Gmail let the user review/edit before sending; Zapier-as-draft adds no value.

### What this means for orchestrators

Each orchestrator's `N send` reply-handling path follows the dispatch priority above. The reference order is:

> *"`N send` follows the priority order in `EMAIL_DRAFT_PROTOCOL.md` §3c: Zapier first if configured, native Gmail threaded as fallback, standalone as last resort."*

Don't duplicate the dispatch logic in each orchestrator. Reference §3c.

### §3c HARD SCOPE (v2.12.5+ — calendar NEVER through Zapier)

**Zapier scope is locked to email send/draft ONLY.** It NEVER touches calendar, drive, contacts, or any other Google Workspace surface — even if Cowork's Zapier MCP exposes those tools.

Specifically: the Zapier-tool discovery in §3c is gated to tools whose name contains `send_threaded_email` (or fuzzy description match for "Send Threaded Email"). The agent MUST NOT use any other `mcp__zapier_*` tool. Calendar, Drive, Sheets, Docs, etc. — even when Zapier exposes them — are excluded by scope.

**For ALL calendar operations** (creating events, finding events, accepting/declining invites, proposing times, updating events, listing events, busy/free queries), use the native Google Calendar MCP exclusively. The native tool prefix is typically `mcp__*google_calendar_*` and lives outside Zapier's tool namespace.

**Tool-discovery rule (REINFORCED in every orchestrator's Phase 2 setup, v2.12.5+):**

When discovering MCP tool IDs for non-email-send operations:
- ✅ Look for `mcp__*google_calendar_*` (native Calendar MCP)
- ✅ Look for `mcp__*google_drive_*` (native Drive MCP)
- ✅ Look for `mcp__*gmail_*` (native Gmail MCP — for fetch/draft, NOT send when Zapier is configured)
- ❌ NEVER look for `mcp__zapier_*google_calendar*`
- ❌ NEVER look for `mcp__zapier_*google_drive*`
- ❌ NEVER look for any `mcp__zapier_*` tool whose name doesn't contain `send_threaded_email`

If the agent ever surfaces a permission prompt asking "Claude wants to use Google Calendar from Zapier" (or similar), THAT'S A BUG — the orchestrator's tool discovery picked up a Zapier-namespaced calendar tool when it should have picked the native one. The fix is in the discovery code, not in the user's permission grant. M's standing rule per Apr 30: *"calendar never goes through zapier - it goes through native connector."*

Why this rule exists: Zapier's Google Calendar tool has a different OAuth scope, different permission model, different rate limits, and different failure modes than the native Calendar MCP. Mixing namespaces creates inconsistent behavior across users (one workspace might have the Zap, another not). Native Calendar MCP is universal — every Cowork install with Calendar OAuth has it.

### Calendar action handlers (each orchestrator's reply-handling)

These actions across orchestrators ALL use native Calendar MCP, never Zapier:

| Orchestrator | Action | Calendar operation |
|---|---|---|
| Inbox | `accept` (calendar invite) | native `respond_to_event` / equivalent |
| Inbox | `propose [time]` (calendar invite) | native: cancel original or send proposed-time email + tentatively create new event |
| Inbox | `decline [reason]` (calendar invite) | native `respond_to_event` with response: declined |
| Pulse | `schedule catchup [when]` | native `create_event` (tentative) + email-writer for the request email |
| Commitments OWED TO YOU | `follow-up call` | native `create_event` (tentative) + email-writer for the invite email |
| Upcoming Meetings | `push meeting [when]` | native `update_event` to move the existing event + email-writer for the reschedule notice |

Email-writer drafts that come out of these handlers DO go through Zapier on send (via §3c) — that's still email. The CALENDAR side of the handler stays native.

## 4. `N skip` semantics under lazy creation

Since drafts aren't created at fire time, `N skip` has nothing to delete in Gmail. Just:
- Write a `chat_dismissal` event with `target_id` (24h TTL, cross-chat aware)
- Optionally log the dismissed draft text to `staging_emissions.jsonl` for audit
- No Gmail interaction

## 5. Bulk actions

- `send all` → iterate the non-noise items, compose+send each (with one-line confirmations per send: `✓ Sent to <name> at <HH:MM>`)
- `to drafts all` → iterate, create Gmail draft per item. (Note: `to drafts all` is the canonical BULK verb; the per-item verb is `draft`, not `to drafts`.)
- `skip all` → bulk dismissal events

## 6. Reference order in each orchestrator

When this protocol applies, the orchestrator's reply-handling section should reference this doc:

> *"Email-draft actions (`send` / `draft` / `snooze 3d`) follow the lazy-creation + numbered-format + Gmail-defensive rules in `shared/EMAIL_DRAFT_PROTOCOL.md`."*

Earlier phase instructions about creating drafts at fire time are SUPERSEDED by this doc.

## 6.5 Widget degradation ladder (v3.13.8+ — canonical, Bug #46)

When deciding which surface to render, follow this ladder. Skills must NOT route ad-hoc; the v3.13.8 ladder is the contract.

| Case | Canonical surface |
|---|---|
| n>1 + actionable items | Path A — canonical batched widget (Apply-all selection model). Render via `widget_transport.render_and_persist(wrapper="fragment", ...)`. |
| n=1 + actionable + compose-new (no Gmail draft exists yet) | Path B1 — editor card. Multi-field form with editable To/CC/Subject/Body. |
| n=1 + actionable + confirm-drafted (draft already produced by upstream skill) | Path B2 — confirmation card. Avatar + read-only summary + the email card's primary buttons (`send` / `draft` / `snooze 3d` — FB-17; an email-shaped item MUST carry all three per `EMAIL_REQUIRED_ACTIONS`), board-pack pattern. Calendar-shaped confirmations carry `send` / `skip` instead. |
| n=1 + degraded (recipient identified but no actionable email) | Recovery widget per §2.11 — use `add email then send` canonical verb. Single-field email input, transitions to `send` on submit. |
| Single-question single-decision (no draft involved) | Path D — AskUserQuestion (platform-native), NOT a widget. |
| n>0 + closure-reversal detected (the recipient just closed the thread you're about to revive) | Confirmation widget per Strength #20 — surface the reversal and ask before proceeding. |
| Read-only orientation (list-active style, no actions) | Chat-mode synthesis, NOT a widget. |

All paths render via `widget_transport.render_and_persist` (canonical render + validate + persist → relay `transport["html"]` as `show_widget` `widget_code`). Freelance render paths (direct show_widget with hand-built HTML) violate the contract.

Action labels in the rendered HTML MAY interpolate names and friendly text (e.g. "Send invite to Sam") but the wire-level action verb MUST be canonical lowercase (`send invite`). Per CONTRACT.md Rule 5.

## 7. Migration from v2.9.2 and earlier

Workspaces installing v2.9.3+ fresh: orchestrators behave correctly out of the box. No drafts auto-created.

Workspaces upgrading from v2.9.2 with existing scheduled tasks: the next fire of each orchestrator picks up the new behavior (orchestrator prompts are read at fire time, not pinned at registration time — verified per Cowork's empirical model). No re-registration needed.

Existing Gmail drafts from v2.9.2 fires (if any): stay in Gmail Drafts. Not auto-cleaned. M can manually delete or leave as historical residue.

## 8. Migration to v2.10.0 plain-English chat output

Workspaces installing v2.10.0 fresh: the new orchestrator prompts already follow the plain-English chat conventions described in §0. No action needed.

Workspaces upgrading from v2.9.x: each orchestrator's next fire reads the v2.10.0 prompt and produces the new chat shape. Old chat threads from prior fires retain their old shape (we don't rewrite history); only NEW fires use the new format.
