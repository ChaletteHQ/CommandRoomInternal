#!/usr/bin/env python3
"""SPEC OBJ2 §2 — the objective_link apply-choices handlers (confirm /
dismiss / no-op replays), exercised exactly as the SKILL.md cr-brain block
dispatches them.

Covers: (a) the card's action verbs render with no CanonicalActionError;
(b) CONFIRM → the CONFIRMING reclassification+feedback pair (OBJ2-R:
envelope verbatim, confidence 1.0, user_action "confirmed") then
resolve_proposal(..., "applied") — derive_outcome "ok", n_errors 0 in the
built audit event, the detector re-run does not re-propose; (c) DISMISS → the standard reclassification+feedback pair (the
insight-generator Pass 8 idiom) THEN the decline: the reclassification event
correctly shaped (supersedes_seq = the payload's source_event_seq, new
thread ids = old minus the objective thread, reason naming the declined
proposal), the matched schema-valid classifier_feedback row, the declined
tombstone, the live 60d ledger cooldown, a detector re-run that proposes
nothing, and audit outcome "ok" via the handler_result pin (FS-18a — the
pair-writes are asserted here, never passed as handler_result);
(d) double-confirm / double-dismiss → honest already_resolved no-ops that
never audit as error and never duplicate the pair; (e) a pending_review-only
source event (no numeric confidence — the safety-inversion flag alone makes
it provisional) → the pair's confidence_before rides the 0.0 fallback so the
feedback row stays schema-valid (required number, never null).

The former reader-gap waiver is CLOSED (OBJ2-R): movement/detector reads
fold reclassifications via thread_activity.apply_reclassifications — the
movement-delta assertions live in run_obj2_supersession_test.py.

Fixtures mirror real substrate shapes; all dates computed relative to today
(G14); placeholder names only (Sam Sample, Bo Sample)."""
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "shared" / "scripts"))
import brain_proposals as bp  # noqa: E402
import objective_link_detector as old  # noqa: E402
from apply_audit import build_apply_choices_applied_event, derive_outcome  # noqa: E402
from atomic_write import atomic_append_jsonl  # noqa: E402
from chat_output_renderer import (  # noqa: E402
    CanonicalActionError,
    render_chat_output_widget,
)
from event_gate import append_event  # noqa: E402
from proposal_ledger import active_cooldowns  # noqa: E402

PASS = 0


def check(c, m):
    global PASS
    assert c, "FAIL: " + m
    PASS += 1


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)
OBJ = "project_901"
FP_CONF = "objective_link:project_901:cmt_01OBJ2DISPATCHCONF"
FP_DISM = "objective_link:project_901:cmt_01OBJ2DISPATCHDISM"
FP_PEND = "objective_link:project_901:cmt_01OBJ2DISPATCHPEND"
USER = "person_sam"


def _ws():
    d = Path(tempfile.mkdtemp())
    data = d / "_hq" / "data"
    data.mkdir(parents=True)
    ent = {"version": 1, "people": [
        {"id": "person_sam", "canonical_name": "Sam Sample"},
        {"id": "person_bo", "canonical_name": "Bo Sample"},
    ], "orgs": [], "engagements": [], "threads": [
        # the open objective (activity-bound to the Acme Pilot thread)
        {"id": OBJ, "kind": "objective", "status": "active",
         "canonical_name": "Land three lighthouse clients",
         "objective": {"statement": "Land three lighthouse clients",
                       "binding": {"type": "activity",
                                   "entity_ids": ["project_101"]},
                       "opened_at": (NOW - timedelta(days=40)).strftime("%Y-%m-%d")}},
        {"id": "project_101", "kind": "project", "status": "active",
         "canonical_name": "Acme Pilot"},
        # an unbound activity thread — the dismiss fixture's real home
        {"id": "project_202", "kind": "project", "status": "active",
         "canonical_name": "Summit Retreat"},
    ]}
    (data / "entities.json").write_text(json.dumps(ent), encoding="utf-8")
    evs = [
        # the CONFIRM fixture: provisional, objective as PRIMARY
        {"seq": 1, "ts": _iso(NOW - timedelta(days=5)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": OBJ,
         "classification_confidence": 0.55,
         "data": {"id": "cmt_01OBJ2DISPATCHCONF",
                  "title": "Sam Sample: draft the lighthouse pitch",
                  "status": "open", "origin": "user_stated"}},
        # the DISMISS fixture: provisional, objective in RELATED (primary
        # stands after the unlink — exercises "old minus the objective")
        {"seq": 2, "ts": _iso(NOW - timedelta(days=4)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "project_202",
         "related_thread_ids": [OBJ],
         "classification_confidence": 0.50,
         "data": {"id": "cmt_01OBJ2DISPATCHDISM",
                  "title": "Bo Sample: book the retreat venue",
                  "status": "open", "origin": "user_stated"}},
        # the PENDING-REVIEW-ONLY fixture: NO numeric confidence at all —
        # the writer's own flag is what makes it provisional (the v4.5.2
        # safety inversion); exercises the pair's confidence_before 0.0
        # fallback (the schema requires a number, never null)
        {"seq": 3, "ts": _iso(NOW - timedelta(days=3)), "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": OBJ,
         "data": {"id": "cmt_01OBJ2DISPATCHPEND", "pending_review": True,
                  "title": "Sam Sample: sketch the lighthouse deck",
                  "status": "open", "origin": "user_stated"}},
    ]
    (data / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
    return d


def _events(ws):
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _open_link_rows(ws):
    return [i for i in bp.load_open_proposals(ws, "staff-meeting")
            if i["kind"] == "objective_link"]


def _payload(ws, fingerprint):
    """The raw brain_proposal payload — where the SKILL.md handlers read
    objective_id / target_id / source_event_seq verbatim."""
    for ev in _events(ws):
        if (ev.get("type") == "brain_proposal"
                and ev.get("data", {}).get("fingerprint") == fingerprint):
            return ev["data"]
    return None


def _reclass_events(ws):
    return [e for e in _events(ws) if e.get("type") == "reclassification"]


ws = _ws()
out = old.run_objective_link_detector(ws)
check(out == {"candidates": 3, "proposed": 3, "suppressed": 0},
      f"all three fixtures propose (pending_review-only counts as "
      f"provisional): {out}")
rows = {r["fingerprint"]: r for r in _open_link_rows(ws)}
check(set(rows) == {FP_CONF, FP_DISM, FP_PEND},
      f"three open link rows: {set(rows)}")

# ===========================================================================
# §2.a — the card's action verbs render with no CanonicalActionError
# ===========================================================================
print("[a] action verbs render")
view = {"surface": "staff-meeting", "title": "Staff meeting",
        "sections": [{"title": "OBJECTIVES", "items": [
            {"n": str(i + 1), "name": r["render_line"] or r["fingerprint"],
             "actions": [t["action"] for t in r["action_tuples"]]}
            for i, r in enumerate(rows.values())]}]}
try:
    html = render_chat_output_widget(view, wrapper="fragment")
except CanonicalActionError as exc:
    html = None
    check(False, f"objective_link verbs must be canonical: {exc}")
check(html is not None and all(
    f'value="{v}"' in html for v in ("confirm proposal", "dismiss proposal",
                                     "snooze proposal 7d")),
      "the confirm/dismiss/snooze verbs render through the canonical path")

# ===========================================================================
# §2.b — CONFIRM: resolve_proposal(..., "applied") and NOTHING else
# ===========================================================================
print("[b] confirm dispatch")
id_conf = rows[FP_CONF]["id"]
# (1)+(2) the CONFIRMING pair, exactly as the SKILL.md handler dispatches
# it (OBJ2-R): envelope verbatim, confidence 1.0, matched "confirmed" row
pay_conf = _payload(ws, FP_CONF)
src_conf = next(e for e in _events(ws)
                if e.get("seq") == pay_conf["source_event_seq"])
append_event(ws / "_hq" / "data" / "events.jsonl", {
    "type": "reclassification",
    "source_skill": "apply-choices",
    "supersedes_seq": pay_conf["source_event_seq"],
    "primary_thread_id": src_conf.get("primary_thread_id"),
    "related_thread_ids": list(src_conf.get("related_thread_ids") or []),
    "classification_confidence": 1.0,
    "data": {
        "old_primary_thread_id": src_conf.get("primary_thread_id"),
        "new_primary_thread_id": src_conf.get("primary_thread_id"),
        "old_related_thread_ids": list(src_conf.get("related_thread_ids") or []),
        "new_related_thread_ids": list(src_conf.get("related_thread_ids") or []),
        "reason": (f"user confirmed objective-link proposal {id_conf} — "
                   f"link to {pay_conf['objective_id']}"),
    },
}, holder="apply-choices")
atomic_append_jsonl(ws / "_hq" / "data" / "classifier_feedback.jsonl", [{
    "ts": _iso(NOW),
    "event_seq": pay_conf["source_event_seq"],
    "user_action": "confirmed",
    "old_primary": src_conf.get("primary_thread_id"),
    "new_primary": src_conf.get("primary_thread_id"),
    "signals_used": [],
    "confidence_before": src_conf.get("classification_confidence"),
    "notes": f"objective link confirmed: {pay_conf['objective_id']}",
}], holder="apply-choices")
res_conf = bp.resolve_proposal(ws, id_conf, "applied",
                               resolved_by=USER, source_skill="apply-choices")
check(res_conf["status"] == "resolved"
      and res_conf["user_action"] == "applied"
      and res_conf["kind"] == "objective_link",
      f"confirm resolves applied: {res_conf}")
check(derive_outcome(res_conf) == "ok",
      "the handler_result pin: resolve_proposal's return derives 'ok'")
audit = build_apply_choices_applied_event(
    source="cr-brain",
    actions=[{"n": "1", "action": "confirm proposal",
              "handler_result": res_conf}])
check(audit["data"]["n_errors"] == 0
      and audit["data"]["actions"][0]["outcome"] == "ok",
      f"built audit event carries outcome ok / n_errors 0: {audit['data']}")
conf_recs = _reclass_events(ws)
check(len(conf_recs) == 1
      and conf_recs[0].get("supersedes_seq") == 1
      and conf_recs[0].get("classification_confidence") == 1.0
      and conf_recs[0].get("primary_thread_id") == OBJ
      and "confirmed" in (conf_recs[0].get("data") or {}).get("reason", ""),
      f"confirm writes the CONFIRMING reclassification — envelope verbatim, "
      f"confidence 1.0 (OBJ2-R): {conf_recs}")
fb_conf = [json.loads(l) for l in
           (ws / "_hq" / "data" / "classifier_feedback.jsonl").read_text(
               encoding="utf-8").splitlines() if l.strip()]
check(len(fb_conf) == 1 and fb_conf[0]["user_action"] == "confirmed"
      and fb_conf[0]["new_primary"] == fb_conf[0]["old_primary"],
      f"matched 'confirmed' feedback row rides the pair: {fb_conf}")
out2 = old.run_objective_link_detector(ws)
check(out2["proposed"] == 0,
      f"detector re-run does not re-propose the confirmed link: {out2}")
check(FP_CONF not in {c["fingerprint"]
                      for c in old.detect_objective_links(ws)},
      "the confirmed fingerprint is gone at DETECT level")

# ===========================================================================
# §2.c — DISMISS: the reclassification+feedback pair, then the decline
# ===========================================================================
print("[c] dismiss dispatch")
pay = _payload(ws, FP_DISM)
check(pay is not None and pay.get("source_event_seq") == 2
      and pay.get("objective_id") == OBJ,
      f"the payload rides the source seq + objective id verbatim: {pay}")

# (1) the reclassification event — supersedes the source event; new thread
#     ids = old minus the objective thread (the primary stands here).
src_ev = next(e for e in _events(ws) if e.get("seq") == pay["source_event_seq"])
old_primary = src_ev.get("primary_thread_id")
old_related = list(src_ev.get("related_thread_ids") or [])
new_primary = None if old_primary == pay["objective_id"] else old_primary
new_related = [t for t in old_related if t != pay["objective_id"]]
id_dism = rows[FP_DISM]["id"]
append_event(ws / "_hq" / "data" / "events.jsonl", {
    "type": "reclassification",
    "source_skill": "apply-choices",
    "supersedes_seq": pay["source_event_seq"],
    "primary_thread_id": new_primary,
    "related_thread_ids": new_related,
    "classification_confidence": 1.0,
    "data": {
        "old_primary_thread_id": old_primary,
        "new_primary_thread_id": new_primary,
        "old_related_thread_ids": old_related,
        "new_related_thread_ids": new_related,
        "reason": (f"user declined objective-link proposal {id_dism} — "
                   f"unlink from {pay['objective_id']}"),
    },
}, holder="apply-choices")
# (2) the MATCHED classifier_feedback teach row (schema-valid: required keys
#     ts/event_seq/user_action/confidence_before; enum user_action; no
#     extra properties beyond the schema's).
fb_row = {
    "ts": _iso(NOW),
    "event_seq": pay["source_event_seq"],
    "user_action": "changed",
    "old_primary": old_primary,
    "new_primary": new_primary,
    "signals_used": [],
    "confidence_before": src_ev.get("classification_confidence"),
    "notes": f"objective link declined: {pay['objective_id']}",
}
atomic_append_jsonl(ws / "_hq" / "data" / "classifier_feedback.jsonl",
                    [fb_row], holder="apply-choices")
# (3) the decline — cooldown applies; this return IS the handler_result.
res_dism = bp.resolve_proposal(ws, id_dism, "declined",
                               resolved_by=USER, source_skill="apply-choices")

recs = [r for r in _reclass_events(ws) if r.get("supersedes_seq") == 2]
check(len(recs) == 1,
      f"exactly ONE reclassification supersedes the dismiss source: {len(recs)}")
rec = recs[0]
check(rec.get("supersedes_seq") == 2
      and rec.get("source_skill") == "apply-choices"
      and rec.get("classification_confidence") == 1.0,
      f"reclassification supersedes the source event: {rec}")
check(rec.get("primary_thread_id") == "project_202"
      and rec.get("related_thread_ids") == [],
      f"new envelope = old minus the objective thread (primary stands, "
      f"related emptied): {rec}")
rd = rec.get("data") or {}
check(rd.get("old_primary_thread_id") == "project_202"
      and rd.get("new_primary_thread_id") == "project_202"
      and rd.get("old_related_thread_ids") == [OBJ]
      and rd.get("new_related_thread_ids") == [],
      f"data carries the full old/new id pairs: {rd}")
check(id_dism in rd.get("reason", "") and "declined" in rd.get("reason", ""),
      f"reason names the declined objective-link proposal: {rd.get('reason')}")
check(isinstance(rec.get("seq"), int) and rec["seq"] > 2,
      "the reclassification is a NEW append — the original event is never "
      "edited")

fb_lines = (ws / "_hq" / "data" / "classifier_feedback.jsonl").read_text(
    encoding="utf-8").splitlines()
check(len(fb_lines) == 2,
      f"one feedback row per adjudication (confirmed + changed): {len(fb_lines)}")
fb = json.loads(fb_lines[1])
check(all(k in fb for k in ("ts", "event_seq", "user_action",
                            "confidence_before")),
      f"feedback row carries the schema's required keys: {fb}")
check(fb["user_action"] == "changed" and fb["event_seq"] == 2
      and fb["old_primary"] == "project_202"
      and fb["new_primary"] == "project_202"
      and fb["confidence_before"] == 0.50,
      f"feedback row mirrors the reclassification (the matched pair): {fb}")
check(fb["event_seq"] == rec["supersedes_seq"],
      "the pair is MATCHED — feedback row and reclassification point at the "
      "same source event")

check(res_dism["status"] == "resolved"
      and res_dism["user_action"] == "declined",
      f"the proposal is declined: {res_dism}")
check({r["fingerprint"] for r in _open_link_rows(ws)} == {FP_PEND},
      "only the not-yet-adjudicated pending_review row remains open")
check(FP_DISM in active_cooldowns(ws, "objective-link", now_iso=_iso(NOW)),
      "the 60d ledger cooldown is live for the dismissed pairing")
check(derive_outcome(res_dism) == "ok",
      "audit outcome 'ok' via the pin — the decline's return, never the "
      "pair-writes, is the handler_result")
out3 = old.run_objective_link_detector(ws)
check(out3["proposed"] == 0,
      f"detector re-run proposes nothing after dismiss (cooldown): {out3}")
# The reader gap is CLOSED (OBJ2-R): the dismiss's reclassification DOES
# change the movement read now — those delta assertions live in
# run_obj2_supersession_test.py; this suite owns the dispatch shapes only.

# ===========================================================================
# §2.d — double-confirm / double-dismiss: honest no-ops, never audit errors
# ===========================================================================
print("[d] replay no-ops")
for pid, verb in ((id_conf, "applied"), (id_dism, "declined")):
    replay = bp.resolve_proposal(ws, pid, verb,
                                 resolved_by=USER,
                                 source_skill="apply-choices")
    check(replay["status"] == "already_resolved",
          f"replayed {verb} is an honest no-op: {replay}")
    check(derive_outcome(replay) == "already_resolved",
          f"replayed {verb} audits already_resolved, never error")
    audit2 = build_apply_choices_applied_event(
        source="cr-brain",
        actions=[{"n": "1", "action": "confirm proposal",
                  "handler_result": replay}])
    check(audit2["data"]["n_errors"] == 0,
          f"replay audit event carries n_errors 0: {audit2['data']}")
# an already-adjudicated row skips the pair-writes entirely (SKILL.md) —
# the pair never duplicates on a replayed dismiss
check(len(_reclass_events(ws)) == 2
      and len((ws / "_hq" / "data" / "classifier_feedback.jsonl").read_text(
          encoding="utf-8").splitlines()) == 2,
      "replays never duplicate either verb's reclassification+feedback pair")

# ===========================================================================
# §2.e — a pending_review-only source (no numeric confidence): the pair's
#        confidence_before falls back to 0.0 (the schema requires a number)
# ===========================================================================
print("[e] pending_review-only fallback")
pay_pend = _payload(ws, FP_PEND)
src_pend = next(e for e in _events(ws)
                if e.get("seq") == pay_pend["source_event_seq"])
check("classification_confidence" not in src_pend
      and (src_pend.get("data") or {}).get("pending_review") is True,
      "the fixture is provisional by the writer's flag alone — no numeric "
      "confidence anywhere on it")
id_pend = rows[FP_PEND]["id"]
append_event(ws / "_hq" / "data" / "events.jsonl", {
    "type": "reclassification",
    "source_skill": "apply-choices",
    "supersedes_seq": pay_pend["source_event_seq"],
    "primary_thread_id": None,
    "related_thread_ids": [],
    "classification_confidence": 1.0,
    "data": {
        "old_primary_thread_id": OBJ,
        "new_primary_thread_id": None,
        "old_related_thread_ids": [],
        "new_related_thread_ids": [],
        "reason": (f"user declined objective-link proposal {id_pend} — "
                   f"unlink from {pay_pend['objective_id']}"),
    },
}, holder="apply-choices")
atomic_append_jsonl(ws / "_hq" / "data" / "classifier_feedback.jsonl", [{
    "ts": _iso(NOW),
    "event_seq": pay_pend["source_event_seq"],
    "user_action": "changed",
    "old_primary": OBJ,
    "new_primary": None,
    "signals_used": [],
    # the SKILL's fallback, verbatim: the event carries no numeric
    # confidence, so the schema-required number is 0.0 — never null
    "confidence_before": src_pend.get("classification_confidence") or 0.0,
    "notes": f"objective link declined: {pay_pend['objective_id']}",
}], holder="apply-choices")
res_pend = bp.resolve_proposal(ws, id_pend, "declined",
                               resolved_by=USER, source_skill="apply-choices")
check(res_pend["status"] == "resolved"
      and res_pend["user_action"] == "declined",
      f"the pending_review-only row declines clean: {res_pend}")
last_fb = json.loads((ws / "_hq" / "data" / "classifier_feedback.jsonl")
                     .read_text(encoding="utf-8").splitlines()[-1])
check(isinstance(last_fb["confidence_before"], (int, float))
      and last_fb["confidence_before"] == 0.0,
      f"confidence_before rides the 0.0 fallback as a schema-valid NUMBER, "
      f"never null: {last_fb}")
check(_open_link_rows(ws) == [], "no open objective_link rows remain")

print(f"OK — {PASS} checks passed")
