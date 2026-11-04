# Skill Shape — v2.1 Spec

**Purpose:** The canonical definition of what a v2.1 Command Room skill looks like. Every new skill added to the plugin should conform. Only some of these rules are checked by a suite; the Validation section below lists each rule with the suite that checks it, or says it is checked in review only.

**Read by:** skill authors (human or agentic; the HQ `skill-creator` skill reads this when scaffolding new skills), contributors adding to the plugin, and `run_guard_skill_shape_claims_test.py`, which checks this file's own claims (cr1#295).

---

## Required Frontmatter

Every `SKILL.md` must open with:

```yaml
---
name: [kebab-case, matches folder name]
description: [intent-led description — see shape below]
---
```

**`name`:** Kebab-case, unique across the plugin, matches the folder name exactly. No spaces, no underscores, no capitals.

**`description`:** One paragraph, at most 980 characters (the cap `run_guard_g11_description_budget_test.py` checks, with headroom under the host's 1,024-character limit). Shape (in order):
1. **Opening sentence:** what the skill does for the CEO. Lead with the OUTCOME, not the mechanism.
2. **Middle:** what the skill reads, writes, or produces. What connectors it uses.
3. **Trigger phrases:** a natural list ("Use when the CEO says '...', '...', '...'") — not a bullet list, narrative prose.
4. **Negative triggers:** "DOES NOT fire on [phrase] — that's [other-skill]." Explicitly disambiguate from close neighbors.

**Reject any description that:**
- Starts with "Triggers: ..." (command-line, not intent-led)
- Omits negative triggers when close neighbors exist
- Exceeds 980 characters, or holds an angle bracket or a version tag (all three checked by `run_guard_g11_description_budget_test.py`)
- Uses "Comprehensive [thing]" or "Tool for [thing]" — these are category labels, not outcomes

---

## Required Sections

A SKILL.md carries these sections after frontmatter, in roughly this order. Each one says whether it is recommended or required when a condition holds. Section headers use `##` (H2).

### 1. Skill Boundary (v2.1)

Recommended. Disambiguates from close neighbors. Minimum 3 bullets:
- **Use [this-skill] for:** single-sentence scope statement.
- **Use `[close-neighbor]` for:** what that other skill does instead.
- **Pair pattern / Does NOT / Related:** any other routing guidance.

### 2. Voice Calibration (composer skills only)

Required if the skill drafts any text on behalf of the CEO (emails, memos, updates, one-pagers). Copy the standard paragraph from `shared/VOICE_CALIBRATION.md` → "Composer Contract Header" section.

### 3. Writer Contract

Required if the skill writes to any workspace file. References `shared/WORKSPACE_API.md` and declares:
- What files the skill is the primary writer for.
- What files the skill appends to.
- What files the skill never writes to (for disambiguation with neighboring skills).

Required if the skill touches a connector: additionally references `shared/PASSIVE_CAPTURE.md` and declares that connector reads emit events per that contract.

### 4. Main Body

Free-form per skill. Typical sections:
- **For:** [who this is for — one sentence audience statement]
- **What It Does** / **How It Works** — the mechanics
- **How to Use** — trigger phrases as copy-paste examples
- **What You Get** / **Output Structure** — what the CEO gets back
- **Gotchas** — edge cases and foot-guns
- **Connected Tools** — connectors / other skills invoked
- **Scheduled mode** (if applicable) — how the skill behaves when fired on a schedule

### 5. What It Doesn't Do

Recommended. Mirror of Skill Boundary but from the output perspective. What the skill produces is not [X], [Y], [Z].

---

## Structural Invariants

### Path references

- Workspace paths resolve at runtime — always use `[WORKSPACE_ROOT]/...` as the literal placeholder. Never hard-code a specific path.
- Plugin-internal references use `shared/...` or `references/...` (relative to plugin root).

### Customer data

Per `PLUGIN_BOUNDARY.md` invariant 7 — no SKILL.md contains a literal customer name, project name, org name, or email address. Any customer-specific behavior is driven by `BUSINESS_CONTEXT.md` or user-controlled config.

### Trigger phrases

- Must be natural-language, conversational phrasing a CEO would actually use.
- Must include at least one long-form phrasing ("what do I need to know for the board meeting") — not just short commands.
- If the skill has close neighbors, must include at least one explicit negative trigger ("DOES NOT fire on...").

### Scheduled tasks

If the skill is designed to run as a scheduled task, it must reference `shared/RELIABILITY.md` and implement the scheduled-task rules defined there (skip-not-fail, missed-fire recovery, OOO detection).

### Writes outside the workspace

Forbidden. Every file the skill writes must resolve under `[WORKSPACE_ROOT]`. Writes to plugin source, OS paths, or shared locations = plugin-ending per PLUGIN_BOUNDARY.

---

## Validation

Each rule in this spec, and what checks it. "Review only" means the rule is not enforced by any suite: the author's checklist below and the fresh-context review before a pull request are its only check.

| Rule | Checked by |
|---|---|
| Description at most 980 characters, no angle brackets, no version tags, at least one tested trigger stem in its first 250 characters (G11) | `run_guard_g11_description_budget_test.py` |
| Description at most 1,024 characters and no angle brackets, mirroring the remote marketplace validator (G15) | `run_guard_marketplace_validity_test.py` |
| No real client, person or company names (G9) | `run_no_real_customer_names_test.py` |
| No literal Drive paths in skill text | `run_no_hardcoded_drive_test.py` |
| Advertised trigger phrases route to the expected skill and do not collide | `run_trigger_test.py`, with `tests/triggers.yaml` |
| The skill validator's static gates (event types in the schema, atomic writes, entity resolution, widget format, leak tokens and the rest named in its own docstring) | `run_cr_skill_validator_test.py` reports them, but only its `--strict` mode exits non-zero and the battery runs it without that flag, so a gate it reports does not fail the battery |
| Frontmatter `name` matches the folder | review only |
| Description opens with an outcome, has a "Use when" phrase, and has a "DOES NOT fire on" clause where close neighbors exist | review only |
| `## Skill Boundary (v2.1)` and `## What It Doesn't Do` (recommended, not required) | review only |
| Voice calibration on composer skills | partly `run_voice_block_coverage_test.py`: the composers it registers carry a `## Voice Block` section or cite `VOICE_CALIBRATION.md`, and carry the `voice_block_last_refreshed` date; the `## Voice Calibration` heading itself is review only |
| Writer Contract, PASSIVE_CAPTURE and RELIABILITY references where they apply | review only |
| Every write resolves under `[WORKSPACE_ROOT]` | review only (the validator's atomic-write and machine-path gates report part of it, without failing the battery) |

`run_guard_g11_description_budget_test.py`, `run_guard_marketplace_validity_test.py`, `run_no_real_customer_names_test.py`, `run_no_hardcoded_drive_test.py`, `run_trigger_test.py` and `run_voice_block_coverage_test.py` each exit non-zero on a violation of what they check, so that violation fails the battery.

This file is held to its own word by `run_guard_skill_shape_claims_test.py`: a sentence here that says a rule is enforced must name the suite that does it, every suite named here must exist, a section marked required on every skill must be carried by every skill, and the description rule must state the cap that G11 checks.

---

## Checklist for New Skill Authors

Before submitting a new skill:

- [ ] Frontmatter `name` matches folder name exactly
- [ ] Description leads with CEO outcome, not trigger list
- [ ] Description includes natural-language triggers + negative triggers
- [ ] Description at most 980 characters, with a trigger stem in the first 250
- [ ] `## Skill Boundary (v2.1)` section present with neighbor disambiguation (recommended)
- [ ] `## Writer Contract` present (if skill writes) referencing WORKSPACE_API.md
- [ ] `## Voice Calibration` present (if skill drafts text) referencing VOICE_CALIBRATION.md
- [ ] PASSIVE_CAPTURE referenced (if skill reads connectors)
- [ ] RELIABILITY referenced (if skill runs as scheduled task)
- [ ] `## What It Doesn't Do` section present (recommended)
- [ ] No hard-coded customer names, project names, or emails
- [ ] All writes resolve under `[WORKSPACE_ROOT]`
- [ ] Added to `plugin.json` skills array
- [ ] Tested via trigger test harness

---

## Template

Below is a minimal skeleton a new skill can start from:

```markdown
---
name: [skill-name]
description: "[One-sentence outcome the CEO gets.] Use when the CEO says '[phrase]', '[phrase]', '[phrase]'. [What the skill reads/writes/produces.] DOES NOT fire on '[close-neighbor-phrase]' — that's [neighbor-skill] ([what it does instead])."
---

## Skill Boundary (v2.1)

- **Use [skill-name] for:** [scope statement]
- **Use `[neighbor]` for:** [what neighbor does]
- **[Pair pattern / relationship note]**

## Writer Contract

Before writing to any workspace file, read `shared/WORKSPACE_API.md`. [Describe what files this skill writes.]

[If applicable: "Additionally, this skill implements `shared/PASSIVE_CAPTURE.md`..."]

## Voice Calibration

[Only if skill drafts text for CEO. Copy boilerplate from shared/VOICE_CALIBRATION.md.]

---

# [Skill Display Name]

**For:** [one-sentence audience statement]

## What It Does

[Mechanics]

## How to Use

[Trigger examples]

## What You Get

[Output description]

## Gotchas

- [Foot-gun 1]
- [Foot-gun 2]

## What It Doesn't Do

- [Thing it won't do 1]
- [Thing it won't do 2]
```

---

**End of skill shape spec.**

## Naming convention — feedback provenance vs normative prose (P3.3, 2026-07-02)

Feedback citations MAY name M with a date ("per M's 2026-05-20 feedback #9") —
that's provenance, and it stays. NORMATIVE sentences (rules, behavior specs,
output instructions) say **"the user"** — never M, never a real name. The two
audits found the mix drives per-client fan-out bugs: a canonical file that says
"ask M before sending" makes the model address someone else's CEO as M.
