#!/usr/bin/env python3
"""FB-7 — scheduled/manual receipt parity for the staff-meeting surface.

The 2026-07-16 live health check caught the staff-meeting SCHEDULED fire
rendering its widget but never recording its work: the pack_run receipt
lived in a prose orchestrator step AFTER the widget post, where the STOP
contract ends the turn. The manual fire (system-health Step 5) receipted
fine. Fix: the receipt writes INSIDE `surface_drivers.run_surface()` when
the caller passes its run mode (`fired_via`) — render and receipt are one
invocation, and both paths go through the one chokepoint.

Asserts:
  - a scheduled page-1 driver invocation writes the SAME canonical pack_run
    receipt a manual invocation writes (only fired_via differs)
  - pages 2+ (`show more`) never receipt
  - omitting fired_via renders only (legacy callers unchanged)
  - a non-manual re-run inside the RV-3 guard window never double-receipts;
    manual re-fires still receipt (F-08 — two real back-to-back runs)
  - the commitments surface maps to the commitment-triage task
  - the CLI accepts --fired-via and emits the CR-RECEIPT line after the
    HTML markers; without the flag the line is absent

G14: every fixture timestamp is computed relative to today — never
hardcoded. Placeholder names only (Acme / Sam Sample).

House convention: non-zero exit = fail.
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


def _proposal(i: int, days_ago: int = 2) -> dict:
    # G14: opened_at computed relative to today (TTL is 14d from ts — a
    # hardcoded date would silently expire out of the queue).
    ts = (dt.datetime.now(dt.timezone.utc)
          - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "seq": 100 + i, "ts": ts, "type": "brain_proposal",
        "source_skill": "cr-brain",
        "data": {
            "proposal_id": f"bp_{i}", "kind": "deal_creation",
            "detector": "deal_signal", "tier": "confirm",
            "fingerprint": f"fp_{i}", "title": f"Acme Co {i}",
            "render_line": ("likely deal · proposal language in your recent "
                            "sent mail · no pipeline record"),
            "action_tuples": [{"action": "confirm proposal"},
                              {"action": "dismiss proposal"},
                              {"action": "snooze proposal 7d"}],
            "org_id": f"org_{i}",
        },
    }


def make_workspace(n_proposals: int) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="fb7_ws_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for i in range(1, n_proposals + 1):
            f.write(json.dumps(_proposal(i)) + "\n")
    return ws


def staff_receipts(ws: Path) -> list[dict]:
    return R.iter_receipts(ws, task_ids=["staff-meeting"])


def main() -> int:
    # ---- scheduled fire: receipt writes inside the driver call --------------
    ws_sched = make_workspace(3)
    t = sd.run_surface("staff-meeting", ws_sched, fired_via="scheduled")
    check("scheduled page-1 fire returns receipt info on the transport",
          t.get("receipt", {}).get("status") == "written", f"{t.get('receipt')}")
    rs = staff_receipts(ws_sched)
    check("scheduled fire wrote exactly one receipt", len(rs) == 1, f"{len(rs)}")
    if rs:
        check("receipt is a pack_run", rs[0]["type"] == "pack_run")
        check("receipt fired_via is scheduled", rs[0]["fired_via"] == "scheduled")
        data = rs[0]["raw"]["data"]
        check("receipt task_id + kind are canonical",
              data.get("task_id") == "staff-meeting"
              and data.get("kind") == "staff-meeting")
        check("receipt surfaced == queue rows rendered",
              data.get("surfaced") == 3, f"{data.get('surfaced')}")

    # ---- RV-3 guard: a non-manual re-run never double-receipts --------------
    t2 = sd.run_surface("staff-meeting", ws_sched, fired_via="scheduled")
    check("non-manual re-run inside the guard window is deduped",
          t2.get("receipt", {}).get("status") == "deduped_refire",
          f"{t2.get('receipt')}")
    check("still exactly one scheduled receipt after the re-run",
          len(staff_receipts(ws_sched)) == 1)

    # ---- manual fires never dedup (F-08: back-to-back real runs) ------------
    t3 = sd.run_surface("staff-meeting", ws_sched, fired_via="manual")
    check("manual fire after a scheduled one writes its receipt",
          t3.get("receipt", {}).get("status") == "written")
    t4 = sd.run_surface("staff-meeting", ws_sched, fired_via="manual")
    check("second back-to-back manual fire also writes (never deduped)",
          t4.get("receipt", {}).get("status") == "written")
    check("three receipts total on the shared workspace",
          len(staff_receipts(ws_sched)) == 3)

    # ---- parity: the manual path writes the SAME receipt shape --------------
    ws_man = make_workspace(3)
    sd.run_surface("staff-meeting", ws_man, fired_via="manual")
    rm = staff_receipts(ws_man)
    check("manual fire wrote exactly one receipt", len(rm) == 1)
    if rs and rm:
        d_sched, d_man = rs[0]["raw"]["data"], rm[0]["raw"]["data"]
        check("scheduled and manual receipts carry identical field sets",
              set(d_sched) == set(d_man),
              f"{sorted(set(d_sched) ^ set(d_man))}")
        same = {k: v for k, v in d_sched.items() if k != "fired_via"}
        check("scheduled and manual receipts differ ONLY in fired_via",
              same == {k: v for k, v in d_man.items() if k != "fired_via"}
              and d_man["fired_via"] == "manual")
        check("both receipts are the same event type",
              rs[0]["raw"]["type"] == rm[0]["raw"]["type"] == "pack_run")

    # ---- pages 2+ (`show more`) never receipt --------------------------------
    ws_pages = make_workspace(12)
    tp = sd.run_surface("staff-meeting", ws_pages, page=2, page_size=5,
                        fired_via="scheduled")
    check("page-2 fire renders a later page",
          (tp.get("pagination") or {}).get("page") == 2)
    check("page-2 fire attaches no receipt info", "receipt" not in tp)
    check("page-2 fire wrote no receipt", len(staff_receipts(ws_pages)) == 0)

    # ---- omitting fired_via = render only (legacy callers unchanged) --------
    ws_legacy = make_workspace(2)
    tl = sd.run_surface("staff-meeting", ws_legacy)
    check("no fired_via → no receipt info on the transport", "receipt" not in tl)
    check("no fired_via → no receipt written",
          len(staff_receipts(ws_legacy)) == 0)

    # ---- bad fired_via rejected BEFORE anything renders/persists ------------
    ws_bad = make_workspace(1)
    try:
        sd.run_surface("staff-meeting", ws_bad, fired_via="bogus")
        check("invalid fired_via raises", False)
    except ValueError:
        check("invalid fired_via raises", True)
    check("invalid fired_via persisted no widget audit file",
          not (ws_bad / "_hq" / ".system" / "widgets").exists())

    # ---- commitments surface maps to the commitment-triage task -------------
    ws_ct = Path(tempfile.mkdtemp(prefix="fb7_ct_"))
    info = sd._log_fire_receipt(
        ws_ct, "commitments",
        {"sections": [{"items": [{"n": 1}, {"n": 2}]}]}, "scheduled")
    check("commitments surface receipts under commitment-triage",
          info["task_id"] == "commitment-triage" and info["surfaced"] == 2)
    check("commitment-triage receipt readable through the canonical reader",
          len(R.iter_receipts(ws_ct, task_ids=["commitment-triage"])) == 1)

    # ---- CLI: --fired-via emits CR-RECEIPT after the HTML markers -----------
    ws_cli = make_workspace(2)
    cli = [sys.executable, str(ROOT / "shared" / "scripts" / "surface_drivers.py"),
           "staff-meeting", "--workspace", str(ws_cli), "--page", "1",
           "--fired-via", "manual"]
    proc = subprocess.run(cli, capture_output=True, encoding="utf-8", errors="replace")
    check("CLI with --fired-via exits 0", proc.returncode == 0, proc.stderr[-300:])
    out = proc.stdout or ""
    check("CLI stdout keeps the marker contract",
          "CR-PAGINATION: " in out and "CR-WIDGET-HTML-BEGIN" in out
          and "CR-WIDGET-HTML-END" in out)
    receipt_lines = [l for l in out.splitlines() if l.startswith("CR-RECEIPT: ")]
    check("CLI emits exactly one CR-RECEIPT line", len(receipt_lines) == 1)
    if receipt_lines:
        payload = json.loads(receipt_lines[0][len("CR-RECEIPT: "):])
        check("CR-RECEIPT payload confirms the written manual receipt",
              payload.get("status") == "written"
              and payload.get("fired_via") == "manual"
              and payload.get("task_id") == "staff-meeting")
        check("CR-RECEIPT line comes after the END marker",
              out.index("CR-WIDGET-HTML-END") < out.index("CR-RECEIPT: "))
    check("CLI fire wrote the receipt to the substrate",
          len(staff_receipts(ws_cli)) == 1)

    ws_cli2 = make_workspace(1)
    cli2 = [sys.executable, str(ROOT / "shared" / "scripts" / "surface_drivers.py"),
            "staff-meeting", "--workspace", str(ws_cli2), "--page", "1"]
    proc2 = subprocess.run(cli2, capture_output=True, encoding="utf-8", errors="replace")
    check("CLI without --fired-via exits 0", proc2.returncode == 0,
          proc2.stderr[-300:])
    check("CLI without --fired-via emits no CR-RECEIPT line",
          "CR-RECEIPT: " not in (proc2.stdout or ""))
    check("CLI without --fired-via wrote no receipt",
          len(staff_receipts(ws_cli2)) == 0)

    if failures:
        print(f"\nstaff-meeting receipt parity FAIL — {len(failures)} of "
              f"{checks} failed")
        return 1
    print(f"staff-meeting receipt parity (FB-7): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
