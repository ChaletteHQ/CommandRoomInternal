---
name: weekly-recap
surfaces: both
description: "Pulls 7 (or 30) days of context across every connected source — Gmail/Outlook, Calendar, Slack/Teams, Drive/OneDrive, and every meeting-transcript source — into one structured recap: what happened, decisions made over email, threads of note, new people, anomalies, and what Command Room chats produced. Fires on: 'weekly recap', 'what happened this week', 'recap my week', 'monthly recap', 'what happened this month', plus 'tune weekly-recap'. Runs as the Friday-wrap scheduled chat's engine and on demand. Does NOT fire on 'operator report' / 'show me the value' (operator-report — lift accounting), 'value receipt' (value-receipt), or 'weekly insights' (insight-generator — pattern synthesis, not events digest). Owns the named forms above; cedes a bare `morning brief` to the app's own daily-brief route."
---

# Weekly Recap — 7-Day Cross-Connector Synthesis

**For:** the end-of-first-call demo moment, weekly retrospectives, or any time the user wants a substantive look back at the last 7 days. Bridges the gap between scheduled tasks (which fire on today's window only) and the customer's reasonable expectation that the system has something meaningful to say about the last 7 days.

## Skill Boundary (v2.1)

- **Use weekly-recap for:** a synthesis of the last 7 days across every connector, written as both an inline chat summary and a saved `.docx`. Captures the commitments in the freshly-captured meetings itself (Phase 3, the commit path of a commitment scan, through the access layer) so the recap's commitment counts are populated.
- **Use `morning-briefing` for:** today's window (last 18h of email/Slack + today's calendar). Daily scan, not weekly.
- **Use `cleanup` for:** workspace health check (file freshness, schema validity, stale projects). Operational, not narrative.
- **Use `meeting-notes` for:** single-meeting processing.

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. All writes must follow the File Ownership Map, Write Protocol, and Append Format defined there. JSON sources live in `_hq/data/`; markdown views in `_hq/views/` are regenerated and must not be written directly. Violations go to `_hq/CONFLICTS.md`.

**Atomic-write requirement (v2.10.5+):** ALL writes to `_hq/data/entities.json` / `events.jsonl` / `aliases.json` MUST go through `shared/scripts/atomic_write.py`. Use `atomic_append_jsonl` for events.jsonl appends — batch all 7-day capture events into a SINGLE call (one I/O round-trip, not 200).

You are an **appender** for `events.jsonl` — every connector read this skill performs emits a corresponding event (`interaction`, `meeting`, `note`, `file`) tagged with `source_skill: "weekly-recap"`. This is the backfill side effect that primes events.jsonl for the recap's synthesis.

You are the **primary writer** for:
- `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx` — the saved artifact (date is the trigger day in workspace TZ).

You do NOT write to entities.json or aliases.json. Person / project / org records discovered during the 7-day pull are queued for `people-crm` on the next turn via `pending_review: true` event annotations — never written into entities.json by this skill.

Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`. Every connector read emits corresponding events to `events.jsonl` per that contract's rules. Dedup via `source_ref_hash` makes capture idempotent across repeated invocations on the same window.

---

## Output Verbosity Rules (v2 — first-call demo quality)

Outputs must be substantively rich, not just longer. "Rich" means:

1. **More named references, not more adjectives.** Surface specific people, dates, project names, prior commitments, doc names. Never generic summary words. If a section has nothing real to surface, omit it — never pad with "no data captured yet" placeholders.

2. **Cross-references over isolated data.** Every output should connect at least 2-3 entities (this person + that project + that commitment + that older thread). Demonstrates the memory layer at work, not just data retrieval.

3. **Concrete > generic.** "Mira disagreed on sequencing on the Oct 14 call" beats "team has differing views on timing." If the data supports a concrete reference, use it.

4. **Length scales with signal density.** A project with 47 events in 14 days gets a 6-bullet "where things stand"; a project with 4 events gets 2 bullets. No padding to hit a target length.

5. **Every output ends with a clear "what now" line** — either next actions, a question, or a clean handoff to another skill. No outputs that just dump data without telling the user what to do with it.

---

## First-Run Personalization (SPEC FRP1)

This skill adopts the First-Run Personalization Protocol (`shared/FIRST_RUN_PROTOCOL.md`). Both
decisions are **show-then-tune (STT)** — the recap renders first, then one-tap changes are offered.
Read config through `get_config` — never the raw file.

Every call below goes through the access layer, beside the data (MIGRATE3-FW): run the Access preamble first (CONTRACT Rule 22 v6, the block in Phase 1 below), which resolves `$RT` and `<WS>`.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "friday_wrap_helpers:recap_config"}'
```

The answer is `{config, configured, defaults, skill_name}`. `config` is `get_config(workspace_root, "weekly-recap", DEFAULTS)` and `configured` is `is_configured(workspace_root, "weekly-recap")`. `DEFAULTS` has ONE home, `friday_wrap_helpers.RECAP_DEFAULTS`: `lens: theme_led` (narrative themes lead; `numbers_led` leads with the metrics) and `internal_backlog: split` (the internal vs external split view; `external_only` hides the internal backlog). The settings write is a WRITER and goes through the write door, arguments by name: the first-fire save hands `config` = the answer's `defaults`; a tune hands the new config and `is_reconfigure: true`.

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"config": {<the config to save>}, "is_reconfigure": <false on the first fire, true on a tune>, "skill_name": "weekly-recap", "workspace_root": "<WS>"}, "name": "skill_config_writer:save_skill_config"}'
```

`lens` sets whether the recap opens with narrative themes or with the week's numbers.
`internal_backlog=split` shows internal build work separately from external/client work;
`external_only` hides the internal backlog section.

**Mode dispatch (4 modes):**

| Mode | Trigger | Behavior |
|---|---|---|
| **Detect** (default) | "weekly recap", scheduled Friday fire | render the recap with `cfg`. On the FIRST fire only (`not is_configured(...)`): `save_skill_config(workspace_root, "weekly-recap", DEFAULTS)` BEFORE rendering, then append the first-run block. |
| **Show settings** | "show weekly-recap settings" | render current config in plain English; no recap. |
| **Tune** | "tune my weekly recap", "tune weekly-recap" | pre-filled re-questionnaire OR freeform (table below) → `save_skill_config(..., is_reconfigure=True)` → re-render. |
| **Reset** | "reset weekly-recap to defaults" | `wipe_skill_config(workspace_root, "weekly-recap")` → next fire is a first-fire again. |

**The first-run block (transport):** the on-demand fire ends in a chat summary + a `.docx` link
(not an action widget), so it uses a 2–3 line FOOTER after the recap. The scheduled Friday fire
(`orchestrator-friday-wrap.md`) appends the same footer (that orchestrator is a markdown post, not
a widget — see its first-run note).

> *First time recapping your week. I set 2 defaults: **I lead with the themes, not the numbers** ·
> **I split what you owe into work for others vs your own projects**. Say "tune my weekly recap" to
> change either, or just tell me ("lead with the numbers" / "external work only").*

The footer renders exactly once ever (`is_configured` gate). Day/time of the scheduled fire is
`change-schedule` (the friday-wrap task), not tune. The scheduled Friday Wrap fire follows
`shared/RELIABILITY.md` (skip-don't-fail when the workspace isn't ready, OOO handling,
missed-fire recovery, connector-timeout degradation) — the on-demand trigger is unaffected.

**Freeform tune (natural language → config):**

| User says | Config change |
|---|---|
| "lead with the numbers" / "metrics first" | `lens = numbers_led` |
| "lead with the themes" / "narrative first" | `lens = theme_led` |
| "external work only" / "hide my internal backlog" | `internal_backlog = external_only` |
| "show the internal/external split" | `internal_backlog = split` |

After applying: `save_skill_config(..., is_reconfigure=True)` + re-render + confirm in one line.

## Trigger interpretation

Two grouping modes — pick based on trigger phrasing, default to by-project:

| Trigger phrase | Mode |
|---|---|
| `weekly recap` / `weekly summary` / `what happened last week` / `summarize last week` | **By project** (default) |
| `recap last week by project` | **By project** (explicit) |
| `recap last week by day` / `day by day recap` | **By day** |

By-project is default because the customer's mental model is project-shaped (what happened on Acme, what happened on Northstar) more than calendar-shaped. By-day mode is for retrospective use — "what did I actually do Monday vs Friday."

## Window definition

The default window is the last 7 days — `[now - 7d, now]` in workspace timezone (`entities.json` `workspace.user_timezone`), computed at the start of Phase 1 via `shared/scripts/tz.py` `to_local(value, workspace_path=<WORKSPACE>)` (v3.11.1+ — `workspace_path` is REQUIRED). Use the resolved `<window_start>` / `<window_end>` ISO timestamps for every connector query.

**Scheduled fires widen to since-last-run (SPEC CATCHUP1 F-2).** A fixed `[now - 7d, now]` means one missed Friday is a week that never gets recapped — and the NEXT Friday's window does not reach back over it, so the gap is permanent and silent. On the SCHEDULED path (the `friday-wrap` task, `orchestrator-friday-wrap.md`), compute the window from the last successful run instead:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"cap_days": 30, "fired_via": "<the Phase 2.9 receipt_fired_via>", "floor_hours": 168, "scheduled_only": true, "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:catchup_window"}'
```

`friday_wrap_helpers:catchup_window` is `catchup_window('<workspace_root>', 'friday-wrap', floor_hours=168, cap_days=30, fired_via=..., scheduled_only=True)`, read beside the data; its answer is the call's own four timestamps and `extended`.

`floor_hours=168` is the 7-day default, so a normal weekly fire gets exactly the window it always had; `cap_days=30` matches the 30-day ceiling below. `extended: true` means this recap is covering more than a week — say so in the headline (*"the last 12 days"*, not *"this week"*), because the section counts genuinely span that period. Widening is safe: capture dedups via `source_ref_hash`, so a re-covered day double-counts nothing.

**Which clock those timestamps are in (SPEC CATCHUP1 F-1) — stated once, here, because this skill has two.** The call returns FOUR timestamps. `start` / `end` are **machine-local naive** — the clock the scheduler, `late_fire` and every receipt comparison live in (R8: scheduling math is machine-local and is never "corrected" against the workspace TZ). `start_aware` / `end_aware` are the **same two instants carrying the machine's UTC offset**.

- **Hand `start_aware` / `end_aware` to every connector query.** A connector needs an unambiguous instant, and only the offset-carrying form is one. Use them as `<window_start>` / `<window_end>` for Phase 2.
- **Never pass the naive `start` / `end` through `to_local()`.** That helper documents naive input as *ASSUMED UTC* (`shared/scripts/tz.py`), so a machine-local naive value handed to it is re-labelled as a different instant and the window slides by the machine's whole UTC offset. That is LATETZ's exact failure class — a value already expressed in one clock converted as though it were expressed in another — and it is **invisible wherever the two clocks agree**, which is every machine where the workspace TZ matches the machine TZ and every UTC CI runner. Agreement is not correctness; it is the bug not showing.
- **The aware values ARE safe to pass to `to_local()` for display.** An aware input gets converted, not re-labelled, so the headline renders the span in the CEO's timezone without moving it. One conversion, at this boundary, and nowhere else — `catchup.to_connector_iso` is where it happens and it happens once.

On the **on-demand path** no `catchup_window` call happens, so Phase 1 resolves `[now - 7d, now]` through `to_local(...)` exactly as it always did. Nothing about the manual path changes.

**On-demand triggers keep the plain 7-day window.** A human typing "weekly recap" means last week, not five weeks. That is why the call above passes `scheduled_only=True` with the fire's actual run mode — anything that is not a scheduled-context fire (`scheduled` / `catchup`) comes back as the plain floor window, including an omitted or unrecognized value (the DOGFIX1 fail-safe direction). On the on-demand path you may skip the call entirely and use `[now - 7d, now]`.

**A widened window meets caps that were tuned for a narrow one.** Phase 2's per-connector caps were sized for 7 days; this window can be 30. That is the batch-cap trap, and it is fenced by the **COVERAGE HONESTY GATE** in Phase 2 — read it before running a fire that comes back `extended: true`.

The user can override with `weekly recap from <date> to <date>` or `recap the last 14 days` — handle these by re-computing the window before Phase 1; an explicit user window always wins over both the default and any catch-up widening. Cap at 30 days max — anything longer falls outside passive-capture's intended scope, and the user should run `backfill [N] months on [project]` for a single-project deeper pull.

---

## Phase 0 — Catch the upkeep up first (HEAL1 — on demand only, and silently)

Runs after the workspace resolves in Phase 1 and **before Phase 2 pulls anything**. **Skip it entirely on a scheduled fire** — a seat whose scheduler works already runs the background upkeep on its own cadence, and a seat whose scheduler cannot reach the workspace never gets here at all.

**Never judge for yourself whether the upkeep is owed — ask, and do what comes back.**

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"surface": "friday-wrap", "workspace_root": "<WS>"}, "name": "maintenance_dispatcher:catch_up_plan"}'
```

Its answer is the plan (`surface_drivers.maintenance_catch_up(WORKSPACE_ROOT, "friday-wrap")` asked beside the data); call it `plan`. On every seat the question goes through the access layer, one verb, one call, exactly as the Access preamble describes and never hand-written.

**This is one of the two doors the WHOLE registry runs from.** A weekday briefing or day-close holds back the Sunday family — the cleanup pass, the weekly synthesis, the learning pass, the deal signals, the identity reconcile, the lifecycle pass — because a morning that opens with a multi-minute cleanup is the wrong first surface of the day. The weekly recap is the surface those jobs were always meant to land in front of, so here the plan returns everything that is due and the recap reads a settled substrate. The other door is `run maintenance`.

**Run each job by its OWN leg, not by its name (FIX3 F3-6, ruling R-RW-5).** Every row in `plan["jobs"]` carries `leg` — the whole command, already ending `--fired-via manual --triggered-by <this surface>` — and `env`, the same two answers as variables. On a legacy or local seat, paste `job["leg"]` verbatim; where the job is a SKILL rather than a script `leg` is empty, and then export `job["env"]` before running it. On a merged seat the plan line is what crosses the door and the layer forwards the same two variables. This is not decoration: on 2026-09-21 four upkeep jobs ran inside a hand-typed morning brief and all four recorded themselves as a scheduled fire, because "execute each job's skill end to end" gives a flag nowhere to go.

1. **`plan["catch_up"]` is false → do nothing, go to Phase 2.** It is false whenever the last upkeep slot was served, whenever another surface caught up minutes ago, and whenever nothing is due. Write no receipt for a false plan: an empty one would make a stopped scheduler look alive.
2. **True → run `plan["jobs"]` FIRST**, in the order they come back, one at a time, never in parallel. Execute each job's skill end to end, then score it with `maintenance_dispatcher.job_counts_as_complete(job_id, receipt_validated=…, run_reported_nothing_due=…)`. Never widen that predicate by hand.
   A script job (its `leg` is not empty) runs through the write door, ONE line per job, never pasted into a shell: `python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"fired_via": "manual", "job_id": "<the job's job_id>", "triggered_by": "friday-wrap", "workspace_root": "<WS>"}, "name": "maintenance_dispatcher:run_job"}'`. It is complete when the envelope is `ok: true` and its `result` says `ran: true` with `returncode: 0`.
3. **Then ONE receipt, and only because jobs ran:** `maintenance_dispatcher.maintenance_receipt(WORKSPACE_ROOT, jobs_due=…, jobs_completed=…, jobs_failed=…, fired_via="manual", triggered_by="friday-wrap")`, composed beside the data with its append held and landed by ONE `append_jsonl`: `python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"fired_via": "manual", "jobs_completed": [<job ids>], "jobs_due": [<job ids>], "jobs_failed": [<job ids>], "triggered_by": "friday-wrap", "workspace_root": "<WS>"}, "name": "morning_brief_helpers:plan_maintenance_receipt"}'`, then `python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "friday-wrap", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the receipt answer, in order>]}'`. One per catch-up, before anything this surface writes for itself. The cleanup pass writes its own run record as it always has; this receipt is the dispatcher's, not a second copy of that one.
4. **`plan["refused_container"]` is not empty → those jobs are not yours to run from here.** They write, and a write only happens where the files are. They stay owed and the next run from the right side of the bridge picks them up. `plan["refused_line"]` is the sentence for `run maintenance` and the health check — never for this surface.
5. **SAY NOTHING ABOUT ANY OF IT** in the recap. What the jobs DID to the customer's own rows already has a home in this surface — section 8b, in plain English, capped, with the undo phrase on the lines that carry one. The condition of the plumbing does not, and never did.

---

## Phase 1 — Setup

Resolve plugin + workspace paths (canonical CONTRACT.md Rule 22 preamble):

```bash
# >>> CR ACCESS PREAMBLE v6 (CONTRACT Rule 22; shared/WORKSPACE_ACCESS.md) >>>
# The substrate is on the customer's machine; this process may not be. Every
# read, helper and write goes through workspace_access ON the host that holds
# the data. Never open, copy or tar a workspace file into this session, and
# never write one from here.
#  1 RESOLVE, once per call. The four lines below name the plugin root, the
#    environment, and -- on a seat whose files are local -- the workspace. On a
#    merged seat resolve instead with `workspace_access.py discover`, hand the
#    block it prints to the device shell, and keep its answer: WS, RT, BRAIN,
#    MODE; and DEVICE = the entry in get_device_info's connectedFolders whose
#    last path segment is WS's basename -- export CR_DEVICE_WORKSPACE="$DEVICE"
#    before the first plan, so a saved document can name the folder the
#    customer opens. A runtime that is absent, or a runtime_version that
#    differs, is a STOP: run the update-bridge install step. There is no
#    container fallback.
#  2 BRAIN. When BRAIN is not null, `plan read` it first -- one call.
#  3 HELPERS. One verb is one call (150 s budget). Render the command ONLY with
#    `workspace_access.py plan run_helper --json '{"name":"<module:function>",
#    "args":{...}}'` and paste what it prints, verbatim -- INCLUDING the
#    variables in front of python3, which carry the writer identity and the
#    run mode to the host that holds the data. The reply is one JSON
#    envelope; ok:false is a stop, never a hand retry.
#  4 WRITES. Only `plan write` and `plan append_jsonl` -- never an append
#    redirect, an in-place edit, a heredoc into the workspace, or a python body
#    that opens a substrate file.
#  5 LEGACY / LOCAL. When this seat's files are on this filesystem -- an older
#    sandbox seat, or a Code session on the customer's own machine -- the same
#    verbs run in this shell, and the four lines below resolve it for them.
#  6 THE SURFACE IS THE WHOLE ANSWER. A step that could not run gets ONE
#    sentence with no file, script, path, variable, shell text or mechanism
#    in it -- "One step could not run here; what is below is complete." or
#    "... is partial." Never narrate a workaround, never say what you tried.
#  7 STAGING. A file this chat needs for itself -- a widget copy, a scratch
#    render -- lives in this session's own scratch, never under the
#    workspace. Nothing under `_hq/` is created, copied or removed by a
#    redirect, `cp`, `tee` or `rm`: a file is written by `plan write` and
#    removed by `plan remove`, and a removal is reported in the envelope's
#    own words -- removed, moved aside, or still there -- never as done.
#  8 WRITERS. A document, a receipt, a close or a re-pin is written by
#    `plan run_writer` naming a writer on its list -- never by importing a
#    writer in a shell. The door forwards who you are; a writer with no
#    identity on this seat refuses in one sentence, and that sentence is the
#    whole answer.
SESSION_DIR=$(echo "${CLAUDE_CODE_TMPDIR:-}" | sed "s|/tmp$||")
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_*/shared/scripts/chat_output_renderer.py /root/.claude/plugins/synced/*/*/shared/scripts/chat_output_renderer.py 2>/dev/null | head -1 | sed 's|/shared/scripts/chat_output_renderer.py$||')}"
eval "$([ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" 2>/dev/null && python3 shared/scripts/env_detect.py --shell || echo CR_ENV=unknown)"; export CR_ENV CR_PLUGIN_ROOT CR_BRAIN_FILE CR_LOCAL_FS CR_CLOCK_TRUST
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 \( -name "_archive" -o -name "_demo-framework" \) -prune -o -type d -name "_hq" -print 2>/dev/null | awk -F/ -v z=0 '{print NF, $z}' | sort -n | head -1 | cut -d" " -f2- | sed 's|/_hq$||'); [ "${CR_LOCAL_FS:-1}" = "1" ] && [ "${CR_ENV:-}" != "merged_cloud" ] || WORKSPACE=""
[ -n "$PLUGIN_ROOT" ] && cd "$PLUGIN_ROOT" || true
# <<< CR ACCESS PREAMBLE v6 <<<
```

Read, each one `plan read` of the file's workspace-relative path (`{"rel": "_hq/data/entities.json"}`), never a file opened in a shell:
- `<WORKSPACE>/_hq/data/entities.json`, primary user (resolve with `primary_user.resolve_primary_user`, asked through the door as `friday_wrap_helpers:week_facts` in Phase 2, which reads the canonical `workspace.user_id` pointer first and falls back to the legacy `is_primary_user: true` flag, never scan for the flag directly, it is unset on most real workspaces), timezone, all canonical person/project/org records (for canonicalization during capture).
- `<WORKSPACE>/_hq/data/aliases.json` — for raw→canonical resolution on every captured interaction.
- `<WORKSPACE>/CLAUDE.md` if exists — hot cache.

Compute window in workspace timezone.

**Cost budget:** target ~15-30 sec total wall-clock for Phase 2 + Phase 3 + Phase 4. If the budget overruns, fall through to degraded mode (see Phase 2 caps below).

---

## Phase 2 — Pull the resolved window from every connector (parallel reads, capped)

Every connector query below is bounded by the `<window_start>` / `<window_end>` Phase 1 resolved — 7 days by default, wider when the scheduled fire is catching up over a missed Friday (see "Window definition"). Those two values are `catchup_window`'s `start_aware` / `end_aware` on the scheduled path (offset-carrying instants — the naive `start` / `end` never cross into a connector call, per "Window definition"), and the `to_local`-resolved pair on the on-demand path. Never re-derive `now - 7d` inside a connector call: a Phase 1 window that the connector queries ignore is a catch-up that captures nothing.

Run all connector queries in parallel where the MCP layer supports it. Skip any connector whose first call exceeds a 5-second timeout — log silently and footnote at the end of the recap.

**⛔ COVERAGE HONESTY GATE (SPEC CATCHUP1 F-2) — MANDATORY whenever Phase 1 returned `extended: true`.** Every cap below was tuned for a 7-day window: 250 received + 250 sent email, 200 Slack messages, 100 Drive files, 50 transcripts, 30 sessions. A catch-up window is up to 30 days — **4.3× wider against unchanged budgets** — so a capped read can come back full while the span still holds more. Write a receipt on that and the next `catchup_window("friday-wrap")` starts *after* it: the truncated remainder is never captured, permanently, behind a green receipt. That is the same orphaning shape `past-meetings`' batch cap has, on the other surface this window widened.

- **A capped read is TRUNCATED when it comes back at its cap** and the connector does not report the result set complete. Treat at-cap as truncated; the fail-safe direction is to assume more was there.
- **If ANY capped read truncated, this fire SAMPLED its window — it did not cover it.** Two consequences, both mandatory:
  1. **The headline says sampled, not covered.** An `extended: true` recap already names the real span (*"the last 12 days"*); when a read truncated it says the span was **sampled** — *"the last 12 days, sampled — the highest-signal items across the span"* — never a bare claim of coverage over a span the budgets could not reach. A headline naming 12 days over counts silently capped at a 7-day budget is a claim/reality mismatch (the F-50 P2a class).
  2. **Phase 5's receipt carries `window_incomplete_before`**, so the next fire reaches back over the whole span instead of starting after this one. Compute it — never hand-write it:

```python
import sys; sys.path.insert(0, "shared/scripts")
from catchup import receipt_window_marker
marker = receipt_window_marker(window, incomplete=any_capped_read_truncated)
# -> ISO string to put in the receipt's `window_incomplete_before`, or
#    None, meaning OMIT the key entirely.
```

- **No `oldest_unhandled` argument here, and that is deliberate.** `past-meetings` passes one because its cap truncates a chronologically sorted list, so the oldest unprocessed meeting is a real resume point. These caps drop the **lowest-ranked** items, scattered across the whole span — there is no contiguous tail, so the only honest resume point is the window's own start. The next fire re-covers the span, which costs nothing: capture dedups on `source_ref_hash`.
- **Carry the marker forward.** If this fire captured nothing at all (every connector down, budget blown) and the window it was handed already carried a `window_incomplete_before`, pass `incomplete=True` again — the marker clamps back to the window start it was resumed from. A receipt without the field asserts *"everything before this point is handled"*; writing one while a span is outstanding **is** the orphaning bug.
- **Only a fire where no capped read truncated omits the field.** That receipt is what collapses the window back to the nominal 7 days.
- **The growth is bounded.** A window that keeps truncating keeps widening until `cap_days=30` clamps it; the per-fire cost does not grow with it, because the caps are what bound the read. The field name is `window_incomplete_before` and nothing else — `shared/scripts/catchup.py` `WINDOW_INCOMPLETE_FIELD` is the one spelling, and an improvised synonym is invisible to the reader (the F-50 P2c class).

**Pull strategy — headers/snippets by default, full bodies only for top signals.** The handoff originally specced "Gmail full bodies for project-tagged threads" — that's too slow on a busy workspace (200+ threads × 2 sec each = 7 min). Default to headers + snippets for the 7-day pull; full bodies fetched only for top 20 threads ranked by signal density (canonical-person involvement + project-tag match + thread length).

### Mail (Gmail / Outlook — native MCP, never Zapier for read)

- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire) inbox + sent
- Cap: 250 received + 250 sent (default); ranked by importance (canonical-people involved, project alias match in subject, thread length).
- Body strategy: headers + snippets for all; top-20 full bodies (those with canonical-people + project-tag).
- Emit per thread: `type: interaction`, `channel: email`, `direction: inbound|outbound`, `summary: <subject>`, `counterparty_person_ids: [...]`, `primary_thread_id: <resolved project>`, `related_thread_ids: [...]`, `classification_confidence`, `source_ref: "gmail:<thread_id>"`, `source_ref_hash`.

### Calendar (Google / Outlook)

- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire) events that already occurred (start_ts < now).
- Cap: no cap (typically <200 events / week).
- Emit per event: `type: meeting` via `meeting_capture.build_meeting_event()` (BUG-8244 — never a hand-rolled dict): top-level `person_ids` = invitees resolved against entities.json, `attendees` = every invitee EMAIL verbatim, `attendees_external` = unmatched display names, plus `duration_min` and `source_ref: "gcal:<event_id>"`. (`data.attendee_person_ids` is the retired legacy spelling — read-only, never emitted.)

### Slack / Teams

- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire). DMs + tracked channels (per `_hq/BUSINESS_CONTEXT.md` or any channel with ≥2 canonical people).
- Cap: 200 messages total across all channels.
- Emit per distinct participant-day: `type: interaction`, `channel: slack|teams`.

### Drive / OneDrive / SharePoint

- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire) modified or created.
- Cap: 100 files.
- **Names + paths + dates only — NEVER read file content here.**
- Emit per file: `type: note`, `data: {summary: "doc activity: <title>", source_ref: "drive:<file_id>"}`.

### Meeting-transcript sources (Granola / Fireflies / Otter / Read.ai / Zoom AI Companion / Microsoft Teams summaries)

Generalize across all detected MCP connectors — no Granola hardcoding. Whichever transcript-source MCPs are wired contribute.

- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire).
- Cap: 50 transcripts total across all sources.
- Body strategy: summaries by default; full text for the top-5 highest-signal (longest duration × canonical-attendee count).
- Emit per transcript: `type: meeting` via `meeting_capture.build_meeting_event()` (BUG-8244, same canonical binding as the calendar leg: `person_ids` resolved + `attendees` emails + `attendees_external` names), with `summary: <first 500 chars>` and `source_ref: "<connector>:<meeting_id>"`.

### Command Room chat sessions — what happened in your chats this week (R4)

The connectors above cover meetings and mail, but a lot of the week's real work happens in the CEO's ad-hoc Command Room chats — a decision reasoned out, a commitment made, a deliverable produced. Add that as a recap source so the week reads complete.

- Resolve the session-transcript MCP at runtime by tool-name (the server id is per-install — never hard-code it, same as the meeting-transcript sources above). List the sessions active in the Phase 1 resolved window and read each transcript.
- Window: the Phase 1 resolved window (7 days by default; wider on a catch-up fire). Cap: 30 sessions; summaries only, never raw transcript text.
- This is the SAME episodic layer the nightly `session-sweep` promotes into the event log (see `references/HOW_COMMAND_ROOM_WORKS.md` → the three-layer memory model). Read the already-recovered `session_sweep_run` history first: anything the sweep already logged is on file — surface it in the recap, don't re-append it. Only genuinely-new items get emitted, deduped via `source_ref_hash` like every other source (`source_ref: "session:<session_id>"`).
- If no session-transcript MCP is wired, skip this source silently and footnote it — same skip-not-fail posture as every other connector.

**Numeric verification (R6, `shared/SUBAGENT_VERIFICATION.md`).** The recap fans out parallel reads and synthesizes; any count it renders comes from the canonical helper, never a hand-tally of the events a sub-read returned. Commitment totals are `commitment_state.commitment_counts`, and the primary user is `primary_user.resolve_primary_user`, both asked beside the data in ONE read:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<the resolved window start, ISO>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:week_facts"}'
```

The answer is `{primary_person_id, commitment_counts, failed}`. Delivered-work counts (briefs delivered, drafts written) come from the window read's `counts_by_type` below; the value-receipt computation is not run from the wrap (it has no door form, and the operator report owns it).

### Batched append

After all connectors return, append the captured events through the one door (it takes the ledger's own writer lock and stamps each row, exactly as `atomic_append_jsonl` does), in batches of at most 50 rows per call, so no single command line grows past what a shell carries:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "weekly-recap", "rel": "_hq/data/events.jsonl", "rows": [<every captured event, in order>]}'
```

Dedup via `source_ref_hash` against the last 500 events in events.jsonl — any event whose hash already exists is silently skipped. Makes re-running the recap on the same window safe.

---

## Phase 3: capture the commitments in the freshly-captured meetings

This phase does the commit path of a commitment scan itself, through the access layer; it never hands off to another skill (fix round 1, MIGRATE3-FW: the scan skill is not on the access layer, so a wrap that ran it would run an unmigrated file on every fire).

1. **Extract.** From the meeting events this fire just captured (Phase 2's calendar and transcript legs, the text you already hold), prepare candidate commitments by the rules of `shared/COMMITMENT_SCHEMA.md` § "Extraction triggers": the capture floor (a clear owner, a clear deliverable, a real consequence; below-floor items are skipped), `data.kind` classified at capture (`promise` / `task` / `scheduling` / `agenda`), `data.due` from the source language resolved against the meeting's own date or `data.no_due: true`, the counterparty when determinable (`data.counterparty_id`, else `data.counterparty_name`), the owner resolved to a `person_id` (else `owner_id: ""` and the name in `data.owner_external`), and `data.source_ref` the meeting's own `source_ref`. This is reading text you hold; it touches no file.
   **The customer's never-track rules come first (CB-T2B-2).** Before handing any candidate on, read the rules the customer has taught, through the door:

```bash
python3 "$RT/shared/scripts/workspace_access.py" read --json '{"rel": "_hq/config/commitment-rules.md"}'
```

   When the answer is `ok: true` and carries `text`, leave out every candidate that a `never-track:` line in that `text` describes. A line describes a candidate when the candidate's title is that thing, or its owner or counterparty is that person (by name, nickname or email) or that company. A line that is a single common word describes only a candidate whose title is about that thing; a candidate that merely contains the word goes on, to step 2, where the helper may still leave it out as unsure and count it. A line that reads `low-consequence items from <name>` never leaves out a candidate that carries a due date or money; that candidate goes on. When still unsure whether a line describes a candidate, leave the candidate out: the customer's rule wins. Count the candidates this reading left out, then add the helper's own count from step 2: `left_out.never_track` plus `left_out.unsure` of every capture answer. When that sum is above zero, the recap's commitments section says so once, in one line, with the sum in place of N: *"N left out by your never-track rules."* When the answer is `not_found`, the customer has taught no rules and every candidate goes on. When the answer is `ok: true` and carries no `text`, treat it as a refusal. Any other refusal stops Phase 3 with nothing landed, and the wrap goes on with the footnote at the end of this phase. The helper in step 2 checks the same file again in code; that check is the floor under this reading, never a replacement for it.

2. **Gate and compose, beside the data.** Hand the candidates to the door in batches of at most 25 (the door refuses an argument past its argument cap, and a realistic candidate walks about 50), one call per batch. With more than 25 candidates make several calls, each followed by its own append in step 3: every batch lands once, and the append refuses a row it already holds, so no row lands twice. Each item is `{"data": {...}, "primary_thread_id", "person_ids", "classification_confidence", "org_id", "org_name"}`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"items": [<up to 25 candidate commitments>], "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:plan_commitment_captures"}'
```

   The helper runs the capture block and the relevance gate every commitment writer runs (`capture_gate.gate_commitment_data`, then `classify_capture` with the workspace's capture mode and any per-org override) and answers `{rows, n_open, n_set_aside, refused, skipped, left_out}`: an open `commitment` row for an item the owner is party to (or one carrying a due date or money, always), a set-aside `commitment_observed` row for other people's promises, each with `source_skill: scan-for-commitments` and `data.origin: connector`. An item in `refused` failed the capture block; fix what the reason names and send it again, or leave it out. Before the gate it applies the scan's two skips, beside the data: a candidate that a rule the customer taught (`never-track:` in `_hq/config/commitment-rules.md`, read by `commitment_noise.load_never_track_rules`) describes by its source, title, person, email or company, and a candidate whose source a `commitment_resolved` or `thread_resolved` row already covers. Both come back in `skipped` and are never written; do not send them again. A one-word rule the helper finds only inside a title is a match it is unsure of: it still leaves the candidate out, and `left_out.unsure` counts it beside `left_out.never_track`, so nothing a rule left out goes unsaid.
3. **Land.** ONE append per answer, every row, in order:

```bash
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "scan-for-commitments", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the capture answer above, in order>]}'
```

   The append refuses a duplicate `(source_ref, title)` and a meeting that already carries a `meeting_processed` receipt (CAPTUREONCE1), so a re-fire over the same window lands nothing twice and no ledger read is needed first.

**What this phase does NOT run from the wrap:** the scan's opening audit and its question, its dry-run preview, its closing receipt line and its whole-reply post. The wrap never asks, and the wrap's own post (5.A to 5.C) is the only post of the turn. What the scan wrote to the ledger in this step (the same open and set-aside rows, under the same `source_skill`) is exactly what lands here.

This is what gives the recap's "Commitments captured this week" section real content even on a brand-new workspace where Past Meetings hasn't run yet.

If the capture cannot run (the capture or the append answers `ok: false`, the rules read is refused for any reason but `not_found`, or every item is refused), continue without it and surface a footnote: *"I couldn't pull commitments out of the meetings this run, so the section below is surface-level only. Say 'scan for commitments' anytime and I'll go deeper."*

---

## Phase 4 — Synthesize the recap

Build the recap structure from events.jsonl over the window, **read via the org-scoped reader, never a raw load** (PGUARD1): `events_io.load_events_org_scoped(workspace_root)`, asked beside the data:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<the resolved window start, ISO>", "types": ["decision", "meeting", "commitment", "commitment_resolved"], "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:window_events"}'
```

The answer is `{events, counts_by_type, n_skipped, truncated}`: the window's rows of those types, each cut to the fields the sections use, newest first up to 60 KB; `counts_by_type` counts EVERY row of the window, so a count never comes from the trimmed list; `truncated: true` means the list was cut and the headline says the span was sampled. It applies the account-scope mask and drops personal-lane rows by design, so a reclassified personal account or a personal reminder never enters the recap synthesis. Grouping mode per trigger interpretation (default by-project).

### Sections (omit any with no real content — never pad)

> **Executive Output Standard (EXEC1, v3.20.0+).** Per `shared/EXECUTIVE_OUTPUT_STANDARD.md`: the **Headline becomes the full exec header** (verdict = the synthesis-lead sentence — weekly-recap is a sanctioned synthesis-lead surface per the standard's synthesis rule — with CHANGED carrying **week-over-week money/time deltas**), and the **"What now" section is STATEMENTS, not the ASK block** (WRAP2 4.2 item 1, 2026-09-14 — the wrap never asks, so the `asks` kwarg is not used on this surface and there is no one-tap twin; each follow-up carries the phrase that acts on it instead). The headline is still SUBSUMED into the exec header rather than rendered as a duplicate standalone section (net length must not increase).

**Exemplar anchor (SPEC OUT8).** Before composing, load the kind's structural exemplar — `exemplars.get_exemplar("weekly_recap", workspace_root)` (`shared/scripts/exemplars.py`) — and anchor STRUCTURE on it: section order, visual placement, proportions. Workspace exemplar (`_hq/exemplars/weekly_recap/`) beats the shipped seed; `None` = compose on the section list below, unchanged. **Contract beats exemplar beats default** — an exemplar never licenses skipping the exec header or any gate, and it anchors structure, never facts: no name, number, or claim from the exemplar may appear in the recap. After saving the .docx, run `exemplars.scan_docx_for_exemplar_tokens(docx_path, exemplar["text"])`; a finding means exemplar placeholder content leaked — fix the sections payload and re-save AT MOST ONCE (the visual-pass posture, warn-only). When the user gives structural feedback on a delivered recap ("make it like this", reorder/drop a section), capture it with `exemplars.append_structural_correction(workspace_root, kind="weekly_recap", direction=..., section=...)` — capture only; the exemplar itself is updated by the weekly `learning` job's exemplar leg, automatically at the shipped floors and narrated in the morning brief with a one-word undo (`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The exemplar anchor").

**1. Headline → exec-header VERDICT (EXEC1).** One sentence framing the week, as the verdict; CHANGED = week-over-week money/time deltas ("pipeline +45K dollars WoW; 2 deals slipped a week"). Pull from the dominant signal: "Heavy week on [Project A] (47 events) and [Project B] (31 events); 3 new people surfaced; 4 commitments captured." This is the exec header, not a standalone "Headline" section.

Apply the Universal writing standards in `shared/VOICE_CALIBRATION.md`. The headline names a dated anchor moment AND what changed — not a bare metric count.
```
GOOD: The May 14 CEO-group talk was the anchor, 45K dollars of early pipeline, two
      booked demos, and it opened the non-profit logistics wedge now running
      as a thread. Everything else this week supported it.
BAD:  This week had a lot of activity. Closed some deals, shipped features.
BAD:  This week saw 6 commitments, 4 decisions, 15 emails.   (metrics, no meaning)
```
Test: does the lead name a dated moment AND say what CHANGED? If the counts alone tell the same story, rewrite for shape, not numbers.

**2. Top decisions made** — pull `type: decision` events from the window. Bullets, each with: decision text + project + date + who participated. Omit if zero.

**3. Your plate this week — the delta + the PARKED review (SPEC PLATE1 night 2, D7 `wrap`).** ONE call, and it owns every word of this section:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<the resolved window start, ISO>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:plate_cut"}'
```

`friday_wrap_helpers:plate_cut` is `plate_view.wrap_cut(workspace_root, since_iso=..., now_iso=...)` through the WRITE door (building the plate mints its display numbers under the ledger lock). Call its answer `plate`: `plate["text"]` renders VERBATIM in the chat body; `plate["docx_section"]` is `plate_view.wrap_docx_section(plate)`, the `.docx` section, VERBATIM (WRAPSAVE1); `plate["refused"]` is true on a workspace with no resolvable owner: render `plate["line"]` (one plain sentence) and nothing else (D8). Keep the `since_iso` and `now_iso` you passed: 5.C hands the same two back.

`text` is `plate_view.render_plate(build_plate(ws, since_iso=…), "wrap")`: *"Your plate this week — N opened · N closed · N slipped"*, then the rows that entered the plate this week in their blocks (DO IT / CHASE / WAIT / SCHEDULE / CONFIRM / PARKED), the closures (done / dropped), the rows whose date passed this week and are still open, then **PARKED — still on your plate?** (every resting row, open, with its reason — P2: never hidden, this is the weekly "still on your plate?" sweep), and one pointer to `what's on my plate`. Same model and same words as the plate, the morning brief and the day-close. The pre-PLATE1 by-hand split ("you owe / they owe", `_commitment_field(ev, "owner_id")` over the window's `commitment` events, 10 per direction) is RETIRED — never re-derive the section from the raw events, never re-split by owner, never add a count the helper did not print. No verbs render here (the recap is prose; verbs live on the plate). Drop the section only when `text` is empty (it never is on a resolved workspace — an empty week renders its own one-line form).

**BYTE-EXACT, EVERY PARKED ROW — and it is checked in code (CUT-PLATE, 2026-09-06).** The v5.28.0 attended test (B2.3) saw this section rendered as one sentence — *"171 on the plate now, 38 parked, most untouched 36–54 days"* — and not one Parked row. That is the defect: `plate["text"]` is relayed line for line, never summarised, never re-counted, never trimmed to "the notable ones", and the PARKED review lists every resting row with its reason. *CORRECTION, 2026-09-15 (re-verification F-5): the standalone `plate_view.wrap_relay_check` call that stood here is retired.* **`quiet.wrap_post` (Phase 5.A) runs it**, over the FINAL text of the chat turn, and raises `WrapRelayError` naming the first missing line — so this rule is now enforced at the same door as the ask fence and the score fence rather than as a step of its own that the model could skip. A raise means a line of the plate cut — usually a Parked row — did not make it into the post: put the lines back and re-run. Never paraphrase a Parked row, never fold the review into a count. The same rule reaches the `.docx` section, which carries the same text.

**THE `.docx` SECTION IS BUILT BY `plate_view.wrap_docx_section(plate)` — NEVER by hand (WRAPSAVE1, ATTENDED_TEST_v5.29.0 B2.3).** The v5.29.0 wrap did not save at all: the plate cut was handed to `brief_writer` as a section `body`, and the em dash that separates a row's title from its counterparty, its due phrase and its reason is `dash_as_punctuation` — a fail-severity rule that blocks a save on every brief kind (SPEC DASHBAN). The gate refused, so the weekly document never appeared. The helper hands the same lines to the writer as the LIST they are (`bullets`), which is the surface both the voice gate's structural scan and `dash_rewriter` deliberately leave alone (SPEC B2 §8) — so the words are byte-identical, `wrap_relay_check` still passes over them, and the save goes through. Do not re-wrap the cut as prose "so it reads better": that is the exact change that stopped the document saving.

```text
plate_section = plate["docx_section"]      # plate_view.wrap_docx_section(plate), composed beside the data
# {"heading": "Your plate this week", "bullets": [...], "user_spans": [...]}
```

**HAND THE SECTION OVER WHOLE — never rebuild it key by key (WRAPSAVE1, REVIEW_ONEPLATE1 F-1/N-3).** `wrap_docx_section` returns THREE keys, and the third one is load-bearing. `user_spans` is the list of strings your customer's counterparties actually wrote — the promise titles. `brief_writer.make_brief` reads `section["user_spans"]` and hands them to `docx_leak_scanner`, which then blanks a banned MARKETING word only where the renderer wrote it, and leaves it alone inside somebody's own sentence. Copy the `bullets` across on their own and one promise that says "synergy proposal" — a word the counterparty chose, not one Command Room wrote — refuses the whole weekly document, exactly as v5.29.0 did. Every other leak family still reads the document in full, so nothing is weakened by carrying the key. Put the whole dict into `sections` (`sections.append(plate_section)`); if you are writing the JSON by hand, write BOTH `bullets` and `user_spans`.

**4. Notable meetings** — rank by (duration × attendee count × first-touch flag for new attendees). Top 6. Each bullet: title + date + duration + attendees + project routing + one-line topic if transcript summary exists.

**5. Email threads of note** — high-priority unresponded (>72h since last inbound from canonical person + project-tagged) + decisions made over email (threads where the body contains decision-language patterns). Cap at 5.

**6. New people surfaced** — anyone interacting with canonical people during the window who isn't yet in `entities.json` as a person record. Flagged as `pending_review: true`. Surface name + how they appeared + suggested next action ("`tell me about [name]` to pull a full profile").

**7. Anomalies** — projects that went quiet vs. their normal cadence (no events but historical mean > 5/week), unusual cadence shifts on people (weekly→silent), new domains in email that don't map to known orgs.

**8. The grouping breakdown** (default mode) OR **By-day breakdown** (explicit mode).

  - **The heading and the grouping key come from the reader's settings, not from this prose (CUSTOM2).** Call `brief_settings.wrap_group_section(WORKSPACE_ROOT)`; it returns `{"heading", "group_by"}` read off the SAME `organization` setting the morning brief and the day-close read. Use `heading` as the literal section heading and group by `group_by`. A workspace that has never changed it gets `By project` — the shipped heading, unchanged. A reader who said "group my brief by workstream" gets `By workstream` here too, from that one sentence, without being asked again.

    ```bash
    python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"workspace_root": "<WS>"}, "name": "friday_wrap_helpers:group_section"}'
    ```

    The answer is `brief_settings.wrap_group_section(WORKSPACE_ROOT)`, read beside the data: `{"heading": "By workstream", "group_by": "workstream"}`.

  - **Per group:** for each group with ≥3 events in the window, render: group name + event count + status badge + one-line "where it landed" + top open commitment. Sort by event count desc.
  - **By-day:** for each day (Mon → today), render: top 3 events (meetings, key emails, decisions) — one bullet each. Sort chronologically. An explicit `recap last week by day` still wins over the setting — a phrase the reader just typed outranks a standing preference.

**8b, 8b-bis and 8c are ONE read beside the data (MIGRATE3-FW):**

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<the resolved window start, ISO>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:week_sections"}'
```

Its answer carries `change_lines` (8b's `texts`), `open_proposals` (8b's pointer count), `decided_for_you` and `still_waiting` (8b-bis's two texts) and `objective_rows` (8c's `recap_rows`), each composed by the helper named below and each rendered VERBATIM; a part named in `failed` is simply absent, never narrated.

**8b. What the system did this week (SPEC LB1 — the change-feed roll-up).** One compact block, composed by `narration_names.recap_change_lines(WORKSPACE_ROOT, <window start>, now_iso=<now>, skip_categories=["closed_from_meetings", "closed_from_sent", "unconfirmed_expired", "proposals_retracted", "orgs_promoted", "let_go_backlog"])` (`shared/scripts/narration_names.py` — it wraps `change_feed.changes_since` and substitutes every internal id with the entity's NAME at composition, CUT-C item 8 / ATTENDED_TEST_v5.28.0 B2.3: `project_011` reached this section as prose); render its `texts` verbatim — the week's totals in plain English — items recovered from ad-hoc chats, people added / linked, facts noted, proposals confirmed/declined, undos, maintenance. **Skip the categories 8b-bis owns** (REVIEW_QUIET1 F-4 — one act, one line): `closed_from_meetings`, `closed_from_sent`, `unconfirmed_expired`, `proposals_retracted`, `orgs_promoted` render ONLY inside "Decided for you", never here. Render the remaining feed lines verbatim (they carry the counts), cap 4, substance first; append one pointer when open proposals remain (`brain_proposals.card_health_counts` — *"[N] suggestions are waiting on you — say `staff meeting` to review them."*). Drop the whole section on an all-zero week — never pad. This is the value-narration slot: what running Command Room DID, distinct from what the CEO did. **The `let_go_quiet` line is never dropped by the cap (EXIT1, SPEC_FLOW1 Lane B item 3).** It is the one line that reports items LEAVING the list unfinished — *"Let go N items you never touched — say `undo` to put them back on the resting list"* — and a customer who is not told cannot reverse it. The line names WHERE the `undo` puts them (UNDOLAND1, M's ruling 7b): a put-back let-go goes back on the RESTING list with its quiet clock restarted at the undo, not onto the working list, so the sentence must not read as a promise of the latter. If the cap of 4 would cut it, cut the least substantial line instead and keep this one. The sibling lines (`closed_from_your_word`, `closed_from_deal`, `rested_quiet`) take their chances with the cap like every other line: they report items being finished or rested, both of which are reversible and both of which the customer sees on the plate.

**8b-bis. Decided for you / Still waiting (SPEC QUIET1 D6 — the one optional touchpoint).** One helper, rendered VERBATIM like the plate cut: `quiet.wrap_sections(WORKSPACE_ROOT, since_iso=<window start>, now_iso=<now>)` (`shared/scripts/quiet.py`). It returns two texts, each `""` when there is nothing to say — print neither heading yourself:

  - `decided_for_you.text` — the heading `## Decided for you this week` plus what the brain settled in the window on its own: the closes on evidence, the lapses, the withdrawn questions, the promotions, the rows parked with a reason — each line carrying its own `undo` phrase as the feed wrote it — and a batch line ONLY for a change the feed has no line for (today: a preset stamp). This helper is the SINGLE owner of those categories (8b skips them), so one act is narrated exactly once. Missing this section breaks nothing: nothing in it waits on the reader, and every line is one `undo` away. At merge, POLICY1-B's per-group `undo <group>` lines slot into this same block, not beside it.
  - `still_waiting.text` — one sentence: how many questions asked this week are still open on the reader, and how many took their default rather than ask (the weekly budget: at most 5 a week under the default posture). Never a row list; the questions themselves live on the meeting cards and the morning brief.

  Do not add a count the helper did not print, and never re-derive either number from the ledger yourself — the helper reconciles to the same change feed 8b reads.

  **NAME THE ACTS, don't only count them (SPEC SURFACEFIX1 5.6 / amendment W-3, 2026-09-13).** M read this section on two wraps and it said *"Withdrew 229 unanswered questions"*, *"Answered 1 question"*, *"1 took its default"* — counts, naming nothing (B2.7). A reader who cannot see WHICH row moved cannot judge whether the machine was right, and "229" is not something anybody can check. So `decided_for_you.text` carries the NAMED form too: one line per act, **the row's own title, the door that moved it, the date** — *"Send Sample Co the revised scope note — the review door, Aug 12"*.

  **ONE PRODUCER, AND IT IS `quiet.wrap_sections` (WRAPSTAFF1 4.3, 2026-09-17).** This section used to run a SECOND python block of its own — `change_feed.decided_for_you` — beside the helper's return, and the two together were the block. On 2026-09-15 the four-day door retracted 88 superseded suggestions in one quiet batch and the wrap named none of them (ATTENDED_TEST_v5.31.0 B2.7), because a producer a skill has to remember to run is a producer that does not run. `wrap_sections` now composes the whole block — the feed's counts, the batch lines, the expiry lines AND the named acts, batch acts included — and **you render `decided_for_you.text` verbatim and run nothing else.** Do not re-derive, re-cap or re-order it.

  What rides in it, so you can tell a defect from a design: the four-day retraction of superseded suggestions is a BATCH act — a whole pile stops being offered at once, the job writes one receipt per pile and no per-row event — so it arrives as one line carrying its own count, *"Stopped offering 5 old suggestions that a decision was reversed in a later meeting — the question door, Sep 14"* (the same sentence the maintenance receipt uses — one constant, `question_ttl.RETRACTION_SENTENCE`). It offers no `undo`, because nothing was written for an undo to reverse. `question_ttl.decided_for_you_lines` renders NO retraction sentence (trial merge MF-6); the *"Withdrew N unanswered questions…"* line is a DIFFERENT act — the per-row dismissals of the review chips, counted by the feed — and stands beside the batch line; the two never count the same rows.

  The named list is capped at five (`quiet.DECIDED_NAMED_ACT_CAP`, M's ruling of 2026-09-17) and says *"and N more decided for you."* when the window held more — in code, not by you. Five, not the Parked block's ten: these lines are evidence for the counts above them, not work the reader has to do. Never a wire id, never a batch id, never a skill name; the counts above are the whole picture and these lines are the evidence for them. **Which five** is the plate's own `importance_key` — money in the words first, then the most recent — and "most recent" is the act's place in the ledger, never the date the line prints (review N-1, fix round 3; `quiet._act_sort_ts`). A batch act is never cut and always leads.

  **A reversed act is named NOWHERE.** `decided_for_you` does no fold of its own — it reads the same classification `changes_since` gives 8b, which folds out every act an `undo` reversed before the render. That is deliberate: one fold, in one place, so this section and the counts above can never disagree about which acts stood. An act M put back on Wednesday does not appear here on Friday, and it does not appear with an undo offer either. **This became true of the park and rest doors only in fix round 1** (reviewer F-4): those two sit on `commitment_updated`, which the closer fold never covered, so until then the wrap named rows M had already un-parked. The fold they take now is `closure_index.reversed_act_positions`, the same one the promotion door has always taken.

**8b-ter. The measure — ONE line (SPEC_FLOW1 Lane D / MEASURE1).** One helper, one sentence, rendered VERBATIM:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<window start, ISO>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:measure_line"}'
```

The helper runs `from flow_measure import wrap_line` and answers `wrap_line(WORKSPACE_ROOT, since_iso=..., now_iso=...)` (through the WRITE door: its plate number comes off the plate's own projection, which mints display numbers). Call it `measure`: `measure["text"]` -> render VERBATIM as its own line; `""` -> render nothing.

It reads *"This week: 38 came in, 61 went out, 256 on your plate."* — what arrived, what left, and what is still on the plate. That is the whole section: **one line, no heading, no list, no second sentence.** This is the one place the wrap is allowed to get longer, and it got longer by exactly one line, because a plate that is supposed to be shrinking has to be measured somewhere the CEO actually looks.

- **Never re-derive it and never extend it.** The `on your plate` number is `plate_view`'s own count, arriving through the helper — the same number the plate section above already printed, so the two can never disagree. Do not add a count the helper did not print (no "of which N overdue", no route breakdown, no page count, no percentage, no week-over-week arrow). The routes a row left by, and the touches the week asked of the reader, live in the operator report — not here.
- **A number the helper leaves out stays out.** The sentence names only what it can observe; if the plate has no number to state, the sentence simply does not mention the plate. Never write a zero in place of an unknown.
- `""` back means the week saw no movement and the plate had nothing to state — render nothing at all, never *"This week: nothing came in."*

**8c. Objectives — WHERE THEY STAND, and nothing is asked here (SPEC OBJ1; rewritten 2026-09-14, SPEC_SURFACES2_11c WRAP2 4.2 item 1 + ruling R-6).** This section used to call itself *"the ONE place objectives ask for anything"* and to instruct a second sub-part that asked it; both are retired, because the wrap never asks. Drop the whole section when no open objectives exist. Otherwise, a fast substrate-only read: `objective_math.load_objective_inputs` → `compute_objective_health` → `recap_rows(health, names_by_person_id=<people map>)` for the status block (one line per objective, worst-first, rendered verbatim — the helper owns status honesty). **Never call `due_self_reports(...)` here** — that readout is the on-demand `objectives` skill's, and `quiet.assert_wrap_never_asks` refuses the ask in code either way. ONE sub-part, drop-empty:

  - **Where they stand:** the `recap_rows` lines, capped at the `active_cap` (they are a short list by design).
  - **The due self-reports are ON DEMAND — the wrap does not ask for them (SPEC_SURFACES2_11c WRAP2 4.2 item 1, ruling R-6).** Until 2026-09-14 this section carried a second sub-part, "Your word, batched", which numbered the due objectives and asked *"20 seconds: how do these stand?"* on a surface with no reply path. That was one of the two questions M's design rule of 2026-09-06 says the wrap may not carry, and it went out on every fire for months. **Do not render it here.** Render `recap_rows` and stop. The whole due-self-report flow — the numbered list, the ordinal receipt (`log_receipt(WORKSPACE_ROOT, "objectives", ..., extra_data={"due_thread_ids": [...]})`), the graceful-death ask and the reply parser — belongs to the **`objectives` skill**, which writes the receipt when IT renders the list, on demand. The wrap writes no `objectives` receipt and numbers nothing. A reader who wants to give their word says `objectives`.

    The fence is in code, not only here — and since 2026-09-15 that is true of this section too (REVIEW_NIGHT11C H-6). Phase 5.A hands the WHOLE post to `quiet.wrap_post`, which runs the ask fence, the score fence and the plate relay check over the final text and returns it. These `recap_rows` lines are inside that text, so a question mark here refuses the post. Before H-6 the only code caller of the ask fence was the week-against-the-word composer, over blocks it had written itself: this section was fenced only if the model ran a snippet.

**8d. The week against the word — ONE composer, and it owns every word of five sections (SPEC_SURFACES2_11c WRAP2).** One call, rendered VERBATIM like the plate cut and the measure:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"now_iso": "<now, ISO>", "since_iso": "<the resolved window start, ISO>", "what_now": [{"phrase": "<the phrase that acts on it>", "text": "<a statement>"}], "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:coaching_blocks"}'
```

The answer is `quiet.wrap_coaching_blocks(WORKSPACE_ROOT, since_iso=..., now_iso=..., what_now=[...])`, composed beside the data through the WRITE door (next week's three come off the plate, which mints its display numbers). Call it `wrap2`: `wrap2["text"]` renders VERBATIM in the chat body; `wrap2["docx_sections"]` are the `.docx` sections, handed over WHOLE (5.B); `wrap2["receipt_extra"]` merges into the ONE pack_run receipt (Phase 6); `wrap2["offer_pattern_key"]` is what Phase 6 closes after the post. When one of its fences refuses, the answer is `{ok: false, reason, line, detail}` and the refusal's ONE `surface_failed` receipt has already landed: `detail` names the sentence to rewrite as a statement (never posted), and when it cannot be put right `line` is the whole turn.

What it renders, each part drop-empty:

  - **Next week** — the three highest-importance open rows due by the end of next week, as ONE statement line ("Next week: the Stone scope note (Tue) · …"), ranked by the plate's own `importance_key` so overdue comes first. **Stated outranks derived:** a `next week is about X` the reader typed (a `day_intent` for next Monday, written through `quiet.write_next_week_intent`) replaces the derived three, in their own words. Never a question, never a widget.
  - **Against last Friday** — the accountability report: each of LAST Friday's three, as the ledger has them (closed by the reader's own word, closed on the record, moved to a new date, let go through the silence door, still open). Every line carries the ledger seq behind it through `claims.make_claim`, and `claims.assert_surface_resolved` runs over the section before it is returned. **Absent on the first wrap** — no prior receipt means nothing to report against — and ONE line when a prior receipt exists and nothing moved.
  - **In evidence this week** — NAMED/COACHED seats only (`coaching_doors.coaching_shape`): where the behaviour the seat named showed up, as counts and the reader's own self-scores as a LIST. Never a mean, never a trend word, never a score of the person (`eod_synthesis.assert_no_score` runs over the sentence). An OBSERVED seat gets nothing here — not a shorter version, not a teaser.
  - **The bigger picture** — at most three sentences off the week's End-of-Day packs: the arc that moved most, the arc that never moved. Nothing below `claims.FLOOR_INSTANCES` (three) renders, and an arc the wrap cannot NAME in the reader's own words is not talked about at all.
  - **Worth working on** — the earned door, raised ONCE, ever: a pattern the evening coach has kept three times gets one STATEMENT with a phrase ("Say `coach me on X` and I will work it with you; say nothing and I will not raise it again"). The offer row is written **after the post**, in Phase 6, by `quiet.wrap_record_offer` — not at compose. Silence really does mean never again, so the door may only close on a line the reader actually saw: everything between composing and posting can still refuse (the ask fence, the score fence, the claims fence, the relay check, the leak gate), and a door closed before those is a door the reader never got to walk through (REVIEW_NIGHT11C H-1, 2026-09-15).

Do not add a line the helper did not print, do not re-derive any of it, and do not re-word it "so it reads better" — the same rule the plate cut and the measure carry, for the same reason.

**9. What now → STATEMENTS, never an ASK block (WRAP2 4.2 item 1).** Up to three follow-ups, each rendered as a statement carrying the phrase that acts on it — *"The Stone call is the one to prep — say `prep me for it`."* Composed by `quiet.what_now_statements` and returned inside `wrap_coaching_blocks`. **No `asks` kwarg, no one-tap widget, no question.** This block used to be the EXEC1 ASK block ("which commitment is closest to overdue?"), which is the second of the two questions the wrap is no longer allowed to ask.

### Format constraints

- **Length scales with signal density.** A quiet week with 4 meetings and 12 emails produces a 20-line recap. A heavy week with 47 events on one project produces 50-60 lines. **Never pad to hit a target length.**
- **No placeholders.** Sections with no content are omitted entirely, not stubbed with "No data captured."
- **Named references > adjectives.** "Mira disagreed on sequencing on the Tue Oct 14 call" > "team has differing timing views."
- **Cross-reference at least 2-3 entities per bullet.** This person + that project + that commitment.

---

## Phase 5 — Output: dual surface (inline + .docx)

Per `shared/CONTRACT.md` Rule 3 dual-surface pattern.

### 5.A — Inline chat summary

Post the synthesized recap as a markdown chat turn body. Target ~30-60 lines for a typical week (lower bound for quiet weeks, upper for heavy). Use the section structure from Phase 4. Scan-friendly: bold project names, dates in `YYYY-MM-DD` or `Mon DD` format, named people in plain text (no entity-ID leaks). **The length target never trims the plate section**: Phase 4 §3's `plate["text"]` is relayed byte-exact (CUT-PLATE). *CORRECTION, 2026-09-15 (re-verification F-5): the standalone `plate_view.wrap_relay_check` step that stood here is retired — `quiet.wrap_post` below runs it, over the FINAL text, and raises `WrapRelayError` on a cut that was summarised. Do not run it twice and do not run it instead.*

**MANDATORY — THE POST GOES THROUGH ONE DOOR, AND YOU RENDER WHAT IT RETURNS (REVIEW_NIGHT11C H-6, 2026-09-15).** The wrap never asks and never grades (M's design rule of 2026-09-06), and the plate cut is relayed byte-exact (CUT-PLATE). All three used to be separate steps the model was told to run; now they are one call over the FINAL text of the chat turn, and **the thing you post is its return value**:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"relayed": "<plate text, the cut, byte-exact>", "spans": [<every user_spans entry of every dict in wrap2 docx_sections>], "text": "<post_text, the FINAL text of the chat turn>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:wrap_post"}'
```

The helper runs `post_text = wrap_post(post_text, relayed=plate["text"], spans=[...], workspace_root=WORKSPACE_ROOT)` (`quiet.wrap_post`) beside the data, and its answer is: `{text, rows}` when the three fences pass, and `text` is the new `post_text`. When a fence raises the answer is `{ok: false, reason, line, detail, rows}`: `rows` holds the ONE `surface_failed` receipt the raise wrote under the `friday-wrap` task (re-verification F-3, 2026-09-15: without it the watchdog reads a wrap that refused to post as a job that never fired), and it lands by ONE `python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "weekly-recap", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows>]}'`. `detail` names the sentence to rewrite and is never posted.

It replaces the standalone `wrap_relay_check` step above and the separate ask-fence call. Never run it over one block, never post over a refusal, and never post a `post_text` you did not get back from it.

**AND NOTHING IS POSTED AFTER IT (SPEC FIXTRAIN v5.31.0 6.1, R-25 — the trailer sentence).** `wrap_post` reads the FINAL text of the turn, so a sentence typed after its return is a sentence it never saw. On 09-15 the recap posted clean and a diagnosis paragraph followed it, naming a workspace file, "plate helper", "byte-exact relay rule" and "build queue" (recorded leak instance 5) — the chat named the disease and leaked while doing it. **`wrap_post`'s return is the whole turn.** A turn that would carry anything more hands the WHOLE text — that return plus whatever was going to be added — to the leak gate's own whole-turn door first, and posts what comes back:

```text
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"docx_rel": "<the landing's rel>", "label": "Weekly Recap <date_iso>", "now_iso": "<the now_iso plate_cut was given>", "since_iso": "<the since_iso plate_cut was given>", "text": "<post_text plus any trailing text, never a link>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:post_turn"}'
```

(The shape only, never run here: the document has not landed yet and 5.C makes this call once.)

`friday_wrap_helpers:post_turn` makes this call beside the data: `final = post(post_text + trailing_text, surface="wrap", workspace=WORKSPACE_ROOT, relayed=plate["text"])` (`surface_composers.post`).

The relayed text is the plate CUT, not the whole post: `wrap_post` runs the ask, score and relay fences and does NOT run the leak gate, so declaring its return would blank the entire wrap out of the copy the internal-name classes read. The door vouches a relay line for line against what a composer produced IN ITS OWN PROCESS, and a door never stamps what it is handed (RE-VERIFY_LEAK4 P-1), so `post_turn` asks `plate_view.wrap_cut` again with the SAME `since_iso` / `now_iso` and relays that return (the numbers are already minted; nothing new is minted). 5.C makes exactly this call, once, after it appends the footer and the heading link. This paragraph is the SHAPE; 5.C is the call (RE-VERIFY_LEAK4 Q-6).

This also replaces the standing "run the narration scan last, on the whole post" step below with a value you have to render: the scan was already the right rule and it was already written down, and it was still a step a turn could finish without taking.


**Both `relayed` and `spans` are mandatory, and they are not the same thing.** `relayed` is the plate cut, relayed byte-exact, where a customer's own row title can end in a question mark ("Ask Quinn whether the date still works?"); `spans` is every `user_spans` entry §8d's composer hands back, because those sections are BUILT AROUND customer-authored titles rather than relaying them whole — leave them out and one such title takes the entire wrap down (REVIEW_WRAP2 F1). Both are blanked out of the post before the fence reads it; every OTHER word is the product's own and may not carry a question mark at all, including one composed around an exempt span. If it raises, **rewrite the sentence as a statement with a phrase** — never catch the error, never drop the fence, and never move the question to the `.docx` (the same fence runs over the joined sections there). A `WrapRelayError` means the post dropped or reworded a line of the plate cut: put the cut back verbatim.

**MANDATORY narration scan (CUT-C item 8, mirrors apply-choices Step 4) — RUN IT LAST, ON THE WHOLE POST.** 5.C appends the heading link and 5.D may append the closing line AFTER this section composes, so the scan runs on the FINAL text of the chat turn, immediately before posting, never on the 5.A body alone. Run `validate_chat_output(<the whole final chat turn, including anything 5.C and 5.D appended>)` from `chat_output_renderer.py`. It raises `LeakDetectedError` on a raw id (`person_NNN`, `project_NNN`, `org_NNN`, a `cmt_`/`bp_`/`pcand:` wire id), an event or field name, a file name or path, a score. ABORT the post and rewrite the offending sentence with the entity's name (`narration_names.humanize(text, narration_names.name_index(WORKSPACE_ROOT))` is the one substitution). NEVER catch the error and post anyway.

### 5.B — Saved `.docx`

Generate the saved artifact via `shared/scripts/brief_writer.py` per the canonical brief-writer pattern (same as `call-prep` skill):

- **The file is BUILT in this session's scratch and LANDED in the workspace by the access layer (SPEC_NIGHTM2 §5, `shared/scripts/deliverables.py`).** The render call returns where the document is on the CUSTOMER'S OWN COMPUTER — the path you handed in, byte for byte, on a seat where the plugin and the folder share a filesystem, and the customer's own spelling on the merged seat. Link THAT RETURN and pass THAT to any receipt: never a path you re-derive or re-spell yourself, and never the landing's `landed_path`, which on a merged seat names the mount this run reads through and opens nothing on their machine. When you know the workspace folder's absolute path on their computer — a scheduled chat is given it, and `get_device_info` returns it — export it as `CR_DEVICE_WORKSPACE` before the call and the returned path is absolute; without it the return is the folder-relative path, `pc_path_unknown` is set, and you say where the document went in words with no link at all. When the layer cannot reach the folder the call raises `deliverables.DeliveryRefused`: say its one sentence — it is chosen for that reason and it is the whole answer — and stop. Nothing was written anywhere, so there is nothing to clean up and no second way to save it.
- **NEVER hand-roll the recap** with the generic `anthropic-skills:docx` skill, `python-docx` directly, or docx-js. Those paths bypass every gate and ship a substandard or leaking recap (the v3.20.0 failure mode) — and this one fires on a schedule, so a bypass here is not one bad document, it is a standing weekly one nobody is watching.
- **NEVER create, render, copy, upload, or update the recap — or any part, derivative, or restatement of it ("the week in numbers", "a summary") — through Claude Docs (the built-in docs / artifact page), Google Docs, Google Drive, or ANY other document/file connector** (Slides, Sheets, Notion, OneDrive, Dropbox: the ban is on the connector delivery path, not one vendor's API quirk). It fails twice at once: the connector path bypasses every gate above, AND a connector-created file lands at that connector's default location with no folder control — for a Google Doc, and for a parentless Drive upload of the canonical `.docx` itself, that is My Drive root, not `_hq/meetings/` (the 2026-07-24 root-drop incident). Not exceptions: "for mobile", "so the team can read it Monday", "as a copy alongside the canonical file" — **nor a direct instruction**: "put this week's recap in a Google Doc so I can send it round" is a request this gate refuses, not an override. Hand back the `.docx` link and let the user forward the file itself.
- **A Claude Doc produced anyway is exported into the folder (DOCS1 D-2, 2026-09-24).** When the host's built-in docs skill made a page despite the document-routing rule, the page is not the deliverable and is not left as the only copy: discover the docs seam with `tool_discovery.discover_docs_tool(<the tool ids visible in THIS session>, "export")` — never a remembered tool id; `None` means no docs tools in this chat, say so in one line and stop — then call the discovered tool for that doc's ONE tab with `format: "docx"` (a doc with several tabs: `read` the doc first and take the tab that holds the document). Put the payload — `{"doc_ref": "<the doc's link>", "kind": "weekly_recap", "title": "<the doc's title>", "content_base64": "<what the export returned>", "format": "docx", "workspace_root": "<WS>"}` — as JSON into THIS SESSION'S OWN scratch (never under the workspace; a document does not survive a pasted command line — the same carrier the prep's payload uses) and land it through the WRITE door: `plan run_writer` naming `deliverables:export_claude_doc` with `args_file` pointing at that file. Its answer names the file by `rel` and carries `opener_line` — print that verbatim — and `receipt_row`, which you append through `plan append_jsonl` to `_hq/data/events.jsonl` (ONE `deliverable_landed` row; `null` means the same doc was exported inside the window and the file was refreshed in place — append nothing). The doc itself stays where it is: the product never deletes what it did not make. The gates above still bind — an exported doc is a copy of what the composer already said, landed where the workspace can see it, not a second render.

The meetings folder is made through the WRITE door, then the path is asked beside the data (MIGRATE3-FW). `date` is the trigger day in the workspace timezone (`CR_TODAY` on a scheduled fire, the Phase 2.9 `clock["today"]`); an on-demand fire with neither passes an empty string and the helper takes the workspace's own today through `tz.to_local`:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"workspace_root": "<WS>"}, "name": "brief_path:ensure_brief_directory"}'
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"date": "<YYYY-MM-DD in workspace TZ, or empty>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:brief_path"}'
```

The answer is `{brief_path, rel, date, brief_url, session_scoped}`: `get_brief_path(ws, 'weekly_recap', '', date_iso)`, `get_brief_artifact_url(path)` and `is_session_scoped_path(path)`. Below, `BRIEF_PATH` is `brief_path` and `BRIEF_SESSION_SCOPED` is `session_scoped`.

**If `BRIEF_SESSION_SCOPED=True`** (v5.9.2, platform-neutral v5.11.1 — cloud-mounted workspace: Google Drive, OneDrive, or SharePoint; the `computer://` BRIEF_URL will fail with "Failed to load local file." on the customer's machine — this exact surface, QMG field report 2026-07-31; the OneDrive/SharePoint gap, 2026-08-11 / BUG-8538): after the recap is written and synced, look up its web link on the workspace's OWN cloud platform — discover the drive tool with `tool_discovery.discover_drive_tool(tools, "search", prefer_platform=tool_discovery.infer_workspace_drive_platform(<WORKSPACE>))` (never first-match: with two drives connected it can search the one that does not hold the workspace, BUG-8538), search the filename under `_hq/meetings/` (`google_drive` → Drive web link; `onedrive`/`m365_sharepoint`, e.g. the M365 connector's `sharepoint_search` → OneDrive/SharePoint web URL; lookup empty-handed + another drive connected, with or without an inferred preference → try the other) — and use `brief_path.get_brief_opener_url(path, drive_web_url)` in step 5.C. If no lookup succeeds there is NO link for a cloud-mounted workspace (SPEC_NIGHTM2 §5 item 2): the helper answers with an empty string rather than a dead one carrying this run's own id, and step 5.C's footer says where the recap went in words with no href.

Then compose section content matching the inline recap and land the document through the gated chokepoint, `brief_writer.make_brief_from_json`, by the WRITE door, in two calls. The payload never rides as an argument: the door walks every argument and refuses one past its argument cap, inline or by `args_file`, and a heavy week's recap is not handed through it. So first land the payload, the object below serialised as ONE JSON text, with `write` at the fixed system path (it is overwritten each fire), then hand the writer only that path:

```bash
python3 "$RT/shared/scripts/workspace_access.py" write --json '{"data": "<the object below, as one JSON text>", "rel": "_hq/.system/wrap/weekly_recap_payload.json"}'
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"payload_rel": "_hq/.system/wrap/weekly_recap_payload.json", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:land_recap"}'
```

`friday_wrap_helpers:land_recap` reads the payload beside the data and calls `brief_writer.make_brief_from_json` exactly as the old heredoc did. The answer names the file by `rel`, carries `opener_line`, and its `rel` is the one 5.C hands back.

The payload object, the same example that used to be piped as `brief_writer.py <<'JSON'` on stdin and now lands by `write`:

```text
{
  "output_path": "<BRIEF_PATH from above>",
  "brief_kind": "weekly_recap",
  "title": "Weekly recap — <Mon DD> to <Mon DD>",
  "subtitle": "What happened this week and what's next",
  "exec_header": {
    "verdict": "<the synthesis-lead headline sentence — the dated anchor + what changed>",
    "changed": "<week-over-week money/time deltas: 'pipeline +45K dollars WoW; 2 deals slipped a week'>",
    "decide": "<the one decision this week needs, or 'Nothing — execution week.'>",
    "needs": "<the single most important reader-action, or 'Nothing from you.'>"
  },
  "sections": [
    {"heading": "The week in numbers",
     "tiles": [
       {"label": "Meetings", "value": "6"},
       {"label": "You owe", "value": "4"},
       {"label": "Owed to you", "value": "7"},
       {"label": "Decisions", "value": "3"},
       {"label": "New people", "value": "2"}
     ]},
    {"heading": "Decisions this week", "body": "..."},
    {"heading": "Your plate this week", "bullets": ["<EVERY line of plate_view.wrap_docx_section(plate)['bullets'], in order — WRAPSAVE1; never a body>"],
     "user_spans": ["<EVERY string in plate_view.wrap_docx_section(plate)['user_spans'], verbatim — WRAPSAVE1; drop this key and the whole document refuses to save>"]},
    {"heading": "What you owe (external)",
     "table": {"headers": ["What", "For whom · due"],
               "rows": [["Send the packet", "Sam Sample · Fri Jul 11"]]}},
    {"heading": "What you owe (internal projects)", "body": "..."},
    {"heading": "What they owe you",
     "table": {"headers": ["What", "From whom · age"],
               "rows": [["Updated NetSuite mapping", "Bo Sample · 10 days"]]}},
    {"heading": "Meetings worth noting", "body": "..."},
    {"heading": "Email threads worth noting", "body": "..."},
    {"heading": "New people who came up", "body": "..."},
    {"heading": "Things that look unusual", "body": "..."},
    {"heading": "By project", "body": "..."},
    "<EVERY dict in quiet.wrap_coaching_blocks(...)['docx_sections'], in order and WHOLE — WRAP2 4.2 item 7: each is {heading, bullets, user_spans} and the bullets are byte-identical to the chat's lines; never rebuild one key by key, never re-wrap a bullet list as a body>"
  ],
  "_asks_removed": "WRAP2 4.2 item 1 — the wrap never asks, so this kind writes NO `asks` block. The follow-ups render as the What now STATEMENTS section above, inside the `sections` list."
}
```

**Visual layer (v4.6.1 S3 — the prep-v2 pattern extended here per F-60's follow-up; M directive: tiles/tables over bullet walls in recurring deliverables):**

- **"The week in numbers" stat-tile band opens the doc** — 1-5 tiles, every value counted from THIS window's events (the same numbers Phase 4 already derived), never estimated. **A tile with no data is DROPPED, never rendered empty**; when nothing is countable the whole section is omitted (brief_writer's `_add_stat_tiles` refuses empty tiles at the render chokepoint, same as prep).
- **The two owe sections render as two-column tables** (what | who · due/age) instead of bullets — same drop rule: no rows, no section. Cap 10 rows per direction with a final "+N more" row.
- Everything else stays prose/bullets — the tiles and tables carry the scannable layer; no decorative charts, no fabricated numbers, substrate-derived only.

**Visual pass (SPEC OUT2 §3, after the .docx save).** The page render is NOT run from the wrap (fix round 1): the document is built beside the data by `land_recap`, never on this session's filesystem, so there is nothing here for `visual_gate.render_preview` to open, and the pass is warn-only forever (a finding never refuses a save). Log the skip, composed beside the data by `visual_gate.log_visual_gate` with its append held, and land its row:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"doc_rel": "<the landing's rel>", "rendered": false, "skipped_reason": "the document is built beside the data", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:plan_visual_gate"}'
python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "weekly-recap", "rel": "_hq/data/events.jsonl", "rows": [<every row in rows from the visual gate answer above>]}'
```

**Output guard:** no internal tokens, paths, event names, or version numbers in anything the CEO sees — vocabulary per `shared/VOICE_CALIBRATION.md` § Plain-language glossary.
- Bad: "I made 2 calls: theme-led · internal/external split view"
- Good: "I set 2 defaults: I lead with the themes, not the numbers · I split what you owe into work for others vs your own projects"

The former `"Headline"` section is now `exec_header` (the verdict + CHANGED deltas); the former `"What's next"` section is now the `asks` ASK block (max 3). Neither is rendered as a standalone section — they are subsumed (no-duplication, net length must not increase).

The last grouping section's heading is `brief_settings.wrap_group_section(WORKSPACE_ROOT)["heading"]` (CUSTOM2) — `"By project"` on a workspace that has never changed the setting, `"By workstream"` / `"By person"` / `"By money"` on one that has — or `"By day"` for week-with-one-dominant-project or an explicit `by day` phrase. Don't ship both. (Pre-v3.13.6 the example had `"By project" (or "By day")` as a parenthetical inside the JSON; that fails to parse.)

The `subtitle` field is REQUIRED by `brief_writer.make_brief_from_json` — omitting it raises `KeyError: 'subtitle'`. Keep it short (one line, sentence case).

**v3.13.0+ internal/external split rule (per M's 2026-05-20 feedback #23a):** pre-v3.13.0 the recap mixed the user's internal plugin backlog ("re-tune the cleanup rubric", "fix the dropped Daily Brief task") with external client/relationship commitments ("Send the packet") in a single "You Owe" section. Same scope-drift class as #6a (insight-generator) and #11 (memo-writer). v3.13.0 splits them:

- **External (You Owe):** commitments to people OUTSIDE the user (clients, prospects, advisors, family-office portfolio, etc.). The default bucket.
- **Internal (You Owe — build / self-development):** commitments to YOURSELF about a system or platform you operate (an internal product build, workspace cleanup, infrastructure improvements). Only applies when `_hq/BUSINESS_CONTEXT.md` identifies the user as the builder/operator of an own platform or internal system; users without one have an empty Internal section and the rendered output omits it.

Classification heuristic — a commitment is "Internal" when ALL of:
- `data.owner_id == user_id` (the workspace's primary user — the id from `primary_user.resolve_primary_user`, the same one resolved at the top of this skill. Never re-resolve it by reading `entities.json` yourself: per SPEC USERKEY1 the canonical `workspace.user_id` pointer and the legacy `is_primary_user: true` person record are the seam's steps 1 and 3, and a second resolver in prose is exactly what the census exists to eliminate)
- The topic / `primary_thread_id` matches one of the user's own-platform / internal-infrastructure projects as named in `_hq/BUSINESS_CONTEXT.md` (never a hardcoded project list)
- No external counterparty named in the commitment data

Otherwise the commitment is External (default). When in doubt, classify as External — losing an internal item into the external bucket is mild; surfacing internal-build noise alongside client work is the bug M flagged. The Internal section renders only if it has ≥1 item; otherwise omit per the "no empty sections" rule.

### 5.C — Surface as the canonical H2 heading link at the bottom of the chat turn (v3.13.0+)

After the synthesis content and `Sources:` section, render the recap link as an H2 heading at the very bottom of the chat turn per `shared/CONTRACT.md` Rule 3:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_helper --json '{"args": {"docx_rel": "<the landing's rel>", "drive_web_url": "<the web link from the BRIEF_SESSION_SCOPED step, or empty>", "label": "Weekly Recap <date_iso>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:closing_lines"}'
```

The answer is `{footer, h2_link, linked}`, composed beside the data and NOT printed. Its shape, which ASSIGNS and prints nothing (the door form above is what runs it, beside the data):

```python
from brief_path import get_brief_opener_url, saved_to_meetings_footer
from chat_output_renderer import doc_headline_link
footer = saved_to_meetings_footer(absolute_docx_path, label=label,
                                  drive_web_url=drive_web_url)
h2_link = doc_headline_link(label, get_brief_opener_url(absolute_docx_path,
                                                        drive_web_url))
# ASSIGN ONLY: `footer` and `h2_link` are the last two lines of the post,
# in that order, and the post is what carries them out.
```

The same answer: when the whole-turn door's leak gate cannot read the link (a folder named with a space, or the path on the customer's own computer on a merged seat), `linked` is false: `footer` is the composer's words-only form, "Saved to your meetings folder.", and `h2_link` is empty, so the wrap still posts and says in words where the recap went (DELIV1; MIGRATE3-FW seam S-3). The path is spelled beside the data from the landing's workspace-relative `rel`, never handed across: the door refuses an argument carrying a `computer:` link, and on a merged seat the path on the customer's computer is outside its fence. `footer` is `brief_path.saved_to_meetings_footer(absolute_docx_path, label=label, drive_web_url=drive_web_url)`: MF-4 (night 11b trial merge, 2026-09-14), the ONLY sentence that says where the document went, "Saved to your meetings folder" with the document behind the link; `BRIEF_PATH`, the folder name and any `_hq/` spelling never print (attended test v5.30.0, leak instance 12). `h2_link` is `doc_headline_link(label, get_brief_opener_url(absolute_docx_path, drive_web_url))`, cloud-aware (v5.9.2, platform-neutral v5.11.1): on a cloud-mounted workspace the web link resolved in the BRIEF_SESSION_SCOPED step, on a host-native workspace the `computer://` form, `## → **[Weekly Recap <date>](computer://...)**`. `footer` and `h2_link` are the last two lines of the post, in that order, and the post is what carries them out (WRAPSTAFF1 fix round 2, routed in from REVIEW_LEAK4 F-3, 2026-09-17).

**Nothing in this section prints.** `footer` and `h2_link` are composed here and handed on: **the wrap's final text is posted through the trailer door** — the `surface_composers.post(...)` block that follows in this section, which LEAK4 owns and which lands at that lane's merge — so the tail of the turn is composed once, scanned once and posted once. A `print` here would put the footer and the link at the top of the turn, ahead of the recap they belong under, and would put them outside the one door that scans them.

**Never print `BRIEF_PATH`, the meetings folder's path, or any `_hq/` spelling in the chat turn** — the footer above is the one place the save is mentioned, and it is composed in code (LEAK3, night 11b).

**These two lines are part of the turn, not two lines after it (SPEC FIXTRAIN v5.31.0 6.1, R-25).** 5.A's `quiet.wrap_post` reads the FINAL text, and 5.A runs BEFORE this section composes — so a footer and a heading link printed here are printed after the door closed, which is the trailer shape exactly. Append them to `post_text` and post the whole turn through the whole-turn door:

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"docx_rel": "<the landing's rel>", "drive_web_url": "<the web link, or empty>", "label": "Weekly Recap <date_iso>", "now_iso": "<the now_iso plate_cut was given>", "since_iso": "<the since_iso plate_cut was given>", "text": "<post_text, the recap without its closing lines>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:post_turn"}'
```

The call it makes beside the data is `final = post(post_text, surface="wrap", workspace=WORKSPACE_ROOT, relayed=plate["text"])`, with the cut re-composed in its own process and the closing lines already joined on; `final` comes back as the answer.

The helper appends `footer` and `h2_link` itself, composed exactly as `closing_lines` composes them, so the turn it posts is `"\n\n".join([post_text, footer, h2_link])`; hand it `post_text` alone and never paste the two lines into `text` (a `computer:` link in an argument is refused by the door before anything runs). The answer is `{text}`: post THAT, as the whole turn, and nothing after it. When the whole-turn door refuses, the answer is `{ok: false, reason, line, detail}` and its ONE `surface_failed` receipt has already landed: post the recap's own `post_text` alone through the same form, and if even that refuses, `line` (`surface_composers.refused_line`) is the whole turn.

Don't put the link inline in the recap body. It MUST be at the bottom or it gets lost between paragraphs (per M's 2026-05-20 feedback #6d/#9/#11).

The file card `present_files` is OPTIONAL post-v3.13.0 and is no longer the primary opener — the Windows resolver behind it doesn't open most file types reliably from card click (per M's 2026-05-20 testing #29), and on a merged seat the card comes from the harness or not at all. The H2 native `computer://` link IS the opener. If you include `present_files`, position it AFTER the H2 link as a reveal-in-folder convenience only — skip it entirely if the user is unlikely to need filesystem navigation (default for weekly-recap: skip).

### 5.D — Demo closing line (first-call use only)

If invoked during the first-call demo arc (heuristic: `_hq/data/events.jsonl` is <48h old by mtime, AND `<workspace>/SESSION_NOTES_*` files have <5 entries each), end with:

> *"Here's everything Command Room saw from your week. Going forward, this is automatic — Past Meetings catches every call as it happens, Morning Brief surfaces what needs attention before you start your day."*

Skip the demo line on subsequent runs (when the workspace is mature enough that the closing line would feel patronizing).

---

## Phase 6 — Log + close

Append one `weekly_recap_run` event:

```json
{"type": "weekly_recap_run", "source_skill": "weekly-recap", "primary_thread_id": null, "related_thread_ids": [], "classification_confidence": null, "data": {"window_start": "<ISO>", "window_end": "<ISO>", "mode": "by_project|by_day", "events_captured": <N>, "commitments_found": <N>, "recap_path": "<absolute>", "outcome": "complete"}}
```

That row lands through the one door, never typed into the file: `python3 "$RT/shared/scripts/workspace_access.py" append_jsonl --json '{"holder": "weekly-recap", "rel": "_hq/data/events.jsonl", "rows": [<the weekly_recap_run row above>]}'`.

**And carry next week's three onto the fire's ONE `pack_run` receipt (WRAP2 4.2 item 2).** The scheduled fire's receipt is written by `orchestrator-friday-wrap.md` Phase 5; an on-demand fire that writes one merges the same dict into its `extra_data`:

```python
extra_data={..., **wrap2["receipt_extra"]}   # {"next_week_ids": [...]}
```

**And close the earned door here too, in the same breath, for the same reason (REVIEW_NIGHT11C H-1, 2026-09-15):**

```bash
python3 "$RT/shared/scripts/workspace_access.py" run_writer --json '{"args": {"offer_pattern_key": "<wrap2 offer_pattern_key; an empty string when this week offered nothing>", "workspace_root": "<WS>"}, "name": "friday_wrap_helpers:record_offer"}'
```

That is `quiet.wrap_record_offer(WORKSPACE_ROOT, wrap2)` through the WRITE door; no key is a no-op when this week offered nothing.

The offer row is what makes "say nothing and I will not raise it again" true, and `coaching_doors.earned_offer_due` answers False forever once it exists. Writing it at compose meant a wrap that raised on a fence — or a post the leak gate refused — had already spent the once-ever offer on a line nobody read. Call this **after the post**, never before it, and never instead of posting.

**This is load-bearing, not bookkeeping.** Next Friday's accountability section reads exactly this key back off this receipt (`quiet.last_next_week_ids`). A wrap that renders next week's three and does not record them leaves next week's wrap with nothing to report against — which is the state the product was in before 2026-09-14: a promise of accountability with no record anywhere of what had been promised. **One receipt per fire**: merge the key, never add a second `log_receipt` call to carry it.

**STOP.** The chat turn is over. Do not narrate what was just posted.

---

## Idempotency

- All events.jsonl appends dedup via `source_ref_hash`. Re-running on the same window doesn't double-capture.
- The `.docx` overwrites the same date's prior version (filename collision = newer wins). Customers can re-run to refresh.
- The `weekly_recap_run` event ALWAYS appends fresh (one row per fire, even if the captured events were 100% dedups). Provides the audit trail.

## Cost / timing budget

| Phase | Budget | Failure mode |
|---|---|---|
| Phase 2 connector reads | 15-20 sec total | Skip individual connectors that timeout > 5 sec; footnote at end of recap |
| Phase 3 commitment capture | 5-10 sec | Skip silently with footnote |
| Phase 4 synthesis | 5-10 sec | n/a — this is local LLM work |
| Phase 5 `.docx` save | 1-2 sec | If brief_writer fails, surface inline recap only and footnote the missing .docx |
| **Total** | **~25-40 sec** | Within demo-arc tolerance |

If the total budget overruns 60 sec on a heavy workspace, stop the in-progress connector reads, surface a partial recap from what's been captured, and footnote: *"This was a heavier week than usual — I had to wrap before I finished. Run 'weekly recap' again or narrow it with 'recap the last 3 days' and I'll go deeper."*

## The activity log is append-only (MANDATORY — CONTRACT Rule 31)

This skill touches `_hq/data`. **The activity log is never rewritten by hand.**
`events.jsonl` and its yearly shards are only ever ADDED to, through the
writers (`event_gate.append_event` / `atomic_write.atomic_append_jsonl`). No
step here, and no turn this skill runs in, may edit, truncate, reorder, delete
lines from, back up and rewrite, or restore that file — and may never instruct
anyone else to.

- A duplicate or malformed line is **quarantined through the cleanup skill's
  existing path**, never deleted (`recover_corruption.py` for malformed lines,
  `seq_health.py --mark` for a duplicate entry number).
- Correcting writes this skill made means **appending a reversal through
  `brain_undo.undo_batch`** with the batch ref the run advertised — a receipt
  and a real `undo`. `undo` after a re-run means exactly that batch, or the
  words "nothing to reverse"; never an improvised drop, an invented supersede,
  or a hand-edited file.
- If you believe the file itself must change, **STOP and say so in plain
  words.** Do not do it, and do not offer to.

## What it doesn't do

- Does not modify `entities.json` — new people surfaced are queued for `people-crm` via `pending_review: true` event annotations.
- Does not draft any emails or follow-ups — those are explicit follow-on commands (`follow up with [person]`).
- Does not re-process meetings already processed by Past Meetings — the events.jsonl events from those Past Meetings runs are READ by this recap, not duplicated.
- Does not register or modify scheduled tasks.
- Does not fire on `cleanup` (different skill, different surface).
- Does not exceed a 30-day historical window even when explicitly asked — for longer pulls, route to `backfill [N] months on [project]` per-project.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> Pulls 7 days of context across every connected source (Gmail/Outlook, Calendar, Slack/Teams, Drive/OneDrive, every meeting-transcript source, Granola/Fireflies/Otter/Read.ai/Zoom AI Companion/Microsoft Teams), writes interaction events to events.jsonl as it goes (passive backfill side-effect), captures the commitments in the freshly-captured meeting events (Phase 3, through the access layer), then synthesizes the week into both an inline chat summary and a saved `.docx` artifact at `_hq/meetings/Weekly_Recap_<YYYY-MM-DD>.docx`. Designed for the end-of-first-call demo (covers the gap between 'scheduled tasks fire on today only' and 'customer wants substantive output by end of call') and for any week-on-week retrospective. Triggers: 'monthly recap', 'what happened this month' (same recap engine over the last calendar month, set the window accordingly), 'weekly recap', 'weekly summary', 'what happened last week', 'last week recap`, `recap of last week`, `summarize last week`, `give me a weekly recap`, `recap last week by project`, `recap last week by day`. Also handles first-run personalization settings, use when the user says 'tune my weekly recap', 'tune weekly-recap', 'show weekly-recap settings', 'reset weekly-recap to defaults'. DOES NOT fire on `cleanup` (that's `cleanup`, a workspace health check, different surface), `morning briefing` (that's `morning-briefing`, daily, not weekly), `process the call` / `process last meeting` (that's `meeting-notes`, single meeting). Idempotent, all events.jsonl appends dedup via `source_ref_hash`, safe to re-run on the same window.
