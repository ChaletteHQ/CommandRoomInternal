#!/usr/bin/env python3
"""Phase 6 Quick Wins — A (check-deliverables → corrections corpus) and
B (Pulse 'just busy' → persisted cadence baseline)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import deliverable_sweep as ds  # noqa: E402
import dormancy as dz  # noqa: E402
import people_writer as pw  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


# --- Quick Win A ------------------------------------------------------------

def test_feed_voice_corrections():
    ws = Path(tempfile.mkdtemp(prefix="qwa_"))
    (ws / "_hq").mkdir(parents=True)
    result = {
        "flagged": [
            {"path": str(ws / "Acme_Board_Pack_2026-07-01.docx"),
             "voice": {"verdict": "fail", "findings": [
                 {"rule": "opener_happy_to", "severity": "fail",
                  "line": "I'd be happy to help.", "match": "I'd be happy to"},
                 {"rule": "closer_best_regards", "severity": "fail",
                  "line": "Best regards,", "match": "Best regards"},
                 {"rule": "structural_tri_colon", "severity": "warn",
                  "line": "x", "match": "tri-colon"},
             ]},
             "leaks": [{"match": "project_007"}]},
            {"path": str(ws / "clean.docx"),
             "voice": {"verdict": "pass", "findings": []}, "leaks": []},
        ]
    }
    n = ds.feed_voice_corrections(ws, result)
    check("two FAIL tells fed (warn + clean skipped)", n == 2)
    corr = ws / "_hq" / "voice" / "corrections-board-pack-assembler.jsonl"
    check("attributes to board-pack-assembler by filename", corr.exists())
    rows = [json.loads(l) for l in corr.read_text(encoding="utf-8").splitlines() if l.strip()]
    phrases = {r["original_draft"] for r in rows}
    check("banned phrases captured", "I'd be happy to" in phrases and "Best regards" in phrases)
    check("corrected is empty (no user rewrite)", all(r["corrected_by_user"] == "" for r in rows))
    check("privacy leak NOT fed as a voice correction",
          all("project_007" not in r["original_draft"] for r in rows))
    # Idempotent: re-running dedupes on (skill, original, corrected).
    n2 = ds.feed_voice_corrections(ws, result)
    check("re-feed dedupes to zero new rows", n2 == 0)


def test_feed_never_edits_user_file():
    ws = Path(tempfile.mkdtemp(prefix="qwa2_"))
    doc = ws / "memo.docx"
    doc.write_text("original bytes", encoding="utf-8")
    before = doc.read_text(encoding="utf-8")
    ds.feed_voice_corrections(ws, {"flagged": [
        {"path": str(doc), "voice": {"verdict": "fail", "findings": [
            {"rule": "x", "severity": "fail", "line": "Hope this finds you well",
             "match": "Hope this finds you well"}]}, "leaks": []}]})
    check("user file untouched (flag-only)", doc.read_text(encoding="utf-8") == before)


# --- Quick Win B ------------------------------------------------------------

def _ws_with_person():
    ws = Path(tempfile.mkdtemp(prefix="qwb_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    entities = {"people": [
        {"id": "person_001", "canonical_name": "Dana Doe", "first_seen": "2026-01-01"}]}
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def test_effective_baseline():
    check("override widens computed", dz.effective_baseline(21, 45) == 45)
    check("computed wins when wider", dz.effective_baseline(60, 45) == 60)
    check("None-safe", dz.effective_baseline(None, None) is None)
    check("override alone", dz.effective_baseline(None, 30) == 30)


def test_record_just_busy_persists_and_widens():
    ws = _ws_with_person()
    v1 = dz.record_just_busy(ws, "person_001", 40)
    check("first just-busy sets override to gap", v1 == 40)
    rec = next(p for p in json.loads(
        (ws / "_hq" / "data" / "entities.json").read_text(encoding="utf-8"))["people"]
        if p["id"] == "person_001")
    check("override persisted on record", dz.cadence_override_days(rec) == 40)
    # A smaller gap does NOT shrink the baseline; a larger one widens it.
    check("smaller gap does not shrink", dz.record_just_busy(ws, "person_001", 20) == 40)
    check("larger gap widens", dz.record_just_busy(ws, "person_001", 55) == 55)
    check("unknown person → None", dz.record_just_busy(ws, "person_404", 30) is None)


def test_cadence_field_allowed():
    check("cadence_override_days is an allowed person field",
          "cadence_override_days" in pw.ALLOWED_PERSON_FIELDS)
    check("legacy record reads None", dz.cadence_override_days({"id": "person_9"}) is None)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_feed_voice_corrections()
    test_feed_never_edits_user_file()
    test_effective_baseline()
    test_record_just_busy_persists_and_widens()
    test_cadence_field_allowed()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL learning quick-win tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
