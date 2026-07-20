# DECK_GRAMMAR — the board-pack slide contract (SPEC OUT6)

The machine-and-prose contract for the `.pptx` slide companion. The machine copy
of every rule below is pinned as constants in `shared/scripts/deck_writer.py`
(`GRAMMAR` dict) — **if you change a number here, change it there in the same
commit** (and vice versa). `tests/run_deck_writer_test.py` asserts the pins.

## The one rule above all

**The deck derives from the SAME assembled `sections` payload the .docx was
rendered from.** One assembly, two renderers (`brief_writer.make_brief` for the
pack, `deck_writer.make_deck` for the deck). No slide renders from data the docx
section didn't already validate; a deck can never disagree with its pack.

## Scope fence

ONE kind: `board_pack`. `make_deck` refuses any other `kind` — this is the
board-pack deck, not a general deck engine. Other composers (memo-writer,
one-pager) getting a deck path is FUTURE_WORK; the chokepoint is built so that
is cheap later, but wiring them now multiplies the review surface.

## Section → slide map

| Board-pack section | Slide treatment |
|---|---|
| Exec header / §1 Executive summary | **Title slide only.** Verdict line as the title, period + org as the subtitle. The verdict IS the headline — no agenda slide, and §1's bullets do NOT get a content slide (they are the docx's page 1; the deck opens on the verdict). |
| §2 KPIs (heading matches `kpi`) | **One KPI slide:** the section's `tiles` band rendered as a shape row (same `components.validate_tiles` contract as the docx — 1–5 tiles, empty tiles refused), target-delta arrows carried in the tile labels verbatim. Below it, **ONE chart (SPEC OUT3)** when the section carries a valid `charts` spec AND this machine can rasterize — the first valid spec paints as a PNG and REPLACES the compact table beside the tiles (one message per slide; chart strings join the plan leak scan). Refusal, an invalid spec, or no rasterizer falls back to the compact KPI table, byte-identical to pre-OUT3 — the table always stays in the plan as the fallback. |
| Wins / Concerns | **One slide each,** max **5** rows, the quantify tag per row (EXEC1 discipline: a trailing `$52K` / `+9 pts` / `18.4 mo` token renders as an accent-colored tag run). Overflow past 5 rows renders one muted `+N more in the full pack` line — never a sixth row, never a smaller font. |
| Decisions logged | **One slide,** decision · owner · date rows (the section's `table` verbatim when present, else its bullets as rows). |
| Asks | **One slide,** the three-ask cap enforced (EXEC1 `MAX_ASKS`) — a fourth ask is a refusal, not a squeeze. |
| Appendix / anything else | **1–2 content slides each,** ≤ **6** bullets per slide; overflow pushes to a continuation slide (`(cont.)`) — **never shrinks the font below the floor.** Past 2 slides the remainder renders one muted `+N more in the full pack` line. |

## Pinned numbers (the machine copy lives in `deck_writer.GRAMMAR`)

| Pin | Value | Why |
|---|---|---|
| `max_bullets_per_slide` | 6 | past this the slide is a document |
| `max_rows_wins_concerns` | 5 | a board scans, it does not read |
| `max_asks` | 3 | EXEC1: >3 reader-actions is not a contract |
| `max_table_rows_per_slide` | 8 | table legibility at the font floor |
| `max_content_slides_per_section` | 2 | the deck summarizes; the pack carries detail |
| `font_floor_pt` | 12 | nothing on a projected slide below 12 pt, ever |
| `slide_w_in` / `slide_h_in` | 13.333 / 7.5 | 16:9 widescreen |

## Structural rules

- **One message per slide.** Every content slide carries exactly ONE section
  heading. Two sections never share a slide.
- **Overflow never shrinks.** Fit problems are solved by continuation slides or
  the honest `+N more` line — never by dropping below `font_floor_pt`.
- **Drop-empty (F-60).** A section with no real content — empty, or
  `(nothing logged …)` — contributes NO slide. A tile with no data is dropped
  by the caller, never rendered as an empty frame (`components.validate_tiles`
  enforces it at the chokepoint too).
- **Placeholder refusal.** Placeholder text (`[add …]`, `[insert …]`, `TBD`,
  `lorem ipsum`) mixed into real content refuses the render
  (`DeckGrammarError`). A section that is ONLY placeholder (the docx's
  sanctioned `[add asks here]`) is dropped, not rendered.
- **Substrate-derived only.** No stock imagery, no decorative graphics, no
  agenda slide, no accent stripes. The only image the deck may carry is the
  resolved brand logo (title slide, and only when the file exists on disk —
  same silent-fallback posture as `brief_writer._resolve_logo`).
- **Brand-resolved everything.** Every color and font resolves through
  `brand.get_brand()` (workspace `brand` object, per-org override honored —
  board decks are per-org documents). Unconfigured workspace = the premium
  default theme, byte-stably, no logo.
- **Leak scan before save.** Every text run in the slide plan — plus the
  resolved brand footer line, the one painted string that comes from brand
  config rather than the plan — goes through
  `docx_leak_scanner.scan_text_for_leaks` BEFORE the file is written; any
  finding raises `DeckLeakError` and NO file is produced.
- **Deterministic.** Same sections + same brand → identical .pptx slide XML,
  every fire.

## Failure posture (the render_and_persist rule, generalized)

A failed chokepoint NEVER falls back to freelance generation. If python-pptx
cannot be imported or installed, `make_deck` raises `DeckDependencyError` —
the skill says so in one line, delivers the .docx, and stops. No hand-built
deck via the generic pptx skill, no pptxgenjs, no "1–2 slides with key
bullets" improvisation. Same for a grammar or leak refusal: surface the error,
fix the sections payload, re-render — never route around the chokepoint.
