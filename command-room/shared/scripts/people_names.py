"""`{person id: [names]}` from the workspace's people (cr1#764, cr1#765).

One job: the names a person on file goes by, so a matcher can set the
parties' names aside when it asks whether two texts share a subject (the
cr1#468 rule: a shared first name is not a shared topic). {} when the file
cannot be read: a name that cannot be set aside leaves a subject wider,
never narrower, and never raises into a job.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


def people_names(workspace_root) -> dict:
    if not workspace_root:
        return {}
    try:
        from entities_io import entities_collection
        raw = json.loads((Path(workspace_root) / "_hq" / "data" / "entities.json")
                         .read_text(encoding="utf-8"))
        out: dict = {}
        for p in entities_collection(raw, "people"):
            labels = [p.get("name"), p.get("canonical_name")] + list(p.get("aliases") or [])
            pid = str(p.get("id") or "").strip()
            if pid:
                out[pid] = [str(x) for x in labels if isinstance(x, str) and x.strip()]
        return out
    except Exception:  # noqa: BLE001
        return {}


def names_for(people: dict, *person_ids) -> list:
    """Every name on file for the given ids, flat."""
    out: list = []
    for pid in person_ids:
        if pid:
            out += [str(n) for n in (people or {}).get(str(pid)) or [] if n]
    return out


__all__ = ["people_names", "names_for"]
