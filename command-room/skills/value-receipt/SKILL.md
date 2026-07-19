---
name: value-receipt
description: "A monthly 'value receipt' the CEO can read in chat and forward as a Word doc: deterministic counts from their own workspace — commitments captured and resolved on time, drafts written, briefs delivered, hours absorbed — every number computed in code, never estimated. Fires on: 'value receipt', 'monthly value receipt', 'quarterly value receipt', 'roi receipt', 'show me the receipt'. Corroborates delivery from transcripts, states its counting rules inline, and renders a conservative bottom line. Does NOT fire on 'operator report' / 'show me the value' (operator-report — the richer lift narrative), 'usage report' (usage-report — cost/volume telemetry), or 'weekly recap' (weekly-recap). Counting rules and receipt structure: Routing section in the body."
---

# value-receipt

The forwardable ROI receipt. Counts + a conservative hours-saved figure from the
customer's own activity — built to be read in chat AND handed to a board or CFO.

## Skill Boundary (v2.1)

This is not `operator-report`. That report is CEO-*self*-facing: it leads with a
synthesis paragraph ("what the month meant"), may name specific decisions and
relationships, and is shaped like the CEO's own ops update. This receipt is
*forwardable*: numbers-first, zero names or topics, one short framing line, and
a conservative hours figure. Same substrate, different audience — so it is its
own skill, and the forwardable Word doc carries counts and hours ONLY.

This is also not `usage-report` (developer-facing token/connector spend).

## The numbers are CODE, never prose math

Every figure on every surface comes from `shared/scripts/value_receipt.py`
`compute_value_receipt(...)`. You do NOT count events by hand, and you do NOT do
arithmetic in chat. The helper computes the metrics, appends one
`value_receipt_generated` event carrying the code's numbers, and returns a
receipt you render verbatim. A hand-rolled receipt leaves no event, which
`validate_receipt_ran` exposes — this is the same enforcement model as sent
reconciliation (an event is checkable; a printed sentence is not). Computing
numbers in prose is the exact failure class that model has shipped wrong before.

This is also the numeric-verification gate (`shared/SUBAGENT_VERIFICATION.md`, R6): the receipt is a canonical count surface, so its numbers are the code's, never a subagent's or a hand-tally.

## Delivered means delivered, not just fired (R4)

A `pack_run` event proves a scheduled task *fired* — it does not prove the brief actually *reached* the CEO (Bug #98's render-without-write / write-without-render classes). So "briefs delivered" is corroborated against delivery evidence, never taken on the fire alone:

- `compute_value_receipt` counts a morning briefing per calendar DAY, and prefers **render evidence** — the `briefing` event the brief actually emits when it surfaces — deduping a day's fires so a re-fire never double-counts. A bare `pack_run` with no matching render on that day is the weaker signal, not proof of delivery.
- The **scheduled-output self-audit** (cleanup R10 + the watchdog's `receipt_gap` check in `shared/scripts/task_watchdog.py`) flags fires that rendered without writing or wrote without rendering — the phantom-delivery cases that would otherwise inflate the count.
- The nightly **session-sweep** reads the actual session transcripts (the L1 episodic layer — see `references/HOW_COMMAND_ROOM_WORKS.md`), the ground truth of what surfaced. The receipt trusts that render/transcript evidence over a fire receipt.

The number is still `compute_value_receipt`'s, in code. This section is *why* that number leans on what was delivered, not on what was merely scheduled.

## When this runs

| Mode | Trigger |
|------|---------|
| **On-demand (always works)** | "value receipt", "show me the receipt", "monthly value receipt" → previous full calendar month (or "value receipt this month" for month-to-date) |
| **On-demand quarterly** | "quarterly value receipt", "quarterly roi receipt" → previous full calendar quarter, with a month-by-month table |
| **Scheduled** | the monthly-report job inside the `maintenance` task (MAINT1), due at the first fire on/after the 1st — runs the operator report AND this receipt for the previous month; on a quarter boundary also the quarterly roll-up |

The on-demand trigger ALWAYS works, even if the scheduled fire is flaky.
Scheduled-task delivery reliability is not yet something we lean on hard, so the
receipt is designed to be asked for any time and recompute from scratch — it is
window-pure with no cursor state to go stale.

## Behavior

### Step 1 — Determine the window

- Default (no qualifier) → the **previous full calendar month**.
- "this month" → first of the current month → now (month-to-date).
- "quarterly" / "quarter" → the **previous full calendar quarter** (set
  `rollup="quarter"`).

Windows are half-open: `[first-of-period, first-of-next-period)`. A May receipt
is `2026-05-01T00:00:00` → `2026-06-01T00:00:00`.

### Step 2 — Compute the receipt (one call)

Resolve the workspace per `shared/CONTRACT.md` Rule 22, then call the helper.
This is the only place numbers come from:

```bash
SESSION_DIR=$(echo "$CLAUDE_CODE_TMPDIR" | sed "s|/tmp$||")
PLUGIN_ROOT=$(ls -d "$SESSION_DIR"/mnt/.remote-plugins/plugin_* 2>/dev/null | head -1)
WORKSPACE=$(find "$SESSION_DIR/mnt" -maxdepth 5 -type d -name "_hq" 2>/dev/null | head -1 | sed 's|/_hq$||')
cd "$PLUGIN_ROOT" && python3 -c "..."
```

Inside the `python3 -c` body (cwd is `$PLUGIN_ROOT`):

```python
import sys, json
sys.path.insert(0, "shared/scripts")
from value_receipt import compute_value_receipt

receipt = compute_value_receipt(
    "<abs workspace root>",
    "2026-05-01T00:00:00",          # window_start
    "2026-06-01T00:00:00",          # window_end (exclusive)
    rollup="month",                  # or "quarter" for the roll-up
)
print(json.dumps(receipt))
```

The returned `receipt` carries: `metrics` (the counts), `hours_estimate`
(conservative, already rounded), `per_month` (rows for the quarterly table),
`sections` (a ready-to-pass `make_brief` sections list), and `summary` (the
chat line). Calling this ALSO appended the `value_receipt_generated` audit event
— you do not write any event yourself.

**Duplicate guard (v4.5.2 R4 / F-36):** if `receipt["duplicate_guard"]["skipped"]`
is `true`, an identical receipt for this window+rollup was already recorded
moments ago and no second audit event was written (the helper suppressed the
double-emit bug). Render the returned receipt normally — the numbers are the
receipt of record either way. Never call `compute_value_receipt` a second time
in the same fire to "double-check"; the first call's return value IS the receipt.

### Step 3 — Render the chat surface

Render the counts and the hours figure from `receipt["sections"]` and paste
`receipt["summary"]` **verbatim**. Never restate a number you recomputed — if it
is not in the receipt, it does not go in the message. Keep the
counts-and-hours-only discipline; do not add names or topics.

The hours line MUST carry the word "Conservative" adjacent to the figure (the
helper's `Time absorbed` section already does this — keep it). The exact
disclaimer sentence is, verbatim:

> Conservative — assumes you would have done each of these tasks yourself at average speed. The real lift is usually higher because half of these would have just dropped.

A representative chat surface:

> *"Here's your value receipt for May 2026."*
>
> *What Command Room handled*
> *— 14 commitments captured that weren't tracked anywhere else*
> *— 9 resolved on time*
> *— 12 meetings turned into structured briefs*
> *— 22 morning briefings delivered*
> *— 18 drafts written in your voice*
> *— 6 decisions logged*
>
> *Time absorbed: ~31 hours of operational overhead. Conservative — it assumes you'd have done each of these yourself at average speed; the real lift is usually higher because half of these would have just dropped.*

The monthly CHAT surface may name one or two decisions if it helps the CEO (the
operator-report precedent) — but the forwardable Word doc below must not.

### Step 4 — Write the forwardable .docx

Write to `_hq/operator-reports/Value_Receipt_<YYYY-MM>.docx` for a month, or
`_hq/operator-reports/Value_Receipt_Q<n>_<YYYY>.docx` for a quarter. Route
through `brief_writer.make_brief` with `brief_kind="value_receipt"` and the
helper's `receipt["sections"]`:

```python
import sys
sys.path.insert(0, "shared/scripts")
from brief_writer import make_brief

out_path = f"{WORKSPACE}/_hq/operator-reports/Value_Receipt_2026-05.docx"
make_brief(
    out_path,
    brief_kind="value_receipt",
    title="Value Receipt — May 2026",
    subtitle="Your operating layer, in numbers",
    sections=receipt["sections"],
    # Optional provenance footer for a forwarded doc — generic, NO customer name:
    footer_text="Figures computed from your own workspace activity log.",
)
```

`make_brief` runs the post-render leak scan automatically. The doc carries
**counts and hours only** — no names, no topics, no internal ids — so it passes
`docx_leak_scanner` (the privacy gate). Do NOT add a section that reintroduces a
person, project, or topic; if you find yourself wanting to, that content belongs
in `operator-report`, not here. `value_receipt` is intentionally NOT an
executive-header kind — it takes no `exec_header` and no `asks`.

**Format selection (SPEC OUT5).** Before rendering, resolve the backend:
`output_profile.resolve_format_for_kind("value_receipt", workspace_root,
override=...)` — an explicit "as a doc" / "as HTML" in the ask beats the
profile for that render. `"docx"` (the unconfigured default) → `make_brief`
exactly as above. `"premium_html"` → `shared/scripts/premium_html.py`
`make_premium_brief(brief_kind="value_receipt", ...)` with the SAME
`receipt["sections"]` payload and the same `footer_text` (one assembly, two
backends — the identical gate stack runs on both, parity-pinned by G16, incl.
the leak scan this doc's privacy posture leans on). Output: the same
`_hq/operator-reports/` path with `.html`; link via `get_brief_artifact_url()`;
CHECK the file exists on disk after the call before linking. Never
hand-compose HTML around the chokepoint.

### Step 5 — Surface the deliverable link

After the chat surface renders AND the .docx is written, put the H2 deliverable
link at the BOTTOM of the turn (per `CONTRACT.md` Rule 3):

```python
import sys
sys.path.insert(0, "shared/scripts")
from chat_output_renderer import doc_headline_link
from brief_path import get_brief_artifact_url
print(doc_headline_link("Value receipt — May 2026", get_brief_artifact_url(out_path)))
```

One soft, no-pressure line is the most you ever add about forwarding it (e.g.
*"Forward it as-is if it's useful — it carries counts and hours only, nothing
private."*). Never push.

### Step 6 — Quarterly roll-up

Same call with `rollup="quarter"` over a 3-month window. The helper returns
`per_month` and adds a month-by-month table to `receipt["sections"]` (counts +
hours per month). Title it "Value Receipt — Q2 2026"; subtitle the quarter span.
Everything else (numbers-from-code, leak-clean doc, no names) is identical.

### Zero-activity window

If the window has no recorded activity, the helper returns all-zero metrics and
an honest summary. Render that honestly — do not pad, do not invent:

> *"There's nothing to receipt for May yet — Command Room hasn't logged enough activity in that window. Give it a bit more use and ask again."*

The audit event is still written (the receipt ran; it found nothing). A thin
month shows thin numbers, the same zero-fabrication rule operator-report uses.

## Positioning (mandatory — read before forwarding anything)

This receipt is computed entirely from the customer's own private workspace.
Two hard rules:

- **No case-study use without explicit written opt-in.** Chalette may NOT use a
  customer's receipt numbers in marketing, case studies, decks, or any external
  material without that customer's explicit written opt-in, captured outside the
  product. The numbers are theirs.
- **Numbers never leave the customer's own activity log.** The skill never
  phones figures home: the `value_receipt_generated` event stays in the
  customer's own `events.jsonl`, and nothing about a receipt is transmitted
  anywhere. The forwardable doc goes wherever the CUSTOMER chooses to send it,
  carrying counts and hours only.

The quarterly doc may include the one-line provenance footer "Figures computed
from your own workspace activity log." (no customer name) so a board reader knows
the numbers are first-party.

## What this skill does NOT do

- Does not project ROI dollars — hours only. A dollar figure needs a salary
  assumption that does not belong in a forwarded document.
- Does not include any meeting or email content — counts and hours only.
- Does not name people, projects, or topics in the forwardable doc.
- Does not read any connector — it reads only local workspace activity, so there
  is zero connector exposure.
- Does not recompute numbers in prose — every figure comes from the helper.

## Routing (full trigger corpus)

The complete trigger family and fences for this skill, relocated verbatim from the pre-v4.5.1 description (the routing metadata is budget-capped by the platform; routing correctness is enforced mechanically by tests/triggers.yaml). Everything below remains binding at fire time.

> A monthly 'value receipt' the CEO can read in chat and forward as a Word doc: deterministic counts from their own workspace (commitments captured and resolved on time, drafts written, briefs delivered, meetings processed, quiet relationships resurfaced, decisions logged) plus a conservative hours-saved estimate. A quarterly roll-up variant adds a month-by-month table, tuned for the justify-the-spend audience the CEO forwards to a board or CFO. Every number is computed in code, never estimated in prose. Triggers: 'value receipt', 'monthly value receipt', 'roi receipt', 'quarterly value receipt', 'quarterly roi receipt', 'show me the receipt', 'forwardable value report'. Auto-runs on the 1st of each month alongside the operator report. DOES NOT fire on 'operator report' / 'operating lift' / 'show me the value' / 'monthly recap' (that's operator-report — the CEO-self-facing operating-lift report with a synthesis lead and named relationships; the value receipt is forwardable, numbers-only, no names). DOES NOT fire on 'usage report' / 'token usage' / 'where does the spend go' (that's usage-report — developer-facing token and connector spend).
