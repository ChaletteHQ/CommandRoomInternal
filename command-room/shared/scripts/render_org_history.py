#!/usr/bin/env python3
"""Per-company history compiler (SPEC HIST1 Part C — D5, NET-NEW surface).

Companies get a history view for the FIRST time: `_hq/views/orgs/<org_id>.md`
compiles the org's scattered events — creation/changes, deals (PIPE1),
recorded facts, people joining/leaving (lineage), and every meeting /
commitment / touchpoint reaching the org directly (`org_ids[]`) or via an
affiliated thread — into a header + derived stats + relationship timeline +
people movement + open deals + context blocks.

Recency derives from events via org_activity (D6 — `org.last_interaction`
is a fossil, never read here; a zero-event org falls back to the
`first_seen` floor only). The view is an INTERNAL context file (CONTRACT
Rule 27 allowed path) and must stay leak-clean: entity identity lives in
the FILENAME; every free-text line is humanized through the name index.

PUBLIC API:
  render_org_history(workspace_root, org_id) -> dict
  compile_org_history(workspace_root, org_id) -> dict   (the assembly —
      board-pack-assembler reads derived stats + context from here)
  regenerate_changed(workspace_root) -> dict            (cleanup Phase 3.5)

USAGE:
  python3 shared/scripts/render_org_history.py <workspace_root> <org_id>
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
from cru_match import load_events_defensively  # noqa: E402
from event_time import event_time  # noqa: E402
from org_activity import event_org_ids, thread_org_map  # noqa: E402
from quantify import money_time_tag  # noqa: E402
from thread_activity import apply_reclassifications  # noqa: E402

CONFIDENCE_FLOOR = 0.40

TIMELINE_LABELS = {
    "meeting": "Meeting",
    "interaction": "Touchpoint",
    "commitment": "Commitment opened",
    "commitment_resolved": "Commitment closed",
    "org_created": "Company added",
    "engagement_created": "Engagement opened",
    "engagement_updated": "Engagement changed",
    "deal_created": "Deal opened",
    "deal_stage_changed": "Deal moved",
    "deal_won": "Deal won",
    "deal_lost": "Deal lost",
    "org_fact_observed": "Noted",
}

MOVE_TYPE = "person_org_changed"
FACT_TYPE = "org_fact_observed"
RETRACT_TYPE = "entity_fact_retracted"

TIMELINE_CAP = 30
FACTS_CAP = 10

_SOURCE_SEQ_RE = re.compile(r"<!--\s*source_seq=(\d+)\s*-->")
_INTERNAL_ID_RE = re.compile(
    r"\b(person|project|org|event|matter|engagement)_\d{3,}\b", re.IGNORECASE
)


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _load_entities_doc(ws: Path) -> dict:
    p = ws / "_hq" / "data" / "entities.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _collections(doc: dict) -> dict:
    return doc.get("entities") if isinstance(doc.get("entities"), dict) else doc


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


def _dedup_by_seq(events: list[dict]) -> list[dict]:
    """One event, one count — dedup by seq before any stat (second-eyes N1):
    an event reachable BOTH via org_ids[] and via an affiliated thread must
    count exactly once."""
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


def _gather(ws: Path, org_id: str, view: dict) -> tuple[list[dict], list[dict], int]:
    events, skipped = load_events_defensively(_events_path(ws))
    # honor reclassifications ONCE, right after the defensive load (RECL1 —
    # the objective_math pattern): every downstream walk (timeline, stats,
    # event_org_ids) reads the patched stream, so an event moved off an
    # org's thread leaves that org's timeline and joins the corrected one's.
    events = apply_reclassifications(events)
    t_org = thread_org_map(view)
    mine: list[dict] = []
    newest_seq = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        is_retract = ev.get("type") == RETRACT_TYPE and data.get("target_id") == org_id
        if not is_retract and org_id not in event_org_ids(ev, t_org):
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


def _org_deals(view: dict, org_id: str) -> tuple[list[dict], int, int]:
    """(open deal threads, total deal count, closed count) for the org —
    read straight off the PIPE1 `deal` objects on affiliated threads."""
    open_deals: list[dict] = []
    total = 0
    closed = 0
    for coll in ("threads", "projects"):
        for t in view.get(coll) or []:
            if not isinstance(t, dict):
                continue
            oid = t.get("affiliation_id") or t.get("org_id")
            if oid != org_id:
                continue
            deal = t.get("deal")
            if t.get("kind") != "deal" and not isinstance(deal, dict):
                continue
            total += 1
            if isinstance(deal, dict) and deal.get("outcome"):
                closed += 1
            else:
                open_deals.append(t)
    return open_deals, total, closed


def _org_people(view: dict, org_id: str) -> list[dict]:
    out = []
    for p in view.get("people") or []:
        if not isinstance(p, dict) or p.get("status") == "archived":
            continue
        ids = {p.get("primary_org_id"), p.get("org_id")}
        ids.update(p.get("affiliation_ids") or [])
        if org_id in ids:
            out.append(p)
    return out


def _timeline_label(ev: dict, name_idx: dict[str, str]) -> Optional[str]:
    kind = TIMELINE_LABELS.get(ev.get("type") or "")
    if kind is None:
        # org_updated rows are handled in compile_org_history (_money_rows) —
        # a money delta needs the NEXT event's before-snapshot to detect, and
        # routine record edits don't earn a timeline row.
        return None
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    text = data.get("title") or data.get("summary") or data.get("fact") or ""
    text = _humanize(text, name_idx)
    if len(text) > 90:
        text = text[:90].rstrip() + "…"
    return f"{kind}: {text}" if text else kind


def compile_org_history(workspace_root: str | Path, org_id: str) -> dict[str, Any]:
    """Compile the structured history for one org — the single assembly the
    view writer, `go [org] rollup`, and board-pack-assembler read."""
    ws = Path(workspace_root)
    doc = _load_entities_doc(ws)
    view = _collections(doc)
    name_idx = _name_index(view)

    org = next(
        (o for o in (view.get("orgs") or [])
         if isinstance(o, dict) and o.get("id") == org_id),
        None,
    )
    if org is None:
        raise KeyError(f"org_id not found: {org_id!r}")

    events, skipped, newest_seq = _gather(ws, org_id, view)
    confident = [e for e in events if _confident(e)]
    retracted = _retracted_seqs(events)

    # Derived recency (D6): events only — the stored last_interaction fossil
    # is never read; a zero-event org keeps first_seen as its only floor.
    dated = [d for d in (_event_date(e) for e in confident) if d]
    last_touch = max(dated) if dated else None
    meeting_dates = [
        _event_date(e) for e in confident
        if e.get("type") == "meeting" and _event_date(e)
    ]
    cadence = _median_gap_days(meeting_dates)

    first_seen = (org.get("first_seen") or "")[:10] or (dated[0] if dated else None)

    # Money tag through the ONE sanctioned tag composer. The grouped money
    # object rides in as item data so its inner (quantify-conventional) keys
    # resolve; no thread/deal required — this is the org-level tag.
    money = org.get("money") if isinstance(org.get("money"), dict) else None
    money_tag = money_time_tag({"data": dict(money)}, view) if money else None

    open_deals, deal_total, deal_closed = _org_deals(view, org_id)
    open_deal_rows = []
    for t in open_deals:
        deal = t.get("deal") if isinstance(t.get("deal"), dict) else {}
        label = _humanize(
            t.get("display_name") or t.get("canonical_name") or t.get("folder_name") or "(unnamed deal)",
            name_idx,
        )
        stage = deal.get("stage")
        tag = money_time_tag(t, view)
        row = label
        if stage:
            row += f" — {stage.replace('_', ' ')}"
        if tag:
            row += f" · {tag}"
        open_deal_rows.append(row)

    people = _org_people(view, org_id)

    moves = []
    for ev in events:
        if ev.get("type") != MOVE_TYPE:
            continue
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        date = _event_date(ev) or "(undated)"
        who = _humanize(data.get("canonical_name") or "", name_idx) or \
            name_idx.get(data.get("person_id") or "", "(name on file)")
        if data.get("to_org_id") == org_id:
            other = name_idx.get(data.get("from_org_id") or "", "")
            line = f"{date} — {who} joined" + (f" (from {other})" if other else "")
            if data.get("to_role"):
                line += f" as {data['to_role']}"
        elif data.get("from_org_id") == org_id:
            other = name_idx.get(data.get("to_org_id") or "", "")
            line = f"{date} — {who} left" + (f" (to {other})" if other else "")
        else:
            continue
        moves.append(_humanize(line, name_idx))

    facts = []
    for ev in events:
        if ev.get("type") != FACT_TYPE:
            continue
        seq = ev.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool) and seq in retracted:
            continue  # retracted facts disappear from the render (D3/S1)
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        fact = _humanize(data.get("fact") or "", name_idx)
        if fact:
            facts.append(f"{fact} ({_event_date(ev) or 'undated'})")

    # Account-value change trail (D4/D10): org_updated carries only the
    # BEFORE snapshot, so the after-state of update i is update i+1's before
    # (or the current record for the newest). A money delta between the two
    # earns a timeline row; routine record edits don't.
    money_rows: list[dict] = []
    org_updates = [
        e for e in confident
        if e.get("type") == "org_updated"
        and isinstance(e.get("data"), dict)
        and isinstance(e["data"].get("before"), dict)
    ]
    for i, ev in enumerate(org_updates):
        before_money = ev["data"]["before"].get("money")
        after_state = (
            org_updates[i + 1]["data"]["before"]
            if i + 1 < len(org_updates) else org
        )
        after_money = after_state.get("money") if isinstance(after_state, dict) else None
        if before_money != after_money and (before_money or after_money):
            tag = money_time_tag({"data": dict(after_money)}, view) if isinstance(after_money, dict) else None
            label = "Account value updated" + (f" — {tag}" if tag else "")
            money_rows.append({"date": _event_date(ev) or "(undated)", "label": label})

    timeline = []
    for ev in reversed(confident):
        seq = ev.get("seq")
        if (ev.get("type") == FACT_TYPE and isinstance(seq, int)
                and not isinstance(seq, bool) and seq in retracted):
            continue  # a retracted fact disappears from EVERY block (D3/S1)
        label = _timeline_label(ev, name_idx)
        if label:
            timeline.append({"date": _event_date(ev) or "(undated)", "label": label})
    if money_rows:
        timeline.extend(reversed(money_rows))
        timeline.sort(key=lambda t: t["date"], reverse=True)

    return {
        "org": org,
        "name": org.get("canonical_name") or "(unnamed company)",
        "relationship_type": org.get("relationship_type"),
        "tier": org.get("tier"),
        "money_tag": money_tag,
        "first_seen": first_seen,
        "last_touch": last_touch,          # derived — the fossil is never read
        "meeting_cadence_days": cadence,
        "people_count": len(people),
        "deal_total": deal_total,
        "deal_closed": deal_closed,
        "open_deal_rows": open_deal_rows,
        "moves": moves,
        "facts": facts,
        "timeline": timeline,
        "event_count": len(confident),
        "skipped_lines": len(skipped),
        "source_seq": newest_seq,
    }


def _render_md(c: dict[str, Any]) -> str:
    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    out: list[str] = [
        "<!-- AUTO-GENERATED by shared/scripts/render_org_history.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- source_seq={c['source_seq']} -->",
        "",
        f"# {c['name']}",
        "",
    ]
    id_bits = [x for x in (c.get("relationship_type"), c.get("tier")) if x]
    header_line = " · ".join(id_bits)
    if c.get("money_tag"):
        header_line = (header_line + " · " if header_line else "") + c["money_tag"]
    if header_line:
        out += [header_line, ""]
    if c.get("skipped_lines"):
        out += [f"Note: {c['skipped_lines']} history line(s) couldn't be read and were skipped.", ""]

    stats: list[str] = []
    if c.get("first_seen"):
        stats.append(f"- **First seen:** {c['first_seen']}")
    if c.get("last_touch"):
        stats.append(f"- **Last touch:** {c['last_touch']}")
    if c.get("meeting_cadence_days") is not None:
        stats.append(f"- **Meeting cadence:** ~every {c['meeting_cadence_days']}d")
    if c.get("people_count"):
        stats.append(f"- **People on file:** {c['people_count']}")
    if c.get("deal_total"):
        stats.append(
            f"- **Deals:** {c['deal_total']} tracked"
            + (f" ({c['deal_closed']} closed)" if c.get("deal_closed") else "")
        )
    if stats:
        out += stats + [""]

    if c["open_deal_rows"]:
        out += ["## Open deals", ""]
        out += [f"- {r}" for r in c["open_deal_rows"]] + [""]

    if c["moves"]:
        out += ["## People movement", ""]
        out += [f"- {m}" for m in c["moves"]] + [""]

    if c["facts"]:
        out += ["## Context & news", ""]
        shown = c["facts"][-FACTS_CAP:]
        out += [f"- {f}" for f in reversed(shown)]
        if len(c["facts"]) > FACTS_CAP:
            out.append(f"- …and {len(c['facts']) - FACTS_CAP} earlier")
        out.append("")

    if len(c["timeline"]) >= 2:
        out += ["## Relationship timeline", ""]
        shown = c["timeline"][:TIMELINE_CAP]
        out += [f"- {t['date']} — {t['label']}" for t in shown]
        if len(c["timeline"]) > TIMELINE_CAP:
            out.append(f"- …and {len(c['timeline']) - TIMELINE_CAP} earlier (collapsed)")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def view_path(workspace_root: str | Path, org_id: str) -> Path:
    return Path(workspace_root) / "_hq" / "views" / "orgs" / f"{org_id}.md"


def render_org_history(workspace_root: str | Path, org_id: str) -> dict[str, Any]:
    """Compile + atomic-write the per-company history view. Idempotent
    (content-stable apart from the regenerated-at header)."""
    compiled = compile_org_history(workspace_root, org_id)
    content = _render_md(compiled)
    path = view_path(workspace_root, org_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    return {"path": str(path), "written": True, "source_seq": compiled["source_seq"]}


def regenerate_changed(workspace_root: str | Path) -> dict[str, Any]:
    """cleanup Phase 3.5 hook — re-render only the EXISTING org views whose
    source_seq marker is older than the newest event tagging that org."""
    ws = Path(workspace_root)
    views_dir = ws / "_hq" / "views" / "orgs"
    refreshed: list[str] = []
    checked = 0
    if not views_dir.is_dir():
        return {"checked": 0, "refreshed": []}
    doc = _load_entities_doc(ws)
    view = _collections(doc)
    for f in sorted(views_dir.glob("org_*.md")):
        org_id = f.stem
        checked += 1
        try:
            m = _SOURCE_SEQ_RE.search(f.read_text(encoding="utf-8"))
        except OSError:
            m = None
        block_seq = int(m.group(1)) if m else -1
        try:
            _events, _skipped, newest = _gather(ws, org_id, view)
        except Exception:
            continue
        if newest > block_seq:
            try:
                render_org_history(ws, org_id)
                refreshed.append(org_id)
            except KeyError:
                continue
    return {"checked": checked, "refreshed": refreshed}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("Usage: python3 render_org_history.py <workspace_root> <org_id>", file=sys.stderr)
        return 2
    r = render_org_history(argv[1], argv[2])
    print(f"OK — wrote {r['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
