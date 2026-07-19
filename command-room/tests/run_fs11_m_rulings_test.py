#!/usr/bin/env python3
"""FS-11 M rulings (2026-07-15) — auto-close moderate matches, person auto-add
with observed-provenance email capture, email queue-on-click posture.

  5a: reconcile_sent auto-closes UNAMBIGUOUS moderate (pending_review) matches;
      only multi-candidate ambiguity (one send → >1 commitment) stays a confirm
      proposal; ambiguous review proposals carry a TTL that expires.
  5b: people_writer.auto_add_person runs the same-name dedup gate first, stores
      an email ONLY with observed provenance (never a guess), undo = archive.
  5c: email-writer posture is queue-on-click (auto-queue-on-render retired), and
      the queue-on-click wording is carried to the client CLAUDE.md template +
      the update-bridge migration.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import sys
import json
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    # ================= 5a: auto-close moderate matches =====================
    import reconcile_sent_commitments as rsc
    check("AUTO_CLOSE_MODERATE is on (M ruling)", rsc.AUTO_CLOSE_MODERATE is True)

    opens = [
        {"seq": 10, "type": "commitment", "data": {"id": "cmt_a", "status": "open",
         "owner_id": "user", "title": "Send Bob the deck"}},
        {"seq": 11, "type": "commitment", "data": {"id": "cmt_b", "status": "open",
         "owner_id": "user", "title": "Email Jane the notes"}},
        {"seq": 12, "type": "commitment", "data": {"id": "cmt_c", "status": "open",
         "owner_id": "user", "title": "Ping Sam"}},
    ]

    # Unambiguous: message m1 → cmt_a (pending_review), 1:1 → should auto-close.
    orig = rsc.match_send_to_commitments

    def fake_match(*, open_commitments, sender_person_id, recipient_person_ids,
                   subject, body, recipient_names=None):
        # deterministic per-subject mapping
        table = {
            "deck": [("cmt_a", "pending_review", 0.42)],
            # ambiguous: one send matches two commitments at pending grade
            "both": [("cmt_b", "pending_review", 0.40), ("cmt_c", "pending_review", 0.38)],
        }
        out = []
        for cid, rec, score in table.get(subject, []):
            out.append({"commitment_id": cid, "recommendation": rec,
                        "score": score, "title": cid, "owner_id": "user",
                        "primary_thread_id": ""})
        return out

    rsc.match_send_to_commitments = fake_match
    try:
        res = rsc.reconcile_sent(
            opens,
            [{"message_id": "m1", "ts": "2026-07-10T09:00:00", "subject": "deck",
              "body": "here"}],
            user_person_id="user")
        auto_ids = {c["commitment_id"] for c in res["auto_close"]}
        pending_ids = {c["commitment_id"] for c in res["pending"]}
        check("unambiguous moderate match auto-closes (FS-11)", "cmt_a" in auto_ids)
        moderate = [c for c in res["auto_close"] if c["commitment_id"] == "cmt_a"]
        check("promoted moderate close is flagged + narrated honestly",
              moderate and moderate[0].get("moderate") is True
              and "probably handled" in (moderate[0].get("evidence") or ""))
        check("unambiguous moderate is NOT left pending", "cmt_a" not in pending_ids)

        # Ambiguous: one send → two commitments at pending → both stay pending.
        res2 = rsc.reconcile_sent(
            opens,
            [{"message_id": "m2", "ts": "2026-07-10T10:00:00", "subject": "both",
              "body": "x"}],
            user_person_id="user")
        auto2 = {c["commitment_id"] for c in res2["auto_close"]}
        pend2 = {c["commitment_id"] for c in res2["pending"]}
        check("multi-candidate ambiguity stays confirm (not auto-closed)",
              "cmt_b" not in auto2 and "cmt_c" not in auto2)
        check("ambiguous candidates land in pending", {"cmt_b", "cmt_c"} <= pend2)
    finally:
        rsc.match_send_to_commitments = orig

    # TTL expiry on ambiguous review proposals
    import brain_proposals as bp
    d = tempfile.mkdtemp(); os.makedirs(os.path.join(d, "_hq", "data"))
    ep = os.path.join(d, "_hq", "data", "events.jsonl")
    # G14: dates are computed relative to today. The old fixture hardcoded
    # "2026-07-14" for the FRESH proposal against a 14-day TTL — a bomb that
    # would silently expire (flipping this check red) once the wall clock
    # passed 2026-07-28, for reasons having nothing to do with the code.
    # G14 itself does not catch this shape: it scans for TODAY-OR-FUTURE
    # literals, and a past date that ages OUT of a window is neither.
    import datetime as _dt

    def _ago(days):
        return (_dt.datetime.now(_dt.timezone.utc)
                - _dt.timedelta(days=days)).isoformat()

    with open(ep, "w", encoding="utf-8") as f:
        # FB-19: real substrate always has the commitment behind the review —
        # the reconciler only proposes against one it matched, and the row
        # needs its title to state an honest ask.
        for cid, title in (("cmt_fresh", "Send the Q3 draft"),
                           ("cmt_old", "Get Northwind the pricing")):
            f.write(json.dumps({"type": "commitment", "seq": 0,
                    "ts": _ago(90), "source_skill": "meeting-notes",
                    "data": {"id": cid, "title": title,
                             "owner_id": "person:001", "kind": "promise"}}) + "\n")
        # a fresh proposal (kept) and an old one past its TTL (expired)
        f.write(json.dumps({"type": "commitment_review_proposed", "seq": 1,
                "ts": _ago(2),
                "data": {"commitment_id": "cmt_fresh", "ttl_days": 14}}) + "\n")
        f.write(json.dumps({"type": "commitment_review_proposed", "seq": 2,
                "ts": _ago(76),
                "data": {"commitment_id": "cmt_old", "ttl_days": 14}}) + "\n")
    reviews = bp._adapt_commitment_reviews(d)
    ids = {r["commitment_id"] for r in reviews}
    check("fresh review proposal kept", "cmt_fresh" in ids)
    check("review proposal past TTL expires (FS-11)", "cmt_old" not in ids)

    # ================= 5b: person auto-add =================================
    import people_writer as pw
    d2 = tempfile.mkdtemp(); os.makedirs(os.path.join(d2, "_hq", "data"))
    open(os.path.join(d2, "_hq", "data", "entities.json"), "w").write(
        json.dumps({"people": [], "orgs": [], "version": 1}))
    r = pw.auto_add_person(d2, canonical_name="Quinn Sample", email="quinn@example.com",
                           email_provenance={"source": "meeting", "id": "mtg1"})
    check("auto-add with observed provenance stores the email",
          r["status"] == "added" and r["record"].get("email") == "quinn@example.com")
    # Fresh workspace so the shared "Sample" surname token doesn't collide.
    d2b = tempfile.mkdtemp(); os.makedirs(os.path.join(d2b, "_hq", "data"))
    open(os.path.join(d2b, "_hq", "data", "entities.json"), "w").write(
        json.dumps({"people": [], "orgs": [], "version": 1}))
    r2 = pw.auto_add_person(d2b, canonical_name="Dustin Sample", email="guess@example.com")
    check("auto-add DROPS a no-provenance (guessed) email (F-08 at capture)",
          r2["status"] == "added" and r2["record"].get("email") is None
          and r2["email_dropped_no_provenance"] is True)
    r3 = pw.auto_add_person(d2, canonical_name="Quinn Sample",
                            email_provenance={"source": "x"})
    check("same-name dedup gate blocks a duplicate auto-add",
          r3["status"] == "needs_confirm"
          and any(m["canonical_name"] == "Quinn Sample" for m in r3["matches"]))

    # ================= 5c: email queue-on-click posture ====================
    ew = (ROOT / "skills" / "email-writer" / "SKILL.md").read_text(encoding="utf-8")
    ew_flat = " ".join(ew.split())  # normalize line-wraps for substring checks
    check("email-writer states QUEUE-ON-CLICK (FS-11)",
          "QUEUE-ON-CLICK" in ew_flat
          and "nothing touches the mail backend until the user clicks" in ew_flat.lower())
    check("email-writer retires queue-on-render",
          "RETIRED" in ew_flat and "queue on render" in ew_flat.lower())
    check("email-writer fires events AT the click (email_drafted + voice snapshot)",
          "email_drafted" in ew_flat and "voice snapshot" in ew_flat
          and "at that click" in ew_flat.lower())
    tmpl = (ROOT / "references" / "claude-md-template.md").read_text(encoding="utf-8")
    check("client CLAUDE.md template carries queue-on-click posture",
          "nothing touches your mail drafts until you click" in tmpl)
    bridge = (ROOT / "skills" / "command-room-update-bridge" / "SKILL.md").read_text(encoding="utf-8")
    check("bridge migration carries queue-on-click to client workspaces",
          "draft_posture_queue_on_click_v1" in bridge
          and "nothing touches your mail drafts until you click" in bridge)

    if failures:
        print(f"\nFS-11 M rulings FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"FS-11 M rulings (5a/5b/5c): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
