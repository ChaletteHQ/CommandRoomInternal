#!/usr/bin/env python3
"""SPEC LB2 (Living Brain remainder) — §5 suite.

Covers: per-family writer migration round-trips + fossil back-compat (§5.1),
cross-rail dedup (§5.2), the mutation-tested auto-tier parity pin — the FB-20
mandate (§5.3), the auto lifecycle contract (§5.4), the config-drift detector
incl. the prefs byte-identity proof (§5.5), the rm_supersede_v1 planner +
adjudication idempotency (§5.6), shard-transparent tracker/tree reads (§5.7),
the render_tree utf-8 pin (§5.8), and readalarm sidecar pruning (§5.9), plus
instruction-layer pins (G13 posture) for every new helper ↔ skill-text pair.

Fixtures mirror real substrate shapes (real-data fixture gotcha); all dates
computed relative to today (G14); placeholder names only (Sam Sample,
Acme Co, Northwind)."""
import inspect
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
sys.path.insert(0, str(REPO / "skills" / "list-active"))
import brain_proposals as bp  # noqa: E402
import cleanup_actions as ca  # noqa: E402
import config_drift_detector as cdd  # noqa: E402
import render_master_tracker as rmt  # noqa: E402
import render_tree as rt  # noqa: E402
import schedule_config as sc  # noqa: E402
import schedule_proposals as sp  # noqa: E402
from migration_adjudication import is_suppressed  # noqa: E402
from proposal_ledger import active_cooldowns  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
BP_TUPLES = [{"action": "confirm proposal"},
             {"action": "dismiss proposal"},
             {"action": "snooze proposal 7d"}]


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [], "orgs": [
        {"id": "org_acme", "canonical_name": "Acme Co",
         "display_name": "Acme Co", "relationship_type": "prospect"},
    ], "threads": [], "engagements": []}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    (data / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _raw_append(ws, rows):
    """Backdated fixture lines exactly as they sit on disk (real substrate
    shape — seq/ts present; the gate stamps live writes)."""
    path = ws / "_hq" / "data" / "events.jsonl"
    existing = path.read_text(encoding="utf-8")
    seq = existing.count("\n")
    lines = []
    for r in rows:
        seq += 1
        r.setdefault("seq", seq)
        r.setdefault("ts", _iso(NOW - timedelta(days=1)))
        lines.append(json.dumps(r, ensure_ascii=False))
    path.write_text(existing + "".join(l + "\n" for l in lines),
                    encoding="utf-8")


# ===========================================================================
# §5.1 — per-family migration round-trip + fossil back-compat
# ===========================================================================
print("[1] per-family migration round-trips")

# -- org: migrated writer → ONE bp row with verbs → resolve → tombstone+ledger
ws = _ws()
r = bp.propose(ws, kind="org", tier="confirm", fingerprint="org:northwind",
               evidence="5 threads reference northwind.example.com",
               action_tuples=list(BP_TUPLES), detector="dont-forget",
               render_line="track Northwind as a company?",
               extra={"title": "Northwind", "name": "Northwind"})
check(r["status"] == "proposed", "org: migrated writer proposes")
rows = [i for i in bp.load_open_proposals(ws) if i["kind"] == "org"]
check(len(rows) == 1 and rows[0]["source_family"] == "brain",
      f"org: ONE bp row in the projector: {len(rows)}")
check([t["action"] for t in rows[0]["action_tuples"]] ==
      ["confirm proposal", "dismiss proposal", "snooze proposal 7d"],
      "org: bp row carries the registered verbs")
check(rows[0]["shape"] == "identity", "org: identity shape preserved")
res = bp.resolve_proposal(ws, r["proposal_id"], "applied",
                          resolved_by="person_m", source_skill="apply-choices")
check(res["status"] == "resolved", "org: resolves through resolve_proposal")
check(not [i for i in bp.load_open_proposals(ws) if i["kind"] == "org"],
      "org: tombstone retires the row")
# fossil: a pre-migration org_proposal event still renders + still resolves
# through the legacy path (decline event) — adapters are permanent
_raw_append(ws, [{"type": "org_proposal", "source_skill": "pulse",
                  "data": {"name": "Sample Corp", "evidence": "old prose"}}])
fossil = [i for i in bp.load_open_proposals(ws)
          if i["source_family"] == "org"]
check(len(fossil) == 1 and fossil[0]["name"] == "Sample Corp",
      "org fossil: pre-migration event renders via the adapter")
_raw_append(ws, [{"type": "org_proposal_declined", "source_skill": "pulse",
                  "data": {"name": "Sample Corp"}}])
check(not [i for i in bp.load_open_proposals(ws)
           if i["source_family"] == "org"],
      "org fossil: legacy decline still retires it")

# -- project: same shape
ws = _ws()
r = bp.propose(ws, kind="project", tier="confirm",
               fingerprint="project:atlas rollout",
               evidence="recurring cadence with no home",
               action_tuples=list(BP_TUPLES), detector="dont-forget",
               extra={"title": "Atlas Rollout", "name": "Atlas Rollout"})
rows = [i for i in bp.load_open_proposals(ws) if i["kind"] == "project"]
check(len(rows) == 1 and rows[0]["source_family"] == "brain",
      "project: migrated writer lands ONE bp row")
bp.resolve_proposal(ws, r["proposal_id"], "declined",
                    resolved_by="person_m", source_skill="apply-choices")
check("project:atlas rollout" in active_cooldowns(ws, "dont-forget",
                                                  now_iso=_iso(NOW)),
      "project: decline enters the shared 60d ledger")
_raw_append(ws, [{"type": "project_proposal", "source_skill": "pulse",
                  "data": {"name": "Legacy Initiative", "evidence": "x"}}])
check([i["name"] for i in bp.load_open_proposals(ws)
       if i["source_family"] == "project"] == ["Legacy Initiative"],
      "project fossil: pre-migration event still renders")

# -- dormancy: migrated writer; fossil rows keep legacy retirement
ws = _ws()
r = bp.propose(ws, kind="dormancy", tier="confirm",
               fingerprint="dont_forget:th_quiet", thread_id="th_quiet",
               evidence="34 days quiet", ttl_days=30,
               action_tuples=list(BP_TUPLES), detector="dont-forget",
               render_line="this project is going quiet — move to Dormant?",
               extra={"title": "Quiet Project"})
rows = [i for i in bp.load_open_proposals(ws) if i["kind"] == "dormancy"]
check(len(rows) == 1 and rows[0]["source_family"] == "brain",
      "dormancy: migrated writer lands ONE bp row")
bp.resolve_proposal(ws, r["proposal_id"], "declined",
                    resolved_by="person_m", source_skill="pulse")
check(not [i for i in bp.load_open_proposals(ws) if i["kind"] == "dormancy"],
      "dormancy: decline retires the bp row")
_raw_append(ws, [{"type": "dont_forget_dormant_proposal",
                  "source_skill": "pulse",
                  "data": {"thread_id": "th_old", "reason": "45 days quiet"}}])
check([i["target_id"] for i in bp.load_open_proposals(ws)
       if i["source_family"] == "dont_forget"] == ["th_old"],
      "dormancy fossil: pre-migration event still renders")
_raw_append(ws, [{"type": "dont_forget_dormant_proposal_declined",
                  "source_skill": "pulse", "data": {"thread_id": "th_old"}}])
check(not [i for i in bp.load_open_proposals(ws)
           if i["source_family"] == "dont_forget"],
      "dormancy fossil: legacy decline still retires it")

# -- schedule_add: log_proposal writes the suppression record AND the bp row
ws = _ws()
ok = sp.log_proposal(ws, "staff-meeting",
                     line="Say 'add staff meeting' and it runs Mondays.",
                     reason="3 proposals waiting on you")
check(ok, "schedule_add: log_proposal returns True")
evs = [json.loads(l) for l in
       (ws / "_hq" / "data" / "events.jsonl").read_text(
           encoding="utf-8").splitlines() if l.strip()]
check(any(e.get("type") == "schedule_add_proposed" for e in evs),
      "schedule_add: suppression record still written")
rows = [i for i in bp.load_open_proposals(ws) if i["kind"] == "schedule_add"]
check(len(rows) == 1 and rows[0]["source_family"] == "brain"
      and rows[0]["fingerprint"] == "schedule:staff-meeting",
      "schedule_add: bp row persisted with the family fingerprint")
# suppression authority stays singular: the freshly-logged proposal
# suppresses the live-computed adapter path for the full window, so the
# projection can never show a bp row AND an adapter row for the same task
rows_full = [i for i in bp.load_open_proposals(ws, registered_task_ids=[])
             if i["kind"] == "schedule_add"]
check(len(rows_full) == 1,
      f"schedule_add: no double row with the adapter path live: {len(rows_full)}")

# ===========================================================================
# §5.2 — cross-rail dedup (open legacy row + migrated writer re-fire)
# ===========================================================================
print("[2] cross-rail dedup")
ws = _ws()
_raw_append(ws, [{"type": "org_proposal", "source_skill": "pulse",
                  "data": {"name": "Northwind", "evidence": "prose row"}}])
r = bp.propose(ws, kind="org", tier="confirm", fingerprint="org:northwind",
               evidence="same org again", action_tuples=list(BP_TUPLES),
               detector="dont-forget", extra={"name": "Northwind"})
check(r["status"] == "duplicate_open_legacy",
      f"org: open legacy row suppresses the migrated writer: {r['status']}")
check(len([i for i in bp.load_open_proposals(ws)
           if i["kind"] == "org"]) == 1, "org: queue shows ONE row, not two")

ws = _ws()
_raw_append(ws, [{"type": "project_proposal", "source_skill": "pulse",
                  "data": {"name": "Atlas Rollout", "evidence": "x"}}])
r = bp.propose(ws, kind="project", tier="confirm",
               fingerprint="project:atlas rollout", evidence="again",
               action_tuples=list(BP_TUPLES), detector="dont-forget")
check(r["status"] == "duplicate_open_legacy",
      "project: open legacy row suppresses the migrated writer")

ws = _ws()
_raw_append(ws, [{"type": "dont_forget_dormant_proposal",
                  "source_skill": "pulse",
                  "data": {"thread_id": "th1", "reason": "quiet"}}])
r = bp.propose(ws, kind="dormancy", tier="confirm",
               fingerprint="dont_forget:th1", thread_id="th1",
               evidence="again", action_tuples=list(BP_TUPLES),
               detector="dont-forget")
check(r["status"] == "duplicate_open_legacy",
      "dormancy: open legacy row suppresses the migrated writer")

# fingerprint-convention enforcement — the natural key IS the dedup key
try:
    bp.propose(ws, kind="org", tier="confirm", fingerprint="freeform-fp",
               evidence="x", action_tuples=list(BP_TUPLES), detector="d")
    check(False, "migrated kind with off-convention fingerprint must raise")
except bp.BrainProposalError:
    check(True, "migrated-kind fingerprint convention enforced at source")

# ===========================================================================
# §5.3 — auto-tier parity pin (the FB-20 mandate, mutation-tested)
# ===========================================================================
print("[3] auto-tier parity pin")
ws = _ws()
bp.propose(ws, kind="commitment_close", tier="auto",
           change_class="commitment_close", fingerprint="cc:auto:parity",
           evidence="high-band sent match", action_tuples=list(BP_TUPLES),
           detector="reconcile-sent")
bp.propose(ws, kind="deal_update", tier="confirm",
           fingerprint="deal:parity:stage", evidence="stage language",
           action_tuples=list(BP_TUPLES), detector="deal-signals",
           extra={"title": "Acme Co"})
# THE mutation pin: flipping the default back to auto-inclusive goes RED here
check(inspect.signature(bp.load_open_proposals)
      .parameters["include_auto"].default is False,
      "load_open_proposals defaults include_auto=False (parity pin)")
# every consumer path agrees, and none counts the auto row:
brief_count = len(bp.load_open_proposals(ws, "staff-meeting"))     # brief pointer
sm_ids = []
from surface_drivers import build_staff_meeting_view  # noqa: E402
view = build_staff_meeting_view(ws)
for sec in view["sections"]:
    sm_ids += [it["n"] for it in sec["items"]]
n_waiting = len(bp.load_open_proposals(ws, "system-health"))        # system-health
check(brief_count == len(sm_ids) == n_waiting == 1,
      f"parity: pointer({brief_count}) == staff meeting({len(sm_ids)}) == "
      f"n_waiting({n_waiting}) == 1 (0 autos)")
check(len(bp.load_open_proposals(ws, include_auto=True)) == 2,
      "diagnostic escape reaches the auto row")

# ===========================================================================
# §5.4 — auto lifecycle contract (a resting open auto trips the check)
# ===========================================================================
print("[4] auto lifecycle contract")
resting = bp.resting_auto_proposals(ws)
check(len(resting) == 1 and resting[0]["fingerprint"] == "cc:auto:parity",
      "a test detector that proposes auto without same-run resolve trips "
      "resting_auto_proposals")
health = bp.card_health_counts(ws)
check(health["resting_auto"] == 1 and health["open"] == 1,
      f"card_health_counts: resting_auto={health['resting_auto']}, "
      f"open excludes it ({health['open']})")
bp.resolve_proposal(ws, resting[0]["id"], "applied",
                    resolved_by="reconcile-sent", source_skill="reconcile-sent")
check(bp.resting_auto_proposals(ws) == [],
      "same-run apply+resolve clears the contract check")

# ===========================================================================
# §5.5 — config-drift detector (thresholds, cooldown, prefs read-only proof)
# ===========================================================================
print("[5] config-drift detector")


def _drift_ws(age_days, n_signals):
    ws = _ws()
    cfgdir = ws / "_hq" / "data" / "skill_config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "email-writer.json").write_text(json.dumps({
        "schema_version": 1,
        "configured_at": _iso(NOW - timedelta(days=age_days)),
        "skill_name": "email-writer",
        "config": {"sign_off": "Sam Sample"},
    }), encoding="utf-8")
    vdir = ws / "_hq" / "voice"
    vdir.mkdir(parents=True)
    rows = [json.dumps({
        "timestamp": _iso(NOW - timedelta(days=3, minutes=i)),
        "skill": "email-writer", "domain": "external", "recipient_id": None,
        "original_draft": "…", "corrected_by_user": "…",
        "correction_type": "tone",
        "notes": "greeting/sign-off/hedging change",
    }) for i in range(n_signals)]
    (vdir / "corrections-email-writer.jsonl").write_text(
        "\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return ws


# threshold vectors: both edges must hold (5-signal AND 6-month)
check(cdd.detect_config_drift(_drift_ws(200, 4)) == [],
      "4 signals under the bar → no candidate")
check(cdd.detect_config_drift(_drift_ws(100, 7)) == [],
      "young config (100d) → no candidate regardless of signals")
cands = cdd.detect_config_drift(_drift_ws(200, 5))
check(len(cands) == 1 and cands[0]["knob"] == "sign_off"
      and cands[0]["n_signals"] == 5,
      f"5 signals + >6mo → ONE candidate naming the knob: {cands}")

ws = _drift_ws(200, 6)
cfg_path = ws / "_hq" / "data" / "skill_config" / "email-writer.json"
before = cfg_path.read_bytes()
out = cdd.run_drift_detector(ws)
check(out["proposed"] == 1, f"drift run proposes once: {out}")
check(cfg_path.read_bytes() == before,
      "prefs byte-identical after a full detector run (READ-ONLY on prefs)")
rows = [i for i in bp.load_open_proposals(ws, "staff-meeting")
        if i["kind"] == "config_drift"]
check(len(rows) == 1 and rows[0]["surface_hint"] == "staff-meeting",
      "config_drift row reaches the staff meeting with its surface hint")
card = bp.select_confirm_card(ws, "morning-brief")
check(all(i["kind"] != "config_drift" for i in card["items"]),
      "config_drift never enters the daily card (staff meeting only)")
out2 = cdd.run_drift_detector(ws)
check(out2["proposed"] == 0 and out2["suppressed"] == 1,
      "once-per-knob: open row dedups the second run")
bp.resolve_proposal(ws, rows[0]["id"], "declined",
                    resolved_by="person_m", source_skill="apply-choices")
out3 = cdd.run_drift_detector(ws)
check(out3["proposed"] == 0,
      "once-per-knob: dismissal's 60d ledger cooldown suppresses re-offer")
check("config_drift:email-writer:sign_off" in
      active_cooldowns(ws, "config-drift", now_iso=_iso(NOW)),
      "the knob's cooldown rides the shared ledger")

# ===========================================================================
# §5.6 — RM supersede planner + adjudication idempotency
# ===========================================================================
print("[6] rm_supersede_v1")
check(sc.rm_supersede_plan([]) is None, "no RM registration → no plan")
check(sc.rm_supersede_plan([{"taskId": "morning-brief",
                             "cron": "0 7 * * *"}]) is None,
      "other tasks alone → no plan")
plan = sc.rm_supersede_plan([{"taskId": "relationship-moves",
                              "cron": "0 17 * * 0"}])
check(plan is not None and plan["marker"] == "rm_supersede_v1"
      and plan["remove_task_id"] == "relationship-moves"
      and plan["staff_meeting_registered"] is False
      and plan["carry_cron"] is None,
      f"RM on its default cron → default staff-meeting cadence: {plan}")
plan = sc.rm_supersede_plan([
    {"taskId": "relationship-moves", "cron": "0 18 * * 2"},
    {"taskId": "staff-meeting", "cron": "0 9 * * 1,3,5"},
])
check(plan["carry_cron"] == "0 18 * * 2"
      and plan["staff_meeting_registered"] is True,
      f"custom RM cron carries; existing staff meeting detected: {plan}")
# decline path durability + idempotent marker (the adjudication gate)
ws = _ws()
check(not is_suppressed(ws, "rm_supersede_v1"),
      "unadjudicated workspace → migration pending")
_raw_append(ws, [{"type": "workspace_migration_skipped",
                  "source_skill": "command-room-update-bridge",
                  "data": {"migration_id": "rm_supersede_v1",
                           "reason": "user_declined_rm_supersede"}}])
check(is_suppressed(ws, "rm_supersede_v1"),
      "decline suppresses forever (never re-proposes)")
ws = _ws()
_raw_append(ws, [{"type": "workspace_migration_applied",
                  "source_skill": "command-room-update-bridge",
                  "data": {"migration_id": "rm_supersede_v1"}}])
check(is_suppressed(ws, "rm_supersede_v1"),
      "applied marker is idempotent (never re-proposes)")

# ===========================================================================
# §5.7 — shard transparency (tracker + tree)
# ===========================================================================
print("[7] shard-transparent tracker/tree reads")
LASTYEAR = NOW.year - 1
shard_ts = f"{LASTYEAR}-06-15T10:00:00Z"


def _sharded_ws(in_shard=True):
    ws = _ws()
    data = ws / "_hq" / "data"
    ent = json.loads((data / "entities.json").read_text(encoding="utf-8"))
    ent["threads"] = [{"id": "th_shard", "display_name": "Old Faithful",
                       "status": "active", "affiliation_id": "org_acme",
                       "first_seen": f"{LASTYEAR}-01-01"}]
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    ev = {"seq": 1, "ts": shard_ts, "type": "note", "source_skill": "t",
          "primary_thread_id": "th_shard", "data": {"summary": "old touch"}}
    if in_shard:
        (data / f"events-{LASTYEAR}.jsonl").write_text(
            json.dumps(ev) + "\n", encoding="utf-8")
        (data / "events.jsonl").write_text(json.dumps(
            {"seq": 2, "ts": _iso(NOW - timedelta(days=1)),
             "type": "shard_rotated", "source_skill": "rotate_events",
             "data": {"year": LASTYEAR}}) + "\n", encoding="utf-8")
    else:
        (data / "events.jsonl").write_text(
            json.dumps(ev) + "\n", encoding="utf-8")
    return ws


ws_shard, ws_flat = _sharded_ws(True), _sharded_ws(False)
events_shard = rt._load_events(ws_shard)
check(any(e.get("primary_thread_id") == "th_shard" for e in events_shard),
      "render_tree loads events living only in a yearly shard")
tree_shard = rt.build_tree(json.loads(
    (ws_shard / "_hq" / "data" / "entities.json").read_text(encoding="utf-8")),
    events_shard, None, include_archived=False)
tree_flat = rt.build_tree(json.loads(
    (ws_flat / "_hq" / "data" / "entities.json").read_text(encoding="utf-8")),
    rt._load_events(ws_flat), None, include_archived=False)


def _tree_activity(tree):
    orgs, _ = tree
    for o in orgs:
        for p in o.projects:
            if p.id == "th_shard":
                return p.last_activity
    return None


act_shard, act_flat = _tree_activity(tree_shard), _tree_activity(tree_flat)
check(act_shard is not None and str(act_shard)[:10] == f"{LASTYEAR}-06-15",
      f"tree: sharded thread shows its real last activity: {act_shard}")
check(str(act_shard)[:10] == str(act_flat)[:10],
      "tree: recency identical to the unsharded equivalent")

r1 = rmt.regenerate(ws_shard)
view_txt = (ws_shard / "_hq" / "views" / "MASTER_TRACKER.md").read_text(
    encoding="utf-8")
check(f"{LASTYEAR}-06-15" in view_txt,
      "tracker: thread active-as-of the shard event's date (not dormant/—)")

# ===========================================================================
# §5.8 — encoding pin (the cp1252 crash regression)
# ===========================================================================
print("[8] render_tree utf-8 pin")
ws = _ws()
data = ws / "_hq" / "data"
ent = json.loads((data / "entities.json").read_text(encoding="utf-8"))
ent["orgs"][0]["display_name"] = "Café Zürich"
ent["threads"] = [{"id": "th_nonascii", "display_name": "Über—Projekt",
                   "status": "active", "affiliation_id": "org_acme"}]
(data / "entities.json").write_text(
    json.dumps(ent, ensure_ascii=False), encoding="utf-8")
_raw_append(ws, [{"type": "note", "source_skill": "t",
                  "primary_thread_id": "th_nonascii",
                  "data": {"summary": "curly “quotes” and naïveté"}}])
loaded = rt._load_json(data / "entities.json")
check(loaded is not None and "Café Zürich" in json.dumps(
    loaded, ensure_ascii=False), "non-ASCII entities.json loads")
tree = rt.build_tree(loaded, rt._load_events(ws), None, include_archived=False)
rendered = rt.render(*tree)
check("Café Zürich" in rendered, "non-ASCII tree renders end-to-end")
src = (REPO / "skills" / "list-active" / "render_tree.py").read_text(
    encoding="utf-8")
check('path.open(encoding="utf-8")' in src,
      "mutation pin: _load_json opens utf-8 explicitly")

# ===========================================================================
# §5.9 — readalarm sidecar pruning
# ===========================================================================
print("[9] sidecar pruning")
ws = _ws()
data = ws / "_hq" / "data"


def _sidecar(name, days_old):
    p = data / name
    p.write_text(json.dumps({
        "file": name.replace(".readalarm.json", ""),
        "first_seen": _iso(NOW - timedelta(days=days_old + 1)),
        "last_seen": _iso(NOW - timedelta(days=days_old)),
        "count": 3, "last_error": "boom", "last_reader": "events_io",
    }), encoding="utf-8")
    return p


stale = _sidecar("entities.json.readalarm.json", 40)
fresh = _sidecar("events.jsonl.readalarm.json", 2)
mid = _sidecar("aliases.json.readalarm.json", 10)
junk = data / "old.json.readalarm.json"
junk.write_text("{not json", encoding="utf-8")
old_epoch = (NOW - timedelta(days=45)).timestamp()
os.utime(junk, (old_epoch, old_epoch))

pruned = ca.prune_stale_readalarms(ws)
check(sorted(pruned) == ["_hq/data/entities.json.readalarm.json",
                         "_hq/data/old.json.readalarm.json"],
      f"stale (40d) + unreadable-old (mtime) deleted, others kept: {pruned}")
check(fresh.exists() and mid.exists(),
      "fresh (2d) and mid-window (10d) sidecars survive")
check(ca.prune_stale_readalarms(ws) == [], "idempotent: second run finds nothing")
# hard floor: even max_age_days=0 never touches a sidecar younger than
# RECENT_HOURS * 2 (evidence must have been surfaceable at least once)
floor_ws = _ws()
young = (floor_ws / "_hq" / "data" / "x.json.readalarm.json")
young.write_text(json.dumps(
    {"file": "x.json", "last_seen": _iso(NOW - timedelta(hours=100))}),
    encoding="utf-8")
check(ca.prune_stale_readalarms(floor_ws, max_age_days=0) == []
      and young.exists(),
      "floor: a 100h-old sidecar survives even max_age_days=0")

# ===========================================================================
# instruction-layer pins (G13 posture — helper ↔ skill-text pairs)
# ===========================================================================
print("[10] instruction-layer pins")


def _txt(rel):
    return (REPO / rel).read_text(encoding="utf-8")


cleanup_md = _txt("skills/cleanup/SKILL.md")
check("run_drift_detector" in cleanup_md and "config_drift_detector" in cleanup_md,
      "cleanup SKILL.md wires the drift detector")
check("prune_stale_readalarms" in cleanup_md,
      "cleanup SKILL.md wires sidecar pruning")
check("resting_auto" in cleanup_md,
      "cleanup SKILL.md reads the resting-auto contract count")
bridge_md = _txt("skills/command-room-update-bridge/SKILL.md")
check('id: "rm_supersede_v1"' in bridge_md
      and "### Migration: `rm_supersede_v1`" in bridge_md
      and "rm_supersede_plan" in bridge_md,
      "update-bridge carries the rm_supersede_v1 migration + planner")
check("`rm_supersede_v1`)" in bridge_md.split("marker: null` (`staff_meeting_cadence_mwf_v1`, ", 1)[1][:40]
      if "marker: null` (`staff_meeting_cadence_mwf_v1`, " in bridge_md else "rm_supersede_v1" in bridge_md,
      "marker-null adjudication rule names rm_supersede_v1")
dfo_md = _txt("skills/enable-command-room-schedules/references/"
              "orchestrator-dont-forget.md")
check('kind="dormancy"' in dfo_md and 'kind="org"' in dfo_md,
      "dont-forget orchestrator writes through propose() (migrated families)")
ac_md = _txt("skills/apply-choices/SKILL.md")
check("config_drift" in ac_md and "drift_reoffer" in ac_md,
      "apply-choices dispatches the config_drift confirm → note event")
check("migration is LB2" not in _txt("shared/scripts/brain_proposals.py")
      and "migration is LB2" not in ac_md,
      "stale-comment sweep: no 'migration is LB2' left in code or dispatch text")
frp_md = _txt("shared/FIRST_RUN_PROTOCOL.md")
check("config_drift_detector" in frp_md,
      "FIRST_RUN_PROTOCOL names the mechanized drift path")

print(f"OK — {PASS} checks passed")
