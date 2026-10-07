#!/usr/bin/env python3
"""WALKFIX1 Item I — the update bridge's ONE version vocabulary.

THE FINDING (2026-08-10). One bridge run, one install, three different version
stories:

  * receipt A: `from_version 5.11.0, to_version 5.10.0` — reads as a DOWNGRADE
    record to any later auditor, while its own note honestly said the installed
    copy carried unshipped code;
  * receipt B, second pass of the SAME run: `from_version 5.11.0, to_version
    5.11.0` — same install, different `to_version`, while its own text said
    "installed plugin.json reads 5.10.0";
  * the chat line: "the plugin still reads v5.11.0" — plugin.json read 5.10.0.
    The conclusion (nothing to apply) was right; the stated fact was false.

MECHANISM, and it is not a bug in any one line. Version-at-ship means an
installed unshipped tip legitimately carries plugin.json at the LAST SHIPPED
version, a CHANGELOG Unreleased block, and a newest release manifest one
version ahead. That state is documented and deliberate. What the bridge lacked
was a VOCABULARY for it, so different code paths picked different members of
the triple {plugin.json version, newest manifest version, workspace stamp} for
`to_version` and for the sentence, and the receipts disagreed with each other
and with the file.

THE RULE THIS MODULE ENFORCES

  1. The triple is resolved ONCE PER RUN, not once per pass. `resolve_once`
     memoizes into a caller-held state dict; every later pass in the same run
     gets the identical struct even if the tree changes underneath it.
  2. `to_version` := `newest_manifest_version` — the honest answer to "what is
     this tree", picked and written down here rather than re-decided per site.
  3. EVERY `plugin_update` receipt carries all three under their own names, so
     a reader never has to infer which member a number came from.
  4. The chat sentence renders from the same struct, with explicit
     unshipped-tip vocabulary.

THE DOOR SPLIT (HYGIENE3, 2026-09-23; coordinator decision D-3). On the
merged app the plugin lives in the container and the workspace lives on the
customer's computer; neither side can open the other's files. The walk's
update asked the read door for `resolve_install_versions` and was refused -
it opens `plugin.json` and `shared/releases/` beside the workspace, a
plugin-side path the fence refuses, and the staged runtime ships no release
manifests at all - so the chat imported this module in a shell instead. The
triple is now resolved in two halves: `plugin_facts` (the CONTAINER half,
run from the plugin root: `python3 shared/scripts/bridge_versions.py
plugin-facts`) reads the two plugin-side members, and `install_versions`
(the WORKSPACE half, on the read door as `bridge_versions:install_versions`)
takes them as arguments and reads only the workspace stamp - every year
shard included, through `events_io`. `resolve_install_versions` is the two
halves in one process, for a seat where both sides are local.

Stdlib only. Pure except the file reads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from release_remediation_selector import parse_version  # noqa: E402

# The three named members. Spelled once so the receipt writer, the chat line
# and the guard read one list.
VERSION_FIELDS = ("plugin_json_version", "newest_manifest_version",
                  "workspace_stamp")

# The state key `resolve_once` memoizes under.
RUN_STATE_KEY = "cr_bridge_versions"

UNKNOWN = "unknown"


def _read_plugin_version(plugin_root) -> Optional[str]:
    try:
        data = json.loads(
            (Path(plugin_root) / ".claude-plugin" / "plugin.json").read_text(
                encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable manifest is `unknown`
        return None
    v = data.get("version")
    return str(v).strip() if isinstance(v, str) and v.strip() else None


def _newest_manifest(plugin_root) -> Optional[str]:
    """The highest release-manifest version in the tree, numerically.

    Numerically, never lexically, and never "the last filename": `v5.9.4` sorts
    after `v5.11.0` as a string, which is the same class of defect the
    manifest selector's own docstring exists about.
    """
    best = None
    releases = Path(plugin_root) / "shared" / "releases"
    try:
        names = list(releases.glob("v*.json"))
    except Exception:  # noqa: BLE001
        return None
    for path in names:
        raw = path.stem[1:]
        try:
            key = parse_version(raw)
        except ValueError:
            continue          # a stray filename cannot masquerade as a version
        if best is None or key > best[0]:
            best = (key, raw)
    return best[1] if best else None


def _workspace_stamp(workspace_root) -> Optional[str]:
    """The version this workspace was last brought current AT — the newest
    `plugin_update` receipt's `to_version`, which is what `from_version` means
    on the next run. Read across EVERY year shard and the active file through
    `events_io` (HYGIENE3): a workspace whose last update row rotated into a
    year shard is still a workspace that was brought current."""
    try:
        import events_io  # noqa: WPS433 — deferred: the container half never needs it
        rows = events_io.iter_events(workspace_root)
        stamp = None
        for ev in rows:
            if ev.get("type") != "plugin_update":
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            v = data.get("to_version") or ev.get("to_version")
            if isinstance(v, str) and v.strip():
                stamp = v.strip()      # append-only: the last one wins
        return stamp
    except Exception:  # noqa: BLE001 — a fresh install has no ledger yet
        return None


def _clean(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip() and value.strip() != UNKNOWN:
        return value.strip()
    return None


def plugin_facts(plugin_root) -> Dict[str, str]:
    """The CONTAINER half of the triple: the two members that live beside the
    plugin, never beside the workspace. Run where the plugin is."""
    return {"plugin_json_version": _read_plugin_version(plugin_root) or UNKNOWN,
            "newest_manifest_version": _newest_manifest(plugin_root) or UNKNOWN}


def install_versions(workspace_root, *, plugin_json_version: Optional[str] = None,
                     newest_manifest_version: Optional[str] = None) -> Dict[str, Any]:
    """The WORKSPACE half of the triple, on the read door
    (`bridge_versions:install_versions`): the plugin-side members arrive as
    ARGUMENTS (from `plugin_facts`, run in the container) and the only thing
    read here is the workspace stamp. Answers the same struct
    `resolve_install_versions` always has."""
    pj = _clean(plugin_json_version)
    nm = _clean(newest_manifest_version)
    ws = _workspace_stamp(workspace_root)
    out: Dict[str, Any] = {
        "plugin_json_version": pj or UNKNOWN,
        "newest_manifest_version": nm or UNKNOWN,
        "workspace_stamp": ws or UNKNOWN,
    }
    # `to_version` is the newest manifest: it is what this TREE is, which is
    # the question a later auditor is actually asking of an install record.
    out["to_version"] = out["newest_manifest_version"]
    out["from_version"] = out["workspace_stamp"]
    out["unshipped_tip"] = _is_ahead(nm, pj)
    return out


def resolve_install_versions(plugin_root, workspace_root) -> Dict[str, Any]:
    """Read the triple from disk, both halves in one process (a seat whose
    plugin and workspace are on one filesystem). Prefer `resolve_once` — this
    is the READ, and calling it per pass is exactly the defect."""
    facts = plugin_facts(plugin_root)
    return install_versions(workspace_root, **facts)


def _is_ahead(newer: Optional[str], older: Optional[str]) -> bool:
    if not newer or not older:
        return False
    try:
        return parse_version(newer) > parse_version(older)
    except ValueError:
        return False


def resolve_once(state: Dict[str, Any], plugin_root,
                 workspace_root) -> Dict[str, Any]:
    """The triple for THIS RUN, resolved on first call and reused thereafter.

    `state` is any dict the bridge carries across its passes. Two passes of one
    run must produce byte-identical version fields on both receipts and in the
    chat line; the only way to guarantee that is to stop asking the disk. This
    is the whole fix — the members were always readable, they were just read
    again at each site and the tree moved between them.
    """
    cached = state.get(RUN_STATE_KEY)
    if isinstance(cached, dict):
        return cached
    resolved = resolve_install_versions(plugin_root, workspace_root)
    state[RUN_STATE_KEY] = resolved
    return resolved


def receipt_version_fields(triple: Dict[str, Any]) -> Dict[str, Any]:
    """The version block EVERY `plugin_update` receipt carries.

    All three members under their own names, plus the `from_version` /
    `to_version` the existing readers key on — so nobody has to infer which
    member a bare number came from, which is what made the two receipts read
    as a downgrade and then as a no-op.
    """
    out = {field: triple.get(field, UNKNOWN) for field in VERSION_FIELDS}
    out["from_version"] = triple.get("from_version", UNKNOWN)
    out["to_version"] = triple.get("to_version", UNKNOWN)
    out["unshipped_tip"] = bool(triple.get("unshipped_tip"))
    return out


def version_sentence(triple: Dict[str, Any]) -> str:
    """The chat line, rendered from the same struct the receipts carry.

    The unshipped-tip case gets its own vocabulary instead of picking one
    member and stating it as the whole truth: the tree really is ahead of the
    stamp on the file, and saying both is shorter than being wrong about one.
    """
    pj = triple.get("plugin_json_version", UNKNOWN)
    nm = triple.get("newest_manifest_version", UNKNOWN)
    stamp = triple.get("workspace_stamp", UNKNOWN)
    if triple.get("unshipped_tip"):
        return (f"Your workspace was last brought current at v{stamp}. This "
                f"install is running the tree at v{nm}-unreleased — "
                f"plugin.json still stamps v{pj}, because the version stamp "
                f"is written at ship. Nothing new to apply.")
    if stamp == UNKNOWN:
        return (f"This install is at v{nm}. I have no record of this "
                f"workspace being brought current yet.")
    if stamp == nm:
        return (f"Your workspace was last brought current at v{stamp}, and "
                f"this install is at v{nm}. Nothing new to apply.")
    return (f"Your workspace was last brought current at v{stamp}; this "
            f"install is at v{nm}.")


__all__ = [
    "VERSION_FIELDS", "RUN_STATE_KEY", "UNKNOWN",
    "install_versions", "plugin_facts",
    "resolve_install_versions", "resolve_once", "receipt_version_fields",
    "version_sentence",
]


def main(argv=None) -> int:
    """`plugin-facts [<plugin root>]` - the container half, one JSON line.
    The plugin root defaults to the one this file lives in."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "plugin-facts":
        sys.stderr.write("usage: bridge_versions.py plugin-facts [<plugin root>]\n")
        return 2
    root = Path(argv[1]) if len(argv) > 1 else _HERE.parent.parent
    sys.stdout.write(json.dumps(plugin_facts(root), sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
