#!/usr/bin/env python3
"""Conservative intra-line JS tightening (_tighten_js_line, FB-CTS1 diet).

The scaffold diet's JS minifier gained intra-line tightening: spaces adjacent
to punctuation drop, but the rules are deliberately conservative — any line
containing '/' is exempt entirely (regex/comment/division safety), quoted
strings survive verbatim (quote-aware, escape-aware), a space between two word
characters always survives, and '+ +' never collapses to '++'. This suite pins
those rules unit-by-unit, proves idempotence over the REAL emitted payload,
and confirms a rendered page keeps the critical wire literals intact.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import chat_output_renderer as r  # noqa: E402

tighten = r._tighten_js_line

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(f"{label}" + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # ---- 1. Unit rules ------------------------------------------------------
    got = tighten("const s = 'a b  c' + x;")
    check("quoted strings survive verbatim, punct-adjacent spaces drop",
          got == "const s='a b  c'+x;", f"got {got!r}")

    src = "const r = /a b/g;"
    check("slash lines returned unchanged (regex safety)",
          tighten(src) == src, f"got {tighten(src)!r}")

    got = tighten("return true;")
    check("word-word space survives", got == "return true;", f"got {got!r}")

    got = tighten("a + +b")
    check("unary-plus guard: '+ +' never becomes '++'",
          got == "a+ +b", f"got {got!r}")

    src = "s = 'it\\'s  ok';"
    got = tighten(src)
    check("escaped quote does not end the string; inner spacing kept",
          "'it\\'s  ok'" in got, f"got {got!r}")

    # A few extra edges the rules imply.
    got = tighten('const m = "a  b" + "c  d";')
    check("double-quoted strings survive verbatim",
          got == 'const m="a  b"+"c  d";', f"got {got!r}")
    got = tighten("if (a && b) { return x ? y : z; }")
    check("punct-adjacent runs drop around &&/?/:/braces",
          got == "if(a&&b){return x?y:z;}", f"got {got!r}")
    got = tighten("  ")
    check("all-space line collapses to empty", got == "", f"got {got!r}")
    src = "// not reached: minifier drops these before tightening"
    check("comment line (contains /) untouched", tighten(src) == src)

    # ---- 2. Idempotence over the real payload -------------------------------
    payload_lines = r._WIDGET_JS_TEMPLATE_MIN.split("\n")
    non_idem = [l for l in payload_lines if tighten(tighten(l)) != tighten(l)]
    check("tighten is idempotent over every emitted widget JS line",
          not non_idem, f"{len(non_idem)} unstable lines, first: "
          f"{non_idem[0]!r}" if non_idem else "")
    # The emitted payload IS already tightened (wired into _minify_js).
    not_fixed = [l for l in payload_lines if tighten(l) != l]
    check("emitted payload is already a fixed point of tighten",
          not not_fixed, f"first: {not_fixed[0]!r}" if not_fixed else "")

    # ---- 3. Integration: rendered page keeps critical wire literals ---------
    view = {
        "header": "tighten probe", "source_skill": "commitment-triage",
        "sections": [{"title": "S", "items": [{
            "n": "commitment_seq_1", "display_n": 1, "name": "Probe row",
            "context_tag": "41 days old",
            "actions": ["resolved", "push to [date]", "skip"]}]}],
    }
    page_html = r.render_chat_output_widget(view, wrapper="fragment")
    for literal in ("apply choices: ", "sendPrompt unavailable",
                    "cr-widget bind failed:"):
        check(f"rendered page keeps {literal!r} intact", literal in page_html)
    r.validate_rendered_widget(page_html)  # raises on failure

    if failures:
        print(f"\njs minify tighten FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"js minify intra-line tightening: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
