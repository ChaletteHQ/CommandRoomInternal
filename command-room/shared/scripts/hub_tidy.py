#!/usr/bin/env python3
"""What the Hub's Tidy section reads (cr1#1584, pull request 1 of 2).

The Tidy section shows the workspace's loose ends as piles, each cleared
with one press and Undo, on the Work tab's pile pattern (cr1#1540): the feed
decides what is in a pile, never the page; one press saves the same choice
each item's own answer would; the Apply run asks the pile's rule again
before it writes. This module is the READ side. It owns no rule: every pile
is the answer of the function the product already asks the question with.

  * `stalled`: a project the weekly lifecycle pass asks about
    (`lifecycle_pass`): a live project quiet past `PROPOSE_DORMANT_DAYS`,
    or a dormant one quiet past `AUTO_ARCHIVE_DAYS`. It is what
    `lifecycle_pass.plan_lifecycle` would ask on its next run (uncapped:
    its `propose`, `dormant` and `archive` lists), plus every project whose
    question the pass already asked and that still stands open (read as the
    typed `stalled projects` reads it, a snoozed one left out) while it is
    still quiet past the pass's line by the pass's own clock, minus a
    project the owner answered inside the pass's own cooldown
    (`proposal_ledger.active_cooldowns`). The press is the question's own
    `archive` answer (`thread_archive.archive_thread`);
  * `lone_folder`: a folder at the workspace's top no project points to,
    integrity check C10 (`integrity_check.scan_project_structure`), less a
    folder whose name a project already holds (the relink of audit item B3,
    left for the next pull request), one that holds a project's folder or is
    named for a company by any of its names (where projects are grouped),
    one that holds a workspace of its own, and one whose name names a person
    the owner keeps off limits or personal (counted in `left_out`, never
    named; the Apply run's re-check reads the same rule, so a press on such
    a folder is refused). The press makes it a project
    (`thread_writer.create_thread` with that folder) under the owner's own
    company (`files_under`, the work-stream rule), which its card names;
  * `no_home`: an open promise on no thread, the Hub's own "No home yet"
    rows (`cockpit_helpers._feed_no_home`, cr1#1289), lines only heard left
    out. The press is each row's own "Assign to";
  * `loose_docs`: a document the shelf marks unfiled (`doc_shelf`: no
    thread's folder holds it, and it is not under the system folder), read
    from the stored shelf and filed again against today's projects, less one
    inside a folder of the `lone_folder` pile (making that folder a project
    files it), and less one whose name or folder names a person the owner
    keeps off limits or personal, by the wall the imports use
    (`import_wall`, cr1#1577), counted in `left_out` and never named. No
    press yet: filing a document waits on its one home (cr1#1581) and the
    copy-or-link rule.

READ ONLY, and it never raises into a feed: a pile that cannot be read is
named in `errors` and offered nowhere. The writes are
`cockpit_helpers.apply_week_choices`' (the `tidy` rows) and
`cockpit_helpers.apply_commitment_choices`' (the No home yet rows).
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

PILE_STALLED = "stalled"
PILE_FOLDER = "lone_folder"
PILE_NO_HOME = "no_home"
PILE_DOCS = "loose_docs"
#: Pull request 2 (cr1#1584): a project whose same-named folder points to
#: nothing (audit item B3), and a folder holding two session-notes files.
PILE_RELINK = "relink"
PILE_NOTES = "two_notes"
#: A file of the owner's at the root of a project's folder (`root_files`,
#: integrity code C21).
PILE_LOOSE = "loose_files"
#: Command Room's own leftovers: no pile, one status line with one press.
CLUTTER = "clutter"
PILES = (PILE_STALLED, PILE_FOLDER, PILE_NO_HOME, PILE_DOCS, PILE_RELINK, PILE_NOTES, PILE_LOOSE, CLUTTER)
#: The piles whose press is a `tidy` choice row, and that press. The Apply
#: run writes them through `apply_week_choices`, after this pile's rule.
TIDY_VERBS = {PILE_STALLED: "archive", PILE_FOLDER: "make project", PILE_RELINK: "link folder",
              PILE_NOTES: "merge notes", PILE_LOOSE: "file away", CLUTTER: "tidy clutter"}
#: Each press's Undo after Apply, a `tidy` row of its own naming the write it
#: takes back.
TIDY_BACK = {"archive": "restore", "make project": "unmake project", "link folder": "unlink folder",
             "merge notes": "unmerge notes", "file away": "put back", "tidy clutter": "untidy clutter"}
VERBS = frozenset(TIDY_VERBS.values()) | frozenset(TIDY_BACK.values())
#: The most items one pile carries to the page; the count travels whole.
ITEMS_CAP = 100
#: A pile's id on a choice row: `<kind>:<the read>-<a token>`.
PILE_ID_CAP = 80


def pile_kind(pile_id: Any) -> str:
    """The Tidy pile a choice row's `pile` names, or ""."""
    text = str(pile_id or "")
    if not text or len(text) > PILE_ID_CAP:
        return ""
    kind = text.split(":", 1)[0]
    return kind if kind in PILES else ""


def _projects(root: Path) -> List[Dict[str, Any]]:
    from entities_io import entities_collection, read_entities_json

    ent = read_entities_json(root / "_hq" / "data" / "entities.json")
    return [t for t in entities_collection(ent, "projects") if isinstance(t, dict) and t.get("id")]


def _name(t: Dict[str, Any]) -> str:
    return str(t.get("canonical_name") or t.get("display_name") or t.get("name") or t.get("id") or "")[:160]


def stalled(workspace_root, now_iso: str) -> Dict[str, Dict[str, Any]]:
    """`{project id: {id, name, status, quiet_days}}`: the projects the
    lifecycle pass asks about now, asked already or not. Raises when the
    book cannot be read."""
    import lifecycle_pass as lp
    from brain_proposals import load_open_proposals
    from proposal_ledger import active_cooldowns

    root = Path(str(workspace_root))
    projects = {str(t["id"]): t for t in _projects(root)}
    # Uncapped: a cap is how many one weekly run may ask, not how many stand.
    plan = lp.plan_lifecycle(root, now_iso=now_iso, caps={"propose": 0, "dormant": 0, "archive": 0, "revive": 0})
    cool = {str(f) for f in active_cooldowns(root, lp.DETECTOR, now_iso=now_iso)}
    out: Dict[str, Dict[str, Any]] = {}

    def add(tid: str, want: str, quiet: Optional[float]) -> None:
        t = projects.get(tid)
        if t is None or t.get("kind") in lp.EXCLUDED_KINDS:
            return
        if str(t.get("status") or "").lower() != want:
            return
        fp = (lp.ARCHIVE_FINGERPRINT_PREFIX if want == "dormant" else lp.FINGERPRINT_PREFIX) + tid
        if fp in cool:
            return  # the owner answered it inside the pass's own cooldown
        out[tid] = {"id": tid, "name": _name(t), "status": want, "quiet_days": int(quiet)}

    for name, want in (("propose", "active"), ("dormant", "active"), ("archive", "dormant")):
        for r in plan.get(name) or []:
            if isinstance(r.get("quiet_days"), (int, float)):
                add(str(r.get("thread_id") or ""), want, r["quiet_days"])
    # The questions the pass already asked, still open, read as the typed
    # `stalled projects` reads them (a snoozed or held one left out): the
    # pile is where they are answered now, so they stay in it while they
    # stand AND the project is still quiet past the pass's own line, by the
    # pass's own clock (review of #1623: a project asked on day 31 that had
    # a meeting on day 35 is no longer quiet, and a press must not archive
    # it). A dormant project the pass would bring back has moved within its
    # revive window, so it is never past the line either.
    last = None
    now = lp._parse(now_iso)
    for p in load_open_proposals(str(root), "on-demand", now_iso=now_iso):
        fp = str(p.get("fingerprint") or "")
        if p.get("kind") != "dormancy" or not fp.startswith(lp.FINGERPRINT_PREFIX):
            continue
        tid = str(p.get("thread_id") or "")
        want = "dormant" if fp.startswith(lp.ARCHIVE_FINGERPRINT_PREFIX) else "active"
        if tid in out or tid not in projects or now is None:
            continue
        if last is None:
            last = {k: dt for k, (dt, _seq) in lp._last_activity_full(root).items()}
        quiet = _still_quiet(projects[tid], want, last, now)
        if quiet is not None:
            add(tid, want, quiet)
    return out


def _still_quiet(t: Dict[str, Any], want: str, last: Dict[str, Any], now) -> Optional[float]:
    """The project's quiet days by the lifecycle pass's own clock
    (`lifecycle_pass._quiet_days`) when it is still past the pass's line for
    its status (`PROPOSE_DORMANT_DAYS` for a live one, `AUTO_ARCHIVE_DAYS`
    for a dormant one), else None."""
    import lifecycle_pass as lp

    quiet = lp._quiet_days(t, last, now)
    line = lp.AUTO_ARCHIVE_DAYS if want == "dormant" else lp.PROPOSE_DORMANT_DAYS
    return quiet if quiet is not None and quiet > line else None


def lone_folders(workspace_root) -> Dict[str, Dict[str, Any]]:
    """`{folder name: {id, name}}`: the folders the pile offers
    (`lone_folders_counted`), as the Apply run's re-check reads them."""
    return lone_folders_counted(workspace_root)[0]


def lone_folders_counted(workspace_root) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """`({folder name: {id, name}}, n left out)`: the top folders integrity
    check C10 finds no project for, less one whose name a project already
    holds, one where projects are grouped, one holding a workspace, and one
    whose name names a person the owner keeps off limits or personal
    (`off_limits_screen`, counted and never named). Raises when the book or
    the wall cannot be read."""
    import integrity_check as ic
    from entities_io import entities_collection, read_entities_json

    root = Path(str(workspace_root))
    projects = _projects(root)
    held = {_fold(_name(t)) for t in projects}
    # A folder that holds a project's folder, or is named for a company, is
    # where projects are grouped, never a project itself.
    holds = {_fold(str(t.get("folder_name") or "").replace("\\", "/").split("/", 1)[0])
             for t in projects if "/" in str(t.get("folder_name") or "").replace("\\", "/").strip("/")}
    ent = read_entities_json(root / "_hq" / "data" / "entities.json")
    held |= org_names(entities_collection(ent, "orgs"))
    out: Dict[str, Dict[str, Any]] = {}
    for f in ic.scan_project_structure(root):
        if f.check != "C10.orphan_folder" or not f.subject:
            continue
        name = str(f.subject)
        if _fold(name) in held or _fold(name) in holds or not (root / name).is_dir() \
                or _holds_workspace(root / name):
            continue
        out[name] = {"id": name, "name": name[:160]}
    # review of #1623: a folder named for someone kept off limits is never offered
    return off_limits_screen(root, out)


def org_names(orgs: List[Any]) -> set:
    """Every name a company goes by, folded: its name, its legal name and
    each alias (review of #1623: a folder named for a company's other name
    is that company's folder too)."""
    out = set()
    for o in orgs or []:
        if not isinstance(o, dict):
            continue
        names = [o.get("canonical_name"), o.get("name"), o.get("display_name"), o.get("legal_name")]
        names += [a for a in (o.get("aliases") or []) if isinstance(a, str)]
        out |= {_fold(n) for n in names if isinstance(n, str) and n.strip()}
    return out


def files_under(workspace_root) -> str:
    """The company a folder made a project is filed under: the owner's own
    company when the book holds exactly one (`cockpit_helpers._own_org`, the
    rule a new work stream follows), else "" (no company)."""
    import cockpit_helpers as ch
    from entities_io import entities_collection, read_entities_json

    root = Path(str(workspace_root))
    oid = ch._own_org(root)
    ent = read_entities_json(root / "_hq" / "data" / "entities.json")
    for o in entities_collection(ent, "orgs"):
        if isinstance(o, dict) and str(o.get("id") or "") == oid:
            return str(o.get("canonical_name") or o.get("name") or "")[:120]
    return ""


def _holds_workspace(folder: Path, depth: int = 3) -> bool:
    """Whether a folder holds a workspace of its own (a system folder within
    `depth` levels): a copy of a workspace is never made a project."""
    stack = [(folder, 0)]
    while stack:
        d, k = stack.pop()
        try:
            subs = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return True  # cannot be read: never offered
        for e in subs:
            if e.name == "_hq":
                return True
            if k + 1 < depth:
                stack.append((Path(e.path), k + 1))
    return False


def _fold(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def loose_docs(workspace_root, folders: Optional[Dict[str, Any]] = None
               ) -> Optional[Tuple[Dict[str, Dict[str, Any]], int]]:
    """`({shelf id: {id, name, where}}, n left out)` for each document the stored shelf
    holds that no project's folder holds today and that is not under the
    system folder, less one whose name or folder names a person the owner
    keeps off limits or personal (`off_limits_screen`); None when there is
    no stored shelf (not known, so no pile). Opens no document. Raises when
    the wall cannot be read (fail closed: no pile)."""
    import doc_shelf

    root = Path(str(workspace_root))
    shelf = doc_shelf.load_stored(root)
    if shelf is None:
        return None
    homes = doc_shelf._thread_folders(root)
    lone = {_fold(k) for k in (folders or {})}
    out: Dict[str, Dict[str, Any]] = {}
    for r in shelf.get("rows") or []:
        rel = str((r or {}).get("path") or "").replace("\\", "/") if isinstance(r, dict) else ""
        if not rel or r.get("gone") or rel.lower().startswith("_hq/") or doc_shelf._thread_for(rel, homes):
            continue
        top = rel.split("/", 1)[0] if "/" in rel else ""
        if top and _fold(top) in lone:
            continue
        if not (root / rel).is_file():
            continue
        did = str(r.get("id") or doc_shelf.doc_id_for(rel))
        out[did] = {"id": did, "name": str(r.get("name") or rel.rsplit("/", 1)[-1])[:160],
                    "where": top[:120], "_folder": rel.rsplit("/", 1)[0] if "/" in rel else ""}
    kept, left = off_limits_screen(root, out)
    for d in kept.values():
        d.pop("_folder", None)
    return kept, left


def off_limits_screen(workspace_root, docs: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """`(kept, n left out)`: the documents or folders whose name (and, for
    a document, its folder) names no person the owner keeps off limits or
    personal, by the wall the imports
    use (`import_wall.screen_import`, cr1#1577: an id, an address or a name
    with a space in it, joined or not). The ones left out are counted, never
    named. Raises when the wall cannot be read, so the pile is offered
    nowhere (fail closed)."""
    import import_wall

    if not docs:
        return {}, 0
    rows = [{"id": d["id"], "name": d["name"], "folder": d.get("_folder") or d.get("where") or ""}
            for d in docs.values()]
    res = import_wall.screen_import(workspace_root, {"documents": rows})
    if not res.get("ok") or res.get("stop"):
        raise RuntimeError("the off-limits wall could not be read")
    keep = {str(r.get("id")) for r in (res.get("items") or {}).get("documents") or [] if isinstance(r, dict)}
    kept = {k: v for k, v in docs.items() if k in keep}
    return kept, len(docs) - len(kept)


def relink_counted(workspace_root) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """`({project id: {id, name, where}}, n left out)`: audit item B3, a
    project with no folder of its own and a top folder of the same name that
    no project points to (check C10). The press links the two, never makes
    a folder. A name two projects or two folders share is never guessed at;
    a folder holding a workspace of its own and a priority are left out, and
    so is one whose name names someone the owner keeps off limits or
    personal (counted, never named). Raises when the book or the wall cannot
    be read."""
    import integrity_check as ic

    root = Path(str(workspace_root))
    every = _projects(root)
    # review of #1629: a live project only (never archived or resolved), and
    # never a kind that has no folder of its own (a priority, a deal)
    projects = [t for t in every if not str(t.get("folder_name") or "").strip()
                and t.get("kind") not in ("objective", "deal")
                and str(t.get("status") or "").lower() not in ("archived", "resolved")]
    # a folder that holds another project's folder is where projects are grouped
    holds = {_fold(str(t.get("folder_name") or "").replace("\\", "/").split("/", 1)[0])
             for t in every if "/" in str(t.get("folder_name") or "").replace("\\", "/").strip("/")}
    folders: Dict[str, List[str]] = {}
    for f in ic.scan_project_structure(root):
        if f.check == "C10.orphan_folder" and f.subject and (root / str(f.subject)).is_dir():
            folders.setdefault(_fold(f.subject), []).append(str(f.subject))
    named: Dict[str, List[Dict[str, Any]]] = {}
    for t in projects:
        named.setdefault(_fold(_name(t)), []).append(t)
    out: Dict[str, Dict[str, Any]] = {}
    for key, names in folders.items():
        ts = named.get(key) or []
        if len(names) != 1 or len(ts) != 1 or key in holds or _holds_workspace(root / names[0]):
            continue
        tid = str(ts[0]["id"])
        out[tid] = {"id": tid, "name": _name(ts[0]), "where": names[0]}
    return off_limits_screen(root, out)


def two_notes_counted(workspace_root) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """`({folder: {id, name, where, n}}, n left out)`: each project folder
    holding more than one live session-notes file
    (`session_notes_file.two_notes_files`, cr1#1580), less one whose name
    names someone the owner keeps off limits or personal. The press merges
    them into the ruled name (`notes_merge`)."""
    import notes_merge
    import session_notes_file as snf

    root = Path(str(workspace_root))
    out: Dict[str, Dict[str, Any]] = {}
    for r in snf.two_notes_files(root):
        rel = str(r.get("folder") or "")
        if not rel:
            continue
        # review of #1629: the merge's own rule decides, so a folder whose
        # second file is a conflict copy, or whose part does not decode, is
        # never offered
        _ruled, parts, why = notes_merge.merge_parts(root, root / rel)
        if not why:
            out[rel] = {"id": rel, "name": rel.rsplit("/", 1)[-1][:160], "where": rel, "n": len(parts)}
    return off_limits_screen(root, out)


def loose_files_counted(workspace_root) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """`({path: {id, name, where}}, n left out)`: each file of the owner's
    at the root of a live project's folder (`root_files.loose_root_files`,
    integrity code C21), less one whose name or folder names someone the
    owner keeps off limits or personal. The press moves each into the
    project's `deliverables/`, archive first (`root_files`)."""
    import root_files

    root = Path(str(workspace_root))
    out = {r["id"]: {"id": r["id"], "name": r["name"], "where": r["under"]} for r in root_files.loose_root_files(root)}
    return off_limits_screen(root, out)


def clutter(workspace_root) -> Dict[str, Any]:
    """`{n, by_kind}`: Command Room's own leftovers the folder tidy would
    archive now (`cleanup_actions.plan_folder_tidy`, cr1#1019: released
    lock folders, old plugin copies, surplus backups). Read only. A count:
    it skips the walk of each old plugin copy's files (cr1#1714; the tidy
    itself checks each one whole before it moves it)."""
    import cleanup_actions as ca

    plan = ca.plan_folder_tidy(Path(str(workspace_root)), deep=False)
    return {"n": len(plan.get("moves") or []), "by_kind": {k: int(v) for k, v in (plan.get("by_kind") or {}).items() if v}}


def tidy_sets(workspace_root, now_iso: Optional[str] = None) -> Dict[str, Optional[set]]:
    """READ. `{pile: set of ids, or None when it cannot be read}` for the two
    piles a `tidy` row presses. The Apply run asks it before each such row,
    so an item that left its pile since the press is refused."""
    root = Path(str(workspace_root))
    now_iso = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
    out: Dict[str, Optional[set]] = {PILE_STALLED: None, PILE_FOLDER: None}
    try:
        out[PILE_STALLED] = set(stalled(root, now_iso))
    except Exception:  # noqa: BLE001 - fail closed: a pile that cannot be read is no pile
        out[PILE_STALLED] = None
    try:
        out[PILE_FOLDER] = set(lone_folders(root))
    except Exception:  # noqa: BLE001
        out[PILE_FOLDER] = None
    for kind, read in ((PILE_RELINK, relink_counted), (PILE_NOTES, two_notes_counted), (PILE_LOOSE, loose_files_counted)):
        try:
            out[kind] = set(read(root)[0])
        except Exception:  # noqa: BLE001
            out[kind] = None
    try:
        out[CLUTTER] = {CLUTTER} if clutter(root)["n"] else set()
    except Exception:  # noqa: BLE001
        out[CLUTTER] = None
    return out


def tidy_block(workspace_root, now_iso: Optional[str] = None,
               no_home_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """READ. The projects feed's optional `tidy` block: each pile's items
    (at most `ITEMS_CAP`, the count whole), `errors` naming a pile that
    could not be read, `days`, the lifecycle pass's two lines, and
    `files_under`, the company a folder made a project goes under. `no_home_rows` are the feed's own No home yet rows
    (None when they could not be read). An older page never reads it."""
    root = Path(str(workspace_root))
    now_iso = now_iso or _dt.datetime.now(_dt.timezone.utc).isoformat()
    piles: Dict[str, List[Any]] = {}
    n: Dict[str, int] = {}
    errors: List[str] = []

    def put(kind: str, items: List[Any]) -> None:
        piles[kind] = items[:ITEMS_CAP]
        n[kind] = len(items)

    try:
        rows = sorted(stalled(root, now_iso).values(), key=lambda r: (-(r["quiet_days"] or 0), r["name"].lower(), r["id"]))
        put(PILE_STALLED, rows)
    except Exception:  # noqa: BLE001 - said on the page, never shown as an empty pile
        errors.append(PILE_STALLED)
    folders: Optional[Dict[str, Any]] = None
    under = ""
    left_out: Dict[str, int] = {}
    try:
        folders, left = lone_folders_counted(root)
        put(PILE_FOLDER, sorted(folders.values(), key=lambda r: r["name"].lower()))
        if left:
            left_out[PILE_FOLDER] = int(left)
        under = files_under(root) if folders else ""
    except Exception:  # noqa: BLE001
        errors.append(PILE_FOLDER)
    if no_home_rows is None:
        errors.append(PILE_NO_HOME)
    else:
        put(PILE_NO_HOME, [str(r.get("id")) for r in no_home_rows
                           if isinstance(r, dict) and r.get("id") and r.get("from") == "open"])
    try:
        found = loose_docs(root, folders)
        if found is not None:
            docs, left = found
            put(PILE_DOCS, sorted(docs.values(), key=lambda r: (r["where"].lower(), r["name"].lower())))
            if left:
                left_out[PILE_DOCS] = int(left)
    except Exception:  # noqa: BLE001
        errors.append(PILE_DOCS)
    # Pull request 2: the relink, the two notes files, and the clutter line.
    for kind, read, order in ((PILE_RELINK, relink_counted, "name"), (PILE_NOTES, two_notes_counted, "where"),
                              (PILE_LOOSE, loose_files_counted, "id")):
        try:
            got, left = read(root)
            put(kind, sorted(got.values(), key=lambda r, o=order: r[o].lower()))
            if left:
                left_out[kind] = int(left)
        except Exception:  # noqa: BLE001
            errors.append(kind)
    mess: Optional[Dict[str, Any]] = None
    try:
        mess = clutter(root)
    except Exception:  # noqa: BLE001 - said on the page, never shown as nothing to tidy
        errors.append(CLUTTER)
    import lifecycle_pass as lp

    # The pass's own lines, so the page never writes a number of its own.
    return {"piles": piles, "n": n, "errors": errors,
            "days": {"active": int(lp.PROPOSE_DORMANT_DAYS), "dormant": int(lp.AUTO_ARCHIVE_DAYS)},
            # the company a folder made a project goes under ("" for none), said on its card
            "files_under": under,
            # how many a pile left out because they name someone off limits; counted, never named
            "left_out": left_out,
            # Command Room's own leftovers, one status line (None when unread)
            "clutter": mess}


def main(argv=None) -> int:
    import json

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: hub_tidy.py <workspace>")
        return 2
    print(json.dumps(tidy_block(args[0]), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
