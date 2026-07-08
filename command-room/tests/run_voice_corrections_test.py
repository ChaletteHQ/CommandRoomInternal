#!/usr/bin/env python3
"""SPEC B1 — voice-calibration loop tests.

House conventions: check(name, cond), OK/FAIL, non-zero exit, auto-discovered.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import voice_corrections as vc  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws():
    ws = Path(tempfile.mkdtemp(prefix="b1_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "voice").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def test_diff_and_classify():
    check("identical text -> []", vc.diff_and_classify("Same text.", "Same text.") == [])

    r = vc.diff_and_classify("I wanted to circle back on X.", "Following up on X.")
    check("circle-back rewrite -> phrasing", len(r) == 1 and r[0]["correction_type"] == "phrasing")

    r = vc.diff_and_classify("- item one\n- item two", "We did item one and item two.")
    check("bullets -> prose -> structure", r and r[0]["correction_type"] == "structure")

    r = vc.diff_and_classify("Best,", "Thanks,")
    check("sign-off swap -> tone", r and r[0]["correction_type"] == "tone")

    big_o = "Hello team, here is the quarterly revenue update.\n\nWe grew twenty percent and signed five clients."
    big_c = "Switching gears entirely to discuss hiring.\n\nOpening three marketing roles plus two engineers."
    r = vc.diff_and_classify(big_o, big_c)
    check("full rewrite -> single structure row", len(r) == 1 and r[0]["notes"] == "full rewrite")

    six_o = "\n\n".join(f"Point {n} about the plan." for n in range(1, 7))
    six_c = "\n\n".join(f"Point {n} regarding the plan." for n in range(1, 7))
    r = vc.diff_and_classify(six_o, six_c)
    check("6 changed paragraphs -> 5 rows max", len(r) == 5)


def test_append_correction_schema_and_dedup():
    ws = _ws()
    wrote = vc.append_correction(ws, skill="email-writer", domain="email-short-external",
                                 recipient_id="person_012", original="I wanted to circle back.",
                                 corrected="Following up.", correction_type="phrasing", notes="x")
    check("append returns True on first write", wrote is True)
    path = ws / "_hq" / "voice" / "corrections-email-writer.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("exactly one row", len(rows) == 1)
    check("row keys match schema exactly", set(rows[0].keys()) == {
        "timestamp", "skill", "domain", "recipient_id", "original_draft",
        "corrected_by_user", "correction_type", "notes"})
    dup = vc.append_correction(ws, skill="email-writer", domain="email-short-external",
                               recipient_id="person_012", original="I wanted to circle back.",
                               corrected="Following up.", correction_type="phrasing", notes="x")
    rows2 = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("duplicate append is a no-op", dup is False and len(rows2) == 1)


def test_snapshot_and_reconcile_exact_match():
    ws = _ws()
    vc.snapshot_draft(ws, skill="email-writer", domain="email-short-external",
                      recipient_id="person_1", recipient_email="a@example.com",
                      subject="Project update", body="Original first para.\n\nSecond para here.",
                      draft_event_seq=5, gmail_message_id="m1")
    sent = [{"message_id": "m1", "ts": "2026-06-10T00:00:00Z", "recipient_person_ids": ["person_1"],
             "subject": "Project update", "body": "Edited first para now.\n\nSecond para here."}]
    res = vc.reconcile_sent_against_snapshots(ws, sent)
    check("exact gmail_message_id match -> matched + corrections", res["n_matched"] == 1 and res["n_corrections"] >= 1)


def test_reconcile_fallback_and_unchanged_and_missing():
    from cru_match import _now_iso
    ws = _ws()
    vc.snapshot_draft(ws, skill="email-writer", domain="d", recipient_id="person_2",
                      recipient_email="b@example.com", subject="Hello", body="Body one.",
                      draft_event_seq=6)  # no gmail_message_id -> forces fallback
    # Sent ts ~= snapshot ts (both ~now) so the 7-day window passes deterministically.
    sent = [{"message_id": "zzz", "ts": _now_iso(), "recipient_person_ids": ["person_2"],
             "subject": "Re: Hello", "body": "Body one edited."}]
    res = vc.reconcile_sent_against_snapshots(ws, sent)
    check("recipient+subject+window fallback matches", res["n_matched"] == 1 and res["n_corrections"] >= 1)

    ws2 = _ws()
    vc.snapshot_draft(ws2, skill="email-writer", domain="d", recipient_id="person_3",
                      recipient_email="c@example.com", subject="Same", body="Identical body.",
                      draft_event_seq=7, gmail_message_id="m9")
    sent2 = [{"message_id": "m9", "ts": "2026-06-11T00:00:00Z", "recipient_person_ids": ["person_3"],
              "subject": "Same", "body": "Identical body."}]
    res2 = vc.reconcile_sent_against_snapshots(ws2, sent2)
    check("unchanged sent body -> zero corrections", res2["n_matched"] == 1 and res2["n_corrections"] == 0)

    ws3 = _ws()  # no snapshots file written
    (ws3 / "_hq" / "voice" / "draft-snapshots.jsonl").unlink(missing_ok=True)
    res3 = vc.reconcile_sent_against_snapshots(ws3, sent2)
    check("missing snapshots file -> clean no-op", res3["n_matched"] == 0 and res3["n_corrections"] == 0)


def test_unreviewed_counts():
    ws = _ws()
    cpath = ws / "_hq" / "voice" / "corrections-email-writer.jsonl"
    cpath.write_text("\n".join(json.dumps({
        "timestamp": ts, "skill": "email-writer", "domain": "d", "recipient_id": None,
        "original_draft": f"o{ts}", "corrected_by_user": f"c{ts}",
        "correction_type": "phrasing", "notes": ""})
        for ts in ["2026-05-01T00:00:00Z", "2026-06-10T00:00:00Z", "2026-06-12T00:00:00Z"]) + "\n",
        encoding="utf-8")
    (ws / "_hq" / "data" / "events.jsonl").write_text(json.dumps({
        "type": "voice_calibration_review", "ts": "2026-06-05T00:00:00Z",
        "data": {"reviewed_through": {"email-writer": "2026-06-05T00:00:00Z"}}}) + "\n",
        encoding="utf-8")
    counts = vc.unreviewed_counts(ws)
    check("unreviewed honors reviewed_through (2 after cutoff)", counts.get("email-writer") == 2)


def test_override_roundtrip():
    ws = _ws()
    p = vc.write_voice_block_override(ws, "email-writer", "## Voice Block\n\nNo em dashes.",
                                      calibration_level="calibrated", sample_count=5)
    check("override file written", p.exists())
    ov = vc.load_voice_block_override(ws, "email-writer")
    check("override round-trips markdown", ov is not None and "No em dashes." in ov["markdown"])
    check("Last refreshed header present", bool(ov and ov["last_refreshed"]))
    check("sample count header present", ov and ov["sample_count"] == "5")
    check("absent override -> None", vc.load_voice_block_override(ws, "memo-writer") is None)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_diff_and_classify()
    test_append_correction_schema_and_dedup()
    test_snapshot_and_reconcile_exact_match()
    test_reconcile_fallback_and_unchanged_and_missing()
    test_unreviewed_counts()
    test_override_roundtrip()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL voice_corrections tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
