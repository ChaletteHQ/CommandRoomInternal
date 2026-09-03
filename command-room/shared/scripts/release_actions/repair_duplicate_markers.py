#!/usr/bin/env python3
"""DUPMARK1 — duplicate-LIVE-STATE-marker repair one-shot, dry-run by default.

The companion repair SPEC_MIGRATE1 names as required. Real-world shape it
exists to fix: a PROJECT_BRAIN.md carrying TWO `LIVE-STATE:people` marker
blocks — one current (machine-rendered, tag carries render attrs such as
generated_at= / source_seq=) and one stale hand-copied twin — which makes
`render_brain_block` perpetuate the stale twin forever and makes MIGRATE1
refuse the file as "malformed markers: duplicate start marker id 'people'".

ADJUDICATION RULES (reviewer-visible defaults; the tool never guesses):

  A1. Fence-aware everywhere. Markers inside ``` fenced blocks or inline
      backtick spans are quoted text — they neither create a duplicate nor
      participate in one (same masking as migrate_seed_anchors). A file with
      an UNTERMINATED fence anywhere is ambiguous (everything after the
      fence-open is blind) and goes to review, never repaired.
  A2. Clean structure required. For a duplicated id, the markers outside
      code spans must form strictly alternating, complete start/end pairs
      (S,E,S,E,...). Anything else — unbalanced, nested, end-before-start —
      is review lane.
  A3. Machine provenance decides which block survives. A block is "machine"
      when its start tag carries render attrs (generated_at= / source_seq=)
      or the MIGRATE1 seed stamp (seeded_by=). Exactly ONE machine block and
      the rest non-machine -> the machine block is kept. BOTH machine or
      NEITHER machine -> review lane; the tool refuses to pick.
  A4. Stale twins must be content-subsets. A non-machine twin is removable
      only when every effective interior line (whitespace-normalized;
      blanks, HTML comments, and table separator rows ignored) also appears
      in the kept machine block's interior. A twin carrying ANY unique line
      might be hand gold — review lane, with the unique lines listed in the
      record so the human ruling is fast.
  A5. All-or-nothing per file. If any duplicated id in a file fails A1-A4,
      the whole file goes to review — no partial repairs.
  A6. Byte-exact surgery. Only the stale twin's own span is removed (its
      marker lines, interior, and the twin's trailing newline). Every other
      byte of the file — including CRLF line endings, BOM, and surrounding
      hand lines — is preserved exactly. A removal span that would swallow
      another id's marker refuses instead.
  A7. Conflict-copy siblings block repair (same posture as MIGRATE1): a
      folder holding a `PROJECT_BRAIN*` conflict sibling is a review item;
      the canonical file next to one is not repaired.

SAFETY RAILS (inherited from migrate_seed_anchors, reused via import):

  - classify-time sha256 hash pin, re-read + re-checked at apply, and
    re-verified immediately before the atomic rename (_pinned_replace);
  - fresh attr-clean `.pre-dupmark1` backup before the first write, never
    clobbered on re-runs;
  - fsynced per-file journal (dupmark1_journal.jsonl) — every landed repair
    survives a mid-batch crash;
  - witness sidecar re-written for the repaired bytes (witnessed_write);
  - receipt JSON always written (dry-run, apply, and rollback), with the
    untouched-with-reasons list as the centerpiece;
  - long-path (\\\\?\\) handling on every open/stat/walk;
  - rollback (--rollback) under a strict diff-guard: a backup is restored
    only when the current file equals EXACTLY what this tool would produce
    from the backup (recomputed, not trusted from the journal) — any other
    diff is refused and the backup is kept in place.

Usage:
    python repair_duplicate_markers.py <workspace_root> [--apply]
        [--rollback] [--receipt-dir DIR]

Dry-run is the default; nothing is written to any brain without --apply.

stdlib + shared/scripts (migrate_seed_anchors, witnessed_write,
atomic_write). NEVER run against a live workspace — copies only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))  # shared/scripts
sys.path.insert(0, str(_HERE.parent))         # release_actions

import migrate_seed_anchors as msa  # noqa: E402  — shared rails + grammar
import witnessed_write as ww  # noqa: E402

BACKUP_SUFFIX = ".pre-dupmark1"
JOURNAL_NAME = "dupmark1_journal.jsonl"

# Table separator rows (|---|:---:| ...) are structural, not content (A4).
_TABLE_SEP_RE = re.compile(r"^[|\s:\-]+$")
_WS_RUN_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------
# Journal (same fsync-append shape as MIGRATE1, own file)
# --------------------------------------------------------------------------

def _journal_path(receipt_dir) -> Path:
    return Path(receipt_dir) / JOURNAL_NAME


def _journal_append(receipt_dir, rec: dict) -> None:
    jp = _journal_path(receipt_dir)
    os.makedirs(msa._lp(jp.parent), exist_ok=True)
    fd = os.open(msa._lp(jp), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_journal(receipt_dir) -> list[dict]:
    jp = _journal_path(receipt_dir)
    out = []
    try:
        raw = msa._read_bytes(jp).decode("utf-8", errors="replace")
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
# Core pure analysis — shared by classify, apply, and the rollback guard
# --------------------------------------------------------------------------

def _is_machine_tag(tag: str) -> bool:
    """A3: render attrs or the MIGRATE1 seed stamp = machine provenance."""
    return ("source_seq=" in tag or "generated_at=" in tag
            or f"{msa.SEED_STAMP_KEY}=" in tag)


def _effective_lines(interior: str) -> list[str]:
    """A4 normalization: strip, collapse whitespace runs; drop blanks,
    single-line HTML comments, and table separator rows."""
    out = []
    for line in interior.splitlines():
        s = _WS_RUN_RE.sub(" ", line.strip())
        if not s:
            continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        if _TABLE_SEP_RE.match(s):
            continue
        out.append(s)
    return out


def _line_span(text: str, tag_start: int, tag_end: int) -> tuple[int, int]:
    """Expand a marker-pair span to whole lines when the markers own their
    lines: start grows left over same-line whitespace to the line start, end
    grows right over the single trailing newline (CRLF-aware)."""
    line_start = text.rfind("\n", 0, tag_start) + 1
    owns_line = text[line_start:tag_start].strip() == ""
    a = line_start if owns_line else tag_start
    b = tag_end
    if owns_line:  # only eat the newline when we own the whole line
        if text[b:b + 2] == "\r\n":
            b += 2
        elif text[b:b + 1] == "\n":
            b += 1
    return a, b


def analyze_text(text: str) -> dict:
    """Pure adjudication of one file's content. Returns:
      {status: clean}                            — no duplicate ids
      {status: review, reasons: [...]}           — refused, with reasons
      {status: repairable, dup_ids, keep, removals: [
          {id, span: [a,b], sha256, bytes, preview}]}
    Deterministic: apply and the rollback diff-guard both recompute it."""
    masked, unterminated = msa.mask_code_spans(text)

    def _events(src: str) -> dict[str, list]:
        ev: dict[str, list] = {}
        for m in msa.GEN_START_RE.finditer(src):
            ev.setdefault(m.group(1).lower(), []).append(("S", m))
        for m in msa.GEN_END_RE.finditer(src):
            ev.setdefault(m.group(1).lower(), []).append(("E", m))
        for lst in ev.values():
            lst.sort(key=lambda t: t[1].start())
        return ev

    ev = _events(masked)
    dup_ids = sorted(bid for bid, lst in ev.items()
                     if sum(1 for k, _ in lst if k == "S") >= 2)
    if not dup_ids:
        # A1: raw-vs-masked disagreement under an unterminated fence is
        # ambiguous — a duplicate may be hiding in the blind region.
        if unterminated:
            raw_ev = _events(text)
            hidden = [bid for bid, lst in raw_ev.items()
                      if sum(1 for k, _ in lst if k == "S") >= 2]
            if hidden:
                return {"status": "review", "reasons": [
                    "unterminated code fence hides possible duplicate "
                    f"markers ({', '.join(sorted(hidden))}) — ambiguous, "
                    "review by hand (A1)"]}
        return {"status": "clean"}

    if unterminated:
        return {"status": "review", "reasons": [
            "duplicate markers present AND an unterminated code fence — "
            "the blind region could hold more twins; review by hand (A1)"]}

    reasons, removals, keep = [], [], {}
    for bid in dup_ids:
        lst = ev[bid]
        kinds = "".join(k for k, _ in lst)
        n_starts = kinds.count("S")
        if kinds != "SE" * n_starts:
            reasons.append(
                f"id '{bid}': markers are not clean alternating pairs "
                f"(pattern {kinds}) — review (A2)")
            continue
        blocks = []
        for i in range(n_starts):
            sm = lst[2 * i][1]
            em = lst[2 * i + 1][1]
            tag = text[sm.start():sm.end()]
            interior = text[sm.end():em.start()]
            blocks.append({
                "tag": tag, "interior": interior,
                "machine": _is_machine_tag(tag),
                "tag_start": sm.start(), "tag_end": em.end(),
            })
        machine = [b for b in blocks if b["machine"]]
        if len(machine) == 0:
            reasons.append(
                f"id '{bid}': NEITHER block carries machine provenance "
                "(no render attrs, no seed stamp) — refusing to pick (A3)")
            continue
        if len(machine) > 1:
            reasons.append(
                f"id '{bid}': {len(machine)} blocks ALL carry machine "
                "provenance — refusing to pick (A3)")
            continue
        kept = machine[0]
        kept_lines = set(_effective_lines(kept["interior"]))
        ok = True
        for twin in blocks:
            if twin is kept:
                continue
            unique = [ln for ln in _effective_lines(twin["interior"])
                      if ln not in kept_lines]
            if unique:
                reasons.append(
                    f"id '{bid}': stale twin carries lines absent from the "
                    f"kept machine block — possible hand content, review "
                    f"(A4). Unique lines: {json.dumps(unique[:10])}")
                ok = False
                break
        if not ok:
            continue
        for twin in blocks:
            if twin is kept:
                continue
            a, b = _line_span(text, twin["tag_start"], twin["tag_end"])
            span_text = text[a:b]
            # A6: a span may not swallow any marker beyond the twin's own.
            n_markers = (len(msa.GEN_START_RE.findall(span_text))
                         + len(msa.GEN_END_RE.findall(span_text)))
            if n_markers != 2:
                reasons.append(
                    f"id '{bid}': removal span would swallow another "
                    "marker — review (A6)")
                ok = False
                break
            removals.append({
                "id": bid, "span": [a, b],
                "sha256": msa._sha256(span_text.encode("utf-8")),
                "bytes": len(span_text.encode("utf-8")),
                "preview": span_text[:160],
            })
        if ok:
            keep[bid] = {"tag": kept["tag"][:160]}

    if reasons:
        return {"status": "review", "reasons": reasons, "dup_ids": dup_ids}
    return {"status": "repairable", "dup_ids": dup_ids, "keep": keep,
            "removals": sorted(removals, key=lambda r: r["span"][0])}


def repaired_text(text: str, removals: list[dict]) -> str:
    """A6 byte-exact surgery: excise removal spans, preserve everything
    else. Spans are applied back-to-front so offsets stay valid."""
    out = text
    for r in sorted(removals, key=lambda x: x["span"][0], reverse=True):
        a, b = r["span"]
        out = out[:a] + out[b:]
    return out


# --------------------------------------------------------------------------
# Classification over a workspace (MIGRATE1 discovery + fencing posture)
# --------------------------------------------------------------------------

def classify_file(root, rel: str) -> dict:
    rec = {"rel": rel}
    p = Path(root) / rel
    try:
        raw = msa._read_bytes(p)
    except OSError as exc:
        rec.update({"verdict": "unreadable", "reason": f"unreadable: {exc}"})
        return rec
    rec["size_bytes"] = len(raw)
    rec["sha256"] = msa._sha256(raw)  # hash pin, re-checked at apply
    if not raw.strip():
        rec.update({"verdict": "clean", "reason": "empty file — no markers"})
        return rec
    if msa._looks_utf16(raw):
        rec.update({"verdict": "review-utf16",
                    "reason": "utf16-encoded content — cannot scan safely; "
                              "review lane"})
        return rec
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rec.update({"verdict": "review-undecodable",
                    "reason": f"not valid utf-8 ({exc}) — review lane"})
        return rec

    verdict = analyze_text(text)
    if verdict["status"] == "clean":
        rec.update({"verdict": "clean",
                    "reason": "no duplicate LIVE-STATE ids outside code "
                              "spans"})
    elif verdict["status"] == "review":
        rec.update({"verdict": "review",
                    "reason": "; ".join(verdict["reasons"]),
                    "reasons": verdict["reasons"],
                    "dup_ids": verdict.get("dup_ids", [])})
    else:
        rec.update({"verdict": "repair",
                    "reason": "duplicate ids with exactly one machine block "
                              "each; stale twins are content-subsets — "
                              f"repairable: {', '.join(verdict['dup_ids'])}",
                    "dup_ids": verdict["dup_ids"],
                    "keep": verdict["keep"],
                    "removals": verdict["removals"]})
    return rec


def build_plan(root) -> dict:
    disc = msa.discover(root)  # PROJECT_BRAIN* glob + dir fences + conflicts
    conflict_dirs = {str(PurePosixPath(c).parent) for c in disc["conflicts"]}
    entries = []
    for rel in disc["canonical"]:
        rec = classify_file(root, rel)
        parent = str(PurePosixPath(rel).parent)
        if parent in conflict_dirs and rec["verdict"] == "repair":
            rec["verdict"] = "review-conflict-sibling"
            rec["reason"] = ("conflict-copy sibling(s) present in this "
                            "folder — blocking review item; not repaired "
                            "(A7)")
        entries.append(rec)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "entries": entries,
        "blocking_review": [
            {"rel": c, "issue": "conflict-copy sibling of a "
                                "PROJECT_BRAIN.md — needs a human ruling"}
            for c in disc["conflicts"]],
        "dual_folder_pairs": disc["dual_folder_pairs"],
    }


# --------------------------------------------------------------------------
# Apply
# --------------------------------------------------------------------------

def _apply_one(root, entry: dict, ts: str, run_id: str, receipt_dir) -> dict:
    rel = entry["rel"]
    p = Path(root) / rel
    pin = entry["sha256"]

    if not os.access(msa._lp(p), os.W_OK):
        outcome = {"rel": rel, "action": "skip-not-writable",
                   "reason": "file is not writable — skipped; clear the "
                             "attribute and re-run"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # Re-read + hash pin — refuse on any drift since classification.
    raw2 = msa._read_bytes(p)
    if msa._sha256(raw2) != pin:
        outcome = {"rel": rel, "action": "refused-content-drift",
                   "reason": "content changed since classification (hash "
                             "pin mismatch) — refused; re-run to "
                             "re-classify"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    text = raw2.decode("utf-8")
    # Recompute on the pinned bytes — never trust carried offsets blindly.
    verdict = analyze_text(text)
    if verdict["status"] != "repairable":
        outcome = {"rel": rel, "action": "refused-reverify",
                   "reason": "re-analysis at apply no longer says "
                             "repairable — refused"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    bak = str(p) + BACKUP_SUFFIX
    backup_created = False
    if not msa._exists(bak):
        msa._copy_backup(p, bak)  # fresh, attr-clean, fsynced
        backup_created = True

    after = repaired_text(text, verdict["removals"])
    try:
        msa._pinned_replace(p, after, pin)  # atomic + pre-rename hash check
    except msa.ContentDriftError as exc:
        outcome = {"rel": rel, "action": "refused-write-window-drift",
                   "reason": f"{exc} — refused; on-disk content untouched"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # Repair has LANDED — witness/journal failures downgrade to warnings,
    # never to an outcome that says the file was untouched (MIGRATE1 F3).
    warning = None
    try:
        ww.write_witness(p, data=after.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        warning = f"witness write failed after the repair landed: {exc}"
        sys.stderr.write(f"[dupmark1] WARNING {rel}: {warning}\n")
    outcome = {"rel": rel, "action": "repaired",
               "ids_repaired": verdict["dup_ids"],
               "removed": [{k: r[k] for k in
                            ("id", "span", "sha256", "bytes")}
                           for r in verdict["removals"]],
               "backup": rel + BACKUP_SUFFIX,
               "backup_created_this_run": backup_created,
               "sha_before": pin,
               "sha_after": msa._sha256(after.encode("utf-8"))}
    if warning:
        outcome["warning"] = warning
    try:
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
    except Exception as exc:  # noqa: BLE001
        outcome["warning"] = ((outcome.get("warning") or "")
                              + f" | journal append failed after the repair "
                                f"landed: {exc}").lstrip(" |")
        sys.stderr.write(f"[dupmark1] WARNING {rel}: journal append failed "
                         f"after the repair landed: {exc}\n")
    return outcome


def apply_plan(root, plan: dict, receipt_dir) -> dict:
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    touched, skipped, refused = [], [], []
    for entry in plan["entries"]:
        if entry["verdict"] != "repair":
            skipped.append({"rel": entry["rel"], "reason": entry["reason"],
                            "verdict": entry["verdict"]})
            continue
        try:
            outcome = _apply_one(root, entry, ts, run_id, receipt_dir)
        except Exception as exc:  # one bad file never kills the batch
            outcome = {"rel": entry["rel"], "action": "error-skip",
                       "reason": f"{type(exc).__name__}: {exc}"}
            _journal_append(receipt_dir,
                            {"run_id": run_id, "ts": ts, **outcome})
        if outcome["action"] == "repaired":
            touched.append(outcome)
        elif outcome["action"].startswith("refused"):
            refused.append(outcome)
        else:
            skipped.append({"rel": entry["rel"], "reason": outcome["reason"],
                            "verdict": outcome["action"]})
    receipt = _build_receipt("apply", plan, receipt_dir, run_id,
                             touched=touched, skipped=skipped,
                             refused=refused)
    _write_receipt(receipt_dir, "dupmark1_receipt_apply.json", receipt)
    return receipt


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def _build_receipt(mode: str, plan: dict, receipt_dir, run_id,
                   touched=None, skipped=None, refused=None) -> dict:
    touched = touched or []
    skipped = skipped or []
    refused = refused or []
    touched_rels = {t["rel"] for t in touched}
    outcome_by_rel = {}
    for o in list(refused) + list(skipped):
        outcome_by_rel.setdefault(o["rel"], o)
    untouched = []
    for entry in plan["entries"]:
        if entry["rel"] not in touched_rels:
            o = outcome_by_rel.get(entry["rel"])
            untouched.append({
                "rel": entry["rel"],
                "reason": (o or entry).get("reason", entry["verdict"]),
                "verdict": (o.get("verdict") or o.get("action")
                            if o else entry["verdict"]) or entry["verdict"],
            })
    for item in plan["blocking_review"]:
        untouched.append({"rel": item["rel"], "reason": item["issue"],
                          "verdict": "blocking-review"})
    history = [j for j in read_journal(receipt_dir)
               if j.get("action") == "repaired"
               and j.get("rel") not in touched_rels
               and (run_id is None or j.get("run_id") != run_id)]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "run_id": run_id,
        "root": plan["root"],
        "counts": {
            "discovered": len(plan["entries"]) + len(plan["blocking_review"]),
            "touched": len(touched),
            "skipped": len(skipped),
            "refused": len(refused),
            "untouched": len(untouched),
            "blocking_review": len(plan["blocking_review"]),
        },
        "touched": touched,
        "skipped": skipped,
        "refused": refused,
        "blocking_review": plan["blocking_review"],
        "dual_folder_pairs": plan["dual_folder_pairs"],
        "previously_repaired": history,
        "untouched": untouched,
    }


def _write_receipt(receipt_dir, name: str, receipt: dict) -> None:
    os.makedirs(msa._lp(receipt_dir), exist_ok=True)
    from atomic_write import atomic_write_json
    atomic_write_json(Path(msa._lp(Path(receipt_dir) / name)), receipt)


# --------------------------------------------------------------------------
# Rollback — diff-guard: current must equal repair(backup), recomputed
# --------------------------------------------------------------------------

def rollback(root, receipt_dir) -> dict:
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    restored, refused, missing_backup = [], [], []

    backups = []
    for dirpath, dirnames, filenames in os.walk(msa._lp(root)):
        dirnames[:] = [d for d in dirnames
                       if d not in msa.EXCLUDED_DIR_NAMES
                       and not any(d.startswith(p)
                                   for p in msa.EXCLUDED_DIR_PREFIXES)]
        for name in filenames:
            if name.endswith(BACKUP_SUFFIX):
                backups.append(os.path.join(dirpath, name))

    seen_rels = set()
    for bak in sorted(backups):
        orig = bak[:-len(BACKUP_SUFFIX)]
        rel = msa._rel_posix(root, orig)
        seen_rels.add(rel)
        try:
            bak_bytes = msa._read_bytes(bak)
            cur_bytes = (msa._read_bytes(orig)
                         if msa._exists(orig) else None)
        except OSError as exc:
            refused.append({"rel": rel, "issue": f"unreadable: {exc}"})
            continue
        if cur_bytes is None:
            refused.append({"rel": rel,
                            "issue": "original file missing next to its "
                                     "backup — refusing to guess"})
            continue
        if cur_bytes == bak_bytes:
            msa._remove_file(bak)
            restored.append({"rel": rel, "how": "already-equal"})
            continue
        # Diff-guard: recompute what THIS tool would produce from the
        # backup; only that exact result may be rolled back.
        expected = None
        try:
            v = analyze_text(bak_bytes.decode("utf-8"))
            if v["status"] == "repairable":
                expected = repaired_text(bak_bytes.decode("utf-8"),
                                         v["removals"]).encode("utf-8")
        except UnicodeDecodeError:
            expected = None
        if expected is not None and cur_bytes == expected:
            try:
                # Same pinned primitive as apply: a concurrent edit landing
                # between the guard read and the rename hashes different,
                # the replace refuses, and the backup stays for a re-run.
                msa._pinned_replace(orig, bak_bytes.decode("utf-8"),
                                    msa._sha256(cur_bytes))
            except msa.ContentDriftError as exc:
                refused.append({
                    "rel": rel,
                    "issue": f"content changed between the rollback guard "
                             f"read and the restore write ({exc}); REFUSED "
                             f"— backup kept. Re-run rollback."})
                continue
            ww.write_witness(orig, data=bak_bytes)
            msa._remove_file(bak)
            restored.append({"rel": rel, "how": "repair-reversed"})
        else:
            refused.append({"rel": rel,
                            "issue": "current content != backup + expected "
                                     "removal — post-repair edits present; "
                                     "REFUSED (restore would destroy them). "
                                     "Backup kept; reconcile by hand."})

    journal = read_journal(receipt_dir)
    rolled_back_rels = {j.get("rel") for j in journal
                        if j.get("action") == "rolled-back"}
    rolled_back_rels.update(r["rel"] for r in restored)
    for j in journal:
        if j.get("action") != "repaired":
            continue
        rel = j.get("rel")
        if rel in seen_rels or rel in rolled_back_rels or not rel:
            continue
        bak = str(Path(root) / rel) + BACKUP_SUFFIX
        if not msa._exists(bak):
            item = {"rel": rel,
                    "issue": "MISSING BACKUP for a journaled repair — this "
                             "file cannot be rolled back mechanically"}
            if item not in missing_backup:
                missing_backup.append(item)
                sys.stderr.write(
                    f"[dupmark1 rollback] MISSING BACKUP: {rel} — journal "
                    f"says it was repaired but no {BACKUP_SUFFIX} file "
                    f"exists; mixed state, needs a human.\n")

    for r in restored:
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts,
                                      "rel": r["rel"],
                                      "action": "rolled-back",
                                      "how": r["how"]})
    receipt = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "rollback",
        "run_id": run_id,
        "root": str(root),
        "counts": {"restored": len(restored), "refused": len(refused),
                   "missing_backup": len(missing_backup)},
        "restored": restored,
        "refused": refused,
        "missing_backup": missing_backup,
    }
    _write_receipt(receipt_dir, "dupmark1_receipt_rollback.json", receipt)
    return receipt


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def run_repair(root, *, apply: bool = False, receipt_dir=None) -> dict:
    """Dry-run by default: classify + plan + receipt, zero workspace
    writes. apply=True executes under the full rail set."""
    root = Path(root)
    receipt_dir = Path(receipt_dir) if receipt_dir else (
        root / "_hq" / "data" / "dupmark1")
    plan = build_plan(root)
    if apply:
        return apply_plan(root, plan, receipt_dir)
    planned = [{"rel": e["rel"], "dup_ids": e.get("dup_ids", []),
                "removals": [{k: r[k] for k in ("id", "span", "bytes")}
                             for r in e.get("removals", [])],
                "reason": e["reason"]}
               for e in plan["entries"] if e["verdict"] == "repair"]
    receipt = _build_receipt("dry-run", plan, receipt_dir, None)
    receipt["planned_repairs"] = planned
    receipt["counts"]["planned"] = len(planned)
    _write_receipt(receipt_dir, "dupmark1_receipt_dryrun.json", receipt)
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="workspace root (a COPY — never live)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default: dry-run)")
    ap.add_argument("--rollback", action="store_true",
                    help="restore .pre-dupmark1 backups under the "
                         "diff-guard")
    ap.add_argument("--receipt-dir", default=None,
                    help="where receipts + journal land "
                         "(default: <root>/_hq/data/dupmark1)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    receipt_dir = Path(args.receipt_dir) if args.receipt_dir else (
        root / "_hq" / "data" / "dupmark1")

    if args.rollback:
        r = rollback(root, receipt_dir)
        print(f"rollback: restored={r['counts']['restored']} "
              f"refused={r['counts']['refused']} "
              f"missing_backup={r['counts']['missing_backup']}")
        return 1 if (r["refused"] or r["missing_backup"]) else 0

    r = run_repair(root, apply=args.apply, receipt_dir=receipt_dir)
    if r["mode"] == "dry-run":
        print(f"dry-run: planned={r['counts']['planned']} "
              f"untouched={r['counts']['untouched']} "
              f"blocking_review={r['counts']['blocking_review']}")
        for pl in r["planned_repairs"]:
            print(f"  PLAN repair {pl['rel']} "
                  f"(ids: {', '.join(pl['dup_ids'])})")
    else:
        print(f"apply: touched={r['counts']['touched']} "
              f"skipped={r['counts']['skipped']} "
              f"refused={r['counts']['refused']} "
              f"blocking_review={r['counts']['blocking_review']}")
        for t in r["touched"]:
            print(f"  REPAIRED {t['rel']} "
                  f"(ids: {', '.join(t['ids_repaired'])})")
        for f in r["refused"]:
            print(f"  REFUSED {f['rel']} — {f['reason']}")
    for b in r["blocking_review"]:
        print(f"  BLOCKING {b['rel']} — {b['issue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
