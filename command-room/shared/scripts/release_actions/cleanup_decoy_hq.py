#!/usr/bin/env python3
"""DECOYCLEAN1 — quarantine decoy `_hq` directories on a live workspace,
rename-not-delete, dry-run by default.

The one-shot PR #90 (HQRESOLVE1, shipped v5.21.0) left open at promote time.
HQRESOLVE1 hardened the workspace-discovery snippet (prune `_archive/` +
`_demo-framework/`, shallowest match, guard G43) so SHIPPED code no longer
binds a decoy `_hq` — but the decoys themselves still sit on live workspaces,
and any un-swept or legacy code path that globs for `_hq` could still find
them. The two shapes the attended walk observed on a real workspace:

  (a) `_archive/stale-locks/_hq/...` — manufactured by CR's OWN cleanup, which
      archived stale locks with the `_hq/.system`-style path shape preserved
      (fleet-wide exposure; NEW archives mirror to `hq-system-locks` since
      HQRESOLVE1, but the already-minted decoys remain);
  (b) `_demo-framework/_hq/...` — a full fake substrate incl. entities.json,
      the silent-wrong-data shape.

What this tool does, and refuses to do:

  RESOLUTION  The canonical `_hq` is resolved with the exact semantics of the
      G43-pinned pipeline, ported to Python: every dir named `_hq` under the
      root is a candidate; candidates under `_archive/` or `_demo-framework/`
      are ineligible; the SHALLOWEST eligible candidate is canonical. Two
      eligible candidates tied at the shallowest depth — or no eligible
      candidate at all while decoys exist — means the canonical `_hq` cannot
      be confidently resolved: the run REFUSES EVERYTHING and touches nothing.

  CLASSIFICATION  Each non-canonical `_hq`: under `_archive/` -> archived
      decoy (quarantine lane); under `_demo-framework/` -> demo decoy
      (quarantine lane); anywhere else -> UNEXPECTED — review lane, NEVER
      touched (an unexpected `_hq` is exactly the thing a human must look at
      before a machine renames it).

  REMEDIATION  RENAME, not delete (archive-first doctrine; the mount blocks
      hard-deletes anyway): `_hq` -> `hq-decoy-quarantined-YYYYMMDD` in
      place, so no `_hq`-named path shape survives for a legacy glob to bind,
      while every byte stays recoverable. A MANIFEST.md is written inside the
      renamed dir stating what/why/when/undo, carrying a machine-readable
      block with a content digest of the quarantined tree.

  PER-DECOY REFUSALS (review lane, that decoy untouched):
      - the rename target already exists;
      - the decoy contains a `.writer.lock` younger than 24h (someone may be
        mid-write; an unstatable lock counts as fresh — refuse, don't guess);
      - the dir vanished or changed shape between plan and apply.

  ROLLBACK  `--rollback` renames each quarantined dir back to `_hq` under a
      diff-guard: the tree digest (MANIFEST.md excluded) must equal the
      digest recorded at quarantine time — any drift is REFUSED, the
      quarantine dir and its manifest stay in place. On a verified restore
      the manifest is MOVED into the receipt dir (audit trail retained, no
      delete), leaving the restored `_hq` byte-identical to what was
      quarantined. A journaled quarantine whose dir has vanished (and was
      never restored) is reported LOUDLY.

Safety rails inherited from migrate_seed_anchors.py (MIGRATE1) by import:
long-path (\\?\\) IO on win32, fsynced crash-safe journal lines, atomic
receipt writes. The receipt's centerpiece is the `untouched` list — every
`_hq` discovered that this run did NOT rename, each with a reason.

Usage:
    python cleanup_decoy_hq.py <workspace_root> [--apply] [--rollback]
        [--receipt-dir DIR]

Dry-run is the default; nothing is renamed without --apply. A receipt JSON
is ALWAYS written (dry-run, apply, and rollback). Default receipt dir is
`<canonical _hq>/data/decoyclean1`; when the canonical `_hq` cannot be
resolved (refuse-all) and no --receipt-dir is given, the receipt lands in a
fresh temp dir whose path is printed — the workspace is never touched to
store a refusal.

Exit codes: 0 = clean (including a dry-run with planned work and a run that
found nothing); 1 = one or more items refused or routed to review; 2 =
refuse-all (canonical `_hq` unresolvable — ambiguous or absent).

stdlib + shared/scripts (helpers imported from migrate_seed_anchors,
receipts via atomic_write).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))  # shared/scripts on path
sys.path.insert(0, str(_HERE.parent))         # release_actions on path

# MIGRATE1's hardened rails, reused: extended-length paths, byte IO, hashing,
# rel-path normalization, atomic receipt writes.
from migrate_seed_anchors import (  # noqa: E402
    _exists, _lp, _read_bytes, _rel_posix, _sha256, _write_receipt)

HQ_NAME = "_hq"
QUARANTINE_PREFIX = "hq-decoy-quarantined-"
MANIFEST_NAME = "MANIFEST.md"
STAMP = "decoyclean1"
JOURNAL_NAME = "decoyclean1_journal.jsonl"

# The two prune targets of the G43-pinned canonical pipeline. A `_hq` under
# either is ineligible as canonical AND classifiable as a known decoy.
EXCLUDED_PARENTS = ("_archive", "_demo-framework")

# Trees never walked at all (infrastructure noise, not workspace content).
WALK_SKIP = {".git", "node_modules", "__pycache__"}

FRESH_LOCK_SECONDS = 24 * 3600

_MACHINE_RE = re.compile(
    r"<!--\s*decoyclean1-machine\s*\n(.*?)\n\s*-->", re.DOTALL)


# --------------------------------------------------------------------------
# Journal (MIGRATE1 fix-6 shape, own file name)
# --------------------------------------------------------------------------

def _journal_path(receipt_dir) -> Path:
    return Path(receipt_dir) / JOURNAL_NAME


def _journal_append(receipt_dir, rec: dict) -> None:
    """Fsynced O_APPEND of one JSON line — lands the moment the rename does,
    survives a crash of the rest of the batch."""
    jp = _journal_path(receipt_dir)
    os.makedirs(_lp(jp.parent), exist_ok=True)
    fd = os.open(_lp(jp), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_journal(receipt_dir) -> list[dict]:
    """Defensive journal read — a torn trailing line is skipped, never
    fatal."""
    out: list[dict] = []
    try:
        raw = _read_bytes(_journal_path(receipt_dir)).decode(
            "utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# --------------------------------------------------------------------------
# Discovery + canonical resolution (the G43 semantics, in Python)
# --------------------------------------------------------------------------

def _walk(root):
    """os.walk under an extended-length path, WALK_SKIP pruned. NOTE:
    `_archive`/`_demo-framework` are deliberately NOT pruned here — this tool
    exists to look inside them; only canonical ELIGIBILITY prunes them."""
    for dirpath, dirnames, filenames in os.walk(_lp(root)):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP]
        yield dirpath, dirnames, filenames


def discover_hq_dirs(root) -> list[str]:
    """Rel-posix paths of every directory literally named `_hq` under root
    (root itself excluded — a root NAMED `_hq` is operator error, not a
    workspace)."""
    found = []
    for dirpath, dirnames, _ in _walk(root):
        for d in dirnames:
            if d == HQ_NAME:
                found.append(_rel_posix(root, os.path.join(dirpath, d)))
    return sorted(found)


def _decoy_class(rel: str) -> str:
    """archived-decoy | demo-decoy | unexpected, by nearest classification of
    the ancestor chain (an `_archive` anywhere above wins over nothing)."""
    ancestors = PurePosixPath(rel).parts[:-1]
    if "_archive" in ancestors:
        return "archived-decoy"
    if "_demo-framework" in ancestors:
        return "demo-decoy"
    return "unexpected"


def resolve_canonical(candidates: list[str]) -> dict:
    """Mirror of the G43-pinned pipeline: prune `_archive`/`_demo-framework`,
    shallowest eligible wins; a depth tie means no confident answer.

    Returns {"status": resolved|ambiguous|none, "canonical": rel|None,
             "tied": [...]} — `ambiguous`/`none` are refuse-all states
    whenever any candidate exists."""
    eligible = [
        c for c in candidates
        if not any(p in EXCLUDED_PARENTS for p in PurePosixPath(c).parts[:-1])
    ]
    if not eligible:
        return {"status": "none", "canonical": None, "tied": []}
    depth = {c: len(PurePosixPath(c).parts) for c in eligible}
    shallowest = min(depth.values())
    tied = sorted(c for c in eligible if depth[c] == shallowest)
    if len(tied) > 1:
        return {"status": "ambiguous", "canonical": None, "tied": tied}
    return {"status": "resolved", "canonical": tied[0], "tied": tied}


# --------------------------------------------------------------------------
# Tree digest (the rollback diff-guard's currency)
# --------------------------------------------------------------------------

def tree_digest(base, exclude_top: tuple[str, ...] = ()) -> dict:
    """Content digest of a directory tree: every file's (rel path, sha256)
    plus every directory rel path (so empty dirs count), serialized sorted
    and hashed. `exclude_top` names top-level files left out (MANIFEST.md at
    verify time). Byte-identical trees — and only those — digest equal."""
    base = Path(base)
    lines = []
    n_files = 0
    for dirpath, dirnames, filenames in _walk(base):
        for d in dirnames:
            lines.append("D " + _rel_posix(base, os.path.join(dirpath, d)))
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = _rel_posix(base, full)
            if rel in exclude_top:
                continue
            lines.append("F " + rel + " " + _sha256(_read_bytes(full)))
            n_files += 1
    digest = _sha256("\n".join(sorted(lines)).encode("utf-8"))
    return {"sha256": digest, "file_count": n_files}


# --------------------------------------------------------------------------
# Fresh-writer-lock probe
# --------------------------------------------------------------------------

def fresh_writer_locks(root, rel: str, now: float | None = None) -> list[str]:
    """Files named (or suffixed) `.writer.lock` inside the decoy whose mtime
    is younger than 24h — someone may be mid-write. An unstatable lock counts
    as fresh: refuse, don't guess."""
    now = time.time() if now is None else now
    base = Path(root) / rel
    hits = []
    for dirpath, _, filenames in _walk(base):
        for name in filenames:
            if name != ".writer.lock" and not name.endswith(".writer.lock"):
                continue
            full = os.path.join(dirpath, name)
            try:
                age = now - os.stat(full).st_mtime
            except OSError:
                age = 0.0  # unreadable -> treated as fresh
            if age < FRESH_LOCK_SECONDS:
                hits.append(_rel_posix(base, full))
    return sorted(hits)


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def build_plan(root) -> dict:
    root = Path(root)
    candidates = discover_hq_dirs(root)
    res = resolve_canonical(candidates)

    entries = []
    refuse_all = bool(candidates) and res["status"] != "resolved"
    for rel in candidates:
        if rel == res["canonical"]:
            entries.append({"rel": rel, "classification": "canonical",
                            "verdict": "canonical",
                            "reason": "the canonical _hq — never touched"})
            continue
        cls = _decoy_class(rel)
        if refuse_all:
            entries.append({
                "rel": rel, "classification": cls,
                "verdict": "refused-canonical-unresolved",
                "reason": ("canonical _hq cannot be confidently resolved "
                           f"({res['status']}"
                           + (f": {', '.join(res['tied'])}" if res["tied"]
                              else "")
                           + ") — refusing everything, touching nothing")})
        elif cls == "unexpected":
            entries.append({
                "rel": rel, "classification": cls,
                "verdict": "review-unexpected-location",
                "reason": ("a _hq outside _archive/ and _demo-framework/ "
                           "that is not the canonical one — review lane, "
                           "never machine-renamed")})
        else:
            locks = fresh_writer_locks(root, rel)
            if locks:
                entries.append({
                    "rel": rel, "classification": cls,
                    "verdict": "refused-fresh-writer-lock",
                    "reason": ("a .writer.lock younger than 24h is inside "
                               "this decoy — someone may be writing; "
                               "untouched (" + ", ".join(locks) + ")"),
                    "fresh_locks": locks})
            else:
                entries.append({"rel": rel, "classification": cls,
                                "verdict": "quarantine",
                                "reason": f"{cls} — plan: rename in place to "
                                          f"{QUARANTINE_PREFIX}YYYYMMDD"})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "candidates": candidates,
        "canonical": res["canonical"],
        "canonical_status": res["status"],
        "canonical_tied": res["tied"],
        "refuse_all": refuse_all,
        "entries": entries,
    }


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

def _manifest_text(meta: dict) -> str:
    undo = ("python shared/scripts/release_actions/cleanup_decoy_hq.py "
            "<workspace_root> --rollback --receipt-dir <receipt_dir>")
    return (
        f"# QUARANTINED DECOY `_hq` — DECOYCLEAN1\n"
        f"\n"
        f"- **What**: this directory was named `_hq` at "
        f"`{meta['original_rel']}` and was renamed in place to "
        f"`{meta['quarantined_rel']}`. Every byte inside is unchanged; only "
        f"the directory name moved (this MANIFEST.md is the one added "
        f"file).\n"
        f"- **Why**: classified `{meta['classification']}` — a decoy `_hq` "
        f"that legacy workspace-discovery globs could bind instead of the "
        f"canonical `_hq` (attended-walk finding F-4 / HQRESOLVE1; shipped "
        f"code is hardened, this removes the decoy path shape itself). "
        f"Canonical at quarantine time: `{meta['canonical_rel']}`.\n"
        f"- **When**: {meta['quarantined_at']} (run {meta['run_id']}).\n"
        f"- **Undo**: `{undo}` — verifies the tree digest below, then "
        f"renames this directory back to `_hq` and moves this manifest into "
        f"the receipt dir. Never delete this file by hand; without it the "
        f"mechanical rollback refuses.\n"
        f"\n"
        f"<!-- decoyclean1-machine\n"
        f"{json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True)}\n"
        f"-->\n")


def _parse_manifest(path) -> dict | None:
    try:
        text = _read_bytes(path).decode("utf-8", errors="replace")
    except OSError:
        return None
    m = _MACHINE_RE.search(text)
    if not m:
        return None
    try:
        meta = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict) or meta.get("stamp") != STAMP:
        return None
    return meta


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def apply_plan(root, plan: dict, receipt_dir,
               date_str: str | None = None) -> dict:
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp_date = date_str or datetime.now().strftime("%Y%m%d")
    touched, refused, review = [], [], []

    for entry in plan["entries"]:
        verdict = entry["verdict"]
        if verdict == "canonical":
            continue
        if verdict == "quarantine":
            try:
                outcome = _apply_one(root, plan, entry, ts, run_id,
                                     receipt_dir, stamp_date)
            except Exception as exc:  # one bad decoy never kills the batch
                outcome = {"rel": entry["rel"], "action": "error-refused",
                           "reason": f"{type(exc).__name__}: {exc}"}
                _journal_append(receipt_dir, {"run_id": run_id, "ts": ts,
                                              **outcome})
            if outcome["action"] == "quarantined":
                touched.append(outcome)
            else:
                refused.append(outcome)
        elif verdict.startswith("refused"):
            refused.append({"rel": entry["rel"], "action": verdict,
                            "reason": entry["reason"]})
        else:  # review-* lanes
            review.append({"rel": entry["rel"], "action": verdict,
                           "reason": entry["reason"]})

    receipt = _build_receipt("apply", plan, receipt_dir, run_id,
                             touched=touched, refused=refused, review=review)
    _write_receipt(receipt_dir, "decoyclean1_receipt_apply.json", receipt)
    return receipt


def _apply_one(root, plan: dict, entry: dict, ts: str, run_id: str,
               receipt_dir, stamp_date: str) -> dict:
    rel = entry["rel"]
    src = Path(root) / rel

    # Plan->apply drift: the dir must still exist and still be a directory.
    if not os.path.isdir(_lp(src)):
        outcome = {"rel": rel, "action": "refused-vanished",
                   "reason": "planned decoy is no longer a directory at "
                             "apply time — refused; re-run to re-plan"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # Fresh-lock re-probe at apply time (a lock may have appeared since plan).
    locks = fresh_writer_locks(root, rel)
    if locks:
        outcome = {"rel": rel, "action": "refused-fresh-writer-lock",
                   "reason": "a .writer.lock younger than 24h appeared — "
                             "someone may be writing; untouched ("
                             + ", ".join(locks) + ")",
                   "fresh_locks": locks}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    dst = src.parent / (QUARANTINE_PREFIX + stamp_date)
    dst_rel = _rel_posix(root, dst)
    if _exists(dst):
        outcome = {"rel": rel, "action": "refused-target-exists",
                   "reason": f"rename target already exists ({dst_rel}) — "
                             "refused; a human decides what that dir is"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # Digest BEFORE the rename — the rollback diff-guard's reference value.
    dig = tree_digest(src)

    os.rename(_lp(src), _lp(dst))

    # The rename has LANDED. A manifest/journal failure past this point must
    # not demote the outcome — it is reported as a warning instead (the
    # MIGRATE1 review-F3 posture).
    meta = {
        "stamp": STAMP,
        "run_id": run_id,
        "quarantined_at": ts,
        "original_rel": rel,
        "quarantined_rel": dst_rel,
        "classification": entry["classification"],
        "canonical_rel": plan["canonical"],
        "tree_sha256": dig["sha256"],
        "file_count": dig["file_count"],
    }
    warning = None
    try:
        mp = dst / MANIFEST_NAME
        with open(_lp(mp), "w", encoding="utf-8", newline="") as fh:
            fh.write(_manifest_text(meta))
            fh.flush()
            os.fsync(fh.fileno())
    except Exception as exc:  # noqa: BLE001
        warning = (f"manifest write failed AFTER the rename landed: {exc} — "
                   f"the quarantine stands but mechanical rollback will "
                   f"refuse; the journal line carries the digest")
        sys.stderr.write(f"[decoyclean1] WARNING {rel}: {warning}\n")

    outcome = {"rel": rel, "action": "quarantined",
               "quarantined_rel": dst_rel,
               "classification": entry["classification"],
               "tree_sha256": dig["sha256"],
               "file_count": dig["file_count"]}
    if warning:
        outcome["warning"] = warning
    try:
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
    except Exception as exc:  # noqa: BLE001
        outcome["warning"] = ((outcome.get("warning") or "")
                              + f" | journal append failed after the rename "
                                f"landed: {exc}").lstrip(" |")
        sys.stderr.write(f"[decoyclean1] WARNING {rel}: journal append "
                         f"failed after the rename landed: {exc}\n")
    return outcome


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------

def rollback(root, receipt_dir) -> dict:
    """Rename each quarantined dir back to `_hq`, diff-guarded: the tree
    digest (manifest excluded) must equal the manifest's recorded digest.
    Refusals leave the quarantine dir AND its manifest exactly in place.
    Idempotent: a second rollback finds nothing and screams about nothing."""
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    restored, refused, missing = [], [], []

    qdirs = []
    for dirpath, dirnames, _ in _walk(root):
        for d in dirnames:
            if d.startswith(QUARANTINE_PREFIX):
                qdirs.append(os.path.join(dirpath, d))

    seen_rels = set()
    for qd in sorted(qdirs):
        qrel = _rel_posix(root, qd)
        meta = _parse_manifest(Path(qd) / MANIFEST_NAME)
        if meta is None:
            refused.append({"rel": qrel,
                            "issue": "no parseable decoyclean1 manifest — "
                                     "not mechanically rollback-able; needs "
                                     "a human"})
            continue
        seen_rels.add(meta.get("original_rel"))
        target = Path(qd).parent / HQ_NAME
        if _exists(target):
            refused.append({"rel": qrel,
                            "issue": "a directory named _hq already exists "
                                     "beside this quarantine — refusing to "
                                     "guess which is real"})
            continue
        dig = tree_digest(qd, exclude_top=(MANIFEST_NAME,))
        if dig["sha256"] != meta.get("tree_sha256"):
            refused.append({"rel": qrel,
                            "issue": "content changed since quarantine "
                                     "(tree digest mismatch) — REFUSED; the "
                                     "quarantine dir and manifest are kept "
                                     "in place, reconcile by hand"})
            continue
        # Verified. Move the manifest into the receipt dir FIRST (audit
        # trail retained — a move, never a delete), journal the move, then
        # rename. A crash between the two leaves a manifest-less quarantine
        # dir that the journal line fully describes.
        os.makedirs(_lp(receipt_dir), exist_ok=True)
        # The path hash keeps two same-run, same-date quarantines (e.g. the
        # archived + demo decoys) from colliding on one archive filename —
        # a collision would silently overwrite the first audit file.
        saved = receipt_dir / (f"MANIFEST_{meta['run_id']}_"
                               f"{_sha256(qrel.encode('utf-8'))[:10]}_"
                               f"{run_id}.md")
        os.replace(_lp(Path(qd) / MANIFEST_NAME), _lp(saved))
        _journal_append(receipt_dir, {
            "run_id": run_id, "ts": ts, "rel": meta["original_rel"],
            "action": "manifest-moved", "quarantined_rel": qrel,
            "manifest_saved_as": str(saved)})
        os.rename(_lp(qd), _lp(target))
        rec = {"rel": meta["original_rel"], "quarantined_rel": qrel,
               "action": "restored", "tree_sha256": dig["sha256"]}
        restored.append(rec)
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **rec})

    # A journaled quarantine that is neither on disk nor journaled restored —
    # say it loudly (the MIGRATE1 missing-backup posture).
    journal = read_journal(receipt_dir)
    restored_rels = {j.get("rel") for j in journal
                     if j.get("action") == "restored"}
    restored_rels.update(r["rel"] for r in restored)
    for j in journal:
        if j.get("action") != "quarantined":
            continue
        orig = j.get("rel")
        if not orig or orig in restored_rels or orig in seen_rels:
            continue
        if _exists(Path(root) / orig):
            continue  # an _hq is back at the original path — restored by hand
        item = {"rel": orig, "quarantined_rel": j.get("quarantined_rel"),
                "issue": "MISSING QUARANTINE DIR for a journaled quarantine "
                         "— cannot be rolled back mechanically"}
        if item not in missing:
            missing.append(item)
            sys.stderr.write(
                f"[decoyclean1 rollback] MISSING: {j.get('quarantined_rel')}"
                f" — journal says {orig} was quarantined but the dir is "
                f"gone; mixed state, needs a human.\n")

    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "rollback",
        "run_id": run_id,
        "root": str(root),
        "counts": {"restored": len(restored), "refused": len(refused),
                   "missing": len(missing)},
        "restored": restored,
        "refused": refused,
        "missing": missing,
    }
    _write_receipt(receipt_dir, "decoyclean1_receipt_rollback.json", receipt)
    return receipt


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def _build_receipt(mode: str, plan: dict, receipt_dir, run_id: str | None,
                   touched=None, refused=None, review=None) -> dict:
    touched = touched or []
    refused = refused or []
    review = review or []
    touched_rels = {t["rel"] for t in touched}
    outcome_by_rel = {}
    for o in list(refused) + list(review):
        outcome_by_rel.setdefault(o["rel"], o)
    # The centerpiece: every discovered `_hq` this run did NOT rename.
    untouched = []
    for entry in plan["entries"]:
        if entry["rel"] in touched_rels:
            continue
        o = outcome_by_rel.get(entry["rel"])
        untouched.append({
            "rel": entry["rel"],
            "classification": entry["classification"],
            "verdict": (o["action"] if o else entry["verdict"]),
            "reason": (o or entry)["reason"],
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "run_id": run_id,
        "root": plan["root"],
        "canonical": plan["canonical"],
        "canonical_status": plan["canonical_status"],
        "canonical_tied": plan["canonical_tied"],
        "refuse_all": plan["refuse_all"],
        "counts": {
            "discovered": len(plan["entries"]),
            "touched": len(touched),
            "refused": len(refused),
            "review": len(review),
            "untouched": len(untouched),
        },
        "touched": touched,
        "refused": refused,
        "review": review,
        "untouched": untouched,
    }


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def run_cleanup(root, *, apply: bool = False, receipt_dir=None,
                date_str: str | None = None) -> dict:
    """Dry-run by default: discover + classify + receipt, zero renames.
    `apply=True` executes the plan under every refuse rule. `date_str`
    overrides the quarantine-name date stamp (tests pass a PAST-dated
    literal; at runtime the default datetime.now() is correct)."""
    root = Path(root)
    plan = build_plan(root)
    receipt_dir = _resolve_receipt_dir(root, plan, receipt_dir)
    if apply and not plan["refuse_all"]:
        return apply_plan(root, plan, receipt_dir, date_str=date_str)

    mode = "apply-refused-all" if (apply and plan["refuse_all"]) else "dry-run"
    plan_refused = [{"rel": e["rel"], "action": e["verdict"],
                     "reason": e["reason"]} for e in plan["entries"]
                    if e["verdict"].startswith("refused")]
    plan_review = [{"rel": e["rel"], "action": e["verdict"],
                    "reason": e["reason"]} for e in plan["entries"]
                   if e["verdict"].startswith("review")]
    receipt = _build_receipt(mode, plan, receipt_dir, None,
                             refused=plan_refused, review=plan_review)
    receipt["planned"] = [
        {"rel": e["rel"], "classification": e["classification"],
         "reason": e["reason"]}
        for e in plan["entries"] if e["verdict"] == "quarantine"]
    receipt["counts"]["planned"] = len(receipt["planned"])
    name = ("decoyclean1_receipt_refused.json" if mode == "apply-refused-all"
            else "decoyclean1_receipt_dryrun.json")
    _write_receipt(receipt_dir, name, receipt)
    return receipt


def _resolve_receipt_dir(root, plan: dict, receipt_dir):
    if receipt_dir:
        return Path(receipt_dir)
    if plan["canonical"]:
        return Path(root) / plan["canonical"] / "data" / "decoyclean1"
    # Refuse-all / nothing-found with no --receipt-dir: never touch the
    # workspace just to store a refusal — a fresh temp dir, printed loudly.
    td = Path(tempfile.mkdtemp(prefix="decoyclean1_receipts_"))
    sys.stderr.write(f"[decoyclean1] canonical _hq unresolved and no "
                     f"--receipt-dir given; receipt lands in {td}\n")
    return td


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="workspace root to clean")
    ap.add_argument("--apply", action="store_true",
                    help="actually rename (default: dry-run)")
    ap.add_argument("--rollback", action="store_true",
                    help="rename quarantined dirs back under the diff-guard")
    ap.add_argument("--receipt-dir", default=None,
                    help="where receipts + journal land "
                         "(default: <canonical _hq>/data/decoyclean1)")
    args = ap.parse_args(argv)
    if args.apply and args.rollback:
        ap.error("--apply and --rollback are mutually exclusive")

    root = Path(args.root)
    if not os.path.isdir(_lp(root)):
        sys.stderr.write(f"[decoyclean1] not a directory: {root}\n")
        return 2

    if args.rollback:
        rdir = (Path(args.receipt_dir) if args.receipt_dir
                else _resolve_receipt_dir(root, build_plan(root), None))
        r = rollback(root, rdir)
        print(f"rollback: restored={r['counts']['restored']} "
              f"refused={r['counts']['refused']} "
              f"missing={r['counts']['missing']}")
        for it in r["refused"] + r["missing"]:
            print(f"  REFUSED {it['rel']} — {it['issue']}")
        return 1 if (r["refused"] or r["missing"]) else 0

    r = run_cleanup(root, apply=args.apply, receipt_dir=args.receipt_dir)
    c = r["counts"]
    if r["mode"] == "dry-run":
        print(f"dry-run: planned={c['planned']} refused={c['refused']} "
              f"review={c['review']} untouched={c['untouched']}")
        for pl in r["planned"]:
            print(f"  PLAN quarantine {pl['rel']} ({pl['classification']})")
    elif r["mode"] == "apply-refused-all":
        print(f"REFUSED ALL: canonical _hq unresolved "
              f"({r['canonical_status']}"
              + (f": {', '.join(r['canonical_tied'])}" if r["canonical_tied"]
                 else "") + ") — nothing touched")
    else:
        print(f"apply: touched={c['touched']} refused={c['refused']} "
              f"review={c['review']} untouched={c['untouched']}")
        for t in r["touched"]:
            print(f"  QUARANTINED {t['rel']} -> {t['quarantined_rel']}")
    for it in r.get("refused", []):
        print(f"  REFUSED {it['rel']} — {it['reason']}")
    for it in r.get("review", []):
        print(f"  REVIEW {it['rel']} — {it['reason']}")
    if r["refuse_all"]:
        return 2
    return 1 if (r["refused"] or r["review"]) else 0


if __name__ == "__main__":
    sys.exit(main())
