---
name: cleanup
description: Weekly self-maintenance. Keeps the workspace tidy without the CEO's attention — runs Sunday night (scheduled), auto-fixes what's safe, heals substrate corruption, and leaves a short plain-English Monday-morning note only if something needs eyes. Triggers on "weekly cleanup", "clean up my workspace", "tidy up", "maintenance", "deep clean", "clean up". Also catches retired audit phrases "weekly audit", "health check", "system review", "scan everything" and redirects them here. DOES NOT fire on "weekly recap" / "what happened this week" (that's weekly-recap) or "level up command room" (opt-in add-ons).
---

# Cleanup

One skill, one job: keep the customer's workspace tidy every week without requiring their attention. Runs Sunday night via `enable-command-room-schedules`, fixes what it safely can, heals corruption, and leaves a short plain-English Monday-morning note for anything that needs the CEO's eyes. Replaces the retired `weekly-audit`.

The shift from the old audit: cleanup does NOT hand the user a score, a dashboard, or a "want me to fix these?" prompt. For a non-technical CEO the answer to "should I fix this?" is always yes, so cleanup just does the safe fixes and surfaces only genuine judgment calls.

## Personification Contract (v3.13.8.4+)

Before surfacing the summary or composing the `.docx` report, read `shared/PERSONIFICATION.md` and call `shared/scripts/personification.py::get_brain_name(workspace_root)`. The chat summary intro uses the shape `"Cleanup done, {first_name} — {brain_name} tidied up {N} things this weekend."` The `.docx` report (when generated) opens with the same author line in the header. Default `{brain_name}` = `"Penelope"`.

## Writer Contract

- **Primary writer for** `_hq/cleanup-reports/[YYYY-MM-DD]-cleanup.docx` (via `shared/scripts/brief_writer.py` per CONTRACT Rule 27 — no .md deliverables).
- **Appender** for `cleanup_run` events to `_hq/data/events.jsonl` and for `_hq/CONFLICTS.md`.
- **Auto-fix writer** for the safe maintenance actions in Phase 2 and the safe integrity remediations in Phase 3 (see those phases for the exact, bounded write set).
- **Brain Live State renderer (v3.17.0+)** — re-renders each active project's `PROJECT_BRAIN` Live State block via the canonical `render_thread_live_state` / `render_brain_block` helpers (marked-region write, byte-preserves hand-written content) and runs the idempotent one-time brain migration (Phase 3.5). Never hand-edits a brain; only the helper touches the marked block.
- **NEVER writes:** `entities.json` (except via the owner skills' helpers it delegates to), `events.jsonl` other than appending one `cleanup_run` event (+ the `corruption_recovery` event that `recover_corruption.py` appends on its own), `aliases.json`, `_hq/views/*`, `classifier_feedback.jsonl`, or any backward-compat view copy. Canonical entity mutation stays with the owner skills (workspace-manager / project-manager / people-crm).

## Substrate validated every run

Every run validates the v2.2 data substrate per `references/DATA_CONTRACT.md` and the JSON Schemas in `shared/data-schemas/`. The deterministic checker (Phase 3) is the executable backstop; the prose contract in DATA_CONTRACT.md remains the spec.

---

## Phase 1: Silent Scan (no questions)

Scan the entire workspace before doing anything. Resolve `[WORKSPACE_ROOT]` via the canonical CONTRACT.md Rule 22 discovery preamble (find `_hq/` under the mount). Projects live at `[WORKSPACE_ROOT]/[Project Name]/` (root level); infrastructure folders carry a `_` prefix.

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

## Phase 2: Auto-Fix Sweep (does it, doesn't ask)

On the FIRST cleanup run per workspace only (track via `entities.json` `workspace.cleanup_intro_shown: true`), print this one-paragraph reassurance so the user understands what's about to happen. Subsequent runs skip it and just do the work:

```
Running cleanup. Quick note on what this does: I only clean **caches** —
files that get rebuilt automatically when you ask for them (old briefings,
old prep briefs, old reports). Your **memory** is never deleted — session
notes, decisions, commitments, and interaction logs are compressed when they
get old but kept forever. If you ever ask "what has [person] delivered this
year," the answer is still there.
```

Then run all automatic maintenance rules from `workspace-manager/references/maintenance-rules.md` WITHOUT asking:

1. **Briefing pruning** (Rule 4): delete briefings older than 30 days — CACHE
2. **Report pruning** (Rule 5): keep only 12 most recent cleanup reports — CACHE
3. **Prep file pruning** (Rule 6): delete prep files older than 14 days — CACHE
4. **Session-notes rollover** (Rule 1): SESSION_NOTES over 150 lines → archive older entries to `SESSION_NOTES_[NAME]_archive_[YYYY].md` — MEMORY (archived, never deleted)
5. **Brain-thread pruning** (Rule 2): compress resolved threads >30 days to a Thread-History one-liner — MEMORY (compressed)
6. **Commitment archival** (Rule 3): compress delivered commitments >60 days to a Commitment-History one-liner — MEMORY (compressed)
7. **Tracker hygiene** (Rule 8): remove old Completed Quick Tasks / Recently Archived entries — STALE POINTERS (archive folders remain)
8. **Interaction-log tiered compression** (Rule 7): Tier 1 (0–90d) full, Tier 2 (90d–6mo) one-liners, Tier 3 (6mo–1yr) monthly digests, Tier 4 (1yr+) archived — MEMORY (compressed)

These rules are non-destructive by design (compress/archive, never delete memory; only caches are deleted). Record each action taken into `actions_taken[]` for the `cleanup_run` event.

## Phase 3: Substrate Integrity (detect → remediate)

This is the self-healing core. Two steps: a read-only **inspector** finds problems, then cleanup **remediates** the safe ones.

### 3a. Run the inspector (read-only DETECT)

```
python3 shared/scripts/integrity_check.py <workspace_root> --json
```

Returns structured findings with severity ERROR / WARN / INFO across ~13 referential checks: missing/malformed ids, unresolved affiliations, org/thread parent cycles, engagement endpoints, person↔org/thread link symmetry, dangling event references (test-residue detector), dead aliases, orphan folders, thread `folder_name` missing on disk, missing PROJECT_BRAIN, and **duplicate event seq**. The checker NEVER fixes — it only reports. Fold its findings in; do not re-derive them by hand.

### 3b. Heal corruption (REMEDIATE — safe, automatic)

Malformed lines in `events.jsonl` (e.g. a sync hiccup that wrote half a record) are healed automatically every run via the **recurring** self-heal:

```
python3 shared/scripts/recover_corruption.py <workspace_root> --recurring
```

`--recurring` triggers on "is anything broken right now?" (not once-per-version), so it catches drift that accumulates between upgrades. It quarantines only the malformed lines to `_hq/.system/quarantine/` (saved, never deleted), rewrites events.jsonl without them (atomic), appends a `corruption_recovery` event, and returns a friendly `customer_message`. A clean file is a fast no-op (nothing written). Surface its `customer_message` in the Monday note only when it actually healed something.

### 3c. Auto-fix the safe referential findings

For inspector findings that are unambiguous and non-destructive, fix them and record into `actions_taken[]`:
- **Orphan folder with no recent activity** → archive to `_archive/` (folder moved, never deleted).
- **Phantom tracker entry** (tracker row, no folder) → remove the stale pointer (note it).
- **Missing PROJECT_BRAIN on a real project folder** → backfill a scaffold brain from session notes (maintenance-rules Rule 15).
- **Dead alias** pointing at an archived/nonexistent entity → prune the alias.
- **Stale PROJECT_CONTEXT** → regenerate from session notes where the source is clearly present.

### 3d. Flag — never silently mutate — the unsafe ones

These go to `items_flagged_for_user[]` and the Monday note, NOT auto-fixed:
- **Duplicate event seqs** — valid JSON with colliding numbers. Append-only history must NOT be rewritten (it would break the tamper-detection hash chain). These require a deliberate additive correction (a dedicated converter), so cleanup only *reports* them.
- **Org/thread parent cycles**, unresolved affiliations on active orgs, and any ambiguous referential break that needs a human judgment call.

### 3e. Append the run record + conflicts

- Append schema/integrity violations to `_hq/CONFLICTS.md` (same as the old audit).
- Append ONE `cleanup_run` event to `events.jsonl` with `{actions_taken: [...], items_flagged_for_user: [...], tail_hash: "..."}`.
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

> **The analytical views** (`RELATIONSHIPS.md`, `TIMELINE.md`, `COMMITMENT_AGING.md`, `DORMANT.md`, `THEMES.md`) are NOT cleanup's job — they're computed lazily by `insight-generator` at its own weekly run (per `references/VIEW_GENERATION.md`). cleanup owns the PEOPLE.md regen; `MASTER_TRACKER.md` / `DECISION_LOG.md` / `ORG_TREE.md` have their own renderers + owners. If the analytical views look stale, that's insight-generator's fire to make — flag it plainly in the Monday note rather than regenerating them here.

## Phase 4: Monday-Morning Report — the scorecard handshake (no scores)

**Three short beats: what I tidied → what I handled for you → what's waiting / what you missed.** Keep it TIGHT — this is a handshake, not a recap (Friday Wrap owns the business recap; the monthly operator-report owns the deep value proof). It's the silent proof that Command Room earned its keep this week.

### Beat 1 — What I tidied (needs-eyes tiers)

Lead with what's done and what (if anything) needs eyes. Three tiers, mapped from an internal assessment — the tier mapping carries over from the old audit, **the score itself does not and is never surfaced.**

- **Clean tier (default):** one line. `"Tidied up this weekend — pruned [N] old briefings, compressed [M] commitments. Nothing needs your eyes."`
- **A few things tier:** short, plain, forward-action.
  ```
  Tidied up this weekend. A couple of things worth a glance:
  • Your Acme notes haven't been touched in 6 weeks — want to mark the project paused?
  • Two commitments to Sam are aging past 30 days.
  Nothing else needed cleaning.
  ```
- **Backlog tier:** a 3-item prioritized list ("this week's three"), plain English, no scores, no alarm language, forward-action lead.

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

Runs as a scheduled task (Sundays — CEO reviews Monday AM) and implements `shared/RELIABILITY.md`. Point-in-time snapshot (no missed-fire catch-up; runs at next opportunity), runs normally during OOO, 15s per-connector / 60s aggregate timeout budget. This skill is also the backup snapshotter — daily snapshots of data files to `_hq/backups/`, retained 14 days. Corrupted `entities.json` / `aliases.json` triggers auto-restore from `_hq/backups/[file]_[date].backup`; corrupted `events.jsonl` is healed via the Phase 3b recurring self-heal (quarantine, not restore — append-only).

## Gotchas

- **Phantom projects** (tracker says active, no folder) → flag or remove the stale entry; don't invent a folder.
- **Orphan folders** (folder, no tracker entry) → archive or register; never delete.
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
