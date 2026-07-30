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

1. **The canonical surface is the H2 heading link** — `## → **[Document title](computer://<native-windows-path>)**`. Rendered via `chat_output_renderer.doc_headline_link(label, url)` (single-doc) or `doc_headline_link_h3(label, url)` (multi-doc lists). Single-doc skills (`call-prep`, `memo-writer`, `one-pager-composer`, `decision-memo-composer`, `board-pack-assembler`, `contract-review`, `operator-report`, `stress-test`, `dormant-customer-scan`, `automation-scanner`, `scaffold-automation`, `team-intelligence`, `cleanup`, `weekly-recap`) use H2. Multi-doc surfaces (`cr-upcoming-meetings` with several briefs) use H3.

2. **`mcp__cowork__present_files` is DEMOTED to a "reveal in folder" secondary** — pre-v3.13.0 it was the primary opener; M's 2026-05-20 testing showed the cards' primary click DOESN'T open most file types (especially `.md`) — only "Show in Folder" works. So cards are no longer the opener. Keep them as a reveal-in-folder convenience IF the user might want to navigate the file system to that location; otherwise drop them entirely. NEVER surface a `present_files` card as the only way to open a deliverable; the user will find it doesn't work.

3. **Source-citation links stay in the `Sources:` section as plain inline links** per `_hq/CONVENTIONS_SOURCE_LINKS.md` (the canonical convention doc lives in the workspace, not the plugin) — `_link(label, url)` returns `[label](url)`. Gmail threads, Granola transcripts, Drive docs, calendar events in `Sources:` do NOT use the H2 format. Source citations and generated-deliverable links are visually distinct on purpose: one is action ("open this thing I made"), the other is provenance ("here's what informed it").

4. **In-widget `artifact_link.url`** — kept as data-only per `orchestrator-upcoming-meetings.md` line 256 (Cowork's iframe sandbox doesn't reliably resolve `computer://` links inside widget HTML; the widget side is unpainted post-v2.12.0). The H2 link below the widget IS the surface that actually opens — don't try to paint anything inside the iframe.

**URL format (v3.13.0+ — native Windows form):** Cowork's Windows local-file resolver opens the native form: `computer://C:\Users\Sample\Desktop\Claude\Command Room\...\file.docx` — TWO slashes (not three), backslashes preserved, spaces UNENCODED. (The space in `Command Room` is the load-bearing part of this example, not the username.) URL-encoded variants (`%20` for space, `%3A%5C` for `:` and `\`) do NOT open. POSIX absolute paths (macOS/Linux) keep the existing `computer:///url-encoded-path` form — only the Windows-with-space case was broken pre-v3.13.0.

This format is produced by `shared/scripts/brief_path.py` `get_brief_artifact_url()`. The path is hidden behind the link label — never appears as visible text per Rule 4.

**Scope boundary:** the H2 format is for **generated deliverables a skill just produced** (`.docx`/`.pdf`/`.xlsx`/`.pptx`). Source citations stay plain. Links inside in-widget HTML stay unpainted.

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

The leak scanner in `chat_output_renderer.py` enforces this list as a BLOCKING gate. Any leak detected → ABORT, surface plain English.

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
| "Pulse Phase 3 fired with 5 dormancy candidates" | "Found 5 people you haven't talked to in a while." |
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
  - **Morning Brief** (daily, weekday morning) — produced by `morning-briefing`
  - **Upcoming Meetings** (daily, weekday morning) — produced by `orchestrator-upcoming-meetings`
  - **Past Meetings** (rolling, post-meeting) — produced by `orchestrator-past-meetings`
  - **Inbox Triage** (daily, weekday morning) — produced by `inbox-triage` via `orchestrator-inbox`
  - **Commitments** (daily, weekday morning) — produced by `orchestrator-commitments`
  - **Pulse** (weekly, Friday morning) — produced by `orchestrator-dont-forget`
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
- `discover_gmail_tool(tools, operation)` / `discover_mail_*()` — native mail send/reply/draft/search/thread-fetch. Prefer the `discover_mail_*` family: it spans every stack, and it identifies a UUID-namespaced connector by the capability manifest's fingerprints when the tool ids spell no product name (which is every real connector).
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

Brief content = MEETING SUBSTANCE ONLY. Forwardable to a third party without redaction. Footer states: `Forwardable: yes — contains no internal asks or drafts.`

## Rule 16 — Self-refresh after every plugin upgrade

After installing a new plugin version, the user MUST run `set up command room schedules` to refresh registered prompts. The skill compares each registered prompt against the current orchestrator file content and overwrites stale ones via `update_scheduled_task`. v2.11.4+ self-refresh logic is the only path that propagates orchestrator changes to the scheduled-task DB.

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

If two surfaces use the SAME display label, they MUST behave the same way. Per Bo's Apr 30 testing: clicking "Resolved" surprised him with a textarea on Pulse but was a clean state-change on Commitments. Same label, different mechanic = bad UX.

v2.14.1 unified `resolved` to plain state-change everywhere (no input affordance). If a future surface needs a "with reason" variant, it gets a DIFFERENT verb (e.g., `mark in touch [reason]`), not the same verb with surprise input.

Same rule applies generally: action labels must do what their verb implies, with no surprise inputs. Brackets `[input]` in the action_id signal that the widget will expose an input on click — and that's the ONLY signal users have. Don't violate it.

## Rule 22 — Plugin-root discovery is deterministic, not agent-improvised (v2.14.3+)

Per Cowork's confirmed sandbox model: each `mcp__workspace__bash` call is independent (no cwd carryover, no env carryover). There is NO env var like `CR_PLUGIN_ROOT`. Session IDs are non-stable across reinstalls.

**The agent must NEVER guess at the plugin root.** It uses this exact discovery pattern at the start of every multi-step bash invocation:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
cd "$PLUGIN_ROOT" && python3 -c "..."
```

`CLAUDE_CODE_TMPDIR` is the only Cowork-set env var we rely on; from it the session directory derives, and the plugin's installed copy is the first match in `.remote-plugins/`. **v2.14.26+ canonical `WORKSPACE` resolution:** `WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')` — discovers the user's workspace folder dynamically by finding any mounted folder containing `_hq/`. Replaces the pre-v2.14.26 hardcoded `WORKSPACE="$SESSION_DIR/mnt/Command Room/Command Room"` which assumed a specific folder name + nested layout that was inaccurate for most installs (the doubled-Command-Room path was a phantom level — Cowork mounts the connected folder directly, not as a subfolder of itself). The Cowork diagnostic 2026-05-06 confirmed the actual layout is `mnt/<connected-folder-basename>/_hq/`, not `mnt/<basename>/<basename>/_hq/`. **Do NOT hardcode any specific folder name** — workspace discovery works regardless of what the customer named their folder.

The PLACEHOLDER forms `cd "<plugin-root>"` and `[PLUGIN_ROOT]` are no longer allowed in any orchestrator or skill. v2.14.3 began the sweep; v2.14.15 finished it after a /simplify pass found regressions across 9+ files (workspace-manager, command-room-update-bridge, enable-command-room-schedules + 5 orchestrator references, enable-orgs-map, enable-quick-commands, meeting-notes, usage-report, shared/WORKSPACE_API.md). New skills must use the discovery pattern from day one. Run `grep -rn "<plugin-root>\\|\\[PLUGIN_ROOT\\]" skills/ shared/` before every release; non-empty result outside `CHANGELOG.md` and this file is a release blocker.

This is the third architectural enforcement in this series: validators (v2.13.0 + v2.14.1) keep the agent from improvising labels/leaks/data shapes; helpers (v2.14.2) keep the agent from improvising tool selection; v2.14.3 keeps the agent from improvising paths. Each closes a class of agent-freedom drift.

## Rule 21 — Native connector parity (v2.14.2+)

Per M's Apr 30 ask: *"when we talk gmail or granola or google drive — we need to make sure we consider microsoft/fireflies/onedrive etc... whatever is a native connector should work the same."*

Every native connector is addressable through abstracted helpers in `shared/scripts/tool_discovery.py`. Same code paths work for both Google + Microsoft / alt stacks:

| Capability | Google stack | Microsoft / alt stack | Superhuman | Helper |
|---|---|---|---|---|
| Mail send | Gmail (`send_message`) | Outlook (Graph `send_message`) | native `send_draft` | `discover_mail_send_tool()` |
| Mail reply (threaded) | Gmail (`send_draft` + threadId) | Outlook (`reply_to_email`) | native threaded reply | `discover_mail_reply_tool()` |
| Mail draft | Gmail (`create_draft`) | Outlook (`create_draft`) | `create_or_update_draft` | `discover_mail_draft_tool()` |
| Mail search | Gmail (`search_threads`) | Outlook (`outlook_email_search`) | `query_email_and_calendar` | `discover_mail_search_tool()` |
| Mail thread fetch | Gmail (`get_thread`) | Outlook (`get_conversation`) | `get_thread` | `discover_mail_thread_fetch_tool()` |
| Calendar | Google Calendar (`google_calendar_*`) | Outlook Calendar (Graph) | fronts calendar too | `discover_calendar_tool()` (cross-stack) |
| Transcript | Granola | Fireflies | — | `discover_transcript_tool()` |
| File storage | Google Drive | OneDrive | — | `discover_drive_tool()` |

Discovery helpers return `DiscoveryResult.platform` indicating which stack matched (`"gmail"` / `"superhuman"` / `"outlook"` / `"granola"` / `"fireflies"` / `"google_drive"` / `"onedrive"` / `"google_calendar"` / `"outlook_calendar"`).

**An operation is not always a tool name.** Search SCOPES — `in_sent`, `unread`, `in_inbox`, `from_me`, `message_id_lookup` — are INTENTS that `connector_adapters/mail.py` compiles into a provider query; no connector ships a tool called `in_sent`. `discover_for_category` recognizes them (`connector_adapters.mail.is_search_intent`) and resolves them to the backend's SEARCH tool. Matching an intent against tool ids returns nothing on every provider, Gmail included, and a caller that reads that as "not connected" silently hands the read back to the model.

**A real connector spells no product name.** Native Gmail is `mcp__f12657a1__search_threads`, Superhuman `mcp__ec5e0bd5__create_or_update_draft` — the UUID carries the identity, not the tool id. The mail helpers therefore fall back from the product-name hints to the capability manifest's FINGERPRINTS (the same data `repair_backend` re-pairs on), so a real workspace resolves at all.

Orchestrators that need stack-specific adapter logic branch on `result.platform`. Orchestrators that don't care just use `result.tool_id`. **Hard-coded references to specific stacks in orchestrator prose are forbidden** — same enforcement model as `CANONICAL_ACTIONS` for action labels.

Zapier remains EXCLUDED from all native helpers (Calendar HARD SCOPE Rule 8 still holds; Zapier-mail is a separate path via `discover_zapier_send_tool` per `EMAIL_DRAFT_PROTOCOL.md` §3c). Native helpers are for native connectors only.

## Rule 23 — Trailing finish-cluster on Pulse review-shaped items (v2.14.5+)

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
- Score 0.30 - 0.55: write `commitment_review_proposed` event. Pulse Phase 4g surfaces these in the next fire as one-click `confirm` / `skip` items (sub-namespace `r1/r2/...`). User-facing question names the commitment + describes the evidence; user confirms it as fulfillment OR rejects it.
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
5. **Post via `mcp__visualize__show_widget`, passing `transport["html"]` (the persisted page's validated bytes, verbatim) as `widget_code`** — only after the transport call returns clean. Never hand-compose the HTML, never post-process it, and never relay an unbounded set in one page — paginate (`page=N`) for unbounded views (Bug #67; `shared/CHAT_ACTION_WIDGET.md` § Transport).
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
| 23 | Trailing finish-cluster on Pulse | GUIDANCE | Surface-specific convention. |
| 24 | CRU layer is silent | GUIDANCE | Convention; the reconcile audit event (Bug #98-v3) is the closest structural backstop. |
| 25 | Path output uses runtime `$WORKSPACE` | ENFORCED | `run_no_hardcoded_drive` guard + leak scanner |
| 26 | No real customer/partner names | ENFORCED | `run_no_real_customer_names_test` (named-pattern + structural email-domain allowlist) |
| 27 | No `.md` deliverables | ENFORCED | `run_no_md_deliverables_test` |
| 28 | Plain-English customer surfaces | ENFORCED | the 6-gate render chain + `run_customer_facing_voice_test` |

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
