#!/usr/bin/env python3
"""Tests for SPEC FU1 — content-sweep coverage for saved .html deliverables.

Premium-HTML deliverables (OUT5 briefs, OUT7 scorecards, soon OUT4 infographics)
land as `.html`. The save-time gate is bypassable — the LLM can hand-roll or
later-edit a page — so GATE2's load-bearing move is "read what was actually
produced." Before FU1 the sweep walked `.docx` + `.md` only; a hand-rolled
`.html` was invisible to cleanup's weekly backstop, check-deliverables' on-demand
sweep, and the Stop-hook turn sweep. FU1 joins `.html`/`.htm` to the walk, scanned
by the same engine the docx path uses, through a registry dispatch seam.

Covers:
  - find_candidate_html picks up deliverable .html/.htm, skips infra html
    (dashboards, templates, .system/widgets, _archive, intel views) by dir AND
    name stem; find_candidate_deliverables merges docx + md + html newest-first
    under the shared 500 cap;
  - scan_html_for_violations returns the docx-result shape, catches leaks in
    visible text AND in href/src targets, ignores CSS/JS/comment tokens, and
    catches voice structural tells via _html_paragraph_text (NOT the collapsed
    _html_visible_text — proven by the em-dash-spread discriminator);
  - the M-2 fold: a personal-lane substrate fingerprint (rem_ id / data-personal)
    in a produced .html is FLAGGED surface-less (flag-only, never blocking);
  - failure posture: empty / unreadable / >5 MB html each come back error-flagged,
    never clean (Bug #54 loud-not-false-clean);
  - scan_path_for_violations dispatches .html through the html scanner (no
    raw-markup CSS false positive);
  - sweep_workspace / sweep_targets FLAG a hand-rolled .html while leaving the
    user's file byte-identical (read-only), writing only CR-owned telemetry;
  - html rows flow through summarize_for_user + the findings record (basenames
    only, no jargon).

Fixture rules (real-data / org-name / date-guard gotchas): synthetic workspace
mirroring real substrate shape; html shaped like a real premium render (inline
<style>, sections, hrefs); placeholder org "Acme Co" only; no hardcoded dates.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))


def _fresh_ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="fu1_html_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    (ws / "_hq" / "data" / "entities.json").write_text(
        '{"entities": {"threads": []}}', encoding="utf-8"
    )
    return ws


# A hand-rolled premium-HTML deliverable that escaped the save-time gate: an
# internal id in body prose, a substrate path hiding in an href (invisible on the
# page, live on click), and marketing vocab — shaped like a real inline-CSS render.
_DIRTY_HTML = (
    "<!doctype html><html><head><style>.card{color:#1a1714}"
    ".project_020{display:none}</style></head><body>"
    "<section><h1>Acme Co — update</h1>"
    "<p>We will leverage the new flow; see project_020 for the plan.</p>"
    '<p>Details: <a href="_hq/data/events.jsonl">source</a>.</p>'
    "<!-- reviewer note: project_020 events.jsonl --></section></body></html>"
)
# A dirty render whose violations are a marketing-vocab word + an internal id
# (NOT a substrate path) — used where the plain-English summary is asserted to
# be jargon-free: the summary surfaces the literal offending words, so a
# substrate-path leak would legitimately (and correctly) put "_hq/" / ".jsonl"
# in the note. This fixture keeps the jargon-leak assertion meaningful.
_DIRTY_HTML_VOICE = (
    "<!doctype html><html><body><section><h1>Acme Co — update</h1>"
    "<p>We will leverage the new flow; the project_020 rollout starts soon.</p>"
    "</section></body></html>"
)
# Clean render — nothing to flag.
_CLEAN_HTML = (
    "<!doctype html><html><body><section><h1>Acme Co</h1>"
    "<p>Tuesday works. I will send the signed copy Monday.</p>"
    "</section></body></html>"
)
# Infra html that legitimately carries the same tokens — must NEVER be flagged
# (it is not a deliverable): a dashboard render.
_INFRA_HTML = "<html><body><p>project_020 pipeline view; events.jsonl feed.</p></body></html>"


# ---------- candidate discovery ----------

def test_find_candidate_html_includes_deliverables_excludes_infra() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    # Deliverable-shaped html at the workspace root (the "lands anywhere" case)
    # + a client deliverables dir (.htm variant).
    (ws / "brief.html").write_text(_DIRTY_HTML, encoding="utf-8")
    (ws / "Acme Co" / "deliverables").mkdir(parents=True)
    (ws / "Acme Co" / "deliverables" / "scorecard.htm").write_text(
        _CLEAN_HTML, encoding="utf-8"
    )
    # Infra html — excluded by name stem (dashboard).
    (ws / "dashboard_home.html").write_text(_INFRA_HTML, encoding="utf-8")
    # Infra html — excluded by directory (templates / design-library /
    # email-templates / intel are all in the shared infra list).
    for d in ("templates", "design-library", "email-templates", "intel"):
        (ws / "_hq" / d).mkdir(parents=True)
        (ws / "_hq" / d / "x.html").write_text(_INFRA_HTML, encoding="utf-8")
    # Widget pages live under _hq/.system/widgets — pruned by _EXCLUDED_DIR_PARTS.
    (ws / "_hq" / ".system" / "widgets").mkdir(parents=True)
    (ws / "_hq" / ".system" / "widgets" / "w.html").write_text(
        _INFRA_HTML, encoding="utf-8"
    )
    # Archived html — pruned.
    (ws / "_archive").mkdir()
    (ws / "_archive" / "old.html").write_text(_DIRTY_HTML, encoding="utf-8")

    names = {p.name for p in ds.find_candidate_html(ws)}
    assert "brief.html" in names, names
    assert "scorecard.htm" in names, names
    assert "dashboard_home.html" not in names, names
    assert "x.html" not in names, names  # every infra dir variant excluded
    assert "w.html" not in names, names  # .system widgets pruned
    assert "old.html" not in names, names  # _archive pruned
    print("PASS test_find_candidate_html_includes_deliverables_excludes_infra")


def test_find_candidate_deliverables_merges_all_three_formats() -> None:
    import deliverable_sweep as ds
    import zipfile

    ws = _fresh_ws()
    (ws / "note.md").write_text("Tuesday works.\n", encoding="utf-8")
    (ws / "page.html").write_text(_CLEAN_HTML, encoding="utf-8")
    d = ws / "doc.docx"
    with zipfile.ZipFile(str(d), "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            "<w:p><w:r><w:t>Tuesday works.</w:t></w:r></w:p></w:body></w:document>",
        )
    found = {p.name for p in ds.find_candidate_deliverables(ws)}
    assert {"note.md", "page.html", "doc.docx"} <= found, found
    print("PASS test_find_candidate_deliverables_merges_all_three_formats")


def test_find_candidate_html_newest_first_and_cap() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    older = ws / "older.html"
    newer = ws / "newer.html"
    older.write_text(_CLEAN_HTML, encoding="utf-8")
    newer.write_text(_CLEAN_HTML, encoding="utf-8")
    # Force a stable ordering by mtime (no reliance on write timing).
    base = time.time()
    os.utime(older, (base - 100, base - 100))
    os.utime(newer, (base, base))
    ordered = [p.name for p in ds.find_candidate_html(ws)]
    assert ordered.index("newer.html") < ordered.index("older.html"), ordered
    # Merged cap still 500 across the three walks.
    assert len(ds.find_candidate_deliverables(ws, max_files=1)) == 1
    print("PASS test_find_candidate_html_newest_first_and_cap")


# ---------- html scanner ----------

def test_scan_html_catches_visible_and_href_ignores_css() -> None:
    from docx_leak_scanner import scan_html_for_violations

    p = Path(tempfile.mkdtemp()) / "brief.html"
    p.write_text(_DIRTY_HTML, encoding="utf-8")
    r = scan_html_for_violations(p)
    # Same shape as scan_docx_for_violations.
    for key in ("path", "leaks", "voice", "has_violation", "has_voice_warn"):
        assert key in r, (key, r)
    assert r["has_violation"] is True, r
    matches = {x["match"] for x in r["leaks"]}
    assert "project_020" in matches, matches           # body prose (visible)
    assert any("events.jsonl" in m for m in matches), matches  # href target
    assert "leverage" in matches, matches              # marketing vocab
    print("PASS test_scan_html_catches_visible_and_href_ignores_css")


def test_scan_html_css_js_comment_tokens_produce_zero_findings() -> None:
    from docx_leak_scanner import scan_html_for_violations

    # The SAME forbidden tokens, but ONLY inside <style>, <script>, and a comment
    # — none are reader-visible, so the scan must be silent (no raw-markup noise).
    html = (
        "<html><head><style>.project_020{color:red}</style>"
        '<script>var s = "events.jsonl"; // Phase 3</script></head>'
        "<body><!-- project_020 events.jsonl Phase 3 -->"
        "<p>All good. Talk soon.</p></body></html>"
    )
    p = Path(tempfile.mkdtemp()) / "clean_but_tokens_in_css.html"
    p.write_text(html, encoding="utf-8")
    r = scan_html_for_violations(p)
    assert r["leaks"] == [], r["leaks"]
    assert r["has_violation"] is False, r
    print("PASS test_scan_html_css_js_comment_tokens_produce_zero_findings")


def test_scan_html_voice_uses_paragraph_text() -> None:
    from docx_leak_scanner import scan_html_for_violations

    # (a) A tri-colon construction inside ONE <p> block (with an inner inline
    #     tag) is caught — proves the voice scan reads reconstructed,
    #     tag-stripped paragraph text.
    tri = (
        "<body><section><p>Status: <b>green</b>. Owner: Sam. Risk: low.</p>"
        "<p>Thanks.</p></section></body>"
    )
    p = Path(tempfile.mkdtemp()) / "tri.html"
    p.write_text(tri, encoding="utf-8")
    rules = {f["rule"] for f in scan_html_for_violations(p)["voice"]["findings"]}
    assert "structural_tri_colon" in rules, rules

    # (b) Discriminator: three em-dashes, ONE per <p> across three blocks. With
    #     _html_paragraph_text (block-split) each paragraph has a single dash →
    #     NO pileup. Collapsed _html_visible_text would count all three in "one
    #     paragraph" and false-fire — so absence here proves paragraph_text is
    #     wired for the voice scan, not visible_text.
    spread = (
        "<body><p>We ship now — today.</p>"
        "<p>We win — soon.</p><p>We grow — fast.</p></body>"
    )
    p2 = Path(tempfile.mkdtemp()) / "spread.html"
    p2.write_text(spread, encoding="utf-8")
    rules2 = {f["rule"] for f in scan_html_for_violations(p2)["voice"]["findings"]}
    assert "structural_em_dash_pileup" not in rules2, rules2
    print("PASS test_scan_html_voice_uses_paragraph_text")


def test_scan_html_table_cells_split_like_docx_paragraphs() -> None:
    # FU1 second-eyes FIX 1: in a .docx every table cell is its own <w:p>
    # paragraph, so a label/value KPI row must not collapse into one line and
    # false-fire the tri-colon rule. </td>/</th> are paragraph boundaries.
    from docx_leak_scanner import scan_html_for_violations

    p = Path(tempfile.mkdtemp()) / "kpi.html"
    p.write_text(
        "<html><body><table>"
        "<tr><td>Status: green</td><td>Owner: Sam</td><td>Risk: low</td></tr>"
        "</table><p>All fine.</p></body></html>",
        encoding="utf-8",
    )
    r = scan_html_for_violations(p)
    rules = {f["rule"] for f in r["voice"]["findings"]}
    assert "structural_tri_colon" not in rules, rules
    assert r["has_voice_warn"] is False, r

    # FU1 second-eyes FIX 3 (FB-16 parity, landed on main @ fab31a4): table
    # regions are DATA, not voice prose — the docx path strips <w:tbl> before
    # the voice scan, so the html path strips <table>…</table> too. Proven by
    # an em-dash pile-up confined to ONE cell: without the strip that cell is
    # its own paragraph and would fire; with it, silence. The LEAK scan stays
    # table-inclusive — a forbidden token in a cell is still caught.
    p2 = p.parent / "kpi_dashes.html"
    p2.write_text(
        "<html><body><table><tr>"
        "<td>Q1 — up, Q2 — flat, Q3 — down</td>"
        "<td>see project_020</td>"
        "</tr></table><p>All fine.</p></body></html>",
        encoding="utf-8",
    )
    r2 = scan_html_for_violations(p2)
    rules2 = {f["rule"] for f in r2["voice"]["findings"]}
    assert "structural_em_dash_pileup" not in rules2, rules2
    assert "project_020" in {x["match"] for x in r2["leaks"]}, r2["leaks"]
    print("PASS test_scan_html_table_cells_split_like_docx_paragraphs")


def test_scan_html_token_split_across_inline_tags_is_caught() -> None:
    # FU1 second-eyes FIX 2: inline tags render with no spacing — the reader
    # sees `project_<b>020</b>` as `project_020` and a syntax-highlighted
    # `<span>events</span><span>.jsonl</span>` as `events.jsonl`. The scan must
    # be render-faithful (parity with the docx path, which catches a token
    # split across adjacent <w:r> runs). Block tags must still separate words.
    from docx_leak_scanner import scan_html_for_violations

    tmp = Path(tempfile.mkdtemp())
    p = tmp / "split.html"
    p.write_text(
        "<html><body><p>see project_<b>020</b> and "
        '<span class="k">events</span><span class="p">.jsonl</span> here</p>'
        "</body></html>",
        encoding="utf-8",
    )
    r = scan_html_for_violations(p)
    matches = {x["match"] for x in r["leaks"]}
    assert "project_020" in matches, matches
    assert any("events.jsonl" in m for m in matches), matches

    # Block tags still separate: two adjacent <p> halves of a token do NOT
    # join into a false hit (the reader sees them on separate lines).
    p2 = tmp / "blocksplit.html"
    p2.write_text(
        "<html><body><p>project_</p><p>020</p></body></html>", encoding="utf-8"
    )
    r2 = scan_html_for_violations(p2)
    assert not {x["match"] for x in r2["leaks"]}, r2["leaks"]
    print("PASS test_scan_html_token_split_across_inline_tags_is_caught")


def test_scan_html_flags_personal_fingerprint_flag_only() -> None:
    # SPEC FU1 M-2: a personal-lane substrate fingerprint in a produced .html is
    # FLAGGED surface-less (flag-only). These are machinery, not personal
    # content, so a real deliverable never legitimately carries them.
    from docx_leak_scanner import scan_html_for_violations

    html = (
        "<html><body><p>Acme Co plan looks good.</p>"
        '<div data-personal="true">reminder rem_ABCDEF1234 pending</div>'
        "</body></html>"
    )
    p = Path(tempfile.mkdtemp()) / "leaky.html"
    p.write_text(html, encoding="utf-8")
    r = scan_html_for_violations(p)
    names = {x["name"] for x in r["leaks"]}
    assert "personal_reminder_id" in names, names
    assert r["has_violation"] is True, r
    print("PASS test_scan_html_flags_personal_fingerprint_flag_only")


def test_scan_html_failure_posture_loud() -> None:
    from docx_leak_scanner import scan_html_for_violations, _MAX_HTML_BYTES

    tmp = Path(tempfile.mkdtemp())
    # Missing file → error, never a false-clean violation.
    r_missing = scan_html_for_violations(tmp / "nope.html")
    assert "error" in r_missing and r_missing["has_violation"] is False, r_missing

    # Empty file → error (can't verify).
    empty = tmp / "empty.html"
    empty.write_text("", encoding="utf-8")
    r_empty = scan_html_for_violations(empty)
    assert "error" in r_empty and r_empty["has_violation"] is False, r_empty

    # Unreadable → error. A directory with an .html suffix trips read_text
    # (portable across OSes where chmod on a file is unreliable).
    unreadable = tmp / "adir.html"
    unreadable.mkdir()
    r_unread = scan_html_for_violations(unreadable)
    assert "error" in r_unread and r_unread["has_violation"] is False, r_unread

    # Oversize (> 5 MB) → error, not silently skipped.
    big = tmp / "huge.html"
    big.write_text("<p>" + ("a" * (_MAX_HTML_BYTES + 10)) + "</p>", encoding="utf-8")
    r_big = scan_html_for_violations(big)
    assert "error" in r_big and r_big["has_violation"] is False, r_big
    print("PASS test_scan_html_failure_posture_loud")


# ---------- dispatch ----------

def test_scan_path_for_violations_dispatches_html() -> None:
    import deliverable_sweep as ds

    p = Path(tempfile.mkdtemp()) / "x.html"
    p.write_text(_DIRTY_HTML, encoding="utf-8")
    r = ds.scan_path_for_violations(p)
    assert r["has_violation"] is True, r
    matches = {x["match"] for x in r["leaks"]}
    # Routed through the HTML scanner: visible-text findings only, no raw-markup
    # CSS token (e.g. a color hex or a class name) leaked as a finding.
    assert "project_020" in matches, matches
    assert not any("#1a1714" in m or "color" in m.lower() for m in matches), matches
    print("PASS test_scan_path_for_violations_dispatches_html")


# ---------- workspace sweep + read-only ----------

def test_sweep_workspace_flags_html_not_infra_and_readonly() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    dirty = ws / "outbound_brief.html"
    dirty.write_text(_DIRTY_HTML_VOICE, encoding="utf-8")
    infra = ws / "dashboard_x.html"
    infra.write_text(_INFRA_HTML, encoding="utf-8")
    clean = ws / "fine.html"
    clean.write_text(_CLEAN_HTML, encoding="utf-8")

    before_bytes = dirty.read_bytes()
    before_mtime = dirty.stat().st_mtime
    res = ds.sweep_workspace(ws, emit=True, source="on_demand_sweep")

    flagged = {Path(f["path"]).name for f in res["flagged"]}
    assert "outbound_brief.html" in flagged, flagged
    assert "dashboard_x.html" not in flagged, flagged  # infra never scanned
    assert "fine.html" not in flagged, flagged
    assert res["violation_count"] >= 1, res

    # READ-ONLY: the user's file is byte-identical and its mtime is untouched.
    assert dirty.read_bytes() == before_bytes, "sweep mutated the .html file!"
    assert dirty.stat().st_mtime == before_mtime, "sweep touched the .html mtime!"

    # Only CR-owned telemetry was written (findings record + events), never a
    # user file.
    recs = list((ws / "_hq" / ".system" / "gate2_findings").glob("*.json"))
    assert recs, "no findings record written"
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    # Findings store basenames only — never the full path or content.
    for f in rec["flagged"]:
        assert "/" not in f["doc"] and "\\" not in f["doc"], f["doc"]

    # Plain-English summary names the file + offending word, no jargon.
    summary = ds.summarize_for_user(res)
    assert summary and "outbound_brief.html" in summary, summary
    for forbidden in ("_hq/", "gate_ran", "voice_tell", ".jsonl", "structural_"):
        assert forbidden not in summary, f"leaked {forbidden!r}: {summary}"
    print("PASS test_sweep_workspace_flags_html_not_infra_and_readonly")


def test_sweep_targets_html_shape_and_summary() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    p = ws / "one_off.html"
    p.write_text(_DIRTY_HTML, encoding="utf-8")
    res = ds.sweep_targets([str(p)], workspace_root=str(ws), emit=True)
    assert res["scanned"] == 1, res
    assert res["violation_count"] == 1, res
    assert ds.summarize_for_user(res) is not None

    events = [
        json.loads(l)
        for l in (ws / "_hq" / "data" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    assert any(e.get("type") == "gate_ran" for e in events), events

    # A clean html target → no flags, summary None.
    c = ws / "clean_one.html"
    c.write_text(_CLEAN_HTML, encoding="utf-8")
    res2 = ds.sweep_targets([str(c)], workspace_root=str(ws), emit=False)
    assert res2["violation_count"] == 0 and ds.summarize_for_user(res2) is None
    print("PASS test_sweep_targets_html_shape_and_summary")


def main() -> int:
    test_find_candidate_html_includes_deliverables_excludes_infra()
    test_find_candidate_deliverables_merges_all_three_formats()
    test_find_candidate_html_newest_first_and_cap()
    test_scan_html_catches_visible_and_href_ignores_css()
    test_scan_html_css_js_comment_tokens_produce_zero_findings()
    test_scan_html_voice_uses_paragraph_text()
    test_scan_html_table_cells_split_like_docx_paragraphs()
    test_scan_html_token_split_across_inline_tags_is_caught()
    test_scan_html_flags_personal_fingerprint_flag_only()
    test_scan_html_failure_posture_loud()
    test_scan_path_for_violations_dispatches_html()
    test_sweep_workspace_flags_html_not_infra_and_readonly()
    test_sweep_targets_html_shape_and_summary()
    print("\nALL deliverable_sweep_html tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
