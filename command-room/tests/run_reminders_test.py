#!/usr/bin/env python3
"""
v4.6.0 W4a — Reminders v1 acceptance suite (SPEC V4.5.2 Wave 4 + M's four
settled choices + the 4.6-wave repeat decision).

Pins the whole lane:

  1. Capture → pin → daily render → clear round-trip: a reminder pins from
     remind_from, renders EVERY day until cleared, and clearing deactivates
     a one-shot.
  2. Repeat re-arm (recurrence lives on REMINDERS, not commitments):
     weekly / monthly (with month-length clamp) / every_days cron-lite;
     derive-next-on-read from the clear event — a late-cleared daily repeat
     advances past the clear date, never insta-pins with a stale clock.
  3. Escalation (choice ②): pinned + ignored 3d = bold, 7d = top; a `keep`
     touch resets the clock; push moves the pin.
  4. Personal privacy (choice ④): default personal=true with no tracked
     business entity; personal rows EXCLUDED from the default (client-facing)
     read path — only surface="m_facing" sees them.
  5. Ref independence: clearing a reminder that refs a commitment leaves the
     commitment open (and its counts untouched); reminders never leak into
     load_open_commitments / count_commitments.
  6. Origin hard rule: builders, the append gate (both entries), AND the
     reader all reject/ignore non-user_explicit reminder events — a sweep or
     scheduled task can never mint one, and a gate bypass never renders.
  7. Gate identity: reminder ids minted rem_<ulid>; id-less mutations reject
     (dead letters).

The show-my-reminders vs show-my-list trigger fence is pinned in
tests/triggers.yaml (run_trigger_test.py) — phrases for both lanes plus the
'remind me to revisit' decision-revisit fence.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import reminders as R  # noqa: E402
from commitment_state import count_commitments  # noqa: E402
from cru_match import load_open_commitments  # noqa: E402
from event_gate import EventGateError, append_event, gate_events  # noqa: E402

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


def ts(day: str, hm: str = "10:00") -> str:
    return f"{day}T{hm}:00+00:00"


# ---------------------------------------------------------------------------
# 1. Round-trip: capture → pin → daily render → clear (through the REAL
#    locked write path, in a temp workspace)
# ---------------------------------------------------------------------------
print("\n[1] capture → pin → daily render → clear round-trip")

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    events_path = data_dir / "events.jsonl"

    ev = R.capture_reminder(
        events_path,
        "soft-sell follow-up with Michele",
        remind_from="2026-07-10",
        person_ids=["person_michele"],
    )
    rid = ev["data"]["id"]
    check("capture mints rem_<ulid>", rid.startswith("rem_") and len(rid) == 30, rid)

    on_disk = [json.loads(l) for l in events_path.read_text().splitlines()]
    check("capture wrote exactly one gated event", len(on_disk) == 1)
    check("write auto-stamped seq + ts", "seq" in on_disk[0] and "ts" in on_disk[0])

    # Day before remind_from: upcoming, not pinned.
    rows = R.load_active_reminders(ws, "2026-07-09", surface="m_facing")
    check("day before: upcoming, no pin", rows and rows[0]["status"] == "upcoming")

    # Pins on remind_from, and EVERY day after until cleared.
    for day, dp in (("2026-07-10", 0), ("2026-07-11", 1), ("2026-07-14", 4)):
        rows = R.load_active_reminders(ws, day, surface="m_facing")
        check(
            f"pinned on {day} (days_pinned={dp})",
            len(rows) == 1
            and rows[0]["status"] == "pinned"
            and rows[0]["days_pinned"] == dp,
            f"got {rows}",
        )

    # Clear → gone from every later render (one-shot).
    append_event(
        events_path, R.build_reminder_cleared_event(rid), holder="test"
    )
    rows = R.load_active_reminders(ws, "2026-07-15", surface="m_facing")  # DATE_GUARD_OK: this IS the injected as-of clock, not data compared to the real clock
    check("cleared one-shot never renders again", rows == [], f"got {rows}")

# ---------------------------------------------------------------------------
# 2. Repeat re-arm — derive-next-on-read, no scheduler
# ---------------------------------------------------------------------------
print("\n[2] repeat re-arm (weekly / monthly clamp / every_days)")

base = R.build_reminder_event("send weekly update", remind_from="2026-07-06", repeat="weekly")
wid = base["data"]["id"]
evs = [dict(base, ts=ts("2026-07-06"))]
rows = R.active_reminders(evs, "2026-07-08", surface="m_facing")
check("repeating reminder pins like any other", rows[0]["status"] == "pinned")

evs.append(dict(R.build_reminder_cleared_event(wid), ts=ts("2026-07-08")))
rows = R.active_reminders(evs, "2026-07-09", surface="m_facing")
check(
    "weekly clear re-arms to next occurrence (2026-07-13), not deactivation",
    len(rows) == 1 and rows[0]["remind_from"] == "2026-07-13",
    f"got {rows}",
)
check("re-armed row is scheduled/upcoming, not pinned", rows[0]["status"] != "pinned")
rows = R.active_reminders(evs, "2026-07-13", surface="m_facing")
check("re-armed occurrence pins on its day", rows[0]["status"] == "pinned")

check("monthly clamps Jan 31 → Feb 28", R.next_occurrence("2026-01-31", "monthly").isoformat() == "2026-02-28")
check("monthly Dec → Jan year rollover", R.next_occurrence("2026-12-15", "monthly").isoformat() == "2027-01-15")  # DATE_GUARD_OK: pure calendar arithmetic (next_occurrence); no real clock involved
check("every_days cron-lite", R.next_occurrence("2026-07-01", {"every_days": 10}).isoformat() == "2026-07-11")

# Late clear on a high-frequency repeat: advance PAST the clear date.
daily = R.build_reminder_event("standup note", remind_from="2026-07-01", repeat={"every_days": 1})
did = daily["data"]["id"]
evs2 = [
    dict(daily, ts=ts("2026-07-01")),
    dict(R.build_reminder_cleared_event(did), ts=ts("2026-07-06")),
]
rows = R.active_reminders(evs2, "2026-07-06", surface="m_facing")
check(
    "late-cleared daily re-arms past the clear date (tomorrow, not today)",
    len(rows) == 1 and rows[0]["remind_from"] == "2026-07-07" and rows[0]["status"] != "pinned",
    f"got {rows}",
)

bad_repeats = ["daily", {"every_days": 0}, {"every_days": True}, {"cron": "* * *"}, 7]
for br in bad_repeats:
    try:
        R.build_reminder_event("x", remind_from="2026-07-10", repeat=br)
        check(f"invalid repeat {br!r} rejected", False)
    except R.ReminderError:
        check(f"invalid repeat {br!r} rejected", True)

# ---------------------------------------------------------------------------
# 3. Escalation states (choice ②): 3d bold, 7d top; keep resets; push moves
# ---------------------------------------------------------------------------
print("\n[3] escalation: 3d bold / 7d top / keep resets / push moves the pin")

esc = R.build_reminder_event("renew the LLC filing", remind_from="2026-07-01")
eid = esc["data"]["id"]
evs3 = [dict(esc, ts=ts("2026-07-01"))]

for day, tier in (
    ("2026-07-01", "none"),
    ("2026-07-03", "none"),
    ("2026-07-04", "bold"),
    ("2026-07-07", "bold"),
    ("2026-07-08", "top"),
):
    rows = R.active_reminders(evs3, day, surface="m_facing")
    check(
        f"escalation on {day} is {tier}",
        rows[0]["escalation"] == tier,
        f"got {rows[0]['escalation']}",
    )

# keep on day 5 resets the clock (acknowledged touch).
evs3.append(dict(R.build_reminder_updated_event(eid, action="keep"), ts=ts("2026-07-05")))
rows = R.active_reminders(evs3, "2026-07-07", surface="m_facing")
check("keep resets the escalation clock", rows[0]["escalation"] == "none", rows[0]["escalation"])
rows = R.active_reminders(evs3, "2026-07-08", surface="m_facing")
check("clock re-runs from the keep (3d after keep = bold)", rows[0]["escalation"] == "bold")

# push moves the pin date entirely.
evs3.append(
    dict(R.build_reminder_updated_event(eid, action="push", remind_from="2026-07-20"), ts=ts("2026-07-08"))  # DATE_GUARD_OK: remind_from data; active_reminders reads it against an injected as-of date
)
rows = R.active_reminders(evs3, "2026-07-10", surface="m_facing")
check("pushed reminder unpins until the new date", rows[0]["status"] != "pinned")
rows = R.active_reminders(evs3, "2026-07-20", surface="m_facing")  # DATE_GUARD_OK: this IS the injected as-of clock, not data compared to the real clock
check(
    "pushed reminder pins fresh on the new date (no stale escalation)",
    rows[0]["status"] == "pinned" and rows[0]["escalation"] == "none",
    f"got {rows[0]}",
)

# push also re-arms a cleared one-shot.
oneshot = R.build_reminder_event("book flights", remind_from="2026-07-02")
oid = oneshot["data"]["id"]
evs4 = [
    dict(oneshot, ts=ts("2026-07-02")),
    dict(R.build_reminder_cleared_event(oid), ts=ts("2026-07-03")),
    dict(R.build_reminder_updated_event(oid, action="push", remind_from="2026-07-11"), ts=ts("2026-07-04")),
]
rows = R.active_reminders(evs4, "2026-07-11", surface="m_facing")
check("push re-arms a cleared one-shot", len(rows) == 1 and rows[0]["status"] == "pinned")

# ---------------------------------------------------------------------------
# 4. Personal privacy (choice ④) — default rule + client-facing exclusion
# ---------------------------------------------------------------------------
print("\n[4] personal default + client-facing exclusion")

p1 = R.build_reminder_event("renew passport", remind_from="2026-07-10")
check("no tracked entity → personal defaults True", p1["data"]["personal"] is True)
# PGUARD1 D3 — a person reference ALONE no longer flips the default to work:
# "remind me to call Mom" with Mom tracked in entities.json stays personal.
# Only a business reference (ref / primary_thread_id) makes it work.
p_mom = R.build_reminder_event("call Mom", remind_from="2026-07-10", person_ids=["person_042"])
check("person ref alone → personal defaults True (D3)", p_mom["data"]["personal"] is True)
p2 = R.build_reminder_event("nudge the Acme renewal", remind_from="2026-07-10",
                            person_ids=["person_sam"], primary_thread_id="project_acme")
check("thread ref → personal defaults False", p2["data"]["personal"] is False)
p2b = R.build_reminder_event("chase the invoice", remind_from="2026-07-10", ref="cmt_XYZ")
check("business ref → personal defaults False", p2b["data"]["personal"] is False)
p3 = R.build_reminder_event("gym", remind_from="2026-07-10", ref="cmt_ABC", personal=True)
check("explicit personal=True wins over entity linkage", p3["data"]["personal"] is True)

evs5 = [dict(p1, ts=ts("2026-07-09")), dict(p2, ts=ts("2026-07-09")), dict(p3, ts=ts("2026-07-09"))]
m_rows = R.active_reminders(evs5, "2026-07-10", surface="m_facing")
check("m_facing surface sees all three", len(m_rows) == 3, f"got {len(m_rows)}")

# The client-facing render path: a caller that never thought about privacy —
# no surface argument at all — must NEVER receive a personal reminder.
cf_rows = R.active_reminders(evs5, "2026-07-10")
check(
    "default (client-facing) read excludes BOTH personal rows",
    [r["summary"] for r in cf_rows] == ["nudge the Acme renewal"],
    f"got {[r['summary'] for r in cf_rows]}",
)

# PGUARD1 D3 read-side mirror: a legacy flag-less row naming only a PERSON
# defaults personal at read time too — it never reaches a client_facing read.
legacy = dict(p_mom, ts=ts("2026-07-09"))
legacy["data"] = {k: v for k, v in p_mom["data"].items() if k != "personal"}
check(
    "flag-less person-only row → personal on read (D3)",
    R.active_reminders([legacy], "2026-07-10") == []
    and len(R.active_reminders([legacy], "2026-07-10", surface="m_facing")) == 1,
)

# ---------------------------------------------------------------------------
# 5. Ref independence + commitment-lane exclusion
# ---------------------------------------------------------------------------
print("\n[5] ref-clearing independence + reminders never enter commitment counts")

with tempfile.TemporaryDirectory() as td:
    ws = Path(td)
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True)
    events_path = data_dir / "events.jsonl"

    # A real open commitment, written through the gate.
    append_event(
        events_path,
        {
            "type": "commitment",
            "source_skill": "test",
            "data": {
                "title": "send Pedro the revised SOW",
                "kind": "promise",
                "owner_id": "person_user",
                "counterparty_id": "person_pedro",
                "due": "2026-07-11",
                "status": "open",
            },
        },
        holder="test",
    )
    opens = load_open_commitments(str(events_path))
    cmt_id = opens[0]["data"]["id"]
    baseline_counts = count_commitments(opens, user_person_id="person_user", now_iso=ts("2026-07-10"))

    # A reminder pointing AT that commitment.
    rem = R.capture_reminder(
        events_path,
        "the Pedro chase",
        remind_from="2026-07-10",
        ref=cmt_id,
        person_ids=["person_pedro"],
    )
    opens2 = load_open_commitments(str(events_path))
    counts2 = count_commitments(opens2, user_person_id="person_user", now_iso=ts("2026-07-10"))
    check("reminder events never enter load_open_commitments", len(opens2) == len(opens))
    check("commitment counts unchanged by reminder writes", counts2 == baseline_counts)

    # Clearing the reminder leaves the commitment OPEN.
    append_event(events_path, R.build_reminder_cleared_event(rem["data"]["id"]), holder="test")
    opens3 = load_open_commitments(str(events_path))
    check(
        "clearing a ref'd reminder does NOT close the commitment",
        len(opens3) == 1 and opens3[0]["data"]["id"] == cmt_id,
        f"got {len(opens3)} open",
    )
    rows = R.load_active_reminders(ws, "2026-07-11", surface="m_facing")
    check("…and the reminder itself is cleared", rows == [])

# ---------------------------------------------------------------------------
# 6 + 7. Origin hard rule + gate identity
# ---------------------------------------------------------------------------
print("\n[6] origin hard rule — builder, gate (both entries), reader")

for bad in ("session-sweep", "scheduled_task", "skill", "", None):
    try:
        R.build_reminder_event("x", remind_from="2026-07-10", origin=bad)
        check(f"builder rejects origin={bad!r}", False)
    except R.ReminderError:
        check(f"builder rejects origin={bad!r}", True)

hand_rolled = {
    "type": "reminder",
    "source_skill": "session-sweep",
    "data": {"summary": "machine pin", "remind_from": "2026-07-10", "origin": "session-sweep"},
}
try:
    gate_events([hand_rolled], holder="test")
    check("gate (strict) rejects machine-minted reminder", False)
except EventGateError:
    check("gate (strict) rejects machine-minted reminder", True)
try:
    gate_events([hand_rolled], strict_enum=False, holder="test")
    check("gate rejection is UNCONDITIONAL (legacy/warn path too)", False)
except EventGateError:
    check("gate rejection is UNCONDITIONAL (legacy/warn path too)", True)

for mut_type in ("reminder_updated", "reminder_cleared"):
    try:
        gate_events(
            [{"type": mut_type, "data": {"reminder_id": "rem_X", "origin": "cleanup"}}],
            holder="test",
        )
        check(f"gate rejects non-user {mut_type}", False)
    except EventGateError:
        check(f"gate rejects non-user {mut_type}", True)
    try:
        gate_events(
            [{"type": mut_type, "data": {"origin": "user_explicit"}}], holder="test"
        )
        check(f"gate rejects id-less {mut_type} (dead letter)", False)
    except EventGateError:
        check(f"gate rejects id-less {mut_type} (dead letter)", True)

# Gate mints an id when the caller didn't set one (never synthesized-only).
minted = gate_events(
    [{"type": "reminder", "data": {"summary": "s", "remind_from": "2026-07-10", "origin": "user_explicit"}}],
    holder="test",
)[0]
check("gate mints rem_<ulid> when id absent", str(minted["data"]["id"]).startswith("rem_"))

# Reader defense-in-depth: even a gate BYPASS (CR_EVENT_GATE=0 replay) never
# renders a machine-minted reminder.
bypass = dict(hand_rolled, ts=ts("2026-07-09"))
bypass["data"] = dict(bypass["data"], id="rem_BYPASS")
rows = R.active_reminders([bypass], "2026-07-10", surface="m_facing")
check("reader ignores non-user_explicit reminders (gate-bypass safe)", rows == [])

# Machine-minted clear against a legitimate user reminder is also ignored.
legit = R.build_reminder_event("real pin", remind_from="2026-07-09")
machine_clear = {
    "type": "reminder_cleared",
    "data": {"reminder_id": legit["data"]["id"], "origin": "cleanup"},
    "ts": ts("2026-07-09", "12:00"),
}
rows = R.active_reminders([dict(legit, ts=ts("2026-07-09"))] + [machine_clear], "2026-07-10", surface="m_facing")
check("reader ignores a machine-minted clear (pin survives)", len(rows) == 1 and rows[0]["status"] == "pinned")

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}")
print(f"reminders v1 (W4a): {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
