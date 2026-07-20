# Chart selection — which kind, when (SPEC OUT3)

The prose contract for `shared/scripts/charts.py`. The selection heuristics
here are **adapted knowledge from AntV's chart-visualization skills**
(`antvis/chart-visualization-skills`, MIT — see `THIRD_PARTY_NOTICES.md` at
the plugin root); the renderer and this text are ours, and no AntV code or
text is bundled. The caps table below is test-asserted against
`charts.SELECTION_CAPS` (`tests/run_charts_test.py`) — change one, change the
other, same commit (the DECK_GRAMMAR pattern).

## The one rule above all: every chart is substrate-derived

⛔ No decoration, ever. A chart exists only when real workspace numbers —
computed by the helper that already owns them — answer a reader's question.
No illustrative charts, no synthetic examples in deliverables, no re-deriving
numbers in prose that a helper already computed. A chart that can't cite its
computation doesn't render (the no-fabrication gate applies to pixels too).

## Decision table — match the DATA QUESTION to the kind

| The reader is asking | Kind | Data shape |
|---|---|---|
| "How is this changing over time?" (trend, momentum) | `line` | `{series: [{label, points: [{x, y}]}]}` |
| "How do these compare?" (ranking, magnitude across categories) | `bar` | `{categories, series}` (1 series) |
| "How do these compare across a second dimension?" (current vs target, this period vs last) | `bar_grouped` | `{categories, series}` (2–4 series) |
| "What is this made of?" (composition of one whole) | `donut` | `{slices: [{label, value}]}` |
| "What moved the number from A to B?" (decomposed change) | `waterfall` | `{steps: [{label, delta}]}` |

When none of these questions is genuinely being asked, the answer is a table
or a tile band, not a chart. Precision questions ("what exactly is the Q2
figure?") are table questions; charts answer shape questions.

## One message per chart

Each chart carries exactly ONE conclusion the reader should take away. If a
chart needs a compound sentence to explain ("revenue is up AND mix shifted
AND two stages stalled"), split it or drop all but the strongest message.
Corollaries:

- **One unit per chart.** Never mix scales on one axis — a $910K series next
  to an NPS of 62 renders the small series invisible and the chart dishonest.
  Chart the series that share the dominant unit; the table carries the rest.
- A title states the message context, not the mechanism ("KPIs vs targets",
  never "grouped bar chart of…").
- Value labels render only where they don't crowd (single-series bars, line
  endpoints, waterfall steps) — the axis carries the rest.

## Axis & label discipline

- Y axis always includes zero when the data is magnitude-like (bars always;
  lines when the honest baseline is zero). Ticks are "nice" numbers, 4–6 of
  them; the top tick clears the data max (a bar never overflows the frame).
- X labels thin themselves rather than overlap or rotate; long labels
  truncate with an ellipsis. If truncation destroys meaning, the labels are
  too long for a chart — use a table.
- Compact value formatting everywhere (1.2K / 3.4M); `unit: "$"` prefixes,
  any other unit suffixes ("3.5h").

## Caps (machine copy: `charts.SELECTION_CAPS` — test-pinned)

| Cap | Value | Why |
|---|---|---|
| max line series | 3 | more is overplotting; split the question |
| min points per line series | 2 | one point is not a trend — refuse, keep the table |
| max grouped-bar series | 4 | beyond that, grouping stops being readable |
| donut slices | 2–6 | 1 slice has no composition; >6 is unreadable — group the tail into "Other" at the caller |
| waterfall steps | 2–12 | a longer walk reads as noise — aggregate small steps |

## Refusal over empty frames (the components.py posture)

`build_chart` raises `ChartDataError` (a ValueError, the `validate_tiles`
contract) on any violated shape, an all-zero dataset, or an unknown value —
the caller NEVER charts a gap as zero and never renders an empty frame. A
refused chart means the section's existing table/tile representation of the
same numbers stands alone. That fallback is structural: a `charts` entry
never satisfies a section's content requirement, so a chart-only section
cannot exist.

## Two backends, one SVG (SPEC OUT3 §3b)

- **HTML surfaces** (premium brief): the SVG embeds inline in the section's
  `.chart-slot` — self-contained, no asset server.
- **docx / pptx**: `charts.try_chart_png(spec, brand=...)` at the render
  chokepoint — build + rasterize, `None` on refusal / leak finding / no
  rasterizer on this machine. `None` = the section renders exactly as
  pre-OUT3 (the visual_gate contract: upgrade machines that can render,
  never degrade ones that can't). Kill switch `CR_CHART_RASTER=off`.

Colors and fonts resolve ONLY through `brand.get_brand()` (per-org override
honored via the `brand=` kwarg); series colors beyond the named palette keys
are computed tints of brand colors. The visual pass checklist item for
charts: **"chart unreadable / overplotted"** (`visual_gate.CHECKLIST`).
