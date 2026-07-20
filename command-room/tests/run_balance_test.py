#!/usr/bin/env python3
"""
SPEC BAL1 — Balance Guardian acceptance suite (§6 test plan + acceptance
criteria 3, 4, 4a, 5).

Covers: white_space_debt exact math · cadence_days isolation from the work
dormancy path · single-nudge emit with personal-lane payload · dedupe/snooze ·
the FIREWALL (a cold personal tie is present in Balance, ABSENT from
relationship-moves, in the Pulse exclusion list, and read by no org surface) ·
the tie/cadence_days writer round-trip · not-configured refusal · malformed
lines.

Every fixture date is computed relative to a runtime `NOW` (G14 date-guard —
nothing here ever goes stale).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import balance as B  # noqa: E402
import dormancy  # noqa: E402
import personal_leak as PL  # noqa: E402
from relationship_moves import compute_relationship_moves  # noqa: E402

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


# Runtime-relative anchor — all gaps/dates derive from it (G14).
NOW = dt.datetime.now().replace(microsecond=0)
NOW_ISO = NOW.isoformat() + "Z"


def days_ago(n: float) -> str:
    return (NOW - dt.timedelta(days=n)).isoformat() + "Z"


def evening_slot(offset_days: int = 2) -> dict:
    d = (NOW + dt.timedelta(days=offset_days)).date()
    return {"date": d.isoformat(),
            "start": dt.datetime.combine(d, dt.time(18, 0)).isoformat(),
            "end": dt.datetime.combine(d, dt.time(22, 0)).isoformat(),
            "hours": 4.0}


def make_ws(people=None, workspace=None, events=None) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="cr-balance-test-"))
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "version": 1, "people": people or [],
        "workspace": workspace if workspace is not None else {},
    }), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in (events or [])), encoding="utf-8")
    return ws


CAL_WS = {"personal_calendars": ["family-cal@example"],
          "evening_start": "18:00"}


def tie_person(pid, name, last, cadence=None, tie="personal"):
    rec = {"id": pid, "canonical_name": name, "first_seen": days_ago(400)[:10],
           "last_interaction": last[:10], "status": "active", "tie": tie}
    if cadence is not None:
        rec["cadence_days"] = cadence
    return rec


# ---------------------------------------------------------------------------
print("\n[1] white_space_debt — exact decay-first math")
# ---------------------------------------------------------------------------

slots = [evening_slot()]
ties = [
    {"person_id": "person_901", "name": "Alex Sample", "gap_days": 21.0, "cadence_days": 14},
    {"person_id": "person_902", "name": "Bo Sample", "gap_days": 7.0, "cadence_days": 14},
    {"person_id": "person_903", "name": "Cam Sample", "gap_days": 16.0, "cadence_days": 14},
]
ranked = B.white_space_debt(ties, [], slots, NOW_ISO)
check("21d/14d tie ranks first with exact score 1.5",
      ranked and ranked[0]["person_id"] == "person_901"
      and ranked[0]["score"] == 1.5, repr(ranked))
check("16d/14d tie ranks second (score 1.142857)",
      len(ranked) == 2 and ranked[1]["person_id"] == "person_903"
      and ranked[1]["score"] == round(16.0 / 14, 6), repr(ranked))
check("7d/14d tie (score 0.5 < 1.0, not starved) is absent",
      all(c["person_id"] != "person_902" for c in ranked))

# Zero open slots -> starvation is suppressed entirely (a nudge with no
# actionable evening is noise, D2) — the global form of "a starved tie with
# zero slots does not win over one with a slot".
check("starved ties with ZERO open slots are suppressed, not surfaced",
      B.white_space_debt(ties, [], [], NOW_ISO) == [])

# Unknown gap can't claim starvation.
check("tie with no touch on record (gap None) excluded",
      B.white_space_debt([{"person_id": "p", "gap_days": None}], [], slots,
                         NOW_ISO) == [])

# cadence_days drives the threshold: 21d gap vs cadence 30 is NOT starved.
check("per-tie cadence_days respected (21d vs cadence 30 -> not starved)",
      B.white_space_debt([{"person_id": "p", "gap_days": 21.0,
                           "cadence_days": 30}], [], slots, NOW_ISO) == [])

# ---------------------------------------------------------------------------
print("\n[2] cadence_days is read by balance ONLY — work dormancy untouched")
# ---------------------------------------------------------------------------

rec = tie_person("person_901", "Alex Sample", days_ago(21), cadence=14)
check("dormancy.cadence_override_days ignores cadence_days (None)",
      dormancy.cadence_override_days(rec) is None)
check("dormancy.effective_baseline unchanged by cadence_days presence",
      dormancy.effective_baseline(20.0, dormancy.cadence_override_days(rec))
      == 20.0)
check("balance.tie_cadence_days never reads cadence_override_days",
      B.tie_cadence_days({"cadence_override_days": 90}) == B.DEFAULT_CADENCE_DAYS)

# ---------------------------------------------------------------------------
print("\n[3] compute_balance — single emit, personal payload, statuses")
# ---------------------------------------------------------------------------

ws = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(21), 14)],
             workspace=CAL_WS)
res = B.compute_balance(ws, now=NOW_ISO, personal_busy=[], business_busy=[])
check("cold tie yields status nudge", res["status"] == "nudge", repr(res))
check("nudge kind is reconnect with the tie's id",
      res["nudge"]["tie_person_id"] == "person_901"
      and res["nudge"]["kind"] == "reconnect")

evs = [json.loads(ln) for ln in
       (ws / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").splitlines() if ln]
nudge_evs = [e for e in evs if e.get("type") == "balance_nudge_suggested"]
check("exactly one balance_nudge_suggested emitted", len(nudge_evs) == 1,
      repr(evs))
ev = nudge_evs[0]
check("event data.personal is True (D6: personal always)",
      ev["data"].get("personal") is True)
check("event payload equals the returned nudge (plus the personal flag)",
      ev["data"] == dict(res["nudge"], personal=True), repr(ev["data"]))
check("personal_leak.is_personal classifies the emitted nudge row",
      PL.is_personal(ev) is True)
check("proposed_action carries no venue and no draft seq at emit time "
      "(propose-and-confirm — the confirm click appends the linkage later)",
      ev["data"]["proposed_action"]["venue"] is None
      and ev["data"]["proposed_action"]["draft_event_seq"] is None)

# Zero starved -> all_clear, no event.
ws2 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(3), 14)],
              workspace=CAL_WS)
res2 = B.compute_balance(ws2, now=NOW_ISO, personal_busy=[], business_busy=[])
evs2 = (ws2 / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8")
check("nothing starved -> all_clear and NO event written",
      res2["status"] == "all_clear" and "balance_nudge_suggested" not in evs2)

# Not configured -> refusal, never all-clear, nothing written.
ws3 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(90), 14)],
              workspace={})
res3 = B.compute_balance(ws3, now=NOW_ISO, personal_busy=[], business_busy=[])
check("no personal calendar declared -> not_configured (refuse, not all-clear)",
      res3["status"] == "not_configured")
check("not_configured emits nothing",
      "balance_nudge_suggested" not in
      (ws3 / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8"))

# Calendars declared but busy unavailable -> refuse.
ws4 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(90), 14)],
              workspace=CAL_WS)
res4 = B.compute_balance(ws4, now=NOW_ISO, personal_busy=None, business_busy=[])
check("declared calendars but no busy data -> no_calendar_data (refuse)",
      res4["status"] == "no_calendar_data")

# Busy evenings everywhere -> no open slot -> all_clear (suppressed, no guilt).
allbusy = [{"start": (NOW + dt.timedelta(days=d)).replace(hour=17, minute=0).isoformat(),
            "end": (NOW + dt.timedelta(days=d)).replace(hour=23, minute=0).isoformat()}
           for d in range(15)]
res5 = B.compute_balance(ws4, now=NOW_ISO, personal_busy=allbusy, business_busy=[])
check("starved tie but zero open evenings -> suppressed (all_clear, no nudge)",
      res5["status"] == "all_clear", repr(res5.get("status")))

# ---------------------------------------------------------------------------
print("\n[4] dedupe + snooze/dismissal")
# ---------------------------------------------------------------------------

def _nudge_event(seq, ts):
    return {"seq": seq, "ts": ts, "type": "balance_nudge_suggested",
            "source_skill": "balance", "person_ids": ["person_901"],
            "data": {"tie_person_id": "person_901", "kind": "reconnect",
                     "personal": True}}

people = [tie_person("person_901", "Alex Sample", days_ago(30), 14)]

ws5 = make_ws(people=people, workspace=CAL_WS,
              events=[_nudge_event(1, days_ago(4))])
r = B.compute_balance(ws5, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("prior nudge 4 days ago -> tie excluded this fire",
      r["status"] == "all_clear", repr(r.get("status")))

ws6 = make_ws(people=people, workspace=CAL_WS,
              events=[_nudge_event(1, days_ago(8))])
r = B.compute_balance(ws6, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("prior nudge 8 days ago -> tie included again",
      r["status"] == "nudge", repr(r.get("status")))

ws7 = make_ws(people=people, workspace=CAL_WS, events=[
    {"seq": 1, "ts": days_ago(1), "type": "dont_forget_snooze",
     "source_skill": "pulse", "person_ids": ["person_901"], "data": {}}])
r = B.compute_balance(ws7, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("active snooze -> tie excluded", r["status"] == "all_clear")

ws8 = make_ws(people=people, workspace=CAL_WS, events=[
    {"seq": 1, "ts": days_ago(0.5), "type": "chat_dismissal",
     "source_skill": "balance",
     "data": {"person_id": "person_901", "target_id": "person_901"}}])
r = B.compute_balance(ws8, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("live dismissal -> tie excluded", r["status"] == "all_clear")

# Second-eyes fixes (BAL1 review) — snooze/dismissal boundedness + target shape:
# (a) a STALE Pulse snooze (30d old, no snooze_until) must NOT mute the tie
#     forever — person_009/person_013 carry exactly these on live substrate.
ws8b = make_ws(people=people, workspace=CAL_WS, events=[
    {"seq": 1, "ts": days_ago(30), "type": "dont_forget_snooze",
     "source_skill": "pulse", "person_ids": ["person_901"], "data": {}}])
r = B.compute_balance(ws8b, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("30d-old snooze (no snooze_until) does NOT exclude — bounded, not forever",
      r["status"] == "nudge", repr(r.get("status")))

# (b) an ACTIVE snooze_until is honored even when the event ts is old.
ws8c = make_ws(people=people, workspace=CAL_WS, events=[
    {"seq": 1, "ts": days_ago(30), "type": "dont_forget_snooze",
     "source_skill": "pulse", "person_ids": ["person_901"],
     "data": {"snooze_until": (NOW + dt.timedelta(days=2)).isoformat() + "Z"}}])
r = B.compute_balance(ws8c, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("future snooze_until on an old snooze event still excludes",
      r["status"] == "all_clear", repr(r.get("status")))

# (c) the apply-choices dispatch writes mute-ledger-shaped dismissals whose
#     ONLY person reference is data.target_id — the dedupe must read it.
ws8d = make_ws(people=people, workspace=CAL_WS, events=[
    {"seq": 1, "ts": days_ago(0.5), "type": "chat_dismissal",
     "source_skill": "apply-choices",
     "data": {"target_id": "person_901",
              "snooze_until": (NOW + dt.timedelta(days=6)).isoformat() + "Z"}}])
r = B.compute_balance(ws8d, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("dismissal carrying only data.target_id excludes the tie",
      r["status"] == "all_clear", repr(r.get("status")))

# ---------------------------------------------------------------------------
print("\n[5] FIREWALL — Balance sees the tie; the work surfaces never do")
# ---------------------------------------------------------------------------

# A cold personal tie WITH a planted dormancy_signal (as if some emitter
# slipped) must appear in Balance output and NOT in relationship-moves.
fw_events = [
    {"seq": 1, "ts": days_ago(2), "type": "dormancy_signal",
     "source_skill": "pulse",
     "data": {"entity_id": "person_901", "entity_type": "person",
              "gap_days": 40, "baseline_days": 14, "score": 2.8571}},
]
ws9 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(40), 14),
                      tie_person("person_777", "Quinn Sample", days_ago(40),
                                 None, tie="work")],
              workspace=CAL_WS, events=fw_events)
bal = B.compute_balance(ws9, now=NOW_ISO, personal_busy=[], business_busy=[],
                        emit=False)
check("cold personal tie IS the Balance nudge",
      bal["status"] == "nudge"
      and bal["nudge"]["tie_person_id"] == "person_901")
moves = compute_relationship_moves(ws9, top_n=3, emit=False)
check("the SAME tie is ABSENT from relationship-moves (consumer backstop)",
      all(c["person_id"] != "person_901" for c in moves), repr(moves))

# An absent tie field (back-compat work person) still ranks in moves.
fw2 = [dict(fw_events[0])]
fw2[0] = dict(fw2[0], data=dict(fw2[0]["data"], entity_id="person_777"))
ws10 = make_ws(people=[tie_person("person_777", "Quinn Sample", days_ago(40),
                                  None, tie="work")],
               workspace=CAL_WS, events=fw2)
moves2 = compute_relationship_moves(ws10, top_n=3, emit=False)
check("a work person still ranks in relationship-moves (partition, not a mute)",
      any(c["person_id"] == "person_777" for c in moves2), repr(moves2))

# Pulse source gate + cadence exclusion are pinned in the orchestrator text.
pulse = (ROOT / "skills" / "enable-command-room-schedules" / "references" /
         "orchestrator-dont-forget.md").read_text(encoding="utf-8")
check("Pulse Phase 3 carries the personal-tie source gate (D1.1(1))",
      'tie: "personal"' in pulse and "source gate" in pulse)
check("balance_nudge_suggested is in the Pulse cadence-exclusion list",
      "`balance_nudge_suggested`" in pulse)

# Secondary emitters carry the skip (D1.1(3)).
for skill in ("team-intelligence", "dormant-customer-scan"):
    text = (ROOT / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    check(f"{skill} emit gate skips personal ties",
          'tie: "personal"' in text and "emit_dormancy_signal" in text)

# Grep proof (acceptance #5): no org-facing surface names the event type.
# Allowlist = the personal lane itself + its documentation.
TOKEN = "balance_nudge_suggested"
TOKEN_ALLOW = {
    "shared/scripts/balance.py",             # the writer + self-dedupe reader
    "shared/scripts/personal_leak.py",       # the lane classifier
    "shared/data-schemas/events.schema.json",
    "shared/EVENT_TYPES.md",                 # lane documentation
    "shared/CHAT_ACTION_WIDGET.md",          # owner-card documentation
    "skills/balance/SKILL.md",               # the owner surface itself
    "skills/enable-command-room-schedules/references/orchestrator-balance.md",
    "skills/enable-command-room-schedules/references/orchestrator-dont-forget.md",  # the EXCLUSION list
}
offenders = []
for base in (ROOT / "skills", ROOT / "shared"):
    for f in base.rglob("*"):
        if f.suffix not in (".py", ".md", ".json") or not f.is_file():
            continue
        rel = f.relative_to(ROOT).as_posix()
        if rel in TOKEN_ALLOW:
            continue
        try:
            if TOKEN in f.read_text(encoding="utf-8", errors="replace"):
                offenders.append(rel)
        except OSError:
            continue
check("no org-facing skill/driver references balance_nudge_suggested",
      offenders == [], f"offenders={offenders}")

# ---------------------------------------------------------------------------
print("\n[6] writer round-trip — tie + cadence_days through people_writer (4a)")
# ---------------------------------------------------------------------------

from people_writer import update_person  # noqa: E402

ws11 = make_ws(people=[{"id": "person_901", "canonical_name": "Alex Sample",
                        "first_seen": days_ago(400)[:10], "status": "active"}],
               workspace={})
rec = update_person(ws11, "person_901", source_skill="run_balance_test",
                    tie="personal", cadence_days=14)
stored = json.loads((ws11 / "_hq" / "data" / "entities.json")
                    .read_text(encoding="utf-8"))
srec = next(p for p in stored["people"] if p["id"] == "person_901")
check("update_person accepts tie + cadence_days and persists them",
      srec.get("tie") == "personal" and srec.get("cadence_days") == 14,
      repr(srec))

rejected = False
try:
    update_person(ws11, "person_901", source_skill="run_balance_test",
                  relationship_type="spouse")
except ValueError:
    rejected = True
check("relationship_type on a person is still rejected", rejected)

rejected = False
try:
    update_person(ws11, "person_901", source_skill="run_balance_test",
                  tie="family")
except ValueError:
    rejected = True
check("tie outside work|personal is rejected", rejected)

# ---------------------------------------------------------------------------
print("\n[7] malformed events.jsonl line — skipped, no crash")
# ---------------------------------------------------------------------------

ws12 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(30), 14)],
               workspace=CAL_WS)
p = ws12 / "_hq" / "data" / "events.jsonl"
p.write_text("this is not json\n" + json.dumps(
    {"seq": 1, "ts": days_ago(1), "type": "interaction",
     "person_ids": ["person_901"], "source_skill": "t", "data": {}}) + "\n",
    encoding="utf-8")
r = B.compute_balance(ws12, now=NOW_ISO, personal_busy=[], business_busy=[],
                      emit=False)
check("malformed line tolerated (skipped_lines surfaced, no crash)",
      r.get("skipped_lines", 0) >= 1 and r["status"] in ("nudge", "all_clear"),
      repr(r.get("skipped_lines")))
check("the recent interaction reset the gap (all_clear, not a nudge)",
      r["status"] == "all_clear")

# ---------------------------------------------------------------------------
print("\n[8] local wall-clock anchoring (BAL1 second-eyes fix)")
# ---------------------------------------------------------------------------
# The 18:00-22:00 evening window is a LOCAL concept. The anchor for the
# open-evening computation must be workspace-local wall clock, and busy
# intervals arriving as tz.to_local output (AWARE local) must block the
# local evening — not shift to UTC and report it open.

# (a) _local_now_naive localizes through tz when the workspace TZ resolves —
#     pinned with an injected tz module so no tzdata dependency.
import types as _types
_saved_tz = sys.modules.get("tz")
_fake = _types.ModuleType("tz")
_fixed_local = dt.datetime(2026, 1, 5, 8, 0,  # DATE_GUARD_OK — pure-math value
                           tzinfo=dt.timezone(dt.timedelta(hours=-7)))
_fake.to_local = lambda value, workspace_path=None: _fixed_local
sys.modules["tz"] = _fake
try:
    anchored = B._local_now_naive("any-root", "2026-01-05T15:00:00Z")  # DATE_GUARD_OK
    check("_local_now_naive returns the LOCAL wall clock, tzinfo dropped",
          anchored == dt.datetime(2026, 1, 5, 8, 0), repr(anchored))  # DATE_GUARD_OK
finally:
    if _saved_tz is not None:
        sys.modules["tz"] = _saved_tz
    else:
        sys.modules.pop("tz", None)

# (b) unresolvable TZ (test fixtures) -> anchor unchanged (single-clock rule).
check("_local_now_naive falls back to the given now when TZ unresolvable",
      B._local_now_naive(make_ws(), NOW_ISO) == NOW_ISO)

# (c) end-to-end: an aware-local dinner covering every evening of the horizon
#     suppresses the nudge (busy actually subtracts — the UTC-shift bug class).
_off = dt.timezone(dt.timedelta(hours=-7))
aware_busy = [
    {"start": dt.datetime.combine((NOW + dt.timedelta(days=d)).date(),
                                  dt.time(17, 0), tzinfo=_off).isoformat(),
     "end": dt.datetime.combine((NOW + dt.timedelta(days=d)).date(),
                                dt.time(23, 0), tzinfo=_off).isoformat()}
    for d in range(15)]
ws13 = make_ws(people=[tie_person("person_901", "Alex Sample", days_ago(90), 14)],
               workspace=CAL_WS)
r = B.compute_balance(ws13, now=NOW_ISO, personal_busy=aware_busy,
                      business_busy=[], emit=False)
check("aware-local busy evenings actually block (all_clear, no false open slot)",
      r["status"] == "all_clear", repr(r.get("status")))

# ---------------------------------------------------------------------------
print(f"\n{'=' * 60}\nPASS {PASS}  FAIL {FAIL}")
sys.exit(1 if FAIL else 0)
