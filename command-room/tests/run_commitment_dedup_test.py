#!/usr/bin/env python3
"""
v4.6.0 C4 — cross-writer semantic dedup + merge-by-supersession.

THE BUG CLASS (SPEC_V452 addendum · C4): the capture dedup key is
`(source_ref, title)` — SOURCE-SCOPED — so the same real-world commitment
captured from a meeting transcript (granola:X), a follow-up email (gmail:Y),
and the nightly sweep (session:Z) is three open items, structurally. The
2026-07 dogfood hit it live (the Joe/positioning commitments, F-31 → F-46
window). And the closure half was a DEAD PATH: `commitment_superseded` was
honored as a closer by the loader since v3.14.5 with no writer emitting it.

WHAT THIS SUITE PINS
  1. Triple-capture (meeting + email + sweep): the first lands clean; the
     second and third land FLAGGED (pending_review + suspected_duplicate_of)
     — never silently dropped, never silently merged.
  2. Merge: supersede_commitment closes the duplicate; the survivor carries
     the provenance union (merged_source_refs / merged_from) read-side.
  3. A NON-duplicate near-miss (same person, different deliverable) stays
     unflagged — precision over recall; a false merge-ask erodes trust.
  4. Guards: owner-conflict veto, time window, uncorroborated tier,
     self-merge / unknown-id / pending_review-floor errors, idempotency.
  5. Fail-open: a similarity-check crash appends the batch UNFLAGGED —
     flagging must never lose a capture. CR_DEDUP_CHECK=0 disables.

Fixture shapes mirror the REAL F-31/F-46 substrate (per the realdata-fixture
gotcha): the sweep side is field-poor (kind task, no owner/counterparty,
person named in the title with the RAW spelling "Michelle"), the meeting side
is field-rich (resolved ids, due date, the canonical "Michele").
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent / "shared" / "scripts"))

import commitment_dedup as cd  # noqa: E402
from commitment_state import (  # noqa: E402
    CommitmentIdError,
    PendingReviewError,
    close_commitment,
    supersede_commitment,
)
from cru_match import load_open_commitments  # noqa: E402
from event_gate import append_event  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="c4_dedup_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    return ws


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _events(ws: Path) -> list[dict]:
    p = _events_path(ws)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


NOW = datetime.datetime(2026, 7, 8, 20, 0, tzinfo=datetime.timezone.utc)

# The real F-31 sweep capture shape: field-poor, raw name spelling in title.
SWEEP_TITLE = ("Send the positioning briefs + collected feedback to messaging "
               "collaborator before tomorrow's call (unblocks Michelle one-pager).")
# The meeting writer's capture of the SAME commitment: field-rich (F-46 class).
MEETING_DATA = {
    "kind": "promise", "id": "cmt_MEETING",
    "title": "Send positioning briefs and collected feedback to Michele before the call",
    "owner_id": "person_001", "counterparty_id": "person_017",
    "counterparty_name": "Michele Jewett",
    "due": "2026-07-08", "source_ref": "granola:m1",
}


def _build_triple_capture_ws() -> Path:
    """Meeting + email + sweep captures of ONE real commitment (setup shared
    by the flag test and the merge test)."""
    ws = _ws()
    ep = _events_path(ws)
    # 1st capture (meeting writer).
    append_event(ep, [{"type": "commitment", "source_skill": "meeting-notes",
                       "primary_thread_id": "project_015",
                       "data": dict(MEETING_DATA)}], holder="meeting-notes")
    # 2nd capture (follow-up email scan) — gmail source, raw name spelling.
    append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                       "data": {"kind": "promise", "id": "cmt_EMAIL",
                                "title": "Send Michelle the positioning briefs and collected feedback",
                                "owner_id": "person_001",
                                "counterparty_name": "Michelle",
                                "due": "2026-07-08", "source_ref": "gmail:g1"}}],
                 holder="scan-for-commitments")
    # 3rd capture (nightly sweep) — the F-31 field-poor shape, via the
    # sweep's own write path (one write path — C1).
    import session_sweep as ss
    ss.sweep_and_receipt(
        ws,
        [{"session_id": "sess_f27", "type": "commitment", "summary": SWEEP_TITLE,
          "data": {"kind": "task", "no_due": True}}],
        sessions_scanned=1, source_skill="session-sweep", fired_via="manual",
    )
    return ws


def test_triple_capture_flags_second_and_third():
    print("test_triple_capture_flags_second_and_third — meeting + email + sweep")
    ws = _build_triple_capture_ws()

    first = _events(ws)[0]["data"]
    check("first capture lands unflagged", not first.get("pending_review")
          and "suspected_duplicate_of" not in first)

    second = _events(ws)[1]["data"]
    check("second capture LANDS (not dropped)", second.get("id") == "cmt_EMAIL")
    check("second capture flagged pending_review", second.get("pending_review") is True)
    check("second capture points at the first",
          second.get("suspected_duplicate_of") == "cmt_MEETING",
          repr(second.get("suspected_duplicate_of")))
    check("second capture carries a score",
          isinstance(second.get("suspected_duplicate_score"), (int, float)))
    check("review_reason says duplicate",
          "duplicate" in (second.get("review_reason") or ""))

    commits = [e for e in _events(ws) if e.get("type") == "commitment"]
    check("sweep item landed", len(commits) == 3, f"{len(commits)} commitments")
    third = commits[2]["data"]
    check("sweep capture flagged pending_review", third.get("pending_review") is True)
    check("sweep capture points at an existing open item",
          third.get("suspected_duplicate_of") in ("cmt_MEETING", "cmt_EMAIL"),
          repr(third.get("suspected_duplicate_of")))

    # Structural honesty: all three are OPEN (nothing auto-closed).
    opens = load_open_commitments(_events_path(ws))
    check("nothing auto-closed — 3 open until a human merges", len(opens) == 3,
          f"{len(opens)} open")


def test_merge_supersedes_and_carries_provenance():
    print("test_merge_supersedes_and_carries_provenance")
    ws = _build_triple_capture_ws()
    ep = _events_path(ws)

    # Merging a FLAGGED (pending_review) item without user confirmation must
    # refuse — a merge adjudicates the suspect.
    try:
        supersede_commitment(ws, "cmt_MEETING", "cmt_EMAIL",
                             merged_by="person_001", source_skill="commitment-triage")
        check("pending_review merge without user_confirmed refused", False)
    except PendingReviewError:
        check("pending_review merge without user_confirmed refused", True)

    r = supersede_commitment(ws, "cmt_MEETING", "cmt_EMAIL",
                             merged_by="person_001", source_skill="commitment-triage",
                             evidence="user merged in triage", user_confirmed=True)
    check("merge returns superseded", r["status"] == "superseded")
    check("merge names both sides",
          r["commitment_id"] == "cmt_EMAIL" and r["survivor_id"] == "cmt_MEETING")

    sup = [e for e in _events(ws) if e.get("type") == "commitment_superseded"]
    check("ONE commitment_superseded event on disk", len(sup) == 1)
    d = sup[0]["data"]
    check("closer references the superseded item", d.get("commitment_id") == "cmt_EMAIL")
    check("event names the survivor", d.get("superseded_by") == "cmt_MEETING")
    check("resolution is duplicate (historic vocabulary restored)",
          d.get("resolution") == "duplicate")
    check("provenance union on the event",
          set(d.get("merged_source_refs") or []) == {"granola:m1", "gmail:g1"},
          repr(d.get("merged_source_refs")))

    opens = load_open_commitments(ep)
    ids = {c["data"]["id"] for c in opens}
    check("superseded item is CLOSED read-side", "cmt_EMAIL" not in ids)
    check("survivor still open", "cmt_MEETING" in ids)
    survivor = next(c for c in opens if c["data"]["id"] == "cmt_MEETING")
    check("survivor carries the absorbed source (fold)",
          survivor["data"].get("merged_source_refs") == ["gmail:g1"],
          repr(survivor["data"].get("merged_source_refs")))
    check("survivor names what it absorbed",
          survivor["data"].get("merged_from") == ["cmt_EMAIL"])
    check("survivor's ON-DISK event untouched (append-only)",
          "merged_source_refs" not in _events(ws)[0]["data"])

    # Idempotency: re-merging acks honestly, writes nothing.
    n_before = len(_events(ws))
    r2 = supersede_commitment(ws, "cmt_MEETING", "cmt_EMAIL",
                              merged_by="person_001", source_skill="commitment-triage",
                              user_confirmed=True)
    check("re-merge is a no-op ack", r2["status"] == "already_resolved")
    check("re-merge appended nothing", len(_events(ws)) == n_before)

    # Second merge onto the same survivor accumulates provenance.
    sweep_id = [e["data"]["id"] for e in _events(ws)
                if e.get("type") == "commitment"
                and e["data"].get("source_ref", "").startswith("session:")][0]
    supersede_commitment(ws, "cmt_MEETING", sweep_id,
                         merged_by="person_001", source_skill="commitment-triage",
                         user_confirmed=True)
    opens = load_open_commitments(ep)
    check("only the survivor remains open after both merges", len(opens) == 1)
    survivor = opens[0]
    check("fold accumulates across merges",
          set(survivor["data"].get("merged_source_refs") or []) ==
          {"gmail:g1", "session:sess_f27"},
          repr(survivor["data"].get("merged_source_refs")))
    check("merged_from accumulates",
          set(survivor["data"].get("merged_from") or []) == {"cmt_EMAIL", sweep_id})


def test_near_miss_stays_unflagged():
    print("test_near_miss_stays_unflagged — same person, different deliverable")
    ws = _ws()
    ep = _events_path(ws)
    append_event(ep, [{"type": "commitment", "source_skill": "meeting-notes",
                       "data": dict(MEETING_DATA)}], holder="meeting-notes")
    # Same owner, same counterparty, DIFFERENT deliverable.
    append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                       "data": {"kind": "promise", "id": "cmt_INVOICE",
                                "title": "Send Michele the June invoice",
                                "owner_id": "person_001",
                                "counterparty_id": "person_017",
                                "counterparty_name": "Michele Jewett",
                                "due": "2026-07-10", "source_ref": "gmail:g7"}}],
                 holder="scan-for-commitments")
    d = _events(ws)[1]["data"]
    check("near-miss NOT flagged", not d.get("pending_review")
          and "suspected_duplicate_of" not in d, repr(d.get("review_reason")))

    # Related-but-distinct work item for the same deliverable family: the
    # verb differs, overlap sits under the strong bar.
    append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                       "data": {"kind": "promise", "id": "cmt_REVIEW",
                                "title": "Review Michele's feedback on the positioning briefs and send notes",
                                "owner_id": "person_001",
                                "counterparty_id": "person_017",
                                "due": "2026-07-09", "source_ref": "gmail:g8"}}],
                 holder="scan-for-commitments")
    d2 = _events(ws)[2]["data"]
    check("adjacent-work item NOT flagged", "suspected_duplicate_of" not in d2,
          repr(d2.get("suspected_duplicate_score")))


def test_conservative_gates():
    print("test_conservative_gates — owner veto, window, uncorroborated tier")
    # Owner conflict vetoes even a verbatim title.
    open_ev = {"type": "commitment", "seq": 5, "ts": "2026-07-08T01:00:00+00:00",
               "data": {"kind": "promise", "id": "cmt_A",
                        "title": "Send the launch checklist to Brandon",
                        "owner_id": "person_001", "counterparty_name": "Brandon",
                        "source_ref": "granola:x"}}
    other_owner = {"kind": "promise", "title": "Send the launch checklist to Brandon",
                   "owner_id": "person_099", "counterparty_name": "Brandon"}
    check("different resolved owners veto",
          cd.find_suspected_duplicate(other_owner, [open_ev], now_dt=NOW) is None)

    # Window: an open item older than DUP_WINDOW_DAYS is not a candidate.
    same = {"kind": "promise", "title": "Send the launch checklist to Brandon",
            "owner_id": "person_001", "counterparty_name": "Brandon"}
    late = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
    check("outside the time window -> not a candidate",
          cd.find_suspected_duplicate(same, [open_ev], now_dt=late) is None)
    check("inside the window -> flagged",
          cd.find_suspected_duplicate(same, [open_ev], now_dt=NOW) is not None)

    # Uncorroborated tier: two bare self-owed tasks need near-verbatim titles.
    bare = {"type": "commitment", "seq": 6, "ts": "2026-07-08T01:00:00+00:00",
            "data": {"kind": "task", "id": "cmt_T",
                     "title": "Draft the Q3 pricing one-pager", "source_ref": "session:s"}}
    near_verbatim = {"kind": "task", "title": "Draft the Q3 pricing one-pager for review"}
    moderately = {"kind": "task", "title": "Draft the Q3 pricing analysis deck"}
    m = cd.find_suspected_duplicate(near_verbatim, [bare], now_dt=NOW)
    check("bare tasks: near-verbatim flags (uncorroborated tier)", m is not None)
    check("bare tasks: moderate overlap does NOT flag",
          cd.find_suspected_duplicate(moderately, [bare], now_dt=NOW) is None)

    # Counterparty disagreement (both ids resolved, different people) vetoes.
    other_cp = {"kind": "promise", "title": "Send the launch checklist to Brandon",
                "owner_id": "person_001", "counterparty_id": "person_222"}
    open_cp = {**open_ev, "data": {**open_ev["data"], "counterparty_id": "person_333"}}
    check("different resolved counterparties veto",
          cd.find_suspected_duplicate(other_cp, [open_cp], now_dt=NOW) is None)


def test_entities_expand_id_to_name():
    print("test_entities_expand_id_to_name — id-only writer vs name-only writer")
    ws = _ws()
    ep = _events_path(ws)
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "workspace": {}, "orgs": [], "threads": [],
        "people": [{"id": "person_017", "canonical_name": "Michele Jewett",
                    "aliases": ["Michelle"]}],
    }), encoding="utf-8")
    # Open item: counterparty ID only, no name anywhere (title names no one).
    append_event(ep, [{"type": "commitment", "source_skill": "past-meetings",
                       "data": {"kind": "promise", "id": "cmt_IDONLY",
                                "title": "Share the positioning one-pager draft for feedback",
                                "owner_id": "person_001", "counterparty_id": "person_017",
                                "source_ref": "granola:z1"}}], holder="past-meetings")
    # New capture: free-text name only.
    append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                       "data": {"kind": "promise", "id": "cmt_NAMEONLY",
                                "title": "Share the positioning one-pager draft with Michelle for feedback",
                                "owner_id": "person_001", "counterparty_name": "Michelle",
                                "no_due": True, "source_ref": "gmail:z2"}}],
                 holder="scan-for-commitments")
    d = _events(ws)[1]["data"]
    check("id-only vs name-only writers still meet (entities expansion)",
          d.get("suspected_duplicate_of") == "cmt_IDONLY",
          repr({k: d.get(k) for k in ("suspected_duplicate_of", "suspected_duplicate_score")}))


def test_flag_never_blocks_or_drops():
    print("test_flag_never_blocks_or_drops — fail-open + escape hatch + CRU safety")
    ws = _ws()
    ep = _events_path(ws)
    append_event(ep, [{"type": "commitment", "source_skill": "meeting-notes",
                       "data": dict(MEETING_DATA)}], holder="meeting-notes")

    # Fail-open: a crashing similarity check must append the batch unflagged.
    real_loader = cd.load_open_commitments
    cd.load_open_commitments = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                           "data": {"kind": "promise", "id": "cmt_CRASH",
                                    "title": "Send Michele the positioning briefs and collected feedback",
                                    "owner_id": "person_001", "counterparty_name": "Michele",
                                    "due": "2026-07-08", "source_ref": "gmail:crash"}}],
                     holder="scan-for-commitments")
    finally:
        cd.load_open_commitments = real_loader
    evs = _events(ws)
    check("capture survived the dedup crash", evs[1]["data"]["id"] == "cmt_CRASH")
    check("crash path leaves it unflagged (today's behavior)",
          "suspected_duplicate_of" not in evs[1]["data"])

    # Escape hatch: CR_DEDUP_CHECK=0 disables flagging entirely.
    os.environ["CR_DEDUP_CHECK"] = "0"
    try:
        append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                           "data": {"kind": "promise", "id": "cmt_OFF",
                                    "title": "Send Michele the positioning briefs and collected feedback",
                                    "owner_id": "person_001", "counterparty_name": "Michele",
                                    "due": "2026-07-08", "source_ref": "gmail:off"}}],
                     holder="scan-for-commitments")
    finally:
        del os.environ["CR_DEDUP_CHECK"]
    check("CR_DEDUP_CHECK=0 lands unflagged",
          "suspected_duplicate_of" not in _events(ws)[2]["data"])

    # Non-commitment batches take the fast path untouched.
    append_event(ep, [{"type": "pack_run", "source_skill": "session-sweep",
                       "data": {"task_id": "session-sweep", "status": "complete"}}],
                 holder="session-sweep")
    check("non-commitment append untouched", _events(ws)[3]["type"] == "pack_run")

    # CRU safety inversion holds: a FLAGGED suspect refuses to auto-close.
    append_event(ep, [{"type": "commitment", "source_skill": "scan-for-commitments",
                       "data": {"kind": "promise", "id": "cmt_FLAGGED",
                                "title": "Send Michele the positioning briefs and feedback",
                                "owner_id": "person_001", "counterparty_name": "Michele",
                                "due": "2026-07-08", "source_ref": "gmail:fl"}}],
                 holder="scan-for-commitments")
    d = _events(ws)[4]["data"]
    check("setup: this capture is flagged", d.get("pending_review") is True)
    try:
        close_commitment(ws, "cmt_FLAGGED", resolved_by="cru", evidence="auto",
                         source_skill="cru-test")
        check("flagged suspect refuses auto-close", False)
    except PendingReviewError:
        check("flagged suspect refuses auto-close", True)


def test_merge_guards():
    print("test_merge_guards — self-merge, unknown ids, closed survivor")
    ws = _ws()
    ep = _events_path(ws)
    append_event(ep, [
        {"type": "commitment", "source_skill": "t",
         "data": {"kind": "task", "id": "cmt_X", "title": "Prep the board pack",
                  "no_due": True, "source_ref": "session:a"}},
        {"type": "commitment", "source_skill": "t",
         "data": {"kind": "promise", "id": "cmt_Y", "title": "Ship the launch email to Dana",
                  "owner_id": "person_001", "counterparty_name": "Dana",
                  "due": "2026-08-01", "source_ref": "gmail:b"}},
    ], holder="t")

    try:
        supersede_commitment(ws, "cmt_X", "cmt_X", merged_by="p", source_skill="t",
                             user_confirmed=True)
        check("self-merge raises", False)
    except ValueError as e:
        check("self-merge raises", "same commitment" in str(e))

    try:
        supersede_commitment(ws, "cmt_X", "cmt_NOPE", merged_by="p", source_skill="t",
                             user_confirmed=True)
        check("unknown superseded id raises CommitmentIdError", False)
    except CommitmentIdError:
        check("unknown superseded id raises CommitmentIdError", True)

    try:
        supersede_commitment(ws, "cmt_NOPE", "cmt_X", merged_by="p", source_skill="t",
                             user_confirmed=True)
        check("unknown survivor id raises CommitmentIdError", False)
    except CommitmentIdError:
        check("unknown survivor id raises CommitmentIdError", True)

    # A CLOSED survivor is allowed — the duplicate of a done thing is done.
    close_commitment(ws, "cmt_Y", resolved_by="person_001", evidence="done",
                     source_skill="t", user_confirmed=True)
    r = supersede_commitment(ws, "cmt_Y", "cmt_X", merged_by="person_001",
                             source_skill="t", user_confirmed=True)
    check("merge into a closed survivor closes the duplicate",
          r["status"] == "superseded")
    check("both closed afterwards", len(load_open_commitments(ep)) == 0)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_triple_capture_flags_second_and_third()
    test_merge_supersedes_and_carries_provenance()
    test_near_miss_stays_unflagged()
    test_conservative_gates()
    test_entities_expand_id_to_name()
    test_flag_never_blocks_or_drops()
    test_merge_guards()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL commitment_dedup (C4) tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
