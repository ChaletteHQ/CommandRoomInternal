---
name: apply-choices
description: "Receives the consolidated `apply choices: [...]` payload from the all-batch chat action widget's 'Apply all' button (and from the command-room-onboarding Step 1 v2 widget's Finish click). Parses the JSON array of {n, action, sub?, input?} tuples, dispatches each through the same handlers the source orchestrator emitted, and surfaces ONE consolidated chat ack. Apply-time outputs that produce email drafts or documents follow the SAME widget + clickable-link contract as the original orchestrator surface (v2.12.4+). Triggers ONLY on the exact prefix `apply choices: ` followed by a JSON array. Companion to `shared/CHAT_ACTION_WIDGET.md` (canonical widget spec) and `chat_output_renderer.py` `render_chat_output_widget()` (the renderer side; command-room-onboarding Step 1 v2 widget bypasses the renderer and is rendered raw via `mcp__visualize__show_widget`). Used by all 5 CR scheduled-task orchestrators (Pulse, Commitments, Inbox, Past Meetings, Upcoming Meetings) plus on-demand `meeting-notes` Step 9 OPEN ITEMS surface plus first-install `command-room-onboarding` Step 1c."
---

# apply-choices

Single dispatcher for the all-batch widget's "Apply all" submission. The widget renders per-item buttons that toggle local selection state — clicking buttons does NOT fire `sendPrompt`. Only when the user clicks "Apply all" does the widget serialize all selections and fire ONE consolidated message.

## ⛔ STOP CONTRACT (v2.14.14+) — applies to apply-time response too

**The post-Apply response surface follows the same rule as the originating orchestrator: widget + (optional) Briefs/Sources, then STOP.**

When apply-time produces drafts or regenerated docs, those surface in a NEW widget per Step 4A — NOT as standalone HTML files saved to disk, NOT as narrated summaries.

**Forbidden — zero tolerance:**

1. **No writing the post-Apply widget HTML to disk.** Not to `_hq/scheduled_outputs/`, not anywhere. The post-Apply widget surface is `show_widget` ONLY.
2. **No narrating "X drafts ready below"** before the widget. The widget self-describes via its header.
3. **No post-widget summary** like `Saved the standalone HTML at _hq/...` or `Regenerated all N drafts.` — same Rule 4 enforcement as the orchestrators.
4. **Re-renders on user re-prompt** ("regenerate with real data" against an apply-time response) use the same code path as the original Apply — re-execute Step 4A through the SAME pipeline (renderer → `show_widget`).
5. **Do NOT improvise a "save the apply output so the user can re-open" mode.** Saved HTML's buttons aren't wired to Cowork's `sendPrompt` — the saved file is dead on click. This is what broke `Edit then send` post-Apply in v2.14.13 testing.

**Self-check:** before posting anything outside a `show_widget` call, ask: "is this the SHORT plain-English ack per Step 4B, or is it freelancing?" If freelancing → STOP.

---

## Trigger pattern

EXACTLY one shape:

```
apply choices: <JSON array of {"n": <id>, "action": <string>, "sub": <optional string>, "input": <optional string|object>}>
```

Anything else → do not fire. This skill is not for general use.

The `input` field carries widget input-field values for actions that need user input. Two shapes:

- **String** (most actions) — free-text inputs (`resolved [reason]`, `context [text]`, `add more context [text]` (back-compat alias), `push meeting [date]` natural-language, `confirm [type]` overrides, `decide [text]`, etc.)
- **Object** (multi-field email edit) — `edit then send` and `draft` (the v2.14.4+ consolidated verb that replaced `to drafts` + `edit then draft`) carry an object with `to` / `cc` / `subject` / `body` keys.

The `sub` field is currently emitted ONLY by the `command-room-onboarding` Step 1 v2 widget (the role question's sub-chip drill-down). Other orchestrators do not produce it. The dispatcher does not interpret `sub` — it preserves the field intact and passes the full tuple to the source skill's reply handler. Do NOT strip or reject tuples with `sub` present. The allowed-keys set on each tuple is `{n, action, sub, input}`; unknown additional keys are tolerated but ignored.

## Behavior

### Step 0 — Suppress the payload from chat history (v2.12.4+ — REQUIRED)

The user-facing text `apply choices: [{"n":...}]` is wire format. It must never appear as visible chat text.

When this skill fires, the user-typed message ALREADY shows in chat history above your response (Cowork echoes user submissions). The skill cannot remove it from chat. What it CAN do is render its own response as a Cowork-styled card so the substrate-y prefix gets visually overshadowed.

**Do NOT echo or paraphrase the payload.** Don't write "Received: [{...}]" or "Processing 5 actions" with the parsed array shown. The payload is internal mechanics — the user already knows what they clicked Apply on.

### Step 1 — Parse the payload

Strip the `apply choices: ` prefix. Parse the rest as a JSON array. Each entry must have:
- `n` — string or number, the item identifier (matches `data-n` from the widget; can be `1`, `7a`, `e1`, etc.)
- `action` — string, the action label (matches `data-action` from the widget; e.g. `send`, `mark received`, `push meeting [date]`)
- `sub` — optional string, present only for `command-room-onboarding` Step 1 Q1 sub-chip drill-down (e.g. `holdco`, `gp-fund`, `senior-staff`). Other orchestrators never emit it. The dispatcher passes this through verbatim — never strips or rewrites it.
- `input` — optional string OR object, the widget input-field value if the action needed one

If JSON parsing fails, surface plain English:

> *"Couldn't read your choices — refresh the chat and try again, or just tell me what you wanted ('send #2, push #4 to Friday')."*

Do NOT attempt to recover from a malformed payload — the widget produces well-formed JSON or it doesn't.

### Step 2 — Identify the source orchestrator

The widget was emitted by one of these orchestrators:
- `cr-dont-forget`
- `cr-commitments`
- `cr-inbox`
- `cr-past-meetings`
- `cr-upcoming-meetings`
- `meeting-notes` (on-demand processing, Step 9 OPEN ITEMS section)
- `show-my-list` (v2.14.19+ — review surface for `commitment_to_discuss` items captured via `add to my list`. Fires when the user clicks `resolved` / `skip` on items in their discuss list. **Handlers (v3.11.4+ — canonical closure shape per `references/SOURCE_OF_TRUTH.md`):** `resolved` → write **`commitment_resolved` event with `data.commitment_id` set to the original `commitment_to_discuss` event's `seq`** (NOT `thread_resolved` with `data.target_id`, which was the pre-v3.11.4 shape — that shape didn't match any consumer's closure filter, so clicks appeared to work but the discuss item silently stayed open on the next show-my-list re-render). `skip` → write `chat_dismissal` event with `data.target_id` set to the original event's seq (unchanged from pre-v3.11.4 — chat_dismissal is its own canonical pattern). For backwards-compat, show-my-list's render filter accepts BOTH the new `commitment_resolved.data.commitment_id` AND legacy `thread_resolved.data.target_id` as valid closers, so in-flight items from pre-v3.11.4 still close correctly.)
- `command-room-onboarding` (v3.4.1+ — first-install Step 1 setup widget. Fire-marker event type: `onboarding_setup_widget_emitted` with `source_skill: command-room-onboarding` and `data.widget_kind` of either `step_1_setup` (pre-v2) or `step_1_setup_v2` (current, 2026-05-17+). v2 payload is exactly 3 tuples corresponding to widget items 1-3: role / email exclusions / timezone — item 1 may carry an optional `sub` field from the role question's sub-chip drill-down, and any item may carry an optional `input` from a refinement textbox. Pre-v2 (`step_1_setup`) payload was 4 tuples — role / day-to-day note / email exclusions / timezone — with no `sub` field. Dispatch routes to `skills/command-room-onboarding/SKILL.md` Section "Reply handling — Step 1 setup" — that section maps each `(action, sub?)` pair to the appropriate entities.json write, then resumes onboarding flow control at Step 2. The actions are selection labels, not action verbs from `CANONICAL_ACTIONS` — apply-choices Step 3 parses this payload but does NOT validate the actions against `CANONICAL_ACTIONS` for this source.)

To identify which one: read the most recent fire-marker event whose timestamp is within the last 60 minutes. That event's source field names the source orchestrator. apply-choices fires immediately after Apply all is clicked, so the timestamp gap is small.

If no recent fire-marker exists OR the source can't be determined: surface plain English:

> *"Couldn't tell which task this belongs to — too much time may have passed. Reply '#N action' for each one, or run the task again."*

Don't guess. Ambiguous source = high risk of dispatching the wrong handler.

### Step 3 — Dispatch each {n, action, input?} through the source orchestrator's handler

For each entry in the parsed array, route to the corresponding handler in the source orchestrator's "Reply handling" section.

**Input-bearing action handling:**

| Action pattern | Input shape | Handler uses input as |
|---|---|---|
| `edit then send`, `draft` (consolidated v2.14.4+; was previously two separate verbs) | object `{to, cc, subject, body}` | Override the metadata To/Cc/Subject and `body_lines` with the user's edited values. Then dispatch to the corresponding `send` or `draft` handler. Empty fields preserve the original. |
| `edit` (no bracket — non-email contexts only) | string | The full edited body text — replace `body_lines` with this verbatim. |
| `push meeting [date]`, `push to [date]`, `schedule catchup [when]` | string (natural language: "monday at 2", "tomorrow afternoon", "2026-05-12") | Parse the natural-language datetime via the same chrono / date-parser logic the orchestrator uses for free-text user replies. Empty input or unparseable string → surface item-level error in the consolidated ack ("couldn't read 'sometime soon' as a date — try again with a clearer time"). |
| `mark received` (no input — fires `commitment_resolved` directly) | — | No input expected. |
| `add as person to <Org Name>`, `add as new org <Org Name>` | optional string | The free-text content if user adds a note — record per orchestrator's handler. The specific org name is parsed from the action verb itself (per Rule 5 specific-name patterns). |
| `add context [text]`, `add more context [text]` (back-compat alias) | string | The free-text context to fold into the entity record. |
| `add [text]` (REVIEW affirmative) | optional string | Empty = accept inferred values; non-empty = orchestrator folds the text into the entity record. |
| `decide [text]` (Past Meetings decision-needed) | string | Free-text decision content — written to decision log. |
| `snooze [duration]` (deprecated v2.14.38+ alias) | string | Duration string — back-compat for in-flight pre-v2.14.38 widgets. New widgets emit `snooze 3d` (no input). |
| `confirm [type]` on entity proposal | string (optional) | Free-text corrections to inferred entity details. Empty = accept inferred values. |
| `edit [type]`, `edit [change]` | string | Free-text override of the inferred type / change. |
| `confirm` on entity proposal `[e1/e2]` | string (optional) | Free-text corrections to inferred entity details. Empty = accept inferred values. |

If a choice has an action that expects an `input` but none is present (user didn't type anything before hitting Apply), surface plain English in the consolidated ack — DON'T default to a placeholder:

> *"#N: needs a value — try again and fill in the field before submitting."*

**Per-action processing:** call each handler exactly once per `{n, action}` tuple, in the order they appear in the payload. If a handler errors mid-batch:
- Log the error to a local accumulator with `{n, action, plain_english_message}`.
- Continue to the next entry. Do NOT abort the whole batch.

### Step 3a — Person-record dispatch contract (v3.2+ MANDATORY)

Some actions create or update person records as a side effect:

- `add [text]` from `cr-past-meetings` REVIEW pending-items (sub-item Na/Nb/Nc) when the inferred entity is a `person` (e.g., `1a add Rio Sample to Category Company`)
- `confirm` on entity proposal `[e1/e2]` when the proposal is a person
- Any other handler dispatching to people-crm `create_person` / `update_person`

For these, **the dispatch MUST go through `shared/scripts/people_writer.py`. Hand-rolling JSON into `entities.json["people"]` is FORBIDDEN — zero tolerance.** This is the same anti-pattern lineage as the renderer ZERO-MANIPULATION CONTRACT (v2.14.34/.37): when a canonical helper exists, the agent uses it; "improvising the shape because it seems easier" is the bug.

The two real-world failures this gate prevents:

1. **Wrong field names.** The agent picks plausible-sounding adjacent keys (`display_name` instead of `canonical_name`, `current_org_id` instead of `primary_org_id`, `first_seen_at` instead of `first_seen`, `emails[]` plural, etc.). Different fires produce different shapes. Memorialized: `person_063` (Rio Sample, 2026-04-30) and `person_064` (Dustin Sample, 2026-04-26 — also a duplicate).
2. **No dedup.** The agent creates `person_064` Dustin Sample alongside the existing `person_004` Dustin Sample because no helper checks `entities.json` for a name/email match before appending.

**Dispatch flow** (any apply-time handler that ends in a person create/update):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from people_writer import create_person, update_person, find_existing_person, DuplicatePersonError, MultipleCandidatesError; print('OK')"
```

If stdout is not exactly `OK`, the apply-time accumulator records `{n, action, "couldn't save that one — try again in a moment"}` and the consolidated ack reports it under `Errors:`. Do NOT fall back to direct `entities.json` edits.

When the import gate passes:

1. **Dedup-first.** Call `find_existing_person(workspace_root, name=<extracted name>, email=<extracted email or None>, aliases=<any aliases the user supplied>)`.
2. **Branch on result — three outcomes (v3.13.7+):**
   - **Existing record (single safe match) → update.** `find_existing_person` returned a dict. Call `update_person(workspace_root, existing["id"], ...)` with whatever new fields the action carried (e.g., `last_interaction`, role correction, notes append). Surface in the consolidated ack: *"#N: Dustin Sample is already in your network — updated."*
   - **No match → create.** `find_existing_person` returned `None`. Call `create_person(workspace_root, canonical_name=..., primary_org_id=..., source_skill=<source_orchestrator>)`. Surface: *"#N: added Rio Sample under Category Company."*
   - **`MultipleCandidatesError` raised → disambiguation widget.** `find_existing_person` raised because the name was too ambiguous to safely commit (e.g., single-token "Daniel" hit an alias on an existing record but might be a different Daniel). Do NOT auto-pick. Render a disambiguation widget. See "Disambiguation widget shape" below. v3.13.7+ Bug #19 fix.
3. **Catch `DuplicatePersonError` defensively.** If the dedup helper missed a match (rare — usually means the caller didn't pass the matching alias / email), the writer's internal dedup catches it. Treat it as the "existing record" branch and surface the resulting id to the user.

**Disambiguation widget shape (v3.13.7+).** When `MultipleCandidatesError` fires, render a single-item widget per candidate plus a "different person — create new" affordance:

```python
data_view = {
    "widget_mode": "all_batch_widget",
    "header": f"I see {len(err.candidates)} person record(s) with this name. Which one is this?",
    "sub_header": f"You're adding {extracted_display_name!r}. Tell me which path to take.",
    "sections": [{
        "title": None,
        "items": [
            # One item per candidate
            *[
                {
                    "n": i + 1,
                    "icon": "👤",
                    "name": c.get("canonical_name", c["id"]),
                    "context_tag": f"existing record — {c.get('role') or '(no role tracked)'}",
                    "body_lines": [
                        f"This is {extracted_display_name}",
                        "  → update this record with the new context",
                    ],
                    "actions": [f"{i+1} confirm", f"{i+1} skip"],
                }
                for i, c in enumerate(err.candidates)
            ],
            # Plus a final item for "different person, create new"
            {
                "n": len(err.candidates) + 1,
                "icon": "✨",
                "name": f"Add {extracted_display_name} as a different person",
                "context_tag": "different person who happens to share a first name",
                "body_lines": ["This is NOT any of the records above — create new."],
                "actions": [f"{len(err.candidates) + 1} confirm", f"{len(err.candidates) + 1} skip"],
            },
        ],
    }],
}
```

On the user's click:
- `confirm` on an existing-record item → call `update_person(workspace_root, candidate_id, ...)` with the new context, log `person_disambiguation_resolved` event referencing the original ambiguous capture
- `confirm` on the "create new" item → call `create_person(..., skip_dedup=True, ...)` because the user has explicitly confirmed this is a separate person
- `skip` → no write, log `person_disambiguation_skipped` event so we don't re-ask on the next fire for the same input

Why we route through the user instead of guessing: Session-22 Bug #19 documented the exact failure mode of silent corruption. A "Bo (Acme Co)" creation auto-matched an existing Bo Sample record via that record's "Bo" alias. The test caught it before any write, but in production the silent update would have:
- Routed Acme-related commitments to the wrong person record
- Modeled the wrong relationship graph
- Made later "what did Bo say about X" queries return mixed results across two real people

The disambiguation widget is the structural defense — entity-graph trust trumps minor UX friction of asking the user once.

**The contract — what NEVER happens at apply time:**

- ❌ Direct write to `entities.json` via `path.write_text()` / `json.dump()` / `open()`
- ❌ Hand-rolled person record dict (regardless of whether it's then "passed to" the helper — the writer's keyword arguments are the ONLY entry surface)
- ❌ Skipping dedup and going straight to create
- ❌ Inventing extra keys (`first_seen_source`, `confidence`, `inferred_from` etc.) on the person record — those go to `events.jsonl`, not the entity

**Self-check:** before any person-write dispatch, ask: "did I import from `people_writer`? did I call `find_existing_person` first?" If either is no → STOP and re-route.

### Step 3b — Auto-org-attribution on person create (v3.13.0+ MANDATORY)

After Step 3a successfully **creates** a person record (existing-record / update path skips this), Step 3b fires the auto-attribution logic. This closes the long-standing gap where new people landed with no `primary_org_id` even when the capture event carried an explicit `org_hint`.

**The dispatch:**

```bash
# (after create_person returned the new person record)
python3 -c "
import sys
sys.path.insert(0, 'shared/scripts')
from org_writer import attribute_person_to_org
from people_writer import get_person_emails

# 1. Pull the work-domain candidates from the newly-created person
work_domains = []
for e in get_person_emails(new_person_record):
    if '@' in e:
        work_domains.append(e.rsplit('@', 1)[1].strip().lower())

# 2. Pull the org_hint from the original capture event (if any)
# This is the data.org_hint field on the person_pending_review event
# whose seq matches the apply-choices payload's source event.
org_hint = capture_event.get('data', {}).get('org_hint') if capture_event else None

# 3. Try to attach
org_record, reason = attribute_person_to_org(
    workspace_root,
    new_person_record['id'],
    work_domains=work_domains,
    org_hint=org_hint,
    source_skill='apply-choices',
)
print(reason)
"
```

**Branch on the result** (the `reason` string is suitable for the consolidated apply-choices ack):

- *"matched by work-domain {d} to existing org {name}"* → surface in ack: *"#N: attached Sam Sample to Acme Co."* Notify-after, not block-before.
- *"created new org {name} from capture hint and attached"* → surface: *"#N: added Acme Co and attached Sam to it."* No user prompt — the hint was explicit.
- *"no strong signal — left unattached"* → silent. The person record stands with `primary_org_id: null`. If the user later types context like "she's at Acme / her email is X" via the card-context flow (Step 3c, planned v3.14.x+), that path can attach.

**The contract — what NEVER happens at apply-time auto-attribution:**

- ❌ Auto-attach on a free-mail domain (gmail.com / yahoo.com / icloud.com etc.) — those are personal, not work. The helper filters these.
- ❌ Hand-rolling an org record into entities.json — must go through `org_writer.create_org`.
- ❌ Blocking the apply with a y/n prompt on strong signal. Strong signal = auto-apply with notify-after, per the M decision in the 2026-05-20 handoff. Weak signal = leave unattached (no prompt either; that's the propose-and-confirm flow's job, not this one).

**Failure handling:** if `attribute_person_to_org` raises (e.g., schema validation failure on a corrupted org record), catch it defensively. Log to the apply-choices errors list and continue — the person was still created successfully in Step 3a; missing the org link is a degradation, not a hard fail.

### Step 3c — Intro-followup-check dispatch (v3.13.2+ MANDATORY)

The Pulse intro-followup-check surface (per `orchestrator-dont-forget.md` Phase 4h) emits items with three domain-specific resolution verbs in its action set: `landed`, `didnt land`, `snooze 14d`. The renderer accepts these as canonical, but apply-time needs to write the right lifecycle event for each.

**Where to fetch `person_ids` from at apply-time:** the intro-followup-check item itself only carries `{intro_event_seq, scheduled_for, check_question}` (per intro-broker/SKILL.md). The two person ids the intro connected live on the PARENT `intro_made` event referenced by `intro_event_seq`. Apply-time procedure (v3.13.6+ — defensive across both write shapes):

```python
# Read the parent intro_made event to get the two person ids
import json
events = [json.loads(line) for line in events_jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
parent = next((e for e in events if e.get("seq") == intro_event_seq and e.get("type") == "intro_made"), None)
if parent:
    # v3.13.6+ canonical: top-level person_ids array
    person_ids = parent.get("person_ids") or []
    if not person_ids:
        # Pre-v3.13.6 back-compat: scalars under data.person_a_id / data.person_b_id
        data = parent.get("data") or {}
        a = data.get("person_a_id")
        b = data.get("person_b_id")
        person_ids = [pid for pid in (a, b) if pid]
else:
    person_ids = []
```

`person_ids` lives at the TOP level of the event (per `events.schema.json` event shape), not inside `data`. The new events you write follow the same convention (top-level `person_ids` array). The defensive `data.person_a_id` / `data.person_b_id` fallback only matters for in-flight pre-v3.13.6 intro_made events that haven't been re-emitted.

**Dispatch rules:**

- `<n> landed` → append an `intro_landed` event to events.jsonl:
  ```jsonl
  {"type": "intro_landed", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "resolved_by": "operator"}}
  ```
  Use the item's `source_event_seq` (the originating `intro_made` event's seq); the two person ids come from the parent event per the lookup above. The event closes the intro-followup-check loop — the intro counts as successful in relationship-graph queries and downstream intro-broker voice-sample logic.

- `<n> didnt land` → append an `intro_didnt_land` event, same shape (substitute the type):
  ```jsonl
  {"type": "intro_didnt_land", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "resolved_by": "operator"}}
  ```
  Useful signal for intro-broker's pattern detection — over time, "this counterparty type doesn't respond to this framing" becomes learnable.

- `<n> snooze 14d` → schedule a re-emit. Append an `intro_followup_check` event (NOT a `chat_dismissal`):
  ```jsonl
  {"type": "intro_followup_check", "person_ids": [<person_id_a>, <person_id_b>], "data": {"intro_event_seq": <source intro_made seq>, "scheduled_for": "<today + 14 days>"}}
  ```
  Pulse's next fire re-checks `scheduled_for` against today's date and re-surfaces the item when due. This differs from a standard 24h `chat_dismissal` — the snooze is a deliberate "check back in two weeks", not a "not now".

- `<n> skip` — standard `chat_dismissal` event (24-hour TTL), same as every other surface. The intro-followup-check re-surfaces tomorrow.

**Consolidated ack lines** (Step 4B surface) — one line per item:
- *"#N: marked Sam & Bo's intro as landed."*
- *"#N: logged Sam & Bo's intro as didn't land."*
- *"#N: snoozed Sam & Bo's intro check for 14 days."*
- (Skip: silent.)

**Why this lives in apply-choices** (not in the orchestrator's reply handler): apply-choices is the single dispatcher for ALL widget Apply submissions. The orchestrator-dont-forget Phase 4h surface emits the items; apply-choices owns the resolution. Putting handler logic in the orchestrator would duplicate the dispatch pattern + drift over time.

### Step 4 — Surface ONE consolidated chat turn (v2.14.0+ — TERMINAL vs DRAFT-PRODUCING split)

**Critical distinction (v2.14.0):** actions split into two categories. The category determines the response surface.

**TERMINAL actions** — user has already decided. Fire immediately, confirm and stop.
- `send` — fire send right now (uses canonical email body, or the user-edited body if `edit then send` carried `input` object)
- `edit then send` — apply input object, then fire send (ONE round, NOT two clicks). Per M's v2.13.2 testing: *"if you hit edit send — then make edits and hit apply, it should trigger the email sent without regenerating in response."*
- `draft` — save current body to Gmail Drafts immediately
- `draft` (consolidated v2.14.4+; was previously two separate verbs) — apply input, save to Drafts immediately
- `mark received`, `mark received all`, `mark done`, `mark paused`, `resolved`, `resolved [reason]`, `archive`, `keep paused`, `active` — fire the state change immediately. **v3.13.8+ cascade-close (Bug #51):** when the resolved item is a `commitment_to_discuss` wrapper from show-my-list (the source orchestrator's `kind` was `list`), look up `data.source_event_seq` on the wrapper. If it points at an unresolved `commitment` event, ALSO write a second `commitment_resolved` event for the source — `data.commitment_id = source_seq`, `data.resolved_via_wrapper_seq = wrapper_seq`. Both events use a `next_seq.next_seq()` call to reserve seqs. Surface to the user: *"Cleared the [item label] — and closed the underlying commitment."* Skip the cascade only if the source_event_seq points at a non-commitment event type (e.g., a `pending_review` or a previously-resolved commitment).
- `confirm`, `confirm [type]` — apply the proposed change immediately
- `escalate to memo` — fire memo-writer (it produces a memo .docx — see DRAFT-PRODUCING handling for the resulting doc)
- `accept`, `decline`, `decline [reason]` — fire the calendar response immediately
- `skip`, `skip all` — silent dismissal, no widget
- `snooze [duration]`, `snooze 3d`, `snooze 14d`, `keep`, `add to my list`, `not relevant` — record the state, no widget. For `add to my list`: write a `commitment_to_discuss` event to events.jsonl with `data.source_event_seq` pointing back to the originating item's source; show-my-list reads these later when the user types `show my list`. For `snooze 14d`: see "Intro-followup-check dispatch" below — this verb also schedules a future re-emit, not just a dismissal.
- `landed`, `didnt land` — intro-followup-check resolution verbs (v3.13.2+). Each writes a domain-specific lifecycle event. See "Intro-followup-check dispatch" subsection below.

  **v3.13.0+ — orphan-note carrier:** when the widget's `+ Add context` field had text but no action button was selected on that item, the renderer synthesizes a fallback `{n, action: "add to my list", context: "<typed text>"}` choice (see `chat_output_renderer.py` `crApplyAll` orphan-note capture). Apply-choices must store that `context` value on the resulting `commitment_to_discuss` event's `data.summary` field so show-my-list renders it under the item:

  ```jsonl
  {"type": "commitment_to_discuss", "data": {"source_event_seq": <int>, "summary": "<orphan-context text>", "via": "orphan_note_capture"}}
  ```

  When the choice carries NO `context` field (the user selected an action without typing a note), keep the legacy behavior: `data.summary` falls back to the source item's existing label/title. When the choice carries BOTH a `context` AND was paired with an action via the happy path (the user typed a note AND clicked an action — they go together), the note is stored as `data.note` on whatever event the chosen action produces (`commitment_resolved`, `chat_dismissal`, etc.) rather than synthesizing a duplicate `commitment_to_discuss`. The `context` field is the user's typed note no matter which path was used.

For terminal actions, the response is a SHORT plain-English ack (Step 4B below) — no widget re-render, no second-click required.

**DRAFT-PRODUCING actions** — user requested generation of a fresh artifact for review.
- `push meeting [date]`, `push to [date]` — generate reschedule email draft
- `draft re-engagement` — generate re-engagement email draft
- `follow-up call` — generate calendar-invite request draft
- `status check` — generate status-check email draft
- `propose [time]` — generate proposed-time email draft
- `schedule catchup [when]` — generate catchup-request email draft
- `context [text]` (v2.14.37+) — single unified context affordance for upcoming-meetings items. Intent-aware dispatch on the user's textarea content:
  - **Question-shaped** (text ends with `?` OR first word matches `^(what|why|how|when|who|which|is|are|was|were|did|does|do|will|can|could|should|would)\b` case-insensitive) → synthesize an answer using prior meeting transcripts + recent email threads with attendees + relevant decision-log entries. Returns 1-3 paragraphs in chat with source citations (meeting dates, email subjects). Routed via Step 4B (terminal-style ack — the answer text IS the response). Replaces the v2.14.14 `ask question [text]` handler.
  - **Otherwise (statement / instruction)** → re-run call-prep with the user's added context folded in. Regenerate the `.docx` brief via the docx skill. Routed via Step 4A (draft-producing — fresh brief link surfaces in the apply-time widget). Replaces the v2.12.4 `add more context [text]` handler.
- `add more context [text]` (v2.12.4 - v2.14.36 alias, accepted for back-compat) — translate to `context [text]` and dispatch through the intent-aware handler above. Pre-v2.14.37 widgets in flight at upgrade time will still apply correctly.
- `ask question [text]` (v2.14.14 - v2.14.36 alias, accepted for back-compat) — translate to `context [text]` and dispatch through the intent-aware handler above.
- `prep deep work` — generate context-loaded prompt (text content for review, not a send)
- `investigate` — generate cross-reference report (text content)

For draft-producing actions, the GENERATED CONTENT surfaces in a NEW widget so the user can review/edit/send each draft. Step 4A below.

**Mixed batch** (some terminal, some draft-producing): plain-English ack at top summarizing terminal outcomes, then the widget for drafts. Step 4C below.

#### A. Draft-producing actions only → emit a NEW widget

Per M's Apr 30 ask: *"You can host all of those in the same widget."* All draft-producing outputs go in ONE widget — never N separate widgets.

**Step 4A.1 — verify renderer imports (v2.13.0+ MANDATORY, bash-gated, full validator chain):**

Before producing the consolidated response, you MUST execute:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "import sys; sys.path.insert(0,'shared/scripts'); from chat_output_renderer import render_chat_output_widget, validate_chat_output, CANONICAL_ACTIONS, CanonicalActionError, LeakDetectedError; print('OK')"
```

If stdout is not exactly `OK`, ABORT and surface plain English: `(Couldn't put your drafts together just now — try again in a moment.)` Do NOT fall back to markdown rendering of the drafts. The widget IS the contract.

**v2.13.0 enforcement parity with orchestrators:** the renderer raises `CanonicalActionError` if the apply-time data view has a non-canonical action verb (e.g., the agent improvising `keep as draft` instead of `draft`, `send as is` instead of `send`, or numbered markdown actions like `send 1`). It raises `LeakDetectedError` if the rendered output contains forbidden patterns (per `shared/CONTRACT.md` Rule 4). Both blocking; no silent fallback to markdown.

**Action set (frozen — v2.14.4+ consolidated form):** the apply-time post-Apply widget for email drafts uses ONLY these action verbs (matching `CANONICAL_ACTIONS`):
- `<n> send`
- `<n> edit then send`
- `<n> draft`
- `<n> skip`

(Pre-v2.14.4 the set was `send` / `edit then send` / `to drafts` / `edit then draft` / `skip`. v2.14.4 consolidated `to drafts` + `edit then draft` → `draft` — the single verb always opens an edit-then-save flow before persisting to Gmail Drafts. The renderer rejects the legacy 5-verb shape.)

NOT allowed (rejected by validator): `keep as draft`, `send as is`, `revise`, bare `edit`, `regenerate`, `draft 1` / numbered-prefix shorthand, the legacy `to drafts` / `edit then draft`. If the agent is tempted to use any of these — STOP. Pick from the canonical set.

**Step 4A.2 — Build the consolidated post-Apply data view (single widget for ALL drafts/docs):**

```python
post_apply_data_view = {
    "widget_mode": "all_batch_widget",
    "header": "<plain-English summary, e.g. '3 drafts ready. 1 brief regenerated.'>",
    "sub_header": None,
    "sections": [{
        "title": None,
        "count": None,
        "items": [
            # ONE item per draft AND per regenerated document. Multiple drafts go in the
            # SAME widget — DO NOT emit separate widgets per draft. Per M's ask: "You can
            # host all of those in the same widget."
            {
                "n": 1,                       # NEW numbering for this widget — not the source #N
                "icon": "✉",
                "name": "<recipient name>",
                "subject": "<subject>",
                "context_tag": "draft from #<source_n>",
                "metadata": [("To", "<email>"), ("Subject", "<subject>")],
                "body_lines": [...],
                "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"],
                "original_thread": {...} or omit  # if the draft has a source thread
            },
            {
                "n": 2,
                "icon": "📄",
                "name": "<doc title>",
                "context_tag": "regenerated from #<source_n>",
                "artifact_link": {"label": "Open brief", "url": "<docx url>"},
                "actions": ["2 skip"]   # docs are read-only — only action is dismiss
            },
        ]
    }],
}
```

**Canonical email action set** (use these EXACTLY — do not improvise alternatives like `keep as draft`, `send as is`, `revise`):

| Action | Display | Behavior |
|---|---|---|
| `<n> send` | Send | Send via Zapier (if configured) → native Gmail threaded → standalone, per `EMAIL_DRAFT_PROTOCOL.md` §3c |
| `<n> edit then send` | Edit then send | Multi-field edit (To/Cc/Subject/Body) → send |
| `<n> draft` | Draft | Multi-field edit → save to Gmail Drafts via native MCP. v2.14.4+ consolidated `to drafts` + `edit then draft` into this single verb. |
| `<n> skip` | Skip | Discard the draft. No record kept. |

NOT allowed: `keep as draft` (use `draft`), `send as is` (use `send`), `regenerate` (re-fire the source orchestrator instead), bare `edit` (use `edit then send` or `draft`), the legacy `to drafts` / `edit then draft` verbs (consolidated v2.14.4+).

**Step 4A.3 — render and post:**

Call `render_chat_output_widget(post_apply_data_view)` and post via `mcp__visualize__show_widget`. The widget HTML IS the post — do NOT compose a markdown summary of what's in the widget. Same posting contract as the source orchestrators.

**Step 4A.4 — Post the chat-links section:**

After the widget, emit a SHORT markdown chat-links section per `_hq/CONVENTIONS_SOURCE_LINKS.md`:
- For each draft with an original thread → `[<recipient> — <subject>](<thread URL>)`
- For each regenerated doc → `[<doc title>](<docx URL>)`

Numbering matches the widget items.

**Step 4A.5 — STOP.** No trailing narration. No "I drafted X for Y because Z." No "Wrote audit event to events.jsonl." No commentary on which actions you ran or what the response handlers did. Per `shared/CHAT_ACTION_WIDGET.md` MUST NOT rule #5: the widget + Links is the entire post. (v2.12.5+)

#### B. Terminal actions only → emit a SHORT plain-English ack (NO widget re-render)

Per M's v2.13.2 ask: *"if you hit edit send — then make edits and hit apply, it should trigger the email sent without regenerating in response."* Terminal actions FIRE immediately. The response is a short confirmation, not a widget for a second click.

Specifically for `send` / `edit then send` / `draft` / `draft` (consolidated v2.14.4+; was previously two separate verbs):
- Take the canonical body OR the user-edited body (from `input` object) — never re-surface the body for review.
- Fire the actual send / save through the appropriate handler (Zapier first if configured, native Gmail threaded fallback, etc. per `EMAIL_DRAFT_PROTOCOL.md` §3c).
- **CRU pass (v2.14.6+, send only):** after the send is confirmed by the connector, run the cross-reference helper to detect whether this send fulfilled an open commitment owned by the user. See "CRU pass" section below for the exact bash flow. Silent — no chat narration regardless of outcome.
- Confirm with `✓ Sent at HH:MM — Re: <subject> → <recipient>` (one line per send) or `✓ Saved to Drafts — Re: <subject> → <recipient>`.
- NO widget. NO second click. ONE round.

##### CRU pass (v2.14.6+ — silent commitment auto-resolution on `send`)

Per `shared/scripts/cru_match.py` Path 1. Runs ONLY when a `send` action just fired successfully (`edit then send` counts; `draft` does NOT — drafts aren't fulfillment). Conservative auto-resolve only; borderline matches go to a `pending_review` queue surfaced in the next Pulse fire.

Skip entirely if any of:
- The send failed (no commitment can have been resolved by a failed send).
- Sender's `person_id` cannot be resolved from entities.json (the user record has `is_user: true` or `is_primary_user: true` — pull that record's `person_id`).
- No open commitments exist where the user is the owner (helper returns `[]`).

Otherwise, per send, execute via bash:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1); cd "$PLUGIN_ROOT"
python3 -c "
import sys, json
sys.path.insert(0, 'shared/scripts')
from cru_match import (
    load_open_commitments,
    match_send_to_commitments,
    build_commitment_resolved_event,
    build_pending_review_event,
)
from atomic_write import atomic_append_jsonl

events_path = '<absolute path to _hq/data/events.jsonl>'
opens = load_open_commitments(events_path)
results = match_send_to_commitments(
    open_commitments=opens,
    sender_person_id='<resolved sender person_id>',
    recipient_person_ids=['<recipient person_id 1>', ...],
    subject='<subject of the send>',
    body='<body of the send>',
)

# Reserve next seqs (writer helper adapter — use existing seq-reservation path).
next_seq = <peek-next-seq from events.jsonl tail>
to_append = []
for r in results:
    # v2.14.7+: HIGH-confidence → auto-resolve, MEDIUM → pending-review for
    # next Pulse fire to surface as a one-click confirm/skip item.
    if r['recommendation'] == 'auto_resolve':
        to_append.append(build_commitment_resolved_event(
            commitment_id=r['commitment_id'],
            resolved_by='<sender_person_id>',
            primary_thread_id=r['primary_thread_id'],
            source_skill='apply-choices',
            evidence=f\"Sent via Cowork — Subject: {<subject>}\",
            next_seq=next_seq,
        ))
        next_seq += 1
    elif r['recommendation'] == 'pending_review':
        to_append.append(build_pending_review_event(
            commitment_id=r['commitment_id'],
            primary_thread_id=r['primary_thread_id'],
            source_skill='apply-choices',
            proposed_resolution='auto_resolve',
            score=r['score'],
            evidence=f\"Sent via Cowork — Subject: {<subject>}\",
            next_seq=next_seq,
        ))
        next_seq += 1
if to_append:
    atomic_append_jsonl(events_path, to_append)
print(f'CRU: resolved={sum(1 for e in to_append if e[\"type\"]==\"commitment_resolved\")} pending={sum(1 for e in to_append if e[\"type\"]==\"commitment_review_proposed\")}')
"
```

**The stdout is for diagnostic logging only; it MUST NOT appear in chat.** Per CONTRACT.md Rule 4 forbidden-pattern list: `commitment_resolved` and `commitment_review_proposed` event-type names never appear in user-facing chat. The user sees the resolution effect on the next Commitments fire (the resolved item simply doesn't appear).

If the CRU pass errors (helper import fails, events.jsonl missing, etc.): swallow silently. The send already succeeded; the CRU pass is best-effort enrichment, not a blocking gate. **Append a `pack_run.data.errors[]` entry** (v3.5.0+) to the most recent pack_run event in events.jsonl: `{"phase": "apply_choices_cru_pass1", "reason": "<short>", "detail": "<truncated stderr>", "ts": "<ISO>"}`. Pre-v3.5.0 "log the error to telemetry" was prose with no concrete sink; v3.5.0 wires it to the canonical errors[] array so `usage report` surfaces these failures alongside the others.

Three lines max for the overall ack. NO internal jargon. NO IDs. NO file paths. NO event-type names. NO "logged X event written to Y." NO "Pack run complete." Just human language.

Examples (good):
- *"Done — 4 of 5 applied. Rio added under Category Company. Aspen logged for next Pulse. Item 4 needed a clearer date so it's still open."*
- *"Marked Adan items received (5 of 5). Nothing else outstanding from the Apr 8 call."*
- *"Pushed your 8:45 with Sam to next Saturday. Item 3 (Dustin Sample) skipped for 24 hours."*

Examples (forbidden):
- *"`person_063` added to `entities.json`, linked to `org_005`."* ← ID + file leaks
- *"`commitment_resolved` event written for #1 with `commitment_to_discuss` for #2."* ← event-type leaks
- *"Note: the Zapier-threaded send tool wasn't detected on this workspace, so the dispatcher fell through to native Gmail reply (preserved threading via thread_id). All three are continuations of the original threads, not new ones."* ← internal mechanics; on success this is forbidden trailing narration. Only surface the Zapier-not-detected note if a SEND ACTUALLY FAILED. On success, the path used is internal.

#### C. Mixed batch (some terminal, some draft-producing)

Plain-English ack line at top summarizing terminal outcomes. Widget below with the draft-producing outputs. Example:

> *"Pushed #4 to Saturday. #6 marked received. Drafts ready below for #1, #3."*
>
> [WIDGET: 2 items — Sam reschedule draft + Adan chase draft]

Same forbidden-pattern rules apply (no leaks, no version refs, no event-type names).
- *"Pack run complete. 4 dispatch events appended to `_hq/data/events.jsonl`."* ← internal narration + file path
- *"Per v2.12.0+ protocol, post-widget chat-links section emitted."* ← plugin-version leak

**MANDATORY (v2.13.0+):** before posting the plain-English ack, run `validate_chat_output(ack_text)` from `chat_output_renderer.py`. It raises `LeakDetectedError` if any forbidden pattern matches. ABORT the post and rewrite the offending sentence. NEVER catch the error and post anyway. The contract is the contract.

### Step 5 — Append the audit-trail event (silent)

Single audit entry per Apply submission. The user does not see this; it goes to the events store. NO mention of it in chat output.

### Step 6 — Mixed batches

If the batch produces BOTH drafts/docs AND non-draft outcomes (e.g., 3 sends, 1 push, 2 skips), do BOTH:
- The widget surfaces the 3 drafts (per Step 4A)
- The plain-English line above the widget summarizes the non-draft outcomes ("Pushed #4 to Saturday. #5 and #6 skipped.")

The widget body is for things the user can still act on. The plain-English line above is for things already done.

## Forbidden behaviors (v2.12.4+ — REINFORCED)

- **No echoing the payload.** `apply choices: [{...}]` is wire format. Don't repeat it back.
- **No internal-jargon leaks** in the consolidated ack. Run `scan_for_id_leaks()` first.
- **No re-rendering the original widget.** apply-choices fires AFTER Apply; the source widget is done.
- **No partial replies.** Heavyweight outputs go in the widget OR the consolidated ack — not as separate chat turns.
- **No interaction-mid-batch.** Don't ask "should I continue?" partway through. The user already confirmed.
- **No silent dropping.** Every `{n, action}` tuple must succeed (in widget/ack) or fail (in errors line).
- **No retries.** If a handler errors, log and move on.
- **No version / protocol references.** "Per v2.12.4+ protocol..." is forbidden — that's developer-speak. The user doesn't know what version they're on.

## Why this exists

The all-batch button widget pattern (per `shared/CHAT_ACTION_WIDGET.md`) collects N selections in widget local state and submits them as ONE `sendPrompt` payload. apply-choices is the receiving end.

v2.12.4 extends the contract: Apply-time outputs that produce drafts or documents get the SAME widget treatment as the source orchestrator. Per M's Apr 30 ask: edit + send inline beats round-tripping through plain markdown.

## Compatibility / fallback

If the renderer is running in fallback typed-number-row mode (legacy), apply-choices is irrelevant — users type `N action` per line and the orchestrator's existing reply handler fires.

## See also

- `shared/CHAT_ACTION_WIDGET.md` — canonical widget spec.
- `shared/scripts/chat_output_renderer.py` — `render_chat_output_widget()` is the producer.
- Per-orchestrator "Reply handling" sections — the handlers this skill dispatches to.
- `_hq/CONVENTIONS_EMAIL_PREVIEW.md` — email draft format (used inside the post-Apply widget).
- `_hq/CONVENTIONS_SOURCE_LINKS.md` — source-link format (used in the post-Apply chat-links section).
