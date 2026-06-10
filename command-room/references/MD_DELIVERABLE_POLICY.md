# `.md` Deliverable Policy (v3.7.0+)

**Purpose:** the canonical taxonomy for when a Command Room output may be saved as `.md` and when it must be `.docx`. Companion to `shared/CONTRACT.md` Rule 27 (the contract) and `tests/run_no_md_deliverables_test.py` (the enforcement).

**Read by:** skill authors deciding output format, the static guard test (which scans plugin source against the rule), and `cleanup` (which flags any drift in workspace state).

---

## The rule, in one sentence

**Polished output the user opens to read → `.docx`. File Claude reads as context/memory → `.md` is fine.**

---

## Why this rule exists

Pre-v3.7.0 several skills saved polished reports as `.md`:

- `automation-scanner` → `_hq/audit-reports/automation-scan-<date>.md`
- `dormant-customer-scan` → `_hq/dormant/DORMANT_SCAN_<date>.md`
- `cleanup` → `_hq/audit-reports/<date>-cleanup.md`
- `operator-report` → `_hq/operator-reports/<month>.md`
- `follow-up-ritual` → `<project>/meetings/FollowUp_<meeting>_<date>.md`
- `memo-writer` → both the `.docx` deliverable AND a redundant `.md` "for review"
- `email-writer` → `<project>/deliverables/email_drafts/<date>_<recipient>_<topic>.md`
- `workspace-manager` (deep-clean) → `_hq/audit-reports/<date>-maintenance.md`

When customers opened these in Word or Pages, they saw raw markdown syntax (`# Heading`, `**bold**`, `| table | rows |`) instead of formatted prose. Readability complaints accumulated. The pre-v3.7.0 `_hq/audit-reports/` and `<project>/deliverables/` paths existed for documents customers were expected to *open and read*; `.md` was the wrong format for that surface.

This rule sets the policy and the test guard enforces it.

---

## The deliverable / context taxonomy

### Deliverables (must be `.docx`, never `.md`)

A file is a **deliverable** if any of these are true:

- The user opens it to read polished output (one-pager, memo, brief, report, pack).
- The user might share it (board pack, contract review, follow-up email pack).
- It lives in a directory named after deliverable shape: `deliverables/`, `audit-reports/`, `operator-reports/`, `dormant/`, `one-pagers/`, `memos/`, `board-packs/`, `email_drafts/`, `speeches/`.
- Its filename matches a deliverable-prefix pattern: `FollowUp_`, `OnePager_`, `Memo_`, `StressTest_`, `Call_Prep_`, `Past_Meeting_`, `BoardPack_`, `ContractReview_`, `DecisionMemo_`, `DORMANT_SCAN_`.

These ALL save as `.docx` (or `.pptx` for slide decks, `.xlsx` for tabular data). Route through `shared/scripts/brief_writer.py` for layout consistency.

### Context / memory (`.md` is correct)

A file is **context** if any of these are true:

- Claude reads it on future sessions to inform work (`PROJECT_CONTEXT.md`, `PROJECT_BRAIN.md`, `SESSION_NOTES_*.md`, `CLAUDE.md`, `BUSINESS_CONTEXT.md`, `BRAND_VOICE.md`).
- It's an auto-regenerated view of substrate state (`_hq/views/TIMELINE.md`, `DECISION_LOG.md`, `MASTER_TRACKER.md`, `PEOPLE.md`, `RELATIONSHIPS.md`).
- It's an ephemeral snapshot consumed by next-session delta detection (`_hq/briefings/<date>.md`, `_hq/briefings/morning-<date>.md`).
- It's part of the knowledge base (`_hq/intel/INDEX.md`, `_hq/intel/KNOWLEDGE_BASE.md`, `_hq/intel/<date>-<slug>.md`).
- It's a synthesis pass surfaced as next-day chat context (`_hq/insights/<date>_insights.md`).
- It's voice corpus / calibration data (`_hq/voice/*.md`, `*.jsonl` corrections logs).
- It's an ingested meeting transcript (`_hq/meetings/*_transcript.md` — raw text source, not the brief).

These ALL stay `.md`. The user occasionally opens them but they're working files, not polished outputs. Rendered in chat when surfaced; not expected to look pretty in Word.

### Edge cases — how to decide

| Situation | Format |
|---|---|
| The user explicitly asks for "the document I can send to my board" | `.docx` |
| The user asks for "the brief I'll skim before my meeting" | `.docx` (this is what `call-prep` does) |
| The user asks for "an update I'll share in the team channel" | `.docx` (or, if it's email/Slack copy, Gmail Draft / Slack draft, not a file at all) |
| The user asks for "your notes on what we discussed" | `.md` (working notes, context for future sessions) |
| The skill produces a list Claude will read in pulse synthesis | `.md` |
| The skill produces a report the user will open in 30 days to review | `.docx` |

When in doubt: ask "would the user open this and want it to look like a Word doc?" If yes, `.docx`. If they'd open it mostly to copy text into something else, or if Claude is the primary reader, `.md`.

---

## Email drafts are a special case — no file at all

Pre-v3.7.0, `email-writer` and `follow-up-ritual` saved drafts as `[Project]/deliverables/email_drafts/<date>_<recipient>_<topic>.md`. This pattern was vestigial from before Zapier reply-threading worked (v3.2.2). Now that Gmail Drafts is the canonical destination:

- **Email drafts go to Gmail Drafts**, not a saved file. The deliverable IS the draft sitting in Gmail, ready for the user to review and click Send.
- The `_hq/data/events.jsonl` event `email_drafted` records the action with `gmail_draft_id` so the substrate knows the draft exists.
- No `.md` file. No `.docx` file. The draft lives in Gmail; the event references it.

`follow-up-ritual` produces *two* things post-meeting: (1) per-attendee email drafts (→ Gmail Drafts) and (2) a summary pack (→ `.docx`). The pack is the deliverable the user opens; the emails are drafts the user sends from Gmail.

---

## Format-by-skill reference (post-v3.7.0)

| Skill | Output | Format |
|---|---|---|
| `automation-scanner` | Audit report | `.docx` |
| `automation-scanner` (v3.8.0+) | Substrate events | `events.jsonl` |
| `call-prep` | Meeting brief | `.docx` |
| `dormant-customer-scan` | Dormancy report | `.docx` |
| `email-writer` | Draft | Gmail Draft (no file) |
| `follow-up-ritual` | Pack | `.docx` |
| `follow-up-ritual` | Per-attendee drafts | Gmail Drafts (no file) |
| `meeting-notes` | Decisions + commitments | `events.jsonl` (no file beyond the transcript itself) |
| `memo-writer` | Memo | `.docx` only (the legacy `.md` "for review" is retired) |
| `morning-briefing` | Daily digest in chat | Chat (rendered markdown) + `.md` snapshot for next-session delta |
| `one-pager-composer` | One-pager | `.docx` |
| `operator-report` | Monthly Operating Lift | `.docx` |
| `stress-test` | Pre-mortem doc | `.docx` |
| `cleanup` | Audit report | `.docx` |
| `weekly-recap` | Recap | `.docx` |
| `workspace-manager` (deep-clean) | Maintenance report | `.docx` |
| `intel-intake` | Captured intel | `.md` (context) |
| `insight-generator` | Synthesis pass | `.md` (context, surfaced in next-day chat) |

The new v3.8.0 skills all default to `.docx` (or no-file with Gmail Drafts / calendar invites): `contract-review`, `decision-memo-composer`, `board-pack-assembler` are `.docx`; `calendar-writer` produces calendar events; `intro-broker` produces Gmail Drafts; `decision-revisit`, `thread-resurrection` produce widgets + events with no file writes.

---

## When this doc gets stale

Every release that adds a new output path, retires an old one, or changes a skill's output format must update the format-by-skill table above. Treat it like CHANGELOG: append-style edits, version-tagged.

If a future release relaxes or extends the rule (e.g., new allowed `.md` context directory), update both the test exemption list AND this doc in the same commit. The doc and the test must stay in sync.
