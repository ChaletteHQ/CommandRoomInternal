#!/usr/bin/env python3
"""RRF1 — stale "counterparty 'X' has no person record" review_reason
clauses refresh AT RENDER TIME once X's person record exists.

`review_reason` is stamped once by capture_gate and rendered verbatim
forever — a commitment captured while its counterparty had no person record
kept telling the CEO to add a record they already added. The fix is a
render-time overlay (`surface_drivers._display_review_reason`) at the three
review_reason render sites; the STORED value is a gating input (cru_match /
commitment_dedup / confirm_flow) and is NEVER rewritten.

Asserts:
  - captured-stale then person added: the rendered tag shows
    "'X' — contact added ✓" (UXC1 plain copy) at ALL THREE render sites — the
    triage escalation pin, the triage age-section row, and the waiting-on
    confirm tail. (D1 pinned decision: the clause is REPLACED, never
    dropped — a lone clause still renders, no dangling " · ".)
  - person still absent: the UXC1 plain rewrite ("'X' isn't in your
    contacts yet") — the stored "counterparty" wording never renders.
  - compound reason string: ONLY the matching clause is rewritten; sibling
    clauses ("no resolved owner") pass through untouched.
  - display-only on disk: events.jsonl is byte-identical across every
    render, and the projected open set still carries the ORIGINAL stored
    review_reason.
  - D3 read fence: one resolve_all call per distinct name per render pass
    (memoized), and a reason with no eligible clause never hits the resolver.

G14: every fixture timestamp is computed relative to today. Placeholder
names only (Sam / Bo / Dana Sample, Rex Holt). House convention: non-zero
exit = fail.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import surface_drivers as sd  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  ok   {label}")


def _ago(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


STALE_DANA = "counterparty 'Dana Sample' has no person record"
STALE_REX = "counterparty 'Rex Holt' has no person record"
REFRESHED_DANA = "'Dana Sample' — contact added ✓"  # UXC1 plain copy
PLAIN_REX = "'Rex Holt' isn't in your contacts yet"  # UXC1 unresolved copy
PLAIN_DANA = "'Dana Sample' isn't in your contacts yet"  # UXC1 unresolved copy
COMPOUND = STALE_DANA + "; no resolved owner"


def _entities(with_dana: bool) -> dict:
    people = [
        {"id": "person:user", "canonical_name": "Sam Sample",
         "is_primary_user": True},
        {"id": "person:bo", "canonical_name": "Bo Sample",
         "emails": ["bo@example.com"]},
    ]
    if with_dana:
        # The record the CEO adds AFTER capture — the stored review_reason
        # stays frozen; only the render must notice.
        people.append({"id": "person:dana", "canonical_name": "Dana Sample"})
    return {"people": people, "orgs": [], "version": 1}


def _workspace() -> Path:
    """Real-substrate-shaped fixtures (real-data fixture gotcha): commitment
    events exactly as capture_gate stamps them — an unresolved counterparty
    carries counterparty_name (no id), pending_review True, and the
    `;`-joined review_reason."""
    d = Path(tempfile.mkdtemp(prefix="rrf1_"))
    (d / "_hq" / "data").mkdir(parents=True)
    (d / "_hq" / "data" / "entities.json").write_text(
        json.dumps(_entities(with_dana=False)), encoding="utf-8")
    rows = [
        # 10 days unconfirmed -> the triage escalation pin (site 1). Owner
        # resolved, counterparty not -> the single-clause reason shape.
        {"type": "commitment", "seq": 1, "ts": _ago(10),
         "source_skill": "meeting-notes",
         "data": {"id": "c_pin", "title": "Dana signs the SOW",
                  "kind": "promise", "owner_id": "person:bo",
                  "counterparty_name": "Dana Sample",
                  "pending_review": True, "review_reason": STALE_DANA}},
        # 2 days -> below the 7-day pin threshold: the triage age-section
        # row (site 2) + the waiting-on confirm tail (site 3). No owner ->
        # capture_gate's real compound reason for a promise.
        {"type": "commitment", "seq": 2, "ts": _ago(2),
         "source_skill": "meeting-notes",
         "data": {"id": "c_fresh", "title": "Dana sends the redlines",
                  "kind": "promise",
                  "counterparty_name": "Dana Sample",
                  "pending_review": True, "review_reason": COMPOUND}},
        # Counterparty whose record is NEVER added -> clause must render
        # verbatim in both phases.
        {"type": "commitment", "seq": 3, "ts": _ago(3),
         "source_skill": "meeting-notes",
         "data": {"id": "c_absent", "title": "Rex returns the contract",
                  "kind": "promise", "owner_id": "person:bo",
                  "counterparty_name": "Rex Holt",
                  "pending_review": True, "review_reason": STALE_REX}},
    ]
    with (d / "_hq" / "data" / "events.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return d


def _tags_by_id(view: dict) -> dict:
    out = {}
    for sec in view["sections"]:
        for item in sec["items"]:
            out[item["n"]] = (sec["title"], item["context_tag"])
    return out


def main() -> int:
    ws = _workspace()
    events_path = ws / "_hq" / "data" / "events.jsonl"
    bytes_before = events_path.read_bytes()

    print("Phase 1 — person record still absent: UXC1 plain not-in-contacts copy")
    triage = _tags_by_id(sd.build_commitment_triage_view(str(ws)))
    waiting = _tags_by_id(sd.build_waiting_on_view(str(ws)))

    check("c_pin lands in the escalation pin block",
          triage.get("c_pin", ("",))[0] == "Unconfirmed",
          f"got {triage.get('c_pin')}")
    check("pin row renders the plain not-in-contacts copy while Dana is absent",
          PLAIN_DANA in triage.get("c_pin", ("", ""))[1])
    check("age-section compound: matching clause plain, sibling verbatim",
          PLAIN_DANA in triage.get("c_fresh", ("", ""))[1]
          and "no resolved owner" in triage.get("c_fresh", ("", ""))[1],
          f"got {triage.get('c_fresh')}")
    check("waiting-on confirm tail renders the plain not-in-contacts copy",
          PLAIN_DANA in waiting.get("c_fresh", ("", ""))[1])
    check("no refresh copy appears while the record is absent",
          all(REFRESHED_DANA not in tag
              for _, tag in list(triage.values()) + list(waiting.values())))

    print("Phase 2 — Dana Sample's person record added: clause refreshes")
    (ws / "_hq" / "data" / "entities.json").write_text(
        json.dumps(_entities(with_dana=True)), encoding="utf-8")
    triage2 = _tags_by_id(sd.build_commitment_triage_view(str(ws)))
    waiting2 = _tags_by_id(sd.build_waiting_on_view(str(ws)))

    # Site 1 — triage escalation pin.
    check("site 1 (escalation pin) shows the refreshed clause",
          REFRESHED_DANA in triage2.get("c_pin", ("", ""))[1],
          f"got {triage2.get('c_pin')}")
    check("site 1 no longer shows the stale clause",
          STALE_DANA not in triage2.get("c_pin", ("", ""))[1])
    # D1 pinned decision: REPLACED, never dropped — the tag still carries a
    # counterparty clause (no dangling separator, no vanished context).
    check("site 1 lone clause is replaced, not dropped",
          "'Dana Sample'" in triage2.get("c_pin", ("", ""))[1])

    # Site 2 — triage age-section row; compound: ONLY the matching clause.
    check("site 2 (age section) shows the refreshed clause",
          REFRESHED_DANA in triage2.get("c_fresh", ("", ""))[1],
          f"got {triage2.get('c_fresh')}")
    check("site 2 sibling clause passes through untouched",
          "no resolved owner" in triage2.get("c_fresh", ("", ""))[1])
    check("site 2 no longer shows the stale clause",
          STALE_DANA not in triage2.get("c_fresh", ("", ""))[1])

    # Site 3 — waiting-on confirm tail.
    check("site 3 (confirm tail) shows the refreshed clause",
          REFRESHED_DANA in waiting2.get("c_fresh", ("", ""))[1],
          f"got {waiting2.get('c_fresh')}")
    check("site 3 sibling clause passes through untouched",
          "no resolved owner" in waiting2.get("c_fresh", ("", ""))[1])

    # Still-absent name: the UXC1 plain rewrite everywhere, both phases —
    # the stored "counterparty ..." wording never reaches a render.
    for name, tags in (("triage", triage2), ("waiting-on", waiting2)):
        check(f"{name}: absent name renders the plain not-in-contacts copy",
              PLAIN_REX in tags.get("c_absent", ("", ""))[1],
              f"got {tags.get('c_absent')}")
        check(f"{name}: absent name never marked added",
              "contact added" not in tags.get("c_absent", ("", ""))[1])
        check(f"{name}: banned word 'counterparty' absent from the render",
              "counterparty" not in tags.get("c_absent", ("", ""))[1])

    print("Display-only pin — stored substrate byte-identical")
    check("events.jsonl bytes unchanged across all four renders",
          events_path.read_bytes() == bytes_before)
    from cru_match import load_open_commitments
    stored = {(ev.get("data") or {}).get("id"):
              (ev.get("data") or {}).get("review_reason")
              for ev in load_open_commitments(events_path)}
    check("projected open set still carries the ORIGINAL review_reason",
          stored == {"c_pin": STALE_DANA, "c_fresh": COMPOUND,
                     "c_absent": STALE_REX},
          f"got {stored}")

    print("D3 read fence — memoized resolution, lazy resolver")
    import entity_resolve
    calls: list[str] = []
    real = entity_resolve.resolve_all

    def counting(ws_root, query, **kw):
        calls.append(query)
        return real(ws_root, query, **kw)

    entity_resolve.resolve_all = counting
    try:
        cache: dict = {}
        for _ in range(5):  # five rows, one distinct name
            sd._display_review_reason(ws, STALE_DANA, cache)
            sd._display_review_reason(ws, COMPOUND, cache)
        check("one resolve per distinct name per render pass",
              calls == ["Dana Sample"], f"got {calls}")
        calls.clear()
        out = sd._display_review_reason(ws, "extraction confidence 0.4 "
                                        "below threshold", cache)
        check("ineligible reason never touches the resolver",
              calls == [] and out == "extraction confidence 0.4 "
                                     "below threshold")
    finally:
        entity_resolve.resolve_all = real

    print("Degrade — resolver failure never breaks the render")
    corrupt = Path(tempfile.mkdtemp(prefix="rrf1_corrupt_"))
    (corrupt / "_hq" / "data").mkdir(parents=True)
    (corrupt / "_hq" / "data" / "entities.json").write_text(
        "{not json", encoding="utf-8")
    check("corrupt entities.json degrades to the plain capture-time truth",
          sd._display_review_reason(corrupt, STALE_DANA, {}) == PLAIN_DANA)

    print(f"\n{checks} checks, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
