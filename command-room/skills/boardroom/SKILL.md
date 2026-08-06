---
name: boardroom
surfaces: both
description: "Put one subject — a plan, deal, hire, or pricing change — to a board of independent perspectives and get back the conflict map: where the seats disagree and why. Fires on: 'convene the board on [subject]', 'boardroom', 'what would my board say about [subject]', 'run this by the board', 'board review of [subject]', 'C-suite take on [subject]', 'quick board take'; setup via 'configure my board', 'set up my board', 'show my board'. Each seat is its own subagent reading its own workspace slice, blind to the others; seats are archetypes or imported Advisor Profiles. Does NOT fire on 'stress test' / 'pre-mortem' (stress-test — single-lens failure mapping), 'decision memo' (decision-memo-composer), 'build the board pack' (board-pack-assembler), or 'forge my advisor profile' (advisor-export). Seat model and fences: Routing section in the body."
---

## Recommended Model

**Default: Opus.** Boardroom spawns 5–6 parallel reasoning agents plus a synthesis pass; the value is in the quality of each independent judgment and the sharpness of the conflict map. This is exactly the work Opus pays back on. Sonnet is acceptable only for the lightweight `show my board` / `board history` read-back modes.

## Entity-resolve + canonical-helper enforcement

When the subject names a project, person, or org ("board review of the Acme acquisition", "convene the board on the [project]"), you MUST resolve that scope through `shared/scripts/entity_resolve.py::resolve_all(workspace_root, query)` before loading its context — never substring-grep entities.json directly. See `shared/ENTITY_RESOLVE_PROTOCOL.md`. If the subject is a free-form plan with no named entity, skip resolution and work from the pasted text.

## Skill Boundary (v2.1)

- **Use boardroom for:** multi-lens evaluation of ONE subject. Many perspectives, one thing → a conflict map.
- **Use `stress-test` for:** single-lens failure-mode depth on one plan (pre-mortem / inversion). One lens, deep.
- **Use `decision-memo-composer` for:** structured tradeoff between 2–4 named options with weighted criteria.
- **Use `decision-revisit` for:** re-examining a past decision against new signal.
- **Use `board-pack-assembler` for:** the monthly board-meeting reporting artifact (KPIs, financials) — not deliberation.
- **Use `advisor-export` for:** forging / importing the Advisor Profile persona packs that boardroom can seat as guest directors.

## Personification Contract

Before composing the board memo, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The document header below the title uses:

```
Board Review · {Subject}
Convened by {brain_name} for {first_name} · {Date}
```

`{first_name}` comes from `entities.json` `workspace.user_first_name`; `{brain_name}` defaults to `"Penelope"`. This is NOT a voice-composer skill — seat verdicts are archetypal reasoning, not the operator's writing voice, so Voice Calibration (Gate 14) does not apply. The byline carries the personification; the analysis stays formal.

## Writer Contract (substrate-native)

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes use the atomic helpers — no raw `open(path, "w")`.

**Primary writer for:**
- `board_convened` event appended to `_hq/data/events.jsonl` via `shared/scripts/atomic_write.py::atomic_append_jsonl` (omit `seq` — the append gate auto-stamps it; never pre-compute via `next_seq`). Shape: `{subject, scope_project_label, seats[], verdicts[], conflicts[], asks[], artifact_path}`. Seats list each seat's `{name, type}` where type ∈ `archetype` | `persona`. Sole writer of `board_convened`.
- The board memo `.docx` at `[Project]/deliverables/BoardReview_[Topic]_[YYYY-MM-DD].docx` (or `_hq/deliverables/BoardReview_[Topic]_[YYYY-MM-DD].docx` with no project scope). Per CONTRACT Rule 27 the deliverable is `.docx`, never `.md`. Surface it as an H2 clickable link via `shared/scripts/brief_path.py::get_brief_artifact_url()`.

## Deliverable Render Gate (GATE1 — MUST, P1.9)

This skill produces a `.docx` deliverable. It MUST be produced through the canonical chokepoint — no exceptions:

- **Render ONLY via `shared/scripts/brief_writer.py` `make_brief(brief_kind="board_review", ...)`** (eyebrow "BOARD REVIEW"). That single call runs the output-contract gate (required sections: Subject & framing / Verdicts / Conflict map / Per-seat detail / The board's asks), the voice-tell gate, and the post-render leak scan, in that order, BEFORE the file is written.
- **NEVER hand-roll a `.docx`** with the generic docx skill, `python-docx` directly, or docx-js — those paths bypass every gate.
- **NEVER create, render, copy, upload, or update the board review — or any part, derivative, or restatement of it ("the conflict map", "one seat's verdict", "a summary") — through Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not the project's `deliverables/` folder (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "for sharing", "so I can send it to the real advisor this seat is modeled on", "as a copy alongside the canonical file" — **nor a direct instruction**: "put the board review in a Google Doc" is a request this gate refuses, not an override. Hand back the canonical file's link. Seat verdicts read as attributed positions — an ungated copy of a persona seat's reasoning is the worst thing in this workspace to lose folder control of.
- **NEVER answer a board review with a chat-only draft** unless the user explicitly asks for no file — and then flag that the gates only run on the rendered file.
- **Detectability:** `make_brief` emits a `gate_ran` audit event; a boardroom fire that yields a document with no `gate_ran` event for that turn is a flagged bypass. Pass `workspace_root`.
- The bench config at `_hq/data/skill_config/boardroom.json` via `shared/scripts/skill_config_writer.py` (`save_skill_config` / `load_skill_config` / `wipe_skill_config`). Emits `skill_first_run_configured` / `skill_reconfigured`.

**Reads from:**
- `_hq/data/skill_config/boardroom.json` — the configured bench (falls back to the default five archetypes if unconfigured).
- `_hq/data/advisors/*.json` via `shared/scripts/advisor_profile_writer.py::list_advisors()` — available persona seats (imported or locally-modeled colleagues). This is the consumer half of `advisor_profile_imported` / `advisor_profile_modeled`.
- `_hq/data/events.jsonl` — `board_convened` events (its own, for `board history` / `reconvene`); `decision` / `decision_superseded` events on the subject (the skeptic seat); `commitment` events (the COO seat's load check); financial events. **Read via the org-scoped reader, never a raw load** (PGUARD1): `from events_io import load_events_org_scoped; events, skipped = load_events_org_scoped(workspace_root)` — the account-scope mask and personal-lane drop apply by design, so no seat ever reasons from a reclassified personal account's history or a personal reminder.
- `_hq/data/entities.json` — project + person context; `workspace.user_first_name`.
- `_hq/intel/*.md` — captured intel on the subject (the strategy seat).
- QuickBooks MCP — P&L / cash / AR aging for the CFO seat when connected.
- Project context (`PROJECT_BRAIN.md`, `SESSION_NOTES_*`) when the subject references a project.

**Conflict boundary:** sole writer of `board_convened` and of `boardroom.json`. No writes to `entities.json` / `aliases.json` / advisor packs (those belong to people-crm and advisor-export respectively). Deliverable file + one event append + config write only.

## What It Doesn't Do

- Does NOT model seats on real people by default. Default seats are functional archetypes. A real person only enters the board as an explicitly imported/modeled Advisor Profile via `advisor-export` — never inferred silently from your contacts.
- Does NOT decide for you. It maps where independent lenses agree and conflict and surfaces the board's asks. Logging a decision is a separate, offered step (`decision-log`).
- Does NOT run on a schedule. It's an explicitly-invoked deliberation; the parallel-subagent cost is too heavy to fire unattended.
- Does NOT exceed six seats. Beyond six the verdicts blur and the conflict map loses signal — setup caps the bench at six (archetype + persona seats share the cap).
- Does NOT put the operator's writing voice in the memo. Seats reason archetypally; the memo is formal analysis.

## How to Use

```
"convene the board on the Q3 pricing change"
"what would my board say about hiring a Head of Sales now"
"run this by the board: <paste a plan>"
"board review of the Acme acquisition"
"quick board take on raising the seed now"      # skips Round 1
"configure my board"  /  "change my board"  /  "show my board"
"what did my board say about pricing"  /  "board history"   # read-back, no new convene
```

## How It Works

### Setup (first convene, or "configure my board") — "show, then tune"
On the very first convene with no `boardroom.json`, do NOT interrogate first: run with the **default five archetype seats** and append a one-time footer offering to tune the bench. The default bench:

| Seat | Mandate | Substrate slice (read blind to other seats) |
|---|---|---|
| CFO | Can we afford it; what does it do to cash and margin? | QuickBooks MCP; financial events |
| COO | Can we execute this with current load? | open `commitment` events; stalled-project signal; project status |
| Customer voice | What does this do to revenue and how customers experience us? | people-crm records; dormant-customer cadence; recent transcripts on the topic |
| Independent director (skeptic) | Where does this contradict what we said before; what's assumed without evidence? | `decision` / `decision_superseded` events; prior `board_convened` |
| Strategy | Does this position us well 12–24 months out? | `_hq/intel/*`; BUSINESS_CONTEXT; competitive signal |

On `configure my board` / `change my board`: load current bench, let M drop seats, sharpen a mandate, rename, add from the seat library (CTO/product, General Counsel/risk, CPO/people, Brand/comms), or **add a persona seat** from any advisor in `list_advisors()`. Cap six. Save via `save_skill_config`. `show my board` renders the current bench read-only; `reset my board` wipes the config.

### Round 1 — the grilling (skippable)
Each seat reads its substrate slice and asks **its single hardest question** — the one M would least want asked. All questions surface together in one widget rendered via `shared/scripts/chat_output_renderer.py::render_chat_output_widget()` and posted via `widget_transport.render_and_persist` → `show_widget` (`transport["html"]` as `widget_code`) per `shared/CHAT_ACTION_WIDGET.md` § Transport — never markdown numbered actions — each with an answer field, plus one widget-level **`skip all`** action (canonical bulk verb — displays "Skip all"; the intro line above the widget says "answer any, or skip all to go straight to verdicts"; dispatch in apply-choices' `boardroom` source entry). The user answers inline, selectively, or skips all. The trigger `quick board take on` skips Round 1 entirely.

### Round 2 — verdicts (parallel subagents)
One subagent per seat, launched in parallel, each receiving: the subject, M's Round 1 answers, its mandate/temperament, and read instructions for **its slice only**. Seats never see each other's output — blind independence is the point; it prevents the harmonization drift single-pass roleplay produces. A **persona seat** additionally receives its Advisor Profile pack as its character sheet and reasons as that colleague would, applied to *this* workspace's real numbers. Each seat returns:
- **Position:** Support / Oppose / Conditional — condition stated as something verifiable.
- **Evidence:** 2–3 specific data points from its slice (named amounts, dates, commitments, decisions).
- **The one thing:** what this seat would force before proceeding.

For stated-opinion grounding on a persona or referenced advisor, pull from recent transcripts via `transcript-search` (the same mechanism `decision-memo-composer` uses) rather than assuming.

**Numeric verification (R6 — `shared/SUBAGENT_VERIFICATION.md`).** A seat's Evidence is qualitative reasoning plus the ids/refs it read; it is NOT a trusted total. Before any figure a seat cites reaches the synthesis or the memo, re-derive it in code through the canonical helper — commitment counts via `commitment_state.commitment_counts`, delivered-work counts via `value_receipt.compute_value_receipt`, event recency via `event_time.event_time`, financials from the QuickBooks MCP directly. A blind subagent tally is exactly what produced the −70%/−"1 of 11" errors this gate exists to stop; the seat says "sales commitments are piling up," the code says "37 open."

### Synthesis
A final pass over all verdicts builds the **conflict map**: each disagreement, the underlying assumption driving each side, and the 2–3 questions the board would require answered before a green light. This is the core of the deliverable, not the per-seat essays. Every number that survives into the conflict map or the memo is the code's (per the numeric-verification gate above), never a seat's hand-count.

### Chains (offered, never auto-fired)
- Genuine A-vs-B surfaced → "Take this to a decision memo" (`decision-memo-composer`, pre-seeded with options + the seats' criteria).
- One seat flags a dominant failure mode → "Stress test this" (`stress-test`, pre-seeded with the mode).
- M proceeds and states a decision → "Log it" (`decision-log`, with the board memo as the rationale link).

## Output

1. A **chat summary**: the verdict line per seat (Support/Oppose/Conditional) + the conflict map headline + the board's asks. No internal IDs, phases, or jargon (leak-clean per `shared/CHAT_ACTION_WIDGET.md`).
2. The **board memo `.docx`** — rendered through `make_brief(brief_kind="board_review", ...)` per GATE1 above — surfaced as an H2 clickable link via `shared/scripts/chat_output_renderer.py::doc_headline_link()` over `brief_path.py::get_brief_artifact_url()` — never plain-text path narration. Memo sections (sync rule: mirrors `output_contract_validator.py` `RULES_BY_KIND["board_review"]` — edit both or neither): Subject & framing → Verdicts (table) → Conflict map (the core) → Per-seat detail → The board's asks.
3. One `board_convened` event appended to the substrate.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Put one subject — a plan, deal, quarter, hire, pricing change, or product direction — to a board of independent perspectives and get back the conflict map: where the seats disagree and why, because that's where the blind spots are. Each seat runs as its own subagent reading its own slice of your substrate (financials, commitments, decisions, customers, intel), blind to the others, so the views argue from evidence instead of improvising. Seats are functional archetypes by default (CFO, COO, customer voice, skeptical independent director, strategy) and can be customized; a seat can also be a real colleague's imported Advisor Profile so your board includes how they'd actually think. Use when the CEO says 'convene the board on', 'boardroom', 'what would my board say about', 'run this by the board', 'C-suite take on', 'board review of', 'quick board take on'. Setup: 'configure my board', 'set up my board', 'change my board', 'show my board'. DOES NOT fire on 'stress test' / 'pre-mortem' / 'poke holes' (stress-test — single-lens failure-mode mapping), 'help me decide between' / 'decision memo' (decision-memo-composer — structured A-vs-B tradeoff), 'build the board pack' (board-pack-assembler — reporting artifact, not deliberation), 'what did we decide' (decision-log retrieval), or 'forge my advisor profile' / 'import advisor profile' (advisor-export — produces the persona packs this skill seats).
