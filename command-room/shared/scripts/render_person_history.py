#!/usr/bin/env python3
"""Per-person history compiler (SPEC HIST1 Part C — D5).

Compiles the events that already exist for one person — meetings,
touchpoints, commitments, intros, lineage moves, recorded facts, dormancy
flags — into a readable durable view: `_hq/views/people/<person_id>.md`.
Follows the ratified brain-substrate doctrine (recompute from events on
read, never a frozen `history[]` field on the entity — D1) and the
render_people_view / render_master_tracker renderer pattern.

The view is an INTERNAL context file (CONTRACT Rule 27 — `_hq/views/*.md`
is allowed markdown: working memory, not a deliverable). It must stay
leak-clean: no internal entity ids, event-type names, or schema fields in
the content — the entity identity lives in the FILENAME only, and every
free-text line is humanized through the name index.

PUBLIC API:
  render_person_history(workspace_root, person_id) -> dict
      (compile + atomic-write the view; idempotent apart from the
       source_seq marker; returns {path, written, source_seq})
  person_timeline_points(workspace_root, person_id, max_points=12) -> list
      (the durable source for call-prep's
       prep_pipeline.build_relationship_timeline — {date, label} points)
  regenerate_changed(workspace_root) -> dict
      (cleanup Phase 3.5 hook: re-render only the EXISTING person views
       whose newest entity-tagged event seq is newer than the view's
       source_seq marker — a quiet workspace is a fast no-op; views are
       CREATED on `go [person]`, not by the sweep, so the per-entity file
       set only grows for people the user actually opens)

USAGE:
  python3 shared/scripts/render_person_history.py <workspace_root> <person_id>
"""
from __future__ import annotations

import datetime
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_text  # noqa: E402
from cru_match import (  # noqa: E402
    load_events_defensively,
    load_open_commitments,
    split_pending_review,
)
from event_time import event_time  # noqa: E402

CONFIDENCE_FLOOR = 0.40

# Touch semantics mirror render_people_view._last_interaction: meetings and
# interactions are the "we talked" signal that drives first/last-touch and
# cadence. Other families render in the timeline but don't count as touches.
TOUCH_TYPES = frozenset({"meeting", "interaction"})

# Event families compiled into the timeline, with the human label each
# renders under (internal type names must never appear in the view — the
# leak scanner's event-type patterns are the enforcement).
TIMELINE_LABELS = {
    "meeting": "Meeting",
    "interaction": "Touchpoint",
    "commitment": "Commitment opened",
    "commitment_resolved": "Commitment closed",
    "intro_made": "Intro made",
    "intro_landed": "Intro landed",
    "intro_didnt_land": "Intro didn't land",
    "dormancy_signal": "Went quiet",
}

LINEAGE_TYPES = frozenset({"person_role_changed", "person_org_changed"})
FACT_TYPE = "person_fact_observed"
RETRACT_TYPE = "entity_fact_retracted"

TIMELINE_CAP = 30
FACTS_PER_CATEGORY_CAP = 8

_SOURCE_SEQ_RE = re.compile(r"<!--\s*source_seq=(\d+)\s*-->")
_INTERNAL_ID_RE = re.compile(
    r"\b(person|project|org|event|matter|engagement)_\d{3,}\b", re.IGNORECASE
)

CATEGORY_LABELS = {
    "preference": "Preferences",
    "contact": "Contact",
    "personal": "Personal",
    "role": "Role",
    "company_news": "Company news",
    "other": "Other",
}


# ---------------------------------------------------------------------------
# Loading helpers (wrapper-aware, shape-defensive — real-data fixture gotcha)
# ---------------------------------------------------------------------------

def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _load_collections(ws: Path) -> dict:
    p = ws / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("entities") if isinstance(data.get("entities"), dict) else data


def _name_index(view: dict) -> dict[str, str]:
    idx: dict[str, str] = {}
    for o in view.get("orgs") or []:
        if isinstance(o, dict) and o.get("id"):
            idx[o["id"]] = o.get("canonical_name") or "(unnamed company)"
    for coll in ("threads", "projects"):
        for t in view.get(coll) or []:
            if isinstance(t, dict) and t.get("id"):
                idx[t["id"]] = (
                    t.get("display_name") or t.get("canonical_name")
                    or t.get("folder_name") or "(unnamed thread)"
                )
    for pr in view.get("people") or []:
        if isinstance(pr, dict) and pr.get("id"):
            idx[pr["id"]] = pr.get("canonical_name") or "(unnamed person)"
    return idx


def _humanize(text: str, name_idx: dict[str, str]) -> str:
    """Replace internal entity-id tokens with resolved names; strip the
    unresolvable rest. The view must be leak-clean — ids never render."""
    if not text:
        return ""

    def _sub(m: re.Match) -> str:
        return name_idx.get(m.group(0), "").strip() or "(name on file)"

    return _INTERNAL_ID_RE.sub(_sub, str(text)).strip()


def _event_date(ev: dict) -> str:
    ts = event_time(ev)
    return (ts or "")[:10]


def _confident(ev: dict) -> bool:
    conf = ev.get("classification_confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        return conf >= CONFIDENCE_FLOOR
    return True


def _event_persons(ev: dict) -> set[str]:
    """Every person id an event references — top-level person_ids[],
    data.person_id / person_ids[], and commitment owner/counterparty."""
    out: set[str] = set()
    top = ev.get("person_ids")
    if isinstance(top, list):
        out.update(x for x in top if isinstance(x, str))
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if isinstance(data.get("person_id"), str):
        out.add(data["person_id"])
    inner = data.get("person_ids")
    if isinstance(inner, list):
        out.update(x for x in inner if isinstance(x, str))
    for k in ("owner_id", "owner_person_id", "counterparty_id"):
        if isinstance(data.get(k), str):
            out.add(data[k])
    return out


def _dedup_by_seq(events: list[dict]) -> list[dict]:
    """De-duplicate the entity's event set by seq before any count/cadence
    stat (HIST1 second-eyes N1) — an event reachable through two fields must
    count exactly once. Events with no usable seq are kept as-is."""
    out: list[dict] = []
    seen: set[int] = set()
    for ev in events:
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            if seq in seen:
                continue
            seen.add(seq)
        out.append(ev)
    return out


def _gather(ws: Path, person_id: str) -> tuple[list[dict], list[dict], int]:
    """(person-events sorted by time asc, skipped-line records, newest seq
    tagging this person)."""
    events, skipped = load_events_defensively(_events_path(ws))
    mine: list[dict] = []
    newest_seq = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        is_retract = ev.get("type") == RETRACT_TYPE and data.get("target_id") == person_id
        if not is_retract and person_id not in _event_persons(ev):
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq < 10**10:
            newest_seq = max(newest_seq, seq)
        mine.append(ev)
    mine = _dedup_by_seq(mine)
    mine.sort(key=lambda e: event_time(e) or "")
    return mine, skipped, newest_seq


def _retracted_seqs(events: list[dict]) -> set[int]:
    out: set[int] = set()
    for ev in events:
        if ev.get("type") != RETRACT_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        rs = data.get("retracts_seq")
        if isinstance(rs, (int, float)) and not isinstance(rs, bool):
            out.add(int(rs))
    return out


def _median_gap_days(dates: list[str]) -> Optional[int]:
    """Median gap between consecutive distinct touch dates. None below 2
    touches (one point is not a cadence)."""
    parsed: list[datetime.date] = []
    for d in sorted(set(x for x in dates if x)):
        try:
            parsed.append(datetime.date.fromisoformat(d[:10]))
        except ValueError:
            continue
    if len(parsed) < 2:
        return None
    gaps = [(b - a).days for a, b in zip(parsed, parsed[1:])]
    gaps = [g for g in gaps if g >= 0]
    if not gaps:
        return None
    return int(round(statistics.median(gaps)))


def _open_commitment_count(ws: Path, person_id: str) -> Optional[int]:
    """Gate-2 discipline: the open set comes from the canonical projector,
    never a raw event grep. None (drop the stat) when the projector can't
    load — never a fabricated zero."""
    try:
        # INTAKE — unconfirmed extractions never count toward a person's
        # open-item stat: the extractor's guess is not this person's ledger.
        open_items, _needs_review = split_pending_review(
            load_open_commitments(_events_path(ws)))
    except Exception:
        return None
    n = 0
    for item in open_items or []:
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        ids = {data.get(k) for k in ("owner_id", "owner_person_id", "counterparty_id")}
        if person_id in ids:
            n += 1
    return n


def _timeline_label(ev: dict, name_idx: dict[str, str]) -> Optional[str]:
    kind = TIMELINE_LABELS.get(ev.get("type") or "")
    if not kind:
        return None
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    text = data.get("title") or data.get("summary") or data.get("fact") or ""
    text = _humanize(text, name_idx)
    if len(text) > 90:
        text = text[:90].rstrip() + "…"
    return f"{kind}: {text}" if text else kind


# ---------------------------------------------------------------------------
# Compile + render
# ---------------------------------------------------------------------------

def compile_person_history(workspace_root: str | Path, person_id: str) -> dict[str, Any]:
    """Compile the structured history for one person. Returns a dict with
    header/stats/timeline/lineage/facts plus the source-seq high-water —
    the single assembly both the view writer and person_timeline_points
    read (one derivation, every surface — the F-54 lesson)."""
    ws = Path(workspace_root)
    view = _load_collections(ws)
    name_idx = _name_index(view)

    person = next(
        (p for p in (view.get("people") or [])
         if isinstance(p, dict) and p.get("id") == person_id),
        None,
    )
    if person is None:
        raise KeyError(f"no person with id {person_id!r}")

    events, skipped, newest_seq = _gather(ws, person_id)
    confident = [e for e in events if _confident(e)]
    retracted = _retracted_seqs(events)

    touches = [e for e in confident if e.get("type") in TOUCH_TYPES]
    touch_dates = [d for d in (_event_date(e) for e in touches) if d]

    first_touch = touch_dates[0] if touch_dates else None
    last_touch = touch_dates[-1] if touch_dates else None
    cadence = _median_gap_days(touch_dates)

    how_we_met = None
    if touches:
        first = touches[0]
        data = first.get("data") if isinstance(first.get("data"), dict) else {}
        text = _humanize(data.get("title") or data.get("summary") or "", name_idx)
        label = TIMELINE_LABELS.get(first.get("type") or "", "Touchpoint")
        how_we_met = f"{_event_date(first)} — {label.lower()}" + (f": {text}" if text else "")

    timeline = []
    for ev in reversed(confident):
        label = _timeline_label(ev, name_idx)
        if label:
            timeline.append({"date": _event_date(ev) or "(undated)", "label": label})

    lineage = []
    for ev in events:
        if ev.get("type") not in LINEAGE_TYPES:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        date = _event_date(ev) or "(undated)"
        if ev.get("type") == "person_role_changed":
            frm, to = data.get("from_role"), data.get("to_role")
            org = name_idx.get(data.get("org_id") or "", "")
            line = f"{date} — Role: {frm or '(unknown)'} → {to or '(unknown)'}"
            if org:
                line += f" at {org}"
        else:
            frm = name_idx.get(data.get("from_org_id") or "", "") or "(unknown company)"
            to = name_idx.get(data.get("to_org_id") or "", "") or "(unknown company)"
            line = f"{date} — Moved: {frm} → {to}"
            if data.get("to_role"):
                line += f" (now {data['to_role']})"
        lineage.append(_humanize(line, name_idx))

    facts: dict[str, list[str]] = {}
    for ev in events:
        if ev.get("type") != FACT_TYPE:
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq in retracted:
            continue  # retracted facts disappear from the render (D3/S1)
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        fact = _humanize(data.get("fact") or "", name_idx)
        if not fact:
            continue
        cat = data.get("category") if data.get("category") in CATEGORY_LABELS else "other"
        facts.setdefault(cat, []).append(f"{fact} ({_event_date(ev) or 'undated'})")

    org_id = person.get("primary_org_id") or person.get("org_id") or (
        (person.get("affiliation_ids") or [None])[0]
    )

    return {
        "person": person,
        "name": person.get("canonical_name") or "(unnamed)",
        "role": person.get("role"),
        "org_name": name_idx.get(org_id, None) if org_id else None,
        "how_we_met": how_we_met,
        "first_touch": first_touch or (person.get("first_seen") or "")[:10] or None,
        "last_touch": last_touch,          # derived — never the stored field
        "touch_count": len(touches),
        "cadence_days": cadence,
        "open_commitments": _open_commitment_count(ws, person_id),
        "timeline": timeline,
        "lineage": lineage,
        "facts": facts,
        "event_count": len(confident),
        "skipped_lines": len(skipped),
        "source_seq": newest_seq,
    }


def _render_md(c: dict[str, Any]) -> str:
    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    out: list[str] = [
        "<!-- AUTO-GENERATED by shared/scripts/render_person_history.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- source_seq={c['source_seq']} -->",
        "",
        f"# {c['name']}",
        "",
    ]
    id_line = [x for x in (c.get("role"), c.get("org_name")) if x]
    if id_line:
        out += [" · ".join(id_line), ""]
    if c.get("skipped_lines"):
        out += [f"Note: {c['skipped_lines']} history line(s) couldn't be read and were skipped.", ""]

    stats: list[str] = []
    if c.get("how_we_met"):
        stats.append(f"- **How we met:** {c['how_we_met']}")
    if c.get("first_touch"):
        stats.append(f"- **First touch:** {c['first_touch']}")
    if c.get("last_touch"):
        stats.append(f"- **Last touch:** {c['last_touch']}")
    if c.get("touch_count"):
        stats.append(f"- **Touches:** {c['touch_count']}")
    if c.get("cadence_days") is not None:
        stats.append(f"- **Cadence:** ~every {c['cadence_days']}d")
    if c.get("open_commitments"):
        stats.append(f"- **Open commitments:** {c['open_commitments']}")
    if stats:
        out += stats + [""]

    if c["lineage"]:
        out += ["## Role & company history", ""]
        out += [f"- {line}" for line in c["lineage"]] + [""]

    if c["facts"]:
        out += ["## Facts on file", ""]
        for cat in ("preference", "contact", "personal", "role", "company_news", "other"):
            items = c["facts"].get(cat)
            if not items:
                continue
            out.append(f"**{CATEGORY_LABELS[cat]}**")
            shown = items[-FACTS_PER_CATEGORY_CAP:]
            out += [f"- {i}" for i in reversed(shown)]
            if len(items) > FACTS_PER_CATEGORY_CAP:
                out.append(f"- …and {len(items) - FACTS_PER_CATEGORY_CAP} earlier")
            out.append("")

    # Drop-empty: a one-event history is a header card, not a timeline —
    # mirror the one-point-strip rule (an empty frame is worse than nothing).
    if len(c["timeline"]) >= 2:
        out += ["## Timeline", ""]
        shown = c["timeline"][:TIMELINE_CAP]
        out += [f"- {t['date']} — {t['label']}" for t in shown]
        if len(c["timeline"]) > TIMELINE_CAP:
            out.append(f"- …and {len(c['timeline']) - TIMELINE_CAP} earlier (collapsed)")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def view_path(workspace_root: str | Path, person_id: str) -> Path:
    return Path(workspace_root) / "_hq" / "views" / "people" / f"{person_id}.md"


def render_person_history(workspace_root: str | Path, person_id: str) -> dict[str, Any]:
    """Compile + atomic-write the per-person history view. Idempotent
    (content-stable apart from the regenerated-at header)."""
    compiled = compile_person_history(workspace_root, person_id)
    content = _render_md(compiled)
    path = view_path(workspace_root, person_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    return {"path": str(path), "written": True, "source_seq": compiled["source_seq"]}


def person_timeline_points(
    workspace_root: str | Path,
    person_id: str,
    max_points: int = 12,
) -> list[dict]:
    """The durable relationship-timeline source (HIST1 D7): call-prep feeds
    these straight into prep_pipeline.build_relationship_timeline instead of
    re-deriving points from scratch per brief. Newest-last {date, label}
    dicts, capped."""
    compiled = compile_person_history(workspace_root, person_id)
    points = list(reversed(compiled["timeline"]))  # oldest → newest
    if len(points) > max_points:
        points = points[-max_points:]
    return [{"date": p["date"], "label": p["label"]} for p in points]


def regenerate_changed(workspace_root: str | Path) -> dict[str, Any]:
    """cleanup Phase 3.5 hook — re-render only the EXISTING person views
    whose source_seq marker is older than the newest event tagging that
    person. Views are created on `go [person]`; the sweep keeps them fresh."""
    ws = Path(workspace_root)
    views_dir = ws / "_hq" / "views" / "people"
    refreshed: list[str] = []
    checked = 0
    if not views_dir.is_dir():
        return {"checked": 0, "refreshed": []}
    for f in sorted(views_dir.glob("person_*.md")):
        person_id = f.stem
        checked += 1
        try:
            m = _SOURCE_SEQ_RE.search(f.read_text(encoding="utf-8"))
        except OSError:
            m = None
        block_seq = int(m.group(1)) if m else -1
        try:
            _events, _skipped, newest = _gather(ws, person_id)
        except Exception:
            continue
        if newest > block_seq:
            try:
                render_person_history(ws, person_id)
                refreshed.append(person_id)
            except KeyError:
                continue  # view exists for a deleted/merged person — leave for archive pass
    return {"checked": checked, "refreshed": refreshed}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python3 render_person_history.py <workspace_root> <person_id>", file=sys.stderr)
        return 2
    r = render_person_history(argv[1], argv[2])
    print(f"OK — wrote {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
