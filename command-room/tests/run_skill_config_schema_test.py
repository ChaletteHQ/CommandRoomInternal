#!/usr/bin/env python3
"""Settings-layer C4 — skill_config key-schema validation + cleanup lint.

Covers: the per-skill schema is present + parses + covers every FRP1 adopter it
should; validate_skill_config flags unknown keys for registered skills and is
permissive for unregistered ones; save_skill_config REJECTS an unknown key
loudly (ValueError) at write and still accepts known keys; get_config's
fall-back-to-defaults read path is unaffected; lint_skill_configs flags a
dangling saved key and is silent on a clean workspace.

House conventions: check(name, cond) prints OK/FAIL, exit 1 on any failure,
auto-discovered by run_all.py. stdlib only.
"""
from __future__ import annotations

import json
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
    ws = Path(tempfile.mkdtemp(prefix="c4_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    return ws


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== settings-layer C4: skill_config schema ===")

    # ---- schema file present + parses + covers the FRP1 adopters ----
    schema_path = ROOT / "shared" / "data-schemas" / "skill_config.schema.json"
    check("schema: file present", schema_path.exists())
    parsed = None
    if schema_path.exists():
        try:
            parsed = json.loads(schema_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            parsed = None
    check("schema: parses as JSON with a 'skills' map",
          isinstance(parsed, dict) and isinstance(parsed.get("skills"), dict))
    skills = (parsed or {}).get("skills", {})
    for s, key in [
        ("email-writer", "draft_posture"),
        ("morning-briefing", "depth"),
        ("inbox-triage", "discard_aggressiveness"),
        ("call-prep", "auto_fire"),
        ("meeting-notes", "commitment_capture"),
        ("weekly-recap", "lens"),
        ("memo-writer", "register"),
        ("operator-report", "length"),
    ]:
        check(f"schema: {s} registered with key '{key}'",
              key in skills.get(s, []))

    # ---- validate_skill_config: registered skill ----
    check("validate: known key -> no violations",
          scw.validate_skill_config("email-writer", {"draft_posture": "show_first"}) == [])
    check("validate: unknown key -> flagged",
          scw.validate_skill_config("email-writer", {"posture": "x"}) == ["posture"])
    check("validate: unregistered skill -> permissive ([])",
          scw.validate_skill_config("boardroom", {"anything": 1, "goes": 2}) == [])

    # ---- save_skill_config: loud reject on unknown key ----
    ws = _ws()
    raised = False
    try:
        scw.save_skill_config(ws, "email-writer", {"draft_postur": "show_first"})  # typo
    except ValueError as e:
        raised = "draft_postur" in str(e)
    check("save: unknown key raises ValueError naming the bad key", raised)
    check("save: rejected write persisted nothing", scw.load_skill_config(ws, "email-writer") is None)

    # ---- save_skill_config: known keys accepted + round-trip ----
    scw.save_skill_config(ws, "email-writer", {"draft_posture": "auto_queue", "length": "fuller"})
    got = scw.get_config(ws, "email-writer", {"draft_posture": "show_first", "sign_off": "dash_first", "length": "short_direct"})
    check("save: known keys accepted + get_config merges over defaults",
          got["draft_posture"] == "auto_queue" and got["length"] == "fuller" and got["sign_off"] == "dash_first")

    # ---- unregistered skill stays fully permissive at write ----
    scw.save_skill_config(ws, "boardroom", {"whatever": [1, 2, 3]})
    check("save: unregistered skill still writes freely", scw.load_skill_config(ws, "boardroom") is not None)

    # ---- lint_skill_configs: clean vs dangling ----
    ws = _ws()
    check("lint: clean workspace (no config dir) -> {}", scw.lint_skill_configs(ws) == {})
    scw.save_skill_config(ws, "operator-report", {"length": "full"})
    check("lint: valid config -> not flagged", "operator-report" not in scw.lint_skill_configs(ws))
    # hand-plant a dangling key (a deprecated key left after a rename) directly on disk
    cfg_path = ws / "_hq" / "data" / "skill_config" / "operator-report.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    data["config"]["legacy_verbosity"] = "old"
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    lint = scw.lint_skill_configs(ws)
    check("lint: dangling key surfaced for cleanup",
          lint.get("operator-report") == ["legacy_verbosity"])

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} C4 check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL settings-layer C4 checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
