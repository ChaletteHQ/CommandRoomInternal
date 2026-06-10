# Staging convention — `_hq/staging/` (RETIRED v3.12.0)

> **⚠ RETIRED.** This convention described an aspirational "deliverables-in-flight (Phase 2)" artifact that was never built. The actual shipping path for orchestrator-produced work products diverged:
>
> - **Email-shaped output** → Gmail Drafts (per `shared/EMAIL_DRAFT_PROTOCOL.md`), not labeled `cr-staged-*`.
> - **Doc-shaped output (briefs, memos, prep, follow-up packs)** → `_hq/meetings/` via `shared/scripts/brief_path.py::get_brief_path()` (per `CONTRACT.md` Rule 3 and `MD_DELIVERABLE_POLICY.md`).
> - **Other deliverables** → typed subfolders (`_hq/board-packs/`, `_hq/operator-reports/`, `_hq/dormant/`, `_hq/audit-reports/`, `_hq/insights/`) per `MD_DELIVERABLE_POLICY.md`.
> - **Sidebar items** → in-widget action surface (per `shared/CHAT_ACTION_WIDGET.md`).
>
> The `_hq/staging/[YYYY-MM-DD]/` path is now actively forbidden — the orchestrator output validator scans for it as a leak pattern (`shared/scripts/chat_output_renderer.py::_LEAK_PATTERNS`). Companion doc `PROVENANCE_FRONT_MATTER.md` is retired alongside this one.
>
> This file is kept (rather than deleted) so historical CHANGELOG entries that reference it still resolve. Do not follow this convention; do not build against it.

---

## Historical content (retired — for reference only)

The file/folder layout for work products produced by Command Room scheduled orchestrators (daily-morning-pack, EOD wrap, state-trigger fires, etc.). Reading this doc tells you where a staged item lives, what it's named, how to find it, when it expires, and why it was generated.

The convention is the contract between the orchestrators (which produce) and the human reviewer (M, eventually clients). Without it, you wake up to a folder of mystery files. With it, every file is greppable, sortable, debuggable, and disposable.

## Three shapes, three destinations

| Shape | Where it lives | Filename pattern | Why here |
|---|---|---|---|
| **Email-shaped** (drafts, replies, follow-ups, chase notes) | Gmail Drafts with label `cr-staged-[YYYY-MM-DD]` | Gmail's auto-naming | M already lives there, mobile access, sending is one tap |
| **Doc-shaped** (briefs, memos, prep, follow-up packs) | `_hq/staging/[YYYY-MM-DD]/` | `[skill]_[target]_[hhmm].[ext]` | Stays in workspace, version-controllable, doesn't clutter Drive |
| **Sidebar-only** (status flags, "review this", anomaly nudges) | Reference rows in deliverables-in-flight artifact (Phase 2 — not yet built) | n/a | Lightweight, no file needed; user dismisses or acts |

## Doc-shape filename pattern

Format: `[skill]_[target]_[hhmm].[ext]`

- `[skill]` — short skill identifier producing the file: `morning-brief`, `meeting-prep`, `eod-wrap`, `dormant-reengagement`, `followup-pack`
- `[target]` — what the file is ABOUT: project slug, person slug, meeting subject (kebab-cased, max 40 chars)
- `[hhmm]` — generation time in 24-hour local time, no separator
- `[ext]` — `md` for plain prose, `docx` for client-facing deliverables (use the `docx` skill)

Examples:
- `_hq/staging/2026-04-28/morning-brief_daily_0645.md`
- `_hq/staging/2026-04-28/meeting-prep_acme-q3-review_1100.md`
- `_hq/staging/2026-04-28/eod-wrap_friday-cleanup_1700.md`
- `_hq/staging/2026-04-28/dormant-reengagement_lauren-lcb_0648.md`

The first two segments give you what + about-what at a glance; the time gives you when. Sorting alphabetically inside the date folder gives you a roughly time-ordered list of the day's pack.

## Special subfolders inside `_hq/staging/[date]/`

- `_unrouted/` — items the orchestrator couldn't confidently route (project-mapping ambiguity, no entity match). Each file gets a banner at the top: `AMBIGUOUS ROUTE: <reason>. Pick or move.`
- `_rerun/[hhmm]/` — items from a `--force` re-run; prior-run files are NOT overwritten
- `_failed/` — items where a phase of the orchestrator errored before the file could be completed (so the partial work isn't lost)

## Gmail label format

**Use flat labels, not nested.** Cowork's Gmail MCP nested-label support is unverified as of v2.8.1 ship.

- Flat: `cr-staged-2026-04-28` ✓
- Nested (DO NOT use without testing): `CR-staged/2026-04-28` ✗

Each draft email staged by an orchestrator gets the label applied at draft creation. When the date rolls over, a new label is created automatically by the next orchestrator run (Gmail handles label-creation idempotency).

## Provenance front-matter (REQUIRED on every doc-shape file)

Every `.md` file staged to `_hq/staging/[date]/` MUST start with the 5-line YAML front-matter defined in [`PROVENANCE_FRONT_MATTER.md`](./PROVENANCE_FRONT_MATTER.md). Without it, the file is debt — there's no way to trace why it was generated, what fired it, or what to do with it. Front-matter-less files dropped into staging are treated as orphans by the dismiss-without-stigma sweeper (auto-archive after 24h instead of 48h).

## Retention + auto-archive

- **Default TTL: 48 hours.** After 48 hours, files in `_hq/staging/[YYYY-MM-DD]/` are moved to `_hq/staging/_archive/[YYYY-MM-DD]/` by the next orchestrator run.
- **No deletion.** Auto-archive moves; never deletes. M can mine old archives for "things I systematically dismissed" patterns later.
- **Outcomes log persists forever.** `_hq/data/staging_outcomes.jsonl` (one line per staged item: `{ts, file_path, outcome: "used"|"dismissed"|"archived"|"unknown", review_age_hours}`) is small, append-only, and never purged. This is the gold for tuning ("which trigger has 70% review rate vs 5% review rate").

## Gmail Drafts retention

Gmail handles its own label/draft retention. Drafts staged by orchestrators with the `cr-staged-[date]` label stay in Gmail's drafts indefinitely until M sends, archives, or deletes them. No auto-archive sweep on Gmail-side — that's M's call.

## Dismiss-without-stigma model

M reviewing a staged item = engagement. M ignoring it for 48h = signal to the system that this combination of (trigger, target, skill) didn't produce a useful output for this context. Both outcomes log to `_hq/data/staging_outcomes.jsonl`. **There are no "did you skip this?" prompts, no nag toasts, no nag emails.** The system learns silently from what gets used vs. what gets archived.

When the deliverables-in-flight artifact ships (Phase 2 candidate, v2.8.2+), it'll surface a "X of Y staged items used in past 7 days" calibration footer. When that number drops below a threshold (TBD — likely 30%), the system flags itself as producing too much junk and the operator (M, or the bridge) tunes triggers down.

## Greenfield infrastructure that v2.8.1 introduces

These don't exist yet; v2.8.1 creates them:
- `_hq/staging/` directory (empty until first orchestrator fires)
- `_hq/staging/_archive/` directory (empty until first sweep)
- `_hq/data/staging_outcomes.jsonl` (append-only, written by the deliverables-in-flight artifact when M acts on a staged item; written by the auto-archiver when items expire)
- `_hq/data/staging_emissions.jsonl` (append-only, written by EVERY orchestrator on every staged item: `{ts, trigger, target, skill, output_path, ttl}`. State-watcher uses this for cooldown gates)
- `_hq/data/.state_watcher_cursor` (single-line file: last seq processed by Phase 3's state-watcher; not used in v2.8.1, will exist as empty file)

## Why the convention matters more than the convention itself

Any reasonable file/folder layout works. What matters is that the layout is **enforced** — every orchestrator follows it, every consumer (artifact, archiver, outcomes-tracker) reads it. The discipline is what makes the system debuggable. Without enforcement, you get drift: meeting-prep saved as `prep_for_acme.md` here, `meeting_acme_phase1.md` there, `prep_q3review.docx` somewhere else. Becomes impossible to query "show me everything generated for the Acme thread last week."

**Enforcement mechanism:** every orchestrator that stages a doc-shape file MUST include the staging convention as part of its prompt. The convention's filename pattern, front-matter, and destination path are non-negotiable. Cowork agents that fire scheduled tasks should fail loudly (write `staging_convention_violation` event to events.jsonl) if they detect deviation.
