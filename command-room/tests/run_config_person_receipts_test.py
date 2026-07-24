#!/usr/bin/env python3
"""FB-plumbing item 4 — receipt coverage for config + person-field writes.

Two write classes were changing the substrate with no reliable typed receipt:
  - Balance config writes (the `workspace.*` evening window / cadence / personal-
    calendar knobs) were persisted by orchestrator/SKILL prose that hand-appended
    (or forgot) a `workspace_setting_changed` event.
  - Person tie/cadence writes emit `person_updated`, but the event didn't name
    which fields moved, so a reader couldn't see a tie/cadence change without
    diffing the whole record.

Both event types ALREADY exist in the events-schema enum (`workspace_setting_changed`,
`person_updated`) — reused, no schema change. This pins:
  - `workspace_settings.set_workspace_settings` persists the keys AND emits ONE
    `workspace_setting_changed` receipt per changed key (timezone-handler shape),
    is idempotent (no phantom event on a no-op re-save), and both live in the
    same call;
  - `people_writer.update_person(tie=…, cadence_days=…)` emits `person_updated`
    carrying `updated_fields` naming exactly the changed keys.

G14: no fixture timestamps (writers self-stamp). Placeholder names only.
House convention: non-zero exit = fail.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import people_writer as pw  # noqa: E402
import workspace_settings as wss  # noqa: E402

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


def _ws(entities: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="cfg_person_rcpt_"))
    (d / "_hq" / "data").mkdir(parents=True)
    (d / "_hq" / "data" / "entities.json").write_text(
        json.dumps(entities), encoding="utf-8")
    (d / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _events(ws: Path, etype: str) -> list[dict]:
    out = []
    for line in (ws / "_hq" / "data" / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if ev.get("type") == etype:
            out.append(ev)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # === Balance config writes -> workspace_setting_changed receipts ========
    ws = _ws({"version": 1, "people": [], "workspace": {"evening_start": "18:00"}})
    res = wss.set_workspace_settings(
        ws,
        {"evening_start": "19:00",          # changed
         "min_block_hours": 2,              # new key
         "personal_calendars": ["fam@x"]},  # new key
        source_skill="balance")
    check("helper reports 3 changed keys",
          set(res["changed"]) == {"evening_start", "min_block_hours",
                                  "personal_calendars"}, repr(res))
    check("helper reports 3 events emitted", res["events_emitted"] == 3, repr(res))

    evs = _events(ws, "workspace_setting_changed")
    check("one workspace_setting_changed receipt per changed key", len(evs) == 3,
          repr(evs))
    by_key = {e["data"]["key"]: e["data"] for e in evs}
    check("evening_start receipt carries old + new (timezone-handler shape)",
          by_key.get("evening_start", {}).get("old_value") == "18:00"
          and by_key["evening_start"]["new_value"] == "19:00", repr(by_key))
    check("receipt names the source skill",
          all(e.get("source_skill") == "balance" for e in evs))

    # persisted to entities.json workspace block
    ent = json.loads((ws / "_hq" / "data" / "entities.json").read_text("utf-8"))
    check("config value persisted to the workspace block",
          ent["workspace"]["evening_start"] == "19:00", repr(ent["workspace"]))

    # idempotence — re-saving the SAME values emits no phantom receipt
    res2 = wss.set_workspace_settings(ws, {"evening_start": "19:00"},
                                      source_skill="balance")
    check("idempotent re-save emits no event",
          res2["events_emitted"] == 0 and res2["changed"] == {}, repr(res2))
    check("no extra receipt landed on the no-op",
          len(_events(ws, "workspace_setting_changed")) == 3)

    # === Person tie/cadence writes -> person_updated with updated_fields =====
    ws2 = _ws({"version": 1, "workspace": {}, "people": [
        {"id": "person_042", "canonical_name": "Bo Sample",
         "first_seen": "2025-01-01", "status": "active"},
    ]})
    pw.update_person(ws2, "person_042", source_skill="balance",
                     tie="personal", cadence_days=14)
    pu = _events(ws2, "person_updated")
    check("tie/cadence write emits exactly one person_updated", len(pu) == 1,
          repr(pu))
    uf = set(pu[-1]["data"].get("updated_fields") or [])
    check("person_updated names the changed fields (tie + cadence_days)",
          {"tie", "cadence_days"} <= uf, repr(pu[-1]["data"]))
    check("person_updated still carries person_id + before (back-compat)",
          pu[-1]["data"].get("person_id") == "person_042"
          and isinstance(pu[-1]["data"].get("before"), dict))

    print()
    if failures:
        print(f"FAIL — {len(failures)}/{checks} config/person receipt checks failed")
        for f in failures:
            print(f"  ✗ {f}")
        return 1
    print(f"OK — all {checks} config/person receipt checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
