# Command Room — Output Contract (v2.13.0+ canonical)

**The single source of truth for what every scheduled-task and apply-time output must look like.** Compiled from M's feedback across this session and prior chats. Any failure to comply is a bug, not a style choice.

This file is consumed by `chat_output_renderer.py` validators (canonical-action set, leak patterns, required fields) and by `apply-choices/SKILL.md` (apply-time enforcement chain). Orchestrators reference this contract in their Phase Setup; the renderer enforces it at render time.

> **Peer contract — the Executive Output Standard.** Where this file governs *how* outputs are written (leak-clean, plain-English, widget-shaped), `shared/EXECUTIVE_OUTPUT_STANDARD.md` (EXEC1) governs *what an executive gets from the page* — the 30-second exec header, recommendation-before-analysis ordering, derivable-only money/time quantification (`shared/scripts/quantify.py`), the explicit ASK block, inline confidence honesty, and the detail ladder. It is enforced at the `brief_writer.make_brief()` chokepoint (`exec_header` / `asks` kwargs + ordering check) and adds generic-summary banned-header patterns to the leak scanner. The two contracts coexist; neither supersedes the other.

---

## Rule 1 — Widget format is the ONLY action surface

Every scheduled-task fire AND every apply-time response that contains an actionable item MUST render as a widget via `mcp__visualize__show_widget`. Markdown numbered actions (`▸ send 1 ▸ draft 1 ▸ edit 1 [your changes] ▸ keep 1`) are forbidden. Widget HTML is produced by `render_chat_output_widget()`. No exceptions.

If the renderer pre-flight import check fails: ABORT, surface plain-English error. Do NOT improvise markdown.

## Rule 2 — Apply-time drafts come back as a widget

Per M's standing rule: *"if you need to send an email — the widget should open."* Whenever apply-time produces an email draft (push meeting, draft re-engagement, follow-up call, status check, propose time, schedule catchup, an in-flight `edit then send` rewrite, etc.), the response includes a NEW widget with that draft surfaced through the same standard email-card controls — Send / Draft / Snooze (3 days) one-tap buttons and the directly-editable body (FB-17; labels from the verb taxonomy; prose names only what the card shows, t3 FB-11) action set.

Multiple drafts → ONE widget with N items, NEVER N separate widgets. Per M's ask: *"You can host all of those in the same widget."*

## Rule 3 — Documents are clickable + openable in Cowork (v3.13.0+ — H2 heading link as primary; present_files demoted to reveal-in-folder)

Every regenerated brief, memo, or other document produced by an action MUST surface as a clickable hyperlink the user can click to open in Cowork. NEVER as a plain-text path or "the file was generated to..." narration.

**File save location:** `_hq/meetings/<filename>` for all meeting briefs (`Past_Meeting_*.docx`, `Call_Prep_*.docx`). Computed exclusively via `shared/scripts/brief_path.py` `get_brief_path()`. Per M's Apr 30 ask: *"these files were generated to a folder I cant open so make sure you always generate to somewhere in hq."*

**Surface (v3.13.0+ — H2 heading link):**

1. **Where a landing answers with `opener_line`, that line is the pointer and no headline link follows it** (HYGIENE3, R-RW2-4: a headline with no target is a dead arrow, and one beside the saved-at line says the pointer twice). Otherwise, **the canonical surface is the H2 heading link** — `## → **[Document title](computer://<native-windows-path>)**`. Rendered via `chat_output_renderer.doc_headline_link(label, url)` (single-doc) or `doc_headline_link_h3(label, url)` (multi-doc lists). Single-doc skills (`memo-writer`, `one-pager-composer`, `decision-memo-composer`, `board-pack-assembler`, `contract-review`, `operator-report`, `stress-test`, `dormant-customer-scan`, `automation-scanner`, `scaffold-automation`, `team-intelligence`, `cleanup`, `weekly-recap`) use H2. Multi-doc surfaces (`cr-upcoming-meetings` with several briefs) use H3.

2. **The file card is DEMOTED to a "reveal in folder" secondary** — the card is delivered by the harness (`SendUserFile`) where the harness has one, else by the link; `present_files` is the name the retired desktop tool went by, and the shim is where that name lives now. — pre-v3.13.0 it was the primary opener; M's 2026-05-20 testing showed the cards' primary click DOESN'T open most file types (especially `.md`) — only "Show in Folder" works. So cards are no longer the opener. Keep them as a reveal-in-folder convenience IF the user might want to navigate the file system to that location; otherwise drop them entirely. NEVER surface a `present_files` card as the only way to open a deliverable; the user will find it doesn't work.

3. **Source-citation links stay in the `Sources:` section as plain inline links** per `_hq/CONVENTIONS_SOURCE_LINKS.md` (the canonical convention doc lives in the workspace, not the plugin) — `_link(label, url)` returns `[label](url)`. Gmail threads, Granola transcripts, Drive docs, calendar events in `Sources:` do NOT use the H2 format. Source citations and generated-deliverable links are visually distinct on purpose: one is action ("open this thing I made"), the other is provenance ("here's what informed it").

4. **In-widget `artifact_link.url`** — kept as data-only per `orchestrator-upcoming-meetings.md` line 256 (Cowork's iframe sandbox doesn't reliably resolve `computer://` links inside widget HTML; the widget side is unpainted post-v2.12.0). The H2 link below the widget IS the surface that actually opens — don't try to paint anything inside the iframe.

**URL format (v3.13.0+ — native Windows form):** Cowork's Windows local-file resolver opens the native form: `computer://C:\Users\Sample\Desktop\Claude\Command Room\...\file.docx` — TWO slashes (not three), backslashes preserved, spaces UNENCODED. (The space in `Command Room` is the load-bearing part of this example, not the username.) URL-encoded variants (`%20` for space, `%3A%5C` for `:` and `\`) do NOT open. POSIX absolute paths (macOS/Linux) keep the existing `computer:///url-encoded-path` form — only the Windows-with-space case was broken pre-v3.13.0.

This format is produced by `shared/scripts/brief_path.py` `get_brief_artifact_url()`. The path is hidden behind the link label — never appears as visible text per Rule 4.

**Cloud-mounted workspaces (v5.9.2+, platform-neutral v5.11.1 — cloud-aware opener):** a `computer://` link only opens a file that exists on the customer's own machine. A workspace mounted from Google Drive, OneDrive, or SharePoint resolves to a **session-scoped** root (`/sessions/<id>/...` — `brief_path.is_session_scoped_path()` is the test), so a `computer://` link there is a guaranteed "Failed to load local file." on click even though the deliverable saved fine (QMG field reports, 2026-07-28 / 2026-07-31 / 2026-08-11 — the third was a OneDrive/SharePoint workspace the v5.9.2 Drive-only lookup could never serve, BUG-8538). On a session-scoped workspace root, after saving the deliverable, look up the saved file's web link on the workspace's OWN cloud platform: discover the drive tool with the workspace host preferred — `tool_discovery.discover_drive_tool(tools, "search", prefer_platform=tool_discovery.infer_workspace_drive_platform(workspace_root))`, never first-match when more than one drive is connected (first-match can search the drive that does not hold the workspace) — then search `_hq/meetings/<filename>` or the deliverable's own folder (`google_drive` → the Drive web link; `onedrive` / `m365_sharepoint`, e.g. the Microsoft 365 connector's `sharepoint_search` → the OneDrive/SharePoint web URL) and build the link via `brief_path.get_brief_opener_url(absolute_path, drive_web_url)` — the web URL becomes the opener. If the lookup finds nothing and another drive platform is connected (whether or not a preference was inferred), try that one before giving up. If no lookup succeeds there is NO link for a cloud-mounted workspace (SPEC_NIGHTM2 §5 item 2): `get_brief_opener_url` answers with an empty string rather than a dead one carrying this run's own id, and the surface says where the document went in words, with no href. Host-native roots (Windows drive letter, real local folder) are unaffected — `get_brief_opener_url` returns the same `computer://` form as before.

**Persist relative, post absolute (SPEC BRIEFFIX1 Item A, 2026-08-09).** These are two different strings for one file and confusing them is a bug in either direction. Anything PERSISTED — a receipt pointer, a saved digest snapshot — is workspace-relative (`_hq/meetings/<file>.docx`), because a machine-absolute path is valid only on the machine and in the session that wrote it (BRIEFMERGE §C, `workspace_paths.assert_workspace_relative` refuses one at the write). Anything POSTED to chat is machine-absolute in the `computer://` form above, because Cowork resolves the href against THIS computer's filesystem.

A surface that composes text carrying persisted pointers (the morning brief is the one that does) converts ONCE at post time, on the posted copy only, with `chat_output_renderer.absolutize_doc_links(<the composed text>, <the workspace root>)` — called by that surface's own step, beside the data (on a merged seat, through the workspace access layer like every other read of the workspace — Rule 22), never imported into a shell that does not hold the workspace. It resolves each pointer through `workspace_paths`' anchor machinery and `brief_path.get_brief_opener_url` (so a Drive-mounted workspace gets its web link, per the paragraph above) and leaves everything that is not a workspace-relative deliverable link untouched. **Enforced, not requested:** `validate_chat_output` refuses a rendered payload that still carries a workspace-relative doc href — a card that says "this file can't be found on your computer" while the file sits in the synced folder was the 2026-08-09 field report, and prose asking politely for a conversion is presumed skipped.

**Scope boundary:** the H2 format is for **generated deliverables a skill just produced** (`.docx`/`.pdf`/`.xlsx`/`.pptx` — `workspace_paths.DELIVERABLE_SUFFIXES`). Source citations stay plain. Links inside in-widget HTML stay unpainted.

**Placement (v3.13.0+ — bottom of chat, not interspliced):** per M's 2026-05-20 feedback #6d / #9 / #11, deliverable links MUST land at the bottom of the chat turn, not interspliced through the body where they get lost between paragraphs. The chat turn shape: synthesis content (recap, takeaways, action items, etc.) → blank line → `Sources:` section (if any) → blank line → H2 deliverable link(s). The link is the LAST thing the user sees. Pair with the H2 styling so it pops.

## Rule 4 — No technical language post-widget

Per M's Apr 30 standing rule: *"no technical language post widget."* After the widget posts (and the Links: section, when present), the chat turn is DONE. Forbidden in any apply-time response or trailing commentary:

- Internal IDs: `person_NNN`, `org_NNN`, `project_NNN`, `event_NNN` — and the name substituted for a resolved ID MUST be the record's `canonical_name`, never a transcript/ASR/email-header spelling (F-50 P2b rendered "Myra Samples" for a correctly-resolved Mira Sample; full rule + the unresolved-name carve-outs in `shared/ENTITY_RESOLVE_PROTOCOL.md` § Display names)
- Internal data files: `events.jsonl`, `entities.json`, `aliases.json`, `staging_emissions.jsonl`, `known-newsletters.txt`, `events.schema.json`
- Internal `_hq/` paths: `_hq/staging/`, `_hq/data/`, `_hq/views/`, `_hq/deliverables/`, `_hq/tmp/` (note: `_hq/meetings/` is allowed because it's a user-facing file location for clickable artifacts)
- Internal event-type names in narration: `chat_dismissal event written`, `pack_run complete`, `commitment_resolved logged`, `commitment_updated logged` (v2.14.6+), `commitment_review_proposed logged` (v2.14.6+), `outreach_sent appended`, `pattern_break_detected × N`, etc.
- Plugin-version protocol references: `per v2.12.0+ protocol`, `v2.10.9 spec`, `post-widget chat-links section per v...`
- Schema field names: `last_interaction proposed:`, `primary_thread_id`, `classification_confidence`, `source_event_seq`
- Confidence-score leaks: `1 signal, low confidence`, `confidence: 0.87`, `(N source)`
- Phase / Step labels: `Phase 4`, `Step 7c`
- Domain match / routing metadata: `Domain match: x@y.com → Org Name`, `Routing: stage 3 of 5`
- The literal `apply choices: [{"n":...}]` payload string
- Internal narration: `Now appending events to events.jsonl`, `Wrote pattern_break_detected × 5...`, `Backup at events.YYYY-MM-DDTHHMM.bak.jsonl`, `Filter pipeline pulled 82 raw events...`, `Diversification rule pulled X into slot 1...`
- **The DIAGNOSIS class (LEAK4, 2026-09-20 gate walk)** — the chat explaining the product to the customer after a surface that was already right: a script or skill file by name, a plugin folder, a shared protocol document, a shell command word, an argument token, "session-scoped", a mount beside the workspace, `computer://`, "renderer-side", a skill's directory name in backticks. A step that could not run gets ONE sentence with no file, script, path, variable, shell text or mechanism in it. `surface_leak_patterns.diagnosis_leak_patterns()` is the enforced list.

The leak scanner in `chat_output_renderer.py` enforces this list as a BLOCKING gate on every rendered surface. Any leak detected → ABORT, surface plain English.

**One exception, stated rather than implied (2026-09-21).** The DIAGNOSIS bullet is carried by `chat_output_validator.validate_chat_output` — the gate every COMPOSED sentence passes, and the one `deliverables`, `brain_proposals` and `needs_review_queue` run their lines through. It is not yet on the renderer's blocking gate, which extends its pattern list with the connector, opaque-id and plumbing families only. Putting it there is one line beside that extension and a sweep of every shipped surface behind it, which is an M3 row, not an edit. Until then: a composed sentence is refused, a whole rendered surface is not, and this paragraph is what stops that gap being read out of the list above.

### Non-technical voice — broader than just no-leaks (v3.13.0+)

Per M's 2026-05-20 directive: *"these people don't know what jsons are or paths or anything like that — they don't even know really how the system works — they just want the system to work."*

Rule 4 above is the LEAK-PREVENTION half (specific tokens that must never appear). This subsection is the **VOICE half** — even when no leak token is present, the tone and framing must read as a friendly human assistant, not as an engineer. Rule 4 prevents `events.jsonl` from appearing; the voice rule prevents output that sounds engineering-shaped even without leak tokens.

**Forbidden voice patterns (regardless of whether they trip the leak scanner):**

- **Scary error framing** — `FAIL`, `CRITICAL`, `ABORT`, `ERROR`, `Failure: X`, all-caps alarm language. Real users read these as "the system broke" even when nothing is broken.
- **User-shaming numbers** — scores like `90/180`, percentages like `52% drift rate`, grades like `Workspace health: D+`. Numbers ARE fine when they're factual (`3 commitments due today`); they're forbidden when they read as a judgment on the user.
- **Internal mechanism names** — `Phase 4`, `Step 7c`, `Pass 9`, `Tier 2 view`, `the CRU layer`, `the substrate`, `the orchestrator`. The user doesn't care what the internal architecture is called. Say what the THING IS, not what we named it.
- **Jargon without explanation** — `drift`, `overlay`, `dialect`, `closure event`, `provisional review`, `schema validation`, `dedup`, `confidence threshold`. These are precise terms for us; they're noise for the user.
- **Internal narration** — `Now scanning events...`, `Computing dormancy...`, `Regenerating view...`, `Validating shape...`. These describe HOW we do the work, not the OUTCOME the user cares about. Skip them.
- **Process / version references** — `per v2.14.38 spec`, `v3.4.5+ behavior`, `the post-widget protocol`. The user is on whatever version they're on; they don't read the changelog. Just do the thing.
- **System-state pessimism** — `Your workspace has problems`, `Several issues detected`, `Drift class identified`. Reframe as forward action: `Here's what I'm cleaning up` / `Found a few small things to update`.

**Required voice patterns (replacements):**

| Engineer-shaped (bad) | Friendly-shaped (good) |
|---|---|
| "Scanned `events.jsonl` for commitments" | "Looked through your recent activity" or just SKIP (don't narrate the scan) |
| "FAIL — 68 records failed validation" | "Found a few records I want to update — okay if I clean them up?" |
| "Critical: substrate file fails to parse" | "Quick fix needed — one of your files needs a small repair. I can do it now." |
| "Tier 2 view is stale by 9 days" | "The decision log hasn't refreshed in a few days — I'll catch it up." |
| "Workspace health: 90/180 (50%)" | DROP THE SCORE. Or: "Your workspace is mostly current; here are 3 small things worth touching." |
| "The dormancy scan fired with 5 candidates" | "Found 5 people you haven't talked to in a while." |
| "76 of 83 person records carry legacy schema drift" | "Most of your people records were saved in an older format — I'm updating them to the current shape." |
| "Renderer pipeline emitted full HTML document" | (silent — never surface; this is implementation) |
| "atomic-write-locked + post-write parse check" | (silent — never surface) |
| "Phase 4 widget" | "the action buttons" |
| "the `primary_thread_id` field is null" | (silent — fix it) or "I couldn't tell which project this belongs to" |
| "Detected drift class: shape variance" | "Some records had small differences — I've sorted them." |
| "Backfill sweep proposed: 33 candidates" | "33 people don't have a company linked yet — want to go through them together?" |
| "v3.13.0 introduced atomic-write" | (silent — versions are dev-internal) |
| "Schema enum widened to include `decision_reaffirmed`" | (silent — never surface) |

**Lead with what-to-do-next, not what's-wrong:**

Pre-v3.13.0 many surfaces lead with a problem list. Reframe to lead with action. Examples:

| Lead-with-problem (bad) | Lead-with-action (good) |
|---|---|
| "Issues found: 6 orphan folders, 5 schema-drifted orgs, 14 duplicate seqs..." | "I cleaned up a few things in your workspace this morning. Here's the summary: [list]. Two items want your call — let me know how to handle them." |
| "Your decision log is 57 entries behind." | "Caught your decision log up — here's what's new since you last looked." |
| "FAIL: entities.json fails to parse at line 1734." | "One of your files needs a quick repair. I can restore from this morning's backup automatically — okay to do that?" |

**Tone — friendly assistant, not silent robot or chatty chatbot.** The model is the user's chief of staff. Speak the way a smart, calm, well-organized human assistant would speak. Specific, direct, not breezy ("Sure thing! 😊"), not jargon-y, not narrating its own work. When something goes wrong, normalize it: *"Quick thing — [problem]. Want me to [fix]?"* — not *"FAIL"* and not *"OOPS! Something went wrong!"*.

**Where this applies:** EVERY user-facing output — scheduled-task widgets, on-demand chat responses, error messages, empty states, deliverable contents (.docx body text, not just chat headers), notification text, follow-up confirmations. Internal logs, debug surfaces, and event-stream entries are excepted (those are dev-internal and never seen by the user).

**Enforcement:** the leak scanner catches the specific-token patterns. The voice rule above is broader and isn't fully automatable — it requires the skill author + reviewer to apply judgment per output. The non-technical-voice principle is now CANONICAL across all output surfaces; any skill output that violates it is a bug.

### Scheduled-task naming convention (v3.13.6+)

Pre-v3.13.6 skills referred to scheduled tasks inconsistently: "scheduled task" / "scheduled tasks" / "daily threads" / "scheduled chats" / "schedules" — different vocabulary in different skills, confusing for users learning the system.

**Canonical vocabulary:**

- **The concept** (when referring abstractly to the scheduled-fire mechanism): **"scheduled task"** (lowercase, singular) or **"scheduled tasks"** (plural). NOT "scheduled chats" / "daily threads" / "schedules" / "automations" / any other paraphrase.
- **Specific instances** (when referring to a particular task by its name): **Title Case**, matching the task's display name. The seven canonical scheduled tasks (v3.13.0+) are:
  - **Morning Brief** (daily, weekday morning) — produced by `morning-briefing`, and since SPEC BRIEFMERGE it also runs meeting prep as its first leg
  - **Upcoming Meetings** — **RETIRED** (SPEC BRIEFMERGE, 2026-08-08). Its prep generation is the Morning Brief's first leg. The name stays here so pre-retirement receipts and any still-registered task render with a display name, never a bare id.
  - **Past Meetings** (rolling, post-meeting) — produced by `orchestrator-past-meetings`
  - **Inbox Triage** (daily, weekday morning) — produced by `inbox-triage` via `orchestrator-inbox`
  - **Commitments** (daily, weekday morning) — produced by `orchestrator-commitments`
  - **Friday Wrap** (weekly, Friday afternoon) — produced by `weekly-recap` via `orchestrator-friday-wrap`
- **Lowercase + Title Case mixing** is fine in context: "your scheduled tasks (Morning Brief, Friday Wrap, …) all use the same widget pattern." Both forms appear in the same sentence — concept lowercase, specific instance Title Case.

**Avoid** in user-facing prose:
- "scheduled chats" (was used early in v3.11; legacy)
- "daily threads" (was used in onboarding pre-v3.13.6; legacy — refer to scheduled tasks by what they are, not "threads")
- "automations" / "automated workflows" (those are scaffold-automation's domain, separate concept)
- Hyphenated forms like "scheduled-task" inside user prose (hyphenation OK in code identifiers; not in surfaced text).

This is a documentation standard; the leak scanner doesn't enforce it. Skill authors apply judgment.

## Rule 5 — Action labels: canonical set, no improvisation

Every action label (the `data-action` attribute, lowercase canonical) MUST match the set in `chat_output_renderer.py` `CANONICAL_ACTIONS`. The renderer raises `ValueError` if an orchestrator passes an unknown action verb. No silent acceptance of `keep as draft`, `send as is`, `revise`, etc.

Two specific-name exceptions are accepted (mirror patterns):
- `add as person to <Specific Org Name>` — when adding a person and the target org is known.
- `add as new org <Specific Org Name>` (v2.14.5+) — when proposing a new org and the candidate name is inferable (e.g., from email domain, transcript mention). Mirror of the person variant; same enforcement: empty/whitespace-only suffix is rejected.

The renderer's `is_canonical_action()` recognizes both patterns and accepts them.

Display labels (Title Case) are derived deterministically by the renderer from the canonical action_id. Orchestrators don't pick display labels — they pick action_ids.

## Rule 6 — Plain-English clarity on every action

Per M's Apr 30 standing rule, every action label must be clear about WHAT it does:
- `[your call]` is forbidden — use `decide [text]` (display: `Decide`).
- `manually` is forbidden — use `add context [text]` (display: `Add context`).
- `add to [org]` with placeholder is OK ONLY when the target org is genuinely unknown; when known, render with the specific org name: `add as person to Acme Co`.
- `add as person to ...` vs `add as new org` — verbs distinguish what's being created. Per M: *"tate was a person not an org. We need to make sure this is clear to user."*
- `Edit then save` is forbidden — use `draft` (display: `Draft`). Per M: *"save where? draft is what it does."* (v2.14.4+ — the canonical verb `draft` is the consolidated form of the former `to drafts` + `edit then draft`. It always opens an edit field before saving to Gmail Drafts.)
- `Mark expected` is forbidden — use `resolved [reason]` (display: `Resolved`). Same verb as Commitments YOU OWE — different mechanic, same user-mental-model: "this isn't open anymore."
- `More context` is forbidden — use `context [text]` (display: `Context`). v2.14.37+ — canonical unified context verb. The earlier `add more context [text]` is a deprecated back-compat alias only; new widgets emit `context [text]`.

## Rule 7 — Free-text natural-language time inputs

For ALL date/time/when actions, the input affordance MUST be a free-text single-line input that accepts natural language ("monday at 2", "tomorrow afternoon", "next Thursday", "2026-05-12"). NEVER a strict date picker. Per M's Apr 30 ask: *"push meeting should be an open field and they can write monday at 2 or tomorrow at 3 etc."*

Applies to: `push meeting [date]`, `push to [date]`, `schedule catchup [when]`, `set date [when]`. The input type detector returns `when-text` for these.

## Rule 8 — Calendar HARD SCOPE: native only, never Zapier

Per M's Apr 30 standing rule: *"calendar never goes through zapier - it goes through native connector."*

Tool discovery for ALL calendar operations MUST match `mcp__*google_calendar_*` and EXCLUDE any `mcp__zapier_*` calendar tools. If the only calendar tool exposed is Zapier-namespaced, calendar actions degrade gracefully with a plain-English note. Never silently fall back to Zapier Calendar.

Zapier scope = email `send` + `reply to email` only. NEVER calendar, drive, sheets, docs.

## Rule 9 — Tool discovery is centralized

Helpers in `shared/scripts/tool_discovery.py`:
- **`discover_for_category(category, operation, tools, declared=…)` — SERVER-ID-FIRST resolution (connector-agnostic-v1, the primary path).** When a backend is declared for the category (`connector_config.declared_backend(category)`, keyed by MCP server-id), it resolves the operation on THAT server — deterministic, immune to the substring / H-H hazards. When no backend is declared (empty map), it returns None+reason and the caller falls back to the substring helpers below = today's behavior (R4).
- `discover_calendar_tool()` — native-only, returns matched tool ID or None+reason.
- `discover_gmail_tool(tools, operation)` / `discover_mail_*(tools, declared=…)` — native mail send/reply/draft/search/thread-fetch. Prefer the `discover_mail_*` family: it spans every stack, it identifies a UUID-namespaced connector by the capability manifest's fingerprints when the tool ids spell no product name (which is every real connector), and since MAILSEAM2 it resolves the DECLARED backend itself — so the caller makes one call, not two, and cannot forget the second. Always pass `declared=connector_config.declared_backend("email")`.
- `discover_zapier_send_tool(tools, zapier_ids=…)` — the gmail-only dispatch leg; recognizes a UUID-namespaced Zapier server by pinned server-id (`workspace.connectors._zapier_server_ids`) or the `get_configuration_url` signature (R12/H-H), not just the `mcp__zapier_` prefix.
- `discover_granola_tool()` / `discover_transcript_tool()` — transcript fetch.
- `repair_backend(server_tool_ids)` — fingerprint re-pair for a reconnected server whose UUID changed (A1b); confirm-with-user before re-pinning (interactive only, R13).

Discovery is DATA-first: the declared backend + the capability manifest (`shared/data-schemas/connector_capabilities.json`) are the single source both `CONNECTORS.md` and discovery read, so the catalog/hint-map drift (N10) can't recur. Orchestrators import these helpers in Phase 2 setup. They do not pick namespaces themselves, and they never name a provider tool directly (Rule 21).

## Rule 10 — Multi-person items split, never stack

When a meeting / surface mentions N new people who could each be added separately, render N separate sub_items (`1a`, `1b`, ...), each scoped to ONE person. NEVER stack as competing actions on one item — the radio-button rule forces an artificial choice. Same for multi-org candidates.

Per M's Apr 30 ask: *"I am trying to add both people with the same first name but it does not let me select."*

## Rule 11 — REVIEW items: explicit "what does Confirm do"

Every REVIEW item's `context_tag` must follow the shape `<verb the change> + <one-sentence reason> + Confirm-to-X / Edit-to-Y / Skip-to-Z?`. Plain English. Forbidden: `last_interaction proposed:`, `(N signal, low confidence)`, raw "(acme.example.com)" parentheticals, schema field names.

When the same signal generates BOTH a person-record review AND an entity proposal (e.g., "link Quinn to Acme Co" + "add Acme Co as new org"), they MERGE into ONE item with action set `Confirm both | Confirm just person | Confirm just org | Skip both`.

## Rule 12 — Sub-item summaries visible, terse

Per M's Apr 30 ask: *"not sure why there are 6a/b/c/d/e."* Sub-item `summary` field renders as visible text next to the action row. Each summary is a terse, distinctive label ≤8 words that maps 1:1 to the corresponding numbered line in the parent's email body (when applicable).

## Rule 13 — Source thread "Open in" link inside collapsed block

Every email-shaped item with a known source thread URL must populate `original_thread.url`. The renderer adds an `↗ Open in Gmail` / `↗ Open in Granola` link at the top of the expanded `<details>` block. Per M's Apr 30 ask: *"I dont see the link to see the original thread in gmail."*

## Rule 14 — Body content rules (Upcoming Meetings)

`body_lines` is for MEETING SUBSTANCE only. Forbidden in body:
- Raw calendar URLs (`https://www.google.com/calendar/event?eid=...`) — link surfaces in post-widget Links section, never in body
- Routing metadata leaks (`Unrouted — Northstar Partners (org_003) has no active threads`)
- Verbose attendee bios with typo callouts (`Bo Sample (calendar title says 'Barrow' — likely typo)`)
- Internal entity IDs anywhere

Brief preview = lead-with point, decisions to drive, open threads, cross-references. NOT attendee CV or routing trace.

## Rule 15 — Brief .docx forwardable-clean

The brief document itself MUST NOT include:
- Calendar event URLs
- Provenance metadata footers (`Source: cr-upcoming-meetings | Fired: <ts>`)
- Internal entity IDs, file paths, routing-stage labels
- Internal asks or follow-up drafts

Brief content = MEETING SUBSTANCE ONLY. Forwardable to a third party without redaction. One exception, and it needs no redaction either: the single plain-language Sources section `prep_pipeline.assemble_prep_sections` derives from actual consumption and appends last (PREPSRC1) — its labels come from a closed vocabulary ("Email", "Calendar", "Pre-meeting notes", …), never tokens, paths, or product names. Footer states: `Forwardable: yes — contains no internal asks or drafts.`

## Rule 16 — Self-refresh after every plugin upgrade

After installing a new plugin version, the user MUST run `set up command room schedules` to refresh registered prompts. The skill compares each registered prompt against the current orchestrator file content and overwrites stale ones through the seam (`schedule_backend.plan_update`, executed by the skill; on a legacy seat the shim spells the old verb). v2.11.4+ self-refresh logic is the only path that propagates orchestrator changes to the scheduled-task DB.

Without this step, users see old formatting on the tasks the trigger didn't refresh — which has been a recurring source of confusion in M's testing.

The README + install flow must explicitly call this out as Step 5 of the install ritual.

## Rule 17 — Speed over perfection (CLAUDE.md global)

Ship a working v1 fast, iterate. Plain English. Translate plain instructions to code changes. Push back when there's a better approach. Match conviction to confidence — flag opinions as optional, save conviction for evidence-backed recommendations.

## Rule 18 — Session close writes to workspace

End of every meaningful session: append to project `SESSION_NOTES_<PROJECT>.md` and write a `HANDOFF_*.md` doc to the project's folder. Do not wait to be asked.

## Rule 19 — Data-shape consistency per item type (v2.14.1+)

Per Bo's Apr 30 testing: "Edit then send didn't open" + "this one doesn't have a resolve button." Both root-cause to the same class of bug — orchestrator builds items with INCONSISTENT shapes. The renderer raises `DataShapeError` (blocking) on:

- **Email-shaped item rule (FB-17 form, 2026-07-19):** if `metadata` contains `To` AND `Subject` with non-empty values, the item MUST include the required email action set: `send`, `draft`, `snooze 3d` (`EMAIL_REQUIRED_ACTIONS`). No item can have email metadata but offer a partial set; extra domain verbs (Waiting On chase rows) ride in the tail. History: pre-FB-17 the required set was `send` / `edit then send` / `draft` (skip was un-required in v2.14.31 — the v2.14.28 coupling-bug lesson); pre-v2.14.4 it was `send` / `edit then send` / `to drafts` / `edit then draft` / `skip`. `edit then send` is RETIRED — a deprecated alias (→ `send`) accepted only from in-flight widgets, rejected by CANONICAL_ACTIONS at render. Calendar-shaped items (Time/Duration/Location/Date keys) are exempt and use `send` / `skip`.
- **Draft requires populated content rule:** if the action set includes `draft` (or a deprecated in-flight `edit then send`), the item MUST have at least one of `To` / `Cc` / `Subject` metadata populated AND non-empty `body_lines`. Otherwise the edit surface opens with all blank fields = looks broken.

Fix is always at the orchestrator level — populate the data view consistently before render. Never disable the validator.

## Rule 20 — Action label clarity: no surprise inputs (v2.14.1+)

If two surfaces use the SAME display label, they MUST behave the same way. Per Bo's Apr 30 testing: clicking "Resolved" surprised him with a textarea on the (since-retired) Pulse chat but was a clean state-change on Commitments. Same label, different mechanic = bad UX.

v2.14.1 unified `resolved` to plain state-change everywhere (no input affordance). If a future surface needs a "with reason" variant, it gets a DIFFERENT verb (e.g., `mark in touch [reason]`), not the same verb with surprise input.

Same rule applies generally: action labels must do what their verb implies, with no surprise inputs. Brackets `[input]` in the action_id signal that the widget will expose an input on click — and that's the ONLY signal users have. Don't violate it.

## Rule 22 — Every workspace touch goes through the access layer; plugin-root discovery is deterministic, not agent-improvised (v2.14.3+; SLACK1 C-5 headless-aware; ENV1 three-stage + CR_ENV 2026-09-19; v6 Access preamble ORCH1 2026-09-20)

**v6 — the Access preamble.** The rule used to be about one thing: find the plugin. In the merged app that is half the problem, because the helpers and the customer's files are on different machines, so a resolved plugin root buys nothing on its own. The rule now carries the whole contract, and it is written ONCE — in `shared/WORKSPACE_ACCESS.md`, between the `CR ACCESS PREAMBLE v6` sentinels — and propagated into every skill file by `scripts/dev/propagate_access_preamble.py`. Five rules the model reads before it touches anything:

1. **RESOLVE, once per call.** On a merged seat, `workspace_access.py discover` renders a block for the device shell and answers with WS, RT, BRAIN and MODE; a runtime that is absent or a version that differs is a STOP, and there is no container fallback. On a legacy or local seat the four shell lines below ARE the resolve step, byte for byte what they are today.
2. **BRAIN first** — one `plan read`, when there is one.
3. **HELPERS** — one verb is one call, rendered only by `workspace_access.py plan run_helper`; the reply is one JSON envelope and `ok:false` is a stop, never a hand retry.
4. **WRITES** — only `plan write` and `plan append_jsonl`. Never an append redirect, an in-place edit, a heredoc into the workspace, or a `python3 -c` body that opens a substrate file.
5. **LEGACY / LOCAL** — the same verbs run in this shell. The verbs never change; only where the process runs does.
6. **THE SURFACE IS THE WHOLE ANSWER** — a step that could not run gets one sentence with no file, script, path, variable, shell text or mechanism in it.
7. **STAGING** (FIX3 2026-09-21, ruling R-RW-6) — a file this chat needs for ITSELF (a widget copy, a scratch render) lives in the session's own scratch, never under the workspace. Nothing under `_hq/` is created, copied or removed by a redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and removed by `plan remove`, and a removal is reported in the envelope's own words — removed, moved aside, or still there — never as done. The 2026-09-21 re-walk left three staging files under the customer's `_hq/` because a chat with no delete verb and a device shell that reaches a different filesystem had no honest way to clean up after itself. Guard G73 polices the text; `remove` supplies the verb.

The four lines under those rules are unchanged from v5 and stay unchanged, because that is what keeps a Cowork seat and a Claude Code seat behaving exactly as they do today. Guard G69 polices the other half: a `python3` block that opens a workspace path is legal only in a file the migration census still lists as not-yet-migrated, and never in one that has been migrated to `plan` forms.


Every bash call is independent — no cwd carryover, no env carryover, no shell state between tool calls — in all three environments Command Room now runs in: `Bash` in the merged cloud container, `device_bash` against the sandbox VM on the customer's machine, and the sandbox shell on a legacy Cowork seat. Session IDs are non-stable across reinstalls.

**The agent must NEVER guess at the plugin root.** It uses this exact discovery pattern at the start of every multi-step bash invocation:

```bash
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
```

… then `cd "$PLUGIN_ROOT" && python3 -c "…"` for the actual work.

Three-stage resolution (stages 1–2 SLACK1 C-5 2026-08-06; stage 2 added by ENV1 2026-09-19):

1. **`$CLAUDE_PLUGIN_ROOT` wins when set.** Claude Code (the headless VM / Slack surface, and a Code session on the customer's own machine) provides it natively; neither sandbox shape below exists there — pre-C-5 the preamble resolved to nothing on the VM, which silently killed every shared-script call (a contributor to the headless doc-render failures).
2. **The synced plugin registry — the merged environment.** In the merged claude.ai + Cowork container the plugin is SYNCED to `/root/.claude/plugins/synced/<org>_<account>/cr/`, and `$CLAUDE_PLUGIN_ROOT` is EMPTY in a scheduled fire — so without this stage a fire resolves nothing and aborts. The candidate is CONTENT-ADDRESSED by the same marker as stage 3 (`shared/scripts/chat_output_renderer.py`), on the SAME line as `head -1`, so guard G1's positional ban covers it too: a bucket that is not the core plugin can never win a first-match coin flip.
3. **The Cowork `.remote-plugins` shape — legacy.** `CLAUDE_CODE_TMPDIR` → strip the trailing `/tmp` → `<session>/mnt/.remote-plugins/plugin_*/`, CONTENT-ADDRESSED on the same marker (a core-plugin file no add-on pack carries). The old `ls -d plugin_* | head -1` first-match was a coin flip the moment a second plugin (a client add-on) was installed — land on the add-on and every `shared/scripts/*.py` path is missing, so the fire dies. The positional form is BANNED; guard G1 fails the build if it reappears, in either glob.

**Line 3 reads the environment instead of assuming it.** `shared/scripts/env_detect.py --shell` reports which of the three environments this process is in (`CR_ENV` = `legacy_cowork` / `merged_cloud` / `claude_code_local` / `unknown`) plus the resolved plugin root, the staged brain file, whether the workspace is reachable from THIS process's filesystem (`CR_LOCAL_FS`) and whether the host clock may be read as the customer's (`CR_CLOCK_TRUST`). It never raises: when it cannot tell, `CR_ENV=unknown`, and the standing rule is that readers proceed with one plain-English banner line while writers and scheduled fires refuse. Because shell state does not survive between tool calls, **every `python3 -c` subprocess must carry `CR_ENV` forward the way `CR_WORKSPACE` already is** (the CLOCK1 carrier precedent) — exporting it once at the top of a block is not enough for the next tool call. The `cd` is guarded by `[ -n "$PLUGIN_ROOT" ]` because `cd ""` SUCCEEDS in bash: unguarded, a call that resolved no plugin root would run whatever `shared/scripts/env_detect.py` happened to sit under the caller's current directory and export that copy's answer. Nothing resolved must mean `CR_ENV=unknown`, never a neighbour's answer. For the same reason line 1 reads `${CLAUDE_CODE_TMPDIR:-}`: the variable does not exist in the merged container, and a caller running under `set -u` would otherwise get a shell error on every call.

**In the merged environment `WORKSPACE` is never the substrate — line 4 nulls it.** The customer's folder is FUSE-mounted in a sandbox VM on their own machine, not in this container, so the `find` on line 4 resolves one of two things there and NEITHER is the workspace: nothing at all, or — once any file has been staged into the session — a STAGED, READ-ONLY COPY under `/mnt/user-data/uploads/<Folder>/`, a point-in-time snapshot that sits inside the pipeline's `-maxdepth 5` and that no write ever reaches. An earlier draft of this rule promised that the variable would be empty "by design" on that seat; that promise is false the moment anything is staged, and a guard clause written around an empty `WORKSPACE` stops firing exactly when it is needed. So the emptiness is ENFORCED rather than assumed: line 4 ends with `[ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""`, which clears the variable whenever the detector reports the merged container (an unset `CR_LOCAL_FS` defaults to the legacy/local answer, so nothing changes on the seats that have a real mount). On the merged seat every read and write goes through the workspace access layer (`shared/WORKSPACE_ACCESS.md`, ACCESS1), which supplies the handles. **A skill that opens a path under `/mnt/user-data/` as if it were the workspace is the improvised-path class Rule 22 exists to end** — a staged copy is input, never the customer's folder.

`CLAUDE_CODE_TMPDIR` is the only Cowork-set env var we rely on; from it the session directory derives. (The scheduled-task bootloader keeps its own loop form — it content-addresses on its orchestrator filename and counts candidates for diagnostics — with the same `$CLAUDE_PLUGIN_ROOT`-first stage; since HQRESOLVE1 its fallback discovery prunes the same two directories as the snippet below.) **Canonical `WORKSPACE` resolution (v2.14.26+, hardened by HQRESOLVE1 2026-08-29):** `WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""` — discovers the user's workspace folder dynamically by finding a mounted folder containing `_hq/`, with two hardenings over the original v2.14.26 form (attended-walk finding F-4, P0-class): (1) `_archive/` and `_demo-framework/` are PRUNED — cleanup's own stale-lock archiving used to preserve the `_hq/...` path shape under `_archive/stale-locks/`, and the demo framework carries a full fake substrate under `_demo-framework/_hq/`, so both manufactured decoy `_hq` directories that traversal order served FIRST, silently binding preamble-trusting skills to an archived or demo substrate; (2) the SHALLOWEST match wins (`awk`-counted path depth, `sort -n`) instead of first-in-traversal-order, so any other nested `_hq` look-alike loses to the real root at `mnt/<basename>/_hq`. The old `head -1`-on-raw-traversal form is BANNED; guard G43 fails the build if it reappears, and executes this snippet against a planted-decoy fixture. Replaces the pre-v2.14.26 hardcoded `WORKSPACE="$SESSION_DIR/mnt/Command Room/Command Room"` which assumed a specific folder name + nested layout that was inaccurate for most installs (the doubled-Command-Room path was a phantom level — Cowork mounts the connected folder directly, not as a subfolder of itself). The Cowork diagnostic 2026-05-06 confirmed the actual layout is `mnt/<connected-folder-basename>/_hq/`, not `mnt/<basename>/<basename>/_hq/`. **Do NOT hardcode any specific folder name** — workspace discovery works regardless of what the customer named their folder.

The PLACEHOLDER forms `cd "<plugin-root>"` and `[PLUGIN_ROOT]` are no longer allowed in any orchestrator or skill. v2.14.3 began the sweep; v2.14.15 finished it after a /simplify pass found regressions across 9+ files (workspace-manager, command-room-update-bridge, enable-command-room-schedules + 5 orchestrator references, enable-orgs-map, enable-quick-commands, meeting-notes, usage-report, shared/WORKSPACE_API.md). New skills must use the discovery pattern from day one. Run `grep -rn "<plugin-root>\\|\\[PLUGIN_ROOT\\]" skills/ shared/` before every release; non-empty result outside `CHANGELOG.md` and this file is a release blocker.

This is the third architectural enforcement in this series: validators (v2.13.0 + v2.14.1) keep the agent from improvising labels/leaks/data shapes; helpers (v2.14.2) keep the agent from improvising tool selection; v2.14.3 keeps the agent from improvising paths. Each closes a class of agent-freedom drift.

## Rule 21 — Native connector parity (v2.14.2+)

Per M's Apr 30 ask: *"when we talk gmail or granola or google drive — we need to make sure we consider microsoft/fireflies/onedrive etc... whatever is a native connector should work the same."*

Every native connector is addressable through abstracted helpers in `shared/scripts/tool_discovery.py`. Same code paths work for both Google + Microsoft / alt stacks:

| Capability | Google stack | Microsoft / alt stack | Superhuman | Helper |
|---|---|---|---|---|
| Mail send | Gmail (`send_message`) | Outlook (Graph `send_message`) | native `send_draft` | `discover_mail_send_tool(tools, declared=connector_config.declared_backend("email"))` |
| Mail reply (threaded) | Gmail (`send_draft` + threadId) | Outlook (`reply_to_email`) | native threaded reply | `discover_mail_reply_tool(tools, declared=connector_config.declared_backend("email"))` |
| Mail draft | Gmail (`create_draft`) | Outlook (`create_draft`) | `create_or_update_draft` | `discover_mail_draft_tool(tools, declared=connector_config.declared_backend("email"))` |
| Mail search | Gmail (`search_threads`) | Outlook (`outlook_email_search`) | `query_email_and_calendar` | `discover_mail_search_tool(tools, declared=connector_config.declared_backend("email"))` |
| Mail thread fetch | Gmail (`get_thread`) | Outlook (`get_conversation`) | `get_thread` | `discover_mail_thread_fetch_tool(tools, declared=connector_config.declared_backend("email"))` |

The five mail rows take `declared=` for a reason the other rows do not need: a workspace can have **two** mail connectors, and without a declaration the seam resolves whichever platform the hint map lists first. On the wrong one, every client-scoped read returns zero — and zero mail is not an error, it is a week that looks quiet (MAILSEAM2). Pass the declared row on every call; the seam refuses rather than substituting another product's inbox, and refuses a SEND outright.
| Calendar | Google Calendar (`google_calendar_*`) | Outlook Calendar (Graph) | fronts calendar too | `discover_calendar_tool()` (cross-stack) |
| Transcript | Granola | Fireflies | — | `discover_transcript_tool()` |
| File storage | Google Drive | OneDrive / SharePoint (M365 `sharepoint_search`) | — | `discover_drive_tool()` (+ `prefer_platform=` from `infer_workspace_drive_platform()`, v5.11.1) |

Discovery helpers return `DiscoveryResult.platform` indicating which stack matched (`"gmail"` / `"superhuman"` / `"outlook"` / `"granola"` / `"fireflies"` / `"google_drive"` / `"onedrive"` / `"m365_sharepoint"` / `"google_calendar"` / `"outlook_calendar"`).

**Mail draft — reply drafts are NEVER patched in place (DRAFTTHREAD1, 2026-07-29).** The draft-update operation on the primary mail backend carries no reply-to parameter, so patching a reply draft rebuilds the message WITHOUT its `In-Reply-To` / `References` headers and the backend silently reassigns it to a new thread — the tool returns the draft id as if it succeeded, and the `Re:` subject hides the detachment in the drafts list. Any content change to a reply draft is therefore a **fresh create carrying the reply-to reference**, followed by `shared/scripts/draft_threading.py::assert_reply_threaded(created_draft, conversation_thread_id)` — passing the reply-to id is not proof the threading survived, and the assertion is what turns a silent orphan into a loud error. The backend exposes no delete-draft operation, so a re-create leaves the superseded draft behind: NAME it to the user for hand deletion, never leave two drafts silently. (Do not re-add the patch path as an optimisation — this paragraph is why it was removed. Full mechanism: `EMAIL_DRAFT_PROTOCOL.md` §3d. Upstream connector asks — reply-to on the update operation + a delete-draft tool — are filed with MAILTRUST1's short-read ask.)

**An operation is not always a tool name.** Search SCOPES — `in_sent`, `unread`, `in_inbox`, `from_me`, `message_id_lookup` — are INTENTS that `connector_adapters/mail.py` compiles into a provider query; no connector ships a tool called `in_sent`. `discover_for_category` recognizes them (`connector_adapters.mail.is_search_intent`) and resolves them to the backend's SEARCH tool. Matching an intent against tool ids returns nothing on every provider, Gmail included, and a caller that reads that as "not connected" silently hands the read back to the model.

**A real connector spells no product name.** Native Gmail is `mcp__f12657a1__search_threads`, Superhuman `mcp__ec5e0bd5__create_or_update_draft` — the UUID carries the identity, not the tool id. The mail helpers therefore fall back from the product-name hints to the capability manifest's FINGERPRINTS (the same data `repair_backend` re-pairs on), so a real workspace resolves at all.

Orchestrators that need stack-specific adapter logic branch on `result.platform`. Orchestrators that don't care just use `result.tool_id`. **Hard-coded references to specific stacks in orchestrator prose are forbidden** — same enforcement model as `CANONICAL_ACTIONS` for action labels.

Zapier remains EXCLUDED from all native helpers (Calendar HARD SCOPE Rule 8 still holds; Zapier-mail is a separate path via `discover_zapier_send_tool` per `EMAIL_DRAFT_PROTOCOL.md` §3c). Native helpers are for native connectors only.

## Rule 23 — Trailing finish-cluster on review-shaped items (v2.14.5+) — FOSSIL

**RETIRED IN PLACE (LIFECYCLE1, 2026-08-02).** The Pulse chat this rule governed is eliminated, so nothing renders these item shapes any more. The rule stays written down because persisted widgets from before the retirement still dispatch these verbs, and a reader who meets one needs to know what it meant. **Do NOT apply this rule to a new surface** — the review families it describes were consolidated by MLK1 and v2.14.38 well before the retirement, and `verb_taxonomy` plus `CANONICAL_ACTIONS` are the live authority on any verb you are about to render.

Per M's preview-cycle feedback: the type-specific actions ARE intentionally different across Pulse item types (person uses `Resolved`, project uses `Mark paused`, REVIEW uses `Confirm` / `Edit`, dormant transition uses `Active` / `Keep paused` / `Archive`, entity proposal uses `Confirm [type]` / `Edit [type]`) — flattening them loses information.

The fix is structural consistency at the END of each item's action set, not at the type-specific verbs. **Every Pulse item — main and review-shaped — terminates with the same finish-cluster: `snooze [duration]` followed by `skip`.**

| Item type | Type-specific actions | Finish-cluster |
|---|---|---|
| Person dormancy / pattern-break | `investigate`, `draft re-engagement`, `schedule catchup [when]`, `resolved` | `snooze [duration]`, `skip` |
| Stale active project | `prep deep work`, `investigate`, `mark paused`, `status check` | `snooze [duration]`, `skip` |
| Pending people-record review (a/b/c) | `confirm`, `edit [change]` | `snooze [duration]`, `skip` |
| Dormant transition proposal (d1/d2) | `active`, `keep paused`, `archive` | `snooze [duration]`, `skip` |
| Entity proposal (e1/e2) | `confirm [type]`, `edit [type]` | `snooze [duration]`, `skip` |

**Semantics of the finish-cluster:**
- `snooze [duration]` — "I want to come back to this later, on my schedule." The user picks a duration (`7d`, `14d`, `30d`, etc.); the proposal disappears until then.
- `skip` — "Not now, surface again tomorrow." 24h dismissal. Universal escape hatch on every item.

The finish-cluster is enforced via the canonical-action validator (every action verb must be in `CANONICAL_ACTIONS`); the orchestrator-level audit in `tests/run_audit_v2_13_0.py` exercises each Pulse item shape with the cluster appended. Adding new Pulse item types in the future requires the same cluster — no exceptions.

## Rule 25 — Path output uses runtime-resolved $WORKSPACE, never docstring examples (v3.5.3+)

When emitting a file path to the user — in chat, in `review_url` frontmatter, in clickable links, in "I saved your brief to ..." sentences — the absolute path MUST come from the runtime-resolved `$WORKSPACE` value (Rule 22). NEVER reuse a literal path that appears in a reference doc, CHANGELOG, docstring, or example block.

The failure mode this rule closes: literal absolute paths in doc examples (one author's local workspace path, e.g. a Drive-Desktop folder) were leaking back into chat output for users whose actual workspace lived somewhere completely different. Users would click the path, hit "folder not found," and lose trust. Verified live with multiple beta users May 2026 — root cause was the agent improvising path output from doc examples rather than from the resolved workspace.

**Rule:**
- Compute the path at write time from `$WORKSPACE` + the relative file location.
- If `$WORKSPACE` resolution failed (empty/null), do NOT fabricate. Omit the path or surface a plain-English "couldn't resolve your workspace folder" message.
- Never copy a path from `references/`, `CHANGELOG.md`, a docstring, or any other source-doc example.
- Doc examples now use clearly-fake placeholders (`<workspace-root>`, `$WORKSPACE`, `/path/to/workspace/`) that won't resolve as real paths if the agent slips and uses them verbatim.

**Test guard:** `tests/run_no_hardcoded_drive_test.py` greps skill prompts + references for forbidden literal paths (any specific author's machine path that would not resolve on a different user's machine). Non-empty result outside `CHANGELOG.md` and the test file itself is a release blocker.

**Runtime guard (v3.6.0+):** `validate_chat_output()` in `shared/scripts/chat_output_renderer.py` additionally runs `_scan_for_path_leaks()` over every rendered widget / chat post before it's allowed to ship. The runtime scanner extracts every absolute filesystem path mentioned in chat output (cross-platform: `/Users/...`, `/home/...`, `/sessions/...`, `~/...`, `C:\...`, `C:/...`, `/c/Users/...`) and compares its prefix against the runtime-resolved `$WORKSPACE` (per Rule 22), the installed plugin root, and the Cowork session `mnt/` directory. Any path outside those trusted prefixes raises `LeakDetectedError` with kind `path-leak (not under workspace; click would 404)`. Closes the gap left by the static grep: if a future skill author writes a new doc example with a hardcoded path, or the agent improvises a path from somewhere not covered by the grep, the runtime check catches the leak before it reaches the user. Includes paths inside `computer:///` href values — Rule 3 clickable artifacts must resolve too.

## Rule 24 — CRU layer is silent (v2.14.6+)

The Cross-Reference and Update layer (`shared/scripts/cru_match.py`) writes `commitment_resolved` / `commitment_updated` / `commitment_review_proposed` events behind the scenes when an outbound `send` (Path 1, apply-choices) or a meeting transcript (Path 3, past-meetings) provides high-confidence evidence that an open commitment was fulfilled, deferred, or layered with a new ask.

**These resolutions are NEVER narrated in chat.** No "Auto-resolved 2 commitments." No "Closed Mira's pricing deck commitment." No "1 commitment_resolved event written." The user sees the effect on the next Commitments fire — items that were auto-resolved simply don't appear in the OWED TO YOU / YOU OWE columns.

The reasoning: chat is for in-the-moment-actionable surfaces. CRU resolutions are durable workspace facts; they belong on the data layer (events.jsonl) and surface on the next scheduled-task fire alongside other open work. Narrating them at apply-time leaks internal mechanics (Rule 4) and clutters the user's view of the current action.

**Threshold model (v2.14.7 — full coverage):**
- Score ≥ 0.55: auto-resolve immediately (or `commitment_updated` if schedule-shift signal present). Silent.
- Score 0.30 - 0.55: write `commitment_review_proposed` event. The confirm queue surfaces these as one-click `confirm` / `skip` items — `brain_proposals._adapt_commitment_reviews` projects them onto the staff meeting and the `needs your call` queue (before LIFECYCLE1 the retired Pulse chat's Phase 4g did it, sub-namespace `r1/r2/...`). User-facing question names the commitment + describes the evidence; user confirms it as fulfillment OR rejects it.
- Score < 0.30: no action.

**Three CRU paths active:**
- **Path 1 — apply-choices in-Cowork sends.** Catches sends made through Cowork.
- **Path 2 — Commitments orchestrator pre-render scan.** Bulk mail search for outbound sends since last fire. Catches sends made directly from native mail clients.
- **Path 3 — Past Meetings transcript cross-reference.** Catches resolutions / schedule shifts / new asks mentioned in meeting transcripts.

The leak scanner (`chat_output_renderer.py` `_LEAK_PATTERNS`) catches every CRU event-type name appearing in chat — `commitment_resolved`, `commitment_updated`, `commitment_review_proposed`, `commitment_review_dismissed` are all blocked from leaking by the same Rule 4 enforcement that catches `pack_run` and other internal mechanics.

## Rule 26 — No real customer or partner names in plugin source (v3.6.1+)

Plugin source is granted to beta operators (private repo access today, broader collaboration later). Examples, fixtures, CHANGELOG entries, docstrings, comments, and skill references MUST NOT contain real beta-customer or partner names, real email domains, or real org names. Use these placeholders: `Sam Sample`, `Bo Sample`, `Rio Sample`, `Rio Lange`, `Acme Co`, `Northstar Partners`, `Summit Company`, and `@example.com` / `*.example.com` domains. This rule is the shipped statement of the roster; the full policy behind it is a development-only document that does not fan out to client repos (EXEMPTFENCE, 2026-07-26 — a name-scanner-exempt file must contain the patterns it forbids, so it is the last file that should be distributed).

The failure mode this rule closes: the v3.5.2 sanitization pass claimed "25 files, 69 lines swapped — every real name replaced with placeholder" but a 2026-05-18 IT security audit confirmed the sweep was incomplete — ~68 residual hits remained across 16 non-CHANGELOG files. The same agent-improvisation pattern that drives Rule 25 path leaks drives name leaks: doc examples + memorialized-failure narratives in reference files become the substrate the agent samples from when writing chat output.

**Rule:**
- Replace real names at write time. Don't ship a CHANGELOG entry, fixture, or example that names a real beta customer or partner.
- `Summit Company` is the canonical fictional org for the "operator's main client" placeholder pairing with `Sam Sample`. `Northstar Partners` is the canonical fictional partner org pairing with `Bo Sample`. `Acme Co` is the canonical fictional prospect-org for entity-proposal examples.
- `matthew@chaletteholdings.com` is exempt — it's the project's intentional public support address used by the `report-bug` skill.
- CHANGELOG.md is in scope as of v3.6.3. Pre-v3.6.3 the audit-trail-preservation argument exempted it, but the historical content was itself a significant leak surface and was sanitized to placeholder names + `@example.com` domains. The narrative of what each release closed is preserved; only the specific names and domains are placeholderized.

**Test guard:** `tests/run_no_real_customer_names_test.py` runs two checks against skill prompts, references, shared scripts, tests, fixtures, AND `CHANGELOG.md` at the repo root:

1. **Named-pattern layer.** Greps for known historical leak names (limited usefulness; only catches what's already in the pattern list).
2. **Structural email-domain layer.** Extracts every "user@host" literal and rejects any domain not on the allowlist (`example.com`, `*.example.com`, `chaletteholdings.com`, `mail.gmail.com`, `outlook.com`, and a handful of platform/connector domains documented in the test file). The durable defense: it does NOT need to know the name of a real customer to catch the leak — any new real email a future skill author writes fails this check at PR / push time.

Non-empty result outside the test file itself is a release blocker. The chalette plugin's `ship-cr-plugin` skill runs this test as part of the pre-push gate.

**Why a structural guard rather than reviewer discipline:** the v3.5.2 attempt was a one-shot manual sweep; it left ~68 residual hits and shipped. The named-pattern test (v3.6.2) catches known leaks but not novel ones — adding a real name to the pattern list also leaks the name. The structural email-domain layer (v3.6.3) catches future leaks at PR / pre-push time without needing to name any real customer; same enforcement model as Rule 25's `run_no_hardcoded_drive_test.py`.

---

## Rule 27 — No `.md` deliverables in user-facing output paths (v3.7.0+)

Polished outputs the user opens to read MUST be saved as `.docx` (or `.pptx` / `.xlsx` as appropriate). Word and Pages render `.md` badly; saved `.md` deliverables cause readability complaints from customers who open them outside a markdown viewer.

`.md` remains correct for files Claude reads as context/memory — briefings, insights, intel, view files (`TIMELINE.md`, `DECISION_LOG.md`, `MASTER_TRACKER.md`, `PEOPLE.md`, `RELATIONSHIPS.md`), session notes, `PROJECT_CONTEXT.md`, `PROJECT_BRAIN.md`, `BUSINESS_CONTEXT.md`, `BRAND_VOICE.md`, voice corpus, transcripts. Those are working memory, not deliverables; they're surfaced in chat as rendered markdown and rarely opened from disk.

The failure mode this rule closes: pre-v3.7.0, six skills wrote polished reports as `.md` (automation-scanner audit reports, dormant-customer-scan reports, cleanup reports, operator-report monthly recaps, follow-up-ritual packs, memo-writer's redundant `.md` source-for-review). Customers opening these in Word saw raw markdown syntax instead of formatted prose. Same agent-improvisation class as Rule 25 / Rule 26 — the convention "save it as `.md` for review" leaked from doc examples + reference files into skill spec.

**Rule:**
- Deliverable directories that MUST NOT contain `.md` files in their write paths: `deliverables/`, `audit-reports/`, `operator-reports/`, `dormant/`, `one-pagers/`, `memos/`, `board-packs/`, `email_drafts/`, `speeches/`, `summaries/`.
- Filename prefixes that mark a deliverable regardless of directory: `FollowUp_*`, `OnePager_*`, `Memo_*`, `StressTest_*`, `Call_Prep_*`, `Past_Meeting_*`, `BoardPack_*`, `ContractReview_*`, `DecisionMemo_*`, `DORMANT_SCAN_*` — these MUST be `.docx`.
- Email drafts in particular: do NOT save a file at all. Push to Gmail Drafts via Zapier (already wired since v3.2.2). The pre-v3.7.0 `[Project]/deliverables/email_drafts/*.md` pattern is vestigial and was retired in this release.
- Allowed `.md` paths (context/memory, not deliverables): `_hq/briefings/*.md`, `_hq/intel/*.md`, `_hq/insights/*.md`, `_hq/views/*.md`, `_hq/voice/*.md`, `_hq/meetings/*_transcript.md`, project root files (`PROJECT_CONTEXT.md`, `PROJECT_BRAIN.md`, `SESSION_NOTES_*.md`, `MASTER_TRACKER.md`, `PEOPLE.md`, `DECISION_LOG.md`, etc.).

**Test guard:** `tests/run_no_md_deliverables_test.py` scans `skills/`, `shared/`, `references/` for forbidden directory and filename patterns. Non-empty result outside the test file itself, `CHANGELOG.md`, `MD_DELIVERABLE_POLICY.md`, and this `CONTRACT.md` is a release blocker. The chalette plugin's `ship-cr-plugin` runs this test as part of the pre-push gate.

**Why a structural guard rather than reviewer discipline:** mirrors Rule 25 / Rule 26 model. The convention is easy to drift from — a future skill author writes "save to deliverables/foo.md" without thinking about render quality. The static guard catches it at push time before customers open it in Word.

See `references/MD_DELIVERABLE_POLICY.md` for the full deliverable-vs-context taxonomy.

---

## Rule 28 — Plain-English customer surfaces; no plumbing-instruction shapes in announce/auto_apply (v3.14.4+)

The non-technical-customer principle: Command Room customers are CEOs and operators who don't think in JSON / schema / migration / `taskId` / `events.jsonl` vocabulary, and they should never be asked to type a phrase to make the system do its own plumbing work.

**Banned in customer-facing surfaces** (manifest top-level `headline`; manifest `prompt_template` / `notice_template` fields; chat-quoted blocks in SKILL.md; any string the customer will read):

- **Schema / file vocabulary:** `events.jsonl`, `entities.json`, `aliases.json`, `workspace_config`, `schema`, `enum`, `MCP`, `mcp__`, action-type literals (`instruct_user`, `auto_apply`, `announce_only`), filename-shaped strings like `orchestrator-*.md`, internal taskId variants (`cr-*-pulse`, `cr-*-nudge`).
- **Plumbing-instruction shapes** (banned in `announce_only` and `auto_apply` surfaces, allowed in `instruct_user`): `run recovery`, `run [wrapper] backfill`, `run [the] migration`, `apply [the] migration`, `re-fire`, `re-register your tasks/schedules`, `set up command room schedules` (when surfaced as an asking-shape rather than as a recovery instruction), `repair my activity log`.

**The auto_apply default:** if the system can resolve a question without customer input, it MUST be `action: auto_apply` (the action does the thing; the customer sees a plain-English notice about what was done). `instruct_user` is reserved for items where the customer's choice is genuinely required (assistant name, workspace shape, opt-in/out decisions). Default to `auto_apply`; reach for `instruct_user` only when you've ruled it out.

**The customer-voice contract:** notice templates use past-tense, customer-visible-outcome framing — *"I quietly set aside 12 incomplete entries from old data"* — not *"a quarantine pass moved 12 malformed events.jsonl lines to a sidecar."* Same information, different audience.

**Enforcement:** `tests/run_no_jargon_in_customer_surfaces_test.py` scans every `shared/releases/v*.json` manifest's top-level `headline` plus its items' `prompt_template`, `notice_template` and `fallback_prompt_template` fields for the banned patterns. The `.githooks/pre-commit` hook invokes this script, and the battery runs it at guard tier. (Step 6 of `ship-cr-plugin` runs a different, smaller guard set that does not include this one — the hook and the battery are what block the ship.) New violations block the ship.

The `headline` carries no `action`, so both rule sets apply to it unconditionally — including the plumbing-instruction shapes that an `instruct_user` item may legitimately use. A headline summarizes what changed; the instruction to type something belongs in the item prompt. Enforcing this cost zero rewrites: all 88 shipped manifests already read that way, including the six whose `instruct_user` items say "set up command room schedules" under an outcome-shaped headline.

**Why a structural guard rather than reviewer discipline:** mirrors Rule 25 / Rule 26 / Rule 27 model. Customer-friendly prose is easy to drift from — a future skill author writes the prompt the way they'd describe the fix to another developer ("type `run X backfill` to apply the migration"), forgetting the customer reads the same string. The static guard catches it at push time.

See `references/RELEASE_MANIFEST.md` "Action types" and "Action contract" for the full auto_apply spec.

---

## Enforcement chain (v2.13.0+)

Every chat post — orchestrator widget, post-widget Links section, apply-time response — runs through this chain. Failure at ANY step aborts the post and surfaces plain English to the user.

1. **Renderer pre-flight (bash gate)** — `python3 -c "...; from widget_transport import render_and_persist; print('OK')"`. Must print exactly `OK`.
2. **Canonical-action validator** — the transport's internal `render_chat_output_widget(data)` raises `ValueError` if any item has an action not in `CANONICAL_ACTIONS` (or specific-org variant).
3. **Required-fields validator** — every item has `n`. Every email-shaped item with original_thread has `url`. Every brief has `artifact_link.url`.
4. **Leak scanner + wrapper-contract blocking gates** — `validate_chat_output(html)` raises `ValueError` on any forbidden pattern; `validate_rendered_widget(html)` raises `WrapperContractError` on a dropped input wrapper. Both run INSIDE `widget_transport.render_and_persist` (EW2+T). ABORT before posting.
5. **Post via `mcp__visualize__show_widget`, passing `transport["html"]` (the persisted page's validated bytes, verbatim) as `widget_code`** — only after the transport call returns clean. Never hand-compose the HTML, never post-process it, and never relay an unbounded set in one page — paginate (`page=N`) for unbounded views (Bug #67; `shared/CHAT_ACTION_WIDGET.md` § Transport). **Load the widget tool before anything else** (`shared/CHAT_ACTION_WIDGET.md` § Finding the widget tool first): on the merged and half-merged seats it is deferred, and a listed deferred tool counts as present; ToolSearch loads it, its setup tool runs first, then the relay.
6. **Post-widget Links section** — runs through the same leak scanner. ABORT on leak.
7. **Apply-time response** (apply-choices) — runs the same chain. Same enforcement, no special path.

If ANY gate fails, surface plain English to the user. Never silently degrade. Never improvise markdown. Never paraphrase.

---

## Appendix — Enforcement map (SPEC CON1)

Honest classification of every rule above: **ENFORCED** = a test or a runtime validator binds it (a violation fails a build or raises at render); **GUIDANCE** = prose the model is asked to honor with no structural enforcement (a soft hint, not a hard floor). Re-verified 2026-06-21 against `tests/` + the renderer validators. Zero rules unclassified.

| Rule | Subject | Status | Enforced by / honesty note |
|---|---|---|---|
| 1 | Widget is the only action surface | ENFORCED | `chat_output_renderer.render_chat_output_widget` + `chat_output_validator` (renderer-validator suite) |
| 2 | Apply-time drafts return a widget | ENFORCED | apply-choices routes through the same render chain (Rule 28 §7) |
| 3 | Documents clickable in Cowork | GUIDANCE | The `doc_headline_link` helper exists, but no test asserts skills *call* it vs hand-rolling a path. Soft convention. |
| 4 | No technical language post-widget | ENFORCED | leak scanner (`validate_chat_output`) + `run_customer_facing_voice_test` + `run_no_*` guards |
| 5 | Action labels: canonical set | ENFORCED | `render_chat_output_widget` raises `ValueError` on any non-`CANONICAL_ACTIONS` verb |
| 6 | Plain-English clarity on every action | GUIDANCE | Label *legibility* is judgment; the canonical-set membership (Rule 5) is the enforced half. |
| 7 | Free-text NL time inputs | GUIDANCE | Convention; no validator. |
| 8 | Calendar HARD SCOPE: native only | GUIDANCE | `EMAIL_DRAFT_PROTOCOL.md` §3c states it; tool_discovery steers it, but no test blocks a Zapier calendar read. |
| 9 | Tool discovery centralized | ENFORCED | `tool_discovery.discover_*` helpers + `run_ingest_substrate_sync_test` exercise the path |
| 10 | Multi-person items split | ENFORCED | renderer `DataShapeError` on stacked person entities (Rule 19 validator) |
| 11 | REVIEW items: explicit confirm | GUIDANCE | Convention; no validator. |
| 12 | Sub-item summaries terse | GUIDANCE | Terseness is unmeasurable structurally — DEMOTED to guidance (SPEC CON1). |
| 13 | Source-thread "Open in" link | ENFORCED | required-fields validator: every email-shaped item with `original_thread` must carry `url` (Rule 28 §3) |
| 14 | Body content rules (Upcoming Meetings) | GUIDANCE | Surface-specific convention. |
| 15 | Brief .docx forwardable-clean | ENFORCED | `docx_leak_scanner` (`run_docx_leak_scanner_test`) + the `make_brief` save gate |
| 16 | Self-refresh after upgrade | GUIDANCE | `command-room-update-bridge` performs it; no test asserts every skill participates. |
| 17 | Speed over perfection | GUIDANCE | Philosophy (CLAUDE.md global), not a structural rule. |
| 18 | Session close writes to workspace | GUIDANCE | Convention; weakly testable — DEMOTED to guidance. |
| 19 | Data-shape consistency per item | ENFORCED | renderer `DataShapeError` (`run_*` renderer suite) |
| 20 | No surprise inputs behind labels | GUIDANCE | Input-bearing action set is documented in `CHAT_ACTION_WIDGET.md`; the canonical-action validator (Rule 5) covers the verb set, but placeholder/input pairing is convention. |
| 21 | Native connector parity | ENFORCED | `tool_discovery` + the `discover_*` helper tests |
| 22 | Plugin-root discovery deterministic | GUIDANCE | The bash preamble is a copy-paste convention; no test asserts every orchestrator uses it. |
| 23 | Trailing finish-cluster (FOSSIL) | GUIDANCE | Surface-specific convention for a retired surface (LIFECYCLE1); never applied to a new one. |
| 24 | CRU layer is silent | GUIDANCE | Convention; the reconcile audit event (Bug #98-v3) is the closest structural backstop. |
| 25 | Path output uses runtime `$WORKSPACE` | ENFORCED | `run_no_hardcoded_drive` guard + leak scanner |
| 26 | No real customer/partner names | ENFORCED | `run_no_real_customer_names_test` (named-pattern + structural email-domain allowlist) |
| 27 | No `.md` deliverables | ENFORCED | `run_no_md_deliverables_test` |
| 28 | Plain-English customer surfaces | ENFORCED | the 6-gate render chain + `run_customer_facing_voice_test` |
| 31 | The activity log is append-only | ENFORCED | `run_guard_g53_ledger_edit_prose_test` (the rule is carried by every SKILL.md that touches `_hq/data`, and no skill prose may instruct a direct file edit of the ledger) |

**Voice calibration coverage** (VOICE_CALIBRATION.md, not a CONTRACT rule but classified here for completeness): ENFORCED by `run_voice_block_coverage_test` (SPEC CON1) — every named composer carries the `voice_block_last_refreshed` frontmatter + either a `## Voice Block` section or the shared-register + voice-tell-gate path.

The GUIDANCE rows are honest: they read as law but bind nothing structural. They stay as guidance deliberately — most are judgment calls (terseness, label legibility) or copy-paste conventions where a static guard would be brittle. If a GUIDANCE rule starts causing real misses, promote it to a guard then.

## Rule 29 — Same-commit sediment sweep on model changes (Phase 4 G10, 2026-07-02)

When a release changes a skill's core model (write path, render path, draft
lifecycle, schedule shape), the SAME COMMIT must sweep that skill's Writer
Contract, Gotchas, What-It-Doesn't-Do, and output templates for sentences the
change supersedes — and **DELETE the superseded sentence, never annotate it**.
Both 2026-07-01 audits independently identified stale-sediment-next-to-its-
replacement as the #1 root cause of customer-facing contradictions (~a third
of all findings). Incident narratives that justify a rule move to
`references/HISTORY.md` with a one-line citation left in place; rules stay,
stories archive, superseded facts die.

Reviewer checklist form: "does this diff change behavior? → grep the skill for
the OLD behavior's vocabulary before approving."

## Rule 30 — Email composition is email-writer's monopoly (SPEC EW1, 2026-07-13)

Any turn that produces recipient-bound email text — a skill fire, a sub-step of
another skill's work, or a freelance mid-task turn — MUST chain email-writer:
read its SKILL.md and follow `shared/EMAIL_DRAFT_PROTOCOL.md` end to end. No
skill and no freelance path composes email text directly via a mail connector
tool. thread-resurrection's "revival draft via chained email-writer" is the
established pattern; this rule generalizes it to every draft-producing turn.

Why: the mid-turn bypass (Bug #104 — see references/HISTORY.md). Skills route
on user messages only, so an email that arises as a sub-step of a bigger task
never hits the router — and a turn that skips email-writer skips the customer's
voice block, length target, two-pass critique, and voice-tell detector all at
once. For turns where no skill fired at all, the binding is the email-delegation
rule in the workspace CLAUDE.md (written by `references/claude-md-template.md`
at onboarding; back-filled to existing installs by the update bridge's
`claude_md_email_rule_v1` migration) — the one surface every session loads.

Enforcement status (honest): GUIDANCE at runtime — a standing instruction, not
a mechanical gate (same-turn hooks are dead in the runtime — the SPEC GATE2
§2e finding; enforcement moved to detection, see skills/check-deliverables).
Structural presence of the rule text across all four surfaces is test-enforced
(`tests/run_email_delegation_rule_test.py`); check-deliverables remains the
on-demand detection story.

## Rule 31 — The activity log is append-only; it is never rewritten by hand (LEDGERFENCE1, 2026-09-07)

`_hq/data/events.jsonl` and its yearly shards are the workspace's record of
what happened. Nothing — no skill, no scheduled fire, no freelance turn, no
chat cleaning up after itself — edits that file. The only writes are appends
through the writers (`event_gate.append_event` / `atomic_write.
atomic_append_jsonl`, which stamp `seq` inside `writer_lock.
events_writer_lock`).

**Never, by any path:**

- never edit, truncate, reorder or delete lines from `events.jsonl` or any
  `events-<year>.jsonl` shard;
- back the file up and write a corrected copy over it;
- restore the file from a backup, a sync conflict copy, or a `_recovery` folder;
- run a shell redirect, `sed -i`, `rm`, `mv`, `cp`, `open(..., "w")`,
  `write_text` or any other direct write at those paths;
- instruct anybody else — a user, another skill, a script you are about to
  write — to do any of the above.

**Instead:**

- A duplicate or malformed line is QUARANTINED through the cleanup skill's
  existing path (`recover_corruption.py` quarantines the bad lines to
  `_hq/.system/quarantine/` and appends a `corruption_recovery` event;
  `seq_health.py --mark` marks a duplicate entry number additively). Never
  deleted.
- Correcting your OWN writes means appending a reversal through
  `brain_undo.undo_batch` with the batch ref the run advertised — a receipt
  and a real `undo`, not a file edit. `undo` after a re-run has exactly one
  meaning: reverse that run's own batch, or say "nothing to reverse". It is
  never an improvised drop, an invented supersede, or a hand-edited notes
  file (the 2026-09-07 attended test, B1.2).
- If you believe the file itself must change, STOP and say so in plain words.
  Do not do it, and do not offer to.

Why: the ledger is append-only so that every count, every receipt and every
`undo` can be re-derived from it. A hand-edit is undetectable to the readers
and irreversible to the customer — on 2026-09-07 a chat tidying up its own
re-run deleted twelve lines and left a hole at 15555 that nothing can refill.
`seq_health.detect_and_mark_gaps` now reports such a hole the way the
duplicate detector reports a collision. A stretch is called something other
than a removal only on POSITIVE evidence — the numbering visibly restarting,
or a recorded repair that NAMES the numbers it set aside — never on the
absence of evidence, because a detector that explains away anything it cannot
account for is not a detector. Every stretch reaches the maintenance note in
the words that fit it: a restart says nothing is missing, a repair says the
entries are saved, a removal says what it is and that nothing can undo it.
This rule is what makes the removal never appear in the first place.

Enforcement: `tests/run_guard_g53_ledger_edit_prose_test.py` (guard tier) —
every `skills/*/SKILL.md` that touches the ledger (names `_hq/data`,
`events.jsonl`, or a helper that reaches one of them — `events_io`,
`event_gate`, `append_event`, `atomic_append_jsonl`, `brain_undo`,
`event_seq`, `seq_health`; 60 at the pin) carries this rule's MANDATORY
block, scope
pinned by membership so a skill cannot leave it quietly, and no skill prose
anywhere may instruct or permit a direct file edit of the ledger.


## Rule 32 — Machine acts are the machine's (ATTRIB2, M's ruling 2026-09-07)

A reversal, a close or a drop the product performs **on its own judgment** — a
scheduled fire, a rail, a background pass, or a chat deciding mid-run that its
own earlier write was wrong — is stamped with the MACHINE actor, never the
customer's person id. Concretely, at any such call site:

- `commitment_state.reopen_commitment(..., actor=event_types.MACHINE)`
- `commitment_state.close_commitment(..., actor=event_types.MACHINE)`
- `brain_undo.undo_batch(..., actor=event_types.MACHINE)`

You do not have to remember: the writers resolve the actor through
`event_types.resolve_actor`, and an act written under a background
`source_skill` (`event_types.MACHINE_SOURCE_SKILLS` — the reconcilers, the
maintenance run, the sweeps, the drains, the capture passes) takes the rail's
own name whatever the call site passed. Pass `actor=event_types.MACHINE`
anyway when you KNOW the product decided, because your surface may not be one
of those. A person's typed `undo`, tap or explicit `mark done` keeps
`undone_by` / `resolved_by` = their person id, exactly as before.

**And the customer's own gesture is ALWAYS theirs.** Three surfaces run both
ways — the backlog-sweep digest, `cleanup`, `meeting-notes` — so a background
`source_skill` is not proof the product decided. Two fences, both required:

- the background list names a surface's **unattended legs** by their full
  spelling (`commitment-backlog-sweep:review-amnesty`), never the bare
  surface, and the `skill:leg` prefix rule runs one way only — listing a leg
  never drags its chat in;
- **pass `user_confirmed=True` from every dispatch a person just made.**
  `close_commitment` has always honoured it; `reopen_commitment` and
  `undo_batch` now do too, and they need it more because they REWRITE the
  actor. It is honoured only when the actor is a real person id, so it can
  never launder a fire's own reversal into the customer's.

And no customer surface says "you" about an act the customer did not make:
the morning brief's CHANGED feed, End of Day's "you let go" and the weekly
wrap's "Decided for you" all ask `event_types.is_customer_act(event)` first,
and none of them reports an act whose reversal is already on the ledger
(`closure_index.reversed_act_positions`).

Why: the v5.29.0 attended test, 2026-09-07. The maintenance fire read its own
sent-mail closes, judged them wrong and reopened both — the right outcome —
and the reversals went down as `reopened_by: "person_001"` (seqs 15503-15506).
The brief then told M he had reversed them, End of Day counted the undo chat's
own drop as "1 thing you let go", and the wrap offered him an undo for a
promotion that had already been put back. A product that signs the customer's
name to its own acts cannot be audited by the customer.

Enforcement: `tests/run_attrib2_test.py` (writer fence + the three surfaces,
each proven by removal).
## Rule 34 — An automatic exit ACTS with a receipt and an undo; it never offers (EXIT1, M's ruling 2026-09-07)

When the record already shows a promise was kept — a message that went to the
person it was owed to, their reply, a meeting with them that happened, an
agreement signed, an invoice paid, or the owner's own word in their own turn
— the item CLOSES, wherever in the product that fact is noticed. It closes
with one plain line naming what happened and the word `undo` in it, and the
close is stamped with the rail that made it, never with the customer.

**No surface offers to close what it has proved.** Not "say the word and I'll
close them", not "want me to close these four?", not a checkbox. M's ruling
of 2026-09-07, made while reading four mail-proved items that `pull up` had
listed and offered to close: a fact-proved kept promise closes on its own,
and being asked about it is the defect. The same applies to the silence
rails: an item nobody has touched in six weeks rests itself and an item
nobody has touched in two months is let go, each on one batch with one
`undo` and one line in the weekly wrap — never a question, never a queue to
clear.

Four fences make that safe, and they are not optional. Each one names the
place it is CALLED, not only the place it is written: a rule that cites a
function nothing calls is worse than no rule (EXIT1 fix round 1, F-1/F-2).
1. **Reversible by name.** Every act carries a `brain_batch_id` whose change
   class has a registered reverser in `brain_undo.REVERSERS`. An act nobody
   can undo may not be automatic.
2. **Importance first.** Whatever an automatic exit hides, rests or lets go,
   it never touches an item that is overdue or due inside the next seven
   days — the same rule the caps and folds obey.
3. **One question per item.** A withheld close never writes a second
   proposal on an item that is already carrying a question
   (`exit_doors.row_already_asked`, reading the shipped mark, called by
   `commitment_policy_pass.apply_transcript_results` on the withheld-close
   path).
4. **Their word outranks the argument.** These rails close on an argument —
   the owner's own word, a fact on file, six weeks of silence. The other
   side coming back about the item outranks all three: their reply on a
   recently exit-closed item REOPENS it, with its own receipt and its own
   `undo` (`exit_doors.exit_closed_rows` + `reopen_on_counter_evidence`,
   called by `reconcile_inbound_commitments.reconcile_inbound_and_receipt`
   — the only rail that reads their replies). "Recently exit-closed"
   includes the CALENDAR leg: that closer is route 1's other half, signs
   its closes `calendar-close`, and stamps `exit_route: "fact"` like the
   rest. The put-back is itself an act under fence 1: its own batch id, its
   own registered reverser (`counter_evidence_reopen` — the undo closes the
   item again with the original closer's own words, never invented
   evidence), and its own line in the CHANGED list (`change_feed`
   `put_back`), because a receipt that reaches only a diagnostic log
   reaches nobody. An OUT-OF-OFFICE, an auto-reply, a read receipt or a
   delivery notice from the other side is NOT their word and never puts an
   item back.

And what counts as "nobody has touched this" is the customer's hand as well
as ours: a snooze, an un-snooze, a verb tapped on the row, and the other
side's own recorded move are all movement
(`commitment_activity.CUSTOMER_TOUCH_EVENT_TYPES` /
`COUNTERPARTY_ACTIVITY_EVENT_TYPES`, in the one derivation every staleness
surface reads). A receipt that says nobody touched an item the customer
snoozed last month is a wrong sentence, not a rounding error.

Enforcement: `tests/run_guard_g61_exit_acts_test.py` (G61) scans every
customer-facing surface that renders open items for an offer-to-close
sentence, and pins that each rail's act carries its batch id and a
registered reverser. `tests/run_exit1_test.py` section [9] pins fences 3 and
4 AT THEIR CALL SITES and removes each one to watch the regression come
back. The counter-rule is the transcript closer for OTHER people's promises,
which is OFF and stays off until M says otherwise — that one is not a fact,
it is a reading of somebody's words.
---

## Rule 35 — Every question carries a lifetime, a default and a way back (TTL1 / SPEC_FLOW1 Lane G, 2026-09-07)

A question the product asks is half a mechanism. The other half is what
happens when nobody answers it, and until this rule that half was missing,
inconsistent, or silent — which is how the Staff Meeting arrived at a queue
of 174 rows whose oldest identity questions had been asked since June.

**The rule, in three parts.**

1. **Registered.** Every question class the product can ask is a row in
   `question_ttl.CLASSES`, and that row names four things: its LIFETIME (read
   from the module that already owns the number — never a second spelling of
   it), its DEFAULT, the REVERSER that puts the default back, and the RAIL
   that applies it. A new question class without a registry row is a defect.
   A registry row whose reverser is not registered in `brain_undo.REVERSERS`
   is a defect: an automatic act with no way back is not legal on any tier,
   and the reverser lands in the same commit as the writer (Rule 29's
   same-commit posture, applied to reversibility). **That is checked before
   the write, not after it** (`question_ttl.assert_reversible`, fix round 1):
   the engine refuses the class loudly and the questions stay on the page,
   rather than writing a tombstone whose only symptom is an `undo` that
   fails later. Every `reverser` field is therefore a REVERSERS key, or a
   tuple of them — never prose, because prose cannot be checked.
2. **Stated.** A surface that renders a question SAYS what leaving it does,
   in that workspace's own numbers. The sentence is composed from the class's
   lifetime (`question_ttl.expiry_note`, and for the meeting card
   `attribution_doors.card_expiry_sentence`) — never typed into prose. A card
   that says "two days" beside a drain configured to five days tells the
   customer the opposite of what the product will do; that is the F-5 defect,
   and it recurs every time a number is spelled twice. The sentence is
   RENDERED by the builder that builds the card
   (`attribution_doors.build_card_questions_view` carries it as the section's
   `footer_note`), not instructed to a model in one skill's prose — an
   instruction is a promise that goes missing, which is exactly what B3.2
   recorded. The number has ONE spelling too (`question_ttl.days_phrase`).
3. **Bounded by the budget, not by the page.** No questions surface renders
   more in one fire than the seat's own question budget
   (`quiet.QUESTION_BUDGET` for the effective preset, published as
   `question_ttl.staff_meeting_question_ceiling`). A page bound decides what
   one screen holds; it never decided how many questions a week the customer
   agreed to answer, which is why a bounded page still sat on an unbounded
   queue. A new asker comes out of that budget, never on top of it.
   **The ceiling bounds the ASSEMBLED PAGE, every lane of it** (fix round 1):
   the queue lane AND every appended section, because the header count is the
   sum of them and the header count is what the customer reads. Bounding the
   queue alone left the meeting fold's own caps (3 calls / 8 rows, both larger
   than a `light` seat's five) free to walk through the ceiling on an ordinary
   week. `proposal_digests.bound_extra_sections` is the clamp; the queue keeps
   a floor of one row, a trimmed section says so in its own title, and nothing
   is lost — the rest lead the next fire.

**And the safety rail that makes automatic answering acceptable at all:** an
expiry never CLOSES or DROPS a row that is overdue, due this week, or carries
a client or money. It applies the "track it" default instead — the question
stops being asked, the row stays open and keeps its place on the plate. The
predicate is `question_ttl.importance_hold`, it is asked of the default THIS
ROW would take rather than of its class, and it is the same ordering CUT-D
pinned for the plate cap (overdue and this-week are never what a cap hides)
applied to the other end of a row's life. Its four legs read the ROW, so a
projection that drops the date or the amount disarms two of them: the queue
projection therefore carries `due` and the money fields through from the
source event (`brain_proposals._open_brain_proposals`, fix round 1), and the
date legs are answered on the WORKSPACE'S day rather than UTC midnight
(`tz.localize_date`, G14).

**And a lifetime is measured from LAST ACTIVITY, never from the row's
birthday** (fix round 1). A row the customer put back with `undo` has moved;
aged from its original opening it is let go again by the very next daily run,
which makes the way back a one-day promise in every class. This is the rule
the neighbouring drain already states in its own words — "a row the user put
back stays put back" — and the same baseline answers it
(`commitment_backlog_sweep.last_activity_map` for commitment rows,
`last_activity_at` on the brain-family projection). The boundary is `>=`, so
a class acts ON its day and not the day after, which is the instant both
neighbouring rails already use.

Enforcement: `tests/run_guard_g62_question_ttl_test.py` (every clause proven
by removal) + `tests/run_ttl1_test.py`. Every act the engine writes is a
MACHINE act under ATTRIB2's vocabulary — nobody answered, which is the whole
premise — and one expiry run is one batch id and one `undo`.
## Rule 36 — An update may rest a customer's work; it may never end it (CLEANUP1, SPEC_FLOW1 Lane E, 2026-09-07)

A release manifest's `auto_apply` action, running unasked on twenty-one seats
the moment the customer types `update command room`, may PARK an open row. It
may not close one, drop one, let one go, or delete anything — not a row, not an
event, not a file. Parked is the whole of what an update is allowed to do to
somebody's open work: the row stays open, keeps its own words, renders under
Parked with the reason on it, returns by itself the moment either side touches
it, and one `undo` puts the whole batch back.

Concretely, an action module under `shared/scripts/release_actions/` must not
call `commitment_state.close_commitment`, `drop_*`, `apply_amnesty`,
`apply_decisions`, or any delete/unlink of workspace data, and must not append
`commitment_closed` / `commitment_dropped` events by any route. The one act it
takes is written through the sanctioned writers (`park_commitments` —
CONTRACT Rule 31, the log is append-only and nothing is rewritten by hand),
stamped with ONE `brain_batch_id` so `undo` reverses the whole run in one word,
and attributed to the machine — the bridge's own name, never a person id
(ATTRIB2).

ONE receipt, and it says which one it is. The action writes a single
`plugin_update_remediation` carrying `receipt_role: "marker"`, the bar it ran
at (`days`), the batch id and the counts; that row IS the per-seat, per-bar
one-shot marker, and the idempotency read requires all three of the role (or
the action module's own name in `source`), `outcome: "applied"` and the
matching bar, so a bridge-written audit row and a run that parked nothing can
neither of them silence the item forever. The bridge's Step 4.8c, which
otherwise writes its own `plugin_update_remediation` for every item that
surfaces, skips this one when the action's returned context says
`receipt_written` — so exactly one row lands per act rather than two. (Before
this rule the type alone was the marker, and two rows landed: the operator's
log read the act twice.)

Why: the customer did not ask for this. They typed `update command room` and
got a background pass over their entire open book. A park is recoverable by a
word and visible with its reason; a close is a claim that something HAPPENED,
and the product has no evidence of that — an argument from silence is a reason
to stop showing a row, never a reason to say it is done. The rule is what makes
the one-shot safe enough to run unattended on every seat at once.

Enforcement (honest): MECHANICAL for the ending shapes —
`tests/run_cleanup1_test.py` [7] scans every module under
`shared/scripts/release_actions/` for `close_commitment(`, `drop_commitment(`,
`apply_amnesty(`, `apply_decisions(` and the `commitment_closed` /
`commitment_dropped` event types, and reds by name on a hit (removal proof:
planting a `close_commitment(` call in a copy of the action module reds the
check). GUIDANCE for the deleting half: a migration that removes the temp file
or the backup copy it wrote itself is a reversal, not destruction (the rollback
path in `release_actions.migrate_seed_anchors`), and no scan can tell the two
apart from the call shape — a reviewer reads the diff.
## Rule 38 — A schedule fold is not a retirement, and it never forgets how to say yes again (FOLD1A, SPEC_FLOW1 Lane H, 2026-09-07)

A scheduled chat the product FOLDS into another surface (its headline moves
elsewhere; the daily fire itself is switched off) is a DIFFERENT promise from
one the product RETIRES (`schedule_config.RETIRED_TASKS` — elimination,
rename, or readiness). Membership in `schedule_config.FOLDED_FIRES` carries
three obligations no retirement class shares:

1. **The row stays in `DEFAULT_SCHEDULES`.** A folded id's cron and label are
   never removed — `add <task>` needs real metadata to register from, on the
   seat that folded it or a fresh one, forever.
2. **A real `undo` exists.** The disabling act stamps ONE `brain_batch_id`
   and registers a `brain_undo` reverser for its change class — a bare
   `undo` right after the fold puts the live registration back in one word.
   This is the line no retirement class may ever cross the other way: a
   readiness retirement's `add`/`resume` is a REFUSAL by design (the
   substrate was not ready; asking again would rebuild the register-then-nag
   pattern the retirement removes) — see `change-schedule` SKILL.md's
   readiness rule. A fold's refusal-free `add` is not a loophole in that
   rule; it is a different rule for a different reason a fold is not one.
3. **The content stop is per-fire, not permanent, and it reads state the
   fold ITSELF wrote.** The folded orchestrator file gates on the WORKSPACE's
   current state (`schedule_config.fold_is_active`) every time it fires —
   never a hardcoded "this file is retired forever" stub. The moment the
   workspace's own state says the fold is off (a fresh install that never
   took it, or a customer's `undo` / `add`), the SAME file falls through to
   its full, original chat body. **The act that folds must therefore write
   the STORE that gate reads** (`workspace.schedule_config[<id>]["enabled"]`
   in entities.json, through `schedule_config.apply_fold_state`), not only
   the `schedule_config_changed` audit event: the audit writer's consumer is
   the lateness ledger, and a gate reading a container nobody wrote is a gate
   that is off on every seat forever. FOLD1A's first cut shipped exactly that
   and its own pins were green over it (REVIEW_FOLD1A F-1); the store write
   is also what makes the content stop a RECEIPTED, undoable act rather than
   a release deciding on its own that somebody's chat goes quiet.
4. **The undo restores what was there, not a default.** The fold stamps each
   id's PRE-FOLD override on its own event (`schedule_config.
   FOLD_PREV_OVERRIDES_KEY`) and the reverser puts that back verbatim —
   deleting the key again for an id that had no override, so the read falls
   through `DEFAULT_SCHEDULES` exactly as it did. A bare `enabled: true`
   would leave an override the customer never made, which is the orphan-key
   class SCHEDINH1 exists to stop.
5. **ONE receipt, and it says which one it is** — CONTRACT Rule 36's shape,
   which this class inherits whole: `receipt_role: "marker"`, `outcome:
   "applied"`, the batch id, and an idempotency read that requires the role
   (or the action module's own name in `source`) AND the applied outcome, so
   a bridge-written audit row for the same item can never silence the fold on
   that seat forever.
6. **The fold's own OFF is distinguishable from the customer's** (fix round 2,
   REVIEW_FOLD1A R-3). `enabled: false` in the store is what a fold writes and
   it is also what a customer's `pause` writes, and the two are different
   facts about whose decision it was: the fold is the product's, a pause is
   theirs. The folding writer therefore stamps a discriminator on the stored
   override (`schedule_config.FOLDED_BY_KEY`, carrying
   `schedule_config.FOLD_REASON` — the same string its audit event's `reason`
   carries), and ONLY when the fold is what actually switched that chat off;
   the reverser takes it away with the rest of the override. `fold_is_active`
   and `paused_by_customer` are the two reads, and every surface that narrates
   this class — `change-schedule`'s FOLDED render above all — gates on the
   STATE, never on class membership. This is the same device, for the same
   reason, as SCHEDINH1's `SUPERSEDED_BY_KEY`. Telling someone an update
   folded a chat they switched off themselves is the same not-knowing-its-own-
   state defect this rule exists to remove, pointed the other way.

Concretely: a module that folds a schedule must not add the folded id to
`RETIRED_TASKS`, must not compose a "your chat is off the schedule, here is
why it needed groundwork first" line for it (`schedule_config.
readiness_retirement_summary`'s framing is a LIE about a working surface the
product chose to simplify), and must not let `change-schedule`'s readiness
`add`-refusal rule apply to it.

Why: `schedule_config.RETIRED_TASKS`'s own module comment is explicit that
its three classes exist for surfaces that are GONE, MOVED, or NOT READY —
none of which describes a fully working chat the product is folding for the
customer's sake, reversibly, by design (DESIGN_RULE_THREE_SURFACES: "we want
to show less options to clients — they are overwhelmed", never "this cannot
work yet"). Borrowing the readiness class's mechanics without its class marker
degrades to the SAFE class (elimination — propose, never silent) per
`schedule_config.retirement_class`'s own documented default, silently
breaking the "applied, then narrated, on every seat" shape FOLD1A's fold
requires; borrowing readiness's REFUSAL posture on top of that would tell a
customer forever that a working feature "isn't ready", which is false.

Enforcement (honest): MECHANICAL for the structural half —
`tests/run_guard_g66_fold1a_gate_test.py` pins that `schedule_config.
FOLDED_FIRES` and any module's own "which ids does this fold" constant name
the same set, and that every folded id's orchestrator file carries the
Step-0 `fold_is_active` gate before its Phase 1 body (removal proof: the
gate paragraph stripped from a copy of the file fails the check by name), and
that the state that gate reads is REACHABLE — the guard runs the fold end to
end on a fixture and reads `fold_is_active` False / True / False across the
act and its `undo` (removal proof: `apply_fold_state` reverted to an
audit-event-only write reds the named check, and the store reads `{}`).
GUIDANCE for the class-choice half: nothing mechanically stops a future
edit from adding a genuinely-not-ready surface to `FOLDED_FIRES` instead of
`RETIRED_TASKS` (or vice versa) — a reviewer reads the customer-facing line
each class produces and asks whether it is true.
