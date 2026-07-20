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
    with the connector-free manual verbs (no `draft` — send-class, composed on
    demand), and a pending_review item lands in the confirm tail with the
    REVIEW cluster.
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
            {"id": "person:bo", "canonical_name": "Bo Sample"},
        ],
        "orgs": [], "version": 1,
    }), encoding="utf-8")
    rows = [
        # delegated task — owner != user, effective kind task → Delegated
        {"type": "commitment", "seq": 1, "ts": _ago(5),
         "source_skill": "meeting-notes",
         "data": {"id": "c_del", "title": "Bo ships the mapping doc",
                  "owner_id": "person:bo", "kind": "task"}},
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
    check("delegated task lands in the Delegated section",
          deleg is not None
          and any(it["name"] == "Bo ships the mapping doc"
                  for it in deleg["items"]))
    check("delegated row carries connector-free manual verbs, no send-class draft",
          deleg is not None
          and deleg["items"][0]["actions"] == ["mark received", "snooze 3d",
                                                "add to my list"])

    confirm = _section(view, "Needs a quick confirm")
    check("pending_review lands in the confirm tail with the REVIEW cluster",
          confirm is not None
          and confirm["items"][0]["actions"] == ["confirm", "not relevant",
                                                  "add to my list"])

    h = {c["label"]: c["value"] for c in view["counters"]}
    check("header counts come from count_commitments (owed_to_you=2)",
          h.get("Owed to you") == 2, str(h))
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
