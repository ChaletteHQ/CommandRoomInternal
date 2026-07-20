<!-- exemplar-skeleton (SPEC OUT8): structure only. Nothing in this file is
     content — no name, number, or claim below may appear in a deliverable. -->
<!-- tokens: Acme Co | Northstar Partners | $240K | $180K | $60K -->

# Chart on Demand — structural exemplar (kind: chart_on_demand)

> STRUCTURE CONTRACT — annotations are the exemplar; sample lines are
> synthetic. Layout only (contract > exemplar > default).
>
> - Title IS the message, not the mechanism (CHART_SELECTION title rule): what
>   the chart shows, in the reader's terms — "Revenue in play by client", never
>   "Bar chart of value_by_org".
> - Header: VERDICT-only lead (eyebrow-excluded — a one-answer page is a
>   document, not a since-yesterday digest). The verdict is the one-sentence
>   reading of the chart: the single thing the chart says.
> - ONE section, ONE chart. Compound asks ("revenue AND pipeline mix") split
>   into sequential asks — the page says so rather than stacking charts.
> - The section carries THREE things, always: the chart (charts.py, the one
>   SVG owner — the skill never emits SVG), a one-line SOURCE statement, and a
>   TABLE TWIN of the exact same numbers. The table is the precision companion
>   AND the fallback: if the chart spec refuses (< 2 points, all-zero, a cap
>   violation), the table stands alone and the page still answers.
> - Source line states provenance + as-of date: "from N open deals on the
>   pipeline as of [date]" — the no-fabrication gate applies to pixels, so the
>   page names where every number came from.
> - No interpolation, ever: observed periods only. A gap month is an absent
>   row, never a zero point. Numbers come from the owning helper; the skill
>   re-derives nothing.
> - No blank cells in the twin: an absent-but-legitimate value is a dropped
>   row, not an empty cell.
> - An ask the substrate can't answer is a text refusal naming what IS
>   chartable — not a page, not an empty frame.

---

**Acme Co and Northstar Partners together hold most of the revenue in play right now.** [verdict — the one-sentence reading]

## Revenue in play by client
[the ONE chart: a bar of open-deal value per org, drawn by charts.py from pipeline_math.value_by_org — reader-facing org labels, ranked value-desc]

Source: from the open deals on the pipeline as of [as-of date] — stated deal values only, unpriced deals excluded (never estimated). [one-line provenance + as-of date]

[TABLE TWIN of the same numbers — Client | Revenue in play — no blank cells:]
[Acme Co | $240K]
[Northstar Partners | $180K]
[Sample Org 3 | $60K]
