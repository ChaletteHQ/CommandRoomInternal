# Skill Shape — v2.1 Spec

**Purpose:** The canonical definition of what a v2.1 Command Room skill looks like. Every new skill added to the plugin must conform. The `cleanup` skill validates conformance; non-conforming skills cannot be merged.

**Read by:** skill authors (human or agentic — the HQ `skill-creator` skill reads this when scaffolding new skills), `cleanup` (validator), and contributors adding to the plugin.

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

**`description`:** One long paragraph, 80–300 words. Shape (in order):
1. **Opening sentence:** what the skill does for the CEO. Lead with the OUTCOME, not the mechanism.
2. **Middle:** what the skill reads, writes, or produces. What connectors it uses.
3. **Trigger phrases:** a natural list ("Use when the CEO says '...', '...', '...'") — not a bullet list, narrative prose.
4. **Negative triggers:** "DOES NOT fire on [phrase] — that's [other-skill]." Explicitly disambiguate from close neighbors.

**Reject any description that:**
- Starts with "Triggers: ..." (command-line, not intent-led)
- Omits negative triggers when close neighbors exist
- Exceeds 300 words or is shorter than 50 words
- Uses "Comprehensive [thing]" or "Tool for [thing]" — these are category labels, not outcomes

---

## Required Sections

Every SKILL.md, after frontmatter, must include these sections in roughly this order. Section headers use `##` (H2).

### 1. Skill Boundary (v2.1)

Required on every skill. Disambiguates from close neighbors. Minimum 3 bullets:
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

Required. Mirror of Skill Boundary but from the output perspective. What the skill produces is not [X], [Y], [Z].

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

## Validation (run by cleanup)

For each SKILL.md in `plugin-source-v2/skills/`, verify:

1. Frontmatter parses. `name` matches folder. `description` exists and is 50–300 words.
2. Description opens with an intent statement, not a trigger list.
3. Description includes at least one "Use when the CEO says..." phrase.
4. If close neighbors exist (heuristic: other skills with shared trigger words), description includes a "DOES NOT fire on" clause.
5. `## Skill Boundary (v2.1)` section exists with at least 3 bullets.
6. If the skill references `shared/VOICE_CALIBRATION.md` or `shared/PASSIVE_CAPTURE.md`, the reference appears in a contract section (Writer Contract or Voice Calibration).
7. If the skill is a composer (produces user-facing written output), the Voice Calibration section is present.
8. No literal customer strings (grep against `entities.json` canonical names).
9. `## What It Doesn't Do` section exists.

Any violation logs to `_hq/CONFLICTS.md` with type `skill-shape-violation` and surfaces in the cleanup report.

---

## Checklist for New Skill Authors

Before submitting a new skill:

- [ ] Frontmatter `name` matches folder name exactly
- [ ] Description leads with CEO outcome, not trigger list
- [ ] Description includes natural-language triggers + negative triggers
- [ ] Description length between 50 and 300 words
- [ ] `## Skill Boundary (v2.1)` section present with neighbor disambiguation
- [ ] `## Writer Contract` present (if skill writes) referencing WORKSPACE_API.md
- [ ] `## Voice Calibration` present (if skill drafts text) referencing VOICE_CALIBRATION.md
- [ ] PASSIVE_CAPTURE referenced (if skill reads connectors)
- [ ] RELIABILITY referenced (if skill runs as scheduled task)
- [ ] `## What It Doesn't Do` section present
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
