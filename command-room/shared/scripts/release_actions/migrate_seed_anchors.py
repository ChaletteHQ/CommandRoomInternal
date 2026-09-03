#!/usr/bin/env python3
"""MIGRATE1 — production workspace migration: seed the five question-section
anchors into every safe PROJECT_BRAIN.md, EOF-append-only, dry-run by default.

This is the hardened production port of the sandbox-validated R4 prototype
(SPEC_MIGRATE1_workspace_anchor_migration). The additive contract it inherits
was proven on a full workspace mirror: seeds are pure HTML-comment marker
pairs appended at EOF (never inside or between hand lines), invisible in
rendered markdown until a coverage-gated `go` renders content into an anchor.

The eight MANDATORY fixes from the adversarial retest, all implemented here:

  1. Classify-time hash pin, re-checked at apply. Every planned file carries
     the sha256 of the bytes classification saw. Apply re-reads and REFUSES on
     drift, and the atomic write re-verifies the on-disk hash immediately
     before the rename (closing both mid-flight-mutation windows, S1a/S1b,
     and the stale-sync-swap, S1c).
  2. Rollback diff-guard. Rollback restores a file only when current bytes ==
     backup bytes + stamped seed blocks (+ whitespace). Any other diff —
     e.g. a post-migration client edit — is REFUSED and flagged; a backup
     missing for a journaled seed is reported loudly (S7).
  3. Stamped-vs-hand anchor interiors. A seeded anchor carries a
     `seeded_by=migrate1` stamp and a comment-only interior. Anchors present
     WITHOUT machine provenance (no stamp, no render attrs), or stamped
     anchors that grew non-comment content, are hand material: the file is
     flagged for review, never treated as "already anchored" (S3).
     Stamped/rendered-partial sets are safely EXTENDED with the missing ids
     (which is also how a future 6th id rolls out, S9); unstamped-partial
     refuses.
  4. Fence-aware marker scan. Markers inside ``` fenced code blocks or inline
     backtick spans are quoted text, not anchors — they neither satisfy
     "already anchored" nor poison the file as malformed (S4). A file whose
     only markers sit inside an UNTERMINATED fence is ambiguous and goes to
     review instead of risking a duplicate-id seed.
  5. Long-path safety. Every open/copy/stat goes through an extended-length
     (\\\\?\\) path on win32 (S8).
  6. Crash-safe per-file journal + writability preflight + attr-clean
     backups. Each file's outcome is fsync-appended to a journal line the
     moment it lands, so a mid-batch crash never loses the audit trail; a
     read-only file is a recorded skip, not a batch-killer; backups are
     written fresh (never copy2), so a read-only attribute is never
     propagated; receipts merge journal history so pre-crash seeds are
     always accounted for (S6).
  7. Conflict-copy discovery. Discovery globs PROJECT_BRAIN* (not the exact
     name): `(conflicted copy)` / `-DESKTOP-XYZ` file siblings are BLOCKING
     review items (the canonical file next to one is NOT seeded — the
     sibling's divergent content needs a human ruling first), and dual-folder
     twins are seeded-with-flag, carried into the apply receipt (S2).
     TMPLFENCE1 refinements: a *TEMPLATE* file (case-insensitive, mirroring
     cleanup_actions._iter_session_notes) is scaffolding — never a
     conflict-copy sibling, never a migration target; it lands in the receipt
     as an untouched row with reason "template — not a brain" so discovery
     stays honest. And a conflict-copy variant in a folder with NO canonical
     PROJECT_BRAIN.md gets an honest orphan message instead of claiming a
     sibling that does not exist.
  8. Robust exception paths. No unbound locals: an unreadable file is a
     recorded skip with the error text; UTF-16 content is an explicit review
     lane, not a silent permanent skip; `.backups/`, `session-notes-archive/`
     and `_archive`/`_backups` trees are fenced out of discovery.

Marker grammar: reused from `render_brain_block` via import — `_markers(id)`
compiles the per-id start/end patterns and `read_block_meta` parses stamp
attributes. The one generalization made here (an any-id scan for malformed /
unknown-id detection) mirrors that grammar and is asserted against it at plan
time by `_grammar_selfcheck` — the composed seed blocks must parse under the
real renderer's own regexes, or the migration refuses to plan.

Receipt: JSON, written even on partial failure; its centerpiece is the
`untouched` list — everything discovered that this run did NOT write, each
with a reason.

Usage:
    python migrate_seed_anchors.py <workspace_root> [--apply] [--rollback]
        [--receipt-dir DIR] [--ids id1,id2,...]

Dry-run is the default; nothing is written to any brain without --apply.
Rollback (--rollback) restores *.pre-migrate1 backups under the diff-guard.

Bridge entry (MIGRATE2): `run_bridge_migration(workspace_root, *, apply,
answers=None)` — the update-bridge's `apply_workspace_migration` action calls
this through shared/scripts/bridge_migration_runner.py. Same plan/apply
machinery, plus the one-question candor gate before the first seed and a
structured result the bridge narrates. DORMANT until a manifest names it.

stdlib + shared/scripts (atomic_write, render_brain_block, witnessed_write).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parent.parent))  # shared/scripts on path

import render_brain_block as rbb  # noqa: E402  — marker grammar authority
import witnessed_write as ww  # noqa: E402

# The five question-section anchor ids (Memory Program v2, R4).
QUESTION_BLOCK_IDS = [
    "where-things-stand",
    "what-we-decided",
    "whats-owed",
    "landmines-judgment",
    "how-we-work-this",
]

BRAIN_NAME = "PROJECT_BRAIN.md"
BACKUP_SUFFIX = ".pre-migrate1"
SEED_STAMP_KEY = "seeded_by"
SEED_STAMP_VALUE = "migrate1"
SEED_PLACEHOLDER = ("<!-- anchor seeded by MIGRATE1; rendered on first "
                    "coverage-gated go -->")
JOURNAL_NAME = "migrate1_journal.jsonl"

# Directory names fenced out of discovery entirely (fix 8). Prefix matches
# cover the dated recovery/cleanup archives.
EXCLUDED_DIR_NAMES = {"_archive", "_backups", ".backups",
                      "session-notes-archive", ".git", "node_modules"}
EXCLUDED_DIR_PREFIXES = ("_recovery_", "cleanup-")

# Any-id marker scan for malformed / unknown-id detection. This mirrors the
# grammar in render_brain_block._markers with the block id generalized;
# _grammar_selfcheck asserts the two agree for every id in play.
GEN_START_RE = re.compile(r"<!--\s*LIVE-STATE:([A-Za-z0-9_-]+)\b[^>]*-->",
                          re.IGNORECASE)
GEN_END_RE = re.compile(r"<!--\s*/LIVE-STATE:([A-Za-z0-9_-]+)\s*-->",
                        re.IGNORECASE)

_INLINE_CODE_RE = re.compile(r"``[^`]*``|`[^`\n]*`")
_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

_CONFLICT_HINT_RE = re.compile(
    r"conflict|DESKTOP-|LAPTOP-|\(\d+\)|copy", re.IGNORECASE)


# --------------------------------------------------------------------------
# Long-path + IO primitives (fix 5)
# --------------------------------------------------------------------------

def _lp(p) -> str:
    """Extended-length path form for win32; identity elsewhere."""
    s = str(p)
    if sys.platform != "win32":
        return s
    if s.startswith("\\\\?\\"):
        return s
    s = os.path.abspath(s)
    if s.startswith("\\\\"):
        return "\\\\?\\UNC" + s[1:]
    return "\\\\?\\" + s


def _read_bytes(path) -> bytes:
    with open(_lp(path), "rb") as fh:
        return fh.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exists(path) -> bool:
    return os.path.exists(_lp(path))


def _copy_backup(src, dst) -> None:
    """Backup copy that NEVER propagates a read-only attribute (fix 6):
    written fresh + fsynced, mtime preserved for forensics, then explicitly
    marked writable."""
    data = _read_bytes(src)
    with open(_lp(dst), "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        st = os.stat(_lp(src))
        os.utime(_lp(dst), (st.st_atime, st.st_mtime))
    except OSError:
        pass
    try:
        os.chmod(_lp(dst), stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _pinned_replace(path, new_text: str, pin_sha: str) -> None:
    """Atomic write of `new_text` that re-verifies, immediately before the
    rename, that the on-disk file still hashes to `pin_sha` (fix 1: the
    backup->write window). Raises ContentDriftError on drift; the temp file
    is removed and the target is untouched."""
    parent = os.path.dirname(_lp(path))
    fd, tmp = tempfile.mkstemp(prefix=".migrate1.tmp.", suffix=".write",
                               dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
        current = _sha256(_read_bytes(path))
        if current != pin_sha:
            raise ContentDriftError(
                f"on-disk content changed between backup and write "
                f"(pin {pin_sha[:12]}.. vs current {current[:12]}..)")
        os.replace(tmp, _lp(path))
        tmp = None
    finally:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


class ContentDriftError(Exception):
    """The file on disk no longer matches the classify-time hash pin."""


# --------------------------------------------------------------------------
# Fence-aware masking (fix 4)
# --------------------------------------------------------------------------

def mask_code_spans(text: str) -> tuple[str, bool]:
    """Return (masked_text, has_unterminated_fence). Fenced code blocks and
    inline backtick spans are replaced with spaces of identical length, so
    every offset in the masked text maps 1:1 onto the original."""
    out = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in text.splitlines(keepends=True):
        core = line.rstrip("\r\n")
        eol = line[len(core):]
        m = _FENCE_OPEN_RE.match(core)
        if in_fence:
            out.append(" " * len(core) + eol)
            if (m and m.group(1)[0] == fence_char
                    and len(m.group(1)) >= fence_len
                    and core.strip().rstrip(fence_char) == ""):
                in_fence = False
            continue
        if m:
            in_fence = True
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            out.append(" " * len(core) + eol)
            continue
        out.append(_INLINE_CODE_RE.sub(lambda mo: " " * len(mo.group(0)),
                                       core) + eol)
    return "".join(out), in_fence


# --------------------------------------------------------------------------
# Marker grammar (reused from render_brain_block via import)
# --------------------------------------------------------------------------

def seed_block(bid: str, ts: str) -> str:
    return (f"<!-- LIVE-STATE:{bid} {SEED_STAMP_KEY}={SEED_STAMP_VALUE} "
            f"seeded_at={ts} -->\n"
            f"{SEED_PLACEHOLDER}\n"
            f"<!-- /LIVE-STATE:{bid} -->")


def _grammar_selfcheck(ids: list[str]) -> None:
    """Assert the composed seed blocks and the generalized any-id scan both
    agree with render_brain_block's own compiled grammar. A drift here means
    the renderer would not accept what we seed — refuse to plan."""
    ts = "2026-01-01T00:00:00Z"
    for bid in ids:
        block = seed_block(bid, ts)
        s_re, e_re = rbb._markers(bid)
        sm, em = s_re.search(block), e_re.search(block)
        if not sm or not em or em.start() <= sm.end():
            raise AssertionError(
                f"seed block for '{bid}' does not parse under "
                f"render_brain_block's marker grammar")
        g = GEN_START_RE.search(block)
        ge = GEN_END_RE.search(block)
        if not g or g.group(1) != bid or not ge or ge.group(1) != bid:
            raise AssertionError(
                f"generalized marker scan disagrees with "
                f"render_brain_block grammar for '{bid}'")


def _scan_malformed(masked: str) -> list[str]:
    """Malformed-marker findings over the fence-masked text, any id."""
    starts = defaultdict(list)
    ends = defaultdict(list)
    for m in GEN_START_RE.finditer(masked):
        starts[m.group(1).lower()].append(m.start())
    for m in GEN_END_RE.finditer(masked):
        ends[m.group(1).lower()].append(m.start())
    problems = []
    for bid, ps in starts.items():
        es = ends.get(bid, [])
        if len(ps) > 1:
            problems.append(f"duplicate start marker id '{bid}' x{len(ps)}")
        if not es:
            problems.append(f"start-without-end for id '{bid}'")
        elif es[0] < ps[0]:
            problems.append(f"end-before-start for id '{bid}'")
        if len(es) > 1:
            problems.append(f"duplicate end marker id '{bid}' x{len(es)}")
    for bid in ends:
        if bid not in starts:
            problems.append(f"end-without-start for id '{bid}'")
    return problems


def _interior_is_placeholder(interior: str) -> bool:
    """True when the anchor interior carries no hand content: only blank
    lines and single-line HTML comments (the seed placeholder shape)."""
    for line in interior.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        return False
    return True


def anchor_state(text: str, masked: str, bid: str) -> dict:
    """Per-id anchor classification (fix 3), scanned fence-aware (fix 4).

    States:
      absent        no marker pair for this id outside code spans
      stamped       seeded_by-stamped marker, placeholder-only interior —
                    machine-provenance, safe, extendable
      rendered      marker carries render attrs (generated_at/source_seq) —
                    machine-provenance, already a real anchor
      hand          marker with no machine provenance (hand-pasted set)
      hand-content  stamped marker whose interior grew non-comment content
      malformed     unpaired/duplicated markers for this id
    """
    s_re, e_re = rbb._markers(bid)
    sm = list(s_re.finditer(masked))
    em = list(e_re.finditer(masked))
    if not sm and not em:
        return {"state": "absent"}
    if len(sm) != 1 or len(em) != 1 or em[0].start() <= sm[0].end():
        return {"state": "malformed"}
    tag = text[sm[0].start():sm[0].end()]
    interior = text[sm[0].end():em[0].start()]
    stamped = f"{SEED_STAMP_KEY}=" in tag
    rendered = ("source_seq=" in tag) or ("generated_at=" in tag)
    if stamped and not _interior_is_placeholder(interior):
        return {"state": "hand-content"}
    if stamped:
        return {"state": "stamped"}
    if rendered:
        return {"state": "rendered"}
    return {"state": "hand"}


# --------------------------------------------------------------------------
# Discovery (fix 7 + fix 8 fences)
# --------------------------------------------------------------------------

def _rel_posix(root, path) -> str:
    r = os.path.relpath(str(path).replace("\\\\?\\", ""),
                        str(root).replace("\\\\?\\", ""))
    return str(PurePosixPath(*Path(r).parts))


def _is_template_name(name: str) -> bool:
    """Naming fence (TMPLFENCE1), mirroring the TEMPLATE skip in
    cleanup_actions._iter_session_notes: a file whose name says TEMPLATE
    (case-insensitive) is scaffolding — never a brain, never a conflict-copy
    sibling, never a migration target."""
    return "TEMPLATE" in name.upper()


def discover(root) -> dict:
    """Walk `root` for PROJECT_BRAIN* files (long-path-safe), fencing out
    archive/backup trees. Returns:
      canonical:  rel paths of files named exactly PROJECT_BRAIN.md
      conflicts:  rel paths of sibling variants (conflicted copies,
                  -DESKTOP-XYZ, numbered copies, any other PROJECT_BRAIN*.md)
      templates:  rel paths of *TEMPLATE* files (naming fence, TMPLFENCE1) —
                  receipted, never seeded, never a conflict
      dual_folder_pairs: folder-level twins among canonical brain dirs
    """
    root = Path(root)
    canonical, conflicts, templates = [], [], []
    for dirpath, dirnames, filenames in os.walk(_lp(root)):
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDED_DIR_NAMES
            and not any(d.startswith(p) for p in EXCLUDED_DIR_PREFIXES)
        ]
        for name in filenames:
            if not name.startswith("PROJECT_BRAIN"):
                continue
            if name.endswith(BACKUP_SUFFIX) or name.endswith(
                    ww.WITNESS_SUFFIX):
                continue  # our own artifacts
            rel = _rel_posix(root, os.path.join(dirpath, name))
            if _is_template_name(name):
                templates.append(rel)  # TMPLFENCE1: fenced, receipted
            elif name == BRAIN_NAME:
                canonical.append(rel)
            elif name.lower().endswith(".md"):
                conflicts.append(rel)
    canonical.sort()
    conflicts.sort()
    templates.sort()
    return {
        "canonical": canonical,
        "conflicts": conflicts,
        "templates": templates,
        "dual_folder_pairs": _find_dual_pairs(
            [str(PurePosixPath(c).parent) for c in canonical]),
    }


def _norm_tokens(name: str) -> frozenset:
    toks = set(re.findall(r"[a-z0-9]+", name.lower()))
    return frozenset(toks - {"the", "of", "and"})


def _is_ancestor(a: str, b: str) -> bool:
    pa, pb = PurePosixPath(a).parts, PurePosixPath(b).parts
    return pa == pb[:len(pa)] or pb == pa[:len(pb)]


def _find_dual_pairs(active_dirs: list[str]) -> list[dict]:
    """Folder-level twins among active brain dirs: exact basename-token
    twins, plus fuzzy full-path token containment (needs a human eyeball).
    Ported from the validated prototype classifier."""
    pairs = []
    act = [(d, _norm_tokens(PurePosixPath(d).name),
            _norm_tokens(" ".join(PurePosixPath(d).parts)))
           for d in active_dirs]
    for i in range(len(act)):
        for j in range(i + 1, len(act)):
            d1, b1, f1 = act[i]
            d2, b2, f2 = act[j]
            if _is_ancestor(d1, d2):
                continue
            if b1 == b2:
                pairs.append({"kind": "exact-active-twin", "a": d1, "b": d2})
                continue
            small, big = (f1, f2) if len(f1) <= len(f2) else (f2, f1)
            if len(small) >= 2 and small <= big:
                pairs.append({"kind": "fuzzy-name-containment",
                              "a": d1, "b": d2})
    return pairs


# --------------------------------------------------------------------------
# Classification (fixes 1, 3, 4, 5, 8)
# --------------------------------------------------------------------------

def _looks_utf16(raw: bytes) -> bool:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return True
    return len(raw) > 1 and raw.count(b"\x00") > len(raw) // 4


def classify_file(root, rel: str, ids: list[str]) -> dict:
    """Classify one canonical brain. Never raises for a bad file — every
    failure mode is a recorded verdict (fix 8: no unbound locals; the read
    happens first and any OSError is the whole answer)."""
    p = Path(root) / rel
    rec = {"rel": rel}
    try:
        raw = _read_bytes(p)
    except OSError as exc:
        rec.update({"verdict": "unreadable",
                    "reason": f"unreadable: {exc}"})
        return rec
    rec["size_bytes"] = len(raw)
    rec["sha256"] = _sha256(raw)  # fix 1: the classify-time hash pin

    if not raw.strip():
        rec.update({"verdict": "review-empty",
                    "reason": "empty file — treated as client-authored, "
                              "needs review before any seed"})
        return rec
    if _looks_utf16(raw):
        rec.update({"verdict": "review-utf16",
                    "reason": "utf16-encoded content — explicit review lane, "
                              "not a silent skip (fix 8)"})
        return rec
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rec.update({"verdict": "review-undecodable",
                    "reason": f"not valid utf-8 ({exc}) — review lane"})
        return rec

    masked, unterminated = mask_code_spans(text)
    rec["unterminated_fence"] = unterminated
    if unterminated and "LIVE-STATE:" in text and "LIVE-STATE:" not in masked:
        rec.update({"verdict": "review-unterminated-fence",
                    "reason": "LIVE-STATE markers exist only inside an "
                              "unterminated code fence — ambiguous; review "
                              "before seeding (fix 4 guard)"})
        return rec

    malformed = _scan_malformed(masked)
    if malformed:
        rec.update({"verdict": "review-malformed",
                    "reason": "malformed markers outside code spans: "
                              + "; ".join(malformed),
                    "malformed": malformed})
        return rec

    states = {bid: anchor_state(text, masked, bid)["state"] for bid in ids}
    rec["anchor_states"] = states
    quoted = [bid for bid in ids
              if states[bid] == "absent"
              and re.search(r"LIVE-STATE:" + re.escape(bid), text,
                            re.IGNORECASE)
              and not re.search(r"LIVE-STATE:" + re.escape(bid), masked,
                                re.IGNORECASE)]
    if quoted:
        rec["quoted_marker_ids"] = quoted  # informational (renderer-side
        # fence-awareness is a separate, pending renderer change)

    hand = [b for b, s in states.items() if s in ("hand", "hand-content")]
    present = [b for b, s in states.items() if s in ("stamped", "rendered")]
    missing = [b for b in ids if states[b] == "absent"]

    if hand:
        rec.update({"verdict": "review-hand-anchors",
                    "reason": "anchor interiors without machine provenance "
                              f"({', '.join(sorted(hand))}) — hand content; "
                              "flagged for review, never a no-op (fix 3)"})
    elif not missing:
        rec.update({"verdict": "already-anchored",
                    "reason": "all anchor ids present with machine "
                              "provenance (idempotency no-op)"})
    elif present:
        rec.update({"verdict": "seed-extend", "missing_ids": missing,
                    "reason": "stamped/rendered-partial anchor set — safe "
                              f"to extend with: {', '.join(missing)}"})
    else:
        rec.update({"verdict": "seed", "missing_ids": missing,
                    "reason": "no question-section anchors present — full "
                              "seed"})
    return rec


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def build_plan(root, ids: list[str] | None = None) -> dict:
    ids = list(ids) if ids else list(QUESTION_BLOCK_IDS)
    _grammar_selfcheck(ids)
    disc = discover(root)

    conflict_dirs = {str(PurePosixPath(c).parent) for c in disc["conflicts"]}
    dual_dirs = set()
    for pr in disc["dual_folder_pairs"]:
        dual_dirs.add(pr["a"])
        dual_dirs.add(pr["b"])

    entries = []
    for rel in disc["canonical"]:
        rec = classify_file(root, rel, ids)
        parent = str(PurePosixPath(rel).parent)
        rec["dual_folder_pair"] = parent in dual_dirs
        if parent in conflict_dirs and rec["verdict"] in ("seed",
                                                          "seed-extend"):
            # Fix 7: a conflict-copy sibling's divergent content is invisible
            # to every receipt — BLOCKING until a human reconciles it.
            rec["verdict"] = "review-conflict-sibling"
            rec["reason"] = ("conflict-copy sibling(s) present in this "
                            "folder — blocking review item; not seeded")
        entries.append(rec)

    # TMPLFENCE1: the sibling message is only honest when the folder really
    # holds a canonical PROJECT_BRAIN.md — an orphan variant gets its own.
    canonical_dirs = {str(PurePosixPath(c).parent) for c in disc["canonical"]}
    blocking = []
    for c in disc["conflicts"]:
        if str(PurePosixPath(c).parent) in canonical_dirs:
            issue = ("conflict-copy sibling of a PROJECT_BRAIN.md — "
                     "divergent content needs a human ruling")
        else:
            issue = ("conflict-copy variant with no canonical "
                     "PROJECT_BRAIN.md in its folder — orphan; it may itself "
                     "be the surviving brain; needs a human ruling")
        blocking.append({"rel": c, "issue": issue})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "question_block_ids": ids,
        "entries": entries,
        "blocking_review": blocking,
        "templates": disc["templates"],
        "dual_folder_pairs": disc["dual_folder_pairs"],
    }


# --------------------------------------------------------------------------
# Journal (fix 6)
# --------------------------------------------------------------------------

def _journal_path(receipt_dir) -> Path:
    return Path(receipt_dir) / JOURNAL_NAME


def _journal_append(receipt_dir, rec: dict) -> None:
    """Fsynced O_APPEND of one JSON line — lands the moment the file does,
    survives a crash of the rest of the batch."""
    jp = _journal_path(receipt_dir)
    os.makedirs(_lp(jp.parent), exist_ok=True)
    fd = os.open(_lp(jp), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_journal(receipt_dir) -> list[dict]:
    """Defensive journal read — a torn trailing line (crash mid-append) is
    skipped, never fatal."""
    jp = _journal_path(receipt_dir)
    out = []
    try:
        raw = _read_bytes(jp).decode("utf-8", errors="replace")
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
# Apply (fixes 1, 5, 6, 8)
# --------------------------------------------------------------------------

def apply_plan(root, plan: dict, receipt_dir) -> dict:
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    touched, skipped, refused = [], [], []

    for entry in plan["entries"]:
        rel = entry["rel"]
        verdict = entry["verdict"]
        if verdict not in ("seed", "seed-extend"):
            skipped.append({"rel": rel, "reason": entry["reason"],
                            "verdict": verdict})
            continue
        try:
            outcome = _apply_one(root, entry, ts, run_id, receipt_dir)
        except Exception as exc:  # fix 8: one bad file never kills the batch
            outcome = {"rel": rel, "action": "error-skip",
                       "reason": f"{type(exc).__name__}: {exc}"}
            _journal_append(receipt_dir, {"run_id": run_id, "ts": ts,
                                          **outcome})
        if outcome["action"] == "seeded":
            touched.append(outcome)
        elif outcome["action"].startswith("refused"):
            refused.append(outcome)
        else:
            skipped.append({"rel": rel, "reason": outcome["reason"],
                            "verdict": outcome["action"]})

    receipt = _build_receipt("apply", plan, receipt_dir, run_id,
                             touched=touched, skipped=skipped,
                             refused=refused)
    _write_receipt(receipt_dir, "migrate1_receipt_apply.json", receipt)
    return receipt


def _apply_one(root, entry: dict, ts: str, run_id: str,
               receipt_dir) -> dict:
    rel = entry["rel"]
    p = Path(root) / rel
    pin = entry["sha256"]
    missing = entry["missing_ids"]

    # (fix 6) writability preflight — a read-only file is a recorded skip.
    if not os.access(_lp(p), os.W_OK):
        outcome = {"rel": rel, "action": "skip-not-writable",
                   "reason": "file is not writable (read-only attribute?) — "
                             "skipped; clear the attribute and re-run"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # (fix 1) re-read + hash pin check — refuse on any drift since classify.
    raw2 = _read_bytes(p)
    if _sha256(raw2) != pin:
        outcome = {"rel": rel, "action": "refused-content-drift",
                   "reason": "content changed since classification (hash pin "
                             "mismatch) — refused; re-run to re-classify"}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # Backup (first run only; never clobbered), attr-clean (fix 6).
    bak = str(p) + BACKUP_SUFFIX
    backup_created = False
    if not _exists(bak):
        _copy_backup(p, bak)
        backup_created = True

    before = raw2.decode("utf-8")
    blocks = "\n\n".join(seed_block(b, ts) for b in missing)
    sep = "" if before.endswith("\n") or before == "" else "\n"
    after = f"{before}{sep}\n{blocks}\n"
    if not after.startswith(before):
        raise AssertionError(f"additive invariant violated for {rel}")

    # (fix 1) atomic write with an immediately-pre-rename hash re-verify —
    # a mutation landing in the backup->write window is refused, not lost.
    try:
        _pinned_replace(p, after, pin)
    except ContentDriftError as exc:
        outcome = {"rel": rel, "action": "refused-write-window-drift",
                   "reason": f"{exc} — refused; the on-disk content (with "
                             "the concurrent change) is untouched",
                   "backup": rel + BACKUP_SUFFIX if _exists(bak) else None}
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
        return outcome

    # (review F3) the seed has LANDED (os.replace done) — a witness or
    # journal failure past this point must not demote the file to an
    # error-skip, or the receipt's untouched list lies about a file that was
    # in fact seeded. The outcome stays action="seeded" (three consumers key
    # on that action) and carries a `warning` field instead.
    warning = None
    try:
        ww.write_witness(p, data=after.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        warning = f"witness write failed after the seed landed: {exc}"
        sys.stderr.write(f"[migrate1] WARNING {rel}: {warning}\n")
    outcome = {"rel": rel, "action": "seeded", "ids_added": missing,
               "backup": rel + BACKUP_SUFFIX,
               "backup_created_this_run": backup_created,
               "sha_before": pin, "sha_after": _sha256(after.encode("utf-8")),
               "dual_folder_pair": entry.get("dual_folder_pair", False)}
    if warning:
        outcome["warning"] = warning
    try:
        _journal_append(receipt_dir, {"run_id": run_id, "ts": ts, **outcome})
    except Exception as exc:  # noqa: BLE001
        outcome["warning"] = ((outcome.get("warning") or "")
                              + f" | journal append failed after the seed "
                                f"landed: {exc}").lstrip(" |")
        sys.stderr.write(f"[migrate1] WARNING {rel}: journal append failed "
                         f"after the seed landed: {exc}\n")
    return outcome


# --------------------------------------------------------------------------
# Receipts
# --------------------------------------------------------------------------

def _build_receipt(mode: str, plan: dict, receipt_dir, run_id: str | None,
                   touched=None, skipped=None, refused=None) -> dict:
    touched = touched or []
    skipped = skipped or []
    refused = refused or []
    touched_rels = {t["rel"] for t in touched}
    # For a refused / skipped-at-apply file the runtime outcome is the truer
    # reason than the plan verdict — prefer it in the untouched list.
    outcome_by_rel = {}
    for o in list(refused) + list(skipped):
        outcome_by_rel.setdefault(o["rel"], o)
    untouched = []
    for entry in plan["entries"]:
        if entry["rel"] not in touched_rels:
            o = outcome_by_rel.get(entry["rel"])
            untouched.append({
                "rel": entry["rel"],
                "reason": (o or entry).get(
                    "reason", entry.get("reason", entry["verdict"])),
                "verdict": (o.get("verdict") or o.get("action")
                            if o else entry["verdict"]) or entry["verdict"],
            })
    for item in plan["blocking_review"]:
        untouched.append({"rel": item["rel"], "reason": item["issue"],
                          "verdict": "blocking-review"})
    # TMPLFENCE1: templates stay in the receipt so discovery is honest —
    # an untouched row, never a blocker, never a seed target.
    for trel in plan.get("templates", []):
        untouched.append({"rel": trel,
                          "reason": "template — not a brain; naming fence "
                                    "(*TEMPLATE*), never seeded",
                          "verdict": "template"})

    # (fix 6) merge journal history so pre-crash / prior-run seeds are never
    # missing from the audit chain.
    history = [j for j in read_journal(receipt_dir)
               if j.get("action") == "seeded"
               and j.get("rel") not in touched_rels
               and (run_id is None or j.get("run_id") != run_id)]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "run_id": run_id,
        "root": plan["root"],
        "question_block_ids": plan["question_block_ids"],
        "seed_stamp": f"{SEED_STAMP_KEY}={SEED_STAMP_VALUE}",
        "counts": {
            "discovered": (len(plan["entries"]) + len(plan["blocking_review"])
                           + len(plan.get("templates", []))),
            "touched": len(touched),
            "skipped": len(skipped),
            "refused": len(refused),
            "untouched": len(untouched),
            "blocking_review": len(plan["blocking_review"]),
            "templates": len(plan.get("templates", [])),
        },
        "touched": touched,
        "skipped": skipped,
        "refused": refused,
        "blocking_review": plan["blocking_review"],
        "templates": plan.get("templates", []),
        "dual_folder_pairs": plan["dual_folder_pairs"],
        "previously_seeded": history,
        # The centerpiece: everything this run did NOT write, with reasons.
        "untouched": untouched,
    }


def _write_receipt(receipt_dir, name: str, receipt: dict) -> None:
    os.makedirs(_lp(receipt_dir), exist_ok=True)
    from atomic_write import atomic_write_json
    atomic_write_json(Path(_lp(Path(receipt_dir) / name)), receipt)


# --------------------------------------------------------------------------
# Rollback (fix 2)
# --------------------------------------------------------------------------

def _seed_only_remainder(remainder: str) -> bool:
    """True iff `remainder` consists solely of stamped seed blocks and
    whitespace — the only thing rollback is allowed to discard."""
    block_re = re.compile(
        r"<!--\s*LIVE-STATE:[A-Za-z0-9_-]+\s[^>]*"
        + re.escape(f"{SEED_STAMP_KEY}={SEED_STAMP_VALUE}")
        + r"[^>]*-->\s*(?:<!--(?:[^-]|-(?!->))*-->\s*)*"
        r"<!--\s*/LIVE-STATE:[A-Za-z0-9_-]+\s*-->",
        re.IGNORECASE)
    stripped = block_re.sub("", remainder)
    return stripped.strip() == ""


def rollback(root, receipt_dir) -> dict:
    """Restore *.pre-migrate1 backups under the diff-guard (fix 2):
      - current == backup + stamped seeds (+ whitespace) -> restore, drop
        the backup, re-witness the restored bytes;
      - anything else (post-migration edits, unknown divergence) -> REFUSE
        and flag — rollback must never be worse than no rollback;
      - a journaled seed whose backup is gone -> reported LOUDLY.
    Idempotent: a second rollback finds nothing to restore and no new
    refusals for already-restored files."""
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    run_id = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    restored, refused, missing_backup = [], [], []

    backups = []
    for dirpath, dirnames, filenames in os.walk(_lp(root)):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIR_NAMES]
        for name in filenames:
            if name.endswith(BACKUP_SUFFIX):
                backups.append(os.path.join(dirpath, name))

    seen_rels = set()
    for bak in sorted(backups):
        orig = bak[:-len(BACKUP_SUFFIX)]
        rel = _rel_posix(root, orig)
        seen_rels.add(rel)
        try:
            bak_bytes = _read_bytes(bak)
            cur_bytes = _read_bytes(orig) if _exists(orig) else None
        except OSError as exc:
            refused.append({"rel": rel, "issue": f"unreadable: {exc}"})
            continue
        if cur_bytes is None:
            refused.append({"rel": rel,
                            "issue": "original file missing next to its "
                                     "backup — refusing to guess"})
            continue
        if cur_bytes == bak_bytes:
            _remove_file(bak)
            restored.append({"rel": rel, "how": "already-equal"})
            continue
        if cur_bytes.startswith(bak_bytes) and _seed_only_remainder(
                cur_bytes[len(bak_bytes):].decode("utf-8",
                                                  errors="replace")):
            # (review F2) the guard-read -> restore-write window: the restore
            # goes through the SAME pinned primitive apply uses, pinned to the
            # exact bytes the diff-guard just approved. A concurrent edit
            # landing between the guard read and the rename hashes different,
            # the replace REFUSES, the edit survives, and the backup stays in
            # place for the next attempt — never a silent overwrite.
            try:
                _pinned_replace(orig, bak_bytes.decode("utf-8"),
                                _sha256(cur_bytes))
            except ContentDriftError as exc:
                refused.append({
                    "rel": rel,
                    "issue": f"content changed between the rollback guard "
                             f"read and the restore write ({exc}); REFUSED "
                             f"— the concurrent edit is untouched and the "
                             f"backup is kept. Re-run rollback."})
                continue
            ww.write_witness(orig, data=bak_bytes)
            _remove_file(bak)
            restored.append({"rel": rel, "how": "seeds-stripped"})
        else:
            refused.append({"rel": rel,
                            "issue": "current content != backup + seeds — "
                                     "post-migration edits present; REFUSED "
                                     "(restore would destroy them). Backup "
                                     "kept in place; reconcile by hand."})

    # (fix 2) journaled seeds whose backup vanished — say it loudly. A rel
    # with a later journaled rollback already had its backup consumed by a
    # verified restore, so only never-rolled-back seeds count.
    journal = read_journal(receipt_dir)
    rolled_back_rels = {j.get("rel") for j in journal
                        if j.get("action") == "rolled-back"}
    rolled_back_rels.update(r["rel"] for r in restored)
    for j in journal:
        if j.get("action") != "seeded":
            continue
        rel = j.get("rel")
        if rel in seen_rels or rel in rolled_back_rels or not rel:
            continue
        orig = Path(root) / rel
        bak = str(orig) + BACKUP_SUFFIX
        if not _exists(bak):
            try:
                still_seeded = _exists(orig) and (
                    f"{SEED_STAMP_KEY}={SEED_STAMP_VALUE}".encode()
                    in _read_bytes(orig))
            except OSError:
                still_seeded = None
            item = {"rel": rel,
                    "issue": "MISSING BACKUP for a journaled seed — this "
                             "file cannot be rolled back mechanically",
                    "still_carries_seeds": still_seeded}
            if item not in missing_backup:
                missing_backup.append(item)
                sys.stderr.write(
                    f"[migrate1 rollback] MISSING BACKUP: {rel} — journal "
                    f"says it was seeded but no {BACKUP_SUFFIX} file exists; "
                    f"mixed state, needs a human.\n")

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
    _write_receipt(receipt_dir, "migrate1_receipt_rollback.json", receipt)
    return receipt


def _remove_file(path) -> None:
    try:
        os.chmod(_lp(path), stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    os.unlink(_lp(path))


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def run_migration(root, *, apply: bool = False, ids: list[str] | None = None,
                  receipt_dir=None) -> dict:
    """Dry-run by default: classify + plan + receipt, zero writes to any
    brain. `apply=True` executes the plan under all eight fixes."""
    root = Path(root)
    receipt_dir = Path(receipt_dir) if receipt_dir else (
        root / "_hq" / "data" / "migrate1")
    plan = build_plan(root, ids)
    if apply:
        return apply_plan(root, plan, receipt_dir)
    planned = [{"rel": e["rel"], "verdict": e["verdict"],
                "missing_ids": e.get("missing_ids", []),
                "reason": e["reason"]}
               for e in plan["entries"]
               if e["verdict"] in ("seed", "seed-extend")]
    receipt = _build_receipt("dry-run", plan, receipt_dir, None)
    receipt["planned_seeds"] = planned
    receipt["counts"]["planned"] = len(planned)
    _write_receipt(receipt_dir, "migrate1_receipt_dryrun.json", receipt)
    return receipt


# --------------------------------------------------------------------------
# Bridge entry (MIGRATE2) — the update-bridge's `apply_workspace_migration`
# --------------------------------------------------------------------------

# The candor knob GORENDER1 renders under (operator ruling 2026-08-31). The
# bridge asks this ONE question before the first seed on a workspace that has
# never answered it; the operator's own workspace already carries the knob, so
# no question fires there. Values mirror render_brain_anchors.CANDOR_VALUES —
# imported at call time so the two cannot drift apart silently.
CANDOR_SKILL = "brain_render"
CANDOR_KEY = "brain_candor"
CANDOR_DEFAULT = "full"

CANDOR_QUESTION = {
    "skill": CANDOR_SKILL,
    "key": CANDOR_KEY,
    "default": CANDOR_DEFAULT,
    "prompt": (
        "One quick setting before I add memory sections to your project "
        "files. The judgment section can hold candid notes about people — "
        "who is slow to respond, where trust is thin — or stick to how the "
        "work itself is going. These files stay private to your workspace, "
        "so candid is the default; say process only if you would rather "
        "keep people out of it."
    ),
    "options": {
        "full": "candid — people and process, the full record",
        "process-only": "process only — how the work is going, no notes "
                        "about people",
    },
    "accepts": {
        "full": ("full", "candid", "full candor", "default", "yes",
                 "people and process"),
        "process-only": ("process-only", "process only", "process",
                         "no people", "keep people out"),
    },
}


def parse_candor_answer(text: str | None) -> str | None:
    """Map a plain reply onto a candor value; None when it does not parse.
    Fail closed: an unrecognised reply is NOT a default — the caller asks
    again rather than seeding under a value the customer did not choose."""
    if text is None:
        return None
    t = " ".join(str(text).strip().lower().replace("_", "-").split())
    for value, spellings in CANDOR_QUESTION["accepts"].items():
        if t == value or t in spellings:
            return value
    return None


def _candor_state(root) -> dict:
    """{"set": bool, "value": str|None, "invalid": str|None} — read straight
    from the skill_config store, never from a cached module default."""
    from skill_config_writer import load_skill_config
    saved = load_skill_config(root, CANDOR_SKILL)
    cfg = (saved or {}).get("config") if isinstance(saved, dict) else None
    if not isinstance(cfg, dict) or CANDOR_KEY not in cfg:
        return {"set": False, "value": None, "invalid": None}
    raw = cfg.get(CANDOR_KEY)
    try:
        from render_brain_anchors import CANDOR_VALUES
    except Exception:  # noqa: BLE001 — renderer absent: fall back to the pair
        CANDOR_VALUES = ("full", "process-only")
    if raw in CANDOR_VALUES:
        return {"set": True, "value": raw, "invalid": None}
    # Present-but-invalid: GORENDER1 fails this closed to process-only at
    # render time; here it counts as SET (the knob exists) so the bridge does
    # not re-ask over a value someone deliberately wrote.
    return {"set": True, "value": "process-only", "invalid": str(raw)}


def _record_candor(root, value: str) -> None:
    from skill_config_writer import save_skill_config
    origin = ("first_fire_defaults" if value == CANDOR_DEFAULT
              else "first_fire_override")
    save_skill_config(root, CANDOR_SKILL, {CANDOR_KEY: value},
                      origin=origin)


def _next_line(seeded: int, planned: int) -> str:
    """The honest what-happens-next sentence. Nothing is visible in a
    seeded file until a `go` on a project with enough history renders into
    the sections — say so, every time."""
    n = seeded if seeded else planned
    files = "project file" if n == 1 else "project files"
    return (f"{n} {files} now {'carries' if n == 1 else 'carry'} five empty "
            "memory sections at the very end. They stay invisible until you "
            "open a project that has enough history behind it — the first "
            "time that happens the sections fill in, and until then nothing "
            "you see changes.")


def undo_command(root) -> str:
    """The exact rollback invocation for THIS workspace — restores every
    seeded file from its backup under the diff-guard (a file edited since
    the seed is refused, never overwritten)."""
    return (f'python3 "{_HERE}" "{Path(root)}" --rollback')


def run_bridge_migration(workspace_root, *, apply: bool,
                         answers: dict | None = None,
                         ids: list[str] | None = None) -> dict:
    """The update-bridge's entry (MIGRATE2). Adapts the CLI flow into one
    structured result the bridge can narrate, and owns two gates the CLI
    leaves to the operator:

      * dry-run first, always — `apply=False` plans and writes nothing;
      * the candor question — with `apply=True`, a workspace whose
        `brain_candor` knob is unset REFUSES to seed until `answers` carries
        the customer's choice (`{"brain_candor": "full"|"process-only"}`);
        the answer is written to the skill_config store BEFORE the first seed
        so the first render already honours it. A workspace with the knob
        set (the operator's own) is never asked.

    Returns (every key always present):
      status        planned | needs_answer | blocked | noop | applied | error
      ran           True iff bytes were written to any brain
      counts        planned / seeded / refused / blocking / untouched /
                    discovered / templates
      blocking_rows [{"rel", "issue"}] — the rows that stop an apply
      refused_rows  [{"rel", "reason"}] — apply-time hash-pin refusals
      seeded_rels   files this call wrote
      question      the candor question spec when status == needs_answer
      candor        {"set", "value", "invalid", "recorded_this_run"}
      next_line     the honest what-happens-next sentence
      undo_command  the rollback invocation for this workspace
      receipt_dir / receipt_path / journal_path
      error         str | None
    """
    root = Path(workspace_root)
    receipt_dir = root / "_hq" / "data" / "migrate1"
    answers = dict(answers or {})
    out = {
        "action": "apply_workspace_migration",
        "migration": "migrate_seed_anchors",
        "status": "planned", "ran": False,
        "counts": {"planned": 0, "seeded": 0, "refused": 0, "blocking": 0,
                   "untouched": 0, "discovered": 0, "templates": 0},
        "blocking_rows": [], "refused_rows": [], "seeded_rels": [],
        "question": None,
        "candor": {"set": False, "value": None, "invalid": None,
                   "recorded_this_run": False},
        "next_line": "", "undo_command": undo_command(root),
        "receipt_dir": str(receipt_dir), "receipt_path": None,
        "journal_path": str(_journal_path(receipt_dir)),
        "error": None,
    }
    try:
        out["candor"].update(_candor_state(root))

        # Dry-run: pure read of the brains, receipt written, zero seeds.
        dry = run_migration(root, apply=False, ids=ids,
                            receipt_dir=receipt_dir)
        c = dry["counts"]
        out["counts"].update({
            "planned": c["planned"], "blocking": c["blocking_review"],
            "untouched": c["untouched"], "discovered": c["discovered"],
            "templates": c["templates"]})
        out["blocking_rows"] = [{"rel": b["rel"], "issue": b["issue"]}
                                for b in dry["blocking_review"]]
        out["receipt_path"] = str(receipt_dir / "migrate1_receipt_dryrun.json")

        if out["counts"]["blocking"]:
            out["status"] = "blocked"          # never seeds past a blocker
            return out
        if out["counts"]["planned"] == 0:
            out["status"] = "noop"             # idempotent re-run
            return out
        out["next_line"] = _next_line(0, out["counts"]["planned"])
        if not apply:
            return out                          # status "planned"

        # The candor gate — ONE question, before the first seed, only when
        # the workspace has never answered it.
        if not out["candor"]["set"]:
            raw = answers.get(CANDOR_KEY)
            value = parse_candor_answer(raw) if raw is not None else None
            if value is None:
                out["status"] = "needs_answer"
                out["question"] = dict(CANDOR_QUESTION)
                if raw is not None:
                    out["error"] = (f"unrecognised candor answer {raw!r} — "
                                    "not seeding; ask again")
                return out
            _record_candor(root, value)
            out["candor"].update({"set": True, "value": value,
                                  "recorded_this_run": True})

        rec = run_migration(root, apply=True, ids=ids,
                            receipt_dir=receipt_dir)
        c = rec["counts"]
        out["counts"].update({
            "seeded": c["touched"], "refused": c["refused"],
            "blocking": c["blocking_review"], "untouched": c["untouched"],
            "discovered": c["discovered"], "templates": c["templates"]})
        out["seeded_rels"] = [t["rel"] for t in rec["touched"]]
        out["refused_rows"] = [{"rel": r["rel"], "reason": r["reason"]}
                               for r in rec["refused"]]
        out["receipt_path"] = str(receipt_dir / "migrate1_receipt_apply.json")
        out["ran"] = bool(rec["touched"])
        out["status"] = "applied"
        out["next_line"] = _next_line(out["counts"]["seeded"],
                                      out["counts"]["planned"])
        return out
    except Exception as exc:  # noqa: BLE001 — the bridge narrates, never dies
        out["status"] = "error"
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", help="workspace root to migrate")
    ap.add_argument("--apply", action="store_true",
                    help="actually write (default: dry-run)")
    ap.add_argument("--rollback", action="store_true",
                    help="restore .pre-migrate1 backups under the diff-guard")
    ap.add_argument("--receipt-dir", default=None,
                    help="where receipts + journal land "
                         "(default: <root>/_hq/data/migrate1)")
    ap.add_argument("--ids", default=None,
                    help="comma-separated anchor ids "
                         "(default: the five question-section ids)")
    args = ap.parse_args(argv)

    root = Path(args.root)
    receipt_dir = Path(args.receipt_dir) if args.receipt_dir else (
        root / "_hq" / "data" / "migrate1")
    ids = [s.strip() for s in args.ids.split(",")] if args.ids else None

    if args.rollback:
        r = rollback(root, receipt_dir)
        print(f"rollback: restored={r['counts']['restored']} "
              f"refused={r['counts']['refused']} "
              f"missing_backup={r['counts']['missing_backup']}")
        return 1 if (r["refused"] or r["missing_backup"]) else 0

    r = run_migration(root, apply=args.apply, ids=ids,
                      receipt_dir=receipt_dir)
    if r["mode"] == "dry-run":
        print(f"dry-run: planned={r['counts']['planned']} "
              f"untouched={r['counts']['untouched']} "
              f"blocking_review={r['counts']['blocking_review']}")
        for pl in r["planned_seeds"]:
            print(f"  PLAN {pl['verdict']} {pl['rel']} "
                  f"({len(pl['missing_ids'])} ids)")
    else:
        print(f"apply: touched={r['counts']['touched']} "
              f"skipped={r['counts']['skipped']} "
              f"refused={r['counts']['refused']} "
              f"blocking_review={r['counts']['blocking_review']}")
        for t in r["touched"]:
            print(f"  SEEDED {t['rel']} (+{len(t['ids_added'])})")
        for f in r["refused"]:
            print(f"  REFUSED {f['rel']} — {f['reason']}")
    for b in r["blocking_review"]:
        print(f"  BLOCKING {b['rel']} — {b['issue']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
