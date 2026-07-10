#!/usr/bin/env python3
"""
v4.6.1 S3 regression — the subject-line voice gate + the unified
vocabulary policy (FINDINGS F-47 P2d / F-53: em-dash subjects twice in
one day; F-53 P3a: "leverage" hard-blocked in a docx while leading an
email the same day because the two gates kept disjoint lists).

Guards:

  1. scan_subject fails ANY dash-as-punctuation (em, en, spaced hyphen),
     with no pile-up allowance; hyphenated compounds and colons pass.
  2. Subjects are also phrase- and vocabulary-gated (a subject leading
     with "Circling back" or "leverage" fails).
  3. ONE vocabulary list: both gates derive their marketing-word rules
     from shared/scripts/vocabulary_policy.py — the word that reproduces
     F-53 P3a ("leverage") now fails the EMAIL path too, and the docx
     leak gate's patterns come from the same list.
  4. allow_phrases (Voice Block Taboos) carves vocabulary words out —
     client safety preserved.
  5. Prose guard: the rule exists on every subject-minting surface.

Run via: python3 tests/run_subject_vocab_gate_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from voice_tell_detector import scan_subject, scan_text  # noqa: E402
from vocabulary_policy import MARKETING_WORDS  # noqa: E402
import docx_leak_scanner  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}{(' — ' + detail) if detail else ''}")


def rules(result) -> set:
    return {f["rule"] for f in result["findings"]}


def main() -> int:
    # ------------------------------------------------------------------
    print("[1] subject dash gate — the F-47 P2d / F-53 repro")
    # ------------------------------------------------------------------
    # the two real dogfood subjects, verbatim
    r = scan_subject("empower-lab-checkin.skill — my thoughts")
    check("F-47 P2d's em-dash subject FAILS", r["verdict"] == "fail"
          and "subject_dash" in rules(r))
    check("the hyphens in 'empower-lab-checkin' do NOT trip the gate",
          sum(1 for f in r["findings"] if f["rule"] == "subject_dash") == 1)
    r = scan_subject("Command Room x boutique consulting — next steps")
    check("F-53's em-dash subject FAILS", r["verdict"] == "fail")
    check("en dash fails too",
          scan_subject("Q2 deck – status")["verdict"] == "fail")
    check("spaced hyphen as punctuation fails",
          scan_subject("Q2 deck - status")["verdict"] == "fail")
    check("ONE dash fails (no pile-up allowance, unlike the body warn)",
          scan_subject("One — dash")["verdict"] == "fail")
    check("colon rewrite passes", scan_subject("Q2 deck: status")["verdict"] == "pass")
    check("comma rewrite passes",
          scan_subject("Follow-up: pricing call, Jul 9")["verdict"] == "pass")
    check("hyphenated compound passes", scan_subject("Quick check-in")["verdict"] == "pass")
    check("empty subject passes here (Rule 12 owns blanks)",
          scan_subject("")["verdict"] == "pass")

    # ------------------------------------------------------------------
    print("[2] subjects are phrase- and vocabulary-gated")
    # ------------------------------------------------------------------
    r = scan_subject("Circling back on a few things")
    check("banned phrase in a subject fails", r["verdict"] == "fail"
          and "filler_circling_back" in rules(r))
    check("the replacement chase subject passes",
          scan_subject("Quick check on a few things")["verdict"] == "pass")
    r = scan_subject("The most leveraged channel")
    check("inflected 'leveraged' in a subject fails (word-boundary prefix match)",
          r["verdict"] == "fail" and "vocab_leverage" in rules(r))

    # ------------------------------------------------------------------
    print("[3] one vocabulary list, both gates read it (F-53 P3a)")
    # ------------------------------------------------------------------
    r = scan_text("We should leverage the new channel for this.", context="email")
    check("'leverage' now FAILS the email path", r["verdict"] == "fail"
          and "vocab_leverage" in rules(r))
    for w in MARKETING_WORDS:
        r = scan_text(f"A note about {w} in the body.", context="email")
        check(f"vocabulary word {w!r} fails the voice gate", r["verdict"] == "fail")
    leak_rules = {name for name, _ in docx_leak_scanner._FORBIDDEN_PATTERNS}
    check("the docx leak gate carries every shared vocabulary word",
          all(f"marketing_{w}" in leak_rules for w in MARKETING_WORDS),
          str(sorted(leak_rules)))
    check("the leak gate's marketing rules come ONLY from the shared list",
          {n for n in leak_rules if n.startswith("marketing_")}
          == {f"marketing_{w}" for w in MARKETING_WORDS})

    # ------------------------------------------------------------------
    print("[4] client carve-out — allow_phrases still wins")
    # ------------------------------------------------------------------
    r = scan_text("We should leverage the new channel.", context="email",
                  allow_phrases=["leverage"])
    check("a calibrated Voice Block Taboo feeds a vocab word through",
          "vocab_leverage" not in rules(r))
    r = scan_subject("Quick note on leverage", allow_phrases=["leverage"])
    check("the carve-out applies to subjects too", r["verdict"] == "pass")

    # ------------------------------------------------------------------
    print("[5] prose guard — every subject-minting surface carries the rule")
    # ------------------------------------------------------------------
    surfaces = {
        "shared/VOICE_CALIBRATION.md": ["### Subject lines", "vocabulary_policy.py"],
        "skills/email-writer/SKILL.md": ["--context subject"],
        ("skills/enable-command-room-schedules/references/"
         "orchestrator-commitments.md"): ["--context subject"],
        "skills/follow-up-ritual/SKILL.md": ["--context subject"],
    }
    for rel, needles in surfaces.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for needle in needles:
            check(f"{rel} carries {needle!r}", needle in text)
    # no surface still mints the banned/dash example subjects
    oc = (ROOT / "skills/enable-command-room-schedules/references/"
                 "orchestrator-commitments.md").read_text(encoding="utf-8")
    check("orchestrator-commitments no longer instructs a 'Circling back' subject",
          '("Subject", "Circling back' not in oc
          and 'Subject: "Circling back' not in oc)
    fu = (ROOT / "skills/follow-up-ritual/SKILL.md").read_text(encoding="utf-8")
    check("follow-up-ritual's subject template is dash-free",
          'Subject: "Follow-up: [Meeting topic] — [Date]"' not in fu)

    # ------------------------------------------------------------------
    print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
    if FAIL:
        print("FAIL — subject/vocabulary gate regressed")
        return 1
    print("OK — subject gate + unified vocabulary policy hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
