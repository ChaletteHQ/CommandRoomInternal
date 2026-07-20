#!/usr/bin/env python3
"""
Tests for voice_tell_detector (SPEC B2) + its brief_writer save-time gate.

House conventions: stdlib only, plain asserts, prints PASS per test, exits
0 on pass / 1 on failure (run_all.py classifies this as a unit suite).

Covers:
  - every banned phrase in VOICE_CALIBRATION.md's list triggers fail, with the
    correct line_no and the phrase in `match`
  - sync floor: detector fail-rule count >= markdown bullet count
  - clean text passes; structural tells warn (not fail)
  - skip_quoted + allow_phrases hooks
  - check_sections flattens bullets + table cells
  - brief_writer integration: memo fail-blocks PRE-save (no file written),
    call_prep is warn-only (file written), voice_gate="off" lets memo save
  - CLI exit codes
  - prose-guard: all 8 composer SKILL.mds reference the detector
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from voice_tell_detector import (  # noqa: E402
    FAIL_RULE_COUNT,
    VoiceTellError,
    check_sections,
    scan_text,
)

VOICE_CALIBRATION = ROOT / "shared" / "VOICE_CALIBRATION.md"
DETECTOR_PATH = SCRIPTS / "voice_tell_detector.py"

# The 8 composer skills whose Step 2 critique must invoke the detector.
COMPOSER_SKILLS = [
    "email-writer",
    "memo-writer",
    "one-pager-composer",
    "follow-up-ritual",
    "decision-memo-composer",
    "inbox-triage",
    "intro-broker",
    "board-pack-assembler",
]


def _banned_phrases_from_markdown() -> list[tuple[int, str]]:
    """Extract every quoted banned phrase from VOICE_CALIBRATION.md's
    'Universal banned-phrase list' section. Returns (md_line_no, phrase).

    Only quoted-string bullets (`- "..."`) count — the structural-tell bullets
    (`- Tri-colon ...`) are descriptions, not exact phrases, and are warn-only.
    """
    text = VOICE_CALIBRATION.read_text(encoding="utf-8")
    lines = text.split("\n")
    start = end = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## Universal banned-phrase list"):
            start = i
        elif start is not None and ln.strip().startswith("## ") and i > start:
            end = i
            break
    assert start is not None, "could not find banned-phrase list header"
    section = lines[start : end if end else len(lines)]
    out: list[tuple[int, str]] = []
    for offset, ln in enumerate(section):
        m = re.match(r'^- "([^"]+)"', ln.strip())
        if m:
            phrase = m.group(1).rstrip(" .…")
            out.append((start + offset + 1, phrase))
    return out


def test_sync_floor() -> None:
    bullets = _banned_phrases_from_markdown()
    assert len(bullets) >= 1, "no banned phrases parsed — parser drifted"
    assert FAIL_RULE_COUNT >= len(bullets), (
        f"detector encodes {FAIL_RULE_COUNT} fail rules but the markdown list "
        f"has {len(bullets)} phrase bullets — they drifted; update "
        f"voice_tell_detector.py to match VOICE_CALIBRATION.md"
    )
    print(f"PASS test_sync_floor ({FAIL_RULE_COUNT} rules >= {len(bullets)} bullets)")


def test_every_banned_phrase_fails() -> None:
    phrases = _banned_phrases_from_markdown()
    for _md_line, phrase in phrases:
        # Place the phrase on line 3 of a multi-line input.
        text = f"Opening line.\n\n{phrase}\nClosing line."
        result = scan_text(text, context="brief")
        assert result["verdict"] == "fail", f"{phrase!r} did not fail: {result}"
        hits = [f for f in result["findings"] if f["severity"] == "fail"]
        assert hits, f"{phrase!r} produced no fail finding"
        assert any(f["line_no"] == 3 for f in hits), (
            f"{phrase!r} fail finding had wrong line_no: {hits}"
        )
        norm_phrase = re.sub(r"\s+", " ", phrase.lower()).replace("’", "'")
        assert any(
            re.sub(r"\s+", " ", f["match"].lower()).replace("’", "'") in norm_phrase
            or norm_phrase in re.sub(r"\s+", " ", f["match"].lower()).replace("’", "'")
            for f in hits
        ), f"{phrase!r} not echoed in any match: {hits}"
    print(f"PASS test_every_banned_phrase_fails ({len(phrases)} phrases)")


def test_finds_you_well_interpolation() -> None:
    """#v3200-2 regression — the interpolated 'email'/'message'/'note' form of
    the opener must FAIL, not just the contiguous form. v3.20.0 shipped a draft
    with 'I hope this email finds you well' because the literal pattern missed
    the inserted word."""
    variants = [
        "I hope this email finds you well.",
        "I hope this message finds you well.",
        "Hope this note finds you well.",
        "Hope this finds you well.",  # the bare form must still fail
    ]
    for v in variants:
        result = scan_text(v, context="email")
        assert result["verdict"] == "fail", f"{v!r} should fail: {result}"
        assert any(
            f["rule"] == "filler_finds_well" for f in result["findings"]
        ), f"{v!r} did not trip filler_finds_well: {result}"
    # Must not over-match an unrelated sentence that happens to contain the words.
    clean = "I hope this lands. The numbers are well within range."
    assert scan_text(clean, context="email")["verdict"] != "fail", scan_text(clean)
    print(f"PASS test_finds_you_well_interpolation ({len(variants)} variants)")


def test_clean_text_passes() -> None:
    text = (
        "Following up on the Q2 deck. The three API concerns from the May 5 "
        "call are resolved; I sent the revised spec to Sam on May 18.\n\n"
        "Two inbound demos booked this week. I'll have the contract over Friday."
    )
    result = scan_text(text)
    assert result["verdict"] == "pass", result
    print("PASS test_clean_text_passes")


def test_thanks_for_reaching_out_fails() -> None:
    result = scan_text("Thanks for reaching out.")
    assert result["verdict"] == "fail", result
    assert any(f["rule"] == "opener_thanks_reaching" for f in result["findings"])
    print("PASS test_thanks_for_reaching_out_fails")


def test_dash_as_punctuation_ban() -> None:
    # FB-16: dashes-as-punctuation are a product-level FAIL in body prose (the
    # old policy only WARNED at >2 em-dashes/paragraph, so a single "— fast"
    # slipped the gate). One finding per occurrence.
    three = "We shipped — fast — and clean — this week."
    r3 = scan_text(three)
    assert r3["verdict"] == "fail", r3
    assert len([f for f in r3["findings"] if f["rule"] == "dash_as_punctuation"]) == 3, r3
    one = "We shipped — fast this week."
    assert scan_text(one)["verdict"] == "fail", scan_text(one)
    # en dash and spaced hyphen are dashes-as-punctuation too
    assert scan_text("Revenue rose 10 – 20 percent.")["verdict"] == "fail"
    assert scan_text("Revenue rose - a lot - this quarter.")["verdict"] == "fail"
    # hyphenated compounds are NOT punctuation — they stay clean
    assert scan_text("Let's set up a follow-up check-in.")["verdict"] == "pass", \
        scan_text("Let's set up a follow-up check-in.")
    print("PASS test_dash_as_punctuation_ban")


def test_dash_signoff_exempt() -> None:
    # The standalone "— Matthew" sign-off is a deliberate brand-voice element.
    body = "Got it, will send today.\n\n— Matthew"
    assert scan_text(body)["verdict"] == "pass", scan_text(body)
    # Second-eyes fix (2026-07-19): a sign-off hierarchy's LONGEST form is a
    # full first + last name — multi-token sign-offs are exempt too.
    for signoff in ("— Sam Sample", "— MD", "– Bo Sample"):
        r = scan_text(f"Got it.\n\n{signoff}")
        assert r["verdict"] == "pass", (signoff, r)
    # but a dash used as punctuation with prose before it still fails
    assert scan_text("Send it today — Matthew asked twice.")["verdict"] == "fail"
    # and a dash line leading a SENTENCE (4+ capitalized tokens) is not a
    # sign-off — the exemption caps at 3 name tokens
    assert scan_text("Got it.\n\n— Please Send It Now")["verdict"] == "fail"
    # mid-paragraph parenthetical dashes are punctuation, not a sign-off
    assert scan_text("The plan — like this — slipped.")["verdict"] == "fail"
    print("PASS test_dash_signoff_exempt")


def test_dash_ban_overridable() -> None:
    text = "We shipped — fast."
    # ban_dashes=False turns the product ban off for a client who keeps dashes
    assert scan_text(text, ban_dashes=False)["verdict"] == "pass", \
        scan_text(text, ban_dashes=False)
    # a demonstrably-used dashed phrase in the client's Voice Block feeds through
    assert scan_text(text, allow_phrases=["we shipped — fast"])["verdict"] == "pass"
    print("PASS test_dash_ban_overridable")


def test_tri_colon_warns() -> None:
    text = "Our priorities: ship fast: stay lean: win deals."
    result = scan_text(text)
    assert result["verdict"] == "warn", result
    assert any(f["rule"] == "structural_tri_colon" for f in result["findings"])
    print("PASS test_tri_colon_warns")


def test_hedging_stack_warns() -> None:
    text = "I think this might possibly work for the launch."
    result = scan_text(text)
    assert result["verdict"] == "warn", result
    assert any(f["rule"] == "structural_hedging_stack" for f in result["findings"])
    print("PASS test_hedging_stack_warns")


def test_skip_quoted() -> None:
    quoted = '> Let me know if that works\n"I\'d be happy to help," she said'
    assert scan_text(quoted, skip_quoted=True)["verdict"] == "pass", scan_text(
        quoted, skip_quoted=True
    )
    # Same lines unquoted must fail.
    unquoted = "Let me know if that works\nI'd be happy to help, she said"
    assert scan_text(unquoted, skip_quoted=True)["verdict"] == "fail"
    # skip_quoted=False sees through the quoting.
    assert scan_text(quoted, skip_quoted=False)["verdict"] == "fail"
    print("PASS test_skip_quoted")


def test_allow_phrases_suppresses_one_rule() -> None:
    text = "Hope this finds you well. Let me know if you need anything."
    base = scan_text(text)
    assert base["verdict"] == "fail"
    allowed = scan_text(text, allow_phrases=["hope this finds you well"])
    # The allowed phrase is gone, but "Let me know if" still fails.
    rules = {f["rule"] for f in allowed["findings"]}
    assert "filler_finds_well" not in rules, allowed
    assert "filler_let_me_know" in rules, allowed
    # Allowing both yields a pass.
    both = scan_text(
        text, allow_phrases=["hope this finds you well", "let me know if"]
    )
    assert both["verdict"] == "pass", both
    print("PASS test_allow_phrases_suppresses_one_rule")


def test_check_sections_flattens_bullets_and_cells() -> None:
    sections = [
        {"heading": "Summary", "body": "Clean opening sentence with substance."},
        {"heading": "Items", "bullets": ["Real item", "I wanted to circle back here"]},
        {
            "heading": "Compare",
            "table": {
                "headers": ["Option", "Note"],
                "rows": [["A", "solid"], ["B", "Best regards is buried here"]],
            },
        },
    ]
    result = check_sections(sections, brief_kind="memo")
    assert result["verdict"] == "fail", result
    rules = {f["rule"] for f in result["findings"]}
    assert "filler_wanted_circle" in rules, result  # found in a bullet
    assert "closer_best_regards" in rules, result  # found in a table cell
    print("PASS test_check_sections_flattens_bullets_and_cells")


def test_check_sections_structural_only_on_body() -> None:
    # Dashes inside a table cell must NOT fail (structural rules — including the
    # FB-16 dash ban — apply to body paragraphs only; cells are list-shaped
    # data, not prose). The same content in a body paragraph fails.
    table_sections = [
        {
            "heading": "T",
            "table": {"rows": [["x — y — z — w (cell dashes, ignored)"]]},
        }
    ]
    assert check_sections(table_sections, brief_kind="memo")["verdict"] == "pass"
    body_sections = [{"heading": "B", "body": "x — y — z — w in a paragraph."}]
    assert check_sections(body_sections, brief_kind="memo")["verdict"] == "fail"
    print("PASS test_check_sections_structural_only_on_body")


def _import_brief_writer():
    import importlib

    return importlib.import_module("brief_writer")


def test_brief_writer_memo_blocks_pre_save() -> None:
    bw = _import_brief_writer()
    tmp = Path(tempfile.mkdtemp(prefix="voicegate_"))
    out = tmp / "memo.docx"
    sections = [{"heading": "Update", "body": "I wanted to circle back on the deal."}]
    try:
        bw.make_brief(
            str(out),
            brief_kind="memo",
            title="Deal update",
            subtitle="Internal",
            sections=sections,
            contract="off",  # B3: isolating the voice gate, not the contract gate
        )
        raise AssertionError("expected VoiceTellError on memo with banned phrase")
    except VoiceTellError as e:
        assert "circle back" in str(e).lower() or e.findings, str(e)
    assert not out.exists(), "NO file must be written when the gate blocks"
    print("PASS test_brief_writer_memo_blocks_pre_save")


def test_brief_writer_call_prep_warn_only() -> None:
    bw = _import_brief_writer()
    tmp = Path(tempfile.mkdtemp(prefix="voicegate_"))
    out = tmp / "callprep.docx"
    sections = [{"heading": "Notes", "body": "I wanted to circle back on the deal."}]
    path = bw.make_brief(
        str(out),
        brief_kind="call_prep",
        title="Call prep",
        subtitle="Internal",
        exec_header={"verdict": "Walk out with the deal moved."},  # OUT2 §4 flip
        sections=sections,
        contract="off",  # B3: isolating the voice gate; this thin stub would trip the contract gate
    )
    assert Path(path).exists(), "call_prep is warn-only — file must be written"
    print("PASS test_brief_writer_call_prep_warn_only")


def test_brief_writer_voice_gate_off() -> None:
    bw = _import_brief_writer()
    tmp = Path(tempfile.mkdtemp(prefix="voicegate_"))
    out = tmp / "memo_off.docx"
    sections = [{"heading": "Update", "body": "I wanted to circle back on the deal."}]
    path = bw.make_brief(
        str(out),
        brief_kind="memo",
        title="Deal update",
        subtitle="Internal",
        exec_header={"verdict": "Deal moves Friday."},  # OUT2 §4 flip
        sections=sections,
        voice_gate="off",
        contract="off",  # B3: isolating voice_gate behavior; thin stub would trip the contract gate
    )
    assert Path(path).exists(), "voice_gate='off' must let the memo save"
    print("PASS test_brief_writer_voice_gate_off")


def test_brief_writer_clean_memo_saves() -> None:
    bw = _import_brief_writer()
    tmp = Path(tempfile.mkdtemp(prefix="voicegate_"))
    out = tmp / "clean_memo.docx"
    sections = [
        {"heading": "Update", "body": "Following up on the deal. Contract goes out Friday."}
    ]
    path = bw.make_brief(
        str(out),
        brief_kind="memo",
        title="Deal update",
        subtitle="Internal",
        exec_header={"verdict": "Deal moves Friday."},  # OUT2 §4 flip
        sections=sections,
        contract="off",  # B3: isolating voice-gate clean-pass; thin stub would trip the contract gate
    )
    assert Path(path).exists(), "a clean memo must save"
    print("PASS test_brief_writer_clean_memo_saves")


def test_cli_exit_codes() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="voicegate_cli_"))
    clean = tmp / "clean.txt"
    clean.write_text("Following up on the deal. Contract goes out Friday.\n")
    dirty = tmp / "dirty.txt"
    dirty.write_text("Best regards,\nThanks for reaching out.\n")

    r_clean = subprocess.run(
        [sys.executable, str(DETECTOR_PATH), str(clean)],
        capture_output=True, text=True,
    )
    assert r_clean.returncode == 0, (r_clean.returncode, r_clean.stdout, r_clean.stderr)

    r_dirty = subprocess.run(
        [sys.executable, str(DETECTOR_PATH), str(dirty)],
        capture_output=True, text=True,
    )
    assert r_dirty.returncode == 1, (r_dirty.returncode, r_dirty.stdout)

    # stdin form ("-")
    r_stdin = subprocess.run(
        [sys.executable, str(DETECTOR_PATH), "-"],
        input="Best regards,\n", capture_output=True, text=True,
    )
    assert r_stdin.returncode == 1, (r_stdin.returncode, r_stdin.stdout)
    print("PASS test_cli_exit_codes")


def test_composers_reference_detector() -> None:
    missing = []
    for skill in COMPOSER_SKILLS:
        md = ROOT / "skills" / skill / "SKILL.md"
        if not md.exists():
            missing.append(f"{skill} (SKILL.md not found)")
            continue
        if "voice_tell_detector" not in md.read_text(encoding="utf-8"):
            missing.append(f"{skill} (no voice_tell_detector reference)")
    assert not missing, "composer SKILL.mds missing detector wiring: " + ", ".join(missing)
    print(f"PASS test_composers_reference_detector ({len(COMPOSER_SKILLS)} skills)")


def main() -> int:
    test_sync_floor()
    test_every_banned_phrase_fails()
    test_finds_you_well_interpolation()
    test_clean_text_passes()
    test_thanks_for_reaching_out_fails()
    test_dash_as_punctuation_ban()
    test_dash_signoff_exempt()
    test_dash_ban_overridable()
    test_tri_colon_warns()
    test_hedging_stack_warns()
    test_skip_quoted()
    test_allow_phrases_suppresses_one_rule()
    test_check_sections_flattens_bullets_and_cells()
    test_check_sections_structural_only_on_body()
    test_brief_writer_memo_blocks_pre_save()
    test_brief_writer_call_prep_warn_only()
    test_brief_writer_voice_gate_off()
    test_brief_writer_clean_memo_saves()
    test_cli_exit_codes()
    test_composers_reference_detector()
    print("\nALL voice_tell_detector tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
