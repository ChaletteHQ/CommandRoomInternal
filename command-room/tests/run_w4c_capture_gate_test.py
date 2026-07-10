#!/usr/bin/env python3
"""
v4.6.1 W4c — capture relevance gate + observed tier (the volume fix).

Evidence base: F-19's "~40 third-party items are good Not-mine candidates" on
M's own data, plus the Brandon volume story (meeting-heavy user, ~half of
aggressive capture belongs to other attendees — W4b's confirm section would be
a 20-row daily chore without a capture-side gate).

Regression contract (the spec's list, verbatim):
  1. third-party item  -> observed tier, ZERO open items (no count, no open
     row — verified against the real open-set loader, not narration);
  2. party item        -> open exactly as today;
  3. due-date / money item -> surfaces (opens) in EVERY mode incl. per-org
     observed-only override (asymmetric caution rail) — and the observed
     builder REFUSES such items in code;
  4. mode directives change gate behavior (party-only default /
     team-delegation / track-everything, per-org overrides beat global);
  5. corroboration promotes an observed item to a REAL commitment with
     pending_review: true (entering W4b's confirm flow by data contract —
     nothing rendered here), idempotently;
  6. consolidation invariants: the shared gate is the ONE implementation
     (sweep + slack + meeting builders all reject/stamp identically), and
     amber items are SILENT (observed, no pending_review ask).

Plus: fail-open when the primary user is unresolvable (Bug #102 family — a
broken entities file must never silently swallow real commitments), audit
counts (observed_counts), and propose-only verb tuning.

House conventions: check(label, cond), exit 1 on any failure, auto-discovered
by run_all.py. Uses the workspace_mini fixture (real substrate shapes).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from output_exercise_lib import copy_fixture  # noqa: E402
import capture_gate as cg  # noqa: E402
import session_sweep as ss  # noqa: E402
from cru_match import load_open_commitments  # noqa: E402

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


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _events(ws: Path) -> list[dict]:
    out = []
    for line in _events_path(ws).read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _item(summary, data, **top):
    it = {"session_id": "w4c", "type": "commitment", "summary": summary, "data": data}
    it.update(top)
    return it


# Fixture facts (tests/fixtures/workspace_mini): person_001 = Sam Sample, the
# primary user; org_001 relationship_type "self" (person_002 Rio = team);
# person_003 Dustin / person_004 Mira = org_002 (customer).


def test_third_party_goes_observed_zero_open():
    print("test_third_party_goes_observed_zero_open — regression 1")
    ws = copy_fixture()
    base_open = len(load_open_commitments(_events_path(ws)))
    r = ss.sweep_and_receipt(
        ws,
        [_item("Dustin to send Mira the vendor comparison",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_003", "counterparty_id": "person_004"})],
        sessions_scanned=1,
    )
    check("routed to the observed tier",
          r["by_type"] == {"commitment_observed": 1}, repr(r["by_type"]))
    check("open set UNCHANGED (no open item, no count)",
          len(load_open_commitments(_events_path(ws))) == base_open)
    obs = [e for e in _events(ws) if e["type"] == "commitment_observed"]
    check("observed event on disk, searchable", len(obs) == 1)
    d = obs[0]["data"]
    check("carries tier + deterministic obs_ id",
          d.get("tier") == "observed" and str(d.get("id", "")).startswith("obs_"),
          repr(d))
    check("no confirm-row flags on an observed item (silent by definition)",
          "pending_review" not in d and "status" not in d, repr(d))
    check("reason recorded for the audit trail",
          bool(d.get("observed_reason")))
    # Idempotent re-run — same item recovers nothing.
    r2 = ss.sweep_and_receipt(
        ws,
        [_item("Dustin to send Mira the vendor comparison",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_003", "counterparty_id": "person_004"})],
        sessions_scanned=1,
    )
    check("re-run dedups the observed item", r2["events_recovered"] == 0, repr(r2))


def test_party_item_opens_as_today():
    print("test_party_item_opens_as_today — regression 2")
    ws = copy_fixture()
    base_open = len(load_open_commitments(_events_path(ws)))
    r = ss.sweep_and_receipt(
        ws,
        [_item("Send Mira the pricing deck",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_001", "counterparty_id": "person_004"}),
         # self-owed task with no party fields — the self-owed presumption:
         _item("Update the investor tracker", {"kind": "task", "no_due": True})],
        sessions_scanned=1,
    )
    check("both party items open as commitments",
          r["by_type"] == {"commitment": 2}, repr(r["by_type"]))
    check("open set grew by exactly 2",
          len(load_open_commitments(_events_path(ws))) == base_open + 2)


def test_caution_rail_beats_every_mode():
    print("test_caution_rail_beats_every_mode — regression 3 (due/money always surface)")
    dated = {"kind": "promise", "due": "2099-01-15", "title": "Dustin to send Mira the SOW",
             "owner_id": "person_003", "counterparty_id": "person_004"}
    money = {"kind": "promise", "no_due": True,
             "title": "Dustin owes the venue a $2,500 deposit",
             "owner_id": "person_003", "counterparty_id": "person_004"}
    for mode in cg.CAPTURE_MODES:
        for payload, tag in ((dated, "due-date"), (money, "money")):
            v = cg.classify_capture(
                dict(payload), mode=mode, user_id="person_001",
                known_ids={"person_003", "person_004"},
            )
            check(f"{tag} item opens in mode={mode}", v["tier"] == "open", repr(v))
    # ...and via a per-org observed-only override:
    v = cg.classify_capture(dict(dated), mode=cg.MODE_PARTY_ONLY,
                            org_override=cg.MODE_OBSERVED_ONLY,
                            user_id="person_001",
                            known_ids={"person_003", "person_004"})
    check("due-date item opens even under an observed-only org override",
          v["tier"] == "open", repr(v))
    # The observed builder enforces the rail in code:
    for payload, tag in ((dated, "due-date"), (money, "money")):
        try:
            cg.build_observed_event(payload["title"], source_ref="session:x",
                                    reason="t", extra_data={"due": payload.get("due")}
                                    if payload.get("due") else None,
                                    evidence=payload["title"])
            check(f"observed builder refuses a {tag} item", False)
        except cg.CaptureGateError as e:
            check(f"observed builder refuses a {tag} item", "always surface" in str(e).lower()
                  or "caution rail" in str(e).lower(), str(e))
    # Conservative money detector: "10k users" is NOT money.
    v = cg.classify_capture(
        {"kind": "promise", "no_due": True, "title": "Dustin to hit 10k users",
         "owner_id": "person_003"},
        user_id="person_001", known_ids={"person_003"})
    check("bare '10k users' does not trip the money rail", v["tier"] == "observed", repr(v))


def test_mode_directives_change_gate_behavior():
    print("test_mode_directives_change_gate_behavior — regression 4")
    ws = copy_fixture()
    from skill_custom_writer import load_directives  # noqa: F401  (rail exists)
    from skill_custom_writer import add_directive

    third = {"kind": "promise", "no_due": True, "title": "Dustin to send Mira the recap",
             "owner_id": "person_003", "counterparty_id": "person_004"}
    team = {"kind": "promise", "no_due": True, "title": "Rio to draft the SOW",
            "owner_id": "person_002", "counterparty_id": "person_004"}

    ctx = cg.workspace_capture_context(ws)
    check("default mode is party-only", ctx["mode"] == cg.MODE_PARTY_ONLY, ctx["mode"])
    check("team item observed under the default",
          cg.classify_capture(dict(team), mode=ctx["mode"], user_id=ctx["user_id"],
                              team_ids=ctx["team_ids"], known_ids=ctx["known_ids"]
                              )["tier"] == "observed")

    add_directive(ws, cg.CAPTURE_POLICY_SKILL,
                  "capture mode: team-delegation", origin="explicit")
    ctx = cg.workspace_capture_context(ws)
    check("directive flips mode to team-delegation", ctx["mode"] == cg.MODE_TEAM_DELEGATION)
    check("team member's promise now opens",
          cg.classify_capture(dict(team), mode=ctx["mode"], user_id=ctx["user_id"],
                              team_ids=ctx["team_ids"], known_ids=ctx["known_ids"]
                              )["tier"] == "open")
    check("third-party (non-team) still observed in team-delegation",
          cg.classify_capture(dict(third), mode=ctx["mode"], user_id=ctx["user_id"],
                              team_ids=ctx["team_ids"], known_ids=ctx["known_ids"]
                              )["tier"] == "observed")

    add_directive(ws, cg.CAPTURE_POLICY_SKILL,
                  "capture mode: track-everything", origin="explicit")
    ctx = cg.workspace_capture_context(ws)
    check("later directive wins (track-everything)", ctx["mode"] == cg.MODE_TRACK_EVERYTHING)
    check("third-party opens under track-everything (pre-W4c behavior)",
          cg.classify_capture(dict(third), mode=ctx["mode"], user_id=ctx["user_id"],
                              team_ids=ctx["team_ids"], known_ids=ctx["known_ids"]
                              )["tier"] == "open")

    # Per-org override beats global: org_003 observed-only, org_002 untouched.
    add_directive(ws, cg.CAPTURE_POLICY_SKILL,
                  "for org_003: observed-only", origin="explicit")
    check("org override resolves for its org",
          cg.resolve_capture_mode(ws, org_id="org_003") == cg.MODE_OBSERVED_ONLY)
    check("other orgs keep the global mode",
          cg.resolve_capture_mode(ws, org_id="org_002") == cg.MODE_TRACK_EVERYTHING)
    mine = {"kind": "promise", "no_due": True, "title": "send the recap to Quinn",
            "owner_id": "person_001", "counterparty_id": "person_006"}
    v = cg.classify_capture(dict(mine), mode=cg.MODE_PARTY_ONLY,
                            org_override=cg.MODE_OBSERVED_ONLY,
                            user_id="person_001", known_ids={"person_001", "person_006"})
    check("observed-only override sets aside even a party item (undated, no money)",
          v["tier"] == "observed", repr(v))

    # End-to-end: the sweep reads the directives at capture time.
    r = ss.sweep_and_receipt(ws, [_item("Dustin to send Mira the recap", dict(third))],
                             sessions_scanned=1)
    check("sweep honors the directive (track-everything -> opens)",
          r["by_type"] == {"commitment": 1}, repr(r["by_type"]))


def test_amber_is_silent_and_corroboration_promotes():
    print("test_amber_is_silent_and_corroboration_promotes — regression 5")
    ws = copy_fixture()
    base_open = len(load_open_commitments(_events_path(ws)))
    # Amber: named counterparty with no person record, user not a party.
    r = ss.sweep_and_receipt(
        ws,
        [_item("Rakesh to send Jordan the onboarding doc",
               {"kind": "promise", "no_due": True,
                "owner_external": "Rakesh", "counterparty_name": "Jordan Lee"})],
        sessions_scanned=1,
    )
    check("amber lands observed (silent), not open+pending_review",
          r["by_type"] == {"commitment_observed": 1}, repr(r["by_type"]))
    check("zero new open items / zero confirm rows",
          len(load_open_commitments(_events_path(ws))) == base_open)
    obs_id = next(e for e in _events(ws)
                  if e["type"] == "commitment_observed")["data"]["id"]

    check("no corroboration yet -> nothing to promote",
          cg.find_corroborations(ws) == [])

    # The item reappears in a LATER email (different source, shared party
    # name + strong title overlap) — the checkable rule fires.
    from event_gate import append_event
    append_event(_events_path(ws),
                 {"type": "interaction", "source_skill": "inbox-triage",
                  "data": {"summary": "Rakesh confirmed he'll send Jordan the onboarding doc",
                           "channel": "email", "source_ref": "gmail:msg_w4c",
                           "counterparty_name": "Rakesh"}},
                 holder="test")
    finds = cg.find_corroborations(ws)
    check("corroboration found (distinct source, party + content overlap)",
          len(finds) == 1 and finds[0]["observed"]["data"]["id"] == obs_id,
          repr([f['observed']['data'].get('id') for f in finds]))

    res = cg.promote_observed(ws, obs_id, corroborated_by="gmail:msg_w4c")
    check("promotion appends a real commitment", res.get("ok") is True, repr(res))
    cd = res["commitment"]["data"]
    check("promoted item enters the confirm flow by data contract "
          "(pending_review true, never rendered here)",
          cd.get("pending_review") is True and bool(cd.get("review_reason")))
    check("promoted item points back at the observed record",
          cd.get("promoted_from") == obs_id)
    check("open set grew by exactly the promoted item",
          len(load_open_commitments(_events_path(ws))) == base_open + 1)
    check("promotion is idempotent",
          cg.promote_observed(ws, obs_id).get("already") is True)
    check("promoted item leaves the corroboration queue",
          cg.find_corroborations(ws) == [])

    # A mere later meeting with the same person does NOT auto-promote —
    # prep SURFACES it (one tap away), volume stays flat.
    ws2 = copy_fixture()
    ss.sweep_and_receipt(
        ws2,
        [_item("Dustin to send Mira the vendor comparison",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_003", "counterparty_id": "person_004"})],
        sessions_scanned=1)
    append_event(_events_path(ws2),
                 {"type": "meeting", "source_skill": "past-meetings",
                  "person_ids": ["person_003", "person_004"],
                  "data": {"title": "Weekly ops sync",
                           "source_ref": "granola:mtg_w4c", "notes": "status round"}},
                 holder="test")
    check("a recurring meeting alone never auto-promotes (no content overlap)",
          cg.find_corroborations(ws2) == [])
    prep = cg.prep_context_observed(ws2, ["person_004"])
    check("...but prep context surfaces the set-aside item for those attendees",
          len(prep) == 1 and prep[0]["data"]["title"].startswith("Dustin"))


def test_audit_counts_and_tuning_proposals():
    print("test_audit_counts_and_tuning_proposals — audit line data + propose-only tuning")
    ws = copy_fixture()
    ss.sweep_and_receipt(
        ws,
        [_item("Dustin to send Mira the vendor comparison",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_003", "counterparty_id": "person_004"}),
         _item("Rakesh to send Jordan the onboarding doc",
               {"kind": "promise", "no_due": True,
                "owner_external": "Rakesh", "counterparty_name": "Jordan Lee"})],
        sessions_scanned=1)
    counts = cg.observed_counts(ws)
    check("observed_counts backs the weekly 'N set aside — review' line",
          counts["observed"] == 2 and counts["promoted"] == 0, repr(counts))
    check("by_reason splits third-party vs unattributed",
          len(counts["by_reason"]) == 2, repr(counts["by_reason"]))

    # Verb tuning: 5 captures about Mira (org_002), 4 dismissed -> proposal.
    from event_gate import append_event
    from commitment_state import close_commitment
    ep = _events_path(ws)
    cids = []
    for i in range(5):
        ev = {"type": "commitment", "source_skill": "scan-for-commitments",
              "person_ids": ["person_004"],
              "data": {"title": f"vendor follow-up item {i}", "kind": "promise",
                       "no_due": True, "owner_id": "person_004",
                       "counterparty_id": "person_004", "status": "open"}}
        append_event(ep, ev, holder="test")
    for e in _events(ws):
        if e["type"] == "commitment" and str(e["data"].get("title", "")).startswith("vendor follow-up"):
            cids.append(e["data"]["id"])
    for cid in cids[:4]:
        # Dropping from triage is an explicit user action (user_confirmed) —
        # and C4's capture-time dedup may have flagged the similar titles.
        close_commitment(ws, cid, resolved_by="person_001",
                         evidence="not mine", source_skill="commitment-triage",
                         resolution="dropped", user_confirmed=True)
    props = cg.propose_gate_directives(ws, min_items=5, min_dismiss_rate=0.7)
    check("dismiss pattern yields ONE per-org proposal",
          len(props) == 1 and props[0]["dismissed"] == 4 and props[0]["total"] == 5,
          repr(props))
    check("proposal text is an observed-only override, plain-English ask attached",
          props[0]["directive_text"].startswith("for ")
          and "observed-only" in props[0]["directive_text"]
          and "set aside" in props[0]["plain"])
    # Propose-only: nothing changed until the explicit apply.
    check("gate unchanged before approval",
          cg.resolve_capture_mode(ws, org_id="org_002") == cg.MODE_PARTY_ONLY)
    applied = cg.apply_gate_proposal(ws, props[0])
    check("one tap writes the directive", applied.get("ok") is True, repr(applied))
    check("second proposal round skips the applied override",
          cg.propose_gate_directives(ws, min_items=5, min_dismiss_rate=0.7) == [])


def test_fail_open_without_primary_user():
    print("test_fail_open_without_primary_user — Bug #102 family")
    ws = copy_fixture()
    ent_path = ws / "_hq" / "data" / "entities.json"
    raw = json.loads(ent_path.read_text(encoding="utf-8"))
    ent = raw["entities"] if isinstance(raw.get("entities"), dict) else raw
    ent.get("workspace", {}).pop("primary_user_id", None)
    ent.get("workspace", {}).pop("user_person_id", None)
    ent.get("workspace", {}).pop("first_name", None)
    ent.get("workspace", {}).pop("user_first_name", None)
    for p in ent.get("people", []):
        p.pop("is_primary_user", None)
        p.pop("is_user", None)
    ent_path.write_text(json.dumps(raw), encoding="utf-8")
    ctx = cg.workspace_capture_context(ws)
    check("unresolvable user forces track-everything (gate inert, fail-open)",
          ctx["mode"] == cg.MODE_TRACK_EVERYTHING and ctx["user_id"] is None)
    r = ss.sweep_and_receipt(
        ws,
        [_item("Dustin to send Mira the vendor comparison",
               {"kind": "promise", "no_due": True,
                "owner_id": "person_003", "counterparty_id": "person_004"})],
        sessions_scanned=1)
    check("nothing is silently swallowed into the observed tier",
          r["by_type"] == {"commitment": 1}, repr(r["by_type"]))


def test_shared_gate_is_the_one_implementation():
    print("test_shared_gate_is_the_one_implementation — regression 6 (consolidation)")
    import slack_capture as sc
    import meeting_capture as mc

    # All three builders reject the same shapes through the same gate.
    for label, fn in (
        ("sweep", lambda: ss._normalize_item(
            _item("send the deck", {"kind": "task", "no_due": True,
                                    "counterparty_id": "person_004"}), "s")),
        ("slack", lambda: sc.build_slack_commitment_event(
            "send the deck", permalink="https://x.slack.com/p1", kind="task",
            direction=sc.DIRECTION_USER_SENT, no_due=True,
            counterparty_id="person_004")),
        ("meeting", lambda: mc.build_meeting_commitment_event(
            "send the deck", source_ref="granola:m1", kind="task",
            no_due=True, counterparty_id="person_004")),
    ):
        try:
            fn()
            check(f"{label}: task-with-counterparty rejects", False)
        except ValueError as e:
            check(f"{label}: task-with-counterparty rejects",
                  "promise, not a task" in str(e), str(e))

    # ...and stamp the same inversion.
    ev = mc.build_meeting_commitment_event(
        "send the roadmap to Rakesh", source_ref="granola:m2", kind="promise",
        no_due=True, owner_id="person_001", counterparty_name="Rakesh")
    check("meeting builder stamps pending_review for an unresolved counterparty",
          ev["data"].get("pending_review") is True and bool(ev["data"].get("review_reason")))
    check("meeting builder joins resolved ids into person_ids",
          mc.build_meeting_commitment_event(
              "send the deck to Mira", source_ref="granola:m3", kind="promise",
              no_due=True, owner_id="person_001", counterparty_id="person_004"
          )["person_ids"] == ["person_001", "person_004"])


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== W4c capture relevance gate + observed tier ===")
    test_third_party_goes_observed_zero_open()
    test_party_item_opens_as_today()
    test_caution_rail_beats_every_mode()
    test_mode_directives_change_gate_behavior()
    test_amber_is_silent_and_corroboration_promotes()
    test_audit_counts_and_tuning_proposals()
    test_fail_open_without_primary_user()
    test_shared_gate_is_the_one_implementation()
    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} W4c checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
