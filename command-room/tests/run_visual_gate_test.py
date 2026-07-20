#!/usr/bin/env python3
"""SPEC OUT2 §3 — visual gate (render-then-critique) tests.

The load-bearing assertion is the NEGATIVE one: with neither renderer
available (CI has neither — that IS the test), `render_preview` returns None
gracefully and never raises, leaving caller behavior byte-identical to
pre-OUT2. The ladder is monkeypatched empty so the result does not depend on
what the host machine happens to have installed.

The REAL-renderer path (Word COM on Windows / soffice) is deliberately
opt-in via CR_VISUAL_GATE_LADDER_TEST=1 (skip-marked otherwise): exercising
Word COM inside the battery would make the battery's runtime and determinism
depend on the host's Office install (and a Word modal dialog could hang the
whole run). On a Windows dev machine, set the env var to exercise it.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import visual_gate  # noqa: E402
import event_payload_check as epc  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def _make_fixture_docx(tmp: str) -> str:
    """A tiny real .docx via the canonical brief_writer path."""
    from brief_writer import make_brief
    p = os.path.join(tmp, "fixture.docx")
    make_brief(p, brief_kind="memo", title="Visual gate fixture", subtitle="s",
               exec_header={"verdict": "Fixture renders."},
               sections=[{"heading": "Body", "body": "One paragraph."}],
               contract="off", voice_gate="off")
    return p


def test_no_renderer_returns_none():
    """CI-shaped case: ladder empty → None, no raise, no workspace writes."""
    with tempfile.TemporaryDirectory() as tmp:
        docx = _make_fixture_docx(tmp)
        original = visual_gate._DOCX_TO_PDF_LADDER
        try:
            visual_gate._DOCX_TO_PDF_LADDER = ()
            out = visual_gate.render_preview(docx)
        finally:
            visual_gate._DOCX_TO_PDF_LADDER = original
        check("empty ladder returns None (gate skipped, never raises)", out is None)


def test_rung_failures_fall_through_to_none():
    """Every rung erroring/returning None → None, exceptions swallowed."""
    with tempfile.TemporaryDirectory() as tmp:
        docx = _make_fixture_docx(tmp)

        def _boom(path, out_dir):
            raise RuntimeError("renderer exploded")

        def _quiet_none(path, out_dir):
            return None

        original = visual_gate._DOCX_TO_PDF_LADDER
        try:
            visual_gate._DOCX_TO_PDF_LADDER = (_boom, _quiet_none)
            out = visual_gate.render_preview(docx)
        finally:
            visual_gate._DOCX_TO_PDF_LADDER = original
        check("raising rung is swallowed, ladder falls through to None", out is None)


def test_bad_inputs_never_raise():
    for bad in (None, "", "C:/definitely/not/a/file_xyz.docx", 12345, {"path": "x"}):
        try:
            out = visual_gate.render_preview(bad)  # type: ignore[arg-type]
            ok = out is None
        except Exception as e:  # pragma: no cover — the bug this test pins
            ok = False
            print("   raised:", type(e).__name__, e)
        check(f"bad input {bad!r} -> None without raising", ok)


def test_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        docx = _make_fixture_docx(tmp)
        prior = os.environ.get("CR_VISUAL_GATE")
        try:
            os.environ["CR_VISUAL_GATE"] = "off"
            out = visual_gate.render_preview(docx)
        finally:
            if prior is None:
                os.environ.pop("CR_VISUAL_GATE", None)
            else:
                os.environ["CR_VISUAL_GATE"] = prior
        check("CR_VISUAL_GATE=off forces the skipped path", out is None)


def test_checklist_is_the_seven_items():
    # 6 OUT2 items + the SPEC OUT3 chart item. Extended, never reordered —
    # the first six stay positionally identical to the OUT2 pins.
    check("checklist has exactly the 7 contract items", len(visual_gate.CHECKLIST) == 7)
    check("checklist item 7 is the OUT3 chart item",
          visual_gate.CHECKLIST[6] == "chart unreadable / overplotted")
    check("the OUT2 six are unchanged and in order",
          visual_gate.CHECKLIST[:6] == (
              "orphaned heading at a page break",
              "empty or placeholder tile",
              "table overflow / wrap damage",
              "cramped spacing",
              "header/footer intact",
              "brand palette applied",
          ))


def test_log_visual_gate_event_shape():
    """The audit event lands, carries {doc, rendered, findings, fixed}, and
    passes the EVT1 payload schema with zero violations."""
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "ws"
        (ws / "_hq" / "data").mkdir(parents=True)
        ok = visual_gate.log_visual_gate(
            str(ws), doc="_hq/meetings/CallPrep_2026-07-10.docx", rendered=True,
            findings=["empty or placeholder tile"], fixed=True,
            source_skill="call-prep",
        )
        check("log_visual_gate returns True on success", ok)
        ep = ws / "_hq" / "data" / "events.jsonl"
        check("events.jsonl written", ep.is_file())
        evs = [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()]
        vg = [e for e in evs if e.get("type") == "visual_gate"]
        check("exactly one visual_gate event appended", len(vg) == 1)
        if vg:
            d = vg[0]["data"]
            check("payload carries doc/rendered/findings/fixed",
                  d.get("doc") and d.get("rendered") is True
                  and d.get("findings") == ["empty or placeholder tile"]
                  and d.get("fixed") is True)
            v = epc.check_payload(vg[0])
            check("payload passes event_payload_check (zero violations)", not v, str(v))

        # Skipped-path shape: rendered=False + skipped_reason.
        ok2 = visual_gate.log_visual_gate(
            str(ws), doc="_hq/meetings/Memo_2026-07-10.docx", rendered=False,
            skipped_reason="no renderer on this machine", source_skill="memo-writer",
        )
        check("skipped-path event also lands", ok2)
        evs = [json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines() if l.strip()]
        skipped = [e for e in evs if e.get("type") == "visual_gate"
                   and e["data"].get("rendered") is False]
        check("skipped event carries skipped_reason + empty findings",
              len(skipped) == 1
              and skipped[0]["data"].get("skipped_reason") == "no renderer on this machine"
              and skipped[0]["data"].get("findings") == [])
        if skipped:
            v = epc.check_payload(skipped[0])
            check("skipped payload passes event_payload_check", not v, str(v))


def test_log_visual_gate_never_raises():
    """A broken workspace root (a FILE where the dir should be) → False, no raise."""
    with tempfile.TemporaryDirectory() as tmp:
        blocker = Path(tmp) / "notadir"
        blocker.write_text("i am a file", encoding="utf-8")
        try:
            ok = visual_gate.log_visual_gate(
                str(blocker / "nested"), doc="x.docx", rendered=False,
                skipped_reason="test",
            )
            check("broken workspace root -> False, never raises", ok is False)
        except Exception as e:  # pragma: no cover
            check("broken workspace root -> False, never raises", False, repr(e))


def test_visual_gate_in_events_schema_enum():
    schema = json.loads((ROOT / "shared" / "data-schemas" / "events.schema.json")
                        .read_text(encoding="utf-8"))
    enum = schema["properties"]["type"]["enum"]
    check("visual_gate registered in events.schema.json enum", "visual_gate" in enum)


def test_real_ladder_optin():
    """Opt-in (CR_VISUAL_GATE_LADDER_TEST=1): exercise whatever renderer the
    host actually has. Skip-marked by default — see module docstring."""
    if os.environ.get("CR_VISUAL_GATE_LADDER_TEST") != "1":
        print("SKIP real-renderer ladder (set CR_VISUAL_GATE_LADDER_TEST=1 on a Windows dev machine to exercise Word COM / soffice)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        docx = _make_fixture_docx(tmp)
        out = visual_gate.render_preview(docx)
        if out is None:
            print("SKIP real-renderer ladder (no renderer found on this machine — None path already covered above)")
            return
        check("real ladder returns PNG paths that exist",
              isinstance(out, list) and out and all(os.path.isfile(p) for p in out))
        check("real ladder caps at 2 pages", len(out) <= 2)
        ws_root = str(ROOT)
        check("previews land in temp, never the workspace",
              all(not os.path.abspath(p).startswith(os.path.abspath(ws_root)) for p in out))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_no_renderer_returns_none()
    test_rung_failures_fall_through_to_none()
    test_bad_inputs_never_raise()
    test_kill_switch()
    test_checklist_is_the_seven_items()
    test_log_visual_gate_event_shape()
    test_log_visual_gate_never_raises()
    test_visual_gate_in_events_schema_enum()
    test_real_ladder_optin()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL visual_gate tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
