#!/usr/bin/env python3
"""FB-plumbing item 6 — the daily My Plate chat gets its own one-command driver.

My Plate (CTS1 Surface 2; orchestrator-my-plate) was assembled live by the
orchestrator every fire with inline partition + render scripts — the same
hand-build FB-15 killed for Waiting On. This gives it the matching driver:
`surface_drivers.build_my_plate_view` / `run_surface("my-plate", …)`, the exact
`build_waiting_on_view` shape (deterministic connector-free core + orchestrator-
supplied connector rows) riding the FB-7 `--fired-via` receipt path.

Asserts:
  - the partition drives the surface: waiting-on rows (owner != user) never
    appear, an owner-me task lands in the PERSONAL group, and a
    counterparty-unresolved promise lands in the PROMISED group with the
    reassign/make-task fixup verbs;
  - orchestrator-supplied `status_rows` (connector-dependent email cards) lead
    the PROMISED section verbatim;
  - the Personal cap holds and surfaces the "+N more — say 'show my plate'"
    footer; a lifted cap shows everything;
  - the view renders + persists through the transport (no DataShapeError /
    CanonicalActionError), a scheduled/manual page-1 invocation writes the
    `my-plate` pack_run receipt, omitting fired_via renders only, pages 2+ never
    receipt;
  - the CLI accepts the surface + --status-json + --personal-cap + --fired-via;
  - the my-plate orchestrator REFERENCES the driver (no more inline builder).

G14: every fixture timestamp is computed relative to today. Placeholder names
only. House convention: non-zero exit = fail.
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

import receipts as R  # noqa: E402  (parity with the waiting-on driver test)
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


def _workspace(n_personal: int = 1) -> str:
    """A synthetic workspace with one of each My Plate row class + one
    waiting-on row that must NOT surface here (owner != user)."""
    d = Path(tempfile.mkdtemp(prefix="my_plate_"))
    (d / "_hq" / "data").mkdir(parents=True)
    (d / "_hq" / "data" / "entities.json").write_text(json.dumps({
        "people": [
            {"id": "person_001", "canonical_name": "Sam Sample",
             "is_primary_user": True},
            {"id": "person_002", "canonical_name": "Bo Sample"},
        ],
        "orgs": [], "version": 1,
    }), encoding="utf-8")
    rows = [
        # owner-me task -> PERSONAL
        {"type": "commitment", "seq": 1, "ts": _ago(6),
         "source_skill": "meeting-notes",
         "data": {"id": "c_self", "title": "Refresh the data pull",
                  "owner_id": "person_001", "kind": "task"}},
        # owner-me promise with NO counterparty -> PROMISED, unresolved fixup
        {"type": "commitment", "seq": 2, "ts": _ago(5),
         "source_skill": "meeting-notes",
         "data": {"id": "c_unres", "title": "Send the deck I promised",
                  "owner_id": "person_001", "kind": "promise"}},
        # owner != user -> Waiting On, must NOT surface here
        {"type": "commitment", "seq": 3, "ts": _ago(4),
         "source_skill": "meeting-notes",
         "data": {"id": "c_theirs", "title": "Bo sends the Q2 numbers",
                  "owner_id": "person_002", "kind": "promise"}},
    ]
    # extra owner-me tasks to exercise the Personal cap
    for i in range(n_personal - 1):
        rows.append({"type": "commitment", "seq": 10 + i, "ts": _ago(3),
                     "source_skill": "meeting-notes",
                     "data": {"id": f"c_extra_{i}", "title": f"Own task {i}",
                              "owner_id": "person_001", "kind": "task"}})
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
    view = sd.build_my_plate_view(ws)

    names = _all_names(view)
    check("waiting-on row (owner != user) never surfaces on My Plate",
          "Bo sends the Q2 numbers" not in names)

    promised = _section(view, "↗ PROMISED — someone's waiting")
    check("counterparty-unresolved promise lands in PROMISED",
          promised is not None
          and any(it["name"] == "Send the deck I promised"
                  for it in promised["items"]))
    check("unresolved promise carries the reassign/make-task fixup verbs",
          promised is not None
          and promised["items"][0]["actions"] == ["reassign to [name]",
                                                   "make task", "push to [date]",
                                                   "resolved", "drop", "snooze 3d"])

    personal = _section(view, "PERSONAL — your own list")
    check("owner-me task lands in PERSONAL",
          personal is not None
          and any(it["name"] == "Refresh the data pull"
                  for it in personal["items"]))
    check("personal row carries the owner-me act verbs (no drafts; "
          "my-list retired — MLK1)",
          personal is not None
          and personal["items"][0]["actions"] == ["resolved", "push to [date]",
                                                   "prep deep work", "promote",
                                                   "snooze 3d"])

    h = {c["label"]: c["value"] for c in view["counters"]}
    check("header counts come from count_commitments (you_owe includes both)",
          h.get("On your plate") == 2, str(h))
    check("promised + personal counters expose the two groups",
          h.get("Promised") == 1 and h.get("Personal") == 1, str(h))
    check("source_skill stamps the shared commitments src",
          view["source_skill"] == "commitments")
    check("promised renders BEFORE personal (someone's waiting first)",
          [s["title"] for s in view["sections"]].index(
              "↗ PROMISED — someone's waiting")
          < [s["title"] for s in view["sections"]].index(
              "PERSONAL — your own list"))

    # status_rows appended verbatim as the LEADING Promised rows
    status = [{"n": 1, "name": "Sam", "subject": "Send Q2 deck",
               "metadata": [["To", "sam@example.com"], ["Subject", "Q2 deck"]],
               "body_lines": ["Here's where the deck stands."],
               "actions": ["send", "draft", "snooze 3d"]}]
    view_s = sd.build_my_plate_view(ws, status_rows=status)
    promised_s = _section(view_s, "↗ PROMISED — someone's waiting")
    check("status_rows lead the PROMISED section verbatim",
          promised_s["items"][0]["name"] == "Sam"
          and promised_s["items"][0]["actions"] == ["send", "draft", "snooze 3d"])
    check("the deterministic unresolved row still follows the status draft",
          any(it["name"] == "Send the deck I promised"
              for it in promised_s["items"]))

    # Personal cap + footer
    ws_big = _workspace(n_personal=10)
    view_cap = sd.build_my_plate_view(ws_big, personal_cap=3)
    pcap = _section(view_cap, "PERSONAL — your own list")
    check("personal group is capped", len(pcap["items"]) == 3, repr(len(pcap["items"])))
    check("capped personal group carries the show-my-plate tail",
          "show my plate" in (pcap.get("footer_note") or ""),
          repr(pcap.get("footer_note")))
    view_full = sd.build_my_plate_view(ws_big, personal_cap=999)
    pfull = _section(view_full, "PERSONAL — your own list")
    check("lifting the cap shows every personal row (no footer)",
          len(pfull["items"]) == 10 and pfull.get("footer_note") is None,
          repr(len(pfull["items"])))

    # render + persist + receipt (FB-7 parity with the other surfaces)
    t_manual = sd.run_surface("my-plate", ws, status_rows=status,
                              fired_via="manual")
    check("view renders + persists through the transport (no DataShapeError)",
          "cr-action" in t_manual["html"])
    check("manual page-1 invocation writes the my-plate pack_run receipt",
          (t_manual.get("receipt") or {}).get("status") == "written"
          and t_manual["receipt"]["task_id"] == "my-plate")

    ws2 = _workspace()
    t_sched = sd.run_surface("my-plate", ws2, fired_via="scheduled")
    check("scheduled invocation receipts too (only fired_via differs)",
          (t_sched.get("receipt") or {}).get("status") == "written"
          and t_sched["receipt"]["fired_via"] == "scheduled")

    t_p2 = sd.run_surface("my-plate", ws2, page=2, fired_via="scheduled")
    check("pages 2+ never receipt", t_p2.get("receipt") is None)

    t_plain = sd.run_surface("my-plate", ws2)
    check("omitting fired_via renders only (legacy callers unchanged)",
          t_plain.get("receipt") is None and "cr-action" in t_plain["html"])

    check("my-plate maps to the my-plate task",
          sd._SURFACE_TASKS["my-plate"] == "my-plate")

    # CLI: surface choice + --status-json + --personal-cap + --fired-via
    ws3 = _workspace()
    status_file = Path(tempfile.mkdtemp()) / "status.json"
    status_file.write_text(json.dumps(status), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "shared" / "scripts" / "surface_drivers.py"),
         "my-plate", "--workspace", ws3, "--status-json", str(status_file),
         "--personal-cap", "5", "--fired-via", "manual"],
        capture_output=True, text=True, encoding="utf-8",
        env={**__import__("os").environ, "PYTHONUTF8": "1",
             "PYTHONIOENCODING": "utf-8"},
    )
    check("CLI exits 0 for the my-plate surface", proc.returncode == 0,
          proc.stderr[-300:])
    check("CLI emits the widget markers + the CR-RECEIPT line",
          "CR-WIDGET-HTML-BEGIN" in proc.stdout
          and "CR-RECEIPT:" in proc.stdout)

    # The orchestrator must REFERENCE the driver (no more inline builder).
    orch = (ROOT / "skills" / "enable-command-room-schedules" / "references"
            / "orchestrator-my-plate.md").read_text(encoding="utf-8")
    check("orchestrator references the surface_drivers my-plate driver",
          "surface_drivers" in orch
          and ("build_my_plate_view" in orch or "my-plate" in orch))

    print()
    if failures:
        print(f"FAIL — {len(failures)}/{checks} my-plate driver checks failed")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"OK — all {checks} my-plate driver checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
