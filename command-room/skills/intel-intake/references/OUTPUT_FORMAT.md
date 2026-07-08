# Shared Intel Post — Output Format

Created P1.8 2026-07-02 — referenced by SKILL.md Step 7 since v1 but never
shipped. The consumer side pattern-matches on the BOLD HEADERS below — keep
them byte-exact; add nothing above the title line.

## Template

```markdown
# [Title — plain-English, no internal codenames]

**What it is**: [1-2 sentences. The thing itself, not why we care.]

**Why it matters**: [1-3 sentences, GENERIC capability framing — no client
names, project details, or internal strategy. Replace client references with
capability categories ("teams running invoice automation" not "[Client]'s
QuickBooks flow").]

**Status**: shipped | beta | announced | demo-only   ← hype filter, mandatory

**Confidence**: verified | likely_accurate | unverified   ← per SKILL.md Quality Checks

**Source**: [URL] · Tier [1-3] · [date]

**Try it / read more**: [one concrete next step, or omit the line]
```

## Rules (mirror of SKILL.md Step 7 — edit both or neither)

- NO client names, project details, or internal strategy — ever.
- Confidence label on every factual finding, not just the post.
- Include the hype-filter status and source tier on every post.
- One post per intel item; don't batch multiple findings into one post.
