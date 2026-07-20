<!-- exemplar-skeleton (SPEC OUT8): structure only. Nothing in this file is
     content — no name, number, or claim below may appear in a deliverable. -->
<!-- tokens: Acme Co | $478K | $470K | 118% | 125% -->

# KPI Scorecard — structural exemplar (kind: kpi_scorecard)

> STRUCTURE CONTRACT — annotations are the exemplar; sample lines are
> synthetic. Layout only (contract > exemplar > default).
>
> - Header: brief-family kind — VERDICT lead (the single most decision-relevant
>   KPI move this period) THEN the CHANGED / DECIDE / NEEDED eyebrow. Sibling to
>   weekly_recap, not a document kind.
> - Every scorecard states its data-through date in the subtitle — the numbers
>   are only as current as the substrate they were read from.
> - Section order is fixed. Section 1 opens with a KPI TILE BAND (top 4–5 stat
>   tiles, target-delta arrows) ABOVE the KPIs-vs-Targets table. The Scorecard
>   detail table adds Streak + Watch columns. Trends render per FLAGGED KPI
>   only (one chart owner — charts.py; the table is the record either way).
> - Watch flags are threshold-mechanical (a green→warn crossing), labeled as
>   such — never editorializing dressed as data.
> - Needs attention is capped at 3. The monthly scorecard and the quarterly QBR
>   pre-read are ONE generator: quarter adds a Decisions-logged section and a
>   prior-quarter note, nothing else.
> - No blank cells: an absent-but-legitimate value renders an em dash.

---

**One KPI crossed below target this period: NRR, 118% against a 125% target.** [verdict]

CHANGED  [n up / m down vs last period; k/total on or above target]
DECIDE   [which off-target KPI, if any, needs an intervention this period]
NEEDED   [count of flagged KPIs — a real "nothing" when none crossed]

## 1. KPIs vs Targets
[TILE BAND first: 4–5 stat tiles, each metric + target-delta arrow — e.g. "$478K" under "MRR ▲"]
[then the table: Metric | Current | Target | Vs target | Trend — no blank cells; the dollar Current-vs-Target bar renders where charts.py can]

## 2. Scorecard
[the detail table: KPI | Actual (with movement arrow) | Target | Δ | Streak | Watch — one row per KPI, ⚠ only on a fresh crossing]

## 3. Trends for flagged KPIs
[one trend line per KPI flagged below; the table above is the complete record when this machine can't render charts]

## 4. Decisions logged this quarter
[QUARTER ONLY — one line per decision logged in the quarter; omitted on a monthly scorecard]

## 5. Needs attention
[up to 3 one-liners for the flagged KPIs, watch first; "(every KPI on target or better this period)" when nothing is off]
