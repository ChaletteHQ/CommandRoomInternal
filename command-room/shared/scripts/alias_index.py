"""One reader for a workspace's other names (cr1#954, cr1#1011, #789 item 3).

`_hq/data/aliases.json` maps each other name (a nickname, a short form, an old
name) to the id of the person, company or thread it means. The product has
written it in several shapes, and before this module each reader parsed it its
own way, so a shape one reader took crashed or went unseen in another:

  - the schema's tiered dict  {"mappings": {"people": [{raw, canonical_id}], ...}}
    with any tier key ("people", "orgs", "projects", "threads", ...);
  - a flat list               {"mappings": [{raw, canonical_id}, ...]}
    with the older key spellings alias / entity_id / target_id;
  - a plain dict              {"mappings": {"<alias>": "<id>"}}, or the same
    dict with no "mappings" wrapper;
  - a list-valued dict        {"mappings": {"<alias>": ["<id>", ...]}}.

Every reader goes through `alias_rows` (or `aliases_by_entity`), so all of them
see the same names. The writer (`people_writer`) is unchanged.

Read-only and stdlib only. Odd input yields no rows; it never raises.
"""

from __future__ import annotations

from typing import Any, Dict, List

_ALIAS_KEYS = ("raw", "alias")
_ID_KEYS = ("canonical_id", "entity_id", "target_id")
_DOC_FIELDS = ("version", "last_updated", "last_writer")
# Tier names the product has written. A list under one of these is always a
# tier of mapping objects, never one alias naming several ids.
_TIER_KEYS = ("people", "orgs", "projects", "threads")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _row_from_object(obj: dict) -> Dict[str, Any] | None:
    """A mapping object in any key spelling, as a row with `raw` and
    `canonical_id`, keeping the object's other fields (confidence, added_by)."""
    raw = next((_text(obj.get(k)) for k in _ALIAS_KEYS if _text(obj.get(k))), "")
    cid = next((_text(obj.get(k)) for k in _ID_KEYS if _text(obj.get(k))), "")
    if not raw or not cid:
        return None
    row = dict(obj)
    row["raw"] = raw
    row["canonical_id"] = cid
    return row


def _rows_from_pair(alias: Any, target: Any) -> List[Dict[str, Any]]:
    """{"alias": "id"} or {"alias": ["id", ...]} as rows, one per id."""
    raw = _text(alias)
    if not raw:
        return []
    targets = target if isinstance(target, list) else [target]
    return [{"raw": raw, "canonical_id": _text(t)} for t in targets if _text(t)]


def alias_rows(doc: Any) -> List[Dict[str, Any]]:
    """Every (other name, id) mapping in an aliases.json document, whatever its
    shape, as rows with at least `raw` and `canonical_id`.

    A list-valued alias gives one row per id it names. Order follows the file.
    """
    if not isinstance(doc, dict):
        return []
    mappings = doc.get("mappings") if "mappings" in doc else doc
    rows: List[Dict[str, Any]] = []
    if isinstance(mappings, list):
        for obj in mappings:
            if isinstance(obj, dict):
                row = _row_from_object(obj)
                if row:
                    rows.append(row)
        return rows
    if not isinstance(mappings, dict):
        return []
    for key, value in mappings.items():
        if isinstance(value, list) and (key in _TIER_KEYS or any(isinstance(v, dict) for v in value)):
            # A tier: {"people": [{raw, canonical_id}, ...]}.
            for obj in value:
                if isinstance(obj, dict):
                    row = _row_from_object(obj)
                    if row:
                        rows.append(row)
        elif mappings is doc and key in _DOC_FIELDS:
            # A plain dict with no "mappings" wrapper still carries the
            # document's own fields; they are not aliases.
            continue
        else:
            # A pair: {"alias": "id"} or {"alias": ["id", ...]}.
            rows.extend(_rows_from_pair(key, value))
    return rows


def aliases_by_entity(doc: Any) -> Dict[str, List[str]]:
    """{entity id: [other name, ...]}, each name listed once per id, in file order."""
    out: Dict[str, List[str]] = {}
    for row in alias_rows(doc):
        names = out.setdefault(row["canonical_id"], [])
        if row["raw"] not in names:
            names.append(row["raw"])
    return out
