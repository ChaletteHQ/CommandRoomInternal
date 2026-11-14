#!/usr/bin/env python3
"""The CRAFT view: `_hq/views/CRAFT.md`, every craft file in the workspace by entity, scope, status, confidence and last test (SPEC CRAFT1 step 1, cr1#396).

WHY THIS EXISTS
---------------
A craft file lives in a `craft/` folder under the entity it describes, so
there is no single place that says which method files exist, how far each is
trusted and when each was last tested. This view is that place. It also flags
a craft file sitting in an entity folder that no record points at (a folder
no project or person owns, a subject that is not on file, or a subject whose
record points at a different folder), a file that does not read as a craft
file at all, and a file whose method text carries a figure, a third-party
name or an email (named by kind, never quoted). It never reads a file
through a symlink.

GENERATED, NEVER HAND-EDITED
----------------------------
The whole file is rewritten from the craft folders and entities.json. It
carries the AUTO-GENERATED banner the other `_hq/views` pages carry, and its
body sits in a `LIVE-STATE:craft` generated block (the marker grammar of
`render_brain_block`), so a reader and `render_brain_block.read_block_meta`
both see when it was built. Change detection ignores the timestamp, so a
quiet workspace is a true no-op write.

OWNER-FACING. Names come from the records, never an id; the shape of every
file is read through `craft_contract`, the one contract.

PUBLIC API
  view_rows(ws) -> list[dict]          one row per craft file whose header reads
  view_flags(ws) -> list[dict]         {"path", "reason"} for files needing a look
  _build_content(ws) -> (markdown, counts)
  regenerate(ws) -> dict               atomic-write the view
  regenerate_if_changed(ws) -> dict    changed-only (cleanup's weekly entry)

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from atomic_write import atomic_write_text  # noqa: E402
import craft_contract as cc  # noqa: E402

VIEW_REL = Path("_hq") / "views" / "CRAFT.md"
BLOCK_ID = "craft"

_VOLATILE_RE = re.compile(r"generated_at=\S+")


def _view_path(workspace_root) -> Path:
    return Path(workspace_root) / VIEW_REL


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _fmt_conf(value) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    return ("%.2f" % value).rstrip("0").rstrip(".") or "0"


def _cell(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().replace("|", "\\|")


def _scan(workspace_root) -> tuple[list, list]:
    """(rows, flags) over every craft file. Never raises on a bad file."""
    root = Path(workspace_root)
    ents = cc.load_entities(root)
    owner = cc.owner_person_id(ents)
    own_orgs = cc.own_company_org_ids(ents)
    rows: list = []
    flags: list = []
    for path in cc.find_craft_files(root):
        rel = _rel(root, path)
        if not cc.is_safe_path(root, path):
            continue  # never read through a link (review finding 3)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            flags.append({"path": rel, "reason": "could not be read"})
            continue
        res = cc.validate_craft(text, workspace_root=root, entities=ents,
                                path=path)
        header = res["header"]
        if not header.get("subject") or not header.get("scope"):
            flags.append({"path": rel,
                          "reason": "does not read as a craft file"})
            continue
        subject = header["subject"]
        rank = 0 if subject == owner else 1 if subject in own_orgs else 2
        rows.append({
            "path": rel,
            "entity": cc.entity_label(ents, subject),
            "scope": cc.scope_label(header["scope"], root),
            "status": header.get("status") or "unknown",
            "confidence": _fmt_conf(header.get("confidence")),
            "tested": header.get("tested") or "never",
            "_rank": rank,
        })
        place = cc.placement(root, path, header, entities=ents)
        if not place["placed"]:
            flags.append({"path": rel, "reason": place["reason"]})
        elif res["errors"]:
            flags.append({"path": rel,
                          "reason": "does not pass the craft shape: "
                                    + res["errors"][0]})
        if res["privacy"]:
            # By kind only: the offending words never reach the view.
            kinds = sorted({f["kind"] for f in res["privacy"]})
            flags.append({"path": rel,
                          "reason": "its method text carries "
                                    + ", ".join(f"a {k}" for k in kinds)
                                    + ", which a craft file may not hold"})
    rows.sort(key=lambda r: (r["_rank"], r["entity"].lower(), r["scope"],
                             r["path"]))
    for r in rows:
        r.pop("_rank", None)
    flags.sort(key=lambda f: f["path"])
    return rows, flags


def view_rows(workspace_root) -> list:
    return _scan(workspace_root)[0]


def view_flags(workspace_root) -> list:
    return _scan(workspace_root)[1]


def _build_content(workspace_root) -> tuple[str, dict]:
    rows, flags = _scan(workspace_root)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body: list = [
        "# How people work",
        "",
        "Every craft file in this workspace: how you, your firm, a person or "
        "a project does a kind of work, how far it is trusted, and when it "
        "was last tested against real work. A craft file lives in a craft "
        "folder under the person or project it describes.",
        "",
    ]
    if not rows and not flags:
        body += ["No craft files yet.", ""]
    else:
        n = len(rows) + sum(1 for f in flags
                            if f["path"] not in {r["path"] for r in rows})
        body += [f"**{n}** craft file{'s' if n != 1 else ''}; "
                 f"**{len(flags)}** need{'s' if len(flags) == 1 else ''} "
                 "a look.", ""]
    if rows:
        body += ["## By who it describes", "",
                 "| Who | Kind of work | Status | Confidence | Last tested "
                 "| File |",
                 "|---|---|---|---|---|---|"]
        for r in rows:
            body.append(f"| {_cell(r['entity'])} | {_cell(r['scope'])} "
                        f"| {_cell(r['status'])} | {_cell(r['confidence'])} "
                        f"| {_cell(r['tested'])} | {_cell(r['path'])} |")
        body.append("")
    if flags:
        body += ["## Needs a look", ""]
        for f in flags:
            body.append(f"- {_cell(f['path'])}: {_cell(f['reason'])}.")
        body.append("")
    out = [
        "<!-- AUTO-GENERATED by shared/scripts/render_craft_view.py: do not "
        "hand-edit; regenerate from the craft folders. -->",
        "<!-- source: craft folders + _hq/data/entities.json -->",
        "",
        f"<!-- LIVE-STATE:{BLOCK_ID} generated_at={stamp} -->",
        "\n".join(body).rstrip(),
        f"<!-- /LIVE-STATE:{BLOCK_ID} -->",
        "",
    ]
    content = "\n".join(out)
    files = len(rows) + sum(1 for f in flags
                            if f["path"] not in {r["path"] for r in rows})
    return content, {"files": files, "listed": len(rows),
                     "flagged": len(flags)}


def _strip_volatile(text: str) -> str:
    return _VOLATILE_RE.sub("", text)


def regenerate(workspace_root) -> dict:
    """Build and atomic-write `_hq/views/CRAFT.md`. Idempotent."""
    content, counts = _build_content(workspace_root)
    view = _view_path(workspace_root)
    view.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(view, content)
    counts["changed"] = True
    counts["view_path"] = str(view)
    return counts


def regenerate_if_changed(workspace_root) -> dict:
    """Changed-only regeneration, cleanup's weekly entry point."""
    content, counts = _build_content(workspace_root)
    view = _view_path(workspace_root)
    old = view.read_text(encoding="utf-8") if view.exists() else ""
    changed = _strip_volatile(old) != _strip_volatile(content)
    if changed:
        view.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(view, content)
    counts["changed"] = changed
    counts["view_path"] = str(view)
    return counts


def main(argv: list) -> int:
    if len(argv) < 2:
        print("Usage: python3 render_craft_view.py <workspace_root>",
              file=sys.stderr)
        return 2
    ws = Path(argv[1])
    if not ws.exists():
        print(f"ABORT: workspace not found: {ws}", file=sys.stderr)
        return 2
    r = regenerate(ws)
    print(f"OK: regenerated CRAFT.md ({r['files']} craft files, "
          f"{r['flagged']} need a look) -> {r['view_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
