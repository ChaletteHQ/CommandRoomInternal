# How Command Room Works

A single reference for understanding the plugin end-to-end — written for the operator who uses it in Cowork AND the developer who maintains it in Code. Read in order, or skip to a section.

---

## Table of contents

1. [What Command Room actually is](#what-command-room-actually-is)
2. [The substrate (the moat)](#the-substrate-the-moat)
3. [The daily loop — six scheduled chats](#the-daily-loop--six-scheduled-chats)
4. [The on-demand surface — skills by intent](#the-on-demand-surface--skills-by-intent)
5. [Lifecycle — onboarding, updates, release manifests](#lifecycle--onboarding-updates-release-manifests)
6. [Development model — cr1 staging + per-client production](#development-model--cr1-staging--per-client-production)
7. [File layout — where things live](#file-layout--where-things-live)
8. [Recurring bug classes to watch](#recurring-bug-classes-to-watch)
9. [Debugging + verifying](#debugging--verifying)
10. [Quick reference — trigger phrases by intent](#quick-reference--trigger-phrases-by-intent)

---

## What Command Room actually is

Command Room is a Claude Code plugin distributed via the Cowork marketplace. The product promise from `plugin.json`: *"Claude as your chief of staff. Turns a folder of notes, transcripts, and emails into an executive workspace."* In practice, that means:

- **The substrate**: every meeting, email, decision, commitment, and intel item flows into a structured event log (`events.jsonl`) + a relationship/project graph (`entities.json`). This is the moat. Everything else is read or write against it.
- **The daily loop**: 6 scheduled Cowork chats fire on cron (~7-9 AM weekdays). They synthesize what's happened, surface what needs attention, and draft replies. Most days the operator spends 5-15 minutes triaging the output of these chats.
- **The on-demand surface**: ~46 skills accessible by trigger phrase. Writing (email, memo, one-pager, decision-memo, board-pack), prep (call-prep, intro-broker, contract-review), people (people-crm, team-intelligence), scheduling (calendar-writer), surfacing (decision-revisit, thread-resurrection, dormant-customer-scan), lifecycle (onboarding, update-bridge), and meta (workspace-manager catch-all).
- **Voice calibration**: every writing skill runs voice calibration on every fire so drafts sound like the operator. The voice corpus grows over time as the operator sends sent-mail samples.

What it is NOT:
- Not a CRM. There's no pipeline view, no deal stage tracking, no quote generation.
- Not a project manager. Doesn't replace Asana / Linear / Notion for task management — it tracks commitments + decisions, not story points.
- Not a Slack outbound layer. People-crm reads Slack DMs; no skill drafts Slack messages the way `email-writer` drafts email. (Known gap as of v3.5.0; explicitly out of scope per the maintainer's v3.8.0 planning call.)
- _Was_ not a calendar writer pre-v3.8.0 — `calendar-writer` (v3.8.0+) now schedules meetings, drafts substrate-aware agendas (open commitments with attendees), and creates Calendar events.

The current sweet spot: operator-class CEO/founder who reads + reacts to a lot of inbound context across many threads and people, and wants substantive synthesis + drafted action without losing the personal voice.

---

## The substrate (the moat)

Everything Command Room does reads from or writes to three workspace files:

| File | What it holds | Owner |
|---|---|---|
| `_hq/data/events.jsonl` | Append-only timeline. Every meeting, interaction, decision, commitment, status change, plugin event. | Many writers; canonical schema in `shared/data-schemas/events.schema.json`. |
| `_hq/data/entities.json` | People + orgs + projects. Canonical relationship + ownership graph. | `people-crm` (people), `workspace-manager` (projects), `people-crm` + `automation` (orgs). |
| `_hq/data/aliases.json` | Name disambiguation map ("Sam" → person_023). | `people-crm`. |

### events.jsonl — the event log

The most important file in the workspace. Every consequential thing that happens lands here as one line of JSON. Read-heavy — almost every skill reads it. Write-heavy — daily loops add 10-30 events per active day.

**Append-only is non-negotiable.** Per the schema doc, events are never rewritten or deleted. Resolution / closure happens via NEW events (`commitment_resolved` references the original `commitment` by id; `decision_superseded` does the same for decisions).

**Why append-only?** History is the source of truth for surface generation. If we let events get rewritten, we can't reconstruct what the user actually saw in the morning brief on day N, can't reproduce a Pulse fire to debug, can't trust dormancy detection.

**Five known event shape variants for `commitment` events**:

| Shape | Field pattern | Source |
|---|---|---|
| Canonical | `data.owner_id`, `data.title`, `data.due`, `data.status` | post-v2.7.15 writers |
| flat-new | top-level `owner_id`, `title`, `due` | Some legacy writers, untagged scan-for-commitments output |
| legacy | top-level `owner` (no _id), `owner_display`, `requester_display` | pre-v2.7.15 events |
| owner_person_id-variant | `data.owner_person_id`, `data.due_date`, `data.state` (not `status`) | cr-past-meetings actively produces this |
| pending-review | `data.owner_name_proposed` + `data.pending_review: true` | Intentional separate path (Pulse CRU-review queue) |

This is why `shared/scripts/cru_match._commitment_field(ev, "<field>")` exists — it's the canonical reader that handles all 5 shapes via an alias chain. Every consumer that reads commitment events MUST go through this helper. v3.5.0+ also has `event_references_person(ev, person_id)` for Pulse's cadence detection (same problem at a different layer).

### entities.json — the relationship graph

Contains workspace-level state: who's a person you track, what orgs they belong to, what projects you're working on. Schema is documented in `references/ORG_AND_THREAD_MODEL.md`. The canonical writer for people records is `people-crm`; for projects it's `workspace-manager`; for orgs it's various skills routed through `people_writer.py` helpers.

Why does this matter? Every surface that mentions a person ("Sloan replied to your Apr 28 thread") goes through entities.json to resolve `person_023 → "Sloan"`. If entities.json drifts (two records for the same person, missing canonical name, wrong org affiliation), the surfaces show garbage like "person_023 replied" or attribute things to the wrong person.

Two safeguards: (1) `people_writer.py` enforces dedup at write time; (2) Pulse's people-layer synthesis pass (Phase 5) catches relationship drift after the fact.

### aliases.json — name disambiguation

Maps free-text names ("Sam", "Sam P", "DSample") to canonical `person_NNN` ids. Used by every skill that has to interpret natural-language references to people. Cheap to write; rich payoff in surface quality.

### The three-layer memory model (episodic → canonical → semantic)

The three files above are the middle and top of a three-layer memory. Naming the layers explains where every piece of the substrate lives and, more importantly, how memory *heals itself over time* instead of depending on whether a skill happened to fire at the right moment.

| Layer | What it is | Where it lives | Written by |
|---|---|---|---|
| **L1 — episodic** | The raw record of everything that was said: full session transcripts of every Command Room chat, plus meeting transcripts. Verbatim, complete, and **free** — Cowork retains it whether or not we do anything, and (proven 2026-07-01) it is readable from inside a scheduled task. | Cowork's session store + the connected meeting-transcript sources (Granola, etc.) | Nobody — it accrues automatically as the CEO works |
| **L2 — canonical** | The extracted, deduped, queryable timeline: one structured event per consequential thing. This is what almost every skill reads and writes. | `_hq/data/events.jsonl` | Many skills, all through the `append_event()` gate |
| **L3 — semantic** | The consolidated understanding: the relationship/project graph, plus the synthesized analytical views (TIMELINE / RELATIONSHIPS / COMMITMENT_AGING / DORMANT / THEMES). | `_hq/data/entities.json` + `_hq/views/` | `people-crm` / `workspace-manager` (graph); `insight-generator` (views) |

**The layers promote upward on a schedule, so capture is eventually-complete rather than moment-dependent:**

- **L1 → L2, nightly** — the `session-sweep` task (Phase 5) reads the last day's session transcripts and promotes the commitments / decisions / interactions / deliverables that *never became events* into `events.jsonl`, deduped through `.source_refs.idx`. Its one-time companion `session-backfill` does the same over the last 60 days to catch a workspace up. This is the layer that closes the biggest historical leak: anything the CEO did in an ad-hoc chat that didn't fire a writing skill used to be lost; now the episodic layer is always there and the nightly pass lifts it into the canonical log after the fact — including Bug #98-class skips (a task that rendered but didn't write; the transcript shows the render, the sweep writes the missing event).
- **L2 → L3, weekly** — `insight-generator` reads the canonical timeline and recomputes the five analytical views, consolidating events into patterns (aging, dormancy, themes).

**Why this matters:** before the L1→L2 promotion existed, memory depended on the *moment of capture* — a writing skill had to fire exactly when something happened, or it was gone. With the layers, memory stops depending on that moment. The episodic layer is always complete; the canonical layer catches up every night; the semantic layer consolidates every week. A missed capture is no longer a permanent hole — it is a leftover the next sweep collects.

---

## The scheduled chats — six daily + one weekly

These fire on cron (defaults: weekdays 6:30 AM–5 PM for the daily loop, plus Friday 4 PM for the weekly Friday Wrap). Together they constitute most of the operator-facing value. They're configured via `enable-command-room-schedules` and registered through Cowork's scheduled-tasks API. Each is one persistent chat thread in Cowork's "Scheduled" section.

### 1. morning-brief (cron 7:00 AM weekdays)

Thin wrapper around the `morning-briefing` skill (the on-demand variant). Produces a markdown digest: today's calendar, overdue follow-ups, urgent inbox items, open commitment count split by direction. Markdown surface (not a widget) — this is the only daily-loop orchestrator that doesn't render a widget.

**Why it exists separately from the other 5**: morning-brief is the most-frequently-fired and most lightweight surface. The other 5 build per-item action widgets; morning-brief just summarizes.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-morning-brief.md`.

### 2. inbox (cron 7:30 AM weekdays)

Triages overnight email into Reply Now / Decision Needed / FYI / Discard / Deep Read buckets. Drafts replies for 2-3 highest-priority items via `email-writer`. Renders a widget where each thread has its own action set (Send / Edit then send / Draft / Skip).

**The financial-signal override** (v3.x): senders matching `^(billing|invoices?|estimates?|payments?|accounting)@` get +30 priority to override the automated-domain demote — caught a $10,400 QuickBooks estimate that was previously being filtered out.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-inbox.md`.

### 3. commitments (cron 8:30 AM weekdays)

The most complex orchestrator (~700 lines of prompt). Splits open commitments into two directions × three age buckets:

| Direction | Buckets |
|---|---|
| ↗ YOU OWE | overdue / due_near / aging_undated |
| ↙ OWED TO YOU | overdue / due_near / aging_undated |

For each item: per-recipient draft of either a status-update email (YOU OWE) or a chase email (OWED TO YOU). Cowork user reviews + clicks Send / Edit then send / Defer / Done / Snooze (3 days) / Add to my list.

**Sam-class bug history**: this orchestrator was the source of the dual-shape commitment bug (Sam 2026-05-17), the all-shape audit (v3.4.4), and the canonical filter using `_commitment_field` + `_commitment_confidence` helpers.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-commitments.md`.

### 4. past-meetings (cron 9:00 AM weekdays)

Processes meeting transcripts from the last 24 hours. For each transcript:
1. Invokes `meeting-notes` silently to extract decisions, commitments, action items.
2. Invokes `follow-up-ritual` silently to draft per-attendee follow-up emails.
3. Runs the CRU pass (`cru_match`) to auto-resolve open commitments closed by completion language in the transcript.
4. Runs the decision-CRU pass (v3.4.5+) to auto-close decisions executed or superseded by the transcript.
5. Renders a widget per meeting: brief link + action items + draft follow-up emails.

**The producer side of cr-past-meetings**: this is where most commitment events get written. It's also where the `owner_person_id-variant` shape was traced from (the v3.4.4 audit finding).

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-past-meetings.md`.

### 5. upcoming-meetings (cron 9:30 AM weekdays)

Generates per-meeting prep briefs for today's calendar via `call-prep`. Each meeting in the next 24 hours gets a .docx brief in `_hq/meetings/Call_Prep_<slug>_<date>.docx` + a widget row with the link, attendees, last-touch summary, and a quick-action button.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-upcoming-meetings.md`.

### 6. pulse (cron 8:00 AM weekdays) — internally called "dont-forget"

The pattern-detection layer. Surfaces things the operator would otherwise miss:
- Person dormancy (someone you usually talk to weekly has gone quiet 18 days)
- Stale projects (no activity in N days)
- Pending CRU reviews (medium-confidence auto-resolution proposals)
- Org-layer drift (vendor became customer; org tier changed)
- New-entity proposals (a name appeared in N meetings, propose adding to entities.json)

The most sophisticated reactive surface in the product. Phase 3 reference detection is the spec for "did this event mention this person" — v3.5.0+ uses the canonical `event_references_person` helper.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-dont-forget.md` (filename retains the legacy "dont-forget" name for events.jsonl back-compat).

### Scheduling timezone rule (R8 — settled empirically 2026-07-01)

**Cowork cron and `fireAt` evaluate in MACHINE-local time — the computer's clock, not the workspace timezone.** Confirmed live: a machine in Mountain time with a Pacific workspace fired on Mountain wall-clock, and a scheduled session stamped its output in workspace time while firing on machine time. The split is real and permanent:

- **Scheduling math is machine-local.** Cron expressions in `DEFAULT_SCHEDULES` / `schedule_config`, lateness computation (`late_fire.py`), and fired-recency math (`task_watchdog.py`) all use the machine clock. Never "correct" a fire time against the workspace TZ.
- **Workspace TZ is presentation-only** (`shared/scripts/tz.py` `to_local()`): timestamps the CEO reads are rendered in `workspace.user_timezone`; nothing about when tasks fire changes with it.
- **Conversion happens once, at registration/change time.** When the user asks for a time ("set inbox to 8am"), they mean THEIR timezone — change-schedule converts via `schedule_config.workspace_time_to_machine()` before building the cron, and says so in the confirm diff when the two clocks differ. (The conversion uses the current offset; a fixed cron can't track DST transitions, so a machine/workspace TZ pair that shifts on different dates drifts by the DST hour until the schedule is touched again.)
- Most installs run machine == workspace timezone and none of this is visible; the rule exists for the ones that don't (travel, remote-desktop machines, VMs).

### 7. friday-wrap (cron 1 PM Fridays — Phase 3/R4 default for new installs; earlier installs registered at 4 PM keep their time) — NEW v3.11.0

First weekly-rhythm scheduled task. Wraps the existing `weekly-recap` skill — pulls 7 days of context across every connector (Mail, Calendar, Slack/Teams, Drive, every transcript source), runs `scan-for-commitments` on freshly-captured meeting events as a side effect, then synthesizes a recap surfaced both inline (markdown chat) and as a saved `.docx` at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`.

Unlike the 6 daily tasks, this is a **markdown-post orchestrator**, not a widget orchestrator — no `show_widget` call, no apply-choices action surface. Pure recap, link to the `.docx` in a Briefs section, STOP. Same delegation pattern as `morning-brief` wrapping `morning-briefing`.

**Spec**: `skills/enable-command-room-schedules/references/orchestrator-friday-wrap.md`.

### How widgets work (applies to 2-6 — the 6 daily widget orchestrators)

Each widget orchestrator follows the same architecture:
1. **Renderer pipeline**: `shared/scripts/build_*_input.py` projects entities + events into a structured input JSON. Then `shared/scripts/chat_output_renderer.py::render_chat_output_widget(data_view)` produces HTML bytes.
2. **Validators**: `validate_rendered_widget(html)` checks structural invariants (every input-bearing button has its matching wrapper). `validate_chat_output` runs leak-scanner regexes. Both raise on violation.
3. **`mcp__visualize__show_widget(html)`**: posts the widget to the Cowork chat surface.
4. **Apply-choices**: user clicks per-item buttons, widget batches selections, fires one consolidated `apply choices: [...]` submission. The `apply-choices` skill parses + dispatches each action.
5. **STOP CONTRACT** (v3.5.0+: `shared/STOP_CONTRACT.md`): the widget IS the chat turn. No narration after. No file-write fallback. No markdown-list substitute.

This architecture is the product's hardest-won discipline. Pre-v2.14.x the agent would freelance — write the widget to disk "for reopening later," narrate what was in the widget after posting, swap to markdown when output felt too big. Each was a recurring bug. The validators + STOP CONTRACT close those bypass paths structurally.

---

## The on-demand surface — skills by intent

About 47 skills total in `skills/`. Organized here by user intent so you can find the right one quickly.

### "Brief me on something"

| Skill | Trigger | What it does |
|---|---|---|
| `morning-briefing` | `morning briefing`, `brief me`, `what do I need to know today` | The on-demand variant of the daily morning brief. |
| `call-prep` | `prep me for [meeting]`, `prep the call with [name]` | Per-meeting brief — calendar + email + Slack + Granola + open commitments + decision context. |
| `weekly-recap` | `weekly recap`, `summarize last week`, `what happened last week` | 7-day synthesis across all connectors + decision/commitment surface + .docx output. |
| `cleanup` | `clean up my workspace`, `tidy up`, `maintenance`, `deep clean` | Weekly self-maintenance (Sundays). Auto-fixes safe issues, heals substrate corruption, surfaces only what needs eyes. No scores, no dashboard. |
| `operator-report` | `operator report`, `what did you save me`, `monthly recap` | CEO-facing "Operating Lift" report — what would have slipped, what got captured, conservative time-saved estimate. |
| `transcript-search` | `what did anyone say about [topic]`, `meetings about [topic]` | Cross-meeting topic search returning meeting hits with snippets. |
| `decision-revisit` (v3.8.0+) | `what decisions should I revisit`, `decision audit`, `decisions to revisit` | Surfaces decisions worth re-examining based on time elapsed + contradictory signal + named-condition shifts. Companion to decision-log; pure substrate skill. |
| `thread-resurrection` (v3.8.0+) | `what conversations went silent`, `warm threads to revive`, `thread resurrection` | Mirror of dormant-customer-scan but for THREADS not PEOPLE. Cross-graph awareness — surfaces commitment chase as an alternative when relevant. |
| `board-pack-assembler` (v3.8.0+) | `build the board pack for [date]`, `assemble the board pack`, `prep for the [date] board meeting` | Multi-page board pack composed from events.jsonl + decision-log + entities.json + QuickBooks. The purest substrate consumer in the plugin. |

### "Tell me about a person/relationship"

| Skill | Trigger | What it does |
|---|---|---|
| `people-crm` | `who is [name]`, `tell me about [name]`, `prep me for dinner with [name]` | Person profile — last contact + recent threads + open commitments + relationship history. |
| `team-intelligence` | `my team`, `team status`, `prep for 1:1` | Direct-report focused — 1:1 briefs, weekly team rollup, commitment tracking by team member. |
| `dormant-customer-scan` | `who went dark`, `dormant customer scan`, `who haven't I heard from` | Surfaces customers gone quiet vs their own historical cadence. |
| `intro-broker` (v3.8.0+) | `intro [A] to [B]`, `connect [A] and [B]`, `introduce [A] to [B]` | Drafts two-sided intros voice-calibrated from your past intro_made events. Logs to the relationship graph; schedules a 30-day follow-up check. |

### "Process meeting / capture something"

| Skill | Trigger | What it does |
|---|---|---|
| `meeting-notes` | `process meeting`, `meeting notes from [meeting]` | Extracts decisions + commitments + action items from a transcript. |
| `follow-up-ritual` | `follow up on the call`, `draft follow-ups` | Per-attendee follow-up emails after a meeting. |
| `decision-log` | `log decision`, `record decision` | Captures a decision to the events.jsonl + DECISION_LOG view. |
| `intel-intake` | Pastes a URL, says `intel`, `break this down` | Turns external content (article, video) into structured intel cross-referenced to workspace entities. |
| `scan-for-commitments` | `scan for commitments` | One-shot historical scan of meeting transcripts to backfill commitment events. |
| `calendar-writer` (v3.8.0+) | `set up a 30-min with [name]`, `schedule lunch with [name]`, `book time with [name]`, `put [name] on my calendar` | Schedules meetings. Finds mutual availability, drafts substrate-aware agenda (open commitments with attendee), creates the calendar event. Optionally auto-fires call-prep 24h before. Closes the v3.5.0-flagged calendar-write gap. |
| `contract-review` (v3.8.0+) | `review this contract`, `redline this NDA`, `check this MSA` | Parses contract PDF/docx, compares against your standard terms, flags deviations green/yellow/red with redlines. History-aware — surfaces "this counterparty pushed for this carve-out before" patterns. |

### "Write something in my voice"

| Skill | Trigger | What it does |
|---|---|---|
| `email-writer` | `draft an email to [recipient]`, `email [name] about [topic]` | Voice-calibrated email draft. |
| `memo-writer` | `write a memo on [topic]`, `decision doc`, `board update` | Voice-calibrated internal memo / decision doc / monthly investor update. |
| `one-pager-composer` | `one-pager on [topic]` | Voice-calibrated structured one-pager. (v3.7.1+: writes `one_pager_drafted` events; reads decision-log + intel for substrate-aware drafting.) |
| `decision-memo-composer` (v3.8.0+) | `decision memo on [topic]`, `tradeoff analysis`, `choosing between [A] and [B]` | Structured tradeoff analysis — framing, options, weighted criteria, comparison matrix, recommendation. Optional stress-test integration. Auto-fires decision-log on "Log decision" click. |
| `stress-test` | `stress test this`, `pre-mortem`, `red team` | Munger-style inversion — failure-mode mapping + safeguards. |

### "Manage workspace lifecycle"

| Skill | Trigger | What it does |
|---|---|---|
| `workspace-manager` | `let's work`, `what's going on`, `new project`, `archive`, named-entity references with no specialist match | Catch-all router + project/session manager. |
| `command-room-onboarding` | First install (auto), `set up command room`, `restart onboarding` | 6-phase M1 onboarding (~40 min) distributed across 13 chats. |
| `command-room-update-bridge` | `update command room`, `what's new`, `install latest` | Reconciles missing dashboards + applies workspace migrations + plays release manifests (v3.4.5+). |
| `enable-command-room-schedules` | `set up command room schedules`, `change schedule` | Registers / re-registers the 7 scheduled chats (6 daily + 1 weekly Friday Wrap, v3.11.0+). |
| `enable-workspace-map` (renamed v3.5.0 from `enable-orgs-map`) | `install workspace map`, `enable workspace map`, `rebuild workspace map` | Installs the Workspace Map sidebar artifact. |
| `enable-quick-commands` | `install quick commands` | Installs the Quick Commands cheat-sheet sidebar artifact. |
| `level-up-command-room` | `level up command room`, `add dashboards` | Umbrella menu of optional Layer 2 dashboards. Empty as of v3.11.0 — Commitment Cockpit retired and folded into the Commitments scheduled chat. |

### "Pull in / file existing context"

| Skill | Trigger | What it does |
|---|---|---|
| `ingest-context` (v3.5.0+) | `ingest context from [path]`, `pull context from [path]`, `import chatgpt` | Extract people/projects/decisions from a source; no file copies. |
| `file-documents` (v3.5.0+) | `file documents from [path]`, `sort my downloads into projects` | Same as above PLUS copy source files into project folders. |
| `workspace-ingest` | `ingest folder [path]`, `scan my desktop` (covers both intents) | Underlying pipeline; alias skills route to subsets. |

### "Bug triage / diagnostics"

| Skill | Trigger | What it does |
|---|---|---|
| `report-bug` | `report bug`, `this isn't working`, `something broke` | OUTBOUND — diagnoses + drafts a Gmail to matthew@chaletteholdings.com. |
| `usage-report` | `usage report`, `where does the spend go` | pack_run telemetry aggregation. |
| `automation-scanner` | `automation scan`, `what can be automated` | Surfaces repetitive patterns in events + session notes worth automating. (v3.7.1+: writes per-opportunity `automation_opportunity_surfaced` events.) |
| `scaffold-automation` (v3.8.0+) | `scaffold #N`, `build the automation for [X]`, `scaffold the [opportunity] one` | Pairs with automation-scanner — picks an opportunity by seq and generates real working artifacts (Zapier config / Python skeleton / n8n flow + setup recipe + rollback doc). Closes the scan→build loop. |

### "Quick utility"

| Skill | Trigger | What it does |
|---|---|---|
| `list-active` | `list projects`, `active projects`, `roster` | Zero-interaction tree render of all projects. |
| `show-my-list` | `show my list`, `discuss list` | Curated "discuss later" list captured via "add to my list" actions across surfaces. |
| `log-resolution` | (auto on ✓ done clicks) | Writes thread_resolved events when user clicks ✓ in a widget. |
| `apply-choices` | (auto on Apply all clicks) | Receives consolidated widget submission, dispatches each action. |

---

## Lifecycle — onboarding, updates, release manifests

### First install (onboarding)

`command-room-onboarding` fires automatically on first install when `CLAUDE.md` doesn't exist in the workspace. M1 (2026-05-23+; scheduled-task generation stripped 2026-06) ships a 6-phase ~30-min flow distributed across several chats. **Onboarding registers no scheduled tasks** — the daily/weekly scheduled chats are an opt-in the customer sets up after the call by running `set up command room schedules` in a fresh chat (registration only works reliably from its own chat, which is why onboarding no longer attempts it):

0. **Setup widget** — workspace shape, email exclusions, timezone, AI name (progressive-reveal widget; AI name defaults to "Penelope").
1. **Scan + workspace build + Workspace Map** — Chat 1 runs the 60-day metadata scan + builds the workspace; Chat 3 (customer-opened) installs the Workspace Map. No backfill task, no schedules chat.
2. **Mirror + Voice contrast + Insights in Chat 4** — Mirror v1 + Voice contrast immediately on Opus; Insights fire user-triggered when the customer types `show me what's next`, computed from the 60-day scan (no deep-read wait). The deeper last-7-days read is pointed to via on-demand `weekly-recap`.
3. **Compounding loop** — Chat 4 frames how the substrate compounds (every meeting / decision / follow-up / `weekly-recap` builds on the 60-day baseline).
4. **(removed)** — the old Run Now ritual for 5 scheduled chats is gone; onboarding registers nothing to authorize.
5. **Training prompts** — customer fires 3 hands-on commands in 3 new chats (`prep me for [meeting]`, `tell me about [person]`, `draft a check-in to [person]`).
6. **Coach handoff** — accomplishment summary (which points the customer to `set up command room schedules` and `weekly-recap`); Chat 4 becomes the customer's permanent home with their AI via the `command-room-coach` skill.

Day-1 customers register **no** scheduled tasks during onboarding. When ready, they opt in via `set up command room schedules`, which registers the 5 first-install tasks (`morning-brief`, `upcoming-meetings`, `past-meetings`, `inbox`, `friday-wrap`) per `FIRST_INSTALL_TASK_IDS`. The remaining 2 (`commitments`, `pulse`) get added later in operator-driven follow-up sessions once accumulated workspace signal makes them useful.

### Plugin updates

Updates happen via Cowork's UI (Customize → Personal Plugins → Check for updates → Update). Plugin code refreshes on disk; restart Cowork to pick up new code.

After restart, the customer can run `update command room` to fire `command-room-update-bridge`, which:
1. Detects missing default sidebar dashboards (Workspace Map, Quick Commands) and installs them.
2. Detects pending workspace-file migrations (CLAUDE.md preference additions, BUSINESS_CONTEXT additions) and applies them after user confirm.
3. **v3.4.5+**: plays per-version release manifests at `shared/releases/v*.json`. Each manifest's items have detectors that check workspace state; only items whose detectors return truthy get surfaced. Example: the v3.4.4 manifest's `count_dropped_open_commitments` detector counts non-canonical commitments in your workspace; if you have any, you see a re-fire prompt with the actual count.

### Release manifests (v3.4.5+)

Every release ships a manifest at `shared/releases/v<X.Y.Z>.json`. Mandatory — `ship-cr-plugin` (in the chalette plugin, v0.4.1+) blocks the ship if it's missing.

Manifest schema in `references/RELEASE_MANIFEST.md`. Two action types currently supported:
- `announce_only` — informational, no user action required (uses `release_detectors.always` detector).
- `instruct_user` — tells the user what to type/click in Cowork (e.g., "re-fire your Commitments task"). Has a real detector that gates whether to surface.

Future action types (`apply_workspace_migration`, `programmatic_refire`) will be added as needed.

---

## Development model — cr1 staging + per-client production

Current model established 2026-06-22 (the "cr1 model"). Documented in the staging repo's `DEVELOPMENT.md` and the operator workspace's CLAUDE.md + `_hq/INFRASTRUCTURE.md`.

### The canonical edit surface

**A dedicated working clone is the source of truth.** Path: `~/repos/cr1-canonical/command-room/` — a git clone of **`ChaletteHQ/cr1`**, the private staging repo. Edits land there directly and push to `cr1`.

**Do not edit any Command Room clone under `~/.claude/plugins/marketplaces/`.** Those are Cowork's locally-installed copies — read-only install caches, coupled to the Cowork install location and stale the moment staging moves ahead. The legacy staging marketplace clone's remote was renamed to `oldtest` and retired on 2026-06-22.

### Staging vs production

| | Repo(s) | Visibility | Role |
|---|---|---|---|
| Staging | `ChaletteHQ/cr1` | Private | The canonical edit surface clones this; the operator dogfoods from it |
| Production | Per-client repos under `ChaletteHQ`: `CommandRoomInternal` plus one `CommandRoom<Client>` repo per client | Private (one repo per client) | What each client installs from |
| Chalette admin | `chaletteholdings/chalette` | Private (operator only) | Internal operator tooling |

A promote fans the core out from staging to every per-client repo via `scripts/promote_core_to_clients.py`, honoring each client's `_chalette/overrides.json` so client-custom skills are never clobbered.

### Ship flow

`ship-cr-plugin` skill in the chalette plugin handles the full release ritual:

1. Pull staging fresh
2. Status check (no unexpected dirty state)
3. Release-readiness inspect
4. Get version bump from the operator
5. Get release notes summary
6. Write CHANGELOG.md + plugin.json
7. **(v0.4.1+) MANDATORY release manifest at `shared/releases/v<X.Y.Z>.json`** — Step 5.5. Step 6 has a pre-commit gate that aborts if missing.
8. Commit + push to staging
9. Mirror to Cowork local-uploads (bypass VHD cache)
10. Tell the operator what to do in Cowork

Production promote is a separate command (`promote v3.X.Y`). It fans staging out to every per-client repo (see above), commits + tags + pushes each.

### Marketplace.json contract (non-negotiable)

- **NO `version` field on the plugin entry.** Field present → Cowork treats plugin as "already at this version" and grays out Update button. Field absent → Cowork falls back to commit-SHA detection. Documented as Claude Code GitHub issues #52218, #26744, #31462.
- Plugin internal name lowercase only. `CR` was rejected at sync; `cr` works. Cowork title-cases for display.

---

## File layout — where things live

```
command-room/
├── .claude-plugin/
│   └── plugin.json                    # Plugin metadata + version
├── CHANGELOG.md                       # All releases, newest first
├── README.md                          # Marketplace-facing description
│
├── skills/                            # ~46 skills, one directory each
│   ├── morning-briefing/SKILL.md
│   ├── inbox-triage/SKILL.md
│   ├── workspace-manager/SKILL.md
│   ├── command-room-onboarding/SKILL.md
│   ├── command-room-update-bridge/SKILL.md
│   ├── enable-command-room-schedules/
│   │   ├── SKILL.md                   # The registration skill
│   │   └── references/                # The 7 orchestrator prompts (6 daily widgets + 1 weekly markdown)
│   │       ├── orchestrator-morning-brief.md
│   │       ├── orchestrator-commitments.md
│   │       ├── orchestrator-inbox.md
│   │       ├── orchestrator-past-meetings.md
│   │       ├── orchestrator-upcoming-meetings.md
│   │       ├── orchestrator-dont-forget.md       # Pulse
│   │       └── orchestrator-friday-wrap.md       # NEW v3.11.0 — weekly recap
│   └── (40+ more skills)
│
├── shared/                            # Cross-skill helpers + contracts
│   ├── CONTRACT.md                    # Rule 1-24 (path resolution, validators, etc.)
│   ├── STOP_CONTRACT.md               # (v3.5.0+) Post-widget output rules
│   ├── COMMITMENT_SCHEMA.md           # Canonical commitment event shape + variants
│   ├── ORG_AND_THREAD_MODEL.md        # Entity graph schema
│   ├── CHAT_ACTION_WIDGET.md          # Widget submission contract
│   ├── EMAIL_DRAFT_PROTOCOL.md        # Send dispatch order (Zapier / native / standalone)
│   ├── VOICE_CALIBRATION.md           # Voice-calibration protocol
│   ├── WORKSPACE_API.md               # Atomic writes + classification rules
│   ├── PASSIVE_CAPTURE.md             # Auto-event-capture spec
│   ├── data-schemas/
│   │   └── events.schema.json         # Canonical event-type enum + payload shapes
│   ├── releases/                      # (v3.4.5+) Per-version release manifests
│   │   ├── v3.4.2.json
│   │   ├── v3.4.3.json
│   │   ├── v3.4.4.json
│   │   ├── v3.4.5.json
│   │   └── v3.5.0.json
│   └── scripts/                       # Python helpers (importable from skills via bash)
│       ├── cru_match.py               # Commitment match scoring + load_open_commitments
│       ├── decision_match.py          # Decision auto-resolve / supersede
│       ├── confidence.py              # (v3.5.0+) Shared threshold constants
│       ├── build_workspace_map_input.py
│       ├── build_dcc_input.py
│       ├── chat_output_renderer.py    # The renderer pipeline + validators
│       ├── render_artifact.py         # Sidebar artifact renderer
│       ├── brief_writer.py            # .docx brief generator (deterministic format)
│       ├── people_writer.py           # Canonical entities.json writer for people
│       ├── atomic_write.py            # Atomic append/write helpers
│       ├── telemetry.py               # pack_run telemetry builder
│       ├── tz.py                      # Timezone helpers
│       ├── tool_discovery.py          # MCP tool discovery (Gmail, Calendar, etc.)
│       ├── zapier_send.py             # Zapier reply-thread helpers (latest message id)
│       └── release_detectors/         # (v3.4.5+) Per-release manifest detectors
│           ├── always.py
│           └── v3_4_4_dropped_commitments.py
│
├── references/                        # Higher-level architecture docs
│   ├── HOW_COMMAND_ROOM_WORKS.md      # THIS DOC
│   ├── VIEW_GENERATION.md             # How TIMELINE / DECISION_LOG / etc. regen
│   ├── RUNTIME_DEBUGGING.md           # Cowork-as-runtime-debugger templates
│   ├── RELEASE_MANIFEST.md            # (v3.4.5+) Manifest schema + writing contract
│   └── (more)
│
└── tests/                             # Python unit tests (pytest-style, run with `python tests/run_*.py`)
    ├── run_cru_match_test.py          # 63 tests as of v3.5.0
    ├── run_decision_match_test.py     # 16 tests
    ├── run_release_detectors_test.py  # 10 tests
    ├── run_chat_output_test.py
    └── (more)
```

### What lives in your workspace (the operator's machine)

```
<workspace-root>/                       # the folder the user mounted in Cowork — whatever they named it, wherever it lives on their machine
├── CLAUDE.md                           # Personal-instructions file Claude reads on every session
├── _hq/
│   ├── data/
│   │   ├── events.jsonl                # The event log
│   │   ├── entities.json               # People + orgs + projects
│   │   ├── aliases.json                # Name disambiguation
│   │   └── known-billing-domains.txt   # Optional override list for cr-inbox
│   ├── meetings/                       # Per-meeting prep briefs + transcripts
│   ├── briefings/                      # Per-day morning briefings
│   ├── intel/                          # Captured intel
│   ├── insights/                       # Weekly synthesis output
│   ├── operator-reports/               # Monthly operating-lift reports
│   ├── bugs/                           # (optional) Per-bug docs from inbound triage (the maintainer-side; chalette plugin owns the triage skill)
│   ├── views/                          # Auto-regen markdown views
│   │   ├── TIMELINE.md
│   │   ├── DECISION_LOG.md
│   │   ├── MASTER_TRACKER.md
│   │   ├── PEOPLE.md
│   │   └── RELATIONSHIPS.md
│   └── PICKUP_RITUAL_command_room.md   # Session-pickup guide for next chat
└── <per-project folders>/              # Auto-created when projects pass the threshold
    ├── SESSION_NOTES_<project>.md
    ├── deliverables/
    └── (more)
```

---

## Recurring bug classes to watch

Four classes have hit the plugin repeatedly in May 2026. Knowing them lets you triage future bugs in the right shape.

### 1. Writer/consumer split

A new feature ships that WRITES events to events.jsonl, but no view or skill is updated to READ those events. The feature appears to work (events land), but the user-facing surface never reflects them.

Examples: Sam 2026-05-17 dual-shape commitments (consumer side only handled 2 of 5 shapes); v3.4.5 decision closure shipped without DECISION_LOG view filter update (Theme 3 catch); `intel_logged` writer was missing despite TIMELINE expecting the events (v3.5.0 fix).

**Mitigation**: per `feedback_verify_consumers_before_ship.md` memory, the gate is "did I just ship a feature that writes events nobody reads?" Before every release, audit every consumer of any new event type + every field-name variant.

### 2. Canonical-path improvisation

The agent freelances around a canonical path when the canonical UX feels suboptimal. Examples: writing widget HTML to disk "for reopening later," narrating widget contents after `show_widget`, swapping to markdown when the widget feels too big, hand-rolling an artifact when `create_artifact` fails.

**Mitigation**: validators + leak-scanner + ZERO-MANIPULATION CONTRACT (v3.5.0+ extracted to `shared/STOP_CONTRACT.md`). Each release closes one bypass; agent finds the next one. Pattern, not point fixes.

### 3. Shape variance / data-field-name drift

The same logical field appears under multiple names in events written by different writers. `owner_id` vs `owner_person_id` vs `owner`. `due` vs `due_date`. `status` vs `state`. `confidence` as float vs `"HIGH"`/`"high"` string.

**Mitigation**: shared field-aware helpers (`cru_match._commitment_field` + alias chain). Consumers MUST use the helper. v3.5.0+ extends this to `event_references_person` for the Pulse cadence-detection class.

### 4. Confidence threshold inconsistency

The same word ("confidence threshold") used in multiple places with different scales. Commitments filter 0.7, past-meetings auto-commit 0.8, Pulse auto-apply 0.85, decision-CRU 0.65. Match scores from cru_match: 0.55/0.30.

**Mitigation**: v3.5.0+ `shared/scripts/confidence.py` consolidates into `CONFIDENCE_*` (extraction confidence) and `MATCH_SCORE_*` (CRU match scoring) named constants. Two semantic categories, one canonical source.

---

## Debugging + verifying

### Cowork-as-runtime-debugger

When something misfires in Cowork, the fastest diagnostic is: have Cowork dump the raw state it's working with. The Cowork sandbox has tools the customer wouldn't normally invoke. Templates at `references/RUNTIME_DEBUGGING.md`. Pattern:

```
Dump the current state of <X>. Output as fenced JSON / markdown. Don't summarize, don't explain — just print the raw state so the maintainer can debug.
```

Caught the v2.14.34 widget-wrapper-dropping bug after two days of misdiagnosis chasing CSS issues.

### Running tests locally

```bash
cd ~/repos/cr1-canonical/command-room/
python tests/run_cru_match_test.py
python tests/run_decision_match_test.py
python tests/run_release_detectors_test.py
python tests/run_chat_output_test.py
```

All should output `OK N tests passed`.

### Verifying a manifest detector against your own events.jsonl

```bash
cd ~/repos/cr1-canonical/command-room/
python shared/scripts/release_detectors/<detector_module>.py "<absolute path to your events.jsonl>"
```

Prints the `{applies, context}` JSON. Useful for previewing what the user would see before pushing a manifest item.

### Reporting a bug

Customers fire the `report-bug` skill (in this plugin). It diagnoses against the codebase, pattern-matches against known-issue patterns (about half of reports get a same-session fix without ever leaving the customer), and for the rest drafts a Gmail to `matthew@chaletteholdings.com` with the full diagnosis pre-filled.

### Triaging inbound bug reports (the maintainer-only)

The inbound triage skill (`process-bug-report`) was moved to the chalette internal plugin (`chaletteholdings/chalette` v0.5.0+) in v3.9.0. Customers never need it — they fire `report-bug` to send the bug; the maintainer fires `process bug report` in the chalette plugin to receive and triage it.

---

## Quick reference — trigger phrases by intent

| If you want to... | Type |
|---|---|
| See today's brief | `morning briefing` / `brief me` |
| Triage email | `triage my inbox` / `process my inbox` |
| Process a meeting | `process meeting` / `meeting notes` |
| Send follow-ups from a meeting | `follow up on the call` / `close the loop` |
| Prep for a specific meeting | `prep me for [meeting]` / `prep the call with [name]` |
| Get this week's recap | `weekly recap` / `summarize last week` |
| Keep the workspace tidy | `clean up my workspace` / `tidy up` |
| Look someone up | `who is [name]` / `tell me about [name]` |
| Prep a 1:1 | `prep for 1:1 with [name]` |
| Find dormant customers | `who went dark` / `dormant customer scan` |
| Draft an email | `draft an email to [recipient]` |
| Draft a memo | `write a memo on [topic]` |
| Draft a one-pager | `one-pager on [topic]` |
| Draft a decision memo (multi-option tradeoff) | `decision memo on [topic]` / `tradeoff analysis` |
| Build a board pack | `build the board pack for [date]` / `prep for the [date] board meeting` |
| Stress-test a plan | `stress test this plan` / `pre-mortem` |
| Schedule a meeting | `set up a 30-min with [name]` / `book time with [name]` |
| Review a contract | `review this contract` / `redline this NDA` |
| Make an introduction | `intro [A] to [B]` / `connect [A] and [B]` |
| Surface stale decisions | `what decisions should I revisit` |
| Surface stale conversations | `what conversations went silent` / `warm threads to revive` |
| Scaffold an automation (after scan) | `scaffold #N` / `scaffold the [opportunity] one` |
| Capture intel | Paste URL + say `intel` / `break this down` |
| Run a project | `new project` / `go [project name]` / `archive [project]` |
| List projects | `list projects` / `roster` |
| See your discuss list | `show my list` |
| Update the plugin | (UI: Personal Plugins → Update → restart Cowork) then `update command room` |
| Install missing dashboards | `install workspace map` / `install quick commands` |
| Set up scheduled chats | `set up command room schedules` |
| Change scheduled-chat timing | `change my schedule` / `move morning brief to 8 AM` |
| Report a bug to the maintainer | `report bug` / `something's wrong` |
| Pull in old context | `ingest context from [path]` / `import chatgpt` |
| File documents into projects | `file documents from [path]` / `sort my downloads` |
| See cost telemetry | `usage report` |
| See operating-lift report | `operator report` |
| Find automation opportunities | `automation scan` |
| Search transcripts for a topic | `what did anyone say about [topic]` |

---

## When this doc gets stale

Every release that adds a new skill, retires a skill, changes the daily-loop architecture, or adds a new shape variant should update the relevant section here. Treat it like CHANGELOG: append-style edits, version-tagged.

If you're touching the substrate (events.jsonl schema), the daily loop (any of the 6 orchestrators), or the release-manifest system — bump the section here in the same release. The doc's purpose is to be the one place the operator (you, future-the maintainer, or a teammate) can read to ground themselves quickly. Stale = useless.
