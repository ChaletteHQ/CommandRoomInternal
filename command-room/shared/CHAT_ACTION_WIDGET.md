# Chat Action Widget — All-Batch Surface (v2.10.9+)

> Canonical spec for the action surface across every Command Room scheduled-task skill (Pulse, Commitments, Inbox, Past Meetings, Upcoming Meetings) AND on-demand `meeting-notes` Step 9 OPEN ITEMS section.

## Why this exists

Pre-v2.10.9 each per-item action row was a typed-number text format: `▸ 1 send  ▸ 1 to drafts  ▸ 1 edit  ▸ 1 skip`. The user typed `1 send` to fire an action. M tested an inline-button alternative on Apr 29 (probe: `plugin-source-v2/skills/probe-widget/`) and found:

- Buttons work in production Cowork chat (multi-click during streaming, post-response click, post-refresh click — all PASS).
- Per-click `sendPrompt` triggers Cowork's auto-scroll to the response. For multi-item resolution flows (close 4 items in a row), this means click → scroll-down → scroll back up → click → repeat. Annoying.

**Verdict:** use `show_widget`-rendered button surfaces with **all-batch close** — clicks accumulate in widget local state, one "Apply all" button fires one consolidated `sendPrompt` at the end. Single scroll, single ack, all choices logged in one batch.

Trade-off accepted: heavyweight actions that produce content (send email, draft reply, expand brief) don't show their output until after Apply. M chose all-batch over a mixed mode (lightweight batch + heavyweight per-click) for v1 simplicity. Revisit per-skill if specific surfaces feel wrong with all-batch.

See `PROBE_RESULTS_past-meetings-open-items.md` (workspace root) for the test results that drove this decision.

## The widget shape

Each scheduled-task skill calls `mcp__visualize__show_widget` with HTML that renders:

```
┌─────────────────────────────────────────────────────────┐
│ [Skill header — e.g. "Commitments — 8 total open"]       │
├─────────────────────────────────────────────────────────┤
│ 1. ✉ Sam Sample — "Re: Workspace Map renderer..."   │
│    Originally: [Apr 14 thread](https://...)              │
│                                                          │
│    > **To:** [sam@example.com](mailto:...)    │
│    > **Subject:** Re: Workspace Map renderer diagnostic  │
│    >                                                     │
│    > Hey Sam — Got your renderer-pipeline...          │
│                                                          │
│    [prep deep work] [send] [edit then send] [draft] [push to] [skip]  │
├─────────────────────────────────────────────────────────┤
│ 2. ✉ Quinn (Aspen Hardware) — vendor team account...      │
│    ... (same pattern) ...                                │
├─────────────────────────────────────────────────────────┤
│ ... more items ...                                       │
├─────────────────────────────────────────────────────────┤
│ Quick read: [if applicable]                             │
│                                                          │
│ Selections so far: 0 of 8                                │
│ [ Apply all ]   [ Clear ]   [ Skip all ]                 │
└─────────────────────────────────────────────────────────┘
```

### Per-item buttons

Each item gets a horizontal button group containing the same actions that used to be the typed-number row. Click behavior:

- **First click on a button:** widget records the choice for that item locally, button gets a "selected" visual state (bold border, color change, checkmark badge).
- **Click a different button on the same item:** previous choice is replaced (one action per item — can't both `send` and `skip` the same item).
- **Click the same button again:** deselects (item returns to "no choice yet" state).

### Counter

Below the items, a counter shows `Selections so far: X of N`. Increments with each unique-item selection, decrements on deselection.

### Bottom row buttons

- **Apply all** — fires one `sendPrompt` with all current selections (see "Submission format" below). Disabled when 0 selections.
- **Clear** — resets all per-item selections to "no choice yet." No `sendPrompt`. Local state only.
- **Skip all** — selects `skip` for every unselected item, then fires Apply automatically. Convenience for "I've reviewed; mark the rest dismissed."

### What the widget does NOT do

- **Does not pre-select anything.** Every item starts with no choice. M has to actively select.
- **Does not auto-fire on click.** Per-button clicks are local state changes only. Submission is gated on the Apply all button.
- **Does not persist state across refreshes.** If the user refreshes the chat page, all selections are lost (the widget re-renders with fresh state). This is acceptable per the all-batch design — M closes items in one sitting; if interrupted, he starts over.
- **Does not show heavyweight action output inline.** Drafts, brief expansions, and email sends produce their visible output AFTER Apply, in the response chat turn. The widget itself stays compact.

## Submission format

When **Apply all** fires, the consolidated `sendPrompt` carries the canonical batch shape:

```
apply choices: [{"n":1,"action":"send"},{"n":2,"action":"prep deep work"},{"n":3,"action":"push to 2026-05-05"},{"n":4,"action":"skip"}]
```

The serialization is JSON inside the `apply choices:` prefix. The receiving skill (see "Receiving skill" below) parses the JSON, dispatches each choice through the same per-action handlers the typed-number row used to call.

For grouped items with sub-letters (e.g. `7a`, `7b` in commitments), the `n` field carries the full identifier:

```
apply choices: [{"n":"7a","action":"mark received"},{"n":"7b","action":"mark received"},{"n":"7","action":"send"}]
```

For actions that take a parameter (`push to [date]`, `resolved [reason]`, `context [text]`), the parameter is part of the `action` string. The widget collects the parameter via an inline input that appears when the user clicks the button (small popover with a date picker for date params; small text input for free-form params).

## Receiving skill

A new skill `apply-choices` lives at `plugin-source-v2/skills/apply-choices/SKILL.md`. (Not yet implemented as of 2026-04-29 evening — flagged in `HANDOFF_cr-plugin-feedback-v2-10-9_2026-04-29.md` as a v2.10.9 follow-on build.) Its job:

1. Parse the JSON array from the `apply choices:` prefix.
2. For each `{"n", "action"}` tuple, dispatch through the same handler the original typed-number row would have called. The orchestrator that emitted the widget cached the per-item context (recipient, subject, draft body, etc.) — `apply-choices` reads that cache to know what `1 send` means in the current context.
3. Return one consolidated chat ack: `✓ Applied N choices: send (3), draft (1), skip (4).` Plus any heavyweight action output (sent email confirmations, draft bodies, expanded briefs) under a dedicated section.

As of v2.10.9 end-of-day, `apply-choices` is built and all 5 orchestrators + `meeting-notes` Step 9 emit the widget directly via `mcp__visualize__show_widget`. No fallback typed-number-row path remains in production code — that mode existed only during the v2.10.9 build window before the renderer + apply-choices shipped.

## Post-widget chat-links section (v2.12.0+)

Hyperlinks inside the widget's iframe are unreliable — Cowork's iframe sandbox blocks `computer://` links and even some `https://` links don't fire on click consistently. **Hyperlinks in regular chat markdown work reliably** (verified Apr 30 by M).

So the architecture is:
- **Widget HTML** = action surface only (buttons + inputs). NO source/brief links inside.
- **Chat markdown post AFTER the widget** = links section. Per-item source thread URL, per-item brief `.docx` URL, all clickable.

### Format

After `mcp__visualize__show_widget` posts, emit a second chat turn with a markdown links block:

```markdown
**Links:**

1. [Sam Sample — Framing the build](https://mail.google.com/mail/u/0/#all/198abc...) · [📄 brief](computer:///c%3A/Users/.../Past_Meeting_Sam_2026-04-30.docx)
2. [Lyra Sample — Acme Research](https://mail.google.com/mail/u/0/#all/198def...)
3. [Adan Sample — Apr 14 kickoff call](https://notes.granola.ai/d/abc...) · [📄 brief](computer:///c%3A/Users/.../Past_Meeting_Adan_2026-04-14.docx)
```

Numbering matches the widget's item numbering exactly so the user can map widget items → links.

### Per-orchestrator content

| Orchestrator | Source link | Brief link |
|---|---|---|
| `cr-past-meetings` | Granola transcript (`notes.granola.ai/d/{note_id}`) | `.docx` brief in `[Project]/meetings/Past_Meeting_<slug>_<date>.docx` |
| `cr-upcoming-meetings` | Calendar event (`calendar.google.com/calendar/event?eid=...`) | `.docx` prep in `[Project]/meetings/Call_Prep_<slug>_<date>.docx` |
| `cr-inbox` | Gmail thread (`mail.google.com/mail/u/0/#all/{thread_id}`) | n/a |
| `cr-commitments` | Gmail thread (chase email source) OR Granola transcript (commitment source) | n/a |
| `cr-dont-forget` | Open-context link (varies — Gmail thread, Granola, Drive doc) | n/a |
| `meeting-notes` (on-demand) | Granola transcript | `.docx` brief |

### URL conventions

Use `_hq/CONVENTIONS_SOURCE_LINKS.md` as the canonical source-link format:
- Always prefer URLs returned by the connector (`get_thread`, `get_meeting_transcript`, `get_event`) over synthesized ones
- Local `.docx` files: `computer:///<URL-encoded-absolute-path>` per workspace file-handling rules (M confirmed Apr 30 these work in regular chat)
- Format: `[Title — date](URL)` — but the post-widget-links shape uses item numbering instead of dates: `<N>. [Title](URL) · [📄 brief](URL)`

### When to omit

- If the orchestrator produced 0 meetings/threads/items with linkable sources, omit the entire Links block.
- If a specific item has no source URL (e.g., a self-commitment with no email thread), render its line without the source link: `4. (no source) · [📄 brief](...)` or just skip the entry.

### Drop `present_files` in v2.12.0+

The post-widget markdown links section replaces `mcp__cowork__present_files` cards. Cards are visually separated from the chat content; markdown links sit naturally inline and match Sam's Apr 30 ask: *"those briefs hyperlinked right under that UI."* `present_files` calls in orchestrator phase 6/9 specs are removed v2.12.0+.

## Action reference — what each button does (v2.12.2+)

User-facing reference. Every action label across all surfaces, with semantics. Action labels are stored lowercase as `data-action` attribute (canonical for parsing); display is title-cased at render time.

### Email-shaped items (Inbox, Commitments YOU OWE / OWED TO YOU)

| Action | Display | What it does |
|---|---|---|
| `send` | Send | Compose+send the current draft as-is. Zapier first if configured (best thread fidelity); falls back to native Gmail threaded; standalone last resort. Works without Zapier. |
| `edit then send` | Edit then send | Widget exposes textarea pre-populated with body. User edits inline. Apply submits the edited body via `send`. Single round. |
| `add email then send` | Add email then send | (v3.13.8+ Bug #44 recovery verb) Widget exposes a single-field email-address input. On submit, updates the To: field on the item + transitions to enabled `send`. Use when the recipient is identified (resolved person record) but no actionable email exists. Writes `contact_email_captured` event with `data.person_id` for downstream people-CRM persistence. |
| `draft` | To drafts | Save current draft to Gmail Drafts as-is. |
| `draft` (consolidated v2.14.4+; was previously two separate verbs) | Edit then draft | Widget exposes textarea. User edits. Apply saves the edited body to Gmail Drafts. |
| `escalate to memo` | Escalate to memo | Promote to memo-writer skill — generates a longer-form `.docx` memo when an email reply isn't enough. |
| `skip` | Skip | Dismiss for 24h. Resurfaces tomorrow. |

### Commitments YOU OWE only

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generates a context-loaded prompt to paste into a new task. For when you want to dig in and do work on the commitment. No email/send. |
| `push to [date]` | Push to | Widget exposes a free-text natural-language input ("monday at 2", "next thursday afternoon"). Reply handler parses on apply. Records deferral, updates the draft to mention the new date. (v2.12.4+ — replaces strict date picker.) |
| `resolved` | Resolved | Mark commitment fulfilled. Won't surface again. **(Distinct from `skip`, which is 24h dismissal.)** Renamed v2.12.3 from `close`. |

### Commitments OWED TO YOU only

| Action | Display | What it does |
|---|---|---|
| `follow-up call` | Follow-up call | Drafts a calendar-invite request for a quick 15-min sync instead of an email chase. |
| `mark received` | Mark received | Mark the commitment as fulfilled by the counterparty. Writes `thread_resolved` event. |
| `mark received [a/b/c/all]` | Mark received [letter] | For grouped chases (one chase email covering N items), mark only the specific sub-items received. `mark received all` closes the whole group. |

### Commitments grouped sub-items (7a, 7b, 7c…)

When ONE person owes you multiple things in the same fire (e.g., five outstanding deliverables from one Apr 14 call), the orchestrator collapses them into a single "Circling back on a few things" chase email with sub-items per individual deliverable. Each sub-item gets its own action row:

| Action | Display | What it does |
|---|---|---|
| `mark received` | Mark received | Mark THIS specific sub-item as received. Doesn't affect siblings. |
| `skip` | Skip | Dismiss this sub-item for 24h. |

### Self-commitments (no email — you owe yourself)

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generate a deep-work prompt as above. |
| `push to [date]` | Push to | Defer the self-commitment. |
| `mark done` | Mark done | Same as `close` but for self-commitments. |
| `skip` | Skip | 24h dismissal. |

### Pulse — person dormancy/pattern-break

| Action | Display | What it does |
|---|---|---|
| `investigate` | Investigate | Fires `tell me about [name]` — pulls cross-references from across your data. Read-only. Use when you want context, not action. |
| `draft re-engagement` | Draft re-engagement | Generates a re-engagement email draft to the person. The draft surfaces in the apply-time widget with Send / Edit then send / etc. (v2.12.4+) |
| `schedule catchup [when]` | Schedule catchup | Widget exposes a free-text natural-language input. User types "next Tuesday afternoon", "this Friday at 4pm", "sometime next week". Drafts the request email + creates tentative invite if a specific time was given. (v2.12.4+ — was no-input in earlier versions.) |
| `resolved` | Resolved | State change. Suppresses the alert for 14 days. NO input affordance, NO textarea — clean one-click. (v2.14.1+ unified with Commitments YOU OWE `resolved`; v2.12.4 had `resolved [reason]` with a textarea, but Bo's Apr 30 testing flagged the surprise input as confusing — dropped per CONTRACT.md Rule 6 on action-label clarity.) Same display label, same behavior, same mental model as Commitments: "this isn't open anymore." |
| `snooze [duration]` | Snooze | Snooze the alert for the picked duration (`7d`, `14d`, `30d`, etc.). |
| `skip` | Skip | 24h dismissal. |

### Pulse — stale-active project

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generates a context-loaded prompt for revisiting the project (last 14 days of events + last decision + open commitments). For when YOU want to do work on it. |
| `investigate` | Investigate | Fires `tell me about [project]` — pulls cross-references and surfaces what you might be missing. Read-only. |
| `mark paused` | Mark paused | Move the project to paused status. Drops out of Pulse alerts. |
| `status check` | Status check | Drafts an internal status-check email TO whoever owns the project. For when YOU haven't been driving and want someone else's update. |
| `snooze [duration]` | Snooze | Snooze the alert. |
| `skip` | Skip | 24h dismissal. |

### Pulse — pending people-record review (a, b, c…)

When the synthesis layer detects a low-confidence change to someone's record (new role, new org, etc.), it surfaces here for confirmation:

| Action | Display | What it does |
|---|---|---|
| `confirm` | Confirm | Apply the proposed change to the person record. |
| `edit [change]` | Edit | Widget exposes textarea. User types the corrected value. Applied as the change instead of the proposed one. |
| `snooze [duration]` | Snooze | Suppress this specific proposal for the picked duration (`7d`, `14d`, `30d`). Won't re-surface until the snooze expires. (v2.14.5+ — finish-cluster consistency.) |
| `skip` | Skip | Reject the proposal. 30-day cooldown — won't re-surface for 30 days. |

### Pulse — dormant transition proposal (d1, d2…)

When an active project has been quiet 30+ days, surfaces here to ask if it should move to Dormant status:

| Action | Display | What it does |
|---|---|---|
| `active` | Active | Keep the project active. 14-day cooldown — won't re-propose for 14 days. |
| `keep paused` | Keep paused | Already paused, no change. |
| `archive` | Archive | Skip the dormant step entirely; archive the project (it's effectively closed). |
| `snooze [duration]` | Snooze | Suppress this specific proposal for the picked duration. (v2.14.5+ — finish-cluster.) |
| `skip` | Skip | 24h dismissal; re-surfaces tomorrow. (v2.14.5+ — finish-cluster.) |

### Pulse — entity proposal (e1, e2…)

When passive ingestion detects a new org or project from your activity. The Phase 8 context_tag NAMES the candidate explicitly and explains what Confirm does (e.g., "Track Acme Co as a prospect org? Email domain acme.example.com seen in 5 threads.") instead of generic "new org candidate" framing — per M's v2.14.5+ ask: a generic "Add as new org" button doesn't tell the user WHICH org or WHAT tracking it does.

| Action | Display | What it does |
|---|---|---|
| `confirm [type]` | Confirm | Click opens a textarea pre-populated with the inferred entity details (name, domains, relationship_type, scope, signal). Empty submit = accept inferred. Type corrections to override before writing. |
| `edit [type]` | Edit | Same textarea opens; intended for cases where the user wants to flip the inferred relationship_type (vendor → client, prospect → partner, etc.) before confirming. (v2.14.5+) |
| `snooze [duration]` | Snooze | Suppress this proposal for the picked duration. Won't re-propose until the snooze expires. (v2.14.5+ — finish-cluster.) |
| `skip` | Skip | 60-day cooldown — won't re-surface for 60 days. Use when the proposal is wrong or you don't want to track this entity. |

### Past Meetings — new-person sub-item (1a, 1b, 1c, …)

When a meeting mentions one or more new people, **each person gets their OWN sub-item** (v2.12.4+ — Rio Sample goes to 1a, Rio Lange to 1b, etc.). Never stacked as competing actions on a single sub-item.

| Action | Display | What it does |
|---|---|---|
| `add as person to <Org>` | Add as person to <Org> | Specific-org variant — create a person record under the named org. Used when the meeting / signal makes the org clear. |
| `add as person to [org]` | Add as person to org | Generic — widget exposes textarea for org name. Used when the org isn't determinable. |
| `add as new org <Org Name>` | Add as new org <Org Name> | (v2.14.5+) Specific-name variant — create the named org as a new entity, with inferred relationship_type pre-populated. Used when the candidate org name is inferable from email domain, transcript mention, or signature block. |
| `add as new org` | Add as new org | Generic fallback — opens the same flow but prompts for the org name. Used when the candidate name isn't determinable. |
| `add context [text]` | Add context | Widget exposes textarea for free-form context (where you met, role, etc.). On Apply, opens an interactive entity-creation flow seeded with that context. |
| `add to my list` | Add to my list | Flag for later review. Surfaces via `show my list` (the retrieval trigger uses the same noun). |
| `skip` | Skip | 24h dismissal. |

(Removed in v2.12.4: `search emails` — per M's Apr 30 ask. Use `tell me about [name]` directly for cross-reference.)

(Renamed in v2.14.19: `add to list` → `add to my list` to match the `show my list` retrieval trigger. The display label "Log to discuss" was also retired in this rename — single noun across the loop, no guessing.)

### Past Meetings — vague-timing sub-item (1b)

When a meeting mentioned a commitment without a clear due date:

| Action | Display | What it does |
|---|---|---|
| `set date [when]` | Set date | Free-text natural-language date ("monday at 2", "next Thursday"). Sets a specific due date. |
| `add to my list` | Add to my list | Flag for later review. |
| `skip` | Skip | 24h dismissal. |

### Upcoming Meetings (per meeting)

| Action | Display | What it does |
|---|---|---|
| `context [text]` | Context | (v2.14.37+) Single unified context affordance — replaces `add more context [text]` + `ask question [text]`. Widget exposes a textarea; user types anything (background context, talking points, a question, an instruction). On Apply, the handler routes intent-aware: question-shaped input synthesizes an answer using prior meeting transcripts + recent emails with attendees + relevant decision-log entries (1-3 paragraphs with source citations); statement-shaped input re-runs call-prep with the added context folded in and regenerates the `.docx` brief. Per M's 2026-05-07 evening ask: *"we only need a 'Context' button… just one option that opens that up for you to interact how you wish."* |
| `add more context [text]` | Add more context | (v2.12.4 - v2.14.36 alias, retained for back-compat) Translates at apply time to `context [text]`. New widgets emit `context [text]`; pre-v2.14.37 widgets in flight at upgrade still dispatch correctly. |
| `ask question [text]` | Ask question | (v2.14.14 - v2.14.36 alias, retained for back-compat) Translates at apply time to `context [text]`. |
| `push meeting [date]` | Push meeting | Widget exposes a free-text natural-language input ("monday at 2", "tomorrow afternoon", "2026-05-12"). The reply handler parses the natural language at apply time. Drafts the reschedule email; surfaces in the apply-time widget with Send / Edit then send / etc. (v2.12.4+ — replaces strict date picker.) |
| `skip` | Skip | 24h dismissal. |

### Bulk row (every surface)

| Action | Display | What it does |
|---|---|---|
| `send all` | Send all | Sequential sends across all non-noise items. |
| `to drafts all` | To drafts all | Bulk save to Gmail Drafts. |
| `show more` | Show more | Re-render with top 10 instead of top 5. |
| `skip all` | Skip all | Bulk 24h dismissal. |

### Why so many actions?

Every surface has 5–8 buttons because each one corresponds to a different real decision. Pre-v2.11.x the typed-number row exposed all of them as text; v2.11.x widget made them clickable; v2.12.x consolidates redundant variants (`edit firmer`/`edit softer` removed; `edit` + disposition combined; `keep` standardized to `skip`). The set is now near-minimal — every button is a meaningfully different action.

## Posting contract — what the orchestrator MUST do, MUST NOT do (v2.11.3+)

**MUST do:**

1. Build the data view, set `widget_mode: "all_batch_widget"`, call `render_chat_output_widget(data_view)` to get HTML.
2. Post the HTML via `mcp__visualize__show_widget`. **The widget HTML is the entire user-facing surface for the items.** No accompanying markdown narration, no "here's what you can do" prose, no recap of the widget's button labels.
3. After the widget posts, if any `.docx` deliverables were produced this fire (briefs, prep docs, etc.), call `mcp__cowork__present_files` ONCE with an array of all absolute paths. Cowork emits inline file cards beneath the widget, named by the source filename (which already includes the meeting / project slug). This is the ONLY mechanism for clickable file surfaces — `computer://` links inside the widget HTML do not work (iframe sandbox blocks them).

**MUST NOT do:**

1. **Do NOT narrate or paraphrase the widget's behavior in chat.** Lines like *"Click any action button per item, then Apply all to fire..."* are forbidden. The widget's footer counter + Apply button are self-explanatory; explaining them in markdown text duplicates the surface and signals fallback-mode behavior. If you ever feel like writing prose about what the buttons do, STOP — emit the widget and trust it.
2. **Do NOT leak internal routing metadata into chat.** Forbidden patterns include `Domain match: x@y.com → Org Name (project_NNN, active)`, `Routing: stage 3 of 5`, `Confidence: 0.87`, `entities.json line 142`, internal entity IDs (`person_NNN`, `project_NNN`, `org_NNN`), event seq numbers, file paths under `_hq/staging/`, debug strings, "phase 4" labels. These are internal mechanics and never appear in user-facing output. The user sees PEOPLE NAMES and PROJECT NAMES — never the substrate.
3. **Do NOT include brief / .docx links inside the widget HTML.** The widget's `_render_widget_item` no longer renders `artifact_link` (v2.11.3+). The `artifact_link` field stays in the data shape so the orchestrator can collect paths for `present_files` — it does not appear in the widget body.
4. **Do NOT fall back to markdown narration if `mcp__visualize__show_widget` is unavailable.** ABORT the fire and surface plain English: `(Widget surface unavailable — re-fire when the visualize MCP is reachable.)` Do not improvise a text-based action surface. The pre-flight check (`from chat_output_renderer import render_chat_output_widget; print('OK')`) catches the renderer-import case; if `show_widget` itself is missing, surface the same plain-English abort.
5. **Do NOT post any commentary AFTER the widget + Links section.** No "Surfaced 5 items from this morning's scan." No "Cadence math is in early-baseline mode for almost everyone." No "Diversification rule pulled X into slot 1." No "Wrote pattern_break_detected × 5, dont_forget_run, pack_run to events.jsonl." No "Backup at events.YYYY-MM-DDTHHMM.dont_forget.bak.jsonl." No "I noted that in the Quick read." NO commentary at all about what you observed under the hood, what you wrote, what backups you made, what scoring decisions you made, what diversification did. (v2.12.5+ — per M's Apr 30 ask: *"this technical stuff should not show up."*)

   The widget IS the surface. The Links: section IS the source-link layer. After those two, the chat turn is DONE. If you want to write any of that diagnostic / process commentary, write it to `events.jsonl` as a `pack_run.notes` field (silent per Rule 9) — never to chat.

   This applies even if you think a note adds value ("FYI the Acme Co items a+e1 are the same signal"). The user can see that themselves; if they can't, the orchestrator's data-shape build is the bug — fix it there (merge the items) instead of explaining it after the fact.

**Why these rules exist:** v2.11.0/v2.11.1/v2.11.2 surfaced cases where (a) brief paths inside widget rendered as unclickable text masquerading as links, (b) inbox orchestrator emitted both a widget AND a markdown paragraph describing the widget, (c) upcoming-meetings dumped routing metadata like `Domain match: sam@example.com → Category Company (project_002, active)` into chat. v2.11.3 closes these as forbidden patterns.

## Renderer (shipped v2.10.9 EOD)

`shared/scripts/chat_output_renderer.py` exposes two functions:

1. **`render_chat_output(data)`** — original markdown renderer. Returns markdown string. Still used for any non-action-widget chat post (status messages, error notes, plain confirmations).
2. **`render_chat_output_widget(data)`** — v2.10.9+ widget renderer. Returns self-contained HTML for `mcp__visualize__show_widget`. Triggered when `data_view["widget_mode"] == "all_batch_widget"`.

Each per-item button label is pulled from the existing `actions` array on each item. The `_strip_action_n_prefix()` helper strips the `N ` / `Na ` prefix from action strings (since fallback text was `1 send` and the widget displays just `send`).

All 17 existing renderer tests still pass — markdown mode unchanged by the v2.10.9 widget addition.

## Per-skill notes

| Skill | Surface | Notes |
|---|---|---|
| `orchestrator-dont-forget.md` | Top-N cracks + REVIEW section | All actions batch. Sub-letter items (`a/b/c`, `d1/d2`, `e1/e2`) batch alongside main items in the same widget. |
| `orchestrator-commitments.md` | YOU OWE + OWED TO YOU | All actions batch. Grouped items (sub_items `7a`, `7b`, etc.) get individual checkboxes within the parent item's button group. |
| `orchestrator-inbox.md` | Top of pile | All actions batch. The `escalate to memo` action is heavyweight — fires its memo content inline AFTER Apply, not before. |
| `orchestrator-past-meetings.md` | Per-meeting cards | All actions batch. Pending review sub-items (`Na`, `Nb`, `Nc`) batch within parent. |
| `orchestrator-upcoming-meetings.md` | Per-meeting cards | All actions batch. The `open SLUG` action expands the brief inline AFTER Apply. |
| `meeting-notes/SKILL.md` Step 9 | OPEN ITEMS section | All actions batch. Decisions are NOT in the widget — they auto-log at processing time per the v2.10.9 4-section card spec. The widget only handles M-only resolution items. |

## Apply-time output contract (v2.12.4+)

The widget collects selections; the user clicks Apply. What happens AFTER apply produces drafts or documents needs the same shipping discipline as the original orchestrator output. Per M's Apr 30 ask: *"It is just the easiest way to just get an email out because you can edit and send inline if needed — like the original output."*

Three rules govern the post-Apply chat turn (enforced in `apply-choices/SKILL.md` Step 4):

### Rule 1 — Drafts and documents come back in the SAME widget format

If any apply-time action produces an email draft (push meeting, draft re-engagement, follow-up call, status check, propose time, schedule catchup, etc.) OR a regenerated document (add more context regenerating the brief, escalate to memo producing a memo .docx), those outputs render through `render_chat_output_widget` as a NEW widget — not as inline markdown.

The new widget's items use the same shape as email-shaped items: metadata, body_lines, original_thread (when relevant), action set `Send / Edit then send / To drafts / Edit then draft / Skip`. Documents render with `artifact_link` inline. The user can edit + send inline without retyping.

### Rule 2 — Mixed batches surface BOTH

If apply produces some drafts/docs AND some non-draft outcomes (mark received, push, skip), the response is:
- Plain-English line above: summarizes the non-draft outcomes ("Pushed #4 to Saturday. #5 and #6 skipped.")
- Widget below: surfaces the drafts/docs the user can still act on

### Rule 3 — Forbidden patterns extend to apply-time output

The MUST NOT list (forbidden patterns) above applies to the apply-time response too — not just the initial widget. Specifically: `apply-choices/SKILL.md` Step 4 must run `scan_for_id_leaks()` over the consolidated chat-ack text BEFORE posting and rewrite any sentence that triggers a leak.

New patterns added in v2.12.4 (beyond the v2.11.3 base set):
- Internal file paths under `_hq/` (data, views, deliverables, tmp — not just staging)
- Internal data files: `events.jsonl`, `entities.json`, `aliases.json`, `staging_emissions.jsonl`, `known-newsletters.txt`
- Internal session-notes file paths
- Internal event-type names in narration: "chat_dismissal event written", "pack_run complete", "X event logged to Y", etc.
- Plugin-version protocol references: "per v2.12.0+ protocol", "v2.10.9 spec", "post-widget chat-links section per ..."
- The literal `apply choices: [...]` payload string (must never appear as visible chat text — it's wire format)
- Internal narration patterns: "Now appending events to events.jsonl", "Pack run complete. N dispatch events appended.", etc.

See `chat_output_renderer.py` `_LEAK_PATTERNS` for the full regex set.

## Open questions (deferred — not v1 blockers)

- **Long sessions:** if M has 20+ items in a Pulse fire and only resolves 5 in this sitting, the other 15 disappear on next fire (because state doesn't persist across refresh). Should the widget offer a "save selections without applying" path? Layer-2 question.
- **Multi-skill batching:** if M has Commitments + Inbox + Pulse all open in the same chat, three separate widgets exist. Should there be a unified "Apply across all widgets" affordance? Probably not — adds complexity, M can just hit Apply on each.
- **Heavyweight action ergonomics:** if 5 of 8 selections are `send` (heavyweight), Apply produces 5 sent-email confirmations in one chat turn. That's a lot of output. May want to chunk or summarize. Per-skill to address.

## See also

- `PROBE_RESULTS_past-meetings-open-items.md` — the empirical test results that drove this design.
- `HANDOFF_past-meetings-open-items-ux.md` — the original spec from Cowork (largely superseded by M's "no sidebar artifacts" directive; this widget spec is the resolution).
- `HANDOFF_cr-plugin-feedback-v2-10-9_2026-04-29.md` — the broader v2.10.9 feedback batch state.
- `_hq/CONVENTIONS_EMAIL_PREVIEW.md` — email draft format used inside the widget for any item that has a draft.
- `_hq/CONVENTIONS_SOURCE_LINKS.md` — source-link format used in item context (linked phrasing, source emails, transcripts).
