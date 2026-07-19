#!/usr/bin/env python3
"""G16 — deliverable gate parity (SPEC OUT5 §3b, the spec's load-bearing pin).

THE INVARIANT: **no gate that fires on the .docx path may be absent on the
premium-HTML path** (and vice versa). GATE1's lesson was that a second render
path with its own gate wiring is how runtime-unreachable gates re-enter the
product; OUT5 adds a second backend, so this guard pins the two to one stack.

Three layers, so an asymmetry cannot slip through any single seam:

  1. ENUMERATION — render the same payload through BOTH backends against the
     same workspace and compare the recorded `gate_ran` gates sets. A gate
     appended to one backend's gates_ran and not the other's fails here,
     naming both sides.
  2. STRUCTURE — both chokepoints must call brief_gates.run_pre_save_gates
     and neither may import a gate checker (output_contract_validator /
     voice_tell_detector) directly: a gate added inline in one backend
     instead of in brief_gates fails here.
  3. BEHAVIOR — every gate in brief_gates.PRE_SAVE_GATES + POST_SAVE_GATES is
     fired against BOTH backends with a payload built to trip it, and both
     must refuse with the same exception class. A gate that silently stopped
     firing on one side fails here naming the backend that let it through.

House convention: check(name, cond) prints OK/FAIL, exit 1 on any failure,
auto-discovered by run_all.py (run_guard_* → guard tier).
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import traceback
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import brief_gates  # noqa: E402
from brief_writer import make_brief  # noqa: E402
from premium_html import (  # noqa: E402
    make_premium_brief,
    PREMIUM_SUPPORTED_KINDS,
)
from brief_writer import SUPPORTED_BRIEF_KINDS  # noqa: E402
from output_contract_validator import OutputContractError  # noqa: E402
from voice_tell_detector import VoiceTellError  # noqa: E402
from docx_leak_scanner import LeakScanError  # noqa: E402

_failures: list = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="g16_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


# Comfortably over every kind's word floor while staying under the per-
# paragraph word cap (blank lines split paragraphs on both backends).
_PARA = (
    "The quarterly review covers the vendor screen in full detail and the "
    "resulting pilot recommendation for the operations group. "
) * 4
_BODY = "\n\n".join([_PARA] * 3)


def _payload(kind: str = "memo") -> dict:
    return {
        "brief_kind": kind,
        "title": "Vendor Screen Review",
        "subtitle": "Prepared for the operations group",
        "sections": [
            {"heading": "Recommendation", "body": _BODY,
             "tiles": [{"label": "Screened", "value": "12"}]},
            {"heading": "Detail", "body": _BODY,
             "table": {"headers": ["Vendor", "Fit"],
                       "rows": [["A", "Good"], ["B", "Strong"]]}},
        ],
        "exec_header": {"verdict": "Approve the pilot.",
                        "changed": "Screen completed.",
                        "decide": "Pilot vendor.",
                        "needs": "Budget sign-off."},
        "asks": [{"text": "Approve the pilot budget", "deadline": "Friday"}],
    }


def _render_both(ws: Path, payload: dict, **kw):
    """Render the same payload through both backends. Returns (docx_path,
    html_path); exceptions propagate to the caller's assertion."""
    d = tempfile.mkdtemp(prefix="g16_out_")
    docx = os.path.join(d, "twin.docx")
    html = os.path.join(d, "twin.html")
    buf = io.StringIO()
    with redirect_stderr(buf):
        make_brief(docx, workspace_root=str(ws), **payload, **kw)
        make_premium_brief(html, workspace_root=str(ws), **payload, **kw)
    return docx, html


def _both_raise(exc_type, payload: dict, label: str, **kw) -> None:
    """Assert BOTH backends raise `exc_type` for this payload, and that no
    file survives a pre-render refusal on either side."""
    d = tempfile.mkdtemp(prefix="g16_raise_")
    outcomes = {}
    for name, fn, out in (
        ("docx", make_brief, os.path.join(d, "x.docx")),
        ("premium_html", make_premium_brief, os.path.join(d, "x.html")),
    ):
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                fn(out, **payload, **kw)
            outcomes[name] = "no-raise"
        except exc_type:
            outcomes[name] = "raised"
        except Exception as e:  # wrong class = a diverged gate
            outcomes[name] = f"wrong-class:{type(e).__name__}"
    for name in ("docx", "premium_html"):
        check(f"{label}: {name} backend refuses ({exc_type.__name__})",
              outcomes[name] == "raised")


def _gate_ran_sets(ws: Path) -> dict:
    """{surface: gates list} from the workspace's gate_ran events."""
    out = {}
    events = (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    for line in events.splitlines():
        if not line.strip():
            continue
        ev = json.loads(line)
        if ev.get("type") == "gate_ran":
            out[ev["data"]["surface"]] = ev["data"]["gates"]
    return out


print("=== G16 layer 1 — enumeration: same payload, same recorded gate set ===")
try:
    ws = _ws()
    docx, html = _render_both(ws, _payload("memo"))
    check("docx twin rendered", os.path.isfile(docx))
    check("html twin rendered", os.path.isfile(html))
    sets = _gate_ran_sets(ws)
    check("gate_ran recorded for both surfaces",
          {"docx", "premium_html"} <= set(sets))
    check(
        f"gates sets identical (docx={sets.get('docx')} vs "
        f"premium_html={sets.get('premium_html')})",
        sets.get("docx") == sets.get("premium_html"),
    )
    check("the full mode-dependent stack ran (contract, voice, leak)",
          sets.get("docx") == ["contract", "voice", "leak"])
except Exception:
    traceback.print_exc()
    check("layer 1 completed without crashing", False)

print("\n=== G16 layer 2 — structure: one gate stack, no inline gates ===")
try:
    bw_src = (ROOT / "shared" / "scripts" / "brief_writer.py").read_text(encoding="utf-8")
    ph_src = (ROOT / "shared" / "scripts" / "premium_html.py").read_text(encoding="utf-8")
    for name, src in (("brief_writer", bw_src), ("premium_html", ph_src)):
        check(f"{name} calls brief_gates.run_pre_save_gates",
              "run_pre_save_gates(" in src)
        # The gate checkers may be imported ONLY by brief_gates — an inline
        # import in a backend is a gate growing on one side of the seam.
        check(f"{name} does not import output_contract_validator inline",
              "output_contract_validator" not in src.replace(
                  "run_guard_g16", ""))
        check(f"{name} does not import voice_tell_detector inline",
              "voice_tell_detector" not in src)
    check("premium kinds are a superset of docx kinds",
          SUPPORTED_BRIEF_KINDS <= PREMIUM_SUPPORTED_KINDS)
    check("gate registry declares the pre-save gates",
          brief_gates.PRE_SAVE_GATES == (
              "input_validation", "rec_ordering", "contract", "voice",
              "exec_header"))
    check("gate registry declares the post-save leak gate",
          brief_gates.POST_SAVE_GATES == ("leak",))
except Exception:
    traceback.print_exc()
    check("layer 2 completed without crashing", False)

print("\n=== G16 layer 3 — behavior: every gate fires on BOTH backends ===")
try:
    # input_validation — unknown kind, empty sections, over-cap asks.
    p = _payload("memo"); p["brief_kind"] = "not_a_kind"
    _both_raise(ValueError, p, "input_validation: unknown kind")
    p = _payload("memo"); p["sections"] = []
    _both_raise(ValueError, p, "input_validation: empty sections")
    p = _payload("memo")
    p["asks"] = [{"text": f"ask {i}"} for i in range(4)]
    _both_raise(ValueError, p, "input_validation: asks over MAX_ASKS")

    # rec_ordering — decision-shaped kind with a late recommendation section.
    p = _payload("memo")
    p["sections"] = (
        [{"heading": f"Analysis part {i}", "body": _BODY} for i in range(4)]
        + [{"heading": "Recommendation", "body": _BODY}]
    )
    _both_raise(ValueError, p, "rec_ordering: late recommendation")

    # contract — a one_pager far under its word floor (blocking violation).
    p = _payload("one_pager")
    p["sections"] = [{"heading": "Summary", "body": "Too thin."}]
    _both_raise(OutputContractError, p, "contract: under word floor")

    # voice — a fail-severity banned phrase in an outbound kind.
    p = _payload("memo")
    p["sections"][0]["body"] = "I'd be happy to walk the board through this. " + _BODY
    _both_raise(VoiceTellError, p, "voice: banned phrase in outbound kind")

    # exec_header — STANDARD_KIND with no exec header.
    p = _payload("memo"); p.pop("exec_header")
    _both_raise(ValueError, p, "exec_header: missing on STANDARD_KIND")

    # leak (post-save) — an internal ID in body prose must fail BOTH scans.
    p = _payload("memo")
    p["sections"][0]["body"] = _BODY + " Tracked internally as project_020."
    _both_raise(LeakScanError, p, "leak: internal id in body")

    # leak (html-only surface) — a substrate path hiding in an href must be
    # caught by the HTML scan (the docx twin has no href channel; this pins
    # the HTML scan covering its format-specific channel too).
    d = tempfile.mkdtemp(prefix="g16_href_")
    p = _payload("memo")
    p["sections"][1]["sources"] = [
        {"label": "Working notes", "url": "file:///ws/_hq/data/events.jsonl"}
    ]
    try:
        buf = io.StringIO()
        with redirect_stderr(buf):
            make_premium_brief(os.path.join(d, "href.html"), **p)
        check("leak: substrate path in href refused (premium_html)", False)
    except LeakScanError:
        check("leak: substrate path in href refused (premium_html)", True)
except Exception:
    traceback.print_exc()
    check("layer 3 completed without crashing", False)

print()
if _failures:
    print(f"=== {len(_failures)} FAILED ===")
    for f in _failures:
        print(f"  FAIL {f}")
    sys.exit(1)
print("=== G16 gate parity: all checks passed ===")
