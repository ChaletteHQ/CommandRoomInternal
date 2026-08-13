# Shared Chat-Output Protocol (v2.10.6+ · v3.13.0+ empty-state contract)

## Empty-state contract (v3.13.0+ — applies to every scheduled orchestrator)

Per M's 2026-05-20 feedback #26: when a scheduled task fires and surfaces zero items, the chat output must NOT be a one-line "nothing today" dead-end. Three things every empty-state surface must do:

**1. EXPLAIN THE WHY.** What window was scanned. What sources were checked. What filters were applied. Why nothing surfaced.

Examples — clear:
- *"Scanned the next 48h of calendar + open inbox commitments. Nothing ahead that needs prep: your 9 AM Trino call already wrapped, and tomorrow's calendar is empty so far."*
- *"Scanned 24h of unread mail (47 messages) + open commitment events. Everything that came in resolves to threads where you've already replied — nothing in Reply Now."*
- *"Scanned 7-day cadence for the 12 active relationships. Every one had contact within their baseline window — no dormancy alerts."*

Anti-pattern (avoid):
- "Nothing today." (no context — looks like the skill might have failed)
- "All clear!" (cheerful but uninformative)

**2. FALLBACK TO LAST FIRE'S OUTPUT.** Link to the most recent NON-empty fire's brief/deliverable. Reading-yesterday's-output is the next best thing when today is empty.

```python
# Resolve via events.jsonl: find the most recent COMPLETE `pack_run` for this
# task that produced a saved artifact, and surface it as an H2 link at the end
# of the empty-state widget.
#
# v3.14.5 fix: this previously searched for a `scheduled_task_completed` event
# with data.artifact_url + data.item_count — but NO orchestrator writes that
# event type or those field names. Every orchestrator writes `pack_run`, so the
# fallback link never rendered. The pack_run schema is also NOT uniform: the
# identity field is source_skill on some / data.task_id or data.kind on others,
# the artifact path key varies (morning-brief→data.digest_path,
# friday-wrap→data.recap_path, meeting orchestrators→a saved .docx, and
# inbox/commitments/staff-meeting render inline with NO saved doc), and "complete" is
# data.outcome on some / data.status on inbox. So we read defensively below.
# (Cleaner future fix: standardize a canonical data.artifact_path on every
# pack_run write so this reader can stop guessing.)
import json

_ARTIFACT_KEYS = ("artifact_path", "digest_path", "recap_path",
                  "output_path", "brief_path", "artifact_url")

def _pack_artifact(data):
    for k in _ARTIFACT_KEYS:
        if data.get(k):
            return data[k]
    for k, v in data.items():                 # last resort: any *_path string
        if k.endswith("_path") and isinstance(v, str) and v:
            return v
    return None

# Client back-compat (FIX1 Batch A; v4.5.2 R1): live workspaces have
# events.jsonl history in every legacy spelling (cr-inbox, past_meetings,
# dont_forget→pulse, ...). events.jsonl is append-only — never rewritten —
# so match through the receipt contract's normalizer, which parses ALL of
# them forever (cr- prefixes AND underscore kinds — the plain
# source_skill_compat strip missed `past_meetings`).
from receipts import normalize_task_id  # shared/scripts is on path

def _is_for(e, slug):
    d = e.get("data") or {}
    target = normalize_task_id(slug)
    return any(
        normalize_task_id(v) == target
        for v in (e.get("source_skill"), d.get("task_id"), d.get("kind"))
    )

def _is_complete(data):
    return (data.get("outcome") or data.get("status")) == "complete"

slug = "<this orchestrator's slug>"
events = [json.loads(l) for l in open(events_path) if l.strip()]
prior = [e for e in events
         if e.get("type") == "pack_run"
         and _is_for(e, slug)
         and _is_complete(e.get("data") or {})
         and _pack_artifact(e.get("data") or {})]   # only fires that saved a doc
if prior:
    latest = max(prior, key=lambda e: e.get("ts") or e.get("timestamp", ""))
    last_artifact = _pack_artifact(latest["data"])
    last_date = (latest.get("ts") or latest.get("timestamp", ""))[:10]
    # Surface via doc_headline_link or doc_headline_link_h3 per CONTRACT Rule 3.
```

Phrasing:
- *"Last [Task] output (fired [date]) is here:"* (then H2/H3 link via `doc_headline_link`)
- Skip the fallback line entirely if there is no prior non-empty fire (e.g., first-install workspace).

**3. DISTINGUISH FAILURE FROM "TRULY EMPTY."** Three states the user can confuse if the wording is sloppy:

| State | What it means | Wording |
|---|---|---|
| Truly empty | The substrate has nothing matching the filters. The skill worked. | *"Scanned X + Y. Nothing surfaced — [reason]."* |
| Nothing changed | Today's results match yesterday's; nothing NEW. The skill worked. | *"Same X items as yesterday — see [last fire link]. Nothing new since."* |
| Silent failure | A connector flaked, a read errored. The skill failed but didn't crash. | *"Couldn't reach [Gmail / Calendar / etc.] this fire — surfacing what I had from the prior cache. Try `re-run` if [thing] should have updated."* |

The empty-state widget data view shape (`widget_mode: "all_clear_summary"`) already supports the `header` + `summary_line` + optional `tracked_items` fields. Use those to encode the explanation + fallback link. The renderer enforces no bottom buttons on empty states (per v2.14.18 hand-built-widget post-mortem) — don't add "Run scan" or "Re-fire" buttons; the empty state is informational.

**Implementation guidance per orchestrator (5 scheduled + Friday Wrap):**

- **cr-upcoming-meetings:** if 0 events ahead, explain whether tomorrow is empty too or just blocked-from-loading. Link to today's wrapped-meeting recap if applicable.
- **cr-past-meetings:** if 0 meetings to process, link to yesterday's Past Meetings recap.
- **cr-inbox:** if 0 priority threads, explain how many total were scanned + filtered out (and why — bulk / self-replied / etc.).
- **cr-commitments:** if 0 commitments needing action, explain count of open / closed-yesterday and link to last weekly view.
- **cr-pulse (FOSSIL — the chat is retired, LIFECYCLE1; kept as the shape reference for any surface with a genuinely-empty scan):** if 0 dormant relationships, summarize: "12 active relationships, all within cadence baseline."
- **cr-friday-wrap:** if a thin week (rare), still produce a synthesis lead + commitment delta — never an empty wrapper.

---



## Connector drift during a scheduled fire (R13, connector-agnostic-v1 closeout)

Applies to EVERY scheduled orchestrator that resolves a connector through the seam (`discover_for_category` / the `discover_*` helpers). Server UUIDs rotate on reconnect (CONTRACT Rule 22), so a fire WILL eventually find its declared backend missing.

When seam resolution reports the declared backend NOT PRESENT (the drift reason, distinct from capability-absent) during a **scheduled/silent fire**:

1. **Skip that connector's leg** for this fire, with the existing one-line plain-English note in the output (same shape as the connector-not-connected degradation). Never hard-fail the whole fire over one drifted connector.
2. **Append ONE deduped `connector_detected` flag** via `event_gate.append_event` — `{"type": "connector_detected", "data": {"server_id": <the new/unmatched server id if visible>, "provider": <fingerprint match if any>, "fingerprint_matched": <bool>, "triggered_by": "scheduled_fire_drift"}}` — deduped against an already-open flag (an unresolved `connector_detected` with no later `connector_backend_changed` for the same category = open; don't append a second). This is what surfaces the re-pin confirm in the NEXT interactive session (workspace-manager's drift-detect prose).
3. **NEVER prompt, never ask, never ingest from an unconfirmed server** in a silent fire. The ask happens interactively.

Interactive sessions follow workspace-manager's drift-detect flow instead (confirm + re-pin via the setter).

## v2.10.6 — Architectural change: format is now deterministic Python, not LLM prose

**The 12 chat-output rules below are now ENFORCED by `shared/scripts/chat_output_renderer.py` + `shared/scripts/chat_output_validator.py`. Orchestrators don't render their own chat strings anymore — they hand a structured data view to the renderer and the renderer produces the bytes.**

Why: v2.7-v2.10.5 had the LLM remember and apply 12 rules simultaneously while gathering data, drafting emails, and writing chat output. The LLM kept dropping rules under load — entity-ID leaks, phase labels, dot-format actions, missing `N.` prefixes, empty subjects all slipped through. Real-fire feedback in v2.10.5 confirmed: "the format still sucks." Adding more prose rules wasn't the answer; taking format AWAY from the LLM was.

**The new pattern (mandatory for every orchestrator):**

```python
# 1. Orchestrator gathers data (the smart work)
data_view = {
    "header": "Inbox · Apr 28 · 5 priority threads",
    "sub_header": "Noise filtered (49 total): listings (24), ...",
    "sections": [
        {"title": "OVERDUE", "count": 1, "items": [{"n": 1, "icon": "✉", ...}]}
    ],
    "quick_read": "...",
    "bulk_actions": ["send all", ...],
}

# 2. Renderer produces the chat string (deterministic Python)
# (Wrap in CONTRACT.md Rule 22 preamble: SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||");
#  PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"; cd "$PLUGIN_ROOT")
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import render_chat_output
from chat_output_validator import validate_chat_output

text = render_chat_output(data_view)

# 3. Validator catches anything that slipped through
result = validate_chat_output(text)
if not result.ok:
    # Log violation, force re-gather (NOT re-render — the renderer is deterministic;
    # if the validator catches something, the data view itself has a leak)
    raise RuntimeError(f"Pre-flight validation failed: {result.summary()}")

# 4. Post the validated text
print(text)
```

**Where the LLM still owns:**
- Gathering data (read events.jsonl, fetch from connectors, etc.)
- Drafting email bodies (writing the actual prose)
- Quick Read commentary (interpretation of clusters)
- Resolving entity IDs to canonical names BEFORE building the data view (the renderer doesn't resolve — it expects already-resolved names)
- Picking which items to surface (priority scoring)

**Where the renderer owns:**
- Pill format (`▸` markers, exact spacing)
- Markdown headers + dividers between items + section labels
- Italic-wrapping draft body lines
- Inline source link rendering
- `N.` prefix enforcement (renderer raises if missing)
- Tail-block discipline (only Quick Read + bulk actions render at the tail; no telemetry)
- Mojibake-clean text

**Where the validator owns:**
- Catching what shouldn't have slipped through (entity-ID leaks, phase labels, internal paths, telemetry narration, threshold rationales, empty subjects, mojibake, missing item numbers, flag names, engineer phrasing)

The 12 rules below are STILL THE SOURCE OF TRUTH for what the renderer + validator implement. They're documented here for clarity. Orchestrators reference this doc for context, but they don't reimplement the rules themselves — they trust the renderer.

---

# Original Shared Chat-Output Protocol (v2.10.2+ rules)

Universal rules for every Command Room scheduled-task chat output. Every orchestrator (`Upcoming Meetings`, `Inbox`, `Commitments`, `Pulse`, `Past Meetings`) references this doc and follows it. New orchestrators added later inherit by referencing it too.

**Why this exists:** v2.10.0/v2.10.1 fixed render leaks per-orchestrator. They drifted within hours because the rules were duplicated in each prompt and we patched each one independently. Single source of truth here so future fixes don't drift.

**Where this fits:** peer to `EMAIL_DRAFT_PROTOCOL.md`. That doc owns email-draft mechanics (lazy creation, Gmail MCP defensive handling, action shortcuts). This doc owns chat-output presentation. Each orchestrator references both.

---

## The 10 universal rules

### Rule 1 — No engineer-speak in chat, ever

Plain executive English only. **Every** internal reference must resolve to plain English at render time before the chat string is posted.

**Forbidden in chat output (resolve / drop / rephrase before posting):**

| Category | Examples — never let these reach chat |
|---|---|
| Entity IDs | `org_010`, `person_058`, `event_137`, `project_007`, `engagement_001` |
| External IDs | Gmail thread IDs (`19dcd01a93aa79f9`), file IDs, message-Ids |
| Internal paths | `_hq/data/events.jsonl`, `_hq/staging/2026-04-28/`, `staging_emissions.jsonl`, `_unrouted/`, `pre-engagements backup` |
| Tool names | `present_files`, `people-crm`, `scan-for-commitments`, `EMAIL_DRAFT_PROTOCOL` |
| Flag names | `--force`, `--refresh`, `--debug`, `--dry-run` |
| Telemetry mechanics | `pack_run seq 203`, `connector_read events`, `force=true`, `(seq 128-136)`, `items_drafted=3` |
| Threshold rationales | "(First 30 days — using absolute thresholds; statistical baseline activates after 30d of substrate accumulation.)" |
| Engineer phrasing | "PT-bounded", "force re-emitted", "force re-emit", "provenance footer applied", "pending_enrichment rows queued", "staying lazy per ___", "truncated mid-file at person_NNN", "durable from prior run" |
| File-system mechanics | "Files are written, provenance footer applied", "events.jsonl + staging_emissions.jsonl appended" |
| Error-class strings | tool error names, stack-trace fragments, schema rejection messages |

**Resolution patterns:**

| When you'd want to write... | Write instead... |
|---|---|
| `org_010` | "Acme Logistics" (resolve via entities.json canonical_name) |
| `person_058` | "Quinn Sample" |
| `event_137` | inline source link (Rule 2): `[Granola transcript from last night's call](https://granola.ai/note/...)` |
| `(--force, surfacing back to Apr 22)` | `(re-run, looking back to Apr 22)` or omit the parenthetical |
| `force re-emit` | `re-run` |
| `(seq 128-136)` | drop entirely, OR "from the prior run" if re-run context is needed |
| `pack_run seq 203 logged` | drop — telemetry writes silently per Rule 9 |
| `Sources: _hq/data/events.jsonl · _hq/data/staging_emissions.jsonl` | drop — replace with inline source links per Rule 2, OR drop entirely |
| `Brief artifact in the side panel` (when not actually surfaced) | "Open the brief from the link below" + actual working link |

### Rule 2 — Inline source links

Every factual claim that came from a connector gets a clickable link inline, in plain-English markdown form: `[plain-English description](url)`.

This **replaces** the entity-ID leak pattern. Instead of:
> Granola transcript already extracted (event_137);

write:
> [Granola transcript captured last night](https://granola.ai/note/abc123);

The link is the source attribution AND the affordance to drill in. Two birds.

**URL helper — ALWAYS prefer the URL the connector RETURNS (connector-agnostic-v1, Rule 13/N8):** every mail/calendar/drive/chat tool returns a deep-link on the item; use it. `connector_adapters/mail.py::deep_link` / `calendar.py::deep_link` implement this (prefer returned URL → per-provider host fallback only when known → None, degrade the affordance, never emit a broken link). Never synthesize a provider URL host in skill prose. The per-provider host formats live in the adapters, not here.

| Source type | URL |
|---|---|
| Mail thread (any backend) | the URL the mail connector returns for the thread |
| Calendar event (any backend) | the deep-link the calendar connector returns on the event |
| Granola transcript | the URL the transcript connector returns |
| Drive / OneDrive doc | the `webViewLink` / `webUrl` the storage connector returns |
| Slack / Teams message | the permalink the chat connector returns |
| Local file (rare) | omit link; reference by name only ("the migration brief in your Aspen folder") |

**When to link inline vs footer:**
- **Inline** is the default. "[the diagnostic dump Sam sent](url)" reads naturally and the user can click straight through.
- **Footer "Sources:"** block ONLY when an item needs ≥3 sources to back one claim, or when the chat output already has a lot of inline links and another would clutter. In that case the footer lists them as `[1] [Gmail thread](...), [2] [Granola transcript](...), [3] [Drive doc](...)`.
- **NEVER** write `Sources: _hq/data/events.jsonl` — that's not a source, that's our internal substrate. Real sources are the things we read FROM (Gmail, Granola, Drive, etc.).

**Privacy note:** connector-returned thread/event/doc URLs encode IDs but contain no body content. Safe to surface in chat.

### Rule 3 — Global item numbering across sections

Items are numbered globally (1, 2, 3, … 12), not per-section (1, 2, 3 / 1, 2, 3 / 1, 2, 3). So a reply token like `5 send` is unambiguous regardless of which bucket the item lives in.

Even at N=1, the item gets a `1.` prefix — the action menu's `N send` reference is meaningless without one.

### Rule 4 — Blank line between every item

Items never run together visually. One blank line between every item, and between every section. Visual scan is the whole point.

### Rule 5 — Per-item action pills (v2.10.5+ format)

Every orchestrator (Inbox, Commitments, Staff Meeting, Past Meetings, Upcoming Meetings — ALL of them now) renders actions as a per-item **pill line** directly under the item content.

**Pill format:**

```
▸ verb1 [token]  ▸ verb2 [token]  ▸ verb3 [token]
```

- `▸` is the visual pill marker (a single right-pointing triangle). One marker per action.
- Two spaces between pills (`  ▸  `) for visual separation. Not dots, not bullets.
- `[token]` is the action token specific to the orchestrator: `N` (item number) for Inbox / Commitments / Pulse / Past Meetings; `slug` for Upcoming Meetings.

Examples per orchestrator:

**Inbox / Commitments (N-tokenized):**
```
▸ 3 send  ▸ 3 draft  ▸ 3 push to [date]  ▸ 3 snooze 3d
```

**Upcoming Meetings (slug-tokenized — pill embeds the literal slug):**
```
▸ open sam  ▸ tweak sam  ▸ regenerate sam  ▸ skip sam  ▸ push sam to [date]
```

**Past Meetings pending items (IDX-tokenized):**
```
▸ 1a add as person to [Org]  ▸ 1a add as new org  ▸ 1a add context [text]  ▸ 1a skip
```

(The pre-v2.14.x examples used legacy verbs `to drafts` / `edit` / `manually` / `log to discuss` — all consolidated into the canonical set above; `add to my list` was retired at MLK1 2026-07-21 and no example emits it. The renderer rejects the legacy forms; only the examples here matter for orchestrator authors learning the pattern.)

The v2.10.2 pattern (dot-separated `Reply: …`) is replaced by the pill pattern. Drop ANY global "Actions per item" / "Actions (replace XXXX with…)" block at the bottom of the chat turn — pills live with each item.

### Rule 5b — Draft body in italics (v2.10.5+ format)

Whenever an orchestrator surfaces a draft email body, status note, chase note, or any other proposed-text-for-user-to-send block in chat, wrap the body in markdown italics (`*…*` or `_…_`) so the user can visually distinguish:

- **Metadata** (To / Subject / context tags) — plain
- **Draft body** (the actual proposed text) — *italic*
- **Action pills** — pill-marker prefix

Pattern:

```
Subject: Re: Q2 deck — your thoughts
To: sam@example.com
Body:
*Hey Sam — got your write-up. Read it last night.*
*A handful of your call-outs map directly to what we're already patching*
*in this release. A couple are real and need a design conversation.*
*Real reply by Friday EOD.*
*MD*

▸ 3 send  ▸ 3 draft  ▸ 3 snooze 3d
```

Multi-line drafts: each line individually wrapped in italics (the standard markdown rendering preserves line breaks within an italic block but most renderers prefer per-paragraph). Long drafts: wrap each paragraph in its own `*…*` pair.

### Rule 6 — Drop redundant `[slug]` prefix

When the action token is `N` (the item number) AND a full name appears in the same line, the `[slug]` prefix is redundant. Drop it.

**Wrong:**
```
1. 👤 [bo] Bo Sample — Cadence break.
```

**Right:**
```
1. 👤 Bo Sample — Cadence break.
```

The slug stays only in Upcoming Meetings (where the user types it as the action verb's argument: `open bo`, `tweak aria`).

### Rule 7 — Closing "Quick Read" meta-commentary

When N items > 2 AND a pattern-clustering signal exists (e.g., "items 1+2 are the same vendor-eval pattern you suppressed last week"), close the chat turn with a brief meta-commentary block. 1-3 sentences, plain English, bot interpreting the items not just listing them.

Example from the retired cracks-watch / Pulse chat (shape reference only — LIFECYCLE1):
> Quick read: items 1 and 2 look like the vendor-eval pattern you've already been suppressing — `1 resolved vendor eval done` and `2 resolved vendor eval done` clear them. Item 3 (Sam) is the highest-signal real crack — silence is likely on your side waiting for the prep materials. Item 5 (Aria) is the most genuinely anomalous cadence break.

The Quick Read is where the bot becomes a coworker, not a list-printer. When a clustering signal isn't present, omit the block entirely — never pad with "all items deserve attention" filler.

### Rule 8 — Tool errors never expose internals

If a tool call fails, surface the consequence in plain English. Never expose tool names, error class strings, or filesystem mechanics.

**Wrong:**
> Chat-link surfacing via present_files failed (path resolution error) — open them directly from the staging folder. Files are written, provenance footer applied, events.jsonl + staging_emissions.jsonl appended.

**Right:**
> Couldn't link the briefs in chat — open them from your Command Room workspace folder under today's date.

If a connector flakes:
> (Slack didn't respond — retry with `refresh slack`.)

If a sub-skill fails:
> (Voice calibration unreadable — using neutral professional tone for now. `refresh voice samples` to fix.)

### Rule 9 — Tail/closing block discipline

Telemetry events (`pack_run`, `draft_created`, `connector_read`, `meeting_processed`, etc.) write to `events.jsonl` **silently** as a side effect. Never narrate them in chat.

**Wrong tail block (real example from a cr-commitment-chase fire):**
> Logged: pack_run seq 203 (commitment_chase, status ok, force=true, items_drafted=3), 3 draft_created events, 1 connector_read. No Gmail drafts created — staying lazy per EMAIL_DRAFT_PROTOCOL.
> Sources: _hq/data/events.jsonl · _hq/data/staging_emissions.jsonl

**What's allowed at the tail:**
- The Quick Read block (Rule 7), when applicable
- A one-line plain-English save confirmation: "Drafts saved — reply to send any of them." or "Briefs saved to your Command Room folder for today."
- A "Sources:" block of inline source links per Rule 2, when ≥3 sources back the items and inline linking would have been cluttered
- **The receipt-errors notice, when there is one (WALKFIX1 Item J, 2026-08-10).** If this fire's own `pack_run` receipt carries a non-empty `data.errors`, the tail carries ONE line, rendered by `shared/scripts/receipts.py::receipt_errors_notice(<the receipt event>)` — never composed:

  > 1 internal correction noted — details in the run receipt (entry 8324).

  This is the ONE exception to "never narrate telemetry", and it is narrow on purpose: a count and a pointer, no error text, no phase names, no alarm vocabulary, and nothing for the reader to do. It exists because the alternative is worse — on 2026-08-10 a fire wrote an `errors[]` entry naming a real correctness defect in its own run and the chat presented a clean recap beside it, so the operator could only learn of it by reading the ledger. A system that noticed something and said nothing has spent trust it did not have to. Empty or absent `errors` renders nothing at all; a notice about nothing is worse than silence.

  > **FLAGGED FOR M's UI/UX RETEST.** This amends chat-surface posture on a question M has not explicitly ruled on. It follows the product's own closing-silently-is-worse principle, and M may strike it.
  >
  > **COMPLETE STRIKE SET — all five, and the battery is RED if you stop at three.** (The first cut of this list named only the first three; a reviewer executed it and the mutation harness halted with `mutation anchor in shared/scripts/receipts.py matched 0 times, expected 1` — a striker following a partial list gets a red battery and no hint why. The list is complete when striking it leaves the battery green, and that is now verified by execution.)
  >
  > 1. **this bullet** — the whole `**The receipt-errors notice…**` item, including this flag block;
  > 2. `shared/scripts/receipts.py` — the `receipt_errors_notice` function and its docstring;
  > 3. `tests/run_walkfix1_chat_errors_test.py` — `section_errors_notice` (its `[J]` / `[J2]` output) **and its call in `main()`**;
  > 4. `tests/run_walkfix1_mutation_test.py` — mutation **`W18`** (the `M.run_mutation("W18 …")` block) **and its `W18` row in the module docstring**. *This is the one the partial list missed: the mutation's anchor is the code struck in step 2, so leaving it in place halts the harness.*
  > 5. `CHANGELOG.md` — the *"A run that corrected itself says so, in one line"* section of the `## Unreleased — WALKFIX1` block, which would otherwise ship a release note for a feature that is not in the build.
- Nothing else. No internal mechanics. No tool-call summaries. No event seq numbers.

### Rule 11 — Phase labels NEVER appear in chat (v2.10.5+)

The orchestrator prompts are organized into phases (`# Phase 1 — Always run`, `# Phase 3 — Setup`, `# Phase 7 — Memory updates`, `# Phase 8 — Post the chat turn`, etc.). These are **internal scaffolding for the orchestrator, not user-facing labels**. The chat output starts DIRECTLY with the user-facing format block (e.g. `Inbox · Apr 28 · N priority threads...`).

**Forbidden in chat output:**
- `Phase 7 — silent memory updates`
- `Phase 8 — chat turn`
- `# Phase N — anything`
- `Setup phase complete, moving to Phase 4`
- `Idempotency gate skipped` (the gate was removed in v2.10.5; never narrate its presence or absence)
- `Logged events to events.jsonl` (engineer-speak per Rule 1)
- `Read a file, loaded tools, ran a command` (this is Cowork's tool-call tracker echoing — not orchestrator output, but flag for awareness; the orchestrator should never emit similar narration on top of it)

The user-facing chat output is pure deliverable. Internal mechanics, phase progress, telemetry writes, tool-call counts — all of it stays invisible.

If you want to explain something to the user (a degradation, an error, a heads-up), do it in plain English at the right point in the format, NOT as a phase-labeled narration.

### Rule 12 — Email/message subject fallback (v2.10.5+)

When an orchestrator surfaces a draft email and the source thread's subject is empty or partial (e.g., the user is replying to a thread where `Subject: Re:` has nothing after the colon), DO NOT emit a literal empty `Subject: Re:` line. Fallback chain:

1. Use the most recent message's full subject in the thread (NOT just "Re:" — the entire subject string).
2. If still empty/blank, use the previous message's subject.
3. If the entire thread has no subject (rare), use a 5-word descriptor of the thread topic from the most recent message body — formatted as the new subject (no `Re:` prefix since there's nothing to reply to).
4. NEVER ship a draft with `Subject: Re:` (empty), `Subject: ` (blank), or `Subject: <empty>`.

### Rule 10 — Summary blocks render as bullets, not run-on prose

Any orchestrator that produces a multi-point summary (Upcoming Meetings briefs, Past Meetings summaries, deep-dive blocks in onboarding) renders the summary as 3-7 bullet points, one per distinct point. Sub-bullets allowed for clusters.

**Wrong (run-on prose, real example from cr-meetings-processed):**

> Walked Quinn through the Command Room install — workspace folder, Cowork, automatic ingestion. Mid-call corrected the entity model: The Link, The Crescent, Imperial Valley Mall, and Finger Lakes Mall are each separate LLCs (not under Acme Co); Acme Holdings is Quinn's personal operating LLC; Acme Co is its own thing. Three integration blockers surfaced — Granola transcript flapping, Claude's one-email-per-account limit hits Quinn's dual addresses, and Dropbox isn't reachable.

**Right (bullets, sub-bullets for clusters):**

> Summary:
>   - Walked Quinn through the Command Room install (workspace folder, Cowork, automatic ingestion)
>   - Corrected the entity model mid-call: The Link, The Crescent, Imperial Valley Mall, Finger Lakes Mall are each separate LLCs (not under Acme Co); Acme Holdings is Quinn's personal operating LLC; Acme Co is its own thing
>   - Three integration blockers surfaced:
>     - Granola transcript flapping
>     - One-email-per-account limit (Quinn has acme.example.com + acme.example.com addresses)
>     - Dropbox isn't reachable

---

## How orchestrators reference this doc

Every orchestrator's prompt starts with a single line near the top:

> **Chat-output rules:** follow `references/SHARED_CHAT_OUTPUT_PROTOCOL.md`. The 10 universal rules apply to everything you write to chat.

The orchestrator's specific format spec then describes only what's UNIQUE to that orchestrator (section structure, item shape, action verbs). Universal rules don't get restated.

When a rule changes, this doc is the only file edited. All orchestrators inherit the change on their next fire.

---

## Migration from v2.10.0/v2.10.1

The previous orchestrator prompts had inline rule restatements that conflicted with each other. v2.10.2 strips those inline restatements and replaces them with the reference line above. If you find an orchestrator restating a rule (e.g., "no entity IDs in chat"), drop the restatement — the SHARED protocol owns it.

EMAIL_DRAFT_PROTOCOL.md's §0 ("Plain-English chat output") was a half-step toward this doc. v2.10.2 promotes it: §0 in EMAIL_DRAFT_PROTOCOL.md now points here for the universal rules; EMAIL_DRAFT_PROTOCOL.md retains only the email-draft-specific mechanics.
