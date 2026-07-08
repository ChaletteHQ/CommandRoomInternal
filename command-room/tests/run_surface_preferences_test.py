#!/usr/bin/env python3
"""Phase 6 Loop 2 — dismissal mining across BOTH event families + suppression store."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import surface_preferences as sp  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _ws(events):
    ws = Path(tempfile.mkdtemp(prefix="sfp_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    lines = []
    for i, e in enumerate(events, 1):
        e = dict(e)
        e.setdefault("seq", i)
        lines.append(json.dumps(e))
    (ws / "_hq" / "data" / "events.jsonl").write_text("\n".join(lines) + "\n",
                                                      encoding="utf-8")
    return ws


def test_fingerprint_stable():
    a = sp.dismissal_fingerprint("commitments", "chase", "person_9")
    b = sp.dismissal_fingerprint("Commitments", "Chase", "PERSON_9")
    check("fingerprint is case-insensitive-stable", a == b)
    check("different entity → different fp",
          a != sp.dismissal_fingerprint("commitments", "chase", "person_8"))


def test_reads_both_families():
    fp = sp.dismissal_fingerprint("commitments", "chase", "person_9")
    events = []
    # 2 chat_dismissal + 1 dont_forget_feedback about DIFFERENT surfaces, but
    # Loop 2 keys on the explicit fingerprint when present.
    for i in range(2):
        events.append({"ts": _iso(i), "type": "chat_dismissal",
                       "source_skill": "commitments",
                       "data": {"surface": "commitments", "item_class": "chase",
                                "entity_id": "person_9", "fingerprint": fp}})
    events.append({"ts": _iso(3), "type": "dont_forget_feedback",
                   "source_skill": "pulse",
                   "data": {"person_id": "person_9", "feedback": "just_busy",
                            "surface": "commitments", "item_class": "chase",
                            "fingerprint": fp}})
    ws = _ws(events)
    rows = sp.load_dismissals(ws, since_iso=_iso(30))
    check("reads BOTH families", len(rows) == 3)
    check("both source types present",
          {r["source_type"] for r in rows} == {"chat_dismissal", "dont_forget_feedback"})
    counts = sp.count_repeats(rows, min_count=3)
    check("3 repeats crosses the floor", fp in counts and counts[fp]["count"] == 3)


def test_legacy_derivation():
    # A legacy dont_forget_feedback with NO fingerprint field derives one.
    ev = {"ts": _iso(1), "type": "dont_forget_feedback", "source_skill": "pulse",
          "data": {"person_id": "person_5", "feedback": "expected"}}
    norm = sp.normalize_dismissal(ev)
    check("legacy dont_forget derives fingerprint", norm is not None and norm["fingerprint"])
    check("legacy derives pulse surface + dormancy class",
          norm["surface"] == "pulse" and norm["item_class"] == "dormancy")
    # A bare legacy chat_dismissal keyed only to a per-day seq → skipped (not a
    # stable pattern).
    bare = {"ts": _iso(1), "type": "chat_dismissal", "source_skill": "inbox-triage",
            "data": {"target_id": 12345}}
    check("bare seq-only dismissal is skipped", sp.normalize_dismissal(bare) is None)


def test_below_floor_no_count():
    fp = sp.dismissal_fingerprint("pulse", "dormancy", "person_1")
    rows = [{"surface": "pulse", "item_class": "dormancy", "entity_id": "person_1",
             "fingerprint": fp, "ts": _iso(i)} for i in range(2)]
    check("2 dismissals below the 3-floor", sp.count_repeats(rows, min_count=3) == {})


def test_propose_and_cooldown():
    fp = sp.dismissal_fingerprint("commitments", "chase", "person_9")
    counts = {fp: {"count": 6, "latest_ts": _iso(0), "surface": "commitments",
                   "item_class": "chase", "entity_id": "person_9"}}
    props = sp.propose_suppressions(counts, entity_names={"person_9": "Dana"})
    check("proposes a suppression", len(props) == 1)
    check("plain text names the person + surface",
          "Dana" in props[0]["plain"] and "chas" in props[0]["plain"].lower())
    check("cooldown suppresses",
          sp.propose_suppressions(counts, cooldown_fingerprints={fp}) == [])
    existing = [{"fingerprint": fp}]
    check("existing suppression not re-proposed",
          sp.propose_suppressions(counts, existing_prefs=existing) == [])


def test_store_and_is_suppressed():
    ws = Path(tempfile.mkdtemp(prefix="sfpstore_"))
    check("missing store reads empty",
          sp.load_surface_preferences(ws) == {"version": 1, "suppressions": []})
    prop = {"fingerprint": "sfp_a", "surface": "commitments", "item_class": "chase",
            "entity_id": "person_9", "count": 6, "plain": "p"}
    store = sp.load_surface_preferences(ws)
    store["suppressions"].append(sp.suppression_from_proposal(prop, added_ts=_iso(0)))
    sp.write_surface_preferences(ws, store)
    prefs = sp.load_surface_preferences(ws)
    check("exact match suppressed",
          sp.is_suppressed(prefs, "commitments", "chase", "person_9") is True)
    check("different person NOT suppressed",
          sp.is_suppressed(prefs, "commitments", "chase", "person_8") is False)
    check("different surface NOT suppressed",
          sp.is_suppressed(prefs, "pulse", "chase", "person_9") is False)


def test_wildcards():
    prefs = {"suppressions": [
        {"surface": "*", "item_class": "stale_project", "entity_id": "project_7",
         "mode": "suppress", "fingerprint": "sfp_b"}]}
    check("surface wildcard matches any surface",
          sp.is_suppressed(prefs, "pulse", "stale_project", "project_7"))
    check("class-wide (null entity) matches",
          sp.is_suppressed({"suppressions": [
              {"surface": "inbox", "item_class": "newsletter", "entity_id": None,
               "mode": "suppress"}]}, "inbox", "newsletter", "anyone"))
    check("empty prefs never suppress", sp.is_suppressed({}, "inbox", "x", "y") is False)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_fingerprint_stable()
    test_reads_both_families()
    test_legacy_derivation()
    test_below_floor_no_count()
    test_propose_and_cooldown()
    test_store_and_is_suppressed()
    test_wildcards()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL surface_preferences tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
