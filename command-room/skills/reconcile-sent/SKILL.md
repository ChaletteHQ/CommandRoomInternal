---
name: reconcile-sent
description: "Silent scheduled maintenance task (3x weekdays) with four write jobs: close commitments the CEO completed by emailing outside the product's draft path; open a commitment when a sent reply carries an untracked promise; watch earlier sends for outcomes (replied / no reply / bounced); persist mid-confidence matches for one-click confirm in the next Waiting On chat. Runs as the first job inside the maintenance background task (MAINT1) — no widget, no chat surface. Manual fire: 'reconcile my sent mail'. Honors pending-review flags (never auto-resolves); reconciliation only, whatever task carries it. Does NOT fire on 'follow up' phrasings (follow-up-ritual / email-writer) or 'scan for commitments' (scan-for-commitments — historic bulk extraction). Matching paths and cursor mechanics: Routing section in the body."
---

# Reconcile Sent — silent commitment reconciliation

This task is all write-side work — four silent write jobs (commitment closures,
the sent-promise capture, the email-outcome watch, the voice reconcile) and no
visible deliverable to hide behind. Reconciliation is write-side maintenance (cursor semantics + idempotent
closures); the morning brief is a read-mostly render. They were co-located and
the invisible write lost every time (Bug #98-v3). Here the writes ARE the job,
so there is nothing to deprioritize them against — the same reason the silent
Sunday `cleanup` task runs reliably.

## Skill Boundary (v2.1)

- **Use reconcile-sent for:** the silent background pass over Gmail Sent — closing already-handled commitments, OPENING commitments from sent promises nothing tracks yet (v4.6.2), watching tracked sends for outcomes, logging voice corrections. It renders nothing.
- **Use `inbox-triage` for:** inbound mail — triage, classification, reply drafts. Its commitment extractor covers both directions but only ever sees unread-inbox threads (the unread + in-inbox intents) — a thread the CEO read and replied to the same day never reaches it. THIS task's sent-promise capture is the rescue path for exactly those outbound promises (the BUG-3719 class: a promise made in the CEO's own reply, in a thread triage never scanned, aging silently until the counterparty chases).
- **Use `morning-briefing` for:** the reader-facing brief; it reads the closures this task wrote.
- **Use `show-my-list` for:** viewing open commitments on demand.

## Writer Contract

Every write goes through a locked helper — never a hand-rolled append:

- `commitment_resolved` events + the `sent_reconcile` audit event + the `workspace.sent_reconcile_cursor` advance — via `reconcile_sent_commitments.reconcile_and_receipt` (one call, Step 3).
- NEW `commitment` opens (and `commitment_observed` set-asides) from the CEO's own sent promises — the SAME `reconcile_and_receipt` call (its `sent_commitment_items=` argument, Step 2c → Step 3), which routes them through `sent_capture.capture_sent_items` → the shared capture block (`capture_gate.gate_commitment_data`), the W4c relevance gate, restatement dedup vs the open set, and one locked `event_gate.append_event` batch.
- Terminal `email_outcome` events (`replied` / `no_reply_7d` / `bounced`) — via `email_outcomes.watch_and_receipt` (Step 2b).
- Voice-correction appends under `_hq/voice/` — via `voice_corrections.reconcile_sent_against_snapshots` (Step 4b).

No other files touched; no view renders. Reads: the declared mail backend's Sent folder (resolved via `tool_discovery.discover_for_category("email","in_sent",…, declared=connector_config.declared_backend("email"))` → `discover_mail_search_tool` fallback; the in-sent / from-me intents compile to provider operators in `connector_adapters/mail.py`, never named here), `entities.json`, `events.jsonl`, `_hq/voice/draft-snapshots.jsonl`.

**Account-scope (connector-agnostic-v1, R10 §7a).** Sent-mail scanning attributes commitments to the owner, so it runs ONLY against **classified in-scope** accounts. An `unclassified` account is excluded from the Sent pass entirely (fail-closed — never file personal sent mail as business commitments before classification). Superhuman's ~1-min post-send sync lag (capability manifest) is tolerated — a just-sent message may not appear yet; the over-wide window stays safe because of the BUG-3719 self-closure guard, which now keys on the **canonical dedup key** (`connector_adapters.provenance.canonical_dedup_key`) so it holds across legacy `gmail:<id>` and new structured provenance (R16).

## When this runs
| Mode | Trigger |
|------|---------|
| **Scheduled (primary)** | the FIRST job in the `maintenance` task's weekday fires (MAINT1) — due at every 6:45 AM / 12:45 PM / 5:45 PM weekday slot per `maintenance_dispatcher.due_jobs`; the 6:45 pass runs BEFORE the 7:00 morning brief, so the brief reads an already-reconciled substrate |
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

1. **Determine the fetch window — and look BACK far enough to clear stranded backlog (Bug #101).** Read `workspace.sent_reconcile_cursor` from `entities.json` and hold it as `cursor_before` (you'll validate against it). **The cursor is NOT automatically trustworthy as a fetch floor:** a pre-v3.18.12 version could leave the cursor sitting ahead of mail it never actually reconciled (it claimed "reconciled up to here" while doing nothing), which strands every earlier sent message — a fetch floored at the cursor would never see it. So choose the fetch floor by mode:
   - **First real run on this workspace** — detect via `validate_reconcile_ran(workspace_root)["ran"] is False` (no prior `sent_reconcile` audit event exists) → fetch the **last 30 days** regardless of the cursor. This clears any backlog a stale cursor skipped past.
   - **Manual catch-up** ("catch up my sent mail" / "reconcile the last N days") → fetch the requested window (default **30 days**) regardless of the cursor.
   - **Normal scheduled run** (a prior audit event exists) → fetch with the `{"in_sent": true, "after": <cursor-date>}` intent (compiled per provider by `connector_adapters/mail.py`) with a ~1-day overlap behind the cursor (cheap, idempotent, catches near-cursor stragglers).

   `reconcile_and_receipt` advances the cursor to the newest message in whatever window you fetched (never backwards), and matching is idempotent — a wide re-scan closes nothing twice, so an over-wide window is always safe.

2. **Do a REAL Sent fetch over the Step-1 window.** The declared mail backend's seam-resolved search tool, with the `{"in_sent": true, "after": <the floor date you chose in Step 1>}` intent compiled per provider by `connector_adapters/mail.py` (30-day floor on a first run / catch-up; the cursor date on a normal run). This is the whole point of the task — do NOT reuse a message list pulled for some other purpose; that's the exact shortcut that gamed the old gate. For each outbound message resolve `recipient_person_ids` against `entities.json` people (by email), AND capture `recipient_names` — the recipients' display names and email local-parts (e.g. `["Sam", "sam"]` where `sam` is the local-part of the address; `["Bowie Stone", "bstone"]`). Build `sent_messages = [{message_id, ts, recipient_person_ids, recipient_names, subject, body}, ...]`. **`recipient_names` is load-bearing for recall (Bug #103):** commitment extraction frequently fails to link the counterparty into a commitment's `person_ids` (some "Send [Name] …" items are stored with only the user), or the counterparty has no email on file, so resolving by id alone misses real completions — the matcher falls back to finding the recipient's name in the commitment title. (Outlook: equivalent Sent query. No mail connector → emit nothing and exit clean; the brief's soften floor still protects the CEO.)

2b. **Outcome watch (v1) — silent (B6).** A second silent write on the same fire: detect whether earlier CR-sent emails got a reply. Co-locating two silent *writes* is fine — the Bug #98 anti-pattern was a silent write next to a *visible deliverable*, not two background writes. This runs BEFORE Step 3 so the single `sent_reconcile` audit event can carry the reply counts.

```python
import sys; sys.path.insert(0, "shared/scripts")
from email_outcomes import load_pending_tracked_sends, watch_and_receipt

pending = load_pending_tracked_sends("<abs workspace root>")   # email_sent in last 21d, no terminal outcome yet
to_check = pending[-25:]   # cap 25 per run, OLDEST first; backlog drains across mornings (idempotent)
```

   For each send in `to_check`, fetch its thread state via the seam-resolved thread-fetch tool — by the stored thread id if present (the loader returns it from the legacy field or the structured `provenance` block transparently), else the seam-resolved search tool with the `{"message_id_lookup": <the send's stored message id>}` intent (compiled per provider by `connector_adapters/mail.py`) to resolve the thread. Build `thread_states = {sent_event_seq: {"messages": [{"from", "ts", "message_id"}, ...]}}` — **metadata only, never reply bodies or subjects** (privacy). Then:

```python
summary = watch_and_receipt("<abs workspace root>", thread_states, source_skill="reconcile-sent")
```

   **Read-receipts as an extra reply-watch signal (A6, feature-detected — external/sales contacts ONLY per N5):** when the declared backend's manifest has `read_receipts` (`connector_adapters.capabilities.supports(provider, "read_receipts")` — Superhuman's `get_read_status_feed` class), the outcome watch MAY consult the read-status feed for sends still pending. The N5 constraint is CATEGORICAL, not per-message judgment — but be honest about the enforcement tier: it is PROSE-LEVEL (no code consults the read-status feed or checks org type; this instruction is the boundary). Consult read status only for recipients whose resolved person record ties to an EXTERNAL org (client / prospect / vendor — an org that is not the user's own `is_primary_focus` org and not a teammate `reports_to` chain member). Internal recipients are never read-status-watched — that's surveillance, not sales follow-through. Read status never creates a terminal outcome by itself (a read is not a reply); it may only annotate the pending send (`data.seen: true`) so relationship surfaces can render a seen-but-not-replied state. Capability absent → silent skip; the reply watch is unchanged.

   `watch_and_receipt` appends one terminal `email_outcome` event per resolved send (`replied` / `no_reply_7d` / `bounced`) and is idempotent (a re-run re-loads pending and the now-terminal sends are already excluded). Hold `summary` — Step 3 passes it into `reconcile_and_receipt(..., outcome_watch_summary=summary)` so the audit event records the reply counts. **Stay silent — produce no chat output for this phase;** the reply/latency patterns surface later through `insight-generator`'s Outcome-patterns pass, never here. Budget: ≤25 thread fetches + ≤25 message-id-lookup fallback searches per fire; on connector-budget exhaustion, stop and leave the rest pending (idempotence makes that safe). A send with neither a stored thread id nor a resolvable message-id lookup is skipped and ages out of the 21-day window — never guess a thread by recipient+subject.

2c. **Sent-promise extraction (v4.6.2, the BUG-3719 fix) — reuses the Step-2 fetch; no second Gmail pass.** Read each Step-2 sent message and extract NEW commissives — promises the CEO made in their own outbound words ("I'll send corrected invoices next week", "we'll reconcile the billing internally and reissue"). The Stage-D capture floor (`shared/COMMITMENT_SCHEMA.md` § "Extraction triggers") applies with full force: clear owner (the CEO — this pass reads only their own sent words), clear deliverable, real consequence. Rules:
   - **Promises only, not completions.** "Just sent the deck" / "here's the file" is evidence of DOING — that's Step 3's matcher's job (it closes). Extract only forward-looking commissives. Polite filler ("I'll take a look", "will do") is below the floor — when in doubt, skip (same rule as the Slack leg).
   - **This pass never extracts what's owed TO the CEO.** Inbound asks are `inbox-triage`'s lane; direction here is fixed by the surface — Sent mail, the CEO's own words, the CEO's own promises.
   - Per commissive, build an item dict: `{"message_id", "ts" (the message's send time), "title", "kind"` (counterparty determinable → `"promise"`; the recipient almost always is)`, "due"` resolved from the language against the MESSAGE's send date (or `"no_due": true` — silence is rejected by the gate)`, "counterparty_id"` (recipient resolved via `entities.json`, else `"counterparty_name"`)`, "evidence"` (the promise sentence)`, "org_id"/"org_name"` (the recipient's resolved org, for the per-org capture override)`, "person_ids", "classification_confidence"}`. Hold the list as `sent_items` — Step 3 writes them; do NOT append them yourself.
   - Skip extraction for messages already covered (`sent_capture.already_captured(root, message_id, title, provider=<the seam-resolved provider>)`) — cheap, and re-runs stay idempotent either way (Step 3 re-checks). The provider is the declared email backend's provider tag / `DiscoveryResult.platform` from the Step-2 seam resolution — pass it so new refs are honestly attributed (`superhuman:<id>` on a Superhuman backend, never `gmail:<superhuman-id>`).

3. **Run the orchestrator — ONE call. It does the matching, the `commitment_resolved` writes, the sent-promise capture (opens + restatement merges + set-asides), the cursor advance, AND emits the `sent_reconcile` audit event.**

```python
import sys; sys.path.insert(0, "shared/scripts")
from reconcile_sent_commitments import reconcile_and_receipt, validate_reconcile_ran
from primary_user import resolve_primary_user

user_id = resolve_primary_user("<abs workspace root>")   # deterministic — do NOT guess (Bug #102)
receipt = reconcile_and_receipt("<abs workspace root>", sent_messages,
                                user_person_id=user_id,
                                source_skill="reconcile-sent",
                                outcome_watch_summary=summary,   # from Step 2b
                                sent_commitment_items=sent_items,  # from Step 2c — [] when nothing extracted, never None
                                fired_via="scheduled",  # "manual" on a chat-phrase / Run Now fire (v4.5.2 receipt contract)
                                provider="<the seam-resolved provider>")  # declared backend's provider tag / DiscoveryResult.platform from Step 2 — honest source_ref attribution on non-Gmail backends
```

   The capture pass dedups each item against the open set as it stood BEFORE this run's closes (`capture_gate.matches_open_commitment` — shared non-user party + content overlap), so a sent restatement of a meeting-sourced or triage-sourced commitment MERGES into the item that already tracks it instead of double-tracking. Items route through the W4c relevance gate (the CEO is a party to their own promise → opens under party-only; an org-level observed-only override still sets aside; dated/money items always open). Per-item extraction failures land loudly in `receipt["capture"]["errors"]` — non-empty means the extraction must be fixed and the run repeated (idempotency makes that safe); never ignore it silently.
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

   It matches each sent message to a `_hq/voice/draft-snapshots.jsonl` row (by the send's stored message id, else recipient + normalized subject + a 7-day window), classifies the drafted-vs-sent diff, and appends voice corrections. Silent and non-blocking — it never affects the commitment reconciliation receipt; on any error, continue.

   **Structural-correction rider (SPEC OUT8) — additive, same silent contract.** When a Step-4b match corresponds to a delivered Command Room document (the snapshot references a rendered brief) AND the drafted-vs-sent diff is STRUCTURAL — the user reordered, dropped, or reshaped sections before sending, not just rewording — also append one structural correction: `from exemplars import append_structural_correction; append_structural_correction("<abs workspace root>", kind="<the doc's brief kind>", direction=..., section=..., doc="<filename>", source="reconcile_sent")` (`shared/scripts/exemplars.py`; direction from `exemplars.KNOWN_DIRECTIONS`). Wording-only diffs stay voice-rail-only — the two rails are disjoint (voice owns words, exemplars own layout; `shared/VOICE_CALIBRATION.md` § "The rail boundary"). Capture only — this NEVER writes an exemplar; insight-generator Pass 16 batches the log and proposes confirm-first. Silent and non-blocking; on any error, continue.

4c. **Per-person receipts on group items (HYG1 — the MC1 4.7 wire-up) — silent, automatic, inside the Step-3 call.** When a sent message matches ONE recipient of a multi-recipient commitment at auto-resolve-grade score ("send the deck to the board" → a send to one board member), `reconcile_and_receipt` now records that person's delivery receipt automatically instead of skipping the match. Nothing to do here — the orchestrator handles it — but know the contract:
   - **Non-destructive by construction.** A receipt NEVER closes the item; when the last recipient's receipt lands, the item stamps its everyone-received signal, which PROPOSES closure (`receipt["partial_propose_closure"]`) — the user closes, never the task.
   - **Only resolved recipients.** A name-only match (no person record) is skipped and reported in `receipt["partial_skipped_names"]` — an id is never guessed from a name token at write time.
   - **Idempotent.** A recipient already recorded never gets a second receipt; re-runs and wide catch-up windows are safe.
   - The self-closure guard applies unchanged: a receipt is never recorded from the message that opened the commitment.

5. **Surface only if something closed or opened (otherwise stay silent — this is a background task).**
   - `receipt["resolved"]` non-empty → one line, delivered the same way other scheduled tasks deliver (Slack DM / email / saved note per the workspace's delivery preference): *"Closed N follow-ups you'd already sent — [titles]. Say `undo` to reopen any."*
   - `receipt["opened"]` non-empty → *"Started tracking N promise[s] from your sent mail — [titles]. Say `not mine [n]` to drop any."* (Plain language only — never "captured a commissive" or any event name.)
   - `receipt["pending"]` non-empty → *"Did you already handle these? [title] — `mark done [n]`."*
   - `receipt["partial_propose_closure"]` non-empty → *"Everyone on [title] has received theirs — close it when ready."* (Partial receipts themselves stay silent; only a COMPLETED roster earns a line.)
   - Nothing closed, nothing opened → no output. Silence is correct; the audit event is the proof it ran. (Restatement merges, set-asides, and per-person receipts are silent by design — they surface through the Waiting On chat's outstanding rows, not here.)

## Self-heal
The FIRST fire on a workspace fetches a **30-day window regardless of the cursor**
(Step 1, Bug #101) — so it clears the entire accumulated backlog of already-sent
follow-ups, including mail sent BEFORE a stale cursor that an earlier broken
version left sitting ahead of un-reconciled messages. Zero prompt, zero user
action. After that first wide pass, normal runs go forward from the cursor. If a
workspace already had its first run under the cursor-only behavior (so the backlog
is still stranded), a one-time "catch up my sent mail" clears it.

## What it does NOT do
- It does not render a morning brief or a commitments view — the brief reads the closures and opens this task wrote.
- It does not fetch inbound mail, triage, or draft replies — that's `inbox-triage`. The sent-promise capture opens items ONLY from the CEO's own outbound words — it never opens owed-to-you items from what correspondents wrote.
- It does not double-track a promise that already exists — a sent restatement of a tracked commitment merges via the restatement match; the same message re-scanned is skipped by `(source_ref, title)`.
- It never advances the cursor without a real fetch behind it — the audit event + validator make that checkable.
- It does not surface reply/outcome patterns — Step 2b only WRITES the `email_outcome` events silently; `insight-generator`'s Outcome-patterns pass is the reader-facing surface.

## Reliability
Runs as a scheduled task and follows `shared/RELIABILITY.md`: skip-not-fail when the workspace isn't ready, 15s/60s connector budgets, no fabricated data when a connector is down (emit nothing and exit clean — the brief's `reconcile_stale` soften covers the gap).

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Silent maintenance task with four silent write jobs: (1) close commitments the CEO completed by sending a follow-up DIRECTLY from Gmail (outside the product's draft->send path), (2) open a new commitment when a sent reply carries a promise nothing tracks yet (v4.6.2 — the rescue path for outbound promises in threads that were read and replied before inbox triage ever saw them), (3) watch earlier tracked sends for reply/no-reply/bounce outcomes, (4) reconcile final sent bodies against draft snapshots for voice corrections. Fires as the first job inside the maintenance background task on weekday slots (6:45 AM pass lands before the morning brief; MAINT1) and can be run manually with 'reconcile my sent mail' / 'reconcile sent'. A wide one-time catch-up runs via 'catch up my sent mail' / 'reconcile the last N days' / 'reconcile my backlog' — fetches a 30-day window regardless of the cursor to clear a stranded backlog. The core pass: fetch Gmail Sent (since the cursor, or a 30-day window on a first run / catch-up), match against open commitments, write the closures + advance the cursor, and self-validate that the work actually landed; the sent-promise capture, outcome watch, and voice reconcile all ride the same fetch. It does NOT produce a reader-facing brief — the morning brief reads what this task wrote. This split exists because reconciliation is an invisible substrate write that loses to visible deliverables when co-located with them (Bug #98-v3: three folds into the brief/inbox were all skipped; the brief diagnosed the structural cause itself and recommended moving it out).
