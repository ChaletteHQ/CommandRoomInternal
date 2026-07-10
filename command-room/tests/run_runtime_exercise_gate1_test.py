#!/usr/bin/env python3
"""
Runtime exercise pass for SPEC GATE1 — the runtime-enforcement fix.

WHY THIS EXISTS (the whole point)
---------------------------------
v3.20.0's save-time gates passed every STATIC suite and still shipped broken:
`test_composers_reference_detector` only checked that SKILL.mds *reference* the
detector, and the validator tests called `validate_brief` in ISOLATION. Nothing
exercised the RUNTIME render path — whether a gate actually fires when a
deliverable is produced through `make_brief`, and whether a bypass is detectable.
That gap is exactly what let a voice-violating .docx reach disk.

This exercise closes that gap. It drives the REAL render path (`make_brief`) and
the REAL chat-render backstop (`turn_backstop`) against a synthetic workspace and
asserts:

  1. a successful render EMITS a `gate_ran` event recording which gates ran (the
     detectable-bypass signal), on the actual make_brief path — not in isolation;
  2. the contract gate and the voice gate actually BLOCK on the render path
     (raise, write NO file, and therefore emit NO gate_ran);
  3. a deliverable produced WITHOUT make_brief leaves NO gate_ran — and the
     join that flags that bypass works;
  4. the turn-level backstop fires on the email/chat surface (the path that
     never reaches make_brief), emitting a `gate_ran` surface=chat_email event;
  5. #v3200-2: the interpolated "I hope this email finds you well" is caught on
     that chat path end-to-end;
  6. `gate_ran` is a real events-schema enum member (self-contained, no EXEC1
     brief_meta dependency);
  7. every composer SKILL.md carries the GATE1 deliverable-render MUST-language.

Run from the command-room repo root:
    python tests/run_runtime_exercise_gate1_test.py
(Named run_*_test so tests/run_all.py discovers it; classified in the `runtime`
tier via the "runtime_exercise" marker.) Exits 0 on full green, 1 on any failure.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def _ok(name: str, detail: str = "") -> None:
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    PASS.append(name)


def _fail(name: str, reason: str) -> None:
    print(f"  FAIL  {name}: {reason}")
    FAIL.append((name, reason))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _read_events(ws: Path) -> list[dict]:
    p = ws / "_hq" / "data" / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def _new_ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="gate1_rt_"))
    (ws / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    return ws


# -----------------------------------------------------------------------------
# Exercise 1 — gate_ran fires on the REAL make_brief render path
# -----------------------------------------------------------------------------

def exercise_gate_ran_on_render() -> None:
    _section("§1 gate_ran emitted on the make_brief render path (contract+voice+leak)")
    from brief_writer import make_brief

    ws = _new_ws()
    out = ws / "prep.docx"
    # call_prep: total-word floor is WARN (won't block), only Meeting Details is
    # required — so contract='enforce' RUNS and PASSES, giving us a success path
    # that records all three gates.
    make_brief(
        str(out),
        brief_kind="call_prep",
        title="Acme kickoff — prep",
        subtitle="Fri 9:00 AM PT",
        exec_header={"verdict": "Walk out with the cutover window confirmed."},  # OUT2 §4 flip
        sections=[
            {"heading": "Meeting Details",
             "body": "Acme kickoff. Friday 9:00 AM PT, 45 minutes, Google Meet. "
                     "Attendee: the Acme account lead. Project routing: Acme onboarding."},
            {"heading": "Where We Left Off",
             "body": "We agreed last week to confirm the data migration window. "
                     "The lead asked for a written cutover plan. We owe them the "
                     "revised pricing sheet. They owe us the sandbox credentials. "
                     "Nothing else is blocking the kickoff."},
        ],
        contract="enforce",
        voice_gate="default",
        workspace_root=str(ws),
    )
    if not out.exists():
        _fail("render success", "call_prep brief was not written")
        return
    events = _read_events(ws)
    gate_evs = [e for e in events if e.get("type") == "gate_ran"]
    if len(gate_evs) != 1:
        _fail("gate_ran emitted once", f"expected 1 gate_ran, got {len(gate_evs)}: {gate_evs}")
        return
    data = gate_evs[0].get("data") or {}
    gates = set(data.get("gates") or [])
    if data.get("surface") == "docx" and {"contract", "voice", "leak"} <= gates:
        _ok("gate_ran records contract+voice+leak on the docx render path", f"gates={sorted(gates)}")
    else:
        _fail("gate_ran gates", f"unexpected gate_ran data: {data}")
    # The seq must have been auto-stamped inside the locked writer.
    if isinstance(gate_evs[0].get("seq"), int) and gate_evs[0].get("ts"):
        _ok("gate_ran seq+ts auto-stamped by the locked writer")
    else:
        _fail("gate_ran stamping", f"missing seq/ts: {gate_evs[0]}")


# -----------------------------------------------------------------------------
# Exercise 2 — the gates BLOCK on the render path (raise, no file, NO gate_ran)
# -----------------------------------------------------------------------------

def exercise_gates_block_on_render() -> None:
    _section("§2 contract + voice gates block on the render path (no file, no gate_ran)")
    from brief_writer import make_brief
    from output_contract_validator import OutputContractError
    from voice_tell_detector import VoiceTellError

    # 2a — contract gate blocks a sub-floor one_pager.
    ws = _new_ws()
    out = ws / "thin.docx"
    try:
        make_brief(
            str(out),
            brief_kind="one_pager",
            title="Command Room is great",          # 4 words — sub-floor headline is fine,
            subtitle="x",
            sections=[{"heading": "Recommendation", "body": "Buy it now."}],  # ~3 words total
            contract="enforce",
            workspace_root=str(ws),
        )
        _fail("contract gate blocks", "expected OutputContractError on a sub-floor one_pager")
    except OutputContractError:
        no_file = not out.exists()
        no_gate = not any(e.get("type") == "gate_ran" for e in _read_events(ws))
        if no_file and no_gate:
            _ok("contract gate raised pre-save — no file AND no gate_ran (blocked render is detectable)")
        else:
            _fail("contract block side-effects", f"file_exists={out.exists()}, gate_ran_present={not no_gate}")

    # 2b — voice gate blocks a memo carrying a banned phrase.
    ws2 = _new_ws()
    out2 = ws2 / "voice.docx"
    try:
        make_brief(
            str(out2),
            brief_kind="memo",
            title="Deal update",
            subtitle="Internal",
            sections=[{"heading": "Update", "body": "I hope this email finds you well. We won the deal."}],
            contract="off",            # isolate the voice gate
            voice_gate="default",
            workspace_root=str(ws2),
        )
        _fail("voice gate blocks", "expected VoiceTellError on a memo with a banned phrase")
    except VoiceTellError:
        no_file = not out2.exists()
        no_gate = not any(e.get("type") == "gate_ran" for e in _read_events(ws2))
        if no_file and no_gate:
            _ok("voice gate raised pre-save — no file AND no gate_ran; #v3200-2 phrase caught at render")
        else:
            _fail("voice block side-effects", f"file_exists={out2.exists()}, gate_ran_present={not no_gate}")


# -----------------------------------------------------------------------------
# Exercise 3 — the detectable-bypass join (deliverable event with no gate_ran)
# -----------------------------------------------------------------------------

def _bypass_flagged(events: list[dict]) -> bool:
    """A deliverable-class event with NO gate_ran in the same event set is a
    flaggable bypass. This is the join the verify loop / cleanup runs per turn."""
    DELIVERABLE_TYPES = {
        "one_pager_drafted", "memo_drafted", "decision_memo_drafted",
        "board_pack_assembled", "followup_pack_drafted",
    }
    has_deliverable = any(e.get("type") in DELIVERABLE_TYPES for e in events)
    has_gate = any(e.get("type") == "gate_ran" for e in events)
    return has_deliverable and not has_gate


def exercise_bypass_detection() -> None:
    _section("§3 detectable-bypass join — deliverable event without a gate_ran is flagged")
    # The v3.20.0 failure: a one_pager produced via the generic docx skill emits
    # the deliverable event but never gate_ran.
    bypass = [{"type": "one_pager_drafted", "data": {"topic": "value prop"}}]
    if _bypass_flagged(bypass):
        _ok("a one_pager_drafted with no gate_ran is flagged as a bypass")
    else:
        _fail("bypass detection", "should flag a deliverable event lacking gate_ran")

    # The gated path: deliverable event + gate_ran present → NOT a bypass.
    gated = [
        {"type": "one_pager_drafted", "data": {"topic": "value prop"}},
        {"type": "gate_ran", "data": {"surface": "docx", "gates": ["contract", "voice", "leak"]}},
    ]
    if not _bypass_flagged(gated):
        _ok("a one_pager_drafted WITH a gate_ran is not flagged")
    else:
        _fail("bypass detection", "gated render should not be flagged")


# -----------------------------------------------------------------------------
# Exercise 4 — turn-level backstop on the email/chat surface
# -----------------------------------------------------------------------------

def exercise_turn_backstop() -> None:
    _section("§4 turn-level backstop fires on the chat email surface (no make_brief)")
    from turn_backstop import scan_data_view_for_tells

    ws = _new_ws()
    dirty = {"sections": [{"items": [{
        "n": 1,
        "metadata": [["To", "lead@example.com"], ["Subject", "Checking in"]],
        "body_lines": ["I hope this email finds you well.", "Best regards"],
    }]}]}
    r = scan_data_view_for_tells(dirty, workspace_root=str(ws), source_skill="email-writer")
    if r["fail_count"] >= 2 and r["items_scanned"] == 1:
        _ok("backstop catches interpolated finds-you-well + Best regards in a chat email body",
            f"fail_count={r['fail_count']}")
    else:
        _fail("backstop dirty", f"unexpected result: {r}")

    gate_evs = [e for e in _read_events(ws) if e.get("type") == "gate_ran"]
    if (gate_evs and (gate_evs[0].get("data") or {}).get("surface") == "chat_email"
            and (gate_evs[0]["data"]).get("result") == "fail"):
        _ok("backstop emits a detectable gate_ran surface=chat_email result=fail")
    else:
        _fail("backstop gate_ran", f"expected chat_email fail event, got {gate_evs}")

    # Clean email → pass, no fail findings.
    ws2 = _new_ws()
    clean = {"sections": [{"items": [{
        "n": 1,
        "metadata": [["To", "lead@example.com"], ["Subject", "Re: cutover"]],
        "body_lines": ["Tuesday 2pm works. I'll send the cutover plan Monday."],
    }]}]}
    r2 = scan_data_view_for_tells(clean, workspace_root=str(ws2), source_skill="email-writer")
    res = [e for e in _read_events(ws2) if e.get("type") == "gate_ran"]
    if r2["fail_count"] == 0 and res and (res[0]["data"]).get("result") == "pass":
        _ok("clean email body → no fails, gate_ran result=pass")
    else:
        _fail("backstop clean", f"result={r2}, events={res}")

    # The renderer chokepoint wires the backstop in (non-blocking) — a dirty
    # body must still RENDER (never raise) while the warning fires.
    from chat_output_renderer import render_chat_output_widget
    rdv = {"header": "Draft", "sections": [{"title": None, "count": None, "items": [{
        "n": 1, "icon": "✉️", "name": "Lead",
        "metadata": [["To", "lead@example.com"], ["Subject", "Checking in"]],
        "body_lines": ["I hope this email finds you well."],
        "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"],
    }]}]}
    try:
        html = render_chat_output_widget(rdv, wrapper="fragment")
        if html and "Lead" in html:
            _ok("renderer wires the backstop in NON-BLOCKING (dirty email still renders)")
        else:
            _fail("renderer backstop", "render produced no/!expected html")
    except Exception as e:
        _fail("renderer backstop", f"render raised {type(e).__name__}: {e} (backstop must not block)")


# -----------------------------------------------------------------------------
# Exercise 5 — schema + MUST-language static guards (with teeth)
# -----------------------------------------------------------------------------

def exercise_schema_and_must_language() -> None:
    _section("§5 gate_ran is a real schema enum member + composers carry MUST-language")
    schema = json.loads((ROOT / "shared" / "data-schemas" / "events.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["type"]["enum"]
    if "gate_ran" in enum:
        _ok("gate_ran is an events.schema.json enum member (self-contained)")
    else:
        _fail("schema enum", "gate_ran missing from events.schema.json enum")

    composers = [
        "one-pager-composer", "decision-memo-composer", "board-pack-assembler",
        "call-prep", "memo-writer",
    ]
    missing = []
    for s in composers:
        text = (ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
        has_gate_block = "Deliverable Render Gate (GATE1" in text
        has_make_brief = "make_brief" in text
        has_no_handroll = "NEVER hand-roll" in text or "never hand-roll" in text.lower()
        if not (has_gate_block and has_make_brief and has_no_handroll):
            missing.append(f"{s} (gate_block={has_gate_block}, make_brief={has_make_brief}, no_handroll={has_no_handroll})")
    if not missing:
        _ok(f"all {len(composers)} composers carry the GATE1 deliverable-render MUST-language")
    else:
        _fail("MUST-language", f"missing in: {missing}")


def report() -> int:
    print("\n" + "=" * 70)
    print(f"  PASSED: {len(PASS)}")
    print(f"  FAILED: {len(FAIL)}")
    if FAIL:
        print("\n  Failures:")
        for name, reason in FAIL:
            print(f"    - {name}: {reason}")
        return 1
    return 0


def main() -> int:
    print("SPEC GATE1 runtime exercise — proves gates fire on the real render path")
    print(f"Repository: {ROOT}")
    exercise_gate_ran_on_render()
    exercise_gates_block_on_render()
    exercise_bypass_detection()
    exercise_turn_backstop()
    exercise_schema_and_must_language()
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
