#!/usr/bin/env python3
"""t3 FB-9 — the one-command morning-brief pack driver.

A live post-update fire skipped the mandatory brain-card widget AND the
substrate-alarm line while the pre-update scheduled fire rendered both —
per-step instruction-layer MUSTs (FS-09) don't survive an orchestrator
that stops early. `surface_drivers.build_morning_brief_pack` assembles,
validates, and persists the brief's substrate blocks in ONE call whose
output the orchestrator relays byte-exact.

FB-20 (M's ruling 2026-07-16 — "the morning brief should just be a morning
brief") retired the confirm card from this surface: the pack is PROSE ONLY.
The card assertions below were rewritten, not deleted — the FB-9 contract
(one call, every mandatory block, skip-proof) is unchanged and still the
reason this driver exists; only the card blocks became prose blocks.
The no-widget contract itself is pinned in run_t32_brief_relay_test.py, and
the money/pointer semantics in run_fb20_readonly_brief_test.py.

Asserts:
  - the pack carries every mandatory block key (alarm_lines / changed /
    brief_state / watchdog_line / money_lines / queue_pointer)
  - the driver logs the `brief_state` audit event exactly ONCE per call
    (the Step-3d derivation moved inside)
  - scheduled AND manual mode return the same prose pack — no widget
    transport on either (FB-20: mode no longer changes the card's shape,
    because there is no card)
  - the CHANGED window opens at the last brief receipt and the feed's
    lines ride the pack
  - the pack audit copy persists under `_hq/.system/briefs/`
  - the CLI emits CR-BRIEF-PACK and nothing else

G14: every fixture timestamp is computed relative to today. Placeholder
names only (Acme / Sam Sample).

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

import surface_drivers as sd  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _iso(days_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _proposal(i: int) -> dict:
    return {
        "seq": 100 + i, "ts": _iso(2), "type": "brain_proposal",
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


def make_workspace(*, n_proposals: int = 0, with_closure: bool = False,
                   card_config: str | None = None) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="fb9_ws_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = [
        {"seq": 1, "ts": _iso(10), "type": "commitment",
         "source_skill": "meeting-notes",
         "data": {"id": "cmt_fx_001", "title": "Send Sam Sample the draft",
                  "owner_id": "person:001", "kind": "promise"}},
        # a prior brief fire — opens the CHANGED window
        {"seq": 2, "ts": _iso(1.0), "type": "pack_run",
         "source_skill": "morning-briefing",
         "data": {"task_id": "morning-brief", "kind": "morning-brief",
                  "fired_via": "scheduled"}},
    ]
    if with_closure:
        # inside the window (newer than the receipt above) — must surface
        # on the pack's changed lines
        events.append(
            {"seq": 3, "ts": _iso(0.2), "type": "sent_reconcile",
             "source_skill": "reconcile-sent",
             "data": {"n_closed": 2, "commitment_ids": ["cmt_a", "cmt_b"],
                      "window_days": 1}})
    events.extend(_proposal(i) for i in range(1, n_proposals + 1))
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (data_dir / "entities.json").write_text(json.dumps({
        "persons": [{"id": "person:001", "canonical_name": "Sam Sample",
                     "is_primary_user": True}],
        "orgs": [], "threads": [],
    }), encoding="utf-8")
    if card_config is not None:
        cfg_dir = ws / "_hq" / "data" / "skill_config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "system-health.json").write_text(json.dumps({
            "config": {"daily_confirm_card": card_config}}), encoding="utf-8")
    return ws


def _brief_state_events(ws: Path) -> list[dict]:
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
            encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "brief_state":
            out.append(ev)
    return out


def main() -> int:
    # ---- scheduled fire with proposals: full pack + widget ------------------
    ws = make_workspace(n_proposals=3, with_closure=True)
    pack = sd.build_morning_brief_pack(ws, mode="scheduled")

    for key in ("alarm_lines", "changed", "brief_state", "watchdog_line",
                "money_lines", "queue_pointer"):
        check(f"pack carries {key}", key in pack)
    check("the retired confirm_card block is gone (FB-20)",
          "confirm_card" not in pack,
          "a lingering card field invites an orchestrator to render one")
    check("alarm_lines is a list", isinstance(pack["alarm_lines"], list))
    check("brief_state headline present",
          isinstance(pack["brief_state"].get("headline"), dict)
          and pack["brief_state"]["headline"].get("total") == 1,
          f"{pack['brief_state'].get('headline')}")
    check("driver logged the brief_state event exactly once",
          len(_brief_state_events(ws)) == 1,
          f"{len(_brief_state_events(ws))}")
    check("CHANGED window opens at the prior brief receipt",
          pack["changed"]["since_ts"] >= _iso(1.1),
          f"{pack['changed']['since_ts']}")
    check("closure inside the window rides the changed lines",
          any("2" in l for l in pack["changed"]["lines"]),
          f"{pack['changed']['lines']}")
    # FB-20: no card, no transport — on the exact fixture that used to render
    # one (3 open proposals in scheduled mode).
    check("no widget transport on a scheduled fire with a full queue",
          "transport" not in pack, str(sorted(pack.keys())))
    check("the queue pointer counts the whole queue, uncapped",
          pack["queue_pointer"]["count"] == 3,
          f"{pack['queue_pointer']} — the old card capped at 2/detector; the "
          "POINTER must not inherit that cap or it under-promises the queue")
    check("the pointer line names the staff meeting",
          "staff meeting" in pack["queue_pointer"]["line"],
          pack["queue_pointer"]["line"])
    briefs_dir = ws / "_hq" / ".system" / "briefs"
    check("pack audit copy persisted",
          briefs_dir.exists() and any(briefs_dir.glob("morning-pack-*.json")))

    # a second call logs a second brief_state (each CALL is a fire — the
    # idempotent-single-call rule lives in the orchestrator text)
    sd.build_morning_brief_pack(ws, mode="scheduled")
    check("each driver call logs its own brief_state",
          len(_brief_state_events(ws)) == 2)

    # ---- manual fire: identical prose pack (FB-20 — mode no longer branches
    # the card's shape, because there is no card) ------------------------------
    ws_man = make_workspace(n_proposals=2)
    pack_man = sd.build_morning_brief_pack(ws_man, mode="manual")
    check("manual mode carries no transport", "transport" not in pack_man)
    check("manual mode carries the same prose blocks as scheduled",
          isinstance(pack_man["money_lines"], list)
          and isinstance(pack_man["queue_pointer"], dict))
    check("manual mode's pointer is honest too",
          pack_man["queue_pointer"]["count"] == 2,
          f"{pack_man['queue_pointer']}")

    # ---- empty queue ---------------------------------------------------------
    ws_empty = make_workspace(n_proposals=0)
    pack_empty = sd.build_morning_brief_pack(ws_empty, mode="scheduled")
    check("empty queue -> no transport, no pointer line, no money (drop-empty)",
          "transport" not in pack_empty
          and pack_empty["queue_pointer"]["count"] == 0
          and pack_empty["queue_pointer"]["line"] == ""
          and pack_empty["money_lines"] == [])

    # ---- FRP1 config gate — RETIRED on this surface (FB-20) ------------------
    # `daily_confirm_card: off` gated the CARD. With no card to gate, the
    # setting no longer suppresses anything here: the pointer + money prose
    # are substrate truth the brief always owes, and `off` never suppressed
    # the queue itself anyway (its documented semantics were "reachable via
    # `staff meeting`" — which is exactly what the pointer line says). The
    # key still gates `coach`, which does render a card.
    ws_off = make_workspace(n_proposals=3, card_config="off")
    pack_off = sd.build_morning_brief_pack(ws_off, mode="scheduled")
    check("daily_confirm_card off -> still no widget (nothing to suppress)",
          "transport" not in pack_off)
    check("daily_confirm_card off -> the pointer still tells the truth",
          pack_off["queue_pointer"]["count"] == 3,
          f"{pack_off['queue_pointer']} — the setting turned off a CARD; it "
          "was never a gag order on the queue's existence")

    # ---- bad mode ------------------------------------------------------------
    try:
        sd.build_morning_brief_pack(ws_empty, mode="widget")
        check("bad mode raises", False)
    except ValueError:
        check("bad mode raises", True)

    # ---- CLI contract ---------------------------------------------------------
    ws_cli = make_workspace(n_proposals=2)
    res = subprocess.run(
        [sys.executable, str(ROOT / "shared" / "scripts" / "surface_drivers.py"),
         "morning-brief", "--workspace", str(ws_cli), "--mode", "scheduled"],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    check("CLI exits 0", res.returncode == 0, res.stderr[-300:] if res.stderr else "")
    out = res.stdout or ""
    check("CLI emits CR-BRIEF-PACK", "CR-BRIEF-PACK: " in out)
    check("CLI emits NO widget markers (FB-20 — prose only)",
          "CR-WIDGET-HTML" not in out)
    if "CR-BRIEF-PACK: " in out:
        line = [l for l in out.splitlines() if l.startswith("CR-BRIEF-PACK: ")][0]
        parsed = json.loads(line[len("CR-BRIEF-PACK: "):])
        check("CLI pack json parses with the block keys",
              all(k in parsed for k in
                  ("alarm_lines", "changed", "brief_state", "money_lines",
                   "queue_pointer")))
        check("CLI pack json carries no transport", "transport" not in parsed)

    print(f"\n{checks - len(failures)}/{checks} checks OK")
    if failures:
        print(f"{len(failures)} FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("run_morning_brief_pack_test: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
