<!-- exemplar-skeleton (SPEC OUT8): structure only. Nothing in this file is
     content — no name, number, or claim below may appear in a deliverable. -->
<!-- tokens: Acme Co | Sam Sample | single-customer concentration -->

# Stress Test — structural exemplar (kind: stress_test)

> STRUCTURE CONTRACT — annotations are the exemplar; sample lines are
> synthetic. Layout only (contract > exemplar > default).
>
> - Header: VERDICT-only lead (eyebrow-excluded). Verdict = the kill-risk
>   line — the single highest likelihood-times-severity failure mode.
> - Ordering rule: SAFEGUARDS FIRST. "What to Do" leads; the failure analysis
>   that justifies it follows. The reader gets the fix before the fear.
> - Visuals: the safeguard ranking renders via the table primitive, each row
>   carrying its likelihood-times-severity score next to the safeguard so the
>   reader can challenge the inputs.
> - The failure map's subsections keep a fixed order: post-mortem story →
>   attack surface → assumptions → second-order effects → the full ranked
>   safeguard list.
> - Ask close: the NEEDED reader-action ("write the hard-rethink trigger date
>   into the plan") rides the verdict; no separate ask section.

---

**The kill risk is single-customer concentration — Acme Co is [N]% of revenue.** [verdict — the kill-risk line]

## What to Do (Top Safeguards)
[ranked list, 3–5 items; each row: safeguard · the failure it blocks · score · owner (Sam Sample style) · trigger date]

## The Failure Map

### How it could die — the 18-month post-mortem
[a short narrative written from the failure looking back — the strongest failure mode as a story]

### Where a hostile insider would attack
[2–4 bullets: the weakest points as an adversary would rank them]

### Assumptions and where they break
[one line per assumption: the assumption · the break condition · what happens then]

### Second-order failures
[what the first failure knocks over — 2–3 chains, one line each]

### Every safeguard, ranked
[table: Safeguard | Failure mode it blocks | Score | Trigger date — the full list behind the top-safeguards cut above]
