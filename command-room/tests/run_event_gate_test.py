#!/usr/bin/env python3
"""
Regression tests for the append_event() gatekeeper (Phase 1 Foundation F1).

Fixtures use REAL event shapes — the canonical commitment envelope from
shared/COMMITMENT_SCHEMA.md and the live-substrate closure shapes the
2026-07-01 lifecycle audit catalogued (291 id-less closures, 5 spellings of
one type, 1,138 null-seq events across every family). Covers:

  - cmt_<ulid> minting on commitment events (absent id only; format checked)
  - data.kind stamping ('promise' default) + enum validation (ratified kinds)
  - fail-loud rejection of id-less commitment_resolved via BOTH entries
    (append_event and the legacy atomic_append_jsonl path)
  - commitment_update -> commitment_updated normalization
  - schema-enum validation: strict via BOTH entries (Phase 4 2026-07-02);
    warn-only posture reachable only via explicit gate_events(strict_enum=False)
  - seq/ts auto-stamp still applies through append_event (extension, not a
    second append path)
  - caller dicts are never mutated
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from atomic_write import atomic_append_jsonl  # noqa: E402
from event_gate import (  # noqa: E402
    EventGateError,
    append_event,
    gate_events,
    new_commitment_id,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail="") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


def _tmp_events_path() -> Path:
    d = Path(tempfile.mkdtemp(prefix="event_gate_test_"))
    (d / "_hq" / "data").mkdir(parents=True)
    return d / "_hq" / "data" / "events.jsonl"


def _read(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# Canonical commitment envelope — real shape per shared/COMMITMENT_SCHEMA.md.
def _commitment_fixture(**data_overrides) -> dict:
    data = {
        "owner_id": "person_004",
        "title": "send updated pricing deck to Mira",
        "due": "2026-07-10",
        "status": "open",
        "kind": "promise",  # Stage D: producers classify at capture
        "source_event_seq": 138,
        "source_ref": "granola:abc123def456",
    }
    data.update(data_overrides)
    return {
        "ts": "2026-07-01T15:30:00Z",
        "type": "commitment",
        "source_skill": "meeting-notes",
        "primary_thread_id": "project_017",
        "related_thread_ids": [],
        "classification_confidence": 0.92,
        "person_ids": ["person_004", "person_011"],
        "data": data,
    }


CMT_ID_RE = re.compile(r"^cmt_[0-9A-HJKMNP-TV-Z]{26}$")


def test_commitment_id_minting():
    print("test_commitment_id_minting")
    path = _tmp_events_path()
    append_event(path, _commitment_fixture(), holder="test")
    ev = _read(path)[0]
    check("data.id minted", CMT_ID_RE.match(ev["data"].get("id") or ""),
          repr(ev["data"].get("id")))
    check("seq auto-stamped", ev.get("seq") == 1, repr(ev.get("seq")))
    check("ts preserved", ev.get("ts") == "2026-07-01T15:30:00Z")

    # Explicit id is respected — never re-minted.
    append_event(path, _commitment_fixture(id="cmt_01JZ7Y0000000000000000TEST"),
                 holder="test")
    ev2 = _read(path)[1]
    check("explicit id preserved",
          ev2["data"]["id"] == "cmt_01JZ7Y0000000000000000TEST")

    # v4.5.2 R1c — a NEW commitment may NOT claim an id shaped like a legacy
    # seq alias: closures spelling "commitment_seq_142" mean "the commitment
    # at seq 142", and an explicit id in that namespace would shadow them
    # (closures land on the wrong commitment). Read-side amnesty for HISTORIC
    # events is unchanged — this gate is write-time only.
    try:
        append_event(path, _commitment_fixture(id="commitment_seq_142"), holder="test")
        check("seq-alias-shaped explicit id rejected (R1c)", False,
              "gate accepted a seq-alias-shaped id")
    except EventGateError as e:
        check("seq-alias-shaped explicit id rejected (R1c)", "seq" in str(e))
    check("rejected id wrote nothing", len(_read(path)) == 2, len(_read(path)))

    check("new_commitment_id() format", CMT_ID_RE.match(new_commitment_id()))
    check("mint uniqueness",
          len({new_commitment_id() for _ in range(50)}) == 50)


def test_commitment_kind():
    print("test_commitment_kind")
    path = _tmp_events_path()
    # Stage D flip: kind is REQUIRED AT CAPTURE. Missing kind REJECTS through
    # the strict append_event() path...
    no_kind = _commitment_fixture()
    del no_kind["data"]["kind"]
    try:
        append_event(path, dict(no_kind), holder="test")
        check("missing kind rejected (append_event, Stage D flip)", False)
    except EventGateError:
        check("missing kind rejected (append_event, Stage D flip)", True)
    # ...and the legacy path rejects too (Phase 4 2026-07-02 — burn-in over).
    try:
        atomic_append_jsonl(path, dict(no_kind))
        check("missing kind rejected (legacy path, Phase 4 strict)", False)
    except EventGateError:
        check("missing kind rejected (legacy path, Phase 4 strict)", True)
    # The warn+stamp posture survives ONLY behind an explicit
    # strict_enum=False (controlled replay tooling).
    stamped = gate_events([dict(no_kind)], strict_enum=False)[0]
    check("explicit strict_enum=False replay path stamps promise",
          stamped["data"].get("kind") == "promise",
          repr(stamped["data"].get("kind")))

    # Provided valid kind respected.
    for kind in ("promise", "task", "scheduling", "agenda"):
        append_event(path, _commitment_fixture(kind=kind), holder="test")
    kinds = [e["data"]["kind"] for e in _read(path)]
    check("ratified kinds pass through",
          kinds == ["promise", "task", "scheduling", "agenda"], repr(kinds))

    # Invalid kind rejected at append time — on BOTH entries.
    bad = _commitment_fixture(kind="errand")
    try:
        append_event(path, bad, holder="test")
        check("invalid kind rejected (append_event)", False)
    except EventGateError:
        check("invalid kind rejected (append_event)", True)
    try:
        atomic_append_jsonl(path, bad)
        check("invalid kind rejected (legacy path)", False)
    except EventGateError:
        check("invalid kind rejected (legacy path)", True)
    check("rejected events not written", len(_read(path)) == 4)


def test_idless_commitment_resolved_fails_loud():
    print("test_idless_commitment_resolved_fails_loud")
    path = _tmp_events_path()
    # The EXACT live failure shape: a closure with prose evidence but no id —
    # 291 of these existed in the substrate when the gate shipped.
    idless = {
        "ts": "2026-07-01T18:00:00Z",
        "type": "commitment_resolved",
        "source_skill": "workspace-manager",
        "primary_thread_id": "project_017",
        "data": {
            "resolved_by": "person_004",
            "evidence": "Mira replied confirming receipt of the deck",
        },
    }
    for entry, fn in (("append_event", lambda: append_event(path, idless)),
                      ("legacy atomic_append_jsonl",
                       lambda: atomic_append_jsonl(path, idless))):
        try:
            fn()
            check(f"id-less closure rejected via {entry}", False)
        except EventGateError as e:
            check(f"id-less closure rejected via {entry}", True)
            check(f"error names the fix ({entry})", "commitment_id" in str(e))
    check("nothing written", not path.exists() or len(_read(path)) == 0)

    # Every readable id spelling passes — canonical, fallback, legacy, seq-alias.
    ok_shapes = [
        {"commitment_id": "cmt_01JZ7Y0000000000000000TEST"},
        {"commitment_id": "commitment_seq_142"},
        {"id": "cmt_01JZ7Y0000000000000000TEST"},
        {"target_id": "commitment_seq_9"},          # legacy, accepted
        {"commitment_seq": 86},                      # F3 amnesty alias
        {"source_event_seq": 52},                    # F3 amnesty alias
    ]
    for shape in ok_shapes:
        ev = {
            "ts": "2026-07-01T18:00:00Z",
            "type": "commitment_resolved",
            "source_skill": "log-resolution",
            "data": {"resolved_by": "person_001", **shape},
        }
        try:
            append_event(path, ev)
            check(f"closure with {list(shape)[0]} accepted", True)
        except EventGateError as e:
            check(f"closure with {list(shape)[0]} accepted", False, str(e))


def test_type_normalization():
    print("test_type_normalization")
    path = _tmp_events_path()
    drift = {
        "ts": "2026-07-01T12:00:00Z",
        "type": "commitment_update",  # known drift spelling
        "source_skill": "orchestrator-commitments",
        "data": {"commitment_id": "commitment_seq_142", "due": "2026-07-15"},  # DATE_GUARD_OK: event-shape validation only; the gate derives no status
    }
    atomic_append_jsonl(path, drift)
    ev = _read(path)[0]
    check("commitment_update normalized", ev["type"] == "commitment_updated",
          ev["type"])
    check("caller dict not mutated", drift["type"] == "commitment_update")


def test_enum_validation():
    print("test_enum_validation")
    path = _tmp_events_path()
    unknown = {"ts": "2026-07-01T12:00:00Z", "type": "apply_choices_done",
               "source_skill": "apply-choices", "data": {}}
    # Strict via append_event.
    try:
        append_event(path, unknown)
        check("unknown type rejected via append_event", False)
    except EventGateError as e:
        check("unknown type rejected via append_event", True)
        check("error points at registry", "EVENT_TYPES" in str(e))
    # Strict via the legacy path too (Phase 4 2026-07-02 — burn-in over).
    try:
        atomic_append_jsonl(path, unknown)
        check("unknown type rejected on legacy path (Phase 4 strict)", False)
    except EventGateError:
        check("unknown type rejected on legacy path (Phase 4 strict)", True)
    check("rejected unknown type not written",
          not path.exists() or len(_read(path)) == 0)
    # The full 2026-07 wave vocabulary is registered.
    wave = ["skill_customization_added", "skill_customization_removed",
            "skill_customization_updated", "skill_customization_reset",
            "skill_customization_review", "onboarding_seed_ingested",
            "schedule_config_healed", "late_fire", "pulse_run",
            "triage_feedback", "prep_feedback", "session_sweep_run",
            "session_backfill_run"]
    for t in wave:
        try:
            append_event(path, {"type": t, "source_skill": "test", "data": {}})
            ok = True
        except EventGateError:
            ok = False
        check(f"wave type registered: {t}", ok)


def test_no_second_append_path():
    print("test_no_second_append_path")
    # append_event must delegate to atomic_append_jsonl — same seq counter,
    # same lock, same file semantics, interleaved freely.
    path = _tmp_events_path()
    append_event(path, {"type": "note", "source_skill": "test", "data": {}})
    atomic_append_jsonl(path, {"type": "note", "source_skill": "test", "data": {}})
    append_event(path, _commitment_fixture())
    rows = _read(path)
    check("interleaved seq monotonic", [r["seq"] for r in rows] == [1, 2, 3],
          repr([r.get("seq") for r in rows]))
    check("all rows ts-stamped", all(r.get("ts") for r in rows))


def test_gate_events_pure():
    print("test_gate_events_pure")
    src = _commitment_fixture()  # carries kind (Stage D: producers classify at capture)
    out = gate_events([src], strict_enum=True)
    check("returns new dicts", out[0] is not src)
    check("source data untouched", "id" not in src["data"] and src["data"] is not out[0]["data"])
    check("gated copy minted", CMT_ID_RE.match(out[0]["data"]["id"]))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== event gate (append_event gatekeeper) ===")
    test_commitment_id_minting()
    test_commitment_kind()
    test_idless_commitment_resolved_fails_loud()
    test_type_normalization()
    test_enum_validation()
    test_no_second_append_path()
    test_gate_events_pure()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} event-gate checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
