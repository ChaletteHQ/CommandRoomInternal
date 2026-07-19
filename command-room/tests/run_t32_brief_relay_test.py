#!/usr/bin/env python3
"""T3.2 FB-18 → FB-20 — the morning brief's widget contract, INVERTED.

HISTORY (read this before "fixing" a failure here — the inversion is deliberate)
-------------------------------------------------------------------------------
T3.2 (FB-18) fixed a live 2026-07-16 scheduled fire that emitted the brief's
confirm-card widget bytes and never relayed them: the driver ran FIRST, ~10
connector steps later the turn read the bytes, logged the receipt, and posted
prose only. The fix pinned render→relay ADJACENCY — driver last, relay as the
immediate next action, a self-priming banner in the driver's own stdout.

FB-20 (M's ruling 2026-07-16 — "the morning brief should just be a morning
brief") retired the widget from this surface entirely. The brief is read-only:
no card, no rows, no buttons, no `show_widget`. Adjudication moved to the staff
meeting, which runs Mon/Wed/Fri instead of Mondays only.

So the pins below are the PHOTOGRAPHIC NEGATIVE of the T3.2 suite they replace.
Where T3.2 asserted "a widget block is emitted and relayed before the digest",
this asserts "NO widget block is ever emitted, and no relay is owed." FB-18's
failure mode is now impossible by construction rather than by instruction —
there are no bytes to drop on the way to the relay.

**A widget emitted from the brief driver is RED.** That is the whole point.

The relay machinery itself is NOT retired — it still serves commitments and
staff-meeting, and section [4] pins that it survived intact. FB-20 removed the
brief from the widget business; it did not remove the widget business.

Asserts:
  [1] driver: the brief pack emits NO widget block, NO relay banner, and no
      `transport` key — in scheduled AND manual mode, with a non-empty queue
      (the case that USED to render) and an empty one
  [2] pack contract: prose-only fields present (money_lines, queue_pointer);
      the confirm_card/transport fields are gone
  [3] orchestrator + SKILL.md: the card placement/relay steps are gone, the
      no-widget ban is explicit, and rule 6's receipt carve-out reads clean
      for a surface that owes no relay
  [4] the relay machinery survives untouched on staff-meeting (the pattern
      T3.2 established — FB-20 must not have collaterally killed it)

G14: every fixture timestamp is computed relative to today. Placeholder names
only (Northwind / Sam Sample).

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ORCH = (ROOT / "skills" / "enable-command-room-schedules" / "references"
        / "orchestrator-morning-brief.md")
SKILL = ROOT / "skills" / "morning-briefing" / "SKILL.md"
STAFF = (ROOT / "skills" / "enable-command-room-schedules" / "references"
         / "orchestrator-staff-meeting.md")
DRIVER = ROOT / "shared" / "scripts" / "surface_drivers.py"

DRIVER_CMD = "python3 shared/scripts/surface_drivers.py morning-brief"

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
            "fingerprint": f"fp_{i}", "title": f"Northwind {i}",
            "render_line": ("likely deal · proposal language in your recent "
                            "sent mail · no pipeline record"),
            "action_tuples": [{"action": "confirm proposal"},
                              {"action": "dismiss proposal"},
                              {"action": "snooze proposal 7d"}],
            "org_id": f"org_{i}",
        },
    }


def make_workspace(*, n_proposals: int = 0) -> Path:
    ws = Path(tempfile.mkdtemp(prefix="fb20_ws_"))
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    events: list[dict] = [
        {"seq": 1, "ts": _iso(10), "type": "commitment",
         "source_skill": "meeting-notes",
         "data": {"id": "cmt_fx_001", "title": "Send Sam Sample the draft",
                  "owner_id": "person:001", "kind": "promise"}},
    ]
    events.extend(_proposal(i) for i in range(1, n_proposals + 1))
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    (data_dir / "entities.json").write_text(json.dumps({
        "persons": [{"id": "person:001", "canonical_name": "Sam Sample",
                     "is_primary_user": True}],
        "orgs": [], "threads": [],
    }), encoding="utf-8")
    return ws


def _run_cli(ws: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DRIVER), "morning-brief",
         "--workspace", str(ws), "--mode", mode],
        capture_output=True, text=True, encoding="utf-8", timeout=120)


def _pack(out: str) -> dict:
    for line in out.splitlines():
        if line.startswith("CR-BRIEF-PACK: "):
            return json.loads(line[len("CR-BRIEF-PACK: "):])
    return {}


def main() -> int:
    orch = ORCH.read_text(encoding="utf-8")
    skill = SKILL.read_text(encoding="utf-8")
    staff = STAFF.read_text(encoding="utf-8")

    # ---- [1] the driver emits NO widget, on any path ------------------------
    print("[1] driver emits no widget block and no relay banner")

    # The load-bearing case: a NON-EMPTY queue in scheduled mode. This is the
    # exact input that rendered the card + banner before FB-20 — if a widget
    # ever comes back, it comes back HERE.
    ws = make_workspace(n_proposals=2)
    res = _run_cli(ws, "scheduled")
    check("CLI exits 0 (scheduled, non-empty queue)", res.returncode == 0,
          (res.stderr or "")[-300:])
    out = res.stdout or ""
    check("scheduled fire with items emits NO widget block",
          "CR-WIDGET-HTML-BEGIN" not in out and "CR-WIDGET-HTML-END" not in out,
          "FB-20: the brief is read-only — a widget from this driver is the "
          "contract violation, not the deliverable")
    check("scheduled fire emits NO relay banner",
          "CR-REQUIRED-NEXT-STEP" not in out,
          "nothing to relay → a banner demanding a relay would misprime a "
          "literal step-follower into hunting for bytes that do not exist")
    check("driver prints exactly the ONE pack line",
          len([l for l in out.splitlines() if l.strip()]) == 1,
          f"stdout lines={[l[:40] for l in out.splitlines() if l.strip()]}")

    for mode in ("scheduled", "manual"):
        for n in (0, 2):
            w = make_workspace(n_proposals=n)
            r = _run_cli(w, mode)
            o = r.stdout or ""
            check(f"no widget block ({mode}, {n} proposals)",
                  r.returncode == 0 and "CR-WIDGET-HTML" not in o,
                  (r.stderr or "")[-200:])
            check(f"no relay banner ({mode}, {n} proposals)",
                  "CR-REQUIRED-NEXT-STEP" not in o)
            check(f"no transport key in the pack ({mode}, {n} proposals)",
                  "transport" not in _pack(o),
                  "a transport key IS widget bytes — relay-shaped output from "
                  "a surface that must not relay")

    # ---- [2] the prose-only pack contract -----------------------------------
    print("[2] pack carries prose, not a card")

    p = _pack(out)
    check("pack has money_lines", isinstance(p.get("money_lines"), list))
    check("pack has queue_pointer",
          isinstance(p.get("queue_pointer"), dict)
          and "count" in p["queue_pointer"] and "line" in p["queue_pointer"])
    check("confirm_card field is GONE", "confirm_card" not in p,
          "the card is retired from this surface — a lingering field would "
          "invite an orchestrator to render it")
    for field in ("alarm_lines", "changed", "brief_state", "watchdog_line"):
        check(f"pack keeps {field}", field in p)

    # ---- [3] the instruction layer matches --------------------------------
    print("[3] orchestrator + SKILL.md carry the no-widget contract")

    check("orchestrator bans show_widget outright",
          "Do NOT call `mcp__visualize__show_widget` from this orchestrator" in orch)
    check("STOP rule 5 is the NO-WIDGET rule now",
          "NO WIDGET. AT ALL." in orch)
    check("the old ONE WIDGET EXCEPTION is explicitly retired",
          "RETIRED" in orch and "ONE WIDGET EXCEPTION" in orch,
          "name the retirement — a silently deleted exception reads as an "
          "oversight to the next editor (the FB-11 prose-drift class)")
    check("no stale relay mandate survives in the orchestrator",
          "THE RELAY IS THE IMMEDIATE NEXT ACTION" not in orch
          and "IMMEDIATE next action" not in orch,
          "a leftover relay mandate on a surface with nothing to relay is a "
          "literal step-follower's dead end")
    check("no stale CR-WIDGET-HTML relay reference",
          "relay `transport[\"html\"]`" not in orch
          and "transport['html']" not in orch)
    check("orchestrator points at the staff meeting instead",
          "staff meeting" in orch.lower())

    # rule 6's carve-out must read clean for a no-relay surface: the receipt
    # is owed, and the old widget-shaped condition is gone.
    r6 = orch.split("HARD LINE — logging is not posting", 1)[-1] \
             .split("Self-check", 1)[0]
    check("rule 6 still forbids logging-as-delivery",
          "BOOKKEEPING" in r6 and "not delivery" in r6)
    check("rule 6's carve-out names the degrade tier AND owes the receipt",
          "degrade" in r6 and "receipt" in r6)
    check("rule 6 no longer conditions on an emitted widget",
          "CR-WIDGET-HTML" not in r6,
          "the FB-18 condition ('emitted but not relayed') is unreachable "
          "now — leaving it would make the rule unfalsifiable prose")
    check("Phase 5 gate is on the DIGEST, not a relay",
          "If Phase 4's digest has not been posted" in orch)

    check("SKILL.md Step 3h is the money/pointer step, not the card step",
          "money carve-out" in skill and "queue pointer" in skill.lower())
    check("SKILL.md states the brief renders no widget",
          "no widget on any fire" in skill.lower()
          or "renders no card" in skill.lower())
    check("SKILL.md's stale card-placement wording is gone",
          "BEFORE the digest prose" not in skill
          and "widget after the digest" not in skill,
          "both orderings are now wrong — there is no widget to order")
    check("SKILL.md names the money helper (G13 — code no skill text "
          "references is invisible at runtime)",
          "money_prose_lines" in skill)

    how = (ROOT / "references" / "HOW_COMMAND_ROOM_WORKS.md").read_text(
        encoding="utf-8")
    check("HOW_COMMAND_ROOM_WORKS describes the brief as prose-only "
          "(FB-11 prose-drift class — a reference that primes the old "
          "behavior is how FB-9 came back)",
          "renders NO widget on any fire" in how
          and "BEFORE the digest prose" not in how)

    # ---- [4] the relay machinery survives where it still belongs ------------
    print("[4] staff-meeting relay pattern intact (FB-20 is surface-scoped)")

    check("staff-meeting still renders+posts via ONE driver call",
          "ONE driver call" in staff
          and "surface_drivers.py staff-meeting" in staff)
    check("staff-meeting still mandates the byte-exact relay",
          "byte-exact" in staff)

    src = DRIVER.read_text(encoding="utf-8")
    check("the driver still emits widget blocks for OTHER surfaces",
          "CR-WIDGET-HTML-BEGIN" in src,
          "FB-20 retires the brief's widget, not the transport itself")
    # T3.2's self-priming CR-REQUIRED-NEXT-STEP banner is GONE, and correctly
    # so: it only ever printed inside the morning-brief branch (verified
    # against main @ 980fe7a — commitments/staff-meeting never emitted one).
    # FB-20 deleted its sole consumer, so the whole mechanism goes with the
    # card. Staff-meeting's relay is pinned by orchestrator adjacency + the
    # byte-exact mandate ([4] above), never by a banner — which is why that
    # surface never skipped and never needed one.
    check("the banner mechanism is gone with the card it primed",
          "CR-REQUIRED-NEXT-STEP" not in src,
          "the brief was its only consumer — a banner with no emitter is "
          "dead prose that the next reader will try to wire up")

    print(f"\n{checks - len(failures)}/{checks} checks OK")
    if failures:
        print(f"{len(failures)} FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("run_t32_brief_relay_test: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
