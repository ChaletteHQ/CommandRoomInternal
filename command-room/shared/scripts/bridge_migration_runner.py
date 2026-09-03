#!/usr/bin/env python3
"""MIGRATE2 — the update-bridge's `apply_workspace_migration` action runner.

WHY THIS EXISTS
---------------
`references/RELEASE_MANIFEST.md` anticipated an `apply_workspace_migration`
action from the day the manifest layer shipped; until now the only actions
were `announce_only`, `instruct_user` and `auto_apply`, and the anchor
migration (MIGRATE1) was an operator-run CLI. This module graduates it to a
bridge-runnable, versioned action — so that when the operator decides a
client fleet gets memory, the delivery is ONE manifest item, not a hand-run
command per workspace.

It is built DORMANT: no shipped manifest under `shared/releases/` names the
action (pinned by tests/run_migrate2_test.py). Activation = a future manifest
item + the operator's go.

THE SAFETY POSTURE (the bridge's existing rules, made mechanical here)
----------------------------------------------------------------------
  * Never on a decoy root. A workspace path with `_archive` or
    `_demo-framework` as a component is refused outright (HQRESOLVE1 / G43 —
    the two decoys a real customer workspace was observed to carry), and a
    root that does not hold `_hq/data/entities.json` is not a workspace at all
    (`workspace_root.is_workspace_root`). Nothing is written, nothing is
    logged INTO the refused tree.
  * Refuse-all if canonical is ambiguous. When the bridge hands over the
    full `_hq` candidate list, `resolve_workspace_candidates` prunes the
    decoys and takes the shallowest — and REFUSES when two candidates tie at
    the shallowest depth, rather than picking one lexically.
  * Dry-run first, always. Apply only when the dry-run plans cleanly with
    ZERO blocking rows; otherwise the blocking rows are surfaced to the user
    as an instruct_user-style disclosure and the run stops having seeded
    nothing.
  * One question, once. When the migration reports it needs the customer's
    answer (the candor knob unset on this workspace), the runner returns
    `needs_answer` with the question spec; the bridge asks, calls
    `record_answer`, and re-invokes. A workspace that already carries the
    knob is never asked.
  * Receipts land in the workspace's own `_hq/data/migrate1/` — the SAME
    directory the CLI uses, so `--rollback` reads the same fsynced journal
    the bridge run wrote. The bridge adds `migrate1_receipt_bridge.json`
    beside MIGRATE1's dry-run/apply receipts, mirroring their shape.
  * Every outcome that touched or deliberately declined to touch the
    workspace is receipted in events.jsonl through the gated appender
    (plugin_update_remediation on applied / blocked; release_action_failed on
    an error), so a later bridge run and the audit trail agree.

CONTRACT (mirrors release_actions/__init__.py, extended)
--------------------------------------------------------
A migration function named by the manifest item's `migration_module` +
`migration_function` has the signature

    def fn(workspace_root, *, apply: bool, answers: dict | None = None) -> dict

and returns at least: status (planned | needs_answer | blocked | noop |
applied | error), ran, counts{planned, seeded, refused, blocking},
blocking_rows, question, next_line, undo_command, error. See
`release_actions.migrate_seed_anchors.run_bridge_migration` — the first and,
today, only implementation.

stdlib + shared/scripts only. Run: imported by the bridge's Phase 4.8 python
snippet; CLI form below for the operator.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePath

_HERE = Path(__file__).resolve()
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

ACTION = "apply_workspace_migration"
SOURCE_SKILL = "command-room-update-bridge"

#: Path components that mark a decoy substrate (HQRESOLVE1 / G43).
DECOY_NAMES = frozenset({"_archive", "_demo-framework"})

#: Where the bridge's own receipt lands, beside MIGRATE1's receipts + journal.
RECEIPT_SUBDIR = ("_hq", "data", "migrate1")
BRIDGE_RECEIPT_NAME = "migrate1_receipt_bridge.json"

#: Manifest item fields this action requires / accepts.
REQUIRED_ITEM_FIELDS = ("id", "migration_module", "migration_function",
                        "notice_template")
OPTIONAL_ITEM_FIELDS = ("blocked_template", "detector_module",
                        "detector_function", "prompt_template")

#: Plain-English fallbacks when a manifest item carries no template.
DEFAULT_NOTICE = ("I added five empty memory sections to the end of "
                  "{seeded} project file(s). {next_line}")
DEFAULT_BLOCKED = ("I held off on adding memory sections: {blocking} project "
                   "file(s) have a duplicate copy sitting next to them and I "
                   "cannot tell which one is current. Nothing was changed. "
                   "Once you have merged or removed the duplicates, say "
                   "update command room and I will pick this back up.")
UNDO_LINE = ("If you want this reversed, tell me and I will restore every "
             "file from its backup — the restore refuses any file you have "
             "edited since, so nothing of yours gets overwritten.")


# --------------------------------------------------------------------------
# Workspace safety
# --------------------------------------------------------------------------

def _depth(p: str) -> int:
    return len([x for x in PurePath(p.replace("\\", "/")).parts if x])


def is_decoy_path(path) -> bool:
    """True when any component of `path` is a decoy marker name."""
    parts = PurePath(str(path).replace("\\", "/")).parts
    return any(part in DECOY_NAMES for part in parts)


def workspace_safety(workspace_root) -> dict:
    """{"ok": bool, "reason": str|None} — the single-root checks."""
    if workspace_root is None or not str(workspace_root).strip():
        return {"ok": False, "reason": "workspace root unresolved (empty)"}
    root = Path(workspace_root)
    if is_decoy_path(root):
        return {"ok": False,
                "reason": "workspace path passes through a decoy folder "
                          "(_archive / _demo-framework) — refusing to run a "
                          "migration against an archived or demo substrate"}
    try:
        from workspace_root import is_workspace_root
        anchored = is_workspace_root(root)
    except Exception:  # noqa: BLE001 — fall back to the literal anchor
        anchored = (root / "_hq" / "data" / "entities.json").is_file()
    if not anchored:
        return {"ok": False,
                "reason": "not a workspace root (no _hq/data/entities.json "
                          "beneath it) — refusing to guess"}
    return {"ok": True, "reason": None}


def resolve_workspace_candidates(candidates) -> dict:
    """Pick the canonical workspace from a list of `_hq` directory paths (the
    bridge's `find` output, one per line or a list). Mirrors the G43 pipeline
    — prune decoys, shallowest wins — and adds the refuse-all rule: a tie at
    the shallowest depth is AMBIGUOUS and resolves to nothing.

    Returns {"root": str|None, "refused": bool, "reason": str|None,
             "candidates_considered": [..]}.
    """
    if isinstance(candidates, str):
        candidates = [c for c in candidates.splitlines()]
    cands = []
    for c in candidates or []:
        c = str(c).strip()
        if not c:
            continue
        norm = c.replace("\\", "/").rstrip("/")
        if PurePath(norm).name == "_hq":
            norm = norm[:-len("/_hq")] if norm.endswith("/_hq") else norm
        if is_decoy_path(norm):
            continue
        cands.append(norm)
    if not cands:
        return {"root": None, "refused": True,
                "reason": "no workspace candidate survived the decoy prune",
                "candidates_considered": []}
    cands = sorted(set(cands), key=lambda p: (_depth(p), p))
    top_depth = _depth(cands[0])
    tied = [c for c in cands if _depth(c) == top_depth]
    if len(tied) > 1:
        return {"root": None, "refused": True,
                "reason": "ambiguous — more than one workspace at the same "
                          "depth; refusing to pick one: "
                          + ", ".join(tied),
                "candidates_considered": cands}
    return {"root": cands[0], "refused": False, "reason": None,
            "candidates_considered": cands}


# --------------------------------------------------------------------------
# Item validation + import
# --------------------------------------------------------------------------

def validate_item(item: dict) -> list[str]:
    problems = []
    if not isinstance(item, dict):
        return ["manifest item is not an object"]
    if item.get("action") != ACTION:
        problems.append(f"action is {item.get('action')!r}, expected {ACTION!r}")
    for f in REQUIRED_ITEM_FIELDS:
        if not isinstance(item.get(f), str) or not item.get(f).strip():
            problems.append(f"missing required string field {f!r}")
    mod = item.get("migration_module", "")
    if isinstance(mod, str) and mod and not mod.startswith("release_actions."):
        problems.append("migration_module must live under release_actions.")
    return problems


def _load_migration(item: dict):
    mod = importlib.import_module(item["migration_module"])
    fn = getattr(mod, item["migration_function"], None)
    if fn is None or not callable(fn):
        raise AttributeError(
            f"{item['migration_module']} has no callable "
            f"{item['migration_function']!r}")
    return fn


# --------------------------------------------------------------------------
# Receipts + events
# --------------------------------------------------------------------------

def _receipt_dir(root: Path) -> Path:
    return root.joinpath(*RECEIPT_SUBDIR)


def _write_bridge_receipt(root: Path, receipt: dict) -> str:
    from atomic_write import atomic_write_json
    rdir = _receipt_dir(root)
    os.makedirs(rdir, exist_ok=True)
    path = rdir / BRIDGE_RECEIPT_NAME
    atomic_write_json(path, receipt)
    return str(path)


def _log_event(root: Path, event: dict) -> bool:
    """Gated append; a logging failure is reported, never fatal."""
    try:
        from event_gate import append_event
        append_event(root / "_hq" / "data" / "events.jsonl", event,
                     holder=SOURCE_SKILL)
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[migrate2] event append failed: {exc}\n")
        return False


def _fmt(template: str, ctx: dict) -> str:
    class _Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    try:
        return template.format_map(_Safe(ctx))
    except Exception:  # noqa: BLE001
        return template


def record_answer(workspace_root, question: dict, answer: str) -> dict:
    """Parse + persist the customer's reply to a `needs_answer` question via
    the migration module's own parser (so spellings live in one place). Does
    NOT seed — the bridge re-invokes `run_apply_workspace_migration` with
    `answers={key: value}` after this returns ok.

    Returns {"ok": bool, "key": str, "value": str|None, "reason": str|None}.
    """
    key = (question or {}).get("key")
    from release_actions import migrate_seed_anchors as msa
    value = msa.parse_candor_answer(answer) if key == msa.CANDOR_KEY else None
    if value is None:
        return {"ok": False, "key": key, "value": None,
                "reason": f"could not read {answer!r} as an answer — ask again "
                          "with the two options spelled out"}
    return {"ok": True, "key": key, "value": value, "reason": None}


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------

def run_apply_workspace_migration(item: dict, workspace_root,
                                  answers: dict | None = None,
                                  manifest_version: str | None = None,
                                  candidates=None) -> dict:
    """Run one `apply_workspace_migration` manifest item against a workspace
    under the bridge's safety posture. Never raises.

    Args:
        item: the manifest item (action, id, migration_module,
              migration_function, notice_template, blocked_template?).
        workspace_root: the resolved workspace root (parent of `_hq/`). When
              `candidates` is given it is resolved from them instead and
              `workspace_root` is ignored.
        answers: {question_key: value} — the customer's reply to a prior
              `needs_answer` result, already passed through `record_answer`.
        manifest_version: stamped into the events + receipt.
        candidates: optional list/newline-string of `_hq` paths from the
              bridge's discovery; enables the refuse-all-on-ambiguity rule.

    Returns a dict with (always present): action, item_id, status
    (refused | needs_answer | blocked | noop | applied | failed), success,
    ran, surface (the plain-English line to show, or None), question,
    counts, blocking_rows, seeded_rels, undo_command, undo_line, next_line,
    receipt_path, workspace_root, error.
    """
    out = {
        "action": ACTION,
        "item_id": (item or {}).get("id") if isinstance(item, dict) else None,
        "manifest_version": manifest_version,
        "status": "refused", "success": False, "ran": False,
        "surface": None, "question": None,
        "counts": {"planned": 0, "seeded": 0, "refused": 0, "blocking": 0},
        "blocking_rows": [], "seeded_rels": [],
        "undo_command": None, "undo_line": None, "next_line": None,
        "receipt_path": None, "workspace_root": None, "error": None,
    }

    # -- 1. the workspace: decoy / anchor / ambiguity ------------------------
    if candidates is not None:
        res = resolve_workspace_candidates(candidates)
        if res["refused"]:
            out["error"] = res["reason"]
            return out
        workspace_root = res["root"]
    safety = workspace_safety(workspace_root)
    if not safety["ok"]:
        out["error"] = safety["reason"]
        return out
    root = Path(workspace_root)
    out["workspace_root"] = str(root)

    # -- 2. the item + module -------------------------------------------------
    problems = validate_item(item)
    if problems:
        out["status"] = "failed"
        out["error"] = "invalid manifest item: " + "; ".join(problems)
        _log_event(root, {"type": "release_action_failed",
                          "source_skill": SOURCE_SKILL,
                          "data": {"item_id": out["item_id"],
                                   "action": ACTION,
                                   "manifest_version": manifest_version,
                                   "error": out["error"]}})
        return out
    try:
        fn = _load_migration(item)
    except Exception as exc:  # noqa: BLE001
        out["status"] = "failed"
        out["error"] = f"migration import failed: {type(exc).__name__}: {exc}"
        _log_event(root, {"type": "release_action_import_failed",
                          "source_skill": SOURCE_SKILL,
                          "data": {"module": item["migration_module"],
                                   "item_id": out["item_id"],
                                   "error": out["error"]}})
        return out

    run_id = uuid.uuid4().hex[:12]
    ctx_base = {"item_id": out["item_id"], "manifest_version": manifest_version}

    # -- 3. dry-run first, always ---------------------------------------------
    try:
        dry = fn(root, apply=False, answers=answers)
    except Exception as exc:  # noqa: BLE001
        dry = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if dry.get("status") == "error":
        return _fail(out, root, dry.get("error") or "dry-run failed",
                     manifest_version)

    out["counts"].update({k: dry.get("counts", {}).get(k, 0)
                          for k in out["counts"]})
    out["blocking_rows"] = list(dry.get("blocking_rows") or [])
    out["undo_command"] = dry.get("undo_command")
    out["next_line"] = dry.get("next_line") or None

    if dry.get("status") == "blocked" or out["counts"]["blocking"]:
        # Disclosure, not a seed: the rows go to the user, the run stops.
        out["status"] = "blocked"
        out["success"] = True
        rows = "\n".join(f"  - {r['rel']} — {r['issue']}"
                         for r in out["blocking_rows"])
        ctx = {**ctx_base, **out["counts"], "blocking_rows": rows}
        out["surface"] = (_fmt(item.get("blocked_template") or DEFAULT_BLOCKED,
                               ctx) + ("\n" + rows if rows else ""))
        out["receipt_path"] = _write_bridge_receipt(
            root, _receipt(out, run_id, dry, None))
        _log_event(root, {"type": "plugin_update_remediation",
                          "source_skill": SOURCE_SKILL,
                          "data": {**ctx_base, "action": ACTION,
                                   "outcome": "blocked",
                                   "counts": out["counts"],
                                   "blocking_rows": out["blocking_rows"],
                                   "receipt_path": _relative(
                                       out["receipt_path"], root)}})
        return out

    if dry.get("status") == "noop" or out["counts"]["planned"] == 0:
        # Already applied (or nothing to do). No surface, no mark-applied —
        # the auto_apply idempotency shape.
        out["status"] = "noop"
        out["success"] = True
        return out

    # -- 4. the apply (the migration itself enforces its question gate) -----
    try:
        rec = fn(root, apply=True, answers=answers)
    except Exception as exc:  # noqa: BLE001
        rec = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if rec.get("status") == "error":
        return _fail(out, root, rec.get("error") or "apply failed",
                     manifest_version)

    if rec.get("status") == "needs_answer":
        out["status"] = "needs_answer"
        out["success"] = True
        out["question"] = rec.get("question")
        out["error"] = rec.get("error")
        out["surface"] = (rec.get("question") or {}).get("prompt")
        return out

    out["counts"].update({k: rec.get("counts", {}).get(k, out["counts"][k])
                          for k in out["counts"]})
    out["seeded_rels"] = list(rec.get("seeded_rels") or [])
    out["undo_command"] = rec.get("undo_command") or out["undo_command"]
    out["next_line"] = rec.get("next_line") or out["next_line"]
    out["undo_line"] = UNDO_LINE
    out["status"] = "applied"
    out["success"] = True
    out["ran"] = bool(rec.get("ran"))
    ctx = {**ctx_base, **out["counts"], "next_line": out["next_line"] or "",
           "undo_line": UNDO_LINE}
    notice = _fmt(item.get("notice_template") or DEFAULT_NOTICE, ctx)
    refused = out["counts"].get("refused", 0)
    if refused:
        notice += (f" {refused} file(s) changed while I was working and were "
                   "left exactly as they were — the next update picks them "
                   "up.")
    out["surface"] = notice + " " + UNDO_LINE
    out["receipt_path"] = _write_bridge_receipt(
        root, _receipt(out, run_id, dry, rec))
    # Persisted pointers are workspace-relative (BRIEFMERGE / WALKFIX1 — a
    # machine-absolute path in events.jsonl is valid only on the machine and
    # in the session that wrote it). The undo command is machine-specific by
    # nature, so it lives in the receipt FILE, never in the ledger; it is
    # re-derivable from the workspace root at any time.
    _log_event(root, {"type": "plugin_update_remediation",
                      "source_skill": SOURCE_SKILL,
                      "data": {**ctx_base, "action": ACTION,
                               "outcome": "applied",
                               "counts": out["counts"],
                               "seeded_rels": out["seeded_rels"],
                               "candor": rec.get("candor"),
                               "undo": "the migration's --rollback on this "
                                       "workspace (see the receipt)",
                               "receipt_path": _relative(
                                   out["receipt_path"], root)}})
    return out


def _relative(path, root: Path) -> str:
    """Workspace-relative form of a receipt pointer for the ledger."""
    try:
        from workspace_paths import to_workspace_relative
        rel = to_workspace_relative(path, workspace_root=root)
    except Exception:  # noqa: BLE001
        rel = ""
    if not rel:
        try:
            rel = Path(path).resolve().relative_to(
                Path(root).resolve()).as_posix()
        except Exception:  # noqa: BLE001
            rel = "/".join(RECEIPT_SUBDIR) + "/" + BRIDGE_RECEIPT_NAME
    return rel


def _fail(out: dict, root: Path, error: str, manifest_version) -> dict:
    out["status"] = "failed"
    out["success"] = False
    out["error"] = error
    _log_event(root, {"type": "release_action_failed",
                      "source_skill": SOURCE_SKILL,
                      "data": {"item_id": out["item_id"], "action": ACTION,
                               "manifest_version": manifest_version,
                               "error": error}})
    return out


def _receipt(out: dict, run_id: str, dry: dict, rec: dict | None) -> dict:
    """The bridge receipt — mirrors MIGRATE1's receipt shape (generated_at /
    mode / run_id / root / counts / the untouched-style row lists) and adds
    the bridge's own verdict + the undo path."""
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "bridge",
        "run_id": run_id,
        "root": out["workspace_root"],
        "action": ACTION,
        "item_id": out["item_id"],
        "manifest_version": out["manifest_version"],
        "status": out["status"],
        "ran": out["ran"],
        "counts": dict(out["counts"]),
        "seeded": list(out["seeded_rels"]),
        "blocking_review": list(out["blocking_rows"]),
        "refused": list((rec or {}).get("refused_rows") or []),
        "candor": (rec or dry or {}).get("candor"),
        "undo_command": out["undo_command"],
        "migration_receipts": {
            "dry_run": dry.get("receipt_path"),
            "apply": (rec or {}).get("receipt_path"),
            "journal": dry.get("journal_path"),
        },
    }


# --------------------------------------------------------------------------
# CLI (operator use; the bridge imports the function directly)
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("item_json", help="manifest item as a JSON string or "
                                      "@path to a JSON file")
    ap.add_argument("workspace_root")
    ap.add_argument("--answers", default=None,
                    help='JSON object, e.g. {"brain_candor": "full"}')
    ap.add_argument("--manifest-version", default=None)
    args = ap.parse_args(argv)
    raw = args.item_json
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    item = json.loads(raw)
    answers = json.loads(args.answers) if args.answers else None
    result = run_apply_workspace_migration(
        item, args.workspace_root, answers=answers,
        manifest_version=args.manifest_version)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if result["status"] in ("applied", "noop") else 1


if __name__ == "__main__":
    sys.exit(main())
