# Install Validation Checklist — v3.12.0

**Purpose:** Verify that a fresh Command Room plugin install landed correctly on a customer's machine, before they start building real workspace state on top of a broken foundation. Run this immediately after install — or any time a cleanup flags "install incomplete."

**Ownership:** `command-room-onboarding` runs this automatically on first session. `cleanup` re-runs checks 1–6 every Sunday. Can be invoked manually with "validate my install" or "check my command room install."

**Outcome:** Every check returns ✅ (pass) or ❌ (fail with one-sentence reason). Any ❌ blocks the onboarding wizard's "you're done" screen.

**v3.12.0 refresh:** This file was refreshed from v2.1. The v2.1 version referenced retired concepts (`type: "home"` org field replaced by `is_primary_focus` in v2.2; legacy `morning-briefing` task name replaced by `morning-brief` scheduled task; outdated default times; references to the retired shared `VOICE_SAMPLES.md`).

---

## 1. Plugin file integrity

Validate the plugin source arrived intact.

- [ ] `.claude-plugin/plugin.json` exists and parses as valid JSON.
- [ ] `.claude-plugin/plugin.json` declares the plugin version, name `command-room`, and a `python_dependencies` list including `python-docx`.
- [ ] Every skill folder at `skills/[name]/` contains a `SKILL.md` with valid frontmatter (`name:` matches the folder; `description:` ≥ 80 chars).
- [ ] `shared/` directory contains: `WORKSPACE_API.md`, `PASSIVE_CAPTURE.md`, `VOICE_CALIBRATION.md`, `PLUGIN_BOUNDARY.md`, `RELIABILITY.md`, `CONTRACT.md`, `COMMITMENT_SCHEMA.md`, and `data-schemas/`.
- [ ] `shared/data-schemas/` contains `entities.schema.json`, `events.schema.json`, `aliases.schema.json`, and a `seed/` subdirectory.
- [ ] `shared/scripts/` contains the canonical helpers: `atomic_write.py`, `cru_match.py`, `tz.py`, `brief_writer.py`, `brief_path.py`, `chat_output_renderer.py`, `people_writer.py`, `telemetry.py`, `tool_discovery.py`, `schedule_config.py`.
- [ ] `references/` contains `SOURCE_OF_TRUTH.md`, `DATA_CONTRACT.md`, `VIEW_GENERATION.md`, `MD_DELIVERABLE_POLICY.md`.

**Fail behavior:** If any of these are missing, stop. Tell the user: "Plugin source looks incomplete — reinstall from the marketplace."

---

## 2. Workspace scaffolding

Validate the customer workspace has the minimum structure.

- [ ] `[WORKSPACE_ROOT]/_hq/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/data/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/data/entities.json` exists and parses as valid JSON.
- [ ] `[WORKSPACE_ROOT]/_hq/data/events.jsonl` exists (may be empty file on fresh install).
- [ ] `[WORKSPACE_ROOT]/_hq/data/aliases.json` exists and parses.
- [ ] `[WORKSPACE_ROOT]/_hq/views/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/briefings/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/insights/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/audit-reports/` directory exists.
- [ ] `[WORKSPACE_ROOT]/_hq/meetings/` directory exists (the canonical deliverable destination — `Call_Prep_*.docx`, `Past_Meeting_*.docx`, `Weekly_Recap_*.docx`).
- [ ] `[WORKSPACE_ROOT]/_hq/logs/` directory exists.
- [ ] `[WORKSPACE_ROOT]/CLAUDE.md` exists with at least the baseline workspace brief.
- [ ] `[WORKSPACE_ROOT]/_hq/BUSINESS_CONTEXT.md` exists (may be a template on fresh install).

**Fail behavior:** Trigger `command-room-onboarding` to seed missing pieces. Do not attempt to run any other skill until complete.

---

## 3. Schema validity

Validate the seeded JSON matches the schemas.

- [ ] `entities.json` validates against `shared/data-schemas/entities.schema.json` — top-level `entities.{people, threads, orgs}` arrays present.
- [ ] `events.jsonl` — every line (if any) validates against `shared/data-schemas/events.schema.json`. Every `type:` value is in the enum **or** is a documented pre-registry fossil (`shared/scripts/event_types.py::PRE_REGISTRY_FOSSILS` / `is_pre_registry_fossil()`). An unregistered type that is NOT on that list is the defect this check is looking for. Corrected 2026-07-25: a migrated workspace legitimately carries ~50 fossil types written before the append gate went strict on 2026-07-02 — reading the bare enum check literally flags a healthy install. See `shared/EVENT_TYPES.md` § Pre-registry fossils.
- [ ] `aliases.json` validates against `shared/data-schemas/aliases.schema.json`.
- [ ] Every reference in entities.json (owner_person_id, reports_to_id, affiliation_id, parent_org_id) resolves to an existing record.
- [ ] Every canonical_id in aliases.json resolves.

**Fail behavior:** Log to `_hq/CONFLICTS.md` with type `schema-violation`. Offer to restore from `shared/data-schemas/seed/` if the customer hasn't entered data yet.

---

## 4. Primary-focus org + primary user identity (v2.2+ org tree)

Validate the customer has identified themselves and at least one primary-focus org. The v1.8 `type: "home"` field was retired in v2.2; the canonical signal is now `is_primary_focus: true` on org records (per `references/DATA_CONTRACT.md` and `references/ORG_AND_THREAD_MODEL.md`).

- [ ] `entities.json` has at least one `org` record with `is_primary_focus: true` (no parent_org_id, OR holding-level org with operating-children that carry is_primary_focus).
- [ ] `entities.json` has at least one `person` record flagged as the primary user (`is_primary_user: true`).
- [ ] The primary user's `workspace.user_timezone` is set in `entities.json` (required for `shared/scripts/tz.py` per the v3.11.3 contract — no silent UTC fallback).
- [ ] `BUSINESS_CONTEXT.md` has a filled-in "Primary focus" section (not a template placeholder).
- [ ] `BUSINESS_CONTEXT.md` has a "User" / "CEO name" or equivalent identity section.

**Fail behavior:** Jump to the onboarding identity prompt flow. This is foundational — nothing else works without it.

---

## 5. Plugin boundary cleanliness

Validate no customer data has landed in plugin source (per `PLUGIN_BOUNDARY.md`).

- [ ] Grep plugin source for any canonical person name in `entities.json` — zero hits (the structural guard `tests/run_no_real_customer_names_test.py` enforces this on every release).
- [ ] Grep plugin source for any org canonical_name in `entities.json` — zero hits.
- [ ] Grep plugin source for any project name in `entities.json` — zero hits.
- [ ] No files under plugin source modified by the customer (mtime since install).
- [ ] No caches, logs, or connector output files under plugin source.

**Fail behavior:** Log to `_hq/CONFLICTS.md` with type `boundary-violation` — HIGHEST severity. Block plugin auto-update until resolved.

---

## 6. Connector connectivity (optional but recommended)

Validate each configured connector is reachable. Skip cleanly if the customer hasn't set them up yet.

- [ ] Mail (Gmail or Outlook): `whoami` equivalent succeeds within 15s timeout (per `shared/RELIABILITY.md` connector budget).
- [ ] Calendar (Google or Outlook): `list_calendars` returns at least one calendar.
- [ ] Slack (or Teams): `whoami` equivalent succeeds.
- [ ] Transcript source (Granola / Fireflies / Otter / Read.ai / Zoom AI / Teams summaries): per-connector availability check.
- [ ] Drive (Google Drive / OneDrive / SharePoint): `list_recent_files` returns.

**Fail behavior:** Surface each failed connector with the one-line message: "⚠️ [Connector] — [not connected / auth expired / timeout]. Reconnect in Cowork settings." Not blocking — the customer can proceed with what's working.

---

## 7. Skill roster sanity

Validate the customer sees the expected skills.

- [ ] All ~46 skills in `skills/` appear in the customer's `available_skills` list.
- [ ] No leftover deprecated skill names (the structural guard `tests/run_no_retired_skills_test.py` enforces this on every release).
- [ ] `workspace-manager`, `morning-briefing`, `cleanup`, and `command-room-onboarding` are present (minimum viable roster).

**Fail behavior:** Log which skills are missing. Prompt the customer to reinstall the plugin.

---

## 8. Scheduled tasks (if the customer opted in)

Validate the recommended scheduled tasks are registered. Defaults per `shared/scripts/schedule_config.py::DEFAULT_SCHEDULES`. First-install workspaces register the 4 day-one tasks in `FIRST_INSTALL_TASK_IDS`; the others are added later in follow-up sessions.

- [ ] `morning-brief`: scheduled for weekday 7:00 AM (or customer-configured time).
- [ ] `upcoming-meetings`: scheduled for weekday 6:30 AM.
- [ ] `past-meetings`: scheduled for weekday 5:00 PM.
- [ ] `friday-wrap`: scheduled for Friday 4:00 PM (first weekly-rhythm task, added in v3.11.0).
- [ ] If added in a follow-up session: `inbox` at 7:15 AM, `commitments` at 8:30 AM, `pulse` at 9:00 AM.
- [ ] `cleanup`: optional — Sunday.
- [ ] `insight-generator`: optional — Sunday 19:00 (or later).

**Fail behavior:** Not blocking, but prompt: "Want me to set up the default scheduled tasks?"

---

## 9. First-session readiness

Final gate — the customer is ready to actually use the system.

- [ ] Checks 1–5 all pass.
- [ ] At least one `thread` record exists in entities.json (even if just a placeholder during onboarding).
- [ ] `_hq/voice/` directory exists. Per-skill voice correction logs (`corrections-<skill>.jsonl`) start empty and populate as the user gives feedback per `shared/VOICE_CALIBRATION.md`. The shared `VOICE_SAMPLES.md` model was retired in v3.0; do not check for it.
- [ ] `PASSIVE_CAPTURE_OPTOUT.md` file exists (may be empty; customer can add opt-outs later).

---

## Report Format

Validation output is a single scannable block:

```
COMMAND ROOM INSTALL VALIDATION — [DATE]

1. Plugin file integrity         ✅
2. Workspace scaffolding         ✅
3. Schema validity               ✅
4. Primary-focus org + user      ⚠️  Primary user identity not set — run onboarding
5. Plugin boundary cleanliness   ✅
6. Connector connectivity        ⚠️  Slack not connected (optional)
7. Skill roster sanity           ✅
8. Scheduled tasks               ⚠️  Not configured — want to set up?
9. First-session readiness       ❌  Blocked by #4

RESULT: Install incomplete. Run `command-room-onboarding` to finish.
```

Overall verdict is one of:
- **Install complete** — all blocking checks pass, optional warnings may exist.
- **Install incomplete** — one or more blocking checks fail (1, 2, 3, 4, 5, 7, 9).
- **Install broken** — plugin source is corrupt (check 1 fails hard).

---

## When to Run

- Automatically on first session after install (via `command-room-onboarding`).
- Every Sunday as part of `cleanup`.
- On demand when the user says "validate my install" / "check my command room install" / "is my setup right?"
- After any plugin version bump.

---

**End of install validation checklist.**
