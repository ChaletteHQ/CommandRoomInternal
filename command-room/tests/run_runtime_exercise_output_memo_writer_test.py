#!/usr/bin/env python3
"""SPEC A8 — memo-writer output regression exercise (runtime tier).

Simulates what memo-writer's SKILL.md instructs the model to do MECHANICALLY for
"write a memo on pricing" against the workspace_mini fixture: read the substrate with
the real readers, derive source_decision_ids from prior `decision` events on the topic,
build the section payload, render via the real `make_brief`, append the `memo_drafted`
event via the real append path. Asserts STRUCTURE + DERIVATION + side-effects + a leak
golden — never prose quality (that's the eval layer). LLM prose is a fixed leak-clean
stand-in.

Discovered by run_all.py (run_ prefix) into the runtime tier (runtime_exercise marker).
Regenerate the golden with: CR_UPDATE_GOLDENS=1 python tests/run_output_regression.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import output_exercise_lib as lib  # noqa: E402

NOW = "2026-06-01T12:00:00+00:00"
DATE = "2026-06-01"


def _pricing_decision_seqs(events: list[dict]) -> list[int]:
    """The derivation memo-writer's SKILL.md specifies: prior `decision` events on the
    topic, for this project — their seqs become data.source_decision_ids[]."""
    out = []
    for e in events:
        if e.get("type") != "decision" or e.get("primary_thread_id") != "project_001":
            continue
        d = e.get("data", {})
        blob = (str(d.get("topic", "")) + " " + str(d.get("summary", ""))).lower()
        if "pricing" in blob:
            out.append(e["seq"])
    return sorted(out)


def _sections(body_decision: str) -> list[dict]:
    """The decision-memo structure (5 headings incl. one table). `body_decision` lets
    the negative leak case inject a forbidden token into the body."""
    return [
        {"heading": "Decision", "body": body_decision},
        {"heading": "Context",
         "body": "Pricing has been the recurring open question across three logged "
                 "decisions. The Northstar renewal forces the question now."},
        {"heading": "Options considered",
         "table": {"headers": ["Option", "Pros", "Cons"],
                   "rows": [
                       ["Per-seat monthly", "Simple to explain", "Revenue is lumpy"],
                       ["Annual-only", "Predictable revenue", "Higher friction to close"],
                       ["Usage-based", "Scales with value", "Hard to forecast"],
                   ]}},
        {"heading": "Rationale",
         "bullets": ["Annual-only smooths revenue ahead of the renewal.",
                     "Tiered seats keep the entry price honest.",
                     "Locking tiers now removes a renewal-time negotiation variable."]},
        {"heading": "Next steps",
         "bullets": ["Ship the pricing page copy this week.",
                     "Send the pricing sheet to the Northstar team."]},
    ]


def main() -> int:
    from brief_writer import make_brief
    from docx_leak_scanner import LeakScanError
    from cru_match import load_events_defensively
    from next_seq import next_seq
    from atomic_write import atomic_append_jsonl

    ok, fail, section, finish = lib.make_recorder()
    ws = lib.copy_fixture()
    events_path = ws / "_hq" / "data" / "events.jsonl"
    events, _skipped = load_events_defensively(str(events_path))

    section("derivation")
    decision_ids = _pricing_decision_seqs(events)
    if decision_ids == [8, 14, 20]:
        ok("source_decision_ids derived from the 3 pricing decisions", str(decision_ids))
    else:
        fail("source_decision_ids derivation", f"expected [8,14,20], got {decision_ids}")

    section("render (real make_brief)")
    out_dir = ws / "Acme Co - Sourcing Bot" / "deliverables" / "memos"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{DATE}_pricing.docx"
    clean_decision = ("Move Sourcing Bot to annual-only billing and lock the pricing "
                      "tiers before the Northstar renewal.")
    try:
        make_brief(
            str(out_path), brief_kind="memo",
            title="Acme Co — Sourcing Bot — Pricing",
            subtitle=f"Strategy memo · {DATE} · Acme Co",
            sections=_sections(clean_decision),
            contract="report",  # structure exercise — the contract gate has its own test
        )  # no workspace_root: we don't want make_brief's gate_ran audit events polluting
           # the fixture copy's seq line — the memo_drafted append below owns the side-effect
        ok("make_brief rendered the memo without raising")
    except Exception as e:
        fail("make_brief rendered the memo", f"{type(e).__name__}: {e}")
        return finish("memo_writer_exercise")

    if out_path.exists() and out_path.stat().st_size > 5000:
        ok("memo .docx exists at the canonical path, > 5KB", f"{out_path.stat().st_size}B")
    else:
        fail("memo .docx exists > 5KB")

    section("extracted structure + golden")
    text = lib.extract_docx_text(out_path)
    for heading in ("MEMO", "Decision", "Context", "Options considered", "Rationale", "Next steps"):
        if heading in text:
            ok(f"contains heading/eyebrow: {heading}")
        else:
            fail(f"contains heading/eyebrow: {heading}")
    if "Command Room" in text:
        ok("footer 'Command Room' present")
    else:
        fail("footer 'Command Room' present")
    ph = lib.assert_no_placeholders(text)
    ok("no placeholder tokens in output") if not ph else fail("no placeholders", str(ph))
    matched, diff = lib.compare_golden("memo_writer", text)
    ok("extracted text matches golden") if matched else fail("golden match", diff[:600])

    section("leak-scan negative case")
    try:
        make_brief(
            str(out_dir / "leak_probe.docx"), brief_kind="memo",
            title="Acme Co — Sourcing Bot — Pricing",
            subtitle=f"Strategy memo · {DATE}",
            sections=_sections("This memo references project_001 in the body."),
            contract="off",
        )
        fail("leak scanner raises on an internal id in the body (project_001)")
    except LeakScanError:
        ok("LeakScanError raised when project_001 appears in the body")
    except Exception as e:
        # Any other raise still means the leaky doc was NOT written clean — acceptable
        # as long as it didn't silently pass. Treat a non-leak raise as a soft pass only
        # if it's clearly the gate; otherwise fail.
        fail("leak scanner raises on internal id", f"unexpected {type(e).__name__}: {e}")

    section("substrate side-effect (memo_drafted event)")
    seq = next_seq(str(events_path))
    ev = {
        "seq": seq, "ts": NOW, "type": "memo_drafted", "source_skill": "memo-writer",
        "primary_thread_id": "project_001",
        "data": {"topic": "pricing", "audience": "team", "memo_type": "strategy_memo",
                 "primary_thread_id": "project_001",
                 "artifact_path": "Acme Co — Sourcing Bot/deliverables/memos/" + f"{DATE}_pricing.docx",
                 "source_decision_ids": decision_ids},
    }
    atomic_append_jsonl(events_path, [ev])
    appended = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    v = lib.validate_event(appended)
    ok("appended memo_drafted passes the schema validator") if not v else fail("event valid", str(v))
    if appended["seq"] == 29:
        ok("seq is 29 (next after the fixture's 28 events)")
    else:
        fail("seq monotonic", f"expected 29, got {appended['seq']}")
    if appended["source_skill"] == "memo-writer":
        ok("source_skill == memo-writer")
    else:
        fail("source_skill", appended.get("source_skill"))
    if appended["data"]["source_decision_ids"] == [8, 14, 20]:
        ok("event carries source_decision_ids == [8,14,20]")
    else:
        fail("source_decision_ids on event", str(appended["data"]["source_decision_ids"]))

    # the checked-in fixture must be untouched (we mutated the temp copy only)
    orig = (lib.FIXTURE / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
    if len([l for l in orig.splitlines() if l.strip()]) == 28:
        ok("checked-in fixture untouched (still 28 events)")
    else:
        fail("checked-in fixture untouched")

    return finish("memo_writer_exercise")


if __name__ == "__main__":
    raise SystemExit(main())
