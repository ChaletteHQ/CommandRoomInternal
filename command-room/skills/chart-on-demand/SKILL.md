---
name: chart-on-demand
description: "Render one workspace-derived chart on demand — a trend, comparison, composition, or delta walk computed by the workspace's own helpers and drawn by the shared chart engine, never invented. Fires on: 'chart revenue by client', 'chart my', 'chart the', 'graph the pipeline', 'graph my', 'show me a trend', 'trend of', 'plot', 'visualize', plus 'tune chart-on-demand'. Loose names resolve through the entity ladder before any series is built; an ask the numbers can't answer gets an honest list of what IS chartable — no interpolation, no invented numbers, the chart page states its source. Does NOT fire on 'chart of accounts' (finance vocabulary, not a chart ask), 'build the board pack' (board-pack-assembler), 'operator report' (operator-report), 'value receipt' (value-receipt), or market/industry research like 'trends in' an industry (research)."
---

# chart-on-demand

One chart, one message. The CEO says "chart revenue by client" or "graph the
pipeline" and gets a single substrate-derived chart — computed by the helper
that already owns those numbers, validated and drawn by `charts.py`, branded,
gate-checked, saved as a deliverable — or an honest refusal naming what IS
chartable. No new render path. No invented series. Ever.

## The one rule: refusal over fabrication

A chart is a claim in pixels. This skill draws only numbers the substrate can
prove, and refuses — in words — anything it can't. It never interpolates,
never smooths, never gap-fills, never extrapolates, never charts a gap as
zero, and never sketches a "roughly like this" chart. If the closed catalog
below can't answer the ask, the reply is text: what IS chartable, not a page.

`charts.py` is the ONE chart owner. This skill never emits SVG, never imports
a plotting library, never re-derives a number a helper owns. New series math
lands in the owning helper module, never in the skill and never in charts.py.

## The closed series catalog

Every chart maps the ask's data question (`shared/CHART_SELECTION.md` decision
table) onto ONE of these enumerated series. Each names the helper that owns its
numbers. Nothing outside this table is chartable in v1 — an ask that doesn't
map here gets the refusal in Step 5.

| id | ask shapes | kind | owner |
|---|---|---|---|
| `value_trend` | "trend of hours absorbed", "chart drafts per month", commitments-resolved over time | line | `value_receipt.build_trend_chart(per_month, metric=...)` over `value_receipt.compute_metrics` rows |
| `pipeline_mix` | "graph the pipeline", "pipeline mix" | donut | `pipeline_math.stage_mix(open_deals)` |
| `pipeline_by_org` | "chart revenue by client", "revenue in play by org" | bar | `pipeline_math.value_by_org(open_deals, org_names=...)` |

Open deals come from `deal_state.list_open_deals(workspace_root)`; per-month
value-receipt rows from `value_receipt.compute_metrics`. These are the same
readers the pipeline and value-receipt surfaces use — one computation, two
renders, never re-derived in prose.

**Explicitly OUT of the v1 catalog (refuse, don't fake):**

- **KPI-vs-target.** OUT7 established there is NO KPI-value substrate
  (`scorecard.compute_kpi_rows` takes model-supplied readings), so a cold
  "chart my KPIs" cannot be deterministically substrate-derived. It stays a
  mid-flow reuse inside a board pack until OUT7-P2's `kpi_snapshot` exists.
- **Booked / actual revenue.** "chart revenue by client" means revenue *in
  play* (`pipeline_by_org`). If the ask is explicitly booked/actual revenue,
  that needs QuickBooks — which clients don't connect (the standing QBO gate).
  Refuse with that stated, and offer `pipeline_by_org` instead.
- **Revenue-at-stake / dormant-value ranking.** No dollar-at-stake computation
  exists in code today, so there is no series to draw. Refuse; if the
  computation ever lands in a helper, add it as a catalog row, never inline.

## Name-bearing asks resolve first

Any ask carrying a loose name — "chart revenue for Acme Co", "trend of Sam
Sample's deals", "graph the Northstar pipeline" — runs the resolver in
`shared/ENTITY_RESOLVE_PROTOCOL.md` (§ Canonical invocation) verbatim BEFORE
any series is built. In short: call `entity_resolve.resolve_all(workspace_root,
query)` FIRST (signature order: `workspace_root`, then `query` — skipping it
and grep'ing directly is a contract violation); surface the disambiguation
widget on multiple top-tier candidates (NEVER silent-first-pick); fall back to
a flagged substring match only when it returns nothing. The protocol doc owns
the tier thresholds — do not restate them here.

The resolved `org_id` / `person_id` filters the series — a scoped
`pipeline_by_org` renders that org's deals only. A name that resolves to
nothing chartable → the Step 5 refusal, naming the resolved entity so the CEO
knows resolution worked. Group-by asks with no single entity ("revenue by
client") skip resolution and use the owning helper's reader-facing labels (the
`stage_mix` / `value_by_org` posture — never an internal token the CEO sees).

## Behavior

Resolve the workspace and plugin root per `shared/CONTRACT.md` Rule 22 FIRST —
every code block below runs with cwd = `$PLUGIN_ROOT` (that is what makes the
`sys.path.insert(0, "shared/scripts")` snippets resolve):

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "..."
```

### Step 1 — Map the ask to the catalog

Read the data question against `shared/CHART_SELECTION.md`. Pick the ONE
catalog row it matches. No match → Step 5 refusal. One chart per ask: a
compound ask ("revenue AND pipeline mix") is split into sequential asks, and
you say so — never stack two charts on one page.

### Step 2 — Resolve any names (above)

### Step 3 — Build the series through its owning helper

Call the owner named in the catalog. It returns a ready `charts` spec (or the
inputs for one) with observed points ONLY:

```python
import sys
sys.path.insert(0, "shared/scripts")
import pipeline_math, value_receipt
from deal_state import list_open_deals

open_deals = list_open_deals(WORKSPACE)            # pipeline_by_org / pipeline_mix
rows = pipeline_math.value_by_org(open_deals, org_names=ORG_NAMES)  # [{label, value}]
spec = {"kind": "bar",
        "title": "Revenue in play by client",       # the message, not the mechanism
        "data": {"categories": [r["label"] for r in rows],
                 "series": [{"label": "Revenue in play",
                             "values": [r["value"] for r in rows]}],
                 "unit": "$"}}
```

`ORG_NAMES` maps `org_id` → display name, resolved from the org records
(`org_writer.get_org_display_names(record)[0]`). If the helper returns `[]` /
`None` (no priced deals, <2 months, all-zero) → Step 5 refusal; NEVER an empty
frame.

### Step 4 — Render the premium page (D2/D3)

`chart_on_demand` is a `PREMIUM_LAUNCH_KIND` with premium-HTML as its base
format, so it renders through `premium_html.make_premium_brief` — the OUT5
rail, full gate parity (contract → voice → exec-header → render → leak scan) by
construction. Resolve the backend with
`output_profile.resolve_format_for_kind("chart_on_demand", workspace_root,
override=...)` ("as a doc" → docx twin, free via the kind registry).

The page is ONE section carrying THREE things — the chart, a source line, and
the table twin of the same numbers. The table twin is mandatory: it is the
precision companion AND the fallback if the chart spec refuses (charts never
satisfy a section's content — a chart-only section raises by design).

```python
from premium_html import make_premium_brief
from brief_path import get_brief_artifact_url

reading = "Acme Co and Northstar hold most of the revenue in play right now."  # the one-sentence reading
section = {
    "heading": "Revenue in play by client",
    "charts": [spec],                                  # the ONE validated spec
    "body": "From the open deals on the pipeline as of "
            f"{AS_OF}. Stated deal values only — unpriced deals excluded, "
            "never estimated.",                        # source + provenance
    "table": {"headers": ["Client", "Revenue in play"],
              "rows": [[r["label"], f"${r['value']:,.0f}"] for r in rows]},
}
out_path = f"{WORKSPACE}/_hq/deliverables/Chart_Revenue_By_Client_{TODAY}.html"
make_premium_brief(
    out_path,
    brief_kind="chart_on_demand",
    title="Revenue in play by client",                 # title IS the message
    subtitle=f"As of {AS_OF}",
    sections=[section],
    exec_header={"verdict": reading},                  # STANDARD_KIND: verdict required (eyebrow-excluded → verdict only)
    workspace_root=WORKSPACE, org_id=ORG_ID,           # org_id only when entity-scoped
)
# ASSERT out_path exists on disk before linking — the return is the render proof.
```

Chat reply: the one-line takeaway + the deliverable link
(`chat_output_renderer.doc_headline_link(..., get_brief_artifact_url(out_path))`)
at the bottom of the turn. No `show_widget`, no hand-composed HTML in v1 (D2 —
an in-chat chart surface is the future OUT3B-W mini-spec, not this build).

**Visual check (SPEC OUT2 §3 / §3f — checklist item 7 owns this page):** the
7-item checklist in `visual_gate.CHECKLIST` applies, with special weight on
item 7 — **"chart unreadable / overplotted"** (mixed scales, crowded labels,
too many categories; the fix is usually fewer bars/slices — the
`charts.SELECTION_CAPS` caps — or standing on the table twin alone). On the
docx-twin path run the full render-then-critique pass per
`shared/EXECUTIVE_OUTPUT_STANDARD.md` § "The visual pass"
(`visual_gate.render_preview(out_path)` → LOOK → fix + re-save AT MOST ONCE →
`visual_gate.log_visual_gate(...)` either way; `None` = no renderer, log
`rendered: false` + `skipped_reason`). On the premium-HTML base the renderer
ladder has no `.html` rung (same posture as the research kind) — eyeball the
validated spec against item 7 before saving instead; warn-only forever, a
finding never refuses a save.

### Step 5 — Refusal path (out-of-catalog, or the helper refuses)

Reply in plain words: name what IS chartable from this workspace right now
(the catalog rows whose helpers return data), and — for a booked-revenue ask —
state the QBO gate and offer `pipeline_by_org`. A refusal is text, never a
page, never an empty frame, never a downgraded freelance sketch. If the ask
named an entity that resolved, name it back so the CEO knows resolution worked.

### Step 6 — Log the receipt (every ask, D4)

Write exactly one `chart_render` event — rendered OR refused — via the
canonical gated writer:

```python
from event_gate import append_event
append_event(
    f"{WORKSPACE}/_hq/data/events.jsonl",
    {"type": "chart_render",
     "data": {"catalog_id": "pipeline_by_org", "kind": "bar",
              "org_id": ORG_ID,                 # omit when not entity-scoped
              "artifact": out_path, "refused": False}},
    holder="chart_render",
)
```

On a refusal: `{"catalog_id": <closest row>, "kind": <its kind>, "refused":
True, "reason": <why + closest catalog entry>}`, no `artifact`. With zero
recorded chart asks today, refusal receipts are how the catalog learns what the
CEO actually wants charted.

## Fences

- ⛔ `charts.py` is the ONE chart owner — never emit SVG, never import a
  plotting lib, never re-derive a number a helper owns. The stray-palette
  guard stays green with zero allowlist additions.
- ⛔ No interpolation, smoothing, gap-fill, extrapolation, or zero-for-gap.
  Observed periods only. Refusal over fabrication, always.
- ⛔ Series come ONLY from the closed catalog. New series math lands in the
  owning helper module, never in the skill, never in charts.py.
- ⛔ No hand-composed widget HTML, no `show_widget` in v1 (the
  `render_and_persist` session rule; charts have no in-chat surface yet).
- ⛔ One chart per page. Compound asks split into sequential asks.
- ⛔ The charted section ALWAYS carries its table twin — a chart never stands
  alone (it can refuse).
- ⛔ Placeholder names only in any example (Sam Sample, Acme Co, Northstar
  Partners).

## First run

None. No questionnaire, no onboarding mention (the `output_profile` posture).
`tune chart-on-demand` rides the standard FRP1 tuning rail.

## What this skill does NOT do

- It does not invent, estimate, or interpolate a number.
- It does not chart KPI-vs-target (no substrate — OUT7) or booked revenue (no
  QuickBooks — the QBO gate).
- It does not draw its own SVG or restyle a chart — `charts.py` owns pixels.
- It does not fire on `chart of accounts` (finance vocabulary), a board pack,
  an operator report, a value receipt, or industry `trends in` research.

## Routing (full trigger corpus)

Fires on (positive):

- 'chart revenue by client', 'chart revenue in play', 'chart my', 'chart the',
  'chart drafts per month', 'graph the pipeline', 'graph my', 'graph the',
  'show me a trend', 'trend of', 'plot', 'visualize', 'visualise', 'chart it',
  'graph it', plus 'tune chart-on-demand'.

Does NOT fire on (negative — owned elsewhere). The machine-readable negative
fences live in the description's does-NOT-fire clause; restated here in prose
so nothing leaks as a stray positive:

- chart of accounts — finance vocabulary, not a chart ask (the one recorded
  collision phrase in this workspace; owned by nobody, matches nothing).
- build the board pack / a board pack — board-pack-assembler (its pipeline
  appendix already carries charts mid-flow).
- operator report — operator-report (the richer lift narrative).
- value receipt — value-receipt (its quarterly roll-up already carries the MoM
  trend).
- kpi scorecard / a scorecard — kpi-scorecard (its own trends per flagged KPI).
- market/industry research (trends-in-an-industry, research a named company) —
  research (workspace-blind external brief).

Notes:

- The example phrases in the description are illustrative; the mechanical
  family is the unbracketed stems above plus this corpus. Bare chart (the word
  alone, unquoted) is deliberately NOT a positive trigger — its collision
  surface is too wide (it would hijack chart of accounts).
- Every name-bearing ask resolves through `shared/ENTITY_RESOLVE_PROTOCOL.md`
  before a series is built.
