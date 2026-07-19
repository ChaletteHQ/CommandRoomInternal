---
name: cleanup
description: "Weekly self-maintenance. Tidies the workspace without the CEO's attention — runs Sunday night, auto-fixes what's safe, repairs damaged records, leaves a short Monday note only if something needs eyes. Triggers on 'weekly cleanup', 'clean up my workspace', 'clean up the workspace', 'tidy up', 'deep clean' (the full pass) — and 'maintenance', 'run my maintenance', 'run maintenance now' (the background-maintenance manual fire — run what's due or report nothing due; Step 0, NOT a cleanup pass). (Bare 'clean up [a thing]' — an email, a doc, a list — is NOT this skill; only workspace-shaped cleanup fires it.) Also catches retired phrases 'weekly audit', 'system review', 'scan everything'. DOES NOT fire on 'weekly recap' / 'what happened this week' (that's weekly-recap), 'level up command room' (opt-in add-ons), or 'health check' / 'system health' / 'is everything running' (that's system-health — the scheduled-task watchdog)."
---

# Cleanup

One skill, one job: keep the customer's workspace tidy every week without requiring their attention. Runs weekly as a job inside the `maintenance` background task (MAINT1 — due at the Sunday 5:45 PM fire, and still due at the next fire if the computer was closed: `maintenance_dispatcher.due_jobs` self-heals missed weeks), fixes what it safely can, heals corruption, and leaves a short plain-English Monday-morning note for anything that needs the CEO's eyes. Replaces the retired `weekly-audit`.

The shift from the old audit: cleanup does NOT hand the user a score, a dashboard, or a "want me to fix these?" prompt. For a non-technical CEO the answer to "should I fix this?" is always yes, so cleanup just does the safe fixes and surfaces only genuine judgment calls.

## Skill Boundary (v2.1)

- **Use cleanup for:** the weekly (or on-demand) workspace maintenance pass — safe auto-fixes, substrate self-heal, and the short Monday note. Retired audit phrases ("weekly audit", "system review", "scan everything") redirect here.
- **Use `weekly-recap` for:** "weekly recap" / "what happened this week" — the week-in-review narrative, not maintenance.
- **Use `system-health` for:** "health check" / "system health" / "is everything running" — the scheduled-task watchdog (moved out of cleanup in Phase 3/W1).
- **Use `level-up-command-room` for:** "level up command room" — the opt-in add-ons menu.
- **Does NOT fire on** bare "clean up [a thing]" (an email, a doc, a list) — only workspace-shaped cleanup fires it.
- **Maintenance-shaped phrases run Step 0 below, NOT a cleanup pass** — "run my maintenance", "run maintenance now", "run maintenance", bare "maintenance" (EW2+T, F-14).

## Step 0 — Manual maintenance dispatch (EW2+T, F-14; runs INSTEAD of the phases below)

Post-MAINT1, the bridge and the install ritual teach the customer they have a "Maintenance" background task — so the natural phrases "run my maintenance" / "run maintenance now" / bare "maintenance" mean **fire that task's due-jobs engine now**, not "run a full workspace cleanup" (the live D8 fire ran a duplicate cleanup pass instead; harmless but not what was asked). When the firing phrase is maintenance-shaped:

1. Resolve the workspace + plugin root per CONTRACT Rule 22 (multiple plugins may be installed — filter the plugin_* candidates by this plugin's name, the MAINT-RUN discovery wobble).
2. Ask the dispatcher what is due — NEVER judge due-ness yourself: `python3 shared/scripts/maintenance_dispatcher.py <workspace_root>` and hold its JSON plan (`due` is ordered; the order is the contract).
3. **Nothing due →** one honest plain-English line built from the plan — *"Nothing's due — everything ran on schedule. Next up: [job] at [its next slot]."* — and STOP. Write NO `maintenance_run` receipt for a nothing-due manual poke: the watchdog reads `maintenance_run` for task freshness, and an empty manual receipt could mask a broken scheduled task.
4. **Jobs due →** execute each due job's skill END-TO-END in plan order, one at a time, never in parallel — identical rules to the registered task prompt: a job COMPLETED only when its OWN receipt validator confirms its substrate receipt; an unreceipted job goes in jobs_failed and stays due (self-healing). Then finish with ONE `maintenance_dispatcher.maintenance_receipt(workspace_root, jobs_due=…, jobs_completed=…, jobs_failed=…, skipped_disabled=…, fired_via="manual")` and confirm it landed via `validate_maintenance_ran`.
5. Chat output: one plain-English line per job that ran ("Reconciled your sent mail — closed 1, opened 2." / "Weekly cleanup done — the Monday note has 2 items."), plus each job's own must-surface lines per its SKILL.md. No event-type names, no receipts narration.

Cleanup-shaped phrases ("weekly cleanup", "clean up my workspace", "clean up the workspace", "tidy up", "deep clean", "scan everything", "weekly audit", "system review") run the full pass below, exactly as before. When cleanup runs AS a job inside a maintenance fire (scheduled or Step-0 manual), it starts at Phase 1 directly — Step 0 is the entry router for the chat phrase only.

## Personification Contract (v3.13.8.4+)

Before surfacing the summary or composing the `.docx` report, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The chat summary intro uses the shape `"Cleanup done, {first_name} — {brain_name} tidied up {N} things this weekend."` The `.docx` report (when generated) opens with the same author line in the header. Default `{brain_name}` = `"Penelope"`.

## Writer Contract

- **Primary writer for** `_hq/cleanup-reports/[YYYY-MM-DD]-cleanup.docx` (via `shared/scripts/brief_writer.py` per CONTRACT Rule 27 — no .md deliverables).
- **Appender** for `cleanup_run` events to `_hq/data/events.jsonl` and for `_hq/CONFLICTS.md`.
- **Auto-fix writer** for the safe maintenance actions in Phase 2 and the safe integrity remediations in Phase 3 (see those phases for the exact, bounded write set).
- **Brain Live State renderer (v3.17.0+)** — re-renders each active project's `PROJECT_BRAIN` Live State block via the canonical `render_thread_live_state` / `render_brain_block` helpers (marked-region write, byte-preserves hand-written content) and runs the idempotent one-time brain migration (Phase 3.5). Never hand-edits a brain; only the helper touches the marked block.
- **Derived-view + scaffold + lock writer (bounded, v3.19.x / SPEC CLEAN1)** — regenerates the views it owns from the substrate (`_hq/views/PEOPLE.md` via Phase 3.5c; `_hq/views/DECISION_LOG.md` via the **changed-only** `render_decision_log.regenerate_if_changed`, Phase 3.5d), scaffolds a **missing** `SESSION_NOTES_[NAME].md` (Phase 3c / D3 — never overwrites one that exists), and **archives** `*.lock.stale.*` sentinels older than 1 hour into `_archive/stale-locks/` (Phase 2 Rule 9 / D6 — moved, never deleted). Every one of these is idempotent: re-running on a clean workspace writes nothing.
- **Living Brain expiry sweep (SPEC LB1, Phase 3j)** — appends the silent `brain_proposal_expired` tombstones for open proposals past their TTL, ONLY via `brain_proposals.expire_stale()` (the canonical sweep helper — never a hand-rolled append). Idempotent: nothing stale → nothing written.
- **NEVER writes:** `entities.json` (except via the owner skills' helpers it delegates to), `events.jsonl` other than appending one `cleanup_run` event (+ the `corruption_recovery` event that `recover_corruption.py` appends on its own, + the Phase 3j expiry tombstones via `brain_proposals.expire_stale`), `aliases.json`, the **analytical views** (`RELATIONSHIPS`/`TIMELINE`/`COMMITMENT_AGING`/`DORMANT`/`THEMES` — insight-generator owns those; cleanup only flags them stale) or `_hq/views/ALIASES.md` (people-crm owns it — flag only), and `classifier_feedback.jsonl`. Never deletes a user's folders or files; orphan folders are FLAGGED, not removed. Canonical entity mutation stays with the owner skills (workspace-manager / project-manager / people-crm).

## Substrate validated every run

Every run validates the v2.2 data substrate per `references/DATA_CONTRACT.md` and the JSON Schemas in `shared/data-schemas/`. The deterministic checker (Phase 3) is the executable backstop; the prose contract in DATA_CONTRACT.md remains the spec.

---

## Phase 1: Silent Scan (no questions)

Scan the entire workspace before doing anything. Resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble (find `_hq/` under the mount). Projects live at `[WORKSPACE_ROOT]/[Project Name]/` (root level); infrastructure folders carry a `_` prefix.

### 1.0 — Deterministic structural scan (CODE, runs first, EVERY fire)

The structural folder↔thread reconciliation runs as **code, not prose** (SPEC CLEAN1 / D1) — five real weekly runs reported "clean" while six hygiene classes accumulated because the scan below used to be prose the model skipped under load. This block executes on **every** cleanup fire (weekly included — NOT deep-clean-only, D2). Capture its findings and carry them into Phase 3 remediation + the Phase 4 Monday note.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic
ws = '<workspace_root>'
findings = ic.scan_project_structure(ws)
out = {'orphan_folder': [], 'missing_brain': [], 'missing_session_notes': []}
for f in findings:
    if f.check == 'C10.orphan_folder': out['orphan_folder'].append(f.subject)
    elif f.check == 'C11.missing_brain': out['missing_brain'].append(f.subject)
    elif f.check == 'C11b.missing_session_notes': out['missing_session_notes'].append(f.subject)
print(json.dumps(out))
"
```

- `orphan_folder` → **FLAG only** in the Monday note (never delete — see Phase 3d). On a live client workspace these are almost always hand-made folders the CEO created on purpose.
- `missing_brain` → feeds Phase 3c brain backfill (Rule 15).
- `missing_session_notes` → feeds Phase 3c session-notes backfill (Rule 16, D3).

The prose checks 1a–1j below remain the broader, judgment-driven sweep layered on top of this deterministic core — they are NOT a substitute for it.

- **1a. Project health** — for each project folder: PROJECT_CONTEXT.md present + last-modified; SESSION_NOTES_[NAME].md present + most-recent entry; loose files; structure complete. Cross-reference stage in MASTER_TRACKER against file activity.
- **1b. Master tracker integrity** — every tracked project has a folder (or exploring notes); every folder has a tracker entry; phantom entries (active but folder missing); orphan folders (folder, no entry); plausible "Last Touched" dates; overdue commitments; aging Inbox items (30+ days).
- **1c. HQ infrastructure** — BUSINESS_CONTEXT.md / MASTER_TRACKER.md currency; temp/.old/stale artifacts.
- **1d. Session-notes freshness** — most recent entry per active project; flag active projects past their staleness threshold.
- **1e. Skills health** — outdated path/config references; descriptions accurate; long-unused skills.
- **1f. Intel system (optional)** — only if `_hq/intel/` exists; else skip entirely.
- **1g. File size & bloat** — against WORKSPACE_SCHEMA.md targets: SESSION_NOTES >150 lines, PROJECT_BRAIN >4KB, PERSON files >3KB, MASTER_TRACKER >2KB, `_hq/briefings/` >30, `_people/prep/` >20, `_hq/cleanup-reports/` >12, any .md >10KB.
- **1h. Team health (if `_people/` exists)** — else skip entirely. Last interaction, open/overdue commitments per member; flag 14+ days silent, 3+ overdue, profiles >30 days stale.
- **1i. Content accuracy** — cross-reference docs vs recent session notes to find drift (stale contexts, dormant "active" projects, undocumented people/decisions).
- **1j. Prospects that look converted (Bug #92 — detect-and-nudge, NEVER auto-flip).** Run `shared/scripts/prospect_conversion_detector.py::detect_prospect_conversion_candidates(workspace_root)`. For any candidate, add a line to the Monday note's "worth a glance" tier — *"[Name] looks like a client now ([reason]) — say `[Name] is now a client` to convert."* This is the weekly backstop for the real-time coach nudge. Cleanup does NOT change `relationship_type` itself — it only surfaces the suggestion; the CEO runs the Bug #91 conversion.
- **1k. Commitment write-contract violations (Phase 2 Stage D, S4 — flag-only).** `integrity_check.run_checks` now emits `C17.cleanup_keys` (any event carrying `_cleanup_*` keys — the signature of a hand-rolled in-place edit) and `C17.inplace_status` (commitment events with a closed-family `data.status`; legacy rows read fine forever, but GROWTH week-over-week means an active F4 mutation writer). Surface both in the Monday note as contract violations in plain English (*"something edited your activity log the unsafe way this week — nothing lost, but worth flagging"*). Cleanup NEVER rewrites the rows itself — F4 applies to cleanup too; commitment closure is `close_commitment()` appends only.

## Phase 2: Auto-Fix Sweep (does it, doesn't ask)

On the FIRST cleanup run per workspace only (track via `entities.json` `workspace.cleanup_intro_shown: true`), print this one-paragraph reassurance so the user understands what's about to happen. Subsequent runs skip it and just do the work:

```
Running cleanup. Quick note on what this does: I never delete anything.
Old **caches** — briefings, prep briefs, reports — get moved into an
`_archive` folder so your working space stays tidy, but they're still there
if you ever want them. Your **memory** — session notes, decisions,
commitments, interaction logs — is compressed when it gets old but kept
forever. If you ever ask "what has [person] delivered this year," the answer
is still there.
```

Then run all automatic maintenance rules from `workspace-manager/references/maintenance-rules.md` WITHOUT asking:

1. **Briefing archival** (Rule 4): briefings older than 30 days → move to `_archive/briefings/` — CACHE (archived, never deleted)
2. **Report archival** (Rule 5): keep only 12 most recent cleanup reports → move older ones to `_archive/cleanup-reports/` — CACHE (archived, never deleted)
3. **Prep file archival** (Rule 6): prep files older than 14 days → move to `_archive/people-prep/` — CACHE (archived, never deleted)
4. **Session-notes rollover** (Rule 1): SESSION_NOTES over 150 lines → archive older entries to `SESSION_NOTES_[NAME]_archive_[YYYY].md` — MEMORY (archived, never deleted)
5. **Brain-thread pruning** (Rule 2): compress resolved threads >30 days to a Thread-History one-liner — MEMORY (compressed)
6. **Commitment archival** (Rule 3): compress delivered commitments >60 days to a Commitment-History one-liner — MEMORY (compressed)
7. **Tracker hygiene** (Rule 8): move old Completed Quick Tasks / Recently Archived entries into the tracker's own archive section — STALE POINTERS (archived in place, never deleted; project archive folders remain)
8. **Interaction-log tiered compression** (Rule 7): Tier 1 (0–90d) full, Tier 2 (90d–6mo) one-liners, Tier 3 (6mo–1yr) monthly digests, Tier 4 (1yr+) archived — MEMORY (compressed)
9. **Stale lock-file archival** (D6): move `_hq/data/*.lock.stale.*` and `_hq/.system/*.lock.stale.*` sentinels older than **1 hour** into `_archive/stale-locks/` — STALE LOCKS (not memory, not caches; moved, never deleted). Run the code block below; record the count into `actions_taken[]`. A 1-hour floor means a writer that's still mid-recovery is never disturbed; the docstring on `atomic_write.py` long claimed "weekly Tidy Up cleans them" but nothing did — this is the implementation.

```bash
python3 -c "
import sys; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
archived = ca.sweep_stale_locks('<workspace_root>')  # >1h-old only; archive-move, never touches fresh locks
print('archived', len(archived), 'stale lock files')
"
```

> **A1 coordination (reconciled — A1 shipped second):** SPEC A1's stale-lock sweep requirement is already satisfied by `cleanup_actions.sweep_stale_locks` above (covers `_hq/data/` + `_hq/.system/`, >1h floor) — A1 added **no** duplicate sweep. A1 adds ONLY the contention-reporting step below. Exactly one owner of the stale-lock sweep: this Rule 9.

10. **events-lock contention report** (A1): the events.jsonl writer lock records best-effort contention counters in `_hq/.system/lock_stats.json` (only when a wait exceeded 100ms — a quiet workspace has no file). Read it, fold a plain-English line into the Monday note (Beat 1), then **reset** it so each week's report covers just that week. This is read-report-reset, NOT a sweep. Run the code block below; record nothing into `actions_taken[]` (reporting only).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from pathlib import Path
from atomic_write import atomic_write_json
p = Path('_hq/.system/lock_stats.json')
if p.exists():
    try: s = json.loads(p.read_text(encoding='utf-8'))
    except Exception: s = {}
    waits = s.get('waits', 0); timeouts = s.get('timeouts', 0)
    fb = s.get('fallback_sentinel_acquires', 0)
    print(f'events lock contention this week: {waits} waits, {timeouts} timeouts, {fb} sentinel fallbacks')
    atomic_write_json(p, {})  # reset for next week
else:
    print('events lock contention this week: none (no waits over 100ms)')
"
```

These rules are non-destructive by design — **nothing is ever deleted.** Memory is compressed/archived in place; caches and >1h-stale locks are MOVED into `_archive/` (never a user's folders or files). Record each action taken into `actions_taken[]` for the `cleanup_run` event.

## Phase 3: Substrate Integrity (detect → remediate)

This is the self-healing core. Two steps: a read-only **inspector** finds problems, then cleanup **remediates** the safe ones.

### 3a. Run the inspector (read-only DETECT)

```
python3 shared/scripts/integrity_check.py <workspace_root> --json
```

Returns structured findings with severity ERROR / WARN / INFO across ~13 referential checks: missing/malformed ids, unresolved affiliations, org/thread parent cycles, engagement endpoints, person↔org/thread link symmetry, dangling event references (test-residue detector), dead aliases, orphan folders, thread `folder_name` missing on disk, missing PROJECT_BRAIN, and **duplicate event seq**. The checker NEVER fixes — it only reports. Fold its findings in; do not re-derive them by hand.

### 3a-bis. Lint skill settings (read-only DETECT — settings-layer C4)

```
python3 -c "import sys,json; sys.path.insert(0,'shared/scripts'); \
from skill_config_writer import lint_skill_configs; \
print(json.dumps(lint_skill_configs('<workspace_root>')))"
```

Returns `{skill: [dangling keys]}` for any skill whose saved settings carry a key that
is no longer in `shared/data-schemas/skill_config.schema.json` (a deprecated key left
behind after a knob rename, or drift). Read-only — cleanup never edits a saved setting
(FRP1 precedent: read-only on prefs). A non-empty result is surfaced in the Monday note's
"a few things" tier in plain English ("One of your saved settings is from an older version
— I can clear it next time we update") and is the signal that a release-manifest migration
should heal it. An empty result (the common case) is silent.

### 3b. Heal corruption (REMEDIATE — safe, automatic)

Malformed lines in `events.jsonl` (e.g. a sync hiccup that wrote half a record) are healed automatically every run via the **recurring** self-heal:

```
python3 shared/scripts/recover_corruption.py <workspace_root> --recurring
```

`--recurring` triggers on "is anything broken right now?" (not once-per-version), so it catches drift that accumulates between upgrades. It quarantines only the malformed lines to `_hq/.system/quarantine/` (saved, never deleted), rewrites events.jsonl without them (atomic), appends a `corruption_recovery` event, and returns a friendly `customer_message`. A clean file is a fast no-op (nothing written). Surface its `customer_message` in the Monday note only when it actually healed something.

### 3c. Auto-fix the safe referential findings

For inspector findings that are unambiguous and non-destructive, fix them and record into `actions_taken[]`:
- **Phantom tracker entry** (tracker row, no folder) → remove the stale pointer (note it).
- **Missing PROJECT_BRAIN on a real project folder** (`C11.missing_brain`) → backfill a scaffold brain from session notes (maintenance-rules Rule 15).
- **Missing SESSION_NOTES on a real project folder** (`C11b.missing_session_notes`, D3) → scaffold a session-notes file from `references/session-notes-template.md` via the helper below. **NEVER overwrites an existing notes file** — the helper refuses if any `SESSION_NOTES*.md` already exists, so a client's real notes are safe. Mirrors the brain backfill (Rule 16 in maintenance-rules).
- **Dead alias** pointing at an archived/nonexistent entity → prune the alias.
- **Stale PROJECT_CONTEXT** → regenerate from session notes where the source is clearly present.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import integrity_check as ic, cleanup_actions as ca
ws = '<workspace_root>'
created = []
for f in ic.scan_project_structure(ws):
    if f.check == 'C11b.missing_session_notes':
        path = ca.backfill_session_notes(ws, f.subject)  # None if notes already exist (never overwrites)
        if path: created.append(path)
print(json.dumps({'session_notes_backfilled': created}))
"
```

> **Orphan folders are FLAGGED, not moved** (revised for client safety, D1). On the 5 live client workspaces, a folder with no thread record is almost always a hand-made folder the CEO created deliberately — auto-archiving it to `_archive/` would be destructive from their point of view. Surface orphans in the Monday note (Phase 3d / Beat 1) with a one-line "register or archive?" prompt; let the CEO decide. Only archive an orphan when the CEO explicitly says so.

### 3d. Flag — never silently mutate — the unsafe ones

These go to `items_flagged_for_user[]` and the Monday note, NOT auto-fixed:
- **Orphan folders** (`C10.orphan_folder`) — folder on disk with no thread record. FLAG with a "register or archive?" prompt; never move or delete (client safety — see 3c note).
- **Duplicate event seqs** — valid JSON with colliding numbers. Append-only history must NOT be rewritten (it would break the tamper-detection hash chain). These require a deliberate additive correction (a dedicated converter), so cleanup only *reports* them.
- **Org/thread parent cycles**, unresolved affiliations on active orgs, and any ambiguous referential break that needs a human judgment call.

### 3d-bis. Writer-Contract lint (event-write path — SPEC GATE1)

The old Writer-Contract check only confirmed a SKILL.md carried the `## Writer Contract` **header** (`WORKSPACE_API.md` §"How Skills Reference This File"). A header is not a write path: a skill could carry the boilerplate header and still hand-roll a `next_seq`+`open('a')` append that dodges the A1 writer lock (decision-log was the confirmed bypass). This step runs the executable lint that asserts the BODY of every event-appending skill names the locked writer `atomic_append_jsonl` (or a known append-routing helper script that does).

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import writer_contract_lint as wcl
findings = wcl.lint_skill_event_writes('.')   # plugin root = cwd ($PLUGIN_ROOT)
print(json.dumps({'count': len(findings), 'skills': [f['skill'] for f in findings]}))
"
```

FLAG only — never auto-edit a SKILL.md. If the count is non-zero, add ONE plain-English line to the Monday note's "worth a glance" tier (no `_hq/` paths, no `atomic_append_jsonl` jargon, no skill names): *"Part of Command Room is saving your activity log an older way — nothing's broken, but it's worth mentioning to whoever set up your Command Room."* A clean tree (count 0) adds nothing.

### 3e. Append the run record + conflicts

- Append schema/integrity violations to `_hq/CONFLICTS.md` (same as the old audit).
- Append ONE `cleanup_run` receipt via the canonical helper (`shared/scripts/receipts.py`, v4.5.2 R1) — **this receipt is REQUIRED on every run, even a nothing-to-do run**: cleanup fired receiptless for ~6 weeks during the v4.5.1 dogfood and its silent failures were indistinguishable from silent successes (FINDINGS F-39/F-43/F-54). One line: `from receipts import log_receipt; log_receipt(WORKSPACE_ROOT, "cleanup", receipt_type="cleanup_run", fired_via="scheduled", extra_data={"actions_taken": [...], "items_flagged_for_user": [...], "tail_hash": "..."})` — `"manual"` for fired_via on `run cleanup` chat fires.
- **tail_hash backward compatibility:** when computing the append-only mutation check, look up the previous run's `tail_hash` from the most recent `cleanup_run` **OR** legacy `audit_run` event (accept either type). New events are always written as `cleanup_run`. Never rewrite old `audit_run` events.

## Phase 3.5: Brain self-heal (Live State render + one-time migration)

The brain-substrate fleet backstop — this is what makes the weekly schedule earn its keep. Per-project `PROJECT_BRAIN` files keep their People + Status sections rendered live from the substrate. The `go [project]` load-path already refreshes a project the moment the CEO opens it; this weekly sweep covers the projects they did NOT open, so nothing silently goes stale.

### 3.5a — One-time migration (idempotent, never destructive — safe to run every week)
Convert each project's hand-written People table into the generated Live State block. The helper is idempotent — a no-op after the first conversion — so it's safe to call every run.

**Hard gate (v3.18.2+ — Bug #84). Run the EXACT block below; do not pre-flight it.** The migration script ships at `shared/scripts/release_actions/migrate_brain_live_state.py` in **every v3.16+ build** — it is NOT optional and NOT version-gated. Do NOT check "does this version have the script" and skip; do NOT guess the path (`shared/scripts/migrate_brain_live_state.py` — without `release_actions/` — is the WRONG path and is what the v3.18.1 scheduled fire mis-resolved before logging "doesn't exist in this version — skipped gracefully"). The block resolves `PLUGIN_ROOT` explicitly (so it does not depend on the current working directory at fire time) and **assert-imports** the module: if the import raises, that is a LOUD, real failure (incomplete plugin install) to surface in the run record — **never** a silent "feature not in this version" skip.

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -dt "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/ 2>/dev/null | head -1 | sed 's:/$::')
cd "$PLUGIN_ROOT" && python3 -c "
import sys, json, os
root = os.getcwd()
# Absolute sys.path — independent of cwd at fire time (Bug #84 root cause).
sys.path.insert(0, os.path.join(root, 'shared', 'scripts'))
sys.path.insert(0, os.path.join(root, 'shared', 'scripts', 'release_actions'))
ra = os.path.join(root, 'shared', 'scripts', 'release_actions', 'migrate_brain_live_state.py')
# Assert-import — fail LOUD, never 'skip gracefully'. Ships in every v3.16+ build.
try:
    import migrate_brain_live_state as m
    import render_thread_live_state as r
except ImportError as e:
    raise SystemExit('ABORT Phase 3.5a — could not import migrate_brain_live_state from '
                     + ra + ': ' + repr(e) + '. The plugin install is incomplete; this is a REAL '
                     'error to surface, NOT a missing-feature skip.')
assert hasattr(m, 'migrate_brain'), 'migrate_brain_live_state imported but has no migrate_brain() — stale/corrupt build; ABORT (do not skip)'
ws = '<workspace_root>'
# Shape-defensive entities read (Bug #84-followup, found 2026-05-31 in A84 verify):
# many real workspaces (M's included) store entities FLAT (threads at top level, no
# 'entities' wrapper). The old wrapper-only read returned an empty dict on a flat file
# -> 0 threads -> migration silently processed nothing (the #84 outcome via a 2nd cause).
_d = json.load(open(ws + '/_hq/data/entities.json'))
ent = _d['entities'] if isinstance(_d.get('entities'), dict) else _d
threads = ent.get('threads') or ent.get('projects') or []
migrated, errors = [], []
for t in threads:
    bp = r.default_brain_path(ws, t['id'])
    if not bp: continue
    try:
        m.migrate_brain(ws, t['id'], bp, dry_run=False); migrated.append(t['id'])
    except Exception as e:
        errors.append((t['id'], repr(e)))  # per-thread, surfaced — not silently swallowed
print('migrated/verified', len(migrated), 'brains; per-thread errors:', len(errors))
if errors: print('PER-THREAD-ERRORS:', errors)
"
```
Two distinct failure modes: a **missing/broken module** is a loud ABORT (the assert-import above) — surface it, never skip Phase 3.5a; a **per-thread** exception is collected and surfaced in the run log but does not abort the sweep. `migrate_brain` **NEVER deletes a hand-written person** — anyone with no events relocates to a "Manually tracked" durable list, never dropped. Record into `actions_taken[]` only the brains it actually changed.

### 3.5b — Re-render every active thread's Live State (dirty-checked, cheap)
```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import render_thread_live_state as r
ws = '<workspace_root>'
# Shape-defensive read (Bug #84-followup) — flat OR wrapped entities.json.
_d = json.load(open(ws + '/_hq/data/entities.json'))
ent = _d['entities'] if isinstance(_d.get('entities'), dict) else _d
threads = ent.get('threads') or ent.get('projects') or []
refreshed = []
for t in threads:
    if t.get('status') == 'archived': continue
    try:
        r.render_live_state(ws, t['id']); refreshed.append(t['id'])
    except Exception: pass
print('refreshed', len(refreshed), 'brains')
"
```
The render is **dirty-checked** — it only rewrites a block when a thread-tagged event newer than the block's recorded `source_seq` exists, so a quiet workspace is a fast no-op. It **byte-preserves** all hand-written brain content (only the marked Live-State region changes). Record refreshed brains into `actions_taken[]`.

### 3.5c — Regenerate the People registry (`_hq/views/PEOPLE.md`)
The people directory is a generated view that drifts when no code re-fires it (it had no renderer until v3.17.1, and stale-drifted from 95 people in the substrate down to a 69-person view). Regenerate it deterministically from the substrate every run:
```bash
python3 -c "import sys; sys.path.insert(0, 'shared/scripts'); import render_people_view as r; print(r.regenerate('<workspace_root>'))"
```
Atomic-write, idempotent (content-stable apart from the header timestamp), also updates the back-compat copy at `_hq/PEOPLE.md`. Record into `actions_taken[]` only if the active/archived counts changed from the prior PEOPLE.md header.

**ALIASES.md safety net (D7).** `_hq/views/ALIASES.md` is projected from `aliases.json` and owned by **people-crm** (regenerated on any `aliases.json` write — per `references/VIEW_GENERATION.md`). There is **no standalone aliases-view renderer** in `shared/scripts/` (verified at build time), so cleanup cannot safely regenerate it — it would have to re-implement people-crm's projection. Instead, cleanup **flags** staleness and names people-crm as the owner:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.check_aliases_staleness('<workspace_root>')  # None if current
print(json.dumps(r))
"
```
If it returns non-None, add a Monday-note line: *"My list of name shortcuts looks out of date — say `refresh aliases` and I'll rebuild it."* Never regenerate ALIASES.md here.

### 3.5d — Regenerate the DECISION_LOG view (changed-only) (D4)

`_hq/views/DECISION_LOG.md` has a renderer (`render_decision_log.py`) and is wired into decision-write paths, but a **missed** regen (e.g. insight-generator paused, or a decision logged by a path that didn't re-fire it) then persists until the next decision is logged — which can be weeks (the forensic case: 23 days stale). cleanup is the weekly backstop. Use the **changed-only** entry point so a quiet workspace stays a true no-op write (idempotence):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.regenerate_decision_log_if_changed('<workspace_root>')
print(json.dumps(r))
"
```
Record into `actions_taken[]` **only when `changed` is True**. This regenerates a derived view from the substrate — it never rewrites `events.jsonl`/`entities.json`.

### 3.5d2 — Regenerate the MASTER_TRACKER view (changed-only) (D4)

`_hq/views/MASTER_TRACKER.md` is a generated projection of `entities.json` + `events.jsonl` but had **no renderer** until v4.2.0 — `VIEW_GENERATION.md` and `workspace-manager` claimed a "writer helper" regenerated it, but the only thing that ever did was the LLM hand-rendering it during end-session. When that hand-render lapsed, the tracker froze while the substrate stayed current (forensic case: M's tracker frozen from 2026-06-11 while events.jsonl ran through today). It was the only major projected view with no renderer and no cleanup backstop. v4.2.0 ships `render_master_tracker.py` and wires `regenerate` into end-session; cleanup is the weekly backstop. Use the **changed-only** entry point so a quiet workspace stays a true no-op write (idempotence):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
r = ca.regenerate_master_tracker_if_changed('<workspace_root>')
print(json.dumps(r))
"
```
Record into `actions_taken[]` **only when `changed` is True**. The renderer reads every commitment field through `cru_match` (shape-safe) and dual-writes the canonical `_hq/views/MASTER_TRACKER.md` + back-compat `_hq/MASTER_TRACKER.md`; it never rewrites the substrate. `changed` also flips True when only the back-compat copy was missing — so this heals the `_hq/` vs `_hq/views/` path-orphan in older workspaces on the next sweep.

### 3.5e — Flag stale analytical views + nudge a paused insight-generator (D5)

The analytical views (`RELATIONSHIPS.md`, `TIMELINE.md`, `COMMITMENT_AGING.md`, `DORMANT.md`, `THEMES.md`) are **NOT cleanup's job to regenerate** — they're `insight-generator`'s expensive lazy synthesis (per `references/VIEW_GENERATION.md`). cleanup's job is to **flag honestly** when they've fallen behind the substrate, and to surface the real root cause (a paused insight-generator) — the forensic gap was that cleanup neither regenerated NOR actually flagged, and insight-generator wasn't firing, so nobody owned it.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import cleanup_actions as ca
ws = '<workspace_root>'
stale = ca.check_analytical_view_staleness(ws)            # views older than the substrate
nudge = ca.insight_generator_staleness(ws)                # has insight-generator gone quiet >14d?
print(json.dumps({'stale_views': stale, 'insight_nudge': nudge}))
"
```
- If `stale_views` is non-empty → one Monday-note line (insight-generator is the owner internally; never name it to the CEO): *"A few of your insight pages are behind — say `run insights` and I'll bring them current."* (List the page names plainly; no `_hq/` paths.)
- If `insight_nudge.stale` is True (insight-generator hasn't fired in **>14 days**, or never) → add the deeper nudge: *"I haven't run your weekly insights in a while — say `run insights` and I'll bring the patterns and pages current."* This is the honest signal that the analytical views will keep drifting until insight-generator runs.

cleanup **never** regenerates these views (D5 — duplicating the synthesis blurs ownership and is expensive). It flags, names the owner, and moves on.

### 3.5g — Rotate the event log if it's grown large (SPEC A5)

`events.jsonl` is rewritten in full on every append, so on a large workspace write cost
grows with history. Once it crosses **5 MB or 10,000 lines AND contains prior-calendar-year
events**, rotate the prior years into immutable `events-<year>.jsonl` shards (the active file
keeps the current year + a `shard_rotated` seq-continuity marker). Small workspaces never
shard. Dry-run first, then rotate if eligible. The rotation runs under the A1 writer lock and
rebuilds the A3 dedup index itself.

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import rotate_events as rot
ws = '<workspace_root>'
preview = rot.rotate(ws, now_iso='<local ISO>', dry_run=True)
res = rot.rotate(ws, now_iso='<local ISO>') if preview.get('would_archive') and not preview.get('rotated', True) is True else preview
print(json.dumps(res))
"
```

If `rotated` is True, add one reassuring Monday-note line — *"Your activity log got large, so I archived [N] older events into a yearly file — nothing lost, everything still searchable."* Omit entirely when nothing rotated (the common case). Readers are shard-transparent (the canonical loaders include shards automatically), so deep-history views keep working after a rotation.

### 3e-bis. Scheduled-task watchdog sweep (Phase 3 — W1 surface (b) + R5 missed-fire detection + R10 scheduled-output self-audit)

Cleanup is the weekly deep pass of the reliability watchdog (the morning brief runs the light daily pass). Three layers, in order:

**1. Fired-recency + missed-fire check (W1 + R5).** Call `mcp__scheduled-tasks__list_scheduled_tasks` (cleanup runs interactively enough to afford the MCP call — this is what makes it the deep pass), then:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
ws = '<workspace_root>'
records = <the list_scheduled_tasks result, as a Python list of task dicts>
verdict = tw.health_verdict(ws, task_records=records)
print(json.dumps({'vantage': verdict['vantage'], 'lines': verdict['lines'],
                  'info_lines': verdict['info_lines'], 'reports': verdict['reports']}))
"
```

`task_records` gives the watchdog the scheduler's `lastRunAt` per task, which is what detects the R5 class — a task that silently skipped >=2 expected fires (`late`), a task registered but never authorized (`never_authorized`), and the fired-but-wrote-nothing case (`receipt_gap`: fresh `lastRunAt`, stale substrate receipt — the render-without-write class). Fold every returned `lines` entry into the Monday note's "worth a glance" tier verbatim, and any `info_lines` entry (dated late catch-ups, tasks still waiting on a first run — v4.5.2 R3) as one-liners in the same tier (they're already plain English). If `list_scheduled_tasks` is unavailable in this fire, run receipts-only (`tw.health_verdict(ws)`) — degraded but never skipped.

**Vantage guard (F-40):** if `vantage` is non-null, this session cannot see the machine-local scheduler (cloud/remote chat, or a different computer than the one the tasks run on) — the Monday note carries the single `vantage['line']` sentence instead of any per-task registration claims, and layers 2–4 are skipped for this fire. Never report tasks as unregistered from a blind vantage.

**2. Registered-prompt drift (W4).** With the same records in hand, read the installed plugin version from `$PLUGIN_ROOT/.claude-plugin/plugin.json` and run `tw.check_prompt_versions(records, installed_version)`. Any `stale: True` finding → one Monday-note line: *"Your scheduled chats were set up under an older version — say 'update command room' once and they'll refresh themselves."* (One line total, not per task; unstamped legacy prompts are informational and add nothing.)

**3. Scheduled-output self-audit (R10 — transcripts).** For each scheduled-chat thread that FIRED this week (per the watchdog reports / `lastRunAt`), read its session transcript via the session-info tools (`list_sessions` -> the scheduled-chat thread -> `read_transcript`; proven to work from scheduled sessions, 2026-07-01) and verify BOTH halves of the fire happened:
   - **rendered** — the transcript shows the widget/digest actually posted (a `show_widget` call or the digest text), and
   - **wrote** — the substrate carries the fire's receipt (`pack_run` / `sent_reconcile` / etc. — the watchdog's receipt check above already computed this; a `receipt_gap` finding IS the write-side failure).
   A fire that rendered but didn't write, or wrote but didn't render, gets one Monday-note line naming the task and the half that failed, in plain English (e.g. *"Tuesday's Inbox run showed you the summary but didn't record its work — the numbers it feeds may run a day behind."*). If the session-info tools aren't available in this fire, skip layer 3 silently (layers 1-2 still ran) — never fabricate a transcript finding.

**4. Chronic-lateness proposal (R4 consumer).** Read the late-fire telemetry and propose a better default time for any task that has been >24h late in 3 of the last 4 weeks (thresholds live in `late_fire.py` — one place):

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from late_fire import detect_chronic_lateness
print(json.dumps(detect_chronic_lateness('<workspace_root>')))
"
```

Each returned `line` goes into the Monday note verbatim — it already names the task in plain English and routes the fix through `change my schedule`. PROPOSE ONLY: cleanup never moves a cron itself, and a user-customized time is never overridden (the move happens only if the CEO says so via change-schedule).

**5. Schedule-parity check (R2 — detect + report, NO config writes).** With the same scheduler records, compare the registered-task set against the merged schedule view:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
import task_watchdog as tw
from event_gate import append_event
ws = '<workspace_root>'
registered = <the taskIds from list_scheduled_tasks, as a Python set>
parity = tw.check_schedule_parity(ws, registered)
append_event(f'{ws}/_hq/data/events.jsonl', {
    'type': 'schedule_parity_checked',
    'source_skill': 'cleanup',
    'data': {k: len(v) for k, v in parity.items()},
}, holder='cleanup')
print(json.dumps(parity))
"
```

- **`ghost_first_install`** (a first-install task enabled in the config/defaults but missing from the registered set) → real breakage; one Monday-note line: *"Your [Display Name] task is missing from the schedule — say 'set up command room schedules' to restore it."* (The watchdog layer above usually catches this too; don't double-report the same task.)
- **`ghost_later_add`** (commitments / pulse / relationship-moves / commitment-triage / staff-meeting not registered) → EXPECTED, say nothing — the R3 proposal step owns that nudge.
- **`orphan_overrides`** (a `schedule_config` entry for a taskId that exists in neither DEFAULT_SCHEDULES nor the registered set — e.g. a legacy `cr-*` key) → one Monday-note line: *"An old schedule setting from a previous version is lingering — harmless, but say 'change my schedule' and 'back to defaults' if you ever want a clean slate."* FLAG ONLY: the only heal for an orphan override is a removal, and cleanup never removes; under sparse-config semantics there is no safe ADDITIVE heal (densifying would destroy the customized-cron signal), so the heal direction stays flag-only and `schedule_config_healed` remains registered-but-unwritten.
- The audit event's counts are what make the weekly check auditable — never narrate the event name to the CEO.

**6. Later-add task proposal (R3 — propose, NEVER auto-register).** When the parity check reported a `ghost_later_add` set (or on any healthy week), run the readiness check:

```bash
python3 -c "
import sys, json; sys.path.insert(0, 'shared/scripts')
from schedule_proposals import propose_later_add_tasks, log_proposal
ws = '<workspace_root>'
registered = <the taskIds from list_scheduled_tasks, as a Python set>
proposals = propose_later_add_tasks(ws, registered)
for prop in proposals:
    log_proposal(ws, prop['task'])   # the 6-week suppression record
print(json.dumps(proposals))
"
```

Thresholds live in ONE table (`schedule_proposals.PROPOSAL_THRESHOLDS`): the staff-meeting (LB1 R4 — it absorbed the standalone relationship-moves proposal slot; existing relationship-moves registrations are untouched) qualifies on ≥8 prospect+client orgs AND ≥14 days of accumulated dormancy signal, OR ≥3 open Living Brain proposals waiting on the user; dormant-customer-scan (≥5 clients) is offered only as the lighter alternative when the staff meeting doesn't land — never both in one round. Surface each returned `line` verbatim in the Monday note's "worth a glance" tier, citing the real counts the helper already baked in. The add itself flows through the EXISTING paths (say `add staff meeting` → change-schedule / registration Phase 6 add) — cleanup registers NOTHING. A proposal that was surfaced is automatically suppressed for 6 weeks; an empty result adds no line.

Enforcement note (the #98 lesson, generalized): every check above binds to artifacts — substrate receipts, scheduler records, transcripts — never to what a fire *narrated*. The watchdog READS receipts; it does not trust narration.

### 3f. Deliverable voice/privacy sweep — the GATE2 backstop (SPEC GATE2 D3)

This is the load-bearing layer of GATE2's enforcement-by-detection. The save-time voice + leak gates only run when a deliverable routes through `brief_writer.make_brief`; live testing proved the LLM routinely hand-rolls a `.docx` via the generic docx skill (0 `gate_ran`, every gate bypassed). So instead of trusting the route, cleanup **reads the documents that were actually produced this week** and flags any carrying a voice tell or a privacy/substrate leak — regardless of how they were made. The scanner opens the file itself, so a hand-rolled doc is caught exactly like a `make_brief` one.

**READ + FLAG ONLY — never deletes, moves, or edits a user's file** (client safety, same posture as orphan folders in 3d). "Quarantine" here means surface loudly, not relocate. The only writes are CR-owned telemetry (a `gate_ran` event + a findings record under `_hq/.system/gate2_findings/`), and they can never block.

```bash
python3 -c "
import sys, json, time; sys.path.insert(0, 'shared/scripts')
import deliverable_sweep as ds
ws = '<workspace_root>'
since = time.time() - 7*86400          # only docs produced in the last 7 days
res = ds.sweep_workspace(ws, since_ts=since, emit=True, source='cleanup_sweep')
bypass = ds.detect_gate_bypass(ws, since_ts=since)   # D5 gate_ran join (cheap complement)
summary = ds.summarize_for_user(res)
print(json.dumps({
    'scanned': res['scanned'],
    'violations': res['violation_count'],
    'warns': res['warn_count'],
    'errors': res['error_count'],
    'suspected_bypass': bypass['suspected_bypass'],
    'summary': summary,
}))
"
```

Fold the result into Beat 1 (see below):
- If `violations > 0` (or `errors > 0`): surface the `summary` string verbatim under the "worth a glance" tier — it names each doc by filename and the offending language in plain English (no `_hq/` paths, no token jargon). This is the "this document didn't pass the quality gate" flag, reaching the CEO before they forward it.
- If `suspected_bypass > 0` AND there were no content violations: a softer line — *"A few documents were produced this week without going through the quality check — worth a glance to confirm they sound like you."* (The content sweep already covers the ones still on disk; this catches deliverables that left the workspace.)
- Clean sweep (all zero): add nothing.

> **Honest framing (SPEC GATE2 D7).** This sweep is why the product claim is "Command Room **detects and flags** voice/privacy violations in what it produces, before they leave your hands" — not "bad output can't be produced." An LLM with code access can always hand-roll a doc; reading the produced file is what makes the violation catchable. The weekly cadence is the floor; the same-turn Stop hook (`hooks/hooks.json` → `gate2_turn_sweep.py`) catches it sooner **if the runtime executes plugin hooks** (unverified in Cowork — the live re-run confirms).

### 3g. Voice draft-snapshot pruning (B1)

`_hq/voice/draft-snapshots.jsonl` holds drafted email bodies kept only long enough to diff against the sent version (voice calibration). Prune rows that are **already matched** (a correction row for the same `draft_event_seq` exists in any `_hq/voice/corrections-*.jsonl`) OR **older than 90 days**. READ + rewrite the snapshots file only — never touch corrections logs or any user deliverable. Bodies are workspace-private (same class as transcripts); pruning is mandatory so they don't accumulate. If the file is absent, no-op.

### 3h. source_ref dedup index verify (A3)

The dedup index (`_hq/data/.source_refs.idx`) is a cache over events.jsonl that keeps duplicate captures out regardless of age. It self-maintains on every append, but manual events.jsonl surgery (corruption recovery, quarantine release) can leave it divergent. Run `python3 shared/scripts/source_ref_index.py verify <workspace_root>`; on `MISMATCH`, run `rebuild` and add one line to the Monday note ("tidied up one of my behind-the-scenes indexes — nothing changed in your data"). Run rebuild AFTER any corruption-recovery path in this cleanup that touched events.jsonl. Also move any cloud-sync conflict copies matching `.source_refs*.idx` that aren't the canonical name into `_archive/dedup-index/` (archived, never deleted). If the index matches, say nothing.

### 3i. Long-unconfirmed commitment sweep (v4.6.1 W4b — PROPOSE only)

Captures that have sat unconfirmed (pending_review / no owner / suspected duplicate) for 30+ days almost certainly resolved outside the system or were never real — the weekly note proposes Drop; the drop itself is ALWAYS a manual click on the triage surface, never something cleanup does. Read-only here:

```python
import sys; sys.path.insert(0, "shared/scripts")
from cru_match import load_open_commitments
from confirm_flow import select_unconfirmed_escalation

opens = load_open_commitments("<WORKSPACE>/_hq/data/events.jsonl")
stale_unconfirmed = select_unconfirmed_escalation(opens, "<now ISO>")["propose_drop"]
```

A non-empty result adds ONE line to the Monday note's Beat 1 (see below). Zero → nothing. No events written, no receipts beyond the standard cleanup_run, and this never touches the scheduled-task watchdog pass (3e-bis) or its note lines.

### 3j. Living Brain proposal expiry sweep + card health (SPEC LB1)

The anti-fatigue contract's back half: an ignored proposal expires SILENTLY at its TTL (default 14 days) — logged, never nagged — and the expiry count is visible here so queue rot reaches the CEO's dogfood without a nag. Two calls, both through the canonical module:

```python
import sys; sys.path.insert(0, "shared/scripts")
from brain_proposals import expire_stale, card_health_counts

swept = expire_stale("<WORKSPACE>")          # appends brain_proposal_expired tombstones (bounded write, see Writer Contract)
health = card_health_counts("<WORKSPACE>")   # {"open": N, "expired_in_window": M} — 30-day window
```

The sweep is the ONLY expiry writer (surfaces already exclude TTL-past items from render, so a missed Sunday never shows stale rows — the tombstone just makes the ledger explicit). Feed `health` into the Beat 1 card-health line below. Never narrate proposal ids or event types.

## Phase 4: Monday-Morning Report — the scorecard handshake (no scores)

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- BAD: "Your event log had some write contention this week (3 waits, 1 timeout) — the writer lock held."
- GOOD: "A few things tried to save to your activity log at the same moment this week — everything saved fine, nothing was lost."

**Three short beats: what I tidied → what I handled for you → what's waiting / what you missed.** Keep it TIGHT — this is a handshake, not a recap (Friday Wrap owns the business recap; the monthly operator-report owns the deep value proof). It's the silent proof that Command Room earned its keep this week.

### Beat 1 — What I tidied (needs-eyes tiers)

Lead with what's done and what (if anything) needs eyes. Three tiers, mapped from an internal assessment — the tier mapping carries over from the old audit, **the score itself does not and is never surfaced.**

- **Clean tier (default):** one line. `"Tidied up this weekend — filed away [N] old briefings and [M] long-finished commitments. Nothing needs your eyes."`
- **A few things tier:** short, plain, forward-action.
  ```
  Tidied up this weekend. A couple of things worth a glance:
  • Your Acme notes haven't been touched in 6 weeks — want to mark the project paused?
  • Two commitments to Sam are aging past 30 days.
  Nothing else needed cleaning.
  ```
- **Backlog tier:** a 3-item prioritized list ("this week's three"), plain English, no scores, no alarm language, forward-action lead.

**Beats the CLEAN1 scan feeds into Beat 1 (include only the ones with real findings — omit zeros):**
- **Orphan folders found** (Phase 1.0 `orphan_folder`): *"I noticed [N] folders that aren't tracked yet — [names]. Want me to register or archive them?"* FLAG only; never moved.
- **Session-notes backfilled** (Phase 3c, D3): *"[N] projects were missing a notes file — I started one for each so they stay current."* (Only counts files actually created; the helper never overwrites existing notes.)
- **Stale insight views** (Phase 3.5e, D5): the single line — *"A few of your insight pages are behind — say `run insights` and I'll bring them current."* Add the >14-day insight-generator nudge when `insight_nudge.stale` is True.
- **Long-unconfirmed items** (Phase 3i, v4.6.1 W4b): when `stale_unconfirmed` is non-empty, ONE line — *"[N] captured to-dos have sat unconfirmed for over a month — say `triage my commitments` and I'll queue them up to drop or keep."* PROPOSE only (the drop is a click on the triage surface, never automatic); omit on zero. Keep this line out of the watchdog cluster below — it's a substrate-hygiene item, not a schedule finding.
- **Living Brain card health** (Phase 3j, LB1): when `health["open"] > 0` or `health["expired_in_window"] > 0`, ONE line — *"[N] suggestions are waiting on your yes/no — say `staff meeting` to run through them. [M] older ones expired quietly without an answer this month."* (Drop either half at zero; drop the line when both are zero.) This is the queue-rot visibility line — if the expired half keeps growing, the card cadence or the detectors need tuning, and this line is the evidence. Substrate-hygiene item — keep it out of the watchdog cluster.
- **Lock files archived** (Phase 2 Rule 9, D6): fold the count into the "tidied up" line — *"…tidied away [N] leftover lock files."* Plain English; never say "lock.stale" or surface a path. (They're moved to `_archive/`, never deleted — but don't burden the CEO with that detail unless asked.)
- **Deliverable voice/privacy flags** (Phase 3f, GATE2): when the sweep flagged docs, surface its plain-English `summary` verbatim — *"[N] documents produced recently didn't pass the quality gate — worth a glance before any go out: • [filename] — language that doesn't sound like you ('leverage')…"*. Filenames only, never `_hq/` paths or token jargon. When only `suspected_bypass` is non-zero, use the softer "produced without the quality check" line. Omit entirely on a clean sweep.
- **Scheduled-task watchdog findings** (Phase 3e-bis, W1/R5/R10 + R3 truth rules): surface each returned line verbatim under the "worth a glance" tier — dead task, never-authorized task, folder rename, prompt drift, render-without-write, plus the R3 info lines (dated late catch-ups, first-run-pending). These lead the tier when present (a dead schedule starves every other surface). If the vantage guard fired (F-40), its single line replaces ALL per-task schedule claims. Omit entirely when the watchdog returns nothing (the common case). A task named in any of these lines is never simultaneously described as running normally elsewhere in the note.
- **Events-lock contention** (Phase 2 Rule 10, A1): include ONLY when there were waits or timeouts this week — *"A few things tried to save to your activity log at the same moment this week — everything saved fine, nothing was lost."* Omit the line entirely on a quiet week (the common case). Never surface file paths, "lock_stats", wait/timeout counts, or lock vocabulary; the point is reassurance that it was handled, not a metric dump. The counters reset after this report.

**Set-aside audit line (W4c — own paragraph, non-zero weeks only).** Read `capture_gate.observed_counts(workspace_root, since_ts=<7 days ago ISO>)` and, when `observed > 0`, add exactly one sentence to Beat 1: *"I also set aside [N] items from meetings and chats that looked like other people's to-dos — ask me to show them if you want a look."* Never a per-item list, never the words "observed" or "tier"; omit entirely at zero. (Visible rejects are what make the capture filter trustworthy.) HYG1: `observed` counts LIVE items only — 30-day-expired set-asides report under the separate `expired` field and never inflate this sentence; nothing is deleted, they just age out of the surfaces.

### Beat 2 — What I handled for you (the value, this week)

A short concrete line of what Command Room delivered this week — the "we did our job" proof. Compute from `events.jsonl` over the last 7 days (the same idea as `operator-report`'s "delivered without being asked," just weekly and shorter). **Specifics, never a score:** *"This week: 12 morning briefs, 8 meeting preps, 14 drafts in your voice, 6 commitments captured from meetings, 2 customers flagged going quiet."* Only categories with real counts; omit zeros. One line, two at most.

### Beat 3 — Where things stand with you (adaptive — never a scold)

Branch on how engaged the CEO was this week (their actions vs their own trailing ~4-week baseline — on-demand commands fired, scheduled items acted on, projects opened, commitments closed):

- **Engaged week → surface the plate (a mirror, not a grade).** *"Waiting on you: 4 commitments aged past their date, 3 drafts I prepped you haven't sent, Northstar's gone quiet for 18 days."* Answers "did you do yours?" by showing what's outstanding — service, not judgment.
- **Light week (notably below their baseline) → the activation nudge.** Show the value left on the table, each paired with the exact words to capture it next time — FOMO + a free lesson, NEVER "you didn't use me":
  - *"You had 6 meetings on your calendar — I only prepped 1. Say `prep me for my [meeting]` and I'll have a brief ready 5 minutes before."*
  - *"~15 emails went out; I drafted 0. Try `draft a reply to [name]` — I'll match your voice."*
  - *"3 Granola transcripts landed I never processed. `process the call` pulls the action items in 60 seconds."*
  - *"You haven't opened [project] in 3 weeks. `go [project]` brings you current instantly."*
  Pick the **1–3 highest-payoff** missed opportunities; never a wall of "you should have."

**Don't-nag guardrail (mandatory).** The nudge must not become a weekly guilt drip — that churns a light-by-choice CEO faster, the opposite of the intent. **Rotate** the examples week to week (never the same nudge verbatim); if the CEO ignores it ~3 weeks running, **back off** to just Beat 2 (a good chief of staff reads the room, it doesn't hector). Tone test every line: would a trusted human chief of staff say this to their CEO? If it reads as grading or guilt, rewrite it as service.

**Forbidden in the user-facing surface** (per CONTRACT Rule 4): vendor self-congratulation ("look how much we did!"); any line that grades the CEO's effort or guilt-trips low usage (Beat 3 surfaces value + opportunity, never judgment); score numbers; ALL-CAPS headers; 🟢/🟡/🔴 grade emoji; internal mechanism names (`classifier health`, `tail_hash`, `cleanup_run event`, `org tree`, `boundary violation`); raw `_hq/` paths; the words FAIL/ERROR/CRITICAL/VIOLATION; self-narration ("Phase 1 scanning…"). When the self-heal fixed corruption, say it plainly: "I noticed your activity log looked a little off and tidied it up — nothing lost." Never "events.jsonl tail_hash mismatch."

## Phase 5: Save the Report

Generate a `.docx` at `[WORKSPACE_ROOT]/_hq/cleanup-reports/[YYYY-MM-DD]-cleanup.docx` via `brief_writer.py` **only** when there's something substantive to surface (A-few-things / Backlog tiers). Clean weeks: no doc, just the one-liner. The .docx body follows the same non-technical voice (no scores, no alarm language, forward-action framing). Surface it as the canonical H2 deliverable link at the bottom of the chat turn per CONTRACT Rule 3. Create `cleanup-reports/`, `briefings/`, and `summaries/` under `_hq/` if missing.

## Reliability

Runs as a job inside the `maintenance` scheduled task (Sunday evening slot — CEO reviews Monday AM; a missed Sunday self-heals at the next fire) and implements `shared/RELIABILITY.md`. Point-in-time snapshot (no missed-fire catch-up; runs at next opportunity), runs normally during OOO, 15s per-connector / 60s aggregate timeout budget. This skill is also the backup snapshotter — daily snapshots of data files to `_hq/backups/`; snapshots older than 14 days are moved to `_archive/backups/` (archived, never deleted). Corrupted `entities.json` / `aliases.json` triggers auto-restore from `_hq/backups/[file]_[date].backup`; corrupted `events.jsonl` is healed via the Phase 3b recurring self-heal (quarantine, not restore — append-only).

## Gotchas

- **Phantom projects** (tracker says active, no folder) → Phase 3c auto-fixes this one: remove the stale pointer and note it in `actions_taken[]`; don't invent a folder.
- **Orphan folders** (folder, no tracker entry) → **FLAG ONLY** (client safety, D1 — same rule as Phase 3d): surface in the Monday note with the "register or archive?" prompt and let the CEO decide. Never move, archive, or delete without the CEO's explicit instruction.
- **Session-notes path** lives at `[WORKSPACE_ROOT]/[Project Name]/SESSION_NOTES_[NAME].md`, not a subfolder.
- **Inbox** is a section in MASTER_TRACKER.md, not a folder.
- **Content-accuracy false positive** — a paused/steady-state project isn't "drifted"; respect the stage field.
- **Duplicate seqs are NOT corruption the self-heal touches** — they're valid JSON; only flagged, never auto-rewritten (would break the hash chain).
- **Staleness thresholds** — if MASTER_TRACKER has no "Staleness Rules" section, use defaults and note it.

## What It Doesn't Do

- Does not produce a narrative recap — `weekly-recap` owns the 7-day narrative.
- Does not surface a score — workspace health is binary (tidy / needs your eyes), never graded.
- Does not generate a dashboard — retired; the Monday note has the same information in plain English.
- Does not ask "want me to fix these?" — it does the safe fixes and surfaces only judgment calls.
- Does not rewrite append-only history — corruption is quarantined, duplicate seqs are flagged for a deliberate converter, old `audit_run` events are read but never mutated.
- Does not run destructive repairs silently — only the bounded safe set in Phases 2 & 3c; everything ambiguous is flagged.
