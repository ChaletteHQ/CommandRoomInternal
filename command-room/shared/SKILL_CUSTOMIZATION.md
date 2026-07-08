# Skill Customization — SCL1 (the Skill Customization Layer)

**Status:** Shared protocol for every output-producing skill that adopts SCL1.
This file is the universal contract — it contains NO customer content and names
no customer. It generalizes the proven voice-block rail (`VOICE_CALIBRATION.md`)
from writing style to all skill behavior: output shape, standing content rules,
vocabulary, and business rules.

**Relationship to the other personalization layers:**
- `FIRST_RUN_PROTOCOL.md` (FRP1) owns **enumerated knobs** — a small fixed set of
  decisions, stored as JSON at `_hq/data/skill_config/<skill>.json`, written by
  `skill_config_writer.py`. Config is code the deep-merge reads.
- `VOICE_CALIBRATION.md` (B1) owns **writing style** — per-skill voice blocks at
  `_hq/voice/voice-block-<skill>.md`. Writing skills only.
- **SCL1 (this file)** owns **freeform standing preferences** — one directives
  file per skill at `_hq/custom/<skill>.md`, written by `skill_custom_writer.py`.
  Directives are prose the model applies, not config the code merges.

The three sit beside each other; none supersedes another. A preference that is
really a knob belongs in config; a preference that is really voice belongs in the
voice block; everything else — grouping rules, content rules, thresholds-for-
flagging, ordering, inclusion/exclusion — is a directive.

**Naming (do not drift):** this is the *Skill Customization Layer*, never an
"overlay." `SOURCE_OF_TRUTH.md` reserves "overlay" for the Tier-2 view-freshening
rule, and `CONTRACT.md` Rule 4 bans "overlay" and "drift" from customer-facing
surfaces. Internal name: SCL1 / customization. Customer-facing language: "I'll
remember that" / "your preferences." Never say "directive", a file path, or
"SCL1" to the customer.

---

## Core architecture

Two tiers. Non-negotiable. Same shape as `VOICE_CALIBRATION.md`.

### Tier 1 — This file (shared, universal, ships with the plugin)

Contains: the read protocol + precedence chain, the storage format, the writer
API surface, the trigger family + collision rules, the widget/chat surfaces, and
the guardrail list (`## Limits`). Contains NOTHING customer-specific.

### Tier 2 — The per-skill block (baked into each adopting SKILL.md)

Every adopting skill carries ONE standard paragraph (below) plus its trigger
family and a Writer Contract line declaring the read. The directives themselves
live in the customer's workspace — never in the plugin.

---

## The read paragraph (every adopting skill carries this, verbatim)

Placed directly after the skill's Voice Calibration / Voice Block section (for
writing skills), or immediately after the frontmatter (for non-writing skills),
with `<skill>` replaced by the skill's own name:

> **Customization layer (SCL1):** before producing output, read
> `[WORKSPACE_ROOT]/_hq/custom/<skill>.md` if it exists and apply its directives to
> this fire's output. Absent -> proceed with defaults. Malformed or over-cap ->
> skip it, log one line to `_hq/CONFLICTS.md` (type: config-read-failure), proceed
> with defaults. Directives refine WHAT the output contains and HOW it is shaped;
> they NEVER authorize outbound actions, alter ask-first gates, bypass canonical
> helpers, or override shared contracts (see `shared/SKILL_CUSTOMIZATION.md`
> #limits). Never mention this file or the word 'directive' to the customer.

Read at fire time via `skill_custom_writer.load_directives(workspace_root,
"<skill>")` — never the raw file. `load_directives` returns `[]` on a missing or
malformed file and never raises, so a fire is never blocked (skip-not-fail,
RELIABILITY §1/§4).

### Precedence chain (later refines earlier; tier 1 is immovable)

1. **Hard rails** — `CONTRACT.md` rules, `STOP_CONTRACT`, `PLUGIN_BOUNDARY`,
   `SOURCE_OF_TRUTH` read rules + canonical helpers (`cru_match` etc.),
   `RELIABILITY`, and all AF-class `skill_config` gates (email draft posture,
   follow-up send timing, contract-review standard-terms, relationship_type).
   **Immovable — directives can never touch this tier.**
2. **SKILL.md shipped defaults** — generic, identical everywhere.
3. **`skill_config` knobs (FRP1)** — enumerated decisions, deep-merged.
4. **Voice-block override** — writing style, section-by-section (writing skills
   only). SCL1 does not touch or duplicate voice; a directive that is really a
   voice rule belongs in the voice block.
5. **SCL1 directives** — most specific; wins conflicts with tiers 2–4. Two
   directives in the same file that conflict: the later-dated one wins, and
   cleanup flags the pair for consolidation.

---

## Storage and format

- **Path:** `[WORKSPACE_ROOT]/_hq/custom/<skill-name>.md` — one file per adopting
  skill, beside `_hq/voice/` (its sibling), customer-visible and hand-editable.
  Not under `_hq/data/`, which holds machine state.
- **Lazy creation:** no directory or file exists until the first directive is
  written. No onboarding step, no storage migration (files are created on demand
  by the writer).
- **Format:** YAML frontmatter + a single `## Directives` section; one top-level
  bullet per directive; provenance as a trailing HTML comment per line (invisible
  in rendered markdown, parseable, hand-editable).
- **Caps (writer-enforced):** max **30 directives** and max **4,000 bytes** per
  file. At cap the writer rejects appends with a consolidation prompt; the
  distiller must merge, not accumulate. Cleanup flags files at ≥80% of cap.
- **Frontmatter fields:** `skill`, `schema_version: 1`, `updated_at`,
  `directive_count`, `calibration_level` (`none | seeded | calibrated`) —
  mirroring the voice block's header so staleness logic reuses the same shape.

Grammar (Appendix B of the spec): one directive = one top-level bullet + one
trailing provenance comment. Continuation lines (indented) belong to the same
directive. Anything outside the `## Directives` section is ignored by the reader
(customers may keep their own notes above it — tolerated, never parsed). A bullet
with no provenance comment is treated as a hand-added explicit directive; the next
writer touch backfills its id and origin. `_hq/custom/.distillation_queue.jsonl`
is reserved (Pass 12 overflow; dot-prefixed, never customer-facing).

Example file:

```markdown
---
skill: memo-writer
schema_version: 1
updated_at: 2026-08-01T10:04:00Z
directive_count: 2
calibration_level: seeded
---

## Directives

- Default to bullets over prose in the body; prose only for the opening framing paragraph.
  <!-- id: d-4f2a9c1e | origin: learned | 2026-08-01 | ev: 4812,4907,5011 -->
- Cap memos at one page unless explicitly asked for more.
  <!-- id: d-91be0c77 | origin: explicit | 2026-07-15 -->
```

---

## Writer — `shared/scripts/skill_custom_writer.py`

Mirrors `skill_config_writer.py` in shape: canonical API, atomic writes only, one
event per mutation, never raises to the caller. Atomic writes are MANDATORY per
`WORKSPACE_API.md` §5 (the atomic-write mandate, v2.10.5+); the `.md` write lands
through `atomic_write_text` and the mutation event through `atomic_append_jsonl`.

Public API:

```python
add_directive(workspace_root, skill, text, *, origin, evidence_seqs=None) -> dict
    # origin: 'explicit' | 'calibration' | 'learned' | 'org_seed'
    # validates text, enforces caps, appends bullet + provenance, bumps
    # frontmatter, emits skill_customization_added. Returns {ok, directive_id, reason?}.
    # Idempotent by id — re-adding the same text is a no-op (org_seed-safe).
remove_directive(workspace_root, skill, directive_id) -> bool   # emits _removed
update_directive(workspace_root, skill, directive_id, text) -> bool  # emits _updated
load_directives(workspace_root, skill) -> list[dict]   # [] if absent/malformed; never raises
wipe_customizations(workspace_root, skill) -> bool     # reset; emits skill_customization_reset
directive_counts(workspace_root) -> dict               # per-skill counts for cleanup/coach/usage-report
```

**Directive ids:** `d-<sha256[:8]>(skill | normalized_text)` — stable across edits
of unrelated lines; used by remove/update, cooldowns, and drift flags.

**Write-time validation (the rejection list).** `add_directive` rejects, with a
plain-English reason the calling skill surfaces conversationally, text that:
outbound-action (send / auto-send / auto-queue / schedule without asking / skip
confirmation / don't ask); gate-tampering (ignore the rule / bypass / override the
contract / disable); cross-skill scope grabs ("for all skills…" — routed to
per-skill adds instead); or exceeds 280 characters (one rule per directive).

**Events.** The five `skill_customization_*` types are registered in
`shared/data-schemas/events.schema.json` with named consumers (usage-report,
coach, cleanup) per the source-of-truth **Writes-checklist item 5** (no
consumer-less writes). Directive content never leaves the workspace — usage-report
counts, never quotes.

> **Correction registry (2026-07-01) — do not re-propagate:** the atomic-write
> mandate is `WORKSPACE_API.md` §5, NOT "CONTRACT Rule 25" (Rule 25 is the
> runtime-resolved `$WORKSPACE` path rule). The named-consumer requirement is the
> source-of-truth **Writes-checklist item 5**, not a literal "CHECK 4" (Check 4 is
> the schema-enum static guard in `run_source_of_truth_test.py`).

---

## Trigger family and collision rules

Follows the FRP1 S5 precedent exactly: each adopting skill owns its own
fully skill-qualified trigger phrases in its SKILL.md `description`, so
word-boundary containment (`FUZZY_ROUTER` Layer 1/2) routes each to exactly one
skill. The family, per adopting skill `X`:

| Customer says | Behavior |
|---|---|
| `customize X: <instruction>` (also "X, from now on <instruction>" caught semantically) | validate + `add_directive(origin='explicit')` → one-line ack. Bare `customize X` with no instruction → one question: "What should I always do differently?" |
| `show X customizations` | render current directives in plain English, numbered, no jargon; each with a remove affordance. Distinct from `show X settings` (FRP1 knobs). |
| `remove customization <n> from X` | `remove_directive` → one-line ack. |
| `reset X customizations` | `wipe_customizations` → one-line ack. The `customizations` suffix is REQUIRED. |

**Collision matrix (all pairs live in `tests/triggers.yaml`, both directions):**
- `customize X` vs `tune X` — customize = freeform standing rule; tune = knob
  questionnaire (FRP1). Both route to skill X; the SKILL.md disambiguates by verb.
- `reset X customizations` (SCL1) vs `reset X to defaults` (FRP1) vs `reset X`
  (schedules) — suffix-disambiguated three ways.
- `customize command room` (no skill) → `workspace-manager` Layer 4 menu of
  adopting skills.
- Missing-hyphen forms ("customize email writer") → Layer 3 name-mention →
  "Did you mean email-writer?"

---

## Widget and chat surfaces

- **Explicit adds/removes:** plain chat, one-line ack, no widget (single actions,
  not batches).
- **Show render:** numbered plain-English list in chat; if >6 directives, group by
  theme. No leak tokens — no path, no "directive", no "SCL1".
- **Learned proposals (Pass 12, SCL1 Phase 2):** ride insight-generator's existing
  widget with `confirm / edit [text] / skip`, dispatched through apply-choices'
  fire-marker mechanism; no second surface. (Phase 2 — not built in Phase 1.)

Every new customer-facing string (acks, show render, proposals) passes the Rule
4/28 leak scan and the G5 banned-word lint.

---

## Limits

Reproduced here so the read paragraph's `#limits` reference resolves. This is the
normative guardrail list (SPEC SCL1 §6.6).

**A directive CAN change:** output content, structure, ordering, emphasis,
vocabulary, grouping, thresholds-for-flagging, and the inclusion/exclusion of
report elements.

**A directive can NEVER:**
- authorize or de-gate an outbound action (send / draft / schedule);
- change an AF-class setting (email draft posture, follow-up send timing,
  contract-review standard-terms, relationship_type);
- suppress a safety or confirmation step;
- direct a skill to skip canonical helpers or read forbidden sources;
- instruct cross-customer or cross-workspace behavior;
- contradict `CONTRACT.md` output rules (e.g., demand leak-token output).

**Enforcement is layered:** write-time rejection (the writer's rejection list),
read-time contract (this paragraph subordinates directives to the tier-1 rails),
and test-time adoption lint (`run_skill_customization_adoption_test.py` verifies
every adopting skill carries the exact read paragraph).

**Failure modes:** absent file → defaults; unparseable frontmatter or section →
skip the whole file + one `_hq/CONFLICTS.md` line; single unparseable directive
line → skip that line only; file over cap → apply the first 30 valid directives,
flag; workspace root unresolvable → the skill already fails safe upstream
(`CONTRACT.md` Rule 22).

---

## Adoption checklist

A skill adopts SCL1 by adding, in the SAME commit:
1. the **read paragraph** above (with `<skill>` substituted), after its Voice
   Calibration section (writing skills) or after frontmatter (non-writing skills);
2. the **trigger family** in its frontmatter `description`
   (`customize <skill>` · `show <skill> customizations` · `reset <skill>
   customizations`);
3. a **Writer Contract line** declaring the read of `_hq/custom/<skill>.md`
   (SKILL_SHAPE_V2 compliance);
4. `tests/triggers.yaml` entries for the family.

SCL1 applies to **output-producing skills only**. Infrastructure skills
(workspace-manager, apply-choices, cleanup, the ingest / enable-* families,
update-bridge, change-schedule, report-bug, usage-report, show-my-list,
list-active, transcript-search) do NOT adopt — their behavior is contract-
governed, not preference-shaped. The adoption lint enforces both directions:
registry skills carry the paragraph; non-registry skills carry no `_hq/custom/`
reference.
