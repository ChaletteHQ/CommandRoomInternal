#!/usr/bin/env python3
"""SPEC FRP1 (S1) — get_config deep-merge + origin round-trip. House conventions:
check(name, cond) prints OK/FAIL, exit 1 on any failure, auto-discovered by run_all.py."""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import skill_config_writer as scw  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="frp1_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def _last_event(ws: Path) -> dict:
    lines = [l for l in (ws / "_hq" / "data" / "events.jsonl").read_text().splitlines() if l.strip()]
    return json.loads(lines[-1])


DEFAULTS = {"depth": "headline", "leads_with": "synthesis",
            "going_quiet": {"enabled": True, "threshold_days": 14}}


def main() -> int:
    # ---- get_config: no saved -> defaults copy (and never mutates defaults) ----
    ws = _ws()
    got = scw.get_config(ws, "morning-briefing", DEFAULTS)
    check("get_config: no saved config -> equals defaults", got == DEFAULTS)
    got["depth"] = "MUTATED"
    check("get_config: returned dict is a copy (defaults untouched)", DEFAULTS["depth"] == "headline")

    # ---- deep-merge: saved partial wins; unsaved keys fall back to defaults ----
    ws = _ws()
    scw.save_skill_config(ws, "morning-briefing", {"depth": "full"})
    merged = scw.get_config(ws, "morning-briefing", DEFAULTS)
    check("get_config: saved key wins", merged["depth"] == "full")
    check("get_config: unsaved key falls back to default", merged["leads_with"] == "synthesis")
    check("get_config: a v+1 default key (going_quiet) survives an old config",
          merged["going_quiet"] == {"enabled": True, "threshold_days": 14})

    # ---- nested dict deep-merge (not wholesale replace) ----
    ws = _ws()
    scw.save_skill_config(ws, "morning-briefing", {"going_quiet": {"enabled": False}})
    merged = scw.get_config(ws, "morning-briefing", DEFAULTS)
    check("get_config: nested merge keeps the unsaved sub-key",
          merged["going_quiet"] == {"enabled": False, "threshold_days": 14})

    # ---- origin round-trip ----
    ws = _ws()
    scw.save_skill_config(ws, "inbox-triage", {"discard_aggressiveness": "standard"})  # first fire, default
    ev = _last_event(ws)
    check("origin: first fire of defaults -> first_fire_defaults",
          ev["type"] == "skill_first_run_configured" and ev["data"]["origin"] == "first_fire_defaults")
    scw.save_skill_config(ws, "inbox-triage", {"discard_aggressiveness": "aggressive"})  # reconfigure
    ev = _last_event(ws)
    check("origin: reconfigure -> tune",
          ev["type"] == "skill_reconfigured" and ev["data"]["origin"] == "tune")
    # explicit override (m1_batch)
    ws = _ws()
    scw.save_skill_config(ws, "email-writer", {"draft_posture": "show_first"}, origin="m1_batch")
    ev = _last_event(ws)
    check("origin: explicit m1_batch round-trips", ev["data"]["origin"] == "m1_batch")

    # ---- is_configured gate (block renders once) still holds ----
    check("is_configured: True after a save", scw.is_configured(ws, "email-writer"))
    scw.wipe_skill_config(ws, "email-writer")
    check("is_configured: False after wipe (reset -> next fire is first-fire again)",
          not scw.is_configured(ws, "email-writer"))

    # ---- the FIRST_RUN_PROTOCOL doc + widget exception exist ----
    proto = ROOT / "shared" / "FIRST_RUN_PROTOCOL.md"
    check("FIRST_RUN_PROTOCOL.md exists", proto.exists())
    if proto.exists():
        t = proto.read_text(encoding="utf-8")
        check("protocol: documents the 4 modes", all(m in t for m in ("Detect", "Show", "Tune", "Reset")))
        check("protocol: states show-then-tune default + ask-first class",
              "show-then-tune" in t.lower() and "ask-first" in t.lower())
    widget = (ROOT / "shared" / "CHAT_ACTION_WIDGET.md").read_text(encoding="utf-8")
    check("CHAT_ACTION_WIDGET.md carries the fr-item shape + preselect exception",
          "fr1" in widget and "SPEC FRP1" in widget)
    ac = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(encoding="utf-8")
    check("apply-choices dispatches fr* to save_skill_config",
          "fr" in ac and "save_skill_config" in ac and "FRP1" in ac)

    # ---- per-skill adoption gate (SPEC FRP1 S2-S4) ----
    # Each adopting skill's SKILL.md must carry the full first-run contract: the
    # section, DEFAULTS, the is_configured gate (block renders once), the writer +
    # read-path helpers, a freeform tune table, and the trigger family in its
    # frontmatter description (tune / show settings / reset to defaults). Append a
    # skill here as it adopts so "green" proves adoption, not just the S1 storage layer.
    ADOPTED = [
        "email-writer", "morning-briefing", "inbox-triage",
        "call-prep", "meeting-notes", "follow-up-ritual",
        "weekly-recap", "dormant-customer-scan", "decision-log",
        "memo-writer", "one-pager-composer", "stalled-projects",
        "operator-report",  # settings-layer C2 #6 — length preset (PR2)
        # SPEC OUT2 §5 — the composer wave.
        "board-pack-assembler", "decision-memo-composer",
        "stress-test", "automation-scanner",
        "objectives",  # SPEC OBJ1 (draft) — 3 decisions: drift preset / active cap / cold-start proposals
    ]
    # OUT2 §5 adopters landed with the G11 catalog budget at cap, so their
    # primary 'tune <skill>' phrase lives in the body's '## Routing (full
    # trigger corpus)' section instead of the frontmatter description (the
    # runtime router and run_trigger_test read description + Routing together —
    # the v4.5.1 rule). If description budget is ever freed, moving the phrase
    # up and removing the skill from this set is the tightening move.
    G11_CONSTRAINED = {
        "board-pack-assembler", "decision-memo-composer",
        "stress-test", "automation-scanner",
    }
    BODY_MARKERS = [
        ("SPEC FRP1", "names the protocol"),
        ("First-Run Personalization", "has the section heading"),
        ("DEFAULTS", "declares DEFAULTS"),
        ("is_configured", "gates the block once (is_configured)"),
        ("save_skill_config", "writes via save_skill_config"),
        ("get_config", "reads via get_config"),
        ("wipe_skill_config", "supports reset (wipe_skill_config)"),
        ("Freeform tune", "carries a freeform tune table"),
    ]
    for skill in ADOPTED:
        md = (ROOT / "skills" / skill / "SKILL.md")
        if not md.exists():
            check(f"adoption[{skill}]: SKILL.md exists", False)
            continue
        text = md.read_text(encoding="utf-8")
        for marker, label in BODY_MARKERS:
            check(f"adoption[{skill}]: {label}", marker in text)
        # v4.5.1 contract: the PRIMARY config phrase must be in the budget-capped
        # description (front-loaded routing); the full family may live in the
        # description OR the body's Routing section (loaded at fire time; the
        # runtime router matches variants semantically). G11_CONSTRAINED skills
        # (OUT2 §5) carry the primary in the Routing corpus instead — see the
        # set's comment above.
        fm = text.split("---", 2)
        desc = fm[1] if len(fm) >= 3 else text
        if skill in G11_CONSTRAINED:
            rm = re.search(
                r"^## Routing \(full trigger corpus\)\n(.*?)(?=^## |\Z)",
                text, re.S | re.M)
            routing = rm.group(1) if rm else ""
            check(f"adoption[{skill}]: Routing corpus carries 'tune {skill}' "
                  f"(G11-capped placement)", f"tune {skill}" in routing)
        else:
            check(f"adoption[{skill}]: description carries 'tune {skill}'",
                  f"tune {skill}" in desc)
        for trig in (f"show {skill} settings", f"reset {skill} to defaults"):
            check(f"adoption[{skill}]: corpus carries '{trig}'", trig in text)

    # ---- M1 onboarding widget: the draft-posture row (SPEC FRP1 S5 / D6) ----
    widget = (ROOT / "skills" / "command-room-onboarding" / "references" / "step1_widget_v2.html")
    if widget.exists():
        w = widget.read_text(encoding="utf-8")
        check("m1 widget: final question card is Q4 (data-q=4, no data-q=5)",
              'data-q="4"' in w and 'data-q="5"' not in w)
        check("m1 widget: posture chips show_first + auto_queue",
              'data-v="show_first"' in w and 'data-v="auto_queue"' in w)
        check("m1 widget: submit payload is 4 tuples", "[1, 2, 3, 4]" in w)
        check("m1 widget: shows 4/4 progress", "4 / 4" in w)
        check("m1 widget: email-exclusion question removed", 'data-v="exclude"' not in w)
    else:
        check("m1 widget: step1_widget_v2.html exists", False)
    onb = (ROOT / "skills" / "command-room-onboarding" / "SKILL.md").read_text(encoding="utf-8")
    check("m1 onboarding: fire-marker bumped to step_1_setup_v5", "step_1_setup_v5" in onb)
    check("m1 onboarding: item 4 writes email-writer posture with m1_batch origin",
          "m1_batch" in onb and "draft_posture" in onb)
    apc = (ROOT / "skills" / "apply-choices" / "SKILL.md").read_text(encoding="utf-8")
    check("m1 apply-choices: recognizes step_1_setup_v5 + routes posture via m1_batch",
          "step_1_setup_v5" in apc and "m1_batch" in apc)

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} first-run-protocol check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL first-run-protocol checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
