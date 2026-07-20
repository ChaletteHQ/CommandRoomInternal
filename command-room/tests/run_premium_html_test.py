#!/usr/bin/env python3
"""SPEC OUT5 — the premium HTML brief backend + format selection.

Covers (house convention: check(name, cond) prints OK/FAIL, exit 1 on any
failure, auto-discovered by run_all.py):

  - resolve_format_for_kind: unconfigured workspace = docx for every launched
    kind EXCEPT research (its shipped default is HTML) and docx for every
    unlaunched kind regardless of profile/override (the golden posture);
    default_format=premium_html flips launched kinds only; format_by_kind
    beats default_format; unknown values keep the default; the trigger-level
    override beats the profile; research pins to docx via format_by_kind.
  - Structural golden (acceptance #3): a research-shape render through the
    shared template carries the research template's regions — eyebrow, title,
    badges + source/confidence chips, bottom line, key-findings cites,
    who-&-what-matters buyer card, recent-signals strip, sources list.
  - Byte-stable determinism: same input renders identical bytes twice.
  - Brand + org override flow into the HTML (accent hex, footer wordmark,
    custom heading font in the serif stack); default brand = default accent.
  - Self-contained fence (SPEC OUT5 §4): no @import, no external
    stylesheet/script/font URL anywhere in the artifact.
  - No unfilled {{TOKENS}} survive a render (the hand-fill class is dead).
  - Output-profile knobs on the HTML backend: density narrative loosens the
    body leading var; visual_bias prose_first flips tiles/body order.
  - Leak scan with real-data shapes (real-data fixture gotcha): substrate
    paths / internal ids / banned marketing words in body prose raise
    LeakScanError and the scan reads the SAVED FILE (catches href leaks too —
    scan_html_for_leaks direct fixtures).
  - make_premium_brief_from_json round-trip (the CLI shape research fires).
  - premium_html never imports python-docx (an HTML render must not trigger
    the docx self-install).
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from output_profile import (  # noqa: E402
    PREMIUM_LAUNCH_KINDS,
    resolve_format_for_kind,
    validate_output_profile,
)
from premium_html import (  # noqa: E402
    make_premium_brief,
    make_premium_brief_from_json,
    PREMIUM_SUPPORTED_KINDS,
)
from docx_leak_scanner import scan_html_for_leaks, LeakScanError  # noqa: E402

_failures: list = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="out5_ws_"))
    (ws / "_hq" / "data" / "skill_config").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def _write_profile(ws: Path, cfg: dict) -> None:
    path = ws / "_hq" / "data" / "skill_config" / "output_profile.json"
    path.write_text(json.dumps(
        {"schema_version": 1, "skill_name": "output_profile", "config": cfg}
    ), encoding="utf-8")


_PARA = (
    "The vendor screen covered twelve candidates against the operations "
    "requirements set in the June review cycle. "
) * 4
_BODY = "\n\n".join([_PARA] * 3)


def _research_payload(out: str) -> dict:
    return {
        "output_path": out,
        "brief_kind": "research",
        "title": "Sample Holdings",
        "subtitle": "Researched ahead of the Thursday call",
        "sections": [
            {"heading": "Key findings", "bullets": [
                {"text": "Headcount verified at 312", "url": "https://example.com/a"},
                {"text": "Likely expanding support", "url": "https://example.com/b",
                 "low_confidence": True},
                "A plain uncited finding line",
            ]},
            {"heading": "Who & what matters", "people": [
                {"name": "Bo Stone", "role": "COO", "buyer": True,
                 "note": "Owns the tooling decision."},
                {"name": "Skyler Sample", "role": "VP Finance"},
            ]},
            {"heading": "Recent signals", "events": [
                {"when": "Mar 2026", "text": "Growth round closed",
                 "url": "https://example.com/c"},
                {"when": "Jun 2026", "text": "Ops hiring wave"},
            ]},
            {"heading": "Sources", "sources": [
                {"label": "Company newsroom", "url": "https://example.com/a"},
                {"label": "Funding filing", "url": "https://example.com/c"},
            ]},
        ],
        "exec_header": {"verdict": "Bo Stone owns the decision, not the CEO."},
        "badges": {"source": "enriched", "confidence": "high"},
        "source_summary": "3 sources via enrichment",
    }


def _render(out: str, ws=None, kind: str = "memo", **kw) -> str:
    buf = io.StringIO()
    with redirect_stderr(buf):
        make_premium_brief(
            out, brief_kind=kind, title="Vendor Screen Review",
            subtitle="Prepared for the operations group",
            sections=[
                {"heading": "Recommendation", "body": _BODY,
                 "tiles": [{"label": "Screened", "value": "12"}]},
                {"heading": "Detail", "body": _BODY},
            ],
            exec_header={"verdict": "Approve the pilot.",
                         "changed": "Screen completed.",
                         "decide": "Pilot vendor.",
                         "needs": "Budget sign-off."},
            workspace_root=str(ws) if ws else None,
            **kw,
        )
    return Path(out).read_text(encoding="utf-8")


print("=== resolve_format_for_kind (SPEC OUT5 §3c) ===")
ws = _ws()  # unconfigured
check("launch set is the §3c four + OUT3B chart_on_demand",
      PREMIUM_LAUNCH_KINDS == {"board_pack", "one_pager", "value_receipt",
                              "research", "chart_on_demand"})
check("unconfigured: chart_on_demand -> premium_html (OUT3B base, D2)",
      resolve_format_for_kind("chart_on_demand", ws) == "premium_html")
check("unconfigured: board_pack -> docx (golden)",
      resolve_format_for_kind("board_pack", ws) == "docx")
check("unconfigured: one_pager -> docx (golden)",
      resolve_format_for_kind("one_pager", ws) == "docx")
check("unconfigured: research -> premium_html (its shipped default)",
      resolve_format_for_kind("research", ws) == "premium_html")
check("unconfigured, NO workspace at all: docx / research-html",
      resolve_format_for_kind("board_pack") == "docx"
      and resolve_format_for_kind("research") == "premium_html")
check("unlaunched kind -> docx always (memo)",
      resolve_format_for_kind("memo", ws) == "docx")

ws2 = _ws()
_write_profile(ws2, {"default_format": "premium_html"})
check("default_format=premium_html flips a launched kind",
      resolve_format_for_kind("board_pack", ws2) == "premium_html")
check("default_format=premium_html does NOT flip an unlaunched kind",
      resolve_format_for_kind("memo", ws2) == "docx")
check("default_format=docx (explicit) does NOT drag research to docx",
      resolve_format_for_kind("research", _ws()) == "premium_html")

ws3 = _ws()
_write_profile(ws3, {"default_format": "premium_html",
                     "format_by_kind": {"board_pack": "docx",
                                        "research": "docx",
                                        "memo": "premium_html",
                                        "one_pager": "bogus"}})
check("format_by_kind beats default_format (board_pack pinned docx)",
      resolve_format_for_kind("board_pack", ws3) == "docx")
check("format_by_kind pins research to docx (the one way to flip it)",
      resolve_format_for_kind("research", ws3) == "docx")
check("format_by_kind cannot launch an unlaunched kind (memo stays docx)",
      resolve_format_for_kind("memo", ws3) == "docx")
check("invalid format_by_kind value dropped at resolution (one_pager falls to default)",
      resolve_format_for_kind("one_pager", ws3) == "premium_html")
check("trigger override beats the profile (board_pack ask 'as HTML')",
      resolve_format_for_kind("board_pack", ws3, override="premium_html") == "premium_html")
check("trigger override 'as a doc' beats a premium profile",
      resolve_format_for_kind("one_pager", ws2, override="docx") == "docx")
check("override cannot cross the launch fence (memo 'as HTML' stays docx)",
      resolve_format_for_kind("memo", ws2, override="premium_html") == "docx")

check("validate: format_by_kind bad value flagged",
      any("format_by_kind" in p for p in
          validate_output_profile({"format_by_kind": {"board_pack": "pdf"}})))
check("validate: premium_html now a legal default_format",
      validate_output_profile({"default_format": "premium_html"}) == [])
check("validate: clean format_by_kind passes",
      validate_output_profile(
          {"format_by_kind": {"board_pack": "premium_html"}}) == [])

print("\n=== structural golden — research through the shared template ===")
d = tempfile.mkdtemp(prefix="out5_render_")
out = os.path.join(d, "Research_Brief_sample_2026-01-01.html")
buf = io.StringIO()
with redirect_stderr(buf):
    make_premium_brief_from_json(json.dumps(_research_payload(out)))
html = Path(out).read_text(encoding="utf-8")
for region, needle in [
    ("eyebrow (kind label)", '<p class="eyebrow">RESEARCH</p>'),
    ("title", '<h1 class="title">Sample Holdings</h1>'),
    ("source badge chip", '<span class="chip enriched">'),
    ("confidence chip", '<span class="chip high">'),
    ("bottom line (verdict lead)", '<p class="bottomline">'),
    ("cited finding anchor", '<a class="cite" href="https://example.com/a">[1]</a>'),
    ("low-confidence flag", '<span class="flag-low">'),
    ("buyer card", '<div class="person buyer">'),
    ("likely-buyer tag", '<span class="tag">Likely buyer</span>'),
    ("recent-signals strip", '<ul class="events">'),
    ("sources list", "<ol><li>"),
    ("source summary footer", "3 sources via enrichment"),
    ("saved-to-workspace foot line", "Saved to your workspace."),
]:
    check(f"region present: {region}", needle in html)
check("research renders verdict-only exec header (no CHANGED eyebrow)",
      'class="exec-lines"' not in html)
check("no unfilled template token survives", "{{" not in html)
check("byte-stable: same input renders identical bytes",
      html == Path(out).read_text(encoding="utf-8") and _render(
          os.path.join(d, "t1.html")) == _render(os.path.join(d, "t2.html")))

print("\n=== self-contained fence (SPEC OUT5 §4) ===")
head = html.split("</head>")[0]
check("no @import anywhere", "@import" not in html)
check("no external stylesheet/font link", "<link" not in html)
check("no script tags at all", "<script" not in html.lower())
check("no external URL in the head (fonts/CDN)",
      not re.search(r"https?://", head))

print("\n=== brand + profile flow into the HTML ===")
wsb = _ws()
(wsb / "_hq" / "data" / "entities.json").write_text(json.dumps({
    "workspace": {"brand": {"palette": {"accent": "8A5A2B"},
                            "footer_line": "Sample Firm",
                            "fonts": {"heading": "Palatino Linotype"}}},
    "entities": {"orgs": [{"id": "org_x", "canonical_name": "Org X",
                           "brand": {"palette": {"accent": "112233"}}}]},
}), encoding="utf-8")
h = _render(os.path.join(d, "brand.html"), ws=wsb)
check("workspace brand accent lands in the CSS", "#8A5A2B" in h)
check("brand footer_line is the wordmark", ">Sample Firm<" in h.replace("\n", ""))
check("monogram derives from the wordmark", ">S</text>" in h)
check("custom heading font leads the serif stack", "'Palatino Linotype'" in h)
h_org = _render(os.path.join(d, "brand_org.html"), ws=wsb, org_id="org_x")
check("org brand overrides workspace accent", "#112233" in h_org)
h_def = _render(os.path.join(d, "brand_def.html"))
check("default brand accent on a bare render", "#2E7D6B" in h_def)
check("default wordmark is Command Room", ">Command Room<" in h_def.replace("\n", ""))

wsp = _ws()
_write_profile(wsp, {"density": "narrative", "visual_bias": "prose_first"})
h_prof = _render(os.path.join(d, "prof.html"), ws=wsp)
check("density narrative loosens body leading", "--body-leading:1.85" in h_prof)
h_tight = _render(os.path.join(d, "tight.html"))
check("default density keeps the tight leading", "--body-leading:1.65" in h_tight)
_TILE_MARKUP = '<div class="cr-counter-grid">'  # markup, not the CSS rule
check("visual_bias prose_first puts body before tiles",
      0 < h_prof.find("<p>The vendor screen") < h_prof.find(_TILE_MARKUP))
check("default tiles_first puts tiles before body",
      0 < h_tight.find(_TILE_MARKUP) < h_tight.find("<p>The vendor screen"))

print("\n=== leak scan through the HTML path (real-data shapes) ===")
for label, mutate in [
    ("internal id in body", lambda p: p["sections"][0]["bullets"].append(
        "Tracked as project_020 internally")),
    ("substrate path in body", lambda p: p["sections"][0]["bullets"].append(
        "State lives in events.jsonl for now")),
    ("marketing word in body", lambda p: p["sections"][0]["bullets"].append(
        "We can leverage the new tooling")),
    ("substrate path in a source href", lambda p: p["sections"][3]["sources"].append(
        {"label": "notes", "url": "file:///x/_hq/data/events.jsonl"})),
]:
    payload = _research_payload(os.path.join(d, "leak.html"))
    mutate(payload)
    buf = io.StringIO()
    try:
        with redirect_stderr(buf):
            make_premium_brief_from_json(json.dumps(payload))
        check(f"leak refused: {label}", False)
    except LeakScanError:
        check(f"leak refused: {label}", True)

clean = os.path.join(d, "clean_scan.html")
Path(clean).write_text(
    "<html><body><p>All fine here.</p></body></html>", encoding="utf-8")
check("scan_html_for_leaks passes a clean file",
      scan_html_for_leaks(clean) == [])
empty = os.path.join(d, "empty.html")
Path(empty).write_text("", encoding="utf-8")
try:
    scan_html_for_leaks(empty)
    check("empty file is LOUD, never false-clean (Bug #54 posture)", False)
except LeakScanError:
    check("empty file is LOUD, never false-clean (Bug #54 posture)", True)

print("\n=== gate_ran surface + kind registry ===")
wsg = _ws()
_render(os.path.join(d, "gate.html"), ws=wsg)
events = [json.loads(l) for l in
          (wsg / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines()
          if l.strip()]
gate = [e for e in events if e.get("type") == "gate_ran"]
check("gate_ran event lands with surface=premium_html",
      len(gate) == 1 and gate[0]["data"]["surface"] == "premium_html")
check("premium kinds = docx kinds + research",
      PREMIUM_SUPPORTED_KINDS == frozenset(
          list(__import__("brief_gates").EYEBROW_BY_KIND) + ["research"]))

print("\n=== stdlib-only import (no python-docx pull) ===")
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, r'%s'); import premium_html; "
     "print('docx' in sys.modules)" % str(ROOT / "shared" / "scripts")],
    capture_output=True, text=True, timeout=120,
)
check("importing premium_html does not import python-docx",
      probe.returncode == 0 and probe.stdout.strip() == "False")

print()
if _failures:
    print(f"=== {len(_failures)} FAILED ===")
    for f in _failures:
        print(f"  FAIL {f}")
    sys.exit(1)
print("ALL SPEC OUT5 premium-html checks PASSED")
