#!/usr/bin/env python3
"""FB-15 — the daily Waiting On chat gets its own one-command driver.

The Waiting On chat (CTS1 Surface 1; orchestrator-commitments) was assembled
live by the orchestrator every fire — the ~30-command hand-build the RV round
measured for `commitments`, minus a deterministic driver. FB-15 gives it one:
`surface_drivers.build_waiting_on_view` / `run_surface("waiting-on", …)`,
matching the staff-meeting shape (deterministic core + orchestrator-supplied
connector rows) and riding the FB-7 `--fired-via` receipt path.

Asserts:
  - the partition drives the surface: owner-me rows never appear (My Plate),
    a delegated task (owner != user, kind task) lands in the Delegated section
    NAMING who we delegated to, with the manual verb set led by `draft` (the
    on-demand nudge — connector-free in the row, composed at dispatch per
    orchestrator-commitments §417/§958), and the unowned + pending_review items
    land in the confirm tail with the ownership cluster (`mine` / `theirs to
    [name]` — never the opaque person-record `confirm`).
  - orchestrator-supplied `chase_rows` (connector-dependent email cards) are
    appended verbatim as the leading Waiting On section.
  - the view renders + persists through the transport (no DataShapeError), and
    a scheduled/manual page-1 invocation writes the `waiting-on` pack_run
    receipt; omitting fired_via renders only; pages 2+ never receipt.
  - the CLI accepts the surface + --chase-json + --fired-via.

G14: every fixture timestamp is computed relative to today. Placeholder names
only (Sam / Bo Sample). House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import receipts as R  # noqa: E402
import surface_drivers as sd  # noqa: E402

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


def _workspace() -> str:
    """A synthetic workspace with one of each Waiting On row class + one
    owner-me row that must NOT surface here (it belongs to My Plate)."""
    d = Path(tempfile.mkdtemp(prefix="waiting_on_"))
    (d / "_hq" / "data").mkdir(parents=True)
    (d / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "people": [
            {"id": "person:user", "canonical_name": "Sam Sample",
             "is_primary_user": True},
            # Bo has an email on file → the delegated `draft` button is live.
            {"id": "person:bo", "canonical_name": "Bo Sample",
             "emails": ["bo@example.com"]},
            # Cara has NO email → the delegated row degrades `draft` to the
            # `add email then send` recovery verb (Bug #44).
            {"id": "person:cara", "canonical_name": "Cara Sample"},
        ],
        "orgs": [], "version": 1,
    }), encoding="utf-8")
    rows = [
        # delegated task — owner != user, effective kind task → Delegated.
        # Owner (Bo) has an email → the row carries a live `draft` button.
        {"type": "commitment", "seq": 1, "ts": _ago(5),
         "source_skill": "meeting-notes",
         "data": {"id": "c_del", "title": "Bo ships the mapping doc",
                  "owner_id": "person:bo", "kind": "task"}},
        # delegated task whose owner (Cara) has NO email → draft degrades to
        # the `add email then send` recovery verb.
        {"type": "commitment", "seq": 6, "ts": _ago(5),
         "source_skill": "meeting-notes",
         "data": {"id": "c_del2", "title": "Cara reviews the deck",
                  "owner_id": "person:cara", "kind": "task"}},
        # owed-to-you promise — owner != user, kind promise → CRU-eligible
        # waiting_on (no pre-staged draft here; the chase body rides chase_rows)
        {"type": "commitment", "seq": 2, "ts": _ago(4),
         "source_skill": "meeting-notes",
         "data": {"id": "c_owed", "title": "Bo sends the Q2 numbers",
                  "owner_id": "person:bo", "kind": "promise"}},
        # pending_review — unconfirmed → confirm tail
        {"type": "commitment", "seq": 3, "ts": _ago(3),
         "source_skill": "meeting-notes",
         "data": {"id": "c_pend", "title": "Someone owes a contract",
                  "review_reason": "unclear owner", "pending_review": True,
                  "kind": "promise"}},
        # owner-me task — belongs to My Plate, must NOT surface here
        {"type": "commitment", "seq": 4, "ts": _ago(2),
         "source_skill": "meeting-notes",
         "data": {"id": "c_mine", "title": "I file the expense report",
                  "owner_id": "person:user", "kind": "task"}},
        # unowned — owner missing, NOT pending_review → confirm tail (unowned)
        {"type": "commitment", "seq": 5, "ts": _ago(6),
         "source_skill": "meeting-notes",
         "data": {"id": "c_unowned", "title": "Whose is the vendor follow-up",
                  "kind": "promise"}},
    ]
    with (d / "_hq" / "data" / "events.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return str(d)


def _all_names(view: dict) -> set[str]:
    return {it.get("name") for sec in view["sections"]
            for it in sec.get("items") or []}


def _section(view: dict, title: str) -> dict | None:
    for sec in view["sections"]:
        if sec.get("title") == title:
            return sec
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ws = _workspace()
    view = sd.build_waiting_on_view(ws)

    names = _all_names(view)
    check("owner-me row never surfaces on Waiting On (My Plate owns it)",
          "I file the expense report" not in names)

    deleg = _section(view, "Delegated")
    deleg_by_name = ({it["name"]: it for it in deleg["items"]}
                     if deleg is not None else {})
    bo_row = deleg_by_name.get("Bo ships the mapping doc")
    cara_row = deleg_by_name.get("Cara reviews the deck")
    check("delegated task lands in the Delegated section", bo_row is not None)
    check("delegated row (owner has email) carries the manual set led by "
          "on-demand draft (FIX A)",
          bo_row is not None
          and bo_row["actions"] == ["draft", "mark received", "snooze 3d",
                                    "add to my plate"],
          bo_row and bo_row.get("actions"))
    check("live draft row carries the owner's To: (renderer Gate 6 / Bug #44)",
          bo_row is not None
          and bo_row.get("metadata") == [["To", "bo@example.com"]],
          bo_row and bo_row.get("metadata"))
    check("delegated tag names who we delegated to (owner != M) (FIX A)",
          bo_row is not None
          and "delegated to Bo Sample" in bo_row["context_tag"],
          bo_row and bo_row.get("context_tag"))
    check("delegated row with NO owner email degrades draft to "
          "`add email then send` (Bug #44), still naming the owner",
          cara_row is not None
          and cara_row["actions"] == ["add email then send", "mark received",
                                      "snooze 3d", "add to my plate"]
          and "delegated to Cara Sample" in cara_row["context_tag"],
          cara_row and cara_row.get("actions"))

    confirm = _section(view, "Needs a quick confirm")
    check("both confirm-tail classes (unowned + pending_review) render",
          confirm is not None and len(confirm["items"]) == 2,
          confirm and len(confirm["items"]))
    if confirm is not None:
        by_name = {it["name"]: it for it in confirm["items"]}
        unowned_row = by_name.get("Whose is the vendor follow-up")
        pending_row = by_name.get("Someone owes a contract")
        for label, row in (("unowned", unowned_row),
                           ("pending_review", pending_row)):
            check(f"{label} confirm row carries the ownership cluster "
                  f"(mine + theirs to [name]), not bare confirm (FIX B+E)",
                  row is not None
                  and "mine" in row["actions"]
                  and "theirs to [name]" in row["actions"]
                  and "confirm" not in row["actions"],
                  row and row.get("actions"))
        check("confirm rows use the full ownership cluster verbatim (FIX B+E)",
              unowned_row is not None
              and unowned_row["actions"] == ["mine", "theirs to [name]", "drop",
                                             "not relevant", "add to my plate"],
              unowned_row and unowned_row.get("actions"))

    h = {c["label"]: c["value"] for c in view["counters"]}
    # owed_to_you counts every owner != M item regardless of kind (spec §417:
    # the split filters SURFACING, not the canonical numbers) — the two
    # delegated tasks (Bo, Cara) + the owed promise (Bo) = 3.
    check("header counts come from count_commitments (owed_to_you=3)",
          h.get("Owed to you") == 3, str(h))
    check("source_skill stamps the shared commitments src",
          view["source_skill"] == "commitments")

    # chase_rows appended verbatim as the leading Waiting On section
    chase = [{"n": 1, "name": "Bo Sample", "subject": "Q2",
              "metadata": [["To", "bo@example.com"], ["Subject", "Re: Q2"]],
              "body_lines": ["Any update on the Q2 numbers?"],
              "actions": ["1 send", "1 draft", "1 snooze 3d"]}]
    view_c = sd.build_waiting_on_view(ws, chase_rows=chase)
    check("chase_rows lead as the Waiting On section",
          view_c["sections"][0]["title"] == "Waiting On"
          and view_c["sections"][0]["items"][0]["name"] == "Bo Sample")

    # render + persist + receipt (FB-7 parity with the other surfaces)
    t_manual = sd.run_surface("waiting-on", ws, chase_rows=chase,
                              fired_via="manual")
    check("view renders + persists through the transport (no DataShapeError)",
          "cr-action" in t_manual["html"])
    check("manual page-1 invocation writes the waiting-on pack_run receipt",
          (t_manual.get("receipt") or {}).get("status") == "written"
          and t_manual["receipt"]["task_id"] == "waiting-on")

    ws2 = _workspace()
    t_sched = sd.run_surface("waiting-on", ws2, fired_via="scheduled")
    check("scheduled invocation receipts too (only fired_via differs)",
          (t_sched.get("receipt") or {}).get("status") == "written"
          and t_sched["receipt"]["fired_via"] == "scheduled")

    t_p2 = sd.run_surface("waiting-on", ws2, page=2, fired_via="scheduled")
    check("pages 2+ never receipt", t_p2.get("receipt") is None)

    t_plain = sd.run_surface("waiting-on", ws2)
    check("omitting fired_via renders only (legacy callers unchanged)",
          t_plain.get("receipt") is None and "cr-action" in t_plain["html"])

    check("waiting-on maps to the waiting-on task", sd._SURFACE_TASKS["waiting-on"] == "waiting-on")

    # CLI: surface choice + --chase-json + --fired-via
    ws3 = _workspace()
    chase_file = Path(tempfile.mkdtemp()) / "chase.json"
    chase_file.write_text(json.dumps(chase), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "shared" / "scripts" / "surface_drivers.py"),
         "waiting-on", "--workspace", ws3, "--chase-json", str(chase_file),
         "--fired-via", "manual"],
        capture_output=True, text=True, encoding="utf-8",
        env={**__import__("os").environ, "PYTHONUTF8": "1",
             "PYTHONIOENCODING": "utf-8"},
    )
    check("CLI exits 0 for the waiting-on surface", proc.returncode == 0,
          proc.stderr[-300:])
    check("CLI emits the widget markers + the CR-RECEIPT line",
          "CR-WIDGET-HTML-BEGIN" in proc.stdout
          and "CR-RECEIPT:" in proc.stdout)

    print()
    if failures:
        print(f"FAIL — {len(failures)}/{checks} waiting-on driver checks failed")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"OK — all {checks} waiting-on driver checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
