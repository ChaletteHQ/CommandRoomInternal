#!/usr/bin/env python3
"""narration_names — internal ids become names AT COMPOSITION, for chat prose.

CUT-C item 8 (ATTENDED_TEST_v5.28.0 B2.3 / B4.4): `project_011` reached the
weekly recap's narration and `person_201` the people-crm record header. The
leak scanner (`chat_output_renderer.validate_chat_output`) existed but ran
only over widget bodies, the slack leg, two driver text paths, the board and
the Apply ack — never over recap / people / brief / day-close PROSE, which
is composed by the model from SKILL.md instructions. Two halves close it:

  1. THIS module — the one name index + the one substitution, lifted from
     `render_person_history._humanize` (which now delegates here, as does
     `render_org_history`). Composers call `humanize` / `humanize_lines` at
     composition so a raw id never reaches a sentence; `person_header`
     composes the people-crm record header; `recap_change_lines` is the
     weekly recap's change-feed roll-up with every line humanized.
  2. The scan step — every customer-facing SKILL.md that composes narration
     names `validate_chat_output` as a MANDATORY post step (guard G51,
     `tests/run_guard_g51_narration_scan_test.py`).

Names come from `_hq/data/entities.json` through `entities_io` (people /
orgs / threads — the canonical `projects` alias resolves to threads). An id
with no record renders "(name on file)" — never the id, never a blank that
swallows the sentence.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from entities_io import entities_collection, unwrap_entities  # noqa: E402

# The entity-id shapes the leak scanner forbids in prose
# (`chat_output_renderer._LEAK_PATTERNS`, first rule) — ONE regex, shared by
# the history renderers through this module.
INTERNAL_ID_RE = re.compile(
    r"\b(person|project|org|event|matter|engagement)_\d{3,}\b", re.IGNORECASE
)

UNRESOLVED_LABEL = "(name on file)"


def _load_doc(workspace_root) -> dict:
    p = Path(workspace_root) / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def name_index_from_doc(doc: dict) -> dict[str, str]:
    """{entity id -> display name} over people, orgs and threads of an
    entities document (wrapped or flat). Pure."""
    idx: dict[str, str] = {}
    if not isinstance(doc, dict):
        return idx
    container = unwrap_entities(doc)
    for o in entities_collection(container, "orgs"):
        if isinstance(o, dict) and o.get("id"):
            idx[str(o["id"])] = (o.get("canonical_name") or o.get("name")
                                 or "(unnamed company)")
    for t in entities_collection(container, "threads"):
        if isinstance(t, dict) and t.get("id"):
            idx[str(t["id"])] = (t.get("display_name") or t.get("canonical_name")
                                 or t.get("folder_name") or "(unnamed thread)")
    for pr in entities_collection(container, "people"):
        if isinstance(pr, dict) and pr.get("id"):
            idx[str(pr["id"])] = (pr.get("canonical_name") or pr.get("name")
                                  or "(unnamed person)")
    return idx


def name_index(workspace_root) -> dict[str, str]:
    """{entity id -> display name} for a workspace. Read-only."""
    return name_index_from_doc(_load_doc(workspace_root))


def humanize(text, name_idx: dict[str, str]) -> str:
    """Replace every internal entity-id token in `text` with its name;
    an id the index does not carry becomes `UNRESOLVED_LABEL`. The result
    never contains an id shape the leak scanner forbids."""
    if not text:
        return ""

    def _sub(m: re.Match) -> str:
        # REVIEW_CUTC F-8: the regex is case-insensitive, so the lookup is
        # too — `PROJECT_011` / `Person_201` resolve to the same name as their
        # lower-case spelling instead of swallowing into the honest label.
        tok = m.group(0)
        name = name_idx.get(tok) or name_idx.get(tok.lower())
        return (name or "").strip() or UNRESOLVED_LABEL

    return INTERNAL_ID_RE.sub(_sub, str(text)).strip()


def humanize_lines(lines: Iterable, name_idx: dict[str, str]) -> list[str]:
    """`humanize` over a list of strings (or dicts carrying `text`)."""
    out: list[str] = []
    for ln in lines or []:
        if isinstance(ln, dict):
            out.append(humanize(ln.get("text") or "", name_idx))
        else:
            out.append(humanize(ln, name_idx))
    return out


def carries_internal_id(text) -> bool:
    """True when `text` still carries an entity-id token (the composer's own
    post-condition; the SKILL-level scan is `validate_chat_output`)."""
    return bool(text) and INTERNAL_ID_RE.search(str(text)) is not None


# ---------------------------------------------------------------------------
# Composers
# ---------------------------------------------------------------------------

def person_header(workspace_root, person_id, *, doc: Optional[dict] = None) -> str:
    """The people-crm record header (`who is [name]`), composed here so the
    line carries the person's NAME and never their id: "### Name — Role,
    Org" (role and org only when on record). A person the index does not
    carry renders "### (name on file)" — an honest gap, never `person_NNN`."""
    doc = doc if doc is not None else _load_doc(workspace_root)
    container = unwrap_entities(doc) if isinstance(doc, dict) else {}
    idx = name_index_from_doc(doc)
    rec = None
    for pr in entities_collection(container, "people") if container else []:
        if isinstance(pr, dict) and str(pr.get("id")) == str(person_id):
            rec = pr
            break
    name = idx.get(str(person_id)) or UNRESOLVED_LABEL
    tail: list[str] = []
    if rec:
        role = (rec.get("role") or rec.get("title") or "").strip()
        if role:
            tail.append(humanize(role, idx))
        org_id = rec.get("primary_org_id") or rec.get("org_id")
        org_ids = rec.get("org_ids") if isinstance(rec.get("org_ids"), list) else []
        org_name = ""
        for oid in ([org_id] if org_id else []) + list(org_ids):
            if oid and idx.get(str(oid)):
                org_name = idx[str(oid)]
                break
        if not org_name and rec.get("org"):
            org_name = humanize(str(rec.get("org")), idx)
        if org_name:
            tail.append(org_name)
    line = f"### {name}"
    if tail:
        line += " — " + ", ".join(tail)
    return line


def recap_change_lines(workspace_root, since_ts: str, *,
                       now_iso: Optional[str] = None,
                       max_lines: Optional[int] = None,
                       skip_categories: Iterable[str] = ()) -> dict:
    """The weekly recap's 8b roll-up: `change_feed.changes_since` with EVERY
    line humanized at composition. Returns the feed's dict with `lines`
    carrying `text` substituted (and the original under `raw_text`), plus
    `texts` — the plain list the prose renders verbatim. `skip_categories`
    drops the categories another section owns (REVIEW_QUIET1 F-4)."""
    from change_feed import changes_since

    feed = changes_since(workspace_root, since_ts, now_iso=now_iso,
                         max_lines=max_lines)
    idx = name_index(workspace_root)
    skip = {str(c) for c in (skip_categories or ())}
    kept: list[dict] = []
    for row in feed.get("lines") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("category") or "") in skip:
            continue
        r = dict(row)
        r["raw_text"] = row.get("text") or ""
        r["text"] = humanize(r["raw_text"], idx)
        kept.append(r)
    out = dict(feed)
    out["lines"] = kept
    out["texts"] = [r["text"] for r in kept]
    return out


__all__ = [
    "INTERNAL_ID_RE",
    "UNRESOLVED_LABEL",
    "carries_internal_id",
    "humanize",
    "humanize_lines",
    "name_index",
    "name_index_from_doc",
    "person_header",
    "recap_change_lines",
]
