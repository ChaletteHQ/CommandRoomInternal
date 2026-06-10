# Command Room Plugin — Build Notes

This folder is the active build. Each release ships as its own pinned GitHub repo (`chaletteholdings/commandroom21XX`) so every install gets a clean cache. Prior parallel-build era (v1.8 markdown-first in `../plugin-source/`, v2.0 JSON-first here) collapsed into a single line at v2.2.0 — the v1.8 bundle is archived; all new work lands here.

For the current version and full release history see `CHANGELOG.md` and `.claude-plugin/plugin.json`. Major architectural rewrites since these notes were first written: v2.9.0 (dashboards retired, scheduled-task topic chats), v2.10.6 (chat-output deterministic Python renderer + validator), v2.13.0 (CONTRACT.md single-source-of-truth + hard-validated enforcement chain), v2.14.x (CRU layer, native connector parity, Workspace Map dashboard, plugin-root discovery deterministic).

## Data substrate (v2.0+)

The markdown registries that were sources of truth in v1.8 (`MASTER_TRACKER.md`, `PEOPLE.md`, `DECISION_LOG.md`, `ALIASES.md`) are **regenerated views** produced from four canonical JSON sources:

- `_hq/data/entities.json` — registry of people, projects, orgs. Stable ids (`person_042`, `project_008`, `org_acme_co`).
- `_hq/data/events.jsonl` — append-only log: meetings, decisions, commitments, interactions, status/scope changes, briefings, audits.
- `_hq/data/aliases.json` — raw string → canonical_id mappings.
- `_hq/data/classifier_feedback.jsonl` — CEO overrides from Pass 8 classification review (added v2.2).

Writer Contract (`shared/WORKSPACE_API.md`) is the authoritative spec for every write path.

## What changed at each version

- **v2.0.0** — introduced the JSON substrate, view regeneration, migration-v2 from v1.8 markdown.
- **v2.1.0** — PASSIVE_CAPTURE, VOICE_CALIBRATION, RELIABILITY contracts. Writer Contract headers on every writing skill.
- **v2.2.0** — Nested org tree (`parent_org_id`, `relationship_type`, `is_primary_focus`). Multi-project events (`primary_project_id` + `related_project_ids[]`). `classifier_feedback.jsonl` + insight-generator Pass 8 review loop. `migration-v2` extended for v1 → v2.2 upgrades.
- **v2.3.0** — `list-active` skill (zero-interaction project tree render). Insight-generator Pass 9 (auto-proposed projects from untracked cadence). Vocabulary revert: user-facing "thread" → "project" while schema fields (`primary_thread_id`, etc.) stay for backward compat.

Full release notes in `CHANGELOG.md`.

## Key files

- `shared/WORKSPACE_API.md` — Writer Contract (read before any write)
- `shared/PASSIVE_CAPTURE.md` — event-emission rules for connector reads
- `shared/VOICE_CALIBRATION.md` — draft voice calibration protocol
- `shared/RELIABILITY.md` — scheduled-task, connector, and write reliability contract
- `shared/FUZZY_ROUTER.md` — workspace-manager routing spec (primary-focus + side-stuff model)
- `shared/PLUGIN_BOUNDARY.md` — what the plugin may and may not do
- `shared/data-schemas/` — JSON Schemas + seed files for fresh workspaces
- `references/DATA_CONTRACT.md` — canonical storage spec
- `references/VIEW_GENERATION.md` — deterministic view regeneration templates
- `references/SKILL_SHAPE_V2.md` — required shape for every skill (Writer Contract, Reliability, Voice Calibration refs)
- `skills/migration-v2/` — v1 → v2.x converter (idempotent, backs up first)

## Installation

1. Build the `.plugin` bundle from this folder (zip contents, rename `.plugin`).
2. Install into Cowork.
3. Fresh workspace → `command-room-onboarding` seeds the v2.x substrate automatically.
4. Existing v1.8 workspace → say `migrate to v2` to invoke `migration-v2`.
5. Existing v2.0/v2.1 workspace → `migration-v2` also handles the nested-org + multi-project upgrade to v2.2+.

## Event types

Full payload schema in `shared/data-schemas/events.schema.json`. Current set:

`meeting`, `decision`, `commitment`, `commitment_resolved`, `interaction`, `status_change`, `scope_change`, `intel_logged`, `briefing`, `audit_run`, `onboarding_step`, `classification_override`, `project_proposal`, `note`, `other`.

## Portability

Language-agnostic substrate:

- JSON Schema for validation (any language with a JSON Schema library)
- JSONL for events (streaming parsers everywhere)
- Deterministic view templates (reproducible outside Claude)

Foundation for running Command Room on custom tooling or a future desktop app.

## Dev rules

- Never edit `_hq/views/*` by hand — regenerated from JSON sources.
- Always follow the Writer Contract (version-check, atomic append, conflict log).
- Never reuse entity ids. `person_042` means one person, forever.
- `events.jsonl` is append-only. Correct via `supersedes_seq`, never in-place mutation.
- `weekly-audit` validates everything. Run it often during development.
- Every writing skill declares a `## Writer Contract` header naming the files and event types it owns — no silent writes.
- Every scheduled-task skill declares a `## Reliability` header referencing `shared/RELIABILITY.md`.

## Testing

- `tests/run_trigger_test.py` — trigger phrase coverage across all skills (162 phrases at v2.3).
- `tests/triggers.yaml` — maintained alongside skill descriptions. Update both when descriptions change.

## See also

- `CHANGELOG.md` — full release history
- `README.md` — user-facing plugin description + skill roster
- `WORKSPACE_SCHEMA.md` — workspace folder layout and file ownership map
