#!/usr/bin/env python3
"""FS-15 — corruption must be loud (read-time alarms).

The 2026-07-14 dogfood: Cowork's sync cache served a TRUNCATED entities.json
for ~90 minutes; every defensive reader served its fallback SILENTLY, and the
scan-time parse check (T2) couldn't see it — by the time a scan ran, the file
read clean again. This suite pins the read-path fix:

  - read_alarm.py: sidecar record/merge, recency window, never-raises.
  - entity_resolve: corrupt entities.json -> plain-English SubstrateReadError
    (with the full-quit remedy) + sidecar; missing file behavior unchanged;
    corrupt aliases.json -> fallback kept + sidecar.
  - brand: corrupt entities.json -> DEFAULT_BRAND kept (byte-stable contract)
    + sidecar (THE dogfood incident, no longer silent).
  - task_watchdog.read_workspace_config: corrupt -> {} + sidecar; missing ->
    {} and NO sidecar.
  - events loaders (events_io + cru_match): truncated FINAL line -> rows
    before it still served + sidecar; interior junk stays tolerated with NO
    sidecar (fixture/recovery compat); missing file -> no sidecar.
  - substrate_health: read alarms surface LOUDLY (still-bad and the healed
    transient-window case), carry the full-quit remedy, dedupe against the
    scan-time parse line, age out after RECENT_HOURS.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _mk_ws() -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "_hq" / "data").mkdir(parents=True)
    return d


def main() -> int:
    import read_alarm as ra

    # ---- sidecar record / merge / recency --------------------------------
    ws = _mk_ws()
    target = ws / "_hq" / "data" / "entities.json"
    target.write_text("{ truncated", encoding="utf-8")
    ra.record_read_alarm(target, "boom-1", reader="test")
    a1 = ra.read_alarm_for(target)
    check("first record writes the sidecar", a1 is not None and a1["count"] == 1)
    ra.record_read_alarm(target, "boom-2", reader="test2")
    a2 = ra.read_alarm_for(target)
    check("second record merges (count=2, first_seen kept)",
          a2["count"] == 2 and a2["first_seen"] == a1["first_seen"]
          and a2["last_error"] == "boom-2")
    check("fresh alarm is recent", ra.is_recent(a2))
    old = dict(a2, last_seen=(dt.datetime.now(dt.timezone.utc)
                              - dt.timedelta(hours=ra.RECENT_HOURS + 1)
                              ).isoformat(timespec="seconds"))
    check("old alarm ages out", not ra.is_recent(old))
    check("undatable alarm stays recent (never silently ages out)",
          ra.is_recent({"last_seen": "not-a-date"}))
    check("remedy names the full quit",
          "quit" in ra.remedy_line().lower() and "Cowork" in ra.remedy_line())

    # ---- entity_resolve ----------------------------------------------------
    import entity_resolve as er
    ws = _mk_ws()
    ents = ws / "_hq" / "data" / "entities.json"
    ents.write_text('{"entities": {"people": [], "orgs": [', encoding="utf-8")
    raised = None
    try:
        er._load_entities(ws)
    except ra.SubstrateReadError as e:
        raised = e
    check("corrupt entities.json raises SubstrateReadError", raised is not None)
    check("the error is plain-English with the full-quit remedy",
          raised is not None and "quit" in str(raised).lower()
          and "not gone" in str(raised).lower())
    check("entity_resolve failure recorded a sidecar",
          ra.read_alarm_for(ents) is not None)
    ws2 = _mk_ws()
    missing_raised = False
    try:
        er._load_entities(ws2)
    except FileNotFoundError:
        missing_raised = True
    check("missing entities.json keeps pre-FS-15 FileNotFoundError",
          missing_raised)
    check("missing file writes NO sidecar",
          ra.read_alarm_for(ws2 / "_hq" / "data" / "entities.json") is None)
    aliases = ws / "_hq" / "data" / "aliases.json"
    aliases.write_text('{"mappings": { cut', encoding="utf-8")
    check("corrupt aliases.json keeps the fallback",
          er._load_aliases(ws) == {"mappings": {}})
    check("aliases failure recorded a sidecar",
          ra.read_alarm_for(aliases) is not None)

    # ---- brand (THE dogfood incident) -------------------------------------
    import brand
    ws = _mk_ws()
    ents = ws / "_hq" / "data" / "entities.json"
    ents.write_text('{"workspace": {"brand": {"palette"', encoding="utf-8")
    b = brand.get_brand(str(ws))
    check("corrupt entities.json still renders DEFAULT_BRAND (byte-stable)",
          b == brand.DEFAULT_BRAND)
    check("brand degradation recorded a sidecar (no longer silent)",
          ra.read_alarm_for(ents) is not None
          and ra.read_alarm_for(ents)["last_reader"] == "brand")
    ws2 = _mk_ws()
    brand.get_brand(str(ws2))
    check("missing entities.json -> default brand, NO sidecar",
          ra.read_alarm_for(ws2 / "_hq" / "data" / "entities.json") is None)

    # ---- task_watchdog.read_workspace_config ------------------------------
    from task_watchdog import read_workspace_config
    ws = _mk_ws()
    cfg = ws / "_hq" / "workspace_config.json"
    cfg.write_text('{"registered_taskIds": ["morning-br', encoding="utf-8")
    check("corrupt workspace_config keeps the {} fallback",
          read_workspace_config(ws) == {})
    check("workspace_config failure recorded a sidecar",
          ra.read_alarm_for(cfg) is not None)
    ws2 = _mk_ws()
    check("missing workspace_config -> {} and NO sidecar",
          read_workspace_config(ws2) == {}
          and ra.read_alarm_for(ws2 / "_hq" / "workspace_config.json") is None)

    # ---- events loaders ----------------------------------------------------
    import events_io
    ws = _mk_ws()
    ep = ws / "_hq" / "data" / "events.jsonl"
    good = json.dumps({"type": "pack_run", "seq": 1, "data": {}})
    good2 = json.dumps({"type": "pack_run", "seq": 2, "data": {}})
    # truncated final line — the partial-write / cache-truncation signature
    ep.write_text(good + "\n" + good2 + "\n" + '{"type": "commitm', encoding="utf-8")
    rows = events_io.load_all(ws)
    check("truncated tail: rows before it still served", len(rows) == 2)
    alarm = ra.read_alarm_for(ep)
    check("truncated tail recorded a sidecar",
          alarm is not None and "truncation" in alarm["last_error"])
    # interior junk + clean tail — tolerated, NO alarm (fixture compat)
    ws2 = _mk_ws()
    ep2 = ws2 / "_hq" / "data" / "events.jsonl"
    ep2.write_text("not json at all\n" + good + "\n", encoding="utf-8")
    rows = events_io.load_all(ws2)
    check("interior junk with clean tail: tolerated, served", len(rows) == 1)
    check("interior junk with clean tail: NO sidecar",
          ra.read_alarm_for(ep2) is None)
    ws3 = _mk_ws()
    check("missing events.jsonl: empty, NO sidecar",
          events_io.load_all(ws3) == []
          and ra.read_alarm_for(ws3 / "_hq" / "data" / "events.jsonl") is None)

    from cru_match import load_events_defensively
    ws4 = _mk_ws()
    ep4 = ws4 / "_hq" / "data" / "events.jsonl"
    ep4.write_text(good + "\n" + '{"type": "commitm', encoding="utf-8")
    events, skipped = load_events_defensively(ep4)
    check("cru_match: truncated tail still returns prior rows + skipped entry",
          len(events) == 1 and len(skipped) == 1)
    check("cru_match: truncated tail recorded a sidecar",
          ra.read_alarm_for(ep4) is not None)
    ws5 = _mk_ws()
    ep5 = ws5 / "_hq" / "data" / "events.jsonl"
    ep5.write_text("junk line\n" + good + "\n", encoding="utf-8")
    load_events_defensively(ep5)
    check("cru_match: interior junk with clean tail -> NO sidecar",
          ra.read_alarm_for(ep5) is None)

    # ---- substrate_health surfacing ---------------------------------------
    import substrate_health as sh
    # (a) healed transient window: sidecar present, file reads clean NOW
    ws = _mk_ws()
    ents = ws / "_hq" / "data" / "entities.json"
    ents.write_text('{"entities": {"people": []}}', encoding="utf-8")
    ra.record_read_alarm(ents, "Expecting value: line 1 column 9", reader="brand")
    alarms = sh.check_read_alarms(ws)
    check("healed window is detected (still_bad=False)",
          len(alarms) == 1 and alarms[0]["still_bad"] is False)
    lines = sh.substrate_alarm_lines(ws)
    check("healed window surfaces LOUDLY even though the file is healthy now",
          any("reads fine now" in ln for ln in lines))
    check("healed-window line carries the full-quit remedy",
          any("fully quit" in ln for ln in lines))
    # (b) still-bad file already caught by the scan check -> ONE line, not two
    ents.write_text("{ truncated", encoding="utf-8")
    lines = sh.substrate_alarm_lines(ws)
    check("still-corrupt entities.json gets exactly ONE alarm line",
          sum("entities.json" in ln for ln in lines) == 1)
    # (c) still-truncated activity log (not covered by the scan parse check)
    ws2 = _mk_ws()
    ep = ws2 / "_hq" / "data" / "events.jsonl"
    ep.write_text(good + "\n" + '{"type": "commitm', encoding="utf-8")
    list(events_io.iter_events(ws2))  # the read records the alarm
    lines = sh.substrate_alarm_lines(ws2)
    check("still-truncated events.jsonl surfaces with the remedy",
          any("events.jsonl" in ln and "fully quit" in ln.lower()
              for ln in lines))
    # (d) aged-out alarm goes quiet
    ws3 = _mk_ws()
    ents3 = ws3 / "_hq" / "data" / "entities.json"
    ents3.write_text("{}", encoding="utf-8")
    stale = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=ra.RECENT_HOURS + 1)).isoformat(timespec="seconds")
    ra.sidecar_path(ents3).write_text(json.dumps({
        "file": "entities.json", "first_seen": stale, "last_seen": stale,
        "count": 4, "last_error": "old", "last_reader": "brand"}),
        encoding="utf-8")
    check("aged-out alarm no longer surfaces",
          sh.check_read_alarms(ws3) == [] and sh.substrate_alarm_lines(ws3) == [])
    # (e) healthy workspace stays silent
    ws4 = _mk_ws()
    (ws4 / "_hq" / "data" / "events.jsonl").write_text(good + "\n", encoding="utf-8")
    check("healthy workspace -> no alarms", sh.substrate_alarm_lines(ws4) == [])

    # ---- fail-open: a broken alarm recorder must never break a read --------
    # (second-eyes fix, 2026-07-16: the module-level atomic_write import made
    # every instrumented reader die at import time when atomic_write.py was a
    # mid-update half-file — the live 2026-07-15 truncation gotcha. The import
    # is lazy now; these checks pin both the laziness and the fail-open call.)
    import ast as _ast
    tree = _ast.parse(Path(ra.__file__).read_text(encoding="utf-8"))
    top_level_imports = [
        n for n in tree.body if isinstance(n, (_ast.Import, _ast.ImportFrom))
    ]
    check("read_alarm has NO module-level atomic_write import (lazy only)",
          not any(
              (getattr(n, "module", None) == "atomic_write")
              or any(a.name == "atomic_write" for a in getattr(n, "names", []))
              for n in top_level_imports
          ))
    import atomic_write as _aw
    import task_watchdog as _tw

    def _boom(*a, **k):
        raise OSError("disk full / read-only / Drive-locked")

    _orig_awj = _aw.atomic_write_json
    _aw.atomic_write_json = _boom
    try:
        ws6 = _mk_ws()
        t6 = ws6 / "_hq" / "data" / "entities.json"
        t6.write_text("{ cut", encoding="utf-8")
        try:
            ra.record_read_alarm(t6, "boom", reader="test")
            check("record_read_alarm is fail-open when the sidecar write fails",
                  True)
        except Exception as e:
            check("record_read_alarm is fail-open when the sidecar write fails",
                  False, repr(e))
        check("failed sidecar write leaves no sidecar (and no crash)",
              ra.read_alarm_for(t6) is None)
        check("brand still serves DEFAULT_BRAND with a dead recorder",
              brand.get_brand(str(ws6)) == brand.DEFAULT_BRAND)
        raised6 = None
        try:
            er._load_entities(ws6)
        except ra.SubstrateReadError as e:
            raised6 = e
        check("entity_resolve still raises plain-English SubstrateReadError "
              "with a dead recorder (not the recorder's OSError)",
              raised6 is not None)
        cfg6 = ws6 / "_hq" / "workspace_config.json"
        cfg6.write_text("{ cut", encoding="utf-8")
        check("task_watchdog keeps the {} fallback with a dead recorder",
              _tw.read_workspace_config(ws6) == {})
        ep6 = ws6 / "_hq" / "data" / "events.jsonl"
        ep6.write_text(good + "\n" + '{"type": "commitm', encoding="utf-8")
        check("events_io still serves rows before the cut with a dead recorder",
              len(events_io.load_all(ws6)) == 1)
        ev6, sk6 = load_events_defensively(ep6)
        check("cru_match still serves rows + skipped with a dead recorder",
              len(ev6) == 1 and len(sk6) == 1)
    finally:
        _aw.atomic_write_json = _orig_awj

    # ---- no internal jargon on the customer-facing lines -------------------
    from vocabulary_policy import internal_vocab_patterns
    import re as _re
    ws5 = _mk_ws()
    e5 = ws5 / "_hq" / "data" / "entities.json"
    e5.write_text("{}", encoding="utf-8")
    ra.record_read_alarm(e5, "x", reader="brand")
    e5.write_text("{ bad", encoding="utf-8")
    ep5 = ws5 / "_hq" / "data" / "events.jsonl"
    ep5.write_text('{"type": "commitm', encoding="utf-8")
    list(events_io.iter_events(ws5))
    for ln in sh.substrate_alarm_lines(ws5):
        for tid, rx in internal_vocab_patterns():
            check(f"alarm line clean of internal vocab ({tid})",
                  not _re.search(rx, ln, _re.IGNORECASE), ln)

    if failures:
        print(f"\nread-alarm (FS-15) FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"read-alarm loud-corruption (FS-15): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
