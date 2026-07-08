---
name: transcript-search
description: "Search across all meeting transcripts for mentions of a topic, project, keyword, or non-attendee person. Returns meeting hits with date, attendees, and a snippet showing the topic in context. Triggers: 'what did anyone say about [topic]', 'what did [person] say about [topic]', 'meetings about [topic]', 'transcript search [topic]', 'where was [topic] discussed', 'find meetings on [topic]', 'search transcripts for [topic]'. Cross-meeting topic-based search — different from people-crm's `tell me about [person]` which scopes to where the person was an attendee."
---

# transcript-search

Cross-meeting topic search. The user wants to know what was said about something — a project, a competitor, a deal, a feature, a person who wasn't necessarily a meeting attendee — without having to remember which specific meeting it came up in.

## Writer Contract (v3.7.1+ — substrate-aware; v3.14.6+ — reconcile-on-read)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`.

**Substrate-sync contract (v3.14.6+ — MANDATORY):** this skill fetches raw
transcripts on-demand, so it is bound by `shared/INGEST_SUBSTRATE_SYNC.md`. Any
transcript it surfaces that has never been processed into substrate MUST have its
entities reconciled (Step 5.5 below). transcript-search is NOT exempt — being
read-only over the *results display* does not excuse dropping a new person/org/
commitment that appears in a transcript it just pulled. This closes the gap M hit
2026-05-28 (pulled a transcript, the person in it was never added).

**Appends to:**
- `_hq/data/events.jsonl` — event type `search_performed` with `{query, person_filter, hits_count, top_hit_event_seq, scope_window_days}` for every search the user runs.
- `_hq/data/events.jsonl` + `_hq/data/entities.json` — via the Step 5.5 reconcile pass, the same `meeting` / `commitment` / `decision` / person-write events the scheduled `cr-past-meetings` orchestrator emits, but ONLY for surfaced transcripts not already in substrate, and ONLY the data layer (no briefs/drafts/widgets). These writes go through `meeting-notes` + `people_writer` — never hand-rolled. Per-search granularity is intentional: aggregated query frequency is an insight signal in its own right. **In-skill use today:** this skill reads its own past `search_performed` events to detect repeat queries and surface the "you've searched this 7 times" hint alongside hits. **Future cross-surface use:** the event is canonical-shape substrate so insight-generator or Pulse can later detect topical obsession patterns ("you keep researching pricing — want a synthesis?"). No cross-surface consumer reads this yet as of v3.12.0.

**Reads from:**
- Granola / Fireflies / Otter transcript connectors (or `_hq/meetings/*_transcript.md` if synced).
- `_hq/data/entities.json` person records when the query specifies a person filter (e.g., "what did Bo say about X").
- `_hq/data/events.jsonl` `meeting` events to resolve transcript → meeting metadata (date, attendees, project).
- `_hq/data/events.jsonl` prior `search_performed` events to detect repeated-query patterns (informs the "you've searched this 7 times" UX hint surfaced in chat alongside the hits).

**Conflict boundary:** sole writer of `search_performed` events. Read-only over every other surface.

**Why the upgrade (v3.7.1 note):** pre-v3.7.1 transcript-search was a pure reader — searches happened and vanished. The query log was lost. v3.7.1 turns search activity into a substrate signal so the system can detect topical obsession ("you keep researching pricing — want me to surface what came up across all 7 of those searches?") without the user having to ask.

## When this fires vs. people-crm

| Query shape | Skill | Scope |
|---|---|---|
| `tell me about [Person]` | people-crm | All transcripts where Person was an attendee |
| `what did [Person] say about [topic]` | transcript-search | All transcripts where Person was an attendee AND topic appears in text |
| `what did anyone say about [topic]` | transcript-search | All transcripts (any attendee) — keyword-only |
| `meetings about [topic]` | transcript-search | Same — keyword-only |
| `where was [topic] discussed` | transcript-search | Same — keyword-only |

people-crm pulls per-attendee. transcript-search pulls per-topic. They complement each other and don't overlap.

## What it does

Takes the topic phrase from the trigger. Searches across recent meeting transcripts (default last 90 days) for hits where the topic phrase appears (or has high overlap with the transcript content). Ranks results by relevance × recency. Returns the top 5 with enough context to recognize the meeting and decide if it's worth opening the brief.

## Behavior

### Step 0 — Parse the topic from the trigger

Extract the topic phrase. Examples:
- `"what did anyone say about MegaSupply"` → topic = `"MegaSupply"`
- `"meetings about Q3 pricing review"` → topic = `"Q3 pricing review"`
- `"where was the AP automation pitch discussed"` → topic = `"AP automation pitch"`
- `"what did Bo say about the term sheet"` → topic = `"term sheet"`, person filter = `"Bo"`

If the trigger names a SPECIFIC person AND a topic ("what did Bo say about X"), apply both filters: only meetings where Bo was an attendee AND the topic appears in the transcript. Otherwise topic-only.

If the topic is ambiguous or empty (e.g., `"transcript search"` with no topic), surface plain English: *"What should I look for across your meetings?"*

### Step 1 — Discover the transcript connector

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from tool_discovery import discover_transcript_tool
result = discover_transcript_tool()
print(f'TOOL_ID={result.tool_id}')
print(f'PLATFORM={result.platform}')
"
```

If no transcript tool is discovered, surface plain English: *"I don't see a meeting transcript source connected yet. Connect Granola or Fireflies in your settings and I'll be able to search through your meetings."* STOP.

### Step 2 — List candidate meetings

For the discovered platform:

- **Granola:** call the meetings-list tool with date filter (last 90 days). Or `query_granola_meetings` with the topic as a keyword if available — that's a single-call pre-filter that saves transcript fetches.
- **Fireflies:** equivalent list-meetings call with date filter.

Cap candidate list at 50 meetings. If the connector returns more, take the most recent 50 by date.

### Step 2.5 — Entity-resolve the query BEFORE literal scoring (v3.13.0+ — closes the "Dynarii returns 0 results" gap; v3.13.7+ MUST-language enforcement + layer-on-top design)

**MUST-language enforcement gate (v3.13.7+):** for every transcript-search invocation, you MUST call `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` in this step, per `shared/ENTITY_RESOLVE_PROTOCOL.md` (the ladder, tiers, and fallback rules live there). The resolver runs LAYERED ON TOP of any Granola NL search, NOT as a replacement for it.

> **Run entity_resolve in parallel with Granola NL. Take the UNION of attendee-matched meetings (from resolver) + content-matched meetings (from Granola NL / literal scoring). Both signals contribute to the candidate set. Never skip the resolver because Granola NL "looked sufficient" — that's exactly the bypass Session-22 Bug #11 documented.**

Why layer-on-top, not either-or:
- **Granola NL** is excellent at content-match — it found the right 7 transcripts for "empower group" by language similarity, no aliases needed
- **entity_resolve** is excellent at attendee-match — it pulls every meeting where a person/org/project member attended, even when the brand name was never spoken (the code-named-project class)
- The union covers both classes; either alone misses the other's specialty

Session 22 (Phase 2E test) verified that Cowork called Granola NL and got correct results — but skipped entity_resolve entirely. Output was right by luck of Granola NL's strength on that particular query; an attendee-only-relevance query would have returned empty. The layer-on-top contract means the resolver runs every time, and contributes whatever it can.

**Why this step exists in the first place:** the 2026-05-20 Cowork handoff diagnosed the bug exactly. A user ran "search transcripts for [code-named project]" and got "couldn't find any" — even though the project has 6+ meetings in the last week and is fully modeled in entities.json (org + project + 3 people + 8 aliases). The brand name is never spoken in transcripts (the team refers to "the app," "the game," etc.). Literal-token scoring on the brand name returns zero. Per #18: this happens for EVERY early-stage venture, code-named project, or company whose brand name doesn't get spoken in meetings.

**The fix:** resolve the topic via `shared/scripts/entity_resolve.py` to see if it names a person/org/project the workspace knows about. If yes, EXPAND the candidate set + the search-term set:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from entity_resolve import resolve_all
result = resolve_all('$WORKSPACE', '<query>')  # same call the MUST-gate mandates — never the single-entity resolve()
if result:
    print(f'ENTITY_TYPE={result.entity_type}')
    print(f'ENTITY_ID={result.entity_id}')
    print(f'CANONICAL_NAME={result.record.get(\"canonical_name\", \"\")}')
"
```

If the resolve returns a person/org/project:

- **Person**: include EVERY meeting where this person was an attendee as a candidate, regardless of whether the literal token appears. Label each match with `from a meeting <name> was in` (per CONTRACT Rule 4 — use display name, not the `person_NNN` id).
- **Org**: include every meeting where any LINKED PERSON (org members) was an attendee. Plus expand the search-term set with the org's aliases + the canonical names of its linked projects.
- **Project**: include every meeting where the project's `key_contact_id` person was an attendee, OR any meeting tagged with this `primary_thread_id` in events.jsonl. Expand search terms with the project's aliases + the project's canonical name.

The expanded candidate set goes through the same Step 3 scoring (`score_match`). Meetings that pass via attendee-only (not literal-token) are kept in the result set and labeled per Rule 4 — the user sees WHY they matched ("from a meeting Aria Sample was in") without leaking IDs/paths.

If the resolve returns nothing AND the literal-token score also returns nothing in Step 3, the empty-state message in Step 4 should still be friendly ("I couldn't find...") rather than misleading. This is the 0-hit graceful-fallback from the handoff.

### Step 3 — Score each candidate

For each candidate meeting (literal-token OR entity-expanded — see Step 2.5):
1. Fetch the transcript text via the transcript-fetch tool.
2. If a person filter was applied (`"what did Bo say about X"`), skip meetings where Bo was not an attendee.
3. Run `score_match(topic, transcript_text)` from `shared/scripts/cru_match.py`. This uses the same overlap-coefficient + bigram Jaccard scoring that the CRU layer uses — already proven, no new helper needed.
4. **For entity-expanded candidates**: also keep a minimum-score floor of 0.0 (don't filter out attendee-matched meetings just because the literal token isn't in the transcript — they're explicitly meant to be in the result set).
5. Extract a snippet: find the first sentence/phrase in the transcript that contains the topic words (or the highest-density region if no exact phrase match). ~2 lines max. For attendee-matched results with no literal hit, surface the first 2 lines of the attendee's first significant utterance as the snippet.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from cru_match import score_match
score = score_match('<topic phrase>', '<transcript text>')
print(f'SCORE={score}')
"
```

### Step 4 — Rank and pick top 5

Sort candidates by `score × recency_decay` where recency_decay is a simple linear factor: 1.0 for today, 0.5 for 90 days ago. Take top 5. The `score >= 0.20` floor applies to LITERAL-TOKEN candidates only (keep the bar low — topic search is exploratory); entity-expanded / attendee-matched candidates are EXEMPT from the floor, per Step 3's rule 4 — they're in the set by attendee match, not token score, and the 0.20 filter would silently undo that rule.

If no candidate scores ≥ 0.20 → surface plain English: *"I couldn't find any meetings where '[topic]' came up in the last 90 days. Try a different phrasing, or ask me to look back further."*

### Step 5 — Render the results

Plain markdown list (NOT a widget — this is read-only retrieval, no actions). Format:

```markdown
**Found 4 meetings discussing "MegaSupply" in the last 90 days:**

1. **Sam Sample — Workspace Map renderer review** · Apr 28
   _Attendees: Sam, M_
   > "...we should look at how MegaSupply is handling the same data shape — Bo mentioned they had a similar problem..."

2. **Bo Sample — Northstar Partners sync** · Apr 22
   _Attendees: Bo, M_
   > "...MegaSupply's procurement system is the model — that's why Quinn keeps bringing them up..."

3. **Quinn Sample — Initial discovery call** · Apr 18
   _Attendees: Quinn, M_
   > "...MegaSupply was the one that almost got the contract before they went with the competitor..."

4. **Internal — Strategy review** · Apr 12
   _Attendees: M, Dustin_
   > "...the MegaSupply opportunity is still warm — we should circle back in Q3..."

Source: Granola transcripts.
```

**Format rules:**

- One numbered entry per hit.
- Bold = meeting title (or attendee name + topic if title is missing).
- Date in the format the user expects (rolling-relative: "yesterday", "Apr 28", etc.).
- Italicized attendee list (resolve person_NNN → real names always).
- Blockquoted snippet with the topic word(s) intact. Trim ellipses around the snippet to signal it's a fragment, not the full sentence.
- Trailing line names the source connector (Granola / Fireflies).

Per `shared/CONTRACT.md` Rule 4: NO entity IDs in the output (`person_NNN`, `org_NNN`, `meeting_NNN`). NO file paths. NO event-type names.

If a Granola transcript URL is available, link the meeting title: `[Sam Sample — Workspace Map renderer review](https://notes.granola.ai/d/<note_id>)`.

### Step 5.5 — Reconcile surfaced transcripts into substrate (v3.14.6+ — MANDATORY)

Per `shared/INGEST_SUBSTRATE_SYNC.md`. Searching a transcript pulls its full text
into context — if that meeting was never processed, the people / orgs /
commitments in it are invisible to the workspace, and the user has no idea (M's
2026-05-28 report: pulled a transcript, the new person in it was never added).

For EACH meeting surfaced in Step 5 (the top-5 result set only — do not crawl
beyond what the user is looking at):

1. **Dedup check.** Look for a `meeting` event in `events.jsonl` whose
   `data.source_ref` is `granola:<meeting_id>` (or `fireflies:<id>`).
   - **Found** → already captured. No-op. Skip.
   - **Not found** → this transcript has been read but never captured. Continue.

2. **Capture the data layer — reuse `meeting-notes`, do NOT re-implement.** Invoke
   the `meeting-notes` skill silently on that transcript (the same extraction
   `cr-past-meetings` Phase 4 runs). It emits the canonical `meeting` event (with
   `source_ref` for idempotency), `commitment` / `decision` events, and routes
   new people through `shared/scripts/people_writer.py` (`find_existing_person`
   dedup FIRST, then `create_person` for high-confidence / `person_proposal` for
   low) and new orgs through the `org_proposed` path. Honor the cross-meeting
   fusion guardrail + speaker-attribution ambiguity guard from
   `orchestrator-past-meetings.md` (§4 + §4.5d) — the transcript text is already
   in hand, so the same safety checks apply.

3. **Data layer ONLY.** Do NOT generate a `.docx` brief, follow-up drafts, or a
   chat widget. Those are orchestrator-owned deliverables. The reconcile pass
   makes the entities/commitments EXIST; it does not produce documents.

4. **Idempotent.** The `source_ref` dedup in step 1 means the same transcript
   surfaced by ten searches captures exactly once; a later scheduled
   `cr-past-meetings` fire also no-ops on it via the same ref.

5. **One-line plain-English note** appended after the results (CONTRACT Rule 4 —
   no event-type names, no IDs, no paths):

   > *(2 of these meetings weren't in your workspace yet — I've captured them,
   > including 1 new person, Quinn Sample. Say `past meetings` or check your next
   > Pulse to see what I added.)*

   If every surfaced transcript was already in substrate, append nothing.

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "matched via attendee: person_014 — review what I logged"
- Good: "from a meeting Aria Sample was in — check your next Pulse to see what I added"

If `meeting-notes` errors on a transcript, swallow it silently and continue (the
search results already stand). No error note is required — transcript-search has no
pack_run. Just don't break the read.

### Step 6 — STOP

No widget. No follow-up offer. No "want me to dig into one of these?" If the user wants to drill into a specific meeting, they'll ask. The search RESULTS are read-only retrieval and stand on their own — the only writes are the Step 5.5 reconcile pass (capturing entities the user would otherwise lose) and the `search_performed` log.

## What this skill does NOT do

- Does NOT fire scheduled tasks
- Does NOT generate `.docx` briefs, follow-up drafts, or chat widgets — the Step 5.5 reconcile pass captures the DATA layer only (entities/commitments), never the heavyweight deliverables
- Does NOT search emails, Slack, or Drive — transcripts only (Granola / Fireflies)
- Does NOT search by attendee alone — that's `tell me about [Person]` (people-crm)

It IS read-only over the *results display* (no widget, no actions), but per
`shared/INGEST_SUBSTRATE_SYNC.md` it is NOT read-only over substrate: a surfaced
transcript that was never processed gets its entities reconciled (Step 5.5). The
pre-v3.14.6 "does NOT modify any workspace data" claim was the bug — it let a
pulled transcript's new people vanish.

## Trigger pattern

`what did anyone say about [topic]` / `what did [Person] say about [topic]` / `meetings about [topic]` / `transcript search [topic]` / `where was [topic] discussed` / `find meetings on [topic]` / `search transcripts for [topic]`

## When to use

- Recalling what was said about a deal, project, company, or topic across multiple meetings without remembering the exact meeting
- Pulling cross-meeting context for a brief or memo (e.g., "what's the running thread on MegaSupply across the last quarter")
- Verifying whether a topic was actually discussed (vs. assumed) before referencing it in a follow-up

## See also

- `people-crm` — `tell me about [Person]`, scoped to where the person was an attendee
- `decision-log` — `what did we decide about [topic]`, scoped to extracted decisions (smaller corpus, more precise)
- `shared/scripts/cru_match.py` — the `score_match` function this skill uses for relevance ranking
- `shared/scripts/tool_discovery.py` — `discover_transcript_tool()` for cross-stack Granola/Fireflies routing
