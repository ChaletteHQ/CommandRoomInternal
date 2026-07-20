# Infographic layout registry (SPEC OUT4)

The closed set of infographic layouts. **Constrain structure, let content
vary** — the layout is chosen from this table (AntV's verified insight: a fixed
template library makes output consistent by construction, not by hope). Adapted
here to a small curated set (8 to launch, not AntV's 58); substrate-derived
content only, brand-resolved, rendered as self-contained premium HTML on the
OUT5 rail.

This file is the SELECTION table — the chooser is the model reading it, the
validator is code (`shared/scripts/infographic.py`, one shape validator per
layout). A content set that fits no row is an honest "this doesn't fit an
infographic" — never a force-fit (SPEC OUT4 §3a, acceptance #2).

## Selecting a layout

Match the CONTENT SHAPE, not the topic. Pick the row whose shape the data
already has; if none fits, decline.

| layout | content shape | reach for it when | example use (placeholder orgs only) |
|---|---|---|---|
| `ranked_list` | scored rows + optional tile band | items carry an order or a score | dormant-scan top 5, relationship moves |
| `sequence` | ordered steps | the content IS an order of operations | onboarding plan, deal-stage walkthrough |
| `comparison_2col` | A-vs-B rows | two options compared attribute by attribute | contract vs standard, vendor A/B |
| `hierarchy` | tree, ≤3 levels | nesting / reporting structure | org or project structure |
| `timeline_spread` | dated events | a chronology | relationship or project history |
| `stat_spotlight` | 1 hero number + 1–4 support tiles | one number carries the story | value-receipt quarterly highlight |
| `quadrant` | 2-axis placement | items positioned on two dimensions | effort/impact of automation candidates |
| `checklist_scorecard` | pass / warn / fail rows | a set of checks with verdicts | stress-test results, health checks |

## Required content shape (validated in code — refusal over empty frames)

Every layout declares its shape below. The validator drops empty elements
(a row with no label, a zero tile) and REFUSES when nothing renders — a layout
never paints an empty frame (the `components.py` posture). Placeholder orgs /
names only in every example (org-name leak gotcha).

- **`ranked_list`** — `{"rows": [{"label", "score"?, "note"?}] (>=2), "tiles": [{"label","value"}] (0-5)?}`. Rows are shown in given order; `score` renders as the right-hand figure; an optional stat band leads.
- **`sequence`** — `{"steps": [{"title", "detail"?}] (>=2)}`. Numbered, in order.
- **`comparison_2col`** — `{"a_label", "b_label", "rows": [{"label", "a", "b"}] (>=2)}`.
- **`hierarchy`** — `{"root": {"label", "children": [ {node}, ... ]}}`, nesting **≤3 levels** (root = level 1). Deeper is refused (collapse or split).
- **`timeline_spread`** — `{"events": [{"date", "label", "detail"?, "current"?}] (>=2)}`. Renders through the shared `components.build_timeline_html` strip.
- **`stat_spotlight`** — `{"hero": {"value", "label"}, "support": [{"label","value"}] (1-4)}`. One hero figure, 1–4 support tiles.
- **`quadrant`** — `{"x_axis": {"low","high"}, "y_axis": {"low","high"}, "items": [{"label", "x", "y"}] (>=2)}`, with `x`/`y` in `[0,1]`.
- **`checklist_scorecard`** — rows of `{label, status, note?}` (`>=2`), where each row's status reads as **pass / warn / fail**. Synonyms (ok/good/green, review/watch, bad/red…) normalize through `components.flag_key_for`.

## Fences (SPEC OUT4 §4)

- Substrate-derived only — an infographic is a VIEW of workspace truth, never
  decoration (strategy §5.6).
- Self-contained HTML only (OUT5 posture): no external font, CDN, or asset
  server. Every color/font resolves through `brand.get_brand()` — no palette
  constant lives in a layout template (the stray-palette guard enforces).
- Charts, when a layout wants one, come from `charts.py` — the ONE chart owner.
  There is no second SVG path.
- The layout set is CLOSED. Adding a layout is a spec'd change: a registry row
  here + a shape validator + a template file + a golden test — never an inline
  improvisation.

## Adding a layout (the closed-set discipline)

1. Add the row to the selection table + the shape block above.
2. Add `shared/templates/infographic/<layout>.html` (a premium-HTML fragment,
   brand-variable styling, **no literal hex**).
3. Register it in `infographic.py` `LAYOUTS` with its shape validator.
4. Add a golden + refusal case to `tests/run_infographic_test.py`.
