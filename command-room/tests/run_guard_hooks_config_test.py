#!/usr/bin/env python3
"""
Structural guard: plugin hooks configs may contain ONLY schema-valid hook
fields — no documentation keys, no unknown fields.

Why this guard exists (v4.6.1 hotfix, 2026-07-09):

  v4.6.0 carried a top-level "_comment" key in command-room/hooks/hooks.json
  (a JSON-comment workaround holding the GATE2 D3 rationale — JSON has no
  real comment syntax). The Claude Code CLI tolerates extra fields, so it
  passed every local test. But the Cowork REMOTE marketplace validator
  schema-validates hooks configs and rejects any field it can't surface in
  the approval UI:

     status=failed_content
     "Unknown hook field(s) ['_comment'] in hooks config."

  That failure only fires at marketplace SYNC time (install / update), so it
  shipped clean and broke install on every client repo fleet-wide. This is
  the same "local passes, server rejects" class as the non-ASCII path gotcha
  (run … path guard). The durable fix is a ship-time guard that mirrors the
  server rule so the ship aborts BEFORE push, not at the customer's install.

The rule enforced:

  * Top-level keys of a hooks config ⊆ {"hooks"}.
  * Event names (keys under "hooks") ⊆ KNOWN_EVENTS.
  * Each matcher-group's keys ⊆ {"matcher", "hooks"}.
  * Each hook entry's keys ⊆ {"type", "command", "timeout"}.
  * NO key anywhere may start with "_" (the _comment doc-key anti-pattern).

If Claude Code adds a new documented hook event or field, extend the
allowlists below IN THE SAME COMMIT that starts using it — exactly as the
server error message instructs.

Companion to hooks/README.md (where hook rationale now lives instead of a
_comment key) and the cr-hooks-comment-marketplace-gotcha memory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# Documented Claude Code hook events (keys under "hooks").
KNOWN_EVENTS = {
    "PreToolUse", "PostToolUse", "UserPromptSubmit", "Notification",
    "Stop", "SubagentStop", "SessionStart", "SessionEnd", "PreCompact",
}
ALLOWED_TOPLEVEL = {"hooks"}
ALLOWED_GROUP_KEYS = {"matcher", "hooks"}
ALLOWED_ENTRY_KEYS = {"type", "command", "timeout"}


def _check_config(data: dict, where: str) -> list[str]:
    """Return a list of violation strings for one parsed hooks config."""
    v: list[str] = []

    # Underscore-prefixed keys anywhere = the doc-key anti-pattern.
    def _no_underscore(obj, path):
        if isinstance(obj, dict):
            for k, val in obj.items():
                if isinstance(k, str) and k.startswith("_"):
                    v.append(f"{where}: doc/comment key '{k}' at {path or '<root>'} "
                             f"— hooks configs allow only schema fields; move prose to a README")
                _no_underscore(val, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _no_underscore(item, f"{path}[{i}]")

    _no_underscore(data, "")

    for k in data:
        if k not in ALLOWED_TOPLEVEL:
            v.append(f"{where}: unknown top-level key '{k}' (allowed: {sorted(ALLOWED_TOPLEVEL)})")

    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        v.append(f"{where}: 'hooks' must be an object")
        return v

    for event, groups in hooks.items():
        if event not in KNOWN_EVENTS:
            v.append(f"{where}: unknown hook event '{event}' "
                     f"— if newly documented, add it to KNOWN_EVENTS in this guard")
        if not isinstance(groups, list):
            v.append(f"{where}: hooks.{event} must be an array")
            continue
        for gi, group in enumerate(groups):
            if not isinstance(group, dict):
                v.append(f"{where}: hooks.{event}[{gi}] must be an object")
                continue
            for gk in group:
                if gk not in ALLOWED_GROUP_KEYS:
                    v.append(f"{where}: hooks.{event}[{gi}] unknown key '{gk}' "
                             f"(allowed: {sorted(ALLOWED_GROUP_KEYS)})")
            for ei, entry in enumerate(group.get("hooks", []) or []):
                if not isinstance(entry, dict):
                    v.append(f"{where}: hooks.{event}[{gi}].hooks[{ei}] must be an object")
                    continue
                for ek in entry:
                    if ek not in ALLOWED_ENTRY_KEYS:
                        v.append(f"{where}: hooks.{event}[{gi}].hooks[{ei}] unknown key '{ek}' "
                                 f"(allowed: {sorted(ALLOWED_ENTRY_KEYS)})")
    return v


def scan() -> list[str]:
    violations: list[str] = []
    for path in PLUGIN_ROOT.rglob("hooks.json"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(PLUGIN_ROOT)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            violations.append(f"{rel}: not valid JSON ({e})")
            continue
        if not isinstance(data, dict):
            violations.append(f"{rel}: top level must be an object")
            continue
        violations.extend(_check_config(data, str(rel)))
    return violations


def _selftest() -> bool:
    """Prove the guard would have caught the v4.6.0 bug."""
    bad = {"_comment": "rationale here", "hooks": {"Stop": [{"matcher": "*",
           "hooks": [{"type": "command", "command": "x"}]}]}}
    ok = {"hooks": {"Stop": [{"matcher": "*",
          "hooks": [{"type": "command", "command": "x"}]}]}}
    return bool(_check_config(bad, "fixture")) and not _check_config(ok, "fixture")


def main() -> int:
    if "--selftest" in sys.argv:
        passed = _selftest()
        print("OK — selftest passed (guard catches _comment, passes clean config)"
              if passed else "FAIL — selftest broken")
        return 0 if passed else 1

    violations = scan()
    if violations:
        print("FAIL — invalid field(s) in plugin hooks config:")
        print()
        for msg in violations:
            print(f"  {msg}")
        print()
        print(f"Total: {len(violations)} violation(s)")
        print()
        print("The Cowork marketplace validator rejects any field it can't surface")
        print("in the approval UI (status=failed_content), which blocks install/update")
        print("fleet-wide. Keep hooks.json limited to schema-valid hook fields; put")
        print("rationale in a sibling README (see hooks/README.md).")
        return 1
    print("OK — all hooks configs contain only schema-valid fields")
    return 0


if __name__ == "__main__":
    sys.exit(main())
