# View Generation — v2.2

**Version:** 2.2
**Companion to:** `shared/WORKSPACE_API.md`, `references/DATA_CONTRACT.md`, `references/ORG_AND_THREAD_MODEL.md`
**Layout contract:** All views render the org tree per the 6 authoritative rules in `skills/morning-briefing/SKILL.md` Step 4.

In v2.2, the generated views are `MASTER_TRACKER.md`, `PEOPLE.md`, `DECISION_LOG.md`, `ALIASES.md`, `ORG_TREE.md`, and the analytical views (TIMELINE, RELATIONSHIPS, AGING, DORMANT, THEMES). All are **generated views**, not writable sources. This file defines how each view is produced from the JSON sources so generation is deterministic, portable, and testable.

**Nested-org rendering:** primary-focus orgs render first as top-level sections with nested operating children under holdings; remaining orgs roll into an OTHER ORGS section grouped by `relationship_type`. User-facing vocabulary is "thread"; schema IDs keep the `project_` prefix.

Views are produced by the skill that made the triggering source write. There is no background process. Regeneration runs synchronously within the same turn as the source write.

---

## Invariants

1. **Deterministic.** Same source → same view, every time. No randomness, no timestamps in content (except the version header).
2. **Atomic.** Writers produce the view into a temp file, then rename. Readers never see a half-generated view.
3. **Idempotent.** Regenerating a view when sources haven't changed produces byte-identical output (useful for detecting unexpected drift).
4. **Bounded size.** If a view would exceed 2x its size target, the generator trims oldest entries (for log-style views) or splits (for registry-style views) and notes the elision inline.

---

## `_hq/views/MASTER_TRACKER.md`

Projected from: `entities.json` (threads array + orgs array) + `events.jsonl` (activity events)

**Generator (v4.2.0+):** `shared/scripts/render_master_tracker.py` — `regenerate(workspace_root)` / `regenerate_if_changed(workspace_root)`. Dual-writes the canonical `_hq/views/MASTER_TRACKER.md` + back-compat `_hq/MASTER_TRACKER.md`, atomic + idempotent, reads commitments shape-safely via `cru_match`. Mirrors `render_people_view.py` / `render_decision_log.py`. Wired into end-session (`workspace-manager` Step 2.5) and cleanup Phase 3.5d2 (changed-only weekly backstop). Before v4.2.0 there was no renderer — the tracker was hand-rendered by the LLM at end-session and silently froze when that lapsed.

**Regenerated when:**
- Any write to `entities.json` affecting threads OR orgs
- Any `events.jsonl` append with `type` in `{status_change, scope_change, meeting, commitment, commitment_resolved, decision, reclassification}` (these update "last activity", "next step", or the thread's affiliation)

**Template (v2.2 — org-tree grouped):**

```markdown
<!-- generated-from: _hq/data/entities.json, _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: render_master_tracker.py -->
<!-- source-version: entities@<N>, events@<seq> -->

# Master Tracker

<!-- PRIMARY FOCUS ORG SECTIONS -->
<!-- Iterate orgs where is_primary_focus==true AND parent_org_id==null, sorted by most-recent activity desc. -->
<!-- For each holding, nest operating children as sub-sections. For a primary-focus operating org with no holding parent, render flat. -->

<for each org where is_primary_focus == true and parent_org_id == null>

## <canonical_name(org)>  <relationship_type_badge(org)>

<if org.scope == "holding" and has operating children>
<for each child in children(org), sorted by most-recent activity desc>

### <canonical_name(child)>  <relationship_type_badge(child)>

<threads_table_for_org(child.id)>
</for>

<if any threads directly affiliated to holding (no operating child)>
### Holding-level threads

<threads_table_for_org(org.id, include_descendants=false)>
</if>

<else>
<threads_table_for_org(org.id)>
</if>
</for>

<!-- OTHER ORGS ROLLUP -->
<!-- Iterate orgs where is_primary_focus==false, grouped by relationship_type. -->

## Other Orgs

<for each relationship_type in [operating, partner, client, board, advisory, investment, portfolio_company, beneficiary, other]>
<if any org with this relationship_type and is_primary_focus==false and has active threads>
### <relationship_type_label>

<for each such org, sorted by most-recent activity desc>
- **<canonical_name>** — <thread_count> active, last activity <computed_last_activity>
</for>
</if>
</for>

## Paused / Blocked (across all orgs)

| Thread | Org | Status | Last Activity | Reason |
|---|---|---|---|---|
<for each thread where status in ["paused", "blocked"], sorted by last_activity desc>
| <display_name> | <canonical_name(affiliation_id)> | <status> | <computed_last_activity> | <latest_event_of_type_status_change.data.reason or "—"> |
</for>

## Recently Archived

| Thread | Org | Archived | Reason |
|---|---|---|---|
<for each thread where status == "archived", sorted by archived_at desc, top 10>
| <display_name> | <canonical_name(affiliation_id)> | <archived_at> | <archive_reason or "—"> |
</for>

## Open Commitments (across all threads)

| Description | Owner | Due | Thread | Org | Status |
|---|---|---|---|---|---|
<for each event where type == "commitment" and _commitment_field(ev, "status") in ("open", "overdue") and passes_surface_floor(ev, floor=0.40) and id not in closed-commitment-ids, sorted by _commitment_field(ev, "due") asc, top 20>
| <_commitment_field(ev, "title")> | <canonical_name(_commitment_field(ev, "owner_id"))> | <_commitment_field(ev, "due")> | <display_name(primary_thread_id)> | <canonical_name(thread.affiliation_id)> | <_commitment_field(ev, "status")> |
</for>

<if any provisional or low-confidence commitments exist>
> _N open commitments are on events with classification_confidence < 0.40 or pending Pass 8 review — not shown above. Run `insight-generator` to review._
</if>
```

**Shape-aware reads (v3.4.4+ — REQUIRED):** every commitment field read in this section MUST go through `shared/scripts/cru_match.py::_commitment_field` (handles 5 shape variants: canonical, flat-new, legacy `owner`, `owner_person_id`-variant, pending-review). Confidence filtering goes through `cru_match.passes_surface_floor(ev, floor=0.40)` (BUG-8330 item 6): MISSING confidence is unscored and PASSES — `_commitment_confidence`’s missing→0.0 default is for explicit comparisons only, and applying it to a floor silently dropped every unscored capture. The 0.40 here is the VIEW floor (render tail note for what it hides); the daily-surface floor is `confidence.surface_min()` (0.7 shipped, per-workspace calibrated) and is enforced in code by `surface_drivers._apply_confidence_floor` — the two are different dials by design. Closed-commitment filter is the SHARED closure chain — use `cru_match.load_open_commitments` (or `closure_index.build_closure_index`); never a private resolved-id set (BUG-8330 item 1: private chains missed `commitment_superseded`, `target_id`, the seq aliases, and reopens). Direct reads of `data.owner_person_id` / `data.description` (legacy field names) silently drop ~42% of commitments in production workspaces; this is the v3.4.4 bug class extended to view-regen.

**Headline count — the one counting API (Phase 2 Stage A, REQUIRED):** the tracker's "N open commitments" headline (header line + `<!-- totals -->` comment) is `commitment_state.count_commitments(load_open_commitments(...))["total"]` — the FULL open set, NOT the confidence-filtered row count above. The ≥0.40 floor only decides which rows render in the table; provisional items stay in the headline (they are open commitments) and are called out in the blockquote note. Reporting the filtered count as the headline made the tracker one of the three diverging aggregators in the 2026-07-01 audit (tracker 54 vs live replay 105). `render_master_tracker.py` implements this; the projector's loader also folds `commitment_updated` deferrals into the effective `due`, so a pushed item renders its new date.

**Helper — `threads_table_for_org(org_id, include_descendants=true)`:**

```
| Thread | Kind | Status | Stage | Last Activity | Next Step | Owner |
|---|---|---|---|---|---|---|
<for each active thread where affiliation_id in descendant_set(org_id, include_descendants), sorted by computed_last_activity desc>
| <display_name> | <kind> | <status> | <stage> | <computed_last_activity> | <next_step or "—"> | <canonical_name(owner_person_id) or "—"> |
</for>
```

**Helper — `relationship_type_badge(org)`:** emits a small tag next to the org name, e.g. `[operating]`, `[board]`, `[advisory]`. Omit badge if `relationship_type == "operating"` and `is_primary_focus == true` (reduces noise on the default case).

**computed_last_activity for a thread:** max `ts` across all events where `primary_thread_id == thread.id` AND `classification_confidence >= 0.40`. Falls back to `thread.first_seen` if none.

**Size target:** <8 KB. If exceeded, truncate "Recently Archived" to top 5 and open commitments to top 10.

---

## `_hq/views/PEOPLE.md`

Projected from: `entities.json` (people + orgs arrays) + `events.jsonl` (interactions)

**Regenerated when:**
- Any write to `entities.json` affecting people OR orgs
- Any `events.jsonl` append with `type: interaction` or `type: meeting` (updates last_interaction)

**Template (v2.2 — grouped by primary_org_id with org-tree layout):**

```markdown
<!-- generated-from: _hq/data/entities.json, _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: people-crm -->
<!-- source-version: entities@<N>, events@<seq> -->

# People

<!-- PRIMARY FOCUS ORGS first, then OTHER ORGS rollup. Same layout contract as MASTER_TRACKER. -->

<for each org where is_primary_focus == true and parent_org_id == null>
## <canonical_name(org)>  <relationship_type_badge(org)>

<if org.scope == "holding" and has operating children>
<for each child in children(org)>
### <canonical_name(child)>

<people_list_for_org(child.id)>
</for>
<else>
<people_list_for_org(org.id)>
</if>
</for>

## Other Orgs

<for each relationship_type>
<if any org with this relationship_type and is_primary_focus==false>
### <relationship_type_label>

<for each such org>
#### <canonical_name>
<people_list_for_org(org.id)>
</for>
</if>
</for>

## Unaffiliated

<people_list_for_org(null)>

<if any person where status == "archived">
## Archived (<count>)

<comma-joined canonical names, up to 20>

…see entities.json for full list.
</if>
```

**Helper — `people_list_for_org(org_id)`:**

```
<for each active person where primary_org_id == org_id (or org_id in org_ids if org_id is null → primary_org_id is null), sorted by canonical_name>
### <canonical_name> (<id>)

- **Role:** <role or "—">
- **Primary Org:** <canonical_name(primary_org_id)> <relationship_type_badge(primary_org_id)>
<if len(org_ids) > 1>
- **Other Orgs:** <comma-joined canonical_name(other_org_ids) with relationship_type badges>
</if>
- **Email:** <email or "—">
- **Aliases:** <comma-joined aliases or "—">
- **Threads:** <comma-joined display_names_of(thread_ids) or "—">
- **First seen:** <first_seen>
- **Last interaction:** <computed_last_interaction>
- **Notes:** <notes or "—">

---
</for>
```

**computed_last_interaction:** max `ts` across events where `<person.id>` is in `person_ids` AND `classification_confidence >= 0.40` (or null for infrastructure events). If no qualifying events, use `person.first_seen`.

**Size target:** <15 KB. If exceeded, paginate into `PEOPLE.md` + `PEOPLE_archived.md` (unaffiliated section moves to the paginated file).

---

## `_hq/views/DECISION_LOG.md`

Projected from: `events.jsonl` (type == "decision", with closure status from `decision_resolved` / `decision_superseded` events — v3.4.5+)

**Regenerated when:** any `events.jsonl` append with `type: decision`, `type: decision_resolved` (v3.4.5+), `type: decision_superseded` (v3.4.5+), or `supersedes_seq` pointing to a decision event (including `reclassification` events that reroute a decision's primary thread).

**Closure model (v3.4.5+):** a decision's status is `Active` by default, `Resolved` if any later event of `type: decision_resolved` references it via `data.decision_id`, `Superseded` if any later event of `type: decision_superseded` references it. Closed decisions stay in the log — they're marked, never deleted — but get a visible badge so the active set is scannable. Decision-CRU writes closure events silently (CONTRACT.md Rule 24); the user discovers them via this view.

**Closure-id matching:** for each decision event, its id is `data.id` if set, else the synthesized `decision_seq_<seq>`. Closure events reference that same id via `data.decision_id`. Mirrors the `commitment` / `commitment_resolved` pairing pattern.

**Template (v3.4.5+):**

```markdown
<!-- generated-from: _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: decision-log -->
<!-- source-version: events@<seq> -->

# Decision Log

<!-- First pass: collect resolution/supersession events keyed by decision id -->
<resolved_ids = {ev.data.decision_id for ev where type == "decision_resolved"}>
<superseded_map = {ev.data.decision_id: ev for ev where type == "decision_superseded"}>

<for each event where type == "decision" and classification_confidence >= 0.40, sorted by ts desc>
## <ts (YYYY-MM-DD)> — <data.title> <status_badge>

- **Status:** <status>  <!-- "Active", "Resolved", or "Superseded" — derived per closure model above -->
- **Context:** <data.context>
- **Decision:** <data.decision>
- **Rationale:** <data.rationale>
- **Alternatives considered:** <comma-joined data.alternatives or "—">
- **Who decided:** <canonical_name(data.decided_by_person_id) or "—">
- **Thread:** <display_name(primary_thread_id) or "cross-thread">
- **Org:** <canonical_name(affiliation_id of thread)> (primary-focus • <relationship_type>)
<if related_thread_ids is non-empty>
- **Related threads:** <comma-joined display_name(related_thread_ids)> — <cross_ref_reason summary>
</if>
- **Confidence:** <classification_confidence> <"(provisional)" if confidence < 0.75>
- **Source:** <source_skill>
<if this decision's id in resolved_ids>
- **✓ Resolved:** <resolution_event.ts (YYYY-MM-DD)> — <resolution_event.data.evidence or "—">
</if>
<if this decision's id in superseded_map>
- **⚠ Superseded:** <superseded_event.ts (YYYY-MM-DD)> — <superseded_event.data.evidence or "—">
  <if superseded_event.data.superseded_by_decision_seq>
  - Newer decision: <link to event with seq == superseded_by_decision_seq>
  </if>
</if>
<if legacy supersedes_seq mechanism (some other event has supersedes_seq == this.seq) AND not already marked Superseded via decision_superseded>
- **⚠ Superseded by:** <link to superseding event's ts + title>  <!-- legacy v2.2 path; kept for back-compat -->
</if>

---
</for>

<if any decision events with classification_confidence < 0.40 exist>
## Pending Review (Pass 8)

_<count> decisions are on low-confidence events and are not shown above. Run `insight-generator` to review and route them to the correct thread._
</if>
```

**status_badge:** emits `` (empty) for Active, ` ✓` for Resolved, ` ⚠ Superseded` for Superseded. Renders next to the title for at-a-glance scanning. Some renderers may prefer to group by status; that's a future split.

**Size target:** <25 KB. If exceeded, split last N years into `DECISION_LOG.md` (current year) + `DECISION_LOG_YYYY.md` (past years).

**Closed decisions stay in the log** — they're marked but never deleted. Preserves the decision history.

---

## `_hq/views/ALIASES.md`

Projected from: `aliases.json`

**Regenerated when:** any write to `aliases.json`.

**Template:**

```markdown
<!-- generated-from: _hq/data/aliases.json -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: people-crm -->
<!-- source-version: aliases@<N> -->

# Aliases

## People

| Raw | Canonical |
|---|---|
<for each mapping in mappings.people, sorted by canonical_name then raw>
| <raw> | <canonical_name(canonical_id)> |
</for>

## Threads

| Raw | Canonical (display_name) | Org |
|---|---|---|
<for each mapping in mappings.threads or mappings.projects>
| <raw> | <display_name(canonical_id)> | <canonical_name(thread.affiliation_id)> |
</for>

## Orgs

| Raw | Canonical | Scope | Relationship |
|---|---|---|---|
<for each mapping in mappings.orgs>
| <raw> | <canonical_name(canonical_id)> | <scope(canonical_id)> | <relationship_type(canonical_id)> |
</for>
```

**Size target:** <5 KB.

---

## `_hq/views/ORG_TREE.md` (v2.2)

Projected from: `entities.json` (orgs array)

**Regenerated when:** any write to `entities.json` affecting orgs (including `parent_org_id`, `scope`, `relationship_type`, `is_primary_focus`, `status` changes).

**Purpose:** Human-readable visualization of the nested org tree. Shows which orgs are primary focus, which hold which, which relationship types apply, and which connector signals surfaced each org. Used by onboarding to confirm tree, by cleanup to debug drift, and by anyone asking "what orgs do I have?"

**Template:**

```markdown
<!-- generated-from: _hq/data/entities.json -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: workspace-manager -->
<!-- source-version: entities@<N> -->

# Org Tree

## Primary Focus

<for each org where is_primary_focus == true and parent_org_id == null, sorted by canonical_name>

### <canonical_name> — <scope> • <relationship_type>

<if org.aliases>
- **Aliases:** <comma-joined aliases>
</if>
<if org.domains>
- **Domains:** <comma-joined domains>
</if>
<if org.slack_workspace_ids>
- **Slack:** <comma-joined slack_workspace_ids>
</if>
- **Inferred from:** <comma-joined inferred_from>

<if has children>
**Operating companies:**

<for each child, sorted by canonical_name>
- **<canonical_name(child)>** — <scope> • <relationship_type> <if child.is_primary_focus>[primary]</if>
  - Aliases: <aliases or "—"> | Domains: <domains or "—">
  - Inferred from: <inferred_from>
</for>
</if>
</for>

## Other Orgs

<for each relationship_type in [operating, partner, client, board, advisory, investment, portfolio_company, beneficiary, other]>
<if any org with this relationship_type and is_primary_focus == false and parent_org_id == null>
### <relationship_type_label> (<count>)

<for each such org, sorted by canonical_name>
- **<canonical_name>** — <scope> | Domains: <domains or "—"> | Inferred from: <inferred_from>
</for>
</if>
</for>

<if any org where status != "active">
## Archived / Inactive

<for each archived org>
- <canonical_name> (<status>) — previously <scope> • <relationship_type>
</for>
</if>
```

**Size target:** <10 KB. Large org trees (100+ orgs) paginate the Other Orgs section into per-relationship-type files.

**Invariant coverage:** ORG_TREE.md renders must match the tree shape validated by cleanup checks 10 (no cycles), 11 (affiliation resolves), 15 (at least one primary focus), 16 (non-empty inferred_from). If any invariant fails, the regen is aborted and a `view-regen-failure` conflict is logged.

---

## Analytical views (TIMELINE / RELATIONSHIPS / COMMITMENT_AGING / DORMANT / THEMES)

**Generator owner:** `insight-generator` (lazy / inline — v3.12.0+).

Pre-v3.12.0 these 5 views were declared with `<!-- generator: insight-generator (lazy inline at synthesis time) -->`, but no `view-generator` skill ever existed. The plugin shipped readers without a corresponding writer. v3.12.0 retires the ghost: these projections are now computed by `insight-generator` directly from `_hq/data/entities.json` + `_hq/data/events.jsonl` at the start of its own synthesis run, then optionally written to `_hq/views/*.md` on the same turn for human-readability. They are not regenerated on every events.jsonl append (the cost of that on a busy workspace would be high and the only consumer is insight-generator's weekly fire).

If a customer reads `_hq/views/TIMELINE.md` directly between insight-generator runs they may see a stale snapshot from the last run — that's expected. Live timeline browsing of any kind should use the `list-active` skill or workspace-manager's "what's going on" briefing (both derive from canonical Tier 1 sources per the v3.11.4 SOURCE_OF_TRUTH contract).

---

## `_hq/views/TIMELINE.md` (computed inline by insight-generator — v3.12.0+)

Projected from: `events.jsonl` (all types except `briefing`, `audit_run`, `onboarding_step`, `classification_review`, `boundary_marker`)

**Regenerated when:** at the start of every `insight-generator` run (weekly Sunday fire or on-demand). Stored in `_hq/views/TIMELINE.md` as a side effect for human-readability; the canonical computation lives in insight-generator's own synthesis logic. Per `references/SOURCE_OF_TRUTH.md`, never read this file as the source of truth — it's a Tier 2 snapshot from the last insight-generator run.

**Purpose:** Chronological "what happened" across the whole workspace, reverse-chron, grouped by day. Used by the CEO to scan recent history and by insight-generator to detect cross-thread patterns. Shows classification confidence inline so provisional/low-confidence events are visually distinct.

**Template:**

```markdown
<!-- generated-from: _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: insight-generator (lazy inline at synthesis time) -->
<!-- source-version: events@<seq> -->

# Timeline

<for each day where any qualifying event exists, sorted desc, last 30 days>
## <YYYY-MM-DD> (<day_of_week>)

<for each event on this day, sorted by ts desc>
- **<ts HH:MM>** — <type_label> · <display_name(primary_thread_id) or "HQ"> <org_badge> · <one_line_summary> <confidence_marker>
<if related_thread_ids non-empty>
  - also linked to: <comma-joined display_name(related_thread_ids)>
</if>
</for>
</for>

<if more than 30 days of events exist>
---
…older history in `_hq/views/TIMELINE_archive/YYYY-MM.md` (monthly archives).
</if>
```

**type_label map:** meeting → "Meeting", decision → "Decision", decision_resolved → "Decision ✓", decision_superseded → "Decision ⚠ superseded", commitment → "Committed", commitment_resolved → "Closed", commitment_updated → "Updated", interaction → "Contact", status_change → "Status", scope_change → "Scope", intel_logged → "Intel", reclassification → "Reclassified", note → "Note", other → "Note".

**org_badge:** emits the affiliation org's canonical_name in parens if the primary-focus org of the thread is different from the default primary-focus org. E.g. `Sourcing Bot (Acme Restaurant)`.

**confidence_marker:** for `classification_confidence` < 0.75, append ` [prov:0.62]`; for < 0.40, append ` [low:0.28]`. Empty string for high-confidence or null (infrastructure) events.

**one_line_summary:** for each type, pull from `data`: meeting→`data.title`, decision→`data.title`, commitment→`data.description`, commitment_resolved→`data.description + " resolved"`, interaction→`data.summary`, status_change→`<from> → <to>`, scope_change→`data.summary`, intel_logged→`data.source_title`, reclassification→`<display_name(from_primary)> → <display_name(to_primary)>`, note→`data.text` (first 80 chars).

**Size target:** <30 KB for the rolling 30-day window. Older content rotates into `_hq/views/TIMELINE_archive/YYYY-MM.md` on the first regen of each month.

---

## `_hq/views/RELATIONSHIPS.md` (v2.2)

Projected from: `entities.json` (people) + `events.jsonl` (interactions/meetings)

**Regenerated when:** any `events.jsonl` append with type in {`interaction`, `meeting`, `commitment`, `commitment_resolved`} AND any write to `entities.json` affecting people.

**Purpose:** Who needs attention. Not a registry (that's PEOPLE.md) — a **cadence-ordered** view surfacing people whose last-touch has aged past expected cadence, plus recent interactions per person.

**Template:**

```markdown
<!-- generated-from: _hq/data/entities.json, _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: insight-generator (lazy inline at synthesis time) -->
<!-- source-version: entities@<N>, events@<seq> -->

# Relationships

## Overdue for Touch

Sorted by days-since-last-touch, highest first. Threshold = `data.cadence_days` on person if present, else default 30.

| Person | Role | Primary Org | Last Touch | Days Open | Cadence Target | Threads |
|---|---|---|---|---|---|---|
<for each active person where (today - computed_last_interaction) > cadence_days, sorted desc by days_open>
| <canonical_name> | <role or "—"> | <canonical_name(primary_org_id)> <relationship_type_badge> | <last_interaction_ts (YYYY-MM-DD)> | <days_open> | <cadence_days or "30 default"> | <comma-joined display_names of thread_ids, max 3> |
</for>

## Recent Contact (last 14 days)

<for each active person where (today - computed_last_interaction) <= 14, sorted by last_interaction desc>
- **<canonical_name>** (<canonical_name(primary_org_id)>) — <last_interaction_ts> · <most_recent_event_summary> · <display_name(primary_thread_id) or "—">
</for>

## Dormant (no interaction in 90+ days)

<for each active person where (today - computed_last_interaction) > 90, sorted by canonical_name>
- <canonical_name> — last contact <last_interaction_ts>
</for>
```

**cadence_days:** default 30. Can be overridden per person by storing in `person.notes` (parsed) or as a dedicated field (future). For v2.1, accept a free-form "cadence: 14" line in `notes` and parse it.

**Size target:** <15 KB. If exceeded, truncate Dormant section to top 30.

---

## `_hq/views/COMMITMENT_AGING.md` (v2.2)

Projected from: `events.jsonl` (commitment, commitment_resolved)

**Regenerated when:** any `events.jsonl` append with type in {`commitment`, `commitment_resolved`}.

**Purpose:** Every open commitment sorted by how long it's been open. Separates commitments TO the CEO (someone owes them) from commitments BY the CEO (they owe someone). Surfaces rot.

**Template:**

```markdown
<!-- generated-from: _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: insight-generator (lazy inline at synthesis time) -->
<!-- source-version: events@<seq> -->

# Commitment Aging

## Commitments BY you (you owe)

Sorted by days-open desc. Flag threshold: 7 days = 🟡, 21 days = 🔴.

| Commitment | To | Made | Days Open | Due | Thread | Org | Flag |
|---|---|---|---|---|---|---|---|
<for each open commitment where _commitment_field(ev, "owner_id") == user_self_id and passes_surface_floor(ev, floor=0.40), sorted by days_open desc>
| <_commitment_field(ev, "title")> | <canonical_name(_commitment_field(ev, "requester_id")) or "—"> | <ts (YYYY-MM-DD)> | <days_open> | <_commitment_field(ev, "due") or "—"> | <display_name(primary_thread_id) or "—"> | <canonical_name(thread.affiliation_id) or "—"> | <flag> |
</for>

## Commitments TO you (they owe)

Sorted by days-open desc. Flag threshold: 7 days = 🟡, 21 days = 🔴.

| Commitment | From | Made | Days Open | Due | Thread | Org | Flag |
|---|---|---|---|---|---|---|---|
<for each open commitment where _commitment_field(ev, "requester_id") == user_self_id and passes_surface_floor(ev, floor=0.40), sorted by days_open desc>
| <_commitment_field(ev, "title")> | <canonical_name(_commitment_field(ev, "owner_id"))> | <ts (YYYY-MM-DD)> | <days_open> | <_commitment_field(ev, "due") or "—"> | <display_name(primary_thread_id) or "—"> | <canonical_name(thread.affiliation_id) or "—"> | <flag> |
</for>

**Shape-aware reads (v3.4.4+ — REQUIRED):** every commitment field read in both sections MUST go through `shared/scripts/cru_match.py::_commitment_field`. Same rationale as the MASTER_TRACKER "Open Commitments" section above — direct field-name reads silently drop the 4 non-canonical shape variants. Use `_commitment_confidence` for the threshold check (coerces string-label confidences into floats). Open-status check mirrors `load_open_commitments` (filter out commitments closed by a subsequent `commitment_resolved` / `thread_resolved` event).

**Open set + counts — the projector (Phase 2 Stage A, REQUIRED):** the open set both sections iterate is EXACTLY `load_open_commitments(events.jsonl)` (never a hand-rolled scan), which folds `commitment_updated` deferrals into the effective `due` — a deferred commitment ages against its pushed date, not the immutable original (pre-Stage-A it rendered overdue forever). Any headline/summary count this view states comes from `commitment_state.count_commitments(opens, ...)` / `commitment_counts(workspace_root)` — the one counting API shared with MASTER_TRACKER, the morning brief, the coach, and the Commitments orchestrator. COMMITMENT_AGING reporting 104 while MASTER_TRACKER said 54 and a live replay said 105 (2026-07-01 audit) is the divergence class this closes.

**Task kind never ages here (Phase 2 Stage D, S5 — REQUIRED):** filter both sections to effective `kind != "task"` (the projector has already applied `commitment_reclassified` overrides to the copies it returns). Tasks are self-owed items with no counterparty; they age on the Commitment Triage surface as "still on your plate?" (30-day staleness via `commitment_state.stale_tasks`), are never chased by CRU (`cru_match.cru_eligible` excludes them at the matcher layer), and rendering them here as 🔴-flagged rot is exactly the noise the kind split removes.

## Recently Resolved (last 14 days)

<for each commitment_resolved event where ts > today-14, sorted ts desc, top 10>
- <ts> — <resolved_event.data.evidence or "(commitment closed)"> · <display_name(primary_thread_id) or "—">
</for>

<if any commitment on classification_confidence < 0.40 exists>
---
_<count> commitments on low-confidence events are not shown above. Run `insight-generator` Pass 8 to route them to the correct thread._
</if>
```

**days_open:** `today - ts` of the original commitment event. A commitment is "open" if no subsequent `commitment_resolved` event references its seq via `supersedes_seq`.

**user_self_id:** read from entities.json — the person record where `id == person_001` (reserved for the workspace owner).

**Size target:** <20 KB. If exceeded, cap each section at top 25.

---

## `_hq/views/DORMANT.md` (v2.2)

Projected from: `entities.json` (threads + orgs) + `events.jsonl` (all types)

**Regenerated when:** any `events.jsonl` append AND any write to `entities.json` threads or orgs. Cheap regen — same inputs as MASTER_TRACKER.

**Purpose:** Threads (of any kind) that have gone quiet. Paired with kind filter so insight-generator can say "your dormant initiatives look like X; your dormant relationships look like Y."

**Template:**

```markdown
<!-- generated-from: _hq/data/entities.json, _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: insight-generator (lazy inline at synthesis time) -->
<!-- source-version: entities@<N>, events@<seq> -->

# Dormant Threads

Active threads with no activity in 14+ days, sorted by days-since-activity desc. Grouped by kind.

<for each kind in [initiative, deal, advisory, relationship, theme, concern, ritual, investment, board, personal, other]>
<if any dormant thread of this kind>
## <Kind> (<count>)

| Thread | Status | Last Activity | Days Quiet | Next Step | Org | Focus |
|---|---|---|---|---|---|---|
<for each active thread of this kind where (today - computed_last_activity) > 14, sorted desc by days_quiet>
| <display_name> | <status> | <computed_last_activity> | <days_quiet> | <next_step or "—"> | <canonical_name(affiliation_id)> <relationship_type_badge> | <"★" if org.is_primary_focus else ""> |
</for>
</if>
</for>

## Paused or Blocked (any kind)

| Thread | Status | Since | Reason | Org |
|---|---|---|---|---|
<for each thread where status in [paused, blocked], sorted by (today - last_status_change_ts) desc>
| <display_name> | <status> | <last_status_change.ts> | <last_status_change.data.reason or "—"> | <canonical_name(affiliation_id)> |
</for>
```

**days_quiet threshold** defaults to 14 but is per-kind: relationship=30, theme=60, ritual=2x-cadence. These defaults live in `references/DORMANCY_DEFAULTS.md` (to be added; v2.1 uses 14 universal until that ships).

**Size target:** <15 KB.

---

## `_hq/views/THEMES.md` (v2.2)

Projected from: `entities.json` (threads where kind == "theme") + `events.jsonl`

**Regenerated when:** any `events.jsonl` append AND any write to `entities.json` threads affecting themes. A theme thread can appear as a `related_thread_ids[]` entry on events whose primary thread is elsewhere — those cross-refs are how themes aggregate across orgs.

**Purpose:** Recurring topics that span multiple threads. Insight surfacing: "pricing came up in 4 different deals this month" → the theme-thread called "pricing" shows each mention, aggregating across threads.

**Template:**

```markdown
<!-- generated-from: _hq/data/entities.json, _hq/data/events.jsonl -->
<!-- generated-at: YYYY-MM-DD HH:MM -->
<!-- generator: insight-generator (lazy inline at synthesis time) -->
<!-- source-version: entities@<N>, events@<seq> -->

# Themes

<for each active thread where kind == "theme", sorted by computed_last_activity desc>
## <display_name>

- **Status:** <status>
- **Affiliation:** <canonical_name(affiliation_id) or "cross-org"> <relationship_type_badge>
- **Last activity:** <computed_last_activity>
- **Cross-org mentions (last 30 days):** <count of events where theme.id ∈ related_thread_ids[], grouped by primary-thread's affiliation org>

### Recent mentions

<for each event in last 30 days where theme.id ∈ related_thread_ids[] OR primary_thread_id == theme.id, sorted ts desc, top 10>
- <ts (YYYY-MM-DD)> — <display_name(primary_thread_id) or "HQ"> (<canonical_name(thread.affiliation_id)>) — <one_line_summary> <confidence_marker>
</for>

---
</for>
```

**How themes aggregate in v2.2:** A theme thread surfaces on any event where its id appears in `related_thread_ids[]` (cross-ref) OR equals `primary_thread_id`. `cross_ref_reason[theme.id]` explains why the classifier linked this event to the theme. This replaces v2.1's `data.theme_ids` field — the multi-thread shape is now the canonical mechanism for theme aggregation.

**Size target:** <20 KB.

---

## Regeneration Procedure

Every view regeneration follows this procedure:

```
1. Determine which views are affected by the write that just occurred.
2. For each affected view:
   a. Read the source(s).
   b. Apply the template with the current data.
   c. Write the rendered markdown to <view_path>.tmp.
   d. Rename .tmp → view_path (atomic).
   e. If the workspace uses backward-compat copies at `_hq/<viewname>.md`,
      also update those (copy + rename atomically).
3. Log the regen as an event:
   {"type": "other", "source_skill": "<generator>", "data": {"regen": "<view_path>", "source_version": "<version>"}}
```

If any step fails, the view is left in its previous state and a conflict is logged. The source write is considered successful regardless of view regen — the view is a projection, recoverable from the source.

---

## Regeneration Ownership

To avoid redundant regenerations when multiple sources update in rapid succession:

| Source write | Skill triggers regen of |
|---|---|
| `entities.json` (threads) | MASTER_TRACKER.md, DORMANT.md, THEMES.md (if theme threads), RELATIONSHIPS.md (if thread roster changed) |
| `entities.json` (people) | PEOPLE.md, MASTER_TRACKER.md (owners resolve to canonical_name), RELATIONSHIPS.md |
| `entities.json` (orgs) | PEOPLE.md (org groupings), MASTER_TRACKER.md (org tree layout), ORG_TREE.md, DORMANT.md (focus badges), any view that renders `relationship_type_badge` |
| `aliases.json` | ALIASES.md |
| `events.jsonl` type=decision | DECISION_LOG.md, TIMELINE.md |
| `events.jsonl` type=decision_resolved | DECISION_LOG.md (status badge updates), TIMELINE.md |
| `events.jsonl` type=decision_superseded | DECISION_LOG.md (status badge updates), TIMELINE.md |
| `events.jsonl` type=interaction/meeting | PEOPLE.md, MASTER_TRACKER.md (last_activity), RELATIONSHIPS.md, TIMELINE.md, THEMES.md (if theme thread in related_thread_ids), DORMANT.md |
| `events.jsonl` type=status_change/scope_change | MASTER_TRACKER.md, DORMANT.md, TIMELINE.md |
| `events.jsonl` type=commitment/commitment_resolved | MASTER_TRACKER.md (Open Commitments section), COMMITMENT_AGING.md, TIMELINE.md, DORMANT.md (updates last_activity) |
| `events.jsonl` type=reclassification | every view that consumed the superseded event; easiest to regen all thread-scoped views (MASTER_TRACKER, DECISION_LOG if decision, TIMELINE, THEMES, DORMANT) |
| `events.jsonl` type=classification_review | no view regen; records audit trail only |
| `events.jsonl` type=intel_logged/note | TIMELINE.md |
| `classifier_feedback.jsonl` append | no view regen; consumed by future classification passes only |

If a turn writes to multiple sources, regenerations are batched: collect all affected views, regenerate each once, at the end of the turn.

---

## Backward-Compat Copies

For ease of migration and user habit, the v1.8 paths remain accessible:

- `_hq/MASTER_TRACKER.md` ← copy of `_hq/views/MASTER_TRACKER.md`
- `_hq/PEOPLE.md` ← copy of `_hq/views/PEOPLE.md`
- `_hq/DECISION_LOG.md` ← copy of `_hq/views/DECISION_LOG.md`
- `_hq/ALIASES.md` ← copy of `_hq/views/ALIASES.md`

Each view's renderer updates both the `views/` file and the `_hq/` copy atomically (`render_master_tracker.py`, `render_people_view.py`, `render_decision_log.py` all dual-write). Users querying either path get the same content.

**Legacy-path deprecation — DECISION (v4.2.0): DEFERRED to v5.0.** As of v4.2.0 the legacy flat `_hq/<view>.md` paths are still read ~2:1 over the canonical `_hq/views/` paths, and three tier-1 skills read them as PRIMARY source: `morning-briefing` (`_hq/MASTER_TRACKER.md`), `inbox-triage` (`_hq/PEOPLE.md` VIP tiering), and `command-room-coach` (`_hq/PEOPLE.md` + `_hq/DECISION_LOG.md`). Retiring the legacy paths now would break the daily driver. Plan: keep dual-writing through the v4.x line; in a dedicated v5.0 pass, run a reader-migration audit that moves those three skills (and any remaining readers) to `_hq/views/`, THEN retire all three views' legacy copies in one coordinated release — never piecemeal.

---

## Validation (v2.2)

`cleanup` verifies (per `skills/cleanup/SKILL.md` checks 19–22):

1. Each view file exists and parses as markdown.
2. Each view's `source-version` header is ≥ its source's current version (otherwise it's stale; regen is scheduled).
3. Each view's content, when re-rendered from sources, is byte-identical to the file on disk (catches tampering or drift).
4. No view file has been edited by hand (heuristic: diff against re-render).
5. Generated views render the org tree per `morning-briefing` Step 4 rules — primary-focus orgs first, nested operating children under holdings, OTHER ORGS rollup grouped by `relationship_type`. Structural deviations (e.g., flat rendering of a holding with operating children) = `view-regen-failure` conflict.
6. ORG_TREE.md structure matches org-tree invariants (no cycles, at least one primary focus, every org has `inferred_from`). Failure aborts regen and logs a conflict.

Drift between view and source logs a `view-regen-failure` conflict.

---

**End of view generation spec (v2.2).**
