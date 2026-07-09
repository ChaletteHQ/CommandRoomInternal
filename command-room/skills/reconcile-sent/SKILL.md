---
name: reconcile-sent
description: "Silent scheduled maintenance task (3x weekdays) with three write jobs: close commitments the CEO completed by sending email directly from Gmail outside the product's draft path; watch earlier sends for terminal outcomes (replied / no reply / bounced) as learning signal; and persist mid-confidence matches for one-click confirm in the next Commitments chat. Runs from the SILENT_TASKS registry — no widget, no chat surface; its work appears as receipts and items quietly checked off. Manual fire: 'reconcile my sent mail'. Honors pending-review flags (never auto-resolves them) and keeps its single-responsibility isolation — nothing else folds into this task. Does NOT fire on 'follow up' phrasings (follow-up-ritual / email-writer) or 'scan for commitments' (scan-for-commitments — historic bulk extraction). Matching paths and cursor mechanics: Routing section in the body."
---

# Reconcile Sent — silent commitment reconciliation

This task is all write-side work — three silent write jobs (commitment closures,
the email-outcome watch, the voice reconcile) and no visible deliverable to hide
behind. Reconciliation is write-side maintenance (cursor semantics + idempotent
closures); the morning brief is a read-mostly render. They were co-located and
the invisible write lost every time (Bug #98-v3). Here the writes ARE the job,
so there is nothing to deprioritize them against — the same reason the silent
Sunday `cleanup` task runs reliably.

## Skill Boundary (v2.1)

- **Use reconcile-sent for:** the silent background pass over Gmail Sent — closing already-handled commitments, watching tracked sends for outcomes, logging voice corrections. It renders nothing.
- **Use `inbox-triage` for:** inbound mail — triage, classification, reply drafts.
- **Use `morning-briefing` for:** the reader-facing brief; it reads the closures this task wrote.
- **Use `show-my-list` for:** viewing open commitments on demand.

## Writer Contract

Every write goes through a locked helper — never a hand-rolled append:

- `commitment_resolved` events + the `sent_reconcile` audit event + the `workspace.sent_reconcile_cursor` advance — via `reconcile_sent_commitments.reconcile_and_receipt` (one call, Step 3).
- Terminal `email_outcome` events (`replied` / `no_reply_7d` / `bounced`) — via `email_outcomes.watch_and_receipt` (Step 2b).
- Voice-correction appends under `_hq/voice/` — via `voice_corrections.reconcile_sent_against_snapshots` (Step 4b).

No other files touched; no view renders. Reads: Gmail Sent (discovered mail tool), `entities.json`, `events.jsonl`, `_hq/voice/draft-snapshots.jsonl`.

## When this runs
| Mode | Trigger |
|------|---------|
| **Scheduled (primary)** | the `reconcile-sent` scheduled task, ~6:45 AM weekdays — BEFORE the 7:00 morning brief, so the brief reads an already-reconciled substrate |
| **Manual (normal)** | "reconcile my sent mail", "reconcile sent" |
| **Manual (wide catch-up)** | "catch up my sent mail", "reconcile the last N days", "reconcile my backlog" — fetches a 30-day (or N-day) window regardless of the cursor, to clear backlog stranded behind a stale cursor (Bug #101) |

## Enforcement is on the EVENT, not a sentence (Bug #98-v3)

The v3.18.9 fix gated on a printed "reconciled…" line. That was gameable — the
model fed the matcher a curated message list and printed a truthful-looking line
without a real fetch. So the success criterion here is a **substrate artifact a
validator reads back**, never a narration:

- A real `sent_reconcile` audit event must land in `events.jsonl` carrying
  `cursor_from`, `cursor_to`, and `sent_scanned_count`.
- `validate_reconcile_ran` reads it back and confirms it's THIS run's audit.
- If you cannot show that event, **the task FAILED** — say so plainly; do not
  narrate a success.

## The job (do exactly this)

**Before any python snippet below (Rule 22):** resolve the plugin root and run every snippet from it — the cwd never persists and `shared/scripts` only resolves from the plugin root:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
```

1. **Determine the fetch window — and look BACK far enough to clear stranded backlog (Bug #101).** Read `workspace.sent_reconcile_cursor` from `entities.json` and hold it as `cursor_before` (you'll validate against it). **The cursor is NOT automatically trustworthy as a fetch floor:** a pre-v3.18.12 version could leave the cursor sitting ahead of mail it never actually reconciled (it claimed "reconciled up to here" while doing nothing), which strands every earlier sent message — `after:cursor` would never see it. So choose the fetch floor by mode:
   - **First real run on this workspace** — detect via `validate_reconcile_ran(workspace_root)["ran"] is False` (no prior `sent_reconcile` audit event exists) → fetch the **last 30 days** regardless of the cursor. This clears any backlog a stale cursor skipped past.
   - **Manual catch-up** ("catch up my sent mail" / "reconcile the last N days") → fetch the requested window (default **30 days**) regardless of the cursor.
   - **Normal scheduled run** (a prior audit event exists) → fetch `after:<cursor-date>` with a ~1-day overlap behind the cursor (cheap, idempotent, catches near-cursor stragglers).

   `reconcile_and_receipt` advances the cursor to the newest message in whatever window you fetched (never backwards), and matching is idempotent — a wide re-scan closes nothing twice, so an over-wide window is always safe.

2. **Do a REAL Gmail Sent fetch over the Step-1 window.** Gmail MCP `search_threads` / message list, query `in:sent after:<the floor date you chose in Step 1>` (30-day floor on a first run / catch-up; the cursor date on a normal run). This is the whole point of the task — do NOT reuse a message list pulled for some other purpose; that's the exact shortcut that gamed the old gate. For each outbound message resolve `recipient_person_ids` against `entities.json` people (by email), AND capture `recipient_names` — the recipients' display names and email local-parts (e.g. `["Sam", "sam"]` where `sam` is the local-part of the address; `["Jordan Lee", "jlee"]`). Build `sent_messages = [{message_id, ts, recipient_person_ids, recipient_names, subject, body}, ...]`. **`recipient_names` is load-bearing for recall (Bug #103):** commitment extraction frequently fails to link the counterparty into a commitment's `person_ids` (some "Send [Name] …" items are stored with only the user), or the counterparty has no email on file, so resolving by id alone misses real completions — the matcher falls back to finding the recipient's name in the commitment title. (Outlook: equivalent Sent query. No mail connector → emit nothing and exit clean; the brief's soften floor still protects the CEO.)

2b. **Outcome watch (v1) — silent (B6).** A second silent write on the same fire: detect whether earlier CR-sent emails got a reply. Co-locating two silent *writes* is fine — the Bug #98 anti-pattern was a silent write next to a *visible deliverable*, not two background writes. This runs BEFORE Step 3 so the single `sent_reconcile` audit event can carry the reply counts.

```python
import sys; sys.path.insert(0, "shared/scripts")
from email_outcomes import load_pending_tracked_sends, watch_and_receipt

pending = load_pending_tracked_sends("<abs workspace root>")   # email_sent in last 21d, no terminal outcome yet
to_check = pending[-25:]   # cap 25 per run, OLDEST first; backlog drains across mornings (idempotent)
```

   For each send in `to_check`, fetch its thread state from Gmail MCP — `get_thread` by the stored `data.gmail_thread_id` if present, else `search_threads` with query `rfc822msgid:<gmail_message_id>` to resolve the thread. Build `thread_states = {sent_event_seq: {"messages": [{"from", "ts", "message_id"}, ...]}}` — **metadata only, never reply bodies or subjects** (privacy). Then:

```python
summary = watch_and_receipt("<abs workspace root>", thread_states, source_skill="reconcile-sent")
```

   `watch_and_receipt` appends one terminal `email_outcome` event per resolved send (`replied` / `no_reply_7d` / `bounced`) and is idempotent (a re-run re-loads pending and the now-terminal sends are already excluded). Hold `summary` — Step 3 passes it into `reconcile_and_receipt(..., outcome_watch_summary=summary)` so the audit event records the reply counts. **Stay silent — produce no chat output for this phase;** the reply/latency patterns surface later through `insight-generator`'s Outcome-patterns pass, never here. Budget: ≤25 thread fetches + ≤25 `rfc822msgid:` fallback searches per fire; on connector-budget exhaustion, stop and leave the rest pending (idempotence makes that safe). A send with neither a stored thread id nor a resolvable `rfc822msgid:` is skipped and ages out of the 21-day window — never guess a thread by recipient+subject.

3. **Run the orchestrator — ONE call. It does the matching, the `commitment_resolved` writes, the cursor advance, AND emits the `sent_reconcile` audit event.**

```python
import sys; sys.path.insert(0, "shared/scripts")
from reconcile_sent_commitments import reconcile_and_receipt, validate_reconcile_ran
from primary_user import resolve_primary_user

user_id = resolve_primary_user("<abs workspace root>")   # deterministic — do NOT guess (Bug #102)
receipt = reconcile_and_receipt("<abs workspace root>", sent_messages,
                                user_person_id=user_id,
                                source_skill="reconcile-sent",
                                outcome_watch_summary=summary,   # from Step 2b
                                fired_via="scheduled")  # "manual" on a chat-phrase / Run Now fire (v4.5.2 receipt contract)
```
   **Resolve the user via `resolve_primary_user`, never by guessing** — on real workspaces the `is_primary_user` flag is often unset, so a hand-resolved user can come back wrong/None and the matcher's owner gate (`owner_id == user`) then matches nothing (Bug #102, the other half of why reconciliation closed 0).

4. **Self-validate against the event (mandatory).** Read the audit back and confirm it's this run's:

```python
v = validate_reconcile_ran("<abs workspace root>", since_cursor=<cursor_before>)
# v["ok"] must be True. v carries cursor_from/cursor_to/sent_scanned_count/n_closed.
```

   - `v["ok"] is True` → reconciliation genuinely ran. Done.
   - `v["ok"] is False` → you did NOT actually reconcile (no audit event, or it's a stale one). Do not report success. Re-run steps 2–3 with a real fetch, or report the failure plainly.

4b. **Voice reconcile (B1) — silent.** After validation, diff the FINAL sent bodies against the draft snapshots:

```python
import sys; sys.path.insert(0, "shared/scripts")
from voice_corrections import reconcile_sent_against_snapshots
reconcile_sent_against_snapshots("<abs workspace root>", sent_messages)   # reuses the Step-2 fetch; no second Gmail pass
```

   It matches each sent message to a `_hq/voice/draft-snapshots.jsonl` row (by `gmail_message_id`, else recipient + normalized subject + a 7-day window), classifies the drafted-vs-sent diff, and appends voice corrections. Silent and non-blocking — it never affects the commitment reconciliation receipt; on any error, continue.

5. **Surface only if something closed (otherwise stay silent — this is a background task).**
   - `receipt["resolved"]` non-empty → one line, delivered the same way other scheduled tasks deliver (Slack DM / email / saved note per the workspace's delivery preference): *"Closed N follow-ups you'd already sent — [titles]. Say `undo` to reopen any."*
   - `receipt["pending"]` non-empty → *"Did you already handle these? [title] — `mark done [n]`."*
   - Nothing closed → no output. Silence is correct; the audit event is the proof it ran.

## Self-heal
The FIRST fire on a workspace fetches a **30-day window regardless of the cursor**
(Step 1, Bug #101) — so it clears the entire accumulated backlog of already-sent
follow-ups, including mail sent BEFORE a stale cursor that an earlier broken
version left sitting ahead of un-reconciled messages. Zero prompt, zero user
action. After that first wide pass, normal runs go forward from the cursor. If a
workspace already had its first run under the cursor-only behavior (so the backlog
is still stranded), a one-time "catch up my sent mail" clears it.

## What it does NOT do
- It does not render a morning brief or a commitments view — the brief reads the closures this task wrote.
- It does not fetch inbound mail, triage, or draft replies — that's `inbox-triage`.
- It never advances the cursor without a real fetch behind it — the audit event + validator make that checkable.
- It does not surface reply/outcome patterns — Step 2b only WRITES the `email_outcome` events silently; `insight-generator`'s Outcome-patterns pass is the reader-facing surface.

## Reliability
Runs as a scheduled task and follows `shared/RELIABILITY.md`: skip-not-fail when the workspace isn't ready, 15s/60s connector budgets, no fabricated data when a connector is down (emit nothing and exit clean — the brief's `reconcile_stale` soften covers the gap).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Silent maintenance task with three silent write jobs: (1) close commitments the CEO completed by sending a follow-up DIRECTLY from Gmail (outside the product's draft->send path), (2) watch earlier tracked sends for reply/no-reply/bounce outcomes, (3) reconcile final sent bodies against draft snapshots for voice corrections. Fires as a scheduled task on weekday mornings (6:45 AM, before the morning brief) and can be run manually with 'reconcile my sent mail' / 'reconcile sent'. A wide one-time catch-up runs via 'catch up my sent mail' / 'reconcile the last N days' / 'reconcile my backlog' — fetches a 30-day window regardless of the cursor to clear a stranded backlog. The core pass: fetch Gmail Sent (since the cursor, or a 30-day window on a first run / catch-up), match against open commitments, write the closures + advance the cursor, and self-validate that the work actually landed; the outcome watch and voice reconcile ride the same fetch. It does NOT produce a reader-facing brief — the morning brief reads what this task wrote. This split exists because reconciliation is an invisible substrate write that loses to visible deliverables when co-located with them (Bug #98-v3: three folds into the brief/inbox were all skipped; the brief diagnosed the structural cause itself and recommended moving it out).
