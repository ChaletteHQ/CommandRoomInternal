#!/usr/bin/env python3
"""
Deliverable content sweep (SPEC GATE2 D2/D3) — the load-bearing detector.

WHY THIS EXISTS
---------------
GATE1 tried to FORCE every deliverable through `brief_writer.make_brief` so the
save-time voice + leak gates would run. Live Cowork proved that fails: given a
realistic batch, the LLM hand-rolled all three .docx via one `python-docx`
script — 0 `gate_ran`, every gate bypassed. The bypass routes can't be removed
(the generic docx skill is a Cowork built-in; the LLM can `pip install docx`).

So GATE2 stops trying to guarantee the route and instead SCANS WHAT WAS ACTUALLY
PRODUCED. This module sweeps the deliverables on disk + any chat-rendered
deliverable text and flags anything carrying a voice tell or a privacy/substrate
leak — regardless of how it was made. `docx_leak_scanner.scan_docx_for_violations`
reads the file itself, so a hand-rolled doc is caught exactly like a make_brief one.

CLIENT SAFETY (non-negotiable — runs on 5 live client workspaces)
-----------------------------------------------------------------
- READ + FLAG ONLY. This module NEVER deletes, moves, renames, or edits a user's
  file. "Quarantine" here means SURFACE LOUDLY, not relocate.
- The only writes are CR-owned telemetry: a findings record under
  `_hq/.system/gate2_findings/` and a best-effort `gate_ran` event. Both are
  wrapped so they can never raise into the caller.
- NEVER raises. A sweep that hit one unreadable file flags it ("couldn't verify
  this doc") and keeps going.

Used by:
  - cleanup/SKILL.md (Phase 3f) — the weekly backstop sweep.
  - gate2_turn_sweep.py — the best-effort same-turn Stop-hook runner.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Directory name parts we never sweep: archives, backups, quarantines, caches,
# VCS, and the system dir itself. Matched case-insensitively against each path
# part so `_archive`, `_hq/backups`, `.system/quarantine`, `.git` are all pruned.
_EXCLUDED_DIR_PARTS = frozenset(
    {
        "_archive",
        "backups",
        ".system",
        "quarantine",
        ".git",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "session-notes-archive",
    }
)

# Markdown/text deliverable extensions. The LLM hand-rolls deliverables as .md
# (the live GATE2 re-run: a drafted email + a decision memo both escaped as .md,
# uncaught, because the sweep only walked .docx). These get scanned too.
_TEXT_DELIVERABLE_EXTS = (".md", ".markdown")

# CONTEXT/MEMORY markdown is NOT a deliverable (CONTRACT Rule 27 model — the same
# distinction run_no_md_deliverables_test enforces): session notes, the workspace
# brief, memory, analytical views, build specs, voice corpus, transcripts. These
# legitimately contain "Phase N"/substrate language and would be pure
# false-positive noise if scanned. We scan .md only when it looks like a
# deliverable that escaped — i.e. NOT one of these infra surfaces. The .docx walk
# needs none of this (a .docx is a deliverable by definition).
_INFRA_TEXT_DIR_PARTS = frozenset(
    {
        "build-specs",
        ".claude",
        ".backups",
        "_people",
        "views",
        "briefings",
        "insights",
        "intel",
        "transcripts",
        "voice",
        "meetings",
    }
)
# Filename stems (case-insensitive startswith) that mark a .md as context/memory.
_INFRA_TEXT_NAME_STEMS = (
    "session_notes",
    "claude",
    "memory",
    "readme",
    "changelog",
    "contract",
    "skill",
    "timeline",
    "decision_log",
    "master_tracker",
    "relationships",
    "dormant",
    "themes",
    "commitment_aging",
    "people",
    "business_context",
    "project_context",
    "project_",
    "positioning",
    "infrastructure",
    "working_style",
    "conventions",
    "brand",
    "start_here",
    "future_work",
    "testing_guide",
    "assumptions",
    "validation",
    "md_deliverable_policy",
    "voice_calibration",
    "voice_samples",
    "how_command_room_works",
)

# Skip text files larger than this — a deliverable email/memo is small; a huge
# .md is almost certainly a log/export, not something to voice-check.
_MAX_TEXT_BYTES = 2_000_000


def _is_infra_text(path: Path) -> bool:
    """True if a .md/.markdown path is context/memory, not a deliverable."""
    if {p.lower() for p in path.parts} & _INFRA_TEXT_DIR_PARTS:
        return True
    stem = path.name.lower()
    return any(stem.startswith(s) for s in _INFRA_TEXT_NAME_STEMS)


def _now_iso() -> Optional[str]:
    try:
        from cru_match import _now_iso as _ts  # type: ignore

        return _ts()
    except Exception:
        return None


def _iter_candidate_files(
    workspace_root: str | Path,
    exts: tuple[str, ...],
    *,
    since_ts: Optional[float],
    max_files: int,
    infra_filter=None,
) -> List[Path]:
    """Workspace-wide walk for files ending in any of `exts`, newest first.

    A hand-rolled deliverable can land ANYWHERE (the live test wrote one to the
    Drive root), so this is a full walk minus the excluded archive/backup/cache
    dirs. `infra_filter`, when given, drops paths it returns True for (used to
    skip context/memory markdown). `since_ts` is a POSIX mtime floor; None →
    every candidate (bounded by `max_files`).
    """
    root = Path(workspace_root)
    if not root.exists():
        return []
    found: List[tuple] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded dirs in-place so os.walk doesn't descend into them.
        dirnames[:] = [
            d for d in dirnames if d.lower() not in _EXCLUDED_DIR_PARTS
        ]
        for fn in filenames:
            low = fn.lower()
            if not any(low.endswith(e) for e in exts):
                continue
            if fn.startswith("~$"):  # Word lock/temp file
                continue
            p = Path(dirpath) / fn
            if infra_filter is not None and infra_filter(p):
                continue
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if since_ts is not None and mtime < since_ts:
                continue
            found.append((mtime, p))
    found.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in found[:max_files]]


def find_candidate_docx(
    workspace_root: str | Path,
    *,
    since_ts: Optional[float] = None,
    max_files: int = 500,
) -> List[Path]:
    """Walk `workspace_root` for `.docx` deliverables, newest first.

    cleanup passes "7 days ago" so the weekly sweep covers freshly-produced docs
    and never re-flags a client's old files. `max_files` is a hard cap so a huge
    workspace can't make the sweep run away.
    """
    return _iter_candidate_files(
        workspace_root, (".docx",), since_ts=since_ts, max_files=max_files
    )


def find_candidate_text(
    workspace_root: str | Path,
    *,
    since_ts: Optional[float] = None,
    max_files: int = 500,
) -> List[Path]:
    """Walk for `.md`/`.markdown` DELIVERABLES, newest first — skipping the
    context/memory markdown (`_is_infra_text`) that would only add noise."""
    return _iter_candidate_files(
        workspace_root,
        _TEXT_DELIVERABLE_EXTS,
        since_ts=since_ts,
        max_files=max_files,
        infra_filter=_is_infra_text,
    )


def find_candidate_deliverables(
    workspace_root: str | Path,
    *,
    since_ts: Optional[float] = None,
    max_files: int = 500,
) -> List[Path]:
    """Every candidate deliverable (.docx + deliverable-shaped .md), newest
    first. This is what the sweep walks — the LLM emits both formats."""
    merged = find_candidate_docx(
        workspace_root, since_ts=since_ts, max_files=max_files
    ) + find_candidate_text(
        workspace_root, since_ts=since_ts, max_files=max_files
    )

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    merged.sort(key=_mtime, reverse=True)
    return merged[:max_files]


def scan_text_file(path: str | Path) -> dict:
    """Scan a `.md`/`.markdown` (or any plain-text) DELIVERABLE file for voice
    tells + privacy/substrate leaks. Returns the SAME result shape as
    `scan_docx_for_violations` (path, leaks, voice, has_violation,
    has_voice_warn, optional error) so it flows through the sweep + summary
    unchanged. Reads the file and runs the chat-text scanner (context="brief",
    so markdown bullets don't false-trip). NEVER raises."""
    path = Path(path)
    result: dict = {
        "path": str(path),
        "leaks": [],
        "voice": {"verdict": "pass", "findings": []},
        "has_violation": False,
        "has_voice_warn": False,
    }
    try:
        if not path.exists():
            result["error"] = f"file not found: {path}"
            return result
        if path.stat().st_size > _MAX_TEXT_BYTES:
            result["error"] = f"file too large to scan: {path.name}"
            return result
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        result["error"] = f"unreadable file ({type(e).__name__}): {path.name}"
        return result
    scan = scan_chat_text(text, context="brief")
    result["leaks"] = scan["leaks"]
    result["voice"] = scan["voice"]
    result["has_violation"] = scan["has_violation"]
    result["has_voice_warn"] = scan["has_voice_warn"]
    return result


def scan_path_for_violations(path: str | Path) -> dict:
    """Dispatch a single path to the right scanner by extension: `.docx` reads
    the rendered document; everything else is treated as a text deliverable."""
    p = Path(path)
    if p.suffix.lower() == ".docx":
        from docx_leak_scanner import scan_docx_for_violations

        return scan_docx_for_violations(p)
    return scan_text_file(p)


def sweep_paths(paths: List[str | Path]) -> List[dict]:
    """Run the right scanner over each path (docx or text). Returns ONLY the
    results that carry something worth surfacing: a leak, a fail/warn voice tell,
    or a read error. Clean files are dropped (no false-positive noise). Never
    raises."""
    flagged: List[dict] = []
    for p in paths:
        try:
            r = scan_path_for_violations(p)
        except Exception as e:  # belt — the scanners already swallow internally
            r = {"path": str(p), "error": f"scan failed ({type(e).__name__})",
                 "leaks": [], "voice": {"verdict": "pass", "findings": []},
                 "has_violation": False, "has_voice_warn": False}
        if r.get("has_violation") or r.get("has_voice_warn") or r.get("error"):
            flagged.append(r)
    return flagged


# Back-compat alias for docx-era callers; the body now dispatches by extension.
def sweep_docx_paths(paths: List[str | Path]) -> List[dict]:
    return sweep_paths(paths)


def scan_chat_text(text: str, *, context: str = "email") -> dict:
    """SPEC GATE2 D4 — scan a chat-rendered deliverable body (an email or a memo
    drafted as chat prose) for voice tells AND privacy/substrate leaks.

    Returns {"leaks": [...], "voice": {...}, "has_violation": bool,
             "has_voice_warn": bool}. Never raises."""
    out = {
        "leaks": [],
        "voice": {"verdict": "pass", "findings": []},
        "has_violation": False,
        "has_voice_warn": False,
    }
    if not text:
        return out
    try:
        from docx_leak_scanner import scan_text_for_leaks

        out["leaks"] = scan_text_for_leaks(text)
    except Exception:
        out["leaks"] = []
    try:
        from voice_tell_detector import scan_text

        out["voice"] = scan_text(text, context=context)
    except Exception:
        out["voice"] = {"verdict": "pass", "findings": []}
    vf = out["voice"].get("findings", [])
    out["has_voice_warn"] = any(f.get("severity") == "warn" for f in vf)
    out["has_violation"] = bool(out["leaks"]) or any(
        f.get("severity") == "fail" for f in vf
    )
    return out


# Phase 6 Quick Win A — filename → producing-skill attribution for the voice
# corrections feed. Best-effort: a well-attributed tell trains the right skill's
# voice block; an unrecognized deliverable falls to the generic "deliverables"
# corpus (still read by Pass 11's corrections-*.jsonl glob). Never a user write.
_SKILL_FILENAME_HINTS = (
    ("call_prep", "call-prep"),
    ("call prep", "call-prep"),
    ("board_pack", "board-pack-assembler"),
    ("board pack", "board-pack-assembler"),
    ("one_pager", "one-pager-composer"),
    ("one pager", "one-pager-composer"),
    ("memo", "memo-writer"),
    ("insights", "insight-generator"),
    ("operator", "operator-report"),
    ("decision_memo", "decision-memo-composer"),
    ("board_minutes", "board-pack-assembler"),
)

# Map a voice-tell rule id to a correction_type bucket so Pass 11 groups these
# alongside user edit-corrections. Voice tells are all "banned phrasing" for the
# purposes of the corpus — the offending phrase is the pattern to stop using.
_VOICE_RULE_TO_TYPE = {
    "structural_em_dash_pileup": "structure",
    "structural_tri_colon": "structure",
    "structural_bullets_in_email": "structure",
    "structural_hedging_stack": "tone",
}


def _infer_skill_from_path(path: str) -> str:
    name = Path(path or "").name.lower()
    for token, skill in _SKILL_FILENAME_HINTS:
        if token in name:
            return skill
    return "deliverables"


def feed_voice_corrections(workspace_root: str | Path, result: dict) -> int:
    """Quick Win A — append each FAIL-severity voice tell found in a produced
    deliverable to the relevant `corrections-<skill>.jsonl`, giving
    insight-generator Pass 11 more training data for free. FLAG-ONLY: this never
    edits, moves, or rewrites the user's deliverable — it only appends a
    CR-owned correction row under `_hq/voice/` (same class as the findings
    record). The offending phrase is stored as `original` with an empty
    `corrected` (there is no user rewrite to compare against — the signal is
    "this tell was produced; stop using it"), so Pass 11 can propose banning it.
    Privacy/substrate leaks are NOT fed here — they are not a voice pattern and
    stay flag-only. Returns the number of rows written. NEVER raises."""
    written = 0
    try:
        from voice_corrections import append_correction
    except Exception:
        return 0
    for f in result.get("flagged", []) or []:
        findings = (f.get("voice") or {}).get("findings") or []
        fails = [x for x in findings if x.get("severity") == "fail"]
        if not fails:
            continue
        skill = _infer_skill_from_path(f.get("path", ""))
        seen = set()
        for x in fails:
            phrase = (x.get("match") or "").strip()
            if not phrase or phrase.lower() in seen:
                continue
            seen.add(phrase.lower())
            ctype = _VOICE_RULE_TO_TYPE.get(x.get("rule", ""), "phrasing")
            try:
                if append_correction(
                    workspace_root,
                    skill=skill,
                    domain="deliverable",
                    recipient_id=None,
                    original=phrase,
                    corrected="",
                    correction_type=ctype,
                    notes=f"check-deliverables: {x.get('rule', 'voice_tell')} "
                          f"in {Path(f.get('path', '')).name}",
                ):
                    written += 1
            except Exception:
                continue
    return written


def sweep_workspace(
    workspace_root: str | Path,
    *,
    since_ts: Optional[float] = None,
    emit: bool = True,
    source: str = "sweep",
) -> dict:
    """Find + scan every candidate .docx under the workspace. FLAG-only.

    Returns:
      {"scanned": int, "flagged": [ <scan result>, ... ],
       "violation_count": int, "warn_count": int, "error_count": int}

    When `emit` is True and at least one file was scanned, appends a best-effort
    `gate_ran` event (`surface=<source>`) so the result is detectable in
    substrate — the cheap complement to the gate_ran join (D5)."""
    candidates = find_candidate_deliverables(workspace_root, since_ts=since_ts)
    flagged = sweep_paths(candidates)
    violation_count = sum(1 for f in flagged if f.get("has_violation"))
    warn_count = sum(
        1 for f in flagged if f.get("has_voice_warn") and not f.get("has_violation")
    )
    error_count = sum(1 for f in flagged if f.get("error"))
    result = {
        "scanned": len(candidates),
        "flagged": flagged,
        "violation_count": violation_count,
        "warn_count": warn_count,
        "error_count": error_count,
    }
    if emit and candidates:
        _emit_sweep_event(workspace_root, result, source=source)
        _write_findings_record(workspace_root, result, source=source)
        feed_voice_corrections(workspace_root, result)  # Quick Win A
    return result


def sweep_targets(
    paths: List[str | Path],
    *,
    workspace_root: Optional[str | Path] = None,
    emit: bool = False,
    source: str = "on_demand_targets",
) -> dict:
    """Scan an EXPLICIT list of files (the on-demand 'scan this' path), returning
    the same result shape as `sweep_workspace` so `summarize_for_user` consumes
    it unchanged. FLAG-only + read-only. Telemetry (gate_ran + findings record)
    is written only when `emit` and `workspace_root` are both given."""
    paths = list(paths)
    flagged = sweep_paths(paths)
    violation_count = sum(1 for f in flagged if f.get("has_violation"))
    warn_count = sum(
        1 for f in flagged if f.get("has_voice_warn") and not f.get("has_violation")
    )
    error_count = sum(1 for f in flagged if f.get("error"))
    result = {
        "scanned": len(paths),
        "flagged": flagged,
        "violation_count": violation_count,
        "warn_count": warn_count,
        "error_count": error_count,
    }
    if emit and workspace_root and flagged:
        _emit_sweep_event(workspace_root, result, source=source)
        _write_findings_record(workspace_root, result, source=source)
        feed_voice_corrections(workspace_root, result)  # Quick Win A
    return result


def _emit_sweep_event(workspace_root: str | Path, result: dict, *, source: str) -> None:
    """Best-effort detectable signal. Reuses the existing `gate_ran` enum member
    (no schema change) with surface=<source>; the verify loop / cleanup can join
    against deliverable events. NEVER raises."""
    try:
        from atomic_write import atomic_append_jsonl as _append  # type: ignore
    except Exception:
        return
    ts = _now_iso()
    try:
        events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
        ev = {
            "type": "gate_ran",
            "source_skill": "deliverable_sweep",
            "data": {
                "surface": source,
                "gates": ["voice", "leak"],
                "result": "fail" if result["violation_count"] else "pass",
                "scanned": result["scanned"],
                "violation_count": result["violation_count"],
                "warn_count": result["warn_count"],
                "error_count": result["error_count"],
            },
        }
        if ts:
            ev["ts"] = ts
        _append(events_path, [ev], holder="deliverable_sweep.gate_ran")
    except Exception:
        pass


def _write_findings_record(workspace_root: str | Path, result: dict, *, source: str) -> None:
    """Write a durable findings record to the CR-owned system dir so the flags
    survive the turn (the Stop hook's stdout may not reach the user in every
    runtime). Writes ONLY under `_hq/.system/gate2_findings/`. NEVER raises and
    NEVER touches a user file."""
    if not result["flagged"]:
        return
    try:
        import json

        out_dir = Path(workspace_root) / "_hq" / ".system" / "gate2_findings"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = (_now_iso() or "unknown").replace(":", "").replace(" ", "_")
        rec = {
            "ts": _now_iso(),
            "source": source,
            "scanned": result["scanned"],
            "violation_count": result["violation_count"],
            "warn_count": result["warn_count"],
            "error_count": result["error_count"],
            # Store basenames + findings only — never the user's full content.
            "flagged": [
                {
                    "doc": Path(f.get("path", "")).name,
                    "leaks": sorted({x["match"] for x in f.get("leaks", [])}),
                    "voice": sorted(
                        {x["rule"] for x in f.get("voice", {}).get("findings", [])}
                    ),
                    "error": f.get("error"),
                }
                for f in result["flagged"]
            ],
        }
        (out_dir / f"{ts}-{source}.json").write_text(
            json.dumps(rec, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


# ---- Plain-English surfacing (CONTRACT Rule 4 safe — no paths, no jargon) ----

def _why_phrase(f: dict) -> str:
    """One human phrase describing what's wrong with a flagged doc. No internal
    token names, no `_hq/` paths — just what a CEO would understand."""
    if f.get("error"):
        return "couldn't be checked (the file wouldn't open)"
    bits = []
    leaks = sorted({x["match"] for x in f.get("leaks", [])})
    if leaks:
        # Surface the literal offending words — that's the actionable part for a
        # rewrite — but cap the list so the note stays tight.
        shown = ", ".join(repr(w) for w in leaks[:4])
        more = f" +{len(leaks) - 4} more" if len(leaks) > 4 else ""
        bits.append(f"language that doesn't sound like you ({shown}{more})")
    voice_rules = {x["rule"] for x in f.get("voice", {}).get("findings", [])}
    if any(r.startswith("opener_") or r.startswith("filler_") or r.startswith("closer_") or r.startswith("preamble_") for r in voice_rules):
        bits.append("a generic-assistant phrase")
    if "structural_tri_colon" in voice_rules:
        bits.append("a colon-chained construction that reads as AI-written")
    if "structural_em_dash_pileup" in voice_rules:
        bits.append("an em-dash pile-up")
    if not bits:
        bits.append("a possible voice tell")
    return " + ".join(bits)


def summarize_for_user(result: dict, *, max_docs: int = 5) -> Optional[str]:
    """Plain-English summary for the cleanup Monday note / Stop-hook surface.

    Returns None when there's nothing to flag (clean sweep). Otherwise a short,
    forward-action paragraph naming docs by FILENAME only — no `_hq/` paths, no
    internal token names beyond the literal offending words the CEO can search
    for. NEVER includes event-type names or version refs (CONTRACT Rule 4)."""
    flagged = result.get("flagged") or []
    if not flagged:
        return None
    lines = []
    real = [f for f in flagged if f.get("has_violation") or f.get("error")]
    target = real or flagged
    n = len(target)
    head = (
        f"{n} document{'s' if n != 1 else ''} produced recently didn't pass the "
        f"quality gate — worth a glance before any of them go out:"
    )
    lines.append(head)
    for f in target[:max_docs]:
        doc = Path(f.get("path", "")).name or "a document"
        lines.append(f"• {doc} — {_why_phrase(f)}")
    if n > max_docs:
        lines.append(f"• …and {n - max_docs} more")
    return "\n".join(lines)


# ---- gate_ran join (SPEC GATE2 D5 / GATE1 §3b standing detector) ----

# Deliverable events that SHOULD have routed through brief_writer.make_brief,
# which emits a gate_ran(surface="docx") event per rendered .docx. A deliverable
# event in the window with no matching docx gate_ran is a suspected bypass (the
# composer hand-rolled the doc, dodging the save-time gates). This is the cheap
# complement to the content sweep — it can't see a hand-rolled doc that emitted
# NO deliverable event either, which is exactly why scan-the-file (D2) is primary.
_GATED_DELIVERABLE_EVENTS = frozenset(
    {
        "one_pager_drafted",
        "memo_drafted",
        "decision_memo_drafted",
        "board_pack_assembled",
        "followup_pack_drafted",
        "operator_report_generated",
        "value_receipt_generated",
    }
)


def detect_gate_bypass(
    workspace_root: str | Path, *, since_ts: Optional[float] = None
) -> dict:
    """Join deliverable events against docx gate_ran events over a window.

    Returns {"deliverables": int, "docx_gate_ran": int, "suspected_bypass": int,
             "by_type": {<event_type>: count}}. A positive suspected_bypass count
             means more gated-deliverable events were produced than gates ran —
             i.e. some deliverable skipped make_brief. FLAG-only; never raises."""
    import json

    result = {
        "deliverables": 0,
        "docx_gate_ran": 0,
        "suspected_bypass": 0,
        "by_type": {},
    }
    events_path = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    if not events_path.exists():
        return result

    def _ts_ok(ev) -> bool:
        if since_ts is None:
            return True
        try:
            from event_time import event_time
        except ImportError:  # pragma: no cover
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from event_time import event_time
        ts = event_time(ev)
        if not ts:
            return True  # undated event — don't exclude it
        try:
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() >= since_ts
        except Exception:
            return True

    deliverables = 0
    docx_gate_ran = 0
    by_type: Dict[str, int] = {}
    try:
        for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue  # defensive — tolerate a malformed line
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            if etype in _GATED_DELIVERABLE_EVENTS and _ts_ok(ev):
                deliverables += 1
                by_type[etype] = by_type.get(etype, 0) + 1
            elif etype == "gate_ran" and _ts_ok(ev):
                if (ev.get("data") or {}).get("surface") == "docx":
                    docx_gate_ran += 1
    except Exception:
        return result

    result["deliverables"] = deliverables
    result["docx_gate_ran"] = docx_gate_ran
    result["suspected_bypass"] = max(0, deliverables - docx_gate_ran)
    result["by_type"] = by_type
    return result


__all__ = [
    "find_candidate_docx",
    "find_candidate_text",
    "find_candidate_deliverables",
    "scan_text_file",
    "scan_path_for_violations",
    "sweep_docx_paths",
    "sweep_paths",
    "sweep_targets",
    "scan_chat_text",
    "sweep_workspace",
    "summarize_for_user",
    "detect_gate_bypass",
]


def _main(argv: List[str]) -> int:
    """CLI: `deliverable_sweep.py <workspace_root> [--days N] [--no-emit]`.
    Prints the plain-English summary (or 'clean'). Exit 0 always (flag-only)."""
    import time

    if not argv:
        print("usage: deliverable_sweep.py <workspace_root> [--days N] [--no-emit]",
              file=sys.stderr)
        return 2
    workspace_root = argv[0]
    since_ts = None
    emit = True
    i = 1
    while i < len(argv):
        if argv[i] == "--days" and i + 1 < len(argv):
            since_ts = time.time() - float(argv[i + 1]) * 86400
            i += 2
        elif argv[i] == "--no-emit":
            emit = False
            i += 1
        else:
            i += 1
    result = sweep_workspace(workspace_root, since_ts=since_ts, emit=emit)
    summary = summarize_for_user(result)
    if summary is None:
        print(f"clean — scanned {result['scanned']} document(s), nothing to flag")
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(_main(sys.argv[1:]))
