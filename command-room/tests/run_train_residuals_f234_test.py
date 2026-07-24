#!/usr/bin/env python3
"""Train-merge review residuals F-2 / F-3 / F-4 (REVIEW_TRAIN_MERGE_2026-07-21).

Prose IS the executable layer in this product (the instruction-layer-gap
gotcha), so the F-2/F-3 pins grep the instruction files the way the battery
greps code.

F-2 — orchestrator-commitments.md re-keyed draft→nudge on delegated rows:
  the four ruled sites (§417 kind filter, §476 meeting_today exemption d,
  §588 Phase 7, §958 action sets) plus §478's meeting-linked pending_review
  cluster (bare-confirm → the driver's ownership cluster). The stale phrases
  are pinned ABSENT and the prose is keyed to the driver's actual verb lists
  (_DELEGATED_VERBS / _REVIEW_VERBS) so a future verb change trips this test.

F-3 — apply-choices `add email then send` handler row now says the nudge body
  is COMPOSED (compose-on-click chain) before send is enabled on a
  driver-degraded delegated row.

F-4 — DELIBERATE CHOICE (ruled 2026-07-22): `nudge` stays OUT of renderer
  Gate 6's _SEND_CLASS_ACTIONS, because the WG1-B D-B4 moves adapter
  legitimately emits To-less nudge rows on scheduled staff-meeting fires
  (compose-on-click resolves the address at dispatch). Both directions are
  fenced here: (a) the frozenset excludes nudge and the To-less moves row
  still renders end to end; (b) the waiting-on driver NEVER emits `nudge`
  without a resolved To: (its degrade is the enforcement); (c) no comment
  claims Gate 6 covers nudge anymore.

G14: fixture dates relative to today. Placeholder names only (Bo Sample /
Cara Sample / Rio Placeholder). House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import chat_output_renderer as cor  # noqa: E402
import relationship_moves as rm  # noqa: E402
import surface_drivers as sd  # noqa: E402
from widget_transport import render_and_persist  # noqa: E402

ORCH = (ROOT / "skills" / "enable-command-room-schedules" / "references"
        / "orchestrator-commitments.md").read_text(encoding="utf-8")
APPLY = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(
    encoding="utf-8")
DRIVER_SRC = (ROOT / "shared" / "scripts" / "surface_drivers.py").read_text(
    encoding="utf-8")

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


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    # =======================================================================
    # F-2 — the delegated-row prose is re-keyed draft→nudge, all sites
    # =======================================================================
    print("[F-2] orchestrator-commitments.md delegated-row prose")

    # The exact stale phrases the review named, pinned ABSENT.
    for stale in (
        "(`draft` / `mark received` / `snooze 3d` / `add to my plate`)",
        "their `draft` verb composes",
        "`draft` (manual nudge",
        "`draft` composes a nudge",
        "REVIEW action cluster (`confirm`",
    ):
        check(f"stale phrase gone from orchestrator prose: {stale!r}",
              stale not in ORCH)

    # Every line describing delegated-task rows must key on `nudge`; `draft`
    # may appear only when the line is explicitly saying delegated rows do
    # NOT carry it (the fossil-dispatch note).
    for i, line in enumerate(ORCH.splitlines(), 1):
        low = line.lower()
        if "delegated" not in low or "`draft`" not in line:
            continue
        check(f"line {i}: delegated prose mentioning `draft` is the "
              "not-draft fossil note, and names `nudge`",
              "`nudge`" in line and ("not `draft`" in line
                                     or "not a chase" in low),
              line.strip()[:120])

    # Prose keyed to the driver's ACTUAL verb lists — a future verb change
    # must break this test so the prose gets re-keyed in the same commit.
    check("driver delegated set is the ruled D-A4 set",
          sd._DELEGATED_VERBS == ["nudge", "mark received", "snooze 3d",
                                  "add to my plate"], str(sd._DELEGATED_VERBS))
    for v in sd._DELEGATED_VERBS:
        check(f"§417 kind-filter names driver verb `{v}`",
              f"`{v}`" in ORCH)
    check("§417/§476/§958 all carry the no-email degrade",
          ORCH.count("degrades to") >= 2
          and "`add email then send`" in ORCH)
    check("§476 exemption (d) keys the meeting-linked delegated set on nudge",
          "meeting-linked DELEGATED task (owner ≠ M, kind task) renders with "
          "the delegated set (`nudge`" in ORCH)
    check("§588 Phase 7: the nudge composes at dispatch, never at fire time",
          "their `nudge` verb (WG1-A D-A4) composes the chase on demand"
          in ORCH)
    check("§958 action set leads the delegated row with nudge",
          "`nudge` (WG1-A D-A4 — manual chase, composed on CLICK" in ORCH)

    # §478 — meeting-linked pending_review rows carry the driver's ownership
    # cluster, exactly _REVIEW_VERBS, never bare-confirm. (Pin moved with
    # policy at the UXR1 merge: D1, M ruling 2026-07-21, slimmed the tail
    # from five — `not relevant` / `add to my plate` left the EMISSION only,
    # wire ids stay registered for persisted widgets.)
    check("driver review cluster is the ruled ownership cluster (D1 slim)",
          sd._REVIEW_VERBS == ["mine", "theirs to [name]", "drop",
                               "snooze 3d"],
          str(sd._REVIEW_VERBS))
    ownership = " / ".join(f"`{v}`" for v in sd._REVIEW_VERBS)
    check("§478 names the ownership cluster verbatim",
          ownership in ORCH, ownership)

    # Cosmetic site: the driver comment at the Delegated section.
    check("surface_drivers comment re-keyed (no '`draft` composes a nudge')",
          "`draft` composes a nudge" not in DRIVER_SRC)

    # =======================================================================
    # F-3 — add-email-then-send composes the nudge body on degraded rows
    # =======================================================================
    print("[F-3] apply-choices `add email then send` handler contract")
    row_line = next((ln for ln in APPLY.splitlines()
                     if ln.startswith("| `add email then send`")), "")
    check("handler row found in the input table", bool(row_line))
    check("degraded delegated rows compose the nudge body before send",
          "COMPOSED" in row_line and "compose-on-click chain" in row_line,
          row_line[:160])
    check("send is enabled only AFTER the compose",
          "THEN enable `send`" in row_line)
    check("body-less straight-to-send is banned in words",
          "Never transition a body-less row" in row_line)

    # =======================================================================
    # F-4 — nudge is DELIBERATELY not send-class; both directions fenced
    # =======================================================================
    print("[F-4] Gate 6 / nudge — the ruled deliberate choice")

    # (a) The frozenset excludes nudge. If you are here because you added it:
    # the WG1-B moves adapter emits To-less nudge rows on scheduled
    # staff-meeting fires — adding nudge to _SEND_CLASS_ACTIONS DataShapeErrors
    # that whole surface. F-4 ruling 2026-07-22 keeps the driver-level degrade
    # as the enforcement for delegated rows.
    check("`nudge` stays OUT of _SEND_CLASS_ACTIONS (F-4 ruled choice — "
          "the moves adapter's To-less rows are legitimate)",
          "nudge" not in cor._SEND_CLASS_ACTIONS,
          str(sorted(cor._SEND_CLASS_ACTIONS)))
    check("the send-class trio is unchanged",
          cor._SEND_CLASS_ACTIONS == frozenset(
              {"send", "draft", "edit then send"}))

    # (a, live) A To-less moves-shaped nudge row renders end to end — the
    # exact scheduled staff-meeting path the frozenset extension would break.
    ws = Path(tempfile.mkdtemp(prefix="f234_moves_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "version": 1, "orgs": [], "threads": [],
        "people": [{"id": "person_201",
                    "canonical_name": "Rio Placeholder"}],
    }), encoding="utf-8")
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    moves = rm.moves_rows_from_candidates(
        [{"person_id": "person_201", "score": 0.4,
          "components": {"dormancy": 0.4}}], ws)
    check("moves adapter emits a nudge row with NO To: metadata",
          len(moves) == 1 and "nudge" in moves[0]["actions"]
          and not moves[0].get("metadata"))
    view = sd.build_staff_meeting_view(ws, moves_rows=moves)
    try:
        t = render_and_persist(data_view=view, wrapper="fragment",
                               persist_dir=tempfile.mkdtemp(),
                               name_hint="staff-meeting", page=1,
                               page_size=10)
        check("To-less nudge row renders through Gate 6 without a raise",
              "Rio Placeholder" in t["html"])
    except Exception as exc:  # DataShapeError included
        check("To-less nudge row renders through Gate 6 without a raise",
              False, f"{type(exc).__name__}: {exc}")

    # (b) The waiting-on driver never emits `nudge` without a resolved To: —
    # its degrade IS the enforcement Gate 6 no longer claims.
    ws2 = Path(tempfile.mkdtemp(prefix="f234_wo_"))
    (ws2 / "_hq" / "data").mkdir(parents=True)
    (ws2 / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "version": 1, "orgs": [],
        "people": [
            {"id": "person:user", "canonical_name": "Sam Sample",
             "is_primary_user": True},
            {"id": "person:bo", "canonical_name": "Bo Sample",
             "emails": ["bo@example.com"]},
            {"id": "person:cara", "canonical_name": "Cara Sample"},
        ],
    }), encoding="utf-8")
    evs = [
        {"type": "commitment", "seq": 1, "ts": _ago(4),
         "source_skill": "meeting-notes",
         "data": {"id": "c_bo", "title": "Bo ships the mapping doc",
                  "owner_id": "person:bo", "kind": "task"}},
        {"type": "commitment", "seq": 2, "ts": _ago(4),
         "source_skill": "meeting-notes",
         "data": {"id": "c_cara", "title": "Cara reviews the deck",
                  "owner_id": "person:cara", "kind": "task"}},
    ]
    with (ws2 / "_hq" / "data" / "events.jsonl").open(
            "w", encoding="utf-8") as f:
        for e in evs:
            f.write(json.dumps(e) + "\n")
    wo = sd.build_waiting_on_view(str(ws2))
    rows = [it for s in wo["sections"] for it in s.get("items") or []]
    nudge_rows = [r for r in rows if "nudge" in (r.get("actions") or [])]
    check("driver fixture produced a live nudge row", len(nudge_rows) == 1)
    for r in nudge_rows:
        to = next((v for k, v in r.get("metadata") or [] if k == "To"), "")
        check(f"every driver nudge row carries a resolved To: ({r['n']})",
              bool(cor._EMAIL_REGEX.match(to)), repr(to))
    degraded = [r for r in rows
                if "add email then send" in (r.get("actions") or [])]
    check("no-email delegated row degrades (nudge absent, recovery verb "
          "present)",
          len(degraded) == 1
          and "nudge" not in degraded[0]["actions"])

    # (c) No source comment still claims Gate 6 enforces nudge.
    check("driver comment no longer claims Gate 6 covers the nudge To:",
          "send-class\n            # (renderer Gate 6" not in DRIVER_SRC
          and "the composed draft is send-class" not in DRIVER_SRC)

    print()
    if failures:
        print(f"{len(failures)} FAILED of {checks}")
        return 1
    print(f"ALL F-2/F-3/F-4 residual tests PASSED ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
