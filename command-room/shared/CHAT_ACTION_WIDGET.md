# Chat Action Widget — All-Batch Surface (v2.10.9+)

> Canonical spec for the action surface across every Command Room scheduled-task skill (Staff Meeting, Commitments, Inbox, Past Meetings, Upcoming Meetings) AND on-demand `meeting-notes` Step 9 OPEN ITEMS section.

## Why this exists

Pre-v2.10.9 each per-item action row was a typed-number text format: `▸ 1 send  ▸ 1 to drafts  ▸ 1 edit  ▸ 1 skip`. The user typed `1 send` to fire an action. M tested an inline-button alternative on Apr 29 (a probe-widget skill in the pre-release tree) and found:

- Buttons work in production Cowork chat (multi-click during streaming, post-response click, post-refresh click — all PASS).
- Per-click `sendPrompt` triggers Cowork's auto-scroll to the response. For multi-item resolution flows (close 4 items in a row), this means click → scroll-down → scroll back up → click → repeat. Annoying.

**Verdict:** use `show_widget`-rendered button surfaces with **all-batch close** — clicks accumulate in widget local state, one "Apply all" button fires one consolidated `sendPrompt` at the end. Single scroll, single ack, all choices logged in one batch.

Trade-off accepted: heavyweight actions that produce content (send email, draft reply, expand brief) don't show their output until after Apply. M chose all-batch over a mixed mode (lightweight batch + heavyweight per-click) for v1 simplicity. Revisit per-skill if specific surfaces feel wrong with all-batch.

See `PROBE_RESULTS_past-meetings-open-items.md` (workspace root) for the test results that drove this decision.

## The widget shape

Each scheduled-task skill calls `mcp__visualize__show_widget` (fed the persisted page's validated bytes as `widget_code` per § Transport, never hand-composed) with a widget that renders:

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
│    [prep deep work] [send] [draft] [push to] [snooze 3d]  │
├─────────────────────────────────────────────────────────┤
│ 2. ✉ Quinn (Aspen Hardware) — vendor team account...      │
│    ... (same pattern) ...                                │
├─────────────────────────────────────────────────────────┤
│ ... more items ...                                       │
├─────────────────────────────────────────────────────────┤
│ Quick read: [if applicable]                             │
│                                                          │
│ 0 of 8 selected                                          │
│ [ Apply all ]   [ Reset ]   [ Snooze rest (1 day) ]      │
└─────────────────────────────────────────────────────────┘
```

### Per-item verb dropdown (T2.2 row diet — replaces the button group)

Each item gets ONE `<select>` (class `cr-action-select`) listing that row's
registered actions, defaulting to **"— leave —"** (no choice). M approved
dropdowns over the six-button row (RV feedback: "lighten the render and add
more rows") — a row's verb chrome went from ~1.3KB of buttons to one compact
control, which is what makes 12-15 rows per page fit the relay budget.
Selection behavior:

- **Pick a verb:** the select arms (gold `cr-select-armed` state — the F-58
  visible-armed-state contract, ported from `.cr-selected`), and if the verb
  needs an input its `.cr-action-input` wrapper opens below the row exactly
  as it did for buttons.
- **Pick a different verb on the same row:** the previous choice is replaced
  (one action per item, enforced by the control itself).
- **Pick "— leave —":** deselects (row returns to "no choice yet").
- The option's `value` carries the FULL action string (brackets included) —
  the `apply choices:` wire format is byte-identical to the button era.
- The "+ Add context" note toggle is unchanged.
- Legacy note: the onboarding Step-1 setup widget keeps option BUTTONS
  (`cr-action` + `crToggle`) — its options are selection labels, not verbs;
  `validate_rendered_widget` validates both shapes (selects are the ported
  contract, not a weakened one).

### Primary verb buttons (t3 FB-4 — M ruling 2026-07-16)

A row's MAIN move renders as a visible one-tap button (`cr-action
cr-action-primary`) next to the dropdown; the tail verbs stay in the
dropdown (whose empty option reads **"— more —"** when primaries are
present). Row-shape driven, so mixed surfaces route correctly:

- **Commitment-shaped rows** (`resolved` / `mark done` present): **Done**.
- **Email-shaped rows** (`send` / `draft` present): **Send** + **Draft**.

Selection model is unchanged — tapping a primary ARMS the row (`.cr-selected`)
and Apply still batches; a row has one armed verb (tapping a button clears the
row's dropdown pick and vice versa). Primary buttons carry
`data-input-type="none"`: Send/Done/Snooze need nothing typed, and Draft's
edit surface is the inline-editable body (below), not a popup editor. FB-17
retired the `edit then send` popup form — inline editing replaces it — so the
email card is Send / Draft / Snooze with no dropdown.

### 'Later…' — the merged Defer/Snooze option (t3 FB-3 — M ruling)

Defer and Snooze read as two time-kicking verbs on one row, so a row that
carries both `push to [date]` and `skip`/`snooze Nd` renders ONE **Later…**
option and the separate snooze option is suppressed. Wire ids frozen —
display + dispatch only:

- The option's value stays `push to [date]`; its when-input accepts a
  natural-language date OR a bare number of days ("5" = five days from
  today; `commitment_state.parse_later_when` owns the deterministic slice).
- Dispatch AUTO-ROUTES by ownership (`commitment_state.later_route`): the
  user's OWN item → `commitment_updated` due-date shift; owed-to-you /
  unowned / visibility-only → `chat_dismissal` carrying `data.snooze_until`
  via the mute ledger (the item stays open, it just stops rendering until
  the date).
- Rows without `push to [date]` keep their snooze option unchanged, and the
  footer **Snooze rest (1 day)** still mutes merged rows (their skip entries
  ride the Apply payload directly since the dropdown no longer offers skip).

### Inline-editable email body (t3 FB-10 — M ruling)

The email draft body renders directly editable — click into it and type, no
Edit button. The `cr-eb-body` wrapper is `contenteditable` and carries
`data-original` (the queued text, innerText-shaped). On Apply, the widget
serializes the CURRENT on-screen text; if it differs from `data-original`
the choice carries `input: {"body": "<current text>"}` — queued equals
visible, always. Reset restores the original. The orchestrator diffs
rendered-vs-queued and logs the edit to the voice-corrections file the same
way a disposition edit is logged. (FB-17 retired the `edit then send`
multi-field editor; the inline body is now the sole edit surface. A
deprecated `edit then send` payload on an in-flight widget still wins over the
inline body, since it carries body + To/Cc/Subject.) Displayed bodies never show the `> ` blockquote-convention
markers — the renderer strips them (t3 FB-12); they are markdown plumbing,
and storage was never affected.

### Counter

Below the items, a live counter shows `X of N selected` (v4.5.2 S2 — F-58).
Increments with each unique-item selection, decrements on deselection. The
counter + the selected-button styling are the two signals that make armed
state visible before Apply; `validate_rendered_widget` refuses any widget
HTML that carries action buttons without them.

### Bottom row buttons

- **Apply all** — fires one `sendPrompt` with all current selections (see "Submission format" below). Disabled when 0 selections. **Disable-with-reason (v4.5.2 S2 — F-17):** when a selected action is missing its REQUIRED input (Later… without a date), Apply stays disabled and the reason renders on the footer's `#cr-apply-reason` line ("Apply is waiting on item 3 — Later… needs a date."), the offending row highlights, and the field shows an inline "needs a date" note. The hold clears live as the field fills. A widget must NEVER swallow an Apply click silently — that is exactly F-17 (M concluded the button was dead).
- **Reset** — resets all per-item selections to "no choice yet." No `sendPrompt`. Local state only. (Button label "Reset"; was "Clear".)
- **Snooze rest (1 day)** — selects `skip` (the 1-day mute) for every unselected item, then fires Apply automatically. Convenience for "I've reviewed; snooze the rest until tomorrow." (Label was "Skip all"/"Dismiss rest" — renamed so the mute states its duration, F-59.)

### What the widget does NOT do

- **Does not pre-select anything.** Every item starts with no choice. M has to actively select.
  - **DOCUMENTED EXCEPTION — first-run personalization items (SPEC FRP1).** First-run `fr1`/`fr2`/`fr3` items (the "Make this yours" section at the BOTTOM of a scheduled orchestrator's widget) render each fixed-option row with the **already-saved default in a "current" visual state**. This is NOT a pre-selected pending choice — the default is already applied + persisted (`save_skill_config` ran before the widget); the buttons are an OVERRIDE surface, and tapping one emits a normal `{n:"fr1", action, sub?, input?}` payload that apply-choices routes to `save_skill_config(..., is_reconfigure=True, origin="first_fire_override")`. The block renders exactly once ever (`is_configured` gate). On-demand skills use the footer micro-widget variant instead (never a second widget surface — that's what would break MUST-NOT rule 5). Full protocol: `shared/FIRST_RUN_PROTOCOL.md`.
- **Does not auto-fire on click.** Per-button clicks are local state changes only. Submission is gated on the Apply all button.
- **Does not persist state across refreshes.** If the user refreshes the chat page, all selections are lost (the widget re-renders with fresh state). This is acceptable per the all-batch design — M closes items in one sitting; if interrupted, he starts over.
- **Does not show heavyweight action output inline.** Drafts, brief expansions, and email sends produce their visible output AFTER Apply, in the response chat turn. The widget itself stays compact.

## Submission format

When **Apply all** fires, the consolidated `sendPrompt` carries the canonical batch shape:

```
apply choices: [{"n":1,"action":"send","src":"inbox"},{"n":2,"action":"prep deep work","src":"inbox"},{"n":3,"action":"push to 2026-05-05","src":"inbox"},{"n":4,"action":"skip","src":"inbox"}]
```

The serialization is JSON inside the `apply choices:` prefix. The receiving skill (see "Receiving skill" below) parses the JSON, dispatches each choice through the same per-action handlers the typed-number row used to call.

**`src` — stateless source dispatch (Phase 3 / W4, 2026-07).** Every tuple carries `src`: the emitting surface's id (the orchestrator/skill name the data view passed as `source_skill` — `inbox`, `commitments`, `pulse`, `past-meetings`, `upcoming-meetings`, `show-my-list`, `meeting-notes`, ...). apply-choices dispatches on `src` FIRST, so a widget click works no matter how much later it lands — scheduled chats are persistent threads the CEO opens hours later; the evening click on the 7:15 AM inbox widget is the normal case, not the edge. The fire-marker lookup (and its 60-minute TTL) applies ONLY as the fallback for legacy widgets whose tuples carry no `src`. The renderer stamps `src` automatically when the data view includes `source_skill`; orchestrators MUST pass it.

For grouped items with sub-letters (e.g. `7a`, `7b` in commitments), the `n` field carries the full identifier:

```
apply choices: [{"n":"7a","action":"mark received"},{"n":"7b","action":"mark received"},{"n":"7","action":"send"}]
```

For actions that take a parameter (`push to [date]`, `resolved [reason]`, `context [text]`), the parameter is part of the `action` string. The widget collects the parameter via an inline input that appears when the user clicks the button (small popover with a date picker for date params; small text input for free-form params).

### Commitment identity in widgets (Phase 2 Stage B, F2 — MANDATORY)

Every surface that renders a ✓ / `resolved` / `mark received` / any commitment-closing action MUST embed the commitment's **`data.id` verbatim** in the item it renders (the per-item context cache the receiving skill reads, and any `log resolved: <id>` sendPrompt an artifact fires). No surface may re-derive, abbreviate, or substitute an id — not the seq, not a truncated ULID, not a title hash. The dispatch side closes through `commitment_state.close_commitment()`, whose normalizer accepts the legacy spellings still in flight (bare int `86`, `seq_86`, `event_086`, `commitment_seq_86`) and raises `CommitmentIdError` on anything that matches no commitment — but the normalizer is a safety net for HISTORIC emitters, not a license for new ones. Re-derived ids are how 74 orphan tombstones (closures that matched nothing) entered the live substrate. `pending_review` items may render a closing action only as an explicit confirm (dispatch passes `user_confirmed=True`); no widget path auto-resolves them.

## Receiving skill

The `apply-choices` skill (skills/apply-choices/SKILL.md — shipped v2.12.4) owns this. Its job:

1. Parse the JSON array from the `apply choices:` prefix.
2. For each `{"n", "action"}` tuple, dispatch through the same handler the original typed-number row would have called. The orchestrator that emitted the widget cached the per-item context (recipient, subject, draft body, etc.) — `apply-choices` reads that cache to know what `1 send` means in the current context.
3. Return one consolidated chat ack: `✓ Applied N choices: send (3), draft (1), skip (4).` Plus any heavyweight action output (sent email confirmations, draft bodies, expanded briefs) under a dedicated section.

As of v2.10.9 end-of-day, `apply-choices` is built and all 5 orchestrators + `meeting-notes` Step 9 emit the widget via `mcp__visualize__show_widget` (as of T2: fed the persisted page's validated bytes as `widget_code` per § Transport, never hand-composed). No fallback typed-number-row path remains in production code — that mode existed only during the v2.10.9 build window before the renderer + apply-choices shipped.

## Post-widget chat-links section (v2.12.0+)

Hyperlinks inside the widget's iframe are unreliable — Cowork's iframe sandbox blocks `computer://` links and even some `https://` links don't fire on click consistently. **Hyperlinks in regular chat markdown work reliably** (verified Apr 30 by M).

So the architecture is:
- **Widget HTML** = action surface only (buttons + inputs). NO source/brief links inside.
- **Chat markdown post AFTER the widget** = links section. Per-item source thread URL, per-item brief `.docx` URL, all clickable.

### Format

After `mcp__visualize__show_widget` posts, emit a second chat turn with a markdown links block:

```markdown
**Links:**

1. [Sam Sample — Framing the build](https://mail.google.com/mail/u/0/#all/198abc...) · [📄 brief](computer://C:\Users\...\Past_Meeting_Sam_2026-04-30.docx)
2. [Lyra Sample — Acme Research](https://mail.google.com/mail/u/0/#all/198def...)
3. [Bowie Sample — Apr 14 kickoff call](https://notes.granola.ai/d/abc...) · [📄 brief](computer://C:\Users\...\Past_Meeting_Bowie_2026-04-14.docx)
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

## Action reference — what each button does (v2.12.2+; v4.5.2 S2)

User-facing reference. Every action label across all surfaces, with semantics. Action labels are stored lowercase as `data-action` attribute (canonical for parsing); display labels come from the verb taxonomy at render time.

> **Source of truth (v4.5.2 S2 — F-59):** `shared/scripts/verb_taxonomy.py` is THE verb table — wire id, display label, event dispatched, surfaces, mute TTL, required-input flag. The renderer derives `CANONICAL_ACTIONS` and every button label from it; the tables below are the human-readable view and the table wins on conflict. One label per verb, everywhere: `resolved` displays **Done** (never "Resolved"), `push to [date]` displays **Later…** (never "Push to"; was "Defer" pre-t3 FB-3 — the merged Defer/Snooze option), `skip` displays **Snooze (1 day)**, and every mute states its duration on the button — `Snooze (3 days)`, `Not relevant (60 days)`, `Never track (permanent)`. Prose that names an action names the same word as the button (F-13 P2a). Rows that offer a reduced verb set (needs-confirm items) pass `reduced_verbs_reason` so the row says WHY in one line (F-59).

### Email-shaped items (Inbox, Commitments YOU OWE / OWED TO YOU)

The plain email card is **Send / Draft / Snooze** — three primary buttons, no dropdown (FB-17, M 2026-07-19). `edit then send` is RETIRED: the FB-10 inline-editable body replaced the To/Cc/Subject/Body popup editor, so the card no longer offers it (the wire id stays a dispatchable deprecated alias → `send` for in-flight widgets, but no new card emits or renders it). Waiting On chase rows are also email-shaped but carry domain verbs (mark received, follow-up call) in the tail.

| Action | Display | What it does |
|---|---|---|
| `send` | Send | Compose+send the current draft as-is. Zapier first if configured (best thread fidelity); falls back to native Gmail threaded; standalone last resort. Works without Zapier. |
| `draft` (consolidated v2.14.4+; was previously two separate verbs) | Draft | One-tap primary button (t3 FB-4). Apply saves the current body — the card body is directly editable (t3 FB-10), so an inline edit rides the choice as `{"body": …}` — to the declared backend's Drafts. |
| `snooze 3d` | Snooze (3 days) | One-tap primary button (FB-17). "Deal with it later" — mutes the card for 3 days, then it resurfaces. |
| `add email then send` | Add email then send | (v3.13.8+ Bug #44 recovery verb) Widget exposes a single-field email-address input. On submit, updates the To: field on the item + transitions to enabled `send`. Use when the recipient is identified (resolved person record) but no actionable email exists. Writes `contact_email_captured` event with `data.person_id` for downstream people-CRM persistence. |
| `escalate to memo` | Escalate to memo | (Waiting On / inbox tail verb, not on the plain card) Promote to memo-writer skill — generates a longer-form `.docx` memo when an email reply isn't enough. |
| `edit then send` *(retired FB-17)* | — | Deprecated alias → `send`. Accepted from in-flight widgets; never emitted anew. The inline body (FB-10) is the edit surface now. |

### Commitments YOU OWE only

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generates a context-loaded prompt to paste into a new task. For when you want to dig in and do work on the commitment. No email/send. |
| `push to [date]` | Later… | Widget exposes a free-text input taking a date ("monday at 2", "next thursday") OR a bare number of days ("5"). Reply handler parses on apply (`parse_later_when` first, NL fallback). Dispatch auto-routes by ownership (t3 FB-3): own item → deferral (`commitment_updated`), otherwise a dated mute (`chat_dismissal` + `data.snooze_until`). The when is REQUIRED — an empty one holds Apply with the reason visible (F-17). |
| `resolved` | Done | Mark commitment fulfilled. Won't surface again. **(Distinct from `skip`, the 1-day snooze.)** Wire id `resolved` retained (renamed v2.12.3 from `close`); displays Done everywhere (F-59). |

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
| `skip` | Snooze (1 day) | Mute this sub-item for 1 day. |

**Two different things render through the sub-row shape — don't conflate them (SUB1):**

1. **Grouped chases (above)** — RENDER-time grouping only: N independent commitments owed by one person, folded into one chase email. No data relationship; the "parent" row doesn't exist on disk; `mark received all` closes N independent items.
2. **Sub-items (SUB1)** — a DATA relationship: real child commitments carrying `data.parent_id`, nested under their still-open parent on commitment-triage. The child row's id is the child's own `data.id` VERBATIM (identity contract) and dispatches like any commitment row (Done / Later… / Drop — children are real commitments); `never track this` stays parent-level. The parent row carries the progress chip ("sub-items 1/3 · next: [step]") and, when the last open child closes, the propose line "all sub-items done — close it?" (PROPOSE — never auto-close). Done on a parent with open sub-items requires the one-line cascade confirm before dispatching `close_subitems=True`. Families are pagination-atomic: a parent and its sub-items never split across pages.

### Self-commitments (no email — you owe yourself)

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generate a deep-work prompt as above. |
| `push to [date]` | Later… | Move the self-commitment's date (date or days, required). |
| `mark done` | Done | Close the self-commitment — same display word as `resolved` (F-59). |
| `skip` | Snooze (1 day) | 1-day mute. |

### Commitment Triage (Phase 2 Stage D, S4 — `triage my commitments` + the Friday opt-in chat)

Rows are the FULL open set sorted by age (promises AND tasks; stale tasks flagged "still on your plate?"). `done`/`defer` respec onto the canonical verbs; the five triage-specific verbs are the ONE deliberation verb set added under the ratified pre-authorization. Every action dispatches through `close_commitment()` / `commitment_state` helpers via apply-choices — NO in-place mutation ever (F4); this surface exists so the next cleanup-chat doesn't rewrite history.

| Action | Display | What it does |
|---|---|---|
| `resolved` | Done | Close via `close_commitment(..., resolution="done", user_confirmed=True)`. |
| `push to [date]` | Later… | Auto-routes (t3 FB-3): own item → `commitment_updated` with `new_due` (the Stage A fold renders the new date everywhere); owed-to-you/unowned → `chat_dismissal` with `data.snooze_until` via the mute ledger. |
| `drop` | Drop | Close via `close_commitment(..., resolution="dropped", user_confirmed=True)` — deliberately let go, distinct from done. |
| `not mine` | Not mine | Close (`resolution="dropped"`, evidence "not the user's item") — the cross-attendee capture class. When the user NAMES the real owner, route via `reassign to [name]` instead of dropping. |
| `fix wording [text]` | Fix wording | `commitment_state.edit_commitment_wording` — corrects a mis-extracted title/summary; the projector renders the new text, history keeps the original (S4). |
| `reassign to [name]` | Reassign | `commitment_state.reassign_commitment` — routes the item to its real owner instead of discarding it; unconfirmed reassignments stay pending_review and never enter chase (S4; W4b's Theirs → [name] confirm verb dispatches the same event). |
| `split into [items]` | Split | `commitment_state.split_commitment` — N Stage-D-complete children (provenance → the original) + the original closed with "split into …" (S4; extraction pre-split stays the doctrine — this is the manual correction path). |
| `make task` | Turn into a task | `promote_task_to_commitment(..., new_kind="task")` — additive `commitment_reclassified` marker; drops off CRU/chase/aging, lives on the task surface. |
| `promote` | Make it a commitment | `promote_task_to_commitment(..., new_kind="promise")` — S5 one-tap promote when a counterparty appears. |
| `never track this` | Never track (permanent) | Appends a suppression pattern to `_hq/config/commitment-rules.md` (extractors read it before writing) + closes the item (`resolution="dropped"`). Permanent — the label says so; every TIMED mute is reversible via the S4 ledger (`show muted` + Unmute). |
| `skip` | Snooze (1 day) | 1-day mute (unchanged semantics; label now states the duration). |

### Confirm section — "Needs a quick confirm" (v4.6.1 W4b, the daily Waiting On chat — CTS1; pre-split: the Commitments chat — + the triage Unconfirmed block)

Rows come from `confirm_flow` selectors: captures younger than the 7-day
escalation pin that are
pending_review / unowned / suspected duplicates, plus unadjudicated person
proposals (daily until adjudicated — the stranding fix) and kind
auto-promotion proposals. Unconfirmed items NEVER enter chase and count only
in the headline `unconfirmed` bucket. Verb clusters per row class:

| Action | Display | What it does |
|---|---|---|
| `mine` | Mine | `commitment_state.confirm_commitment_owner` — you own it; the confirm flag clears and it joins you-owe. |
| `theirs to [name]` | Theirs | `commitment_state.reassign_commitment(..., confirmed=True)` — routes to the named person; the name is REQUIRED (F-17) and IS the explicit confirmation. |
| `make task` / `drop` | Turn into a task / Drop | Same dispatch as triage (above) — the click adjudicates a pending_review row (`user_confirmed=True`). |
| `merge` | Merge | Duplicate rows only — `commitment_state.supersede_commitment(survivor=the flagged suspected_duplicate_of target, superseded=this row, user_confirmed=True)`. Both ids are embedded; no input. |
| `keep both` | Keep both | Duplicate rows only — `commitment_state.clear_review_flags` ("confirmed distinct"); both stay open. |
| `add person` | Add person | Unknown-person rows — `people_writer.create_person` via apply-choices Step 3a (dedup-first), then the proposal tombstone. |
| `same as [existing]` | Same as | Unknown-person rows — resolve the typed name (ambiguous → ask), `people_writer.add_person_alias` (aliases.json + the person record), then the tombstone. Name REQUIRED. |
| `proposal not relevant` | Not relevant (permanent) | Unknown-person rows — proposal tombstone, nothing else written. Permanent by design (not a timed mute; the label says so). |
| `promote` | Make it a commitment | Promotion-proposal rows — same S5 dispatch as triage; the click IS the adjudication (PROPOSE only, never auto). |

**Send-vs-draft clarity (PL 2026-07-02):** wherever `send` and `draft` appear side by side on an email-shaped item, the widget's intro line (or the item's context tag) makes the difference visible in plain words — "**Send** delivers it now · **Draft** saves it to your Drafts to send later." Never assume the customer knows the distinction from the labels alone.

### Needs your call — the unconfirmed-extraction queue (INTAKE; CAPTUREFLOW §C; DONE1 2026-08-03)

Rows are UNCONFIRMED EXTRACTIONS — a capture the extractor was not sure about, a capture whose evidence was not found in its transcript, or one the admission floor gated. `n` = the commitment's `data.id` verbatim. The SAME four verbs render on both places this queue appears — the on-demand `needs your call` widget (`src: "needs-your-call"`, `needs_review_queue.build_queue_data_view`) and the staff meeting's `FROM YOUR MEETINGS` fold (`src: "cr-brain"`, `needs_review_queue.staff_meeting_group_section`) — because both read the ONE `needs_review_queue.QUEUE_ROW_ACTIONS` list. There is no per-surface verb variant and no second write path.

| Action | Display | What it does |
|---|---|---|
| `confirm` | Confirm | `needs_review_queue.confirm_items` → `commitment_state.clear_review_flags` — the capture was real; it becomes an ordinary open commitment. Runs THE shared bulk-accept fence (`watch_gate.screen_bulk_accept`): a weak row (no evidence, or title-match-only) is HELD unless the user typed that row's own number. |
| `already done` | Already done | `needs_review_queue.done_items` → `clear_review_flags` THEN `close_commitment(..., resolution="done", user_confirmed=True)`. The capture was real AND the user already did it off-mail. Evidence is the user's own attestation, never a match or a score; an additive `completion_basis: "user_attestation"` stamp rides `extra_data`. Same fence, plus a STRICTER caller-side bar: **per item only** — every id must be individually named, so `all`, a range and a call phrase write nothing (`not_individually_named`). Deliberately NOT the `resolved` wire id, which a global Step-4 handler would route straight to `close_commitment`, bypassing both. |
| `drop` | Drop | `needs_review_queue.drop_items` → `close_commitment(..., resolution="dropped", user_confirmed=True)` — the capture should not have been tracked. Note the cost this carries and `already done` does not: a `dropped` closure is a DISMISSAL signal for that counterparty in `capture_gate`'s tuning miner. |
| `not mine` | Not mine | `needs_review_queue.not_mine_items` — the same closure with the honest reason. When the user NAMES the real owner, route via `commitment_state.reassign_commitment` instead. |

**Undo (UNCONFIRM1 2026-08-03).** A confirm reverses via `needs_review_queue.undo_confirm_items` and an `already done` via `undo_done_items` (reopen THEN un-confirm — a bare reopen leaves an OPEN, CONFIRMED item, which is not what the user had before they tapped). Both write through `commitment_state.restore_review_flags`, the purpose-built un-confirm writer: one additive `commitment_updated` carrying the existing `data.review_flags_set` fold key plus `review_flags_restored` provenance and the item's ORIGINAL `review_reason`, and carrying NO `suspected_duplicate_of`. `flag_duplicate_for_review` is the duplicate-PAIR writer and is never the reverser of a confirm. An un-confirm REFUSES an item that has been independently touched since (`touched_since_confirm`, naming what happened). Never edit or delete prior events.

### Deliberation extension (Phase 4 2026-07-02 — same pre-authorized set, grown once)

| Action | Display | What it does |
|---|---|---|
| `revisit` | Revisit now | decision-revisit: opens `decision-memo-composer` pre-filled with the original decision's framing + the contradictory-signal pass. |
| `still valid` | Still valid | decision-revisit: writes `decision_reaffirmed` referencing the original decision. |
| `replace` | Replace it | decision-revisit: chains to `decision-log` to capture the new decision; writes `decision_superseded` linking the original (event type unchanged — the friendly label is UI-only). |
| `snooze 30d` | Snooze (30 days) | decision-revisit: `decision_revisit_scheduled` with `snooze_until_ts = now + 30d`. |
| `snooze 7d` | Snooze (7 days) | scaffold-automation: re-surface the deployed-yet? check in a week. |

**Undo (S4 — delivered by this surface):** the post-Apply ack ends with *"Say `undo` to reverse this triage."* A follow-up `undo` (within the same chat) reopens every commitment the batch closed via `commitment_state.reopen_commitment` — an ADDITIVE `commitment_reopened` event per item; tombstones stay in history and a later re-close works normally. Reclassifications undo with a reverse `commitment_reclassified` marker. The batch's mutes undo too: every `chat_dismissal` the batch wrote is cleared via `mute_ledger.clear_dismissals` (an additive `chat_dismissal_cleared` per mute — the F-20 P3a asymmetry, fixed: undo used to reopen items while leaving their mutes in force). Never edit or delete prior events.

### Mute ledger — `show muted` / `show snoozed` (v4.6.0 S4, rendered by show-my-list)

Every live `chat_dismissal` (Snooze 1d/3d/7d/14d, Not relevant 60d), one row each, oldest first — each row states WHAT was muted, WHERE it came from, and its remaining time verbatim from `mute_ledger.live_mutes` (`ttl_label`: "3 days left" / "expires in an hour"). Timed mutes stop being a one-way door.

| Action | Display | What it does |
|---|---|---|
| `unmute` | Unmute | `mute_ledger.clear_dismissal(<row's dismissal seq>)` — additive `chat_dismissal_cleared`; the item re-surfaces on its next scheduled chat. |
| `skip` | Snooze (1 day) | Leave the mute in force (the ledger row itself disappears for a day). |

Permanent `never track this` rules are NOT in this ledger (they are suppression rules in `_hq/config/commitment-rules.md`, lifted by editing the file); learned suppressions from `surface-preferences.json` are a separate durable layer.

### Reminders — brief Pinned block + `show my reminders` (v4.5.2 S2)

W4a shipped reminders as a chat-phrase-only surface and deferred the widget
verbs to the S2 taxonomy. These are the rows any reminder-rendering widget
uses; dispatch goes through `shared/scripts/reminders.py` builders (see
apply-choices § "Reminder dispatch"). Same display words as the commitment
lane — Done closes, Later… moves the date — so one vocabulary covers both
lanes when they share a brief widget.

| Action | Display | What it does |
|---|---|---|
| `reminder done` | Done | Clear the reminder (`reminder_cleared`). It leaves the Pinned block; a referenced commitment is NOT touched — closing that is its own action. |
| `reminder push [date]` | Later… | Move the pin date (`reminder_updated`, action `push`). Date REQUIRED — empty holds Apply with the reason (F-17 contract). Re-arms a cleared one-shot. |
| `reminder keep` | Keep | Acknowledge without clearing (`reminder_updated`, action `keep`) — resets the escalation clock, stays pinned. |

### Balance — the Sunday reconnect card (SPEC BAL1, `surface: "m_facing"` only)

The personal white-space surface's card. PERSONAL-LANE: this widget never
renders on any org/board/client surface, and every dispatch stays
propose-and-confirm — no code path books, sends, or spends without the click.

| Action | Display | What it does |
|---|---|---|
| `book` | Book it | THE consent click (BAL1 D4): tentative personal-calendar hold via calendar-writer's Phase 5/6 path + the venue outreach draft queues per the draft posture. Nothing books/sends/spends itself — a reservation is a commitment + potential financial action, both user-click-gated. Optional input = venue name/correction. |
| `propose other night` | Another night | Type a date (validated against the fire's busy set via `availability.has_conflict` — a conflicted evening gets an honest decline) or leave empty to see the other open evenings. No writes until a later `book`. |
| `snooze 7d` | Snooze (7 days) | Not this week — the tie re-ranks next Sunday (matches the 7-day per-tie dedupe). |
| `skip` | Snooze (1 day) | 1-day mute. |

### Pulse — person dormancy/pattern-break — FOSSIL (the chat is RETIRED, LIFECYCLE1)

**The five Pulse sections below describe a surface that no longer renders.** They stay because a widget persisted before the retirement can still be clicked, and `apply-choices` still has to know what each verb meant. Never build a NEW surface from them — `verb_taxonomy` and `CANONICAL_ACTIONS` are the live authority, and the dormancy row's live home is `stalled projects`.

| Action | Display | What it does |
|---|---|---|
| `investigate` | Investigate | Fires `tell me about [name]` — pulls cross-references from across your data. Read-only. Use when you want context, not action. |
| `draft re-engagement` | Draft re-engagement | Generates a re-engagement email draft to the person. The draft surfaces in the apply-time widget with the standard email-card controls (Send + Draft buttons, editable body — t3 FB-4/FB-10). |
| `schedule catchup [when]` | Schedule catchup | Widget exposes a free-text natural-language input. User types "next Tuesday afternoon", "this Friday at 4pm", "sometime next week". Drafts the request email + creates tentative invite if a specific time was given. (v2.12.4+ — was no-input in earlier versions.) |
| `resolved` | Done | State change. Suppresses the alert for 14 days. NO input affordance, NO textarea — clean one-click. (v2.14.1+ unified with Commitments YOU OWE `resolved`; v4.5.2 S2 displays Done everywhere.) Same display label, same behavior, same mental model as Commitments: "this isn't open anymore." |
| `snooze [duration]` | *(deprecated — never rendered)* | Back-compat alias only. New widgets emit a FIXED duration verb whose label states it — `snooze 3d` → "Snooze (3 days)", `snooze 7d`/`14d`/`30d` likewise; pre-v2.14.38 in-flight widgets may still emit `snooze [duration]` with free text — apply-choices accepts BOTH (see its Step 3 table). A bare "Snooze" with an invisible duration is a banned label (F-59). |
| `skip` | Snooze (1 day) | 1-day mute. |

### Pulse — stale-active project

| Action | Display | What it does |
|---|---|---|
| `prep deep work` | Prep deep work | Generates a context-loaded prompt for revisiting the project (last 14 days of events + last decision + open commitments). For when YOU want to do work on it. |
| `investigate` | Investigate | Fires `tell me about [project]` — pulls cross-references and surfaces what you might be missing. Read-only. |
| `mark paused` | Mark paused | Move the project to paused status. Drops out of the stall surface. |
| `status check` | Status check | Drafts an internal status-check email TO whoever owns the project. For when YOU haven't been driving and want someone else's update. |
| `snooze [duration]` | *(deprecated)* | Use a fixed-duration snooze verb (`snooze 3d` etc.) — labels state the duration. |
| `skip` | Snooze (1 day) | 1-day mute. |

### Pulse — pending people-record review (a, b, c…)

When the synthesis layer detects a low-confidence change to someone's record (new role, new org, etc.), it surfaces here for confirmation:

| Action | Display | What it does |
|---|---|---|
| `confirm` | Confirm | Apply the proposed change to the person record. |
| `edit [change]` | Edit | Widget exposes textarea. User types the corrected value. Applied as the change instead of the proposed one. |
| `snooze [duration]` | *(deprecated)* | Use a fixed-duration snooze verb — labels state the duration. Won't re-surface until the snooze expires. |
| `skip` | Snooze (1 day) | Not now — resurfaces tomorrow. (Rejecting outright is `not relevant`, 60 days; the old 30-day-cooldown claim here contradicted the dispatch layer, which has always written the 1-day mute.) |

### Pulse — dormant transition proposal (d1, d2…)

When an active project has been quiet 30+ days, surfaces here to ask if it should move to Dormant status:

| Action | Display | What it does |
|---|---|---|
| `active` | Active | Keep the project active. 14-day cooldown — won't re-propose for 14 days. |
| `keep paused` | Keep paused | Already paused, no change. |
| `archive` | Archive | Skip the dormant step entirely; archive the project (it's effectively closed). |
| `snooze [duration]` | *(deprecated)* | Use a fixed-duration snooze verb — labels state the duration. |
| `skip` | Snooze (1 day) | 1-day mute; re-surfaces tomorrow. (v2.14.5+ — finish-cluster.) |

### Pulse — entity proposal (e1, e2…)

When passive ingestion detects a new org or project from your activity. The Phase 8 context_tag NAMES the candidate explicitly and explains what Confirm does (e.g., "Track Acme Co as a prospect org? Email domain acme.example.com seen in 5 threads.") instead of generic "new org candidate" framing — per M's v2.14.5+ ask: a generic "Add as new org" button doesn't tell the user WHICH org or WHAT tracking it does.

| Action | Display | What it does |
|---|---|---|
| `confirm [type]` | Confirm | Click opens a textarea pre-populated with the inferred entity details (name, domains, relationship_type, scope, signal). Empty submit = accept inferred. Type corrections to override before writing. |
| `edit [type]` | Edit | Same textarea opens; intended for cases where the user wants to flip the inferred relationship_type (vendor → client, prospect → partner, etc.) before confirming. (v2.14.5+) |
| `snooze [duration]` | *(deprecated)* | Use a fixed-duration snooze verb — labels state the duration. Won't re-propose until the snooze expires. |
| `skip` | Snooze (1 day) | Not now — resurfaces tomorrow. For "this proposal is wrong / don't track this," use `not relevant` (60 days) — the verb whose label states that cooldown. (The old 60-day-cooldown claim here contradicted the dispatch layer.) |

### Past Meetings — new-person sub-item (1a, 1b, 1c, …)

When a meeting mentions one or more new people, **each person gets their OWN sub-item** (v2.12.4+ — Rio Sample goes to 1a, Rio Lange to 1b, etc.). Never stacked as competing actions on a single sub-item.

| Action | Display | What it does |
|---|---|---|
| `add as person to <Org>` | Add as person to <Org> | Specific-org variant — create a person record under the named org. Used when the meeting / signal makes the org clear. |
| `add as person to [org]` | Add as person to org | Generic — widget exposes textarea for org name. Used when the org isn't determinable. |
| `add as new org <Org Name>` | Add as new org <Org Name> | (v2.14.5+) Specific-name variant — create the named org as a new entity, with inferred relationship_type pre-populated. Used when the candidate org name is inferable from email domain, transcript mention, or signature block. |
| `add as new org` | Add as new org | Generic fallback — opens the same flow but prompts for the org name. Used when the candidate name isn't determinable. |
| `add context [text]` | Add context | Widget exposes textarea for free-form context (where you met, role, etc.). On Apply, opens an interactive entity-creation flow seeded with that context. |
| `skip` | Snooze (1 day) | 1-day mute. |

(RETIRED at MLK1 2026-07-21: `add to my list` — no new render offers it anywhere. The wire id stays registered in the taxonomy so a persisted old widget's click still dispatches with its original meaning; remaining list items drain read-only via `show my list`.)

(Removed in v2.12.4: `search emails` — per M's Apr 30 ask. Use `tell me about [name]` directly for cross-reference.)

(Renamed in v2.14.19: `add to list` → `add to my list` to match the `show my list` retrieval trigger. The display label "Log to discuss" was also retired in this rename — single noun across the loop, no guessing.)

### Past Meetings — vague-timing sub-item (1b)

When a meeting mentioned a commitment without a clear due date:

| Action | Display | What it does |
|---|---|---|
| `set date [when]` | Set date | Free-text natural-language date ("monday at 2", "next Thursday"). Sets a specific due date. |
| `skip` | Snooze (1 day) | 1-day mute. |

### Upcoming Meetings (per meeting)

| Action | Display | What it does |
|---|---|---|
| `context [text]` | Context | (v2.14.37+) Single unified context affordance — replaces `add more context [text]` + `ask question [text]`. Widget exposes a textarea; user types anything (background context, talking points, a question, an instruction). On Apply, the handler routes intent-aware: question-shaped input synthesizes an answer using prior meeting transcripts + recent emails with attendees + relevant decision-log entries (1-3 paragraphs with source citations); statement-shaped input re-runs call-prep with the added context folded in and regenerates the `.docx` brief. **Intent heuristic:** treat the input as question-shaped if it ends with `?` OR its first word matches `(what|why|how|when|who|which|is|are|was|were|did|does|do|will|can|could|should|would)` (case-insensitive); otherwise statement-shaped (brief regeneration). Per M's 2026-05-07 evening ask: *"we only need a 'Context' button… just one option that opens that up for you to interact how you wish."* |
| `add more context [text]` | Add more context | (v2.12.4 - v2.14.36 alias, retained for back-compat) Translates at apply time to `context [text]`. New widgets emit `context [text]`; pre-v2.14.37 widgets in flight at upgrade still dispatch correctly. |
| `ask question [text]` | Ask question | (v2.14.14 - v2.14.36 alias, retained for back-compat) Translates at apply time to `context [text]`. |
| `push meeting [date]` | Push meeting | Widget exposes a free-text natural-language input ("monday at 2", "tomorrow afternoon", "2026-05-12"). The reply handler parses the natural language at apply time. Drafts the reschedule email; surfaces in the apply-time widget with the standard email-card controls (t3 FB-4/FB-10). (v2.12.4+ — replaces strict date picker.) |
| `skip` | Snooze (1 day) | 1-day mute. |

### Living Brain card — "Needs your eyes" + Staff Meeting (SPEC LB1, `src: "cr-brain"`)

The unified confirm card: ≤5 items on daily surfaces (`brain_proposals.DAILY_CONFIRM_CAP`), and on the Staff Meeting surface the ranked queue **grouped into evidence-class digests and bounded to about two screens per fire** (STAFFCUT — see the bound paragraph below; the projector still returns the full set, the RENDER is what is bounded). Rows come from `brain_proposals.select_confirm_card` / `load_open_proposals` — ONE queue mixing brain-family (`bp_*`) proposals with adapter-read legacy items. Each row embeds its proposal id and every target id VERBATIM (the F2 identity rule applies to the underlying commitment/thread/person ids too) and carries `context.kind` naming its family so apply-choices can dispatch per-kind. No row may require an input to unblock the batch (F-17): every input is optional — an empty confirm applies the proposal as-is and the ack says so.

Brain-family rows (deal signals today; every new detector tomorrow):

| Action | Display | What it does |
|---|---|---|
| `confirm proposal` | Confirm | Apply the proposed change through its class's single writer (deal moves via `deal_state`, creations via `deal_state.create_deal`), then retire the proposal. Optional textarea to correct inferred details first. |
| `dismiss proposal` | Not relevant (60 days) | Decline — the tombstone plus a 60-day fingerprint cooldown in the shared ledger. The same suggestion stays away. |
| `snooze proposal 7d` | Snooze (7 days) | Set it aside; re-surfaces in a week. The proposal's own TTL keeps running (default 14d — an ignored proposal expires silently). |
| `merge person records` | Merge records | PID1 D4b — `kind: person_merge` rows only. Merge the duplicate contact into the record it duplicates (`people_writer.merge_person_into`, both ids embedded verbatim), then retire the proposal. Confirm-only forever: no reverser exists, the label says the merge cannot be undone. |

PID1 merge-propose rows ride this family too: `kind: person_link` ("[name] is already on file — link it?") uses the generic `confirm proposal` / `dismiss proposal` / `snooze proposal 7d` verbs — confirm dispatches the alias link (`add_person_alias`) + a `same_as` tombstone per underlying proposal; dismiss ALSO tombstones the underlying proposals `not_relevant` (the on-file zombies must die permanently). `kind: person_merge` renders `merge person records` / `proposal not relevant` / `snooze proposal 7d`.

Legacy-family rows keep their OWN shipped verbs, exactly as on their home surfaces (the adapters are permanent fossil readers — LB2 migrated the org/project/dormancy/schedule_add writers onto the bp rail, so NEW rows of those kinds arrive as `bp_` rows with the bp verbs; person/commitment_review remain legacy-written): person proposals render `add person` / `same as [existing]` / `proposal not relevant` (W4b) — since PID1 these are identity-CLUSTERED rows (one person = one row; the row embeds `data.cluster_seqs`, and one click adjudicates every underlying proposal); commitment-review rows render `confirm` / `not relevant` (the orchestrator-commitments Phase 3.6 dispatch); dont-forget dormancy rows render `active` / `archive` / `snooze 14d`; entity proposals render `confirm [type]` / `not relevant`; schedule-add rows are RETIRED — no longer emitted AND no longer projected (STAFFCUT §3.6 retired the adapter; LIFECYCLE1 §7a stopped the migrated bp-rail writer and added the kind to `brain_proposals.RETIRED_KINDS`, because a row that is retired at one rail and still written on the other renders anyway — which is what M saw on the 2026-08-03 staff meeting. Rows already open still expire on their own TTL; a pre-retirement row still in a persisted widget stays a pointer row). Dispatch table: `skills/apply-choices/SKILL.md` Step 2 `cr-brain`.

**STAFFCUT (2026-08-02) — three corrections to what those legacy rows actually carried.** The org/project, dormancy and schedule_add adapters shipped `action_tuples: []` hardcoded, so those rows rendered BUTTONLESS — six org rows and one dormancy row on the audit day were permanently unanswerable while their handlers sat registered and unreachable. Org/project rows now carry `confirm [type]` / `not relevant` and dormancy rows carry `active` / `archive` / `snooze 14d` (the same handlers, finally on the row); schedule_add retired. Dormancy rows are also ON-DEMAND now — they carry `surface_hint: "on-demand"`, so no scheduled surface renders one and `load_open_proposals(ws, "on-demand")` is the read. Since LIFECYCLE1 the ASKING surface is `stalled projects` (one owner, project-shaped rows on the project-shaped surface) and the WRITER is the weekly `lifecycle` maintenance job.

**DIGEST rows (STAFFCUT).** The staff-meeting driver folds each evidence class into one row whose `n` starts with `digest:`, carrying `data.digest_class` / `data.digest_count` / `data.digest_members`. The members' ids, verbs and dispatch payloads ride the row verbatim — the PID1 `cluster_seqs` contract generalized — so a grouped answer is still N per-id resolutions, a single member can be answered out of the group, and a grouped confirm goes through the SAME shared bulk-accept fence (`proposal_digests.confirm_review_digest` → `watch_gate.confirm_review_rows`). A `digest:` id is never a proposal id and never a dismissal target.

Card-wide contract: ranking money > identity > hygiene then age; max 2 slots per detector per render; the overflow line teaches the full-queue phrase ("N more queued — say `staff meeting` to review everything."); batch Apply posts ONE consolidated ack; the narrated batch ends with the standard undo affordance ("Say `undo` to reverse this.") and the undo reverses ADDITIVELY (`brain_undo.undo_batch` — commitment reopens, mute clears, archive flips; never edit or delete prior events). Cross-surface dedup (R2): an item rendered on one daily surface today is not re-shown on another the same day — the Staff Meeting full set and explicit asks are exempt. Pagination on Staff Meeting is design, not a size fallback: the queue renders one `page` at a time (§ Transport), each page relayed as `widget_code`; `show more` re-fires the next page. Since STAFFCUT the Staff Meeting page-SET is itself bounded (`proposal_digests.STAFF_PAGE_ROW_CAP`, ~21 rows for the whole page including the appended sections, budget split across the shapes present so no lane starves): the bound holds back the ranked TAIL of each lane, the section titles carry the honest full totals plus a pointer, and the held-back rows stay queued and lead the next fire. It bounds the render, never the projector.

### Bulk row (every surface)

| Action | Display | What it does |
|---|---|---|
| `send all` | Send all | Sequential sends across all non-noise items. |
| `to drafts all` | To drafts all | Bulk save to Gmail Drafts. |
| `show more` | Show more | Re-render with top 10 instead of top 5. |
| `skip all` | Snooze rest (1 day) | Bulk 1-day mute of everything unselected. |

### Why so many actions?

Every surface has 5–8 buttons because each one corresponds to a different real decision. Pre-v2.11.x the typed-number row exposed all of them as text; v2.11.x widget made them clickable; v2.12.x consolidates redundant variants (`edit firmer`/`edit softer` removed; `edit` + disposition combined; `keep` standardized to `skip`). The set is now near-minimal — every button is a meaningfully different action.

## Transport — how the widget reaches the screen (T2 delivery rework, Bug #67)

**THE posting path for every row-list / all-batch widget: render+validate+persist one page, then relay that page's validated bytes as `show_widget`'s `widget_code`.**

**One-command drivers (T2.2 — the ~30-command prep killer):** the two big
row-list surfaces have dedicated drivers in
`shared/scripts/surface_drivers.py` that run the WHOLE pipeline (canonical
loaders → projectors → data view → `render_and_persist(page=N)`) in one CLI
invocation and print `CR-PAGINATION: {...}` plus the page bytes between
`CR-WIDGET-HTML-BEGIN`/`CR-WIDGET-HTML-END` markers — relay those bytes as
`widget_code`, byte-exact. `commitments` (commitment-triage Step 3) and
`staff-meeting` (orchestrator Phase 5) MUST use their driver — one invocation
per page per fire, never re-run for a page already in hand (RV-3
double-render). Surfaces without a driver use the direct call below.

```python
# Rule 22 preamble REQUIRED before this runs: cd "$PLUGIN_ROOT" (SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||"); PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* | head -1))
import sys; sys.path.insert(0, "shared/scripts")
from widget_transport import render_and_persist
transport = render_and_persist(
    data_view=data_view,                     # widget_mode: "all_batch_widget"
    wrapper="fragment",
    persist_dir="<WORKSPACE>/_hq/.system/widgets",
    name_hint="<surface id, e.g. commitments>",
    page=1, page_size=10,                    # unbounded views: paginate. Omit page for small/bounded views.
)
# Deliver: pass transport["html"] to mcp__visualize__show_widget as `widget_code`, VERBATIM.
# transport["html"] IS the persisted page's validated bytes. Do not edit it. Done.
```

Every validator fires inside the call — the renderer's canonical-action, data-shape, and leak checks, plus `validate_rendered_widget` (the wrapper + visible-feedback contract). The persisted file is the validation gate + the audit trail; `transport["html"]` is the deliverable.

**Why THIS is the contract (Bug #67, confirmed twice in the 2026-07 dogfood cycles):** Cowork's `mcp__visualize__show_widget` schema is `loading_messages` (required) / `title` / `widget_code` — **there is no `file_uri` parameter.** The prior "hand the persisted file's URI to show_widget" mandate was impossible on the live runtime (no such parameter), so the runtime silently improvised a freelance render on every fire (FS-08) — the exact failure the transport exists to prevent. `widget_code` is the only carrier the runtime actually has. It accepts a page-sized payload (the live 133-row improvised widget rendered and its buttons worked); what it cannot carry is an unbounded set relayed byte-faithfully from context. Pagination + the diet-minified scaffold make each page fit one Cowork Read (25K-token cap), so the relay is faithful and mechanical.

**Paginate by design — NOT a size fallback.** Unbounded views (the full commitment set, the Staff Meeting queue) render ONE page per fire (`page=N`), sized by `chat_output_renderer.DEFAULT_PAGE_SIZE` unless the caller passes a lower ceiling. `render_and_persist` slices, validates, and persists each page independently and stamps `transport["pagination"]`. The widget's position line teaches `show more`; a `show more` click re-fires the surface with `page=N+1`. Bounded surfaces (the daily ≤5 card, small fires) omit `page` and render the whole set — pagination is inert. This is the surface's DESIGN, decided by the data, never a reaction to a "transmission ceiling."

**A page-set is ONE question asked ONCE (PAGESNAP).** Page 1 freezes the built view as the fire's page-set (`page_snapshot`, stored at `_hq/.system/widgets/pagesets/<surface>.json`); pages 2+ slice that frozen list. Pagination previously rebuilt the view from live substrate on every page, so an index computed against page 1's ordering was applied to a different query result: a write landing between the renders pushed the tail of page 1 onto page 2 (duplicates) or made the rows that should have opened page 2 render on NO page at all (silent, and the one that loses the user's work). Never re-derive a later page from a fresh read — that IS the bug.

**Three pagination flags you must SAY, not swallow.** They ride on `CR-PAGINATION` / `transport["pagination"]`, and each exists because the alternative is the system doing something other than what was asked and staying quiet about it:

| Flag | What happened | What to say |
|---|---|---|
| `refreshed` (+ `refresh_reason`, `previous_total`) | The page-set went stale or was missing, so the list was rebuilt — this page is sliced from a NEWER read than page 1. | Say the list refreshed before the rows, and give the new total. Never present a refreshed page as a continuation. |
| `suppressed: N` | N rows on this page were dropped because the user already applied them in this page-set. | Nothing is owed, but never describe the page as short or the count as wrong — those rows were handled. |
| `clamped` (+ `requested_page`) | A page past the end was asked for; the LAST page was served again. | Say that is the end of the set. Never present re-served rows as new ones. |

Absent flags mean the page came straight off the frozen page-set (`from_snapshot`) and needs no narration beyond the normal position line.

**Zero-manipulation survives, translated.** Relay `transport["html"]` byte-for-byte as `widget_code` — never minify it, never whitespace-strip it, never "trim for size", never drop what looks like a duplicate wrapper, never edit the persisted file. The renderer already diet-minified the scaffold inside the call; the page is exactly as large as it needs to be. If a page still feels large, that is what pagination is for — LOWER `page_size`, never edit the bytes. `validate_rendered_widget` runs inside the transport and raises if a wrapper was dropped.

**No silent fallback (FS-08).** After a clean `render_and_persist` call you MUST call `show_widget` with `transport["html"]`. If — and only if — `show_widget` itself errors or is unavailable, you MUST SAY SO in plain English and STOP: surface the error string verbatim (or `(Widget surface unavailable — re-fire when the visualize MCP is reachable.)`). Never improvise a hand-built widget, a compact freelance render, a custom wire prefix, or a chat-listed substitute; never narrate that the widget "couldn't transmit," "hit a payload limit," "was too large," or "validated but…" — none of those conditions exist on this path, and inventing one is the FS-08 silent-improvisation failure. A mandated call that cannot execute is reported, never worked around quietly.

**Chat carries zero widget bytes (F-09):** no fragment of widget HTML — style blocks, `::view-transition-group` preludes, tags, minified CSS — ever appears in chat *text*. The bytes travel only as the `widget_code` parameter; if a CSS-looking prelude would precede the render in the chat transcript, that is a leak, and the leak scanner flags the echo.

## Posting contract — what the orchestrator MUST do, MUST NOT do (v2.11.3+)

**MUST do:**

1. Build the data view, set `widget_mode: "all_batch_widget"`, and render + validate + persist it via `widget_transport.render_and_persist` (§ Transport above) — all validators fire inside the call. Unbounded views pass `page=N` and paginate; bounded views omit `page`.
2. Post the widget by passing `transport["html"]` (the persisted page's validated bytes, verbatim) to `mcp__visualize__show_widget` as `widget_code`. **The widget is the entire user-facing surface for the items.** No accompanying markdown narration, no "here's what you can do" prose, no recap of the widget's button labels.
3. After the widget posts, if any `.docx` deliverables were produced this fire (briefs, prep docs, etc.), call `mcp__cowork__present_files` ONCE with an array of all absolute paths. Cowork emits inline file cards beneath the widget, named by the source filename (which already includes the meeting / project slug). This is the ONLY mechanism for clickable file surfaces — `computer://` links inside the widget HTML do not work (iframe sandbox blocks them).

**MUST NOT do:**

1. **Do NOT narrate or paraphrase the widget's behavior in chat.** Lines like *"Click any action button per item, then Apply all to fire..."* are forbidden. The widget's footer counter + Apply button are self-explanatory; explaining them in markdown text duplicates the surface and signals fallback-mode behavior. If you ever feel like writing prose about what the buttons do, STOP — emit the widget and trust it.
2. **Do NOT leak internal routing metadata into chat.** Forbidden patterns include `Domain match: x@y.com → Org Name (project_NNN, active)`, `Routing: stage 3 of 5`, `Confidence: 0.87`, `entities.json line 142`, internal entity IDs (`person_NNN`, `project_NNN`, `org_NNN`), event seq numbers, file paths under `_hq/staging/`, debug strings, "phase 4" labels. These are internal mechanics and never appear in user-facing output. The user sees PEOPLE NAMES and PROJECT NAMES — never the internal machinery. And the person name shown is the RESOLVED record's spelling (`canonical_name` via `entity_resolve`), never a transcript/ASR spelling — F-50 P2b rendered "Myra Samples" in a widget for a correctly-resolved Mira Sample. Raw spellings appear only inside verbatim evidence quotes or on genuinely unresolved rows (see `shared/ENTITY_RESOLVE_PROTOCOL.md` § Display names).
3. **Do NOT include brief / .docx links inside the widget HTML.** The widget's `_render_widget_item` no longer renders `artifact_link` (v2.11.3+). The `artifact_link` field stays in the data shape so the orchestrator can collect paths for `present_files` — it does not appear in the widget body.
4. **Do NOT fall back to markdown narration, a hand-built widget, or a compact freelance render if `mcp__visualize__show_widget` errors or is unavailable (FS-08 no-silent-fallback).** SAY SO in plain English and STOP: surface the error string verbatim, or `(Widget surface unavailable — re-fire when the visualize MCP is reachable.)` Do not improvise a text-based action surface, a custom wire prefix, or a "here's what it would have shown" summary. The pre-flight check (`from widget_transport import render_and_persist; print('OK')`) catches the renderer/transport-import case; if `show_widget` itself is missing or errors, surface the same plain-English abort. A mandated call that cannot execute is reported, never worked around quietly.
5. **Do NOT post any commentary AFTER the widget + Links section.** No "Surfaced 5 items from this morning's scan." No "Cadence math is in early-baseline mode for almost everyone." No "Diversification rule pulled X into slot 1." No "Wrote pattern_break_detected × 5, dont_forget_run, pack_run to events.jsonl." No "Backup at events.YYYY-MM-DDTHHMM.dont_forget.bak.jsonl." No "I noted that in the Quick read." NO commentary at all about what you observed under the hood, what you wrote, what backups you made, what scoring decisions you made, what diversification did. (v2.12.5+ — per M's Apr 30 ask: *"this technical stuff should not show up."*)

   The widget IS the surface. The Links: section IS the source-link layer. After those two, the chat turn is DONE. If you want to write any of that diagnostic / process commentary, write it to `events.jsonl` as a `pack_run.notes` field (silent per Rule 9) — never to chat.

   This applies even if you think a note adds value ("FYI the Acme Co items a+e1 are the same signal"). The user can see that themselves; if they can't, the orchestrator's data-shape build is the bug — fix it there (merge the items) instead of explaining it after the fact.

6. **Pass ONLY `transport["html"]` (the persisted page's validated bytes, verbatim) as `show_widget`'s `widget_code`, and do NOT echo any fragment of widget HTML into chat text** (style blocks included — the `::view-transition-group` prelude, F-09). Never hand-compose widget HTML, never post-process `transport["html"]`, and never relay an unbounded set in one page — paginate. The `widget_code` parameter is the only carrier; the bytes never appear in chat text. (T2)

**Why these rules exist:** v2.11.0/v2.11.1/v2.11.2 surfaced cases where (a) brief paths inside widget rendered as unclickable text masquerading as links, (b) inbox orchestrator emitted both a widget AND a markdown paragraph describing the widget, (c) upcoming-meetings dumped routing metadata like `Domain match: sam@example.com → Summit Company (project_002, active)` into chat. v2.11.3 closes these as forbidden patterns.

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
| `orchestrator-staff-meeting.md` + morning-briefing / coach card | Living Brain queue (`src: "cr-brain"`) | All actions batch. Per-kind dispatch via `context.kind` (see the LB1 action section above). Staff Meeting renders the FULL paginated queue + change feed + "This week's moves"; daily surfaces render the ≤5 card. |

## Apply-time output contract (v2.12.4+)

The widget collects selections; the user clicks Apply. What happens AFTER apply produces drafts or documents needs the same shipping discipline as the original orchestrator output. Per M's Apr 30 ask: *"It is just the easiest way to just get an email out because you can edit and send inline if needed — like the original output."*

Three rules govern the post-Apply chat turn (enforced in `apply-choices/SKILL.md` Step 4):

### Rule 1 — Drafts and documents come back in the SAME widget format

If any apply-time action produces an email draft (push meeting, draft re-engagement, follow-up call, status check, propose time, schedule catchup, etc.) OR a regenerated document (add more context regenerating the brief, escalate to memo producing a memo .docx), those outputs render through `render_chat_output_widget` as a NEW widget — not as inline markdown.

The new widget's items use the same shape as email-shaped items: metadata, body_lines, original_thread (when relevant), action set `the standard email-card controls — Send / Draft / Snooze (3 days) one-tap buttons and the directly-editable body (FB-17; labels from the verb taxonomy; prose names only what the card shows, t3 FB-11)`. Documents render with `artifact_link` inline. The user can edit + send inline without retyping.

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

- **Long sessions:** if M has 20+ items in one fire and only resolves 5 in this sitting, the other 15 disappear on next fire (because state doesn't persist across refresh). Should the widget offer a "save selections without applying" path? Layer-2 question.
- **Multi-skill batching:** if M has Commitments + Inbox + Staff Meeting all open in the same chat, three separate widgets exist. Should there be a unified "Apply across all widgets" affordance? Probably not — adds complexity, M can just hit Apply on each.
- **Heavyweight action ergonomics:** if 5 of 8 selections are `send` (heavyweight), Apply produces 5 sent-email confirmations in one chat turn. That's a lot of output. May want to chunk or summarize. Per-skill to address.

## See also

- `PROBE_RESULTS_past-meetings-open-items.md` — the empirical test results that drove this design.
- `HANDOFF_past-meetings-open-items-ux.md` — the original spec from Cowork (largely superseded by M's "no sidebar artifacts" directive; this widget spec is the resolution).
- `HANDOFF_cr-plugin-feedback-v2-10-9_2026-04-29.md` — the broader v2.10.9 feedback batch state.
- `_hq/CONVENTIONS_EMAIL_PREVIEW.md` — email draft format used inside the widget for any item that has a draft.
- `_hq/CONVENTIONS_SOURCE_LINKS.md` — source-link format used in item context (linked phrasing, source emails, transcripts).
