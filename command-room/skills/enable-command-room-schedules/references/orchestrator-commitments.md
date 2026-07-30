# Orchestrator prompt — Waiting On (SPEC CTS1 Surface 1)

This file is the EXACT prompt the bootloader cats and executes for `taskId: waiting-on`. Fires 8:30 AM weekdays local (the slot the retired `commitments` task held). **CTS1 (RULED 2026-07-16): the old two-direction Commitments chat is SPLIT** — this chat is now **things people owe the user** (they act next; the user nudges, they don't do) plus the unowned/unconfirmed confirm tail (§2.4 ruling: My Plate stays a pure act-list). The owner-me direction — Promised + Personal — renders on the **My Plate** chat (`orchestrator-my-plate.md`, taskId `my-plate`). The two surfaces are read-side filters over the SAME projected open set (`shared/scripts/surface_split.py` — one lane, views not stores; classifier = EFFECTIVE kind, never raw counterparty presence).

Filename + event continuity: this file keeps its name and events keep `source_skill='commitments'` (the pulse/orchestrator-dont-forget pattern) — history at `source_skill='cr-commitments'` / `'commitments'` stays valid as append-only history and every reader that filters on the commitments family keeps matching. Fire receipts log under task id `waiting-on` (see Phase 8); the last-fire window reads BOTH `waiting-on` and legacy `commitments` receipts so the first post-split fire doesn't re-scan a week of mail.

**OUTPUT CONTRACT (v2.13.0+ — MANDATORY):** every chat post follows `shared/CONTRACT.md`. The renderer enforces canonical action labels (`CanonicalActionError`) and blocks leaks (`LeakDetectedError`) before any post. Rules 1–18 are non-negotiable. The widget + Links section is the ENTIRE chat turn; STOP after that. No commentary, no narration.
**Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md` for the markdown-mode legacy rules; follow `shared/CONTRACT.md` for the v2.13.0 strict contract.
**Email-draft mechanics:** follow `shared/EMAIL_DRAFT_PROTOCOL.md`. Drafts are TEXT in chat until user persists. Zapier scope HARD-LIMITED to email send/reply.

---

## ⛔ STOP CONTRACT — READ BEFORE YOU DO ANYTHING

Read `shared/STOP_CONTRACT.md` from disk and obey it as your first action of every fire. It carries the canonical post-widget output rules (no writing widget HTML to disk, no narrating widget contents, no markdown lists as substitute for widget rendering, no skipping `show_widget` after clean validator pass, etc.). Pre-v3.5.0 each orchestrator inlined a ~25-line copy of this contract; v3.5.0+ they reference the shared file so amendments edit one place.

The applies-to-this-orchestrator framing: re-runs of THIS orchestrator (`regenerate with real events`, `show me with populated data`, `re-fire commitments`) MUST re-execute Phase 1 onward through the SAME pipeline (renderer → `show_widget`); do NOT switch to file-write mode, do NOT save intermediate outputs, do NOT improvise a "save HTML so the user can reopen it later" mode. The widget is live in chat history; nothing else is needed.

---

You are firing the Command Room "Waiting On" chat (CTS1 Surface 1). Surfacing what OTHER PEOPLE owe M — chase drafts, delegated work, the quiet nudged-no-reply tail (Tue/Thu — Phase 3.8), and the unowned/unconfirmed confirm tail. Read-mode is ACCOUNTABILITY: M nudges; M doesn't do. Items M owes render on the My Plate chat, never here (the one exception: nothing — a row where M acts next does not belong in this chat; if the partition puts a row here, someone else acts next).

The CRU pre-render scans below (Phases 2.5/2.6/2.7) still run ALL THREE directions of substrate hygiene — including the OUTBOUND scan that closes items M owed (those rows render on My Plate, which fires 15 minutes after this chat and reads the substrate this fire just reconciled). Splitting the surfaces did NOT split the reconciliation; this fire remains the morning's one bulk scan.

# Phase 1 — Always run (no idempotency gate, v2.10.5+)

The v2.7-v2.10.4 idempotency gate was removed in v2.10.5. This orchestrator ALWAYS runs when fired — whether by cron or by manual `re-run` trigger. Multiple fires per day are intentionally allowed.

A `pack_run` event still writes at the end of every fire (for audit trail), but no gate blocks subsequent fires. Re-running is cheap because drafts are TEXT-only until the user persists them per `EMAIL_DRAFT_PROTOCOL.md`.

# Phase 2 — Setup

- **Record the fire start FIRST, before any read or write:** `fire_start = datetime.datetime.now(datetime.timezone.utc).isoformat()`. Hold it as `fire_start` — Phases 2.5 and 2.6 both pass it. It marks the instant this fire began, so a commitment this same fire captured cannot be treated as independent evidence for closing itself (the circularity fence, layer 2). It must be taken HERE, at the top, not next to the calls that use it: taken later it sits after the capture phases and fences nothing.
- Compute today's date in local time.
- Read entities.json + aliases.json.
- Read voice calibration (cache once for the session).
- **Resolve the mail tools through the seam** — `tool_discovery.discover_for_category("email", "<op>", tools, declared=connector_config.declared_backend("email"))` for the search / draft-create / send / label operations, falling back to `discover_mail_search_tool` / `discover_mail_draft_tool` / `discover_mail_send_tool` when no backend is declared (empty map = today's behavior, R4). Zapier legs are excluded from native discovery automatically (pinned server-ids + signature detection, R12/H-H). Never name a provider tool id directly. On drift (declared backend NOT PRESENT) in a scheduled fire: skip-and-flag per SHARED_CHAT_OUTPUT_PROTOCOL § Connector drift (R13) — never prompt from a silent fire.
- **Resolve the calendar tools through the seam** for the `follow-up call` handler (drafts a calendar-invite request) — `tool_discovery.discover_for_category("calendar", "<op>", tools, declared=connector_config.declared_backend("calendar"))` for the event-create operation, falling back to `discover_calendar_tool(tools, "<op>")` when no backend is declared (empty map = today's behavior, R4). Native calendar via the seam, Zapier-excluded — per `EMAIL_DRAFT_PROTOCOL.md` §3c HARD SCOPE calendar never goes through Zapier (the seam excludes Zapier legs automatically: pinned server-ids + signature detection, R12/H-H). If no native calendar tool resolves, `follow-up call` degrades to email-only (drafts a "let's grab 15 min" message without creating a tentative invite) with a one-time per-session note. Never name a provider tool id directly.
- Discover Zapier-threaded-send tool per `EMAIL_DRAFT_PROTOCOL.md` §3c (limit to tools whose name OR description contains `Send Threaded Email`; never any other Zapier tool — including, explicitly, no Zapier Calendar / Drive / Sheets tools). Cache for the session. If none, fall back to native Gmail at `N send` time — no error.
- M's primary `user_id` from entities.json.

# Phase 2.5 — CRU pre-render scan: auto-resolve commitments from outbound sends since last fire (v2.14.7+)

Per `shared/scripts/cru_match.py` Path 2. Before Phase 3's bucket filter runs, scan recent outbound sends from native mail (Gmail / Outlook / wherever) and auto-resolve open commitments that those sends fulfilled. The premise: many commitments get fulfilled by sends made directly from the user's mail client OUTSIDE Cowork. Without this scan, those commitments would still surface in today's Commitments widget even though the user has already done the work.

Path 2 catches what Path 1 (apply-choices in-Cowork sends) and Path 3 (past-meetings transcripts) can't: standalone sends from native clients that aren't tied to a meeting and didn't go through Cowork.

**Cost:** one bulk mail search per Commitments fire (1-2x/day). Conservative threshold (≥ 0.55) means false positives are rare; medium-confidence matches (0.30 - 0.55) write `commitment_review_proposed` events that the next Pulse fire surfaces for one-click confirm.

**Skip entirely if:**
- No mail send tool was discovered in Phase 2 (degraded — proceed without scan).
- No open commitments where the user is the owner (helper returns `[]`).

**These skips are exhaustive — the run mode never adds one (v4.5.2 R2, applies equally to 2.6 and 2.7).** A scheduled fire is NOT "an autonomous run with no connector fetch," and a manual fire is interactive by definition — BOTH run these scans in full. The dogfood's improvised skip ("CRU pre-render scans skipped this fire — scheduled autonomous run, no connector fetch", FINDINGS F-47 P1a) left "no email on file" on a chase row for a contact who had emailed that very day.

Otherwise, execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import load_open_commitments

events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_commitments(events_path)

# Determine the time window: max(last Waiting On fire ts, today - 7 days).
# First fire ever defaults to last 7 days. Provided as <newer_than_iso> below.
# v4.5.2 R1 + CTS1 — find the prior fire through the shared receipt reader
# (parses every legacy shape: cr-commitments, kind-only, task_id-only —
# forever). CTS1: read BOTH the new task id and the retired one and take the
# newest — the first post-split fire must see the last pre-split fire:
#   from receipts import last_receipt_times
#   times = last_receipt_times(WORKSPACE_ROOT, ["waiting-on", "commitments"])
#   last_fire_dt = max((t for t in times.values() if t), default=None)
# events.jsonl is append-only — never rewritten.
last_fire_ts = '<ISO of the max above, or today-7d>'
print(f'WINDOW={last_fire_ts}')
print(f'OPEN_COUNT={len(opens)}')
"
```

Then use the seam-resolved mail-search tool (from Phase 2) to query outbound mail since the last fire — the `{"from_me": true, "after": "YYYY/MM/DD"}` intent (or `{"from_me": true, "newer_than": "Nd"}`), compiled per provider by `connector_adapters/mail.py`; pass-through providers take the structured intent directly.

For each result, fetch the thread/message body via the discovered thread-fetch tool. Extract:
- the message id (the connector's native id — RECONFENCE layer 1 needs it; a send cannot be evidence about the commitment it created)
- recipient email(s) → resolve to person_id(s) via `aliases.json` / `entities.json` lookup
- subject
- body (last user-authored message in the thread)

Then run `match_send_to_commitments` per send:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_send_to_commitments,
    build_pending_review_event,
)
from commitment_state import close_commitment, CommitmentIdError, PendingReviewError
from connector_adapters.provenance import primary_artifact_key, resolve_mail_provider
from atomic_write import atomic_append_jsonl

workspace_root = '<absolute path to the workspace root>'
events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_commitments(events_path)

# MAILSEAM: resolve the mail provider ONCE. An explicit tag from the Phase-2
# seam wins; otherwise the declared email backend answers. Never a literal —
# `gmail:<id>` built on a Superhuman backend matches no commitment on disk,
# which is a fence that stopped fencing without failing anything.
provider = resolve_mail_provider(workspace_root, '<the seam-resolved provider, or None>')

# Stage B (F2): auto-resolves close through commitment_state.close_commitment
# — THE closure path. Matching (Path 1) is unchanged; only the write moved.
n_resolved = 0
next_seq = <peek-next-seq>  # for pending events only
to_append = []
for send in <list of sends since window>:
    results = match_send_to_commitments(
        open_commitments=opens,
        sender_person_id='<user person_id>',
        recipient_person_ids=send['recipient_person_ids'],
        subject=send['subject'],
        body=send['body'],
        workspace_root=WORKSPACE,   # Phase 6 Loop 4: honor _hq/data/confidence-overrides.json
        # RECONFENCE layer 1 — the ref of the message being scored. A commitment
        # attributed to THIS message is dropped before scoring: the send that
        # captured a promise is not evidence that the promise was kept.
        send_source_ref=primary_artifact_key(provider, send['message_id']),
        # RECONFENCE layer 2 — a commitment THIS fire captured is one source
        # with the send being scored, not two. Phase 2.5 runs in the same fire
        # as the capture phases, which is where layer 2 bites hardest.
        exclude_captured_since=fire_start,   # from Phase 2
        # EVORDER layer 3 — when the message was actually SENT. Layer 2 fences
        # against the start of this fire, which is a different question: a
        # commitment captured before the fire but AFTER the send sails past it,
        # and a send cannot be evidence for a promise that did not exist yet.
        # REQUIRED whenever the send carries a timestamp — omitting it silently
        # disables the guard (F-11 measured four false closes from that gap).
        send_ts=send['ts'],
    )
    for r in results:
        evidence = f\"Sent via native mail client at {send['ts']} — Subject: {send['subject']}\"
        if r['recommendation'] == 'auto_resolve':
            try:
                res = close_commitment(
                    workspace_root, r['commitment_id'],
                    resolved_by='<user person_id>',
                    evidence=evidence,
                    source_skill='commitments',
                )
                if res['status'] == 'closed':
                    n_resolved += 1
            except (CommitmentIdError, PendingReviewError) as e:
                print(f'CRU skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
        elif r['recommendation'] == 'partial_received':
            # MC1: a send to ONE counterparty of a multi-counterparty commitment
            # records that person's receipt — NEVER a whole close. Closure is
            # proposed (Phase 4.5) once all are in.
            from commitment_state import mark_partial_received
            for cp in r.get('matched_counterparty_ids') or []:
                try:
                    mark_partial_received(
                        workspace_root, r['commitment_id'],
                        received_by='<user person_id>', source_skill='commitments',
                        counterparty_id=cp, evidence=evidence,
                    )
                except CommitmentIdError as e:
                    print(f'CRU partial skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
        elif r['recommendation'] == 'pending_review':
            to_append.append(build_pending_review_event(
                commitment_id=r['commitment_id'],
                primary_thread_id=r['primary_thread_id'],
                source_skill='commitments',
                proposed_resolution='auto_resolve',
                score=r['score'],
                evidence=evidence,
                next_seq=next_seq,
            ))
            next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU commitments pre-render: resolved={n_resolved} pending={len(to_append)}')
"
```

**The stdout is for diagnostic logging only.** Per CONTRACT.md Rule 4 forbidden-pattern list: CRU event-type names never appear in chat. The user sees the resolution effect via Phase 3's filter — auto-resolved commitments simply don't appear in today's widget.

**Failure handling:** if the CRU pre-render scan errors (mail search failure, helper import fails), swallow silently and continue to Phase 3. The pre-render scan is best-effort enrichment; the Commitments widget still surfaces the open commitments either way (just possibly including ones already resolved-but-undetected). **Append a `pack_run.data.errors[]` entry** (v3.5.0+) so the failure is auditable via `usage report` even though the user doesn't see it: `{"phase": "2.5_cru_pre_render", "reason": "<short>", "detail": "<truncated stderr or exception message>", "ts": "<UTC ISO — never the local wall clock>"}`. Pre-v3.5.0 these failures were truly silent; if the helper import has been raising in production no one would have known.

# Phase 2.6 — CRU pre-render scan: auto-resolve OWED-TO-YOU commitments from inbound mail since last fire (v3.14.5+)

Per `shared/scripts/cru_match.py` Path 4. The inbound mirror of Phase 2.5. Where Phase 2.5 catches commitments the USER fulfilled via outbound sends, Phase 2.6 catches commitments a COUNTER-PARTY fulfilled by emailing the user their deliverable ("here's the deck", "attached as promised"). Without this scan, an OWED-TO-YOU commitment stays on the widget even though the counter-party already delivered between inbox fires.

This is the daily backstop to the real-time leg in `orchestrator-inbox.md` Phase 5.5. The inbox fire resolves on its own schedule (7 AM weekdays); Phase 2.6 here re-scans inbound mail since the last Commitments fire so deliveries that arrived off-cycle (weekend, after the morning inbox fire, or while inbox was degraded) still close.

**How a reply closes something (REPLYCLOSE), same three bases as Phase 5.5:** (1) their message quotes the item strongly enough AND says it's done — unchanged; (2) **they replied on the thread the item came from**, with either "it's done" wording or the document the item asked for — closes outright; (3) **the same thing off-thread** becomes a confirm, never a close. A reply that fits two open items closes neither. Only THEIR reply counts — a message from the CEO is refused outright, because on a thread the CEO answered last "the latest message" is the CEO's, and closing the CEO's own promises on the CEO's own words is the sent path's job.

**Skip entirely if:**
- No mail search tool was discovered in Phase 2 (degraded — proceed without scan).
- No open commitments where a counter-party is the owner (OWED-TO-YOU set empty).

Otherwise, use the seam-resolved mail-search tool to query INBOUND mail since the last fire — the `{"in_inbox": true, "after": "YYYY/MM/DD"}` intent (or with `newer_than`), compiled per provider by `connector_adapters/mail.py`.

For each result, fetch the message body, resolve the SENDER email → `person_id` (via `aliases.json` / `entities.json`), and carry the message's **`thread_id`** (the connector's conversation id) and **`has_attachment`** (the connector's attachment flag — never inferred from a body that says "attached"). Those two fields are what let a reply be recognized as the delivery rather than as words about it. **Do NOT pre-filter the CEO's own messages or unresolvable senders out of the list** — the helper refuses the first and counts the second, and both counts are how a quiet fire gets explained instead of reading as a clean zero. Then make ONE call over the whole batch:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
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

# One dict per inbound message in the window. Keep the CEO's own messages and
# the unresolvable senders IN — they are counted, not silently dropped.
inbound_messages = <[{'message_id', 'ts', 'sender_person_id', 'subject', 'body',
                      'thread_id', 'has_attachment'}, ...]>

receipt = reconcile_inbound_and_receipt(
    workspace_root, inbound_messages,
    user_person_id=user_id,
    source_skill='commitments',
    fired_via='scheduled',              # 'manual' on a chat-phrase / Run Now fire
    exclude_captured_since=fire_start,  # from Phase 2 — the fence, layer 2
    provider='<the seam-resolved provider>',
)
print('CRU commitments inbound pre-render: closed=%s pending=%s updated=%s batch=%s'
      % (receipt['n_auto_closed'], receipt['n_pending'],
         receipt['n_updated'], receipt['batch_id']))
"
```

**The circularity fence (REPLYCLOSE §3, plus EVORDER layer 3).** Layer 1 needs no argument — the helper derives each message's own ref internally and drops any commitment attributed to that very message, which is what stops the inbound message that CREATED a waiting-on item from being the message that closes it on a later scan (inbox-triage stamps `data.source_ref: gmail:<message_id>` on exactly those captures). Layer 2 is `exclude_captured_since=fire_start` — commitments this same fire captured are one source with the evidence, not two. Anything captured before the fire start stays fully matchable.

**Layer 3 needs no argument either, but it needs each message's `ts`** — it refuses to close a commitment captured AFTER the reply arrived (the F-11 class: layer 2 fences against the start of THIS FIRE, so an item captured before the fire but after the message sails straight through it). Each `inbound_messages` entry must therefore carry the connector's raw ISO-8601 `ts`, never a reformatted display date. Absent `ts` leaves layer 3 inert, which is safe. A present-but-unparseable `ts` fails SAFE and LOUD: the pass closes nothing at all and prints `RECONFENCE: inbound_ts=…` on stderr. `receipt['signal_fields']['n_stale_evidence_skipped']` counts what layer 3 refused — non-zero is the fence working, not an error.

**Self-validate (mandatory).** `v = validate_inbound_reconcile_ran(workspace_root, since_ts=fire_start)` — `v["ok"]` must be True, or this pass did not actually run and its zero means nothing. Also read `receipt["signal_fields"]`: messages scored with neither the conversation nor the attachment field present means the reply checks could not run at all; `receipt["summary"]` says so in plain language in exactly that state, and `receipt["coverage"]` reports how many open items have no resolvable owner and therefore can never be closed by any reply.

**An unresolved user ABORTS this pass** (`PrimaryUserUnresolvedError`) — no audit event, nothing closed. Do not catch it and continue: direction is derived from owner vs the user, so with no user every reply basis is inert and a clean zero would be a lie.

**The stdout is for diagnostic logging only.** Same Rule 4/9 silence as Phase 2.5 — CRU event-type names never appear in chat. The user sees the effect via Phase 3's filter: resolved OWED-TO-YOU commitments drop off today's widget.

**Resolution-miss tag (Phase 6 Loop 5, silent).** This leg already reads the inbound reply body, so it's the privacy-correct place to notice a resolution the CRU pass MISSED: when a reply carries "already done / sent last week" language but produced NO close, mark it. Run it AFTER the Step-above call, off the receipt it returned:

```python
from extraction_hints import is_resolution_miss
closed_mids = {r["message_id"] for r in receipt["resolved"] if r.get("message_id")}
for msg in inbound_messages:
    if is_resolution_miss(msg.get("body") or "") and msg["message_id"] not in closed_mids:
        ...  # append data.resolution_miss = True to that thread's outcome/interaction
```

Additive telemetry — never surfaced. insight-generator's Loop 5 pass clusters these into resolution-language hints that cru_match's completion detection reads back. reconcile-sent's outcome watch stays metadata-only; this is where reply text is already in hand. (`receipt["resolved"]` carries `message_id` on every row precisely so this check needs no second matcher run — a per-message answer from the same pass that produced the closures, not a re-derivation that could disagree with it.)

**De-dup with Phase 5.5:** both legs check `load_open_commitments`, which already excludes anything closed by a prior `commitment_resolved` / `thread_resolved`. If the morning inbox fire (Phase 5.5) already resolved a commitment, it won't be in `opens` here, so Phase 2.6 won't double-write. Idempotent by construction.

**Failure handling:** identical to Phase 2.5 — swallow silently, continue to Phase 3, append a `pack_run.data.errors[]` entry `{"phase": "2.6_inbound_cru_pre_render", "reason": "<short>", "detail": "<truncated stderr>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 2.7 — CRU pre-render scan: auto-resolve SCHEDULING commitments from calendar events since last fire (v3.14.7+)

Per `shared/scripts/cru_match.py` Path 5. Phases 2.5/2.6 are message-direction scans — they only ever look at mail. A whole class of commitments is fulfilled NOT by a message but by an event appearing on the calendar: "set up the build call with Bo", "lock Monday with Rio", "find time with the integrator". The moment the user creates the invite the commitment is done, but with no calendar scan it stays surfaced as "reply to X to lock time" for days (the live scheduling-close bug, 2026-05-29: the user created a Monday invite at 8:29 AM, and the ~11 AM brief still said "reply to Bo to lock Monday" because the counter-party's "Monday is fine" email was still the thread's latest message).

This is the daily backstop to the real-time leg in `calendar-writer` (which resolves when CR itself creates the event). Phase 2.7 catches invites the user made **directly in Google/Outlook calendar, outside Cowork** — exactly the case `calendar-writer` can't see. It also captures the counter-party's acceptance (via `connector_adapters.calendar.is_accepted(attendee, provider)` — the RSVP field name is per-provider and lives in the adapter) as confirmation — the same signal the inbox classifier discards as calendar-noise.

**Precision is structural, not threshold-based** (per Path 5 docstring): a commitment auto-resolves only when it is owed BY the user, its title carries scheduling intent (`detect_scheduling_intent`), and a calendar event exists whose attendees include the commitment's counter-party and doesn't predate the commitment. A deliverable commitment ("send Bo the one-pager") never resolves just because a meeting got booked. Topic-match-without-scheduling-intent → `commitment_review_proposed`, not silent resolve.

**Skip entirely if:**
- No native calendar tool resolved through the seam in Phase 2 (the event-list reader via `discover_for_category("calendar", …)` / `discover_calendar_tool` — Zapier calendar tools excluded per `EMAIL_DRAFT_PROTOCOL.md` §3c). Degraded → proceed without scan.
- No open commitments owned by the user (OWED-BY-YOU set empty).

Otherwise, use the discovered Calendar tool to list events **created or updated since the last fire** (the created/updated window compiled via `connector_adapters.calendar.compile_window(start, end, provider)`; include events whose start is in the recent past or near future — a just-booked future meeting is the common case). For each event, resolve every attendee email → `person_id` (via `aliases.json` / `entities.json`; drop unresolvable attendees), capture the event `summary`, the `created`/`updated` ts, the `calendar_event_id`, and the set of attendee person_ids accepted per `connector_adapters.calendar.is_accepted(attendee, provider)`. Then run `match_calendar_to_commitments` once over the whole event set:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_calendar_to_commitments,
    build_pending_review_event,
)
from commitment_state import close_commitment, CommitmentIdError, PendingReviewError
from atomic_write import atomic_append_jsonl

workspace_root = '<absolute path to the workspace root>'
events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_commitments(events_path)

# Each calendar event resolved to person_ids by the caller:
#   {'attendee_person_ids': [...], 'summary': str, 'created_ts': ISO,
#    'accepted_by': [...], 'calendar_event_id': str}
calendar_events = <list of resolved calendar events created/updated since window>

results = match_calendar_to_commitments(
    open_commitments=opens,
    user_person_id='<primary user person_id>',
    calendar_events=calendar_events,
)

# Stage B (F2): auto-resolves close through close_commitment; matching (Path 5)
# unchanged. Pending events keep their builder.
n_resolved = 0
next_seq = <peek-next-seq>  # for pending events only
to_append = []
for r in results:
    if r['recommendation'] == 'auto_resolve':
        try:
            res = close_commitment(
                workspace_root, r['commitment_id'],
                resolved_by=r['owner_id'],  # the user — they scheduled it
                evidence=r['evidence'],
                source_skill='commitments',
            )
            if res['status'] == 'closed':
                n_resolved += 1
        except (CommitmentIdError, PendingReviewError) as e:
            print(f'CRU skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
    elif r['recommendation'] == 'partial_received':
        # MC1: a calendar event with ONE counterparty of a multi-counterparty
        # scheduling commitment fulfills only that leg — record its receipt.
        from commitment_state import mark_partial_received
        for cp in r.get('matched_counterparty_ids') or []:
            try:
                mark_partial_received(
                    workspace_root, r['commitment_id'],
                    received_by=r['owner_id'], source_skill='commitments',
                    counterparty_id=cp, evidence=r['evidence'],
                )
            except CommitmentIdError as e:
                print(f'CRU partial skip {r[\"commitment_id\"]}: {type(e).__name__}', file=sys.stderr)
    elif r['recommendation'] == 'pending_review':
        to_append.append(build_pending_review_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='commitments',
            proposed_resolution='auto_resolve',
            score=r['score'],
            evidence=r['evidence'],
            next_seq=next_seq,
        ))
        next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU commitments calendar pre-render: resolved={n_resolved} pending={len(to_append)}')
"
```

**The stdout is for diagnostic logging only.** Same Rule 4/9 silence as Phases 2.5/2.6 — CRU event-type names never appear in chat. The user sees the effect via Phase 3's filter: resolved scheduling commitments drop off today's widget.

**De-dup with the calendar-writer real-time leg:** both call `load_open_commitments`, which excludes anything already closed by a prior `commitment_resolved` / `thread_resolved`. If calendar-writer already resolved a commitment when it created the event, it won't be in `opens` here. Idempotent by construction.

**Failure handling:** identical to Phases 2.5/2.6 — swallow silently, continue to Phase 3, append a `pack_run.data.errors[]` entry `{"phase": "2.7_calendar_cru_pre_render", "reason": "<short>", "detail": "<truncated stderr>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 2.8 — Apply stamped auto-merges (AUTOAPPLY §4c)

Per `shared/scripts/commitment_dedup.py`. Capture time evaluates the auto-merge gate and STAMPS `data.auto_merge_of` on a new commitment that is beyond doubt the same real-world item as an open one (owner ids equal, counterparty ids overlapping, near-verbatim title after name-stripping, DIFFERENT writers — the cross-writer capture is the corroboration). It cannot apply the merge there: the capture hook runs inside the append, before the new event exists on disk. This phase is the apply half.

Run BEFORE Phase 3 so the merged duplicate never reaches the widget:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from commitment_dedup import apply_auto_merges
print(json.dumps(apply_auto_merges('<workspace_root>', source_skill='commitments')))
"
```

Each merge runs `propose(tier='auto')` + `supersede_commitment(auto_merge=True)` + `resolve_proposal('applied')` in one pass — the FB-20 lifecycle, so no auto proposal ever rests open. Reversible: the registered `commitment_merge` reverser splits it back out via `commitment_reopened`.

**Narration:** the CHANGED line reports it in the user's language — "merged a duplicate capture of *[title]* — say `undo` to reverse any." Never the event-type name (Rule 4). Zero merges → say nothing.

**Failure handling:** identical to Phases 2.5–2.7 — swallow silently, continue, append a `pack_run.data.errors[]` entry `{"phase": "2.8_auto_merge", "reason": "<short>", "detail": "<truncated stderr>", "ts": "<UTC ISO — never the local wall clock>"}`.

# Phase 2.9 — Run mode + lateness check (Phase 3 / R4; run-mode gate v4.5.2 R2 — runs BEFORE any surface is rendered)

**Determine the run mode FIRST**, per `shared/RECEIPT_CONTRACT.md` § Run-mode detection: `scheduled` when this session was started by Cowork's scheduler executing this registered prompt (app-launch catch-up deliveries of a missed slot included); `manual` when a human caused the fire — a typed trigger, a Run Now click, a re-run request in an open chat. **When uncertain, it is `manual`**: a mis-labeled manual costs one missing lateness note; a mis-labeled scheduled fabricates lateness history (FINDINGS F-47 P1a — three false late_fire receipts in one afternoon).

Cowork fires a missed slot at next app launch, hours or days late, and without this check the run would render a stale surface as if it were fresh. Compute the tier via the shared helper (never inline the math — thresholds live in ONE constant, `late_fire.LATENESS_TIERS`; all math is machine-local, the clock cron actually evaluates in), passing the detected run mode:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import check_lateness
print(json.dumps(check_lateness('<workspace_root>', 'waiting-on', fired_via='<scheduled|manual>')))
"
```

Branch on `tier` (this does not weaken the anti-improvisation contract — every phase below still executes verbatim; the tier only governs what is RENDERED):

- **`manual`** — an interactive fire is never late: run EVERY phase normally (connector pre-scans included — a run mode never adds skip conditions), with NO timing banner and NO lateness narrative of any kind, anywhere. The helper wrote no event; do not hand-compute lateness around it (FINDINGS F-47 P1a).
- **`none` / `exempt` / `unknown`** — run normally. No mention of timing anywhere. `none` with a `suppressed` reason means the helper's ledger found the slot already served (a receipt exists after it) or minted by a schedule change — believe it: never re-derive lateness, never invent a cause ("the computer was probably asleep").
- **`note` (3–24h late)** — run ALL phases normally, but the chat output OPENS with the returned `banner` line verbatim (one line, before anything else). Nothing else changes.
- **`degrade` (>24h late)** — the surface is stale; do NOT render it. Execute every phase below EXCEPT the surface-rendering one (the widget-render/post phase): all substrate writes the task owes — events, view updates, the Phase-final `pack_run` receipt — still happen, silently and explicitly (skipping them is the Bug #98 class: an invisible write must not lose to a suppressed deliverable). Then post ONLY the returned `degrade_notice` line as the entire chat output and STOP. No widget, no digest, no Links section. The next Morning Brief reads events.jsonl, so nothing captured is lost.

The helper already appended the `late_fire` telemetry on note/degrade tiers (cleanup and the insight pass consume it to propose better default times) — do not append a second one, and never narrate the event or the tier name to the user. Carry the returned `receipt_fired_via` (`manual` / `scheduled` / `catchup`) into the fire receipt — it is the ONLY `fired_via` value `log_receipt` gets; never guess it independently.

# Phase 3 — Find the Waiting On set (3 date buckets, owner ≠ M)

**The surface filter (CTS1 — code, never prose):** after the base filter below, partition the projected open set via `surface_split.partition_surfaces(opens, <M's person_id>)` (`shared/scripts/surface_split.py`) and render rows from `partition["waiting_on"]` plus the confirm tail (`partition["unowned"]` + `partition["unconfirmed"]` ride the "Needs a quick confirm" section below — the §2.4 ruling). `partition["promised"]` and `partition["personal"]` belong to My Plate — NEVER render them here. The partition is TOP-LEVEL only (SUB1 — sub-items are excluded by `partition_subitems` inside it) and classifies on EFFECTIVE kind (§2.2 Option B). The filter trap is encoded in the module: Waiting On is owner PRESENT and ≠ M — a missing owner is unowned, never waiting-on.

**Multi-shape field reads (v3.4.3+ — REQUIRED, supersedes the v3.4.2 dual-shape spec).** Per `shared/COMMITMENT_SCHEMA.md` consumers MUST handle every commitment-event shape variant produced by any writer. Five distinct shapes have been observed in production workspaces (M's events.jsonl 2026-05-17 audit):

1. **Canonical** — `data.owner_id`, `data.title`, `data.due`, `data.status`, `data.confidence` (post-v2.7.15 standard).
2. **flat-new** — `owner_id`, `title`, `due`, `status`, `confidence` at top level; partial `data` may exist holding `evidence` + duplicate `due`.
3. **legacy** — top-level `owner` (no `_id` suffix), `owner_display`, `requester_display`, `title`, `due`, `status`, `confidence` (pre-v2.7.15 shape per the schema doc).
4. **owner_person_id-variant** — `data.owner_person_id`, `data.requester_person_id`, `data.title`, `data.due_date` (note: not `due`), `data.state` (note: not `status`), `data.confidence`. Actively produced by cr-past-meetings in some fires.
5. **pending-review** — `data.owner_name_proposed` + `data.pending_review: true`. Intentionally distinct — these are extraction proposals awaiting user confirmation, not committed commitments. Filtered out of the standard surface; surfaced via the Pulse CRU-review pipeline instead.

Use `cru_match._commitment_field(ev, "<field>")` for every commitment-field read in this phase. It tries `data.<field>` first (with all known field-name aliases), then top-level `<field>` (same alias chain). The alias table in `cru_match.py` covers `owner_id` ↔ `owner_person_id` ↔ `owner`, `requester_id` ↔ `requester_person_id`, `due` ↔ `due_date`, `status` ↔ `state`, and `confidence` ↔ `classification_confidence`. Reading only `data.<field>` silently drops shape variants 2–4 from view. Sam bug report 2026-05-17 surfaced this for shape 2 (~2/3 of his commitments dropped); M's own workspace audit revealed shapes 3 and 4 are also load-bearing (47 of 113 commitments non-canonical, ~42%).

For the confidence threshold specifically, use `cru_match._commitment_confidence(ev)` (returns a normalized float in [0.0, 1.0]). Some writers store confidence as a string label (`"HIGH"`, `"medium"`, `"high"`) rather than a 0-1 float; the comparison crashes on string values and silently drops the event. The helper coerces both via `_CONFIDENCE_LEVEL_MAP`.

Read events.jsonl. Apply base filter to every commitment event (every field read uses `_commitment_field`; confidence read uses `_commitment_confidence`):
- **Kind filter (Phase 2 Stage D, re-scoped by CTS1):** OWNER-ME task-kind items never surface here — they are My Plate · Personal rows (the `surface_split` partition already routes them). **Delegated tasks (owner ≠ M, effective kind `task` — CTS1 §2.3) DO render in this chat**: someone else acts next, so they belong on Waiting On — but `cru_match.cru_eligible` excludes task-kind from CRU, so they get NO pre-staged chase draft and NO reconcile: render with the delegated set only (`nudge` + `mark received` + `snooze 3d` + `add to my plate` — WG1-A D-A4: `nudge` is compose-on-CLICK, connector-free at render; the row carries the owner's resolved email as `To:` metadata; when NO email is on file, `nudge` degrades to the `add email then send` recovery verb and the other three verbs stay — mirror `surface_drivers._DELEGATED_VERBS` + its degrade exactly), tagged "(delegated — nudge is manual, I won't auto-chase this)". The header counts from `count_commitments` still include every kind in `total`/`by_kind` — the split filters SURFACING, not the canonical numbers.
- **Sub-item filter (SUB1 — REQUIRED):** live sub-items (projected `data.parent_id` naming an open parent) NEVER surface as their own chase rows and NEVER enter the CRU legs — `cru_match.cru_eligible` excludes them in code, same mechanism as the task filter. The PARENT is the commitment of record: its row carries the progress annotation ("2 of 3 sub-items done · next: [step]" — from the loader's `n_subitems_open`/`n_subitems_done`/`next_subitem_due` stamps) and, when the last open child has closed, the propose line "all sub-items done — close it?" (PROPOSE — never auto-close). Child activity already bubbles into the parent's movement (never render a parent "stuck" while its steps are moving). Orphan children (parent closed — cascade crash window) surface as ordinary top-level rows with "was part of: [parent title]". `data.next_subitem_due` is an annotation/ranking signal ONLY — never the parent's due; a deferred parent stays deferred.
- `_commitment_confidence(ev) >= confidence.CONFIDENCE_SURFACE_MIN` (== 0.7 as of v3.5.0; canonical constant in `shared/scripts/confidence.py`)
- `_commitment_field(ev, "status") not in ("pending_review", "proposed")` — filter out shape-5 pending-review events; they surface via Pulse CRU-review, not daily commitments
- No subsequent `commitment_resolved` event with matching id (Sam Apr 29 — stale "this is really old" items were resolved-but-still-surfacing because the prior filter only checked `thread_resolved`. Both event types close the commitment; both must filter it out. Mirror `shared/scripts/cru_match.py::load_open_commitments`, which already does both.)
- No subsequent `thread_resolved` event with matching id
- No active `chat_dismissal` event with target_id matching this commitment id, where "active" means:
  - if the event has `data.snooze_until` set, the date hasn't passed yet (v3.5.0+ — honors the duration the user actually picked when they clicked `snooze 3d` / `snooze [N]`. Pre-v3.5.0 every dismissal expired at 24h regardless, so `snooze 3d` effectively only snoozed for 1 day — verified in the 2026-05-17 audit)
  - else, the event is within the last 24h (legacy default — covers dismissals written without a snooze_until field)
  - AND no later `chat_dismissal_cleared` references it (v4.6.0 S4 — the Unmute verb / triage batch undo; an unmuted dismissal is INACTIVE regardless of remaining TTL). Don't hand-roll this three-way check — `mute_ledger.active_dismissal_target_ids(events, now_iso)` is THE liveness filter and encodes all three conditions.
- **(v2.10.3)** Commitment's primary_thread_id resolves to a project with `status` in `{active, paused, blocked}` — exclude commitments tied to `dormant` or `archived` projects (those are accessible via `show more` + `go [project]` but don't surface in the daily commitments flow). If the user wants visibility on dormant-project commitments, they can run `show more` to see everything regardless of status.
- **Aging cap (Sam Apr 29):** for `aging_undated` items only, exclude commitments logged ≥ 60 days ago with NO related event activity (no outreach_sent, no commitment_update, no chat_dismissal, no thread reply touching the commitment) in the last 30 days. These are ghost items that almost certainly resolved outside the system; surfacing them daily as "wrong person" / "really old" pollutes the widget. The user can still see them via `show more`. `overdue` and `due_near` items are unaffected — those have an explicit due date that gives the user a concrete reason to keep seeing them.

**Header counts — the one bucket export (Phase 2 Stage A + v4.5.2 R4 + v4.6.0 MC2, MANDATORY).** The widget-header numbers (and the all-clear counters) MUST come from `commitment_state.count_commitments(opens, user_person_id=<M's person_id>, now_iso=<now>, movement=movement)["headline"]` over the FULL `load_open_commitments` set — `n_total = headline["total"]`, `n_you_owe = headline["you_owe"]`, `n_owed_to_you = headline["owed_to_you"]`, `n_unowned = headline["unowned"]`, `n_unconfirmed = headline["unconfirmed"]`, `n_stuck = headline["stuck"]`, `n_blocked = headline["blocked"]` — where `movement = commitment_activity.derive_commitment_movement("<WORKSPACE>/_hq/data/events.jsonl")` (v4.6.0 MC2: the REAL stuck metric — no movement 21+ days, or blocked on a named person; `blocked ⊆ stuck`. Derive the map ONCE per fire and pass the SAME map everywhere a movement-based number or row renders — the F-54 cross-surface-split rule. If `headline` carries no `stuck` key the derivation was unavailable: omit the segment, never render 0). **Never fold unowned or unconfirmed into a direction** — the pre-R4 rule here (`n_owed_to_you = they_owe + unowned`) is exactly why one dogfood day produced four different open counts across surfaces (F-47 P2b / F-56: this chat said 52 owed-to-you while triage said 40 + 18 unowned, from the same substrate). Unowned and unconfirmed render as their own numbers, identical on the morning brief, this chat, and commitment-triage. The confidence / dismissal / project-status / aging filters above decide which items SURFACE as actionable rows — they never shrink the header counts. This is the same export the morning brief and commitment-triage render; the 2026-07-01 audit found three surfaces disagreeing (104 vs 54 vs 105) because each hand-rolled this math. Note `load_open_commitments` now folds `commitment_updated` deferrals into the effective `data.due` (a `push to [date]` item stops rendering overdue) — never re-derive due from the raw original event. SUB1: every headline number is computed over TOP-LEVEL items only — a parent with 3 open sub-items is **1** open commitment, never 4 (decomposing an item must not jump the headline). When sub-items exist the headline carries the additive `subitems_open` / `subitems_done_of_open_parents` keys (absent otherwise — never render a guessed 0); the header may append "(+N sub-items)" from `subitems_open`, never a new bucket.

## NEEDS A QUICK CONFIRM — the daily confirm section, renders FIRST in the widget (v4.6.1 W4b)

**Principle: unconfirmed items don't age into the pool — they escalate to confirmation** (F-13 P2b / F-56: owner misattributions persisted for days because nothing ever asked). The chat OPENS with this section — it renders ABOVE meeting_today and both directions, titled **"Needs a quick confirm"**. Skip the section entirely when every selector below returns empty (never pad).

Build the row set in code — never hand-derive:

```python
import sys; sys.path.insert(0, "shared/scripts")
from confirm_flow import (select_confirm_items, select_promotion_proposals,
                          load_open_person_proposals)
from mute_ledger import active_dismissal_target_ids

dismissed = active_dismissal_target_ids(<all events>, "<now ISO>")
confirm_rows = select_confirm_items(opens, "<now ISO>", dismissed_ids=dismissed)
promo_rows   = select_promotion_proposals(opens, dismissed_ids=dismissed)
person_rows  = load_open_person_proposals(events_path, dismissed_target_ids=dismissed, suppress_on_file=True)  # FS-19: already-a-contact rows never surface
```

`opens` is the SAME projected set Phase 3 already loaded — no second read. Three row classes, each with its verb cluster (display labels from `verb_taxonomy` — never restate them):

1. **Commitment rows** (`confirm_rows` — every capture younger than the 7-day escalation pin that is pending_review, unowned, or a suspected duplicate; the daily window tiles exactly with the pin, so no amber item ever falls between this section and the Unconfirmed block): one line each (title + who/what we think it involves + the `review_reason` when present, plain English). Actions: `mine` / `theirs to [name]` / `drop` / `snooze 3d` (UXR1 D1, M ruling 2026-07-21 — the tail slimmed from five: `not relevant` and `add to my plate` left the EMISSION only; their wire ids stay registered so persisted 5-verb widgets still dispatch). Rows whose class list includes `suspected_duplicate` render INSTEAD as **"looks like a duplicate of [the flagged item's title] — merge, or keep both?"** (C4's contract) with actions `merge` / `keep both` / `drop` — the flagged target's title comes from looking its id up in `opens`.
2. **Unknown-person rows** (`person_rows` — unadjudicated person proposals; these re-surface EVERY day until adjudicated, no age window — the F-46 P2b stranding fix): "[name] came up in [source context] — track them?" Actions: `add person` / `same as [existing]` / `proposal not relevant`.
3. **Promotion-proposal rows** (`promo_rows` — kind=task items that gained a resolvable counterparty via reassign/edit/corroboration): **"Make it a commitment?"** framed as a question. Actions: `promote` / `not relevant` / `skip`. PROPOSE only — nothing auto-promotes, ever (4.6 fold-in, M decision).

**Caps + counts:** cap the section at 8 rows per fire (oldest first, `+N more ride tomorrow` in the section title when trimmed) — confirm rows are light (no drafts, no original_thread accordion) and do NOT count toward the 7-item chase cap. Header counts are untouched: unconfirmed items count ONLY in `headline["unconfirmed"]` (R4's one bucket export) — never folded into a direction, and this section never changes the numbers.

**Guardrails (restate nowhere else, enforce everywhere):** unconfirmed items NEVER enter chase — no chase draft is ever pre-staged on a confirm row (no auto-email on a guessed owner; the code paths already enforce this — `pending_review` is filtered out of the chase buckets and CRU). Rows re-surface daily until adjudicated: a verb click adjudicates; a snooze quiets for its stated TTL only. Every row embeds the commitment's `data.id` (or the proposal's seq) VERBATIM for stateless dispatch.

## MEETING TODAY — relevance bucket, renders FIRST in each direction (v4.5.2 C1 / F-44)

Before the date buckets, build the meeting-relevance set. F-44's failure: sweep-recovered items about that very morning's 9:15 were invisible on every chase surface because all three buckets key on the due date (and the confidence floor drops confidence-less recovered captures besides). Relevance to a meeting happening TODAY is its own reason to surface — **a missing due date must not make a meeting-relevant item invisible on the day of the meeting.**

1. Pull TODAY's calendar events (the seam-resolved calendar list tool with the day window compiled via `connector_adapters.calendar.compile_window(<start of today>, <end of today>, provider)`, machine-local — a dedicated narrow fetch; do NOT reuse Phase 2.7's created/updated-since-window pull). Calendar unavailable → skip this bucket entirely (skip-not-fail); the date buckets below are unaffected.
2. Resolve each event to `{"meeting_id", "title", "attendee_person_ids", "attendee_names"}` — attendee emails → person_ids via `entities.json`/`aliases.json`; attendee_names include display names PLUS alias spellings from `aliases.json`.
3. Match in code — never hand-derive:

```python
from commitment_state import match_commitments_to_meetings
meeting_today = match_commitments_to_meetings(opens, todays_meetings,
                                              user_person_id="<M's person_id>")
```

Rows match by counterparty (`counterparty_id` / `owner_id` is an attendee) OR name-mention (an attendee's name appears in the item's own text — catches counterparty-less legacy captures whose title names the person). **CTS1 scope:** render ONLY the rows whose surface is `waiting_on` (owner present, ≠ M) as the FIRST bucket of the chat, labeled with the meeting ("you see [name] at [time] — this is open between you"). The owner-me matches render as My Plate's meeting bucket (that orchestrator runs the same `match_commitments_to_meetings` call over ITS partition) — never here.

**Exemptions (deliberate, all four):** the meeting_today bucket bypasses (a) the due-date requirement — undated items render "no date set", never a blank; (b) the `aging_undated` ≥ 7-day floor — a fresh undated capture that matters today surfaces today; (c) the confidence floor — recovered captures carry no confidence score and would otherwise never surface here; (d) the chase-eligibility filter — a meeting-linked DELEGATED task (owner ≠ M, kind task) renders with the delegated set (`nudge` / `mark received` / `snooze 3d` / `add to my plate` — nudge composes on CLICK, D-A4; no owner email on file → `nudge` degrades to `add email then send`, exactly the driver's Phase-3 degrade), never a pre-staged chase draft. The surface partition itself is NOT bypassed (CTS1): owner-me meeting matches belong to My Plate's meeting bucket. Everything else holds: closed/dismissed/suppressed items never enter (they are not in `opens`), and the surface-preference filter still applies.

**pending_review rows render as a confirm, not a chase:** tag `(captured from a chat — confirm it's yours)` and use the OWNERSHIP cluster (`mine` / `theirs to [name]` / `drop` / `snooze 3d` — mirror `surface_drivers._REVIEW_VERBS`; UXR1 D1, M ruling 2026-07-21: the tail slimmed from five — `not relevant` and `add to my plate` left the EMISSION only, wire ids stay registered so persisted 5-verb widgets dispatch; `mine` claims + clears pending_review, `theirs to [name]` reassigns confirmed, `drop` dismisses, never the opaque person-record `confirm` verb). Never pre-stage a chase email on an unconfirmed item — no auto-email on a guessed owner.

**Caps:** meeting_today takes priority INSIDE the existing 7-item total cap (cap 3 meeting_today rows per fire, soonest meeting first; the date buckets fill the remainder). No double-surfacing: an item already rendering in meeting_today is excluded from the date buckets below for this fire. Header counts are untouched — this bucket changes SURFACING only.

Then split the surviving `waiting_on` rows into THREE date buckets (the owner-me directions are gone from this chat — CTS1; My Plate owns them):

## WAITING ON (`partition["waiting_on"]` — owner present and ≠ M; plus `owner_id null` with non-empty `data.owner_external`)

Filter additionally: M is requester (`requester_id == M's person_id`) OR (`requester_id` empty AND M was attendee at the meeting where commitment was made — check `source_event_seq`). All field reads go through `_commitment_field` for the same dual-shape reason.

**v2.14.19+ reachability split — DO NOT exclude unreachable owners; surface them as a different action shape.** Two sub-buckets:

### B-reachable: owner has entity record + email in entities.json
Same date buckets (`overdue`, `due_near`, `aging_undated`). Renders with the standard WAITING ON action set: `["N send", "N draft", "N follow-up call", "N mark received", "N escalate to memo", "N snooze 3d"]`. Pre-staged chase email lives in the widget.

**Learned chase cadence (Phase 6 Loop 6).** When surfacing an OWED-TO-YOU item for chase, honor the per-relationship-type chase window learned by insight-generator's Pass 7b instead of the flat 7-day default: `from chase_policy import load_chase_policy, get_chase_window` → `chase_days, escalate = get_chase_window(policy, <owner's org relationship_type>)`. An `aging_undated` item from a relationship that "goes quiet 40% of the time" surfaces to chase at day 3 rather than 7; after `escalate` silent chases, the item's annotation suggests a call (`follow-up call`). Missing store → the default `(7, 3)`, so behavior is unchanged until the CEO approves a policy. This tunes WHEN a chase is offered; it never auto-sends.

### B-unreachable: `data.owner_id` is null AND `data.owner_external` is set (e.g., `"Bowie"`) — owner was named in extraction but has no entity record yet
Same date buckets — **today-due items here STILL count as "needing action."** Renders with a different action set scoped to "you can't auto-chase yet — fix that first": `["N add as person <owner_external> to <inferred_org_or_blank>", "N add to my plate", "N skip"]` (v2.14.36+ — `add context [text]` dropped; the per-item "+ Add context" toggle handles context capture universally). Tag annotation: `(no email on file — adding <owner_external> as a contact enables auto-chase next time)`.

Per M's v2.14.18 testing: an owed-to-you commitment due today with no contact info should NOT disappear into "0 needing action." The action is different (add the contact) but it's still action — and the user explicitly noticed the silence as a UX bug. v2.14.19 surfaces these explicitly.

- **`overdue`** — same logic, sort by aging desc. Cap top 5 across BOTH sub-buckets combined.
- **`due_near`** — same. Cap top 5.
- **`aging_undated`** — same. Cap top 5.

**Total cap across all buckets — meeting_today plus the 3 date buckets (× 2 reachability sub-buckets where applicable): 7 items per fire (v3.13.7+, CTS1: this chat's cap is now all owed-to-M rows — the owner-me direction has its own budget on My Plate).** meeting_today rows count toward the 7 and take priority (max 3, see above). Empty buckets are omitted entirely (no "0 items" placeholders).

**Why 7, not 16 — a DESIGN cap (EW2+T reframe):** the cap originated as the v3.13.7 transmission-ceiling fix (Bug #14 — an 11-item, 81KB widget silently failed the byte relay on every fire). The widget_code transport with pagination (§ Transport) removed the byte ceiling, but the 7-cap SURVIVES on design grounds: the daily chat is an attention surface, and 7 chase-ready items is what a CEO actually processes in one sitting. Do not raise the cap because "the transport can carry more" — full-set review is commitment-triage's job, not the daily chat's.

**For overflow ("I have more than 7"):** the `show more` bulk action stays canonical and triggers paginated re-render of the next 7 items via apply-choices dispatch (see Bulk actions section at the bottom of this file). The original 16-cap is preserved as the OVERALL pagination ceiling across all `show more` pages combined — pages of 7 until either 16 items shown or all items exhausted, whichever comes first. Beyond that, the user runs the substrate query directly via `show my list` (which is page-tolerant by design).

**"Needing action" counter (v2.14.19+):** the visible counter at the top of the widget counts every item that survives ANY bucket — meeting_today included (v4.5.2 C1) — including B-unreachable items. The previous behavior (counter excluded unreachable) confused users when a date-pressing item appeared in the list but wasn't reflected in the counter.

**Surface-preference filter (Phase 6 Loop 2 — before rendering).** After the buckets are built and capped, drop any commitment the CEO has taught the system to stop chasing (insight-generator Pass 14 → `_hq/data/surface-preferences.json`):

```python
import sys; sys.path.insert(0, "shared/scripts")
from surface_preferences import load_surface_preferences, is_suppressed
prefs = load_surface_preferences("<abs workspace root>")   # treat-as-empty-if-missing
surfaced = [c for c in surfaced
            if not is_suppressed(prefs, "commitments", item_class="chase",
                                entity_id=c.counterparty_person_id)]
```

Missing store → no-op. This hides the chase PROMPT only; the commitment stays open in the substrate and still counts. The `is_suppressed` filter is the SAME one every widget orchestrator applies.

# Phase 3.6 — CRU review items (Phase 2 Stage E, F5 — the pending band surfaces HERE)

The 0.30–0.55 MEDIUM matches (from apply-choices sends, the 2.5/2.6/2.7 pre-render legs, past-meetings transcripts, AND reconcile-sent's newly-persisted pending band) must not evaporate — this chat is their one-click confirm/deny surface.

1. Load via `cru_match.load_open_review_proposals(events_path)` (last-7-days window, already filtered for confirmed/dismissed/otherwise-closed).
2. Render as a compact **"Did these get handled?"** section at the BOTTOM of the widget (after the 6 buckets, before any fr-items), sub-namespace `r1/r2/...` — same shape as Pulse's CRU-review items. Cap 3 per fire (oldest first; the rest ride future fires — the 7-day window self-prunes). Each row: the commitment title + the proposal's evidence in plain English ("looks like your Tuesday email to Sam covered this"). NO score display beyond "likely".
3. Actions per row (REVIEW cluster — all canonical): `confirm` · `not relevant` · `add to my plate`.

**Reply handlers (dispatched via apply-choices on this orchestrator's `src`):**
- `[rN] confirm` → `commitment_state.close_commitment(workspace_root, <underlying commitment_id verbatim>, resolved_by=<the commitment's owner_id>, evidence=<the proposal's evidence>, source_skill="commitments", user_confirmed=True)` — THE closure path; the explicit click IS user confirmation, so this may close a `pending_review`-flagged commitment. Confirm: `✓ Closed: [title].`
- `[rN] not relevant` → `commitment_review_dismissed` event (via `cru_match.build_commitment_review_dismissed_event`, 60-day cooldown). The commitment STAYS OPEN. Confirm: `✓ Kept open — that signal wasn't it.`
- `[rN] add to my plate` → `commitment_state.create_personal_task` (owner-me `task` — it lands on My Plate), same as everywhere.

Both closers retire the proposal implicitly (append-only — the original `commitment_review_proposed` event is never touched). Pulse keeps its own CRU-review pass — the two surfaces read the same `load_open_review_proposals` set, and whichever the user answers first wins (the other stops showing it).

# Phase 3.8 — NUDGED — NO REPLY (Tue/Thu fires only — W5, enabled Phase 4 2026-07-02; renamed from "WAITING ON" by CTS1 §4.1)

**Naming (CTS1 §4.1 — mandatory rename):** this section was titled "WAITING ON" when the chat was "Commitments". Now the whole CHAT is named Waiting On, so the old section title would make one phrase mean two things in one product. This section is the quiet chased tail — "nudged, no reply" — and renders under that name. Never render a section titled "WAITING ON" inside the Waiting On chat.

The reliability spec's W5 waiting-on chase, riding this orchestrator's Tue/Thu fires (nothing new registers; the gates it waited on — the Stage D kinds split and Stage E counterparty receipts — are merged). It rescues the "you nudged, they went quiet" set: items the user already chased that the daily filters have stopped surfacing.

**Run only when the fire's machine-local weekday is Tuesday or Thursday.** On other days, skip this phase entirely — no section, no mention.

**Qualification (substrate-only — no connector fetch in this phase):** a WAITING ON item from Phase 3's set qualifies when ALL hold:
1. Effective `data.kind != "task"` (same Stage D filter as everything above) and the item is still open after the 2.5/2.6/2.7 pre-render scans. Still-open is the no-reply proxy: if the counterparty had replied or delivered, the inbound leg (Phase 2.6, cumulative across every prior weekday fire) or a `mark received` would have closed it or queued a review proposal.
2. A prior outbound touch exists: an `outreach_sent` event whose `source_skill` normalizes to `commitments` (via `source_skill_compat.normalize_source_skill`) targeting this commitment_id — OR a `sent_reconcile`-attributed outbound naming its counterparty (Stage E `data.counterparty_id` / `counterparty_name` receipts identify the counterparty; skip items with no resolvable counterparty).
3. That latest outbound touch is ≥ 3 weekdays old (machine-local, same clock as Phase 2.9).
4. The item is NOT already rendering as an actionable row in today's main WAITING ON sections (no double-surfacing — NUDGED — NO REPLY exists for the quiet tail, not to echo the main list), and no `chat_dismissal` for it is live.

**Render:** one extra section after the main date buckets, title `⏳ NUDGED — NO REPLY`, cap 5 (oldest outbound first; the rest ride the next Tue/Thu fire). Each item: title + counterparty + one plain-English age line ("you nudged Sam last Tuesday — nothing back"). Actions reuse the standard WAITING ON cluster verbatim — `send` / `draft` on a pre-staged nudge email + `mark received` + `snooze 3d` — NO new verbs (`CANONICAL_ACTIONS` untouched; apply-choices dispatches these through the existing commitments handlers on this orchestrator's `src`).

**The nudge draft** goes through email-writer's lazy-draft path exactly like Phase 7's chase drafts, with the Phase 5 severity tier bumped one level (a re-nudge is never `friendly`), and the repeat-chase suppression in Phase 5 applies unchanged — a WAITING ON send writes the same `outreach_sent` receipt, which resets this phase's 3-weekday clock.

**Counts:** WAITING ON items are already inside `count_commitments`' totals (they're open owed-to-you items) — the section changes SURFACING only; never add them to the header numbers twice.

# Phase 4 — Group by recipient

If a single owner owes multiple things in the same bucket, merge into ONE chase email listing all items. Subject: "Quick check on a few things" (the old "Circling back..." subject is on the universal banned-phrase list — the voice gate now reads subjects too). Body lists each. Singletons stay single-subject. (My Plate never groups — each thing M owes is its own status email; that rule lives in orchestrator-my-plate.md now.)

# Phase 4.5 — Multi-counterparty fan-out (MC1)

A single commitment can name N counterparties — "send the deck to the board" is owed to three people at once (`data.counterparty_ids` carries the roster; `data.counterparty_id` stays the primary). Do NOT chase or close it as one blob:

1. **Outstanding set:** call `commitment_parties.outstanding_counterparties(commitment)` — the roster minus everyone already received (`data.received_from` / `data.received_from_names`, folded onto the projection by the loader). Render **one row per OUTSTANDING counterparty**, grouped per person exactly like Phase 4's per-recipient grouping (received counterparties simply don't appear — they've delivered). A single-counterparty commitment has exactly one entry, so this reduces to today's behavior with zero change.
2. **Per-person verb:** each fan-out row carries `mark received from [name]` (verb_taxonomy `mark received from [name]` → `commitment_state.mark_partial_received`), with the counterparty id embedded on the row so apply-choices dispatches statelessly. An owed-to-M multi-counterparty item drafts one nudge per outstanding recipient (the owner-me fan-out — one status note per outstanding recipient — lives on My Plate now, CTS1). Never chase a counterparty already in `received_from`.
3. **Closure PROPOSAL, never auto-close:** when the projection carries `data.all_counterparties_received: true` (every counterparty is in), render a single "everyone's received — close it?" row (the `mark done` / `resolved` verb) INSTEAD of the fan-out. The item stays open until the user clicks; nothing here auto-closes. The CRU pre-render scans (2.5/2.6/2.7) already record per-person receipts rather than whole-closing a multi-counterparty item (`match_*` returns `recommendation: "partial_received"` with `matched_counterparty_ids` — dispatch each through `mark_partial_received`, never `close_commitment`).

# Phase 5 — Apply severity tier (auto-tone by aging — every chase row in this chat)

| Aging | Tier | Voice tilt |
|---|---|---|
| 1-7 days | **friendly** | Casual circle-back. "Still on for this week or has timing shifted?" |
| 8-30 days | **firmer** | Timing-alignment, no blame. "Touching base on X timing. Looking to align on a revised ETA." |
| 30+ days | **status check** | Formal request. "Status check on X — want to align on where this stands." |

Pass tier to email-writer as voice directive. Repeat-chase escalation: scan events.jsonl for prior `outreach_sent` whose `source_skill` normalizes to `commitments` (via `source_skill_compat.normalize_source_skill` — matches the bare `commitments` form AND legacy `cr-commitments` / `cr-commitment-chase` history in workspaces that predate the v2.14.27 rename) targeting same commitment_id within last 14 days. If found → bump tier up one level. Prevents identical chase repetition when nothing's moving.

(CTS1: the owner-me direction's fixed status-email voice tilt and the Phase 6 recipient classification moved to `orchestrator-my-plate.md` with the rows they governed.)

# Phase 7 — Draft emails (lazy — no Gmail writes yet)

Per `EMAIL_DRAFT_PROTOCOL.md` §1: generate draft TEXT only. Show inline in chat. NO Gmail draft creation at fire time.

For each chase-eligible row (or grouped set): run `email-writer` with the Phase 5 tier voice directive. Delegated-task rows (owner ≠ M, kind task — Phase 3 kind filter) get NO pre-staged draft; their `nudge` verb (WG1-A D-A4) composes the chase on demand at dispatch time — compose-on-CLICK through apply-choices' nudge handler, never here at fire time.

# Phase 8 — Memory updates (silent per Rule 9)

Append to events.jsonl:
- `connector_read` for events.jsonl scan
- The fire receipt — **ONE call to the canonical receipt helper (`shared/scripts/receipts.py`, v4.5.2 R1). NEVER hand-roll the receipt JSON** — this exact skill wrote two different receipt shapes in one day during the dogfood (`{task_id: 'cr-commitments', fired_at, outcome}` in the morning, `{kind, date, status, late_tier}` in the afternoon — FINDINGS F-47 P2a):

```python
from receipts import log_receipt
log_receipt(
    WORKSPACE_ROOT, "waiting-on",  # CTS1 — receipts key on the TASK id; events keep source_skill='commitments'
    fired_via=lateness["receipt_fired_via"],  # from Phase 2.9 — manual | scheduled | catchup; never guess it
    surfaced=n_surfaced,
    duration_ms=elapsed_ms,
    late_tier=lateness["tier"] if lateness["tier"] in ("note", "degrade") else None,
    extra_data={"items_drafted_text": {...}, "errors": [],
                "telemetry": {...}},
)
```

**Telemetry capture (v2.14.0+ Phase 1):** Build the telemetry block via `shared/scripts/telemetry.py` `build_pack_run_telemetry()` (same pattern as orchestrator-inbox.md Phase 7). Track connector calls + prompt/response sizes + duration. Pass it through `extra_data` as `telemetry: {...}`. Silent — never narrated to chat.

NO `draft_created` events at fire time (lazy creation). Append to staging_emissions.jsonl per item drafted.

**Telemetry writes silently. Never narrate to chat.** No "Logged: pack_run seq …" line at the end.

# Phase 9 — Post the chat turn (v2.10.8+ — renderer-driven, ENFORCED)

**Mandatory execution contract (v2.10.8+):**

You MUST execute the renderer via `mcp__workspace__bash`. You MUST NOT hand-write or paraphrase the chat string.

**Step 1 — verify renderer imports (FIRST action of Phase 9):**

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget, validate_chat_output, validate_rendered_widget, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError, WrapperContractError; print('OK')"
```

If stdout is not exactly `OK`, ABORT the fire and surface plain English: `(Renderer pre-flight failed — chat output deferred. Diagnostic: <error>.)` Do NOT post any chat string.

---

## ⛔ ZERO-MANIPULATION CONTRACT (v2.14.34+, transport-updated EW2+T) — READ THIS BEFORE STEP 2

**The render is sealed. Post via `widget_transport.render_and_persist` (all validators fire inside) and pass `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code` — never hand-composed or post-processed HTML, and never a post-processed version of `transport["html"]` or the persisted file. Any post-processing is FORBIDDEN.** (`shared/CHAT_ACTION_WIDGET.md` § Transport — EW2+T, F-15.)

Specifically forbidden — zero tolerance:

1. **No "minification."** Don't strip whitespace, don't collapse tags, don't compress CSS. Renderer output looks "verbose" by design — every `<div class="cr-item-inputs">` block, every `<div class="cr-action-input" data-input-for-n="..." data-input-for-action="...">` wrapper, every `<style>` rule is functionally required.
2. **No "trimming for size."** If the HTML is 50KB+, that's the correct size — every wrapper, every input-affordance, every per-item note field is necessary. The agent's instinct to "trim" cuts wrappers silently and produces a widget where buttons select-gold but nothing opens. The customer can't see the failure; only console.warn fires.
3. **No "cleaning up duplicates."** What looks like a duplicate `cr-action-input` is the wrapper for a different action on the same item. Each `data-input-for-action` value is unique per (n, action). Don't collapse them.
4. **No filtering items the renderer included.** If the data_view passed all validators, every item the renderer emitted belongs in the widget.
5. **No re-emitting the HTML in a different shape.** If the canonical output is judged suboptimal, the fix is in `chat_output_renderer.py`, not in agent post-processing.
6. **(v2.14.37+, EW2+T) No skipping `show_widget` after a clean transport call.** If `widget_transport.render_and_persist()` returns without raising, you MUST call `mcp__visualize__show_widget` with `transport["html"]`. Narrating that the widget "couldn't transmit," "hit a session payload limit," "exceeded the live widget surface," "was too large," "render validated but..." or any other reason is FORBIDDEN — none of those phrases exist anywhere in this codebase, they are pure agent improvisation, and the widget_code transport removes the size wall those improvisations pointed at. The clean transport call IS the contract — the widget ships. If `show_widget` itself errors, surface the error string verbatim and STOP. Do not paraphrase, do not "summarize what the widget would have shown," do not chat-list the items as a substitute.
7. **(v2.14.37+) No markdown lists as a substitute for widget rendering.** If a user follow-up asks you to "surface past commitments" / "show what's open" / "list the X" — any kind of "render these items in chat" ask — the path is `render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`). Emitting a markdown bullet list of items in chat is FORBIDDEN, even when the prior widget was empty-state, even when the user explicitly asked for "a list," even when you think markdown is "lighter weight." Re-fire through the canonical path with the appropriate `data_view` (e.g., adjust filter threshold to surface previously-noise-filtered items as `tracked_items`).

**Pre-ship validation is built in (EW2+T):** `render_and_persist` runs the full validator chain — the renderer's canonical-action / data-shape / leak checks AND `validate_rendered_widget` (every input-needing button has its matching wrapper; raises `WrapperContractError`). If it raises, fix the data view and re-render via the canonical path — never hand-patch HTML, never post until the transport call passes clean.

**Why this contract exists:** 2026-05-07 cr-commitments fire visibly broken — Edit-then-send and Add-context buttons selected gold but no textareas opened. Two days of misdiagnosis chasing CSS / scroll / focus issues (v2.14.30 shipped defensive visibility hardening based on flawed premise). Cowork's structural diagnostic finally caught it: the agent post-minified the renderer's output and dropped 4/11 items' input wrappers. Renderer was correct; the bypass dropped wrappers silently. Same anti-pattern as v2.14.18 (empty-state hand-built widget). `validate_rendered_widget` is the structural defense — it makes this class of bug impossible to ship without raising loudly.

**v2.13.0 enforcement:** `CanonicalActionError` raised pre-render → fix the data view's actions to use canonical verbs (`prep deep work`, `send`, `draft` (consolidated v2.14.4+; was previously two separate verbs), `snooze 3d`, `push to [date]`, `resolved`, `mark received`, `mark received all`, `follow-up call`, `escalate to memo`, `skip` — see `CANONICAL_ACTIONS` in `chat_output_renderer.py`; `edit then send` is NOT canonical since FB-17 — emitting it raises this error). `LeakDetectedError` raised post-render → strip forbidden patterns from headers / context_tags / body_lines / sub-item summaries. Both blocking; no silent fallback.

**Empty-state rule (v2.14.19+ — REQUIRED, no exceptions):** if all 6 buckets (× 2 reachability sub-buckets) are empty after filtering, do NOT improvise an "all clear" widget by hand-typing HTML. Build a data_view with `widget_mode: "all_clear_summary"` and pass it to `render_chat_output_widget()` like any other state. The renderer has a first-class branch for this mode that produces counter cards + summary callout + read-only WHAT'S ON THE BOOKS list. Shape:

```python
data_view = {
    "widget_mode": "all_clear_summary",
    "header": "Waiting On — nobody owes you anything that needs a nudge this morning",
    "sub_header": f"{day_full}, {month_short} {day_num} · {fire_time} check",
    "counters": [
        # v4.5.2 R4: values verbatim from counts["headline"] (n_open_total =
        # headline["total"], directions = headline["you_owe"]/["owed_to_you"]).
        # Unowned/unconfirmed items are in Open total but neither direction —
        # never fold them into Waiting on. The "On your plate" tile keeps the
        # cross-surface parity visible (F-56) — its ROWS live on My Plate.
        {"label": "Open total", "value": n_open_total},
        {"label": "Waiting on", "value": n_owed_to_you},
        {"label": "On your plate", "value": n_you_owe},
        {"label": "Needing action", "value": n_needing_action},  # MUST include B-unreachable items per the v2.14.19 reachability rule above
        # v4.6.0 MC2: append {"label": "Stuck", "value": n_stuck} ONLY when
        # headline carries the key and it is > 0 — an all-clear morning with
        # stuck items should still show the honest number.
    ],
    "summary_line": "Nothing overdue, nothing due in the next 3 days, and nothing has been sitting open long enough to be aging. The N things others owe you were either recently captured or have downstream dates further out.",
    "tracked_items": [
        # Read-only line list — this chat's partition only. NO action buttons.
        {"direction": "Waiting on", "title": "Bowie — 2-3 redacted prior EB-5 packets", "due": "today"},
        # ...
    ],
    "footer": None,  # NEVER add bottom buttons. The agent's instinct to add Show all open / Add email for X / Prep deep work: Y is what produced the v2.14.18 hand-built widget. Empty-state has no buttons.
}
from widget_transport import render_and_persist
transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="waiting-on")
# Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) — same pipeline
# as the standard widget (EW2+T, § Transport).
```

**Why this rule exists:** in v2.14.18 the agent fired this orchestrator with 0 items qualifying after the bucket filter, judged the canonical empty-state as worse UX than a richer custom card, and bypassed the renderer entirely. Result: a hand-typed widget with hardcoded "Needing action: 0" counter, four model-improvised bottom buttons (`Show all open`, `Add email for Sloan`, `Add Bowie as contact`, `Prep deep work: EB-5`), and zero validators run. Three contracts broken at once (Rule 1 widget format, Rule 5 canonical actions, Rule 19 data shape) — the enforcement chain is structurally unable to catch a renderer bypass because the validators run AT render time. The fix is to make the canonical empty-state look good enough that the agent has no incentive to improvise. NEVER hand-build the empty-state widget, even if you think the canonical version is mid-tier UX. If the canonical UX feels wrong, file a follow-up to improve `_render_all_clear_summary` in `chat_output_renderer.py` — do not improvise around it.

**One-command driver (FB-15) — the deterministic core in ONE call.** The
partition, the header counts, the Delegated section, and the Needs-a-quick-confirm
tail are all deterministic, so a single driver invocation builds them,
renders + persists the page, and — with `--fired-via` — writes the surface's
`waiting-on` `pack_run` receipt INSIDE the same call (FB-7; render and receipt
can never be split). You supply only the connector-dependent part: the
pre-staged **chase drafts** (email-shaped rows, composed via email-writer's
lazy-draft path — Phase 7), passed as `--chase-json`, exactly as staff-meeting
passes `--moves-json`.

```bash
python3 shared/scripts/surface_drivers.py waiting-on \
    --workspace "$WORKSPACE" [--page N] [--chase-json <chase-rows.json>] \
    --fired-via "<the Phase 2.9 receipt_fired_via>"
```

**`--fired-via` is MANDATORY on the page-1 call — it is the receipt (FB-7).**
Relay the bytes between `CR-WIDGET-HTML-BEGIN`/`END` to `show_widget` as
`widget_code`, verbatim; the `CR-RECEIPT: {...}` line after the END marker is
the confirmation — do NOT append a second receipt, NEVER hand-roll receipt JSON.
Pages 2+ (`show more`) never receipt, and a non-manual re-run inside the RV-3
guard window never double-receipts. Pages 2+ also slice the page-set page 1
froze rather than re-reading the substrate (PAGESNAP; see
`shared/CHAT_ACTION_WIDGET.md` § "A page-set is ONE question asked ONCE") — if
`CR-PAGINATION` carries `refreshed`, `suppressed`, or `clamped`, SAY it in one
line before the rows. The manual `python3 -c` assembly below
remains valid for callers that need to hand-shape sections the driver does not
build (e.g. the meeting-relevance bucket or the Tue/Thu nudged-no-reply tail);
those still go through the same `render_and_persist` chokepoint.

**Step 2 — build data_view, render widget HTML, post via show_widget (v2.10.9+):**

```python
# (Inside python3 -c body invoked after the Rule 22 preamble + cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist

# Build sections — CTS1: this chat renders the Waiting On partition only
# (confirm tail first, then meeting_today, then the date buckets, then the
# Tue/Thu NUDGED — NO REPLY tail). Empty sections omitted.
sections = []
if confirm_rows or promo_rows or person_rows:
    sections.append({"title": "NEEDS A QUICK CONFIRM", "count": ..., "items": ...})
if nudged_no_reply_rows:  # Tue/Thu only — Phase 3.8
    sections.append({"title": "⏳ NUDGED — NO REPLY", "count": ..., "items": ...})

# UXR1 D6 (reviewer-classified doc defect): this example previously hand-
# shaped a "↙ WAITING ON" section — a title §"Naming (CTS1 §4.1)" BANS inside
# the Waiting On chat. The main date-bucket/chase sections come from the
# one-command driver (build_waiting_on_view) — never hand-shape them here;
# the manual path exists for the sections the driver does not build (the
# meeting-relevance bucket and the Tue/Thu ⏳ NUDGED — NO REPLY tail above).
# Section titles render in brand-gold monospaced caps via the widget CSS.

data_view = {
    "widget_mode": "all_batch_widget",
    "source_skill": "commitments",  # W4 (Phase 3) — stamped into every Apply-all tuple as src; apply-choices dispatches on it statelessly. CTS1: kept as 'commitments' DELIBERATELY — the dispatch registry, verb surfaces, and event history all key on the commitments family; the TASK id (waiting-on) is a scheduler/receipts concern, not a dispatch concern.
    # v4.5.2 R4 + v4.6.0 MC2: all numbers verbatim from counts["headline"] —
    # never fold unowned/unconfirmed into a direction. The header still shows
    # ALL FIVE headline buckets (F-56: identical numbers on every surface —
    # the split changes which ROWS render here, never the numbers). "on your
    # plate" = headline["you_owe"] — the My Plate chat renders those rows.
    # Omit a zero segment (never pad); omit stuck when headline has no key.
    "header": f"Waiting On — {n_owed_to_you} waiting on others · {n_you_owe} on your plate (see My Plate) · {n_unowned} unowned · {n_unconfirmed} unconfirmed · {n_total} total open · {n_stuck} stuck",
    "sections": sections,
    "quick_read": quick_read,           # 1-3 sentences when N>2 and clustering signal exists
}

transport = render_and_persist(data_view=data_view, wrapper="fragment",
                               persist_dir="<WORKSPACE>/_hq/.system/widgets",
                               name_hint="waiting-on")
# EW2+T (F-15): the transport runs the full validator chain (canonical
# actions, data shape, leak scan, wrapper contract) and persists the sealed
# render. Pass transport["html"] to mcp__visualize__show_widget as widget_code (persisted page bytes, verbatim) — never
# a hand-composed variant, never a post-processed one.
```

The widget posts via `mcp__visualize__show_widget` instead of being a chat string. User clicks per-item buttons to select actions; widget batches selections and fires one consolidated `apply choices: [...]` payload on Apply all. The `apply-choices` skill catches that payload and dispatches each `{n, action}` through the reply handlers below. See `shared/CHAT_ACTION_WIDGET.md` for full widget behavior, `skills/apply-choices/SKILL.md` for the receiving end.

**First-Run Personalization (SPEC FRP1) — the commitments surface's two knobs.** This orchestrator
reads its presentation knobs via `get_config(WORKSPACE, "commitments", DEFAULTS)` where:

```python
DEFAULTS = {
    "group_by": "person",      # person (group waiting-on rows by the person who owes) | project
    "chase_tone": "friendly",  # friendly | direct (the status-draft register My Plate's Phase 7 twin reads)
}
```

`group_by` drives the Phase 4 grouping; `chase_tone` is read by orchestrator-my-plate.md for its
status drafts (CTS1: the config store stays under the `"commitments"` key — ONE knob set for the
commitment family, both surfaces read it; a re-keyed store would orphan every already-configured
workspace). On the FIRST fire only (`not is_configured(WORKSPACE, "commitments")`):
`save_skill_config(WORKSPACE, "commitments", DEFAULTS)` BEFORE rendering, then append a
**"Make this yours"** section at the BOTTOM of `sections` carrying two fr-items — `fr1` group-by
(person/project) and `fr2` chase-tone (friendly/direct) — each rendered as the documented
current-state fixed-option row (the fr-item preselect exception in `shared/CHAT_ACTION_WIDGET.md`).
A tap emits `{n:"fr1", action, sub?}` → apply-choices routes it to `save_skill_config(WORKSPACE,
"commitments", cfg, is_reconfigure=True, origin="first_fire_override")`. The "Make this yours"
section renders exactly once ever (`is_configured` gate) — and only on ONE surface: this chat owns
the fr-items; My Plate never re-renders them. Freeform tune ("group by project" / "chase people
more directly") maps to the same two keys. Cadence/timing of this scheduled fire is
`change-schedule`, not tune.

**Step 3 — Post the chat-links section (v2.12.0+):**

After posting the widget, emit a second chat turn with markdown source links per commitment item. Format per `shared/CHAT_ACTION_WIDGET.md` § "Post-widget chat-links section":

```markdown
**Links:**

1. [<Recipient> — <subject>](<connector-returned thread URL, else connector_adapters.mail.deep_link(provider, thread_id)>)
2. (no source — Self-commitment)
3. [<Granola transcript title> — <date>](https://notes.granola.ai/d/<note_id>)
...
```

- Numbering matches the widget items exactly.
- Source URL per commitment item:
  - Mail-sourced (email provider prefix on `data.source_ref` / mail provenance) → the mail thread URL
  - If `data.source_ref` starts with `granola:` → Granola transcript URL
  - Self-commitments / undated items with no `source_ref` → render `(no source — <Self-commitment | undated>)` or skip
- Use URLs returned by the connector (thread fetch, `get_meeting_transcript`). Don't synthesize (`connector_adapters.mail.deep_link` is the only sanctioned fallback; it returns None for providers with no stable host — drop the link, N8).
- If 0 items have any source link, omit the block.

Per Sam's Apr 30 ask: *"this needs to be a response to an email. I'd want to see the link to the most recent email on the subject so you can click it and respond."* For commitments where the user wants to respond on the original thread (instead of the new draft this orchestrator generated), the chat-link gives one-click access to Gmail.

**Original-thread block (v2.12.1+; v2.14.36+ MANDATORY for every email-shaped commitment with a source_ref):** populate the `original_thread` field on the item dict with the source thread snippet. The renderer wraps it in a collapsible `<details>` block above the draft so the user can expand to see the original message they're following up on. Mirrors the inbox-triage `original_thread` block one-for-one — the same accordion + ↗ Open in Gmail link the user already knows from inbox.

Pre-v2.14.36 the orchestrator was emitting an inline `("Originally", "<plain text>")` metadata key as a fallback when `original_thread` wasn't populated (or in addition to it). M's 2026-05-07 testing surfaced the gap: half the commitment items showed the rich collapsible (Dustin Sample / DocuSeal example), the other half showed the plain inline "Originally: the Apr 27 thread" pointer with no expand-to-read and no Gmail link. v2.14.36 hard-contracts the rich pattern for every item: ALWAYS populate `original_thread` when the commitment has any traceable origin. The inline `("Originally", ...)` metadata key is DROPPED from the v2.14.36+ item shapes (renderer also defensively skips it now if orchestrator still emits it).

Shape:

```python
"original_thread": {
    "author": "<sender display name + email>",
    "date": "<localized timestamp of latest message>",
    "subject": "<latest message subject>",
    "body": "",                                                    # v3.13.7+ — DEFAULT EMPTY (was ~800 chars).
                                                                   # See "Body field is empty by default" note below.
    "url": "<connector-returned thread URL, else connector_adapters.mail.deep_link(provider, thread_id)>",    # v2.12.4+ — REQUIRED when known
}
```

**The `url` field is REQUIRED whenever the thread URL is available** (v2.12.4+). The renderer adds a prominent "↗ Open in Gmail" / "↗ Open in Granola" link at the top of the expanded original-thread block. Per M's Apr 30 ask: *"we need to make the April 26 thread that links you to google mail more visible as a link."*

**Body field is empty by default (v3.13.7+ — Bug #14 transmission-ceiling fix).** Pre-v3.13.7 orchestrators embedded the first ~800 chars of the thread body inside the widget. That averaged 3-4KB per item and broke the widget transmission ceiling on workspaces with >7 surfaced commitments. v3.13.7 inverts the default: `body` is empty string, and the accordion summary + the "↗ Open in Gmail" / "↗ Open in Granola" link give the user one-click access to the full thread in its native UI (where they were going to read it anyway — text reflow + replies + attachments all live there, not in a 800-char preview). If the thread URL is unavailable for some reason (rare — deleted thread, permission error), populate body with `"(thread body unavailable — open in Gmail to read)"` so the user has a hint, but still set url so the click-through works. This keeps the per-item widget size around 2-3KB instead of 5-9KB, allowing 7+ items to fit under the transmission ceiling.

For Granola-sourced commitments (`source_ref` starts with `granola:`), the equivalent is the transcript snippet — populate `original_thread` with `author = attendee names`, `date = meeting date`, `subject = meeting title`, `body = ""`, `url = https://notes.granola.ai/d/<note_id>`. Body stays empty for the same transmission-size reason; the Granola URL opens the full transcript.

Self-commitments and undated items genuinely have no source thread → no `original_thread` field, no `<details>` block. The chat output's "Sources" section at the end of the turn carries the `(self-commitment, logged <date>)` provenance instead of polluting every item with a content-empty Originally line.

(CTS1: the YOU OWE and Self-commitment per-item shapes moved to `orchestrator-my-plate.md` with the rows they describe.)

**Per-item shape, WAITING ON:**

```python
{
    "n": 6,
    "name": "Bo",                       # the person who owes M
    "subject": "Updated NetSuite mapping",
    "context_tag": "committed by Bo Apr 18, 10 days overdue · firmer",   # tier suffix per Phase 5
    "original_thread": {                                # v2.14.36+ MANDATORY when source_ref exists
        "author": "Bo Sample <bo@example.com>",
        "date": "Apr 18, 2:11 PM",
        "subject": "NetSuite mapping",
        "body": "I'll send the updated mapping by end of next week — pulling in the new product hierarchy first...",
        "url": "<the connector-returned thread URL>",
    },
    "metadata": [                                       # v2.14.36+ DROPPED ("Originally", ...) — original_thread carries it
        ("To", "bo@example.com"),
        ("Subject", "NetSuite mapping: timing"),   # dash-free (S3 subject gate)
    ],
    "body_lines": [...],
    "actions": ["6 send", "6 draft", "6 follow-up call", "6 mark received", "6 escalate to memo", "6 snooze 3d", "6 add to my plate"],  # v2.14.38+ — standardized deferral cluster (replaces `skip`).
}
```

**Grouped item (multiple commitments from one owner — renders with sub_items):**

```python
{
    "n": 7,
    "name": "Sam",
    "subject": "3 things overdue",
    "context_tag": "oldest committed Apr 8, 20 days overdue · firmer",
    "metadata": [("To", "sam@example.com"), ("Subject", "Quick check on a few things")],
    "body_lines": [...],                    # grouped chase draft (lists the items in the EMAIL going to Sam)
    "sub_items": [
        {"id": "7a", "summary": "Q2 deck refresh (committed Apr 8)", "actions": ["7a mark received", "7a not relevant", "7a add to my plate"]},
        {"id": "7b", "summary": "Margin model (committed Apr 12)", "actions": ["7b mark received", "7b not relevant", "7b add to my plate"]},
        {"id": "7c", "summary": "Partner-tier proposal (committed Apr 18)", "actions": ["7c mark received", "7c not relevant", "7c add to my plate"]},
    ],
    "actions": ["7 send", "7 draft", "7 mark received all", "7 snooze 3d", "7 add to my plate"],  # v2.14.38+ — parent gets the daily-loop deferral cluster; sub-items get a tighter set (mark received / not relevant / add to my plate — `snooze 3d` per sub-item is noisy when the parent email is the action).
}
```

**Sub-item rendering (v2.12.4+ — visible summary, terse, mapped to email body):**

The `summary` field on each sub-item now **renders as visible text** next to the action row, per M's Apr 30 ask: *"not sure why there are 6a/b/c/d/e."* Without the summary visible, the user can't tell which numbered item in the email body each sub-letter corresponds to.

Rule: each sub-item's `summary` MUST be a terse, distinctive label that maps 1:1 to the corresponding numbered line in `body_lines`. Use the same noun phrase the email body uses, abbreviated. Example: if the email body has `1. Screenshot + recap email of the cloud migration proposal`, the sub-item summary is `Recap email + screenshot` (matches at first glance, no rereading needed).

Sub-items render as:

```
7a · Recap email + screenshot               [Mark received] [Not relevant] [Add to My Plate]
7b · Software license list                  [Mark received] [Not relevant] [Add to My Plate]
7c · Document mgmt double-check (Quinn)      [Mark received] [Not relevant] [Add to My Plate]
7d · Lyra / RPC setup                       [Mark received] [Not relevant] [Add to My Plate]
7e · Cloud-side pricing CC'd to all 3       [Mark received] [Not relevant] [Add to My Plate]
```

(v2.10.9–v2.12.3 hid the summary; v2.12.4 brings it back. The earlier "redundant duplication" concern was about repeating LONG bullet text below an email body that already had it; the v2.12.4 fix is to make summaries TERSE — single noun phrase, ≤ 8 words — so they're scannable shortcuts, not duplicate prose.)

**Pre-build resolution rules (your job, BEFORE calling renderer):**
- Resolve every `person_NNN` / `org_NNN` to canonical name in name + body_lines + context_tag — and the name is the RESOLVED record's spelling, never a transcript/ASR spelling (F-50 P2b; `shared/ENTITY_RESOLVE_PROTOCOL.md` § Display names)
- Subject fallback per Rule 12 — never ship blank subjects
- Every OUTBOUND draft subject passes the subject voice gate (v4.6.1 S3): `printf '%s' "$SUBJECT" | python3 "$PLUGIN_ROOT/shared/scripts/voice_tell_detector.py" - --context subject` — no dashes as punctuation, no banned phrases, no vocabulary words (F-47 P2d's em-dash subject shipped from THIS surface). The inbound `original_thread.subject` stays verbatim — never rewrite the sender's own subject.
- Source thread → `original_thread` field (v2.14.36+ MANDATORY when `source_ref` exists). Mirror the inbox-triage pattern. Drop the legacy inline `("Originally", ...)` metadata key — the renderer no longer paints it.
- Grouping applies only when one owner owes multiple things (Phase 4) — never invent groups across owners

**No example rendered output is included by design (v2.10.8+).** Read `shared/scripts/chat_output_renderer.py` if you need to understand the output format. Execute the transport (`render_and_persist`); relay its page bytes (`transport["html"]`) as `widget_code` — the persisted render is sealed.

**Required visual structure for every email-shaped item (v2.14.36+ HARD CONTRACT — original_thread accordion replaces the legacy `Originally:` line):**

Pre-v2.14.36 the spec required a clickable `Originally: [link](url)` line above the draft. M's 2026-05-07 testing surfaced the gap: half the items used the rich collapsible accordion (matching inbox), half used the legacy plain `Originally: the Apr 27 thread` pointer. The split-personality UI made commitments look broken next to inbox.

v2.14.36 hard-locks the rich pattern: every email-shaped item with a `source_ref` populates `original_thread` (the same collapsible block inbox uses), and the renderer renders the accordion above the draft blockquote. The legacy inline `Originally:` line is GONE — orchestrators no longer emit it, renderer no longer paints it. Self-commitments / source-less items have no accordion (the chat-turn `Sources:` section carries provenance instead).

```
N. [First-line summary with attendee + topic + aging tag]

   ▼ Original thread — <Sender> · <date>
       ↗ Open in Gmail
       Subject: <original subject>
       <first ~800 chars of original body>

   > **To:** [recipient@example.com](mailto:recipient@example.com)
   > **Subject:** real subject — never empty, fallback per Rule 12
   >
   > Draft body line 1, paragraph breaks preserved with blank `>` lines.
   >
   > Draft body line N
   >
   > Sign-off
   > Mira

   ▸ N verb1  ▸ N verb2  ▸ N verb3
```

**Format rules (per `_hq/CONVENTIONS_EMAIL_PREVIEW.md`):**
- Two header lines only (`To:`, `Subject:`) inside the blockquote, both bolded.
- No `Body:` label. Body starts on the line after Subject, separated by one blank `>` line.
- Recipient as mailto markdown link.
- Cc (if any) on its own line between To and Subject, also bolded: `**Cc:** [name@example.com](mailto:name@example.com)`.
- Body verbatim — no italics wrapping, no reflow.
- Blank `>` lines preserve paragraph breaks inside the blockquote.

Blank line BEFORE the blockquote (separates metadata from draft). Blank line BEFORE the `▸` pill row (separates draft from actions). Blank line BETWEEN items. The whitespace IS the page break — visually identifies each item as discrete.

**Sources section at the end of the chat turn (per `_hq/CONVENTIONS_SOURCE_LINKS.md`):**

After all items render, append a `Sources:` section listing every connector source referenced — Gmail threads, Granola transcripts, Drive docs. One bullet per source. Markdown links only, prefer URLs from MCP tool results. If no connector sources were referenced (rare), omit the section.

**Action surface (v2.10.9+ — all-batch button widget per `shared/CHAT_ACTION_WIDGET.md`):**

The action surface is a `show_widget`-rendered button group per item, with per-item selections accumulating in widget local state and one "Apply all" submission at the end. The Apply submission fires `apply choices: [...]` as one consolidated `sendPrompt`, which the `apply-choices` skill catches and dispatches through the reply handlers below.

**Action sets (the buttons rendered in the widget, v2.14.38+ — standardized deferral cluster; CTS1: this chat renders the waiting-on clusters only — the owner-me clusters live in orchestrator-my-plate.md):**

WAITING ON actions (chase-eligible rows): `send`, `draft`, `follow-up call`, `mark received`, `escalate to memo`, `snooze 3d`, `add to my plate`. `edit then send` retired (FB-17 — inline body edits). Skip removed v2.14.38; `snooze 3d` + `add to my plate` replace it.

Delegated-task rows (owner ≠ M, effective kind `task` — CTS1 §2.3): `nudge` (WG1-A D-A4 — manual chase, composed on CLICK at dispatch, no pre-staged text), `mark received`, `snooze 3d`, `add to my plate`. No owner email on file → `nudge` degrades to `add email then send` (the driver's Bug #44 degrade — the other three verbs stay).

Display labels come from `shared/scripts/verb_taxonomy.py` (F-59 — never restate them locally): `resolved` displays **Done**, `push to [date]` displays **Later…** (t3 FB-3), the rest per their taxonomy rows. Rendering (t3 FB-4, FB-17): email-shaped rows show **Send** / **Draft** / **Snooze (3 days)** one-tap buttons; commitment rows show **Done**; the tail sits in the row's `— more —` dropdown. Rows carrying `push to [date]` have their separate snooze dropdown option suppressed (the FB-3 merge — Later… covers it). Chat prose under the widget names ONLY the controls the card visibly shows, by their exact labels (t3 FB-11).

Action semantics:

- `send` — send the current draft as-is. No edit step. (Body edits happen directly on the card, t3 FB-10; `edit then send` is retired per FB-17 — never emit it, its wire id survives only as an in-flight deprecated alias → `send`.)
- `draft` (consolidated v2.14.4+) — one-tap button (t3 FB-4); the card body is the edit surface (t3 FB-10). Apply saves to the declared backend's Drafts. (Chase-eligible email rows only — delegated-task rows carry `nudge`, not `draft`, since WG1-A D-A4; a persisted pre-train widget's `draft` click on a delegated row still dispatches through apply-choices' consolidated handler.)
- `nudge` (WG1-A D-A4) — the delegated row's manual chase, composed on CLICK: apply-choices runs the email-writer chain at dispatch (draft posture — never auto-sent); nothing is composed at fire time. Requires the row's `To:` metadata (the driver resolves the owner's email; no email → the row carries `add email then send` instead, never a dead nudge button).
- `follow-up call` — drafts a calendar-invite request for a quick 15-min sync.
- `mark received` — mark the commitment as fulfilled by the counterparty. Writes `thread_resolved` event for that commitment id.
- `escalate to memo` — fire memo-writer skill, generates a longer-form `.docx` memo when the conversation has gotten complex enough that a quick reply isn't appropriate.
- `snooze 3d` (v2.14.38+) — fixed 3-day snooze. Writes `chat_dismissal` event with `data.snooze_until: <today + 3d>`. Item won't re-surface until the date passes. No textarea — fixed duration per M's standardization.
- `add to my plate` — dispatch `commitment_state.create_personal_task` (owner-me `task`, status `created`). It lands on My Plate. (`add to my list` is RETIRED — MLK1, 2026-07-21; no row emits it. A persisted old widget's click still dispatches through apply-choices with its original meaning.)

(`prep deep work`, `push to [date]`/Later… as a due-date shift, and `resolved` on the user's own items are My Plate semantics now — see orchestrator-my-plate.md. A `push to [date]` typed HERE on an owed-to-M row still auto-routes per t3 FB-3: it lands as a snooze, never a rewrite of a date the counterparty owns.)

(Removed in v2.12.0: standalone `edit [change]`. Removed in v2.12.1: `edit firmer` / `edit softer`. Removed in v2.12.2: standalone `edit` — combined `edit then send` / `draft` (consolidated v2.14.4+; was previously two separate verbs) replace it so editing always pairs with a disposition in one step.)

For grouped items: each sub_item (`7a`, `7b`, `7c`) gets its own `mark received` / `not relevant` / `add to my plate` button row inside the parent's group, plus the parent-level `mark received all`.


# Phase 10 — Failure handling

Per Rule 8: never expose internals. Standard: voice calibration unreadable → neutral professional tone fallback, surface inline note ("(Voice calibration unreadable — using neutral tone for now.)"). Connector flake → degrade gracefully, log to `errors[]` in pack_run event. No silent retries.

If Gmail MCP rate limits during email-writer runs: write what you have, surface "(N drafts pending — retry with `re-run`.)" Do not block the chat turn waiting for retries.

# Reply handling

Parse `N action` (with or without period). (CTS1: the owner-me handler set — `prep deep work`, `push to [date]` as a due shift, `resolved` on the user's own items, `keep` — lives in orchestrator-my-plate.md; a typed owner-me verb landing in THIS chat still dispatches correctly because apply-choices routes on the row's embedded `data.id` and ownership, not on which chat the click came from.)

## WAITING ON actions

- `N send` → per `EMAIL_DRAFT_PROTOCOL.md` §3c "Dispatch" + "Zapier param contract", dispatch in priority order: **Zapier first if configured** (build payload via `shared/scripts/zapier_send.py` `extract_latest_message_id` + `build_zapier_send_payload` — the `thread_id` param wants the LATEST MESSAGE ID, not the Gmail thread-level ID, per the v2.14.38+ contract), **native Gmail threaded** as fallback (per §3a), **standalone** as last resort. Confirm `✓ Sent (threaded) at HH:MM` (Zapier) or `✓ Sent to [name] at HH:MM` (native). Write `outreach_sent` event with `via` field set to the path used.
- `N edit then send` *(retired FB-17 — deprecated alias, accepted ONLY from in-flight widgets, never emitted anew)* → replace body with input, then send via `N send` handler.
- `N draft` (consolidated v2.14.4+) → widget exposes textarea pre-populated; user edits; Apply saves to Gmail Drafts.
- `N nudge` (WG1-A D-A4, delegated rows) → apply-choices' `nudge` handler: compose the chase through the email-writer chain at dispatch (draft posture — never auto-sent), using the row's item + counterparty context; no email on file at click time → the `add email then send` recovery flow, never an error.
- `N follow-up call` → drafts a calendar-invite request via email-writer ("Quick 15 min sometime this week?"). If Calendar MCP supports tentative invite, create that too. Stage email draft.
- `N mark received` (singleton) → close through `commitment_state.close_commitment(workspace_root, <item's data.id verbatim>, resolved_by=<user person_id>, evidence="counterparty delivered — marked received", source_skill="commitments", user_confirmed=True)` (Stage B — supersedes the bare `thread_resolved` write; the canonical `commitment_resolved` shape is what every consumer's closure filter reads first). Confirm. If it returns `already_resolved`, write NOTHING and say so plainly ("that one was already marked received earlier") — never append a second closure (v4.5.2 R1c).
- `N mark received all` (grouped) → same close_commitment call per sub-item (batch via `commitment_state.close_commitments`). Suppress the parent.
- `N escalate to memo` → fire memo-writer through standard chat invocation. Memo-writer produces .docx via the docx skill and surfaces the link the standard Cowork way. Do NOT emit `file://` links yourself. Re-prompt in plain English: "Want to send this as the email body, attach it to the reply, or send it standalone?"
- `N snooze 3d` (v2.14.38+) → `chat_dismissal` with 3-day TTL.
- `N add to my plate` → `commitment_state.create_personal_task` (owner-me `task` — it lands on My Plate).

## Sub-item actions (grouped items, v2.14.38+)

For `7a`, `7b`, `7c` style sub-items inside a grouped chase email:

- `Na mark received` → write `thread_resolved` for that specific commitment id. Update parent's chase draft to drop the now-received item.
- `Na not relevant` → write `chat_dismissal` with `data.target_id: <sub-commitment-id>`, `data.reason: "not_relevant"`, 60-day TTL. Sub-commitment suppressed for 60 days; parent stays open if other sub-items remain.
- `Na add to my plate` → `commitment_state.create_personal_task` for that sub-item (owner-me `task` — it lands on My Plate).

## Lifecycle corrections (v4.6.0 S4 — typed chat phrases, both directions)

Three correction verbs for captures that landed WRONG, all registered in `verb_taxonomy` and dispatched through `commitment_state` (apply-choices § commitment-triage documents the exact calls — same handlers here):

- `N fix wording: <text>` / "that should say <text>" → `commitment_state.edit_commitment_wording(workspace_root, <item's data.id verbatim>, new_summary=<text>, edited_by=<user person_id>, source_skill="commitments")`. The item re-renders with the corrected text on every surface; the original stays in history. Confirm with the corrected line, e.g. `✓ Fixed — "send Mira the positioning brief".`
- `N reassign to [name]` / "that's actually [name]'s" → resolve the name via the standard entity path (ambiguous → ask in one line, never guess), then `commitment_state.reassign_commitment(workspace_root, <id>, new_owner_id=<resolved person_id>, new_owner_name=<display name>, reassigned_by=<user person_id>, reason="user reassigned", source_skill="commitments", confirmed=True)`. The item leaves the user's you-owe and lands on the named owner — `not mine` DISCARDS, reassign ROUTES. Confirm: `✓ Routed to [name] — off your list.` (Unconfirmed/inferred reassignments — never from a typed name — stay in the unconfirmed bucket and are NEVER chased; no auto-email on a guessed owner.)
- `N split into: A / B / C` (2+ parts, separated by newlines / semicolons / " / ") → `commitment_state.split_commitment(workspace_root, <id>, [{"title": ...}, ...], split_by=<user person_id>, source_skill="commitments", user_confirmed=True)`. Each part becomes its own commitment carrying the original's provenance; the original closes with a "split into …" note. Confirm by naming the N new items. Extraction pre-split stays the doctrine (M decision 2026-07-09) — this is the manual correction path, never an extraction substitute. A parent with open sub-items refuses to split (ValueError — surface its message verbatim; the parts belong to ONE deliverable).

## Sub-items (SUB1 — decomposition; the parent stays open)

- `N add subitems: A / B / C` / "break #N into: …" / "steps for #N: …" (1+ parts, same newline / semicolon / " / " parsing) → `commitment_state.add_subitems(workspace_root, <parent data.id verbatim>, [{"title": ...}, ...], added_by=<user person_id>, source_skill="commitments", user_confirmed=True)`. The OPPOSITE of split: the parent STAYS OPEN as the one commitment of record; the parts become child commitments nested under it. Confirm by naming the steps and the still-open parent. Cap 12 open children (loud writer error — surface verbatim); one level deep; USER-INITIATED only (extraction/sweeps never mint hierarchies).
- Closing a child: plain `close_commitment` on the child's `data.id` — zero special-casing. When the LAST open child closes, the parent's next render carries "all sub-items done — close it?" — a PROPOSE; never auto-close the parent.
- `N resolved` / "close #N" where N is a parent with open sub-items → `close_commitment` raises `OpenSubitemsError`: ask the one-line confirm ("this also closes its N open sub-items — go ahead?"), and only on yes re-dispatch with `close_subitems=True, user_confirmed=True`. Cache the cascade's returned `closed_subitems` ids with the batch's undo set (an undo reopens the whole family, per-item). Never resolve a bare ordinal to a child silently.
- Programmatic closes (reconcile-sent, CRU auto-resolve) NEVER cascade — the matchers already downgrade a parent-with-open-children to a `pending_review` proposal (`cru_match.parent_blocks_auto_resolve`); render the proposal, don't close.

## Confirm section actions (v4.6.1 W4b — "Needs a quick confirm" rows; apply-choices dispatches these on this orchestrator's `src`)

All writes through the canonical helpers — never hand-built appends. Every handler is TERMINAL (state change + one plain-English ack line; no widget re-render, no draft):

- `N mine` → `commitment_state.confirm_commitment_owner(workspace_root, <row's data.id verbatim>, owner_id=<user person_id>, confirmed_by=<user person_id>, source_skill="commitments")`. Ownership folds to the user and the confirm flag clears — the item joins you-owe on the next fire. Ack: `✓ Yours — [title].`
- `N theirs to [name]` → resolve the name via the standard entity path (aliases first; ambiguous → ask in one line, never guess), then `commitment_state.reassign_commitment(workspace_root, <id>, new_owner_id=<resolved person_id>, new_owner_name=<display name>, reassigned_by=<user person_id>, reason="confirmed: theirs", source_skill="commitments", confirmed=True)` — the tapped name IS the explicit confirmation (S4's `reassign to [name]` is the chat-phrase twin). Ack: `✓ Routed to [name].` An unresolvable name → item-level error in the ack, nothing written.
- `N make task` / `N drop` → the same dispatches as commitment-triage (`promote_task_to_commitment(new_kind="task")` / `close_commitment(resolution="dropped", user_confirmed=True)`) — the explicit click adjudicates a pending_review row.
- `N merge` (duplicate rows) → `commitment_state.supersede_commitment(workspace_root, survivor_id=<the row's suspected_duplicate_of target verbatim>, superseded_id=<the row's data.id>, merged_by=<user person_id>, source_skill="commitments", evidence="user merged from the confirm section", user_confirmed=True)`. Honor `already_resolved` as a NO-OP with an honest ack. Ack: `✓ Merged — one item now, both sources kept.`
- `N keep both` (duplicate rows) → `commitment_state.clear_review_flags(workspace_root, <id>, cleared_by=<user person_id>, source_skill="commitments")`. Ack: `✓ Kept both — they're separate items.`
- `N promote` (promotion-proposal rows) → `promote_task_to_commitment(workspace_root, <id>, new_kind="promise", source_skill="commitments", reason="counterparty appeared — user promoted from the confirm section")`. Ack names the counterparty it will now be tracked against.
- **Unknown-person rows** (the proposal's event seq is the row id; every branch ENDS with the tombstone — `confirm_flow.build_person_proposal_resolved_event(proposal_seq=<seq>, resolution=<...>, source_skill="commitments", ...)` appended via `event_gate.append_event` — the tombstone is what stops the daily re-surface, never skip it):
  - `N add person` → apply-choices Step 3a (dedup-first `people_writer` path; `MultipleCandidatesError` → disambiguation widget), then the tombstone with `resolution="person_added"` + the new `person_id`. Ack: `✓ Added [name].`
  - `N same as [existing]` → resolve the typed name via the standard entity path (ambiguous → disambiguation widget, never guess), then `people_writer.add_person_alias(workspace_root, <resolved person_id>, <the proposal's raw name>, source_skill="commitments")` — the permanent resolution improvement (aliases.json mapping + the person record; future captures of that spelling resolve correctly, the F-13 P2b/F-56 fix) — then the tombstone with `resolution="same_as"` + person_id + alias. If `add_person_alias` raises the already-mapped-elsewhere conflict, surface it plainly and write NO tombstone (the proposal stays open for a human decision). Ack: `✓ Saved — "[raw name]" is [canonical name].`
  - `N proposal not relevant` → the tombstone with `resolution="not_relevant"`, nothing else written. Permanent (not a timed mute). Ack: counted in the consolidated line, no per-item callout.

"show muted" / "show snoozed" in this chat → the mute ledger (show-my-list's ledger mode): every live snooze with its remaining time and an Unmute action.

## Bulk actions (canonical set only — v2.14.19+ retired non-canonical "show all open" / "show all overdue" navigation buttons)

- `send all` → sequential sends across all non-noise items.
- `to drafts all` → bulk save all current drafts to mail Drafts folder.
- `show more` → re-render with the expanded list (up to 25 items across all buckets — overdue, due_near, aging_undated combined). Replaces the v2.14.18-and-earlier `show all open` + `show all overdue` split, which were non-canonical and not in `CANONICAL_ACTIONS`.
- `skip all` → bulk dismissal events.

For unrecognized → respond in plain English: "Reply with the item number + action — e.g., `3 send`, `7 mark received a`, `4 push to Friday`. Or `send all` / `show more` / `skip all`."

# What this orchestrator does NOT do

- Does NOT auto-send anything (every send is M's explicit action).
- Does NOT modify entities.json directly (people-crm canonical writer).
- Does NOT create nested Gmail labels (flat `cr-staged-[date]` only).
- Does NOT write `draft_created` events at fire time (lazy creation per EMAIL_DRAFT_PROTOCOL).

---

# Appendix — `prep deep work` context-loaded prompt template

(CTS1: `prep deep work` is a My Plate verb now — the template stays in THIS file because orchestrator-my-plate.md references it here, the tombstoned orchestrator-commitment-nudge.md points here, and moving canonical reference content breaks in-flight pointers. My Plate reads it at dispatch.)

For owner-me items where M replies `N prep deep work` (formerly `N work on it`, renamed v2.12.0), generate this prompt in chat (copy-paste-ready):

```
📋 **This is a prompt — not Claude replying to you.**

Copy the block between the lines and paste it into a NEW chat. Claude will
load the full project context and pick up the work where this commitment
left off.

────────────────────────────────────────────────────────────
go [project name]

Working session: [commitment title]

Context:
  - Committed on [date] to [recipient name] ([email if any])
  - Originally promised: "[exact phrasing from the original message]"
  - Originally due: [date]. Now: [N] days overdue.
  - Deliverable type: [inferred from title — e.g. "deck" / "memo" /
    "one-pager" / "status doc" / "data refresh"]

Recent context for this project (last 7 days):
  - [event 1: type, summary, date]
  - [event 2: ...]
  - [event 3: ...]

Open threads / blockers (if any):
  - [thread/blocker if found]

What I want:
  - Produce the [deliverable type], voice-calibrated to me
  - Recommend invoking [relevant skill — e.g. one-pager-composer /
    memo-writer / docx skill — based on deliverable type]
  - Save output to [project folder]/deliverables/ as .docx

When complete:
  - Mark the original commitment resolved (write commitment_resolved
    event to events.jsonl, ref id [commitment_id])
  - Optionally send to [recipient name] via Gmail (or stage to drafts
    for review)
────────────────────────────────────────────────────────────
```

The prompt is generated using (internal mechanics — not surfaced in chat):
- Commitment record from events.jsonl (id, title, owner, requester, due, source_ref, original phrasing)
- Project context from entities.json (name, deliverables folder path)
- Last 7 days of events.jsonl entries with `primary_thread_id == this project`
- Inferred skill based on deliverable type heuristic
