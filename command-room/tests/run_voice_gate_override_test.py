#!/usr/bin/env python3
"""Regression battery — the pre-save voice-tell gate CONSUMES the customer-side
voice-block override (B1 -> B2 wiring in brief_gates.run_pre_save_gates).

THE BUG (pre-existing since SPEC B2; found in second-eyes review of
fb-bundle-jul19, 2026-07-19): the gate called
`load_voice_block_override(brief_kind)` — wrong arity (the loader takes
(workspace_root, skill)) AND the wrong key (override files are
voice-block-<skill>.md, keyed by COMPOSER SKILL, never by brief kind). The
TypeError was swallowed by the gate's `except Exception`, so `allow_phrases`
was ALWAYS None: a client whose calibrated Voice Block carves out
"Best regards" still had memo saves hard-blocked. On top of that the loader
returned no phrase list at all, so even a correct call had nothing to feed
through. These tests pin the repaired path end-to-end: a Taboos carve-out in
the workspace override file suppresses the hard block; the override load
never degrades silently (a failure now prints on stderr).

House conventions: check(name, cond), OK/FAIL, non-zero exit, auto-discovered.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import brief_gates  # noqa: E402
import voice_corrections as vc  # noqa: E402
from voice_tell_detector import FAIL_BLOCKING_KINDS, VoiceTellError  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws():
    ws = Path(tempfile.mkdtemp(prefix="b2wire_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "voice").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


# A memo body carrying exactly one banned phrase ("Best regards" — the closers
# rule). memo is a FAIL_BLOCKING_KIND, so without a carve-out the save raises.
_SECTIONS = [
    {"heading": "Update", "body": "The rollout finished on schedule.\n\nBest regards"},
]

_CALIBRATED_BLOCK = (
    "## Voice Block\n\n"
    "### Punctuation\n"
    "- Em-dashes: rare\n\n"
    "### Taboos (per-skill overrides to universal list)\n"
    "- Never: leverage\n"
    '- OK despite being on universal list: "Best regards" (signs this way in 9 of 10 sent emails)\n'
)


def _run_gates(ws, sections=_SECTIONS):
    """Run the canonical gate stack for a memo, contract gate off (B3 is not
    under test), capturing stderr so the tests can assert on the gate's own
    diagnostics. Returns (gates_ran_or_None, raised_exc_or_None, stderr_text)."""
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            ran = brief_gates.run_pre_save_gates(
                brief_kind="memo",
                title="Q3 rollout memo",
                subtitle="For the leadership team",
                sections=sections,
                supported_kinds=brief_gates.SUPPORTED_BRIEF_KINDS,
                contract="off",
                voice_gate="default",
                exec_header={"verdict": "Rollout done; no action needed."},
                workspace_root=str(ws),
            )
        return ran, None, err.getvalue()
    except Exception as exc:  # noqa: BLE001 — the raise IS the assertion target
        return None, exc, err.getvalue()


def test_blocked_without_override():
    ws = _ws()
    ran, exc, _ = _run_gates(ws)
    check("no override -> banned closer still hard-blocks the memo save",
          isinstance(exc, VoiceTellError))


def test_taboos_carveout_reaches_the_gate():
    ws = _ws()
    vc.write_voice_block_override(ws, "memo-writer", _CALIBRATED_BLOCK,
                                  calibration_level="calibrated", sample_count=9)
    ran, exc, err = _run_gates(ws)
    check("calibrated Taboos carve-out -> memo save passes the voice gate",
          exc is None and ran is not None and "voice" in ran)
    check("override load did not degrade (no failure line on stderr)",
          "override load failed" not in err)


def test_override_is_keyed_by_skill_not_kind():
    ws = _ws()
    # The carve-out under the WRONG skill's file must not reach a memo save —
    # and a file named by the brief kind (the old bug's lookup) must not either.
    vc.write_voice_block_override(ws, "email-writer", _CALIBRATED_BLOCK)
    vc.write_voice_block_override(ws, "memo", _CALIBRATED_BLOCK)
    ran, exc, _ = _run_gates(ws)
    check("other skill's / kind-named override does not unblock the memo",
          isinstance(exc, VoiceTellError))


def test_dash_override_plumbs_without_breaking_the_gate():
    ws = _ws()
    block = _CALIBRATED_BLOCK.replace("Em-dashes: rare", "Em-dashes: frequent")
    vc.write_voice_block_override(ws, "memo-writer", block)
    ov = vc.load_voice_block_override(ws, "memo-writer")
    check("Em-dashes: frequent -> loader reports ban_dashes False",
          ov is not None and ov["ban_dashes"] is False)
    # The gate forwards ban_dashes=False only when the installed detector
    # accepts the kwarg (FB-16); either way the save must run, not TypeError.
    sections = [{"heading": "Update",
                 "body": "We shipped — fast.\n\nThe team closed it out."}]
    ran, exc, _ = _run_gates(ws, sections=sections)
    check("dash-keeping client: gate runs clean (no TypeError, no block)",
          exc is None and ran is not None and "voice" in ran)


def test_kind_to_skill_map_covers_blocking_kinds():
    check("VOICE_SKILL_BY_KIND covers exactly the hard-blocking kinds",
          set(brief_gates.VOICE_SKILL_BY_KIND) == set(FAIL_BLOCKING_KINDS))
    skills_dir = ROOT / "skills"
    check("every mapped skill exists in skills/",
          all((skills_dir / s).is_dir()
              for s in brief_gates.VOICE_SKILL_BY_KIND.values()))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_blocked_without_override()
    test_taboos_carveout_reaches_the_gate()
    test_override_is_keyed_by_skill_not_kind()
    test_dash_override_plumbs_without_breaking_the_gate()
    test_kind_to_skill_map_covers_blocking_kinds()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL voice_gate_override tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
