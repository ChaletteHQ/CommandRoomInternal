#!/usr/bin/env python3
"""
Tests for `shared/scripts/brief_writer.py` (v2.14.32+).

Covers the contract `brief_writer` must satisfy:

  - make_brief() writes a non-empty .docx to the requested path
  - Eyebrow label is "CALL PREP" for call_prep, "MEETING BRIEF" for past_meeting
  - Title appears as a 22pt bold paragraph in dark navy
  - Section headings appear as 13pt bold paragraphs in dark navy
  - Body paragraphs render in 11pt near-black with 1.25 line spacing
  - Bullets use the "List Bullet" style
  - Footer is hard-coded to "Command Room" (centered)
  - Margins are 0.9" top/bottom, 1.0" left/right
  - JSON-via-stdin entry point (`make_brief_from_json`) round-trips
  - Bad inputs raise ValueError (kind, missing fields)

Forwardable-clean structural enforcement (per CONTRACT.md Rule 15):
  - Footer never accepts provenance metadata — it's a fixed positional argument
  - The pre-v2.14.32 `Source: ... | Fired: ... | Inputs: ... | TTL: ...`
    pattern cannot leak through brief_writer.
"""
import json
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared", "scripts"))

from brief_writer import make_brief, make_brief_from_json
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH


results = {"pass": 0, "fail": 0, "failures": []}


def check(name, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  PASS  {name}")
    else:
        results["fail"] += 1
        results["failures"].append(f"{name} ({detail})" if detail else name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


def _sample_call_prep(path):
    return make_brief(
        path,
        brief_kind="call_prep",
        title="Sam Sample — Q2 deck review",
        subtitle="Friday, May 9, 2026 · 9:00 AM PT · Category Company",
        sections=[
            {"heading": "Lead with", "body": "Open warm.\n\nPivot to substance fast."},
            {"heading": "What to demo", "bullets": ["Connect Gmail.", "Surface a real signal."]},
            {"heading": "Suggested outcome", "body": "End with an installed Cowork."},
        ],
    )


def _sample_past_meeting(path):
    return make_brief(
        path,
        brief_kind="past_meeting",
        title="Sam Sample — UX review (continuation)",
        subtitle="Wednesday, Apr 29, 2026 · 9:05 PM PT · 70 min · Command Room",
        sections=[
            {"heading": "Attendees", "bullets": ["Matthew Sample", "Sam Sample"]},
            {"heading": "Summary", "bullets": ["Locked the 5-promise framing.", "Sam ran Inbox end-to-end."]},
            {"heading": "Decisions", "bullets": ["Promise #3 wording: 'nothing important slips'."]},
        ],
    )


# ============================================================================
# Test 1 — basic file output
# ============================================================================
print("\n=== make_brief: basic output ===")

with tempfile.TemporaryDirectory() as tmp:
    cp = os.path.join(tmp, "cp.docx")
    pm = os.path.join(tmp, "pm.docx")
    _sample_call_prep(cp)
    _sample_past_meeting(pm)
    check("call_prep file written", os.path.isfile(cp))
    check("past_meeting file written", os.path.isfile(pm))
    check("call_prep file is non-empty", os.path.getsize(cp) > 5000)
    check("past_meeting file is non-empty", os.path.getsize(pm) > 5000)

    # ============================================================================
    # Test 2 — eyebrow label correctness
    # ============================================================================
    print("\n=== eyebrow labels ===")
    doc_cp = Document(cp)
    doc_pm = Document(pm)
    eyebrow_cp = doc_cp.paragraphs[0].text
    eyebrow_pm = doc_pm.paragraphs[0].text
    check("call_prep eyebrow = 'CALL PREP'", eyebrow_cp == "CALL PREP", f"got {eyebrow_cp!r}")
    check("past_meeting eyebrow = 'MEETING BRIEF'", eyebrow_pm == "MEETING BRIEF", f"got {eyebrow_pm!r}")

    # ============================================================================
    # Test 3 — title typography (22pt bold, dark navy)
    # ============================================================================
    print("\n=== title typography ===")
    title_para = doc_cp.paragraphs[1]
    title_run = title_para.runs[0]
    check("title text matches", title_para.text == "Sam Sample — Q2 deck review")
    check("title is 22pt", title_run.font.size == Pt(22), f"got {title_run.font.size}")
    check("title is bold", title_run.font.bold is True)
    check(
        "title color is dark navy (#0F2A3F)",
        str(title_run.font.color.rgb) == "0F2A3F",
        f"got {title_run.font.color.rgb}",
    )

    # ============================================================================
    # Test 4 — subtitle typography (11pt muted grey)
    # ============================================================================
    print("\n=== subtitle typography ===")
    sub_run = doc_cp.paragraphs[2].runs[0]
    check("subtitle is 11pt", sub_run.font.size == Pt(11))
    check(
        "subtitle color is muted grey (#6B6B6B)",
        str(sub_run.font.color.rgb) == "6B6B6B",
    )

    # ============================================================================
    # Test 5 — section heading typography (13pt bold dark navy)
    # ============================================================================
    print("\n=== section heading typography ===")
    # Find the first section heading paragraph (after eyebrow, title, subtitle, rule)
    heading_paras = [
        p for p in doc_cp.paragraphs
        if p.runs and p.runs[0].font.size == Pt(13) and p.runs[0].font.bold
    ]
    check("at least one section heading present", len(heading_paras) >= 3)
    if heading_paras:
        h = heading_paras[0]
        check("first section heading text = 'Lead with'", h.text == "Lead with")
        check(
            "section heading color is dark navy",
            str(h.runs[0].font.color.rgb) == "0F2A3F",
        )

    # ============================================================================
    # Test 6 — bullets use List Bullet style
    # ============================================================================
    print("\n=== bullets ===")
    bullet_paras = [p for p in doc_cp.paragraphs if p.style.name == "List Bullet"]
    check("at least 2 List Bullet paragraphs", len(bullet_paras) >= 2)
    if bullet_paras:
        check(
            "first bullet text matches",
            bullet_paras[0].text == "Connect Gmail.",
            f"got {bullet_paras[0].text!r}",
        )

    # ============================================================================
    # Test 7 — footer is hard-coded "Command Room" centered
    # ============================================================================
    print("\n=== footer enforcement ===")
    footer_para = doc_cp.sections[0].footer.paragraphs[0]
    check("footer text = 'Command Room'", footer_para.text == "Command Room")
    check("footer is centered", footer_para.alignment == WD_ALIGN_PARAGRAPH.CENTER)
    footer_run = footer_para.runs[0]
    check("footer color is muted grey", str(footer_run.font.color.rgb) == "6B6B6B")
    check("footer is 9pt", footer_run.font.size == Pt(9))

    # ============================================================================
    # Test 8 — margins (0.9" top/bot, 1.0" left/right)
    # ============================================================================
    print("\n=== margins ===")
    sec = doc_cp.sections[0]
    # 1 inch = 914400 EMU; 0.9 inch = 822960 EMU
    check("top margin is 0.9 inch", sec.top_margin == 822960, f"got {sec.top_margin}")
    check("bottom margin is 0.9 inch", sec.bottom_margin == 822960)
    check("left margin is 1.0 inch", sec.left_margin == 914400)
    check("right margin is 1.0 inch", sec.right_margin == 914400)

    # ============================================================================
    # Test 9 — forwardable-clean structural enforcement
    # ============================================================================
    print("\n=== forwardable-clean enforcement ===")
    # Verify no provenance leak strings appear anywhere in the document body
    full_text = "\n".join(p.text for p in doc_cp.paragraphs)
    check("no 'Source:' leak in body", "Source:" not in full_text)
    check("no 'Fired:' leak in body", "Fired:" not in full_text)
    check("no 'TTL:' leak in body", "TTL:" not in full_text)
    check("no 'Inputs:' leak in body", "Inputs:" not in full_text)
    check("no 'meeting_id:' leak in body", "meeting_id:" not in full_text)

    # ============================================================================
    # Test 10 — JSON-via-stdin entry point
    # ============================================================================
    print("\n=== make_brief_from_json ===")
    json_path = os.path.join(tmp, "from_json.docx")
    payload = json.dumps({
        "output_path": json_path,
        "brief_kind": "call_prep",
        "title": "JSON Test Title",
        "subtitle": "Today",
        "sections": [{"heading": "Solo section", "body": "Hello."}],
    })
    returned = make_brief_from_json(payload)
    check("JSON entry point returns the path", returned == json_path)
    check("JSON-produced file exists", os.path.isfile(json_path))
    doc_json = Document(json_path)
    check("JSON-produced file has the right title", doc_json.paragraphs[1].text == "JSON Test Title")


# ============================================================================
# Test 11 — bad-input rejection
# ============================================================================
print("\n=== input validation ===")

with tempfile.TemporaryDirectory() as tmp:
    bad = os.path.join(tmp, "bad.docx")

    def _expect_value_error(label, fn):
        try:
            fn()
            check(label, False, "expected ValueError, got success")
        except ValueError:
            check(label, True)
        except Exception as e:
            check(label, False, f"expected ValueError, got {type(e).__name__}: {e}")

    _expect_value_error(
        "rejects unknown brief_kind",
        lambda: make_brief(bad, brief_kind="totally_made_up_kind", title="t", subtitle="s",
                           sections=[{"heading": "h", "body": "b"}]),
    )
    _expect_value_error(
        "rejects missing title",
        lambda: make_brief(bad, brief_kind="call_prep", title="", subtitle="s",
                           sections=[{"heading": "h", "body": "b"}]),
    )
    _expect_value_error(
        "rejects empty sections",
        lambda: make_brief(bad, brief_kind="call_prep", title="t", subtitle="s", sections=[]),
    )
    _expect_value_error(
        "rejects section without heading",
        lambda: make_brief(bad, brief_kind="call_prep", title="t", subtitle="s",
                           sections=[{"body": "b"}]),
    )
    _expect_value_error(
        "rejects section without body or bullets",
        lambda: make_brief(bad, brief_kind="call_prep", title="t", subtitle="s",
                           sections=[{"heading": "h"}]),
    )


# ============================================================================
# Summary
# ============================================================================
print(f"\n=== {results['pass']} passed, {results['fail']} failed ===")
if results["fail"]:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
