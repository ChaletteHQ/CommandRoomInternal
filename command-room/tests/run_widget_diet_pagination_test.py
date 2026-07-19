#!/usr/bin/env python3
"""Scaffold-diet + paginate-by-design invariants (T2, F2 delivery rework).

The delivery contract moved from an impossible file_uri handoff (Bug #67) to
"paginate by design, relay each validated page's bytes as show_widget's
`widget_code`." Two properties must hold for that to be faithful on the live
runtime:

  1. The fixed scaffold (CSS + JS) is diet-minified at render time — comments
     and inter-token whitespace stripped — WITHOUT changing any selector, id,
     class, statement, or the JS placeholders. The quote-aware CSS minifier in
     particular must preserve attribute selectors whose value contains a space
     (`[style*="display: block"]`).
  2. A page of ~10 rows fits comfortably inside one Cowork Read (25K-token cap)
     so the widget_code relay is mechanical, and pagination slices the full set
     across section boundaries in order with correct metadata.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import chat_output_renderer as r  # noqa: E402
from widget_transport import render_and_persist  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(f"{label}" + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


# Rough bytes→tokens factor for HTML (conservative; real tokenizers pack ~2.4).
_BYTES_PER_TOKEN = 2.4
# Cowork Read page cap; a single page must fit with margin.
_READ_CAP_TOKENS = 25_000
# show_widget accepted a ~40KB improvised widget live (FS-08 probe); keep any
# single page under that empirical ceiling.
_PAGE_BYTE_CEILING = 40_000


def _brain_row(i: int) -> dict:
    return {
        "n": i,
        "name": f"Acme Co {i}",
        "context": ("likely deal · proposal language in your Jul 8 sent mail · "
                    "no pipeline record"),
        "data": {"id": f"bp_{i}"},
        "actions": ["confirm proposal", "dismiss proposal", "snooze proposal 7d"],
    }


def main() -> int:
    # ---- 1. Diet: minification is effective + non-destructive ---------------
    check("CSS minified smaller than source",
          len(r._WIDGET_CSS_MIN) < len(r._WIDGET_CSS))
    check("JS minified smaller than source",
          len(r._WIDGET_JS_TEMPLATE_MIN) < len(r._WIDGET_JS_TEMPLATE))
    # Diet round 2 (T2.2): two bounds. The EVERYTHING-included monolith must
    # stay bounded, and the scaffold a real heavy row-list page actually
    # SHIPS (conditional emission) must stay bounded too. The 12KB stretch
    # target was not reached (measured floor ~15.4KB with the full
    # F-17/notes/flash feedback layer intact — T2.2 BUILD_REPORT deviation).
    # t3 re-pin (M rulings FB-3/FB-4/FB-10): row-list pages now CARRY the
    # button layer (Done/Send/Draft one-tap primaries), the inline-editable
    # body machinery, the Later…-merge Snooze-rest fallback, and the FB-1
    # fix — ~1.4KB monolith growth (21.4→22.9KB) and ~3.3KB on the composed
    # heavy page (15.4→18.7KB… measured 19.3KB with the probe row). These
    # bounds guard the NEW floor; a regression past them is unexplained
    # scaffold growth, not the rulings.
    scaffold = len(r._WIDGET_CSS_MIN) + len(r._WIDGET_JS_TEMPLATE_MIN)
    check("minified full scaffold under 23.5KB (~22.9KB at t3; 21.4KB at "
          "T2.2, 35KB pre-diet)",
          scaffold < 23_500, f"scaffold={scaffold}")
    _probe_view = {
        "header": "probe", "source_skill": "commitment-triage",
        "counters": [{"label": "Open", "value": 3}],
        "sections": [{"title": "S", "items": [{
            "n": "commitment_seq_1", "display_n": 1, "name": "Probe row",
            "context_tag": "41 days old · due Jul 22",
            "actions": ["resolved", "push to [date]", "skip"]}]}],
        "pagination": {"page": 1, "total_pages": 2, "has_more": True},
    }
    import re as _re
    _probe_html = r.render_chat_output_widget(_probe_view, wrapper="fragment")
    _style = _re.findall(r"<style[^>]*>(.*?)</style>", _probe_html, _re.DOTALL)[0]
    _script = _re.findall(r"<script[^>]*>(.*?)</script>", _probe_html, _re.DOTALL)[0]
    composed = len(_style) + len(_script)
    check("composed scaffold for a heavy row-list page under 20KB "
          "(t3 re-pin — the FB-4 primary-button layer rides every row page)",
          composed < 20_000, f"composed={composed}")
    # Conditional emission actually conditions: the row-list page ships no
    # email-draft CSS, but SINCE t3 FB-4 it DOES ship the button layer —
    # commitment rows render the Done one-tap primary (the t2.2-era
    # "drops the button blocks" assertions inverted by M's ruling).
    check("row-list page drops the email-draft CSS", ".cr-email-draft" not in _style)
    check("row-list page ships the button CSS (FB-4 primaries)",
          "button.cr-action" in _style)
    check("row-list page ships the button JS (crToggle, FB-4 primaries)",
          "function crToggle(" in _script)
    check("row-list page renders the Done primary button",
          "cr-action-primary" in _probe_html)
    check("row-list page ships the select JS (crSel)",
          "function crSel(" in _script)
    check("row-list page ships the armed-state CSS", ".cr-select-armed" in _style)

    # Quote-aware CSS: attribute selector with a literal space survives.
    check("attribute selector space preserved",
          '[style*="display: block"]' in r._WIDGET_CSS_MIN)
    # Structural anchors validate_rendered_widget / the JS depend on.
    for token in (".cr-selected", ".cr-select-armed", ".cr-action-input",
                  "@keyframes cr-flash-open", '2713'):
        check(f"CSS min keeps {token!r}", token in r._WIDGET_CSS_MIN)
    for token in ("__TOTAL_ITEMS__", "__CR_SRC__", "crApplyAll",
                  "apply choices: ", "crSel(", "crToggle("):
        check(f"JS min keeps {token!r}", token in r._WIDGET_JS_TEMPLATE_MIN)
    # No line-comment survived the JS minify; no block comment survived CSS.
    check("JS min dropped whole-line // comments",
          not any(l.startswith("//") for l in r._WIDGET_JS_TEMPLATE_MIN.split("\n")))
    check("CSS min dropped block comments", "/*" not in r._WIDGET_CSS_MIN)
    # Harder minify (T2.2) stays quote-aware: declaration-colon spaces trim,
    # final semicolons drop, the quoted attribute selector above survives.
    check("CSS min trims declaration-colon spaces", "color:#E8E0D6" in r._WIDGET_CSS_MIN)
    check("CSS min drops final semicolon before }", ";}" not in r._WIDGET_CSS_MIN)

    # ---- 2. Rendered page still validates + fits the relay budget -----------
    big = {
        "header": "Staff Meeting — 44 open",
        "source_skill": "cr-brain",
        "sections": [{"title": "MONEY", "items": [_brain_row(i) for i in range(1, 45)]}],
    }
    import tempfile
    persist = tempfile.mkdtemp()
    t1 = render_and_persist(data_view=big, wrapper="fragment",
                            persist_dir=persist, name_hint="staff-meeting",
                            page=1, page_size=10)
    page_html = t1["html"]
    r.validate_rendered_widget(page_html)  # raises on failure
    pag = t1["pagination"]
    check("page 1 has pagination metadata", pag is not None)
    check("total_pages computed (44 rows / 10)", pag["total_pages"] == 5,
          f"got {pag['total_pages']}")
    check("page 1 has_more", pag["has_more"] is True)
    n_rows = sum(len(s["items"]) for s in
                 r.paginate_data_view(big, page=1, page_size=10)["sections"])
    check("page 1 holds page_size rows", n_rows == 10, f"got {n_rows}")
    tokens = len(page_html) / _BYTES_PER_TOKEN
    check("a 10-row page fits one Cowork Read with margin",
          tokens < _READ_CAP_TOKENS * 0.75,
          f"~{int(tokens)} tokens")
    check("a 10-row page under the show_widget byte ceiling",
          len(page_html) < _PAGE_BYTE_CEILING, f"{len(page_html)} bytes")
    check("pagination position line rendered + teaches show more",
          'class="cr-pagination"' in page_html and "show more" in page_html)

    # ---- 3. Pagination slices correctly across a section boundary -----------
    multi = {
        "source_skill": "cr-brain",
        "sections": [
            {"title": "MONEY", "items": [_brain_row(i) for i in range(1, 8)]},      # 7
            {"title": "IDENTITY", "items": [_brain_row(i) for i in range(100, 106)]},  # 6
        ],
    }
    p1 = r.paginate_data_view(multi, page=1, page_size=10)
    # page 1 = 7 money + 3 identity, preserving order + section grouping
    check("page 1 keeps both sections when the boundary splits",
          [s["title"] for s in p1["sections"]] == ["MONEY", "IDENTITY"])
    check("page 1 money section full (7)",
          len(p1["sections"][0]["items"]) == 7)
    check("page 1 identity section partial (3)",
          len(p1["sections"][1]["items"]) == 3)
    p2 = r.paginate_data_view(multi, page=2, page_size=10)
    check("page 2 drops the now-empty money section",
          [s["title"] for s in p2["sections"]] == ["IDENTITY"])
    check("page 2 identity remainder (3)",
          len(p2["sections"][0]["items"]) == 3)
    check("page 2 is the last page", p2["pagination"]["has_more"] is False)

    # ---- 4. Bounded surfaces: no pagination when page omitted ---------------
    small = {"source_skill": "commitments",
             "sections": [{"title": "OPEN", "items": [_brain_row(1)]}]}
    t_small = render_and_persist(data_view=small, wrapper="fragment",
                                 persist_dir=persist, name_hint="commitments")
    check("bounded fire returns no pagination block",
          "pagination" not in t_small)
    check("bounded fire renders no position line",
          'class="cr-pagination"' not in t_small["html"])

    # ---- 5. Byte-budget fit (T2.1 → T2.2 binary search) ----------------------
    # T2.2 replaced the halving sequence with a binary search over
    # requested.._MIN_PAGE_SIZE: the LARGEST genuinely-fitting size wins
    # (halving couldn't pick 6-9, or 11-14 on a 15 request). The page-1
    # anchor + determinism + over_budget flag are unchanged.
    import shutil
    from widget_transport import (WIDGET_PAGE_BYTE_BUDGET, _MIN_PAGE_SIZE,
                                  _fit_page_size)

    def _heavy_row(i):
        # A real-shape commitment-triage row: long title, dated context tag,
        # evidence line, the full 7-verb promise set (one required-input verb
        # brings a when-text wrapper), note affordance implied by the item.
        return {"n": f"commitment_seq_{i}", "display_n": i,
                "name": f"Sample commitment {i} with a realistically long title about sending Quinn the revised onboarding scope",
                "context_tag": "you owe · 41 days old · due Jul 22 · still on your plate?",
                "body_lines": ["evidence: matched from a meeting capture on 2026-07-01"],
                "actions": ["resolved", "push to [date]", "drop", "not mine",
                            "make task", "never track this", "skip"]}

    _tmp = tempfile.mkdtemp()
    try:
        # 5a. Uniform-heavy view, requested 25: the search must land on the
        # LARGEST fitting size — probe(k) fits, probe(k+1) doesn't.
        hdv = {"title": "Fit probe", "surface": "commitment-triage",
               "source_skill": "commitment-triage",
               # The real triage page carries the 5-tile bucket band — the
               # density gate measures the SHIPPED shape, not a bare fixture.
               "counters": [{"label": "Open", "value": 125},
                            {"label": "You owe", "value": 40},
                            {"label": "Owed to you", "value": 50},
                            {"label": "Unowned", "value": 20},
                            {"label": "Unconfirmed", "value": 15}],
               "sections": [{"heading": "S", "items": [_heavy_row(i) for i in range(1, 126)]}]}
        t1 = render_and_persist(data_view=hdv, wrapper="fragment", persist_dir=_tmp, page=1, page_size=25)
        eff = t1["pagination"]["page_size"]
        check("fit: heavy page 1 fits the byte budget", len(t1["html"]) <= WIDGET_PAGE_BYTE_BUDGET)
        check("fit: shrank below the requested 25", eff < 25, f"eff={eff}")

        def _page1_bytes(size):
            probe = r.paginate_data_view(hdv, page=1, page_size=size)
            return len(r.render_chat_output_widget(probe, wrapper="fragment"))

        check("fit: chosen size is genuinely the LARGEST that fits "
              "(size+1 exceeds the budget)",
              _page1_bytes(eff) <= WIDGET_PAGE_BYTE_BUDGET
              and _page1_bytes(eff + 1) > WIDGET_PAGE_BYTE_BUDGET,
              f"eff={eff}: {_page1_bytes(eff)}B, +1: {_page1_bytes(eff + 1)}B")
        # The halving sequence from 25 could only pick {25, 12, 6, 3}; the
        # binary search is exercised for real iff the true max is none of
        # those. Assert conditionally so a future row-weight change can't
        # make this check vacuously pin an accidental value.
        if eff not in (25, 12, 6, 3):
            check("fit: binary search picked a size halving never could",
                  True)
        # Stability: page 5 uses page-1's size (deterministic anchor).
        t5 = render_and_persist(data_view=hdv, wrapper="fragment", persist_dir=_tmp, page=5, page_size=25)
        check("fit: eff page_size stable across pages",
              t5["pagination"]["page_size"] == eff)
        check("fit: later page also fits", len(t5["html"]) <= WIDGET_PAGE_BYTE_BUDGET)

        # 5b. Heterogeneous view (light head, heavy tail): the PAGE-1 anchor
        # keeps the requested size; a requested-page probe would shrink it.
        mixed_items = [{"n": i, "name": f"Row {i}", "actions": ["mark done", "drop"]} for i in range(1, 11)]
        mixed_items += [_heavy_row(i) for i in range(11, 60)]
        mdv = {"title": "Anchor probe", "surface": "commitment-triage",
               "sections": [{"heading": "S", "items": mixed_items}]}
        m1 = render_and_persist(data_view=mdv, wrapper="fragment", persist_dir=_tmp, page=1, page_size=10)
        m3 = render_and_persist(data_view=mdv, wrapper="fragment", persist_dir=_tmp, page=3, page_size=10)
        check("fit: page-1 anchor — light page 1 keeps requested size", m1["pagination"]["page_size"] == 10)
        check("fit: page-1 anchor — page 3 uses page-1's size, not its own", m3["pagination"]["page_size"] == 10)
        # 5c. Light rows: no shrink at all.
        light = [{"n": i, "name": f"Row {i}", "body_lines": ["x" * 100]} for i in range(1, 40)]
        ldv = {"title": "Light probe", "sections": [{"heading": "S", "items": light}]}
        tl = render_and_persist(data_view=ldv, wrapper="fragment", persist_dir=_tmp, page=1, page_size=10)
        check("fit: light rows keep the requested page_size", tl["pagination"]["page_size"] == 10)
        # 5d. Monster rows (T2.1 review F-5): floor terminates, over_budget flagged.
        monster = [{"n": i, "name": "M" * 200, "body_lines": ["y" * 20000]} for i in range(1, 5)]
        xdv = {"title": "Monster probe", "sections": [{"heading": "S", "items": monster}]}
        tx = render_and_persist(data_view=xdv, wrapper="fragment", persist_dir=_tmp, page=1, page_size=10)
        check("fit: monster rows terminate at the floor", tx["pagination"]["page_size"] == _MIN_PAGE_SIZE)
        check("fit: over-budget floor page is flagged", tx["pagination"].get("over_budget") is True)
        check("fit: fitted pages carry no over_budget flag", "over_budget" not in t1["pagination"])

        # ---- 6. Density gate (T2.2 target, t3 re-pin) ------------------------
        # T2.2 hit 12-15 heavy real-shape rows per 40KB page. t3 (M rulings
        # FB-4/FB-10) deliberately spends bytes on the row: Done/Send/Draft
        # one-tap primaries + the inline-editable body. Measured cost on the
        # heavy mixed fixture: 11 → 9 rows/page (~2 rows) — accepted in the
        # t3 BUILD_REPORT as the density tradeoff for one-tap ergonomics;
        # the binary-search fit absorbs it (more pages, same contract).
        # This gate now pins the NEW floor: ≥9 heavy rows per page.
        dens = _fit_page_size(hdv, "fragment", 15)
        check("density: >=9 heavy real-shape rows fit per page (t3 floor)",
              dens >= 9, f"fit picked {dens} rows")
        dv = render_and_persist(data_view=hdv, wrapper="fragment",
                                persist_dir=_tmp, page=1, page_size=15)
        check("density: the 15-requested page renders >=9 rows and fits",
              dv["pagination"]["page_size"] >= 9
              and len(dv["html"]) <= WIDGET_PAGE_BYTE_BUDGET,
              f"eff={dv['pagination']['page_size']}, {len(dv['html'])}B")
        # Display hygiene rides along: wire ids stay in data-n, the visible
        # row number is display_n.
        check("density page: wire id present as data-n",
              'data-n="commitment_seq_1"' in dv["html"])
        check("density page: wire id never visible as row-number text",
              ">commitment_seq_1.<" not in dv["html"])
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    if failures:
        print(f"\nwidget diet/pagination FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"widget diet + paginate-by-design: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
