#!/usr/bin/env python3
"""
Per-type event payload validator (SPEC EVT1) — stdlib only, no jsonschema dep.

events.schema.json constrains only the `type` enum; `data` was free-form, so
payload drift (two skills emitting the same type with different shapes) surfaced
as runtime crashes / silently-dropped fields instead of a test failure. This
checks an event's `data` against `shared/data-schemas/event-payloads.schema.json`
(the custom alias-group mini-schema).

Posture: WARN-ONLY at write time (see atomic_append_jsonl) — `check_payload`
returns a list of violation strings; it never raises and never blocks a write.
Types with no schema entry are unconstrained (pass). Unknown keys are allowed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "data-schemas" / "event-payloads.schema.json"
_SCHEMA_CACHE: Optional[dict] = None

_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _load_schema() -> dict:
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        try:
            raw = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
            _SCHEMA_CACHE = {k: v for k, v in raw.items() if not k.startswith("_")}
        except Exception:
            _SCHEMA_CACHE = {}
    return _SCHEMA_CACHE


def check_payload(event: dict) -> List[str]:
    """Return a list of payload violations for `event` (empty = valid). Never
    raises. An unrecognized type, or a missing/none `data`, validates as a pass
    when the type has no schema; a schema'd type with no `data` reports its
    missing required groups."""
    violations: List[str] = []
    if not isinstance(event, dict):
        return ["event is not a dict"]
    etype = event.get("type")
    schema = _load_schema().get(etype)
    if schema is None:
        return violations  # unconstrained type
    data = event.get("data")
    if not isinstance(data, dict):
        data = {}

    # Required alias-groups: each group satisfied if ANY of its keys is present.
    for group in schema.get("required", []):
        keys = group if isinstance(group, list) else [group]
        if not any(k in data and data[k] not in (None, "") for k in keys):
            if len(keys) == 1:
                violations.append(f"{etype}: missing required key '{keys[0]}'")
            else:
                violations.append(f"{etype}: missing required key (one of {keys})")

    # Type-check declared properties when present.
    for key, expected in (schema.get("properties") or {}).items():
        if key in data and data[key] is not None:
            checker = _TYPE_CHECKS.get(expected)
            if checker and not checker(data[key]):
                violations.append(
                    f"{etype}: key '{key}' should be {expected}, got {type(data[key]).__name__}"
                )
    return violations


def covered_types() -> List[str]:
    return list(_load_schema().keys())


__all__ = ["check_payload", "covered_types"]


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 2:
        ev = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        v = check_payload(ev)
        print("\n".join(v) if v else "OK")
        raise SystemExit(1 if v else 0)
    print("covered types:", ", ".join(covered_types()))
