#!/usr/bin/env python3
"""
People-registry view regenerator (v3.17.1+).

Deterministically regenerates `_hq/views/PEOPLE.md` (+ the back-compat copy
`_hq/PEOPLE.md`) from `_hq/data/entities.json` (people + orgs) and
`_hq/data/events.jsonl` (interaction/meeting events → last-contact), per the
PEOPLE.md template in `references/VIEW_GENERATION.md`.

WHY THIS EXISTS:
PEOPLE.md is a *generated* view but — exactly like DECISION_LOG before
`render_decision_log.py` shipped — it had NO code generator. It relied on the
people-crm skill regenerating it freehand on every write, which it didn't
reliably do, so the registry drifted (M's workspace showed 69 people while the
substrate had 95, stale since 2026-05-10). This renderer makes the registry
deterministic; cleanup's weekly sweep (Phase 3.5) calls it so the view never
falls behind canonical substrate. Wrapper-aware (nested-vs-flat entities shape)
and shape-defensive on the deprecated `email`/`org_id` fields.

PEOPLEID1 (operator ruling 2026-09-02) — heading id marker:
Person-card headings used to render the id as a visible parenthetical —
`### Bo Stone (person_002)` — which leaked an internal token into a
customer-facing rendered view (JARGONVIEWS1 F5). The id is load-bearing
(`shared/FUZZY_ROUTER.md` documents PEOPLE.md as the name -> id lookup table
workspace-manager reads), so it could not simply be deleted. The ruling: keep
the id on the same heading line but move it into an HTML comment, invisible
when the view renders as markdown/HTML but still present in the raw text any
reader (human, script, or Claude reading the file) sees:

  ### Bo Stone <!-- id: person_002 -->

Marker grammar: `<!-- id: <id> -->`, one space after `id:`, immediately
following the name with a single space, nothing else on the line. Greppable
via `<!-- id: (person_[0-9a-z]+) -->`. `parse_person_headings()` /
`lookup_person_id()` below are the canonical backward-compatible readers —
they accept BOTH this shape and the old parenthetical shape, so a client
workspace whose PEOPLE.md has not yet regenerated still resolves correctly.
A workspace flips to the new shape automatically the next time this module's
`regenerate()` runs (any "cleanup" invocation, or any people-touching write
from workspace-manager / people-crm) — no separate migration step.

PUBLIC API:
  regenerate(workspace_root) -> dict   (counts; idempotent; atomic-write)
  parse_person_headings(rendered) -> dict[str, str]   (name -> id, both shapes)
  lookup_person_id(rendered, name) -> str | None       (exact-name lookup)

USAGE:
  python3 shared/scripts/render_people_view.py <workspace_root>
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atomic_write import atomic_write_text  # noqa: E402
from entities_io import entities_collection  # noqa: E402
from event_time import event_time  # noqa: E402

# TZDATE2 (same class as walk finding F-12): first-seen / last-interaction
# dates render the WORKSPACE-local day via the canonical tz.localize_date —
# a raw [:10] slice stamped the UTC date, so an evening-Pacific interaction
# dated a day late. Every call site passes the workspace path. UTC-slice
# stub only when tz.py itself is missing (stripped install).
try:
    from tz import localize_date as _localize_date  # noqa: E402
except ImportError:
    def _localize_date(ts: str | None, workspace_path: str | None = None) -> str:
        return ts[:10] if isinstance(ts, str) and ts else ""

CONFIDENCE_FLOOR = 0.40
REL_TYPE_ORDER = [
    "operating", "partner", "client", "board", "advisory", "investment",
    "portfolio_company", "beneficiary", "service_provider", "vendor",
    "prospect", "other",
]


# ---------------------------------------------------------------------------
# PEOPLEID1 — person-card heading id marker: writer + backward-compatible
# reader. See the module docstring for the ruling and the marker grammar.
# ---------------------------------------------------------------------------

# Matches a person-card heading in EITHER shape and captures the id:
#   new:  ### Bo Stone <!-- id: person_002 -->
#   old:  ### Bo Stone (person_002)
_HEADING_ID_RE = re.compile(
    r"^###\s+(?P<name>.+?)"
    r"(?:\s*<!--\s*id:\s*(?P<id_new>person_[0-9a-z]+)\s*-->"
    r"|\s*\((?P<id_old>person_[0-9a-z]+)\))"
    r"\s*$"
)


def parse_person_headings(rendered: str) -> dict[str, str]:
    """Backward-compatible reader: {canonical_name: id} for every person-card
    heading in a rendered PEOPLE.md, regardless of whether that heading was
    written in the old visible-parenthetical shape or the new HTML-comment
    marker shape (both are accepted for the transition — see module
    docstring). EXACT strings only: no fuzzing, no edit-distance, no Soundex.
    Fuzzy/phonetic name matching is entity_resolve.py's job, reading
    entities.json directly; this function's contract is deterministic exact
    lookup only, so two similarly-named people (e.g. "Bo Stone" and "Mira
    Stone") never collide here.
    """
    out: dict[str, str] = {}
    for line in rendered.splitlines():
        m = _HEADING_ID_RE.match(line.strip())
        if not m:
            continue
        pid = m.group("id_new") or m.group("id_old")
        name = m.group("name").strip()
        if name and pid:
            out[name] = pid
    return out


def lookup_person_id(rendered: str, name: str) -> str | None:
    """Exact (case- and whitespace-insensitive) name -> id lookup against a
    rendered PEOPLE.md. This is the canonical implementation of the
    name -> id step `shared/FUZZY_ROUTER.md` documents PEOPLE.md as providing
    for workspace-manager's Layer 3 name-mention routing: deterministic,
    no fuzziness, and unaffected by whether the source view is old-shape or
    new-shape. Returns None on no exact match — ambiguous / fuzzy resolution
    is out of scope here (see entity_resolve.resolve_all for that ladder)."""
    index = {
        " ".join(n.split()).casefold(): pid
        for n, pid in parse_person_headings(rendered).items()
    }
    return index.get(" ".join(name.split()).casefold())


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _entities_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "entities.json"


def _load_events(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    # R5 reader-honor: people-view never derives last-contact (or any card
    # signal) from a scope-masked account's history. Defensive — failure
    # leaves events unfiltered.
    try:
        from account_scope_gate import filter_masked_events
        out = filter_masked_events(out)
    except Exception:
        pass
    return out


def _load_collections(p: Path) -> dict:
    """Wrapper-aware: return the dict holding people/orgs/threads regardless of
    nested-under-`entities` vs flat top-level shape."""
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
            idx[o["id"]] = o.get("canonical_name") or o["id"]
    for t in entities_collection(view, "projects"):
        if isinstance(t, dict) and t.get("id"):
            idx[t["id"]] = (
                t.get("display_name") or t.get("canonical_name")
                or t.get("folder_name") or t["id"]
            )
    for pr in view.get("people") or []:
        if isinstance(pr, dict) and pr.get("id"):
            idx[pr["id"]] = pr.get("canonical_name") or pr["id"]
    return idx


def _person_org_id(p: dict) -> str | None:
    return p.get("primary_org_id") or p.get("org_id") or (
        (p.get("affiliation_ids") or [None])[0]
    )


def _person_emails(p: dict) -> list[str]:
    em = p.get("emails")
    if isinstance(em, list) and em:
        return [e for e in em if e]
    single = p.get("email")  # deprecated singular
    return [single] if single else []


def _last_interaction(person_id: str, events: list[dict], first_seen: str,
                      workspace_path: str | None = None) -> str:
    latest = None
    for ev in events:
        if ev.get("type") not in ("interaction", "meeting"):
            continue
        conf = ev.get("classification_confidence")
        if isinstance(conf, (int, float)) and conf < CONFIDENCE_FLOOR:
            continue
        pids = ev.get("person_ids") or (ev.get("data") or {}).get("person_ids") or []
        if person_id not in pids:
            continue
        ts = event_time(ev)
        if ts and (latest is None or ts > latest):
            latest = ts
    return _localize_date(latest or first_seen or "", workspace_path)


def _person_card(p: dict, name_idx: dict[str, str], events: list[dict],
                 workspace_path: str | None = None) -> str:
    name = p.get("canonical_name") or p.get("id") or "(unnamed)"
    out = [f"### {name} <!-- id: {p.get('id', '')} -->", ""]
    out.append(f"- **Role:** {p.get('role') or '—'}")
    org_id = _person_org_id(p)
    if org_id:
        # JARGONVIEWS1: an org id that is not in the index (pruned, merged, or
        # written before the org landed) used to render the raw `org_001`
        # token into the customer's registry. Fall back to the view's own
        # missing-value mark instead of an internal id.
        out.append(f"- **Primary Org:** {name_idx.get(org_id) or '—'}")
    emails = _person_emails(p)
    out.append(f"- **Email:** {', '.join(emails) if emails else '—'}")
    aliases = p.get("aliases") or []
    if aliases:
        out.append(f"- **Aliases:** {', '.join(aliases)}")
    threads = p.get("project_ids") or []
    if threads:
        # JARGONVIEWS1: same dangling-reference class as Primary Org above —
        # an unresolvable thread id is DROPPED from the list rather than
        # printed raw. A shorter honest list beats one carrying `project_003`.
        names = [n for n in (name_idx.get(t) for t in threads) if n]
        if names:
            out.append(f"- **Threads:** {', '.join(names)}")
    fs = _localize_date(p.get("first_seen") or "", workspace_path)
    if fs:
        out.append(f"- **First seen:** {fs}")
    li = _last_interaction(p.get("id", ""), events, p.get("first_seen", ""),
                           workspace_path)
    if li:
        out.append(f"- **Last interaction:** {li}")
    notes = p.get("notes")
    if notes:
        out.append(f"- **Notes:** {notes if len(notes) <= 200 else notes[:200].rstrip() + '…'}")
    out.append("")
    return "\n".join(out)


def regenerate(workspace_root: str | Path) -> dict[str, Any]:
    """Read entities.json + events.jsonl, render PEOPLE.md, atomic-write the
    view (+ back-compat copy). Returns counts. Idempotent (content-stable apart
    from the regenerated-at header)."""
    ws = Path(workspace_root)
    ws_str = str(ws)  # TZDATE2 — threaded to every date-rendering call
    view = _load_collections(_entities_path(ws))
    events = _load_events(_events_path(ws))
    name_idx = _name_index(view)

    people = [p for p in (view.get("people") or []) if isinstance(p, dict)]
    orgs = [o for o in (view.get("orgs") or []) if isinstance(o, dict)]

    active = [p for p in people if p.get("status") != "archived"]
    archived = [p for p in people if p.get("status") == "archived"]

    by_org: dict[Any, list[dict]] = defaultdict(list)
    for p in active:
        by_org[_person_org_id(p)].append(p)
    for k in by_org:
        by_org[k].sort(key=lambda p: (p.get("canonical_name") or "").lower())

    def children_of(oid: str) -> list[dict]:
        return sorted(
            [o for o in orgs if o.get("parent_org_id") == oid],
            key=lambda o: (o.get("canonical_name") or "").lower(),
        )

    rendered: set[str] = set()

    def org_cards(o: dict) -> list[str]:
        ppl = by_org.get(o.get("id"), [])
        if not ppl:
            return []
        rendered.add(o["id"])
        return [_person_card(p, name_idx, events, ws_str) for p in ppl]

    body: list[str] = []

    # Primary-focus orgs (no parent) first, with operating children nested.
    primary = sorted(
        [o for o in orgs if o.get("is_primary_focus") and not o.get("parent_org_id")],
        key=lambda o: (o.get("canonical_name") or "").lower(),
    )
    for o in primary:
        kids = children_of(o["id"]) if o.get("id") else []
        own = by_org.get(o.get("id"), [])
        if not (kids or own):
            continue
        body += [f"## {o.get('canonical_name') or o.get('id')}", ""]
        if o.get("scope") == "holding" and kids:
            for c in kids:
                cards = org_cards(c)
                if cards:
                    body += [f"### {c.get('canonical_name') or c.get('id')}", ""] + cards
            if own:
                body += org_cards(o)
        else:
            body += org_cards(o)

    # Other orgs grouped by relationship_type.
    other: list[str] = []
    for rt in REL_TYPE_ORDER:
        rt_orgs = sorted(
            [o for o in orgs
             if not o.get("is_primary_focus")
             and o.get("relationship_type") == rt
             and by_org.get(o.get("id"))],
            key=lambda o: (o.get("canonical_name") or "").lower(),
        )
        section: list[str] = []
        for o in rt_orgs:
            cards = org_cards(o)
            if cards:
                section += [f"#### {o.get('canonical_name') or o.get('id')}", ""] + cards
        if section:
            other += [f"### {rt.replace('_', ' ').title()}", ""] + section
    if other:
        body += ["## Other Orgs", ""] + other

    # Unaffiliated + any people whose org was missing/not rendered.
    unaff = list(by_org.get(None, []))
    for oid, ppl in by_org.items():
        if oid is not None and oid not in rendered:
            unaff += ppl
    if unaff:
        body += ["## Unaffiliated", ""]
        for p in sorted(unaff, key=lambda p: (p.get("canonical_name") or "").lower()):
            body.append(_person_card(p, name_idx, events, ws_str))

    if archived:
        names = sorted(a.get("canonical_name") or a.get("id", "") for a in archived)
        body += [f"## Archived ({len(archived)})", "", ", ".join(names[:50]), ""]

    now_iso = datetime.datetime.now().replace(microsecond=0).isoformat()
    header = [
        "<!-- AUTO-GENERATED by shared/scripts/render_people_view.py — do not edit by hand. -->",
        f"<!-- regenerated-at: {now_iso} -->",
        f"<!-- total-people: {len(active)} active, {len(archived)} archived -->",
        "<!-- source: _hq/data/entities.json + events.jsonl -->",
        "",
        "# People",
        "",
        f"_{len(active)} active · {len(archived)} archived · regenerated {now_iso}_",
        "",
        "---",
        "",
    ]
    content = "\n".join(header + body).rstrip() + "\n"

    view_path = ws / "_hq" / "views" / "PEOPLE.md"
    view_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view_path, content)
    try:  # back-compat copy at _hq/PEOPLE.md
        atomic_write_text(ws / "_hq" / "PEOPLE.md", content)
    except Exception:
        pass

    return {
        "active": len(active),
        "archived": len(archived),
        "view_path": str(view_path),
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_people_view.py <workspace_root>", file=sys.stderr)
        return 2
    ws = Path(argv[1])
    if not ws.exists():
        print(f"ABORT: workspace not found: {ws}", file=sys.stderr)
        return 2
    r = regenerate(ws)
    print(f"OK — regenerated PEOPLE.md ({r['active']} active, {r['archived']} archived)")
    print(f"  written to: {r['view_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
