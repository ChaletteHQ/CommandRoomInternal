#!/usr/bin/env python3
"""
Pre-big-test follow-up pins (FU-A4 / FU-C1 / FU-B1 from the PGUARD2
third-eyes ledger, handoffs/REVIEW_PGUARD2_secondeyes_2026-07-19.md).

FU-A4 — the balance `book` confirm-path linkage event type must be PINNED
        (not left to the executing model) in BOTH prose sites — balance
        SKILL Step 4 confirm item 3 and the apply-choices balance dispatch —
        and the pinned type must be schema-registered and personal-classified
        (an unpinned type let an executing LLM put venue + date-night content
        into org-scoped reads).
FU-C1 — report-bug's Last-5 events capture feeds an OUTBOUND email; a
        personal-lane event type token in that table is the
        `personal_event_type` fingerprint leaving the workspace. The capture
        must mask rows where `personal_leak.is_personal(row)` is true.
FU-B1 — value-receipt Step 6b must teach that a RAISED LeakScanError is
        surfaced verbatim, never folded into the `None` no-fit fallback.

Fixture dates are computed relative to runtime NOW (G14); person references
are synthetic ids only.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import personal_leak as PL  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


BALANCE_SKILL = (ROOT / "skills" / "balance" / "SKILL.md").read_text(encoding="utf-8")
APPLY_SKILL = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(encoding="utf-8")
REPORT_BUG_SKILL = (ROOT / "skills" / "report-bug" / "SKILL.md").read_text(encoding="utf-8")
VALUE_RECEIPT_SKILL = (ROOT / "skills" / "value-receipt" / "SKILL.md").read_text(encoding="utf-8")
SCHEMA = json.loads(
    (ROOT / "shared" / "data-schemas" / "events.schema.json").read_text(encoding="utf-8")
)

# The shared pin phrase both prose sites must carry — extraction, not
# hardcoding, so a drift in EITHER file (or a divergence between them) fails.
LINKAGE_PIN_RE = re.compile(
    r"append the follow-on linkage as a `([a-z][a-z0-9_]+)` event"
)


def test_fu_a4_linkage_type_pinned() -> None:
    print("\nFU-A4 — confirm-path linkage event type pin")

    in_balance = LINKAGE_PIN_RE.findall(BALANCE_SKILL)
    in_apply = LINKAGE_PIN_RE.findall(APPLY_SKILL)
    check("balance SKILL pins the linkage type exactly once",
          len(in_balance) == 1, f"found {in_balance}")
    check("apply-choices SKILL pins the linkage type exactly once",
          len(in_apply) == 1, f"found {in_apply}")
    if not (in_balance and in_apply):
        return
    t_balance, t_apply = in_balance[0], in_apply[0]
    check("the two prose pins are the SAME type string",
          t_balance == t_apply, f"{t_balance!r} != {t_apply!r}")

    t = t_balance
    enum = set(SCHEMA["properties"]["type"]["enum"])
    check(f"pinned type `{t}` is schema-registered (events.schema.json enum)",
          t in enum)
    check("pinned type is personal-classified (_PERSONAL_EVENT_TYPES)",
          t in PL._PERSONAL_EVENT_TYPES)
    # Type ALONE must classify the row — the firewall cannot depend on the
    # executing model remembering data.personal on the append.
    check("is_personal(row) is True on type alone (no data.personal flag)",
          PL.is_personal({"type": t, "data": {"tie_person_id": "person_9001"}}))
    # And the rendered-text backstop knows the token.
    pet = dict((n, p) for n, p in PL.PERSONAL_LEAK_PATTERNS)["personal_event_type"]
    check("personal_event_type fingerprint covers the pinned token",
          bool(pet.search(f"stray {t} token")))
    # Both prose sites also mandate the personal flag on the append.
    for name, text in (("balance", BALANCE_SKILL), ("apply-choices", APPLY_SKILL)):
        seg_start = text.find(f"`{t}` event")
        seg = text[seg_start:seg_start + 200] if seg_start >= 0 else ""
        check(f"{name} pin mandates `data.personal: true` on the append",
              "`data.personal: true`" in seg)

    # Grep proof (mirrors run_balance_test acceptance #5 for the suggested
    # token): no org-facing skill/driver names the pinned linkage type.
    # Allowlist = the personal lane itself + registration + documentation.
    allow = {
        "shared/scripts/personal_leak.py",       # the lane classifier
        "shared/data-schemas/events.schema.json",
        "shared/EVENT_TYPES.md",                 # lane documentation
        "skills/balance/SKILL.md",               # the owner surface itself
        "skills/apply-choices/SKILL.md",         # the confirm-path writer
    }
    offenders = []
    for base in (ROOT / "skills", ROOT / "shared"):
        for f in base.rglob("*"):
            if f.suffix not in (".py", ".md", ".json") or not f.is_file():
                continue
            rel = f.relative_to(ROOT).as_posix()
            if rel in allow:
                continue
            try:
                if t in f.read_text(encoding="utf-8", errors="replace"):
                    offenders.append(rel)
            except OSError:
                continue
    check("no org-facing skill/driver references the pinned linkage type",
          offenders == [], f"offenders={offenders}")


# ---------------------------------------------------------------------------
# FU-C1 — report-bug Last-5 capture never leaks a personal-lane type token.
# ---------------------------------------------------------------------------

NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _ts(minutes_ago: int) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat()


def _fixture_tail() -> list[dict]:
    """A realistic last-5 tail: four org-lane rows + one balance nudge.
    Mirrors real substrate shape (type / source_skill / ts / data)."""
    return [
        {"type": "meeting_processed", "source_skill": "meeting-notes",
         "ts": _ts(190), "data": {}},
        {"type": "commitment", "source_skill": "meeting-notes",
         "ts": _ts(188), "data": {"kind": "promise"}},
        {"type": "balance_nudge_suggested", "source_skill": "balance",
         "ts": _ts(120),
         "data": {"personal": True, "tie_person_id": "person_9001"}},
        {"type": "email_drafted", "source_skill": "email-writer",
         "ts": _ts(45), "data": {}},
        {"type": "pack_run", "source_skill": "morning-briefing",
         "ts": _ts(10), "data": {}},
    ]


def _capture_last5(rows: list[dict], mask_personal: bool) -> list[str]:
    """The Step-2 item-3 capture recipe as prose documents it: type ·
    source_skill · ts per row, personal-lane rows masked."""
    out = []
    for i, row in enumerate(rows, start=1):
        if mask_personal and PL.is_personal(row):
            out.append(f"{i}. (personal-lane event) · (masked) · {row['ts']}")
        else:
            out.append(f"{i}. {row['type']} · {row['source_skill']} · {row['ts']}")
    return out


def _draft_body(last5_lines: list[str]) -> str:
    """The Step-4b body template's LAST 5 EVENTS section, assembled the way
    the SKILL's verbatim template lays it out."""
    return (
        "AUTO-DIAGNOSIS\n"
        "• Plugin version: 0.0.0-test\n"
        "• Last skill fired: morning-briefing at " + _ts(10) + "\n"
        "\nLAST 5 EVENTS\n" + "\n".join(last5_lines) + "\n"
    )


def test_fu_c1_report_bug_capture_masked() -> None:
    print("\nFU-C1 — report-bug outbound capture masks personal-lane rows")

    # Prose pin: the capture instruction itself carries the mask rule.
    m = re.search(r"\*\*Last 5 events\*\*[^\n]*", REPORT_BUG_SKILL)
    line = m.group(0) if m else ""
    check("Last-5 capture line exists in report-bug SKILL", bool(line))
    check("capture line invokes personal_leak.is_personal",
          "personal_leak.is_personal" in line)
    check("capture line pins the masked rendering literal",
          "(personal-lane event)" in line)
    check("capture line names the outbound stake (email leaves the workspace)",
          "OUTBOUND" in line)

    # Fixture: a balance_nudge_suggested in the tail NEVER reaches draft text.
    rows = _fixture_tail()
    body = _draft_body(_capture_last5(rows, mask_personal=True))
    check("masked body carries no balance_nudge_suggested token",
          "balance_nudge_suggested" not in body)
    check("masked body carries no personal-lane fingerprint at all",
          PL.scan_for_personal_leak(body) == [])
    check("masked body still lists 5 rows (masked, not dropped — timing shape kept)",
          body.count("\n1. ") + body.count("\n2. ") + body.count("\n3. ")
          + body.count("\n4. ") + body.count("\n5. ") == 5)
    check("non-personal rows still captured verbatim",
          "meeting_processed · meeting-notes" in body
          and "pack_run · morning-briefing" in body)

    # Planted red — the UNMASKED capture is exactly the leak the scanner must
    # see (proves the fixture is live and the fingerprint would catch it).
    leaky = _draft_body(_capture_last5(rows, mask_personal=False))
    findings = PL.scan_for_personal_leak(leaky)
    check("unmasked capture IS flagged by the personal_event_type fingerprint",
          any(f["name"] == "personal_event_type" for f in findings))


def test_fu_b1_value_receipt_refusal_surfaced() -> None:
    print("\nFU-B1 — value-receipt surfaces a raised gate, never folds it")

    m = re.search(r"### Step 6b.*?(?=\n### )", VALUE_RECEIPT_SKILL, re.DOTALL)
    step6b = m.group(0) if m else ""
    check("Step 6b section found", bool(step6b))
    check("Step 6b distinguishes a RAISED error from the None no-fit",
          "LeakScanError" in step6b)
    check("Step 6b mandates surfacing the gate message verbatim",
          "verbatim" in step6b)
    check("Step 6b forbids folding a refusal into the no-fit line",
          "never folded" in step6b)


def main() -> int:
    print("run_fu_pretest_pins_test — FU-A4 / FU-C1 / FU-B1 pins")
    test_fu_a4_linkage_type_pinned()
    test_fu_c1_report_bug_capture_masked()
    test_fu_b1_value_receipt_refusal_surfaced()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
