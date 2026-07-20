#!/usr/bin/env python3
"""Infographic composer test (SPEC OUT4).

Covers §5:
  1. Layout set: LAYOUTS matches the shipped template files (closed set), each
     renders, each is deterministic (same input + brand -> same bytes).
  2. Per-layout shape validation + refusals (refusal over empty frames), the
     honest no-fit result, unknown-layout refusal, hierarchy depth cap, quadrant
     range, checklist status vocabulary.
  3. Gate parity extended to the builder: the leak scan (forbidden token in
     prose) and the voice-tell gate (banned phrase in prose) both REFUSE the
     render, exactly as the OUT5 rail's gates do; voice_gate="off" bypasses.
  4. Brand/org resolution: explicit brand dict, workspace + per-org override —
     the resolved accent reaches the page (premium-rail parity).
  5. Real-data-shape fixtures for ranked_list + stat_spotlight (the two wired
     first) + a leak-poisoned fixture through the builder.
  6. value-receipt wiring: build_value_receipt_infographic renders quarter with
     numbers VERBATIM + forwardability lock (no names), returns None on every
     honest no-fit; the visual_first profile knob + renders_infographic_first.
  7. Self-contained fence: the rendered page pulls no CDN / external font /
     script (OUT5 posture).
  8. Coupling pins: infographic reuses premium_html._template_vars + the shell
     path; the template files carry NO literal hex (stray-palette parity).

House convention: check(name, cond) prints OK/FAIL; missing deps fail LOUDLY
(exit 2), never SKIP-but-PASS.
"""
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import infographic
    from infographic import (
        build_infographic, SUPPORTED_LAYOUTS, LAYOUTS, InfographicDataError,
    )
    from docx_leak_scanner import LeakScanError
    from voice_tell_detector import VoiceTellError
    from brand import get_brand, DEFAULT_BRAND
    import premium_html
    import output_profile
    from output_profile import renders_infographic_first
    from value_receipt import build_value_receipt_infographic
except ImportError as exc:  # pragma: no cover
    print(f"ERROR: cannot import an OUT4 dependency ({exc}) — this suite "
          f"requires the full render stack (same fail-loud class as G11 "
          f"SKIP-but-PASS).")
    sys.exit(2)

TEMPLATE_DIR = ROOT / "shared" / "templates" / "infographic"
_HEX_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")

_failures = []


def check(name, cond, extra=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        _failures.append(name)


# Synthetic, placeholder-only fixtures (org-name leak gotcha). No YYYY-MM-DD
# literals (G14) — timeline uses month labels, which are not clock-compared.
FIXTURES = {
    "ranked_list": {
        "tiles": [{"label": "Screened", "value": "12"}],
        "rows": [
            {"label": "Acme Co", "score": "92", "note": "warm intro pending"},
            {"label": "Northstar Partners", "score": "81"},
            {"label": "Sample Org 3", "score": "70"},
        ],
    },
    "sequence": {"steps": [
        {"title": "Kickoff", "detail": "scope the pilot"},
        {"title": "Build", "detail": "ship the first version"},
        {"title": "Review"},
    ]},
    "comparison_2col": {
        "a_label": "Our terms", "b_label": "Standard",
        "rows": [
            {"label": "Payment", "a": "Net 30", "b": "Net 60"},
            {"label": "Renewal", "a": "Annual", "b": "Monthly"},
        ],
    },
    "hierarchy": {"root": {"label": "Acme Co", "children": [
        {"label": "Operations", "children": [{"label": "Support"}]},
        {"label": "Growth"},
    ]}},
    "timeline_spread": {"events": [
        {"date": "Jan", "label": "First call"},
        {"date": "Mar", "label": "Pilot", "detail": "signed"},
        {"date": "May", "label": "Expansion", "current": True},
    ]},
    "stat_spotlight": {
        "hero": {"value": "~48", "label": "Hours returned"},
        "support": [
            {"label": "Actions handled", "value": "63"},
            {"label": "Threads advanced", "value": "9"},
        ],
    },
    "quadrant": {
        "x_axis": {"low": "Low effort", "high": "High effort"},
        "y_axis": {"low": "Low impact", "high": "High impact"},
        "items": [
            {"label": "Auto-triage", "x": 0.2, "y": 0.8},
            {"label": "Full rebuild", "x": 0.9, "y": 0.6},
        ],
    },
    "checklist_scorecard": {"rows": [
        {"label": "Data backups", "status": "ok", "note": "nightly"},
        {"label": "Access review", "status": "warn"},
        {"label": "Single admin", "status": "bad", "note": "one point of failure"},
    ]},
}

# Marker content that must survive into each rendered page (the golden anchor).
GOLDEN_MARKERS = {
    "ranked_list": ["Acme Co", "92", "ig-ranked"],
    "sequence": ["Kickoff", "ig-seq"],
    "comparison_2col": ["Our terms", "Net 30", "ig-cmp"],
    "hierarchy": ["Operations", "Support", "ig-hier"],
    "timeline_spread": ["First call", "cr-timeline"],
    "stat_spotlight": ["~48", "Actions handled", "ig-spot"],
    "quadrant": ["Auto-triage", "ig-quad", "left:20"],
    "checklist_scorecard": ["Data backups", "PASS", "FAIL", "ig-check"],
}


def main():
    # ---------------------------------------------------------------- 1
    files = {p.stem for p in TEMPLATE_DIR.glob("*.html")}
    check("LAYOUTS matches the shipped template files (closed set)",
          set(SUPPORTED_LAYOUTS) == files,
          extra=f"registry={sorted(SUPPORTED_LAYOUTS)} files={sorted(files)}")
    check("exactly 8 launch layouts", len(SUPPORTED_LAYOUTS) == 8,
          extra=str(len(SUPPORTED_LAYOUTS)))

    for layout in SUPPORTED_LAYOUTS:
        html1 = build_infographic(layout, FIXTURES[layout],
                                  title=f"{layout} demo", subtitle="placeholder data")
        html2 = build_infographic(layout, FIXTURES[layout],
                                  title=f"{layout} demo", subtitle="placeholder data")
        check(f"{layout}: renders + deterministic",
              html1 == html2 and html1.strip().startswith("<!doctype html>"))
        check(f"{layout}: no unfilled slots", "{{" not in html1)
        check(f"{layout}: golden markers present",
              all(m in html1 for m in GOLDEN_MARKERS[layout]),
              extra=f"missing {[m for m in GOLDEN_MARKERS[layout] if m not in html1]}")

    # ---------------------------------------------------------------- 2
    check("unknown layout refuses",
          _raises(InfographicDataError, "pie_chart", {}))
    check("non-dict content refuses",
          _raises(InfographicDataError, "ranked_list", ["not", "a", "dict"]))
    # Per-layout refusals (too few / empty after drop-empty).
    refusals = {
        "ranked_list": {"rows": [{"label": "only one"}]},
        "sequence": {"steps": [{"title": "one"}]},
        "comparison_2col": {"a_label": "A", "b_label": "B",
                            "rows": [{"label": "one", "a": "x", "b": "y"}]},
        "hierarchy": {"root": {"label": ""}},
        "timeline_spread": {"events": [{"date": "Jan", "label": "only"}]},
        "stat_spotlight": {"hero": {"value": "1", "label": "h"}, "support": []},
        "quadrant": {"x_axis": {"low": "a", "high": "b"},
                     "y_axis": {"low": "c", "high": "d"},
                     "items": [{"label": "one", "x": 0.5, "y": 0.5}]},
        "checklist_scorecard": {"rows": [{"label": "one", "status": "ok"}]},
    }
    for layout, bad in refusals.items():
        check(f"{layout}: refuses an empty-frame shape (honest no-fit)",
              _raises(InfographicDataError, layout, bad))
    # Hierarchy depth cap: level 3 OK, level 4 refused.
    depth3 = {"root": {"label": "L1", "children": [
        {"label": "L2", "children": [{"label": "L3"}]}]}}
    check("hierarchy depth 3 renders", isinstance(
        build_infographic("hierarchy", depth3, title="t", subtitle="s"), str))
    depth4 = {"root": {"label": "L1", "children": [
        {"label": "L2", "children": [
            {"label": "L3", "children": [{"label": "L4"}]}]}]}}
    check("hierarchy depth 4 refused", _raises(InfographicDataError, "hierarchy", depth4))
    # Quadrant out-of-range + checklist bad status vocabulary.
    check("quadrant out-of-range refused", _raises(
        InfographicDataError, "quadrant",
        {"x_axis": {"low": "a", "high": "b"}, "y_axis": {"low": "c", "high": "d"},
         "items": [{"label": "p", "x": 1.5, "y": 0.5}, {"label": "q", "x": 0.2, "y": 0.2}]}))
    check("checklist unknown status refused", _raises(
        InfographicDataError, "checklist_scorecard",
        {"rows": [{"label": "a", "status": "maybe"}, {"label": "b", "status": "ok"}]}))
    # Review F-2 regression: the RENDERED vocabulary (pass/warn/fail) must
    # round-trip as input — "fail"/"failed" refused before the FLAG_TINT_KEYS
    # fix while the refusal message itself advertised them.
    rendered_vocab = build_infographic(
        "checklist_scorecard",
        {"rows": [{"label": "a", "status": "pass"},
                  {"label": "b", "status": "warn"},
                  {"label": "c", "status": "fail"},
                  {"label": "d", "status": "failed"}]},
        title="t", subtitle="s")
    check("checklist accepts its own rendered vocabulary (pass/warn/fail/failed)",
          isinstance(rendered_vocab, str)
          and rendered_vocab.count('class="ig-check-row fail"') == 2
          and 'class="ig-check-row pass"' in rendered_vocab
          and 'class="ig-check-row warn"' in rendered_vocab)
    # Review F-3 regression: template header comments document slots as literal
    # {{TOKEN}} text — before the _load_fragment comment-strip, slot fill
    # injected a FULL SECOND COPY of the content into an HTML comment on every
    # page (invisible to the reader, masked by the 'no unfilled slots' check).
    check("content renders exactly once (no comment-duplicated copy)",
          rendered_vocab.count('<ul class="ig-check">') == 1
          and "Infographic layout:" not in rendered_vocab)

    # ---------------------------------------------------------------- 3
    # Gate parity: leak scan (forbidden token in a note) refuses.
    check("leak scan refuses a forbidden token in prose", _raises(
        LeakScanError, "ranked_list",
        {"rows": [{"label": "Acme Co", "note": "tracked as project_020"},
                  {"label": "Northstar Partners"}]}))
    # Voice-tell gate refuses a fail-severity banned phrase.
    voice_bad = {"steps": [
        {"title": "Kickoff", "detail": "I'd be happy to walk you through this."},
        {"title": "Build"}]}
    check("voice gate refuses a banned phrase in prose",
          _raises(VoiceTellError, "sequence", voice_bad))
    ok_off = build_infographic("sequence", voice_bad, title="t", subtitle="s",
                               voice_gate="off")
    check("voice_gate='off' bypasses the voice gate", isinstance(ok_off, str))

    # ---------------------------------------------------------------- 4
    # Brand/org resolution — the resolved accent reaches the page.
    default_accent = "#" + DEFAULT_BRAND["palette"]["accent"]
    page_def = build_infographic("stat_spotlight", FIXTURES["stat_spotlight"],
                                 title="t", subtitle="s")
    check("default render carries the default brand accent",
          default_accent in page_def)
    custom = get_brand({"workspace": {"brand": {"palette": {"accent": "8A5A2B"}}}})
    page_custom = build_infographic("stat_spotlight", FIXTURES["stat_spotlight"],
                                    title="t", subtitle="s", brand=custom)
    check("explicit brand dict themes the page (accent swapped)",
          "#8A5A2B" in page_custom and default_accent not in page_custom)
    # Per-org override via workspace_root + org_id.
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        (ws / "_hq" / "data").mkdir(parents=True)
        import json
        (ws / "_hq" / "data" / "entities.json").write_text(json.dumps({
            "entities": {"orgs": [
                {"id": "org_x", "canonical_name": "Placeholder Org",
                 "brand": {"palette": {"accent": "3355AA"}}}]}}), encoding="utf-8")
        page_org = build_infographic("ranked_list", FIXTURES["ranked_list"],
                                     title="t", subtitle="s",
                                     workspace_root=str(ws), org_id="org_x")
        check("per-org brand override reaches the page", "#3355AA" in page_org)

    # ---------------------------------------------------------------- 5
    # Real-data-shape fixtures (the two wired first) + poisoned fixture.
    rl = build_infographic("ranked_list", FIXTURES["ranked_list"],
                           title="Dormant top 3", subtitle="last quarter")
    check("ranked_list real-shape renders the tile band + rows",
          "cr-counter-grid" in rl and "Northstar Partners" in rl)
    ss = build_infographic("stat_spotlight", FIXTURES["stat_spotlight"],
                           title="Value Receipt", subtitle="the quarter")
    check("stat_spotlight real-shape renders hero + support band",
          "~48" in ss and "cr-counter-grid" in ss)
    check("poisoned fixture (substrate path in a note) refused through builder",
          _raises(LeakScanError, "checklist_scorecard",
                  {"rows": [{"label": "Notes", "status": "warn",
                             "note": "see _hq/data/events.jsonl"},
                            {"label": "ok", "status": "ok"}]}))

    # ---------------------------------------------------------------- 6
    # value-receipt wiring.
    receipt_q = {
        "rollup": "quarter", "window": "the previous quarter",
        "metrics": {"commitments_captured": 40, "meetings_processed": 12,
                    "briefs_delivered": 8, "drafts_produced": 15,
                    "decisions_logged": 6, "dormant_resurfaced": 3},
        "hours_estimate": 48.5,
    }
    ig = build_value_receipt_infographic(receipt_q, label="the previous quarter")
    check("value-receipt quarter -> stat_spotlight HTML", isinstance(ig, str))
    check("value-receipt hero number is VERBATIM from the receipt",
          ig and "48.5" in ig)
    # Forwardability lock: no person/org names, only counts + labels.
    check("value-receipt infographic carries no names (forwardability lock)",
          ig and "Acme" not in ig and "Northstar" not in ig)
    check("value-receipt monthly roll-up does NOT render an infographic",
          build_value_receipt_infographic({**receipt_q, "rollup": "month"}) is None)
    check("value-receipt with no hours returns None (honest no-fit)",
          build_value_receipt_infographic({
              **receipt_q, "hours_estimate": 0,
              "metrics": {k: 0 for k in receipt_q["metrics"]}}) is None)

    # visual_first profile knob + resolver.
    check("renders_infographic_first is False for an unconfigured workspace",
          renders_infographic_first("value_receipt", None) is False)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        cfgdir = ws / "_hq" / "data" / "skill_config"
        cfgdir.mkdir(parents=True)
        import json
        (cfgdir / "output_profile.json").write_text(
            json.dumps({"config": {"visual_first": ["value_receipt", "", 5]}}),
            encoding="utf-8")
        prof = output_profile.get_output_profile(str(ws))
        check("visual_first resolves to clean kind-string list",
              prof["visual_first"] == ["value_receipt"])
        check("renders_infographic_first True once opted in",
              renders_infographic_first("value_receipt", str(ws)) is True)
    check("validate flags a non-list visual_first",
          output_profile.validate_output_profile({"visual_first": "value_receipt"}) != [])
    check("validate passes a clean visual_first",
          output_profile.validate_output_profile({"visual_first": ["value_receipt"]}) == [])

    # ---------------------------------------------------------------- 7
    # Self-contained fence (OUT5 posture): no CDN / external font / script.
    page = build_infographic("ranked_list", FIXTURES["ranked_list"],
                             title="t", subtitle="s")
    # Strip HTML comments first — the shell's fence comment literally spells out
    # "no cdn", which is documentation, not a resource pull.
    body = re.sub(r"<!--.*?-->", " ", page, flags=re.DOTALL).lower()
    check("no <script> in a rendered infographic", "<script" not in body)
    check("no external stylesheet <link>", "<link" not in body)
    check("no external font / CDN / remote asset pull",
          "fonts.googleapis" not in body and "cdn" not in body
          and "src=\"http" not in body and "href=\"http" not in body
          and "url(http" not in body and "@import" not in body)

    # ---------------------------------------------------------------- 8
    # Coupling pins + template hygiene.
    check("infographic reuses premium_html._template_vars (coupling pin — if "
          "renamed, update infographic.py)", hasattr(premium_html, "_template_vars"))
    check("premium shell path exists", premium_html.TEMPLATE_PATH.is_file())
    hexy = []
    for f in sorted(TEMPLATE_DIR.glob("*.html")) + [ROOT / "shared" / "scripts" / "infographic.py"]:
        if _HEX_RE.search(f.read_text(encoding="utf-8")):
            hexy.append(f.name)
    check("no literal hex in infographic templates or composer "
          "(stray-palette parity)", not hexy, extra=f"hex in {hexy}")

    print()
    if _failures:
        print(f"FAILURES ({len(_failures)}): {_failures}")
        return 1
    print(f"ALL infographic tests PASSED ({len(SUPPORTED_LAYOUTS)} layouts)")
    return 0


def _raises(exc_type, layout, content, **kw):
    try:
        build_infographic(layout, content, title="t", subtitle="s", **kw)
        return False
    except exc_type:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
